"""截图编辑窗口及导出操作。"""

import os
import re
import logging
from datetime import datetime

from PySide6.QtCore import Qt, Signal, QSignalBlocker, QMimeData
from PySide6.QtGui import QGuiApplication, QKeySequence, QShortcut
from PySide6.QtWidgets import (QMainWindow, QWidget, QVBoxLayout,
                               QDialog, QDialogButtonBox, QDial, QDoubleSpinBox, QHBoxLayout, QLabel,
                               QStyle, QSlider, QSpinBox, QGraphicsView)

from core.image_io import save_image, saved_extension
from core.path_utils import resolved_dir
from config.config_manager import TOOL_WIDTH_KEYS
from editor.annotation_canvas import AnnotationCanvas
from editor.image_transform import transform
from editor.toolbar_widget import ToolbarWidget


class EditorWindow(QMainWindow):
    """将工具栏命令接入画布，并统一处理保存、剪贴板与贴图输出。"""

    image_saved = Signal(str, object)
    sticker_requested = Signal(object)
    close_all_requested = Signal()
    status = Signal(str)
    pen_color_changed = Signal(str)
    setting_changed = Signal(str, object)

    def __init__(self, image, settings, alternate=None):
        super().__init__()
        self.settings = settings
        self.last_path = None
        self.setWindowTitle("截图编辑器")
        self.resize(1200, 760)
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(8, 6, 8, 8)
        layout.setSpacing(4)
        self.toolbar = ToolbarWidget(settings["pen_color"], settings)
        self.canvas = AnnotationCanvas(image, settings, alternate)
        self.canvas.tool = next((key for key, button in self.toolbar.tool_buttons.items()
                                 if button.isChecked()), "select")
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
        self.operation_tips.setStyleSheet(
            "QLabel { color: #34434a; background: #eef2f3; "
            "border-radius: 4px; padding: 6px 9px; }"
        )
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
        self.setCentralWidget(container)
        self.toolbar.tool_changed.connect(self.set_tool)
        self.toolbar.color_changed.connect(self.set_pen_color)
        self.toolbar.setting_changed.connect(self.set_annotation_setting)
        self.toolbar.crop_style_changed.connect(self.set_crop_style)
        self.toolbar.command.connect(self.execute)
        # 画布取得焦点时仍由窗口级快捷键处理 Esc，避免误执行保存。
        self.escape_shortcut = QShortcut(QKeySequence("Esc"), self)
        self.escape_shortcut.activated.connect(self.close)
        self.canvas.confirmed.connect(lambda: self.execute("save"))
        self.canvas.cancelled.connect(self.close)
        self.canvas.color_picked.connect(self.apply_picked_color)

    def set_tool(self, tool):
        """将工具栏的标注工具切换同步到画布。"""
        self.canvas.tool = tool
        if tool != "select":
            self.canvas.unsetCursor()
        self.canvas.selection_area = None
        self.canvas.selection_start = None
        self.canvas.selection_end = None
        self.canvas.setDragMode(QGraphicsView.RubberBandDrag if tool == "select" else QGraphicsView.NoDrag)
        self.canvas.eraser_point = None
        self.canvas.viewport().update()
        self.set_annotation_setting("annotation_tool", tool)

    def set_pen_color(self, color):
        """更新当前画笔并通知设置页将新颜色写入配置。"""
        self.settings["pen_color"] = color
        self.canvas.set_selected_color(color)
        self.canvas.update()
        self.pen_color_changed.emit(color)

    def apply_picked_color(self, color):
        """将取样颜色设为当前标注色，同时复制十六进制值便于粘贴使用。"""
        self.toolbar.pen_color.set_color(color)
        self.set_pen_color(color)
        QGuiApplication.clipboard().setText(color)

    def set_annotation_setting(self, key, value):
        """保存当前工具参数，并更新适用的已选标注。"""
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
        return self.canvas.render_image()

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
            self.sticker_requested.emit(self.output_image())
        elif action == "save":
            self.save(copy_to_clipboard=True)
            self.close()
        elif action == "copy":
            self.save(copy_to_clipboard=True, force_copy_image=True)
        elif action == "path":
            if not self.last_path:
                self.save()
            QGuiApplication.clipboard().setText(str(self.last_path))
            self.status.emit("文件路径已复制")
        elif action == "close_all_editors":
            self.close_all_requested.emit()
        elif action == "discard":
            self.close()