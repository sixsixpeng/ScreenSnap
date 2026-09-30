"""截图完成后显示带真实选区缩略图的轻量提示。"""

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QCursor, QGuiApplication, QPixmap, QImage
from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel

from core.screen_capture import to_qimage


class CaptureNotification(QWidget):
    """以非激活窗口预览选区，不抢走编辑器的键盘焦点。"""

    file_activated = Signal(str)
    native_failed = Signal(object)
    native_activated = Signal()

    def __init__(self, image, count=1, title="截图完成", detail="", target_path=None,
                 backend="win11toast"):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint |
                         Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setObjectName("captureNotification")
        self.setAutoFillBackground(True)
        self.target_path = None
        self.backend = backend
        self._pressed = False
        self.toast_title = f"{title} · {count} 张"
        self.toast_body = detail or "图片预览"
        self._toast_image = image.copy() if isinstance(image, QImage) else to_qimage(image)
        self.native_failed.connect(self._show_local_preview)
        self.native_activated.connect(self._handle_native_activated)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 12)
        layout.setSpacing(8)
        heading = QLabel(f"{title} · {count} 张", self)
        heading.setAttribute(Qt.WA_TransparentForMouseEvents)
        layout.addWidget(heading)
        self.preview = QLabel(self)
        self.preview.setAttribute(Qt.WA_TransparentForMouseEvents)
        if isinstance(image, QImage):
            pixmap = QPixmap.fromImage(image).scaled(240, 140, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        else:
            thumbnail = image.copy()
            thumbnail.thumbnail((240, 140))
            pixmap = QPixmap.fromImage(to_qimage(thumbnail))
        self.preview.setPixmap(pixmap)
        layout.addWidget(self.preview)
        self.labels = [heading, self.preview]
        if detail:
            path_label = QLabel(self.fontMetrics().elidedText(detail, Qt.ElideMiddle, 235), self)
            path_label.setMaximumWidth(240)
            path_label.setToolTip(detail)
            path_label.setAttribute(Qt.WA_TransparentForMouseEvents)
            layout.addWidget(path_label)
            self.labels.append(path_label)
        self.adjustSize()
        self.set_target_path(target_path)

    def set_target_path(self, path):
        """更新保存完成后才可用的文件目标，并提示通知可以点击。"""
        self.target_path = str(path) if path else None
        self.setCursor(Qt.PointingHandCursor if self.target_path else Qt.ArrowCursor)
        self.setToolTip("点击在文件夹中显示此文件" if self.target_path else "")
        if self.target_path:
            detail = next((label for label in self.labels[2:]), None)
            if detail is None:
                detail = QLabel(self.fontMetrics().elidedText(self.target_path, Qt.ElideMiddle, 235), self)
                detail.setMaximumWidth(240)
                detail.setToolTip(self.target_path)
                detail.setAttribute(Qt.WA_TransparentForMouseEvents)
                self.layout().addWidget(detail)
                self.labels.append(detail)
                self.adjustSize()
            else:
                detail.setText(self.fontMetrics().elidedText(self.target_path, Qt.ElideMiddle, 235))
                detail.setToolTip(self.target_path)

    def show_preview(self):
        """按设置发送 Win11 图片 Toast；旧版模式或发送失败时显示本地预览。"""
        if self.backend != "win11toast":
            self._show_local_preview()
            return
        from ui.native_toast import cache_toast_image, show_native_toast

        try:
            image_path = cache_toast_image(self._toast_image)
        except (OSError, ValueError):
            image_path = None
        if image_path and show_native_toast(
                self.toast_title, self.toast_body, image_path,
                self._native_activated, self.native_failed.emit):
            return
        self._show_local_preview()

    def _native_activated(self, args=None):
        self.native_activated.emit()

    def _handle_native_activated(self):
        if self.target_path:
            self.file_activated.emit(self.target_path)

    def _show_local_preview(self, error=None):
        """贴靠可用桌面右下角，几秒后自动关闭。"""
        screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        if screen is not None:
            area = screen.availableGeometry()
            self.move(area.right() - self.width() - 16, area.bottom() - self.height() - 16)
        self.show()
        QTimer.singleShot(4000, self.close)

    def mousePressEvent(self, event):
        self._pressed = event.button() == Qt.LeftButton
        event.accept()

    def mouseReleaseEvent(self, event):
        if self._pressed and event.button() == Qt.LeftButton and self.rect().contains(event.position().toPoint()):
            if self.target_path:
                self.file_activated.emit(self.target_path)
        self._pressed = False
        event.accept()