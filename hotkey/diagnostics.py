"""Qt线程上的元数据诊断工具；不采集输入内容、不合成键、不重置钩子。"""

from collections import deque
import builtins
import json
import logging
import os
import time

from PySide6.QtCore import QTimer

from logger import get_logger


def physical_pressed_summary(backend):
    if os.name != "nt":
        return {"available": False, "reason": "unsupported-platform"}
    lock = getattr(backend, "_pressed_events_lock", None)
    if lock is None or not lock.acquire(blocking=False):
        return {"available": False, "reason": "backend-busy-or-unavailable"}
    try:
        scan_codes = tuple(getattr(backend, "_pressed_events", ()))
    finally:
        lock.release()
    try:
        import ctypes
        user32 = ctypes.windll.user32
        checked = down = unknown = 0
        for code in scan_codes:
            virtual = user32.MapVirtualKeyW(int(code), 3)
            if not virtual:
                unknown += 1
                continue
            checked += 1
            down += bool(user32.GetAsyncKeyState(virtual) & 0x8000)
        return dict(available=True, backend_count=len(scan_codes), checked=checked,
                    physically_down=down, stale_candidates=checked - down, unmappable=unknown)
    except Exception as error:  # noqa: BLE001 只保留类型，不把异常中的路径/内容写进报告
        return {"available": False, "reason": type(error).__name__}


