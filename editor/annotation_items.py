"""可选中和移动的 Qt 标注图元工厂。"""

import math

from PySide6.QtCore import Qt, QPointF, QRectF
from PySide6.QtGui import (QColor, QPen, QPainterPath, QFont, QTextCursor,
                           QTextBlockFormat, QPolygonF)
from PySide6.QtWidgets import (QGraphicsItem, QGraphicsRectItem, QGraphicsEllipseItem,
                               QGraphicsPathItem, QGraphicsTextItem, QGraphicsPixmapItem,
                               QStyle, QStyleOptionGraphicsItem)


# 序号标注可选形状：常用几何（圆/方/三角/菱形/五边形/六边形/星形）之外，
# 还提供更具表达力的发散形状（心形/箭头/对话气泡/云/十字/水滴），便于按场景强调。
SEQUENCE_SHAPES = ("circle", "square", "triangle", "diamond", "pentagon", "hexagon",
                   "star", "heart", "arrow", "bubble", "cloud", "plus", "drop")

# 预选组合：一键套用形状 + 填充色 + 文字色；custom 表示用户自由搭配，不覆盖任何单项。
SEQUENCE_PRESETS = {
    "custom": {},
    "red_circle": {"sequence_shape": "circle", "sequence_fill_color": "#ff0000",
                   "sequence_text_color": "#ffffff"},
    "blue_square": {"sequence_shape": "square", "sequence_fill_color": "#168cff",
                    "sequence_text_color": "#ffffff"},
    "green_star": {"sequence_shape": "star", "sequence_fill_color": "#2e9e5b",
                   "sequence_text_color": "#ffffff"},
    "amber_diamond": {"sequence_shape": "diamond", "sequence_fill_color": "#f59f00",
                      "sequence_text_color": "#1f1f1f"},
    "purple_pentagon": {"sequence_shape": "pentagon", "sequence_fill_color": "#8e5bd0",
                        "sequence_text_color": "#ffffff"},
    "teal_hexagon": {"sequence_shape": "hexagon", "sequence_fill_color": "#00ad91",
                     "sequence_text_color": "#ffffff"},
    "red_heart": {"sequence_shape": "heart", "sequence_fill_color": "#e23b4e",
                  "sequence_text_color": "#ffffff"},
    "blue_arrow": {"sequence_shape": "arrow", "sequence_fill_color": "#168cff",
                   "sequence_text_color": "#ffffff"},
}


def sequence_shape_points(shape, radius):
    """返回以原点为中心、外接半径为 radius 的规则多边形顶点。

    圆形返回 None（用椭圆绘制）；心形/箭头/气泡/云/十字/水滴等特殊形状也返回
    None，由 AnnotationSequenceItem 的专属绘制方法处理；未知形状回退为方形。
    """
    if shape == "circle":
        return None
    count = {"square": 4, "diamond": 4, "triangle": 3, "pentagon": 5,
             "hexagon": 6, "star": 10}.get(shape)
    if count is None:
        return None
    points = []
    for index in range(count):
        angle = math.radians(90 + index * (360 / count))
        outer = radius if (count != 10 or index % 2 == 0) else radius * 0.45
        points.append(QPointF(outer * math.cos(angle), -outer * math.sin(angle)))
    return points


def apply_sequence_preset(settings, preset_key):
    """把预选组合写入 settings（custom 不覆盖任何值）。"""
    preset = SEQUENCE_PRESETS.get(preset_key)
    if preset:
        settings.update(preset)
    return settings


class AnnotationPaintMixin:
    """Keep an annotation's original appearance visible while it is selected."""

    def paint(self, painter, option, widget=None):
        visible_option = QStyleOptionGraphicsItem(option)
        visible_option.state &= ~QStyle.State_Selected
        super().paint(painter, visible_option, widget)


class AnnotationRectItem(AnnotationPaintMixin, QGraphicsRectItem):
    pass


class AnnotationEllipseItem(AnnotationPaintMixin, QGraphicsEllipseItem):
    pass


class AnnotationPathItem(AnnotationPaintMixin, QGraphicsPathItem):
    pass


class AnnotationTextItem(AnnotationPaintMixin, QGraphicsTextItem):
    pass


class AnnotationPixmapItem(AnnotationPaintMixin, QGraphicsPixmapItem):
    pass


class RoundedRectItem(AnnotationPaintMixin, QGraphicsRectItem):
    """绘制圆角的矩形图元，仍保留矩形的选中与缩放行为。"""

    def __init__(self, rect, radius):
        super().__init__(rect)
        self.corner_radius = max(0, float(radius))

    def paint(self, painter, option, widget=None):
        if self.corner_radius <= 0:
            super().paint(painter, option, widget)
            return
        painter.save()
        painter.setPen(self.pen())
        painter.setBrush(self.brush())
        rect = self.rect()
        radius = min(self.corner_radius, rect.width() / 2, rect.height() / 2)
        painter.drawRoundedRect(rect, radius, radius)
        painter.restore()


