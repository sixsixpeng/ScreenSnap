"""设置页标注参数的实时预览画布。"""

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (QColor, QLinearGradient, QImage, QPainter, QPainterPath, QPen,
                           QPixmap)
from PySide6.QtWidgets import (QGraphicsPathItem, QGraphicsPixmapItem, QGraphicsRectItem,
                               QGraphicsScene, QSizePolicy, QWidget)

from config.config_manager import DEFAULTS
from core.screen_capture import qimage_to_pillow, to_qimage
from editor.annotation_canvas import mosaic_image
from editor.annotation_items import shape, text_item

# 示例文字同时包含中英文与数字，便于比较不同字体的观感。
SAMPLE_TEXT = "ScreenSnap 预览\nAaBbCc 0123"

ALIGNMENTS = {"left": Qt.AlignLeft, "center": Qt.AlignHCenter, "right": Qt.AlignRight}

# 示意底图只生成一次，避免每次重绘都重新画背景。
_background = None


def sample_image():
    """生成模拟截图内容的底图，供所有预览复用。"""
    global _background
    if _background is None:
        image = QImage(320, 160, QImage.Format_RGB32)
        painter = QPainter(image)
        gradient = QLinearGradient(0, 0, 320, 160)
        gradient.setColorAt(0, QColor("#f5f9fb"))
        gradient.setColorAt(1, QColor("#d3e0e8"))
        painter.fillRect(image.rect(), gradient)
        for index, color in enumerate(("#00ad91", "#ff7043", "#5c6bc0", "#ffd54f")):
            painter.fillRect(14 + index * 76, 16, 58, 38, QColor(color))
        painter.setPen(QPen(QColor("#93a8b4"), 2))
        for row in range(74, 152, 14):
            painter.drawLine(14, row, 306 - (row % 46), row)
        painter.end()
        _background = image
    return _background


class AnnotationPreview(QWidget):
    """按当前配置即时绘制标注效果，避免只能反复截图试参数。"""

    def __init__(self, config, kind, height=96):
        super().__init__()
        self.config = config
        self.kind = kind
        self.scene = None
        self.setFixedHeight(height)
        self.setMinimumWidth(260)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def refresh(self):
        """配置变化后丢弃缓存场景，下一次绘制时按新参数重建。"""
        self.scene = None
        self.update()

    def paintEvent(self, event):
        """用缓存的场景绘制，避免每次重绘都重新计算马赛克。"""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        area = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        if self.scene is None:
            self.scene = self.build(area)
        self.scene.render(painter, area)
        painter.setPen(QPen(QColor("#c3ced6"), 1))
        painter.drawRect(area)
        painter.end()

    def build(self, area):
        """复用真实标注工厂生成预览，保证所见即所得。"""
        scene = QGraphicsScene()
        scene.setSceneRect(area)
        width, height = area.width(), area.height()
        background = sample_image().scaled(int(width), int(height),
                                           Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        scene.addItem(QGraphicsPixmapItem(QPixmap.fromImage(background)))
        # 编辑器工具栏传来的可能只是部分设置，用默认值补齐后再绘制。
        raw = self.config.data if hasattr(self.config, "data") else self.config
        settings = dict(DEFAULTS, **dict(raw or {}))
        color = settings["pen_color"]
        if self.kind == "text":
            scene.addItem(text_item(QPointF(10, 8), SAMPLE_TEXT, settings,
                                    ALIGNMENTS.get(settings.get("text_alignment"), Qt.AlignLeft)))
        elif self.kind == "arrow":
            scene.addItem(shape("arrow", QPointF(width * 0.12, height * 0.72),
                                QPointF(width * 0.88, height * 0.28),
                                color, settings["arrow_width"], settings["arrow_style"]))
        elif self.kind == "shapes":
            scene.addItem(shape("rect", QPointF(width * 0.06, height * 0.16),
                                QPointF(width * 0.34, height * 0.62),
                                color, settings["rect_width"], settings["rect_style"]))
            scene.addItem(shape("ellipse", QPointF(width * 0.40, height * 0.16),
                                QPointF(width * 0.62, height * 0.62),
                                color, settings["ellipse_width"], settings["ellipse_style"]))
            path = QPainterPath(QPointF(width * 0.70, height * 0.62))
            path.quadTo(QPointF(width * 0.82, height * 0.18), QPointF(width * 0.94, height * 0.60))
            stroke = QGraphicsPathItem(path)
            stroke.setPen(QPen(QColor(color), settings["pen_width"], Qt.SolidLine,
                               Qt.RoundCap, Qt.RoundJoin))
            scene.addItem(stroke)
        elif self.kind == "marker":
            # 与画布 stroke_pen 保持一致：不透明度只作用于荧光笔，宽度额外放大。
            marker = QColor(color)
            marker.setAlpha(round(settings.get("marker_opacity", 38) * 255 / 100))
            path = QPainterPath(QPointF(width * 0.12, height * 0.66))
            path.quadTo(QPointF(width * 0.5, height * 0.26), QPointF(width * 0.88, height * 0.66))
            stroke = QGraphicsPathItem(path)
            stroke.setPen(QPen(marker, max(12, settings["marker_width"] * 5), Qt.SolidLine,
                               Qt.RoundCap, Qt.RoundJoin))
            scene.addItem(stroke)
        elif self.kind == "mosaic":
            half = max(1, background.width() // 2)
            source = qimage_to_pillow(background).crop((half, 0, background.width(),
                                                        background.height()))
            covered = to_qimage(mosaic_image(source, settings["mosaic_mode"],
                                             settings["mosaic_size"]))
            item = QGraphicsPixmapItem(QPixmap.fromImage(covered.scaled(source.width, source.height)))
            item.setPos(half, 0)
            scene.addItem(item)
            divider = QGraphicsRectItem(QRectF(half - 0.5, 0, 1, height))
            divider.setPen(QPen(QColor("#ffffff"), 1))
            scene.addItem(divider)
        elif self.kind == "crop":
            frame = QGraphicsRectItem(QRectF(width * 0.18, height * 0.18,
                                             width * 0.64, height * 0.64))
            frame.setPen(QPen(QColor(settings["crop_color"]), settings["crop_width"], Qt.SolidLine))
            fill = QColor(settings["crop_color"])
            fill.setAlpha(50)
            frame.setBrush(fill)
            scene.addItem(frame)
        return scene
