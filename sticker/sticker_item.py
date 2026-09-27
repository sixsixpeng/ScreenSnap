"""独立无边框贴图窗口。"""

from PySide6.QtCore import Qt, QPoint
from PySide6.QtGui import QGuiApplication, QPixmap, QPainter
from PySide6.QtWidgets import QWidget

from sticker.sticker_menu import show_menu


class StickerItem(QWidget):
    """独立悬浮贴图，单独维护尺寸、位置与输入状态。"""

    def __init__(self, image, source=None, settings=None):
        super().__init__()
        self.image = image
        self.source = str(source) if source else None
        self.settings = settings or {}
        self.pixmap = QPixmap.fromImage(image)
        self.locked = False
        self.click_through = False
        self.always_on_top = True
        self.scale_factor = 1.0
        self.drag_origin = None
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.resize(self.pixmap.size())
        self.setFocusPolicy(Qt.StrongFocus)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        painter.drawPixmap(self.rect(), self.pixmap)

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
        self.resize(self.pixmap.size() * self.scale_factor)

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
        self.resize(self.pixmap.size())

    def replace_image(self, image, source):
        """历史图片轮换时保留贴图窗口的当前位置和缩放倍率。"""
        self.image = image
        self.source = str(source)
        self.pixmap = QPixmap.fromImage(image)
        self.resize(self.pixmap.size() * self.scale_factor)
        self.update()

    def copy_image(self):
        """复制原始图像而非带窗口缩放效果的预览。"""
        QGuiApplication.clipboard().setImage(self.image)

    def toggle_top(self):
        """切换置顶标志后重新显示窗口，使窗口管理器应用新状态。"""
        self.always_on_top = not self.always_on_top
        self.setWindowFlag(Qt.WindowStaysOnTopHint, self.always_on_top)
        self.show()

    def state(self):
        """返回可写入 JSON 的窗口状态，源路径必须为字符串。"""
        return {"source": self.source, "x": self.x(), "y": self.y(),
                "scale": self.scale_factor, "opacity": self.windowOpacity(),
                "locked": self.locked, "top": self.always_on_top,
                "click_through": self.click_through}