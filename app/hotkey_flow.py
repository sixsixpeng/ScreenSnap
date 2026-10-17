"""常驻应用拥有诊断工具，设置页仅接受动作注入。

本 mixin 只“收集状态 / 转发动作”，不直接读 HotkeyManager 内部字段。
诊断信息一律写日志（2026-10-16：导出/复制报告已删除）。日志只记录动作本身，不写路径、键名或输入内容。
"""

import os

from shiboken6 import isValid
from logger import get_logger
from logger.log_setup import log_directory


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
        return dict(snapshot=lambda: self.hotkeys.capture_snapshot("panel"),
                    recover=self.restore_hotkeys, open_logs=self.open_hotkey_logs,
                    verify=self.hotkeys.reproduce_diagnostics,
                    compatibility=self.capture_compatibility_status,
                    compare_off=lambda: self.set_capture_comparison(True),
                    compare_restore=lambda: self.set_capture_comparison(False))

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
