"""文字标注输入对话框：输入框上方带一排文字样式设置，默认套用当前配置并可同步回配置。"""

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFontComboBox,
                               QFormLayout, QGroupBox, QHBoxLayout, QLabel, QPlainTextEdit,
                               QSpinBox, QVBoxLayout, QWidget)

from ui.widgets.color_button import ColorButton

# 对话框可直接调整并回写到配置的文字相关设置项。
TEXT_SETTING_KEYS = ("font", "font_size", "text_alignment", "text_color",
                     "text_bold", "text_italic", "text_underline", "text_strikethrough",
                     "text_background_enabled", "text_background")

# 配置缺失时的兜底值，与 config.DEFAULTS 保持一致。
TEXT_SETTING_FALLBACKS = {
    "font": "", "font_size": 18, "text_alignment": "left", "text_color": "#ff0000",
    "text_bold": False, "text_italic": False, "text_underline": False,
    "text_strikethrough": False, "text_background_enabled": False,
    "text_background": "#fff3a0",
}

ALIGNMENT_OPTIONS = (("左对齐", "left"), ("居中", "center"), ("右对齐", "right"))

FORMAT_OPTIONS = (("text_bold", "粗体"), ("text_italic", "斜体"),
                  ("text_underline", "下划线"), ("text_strikethrough", "删除线"))


def _row(layout):
    """把横向布局包成可放入表单的控件。"""
    widget = QWidget()
    widget.setLayout(layout)
    return widget


class TextInputDialog(QDialog):
    """输入文字的同时调整样式：控件默认取当前配置，改动的项会同步回配置。"""

    def __init__(self, parent, title, settings, initial_text="", label="内容"):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(460)
        self._original = {key: settings.get(key, TEXT_SETTING_FALLBACKS[key])
                          for key in TEXT_SETTING_KEYS}
        self._values = dict(self._original)
        layout = QVBoxLayout(self)
        layout.addWidget(self._build_style_group())
        layout.addWidget(QLabel(label))
        self.editor = QPlainTextEdit(initial_text)
        self.editor.setMinimumHeight(120)
        layout.addWidget(self.editor)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.editor.setFocus()

    def _build_style_group(self):
        group = QGroupBox("文字样式（默认套用当前设置，改动会同步保存）")
        form = QFormLayout(group)
        form.setSpacing(8)

        self.font = QFontComboBox()
        self.font.setToolTip("新建或本次文字使用的字体")
        if self._values["font"]:
            self.font.setCurrentFont(QFont(self._values["font"]))
        self.font.currentFontChanged.connect(lambda value: self._set("font", value.family()))

        self.font_size = QSpinBox()
        self.font_size.setRange(6, 200)
        self.font_size.setValue(int(self._values["font_size"]))
        self.font_size.setSuffix(" pt")
        self.font_size.setToolTip("文字字号")
        self.font_size.valueChanged.connect(lambda value: self._set("font_size", value))

        self.alignment = QComboBox()
        for text, value in ALIGNMENT_OPTIONS:
            self.alignment.addItem(text, value)
        self.alignment.setCurrentIndex(
            max(0, self.alignment.findData(self._values["text_alignment"])))
        self.alignment.setToolTip("多行文字的对齐方式")
        self.alignment.currentIndexChanged.connect(
            lambda _index: self._set("text_alignment", self.alignment.currentData()))

        font_row = QHBoxLayout()
        font_row.addWidget(self.font)
        font_row.addWidget(self.font_size)
        font_row.addWidget(QLabel("对齐"))
        font_row.addWidget(self.alignment)
        form.addRow("字体", _row(font_row))

        self.checks = {}
        format_row = QHBoxLayout()
        for key, text in FORMAT_OPTIONS:
            box = QCheckBox(text)
            box.setChecked(bool(self._values[key]))
            box.toggled.connect(lambda value, name=key: self._set(name, bool(value)))
            format_row.addWidget(box)
            self.checks[key] = box
        format_row.addStretch()
        form.addRow("格式", _row(format_row))

        self.text_color = ColorButton(self._values["text_color"],
                                      lambda value: self._set("text_color", value))
        self.text_color.setToolTip("文字颜色")
        self.background_enabled = QCheckBox("文字背景")
        self.background_enabled.setChecked(bool(self._values["text_background_enabled"]))
        self.background_enabled.setToolTip("为文字添加背景色块")
        self.background_enabled.toggled.connect(
            lambda value: self._set("text_background_enabled", bool(value)))
        self.background = ColorButton(self._values["text_background"],
                                      lambda value: self._set("text_background", value))
        self.background.setToolTip("文字背景色块的颜色；需开启文字背景")

        color_row = QHBoxLayout()
        color_row.addWidget(QLabel("文字"))
        color_row.addWidget(self.text_color)
        color_row.addWidget(self.background_enabled)
        color_row.addWidget(QLabel("背景色"))
        color_row.addWidget(self.background)
        color_row.addStretch()
        form.addRow("颜色", _row(color_row))
        return group

    def _set(self, key, value):
        self._values[key] = value

    def text(self):
        return self.editor.toPlainText()

    def values(self):
        """返回对话框内全部文字设置（含未改动的项）。"""
        return dict(self._values)

    def changed_settings(self):
        """返回与进入对话框时不同的设置项，供调用方回写配置。"""
        return {key: value for key, value in self._values.items()
                if value != self._original[key]}


ALIGNMENT_MAP = {"left": Qt.AlignLeft, "center": Qt.AlignHCenter, "right": Qt.AlignRight}
