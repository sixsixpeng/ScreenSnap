"""图片画布、标注事件和可撤销操作。"""

import logging
import math

from PIL import Image, ImageFilter
from PySide6.QtCore import Qt, QPointF, QRectF, QLineF, Signal, QTimer
from PySide6.QtGui import (QBrush, QPainter, QPainterPath, QPen, QColor, QPixmap, QImage,
                             QPolygonF, QPainterPathStroker, QTextBlockFormat,
                             QTextCursor, QTransform, QCursor, QKeySequence)
from PySide6.QtWidgets import (QApplication, QGraphicsView, QGraphicsScene, QGraphicsPixmapItem,
                               QGraphicsRectItem, QGraphicsEllipseItem,
                               QGraphicsTextItem, QDialog, QMenu, QToolTip)

from core.constants import checker_tile_size
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

# 文字工具单击后等多久才弹输入框（毫秒）。文字框是模态的、会吃掉双击第二拍，所以必须留出
# 一段“可能还在双击”的窗口；但系统双击间隔（Windows 默认约 400ms）体感太长，这里取更短上限：
# 比该窗口更快的双击会被判为“保存”，更慢的“双击”会先弹框、第二拍落到输入框里。
TEXT_CLICK_COMMIT_MS = 180


def shape_constraint_active(modifiers):
    return bool(modifiers & (Qt.ControlModifier | Qt.AltModifier))


def constrained_shape_endpoint(start, end):
    dx = end.x() - start.x()
    dy = end.y() - start.y()
    side = max(abs(dx), abs(dy))
    return QPointF(start.x() + (side if dx >= 0 else -side),
                   start.y() + (side if dy >= 0 else -side))

# 文字标注用边中点拖动调整文本框时的最小宽/高（像素），避免拖成不可见。
MIN_TEXT_WIDTH = 40.0
MIN_TEXT_HEIGHT = 24.0

# 绘制类工具在画布上使用的工具字形光标：位图逻辑尺寸、热点（锚点即真实落点）与配色来源。
CURSOR_BITMAP_SIZE = 32
TOOL_CURSOR_TOOLS = ("pen", "marker", "rect", "ellipse", "text", "arrow")
TOOL_CURSOR_COLOR_KEYS = {"pen": "pen_color", "marker": "marker_color",
                          "rect": "rect_color", "ellipse": "ellipse_color",
                          "text": "text_color", "arrow": "arrow_color"}
# pen/marker/arrow 热点在笔尖/箭尾，rect/ellipse 在拖拽起始角，text 在插入点。
TOOL_CURSOR_HOTSPOTS = {"pen": (5, 27), "marker": (5, 27), "arrow": (5, 27),
                        "rect": (6, 26), "ellipse": (6, 26), "text": (7, 25)}
