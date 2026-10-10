"""全屏统一遮罩与多选区事件分发。"""

import logging
import os
import re
import time
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import (Qt, Signal, QPoint, QPointF, QRect, QRectF, QEvent, QMimeData,
                            QTimer, QSize)
from PySide6.QtGui import QKeyEvent
from PySide6.QtGui import QMouseEvent
from PySide6.QtGui import (
    QKeyEvent,QColor, QCursor, QPainter, QPainterPath, QPen, QGuiApplication,
                           QMouseEvent, QPixmap, QShortcut, QKeySequence)
from PySide6.QtWidgets import (QWidget, QApplication, QDialog, QFileDialog, QDialogButtonBox, QFormLayout, QSpinBox,
                               QFrame, QGraphicsView, QMenu, QToolButton, QLabel)

from core.dpi import DisplayMapper
from core.image_io import (matches_saved_format, normalize_save_as_path, save_as_filter,
                           save_image, saved_extension)
from core.path_utils import resolved_dir, save_as_directory
from core.screen_capture import to_qimage
from core import qimage_to_pillow
from core.window_boundaries import visible_windows
from core.window_elements import element_chain
from core.window_uia import forget_click_through
from core.window_focus import activate_window
from config.config_manager import INTRUDER_WARNING_LABELS, hint_bar_style
from logger.log_rate import log_every
from editor.annotation_canvas import AnnotationCanvas
from editor.image_effects import apply_output_effects, copy_saved_to_clipboard
from editor.toolbar_widget import ToolbarWidget, rich_tooltip
from screenshot.selection_rect import SelectionRects
from screenshot.overlay_info import (hint_visible_on_monitor, info_bar_layout,
                                     paint_info_bar, paint_info_badge)
from screenshot.magnifier_widget import (MAGNIFIER_DEFAULT_SIZE, magnifier_rect,
                                         paint_magnifier)
from screenshot.hint_items import CAPTURE_ACTION_KEYS, TOOLBAR_HIDE_KEY, hint_items, hint_texts

# 鼠标移动时悬停识别的刷新间隔由设置“window_hover_interval”控制（毫秒），避免每个移动事件都调用系统 API。

# 没抢到焦点时的全局 Esc 兜底需要系统键盘钩子；自动化测试会置为 False，避免吃掉真实按键。
ESCAPE_FALLBACK_ENABLED = os.name == "nt"

# 原地编辑两排图标条的紧凑尺寸：按钮边长与图标边长，尽量减少对截图区域的遮挡。
INLINE_BUTTON_SIZE = 24
INLINE_ICON_SIZE = 16

# 选区比某块显示器多出的容差（物理像素）。最大化窗口在取不到 DWM 可见边界时会退回
# GetWindowRect，四周多出约 8px 的不可见 resize frame；容差内直接收边到该显示器，
# 避免把同一块屏上的窗口误判成跨屏而进独立编辑器。
INLINE_SPILL_TOLERANCE = 32
# 收边还要看“保留面积占比”：只按溢出量会把“只压过来一点点”的**真正跨屏**窗口也收掉。
# 最大化窗口的不可见边框最多让选区比显示器大几个百分点，取 90% 作下限。
INLINE_TRIM_KEEP_RATIO = 0.9
# 悬停结果复用半径（物理像素）：光标还停在上次识别出的元素内、且移动不超过这个
# 距离时，直接复用高亮，不重复查询；超过则重查，避免大容器里冒出更小控件时高亮不更新。
HOVER_REUSE_RADIUS = 8
# 复用结果的最长时间：指针停住不动时不能永久沿用父级高亮（细小控件上尤其明显），
# 超过该秒数就重新查询一次，让高亮最终收敛到光标下真正的最内层控件。
HOVER_REUSE_SECONDS = 0.35

# 拖动工具栏后松手时，与候选位置的距离（曼哈顿，像素）在此以内就吸附过去。
# 拖动工具栏后松手时，与候选位置的距离（曼哈顿，像素）在此以内就吸附过去。
TOOLBAR_SNAP_DISTANCE = 24
# 工具栏左端抓取提示的宽度（像素）：拖动整条工具栏的入口，兼作视觉提示。
TOOLBAR_HANDLE_WIDTH = 10

# 采集自检：会在屏幕上长期存在、可能被一起采进画面的自身窗口。
# 类名 → 警示分类 id；分类的显示名与子开关在配置层 INTRUDER_WARNING_ITEMS 定义。
# 新增这类常驻窗口时请一并登记；遮罩自身、放大镜 HUD 与内部控件不在其中（只画在遮罩表面）。
INTRUDING_WINDOW_CATEGORIES = {
    "CaptureNotification": "notification",
    "EditorWindow": "editor",
    "SettingsWindow": "settings",
    "StickerItem": "sticker",
    "StickerPanel": "sticker_panel",
    "RecycleWindow": "recycle",
}
# 自报标签 `screensnap_self_window` → 警示分类 id；未登记的自报标签归入 other。
INTRUDING_SELF_LABEL_CATEGORIES = {"外观弹层": "appearance"}


def window_category(widget):
    """干扰窗口的警示分类 id（配置层据此决定显示名与子开关）。

    优先级：登记类名 → 自报标签映射 → 弹出菜单 → 其它本程序窗口。
    """
    category = INTRUDING_WINDOW_CATEGORIES.get(type(widget).__name__)
    if category is not None:
        return category
    reported = widget.property("screensnap_self_window")
    if reported:
        return INTRUDING_SELF_LABEL_CATEGORIES.get(reported, "other")
    if isinstance(widget, QMenu):
        return "popup"
    return "other"


def window_label(widget):
    """干扰窗口在提示条里的中文名：窗口可自报（`screensnap_self_window`），否则按分类取名。

    自报用于类名不在登记表里的本程序浮层（例如工具栏的「外观」弹层是 `QFrame`），
    否则这类窗口会被采进画面却连一句警示都没有；`QMenu` 归入「弹出菜单」分类。
    """
    return (widget.property("screensnap_self_window")
            or INTRUDER_WARNING_LABELS.get(window_category(widget), "本程序窗口"))


try:
    from shiboken6 import isValid as _cpp_alive
except ImportError:  # 理论上不会发生：PySide6 一定带 shiboken6
    def _cpp_alive(widget):
        return True


def live_widget(widget):
    """控件底层的 C++ 对象是否仍然存在。

    控件被析构后，PySide6 仍可能保留（或被别的列表引用）包装对象；对这类对象调用
    任何方法都会访问已释放内存，在绘制期间遍历窗口时会让整个进程崩掉。
    """
    return _cpp_alive(widget)


def _hover_fill_color(settings):
    color = QColor(settings.get("window_hover_color", "#168CFF"))
    opacity = settings.get("window_hover_opacity", 35)
    color.setAlpha(round(opacity * 255 / 100))
    return color


def _mask_overlay_color(settings):
    color = QColor(settings.get("mask_color", "#000000"))
    opacity = settings.get("mask_opacity", 70)
    color.setAlpha(round(opacity * 255 / 100))
    return color


class MaskSession:
    """一轮截图中的共享状态；每个显示器一个 MaskWindow 共同使用。"""

    def __init__(self, mapper):
        self.mapper = mapper
        cursor_global = mapper.native_global_to_physical_global(QCursor.pos())
        self.position = cursor_global - mapper.physical_bounds.topLeft()
        self.selection = SelectionRects()
        self.element_selected = False
        self.views = []
        self.completing = False
        self.closing = False
        self.inline_editor = None
        self.multi_select_mode = False
        self.right_capture_mode = False


class MagnifierOverlay(QWidget):
    """鼠标旁的独立放大镜窗口，叠在遮罩和原地编辑控件上方。"""

    def __init__(self, view):
        super().__init__(view, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint |
                         Qt.WindowDoesNotAcceptFocus | Qt.WindowTransparentForInput)
        self.view = view
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_OpaquePaintEvent)
        self.hide()

    def sync(self):
        view = self.view
        editor = view.session.inline_editor
        # 任意模态对话框或下拉弹出层（如取色框、字体选择下拉）打开时都隐藏放大镜，
        # 避免它作为置顶浮层盖住这些控件。
        visible = (view.isVisible() and view.settings.get("magnifier", False) and
                   view.monitor_rect.contains(view.position) and
                   QGuiApplication.modalWindow() is None and
                   QApplication.activePopupWidget() is None and
                   not (editor is not None and editor.toolbar.options_button.menu().isVisible()))
        if not visible:
            self.hide()
            return
        frame = view.magnifier_frame()
        self.setGeometry(QRect(view.mapToGlobal(frame.topLeft()), frame.size()))
        if not self.isVisible():
            self.show()
            self.raise_()
        self.update()

    def paintEvent(self, event):
        view = self.view
        if (view is None or not view.settings.get("magnifier", False) or
                not view.monitor_rect.contains(view.position)):
            return
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("white"))
        frame = view.magnifier_frame()
        painter.translate(-frame.x(), -frame.y())
        paint_magnifier(painter, view.preview, view.to_logical_point(view.position),
                        view.rect(), view.position,
                        grid=view.settings.get("magnifier_grid", False),
                        grid_color=view.settings.get("magnifier_grid_color", "#cccccc"),
                        size=view.magnifier_size())


class InfoBar(QWidget):
    """选区提示条：遮罩的**子控件**，始终抬在原地编辑画布与工具栏之上。

    过去它画在遮罩的 `paintEvent` 里，光标一移进选区就被选区预览和原地编辑画布盖住
    （画布是遮罩的子控件，天然在遮罩绘制之上）。改成子控件后与放大镜浮层一样压在
    内容之上；本身不接受鼠标事件，也不参与命中。
    """

    def __init__(self, view):
        super().__init__(view)
        self.view = view
        self.rows = []
        self._cursor_monitor_active = None
        self.warning = None
        self.style = None
        self.bar = QRect()
        self.align_right = False
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.hide()

    def sync(self):
        """按当前光标位置重算位置与内容；没有内容或不该显示时整条隐藏。"""
        view = self.view
        if not view.isVisible():
            self.hide()
            return
        # 模态对话框或下拉弹出层打开时隐藏，避免作为置顶浮层盖住这些控件。
        if QGuiApplication.modalWindow() is not None or QApplication.activePopupWidget() is not None:
            self.hide()
            return
        on_cursor_monitor = hint_visible_on_monitor(view.position, view.monitor_rect)
        if self._cursor_monitor_active != on_cursor_monitor:
            self._cursor_monitor_active = on_cursor_monitor
            logging.getLogger("screensnap").debug(
                "截图提示条屏幕归属切换: monitor=%s visible=%s",
                view.monitor_rect.getRect(), on_cursor_monitor)
        if not on_cursor_monitor:
            self.hide()
            return
        metrics = self.fontMetrics()
        warning = view.self_check_warning()
        settings = view.settings or {}
        # 传入光标（逻辑坐标）：提示条据此与放大镜一起翻转、并与放大镜的边缘直接对齐。
        bar, rows, align_right = info_bar_layout(
            metrics, view.rect(), view.magnifier_frame(), view.capture_hint_items(),
            warning=warning, per_line=view.hint_per_line(),
            cursor=view.to_logical_point(view.position),
            gap=int(settings.get("capture_hint_gap", 0) or 0))
        if bar.isEmpty():
            self.bar = QRect()
            self.hide()
            return
        self.bar, self.rows = bar, rows
        self.align_right = align_right
        self.warning = warning
        # 无警示时用普通样式；出现采集自检警示时整条换成警示样式。
        self.style = hint_bar_style(settings, warning)
        self.setGeometry(bar)
        if not self.isVisible():
            self.show()
        # 画布与工具栏是遮罩的子控件：每次都重新抬到最上层，保证提示条不被它们盖住。
        self.raise_()
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        paint_info_bar(painter, self.rect(), self.rows, self.style, self.fontMetrics(),
                       align_right=self.align_right)


