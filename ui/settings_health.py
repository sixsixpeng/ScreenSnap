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
        self.status.setMaximumHeight(84)
        self.status.setToolTip("三行只读状态：注册/监听、输入时间与回调计数、当前判定。"
                               "原始 54 键快照只在故障日志里（搜索「热键故障快照:」）。")
        layout.addWidget(self.status)
        self.message = QLabel()
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        if compatibility:
            commands = (("本次/下一次关闭兜底", "compare_off"), ("恢复兼容策略", "compare_restore"))
        else:
            # 2026-10-16：导出/复制报告、检查/保存快照、只读核对、清除快照、开始/停止详细诊断
            # 全部删除 —— 诊断信息一律写日志；这里只留"救急 + 看日志 + 复现"三个入口。
            commands = (("重新注册", "recover"), ("打开日志目录", "open_logs"),
                        ("复现诊断 10 秒", "verify"))
        # 每个命令配一个标准图标：设置窗口的既有契约是“所有按钮都有图标”
        # （tests/editor/test_text 的 test_visible_commands_have_text_and_icons 会逐按钮断言）。
        icons = {
            "recover": QStyle.SP_BrowserReload, "open_logs": QStyle.SP_DirOpenIcon,
            "verify": QStyle.SP_MediaPlay,
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
        self.timer.setInterval(2000)
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
                state = self.tools[name]()
            except (RuntimeError, ValueError):
                text = "状态暂不可用"
            else:
                text = (self._compatibility_summary(state) if self.compatibility
                        else self._hotkey_summary(state))
        if self.status.toPlainText() != text:
            self.status.setPlainText(text)

    @staticmethod
    def _age_text(value):
        return "无记录" if value is None else f"{value:.1f} 秒前"

    @classmethod
    def _hotkey_summary(cls, state):
        """54 键原始快照 → 三行可读状态（原始 JSON 只写故障日志，不再往界面上刷）。"""
        handles = state.get("handles") or 0
        listening = {True: "监听正常", False: "监听未启动"}.get(state.get("listening"), "监听未知")
        pressed = state.get("local_pressed_count")
        pressed_text = "按键记录未知" if pressed is None else f"按键记录 {pressed} 个"
        reason = state.get("recovery_reason") or ""
        fault = reason.split(":")[0] if reason else "无"
        paused = " · 已暂停（录制中）" if state.get("paused") else ""
        line1 = (f"热键：{'已注册 %d 项' % handles if handles else '未注册'} · {listening}"
                 f" · {pressed_text} · 最近故障：{fault}{paused}")

        line2 = (f"输入：键盘 {cls._age_text(state.get('keyboard_age'))}"
                 f" · 鼠标 {cls._age_text(state.get('mouse_age'))}"
                 f" · 回调 {state.get('callbacks') or 0} 次（Qt 投递 {state.get('qt_callbacks') or 0} 次）")

        evidence = state.get("evidence") or {}
        if not (state.get("handles") or state.get("requested")):
            # 还没注册过：没有线程可谈"可用/不可用"，不要写成故障。
            threads = "尚未注册"
        else:
            threads = {True: "线程不可用", False: "线程正常"}.get(
                evidence.get("threads_unavailable"), "线程未知")
        unfired = evidence.get("unfired_action")
        phantom = evidence.get("phantom_pressed")
        if phantom is None:
            phantom_text = "按键核对未知"
        else:
            phantom_text = f"卡住按键 {phantom} 个" if phantom else "无卡住按键"
        stale = " · 输入链停滞" if evidence.get("input_stale") else ""
        line3 = (f"判定：{threads} · {f'未触发：{unfired}' if unfired else '无未触发动作'}"
                 f" · {phantom_text}{stale}")
        return "\n".join((line1, line2, line3))

    _FALLBACK_TEXT = {"enabled": "启用", "environment-disabled": "环境变量关闭",
                      "session-comparison-disabled": "本次对照关闭",
                      "preference-disabled": "设置关闭", "action-disabled": "功能关闭",
                      "conflict-with-global-binding": "与全局热键冲突",
                      "unsupported-or-offscreen": "不适用（离屏）",
                      "installation-failed": "安装失败"}

    @classmethod
    def _compatibility_summary(cls, state):
        pairs = (("toolbar", "工具栏隐藏键"), ("quick_sticker", "快速贴图键"), ("escape", "Escape"))
        line1 = "兜底：" + " · ".join(
            f"{label}={cls._FALLBACK_TEXT.get(state.get(key), '未知')}" for key, label in pairs)
        if state.get("capture_active"):
            escape = "已装" if state.get("escape_installed") else "未装"
            line2 = (f"截图：进行中 · 临时句柄 {state.get('temporary_handles') or 0} 个"
                     f" · Escape 兜底{escape}")
        else:
            line2 = "截图：未进行"
        return "\n".join((line1, line2))

    def showEvent(self, event):
        super().showEvent(event)
        self.timer.start()

    def hideEvent(self, event):
        self.timer.stop()
        super().hideEvent(event)
