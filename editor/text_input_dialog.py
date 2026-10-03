"""文字标注输入对话框：输入框上方带一排文字样式设置，默认套用当前配置并可同步回配置。"""

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFontComboBox,
                               QFormLayout, QGroupBox, QHBoxLayout, QLabel, QPlainTextEdit,
                               QSpinBox, QVBoxLayout, QWidget)

from editor.annotation_items import auto_text_height, auto_text_width
from ui.widgets.color_button import ColorButton

# 对话框可直接调整并回写到配置的文字相关设置项。
TEXT_SETTING_KEYS = ("font", "font_size", "text_alignment", "text_color",
                     "text_bold", "text_italic", "text_underline", "text_strikethrough",
                     "text_background_enabled", "text_background", "text_width",
                     "text_height")

# 配置缺失时的兜底值，与 config.DEFAULTS 保持一致。
TEXT_SETTING_FALLBACKS = {
    "font": "", "font_size": 18, "text_alignment": "left", "text_color": "#ff0000",
    "text_bold": False, "text_italic": False, "text_underline": False,
    "text_strikethrough": False, "text_background_enabled": False,
    "text_background": "#fff3a0", "text_width": 0, "text_height": 0,
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
    """输入文字的同时调整样式。

    新建时默认取公共配置，改动会同步回公共配置；编辑已有标注时由调用方传入该标注
    当前的样式（`values`），此处的改动只作用于这一个标注，不回写公共配置。
    """

    def __init__(self, parent, title, settings, initial_text="", label="内容", values=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(460)
        # 每标注编辑：以该标注自身的当前样式为初值，缺失项再回落到公共配置。
        self.per_annotation = values is not None
        source = settings if values is None else {**settings, **values}
        self._original = {key: source.get(key, TEXT_SETTING_FALLBACKS[key])
                          for key in TEXT_SETTING_KEYS}
        self._values = dict(self._original)
        layout = QVBoxLayout(self)
        layout.addWidget(self._build_style_group())
        layout.addWidget(QLabel(label))
        self.editor = QPlainTextEdit(initial_text)
        self.editor.setMinimumHeight(120)
        layout.addWidget(self.editor)
        # 实时预览：直接使用编辑框里正在输入的文字，而不是固定示例文案。
        from ui.widgets.annotation_preview import AnnotationPreview
        self.preview = AnnotationPreview(self._values, "text", 108,
                                         sample_text=self.editor.toPlainText())
        preview_box = QWidget()
        preview_layout = QVBoxLayout(preview_box)
        preview_layout.setContentsMargins(0, 0, 0, 0)
        preview_layout.setSpacing(2)
        preview_layout.addWidget(QLabel("预览"))
        preview_layout.addWidget(self.preview)
        layout.addWidget(preview_box)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.editor.textChanged.connect(self._on_text_changed)
        self._refresh_size_hints()
        self.editor.setFocus()

    def _on_text_changed(self):
        """输入变化时同步预览文字与实际宽高提示。"""
        preview = getattr(self, "preview", None)
        if preview is not None:
            preview.sample_text = self.editor.toPlainText()
            preview.refresh()
        self._refresh_size_hints()

    def _refresh_size_hints(self):
        """显示当前生效的文本框宽/高：自动时给出按内容实测值，便于判断大小。"""
        text = self.editor.toPlainText() if hasattr(self, "editor") else ""
        font = QFont(self._values.get("font") or "Microsoft YaHei",
                     int(self._values.get("font_size", 18)))
        alignment = ALIGNMENT_MAP.get(self._values.get("text_alignment"), Qt.AlignLeft)
        width = int(self._values.get("text_width", 0) or 0)
        hint = getattr(self, "width_hint", None)
        if hint is not None:
            hint.setText(f"固定 {width} px" if width > 0
                         else f"自动 ≈ {round(auto_text_width(text or ' ', font))} px")
        height = int(self._values.get("text_height", 0) or 0)
        hint = getattr(self, "height_hint", None)
        if hint is not None:
            hint.setText(
                f"固定 {height} px" if height > 0
                else f"自动 ≈ {round(auto_text_height(text or ' ', self._values, alignment))} px")

    def _build_style_group(self):
        group = QGroupBox("文字样式（本标注）" if self.per_annotation
                          else "文字样式（默认套用当前设置，改动会同步保存）")
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

        self.text_width = QSpinBox()
        self.text_width.setRange(0, 2000)
        self.text_width.setValue(int(self._values["text_width"]))
        self.text_width.setSuffix(" px")
        self.text_width.setSpecialValueText("自动")
        self.text_width.setToolTip("文本框宽度；超出后自动换行，0 表示按内容自动")
        self.text_width.valueChanged.connect(lambda value: self._set("text_width", value))
        self.width_hint = QLabel()
        self.width_hint.setMinimumWidth(96)
        self.width_hint.setToolTip("自动时按当前文字与字体实测的宽度；可在左侧改为固定宽度")
        width_row = QHBoxLayout()
        width_row.addWidget(QLabel("文本框宽度"))
        width_row.addWidget(self.text_width)
        width_row.addWidget(self.width_hint)
        width_row.addStretch()
        form.addRow("宽度", _row(width_row))

        self.text_height = QSpinBox()
        self.text_height.setRange(0, 2000)
        self.text_height.setValue(int(self._values["text_height"]))
        self.text_height.setSuffix(" px")
        self.text_height.setSpecialValueText("自动")
        self.text_height.setToolTip("文本框高度；超出部分裁剪，0 表示按内容自动增长")
        self.text_height.valueChanged.connect(lambda value: self._set("text_height", value))
        self.height_hint = QLabel()
        self.height_hint.setMinimumWidth(96)
        self.height_hint.setToolTip("自动时按当前文字、字体与宽度实测的高度；可在左侧改为固定高度")
        height_row = QHBoxLayout()
        height_row.addWidget(QLabel("文本框高度"))
        height_row.addWidget(self.text_height)
        height_row.addWidget(self.height_hint)
        height_row.addStretch()
        form.addRow("高度", _row(height_row))
        return group

    def _set(self, key, value):
        self._values[key] = value
        preview = getattr(self, "preview", None)
        if preview is not None:
            preview.refresh()
        self._refresh_size_hints()

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
