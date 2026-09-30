"""keyboard 回调只向 Qt 主线程投递信号。"""

import logging
import queue
import threading

import keyboard
from PySide6.QtCore import QObject, Signal


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

    def register(self, settings):
        """拷贝当前设置，防止主线程修改字典时工作线程读到半成品。"""
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
            self.triggered.emit(action)

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
        self.requests.put(None)
        self.worker.join(timeout=2)