# 白色外描边 + 深青内描边，保证深浅底色下都清晰可辨，与取色光标同一配色语言。
CURSOR_OUTLINE_LIGHT = "#f7fbfa"
CURSOR_OUTLINE_DARK = "#142c32"


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
        # 透明像素（多屏间隙、擦除镂空、圆角外）统一用棋盘格预览，样式跟随“透明图像背景”。
        self.corner_preview_brush = self.transparent_checker_brush()
        # theme 模式的棋盘格取自 QPalette.Base；记下生成笔刷时的底色，
        # 调色板变化（换主题、宿主改调色板）后要在绘制时重建。
        self._checker_palette_base = self.palette().base().color()
        self.refresh_image()
        self.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
        self.setViewportUpdateMode(QGraphicsView.FullViewportUpdate)
        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)
        self.setAlignment(Qt.AlignCenter)
        self.setDragMode(QGraphicsView.RubberBandDrag)
        self.tool = "select"
        # 文字工具的双击判定：输入框是模态的，先等一小段判定窗口再弹，双击才能改成保存。
        self._pending_text_point = None
        self._text_click_timer = QTimer(self)
        self._text_click_timer.setSingleShot(True)
        self._text_click_timer.timeout.connect(self._commit_text_click)
        # 序号工具刚落下的那一颗：双击表示保存时要把它撤掉，避免留下多余序号。
        self._last_number_click = None
        self._picker_cursor = self._create_picker_cursor()
        self._picker_pressed_cursor = self._create_picker_cursor(pressed=True)
        # 工具字形光标按 (工具, 颜色, 是否按下) 惰性构建并缓存；左键按下时切换按下态。
        self._tool_cursor_cache = {}
        self._applied_cursor_key = None
        self._left_button_down = False
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
        self.rotation_cursor = self._create_rotation_cursor()
        self.rotation_center = None
        self.rotation_start_angle = 0.0
        self.rotation_start_value = 0.0
        self.mosaic_drawing = None
        self.mosaic_preview = None
        self.mosaic_point = None
        # 擦除以带 z 序的 EraseMaskItem 存在于场景中；该标志表示当前是否有擦除层，
        # 供实时视图决定是否走合成路径。
        self.erase_mask_dirty = False
        self.resize_origin = None
        self.resize_position = None
        self.resize_start = None
        # 空格同时是临时平移键；这里单独记录其按下状态，用于四角自由拉伸判定。
        self.space_pressed = False
        self.space_pan_active = False
        self.space_pan_start = None
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
        # Ctrl+滚轮缩放只在窗口编辑器启用（原地编辑画布太小、也没有缩放控件可复位）。
        self.wheel_zoom_enabled = False

    @staticmethod
    def _create_picker_cursor(pressed=False):
        """创建标准斜置滴管光标；球泡在左上，吸头朝右下。"""
        pixmap = QPixmap(32, 32)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        body = QPainterPath()
        body.moveTo(8, 8)
        body.lineTo(11, 5)
        body.lineTo(26, 20)
        body.lineTo(23, 25)
        body.closeSubpath()
        painter.setPen(QPen(QColor(CURSOR_OUTLINE_LIGHT), 4.6,
                            Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(body)
        painter.setPen(QPen(QColor(CURSOR_OUTLINE_DARK), 1.8,
                            Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        painter.setBrush(QColor("#d8edf1"))
        painter.drawPath(body)
        painter.setPen(QPen(QColor("#71858c"), 1.1, Qt.SolidLine, Qt.RoundCap))
        painter.drawLine(QPointF(13, 9), QPointF(23, 19))
        # The rubber bulb/cap makes the implement read as a dropper, not a blade.
        cap = QPainterPath()
        cap.addRoundedRect(QRectF(3.5, 3.5, 9, 8), 3.5, 3.5)
        painter.setPen(QPen(QColor(CURSOR_OUTLINE_LIGHT), 4.0,
                            Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(cap)
        painter.setPen(QPen(QColor(CURSOR_OUTLINE_DARK), 1.7,
                            Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        painter.setBrush(QColor("#24b88a"))
        painter.drawPath(cap)
        painter.setPen(QPen(QColor("#e8fff6"), 1.0, Qt.SolidLine, Qt.RoundCap))
        painter.drawLine(QPointF(6, 5.5), QPointF(9, 5.5))
        tip = QPainterPath(QPointF(23, 23))
        tip.lineTo(29, 29)
        painter.setPen(QPen(QColor(CURSOR_OUTLINE_LIGHT), 4.0,
                            Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        painter.drawPath(tip)
        painter.setPen(QPen(QColor(CURSOR_OUTLINE_DARK), 1.8,
                            Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        painter.drawPath(tip)
        if pressed:
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(QColor(CURSOR_OUTLINE_LIGHT), 3.4))
            painter.drawEllipse(QPointF(28, 28), 3.8, 3.8)
            painter.setPen(QPen(QColor("#087a68"), 1.6))
            painter.drawEllipse(QPointF(28, 28), 3.8, 3.8)
        painter.setPen(QPen(QColor(CURSOR_OUTLINE_DARK), 1.2))
        painter.setBrush(QColor("#31a98f"))
        painter.drawEllipse(QPointF(28, 28), 1.8, 1.8)
        painter.end()
        return QCursor(pixmap, 28, 28)

    @staticmethod
    def _create_tool_cursor(tool, color, pressed=False, fill_color=None,
                            fill_opacity=0):
        """绘制绘制类工具的字形光标。

        位图为 32×32 逻辑像素、透明底：先以浅色画粗描边作衬底，再以深青描边勾形，
        主体用工具当前色填充，保证在任意底色上都清晰。热点是该工具的真实绘制落点；
        按下态只加粗描边、绕热点轻微放大并在锚点套一圈高亮环，热点坐标保持不变。
        """
        pixmap = QPixmap(CURSOR_BITMAP_SIZE, CURSOR_BITMAP_SIZE)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        ink = QColor(color)
        if not ink.isValid():
            ink = QColor("#ff0000")
        light = QColor(CURSOR_OUTLINE_LIGHT)
        dark = QColor(CURSOR_OUTLINE_DARK)
        outline = 2.6 if pressed else 2.0

        def stroke_and_fill(path, brush):
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(light, outline + 2.0, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            painter.drawPath(path)
            painter.setPen(QPen(dark, outline, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            painter.setBrush(brush)
            painter.drawPath(path)

        if pressed:
            anchor_x, anchor_y = TOOL_CURSOR_HOTSPOTS[tool]
            painter.save()
            painter.translate(anchor_x, anchor_y)
            painter.scale(1.08, 1.08)
            painter.translate(-anchor_x, -anchor_y)

        if tool == "pen":
            shaft = QPainterPath()
            shaft.addPolygon(QPolygonF([QPointF(9, 19.5), QPointF(21, 7.5),
                                        QPointF(26, 12.5), QPointF(14, 24.5)]))
            shaft.closeSubpath()
            nib = QPainterPath()
            nib.addPolygon(QPolygonF([QPointF(5, 27), QPointF(9, 19.5), QPointF(14, 24.5)]))
            nib.closeSubpath()
            stroke_and_fill(shaft, light)
            stroke_and_fill(nib, ink)
            painter.setPen(QPen(dark, 1.2, Qt.SolidLine, Qt.RoundCap))
            painter.drawLine(QPointF(12.5, 16.5), QPointF(17.5, 21.5))
        elif tool == "marker":
            shaft = QPainterPath()
            shaft.addPolygon(QPolygonF([QPointF(10.5, 18.5), QPointF(22, 7),
                                        QPointF(28, 13), QPointF(16.5, 24.5)]))
            shaft.closeSubpath()
            chisel = QPainterPath()
            chisel.addPolygon(QPolygonF([QPointF(5, 27), QPointF(10.5, 18.5),
                                         QPointF(16.5, 24.5)]))
            chisel.closeSubpath()
            stroke_and_fill(shaft, QColor(ink.red(), ink.green(), ink.blue(), 150))
            stroke_and_fill(chisel, ink)
            painter.setPen(QPen(dark, 1.2, Qt.SolidLine, Qt.RoundCap))
            painter.drawLine(QPointF(13, 16), QPointF(18.5, 21.5))
        elif tool == "arrow":
            line = QPainterPath(QPointF(5, 27))
            line.lineTo(24, 8)
            head = QPainterPath()
            head.addPolygon(QPolygonF([QPointF(24, 8), QPointF(16.5, 9.5),
                                       QPointF(22.5, 15.5)]))
            head.closeSubpath()
            stroke_and_fill(line, ink)
            stroke_and_fill(head, ink)
            painter.setBrush(light)
            painter.setPen(QPen(dark, 1.2))
            painter.drawEllipse(QPointF(5, 27), 2.2, 2.2)
        elif tool in ("rect", "ellipse"):
            body = QPainterPath()
            if tool == "rect":
                body.addRoundedRect(QRectF(9, 6, 19, 19), 2.5, 2.5)
            else:
                body.addEllipse(QRectF(9, 6, 19, 19))
            fill = QColor(fill_color) if fill_color else QColor(ink)
            fill.setAlpha(round(max(0, min(100, fill_opacity)) * 255 / 100)
                           if fill_color else 0)
            painter.setPen(Qt.NoPen)
            painter.setBrush(fill if fill.alpha() else Qt.NoBrush)
            painter.drawPath(body)
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(light, 4.2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            painter.drawPath(body)
            painter.setPen(QPen(dark, 2.4, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            painter.drawPath(body)
            painter.setPen(QPen(ink, 1.35, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            painter.setBrush(Qt.NoBrush)
            painter.drawPath(body)
            painter.setBrush(light)
            painter.setPen(QPen(dark, 1.2))
            painter.drawEllipse(QPointF(6, 26), 1.8, 1.8)
        elif tool == "text":
            caret = QPainterPath()
            caret.moveTo(7, 7)
            caret.lineTo(17, 7)
            caret.moveTo(12, 7)
            caret.lineTo(12, 25)
            caret.moveTo(7, 25)
            caret.lineTo(17, 25)
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(light, 4.4, Qt.SolidLine, Qt.RoundCap))
            painter.drawPath(caret)
            painter.setPen(QPen(ink, 2.2, Qt.SolidLine, Qt.RoundCap))
            painter.drawPath(caret)

        if pressed:
            painter.restore()
            anchor_x, anchor_y = TOOL_CURSOR_HOTSPOTS[tool]
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(light, 3.4, Qt.SolidLine))
            painter.drawEllipse(QPointF(anchor_x, anchor_y), 3.4, 3.4)
            painter.setPen(QPen(dark, 1.6, Qt.SolidLine))
            painter.drawEllipse(QPointF(anchor_x, anchor_y), 3.4, 3.4)
        painter.end()
        hotspot = TOOL_CURSOR_HOTSPOTS[tool]
        return QCursor(pixmap, hotspot[0], hotspot[1])

    def _current_cursor_color(self):
        """当前工具光标应使用的颜色名；缺失或无效时退回画笔色，再退回红色。"""
        key = TOOL_CURSOR_COLOR_KEYS.get(self.tool)
        raw = self.settings.get(key) if key else None
        raw = raw or self.settings.get("pen_color") or "#ff0000"
        color = QColor(raw)
        return color.name() if color.isValid() else "#ff0000"

    def _tool_cursor(self, tool, pressed=False):
        color = self._current_cursor_color()
        fill_color = None
        fill_opacity = 0
        if tool in ("rect", "ellipse"):
            if self.settings.get(f"{tool}_fill_enabled", False):
                fill_color = QColor(self.settings.get(
                    f"{tool}_fill_color", color)).name()
                fill_opacity = int(self.settings.get(f"{tool}_fill_opacity", 35))
        key = (tool, color, bool(pressed), fill_color, fill_opacity)
        cursor = self._tool_cursor_cache.get(key)
        if cursor is None:
            cursor = self._create_tool_cursor(tool, color, pressed,
                                              fill_color, fill_opacity)
            if len(self._tool_cursor_cache) >= 64:
                self._tool_cursor_cache.clear()
            self._tool_cursor_cache[key] = cursor
        return cursor

    def _apply_tool_cursor(self):
        """按当前工具与是否按下左键应用光标；用「上次应用键」去抖，避免重复 setCursor。

        仅处理取色与六种绘制工具；其余工具的光标由调用方（缩放手柄、抓平移等）自行管理，
        这里保持不动，避免覆盖它们的专用光标。
        """
        if self.tool == "picker":
            pressed = bool(self._left_button_down)
            key = ("picker", pressed)
            if key != self._applied_cursor_key:
                self._applied_cursor_key = key
                self.setCursor(self._picker_pressed_cursor if pressed
                               else self._picker_cursor)
            return
        if self.tool in ("eraser", "mosaic"):
            circle_color = self.settings.get(f"{self.tool}_cursor_color",
                                             "#ff8c00" if self.tool == "eraser" else "#00c853")
            key = (self.tool, circle_color,
                   self.settings.get("eraser_width" if self.tool == "eraser" else "mosaic_width"),
                   bool(self.settings.get("mosaic_brush", True)))
            if key != self._applied_cursor_key:
                self._applied_cursor_key = key
                if self.tool == "eraser" or self.settings.get("mosaic_brush", True):
                    self.setCursor(Qt.BlankCursor)
                else:
                    self.unsetCursor()
            return
        if self.tool not in TOOL_CURSOR_TOOLS:
            return
        pressed = bool(self._left_button_down) and self.tool != "text"
        key = (self.tool, self._current_cursor_color(), pressed)
        if key != self._applied_cursor_key:
            self._applied_cursor_key = key
            self.setCursor(self._tool_cursor(self.tool, pressed))

    def refresh_tool_cursor(self):
        """标注颜色等设置变化后，作废当前工具的光标缓存并重建。"""
        if self.tool not in TOOL_CURSOR_TOOLS + ("eraser", "mosaic"):
            return
        for key in [item for item in self._tool_cursor_cache if item[0] == self.tool]:
            self._tool_cursor_cache.pop(key, None)
        self._applied_cursor_key = None
        self._apply_tool_cursor()

    def _reset_hover_cursor(self):
        """悬停交互结束（划出场景/松开缩放手柄）后的光标回退。

        select 与手型工具回退为系统箭头；绘制工具与取色恢复各自的工具字形光标，
        避免右键平移或划出场景后把工具光标误清成箭头。
        """
        if self.tool in TOOL_CURSOR_TOOLS or self.tool == "picker":
            self._applied_cursor_key = None
            self._apply_tool_cursor()
        elif self.tool in ("eraser", "mosaic"):
            self._applied_cursor_key = None
            self._apply_tool_cursor()
        else:
            self.unsetCursor()

    def set_tool(self, tool):
        """切换标注工具并同步其专属光标。"""
        if tool != self.tool:
            self._cancel_pending_text()
            self._last_number_click = None
            self.chain_active = False
            self.start = None
            self.preview_end = None
            self.committed_segments = []
            self.straight_drawing = False
        self.tool = tool
        self._left_button_down = False
        self._applied_cursor_key = None
        if tool in TOOL_CURSOR_TOOLS or tool in ("picker", "eraser", "mosaic"):
            self._apply_tool_cursor()
        else:
            self.unsetCursor()

    def _chain_tool(self):
        """当前工具是否支持多段连续绘制（箭头/开启多段的画笔/记号笔）。"""
        if self.tool == "arrow":
            return True
        if self.tool in ("pen", "marker"):
            return bool(self.settings.get(f"{self.tool}_chain", False))
        return False

    def _activate_click_tool(self, tool, point):
        """吸管/文字/序号这类单击生效的工具：按下即执行，双击按“保存”处理。

        序号立即落下，双击时撤销这一拍刚落的序号；文字输入框是模态的会挡住第二拍，
        因此延后一小段判定窗口（TEXT_CLICK_COMMIT_MS）再弹出，双击时取消弹框并改为保存。
        """
        if tool == "picker":
            x, y = int(point.x()), int(point.y())
            if 0 <= x < self.image.width and 0 <= y < self.image.height:
                self.color_picked.emit(
                    "#%02x%02x%02x" % self.image.convert("RGB").getpixel((x, y)))
            return
        if tool == "text":
            self._pending_text_point = QPointF(point)
            # 等待窗口可配置，但仍不超过系统双击间隔，否则双击会被判成“已弹出输入框”。
            self._text_click_timer.start(min(
                QApplication.doubleClickInterval(),
                int(self.settings.get("editor_text_click_delay", TEXT_CLICK_COMMIT_MS))))
            return
        if tool == "number":
            item = AnnotationSequenceItem(
                self.next_sequence_number(),
                self.settings.get("sequence_fill_color", "#ff0000"),
                self.settings.get("sequence_text_color", "#ffffff"),
                self.settings.get("sequence_font_size", 14),
                self.settings.get("sequence_shape", "circle"),
                self.settings.get("font", ""), point)
            self._add_annotation(item)
            self.checkpoint()
            self._last_number_click = item

    def _commit_text_click(self):
        """判定窗口内没有等到第二拍，才真正弹出文字输入框。"""
        point = self._pending_text_point
        self._pending_text_point = None
        if point is None:
            return
        text, _changed, ok = self.input_text("文字标注")
        if ok and text:
            self._add_annotation(text_item(
                point, text, self.settings, self.text_alignment))
            self.checkpoint()
        self._left_button_down = False
        self._applied_cursor_key = None
        self._apply_tool_cursor()

    def _cancel_pending_text(self):
        """放弃尚未弹出的文字输入框（换工具、失焦或按下被双击取代时调用）。"""
        if self._text_click_timer.isActive():
            self._text_click_timer.stop()
        self._pending_text_point = None

    def _revert_click_tool_once(self):
        """双击时撤销这一拍刚产生的标注：取消待弹文字框、删掉刚落的序号。"""
        self._cancel_pending_text()
        item = self._last_number_click
        self._last_number_click = None
        if item is not None and item.scene() is self.scene_data:
            self.scene_data.removeItem(item)
            self.checkpoint()

    def _shape_constraint_active(self, event=None):
        modifiers = QApplication.keyboardModifiers()
        modifiers = (event.modifiers() if event is not None
                     else QApplication.keyboardModifiers())
        return shape_constraint_active(modifiers)

    def _straight_gesture_active(self, event=None):
        """当前笔划是否为直线：多段模式，或 Ctrl、Alt 任一手势键。

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

    def transparent_checker_brush(self):
        """按“设置 > 编辑器 > 透明背景”与当前主题生成预览笔刷。

        theme 用主题感知的中性棋盘；dark/light_checker 用固定明暗棋盘；
        transparent 返回主题纯色（不画棋盘）。只影响编辑显示，不写入导出图片。
        """
        mode = self.settings.get("editor_transparent_background", "theme")
        dark_theme = self.palette().base().color().lightness() < 128
        if mode == "dark_checker":
            first, second = "#252525", "#3b3b3b"
        elif mode == "light_checker":
            first, second = "#f0f0f0", "#c8c8c8"
        elif mode == "transparent":
            # 纯透明：不画棋盘，透明处直接用主题底色，和画布留白一致。
            return QBrush(self.palette().base().color())
        elif dark_theme:
            first, second = "#3b3f45", "#4c5158"
        else:
            first, second = "#e0e3e6", "#f1f3f5"
        # 棋盘格边长可调（4–32px）；只在生成笔刷时读一次，设置变化后由 refresh 重建。
        tile = max(4, min(32, int(self.settings.get("editor_checker_tile_size",
                                                    checker_tile_size(1.0)))))
        pixmap = QPixmap(tile * 2, tile * 2)
        pixmap.fill(QColor(first))
        painter = QPainter(pixmap)
        painter.fillRect(0, 0, tile, tile, QColor(second))
        painter.fillRect(tile, tile, tile, tile, QColor(second))
        painter.end()
        return QBrush(pixmap)

    def refresh_transparency_preview(self):
        """透明棋盘样式变化（设置/主题）后重建笔刷并重绘。"""
        logging.getLogger("screensnap").debug(
            "重建透明棋盘预览: 样式=%s", self.settings.get("editor_transparent_background", "theme"))
        self.corner_preview_brush = self.transparent_checker_brush()
        self._checker_palette_base = self.palette().base().color()
        self.viewport().update()

    def _sync_checker_brush_to_palette(self):
        """调色板底色变了就重建棋盘格笔刷。

        theme 模式和“纯透明”模式的笔刷直接取自 QPalette.Base，只在设置变化时重建的话，
        宿主改调色板（换主题）后棋盘格会停在旧配色上。
        """
        base = self.palette().base().color()
        if base != self._checker_palette_base:
            self._checker_palette_base = base
            self.corner_preview_brush = self.transparent_checker_brush()

    def drawBackground(self, painter, rect):
        """图片区域铺透明棋盘格，图片外沿用主题底色。

        只覆盖 sceneRect，避免把图片周围的画布留白也画成“透明”，让用户误以为留白属于截图。
        """
        painter.fillRect(rect, self.palette().base())
        self._sync_checker_brush_to_palette()
        area = self.sceneRect().intersected(rect)
        if not area.isEmpty():
            painter.fillRect(area, self.corner_preview_brush)

    def set_round_corner_preview(self, enabled, radius=None):
        self.round_corner_preview = bool(enabled)
        if radius is not None:
            self.corner_radius = radius
        self.viewport().update()

    def image_point(self, point):
        bounds = self.sceneRect()
        return QPointF(min(max(point.x(), bounds.left()), bounds.right()),
                       min(max(point.y(), bounds.top()), bounds.bottom()))

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

    def constrain_selected_to_canvas(self):
        """按「标注超出画布」设置处理：block 模式把选中标注平移回画布内。

        clip（默认）不动几何，超出的部分在编辑视图与导出里直接裁掉；block 只做平移，
        不做旧版那种「整体缩放再挤回」，避免贴边填充的矩形被挤掉半条线宽。
        """
        if self.settings.get("editor_overcanvas_mode", "clip") != "block":
            return
        bounds = self.sceneRect()
        for item in self.scene_data.selectedItems():
            if item is self.base:
                continue
            rect = item.sceneBoundingRect()
            dx = dy = 0.0
            if rect.left() < bounds.left():
                dx = bounds.left() - rect.left()
            elif rect.right() > bounds.right():
                dx = bounds.right() - rect.right()
            if rect.top() < bounds.top():
                dy = bounds.top() - rect.top()
            elif rect.bottom() > bounds.bottom():
                dy = bounds.bottom() - rect.bottom()
            if dx or dy:
                item.setPos(item.pos() + QPointF(dx, dy))

    def checkpoint(self):
        """编辑后截断重做分支，记录两版图片、光标状态和全部标注。"""
        self.constrain_selected_to_canvas()
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
        标注超出画布的部分在此裁掉：编辑时只显示画布内的像素，和导出结果一致，
        贴边填充也因此不会被“挤回画布内”。
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
        # 先铺透明棋盘格；擦除底图留下的洞需明确显示为透明，而不是主题纯色。
        painter.save()
        painter.setBrushOrigin(QPointF(0, 0))
        painter.fillRect(QRectF(visible), self.corner_preview_brush)
        painter.restore()
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
        # 超出画布的标注在编辑视图里也截掉：把画布外区域盖回主题底色，和导出结果一致；
        # 画布内的标注位置完全不动（不再像旧版那样被缩放/平移挤回画布内）。
        image = self.sceneRect()
        exposed = QRectF(rect)
        if not image.contains(exposed):
            outside = QPainterPath()
            outside.setFillRule(Qt.OddEvenFill)
            outside.addRect(exposed)
            outside.addRect(image)
            painter.save()
            painter.setPen(Qt.NoPen)
            painter.setBrush(self.palette().base())
            painter.drawPath(outside)
            painter.restore()
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
            color = QColor(self.settings.get("eraser_cursor_color", "#ff8c00"))
            painter.setPen(QPen(color, 1.5, Qt.SolidLine))
            painter.setBrush(Qt.NoBrush)
            radius = self.settings.get("eraser_width", self.settings["pen_width"]) / 2
            painter.drawEllipse(self.eraser_point, radius, radius)
        # 马赛克涂抹模式：同样用虚线圆环表示笔刷宽度，便于对齐涂抹范围。
        if self.tool == "mosaic" and self.mosaic_point is not None \
            and self.settings.get("mosaic_brush", True):
            color = QColor(self.settings.get("mosaic_cursor_color", "#00c853"))
            painter.setPen(QPen(color, 1.5, Qt.SolidLine))
            painter.setBrush(Qt.NoBrush)
            radius = max(2, self.settings.get("mosaic_width", 20) / 2)
            painter.drawEllipse(self.mosaic_point, radius, radius)
        mosaic_brush_active = (
            self.tool == "mosaic" and self.settings.get("mosaic_brush", True))
        if (self.start is not None and self.preview_end is not None
            and not mosaic_brush_active):
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
        if self.mosaic_preview is not None:
            bounds, preview = self.mosaic_preview
            painter.drawImage(QPointF(bounds.topLeft()), preview)
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
        # 单选时在标注中心画旋转按钮（随图形移动/旋转，不会被放大挤出画面）。
        if len(selected) == 1:
            item = selected[0]
            handle = self.rotation_handle_position(item)
            pixmap = self.rotation_cursor.pixmap()
            painter.drawPixmap(QPointF(handle.x() - pixmap.width() / 2,
                                       handle.y() - pixmap.height() / 2), pixmap)
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

    # top 模式下旋转按钮离顶边的距离（center 模式用不到）。
    ROTATION_HANDLE_GAP = 24

    # 8 个控制点在标注自身坐标里的朝外方向（y 轴向下），用于旋转后换算光标方向。
    HANDLE_DIRECTIONS = {"nw": (-1, -1), "n": (0, -1), "ne": (1, -1), "e": (1, 0),
                         "se": (1, 1), "s": (0, 1), "sw": (-1, 1), "w": (-1, 0)}

    def _rotated_handle_cursor(self, handle, item):
        """按标注当前旋转角度给出该控制点的缩放光标方向。

        水平/垂直/两条斜线四种光标只有固定方向，旋转后若仍按未旋转的映射，
        光标会和手柄实际朝向对不上；这里把控制点朝外方向按图元变换旋转后再归类。
        """
        dx, dy = self.HANDLE_DIRECTIONS[handle]
        # sceneTransform 才同时包含 setTransform、setRotation 与 setScale；
        # item.transform() 只反映 setTransform，拿它算方向会永远按未旋转处理。
        transform = item.sceneTransform()
        origin = transform.map(QPointF(0, 0))
        direction = transform.map(QPointF(dx, dy)) - origin
        angle = math.degrees(math.atan2(direction.y(), direction.x())) % 180
        if angle < 22.5 or angle >= 157.5:
            return Qt.SizeHorCursor
        if angle < 67.5:
            return Qt.SizeFDiagCursor
        if angle < 112.5:
            return Qt.SizeVerCursor
        return Qt.SizeBDiagCursor

    def rotation_handle_position(self, item):
        """旋转按钮位置。

        默认（center）画在标注自身包围盒中心：随图形移动/旋转、始终在图形内部，
        放大或旋转后也不会被挤出画面；设为 top 时画在顶边外侧固定距离处（更接近常见习惯，
        但标注靠近画布上缘时可能超出可视区）。
        """
        rect = item.boundingRect()
        if self.settings.get("editor_rotation_handle", "center") == "top":
            top = item.mapToScene(QPointF(rect.center().x(), rect.top()))
            return QPointF(top.x(), top.y() - ROTATION_HANDLE_GAP)
        return item.mapToScene(rect.center())

    def rotation_handle_at(self, item, point):
        """指针是否落在旋转手柄 8 像素（场景单位）内。"""
        handle = self.rotation_handle_position(item)
        return ((handle.x() - point.x()) ** 2 + (handle.y() - point.y()) ** 2) <= 64

    @staticmethod
    def _create_rotation_cursor():
        pixmap = QPixmap(24, 24)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(4, 4, 16, 16)
        arrow = QPolygonF((QPointF(17, 3), QPointF(21, 9), QPointF(14, 8)))
        for color, width in ((QColor("#ffffff"), 4), (QColor("#202124"), 2)):
            painter.setPen(QPen(color, width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            painter.drawArc(rect, 35 * 16, 285 * 16)
            painter.drawLine(QPointF(17, 3), QPointF(20, 8))
            painter.drawLine(QPointF(17, 3), QPointF(13, 5))
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#202124"))
        painter.drawPolygon(arrow)
        painter.end()
        return QCursor(pixmap, 12, 12)

    def _begin_rotation(self, item, point):
        """开始旋转：以项包围盒中心为支点，记录起始角度与当前旋转值。"""
        self.rotating = item
        self.setCursor(self.rotation_cursor)
        logging.getLogger("screensnap").debug("标注旋转开始")
        center_local = item.boundingRect().center()
        # 改旋转支点会让「已带旋转的图元」整体平移一个常量（缩放过、或刚缩放过时支点并不是中心），
        # 先记下同一局部点的场景位置，改完再把这段位移补回去，避免按下瞬间“闪”到别处。
        before = item.mapToScene(center_local)
        item.setTransformOriginPoint(center_local)
        after = item.mapToScene(center_local)
        item.setPos(item.pos() + before - after)
        center = item.mapToScene(center_local)
        self.rotation_center = center
        self.rotation_start_value = item.rotation()
        # 旋转按钮就在中心：按下点几乎与支点重合，此时算不出有效方向；
        # 起始角留到第一次移动再取，避免按下瞬间跳一个角度。
        self.rotation_start_angle = None
        QToolTip.hideText()

    def _rotate_to(self, point, snap=False):
        """根据指针相对支点的角度增量更新旋转，snap=True 时吸附 15°。"""
        angle = math.atan2(point.y() - self.rotation_center.y(),
                           point.x() - self.rotation_center.x())
        if self.rotation_start_angle is None:
            # 第一次移动只用来确定起始方向，不产生跳变。
            self.rotation_start_angle = angle
            self.rotation_start_value = self.rotating.rotation()
            return
        delta = math.degrees(angle - self.rotation_start_angle)
        new_value = self.rotation_start_value + delta
        snap_step = int(self.settings.get("editor_rotation_snap", 15) or 0)
        if snap and snap_step > 0:
            new_value = round(new_value / snap_step) * snap_step
        self.rotating.setRotation(new_value)
        self.viewport().update()

    def _mosaic_brush_overlay(self, path):
        """生成透明笔刷覆盖图，返回其场景边界和 QImage。"""
        radius = max(2, self.settings.get("mosaic_width", 20) / 2)
        stroker = QPainterPathStroker()
        stroker.setWidth(radius * 2)
        stroker.setCapStyle(Qt.RoundCap)
        stroker.setJoinStyle(Qt.RoundJoin)
        region = stroker.createStroke(path)
        bounds = region.boundingRect().toRect().intersected(self.sceneRect().toRect())
        if bounds.isEmpty():
            return None
        sample = self.image.crop((bounds.left(), bounds.top(), bounds.right() + 1, bounds.bottom() + 1))
        mode = self.settings.get("mosaic_mode", "blur")
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
        return bounds, out

    def _update_mosaic_brush_preview(self):
        self.mosaic_preview = (self._mosaic_brush_overlay(self.mosaic_drawing)
                               if self.mosaic_drawing is not None else None)

    def _commit_mosaic_brush(self, path):
        """提交与实时预览相同的非破坏性马赛克/模糊覆盖。"""
        overlay = self._mosaic_brush_overlay(path)
        if overlay is None:
            return
        bounds, out = overlay
        item = editable(AnnotationPixmapItem(QPixmap.fromImage(out)))
        item.setOffset(QPointF(bounds.left(), bounds.top()))
        self._add_annotation(item)
        self.checkpoint()

    def _space_press_targets_handle(self, event):
        """空格+左键按在选中标注的控制点上时，应走「自由拉伸」而不是临时平移。

        工具提示与回归用例都写明「按住 Ctrl / Alt / Shift / Space 任意键可自由拉伸变形」，
        所以只在没按到控制点时才让空格接管平移。
        """
        if self.tool != "select":
            return False
        point = self.mapToScene(event.position().toPoint())
        for item in self.scene_data.selectedItems():
            if self.rotation_handle_at(item, point):
                return True
            if self.resize_handle_at(self.item_resize_handles(item), point):
                return True
        return False

    def mousePressEvent(self, event):
        if (self.space_pressed and self.start is None
                and self.mosaic_drawing is None
                and event.button() == Qt.LeftButton
                and not self._space_press_targets_handle(event)):
            self.space_pan_active = True
            self.space_pan_start = event.position().toPoint()
            event.accept()
            return
        """根据工具决定选中图元、取色或开始新的标注。"""
        if (event.button() == Qt.LeftButton and
            (self.tool in TOOL_CURSOR_TOOLS or self.tool == "picker") and
            self.tool != "text"):
            # 绘制工具按住左键时切换「按下态」光标；文字工具点击即弹对话框，不进入按下态。
            self._left_button_down = True
            self._apply_tool_cursor()
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
            # 手柄/旋转手柄判定在前：标注超出画布时手柄可能落在 sceneRect 外，也要能抓到；
            # 画布外的空白仍不参与框选，避免在灰色区域拖出整屏选区。
            if not self.sceneRect().contains(point):
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
            self._activate_click_tool("picker", point)
            return
        if self.tool not in ("select", "hand") and not self.sceneRect().contains(point):
            event.accept()
            return
        if self.tool == "text":
            self._activate_click_tool("text", point)
            return
        if self.tool == "number":
            self._activate_click_tool("number", point)
            return
        if self.tool == "mosaic" and self.settings.get("mosaic_brush", True) \
                and event.button() == Qt.LeftButton:
            # 预览层只显示效果，不进场景/历史；松开后再提交正式覆盖。
            self.start = point
            self.straight_drawing = self._straight_gesture_active(event)
            self.mosaic_drawing = QPainterPath(point)
            self.mosaic_point = point
            self.preview_end = point
            self._update_mosaic_brush_preview()
            self.viewport().update()
            event.accept()
            return
        if self.tool not in ("select", "hand") and event.button() == Qt.LeftButton:
            # 多段绘制进行中：保留上一终点作为本段起点，不从按下点重置。
            if not (self.chain_active and self._chain_tool() and self.start is not None):
                self.start = point
            self.preview_end = point
            if self.tool in ("pen", "marker"):
                self.straight_drawing = self._straight_gesture_active(event)
                if self.straight_drawing:
                    self.drawing = QPainterPath()
                    if not self.chain_active:
                        self.committed_segments = []
                else:
                    self.drawing = QPainterPath(self.start)
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.space_pan_active:
            point = event.position().toPoint()
            distance = point - self.space_pan_start
            self.horizontalScrollBar().setValue(
                self.horizontalScrollBar().value() - distance.x())
            self.verticalScrollBar().setValue(
                self.verticalScrollBar().value() - distance.y())
            self.space_pan_start = point
            event.accept()
            return
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
        if self.tool == "mosaic" and self.settings.get("mosaic_brush", True):
            # 未按住时也跟随指针，用虚线圆环显示笔刷宽度与位置。
            point = self.image_point(self.mapToScene(event.position().toPoint()))
            self.mosaic_point = point
            if self.mosaic_drawing is not None:
                if (not self.straight_drawing and
                        self._straight_gesture_active(event)):
                    self.straight_drawing = True
                if self.straight_drawing and self.start is not None:
                    self.mosaic_drawing = QPainterPath(self.start)
                    self.mosaic_drawing.lineTo(point)
                else:
                    self.mosaic_drawing.lineTo(point)
                self._update_mosaic_brush_preview()
                self.preview_end = point
            self.viewport().update()
            return
        if self.resizing is not None:
            # 缩放不把鼠标点夹回画布内：允许标注超出画布，超出部分绘制时裁掉。
            point = self.mapToScene(event.position().toPoint())
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
            self.viewport().update()
            event.accept()
            return
        if self.start is not None:
            end = self.image_point(self.mapToScene(event.position().toPoint()))
            if (self.tool in ("rect", "ellipse")
                    and self._shape_constraint_active(event)):
                end = constrained_shape_endpoint(self.start, end)
            self.preview_end = end
            if self.tool in ("pen", "marker"):
                # 修饰键可能在鼠标按下之后才按住，拖动途中也能切换为直线模式。
                if not self.straight_drawing and self._straight_gesture_active(event):
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
            self._reset_hover_cursor()
            QToolTip.hideText()
            return
        for item in self.scene_data.selectedItems() if self.tool == "select" else ():
            if self.rotation_handle_at(item, point):
                self.setCursor(self.rotation_cursor)
                QToolTip.hideText()
                return
            handle = self.resize_handle_at(self.item_resize_handles(item), point)
            if handle:
                cursor = self._rotated_handle_cursor(handle, item)
                if len(handle) == 1 and isinstance(item, QGraphicsTextItem):
                    # 文字标注：左右边中点调整文本框宽度、上下边中点调整高度。
                    if handle in ("e", "w"):
                        self.setCursor(cursor)
                        QToolTip.showText(
                            self.viewport().mapToGlobal(position),
                            "拖动左右边中点调整文本框宽度（文字自动换行）；四角仍是缩放",
                            self)
                    else:
                        self.setCursor(cursor)
                        QToolTip.showText(
                            self.viewport().mapToGlobal(position),
                            "拖动上下边中点调整文本框高度（超出部分裁剪）；四角仍是缩放",
                            self)
                else:
                    self.setCursor(cursor)
                    QToolTip.showText(
                        self.viewport().mapToGlobal(position),
                        "拖动控制点等比缩放；按住 Ctrl / Alt / Shift / Space 任意键可自由拉伸变形",
                        self)
                return
            if item.contains(item.mapFromScene(point)):
                self.setCursor(Qt.SizeAllCursor)
                QToolTip.hideText()
                return
        self._reset_hover_cursor()
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
        # 清除「上次应用键」，使重新进入时能按当前状态重新应用工具光标。
        self._applied_cursor_key = None
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
        if self.space_pan_active and event.button() == Qt.LeftButton:
            self.space_pan_active = False
            self.space_pan_start = None
            if not self.space_pressed:
                self.setDragMode(QGraphicsView.RubberBandDrag if self.tool == "select"
                                 else QGraphicsView.NoDrag)
            event.accept()
            return
        """按当前工具提交图元；裁剪时两版截图使用相同区域。"""
        if self._left_button_down:
            # 松开左键无条件退出「按下态」光标；多段绘制松手后仍保持链状态。
            self._left_button_down = False
            self._applied_cursor_key = None
            self._apply_tool_cursor()
        if self.rotating is not None:
            logging.getLogger("screensnap").debug("标注旋转结束")
            self.rotating = None
            self.checkpoint()
            self.viewport().update()
            self._update_resize_cursor(event.position().toPoint())
            event.accept()
            return
        if self.mosaic_drawing is not None:
            self._commit_mosaic_brush(self.mosaic_drawing)
            self.mosaic_drawing = None
            self.mosaic_preview = None
            self.start = None
            self.straight_drawing = False
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
            if (self.tool in ("rect", "ellipse")
                    and self._shape_constraint_active(event)):
                end = constrained_shape_endpoint(self.start, end)
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
                mode = self.settings.get("mosaic_mode", "blur")
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
                if not dragged:
                    # 单击（无拖动）不落零尺寸图形：与画笔/记号笔的孤立单击不绘制保持一致。
                    self.start = None
                    self.chain_active = False
                    self.viewport().update()
                    return
                style = self.settings.get(f"{self.tool}_style", "solid") if self.tool in ("rect", "ellipse") else \
                    (self.settings.get("arrow_style", "filled") if self.tool == "arrow" else "filled")
                item = shape(self.tool, self.start, end,
                             self.tool_color(), self.tool_width(), style,
                             self.settings.get("rect_corner_radius", 0)
                             if self.tool == "rect" and self.settings.get("rect_corner_enabled", True)
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
        if event.button() == Qt.LeftButton and self.tool in ("picker", "text", "number"):
            # 单击生效的工具：双击表示“保存”。撤销第一拍刚落下的序号或待弹文字框，
            # 再发确认信号，交给编辑器执行与“选择工具双击空白”一致的保存。
            self._revert_click_tool_once()
            self.confirmed.emit()
            event.accept()
            return
        if event.button() == Qt.LeftButton and self.tool == "eraser":
            # 橡皮擦的单击（无拖动）会留下一个擦除点；双击表示“保存”，
            # 先把这一点撤销掉，否则双击位置会残留一块擦除白点。
            self.undo()
            self.confirmed.emit()
            event.accept()
            return
        if (event.button() == Qt.LeftButton and self.tool == "mosaic"
                and self.settings.get("mosaic_brush", True)):
            # 涂抹马赛克单击是零长度笔迹、本来就不落点，也没有历史可撤，直接保存即可。
            self.confirmed.emit()
            event.accept()
            return
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
            self._left_button_down = False
            self._applied_cursor_key = None
            self._apply_tool_cursor()
            self.cancelled.emit()
        elif event.key() == Qt.Key_Space:
            self.space_pressed = True
            if self.start is None and self.mosaic_drawing is None:
                self.setDragMode(QGraphicsView.ScrollHandDrag)
            event.accept()
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
            if not self.space_pan_active:
                self.setDragMode(QGraphicsView.RubberBandDrag if self.tool == "select"
                                 else QGraphicsView.NoDrag)
        super().keyReleaseEvent(event)

    def focusOutEvent(self, event):
        # 焦点丢失时清空空格状态，避免拖拽判定残留；并作废光标应用键以便重新校准。
        self._cancel_pending_text()
        self.space_pressed = False
        self.space_pan_active = False
        self.space_pan_start = None
        self.setDragMode(QGraphicsView.RubberBandDrag if self.tool == "select"
                 else QGraphicsView.NoDrag)
        self._left_button_down = False
        self._applied_cursor_key = None
        self._apply_tool_cursor()
        super().focusOutEvent(event)

    def _zoom_at(self, viewport_position, percent):
        """缩放并尽量让光标下的场景点保持在原处（滚轮缩放用）。"""
        before = self.mapToScene(viewport_position)
        previous = self.zoom_percent
        self.set_zoom(percent)
        if self.zoom_percent != previous:
            # 只在比例真的变化时记一行；滚轮连滚不会刷屏。
            logging.getLogger("screensnap").debug("Ctrl+滚轮缩放显示比例: %d%% -> %d%%",
                                                  previous, self.zoom_percent)
        after = self.mapToScene(viewport_position)
        shift = after - before
        scale = self.transform().m11()
        self.horizontalScrollBar().setValue(
            round(self.horizontalScrollBar().value() - shift.x() * scale))
        self.verticalScrollBar().setValue(
            round(self.verticalScrollBar().value() - shift.y() * scale))

    def wheelEvent(self, event):
        """默认纵向滚动；启用滚轮缩放时 Ctrl+滚轮缩放；Alt+滚轮横向移动。"""
        modifiers = event.modifiers() | QApplication.keyboardModifiers()
        if self.wheel_zoom_enabled and (modifiers & Qt.ControlModifier):
            angle = event.angleDelta().y()
            pixel = event.pixelDelta().y()
            delta = angle or pixel
            if delta:
                base = max(1, int(self.settings.get("editor_zoom_wheel_step", 10)))
                step = (base * max(1, abs(angle) // 120)) if angle else base
                self._zoom_at(event.position().toPoint(),
                              self.zoom_percent + (step if delta > 0 else -step))
            event.accept()
            return
        horizontal_scroll = bool(modifiers & Qt.AltModifier)
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