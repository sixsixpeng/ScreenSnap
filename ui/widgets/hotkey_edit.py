"""使用 Qt 自带的组合键录制控件编辑全局热键。"""

from PySide6.QtCore import Signal
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import QKeySequenceEdit


class HotkeyEdit(QKeySequenceEdit):
    recording = Signal(bool)

    def __init__(self, value, changed):
        super().__init__()
        self.changed = changed
        self.setKeySequence(QKeySequence(value))
        self.setMaximumSequenceLength(1)
        # 清除由设置页的“图标 + 文字”按钮执行，避免 Qt 内建的纯图标按钮。
        self.setClearButtonEnabled(False)
        self.setToolTip("点击后按下组合键；录制期间暂时停用全局热键")
        self.editingFinished.connect(self.commit)

    def focusInEvent(self, event):
        # 暂停全局监听，录制已占用的 F1 等按键时才不会同时触发截图。
        self.recording.emit(True)
        super().focusInEvent(event)

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        self.recording.emit(False)

    def commit(self):
        # PortableText 使用英文键名，转换为 keyboard 库的注册格式。
        text = self.keySequence().toString(QKeySequence.PortableText)
        aliases = {"meta": "windows", "return": "enter", "del": "delete"}
        parts = [aliases.get(part.lower(), part.lower()) for part in text.split("+") if part]
        self.changed("+".join(parts))