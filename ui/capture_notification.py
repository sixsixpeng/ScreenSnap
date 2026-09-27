"""截图完成后显示带真实选区缩略图的轻量提示。"""

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QCursor, QGuiApplication, QPixmap, QImage
from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel

from core.screen_capture import to_qimage


class CaptureNotification(QWidget):
    """以非激活窗口预览选区，不抢走编辑器的键盘焦点。"""

    def __init__(self, image, count=1, title="截图完成", detail=""):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint |
                         Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setObjectName("captureNotification")
        self.setStyleSheet("#captureNotification { background: #18343b; border: 1px solid #30d2a2; }"
                           "QLabel { color: #f5fbfa; }")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 12)
        layout.setSpacing(8)
        heading = QLabel(f"{title} · {count} 张", self)
        layout.addWidget(heading)
        self.preview = QLabel(self)
        if isinstance(image, QImage):
            pixmap = QPixmap.fromImage(image).scaled(240, 140, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        else:
            thumbnail = image.copy()
            thumbnail.thumbnail((240, 140))
            pixmap = QPixmap.fromImage(to_qimage(thumbnail))
        self.preview.setPixmap(pixmap)
        layout.addWidget(self.preview)
        if detail:
            path_label = QLabel(self.fontMetrics().elidedText(detail, Qt.ElideMiddle, 235), self)
            path_label.setMaximumWidth(240)
            path_label.setToolTip(detail)
            layout.addWidget(path_label)
        self.adjustSize()

    def show_preview(self):
        """贴靠可用桌面右下角，几秒后自动关闭。"""
        screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        if screen is not None:
            area = screen.availableGeometry()
            self.move(area.right() - self.width() - 16, area.bottom() - self.height() - 16)
        self.show()
        QTimer.singleShot(4000, self.close)