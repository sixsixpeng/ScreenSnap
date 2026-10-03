"""可选中和移动的 Qt 标注图元工厂。"""

import math

from PySide6.QtCore import Qt, QPointF, QRectF
from PySide6.QtGui import (QColor, QPen, QPainter, QPainterPath, QFont, QFontMetricsF,
                           QTextCursor, QTextBlockFormat, QPolygonF, QTextCharFormat)
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
    """文字标注；可选背景色块绘制在文字之下，便于突出显示。

    支持固定高度（`set_text_height`，0 表示按内容自动）：固定高度后高度不再随
    内容增长、超出部分被裁剪，便于用上下手柄精确控制文本框的纵向大小。
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.background_color = None
        self.fixed_height = 0.0

    def set_text_height(self, height):
        """设置固定高度；0 表示按内容自动（恢复自然高度）。"""
        height = max(0.0, float(height or 0))
        if abs(height - self.fixed_height) < 1e-6:
            return
        self.prepareGeometryChange()
        self.fixed_height = height
        self.update()

    def auto_height(self):
        """按内容自动排版后的自然高度（忽略固定高度裁剪）。"""
        return float(super().boundingRect().height())

    def boundingRect(self):
        rect = super().boundingRect()
        if self.fixed_height > 0:
            rect.setHeight(self.fixed_height)
        return rect

    def paint(self, painter, option, widget=None):
        rect = self.boundingRect()
        if self.background_color:
            painter.save()
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(self.background_color))
            painter.drawRect(rect)
            painter.restore()
        if self.fixed_height > 0:
            # 固定高度时裁剪超出区域，使显示范围与手柄位置一致。
            painter.save()
            painter.setClipRect(rect)
            super().paint(painter, option, widget)
            painter.restore()
            return
        super().paint(painter, option, widget)


class AnnotationPixmapItem(AnnotationPaintMixin, QGraphicsPixmapItem):
    pass


class EraseMaskItem(QGraphicsItem):
    """非破坏性擦除层：在标注层内按 z 顺序镂空其下方内容。

    以遮罩（白色=擦除）用 DestinationOut 清除同一图层中 z 更低（更早绘制）的像素，
    因此“先擦除再新画”的标注位于擦除之上、不会被擦到，也支持多次擦除与多层覆盖。
    擦除层不参与选中/移动，只作为渲染层存在于场景中。

    同时记录每段笔迹（`strokes`，按第几次拖动分组）：遮罩始终等于这些笔迹的渲染结果，
    因此可以按笔迹精确删除（最近一次 / 经过某点），不必整层丢弃导致所有擦除一起消失。
    """

    def __init__(self, mask, width, height, strokes=None):
        super().__init__()
        self.mask = mask
        self._rect = QRectF(0, 0, width, height)
        self.strokes = [tuple(stroke) for stroke in (strokes or [])]
        self.setFlag(QGraphicsItem.ItemIsSelectable, False)
        self.setFlag(QGraphicsItem.ItemIsMovable, False)
        self.setAcceptedMouseButtons(Qt.NoButton)

    def boundingRect(self):
        return self._rect

    def paint(self, painter, option, widget=None):
        painter.save()
        painter.setCompositionMode(QPainter.CompositionMode_DestinationOut)
        painter.drawImage(0, 0, self.mask)
        painter.restore()

    def add_stroke(self, group, start, end, width, erase_base=False):
        """记录并绘制一段擦除笔迹。

        `group` 表示同一次拖动（同一次擦除）；`erase_base` 记录这段笔迹是否同时擦掉
        截图原图——按笔迹保存，因此之后切换「同时擦除原图」不会改变已有擦除。
        """
        stroke = (group, start.x(), start.y(), end.x(), end.y(), float(width), bool(erase_base))
        self.strokes.append(stroke)
        self._paint_stroke(stroke)

    def render(self):
        """按记录的笔迹重绘遮罩，使遮罩与笔迹记录随时保持一致。"""
        self.mask.fill(Qt.transparent)
        for stroke in self.strokes:
            self._paint_stroke(stroke)

    def drop_groups(self, groups):
        """丢弃指定分组的笔迹并重绘遮罩；返回是否发生了变化。"""
        groups = set(groups)
        if not groups:
            return False
        kept = [stroke for stroke in self.strokes if stroke[0] not in groups]
        if len(kept) == len(self.strokes):
            return False
        self.strokes = kept
        self.render()
        return True

    def group_ids(self):
        """本层包含的擦除分组编号集合。"""
        return {stroke[0] for stroke in self.strokes}

    def groups_near(self, point, slack=3.0):
        """返回笔迹经过该点（距离 ≤ 笔画半径 + slack）的分组编号集合。"""
        hit = set()
        for stroke in self.strokes:
            if self._stroke_distance(stroke, point) <= stroke[5] / 2 + slack:
                hit.add(stroke[0])
        return hit

    def base_strokes(self):
        """需要同时擦掉截图原图的那部分笔迹。"""
        return [stroke for stroke in self.strokes if stroke[6]]

    def base_flags(self, groups=None):
        """返回指定分组（默认全部）的“是否擦除原图”标记集合。"""
        return {stroke[6] for stroke in self.strokes
                if groups is None or stroke[0] in groups}

    def set_groups_erase_base(self, groups, erase_base):
        """修改指定分组笔迹的“是否擦除原图”标记；遮罩本身不受影响，返回是否有改动。"""
        groups = set(groups)
        changed = False
        updated = []
        for stroke in self.strokes:
            if stroke[0] in groups and bool(stroke[6]) != bool(erase_base):
                stroke = stroke[:6] + (bool(erase_base),)
                changed = True
            updated.append(stroke)
        if changed:
            self.strokes = updated
        return changed

    @staticmethod
    def _stroke_distance(stroke, point):
        return _point_to_segment(point, QPointF(stroke[1], stroke[2]),
                                 QPointF(stroke[3], stroke[4]))

    def _paint_stroke(self, stroke):
        painter = QPainter(self.mask)
        painter.setRenderHint(QPainter.Antialiasing)
        self.paint_stroke(painter, stroke)
        painter.end()

    def paint_stroke(self, painter, stroke):
        """把一段笔迹画到给定画笔上（擦除遮罩与底图镂空共用同一套几何）。"""
        width = stroke[5]
        start = QPointF(stroke[1], stroke[2])
        end = QPointF(stroke[3], stroke[4])
        painter.setPen(QPen(QColor("white"), width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        painter.drawLine(start, end)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("white"))
        painter.drawEllipse(end, width / 2, width / 2)


def _point_to_segment(point, start, end):
    """点到线段的距离（像素），用于判断擦除笔迹是否经过某点。"""
    dx, dy = end.x() - start.x(), end.y() - start.y()
    if not dx and not dy:
        return math.hypot(point.x() - start.x(), point.y() - start.y())
    ratio = ((point.x() - start.x()) * dx + (point.y() - start.y()) * dy) / (dx * dx + dy * dy)
    ratio = max(0.0, min(1.0, ratio))
    return math.hypot(point.x() - (start.x() + ratio * dx),
                      point.y() - (start.y() + ratio * dy))


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
          fill_enabled=False, fill_opacity=35, fill_color=None):
    """按工具类型建立可选中、可移动的矢量图元。

    fill_color 为 None 时填充沿用线条颜色，保持旧行为。
    """
    bounds = QRectF(start, end).normalized()
    dashed = arrow_style in ("dashed", "double_dashed", "dashed_line", "rect_dashed", "dash")
    pen = QPen(QColor(color), width, Qt.DashLine if dashed else Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
    fill = QColor(fill_color or color)
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
                item.start = start
                item.end = end
                item.arrow_style = arrow_style
                item.line_color = color
                item.line_width = width
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
                item.start = start
                item.end = end
                item.arrow_style = arrow_style
                item.line_color = color
                item.line_width = width
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
            item.start = start
            item.end = end
            item.arrow_style = arrow_style
            item.line_color = color
            item.line_width = width
    return editable(item)


def apply_text_format(item, bold, italic, underline, strike):
    """整篇应用文字格式（粗体/斜体/下划线/删除线），Qt5/6 通用。"""
    cursor = item.textCursor()
    cursor.select(QTextCursor.Document)
    fmt = QTextCharFormat()
    fmt.setFontWeight(QFont.Bold if bold else QFont.Normal)
    fmt.setFontItalic(italic)
    fmt.setFontUnderline(underline)
    fmt.setFontStrikeOut(strike)
    cursor.mergeCharFormat(fmt)
    cursor.clearSelection()
    item.setTextCursor(cursor)


def read_text_format(item):
    """读取整篇文字的当前格式标志（粗体/斜体/下划线/删除线）。"""
    cursor = item.textCursor()
    if item.document().characterCount() > 1:
        cursor.setPosition(1)
    else:
        cursor.setPosition(0)
    fmt = cursor.charFormat()
    return (fmt.fontWeight() >= QFont.Bold, fmt.fontItalic(),
            fmt.fontUnderline(), fmt.fontStrikeOut())


def auto_text_width(text, font):
    """自动模式下的文本框宽度：按字体实测内容宽度给紧凑值（含少量留白），避免默认过宽。"""
    metrics = QFontMetricsF(font)
    lines = text.split("\n") or [""]
    natural = max(metrics.horizontalAdvance(line) for line in lines)
    return max(60.0, float(math.ceil(natural) + 12))


def text_item(point, text, settings, alignment):
    """为文字图元设置默认字体、对齐和按百分比计算的段落行距。"""
    item = AnnotationTextItem(text)
    font = QFont(settings["font"] or "Microsoft YaHei", settings["font_size"])
    item.setFont(font)
    # 注意：不能用 settings.get("text_color", settings["pen_color"])——默认值会被预先求值，
    # 编辑对话框传入的样式快照可能不含 pen_color 而抛 KeyError。
    item.setDefaultTextColor(QColor(settings.get("text_color") or settings.get("pen_color")
                                    or "#ff0000"))
    if settings.get("text_background_enabled"):
        item.background_color = settings.get("text_background", "#fff3a0")
    apply_text_format(item, settings.get("text_bold", False), settings.get("text_italic", False),
                     settings.get("text_underline", False), settings.get("text_strikethrough", False))
    option = item.document().defaultTextOption()
    option.setAlignment(alignment)
    item.document().setDefaultTextOption(option)
    # 文本框宽度：配置为正数时用固定宽度（超出自动换行），0 表示按内容自动。
    text_width = int(settings.get("text_width", 0) or 0)
    item.document().setTextWidth(float(text_width) if text_width > 0
                                 else auto_text_width(text, font))
    cursor = QTextCursor(item.document())
    cursor.select(QTextCursor.Document)
    block = QTextBlockFormat()
    block.setAlignment(alignment)
    block.setLineHeight(round(settings["line_spacing"] * 100), QTextBlockFormat.ProportionalHeight.value)
    cursor.mergeBlockFormat(block)
    # 文本框高度：配置为正数时固定高度（超出裁剪），0 表示按内容自动增长。
    text_height = int(settings.get("text_height", 0) or 0)
    if text_height > 0:
        item.set_text_height(text_height)
    item.setPos(point)
    return editable(item)


def auto_text_height(text, settings, alignment):
    """按内容自动排版后的文本框高度（忽略固定高度，与实际建立的高度一致）。

    用于对话框「高度＝自动」时提示实际生效的高度：按当前文字、字体、宽度与
    行距真实排版一次后取值，避免另写一套估算公式与实际渲染不一致。
    """
    probe = text_item(QPointF(0, 0), text, {**settings, "text_height": 0,
                                            "line_spacing": settings.get("line_spacing") or 1.2},
                      alignment)
    return float(probe.auto_height())


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