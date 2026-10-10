"""keyboard 回调只向 Qt 主线程投递信号。"""

import logging
import queue
import threading
import time

import keyboard
from PySide6.QtCore import QObject, QTimer, Signal

# 体检周期（设置项 hotkey_health_interval，15–600 秒）与重装限速。
# 规则 14：只在重装与每 10 分钟汇总时记录，不刷屏。
#
# 为什么是「定期预防性重装」而不是「合成键探针」（2026-10-11 实测）：
#   keyboard.send()/press_and_release() 合成的事件**不会触发该库自己的热键回调**
#   （5 种组合全部 NO-RESPONSE，日志里探针正常 0 次、无响应每周期一次）⇒ 探针会把
#   「钩子正常」误报成「已被摘掉」，每轮都白重装一次。既然无法低成本探测，就改为
#   「不看症状、定期刷新钩子」：恢复时间即本周期，且不注入任何按键（无外泄风险）。
DEFAULT_HEALTH_SECONDS = 60
MIN_HEALTH_SECONDS = 15
MAX_HEALTH_SECONDS = 600
HEARTBEAT_SECONDS = 600.0
RETRY_MIN_SECONDS = 30.0
# 主动探针：Windows 静默摘掉 WH_KEYBOARD_LL 钩子时，keyboard 的监听线程仍然存活，
# 只看标志位永远发现不了（真机验证 2026-10-11）。唯一可靠的判据是「发一次合成键，
# 看自家钩子有没有回调」——探针组合极冷门，且 suppress=True 让它在钩子正常时不外泄。


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
        # 单一周期定时器（用户选择「甲」，2026-10-11）：每跳①探活（线程死了立刻重装），
        # ②线程正常时做一次**预防性重装** —— 真机已证明「Windows 静默摘钩时监听线程仍存活」，
        # 且合成键探针不可用（见文件头注释），所以用定期刷新兜住这一类无法探测的失效。
        self.health_timer = QTimer(self)
        self.health_timer.setInterval(DEFAULT_HEALTH_SECONDS * 1000)
        self.health_timer.timeout.connect(self.check_health)
        self.health_timer.start()
        # 预防性重装计数与每 10 分钟一条的汇总时间戳。
        self.preventive_reinstalls = 0
        self._heartbeat_at = time.monotonic()

    def set_health_interval(self, seconds):
        """按设置调整体检周期（夹紧到 3–300 秒；只在真的变化时生效）。"""
        try:
            value = int(seconds)
        except (TypeError, ValueError):
            value = DEFAULT_HEALTH_SECONDS
        value = max(MIN_HEALTH_SECONDS, min(MAX_HEALTH_SECONDS, value))
        if self.health_timer.interval() == value * 1000:
            return
        self.health_timer.setInterval(value * 1000)
        logging.getLogger("screensnap").debug("热键体检间隔调整为 %d 秒", value)

    def _maybe_log_heartbeat(self):
        """节流汇总（每 10 分钟一条）：让「体检确实在跑」有据可查（规则 14）。"""
        now = time.monotonic()
        if now - self._heartbeat_at < HEARTBEAT_SECONDS:
            return
        self._heartbeat_at = now
        logging.getLogger("screensnap").debug(
            "热键体检累计: 预防性重装 %d 次，异常重装 %d 次，间隔 %d 秒",
            self.preventive_reinstalls, self.reinstall_count,
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

        `force=True` 表示由用户操作触发的按需体检（例如托盘双击截图），
        只跳过「限速」这一层，仍然只在监听线程确实不可用时才重装。

        适用场景（真机反馈，2026-10-11）：安全软件在钩子链里拖慢回调时，Windows 会按
        LowLevelHooksTimeout 静默移除 WH_KEYBOARD_LL 钩子，且不报错 —— 表现为「被拦一次后
        所有热键永久失效」。这里没有任何 Qt 窗口操作，只在必要时把重装请求排进工作线程。
        """
        if self._last_request is None:
            return
        logger = logging.getLogger("screensnap")
        alive = self.listener_alive()
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
        else:
            # 监听线程正常 ⇒ 仍做一次预防性重装：Windows 静默摘钩无法探测（实测），
            # 定期刷新是唯一能兜住它的办法，恢复时间就等于本周期。
            self.requests.put(dict(self._last_request))
            self.preventive_reinstalls += 1
            logger.debug("定期预防性重装全局热键: 第 %d 次（周期 %d 秒）",
                         self.preventive_reinstalls, self.health_timer.interval() // 1000)
            self._maybe_log_heartbeat()
            return
        self._last_retry = time.monotonic()
        self.reinstall_count += 1
        self.requests.put(dict(self._last_request))


    def listen(self):
        """在后台线程持续接收配置刷新及停止请求。"""
        while True:
            request = self.requests.get()
            if request is None:
                self.clear()
                return
            try:
                self.install(request)
            except (ValueError, OSError) as error:
                self.failed.emit(str(error))

    def install(self, request):
        """先移除旧热键，部分注册失败时也清理已注册的部分。"""
        self.clear()
        if not request["enabled"]:
            return
        logger = logging.getLogger("screensnap")
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
        """释放 keyboard 持有的所有全局热键句柄。"""
        for handle in self.handles:
            keyboard.remove_hotkey(handle)
        self.handles.clear()

    def stop(self):
        """通知监听线程注销热键，并等待有限时间避免退出卡住。"""
        for timer in (getattr(self, "health_timer", None),):
            try:
                if timer is not None:
                    timer.stop()
            except Exception:  # noqa: BLE001
                pass
        self.requests.put(None)
        self.worker.join(timeout=2)