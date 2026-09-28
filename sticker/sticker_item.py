"""独立无边框贴图窗口。"""

from PySide6.QtCore import Qt, QPoint, QRect, QSize, Signal
from PySide6.QtGui import QGuiApplication, QPixmap, QPainter, QColor, QPen, QImage
from PySide6.QtWidgets import QWidget

from sticker.sticker_menu import show_menu


class StickerItem(QWidget):
    """独立悬浮贴图，单独维护尺寸、位置与输入状态。"""

    closed = Signal()

    def __init__(self, image, source=None, settings=None):
        super().__init__()
        self.source = str(source) if source else None
        self.image = None if self.source else image
        self.settings = settings or {}
        self.pixmap = QPixmap.fromImage(image)
        self.locked = False
        self.click_through = False
        self.always_on_top = True
        self.border_enabled = self.settings.get("sticker_border_enabled", True)
        self.shadow_enabled = self.settings.get("sticker_shadow_enabled", True)
        self.scale_factor = 1.0
        self.drag_origin = None
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.resize(self.window_size())
        self.setFocusPolicy(Qt.StrongFocus)

    def padding(self):
        border = self.settings.get("sticker_border_width", 2) if self.border_enabled else 0
        shadow = round(self.settings.get("sticker_shadow_strength", 35) / 5) if self.shadow_enabled else 0
        return max(0, border, shadow)

    def image_rect(self):
        pad = self.padding()
        return QRect(pad, pad, max(1, self.width() - pad * 2), max(1, self.height() - pad * 2))

    def window_size(self):
        pad = self.padding()
        image_size = self.pixmap.size() * self.scale_factor
        return QSize(image_size.width() + pad * 2, image_size.height() + pad * 2)

    def apply_style(self):
        self.resize(self.window_size())
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        rect = self.image_rect()
        if self.shadow_enabled and self.settings.get("sticker_shadow_strength", 35):
            color = QColor(self.settings.get("sticker_shadow_color", "#000000"))
            strength = self.settings.get("sticker_shadow_strength", 35)
            color.setAlpha(round(255 * strength / 100))
            offset = max(1, round(strength / 20))
            painter.fillRect(rect.translated(offset, offset), color)
        painter.drawPixmap(rect, self.pixmap)
        if self.border_enabled and self.settings.get("sticker_border_width", 2):
            painter.setPen(QPen(QColor(self.settings.get("sticker_border_color", "#00ad91")),
                                self.settings.get("sticker_border_width", 2)))
            painter.drawRect(rect.adjusted(0, 0, -1, -1))

    def mousePressEvent(self, event):
        # 记录全局鼠标到窗口左上角的偏移，跨显示器拖动仍保持原抓取位置。
        if event.button() == Qt.RightButton:
            show_menu(self, event.globalPosition().toPoint())
        elif event.button() == Qt.LeftButton and not self.locked:
            self.drag_origin = event.globalPosition().toPoint() - self.pos()
            self.setFocus()

    def mouseMoveEvent(self, event):
        """以全局坐标拖动窗口，跨显示器时不重置抓取偏移。"""
        if self.drag_origin is not None:
            self.move(event.globalPosition().toPoint() - self.drag_origin)

    def mouseReleaseEvent(self, event):
        """释放拖动状态，避免下一次鼠标移动继续平移。"""
        self.drag_origin = None

    def wheelEvent(self, event):
        """未锁定时按固定倍率缩放，并限制最小、最大尺寸。"""
        if self.locked:
            return
        factor = 1.1 if event.angleDelta().y() > 0 else 1 / 1.1
        self.scale_factor = min(10, max(0.1, self.scale_factor * factor))
        self.resize(self.window_size())

    def keyPressEvent(self, event):
        """Esc 仅关闭当前贴图，方向键在未锁定时微移窗口。"""
        if event.key() == Qt.Key_Escape:
            # 每个贴图独立关闭，其他贴图窗口保持原状。
            self.close()
            return
        if event.key() in (Qt.Key_Left, Qt.Key_Right, Qt.Key_Up, Qt.Key_Down,
                           Qt.Key_A, Qt.Key_D, Qt.Key_W, Qt.Key_S) and not self.locked:
            self.move(self.pos() + QPoint((event.key() in (Qt.Key_Right, Qt.Key_D)) -
                                           (event.key() in (Qt.Key_Left, Qt.Key_A)),
                                           (event.key() in (Qt.Key_Down, Qt.Key_S)) -
                                           (event.key() in (Qt.Key_Up, Qt.Key_W))))

    def toggle_lock(self):
        """锁定后禁止鼠标拖动、滚轮缩放和方向键微调。"""
        self.locked = not self.locked

    def toggle_click_through(self):
        """穿透后无法右键自身，需通过恢复交互的全局热键重新启用输入。"""
        self.click_through = not self.click_through
        self.setWindowFlag(Qt.WindowTransparentForInput, self.click_through)
        self.show()

    def reset_size(self):
        """恢复原始像素大小，不更换贴图内容。"""
        self.scale_factor = 1.0
        self.resize(self.window_size())

    def replace_image(self, image, source):
        """历史图片轮换时保留贴图窗口的当前位置和缩放倍率。"""
        self.source = str(source)
        self.image = None
        self.pixmap = QPixmap.fromImage(image)
        self.resize(self.window_size())
        self.update()

    def closeEvent(self, event):
        self.closed.emit()
        super().closeEvent(event)

    def copy_image(self):
        """复制原始图像而非带窗口缩放效果的预览。"""
        image = QImage(self.source) if self.source else self.image
        if image is not None and not image.isNull():
            QGuiApplication.clipboard().setImage(image)

    def toggle_top(self):
        """切换置顶标志后重新显示窗口，使窗口管理器应用新状态。"""
        self.always_on_top = not self.always_on_top
        self.setWindowFlag(Qt.WindowStaysOnTopHint, self.always_on_top)
        self.show()

    def toggle_border(self):
        self.border_enabled = not self.border_enabled
        self.apply_style()

    def toggle_shadow(self):
        self.shadow_enabled = not self.shadow_enabled
        self.apply_style()

    def state(self):
        """返回可写入 JSON 的窗口状态，源路径必须为字符串。"""
        return {"source": self.source, "x": self.x(), "y": self.y(),
                "scale": self.scale_factor, "opacity": self.windowOpacity(),
                "locked": self.locked, "top": self.always_on_top,
                "click_through": self.click_through, "border": self.border_enabled,
                "shadow": self.shadow_enabled}
