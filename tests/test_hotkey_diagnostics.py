"""热键失效取证；只使用假 backend，不向用户桌面安装/移除原生钩子。"""

import logging
import os
import queue
import sys
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT in sys.path:
    sys.path.remove(_ROOT)
sys.path.insert(0, _ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication
from hotkey import hotkey_manager as hm
from logger import log_context


class HotkeyDiagnosticsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.backend = SimpleNamespace(
            _listener=SimpleNamespace(
                listening=True,
                listening_thread=Mock(is_alive=Mock(return_value=True)),
                processing_thread=Mock(is_alive=Mock(return_value=True)),
                queue=queue.Queue(), active_modifiers={123}, modifier_states={123: "secret"},
                is_replaying=False),
            _pressed_events={123: "PRIVATE-KEY-CONTENT"},
            _logically_pressed_keys={123: "PRIVATE-KEY-CONTENT"},
            add_hotkey=Mock(side_effect=lambda binding, callback, suppress=False: callback),
            remove_hotkey=Mock(), hook=Mock(side_effect=lambda callback: callback), unhook=Mock())
        self.keyboard_patch = patch.object(hm, "keyboard", self.backend)
        self.mouse_patch = patch.object(hm.HotkeyManager, "_mouse_module", return_value=(None, "isolated"))
        self.keyboard_patch.start()
        self.mouse_patch.start()
        self.manager = hm.HotkeyManager()
        self.settings = dict(hotkeys_enabled=True, hotkeys={"capture": "ctrl+alt+a"},
                             capture_hotkey_suppress=True, hotkey_health_interval=4)

    def tearDown(self):
        self.manager.stop()
        self.app.processEvents()
        self.mouse_patch.stop()
        self.keyboard_patch.stop()

    def wait_for(self, predicate):
        deadline = time.monotonic() + 3
        while not predicate() and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(predicate(), "registration did not finish within three seconds")

    def register(self):
        self.manager.register(self.settings)
        self.wait_for(lambda: self.manager._registration_completed == 1)

    def test_real_listener_threads_are_checked_even_if_listening_flag_is_true(self):
        self.assertTrue(self.manager.listener_alive())
        for attribute in ("listening_thread", "processing_thread"):
            with self.subTest(attribute=attribute):
                thread = getattr(self.backend._listener, attribute)
                thread.is_alive.return_value = False
                self.assertFalse(self.manager.listener_alive())
                state = self.manager.diagnostic_snapshot()
                self.assertTrue(state["listening"])
                self.assertFalse(state["native_thread" if attribute == "listening_thread" else "processing_thread"])
                thread.is_alive.return_value = True
        self.register()
        before = self.manager._registration_requested
        with patch.object(self.manager, "worker", Mock(is_alive=Mock(return_value=False))), \
                self.assertLogs("screensnap", level="ERROR") as captured:
            self.manager.check_health(force=True)
        self.assertEqual(self.manager._registration_requested, before)
        self.assertTrue(any("registration-worker-dead:restart-required" in line for line in captured.output))

    def test_unknown_listener_thread_attributes_do_not_claim_failure(self):
        self.backend._listener = SimpleNamespace(listening=True)
        self.assertTrue(self.manager.listener_alive())
        state = self.manager.diagnostic_snapshot()
        self.assertIsNone(state["native_thread"])
        self.assertIsNone(state["processing_thread"])
        self.assertIsNone(state["backend_pending"])

    def test_keyboard_and_mouse_observation_are_distinct_and_metadata_only(self):
        self.manager._note_input(SimpleNamespace(name="PRIVATE-TYPED-TEXT", event_type="down"))
        keyboard_time = self.manager._observed_keyboard_input
        for _ in range(30):
            self.manager._note_mouse_input(SimpleNamespace(name="MOUSE-PRIVATE-CONTENT"))
        state = self.manager.diagnostic_snapshot()
        self.assertEqual(self.manager._observed_keyboard_input, keyboard_time)
        self.assertEqual(state["keyboard_events"], 1)
        self.assertEqual(state["mouse_events"], 30)
        self.assertEqual(state["pressed_count"], 1)
        self.assertNotIn("PRIVATE", str(state))
        self.assertNotIn("secret", str(state))

    def test_metadata_log_filter_does_not_query_qt_or_foreground(self):
        record = logging.LogRecord("screensnap", logging.INFO, __file__, 1, "diagnostic", (), None)
        record.screensnap_metadata_only = True
        with patch.object(log_context, "warm") as warm, \
                patch.object(log_context, "mouse_text") as mouse, \
                patch.object(log_context, "foreground_text") as foreground:
            self.assertTrue(log_context.ContextFilter().filter(record))
        warm.assert_not_called()
        mouse.assert_not_called()
        foreground.assert_not_called()
        self.assertIn(f"pid={os.getpid()}", record.context)
        self.assertIn("thread=", record.context)

    def test_registration_has_queued_started_and_completed_generations(self):
        with self.assertLogs("screensnap", level="INFO") as captured:
            self.register()
            self.wait_for(lambda: any("installed-bindings-only" in line for line in captured.output))
        text = "\n".join(captured.output)
        for reason in ("queued:settings", "install-start:settings", "installed-bindings-only"):
            self.assertIn(reason, text)
        self.assertEqual(self.manager._registration_requested, 1)
        self.assertEqual(self.manager._registration_started, 1)
        self.assertEqual(self.manager._registration_completed, 1)
        self.backend.add_hotkey.assert_called_once()
        self.assertTrue(self.backend.add_hotkey.call_args.kwargs["suppress"])
        self.assertNotIn("PRIVATE", text)

    def test_failed_registration_is_distinct_from_completion_and_worker_lives(self):
        self.backend.add_hotkey.side_effect = RuntimeError("isolated failure")
        with self.assertLogs("screensnap", level="INFO") as captured:
            self.manager.register(self.settings)
            self.wait_for(lambda: any("install-error:RuntimeError" in line for line in captured.output))
        self.assertEqual(self.manager._registration_failed, 1)
        self.assertEqual(self.manager._registration_completed, 0)
        self.assertTrue(self.manager.worker.is_alive())

    def test_diagnostic_failure_does_not_block_binding_installation(self):
        with patch.object(self.manager, "diagnostic_snapshot", side_effect=RuntimeError("diagnostic only")):
            self.register()
        self.assertEqual(len(self.manager.handles), 1)
        self.assertEqual(self.manager._diagnostic_error, "RuntimeError")
        self.assertTrue(self.manager.worker.is_alive())

    def test_background_callback_does_not_write_logs_until_qt_receives_it(self):
        self.register()
        with self.assertLogs("screensnap", level="DEBUG") as captured:
            thread = threading.Thread(target=self.manager.emit_action, args=("capture",), name="isolated-native-hook")
            thread.start()
            thread.join(timeout=2)
            self.assertFalse(thread.is_alive())
            self.assertEqual(self.manager._callback_events, 1)
            self.assertEqual(self.manager._qt_callbacks, 0)
            self.assertEqual(captured.records, [], "native hook must not synchronously collect context/write logs")
            self.app.processEvents()
        self.assertEqual(self.manager._qt_callbacks, 1)
        self.assertTrue(any("callback-reached-qt" in line for line in captured.output))
        self.assertTrue(all(record.threadName != "isolated-native-hook" for record in captured.records))

    def test_paused_callbacks_are_counted_but_not_forwarded(self):
        forwarded = Mock()
        self.manager.triggered.connect(forwarded)
        self.manager.set_paused(True)
        for _ in range(30):
            self.manager.emit_action("capture")
        self.app.processEvents()
        forwarded.assert_not_called()
        state = self.manager.diagnostic_snapshot()
        self.assertEqual(state["paused_callbacks"], 30)
        self.assertEqual(state["callbacks"], 30)
        self.assertEqual(state["qt_callbacks"], 0)

    def test_unchanged_health_and_repeated_callbacks_do_not_flood_logs(self):
        self.register()
        with patch.object(hm, "last_input_seconds", return_value=30), \
                patch.object(self.manager, "log_diagnostics") as diagnostic:
            for _ in range(30):
                self.manager.check_health()
            diagnostic.assert_not_called()
            self.manager._heartbeat_at -= hm.HEARTBEAT_SECONDS
            for _ in range(30):
                self.manager.check_health()
            diagnostic.assert_not_called()
            self.manager._summary_timer.timeout.emit()
            diagnostic.assert_called_once_with("heartbeat")
        with self.assertLogs("screensnap", level="DEBUG") as captured:
            for _ in range(30):
                self.manager.emit_action("capture")
        self.assertEqual(sum("热键回调:" in line for line in captured.output), 1)
        self.assertEqual(sum("callback-reached-qt" in line for line in captured.output), 1)

    def test_disabled_hotkeys_stay_disabled_after_health_check(self):
        self.settings["hotkeys_enabled"] = False
        self.register()
        self.assertIsNone(self.manager._last_request)
        self.manager.check_health(force=True)
        self.assertEqual(self.manager._registration_requested, 1)
        self.backend.add_hotkey.assert_not_called()

    def test_manual_recovery_reports_request_not_success_and_clears_pause(self):
        from main import Application

        hotkeys = Mock()
        app = SimpleNamespace(logger=Mock(), hotkeys=hotkeys, hotkey_recording=True,
                              config=SimpleNamespace(data=self.settings), notify=Mock())
        Application.restore_hotkeys(app)
        hotkeys.log_diagnostics.assert_called_once_with("manual-before-unpause")
        hotkeys.set_paused.assert_called_once_with(False)
        hotkeys.manual_recover.assert_called_once_with(self.settings)
        self.assertFalse(app.hotkey_recording)
        self.assertIn("已请求", app.notify.call_args.args[0])
        self.assertNotIn("已重新注册", app.notify.call_args.args[0])


if __name__ == "__main__":
    unittest.main()

