"""Qt线程上的元数据诊断工具；不采集输入内容、不合成键、不重置钩子。"""

import builtins
import json
import logging
import os
import time

from PySide6.QtCore import QTimer

from logger import get_logger

# 「复现诊断」的统计窗口：同时开详细日志与输入链计数，到期自动结束并把结果写进日志。
VERIFICATION_SECONDS = 10.0


def backend_pressed_codes(backend):
    """库侧记录的“当前按下”键标识（扫描码；库把 sc 为 0 的键记为 -vk）。

    非 Windows、锁被钩子占用或库结构不可读时返回 None —— 表示**无法判定**，
    调用方必须按“不下结论”处理，绝不能当成“没有按键”。
    """
    if os.name != "nt":
        return None
    lock = getattr(backend, "_pressed_events_lock", None)
    if lock is None or not lock.acquire(blocking=False):
        return None
    try:
        return tuple(getattr(backend, "_pressed_events", ()))
    finally:
        lock.release()


def _virtual_key(code, user32):
    """键标识 → 虚拟键码；is_extended 归一到通用 VK（左右 Ctrl 都落到 VK_CONTROL），无法映射返回 0。"""
    if code < 0:
        # 库对 scan_code 为 0 的键直接存 -vk（keyboard/_winkeyboard.process_key），可直接当 VK 用。
        return -int(code)
    return user32.MapVirtualKeyW(int(code) & 0xFF, 3)


def physically_up_codes(codes):
    """返回 codes 中“系统按键状态显示未按下”的键标识；无法判定时返回 None。

    用 `GetAsyncKeyState` 而不是合成键或 `GetLastInputInfo`：合成事件不触发库自己的热键回调
    （无法证伪，见 hotkey_manager 文件头），而 GetLastInputInfo 只能说“最近有输入”，
    给不出“哪些键还按着”。无法映射（0）或调用失败的项不下结论。
    """
    if os.name != "nt":
        return None
    try:
        import ctypes
        user32 = ctypes.windll.user32
        stale = []
        for code in codes:
            virtual = _virtual_key(code, user32)
            if not virtual:
                continue
            if not user32.GetAsyncKeyState(virtual) & 0x8000:
                stale.append(code)
        return tuple(stale)
    except Exception:  # noqa: BLE001 判定不可用时不建议任何清理
        return None


def stale_pressed_codes(backend, extra_codes=()):
    """“被记为按下、但系统按键状态显示没按”的键标识；无法判定返回 None。

    2026-10-16（实测故障）：库的热键派发按“当前按下的全部键”的精确集合查表
    （keyboard/__init__.py: hotkey = tuple(sorted(_pressed_events))），只要有一个 KEY_UP
    没被钩子收到（前台切到提权窗口、安全桌面等），残留项就让**所有**热键一起失灵，
    而监听线程、观察器、绑定数量看上去全部正常 ⇒ 只能用系统按键状态证伪。
    `extra_codes` 并入调用方自己维护的按键（库表之外），使两侧都能被判为卡住。
    """
    codes = backend_pressed_codes(backend)
    pool = list(dict.fromkeys([*(codes or ()), *(code for code in extra_codes if code is not None)]))
    if not pool:
        return None if codes is None else ()
    return physically_up_codes(pool)


class DiagnosticsMixin:
    def _init_diagnostics(self):
        self._detail_until = 0.0
        self._verification_until = 0.0
        self._verification_baseline = (0, 0, 0)
        self._verification_result = None
        self._registration_result = None
        self._last_health_evidence = None
        self._summary_timer = QTimer(self)
        self._summary_timer.setInterval(600_000)
        self._summary_timer.timeout.connect(lambda: self.log_diagnostics("heartbeat"))
        self._summary_timer.start()
        self._verification_timer = QTimer(self)
        self._verification_timer.setSingleShot(True)
        self._verification_timer.timeout.connect(self.finish_verification)

    def _apply_diagnostic_policy(self):
        p = self.policy
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

    def reproduce_diagnostics(self):
        """复现诊断：10 秒内开详细日志并统计输入链，到期自动结束、结果写日志。

        2026-10-16（个人开发口径）：导出/复制报告已删除，诊断信息一律落日志，
        不再依赖"点按钮把报告存出去"。期间不执行截图/贴图动作。
        """
        self._detail_until = time.monotonic() + VERIFICATION_SECONDS
        self._verification_baseline = (self._keyboard_events, self._callback_events, self._qt_callbacks)
        self._verification_until = time.monotonic() + VERIFICATION_SECONDS
        self._verification_result = None
        self._verification_timer.start(int(VERIFICATION_SECONDS * 1000))
        self.log_diagnostics("guided-verification-start")
        return "10 秒内尝试当前快捷键：期间开详细诊断，结束自动写日志，不执行截图/贴图"

    def finish_verification(self):
        before = self._verification_baseline
        self._verification_until = 0.0
        self._detail_until = 0.0
        self._verification_timer.stop()
        self._verification_result = dict(keyboard_events=self._keyboard_events - before[0],
                                         callbacks=self._callback_events - before[1],
                                         qt_callbacks=self._qt_callbacks - before[2])
        self.log_diagnostics("guided-verification-finished")
        get_logger().info("复现诊断结果: %s",
                          json.dumps(self._verification_result, ensure_ascii=False),
                          extra={"screensnap_metadata_only": True})
        return self._verification_result

    def capture_snapshot(self, reason="manual"):
        # 只读判定顺带刷新一次：面板每 2 秒取一次快照，这一行让"判定"反映当前状态而不是
        # 只反映上一次故障；成本是每个已记录按键一次 GetAsyncKeyState（通常 0 个）。
        self._last_health_evidence = self.health_evidence()
        state = self.diagnostic_snapshot()
        state.update(self.recovery.snapshot(time.monotonic()))
        state.update(reason=reason, registration_state=self._registration_state,
                     detail_remaining=round(max(0.0, self._detail_until - time.monotonic()), 1),
                     verification_active=time.monotonic() < self._verification_until,
                     verification_result=self._verification_result,
                     install_result=self._registration_result,
                      evidence=self._last_health_evidence,
                     effective_recovery=self.policy["recovery_effective"],
                     session_available=self._session_available)
        # 只保留基础类型，保证结果能直接 json.dumps 写进日志（拒绝私有 backend 对象或异常文本）。
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
            elif key in ("verification_result", "install_result", "evidence") and isinstance(value, dict):
                clean[key] = {k: v for k, v in value.items() if type(v) in (bool, int, float) or v is None}
                if key == "install_result":
                    if value.get("state") in ("completed", "partial", "failed", "disabled", "cancelled"):
                        clean[key]["state"] = value["state"]
                    clean[key]["failed_actions"] = [action for action in value.get("failed_actions", []) if action in self._hotkey_keys]
                elif key == "evidence" and value.get("unfired_action") in self._hotkey_keys:
                    clean[key]["unfired_action"] = value["unfired_action"]
        return clean
