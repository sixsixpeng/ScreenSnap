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

    def __init__(self, image=None, count=1, title="截图完成", detail="", target_path=None, settings=None,
                 backend="win11toast", close_after=4):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint |
                         Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setObjectName("captureNotification")
        self.setAutoFillBackground(True)
        # 自动关闭时长（秒）；0 表示不自动关闭，需手动点击关闭。
        self.close_after = close_after
        self.target_path = None
        self.backend = backend
        # 通知缩略图/Toast 临时图的落盘位置由 settings 里的保存目录决定（2026-10-11）。
        self.settings = settings or {}
        self._pressed = False
        # image=None 表示纯文字提示（2026-10-16）：旧版应用通知下，托盘气泡会被 Windows
        # 画成系统 toast，与应用内预览的外观不一致；文字提示改走同一个窗口，只是没有缩略图。
        self._has_image = image is not None
        # 2026-10-11（用户反馈：所有带图通知都挂着一个固定的"· 1 张"✗）：目前保存/贴图都是一张张来的，
        # 恒为 1 ⇒ 这个后缀纯属噪音。改为**只有多于一张时才显示**（未来真出现多张也不会丢信息 ✓）。
        count_suffix = f" · {count} 张" if isinstance(count, int) and count > 1 else ""
        self.toast_title = f"{title}{count_suffix}"
        self.toast_body = detail or ("图片预览" if self._has_image else "")
        self._toast_image = None
        if self._has_image:
            self._toast_image = image.copy() if isinstance(image, QImage) else to_qimage(image)
        self.native_failed.connect(self._show_local_preview)
        self.native_activated.connect(self._handle_native_activated)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 12)
        layout.setSpacing(8)
        heading = QLabel(f"{title}{count_suffix}", self)
        heading.setAttribute(Qt.WA_TransparentForMouseEvents)
        layout.addWidget(heading)
        self.preview = None
        self.labels = [heading]
        if self._has_image:
            self.preview = QLabel(self)
            self.preview.setAttribute(Qt.WA_TransparentForMouseEvents)
            # 缩略图比标题/路径行窄时（竖长截图、小图）标签会被布局拉到整窗宽：不设对齐的话
            # pixmap 贴在左边、右边留一大块空白，看上去像渲染坏了（2026-10-16 通知扩展检查发现）。
            self.preview.setAlignment(Qt.AlignCenter)
            if isinstance(image, QImage):
                pixmap = QPixmap.fromImage(image).scaled(240, 140, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            else:
                thumbnail = image.copy()
                thumbnail.thumbnail((240, 140))
                pixmap = QPixmap.fromImage(to_qimage(thumbnail))
            self.preview.setPixmap(pixmap)
            layout.addWidget(self.preview)
            self.labels.append(self.preview)
        if detail:
            if self._has_image:
                path_label = QLabel(self.fontMetrics().elidedText(detail, Qt.ElideMiddle, 235), self)
                path_label.setMaximumWidth(240)
                path_label.setToolTip(detail)
            else:
                # 纯文字提示就是这条消息本身：按窗口宽度换行，不省略（文案已按精简要求收短）。
                path_label = QLabel(detail, self)
                path_label.setWordWrap(True)
                path_label.setMaximumWidth(260)
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
        # 纯文字提示已经把位置写进消息本身，不再追加一行路径（否则同一信息出现两次）。
        if self.target_path and self._has_image:
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
        """按设置发送 Win11 图片 Toast；旧版模式或发送失败时显示本地预览。

        纯文字提示（无缩略图）没有可发给 Toast 的图片，只走本地预览 ——
        Win11 原生通知下的文字提示由 `NotificationMixin.notify()` 直接发送。
        """
        if self.backend != "win11toast" or self._toast_image is None:
            self._show_local_preview()
            return
        from ui.native_toast import (cache_toast_image, native_toast_duration,
                         show_native_toast)

        try:
            # settings 用于把 Toast 临时图放进 <保存目录>/cache（2026-10-11）。
            image_path = cache_toast_image(self._toast_image, self.settings)
        except (OSError, ValueError):
            image_path = None
        if image_path and show_native_toast(
                self.toast_title, self.toast_body, image_path,
            self._native_activated, self.native_failed.emit,
            duration=native_toast_duration(self.close_after)):
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
        if self.close_after and self.close_after > 0:
            QTimer.singleShot(self.close_after * 1000, self.close)

    def mousePressEvent(self, event):
        self._pressed = event.button() == Qt.LeftButton
        event.accept()

    def mouseReleaseEvent(self, event):
        if self._pressed and event.button() == Qt.LeftButton and self.rect().contains(event.position().toPoint()):
            if self.target_path:
                self.file_activated.emit(self.target_path)
        self._pressed = False
        event.accept()