"""图片画布、标注事件和可撤销操作。"""

import math

from PIL import Image, ImageFilter
from PySide6.QtCore import Qt, QPointF, QRectF, Signal
from PySide6.QtGui import QBrush, QPainter, QPainterPath, QPen, QColor, QPixmap, QImage, QTextCursor, QTransform, QCursor
from PySide6.QtWidgets import (QApplication, QGraphicsView, QGraphicsScene, QGraphicsPixmapItem,
                               QGraphicsItem, QGraphicsRectItem, QGraphicsEllipseItem,
                               QGraphicsTextItem, QInputDialog, QMenu, QToolTip,
                               QStyleOptionGraphicsItem)

from core.screen_capture import to_qimage
from config.config_manager import TOOL_WIDTH_KEYS
from editor.annotation_items import (shape, text_item, editable, RoundedRectItem,
                                     AnnotationRectItem, AnnotationEllipseItem,
                                     AnnotationPathItem, AnnotationTextItem,
                                     AnnotationPixmapItem, AnnotationSequenceItem)


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
        self.resizing = None
        self.resize_handle = None
        self.resize_anchor = None
        self.resize_anchor_local = None
        self.resize_scale = 1
        self.resize_transform = None
        self.resize_origin = None
        self.resize_position = None
        self.resize_start = None
        # 空格同时是临时平移键；这里单独记录其按下状态，用于四角自由拉伸判定。
        self.space_pressed = False
        self.eraser_last = None
        self.eraser_point = None
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
        previous = self.tool
        self.tool = tool
        if tool == "picker":
            self.setCursor(self._picker_cursor)
        elif previous == "picker" or tool != "select":
            self.unsetCursor()

    def _finish_chain(self):
        """结束多段绘制：保留已画图形，复位连续绘制状态。"""
        if not self.chain_active:
            return
        self.chain_active = False
        self.start = None
        self.preview_end = None
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
        """返回不包含底图的图元，用于层级管理和历史记录。"""
        return [item for item in self.scene_data.items() if item is not self.base]

    def snapshot(self):
        """以可重建的属性记录图元，供撤销历史使用。"""
        # 使用图元的深拷贝代价较高；记录图元类型与属性以支持撤销/重做。
        records = []
        for item in reversed(self.annotations()):
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
            elif data["type"] == "QGraphicsTextItem":
                data.update(text=item.toHtml(), font=item.font(),
                            text_width=item.document().textWidth(),
                            color=item.defaultTextColor().name())
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
        for item in self.annotations():
            self.scene_data.removeItem(item)
        for data in records:
            kind = data["type"]
            if kind == "RoundedRectItem":
                item = RoundedRectItem(QRectF(*data["rect"]), data.get("corner_radius", 0))
            elif kind == "QGraphicsRectItem":
                item = AnnotationRectItem(QRectF(*data["rect"]))
            elif kind == "QGraphicsEllipseItem":
                item = AnnotationEllipseItem(QRectF(*data["rect"]))
            elif kind == "QGraphicsPathItem":
                item = AnnotationPathItem(data["path"])
            elif kind == "QGraphicsTextItem":
                item = AnnotationTextItem()
                item.setHtml(data["text"])
                item.setFont(data["font"])
                item.document().setTextWidth(data["text_width"])
                item.setDefaultTextColor(QColor(data["color"]))
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
            item.setZValue(data["z"])
            self.scene_data.addItem(item)
        self.changed.emit()

    def checkpoint(self):
        """编辑后截断重做分支，记录两版图片、光标状态和全部标注。"""
        for item in self.annotations():
            self.constrain_item(item)
        self.history = self.history[:self.cursor_index + 1]
        self.history.append((self.image, self.alternate, self.cursor_enabled, self.snapshot()))
        self.cursor_index += 1
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
        self.checkpoint()

    def remove_selected(self):
        """删除选中的标注并记录撤销历史。"""
        for item in self.scene_data.selectedItems():
            self.scene_data.removeItem(item)
        self.renumber_sequence_items()
        self.checkpoint()

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

    def set_selected_fill(self, tool, enabled, opacity):
        """实时更新选中矩形或椭圆的填充与透明度。"""
        item_type = QGraphicsRectItem if tool == "rect" else QGraphicsEllipseItem
        selected = [item for item in self.scene_data.selectedItems()
                    if isinstance(item, item_type)]
        for item in selected:
            if not enabled:
                item.setBrush(Qt.NoBrush)
                continue
            fill = QColor(item.pen().color())
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
        for item in self.annotations():
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

    def render_image(self):
        """仅渲染场景中的底图和标注；视图前景的缩放手柄不会导出。"""
        output = QImage(self.image.width, self.image.height, QImage.Format_ARGB32)
        output.fill(Qt.transparent)
        painter = QPainter(output)
        painter.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
        self.scene_data.render(painter, QRectF(output.rect()), self.scene_data.sceneRect())
        painter.end()
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

    def erase_segment(self, start, end):
        radius = self.settings.get("eraser_width", self.settings["pen_width"]) / 2
        area = QRectF(start, end).normalized().adjusted(-radius, -radius, radius, radius)
        for item in self.scene_data.items(area):
            if item is self.base:
                continue
            if not isinstance(item, QGraphicsPixmapItem):
                bounds = item.boundingRect().toAlignedRect()
                if bounds.isEmpty():
                    continue
                image = QImage(bounds.size(), QImage.Format_ARGB32_Premultiplied)
                image.fill(Qt.transparent)
                painter = QPainter(image)
                painter.setRenderHint(QPainter.Antialiasing)
                painter.translate(-bounds.topLeft())
                item.paint(painter, QStyleOptionGraphicsItem(), None)
                painter.end()
                replacement = editable(AnnotationPixmapItem(QPixmap.fromImage(image)))
                replacement.setOffset(bounds.topLeft())
                replacement.setPos(item.pos())
                replacement.setTransform(item.transform())
                replacement.setScale(item.scale())
                replacement.setZValue(item.zValue())
                self.scene_data.removeItem(item)
                self.scene_data.addItem(replacement)
                item = replacement
            image = item.pixmap().toImage().convertToFormat(QImage.Format_ARGB32_Premultiplied)
            painter = QPainter(image)
            painter.setRenderHint(QPainter.Antialiasing)
            painter.setCompositionMode(QPainter.CompositionMode_Clear)
            painter.setPen(QPen(QColor("#000000"), radius * 2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            local_start = item.mapFromScene(start) - item.offset()
            local_end = item.mapFromScene(end) - item.offset()
            if local_start == local_end:
                painter.setPen(Qt.NoPen)
                painter.setBrush(QColor("#000000"))
                painter.drawEllipse(local_start, radius, radius)
            else:
                painter.drawLine(local_start, local_end)
            painter.end()
            item.setPixmap(QPixmap.fromImage(image))

    def drawForeground(self, painter, rect):
        """只在视图预览正在绘制的标注和缩放手柄，导出不包含这些辅助线。"""
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
        if self.tool != "select":
            return
        painter.setPen(QPen(QColor("#00ad91"), 1))
        painter.setBrush(Qt.NoBrush)
        for item in self.scene_data.selectedItems():
            bounds = item.sceneBoundingRect()
            painter.drawRect(bounds)
            for handle in self.resize_handles(bounds).values():
                handle_rect = QRectF(handle.x() - 4, handle.y() - 4, 8, 8)
                painter.fillRect(handle_rect, QColor("white"))
                painter.drawRect(handle_rect)

    @staticmethod
    def resize_handles(bounds):
        center = bounds.center()
        return {
            "nw": bounds.topLeft(), "n": QPointF(center.x(), bounds.top()),
            "ne": bounds.topRight(), "e": QPointF(bounds.right(), center.y()),
            "se": bounds.bottomRight(), "s": QPointF(center.x(), bounds.bottom()),
            "sw": bounds.bottomLeft(), "w": QPointF(bounds.left(), center.y()),
        }

    def mousePressEvent(self, event):
        """根据工具决定选中图元、取色或开始新的标注。"""
        if event.button() == Qt.RightButton and self.chain_active and self.tool == "arrow":
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
            self.viewport().update()
            for item in self.scene_data.selectedItems():
                bounds = item.sceneBoundingRect()
                handle = self.resize_handle_at(bounds, point)
                if handle is not None:
                    handles = self.resize_handles(bounds)
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
                    QToolTip.hideText()
                    event.accept()
                    return
            if self.scene_data.itemAt(point, self.transform()) in (None, self.base):
                # 从底图空白处拖动只画辅助选区，松开后保留框线并选中其中的标注。
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
            text, ok = QInputDialog.getMultiLineText(self, "文字标注", "内容")
            if ok and text:
                self.scene_data.addItem(text_item(point, text, self.settings, self.text_alignment))
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
            self.scene_data.addItem(item)
            self.checkpoint()
            return
        if self.tool not in ("select", "hand") and event.button() == Qt.LeftButton:
            # 多段绘制进行中：保留上一终点作为本段起点，不从按下点重置。
            if not (self.chain_active and self.tool == "arrow" and self.start is not None):
                self.start = point
            self.preview_end = point
            if self.tool in ("pen", "marker"):
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
        if self.resizing is not None:
            point = self.image_point(self.mapToScene(event.position().toPoint()))
            handle = self.resize_handle
            scale_x, scale_y = 1.0, 1.0
            if "w" in handle or "e" in handle:
                start_width = self.resize_start.x() - self.resize_anchor.x()
                scale_x = self.clamp_resize_ratio((point.x() - self.resize_anchor.x()) / start_width)
            if "n" in handle or "s" in handle:
                start_height = self.resize_start.y() - self.resize_anchor.y()
                scale_y = self.clamp_resize_ratio((point.y() - self.resize_anchor.y()) / start_height)
            # 四角默认等比缩放（保持宽高比）；按住 Ctrl/Alt/Shift/Space 任意其一则自由拉伸变形。
            is_corner = ("w" in handle or "e" in handle) and ("n" in handle or "s" in handle)
            if is_corner and not self._free_distortion(event):
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
        if self.start is not None and self.tool in ("pen", "marker"):
            self.drawing.lineTo(self.image_point(self.mapToScene(event.position().toPoint())))
        if self.start is not None:
            self.preview_end = self.image_point(self.mapToScene(event.position().toPoint()))
            self.viewport().update()
        if self.start is None:
            super().mouseMoveEvent(event)
            if self.tool == "select":
                for item in self.scene_data.selectedItems():
                    self.constrain_item(item)
                self._update_resize_cursor(event.position().toPoint())

    def _free_distortion(self, event):
        """四角是否允许自由拉伸：按住 Ctrl/Alt/Shift 或 Space 任意其一。"""
        modifiers = event.modifiers()
        return bool(modifiers & (Qt.ControlModifier | Qt.AltModifier | Qt.ShiftModifier)) \
            or self.space_pressed

    def _update_resize_cursor(self, position):
        point = self.mapToScene(position)
        if not self.sceneRect().contains(point):
            self.unsetCursor()
            QToolTip.hideText()
            return
        for item in self.scene_data.selectedItems() if self.tool == "select" else ():
            bounds = item.sceneBoundingRect()
            handle = self.resize_handle_at(bounds, point)
            cursors = {"nw": Qt.SizeFDiagCursor, "se": Qt.SizeFDiagCursor,
                       "ne": Qt.SizeBDiagCursor, "sw": Qt.SizeBDiagCursor,
                       "n": Qt.SizeVerCursor, "s": Qt.SizeVerCursor,
                       "e": Qt.SizeHorCursor, "w": Qt.SizeHorCursor}
            if handle:
                self.setCursor(cursors[handle])
                if handle in ("nw", "ne", "sw", "se"):
                    QToolTip.showText(
                        self.viewport().mapToGlobal(position),
                        "拖动四角等比缩放；按住 Ctrl / Alt / Shift / Space 任意键可自由拉伸变形",
                        self)
                else:
                    QToolTip.hideText()
                return
            if item.contains(item.mapFromScene(point)):
                self.setCursor(Qt.SizeAllCursor)
                QToolTip.hideText()
                return
        self.unsetCursor()
        QToolTip.hideText()

    @staticmethod
    def resize_handle_at(bounds, point):
        """返回距指针 8 像素内最近的边角控制点。"""
        candidates = AnnotationCanvas.resize_handles(bounds)
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
        self.viewport().update()
        super().leaveEvent(event)

    def contextMenuEvent(self, event):
        # 菜单在无拖动的右键松开时显示，避免与抓手平移冲突。
        event.accept()

    def edit_text_item(self, item):
        text, ok = QInputDialog.getMultiLineText(self, "修改文字标注", "内容", item.toPlainText())
        if ok and text != item.toPlainText():
            if not text:
                self.scene_data.removeItem(item)
            else:
                original_format = item.document().firstBlock().blockFormat()
                text_width = item.document().textWidth()
                item.setPlainText(text)
                cursor = QTextCursor(item.document())
                cursor.select(QTextCursor.Document)
                cursor.mergeBlockFormat(original_format)
                item.document().setTextWidth(text_width)
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

    def mouseReleaseEvent(self, event):
        """按当前工具提交图元；裁剪时两版截图使用相同区域。"""
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
                        item = self.scene_data.itemAt(self.mapToScene(event.position().toPoint()),
                                                      self.transform())
                        if item is not None and item is not self.base:
                            self.show_annotation_menu(item, event.globalPosition().toPoint())
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
            if self.tool in ("pen", "marker"):
                self.drawing.lineTo(end)
            self.preview_end = None
            self.viewport().update()
            if self.tool in ("pen", "marker"):
                item = AnnotationPathItem(self.drawing)
                item.setPen(self.stroke_pen())
                editable(item)
            elif self.tool == "mosaic":
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
                             self.settings.get(f"{self.tool}_fill_opacity", 35))
            self.scene_data.addItem(item)
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
        # 其他工具先切到选择；选择工具中双击已有标注可直接删除。
        if event.button() != Qt.LeftButton:
            event.accept()
            return
        point = self.mapToScene(event.position().toPoint())
        item = self.scene_data.itemAt(point, self.transform())
        if item is not None and item is not self.base:
            if self.tool == "select":
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
        """处理删除、临时平移和放弃编辑等画布快捷键。"""
        if event.key() == Qt.Key_Escape:
            if self.chain_active and self.tool == "arrow":
                self._finish_chain()
                event.accept()
                return
            self.cancelled.emit()
        elif event.key() == Qt.Key_Space:
            self.space_pressed = True
            self.setDragMode(QGraphicsView.ScrollHandDrag)
        elif event.key() in (Qt.Key_Delete, Qt.Key_Backspace):
            self.remove_selected()
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