"""常驻隐藏窗口接收会话/电源通知，不把托盘应用失去焦点当成锁屏。"""

import ctypes
import os
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QWidget

from logger import get_logger


class SessionMonitor(QWidget):
    available_changed = Signal(bool)

    def __init__(self):
        super().__init__(None, Qt.Tool)
        self.setAttribute(Qt.WA_DontShowOnScreen, True)
        self.registered = False
        self._handle = None
        self._locked = self._suspended = False
        self._connected = True
        self._available = True
        if os.name != "nt" or os.environ.get("QT_QPA_PLATFORM") == "offscreen":
            return
        try:
            self._handle = int(self.winId())
            function = ctypes.windll.wtsapi32.WTSRegisterSessionNotification
            function.argtypes = (ctypes.c_void_p, ctypes.c_uint)
            function.restype = ctypes.c_bool
            self.registered = bool(function(self._handle, 0))
            get_logger().info("会话状态观察: registered=%s", self.registered,
                                                 extra={"screensnap_metadata_only": True})
        except (AttributeError, OSError, RuntimeError):
            get_logger().warning("会话通知不可用，使用计时空档宽限兜底",
                                                    extra={"screensnap_metadata_only": True})

    def nativeEvent(self, event_type, message):
        if os.name == "nt":
            from ctypes import wintypes
            msg = wintypes.MSG.from_address(int(message))
            self.process_notification(msg.message, msg.wParam)
        return super().nativeEvent(event_type, message)

    def process_notification(self, message, value):
        # WM_WTSSESSION_CHANGE(0x02B1)：7=本会话被锁定、8=解锁；1=连接、3=远程连接
        #   （可用），2=控制台连接、4=会话注销（不可用）。
        if message == 0x02B1:
            if value in (7, 8):
                self._locked = value == 7
            elif value in (1, 2, 3, 4):
                self._connected = value in (1, 3)
        # WM_POWERBROADCAST(0x0218)：4=进入挂起，7/18=从挂起恢复。
        elif message == 0x0218:
            if value == 4:
                self._suspended = True
            elif value in (7, 18):
                self._suspended = False
        available = self._connected and not self._locked and not self._suspended
        if available != self._available:
            # 只在真实变化时发信号并留痕（规则 14：不记录未变状态）。热键侧据此
            # 丢弃旧输入证据并进入唤醒宽限，是排查“锁屏后热键不恢复”的关键锚点。
            self._available = available
            get_logger().info(
                "会话可用性变更: available=%s locked=%s connected=%s suspended=%s",
                available, self._locked, self._connected, self._suspended,
                extra={"screensnap_metadata_only": True})
            self.available_changed.emit(available)

    def closeEvent(self, event):
        if self.registered:
            try:
                function = ctypes.windll.wtsapi32.WTSUnRegisterSessionNotification
                function.argtypes = (ctypes.c_void_p,)
                function(self._handle)
            except (AttributeError, OSError):
                pass
            self.registered = False
        super().closeEvent(event)
