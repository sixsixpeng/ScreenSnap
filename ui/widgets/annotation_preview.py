"""设置页标注参数的实时预览画布。"""

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (QColor, QFont, QLinearGradient, QImage, QPainter, QPainterPath,
                           QPen, QPixmap)
from PySide6.QtWidgets import (QGraphicsPathItem, QGraphicsPixmapItem, QGraphicsRectItem,
                               QGraphicsEllipseItem, QGraphicsScene, QSizePolicy, QWidget)

from config.config_manager import DEFAULTS
from core.constants import CHECKER_TILE_SIZE
from core.screen_capture import qimage_to_pillow, to_qimage
from editor.annotation_canvas import mosaic_image
from editor.annotation_items import shape, text_item, AnnotationSequenceItem
from editor.image_effects import apply_output_effects

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
        painter.setPen(QPen(QColor("#ffffff"), 1))
        for x in range(18, 306, 5):
            painter.drawLine(x, 20, x + 18, 50)
        painter.setPen(QPen(QColor("#233746"), 1))
        for x in range(16, 306, 7):
            painter.drawLine(x, 82, x + 3, 154)
        for y in range(84, 156, 5):
            painter.drawLine(16, y, 304, y + 2)
        painter.setPen(QPen(QColor("#93a8b4"), 2))
        for row in range(74, 152, 14):
            painter.drawLine(14, row, 306 - (row % 46), row)
        painter.end()
        _background = image
    return _background


