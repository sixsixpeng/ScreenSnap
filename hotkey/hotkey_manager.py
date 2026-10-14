"""keyboard 回调只向 Qt 主线程投递信号。"""

import logging
import queue
import threading
import time

# 进程级屏蔽 keyboard 包的 DeprecationWarning，而且必须**在 import keyboard 之前**装好：
# 那条警告由 keyboard/mouse.py 在**导入期**抛出，谁先导入谁触发；局部 catch_warnings() 只在
# 当前上下文生效，真机上（keyboard 被更早导入）仍会漏到控制台（2026-10-11 实测）。
# 只忽略来自 keyboard 包的 DeprecationWarning，不影响其它库的警告。
#
# 为什么要写两处（2026-10-11 真机二次复现后加固）：warnings.filterwarnings 只是往 filters
# 里插一条，而**任何**库调用 warnings.resetwarnings() 都会把 filters 重置回 sys.warnoptions
# 派生值 —— 那一下就把这条抹掉了（真机上正是这么漏出来的，本地单独导入复现不出）。所以同时
# 写进 sys.warnoptions：此后无论谁再 resetwarnings()，这条 ignore 都会被重新装上。
# 关键点（2026-10-11 真机多次复现后定的）：按消息内容过滤，不要只按 module 名 —— 该警告带
# stacklevel 抛出，模块名可能被算成导入方（也就是我们自己），module=keyboard 就匹配不上。
import sys as _sys
import warnings as _warnings

if "ignore::DeprecationWarning:keyboard" not in _sys.warnoptions:
    _sys.warnoptions.append("ignore::DeprecationWarning:keyboard")
_warnings.resetwarnings()
_warnings.filterwarnings("ignore", message=r"The mouse sub-library is deprecated")
_warnings.filterwarnings("ignore", category=DeprecationWarning, module=r"keyboard")
import keyboard
from PySide6.QtCore import QObject, QTimer, Signal

# 体检周期（设置项 hotkey_health_interval，2–600 秒）与重装限速。
# 规则 14：健康时既不重装也不打日志，只在**判定失效**并重装时记录。
#
# 失效判据（2026-10-11 真机三轮迭代后的最终方案）：
#   · Windows 的 GetLastInputInfo 给出全系统最后一次键鼠输入的时刻；
#   · 我们额外挂一个键盘钩子与鼠标钩子，只记录「最后一次收到事件的时刻」；
#   · 系统说刚刚有输入、而自家钩子两秒多没收到任何事件 ⇒ 钩子已被系统静默摘掉 ⇒ 重装。
# 为什么不用合成键探针：keyboard.send()/press_and_release() 合成的事件不会触发该库自己的
#   热键回调（实测 5 种组合全部 NO-RESPONSE）⇒ 会把「钩子正常」误报成「已被摘掉」。
# 为什么不做无条件定期重装：健康时每周期白白清空并重建热键（约 4ms 空窗），日志里每周期
#   一条「已注册全局热键」—— 用户实测反馈「已经能截图了还在打印重载」，故废弃。
DEFAULT_HEALTH_SECONDS = 4
MIN_HEALTH_SECONDS = 2
MAX_HEALTH_SECONDS = 600
HEARTBEAT_SECONDS = 600.0
RETRY_MIN_SECONDS = 30.0
# 系统输入距现在这么久以内，才算「刚刚有输入」（空闲时不作判断，避免误判）
RECENT_INPUT_SECONDS = 1.5
# 系统输入比自家钩子新这么多秒，判定钩子没有在工作
MISSED_INPUT_SECONDS = 2.5
# 2026-10-14（用户报告"被别的程序抢了、一直不恢复、也没日志"）：旧判据只看「系统有输入而自家
# 通用钩子没收到」✗ —— 抢占方通常**只吞掉那一个组合键**，你敲别的键/动鼠标仍会刷新
# `_observed_input` ⇒ 判据恒为"健康" ⇒ 不重装、不打日志。
#
# 因此新增按热键的判据：通用钩子在键盘链上看到某热键的**主键按下、且修饰键恰好等于绑定**
# （多按一个修饰键时 keyboard 根本不会触发该热键），而回调连续 REQUIRED_MISSES 次都没触发
# ⇒ 判定它被其它程序吞掉/抢占。
#
# ⚠ 回退教训（2026-10-14 真机把热键整体改坏）：**通用钩子回调里绝不能调用 keyboard 的任何
# API**（当时调了 `keyboard.is_pressed` 判断修饰键）。那会在真实钩子链上出问题（已实证：
# 按键仍到达通用钩子、但 18 个热键的回调再也不触发），而且第一版判据还会误报——
# 真机日志里 `repeat` 被误判成"被抢占"并触发了一次重装。现在改成：钩子回调只做纯 Python 的
# 集合/字符串判断（自己维护按下的修饰键集合），并且必须**连续两次**同键未触发才算数。
REQUIRED_MISSES = 2
# 两次「按下但未触发」之间超过这么久就重新计数（避免把相隔很久的两次偶发算成一次）
MISS_WINDOW_SECONDS = 10.0
# 回调刚触发过这么久以内，通用钩子随后再看到同一主键也不算“等待回调”——
# 钩子链顺序可能让回调先跑、通用钩子后跑（否则会被误判成被抢占）。
FIRED_GRACE_SECONDS = 0.25

