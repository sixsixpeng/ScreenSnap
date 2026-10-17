"""全局热键录制与冲突检查，以及热键健康、自动恢复与诊断。

2026-10-16（用户要求）：原独立导航页「热键健康与恢复」并入本页 —— 两者都只服务热键这一件事，
拆成两个入口后用户要来回切换；现在「快捷键」页自上而下是：常驻热键绑定 → 健康/自动恢复偏好
→ 有效策略与诊断面板。
"""

from PySide6.QtWidgets import (QLabel, QMessageBox, QWidget, QHBoxLayout, QPushButton, QStyle)

from core.constants import HOTKEY_LABELS
from core.hotkey_policy import THRESHOLD_KEYS, effective_policy
from config.config_manager import (CAPTURE_SHORTCUT_KEYS, DEFAULTS, canonical_hotkey, validate)
from ui.settings_health import DiagnosticPanel, add_preferences, sync_preference_rows
from ui.widgets.hotkey_edit import HotkeyEdit
from ui.widgets.tooltip import SettingsPage

SCREENSHOT_HOTKEY_ACTIONS = CAPTURE_SHORTCUT_KEYS


class HotkeyPage(SettingsPage):
    """通过 Qt 录制组合键并在写入配置前检查动作冲突；同时承载热键健康与恢复。"""

    def __init__(self, config, changed, recording=None, restore_hotkeys=None, tools=None):
        super().__init__(config, changed)
        self.group("全局热键")
        self.check("hotkeys_enabled", "启用全局热键",
                   "关闭常驻全局动作绑定；截图临时兜底由截图页独立控制。\n"
                   "体检与自动恢复策略见本页下方的「状态与体检」「自动恢复」等分组。")
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
        # 两个编辑器共用的「另存为」（2026-10-11 用户要求：单键即可）。
        self.text("editor_save_as_shortcut", "另存为（两个编辑器）",
                  "独立编辑器与原地编辑器共用的另存为按键；保存与仅复制沿用截图快捷键里的那两项。\n"
                  "取值：任意单键或组合键，默认单键 A（例如改成 D 或 Ctrl+Shift+S）。")
        for key, label, tip in (
                ("editor_undo_shortcut", "撤销（两个编辑器）",
                 "独立编辑器与原地编辑器共用的撤销按键；默认遵循 Windows 通用约定 Ctrl+Z。"),
                ("editor_redo_shortcut", "重做（两个编辑器）",
                 "两个编辑器共用的重做按键，默认 Ctrl+Y。"),
                ("editor_zoom_reset_shortcut", "画布缩放复位（仅独立编辑器）",
                 # 2026-10-11（审查 M1）：label 写"仅独立编辑器"、tooltip 却写"两边都可用" ✗ 自相矛盾
                 # ⇒ 统一为"仅独立编辑器"（原地编辑器没有画布缩放，Ctrl+0 不生效是有意设计 ✓ 规则 33）。
                 "把画布缩放复位到 100%，默认 Ctrl+0；仅独立编辑器可用（原地编辑器没有画布缩放）。")):
            self.text(key, label, tip)

        # 原「热键健康与恢复」页内容（状态与体检 / 自动恢复 / 失败与恢复提示 / 高级判定）。
        add_preferences(self, "health")
        self.group("有效策略与诊断")
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        self.summary.setToolTip("预设和只记录模式仅覆盖运行时有效值；切回自定义保留原值。")
        self.form.addRow(self.summary)
        self.validation_message = QLabel()
        self.validation_message.setWordWrap(True)
        self.form.addRow(self.validation_message)
        tools = dict(tools or {})
        if restore_hotkeys is not None:
            # 手动兜底入口：热键被其它程序抢占时不必等到自动判据触发（面板里的「重新注册」同一动作）。
            tools["recover"] = restore_hotkeys
        self.panel = DiagnosticPanel(tools)
        self.form.addRow(self.panel)
        # 2026-10-16（用户要求）：原「截图 > 键盘兼容性」分组与本页的对照面板整体移到本页，
        # 与热键绑定、健康/自动恢复放在一起（工具栏隐藏键 / 快速贴图键 / Escape 的临时全局兜底）。
        add_preferences(self, "compatibility")
        self.compatibility_panel = DiagnosticPanel(tools, compatibility=True)
        self.form.addRow(self.compatibility_panel)
        self.sync_visibility()
        self.sync_controls()
        self.refresh_previews()

    def update_value(self, key, value):
        """健康偏好之间有联动约束：先整体校验，非法值只报错、不写配置。"""
        message = getattr(self, "validation_message", None)
        candidate = dict(self.config.data, **{key: value})
        try:
            validate(candidate)
        except ValueError as error:
            if message is not None:
                message.setText(str(error))
            self.sync_controls()
            return
        if message is not None:
            message.clear()
        super().update_value(key, value)
        self.sync_visibility()

    def sync_visibility(self):
        """按当前偏好刷新隐藏行、有效策略摘要与状态面板可见性。"""
        sync_preference_rows(self)
        if not hasattr(self, "summary"):
            return
        policy = effective_policy(self.config.data)
        detail = "；".join(f"{key}={policy[key]}" for key in THRESHOLD_KEYS)
        self.summary.setText(
            f"有效自动恢复={'开启' if policy['recovery_effective'] else '关闭'}；{detail}\n"
            "重建绑定不等于重启原生钩子；未按键保持尚未验证。")
        self.panel.status.setVisible(self.config.data["hotkey_status_visible"])

    def sync_controls(self):
        super().sync_controls()
        if hasattr(self, "panel"):
            self.sync_visibility()

    def reset_page(self):
        """本页恢复默认：开关、全部热键绑定与健康偏好一起回到出厂值。"""
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
