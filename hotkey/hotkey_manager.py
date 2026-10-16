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
from PySide6.QtCore import QObject, QTimer, Signal, Slot
from core.hotkey_policy import effective_policy, normalize_key_name
from hotkey.recovery import RecoveryState
from logger import get_logger
from hotkey.diagnostics import DiagnosticsMixin

# 体检周期（设置项 hotkey_health_interval，2–600 秒）与重装限速。
# 规则 14：健康时既不重装也不打日志，只在**判定失效**并重装时记录。
#
# 失效判据（2026-10-11 真机三轮迭代后的最终方案）：
#   · Windows 的 GetLastInputInfo 给出全系统最后一次键鼠输入的时刻；
#   · 我们额外挂一个键盘钩子与鼠标钩子，只记录「最后一次收到事件的时刻」；
#   · 系统说刚刚有输入、而自家钩子两秒多没收到任何事件 ⇒ 输入链疑似异常 ⇒ 尝试重建绑定。
# 2026-10-15：keyboard 0.13.5 的 remove/add 只改回调表，不重建 WH_KEYBOARD_LL，
# 不能将“18 个绑定已安装”或 listening=True 当作底层钩子恢复的证据。
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
    text = str(binding or "").strip().lower()
    if not text or "," in text:
        return None
    parts = [normalize_key_name(part) for part in text.split("+")]
    if not all(parts):
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
        tick = ctypes.windll.kernel32.GetTickCount
        tick.restype = ctypes.c_uint32
        return ((tick() - info.dwTime) & 0xFFFFFFFF) / 1000.0
    except Exception:  # noqa: BLE001 取不到就不下结论
        return None


