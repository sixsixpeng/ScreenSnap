"""颜色选择按钮。"""

from PySide6.QtCore import QLibraryInfo, QTranslator
from PySide6.QtWidgets import QApplication, QPushButton, QColorDialog, QLabel
from PySide6.QtGui import QColor, QIcon, QPixmap

COMMON_COLORS = (
    "#ff0000", "#00a000", "#0000ff", "#000000", "#ffffff",
    "#ffff00", "#ff8000", "#800080", "#00ffff", "#808080",
)
_color_translator = None
_common_colors_initialized = False
_FALLBACK_LABELS = {
    "Basic colors": "基本颜色", "Custom colors": "自定义颜色",
    "Hue:": "色相：", "Sat:": "饱和度：", "Val:": "明度：",
    "Red:": "红：", "Green:": "绿：", "Blue:": "蓝：",
    "Alpha channel:": "透明度：", "HTML:": "颜色代码：",
    "Pick Screen Color": "屏幕取色", "Add to Custom Colors": "添加到自定义颜色",
    "OK": "确定", "Cancel": "取消",
}


def color_dialog(initial, parent):
    """固定使用可翻译的 Qt 对话框，并在自定义色板中预置常用色。"""
    global _color_translator, _common_colors_initialized
    app = QApplication.instance()
    if _color_translator is None:
        translator = QTranslator(app)
        path = QLibraryInfo.path(QLibraryInfo.TranslationsPath)
        if translator.load("qtbase_zh_CN", path):
            app.installTranslator(translator)
            _color_translator = translator
    if not _common_colors_initialized:
        for index, value in enumerate(COMMON_COLORS):
            QColorDialog.setCustomColor(index, QColor(value))
        _common_colors_initialized = True
    dialog = QColorDialog(QColor(initial), parent)
    dialog.setOption(QColorDialog.DontUseNativeDialog)
    dialog.setWindowTitle("选择颜色")
    if _color_translator is None:
        for widget in (*dialog.findChildren(QLabel), *dialog.findChildren(QPushButton)):
            text = widget.text().replace("&", "")
            if text in _FALLBACK_LABELS:
                widget.setText(_FALLBACK_LABELS[text])
    return dialog


class ColorButton(QPushButton):
    """打开系统取色对话框，确认选择后再通知设置页。"""

    def __init__(self, color, changed, compact=False, purpose="标注颜色"):
        super().__init__()
        self.color = color
        self.changed = changed
        self.compact = compact
        self.purpose = purpose
        self.update_color()
        self.clicked.connect(self.choose)

    def update_color(self):
        # Qt 没有颜色选择的标准图标；以当前颜色色块作图标便于识别实际值。
        swatch = QPixmap(18, 18)
        swatch.fill(QColor(self.color))
        self.setIcon(QIcon(swatch))
        self.setText("" if self.property("icon_only") else
                 "颜色" if self.compact else f"选择颜色 {self.color}")
        if self.compact:
            self.setToolTip(f"选择{self.purpose}：{self.color}")

    def set_color(self, color):
        self.color = color
        self.update_color()

    def choose(self):
        """只有确认有效颜色后才改变色块并触发设置保存。"""
        dialog = color_dialog(self.color, self)
        if dialog.exec() == QColorDialog.Accepted:
            color = dialog.currentColor()
            self.set_color(color.name())
            self.changed(self.color)