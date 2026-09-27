"""图片画布、标注事件和可撤销操作。"""

import math

from PIL import Image, ImageFilter
from PySide6.QtCore import Qt, QPointF, QRectF, Signal
from PySide6.QtGui import QPainter, QPainterPath, QPen, QColor, QPixmap, QImage, QTextCursor, QTransform, QCursor
from PySide6.QtWidgets import (QApplication, QGraphicsView, QGraphicsScene, QGraphicsPixmapItem,
                               QGraphicsItem, QGraphicsTextItem, QInputDialog, QMenu,
                               QStyleOptionGraphicsItem)

from core.screen_capture import to_qimage
from config.config_manager import TOOL_WIDTH_KEYS
from editor.annotation_items import shape, text_item, editable


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
        self.crop_color = "#00ad91"
        self.crop_width = 2
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
        self.refresh_image()
        self.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
        self.setViewportUpdateMode(QGraphicsView.FullViewportUpdate)
        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)
        self.setAlignment(Qt.AlignCenter)
        self.setDragMode(QGraphicsView.RubberBandDrag)
        self.tool = "select"
        self.text_alignment = Qt.AlignLeft
        self.start = None
        self.drawing = None
        self.preview_end = None
        self.resizing = None
        self.resize_anchor = None
        self.resize_distance = 1
        self.resize_scale = 1
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
            data = {"type": type(item).__name__, "pos": (item.pos().x(), item.pos().y()),
                "scale": item.scale(), "z": item.zValue(), "transform": QTransform(item.transform())}
            if hasattr(item, "pen"):
                data["pen"] = QPen(item.pen())
            if data["type"] in ("QGraphicsRectItem", "QGraphicsEllipseItem"):
                rect = item.rect()
                data["rect"] = (rect.x(), rect.y(), rect.width(), rect.height())
            elif data["type"] == "QGraphicsPathItem":
                data["path"] = QPainterPath(item.path())
            elif data["type"] == "QGraphicsTextItem":
                data.update(text=item.toHtml(), font=item.font(),
                            text_width=item.document().textWidth(),
                            color=item.defaultTextColor().name())
            elif data["type"] == "QGraphicsPixmapItem":
                data["pixmap"] = QPixmap(item.pixmap())
                data["offset"] = (item.offset().x(), item.offset().y())
            records.append(data)
        return records

    def restore(self, records):
        """清除现有标注，再按快照重建类型、样式和图层。"""
        for item in self.annotations():
            self.scene_data.removeItem(item)
        from PySide6.QtWidgets import QGraphicsRectItem, QGraphicsEllipseItem, QGraphicsPathItem, QGraphicsTextItem
        for data in records:
            kind = data["type"]
            if kind == "QGraphicsRectItem":
                item = QGraphicsRectItem(QRectF(*data["rect"]))
            elif kind == "QGraphicsEllipseItem":
                item = QGraphicsEllipseItem(QRectF(*data["rect"]))
            elif kind == "QGraphicsPathItem":
                item = QGraphicsPathItem(data["path"])
            elif kind == "QGraphicsTextItem":
                item = QGraphicsTextItem()
                item.setHtml(data["text"])
                item.setFont(data["font"])
                item.document().setTextWidth(data["text_width"])
                item.setDefaultTextColor(QColor(data["color"]))
            else:
                item = QGraphicsPixmapItem(data["pixmap"])
                item.setOffset(QPointF(*data.get("offset", (0, 0))))
            if "pen" in data:
                item.setPen(QPen(data["pen"]))
            editable(item)
            item.setPos(QPointF(*data["pos"]))
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
            elif isinstance(item, QGraphicsTextItem):
                item.setDefaultTextColor(QColor(color))
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
        color = QColor(self.settings["pen_color"])
        width = self.tool_width()
        if self.tool == "marker":
            # 百分比只作用于荧光笔，普通画笔保持不透明；预览与提交共用同一画笔。
            color.setAlpha(round(self.settings.get("marker_opacity", 38) * 255 / 100))
            width = max(12, width * 5)
        return QPen(color, width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)

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
                replacement = editable(QGraphicsPixmapItem(QPixmap.fromImage(image)))
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
                              self.settings["pen_color"], self.tool_width(),
                              self.settings.get("arrow_style", "filled"))
                painter.setBrush(arrow.brush())
                painter.drawPath(arrow.path())
        if self.tool != "select":
            return
        painter.setPen(QPen(QColor("#00ad91"), 1))
        painter.setBrush(QColor("white"))
        for item in self.scene_data.selectedItems():
            painter.drawRect(item.sceneBoundingRect())
            corner = item.sceneBoundingRect().bottomRight()
            painter.drawRect(QRectF(corner.x() - 4, corner.y() - 4, 8, 8))

    def mousePressEvent(self, event):
        """根据工具决定选中图元、取色或开始新的标注。"""
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
                corner = bounds.bottomRight()
                if abs(point.x() - corner.x()) <= 8 and abs(point.y() - corner.y()) <= 8:
                    anchor_local = item.mapFromScene(bounds.topLeft())
                    anchor_scene = item.mapToScene(anchor_local)
                    item.setTransformOriginPoint(anchor_local)
                    item.setPos(item.pos() + anchor_scene - item.mapToScene(anchor_local))
                    self.resizing = item
                    self.resize_anchor = bounds.topLeft()
                    self.resize_distance = max(1, ((corner.x() - bounds.left()) ** 2 +
                                                   (corner.y() - bounds.top()) ** 2) ** 0.5)
                    self.resize_scale = item.scale()
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
        if self.tool not in ("select", "hand") and event.button() == Qt.LeftButton:
            self.start = point
            self.preview_end = point
            if self.tool in ("pen", "marker"):
                self.drawing = QPainterPath(point)
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
            distance = ((point.x() - self.resize_anchor.x()) ** 2 +
                (point.y() - self.resize_anchor.y()) ** 2) ** 0.5
            self.resizing.setScale(min(10, max(0.1, self.resize_scale * distance / self.resize_distance)))
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

    def _update_resize_cursor(self, position):
        point = self.mapToScene(position)
        if not self.sceneRect().contains(point):
            self.unsetCursor()
            return
        for item in self.scene_data.selectedItems() if self.tool == "select" else ():
            corner = item.sceneBoundingRect().bottomRight()
            if abs(point.x() - corner.x()) <= 8 and abs(point.y() - corner.y()) <= 8:
                self.setCursor(Qt.SizeFDiagCursor)
                return
            if item.contains(item.mapFromScene(point)):
                self.setCursor(Qt.SizeAllCursor)
                return
        self.unsetCursor()

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
            self.tool = "select"
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
            from PySide6.QtWidgets import QGraphicsPathItem
            end = self.image_point(self.mapToScene(event.position().toPoint()))
            if self.tool in ("pen", "marker"):
                self.drawing.lineTo(end)
            self.preview_end = None
            self.viewport().update()
            if self.tool in ("pen", "marker"):
                item = QGraphicsPathItem(self.drawing)
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
                item = editable(QGraphicsPixmapItem(QPixmap.fromImage(to_qimage(result))))
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
                item = shape(self.tool, self.start, end,
                             self.settings["pen_color"], self.tool_width(),
                             self.settings.get("arrow_style", "filled")
                             if self.tool == "arrow" else "filled")
            self.scene_data.addItem(item)
            self.start = None
            self.checkpoint()
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
            self.tool = "select"
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
            self.cancelled.emit()
        elif event.key() == Qt.Key_Space:
            self.setDragMode(QGraphicsView.ScrollHandDrag)
        elif event.key() in (Qt.Key_Delete, Qt.Key_Backspace):
            self.remove_selected()
        else:
            super().keyPressEvent(event)

    def keyReleaseEvent(self, event):
        """松开空格后退出临时平移状态。"""
        if event.key() == Qt.Key_Space:
            self.setDragMode(QGraphicsView.RubberBandDrag if self.tool == "select"
                             else QGraphicsView.NoDrag)
        super().keyReleaseEvent(event)

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