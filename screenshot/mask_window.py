"""全屏统一遮罩与多选区事件分发。"""

import logging
import os
import re
import time
from datetime import datetime

from PySide6.QtCore import Qt, Signal, QPoint, QPointF, QRect, QEvent, QMimeData, QTimer, QSize
from PySide6.QtGui import (QColor, QCursor, QPainter, QPainterPath, QPen, QGuiApplication,
                           QMouseEvent, QShortcut, QKeySequence)
from PySide6.QtWidgets import (QWidget, QApplication, QDialog, QDialogButtonBox, QFormLayout, QSpinBox,
                               QFrame, QGraphicsView, QMenu, QToolButton, QLabel)

from core.dpi import DisplayMapper
from core.image_io import save_image, saved_extension
from core.path_utils import resolved_dir
from core.screen_capture import to_qimage
from core.window_boundaries import visible_windows
from core.window_elements import element_chain
from core.window_focus import activate_window
from logger.log_rate import log_every
from editor.annotation_canvas import AnnotationCanvas
from editor.image_effects import apply_output_effects
from editor.toolbar_widget import ToolbarWidget, rich_tooltip
from screenshot.selection_rect import SelectionRects
from screenshot.overlay_info import info_bar_layout, paint_info_bar, paint_info_badge
from screenshot.magnifier_widget import (MAGNIFIER_DEFAULT_SIZE, magnifier_rect,
                                         paint_magnifier)
from screenshot.hint_items import CAPTURE_ACTION_KEYS, TOOLBAR_HIDE_KEY, hint_items, hint_texts

# 鼠标移动时悬停识别的刷新间隔由设置“window_hover_interval”控制（毫秒），避免每个移动事件都调用系统 API。

# 没抢到焦点时的全局 Esc 兜底需要系统键盘钩子；自动化测试会置为 False，避免吃掉真实按键。
ESCAPE_FALLBACK_ENABLED = os.name == "nt"

# 原地编辑两排图标条的紧凑尺寸：按钮边长与图标边长，尽量减少对截图区域的遮挡。
INLINE_BUTTON_SIZE = 24
INLINE_ICON_SIZE = 16

# 拖动工具栏后松手时，与候选位置的距离（曼哈顿，像素）在此以内就吸附过去。
# 拖动工具栏后松手时，与候选位置的距离（曼哈顿，像素）在此以内就吸附过去。
TOOLBAR_SNAP_DISTANCE = 24
# 工具栏左端抓取提示的宽度（像素）：拖动整条工具栏的入口，兼作视觉提示。
TOOLBAR_HANDLE_WIDTH = 10

# 采集自检：会在屏幕上长期存在、可能被一起采进画面的自身窗口（类名 → 提示用中文名）。
# 新增这类常驻窗口时请一并登记；遮罩自身、放大镜 HUD 与内部控件不在其中（只画在遮罩表面）。
INTRUDING_WINDOW_LABELS = {
    "CaptureNotification": "通知缩略图",
    "EditorWindow": "编辑器",
    "SettingsWindow": "设置窗口",
    "StickerItem": "贴图",
    "StickerPanel": "贴图管理",
    "RecycleWindow": "贴图回收站",
}


def window_label(widget):
    """干扰窗口在提示条里的中文名：窗口可自报（`screensnap_self_window`），否则查登记表。

    自报用于类名不在登记表里的本程序浮层（例如工具栏的「外观」弹层是 `QFrame`），
    否则这类窗口会被采进画面却连一句警示都没有。
    """
    return (widget.property("screensnap_self_window")
            or INTRUDING_WINDOW_LABELS.get(type(widget).__name__, "本程序窗口"))


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
    opacity = settings.get("mask_opacity", 50)
    color.setAlpha(round(opacity * 255 / 100))
    return color


