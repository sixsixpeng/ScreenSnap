"""热键健康偏好的登记与同步；参数可见性与运行时使用同一纯数据契约。

2026-10-16（用户要求）：原独立导航页「热键健康与恢复」已并入「快捷键」页，本模块只保留可复用的
三块：`add_preferences()` 把偏好登记成设置控件、`sync_preference_rows()` 刷新隐藏行、
`DiagnosticPanel` 只读状态与显式诊断命令。
"""

import json
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
                               QPlainTextEdit, QStyle)
from core.hotkey_policy import PREFERENCES, preference_visible


def add_preferences(page, scope, excluded=()):
    """登记隐藏行也进入controls，重置/导入/导出不丢失偏好。"""
    page.preference_rows = getattr(page, "preference_rows", {})
    last_group = None
    for spec in PREFERENCES:
        if spec.page != scope or spec.key in excluded:
            continue
        if spec.group != last_group:
            page.group(spec.group)
            last_group = spec.group
        row = page.form.rowCount()
        if spec.choices:
            page.radios(spec.key, spec.label, spec.choices, spec.help)
        elif type(spec.default) is bool:
            page.check(spec.key, spec.label, spec.help)
        else:
            page.number(spec.key, spec.label, spec.low, spec.high, spec.help)
        page.preference_rows[spec.key] = (page.form, row)


def sync_preference_rows(page):
    for key, (form, row) in getattr(page, "preference_rows", {}).items():
        form.setRowVisible(row, preference_visible(key, page.config.data))


class DiagnosticPanel(QWidget):
    """只在可见时刷新只读状态；一键命令不作为配置键。"""
    def __init__(self, tools=None, compatibility=False):
        super().__init__()
        self.tools = tools or {}
        self.compatibility = compatibility
        layout = QVBoxLayout(self)
        self.status = QPlainTextEdit()
        self.status.setReadOnly(True)
        self.status.setMaximumHeight(170)
        self.status.setToolTip("绑定完成、线程存活和单个回调不能证明所有热键正常；None表示未知。")
        layout.addWidget(self.status)
        self.message = QLabel()
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        if compatibility:
            commands = (("本次/下一次关闭兜底", "compare_off"), ("恢复兼容策略", "compare_restore"))
        else:
            commands = (("检查/保存快照", "check"), ("重新注册", "recover"), ("复制脱敏报告", "copy"),
                        ("导出报告", "export"), ("打开日志目录", "open_logs"),
                        ("开始临时详细诊断", "detail_start"), ("停止详细诊断", "detail_stop"),
                        ("只读核对按键状态", "physical"), ("10秒引导验证", "verify"), ("清除快照", "clear"))
        # 每个命令配一个标准图标：设置窗口的既有契约是“所有按钮都有图标”
        # （tests/editor/test_text 的 test_visible_commands_have_text_and_icons 会逐按钮断言）。
        icons = {
            "check": QStyle.SP_DialogApplyButton, "recover": QStyle.SP_BrowserReload,
            "copy": QStyle.SP_FileDialogDetailedView, "export": QStyle.SP_DialogSaveButton,
            "open_logs": QStyle.SP_DirOpenIcon, "detail_start": QStyle.SP_MediaPlay,
            "detail_stop": QStyle.SP_MediaStop, "physical": QStyle.SP_ComputerIcon,
            "verify": QStyle.SP_DialogApplyButton, "clear": QStyle.SP_DialogDiscardButton,
            "compare_off": QStyle.SP_DialogCancelButton, "compare_restore": QStyle.SP_BrowserReload,
        }
        for index, (label, name) in enumerate(commands):
            if index % 3 == 0:
                row = QHBoxLayout()
                layout.addLayout(row)
            button = QPushButton(label)
            icon = icons.get(name)
            if icon is not None:
                button.setIcon(self.style().standardIcon(icon))
            button.setToolTip("显式诊断命令；不合成按键。「重新注册」会重建绑定并清理记录里「系统已未按下」的残留按键，不重启原生钩子。引导验证期间不执行截图/贴图动作。")
            button.setEnabled(name in self.tools)
            button.clicked.connect(lambda checked=False, command=name: self.run(command))
            row.addWidget(button)
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.refresh_status)
        self.refresh_status()

    def run(self, name):
        try:
            result = self.tools[name]()
            self.message.setText("操作请求已提交" if result is None else result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, default=str))
        except (OSError, RuntimeError, ValueError) as error:
            self.message.setText(f"操作未完成：{error}")
        self.refresh_status()

    def refresh_status(self):
        name = "compatibility" if self.compatibility else "snapshot"
        if name not in self.tools:
            text = "诊断工具未连接；运行中的应用会注入实时状态。"
        else:
            try:
                text = json.dumps(self.tools[name](), ensure_ascii=False, indent=2, default=str)
            except (RuntimeError, ValueError):
                text = "状态暂不可用"
        if self.status.toPlainText() != text:
            self.status.setPlainText(text)

    def showEvent(self, event):
        super().showEvent(event)
        self.timer.start()

    def hideEvent(self, event):
        self.timer.stop()
        super().hideEvent(event)
