"""设置表单共享控件与悬浮提示样式。"""

from PySide6.QtWidgets import (QWidget, QFormLayout, QCheckBox, QSpinBox, QDoubleSpinBox,
                               QLineEdit, QComboBox, QGroupBox, QScrollArea, QVBoxLayout)


TOOLTIP_STYLE = "QToolTip { background: #213640; color: white; border: 1px solid #00ad91; padding: 8px; border-radius: 5px; }"


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

    def group(self, title):
        box = QGroupBox(title)
        form = QFormLayout(box)
        form.setSpacing(10)
        self.groups.insertWidget(self.groups.count() - 1, box)
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