class MaskSession:
    """一轮截图中的共享状态；每个显示器一个 MaskWindow 共同使用。"""

    def __init__(self, mapper):
        self.mapper = mapper
        cursor_global = mapper.logical_global_to_physical_global(QCursor.pos())
        self.position = cursor_global - mapper.physical_bounds.topLeft()
        self.selection = SelectionRects()
        self.element_selected = False
        self.views = []
        self.completing = False
        self.closing = False
        self.inline_editor = None


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
        self.warning = None
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
        metrics = self.fontMetrics()
        warning = view.self_check_warning()
        # 传入光标（逻辑坐标）：提示条据此与放大镜一起翻转、并与放大镜的边缘对齐。
        bar, rows, align_right = info_bar_layout(
            metrics, view.rect(), view.magnifier_frame(), view.capture_hint_items(),
            warning=warning, per_line=view.hint_per_line(),
            cursor=view.to_logical_point(view.position))
        if bar.isEmpty():
            self.bar = QRect()
            self.hide()
            return
        self.bar, self.rows = bar, rows
        self.align_right = align_right
        self.warning = warning
        self.setGeometry(bar)
        if not self.isVisible():
            self.show()
        # 画布与工具栏是遮罩的子控件：每次都重新抬到最上层，保证提示条不被它们盖住。
        self.raise_()
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        paint_info_bar(painter, self.rect(), self.rows, self.warning, self.fontMetrics(),
                       align_right=self.align_right)


