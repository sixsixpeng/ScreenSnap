"""不透明度滑块：统一设置页、两个编辑器和标注对话框的外观。"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSlider, QWidget


class OpacitySlider(QWidget):
    """QSlider + 百分比数值标签。

    颜色全部来自当前控件调色板，不写死深/浅色；切换主题时标签、滑轨随系统/Qt 样式重绘。
    """

    valueChanged = Signal(int)

    def __init__(self, value=100, minimum=0, maximum=100, suffix="%", parent=None):
        super().__init__(parent)
        self._suffix = suffix
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self.slider = QSlider(Qt.Horizontal, self)
        self.slider.setRange(int(minimum), int(maximum))
        self.slider.setValue(int(value))
        self.slider.setMinimumWidth(150)
        self.label = QLabel(f"{self.slider.value()}{suffix}", self)
        self.label.setMinimumWidth(44)
        self.label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        layout.addWidget(self.slider, 1)
        layout.addWidget(self.label)
        self.slider.valueChanged.connect(self._on_value_changed)

    def _on_value_changed(self, value):
        self.label.setText(f"{value}{self._suffix}")
        self.valueChanged.emit(value)

    def value(self):
        return self.slider.value()

    def setValue(self, value):
        self.slider.setValue(int(value))

    def setRange(self, minimum, maximum):
        self.slider.setRange(int(minimum), int(maximum))

    def setSuffix(self, suffix):
        self._suffix = suffix
        self.label.setText(f"{self.value()}{suffix}")
