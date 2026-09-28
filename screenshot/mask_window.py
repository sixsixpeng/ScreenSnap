"""全屏统一遮罩与多选区事件分发。"""

import os
import re
from datetime import datetime

from PySide6.QtCore import Qt, Signal, QPoint, QPointF, QRect, QEvent, QMimeData, QTimer
from PySide6.QtGui import QColor, QCursor, QPainter, QPen, QGuiApplication, QMouseEvent
from PySide6.QtWidgets import (QWidget, QDialog, QDialogButtonBox, QFormLayout, QSpinBox, QStyle,
                               QFrame, QGraphicsView, QToolButton, QLabel)

from core.dpi import DisplayMapper
from core.path_utils import resolved_dir
from core.screen_capture import to_qimage
from core.window_boundaries import visible_windows
from editor.annotation_canvas import AnnotationCanvas
from editor.toolbar_widget import ToolbarWidget
from screenshot.selection_rect import SelectionRects
from screenshot.overlay_info import paint_info
from screenshot.magnifier_widget import magnifier_rect, paint_magnifier


class MaskSession:
    """一轮截图中的共享状态；每个显示器一个 MaskWindow 共同使用。"""

    def __init__(self, mapper):
        self.mapper = mapper
        cursor_global = mapper.logical_global_to_physical_global(QCursor.pos())
        self.position = cursor_global - mapper.physical_bounds.topLeft()
        self.selection = SelectionRects()
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
        visible = (view.isVisible() and view.settings.get("magnifier", False) and
                   view.monitor_rect.contains(view.position) and
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
                        view.rect(), view.position)


