"""图片画布、标注事件和可撤销操作。"""

import logging
import math

from PIL import Image, ImageFilter
from PySide6.QtCore import Qt, QPointF, QRectF, QLineF, Signal
from PySide6.QtGui import (QBrush, QPainter, QPainterPath, QPen, QColor, QPixmap, QImage,
                             QPolygonF, QPainterPathStroker, QTextBlockFormat,
                             QTextCursor, QTransform, QCursor, QKeySequence, QFont)
from PySide6.QtWidgets import (QApplication, QGraphicsView, QGraphicsScene, QGraphicsPixmapItem,
                               QGraphicsItem, QGraphicsRectItem, QGraphicsEllipseItem,
                               QGraphicsTextItem, QDialog, QMenu, QToolTip,
                               QStyleOptionGraphicsItem)

from core.screen_capture import to_qimage
from config.config_manager import TOOL_WIDTH_KEYS
from editor.annotation_items import (shape, text_item, editable, RoundedRectItem,
                                     AnnotationRectItem, AnnotationEllipseItem,
                                     AnnotationPathItem, AnnotationTextItem,
                                     AnnotationPixmapItem, AnnotationSequenceItem,
                                     EraseMaskItem, auto_text_width,
                                     apply_text_format, read_text_format)

# 直线/折线模式下单段最短位移（像素），低于此值视为“未确定第二点”，不绘制。
STRAIGHT_MIN_DISTANCE = 3

# 文字标注用边中点拖动调整文本框时的最小宽/高（像素），避免拖成不可见。
MIN_TEXT_WIDTH = 40.0
MIN_TEXT_HEIGHT = 24.0


