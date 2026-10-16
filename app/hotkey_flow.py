"""常驻应用拥有诊断工具，设置页仅接受动作注入。

本 mixin 只“收集状态 / 转发动作”，不直接读 HotkeyManager 内部字段：报告与导出都
走 `self.hotkeys` 的只读接口。日志只记录动作本身，不写路径、键名或输入内容。
"""

import json
import os
from pathlib import Path
import sys

from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox
from shiboken6 import isValid
from logger import get_logger
from logger.log_setup import log_directory
from logger.diagnostic_report import export_report


class HotkeyControlMixin:
    def hotkey_busy(self):
        if getattr(self, "_capture_pending", 0):
            return True
        windows = [getattr(self, "mask", None), *getattr(self, "editors", ())]
        for window in windows:
            try:
                if window is not None and isValid(window) and window.isVisible():
                    return True
            except (RuntimeError, TypeError):
                continue
        return False

    def hotkey_tools(self):
        return dict(snapshot=lambda: self.hotkeys.capture_snapshot("panel", retain=False),
                    check=self.hotkeys.inspect_health, recover=self.restore_hotkeys,
                    copy=self.copy_hotkey_report, export=self.export_hotkey_report,
                    open_logs=self.open_hotkey_logs, detail_start=self.hotkeys.start_detail,
                    detail_stop=self.hotkeys.stop_detail, physical=self.hotkeys.check_physical_state,
                    verify=self.hotkeys.begin_verification, clear=self.hotkeys.clear_diagnostic_history,
                    compatibility=self.capture_compatibility_status,
                    compare_off=lambda: self.set_capture_comparison(True),
                    compare_restore=lambda: self.set_capture_comparison(False))

    def hotkey_report(self):
        report = json.loads(self.hotkeys.diagnostic_report())
        states = self.capture_compatibility_status()
        allowed = {"environment-disabled", "session-comparison-disabled", "preference-disabled",
                   "action-disabled", "conflict-with-global-binding", "unsupported-or-offscreen",
                   "enabled", "installation-failed"}
        compatibility: dict[str, str | bool | int] = {
            key: states[key] for key in ("toolbar", "quick_sticker", "escape")
            if type(states.get(key)) is str and states[key] in allowed}
        for key in ("capture_active", "escape_installed"):
            if type(states.get(key)) is bool:
                compatibility[key] = states[key]
        count = states.get("temporary_handles")
        if type(count) is int and 0 <= count <= 2:
            compatibility["temporary_handles"] = count
        report["capture_compatibility"] = compatibility
        return json.dumps(report, ensure_ascii=False, indent=2)

    def copy_hotkey_report(self):
        from editor.image_effects import clipboard_available
        if not clipboard_available():
            return "剪贴板被占用，未复制报告"
        try:
            QApplication.clipboard().setText(self.hotkey_report())
        except (RuntimeError, OSError):
            return "报告复制失败，原剪贴板未作进一步操作"
        self.hotkeys.log_diagnostics("report-copy-requested")
        return "已请求写入脱敏报告（Qt不提供实际粘贴成功回执）"

    def export_hotkey_report(self):
        include = bool(self.config.data["hotkey_report_include_logs"])
        parent = self.settings_window
        if include and QMessageBox.question(parent, "附带原始日志",
                "原始日志可能包含窗口标题、路径、命令行及其他信息，附件不保证脱敏。是否继续？",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return "已取消原始日志导出"
        filename = "ScreenSnap-hotkey-report.zip" if include else "ScreenSnap-hotkey-report.json"
        filters = "ZIP (*.zip)" if include else "JSON (*.json)"
        path, _ = QFileDialog.getSaveFileName(parent, "导出热键诊断报告", filename, filters)
        if not path:
            return "已取消导出"
        if not Path(path).suffix:
            path += ".zip" if include else ".json"
        startup = Path(sys.executable if getattr(sys, "frozen", False) else sys.argv[0]).resolve().parent
        result = export_report(path, self.hotkey_report(), log_directory(self.config.data),
                               startup, self.config.path, include_logs=include)
        self.hotkeys.log_diagnostics("report-exported")
        return (f"诊断报告已导出；未读日志数量={result['unreadable_logs']}；"
                f"数量超限未附带={result['truncated_logs']}")

    def open_hotkey_logs(self):
        """打开实际日志目录（用户排查热键问题的一步）。只记动作，不记路径。"""
        directory = log_directory(self.config.data)
        directory.mkdir(parents=True, exist_ok=True)
        if os.name == "nt":
            os.startfile(directory)
        get_logger().info("已请求打开日志目录",
                                             extra={"screensnap_metadata_only": True})
        return "已请求打开实际日志目录"

    def capture_compatibility_status(self):
        from screenshot.keyboard_compatibility import compatibility_status
        return compatibility_status(self.config.data, getattr(self, "mask", None),
                                    getattr(self, "_next_capture_comparison", False))

    def set_capture_comparison(self, disabled):
        """临时对照开关：本次截图（或下一次）单独关闭全局兜底，不落盘。

        没有活动遮罩时只对“下一次交互截图”生效，避免把一次性对照写成长期偏好。
        """
        mask = getattr(self, "mask", None)
        if mask is not None and isValid(mask) and mask.isVisible():
            mask.session.compatibility_override = bool(disabled)
            mask.sync_keyboard_compatibility()
            get_logger().info(
                "截图兼容兜底临时对照: %s", "关闭" if disabled else "恢复",
                extra={"screensnap_metadata_only": True})
            return "本次截图全局兜底已临时关闭" if disabled else "本次截图已恢复保存的兼容策略"
        self._next_capture_comparison = bool(disabled)
        get_logger().info(
            "截图兼容兜底对照已排队: %s", "关闭" if disabled else "取消",
            extra={"screensnap_metadata_only": True})
        return "仅下一次交互截图临时关闭全局兜底" if disabled else "已取消下一次截图对照覆盖"
