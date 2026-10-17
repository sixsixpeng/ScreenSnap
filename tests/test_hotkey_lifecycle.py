"""热键收尾回归：假后端、有限屏障、临时配置，不触碰真实输入链。"""
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

_ROOT = str(Path(__file__).resolve().parents[1])
if _ROOT in sys.path:
    sys.path.remove(_ROOT)
sys.path.insert(0, _ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from tests import test_hotkey_policy as fixtures
from config.config_manager import ConfigManager, DEFAULTS
from hotkey.recovery import RecoveryState


class HotkeyLifecycleTests(unittest.TestCase):
    def test_inflight_install_rolls_back_after_lifecycle_changes(self):
        for transition in ("pause", "disable", "session", "stop", "automatic-policy", "automatic-busy"):
            with self.subTest(transition=transition), fixtures.manager_context() as (manager, backend, settings):
                entered, release = threading.Event(), threading.Event()
                original = backend.add_hotkey.side_effect
                before = backend.add_hotkey.call_count
                def blocked(*args, **kwargs):
                    entered.set()
                    if not release.wait(3):
                        raise RuntimeError("bounded install barrier")
                    return original(*args, **kwargs)
                backend.add_hotkey.side_effect = blocked
                automatic = transition.startswith("automatic")
                try:
                    manager._queue_registration(manager._desired_request, "test", automatic=automatic)
                    self.assertTrue(entered.wait(1))
                    if transition == "pause":
                        manager.set_paused(True)
                    elif transition == "disable":
                        manager._actions_enabled = False
                    elif transition == "session":
                        manager.session_changed(False)
                    elif transition == "stop":
                        manager.stop()
                    elif transition == "automatic-policy":
                        settings["hotkey_recovery_enabled"] = False
                        manager.configure_policy(settings)
                    else:
                        manager.set_busy_state(True)
                    release.set()
                    fixtures.wait_for(lambda: manager._install_started_at is None)
                    self.assertEqual(backend.add_hotkey.call_count, before + 1)
                    self.assertEqual(manager.handles, [])
                    self.assertNotEqual(manager._registration_state, "completed")
                    if transition == "stop":
                        fixtures.wait_for(lambda: not manager.worker.is_alive())
                finally:
                    release.set()

    def test_callback_signal_deletion_does_not_escape_to_backend(self):
        with fixtures.manager_context() as (manager, _, _):
            with patch.object(manager, "_callback_trace", SimpleNamespace(emit=Mock(side_effect=RuntimeError("deleted")))):
                manager.emit_action("capture")
            self.assertEqual(manager._paused_callbacks, 1)

    def test_registration_signal_deletion_does_not_kill_worker(self):
        with fixtures.manager_context() as (manager, _, settings):
            with patch.object(manager, "registration_finished", SimpleNamespace(emit=Mock(side_effect=RuntimeError("deleted")))):
                manager.register(settings)
                fixtures.wait_for(lambda: manager._install_started_at is None and manager._registration_completed == manager._registration_requested)
                manager.register(settings)
                fixtures.wait_for(lambda: manager._install_started_at is None and manager._registration_completed == manager._registration_requested)
                self.assertTrue(manager.worker.is_alive())

    def test_recovery_permission_is_rechecked_when_worker_starts(self):
        with fixtures.manager_context(hotkey_manual_bypass=False, hotkey_manual_reset=False, hotkey_retry_limit=1) as (manager, backend, settings):
            entered, release = threading.Event(), threading.Event()
            original = backend.add_hotkey.side_effect
            def blocked(*args, **kwargs):
                entered.set()
                if not release.wait(2):
                    raise RuntimeError("bounded queue barrier")
                return original(*args, **kwargs)
            backend.add_hotkey.side_effect = blocked
            try:
                manager.register(settings)
                self.assertTrue(entered.wait(1))
                manager.manual_recover(settings)
                manager.recovery.started(time.monotonic(), manager.policy)
                release.set()
                fixtures.wait_for(lambda: manager.requests.empty() and manager._install_started_at is None)
                self.assertEqual(manager.recovery.attempts, 1)
            finally:
                release.set()

    def test_extra_ordinary_key_does_not_create_unfired_evidence(self):
        with fixtures.manager_context(hotkeys={"capture": "ctrl+alt+a"}) as (manager, _, _):
            for name in ("ctrl", "alt", "b"):
                manager._note_input(SimpleNamespace(name=name, event_type="down"))
            for _ in range(2):
                manager._note_input(SimpleNamespace(name="a", event_type="down"))
                manager._note_input(SimpleNamespace(name="a", event_type="up"))
            self.assertNotIn("capture", manager._pending_attempts)
            manager._note_input(SimpleNamespace(name="b", event_type="up"))
            manager._note_input(SimpleNamespace(name="a", event_type="down"))
            self.assertEqual(len(manager._pending_attempts["capture"]), 1)

    def test_binding_aliases_and_multiword_key_names_match_events(self):
        for binding, name in (("Ctrl+PageUp", "page up"), ("ctrl+page down", "page down"), ("Escape", "esc"), ("Return", "enter")):
            with self.subTest(binding=binding), fixtures.manager_context(hotkeys={"capture": binding}) as (manager, _, _):
                if binding.lower().startswith("ctrl"):
                    manager._note_input(SimpleNamespace(name="ctrl", event_type="down"))
                manager._note_input(SimpleNamespace(name=name, event_type="down"))
                self.assertEqual(len(manager._pending_attempts.get("capture", [])), 1)

    def test_missing_observer_is_retried_without_duplicating_other_owner(self):
        with fixtures.manager_context() as (manager, backend, settings):
            manager._remove_input_hooks()
            mouse = SimpleNamespace(hook=Mock(return_value=object()), unhook=Mock())
            backend.hook.side_effect = [OSError("isolated"), object()]
            manager._mouse = mouse
            manager.register(settings)
            fixtures.wait_for(lambda: manager._registration_completed == manager._registration_requested)
            self.assertFalse(manager._keyboard_observer)
            self.assertTrue(manager._mouse_observer)
            manager.register(settings)
            fixtures.wait_for(lambda: manager._registration_completed == manager._registration_requested)
            self.assertTrue(manager._keyboard_observer)
            self.assertEqual(mouse.hook.call_count, 1)
            manager.stop()
            mouse.unhook.assert_called_once()

    def test_metadata_snapshot_keeps_safe_failure_reasons_and_evidence(self):
        with fixtures.manager_context() as (manager, _, _):
            manager._observer_error = "OSError"
            manager._diagnostic_error = "RuntimeError"
            manager._registration_result = {"state": "partial", "failed": 1, "failed_actions": ["capture", "PRIVATE-PATH"]}
            manager._pending_attempts["capture"] = [time.monotonic() - 1] * 2
            # 2026-10-16：报告改为直接写日志 ⇒ 快照本身就是最终产物（不再有 history/物理核对）。
            manager._last_health_evidence = manager.health_evidence()
            current = manager.capture_snapshot("fault")
            self.assertEqual(current["observer_error"], "OSError")
            self.assertEqual(current["diagnostic_error"], "RuntimeError")
            self.assertEqual(current["install_result"]["state"], "partial")
            self.assertEqual(current["install_result"]["failed_actions"], ["capture"])
            self.assertEqual(current["evidence"]["unfired_action"], "capture")
            self.assertNotIn("PRIVATE", json.dumps(current, ensure_ascii=False))

    def test_manual_restore_clears_capture_recording_even_if_rejected(self):
        from main import Application
        for accepted in (True, False):
            with self.subTest(accepted=accepted):
                mask = SimpleNamespace(session=SimpleNamespace(keyboard_recording=True), sync_keyboard_compatibility=Mock())
                app = Application.__new__(Application)
                app.logger = Mock(); app.hotkeys = Mock(); app.hotkey_recording = True; app.mask = mask
                app.config = SimpleNamespace(data=copy.deepcopy(DEFAULTS)); app.notify = Mock()
                app.hotkeys.manual_recover.return_value = accepted
                with patch("main.MaskWindow", SimpleNamespace):
                    Application.restore_hotkeys(app)
                self.assertFalse(mask.session.keyboard_recording)
                mask.sync_keyboard_compatibility.assert_called_once()

    def test_capture_page_reset_syncs_shortcut_display_without_recording(self):
        from PySide6.QtGui import QKeySequence
        from ui.settings_window import SettingsWindow
        with tempfile.TemporaryDirectory() as folder:
            config = ConfigManager(Path(folder) / "settings.json")
            window = SettingsWindow(config)
            try:
                page = window.page("截图")
                key = "capture_toolbar_hide_shortcut"
                config.data[key] = "Ctrl+J"
                widget = page.controls[key]
                widget.setKeySequence(QKeySequence("Ctrl+J"))
                recording = Mock()
                window.recording.connect(recording)
                page.reset_page()
                self.assertEqual(widget.keySequence(), QKeySequence(DEFAULTS[key]))
                recording.assert_not_called()
            finally:
                window.close()

    def test_cancelled_automatic_request_preserves_inflight_user_bindings(self):
        with fixtures.manager_context() as (manager, backend, settings):
            entered, release = threading.Event(), threading.Event()
            original = backend.add_hotkey.side_effect
            def blocked(*args, **kwargs):
                entered.set()
                if not release.wait(2):
                    raise RuntimeError("bounded intent barrier")
                return original(*args, **kwargs)
            backend.add_hotkey.side_effect = blocked
            try:
                manager.register(settings)
                self.assertTrue(entered.wait(1))
                manager._queue_registration(manager._desired_request, "automatic", automatic=True)
                settings["hotkey_recovery_enabled"] = False
                manager.configure_policy(settings)
                release.set()
                fixtures.wait_for(lambda: manager._registration_state == "cancelled" and manager._install_started_at is None)
                self.assertEqual(len(manager.handles), 2, "cancelled retry must not remove valid user bindings")
                received = []
                manager.triggered.connect(received.append)
                manager.handles[0]()
                self.assertEqual(received, ["capture"])
            finally:
                release.set()

    def test_session_resume_does_not_revive_cancelled_automatic_recovery(self):
        with fixtures.manager_context(hotkey_recovery_enabled=False) as (manager, _, _):
            manager.clear()
            manager._registration_state = "cancelled"
            before = manager._registration_requested
            manager.session_changed(False)
            manager.session_changed(True)
            manager._housekeeping()
            self.assertEqual(manager._registration_requested, before)

    def test_interrupted_automatic_install_restores_desired_bindings_without_retry(self):
        for transition in ("policy", "busy", "session"):
            with self.subTest(transition=transition), fixtures.manager_context(hotkey_wake_grace=0) as (manager, backend, settings):
                entered, release = threading.Event(), threading.Event()
                original = backend.add_hotkey.side_effect
                def blocked(*args, **kwargs):
                    entered.set()
                    if not release.wait(2):
                        raise RuntimeError("bounded rollback barrier")
                    return original(*args, **kwargs)
                backend.add_hotkey.side_effect = blocked
                try:
                    manager.recovery.fault("unfired:capture", ["capture"])
                    manager._queue_registration(manager._desired_request, "unfired:capture", recovery=True, automatic=True)
                    self.assertTrue(entered.wait(1))
                    if transition == "policy":
                        settings["hotkey_health_enabled"] = False
                        manager.configure_policy(settings)
                    elif transition == "busy":
                        manager.busy_check = lambda: True
                        manager.set_busy_state(True)
                    else:
                        manager.session_changed(False)
                    release.set()
                    fixtures.wait_for(lambda: manager._registration_state == "cancelled" and manager._install_started_at is None)
                    self.assertEqual(manager.handles, [])
                    attempts = manager.recovery.attempts
                    if transition != "policy":
                        manager._housekeeping()
                        self.assertEqual(manager.handles, [], "busy/unavailable state must still defer restoration")
                    manager.busy_check = lambda: False
                    manager.set_busy_state(False)
                    if transition == "session":
                        manager.session_changed(True)
                    manager._housekeeping()
                    fixtures.wait_for(lambda: manager._install_started_at is None and len(manager.handles) == 2)
                    self.assertEqual(manager.recovery.attempts, attempts, "binding rollback is not another automatic recovery attempt")
                    received = []
                    manager.triggered.connect(received.append)
                    manager.handles[0]()
                    self.assertEqual(received, ["capture"])
                finally:
                    release.set()

    def test_tray_recovery_is_a_request_not_a_native_hook_repair_promise(self):
        from ui.tray_menu import make_tray_menu
        recover = Mock()
        menu = make_tray_menu(fixtures.APP, Mock(), Mock(), Mock(), restore_hotkeys=recover)
        try:
            action = next(item for item in menu.actions() if item.text() == "重新注册全局热键")
            self.assertIn("请求", action.toolTip())
            self.assertIn("不重启原生钩子", action.toolTip())
            action.trigger()
            recover.assert_called_once_with()
        finally:
            menu.close()

    def test_cooldown_refresh_keeps_original_start_and_full_duration(self):
        policy = fixtures.effective_policy(dict(DEFAULTS, hotkey_retry_limit=1, hotkey_retry_exhausted="cooldown"))
        state = RecoveryState()
        state.started(100, policy)
        self.assertFalse(state.permit(130, policy))
        self.assertEqual(state.cooldown_until, 430)
        state.reconfigure(policy)
        self.assertEqual(state.cooldown_until, 430)
        policy["hotkey_retry_cooldown"] = 600
        state.reconfigure(policy)
        self.assertEqual(state.cooldown_until, 730)

    def test_unchanged_policy_does_not_restart_summary_timer(self):
        with fixtures.manager_context() as (manager, _, settings):
            with patch.object(manager._summary_timer, "setInterval") as interval:
                manager.configure_policy(settings)
            interval.assert_not_called()

    def test_temporary_fallbacks_reject_normalized_global_binding_conflicts(self):
        from screenshot import keyboard_compatibility as compatibility
        for kind, shortcut, global_binding in (("toolbar", "Ctrl+Return", "enter+control"),
                ("quick_sticker", "Space", "space"), ("escape", None, "Escape")):
            with self.subTest(kind=kind), patch.object(compatibility, "offscreen", return_value=False):
                settings = copy.deepcopy(DEFAULTS)
                settings["hotkeys"] = {"capture": global_binding}
                key = compatibility.FALLBACKS[kind][2]
                if key is not None:
                    settings[key] = shortcut
                self.assertEqual(compatibility.fallback_state(kind, settings), "conflict-with-global-binding")
                settings["hotkeys_enabled"] = False
                self.assertEqual(compatibility.fallback_state(kind, settings), "enabled")


if __name__ == "__main__":
    unittest.main()

