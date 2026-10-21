"""可选中和移动的 Qt 标注图元工厂。"""

import math

from PySide6.QtCore import Qt, QPointF, QRectF
from PySide6.QtGui import (QColor, QPen, QPainter, QPainterPath, QPainterPathStroker, QFont,
                           QPolygonF,
                           QFontMetricsF, QPixmap, QTextCursor, QTextBlockFormat, QTextCharFormat)
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
    "orange_bubble": {"sequence_shape": "bubble", "sequence_fill_color": "#ff8c00",
                      "sequence_text_color": "#ffffff"},
    "green_cloud": {"sequence_shape": "cloud", "sequence_fill_color": "#2e9e5b",
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
        # 文字背景增强：内边距让背景比文字略大，圆角柔化边角，不透明度可压暗背景。
        # 三者默认 0/0/100，与旧版背景完全一致（不改变已有用户的既有输出）。
        self.background_padding = 0
        self.background_radius = 0
        self.background_opacity = 100
        self.locked = False

    def set_text_height(self, height):
        """设置固定高度；0 表示按内容自动（恢复自然高度）。"""
        height = max(0.0, float(height or 0))
        if abs(height - self.fixed_height) < 1e-6:
            return
        self.prepareGeometryChange()
        self.fixed_height = height
        self.update()

    def set_background_style(self, padding=0, radius=0, opacity=100):
        """设置背景内边距/圆角/不透明度（只作用于当前文字标注）。"""
        padding = max(0, int(padding or 0))
        radius = max(0, int(radius or 0))
        opacity = max(0, min(100, int(opacity if opacity is not None else 100)))
        if (padding, radius, opacity) == (self.background_padding,
                                          self.background_radius,
                                          self.background_opacity):
            return
        if padding != self.background_padding:
            self.prepareGeometryChange()
        self.background_padding = padding
        self.background_radius = radius
        self.background_opacity = opacity
        self.update()

    def auto_height(self):
        """按内容自动排版后的自然高度（忽略固定高度裁剪）。"""
        return float(super().boundingRect().height())

    def content_rect(self):
        """文字内容本身的矩形（背景内边距不包含在内）。"""
        rect = super().boundingRect()
        if self.fixed_height > 0:
            rect.setHeight(self.fixed_height)
        return rect

    def boundingRect(self):
        rect = self.content_rect()
        padding = self.background_padding
        if padding:
            rect = rect.adjusted(-padding, -padding, padding, padding)
        return rect

    def contains(self, point):
        """命中区域包含可见的背景内边距，否则点击色块边缘选不中文字标注。

        注意：QGraphicsTextItem 的 shape() 覆写不会被 Qt 的 scene 命中测试采用
        （实测 scene.items(..., IntersectsItemShape) 走的是 contains），因此这里覆写 contains。
        """
        if self.background_color and self.background_padding:
            return self.boundingRect().contains(point)
        return super().contains(point)

    def paint(self, painter, option, widget=None):
        content = self.content_rect()
        if self.background_color:
            rect = content.adjusted(-self.background_padding, -self.background_padding,
                                    self.background_padding, self.background_padding)
            color = QColor(self.background_color)
            color.setAlpha(round(255 * self.background_opacity / 100))
            painter.save()
            painter.setPen(Qt.NoPen)
            painter.setBrush(color)
            if self.background_radius > 0:
                radius = min(self.background_radius,
                             rect.width() / 2, rect.height() / 2)
                painter.drawRoundedRect(rect, radius, radius)
            else:
                painter.drawRect(rect)
            painter.restore()
        if self.fixed_height > 0:
            # 固定高度时只裁剪文字内容，背景仍完整显示（否则内边距会被一起切掉）。
            painter.save()
            painter.setClipRect(content)
            super().paint(painter, option, widget)
            painter.restore()
            return
        super().paint(painter, option, widget)


class AnnotationZoomItem(AnnotationPaintMixin, QGraphicsPixmapItem):
    """局部放大标注：圆形/矩形镜片 + 指向原选区的云朵小尾巴。

    source_pixmap 保存未放大的原图块；编辑倍率时只重建显示 pixmap，不丢源图。
    source_rect 是场景坐标里的原选区，尾巴终点随图元移动/缩放实时重算。
    """

    def __init__(self, source_pixmap, source_rect, factor=2, style="circle",
                 border_color="#000000", border_width=1, opacity=100,
                 max_size=480, rect_rounded=True, rect_radius=10,
                 glow_enabled=True, glow_color="#168cff", glow_width=10, glow_opacity=55,
                 source_marker="dash", source_corners=True,
                 border_enabled=False, source_color="#000000"):
        super().__init__()
        self.zoom_source = QPixmap(source_pixmap)
        self.source_rect = QRectF(source_rect)
        self.zoom_factor = max(1, min(16, int(factor)))
        self.zoom_style = "rect" if style == "rect" else "circle"
        self.zoom_border_color = border_color
        self.zoom_border_enabled = bool(border_enabled)
        self.zoom_source_color = source_color
        self.zoom_border_width = max(1, min(12, int(border_width)))
        self.zoom_rect_rounded = bool(rect_rounded)
        self.zoom_rect_radius = max(0, min(60, int(rect_radius)))
        self.zoom_glow_enabled = bool(glow_enabled)
        self.zoom_glow_color = glow_color
        self.zoom_glow_width = max(0, min(30, int(glow_width)))
        self.zoom_glow_opacity = max(0, min(100, int(glow_opacity)))
        self.zoom_source_marker = source_marker if source_marker in ("dash", "solid", "off") else "dash"
        self.zoom_source_corners = bool(source_corners)
        self.zoom_max_size = max(80, min(1200, int(max_size)))
        self.zoom_display_scale = 1.0
        self._rebuild_pixmap()
        self.setOpacity(max(0, min(100, int(opacity))) / 100)

    def _content_rect(self):
        return QRectF(self.offset().x(), self.offset().y(),
                      self.pixmap().width(), self.pixmap().height())

    def _frame_path(self, rect=None):
        rect = QRectF(rect or self._content_rect())
        path = QPainterPath()
        if self.zoom_style == "rect":
            radius = (min(float(self.zoom_rect_radius), rect.width() / 2, rect.height() / 2)
                      if self.zoom_rect_rounded else 0.0)
            if radius > 0:
                path.addRoundedRect(rect, radius, radius)
            else:
                path.addRect(rect)
        else:
            path.addEllipse(rect)
        return path

    def _rebuild_pixmap(self):
        source = self.zoom_source
        if source.isNull():
            return
        self.prepareGeometryChange()
        requested_w = max(1, source.width() * self.zoom_factor)
        requested_h = max(1, source.height() * self.zoom_factor)
        # 倍率只是请求值；最终显示尺寸受最大边长约束，避免框选大区域后无限膨胀。
        self.zoom_display_scale = min(1.0, self.zoom_max_size / max(requested_w, requested_h))
        scaled = source.scaled(max(1, round(requested_w * self.zoom_display_scale)),
                               max(1, round(requested_h * self.zoom_display_scale)),
                               Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        self.setPixmap(scaled)
        self.update()

    def set_zoom_max_size(self, max_size):
        value = max(80, min(1200, int(max_size)))
        if value == self.zoom_max_size:
            return
        self.zoom_max_size = value
        self._rebuild_pixmap()

    def set_zoom_factor(self, factor):
        value = max(1, min(16, int(factor)))
        if value == self.zoom_factor:
            return
        self.zoom_factor = value
        self._rebuild_pixmap()

    def set_zoom_style(self, style):
        value = "rect" if style == "rect" else "circle"
        if value != self.zoom_style:
            self.zoom_style = value
            self.update()

    def set_zoom_opacity(self, opacity):
        self.setOpacity(max(0, min(100, int(opacity))) / 100)

    def set_zoom_border_color(self, color):
        value = QColor(color)
        if value.isValid() and value.name() != self.zoom_border_color:
            self.zoom_border_color = value.name()
            self.update()

    def set_zoom_border_width(self, width):
        value = max(1, min(12, int(width)))
        if value != self.zoom_border_width:
            self.zoom_border_width = value
            self.update()

    def set_zoom_border_enabled(self, enabled):
        value = bool(enabled)
        if value != self.zoom_border_enabled:
            self.zoom_border_enabled = value
            self.update()

    def set_zoom_source_color(self, color):
        value = QColor(color)
        if value.isValid() and value.name() != self.zoom_source_color:
            self.zoom_source_color = value.name()
            self.update()

    def set_zoom_rect_rounded(self, enabled):
        value = bool(enabled)
        if value != self.zoom_rect_rounded:
            self.zoom_rect_rounded = value
            self.update()

    def set_zoom_rect_radius(self, radius):
        value = max(0, min(60, int(radius)))
        if value != self.zoom_rect_radius:
            self.zoom_rect_radius = value
            self.update()

    def set_zoom_glow_enabled(self, enabled):
        value = bool(enabled)
        if value != self.zoom_glow_enabled:
            self.zoom_glow_enabled = value
            self.update()

    def set_zoom_glow_color(self, color):
        value = QColor(color)
        if value.isValid() and value.name() != self.zoom_glow_color:
            self.zoom_glow_color = value.name()
            self.update()

    def set_zoom_glow_width(self, width):
        value = max(0, min(30, int(width)))
        if value != self.zoom_glow_width:
            self.zoom_glow_width = value
            self.update()

    def set_zoom_glow_opacity(self, opacity):
        value = max(0, min(100, int(opacity)))
        if value != self.zoom_glow_opacity:
            self.zoom_glow_opacity = value
            self.update()

    def set_zoom_source_marker(self, style):
        value = style if style in ("dash", "solid", "off") else "dash"
        if value != self.zoom_source_marker:
            self.zoom_source_marker = value
            self.update()

    def set_zoom_source_corners(self, enabled):
        value = bool(enabled)
        if value != self.zoom_source_corners:
            self.zoom_source_corners = value
            self.update()

    def _source_marker(self):
        """源区域轮廓，映射到本图元局部坐标；形状跟随镜片圆/方样式。"""
        if self.zoom_source_marker == "off" or self.zoom_source.isNull() or self.source_rect.isEmpty():
            return None
        frame = self._frame_path()
        if frame.contains(self.mapFromScene(self.source_rect.center())):
            return None
        local = QRectF(
            self.mapFromScene(self.source_rect.topLeft()),
            self.mapFromScene(self.source_rect.bottomRight())).normalized()
        path = QPainterPath()
        if self.zoom_style == "circle":
            path.addEllipse(local)
        else:
            radius = (min(float(self.zoom_rect_radius), local.width() / 2, local.height() / 2)
                      if self.zoom_rect_rounded else 0.0)
            if radius > 0:
                path.addRoundedRect(local, radius, radius)
            else:
                path.addRect(local)
        return path

    def boundingRect(self):
        base = super().boundingRect()
        halo_width = self.zoom_glow_width if self.zoom_glow_enabled else 0
        halo_padding = max(8.0, self.zoom_border_width + 5.0, halo_width + 3.0)
        base = base.adjusted(-halo_padding, -halo_padding, halo_padding, halo_padding)
        marker = self._source_marker()
        if marker is None:
            return base
        padding = 10.0
        return base.united(marker.boundingRect().adjusted(-padding, -padding, padding, padding))

    def shape(self):
        path = self._frame_path()
        marker = self._source_marker()
        if marker is not None:
            stroker = QPainterPathStroker()
            stroker.setWidth(max(3.0, self.zoom_border_width * 1.5))
            stroker.setCapStyle(Qt.RoundCap)
            stroker.setJoinStyle(Qt.RoundJoin)
            path.addPath(stroker.createStroke(marker))
        return path

    def _draw_source_marker(self, painter, marker):
        """源区域按配置绘制：圆形/圆角矩形无直角角标，方形矩形可选四角角标。"""
        style = Qt.DashLine if self.zoom_source_marker == "dash" else Qt.SolidLine
        color = QColor(self.zoom_source_color)
        color.setAlpha(190)
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(color, max(1.0, self.zoom_border_width * 0.85),
                            style, Qt.RoundCap, Qt.RoundJoin))
        painter.drawPath(marker)
        is_square_rect = (self.zoom_style == "rect"
                          and (not self.zoom_rect_rounded or self.zoom_rect_radius <= 0))
        if not is_square_rect or not self.zoom_source_corners:
            return
        corner_color = QColor(self.zoom_source_color)
        corner_color.setAlpha(235)
        painter.setPen(QPen(corner_color, max(1.4, self.zoom_border_width * 1.1),
                            Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        bounds = marker.boundingRect()
        points = [bounds.topLeft(), bounds.topRight(),
                  bounds.bottomRight(), bounds.bottomLeft()]
        for index, point in enumerate(points):
            for neighbor in (points[(index - 1) % 4], points[(index + 1) % 4]):
                vx, vy = neighbor.x() - point.x(), neighbor.y() - point.y()
                length = math.hypot(vx, vy)
                if length <= 0.01:
                    continue
                reach = min(10.0, length * 0.28)
                painter.drawLine(point, QPointF(point.x() + vx * reach / length,
                                                point.y() + vy * reach / length))

    def paint(self, painter, option, widget=None):
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        frame = self._frame_path()
        marker = self._source_marker()
        if marker is not None:
            self._draw_source_marker(painter, marker)
        # 镜片外圈柔光：颜色跟随边框，开关/宽度/不透明度由配置控制。
        if self.zoom_glow_enabled and self.zoom_glow_width > 0 and self.zoom_glow_opacity > 0:
            halo = QColor(self.zoom_glow_color)
            halo.setAlpha(round(255 * self.zoom_glow_opacity / 100))
            painter.setPen(QPen(halo, max(1.0, float(self.zoom_glow_width)),
                                Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            painter.setBrush(Qt.NoBrush)
            painter.drawPath(frame)
        painter.save()
        painter.setClipPath(frame)
        painter.drawPixmap(self.offset(), self.pixmap())
        painter.restore()
        if self.zoom_border_enabled:
            painter.setPen(QPen(QColor(self.zoom_border_color),
                                self.zoom_border_width, Qt.SolidLine,
                                Qt.RoundCap, Qt.RoundJoin))
            painter.setBrush(Qt.NoBrush)
            painter.drawPath(frame)
        painter.restore()


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

    def shape(self):
        """命中范围＝擦除笔迹本身，而不是整幅底图。

        默认实现返回 `boundingRect()`（整幅图），任何走场景命中测试的代码都会被擦除层
        整屏挡住——历史上正是它让被擦过的标注无法选中/编辑/删除。这里按笔迹生成描边
        路径：即便将来有代码回退到场景命中，也只会命中真正擦过的位置。
        """
        path = QPainterPath()
        for stroke in self.strokes:
            if self._stroke_kind(stroke) == "rect":
                path.addRect(QRectF(stroke[1], stroke[2], stroke[3], stroke[4]))   # 矩形擦除的命中范围 ✓
                continue
            segment = QPainterPath(QPointF(stroke[1], stroke[2]))
            segment.lineTo(QPointF(stroke[3], stroke[4]))
            stroker = QPainterPathStroker()
            stroker.setWidth(max(1.0, float(stroke[5])))
            stroker.setCapStyle(Qt.RoundCap)
            stroker.setJoinStyle(Qt.RoundJoin)
            path.addPath(stroker.createStroke(segment))
        return path

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

    def add_rect(self, group, rect, erase_base=False, kind="rect"):
        """记录并填充一块**矩形擦除**（2026-10-11 用户要求：橡皮擦支持「框选」模式 ✓）。

        笔迹元组与线段同构，只多出第 8 位 `kind`（"line" / "rect" ✓）；旧笔迹没有该位时按线段处理 ✓
        （`paint_stroke` / `shape` / `_stroke_distance` 都做了兜底 ✓ 不影响历史会话 ✓）。
        """
        stroke = (group, rect.x(), rect.y(), rect.width(), rect.height(), 0.0,
                  bool(erase_base), kind)
        self.strokes.append(stroke)
        self._paint_stroke(stroke)

    @staticmethod
    def _stroke_kind(stroke):
        """笔迹类型：第 8 位存在且为 "rect" 时是矩形擦除，其余按线段处理 ✓。"""
        return stroke[7] if len(stroke) > 7 else "line"

    def render(self):
        """按记录的笔迹重绘遮罩，使遮罩与笔迹记录随时保持一致。"""
        self.mask.fill(Qt.transparent)
        for stroke in self.strokes:
            self._paint_stroke(stroke)

    def drop_groups(self, groups):
        """丢弃指定分组的笔迹并重绘遮罩；返回是否发生了变化。"""
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
                # 保留第 8 位 kind（矩形擦除 ✓）；旧笔迹没有该位 ⇒ 拼出来仍与原来一致 ✓
                stroke = stroke[:6] + (bool(erase_base),) + tuple(stroke[7:])
                changed = True
            updated.append(stroke)
        if changed:
            self.strokes = updated
        return changed

    @staticmethod
    def _stroke_distance(stroke, point):
        if EraseMaskItem._stroke_kind(stroke) == "rect":
            # 矩形擦除：点在框内距离 0，框外取到四条边的最小距离 ✓
            rect = QRectF(stroke[1], stroke[2], stroke[3], stroke[4])
            if rect.contains(point):
                return 0.0
            return min(_point_to_segment(point, rect.topLeft(), rect.topRight()),
                       _point_to_segment(point, rect.topRight(), rect.bottomRight()),
                       _point_to_segment(point, rect.bottomRight(), rect.bottomLeft()),
                       _point_to_segment(point, rect.bottomLeft(), rect.topLeft()))
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
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("white"))
        if self._stroke_kind(stroke) == "rect":
            # 矩形擦除：整块填充 ✓（"框选"模式擦除框内内容 ✓）
            painter.drawRect(QRectF(stroke[1], stroke[2], stroke[3], stroke[4]))
            return
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
        # 圆角矩形标注：显式开启抗锯齿，不依赖调用方（视图/导出画笔）的 renderHint。
        painter.setRenderHint(QPainter.Antialiasing, True)
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
    if not hasattr(item, "locked"):
        item.locked = False
    return item


def annotation_locked(item):
    """标注是否已锁定。"""
    return bool(getattr(item, "locked", False))


def set_annotation_locked(item, locked):
    """锁定/解锁单个标注。

    锁定后取消选中并移除选中/移动标志：右键菜单仍能命中它并提供「解锁」，
    但拖动、缩放、旋转、删除与样式批量修改都不会再作用到它。
    """
    item.locked = bool(locked)
    if item.locked:
        item.setSelected(False)
    item.setFlag(QGraphicsItem.ItemIsSelectable, not item.locked)
    item.setFlag(QGraphicsItem.ItemIsMovable, not item.locked)
    item.update()
    return item.locked


def _quadratic_point(start, control, end, t):
    """二次贝塞尔曲线在参数 t 处的点。"""
    inverse = 1.0 - t
    return QPointF(inverse * inverse * start.x() + 2 * inverse * t * control.x() + t * t * end.x(),
                   inverse * inverse * start.y() + 2 * inverse * t * control.y() + t * t * end.y())


def curved_arrow_polygon(start, end, width, direction="right"):
    """生成曲线箭头轮廓：弯曲方向可切换，箭头头部沿用直线单箭头的三角头。

    direction 为 "left"/"right"，表示沿 start→end 行进时向哪一侧弯。
    箭杆沿二次曲线采样；头部使用终点切线、与直线箭头相同的
    head_length/shaft_half/head_half/shoulder 比例，避免旧实现把弯曲点直接当
    头底导致箭头歪斜。返回 None 表示起止点重合、无法建模。
    """
    direction_value = end - start
    length = math.hypot(direction_value.x(), direction_value.y())
    if length < 1:
        return None
    axis = QPointF(direction_value.x() / length, direction_value.y() / length)
    normal = QPointF(-axis.y(), axis.x())
    bend = -1.0 if direction == "left" else 1.0
    control = QPointF((start.x() + end.x()) / 2 + normal.x() * length * 0.22 * bend,
                      (start.y() + end.y()) / 2 + normal.y() * length * 0.22 * bend)
    head_length = min(length * 0.48, max(24, width * 8), length * 0.9)
    shaft_half = min(max(3.5, width * 1.15), head_length * 0.8)
    head_half = max(shaft_half * 1.2, head_length * 0.7)
    shoulder = min(head_length * 0.22, (head_half - shaft_half) * 0.6)
    # 采样曲线并按累计弧长定位头部底边，避免按参数 t 估算导致头过长/过短。
    samples = []
    previous = start
    total = 0.0
    for index in range(25):
        t = index / 24.0
        point = _quadratic_point(start, control, end, t)
        total += math.hypot(point.x() - previous.x(), point.y() - previous.y())
        if index > 0:
            samples.append((t, point, total))
        previous = point
    if not samples or total <= 1e-6:
        return None
    # 终点切线决定箭头朝向；曲线在末端仍有明显弯曲时，不能拿起止直线当箭轴。
    end_axis = end - _quadratic_point(start, control, end, 23.0 / 24.0)
    end_axis_length = math.hypot(end_axis.x(), end_axis.y())
    if end_axis_length > 1e-9:
        head_axis = QPointF(end_axis.x() / end_axis_length, end_axis.y() / end_axis_length)
    else:
        head_axis = axis
    head_side = QPointF(-head_axis.y(), head_axis.x())
    target = max(0.0, total - (head_length - shoulder))
    base_t, base_point = samples[-1][0], samples[-1][1]
    for t, point, distance in samples:
        if distance >= target:
            base_t, base_point = t, point
            break
    base_tangent = _quadratic_point(start, control, end, min(1.0, base_t + 0.01)) - base_point
    base_tangent_length = math.hypot(base_tangent.x(), base_tangent.y())
    base_normal = (QPointF(-base_tangent.y() / base_tangent_length,
                           base_tangent.x() / base_tangent_length)
                   if base_tangent_length > 1e-9 else normal)
    left = [start + normal * shaft_half]
    right = [start - normal * shaft_half]
    for t, point, _distance in samples:
        if t > base_t:
            break
        tangent = _quadratic_point(start, control, end, min(1.0, t + 0.01)) - point
        tangent_length = math.hypot(tangent.x(), tangent.y())
        side = (QPointF(-tangent.y() / tangent_length, tangent.x() / tangent_length)
                if tangent_length > 1e-9 else normal)
        left.append(point + side * shaft_half)
        right.append(point - side * shaft_half)
    left.append(base_point + base_normal * shaft_half)
    right.append(base_point - base_normal * shaft_half)
    back = base_point - head_axis * shoulder
    polygon = left + [back + head_side * head_half, end, back - head_side * head_half]
    polygon += list(reversed(right))
    return polygon



def shape(tool, start, end, color, width, arrow_style="filled", corner_radius=0,
          fill_enabled=False, fill_opacity=35, fill_color=None,
          arrow_curve_direction="right"):
    """按工具类型建立可选中、可移动的矢量图元。

    fill_color 为 None 时填充沿用线条颜色，保持旧行为。
    """
    bounds = QRectF(start, end).normalized()
    dashed = arrow_style in ("dashed", "double_dashed", "dashed_line", "rect_dashed",
                             "curve_open_dashed", "dash")
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
                item.arrow_curve_direction = arrow_curve_direction
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
                item.arrow_curve_direction = arrow_curve_direction
                item.line_color = color
                item.line_width = width
                return editable(item)
            if arrow_style in ("curve_filled", "curve_open", "curve_open_dashed"):
                points = curved_arrow_polygon(start, end, width, arrow_curve_direction)
                if points is None:
                    item = AnnotationPathItem(path)
                    item.setPen(pen)
                    return editable(item)
                curved = QPainterPath(points[0])
                for point in points[1:]:
                    curved.lineTo(point)
                curved.closeSubpath()
                item = AnnotationPathItem(curved)
                item.setPen(pen)
                item.setBrush(QColor(color) if arrow_style == "curve_filled" else Qt.NoBrush)
                item.start = start
                item.end = end
                item.arrow_style = arrow_style
                item.arrow_curve_direction = arrow_curve_direction
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
            item.arrow_curve_direction = arrow_curve_direction
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
        item.set_background_style(
            settings.get("text_background_padding", 0),
            settings.get("text_background_radius", 0),
            settings.get("text_background_opacity", 100))
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
        # 序号形状含圆/圆角气泡等曲线：显式开启抗锯齿，不依赖调用方的 renderHint。
        painter.setRenderHint(QPainter.Antialiasing, True)
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