"""截图编辑窗口及导出操作。"""

import os
import re
import logging
from datetime import datetime

from PySide6.QtCore import Qt, Signal, QSignalBlocker, QMimeData
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (QMainWindow, QWidget, QVBoxLayout,
                               QDialog, QDialogButtonBox, QDial, QDoubleSpinBox, QHBoxLayout, QLabel,
                               QStyle, QSlider, QSpinBox, QGraphicsView)

from core.image_io import save_image, saved_extension
from core.path_utils import resolved_dir
from config.config_manager import TOOL_WIDTH_KEYS, fill_colors_following_stroke
from editor.annotation_canvas import AnnotationCanvas
from editor.image_effects import apply_output_effects
from core.screen_capture import qimage_to_pillow
from editor.image_transform import transform
from editor.toolbar_widget import ToolbarWidget

ANNOTATION_COLOR_TOOLS = frozenset(("pen", "rect", "ellipse", "arrow", "marker", "text"))


class EditorWindow(QMainWindow):
    """将工具栏命令接入画布，并统一处理保存、剪贴板与贴图输出。"""

    image_saved = Signal(str, object)
    sticker_requested = Signal(object)
    recapture_requested = Signal()
    close_all_requested = Signal()
    status = Signal(str)
    pen_color_changed = Signal(str)
    tool_color_changed = Signal(str, str)
    setting_changed = Signal(str, object)

    def __init__(self, image, settings, alternate=None, from_capture=False):
        super().__init__()
        self.settings = settings
        self.from_capture = from_capture
        self.round_corners = bool(settings.get("editor_image_round_corners", True))
        self.last_path = None
        self.setWindowTitle("截图编辑器")
        self.resize(1200, 760)
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(8, 6, 8, 8)
        layout.setSpacing(4)
        self.toolbar = ToolbarWidget(settings["pen_color"], settings,
                         show_capture_actions=False)
        self.canvas = AnnotationCanvas(image, settings, alternate)
        self.canvas.set_round_corner_preview(
            self.round_corners, settings.get("editor_image_corner_radius", 16))
        self.canvas.set_tool(next((key for key, button in self.toolbar.tool_buttons.items()
                       if button.isChecked()), "select"))
        self.last_color_tool = self.canvas.tool if self.canvas.tool in ANNOTATION_COLOR_TOOLS else "pen"
        self.toolbar.set_active_tool(self.canvas.tool, self.last_color_tool)
        self.canvas.setDragMode(QGraphicsView.RubberBandDrag if self.canvas.tool == "select"
                    else QGraphicsView.NoDrag)
        self.canvas.text_alignment = {"left": Qt.AlignLeft, "center": Qt.AlignHCenter,
                                      "right": Qt.AlignRight}.get(settings.get("text_alignment"), Qt.AlignLeft)
        self.toolbar.cursor_switch.setChecked(settings["cursor"])
        self.toolbar.cursor_switch.setEnabled(alternate is not None)
        self.toolbar.cursor_switch.toggled.connect(self.canvas.toggle_cursor)
        self.toolbar.cursor_switch.toggled.connect(
            lambda enabled: self.set_annotation_setting("cursor", enabled))
        self.canvas.changed.connect(self.update_cursor_switch)
        self.canvas.changed.connect(self.invalidate_rotation_reset)
        layout.addWidget(self.toolbar)
        layout.addWidget(self.canvas, 1)
        self.operation_tips = QLabel(
            "拖动绘制标注 | 选择工具可快速选中、移动或调整标注 | "
            "双击空白处保存并退出 | 滚轮上下滚动，Ctrl/Alt+滚轮横向移动 | "
            "用滑块或数值调整缩放 | 右键/中键拖动或空格拖动平移 | Esc 放弃编辑"
        )
        self.operation_tips.setObjectName("editorOperationTips")
        self.operation_tips.setWordWrap(True)
        layout.addWidget(self.operation_tips)
        zoom_row = QHBoxLayout()
        zoom_row.addWidget(QLabel("缩放"))
        self.zoom_slider = QSlider(Qt.Horizontal)
        self.zoom_slider.setRange(1, 800)
        self.zoom_slider.setValue(100)
        self.zoom_slider.setToolTip("图片和标注的显示缩放比例")
        zoom_row.addWidget(self.zoom_slider, 1)
        self.zoom_input = QSpinBox()
        self.zoom_input.setRange(1, 800)
        self.zoom_input.setValue(100)
        self.zoom_input.setSuffix("%")
        self.zoom_input.setToolTip("精确输入显示缩放比例")
        zoom_row.addWidget(self.zoom_input)
        layout.addLayout(zoom_row)
        self.zoom_slider.valueChanged.connect(self.canvas.set_zoom)
        self.zoom_input.valueChanged.connect(self.canvas.set_zoom)
        self.canvas.zoom_changed.connect(self.zoom_slider.setValue)
        self.canvas.zoom_changed.connect(self.zoom_input.setValue)
        self.canvas.selection_requested.connect(self.toolbar.tool_buttons["select"].click)
        self.canvas.setting_changed.connect(self.apply_canvas_setting)
        self.setCentralWidget(container)
        self.toolbar.tool_changed.connect(self.set_tool)
        self.toolbar.color_changed.connect(self.set_pen_color)
        self.toolbar.setting_changed.connect(self.set_annotation_setting)
        self.toolbar.crop_style_changed.connect(self.set_crop_style)
        self.toolbar.command.connect(self.execute)
        # 不再把 Esc 绑定为“退出编辑”：取色、字体等模态对话框也用 Esc 关闭，
        # 窗口级快捷键会在对话框打开时一并触发，导致关掉对话框的同时直接退出编辑。
        self.canvas.confirmed.connect(lambda: self.execute("save"))
        # 不再把画布上的 Esc 绑定为“退出编辑”：取色、字体等模态框打开时焦点可能落在画布，
        # Esc 会被画布接收并直接关掉整个编辑窗口。改为仅取消当前选中，不关闭窗口。
        self.canvas.cancelled.connect(lambda: self.canvas.scene_data.clearSelection())
        self.canvas.color_picked.connect(self.apply_picked_color)

    def set_tool(self, tool):
        """将工具栏的标注工具切换同步到画布。"""
        if tool in ANNOTATION_COLOR_TOOLS:
            self.last_color_tool = tool
        self.canvas.set_tool(tool)
        self.canvas.selection_area = None
        self.canvas.selection_start = None
        self.canvas.selection_end = None
        self.canvas.setDragMode(QGraphicsView.RubberBandDrag if tool == "select" else QGraphicsView.NoDrag)
        self.canvas.eraser_point = None
        self.canvas.viewport().update()
        self.toolbar.set_active_tool(tool, self.last_color_tool)
        self.set_annotation_setting("annotation_tool", tool)

    def set_pen_color(self, color):
        """更新当前标注工具颜色，并同步已选图元与设置页。"""
        tool = self.canvas.tool if self.canvas.tool in ANNOTATION_COLOR_TOOLS else self.last_color_tool
        key = "pen_color" if tool == "pen" else f"{tool}_color"
        self.settings[key] = color
        self.toolbar.sync_tool_color(key, color)
        self.canvas.settings[key] = color
        self.canvas.set_selected_color(color)
        self.canvas.update()
        self.tool_color_changed.emit(tool, color)
        if tool == "pen":
            self.pen_color_changed.emit(color)

    def apply_picked_color(self, color):
        """将取样色写入吸管前最近使用的可调色工具，并复制色值。"""
        self.set_pen_color(color)
        self.toolbar.set_active_tool(self.canvas.tool, self.last_color_tool)
        self.toolbar.pen_color.set_color(color)
        QGuiApplication.clipboard().setText(color)

    def apply_canvas_setting(self, key, value):
        """画布内入口（如文字输入对话框）改配置：写入配置并同步工具栏控件。"""
        self.set_annotation_setting(key, value)
        self.toolbar.sync_setting(key, value)

    def set_annotation_setting(self, key, value):
        """保存当前工具参数，并更新适用的已选标注。"""
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
                self.canvas.update()
        if key == "editor_image_round_corners":
            self.round_corners = value
        if key in ("editor_image_round_corners", "editor_image_corner_radius"):
            self.canvas.set_round_corner_preview(
                self.round_corners, self.settings.get("editor_image_corner_radius", 16))
        if key in TOOL_WIDTH_KEYS.values():
            self.canvas.set_selected_width(value)
        elif key in ("rect_style", "ellipse_style"):
            self.canvas.set_selected_line_style(value)
        elif key == "arrow_style":
            self.canvas.set_selected_arrow_style(value)
        elif key in ("text_bold", "text_italic", "text_underline", "text_strikethrough"):
            self.canvas.set_selected_text_format()
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
        self.setting_changed.emit(key, value)

    def set_crop_style(self, key, value):
        if key == "crop_color":
            self.canvas.crop_color = value
        elif key == "crop_width":
            self.canvas.crop_width = value
        self.settings[key] = value
        self.setting_changed.emit(key, value)
        self.canvas.viewport().update()

    def update_cursor_switch(self):
        """撤销后同步复选框，屏蔽信号以免误触发新一次切换。"""
        with QSignalBlocker(self.toolbar.cursor_switch):
            self.toolbar.cursor_switch.setChecked(self.canvas.cursor_enabled)

    def output_image(self):
        """所有导出操作共用同一张合成图，避免保存与剪贴板结果不一致。"""
        return apply_output_effects(self.canvas.render_image(), self.settings,
                                    self.round_corners,
                                    self.settings.get("editor_image_corner_radius", 16))

    def allocate_path(self, automatic=False):
        """首次保存时分配唯一文件名；之后保存复用 last_path 覆盖。"""
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

    def save(self, automatic=False, copy_to_clipboard=False, force_copy_image=False):
        """首次保存分配文件名；后续保存覆盖同一路径，避免重复文件。"""
        logger = logging.getLogger("screensnap")
        path = self.last_path or self.allocate_path(automatic)
        result = self.output_image()
        if not save_image(result, path, self.settings):
            logger.error("图片保存失败: %s（格式 %s，质量 %s）", path,
                         self.settings["save_format"], self.settings["save_quality"])
            raise OSError(f"图片保存失败：{path}")
        logger.info("保存图片: %s（%sx%s，%s）", path, result.width(), result.height(),
                    self.settings["save_format"])
        self.last_path = path
        self.image_saved.emit(str(path), result)
        if (copy_to_clipboard and (self.settings["copy_saved_image"] or self.settings["copy_saved_path"])) or force_copy_image:
            payload = QMimeData()
            if self.settings["copy_saved_path"]:
                payload.setText(str(path))
            if self.settings["copy_saved_image"] or force_copy_image:
                payload.setImageData(result)
            QGuiApplication.clipboard().setMimeData(payload)
            self.status.emit("已复制保存内容")
        if self.settings["open_dir"] and os.name == "nt":
            try:
                os.startfile(str(path.parent))
            except OSError as error:
                logging.getLogger("screensnap").warning("打开保存目录失败 %s: %s", path.parent, error)
        return path

    def copy_to_clipboard_only(self):
        """仅把当前合成图复制到剪贴板，不落盘、不退出编辑。"""
        logger = logging.getLogger("screensnap")
        result = self.output_image()
        payload = QMimeData()
        payload.setImageData(result)
        QGuiApplication.clipboard().setMimeData(payload)
        self.status.emit("已复制到剪贴板")
        logger.info("仅复制图片到剪贴板: %dx%d", result.width(), result.height())

    def invalidate_rotation_reset(self):
        self.rotation_reset_state = None
        self.toolbar.reset_rotation_button.setEnabled(False)

    def rotate_angle(self):
        """旋钮和数字输入同步控制画布预览，确认后只记录一次历史。"""
        self.invalidate_rotation_reset()
        dialog = QDialog(self)
        dialog.setWindowTitle("任意角度旋转")
        dialog.setMinimumSize(500, 310)
        dialog.resize(560, 360)
        layout = QVBoxLayout(dialog)
        row = QHBoxLayout()
        dial = QDial(dialog)
        dial.setRange(-360, 360)
        dial.setNotchesVisible(True)
        dial.setMinimumSize(190, 190)
        dial.setToolTip("拖动旋钮预览旋转角度；数字框可精确输入")
        degrees = QDoubleSpinBox(dialog)
        degrees.setObjectName("rotationDegrees")
        degrees.setRange(-360, 360)
        degrees.setSingleStep(1)
        degrees.setSuffix("°")
        row.addWidget(dial)
        row.addWidget(QLabel("角度", dialog))
        row.addWidget(degrees)
        layout.addLayout(row)
        original = self.canvas.image
        alternate = self.canvas.alternate
        annotations = self.canvas.snapshot()

        def preview(value):
            self.canvas.image = transform(original, "angle", value)
            self.canvas.alternate = transform(alternate, "angle", value) if alternate is not None else None
            self.canvas.refresh_image()
            self.canvas.transform_annotations(annotations, "angle", value, original.size)

        dial.valueChanged.connect(degrees.setValue)
        degrees.valueChanged.connect(lambda value: dial.setValue(round(value)) if dial.value() != round(value)
                         else None)
        degrees.valueChanged.connect(preview)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=dialog)
        buttons.button(QDialogButtonBox.Ok).setIcon(self.style().standardIcon(QStyle.SP_DialogApplyButton))
        buttons.button(QDialogButtonBox.Cancel).setIcon(self.style().standardIcon(QStyle.SP_DialogCancelButton))
        reset_angle = buttons.addButton("重置角度", QDialogButtonBox.ResetRole)
        reset_angle.setObjectName("resetRotationAngle")
        reset_angle.clicked.connect(lambda: degrees.setValue(0))
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() == QDialog.Accepted and degrees.value():
            self.canvas.checkpoint()
            self.rotation_reset_state = (
                original, alternate, annotations, self.canvas.image,
                self.canvas.alternate, self.canvas.cursor_index,
            )
            self.toolbar.reset_rotation_button.setEnabled(True)
        else:
            self.canvas.image = original
            self.canvas.alternate = alternate
            self.canvas.refresh_image()
            self.canvas.restore(annotations)

    def execute(self, action):
        """分发工具栏命令；图片变换同步处理光标替代版本。"""
        logging.getLogger("screensnap").debug("编辑器操作: %s", action)
        if action in ("undo", "redo", "reset"):
            getattr(self.canvas, action)()
        elif action == "reset_rotation":
            state = self.rotation_reset_state
            if state is None:
                return
            original, alternate, annotations, rotated_image, rotated_alternate, history_index = state
            if (self.canvas.cursor_index != history_index or self.canvas.image is not rotated_image or
                    self.canvas.alternate is not rotated_alternate):
                self.invalidate_rotation_reset()
                return
            self.invalidate_rotation_reset()
            self.canvas.image = original
            self.canvas.alternate = alternate
            self.canvas.refresh_image()
            self.canvas.restore(annotations)
            self.canvas.checkpoint()
        elif action == "delete":
            self.canvas.remove_selected()
        elif action in ("top", "bottom", "up", "down"):
            self.canvas.layer(action)
        elif action == "angle":
            self.rotate_angle()
        elif action in ("left", "right", "half", "horizontal", "vertical"):
            old_size = self.canvas.image.size
            annotations = self.canvas.snapshot()
            self.canvas.image = transform(self.canvas.image, action)
            if self.canvas.alternate is not None:
                self.canvas.alternate = transform(self.canvas.alternate, action)
            self.canvas.refresh_image()
            self.canvas.transform_annotations(annotations, action, 0, old_size)
            self.canvas.checkpoint()
        elif action == "paste":
            image = self.output_image()
            logging.getLogger("screensnap").debug(
                "编辑器请求创建贴图: %dx%d", image.width(), image.height())
            self.sticker_requested.emit(image)
            self.suppress_save_notification = True
            try:
                self.save(automatic=True)
            except OSError as error:
                logging.getLogger("screensnap").error(
                    "贴图已创建但编辑器自动保存失败: %s", error, exc_info=True)
                self.status.emit(f"贴图已创建，但自动保存失败: {error}")
            finally:
                self.suppress_save_notification = False
                self.close()
        elif action == "custom_size":
            self.set_custom_size()
        elif action == "recapture":
            self.recapture_requested.emit()
        elif action == "save":
            self.save(copy_to_clipboard=True)
            self.close()
        elif action == "copy":
            self.save(copy_to_clipboard=True, force_copy_image=True)
        elif action == "copy_only":
            self.copy_to_clipboard_only()
        elif action == "path":
            if not self.last_path:
                self.save()
            QGuiApplication.clipboard().setText(str(self.last_path))
            self.status.emit("文件路径已复制")
        elif action == "close_all_editors":
            self.close_all_requested.emit()
        elif action == "discard":
            self.close()

    def set_custom_size(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("自定义画布尺寸")
        layout = QFormLayout(dialog)
        width_input = QSpinBox(dialog)
        width_input.setRange(1, 16384)
        width_input.setValue(self.canvas.image.width)
        height_input = QSpinBox(dialog)
        height_input.setRange(1, 16384)
        height_input.setValue(self.canvas.image.height)
        layout.addRow("宽度", width_input)
        layout.addRow("高度", height_input)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=dialog)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addRow(buttons)
        if dialog.exec() != QDialog.Accepted:
            return

        size = width_input.value(), height_input.value()
        rendered = qimage_to_pillow(self.canvas.render_image())
        left = max(0, (rendered.width - size[0]) // 2)
        top = max(0, (rendered.height - size[1]) // 2)
        right, bottom = min(rendered.width, left + size[0]), min(rendered.height, top + size[1])
        cropped = rendered.crop((left, top, right, bottom))
        if cropped.size != size:
            from PIL import Image
            canvas = Image.new("RGBA", size, (0, 0, 0, 0))
            canvas.paste(cropped, (max(0, (size[0] - cropped.width) // 2),
                                   max(0, (size[1] - cropped.height) // 2)))
            cropped = canvas
        self.canvas.image = cropped
        self.canvas.alternate = None
        self.canvas.cursor_enabled = False
        self.toolbar.cursor_switch.setChecked(False)
        self.toolbar.cursor_switch.setEnabled(False)
        self.canvas.refresh_image()
        self.canvas.restore([])
        self.canvas.checkpoint()