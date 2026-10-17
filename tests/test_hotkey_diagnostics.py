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

    def test_input_stale_fault_notice_is_actionable_while_others_stay_generic(self):
        """input-stale 的提示必须给可操作方向（切走焦点），其余判据不猜原因、保持通用措辞。

        2026-10-16 真机实测：该判据出现在前台为提权窗口（任务管理器）时 —— 非提权进程拿不回被
        挡住的键盘输入，重建绑定无效，用户在日志之外看不到任何提示（旧文案只说"异常证据"）。
        """
        # 2026-10-16：文案按通知长度精简（应用内提示与系统 toast 都只显示一两行）。
        generic = "热键异常；重建绑定不保证恢复原生钩子"
        self.assertEqual(hm.HotkeyManager._fault_notice("unfired:capture"), generic)
        self.assertEqual(hm.HotkeyManager._fault_notice("phantom-pressed:1"), generic)

        notices = []
        self.manager.notice.connect(notices.append)
        self.register()
        with patch.object(self.manager, "_hook_missed_input", return_value=True):
            self.manager.check_health()
        self.assertTrue(notices, "input-stale 必须提示，而不是只写日志")
        self.assertIn("管理员", notices[0])
        self.assertIn("切换", notices[0])

    # ---- 卡住的按键记录（2026-10-16 真机故障）-------------------------------------

    def test_stale_pressed_clears_only_entries_the_system_says_are_up(self):
        """只清「系统证明未按下」的项：真正按住的键必须保留，否则未触发判据会变成误报源。"""
        self.backend._pressed_events_lock = threading.Lock()
        self.backend._pressed_events.update({77: "held", 88: "held"})
        self.manager._pressed_names.update({"f1", "f2"})
        self.manager._pressed_scans.update({"f1": 77, "f2": 88})
        with patch.object(hm, "stale_pressed_codes", return_value=(77,)), \
                patch.object(hm, "physically_up_codes", return_value=(77,)):
            removed = self.manager._clear_stale_pressed("test")
        self.assertEqual(removed, 1)
        self.assertNotIn(77, self.backend._pressed_events)
        self.assertIn(88, self.backend._pressed_events, "系统说仍按下的键不得被清掉")
        self.assertEqual(self.manager._pressed_names, {"f2"})
        self.assertEqual(self.manager._pressed_scans, {"f2": 88})

    def test_stale_pressed_detection_needs_two_checks_then_rebuilds_bindings(self):
        """连续两次成立才处置；处置后库表与本程序记录都干净，并真的重建了绑定。

        「重新注册只重建绑定」是本次事故里假成功的直接原因，所以这里同时断言
        「第一次不重建」与「第二次清完再重建」。
        """
        self.backend._pressed_events_lock = threading.Lock()
        self.backend._pressed_events[77] = "held"
        self.manager._pressed_names.update({"f1"})
        self.manager._pressed_scans["f1"] = 77
        self.register()
        requested = self.manager._registration_requested
        with patch.object(hm, "stale_pressed_codes", return_value=(77,)), \
                patch.object(hm, "physically_up_codes", return_value=(77,)):
            self.manager.check_health()
            self.assertEqual(self.manager._registration_requested, requested,
                             "第一次候选不得立即重建绑定（可能与刚释放的键竞态）")
            self.assertEqual(self.manager.recovery.reason, "")
            self.manager.check_health()
        self.assertEqual(self.manager.recovery.reason, "phantom-pressed:1")
        self.assertNotIn(77, self.backend._pressed_events)
        self.assertEqual(self.manager._pressed_names, set())
        self.assertEqual(self.manager._pressed_scans, {})
        self.wait_for(lambda: self.manager._registration_requested > requested)

    def test_stale_pressed_is_cleared_even_when_recovery_gate_waits(self):
        """清理不能被恢复门禁拦住（2026-10-16 第二轮真机实测的回归）。

        现场：日志里只有「退避等待中，剩余 6.0s」而没有清理行 —— 清理原先排在 permit() 之后，
        退避/冷却/上限/忙时延后任意一条拒绝就跳过 ⇒ 热键一直不恢复。清理是状态修复，
        没有注销重注册的空窗，必须先做；绑定重建仍由门禁决定。
        """
        self.backend._pressed_events_lock = threading.Lock()
        self.backend._pressed_events[77] = "held"
        self.register()
        requested = self.manager._registration_requested
        self.manager.recovery.next_at = time.monotonic() + 600      # 门禁：退避中，不允许重建
        with patch.object(hm, "stale_pressed_codes", return_value=(77,)), \
                patch.object(hm, "physically_up_codes", return_value=(77,)):
            self.manager.check_health()
            self.manager.check_health()
        self.assertEqual(self.manager.recovery.state, "waiting-backoff")
        self.assertNotIn(77, self.backend._pressed_events, "退避期间也必须清掉卡住的记录")
        self.assertEqual(self.manager._registration_requested, requested,
                         "门禁拒绝时不得顺带重建绑定")

    def test_stale_pressed_keeps_records_when_library_table_is_writable_only_later(self):
        """库表被钩子占用时不装作已恢复：不重建绑定、留痕，下一轮重试。"""
        self.backend._pressed_events_lock = threading.Lock()
        self.backend._pressed_events[77] = "held"
        self.register()
        requested = self.manager._registration_requested
        lock_holder = threading.Event()

        def hold():
            with self.backend._pressed_events_lock:
                lock_holder.wait(1)

        thread = threading.Thread(target=hold, name="isolated-hook-writer")
        thread.start()
        try:
            self.wait_for(lambda: self.backend._pressed_events_lock.locked())
            with patch.object(hm, "stale_pressed_codes", return_value=(77,)), \
                    patch.object(hm, "physically_up_codes", return_value=(77,)):
                self.manager.check_health()
                self.manager.check_health()
                self.manager.check_health()
            self.assertEqual(self.manager._registration_requested, requested,
                             "库表不可写时只重建绑定救不回来，必须等下一轮")
            self.assertIn(77, self.backend._pressed_events)
        finally:
            lock_holder.set()
            thread.join(timeout=2)

    def test_disabled_criterion_skips_automatic_cleanup_but_manual_recovery_clears(self):
        """关掉判据后不再自动清理；手动「重新注册」仍要能救回来（手动兜底的意义）。"""
        self.backend._pressed_events_lock = threading.Lock()
        self.backend._pressed_events[77] = "held"
        self.settings["hotkey_criterion_phantom"] = False
        self.register()
        requested = self.manager._registration_requested
        with patch.object(hm, "stale_pressed_codes", return_value=(77,)), \
                patch.object(hm, "physically_up_codes", return_value=(77,)):
            self.manager.check_health()
            self.manager.check_health()
            self.assertEqual(self.manager._registration_requested, requested)
            self.assertIn(77, self.backend._pressed_events)
            self.manager.manual_recover(self.settings)
        self.assertNotIn(77, self.backend._pressed_events)
        self.wait_for(lambda: self.manager._registration_requested > requested)


if __name__ == "__main__":
    unittest.main()