def mosaic_image(sample, mode, size):
    """对框选像素生成可移动的方块、细粒或毛玻璃标注。"""
    if mode == "blur":
        return sample.filter(ImageFilter.GaussianBlur(radius=max(1, size / 2)))
    block = max(2, size // 3) if mode == "fine" else size
    small = sample.resize((max(1, sample.width // block), max(1, sample.height // block)),
                          Image.Resampling.NEAREST)
    return small.resize(sample.size, Image.Resampling.NEAREST)


class AnnotationCanvas(QGraphicsView):
    """管理底图、可编辑图元和同时覆盖图片版本的撤销历史。"""

    changed = Signal()
    color_picked = Signal(str)
    confirmed = Signal()
    cancelled = Signal()
    zoom_changed = Signal(int)
    selection_requested = Signal()
    # 选中标注类型变化时发出（"rect"/"ellipse"/"arrow"/"text"/"pen"/"number"/""），
    # 供工具栏按选中项类型展示对应的「更多设置」参数。
    selected_annotation_changed = Signal(str)
    # 画布内入口（如文字输入对话框）改动配置时发出，交由编辑器同步配置与工具栏。
    setting_changed = Signal(str, object)

    def __init__(self, image, settings, alternate=None):
        super().__init__()
        self.settings = settings
        self.crop_color = settings["crop_color"]
        self.crop_width = settings["crop_width"]
        self.original = image.copy()
        self.image = image.copy()
        self.alternate = alternate.copy() if alternate else None
        self.original_alternate = self.alternate.copy() if self.alternate else None
        self.cursor_enabled = settings["cursor"]
        self.scene_data = QGraphicsScene(self)
        self.setScene(self.scene_data)
        self.scene_data.selectionChanged.connect(self._on_selection_changed)
        self.base = QGraphicsPixmapItem()
        # 底图固定在所有标注之下，且不会进入标注快照。
        self.base.setZValue(-10000)
        self.scene_data.addItem(self.base)
        self.round_corner_preview = False
        self.corner_radius = settings.get("editor_image_corner_radius", 16)
        checker = QPixmap(12, 12)
        checker.fill(QColor("#ffffff"))
        checker_painter = QPainter(checker)
        checker_painter.fillRect(0, 0, 6, 6, QColor("#d8d8d8"))
        checker_painter.fillRect(6, 6, 6, 6, QColor("#d8d8d8"))
        checker_painter.end()
        self.corner_preview_brush = QBrush(checker)
        self.refresh_image()
        self.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
        self.setViewportUpdateMode(QGraphicsView.FullViewportUpdate)
        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)
        self.setAlignment(Qt.AlignCenter)
        self.setDragMode(QGraphicsView.RubberBandDrag)
        self.tool = "select"
        self._picker_cursor = self._create_picker_cursor()
        self.text_alignment = Qt.AlignLeft
        self.start = None
        self.drawing = None
        self.preview_end = None
        self.chain_active = False
        self.committed_segments = []
        self.straight_drawing = False
        self.resizing = None
        self.resize_handle = None
        self.resize_anchor = None
        self.resize_anchor_local = None
        self.resize_scale = 1
        self.resize_transform = None
        # 文字标注边中点调整宽度时的按下基准（宽度, 位置），避免逐帧叠加导致跳跃。
        self.text_resize_origin = None
        self.alignment_guides = []
        self.rotating = None
        self.rotation_center = None
        self.rotation_start_angle = 0.0
        self.rotation_start_value = 0.0
        self.mosaic_drawing = None
        self.mosaic_point = None
        # 擦除以带 z 序的 EraseMaskItem 存在于场景中；该标志表示当前是否有擦除层，
        # 供实时视图决定是否走合成路径。
        self.erase_mask_dirty = False
        self.resize_origin = None
        self.resize_position = None
        self.resize_start = None
        # 空格同时是临时平移键；这里单独记录其按下状态，用于四角自由拉伸判定。
        self.space_pressed = False
        self.eraser_last = None
        self.eraser_point = None
        # 擦除笔迹分组编号：每次按下（或直接调用 erase_segment）递增，用于按笔迹删除擦除。
        self.eraser_group = 0
        self.erasing = False
        self.right_pan_start = None
        self.right_pan_cursor = None
        self.pan_button = None
        self.right_pan_moved = False
        self.selection_start = None
        self.selection_end = None
        self.selection_area = None
        self.history = [(self.image, self.alternate, self.cursor_enabled, [])]
        self.cursor_index = 0
        self.zoom_percent = 100

    @staticmethod
    def _create_picker_cursor():
        """创建尖端定位明确、深浅对比清晰的滴管光标。"""
        pixmap = QPixmap(32, 32)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        body = QPainterPath(QPointF(4, 28))
        body.lineTo(9, 23)
        body.lineTo(19, 13)
        body.lineTo(25, 19)
        body.lineTo(15, 29)
        body.lineTo(8, 31)
        body.closeSubpath()
        painter.setPen(QPen(QColor("#142c32"), 2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        painter.setBrush(QColor("#f7fbfa"))
        painter.drawPath(body)
        painter.setPen(QPen(QColor("#315c66"), 2, Qt.SolidLine, Qt.RoundCap))
        painter.drawLine(QPointF(8, 26), QPointF(19, 15))
        painter.setPen(QPen(QColor("#142c32"), 2, Qt.SolidLine, Qt.RoundCap))
        painter.setBrush(QColor("#00ad91"))
        painter.drawEllipse(QPointF(24, 8), 4, 4)
        painter.setPen(QPen(QColor("#f7fbfa"), 1.5, Qt.SolidLine, Qt.RoundCap))
        painter.drawLine(QPointF(22, 8), QPointF(26, 8))
        painter.end()
        return QCursor(pixmap, 4, 28)

    def set_tool(self, tool):
        """切换标注工具并同步其专属光标。"""
        if tool != self.tool:
            self.chain_active = False
            self.start = None
            self.preview_end = None
            self.committed_segments = []
            self.straight_drawing = False
        previous = self.tool
        self.tool = tool
        if tool == "picker":
            self.setCursor(self._picker_cursor)
        elif previous == "picker" or tool != "select":
            self.unsetCursor()

    def _chain_tool(self):
        """当前工具是否支持多段连续绘制（箭头/开启多段的画笔/记号笔）。"""
        if self.tool == "arrow":
            return True
        if self.tool in ("pen", "marker"):
            return bool(self.settings.get(f"{self.tool}_chain", False))
        return False

    def _pen_marker_straight(self, event=None):
        """画笔/记号笔当前笔划是否按直线模式绘制：多段开关开启，或按住 Ctrl / Alt 任意一个。

        修饰键同时取事件自带状态与全局键盘状态：只依赖 event.modifiers() 时，
        若按下瞬间键盘状态尚未同步（或画布未持有焦点）会漏判成自由手绘。
        """
        modifiers = QApplication.keyboardModifiers()
        if event is not None:
            modifiers |= event.modifiers()
        ctrl_or_alt = bool(modifiers & (Qt.ControlModifier | Qt.AltModifier))
        return ctrl_or_alt or self._chain_tool() or self.chain_active

    def _straight_preview_path(self, end):
        """由已提交段与当前橡皮筋段拼出直线折线的预览路径。"""
        path = QPainterPath()
        for start, stop in self.committed_segments:
            path.moveTo(start)
            path.lineTo(stop)
        if self.start is not None and end is not None:
            path.moveTo(self.start)
            path.lineTo(end)
        return path

    @staticmethod
    def _smooth_freehand_path(path):
        """把自由手绘的折线（moveTo + 一连串 lineTo）转成经过各点中点的二次贝塞尔曲线，
        消除手绘毛刺；点数不足则原样返回。"""
        points = []
        for i in range(path.elementCount()):
            element = path.elementAt(i)
            if element.type in (QPainterPath.ElementType.MoveToElement,
                                QPainterPath.ElementType.LineToElement):
                points.append(QPointF(element.x, element.y))
        if len(points) < 3:
            return path
        # 去除过于密集的重复点，减少噪声。
        cleaned = [points[0]]
        for point in points[1:]:
            if (point - cleaned[-1]).manhattanLength() >= 1:
                cleaned.append(point)
        if len(cleaned) < 3:
            return path
        smoothed = QPainterPath()
        smoothed.moveTo(cleaned[0])
        for index in range(1, len(cleaned) - 1):
            current = cleaned[index]
            nxt = cleaned[index + 1]
            mid = QPointF((current.x() + nxt.x()) / 2, (current.y() + nxt.y()) / 2)
            smoothed.quadTo(current, mid)
        smoothed.lineTo(cleaned[-1])
        return smoothed

    def _update_alignment_guides(self):
        """拖动选中标注时，检测其边缘/中心与其它标注或画布边缘的对齐，绘制虚线参考并吸附。"""
        self.alignment_guides = []
        selected = self.scene_data.selectedItems()
        if not selected:
            return
        # 以所有选中项的整体包围盒参与对齐，保持相对位置不变。
        group = QRectF()
        for item in selected:
            group = group.united(item.sceneBoundingRect())
        scene = self.sceneRect()
        threshold = 6
        candidate_x = [scene.left(), scene.center().x(), scene.right()]
        candidate_y = [scene.top(), scene.center().y(), scene.bottom()]
        for item in self.scene_data.items():
            if item is self.base or item in selected:
                continue
            rect = item.sceneBoundingRect()
            candidate_x.extend([rect.left(), rect.center().x(), rect.right()])
            candidate_y.extend([rect.top(), rect.center().y(), rect.bottom()])
        best_x = None
        snap_x = None
        for edge in (group.left(), group.center().x(), group.right()):
            for candidate in candidate_x:
                delta = candidate - edge
                if abs(delta) <= threshold and (best_x is None or abs(delta) < abs(best_x)):
                    best_x = delta
                    snap_x = candidate
        best_y = None
        snap_y = None
        for edge in (group.top(), group.center().y(), group.bottom()):
            for candidate in candidate_y:
                delta = candidate - edge
                if abs(delta) <= threshold and (best_y is None or abs(delta) < abs(best_y)):
                    best_y = delta
                    snap_y = candidate
        offset = QPointF(best_x or 0, best_y or 0)
        if offset.x() == 0 and offset.y() == 0:
            return
        for item in selected:
            item.setPos(item.pos() + offset)
        if snap_x is not None:
            self.alignment_guides.append(
                QLineF(snap_x, scene.top(), snap_x, scene.bottom()))
        if snap_y is not None:
            self.alignment_guides.append(
                QLineF(scene.left(), snap_y, scene.right(), snap_y))
        self.viewport().update()

    def _finish_chain(self):
        """结束多段绘制：保留已画图形，复位连续绘制状态。"""
        if not self.chain_active:
            return
        self.chain_active = False
        self.start = None
        self.preview_end = None
        self.committed_segments = []
        self.straight_drawing = False
        self.drawing = None
        self.viewport().update()

    def set_zoom(self, percent):
        percent = min(800, max(1, int(percent)))
        if percent == self.zoom_percent:
            return
        self.zoom_percent = percent
        self.setTransform(QTransform.fromScale(percent / 100, percent / 100))
        self.zoom_changed.emit(percent)

    def refresh_image(self):
        """图片尺寸变化后同步更新场景范围，避免旋转后的坐标错位。"""
        self.base.setPixmap(QPixmap.fromImage(to_qimage(self.image)))
        self.scene_data.setSceneRect(0, 0, self.image.width, self.image.height)

    def set_round_corner_preview(self, enabled, radius=None):
        self.round_corner_preview = bool(enabled)
        if radius is not None:
            self.corner_radius = radius
        self.viewport().update()

    def image_point(self, point):
        bounds = self.sceneRect()
        return QPointF(min(max(point.x(), bounds.left()), bounds.right()),
                       min(max(point.y(), bounds.top()), bounds.bottom()))

    def constrain_item(self, item):
        bounds = item.sceneBoundingRect()
        image = self.sceneRect()
        if bounds.width() > image.width() or bounds.height() > image.height():
            factor = min(image.width() / max(bounds.width(), 1),
                         image.height() / max(bounds.height(), 1))
            item.setScale(item.scale() * factor)
            bounds = item.sceneBoundingRect()
        offset = QPointF(min(max(bounds.left(), image.left()), image.right() - bounds.width()) - bounds.left(),
                         min(max(bounds.top(), image.top()), image.bottom() - bounds.height()) - bounds.top())
        item.setPos(item.pos() + offset)

    def annotations(self):
        """返回标注图元（不含底图与擦除层），用于层级管理、选中与历史记录。"""
        return [item for item in self.scene_data.items()
                if item is not self.base and not isinstance(item, EraseMaskItem)]

    def annotation_at(self, point):
        """返回场景点处最上层的标注图元，没有则返回 None。

        命中测试跳过底图与擦除层：擦除层（`EraseMaskItem`）z 更高且覆盖在标注之上，
        直接用它会导致被擦过的标注命中到擦除层，从而无法选中、编辑或删除。
        """
        layer = self.annotations()
        for item in self.scene_data.items(point, Qt.IntersectsItemShape,
                                          Qt.DescendingOrder, self.transform()):
            if item in layer:
                return item
        return None

    def erase_items(self):
        """按 z 顺序返回擦除层图元。"""
        return sorted((item for item in self.scene_data.items()
                       if isinstance(item, EraseMaskItem)), key=lambda item: item.zValue())

    def _erase_item_covers(self, item, point):
        """擦除层是否在该场景点留下过笔迹（遮罩像素 alpha>0 即为已擦除）。"""
        local = item.mapFromScene(point)
        x, y = int(local.x()), int(local.y())
        return (0 <= x < item.mask.width() and 0 <= y < item.mask.height()
                and QColor.fromRgba(item.mask.pixel(x, y)).alpha() > 0)

    def erased_at(self, point):
        """该场景点是否被擦除过（用于在被擦区域提供删除擦除层的入口）。"""
        return any(self._erase_item_covers(item, point) for item in self.erase_items())

    def erase_layer(self, action, point=None):
        """删除擦除内容：其下方标注恢复显示，不影响其后画的标注。

        `erase_one` 删除最近一次擦除（最后一次拖动形成的笔迹，而不是整层）；
        `erase_at` 删除经过指定点的那几次擦除；`erase_clear` 删除全部擦除层。
        同一次拖动写入同一组笔迹，因此「最近一次/此处」只删对应笔迹，不会清空全部擦除。
        删除会记入历史，可随时撤销恢复。返回受影响（删除）的擦除组数与层数。
        """
        items = self.erase_items()
        if not items:
            return 0
        if action == "erase_clear":
            for item in items:
                self.scene_data.removeItem(item)
            removed = len(items)
        else:
            if action == "erase_at":
                groups = self._groups_at(point)
            elif action == "erase_one":
                latest = max((group for item in items for group in item.group_ids()),
                             default=None)
                groups = {latest} if latest is not None else set()
            else:
                raise ValueError(f"未知擦除层操作: {action}")
            results = [item.drop_groups(groups) for item in items]
            if not any(results):
                return 0
            # 不再包含任何笔迹的擦除层已无意义，直接移除。
            for item in list(self.erase_items()):
                if not item.strokes:
                    self.scene_data.removeItem(item)
            removed = len(groups)
        self.erase_mask_dirty = bool(self.erase_items())
        self.viewport().update()
        self.checkpoint()
        return removed

    def _groups_at(self, point):
        """返回“笔迹经过该场景点”的擦除分组编号集合。

        点先换算到各擦除层自身坐标，图片旋转/翻转后判定同样准确。
        """
        groups = set()
        for item in self.erase_items():
            groups |= item.groups_near(item.mapFromScene(point))
        return groups

    def erase_base_at(self, point):
        """该点处的擦除是否已标记「同时擦除原图」（相关分组全为真才算）。"""
        groups = self._groups_at(point)
        if not groups:
            return False
        flags = set()
        for item in self.erase_items():
            flags |= item.base_flags(groups)
        return flags == {True}

    def set_erase_base(self, point, erase_base):
        """把该点处的擦除改为（不）同时擦除原图，逐笔生效，不影响其它擦除。

        返回被改动的擦除分组数；改动记入历史，可撤销。
        """
        groups = self._groups_at(point)
        if not groups:
            return 0
        results = [item.set_groups_erase_base(groups, erase_base)
                   for item in self.erase_items()]
        if not any(results):
            return 0
        self.erase_mask_dirty = bool(self.erase_items())
        self.viewport().update()
        self.checkpoint()
        return len(groups)

    def _layer_items(self):
        """标注层全部图元：普通标注 + 擦除层（不含底图）。"""
        return self.annotations() + self.erase_items()

    def _next_annotation_z(self):
        """新标注的 z：始终高于当前标注层所有图元，使其位于既有擦除之上。"""
        levels = [item.zValue() for item in self._layer_items()]
        return (max(levels) + 1.0) if levels else 1.0

    def _add_annotation(self, item):
        """把新标注加入场景并赋予递增 z（保证后画的位于既有擦除之上）。"""
        item.setZValue(self._next_annotation_z())
        self.scene_data.addItem(item)
        return item

    def snapshot(self):
        """以可重建的属性记录图元，供撤销历史使用。"""
        # 使用图元的深拷贝代价较高；记录图元类型与属性以支持撤销/重做。
        records = []
        for item in sorted(self._layer_items(), key=lambda entry: entry.zValue()):
            if isinstance(item, EraseMaskItem):
                rect = item.boundingRect()
                records.append({"type": "EraseMask",
                                "pos": (item.pos().x(), item.pos().y()),
                                "z": item.zValue(),
                                "transform": QTransform(item.transform()),
                                "origin": (item.transformOriginPoint().x(),
                                           item.transformOriginPoint().y()),
                                "mask": QImage(item.mask),
                                "strokes": [tuple(stroke) for stroke in item.strokes],
                                "size": (rect.width(), rect.height())})
                continue
            kind = ("RoundedRectItem" if isinstance(item, RoundedRectItem) else
                    "QGraphicsRectItem" if isinstance(item, AnnotationRectItem) else
                    "QGraphicsEllipseItem" if isinstance(item, AnnotationEllipseItem) else
                    "QGraphicsPathItem" if isinstance(item, AnnotationPathItem) else
                    "QGraphicsTextItem" if isinstance(item, AnnotationTextItem) else
                    "QGraphicsPixmapItem" if isinstance(item, AnnotationPixmapItem) else
                    "AnnotationSequenceItem" if isinstance(item, AnnotationSequenceItem) else
                    type(item).__name__)
            data = {"type": kind, "pos": (item.pos().x(), item.pos().y()),
                "scale": item.scale(), "z": item.zValue(), "transform": QTransform(item.transform()),
                "rotation": item.rotation(),
                "origin": (item.transformOriginPoint().x(), item.transformOriginPoint().y())}
            if hasattr(item, "corner_radius"):
                data["corner_radius"] = item.corner_radius
            if hasattr(item, "pen"):
                data["pen"] = QPen(item.pen())
            if hasattr(item, "brush"):
                data["brush"] = item.brush()
            if data["type"] in ("QGraphicsRectItem", "RoundedRectItem", "QGraphicsEllipseItem"):
                rect = item.rect()
                data["rect"] = (rect.x(), rect.y(), rect.width(), rect.height())
                if data["type"] == "RoundedRectItem":
                    data["corner_radius"] = item.corner_radius
            elif data["type"] == "QGraphicsPathItem":
                data["path"] = QPainterPath(item.path())
                if getattr(item, "arrow_style", None) is not None:
                    data["arrow_style"] = item.arrow_style
                    data["start"] = item.start
                    data["end"] = item.end
                    data["line_color"] = item.line_color
                    data["line_width"] = item.line_width
            elif data["type"] == "QGraphicsTextItem":
                data.update(text=item.toHtml(), font=item.font(),
                            text_width=item.document().textWidth(),
                            text_height=getattr(item, "fixed_height", 0.0),
                            color=item.defaultTextColor().name(),
                            background=item.background_color)
                bold, italic, underline, strike = read_text_format(item)
                data.update(bold=bold, italic=italic, underline=underline, strike=strike)
            elif data["type"] == "QGraphicsPixmapItem":
                data["pixmap"] = QPixmap(item.pixmap())
                data["offset"] = (item.offset().x(), item.offset().y())
            elif data["type"] == "AnnotationSequenceItem":
                data["number"] = item.number
                data["sequence_fill_color"] = item.sequence_fill_color
                data["sequence_text_color"] = item.sequence_text_color
                data["sequence_font_size"] = item.sequence_font_size
                data["sequence_shape"] = item.sequence_shape
                data["sequence_font_family"] = item.sequence_font_family
            records.append(data)
        return records

    def next_sequence_number(self):
        """序号标注的下一个编号：从 sequence_start 起取最小未被占用的整数。

        删除中间序号后由 renumber_sequence_items 把剩余项重排为连续编号，
        因此这里取到的通常是 start + 现有序号项数；min-unused 仅作兜底，
        保证任何状态下都不会出现重复编号。
        """
        start = self.settings.get("sequence_start", 1)
        used = {item.number for item in self.annotations()
                if isinstance(item, AnnotationSequenceItem)}
        number = start
        while number in used:
            number += 1
        return number

    def renumber_sequence_items(self):
        """删除/变动后把现有序号标注按当前大小顺序重新连续编号（从 sequence_start 起）。

        无论删除的是中间单个还是连续多个（如删掉 2/3/4/5），剩余项都会被重排为
        1/2/3…，不留空缺；相对顺序保持不变。已是最优编号的项不会被改写，避免无谓重绘。
        """
        start = self.settings.get("sequence_start", 1)
        items = sorted(
            (item for item in self.annotations()
             if isinstance(item, AnnotationSequenceItem)),
            key=lambda item: item.number)
        for index, item in enumerate(items):
            new_number = start + index
            if item.number != new_number:
                item.number = new_number
                item.update()

    def restore(self, records):
        """清除现有标注，再按快照重建类型、样式和图层。"""
        for item in self._layer_items():
            self.scene_data.removeItem(item)
        for data in records:
            kind = data["type"]
            if kind == "EraseMask":
                width, height = data["size"]
                mask_item = EraseMaskItem(QImage(data["mask"]), width, height,
                                          data.get("strokes", []))
                mask_item.setPos(QPointF(*data["pos"]))
                mask_item.setTransformOriginPoint(QPointF(*data.get("origin", (0, 0))))
                mask_item.setTransform(data.get("transform", QTransform()))
                mask_item.setZValue(data["z"])
                self.scene_data.addItem(mask_item)
                continue
            if kind == "RoundedRectItem":
                item = RoundedRectItem(QRectF(*data["rect"]), data.get("corner_radius", 0))
            elif kind == "QGraphicsRectItem":
                item = AnnotationRectItem(QRectF(*data["rect"]))
            elif kind == "QGraphicsEllipseItem":
                item = AnnotationEllipseItem(QRectF(*data["rect"]))
            elif kind == "QGraphicsPathItem":
                item = AnnotationPathItem(data["path"])
                if "arrow_style" in data:
                    item.start = data["start"]
                    item.end = data["end"]
                    item.arrow_style = data["arrow_style"]
                    item.line_color = data["line_color"]
                    item.line_width = data["line_width"]
            elif kind == "QGraphicsTextItem":
                item = AnnotationTextItem()
                item.setHtml(data["text"])
                item.setFont(data["font"])
                item.document().setTextWidth(data["text_width"])
                if data.get("text_height"):
                    item.set_text_height(data["text_height"])
                item.setDefaultTextColor(QColor(data["color"]))
                item.background_color = data.get("background")
                apply_text_format(item, data.get("bold", False), data.get("italic", False),
                                 data.get("underline", False), data.get("strike", False))
            elif kind == "AnnotationSequenceItem":
                item = AnnotationSequenceItem(
                    data["number"],
                    data.get("sequence_fill_color", "#ff0000"),
                    data.get("sequence_text_color", "#ffffff"),
                    data.get("sequence_font_size", 14),
                    data.get("sequence_shape", "circle"),
                    data.get("sequence_font_family", ""))
            else:
                item = AnnotationPixmapItem(data["pixmap"])
                item.setOffset(QPointF(*data.get("offset", (0, 0))))
            if "pen" in data:
                item.setPen(QPen(data["pen"]))
            if "brush" in data and hasattr(item, "setBrush"):
                item.setBrush(data["brush"])
            editable(item)
            item.setPos(QPointF(*data["pos"]))
            item.setTransformOriginPoint(QPointF(*data.get("origin", (0, 0))))
            item.setScale(data["scale"])
            item.setTransform(data.get("transform", QTransform()))
            item.setRotation(data.get("rotation", 0))
            item.setZValue(data["z"])
            self.scene_data.addItem(item)
        self.changed.emit()

    def checkpoint(self):
        """编辑后截断重做分支，记录两版图片、光标状态和全部标注。"""
        for item in self.annotations():
            self.constrain_item(item)
        self.history = self.history[:self.cursor_index + 1]
        self.history.append((self.image, self.alternate, self.cursor_enabled,
                             self.snapshot()))
        self.cursor_index += 1
        self.erase_mask_dirty = bool(self.erase_items())
        self.changed.emit()

    def restore_checkpoint(self):
        """从历史节点恢复底图与标注，通知工具栏同步光标开关。"""
        image, alternate, cursor_enabled, records = self.history[self.cursor_index]
        if self.image is not image:
            self.image = image
            self.refresh_image()
        self.alternate = alternate
        self.cursor_enabled = cursor_enabled
        self.restore(records)
        # 撤销/重做后擦除层随快照一并重建，这里据实际存在的擦除层刷新标志。
        self.erase_mask_dirty = bool(self.erase_items())

    def reset_history(self):
        """重建初始历史（清空擦除层），用于选区重置或外部重置。"""
        self.erase_mask_dirty = False
        self.history = [(self.image, self.alternate, self.cursor_enabled, [])]
        self.cursor_index = 0

    def toggle_cursor(self, enabled):
        """只交换本次截图的两个版本，不修改全局捕获设置。"""
        if self.alternate is None or self.cursor_enabled == enabled:
            return
        self.image, self.alternate = self.alternate, self.image
        self.cursor_enabled = enabled
        self.refresh_image()
        self.checkpoint()

    def undo(self):
        """回退到上一份图片及标注快照。"""
        if self.cursor_index:
            self.cursor_index -= 1
            self.restore_checkpoint()

    def redo(self):
        """重放被撤销的快照，不生成新的历史节点。"""
        if self.cursor_index + 1 < len(self.history):
            self.cursor_index += 1
            self.restore_checkpoint()

    def reset(self):
        """还原最初捕获的两版图片并清空标注，重置动作本身可撤销。"""
        self.image = self.original.copy()
        self.alternate = self.original_alternate.copy() if self.original_alternate else None
        self.cursor_enabled = self.settings["cursor"]
        self.refresh_image()
        self.restore([])
        self.erase_mask_dirty = False
        self.checkpoint()

    def remove_selected(self):
        """删除选中的标注并记录撤销历史。"""
        selected = self.scene_data.selectedItems()
        if not selected:
            return
        for item in selected:
            self.scene_data.removeItem(item)
        self.renumber_sequence_items()
        self._on_selection_changed()
        self.checkpoint()
        logging.getLogger("screensnap").debug(
            "标注删除完成: 删除=%d, 剩余=%d, 选中类型=%s",
            len(selected), len(self.annotations()), self.selected_annotation_tool() or "无")

    def set_selected_width(self, width):
        """对所选矢量标注重新描边，保持历史记录可撤销。"""
        selected = [item for item in self.scene_data.selectedItems() if hasattr(item, "pen")]
        for item in selected:
            pen = item.pen()
            pen.setWidth(max(12, width * 5) if pen.color().alpha() < 255 else width)
            item.setPen(pen)
        if selected:
            self.checkpoint()

    def set_selected_line_style(self, style):
        """对所选矩形或椭圆切换实线/虚线线型。"""
        selected = [item for item in self.scene_data.selectedItems()
                if isinstance(item, (QGraphicsRectItem, QGraphicsEllipseItem))]
        for item in selected:
            pen = item.pen()
            pen.setStyle(Qt.DashLine if style == "dash" else Qt.SolidLine)
            item.setPen(pen)
        if selected:
            self.checkpoint()

    def set_selected_arrow_style(self, style):
        """对已选中的箭头切换线型（实心/空心/双头/直线/矩形箭杆等）。"""
        selected = [item for item in self.scene_data.selectedItems()
                   if getattr(item, "arrow_style", None) is not None]
        for item in selected:
            rebuilt = shape("arrow", item.start, item.end, item.line_color,
                            item.line_width, arrow_style=style)
            item.setPath(rebuilt.path())
            item.setPen(rebuilt.pen())
            item.setBrush(rebuilt.brush())
            item.arrow_style = style
        if selected:
            self.checkpoint()

    def set_selected_text_format(self):
        """把当前文字格式开关应用到选中的文字标注（整篇生效）。"""
        changed = False
        for item in self.scene_data.selectedItems():
            if isinstance(item, QGraphicsTextItem):
                apply_text_format(item, self.settings.get("text_bold", False),
                                 self.settings.get("text_italic", False),
                                 self.settings.get("text_underline", False),
                                 self.settings.get("text_strikethrough", False))
                changed = True
        if changed:
            self.checkpoint()

    def set_selected_color(self, color):
        """当前选中的线条或文字使用新颜色，后续新标注也使用该颜色。"""
        selected = self.scene_data.selectedItems()
        for item in selected:
            if hasattr(item, "pen"):
                pen = item.pen()
                replacement = QColor(color)
                replacement.setAlpha(pen.color().alpha())
                pen.setColor(replacement)
                item.setPen(pen)
                if isinstance(item, (QGraphicsRectItem, QGraphicsEllipseItem)) and item.brush().style() != Qt.NoBrush:
                    fill = QColor(color)
                    fill.setAlpha(item.brush().color().alpha())
                    item.setBrush(fill)
            elif isinstance(item, QGraphicsTextItem):
                item.setDefaultTextColor(QColor(color))
        if selected:
            self.checkpoint()

    def set_selected_fill(self, tool, enabled, opacity, color=None):
        """实时更新选中矩形或椭圆的填充、颜色与透明度。"""
        item_type = QGraphicsRectItem if tool == "rect" else QGraphicsEllipseItem
        selected = [item for item in self.scene_data.selectedItems()
                    if isinstance(item, item_type)]
        for item in selected:
            if not enabled:
                item.setBrush(Qt.NoBrush)
                continue
            # 未指定填充色时沿用线条颜色，保持与新建时一致。
            fill = QColor(color or item.pen().color())
            fill.setAlpha(round(255 * max(0, min(100, opacity)) / 100))
            item.setBrush(fill)
        if selected:
            self.checkpoint()

    def set_selected_font(self, family):
        """字体选择器既设置默认字体，也更新已选中的文字。"""
        selected = [item for item in self.scene_data.selectedItems() if isinstance(item, QGraphicsTextItem)]
        for item in selected:
            font = item.font()
            font.setFamily(family or "Microsoft YaHei")
            item.setFont(font)
        if selected:
            self.checkpoint()

    def set_selected_font_size(self, size):
        selected = [item for item in self.scene_data.selectedItems()
                    if isinstance(item, QGraphicsTextItem)]
        for item in selected:
            font = item.font()
            font.setPointSize(size)
            item.setFont(font)
        if selected:
            self.checkpoint()

    def _text_box_width(self, item, text, width=None):
        """按配置计算文本框宽度：正数为固定宽度，0 表示按内容自动（按字体实测，与 text_item 一致）。"""
        value = int(self.settings.get("text_width", 0) if width is None else width)
        return float(value) if value > 0 else auto_text_width(text, item.font())

    def set_selected_text_width(self, width):
        """把文本框宽度应用到选中的文字标注（0 表示按内容自动换行）。"""
        selected = [item for item in self.scene_data.selectedItems()
                    if isinstance(item, QGraphicsTextItem)]
        for item in selected:
            item.document().setTextWidth(
                self._text_box_width(item, item.toPlainText(), width))
        if selected:
            self.checkpoint()

    def set_selected_text_height(self, height):
        """把文本框高度应用到选中的文字标注（0 表示按内容自动）。"""
        selected = [item for item in self.scene_data.selectedItems()
                    if isinstance(item, QGraphicsTextItem)]
        for item in selected:
            if hasattr(item, "set_text_height"):
                item.set_text_height(int(height) if int(height) > 0 else 0)
        if selected:
            self.checkpoint()

    def selected_annotation_tool(self):
        """返回当前选中的单个标注对应的工具类型，多选/未选/不可识别时返回空串。"""
        selected = self.scene_data.selectedItems()
        if len(selected) != 1:
            return ""
        item = selected[0]
        if isinstance(item, (AnnotationRectItem, RoundedRectItem)):
            return "rect"
        if isinstance(item, AnnotationEllipseItem):
            return "ellipse"
        if isinstance(item, (AnnotationTextItem, QGraphicsTextItem)):
            return "text"
        if isinstance(item, AnnotationSequenceItem):
            return "number"
        if isinstance(item, AnnotationPathItem):
            return "arrow" if getattr(item, "arrow_style", None) is not None else "pen"
        return ""

    def _on_selection_changed(self):
        """选中项变化后通知工具栏，使其「更多设置」展示对应类型参数。"""
        self.selected_annotation_changed.emit(self.selected_annotation_tool())

    def transform_annotations(self, records, operation, degrees, old_size):
        """从原始快照重建图元，再按底图变换同步移动和旋转标注。"""
        self.restore(records)
        old_width, old_height = old_size
        new_width, new_height = self.image.size
        if operation == "horizontal":
            linear = QTransform(-1, 0, 0, 1, 0, 0)
            offset = QPointF(old_width, 0)
        elif operation == "vertical":
            linear = QTransform(1, 0, 0, -1, 0, 0)
            offset = QPointF(0, old_height)
        else:
            angle = {"left": -90, "right": 90, "half": 180}.get(operation, degrees)
            radians = math.radians(angle)
            cosine, sine = math.cos(radians), math.sin(radians)
            linear = QTransform(cosine, sine, -sine, cosine, 0, 0)
            old_center = QPointF(old_width / 2, old_height / 2)
            offset = QPointF(new_width / 2, new_height / 2) - linear.map(old_center)
        # 擦除层与标注一起变换，旋转/翻转后擦除位置仍与画面一致。
        for item in self._layer_items():
            item.setPos(linear.map(item.pos()) + offset)
            item.setTransform(linear * item.transform())

    def layer(self, action):
        """按层级命令调整当前选中标注的前后顺序。"""
        items = self.annotations()
        for item in self.scene_data.selectedItems():
            levels = [other.zValue() for other in items]
            item.setZValue({"top": max(levels) + 1, "bottom": min(levels) - 1,
                            "up": item.zValue() + 1, "down": item.zValue() - 1}[action])
        self.checkpoint()

    def _base_erase_mask(self):
        """按「逐笔是否擦原图」的标记合并出需要镂空底图的遮罩。

        底图位于全部标注层之下，不受擦除层级影响；只绘制带“同时擦除原图”标记的笔迹，
        因此之后切换该开关不会改变已有擦除的效果，逐层按其场景变换绘制也保证对齐。
        """
        items = [(item, item.base_strokes()) for item in self.erase_items()]
        items = [(item, strokes) for item, strokes in items if strokes]
        if not items:
            return None
        union = QImage(self.image.width, self.image.height, QImage.Format_ARGB32)
        union.fill(Qt.transparent)
        painter = QPainter(union)
        painter.setRenderHint(QPainter.Antialiasing)
        for item, strokes in items:
            painter.save()
            painter.setTransform(item.sceneTransform(), True)
            for stroke in strokes:
                item.paint_stroke(painter, stroke)
            painter.restore()
        painter.end()
        return union

    def render_image(self):
        """仅渲染场景中的底图和标注；视图前景的缩放手柄不会导出。

        非破坏性橡皮擦：擦除以带 z 序的 EraseMaskItem 存在于标注层内，渲染时按 z
        镂空其下方（更早绘制）的标注并露出底图；由 eraser_erase_base 决定是否也擦底图。
        """
        width, height = self.image.width, self.image.height
        output = QImage(width, height, QImage.Format_ARGB32)
        output.fill(Qt.transparent)
        painter = QPainter(output)
        painter.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
        painter.drawImage(0, 0, to_qimage(self.image))
        painter.end()
        # 单独渲染标注层（含擦除层按 z 镂空），不破坏底图与矢量标注。
        layer = QImage(width, height, QImage.Format_ARGB32)
        layer.fill(Qt.transparent)
        layer_painter = QPainter(layer)
        layer_painter.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
        self.base.hide()
        self.scene_data.render(layer_painter, QRectF(layer.rect()), self.scene_data.sceneRect())
        self.base.show()
        layer_painter.end()
        base_mask = self._base_erase_mask()
        if base_mask is not None:
            base_painter = QPainter(output)
            base_painter.setCompositionMode(QPainter.CompositionMode_DestinationOut)
            base_painter.drawImage(0, 0, base_mask)
            base_painter.end()
        compositor = QPainter(output)
        compositor.drawImage(0, 0, layer)
        compositor.end()
        return output

    def stroke_pen(self):
        if self.tool == "crop":
            return QPen(QColor(self.crop_color), self.crop_width, Qt.SolidLine,
                        Qt.RoundCap, Qt.RoundJoin)
        color = QColor(self.settings.get(f"{self.tool}_color", self.settings["pen_color"]))
        width = self.tool_width()
        line_style = Qt.DashLine if self.tool in ("rect", "ellipse") and \
            self.settings.get(f"{self.tool}_style", "solid") == "dash" else Qt.SolidLine
        if self.tool == "marker":
            # 百分比只作用于荧光笔，普通画笔保持不透明；预览与提交共用同一画笔。
            color.setAlpha(round(self.settings.get("marker_opacity", 38) * 255 / 100))
            width = max(12, width * 5)
        return QPen(color, width, line_style, Qt.RoundCap, Qt.RoundJoin)

    def tool_color(self, tool=None):
        tool = tool or self.tool
        return self.settings.get(f"{tool}_color", self.settings.get("pen_color", "#ff0000"))

    def tool_width(self):
        return self.settings.get(TOOL_WIDTH_KEYS.get(self.tool, "pen_width"),
                                 self.settings["pen_width"])

    def _erase_stroke_item(self):
        """返回本次擦除笔迹应写入的擦除层。

        擦除层带 z 序：若已有擦除层仍位于全部标注之上（其后没再画新标注），
        则把笔迹并入该层；否则新开一层，使“擦除之后新画的标注”位于擦除之上。
        """
        existing = self.erase_items()
        if existing:
            annotation_levels = [item.zValue() for item in self.annotations()]
            top_erase = existing[-1]
            if not annotation_levels or top_erase.zValue() > max(annotation_levels):
                return top_erase
        levels = [item.zValue() for item in self._layer_items()]
        mask = QImage(self.image.width, self.image.height, QImage.Format_ARGB32)
        mask.fill(Qt.transparent)
        item = EraseMaskItem(mask, self.image.width, self.image.height)
        item.setZValue((max(levels) + 0.5) if levels else 0.5)
        self.scene_data.addItem(item)
        return item

    def erase_segment(self, start, end):
        """把擦除笔迹绘制进当前擦除层（默认只作用于其下方标注，露出底图）。

        笔迹按“第几次擦除”分组记录，便于之后按「最近一次 / 经过某点」精确删除。
        """
        item = self._erase_stroke_item()
        if not self.erasing:
            # 非拖动路径（直接调用）每次自成一次擦除。
            self.eraser_group += 1
        # 笔迹按擦除层自身坐标记录：遮罩、底图镂空与按点删除用的都是同一套几何，
        # 图片旋转/翻转（擦除层随之变换）后也保持一致。
        item.add_stroke(self.eraser_group, item.mapFromScene(start), item.mapFromScene(end),
                        self.settings.get("eraser_width", self.settings["pen_width"]),
                        self.settings.get("eraser_erase_base", False))
        self.erase_mask_dirty = True
        self.viewport().update()

    def _erase_live_active(self):
        """实时视图是否需要按擦除层合成（存在擦除层时）。"""
        return bool(self.erase_items())

    def drawItems(self, painter, items, options):
        """擦除激活时跳过默认绘制，改由 drawForeground 合成底图与镂空标注层，

        使非破坏性橡皮擦在实时视图中可见；其余情况下保持原生矢量绘制。
        """
        if self._erase_live_active():
            return
        super().drawItems(painter, items, options)

    def _paint_erased_composite(self, painter):
        """仅渲染可见区域：先铺背景，再画底图（按开关决定是否擦底图），最后画被遮罩镂空的标注层。"""
        vp = self.viewport().rect()
        if vp.isEmpty():
            return
        visible = self.mapToScene(vp).boundingRect().intersected(self.sceneRect())
        if visible.isEmpty():
            return
        x0 = max(0, int(math.floor(visible.x())))
        y0 = max(0, int(math.floor(visible.y())))
        x1 = max(x0 + 1, int(math.ceil(visible.x() + visible.width())))
        y1 = max(y0 + 1, int(math.ceil(visible.y() + visible.height())))
        w = x1 - x0
        h = y1 - y0
        if w <= 0 or h <= 0:
            return
        # Qt6 的 QGraphicsView 不再调用 drawItems，场景仍会在底层绘制底图与标注；
        # 先铺一层视图背景覆盖，擦除底图留下的透明洞才不会透出底部旧图元。
        background = self.backgroundBrush()
        if background.style() == Qt.NoBrush:
            background = self.palette().base()
        painter.fillRect(QRectF(visible), background)
        # 底图层：取可见区域的底图像素，开启 eraser_erase_base 时一并镂空。
        base_img = self.base.pixmap().toImage().convertToFormat(QImage.Format_ARGB32)
        if not base_img.isNull():
            bw = min(base_img.width() - x0, w)
            bh = min(base_img.height() - y0, h)
            if bw > 0 and bh > 0:
                base_sub = base_img.copy(x0, y0, bw, bh)
                base_mask = self._base_erase_mask()
                if base_mask is not None:
                    mp = QPainter(base_sub)
                    mp.setCompositionMode(QPainter.CompositionMode_DestinationOut)
                    mp.drawImage(-x0, -y0, base_mask)
                    mp.end()
                painter.drawImage(visible.topLeft(), base_sub)
        # 标注层：擦除层已按 z 序镂空其下方内容。
        ann = QImage(w, h, QImage.Format_ARGB32)
        ann.fill(Qt.transparent)
        ap = QPainter(ann)
        ap.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
        self.base.hide()
        self.scene_data.render(ap, QRectF(0, 0, w, h), QRectF(x0, y0, w, h))
        self.base.show()
        ap.end()
        painter.drawImage(visible.topLeft(), ann)

    def drawForeground(self, painter, rect):
        """只在视图预览正在绘制的标注和缩放手柄，导出不包含这些辅助线。"""
        # 非破坏性橡皮擦的实时镂空效果（绘制在选区框、手柄等辅助线之下）。
        if self._erase_live_active():
            self._paint_erased_composite(painter)
        if self.round_corner_preview and self.corner_radius > 0:
            bounds = self.sceneRect()
            radius = min(self.corner_radius, bounds.width() / 2, bounds.height() / 2)
            rounded = QPainterPath()
            rounded.addRoundedRect(bounds, radius, radius)
            corners = QPainterPath()
            corners.setFillRule(Qt.OddEvenFill)
            corners.addRect(bounds)
            corners.addPath(rounded)
            painter.save()
            painter.setRenderHint(QPainter.Antialiasing, True)
            painter.setBrushOrigin(0, 0)
            painter.fillPath(corners, self.corner_preview_brush)
            painter.restore()
        width = self.settings.get("editor_border_width", 1)
        bounds = self.sceneRect().adjusted(width / 2, width / 2, -width / 2, -width / 2)
        if not bounds.isEmpty():
            painter.save()
            painter.setPen(QPen(QColor(self.settings.get("editor_border_color", "#000000")), width))
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(bounds)
            painter.restore()
        if self.tool == "select":
            area = (QRectF(self.selection_start, self.selection_end).normalized()
                    if self.selection_start is not None else self.selection_area)
            if area is not None and not area.isEmpty():
                painter.setPen(QPen(QColor("#00ad91"), 1, Qt.DashLine))
                painter.fillRect(area, QColor(0, 173, 145, 35))
                painter.drawRect(area)
        if self.tool == "eraser" and self.eraser_point is not None:
            painter.setPen(QPen(QColor("#00ad91"), 1, Qt.DashLine))
            painter.setBrush(Qt.NoBrush)
            radius = self.settings.get("eraser_width", self.settings["pen_width"]) / 2
            painter.drawEllipse(self.eraser_point, radius, radius)
        # 马赛克涂抹模式：同样用虚线圆环表示笔刷宽度，便于对齐涂抹范围。
        if self.tool == "mosaic" and self.mosaic_point is not None \
                and self.settings.get("mosaic_brush", False):
            painter.setPen(QPen(QColor("#00ad91"), 1, Qt.DashLine))
            painter.setBrush(Qt.NoBrush)
            radius = max(2, self.settings.get("mosaic_width", 20) / 2)
            painter.drawEllipse(self.mosaic_point, radius, radius)
        if self.start is not None and self.preview_end is not None:
            painter.setPen(QPen(QColor("#00ad91"), 1, Qt.DashLine) if self.tool == "mosaic"
                           else self.stroke_pen())
            area = QRectF(self.start, self.preview_end).normalized()
            if self.tool in ("rect", "crop", "mosaic"):
                if self.tool == "crop":
                    fill = QColor(self.crop_color)
                    fill.setAlpha(50)
                    painter.fillRect(area, fill)
                elif self.tool == "mosaic":
                    painter.fillRect(area, QColor(0, 173, 145, 50))
                painter.drawRect(area)
            elif self.tool == "ellipse":
                painter.drawEllipse(area)
            elif self.tool in ("pen", "marker") and self.drawing is not None:
                painter.drawPath(self.drawing)
            elif self.tool == "arrow":
                arrow = shape("arrow", self.start, self.preview_end,
                              self.tool_color("arrow"), self.tool_width(),
                              self.settings.get("arrow_style", "filled"))
                painter.setBrush(arrow.brush())
                painter.drawPath(arrow.path())
        if self.mosaic_drawing is not None:
            painter.setPen(QPen(QColor("#00ad91"), 1, Qt.DashLine))
            painter.setBrush(Qt.NoBrush)
            painter.drawPath(self.mosaic_drawing)
        if self.tool != "select":
            return
        selected = self.scene_data.selectedItems()
        painter.setPen(QPen(QColor("#00ad91"), 1))
        painter.setBrush(Qt.NoBrush)
        for item in selected:
            # 选中描边：旋转项绘制按局部包围盒变换后的实际轮廓，未旋转项等价矩形。
            rect = item.boundingRect()
            corners = [rect.topLeft(), rect.topRight(), rect.bottomRight(), rect.bottomLeft()]
            outline = QPolygonF([item.mapToScene(corner) for corner in corners])
            painter.drawPolygon(outline)
            # 缩放手柄随项旋转，旋转后依然可用（与旋转手柄共存）。
            for handle in self.item_resize_handles(item).values():
                handle_rect = QRectF(handle.x() - 4, handle.y() - 4, 8, 8)
                painter.fillRect(handle_rect, QColor("white"))
                painter.drawRect(handle_rect)
        # 单选时在其上方显示旋转手柄（绕中心旋转），手柄杆自顶边中点引出。
        if len(selected) == 1:
            item = selected[0]
            rect = item.sceneBoundingRect()
            top = QPointF(rect.center().x(), rect.top())
            handle = self.rotation_handle_position(item)
            painter.drawLine(top, handle)
            painter.setBrush(QColor("white"))
            painter.drawEllipse(handle, 5, 5)
            painter.setBrush(Qt.NoBrush)
        # 拖动选中标注时显示对齐参考虚线（导出不含）。
        if self.alignment_guides:
            painter.save()
            painter.setPen(QPen(QColor("#ff5fa2"), 1, Qt.DashLine))
            painter.setBrush(Qt.NoBrush)
            for line in self.alignment_guides:
                painter.drawLine(line)
            painter.restore()

    @staticmethod
    def resize_handles(bounds):
        center = bounds.center()
        return {
            "nw": bounds.topLeft(), "n": QPointF(center.x(), bounds.top()),
            "ne": bounds.topRight(), "e": QPointF(bounds.right(), center.y()),
            "se": bounds.bottomRight(), "s": QPointF(center.x(), bounds.bottom()),
            "sw": bounds.bottomLeft(), "w": QPointF(bounds.left(), center.y()),
        }

    def item_resize_handles(self, item):
        """按项局部包围盒取 8 个控制点的场景坐标；旋转时随项一起转，手柄始终贴在标注上。"""
        rect = item.boundingRect()
        center = rect.center()
        local = {
            "nw": rect.topLeft(), "n": QPointF(center.x(), rect.top()),
            "ne": rect.topRight(), "e": QPointF(rect.right(), center.y()),
            "se": rect.bottomRight(), "s": QPointF(center.x(), rect.bottom()),
            "sw": rect.bottomLeft(), "w": QPointF(rect.left(), center.y()),
        }
        return {name: item.mapToScene(point) for name, point in local.items()}

    ROTATION_HANDLE_DISTANCE = 24

    def rotation_handle_position(self, item):
        """单选标注上方、固定在场景包围盒顶边之上的旋转手柄（不压住缩放手柄）。"""
        rect = item.sceneBoundingRect()
        return QPointF(rect.center().x(), rect.top() - self.ROTATION_HANDLE_DISTANCE)

    def rotation_handle_at(self, item, point):
        """指针是否落在旋转手柄 8 像素（场景单位）内。"""
        handle = self.rotation_handle_position(item)
        return ((handle.x() - point.x()) ** 2 + (handle.y() - point.y()) ** 2) <= 64

    def _begin_rotation(self, item, point):
        """开始旋转：以项包围盒中心为支点，记录起始角度与当前旋转值。"""
        self.rotating = item
        center_local = item.boundingRect().center()
        item.setTransformOriginPoint(center_local)
        center = item.mapToScene(center_local)
        self.rotation_center = center
        self.rotation_start_angle = math.atan2(point.y() - center.y(), point.x() - center.x())
        self.rotation_start_value = item.rotation()
        QToolTip.hideText()

    def _rotate_to(self, point, snap=False):
        """根据指针相对支点的角度增量更新旋转，snap=True 时吸附 15°。"""
        angle = math.atan2(point.y() - self.rotation_center.y(),
                           point.x() - self.rotation_center.x())
        delta = math.degrees(angle - self.rotation_start_angle)
        new_value = self.rotation_start_value + delta
        if snap:
            new_value = round(new_value / 15) * 15
        self.rotating.setRotation(new_value)
        self.viewport().update()

    def _commit_mosaic_brush(self, path):
        """沿笔迹路径生成马赛克/模糊覆盖：取底图对应区域，按效果生成后用笔迹形状裁掉外部像素。"""
        radius = max(2, self.settings.get("mosaic_width", 20) / 2)
        stroker = QPainterPathStroker()
        stroker.setWidth(radius * 2)
        stroker.setCapStyle(Qt.RoundCap)
        stroker.setJoinStyle(Qt.RoundJoin)
        region = stroker.createStroke(path)
        bounds = region.boundingRect().toRect().intersected(self.sceneRect().toRect())
        if bounds.isEmpty():
            return
        sample = self.image.crop((bounds.left(), bounds.top(), bounds.right() + 1, bounds.bottom() + 1))
        mode = self.settings.get("mosaic_mode", "blocks")
        size = self.settings.get("mosaic_size", 10)
        result = mosaic_image(sample, mode, size).convert("RGBA")
        # 在区域尺寸的图像上用白色绘制笔迹作为透明度遮罩。
        mask = QImage(bounds.width(), bounds.height(), QImage.Format_ARGB32)
        mask.fill(Qt.transparent)
        mp = QPainter(mask)
        mp.setRenderHint(QPainter.Antialiasing)
        mp.setTransform(QTransform().translate(-bounds.left(), -bounds.top()))
        mp.setPen(Qt.NoPen)
        mp.setBrush(QColor("white"))
        mp.drawPath(region)
        mp.end()
        out = QImage(bounds.width(), bounds.height(), QImage.Format_ARGB32)
        out.fill(Qt.transparent)
        op = QPainter(out)
        op.setRenderHint(QPainter.Antialiasing)
        op.drawImage(0, 0, to_qimage(result))
        op.end()
        out.setAlphaChannel(mask)
        item = editable(AnnotationPixmapItem(QPixmap.fromImage(out)))
        item.setOffset(QPointF(bounds.left(), bounds.top()))
        self._add_annotation(item)
        self.checkpoint()

    def mousePressEvent(self, event):
        """根据工具决定选中图元、取色或开始新的标注。"""
        if event.button() == Qt.RightButton and self.chain_active and self._chain_tool():
            # 多段绘制进行中，右键直接结束连续绘制并保留已画图形。
            self._finish_chain()
            event.accept()
            return
        if event.button() in (Qt.RightButton, Qt.MiddleButton):
            # 右键和中键使用自定义抓手平移，不改变当前标注工具。
            self.right_pan_start = event.position().toPoint()
            self.right_pan_cursor = QCursor(self.cursor())
            self.pan_button = event.button()
            self.right_pan_moved = False
            self.setCursor(Qt.ClosedHandCursor)
            event.accept()
            return
        point = self.mapToScene(event.position().toPoint())
        if self.tool == "select" and event.button() == Qt.LeftButton:
            if not self.sceneRect().contains(point):
                event.accept()
                return
            self.selection_area = None
            self.rotating = None
            self.viewport().update()
            selected = self.scene_data.selectedItems()
            # 先判定缩放手柄（旋转项的手柄随项转动），再判定旋转手柄，避免误触。
            for item in selected:
                handles = self.item_resize_handles(item)
                handle = self.resize_handle_at(handles, point)
                if handle is not None:
                    anchor_name = {"nw": "se", "n": "s", "ne": "sw", "e": "w",
                                   "se": "nw", "s": "n", "sw": "ne", "w": "e"}[handle]
                    anchor_scene = handles[anchor_name]
                    anchor_local = item.mapFromScene(anchor_scene)
                    original_scene_anchor = item.mapToScene(anchor_local)
                    self.resize_transform = item.transform()
                    self.resize_scale = item.scale()
                    self.resize_origin = item.transformOriginPoint()
                    self.resize_position = item.pos()
                    item.setTransformOriginPoint(anchor_local)
                    item.setPos(item.pos() + original_scene_anchor - item.mapToScene(anchor_local))
                    self.resizing = item
                    self.resize_handle = handle
                    self.resize_anchor = anchor_scene
                    self.resize_anchor_local = anchor_local
                    self.resize_start = handles[handle]
                    # 文字宽度以按下时的实际宽度与位置为基准，首帧再捕获。
                    self.text_resize_origin = None
                    QToolTip.hideText()
                    event.accept()
                    return
            # 单选且不在缩放手柄上时，尝试命中旋转手柄。
            if len(selected) == 1 and self.rotation_handle_at(selected[0], point):
                self._begin_rotation(selected[0], point)
                event.accept()
                return
            if self.annotation_at(point) is None:
                # 从底图空白处（含已被擦除的区域）拖动只画辅助选区，松开后保留框线并选中其中的标注。
                self.scene_data.clearSelection()
                self.selection_start = point
                self.selection_end = point
                event.accept()
                return
        if self.tool == "eraser":
            if event.button() == Qt.LeftButton:
                self.erasing = True
                self.eraser_last = point
                self.eraser_point = point
                self.eraser_group += 1          # 本次拖动自成一组擦除笔迹
                self.erase_segment(point, point)
                self.viewport().update()
            return
        if self.tool == "picker":
            x, y = int(point.x()), int(point.y())
            if 0 <= x < self.image.width and 0 <= y < self.image.height:
                self.color_picked.emit("#%02x%02x%02x" % self.image.convert("RGB").getpixel((x, y)))
            return
        if self.tool not in ("select", "hand") and not self.sceneRect().contains(point):
            event.accept()
            return
        if self.tool == "text":
            text, _changed, ok = self.input_text("文字标注")
            if ok and text:
                self._add_annotation(
                    text_item(point, text, self.settings, self.text_alignment))
                self.checkpoint()
            return
        if self.tool == "number":
            number = self.next_sequence_number()
            item = AnnotationSequenceItem(
                number,
                self.settings.get("sequence_fill_color", "#ff0000"),
                self.settings.get("sequence_text_color", "#ffffff"),
                self.settings.get("sequence_font_size", 14),
                self.settings.get("sequence_shape", "circle"),
                self.settings.get("font", ""), point)
            self._add_annotation(item)
            self.checkpoint()
            return
        if self.tool == "mosaic" and self.settings.get("mosaic_brush", False) \
                and event.button() == Qt.LeftButton:
            # 自由笔刷：记录笔迹路径，松开时沿笔迹生成马赛克/模糊覆盖。
            self.mosaic_drawing = QPainterPath(point)
            self.mosaic_point = point
            self.preview_end = point
            self.viewport().update()
            event.accept()
            return
        if self.tool not in ("select", "hand") and event.button() == Qt.LeftButton:
            # 多段绘制进行中：保留上一终点作为本段起点，不从按下点重置。
            if not (self.chain_active and self._chain_tool() and self.start is not None):
                self.start = point
            self.preview_end = point
            if self.tool in ("pen", "marker"):
                self.straight_drawing = self._pen_marker_straight(event)
                if self.straight_drawing:
                    self.drawing = QPainterPath()
                    if not self.chain_active:
                        self.committed_segments = []
                else:
                    self.drawing = QPainterPath(self.start)
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        """绘制期间只更新预览，松开鼠标后才提交到场景。"""
        if self.right_pan_start is not None:
            point = event.position().toPoint()
            distance = point - self.right_pan_start
            if distance.manhattanLength() > 3:
                self.right_pan_moved = True
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - distance.x())
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - distance.y())
            self.right_pan_start = point
            event.accept()
            return
        if self.rotating is not None:
            point = self.mapToScene(event.position().toPoint())
            self._rotate_to(point, snap=bool(event.modifiers() & Qt.ShiftModifier))
            event.accept()
            return
        if self.selection_start is not None:
            self.selection_end = self.image_point(self.mapToScene(event.position().toPoint()))
            self.viewport().update()
            event.accept()
            return
        if self.tool == "eraser":
            point = self.image_point(self.mapToScene(event.position().toPoint()))
            self.eraser_point = point
            if self.erasing:
                self.erase_segment(self.eraser_last, point)
                self.eraser_last = point
            self.viewport().update()
            return
        if self.tool == "mosaic" and self.settings.get("mosaic_brush", False):
            # 未按住时也跟随指针，用虚线圆环显示笔刷宽度与位置。
            point = self.image_point(self.mapToScene(event.position().toPoint()))
            self.mosaic_point = point
            if self.mosaic_drawing is not None:
                self.mosaic_drawing.lineTo(point)
                self.preview_end = point
            self.viewport().update()
            return
        if self.resizing is not None:
            point = self.image_point(self.mapToScene(event.position().toPoint()))
            handle = self.resize_handle
            # 旋转项：把场景位移换算到项自身未旋转的坐标轴上，拖动手感才与视觉一致。
            angle = math.radians(-self.resizing.rotation())
            cos_a, sin_a = math.cos(angle), math.sin(angle)

            def to_local(vector):
                return QPointF(vector.x() * cos_a - vector.y() * sin_a,
                               vector.x() * sin_a + vector.y() * cos_a)

            start_local = to_local(self.resize_start - self.resize_anchor)
            now_local = to_local(point - self.resize_anchor)
            # 文字标注：边中点（n/s/e/w）拖动改为直接调整文本框宽度、文字随之重排；
            # 四角仍是等比/自由缩放。仅文字走此分支，其它标注类型行为不变。
            if len(handle) == 1 and isinstance(self.resizing, QGraphicsTextItem):
                self._resize_text_box(self.resizing, handle, point)
                self.constrain_item(self.resizing)
                self.viewport().update()
                event.accept()
                return
            scale_x, scale_y = 1.0, 1.0
            if ("w" in handle or "e" in handle) and abs(start_local.x()) > 1e-6:
                scale_x = self.clamp_resize_ratio(now_local.x() / start_local.x())
            if ("n" in handle or "s" in handle) and abs(start_local.y()) > 1e-6:
                scale_y = self.clamp_resize_ratio(now_local.y() / start_local.y())
            # 所有把手默认等比缩放（保持宽高比）；按住 Ctrl/Alt/Shift/Space 任意其一则自由拉伸变形。
            if not self._free_distortion(event):
                driver = scale_x if abs(scale_x - 1) >= abs(scale_y - 1) else scale_y
                scale_x = scale_y = driver
            self.resizing.setTransform(self.resize_transform)
            self.resizing.setScale(self.resize_scale)
            self.resizing.setTransform(QTransform().scale(scale_x, scale_y), True)
            current_anchor = self.resizing.mapToScene(self.resize_anchor_local)
            self.resizing.setPos(self.resizing.pos() + self.resize_anchor - current_anchor)
            self.constrain_item(self.resizing)
            self.viewport().update()
            event.accept()
            return
        if self.start is not None:
            end = self.image_point(self.mapToScene(event.position().toPoint()))
            self.preview_end = end
            if self.tool in ("pen", "marker"):
                # 修饰键可能在鼠标按下之后才按住，拖动途中也能切换为直线模式。
                if not self.straight_drawing and self._pen_marker_straight(event):
                    self.straight_drawing = True
                    self.committed_segments = []
                if self.straight_drawing:
                    self.drawing = self._straight_preview_path(end)
                else:
                    self.drawing.lineTo(end)
            self.viewport().update()
        if self.start is None:
            super().mouseMoveEvent(event)
            if self.tool == "select":
                for item in self.scene_data.selectedItems():
                    self.constrain_item(item)
                self._update_alignment_guides()
                self._update_resize_cursor(event.position().toPoint())

    def _free_distortion(self, event=None):
        """四角是否允许自由拉伸：按住 Ctrl/Alt/Shift 或 Space 任意其一。

        与直线判定同理，修饰键同时取事件自带状态与全局键盘状态，
        避免按下瞬间键盘状态未同步时误判成「等比」，导致按了键也不生效。
        """
        modifiers = QApplication.keyboardModifiers()
        if event is not None:
            modifiers |= event.modifiers()
        return bool(modifiers & (Qt.ControlModifier | Qt.AltModifier | Qt.ShiftModifier)) \
            or self.space_pressed

    def _resize_text_box(self, item, handle, point):
        """文字标注的边中点拖动：横向（e/w）调整文本框宽度、纵向（n/s）调整高度。

        尺寸以「按下时的尺寸」为基准加上累计本地位移，避免逐帧叠加导致的跳跃；
        本地位移按标注自身的旋转/缩放换算，因此缩放或旋转后手感仍然一致。
        拖左边（w）/上边（n）时同步平移标注，使对侧边保持不动。
        尺寸上限取画布尺寸，避免无限放大后被整体缩回；四角仍是整体缩放。
        """
        if self.text_resize_origin is None:
            # 首帧捕获：按下后（锚点已就位）的文本框尺寸与实际位置。
            self.text_resize_origin = (
                float(item.document().textWidth()),
                float(item.boundingRect().height()),
                float(getattr(item, "fixed_height", 0.0)),
                QPointF(item.pos()))
        origin_width, origin_height, origin_fixed, origin_pos = self.text_resize_origin
        inverse, invertible = item.sceneTransform().inverted()
        if not invertible:
            inverse = QTransform()
        scene_dx = point.x() - self.resize_start.x()
        scene_dy = point.y() - self.resize_start.y()
        # 只取线性部分把场景位移换算到标注自身坐标（忽略平移），避免位置变化影响位移。
        local_dx = inverse.m11() * scene_dx + inverse.m21() * scene_dy
        local_dy = inverse.m12() * scene_dx + inverse.m22() * scene_dy
        image = self.sceneRect()
        if handle in ("e", "w"):
            limit = max(MIN_TEXT_WIDTH, math.hypot(inverse.m11(), inverse.m12()) * image.width())
            width = origin_width + (local_dx if handle == "e" else -local_dx)
            width = max(MIN_TEXT_WIDTH, min(width, limit))
            item.document().setTextWidth(width)
            if handle == "w":
                # 保持右边缘不动：从按下位置按本地 x 方向平移 (origin_width - width)。
                item.setPos(origin_pos + self._local_vector(item, origin_width - width, 0))
            else:
                item.setPos(origin_pos)
            return
        # n / s：调整文本框高度（固定高度，超出内容被裁剪）；拖上边时下边缘保持不动。
        limit = max(MIN_TEXT_HEIGHT, math.hypot(inverse.m21(), inverse.m22()) * image.height())
        base_height = origin_fixed if origin_fixed > 0 else origin_height
        height = base_height + (local_dy if handle == "s" else -local_dy)
        height = max(MIN_TEXT_HEIGHT, min(height, limit))
        if hasattr(item, "set_text_height"):
            item.set_text_height(height)
        if handle == "n":
            item.setPos(origin_pos + self._local_vector(item, 0, base_height - height))
        else:
            item.setPos(origin_pos)

    def _local_vector(self, item, dx, dy):
        """把标注自身坐标的位移换算为场景位移（含旋转/缩放，忽略平移）。"""
        transform = item.sceneTransform()
        base = transform.map(QPointF(0, 0))
        return transform.map(QPointF(dx, dy)) - base

    def _update_resize_cursor(self, position):
        point = self.mapToScene(position)
        if not self.sceneRect().contains(point):
            self.unsetCursor()
            QToolTip.hideText()
            return
        for item in self.scene_data.selectedItems() if self.tool == "select" else ():
            handle = self.resize_handle_at(self.item_resize_handles(item), point)
            cursors = {"nw": Qt.SizeFDiagCursor, "se": Qt.SizeFDiagCursor,
                       "ne": Qt.SizeBDiagCursor, "sw": Qt.SizeBDiagCursor,
                       "n": Qt.SizeVerCursor, "s": Qt.SizeVerCursor,
                       "e": Qt.SizeHorCursor, "w": Qt.SizeHorCursor}
            if handle:
                if len(handle) == 1 and isinstance(item, QGraphicsTextItem):
                    # 文字标注：左右边中点调整文本框宽度、上下边中点调整高度。
                    if handle in ("e", "w"):
                        self.setCursor(Qt.SizeHorCursor)
                        QToolTip.showText(
                            self.viewport().mapToGlobal(position),
                            "拖动左右边中点调整文本框宽度（文字自动换行）；四角仍是缩放",
                            self)
                    else:
                        self.setCursor(Qt.SizeVerCursor)
                        QToolTip.showText(
                            self.viewport().mapToGlobal(position),
                            "拖动上下边中点调整文本框高度（超出部分裁剪）；四角仍是缩放",
                            self)
                else:
                    self.setCursor(cursors[handle])
                    QToolTip.showText(
                        self.viewport().mapToGlobal(position),
                        "拖动控制点等比缩放；按住 Ctrl / Alt / Shift / Space 任意键可自由拉伸变形",
                        self)
                return
            if item.contains(item.mapFromScene(point)):
                self.setCursor(Qt.SizeAllCursor)
                QToolTip.hideText()
                return
        self.unsetCursor()
        QToolTip.hideText()

    @staticmethod
    def resize_handle_at(bounds, point):
        """返回距指针 8 像素内最近的边角控制点；bounds 可为 QRectF 或已算好的手柄字典。"""
        candidates = (AnnotationCanvas.resize_handles(bounds)
                      if isinstance(bounds, QRectF) else bounds)
        nearest = min(candidates, key=lambda name:
                      (candidates[name].x() - point.x()) ** 2 +
                      (candidates[name].y() - point.y()) ** 2)
        handle = candidates[nearest]
        return nearest if ((handle.x() - point.x()) ** 2 +
                           (handle.y() - point.y()) ** 2) <= 64 else None

    @staticmethod
    def clamp_resize_ratio(ratio):
        """允许跨过锚点翻转方向，但避免零尺寸和过度放大。"""
        sign = -1 if ratio < 0 else 1
        return sign * min(10.0, max(0.02, abs(ratio)))

    def leaveEvent(self, event):
        self.eraser_point = None
        self.mosaic_point = None
        self.viewport().update()
        super().leaveEvent(event)

    def contextMenuEvent(self, event):
        # 菜单在无拖动的右键松开时显示，避免与抓手平移冲突。
        event.accept()

    def input_text(self, title, initial="", values=None, persist=True):
        """弹出文字输入对话框。

        `values` 非空表示编辑某个已有标注：以该标注当前样式为初值，改动只作用于它；
        `persist=False` 时不回写公共配置。返回 (文字, 改动项, 是否确认)。
        """
        from editor.text_input_dialog import TextInputDialog

        dialog = TextInputDialog(self, title, self.settings, initial, values=values)
        if dialog.exec() != QDialog.Accepted:
            return None, {}, False
        changed = dialog.changed_settings()
        if persist:
            for key, value in changed.items():
                self.settings[key] = value
                if key == "text_alignment":
                    self.text_alignment = {"left": Qt.AlignLeft, "center": Qt.AlignHCenter,
                                           "right": Qt.AlignRight}[value]
                self.setting_changed.emit(key, value)
        return dialog.text(), changed, True

    def _text_values_from_item(self, item):
        """读取某个文字标注当前的样式，作为「编辑该标注」对话框的初值。"""
        font = item.font()
        bold, italic, underline, strike = read_text_format(item)
        alignment = item.document().defaultTextOption().alignment()
        if alignment & Qt.AlignHCenter:
            alignment_key = "center"
        elif alignment & Qt.AlignRight:
            alignment_key = "right"
        else:
            alignment_key = "left"
        return {
            "font": font.family(),
            "font_size": (font.pointSize() if font.pointSize() > 0
                          else self.settings.get("font_size", 18)),
            "text_alignment": alignment_key,
            "text_color": item.defaultTextColor().name(),
            "text_bold": bold, "text_italic": italic,
            "text_underline": underline, "text_strikethrough": strike,
            "text_background_enabled": bool(item.background_color),
            "text_background": item.background_color or self.settings.get("text_background", "#fff3a0"),
            "text_width": int(round(item.document().textWidth())),
            "text_height": int(round(getattr(item, "fixed_height", 0.0))),
        }

    def _apply_text_values(self, item, values, text):
        """把一组文字样式完整应用到某个标注（编辑对话框的改动只落在这一个标注上）。"""
        item.document().setTextWidth(
            self._text_box_width(item, text, values.get("text_width", 0)))
        if hasattr(item, "set_text_height"):
            height = int(values.get("text_height", 0) or 0)
            item.set_text_height(height if height > 0 else 0)
        font = item.font()
        font.setFamily(values.get("font") or "Microsoft YaHei")
        font.setPointSize(int(values.get("font_size", 18)))
        item.setFont(font)
        item.setDefaultTextColor(QColor(values.get("text_color")
                                        or self.settings.get("text_color", "#ff0000")))
        item.background_color = (values.get("text_background")
                                 if values.get("text_background_enabled") else None)
        apply_text_format(item, bool(values.get("text_bold")), bool(values.get("text_italic")),
                          bool(values.get("text_underline")), bool(values.get("text_strikethrough")))
        alignment = {"left": Qt.AlignLeft, "center": Qt.AlignHCenter,
                     "right": Qt.AlignRight}.get(values.get("text_alignment"), Qt.AlignLeft)
        option = item.document().defaultTextOption()
        option.setAlignment(alignment)
        item.document().setDefaultTextOption(option)
        cursor = QTextCursor(item.document())
        cursor.select(QTextCursor.Document)
        block = QTextBlockFormat()
        block.setAlignment(alignment)
        cursor.mergeBlockFormat(block)

    def edit_text_item(self, item):
        """编辑已有文字标注：对话框各项只作用于该标注，不回写公共配置。"""
        values = self._text_values_from_item(item)
        original_text = item.toPlainText()
        text, changed, ok = self.input_text("修改文字标注", original_text,
                                            values=values, persist=False)
        if not ok:
            return
        if not text:
            self.scene_data.removeItem(item)
            self.checkpoint()
            return
        text_changed = text != original_text
        if text_changed:
            original_format = item.document().firstBlock().blockFormat()
            item.setPlainText(text)
            cursor = QTextCursor(item.document())
            cursor.select(QTextCursor.Document)
            cursor.mergeBlockFormat(original_format)
        # 「该标注原值 + 本次改动」整体应用，确保各项只影响这一个标注。
        merged = dict(values)
        merged.update(changed)
        self._apply_text_values(item, merged, text)
        if text_changed or changed:
            self.checkpoint()

    def annotation_menu(self, item):
        menu = QMenu(self)
        edit_action = menu.addAction("编辑文字") if isinstance(item, QGraphicsTextItem) else None
        delete_action = menu.addAction("删除标注")
        delete_action.triggered.connect(lambda: (item.setSelected(True), self.remove_selected()))
        if edit_action is not None:
            edit_action.triggered.connect(lambda: self.edit_text_item(item))
        menu.aboutToHide.connect(menu.deleteLater)
        return menu

    def show_annotation_menu(self, item, position):
        if self.tool != "select":
            self.set_tool("select")
            self.setDragMode(QGraphicsView.RubberBandDrag)
            self.selection_requested.emit()
        self.scene_data.clearSelection()
        item.setSelected(True)
        self.viewport().update()
        self.annotation_menu(item).popup(position)

    def erase_menu(self, point):
        """被擦除区域（该点没有标注）的右键菜单：删除擦除，或单独设置是否擦掉原图。"""
        menu = QMenu(self)
        menu.addAction("删除此处擦除").triggered.connect(
            lambda: self.erase_layer("erase_at", point))
        menu.addAction("删除最近一次擦除").triggered.connect(
            lambda: self.erase_layer("erase_one"))
        menu.addAction("清除全部擦除").triggered.connect(
            lambda: self.erase_layer("erase_clear"))
        # 逐笔设置：只改这一处擦除；工具栏/设置里的开关只决定之后新擦除的默认值。
        base_action = menu.addAction("此处同时擦除原图")
        base_action.setCheckable(True)
        base_action.setChecked(self.erase_base_at(point))
        base_action.setToolTip("只改这一处擦除是否擦掉原图，不影响其它已有擦除")
        base_action.toggled.connect(lambda flag: self.set_erase_base(point, flag))
        menu.aboutToHide.connect(menu.deleteLater)
        return menu

    def show_erase_menu(self, point, position):
        """在画布上直接删除某处擦除，无需回退其后的标注。"""
        if self.tool != "select":
            self.set_tool("select")
            self.setDragMode(QGraphicsView.RubberBandDrag)
            self.selection_requested.emit()
        self.erase_menu(point).popup(position)

    def mouseReleaseEvent(self, event):
        """按当前工具提交图元；裁剪时两版截图使用相同区域。"""
        if self.rotating is not None:
            self.rotating = None
            self.checkpoint()
            self.viewport().update()
            event.accept()
            return
        if self.mosaic_drawing is not None:
            self._commit_mosaic_brush(self.mosaic_drawing)
            self.mosaic_drawing = None
            self.preview_end = None
            self.viewport().update()
            event.accept()
            return
        if self.alignment_guides:
            self.alignment_guides = []
            self.viewport().update()
        if event.button() in (Qt.RightButton, Qt.MiddleButton):
            if self.right_pan_start is not None:
                if event.button() == self.pan_button:
                    show_menu = event.button() == Qt.RightButton and not self.right_pan_moved
                    self.right_pan_start = None
                    # 只恢复进入平移前的光标，原标注工具与未完成笔划保持不变。
                    self.setCursor(self.right_pan_cursor)
                    self.right_pan_cursor = None
                    self.pan_button = None
                    self._update_resize_cursor(event.position().toPoint())
                    if show_menu:
                        # 命中标注而非擦除层，保证被擦过的标注右键仍可编辑/删除。
                        point = self.mapToScene(event.position().toPoint())
                        item = self.annotation_at(point)
                        if item is not None:
                            self.show_annotation_menu(item, event.globalPosition().toPoint())
                        elif self.erased_at(point):
                            # 擦除区域：直接提供删除擦除层的入口，不必为撤回一次擦除回退后续编辑。
                            self.show_erase_menu(point, event.globalPosition().toPoint())
            event.accept()
            return
        if self.selection_start is not None and event.button() == Qt.LeftButton:
            end = self.mapToScene(event.position().toPoint())
            area = QRectF(self.selection_start, end).normalized().intersected(self.sceneRect())
            self.selection_area = area if not area.isEmpty() else None
            self.selection_start = None
            self.selection_end = None
            if self.selection_area is not None:
                for item in self.scene_data.items(self.selection_area, Qt.IntersectsItemShape):
                    if item is not self.base:
                        item.setSelected(True)
            self.viewport().update()
            event.accept()
            return
        if self.tool == "eraser" and self.erasing:
            self.erase_segment(self.eraser_last, self.mapToScene(event.position().toPoint()))
            self.erasing = False
            self.eraser_last = None
            self.checkpoint()
            return
        if self.resizing is not None:
            self.resizing = None
            self.checkpoint()
            self._update_resize_cursor(event.position().toPoint())
            return
        if self.start is not None:
            end = self.image_point(self.mapToScene(event.position().toPoint()))
            dragged = (end - self.start).manhattanLength() >= STRAIGHT_MIN_DISTANCE
            if self.tool in ("pen", "marker") and self.straight_drawing:
                # 直线/折线模式：仅当确有第二点时提交一段直线；无第二点不绘制。
                self.preview_end = None
                if dragged:
                    seg = QPainterPath(self.start)
                    seg.lineTo(end)
                    item = AnnotationPathItem(seg)
                    item.setPen(self.stroke_pen())
                    editable(item)
                    self._add_annotation(item)
                    if self._chain_tool():
                        self.committed_segments.append((QPointF(self.start), QPointF(end)))
                        self.start = end
                        self.chain_active = True
                    else:
                        self.start = None
                        self.chain_active = False
                    self.checkpoint()
                else:
                    if self._chain_tool():
                        # 仅确立链起点，不产生零长度段，保持连续绘制状态。
                        self.start = end
                        self.chain_active = True
                    else:
                        self.start = None
                        self.chain_active = False
                self.viewport().update()
                return
            if self.tool in ("pen", "marker"):
                # 自由手绘：无拖动的孤立点不绘制。
                self.drawing.lineTo(end)
                self.preview_end = None
                if dragged:
                    item = AnnotationPathItem(self._smooth_freehand_path(self.drawing))
                    item.setPen(self.stroke_pen())
                    editable(item)
                    self._add_annotation(item)
                    self.checkpoint()
                self.start = None
                self.chain_active = False
                self.viewport().update()
                return
            self.preview_end = None
            self.viewport().update()
            if self.tool == "mosaic":
                # 将框选像素先缩小再以最近邻放大，形成方块马赛克。
                area = QRectF(self.start, end).normalized().toRect().intersected(self.sceneRect().toRect())
                if area.isEmpty():
                    self.start = None
                    return
                sample = self.image.crop((area.left(), area.top(), area.right() + 1, area.bottom() + 1))
                size = self.settings["mosaic_size"]
                mode = self.settings.get("mosaic_mode", "blocks")
                result = mosaic_image(sample, mode, size)
                item = editable(AnnotationPixmapItem(QPixmap.fromImage(to_qimage(result))))
                item.setPos(area.topLeft())
            elif self.tool == "crop":
                area = QRectF(self.start, end).normalized().toRect().intersected(self.sceneRect().toRect())
                if not area.isEmpty():
                    crop_area = (area.left(), area.top(), area.right() + 1, area.bottom() + 1)
                    self.image = self.image.crop(crop_area)
                    if self.alternate is not None:
                        self.alternate = self.alternate.crop(crop_area)
                    self.refresh_image()
                    self.restore([])
                    self.checkpoint()
                self.start = None
                return
            else:
                style = self.settings.get(f"{self.tool}_style", "solid") if self.tool in ("rect", "ellipse") else \
                    (self.settings.get("arrow_style", "filled") if self.tool == "arrow" else "filled")
                item = shape(self.tool, self.start, end,
                             self.tool_color(), self.tool_width(), style,
                             self.settings.get("rect_corner_radius", 0)
                             if self.tool == "rect" and self.settings.get("rect_corner_enabled", False)
                             else 0,
                             self.settings.get(f"{self.tool}_fill_enabled", False),
                             self.settings.get(f"{self.tool}_fill_opacity", 35),
                             self.settings.get(f"{self.tool}_fill_color"))
            self._add_annotation(item)
            if self.tool == "arrow" and self.settings.get("arrow_chain", False):
                # 多段绘制：保留上一终点作为下一段起点；按右键结束连续绘制。
                self.start = end
                self.chain_active = True
                self.preview_end = None
            else:
                self.start = None
                self.chain_active = False
            self.checkpoint()
            self.viewport().update()
            return
        super().mouseReleaseEvent(event)
        if self.tool == "select":
            self.checkpoint()

    def mouseDoubleClickEvent(self, event):
        # 其他工具先切到选择；选择工具中双击非文字标注可直接删除，双击文字标注则就地编辑。
        if event.button() != Qt.LeftButton:
            event.accept()
            return
        point = self.mapToScene(event.position().toPoint())
        # 命中标注而非擦除层：被擦过的文字/标注仍可双击编辑或删除。
        item = self.annotation_at(point)
        if item is not None:
            if self.tool == "select":
                if isinstance(item, AnnotationTextItem):
                    self.edit_text_item(item)
                else:
                    self.scene_data.clearSelection()
                    item.setSelected(True)
                    self.remove_selected()
                self._update_resize_cursor(event.position().toPoint())
                return
            self.set_tool("select")
            self.setDragMode(QGraphicsView.RubberBandDrag)
            self.selection_requested.emit()
            self.scene_data.clearSelection()
            item.setSelected(True)
            self._update_resize_cursor(event.position().toPoint())
            self.viewport().update()
            return
        self.confirmed.emit()

    def keyPressEvent(self, event):
        """处理删除、撤销/重做、临时平移和放弃编辑等画布快捷键。"""
        if event.key() == Qt.Key_Escape:
            if self.chain_active and self._chain_tool():
                self._finish_chain()
                event.accept()
                return
            self.cancelled.emit()
        elif event.key() == Qt.Key_Space:
            self.space_pressed = True
            self.setDragMode(QGraphicsView.ScrollHandDrag)
        elif event.key() in (Qt.Key_Delete, Qt.Key_Backspace):
            self.remove_selected()
        elif event.matches(QKeySequence.Undo):
            self.undo()
            event.accept()
        elif event.matches(QKeySequence.Redo):
            self.redo()
            event.accept()
        else:
            super().keyPressEvent(event)

    def keyReleaseEvent(self, event):
        """松开空格后退出临时平移状态。"""
        if event.key() == Qt.Key_Space:
            self.space_pressed = False
            self.setDragMode(QGraphicsView.RubberBandDrag if self.tool == "select"
                             else QGraphicsView.NoDrag)
        super().keyReleaseEvent(event)

    def focusOutEvent(self, event):
        # 焦点丢失时清空空格状态，避免拖拽判定残留。
        self.space_pressed = False
        super().focusOutEvent(event)

    def wheelEvent(self, event):
        """默认纵向滚动；Ctrl 或 Alt 将滚轮映射为横向移动。"""
        modifiers = event.modifiers() | QApplication.keyboardModifiers()
        horizontal_scroll = bool(modifiers & (Qt.ControlModifier | Qt.AltModifier))
        scrollbar = self.horizontalScrollBar() if horizontal_scroll else self.verticalScrollBar()
        pixel_delta = event.pixelDelta()
        delta = pixel_delta.x() if horizontal_scroll and pixel_delta.x() else pixel_delta.y()
        if not delta:
            delta = event.angleDelta().y()
        if not delta:
            event.accept()
            return
        if pixel_delta.x() or pixel_delta.y():
            movement = delta * 0.5
        else:
            movement = delta / 120 * max(1, scrollbar.singleStep()) * 1.5
        scrollbar.setValue(round(scrollbar.value() - movement))
        event.accept()