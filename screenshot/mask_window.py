"""全屏统一遮罩与多选区事件分发。"""

import ctypes
import logging
import os
import re
import time
from datetime import datetime

from PySide6.QtCore import Qt, Signal, QPoint, QPointF, QRect, QEvent, QMimeData, QTimer, QSize
from PySide6.QtGui import (QColor, QCursor, QPainter, QPainterPath, QPen, QGuiApplication,
                           QMouseEvent, QShortcut, QKeySequence)
from PySide6.QtWidgets import (QWidget, QApplication, QDialog, QDialogButtonBox, QFormLayout, QSpinBox, QStyle,
                               QFrame, QGraphicsView, QToolButton, QLabel,
                               QHBoxLayout, QPushButton)

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
from ui.action_icons import action_icon
from screenshot.selection_rect import SelectionRects
from screenshot.overlay_info import paint_info
from screenshot.magnifier_widget import magnifier_rect, paint_magnifier

# 鼠标移动时悬停识别的刷新间隔由设置“window_hover_interval”控制（毫秒），避免每个移动事件都调用系统 API。

# 没抢到焦点时的全局 Esc 兜底需要系统键盘钩子；自动化测试会置为 False，避免吃掉真实按键。
ESCAPE_FALLBACK_ENABLED = os.name == "nt"

