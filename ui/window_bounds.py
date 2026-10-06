import logging

from PySide6.QtCore import QEvent, QObject, QRect, QTimer, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QDialog, QMenu, QWidget


def clamp_geometry(frame, bounds):
    """Return a frame rectangle fully contained in the available screen area."""
    width = min(frame.width(), bounds.width())
    height = min(frame.height(), bounds.height())
    x = min(max(frame.left(), bounds.left()), bounds.right() - width + 1)
    y = min(max(frame.top(), bounds.top()), bounds.bottom() - height + 1)
    return QRect(x, y, width, height)


def anchored_popup_geometry(anchor, popup_size, bounds):
    """Place a popup beside its anchor and keep the full popup on-screen."""
    width = min(popup_size.width(), bounds.width())
    height = min(popup_size.height(), bounds.height())
    x = anchor.left()
    if x + width > bounds.right() + 1:
        x = anchor.right() - width + 1
    y = anchor.bottom() + 1
    if y + height > bounds.bottom() + 1:
        y = anchor.top() - height
    x = min(max(x, bounds.left()), bounds.right() - width + 1)
    y = min(max(y, bounds.top()), bounds.bottom() - height + 1)
    return QRect(x, y, width, height)


class WindowBoundsFilter(QObject):
    """Keep application windows and transient popups inside their screen."""

    def eventFilter(self, watched, event):
        if (event.type() == QEvent.Show and isinstance(watched, QWidget)
                and watched.isWindow() and self._should_constrain(watched)):
            QTimer.singleShot(0, watched, lambda: self._constrain(watched))
        return super().eventFilter(watched, event)

    @staticmethod
    def _should_constrain(window):
        if window.property("screensnap_skip_window_bounds"):
            return False
        window_type = window.windowFlags() & Qt.WindowType_Mask
        return window_type not in (Qt.Tool, Qt.ToolTip, Qt.SplashScreen,
                       Qt.Desktop, Qt.SubWindow)

    def _constrain(self, window):
        if not window.isVisible():
            return
        flags = window.windowState()
        if flags & Qt.WindowFullScreen:
            return

        frame = window.frameGeometry()
        parent = window.parentWidget()
        screen = None
        if parent is not None and parent.window() is not window:
            screen = parent.window().screen()
        if screen is None:
            screen = QGuiApplication.screenAt(frame.center())
        if screen is None:
            screen = window.screen() or QGuiApplication.primaryScreen()
        if screen is None:
            return

        bounds = screen.availableGeometry()
        if isinstance(window, QDialog):
            width_delta = max(0, frame.width() - bounds.width())
            height_delta = max(0, frame.height() - bounds.height())
            if width_delta or height_delta:
                window.resize(max(1, window.width() - width_delta),
                              max(1, window.height() - height_delta))
                frame = window.frameGeometry()

        target = clamp_geometry(frame, bounds)
        if target.topLeft() != frame.topLeft():
            window.move(target.topLeft())
            logging.getLogger("screensnap").debug(
                "窗口移入屏幕可用区域: %s", type(window).__name__)
        if isinstance(window, (QDialog, QMenu)) and (
                frame.width() > bounds.width() or frame.height() > bounds.height()):
            logging.getLogger("screensnap").warning(
                "弹窗大于屏幕可用区域，已尽量调整位置: %s", type(window).__name__)