_MODIFIER_ALIASES = {
    "ctrl": "ctrl", "control": "ctrl",
    "shift": "shift",
    "alt": "alt",
    "win": "windows", "windows": "windows", "cmd": "windows", "meta": "windows",
}
MODIFIER_NAMES = ("ctrl", "shift", "alt", "windows")


def parse_binding_keys(binding):
    """绑定串 -> (修饰键集合, 主键名)；不认识的形式返回 None（该项不参与被抢占判定）。

    只做字符串解析、不依赖 keyboard 的私有 API：解析不出来时最差只是退回旧判据，
    绝不会因为解析异常影响热键注册本身。
    """
    text = str(binding or "").strip().lower().replace(" ", "")
    if not text:
        return None
    parts = [part for part in text.split("+") if part]
    if not parts:
        return None
    modifiers = set()
    for token in parts[:-1]:
        modifier = _MODIFIER_ALIASES.get(token)
        if modifier is None:
            return None
        modifiers.add(modifier)
    return modifiers, parts[-1]


def last_input_seconds():
    """距系统最后一次键鼠输入的秒数；非 Windows 或调用失败时返回 None（调用方按无法判定处理）。"""
    try:
        import ctypes

        class LASTINPUTINFO(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

        info = LASTINPUTINFO()
        info.cbSize = ctypes.sizeof(LASTINPUTINFO)
        if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
            return None
        return max(0.0, (ctypes.windll.kernel32.GetTickCount() - info.dwTime) / 1000.0)
    except Exception:  # noqa: BLE001 取不到就不下结论
        return None


class HotkeyManager(QObject):
    """队列串行处理注册请求；监听线程只发信号，不直接操作 Qt 窗口。"""

    triggered = Signal(str)
    failed = Signal(str)

    def __init__(self):
        super().__init__()
        self.handles = []
        self.paused = threading.Event()
        self.requests = queue.Queue()
        self.worker = threading.Thread(target=self.listen, daemon=True, name="global-hotkeys")
        self.worker.start()
        # 最近一次成功安装的请求：钩子失效时就地重装，无需调用方再给一次设置。
        self._last_request = None
        self._last_retry = 0.0
        self.reinstall_count = 0
        # 单一周期定时器（2026-10-11 三轮迭代后的最终形态）：每跳做一次体检 ——
        # ① 监听线程死了 ⇒ 重装；② 系统有输入而自家钩子没收到 ⇒ 重装；③ 正常 ⇒ 什么都不做。
        # 注意：曾经的「无条件预防性重装」与「合成键探针」都已废弃（原因见文件头注释）。
        self.health_timer = QTimer(self)
        self.health_timer.setInterval(DEFAULT_HEALTH_SECONDS * 1000)
        self.health_timer.timeout.connect(self.check_health)
        self.health_timer.start()
        # 失效检测状态：_observed_input 由通用键盘钩子与鼠标钩子的回调刷新（见 _note_input）。
        self._observed_input = time.monotonic()
        self._input_hooks = []
        # 鼠标钩子模块**在主线程解析一次**：warnings.catch_warnings() 不是线程安全的，
        # 在工作线程里临时屏蔽废弃警告会被主线程的其它警告活动提前还原（真机实测漏出过
        # DeprecationWarning），而模块导入只需发生一次，之后工作线程直接用缓存即可。
        self._mouse, self._mouse_source = self._mouse_module()
        self.hook_reinstalls = 0
        self.checks = 0
        self._heartbeat_at = time.monotonic()
        # 正在停止：避免 stop() 卸掉输入观察钩子后，工作线程又在 install() 里重新挂上（泄漏）。
        self._stopping = False
        # 被抢占判定（2026-10-14）：action -> (修饰键集合, 主键名)；action -> [连续未触发次数, 首次时刻]；
        # action -> 最近一次真正触发回调的时刻；以及本地维护的“当前按下的键名”集合。
        # 注意：全部是纯 Python 状态，钩子回调里**不调用 keyboard 的任何 API**（见文件头教训）。
        self._hotkey_keys = {}
        self._misses = {}
        self._last_fired = {}
        self._pressed_names = set()
        self.intercept_reinstalls = 0

    def set_health_interval(self, seconds):
        """按设置调整体检周期（夹紧到 2–600 秒；只在真的变化时生效）。"""
        try:
            value = int(seconds)
        except (TypeError, ValueError):
            value = DEFAULT_HEALTH_SECONDS
        value = max(MIN_HEALTH_SECONDS, min(MAX_HEALTH_SECONDS, value))
        if self.health_timer.interval() == value * 1000:
            return
        self.health_timer.setInterval(value * 1000)
        logging.getLogger("screensnap").debug("热键体检间隔调整为 %d 秒", value)

    def _note_input(self, event=None):
        """通用钩子回调：记时间戳、维护本地修饰键状态，并累计「组合键已按下但未触发」。

        ⚠ 高频路径且**绝不允许调用 keyboard 的任何 API**（规则 14 + 2026-10-14 真机教训：
        在钩子回调里查按键状态会让真实钩子链上的热键回调整体失效）。
        这里只做纯 Python 的集合/字符串判断，并且整体兜住异常，保证回调永不抛异常。
        """
        try:
            now = time.monotonic()
            self._observed_input = now
            name = getattr(event, "name", None)
            if name is None:
                return          # 鼠标事件没有 name
            name = str(name).lower()
            if getattr(event, "event_type", "down") == "down":
                self._pressed_names.add(name)
            else:
                self._pressed_names.discard(name)
            if not self._hotkey_keys or getattr(event, "event_type", "down") != "down":
                return
            for action, parsed in self._hotkey_keys.items():
                if parsed is None:
                    continue
                modifiers, main = parsed
                if main != name:
                    continue
                # 修饰键必须**恰好**等于绑定：多按一个修饰键时 keyboard 根本不会触发该热键，
                # 若不要求精确匹配就会误判被抢占（真机日志里就是这么误报 repeat 的）。
                if (self._pressed_names & set(MODIFIER_NAMES)) != modifiers:
                    continue
                if now - self._last_fired.get(action, 0.0) <= FIRED_GRACE_SECONDS:
                    continue
                entry = self._misses.get(action)
                if entry is not None and now - entry[1] <= MISS_WINDOW_SECONDS:
                    entry[0] += 1
                else:
                    self._misses[action] = [1, now]
        except Exception:  # noqa: BLE001 钩子回调抛异常会破坏 keyboard 的监听链
            logging.getLogger("screensnap").debug("输入观察钩子回调异常（已忽略）", exc_info=True)

    @staticmethod
    def _mouse_module():
        """取鼠标钩子模块。

        优先用独立包 `mouse`；没有就退回 `keyboard.mouse`（该子库已被上游标记废弃，
        会在 stderr 打出 DeprecationWarning，这里就地屏蔽，避免污染控制台输出）。
        """
        try:
            import mouse  # type: ignore

            return mouse, "mouse"
        except Exception:  # noqa: BLE001 未安装独立包
            pass
        try:
            import importlib
            import warnings

            # record=True 是关键：警告被收进列表、**不经过 showwarning**，因此无论运行期谁重置过
            # 过滤器，控制台都不可能再看到它（真机前几版过滤都漏出的收口做法，2026-10-11）。
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                legacy_mouse = importlib.import_module("keyboard.mouse")
            for item in caught:
                logging.getLogger("screensnap").debug(
                    "鼠标钩子导入期警告已捕获并忽略: %s: %s", item.category.__name__, item.message)
            return legacy_mouse, "keyboard.mouse(已废弃)"
        except Exception:  # noqa: BLE001
            return None, ""
    def _ensure_input_hooks(self):
        """挂上键盘与鼠标的通用钩子，用于输入观察与「组合键被吞」判定。

        只做**添加**，不在重装时先卸再挂（2026-10-14 回退）：那会在真实钩子链上卸掉/重挂钩子，
        已被真机证明会把热键整体改坏；观察钩子若真被系统摘掉，由 `_hook_missed_input` 判定并
        重装热键本身即可。
        """
        if self._input_hooks or getattr(self, "_stopping", False):
            return
        # 重新挂钩子时丢弃旧的按键状态，避免把「重挂前按下的键」当成仍按着。
        self._pressed_names = set()
        logger = logging.getLogger("screensnap")
        try:
            self._input_hooks.append(keyboard.hook(self._note_input))
        except Exception as error:  # noqa: BLE001 观察钩子失败不影响热键本身
            logger.debug("键盘观察钩子挂载失败: %s", error)
        mouse, source = getattr(self, "_mouse", None), getattr(self, "_mouse_source", "")
        if mouse is not None:
            try:
                self._input_hooks.append(mouse.hook(self._note_input))
            except Exception as error:  # noqa: BLE001
                logger.debug("鼠标观察钩子挂载失败: %s", error)
        logger.debug("已挂上输入观察钩子: 键盘 1 个，鼠标 %s", source or "未挂上（将只用键盘事件判定）")

    def _remove_input_hooks(self):
        """卸载输入观察钩子（只在停止时调用，重装热键时保留）。"""
        mouse = getattr(self, "_mouse", None)
        for handle in self._input_hooks:
            for module in (keyboard, mouse):
                if module is None:
                    continue
                try:
                    module.unhook(handle)
                    break
                except Exception:  # noqa: BLE001 换下一个模块试
                    continue
        self._input_hooks = []

    def _hook_missed_input(self):
        """系统近期有输入而自家钩子没收到 ⇒ 判定钩子已失效（唯一的失效判据）。"""
        idle = last_input_seconds()
        if idle is None or idle > RECENT_INPUT_SECONDS:
            return False   # 空闲中或无法判定 ⇒ 不下结论
        return (time.monotonic() - self._observed_input) > MISSED_INPUT_SECONDS

    def _intercepted_hotkey(self):
        """连续 REQUIRED_MISSES 次「组合键已按下但回调未触发」的动作名（顺便清计数）。

        2026-10-14：抢占方通常只吞掉某个组合键，因而「系统有输入而自家钩子没收到」这条旧判据
        永远为假（敲别的键仍会刷新观察时间戳）；这里直接盯「该热键该响却没响」，并要求连续两次，
        避免把「多按了一个修饰键」这类偶发误判成被抢占（真机日志里正是一次误报触发了重装）。
        """
        for action, entry in list(self._misses.items()):
            if entry[0] >= REQUIRED_MISSES:
                self._misses.pop(action, None)
                return action
        return None

    def _maybe_log_heartbeat(self):
        """节流汇总（每 10 分钟一条）：让「体检确实在跑」有据可查（规则 14）。"""
        now = time.monotonic()
        if now - self._heartbeat_at < HEARTBEAT_SECONDS:
            return
        self._heartbeat_at = now
        logging.getLogger("screensnap").debug(
            "热键体检累计: 检查 %d 次，判定失效重装 %d 次，间隔 %d 秒",
            self.checks, self.reinstall_count,
            max(MIN_HEALTH_SECONDS, self.health_timer.interval() // 1000))

    def register(self, settings):
        """拷贝当前设置，防止主线程修改字典时工作线程读到半成品。"""
        # 体检周期属于设置项，注册时一并应用（QTimer 只能在本线程改）。
        self.set_health_interval(settings.get("hotkey_health_interval", DEFAULT_HEALTH_SECONDS))
        self.requests.put(dict(enabled=settings["hotkeys_enabled"], bindings=settings["hotkeys"].copy(),
                    suppress_capture=bool(settings.get("capture_hotkey_suppress", False))))

    def set_paused(self, paused):
        """立即屏蔽回调，覆盖后台线程尚未完成热键注销的短暂窗口。"""
        if paused:
            self.paused.set()
        else:
            self.paused.clear()

    def emit_action(self, action):
        """后台热键回调在暂停期间丢弃动作，避免设置页录制时误触发。"""
        if not self.paused.is_set():
            # 被抢占判定用的成对记录：触发过就清掉未触发计数。
            self._last_fired[action] = time.monotonic()
            self._misses.pop(action, None)
            # 留痕（节流）：区分「钩子没收到」与「收到了没动作」，排查热键失效用。
            try:
                from logger.log_rate import log_every

                log_every(logging.getLogger("screensnap"), logging.DEBUG,
                          f"hotkey-fire-{action}", action, 2.0,
                          "热键回调: action=%s 来源=keyboard钩子", action)
            except Exception as error:  # noqa: BLE001 日志失败绝不能影响热键本身
                # 注意（2026-10-11 踩过）：这里曾把模块路径写错（core.log_rate），ImportError 被静默吞掉，
                # 结果留痕永远为 0 条却看不出原因。异常至少记一次，别再让留痕悄悄失效。
                logging.getLogger("screensnap").debug("写热键回调留痕失败: %s", error)
            self.triggered.emit(action)

    def listener_alive(self):
        """试探 keyboard 的监听线程是否还活着。

        keyboard 是私有实现，属性名可能随版本变化，因此全部防御式读取：
        读不到就当作「无法判断」返回 True，避免误判导致无意义的重装。
        """
        listener = getattr(keyboard, "_listener", None)
        if listener is None:
            return False
        listening = getattr(listener, "listening", None)
        if listening is False:
            return False
        is_alive = getattr(listener, "is_alive", None)
        if callable(is_alive):
            try:
                return bool(is_alive())
            except Exception:  # noqa: BLE001
                return True
        return True

    def check_health(self, force=False):
        """体检：钩子被系统静默摘掉或库监听线程死亡时就地重装。

        `force=True` 表示由用户操作触发的按需体检（例如托盘双击截图）：它**无条件重装一次**，
        不看监听线程状态 —— 静默摘钩时监听线程仍然存活，任何「先判断再重装」的逻辑都发现不了它，
        而重装本身才是恢复动作（约 4ms，真机验证有效）。
        非 force 时的判据（按优先级）：① 监听线程不可用；② 某热键的组合键被通用钩子**连续两次**
        看到、回调却都没触发（被其它程序抢占/吞键，2026-10-14 新增）；③ 系统有输入而钩子没收到。
        其余什么都不做（不重装、不打日志）。

        适用场景（真机反馈，2026-10-11）：安全软件在钩子链里拖慢回调时，Windows 会按
        LowLevelHooksTimeout 静默移除 WH_KEYBOARD_LL 钩子，且不报错 —— 表现为「被拦一次后
        所有热键永久失效」。这里没有任何 Qt 窗口操作，只在必要时把重装请求排进工作线程。
        """
        if self._last_request is None:
            return
        self.checks += 1
        logger = logging.getLogger("screensnap")
        alive = self.listener_alive()
        # force 时无条件重装、不看判据；否则先取「组合键被吞」这一条（比“任何输入”判据更具体）。
        intercepted = None if force else self._intercepted_hotkey()
        if force:
            # 按需体检（用户操作触发）：无条件重装一次。
            # 理由（用户 2026-10-11 反馈「一直没看到恢复提示」）：Windows 静默摘掉 WH_KEYBOARD_LL
            # 钩子时，keyboard 的监听线程**仍然存活**、内部标志也是 True，光看标志位永远发现不了；
            # 而「重装」本身才是能真正恢复动作的操作（clear + add，毫秒级）。
            logger.info(
                "按需重装全局热键（用户操作触发）: 监听线程=%s 句柄=%d 第 %d 次",
                "正常" if alive else "不可用", len(self.handles), self.reinstall_count + 1)
        elif not alive:
            now = time.monotonic()
            if now - self._last_retry < RETRY_MIN_SECONDS:
                return
            logger.warning(
                "热键钩子疑似被系统摘掉（监听线程不可用），正在重装: 第 %d 次",
                self.reinstall_count + 1)
        elif intercepted is not None:
            # 2026-10-14：某热键的组合键已被通用钩子看到、回调却没响 ⇒ 疑被其它程序抢占/吞键。
            now = time.monotonic()
            if now - self._last_retry < RETRY_MIN_SECONDS:
                return
            logger.warning(
                "热键 %s 连续 %d 次按下组合键但未触发回调（疑被其它程序抢占/吞掉），正在重装: 第 %d 次",
                intercepted, REQUIRED_MISSES, self.reinstall_count + 1)
        elif self._hook_missed_input():
            # 系统近期有键鼠输入，而自家钩子两秒多没收到任何事件 ⇒ 判定钩子已被摘掉。
            now = time.monotonic()
            if now - self._last_retry < RETRY_MIN_SECONDS:
                return
            logger.warning(
                "热键钩子已失效（系统有输入但钩子未收到），正在重装: 第 %d 次",
                self.reinstall_count + 1)
        else:
            # 一切正常 ⇒ 什么都不做（不重装、不打日志），只做 10 分钟一次的汇总。
            self._maybe_log_heartbeat()
            return
        self._last_retry = time.monotonic()
        self.reinstall_count += 1
        if intercepted is not None:
            self.intercept_reinstalls += 1
        # 重装热键不影响观察钩子（不再卸钩/重挂）⇒ 只需重置观察时间戳与未触发计数，
        # 避免刚重装就被自己的旧计数判成“又失效了”。
        self._observed_input = time.monotonic()
        self._misses.clear()
        self.requests.put(dict(self._last_request))


    def listen(self):
        """在后台线程持续接收配置刷新及停止请求；任何异常都不得让本线程静默退出。"""
        while True:
            try:
                request = self.requests.get()
                if request is None:
                    self.clear()
                    return
                self.install(request)
            except (ValueError, OSError) as error:
                self.failed.emit(str(error))
            except Exception as error:  # noqa: BLE001 兜底：线程一旦死掉，热键就再也回不来
                logging.getLogger("screensnap").error(
                    "热键监听处理请求失败（线程继续运行）: %s", error, exc_info=True)

    def install(self, request):
        """先移除旧热键，部分注册失败时也清理已注册的部分。"""
        self._ensure_input_hooks()
        self.clear()
        if not request["enabled"]:
            self._hotkey_keys = {}
            self._misses.clear()
            # 关闭热键后必须清空重装依据：否则体检会拿着“上一次启用时的绑定”把热键注册回来。
            self._last_request = None
            return
        logger = logging.getLogger("screensnap")
        # 绑定解析只服务「组合键被吞」判定；解析不出来的项退回旧判据，不影响注册本身。
        self._hotkey_keys = {action: parse_binding_keys(binding)
                             for action, binding in request["bindings"].items() if binding}
        self._misses.clear()
        try:
            for action, binding in request["bindings"].items():
                if not binding:
                    continue
                try:
                    suppress = request.get("suppress_capture", False) and action in (
                        "capture", "repeat", "fullscreen", "monitor")
                    handle = keyboard.add_hotkey(
                        binding, lambda current=action: self.emit_action(current), suppress=suppress
                    )
                except (ValueError, OSError) as error:
                    # 单个热键被占用时记录并继续，其余可用热键仍然生效。
                    logger.error("注册热键失败 %s=%s: %s", action, binding, error)
                    self.failed.emit(f"{action}（{binding}）：{error}")
                    continue
                self.handles.append(handle)
            self._last_request = dict(request)   # 供健康检查重装使用
            logger.debug("已注册全局热键: %d 个", len(self.handles))
        except (ValueError, OSError):
            self.clear()
            raise

    def clear(self):
        """释放 keyboard 持有的所有全局热键句柄；单个失效句柄不得阻断后续注册。"""
        for handle in self.handles:
            try:
                keyboard.remove_hotkey(handle)
            except Exception as error:  # noqa: BLE001 2026-10-14：以前这里没兜住 ⇒ 线程会静默死
                logging.getLogger("screensnap").debug("移除热键句柄失败（已跳过）: %s", error)
        self.handles.clear()

    def stop(self):
        """通知监听线程注销热键，并等待有限时间避免退出卡住。"""
        self._stopping = True   # 先置位再卸载，防止工作线程把观察钩子又挂回去
        self._remove_input_hooks()
        for timer in (getattr(self, "health_timer", None),):
            try:
                if timer is not None:
                    timer.stop()
            except Exception:  # noqa: BLE001
                pass
        self.requests.put(None)
        self.worker.join(timeout=2)