class HotkeyManager(DiagnosticsMixin, QObject):
    """队列串行处理注册请求；监听线程只发信号，不直接操作 Qt 窗口。"""

    triggered = Signal(str)
    failed = Signal(str)
    _callback_trace = Signal(str, float, int, int, bool)
    _evidence_ready = Signal()
    registration_finished = Signal(object)
    notice = Signal(str)

    def __init__(self):
        super().__init__()
        self.handles = []
        self.paused = threading.Event()
        self.requests = queue.Queue()
        self.worker = threading.Thread(target=self.listen, daemon=True, name="global-hotkeys")
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
        self._observed_keyboard_input = None
        self._observed_mouse_input = None
        self._keyboard_events = 0
        self._mouse_events = 0
        self._callback_events = 0
        self._paused_callbacks = 0
        self._qt_callbacks = 0
        self._observer_error = None
        self._diagnostic_error = None
        self._registration_requested = 0
        self._registration_started = 0
        self._registration_completed = 0
        self._registration_failed = 0
        self._install_started_at = None
        self._verified_generation = 0
        self._trace_at = {}
        self.policy = effective_policy({})
        self.recovery = RecoveryState()
        self._evidence_lock = threading.RLock()
        self._pending_attempts = {}
        self._desired_request = None
        self._resume_settings = False
        self._policy_configured = False
        self._grace_until = 0.0
        self._health_tick_at = time.monotonic()
        self._session_available = True
        self._registration_state = "idle"
        self._stalled_generation = None
        self._active_generation = 0
        self._input_epoch = 0
        self._automatic_epoch = 0
        self._busy_cached = False
        self._actions_enabled = True
        self.busy_check = lambda: False
        self._health_seconds = DEFAULT_HEALTH_SECONDS
        self._input_hooks = []
        self._keyboard_observer = False
        self._mouse_observer = False
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
        self._callback_trace.connect(self._log_callback)
        self.registration_finished.connect(self._registration_arrived)
        self._init_diagnostics()
        self._evidence_timer = QTimer(self)
        self._evidence_timer.setSingleShot(True)
        self._evidence_timer.timeout.connect(self.check_health)
        self._evidence_ready.connect(self._schedule_evidence)
        self._housekeeping_timer = QTimer(self)
        self._housekeeping_timer.setInterval(500)
        self._housekeeping_timer.timeout.connect(self._housekeeping)
        self._housekeeping_timer.start()
        # 所有纯 Python 状态初始化完才启动工作线程。
        self.worker.start()

    def set_health_interval(self, seconds):
        """按设置调整体检周期（夹紧到 2–600 秒；只在真的变化时生效）。"""
        try:
            value = int(seconds)
        except (TypeError, ValueError):
            value = DEFAULT_HEALTH_SECONDS
        value = max(MIN_HEALTH_SECONDS, min(MAX_HEALTH_SECONDS, value))
        if self.health_timer.interval() == value * 1000:
            return
        self._health_seconds = value
        self.health_timer.setInterval(value * 1000)
        get_logger().debug("热键体检间隔调整为 %d 秒", value)

    def _note_input(self, event=None):
        """通用钩子回调：记时间戳、维护本地修饰键状态，并累计「组合键已按下但未触发」。

        ⚠ 高频路径且**绝不允许调用 keyboard 的任何 API**（规则 14 + 2026-10-14 真机教训：
        在钩子回调里查按键状态会让真实钩子链上的热键回调整体失效）。
        这里只做纯 Python 的集合/字符串判断，并且整体兜住异常，保证回调永不抛异常。
        """
        try:
            now = time.monotonic()
            self._observed_input = now
            self._observed_keyboard_input = now
            self._keyboard_events += 1
            name = getattr(event, "name", None)
            if name is None:
                return          # 鼠标事件没有 name
            name = normalize_key_name(name)
            with self._evidence_lock:
                down = getattr(event, "event_type", "down") == "down"
                repeated = down and name in self._pressed_names
                if down:
                    self._pressed_names.add(name)
                else:
                    self._pressed_names.discard(name)
                if not down or repeated or self.paused.is_set() or self._stopping or not self._session_available:
                    return
                if not self.policy["hotkey_criterion_misses"]:
                    return
                modifiers_down = set()
                ordinary_down = set()
                for held in self._pressed_names:
                    token = held.removeprefix("left ").removeprefix("right ")
                    modifier = _MODIFIER_ALIASES.get(token)
                    if modifier:
                        modifiers_down.add(modifier)
                    else:
                        ordinary_down.add(held)
                for action, parsed in self._hotkey_keys.items():
                    if parsed is None:
                        continue
                    modifiers, main = parsed
                    # keyboard匹配全部按下的键；额外普通键同样意味着组合本来就不应触发。
                    if main != name or modifiers_down != modifiers or ordinary_down != {main}:
                        continue
                    grace = self.policy["hotkey_callback_grace_ms"] / 1000.0
                    if now - self._last_fired.get(action, -float("inf")) <= grace:
                        continue
                    attempts = self._pending_attempts.setdefault(action, [])
                    attempts.append(now)
                    del attempts[:-5]  # 硬上限；钩子回调不做库查询、日志或Qt操作。
                    self._evidence_ready.emit()
        except Exception as error:  # noqa: BLE001 高频回调只记类型，诊断在体检/注册边界输出
            self._observer_error = type(error).__name__

    def _note_mouse_input(self, event=None):
        """只记鼠标事件计数/时间，不让鼠标输入掩盖诊断中的键盘停滞。"""
        now = time.monotonic()
        self._observed_input = now
        self._observed_mouse_input = now
        self._mouse_events += 1

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
                get_logger().debug(
                    "鼠标钩子导入期警告已捕获并忽略: %s: %s", item.category.__name__, item.message)
            return legacy_mouse, "keyboard.mouse(已废弃)"
        except Exception:  # noqa: BLE001
            return None, ""
    def _ensure_input_hooks(self):
        """挂上键盘与鼠标的通用钩子，用于输入观察与「组合键被吞」判定。

        只做添加，不在重装时先卸再挂（2026-10-14 回退）。绑定重建不能证明
        底层原生钩子恢复；诊断保留原观察器，避免盲目重置共享 listener 影响遮罩临时键。
        """
        if getattr(self, "_stopping", False):
            return
        logger = get_logger()
        before = (self._keyboard_observer, self._mouse_observer)
        if not self._keyboard_observer:
            with self._evidence_lock:
                self._pressed_names.clear()
            try:
                self._input_hooks.append((keyboard, keyboard.hook(self._note_input)))
                self._keyboard_observer = True
                self._observer_error = None
            except Exception as error:  # noqa: BLE001 缺失观察器允许下一次注册补装
                self._observer_error = type(error).__name__
                logger.warning("键盘观察钩子挂载失败: type=%s", self._observer_error,
                               extra={"screensnap_metadata_only": True})
        mouse, source = getattr(self, "_mouse", None), getattr(self, "_mouse_source", "")
        if mouse is not None and not self._mouse_observer and not self._stopping:
            try:
                self._input_hooks.append((mouse, mouse.hook(self._note_mouse_input)))
                self._mouse_observer = True
            except Exception as error:  # noqa: BLE001
                self._observer_error = type(error).__name__
                logger.warning("鼠标观察钩子挂载失败: type=%s", self._observer_error,
                               extra={"screensnap_metadata_only": True})
        if before != (self._keyboard_observer, self._mouse_observer):
            logger.debug("输入观察钩子挂载结果: 键盘=%s 鼠标=%s 来源=%s",
                         self._keyboard_observer, self._mouse_observer, source or "无",
                         extra={"screensnap_metadata_only": True})

    def _remove_input_hooks(self):
        """卸载输入观察钩子（只在停止时调用，重装热键时保留）。"""
        for module, handle in self._input_hooks:
            try:
                module.unhook(handle)
            except Exception as error:  # noqa: BLE001 只操作创建该句柄的模块
                get_logger().warning("观察钩子释放失败: type=%s", type(error).__name__,
                                                       extra={"screensnap_metadata_only": True})
        self._input_hooks = []
        self._keyboard_observer = False
        self._mouse_observer = False

    def _hook_missed_input(self):
        """系统近期有输入而观察器没收到：只说明疑似输入链异常，不能证明系统摘钩。"""
        idle = last_input_seconds()
        if idle is None or idle > self.policy["hotkey_recent_input_ms"] / 1000.0:
            return False   # 空闲中或无法判定 ⇒ 不下结论
        return (time.monotonic() - self._observed_input) > self.policy["hotkey_missed_input_ms"] / 1000.0

    def _intercepted_hotkey(self):
        """连续 REQUIRED_MISSES 次「组合键已按下但回调未触发」的动作名（顺便清计数）。

        2026-10-14：抢占方通常只吞掉某个组合键，因而「系统有输入而自家钩子没收到」这条旧判据
        永远为假（敲别的键仍会刷新观察时间戳）；这里直接盯「该热键该响却没响」，并要求连续两次，
        避免把「多按了一个修饰键」这类偶发误判成被抢占（真机日志里正是一次误报触发了重装）。
        """
        now = time.monotonic()
        with self._evidence_lock:
            for action, attempts in list(self._pending_attempts.items()):
                valid = [stamp for stamp in attempts if now - stamp <= self.policy["hotkey_miss_window"]]
                self._pending_attempts[action] = valid
                mature = [stamp for stamp in valid if now - stamp >= self.policy["hotkey_callback_grace_ms"] / 1000.0]
                if mature:
                    self._misses[action] = [len(mature), mature[0]]
                else:
                    self._misses.pop(action, None)
            for action, entry in list(self._misses.items()):
                if now - entry[1] > self.policy["hotkey_miss_window"]:
                    self._misses.pop(action, None)
                elif entry[0] >= self.policy["hotkey_miss_count"]:
                    return action  # 限速期间不消费证据；真正开始尝试时才清。
        return None

    def _maybe_log_heartbeat(self):
        """只由独立汇总计时器输出，健康检查不会另产一条重复汇总。"""
        return

    @Slot()
    def _schedule_evidence(self):
        if not self._stopping and self.policy["hotkey_health_enabled"]:
            self._evidence_timer.start(self.policy["hotkey_callback_grace_ms"] + 10)

    def set_busy_state(self, busy):
        busy = bool(busy)
        if busy != self._busy_cached:
            self._busy_cached = busy
            if busy:
                self._automatic_epoch += 1

    def _housekeeping(self):
        if not self._stopping:
            self.set_busy_state(self.busy_check())
            self._resume_settings_intent()
            if self._install_started_at is not None:
                self.check_health()

    def _record_fault(self, reason, actions, now):
        fresh = not self.recovery.reason
        self.recovery.fault(reason, actions)
        if self.policy["hotkey_snapshot_on_fault"] and not self.recovery.snapshot_taken:
            self.recovery.snapshot_taken = True
            self.capture_snapshot(reason)
        if fresh:
            self.log_diagnostics("fault:" + reason, logging.WARNING)
        if self.recovery.notice_due(now, self.policy):
            self.notice.emit("热键输入链出现异常证据；绑定重建不保证原生钩子恢复")

    def health_evidence(self):
        """只读判定，可用于关闭自动体检后的手动检查；不重建绑定。"""
        action = self._intercepted_hotkey() if self.policy["hotkey_criterion_misses"] else None
        return dict(threads_unavailable=self.policy["hotkey_criterion_threads"] and not self.listener_alive(),
                    unfired_action=action,
                    input_stale=self.policy["hotkey_criterion_input"] and self._hook_missed_input())

    @staticmethod
    def _thread_alive(thread):
        try:
            method = getattr(thread, "is_alive", None)
            return bool(method()) if callable(method) else None
        except Exception:  # noqa: BLE001 私有库对象可能没有该属性
            return None

    def diagnostic_snapshot(self):
        """仅状态/数量；不读按键名称、扫描码、事件内容，不调用库的输入 API。"""
        now = time.monotonic()
        listener = getattr(keyboard, "_listener", None)

        def count(owner, attribute):
            try:
                return len(getattr(owner, attribute))
            except (AttributeError, TypeError):
                return None

        def age(value):
            return round(max(0.0, now - value), 2) if value is not None else None

        def queue_size(owner):
            try:
                return owner.qsize()
            except (AttributeError, TypeError):
                return None

        return dict(
            listening=getattr(listener, "listening", None),
            native_thread=self._thread_alive(getattr(listener, "listening_thread", None)),
            processing_thread=self._thread_alive(getattr(listener, "processing_thread", None)),
            worker=self._thread_alive(self.worker), pending=self.requests.qsize(),
            backend_pending=queue_size(getattr(listener, "queue", None)),
            handles=len(self.handles), observers=len(self._input_hooks), paused=self.paused.is_set(),
            keyboard_observer=self._keyboard_observer, mouse_observer=self._mouse_observer,
            stopping=self._stopping, enabled=(self._last_request or {}).get("enabled"),
            suppress_capture=(self._last_request or {}).get("suppress_capture"),
            requested=self._registration_requested, started=self._registration_started,
            completed=self._registration_completed, failed=self._registration_failed,
            install_age=age(self._install_started_at),
            keyboard_age=age(self._observed_keyboard_input), mouse_age=age(self._observed_mouse_input),
            keyboard_events=self._keyboard_events, mouse_events=self._mouse_events,
            callbacks=self._callback_events, qt_callbacks=self._qt_callbacks,
            paused_callbacks=self._paused_callbacks, observer_error=self._observer_error,
            diagnostic_error=self._diagnostic_error,
            pressed_count=count(keyboard, "_pressed_events"),
            logical_pressed_count=count(keyboard, "_logically_pressed_keys"),
            local_pressed_count=len(self._pressed_names),
            active_modifiers=count(listener, "active_modifiers"),
            modifier_states=count(listener, "modifier_states"),
            replaying=getattr(listener, "is_replaying", None),
            checks=self.checks, reinstalls=self.reinstall_count, interval=self._health_seconds)

    def log_diagnostics(self, reason, level=logging.INFO):
        """关键边界及十分钟汇总；标记禁止上下文过滤器访问 Qt/窗口。"""
        try:
            self._diagnostic_write(level, "热键诊断: reason=%s state=%s", reason, self.diagnostic_snapshot())
        except Exception as error:  # noqa: BLE001 诊断永远不能成为注册失败的原因
            self._diagnostic_error = type(error).__name__

    def _queue_registration(self, request, reason, recovery=False, automatic=False):
        if self._stopping:
            return False
        if self._thread_alive(self.worker) is False:
            self._registration_state = "worker-unavailable"
            self.log_diagnostics("registration-worker-dead:restart-required", logging.ERROR)
            return False
        self._registration_requested += 1
        self._registration_state = "queued"
        self.log_diagnostics("queued:" + reason)
        self.requests.put(dict(request, _generation=self._registration_requested, _reason=reason, _recovery=recovery,
                               _automatic=automatic, _epoch=self._automatic_epoch, _input_epoch=self._input_epoch))
        return True

    def configure_policy(self, settings):
        first = not self._policy_configured
        updated = effective_policy(settings)
        if updated != self.policy:
            self._automatic_epoch += 1
            self.recovery.reconfigure(updated)
        self.policy = updated
        self.set_health_interval(self.policy["hotkey_health_interval"])
        if self.policy["hotkey_health_enabled"]:
            if not self.health_timer.isActive():
                self.health_timer.start()
        else:
            self.health_timer.stop()
        if first and "hotkey_startup_grace" in settings:
            self._grace_until = time.monotonic() + self.policy["hotkey_startup_grace"]
        self._policy_configured = True
        self._apply_diagnostic_policy()

    def session_changed(self, available):
        self._session_available = bool(available)
        self._input_epoch += 1
        self._automatic_epoch += 1
        with self._evidence_lock:
            self._pending_attempts.clear()
            self._misses.clear()
            self._pressed_names.clear()
        if available:
            self._grace_until = time.monotonic() + self.policy["hotkey_wake_grace"]
        self.log_diagnostics("session-resumed" if available else "session-unavailable")
        self._resume_settings_intent()

    def _resume_settings_intent(self):
        # 补完显式设置或已清旧句柄的中途回滚，不重新执行已撤销的自动恢复。
        # 关闭体检也不能令用户原来启用的绑定永远为空；忙时仍不干预截图/编辑。
        if (self._resume_settings and self._session_available and not self.paused.is_set()
                and not self._stopping and self._actions_enabled
                and time.monotonic() >= self._grace_until and self._desired_request
                and self._desired_request["enabled"] and self._install_started_at is None
                and self.requests.empty()
                and not (self.policy["hotkey_defer_busy"] and self._busy_cached)):
            if self._queue_registration(self._desired_request, "bindings-restored"):
                self._resume_settings = False

    def register(self, settings):
        """拷贝当前设置，防止主线程修改字典时工作线程读到半成品。"""
        # 体检周期属于设置项，注册时一并应用（QTimer 只能在本线程改）。
        self.configure_policy(settings)
        request = dict(enabled=settings["hotkeys_enabled"], bindings=settings["hotkeys"].copy(),
                       suppress_capture=bool(settings.get("capture_hotkey_suppress", False)))
        if request != self._desired_request:
            self.recovery.reset()
            with self._evidence_lock:
                self._pending_attempts.clear()
                self._misses.clear()
                self._last_fired.clear()
            self._input_epoch += 1
        self._actions_enabled = request["enabled"]
        self._desired_request = request
        self._resume_settings = False
        return self._queue_registration(request, "settings")

    def manual_recover(self, settings):
        self.configure_policy(settings)
        self._desired_request = dict(enabled=settings["hotkeys_enabled"], bindings=settings["hotkeys"].copy(),
                                     suppress_capture=bool(settings.get("capture_hotkey_suppress", False)))
        if not self.recovery.permit(time.monotonic(), self.policy, manual=True):
            return False
        self._actions_enabled = self._desired_request["enabled"]
        return self._queue_registration(self._desired_request, "manual", recovery=True)

    def set_paused(self, paused):
        """立即屏蔽回调，覆盖后台线程尚未完成热键注销的短暂窗口。"""
        if self.paused.is_set() != bool(paused):
            self._input_epoch += 1
            self._automatic_epoch += 1
        if paused:
            self.paused.set()
            with self._evidence_lock:
                self._pending_attempts.clear()
                self._misses.clear()
        else:
            self.paused.clear()

    def emit_action(self, action, generation=None):
        """后台热键回调在暂停期间丢弃动作，避免设置页录制时误触发。"""
        self._callback_events += 1
        generation = self._active_generation if generation is None else generation
        if not self.paused.is_set() and not self._stopping and self._session_available and self._actions_enabled and generation == self._active_generation:
            # 被抢占判定用的成对记录：触发过就清掉未触发计数。
            self._last_fired[action] = time.monotonic()
            with self._evidence_lock:
                self._misses.pop(action, None)
                self._pending_attempts.pop(action, None)
            # suppress=True 的回调在 WH_KEYBOARD_LL 线程上，绝不能同步日志/采集 Qt 上下文。
            # 每次只发信号；日志与 Qt 到达计数在对象所属的主线程执行。
            suppress_action = time.monotonic() < self._verification_until
            try:
                self._callback_trace.emit(action, self._last_fired[action], generation, self._input_epoch, suppress_action)
            except RuntimeError:
                # 状态检查与信号发射之间QObject也可能析构；异常不得杀死库处理线程。
                self._paused_callbacks += 1
        else:
            self._paused_callbacks += 1

    @Slot(str, float, int, int, bool)
    def _log_callback(self, action, fired_at, generation, epoch, suppress_action):
        if self.paused.is_set() or self._stopping or not self._session_available or not self._actions_enabled or generation != self._active_generation or epoch != self._input_epoch:
            self._paused_callbacks += 1
            return
        self._qt_callbacks += 1
        now = time.monotonic()
        relevant = self.recovery.callback(action)
        if relevant and not self.recovery.reason and self.policy["hotkey_recovered_notice"]:
            self.notice.emit("故障涉及的热键回调已到达主线程；不代表其他热键均已恢复")
        if generation != self._verified_generation:
            self._verified_generation = generation
            self.log_diagnostics("callback-reached-qt")
        if now - self._trace_at.get(action, -float("inf")) >= self.policy["hotkey_trace_interval"]:
            self._trace_at[action] = now
            self._diagnostic_write(logging.DEBUG,
                "热键回调: action=%s 来源=keyboard钩子 Qt投递延迟=%.3fs generation=%d",
                action, max(0.0, now - fired_at), generation)
        if not suppress_action:
            self.triggered.emit(action)

    @Slot(object)
    def _registration_arrived(self, result):
        if self._stopping or result["generation"] != self._registration_requested:
            return
        self._registration_result = result
        self._registration_state = result["state"]
        if result.get("failed"):
            request = self._desired_request or self._last_request or {"bindings": {}}
            actions = result.get("failed_actions") or [a for a, binding in request["bindings"].items() if binding]
            self._record_fault("registration-failed", actions, time.monotonic())
        if result["recovery"]:
            self.recovery.installed(result["failed"] == 0)
        self.log_diagnostics("registration-result")

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
        # keyboard 0.13.5 没有 listener.is_alive()；真实线程在这两个属性上。
        states = [self._thread_alive(getattr(listener, name, None))
                  for name in ("listening_thread", "processing_thread")]
        states.append(self._thread_alive(listener))  # 兼容其它实现/旧测试桩
        return False not in states

    def check_health(self, force=False):
        """有异常证据/用户强制时重建绑定并留痕；不声称重新安装了底层原生钩子。"""
        if self._stopping or self.paused.is_set() or not self._session_available:
            return
        request = self._desired_request or self._last_request
        if request is None or not request["enabled"]:
            return
        now = time.monotonic()
        if now - self._health_tick_at > max(30.0, self._health_seconds * 3):
            self.session_changed(True)
        self._health_tick_at = now
        if not force and (not self.policy["hotkey_health_enabled"] or now < self._grace_until):
            return
        self.checks += 1
        alive = self.listener_alive()
        if self._thread_alive(self.worker) is False:
            self._registration_state = "worker-unavailable"
            self._record_fault("registration-worker-dead", [a for a, b in request["bindings"].items() if b], now)
            if time.monotonic() - self._last_retry >= RETRY_MIN_SECONDS or force:
                self._last_retry = time.monotonic()
                self.log_diagnostics("registration-worker-dead:restart-required", logging.ERROR)
            return  # 向已死线程继续排队不会恢复，且会掩盖现场。
        if self._install_started_at is not None:
            if now - self._install_started_at >= self.policy["hotkey_install_timeout"] and self._stalled_generation != self._registration_started:
                self._stalled_generation = self._registration_started
                self._registration_state = "stalled"
                self._record_fault("registration-stalled", [a for a, b in request["bindings"].items() if b], now)
                self.log_diagnostics("registration-stalled", logging.WARNING)
            return
        intercepted = self._intercepted_hotkey() if self.policy["hotkey_criterion_misses"] else None
        if force:
            reason = "force"
        elif not alive and self.policy["hotkey_criterion_threads"]:
            reason = "listener-unavailable"
        elif intercepted is not None:
            reason = f"unfired:{intercepted}"
        elif self.policy["hotkey_criterion_input"] and self._hook_missed_input():
            reason = "input-stale"
        elif self._registration_state in ("failed", "partial") and self.recovery.reason:
            reason = "registration-failed"
        else:
            self._maybe_log_heartbeat()
            return
        actions = [intercepted] if intercepted else [action for action, binding in request["bindings"].items() if binding]
        if not force:
            self._record_fault(reason, actions, now)
        if not force and not self.policy["recovery_effective"]:
            return
        if not force and self.policy["hotkey_defer_busy"] and self.busy_check():
            self.set_busy_state(True)
            self.recovery.state = "deferred"
            return
        if not self.recovery.permit(now, self.policy, manual=force):
            return
        if self._registration_state == "queued":
            return
        self._last_retry = now
        # 重装热键不影响观察钩子（不再卸钩/重挂）⇒ 只需重置观察时间戳与未触发计数，
        # 避免刚重装就被自己的旧计数判成“又失效了”。
        self.log_diagnostics("未触发回调/输入异常:attempt:" + reason, logging.WARNING)
        self._queue_registration(request, reason, recovery=True, automatic=not force)


    def _request_valid(self, request):
        # 入队代次用于结果归属，不是用户绑定意图：失效的自动尝试不能取消有效settings。
        if self._stopping:
            return False
        if not request["enabled"]:
            return True  # 暂停/关闭请求仍需注销旧绑定。
        if self.paused.is_set() or not self._session_available or not self._actions_enabled or request.get("_input_epoch") != self._input_epoch:
            return False
        return not request.get("_automatic") or (request.get("_epoch") == self._automatic_epoch and
                self.policy["recovery_effective"] and not (self.policy["hotkey_defer_busy"] and self._busy_cached))

    def _post_registration(self, result):
        try:
            self.registration_finished.emit(result)
        except RuntimeError:
            self._diagnostic_error = "RuntimeError"

    def _cancel_install(self, request):
        self.clear()
        self._hotkey_keys = {}
        self._last_request = None
        # 此时旧绑定已移除，取消重试不等于用户取消了常驻绑定。恢复当前期望配置，
        # 不增加重试次数，也不绕过暂停/会话/忙状态；未开始的取消不走此分支。
        self._resume_settings = bool(self._desired_request and self._desired_request["enabled"]
                                     and self._actions_enabled and not self._stopping)
        self._registration_completed = request.get("_generation", 0)
        self._registration_state = "cancelled"
        self.log_diagnostics("install-cancelled:stale-intent")
        self._post_registration(dict(generation=self._registration_completed, state="cancelled", installed=0, failed=0, recovery=False))

    def listen(self):
        """在后台线程持续接收配置刷新及停止请求；任何异常都不得让本线程静默退出。"""
        while True:
            request = None
            try:
                request = self.requests.get()
                # 一个正在安装 + 一个最新完整意图；停止哨兵永不被后续请求覆盖。
                while request is not None:
                    try:
                        newer = self.requests.get_nowait()
                    except queue.Empty:
                        break
                    request = newer
                if request is None:
                    self.clear()
                    self._remove_input_hooks()  # 最终卸载，覆盖stop与安装观察器之间的竞态。
                    return
                if not self._request_valid(request):
                    self._registration_state = "cancelled"
                    if request.get("_reason") in ("settings", "session-resumed", "bindings-restored"):
                        self._resume_settings = bool(self._desired_request and self._desired_request["enabled"]
                                                     and self._actions_enabled and not self._stopping)
                    self._registration_completed = request.get("_generation", 0)
                    self.log_diagnostics("registration-cancelled:stale-intent")
                    continue
                now = time.monotonic()
                if request.get("_recovery") and request["enabled"] and not self.recovery.try_start(now, self.policy,
                        manual=not request.get("_automatic"), reset_batch=not request.get("_automatic") and self.policy["hotkey_manual_reset"]):
                    self._registration_state = "cancelled"
                    self._registration_completed = request.get("_generation", 0)
                    self.log_diagnostics("registration-cancelled:recovery-policy")
                    continue
                self._registration_started = request.get("_generation", 0)
                self._active_generation = self._registration_started
                self._install_started_at = time.monotonic()
                self._registration_state = "installing"
                if request.get("_recovery") and request["enabled"] and not self.paused.is_set():
                    self._last_retry = self._install_started_at
                    self.reinstall_count += 1
                    if request.get("_reason", "").startswith("unfired:"):
                        self.intercept_reinstalls += 1
                    with self._evidence_lock:
                        self._pending_attempts.clear()
                        self._misses.clear()
                self.log_diagnostics("install-start:" + request.get("_reason", "unknown"))
                self.install(request)
            except Exception as error:  # noqa: BLE001 兜底：线程一旦死掉，热键就再也回不来
                self._registration_failed = self._registration_started
                self.log_diagnostics("install-error:" + type(error).__name__, logging.ERROR)
                get_logger().error(
                    "热键监听处理请求失败（线程继续运行）: %s", error, exc_info=True,
                    extra={"screensnap_metadata_only": True})
                self.clear()
                self._registration_state = "failed"
                self._post_registration(dict(generation=self._registration_started, state="failed", installed=0,
                                             failed=1, failed_actions=[], recovery=bool(request and request.get("_recovery"))))
            finally:
                self._install_started_at = None

    def install(self, request):
        """先移除旧热键，部分注册失败时也清理已注册的部分。"""
        self._ensure_input_hooks()
        self.clear()
        if not request["enabled"] or self.paused.is_set() or self._stopping:
            self._hotkey_keys = {}
            with self._evidence_lock:
                self._misses.clear()
                self._pending_attempts.clear()
            # 关闭热键后必须清空重装依据：否则体检会拿着“上一次启用时的绑定”把热键注册回来。
            self._last_request = None
            self._registration_completed = request.get("_generation", 0)
            self.log_diagnostics("installed-disabled")
            self._registration_state = "disabled"
            self._post_registration(dict(generation=request.get("_generation", 0), state="disabled", installed=0, failed=0, recovery=False))
            return
        logger = get_logger()
        # 绑定解析只服务「组合键被吞」判定；解析不出来的项退回旧判据，不影响注册本身。
        self._hotkey_keys = {action: parse_binding_keys(binding)
                             for action, binding in request["bindings"].items() if binding}
        with self._evidence_lock:
            self._misses.clear()
        failures = 0
        failed_actions = []
        try:
            for action, binding in request["bindings"].items():
                if not binding:
                    continue
                if not self._request_valid(request):
                    self._cancel_install(request)
                    return
                try:
                    suppress = request.get("suppress_capture", False) and action in (
                        "capture", "repeat", "fullscreen", "monitor")
                    handle = keyboard.add_hotkey(
                        binding, lambda current=action, gen=request.get("_generation", 0): self.emit_action(current, gen), suppress=suppress
                    )
                except (ValueError, OSError) as error:
                    # 单个热键被占用时记录并继续，其余可用热键仍然生效。
                    self._registration_failed = request.get("_generation", 0)
                    failures += 1
                    failed_actions.append(action)
                    logger.error("注册热键失败 %s=%s: %s", action, binding, error,
                                 extra={"screensnap_metadata_only": True})
                    continue
                self.handles.append(handle)
                # 库调用可能阻塞，期间录制/关闭/会话/策略已改变；迟到句柄必须回滚。
                if not self._request_valid(request):
                    self._cancel_install(request)
                    return
            if not self._request_valid(request):
                self._cancel_install(request)
                return
            self._last_request = dict(request)   # 供健康检查重装使用
            self._resume_settings = False
            self._registration_completed = request.get("_generation", 0)
            self._registration_state = "partial" if failures else "completed"
            self._post_registration(dict(generation=self._registration_completed, state=self._registration_state,
                                         installed=len(self.handles), failed=failures, failed_actions=failed_actions,
                                         recovery=bool(request.get("_recovery"))))
            self.log_diagnostics("installed-bindings-only",
                                 logging.INFO if self.listener_alive() else logging.WARNING)
        except (ValueError, OSError):
            self.clear()
            raise

    def clear(self):
        """释放 keyboard 持有的所有全局热键句柄；单个失效句柄不得阻断后续注册。"""
        for handle in self.handles:
            try:
                keyboard.remove_hotkey(handle)
            except Exception as error:  # noqa: BLE001 2026-10-14：以前这里没兜住 ⇒ 线程会静默死
                get_logger().warning("移除热键句柄失败（已跳过）: %s", error,
                                                       extra={"screensnap_metadata_only": True})
        self.handles.clear()

    def stop(self):
        """通知监听线程注销热键，并等待有限时间避免退出卡住。"""
        self._stopping = True  # 观察器由注册worker串行清理，避免与正在挂载的句柄竞态。
        for timer in (getattr(self, name, None) for name in ("health_timer", "_summary_timer", "_detail_timer", "_verification_timer", "_evidence_timer", "_housekeeping_timer")):
            try:
                if timer is not None:
                    timer.stop()
            except Exception:  # noqa: BLE001
                pass
        self.requests.put(None)
        self.worker.join(timeout=2)
        if self.worker.is_alive():
            self.log_diagnostics("shutdown-cleanup-pending:worker-still-alive", logging.WARNING)
