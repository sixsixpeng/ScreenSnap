"""全局热键录制与冲突检查。"""

from PySide6.QtWidgets import QMessageBox, QWidget, QHBoxLayout, QPushButton, QStyle

from core.constants import HOTKEY_LABELS
from config.config_manager import DEFAULTS, canonical_hotkey
from ui.widgets.hotkey_edit import HotkeyEdit
from ui.widgets.tooltip import SettingsPage


class HotkeyPage(SettingsPage):
    """通过 Qt 录制组合键并在写入配置前检查动作冲突。"""

    def __init__(self, config, changed, recording=None):
        super().__init__(config, changed)
        self.group("全局热键")
        self.check("hotkeys_enabled", "启用全局热键", "关闭后所有全局快捷键暂停注册")
        self.group("截图快捷键")
        self.check("capture_hotkey_suppress", "拦截截图快捷键输入",
               "启用后，截图热键按下/释放会由系统钩子拦截，不传给当前前台应用；释放按键后解除拦截。某些系统权限策略可能限制此能力")
        for action, label in HOTKEY_LABELS.items():
            if action == "edit_clipboard":
                self.group("编辑快捷键")
            elif action == "paste":
                self.group("贴图快捷键")
            widget = HotkeyEdit(config.data["hotkeys"][action],
                                lambda binding, name=action: self.update_binding(name, binding))
            widget.setToolTip(f"点击录制 {label} 的快捷键；录制时暂停全局热键")
            if recording is not None:
                widget.recording.connect(recording)
            row = QWidget()
            layout = QHBoxLayout(row)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.addWidget(widget)
            clear = QPushButton("清除")
            clear.setIcon(self.style().standardIcon(QStyle.SP_DialogCancelButton))
            clear.setToolTip(f"清除 {label} 的热键绑定")
            clear.clicked.connect(lambda checked=False, name=action: self.clear_binding(name))
            layout.addWidget(clear)
            self.form.addRow(label, row)
            setattr(self, f"edit_{action}", widget)

    def reset_page(self):
        """本页恢复默认：开关与全部热键绑定一起回到出厂值。"""
        super().reset_page()
        for action, binding in DEFAULTS["hotkeys"].items():
            self.config.data["hotkeys"][action] = binding
            getattr(self, f"edit_{action}").setKeySequence(binding)
        self.changed()

    def clear_binding(self, action):
        """清除绑定后同步录制控件和 JSON，保留该动作在设置页。"""
        self.update_binding(action, "")
        getattr(self, f"edit_{action}").clear()

    def update_binding(self, action, binding):
        """重复组合键恢复原显示值，合法组合键立即保存并通知重新注册。"""
        bindings = self.config.data["hotkeys"]
        if binding and canonical_hotkey(binding) in (
            canonical_hotkey(value) for key, value in bindings.items() if key != action and value):
            QMessageBox.warning(self, "快捷键冲突", f"{binding} 已被其他操作占用")
            getattr(self, f"edit_{action}").setKeySequence(bindings[action])
            return
        bindings[action] = binding
        self.changed()