class AnnotationPreview(QWidget):
    """按当前配置即时绘制标注效果，避免只能反复截图试参数。"""

    def __init__(self, config, kind, height=96, sample_text=None):
        super().__init__()
        self.config = config
        self.kind = kind
        self.scene = None
        # 文字预览可改用调用方提供的示例文字（如对话框里正在输入的内容）。
        self.sample_text = sample_text
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
        background_item = QGraphicsPixmapItem(QPixmap.fromImage(background))
        scene.addItem(background_item)
        # 编辑器工具栏传来的可能只是部分设置，用默认值补齐后再绘制。
        raw = self.config.data if hasattr(self.config, "data") else self.config
        settings = dict(DEFAULTS, **dict(raw or {}))
        color = settings["pen_color"]
        if self.kind == "output":
            scene.removeItem(background_item)
            rendered = to_qimage(apply_output_effects(
                qimage_to_pillow(background), settings,
                bool(settings.get("editor_image_round_corners", True)),
                settings.get("editor_image_corner_radius", 16)))
            preview = QImage(max(1, int(width)), max(1, int(height)), QImage.Format_ARGB32)
            preview.fill(QColor("#f1f3f4"))
            checker = QPainter(preview)
            tile = CHECKER_TILE_SIZE
            for row, top in enumerate(range(0, preview.height(), tile)):
                for column, left in enumerate(range(0, preview.width(), tile)):
                    if (row + column) % 2:
                        checker.fillRect(left, top, tile, tile, QColor("#d8dde0"))
            scale = min((preview.width() - 16) / max(1, background.width()),
                        (preview.height() - 16) / max(1, background.height()))
            scaled = rendered.scaled(
                max(1, round(rendered.width() * scale)),
                max(1, round(rendered.height() * scale)),
                Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
            checker.drawImage((preview.width() - scaled.width()) // 2,
                              (preview.height() - scaled.height()) // 2, scaled)
            checker.end()
            scene.addItem(QGraphicsPixmapItem(QPixmap.fromImage(preview)))
        elif self.kind == "text":
            sample = self.sample_text if self.sample_text else SAMPLE_TEXT
            item = text_item(QPointF(0, 0), sample, settings,
                             ALIGNMENTS.get(settings.get("text_alignment"), Qt.AlignLeft))
            item_rect = item.boundingRect()
            # 文字实际宽度常超出预览区（大字号尤甚），先等比缩小到可视区域内再居中，
            # 避免左侧被裁掉看不到；能完整放下时不放大，保持原始字号观感。
            scale = min(1.0, (width - 16) / max(item_rect.width(), 1),
                        (height - 12) / max(item_rect.height(), 1))
            item.setScale(scale)
            scaled_width = item_rect.width() * scale
            scaled_height = item_rect.height() * scale
            item.setPos((width - scaled_width) / 2 - item_rect.x() * scale,
                        (height - scaled_height) / 2 - item_rect.y() * scale)
            scene.addItem(item)
        elif self.kind == "arrow":
            scene.addItem(shape("arrow", QPointF(width * 0.12, height * 0.72),
                                QPointF(width * 0.88, height * 0.28),
                                settings.get("arrow_color", color), settings["arrow_width"], settings["arrow_style"]))
        elif self.kind == "rect":
            scene.addItem(shape("rect", QPointF(width * 0.29, height * 0.21),
                                QPointF(width * 0.71, height * 0.79),
                                settings.get("rect_color", color), settings["rect_width"], settings["rect_style"],
                                corner_radius=(settings.get("rect_corner_radius", 12)
                                               if settings.get("rect_corner_enabled", True) else 0),
                                fill_enabled=settings.get("rect_fill_enabled", False),
                                fill_opacity=settings.get("rect_fill_opacity", 35),
                                fill_color=settings.get("rect_fill_color")))
        elif self.kind == "ellipse":
            scene.addItem(shape("ellipse", QPointF(width * 0.40, height * 0.16),
                                QPointF(width * 0.62, height * 0.62),
                                settings.get("ellipse_color", color), settings["ellipse_width"], settings["ellipse_style"],
                                fill_enabled=settings.get("ellipse_fill_enabled", False),
                                fill_opacity=settings.get("ellipse_fill_opacity", 35),
                                fill_color=settings.get("ellipse_fill_color")))
        elif self.kind == "pen":
            path = QPainterPath(QPointF(width * 0.25, height * 0.60))
            path.quadTo(QPointF(width * 0.50, height * 0.20), QPointF(width * 0.75, height * 0.60))
            stroke = QGraphicsPathItem(path)
            stroke.setPen(QPen(QColor(color), settings["pen_width"], Qt.SolidLine,
                               Qt.RoundCap, Qt.RoundJoin))
            stroke_bounds = stroke.boundingRect()
            stroke.setPos(width / 2 - stroke_bounds.center().x(),
                          height / 2 - stroke_bounds.center().y())
            scene.addItem(stroke)
        elif self.kind == "eraser":
            preview = background.copy().convertToFormat(QImage.Format_ARGB32)
            overlay_painter = QPainter(preview)
            overlay_painter.setRenderHint(QPainter.Antialiasing)
            path = QPainterPath(QPointF(width * 0.18, height * 0.58))
            path.quadTo(QPointF(width * 0.50, height * 0.34),
                        QPointF(width * 0.82, height * 0.58))
            overlay_painter.setPen(QPen(QColor(color), 7, Qt.SolidLine,
                                        Qt.RoundCap, Qt.RoundJoin))
            overlay_painter.drawPath(path)
            center = QPointF(width * 0.50, height * 0.46)
            diameter = min(settings["eraser_width"], height * 0.72)
            eraser_rect = QRectF(center.x() - diameter / 2, center.y() - diameter / 2,
                                 diameter, diameter)
            overlay_painter.setCompositionMode(QPainter.CompositionMode_Clear)
            overlay_painter.setPen(Qt.NoPen)
            overlay_painter.setBrush(Qt.white)
            overlay_painter.drawEllipse(eraser_rect)
            overlay_painter.end()
            scene.removeItem(background_item)
            scene.addItem(QGraphicsPixmapItem(QPixmap.fromImage(preview)))
            cursor = QGraphicsEllipseItem(eraser_rect)
            cursor.setPen(QPen(QColor(settings.get("eraser_cursor_color", "#ff8c00")),
                               1.5, Qt.SolidLine))
            cursor.setBrush(Qt.NoBrush)
            scene.addItem(cursor)
        elif self.kind == "marker":
            # 与画布 stroke_pen 保持一致：不透明度只作用于荧光笔，宽度额外放大。
            marker = QColor(settings.get("marker_color", color))
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
            diameter = min(settings.get("mosaic_width", 20), height * 0.72)
            center = QPointF(width * 0.68, height * 0.68)
            ring = QGraphicsEllipseItem(QRectF(center.x() - diameter / 2,
                                                center.y() - diameter / 2,
                                                diameter, diameter))
            ring.setPen(QPen(QColor(settings.get("mosaic_cursor_color", "#00c853")),
                             1.5, Qt.SolidLine))
            ring.setBrush(Qt.NoBrush)
            scene.addItem(ring)
        elif self.kind == "crop":
            frame = QGraphicsRectItem(QRectF(width * 0.18, height * 0.18,
                                             width * 0.64, height * 0.64))
            frame.setPen(QPen(QColor(settings["crop_color"]), settings["crop_width"], Qt.SolidLine))
            fill = QColor(settings["crop_color"])
            fill.setAlpha(50)
            frame.setBrush(fill)
            scene.addItem(frame)
        elif self.kind == "sequence":
            for index, offset in enumerate(((width * 0.30, height * 0.40),
                                            (width * 0.52, height * 0.30),
                                            (width * 0.70, height * 0.56))):
                item = AnnotationSequenceItem(
                    index + 1,
                    settings.get("sequence_fill_color", "#ff0000"),
                    settings.get("sequence_text_color", "#ffffff"),
                    settings.get("sequence_font_size", 18),
                    settings.get("sequence_shape", "circle"),
                    settings.get("font", ""))
                item.setPos(offset[0], offset[1])
                scene.addItem(item)
        elif self.kind == "picker":
            # 取色工具预览：放大镜（可叠加像素网格）+ 中心十字线 + 取到的色值与十六进制色号。
            preview_image = QImage(max(1, int(width)), max(1, int(height)), QImage.Format_ARGB32)
            preview_image.fill(QColor("#f1f3f4"))
            painter = QPainter(preview_image)
            painter.setRenderHint(QPainter.Antialiasing)
            scaled = background.scaled(int(width), int(height),
                                      Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
            painter.drawImage(0, 0, scaled)
            center_x, center_y = width / 2, height / 2
            radius = min(width, height) * 0.34
            lens = QRectF(center_x - radius, center_y - radius, radius * 2, radius * 2)
            lens_path = QPainterPath()
            lens_path.addEllipse(lens)
            painter.save()
            painter.setClipPath(lens_path)
            inner = background.scaled(int(radius * 2), int(radius * 2),
                                     Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
            painter.drawImage(lens.topLeft(), inner)
            if settings.get("magnifier_grid", True):
                grid_color = QColor(settings.get("magnifier_grid_color", "#cccccc"))
                painter.setPen(QPen(grid_color, 1, Qt.DotLine))
                cells = 8
                for index in range(1, cells):
                    offset = radius * 2 * index / cells
                    painter.drawLine(lens.left() + offset, lens.top(),
                                     lens.left() + offset, lens.bottom())
                    painter.drawLine(lens.left(), lens.top() + offset,
                                     lens.right(), lens.top() + offset)
            painter.restore()
            painter.setPen(QPen(QColor("#273b44"), 2))
            painter.setBrush(Qt.NoBrush)
            painter.drawEllipse(lens)
            painter.setPen(QPen(QColor("#e34b5f"), 2))
            painter.drawLine(center_x - radius * 0.5, center_y,
                             center_x + radius * 0.5, center_y)
            painter.drawLine(center_x, center_y - radius * 0.5,
                             center_x, center_y + radius * 0.5)
            sample = scaled.pixelColor(int(center_x), int(center_y))
            swatch = QRectF(width - 92, height - 28, 20, 20)
            painter.setPen(QPen(QColor("#333333"), 1))
            painter.setBrush(QColor(sample))
            painter.drawRect(swatch)
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QColor("#1f2a30"))
            label_font = QFont()
            label_font.setPixelSize(11)
            painter.setFont(label_font)
            painter.drawText(swatch.right() + 6, swatch.top() + 15,
                             "#%02x%02x%02x" % (sample.red(), sample.green(), sample.blue()))
            painter.end()
            scene.addItem(QGraphicsPixmapItem(QPixmap.fromImage(preview_image)))
        return scene