class InlineEditor(QWidget):
    """贴在截图选区上的轻量编辑器；第一版只支持单屏单选区。"""

    saved = Signal(str, object)
    close_all_requested = Signal()
    sticker_requested = Signal(object)

    def __init__(self, view, rect, image, alternate):
        super().__init__(view)
        self.view = view
        self.rect = QRect(rect)
        self.settings = view.settings
        self.last_path = None
        self.resizing_region = False
        self.toolbar = ToolbarWidget(self.settings["pen_color"], self.settings)
        self.toolbar.setObjectName("inlineCaptureToolbar")
        self.toolbar.setAutoFillBackground(True)
        self.toolbar.setStyleSheet(
            "QWidget#inlineCaptureToolbar {"
            "background: #f7fbfc; border: 1px solid #8aa0a6; border-radius: 8px;"
            "}"
            "QWidget#inlineCaptureToolbar QLabel { color: #24363b; }"
            "QWidget#inlineCaptureToolbar QCheckBox {"
            "color: #1f3035; background: #ffffff; border: 1px solid #b7c5c9;"
            "border-radius: 5px; padding: 0; spacing: 0;"
            "}"
            "QWidget#inlineCaptureToolbar QCheckBox:hover { background: #e7f1f3; }"
            "QWidget#inlineCaptureToolbar QCheckBox::indicator {"
            "subcontrol-origin: content; subcontrol-position: center;"
            "}"
            "QWidget#inlineCaptureToolbar QToolButton {"
            "background: #ffffff; color: #1f3035; border: 1px solid #a9bbc0;"
            "border-radius: 5px; padding: 2px;"
            "}"
            "QWidget#inlineCaptureToolbar QToolButton:hover {"
            "background: #e1f0f4; border-color: #5f8992;"
            "}"
            "QWidget#inlineCaptureToolbar QToolButton:pressed, "
            "QWidget#inlineCaptureToolbar QToolButton:checked {"
            "background: #237a8a; color: #ffffff; border: 2px solid #0f5260;"
            "}"
            "QWidget#inlineCaptureToolbar QToolButton:checked:hover { background: #1a6876; }"
            "QWidget#inlineCaptureToolbar QToolButton:disabled {"
            "background: #e0e7e9; color: #6a7c81;"
            "}"
            "QWidget#inlineCaptureToolbar QSlider::groove:horizontal {"
            "height: 6px; background: #d8e5e8; border-radius: 3px;"
            "}"
            "QWidget#inlineCaptureToolbar QSlider::handle:horizontal {"
            "width: 15px; margin: -5px 0; background: #237a8a;"
            "border: 1px solid #155561; border-radius: 7px;"
            "}"
            "QWidget#inlineCaptureToolbar QSpinBox, "
            "QWidget#inlineCaptureToolbar QFontComboBox {"
            "background: #ffffff; color: #1f3035; border: 1px solid #b7c5c9;"
            "border-radius: 4px; padding: 2px 4px;"
            "}"
        )
        for button, action in self.toolbar.command_buttons:
            if action == "close_all_editors":
                button.hide()
        self.compact_toolbar()
        self.canvas = AnnotationCanvas(image, self.settings, alternate)
        self.canvas.setParent(view)
        self.toolbar.setParent(view)
        tool = self.settings.get("annotation_tool", "select")
        if tool not in self.toolbar.tool_buttons or tool == "crop":
            tool = "select"
        self.toolbar.tool_buttons[tool].setChecked(True)
        self.toolbar.set_tool_mode(tool)
        self.toolbar.options_button.setText("")
        self.canvas.tool = tool
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
        self.canvas.cancelled.connect(self.discard)
        self.canvas.color_picked.connect(self.apply_picked_color)
        self.canvas.selection_requested.connect(self.toolbar.tool_buttons["select"].click)
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
            view.close()
            return True
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
            for index in range(header.count()):
                widget = header.itemAt(index).widget()
                if isinstance(widget, QLabel):
                    widget.hide()
        for grid in (self.toolbar.tool_grid, self.toolbar.edit_grid, self.toolbar.image_grid,
                     self.toolbar.output_grid):
            grid.setHorizontalSpacing(2)
            grid.setVerticalSpacing(2)
        self.toolbar.section_layout.setHorizontalSpacing(6)
        self.toolbar.section_layout.setVerticalSpacing(2)
        self.toolbar.image_buttons = []
        self.toolbar.sections[2].hide()
        self.toolbar.sections[1].hide()
        self.apply_compact_buttons()
        self.toolbar.cursor_switch.setText("")
        self.toolbar.cursor_switch.setToolTip("显示鼠标")
        self.toolbar.cursor_switch.setAccessibleName("显示鼠标")
        self.toolbar.cursor_switch.setFixedSize(30, 30)
        self.toolbar.pen_color.setText("")
        self.toolbar.pen_color.setProperty("icon_only", True)
        self.toolbar.pen_color.setFixedSize(30, 30)
        self.toolbar.options_button.setText("")
        self.toolbar.options_button.setFixedSize(30, 30)
        self.arrange_inline_output_edit()

    def apply_compact_buttons(self):
        for button in self.toolbar.findChildren(QToolButton):
            button.setToolButtonStyle(Qt.ToolButtonIconOnly)
            button.setFixedSize(30, 30)
            button.setMinimumWidth(30)
            button.setMaximumWidth(30)

    def arrange_inline_output_edit(self):
        """输出组和编辑组合并为一行：先输出，空一格，再显示上一步/下一步等编辑按钮。"""
        if not hasattr(self, "output_edit_spacer"):
            self.output_edit_spacer = QWidget(self.toolbar)
            self.output_edit_spacer.setFixedSize(30, 30)
        while self.toolbar.output_grid.count():
            self.toolbar.output_grid.takeAt(0)
        widgets = [button for button in self.toolbar.output_buttons if not button.isHidden()]
        widgets.append(self.output_edit_spacer)
        widgets.extend(self.toolbar.edit_buttons)
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
        toolbar_size.setWidth(min(available, max(toolbar_size.width(), 420)))
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
        self.canvas.history = [(self.canvas.image, self.canvas.alternate, self.canvas.cursor_enabled, [])]
        self.canvas.cursor_index = 0
        self.canvas.refresh_image()
        self.position_widgets()
        self.canvas.show()
        self.toolbar.show()
        self.view.magnifier_overlay.raise_()
        self.save(automatic=True)

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
        path = directory / f"{prefix}.png"
        number = 1
        while path.exists():
            path = directory / f"{prefix}_{number}.png"
            number += 1
        return path

    def output_image(self):
        return self.canvas.render_image()

    def save(self, automatic=False, copy_to_clipboard=False, force_copy_image=False):
        path = self.last_path or self.allocate_path(automatic)
        result = self.output_image()
        if not result.save(str(path), "PNG"):
            raise OSError(f"图片保存失败：{path}")
        self.last_path = path
        self.saved.emit(str(path), result)
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
        self.canvas.tool = tool
        self.canvas.setDragMode(QGraphicsView.RubberBandDrag if tool == "select" else QGraphicsView.NoDrag)
        self.canvas.viewport().update()
        self.set_annotation_setting("annotation_tool", tool)

    def set_pen_color(self, color):
        self.settings["pen_color"] = color
        self.canvas.set_selected_color(color)
        self.canvas.update()
        self.view.session.views[0].pen_color_changed.emit(color)

    def apply_picked_color(self, color):
        self.toolbar.pen_color.set_color(color)
        self.toolbar.pen_color.setText("")
        self.set_pen_color(color)
        QGuiApplication.clipboard().setText(color)

    def set_annotation_setting(self, key, value):
        from config.config_manager import TOOL_WIDTH_KEYS
        self.settings[key] = value
        if key in TOOL_WIDTH_KEYS.values():
            self.canvas.set_selected_width(value)
        elif key in ("rect_style", "ellipse_style"):
            self.canvas.set_selected_line_style(value)
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
            self.sticker_requested.emit(self.output_image())
            self.view.close()
        elif action == "save":
            self.save(copy_to_clipboard=True)
            self.view.close()
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

    selected = Signal(object)
    last_region = Signal(object)
    image_saved = Signal(str, object)
    save_failed = Signal(str)
    close_all_requested = Signal()
    sticker_requested = Signal(object)
    annotation_setting_changed = Signal(str, object)
    pen_color_changed = Signal(str)

    def __init__(self, image, bounds, monitors, settings, mode="capture", alternate=None,
                 session=None, monitor=None, primary=True):
        super().__init__()
        self.image = image
        self.alternate = alternate
        self.preview = to_qimage(image)
        self.bounds = bounds
        self.monitors = monitors
        self.mapper = session.mapper if session is not None else DisplayMapper(bounds, monitors)
        self.session = session or MaskSession(self.mapper)
        self.primary = primary
        self.monitor = monitor or (monitors[0] if monitors else bounds)
        self.monitor_rect = self.mapper.monitor_local_rect(self.monitor)
        self.session.views.append(self)
        # 截图前只枚举一次窗口边界，避免鼠标移动时反复调用系统 API。
        self.window_edges = visible_windows() if primary else []
        self.settings = settings
        self.capture_cursor_enabled = settings["cursor"]
        self.selection = self.session.selection
        self.position = QPoint(self.session.position)
        self.resize_cursor = "nwse"
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setGeometry(self.mapper.physical_local_rect_to_native_global_rect(self.monitor_rect).toRect())
        self.magnifier_overlay = MagnifierOverlay(self)
        if primary:
            for extra_monitor in monitors[1:]:
                MaskWindow(image, bounds, monitors, settings, mode, alternate,
                           self.session, extra_monitor, primary=False)
        if primary and mode == "fullscreen":
            self.selection.rects.append(QRect(0, 0, bounds["width"], bounds["height"]))
        elif primary and mode == "monitor":
            point = self.mapper.logical_global_to_physical_global(self.cursor().pos())
            monitor = next((screen for screen in monitors if
                            screen["left"] <= point.x() < screen["left"] + screen["width"] and
                            screen["top"] <= point.y() < screen["top"] + screen["height"]), monitors[0])
            self.selection.rects.append(QRect(monitor["left"] - bounds["left"],
                                              monitor["top"] - bounds["top"],
                                              monitor["width"], monitor["height"]))

    def logical_window_offset(self):
        """当前窗口左上角相对整轮遮罩 logical_bounds 的偏移。"""
        return self.mapper.physical_local_rect_to_logical_global_rect(
            self.monitor_rect).toRect().topLeft() - self.mapper.logical_bounds.topLeft()

    def show(self):
        if self.primary:
            for view in self.session.views:
                QWidget.show(view)
                view.magnifier_overlay.sync()
            self.activateWindow()
            self.setFocus(Qt.ActiveWindowFocusReason)
        else:
            QWidget.show(self)
            self.magnifier_overlay.sync()
            self.activateWindow()
            self.setFocus(Qt.ActiveWindowFocusReason)

    def hide(self):
        if self.primary:
            for view in self.session.views:
                QWidget.hide(view)
        else:
            QWidget.hide(self)

    def close(self):
        if self.session.closing:
            QWidget.close(self)
            return
        self.session.closing = True
        for view in list(self.session.views):
            QWidget.close(view)

    def closeEvent(self, event):
        """窗口关闭后释放截图大图，避免 Application.mask 暂存时继续占用内存。"""
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
        self.session.position = QPoint(self.position)
        for view in self.session.views:
            view.position = QPoint(self.session.position)
            view.update()
            view.magnifier_overlay.sync()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.magnifier_overlay.sync()

    def inline_active(self):
        return self.session.inline_editor is not None

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

    def showEvent(self, event):
        super().showEvent(event)
        self.activateWindow()
        self.setFocus(Qt.ActiveWindowFocusReason)

    def paintEvent(self, event):
        """遮罩、锚点和 HUD 仅绘制在窗口表面，不写入原始截图。"""
        painter = QPainter(self)
        painter.drawImage(self.to_logical_rect(self.monitor_rect), self.preview, self.monitor_rect)
        opacity = round(255 * self.settings.get("mask_opacity", 50) / 100)
        shade = QColor(0, 0, 0, opacity) if self.settings["mask_theme"] == "dark" else QColor(255, 255, 255, opacity)
        painter.fillRect(self.rect(), shade)
        for rect in self.selection.rects + ([self.selection.active] if self.selection.active else []):
            clipped = rect.intersected(self.monitor_rect)
            if clipped.isEmpty():
                continue
            logical_rect = self.to_logical_rect(clipped)
            painter.setPen(QPen(QColor(self.settings.get("selection_border_color", "#ff0000")), 1))
            painter.drawImage(logical_rect, self.preview, clipped)
            painter.drawRect(logical_rect)
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
            paint_info(painter, global_position, current_selection, monitor_area)
        painter.setPen(QPen(QColor(self.settings.get("selection_border_color", "#ff0000")), 1))
        painter.setBrush(Qt.NoBrush)
        for rect in self.selection.rects + ([self.selection.active] if self.selection.active else []):
            clipped = rect.intersected(self.monitor_rect)
            if not clipped.isEmpty():
                painter.drawRect(self.to_logical_rect(clipped))

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
        if event.button() == Qt.LeftButton:
            self.position = self.to_physical_point(event.position().toPoint())
            hit = self.selection.handle_at(self.position)
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
            self.update_all()

    def mouseDoubleClickEvent(self, event):
        """双击提交所有有效选区，没有选区时仅关闭遮罩。"""
        if self.inline_active():
            return
        self.complete()

    def keyPressEvent(self, event):
        """处理取消、提交、固定尺寸创建与最后选区的像素微调。"""
        key = event.key()
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

    def select_fixed_size(self):
        """一次输入宽高，确认后在当前光标位置创建选区。"""
        dialog = QDialog(self)
        dialog.setWindowTitle("固定选区")
        form = QFormLayout(dialog)
        width = QSpinBox(dialog)
        width.setRange(1, max(1, self.bounds["width"]))
        width.setValue(min(640, width.maximum()))
        height = QSpinBox(dialog)
        height.setRange(1, max(1, self.bounds["height"]))
        height.setValue(min(480, height.maximum()))
        form.addRow("宽度 (px)", width)
        form.addRow("高度 (px)", height)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=dialog)
        buttons.button(QDialogButtonBox.Ok).setIcon(self.style().standardIcon(QStyle.SP_DialogApplyButton))
        buttons.button(QDialogButtonBox.Cancel).setIcon(self.style().standardIcon(QStyle.SP_DialogCancelButton))
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec() == QDialog.Accepted:
            self.selection.fixed(self.position, width.value(), height.value())
            self.update_all()

    def complete(self):
        """从原图裁切每个选区，成对发出带/不带光标的图片。"""
        if not self.primary:
            self.session.views[0].complete()
            return
        if self.session.completing:
            return
        self.session.completing = True
        if self.try_inline_edit():
            self.session.completing = False
            return
        images = []
        last_rect = None
        physical_area = self.mapper.full_physical_local_rect()
        for rect in self.selection.rects:
            # 移出遮罩的部分先裁到桌面范围；Pillow 的右下边界是开区间。
            clipped = rect.intersected(physical_area)
            if clipped.width() and clipped.height():
                area = (clipped.x(), clipped.y(), clipped.right() + 1, clipped.bottom() + 1)
                images.append((self.image.crop(area), self.alternate.crop(area) if self.alternate else None))
                last_rect = [clipped.x() + self.bounds["left"], clipped.y() + self.bounds["top"],
                             clipped.width(), clipped.height()]
        if images:
            self.hide()
            self.last_region.emit(last_rect)
            self.selected.emit(images)
        self.close()

    def inline_view_for_rect(self, rect):
        for view in self.session.views:
            if view.monitor_rect.contains(rect):
                return view
        return None

    def try_inline_edit(self):
        """第一版原地编辑仅处理单选区且完整位于一个显示器内的截图。"""
        if not self.settings.get("inline_edit", False):
            return False
        if len(self.selection.rects) != 1:
            return False
        rect = self.selection.rects[0].intersected(self.mapper.full_physical_local_rect())
        if rect.isEmpty():
            return False
        view = self.inline_view_for_rect(rect)
        if view is None:
            return False
        area = (rect.x(), rect.y(), rect.right() + 1, rect.bottom() + 1)
        crop = self.image.crop(area)
        alternate = self.alternate.crop(area) if self.alternate else None
        self.last_region.emit([rect.x() + self.bounds["left"], rect.y() + self.bounds["top"],
                               rect.width(), rect.height()])
        editor = InlineEditor(view, rect, crop, alternate)
        editor.saved.connect(self.image_saved)
        editor.close_all_requested.connect(self.close_all_requested)
        editor.sticker_requested.connect(self.sticker_requested)
        try:
            editor.save(automatic=True)
        except OSError as error:
            self.save_failed.emit(f"初始保存失败: {error}")
        editor.toolbar.show()
        editor.canvas.show()
        editor.canvas.setFocus()
        self.session.inline_editor = editor
        self.update_all()
        return True

