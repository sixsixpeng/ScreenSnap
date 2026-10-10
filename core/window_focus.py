"""把窗口真正切到前台的兜底逻辑（Windows）。

Qt 的 activateWindow() 在 Windows 上最终调用 SetForegroundWindow。若本进程从未
成为过前台进程（托盘常驻程序被全局热键唤起时很常见），系统会静默拒绝这一步：
遮罩仍然置顶显示，键盘事件却继续发给原来的前台窗口，表现为“按 Esc 没有反应，
先点一下鼠标才恢复正常”，且只在启动后的第一次截图出现。

这里在普通激活失败后，把本线程的输入队列临时绑到前台线程再激活一次，成功率
更高；离屏自动化环境不做前台抢夺，避免测试干扰用户当前窗口。
"""

import ctypes
import logging
import os

import shiboken6
from PySide6.QtGui import QGuiApplication

# Windows 专用的前台抢夺兜底；离屏/自动化测试会把它置为 False，避免干扰用户当前窗口。
FOREGROUND_FALLBACK = os.name == "nt"


def offscreen():
    """离屏平台（自动化测试）不做前台抢夺。"""
    app = QGuiApplication.instance()
    return app is not None and app.platformName() == "offscreen"


def foreground_handle():
    """当前前台窗口句柄；非 Windows 或取不到时返回 0。"""
    if os.name != "nt":
        return 0
    try:
        return int(ctypes.windll.user32.GetForegroundWindow() or 0)
    except (AttributeError, OSError, ValueError):
        return 0


def widget_handle(widget):
    """窗口句柄；窗口已销毁或平台不支持时返回 0。

    必须先判活再取句柄（2026-10-11 崩溃修复的同一课）：对已析构的 C++ 对象，winId() 在 C++
    层就会崩，仅靠下面的 except 兜不住 —— 同一天 window_snap.py 就是这么崩过两次的。
    """
    try:
        if not shiboken6.isValid(widget):
            return 0
        return int(widget.winId())
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return 0


def activate_window(widget):
    """尽力把 widget 切到前台并让它拿到键盘输入，返回是否真的成为前台窗口。"""
    handle = widget_handle(widget)
    if not handle:
        return False
    try:
        widget.raise_()
        widget.activateWindow()
    except RuntimeError:
        return False
    if os.name != "nt":
        return False
    if foreground_handle() == handle:
        return True
    if offscreen() or not FOREGROUND_FALLBACK:
        return False
    return activate_with_foreground_thread(handle)


def activate_with_foreground_thread(handle):
    """绑到前台线程的输入队列后再激活，绕开普通 SetForegroundWindow 的限制。"""
    logger = logging.getLogger("screensnap")
    try:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        foreground = user32.GetForegroundWindow()
        if not foreground:
            user32.SetForegroundWindow(handle)
            return foreground_handle() == handle
        foreground_thread = user32.GetWindowThreadProcessId(foreground, None)
        current_thread = kernel32.GetCurrentThreadId()
        if not foreground_thread or foreground_thread == current_thread:
            user32.SetForegroundWindow(handle)
            return foreground_handle() == handle
        if not user32.AttachThreadInput(foreground_thread, current_thread, True):
            return False
        try:
            user32.SetForegroundWindow(handle)
            user32.BringWindowToTop(handle)
            user32.SetFocus(handle)
        finally:
            user32.AttachThreadInput(foreground_thread, current_thread, False)
    except (AttributeError, OSError, RuntimeError) as error:
        logger.debug("前台激活兜底失败: %s", error)
        return False
    return foreground_handle() == handle
