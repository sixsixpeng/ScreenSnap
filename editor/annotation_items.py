"""可选中和移动的 Qt 标注图元工厂。"""

from PySide6.QtCore import Qt, QPointF, QRectF
from PySide6.QtGui import QColor, QPen, QPainterPath, QFont, QTextCursor, QTextBlockFormat
from PySide6.QtWidgets import (QGraphicsItem, QGraphicsRectItem, QGraphicsEllipseItem,
                               QGraphicsPathItem, QGraphicsTextItem, QGraphicsPixmapItem,
                               QStyle, QStyleOptionGraphicsItem)


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
    dashed = arrow_style in ("dash", "solid_dash", "open_dash")
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
            if arrow_style in ("solid_line", "solid_dash"):
                item = AnnotationPathItem(path)
                item.setPen(pen)
                return editable(item)
            if arrow_style in ("open_line", "open_dash"):
                shaft_half = max(3, width * 1.6)
                path = QPainterPath(start + side * shaft_half)
                path.lineTo(end + side * shaft_half)
                path.lineTo(end - side * shaft_half)
                path.lineTo(start - side * shaft_half)
                path.closeSubpath()
                item = AnnotationPathItem(path)
                item.setPen(pen)
                item.setBrush(Qt.NoBrush)
                return editable(item)
            double_headed = arrow_style in ("double", "double_filled")
            head_length = min(length * (0.36 if double_headed else 0.48),
                              max(24, width * 8))
            shaft_half = min(max(3.5, width * 1.15), head_length * (0.55 if double_headed else 0.8))
            head_half = max(shaft_half * 1.2, head_length * 0.7)
            shoulder = (head_length * 0.25 if double_headed else
                        min(head_length * 0.22, (head_half - shaft_half) * 0.6))

            if double_headed:
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
                back = end - axis * head_length
                points = (start + side * shaft_half,
                          back + axis * shoulder + side * shaft_half,
                          back + side * head_half,
                          end,
                          back - side * head_half,
                          back + axis * shoulder - side * shaft_half,
                          start - side * shaft_half)
            path = QPainterPath(points[0])
            for point in points[1:]:
                path.lineTo(point)
            path.closeSubpath()
        item = AnnotationPathItem(path)
        item.setPen(pen)
        if tool == "arrow":
            item.setBrush(QColor(color) if arrow_style in ("filled", "double_filled") else Qt.NoBrush)
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