class InlineEditor(QWidget):
    """贴在截图选区上的轻量编辑器；第一版只支持单屏单选区。"""

    saved = Signal(str, object)
    saved_silently = Signal(str, object)
    save_failed = Signal(str)
    close_all_requested = Signal()
    sticker_requested = Signal(object, object)

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
        self.initial_save_timer = QTimer(self)
        self.initial_save_timer.setSingleShot(True)
        self.initial_save_timer.timeout.connect(self.save_initial_region)
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
        if (event_type == QEvent.MouseMove and
                (watched is self.toolbar or self.toolbar.isAncestorOf(watched)) and
                view.selection.resizing is None and view.selection.dragging is None):
            return super().eventFilter(watched, event)
        local = watched.mapTo(view, event.position().toPoint())
        mapped = QMouseEvent(event_type, QPointF(local), QPointF(view.mapToGlobal(local)),
                             event.button(), event.buttons(), event.modifiers())
        point = view.to_physical_point(local)
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
        if not self.last_path:
            self.initial_save_timer.start(0)

    def save_initial_region(self):
        try:
            self.save(automatic=True)
        except OSError as error:
            self.save_failed.emit(str(error))

    def begin_region_resize(self):
        """调整选区大小时先隐藏旧编辑层，避免遮挡正在查看的新区域。"""
        self.resizing_region = True
        self.canvas.hide()
        self.toolbar.hide()

    def end_region_resize(self):
        self.resizing_region = False

    def allocate_path(self, automatic=False):
        directory = resolved_dir(self.settings, "auto_dir" if automatic else "manual_dir")
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

    def save(self, automatic=False, copy_to_clipboard=False, force_copy_image=False):
        path = self.last_path or self.allocate_path(automatic)
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
        if key == "editor_image_round_corners":
            self.round_corners = value
        elif key == "editor_image_corner_radius":
            self.corner_radius = value
        if key in ("editor_image_round_corners", "editor_image_corner_radius"):
            self.canvas.set_round_corner_preview(self.round_corners, self.corner_radius)
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
            self.initial_save_timer.stop()
            logging.getLogger("screensnap").debug(
                "原地编辑请求创建贴图: %dx%d", image.width(), image.height())
            position = self.view.mapper.physical_local_rect_to_logical_global_rect(
                self.rect).toRect().topLeft()
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
            self.initial_save_timer.stop()
            self.save(copy_to_clipboard=True)
            self.view.close()
        elif action == "copy":
            self.initial_save_timer.stop()
            self.save(copy_to_clipboard=True, force_copy_image=True)
        elif action == "path":
            QGuiApplication.clipboard().setText(str(self.last_path))
        elif action == "discard":
            self.discard()
        elif action == "close_all_editors":
            self.close_all_requested.emit()
            self.view.close()

    def discard(self):
        self.initial_save_timer.stop()
        self.view.close()

    def cleanup(self):
        """释放原地编辑中持有的大图和工具控件引用。"""
        if getattr(self, "initial_save_timer", None) is not None:
            self.initial_save_timer.stop()
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
    picker_copied = Signal(str)
    close_all_requested = Signal()
    sticker_requested = Signal(object, object)
    annotation_setting_changed = Signal(str, object)
    pen_color_changed = Signal(str)
    tool_color_changed = Signal(str, str)
    cancel_requested = Signal()

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
            QCursor.setPos(self.mapper.physical_global_to_logical_global(cursor_target))
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
        self.capture_cursor_enabled = settings["cursor"]
        self.selection = self.session.selection
        self.picker_mode = False
        self.picker_color = None
        self.position = QPoint(self.session.position)
        self.resize_cursor = "nwse"
        # 鼠标悬停识别出的元素矩形（bounds 局部物理坐标）与上次检测时刻。
        self.hover_rect = None
        self.hover_stamp = 0.0
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
            point = self.mapper.logical_global_to_physical_global(self.cursor().pos())
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
        # 选区阶段的四个功能快捷键取代了原先的按钮，只在主遮罩上创建（应用级，遮罩活动即生效）。
        self.capture_action_shortcuts = None
        self.toolbar_hide_shortcut = None
        if primary:
            slots = {"custom_size": self.select_fixed_size,
                     "recapture": self.request_recapture,
                     "window_edit": self.complete_in_window_editor,
                     "copy": self.copy_selection_to_clipboard}
            self.capture_action_shortcuts = {
                name: self._capture_action_shortcut(settings, key, default, slots[name])
                for name, key, default, _label in CAPTURE_ACTION_KEYS}
            # 原地编辑工具栏的隐藏键：同一个机制，只加键位、不加按钮。
            self.toolbar_hide_shortcut = self._capture_action_shortcut(
                settings, *TOOLBAR_HIDE_KEY, self.toggle_inline_toolbar)
        if primary and settings.get("window_detection", True):
            point = self.mapper.logical_global_to_physical_global(QCursor.pos())
            self.element_chain = element_chain((point.x(), point.y()),
                                               settings.get("element_depth", 3),
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


    def _capture_action_shortcut(self, settings, key, default, slot):
        """选区阶段的功能快捷键（自定义尺寸 / 重新截图 / 窗口编辑 / 仅复制）。

        默认：自定义尺寸 `F`、重新截图 `R`、窗口编辑 `E`、仅复制 `Y`（都用单键，
        替换掉此前的 `Ctrl+F`）；都可在「设置 > 截图 > 截图快捷操作」里改键。
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
            toolbar_hidden=bool(editor is not None and editor.toolbar_hidden))

    def hint_texts(self, selection, inline):
        """提示项 id → 文案（供用例复用；实际显示走 `capture_hint_items`）。"""
        editor = self.session.inline_editor
        return hint_texts(
            self.settings, (self.position.x(), self.position.y()),
            (selection.width(), selection.height()) if selection else None,
            inline=inline, picker=self.picker_mode, picker_color=self.picker_color,
            toolbar_hidden=bool(editor is not None and editor.toolbar_hidden))

    def capture_action_hints(self):
        """兼容旧调用：返回提示项里四个选区功能键的 (键, 名称) 列表（供用例复用）。"""
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
        self._sync_escape_fallback()

    def _focus_capture(self):
        """显示完成后重试一次焦点交接，避免全局热键启动时窗口尚未激活。"""
        if self.primary and not self.session.closing and self.isVisible():
            activate_window(self)
            self.setFocus(Qt.ActiveWindowFocusReason)
            self._sync_escape_fallback()

    def _install_escape_fallback(self):
        """注册全局 Esc 热键；由全局钩子线程回调，只发信号不直接关窗口。"""
        try:
            import keyboard

            return keyboard.add_hotkey("esc", self._global_escape, suppress=True)
        except (ImportError, ValueError, OSError, RuntimeError) as error:
            logging.getLogger("screensnap").debug("注册 Esc 兜底热键失败: %s", error)
            return None

    def _release_escape_fallback(self):
        if self.escape_fallback is None:
            return
        handle, self.escape_fallback = self.escape_fallback, None
        try:
            import keyboard

            keyboard.remove_hotkey(handle)
        except (ImportError, ValueError, OSError, RuntimeError) as error:
            logging.getLogger("screensnap").debug("移除 Esc 兜底热键失败: %s", error)

    def _global_escape(self):
        """全局钩子线程里只发信号，关闭动作交回 Qt 主线程执行。"""
        self.cancel_requested.emit()

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
        # 截图结束：清掉悬停高亮，避免下一轮截图残留上一次的元素框。
        self.hover_rect = None
        self.hover_stamp = 0.0
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

    def update_all(self):
        # 选区微调（键位包含 S）与快捷保存快捷键 S 冲突：选区处于可微调状态时不响应保存，
        # 交由 keyPressEvent 处理方向微调；无活动选区时才允许 S 直接保存。
        if self.capture_save_shortcut is not None:
            self.capture_save_shortcut.setEnabled(self.selection.nudge_index is None)
        self.session.position = QPoint(self.position)
        for view in self.session.views:
            view.position = QPoint(self.session.position)
            view.update()
            view.magnifier_overlay.sync()
            # 提示条是子控件：位置与内容都跟着光标变，每帧同步一次并抬到画布之上。
            view.info_bar.sync()

    def request_recapture(self):
        context = {"monitor": dict(self.monitor)}
        self.recapture_requested.emit(context)
        self.close()

    def copy_selection_to_clipboard(self):
        """把整屏截图（或当前选区）写入剪贴板并关闭遮罩，不落盘、不进编辑器。"""
        logger = logging.getLogger("screensnap")
        if self.selection.rects:
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
        self.complete(force_window=True)

    def sync_quick_sticker_shortcut(self, settings):
        self.quick_sticker_enabled = bool(settings.get("capture_quick_sticker_enabled", False))
        if self.quick_sticker_shortcut is None:
            return
        sequence = QKeySequence(settings.get("capture_quick_sticker_shortcut", "Space"))
        if sequence.isEmpty():
            sequence = QKeySequence("Space")
        self.quick_sticker_shortcut.setKey(sequence)
        self.quick_sticker_shortcut.setEnabled(self.quick_sticker_enabled)

    def sync_capture_save_shortcut(self, settings):
        if self.capture_save_shortcut is None:
            return
        sequence = QKeySequence(settings.get("capture_save_shortcut", "S"))
        if sequence.isEmpty():
            sequence = QKeySequence("S")
        self.capture_save_shortcut.setKey(sequence)

    def sync_capture_action_shortcuts(self, settings):
        """改键后同步四个选区阶段功能键与原工具栏隐藏键（提示条读的是同一份设置）。"""
        if self.toolbar_hide_shortcut is not None:
            key, default = TOOLBAR_HIDE_KEY
            sequence = QKeySequence(settings.get(key, default))
            if sequence.isEmpty():
                sequence = QKeySequence(default)
            self.toolbar_hide_shortcut.setKey(sequence)
        if not self.capture_action_shortcuts:
            return
        for name, key, default, _label in CAPTURE_ACTION_KEYS:
            shortcut = self.capture_action_shortcuts.get(name)
            if shortcut is None:
                continue
            sequence = QKeySequence(settings.get(key, default))
            if sequence.isEmpty():
                sequence = QKeySequence(default)
            shortcut.setKey(sequence)

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
                type(widget).__name__ in INTRUDING_WINDOW_LABELS
            if not own and not isinstance(widget, (QMenu, QDialog)):
                continue
            rect = widget.frameGeometry()
            if rect.width() > 0 and rect.height() > 0 and area.intersects(rect):
                hits.append(widget)
        return hits

    def self_check_warning(self, selection=None):
        """采集自检：选区含本程序内容时返回提示文案，否则返回 None。

        两类来源合并成一句：选区内的常驻自身窗口，以及抓屏瞬间记录下的弹出菜单
        （它已被烤进冻结帧，此刻枚举不到）。系统通知不作提示（对用户是噪音）。
        """
        hits = self.intruding_windows(selection)
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
        if area is not None:
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

    def complete(self, force_window=False, save_direct=False):
        """完成当前选区，进入原地编辑或交给独立编辑器。"""
        if self.session.completing or self.session.closing:
            return
        self.session.completing = True
        self.selection.finish()

        bounds = self.mapper.full_physical_local_rect()
        regions = [rect.normalized().intersected(bounds)
                   for rect in self.selection.rects]
        regions = [rect for rect in regions if not rect.isEmpty()]
        if not regions:
            self.session.completing = False
            self.close()
            return

        self.selection.rects[:] = regions
        self.selection.active = None
        rect = regions[-1]
        self.last_region.emit([rect.x() + self.bounds["left"],
                               rect.y() + self.bounds["top"],
                               rect.width(), rect.height()])

        edit_after_capture = self.settings.get("capture_after_selection", "save") == "edit"
        if (edit_after_capture and not save_direct and not force_window
            and self.settings.get("inline_edit", False) and len(regions) == 1):
            view = self.inline_view_for_rect(rect)
            if view is not None:
                area = (rect.x(), rect.y(), rect.right() + 1, rect.bottom() + 1)
                image = self.image.crop(area)
                alternate = self.alternate.crop(area) if self.alternate else None
                editor = InlineEditor(view, rect, image, alternate)
                editor.saved.connect(self.image_saved)
                editor.saved_silently.connect(self.image_saved_silently)
                editor.save_failed.connect(self.save_failed)
                editor.close_all_requested.connect(self.close_all_requested)
                editor.sticker_requested.connect(self.sticker_requested)
                self.session.inline_editor = editor
                editor.reset_region(rect, image, alternate,
                                    self.capture_cursor_enabled)
                self.session.completing = False
                self.update_all()
                editor.canvas.setFocus(Qt.ActiveWindowFocusReason)
                return

        images = []
        for region in regions:
            area = (region.x(), region.y(), region.right() + 1, region.bottom() + 1)
            image = self.image.crop(area)
            alternate = self.alternate.crop(area) if self.alternate else None
            images.append((image, alternate))
        positions = [self.mapper.physical_local_rect_to_logical_global_rect(region).toRect().topLeft()
                     for region in regions]
        if self.settings.get("capture_after_selection") == "copy":
            from PySide6.QtGui import QGuiApplication
            from PySide6.QtCore import QMimeData
            payload = QMimeData()
            payload.setImageData(to_qimage(images[0][0]))
            QGuiApplication.clipboard().setMimeData(payload)
            self.copy_done.emit(images[0][0])
            self.close()
            return
        if save_direct:
            self.save_requested.emit(images, positions)
        elif force_window:
            self.edit_requested.emit(images, positions)
        else:
            self.selected.emit(images, positions)
        self.close()

    def save_selection(self):
        """用当前输出规则保存选区，不进入标注编辑器。"""
        if not self.inline_active() and self.selection.rects:
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

    def paintEvent(self, event):
        """遮罩、锚点和 HUD 仅绘制在窗口表面，不写入原始截图。"""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.drawImage(self.to_logical_rect(self.monitor_rect), self.preview, self.monitor_rect)
        painter.fillRect(self.rect(), _mask_overlay_color(self.settings))
        hover_clipped = None
        if (self.hover_rect is not None and self.selection.active is None and
                self.settings.get("window_hover_fill_mode", "reveal") == "reveal"):
            hover_clipped = self.hover_rect.intersected(self.monitor_rect)
            if not hover_clipped.isEmpty():
                painter.drawImage(self.to_logical_rect(hover_clipped),
                                  self.preview, hover_clipped)
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
            painter.drawImage(logical_rect, self.preview, clipped)
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
        if self.picker_mode and event.button() == Qt.LeftButton:
            # 取色用 Alt/Ctrl + 左键取样，避免与左键框选 / UIA 自动识别选择冲突。
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
            # 不带 Alt/Ctrl 的左键在取色模式下不取样、也不触发选区/识别，直接忽略。
            return
        if event.button() == Qt.LeftButton:
            self.position = self.to_physical_point(event.position().toPoint())
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
            self.selection.begin(self.position)

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
            self.poll_hover()
        else:
            self.hover_rect = None
        self.update_all()

    def mouseReleaseEvent(self, event):
        """结束当前选区的创建、拖动或缩放。"""
        if self.inline_active():
            if event.button() == Qt.LeftButton and (self.selection.resizing is not None or
                                                    self.selection.dragging is not None):
                self.position = self.to_physical_point(event.position().toPoint())
                self.selection.finish()
                self.update_inline_region()
                if self.session.inline_editor is not None:
                    self.session.inline_editor.end_region_resize()
            return
        if event.button() == Qt.LeftButton:
            self.position = self.to_physical_point(event.position().toPoint())
            self.selection.finish()
            # 几乎没移动的单击视为确认，直接选中当前高亮的窗口或控件。
            if (self.press_position is not None
                    and (self.position - self.press_position).manhattanLength() <= 3):
                self.select_hover()
            self.press_position = None
            self.update_all()

    def mouseDoubleClickEvent(self, event):
        """双击提交所有有效选区，没有选区时仅关闭遮罩。"""
        if self.inline_active():
            return
        if event.button() == Qt.LeftButton:
            self.complete()
        elif event.button() == Qt.RightButton:
            self.complete(save_direct=True)

    def keyPressEvent(self, event):
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
                dy = (-1 if key in (Qt.Key_W, Qt.Key_Up) else 1 if key in (Qt.Key_S, Qt.Key_Down) else 0)
                if (dx or dy) and self.selection.rects:
                    if self.selection.handle_at(self.position) is None:
                        self.selection.nudge_corner = None
                    if self.selection.resizing is not None or self.selection.dragging is not None:
                        self.releaseMouse()
                    self.selection.move_last(dx, dy)
                    self.position = (QPoint(self.selection.nudge_corner[2]) if self.selection.nudge_corner
                                     else self.position + QPoint(dx, dy))
                    self.update_inline_region()
                    view = self.focus_view_for_position(self.position)
                    QCursor.setPos(view.mapToGlobal(view.to_logical_point(self.position)))
            return
        # 取色快捷键跟随设置（默认 C），仅单字母 A-Z 生效，否则回退到 C。
        picker_key = (self.settings.get("capture_picker_shortcut", "C") or "C")
        picker_qt_key = Qt.Key_C
        if len(picker_key) == 1 and "A" <= picker_key.upper() <= "Z":
            picker_qt_key = Qt.Key_A + (ord(picker_key.upper()) - ord("A"))
        if key == picker_qt_key and not self.inline_active():
            self.picker_mode = not self.picker_mode
            self.picker_color = None
            self.update_all()
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
            dy = (-1 if key in (Qt.Key_W, Qt.Key_Up) else 1 if key in (Qt.Key_S, Qt.Key_Down) else 0)
            if dx or dy:
                if self.selection.rects and self.selection.handle_at(self.position) is None:
                    self.selection.nudge_corner = None
                self.selection.move_last(dx, dy)
                if self.selection.rects:
                    self.position = (QPoint(self.selection.nudge_corner[2]) if self.selection.nudge_corner
                                     else self.position + QPoint(dx, dy))
                    view = self.focus_view_for_position(self.position)
                    QCursor.setPos(view.mapToGlobal(view.to_logical_point(self.position)))
        self.update_all()


    def trigger_quick_sticker(self):
        if (self.quick_sticker_consumed or not self.quick_sticker_enabled or
                self.inline_active() or not self.selection.rects):
            return
        self.quick_sticker_consumed = True
        region = self.selection.rects[-1].normalized()
        position = self.mapper.physical_local_rect_to_logical_global_rect(
            region).toRect().topLeft()
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
        point = self.mapper.logical_global_to_physical_global(QCursor.pos())
        self.hide()
        try:
            self.element_chain = element_chain((point.x(), point.y()),
                                               self.settings.get("element_depth", 3),
                                               use_uia=self.uia_enabled(),
                                               exclude_hwnd=int(self.winId()),
                                               debug_tree=bool(self.settings.get("uia_debug_tree", False)))
        finally:
            self.show()
        logging.getLogger("screensnap").debug(
            "窗口元素重查: 鼠标物理点=(%d,%d) UIA=%s 层级上限=%s 候选=%d 层 %s",
            point.x(), point.y(), self.uia_enabled(),
            self.settings.get("element_depth", 3),
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

    def detect_hover(self):
        """识别鼠标下的元素用于高亮；不隐藏遮罩，避免移动时闪烁。

        每个显示器的遮罩各自检测：鼠标停在哪个屏，就由那个屏的窗口收事件并
        高亮。不能只让 primary 检测，否则鼠标在其它显示器上时完全没有提示。
        """
        # 已经画出选区后不再提示元素：这时用户在调整或确认自己的选区，
        # 继续高亮只会干扰（也是“截图完了还在识别”的来源）。
        if not self.hover_detection_enabled() or self.selection.rects:
            self.hover_rect = None
            self.hover_source_rect = None
            return None
        point = self.mapper.logical_global_to_physical_global(QCursor.pos())
        chain = element_chain((point.x(), point.y()), self.settings.get("element_depth", 3),
                              use_uia=self.uia_enabled(),
                              exclude_hwnd=int(self.winId()),
                              debug_tree=bool(self.settings.get("uia_debug_tree", False)))
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

    def poll_hover(self):
        """按时间节流刷新悬停高亮，鼠标移动事件非常密集。"""
        # 遮罩已经不可见（截图结束）时不再查询系统窗口。
        if not self.isVisible():
            self.hover_rect = None
            return
        point = self.mapper.logical_global_to_physical_global(QCursor.pos())
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
        # 鼠标移出高亮区域时立即取消，不等下一次轮询，避免高亮滞后。
        if self.hover_rect is not None and not self.hover_rect.contains(self.position):
            self.hover_rect = None
            self.hover_stamp = 0.0
        # 悬停高亮按时间节流刷新（间隔由“悬停识别刷新间隔”设置控制，毫秒）：
        # 数值越小越跟手（灵敏度越高），但调用系统识别接口更频繁；开启 UIA 时无障碍查询
        # 更慢，间隔自动翻倍以兼容。仍卡在 SLOW_SECONDS(0.4s) 熔断阈值内，不会因变快而误关 UIA。
        interval = (self.settings.get("window_hover_interval", 80) / 1000.0) * (2 if self.uia_enabled() else 1)
        stamp = time.monotonic()
        if stamp - self.hover_stamp < interval:
            return
        self.hover_stamp = stamp
        rect = self.detect_hover()
        logger = logging.getLogger("screensnap")
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
        self.selection.rects.clear()
        self.selection.rects.append(QRect(rect))
        self.selection.active = None
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

    def select_fixed_size(self):
        """在鼠标附近创建指定尺寸的选区。"""
        bounds = self.mapper.full_physical_local_rect()
        dialog = QDialog(self)
        dialog.setWindowTitle("固定尺寸选区")
        layout = QFormLayout(dialog)
        width_input = QSpinBox(dialog)
        width_input.setRange(1, max(1, bounds.width()))
        width_input.setValue(max(1, bounds.width()))
        height_input = QSpinBox(dialog)
        height_input.setRange(1, max(1, bounds.height()))
        height_input.setValue(max(1, bounds.height()))
        layout.addRow("宽度", width_input)
        layout.addRow("高度", height_input)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel,
                                   parent=dialog)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addRow(buttons)
        if dialog.exec() != QDialog.Accepted:
            return

        width, height = width_input.value(), height_input.value()
        left = max(bounds.left(), min(self.position.x(), bounds.right() - width + 1))
        top = max(bounds.top(), min(self.position.y(), bounds.bottom() - height + 1))
        self.selection.rects.clear()
        self.selection.rects.append(QRect(left, top, width, height))
        self.selection.active = None
        self.element_index = -1
        self.update_all()

