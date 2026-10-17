# -*- coding: utf-8 -*-
"""运行权限与开机计划任务：命令构建、状态映射与设置页接线（不触发真实 UAC）。"""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from tests.base import CoreTests

from config.config_manager import ConfigManager, DEFAULTS, PRESERVED_ON_VERSION_RESET


class ElevationTests(CoreTests):
    def test_launch_command_is_frozen_aware_and_keeps_markers(self):
        from core.startup import launch_command

        with patch("core.startup.is_frozen", return_value=False):
            command = launch_command(["--elevated"])
            self.assertTrue(command[1].endswith("main.py"))
            self.assertEqual(command[-1], "--elevated")
        with patch("core.startup.is_frozen", return_value=True):
            self.assertEqual(launch_command(["--standard"]),
                             [sys.executable, "--standard"])

    def test_restart_as_admin_is_noop_when_already_elevated(self):
        from core import elevation

        with patch("core.elevation.is_elevated", return_value=True), \
                patch("core.elevation._shell_execute") as execute:
            self.assertEqual(elevation.restart_as_admin(), elevation.RESULT_ALREADY)
            execute.assert_not_called()

    def test_restart_as_admin_passes_marker_and_maps_result(self):
        from core import elevation

        with patch("core.elevation.is_elevated", return_value=False), \
                patch("core.elevation._shell_execute",
                      return_value=elevation.RESULT_CANCELLED) as execute:
            self.assertEqual(elevation.restart_as_admin(), elevation.RESULT_CANCELLED)
            command = execute.call_args[0][0]
            self.assertEqual(command[-1], elevation.ELEVATED_MARKER)

    def test_restart_as_standard_requires_elevation(self):
        from core import elevation

        with patch("core.elevation.is_elevated", return_value=False):
            self.assertEqual(elevation.restart_as_standard(), elevation.RESULT_ALREADY)

    def test_run_elevated_executes_directly_when_already_elevated(self):
        from core import elevation

        completed = Mock(returncode=0)
        with patch("core.elevation.is_elevated", return_value=True), \
                patch("core.elevation.subprocess.run", return_value=completed) as run:
            status, code = elevation.run_elevated(["schtasks", "/Query"])
        self.assertEqual((status, code), (elevation.RESULT_LAUNCHED, 0))
        run.assert_called_once()

    def test_sync_autostart_admin_mode_removes_run_entry(self):
        from core import autostart_admin

        data = {"start_on_boot": True, "start_on_boot_admin": True}
        with patch("core.autostart_admin.task_exists", return_value=True), \
                patch("core.autostart_admin.startup.remove_run_entry") as remove, \
                patch("core.autostart_admin.startup.set_start_on_boot") as set_run, \
                patch("core.autostart_admin.elevation.is_elevated", return_value=False):
            self.assertTrue(autostart_admin.sync_autostart(data))
        remove.assert_called_once()
        set_run.assert_not_called()

    def test_sync_autostart_refreshes_task_when_elevated(self):
        from core import autostart_admin

        data = {"start_on_boot": True, "start_on_boot_admin": True}
        with patch("core.autostart_admin.elevation.is_elevated", return_value=True), \
                patch("core.autostart_admin.install_task", return_value=(True, "已创建")) as install, \
                patch("core.autostart_admin.startup.remove_run_entry"):
            self.assertTrue(autostart_admin.sync_autostart(data))
        install.assert_called_once()

    def test_sync_autostart_standard_mode_restores_run_entry(self):
        from core import autostart_admin

        data = {"start_on_boot": True, "start_on_boot_admin": False}
        with patch("core.autostart_admin.task_exists", return_value=False), \
                patch("core.autostart_admin.startup.set_start_on_boot") as set_run:
            self.assertFalse(autostart_admin.sync_autostart(data))
        set_run.assert_called_once_with(True)

    def test_set_enabled_reports_cancelled_uac(self):
        from core import autostart_admin, elevation

        with patch("core.autostart_admin.task_exists", return_value=False), \
                patch("core.autostart_admin.elevation.run_elevated",
                      return_value=(elevation.RESULT_CANCELLED, -1)):
            ok, message = autostart_admin.set_enabled(True)
        self.assertFalse(ok)
        self.assertEqual(message, "cancelled")

    def test_set_enabled_install_success_removes_run_entry(self):
        from core import autostart_admin, elevation

        with patch("core.autostart_admin.task_exists", return_value=True), \
                patch("core.autostart_admin.elevation.run_elevated",
                      return_value=(elevation.RESULT_LAUNCHED, 0)), \
                patch("core.autostart_admin.startup.remove_run_entry") as remove:
            ok, message = autostart_admin.set_enabled(True)
        self.assertTrue(ok)
        self.assertEqual(message, "已创建")
        remove.assert_called_once()

    def test_helper_main_rejects_unknown_action(self):
        from core import autostart_admin

        self.assertEqual(autostart_admin.helper_main(["--admin-autostart", "boom"]), 1)

    def test_supervisor_drops_permission_markers_for_child(self):
        import supervisor

        self.assertEqual(supervisor.child_argv(["--elevated", "--crash-test", "3"]),
                         ["--crash-test", "3"])
        self.assertEqual(supervisor.child_argv(["--standard"]), [])

    def test_wait_for_previous_instance_returns_true_when_lock_is_free(self):
        import supervisor

        with tempfile.TemporaryDirectory() as folder, \
                patch("core.path_utils.data_dir", return_value=Path(folder)):
            self.assertTrue(supervisor.wait_for_previous_instance(timeout_ms=0))

    def test_config_exposes_admin_autostart_and_preserves_it(self):
        self.assertIs(DEFAULTS["start_on_boot_admin"], False)
        self.assertIn("start_on_boot_admin", PRESERVED_ON_VERSION_RESET)

    def test_settings_page_has_startup_mode_and_privilege_rows(self):
        with tempfile.TemporaryDirectory() as folder:
            settings = self._settings(Path(folder))
            try:
                page = settings.pages.widget(0)
                self.assertIn("start_on_boot", page.controls)
                self.assertIn("start_on_boot_admin", page.controls)
                self.assertIs(page.controls["start_on_boot"],
                              page.controls["start_on_boot_admin"])
                control = page.controls["start_on_boot"]
                self.assertEqual(control.value(), "off")
                self.assertTrue(control.toolTip().strip())
                self.assertTrue(page._privilege_button.text().strip())
                self.assertFalse(page._privilege_button.icon().isNull())
                with patch("core.elevation.is_elevated", return_value=False):
                    page._refresh_privilege_row()
                self.assertIn("管理员", page._privilege_button.text())
                with patch("core.elevation.is_elevated", return_value=True):
                    page._refresh_privilege_row()
                self.assertIn("标准", page._privilege_button.text())
            finally:
                settings.close()

    def test_startup_mode_admin_success_writes_config(self):
        with tempfile.TemporaryDirectory() as folder:
            settings = self._settings(Path(folder))
            try:
                page = settings.pages.widget(0)
                with patch("core.autostart_admin.set_enabled", return_value=(True, "已创建")), \
                        patch("ui.settings_window.set_start_on_boot"), \
                        patch("ui.settings_window.remove_run_entry"):
                    page._startup_mode_control._buttons["admin"].click()
                self.assertTrue(settings.config.data["start_on_boot"])
                self.assertTrue(settings.config.data["start_on_boot_admin"])
                self.assertEqual(page.controls["start_on_boot"].value(), "admin")
            finally:
                settings.close()

    def test_privilege_button_is_wide_enough_for_full_text(self):
        with tempfile.TemporaryDirectory() as folder:
            settings = self._settings(Path(folder))
            try:
                settings.show()
                self.app.processEvents()
                page = settings.pages.widget(0)
                button = page._privilege_button
                self.assertGreaterEqual(button.width(), button.sizeHint().width(),
                                        "按钮实际宽度不得小于完整文字所需的宽度")
                text_width = button.fontMetrics().horizontalAdvance(button.text())
                self.assertGreaterEqual(button.width(), text_width + 40,
                                        "按钮需为图标与内边距留出空间")
                self.assertGreaterEqual(page._privilege_status.width(),
                                        page._privilege_status.sizeHint().width())
            finally:
                settings.close()

    def test_privilege_restart_launches_then_quits(self):
        from PySide6.QtWidgets import QMessageBox
        from main import Application

        app = Application.__new__(Application)
        app.settings_window = Mock()
        app.logger = Mock()
        app.notify = Mock()
        app.quit_app = Mock()
        app.config = Mock()
        app.config.data = {"start_on_boot_admin": False}
        dialog = Mock()
        dialog.exec.return_value = QMessageBox.Yes
        with patch("main.is_elevated", return_value=False), \
                patch("main.restart_as_admin", return_value="launched"), \
                patch("main.yes_no_dialog", return_value=dialog):
            Application.request_privilege_restart(app)
        app.settings_window.flush_persist.assert_called_once()
        app.quit_app.assert_called_once()
        app.notify.assert_not_called()

    def test_privilege_restart_cancelled_uac_keeps_running(self):
        from PySide6.QtWidgets import QMessageBox
        from main import Application

        app = Application.__new__(Application)
        app.settings_window = Mock()
        app.logger = Mock()
        app.notify = Mock()
        app.quit_app = Mock()
        app.config = Mock()
        app.config.data = {"start_on_boot_admin": False}
        dialog = Mock()
        dialog.exec.return_value = QMessageBox.Yes
        with patch("main.is_elevated", return_value=False), \
                patch("main.restart_as_admin", return_value="cancelled"), \
                patch("main.yes_no_dialog", return_value=dialog):
            Application.request_privilege_restart(app)
        app.quit_app.assert_not_called()
        app.notify.assert_called_once_with("已取消管理员权限请求")

    def test_privilege_message_warns_about_persistent_admin_autostart(self):
        from PySide6.QtWidgets import QMessageBox
        from main import Application

        app = Application.__new__(Application)
        app.settings_window = Mock()
        app.logger = Mock()
        app.notify = Mock()
        app.quit_app = Mock()
        app.config = Mock()
        app.config.data = {"start_on_boot_admin": True}
        dialog = Mock()
        dialog.exec.return_value = QMessageBox.Yes
        with patch("main.is_elevated", return_value=True), \
                patch("main.restart_as_standard", return_value="launched"), \
                patch("main.yes_no_dialog", return_value=dialog) as ask:
            Application.request_privilege_restart(app)
        self.assertIn("下次登录", ask.call_args[0][2])
        self.assertIn("计划任务", ask.call_args[0][2])

    def test_full_reset_removes_admin_autostart_task(self):
        from main import Application

        app = Application.__new__(Application)
        app.reset_user_data_requested = False
        app.logger = Mock()
        app.qt = Mock()
        app.settings_window = Mock()
        app.config = Mock()
        app.config.data = {"start_on_boot_admin": True}
        with patch("main.set_start_on_boot"), \
                patch("core.autostart_admin.set_enabled", return_value=(True, "已删除")) as disable:
            self.assertTrue(Application.request_full_user_data_reset(app))
        disable.assert_called_once_with(False)
        app.qt.quit.assert_called_once_with()

    def test_tray_menu_offers_privilege_switch_with_boot_caveat(self):
        from ui.tray_menu import make_tray_menu

        calls = []
        menu = make_tray_menu(self.app, lambda: None, lambda: None, lambda: None,
                              switch_privilege=lambda: calls.append("switch"),
                              elevated=False, admin_autostart=True)
        action = next(item for item in menu.actions()
                      if item.text() == "以管理员身份重启")
        self.assertIn("只影响当前会话", action.toolTip())
        action.trigger()
        self.assertEqual(calls, ["switch"])

        menu = make_tray_menu(self.app, lambda: None, lambda: None, lambda: None,
                              switch_privilege=lambda: calls.append("switch"),
                              elevated=True, admin_autostart=True)
        action = next(item for item in menu.actions()
                      if item.text() == "以标准权限重启")
        self.assertIn("下次登录", action.toolTip())

    def test_tray_menu_separates_hotkeys_privilege_and_settings(self):
        """托盘菜单分组：热键维护 / 运行权限 / 设置 之间各有一条分隔线。"""
        from ui.tray_menu import make_tray_menu

        menu = make_tray_menu(self.app, lambda: None, lambda: None, lambda: None,
                              restore_hotkeys=lambda: None,
                              switch_privilege=lambda: None,
                              elevated=False)
        labels = ["---" if action.isSeparator() else action.text()
                  for action in menu.actions()]
        hotkey = labels.index("重新注册全局热键")
        privilege = labels.index("以管理员身份重启")
        settings = labels.index("设置")
        self.assertEqual(labels[hotkey + 1], "---")
        self.assertEqual(labels[privilege - 1], "---")
        self.assertEqual(labels[privilege + 1], "---")
        self.assertEqual(labels[settings - 1], "---")

        # 两个入口都不可用时不能留下连续分隔线（空组）。
        plain = make_tray_menu(self.app, lambda: None, lambda: None, lambda: None)
        flags = [action.isSeparator() for action in plain.actions()]
        self.assertFalse(any(first and second for first, second in zip(flags, flags[1:])))

    def test_startup_mode_admin_cancel_rolls_back(self):
        from PySide6.QtWidgets import QMessageBox

        with tempfile.TemporaryDirectory() as folder:
            settings = self._settings(Path(folder))
            try:
                page = settings.pages.widget(0)
                with patch("core.autostart_admin.set_enabled", return_value=(False, "cancelled")), \
                        patch("ui.settings_window.set_start_on_boot"), \
                        patch.object(QMessageBox, "information"):
                    page._startup_mode_control._buttons["admin"].click()
                self.assertFalse(settings.config.data["start_on_boot"])
                self.assertFalse(settings.config.data["start_on_boot_admin"])
                self.assertEqual(page.controls["start_on_boot"].value(), "off")
            finally:
                settings.close()

    def test_privilege_button_invokes_injected_callback(self):
        from ui.settings_window import SettingsWindow

        callback = Mock()
        with tempfile.TemporaryDirectory() as folder:
            settings = SettingsWindow(ConfigManager(Path(folder) / "settings.json"),
                                      elevate_callback=callback)
            try:
                settings.pages.widget(0)._switch_privilege()
                callback.assert_called_once()
            finally:
                settings.close()

    def _settings(self, folder):
        from ui.settings_window import SettingsWindow

        return SettingsWindow(ConfigManager(folder / "settings.json"))


if __name__ == "__main__":
    unittest.main()