class DiagnosticsMixin:
    def _init_diagnostics(self):
        self._snapshot_history = deque(maxlen=20)
        self._detail_until = 0.0
        self._verification_until = 0.0
        self._verification_baseline = (0, 0, 0)
        self._verification_result = None
        self._physical_result = None
        self._registration_result = None
        self._last_health_evidence = None
        self._summary_timer = QTimer(self)
        self._summary_timer.setInterval(600_000)
        self._summary_timer.timeout.connect(lambda: self.log_diagnostics("heartbeat"))
        self._summary_timer.start()
        self._detail_timer = QTimer(self)
        self._detail_timer.setSingleShot(True)
        self._detail_timer.timeout.connect(self.stop_detail)
        self._verification_timer = QTimer(self)
        self._verification_timer.setSingleShot(True)
        self._verification_timer.timeout.connect(self.finish_verification)

    def _apply_diagnostic_policy(self):
        p = self.policy
        self._snapshot_history = deque(self._snapshot_history, maxlen=p["hotkey_snapshot_limit"])
        interval = p["hotkey_summary_interval"] * 1000
        if self._summary_timer.interval() != interval:
            self._summary_timer.setInterval(interval)
        if p["hotkey_summary_enabled"]:
            if not self._summary_timer.isActive():
                self._summary_timer.start()
        else:
            self._summary_timer.stop()

    def _diagnostic_write(self, level, message, *args):
        logger = get_logger()
        mode = self.policy["hotkey_diagnostic_level"]
        detailed = mode == "detailed" or time.monotonic() < self._detail_until
        if logger.disabled or mode == "off" and not detailed or mode == "errors" and level < logging.WARNING and not detailed:
            return
        extra = {"screensnap_metadata_only": True}
        if detailed and level == logging.DEBUG:
            # 只覆盖显式诊断行的等级，不改变整个应用logger或保存的log_level。
            record = logger.makeRecord(logger.name, level, __file__, 0, message, args, None, extra=extra)
            logger.handle(record)
        elif level >= logging.INFO:
            logger.log(level, message, *args, extra=extra)
        elif detailed or logger.isEnabledFor(logging.DEBUG):
            logger.debug(message, *args, extra=extra)

    def start_detail(self):
        self._detail_until = time.monotonic() + self.policy["hotkey_detail_minutes"] * 60
        self._detail_timer.start(self.policy["hotkey_detail_minutes"] * 60_000)
        self.log_diagnostics("temporary-detail-start")
        return "临时详细诊断已开始；到期自动恢复当前保存策略"

    def stop_detail(self):
        self._detail_until = 0.0
        self._detail_timer.stop()
        self.log_diagnostics("temporary-detail-stop")
        return "临时详细诊断已停止"

    def capture_snapshot(self, reason="manual", retain=True):
        state = self.diagnostic_snapshot()
        state.update(self.recovery.snapshot(time.monotonic()))
        state.update(reason=reason, registration_state=self._registration_state,
                     detail_remaining=round(max(0.0, self._detail_until - time.monotonic()), 1),
                     verification_active=time.monotonic() < self._verification_until,
                     verification_result=self._verification_result,
                     physical_check=self._physical_result,
                     install_result=self._registration_result,
                      evidence=self._last_health_evidence,
                     effective_recovery=self.policy["recovery_effective"],
                     session_available=self._session_available)
        # 只保存基础类型，拒绝私有backend对象或任意异常文本进入共享报告。
        clean = {}
        for key, value in state.items():
            if isinstance(value, (bool, int, float)) or value is None:
                clean[key] = value
            elif key in ("reason", "registration_state", "recovery_state", "recovery_reason"):
                clean[key] = str(value)
            elif key in ("affected_actions", "verified_actions"):
                clean[key] = [action for action in value if action in self._hotkey_keys]
            elif key in ("observer_error", "diagnostic_error"):
                error = getattr(builtins, value, None) if isinstance(value, str) else None
                clean[key] = value if isinstance(error, type) and issubclass(error, Exception) else "unknown-error-type"
            elif key in ("verification_result", "physical_check", "install_result", "evidence") and isinstance(value, dict):
                clean[key] = {k: v for k, v in value.items() if type(v) in (bool, int, float) or v is None}
                if key == "physical_check":
                    reason = value.get("reason")
                    error = getattr(builtins, reason, None) if isinstance(reason, str) else None
                    if reason in ("unsupported-platform", "backend-busy-or-unavailable") or isinstance(error, type) and issubclass(error, Exception):
                        clean[key]["reason"] = reason
                elif key == "install_result":
                    if value.get("state") in ("completed", "partial", "failed", "disabled", "cancelled"):
                        clean[key]["state"] = value["state"]
                    clean[key]["failed_actions"] = [action for action in value.get("failed_actions", []) if action in self._hotkey_keys]
                elif key == "evidence" and value.get("unfired_action") in self._hotkey_keys:
                    clean[key]["unfired_action"] = value["unfired_action"]
        if retain:
            self._snapshot_history.append(clean)
        return clean

    def diagnostic_report(self):
        import keyboard
        return json.dumps(dict(format="ScreenSnap-hotkey-metadata-v1",
                               backend_version=str(getattr(keyboard, "version", "unknown")),
                               current=self.capture_snapshot("report", retain=False), history=list(self._snapshot_history)),
                          ensure_ascii=False, indent=2)

    def inspect_health(self):
        self._last_health_evidence = self.health_evidence()
        self.log_diagnostics("manual-check")
        return self.capture_snapshot("manual-check")

    def check_physical_state(self):
        from hotkey.hotkey_manager import keyboard
        self._physical_result = physical_pressed_summary(keyboard)
        self.log_diagnostics("physical-state-check")
        return self._physical_result

    def begin_verification(self):
        self._verification_baseline = (self._keyboard_events, self._callback_events, self._qt_callbacks)
        self._verification_until = time.monotonic() + 10.0
        self._verification_result = None
        self._verification_timer.start(10_000)
        self.log_diagnostics("guided-verification-start")
        return "10秒内尝试当前快捷键：本次只验证输入链，不执行截图/贴图动作"

    def finish_verification(self):
        before = self._verification_baseline
        self._verification_until = 0.0
        self._verification_timer.stop()
        self._verification_result = dict(keyboard_events=self._keyboard_events - before[0],
                                         callbacks=self._callback_events - before[1],
                                         qt_callbacks=self._qt_callbacks - before[2])
        self.log_diagnostics("guided-verification-finished")
        return self._verification_result

    def clear_diagnostic_history(self):
        self._snapshot_history.clear()
        self._physical_result = self._verification_result = None
        self._last_health_evidence = None
        return "已清除内存诊断历史；未改底层按键状态或恢复策略计数"