class InlineEditor(QWidget):
    """贴在截图选区上的轻量编辑器；第一版只支持单屏单选区。"""

    saved = Signal(str, object)
    saved_silently = Signal(str, object)
    save_failed = Signal(str)
    close_all_requested = Signal()
    sticker_requested = Signal(object, object)
    save_as_dir_chosen = Signal(str)

    def __init__(self, view, rect, image, alternate):
        super().__init__(view)
        self.view = view
        self.rect = QRect(rect)
        self.settings = view.settings
        self.round_corners = bool(self.settings.get("editor_image_round_corners", True))
        self.corner_radius = self.settings.get("editor_image_corner_radius", 16)
        self.last_path = None
        self.suppress_save_notification = False
        self.resizing_region = False
        # 工具栏拖放与临时隐藏：都只影响本次原地编辑，不落盘。
        # 拖过就以用户放下的位置为准，选区/窗口变化触发重排时回到自动位置。
        self.toolbar_manual = False
        self.toolbar_hidden = False
        self.toolbar_dragging = False
        self.toolbar_drag_grab = None
        self.toolbar_handle = None
        self.toolbar = ToolbarWidget(self.settings["pen_color"], self.settings,
                         show_capture_actions=True)
        self.toolbar.setObjectName("inlineCaptureToolbar")
        self.toolbar.setToolTip(rich_tooltip(
            "原地编辑工具栏",
            "按住左侧抓手（或工具栏空白处）可整体拖动到不遮挡内容的位置，松手自动吸附；"
            "拖动过之后双击空白处可恢复自动位置。"))
        self.toolbar.setAutoFillBackground(True)
        for button, action in self.toolbar.command_buttons:
            if action == "close_all_editors":
                button.hide()
        self.compact_toolbar()
        self.canvas = AnnotationCanvas(image, self.settings, alternate)
        self.multi_select_shortcut = QShortcut(
            QKeySequence(self.settings.get("capture_multi_select_shortcut", "Alt+M")),
            self.canvas)
        self.multi_select_shortcut.setContext(Qt.WidgetWithChildrenShortcut)
        self.multi_select_shortcut.activated.connect(
            lambda: QTimer.singleShot(0, view.toggle_multi_select_mode))
        # 画布会用 ShortcutOverride 抢走可打印字符，故在编辑器侧再建一份窗口级快捷键。
        self.toolbar_hide_shortcut = QShortcut(
            QKeySequence(self.settings.get(*TOOLBAR_HIDE_KEY)), self.canvas)
        self.toolbar_hide_shortcut.setContext(Qt.WindowShortcut)
        self.toolbar_hide_shortcut.activated.connect(
            lambda: QTimer.singleShot(0, view.toggle_inline_toolbar))
        self.canvas.set_round_corner_preview(
            self.round_corners, self.corner_radius)
        self.canvas.setParent(view)
        self.toolbar.setParent(view)
        tool = self.settings.get("annotation_tool", "select")
        if tool not in self.toolbar.tool_buttons or tool == "crop":
            tool = "select"
        self.toolbar.tool_buttons[tool].setChecked(True)
        self.toolbar.set_tool_mode(tool)
        self.toolbar.options_button.setText("")
        self.canvas.set_tool(tool)
        self.last_color_tool = tool if tool in ("pen", "rect", "ellipse", "arrow", "marker", "text") else "pen"
        self.toolbar.set_active_tool(tool, self.last_color_tool)
        self.canvas.setDragMode(QGraphicsView.RubberBandDrag if tool == "select"
                                else QGraphicsView.NoDrag)
        self.toolbar.cursor_switch.setChecked(self.settings["cursor"])
        self.toolbar.cursor_switch.setEnabled(alternate is not None)
        self.toolbar.cursor_switch.toggled.connect(self.canvas.toggle_cursor)
        self.toolbar.cursor_switch.toggled.connect(
            lambda enabled: self.set_annotation_setting("cursor", enabled))
        self.canvas.setFrameShape(QFrame.NoFrame)
        self.canvas.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.canvas.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.canvas.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.canvas.setDragMode(QGraphicsView.NoDrag)
        # 第一版原地编辑先禁用图像旋转组，避免图片尺寸变化后选区和画布几何不一致。
        self.toolbar.sections[2].hide()
        self.toolbar.tool_changed.connect(self.set_tool)
        self.toolbar.color_changed.connect(self.set_pen_color)
        self.toolbar.setting_changed.connect(self.set_annotation_setting)
        self.toolbar.crop_style_changed.connect(self.set_crop_style)
        self.toolbar.command.connect(self.execute)
        self.canvas.confirmed.connect(lambda: self.execute("save"))
        # 取色、字体等模态框或下拉弹出层打开时，画布上的 Esc 由它们自身处理，不要再关掉原地编辑。
        self.canvas.cancelled.connect(
            lambda: self.discard()
            if (QGuiApplication.modalWindow() is None and
                QApplication.activePopupWidget() is None) else None)
        self.canvas.color_picked.connect(self.apply_picked_color)
        self.canvas.selection_requested.connect(self.toolbar.tool_buttons["select"].click)
        self.canvas.selected_annotation_changed.connect(self._on_selected_annotation_changed)
        self.canvas.setting_changed.connect(self.apply_canvas_setting)
        options_menu = self.toolbar.options_button.menu()
        options_menu.aboutToShow.connect(
            lambda: self.toolbar.set_selected_tool(self.canvas.selected_annotation_tool()))
        options_menu.aboutToShow.connect(self.hide_magnifiers)
        options_menu.aboutToHide.connect(lambda: QTimer.singleShot(0, self.restore_magnifiers))
        self.position_widgets()
        self.install_mouse_forwarding()

    def hide_magnifiers(self):
        for view in self.view.session.views:
            view.magnifier_overlay.hide()

    def restore_magnifiers(self):
        if self.view is not None:
            self.view.update_all()

    def install_mouse_forwarding(self):
        """转发编辑子控件上的光标位置，并让选区手柄优先交给遮罩处理。"""
        widgets = [self.canvas, self.canvas.viewport(), self.toolbar]
        widgets.extend(self.toolbar.findChildren(QWidget))
        for widget in widgets:
            widget.setMouseTracking(True)
            widget.installEventFilter(self)

    def eventFilter(self, watched, event):
        view = getattr(self, "view", None)
        if view is None or not view.inline_active():
            return super().eventFilter(watched, event)
        menu = self.toolbar.options_button.menu()
        if watched is menu or menu.isAncestorOf(watched):
            return super().eventFilter(watched, event)
        event_type = event.type()
        if event_type == QEvent.KeyPress and isinstance(event, QKeyEvent):
            hide_key, hide_default = TOOLBAR_HIDE_KEY
            hide_sequence = QKeySequence(view.settings.get(hide_key, hide_default))
            if hide_sequence.isEmpty():
                hide_sequence = QKeySequence(hide_default)
            if QKeySequence(event.keyCombination()) == hide_sequence:
                logging.getLogger("screensnap").debug(
                    "工具栏隐藏键（编辑器过滤器）触发：视图=%s",
                    view.monitor_rect.getRect())
                QTimer.singleShot(0, view.toggle_inline_toolbar)
                return True
        # 工具栏整体拖动：抓手、工具栏空白处与中间挡片都能按住拖走。
        # 只有拖过之后才把双击当"复位"，免得抢掉"双击空白=确认截图"的既有行为。
        if (watched is self.toolbar_handle or watched is self.toolbar or
                watched is getattr(self, "output_edit_spacer", None)):
            if event_type == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
                self.begin_toolbar_drag(watched.mapTo(view, event.position().toPoint()))
                return True
            if event_type == QEvent.MouseMove and self.toolbar_dragging:
                self.drag_toolbar_to(watched.mapTo(view, event.position().toPoint()))
                return True
            if event_type == QEvent.MouseButtonRelease and self.toolbar_dragging:
                self.end_toolbar_drag()
                return True
            if event_type == QEvent.MouseButtonDblClick and self.toolbar_manual:
                self.reset_toolbar_position()
                return True
        if event_type == QEvent.Resize and watched is self.toolbar:
            # 工具栏高度随排版变化，抓手需要重新贴到左内侧垂直居中。
            self.place_toolbar_handle()
        if event_type == QEvent.KeyPress and event.key() == Qt.Key_Escape:
            # 取色、字体等模态框或下拉弹出层打开时，Esc 由它们自身处理，不要再关掉原地编辑。
            if (QGuiApplication.modalWindow() is None and
                    QApplication.activePopupWidget() is None):
                view.close()
                return True
            return super().eventFilter(watched, event)
        if (event_type == QEvent.KeyPress and watched in (self.canvas, self.canvas.viewport()) and
                event.modifiers() == Qt.NoModifier and
                event.key() in (Qt.Key_W, Qt.Key_A, Qt.Key_S, Qt.Key_D,
                                Qt.Key_Up, Qt.Key_Left, Qt.Key_Down, Qt.Key_Right) and
                self.canvas.scene_data.focusItem() is None):
            view.keyPressEvent(event)
            return True
        if event_type not in (QEvent.MouseMove, QEvent.MouseButtonPress, QEvent.MouseButtonRelease):
            return super().eventFilter(watched, event)
        if watched is self.toolbar or self.toolbar.isAncestorOf(watched):
            return super().eventFilter(watched, event)
        local = watched.mapTo(view, event.position().toPoint())
        mapped = QMouseEvent(event_type, QPointF(local), QPointF(view.mapToGlobal(local)),
                             event.button(), event.buttons(), event.modifiers())
        point = view.to_physical_point(local)
        if (event_type == QEvent.MouseButtonPress and event.button() == Qt.LeftButton
                and view.picker_mode
                and QGuiApplication.keyboardModifiers() & (Qt.AltModifier | Qt.ControlModifier)):
            # 取色必须优先于"手柄/边线"判断：否则 Alt+左键会落回画布，遮罩的取色分支收不到事件
            # （日志实证：取色模式已开启，但遮罩里没有 取色取样 记录）。
            # 同一次点击会被画布与视口各转发一次：按"位置 + 时间"去重，避免取样两次（两声提示音）。
            key = (local.x(), local.y())
            now = time.monotonic()
            if (getattr(view, "_picker_forward_key", None) == key
                    and now - getattr(view, "_picker_forward_at", 0.0) < 0.2):
                logging.getLogger("screensnap").debug(
                    "取色转发去重：忽略重复点击 (%d,%d)", local.x(), local.y())
                return True
            view._picker_forward_key = key
            view._picker_forward_at = now
            logging.getLogger("screensnap").debug(
                "取色转发：控件=%s 映射局部点=(%d,%d)",
                type(watched).__name__, local.x(), local.y())
            view.mousePressEvent(mapped)
            return True
        if event_type == QEvent.MouseMove:
            view.mouseMoveEvent(mapped)
            return view.selection.resizing is not None or view.selection.dragging is not None
        if event_type == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
            if view.selection.handle_at(point) is not None or view.selection.border_at(point):
                view.mousePressEvent(mapped)
                view.grabMouse()
                return True
            if watched in (self.canvas, self.canvas.viewport()) and any(
                    rect.contains(point) for rect in view.selection.rects):
                view.position = point
                view.selection.nudge_corner = None
        elif event_type == QEvent.MouseButtonRelease and (view.selection.resizing is not None or
                                                          view.selection.dragging is not None):
            view.mouseReleaseEvent(mapped)
            if view.selection.resizing is None and view.selection.dragging is None:
                if QWidget.mouseGrabber() is view:
                    view.releaseMouse()
            return True
        return super().eventFilter(watched, event)

    def compact_toolbar(self):
        """原地编辑使用图标紧凑模式，减少对截图区域的遮挡。"""
        self.toolbar.setProperty("inline_edit", True)
        self.toolbar.tool_buttons["crop"].hide()
        for header in getattr(self.toolbar, "section_headers", []):
            header.setContentsMargins(0, 0, 0, 0)
            for index in range(header.count()):
                widget = header.itemAt(index).widget()
                if isinstance(widget, QLabel):
                    widget.hide()
                    widget.setFixedHeight(0)
        for section in self.toolbar.sections:
            section.layout().setSpacing(0)
        for grid in (self.toolbar.tool_grid, self.toolbar.edit_grid, self.toolbar.image_grid,
                     self.toolbar.output_grid):
            grid.setHorizontalSpacing(2)
            grid.setVerticalSpacing(0)
        self.toolbar.section_layout.setContentsMargins(3, 1, 3, 1)
        self.toolbar.section_layout.setHorizontalSpacing(2)
        self.toolbar.section_layout.setVerticalSpacing(1)
        self.toolbar.image_buttons = []
        self.toolbar.sections[2].hide()
        self.toolbar.sections[1].hide()
        self.apply_compact_buttons()
        self.toolbar.cursor_switch.setText("")
        self.toolbar.cursor_switch.setToolTip("显示鼠标")
        self.toolbar.cursor_switch.setAccessibleName("显示鼠标")
        self.toolbar.cursor_switch.setFixedSize(INLINE_BUTTON_SIZE, INLINE_BUTTON_SIZE)
        self.toolbar.pen_color.setText("")
        self.toolbar.pen_color.setProperty("icon_only", True)
        self.toolbar.pen_color.setFixedSize(INLINE_BUTTON_SIZE, INLINE_BUTTON_SIZE)
        self.toolbar.options_button.setText("")
        self.toolbar.options_button.setFixedSize(INLINE_BUTTON_SIZE, INLINE_BUTTON_SIZE)
        self.arrange_inline_output_edit()
        self.create_toolbar_handle()

    def create_toolbar_handle(self):
        """工具栏左端的可见抓手：原地编辑才出现，用来把整条工具栏拖开。

        比"拖任意空白处"更容易被发现；它不参与 `reflow()` 的网格（否则会被重排打乱），
        而是在 `section_layout` 左侧预留等宽内边距后贴在左上角内侧。
        """
        if self.toolbar_handle is not None:
            return self.toolbar_handle
        handle = QLabel("≡", self.toolbar)
        handle.setObjectName("inlineToolbarHandle")
        handle.setFixedSize(TOOLBAR_HANDLE_WIDTH, INLINE_BUTTON_SIZE)
        handle.setAlignment(Qt.AlignCenter)
        handle.setCursor(Qt.SizeAllCursor)
        # 只作为"边缘抓取提示"，不做成按钮：无边框、无底色、用调色板中灰色，深浅主题都合适。
        handle.setStyleSheet(
            "QLabel { background: transparent; border: none; color: palette(mid); }")
        handle.setToolTip("按住拖动移动工具栏；双击恢复自动位置")
        # 整条工具栏（含按钮之间的空白）都显示"可移动"光标，而不只是左侧抓手；
        # 内部按钮各自恢复箭头光标，避免在按钮上也提示可以拖动整条工具栏。
        self.toolbar.setCursor(Qt.SizeAllCursor)
        spacer = getattr(self, "output_edit_spacer", None)
        for child in self.toolbar.findChildren(QWidget):
            if child is handle:
                continue
            # 中间挡片本身就是拖动入口，保持可移动光标；其余子控件（按钮）恢复箭头。
            child.setCursor(Qt.SizeAllCursor if child is spacer else Qt.ArrowCursor)
        margins = self.toolbar.section_layout.contentsMargins()
        self.toolbar.section_layout.setContentsMargins(
            margins.left() + TOOLBAR_HANDLE_WIDTH, margins.top(),
            margins.right(), margins.bottom())
        self.toolbar_handle = handle
        self.place_toolbar_handle()
        return handle

    def place_toolbar_handle(self):
        """把抓手贴在工具栏左内侧并垂直居中（工具栏高度会随重排变化）。"""
        handle = self.toolbar_handle
        if handle is None:
            return
        handle.move(2, max(0, (self.toolbar.height() - handle.height()) // 2))

    def apply_compact_buttons(self):
        icon_size = QSize(INLINE_ICON_SIZE, INLINE_ICON_SIZE)
        for button in self.toolbar.findChildren(QToolButton):
            button.setToolButtonStyle(Qt.ToolButtonIconOnly)
            button.setIconSize(icon_size)
            button.setFixedSize(INLINE_BUTTON_SIZE, INLINE_BUTTON_SIZE)
            button.setMinimumWidth(INLINE_BUTTON_SIZE)
            button.setMaximumWidth(INLINE_BUTTON_SIZE)

    def arrange_inline_output_edit(self):
        """输出组和编辑组合并为一行：先输出，空一格，再显示上一步/下一步等编辑按钮。"""
        while self.toolbar.output_grid.count():
            self.toolbar.output_grid.takeAt(0)
        output_buttons = [button for button in self.toolbar.output_buttons
                          if not button.isHidden()]
        if not hasattr(self, "output_edit_spacer"):
            self.output_edit_spacer = QWidget(self.toolbar)
            self.output_edit_spacer.setFixedSize(8, INLINE_BUTTON_SIZE)
        widgets = [*output_buttons, self.output_edit_spacer,
                   *self.toolbar.edit_buttons]
        for column, widget in enumerate(widgets):
            self.toolbar.output_grid.addWidget(widget, 0, column, Qt.AlignVCenter)

    def position_widgets(self):
        # 选区/窗口变化后回到自动位置：手动拖动的位置只在本次原地编辑内有效，不落盘。
        self.toolbar_manual = False
        selection = self.view.to_logical_rect(self.rect).toRect()
        self.canvas.setGeometry(selection)
        if self.canvas.image.width and self.canvas.image.height:
            scale = min(selection.width() / self.canvas.image.width,
                        selection.height() / self.canvas.image.height)
            self.canvas.set_zoom(max(1, round(scale * 100)))
        available = min(max(300, self.view.width() - 16),
                        max(360, min(620, selection.width() + 260)))
        self.apply_compact_buttons()
        self.toolbar.sections[3].setMinimumWidth(0)
        self.toolbar.reflow(available)
        self.arrange_inline_output_edit()
        drawing, output = self.toolbar.sections[0], self.toolbar.sections[3]
        for section in (drawing, output):
            section.layout().itemAt(1).layout().invalidate()
            section.layout().invalidate()
            section.setFixedWidth(section.sizeHint().width())
        margins = self.toolbar.section_layout.contentsMargins()
        row_width = max(drawing.width(), output.width()) + margins.left() + margins.right()
        available = min(max(1, self.view.width() - 16), max(available, row_width))
        self.toolbar.section_layout.invalidate()
        self.toolbar.section_layout.activate()
        self.toolbar.sync_height()
        self.toolbar.adjustSize()
        margin = 8
        toolbar_size = self.toolbar.sizeHint().expandedTo(self.toolbar.minimumSizeHint())
        toolbar_size.setWidth(min(available, max(toolbar_size.width(), row_width)))
        toolbar_size.setHeight(max(toolbar_size.height(), self.toolbar.section_layout.sizeHint().height()))
        safe_area = self.view.rect().adjusted(margin, margin, -margin, -margin)
        selection_with_handles = selection.adjusted(-16, -16, 16, 16)
        candidates, left_x, right_x = self.toolbar_placement_candidates(
            selection, toolbar_size, margin)
        target = None
        for candidate in candidates:
            rect = QRect(candidate, toolbar_size)
            if safe_area.contains(rect) and not rect.intersects(selection_with_handles):
                target = candidate
                break
        if target is None:
            inside_bottom = QPoint(right_x, selection.bottom() - toolbar_size.height() - margin)
            inside_top = QPoint(left_x, selection.top() + margin)
            bottom_rect = QRect(inside_bottom, toolbar_size)
            target = inside_bottom if safe_area.contains(bottom_rect) else inside_top
        x = min(max(target.x(), margin), max(margin, self.view.width() - toolbar_size.width() - margin))
        y = min(max(target.y(), margin), max(margin, self.view.height() - toolbar_size.height() - margin))
        align_right = x + toolbar_size.width() // 2 > self.view.width() // 2
        alignment = (Qt.AlignRight if align_right else Qt.AlignLeft) | Qt.AlignTop
        self.toolbar.section_layout.setAlignment(drawing, alignment)
        self.toolbar.section_layout.setAlignment(output, alignment)
        self.toolbar.setGeometry(x, y, toolbar_size.width(), toolbar_size.height())
        self.toolbar.section_layout.invalidate()
        self.toolbar.section_layout.activate()
        self.place_toolbar_handle()
        logging.getLogger("screensnap").debug(
            "原地编辑工具栏布局：选区 %sx%s，工具栏 %sx%s，位置 (%s,%s)%s",
            selection.width(), selection.height(), toolbar_size.width(), toolbar_size.height(),
            x, y, "" if target is not None else "，空间不足回退到选区内部")

    def toolbar_placement_candidates(self, selection, toolbar_size, margin=8):
        """工具栏的候选摆放位置：选区外侧上下左右（近的先试），都已钳制在视图内。

        返回值同时带上选区左右两侧的 x（`left_x`/`right_x`），供"空间不足时回退到
        选区内部"与"拖动松手吸附"复用，保证自动摆位与吸附用的是同一套目标位置。
        """
        left_x = selection.left()
        right_x = selection.right() - toolbar_size.width() + 1
        first_x, second_x = (right_x, left_x) if selection.center().x() > self.view.width() // 2 else (left_x, right_x)
        box = selection.adjusted(-16, -16, 16, 16)
        raw = (
            QPoint(first_x, box.bottom() + margin),
            QPoint(second_x, box.bottom() + margin),
            QPoint(first_x, box.top() - toolbar_size.height() - margin),
            QPoint(second_x, box.top() - toolbar_size.height() - margin),
            QPoint(box.right() + margin, selection.top()),
            QPoint(box.left() - toolbar_size.width() - margin, selection.top()),
        )
        max_x = max(margin, self.view.width() - toolbar_size.width() - margin)
        max_y = max(margin, self.view.height() - toolbar_size.height() - margin)
        candidates = [QPoint(min(max(point.x(), margin), max_x),
                             min(max(point.y(), margin), max_y)) for point in raw]
        return candidates, left_x, right_x

    def apply_toolbar_visibility(self):
        """按隐藏状态显示/隐藏工具栏（隐藏是本次编辑内的临时状态）。"""
        if self.toolbar_hidden:
            self.toolbar.hide()
        else:
            self.toolbar.show()
            self.toolbar.raise_()
            self.view.magnifier_overlay.raise_()

    def toggle_toolbar(self):
        """临时隐藏/恢复工具栏，返回隐藏后的状态；只由快捷键触发，没有对应按钮。"""
        self.toolbar_hidden = not self.toolbar_hidden
        self.apply_toolbar_visibility()
        self.view.update_all()
        return self.toolbar_hidden

    def begin_toolbar_drag(self, view_point):
        """开始拖动工具栏：记住鼠标相对工具栏左上角的偏移，拖动时保持不掉手。"""
        self.toolbar_dragging = True
        self.toolbar_drag_grab = view_point - self.toolbar.pos()
        if self.toolbar_handle is not None:
            self.toolbar_handle.setCursor(Qt.ClosedHandCursor)
        self.toolbar.raise_()

    def drag_toolbar_to(self, view_point):
        """工具栏跟着鼠标走，并限制在视图内，避免拖出屏幕看不见。"""
        if self.toolbar_drag_grab is None:
            return
        margin = 8
        target = view_point - self.toolbar_drag_grab
        max_x = max(margin, self.view.width() - self.toolbar.width() - margin)
        max_y = max(margin, self.view.height() - self.toolbar.height() - margin)
        self.toolbar.move(min(max(target.x(), margin), max_x),
                          min(max(target.y(), margin), max_y))
        self.toolbar_manual = True

    def end_toolbar_drag(self):
        """松手：离候选位够近就吸附过去，否则就留在用户放下的位置。"""
        self.toolbar_dragging = False
        self.toolbar_drag_grab = None
        if self.toolbar_handle is not None:
            self.toolbar_handle.setCursor(Qt.SizeAllCursor)
        selection = self.view.to_logical_rect(self.rect).toRect()
        candidates, _left_x, _right_x = self.toolbar_placement_candidates(
            selection, self.toolbar.size())
        current = self.toolbar.pos()
        nearest = min(candidates, key=lambda point: (point - current).manhattanLength())
        if (nearest - current).manhattanLength() <= TOOLBAR_SNAP_DISTANCE:
            self.toolbar.move(nearest)

    def reset_toolbar_position(self):
        """双击空白处：放弃手动位置，回到自动摆位。"""
        self.toolbar_manual = False
        self.position_widgets()

    def reset_region(self, rect, image, alternate, cursor_enabled):
        """选区尺寸调整后重置内联编辑底图，并保留同一个保存路径。"""
        self.rect = QRect(rect)
        self.canvas.original = image.copy()
        self.canvas.image = image.copy()
        self.canvas.alternate = alternate.copy() if alternate else None
        self.canvas.original_alternate = self.canvas.alternate.copy() if self.canvas.alternate else None
        self.canvas.cursor_enabled = cursor_enabled if self.canvas.alternate is not None else False
        for item in list(self.canvas.annotations()):
            self.canvas.scene_data.removeItem(item)
        self.canvas.reset_history()
        self.canvas.cursor_index = 0
        self.canvas.refresh_image()
        self.position_widgets()
        self.canvas.show()
        # 隐藏状态是本次编辑内的临时状态：重置底图时不要把它又显示出来。
        self.apply_toolbar_visibility()
        self.view.magnifier_overlay.raise_()
    def begin_region_resize(self):
        """调整选区大小时先隐藏旧编辑层，避免遮挡正在查看的新区域。"""
        self.resizing_region = True
        self.canvas.hide()
        self.toolbar.hide()

    def end_region_resize(self):
        self.resizing_region = False

    def allocate_path(self, automatic=False, directory=None):
        """directory 非空时使用该目录（「另存为」），否则用设置里的保存目录。"""
        directory = Path(directory) if directory else resolved_dir(self.settings)
        directory.mkdir(parents=True, exist_ok=True)
        prefix = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", datetime.now().strftime(self.settings["filename"])).strip(" .") or "Capture"
        extension = saved_extension(self.settings)
        path = directory / f"{prefix}.{extension}"
        number = 1
        while path.exists():
            path = directory / f"{prefix}_{number}.{extension}"
            number += 1
        return path

    def output_image(self):
        return apply_output_effects(self.canvas.render_image(), self.settings,
                                    self.round_corners, self.corner_radius)

    def save_as(self):
        """另存为：可选目录与文件名（默认定位上次目录、预填当前命名），并记住该目录。"""
        # 另存为对话框允许同时改目录与文件名：默认定位上次用过的目录，并预填当前命名规则。
        suggested = self.allocate_path(directory=save_as_directory(self.settings))
        chosen, _ = QFileDialog.getSaveFileName(self, "另存为", str(suggested),
                                               save_as_filter(self.settings))
        if not chosen:
            return None
        path, save_settings = normalize_save_as_path(chosen, self.settings)
        result = self.output_image()
        if not save_image(result, path, save_settings):
            raise OSError(f"图片保存失败：{path}")
        logging.getLogger("screensnap").info(
            "原地编辑另存为: %s（%sx%s）", path, result.width(), result.height())
        save_signal = (self.saved_silently if self.suppress_save_notification else self.saved)
        save_signal.emit(str(path), result)               # 触发既有保存通知
        copy_saved_to_clipboard(path, result, self.settings)
        self.save_as_dir_chosen.emit(str(path.parent))
        # 与「保存」一致：另存为写盘成功、通知与记忆目录都发出后关闭遮罩。
        self.view.close()
        return path

    def save(self, automatic=False, copy_to_clipboard=False, force_copy_image=False):
        path = (self.last_path if matches_saved_format(self.last_path, self.settings)
            else self.allocate_path(automatic))
        result = self.output_image()
        if not save_image(result, path, self.settings):
            raise OSError(f"图片保存失败：{path}")
        self.last_path = path
        save_signal = (self.saved_silently if self.suppress_save_notification
                       else self.saved)
        save_signal.emit(str(path), result)
        if (copy_to_clipboard and (self.settings["copy_saved_image"] or self.settings["copy_saved_path"])) or force_copy_image:
            payload = QMimeData()
            if self.settings["copy_saved_path"]:
                payload.setText(str(path))
            if self.settings["copy_saved_image"] or force_copy_image:
                payload.setImageData(result)
            QGuiApplication.clipboard().setMimeData(payload)
        if self.settings["open_dir"]:
            os.startfile(str(path.parent)) if os.name == "nt" else None
        return path

    def set_tool(self, tool):
        if tool == "crop":
            return
        self.toolbar.options_button.setText("")
        self.canvas.set_tool(tool)
        if tool in ("pen", "rect", "ellipse", "arrow", "marker", "text"):
            self.last_color_tool = tool
        self.canvas.setDragMode(QGraphicsView.RubberBandDrag if tool == "select" else QGraphicsView.NoDrag)
        self.canvas.viewport().update()
        self.toolbar.set_active_tool(tool, self.last_color_tool)
        self.set_annotation_setting("annotation_tool", tool)

    def set_pen_color(self, color):
        tool = (self.canvas.tool if self.canvas.tool in
            ("pen", "rect", "ellipse", "arrow", "marker", "text")
            else self.last_color_tool)
        key = "pen_color" if tool == "pen" else f"{tool}_color"
        self.settings[key] = color
        self.toolbar.sync_tool_color(key, color)
        self.canvas.set_selected_color(color)
        self.canvas.refresh_tool_cursor()
        self.canvas.update()
        self.toolbar.set_active_tool(self.canvas.tool, self.last_color_tool)
        self.view.tool_color_changed.emit(tool, color)
        if tool == "pen":
            self.view.pen_color_changed.emit(color)

    def apply_picked_color(self, color):
        self.toolbar.pen_color.set_color(color)
        self.toolbar.pen_color.setText("")
        self.set_pen_color(color)
        QGuiApplication.clipboard().setText(color)

    def apply_canvas_setting(self, key, value):
        """画布内入口（如文字输入对话框）改配置：写入配置并同步工具栏控件。"""
        self.set_annotation_setting(key, value)
        self.toolbar.sync_setting(key, value)

    def _on_selected_annotation_changed(self, tool):
        """选中标注类型变化时，让工具栏「更多设置」展示该类型专属参数。"""
        self.toolbar.set_selected_tool(tool or None)

    def set_annotation_setting(self, key, value):
        from config.config_manager import TOOL_WIDTH_KEYS, fill_colors_following_stroke
        # 填充色默认与线色一致：仍等于旧线色时跟随变化，用户自定义过则保留。
        for fill_key, fill_value in fill_colors_following_stroke(self.settings, key, value).items():
            self.settings[fill_key] = fill_value
            self.canvas.settings[fill_key] = fill_value
            self.toolbar.sync_setting(fill_key, fill_value)
        self.settings[key] = value
        self.canvas.settings[key] = value
        if key in self.toolbar.tool_color_buttons:
            self.toolbar.sync_tool_color(key, value)
            tool = (self.canvas.selected_annotation_tool() if self.canvas.tool == "select"
                    else self.canvas.tool)
            if key == f"{tool}_color":
                self.canvas.set_selected_color(value)
            # 当前工具光标颜色跟随该工具的取色项变化即时刷新。
            self.canvas.refresh_tool_cursor()
        if key == "editor_image_round_corners":
            self.round_corners = value
        elif key == "editor_image_corner_radius":
            self.corner_radius = value
        if key in ("editor_image_round_corners", "editor_image_corner_radius"):
            self.canvas.set_round_corner_preview(self.round_corners, self.corner_radius)
        if key == "editor_transparent_background":
            self.canvas.refresh_transparency_preview()
        if key in ("editor_toolbar_shadow_enabled", "editor_toolbar_shadow_color",
                   "editor_toolbar_shadow_strength"):
            self.toolbar.apply_toolbar_shadow()
        if key in TOOL_WIDTH_KEYS.values():
            self.canvas.set_selected_width(value)
        elif key in ("rect_style", "ellipse_style"):
            self.canvas.set_selected_line_style(value)
        elif key in ("rect_fill_enabled", "rect_fill_opacity", "rect_fill_color"):
            self.canvas.set_selected_fill(
                "rect", self.settings.get("rect_fill_enabled", False),
                self.settings.get("rect_fill_opacity", 35),
                self.settings.get("rect_fill_color"))
        elif key in ("ellipse_fill_enabled", "ellipse_fill_opacity", "ellipse_fill_color"):
            self.canvas.set_selected_fill(
                "ellipse", self.settings.get("ellipse_fill_enabled", False),
                self.settings.get("ellipse_fill_opacity", 35),
                self.settings.get("ellipse_fill_color"))
        elif key == "font":
            self.canvas.set_selected_font(value)
        elif key == "font_size":
            self.canvas.set_selected_font_size(value)
        elif key == "text_width":
            self.canvas.set_selected_text_width(value)
        elif key == "text_height":
            self.canvas.set_selected_text_height(value)
        elif key == "text_alignment":
            self.canvas.text_alignment = {"left": Qt.AlignLeft, "center": Qt.AlignHCenter,
                                          "right": Qt.AlignRight}[value]
        self.canvas.update()
        self.view.session.views[0].annotation_setting_changed.emit(key, value)

    def set_crop_style(self, key, value):
        if key == "crop_color":
            self.canvas.crop_color = value
        elif key == "crop_width":
            self.canvas.crop_width = value
        self.canvas.viewport().update()

    def execute(self, action):
        if action in ("undo", "redo", "reset"):
            getattr(self.canvas, action)()
        elif action == "delete":
            self.canvas.remove_selected()
        elif action in ("top", "bottom", "up", "down"):
            self.canvas.layer(action)
        elif action in ("erase_one", "erase_clear"):
            # 擦除层可像普通图层一样删除，避免为撤回一次擦除而回退其后的所有编辑。
            self.canvas.erase_layer(action)
        elif action in ("left", "right", "half", "horizontal", "vertical", "angle", "reset_rotation"):
            # 第一版原地编辑禁用会改变图片尺寸/几何的图像变换，避免 DPI 和选区映射风险。
            return
        elif action == "paste":
            image = self.output_image()
            logging.getLogger("screensnap").debug(
                "原地编辑请求创建贴图: %dx%d", image.width(), image.height())
            position = self.view.mapper.physical_local_to_native_global(self.rect.topLeft())
            self.sticker_requested.emit(image, position)
            self.suppress_save_notification = True
            try:
                self.save(automatic=True)
            except OSError as error:
                logging.getLogger("screensnap").error(
                    "贴图已创建但原地编辑自动保存失败: %s", error, exc_info=True)
                self.save_failed.emit(f"贴图已创建，但自动保存失败: {error}")
            finally:
                self.suppress_save_notification = False
                self.view.close()
        elif action == "custom_size":
            self.view.set_inline_custom_size(self)
        elif action == "recapture":
            self.view.request_recapture()
        elif action == "save":
            try:
                self.save(copy_to_clipboard=True)
            except OSError as error:
                # 保存失败必须让用户看到（save_failed → 应用通知），并保持编辑器打开以便重试。
                self.save_failed.emit(f"保存失败: {error}")
                return
            self.view.close()
        elif action == "copy_only":
            image = self.output_image()
            payload = QMimeData()
            payload.setImageData(image)
            QGuiApplication.clipboard().setMimeData(payload)
            logging.getLogger("screensnap").info(
                "原地编辑仅复制到剪贴板: %dx%d", image.width(), image.height())
            # 通知由应用层统一处理（受「复制完成通知」开关控制）：沿用遮罩的 copy_done。
            self.view.copy_done.emit(image)
            self.view.close()
        elif action == "save_as":
            self.save_as()
        elif action == "copy":
            self.save(copy_to_clipboard=True, force_copy_image=True)
        elif action == "path":
            QGuiApplication.clipboard().setText(str(self.last_path))
        elif action == "discard":
            self.discard()
        elif action == "close_all_editors":
            self.close_all_requested.emit()
            self.view.close()

    def discard(self):
        self.view.close()

    def cleanup(self):
        """释放原地编辑中持有的大图和工具控件引用。"""
        if getattr(self, "canvas", None) is not None:
            self.canvas.setParent(None)
            self.canvas.deleteLater()
        if getattr(self, "toolbar", None) is not None:
            self.toolbar.setParent(None)
            self.toolbar.deleteLater()
        self.canvas = None
        self.toolbar = None
        self.view = None
        self.settings = None


class MaskWindow(QWidget):
    """覆盖虚拟桌面的交互窗口；原始像素始终保存在 Pillow 图像中。"""

    recapture_requested = Signal(object)
    quick_sticker_requested = Signal(object, object)
    selected = Signal(object, object)
    edit_requested = Signal(object, object)
    save_requested = Signal(object, object)
    last_region = Signal(object)
    image_saved = Signal(str, object)
    image_saved_silently = Signal(str, object)
    save_failed = Signal(str)
    copy_done = Signal(object)
    save_as_dir_chosen = Signal(str)   # 原地编辑器「另存为」选定的目录，转发给应用持久化
    picker_copied = Signal(str)
    close_all_requested = Signal()
    sticker_requested = Signal(object, object)
    annotation_setting_changed = Signal(str, object)
    pen_color_changed = Signal(str)
    tool_color_changed = Signal(str, str)
    cancel_requested = Signal()
    toolbar_hide_requested = Signal()

    def __init__(self, image, bounds, monitors, settings, mode="capture", alternate=None,
                 session=None, monitor=None, primary=True, initial_rect=None,
                 preferred_monitor=None, extra_intruders=None):
        super().__init__()
        self.setProperty("screensnap_overlay", True)
        # screensnap_mask 单独标记截图遮罩：UIA 与点击识别只穿透它，
        # 贴图、菜单等其它本程序窗口不再被忽略，可以被截图识别命中。
        self.setProperty("screensnap_mask", True)
        self.image = image
        self.alternate = alternate
        self.preview = to_qimage(image)
        self.mode = mode
        self.auto_complete_after_show = mode in ("fullscreen", "monitor")
        self.bounds = bounds
        self.monitors = monitors
        # 采集自检的外部线索：抓屏瞬间仍在屏幕上的弹出菜单——抓屏之后才关闭，此刻已枚举
        # 不到，但它的像素已经进了冻结帧，只能靠抓屏前记下的矩形补警示。
        # 系统通知（Win11 Toast / 托盘气泡）不做提示：它是操作系统窗口，抓屏前关不掉也
        # 枚举不到，"刚有系统通知"这类常驻提醒对用户是噪音。
        self.extra_intruders = [(str(label), QRect(rect))
                                for label, rect in (extra_intruders or ())]
        self.mapper = session.mapper if session is not None else DisplayMapper(bounds, monitors)
        global_bounds = QRect(bounds["left"], bounds["top"], bounds["width"], bounds["height"])
        initial_global_rect = QRect(*initial_rect).intersected(global_bounds) if initial_rect else QRect()
        cursor_target = initial_global_rect.center() if not initial_global_rect.isEmpty() else None
        if cursor_target is None and preferred_monitor:
            target_center = QPoint(preferred_monitor["left"] + preferred_monitor["width"] // 2,
                                   preferred_monitor["top"] + preferred_monitor["height"] // 2)
            current_monitor = next((item for item in monitors
                                    if QRect(item["left"], item["top"], item["width"], item["height"])
                                    .contains(target_center)), None)
            if current_monitor is None and monitors:
                current_monitor = min(monitors, key=lambda item: abs(item["left"] - preferred_monitor["left"])
                                      + abs(item["top"] - preferred_monitor["top"]))
            if current_monitor is not None:
                cursor_target = QPoint(current_monitor["left"] + current_monitor["width"] // 2,
                                       current_monitor["top"] + current_monitor["height"] // 2)
        if cursor_target is not None:
            QCursor.setPos(self.mapper.physical_global_to_native_global(cursor_target))
        self.session = session or MaskSession(self.mapper)
        self.primary = primary
        self.monitor = monitor or (monitors[0] if monitors else bounds)
        self.monitor_rect = self.mapper.monitor_local_rect(self.monitor)
        self.session.views.append(self)
        # 截图前只枚举一次窗口边界，避免鼠标移动时反复调用系统 API。
        self.window_edges = visible_windows() if primary else []
        self.settings = settings
        self.quick_sticker_enabled = bool(settings.get("capture_quick_sticker_enabled", False))
        self.quick_sticker_consumed = False
        self.quick_sticker_armed = False
        self.quick_sticker_pending = False
        self.capture_cursor_enabled = settings["cursor"]
        self.selection = self.session.selection
        self.picker_mode = False
        self.picker_color = None
        self.position = QPoint(self.session.position)
        self.resize_cursor = "nwse"
        # UIA 的读取预算/子控件上限/熔断阈值按当前设置生效（进程内全局，开遮罩时刷新一次）。
        self._apply_uia_limits()
        # 重绘耗时统计（累计秒、次数、峰值秒）与上次输出时刻，由 _record_paint_cost 限频输出。
        self.paint_cost_stats = [0.0, 0, 0.0]
        self.paint_cost_log_at = 0.0
        # 鼠标悬停识别出的元素矩形（bounds 局部物理坐标）与上次检测时刻。
        self.hover_rect = None
        self.hover_stamp = 0.0
        # 上次真正发起悬停查询的光标位置。
        self.hover_query_point = None
        self.press_position = None
        # 采集自检：缓存上次检测到的“落在选区内的自身窗口”类型，仅在变化时写日志。
        self.intruding_window_kinds = ()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        # 全局 Esc 兜底句柄：只有遮罩确实没抢到焦点时才会注册。
        self.escape_fallback = None
        if primary:
            self.cancel_shortcut = QShortcut(QKeySequence(Qt.Key_Escape), self)
            self.cancel_shortcut.setContext(Qt.ApplicationShortcut)
            self.cancel_shortcut.activated.connect(self._request_cancel)
            self.cancel_requested.connect(self._request_cancel)
        self.setGeometry(self.mapper.physical_local_rect_to_native_global_rect(self.monitor_rect).toRect())
        self.quick_sticker_shortcut = None
        self.capture_picker_shortcut = None
        if primary:
            self.quick_sticker_shortcut = QShortcut(
                QKeySequence(settings.get("capture_quick_sticker_shortcut", "Space")), self)
            self.quick_sticker_shortcut.setContext(Qt.ApplicationShortcut)
            self.quick_sticker_shortcut.setEnabled(self.quick_sticker_enabled)
            self.quick_sticker_shortcut.activated.connect(self.trigger_quick_sticker)
            self.capture_save_shortcut = QShortcut(
                QKeySequence(settings.get("capture_save_shortcut", "S")), self)
            self.capture_save_shortcut.setContext(Qt.ApplicationShortcut)
            self.capture_save_shortcut.activated.connect(self.save_selection)
            self.capture_picker_shortcut = QShortcut(
                QKeySequence(settings.get("capture_picker_shortcut", "C")), self)
            self.capture_picker_shortcut.setContext(Qt.ApplicationShortcut)
            self.capture_picker_shortcut.activated.connect(self.toggle_picker_mode)
        else:
            self.capture_save_shortcut = None
        self.magnifier_overlay = MagnifierOverlay(self)
        self.info_bar = InfoBar(self)
        if primary:
            for extra_monitor in monitors[1:]:
                # 采集自检的外部线索（抓屏前记录的弹出菜单矩形）同样交给其它显示器的遮罩，
                # 否则只有主屏提示条会给出这类警示。
                MaskWindow(image, bounds, monitors, settings, mode, alternate,
                           self.session, extra_monitor, primary=False,
                           extra_intruders=extra_intruders)
        if primary and not initial_global_rect.isEmpty():
            self.selection.rects.append(initial_global_rect.translated(-bounds["left"], -bounds["top"]))
        elif primary and mode == "fullscreen":
            self.selection.rects.append(QRect(0, 0, bounds["width"], bounds["height"]))
        elif primary and mode == "monitor":
            point = self.mapper.native_global_to_physical_global(self.cursor().pos())
            monitor = next((screen for screen in monitors if
                            screen["left"] <= point.x() < screen["left"] + screen["width"] and
                            screen["top"] <= point.y() < screen["top"] + screen["height"]), monitors[0])
            self.selection.rects.append(QRect(monitor["left"] - bounds["left"],
                                              monitor["top"] - bounds["top"],
                                              monitor["width"], monitor["height"]))
        # 窗口未显示时先识别一次，此时命中测试不会被本进程遮罩挡住。
        self.element_chain = []
        self.element_index = -1
        # 提示条跟着放大镜走，因此不再需要记录上一帧矩形与"鼠标移入隐藏"状态。
        self.info_bar_rect = QRect()
        # 选区阶段功能快捷键只在主遮罩上创建；ApplicationShortcut 也能从原地编辑子控件触发。
        self.capture_action_shortcuts = None
        self.toolbar_hide_shortcut = None
        if primary:
            slots = {"custom_size": self.select_fixed_size,
                     # R 快捷键＝清除选择（不刷新画面）；工具栏「重新截图」另有入口。
                     "recapture": self.clear_selection,
                     "window_edit": self.complete_in_window_editor,
                     "multi_select": self.toggle_multi_select_mode,
                     "copy": self.copy_selection_to_clipboard}
            self.capture_action_shortcuts = {
                name: self._capture_action_shortcut(settings, key, default, slots[name])
                for name, key, default, _label in CAPTURE_ACTION_KEYS}
            # 原地编辑工具栏的隐藏键：同一个机制，只加键位、不加按钮。
            self.toolbar_hide_shortcut = self._capture_action_shortcut(
                settings, *TOOLBAR_HIDE_KEY, self.toggle_inline_toolbar)
        self.sync_quick_sticker_shortcut(settings)
        self.sync_capture_save_shortcut(settings)
        self.sync_capture_action_shortcuts(settings)
        if primary and settings.get("window_detection", True):
            point = self.mapper.native_global_to_physical_global(QCursor.pos())
            self.element_chain = element_chain((point.x(), point.y()),
                                               settings.get("element_depth", 8),
                                               use_uia=bool(settings.get("window_uia_detect", False)),
                                               exclude_hwnd=int(self.winId()),
                                               debug_tree=bool(settings.get("uia_debug_tree", False)))
            auto_select = bool(settings.get("window_auto_select", False))
            logging.getLogger("screensnap").debug(
                "截图初始识别: 鼠标物理点=(%d,%d) UIA=%s 自动选中=%s 候选=%d 层 %s",
                point.x(), point.y(), self.uia_enabled(),
                auto_select, len(self.element_chain), self.element_chain)
            if self.element_chain and auto_select:
                self.apply_element(0)
                self.auto_complete_after_show = True


    def _capture_action_shortcut(self, settings, key, default, slot):
        """选区阶段的功能快捷键（尺寸 / 重新截图 / 窗口编辑 / 多选 / 仅复制）。

        默认：尺寸 `F`、重新截图 `R`、窗口编辑 `E`、多选 `Alt+M`、仅复制 `Y`；
        都可在「设置 > 截图 > 截图快捷操作」里改键。
        """
        shortcut = QShortcut(QKeySequence(settings.get(key, default)), self)
        shortcut.setContext(Qt.ApplicationShortcut)
        shortcut.activated.connect(slot)
        return shortcut

    def magnifier_size(self):
        """当前生效的放大镜边长（像素）；非法或缺失时回落到默认尺寸。"""
        try:
            size = int(self.settings.get("magnifier_size", MAGNIFIER_DEFAULT_SIZE))
        except (TypeError, ValueError):
            return MAGNIFIER_DEFAULT_SIZE
        return min(max(size, 100), 320)

    def hint_per_line(self):
        """「每行提示数」配置：0 表示只按宽度自动换行。"""
        try:
            value = int(self.settings.get("capture_hint_per_line", 0) or 0)
        except (TypeError, ValueError):
            return 0
        return min(max(value, 0), 8)

    def magnifier_frame(self):
        """放大镜框（本视图逻辑坐标）。

        放大镜关闭、被模态弹窗遮挡时**仍返回按同一避让规则算出的框**，提示条因此
        仍然跟着光标显示，不会因为放大镜不画了就消失。
        """
        return magnifier_rect(self.to_logical_point(self.position), self.rect(),
                              self.magnifier_size())

    def capture_hint_items(self):
        """提示条内容：按「设置里的顺序」给出当前阶段的提示文案。

        每个提示项都能在设置里单独开关（不在 `capture_hint_order` 里的不显示），
        顺序也以那份配置为准；总开关关闭时整条不显示。文案与设置页预览共用
        `screenshot.hint_items`，因此改键后两处显示的都是最新键位。
        """
        selection = self.selection.active or (
            self.selection.rects[-1] if self.selection.rects else None)
        editor = self.session.inline_editor
        return hint_items(
            self.settings, (self.position.x(), self.position.y()),
            (selection.width(), selection.height()) if selection else None,
            inline=self.inline_active(), picker=self.picker_mode,
            picker_color=self.picker_color,
            toolbar_hidden=bool(editor is not None and editor.toolbar_hidden),
            multi_select=self.session.multi_select_mode,
            right_capture=self.session.right_capture_mode,
            inline_tool=(editor.canvas.tool if editor is not None else "select"))

    def hint_texts(self, selection, inline):
        """提示项 id → 文案（供用例复用；实际显示走 `capture_hint_items`）。"""
        editor = self.session.inline_editor
        return hint_texts(
            self.settings, (self.position.x(), self.position.y()),
            (selection.width(), selection.height()) if selection else None,
            inline=inline, picker=self.picker_mode, picker_color=self.picker_color,
            multi_select=self.session.multi_select_mode,
            right_capture=self.session.right_capture_mode,
            toolbar_hidden=bool(editor is not None and editor.toolbar_hidden),
            inline_tool=(editor.canvas.tool if editor is not None else "select"))

    def capture_action_hints(self):
        """兼容旧调用：返回选区功能键的 (键, 名称) 列表（供用例复用）。"""
        if self.picker_mode or self.inline_active():
            return None
        selection = self.selection.active or (
            self.selection.rects[-1] if self.selection.rects else None)
        if selection is None:
            return None
        return [(str(self.settings.get(key, default)), label)
                for _name, key, default, label in CAPTURE_ACTION_KEYS]

    def toggle_inline_toolbar(self):
        """快捷键临时隐藏/恢复原地编辑工具栏。

        界面上不加任何隐藏/显示按钮，提示条第二行负责告诉用户按哪个键。
        """
        editor = self.session.inline_editor
        if editor is None:
            return
        hidden = editor.toggle_toolbar()
        logging.getLogger("screensnap").debug("原地编辑工具栏%s", "已隐藏" if hidden else "已恢复")

    def logical_window_offset(self):
        """当前窗口左上角相对整轮遮罩 logical_bounds 的偏移。"""
        return self.mapper.physical_local_rect_to_logical_global_rect(
            self.monitor_rect).toRect().topLeft() - self.mapper.logical_bounds.topLeft()

    def show(self):
        if self.primary:
            for view in self.session.views:
                QWidget.show(view)
                view.magnifier_overlay.sync()
                view.info_bar.sync()
            activate_window(self)
            self.setFocus(Qt.ActiveWindowFocusReason)
            QTimer.singleShot(0, self._focus_capture)
        else:
            QWidget.show(self)
            self.magnifier_overlay.sync()
            self.info_bar.sync()
            self.activateWindow()
            self.setFocus(Qt.ActiveWindowFocusReason)
        self._install_toolbar_hide_hotkey()
        self._sync_escape_fallback()

    def _focus_capture(self):
        """显示完成后重试一次焦点交接，避免全局热键启动时窗口尚未激活。"""
        if self.primary and not self.session.closing and self.isVisible():
            activate_window(self)
            self.setFocus(Qt.ActiveWindowFocusReason)
            self._sync_escape_fallback()

    def _install_toolbar_hide_hotkey(self):
        """用系统级热键处理工具栏隐藏键：不依赖"哪块遮罩拿到键盘输入"。

        真机日志证明副屏那块遮罩即使被系统报为前台窗口，按键也进不了 Qt 事件循环
        （应用级 eventFilter 一行都没有，且与自家 Esc 钩子无关），所以这个键必须走
        与全局截图热键同一套机制。安装成功的那一个视图负责连接信号（避免多视图重复连接）。
        """
        if getattr(self.session, "toolbar_hide_hotkey", None) is not None:
            return
        from core.window_focus import offscreen

        if offscreen():
            logging.getLogger("screensnap").debug("离屏环境，跳过工具栏隐藏键全局热键")
            return
        hide_key, hide_default = TOOLBAR_HIDE_KEY
        sequence = QKeySequence(self.settings.get(hide_key, hide_default))
        if sequence.isEmpty():
            sequence = QKeySequence(hide_default)
        binding = sequence.toString()
        if not binding or os.environ.get("SCREENSNAP_NO_TOOLBAR_HOTKEY") == "1":
            return
        try:
            import keyboard

            self.session.toolbar_hide_hotkey = keyboard.add_hotkey(
                binding, self._global_toolbar_hide, suppress=True)
            self.toolbar_hide_requested.connect(self.toggle_inline_toolbar)
            logging.getLogger("screensnap").debug(
                "工具栏隐藏键全局热键已注册：键=%r 句柄=%s", binding,
                bool(self.session.toolbar_hide_hotkey))
        except (ImportError, ValueError, OSError, RuntimeError) as error:
            logging.getLogger("screensnap").debug("注册工具栏隐藏键全局热键失败: %s", error)

    def _release_toolbar_hide_hotkey(self):
        handle = getattr(self.session, "toolbar_hide_hotkey", None)
        if handle is None:
            return
        self.session.toolbar_hide_hotkey = None
        try:
            import keyboard

            keyboard.remove_hotkey(handle)
            logging.getLogger("screensnap").debug("工具栏隐藏键全局热键已释放")
        except (ImportError, ValueError, OSError, RuntimeError) as error:
            logging.getLogger("screensnap").debug("释放工具栏隐藏键全局热键失败: %s", error)

    def _global_toolbar_hide(self):
        """钩子线程回调：只发信号，切换在 Qt 线程执行。"""
        logging.getLogger("screensnap").debug("工具栏隐藏键全局热键触发（钩子线程）")
        self.toolbar_hide_requested.emit()

    def _install_escape_fallback(self):
        """注册全局 Esc 热键；由全局钩子线程回调，只发信号不直接关窗口。"""
        try:
            import keyboard

            return keyboard.add_hotkey("esc", self._global_escape, suppress=True)
        except (ImportError, ValueError, OSError, RuntimeError) as error:
            logging.getLogger("screensnap").debug("注册 Esc 兜底热键失败: %s", error)
            return None

    def _release_escape_fallback(self):
        handle = self.escape_fallback
        if handle is None:
            return
        self.escape_fallback = None
        try:
            import keyboard

            keyboard.remove_hotkey(handle)
        except (ImportError, ValueError, OSError, RuntimeError, KeyError, TypeError) as error:
            # 句柄可能已被回收或注册表已删除：按“已释放”处理，不能打断关闭流程。
            logging.getLogger("screensnap").debug("移除 Esc 兜底热键失败: %s", error)

    def _global_escape(self):
        """全局钩子线程里只发信号，关闭动作交回 Qt 主线程执行。"""
        try:
            self.cancel_requested.emit()
        except RuntimeError as error:
            # 遮罩销毁后旧句柄仍可能触发一次；C++ 信号源已删除时必须安全退出并清理自身注册，
            # 否则异常会打断 keyboard 的钩子线程，把整个进程带崩（退出码 -1）。
            logging.getLogger("screensnap").debug("遮罩销毁后忽略 Esc 兜底回调: %s", error)
            self._release_escape_fallback()

    def _request_cancel(self):
        """放弃截图/退出原地编辑；取色、字体等模态框或下拉弹出层打开时 Esc 应交由它们处理。"""
        if (QGuiApplication.modalWindow() is not None or
                QApplication.activePopupWidget() is not None):
            return
        self.close()

    def _sync_escape_fallback(self):
        """只有遮罩确实拿不到焦点时才用全局钩子兜底，保证 Esc 一定能退出截图。"""
        if not ESCAPE_FALLBACK_ENABLED:
            self._release_escape_fallback()
            return
        if not self.primary or self.session.closing or not self.isVisible():
            self._release_escape_fallback()
            return
        # 取色、字体等模态框或下拉弹出层打开时，Esc 应交由它们处理：既不要吞掉按键，也不要关掉截图。
        if (QGuiApplication.modalWindow() is not None or
                QApplication.activePopupWidget() is not None):
            self._release_escape_fallback()
            return
        if self.isActiveWindow():
            self._release_escape_fallback()
        elif self.escape_fallback is None:
            self.escape_fallback = self._install_escape_fallback()

    def changeEvent(self, event):
        """焦点状态变化后同步兜底：拿到焦点就交还给 Qt 的正常按键处理。"""
        super().changeEvent(event)
        if event.type() == QEvent.ActivationChange:
            self._sync_escape_fallback()

    def hide(self):
        self._release_escape_fallback()
        if self.primary:
            for view in self.session.views:
                QWidget.hide(view)
        else:
            QWidget.hide(self)

    def close(self):
        self._release_escape_fallback()
        if self.session.closing:
            QWidget.close(self)
            return
        self.session.closing = True
        for view in list(self.session.views):
            QWidget.close(view)

    def closeEvent(self, event):
        """窗口关闭后释放截图大图，避免 Application.mask 暂存时继续占用内存。"""
        logging.getLogger("screensnap").debug("关闭截图遮罩")
        # 关闭路径可能绕过 Python 的 close()（例如多屏时 Qt 直接关闭其它视图），
        # 这里再释放一次全局 Esc 兜底，避免遮罩销毁后钩子仍持有失效信号源。
        self._release_escape_fallback()
        # 释放缓存的原始扩展样式，避免窗口句柄被复用后拿到过期样式。
        forget_click_through(int(self.winId()))
        # 截图结束：清掉悬停高亮，避免下一轮截图残留上一次的元素框。
        self.hover_rect = None
        self.hover_stamp = 0.0
        self.hover_query_point = None
        self.press_position = None
        self.magnifier_overlay.hide()
        self.info_bar.hide()
        editor = self.session.inline_editor
        if editor is not None:
            editor.cleanup()
            self.session.inline_editor = None
        if self in self.session.views:
            self.session.views.remove(self)
        self.image = None
        self.alternate = None
        self.preview = None
        if not self.session.views:
            self.session.selection.rects.clear()
            self.session.selection.active = None
        super().closeEvent(event)

        self._release_toolbar_hide_hotkey()

    def update_all(self):
        # 这里曾经按 selection.nudge_index 关闭 S：那是"上次微调"留下的记忆值，
        # 而 update_all() 每次鼠标移动都会跑，于是刚设成启用的 S 立刻又被关掉
        # （日志实证：S 快捷键就绪 启用=True，按 S 却毫无反应）。
        # 现在 S 恒定可用作快捷保存，微调交由方向键承担。
        if self.capture_save_shortcut is not None:
            # S 不参与微调：任何状态下都是快捷保存（避免"按住时按键被吞/误保存"的判定问题）
            self.capture_save_shortcut.setEnabled(True)
        self.session.position = QPoint(self.position)
        for view in self.session.views:
            view.position = QPoint(self.session.position)
            view.update()
            view.magnifier_overlay.sync()
            # 提示条是子控件：位置与内容都跟着光标变，每帧同步一次并抬到画布之上。
            view.info_bar.sync()

    def _dismiss_inline_editor(self):
        """关掉原地编辑器并归还快捷键（沿用 toggle_multi_select_mode 的收尾顺序）。"""
        editor = self.session.inline_editor
        if editor is None:
            return False
        dirty = editor.canvas.cursor_index > 0
        logging.getLogger("screensnap").debug(
            "R（清除选择）：退出原地编辑（画布标注数=%d，按清除语义丢弃）", editor.canvas.cursor_index)
        editor.canvas.hide()
        editor.toolbar.hide()
        if editor.toolbar_handle is not None:
            editor.toolbar_handle.hide()
        editor.cleanup()
        editor.hide()
        editor.setParent(None)
        editor.deleteLater()
        self.session.inline_editor = None
        self.sync_quick_sticker_shortcut(self.settings)
        self.sync_capture_save_shortcut(self.settings)
        self.sync_capture_action_shortcuts(self.settings)
        return dirty

    def clear_selection(self):
        """清除当前选择，回到"未选择"状态：不重新抓屏、不移动鼠标。

        R 快捷键走这里；工具栏的「重新截图」仍走 request_recapture（重新取最新画面）。
        原地编辑激活时（左键划选之后就是这个状态）R 同样生效：先退出原地编辑，再清空选区。
        """
        if self.inline_active():
            self._dismiss_inline_editor()
            self.picker_mode = False
        had = len(self.selection.rects)
        self.selection.rects.clear()
        self.selection.active = None
        self.selection.dragging = None
        self.selection.resizing = None
        self.session.multi_select_mode = False
        self.session.right_capture_mode = False
        self.session.element_selected = False
        self.session.fixed_size_rect = None
        self.press_position = None
        self.update_all()
        logging.getLogger("screensnap").debug("清除选区：回到未选择状态（原有 %d 个区域）", had)

    def request_recapture(self):
        if self._delegate_to_owner("request_recapture"):
            return
        context = {"monitor": dict(self.monitor)}
        self.recapture_requested.emit(context)
        self.close()

    def copy_selection_to_clipboard(self):
        """把整屏截图（或当前选区）写入剪贴板并关闭遮罩，不落盘、不进编辑器。"""
        if self._delegate_to_owner("copy_selection_to_clipboard"):
            return
        logger = logging.getLogger("screensnap")
        if self.inline_active():
            # 原地编辑里按 Y ＝ 编辑器工具栏的「仅复制」：带标注的成品图进剪贴板，不落盘（规则 11）。
            editor = self.session.inline_editor
            image = editor.output_image()
            payload = QMimeData()
            payload.setImageData(image)
            QGuiApplication.clipboard().setMimeData(payload)
            logger.info("仅复制（原地编辑）到剪贴板: %dx%d", image.width(), image.height())
            self.copy_done.emit(image)
            self._dismiss_inline_editor()
            self.close()
            return
        if self.selection.rects:
            # 多选收集了多块时只复制**最后划选**的那一块（用户确认的语义，2026-10-10）。
            region = self.selection.rects[-1].normalized()
            area = (region.x(), region.y(), region.right() + 1, region.bottom() + 1)
            image = self.image.crop(area)
            label = "选区"
        else:
            image = self.image
            label = "整屏"
        payload = QMimeData()
        payload.setImageData(to_qimage(image))
        QGuiApplication.clipboard().setMimeData(payload)
        logger.info("仅复制%s到剪贴板: %dx%d", label, image.width, image.height)
        self.copy_done.emit(image)
        self.close()

    def complete_in_window_editor(self):
        """E：用独立编辑器打开当前选区。同一轮截图里只允许成功打开一次。"""
        logger = logging.getLogger("screensnap")
        if getattr(self.session, "editor_opened", False):
            # 每个遮罩视图都会收到这次按键（日志里能看到相隔 1ms 的两条记录）：第二次若继续走
            # complete()，它自己没有选区 → 收摊关闭遮罩 → 连带把刚打开的编辑器关掉。
            logger.debug("E 重复触发，忽略（本轮截图已打开过编辑器 editor_opened=%s）",
                        getattr(self.session, "editor_opened", None))
            return
        logger.info(
            "E窗口编辑：视图=%s 选区=%s 共 %d 个视图",
            self.monitor_rect.getRect(),
            [r.getRect() for r in self.selection.rects], len(self.session.views or []))
        if self._delegate_to_owner("complete_in_window_editor"):
            return
        if self.inline_active():
            self._promote_inline_editor_to_window()
            return
        self.complete(force_window=True)


    def _promote_inline_editor_to_window(self):
        """把原地编辑当前的区域与合成图交给独立编辑器，保留已画好的标注。"""
        logger = logging.getLogger("screensnap")
        editor = self.session.inline_editor
        if editor is None:
            self.complete(force_window=True)
            return
        rect = QRect(editor.rect)
        image = editor.output_image()
        if not hasattr(image, "mode"):       # PIL 图有 .mode；QImage 没有 → 转成 PIL
            image = qimage_to_pillow(image)
        alternate = getattr(editor, "alternate", None)
        position = self.mapper.physical_local_to_native_global(rect.topLeft())
        self.session.editor_opened = True
        self.session.inline_editor = None
        try:
            editor.cleanup()
        except Exception as error:
            logger.warning("转交独立编辑器时清理原地编辑失败: %s", error)
        logger.debug("E（原地编辑）→ 转交独立编辑器：区域=%s 锚点=(%d,%d)",
                    rect.getRect(), position.x(), position.y())
        self.edit_requested.emit([(image, alternate)], [position])
        self.close()
    def toggle_multi_select_mode(self, _forwarded=False):
        """切换到多选收集；原地编辑有改动时按配置静默保存或丢弃。

        原地编辑自带的 Alt+M 会把请求交回遮罩（mask_window.py 的 multi_select_shortcut），
        而归属判定可能把它转给**非主**视图 —— 多选状态只在主视图维护，旧代码在那边直接
        return，于是主屏按 Alt+M 两头空转、毫无反应（副屏恰好经归属转交到主视图，看着正常）。
        现在：非主视图收到的请求**转交主视图**，_forwarded 防止来回递归。
        """
        logger = logging.getLogger("screensnap")
        # 同一按键会被两条快捷键各触发一次：原地编辑的 multi_select_shortcut（WidgetWithChildren）
        # 与遮罩的选区功能键 multi_select（ApplicationShortcut，只建在主屏）—— 两次切换会互相抵消，
        # 表现为"主屏按 Alt+M 毫无反应"。这里按会话记时间戳，短时间内的重复触发一律忽略。
        now = time.monotonic()
        last = getattr(self.session, "_multi_select_at", 0.0)
        # 只对**非转交**调用去抖：转交是同一毫秒内发生的，若也判重复，主视图那一跳会被自己拦掉
        # （日志实证：01:44:01 转交后紧跟一条 "忽略 0.000s 内的重复触发" → 主屏永远进不去）。
        if not _forwarded and now - last < 0.25:
            logger.debug("多选切换：忽略 %.3fs 内的重复触发（视图=%s primary=%s）",
                         now - last, self.monitor_rect.getRect(), self.primary)
            return
        self.session._multi_select_at = now
        logger.debug("多选切换：视图=%s primary=%s inline=%s _forwarded=%s",
                     self.monitor_rect.getRect(), self.primary, self.inline_active(), _forwarded)
        if not _forwarded and self._delegate_to_owner("toggle_multi_select_mode"):
            return
        if not self.primary or self.session.closing:
            primary = next((view for view in (self.session.views or [])
                            if getattr(view, "primary", False)), None)
            if (not self.session.closing and primary is not None and primary is not self
                    and not _forwarded):
                logging.getLogger("screensnap").debug(
                    "多选：非主视图收到切换请求，转交主视图 %s",
                    primary.monitor_rect.getRect())
                primary.toggle_multi_select_mode(_forwarded=True)
            return
        if self.inline_active():
            editor = self.session.inline_editor
            dirty = editor.canvas.cursor_index > 0
            action = self.settings.get("capture_multi_edit_action", "save")
            if dirty and action == "save":
                editor.suppress_save_notification = True
                try:
                    editor.save(automatic=True)
                except OSError as error:
                    editor.save_failed.emit(str(error))
                    self.update_all()
                    return
                finally:
                    editor.suppress_save_notification = False
            editor.canvas.hide()
            editor.toolbar.hide()
            if editor.toolbar_handle is not None:
                editor.toolbar_handle.hide()
            editor.cleanup()
            editor.hide()
            editor.setParent(None)
            editor.deleteLater()
            self.session.inline_editor = None
            self.sync_quick_sticker_shortcut(self.settings)
            self.sync_capture_save_shortcut(self.settings)
            self.sync_capture_action_shortcuts(self.settings)
            shortcut = self.capture_action_shortcuts.get("multi_select")
            if shortcut is not None:
                shortcut.setEnabled(True)
            self.session.multi_select_mode = True
        else:
            self.session.multi_select_mode = not self.session.multi_select_mode
        self.update_all()

    def sync_quick_sticker_shortcut(self, settings):
        self.quick_sticker_enabled = bool(settings.get("capture_quick_sticker_enabled", False))
        if self.quick_sticker_shortcut is None:
            return
        sequence = QKeySequence(settings.get("capture_quick_sticker_shortcut", "Space"))
        if sequence.isEmpty():
            sequence = QKeySequence("Space")
        self.quick_sticker_shortcut.setKey(sequence)
        self.quick_sticker_shortcut.setEnabled(
            self.quick_sticker_enabled and not self.inline_active())

    def sync_capture_save_shortcut(self, settings):
        if self.capture_save_shortcut is None:
            if not getattr(self, "_save_shortcut_missing_logged", False):
                self._save_shortcut_missing_logged = True
                logging.getLogger("screensnap").debug(
                    "S 快捷键未创建：视图=%s（只有主遮罩视图创建快捷键）",
                    self.monitor_rect.getRect())
            return
        sequence = QKeySequence(settings.get("capture_save_shortcut", "S"))
        if sequence.isEmpty():
            sequence = QKeySequence("S")
        self.capture_save_shortcut.setKey(sequence)
        # S 始终可用来保存：以前这里依赖 selection.nudge_index —— 那是"上次微调"留下的记忆值，
        # 一旦残留就把 S 永久禁用（日志实证：nudge_index=0 → 按 S 无反应）。
        # 微调仍由方向键承担（Down 为下移），S 保留为快捷保存。
        # S 不参与微调：任何状态下都是快捷保存
        enabled = True
        if not enabled and getattr(self, "_save_shortcut_last", None) is not False:
            # 只在"由可用变为禁用"时记一条：这正是"按 S 没反应"的成因
            logging.getLogger("screensnap").debug(
                "S 快捷保存被禁用：存在选区微调记忆 nudge_index=%s（视图=%s）",
                self.selection.nudge_index, self.monitor_rect.getRect())
        self._save_shortcut_last = enabled
        self.capture_save_shortcut.setEnabled(enabled)
        if not getattr(self, "_save_shortcut_state_logged", False):
            # 每次截图只记一次：确认 S 快捷键确实存在、键位与启用状态
            self._save_shortcut_state_logged = True
            logging.getLogger("screensnap").debug(
                "S 快捷键就绪：视图=%s 键=%s 启用=%s",
                self.monitor_rect.getRect(), sequence.toString(), enabled)

    def sync_capture_action_shortcuts(self, settings):
        """改键后同步选区功能键、内联多选键与工具栏隐藏键。"""
        inline_active = self.inline_active()
        if self.capture_picker_shortcut is not None:
            sequence = QKeySequence(settings.get("capture_picker_shortcut", "C"))
            if sequence.isEmpty():
                sequence = QKeySequence("C")
            self.capture_picker_shortcut.setKey(sequence)
            # 取色在未选择 / 原地编辑 / 多选下都能用：原地编辑只遮挡选区那块，遮挡外仍可取样。
            self.capture_picker_shortcut.setEnabled(True)
        if self.toolbar_hide_shortcut is not None:
            key, default = TOOLBAR_HIDE_KEY
            sequence = QKeySequence(settings.get(key, default))
            if sequence.isEmpty():
                sequence = QKeySequence(default)
            self.toolbar_hide_shortcut.setKey(sequence)
            self.toolbar_hide_shortcut.setEnabled(inline_active)
        if not self.capture_action_shortcuts:
            return
        editor = self.session.inline_editor
        if editor is not None:
            sequence = QKeySequence(settings.get("capture_multi_select_shortcut", "Alt+M"))
            if sequence.isEmpty():
                sequence = QKeySequence("Alt+M")
            editor.multi_select_shortcut.setKey(sequence)
        for name, key, default, _label in CAPTURE_ACTION_KEYS:
            shortcut = self.capture_action_shortcuts.get(name)
            if shortcut is None:
                continue
            sequence = QKeySequence(settings.get(key, default))
            if sequence.isEmpty():
                sequence = QKeySequence(default)
            shortcut.setKey(sequence)
            # E 与 F（尺寸）在原地编辑里必须保持可用：E 是「升级为独立编辑器」的入口，
            # F 用来给"快速编辑"里的区域改尺寸（实测：原地编辑一打开 F 就被禁用 → 按 F 无反应）。
            # R（清除选择）同样必须可用：左键划选会直接进入原地编辑，此时按 R 没有任何反应
            # （实测主副屏一致）—— R 的语义就是"取消这块选区"，在原地编辑里等价于"退出并清除"。
            # Y（仅复制）也必须可用：左键划选后按 Y 同样被禁用（实测副屏无反应），
            # 在原地编辑里它等价于编辑器工具栏的「仅复制」：复制带标注的成品图、不落盘、关遮罩。
            # 其余功能键（多选）仍按原语义在原地编辑里禁用。
            shortcut.setEnabled(
                name in ("window_edit", "custom_size", "recapture", "copy") or not inline_active)

    def toggle_picker_mode(self):
        if self._delegate_to_owner("toggle_picker_mode"):
            return
        if self.session.closing:
            return
        self.picker_mode = not self.picker_mode
        self.picker_color = None
        self.update_all()
        logging.getLogger("screensnap").debug(
            "取色模式 %s（原地编辑=%s 多选=%s）", "开启" if self.picker_mode else "关闭",
            self.inline_active(), self.session.multi_select_mode)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.magnifier_overlay.sync()
        self.info_bar.sync()

    def inline_active(self):
        return self.session.inline_editor is not None

    def _selection_area(self, selection=None):
        """选区在屏幕物理坐标下的矩形；没有有效选区时返回 None。"""
        if selection is None:
            selection = self.selection.active or (
                self.selection.rects[-1] if self.selection.rects else None)
        if selection is None or selection.isEmpty():
            return None
        return selection.translated(self.bounds["left"], self.bounds["top"])

    def window_label(self, widget):
        """干扰窗口在提示条里的中文名（窗口可自报，见 `screensnap_self_window`）。"""
        return window_label(widget)

    def intruding_windows(self, selection=None):
        """采集自检：返回落在选区内的本程序窗口（它们会被一起采进画面）。

        只统计会在屏幕上长期存在的自身窗口：上一轮通知缩略图、贴图、设置窗口、
        编辑器与弹出菜单/对话框。遮罩自身、放大镜 HUD 以及按钮组、原地编辑工具栏等
        内部控件不算——它们只画在遮罩表面，不会进入裁切像素。带 `screensnap_self_window`
        属性自报的浮层（如工具栏「外观」弹层）同样计入，避免"被采进画面却无警示"。
        """
        area = self._selection_area(selection)
        if area is None:
            return []
        hits = []
        for widget in QApplication.allWidgets():
            # 只看真正的窗口（含带父窗口的对话框），内部子控件（遮罩上的按钮组、原地编辑
            # 工具栏等）不进入裁切像素，因此一律跳过。
            # 先确认底层 C++ 对象仍在：控件析构后包装对象可能还留在列表里，直接调用会崩。
            if not live_widget(widget):
                continue
            if widget is self or not widget.isWindow() or not widget.isVisible():
                continue
            if widget in self.session.views:
                continue
            if isinstance(widget, (MaskWindow, MagnifierOverlay)):
                continue
            own = widget.property("screensnap_self_window") or \
                widget.property("screensnap_overlay") or \
                type(widget).__name__ in INTRUDING_WINDOW_CATEGORIES
            if not own and not isinstance(widget, (QMenu, QDialog)):
                continue
            rect = widget.frameGeometry()
            if rect.width() > 0 and rect.height() > 0 and area.intersects(rect):
                hits.append(widget)
        return hits

    def self_check_warning(self, selection=None):
        """采集自检：开启且选区含本程序内容时返回提示文案，否则返回 None。

        总开关 `intruder_warning_enabled` 默认关闭；开启后按子项 `intruder_warning_items`
        过滤参与警示的窗口分类。两类来源合并成一句：选区内的常驻自身窗口，以及抓屏
        瞬间记录下的弹出菜单（它已被烤进冻结帧，此刻枚举不到）。系统通知不作提示（对用户是噪音）。
        """
        settings = self.settings or {}
        if not settings.get("intruder_warning_enabled", False):
            return None
        enabled = settings.get("intruder_warning_items") or {}
        hits = [widget for widget in self.intruding_windows(selection)
                if enabled.get(window_category(widget), True)]
        kinds = tuple(sorted(type(widget).__name__ for widget in hits))
        logger = logging.getLogger("screensnap")
        if kinds != self.intruding_window_kinds:
            self.intruding_window_kinds = kinds
            if kinds:
                logger.warning("选区含本程序窗口，可能被一起采集: %s", ", ".join(kinds))
            else:
                logger.debug("选区已不含本程序窗口")
        labels = {self.window_label(widget) for widget in hits}
        area = self._selection_area(selection)
        if area is not None and enabled.get("popup", True):
            for label, rect in self.extra_intruders:
                if rect.width() > 0 and rect.height() > 0 and area.intersects(rect):
                    labels.add(label)
        if not labels:
            return None
        return f"选区内有{'、'.join(sorted(labels))}，移开后重截"

    def set_inline_custom_size(self, editor):
        bounds = self.mapper.full_physical_local_rect()
        dialog = QDialog(editor)
        dialog.setWindowTitle("自定义选区尺寸")
        # 遮罩是 Tool + WindowStaysOnTopHint 的置顶全屏窗，还铺了 70% 黑：普通对话框会被压在
        # 它下面，看上去就是「点了却看不到输入框」。这里显式置顶，并居中到编辑区所在屏幕。
        dialog.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        layout = QFormLayout(dialog)
        width_input = QSpinBox(dialog)
        width_input.setRange(1, max(1, bounds.width()))
        width_input.setValue(editor.rect.width())
        height_input = QSpinBox(dialog)
        height_input.setRange(1, max(1, bounds.height()))
        height_input.setValue(editor.rect.height())
        layout.addRow("宽度", width_input)
        layout.addRow("高度", height_input)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=dialog)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addRow(buttons)
        dialog.adjustSize()
        anchor = editor.mapToGlobal(editor.rect.center())
        screen = QGuiApplication.screenAt(anchor) or QGuiApplication.primaryScreen()
        if screen is not None:
            area = screen.availableGeometry()
            frame = dialog.frameGeometry()
            frame.moveCenter(anchor)
            dialog.move(min(max(frame.left(), area.left()), area.right() - frame.width() + 1),
                        min(max(frame.top(), area.top()), area.bottom() - frame.height() + 1))
        dialog.raise_()
        dialog.activateWindow()
        if dialog.exec() != QDialog.Accepted:
            return

        width, height = width_input.value(), height_input.value()
        center = editor.rect.center()
        left = max(bounds.left(), min(center.x() - width // 2, bounds.right() - width + 1))
        top = max(bounds.top(), min(center.y() - height // 2, bounds.bottom() - height + 1))
        self.selection.rects[:] = [QRect(left, top, width, height)]
        self.selection.active = None
        self.update_inline_region()

    def update_inline_region(self):
        """同步被缩放后的选区到内联编辑画布。"""
        editor = self.session.inline_editor
        if editor is None or not self.selection.rects:
            return
        rect = self.selection.rects[0].intersected(self.mapper.full_physical_local_rect())
        view = self.inline_view_for_rect(rect)
        if view is None:
            return
        area = (rect.x(), rect.y(), rect.right() + 1, rect.bottom() + 1)
        selected = self.image.crop(area)
        other = self.alternate.crop(area) if self.alternate else None
        cursor_enabled = editor.toolbar.cursor_switch.isChecked()
        if other is not None and cursor_enabled != self.capture_cursor_enabled:
            selected, other = other, selected
        canvas_focused = editor.canvas.hasFocus() or editor.canvas.viewport().hasFocus()
        editor.view = view
        if editor.canvas.parent() is not view:
            editor.canvas.setParent(view)
            editor.toolbar.setParent(view)
        editor.reset_region(rect, selected, other, cursor_enabled)
        if canvas_focused:
            editor.canvas.setFocus(Qt.ActiveWindowFocusReason)
        self.last_region.emit([rect.x() + self.bounds["left"], rect.y() + self.bounds["top"],
                               rect.width(), rect.height()])
        self.update_all()

    def inline_view_for_rect(self, rect):
        """返回完整容纳选区的显示器遮罩；跨屏选区不支持原地编辑。"""
        return next((view for view in self.session.views
                     if view.monitor_rect.contains(rect)), None)

    def _region_owner_view(self, rect):
        """区域属于哪块屏：优先完全包含它的视图，否则取相交面积最大的那个。"""
        best, best_area = None, 0
        # 只接受真正的遮罩视图：测试桩/Mock 的 monitor_rect.contains() 会返回真值，
        # 让归属落到错的视图上（曾把 test_mask_creates_one_window_per_monitor 跑红）。
        candidates = [self] + [view for view in (self.session.views or [])
                               if view is not None and view is not self
                               and isinstance(getattr(view, "monitor_rect", None), QRect)
                               and hasattr(view, "image")]
        for view in candidates:
            monitor = view.monitor_rect
            if monitor.contains(rect):
                return view
            clipped = monitor.intersected(rect)
            area = clipped.width() * clipped.height()
            if area > best_area:
                best, best_area = view, area
        return best

    def _region_in_view_local(self, rect, view):
        """把本视图局部坐标的区域换算到目标视图的局部坐标（物理全局＝局部＋显示器原点）。"""
        if view is self:
            return rect
        return rect.translated(self.bounds["left"] - view.bounds["left"],
                               self.bounds["top"] - view.bounds["top"])

    def _trim_region_to_monitor(self, rect):
        """选区几乎完整落在某块显示器内、只多出少量边框时收边到该显示器。

        判据同时看“单边溢出量”和“收边后保留的面积占比”：最大化窗口的不可见 resize frame
        只会让选区比显示器多出几个百分点，而真正跨屏的选区会在某块屏上丢掉一大截——
        只按溢出量判断会把“只压过来一点点”的跨屏窗口也误收边。
        """
        area = rect.width() * rect.height()
        if area <= 0:
            return rect
        for view in self.session.views:
            monitor = view.monitor_rect
            if monitor.contains(rect):
                return rect
            if not monitor.intersects(rect):
                continue
            spill = max(monitor.left() - rect.left(), rect.right() - monitor.right(),
                        monitor.top() - rect.top(), rect.bottom() - monitor.bottom())
            clipped = rect.intersected(monitor)
            keep = (clipped.width() * clipped.height()) / area
            if 0 < spill <= INLINE_SPILL_TOLERANCE and keep >= INLINE_TRIM_KEEP_RATIO:
                logging.getLogger("screensnap").debug(
                    "选区几乎落在一块显示器内，按显示器收边: 溢出=%d 保留=%.0f%% 选区=%s 显示器=%s",
                    spill, keep * 100, tuple(rect.getRect()), tuple(monitor.getRect()))
                return clipped
        return rect

    def complete(self, force_window=False, save_direct=False):
        """完成当前选区，进入原地编辑或交给独立编辑器。"""
        if self.session.completing or self.session.closing:
            return
        self.session.completing = True
        self.selection.finish()

        bounds = self.mapper.full_physical_local_rect()
        # 多选可以跨屏收集：只按"本视图这块屏"裁剪会把别屏的块丢成空矩形（保存/复制/编辑器全少块）。
        # 每块按**屏归属**登记它的视图，后面用那个视图自己的 image 与 mapper 裁剪和定位。
        regions, owners = [], []
        for rect in self.selection.rects:
            candidate = self._trim_region_to_monitor(rect.normalized())
            owner = self._region_owner_view(candidate)
            if owner is None:
                clipped = candidate.intersected(bounds)
                if clipped.isEmpty():
                    continue
                candidate, owner = clipped, self
            regions.append(candidate)
            owners.append(owner)
        logging.getLogger("screensnap").debug(
            "提交选区: %d 块 → 归属 %s", len(regions),
            [owner.monitor_rect.getRect() for owner in owners])
        if not regions:
            # 本视图没有可选区域：选区在别的显示器上时转交那个视图完成。
            # 这是"在副屏划选后按 E 没反应"的兜底路径 —— 无论归属启发式算得对不对，
            # 只要共享选区里还有落在别的视图上的区域，就由那个视图完成这次提交。
            for other in list(self.session.views or []):
                if other is None or other is self:
                    continue
                try:
                    other_bounds = other.mapper.full_physical_local_rect()
                except RuntimeError:
                    continue
                fallback = [rect.normalized().intersected(other_bounds)
                            for rect in self.selection.rects]
                fallback = [rect for rect in fallback if not rect.isEmpty()]
                if not fallback:
                    continue
                logging.getLogger("screensnap").info(
                    "本视图无区域，转交选区所在视图完成：from=%s to=%s force_window=%s 区域=%d",
                    self.monitor_rect.getRect(), other.monitor_rect.getRect(),
                    force_window, len(fallback))
                self.session.completing = False
                other.complete(force_window=force_window, save_direct=save_direct)
                return
            logging.getLogger("screensnap").debug(
                "提交时没有任何区域（视图=%s 选区=%d 块），关闭遮罩",
                self.monitor_rect.getRect(), len(self.selection.rects))
            self.session.completing = False
            self.close()
            return

        self.selection.rects[:] = regions
        self.selection.active = None
        rect = regions[-1]
        if self.mode == "capture":
            # 只有自由选区（手动框选 / UIA 点击 / 多选）才刷新“上次截图区域”。
            # 全屏、当前显示器是整屏预设，若也写入会把上次区域覆盖成整块主屏，
            # 表现为“上次截图永远抓主屏”。
            self.last_region.emit([rect.x() + self.bounds["left"],
                                   rect.y() + self.bounds["top"],
                                   rect.width(), rect.height()])
        else:
            logging.getLogger("screensnap").debug(
                "预设入口不更新上次截图区域: mode=%s 选区=(%d,%d,%d,%d)",
                self.mode, rect.x(), rect.y(), rect.width(), rect.height())

        uia_selection = self.session.element_selected and self.mode == "capture"
        action = ("edit" if self.session.right_capture_mode or uia_selection
              else self.selection_action())
        edit_after_capture = action == "edit"
        if (edit_after_capture and not save_direct and not force_window
            and self.mode not in ("fullscreen", "monitor")
            and not self.session.multi_select_mode
            and self.settings.get("inline_edit", False) and len(regions) == 1):
            view = self.inline_view_for_rect(rect)
            if view is None:
                intersections = [
                    (candidate.monitor_rect.x(), candidate.monitor_rect.y(),
                     candidate.monitor_rect.width(), candidate.monitor_rect.height(),
                     tuple(rect.intersected(candidate.monitor_rect).getRect()))
                    for candidate in self.session.views
                    if rect.intersects(candidate.monitor_rect)
                ]
                logging.getLogger("screensnap").debug(
                    "单区域编辑未完整落入单个显示器，改用独立编辑器: 来源=%s 选区=%s 显示器交集=%s",
                    "UIA" if uia_selection else "手绘",
                    tuple(rect.getRect()), intersections)
            if view is not None:
                area = (rect.x(), rect.y(), rect.right() + 1, rect.bottom() + 1)
                image = self.image.crop(area)
                alternate = self.alternate.crop(area) if self.alternate else None
                editor = InlineEditor(view, rect, image, alternate)
                editor.saved.connect(self.image_saved)
                # 原地编辑器「另存为」选的目录要经遮罩转发给应用，否则不会写回配置。
                editor.save_as_dir_chosen.connect(self.save_as_dir_chosen)
                editor.saved_silently.connect(self.image_saved_silently)
                editor.save_failed.connect(self.save_failed)
                editor.close_all_requested.connect(self.close_all_requested)
                editor.sticker_requested.connect(self.sticker_requested)
                self.session.inline_editor = editor
                self.sync_quick_sticker_shortcut(self.settings)
                self.sync_capture_save_shortcut(self.settings)
                self.sync_capture_action_shortcuts(self.settings)
                self.session.right_capture_mode = False
                self.session.element_selected = False
                primary = next((view for view in self.session.views if view.primary), None)
                shortcuts = getattr(primary, "capture_action_shortcuts", None)
                shortcut = shortcuts.get("multi_select") if shortcuts else None
                if shortcut is not None:
                    shortcut.setEnabled(False)
                editor.reset_region(rect, image, alternate,
                                    self.capture_cursor_enabled)
                self.session.completing = False
                # 让编辑器所在那块遮罩成为活动窗口（另一块屏的输入依赖它）。
                activate_window(self)
                logging.getLogger("screensnap").debug(
                    "原地编辑就绪：视图=%s 是否前台=%s", self.monitor_rect.getRect(),
                    self.isActiveWindow())
                self.update_all()
                editor.canvas.setFocus(Qt.ActiveWindowFocusReason)
                return

        images = []
        for region, owner in zip(regions, owners):
            local = self._region_in_view_local(region, owner)
            area = (local.x(), local.y(), local.right() + 1, local.bottom() + 1)
            image = owner.image.crop(area)
            alternate = owner.alternate.crop(area) if owner.alternate else None
            images.append((image, alternate))
        positions = [owner.mapper.physical_local_to_native_global(
            self._region_in_view_local(region, owner).topLeft())
            for region, owner in zip(regions, owners)]
        # S（快速保存）传 save_direct=True：右键/UIA 选区同样要直接保存，不得进编辑器。
        if (self.session.right_capture_mode or uia_selection) and not save_direct:
            self.session.editor_opened = True
            self.edit_requested.emit(images, positions)
            self.close()
            return
        if self.session.multi_select_mode and not save_direct:
            self.session.editor_opened = True
            self.edit_requested.emit(images, positions)
            self.close()
            return
        if action == "copy" and not (save_direct or force_window):
            from PySide6.QtGui import QGuiApplication
            from PySide6.QtCore import QMimeData
            # 多选收集了多块时只复制最后划选的那一块（与 Y 同一语义）。
            payload = QMimeData()
            payload.setImageData(to_qimage(images[-1][0]))
            QGuiApplication.clipboard().setMimeData(payload)
            logging.getLogger("screensnap").info("仅复制最后一个选区到剪贴板: %dx%d",
                                                 images[-1][0].width, images[-1][0].height)
            self.copy_done.emit(images[-1][0])
            self.close()
            return
        if save_direct or (action == "save" and not force_window):
            self.save_requested.emit(images, positions)
        elif force_window:
            self.session.editor_opened = True
            self.edit_requested.emit(images, positions)
        else:
            self.selected.emit(images, positions)
        self.close()

    def selection_action(self):
        """每种截图入口独立决定选区完成后编辑或保存。"""
        setting = {
            "fullscreen": "capture_fullscreen_action",
            "monitor": "capture_monitor_action",
            "repeat": "capture_repeat_action",
        }.get(self.mode)
        if setting is not None:
            return self.settings.get(setting, "save")
        return self.settings.get("capture_after_selection", "save")

    def save_selection(self):
        """用当前输出规则保存选区，不进入标注编辑器。"""
        # 规则：最近一次微调（抓住控制点按方向键）在 0.3 秒内 → S 当作"下移"；否则 S 是快捷保存。
        # 依据实测：按住鼠标时 resizing 有时为真有时为假，唯有"最近微调时间"稳定可判。
        recently_nudged = (time.monotonic() - getattr(self, "_nudge_at", 0.0)) <= 0.3
        if (getattr(self, "_grip_down", False)
                or self.selection.resizing is not None or self.selection.dragging is not None
                or (self.selection.nudge_corner is not None and recently_nudged)):
            # 在按键当刻判断（日志实证：此时 resizing=True），不依赖快捷键启用状态的刷新时机。
            logging.getLogger("screensnap").debug(
                "S 视为下移微调：视图=%s resizing=%s dragging=%s",
                self.monitor_rect.getRect(), self.selection.resizing is not None,
                self.selection.dragging is not None)
            self.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Down, Qt.NoModifier))
            return
        logging.getLogger("screensnap").debug(
            "S 快速保存：视图=%s 选区=%d 块 inline=%s resizing=%s dragging=%s "
            "nudge_index=%s nudge_corner=%s",
            self.monitor_rect.getRect(), len(self.selection.rects),
            self.inline_active(), self.selection.resizing is not None,
            self.selection.dragging is not None, self.selection.nudge_index,
            self.selection.nudge_corner)
        if self._delegate_to_owner("save_selection"):
            return
        if self.inline_active():
            # 原地编辑已打开：S 等价于它的「保存」按钮（保存并关闭），不再"无响应"。
            editor = self.session.inline_editor
            if editor is not None:
                logging.getLogger("screensnap").info("快速保存：保存原地编辑器")
                editor.execute("save")
            return
        if self.selection.rects:
            self.complete(save_direct=True)

    def focus_view_for_position(self, point):
        logical = self.mapper.physical_local_to_logical_local(point) + self.mapper.logical_bounds.topLeft()
        for view in self.session.views:
            if self.mapper.physical_local_rect_to_logical_global_rect(view.monitor_rect).toRect().contains(logical):
                return view
        return self

    def to_logical_point(self, point):
        """截图物理局部坐标 -> 当前窗口逻辑局部坐标。"""
        return self.mapper.physical_local_to_logical_local(point) - self.logical_window_offset()

    def to_physical_point(self, point):
        """当前窗口逻辑局部坐标 -> 截图物理局部坐标。"""
        return self.mapper.logical_local_to_physical_local(point + self.logical_window_offset())

    def to_logical_rect(self, rect):
        """截图物理局部矩形 -> 当前窗口逻辑局部矩形。"""
        logical = self.mapper.physical_local_rect_to_logical_local_rect(rect)
        offset = self.logical_window_offset()
        return logical.translated(-offset.x(), -offset.y())

    def element_size_hint(self, current_selection):
        source = None
        if self.session.element_selected and current_selection is not None:
            source = current_selection
        elif self.hover_rect is not None and self.selection.active is None:
            source = self.hover_rect
        if source is None:
            return None, None
        size = (source.width(), source.height())
        clipped = source.intersected(self.monitor_rect)
        if clipped.isEmpty():
            return None, size
        return self.to_logical_rect(clipped).toRect(), size

    def showEvent(self, event):
        super().showEvent(event)
        self.activateWindow()
        self.setFocus(Qt.ActiveWindowFocusReason)

    def preview_pixmap(self):
        """冻结画面的 QPixmap 缓存。

        reveal 与棋盘预览每帧都要把这块画面按矩形缩放贴出来；QPixmap 是面向屏幕绘制的
        格式，比每次都从 QImage 缩放要快。按 QImage.cacheKey 判断是否失效。
        """
        if self.preview is None:
            return None
        key = self.preview.cacheKey()
        cached = getattr(self, "_preview_cache_pixmap", None)
        if cached is not None and getattr(self, "_preview_cache_key", None) == key:
            return cached
        pixmap = QPixmap.fromImage(self.preview)
        self._preview_cache_pixmap = pixmap
        self._preview_cache_key = key
        return pixmap

    def mask_background_pixmap(self):
        """遮罩底图（冻结画面 + 半透明遮罩）缓存成 pixmap。

        以前每帧都「缩放绘制整块画面 + 全窗口半透明混合」，鼠标一动就要重绘整屏，
        十字线因此明显跟不上指针。缓存后每帧只做一次不透明 blit，画面/尺寸/遮罩
        参数变化时按 key 自动重建。
        """
        key = (self.preview.cacheKey() if self.preview is not None else 0,
               self.width(), self.height(),
               self.settings.get("mask_color", "#000000"),
               int(self.settings.get("mask_opacity", 70)))
        cached = getattr(self, "_mask_background_cache", None)
        if cached is not None and getattr(self, "_mask_background_key", None) == key:
            return cached
        ratio = self.devicePixelRatioF() or 1.0
        pixmap = QPixmap(int(self.width() * ratio), int(self.height() * ratio))
        pixmap.setDevicePixelRatio(ratio)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        if self.preview is not None:
            painter.drawImage(self.to_logical_rect(self.monitor_rect), self.preview,
                              self.monitor_rect)
        painter.fillRect(self.rect(), _mask_overlay_color(self.settings))
        painter.end()
        self._mask_background_cache = pixmap
        self._mask_background_key = key
        # 重建只在画面/尺寸/遮罩参数变化时发生，不是每帧，因此限频记 DEBUG 也不会刷屏。
        log_every(logging.getLogger("screensnap"), logging.DEBUG,
                  "mask-background-cache", key, 5.0,
                  "重建遮罩底图缓存: 尺寸=%dx%d 遮罩=%s/%s%%",
                  self.width(), self.height(), self.settings.get("mask_color", "#000000"),
                  int(self.settings.get("mask_opacity", 70)))
        return pixmap

    def _record_paint_cost(self, cost, now):
        """限频 DEBUG 记录遮罩重绘耗时（5 秒窗口的平均/峰值/次数）。

        用于在实机上确认「每次鼠标移动整窗重绘」的真实成本，作为是否做局部重绘的依据；
        5 秒才输出一行，不属于高频刷屏。
        """
        stats = self.paint_cost_stats
        stats[0] += cost
        stats[1] += 1
        stats[2] = max(stats[2], cost)
        if self.paint_cost_log_at and now - self.paint_cost_log_at < 5.0:
            return
        self.paint_cost_log_at = now
        logging.getLogger("screensnap").debug(
            "遮罩重绘耗时(5s窗口): 平均=%.1fms 峰值=%.1fms 次数=%d 窗口=%dx%d 悬停=%s",
            stats[0] / stats[1] * 1000, stats[2] * 1000, stats[1],
            self.width(), self.height(), self.hover_rect is not None)
        self.paint_cost_stats = [0.0, 0, 0.0]

    def paintEvent(self, event):
        """遮罩、锚点和 HUD 仅绘制在窗口表面，不写入原始截图。"""
        started = time.perf_counter()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.drawPixmap(0, 0, self.mask_background_pixmap())
        hover_clipped = None
        if (self.hover_rect is not None and self.selection.active is None and
                self.settings.get("window_hover_fill_mode", "reveal") == "reveal"):
            hover_clipped = self.hover_rect.intersected(self.monitor_rect)
            preview = self.preview_pixmap()
            if not hover_clipped.isEmpty() and preview is not None:
                painter.drawPixmap(self.to_logical_rect(hover_clipped), preview,
                                   QRectF(hover_clipped))
        selection_paths = []
        for rect in self.selection.rects + ([self.selection.active] if self.selection.active else []):
            clipped = rect.intersected(self.monitor_rect)
            if clipped.isEmpty():
                continue
            logical_rect = self.to_logical_rect(clipped)
            selection_rect = self.to_logical_rect(rect)
            if self.settings.get("editor_image_round_corners", True):
                scale = logical_rect.width() / max(1, clipped.width())
                radius = min(self.settings.get("editor_image_corner_radius", 16) * scale,
                             selection_rect.width() / 2, selection_rect.height() / 2)
                selection_path = QPainterPath()
                selection_path.addRoundedRect(selection_rect, radius, radius)
            else:
                selection_path = QPainterPath()
                selection_path.addRect(selection_rect)
            selection_paths.append(selection_path)
            painter.save()
            painter.setClipPath(selection_path)
            preview = self.preview_pixmap()
            if preview is not None:
                painter.drawPixmap(logical_rect, preview, QRectF(clipped))
            painter.restore()
            painter.setPen(QPen(QColor(self.settings.get("selection_border_color", "#168cff")), 1))
            painter.drawPath(selection_path)
            painter.setPen(QPen(QColor("#00d7aa"), 2))
            for handle, _, _, axis in self.selection.handles_for(rect):
                if not self.monitor_rect.adjusted(-12, -12, 12, 12).contains(handle):
                    continue
                logical_handle = self.to_logical_point(handle)
                size = 11 if axis == "both" else 9
                anchor = QRect(logical_handle.x() - size // 2, logical_handle.y() - size // 2, size, size)
                painter.fillRect(anchor.adjusted(-1, -1, 1, 1), QColor("#ffffff"))
                if self.settings["anchor_style"] == "fill":
                    painter.fillRect(anchor, QColor("#00d7aa"))
                else:
                    painter.drawRect(anchor)
        if self.settings["crosshair"]:
            logical_position = self.to_logical_point(self.position)
            painter.setPen(QPen(QColor(self.settings.get("crosshair_color", "#ff0000")),
                                self.settings.get("crosshair_width", 1)))
            painter.drawLine(logical_position.x(), 0, logical_position.x(), self.height())
            painter.drawLine(0, logical_position.y(), self.width(), logical_position.y())
        current_selection = self.selection.active or (
            self.selection.rects[-1] if self.selection.rects else None
        )
        # 提示条本身是子控件 InfoBar（压在编辑画布之上），这里只画尺寸徽标，并避开提示条。
        monitor_area = self.to_logical_rect(self.monitor_rect).toRect()
        element_rect, element_size = self.element_size_hint(current_selection)
        if element_rect is not None and not monitor_area.intersects(element_rect):
            element_rect = None
        self.info_bar_rect = paint_info_badge(
            painter, monitor_area, element_rect, element_size,
            element_border_color=self.settings.get("window_hover_border_color", "#168cff"),
            element_text_color=self.settings.get("window_hover_text_color", "#F4FFFC"),
            element_background_color=self.settings.get("window_hover_badge_color", "#102A31"),
            element_font_size=self.settings.get("window_hover_font_size", 12),
            element_border_width=self.settings.get("window_hover_border_width", 2),
            bar=self.info_bar.bar if self.info_bar is not None else None)
        painter.setPen(QPen(QColor(self.settings.get("selection_border_color", "#ff0000")), 1))
        painter.setBrush(Qt.NoBrush)
        for selection_path in selection_paths:
            painter.drawPath(selection_path)
        if self.hover_rect is not None and self.selection.active is None:
            # 悬停候选：青色半透明块，与红色选区边框区分。
            clipped = self.hover_rect.intersected(self.monitor_rect)
            if not clipped.isEmpty():
                outline = QColor(self.settings.get(
                    "window_hover_border_color",
                    self.settings.get("window_hover_color", "#168CFF")))
                painter.setPen(QPen(outline, self.settings.get("window_hover_border_width", 2)))
                painter.setBrush(Qt.NoBrush if self.settings.get(
                    "window_hover_fill_mode", "reveal") == "reveal"
                    else _hover_fill_color(self.settings))
                painter.drawRect(self.to_logical_rect(clipped))
                painter.setBrush(Qt.NoBrush)
        self._draw_ruler(painter)
        ended = time.perf_counter()
        self._record_paint_cost(ended - started, ended)

    def _draw_ruler(self, painter):
        """在遮罩边缘绘制像素标尺（顶部/右侧仅短线，底部/左侧带数值），帮助定位。"""
        if not self.settings.get("ruler_enabled", False):
            return
        from PySide6.QtGui import QFont
        rect = self.rect()
        color = QColor(self.settings.get("ruler_color", "#00ad91"))
        painter.save()
        painter.setPen(QPen(color, 1))
        font = QFont()
        font.setPixelSize(9)
        painter.setFont(font)
        step = 50
        major = step * 5
        small, big = 5, 9
        for x in range(0, rect.width() + 1, step):
            length = big if (x % major == 0) else small
            painter.drawLine(x, 0, x, length)
            painter.drawLine(x, rect.height() - length, x, rect.height())
            if x % major == 0 and x > 0:
                painter.drawText(x + 2, rect.height() - length - 2, str(x))
        for y in range(0, rect.height() + 1, step):
            length = big if (y % major == 0) else small
            painter.drawLine(0, y, length, y)
            painter.drawLine(rect.width() - length, y, rect.width(), y)
            if y % major == 0 and y > 0:
                painter.drawText(length + 2, y - 2, str(y))
        painter.restore()

    def wheelEvent(self, event):
        super().wheelEvent(event)

    def mousePressEvent(self, event):
        """左键开始创建选区，已有选区的命中由选区对象判定。"""
        # 取色优先于"原地编辑分支"：原地编辑打开时下面的 inline 分支会直接 return，
        # 取色分支（Alt/Ctrl+左键取样）永远走不到 —— 这正是"划选后无法取色"的原因。
        if (self.picker_mode and event.button() == Qt.LeftButton
                and QGuiApplication.keyboardModifiers() & (Qt.AltModifier | Qt.ControlModifier)):
            point = self.to_physical_point(event.position().toPoint())
            x = point.x() + self.bounds["left"]
            y = point.y() + self.bounds["top"]
            logging.getLogger("screensnap").debug(
                "取色取样：视图=%s 鼠标物理点=(%d,%d) 取样点=(%d,%d) inline=%s",
                self.monitor_rect.getRect(), point.x(), point.y(), x, y, self.inline_active())
            if 0 <= x < self.image.width and 0 <= y < self.image.height:
                r, g, b = self.image.convert("RGB").getpixel((x, y))
                color = "#%02x%02x%02x" % (r, g, b)
                QGuiApplication.clipboard().setText(color)
                self.picker_color = color
                self.picker_copied.emit(color)
            self.update_all()
            event.accept()
            return
        # 自己记录左键是否按下：selection.resizing/dragging 在部分路径下不可靠，
        # 而"S 该微调还是该保存"必须只看"手是否还按着"。
        self._grip_down = event.button() == Qt.LeftButton
        if self.inline_active():
            if event.button() == Qt.LeftButton:
                self.position = self.to_physical_point(event.position().toPoint())
                hit = self.selection.handle_at(self.position)
                if hit is not None or self.selection.border_at(self.position):
                    if hit is not None:
                        self.resize_cursor = hit[2]
                    self.selection.begin(self.position)
                    if self.session.inline_editor is not None:
                        self.session.inline_editor.begin_region_resize()
            self.update_all()
            return
        if self.picker_mode:
            if event.button() == Qt.LeftButton:
                # 取色用 Alt/Ctrl + 左键取样，避免与框选 / UIA 选择冲突。
                if QGuiApplication.keyboardModifiers() & (Qt.AltModifier | Qt.ControlModifier):
                    point = self.to_physical_point(event.position().toPoint())
                    x = point.x() + self.bounds["left"]
                    y = point.y() + self.bounds["top"]
                    if 0 <= x < self.image.width and 0 <= y < self.image.height:
                        r, g, b = self.image.convert("RGB").getpixel((x, y))
                        color = "#%02x%02x%02x" % (r, g, b)
                        QGuiApplication.clipboard().setText(color)
                        self.picker_color = color
                        self.picker_copied.emit(color)
                    self.update_all()
            return
        if event.button() in (Qt.LeftButton, Qt.RightButton):
            self.position = self.to_physical_point(event.position().toPoint())
            if event.button() == Qt.RightButton:
                self.selection.begin_new(self.position)
            else:
                self.selection.begin(self.position)
            if self.quick_sticker_armed and event.button() == Qt.LeftButton:
                self.quick_sticker_armed = False
                self.quick_sticker_pending = True
            # 记录按下位置，用于区分“单击确认元素”和“拖动手绘选区”。
            self.press_position = QPoint(self.position)
            hit = self.selection.handle_at(self.position)
            if (hit is None and not any(rect.adjusted(-6, -6, 6, 6).contains(self.position)
                                        for rect in self.selection.rects)):
                self.session.element_selected = False
            if hit is not None:
                self.resize_cursor = hit[2]
                self.setCursor({"nwse": Qt.SizeFDiagCursor, "nesw": Qt.SizeBDiagCursor,
                                "horizontal": Qt.SizeHorCursor,
                                "vertical": Qt.SizeVerCursor}[self.resize_cursor])

    def mouseMoveEvent(self, event):
        """将显示器及窗口的全局边缘转换为遮罩内的局部坐标。"""
        if self.inline_active():
            self.position = self.to_physical_point(event.position().toPoint())
            hit = self.selection.handle_at(self.position)
            if self.selection.resizing is not None or hit is not None:
                cursor = self.resize_cursor if self.selection.resizing is not None else hit[2]
                self.resize_cursor = cursor
                cursor_shapes = {"nwse": Qt.SizeFDiagCursor, "nesw": Qt.SizeBDiagCursor,
                                 "horizontal": Qt.SizeHorCursor, "vertical": Qt.SizeVerCursor}
                self.setCursor(cursor_shapes[cursor])
            else:
                self.setCursor(Qt.SizeAllCursor if self.selection.dragging is not None or
                               self.selection.border_at(self.position) else Qt.ArrowCursor)
            if self.selection.resizing is not None or self.selection.dragging is not None:
                self.selection.update(self.position)
            self.update_all()
            return
        self.position = self.to_physical_point(event.position().toPoint())
        hit = self.selection.handle_at(self.position)
        if self.selection.resizing is not None or hit is not None:
            cursor = self.resize_cursor if self.selection.resizing is not None else hit[2]
            self.resize_cursor = cursor
            cursor_shapes = {"nwse": Qt.SizeFDiagCursor, "nesw": Qt.SizeBDiagCursor,
                             "horizontal": Qt.SizeHorCursor, "vertical": Qt.SizeVerCursor}
            self.setCursor(cursor_shapes[cursor])
        elif self.selection.dragging is not None or any(rect.contains(self.position) for rect in self.selection.rects):
            self.setCursor(Qt.SizeAllCursor)
        else:
            self.setCursor(Qt.CrossCursor)
        if any((self.selection.start is not None, self.selection.dragging is not None,
            self.selection.resizing is not None)):
            x_edges = [coordinate for screen in self.monitors for coordinate in
                       (screen["left"] - self.bounds["left"], screen["left"] + screen["width"] - self.bounds["left"])]
            y_edges = [coordinate for screen in self.monitors for coordinate in
                       (screen["top"] - self.bounds["top"], screen["top"] + screen["height"] - self.bounds["top"])]
            x_edges.extend(edge - self.bounds["left"] for left, top, right, bottom in self.window_edges
                           for edge in (left, right))
            y_edges.extend(edge - self.bounds["top"] for left, top, right, bottom in self.window_edges
                           for edge in (top, bottom))
            self.selection.update(self.position, x_edges, y_edges)
        # 正在手绘或拖动时不显示悬停高亮，避免和选区抢视觉焦点。
        if (self.selection.start is None and self.selection.dragging is None
                and self.selection.resizing is None):
            # 悬停识别（UIA 跨进程查询）可能耗几十毫秒，同步做会把鼠标移动事件堵住、
            # 十字线就落在指针后面。推迟到本轮事件处理完之后，先按新位置把十字线画出来。
            self.schedule_hover()
        else:
            self.hover_rect = None
        self.update_all()

    def mouseReleaseEvent(self, event):
        self._grip_down = False
        """结束当前选区的创建、拖动或缩放。"""
        if self.inline_active():
            if event.button() == Qt.LeftButton and (self.selection.resizing is not None or
                                                    self.selection.dragging is not None):
                self.position = self.to_physical_point(event.position().toPoint())
                self.selection.finish()
                if QWidget.mouseGrabber() is self:
                    self.releaseMouse()
                self.update_inline_region()
                if self.session.inline_editor is not None:
                    self.session.inline_editor.end_region_resize()
            return
        if event.button() in (Qt.LeftButton, Qt.RightButton):
            self.position = self.to_physical_point(event.position().toPoint())
            active = self.selection.active
            new_region = bool(active and active.width() > 2 and active.height() > 2)
            self.selection.finish()
            if event.button() == Qt.RightButton and new_region:
                self.session.right_capture_mode = True
            # 几乎没移动的单击视为确认，直接选中当前高亮的窗口或控件。
            selected_element = False
            if (event.button() == Qt.LeftButton and self.press_position is not None
                    and (self.position - self.press_position).manhattanLength() <= 3):
                if self.session.element_selected:
                    selected_element = True
                else:
                    selected_element = self.select_hover()
            self.press_position = None
            self.update_all()
            if self.quick_sticker_pending:
                self.quick_sticker_pending = False
                if self.selection.rects:
                    self.trigger_quick_sticker()
                    return
            if (event.button() == Qt.LeftButton
                    and not self.session.multi_select_mode
                    and not self.session.right_capture_mode
                    and (new_region or selected_element)
                    and self.selection.rects):
                self.complete()

    def mouseDoubleClickEvent(self, event):
        """双击提交所有有效选区，没有选区时仅关闭遮罩。"""
        if self.inline_active():
            if event.button() == Qt.LeftButton:
                self.session.inline_editor.execute("save")
                event.accept()
            return
        if event.button() == Qt.LeftButton:
            self.complete()
        elif event.button() == Qt.RightButton:
            self.complete(save_direct=not self.session.right_capture_mode)

    def keyPressEvent(self, event):
        # 只处理真正的 Qt 按键事件：Mock 事件喂给 QKeySequence 会在转换阶段原生崩溃。
        if (isinstance(event, QKeyEvent) and event.type() == QEvent.KeyPress
                and not event.isAutoRepeat() and self.session.inline_editor is not None):
            logging.getLogger("screensnap").debug(
                "遮罩收到按键(快速编辑中)：视图=%s key=%s mods=%s",
                self.monitor_rect.getRect(), event.key(), int(event.modifiers()))
            hide_key, hide_default = TOOLBAR_HIDE_KEY
            hide_sequence = QKeySequence(self.settings.get(hide_key, hide_default))
            if hide_sequence.isEmpty():
                hide_sequence = QKeySequence(hide_default)
            if QKeySequence(event.keyCombination()) == hide_sequence:
                logging.getLogger("screensnap").debug(
                    "工具栏隐藏键（遮罩按键）触发：视图=%s", self.monitor_rect.getRect())
                self.toggle_inline_toolbar()
                event.accept()
                return

        """处理取消、提交、固定尺寸创建与最后选区的像素微调。"""
        key = event.key()
        if key == Qt.Key_Escape and not self.inline_active():
            if self.picker_mode:
                self.picker_mode = False
                self.picker_color = None
                self.update_all()
                event.accept()
                return
            if self.primary:
                self.close()
            event.accept()
            return
        if self.inline_active():
            if key == Qt.Key_Escape:
                self.close()
            elif event.modifiers() == Qt.NoModifier:
                dx = (-1 if key in (Qt.Key_A, Qt.Key_Left) else 1 if key in (Qt.Key_D, Qt.Key_Right) else 0)
                dy = (-1 if key in (Qt.Key_W, Qt.Key_Up) else 1 if key == Qt.Key_Down else 0)
                if (dx or dy) and self.selection.rects:
                    # 只有指针正压在手柄/边线上，或此前真正抓住过（nudge_corner 记忆）才微调：
                    # 从未抓过时方向键既不移动、也不写下微调记忆（记忆会顺带把 S 快捷保存禁用掉）。
                    if (not getattr(self, "_grip_down", False)
                            and self.selection.resizing is None
                            and self.selection.dragging is None
                            and self.selection.nudge_corner is None):
                        event.accept()
                        return
                    if self.selection.handle_at(self.position) is None:
                        self.selection.nudge_corner = None
                    if self.selection.resizing is not None or self.selection.dragging is not None:
                        self.releaseMouse()
                    self.selection.move_last(dx, dy)
                    self._nudge_at = time.monotonic()      # 供"0.3 秒内 S = 下移"判定
                    self.position = (QPoint(self.selection.nudge_corner[2]) if self.selection.nudge_corner
                                     else self.position + QPoint(dx, dy))
                    self.update_inline_region()
                    view = self.focus_view_for_position(self.position)
                    QCursor.setPos(view.mapToGlobal(view.to_logical_point(self.position)))
            return
        if key == Qt.Key_Tab:
            self.cycle_element(-1 if event.modifiers() & Qt.ShiftModifier else 1)
            event.accept()
            return
        if key == Qt.Key_Escape:
            self.close()
        elif key in (Qt.Key_Return, Qt.Key_Enter):
            self.complete()
        else:
            dx = (-1 if key in (Qt.Key_A, Qt.Key_Left) else 1 if key in (Qt.Key_D, Qt.Key_Right) else 0)
            dy = (-1 if key in (Qt.Key_W, Qt.Key_Up) else 1 if key == Qt.Key_Down else 0)
            if dx or dy:
                # 只有指针正压在手柄/边线上，或此前真正抓住过（nudge_corner 记忆）才微调：
                # 从未抓过时方向键既不移动、也不写下微调记忆（记忆会顺带把 S 快捷保存禁用掉）。
                if (not getattr(self, "_grip_down", False)
                        and self.selection.resizing is None
                        and self.selection.dragging is None
                        and self.selection.nudge_corner is None):
                    event.accept()
                    return
                if self.selection.rects and self.selection.handle_at(self.position) is None:
                    self.selection.nudge_corner = None
                self.selection.move_last(dx, dy)
                self._nudge_at = time.monotonic()          # 供"0.3 秒内 S = 下移"判定
                if self.selection.rects:
                    self.position = (QPoint(self.selection.nudge_corner[2]) if self.selection.nudge_corner
                                     else self.position + QPoint(dx, dy))
                    view = self.focus_view_for_position(self.position)
                    QCursor.setPos(view.mapToGlobal(view.to_logical_point(self.position)))
        self.update_all()

    def keyReleaseEvent(self, event):
        if self.quick_sticker_armed and not event.isAutoRepeat():
            sequence = self.quick_sticker_shortcut.key()
            if sequence.count() and sequence[0].key() == event.key():
                self.quick_sticker_armed = False
        super().keyReleaseEvent(event)

    def _quick_sticker_state(self, name, default=False):
        """快速贴图的按住/待发/已消费状态按 session 共享。

        每个视图都有同键的 ApplicationShortcut，Qt 只会激活其中一个视图，而左键可能落在
        另一个视图上 —— 状态若各视图各存一份，就会表现为“只有一个屏幕能快速贴图”。
        """
        session = getattr(self, "session", None)
        if session is None:
            return getattr(self, "_" + name, default)
        return getattr(session, name, default)

    def _set_quick_sticker_state(self, name, value):
        session = getattr(self, "session", None)
        if session is None:
            setattr(self, "_" + name, value)
        else:
            setattr(session, name, value)

    @property
    def quick_sticker_armed(self):
        return self._quick_sticker_state("quick_sticker_armed")

    @quick_sticker_armed.setter
    def quick_sticker_armed(self, value):
        self._set_quick_sticker_state("quick_sticker_armed", bool(value))

    @property
    def quick_sticker_pending(self):
        return self._quick_sticker_state("quick_sticker_pending")

    @quick_sticker_pending.setter
    def quick_sticker_pending(self, value):
        self._set_quick_sticker_state("quick_sticker_pending", bool(value))

    @property
    def quick_sticker_consumed(self):
        return self._quick_sticker_state("quick_sticker_consumed")

    @quick_sticker_consumed.setter
    def quick_sticker_consumed(self, value):
        self._set_quick_sticker_state("quick_sticker_consumed", bool(value))

    def _owner_view(self):
        """返回执行选区动作的视图：选区所在的显示器 → 鼠标所在显示器 → 自己。

        注意 selection 是 session 共享的一份（各视图的 rects 永远相同），所以不能用
        "谁的 rects 非空"来判断归属 —— 那会永远返回列表里第一个视图，导致在别的屏上
        框选后按 E/S/F/Y 时动作落到错误的屏（对话框弹错屏、进不去编辑器等）。
        这里改用选区与各视图 monitor_rect 的相交面积判定，跨屏选区取相交最大的那个。
        """
        views = [v for v in (getattr(self.session, "views", None) or []) if v is not None]
        if not views:
            return self
        selection = getattr(self, "selection", None)
        rect = None
        if selection is not None:
            rect = selection.active or (selection.rects[-1] if selection.rects else None)
        if rect is not None:
            best, best_area = None, 0
            for view in views:
                monitor = getattr(view, "monitor_rect", None)
                if monitor is None:
                    continue
                overlap = rect.intersected(monitor)
                area = overlap.width() * overlap.height()
                if area > best_area:
                    best, best_area = view, area
            if best is not None:
                logging.getLogger("screensnap").debug(
                    "归属判定：选区 %s → 视图 %s（相交面积 %d，共 %d 个视图）",
                    rect.getRect(), best.monitor_rect.getRect(), best_area, len(views))
                return best
            logging.getLogger("screensnap").debug(
                "归属判定：选区 %s 不与任何视图相交，改用鼠标所在屏（共 %d 个视图，"
                "monitor_rects=%s）", rect.getRect(), len(views),
                [v.monitor_rect.getRect() for v in views
                 if getattr(v, "monitor_rect", None) is not None])
        point = QCursor.pos()
        for view in views:
            try:
                if view.geometry().contains(point):
                    return view
            except RuntimeError:
                continue
        return self if self in views else views[0]

    def _delegate_to_owner(self, method_name):
        """把选区动作交给拥有选区（或鼠标所在）的视图执行；已在本视图则不处理。"""
        owner = self._owner_view()
        if owner is None or owner is self:
            return False
        handler = getattr(owner, method_name, None)
        if handler is None:
            return False
        logging.getLogger("screensnap").debug(
            "选区动作 %s 交给选区/鼠标所在视图执行：from=%s to=%s",
            method_name, getattr(self, "monitor_rect", None) and self.monitor_rect.getRect(),
            getattr(owner, "monitor_rect", None) and owner.monitor_rect.getRect())
        handler()
        return True

    def _picker_state(self, name, default=None):
        """取色模式与色值按 session 共享：快捷键可能落在另一块屏的视图上。"""
        session = getattr(self, "session", None)
        if session is None:
            return getattr(self, "_" + name, default)
        return getattr(session, name, default)

    def _set_picker_state(self, name, value):
        session = getattr(self, "session", None)
        if session is None:
            setattr(self, "_" + name, value)
        else:
            setattr(session, name, value)

    @property
    def picker_mode(self):
        return bool(self._picker_state("picker_mode", False))

    @picker_mode.setter
    def picker_mode(self, value):
        self._set_picker_state("picker_mode", bool(value))

    @property
    def picker_color(self):
        return self._picker_state("picker_color", None)

    @picker_color.setter
    def picker_color(self, value):
        self._set_picker_state("picker_color", value)

    def trigger_quick_sticker(self):
        if (self.quick_sticker_consumed or not self.quick_sticker_enabled or
                self.inline_active()):
            return
        if any((self.selection.active is not None,
                self.selection.dragging is not None,
                self.selection.resizing is not None)):
            if not self.quick_sticker_pending:
                self.quick_sticker_pending = True
                logging.getLogger("screensnap").debug("快速贴图等待当前选区完成")
            return
        if not self.selection.rects:
            self.quick_sticker_armed = True
            return
        self.quick_sticker_consumed = True
        region = self.selection.rects[-1].normalized()
        position = self.mapper.physical_local_to_native_global(region.topLeft())
        self.quick_sticker_requested.emit(self._selected_image(), position)
        self.close()

    def _selected_image(self):
        """按当前选区生成一个已完成圆角/边框/阴影的贴图图像。"""
        region = self.selection.rects[-1].normalized()
        area = (region.x(), region.y(), region.right() + 1, region.bottom() + 1)
        image = self.image.crop(area)
        if self.alternate is not None:
            image = image.copy()
        return apply_output_effects(image, self.settings,
                                    self.settings.get("editor_image_round_corners", True),
                                    self.settings.get("editor_image_corner_radius", 16))

    def detect_elements(self):
        """临时隐藏遮罩后识别鼠标下的窗口链，避免遮罩自己挡住命中测试。"""
        if not self.primary:
            return self.session.views[0].element_chain
        point = self.mapper.native_global_to_physical_global(QCursor.pos())
        self.hide()
        try:
            self.element_chain = element_chain((point.x(), point.y()),
                                               self.settings.get("element_depth", 8),
                                               use_uia=self.uia_enabled(),
                                               exclude_hwnd=int(self.winId()),
                                               debug_tree=bool(self.settings.get("uia_debug_tree", False)))
        finally:
            self.show()
        logging.getLogger("screensnap").debug(
            "窗口元素重查: 鼠标物理点=(%d,%d) UIA=%s 层级上限=%s 候选=%d 层 %s",
            point.x(), point.y(), self.uia_enabled(),
            self.settings.get("element_depth", 8),
            len(self.element_chain), self.element_chain)
        self.element_index = -1
        return self.element_chain

    def hover_detection_enabled(self):
        """悬停识别需要总识别开关和本项开关同时打开。"""
        return bool(self.settings.get("window_detection", True)
                    and self.settings.get("window_hover_detect", True))

    def uia_enabled(self):
        """是否优先用 UIA 读自绘界面内部控件；库没装时会自动退回句柄识别。"""
        return bool(self.settings.get("window_uia_detect", False))

    def _apply_uia_limits(self):
        """把「无障碍识别与精度」里的性能设置推给 UIA 模块（进程内全局生效）。"""
        from core.window_uia import set_limits

        set_limits(read_budget=self.settings.get("uia_read_budget"),
                   children_limit=self.settings.get("uia_children_limit"),
                   slow_seconds=self.settings.get("uia_slow_seconds"))

    def detect_hover(self):
        """识别鼠标下的元素用于高亮；不隐藏遮罩，避免移动时闪烁。

        每个显示器的遮罩各自检测：鼠标停在哪个屏，就由那个屏的窗口收事件并高亮。
        UIA 查询必须在本线程里同步执行：查询内部会对本线程创建的遮罩做 SetWindowLong
        命中穿透，挪到工作线程会和等待结果的主线程互相死锁（见 core/window_uia.py 头部说明）。
        """
        point = self.mapper.native_global_to_physical_global(QCursor.pos())
        # 悬停只需要最内层元素：deepest_only 跳过父链上溯，省下 element_depth 层跨进程读取。
        chain = element_chain((point.x(), point.y()), self.settings.get("element_depth", 8),
                              use_uia=self.uia_enabled(),
                              exclude_hwnd=int(self.winId()),
                              debug_tree=bool(self.settings.get("uia_debug_tree", False)),
                              deepest_only=True)
        if not chain:
            self.hover_rect = None
            self.hover_source_rect = None
            return None
        # 取最内层：鼠标停在按钮上就选中按钮，停在窗口空白处选中整个窗口。
        left, top, right, bottom = chain[-1]
        self.hover_source_rect = (left, top, right, bottom)
        self.hover_rect = QRect(left - self.bounds["left"], top - self.bounds["top"],
                                right - left, bottom - top)
        return self.hover_rect

    def schedule_hover(self):
        """把一次悬停识别排到当前事件处理之后（合并同一轮里的多次移动）。"""
        timer = getattr(self, "_hover_timer", None)
        if timer is None:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.setInterval(0)
            timer.timeout.connect(self.poll_hover)
            self._hover_timer = timer
        if not timer.isActive():
            timer.start()

    def poll_hover(self):
        """按时间节流刷新悬停高亮，鼠标移动事件非常密集。"""
        # 遮罩已经不可见（截图结束）时不再查询系统窗口。
        if not self.isVisible():
            self.hover_rect = None
            return
        point = self.mapper.native_global_to_physical_global(QCursor.pos())
        # 每个显示器各有一个遮罩：鼠标在哪个屏，就由那个屏的窗口收事件并高亮，
        # 其它屏的遮罩收不到鼠标事件，它上面残留的高亮永远不会被自己的逻辑清掉。
        # 所以当前活跃屏在轮询一开始就清掉其它屏的残留高亮；鼠标跨屏后旧屏的
        # 元素框立即消失，不会“鼠标都移出去了还亮着”。
        for view in self.session.views:
            if view is not self:
                view.hover_rect = None
        # 鼠标不在本显示器（理论上不会发生，因为本屏遮罩才会收到事件）时同样清掉自己。
        local_point = self.mapper.physical_global_to_local(point)
        if not self.monitor_rect.contains(local_point):
            self.hover_rect = None
            log_every(logging.getLogger("screensnap"), logging.DEBUG,
                      "hover-outside-monitor", None, 1.0,
                      "悬停识别跳过: 鼠标物理全局点=(%d,%d) 局部点=(%d,%d) 不在当前遮罩物理局部显示器区域=%s",
                      point.x(), point.y(), local_point.x(), local_point.y(),
                      (self.monitor_rect.x(), self.monitor_rect.y(),
                       self.monitor_rect.width(), self.monitor_rect.height()))
            return
        logger = logging.getLogger("screensnap")
        # 鼠标移出高亮区域时立即取消，不等下一次轮询，避免高亮滞后。
        if self.hover_rect is not None and not self.hover_rect.contains(self.position):
            self.hover_rect = None
            self.hover_stamp = 0.0
        # 结果复用：光标仍停在上次识别出的元素内、且几乎没移动时沿用高亮，不重复查询；
        # 移动超过阈值才重查，避免大容器里冒出更小控件时高亮不更新。
        if (self.hover_rect is not None and self.hover_query_point is not None
                and self.hover_rect.contains(self.position)
                and (point - self.hover_query_point).manhattanLength() <=
                     int(self.settings.get("window_hover_reuse_radius", HOVER_REUSE_RADIUS))
                and time.monotonic() - self.hover_stamp <= HOVER_REUSE_SECONDS):
            return
        # 已经画出选区后不再提示元素：这时用户在调整或确认自己的选区，
        # 继续高亮只会干扰（也是“截图完了还在识别”的来源）。
        if not self.hover_detection_enabled() or self.selection.rects:
            self.hover_rect = None
            self.hover_source_rect = None
            return
        # 悬停高亮按时间节流刷新（间隔由“悬停识别刷新间隔”设置控制，毫秒）：
        # 数值越小越跟手（灵敏度越高），但调用系统识别接口更频繁；开启 UIA 时无障碍查询
        # 更慢，间隔自动翻倍以兼容。仍卡在 SLOW_SECONDS(0.4s) 熔断阈值内，不会因变快而误关 UIA。
        interval = (self.settings.get("window_hover_interval", 80) / 1000.0) * (2 if self.uia_enabled() else 1)
        stamp = time.monotonic()
        if stamp - self.hover_stamp < interval:
            return
        self.hover_stamp = stamp
        self.hover_query_point = QPoint(point)
        rect = self.detect_hover()
        if rect is None:
            log_every(logger, logging.DEBUG, "hover-none", None, 1.0,
                      "悬停识别: 鼠标物理点(%d,%d) 没有可识别的元素",
                      point.x(), point.y())
            return
        # 高亮结果限频记录为 DEBUG；记录原始全局矩形、遮罩局部矩形及实际绘制区域。
        visible = rect.intersected(self.monitor_rect)
        logical = self.to_logical_rect(visible).toRect() if not visible.isEmpty() else QRect()
        paint_enabled = self.selection.active is None
        log_every(logger, logging.DEBUG, "hover",
                  None, 1.0,
              "悬停高亮候选: 鼠标物理点(%d,%d) 原始全局矩形=%s 遮罩物理局部矩形=(%d,%d,%dx%d) 显示器交集=%s 绘制逻辑矩形=%s 允许绘制=%s",
                  point.x(), point.y(), self.hover_source_rect,
                  rect.x(), rect.y(), rect.width(), rect.height(),
                  (visible.x(), visible.y(), visible.width(), visible.height()) if not visible.isEmpty() else None,
              (logical.x(), logical.y(), logical.width(), logical.height()) if not logical.isEmpty() else None,
              paint_enabled)
        self.update_all()


    def select_hover(self):
        """单击时把当前高亮的窗口或控件直接作为选区。"""
        rect = self.hover_rect
        if rect is None or rect.isEmpty():
            return False
        if not self.session.multi_select_mode:
            self.selection.rects.clear()
        self.selection.rects.append(QRect(rect))
        self.selection.active = None
        self.session.element_selected = True
        logging.getLogger("screensnap").info(
            "单击选中窗口元素: (%d,%d,%dx%d)", rect.x(), rect.y(), rect.width(), rect.height())
        return True

    def apply_element(self, index):
        """把某一层窗口或控件的矩形直接作为选区。"""
        left, top, right, bottom = self.element_chain[index]
        local = QRect(left - self.bounds["left"], top - self.bounds["top"],
                      right - left, bottom - top)
        self.selection.rects.clear()
        self.selection.rects.append(local)
        self.selection.active = None
        self.session.element_selected = True
        visible = local.intersected(self.monitor_rect)
        logical = self.to_logical_rect(visible).toRect() if not visible.isEmpty() else QRect()
        self.update_all()
        logging.getLogger("screensnap").debug(
            "应用第 %d 层元素选区: 原始全局矩形=%s 遮罩物理局部矩形=(%d,%d,%dx%d) "
            "显示器交集=%s 绘制逻辑矩形=%s bounds=%s",
            index + 1, self.element_chain[index],
            local.x(), local.y(), local.width(), local.height(),
            (visible.x(), visible.y(), visible.width(), visible.height()) if not visible.isEmpty() else None,
            (logical.x(), logical.y(), logical.width(), logical.height()) if not logical.isEmpty() else None,
            self.bounds)

    def cycle_element(self, step):
        """在鼠标下的窗口与控件层级间切换选区。"""
        if not self.primary:
            self.session.views[0].cycle_element(step)
            return
        if not self.settings.get("window_detection", True):
            return
        if not self.element_chain and not self.detect_elements():
            logging.getLogger("screensnap").debug("鼠标下没有可识别的窗口或控件")
            return
        if self.element_index < 0:
            self.element_index = 0 if step > 0 else len(self.element_chain) - 1
        else:
            self.element_index = (self.element_index + step) % len(self.element_chain)
        self.apply_element(self.element_index)
        logging.getLogger("screensnap").debug(
            "切换到第 %d/%d 层窗口元素", self.element_index + 1,
            len(self.element_chain))

    def _fixed_size_target(self):
        """返回 F 应当重置尺寸的区域：优先上次 F 建的区域，其次左键拖出的区域。

        右键（元素）选出的区域不参与：按 F 只在它们之外新建一个固定尺寸区域，互不干扰。
        """
        if self.session.right_capture_mode or self.session.element_selected:
            return None
        target = getattr(self.session, "fixed_size_rect", None)
        if target is not None and any(target == rect for rect in self.selection.rects):
            return target
        return self.selection.rects[-1] if self.selection.rects else None

    def apply_fixed_size(self, width, height):
        """按 F 的结果落区域：有可重置的区域则按中心改尺寸，否则在鼠标处新建。"""
        bounds = self.mapper.full_physical_local_rect()
        width = max(1, min(int(width), bounds.width()))
        height = max(1, min(int(height), bounds.height()))
        target = self._fixed_size_target()
        if target is not None:
            center = target.center()
            rect = QRect(center.x() - width // 2, center.y() - height // 2, width, height)
            rect = rect.intersected(bounds)
            target.setRect(rect.x(), rect.y(), rect.width(), rect.height())
            self.session.fixed_size_rect = target
            logging.getLogger("screensnap").debug(
                "固定尺寸：按中心重置已有区域为 %dx%d", width, height)
        else:
            left = max(bounds.left(), min(self.position.x(), bounds.right() - width + 1))
            top = max(bounds.top(), min(self.position.y(), bounds.bottom() - height + 1))
            rect = QRect(left, top, width, height)
            self.selection.rects.append(rect)
            self.session.fixed_size_rect = rect
            logging.getLogger("screensnap").debug(
                "固定尺寸：在鼠标处新建 %dx%d 区域（保留已有 %d 个区域）",
                width, height, len(self.selection.rects) - 1)
        self.selection.active = None
        self.element_index = -1
        self.update_all()
        if self.inline_active():
            # 方案 A：在原地编辑里按 F 改完尺寸后，让原地编辑器跟随新区域
            # （由内联编辑器自己的边框负责调整；遮罩手柄在内联状态下被它盖住）。
            self.update_inline_region()
            logging.getLogger("screensnap").debug(
                "固定尺寸：原地编辑器跟随新区域 %s", rect.getRect())

    def select_fixed_size(self):
        """在鼠标附近创建或重置指定尺寸的选区。"""
        if self._delegate_to_owner("select_fixed_size"):
            return
        bounds = self.mapper.full_physical_local_rect()
        target = self._fixed_size_target()
        dialog = QDialog(self)
        dialog.setWindowTitle("固定尺寸选区")
        # 遮罩是 Tool + WindowStaysOnTopHint 的置顶全屏窗：普通对话框会被压在它下面，
        # 且默认出现在父窗口所在屏。这里置顶并定位到鼠标所在屏幕的鼠标位置。
        dialog.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        layout = QFormLayout(dialog)
        width_input = QSpinBox(dialog)
        width_input.setRange(1, max(1, bounds.width()))
        width_input.setValue(target.width() if target is not None else max(1, bounds.width()))
        height_input = QSpinBox(dialog)
        height_input.setRange(1, max(1, bounds.height()))
        height_input.setValue(target.height() if target is not None else max(1, bounds.height()))
        layout.addRow("宽度", width_input)
        layout.addRow("高度", height_input)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel,
                                   parent=dialog)
        # Qt 内置翻译未加载，OK/Cancel 默认是英文；项目其它对话框也统一用中文按钮。
        buttons.button(QDialogButtonBox.Ok).setText("确定")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addRow(buttons)
        dialog.adjustSize()
        anchor = QCursor.pos()
        screen = QGuiApplication.screenAt(anchor) or QGuiApplication.primaryScreen()
        if screen is not None:
            area = screen.availableGeometry()
            frame = dialog.frameGeometry()
            frame.moveCenter(anchor)
            dialog.move(min(max(frame.left(), area.left()), area.right() - frame.width() + 1),
                        min(max(frame.top(), area.top()), area.bottom() - frame.height() + 1))
        dialog.raise_()
        dialog.activateWindow()
        if dialog.exec() != QDialog.Accepted:
            return
        self.apply_fixed_size(width_input.value(), height_input.value())