def editable(item):
    """统一开启标注的选中和移动能力，保持各图元交互一致。"""
    item.setFlags(QGraphicsItem.ItemIsSelectable | QGraphicsItem.ItemIsMovable |
                  QGraphicsItem.ItemSendsGeometryChanges)
    return item


def shape(tool, start, end, color, width, arrow_style="filled", corner_radius=0,
          fill_enabled=False, fill_opacity=35):
    """按工具类型建立可选中、可移动的矢量图元。"""
    bounds = QRectF(start, end).normalized()
    dashed = arrow_style in ("dashed", "double_dashed", "dashed_line", "rect_dashed", "dash")
    pen = QPen(QColor(color), width, Qt.DashLine if dashed else Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
    fill = QColor(color)
    fill.setAlpha(round(255 * max(0, min(100, fill_opacity)) / 100))
    if tool == "rect":
        item = RoundedRectItem(bounds, corner_radius) if corner_radius > 0 else AnnotationRectItem(bounds)
        item.setPen(pen)
        item.setBrush(fill if fill_enabled else Qt.NoBrush)
    elif tool == "ellipse":
        item = AnnotationEllipseItem(bounds)
        item.setPen(pen)
        item.setBrush(fill if fill_enabled else Qt.NoBrush)
    else:
        path = QPainterPath(start)
        path.lineTo(end)
        if tool == "arrow":
            direction = end - start
            length = (direction.x() ** 2 + direction.y() ** 2) ** 0.5
            if length < 1:
                item = AnnotationPathItem(path)
                item.setPen(pen)
                return editable(item)
            axis = QPointF(direction.x() / length, direction.y() / length)
            side = QPointF(-axis.y(), axis.x())
            if arrow_style in ("line", "dashed_line"):
                item = AnnotationPathItem(path)
                item.setPen(pen)
                return editable(item)
            if arrow_style in ("rect_filled", "rect_open", "rect_dashed"):
                # 仅箭杆矩形：实心/空心/空心+虚线的矩形，不含箭头。
                shaft_half = max(3.5, width * 1.15)
                path = QPainterPath(start + side * shaft_half)
                path.lineTo(end + side * shaft_half)
                path.lineTo(end - side * shaft_half)
                path.lineTo(start - side * shaft_half)
                path.closeSubpath()
                item = AnnotationPathItem(path)
                item.setPen(pen)
                item.setBrush(QColor(color) if arrow_style == "rect_filled" else Qt.NoBrush)
                return editable(item)
            double_headed = arrow_style in ("double_filled", "double_open", "double_dashed")
            head_length = min(length * (0.36 if double_headed else 0.48),
                              max(24, width * 8))
            shaft_half = min(max(3.5, width * 1.15), head_length * (0.55 if double_headed else 0.8))
            head_half = max(shaft_half * 1.2, head_length * 0.7)
            shoulder = (head_length * 0.25 if double_headed else
                        min(head_length * 0.22, (head_half - shaft_half) * 0.6))
            if double_headed:
                # 双向箭头沿用旧的矩形箭杆外观。
                front = start + axis * head_length
                back = end - axis * head_length
                points = (start,
                          front + side * head_half,
                          front - axis * shoulder + side * shaft_half,
                          back + axis * shoulder + side * shaft_half,
                          back + side * head_half,
                          end,
                          back - side * head_half,
                          back + axis * shoulder - side * shaft_half,
                          front - axis * shoulder - side * shaft_half,
                          front - side * head_half)
            else:
                # 单箭头：三角形箭杆，尾部收为尖点，靠箭头一侧为三角形底边。
                back = end - axis * head_length
                base = back + axis * shoulder
                points = (start,
                          base + side * shaft_half,
                          back + side * head_half,
                          end,
                          back - side * head_half,
                          base - side * shaft_half)
            path = QPainterPath(points[0])
            for point in points[1:]:
                path.lineTo(point)
            path.closeSubpath()
        item = AnnotationPathItem(path)
        item.setPen(pen)
        if tool == "arrow":
            item.setBrush(QColor(color) if arrow_style in ("filled", "double_filled", "rect_filled") else Qt.NoBrush)
    return editable(item)


def text_item(point, text, settings, alignment):
    """为文字图元设置默认字体、对齐和按百分比计算的段落行距。"""
    item = AnnotationTextItem(text)
    font = QFont(settings["font"] or "Microsoft YaHei", settings["font_size"])
    item.setFont(font)
    item.setDefaultTextColor(QColor(settings.get("text_color", settings["pen_color"])))
    option = item.document().defaultTextOption()
    option.setAlignment(alignment)
    item.document().setDefaultTextOption(option)
    item.document().setTextWidth(max(180, len(text) * settings["font_size"]))
    cursor = QTextCursor(item.document())
    cursor.select(QTextCursor.Document)
    block = QTextBlockFormat()
    block.setAlignment(alignment)
    block.setLineHeight(round(settings["line_spacing"] * 100), QTextBlockFormat.ProportionalHeight.value)
    cursor.mergeBlockFormat(block)
    item.setPos(point)
    return editable(item)


class AnnotationSequenceItem(QGraphicsItem):
    """点击放置的序号标记：可选形状 + 填充色 + 居中数字，便于步骤编号。"""

    def __init__(self, number, fill_color="#ff0000", text_color="#ffffff",
                 font_size=14, shape="circle", font_family="", pos=None):
        super().__init__()
        self.number = number
        self.sequence_fill_color = fill_color
        self.sequence_text_color = text_color
        self.sequence_font_size = font_size
        self.sequence_shape = shape if shape in SEQUENCE_SHAPES else "circle"
        self.sequence_font_family = font_family or ""
        self._radius = max(11, int(font_size) + 2)
        if pos is not None:
            self.setPos(pos)
        editable(self)

    def boundingRect(self):
        radius = self._radius
        return QRectF(-radius, -radius, radius * 2, radius * 2)

    def paint(self, painter, option, widget=None):
        radius = self._radius
        painter.save()
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(self.sequence_fill_color))
        points = sequence_shape_points(self.sequence_shape, radius)
        if points is None:
            if self.sequence_shape == "circle":
                painter.drawEllipse(QRectF(-radius, -radius, radius * 2, radius * 2))
            else:
                self._paint_special_shape(painter, self.sequence_shape, radius)
        else:
            path = self._poly_path(points)
            painter.drawPath(path)
        font = QFont(self.sequence_font_family) if self.sequence_font_family else QFont()
        font.setPixelSize(self.sequence_font_size)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor(self.sequence_text_color))
        painter.drawText(QRectF(-radius, -radius, radius * 2, radius * 2),
                        Qt.AlignCenter, str(self.number))
        painter.restore()

    @staticmethod
    def _poly_path(points):
        path = QPainterPath()
        path.moveTo(points[0])
        for point in points[1:]:
            path.lineTo(point)
        path.closeSubpath()
        return path

    def _paint_special_shape(self, painter, shape, radius):
        """绘制心形/箭头/对话气泡/云/十字/水滴等无法用单一凸多边形表达的形状。"""
        if shape == "heart":
            points = []
            for index in range(60):
                angle = math.pi * 2 * index / 60
                x = 16 * math.sin(angle) ** 3
                y = 13 * math.cos(angle) - 5 * math.cos(2 * angle) - \
                    2 * math.cos(3 * angle) - math.cos(4 * angle)
                points.append(QPointF(x * radius / 16.0, -y * radius / 16.0))
            painter.drawPath(self._poly_path(points))
        elif shape == "arrow":
            wing = radius * 0.5
            points = [QPointF(0, -radius), QPointF(wing, -radius * 0.25),
                      QPointF(wing * 0.4, -radius * 0.25),
                      QPointF(wing * 0.4, radius), QPointF(-wing * 0.4, radius),
                      QPointF(-wing * 0.4, -radius * 0.25),
                      QPointF(-wing, -radius * 0.25)]
            painter.drawPath(self._poly_path(points))
        elif shape == "plus":
            painter.drawRect(QRectF(-radius * 0.3, -radius, radius * 0.6, radius * 2))
            painter.drawRect(QRectF(-radius, -radius * 0.3, radius * 2, radius * 0.6))
        elif shape == "drop":
            painter.drawEllipse(QRectF(-radius * 0.7, -radius * 0.2, radius * 1.4, radius * 1.4))
            painter.drawPolygon([
                QPointF(0, -radius), QPointF(-radius * 0.5, -radius * 0.1),
                QPointF(radius * 0.5, -radius * 0.1)])
        elif shape == "bubble":
            painter.drawRoundedRect(QRectF(-radius * 0.85, -radius * 0.7,
                                          radius * 1.7, radius * 1.4),
                                   radius * 0.4, radius * 0.4)
            painter.drawPolygon([
                QPointF(-radius * 0.4, radius * 0.6), QPointF(-radius * 0.7, radius),
                QPointF(0, radius * 0.6)])
        elif shape == "cloud":
            for center_x, center_y, sub in (
                (0, 0, radius * 0.7), (-radius * 0.6, 0, radius * 0.5),
                (radius * 0.6, 0, radius * 0.5), (-radius * 0.3, -radius * 0.4, radius * 0.45),
                (radius * 0.3, -radius * 0.4, radius * 0.45)):
                painter.drawEllipse(QRectF(center_x - sub, center_y - sub, sub * 2, sub * 2))
        else:
            painter.drawPath(self._poly_path(sequence_shape_points("square", radius)))