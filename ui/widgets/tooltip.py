"""设置表单共享控件与提示文案绑定。"""

import copy
import logging

from PySide6.QtCore import QSignalBlocker
from PySide6.QtWidgets import (QWidget, QFormLayout, QCheckBox, QSpinBox, QDoubleSpinBox,
                               QLineEdit, QComboBox, QGroupBox, QHBoxLayout, QScrollArea,
                               QVBoxLayout, QPushButton, QStyle)

from ui.widgets.color_button import ColorButton
from ui.widgets.file_path_edit import FilePathEdit


class SettingsPage(QWidget):
    """复用控件与提示文案的绑定方式，配置保存由容器统一处理。"""

    def __init__(self, config, changed):
        super().__init__()
        self.config = config
        self.changed = changed
        layout = QVBoxLayout(self)
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        layout.addWidget(scroll)
        content = QWidget()
        self.groups = QVBoxLayout(content)
        self.groups.setContentsMargins(8, 8, 8, 8)
        self.groups.setSpacing(10)
        self.groups.addStretch()
        scroll.setWidget(content)
        self.form = None
        self.controls = {}
        self.color_buttons = {}
        self.previews = []
        # 放在整页最底部，只影响当前这一页的设置项。
        self.groups.addWidget(self.build_reset_button())
        # 设置组始终插在末尾的伸缩条之前，否则内容会被顶到页面下方。
        self.group_count = 0

    def build_reset_button(self):
        """页面底部的“恢复本页默认”按钮。"""
        button = QPushButton("恢复本页默认")
        button.setIcon(self.style().standardIcon(QStyle.SP_DialogResetButton))
        button.setToolTip("只把当前这一页的选项恢复为默认值，其他页不受影响；\n"
                          "要整体回到出厂设置，用窗口底部的“恢复全部默认”")
        button.clicked.connect(self.reset_page)
        return button

    def reset_page(self):
        """把本页涉及的设置项改回默认值，并同步控件与预览。"""
        from config.config_manager import DEFAULTS

        keys = list(self.controls) + list(self.color_buttons)
        for key in keys:
            if key in DEFAULTS:
                self.config.data[key] = copy.deepcopy(DEFAULTS[key])
        self.sync_controls()
        self.changed()
        self.refresh_previews()
        logging.getLogger("screensnap").info("恢复本页默认设置: %s 页 %d 项",
                                            type(self).__name__, len(set(keys)))

    def sync_controls(self):
        """按控件类型把当前配置写回界面，避免显示停留在重置前的值。"""
        for key, widget in self.controls.items():
            value = self.config.data.get(key)
            with QSignalBlocker(widget):
                if isinstance(widget, QCheckBox):
                    widget.setChecked(bool(value))
                elif isinstance(widget, (QSpinBox, QDoubleSpinBox)):
                    widget.setValue(value)
                elif isinstance(widget, QComboBox):
                    widget.setCurrentIndex(max(0, widget.findData(value)))
                elif isinstance(widget, ColorButton):
                    widget.set_color(value)
                elif isinstance(widget, FilePathEdit):
                    widget.input.setText(str(value or ""))
                elif hasattr(widget, "set_value"):
                    # 复合控件（如提示项列表）自带 set_value，用来把配置写回界面。
                    widget.set_value(copy.deepcopy(value))
        for key, button in self.color_buttons.items():
            button.set_color(self.config.data.get(key, "#000000"))

    def group(self, title):
        box = QGroupBox(title)
        form = QFormLayout(box)
        form.setSpacing(10)
        self.groups.insertWidget(self.group_count, box)
        self.group_count += 1
        self.form = form
        return box

    def bind(self, key, widget, signal, getter, label, help_text):
        """信号到达时读取控件的最新值，而非创建时的默认值。"""
        widget.setToolTip(help_text)
        self.controls[key] = widget
        self.form.addRow(label, widget)
        signal.connect(lambda *args: self.update_value(key, getter()))
        return widget

    def update_value(self, key, value):
        """将控件值写入共享配置并通知容器立即持久化。"""
        self.config.data[key] = value
        self.changed()
        self.refresh_previews()

    def preview(self, kind, height=96):
        """插入实时预览画布，占满整行并在任何设置变化时重绘。"""
        # 延迟导入，避免设置页基类在启动时拉起编辑器与截图依赖。
        from ui.widgets.annotation_preview import AnnotationPreview
        widget = AnnotationPreview(self.config, kind, height)
        self.previews.append(widget)
        self.form.addRow(widget)
        return widget

    def refresh_previews(self):
        """外部改配置后同步刷新预览，保持界面与实际效果一致。"""
        for preview in self.previews:
            preview.refresh()

    def check(self, key, label, help_text):
        """创建布尔设置控件，变化时直接保存新状态。"""
        widget = QCheckBox()
        widget.setChecked(self.config.data[key])
        return self.bind(key, widget, widget.toggled, widget.isChecked, label, help_text)

    def number(self, key, label, low, high, help_text):
        """创建有上下限的整数设置控件。"""
        widget = QSpinBox()
        widget.setRange(low, high)
        widget.setValue(self.config.data[key])
        return self.bind(key, widget, widget.valueChanged, widget.value, label, help_text)

    def check_number(self, check_key, number_key, label, low, high, help_text, suffix=""):
        """开关与数值放在同一行，避免一项设置占用两行。"""
        box = QCheckBox()
        box.setChecked(self.config.data[check_key])
        spin = QSpinBox()
        spin.setRange(low, high)
        spin.setValue(self.config.data[number_key])
        if suffix:
            spin.setSuffix(suffix)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        row.addWidget(box)
        row.addWidget(spin)
        row.addStretch()
        container = QWidget()
        container.setLayout(row)
        box.setToolTip(help_text)
        spin.setToolTip(help_text)
        self.controls[check_key] = box
        self.controls[number_key] = spin
        self.form.addRow(label, container)
        box.toggled.connect(lambda value: self.update_value(check_key, value))
        spin.valueChanged.connect(lambda value: self.update_value(number_key, value))
        return container

    def decimal(self, key, label, low, high, help_text):
        """创建可按 0.1 步进调整的小数设置控件。"""
        widget = QDoubleSpinBox()
        widget.setRange(low, high)
        widget.setSingleStep(0.1)
        widget.setValue(self.config.data[key])
        return self.bind(key, widget, widget.valueChanged, widget.value, label, help_text)

    def text(self, key, label, help_text):
        """文本编辑结束后保存，避免输入过程中频繁写文件。"""
        widget = QLineEdit(self.config.data[key])
        return self.bind(key, widget, widget.editingFinished, widget.text, label, help_text)

    def choice(self, key, label, options, help_text):
        """使用选项的实际值而非显示名称保存枚举设置。"""
        widget = QComboBox()
        for name, value in options:
            widget.addItem(name, value)
        widget.setCurrentIndex(widget.findData(self.config.data[key]))
        return self.bind(key, widget, widget.currentIndexChanged, widget.currentData, label, help_text)

    def color(self, key, label, help_text):
        """创建颜色设置按钮，同时登记到 controls 便于统一刷新。"""
        button = ColorButton(self.config.data[key],
                             lambda value, current=key: self.update_value(current, value))
        button.setToolTip(help_text)
        self.controls[key] = button
        self.color_buttons[key] = button
        self.form.addRow(label, button)
        return button