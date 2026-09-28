"""为每条日志补充鼠标位置、前台窗口等现场信息。"""

import logging
import os
from contextlib import contextmanager
from contextvars import ContextVar

# 窗口标题只保留前若干字符，避免单行日志过长。
TITLE_LIMIT = 18

_scope = ContextVar("screensnap_scope", default="")
# 收集现场信息时可能再次触发日志，用它阻断递归。
_busy = ContextVar("screensnap_busy", default=False)
_current = ContextVar("screensnap_context", default="")


def warm():
    """先刷新屏幕映射缓存，避免收集现场信息时触发嵌套日志。"""
    try:
        from core.screen_mapping import screen_mappings

        screen_mappings()
    except Exception:
        pass


@contextmanager
def log_scope(text):
    """临时给当前流程的日志加一个标记，例如正在操作哪张贴图。"""
    token = _scope.set(text)
    try:
        yield
    finally:
        _scope.reset(token)


def mouse_text():
    """当前鼠标的逻辑坐标与所在屏幕；Qt 不可用时返回 n/a。"""
    try:
        from PySide6.QtGui import QCursor, QGuiApplication

        application = QGuiApplication.instance()
        if application is None:
            return "鼠标(n/a)"
        point = QCursor.pos()
        name = (application.screenAt(point) or application.primaryScreen())
        name = name.name() if name else ""
        return f"鼠标({point.x()},{point.y()})" + (f"@{name}" if name else "")
    except Exception:
        # 退出阶段 Qt 对象可能已销毁，日志不应因此中断。
        return "鼠标(n/a)"


def foreground_text():
    """当前前台窗口的标题与逻辑矩形；非 Windows 或取不到时返回空。"""
    if os.name != "nt":
        return ""
    try:
        import ctypes
        from ctypes import wintypes

        from core.screen_mapping import native_rect, physical_rect_to_logical
        from core.window_snap import process_of, window_text

        user32 = ctypes.windll.user32
        handle = user32.GetForegroundWindow()
        if not handle:
            return "前台(无)"
        bounds = wintypes.RECT()
        size = ""
        if user32.GetWindowRect(handle, ctypes.byref(bounds)):
            rect = physical_rect_to_logical(native_rect(bounds))
            size = f"({rect.left()},{rect.top()},{rect.width()}x{rect.height()})"
        title = window_text(user32, handle)[:TITLE_LIMIT] or "无标题"
        own = "本进程" if process_of(user32, handle) == os.getpid() else "外部"
        return f"前台={title}{size}[{own}]"
    except Exception:
        return ""


class ContextFilter(logging.Filter):
    """把鼠标位置、前台窗口与临时标记附加到每条日志上。"""

    def filter(self, record):
        if _busy.get():
            # 收集现场信息时产生的日志沿用外层现场，不再重复收集，避免递归。
            record.context = _current.get()
            return True
        _busy.set(True)
        try:
            warm()
            parts = (mouse_text(), foreground_text(), _scope.get())
            text = " ".join(part for part in parts if part)
            _current.set(text)
            record.context = text
        finally:
            _busy.set(False)
        return True
