"""策略/诊断/兼容回归；不安装真实钩子，不访问真实用户配置。"""
import copy
import itertools
import json
import os
from pathlib import Path
import queue
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from contextlib import contextmanager
import zipfile

_ROOT = str(Path(__file__).resolve().parents[1])
if _ROOT in sys.path:
    sys.path.remove(_ROOT)
sys.path.insert(0, _ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication, QWidget
from config.config_manager import DEFAULTS, ConfigManager, PRESERVED_ON_VERSION_RESET, validate, repair
from core.hotkey_policy import PREFERENCES, SPECS, effective_policy, preference_visible, THRESHOLD_KEYS
from hotkey import hotkey_manager as hm
from hotkey.recovery import RecoveryState
from ui.settings_window import SettingsWindow
from logger import get_logger
from logger.diagnostic_report import export_report

APP = QApplication.instance() or QApplication([])


def wait_for(predicate):
    deadline = time.perf_counter() + 3
    while not predicate() and time.perf_counter() < deadline:
        time.sleep(0.01)
    if not predicate():
        raise AssertionError("three-second bounded wait expired")


@contextmanager
def manager_context(**overrides):
    fake = SimpleNamespace(_listener=SimpleNamespace(listening=True, queue=queue.Queue(),
        listening_thread=Mock(is_alive=Mock(return_value=True)),
        processing_thread=Mock(is_alive=Mock(return_value=True)),
        active_modifiers=set(), modifier_states={}, is_replaying=False),
        _pressed_events={}, _logically_pressed_keys={},
        hook=Mock(side_effect=lambda cb: cb), unhook=Mock(), remove_hotkey=Mock(),
        add_hotkey=Mock(side_effect=lambda binding, callback, suppress=False: callback))
    settings = copy.deepcopy(DEFAULTS)
    settings.update(hotkeys={"capture": "f1", "paste": "f3"}, hotkey_startup_grace=0,
                    hotkey_failure_notice="off", hotkey_criterion_input=False)
    settings.update(overrides)
    with patch.object(hm, "keyboard", fake), \
            patch.object(hm.HotkeyManager, "_mouse_module", return_value=(None, "isolated")), \
            patch.object(get_logger(), "disabled", False):
        manager = hm.HotkeyManager()
        try:
            manager.register(settings)
            wait_for(lambda: manager._registration_completed == manager._registration_requested)
            APP.processEvents()
            yield manager, fake, settings
        finally:
            manager.stop()
            APP.processEvents()


class PolicyTests(unittest.TestCase):
    def test_every_preference_default_type_range_and_preservation(self):
        for spec in PREFERENCES:
            with self.subTest(key=spec.key):
                self.assertEqual(validate({})[spec.key], spec.default)
                self.assertIn(spec.key, PRESERVED_ON_VERSION_RESET)
                bad = "wrong" if type(spec.default) is not str else 9
                with self.assertRaises(ValueError):
                    validate({spec.key: bad})
                if spec.choices:
                    for _, value in spec.choices:
                        self.assertEqual(validate({spec.key: value})[spec.key], value)
                elif type(spec.default) is int:
                    for value in (spec.low - 1, spec.high + 1, True):
                        with self.assertRaises(ValueError):
                            validate({spec.key: value})

    def test_relations_repair_keeps_valid_custom_pairs_and_other_settings(self):
        values = dict(hotkey_sensitivity="custom", hotkey_recent_input_ms=4000,
                      hotkey_missed_input_ms=7000, hotkey_retry_mode="backoff",
                      hotkey_retry_interval=500, hotkey_retry_max_interval=600, pen_color="#123456")
        fixed, dropped = repair(values)
        self.assertFalse(dropped)
        for key, value in values.items():
            self.assertEqual(fixed[key], value)
        values.update(hotkey_retry_max_interval=30, hotkey_missed_input_ms=1000)
        with self.assertRaises(ValueError):
            validate(values)
        fixed, dropped = repair(values)
        validate(fixed)
        self.assertIn("hotkey_retry_max_interval", dropped)
        self.assertEqual(fixed["pen_color"], "#123456")

    def test_first_run_missing_keys_import_export_and_saved_false_are_idempotent(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "settings.json"
            path.write_text(json.dumps({"hotkey_health_enabled": False, "hotkey_recovery_enabled": False}), encoding="utf-8")
            config = ConfigManager(path)
            for key in SPECS:
                self.assertIn(key, config.data)
            export = Path(folder) / "export.json"
            config.export_to(export)
            original = copy.deepcopy(config.data)
            config.import_from(export)
            self.assertEqual(config.data, original)
            self.assertEqual(ConfigManager(path).data, original)

    def test_presets_and_diagnostic_only_do_not_mutate_saved_preferences(self):
        data = dict(DEFAULTS, hotkey_miss_count=5, hotkey_diagnostic_only=True)
        before = copy.deepcopy(data)
        self.assertFalse(effective_policy(data)["recovery_effective"])
        self.assertEqual(effective_policy(data)["hotkey_miss_count"], 2)
        self.assertEqual(data, before)
        data["hotkey_sensitivity"] = "custom"
        self.assertEqual(effective_policy(data)["hotkey_miss_count"], 5)

    def test_visibility_matrix_and_hidden_value_roundtrip(self):
        with tempfile.TemporaryDirectory() as folder:
            config = ConfigManager(Path(folder) / "settings.json")
            window = SettingsWindow(config)
            try:
                page = window.page("快捷键")
                keys = ("hotkey_health_enabled", "hotkey_recovery_enabled", "hotkey_diagnostic_only",
                        "hotkey_sensitivity", "hotkey_retry_mode", "hotkey_retry_limit", "hotkey_retry_exhausted")
                axes = ((False, True), (False, True), (False, True), ("standard", "custom"),
                        ("fixed", "backoff"), (0, 3), ("stop", "cooldown"))
                for values in itertools.product(*axes):
                    config.data.update(zip(keys, values))
                    page.sync_controls()
                    for key in page.preference_rows:
                        self.assertEqual(not page.controls[key].isHidden(), preference_visible(key, config.data), (key, values))
                config.data.update(hotkey_sensitivity="custom", hotkey_miss_count=4,
                                   hotkey_recovery_enabled=True, hotkey_diagnostic_only=False, hotkey_health_enabled=True)
                page.sync_controls()
                page.controls["hotkey_sensitivity"].set_value("standard")
                page.update_value("hotkey_sensitivity", "standard")
                page.update_value("hotkey_sensitivity", "custom")
                self.assertEqual(config.data["hotkey_miss_count"], 4)
                page.update_value("hotkey_status_visible", False)
                self.assertTrue(page.panel.status.isHidden())
                self.assertFalse(page.panel.isHidden(), "manual tools remain available")
            finally:
                window.close()

    def test_rebuild_page_reset_and_screenshot_recording_injection(self):
        with tempfile.TemporaryDirectory() as folder:
            config = ConfigManager(Path(folder) / "settings.json")
            window = SettingsWindow(config, diagnostic_tools={"snapshot": lambda: {"safe": True}})
            events = []
            window.recording.connect(events.append)
            try:
                for _ in range(2):
                    from ui.widgets.hotkey_edit import HotkeyEdit
                    window.page("截图").findChildren(HotkeyEdit)[0].recording.emit(True)
                    self.assertTrue(events[-1])
                    config.data["hotkey_health_enabled"] = False
                    window.page("快捷键").reset_page()
                    self.assertTrue(config.data["hotkey_health_enabled"])
                    window.rebuild_pages()
                # 2026-10-16：健康/恢复偏好已并入快捷键页，仍需保证它们没有跑到别的页面。
                self.assertIn("hotkey_health_interval", window.page("快捷键").controls)
                self.assertNotIn("hotkey_health_interval", window.page("截图").controls)
            finally:
                window.close()

    def test_log_and_compatibility_rows_are_independent(self):
        with tempfile.TemporaryDirectory() as folder:
            config = ConfigManager(Path(folder) / "settings.json")
            window = SettingsWindow(config)
            try:
                log = window.page("日志与诊断")
                log.update_value("logging_enabled", False)
                self.assertTrue(log.controls["hotkey_summary_interval"].isHidden())
                self.assertFalse(log.controls["hotkey_snapshot_limit"].isHidden())
                # 2026-10-16：键盘兼容性（兜底开关）已从截图页移到快捷键页；可见性依赖截图页的
                # 快速贴图开关，属跨页联动 —— 切换页面会刷新，这里显式刷新快捷键页再断言。
                capture = window.page("截图")
                capture.update_value("capture_quick_sticker_enabled", False)
                hotkey = window.page("快捷键")
                hotkey.sync_controls()
                self.assertTrue(hotkey.controls["capture_quick_sticker_hotkey_fallback"].isHidden())
                self.assertTrue(config.data["capture_quick_sticker_hotkey_fallback"])
            finally:
                window.close()

    def test_retry_fixed_backoff_limit_cooldown_and_policy_refresh(self):
        p = effective_policy(dict(DEFAULTS, hotkey_retry_limit=3, hotkey_retry_mode="backoff", hotkey_retry_max_interval=100))
        state = RecoveryState()
        state.fault("unfired:capture", ["capture"])
        for now, expected in ((100, 130), (130, 190), (190, 290)):
            self.assertTrue(state.permit(now, p))
            state.started(now, p)
            self.assertEqual(state.next_at, expected)
        self.assertFalse(state.permit(1000, p))
        self.assertEqual(state.state, "exhausted")
        p.update(hotkey_retry_limit=0, hotkey_retry_mode="fixed", hotkey_retry_interval=30)
        state.reconfigure(p)
        self.assertEqual(state.next_at, 220)
        self.assertTrue(state.permit(220, p))
        p.update(hotkey_retry_limit=3, hotkey_retry_exhausted="cooldown", hotkey_retry_cooldown=30)
        self.assertFalse(state.permit(220, p))
        self.assertTrue(state.permit(state.cooldown_until, p))

    def test_manual_bypass_and_reset_cannot_bypass_saved_stop_policy(self):
        with manager_context(hotkey_retry_limit=1, hotkey_manual_bypass=False, hotkey_manual_reset=True) as (m, _, settings):
            m.recovery.fault("fault", ["capture"])
            m.recovery.started(time.monotonic(), m.policy)
            self.assertFalse(m.manual_recover(settings))
            self.assertEqual(m.recovery.attempts, 1)
            settings["hotkey_manual_bypass"] = True
            self.assertTrue(m.manual_recover(settings))
            wait_for(lambda: m._registration_completed == m._registration_requested)
            self.assertEqual(m.recovery.attempts, 1)

    def test_policy_refresh_does_not_reinstall_bindings(self):
        with manager_context() as (m, fake, settings):
            before = fake.add_hotkey.call_count
            settings.update(hotkey_health_enabled=False, hotkey_summary_interval=90, hotkey_trace_interval=4)
            m.configure_policy(settings)
            self.assertFalse(m.health_timer.isActive())
            self.assertTrue(m._summary_timer.isActive())
            self.assertEqual(m._summary_timer.interval(), 90_000)
            self.assertEqual(fake.add_hotkey.call_count, before)
            fake.remove_hotkey.assert_not_called()

    def test_long_press_expiry_side_modifiers_and_ordering(self):
        with manager_context(hotkeys={"capture": "ctrl+alt+a"}) as (m, _, settings):
            for name in ("right ctrl", "left ctrl", "alt"):
                m._note_input(SimpleNamespace(name=name, event_type="down"))
            m._note_input(SimpleNamespace(name="left ctrl", event_type="up"))
            for _ in range(30):
                m._note_input(SimpleNamespace(name="a", event_type="down"))
            self.assertEqual(len(m._pending_attempts["capture"]), 1)
            m._pending_attempts["capture"] = [time.monotonic() - 100] * 2
            self.assertIsNone(m._intercepted_hotkey())
            m._note_input(SimpleNamespace(name="a", event_type="up"))
            m._note_input(SimpleNamespace(name="a", event_type="down"))
            m.emit_action("capture")
            self.assertNotIn("capture", m._pending_attempts)

    def test_no_input_is_unverified_and_unrelated_callback_does_not_resolve(self):
        with manager_context() as (m, _, _):
            m._pending_attempts["capture"] = [time.monotonic() - 1, time.monotonic() - 0.5]
            m.check_health()
            wait_for(lambda: m._registration_completed == m._registration_requested)
            APP.processEvents()
            self.assertEqual(m.recovery.attempts, 1)
            self.assertEqual(m.recovery.state, "awaiting-verification")
            for _ in range(10):
                m.check_health()
            self.assertEqual(m.recovery.attempts, 1)
            m.emit_action("paste")
            self.assertTrue(m.recovery.reason)
            m.emit_action("capture")
            self.assertFalse(m.recovery.reason)

    def test_only_record_keeps_one_fault_snapshot_and_no_registration(self):
        with manager_context(hotkey_diagnostic_only=True) as (m, fake, _):
            m._pending_attempts["capture"] = [time.monotonic() - 1, time.monotonic() - 0.5]
            before = fake.add_hotkey.call_count
            for _ in range(30):
                m.check_health()
            self.assertEqual(len(m._snapshot_history), 1)
            self.assertEqual(fake.add_hotkey.call_count, before)
            state = m.recovery
            state.callback("capture")
            state.fault("new", ["capture"])
            self.assertFalse(state.snapshot_taken)
            self.assertFalse(state.noticed)

    def test_queue_coalescing_and_policy_cancellation_keep_active_callbacks(self):
        with manager_context() as (m, fake, settings):
            entered = threading.Event()
            release = threading.Event()
            original_add = fake.add_hotkey.side_effect
            def blocked(*args, **kwargs):
                entered.set()
                if not release.wait(2):
                    raise RuntimeError("bounded test barrier")
                return original_add(*args, **kwargs)
            fake.add_hotkey.side_effect = blocked
            try:
                m.register(settings)
                self.assertTrue(entered.wait(1))
                m._queue_registration(m._desired_request, "automatic", recovery=True, automatic=True)
                settings["hotkey_recovery_enabled"] = False
                m.configure_policy(settings)
                release.set()
                wait_for(lambda: m._registration_state == "cancelled")
                self.assertEqual(m.recovery.attempts, 0)
                forwarded = []
                m.triggered.connect(forwarded.append)
                m.emit_action("capture")
                self.assertEqual(forwarded, ["capture"])
            finally:
                release.set()

    def test_pause_session_and_verification_suppress_delayed_actions(self):
        with manager_context() as (m, _, _):
            forwarded = []
            m.triggered.connect(forwarded.append)
            for transition in (lambda: (m.set_paused(True), m.set_paused(False)),
                               lambda: (m.session_changed(False), m.session_changed(True))):
                thread = threading.Thread(target=m.emit_action, args=("capture",))
                thread.start(); thread.join(1)
                transition()
                APP.processEvents()
            self.assertEqual(forwarded, [])
            m.begin_verification()
            thread = threading.Thread(target=m.emit_action, args=("capture",))
            thread.start(); thread.join(1)
            m.finish_verification()
            APP.processEvents()
            self.assertEqual(forwarded, [], "verification-time callback cannot execute after deadline")

    def test_busy_defers_and_manual_check_does_not_recover(self):
        with manager_context() as (m, _, _):
            m._pending_attempts["capture"] = [time.monotonic() - 1, time.monotonic() - 0.5]
            m.busy_check = lambda: True
            m.check_health()
            self.assertEqual(m.recovery.state, "deferred")
            before = m._registration_requested
            result = m.inspect_health()
            self.assertEqual(result["evidence"]["unfired_action"], "capture")
            self.assertEqual(m._registration_requested, before)
            m.busy_check = lambda: False
            m.set_busy_state(False)
            m.check_health()
            wait_for(lambda: m.recovery.attempts == 1)

    def test_binding_changes_discard_old_evidence(self):
        with manager_context() as (m, _, settings):
            m._pending_attempts["capture"] = [time.monotonic() - 1] * 2
            settings["hotkeys"]["capture"] = ""
            m.register(settings)
            wait_for(lambda: m._registration_completed == m._registration_requested)
            self.assertIsNone(m._intercepted_hotkey())

    def test_partial_registration_becomes_fault_without_per_binding_notification(self):
        with manager_context() as (m, fake, settings):
            notices = []
            failures = []
            m.notice.connect(notices.append); m.failed.connect(failures.append)
            def add(binding, callback, suppress=False):
                if binding == "f1":
                    raise ValueError("isolated unavailable")
                return callback
            fake.add_hotkey.side_effect = add
            m.register(settings)
            wait_for(lambda: m._registration_completed == m._registration_requested)
            APP.processEvents()
            self.assertEqual(m._registration_state, "partial")
            self.assertEqual(m.recovery.actions, {"capture"})
            self.assertEqual(notices, [])
            self.assertEqual(failures, [])

    def test_reports_are_private_bounded_and_reads_do_not_evict_faults(self):
        with manager_context(hotkey_snapshot_limit=2) as (m, fake, _):
            fake._pressed_events[999] = "PRIVATE-INPUT"
            m.capture_snapshot("fault")
            for _ in range(30):
                m.capture_snapshot("panel", retain=False)
                report = m.diagnostic_report()
            self.assertEqual(len(m._snapshot_history), 1)
            self.assertNotIn("PRIVATE-INPUT", report)
            m.capture_snapshot("manual"); m.capture_snapshot("manual")
            self.assertEqual(len(m._snapshot_history), 2)

    def test_temporary_detail_does_not_modify_level_or_restart_on_settings_refresh(self):
        with manager_context() as (m, _, settings):
            logger = get_logger()
            level = logger.level
            m.start_detail()
            until = m._detail_until
            settings["hotkey_detail_minutes"] = 2
            m.configure_policy(settings)
            self.assertEqual(m._detail_until, until)
            self.assertEqual(logger.level, level)
            m.stop_detail()
            self.assertEqual(m._detail_until, 0)

    def test_session_unlock_and_resume_are_not_last_event_wins(self):
        from core.session_monitor import SessionMonitor
        monitor = SessionMonitor()
        events = []
        monitor.available_changed.connect(events.append)
        monitor.process_notification(0x02B1, 7)
        monitor.process_notification(0x0218, 4)
        monitor.process_notification(0x0218, 18)
        self.assertEqual(events, [False])
        monitor.process_notification(0x02B1, 8)
        self.assertEqual(events, [False, True])
        monitor.close()

    def test_export_is_atomic_protects_live_directories_and_includes_newest_logs(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            logs = root / "logs"; logs.mkdir()
            private = root / "private"; private.mkdir()
            report = json.dumps({"safe": True})
            for destination in (logs / "report.json", private / "settings.json"):
                with self.assertRaises(ValueError):
                    export_report(destination, report, logs, root, private / "settings.json")
            (logs / "app.log").write_text("CURRENT", encoding="utf-8")
            for index in range(30):
                (logs / f"app.log.{index:02d}").write_text("OLD", encoding="utf-8")
            result = export_report(root / "report.zip", report, logs, root, private / "settings.json", True)
            self.assertGreater(result["truncated_logs"], 0)
            with zipfile.ZipFile(root / "report.zip") as archive:
                current = [name for name in archive.namelist() if name.endswith("/app.log")]
                self.assertEqual(archive.read(current[0]), b"CURRENT")
            target = root / "report.json"; target.write_text("before", encoding="utf-8")
            with patch.object(Path, "replace", side_effect=OSError("isolated write failure")):
                with self.assertRaises(OSError):
                    export_report(target, report, logs, root, private / "settings.json")
            self.assertEqual(target.read_text(encoding="utf-8"), "before")
            self.assertFalse(list(root.glob("screensnap-report-*.tmp")))

    def test_report_suffix_guard_cannot_overwrite_program_or_mislabel_archive(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for name, include in (("main.py", False), ("ScreenSnap.exe", True),
                                  ("report.json", True), ("report.zip", False)):
                with self.subTest(name=name):
                    target = root / name
                    target.write_text("KEEP", encoding="utf-8")
                    with self.assertRaises(ValueError):
                        export_report(target, "{}", root / "logs", root,
                                      root / "private" / "settings.json", include)
                    self.assertEqual(target.read_text(encoding="utf-8"), "KEEP")
            self.assertFalse(list(root.glob("screensnap-report-*.tmp")))

    def test_copy_and_export_share_allowlisted_capture_compatibility_report(self):
        from app import hotkey_flow as flow
        app = flow.HotkeyControlMixin()
        app.hotkeys = Mock()
        app.hotkeys.diagnostic_report.return_value = json.dumps({"format": "test", "current": {}})
        app.capture_compatibility_status = Mock(return_value=dict(
            toolbar="environment-disabled", quick_sticker="session-comparison-disabled",
            escape="installation-failed", capture_active=True, temporary_handles=1,
            escape_installed=False, private="PRIVATE-PATH", binding="PRIVATE-KEY"))
        clipboard = Mock()
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            app.config = SimpleNamespace(data=dict(DEFAULTS), path=root / "private" / "settings.json")
            app.settings_window = None
            target = root / "report.json"
            with patch("editor.image_effects.clipboard_available", return_value=True), \
                    patch.object(flow.QApplication, "clipboard", return_value=clipboard), \
                    patch.object(flow.QMessageBox, "question") as question, \
                    patch.object(flow.QFileDialog, "getSaveFileName", return_value=(str(target), "")), \
                    patch.object(flow, "log_directory", return_value=root / "logs"):
                app.copy_hotkey_report()
                app.export_hotkey_report()
            question.assert_not_called()
            copied = clipboard.setText.call_args.args[0]
            self.assertEqual(json.loads(copied), json.loads(target.read_text(encoding="utf-8")))
            states = json.loads(copied)["capture_compatibility"]
            self.assertEqual(states["toolbar"], "environment-disabled")
            self.assertEqual(states["temporary_handles"], 1)
            self.assertFalse(states["escape_installed"])
            self.assertNotIn("PRIVATE", copied)

    def test_raw_log_export_requires_each_consent_and_reports_omitted_count(self):
        from app import hotkey_flow as flow
        app = flow.HotkeyControlMixin()
        app.hotkeys = Mock()
        app.hotkeys.diagnostic_report.return_value = "{}"
        app.settings_window = None
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            logs = root / "logs"; logs.mkdir()
            (logs / "app.log").write_text("CURRENT", encoding="utf-8")
            for index in range(25):
                (logs / f"app.log.{index}").write_text("OLD", encoding="utf-8")
            app.config = SimpleNamespace(data=dict(DEFAULTS, hotkey_report_include_logs=True),
                                         path=root / "private" / "settings.json")
            target = root / "report.zip"
            with patch.object(flow.QMessageBox, "question", side_effect=[
                    flow.QMessageBox.No, flow.QMessageBox.Yes, flow.QMessageBox.Yes]) as question, \
                    patch.object(flow.QFileDialog, "getSaveFileName", return_value=(str(target), "")) as dialog, \
                    patch.object(flow.sys, "argv", [str(root / "main.py")]), \
                    patch.object(flow, "log_directory", return_value=logs):
                self.assertIn("取消", app.export_hotkey_report())
                dialog.assert_not_called()
                self.assertFalse(target.exists())
                for _ in range(2):
                    self.assertIn("数量超限未附带=6", app.export_hotkey_report())
                self.assertEqual(question.call_count, 3)
                self.assertTrue(all(call.args[-1] == flow.QMessageBox.No for call in question.call_args_list))
            with zipfile.ZipFile(target) as archive:
                self.assertEqual(len([name for name in archive.namelist() if name.startswith("raw-logs/")]), 20)
                self.assertIn("capture_compatibility", json.loads(archive.read("diagnostic.json")))

    def test_logging_retention_is_consumed_by_actual_handler(self):
        from logger.log_setup import configure_logging
        with tempfile.TemporaryDirectory() as folder, patch("logger.log_setup.enable_crash_dumps"):
            logger = configure_logging(dict(DEFAULTS, log_dir=folder, log_backup_count=7))
            handler = next(item for item in logger.handlers if hasattr(item, "backupCount"))
            self.assertEqual(handler.backupCount, 7)
            configure_logging(dict(DEFAULTS, logging_enabled=False))

    def test_application_policy_only_refresh_does_not_register_or_rebuild_logger(self):
        from main import Application
        app = Application.__new__(Application)
        app.config = SimpleNamespace(data=copy.deepcopy(DEFAULTS))
        app.stickers = Mock(); app.qt = APP; app.tray = Mock(); app.hotkeys = Mock()
        app.hotkey_recording = False; app.mask = None; app.editors = []
        app._applied_hotkey_settings = app.hotkey_binding_settings()
        app._applied_logging_settings = app.logging_settings()
        app.config.data.update(hotkey_summary_interval=120, hotkey_health_enabled=False)
        with patch("main.configure_logging") as configure, patch("main.apply_theme"), \
                patch("main.make_tray_menu", return_value=Mock()):
            app.refresh()
        configure.assert_not_called()
        app.hotkeys.register.assert_not_called()
        app.hotkeys.configure_policy.assert_called_once_with(app.config.data)

    def test_offscreen_mask_without_native_fallback_has_no_global_input_filter(self):
        from PIL import Image
        from screenshot.mask_window import MaskWindow
        bounds = dict(left=0, top=0, width=120, height=80)
        settings = dict(DEFAULTS, window_detection=False, magnifier=False, crosshair=False)
        with patch("screenshot.mask_window.visible_windows", return_value=[]), \
                patch("screenshot.mask_window.ESCAPE_FALLBACK_ENABLED", False):
            mask = MaskWindow(Image.new("RGB", (120, 80)), bounds, [bounds], settings)
            mask.show()
            APP.processEvents()
            self.assertIsNone(getattr(mask.session, "keyboard_bridge", None))
            mask.close()


class CompatibilityTests(unittest.TestCase):
    @contextmanager
    def fixture(self):
        from screenshot import keyboard_compatibility as compat
        owner = QWidget()
        owner.primary = True
        owner.settings = copy.deepcopy(DEFAULTS)
        owner.session = SimpleNamespace(closing=False, views=[owner], compatibility_override=False, keyboard_recording=False)
        owner.toggle_inline_toolbar = Mock(); owner.trigger_quick_sticker = Mock()
        backend = SimpleNamespace(add_hotkey=Mock(side_effect=lambda key, cb, suppress=False: cb), remove_hotkey=Mock())
        with patch.dict(sys.modules, {"keyboard": backend}), patch.object(compat, "fallback_state", return_value="enabled"):
            bridge = compat.CaptureKeyboardBridge(owner)
            owner.sync_keyboard_compatibility = lambda: (bridge.sync("toolbar"), bridge.sync("quick_sticker"))
            owner.session.keyboard_bridge = bridge
            owner.show()
            try:
                with patch.object(bridge, "input_owned_by_editor", return_value=False):
                    owner.sync_keyboard_compatibility()
                    yield owner, bridge, backend
            finally:
                owner.session.closing = True
                bridge.release("toolbar"); bridge.release("quick_sticker")
                owner.close()
                APP.processEvents()

    def test_unchanged_reconciliation_no_duplicates_and_space_is_not_suppressed(self):
        with self.fixture() as (owner, bridge, backend):
            before = backend.add_hotkey.call_count
            for _ in range(30):
                owner.sync_keyboard_compatibility()
            self.assertEqual(backend.add_hotkey.call_count, before)
            space_call = next(call for call in backend.add_hotkey.call_args_list if call.args[0] == "Space")
            self.assertFalse(space_call.kwargs["suppress"])
            owner.settings["capture_quick_sticker_shortcut"] = "Ctrl+Space"
            owner.sync_keyboard_compatibility()
            self.assertEqual(backend.add_hotkey.call_count, before + 1)

    def test_recording_modal_and_stale_callbacks_are_safe(self):
        with self.fixture() as (owner, bridge, backend):
            old = bridge.handles["toolbar"]
            owner.session.keyboard_recording = True
            owner.sync_keyboard_compatibility()
            self.assertFalse(bridge.handles)
            owner.session.keyboard_recording = False
            owner.sync_keyboard_compatibility()
            thread = threading.Thread(target=old)
            thread.start(); thread.join(1)
            APP.processEvents()
            owner.toggle_inline_toolbar.assert_not_called()
            with patch.object(bridge, "input_owned_by_editor", return_value=True):
                owner.sync_keyboard_compatibility()
                self.assertFalse(bridge.handles, "modal must remove suppressing hooks, not merely ignore their callback")

    def test_environment_and_session_overrides_do_not_rewrite_preferences(self):
        from screenshot.keyboard_compatibility import FALLBACKS, fallback_state
        settings = dict(DEFAULTS)
        for kind, (_, environment, _, _, _) in FALLBACKS.items():
            with patch.dict(os.environ, {environment: "1"}):
                self.assertEqual(fallback_state(kind, settings), "environment-disabled")
            self.assertEqual(fallback_state(kind, settings, override=True), "session-comparison-disabled")
        self.assertEqual(settings, DEFAULTS)


if __name__ == "__main__":
    unittest.main()
