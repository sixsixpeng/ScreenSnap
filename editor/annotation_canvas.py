"""图片画布、标注事件和可撤销操作。"""

import math

from PIL import Image, ImageFilter
from PySide6.QtCore import Qt, QPoint, QPointF, QRect, QRectF, QLineF, Signal, QTimer
from PySide6.QtGui import (QBrush, QShortcut, QPainter, QPainterPath, QPen, QColor, QPixmap, QImage,
                             QPolygonF, QPainterPathStroker, QTextBlockFormat,
                             QTextCursor, QTransform, QCursor, QKeySequence)
from PySide6.QtWidgets import (QApplication, QGraphicsView, QGraphicsScene, QGraphicsPixmapItem, QGraphicsItem,
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
from logger import get_logger

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
    # 2026-10-11（D8 ④）：Space 会被 QGraphicsView 用 ShortcutOverride 吞掉（画布拿它拖动画布），
    # 因此工具栏上那个 QShortcut 在主屏（原地编辑里画布有焦点）收不到 ⇒ 在画布侧再建一份并发这个信号。
    sticker_shortcut = Signal()
    # 2026-10-11：无修饰的可打印键（A / 0）会被画布用 ShortcutOverride 吞掉 ⇒ 改在画布内处理，
    # 再让编辑器接这条信号（与 Space 的 sticker_shortcut 同一手法，D8 ④）。
    save_as_requested = Signal()
    # 2026-10-11：Ctrl+S 也走画布（挂窗口快捷键会因「哪块屏/哪种选区入口创建的原地编辑器」而漏 ✓）。
    save_requested = Signal()
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
        # 更新策略（2026-10-11 性能排查后**保持** FullViewportUpdate，别改成 SmartViewportUpdate）：
        # 实测整视口重绘只有 2.7 ms/帧，不是瓶颈；而改小更新区域会让两条既有回归失败 —— 拖动已选中的
        # 标注会留下残影（test_dragging_selected_annotation_repaints_full_viewport）、橡皮擦实时镂空
        # 不再清除背景（test_eraser_erase_base_live_viewport_clears_background，实测擦过处显示成
        # 缓存的棋盘色 #f1f3f5 而不是主题底色）。真要再动这里，必须先复现并解决这两条。
        self.setViewportUpdateMode(QGraphicsView.FullViewportUpdate)
        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)
        self.setAlignment(Qt.AlignCenter)
        self.setDragMode(QGraphicsView.RubberBandDrag)
        # 2026-10-11：这里**不再**注册 Space 的 QShortcut。
        # ① 该键会被 QGraphicsView 用 ShortcutOverride 吞掉，QShortcut 收不到（D8 ④）；
        # ② 同键注册第二份会让**整个应用**的同键判为 ambiguous 而集体失效（D8 ⑤，真机定案）；
        # ③ Space 现在统一在下面的 keyPressEvent 里处理（直接发 sticker_shortcut）⇒ 本份必须删。
        # 移除后果（勿删此注释）：若恢复此快捷键，主屏/副屏的 Space 贴图会再次完全无反应。
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
        # 2026-10-12（问题 2 ✓）：键盘微调的「指针跟随」是程序自己移动指针 —— 标记 + 目标点，
        #   供 mouseMoveEvent 区分程序移动（保留按键定格的手柄）与用户真实移动（清掉定格）✓。
        self._programmatic_cursor_move = False
        self._programmatic_cursor_target = None
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
        self.rotating = None
        self.rotation_cursor = self._create_rotation_cursor()
        self.rotation_center = None
        self.rotation_start_angle = 0.0
        self.rotation_start_value = 0.0
        self.mosaic_drawing = None
        self.mosaic_preview = None
        self._mosaic_preview_key = None
        self._mosaic_preview_source = None
        self.mosaic_point = None
        # 2026-10-11：光标圆环要用**未夹取**的真实指针点 ✓（`image_point()` 会把几何夹到画布边缘；
        # 圆环若也用它，指针移出画布后就会贴在边缘不动 ✗ —— 就是用户说的"鼠标被吸附" ✓）。
        self.mosaic_point_raw = None
        self.eraser_point_raw = None
        # 2026-10-12 标注层缓存（**纯计算** ✓ 不碰任何重绘调用 ✓）：
        # 实测有擦除层时每帧都要 scene.render 全部标注（26.9 ms / 20 个 ✗ ⇒ 整帧 68.7 ms ✓）。
        # 这里缓存"隐藏擦除层后的标注渲染" ✓，每帧只把擦除遮罩按 DestinationOut 叠上去 ✓ ⇒ 逐像素等价 ✓。
        self._annotation_layer_stamp = 0
        self._annotation_layer_cache = (None, None, None)
        self._base_erase_cache = (None, None, None)
        self._building_annotation_cache = False
        self._annotation_layer_pending = False          # 延迟重建是否已排队 ✓（见 _cached_annotation_layer）
        self._base_restore_pending = False              # 底图恢复显示是否已排队 ✓（见 _restore_base_visibility）
        self._eraser_layer_locked = False
        self.scene_data.changed.connect(self._bump_annotation_layer_stamp)
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

    def _apply_handle_cursor(self):
        if self.rotating is not None:
            cursor = self.rotation_cursor
            key = ("rotation-handle",)
        else:
            item = self.resizing
            handle = self.resize_handle if item is not None else None
            if item is None and (self._left_button_down or QApplication.mouseButtons() & Qt.LeftButton):
                handle = self.active_handle()
                selected = self.scene_data.selectedItems()
                item = selected[0] if handle and selected else None
            if item is None or not handle:
                return False
            if handle == "rotate":
                cursor = self.rotation_cursor
                key = ("rotation-handle",)
            else:
                cursor = self._rotated_handle_cursor(handle, item)
                key = ("resize-handle", handle, cursor)
        if key != self._applied_cursor_key:
            self._applied_cursor_key = key
            self.setCursor(cursor)
            get_logger().debug("标注操作光标切换：%s", key)
        return True

    def _apply_tool_cursor(self):
        """按当前工具与是否按下左键应用光标；用「上次应用键」去抖，避免重复 setCursor。

        仅处理取色与六种绘制工具；其余工具的光标由调用方（缩放手柄、抓平移等）自行管理，
        这里保持不动，避免覆盖它们的专用光标。
        """
        if self._apply_handle_cursor():
            return
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
        if self._apply_handle_cursor():
            return
        if self.tool in TOOL_CURSOR_TOOLS or self.tool == "picker":
            self._applied_cursor_key = None
            self._apply_tool_cursor()
        elif self.tool in ("eraser", "mosaic"):
            self._applied_cursor_key = None
            self._apply_tool_cursor()
        else:
            self._applied_cursor_key = None
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
        # 2026-10-12（用户要求加配置 ✓）：触发修饰键由 `editor_straight_modifier` 决定 ✓
        #   either（默认 ✓ Ctrl 或 Alt 任一）/ ctrl / alt / off（关闭直线 ⇒ 全部自由笔迹 ✓）
        mode = str(self.settings.get("editor_straight_modifier", "either") or "either")
        if mode == "off":
            straight_by_key = False
        elif mode == "ctrl":
            straight_by_key = bool(modifiers & Qt.ControlModifier)
        elif mode == "alt":
            straight_by_key = bool(modifiers & Qt.AltModifier)
        else:
            straight_by_key = bool(modifiers & (Qt.ControlModifier | Qt.AltModifier))
        return straight_by_key or self._chain_tool() or self.chain_active

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
        # 底图 QImage 缓存（性能，2026-10-11）：擦除实时合成原先每帧都 toImage() 转整幅底图，
        # 实测 20+ ms/帧。这里在设置 pixmap 的同一处同步维护一份缓存。
        self._base_qimage = None
        # to_qimage 只调一次：同一份像素既给图元，也给导出缓存（导出必须与原先逐像素一致，
        # 所以刻意用 to_qimage 的结果而不是 QPixmap.toImage()——后者可能改变格式/alpha）。
        base_qimage = to_qimage(self.image)
        self.base.setPixmap(QPixmap.fromImage(base_qimage))
        self.base.setCacheMode(QGraphicsItem.DeviceCoordinateCache)  # 缩放/平移时少一次大图重采样
        self._export_qimage = base_qimage
        self._base_qimage = self.base.pixmap().toImage().convertToFormat(QImage.Format_ARGB32)
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
        get_logger().debug(
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

    # ── 标注越出画布的允许余量（像素）—— 这是唯一的调参入口，直接改数字即可 ──
    # 正数 = 允许越出这么多像素（笔迹更贴边）；0 = 中心线只到图片边缘；负数 = 比边缘再内缩（更保守）。
    # 2026-10-11 用户实测：-1 时显示刚好（先试 3 太靠外），故定为 -1。
    OVERFLOW_MARGIN = -1

    def image_point(self, point):
        """把场景点转成**用于标注几何**的图片坐标：默认最远只到画布边缘。

        · 标注（画笔、箭头、矩形、椭圆、文字、擦除、马赛克笔刷）默认最远只到画布边缘 ——
          指针可以自由移出画布，但落在画布外的位置会被夹到边缘这一点；
        · **`editor_overcanvas_mode=allow`（"允许超出"）不夹取**：几何按真实落点取点，
          可以正常画到画布之外（2026-10-15 用户要求新增的分支；clip/block 行为完全不变）；
        · 指针本身从不被移动；
        · **框选（选区橡皮筋）不走这里** —— mouseMoveEvent 里 selection_end 用原始场景点，
          这样框选可拖到画布外，也不会把画布外的按下/右键算成边缘那一点（原先会影响选择与菜单）；
        · 编辑结束时的处理：clip（默认）在编辑视图与导出时裁掉，block 由
          `constrain_selected_to_canvas()` 把标注平移回画布内，allow 保持画出的位置不动。
        """
        if str(self.settings.get("editor_overcanvas_mode", "clip")) == "allow":
            return QPointF(point)   # 允许超出：不夹取（导出仍按画布尺寸裁切）
        margin = self.OVERFLOW_MARGIN
        bounds = self.sceneRect().adjusted(-margin, -margin, margin, margin)
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

    def export_edit_history(self):
        history = list(self.history)
        history[self.cursor_index] = (self.image, self.alternate, self.cursor_enabled, self.snapshot())
        return history, self.cursor_index, self.original, self.original_alternate

    def restore_edit_history(self, state):
        history, index, original, original_alternate = state
        if not history or not isinstance(index, int) or not 0 <= index < len(history):
            raise ValueError("Invalid editor history cursor")
        previous = self.export_edit_history()
        try:
            self.history = list(history)
            self.cursor_index = index
            self.original = original
            self.original_alternate = original_alternate
            self.restore_checkpoint()
        except Exception:
            self.history, self.cursor_index, self.original, self.original_alternate = previous
            self.restore_checkpoint()
            raise
        get_logger().info("已恢复编辑历史：节点=%d 当前=%d 重做=%d",
                                             len(self.history), index, len(self.history) - index - 1)

    def restore_checkpoint(self):
        """从历史节点恢复底图与标注，通知工具栏同步光标开关。"""
        image, alternate, cursor_enabled, records = self.history[self.cursor_index]
        image_changed = self.image is not image
        self.image = image
        self.alternate = alternate
        self.cursor_enabled = cursor_enabled
        if image_changed:
            self.refresh_image()
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
        get_logger().debug(
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
        size = (self.image.width, self.image.height)
        states = [(item, item.strokes, len(item.strokes), QTransform(item.sceneTransform()))
                  for item in self.erase_items()]
        cached_size, previous, union = self._base_erase_cache
        incremental = (cached_size == size and previous is not None and len(previous) == len(states)
                       and all(old[0] is new[0] and old[1] is new[1] and old[2] <= new[2]
                               and old[3] == new[3] for old, new in zip(previous, states)))
        if not incremental:
            union = None
        additions = []
        for index, (item, strokes, count, transform) in enumerate(states):
            offset = previous[index][2] if incremental else 0
            additions.append((item, transform, [stroke for stroke in strokes[offset:count] if stroke[6]]))
        if any(strokes for item, transform, strokes in additions):
            if union is None:
                union = QImage(*size, QImage.Format_ARGB32)
                union.fill(Qt.transparent)
            painter = QPainter(union)
            painter.setRenderHint(QPainter.Antialiasing)
            try:
                for item, transform, strokes in additions:
                    painter.save()
                    painter.setTransform(transform, True)
                    painter.setClipRect(item.boundingRect(), Qt.IntersectClip)
                    for stroke in strokes:
                        item.paint_stroke(painter, stroke)
                    painter.restore()
            finally:
                painter.end()
        self._base_erase_cache = (size, states, union)
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
        # 复用 refresh_image 里算好的导出缓存：原先每次导出都 to_qimage 整幅拷贝（5120x1440 实测 24.7 ms）。
        export_image = self._export_qimage
        if export_image is None:
            export_image = to_qimage(self.image)
            self._export_qimage = export_image
        painter.drawImage(0, 0, export_image)
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
            # 百分比只作用于记号笔，普通画笔保持不透明；预览与提交共用同一画笔。
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
        # 2026-10-12：性能优化已回退（用户要求 ✓）⇒ 恢复整视口重绘 ✓（一定画得出去 ✓）
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
        # 2026-10-12 复查加固（规则 34-B）：擦除拖动期间会锁住标注层缓存 ✓；
        # 若某次按下之后既没有移动也没有 release（例如按下后直接 Esc / 焦点被夺 ✓），锁会一直扣着 ✗
        # ⇒ 之后新画的标注无法让缓存失效 ✗。这里每次合成时自愈 ✓（成本一次布尔判断 ✓）。
        if not self.erasing and self._eraser_layer_locked:
            self._eraser_layer_locked = False
        # 只合成**脏矩形**（有记录时 ✓）；没有记录（例如导出/打印路径 ✓）才退回整视口 ✓。
        # 2026-10-12（用户要求）：**性能优化已回退** ✓ —— 这里恢复为整视口合成 ✓
        # （此前为省时间引入了"脏矩形/强制脏区" ✗ ⇒ 与 Qt 的碎块 paintEvent 相互干扰，
        #  导致直线擦除只在部分区域重绘 ✗；用户明确要求"保留划直线、回退性能部分" ✓）。
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
        # 用缓存（原先每帧 toImage() 会把整幅 5120x1440 底图转一遍，实测 20+ ms/帧）。
        base_img = self._base_qimage
        if base_img is None:
            base_img = self.base.pixmap().toImage().convertToFormat(QImage.Format_ARGB32)
            self._base_qimage = base_img
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
        erase_layers = self.erase_items()
        annotation_levels = [item.zValue() for item in self.annotations()]
        top_erase_above_all = bool(erase_layers) and (
            not annotation_levels or erase_layers[-1].zValue() > max(annotation_levels))
        if top_erase_above_all:
            ann = self._cached_annotation_layer(w, h, x0, y0)
            if ann is None:      # 缓存未就绪 ⇒ 当帧兜底 ✓（延迟重建已排入事件循环 ✓ 见该函数注释）
                ann = self._render_annotation_layer_now(
                    w, h, x0, y0, include_erase=True, excluded_erase=erase_layers[-1])
            ap = QPainter(ann)
            ap.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing |
                              QPainter.SmoothPixmapTransform)
            ap.translate(-x0, -y0)
            ap.setTransform(erase_layers[-1].sceneTransform(), True)
            erase_layers[-1].paint(ap, None, None)
            ap.end()
            painter.drawImage(visible.topLeft(), ann)
        else:
            ann = (None if self.erasing
                   else self._cached_annotation_layer(w, h, x0, y0, want_full=True))
            if ann is None:
                ann = self._render_annotation_layer_now(w, h, x0, y0, include_erase=True)
            painter.drawImage(visible.topLeft(), ann)

    def _restore_base_visibility(self):
        """在**事件循环里**恢复底图显示 ✓（绘制期只允许"请求"，见 AGENTS 经验 D35 ✓）。"""
        self._base_restore_pending = False
        try:
            import shiboken6
            if not shiboken6.isValid(self):
                return
        except Exception:                     # noqa: BLE001  仅安全兜底 ✓
            return
        if self.base is not None and not self.base.isVisible():
            self.base.show()

    def _bump_annotation_layer_stamp(self, *_regions):
        """场景变化使保序缓存失效；无擦除层时保留原生矢量绘制，不构建缓存。"""
        # 2026-10-12 修正（探针实测：拖动 30 次里缓存重建 29 次 ✗ ⇒ 缓存对"拖动中"几乎无效 ✓）：
        # 擦除拖动期间标注层**不可能变化**（用户正在擦除 ✓ 不在编辑标注 ✓）⇒ 此时锁住缓存 ✓；
        # 否则擦除自身的重绘也会把 changed 打进来 ⇒ 每帧重建 ⇒ 缓存形同虚设 ✗。
        if not self._building_annotation_cache and not self._eraser_layer_locked:
            self._annotation_layer_stamp += 1
            if not self.erase_items():
                self._annotation_layer_cache = (None, None, None)
                if getattr(self, "_annotation_cache_mode", None) is not None:
                    get_logger().debug("标注缓存模式：无擦除，恢复原生绘制")
                    self._annotation_cache_mode = None
                return
            # 2026-10-12（D35 收尾）：标注一变就**在这里**（非绘制期 ✓）排一次延迟重建 ✓
            # ⇒ 事件循环下一轮缓存就绪 ⇒ 绘制期几乎永远命中缓存 ✓，
            # 从而让"绘制期切可见性"的兜底 `_render_annotation_layer_now` 退化成罕见路径 ✓。
            # 仍然只排一次（`_annotation_layer_pending` ✓ 规则 14：高频路径不重复排队 ✓）。
            if not self._annotation_layer_pending:
                self._annotation_layer_pending = True
                QTimer.singleShot(0, self._rebuild_annotation_layer)

    def _annotation_layer_key(self, w, h, x0, y0):
        """缓存失效键：视口尺寸 / 可见区原点 / 缩放 / 场景条目数 / 标注变更计数 ✓。"""
        return (w, h, x0, y0, round(float(self.transform().m11()), 6),
                len(self.scene_data.items()), self._annotation_layer_stamp)

    def _cached_annotation_layer(self, w, h, x0, y0, want_full=False):
        """缓存命中时返回最上层擦除之前的保序前缀，或完整保序图像的副本。

        2026-10-12 风险整改（AGENTS 经验 **D35**：绘制期绝不改可见性 ✗）：原实现在缓存未命中时
        **就地** `base.hide() … scene.render() … base.show()` ✗ —— 与那次 0xC0000409 原生崩溃同一族 ✓。
        现在未命中 ⇒ 排一次**延迟重建**（`QTimer.singleShot(0, …)` ✓ 事件循环下一轮、非绘制期 ✓）
        并返回 `None` ✓；调用方当帧退回 `_render_annotation_layer_now`（与旧实现逐像素相同 ✓ 不劣于现状 ✓）。
        """
        key = self._annotation_layer_key(w, h, x0, y0)
        cached_key, cached, full = self._annotation_layer_cache
        chosen = full if want_full else cached
        if cached_key == key and chosen is not None and not chosen.isNull():
            return chosen.copy()
        if not self._annotation_layer_pending:
            self._annotation_layer_pending = True
            QTimer.singleShot(0, self._rebuild_annotation_layer)
        return None

    def _rebuild_annotation_layer(self):
        """在事件循环中重建所需的保序缓存，不改变图元可见性。"""
        self._annotation_layer_pending = False
        # D20/D22 教训：延迟回调可能落在部件已析构之后 ✗ ⇒ 先判活，再碰任何 Qt 对象 ✓。
        try:
            import shiboken6
            if not shiboken6.isValid(self):
                return
        except Exception:                     # noqa: BLE001  仅安全兜底，不影响功能 ✓
            pass
        if not self.isVisible():
            return
        erase_layers = [item for item in self.erase_items() if item.isVisible()]
        if not erase_layers:
            self._annotation_layer_cache = (None, None, None)
            return
        vp = self.viewport().rect()
        if vp.isEmpty():
            return
        visible = self.mapToScene(vp).boundingRect().intersected(self.sceneRect())
        if visible.isEmpty():
            return
        # 区域算法与 `_paint_erased_composite` 保持**一致** ✓（那边还要用同一块区域画棋盘格/底图 ✓）。
        x0 = max(0, int(math.floor(visible.x())))
        y0 = max(0, int(math.floor(visible.y())))
        x1 = max(x0 + 1, int(math.ceil(visible.x() + visible.width())))
        y1 = max(y0 + 1, int(math.ceil(visible.y() + visible.height())))
        w, h = x1 - x0, y1 - y0
        if w <= 0 or h <= 0:
            return
        key = self._annotation_layer_key(w, h, x0, y0)
        annotation_levels = [item.zValue() for item in self.annotations()]
        top_erase_above_all = (not annotation_levels or erase_layers[-1].zValue() > max(annotation_levels))
        image = (self._render_annotation_layer_now(
            w, h, x0, y0, include_erase=True, excluded_erase=erase_layers[-1])
            if top_erase_above_all else None)
        full = None
        needs_ordered = not top_erase_above_all
        if needs_ordered:
            full = self._render_annotation_layer_now(w, h, x0, y0, include_erase=True)
        mode = "prefix" if top_erase_above_all else "ordered"
        if getattr(self, "_annotation_cache_mode", None) != mode:
            self._annotation_cache_mode = mode
            get_logger().debug(
                "标注缓存模式切换：%s 擦除层=%d 标注=%d", mode, len(erase_layers), len(annotation_levels))
        self._annotation_layer_cache = (key, image, full)
        self.viewport().update()      # 缓存就绪 ⇒ 请当帧用缓存重画 ✓

    def _render_annotation_layer_now(self, w, h, x0, y0, include_erase=False, excluded_erase=None):
        """**绘制期**渲染标注层 —— 仅用于"缓存尚未就绪"的那一帧兜底 ✓。

        2026-10-12（D35 收尾第四步 ✓）：**不再切换任何可见性** ✗ ⇒ 改为**逐图元绘制** ✓
        （按 `Qt.AscendingOrder` 遍历场景、跳过底图、按需跳过擦除层、用 `item.sceneTransform()` 复原变换 ✓）。
        前提已核查 ✓：全项目**没有** `setGraphicsEffect` 用在场景图元上 ✓（只在工具栏 ✗ 不在场景 ✓）、
        也没有 `setOpacity` ✓ ⇒ 逐图元绘制与 `scene.render` 等价 ✓（像素探针验证：抽样差异 0 ✓）。
        """
        from PySide6.QtWidgets import QStyleOptionGraphicsItem

        image = QImage(w, h, QImage.Format_ARGB32)
        image.fill(Qt.transparent)
        painter = QPainter(image)
        painter.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing |
                               QPainter.SmoothPixmapTransform)
        painter.setClipRect(QRectF(0, 0, w, h))
        painter.translate(-x0, -y0)
        self._building_annotation_cache = True
        try:
            for item in self.scene_data.items(Qt.AscendingOrder):   # 从底到顶 = z 序 ✓
                if item is self.base or not item.isVisible():
                    continue                                       # 底图由合成单独绘制 ✓
                if item is excluded_erase or not include_erase and isinstance(item, EraseMaskItem):
                    continue                                       # 按需排除擦除层 ✓
                option = QStyleOptionGraphicsItem()
                option.exposedRect = item.boundingRect()
                painter.save()
                painter.setTransform(item.sceneTransform(), True)   # 叠加在 -x0,-y0 之上 ✓
                item.paint(painter, option, None)
                painter.restore()
        finally:
            painter.end()
            self._building_annotation_cache = False
        return image

    def paintEvent(self, event):
        """慢帧探针（性能，2026-10-11）：只在整屏重绘超过 8 ms 时记一条 DEBUG。

        用重写 paintEvent 而不是改 drawForeground：绘制钩子有多条分支与早退，包一层会动到被测逻辑；
        这里只计时，不改任何绘制行为（规则 14：只报慢帧，不刷屏）。
        """
        import time as _time

        started = _time.perf_counter()
        super().paintEvent(event)
        elapsed = (_time.perf_counter() - started) * 1000.0
        if elapsed >= 8.0:
            get_logger().debug(
                "编辑器慢帧: 重绘 %.1f ms 区域=%s 标注=%d",
                elapsed, event.rect().size(), len(self.annotations()))

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
        elif self.base is not None and not self.base.isVisible():
            # 擦除层已消失（撤销/删除 ✓）⇒ 把底图恢复显示一次 ✓（配合合成里"只隐藏一次"的改动 ✓）
            # 2026-10-12（D35 收尾）：绘制期**不得**改可见性 ✗ ⇒ 这里只**排一次**延迟恢复 ✓
            # （恢复显示不涉及本帧像素 ⇒ 推迟一轮视觉上等价 ✓）。
            if not self._base_restore_pending:
                self._base_restore_pending = True
                QTimer.singleShot(0, self._restore_base_visibility)
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
        if self.tool == "eraser" and (self.eraser_point_raw or self.eraser_point) is not None:
            color = QColor(self.settings.get("eraser_cursor_color", "#ff8c00"))
            painter.setPen(QPen(color, 1.5, Qt.SolidLine))
            painter.setBrush(Qt.NoBrush)
            radius = self.settings.get("eraser_width", self.settings["pen_width"]) / 2
            # 圆心用**真实指针**（未夹取 ✓）⇒ 指针移出画布时圆环照常跟着走 ✓（此前会贴在画布边缘 ✗）
            painter.drawEllipse(self.eraser_point_raw or self.eraser_point, radius, radius)
        # 马赛克涂抹模式：同样用虚线圆环表示笔刷宽度，便于对齐涂抹范围。
        if self.tool == "mosaic" and (self.mosaic_point_raw or self.mosaic_point) is not None \
            and self.settings.get("mosaic_brush", True):
            color = QColor(self.settings.get("mosaic_cursor_color", "#00c853"))
            painter.setPen(QPen(color, 1.5, Qt.SolidLine))
            painter.setBrush(Qt.NoBrush)
            radius = max(2, self.settings.get("mosaic_width", 20) / 2)
            painter.drawEllipse(self.mosaic_point_raw or self.mosaic_point, radius, radius)
        mosaic_brush_active = (
            self.tool == "mosaic" and self.settings.get("mosaic_brush", True))
        # 2026-10-11：橡皮擦的「框选」模式同样要显示拖动示意框 ✓（关闭涂抹时 ✓）
        eraser_rect_active = (
            self.tool == "eraser" and not self.settings.get("eraser_brush", True))
        if (self.start is not None and self.preview_end is not None
            and not mosaic_brush_active):
            if self.tool == "eraser":
                painter.setPen(QPen(QColor(self.settings.get("eraser_cursor_color", "#ff8c00")),
                                    1, Qt.DashLine))
            elif self.tool == "mosaic":
                painter.setPen(QPen(QColor("#00ad91"), 1, Qt.DashLine))
            else:
                painter.setPen(self.stroke_pen())
            area = QRectF(self.start, self.preview_end).normalized()
            if self.tool in ("rect", "crop", "mosaic") or eraser_rect_active:
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
        # 选中框与手柄在任何工具下都绘制：编辑已有标注不依赖工具状态。
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
        # 2026-10-11（用户要求删除对齐吸附）：原先这里会画"对齐参考虚线" ✗ —— 随吸附功能一起移除 ✓。

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
        get_logger().debug("标注旋转开始")
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

    def _mosaic_overlay_key(self, path):
        return (QPainterPath(path), self.settings.get("mosaic_width", 20),
                self.settings.get("mosaic_mode", "blur"), self.settings.get("mosaic_size", 10),
                QRectF(self.sceneRect()))

    def _update_mosaic_brush_preview(self):
        self.mosaic_preview = (self._mosaic_brush_overlay(self.mosaic_drawing)
                               if self.mosaic_drawing is not None else None)
        self._mosaic_preview_key = (self._mosaic_overlay_key(self.mosaic_drawing)
                                    if self.mosaic_preview is not None else None)
        self._mosaic_preview_source = self.image if self.mosaic_preview is not None else None

    def _commit_mosaic_brush(self, path):
        """提交与实时预览相同的非破坏性马赛克/模糊覆盖。"""
        reuse = (self.mosaic_preview is not None and self._mosaic_preview_source is self.image
                 and self._mosaic_preview_key == self._mosaic_overlay_key(path))
        overlay = self.mosaic_preview if reuse else self._mosaic_brush_overlay(path)
        self._mosaic_preview_key = None
        self._mosaic_preview_source = None
        if overlay is None:
            return
        bounds, out = overlay
        get_logger().debug("马赛克笔刷提交：复用预览=%s 尺寸=%sx%s", reuse,
                                             bounds.width(), bounds.height())
        item = editable(AnnotationPixmapItem(QPixmap.fromImage(out)))
        item.setOffset(QPointF(bounds.left(), bounds.top()))
        self._add_annotation(item)
        self.checkpoint()

    def _materialize_deferred_pen(self, point):
        """规则 1：指针移动超过阈值时，把“按下时只记起点”的画笔/马克笔真正落笔。

        返回 True 表示本次已开始绘制（调用方应继续按普通绘制流程处理）。
        """
        start = getattr(self, "_deferred_pen", None)
        if start is None:
            return False
        if (abs(point.x() - start.x()) + abs(point.y() - start.y())) < 3:
            return False
        self._deferred_pen = None
        self.straight_drawing = bool(getattr(self, "_deferred_straight", False))
        if self.straight_drawing:
            self.drawing = QPainterPath()
            self.committed_segments = []
        else:
            self.drawing = QPainterPath(start)
        return True

    def _materialize_deferred_pen_at(self, event):
        """鼠标移动时把「延迟落笔」的画笔/马克笔物化（规则 1）。"""
        if getattr(self, "_deferred_pen", None) is None:
            return
        self._materialize_deferred_pen(self.mapToScene(event.position().toPoint()))

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
        # 按下这一刻定格手柄状态（手柄在标注区域内，必须先定手柄再判本体）。
        # D13 留痕（2026-10-11，移到**最前面**）：左键一按下就记下三道门的原始值 —— 门开没开都看得见。
        # 之前放在分支内部 ⇒ 门没开就完全没有日志（排查白跑一轮）。
        if event.button() == Qt.LeftButton:
            _press_point = self.mapToScene(event.position().toPoint())
            _selected = self.scene_data.selectedItems()
            get_logger().debug(
                "左键按下: tool=%s 选中=%d 旋转手柄=%s 缩放手柄=%s",
                self.tool, len(_selected),
                [self.rotation_handle_at(i, _press_point) for i in _selected],
                [self.resize_handle_at(self.item_resize_handles(i), _press_point)
                 for i in _selected])
        self.note_active_handle(event.globalPosition().toPoint())
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
        # 方案甲：处在【编辑态】（已被选中）的标注，任何工具下都能直接拖动；
        # 未选中的标注仍归当前工具落笔 —— 所以「在已有标注上画新图形」不受影响。
        # 注：曾试过「按在任意已有标注上就拖动」，那会压住画图（-k draw 4 失败 1 错误），已废弃。
        # 2026-10-11（用户实测 + 按下留痕 tool=rect 选中=1 旋转手柄=[True] 却"跟着鼠标移动"）：
        # 非选择工具下，旋转/缩放手柄压在已选中标注的包围盒上 ⇒ annotation_at() 命中它 ⇒
        # 下面的"拖动标注"分支**抢先 return**，旋转逻辑永远到不了 ✗。
        # 因此先在**手柄**上判定：点在旋转/缩放手柄上时，绝不当成"拖动标注"。
        _handles_hit = False
        if event.button() == Qt.LeftButton and self.scene_data.selectedItems():
            _sel = self.scene_data.selectedItems()
            _handles_hit = any(
                self.resize_handle_at(self.item_resize_handles(i), point) is not None
                or (len(_sel) == 1 and self.rotation_handle_at(i, point)) for i in _sel)
        if (not _handles_hit and self.tool != "select" and event.button() == Qt.LeftButton
                and self.scene_data.selectedItems()):
            hit = self.annotation_at(point)
            if hit is not None and hit.isSelected():
                self._tool_item_drag = True
                super().mousePressEvent(event)
                event.accept()
                return
        # 控制点优先：任何工具下按在【选中标注的控制点/旋转手柄】上都进入缩放/旋转，
        # 不落笔（用户 2026-10-10 反馈：准备拖手柄结果画了一笔）。
        # 只放开「控制点」这一条：空白处框选仍只属于选择工具（上次整体放开的教训）。
        handle_hit = False
        if self.tool != "select" and event.button() == Qt.LeftButton:
            selected = self.scene_data.selectedItems()
            for item in selected:
                if self.resize_handle_at(self.item_resize_handles(item), point) is not None:
                    handle_hit = True
                    break
                if len(selected) == 1 and self.rotation_handle_at(item, point):
                    handle_hit = True
                    break
        if event.button() == Qt.LeftButton and (self.tool == "select" or handle_hit):
            self.selection_area = None
            self.rotating = None
            self.viewport().update()
            selected = self.scene_data.selectedItems()
            # D13 留痕（2026-10-11 用户反馈"按住旋转手柄却移动了整个标注"）：一次按下即可看清是
            # 哪道门挡下的 —— ① 没进这个分支（tool 非 select 且 handle_hit=False）；② 进了但选中数≠1；
            # ③ 旋转命中为 False（命中区太小）；④ 缩放手柄抢先命中。
            get_logger().debug(
                "按下判定: tool=%s handle_hit=%s 选中=%d 缩放手柄=%s 旋转手柄=%s",
                self.tool, handle_hit, len(selected),
                [self.resize_handle_at(self.item_resize_handles(i), point) for i in selected],
                [self.rotation_handle_at(i, point) for i in selected])
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
                    self._apply_handle_cursor()
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
                # 2026-10-11（用户要求：橡皮擦对齐马赛克 ✓）：起点要记下来 ——
                # 涂抹模式下 Ctrl/Alt 直线擦除要用它做固定端点 ✓；框选模式下它是矩形的一角 ✓。
                self.start = point
                # 落笔时**锁定**直线模式：QTest.mouseMove / 真机快速移动都可能不带修饰键 ✓，
                # 只靠移动事件的 modifiers 会漏判（其它工具同样在落笔时定格 ✓ 保持一致）。
                self.straight_drawing = self._straight_gesture_active(event)
                self._eraser_layer_locked = True     # 拖动期间锁住标注层缓存 ✓（见 _bump_annotation_layer_stamp）
                self.eraser_group += 1          # 本次拖动自成一组擦除笔迹
                if self.settings.get("eraser_brush", True):
                    self.erase_segment(point, point)    # 涂抹模式：落笔即一个点 ✓
                # 框选模式：落笔不擦任何东西，松手时整块擦除 ✓（见 mouseReleaseEvent ✓）
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
            # 在空白处起笔 ⇒ 先退出所有标注的编辑态（与选择工具点空白一致）。
            # 只在需要拖出形状的工具上做：文字/序号/取色/橡皮/马赛克是“单击生效”，语义不同。
            if (self.tool in ("pen", "marker", "rect", "ellipse", "arrow")
                    and self.annotation_at(point) is None
                    and self.scene_data.selectedItems()):
                self.scene_data.clearSelection()
                self.viewport().update()
            # 多段绘制进行中：保留上一终点作为本段起点，不从按下点重置。
            if not (self.chain_active and self._chain_tool() and self.start is not None):
                self.start = point
            self.preview_end = point
            if self.tool in ("pen", "marker"):
                self.straight_drawing = self._straight_gesture_active(event)
                if self.chain_active:
                    # 连笔模式不做延迟：起笔点就是链的续点，语义与单笔不同。
                    if self.straight_drawing:
                        self.drawing = QPainterPath()
                    else:
                        self.drawing = QPainterPath(self.start)
                    return
                # 规则 1（用户 2026-10-10）：点击=编辑、拖动=画图。
                # 先不落笔，只记住起点；指针移动超过阈值才真正开始画。
                # 这样单击/双击已有标注 ⇒ 进入编辑态，且不会留下点状笔迹把选中顶掉。
                self._deferred_pen = point
                self._deferred_straight = self.straight_drawing
                event.accept()
                return
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        # 2026-10-12：鼠标真的动了 ⇒ 若指针已**不在**按键定格的那个手柄上，才解除定格 ✓
        #   （避免粘在旧手柄上 ✗，同时不误伤「指针跟随」把指针放到**同一个手柄**上的情形 ✗）。
        # 判据只看「指针是否还在定格手柄的命中圈内」✓ —— 不看时间 ✗（此前用 50ms 定时器判"程序移动"
        #   真机按键间隔可达 500ms ⇒ 定时器早已过期 ⇒ 合成移动被当成用户移动 ⇒ 清掉定格 ✗
        #   ⇒ 下一次按键重判悬停、光标已偏出命中圈 ⇒ 掉进 nudge_selected ⇒ 标注被整体平移、
        #   指针按 (dx,dy) 走 ⇒ 用户看到的「按键后位移很抖、受控位置会变」✗ 真机日志即此 ✓）。
        # 顺序：先消费程序移动标记（避免它泄漏到下一次真实移动 ✓），再决定是否解除定格 ✓。
        if self._consume_programmatic_cursor_move(event):
            event.accept()
            return
        # 2026-10-12（真机日志定位 ✓）：**按住左键期间绝不解除定格** ✓ —— 这就是"抓着手柄微调"的语义
        #   （用户按下手柄后按住不放 + 连按方向键 ✓）。日志实证：按住期间一旦清掉定格 ✗，
        #   按键就掉进 `nudge_selected`（移动标注本体 ✓）并把指针按 (dx,dy) 带走 ✗
        #   ⇒ 标注每按一次平移 1px、指针跟着走、**再也回不到手柄上** ⇒ 用户看到的"抖/不同步/越走越偏" ✗
        #   （日志：`标注手柄=False` + `指针跟随：手柄=None 位移=(-1,0)` ✓）。
        #   只有**松开左键后**（纯悬停语境 ✓）才按"指针是否仍在同一定格手柄上"决定是否清除 ✓。
        if (not (QApplication.mouseButtons() & Qt.LeftButton)
                and not self._pointer_on_active_handle()):
            self.clear_active_handle()
        self._materialize_deferred_pen_at(event)
        # 2026-10-11（用户实测：按住旋转按钮拖动时标注"跟着鼠标移动"而不是旋转）：
        # mousePressEvent 已经调过 _begin_rotation()（按下留痕显示 旋转手柄=[True] ✓），
        # 但移动路径里"拖动标注本体"的分支抢先 ⇒ 标注被搬走 ✗ ⇒ 这里把**旋转分支提到最前**，
        # 旋转期间直接 return，绝不落到拖动/平移分支（D17：不要删别的守卫，只调优先级）。
        if self.rotating is not None:
            point = self.mapToScene(event.position().toPoint())
            before = self.rotating.rotation()
            self._rotate_to(point)
            if abs(self.rotating.rotation() - before) > 0.01:   # 低频：只在角度真变化时记（规则 14）
                get_logger().debug(
                    "旋转中：角度 %.1f → %.1f（位置未变）",
                    before, self.rotating.rotation())
            event.accept()
            return
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
            # 框选不走 image_point：允许拖到画布外，也避免画布外的点被夹成边缘那一点
            # （2026-10-11：用户要求「标注要夹、框选和菜单不要夹」）。
            self.selection_end = self.mapToScene(event.position().toPoint())
            self.viewport().update()
            event.accept()
            return
        if self.tool == "eraser":
            point = self.image_point(self.mapToScene(event.position().toPoint()))
            # 2026-10-11 修复（用户反馈："橡皮擦/马赛克的鼠标会被吸附在屏幕边缘与画布边缘，无法快速
            # 移动到中间" ✗）：`image_point()` 按设计把**几何**夹到画布边缘 ✓（见它的文档 ✓），
            # 而光标圆环也用了那个夹取后的点 ✗ ⇒ 指针移出画布后圆环贴在边缘不动 ✓。
            # 这里单独记一份**未夹取**的真实指针点，只给圆环绘制用 ✓（几何仍走夹取后的 point ✓）。
            self.eraser_point_raw = self.mapToScene(event.position().toPoint())
            previous_point = self.eraser_point
            self.eraser_point = point
            # 2026-10-11（用户要求：橡皮擦对齐马赛克 ✓）：
            #   · 涂抹模式（eraser_brush 默认 True ✓）：沿笔迹擦除；**按住 Ctrl 或 Alt ⇒ 直线擦除** ✓
            #     （直线时先把本组上一次的橡皮筋线丢掉再重画"起点→当前点" ✓ 松手即定稿 ✓）
            #   · 框选模式（关闭涂抹 ✓）：拖动只更新示意框，**松手时**在 mouseReleaseEvent 里整块擦除 ✓
            if self.erasing and not self.settings.get("eraser_brush", True):
                # 框选模式：更新示意框（松手时整块擦除 ✓）
                self.preview_end = point
            if not self.erasing and self._eraser_layer_locked:
                self._eraser_layer_locked = False      # 兜底：没有走 release 也要解锁 ✓
            if self.erasing and self.settings.get("eraser_brush", True):
                if (self.straight_drawing or self._straight_gesture_active(event)) \
                        and self.start is not None:
                    # 2026-10-12（用户要求："性能部分回退、保留划直线" ✓）：
                    # 恢复为性能优化**之前**的简单实现 —— 丢掉本组已画的橡皮筋线 ✓、重画"起点→当前点" ✓、
                    # 然后**无条件整视口重绘** ✓（与记号笔按住 Ctrl 的直线做法一致 ✓ ⇒ 一定画得出去 ✓）。
                    # 快照/强制脏区/诊断留痕等性能与排查手段**全部移除** ✓（它们正是"只有一小段显示"的来源 ✗）。
                    for _erase_item in self.erase_items():
                        _erase_item.drop_groups({self.eraser_group})
                    self.erase_segment(self.start, point)
                    self.viewport().update()
                else:
                    self.erase_segment(self.eraser_last, point)
                self.eraser_last = point
            # 2026-10-12：性能优化已回退（用户要求 ✓）⇒ 恢复整视口重绘 ✓（与自由涂抹的其它工具一致 ✓）
            self.viewport().update()
            return
        if self.tool == "mosaic" and self.settings.get("mosaic_brush", True):
            # 未按住时也跟随指针，用虚线圆环显示笔刷宽度与位置。
            # 2026-10-11：圆环要用**未夹取**的真实指针点 ✓（否则指针移出画布后圆环贴在画布边缘 ✗）。
            self.mosaic_point_raw = self.mapToScene(event.position().toPoint())
            point = self.image_point(self.mosaic_point_raw)
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
                    if self.drawing is not None:   # 延迟落笔期间 drawing 仍为 None（规则 1）
                        self.drawing.lineTo(end)
            self.viewport().update()
        if self.start is None:
            super().mouseMoveEvent(event)
            # 2026-10-11（用户要求删除）：这里曾调用 `_update_alignment_guides()` —— 拖动标注时把边缘/中心
            # 吸附到画布边或其它的标注（阈值 6px ✓）⇒ 表现为"稍微动一下鼠标就被吸住" ✗。已整块移除 ✓。
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
        if self._apply_handle_cursor():
            QToolTip.hideText()
            return
        self._applied_cursor_key = None
        point = self.mapToScene(position)
        if not self.sceneRect().contains(point):
            self._reset_hover_cursor()
            QToolTip.hideText()
            return
        # 任何工具下都要能编辑选中标注：悬停即显示手柄与提示，不再要求先切到选择工具。
        for item in self.scene_data.selectedItems():
            if self.rotation_handle_at(item, point):
                self.setCursor(self.rotation_cursor)
                QToolTip.showText(
                    self.viewport().mapToGlobal(position),
                    # 用户 2026-10-11：手柄提示太长 ⇒ 按语义换行（纯文本 \n，规则 9）。
                    "拖动旋转按钮旋转标注\n"
                    "按住方向键或 WASD 也可旋转（↑/→ 顺时针，↓/← 逆时针，每次 1°）",
                    self)
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
                            "拖动左右边中点调整文本框宽度（文字自动换行）\n"
                            "四角仍是缩放\n"
                            "握住手柄后用方向键或 WASD 等比微调；按旋转后的方向，向外放大、向内缩小",
                            self)
                    else:
                        self.setCursor(cursor)
                        QToolTip.showText(
                            self.viewport().mapToGlobal(position),
                            "拖动上下边中点调整文本框高度（超出部分裁剪）\n"
                            "四角仍是缩放\n"
                            "握住手柄后用方向键或 WASD 等比微调；按旋转后的方向，向外放大、向内缩小",
                            self)
                else:
                    self.setCursor(cursor)
                    QToolTip.showText(
                        self.viewport().mapToGlobal(position),
                        "拖动控制点等比缩放\n"
                        "按住 Ctrl / Alt / Shift / Space 任意键可自由拉伸变形\n"
                        "握住手柄后用方向键或 WASD 等比微调（以标注中心为基准）；按旋转后的方向，向外放大、向内缩小",
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
        self.eraser_point_raw = None
        self.mosaic_point_raw = None
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
        # 原地编辑（截图内联）时画布挂在【置顶遮罩】之下，普通 QDialog 会被遮罩挡住看不见：
        # 统一置顶 + 显式抬升/激活；独立编辑器下同样正确（对话框在其窗口之上）。
        dialog.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        dialog.raise_()
        dialog.activateWindow()
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
        # 只选中目标，不把工具切成“选择”—— 工具状态只由用户主动点工具栏改变
        # （用户 2026-10-10 要求：画标注是常态，编辑已有标注不该顺带切走工具）。
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
        # 同样不切换工具：擦除菜单与当前工具无关。
        self.erase_menu(point).popup(position)

    def mouseReleaseEvent(self, event):
        self._cursor_follow_generation = getattr(self, "_cursor_follow_generation", 0) + 1
        self._clear_programmatic_cursor_move()
        if getattr(self, "_deferred_pen", None) is not None:
            # 规则 1（用户 2026-10-10）：按下后从未移动 ⇒ 这是一次点击 ⇒
            # 让指针下的已有标注进入编辑态（选中）；不落笔，因此不会有点状笔迹顶掉选中。
            point = self._deferred_pen
            self._deferred_pen = None
            self.start = None
            self.preview_end = None
            item = self.annotation_at(point)
            # 点击空白 ⇒ 退出所有标注的编辑态；点击某个标注 ⇒ 只保留它（其它一律退出）。
            self.scene_data.clearSelection()
            if item is not None:
                item.setSelected(True)
            self.viewport().update()
            event.accept()
            return
        # 松开才允许更新手柄状态。
        self.clear_active_handle()
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
            get_logger().debug("标注旋转结束")
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
        if (self.tool == "eraser" and self.erasing
                and not self.settings.get("eraser_brush", True)):
            # 2026-10-11：橡皮擦「框选」模式（涂抹关闭 ✓）⇒ 松手时把拖出的矩形**整块擦除** ✓
            end = self.image_point(self.mapToScene(event.position().toPoint()))
            if self.start is not None:
                rect = QRectF(self.start, end).normalized()
                if rect.width() >= 1 and rect.height() >= 1:
                    self._erase_stroke_item().add_rect(
                        self.eraser_group, rect,
                        self.settings.get("eraser_erase_base", False))
                    self.erase_mask_dirty = True
                    self.viewport().update()
            self.erasing = False
            self._eraser_layer_locked = False      # 松手解锁标注层缓存 ✓
            self.eraser_last = None
            self.start = None
            self.preview_end = None          # 清掉框选示意框 ✓
            self.eraser_point = None
            return
        if self.tool == "eraser" and self.erasing:
            # 直线模式（Ctrl/Alt ✓）在移动过程中已经把"起点→当前点"画好了 ⇒ 松手**不再补一段** ✗
            # （否则会多出一条从"上一次指针位置"到终点的自由笔迹 ✓ 用例实测为 2 段而不是 1 段 ✗）
            if not self.straight_drawing:
                self.erase_segment(self.eraser_last, self.mapToScene(event.position().toPoint()))
            self.straight_drawing = False
            self.erasing = False
            self._eraser_layer_locked = False      # 松手解锁标注层缓存 ✓
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
        if self.tool == "select" or getattr(self, "_tool_item_drag", False):
            self._tool_item_drag = False
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
        # 其他工具先切到选择；双击**文字**标注就地编辑，双击**非文字**标注只进入编辑态（选中）✓
        # 2026-10-12（用户要求）：**去掉"双击删除标注"** ✗ —— 易误点且不稳定 ✓；
        # 删除仍走明确入口：Delete 键 / 右键菜单「删除标注」（两者都不变 ✓）。
        if event.button() != Qt.LeftButton:
            event.accept()
            return
        point = self.mapToScene(event.position().toPoint())
        # 命中标注而非擦除层：被擦过的文字/标注仍可双击编辑或删除。
        item = self.annotation_at(point)
        if item is not None:
            if isinstance(item, AnnotationTextItem):
                # 双击文字直接进编辑框：任何工具下都成立（编辑不依赖工具状态）。
                self.edit_text_item(item)
                self._update_resize_cursor(event.position().toPoint())
                return
            if self.tool != "select":
                # 方案甲（用户 2026-10-10）：双击任意标注即让它进入【编辑态】——编辑态由标注
                # 自身的选中状态维护，不依赖全局工具；工具保持不变（可继续画）。
                # 双击的第一拍会先落一个点状笔迹（画笔/马克笔），先撤掉它，
                # 否则新笔迹会顶掉刚建立的选中，表现为“双击没能进入编辑态”。
                # 注：这里曾试过先 undo() 撤掉第一拍的点状笔迹，但 undo 会按历史重建场景，
                # 对象身份失效（选中态落不到用户看到的那个标注上），已回退。
                # 规则 1（点击=编辑、拖动=画图）需要“延迟落笔”，尚未实现，见 README/待办。
                self.scene_data.clearSelection()
                item.setSelected(True)
                self.viewport().update()
                self._update_resize_cursor(event.position().toPoint())
                return
            # 双击非文字标注 ⇒ 只选中它（进入编辑态）✓ 与其它工具完全一致 ✓ 不再删除 ✗。
            self.scene_data.clearSelection()
            item.setSelected(True)
            self._update_resize_cursor(event.position().toPoint())
            self.viewport().update()
            return
        self.confirmed.emit()

    def active_handle(self):
        """当前按住的手柄名称（按下时定格，松开前不变）；没有则 None。

        手柄通常就在标注区域内，所以判定顺序必须是「先手柄、后本体」；
        又因为按下事件可能落在画布上（不是遮罩），状态统一由画布在按下时记录。
        """
        return getattr(self, "_active_handle_name", None)

    def note_active_handle(self, global_pos):
        """按下时定格手柄状态（由 mousePressEvent 调用）。"""
        hit = self.pointer_handle(global_pos)
        self._active_handle_name = hit[1] if hit else None
        return self._active_handle_name

    def clear_active_handle(self):
        """松开时清除手柄状态。"""
        self._active_handle_name = None

    def _pointer_on_active_handle(self):
        """指针是否**仍在按键定格的那个手柄**上（同一手柄、同一 8px 命中圈 ✓）。

        2026-10-12（真机日志定位 ✓）：这是"受控位置有没有变"的正确判据 ✓ —— 用它取代
        「程序移动标记 + 50ms 定时器」那套时间判据 ✗（真机按键间隔可达 500ms ⇒ 定时器早已过期
        ⇒ 合成的指针移动被当成用户移动 ⇒ 清掉定格手柄 ⇒ 下一次按键掉进 nudge_selected ✗）。
        · 指针还在同一定格手柄（含"指针跟随"把指针放回该手柄 ✓）⇒ True ⇒ 保留定格 ✓；
        · 指针换到别的/没有手柄（用户真移动了 ✓）⇒ False ⇒ 由调用方清除定格 ✓。
        """
        name = self.active_handle()
        if not name:
            return False
        hit = self.pointer_handle(QCursor.pos())
        return bool(hit) and hit[1] == name

    def pointer_handle(self, global_pos=None):
        """返回指针下的“选中标注”手柄 (item, 名称)，没有则 None。

        名称取 nw/n/ne/e/se/s/sw/w，或 "rotate" 表示旋转手柄；
        复用画布自身的命中阈值（8 像素），与悬停光标那套完全同源。
        """
        if global_pos is None:
            return None
        # 2026-10-12（真机日志定位 ✓）：必须用 **viewport** 的局部坐标 ✗ —— `mapToScene` 期望的是
        #   viewport 坐标 ✓，而 `self.mapFromGlobal` 给的是**整个 QGraphicsView** 的坐标（含边框/边距 ✓）
        #   ⇒ 二者差 1~2px ⇒ 指针明明压在手柄上却判成"没压着" ✗ ⇒ 定格/悬停命中落空
        #   ⇒ 按键掉进"移动标注本体"分支（= 用户看到的"抖/越走越偏"✗）。
        point = self.mapToScene(self.viewport().mapFromGlobal(global_pos))
        for item in self.scene_data.selectedItems():
            if self.rotation_handle_at(item, point):
                return (item, "rotate")
            for name, spot in self.item_resize_handles(item).items():
                if (spot.x() - point.x()) ** 2 + (spot.y() - point.y()) ** 2 <= 64:
                    return (item, name)
        return None

    def pointer_on_handle(self, global_pos=None):
        """指针是否压在“选中标注”的手柄上（含旋转手柄）。"""
        return self.pointer_handle(global_pos) is not None

    def rotate_selected(self, direction):
        """按住旋转手柄时用方向键旋转：direction=+1 顺时针、-1 逆时针（每次 1 度）。

        以标注包围盒中心为轴心（旋转后中心不漂）；旋转后其余手柄方向跟着走，
        紧接着的缩放微调也会以「当前实际朝向」为准（见 resize_selected 的场景逆变换）。
        """
        items = self.scene_data.selectedItems()
        if not items:
            return False
        changed = False
        for item in items:
            before = item.mapToScene(item.boundingRect().center())
            item.setRotation(item.rotation() + 1.0 * direction)
            after = item.mapToScene(item.boundingRect().center())
            item.setPos(item.pos() + before - after)
            changed = True
        if changed:
            self.constrain_selected_to_canvas()   # block 模式：旋转后立刻拉回画布内 ✓（clip 模式不变 ✓）
            # 2026-10-12（用户要求 ✓）：**旋转微调不动鼠标** ✗
            # 此前这里也调了跟随 ✗ ⇒ 指针一动就会被判成拖动、把标注「拖」走 ✓（= 用户看到的"旋转时标注也移动了" ✓）。
            # 缩放微调仍保留跟随 ✓（那是用户明确要的 ✓）。
            self.checkpoint()
            self.update()
        return changed

    def resize_selected(self, dx, dy):
        """按定格手柄的可见朝外方向等比微调，中心不变，步长包含视图和图元变换。

        中点响应旋转后的主屏幕轴；四角响应有径向投影的方向键。
        鼠标相对标注的内外位置不参与方向判定。
        """
        name = self.active_handle() or ""
        items = self.scene_data.selectedItems()
        if not items:
            return False
        left = name in ("nw", "w", "sw")
        right = name in ("ne", "e", "se")
        top = name in ("nw", "n", "ne")
        bottom = name in ("sw", "s", "se")
        if not (left or right or top or bottom):
            # 没有手柄信息时（例如单元测试直接调用）按“右下角”处理：向右下放大。
            right = bottom = True
        # 键盘方向 → 手柄上的增量在循环内按【每个标注当前的旋转变换】换算（见下方 local）。
        # 全部手柄都等比：角标与四个边中点，任何方向键 ⇒ 以中心为基准等比缩放（长宽比恒定）。
        # （2026-10-10 用户确认：早期「边中点只拉一个轴」的说法作废。）
        proportional = True
        changed = False
        for item in items:
            bounds = item.boundingRect()
            if bounds.width() < 2 or bounds.height() < 2:
                get_logger().debug(
                    "手柄键盘缩放：跳过过小的标注 %s", type(item).__name__)
                continue
            _outward = self.HANDLE_DIRECTIONS.get(name)
            if _outward is None:
                # 没有手柄信息时（单元测试直接调用等）退回"按键方向 = 缩放方向"（原来那套 ✓）。
                _proj = 1.0 if (dx if dx else dy) > 0 else -1.0
            else:
                _dir = (self.item_resize_handles(item)[name]
                        - item.mapToScene(bounds.center()))
                if len(name) == 1:
                    horizontal = abs(_dir.x()) > abs(_dir.y()) + 1e-9
                    if (horizontal and not dx) or (not horizontal and not dy):
                        continue
                _proj = dx * _dir.x() + dy * _dir.y()
                if abs(_proj) < 1e-9:
                    continue
            _sign = 1.0 if _proj > 0 else -1.0
            step_x = (max(abs(dx), abs(dy)) * _sign) if (left or right) else 0.0
            step_y = (max(abs(dx), abs(dy)) * _sign) if (top or bottom) else 0.0
            if not step_x and not step_y:
                # 中点手柄的**垂直方向键** ⇒ 纯 no-op ✓：不缩放、不移动、指针不动 ✓（用户问题 2 ✓）。
                continue
            # 2026-10-12（用户反馈：每次按键视觉变化太大 ✓）：步长按**屏幕**换算 ✓
            #   画布放大显示时 1 屏幕像素 < 1 图像像素 ⇒ 每次只变一点点 ✓ 手感恒定 ✓。
            screen_transform = item.deviceTransform(self.viewportTransform())
            screen_origin = screen_transform.map(QPointF(0, 0))
            screen_width = screen_transform.map(QPointF(bounds.width(), 0)) - screen_origin
            screen_height = screen_transform.map(QPointF(0, bounds.height())) - screen_origin
            width_length = math.hypot(screen_width.x(), screen_width.y())
            height_length = math.hypot(screen_height.x(), screen_height.y())
            factor_x = factor_y = 1.0
            if proportional:
                # 2026-10-12（用户澄清 ✓）：**保持长宽比是约定** ✓ 不能改 ✗；
                #   用户说的“某个轴不动”指的是**指针/鼠标**那条 ✓（中点手柄的垂直方向键 = 纯 no-op ✓），
                #   不是“几何只动一个轴” ✗（我一度按后者改成两轴各自 factor ✗ ⇒ 破坏等比 ✗ 已回退 ✓）。
                # 2026-10-12（问题 1 根因 ✓）：曾有一段“死代码 + 独立 if step_y 覆盖”✗ ⇒ 旋转后
                #   step_x/step_y 都非零时 factor_y 被覆盖成 step_y/height ✗ ⇒ 不等比 ⇒ 光标只沿单轴 ✗。
                #   现在只保留两个互斥分支 ⇒ factor_x == factor_y 恒成立 ✓（等比 ✓）。
                if step_x:
                    factor_x = factor_y = max(0.05, 1.0 + step_x / max(width_length, 1e-9))
                elif step_y:
                    factor_x = factor_y = max(0.05, 1.0 + step_y / max(height_length, 1e-9))
            # 【中心锚点】：以标注中心为基准缩放 —— 四条边一起动（等比拉伸），中心不动。
            # 2026-10-12（用户要求 ✓）：**没有发生实际缩放就不算变化** ✓
            #   · 上下或左右中点手柄按垂直方向的键 ⇒ 形状不变 ✓ 指针也不移动 ✗（此前 changed 仍为 True ✗ ⇒ 指针白跳 ✓）
            #   · 角标手柄按任意方向键 ⇒ 两轴一起缩放 ✓ 指针按手柄新的 x 与 y 一起跟随 ✓（handle_global_point 给完整坐标 ✓）
            if abs(factor_x - 1.0) < 1e-9 and abs(factor_y - 1.0) < 1e-9:
                continue
            before = item.mapToScene(bounds.center())
            raw = item.rect() if hasattr(item, "rect") else None
            rect = QRectF(raw) if isinstance(raw, QRect) else raw
            if isinstance(rect, QRectF):
                new_w = max(2.0, rect.width() * factor_x)
                new_h = max(2.0, rect.height() * factor_y)
                center = rect.center()
                item.setRect(QRectF(center.x() - new_w / 2.0, center.y() - new_h / 2.0,
                                    new_w, new_h))
            else:
                item.setTransform(QTransform().scale(factor_x, factor_y), True)
            after = item.mapToScene(item.boundingRect().center())
            item.setPos(item.pos() + before - after)
            changed = True
            get_logger().debug(
                "手柄键盘缩放：%s 手柄=%s dx=%s dy=%s 比例=%.4f/%.4f 尺寸=%sx%s",
                type(item).__name__, name or "(默认右下)", dx, dy, factor_x, factor_y,
                round(bounds.width() * factor_x, 1), round(bounds.height() * factor_y, 1))
        if changed:
            # 2026-10-12（用户要求"标注不应该出去到画布之外" ✓）：缩放后立刻夹回画布内 ✓
            # （`constrain_selected_to_canvas()` 只在 `editor_overcanvas_mode=block`（"禁止移出"）时生效 ✓
            #   默认 clip 保持不变 ✓ —— 用户可在 设置 > 编辑器 > 标注超出画布 里切换 ✓）。
            # 位置必须在**跟随之前**：否则指针会指向一个马上被拉走的手柄位置 ✗。
            self.constrain_selected_to_canvas()
            self._rebase_resize_drag()
            self._follow_handle_cursor()      # 尺寸微调 ⇒ 指针跟随手柄新位置 ✓（旋转后同样成立 ✓）
            self.checkpoint()
            self.update()
        return changed

    def _rebase_resize_drag(self):
        item = self.resizing
        if item is None:
            return
        handles = self.item_resize_handles(item)
        opposite = {"nw": "se", "n": "s", "ne": "sw", "e": "w",
                    "se": "nw", "s": "n", "sw": "ne", "w": "e"}
        self.resize_anchor = handles[opposite[self.resize_handle]]
        self.resize_anchor_local = item.mapFromScene(self.resize_anchor)
        self.resize_start = handles[self.resize_handle]
        self.resize_transform = item.transform()
        self.resize_scale = item.scale()
        self.text_resize_origin = None
        get_logger().debug(
            "键盘缩放后更新鼠标拖拽基准：手柄=%s", self.resize_handle)

    def handle_global_point(self, name=None, precise=False):
        """当前手柄的**屏幕全局坐标**（键盘微调后让指针跟随 ✓ 两个编辑器共用 ✓）。

        2026-10-12（用户反馈："修改标注的尺寸微调时指针一直没有跟随"✗，并要求考虑**旋转后**的跟随 ✓）：
        · 旋转后手柄位置由 item 的**当前变换**决定 ⇒ 每次都按 `item_resize_handles` /
          `rotation_handle_position` 重算 ✓（绝不缓存按下时的坐标 ✗）；
        · `name` 省略时按 `active_handle()` → `pointer_handle()` 的同一套回落顺序取 ✓
          （与画布按键判定、遮罩侧判定完全同源 ✓）；
        · 画布不可见 / 没有选中标注 / 该手柄不存在 ⇒ 返回 None ✓（调用方静默跳过 ✓）。
        · `precise=True` ⇒ 返回**未取整**的 `QPointF` ✓：`mapToGlobal(QPoint)` 会把分数像素直接
          取整 ✗，而角标等比缩放每步只让角移动 (1px, 0.5px) ✓ ⇒ 取整后 y 不动 ⇒ 用户看到
          "角标手柄只有 x 动" ✗（见 `_perceptible_cursor_point` ✓）。
        """
        if not self.isVisible():
            return None
        items = self.scene_data.selectedItems()
        if not items:
            return None
        name = name or self.active_handle()
        if not name:
            hovered = self.pointer_handle()
            name = hovered[1] if hovered else ""
        if not name:
            return None
        item = items[0]
        spot = (self.rotation_handle_position(item) if name == "rotate"
                else self.item_resize_handles(item).get(name))
        if spot is None:
            return None
        local = self.viewportTransform().map(spot)
        origin = self.viewport().mapToGlobal(QPoint(0, 0))
        x, y = origin.x() + local.x(), origin.y() + local.y()
        return QPointF(x, y) if precise else QPoint(round(x), round(y))

    def _apply_cursor_follow(self, target, delta=None):
        """把指针移动到 `target`（Qt 原生全局坐标 ✓），**下一轮再读回校正一次** ✓。

        2026-10-12（用户反馈"手柄缩放时指针跟随不及时、多移动几步直接不同步" ✗）：
        原先在按键处理里**同步** `QCursor.setPos` ✗ ⇒ 此时几何/视图变换可能还没最终确认 ✓，
        且 Windows 上连续快速 setPos 可能被合并/丢弃 ✗ ⇒ 逐步累积偏差 ✓。
        现在：① 立刻设一次（手感及时 ✓）；② 推迟到**事件循环下一轮**读回 `QCursor.pos()` ✓，
        若与目标差 > 2px 就**再设一次**校正 ✓（自校正 ✓ 不掉步 ✓）。
        """
        # 2026-10-12（问题 2 根因 ✓）：**两种可见状态下都要登记**这次是程序自己移动指针 ✗
        #   —— 原先只在 `not visible` 分支登记 ✗ ⇒ 真机（画布可见）时从不登记 ⇒
        #   合成 mouseMoveEvent 按"用户移动"处理 ⇒ 清掉按键定格的手柄 ✗ ⇒ 下一次按键重判悬停、
        #   命中落空即掉进 nudge_selected ⇒ 四个方向键全生效 + 指针乱走（用户问题 2 ✓）。
        if not self.isVisible():
            return
        point = self._perceptible_cursor_point(target)
        if self.resizing is not None:
            self.resize_start = self.mapToScene(self.viewport().mapFromGlobal(point))
        self._mark_programmatic_cursor_move(point)
        generation = getattr(self, "_cursor_follow_generation", 0) + 1
        self._cursor_follow_generation = generation
        # 2026-10-12 诊断（用户要求"多加日志排查" ✓ 按键级 ✓ 非高频 ✗）：一次按键一条 ✓
        #   手柄/目标浮点/当前光标/实际落点/两轴位移 ⇒ 一眼看出"哪条轴没动、是否跳变" ✓。
        _cur = QCursor.pos()
        get_logger().debug(
            "指针跟随：手柄=%s 目标=(%.1f,%.1f) 当前=(%d,%d) 落点=(%d,%d) 位移=(%d,%d)",
            self.active_handle(), target.x(), target.y(), _cur.x(), _cur.y(),
            point.x(), point.y(), point.x() - _cur.x(), point.y() - _cur.y())
        QCursor.setPos(point)

        def correct():
            try:
                import shiboken6
                if (not shiboken6.isValid(self) or not self.isVisible()
                    or self._cursor_follow_generation != generation):
                    return
            except Exception:                 # noqa: BLE001  仅安全兜底 ✓
                return
            current = QCursor.pos()
            if (current - point).manhattanLength() > 2:
                get_logger().debug(
                    "指针跟随校正：目标=%s 实际=%s", point, current)
                self._mark_programmatic_cursor_move(point)
                QCursor.setPos(point)

        QTimer.singleShot(0, correct)

    def _perceptible_cursor_point(self, target):
        """绝对目标就近取整；亚像素位移在几何中累积，不强迫指针过冲。"""
        return QPoint(round(target.x()), round(target.y()))

    def _mark_programmatic_cursor_move(self, target):
        """登记「是程序自己把指针移到了 target」 ⇒ mouseMoveEvent 据此区分程序移动与用户移动。"""
        self._programmatic_cursor_move = True
        # 目标可能是浮点（`precise=True` ✓）⇒ 显式取整，避免依赖隐式转换 ✓（只用于留痕/兜底 ✓）。
        self._programmatic_cursor_target = QPoint(round(target.x()), round(target.y()))
        targets = getattr(self, "_programmatic_cursor_targets", [])
        targets.append(self._programmatic_cursor_target)
        self._programmatic_cursor_targets = targets[-64:]
        generation = getattr(self, "_cursor_move_generation", 0) + 1
        self._cursor_move_generation = generation
        QTimer.singleShot(50, lambda: self._clear_programmatic_cursor_move(generation))

    def _clear_programmatic_cursor_move(self, generation=None):
        """复位程序指针移动标记（50ms 兜底，或事件已被消费时立即清）。"""
        if generation is not None and generation != self._cursor_move_generation:
            return
        self._programmatic_cursor_move = False
        self._programmatic_cursor_target = None
        self._programmatic_cursor_targets = []

    def _consume_programmatic_cursor_move(self, event):
        """一次性消费近期跟随落点；真实移动取消待执行校正并继续普通拖拽。"""
        if not getattr(self, "_programmatic_cursor_move", False):
            return False
        position = event.globalPosition()
        matched = False
        for index, target in enumerate(self._programmatic_cursor_targets):
            if math.hypot(position.x() - target.x(), position.y() - target.y()) <= 0.75:
                self._programmatic_cursor_targets = self._programmatic_cursor_targets[index + 1:]
                matched = True
                if not self._programmatic_cursor_targets:
                    self._clear_programmatic_cursor_move()
                break
        if not matched:
            self._clear_programmatic_cursor_move()
            self._cursor_follow_generation = getattr(self, "_cursor_follow_generation", 0) + 1
        return matched

    def _follow_handle_cursor(self):
        """缩放/旋转微调后把指针带到手柄的**新位置** ✓（旋转后手柄会绕中心走 ⇒ 每次都重算 ✓）。"""
        point = self.handle_global_point(precise=True)   # 浮点 ⇒ 保留 0.5px 级位移（见 _perceptible_* ✓）
        if point is not None:
            self._apply_cursor_follow(point)

    def _follow_move_cursor(self, dx, dy):
        """移动微调后按同样的**屏幕位移**带动指针 ✓（画布单位 × 缩放 = 屏幕像素 ✓）。

        只在"确实按着左键"时跟随 ✓ —— 否则用户只是敲了下方向键，指针会莫名跳 1 像素 ✗
        （遮罩侧 S 通道与两个编辑器的"按住标注本体 + 方向键"都满足这个条件 ✓）。
        """
        if not self.isVisible() or not (QApplication.mouseButtons() & Qt.LeftButton):
            return
        scale = float(self.transform().m11()) or 1.0
        target = QCursor.pos() + QPoint(int(round(dx * scale)), int(round(dy * scale)))
        self._apply_cursor_follow(target)      # 同样走"下一轮读回校正" ✓（不掉步 ✓）

    def nudge_selected(self, dx, dy):
        """方向键/WASD 微调选中的标注图形（移动图形本身，不是移动选区）。

        · 中间按住标注时用它；边缘/手柄的“缩放微调”由遮罩负责，两者不要混。
        · 平移与标注自身旋转无关：旋转只影响本地尺寸与手柄方向，场景位移照旧 1 步。
        · 画布单位 = 图像像素（导出分辨率不受 dpr 影响），因此这里固定 1 步。
        """
        items = self.scene_data.selectedItems()
        if not items:
            return False
        for item in items:
            if hasattr(item, "path"):
                # 画笔/箭头这类标注的几何在 path() 里，setPos 对它不起作用（表驱动用例实测）：
                # 必须直接平移路径本身，否则「微调移动」对这些类型完全无效。
                item.setPath(item.path().translated(QPointF(dx, dy)))
            else:
                item.setPos(item.pos() + QPointF(dx, dy))
        self.constrain_selected_to_canvas()   # block 模式：微调后立刻夹回画布内 ✓（clip 模式不变 ✓）
        self._follow_move_cursor(dx, dy)      # 按住本体微调 ⇒ 指针跟着标注走 ✓
        self.checkpoint()   # 与其它编辑一致：微调可撤销
        self.update()
        return True

    def _binding_matches(self, event, key_name, default):
        """按**配置里的键位**比较当前按键（2026-10-11，审查 H1/L6）。

        背景：另存为（`editor_save_as_shortcut`）与撤销/重做原本都**硬编码** ✗ ⇒ 用户改键无效
        （实测：配置成 D 时按 D 触发 0 次、按 A 仍触发 1 次 ✗）。这里统一按配置比较。
        只用 `event.key()` / `event.modifiers()` 这类稳定 API ✓ —— **不得**用
        `event.keyCombination()` / `QKeyEvent` 序列化（D16 实测会原生崩溃 ✗）。
        取不到 settings、或配置为空/非法时返回 False（行为退化为"该键不生效"，不崩 ✓）。
        """
        settings = getattr(self, "settings", None)
        if settings is None:
            return False
        try:
            sequence = QKeySequence(str(settings.get(key_name, default)))
            if sequence.isEmpty():
                return False
            combination = sequence[0]
            return (event.key() == combination.key()
                    and event.modifiers() == combination.keyboardModifiers())
        except (TypeError, IndexError, AttributeError):
            return False

    def keyPressEvent(self, event):
        """处理删除、撤销/重做、临时平移和放弃编辑等画布快捷键。"""
        if (event.modifiers() == Qt.NoModifier and self.scene_data.selectedItems()
                and event.key() in (Qt.Key_W, Qt.Key_A, Qt.Key_S, Qt.Key_D,
                                    Qt.Key_Up, Qt.Key_Left, Qt.Key_Down, Qt.Key_Right)):
            # 选中标注时方向键/WASD 移动图形本身（遮罩侧只在未选中标注时才接管按键）。
            dx = (-1 if event.key() in (Qt.Key_A, Qt.Key_Left)
                  else 1 if event.key() in (Qt.Key_D, Qt.Key_Right) else 0)
            dy = (-1 if event.key() in (Qt.Key_W, Qt.Key_Up)
                  else 1 if event.key() in (Qt.Key_S, Qt.Key_Down) else 0)
            # 按住手柄 ⇒ 等比缩放（与原地编辑一致）；否则移动图形本身。
            # 这条分支以前只写在遮罩里，导致窗口编辑（独立编辑器）按键永远是"整体移动"。
            # 2026-10-11（用户实测：鼠标松开后手柄上下文被清空 ⇒ 方向键从"旋转"变成"移动标注"）：
            # active_handle() 是"按下时定格"的 ✗ ⇒ 这里回落到**实时悬停**判定 pointer_handle() ✓
            # （旋转手柄的 tooltip 承诺的就是"悬停在旋转按钮上按方向键也能旋转" ✓）。
            handle_name = self.active_handle() or ""
            if not handle_name:
                hovered = self.pointer_handle()
                handle_name = hovered[1] if hovered else ""
                if handle_name:
                    # 2026-10-12（用户真机定位根因）：悬停命中一旦用于**按键**判定就**定格** ✓
                    # 否则指针跟随会让命中在 手柄/边/标注内部/对角手柄 之间跳 ✗ ⇒ 手柄一变方向就翻转 ✓
                    # = 用户看到的四个键莫名全放大或全缩小 ✓；鼠标移动时清除 ✓（见 mouseMoveEvent）。
                    self._active_handle_name = handle_name
            get_logger().debug(
                "画布按键去向：手柄=%s（悬停=%s）dx=%s dy=%s 分支=%s",
                self.active_handle(), handle_name, dx, dy,
                "旋转" if handle_name == "rotate" else ("缩放" if handle_name else "移动/无手柄"))
            if handle_name == "rotate":
                # 旋转手柄 + 方向键：上和右 = 顺时针，下和左 = 逆时针（用户定义）。
                # 2026-10-12（用户报告 ✓ 窗口编辑）：旋转手柄时**旋转是权威** ⇒ 即使本步没转动
                #   （到角度极限 ✓ 或没有选中项 ✓）也必须**消费按键** ✗，绝不能再落到 nudge_selected
                #   ⇒ 否则标注会被平移 ✓ = 用户看到的“旋转时标注也跟着移动” ✗。
                spin = 1 if (dy < 0 or dx > 0) else -1
                self.rotate_selected(spin)
                event.accept()
                return
            elif self.active_handle():
                # 2026-10-12（用户要求 ✓）：按住手柄时**缩放是权威** ⇒ 即使本步没有实际缩放
                #   （例如上下中点按左右键 ✓ 左右中点按上下键 ✓）也必须**消费按键** ✗，
                #   绝不能再落到下面的 nudge_selected ⇒ 否则标注会被平移 ✓ 且指针跟着移动 ✗。
                #   （`resize_selected` 内部对"无实际缩放"已 `continue` ⇒ 不会产生撤销点 ✓）
                self.resize_selected(dx, dy)
                event.accept()
                return
            if self.nudge_selected(dx, dy):
                event.accept()
                return
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
            # 用户 2026-10-11 明确要求：**Space 一律=贴图**（两个编辑器一致），不再兼作平移；
            # 平移改用右键/中键拖动。D8 ⑤：Space 会被画布用 ShortcutOverride 吞掉，挂在上面的
            # QShortcut 收不到（全应用同键也会一并失效）⇒ 就在画布自己的 keyPressEvent 里直接发信号。
            if not event.isAutoRepeat():
                get_logger().info("画布 Space 按下 ⇒ 触发贴图")
                self.sticker_shortcut.emit()
            event.accept()
        elif event.key() == Qt.Key_S and event.modifiers() == Qt.NoModifier:
            # 2026-10-11：单键 S = 保存（由画布处理；W/A/S/D 微调分支在更上面 ⇒ 微调语境先被它消费）。
            get_logger().info("画布 S 按下 ⇒ 触发保存")
            self.save_requested.emit()
            event.accept()
        elif event.key() == Qt.Key_S and (event.modifiers() & Qt.ControlModifier):
            # Ctrl+S = 保存（2026-10-11 用户反馈原地编辑器无效）：与 A/空格 一样在画布内处理，
            # 不依赖「哪块屏 / 哪种选区入口」创建的编辑器窗口，主副屏与三种选区入口都一致。
            get_logger().info("画布 Ctrl+S 按下 ⇒ 触发保存")
            self.save_requested.emit()
            event.accept()
        elif self._binding_matches(event, "editor_save_as_shortcut", "A"):
            # 2026-10-11：另存为键**按配置**判定（默认 A；审查 H1 实测原来硬编码 ⇒ 改键无效 ✗）。
            # 上面的 W/A/S/D 分支只在"按住微调"语境生效（那时 A=左移，在这里之前就 return 了）⇒
            # 不在微调语境时才轮到这里 = 另存为；不用 QShortcut 是为了避开双语义"抢先"（D14 ✓）。
            get_logger().info(
                "画布另存为键按下 ⇒ 触发另存为（键=%s）",
                self.settings.get("editor_save_as_shortcut", "A") if getattr(self, "settings", None) else "A")
            self.save_as_requested.emit()
            event.accept()
        elif (event.key() == Qt.Key_0 and event.modifiers() & Qt.ControlModifier
              and self.wheel_zoom_enabled):
            # 复位缩放到 100%（Ctrl+0）：直接在画布内执行，避免又被吞掉。
            get_logger().info("画布 Ctrl+0 ⇒ 复位缩放到 100%%")
            self.set_zoom(100)
            event.accept()
        elif event.key() in (Qt.Key_Delete, Qt.Key_Backspace):
            self.remove_selected()
        elif self._binding_matches(event, "editor_undo_shortcut", "Ctrl+Z"):
            # 2026-10-11（审查 L6）：原为 event.matches(QKeySequence.Undo) **硬编码** ✗ ⇒ 改键后旧键
            # Ctrl+Z/Y 仍照样生效（实测 ✗）⇒ 改为按配置比较（与另存为同一助手 ✓）。
            self.undo()
            event.accept()
        elif self._binding_matches(event, "editor_redo_shortcut", "Ctrl+Y"):
            self.redo()
            event.accept()
        else:
            super().keyPressEvent(event)

    # 2026-10-11（审查 A#5）：删掉死方法 `_emit_sticker_shortcut` —— 它已无任何调用方 ✗（Space 走
    # keyPressEvent 里的 sticker_shortcut.emit() ✓ 真实路径），其"模态框/文字输入让位"守卫现在只活在遮罩侧。

    def keyReleaseEvent(self, event):
        """松开空格后退出临时平移状态。"""
        if event.key() == Qt.Key_Space:
            self.space_pressed = False
            if not self.space_pan_active:
                # 2026-10-11（审查 A#6/L3）：`space_pressed` 已**不再被置 True**（Space 统一为「贴图」 ✗ 不再
                # 用于按住拖动画布 ⇒ D14 的双语义已取消）⇒ 这里是纯状态清理，拖拽模式复位保留 ✓；
                # 保留该属性是因为既有用例（tests/test_config.py）会显式设置它，删掉会误伤 ✓。
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
            get_logger().debug("Ctrl+滚轮缩放显示比例: %d%% -> %d%%",
                                                  previous, self.zoom_percent)
        after = self.mapToScene(viewport_position)
        shift = after - before
        scale = self.transform().m11()
        self.horizontalScrollBar().setValue(
            round(self.horizontalScrollBar().value() - shift.x() * scale))
        self.verticalScrollBar().setValue(
            round(self.verticalScrollBar().value() - shift.y() * scale))

    def wheelEvent(self, event):
        """默认纵向滚动；启用滚轮缩放时 Ctrl+滚轮缩放；**Shift+滚轮横向**（2026-10-11 用户定案）。

        · Alt 不再横向（原先 Alt+滚轮横向，用户要求取消）；
        · 横向没有可滚动范围时**回落纵向**，避免"按了完全没反应"；
        · Ctrl+仅在窗口编辑器启用（原地编辑画布太小，见 wheel_zoom_enabled）。
        """
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
        horizontal_scroll = bool(modifiers & Qt.ShiftModifier)
        scrollbar = self.horizontalScrollBar() if horizontal_scroll else self.verticalScrollBar()
        if horizontal_scroll and scrollbar.maximum() <= 0:
            # 横向没有可滚动范围（图比视图窄）⇒ 回落纵向，别让按键像"没反应"（2026-10-11）。
            horizontal_scroll = False
            scrollbar = self.verticalScrollBar()
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