# 原地编辑两排图标条的紧凑尺寸：按钮边长与图标边长，尽量减少对截图区域的遮挡。
INLINE_BUTTON_SIZE = 24
INLINE_ICON_SIZE = 16


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
        frame = magnifier_rect(view.to_logical_point(view.position), view.rect())
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
        frame = magnifier_rect(view.to_logical_point(view.position), view.rect())
        painter.translate(-frame.x(), -frame.y())
        paint_magnifier(painter, view.preview, view.to_logical_point(view.position),
                        view.rect(), view.position,
                        grid=view.settings.get("magnifier_grid", False),
                        grid_color=view.settings.get("magnifier_grid_color", "#cccccc"))


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
        self.initial_save_timer = QTimer(self)
        self.initial_save_timer.setSingleShot(True)
        self.initial_save_timer.timeout.connect(self.save_initial_region)
        self.toolbar = ToolbarWidget(self.settings["pen_color"], self.settings,
                         show_capture_actions=True)
        self.toolbar.setObjectName("inlineCaptureToolbar")
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
        self.canvas.setting_changed.connect(self.apply_canvas_setting)
        options_menu = self.toolbar.options_button.menu()
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
        left_x = selection.left()
        right_x = selection.right() - toolbar_size.width() + 1
        first_x, second_x = (right_x, left_x) if selection.center().x() > self.view.width() // 2 else (left_x, right_x)
        candidates = (
            QPoint(first_x, selection_with_handles.bottom() + margin),
            QPoint(second_x, selection_with_handles.bottom() + margin),
            QPoint(first_x, selection_with_handles.top() - toolbar_size.height() - margin),
            QPoint(second_x, selection_with_handles.top() - toolbar_size.height() - margin),
            QPoint(selection_with_handles.right() + margin, selection.top()),
            QPoint(selection_with_handles.left() - toolbar_size.width() - margin, selection.top()),
        )
        target = None
        for candidate in candidates:
            x = min(max(candidate.x(), margin), max(margin, self.view.width() - toolbar_size.width() - margin))
            y = min(max(candidate.y(), margin), max(margin, self.view.height() - toolbar_size.height() - margin))
            candidate = QPoint(x, y)
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
        logging.getLogger("screensnap").debug(
            "原地编辑工具栏布局：选区 %sx%s，工具栏 %sx%s，位置 (%s,%s)%s",
            selection.width(), selection.height(), toolbar_size.width(), toolbar_size.height(),
            x, y, "" if target is not None else "，空间不足回退到选区内部")

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
        self.toolbar.show()
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
            if key == f"{self.canvas.tool}_color":
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
                 preferred_monitor=None):
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
        if primary:
            for extra_monitor in monitors[1:]:
                MaskWindow(image, bounds, monitors, settings, mode, alternate,
                           self.session, extra_monitor, primary=False)
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
        self.capture_actions = self.create_capture_actions()
        self.update_capture_actions()
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


    def create_capture_actions(self):
        actions = QWidget(self)
        actions.setObjectName("captureSelectionActions")
        action_layout = QHBoxLayout(actions)
        action_layout.setContentsMargins(0, 0, 0, 0)
        action_layout.setSpacing(8)
        owner = self.session.views[0]
        custom_size = QPushButton("自定义尺寸", actions)
        custom_size.setIcon(action_icon("resize"))
        custom_size.clicked.connect(owner.select_fixed_size)
        recapture = QPushButton("重新截图", actions)
        recapture.setIcon(action_icon("camera"))
        recapture.clicked.connect(self.request_recapture)
        window_edit = QPushButton("窗口编辑", actions)
        window_edit.setIcon(action_icon("window_edit"))
        window_edit.clicked.connect(owner.complete_in_window_editor)
        copy_only = QPushButton("仅复制", actions)
        copy_only.setIcon(action_icon("clipboard_image"))
        copy_only.clicked.connect(self.copy_selection_to_clipboard)
        action_layout.addWidget(custom_size)
        action_layout.addWidget(recapture)
        action_layout.addWidget(window_edit)
        action_layout.addWidget(copy_only)
        for button in (custom_size, recapture, window_edit, copy_only):
            button.setFixedHeight(32)
            button.setIconSize(QSize(14, 14))
            # 截图遮罩是键盘驱动的取景层，操作按钮只用鼠标点击，不应抢占 Tab 焦点，
            # 否则 Tab 会被焦点遍历抢走，无法在窗口元素层级间循环。
            button.setFocusPolicy(Qt.NoFocus)
        custom_size.setToolTip(rich_tooltip(
            "自定义尺寸",
            "按指定宽高创建选区：宽高默认填整屏像素，可改成任意尺寸（如 1920 × 1080），"
            "选区左上角对齐当前鼠标位置；截图时按 Ctrl+F 也能打开。"))
        recapture.setToolTip(rich_tooltip(
            "重新截图",
            "放弃当前这一屏已冻结的画面，回到同一显示器重新框选；"
            "适合画面还没准备好或想换区域的情况，当前标注不会保留。"))
        window_edit.setToolTip(rich_tooltip(
            "窗口编辑",
            "把当前选区送进独立编辑器窗口，使用完整工具栏编辑；"
            "适合标注较多，或需要缩放、旋转、裁剪、调外观的场景。"))
        copy_only.setToolTip(rich_tooltip(
            "仅复制",
            "把当前整屏截图（有选区时取选区）直接写入剪贴板并关闭遮罩，不落盘、不进入编辑器；"
            "右键双击与快速保存快捷键仍直接保存。"))
        self.capture_action_buttons = (custom_size, recapture, window_edit, copy_only)
        actions.adjustSize()
        return actions

    def logical_window_offset(self):
        """当前窗口左上角相对整轮遮罩 logical_bounds 的偏移。"""
        return self.mapper.physical_local_rect_to_logical_global_rect(
            self.monitor_rect).toRect().topLeft() - self.mapper.logical_bounds.topLeft()

    def show(self):
        if self.primary:
            for view in self.session.views:
                QWidget.show(view)
                view.magnifier_overlay.sync()
                view.update_capture_actions()
            activate_window(self)
            self.setFocus(Qt.ActiveWindowFocusReason)
            QTimer.singleShot(0, self._focus_capture)
        else:
            QWidget.show(self)
            self.magnifier_overlay.sync()
            self.update_capture_actions()
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
            view.update_capture_actions()
            view.position = QPoint(self.session.position)
            view.update()
            view.magnifier_overlay.sync()

    def update_capture_actions(self):
        if self.capture_actions is None:
            return
        visible = bool(self.selection.rects) and not self.inline_active()
        self.capture_actions.setVisible(visible)
        if visible:
            labels = ("自定义尺寸", "重新截图", "窗口编辑", "仅复制")
            compact_labels = ("尺寸", "重截", "编辑", "复制")
            for button, label in zip(self.capture_action_buttons, labels):
                button.setText(label)
            self.capture_actions.adjustSize()
            if self.width() < self.capture_actions.width() + 16:
                for button, label in zip(self.capture_action_buttons, compact_labels):
                    button.setText(label)
                self.capture_actions.adjustSize()
            if self.width() < self.capture_actions.width() + 16:
                for button in self.capture_action_buttons:
                    button.setText("")
                self.capture_actions.adjustSize()
            x = max(8, (self.width() - self.capture_actions.width()) // 2)
            y = 8 + self.fontMetrics().height() + 12 + 6
            y = min(y, max(8, self.height() - self.capture_actions.height() - 8))
            self.capture_actions.move(x, y)

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

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.update_capture_actions()
        self.magnifier_overlay.sync()

    def inline_active(self):
        return self.session.inline_editor is not None

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
        if self.capture_actions is not None:
            self.capture_actions.hide()
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
        global_position = self.position + QPoint(self.bounds["left"], self.bounds["top"])
        current_selection = self.selection.active or (
            self.selection.rects[-1] if self.selection.rects else None
        )
        for monitor in self.monitors:
            monitor_area = self.to_logical_rect(self.mapper.monitor_local_rect(monitor)).toRect()
            element_rect, element_size = self.element_size_hint(current_selection)
            if element_rect is not None and not monitor_area.intersects(element_rect):
                element_rect = None
            quick_sticker = (self.settings.get("capture_quick_sticker_shortcut", "Space")
                             if self.settings.get("capture_quick_sticker_enabled", False)
                             else False)
            pick_color = None
            if self.picker_mode:
                px = self.position.x() + self.bounds["left"]
                py = self.position.y() + self.bounds["top"]
                if 0 <= px < self.image.width and 0 <= py < self.image.height:
                    r, g, b = self.image.convert("RGB").getpixel((px, py))
                    pick_color = "#%02x%02x%02x" % (r, g, b)
            paint_info(painter, global_position, current_selection, monitor_area, quick_sticker,
                       self.settings.get("capture_save_shortcut", "S"), element_rect,
                       element_size,
                       self.settings.get("window_hover_border_color", "#168cff"),
                       self.settings.get("window_hover_text_color", "#F4FFFC"),
                       self.settings.get("window_hover_badge_color", "#102A31"),
                       self.settings.get("window_hover_font_size", 12),
                       self.settings.get("window_hover_border_width", 2),
                       pick_color, picker_mode=self.picker_mode,
                       picker_shortcut=self.settings.get("capture_picker_shortcut", "C"))
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
        elif key == Qt.Key_F and event.modifiers() & Qt.ControlModifier:
            self.select_fixed_size()
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

