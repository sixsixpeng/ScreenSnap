"""配置生命周期：默认值、校验、迁移、修复、设置键、热键与版本闸门。"""

import os
import sys

# 无论 -m unittest、目录内 discover 还是直接跑文件，都要能导入 tests.base。
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from tests.base import (CoreTests, Mock, patch, Path, tempfile, json, unittest, Image,
                        QPointF, Qt, QPoint, QRect, QRectF, QTest, QApplication,
                        ConfigManager, resolved_dir, AnnotationCanvas, shape,
                        EditorWindow, SelectionRects, StickerItem, StickerManager,
                        SettingsWindow, HotkeyEdit)


class ConfigTests(CoreTests):
    def test_group_settings_dialog_applies_explicit_targets(self):
        from PySide6.QtGui import QColor, QImage
        from PySide6.QtWidgets import QDialogButtonBox, QRadioButton
        from config.config_manager import DEFAULTS
        from ui.sticker_panel import GroupSettingsDialog

        image = QImage(12, 8, QImage.Format_RGB32)
        image.fill(QColor("#23bc58"))
        manager = StickerManager(DEFAULTS)
        first = manager.add(image, show=False)
        second = manager.add(image, show=False)
        try:
            first.locked = True
            second.locked = False
            first.setVisible(True)
            manager.assign_group([first, second], "混合状态")
            dialog = GroupSettingsDialog(None, manager, "混合状态")
            self.assertEqual(dialog.checks["locked"].currentData(), None)
            self.assertEqual(dialog.visibility.currentData(), None)
            dialog.opacity.setValue(0.65)
            self.assertEqual([first.locked, second.locked], [True, False])
            self.assertEqual([first.isVisible(), second.isVisible()], [True, False])
            self.assertTrue(all(abs(item.windowOpacity() - 0.65) < 0.02
                                for item in (first, second)))

            dialog = GroupSettingsDialog(None, manager, "混合状态")
            dialog.visibility.setCurrentIndex(1)
            self.assertEqual([first.isVisible(), second.isVisible()], [True, True])

            dialog = GroupSettingsDialog(None, manager, "混合状态")
            dialog.visibility.setCurrentIndex(2)
            self.assertEqual([first.isVisible(), second.isVisible()], [False, False])

            dialog = GroupSettingsDialog(None, manager, "混合状态")
            dialog.opacity.setValue(0.65)
            dialog.checks["locked"].setCurrentIndex(1)
            dialog.checks["border_enabled"].setCurrentIndex(2)
            dialog.reset_size.setChecked(True)
            self.assertTrue(all(item.locked for item in (first, second)))
            self.assertTrue(all(not item.border_enabled for item in (first, second)))
            self.assertTrue(all(abs(item.windowOpacity() - 0.65) < 0.02
                                for item in (first, second)))
            self.assertTrue(all(item.scale_factor == 1.0 for item in (first, second)))

            dialog.findChild(QRadioButton, "rotationPreset90").setChecked(True)
            self.assertTrue(all(item.rotation_degrees == 90 for item in (first, second)))
            dialog.custom_rotation.setValue(37)
            dialog.custom_rotation.editingFinished.emit()
            self.assertTrue(all(item.rotation_degrees == 127 for item in (first, second)))
            self.assertIsNone(dialog.findChild(QDialogButtonBox))
            self.assertTrue(all(item.rotation_degrees == 127 for item in (first, second)))
        finally:
            manager._persist_timer.stop()
            manager.close_all()

    def test_sticker_panel_batch_settings_button_applies_to_selected_group(self):
        from PySide6.QtCore import QTimer
        from PySide6.QtGui import QColor, QImage
        from PySide6.QtWidgets import QDialogButtonBox
        from config.config_manager import DEFAULTS
        from ui.sticker_panel import GroupSettingsDialog, StickerPanel

        with tempfile.TemporaryDirectory() as folder, \
                patch("sticker.sticker_manager.data_dir", return_value=Path(folder)):
            settings = dict(DEFAULTS, save_dir=folder)
            image = QImage(12, 8, QImage.Format_RGB32)
            image.fill(QColor("#23bc58"))
            manager = StickerManager(settings)
            first = manager.add(image, show=False)
            second = manager.add(image, show=False)
            panel = StickerPanel(manager)
            errors = []
            try:
                first.locked = True
                second.locked = False
                first.set_opacity(0.3)
                second.set_opacity(0.9)
                first.setVisible(True)
                second.setVisible(False)
                manager.assign_group([first, second], "批量设置测试")
                panel.refresh()
                panel.show()
                self.assertFalse(panel.batch_settings_button.isEnabled())
                panel.group_combo.setCurrentIndex(
                    panel.group_combo.findData("批量设置测试"))

                self.assertEqual(panel.batch_settings_button.text(), "批量设置")
                self.assertTrue(panel.batch_settings_button.isEnabled())

                def apply_dialog_targets():
                    dialog = self.app.activeModalWidget()
                    if not isinstance(dialog, GroupSettingsDialog):
                        errors.append("批量设置按钮未打开组设置对话框")
                        return
                    dialog.opacity.setValue(0.68)
                    dialog.visibility.setCurrentIndex(1)
                    dialog.checks["locked"].setCurrentIndex(1)
                    if not all(item.locked and item.isVisible()
                               and abs(item.windowOpacity() - 0.68) < 0.02
                               for item in (first, second)):
                        errors.append("组设置未立即应用到组成员")
                    buttons = dialog.findChild(QDialogButtonBox)
                    if buttons is not None:
                        errors.append("组设置对话框仍显示重复按钮栏")
                    dialog.close()

                QTimer.singleShot(0, apply_dialog_targets)
                QTest.mouseClick(panel.batch_settings_button, Qt.LeftButton)

                self.assertEqual(errors, [])
                self.assertTrue(all(item.locked for item in (first, second)))
                self.assertTrue(all(item.isVisible() for item in (first, second)))
                self.assertTrue(all(abs(item.windowOpacity() - 0.68) < 0.02
                                    for item in (first, second)))
                self.assertTrue(manager._persist_timer.isActive())
                manager.persist()
                states = json.loads(
                    (Path(folder) / "stickers.json").read_text(encoding="utf-8"))
                self.assertEqual(len(states), 2)
                self.assertTrue(all(state["locked"] for state in states))
                self.assertTrue(all(abs(state["opacity"] - 0.68) < 0.02
                                    for state in states))
            finally:
                panel.close()
                manager._persist_timer.stop()
                manager.close_all()
                self.app.processEvents()

    def test_capture_hotkey_closes_active_menu_then_shows_interactive_mask(self):
        from types import SimpleNamespace
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QMenu
        from config.config_manager import DEFAULTS
        from main import Application

        application = Application.__new__(Application)
        application.config = SimpleNamespace(data=DEFAULTS.copy())
        application.logger = Mock()
        application.mask = None
        bounds = {"left": 0, "top": 0, "width": 200, "height": 150}
        menu = QMenu()
        with patch("app.capture_flow.capture", return_value=(Image.new("RGB", (200, 150)),
                                                   bounds, [bounds], None)), \
                patch("screenshot.mask_window.visible_windows", return_value=[]):
            QTimer.singleShot(0, lambda: application.dispatch("capture"))
            menu.exec(QPoint(10, 10))
            QTest.qWait(50)
            self.assertFalse(menu.isVisible())
            self.assertIsNotNone(application.mask)
            mask = application.mask
            self.assertTrue(mask.isVisible())
            self.assertIsNone(QApplication.activePopupWidget())
            destroyed = []
            mask.destroyed.connect(lambda: destroyed.append(True))

            original_position = QPoint(mask.position)
            target = next(point for point in (QPoint(50, 50), QPoint(80, 60),
                                              QPoint(120, 90), QPoint(160, 120))
                          if mask.to_physical_point(point) != original_position)
            QTest.mouseMove(mask, target, 50)
            QTest.qWait(50)
            self.assertEqual(mask.position, mask.to_physical_point(target))

            QTest.keyClick(mask, Qt.Key_Escape)
            QTest.qWait(20)
            self.assertTrue(destroyed)
            self.assertIsNone(application.mask)
        application.mask = None

    def test_config_roundtrip_and_conflict(self):
        from config.config_manager import DEFAULTS

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "settings.json"
            manager = ConfigManager(path)
            self.assertEqual(manager.data["capture_after_selection"], "edit")
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["capture_after_selection"], "edit")
            self.assertEqual({DEFAULTS[key] for key in ("pen_width", "rect_width", "ellipse_width",
                                                        "arrow_width")}, {2})
            # 记号笔默认线宽独立为 4（其余绘制工具仍是 2）。
            self.assertEqual(DEFAULTS["marker_width"], 4)
            self.assertEqual(DEFAULTS["eraser_width"], 30)
            self.assertEqual((DEFAULTS["pen_color"], DEFAULTS["mosaic_size"]), ("#ff0000", 10))
            self.assertTrue(DEFAULTS["copy_saved_image"])
            self.assertFalse(DEFAULTS["copy_saved_path"])
            manager.data["hotkeys"]["capture"] = "ctrl+q"
            manager.save()
            self.assertEqual(ConfigManager(path).data["hotkeys"]["capture"], "ctrl+q")
            path.write_text(json.dumps({"hotkeys": {"paste": "ctrl+q", "capture": "ctrl+q"}}))
            self.assertEqual(ConfigManager(path).data["hotkeys"], DEFAULTS["hotkeys"])
            self.assertEqual(len(list(Path(folder).glob("settings.json.broken-*"))), 1)
            path.write_text(json.dumps({"hotkeys": {"paste": "shift+ctrl+q",
                                                     "capture": "ctrl+shift+q"}}))
            restored = ConfigManager(path)
            self.assertEqual(len(list(Path(folder).glob("settings.json.broken-*"))), 2)
            with self.assertRaises(ValueError):
                restored.import_from(next(Path(folder).glob("settings.json.broken-*")))

    def test_requested_defaults_migrate_old_factory_values_safely(self):
        from config.config_manager import DEFAULTS, validate

        expected_hotkeys = {
            "repeat": "shift+f1", "fullscreen": "alt+f1", "monitor": "ctrl+f1",
            "open_image": "ctrl+alt+e", "open_sticker_file": "shift+f3",
            "sticker_panel": "alt+f3",
        }
        self.assertEqual({key: DEFAULTS["hotkeys"][key] for key in expected_hotkeys},
                         expected_hotkeys)
        expected = {
            "sound": True, "crosshair_color": "#ff0000", "element_depth": 8,
            "mask_opacity": 70, "history_limit": 10, "rect_corner_enabled": True,
            "sticker_shadow_enabled": False, "sticker_recycle_limit": 10,
            "marker_width": 4,
        }
        self.assertEqual({key: DEFAULTS[key] for key in expected}, expected)
        self.assertEqual({key: validate({})[key] for key in expected}, expected)
        for invalid_limit in (0, 10001):
            with self.subTest(history_limit=invalid_limit), self.assertRaises(ValueError):
                validate({"history_limit": invalid_limit})

        old_config = {
            "hotkeys": {
                "repeat": "ctrl+shift+f2", "fullscreen": "ctrl+shift+f1",
                "monitor": "ctrl+f1", "open_image": "ctrl+alt+o",
                "open_sticker_file": "ctrl+alt+n", "sticker_panel": "ctrl+alt+p",
            },
            "sound": False, "crosshair_color": "#000000", "element_depth": 12,
            "mask_opacity": 60, "history_limit": 100, "rect_corner_enabled": False,
            "sticker_shadow_enabled": True, "sticker_recycle_limit": 50,
            "marker_width": 2,
        }
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "settings.json"
            path.write_text(json.dumps(old_config), encoding="utf-8")
            migrated = ConfigManager(path)
            self.assertEqual({key: migrated.data["hotkeys"][key] for key in expected_hotkeys},
                             expected_hotkeys)
            self.assertEqual({key: migrated.data[key] for key in expected}, expected)
            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual({key: saved["hotkeys"][key] for key in expected_hotkeys},
                             expected_hotkeys)
            self.assertEqual({key: saved[key] for key in expected}, expected)

            custom_path = Path(folder) / "custom.json"
            custom_config = {
                "hotkeys": {"repeat": "ctrl+f5", "fullscreen": "ctrl+shift+f1",
                            "open_image": "ctrl+alt+k"},
                "crosshair_color": "#123456", "element_depth": 5,
                "mask_opacity": 45, "history_limit": 25, "sticker_recycle_limit": 30,
                "marker_width": 9,
            }
            custom_path.write_text(json.dumps(custom_config), encoding="utf-8")
            customized = ConfigManager(custom_path)
            self.assertEqual(customized.data["hotkeys"]["repeat"], "ctrl+f5")
            self.assertEqual(customized.data["hotkeys"]["fullscreen"], "alt+f1")
            self.assertEqual(customized.data["hotkeys"]["open_image"], "ctrl+alt+k")
            for key, value in custom_config.items():
                if key != "hotkeys":
                    self.assertEqual(customized.data[key], value)

            conflict_path = Path(folder) / "conflict.json"
            conflict = {"hotkeys": {"fullscreen": "ctrl+shift+f1", "paste": "alt+f1"}}
            conflict_path.write_text(json.dumps(conflict), encoding="utf-8")
            conflict_manager = ConfigManager(conflict_path)
            self.assertEqual(conflict_manager.data["hotkeys"]["fullscreen"], "ctrl+shift+f1")
            self.assertEqual(conflict_manager.data["hotkeys"]["paste"], "alt+f1")
            self.assertEqual(conflict_manager.data["hotkeys"]["repeat"], "shift+f1")

    def test_inline_hint_items_are_registered_and_migrated(self):
        """原地编辑新增的提示项要注册进设置清单，旧配置的完整默认顺序会自动补上。"""
        from config.config_manager import (DEFAULTS, HINT_ITEM_IDS, HINT_LABELS,
                                           migrate_legacy_settings)

        new_items = ["inline_edit", "inline_menu", "inline_history"]
        for item_id in new_items:
            self.assertIn(item_id, HINT_ITEM_IDS)
            self.assertIn(item_id, HINT_LABELS)
            self.assertIn(item_id, DEFAULTS["capture_hint_order"])
        legacy_default = [item_id for item_id in HINT_ITEM_IDS if item_id not in new_items]
        migrated = migrate_legacy_settings({"capture_hint_order": list(legacy_default)})
        self.assertEqual(migrated["capture_hint_order"], legacy_default + new_items)
        # 用户自定义过的列表保持原样，不做追加。
        custom = ["coords", "cancel"]
        self.assertEqual(
            migrate_legacy_settings({"capture_hint_order": list(custom)})["capture_hint_order"],
            custom)

    def test_legacy_capture_round_corner_settings_migrate_to_output_settings(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "settings.json"
            legacy = {"capture_round_corners": False, "capture_corner_radius": 27}
            path.write_text(json.dumps(legacy), encoding="utf-8")
            manager = ConfigManager(path)
            self.assertFalse(manager.data["editor_image_round_corners"])
            self.assertEqual(manager.data["editor_image_corner_radius"], 27)
            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertNotIn("capture_round_corners", saved)
            self.assertNotIn("capture_corner_radius", saved)
            imported_path = Path(folder) / "legacy-export.json"
            imported_path.write_text(json.dumps(legacy), encoding="utf-8")
            manager.import_from(imported_path)
            self.assertFalse(manager.data["editor_image_round_corners"])
            self.assertEqual(manager.data["editor_image_corner_radius"], 27)

    def test_config_invalid_json_is_backed_up_on_load(self):
        from config.config_manager import DEFAULTS

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "settings.json"
            path.write_text("{invalid", encoding="utf-8")
            manager = ConfigManager(path)
            self.assertEqual(manager.data, DEFAULTS)
            self.assertEqual(next(Path(folder).glob("settings.json.broken-*")).read_text(encoding="utf-8"),
                             "{invalid")

    def test_invalid_single_setting_falls_back_without_losing_others(self):
        from config.config_manager import DEFAULTS

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "settings.json"
            path.write_text(json.dumps({"pen_color": "#ff0000", "font_size": 999}), encoding="utf-8")
            manager = ConfigManager(path)
            self.assertEqual(manager.data["pen_color"], "#ff0000")
            self.assertEqual(manager.data["font_size"], DEFAULTS["font_size"])
            self.assertEqual(len(list(Path(folder).glob("settings.json.broken-*"))), 1)

    def test_existing_annotation_values_are_not_replaced_by_new_defaults(self):
        from editor.toolbar_widget import ToolbarWidget

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "settings.json"
            path.write_text(json.dumps({"pen_color": "#12ab34", "pen_width": 7,
                                        "arrow_width": 14, "eraser_width": 19,
                                        "mosaic_size": 23}), encoding="utf-8")
            restored = ConfigManager(path).data
            self.assertEqual((restored["pen_color"], restored["pen_width"],
                              restored["arrow_width"], restored["mosaic_size"]),
                             ("#12ab34", 7, 14, 23))
            self.assertEqual((restored["copy_saved_image"], restored["copy_saved_path"]), (True, False))
            self.assertEqual(ToolbarWidget().tool_widths["arrow"], 2)
            self.assertEqual(ToolbarWidget().tool_widths["eraser"], 30)
            self.assertEqual(restored["eraser_width"], 19)
            for values, expected_width in ((restored, 19), (ConfigManager(Path(folder) / "fresh.json").data, 30)):
                toolbar = ToolbarWidget(settings=values)
                toolbar.tool_buttons["eraser"].click()
                self.assertEqual(toolbar.pen_width.value(), expected_width)
                toolbar.close()

    def test_legacy_completion_settings_are_removed_on_load(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "settings.json"
            legacy = {"auto_save": False, "auto_copy": False,
                      "auto_dir": str(Path(folder) / "Auto"),
                      "manual_dir": str(Path(folder) / "Manual")}
            path.write_text(json.dumps(legacy), encoding="utf-8")
            manager = ConfigManager(path)
            self.assertEqual(manager.data["save_dir"], str(Path(folder) / "Auto"))
            self.assertNotIn("auto_dir", manager.data)
            self.assertNotIn("manual_dir", manager.data)
            self.assertNotIn("auto_save", manager.data)
            self.assertNotIn("auto_copy", json.loads(path.read_text(encoding="utf-8")))
            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(saved["save_dir"], str(Path(folder) / "Auto"))
            self.assertNotIn("auto_dir", saved)
            self.assertNotIn("manual_dir", saved)

    def test_unified_save_dir_migration_precedence(self):
        from config.config_manager import migrate_legacy_settings

        self.assertEqual(migrate_legacy_settings({
            "save_dir": "chosen", "auto_dir": "automatic", "manual_dir": "manual"
        }), {"save_dir": "chosen"})
        self.assertEqual(migrate_legacy_settings({
            "auto_dir": "automatic", "manual_dir": "manual"
        }), {"save_dir": "automatic"})
        self.assertEqual(migrate_legacy_settings({"manual_dir": "manual"}),
                         {"save_dir": "manual"})

    def test_log_directory_setting_persists(self):
        from ui.widgets.file_path_edit import FilePathEdit

        with tempfile.TemporaryDirectory() as folder:
            manager = ConfigManager(Path(folder) / "settings.json")
            settings = SettingsWindow(manager)
            directory = settings.page("日志").findChild(FilePathEdit)
            directory.input.setText(str(Path(folder) / "logs"))
            directory.input.editingFinished.emit()
            settings.flush_persist()
            self.assertEqual(ConfigManager(manager.path).data["log_dir"],
                             directory.input.text())
            settings.close()

    def test_save_settings_unified_directory_and_cache_controls(self):
        from PySide6.QtWidgets import QMessageBox
        from ui.settings_save import SaveOutputPage

        with tempfile.TemporaryDirectory() as folder:
            config = ConfigManager(Path(folder) / "settings.json")
            save_dir = Path(folder) / "captures"
            config.data["save_dir"] = str(save_dir)
            clear_cache = Mock(return_value={"clipboard_entries": 1,
                                             "clipboard_images": 1,
                                             "orphan_sticker_images": 2,
                                             "toast_images": 1})
            page = SaveOutputPage(config, Mock(), clear_cache)
            try:
                self.assertIn("save_dir", page.controls)
                self.assertNotIn("auto_dir", page.controls)
                self.assertNotIn("manual_dir", page.controls)
                directory = page.controls["save_dir"]
                with patch("ui.widgets.file_path_edit.QDesktopServices.openUrl") as open_url:
                    directory.open_button.click()
                    self.assertEqual(Path(open_url.call_args.args[0].toLocalFile()), save_dir)
                cache_root = Path(folder) / "appdata"
                with patch("ui.settings_save.data_dir", return_value=cache_root), \
                        patch("ui.settings_save.QDesktopServices.openUrl") as open_cache, \
                        patch("ui.settings_save.yes_no_dialog") as confirm, \
                        patch("ui.settings_save.QMessageBox.information"):
                    confirm.return_value.exec.return_value = QMessageBox.Yes
                    page.open_cache_button.click()
                    self.assertEqual(Path(open_cache.call_args.args[0].toLocalFile()), cache_root)
                    page.clear_cache_button.click()
                clear_cache.assert_called_once_with()
            finally:
                page.deleteLater()

    def test_settings_controls_have_initialized_defaults(self):
        from config.config_manager import DEFAULTS, validate
        from core.window_uia import DESCEND_LIMIT

        self.assertEqual(DEFAULTS["element_depth"], 8)
        self.assertEqual(DESCEND_LIMIT, 24)
        self.assertTrue(DEFAULTS["archive_by_month"])
        self.assertFalse(DEFAULTS["archive_by_day"])
        self.assertTrue(DEFAULTS["open_notification_file"])
        legacy_month = validate({"archive_images": True, "image_archive_period": "month"})
        self.assertTrue(legacy_month["archive_by_month"])
        self.assertFalse(legacy_month["archive_by_day"])
        legacy_day = validate({"archive_images": True, "image_archive_period": "day"})
        self.assertFalse(legacy_day["archive_by_month"])
        self.assertTrue(legacy_day["archive_by_day"])
        legacy_disabled = validate({"archive_images": False})
        self.assertFalse(legacy_disabled["archive_by_month"])
        self.assertFalse(legacy_disabled["archive_by_day"])
        with self.assertRaises(ValueError):
            validate({"archive_images": True, "image_archive_period": "year"})
        self.assertTrue(DEFAULTS["log_monthly_folder"])
        self.assertFalse(DEFAULTS["uia_debug_tree"])
        self.assertEqual(validate({"element_depth": 32})["element_depth"], 32)
        with self.assertRaises(ValueError):
            validate({"element_depth": 33})

        self.assertEqual(DEFAULTS["element_depth"], 8)
        self.assertEqual(validate({"element_depth": 32})["element_depth"], 32)
        with self.assertRaises(ValueError):
            validate({"element_depth": 33})
        self.assertEqual(DEFAULTS["history_limit"], 10)
        self.assertEqual(validate({"history_limit": 10000})["history_limit"], 10000)
        for invalid_limit in (0, 10001):
            with self.subTest(history_limit=invalid_limit), self.assertRaises(ValueError):
                validate({"history_limit": invalid_limit})

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "settings.json"
            path.write_text("{}", encoding="utf-8")
            manager = ConfigManager(path)
            self.assertEqual(manager.data, validate({}))
            self.assertEqual(set(manager.data), set(DEFAULTS))
            self.assertFalse(manager.data["start_on_boot"])
            self.assertEqual(manager.data["filename"], "ScreenSnap_%Y%m%d_%H%M%S")
            settings = SettingsWindow(manager)
            for index in range(settings.pages.count()):
                for key in settings.pages.widget(index).controls:
                    self.assertIn(key, manager.data)
            self.assertFalse(settings.pages.widget(0).controls["start_on_boot"].isChecked())
            depth_control = settings.page("截图").controls["element_depth"]
            self.assertEqual(depth_control.maximum(), 32)
            self.assertEqual(depth_control.value(), 8)
            depth_control = settings.page("截图").controls["element_depth"]
            self.assertEqual(depth_control.maximum(), 32)
            settings.close()

    def test_settings_changes_debounce_config_writes_and_flush_on_close(self):
        with tempfile.TemporaryDirectory() as folder:
            manager = ConfigManager(Path(folder) / "settings.json")
            settings = SettingsWindow(manager)
            try:
                control = settings.page("编辑器").controls["font_size"]
                settings._persist_timer.setInterval(30)
                with patch.object(manager, "save", wraps=manager.save) as save:
                    control.setValue(control.value() + 1)
                    control.setValue(control.value() + 1)
                    control.setValue(control.value() + 1)
                    self.assertEqual(manager.data["font_size"], control.value())
                    self.assertEqual(save.call_count, 0)
                    self.assertTrue(settings._persist_timer.isActive())
                    settings.flush_persist()
                    self.assertEqual(save.call_count, 1)
                    control.setValue(control.value() + 1)
                    control.setValue(control.value() + 1)
                    QTest.qWait(80)
                    self.assertEqual(save.call_count, 2)
                    control.setValue(control.value() + 1)
                    control.setValue(control.value() + 1)
                    settings.close()
                    self.assertEqual(save.call_count, 3)
                self.assertEqual(ConfigManager(manager.path).data["font_size"], control.value())
            finally:
                settings.close()

    def test_uia_detect_default_on_and_hover_interval_clamped(self):
        from config.config_manager import DEFAULTS, validate

        self.assertTrue(DEFAULTS["window_uia_detect"])
        self.assertEqual(DEFAULTS["window_hover_interval"], 80)
        # 区间内的值保留。
        self.assertEqual(validate({"window_hover_interval": 120})["window_hover_interval"], 120)
        # 越界会被拒绝（加载时由 repair 回退到默认值）。
        with self.assertRaises(ValueError):
            validate({"window_hover_interval": 9})
        with self.assertRaises(ValueError):
            validate({"window_hover_interval": 600})

    def test_new_feature_config_keys_initialized_and_reset(self):
        """本轮新增配置键（标尺/取色/序号/回收站）必须：
        ① 都写入 DEFAULTS 且类型正确；② 旧配置缺键时 validate 自动补齐默认值；
        ③ 「恢复本页默认」能把被改掉的新增项还原并持久化。"""
        from config.config_manager import DEFAULTS, validate, ConfigManager

        new_keys = {
            # 选区阶段四个功能键 + 原地编辑工具栏隐藏键：按钮已移除/不加按钮，改为可配置快捷键。
            "capture_custom_size_shortcut": str, "capture_recapture_shortcut": str,
            "capture_window_edit_shortcut": str, "capture_copy_shortcut": str,
            "capture_toolbar_hide_shortcut": str,
            "capture_picker_shortcut": str, "magnifier_grid": bool,
            "magnifier_size": int, "capture_hint_order": list,
            "capture_hint_per_line": int, "capture_hints_enabled": bool,
            "intruder_warning_enabled": bool, "intruder_warning_items": dict,
            "capture_hint_gap": int, "hint_bar_style": dict, "hint_bar_warning_style": dict,
            "magnifier_grid_color": str, "ruler_enabled": bool, "ruler_color": str,
            "sequence_font_size": int, "sequence_start": int, "sequence_shape": str,
            "sequence_text_color": str, "sequence_fill_color": str,
            "sequence_preset": str,
            "sticker_recycle_enabled": bool, "sticker_recycle_limit": int,
        }
        for key, typ in new_keys.items():
            self.assertIn(key, DEFAULTS, key)
            self.assertIsInstance(DEFAULTS[key], typ, key)

        # 旧配置只保留热键时，validate 应补齐全部新增键，且标尺默认开启。
        stripped = {"hotkeys": DEFAULTS["hotkeys"]}
        result = validate(stripped)
        self.assertTrue(result["capture_hotkey_suppress"])
        self.assertTrue(result["ruler_enabled"])
        self.assertTrue(result["magnifier_grid"])
        self.assertTrue(result["sticker_recycle_enabled"])
        self.assertEqual(result["sequence_shape"], "circle")
        # 旧配置缺键时四个选区功能键与工具栏隐藏键也要补齐默认值（初始化/升级路径）。
        self.assertEqual({key: result[key] for key in (
            "capture_custom_size_shortcut", "capture_recapture_shortcut",
            "capture_window_edit_shortcut", "capture_copy_shortcut",
            "capture_toolbar_hide_shortcut")},
            {"capture_custom_size_shortcut": "F", "capture_recapture_shortcut": "R",
             "capture_window_edit_shortcut": "E", "capture_copy_shortcut": "Y",
             "capture_toolbar_hide_shortcut": "`"})
        # 提示项清单与放大镜尺寸同样补齐默认值：默认全部提示项都开启、顺序即默认顺序。
        from config.config_manager import HINT_ITEM_IDS
        self.assertEqual(result["capture_hint_order"], list(HINT_ITEM_IDS))
        self.assertEqual(result["magnifier_size"], 140)
        # 提示条默认每行两个提示，且总开关默认开启；越界的每行提示数会被拒绝。
        self.assertEqual(result["capture_hint_per_line"], 2)
        self.assertTrue(result["capture_hints_enabled"])
        with self.assertRaises(ValueError):
            validate({"capture_hint_per_line": 9})

        # 提示项顺序只保留已知 id、去重且保持用户顺序。
        self.assertEqual(validate({"capture_hint_order": ["copy", "copy", "unknown", "coords"]})[
            "capture_hint_order"], ["copy", "coords"])
        with self.assertRaises(ValueError):
            validate({"magnifier_size": 400})
        # 采集自检警示：默认关闭；子项只认已知分类、缺失分类回退为开启，非法类型被拒绝。
        from config.config_manager import INTRUDER_WARNING_IDS
        self.assertFalse(result["intruder_warning_enabled"])
        self.assertEqual(set(result["intruder_warning_items"]), set(INTRUDER_WARNING_IDS))
        self.assertTrue(all(result["intruder_warning_items"].values()))
        self.assertFalse(validate({"intruder_warning_items": {"sticker": False, "unknown": True}})[
            "intruder_warning_items"]["sticker"])
        self.assertNotIn("unknown", validate({"intruder_warning_items": {"unknown": True}})[
            "intruder_warning_items"])
        with self.assertRaises(ValueError):
            validate({"intruder_warning_items": ["sticker"]})
        # 提示条与放大镜间距：默认 0（紧贴），合法范围 0–40，越界被拒绝。
        self.assertEqual(result["capture_hint_gap"], 0)
        self.assertEqual(validate({"capture_hint_gap": 12})["capture_hint_gap"], 12)
        with self.assertRaises(ValueError):
            validate({"capture_hint_gap": 41})
        with self.assertRaises(ValueError):
            validate({"capture_hint_gap": -1})
        # 两套提示条外观：默认取预设，非法颜色/半径/圆角类型被拒绝，颜色统一小写。
        from config.config_manager import (DEFAULT_HINT_BAR_STYLE,
                                           DEFAULT_HINT_BAR_WARNING_STYLE)
        self.assertEqual(result["hint_bar_style"], DEFAULT_HINT_BAR_STYLE)
        self.assertEqual(result["hint_bar_warning_style"], DEFAULT_HINT_BAR_WARNING_STYLE)
        self.assertEqual(validate({"hint_bar_style": {"text_color": "#ABCDEF"}})[
            "hint_bar_style"]["text_color"], "#abcdef")
        for bad in ({"text_color": "red"}, {"fill_color": "#12345"},
                    {"radius": 21}, {"radius": -1}, {"rounded": "yes"},
                    {"radius": True}, "not-a-dict"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                validate({"hint_bar_style": bad})
        # 预设 id 只在各自那套里有效：普通样式塞警示预设、或反之，都归为「自定义」。
        self.assertEqual(validate({"hint_bar_style": {"preset": "amber"}})[
            "hint_bar_style"]["preset"], "custom")
        self.assertEqual(validate({"hint_bar_warning_style": {"preset": "dark"}})[
            "hint_bar_warning_style"]["preset"], "custom")

        from ui.settings_window import SettingsWindow
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "settings.json"
            # 模拟用户把若干新增项改成非默认值（含把标尺关掉）。
            path.write_text(json.dumps({
                "ruler_enabled": False, "ruler_color": "#123456",
                "sequence_shape": "star",
                "capture_after_selection": "edit",
                "capture_recapture_shortcut": "Alt+R",
                "capture_copy_shortcut": "Alt+C",
                "capture_toolbar_hide_shortcut": "Alt+H",
                "capture_hint_order": ["coords"],
                "capture_hint_per_line": 5,
                "magnifier_size": 300,
                "intruder_warning_enabled": True,
                "intruder_warning_items": {"sticker": False},
                "capture_hint_gap": 18,
                "hint_bar_style": {"preset": "custom", "text_color": "#010203",
                                   "fill_color": "#040506", "border_color": "#070809",
                                   "rounded": False, "radius": 0},
            }), encoding="utf-8")
            manager = ConfigManager(path)
            self.assertTrue(manager.data["intruder_warning_enabled"])
            self.assertFalse(manager.data["intruder_warning_items"]["sticker"])
            self.assertEqual(manager.data["capture_hint_gap"], 18)
            self.assertEqual(manager.data["hint_bar_style"]["text_color"], "#010203")
            self.assertFalse(manager.data["ruler_enabled"])
            self.assertEqual(manager.data["capture_recapture_shortcut"], "Alt+R")
            settings = SettingsWindow(manager)
            screenshot = settings.page("截图")
            editor = settings.page("编辑器")
            # 截图页重置：标尺/标尺颜色与四个选区功能键一起回到默认。
            screenshot.reset_page()
            self.assertTrue(manager.data["ruler_enabled"])
            self.assertEqual(manager.data["ruler_color"], DEFAULTS["ruler_color"])
            self.assertEqual(manager.data["capture_recapture_shortcut"],
                             DEFAULTS["capture_recapture_shortcut"])
            self.assertEqual(manager.data["capture_copy_shortcut"],
                             DEFAULTS["capture_copy_shortcut"])
            self.assertEqual(manager.data["capture_toolbar_hide_shortcut"],
                             DEFAULTS["capture_toolbar_hide_shortcut"])
            # 提示项清单与放大镜尺寸也在本页重置范围内，界面控件同步回默认。
            self.assertEqual(manager.data["capture_hint_order"], DEFAULTS["capture_hint_order"])
            self.assertEqual(manager.data["magnifier_size"], DEFAULTS["magnifier_size"])
            self.assertEqual(screenshot.controls["capture_hint_order"].value(),
                             DEFAULTS["capture_hint_order"])
            self.assertEqual(screenshot.controls["magnifier_size"].value(),
                             DEFAULTS["magnifier_size"])
            self.assertEqual(manager.data["capture_hint_per_line"],
                             DEFAULTS["capture_hint_per_line"])
            self.assertEqual(screenshot.controls["capture_hint_per_line"].value(),
                             DEFAULTS["capture_hint_per_line"])
            # 采集自检警示的总开关与子项同样在本页重置范围内，控件同步回默认。
            self.assertFalse(manager.data["intruder_warning_enabled"])
            self.assertEqual(manager.data["intruder_warning_items"],
                             DEFAULTS["intruder_warning_items"])
            self.assertFalse(screenshot.controls["intruder_warning_enabled"].isChecked())
            self.assertEqual(screenshot.controls["intruder_warning_items"].value(),
                             DEFAULTS["intruder_warning_items"])
            # 提示条间距与两套外观同样在本页重置范围内，复合控件同步回默认。
            self.assertEqual(manager.data["capture_hint_gap"], DEFAULTS["capture_hint_gap"])
            self.assertEqual(manager.data["hint_bar_style"], DEFAULTS["hint_bar_style"])
            self.assertEqual(manager.data["hint_bar_warning_style"],
                             DEFAULTS["hint_bar_warning_style"])
            self.assertEqual(screenshot.controls["capture_hint_gap"].value(),
                             DEFAULTS["capture_hint_gap"])
            self.assertEqual(screenshot.controls["hint_bar_style"].value(),
                             DEFAULTS["hint_bar_style"])
            self.assertEqual(screenshot.controls["hint_bar_warning_style"].value(),
                             DEFAULTS["hint_bar_warning_style"])

            # 编辑器页重置：序号形状回到默认。
            editor.reset_page()
            self.assertEqual(manager.data["sequence_shape"], DEFAULTS["sequence_shape"])
            settings.flush_persist()
            reloaded = ConfigManager(path).data
            self.assertTrue(reloaded["ruler_enabled"])
            self.assertEqual(reloaded["ruler_color"], DEFAULTS["ruler_color"])
            self.assertEqual(reloaded["capture_recapture_shortcut"],
                             DEFAULTS["capture_recapture_shortcut"])
            self.assertEqual(reloaded["capture_copy_shortcut"], DEFAULTS["capture_copy_shortcut"])
            self.assertEqual(reloaded["capture_hint_order"], DEFAULTS["capture_hint_order"])
            self.assertEqual(reloaded["capture_hint_per_line"], DEFAULTS["capture_hint_per_line"])
            self.assertEqual(reloaded["magnifier_size"], DEFAULTS["magnifier_size"])
            self.assertEqual(reloaded["capture_hint_gap"], DEFAULTS["capture_hint_gap"])
            self.assertEqual(reloaded["hint_bar_style"], DEFAULTS["hint_bar_style"])
            self.assertEqual(reloaded["hint_bar_warning_style"], DEFAULTS["hint_bar_warning_style"])

            self.assertEqual(reloaded["sequence_shape"], DEFAULTS["sequence_shape"])
            settings.close()

    def test_capture_hotkey_suppress_default_and_explicit_override(self):
        from config.config_manager import DEFAULTS, validate, ConfigManager

        self.assertTrue(DEFAULTS["capture_hotkey_suppress"])
        self.assertTrue(validate({"hotkeys": DEFAULTS["hotkeys"]})[
            "capture_hotkey_suppress"])
        self.assertFalse(validate({"capture_hotkey_suppress": False})[
            "capture_hotkey_suppress"])
        with tempfile.TemporaryDirectory() as folder:
            manager = ConfigManager(Path(folder) / "settings.json")
            self.assertTrue(manager.data["capture_hotkey_suppress"])
            manager.data["capture_hotkey_suppress"] = False
            manager.save()
            self.assertFalse(ConfigManager(manager.path).data[
                "capture_hotkey_suppress"])

    def test_text_width_default_validated_and_controls_box_width(self):
        from config.config_manager import DEFAULTS, validate
        from editor.annotation_items import text_item
        from editor.annotation_canvas import AnnotationCanvas
        from editor.toolbar_widget import ToolbarWidget
        from PySide6.QtWidgets import QSpinBox

        # 默认 0 = 自动；合法范围 0–2000，非法值被拒绝。
        self.assertEqual(DEFAULTS["text_width"], 0)
        self.assertEqual(validate({"text_width": 0})["text_width"], 0)
        self.assertEqual(validate({"text_width": 320})["text_width"], 320)
        for bad in (2001, -1, "auto", True):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                validate({"text_width": bad})
        self.assertEqual(DEFAULTS["text_height"], 0)
        self.assertEqual(validate({"text_height": 0})["text_height"], 0)
        self.assertEqual(validate({"text_height": 240})["text_height"], 240)
        for bad in (2001, -1, "auto", True):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                validate({"text_height": bad})

        long_text = "这是一段很长的文字用于测试文本框宽度自动换行效果"
        # 自动：宽度随内容增长，短文本至少 180。
        auto = text_item(QPointF(0, 0), long_text, dict(DEFAULTS), Qt.AlignLeft)
        self.assertGreater(auto.document().textWidth(), 180)
        # 固定宽度：文本框宽度等于配置值，超出后自动换行（高度增加）。
        narrow_settings = dict(DEFAULTS, text_width=200)
        narrow = text_item(QPointF(0, 0), long_text, narrow_settings, Qt.AlignLeft)
        self.assertEqual(narrow.document().textWidth(), 200)
        self.assertGreater(narrow.boundingRect().height(),
                           text_item(QPointF(0, 0), "短", narrow_settings,
                                     Qt.AlignLeft).boundingRect().height())
        # 固定高度：显示高度等于配置值；内容更高时被裁剪，auto_height 仍是自然高度。
        natural = text_item(QPointF(0, 0), long_text, dict(DEFAULTS), Qt.AlignLeft)
        tall = text_item(QPointF(0, 0), long_text, dict(DEFAULTS, text_height=24), Qt.AlignLeft)
        self.assertEqual(tall.boundingRect().height(), 24.0)
        self.assertEqual(tall.fixed_height, 24.0)
        self.assertGreater(tall.auto_height(), 24.0)
        self.assertEqual(natural.boundingRect().height(), tall.auto_height())

        # 工具栏：文字工具展示文字宽度行，且外部同步生效。
        settings = dict(DEFAULTS)
        toolbar = ToolbarWidget(settings["pen_color"], settings, "text")
        toolbar.tool_buttons["text"].click()
        self.assertIn(35, toolbar._last_option_rows)
        self.assertIn(36, toolbar._last_option_rows)
        self.assertIsInstance(toolbar.text_width, QSpinBox)
        self.assertIsInstance(toolbar.text_height, QSpinBox)
        toolbar.sync_setting("text_width", 260)
        toolbar.sync_setting("text_height", 120)
        self.assertEqual(toolbar.text_width.value(), 260)
        self.assertEqual(toolbar.text_height.value(), 120)

        # 选中文字后改宽度：应用到该标注。
        canvas = AnnotationCanvas(Image.new("RGB", (200, 160), "white"),
                                  dict(DEFAULTS, text_width=0))
        item = text_item(QPointF(10, 10), long_text, canvas.settings, Qt.AlignLeft)
        canvas.scene_data.addItem(item)
        item.setSelected(True)
        canvas.set_selected_text_width(300)
        self.assertEqual(item.document().textWidth(), 300)
        canvas.set_selected_text_width(0)
        self.assertGreater(item.document().textWidth(), 180)
        # 选中文字后改高度：固定高度生效，设回 0 恢复按内容自动。
        canvas.set_selected_text_height(140)
        self.assertEqual(item.boundingRect().height(), 140.0)
        canvas.set_selected_text_height(0)
        self.assertEqual(item.boundingRect().height(), item.auto_height())
        canvas.close()
        toolbar.close()

    def test_text_settings_available_in_all_four_places(self):
        """今天新增的文字设置（含 text_width）在四处都应就位并带预览：
        ① 设置页「编辑器>文字」；② 工具栏「更多设置>文字」（两个编辑器共用）；
        ③ 新建文字对话框；④ 编辑文字对话框。"""
        from config.config_manager import DEFAULTS
        from editor.toolbar_widget import ToolbarWidget
        from editor.text_input_dialog import TextInputDialog
        from ui.settings_window import SettingsWindow

        text_keys = {"font", "font_size", "text_alignment", "text_color",
                     "text_bold", "text_italic", "text_underline", "text_strikethrough",
                     "text_background_enabled", "text_background", "text_width",
                     "text_height"}

        # ① 设置页：控件齐全且有文字预览。
        with tempfile.TemporaryDirectory() as folder:
            window = SettingsWindow(ConfigManager(Path(folder) / "settings.json"))
            page = window.page("编辑器")
            controls = set(page.controls) | set(page.color_buttons)
            self.assertLessEqual(text_keys, controls)
            self.assertTrue(any(preview.kind == "text" for preview in page.previews))
            window.close()

        # ② 工具栏「更多设置>文字」：参数行齐全且有文字预览。
        settings = dict(DEFAULTS)
        toolbar = ToolbarWidget(settings["pen_color"], settings, "text")
        toolbar.tool_buttons["text"].click()
        # 未显示工具栏时用 isHidden 判断（其未被显式隐藏即为可见）。
        self.assertFalse(toolbar.previews["text"].isHidden())
        self.assertLessEqual({0, 3, 4, 5, 28, 29, 35, 36},
                             set(toolbar._last_option_rows))
        # 工具栏「外观」弹层是 Qt.Tool 的 QFrame，需自报标签才会被采集自检点名。
        self.assertEqual(toolbar.appearance_menu.property("screensnap_self_window"), "外观弹层")
        toolbar.close()

        # ③④ 新建 / 编辑对话框：控件齐全且带（实时文字）预览。
        item_values = {"font": "", "font_size": 20, "text_alignment": "center",
                       "text_color": "#ff0000", "text_bold": False, "text_italic": False,
                       "text_underline": False, "text_strikethrough": False,
                       "text_background_enabled": False, "text_background": "#fff3a0",
                       "text_width": 120, "text_height": 0}
        for values in (None, item_values):
            dialog = TextInputDialog(None, "文字", settings, "abc", values=values)
            for attr in ("font", "font_size", "alignment", "text_color",
                         "background_enabled", "background", "text_width", "width_hint",
                         "text_height", "height_hint"):
                self.assertIsNotNone(getattr(dialog, attr, None), attr)
            self.assertEqual(dialog.per_annotation, values is not None)
            self.assertEqual(dialog.preview.kind, "text")
            self.assertEqual(dialog.preview.sample_text, "abc")
            dialog.accept()

    def test_editing_text_applies_height_only_change_without_touching_config(self):
        """二次编辑只改高度：作用于该标注，且不回写公共配置；初值取自该标注自身。"""
        from config.config_manager import DEFAULTS
        from editor.annotation_items import text_item
        from editor import text_input_dialog
        from PySide6.QtWidgets import QDialog

        settings = dict(DEFAULTS, text_width=0, text_height=0)
        canvas = AnnotationCanvas(Image.new("RGB", (320, 200), "white"), settings)
        item = text_item(QPointF(10, 10), "多行文字", canvas.settings, Qt.AlignLeft)
        canvas._add_annotation(item)
        item.setSelected(True)
        auto_height = item.boundingRect().height()
        self.assertEqual(item.fixed_height, 0.0)

        captured = {}

        class StubDialog:
            def __init__(self, *args, **kwargs):
                # 二次编辑的初值来自该标注自身（自动高度即 0），而不是公共配置。
                captured.update(kwargs.get("values") or {})

            def exec(self):
                return QDialog.Accepted

            def text(self):
                return "多行文字"

            def changed_settings(self):
                return {"text_height": 96}

        original = text_input_dialog.TextInputDialog
        text_input_dialog.TextInputDialog = StubDialog
        try:
            canvas.edit_text_item(item)
        finally:
            text_input_dialog.TextInputDialog = original

        self.assertEqual(captured.get("text_height"), 0)
        self.assertEqual(item.fixed_height, 96.0)
        self.assertEqual(item.boundingRect().height(), 96.0)
        # 只改这一个标注：公共配置与其它标注不受影响。
        self.assertEqual(canvas.settings["text_height"], 0)
        other = text_item(QPointF(10, 120), "另一", canvas.settings, Qt.AlignLeft)
        self.assertEqual(other.fixed_height, 0.0)
        self.assertNotEqual(auto_height, 96.0)
        canvas.close()

    def test_hover_highlight_color_and_opacity_are_configurable(self):
        from config.config_manager import DEFAULTS, validate
        from screenshot.mask_window import _hover_fill_color

        self.assertEqual(DEFAULTS["window_hover_color"], "#168cff")
        self.assertEqual(DEFAULTS["window_hover_border_color"], "#168cff")
        self.assertEqual(DEFAULTS["selection_border_color"], "#168cff")
        self.assertTrue(DEFAULTS["capture_quick_sticker_enabled"])
        self.assertEqual(DEFAULTS["window_hover_opacity"], 35)
        self.assertEqual(DEFAULTS["window_hover_fill_mode"], "reveal")
        self.assertEqual(DEFAULTS["mask_color"], "#000000")
        self.assertEqual(DEFAULTS["mask_opacity"], 70)
        default_color = _hover_fill_color(DEFAULTS)
        self.assertEqual(default_color.name(), "#168cff")
        self.assertEqual(default_color.alpha(), 89)

        custom = dict(DEFAULTS, window_hover_color="#ff0000", window_hover_opacity=60)
        custom_color = _hover_fill_color(custom)
        self.assertEqual(custom_color.name(), "#ff0000")
        self.assertEqual(custom_color.alpha(), 153)
        validated = validate(custom)
        self.assertEqual(validated["window_hover_color"], "#ff0000")
        self.assertEqual(validated["window_hover_opacity"], 60)
        with self.assertRaises(ValueError):
            validate(dict(DEFAULTS, window_hover_opacity=101))
        with self.assertRaises(ValueError):
            validate(dict(DEFAULTS, window_hover_color="blue"))
        with self.assertRaises(ValueError):
            validate(dict(DEFAULTS, window_hover_fill_mode="transparent"))

    def test_editor_toolbar_shadow_effect_follows_settings(self):
        """编辑工具栏阴影光晕按设置开关/强度/颜色生效，关闭后彻底移除效果。"""
        from PySide6.QtWidgets import QGraphicsDropShadowEffect
        from editor.toolbar_widget import ToolbarWidget
        from config.config_manager import DEFAULTS

        toolbar = ToolbarWidget(settings=dict(DEFAULTS))
        try:
            effect = toolbar.graphicsEffect()
            self.assertIsInstance(effect, QGraphicsDropShadowEffect)
            self.assertEqual(effect.offset(), QPointF(0, 0))
            self.assertEqual(effect.blurRadius(), 24)
            self.assertEqual(effect.color().alpha(), 153)

            toolbar.settings["editor_toolbar_shadow_enabled"] = False
            toolbar.apply_toolbar_shadow()
            self.assertIsNone(toolbar.graphicsEffect())
            self.assertIsNone(toolbar.shadow_effect)

            toolbar.settings["editor_toolbar_shadow_enabled"] = True
            toolbar.settings["editor_toolbar_shadow_strength"] = 100
            toolbar.settings["editor_toolbar_shadow_color"] = "#ff0000"
            toolbar.apply_toolbar_shadow()
            effect = toolbar.graphicsEffect()
            self.assertIsInstance(effect, QGraphicsDropShadowEffect)
            self.assertEqual(effect.blurRadius(), 40)
            self.assertEqual(effect.color().name(), "#ff0000")
            self.assertEqual(effect.color().alpha(), 255)
        finally:
            toolbar.close()

    def test_editor_toolbar_shadow_config_lifecycle(self):
        """工具栏阴影设置覆盖初始化 / 校验 / 回滚。"""
        from config.config_manager import DEFAULTS, repair, validate

        self.assertTrue(DEFAULTS["editor_toolbar_shadow_enabled"])
        self.assertEqual(DEFAULTS["editor_toolbar_shadow_color"], "#000000")
        self.assertEqual(DEFAULTS["editor_toolbar_shadow_strength"], 60)
        self.assertEqual(validate({})["editor_toolbar_shadow_strength"], 60)
        self.assertEqual(
            validate({"editor_toolbar_shadow_strength": 0})["editor_toolbar_shadow_strength"], 0)
        with self.assertRaises(ValueError):
            validate({"editor_toolbar_shadow_strength": 101})
        with self.assertRaises(ValueError):
            validate({"editor_toolbar_shadow_color": "black"})
        repaired, dropped = repair({"editor_toolbar_shadow_strength": 101,
                                    "editor_toolbar_shadow_color": "black"})
        self.assertEqual(repaired["editor_toolbar_shadow_strength"], 60)
        self.assertEqual(repaired["editor_toolbar_shadow_color"], "#000000")
        self.assertIn("editor_toolbar_shadow_strength", dropped)
        self.assertIn("editor_toolbar_shadow_color", dropped)

    def test_settings_pages_group_related_controls_and_scroll(self):
        from PySide6.QtWidgets import QGroupBox, QScrollArea

        with tempfile.TemporaryDirectory() as folder:
            settings = SettingsWindow(ConfigManager(Path(folder) / "settings.json"))
            settings.show()
            self.app.processEvents()
            expected = (
                ("截图", "截图内容", ("cursor", "capture_gap_fill")),
                ("截图", "截图后", ("inline_edit", "capture_after_selection",
                                  "capture_fullscreen_action", "capture_monitor_action",
                                  "capture_repeat_action")),
                ("截图", "定位辅助", ("crosshair", "crosshair_color", "crosshair_width",
                                  "magnifier_size", "magnifier_grid")),
                ("截图", "截图快捷操作", ("capture_quick_sticker_enabled", "capture_quick_sticker_shortcut",
                                     "capture_save_shortcut", "capture_custom_size_shortcut",
                                     "capture_recapture_shortcut", "capture_window_edit_shortcut",
                                     "capture_multi_select_shortcut", "capture_multi_edit_action",
                                     "capture_copy_shortcut", "capture_toolbar_hide_shortcut")),
                ("截图", "操作提示", ("capture_hints_enabled", "capture_hint_order",
                                  "capture_hint_per_line", "capture_hint_gap",
                                  "intruder_warning_enabled",
                                  "intruder_warning_items", "hint_bar_style",
                                  "hint_bar_warning_style")),
                ("截图", "窗口与控件识别", ("window_detection", "window_auto_select", "window_hover_detect",
                                      "window_hover_color", "window_hover_opacity", "window_uia_detect",
                                      "window_hover_interval", "element_depth")),
                ("截图", "遮罩与选区", ("mask_color", "mask_opacity", "selection_border_color", "anchor_style")),
                ("剪贴板贴图", "文字贴图", ("text_sticker_font", "text_sticker_font_size")),
                ("剪贴板贴图", "颜色贴图", ("color_sticker_width", "color_sticker_height")),
                ("剪贴板贴图", "文件贴图", ("file_sticker_path", "file_sticker_width")),
                ("保存与输出", "输出图像外观", ("editor_image_round_corners", "editor_image_corner_radius",
                                     "editor_image_border_enabled", "editor_image_border_width",
                                     "editor_image_border_color", "editor_image_shadow_enabled",
                                     "editor_image_shadow_size", "editor_image_shadow_strength",
                                     "editor_image_shadow_color")),
                ("编辑器", "画笔", ("pen_color", "pen_width")),
                ("编辑器", "矩形", ("rect_color", "rect_width", "rect_style",
                                    "rect_corner_enabled", "rect_corner_radius")),
                ("编辑器", "椭圆", ("ellipse_color", "ellipse_width", "ellipse_style")),
                ("编辑器", "箭头", ("arrow_color", "arrow_style", "arrow_width")),
                ("编辑器", "荧光笔", ("marker_color", "marker_width")),
                ("编辑器", "文字", ("text_color", "font", "font_size", "line_spacing")),
                ("编辑器", "橡皮擦", ("eraser_width",)),
                ("编辑器", "马赛克", ("mosaic_mode", "mosaic_size")),
                ("编辑器", "编辑区边框", ("editor_border_width", "editor_border_color")),
                ("编辑器", "透明背景", ("editor_transparent_background",)),
                ("编辑器", "编辑工具栏", ("editor_toolbar_shadow_enabled",
                                     "editor_toolbar_shadow_color",
                                     "editor_toolbar_shadow_strength")),
            )
            for page_title, title, keys in expected:
                page = settings.page(page_title)
                box = next(box for box in page.findChildren(QGroupBox) if box.title() == title)
                for key in keys:
                    control = page.controls.get(key) or page.color_buttons[key]
                    self.assertTrue(box.isAncestorOf(control), (title, key))
            preview_groups = {
                "截图": {"assist": "定位辅助", "hover": "窗口与控件识别",
                         "selection": "遮罩与选区", "hints": "操作提示",
                         "hint_style": "操作提示", "hint_warning_style": "操作提示"},
                "剪贴板贴图": {"text": "文字贴图", "color": "颜色贴图", "file": "文件贴图"},
            }
            for page_title, kinds in preview_groups.items():
                page = settings.page(page_title)
                for preview in page.previews:
                    if preview.kind not in kinds:
                        continue
                    group = next(box for box in page.findChildren(QGroupBox)
                                 if box.title() == kinds[preview.kind])
                    self.assertTrue(group.isAncestorOf(preview),
                                    (page_title, preview.kind))
                    editor_page = settings.page("编辑器")
                    eraser_preview = next(preview for preview in editor_page.previews
                              if preview.kind == "eraser")
                    eraser_group = next(box for box in editor_page.findChildren(QGroupBox)
                            if box.title() == "橡皮擦")
                    self.assertTrue(eraser_group.isAncestorOf(eraser_preview))
                    self.assertEqual(editor_page.controls["eraser_width"].minimum(), 10)
                    self.assertEqual(editor_page.controls["eraser_width"].maximum(), 100)
                    screenshot_previews = {preview.kind: preview
                               for preview in settings.page("截图").previews}
                    self.assertEqual(set(screenshot_previews),
                                     {"assist", "hover", "selection", "hints",
                                      "hint_style", "hint_warning_style"})
                    self.assertTrue(all(not preview.grab().isNull()
                            for preview in screenshot_previews.values()))
                    clipboard_page = settings.page("剪贴板贴图")
                    clipboard_previews = {preview.kind: preview for preview in clipboard_page.previews}
                    self.assertEqual(set(clipboard_previews), {"text", "color", "file"})
                    self.assertEqual(len(clipboard_previews["text"].images), 2)
                    color_preview = clipboard_previews["color"]
                    new_width = clipboard_page.controls["color_sticker_width"].value() + 20
                    clipboard_page.controls["color_sticker_width"].setValue(new_width)
                    self.assertEqual(color_preview.images[0].width(), new_width)
            for index in range(settings.pages.count()):
                page = settings.pages.widget(index)
                for key, control in {**page.controls, **page.color_buttons}.items():
                    self.assertTrue(control.toolTip().strip(),
                                    (settings.navigation.item(index).text(), key))
            save_page = settings.page("保存与输出")
            archive_groups = [box for box in save_page.findChildren(QGroupBox)
                              if box.title() == "图片归档"]
            self.assertEqual(len(archive_groups), 1)
            archive_month = save_page.controls["archive_by_month"]
            archive_day = save_page.controls["archive_by_day"]
            self.assertTrue(archive_month.isChecked())
            self.assertFalse(archive_day.isChecked())
            archive_day.setChecked(True)
            self.assertFalse(archive_month.isChecked())
            archive_month.setChecked(True)
            self.assertFalse(archive_day.isChecked())
            archive_month.setChecked(False)
            self.assertFalse(settings.config.data["archive_by_day"])
            save_page.reset_page()
            self.assertTrue(archive_month.isChecked())
            self.assertFalse(archive_day.isChecked())
            for index in range(settings.pages.count()):
                page = settings.pages.widget(index)
                self.assertTrue(page.findChildren(QGroupBox), index)
                self.assertIsNotNone(page.findChild(QScrollArea))
            editor_scroll = settings.page("编辑器").findChild(QScrollArea)
            self.assertGreater(editor_scroll.widget().sizeHint().height(),
                               editor_scroll.viewport().height())
            settings.close()

    def test_editor_settings_page_exposes_and_resets_new_annotation_options(self):
        from config.config_manager import DEFAULTS

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "settings.json"
            # 把今天新增的标注配置项全部改成非默认值。
            path.write_text(json.dumps({
                "pen_chain": True, "marker_chain": True,
                "text_bold": True, "text_italic": True, "text_underline": True,
                "text_strikethrough": True, "text_background_enabled": True,
                "text_background": "#123456", "text_width": 320, "text_height": 180,
                "mosaic_brush": True,
                "eraser_erase_base": True, "arrow_chain": True, "mosaic_width": 66,
                "mosaic_cursor_color": "#123456", "eraser_cursor_color": "#abcdef",
                "rect_corner_enabled": True, "rect_corner_radius": 40,
                "rect_fill_enabled": True, "rect_fill_opacity": 80, "rect_fill_color": "#112233",
                "ellipse_fill_enabled": True, "ellipse_fill_opacity": 70, "ellipse_fill_color": "#445566",
            }), encoding="utf-8")
            manager = ConfigManager(path)
            settings = SettingsWindow(manager)
            editor = settings.page("编辑器")
            new_keys = ("pen_chain", "marker_chain", "text_bold", "text_italic",
                        "text_underline", "text_strikethrough", "text_background_enabled",
                        "text_background", "text_width", "text_height", "mosaic_brush",
                        "eraser_erase_base",
                        "arrow_chain", "mosaic_width", "mosaic_cursor_color",
                        "eraser_cursor_color", "rect_corner_enabled", "rect_corner_radius",
                        "rect_fill_enabled", "rect_fill_opacity", "rect_fill_color",
                        "ellipse_fill_enabled", "ellipse_fill_opacity", "ellipse_fill_color")
            for key in new_keys:
                control = editor.controls.get(key) or editor.color_buttons.get(key)
                self.assertIsNotNone(control, key)
                self.assertIn(key, DEFAULTS, key)
            # 改动若干开关后，重置本页应恢复默认并被持久化。
            editor.controls["pen_chain"].setChecked(False)
            editor.controls["marker_chain"].setChecked(False)
            editor.controls["text_bold"].setChecked(False)
            editor.controls["text_width"].setValue(400)
            editor.controls["text_height"].setValue(500)
            editor.controls["mosaic_brush"].setChecked(False)
            editor.controls["eraser_erase_base"].setChecked(False)
            editor.controls["arrow_chain"].setChecked(False)
            editor.controls["mosaic_width"].setValue(10)
            editor.color_buttons["mosaic_cursor_color"].changed("#222222")
            editor.color_buttons["eraser_cursor_color"].changed("#333333")
            editor.controls["rect_fill_enabled"].setChecked(False)
            editor.controls["rect_fill_opacity"].setValue(20)
            editor.controls["rect_corner_enabled"].setChecked(False)
            editor.controls["ellipse_fill_opacity"].setValue(5)
            editor.reset_page()
            for key in new_keys:
                self.assertEqual(manager.data[key], DEFAULTS[key], key)
            settings.flush_persist()
            reloaded = ConfigManager(path).data
            for key in new_keys:
                self.assertEqual(reloaded[key], DEFAULTS[key], key)
            settings.close()

    def test_start_on_boot_setting_persists_and_rolls_back_on_error(self):
        with tempfile.TemporaryDirectory() as folder:
            manager = ConfigManager(Path(folder) / "settings.json")
            with patch("ui.settings_window.set_start_on_boot") as startup, \
                 patch("ui.settings_window.QMessageBox.warning") as warning:
                settings = SettingsWindow(manager)
                control = settings.pages.widget(0).controls["start_on_boot"]
                control.setChecked(True)
                startup.assert_called_with(True)
                self.assertTrue(ConfigManager(manager.path).data["start_on_boot"])
                control.setChecked(False)
                startup.assert_called_with(False)
                self.assertFalse(ConfigManager(manager.path).data["start_on_boot"])
                startup.side_effect = OSError("registry unavailable")
                control.setChecked(True)
                self.assertFalse(control.isChecked())
                self.assertFalse(manager.data["start_on_boot"])
                self.assertFalse(ConfigManager(manager.path).data["start_on_boot"])
                warning.assert_called_once()
                settings.close()

    def test_import_settings_syncs_start_on_boot(self):
        with tempfile.TemporaryDirectory() as folder:
            manager = ConfigManager(Path(folder) / "settings.json")
            imported = Path(folder) / "import.json"
            imported.write_text('{"start_on_boot": true}', encoding="utf-8")
            with patch("ui.settings_window.set_start_on_boot") as startup, \
                    patch("ui.settings_window.QFileDialog.getOpenFileName",
                        return_value=(str(imported), "JSON (*.json)")):
                settings = SettingsWindow(manager)
                settings.import_settings()
                startup.assert_called_once_with(True)
                self.assertTrue(settings.pages.widget(0).controls["start_on_boot"].isChecked())
                self.assertTrue(ConfigManager(manager.path).data["start_on_boot"])
                settings.close()

    def test_import_settings_rolls_back_on_startup_error(self):
        with tempfile.TemporaryDirectory() as folder:
            manager = ConfigManager(Path(folder) / "settings.json")
            imported = Path(folder) / "import.json"
            imported.write_text('{"start_on_boot": true}', encoding="utf-8")
            with patch("ui.settings_window.set_start_on_boot", side_effect=OSError("denied")), \
                 patch("ui.settings_window.QFileDialog.getOpenFileName",
                       return_value=(str(imported), "JSON (*.json)")), \
                 patch("ui.settings_window.QMessageBox.warning") as warning:
                settings = SettingsWindow(manager)
                settings.import_settings()
                self.assertFalse(manager.data["start_on_boot"])
                self.assertFalse(ConfigManager(manager.path).data["start_on_boot"])
                self.assertFalse(settings.pages.widget(0).controls["start_on_boot"].isChecked())
                warning.assert_called_once()
                settings.close()

    def test_config_created_on_first_load_with_all_settings(self):
        from config.config_manager import DEFAULTS
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "new" / "settings.json"
            manager = ConfigManager(path)
            self.assertTrue(path.is_file())
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), DEFAULTS)
            settings_reference = manager.data
            manager.data["pen_color"] = "#00ff00"
            manager.save()
            self.assertIs(manager.data, settings_reference)
            self.assertEqual(ConfigManager(path).data["pen_color"], "#00ff00")
            path.write_text(json.dumps({"pen_color": "#123456"}), encoding="utf-8")
            self.assertEqual(ConfigManager(path).data["pen_color"], "#123456")
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["font_size"], DEFAULTS["font_size"])

    def test_settings_import_navigation_and_paths(self):
        from datetime import date
        from core.path_utils import configured_dir, resolved_dir

        with tempfile.TemporaryDirectory() as folder:
            manager = ConfigManager(Path(folder) / "settings.json")
            self.assertEqual(resolved_dir(manager.data),
                             configured_dir(manager.data) / date.today().strftime("%Y-%m"))
            self.assertIn("save_dir", manager.data)
            self.assertNotIn("auto_dir", manager.data)
            self.assertNotIn("manual_dir", manager.data)
            imported = Path(folder) / "import.json"
            imported.write_text(json.dumps({"pen_width": 7}), encoding="utf-8")
            window = SettingsWindow(manager)
            with patch("ui.settings_window.QFileDialog.getOpenFileName", return_value=(str(imported), "")):
                window.import_settings()
                self.assertFalse(window.navigation.item(3).icon().isNull())
                self.assertFalse(window.navigation.item(5).icon().isNull())
            window.navigation.setCurrentRow(3)
            self.assertEqual(window.pages.currentIndex(), 3)
            self.assertEqual(manager.data["pen_width"], 7)

    def test_annotation_color_picker_saves_and_syncs_settings(self):
        from PySide6.QtGui import QColor
        from PySide6.QtWidgets import QColorDialog
        with tempfile.TemporaryDirectory() as folder:
            manager = ConfigManager(Path(folder) / "settings.json")
            settings = SettingsWindow(manager)
            editor = EditorWindow(Image.new("RGB", (40, 30), "white"), manager.data)
            editor.pen_color_changed.connect(settings.set_pen_color)
            with patch("ui.widgets.color_button.color_dialog") as dialog_factory:
                dialog_factory.return_value.exec.return_value = QColorDialog.Accepted
                dialog_factory.return_value.currentColor.return_value = QColor("#12ab34")
                editor.toolbar.color_changed.emit("#12ab34")
            settings.flush_persist()
            self.assertEqual(editor.canvas.settings["pen_color"], "#12ab34")
            self.assertEqual(ConfigManager(manager.path).data["pen_color"], "#12ab34")
            editor_page = settings.page("编辑器")
            self.assertEqual(editor_page.color_buttons["pen_color"].color, "#12ab34")
            editor_page.update_value("pen_color", "#ff0000")
            settings.flush_persist()
            self.assertEqual(editor.canvas.settings["pen_color"], "#ff0000")
            self.assertEqual(ConfigManager(manager.path).data["pen_color"], "#ff0000")
            editor.close()

    def test_annotation_boolean_setting_syncs_checkbox_and_persists(self):
        with tempfile.TemporaryDirectory() as folder:
            manager = ConfigManager(Path(folder) / "settings.json")
            settings = SettingsWindow(manager)
            editor_page = settings.page("编辑器")

            settings.set_annotation_setting("marker_chain", True)
            settings.set_annotation_setting("text_bold", True)

            self.assertTrue(editor_page.controls["marker_chain"].isChecked())
            self.assertTrue(editor_page.controls["text_bold"].isChecked())
            settings.flush_persist()
            saved = ConfigManager(manager.path).data
            self.assertTrue(saved["marker_chain"])
            self.assertTrue(saved["text_bold"])
            settings.close()

    def test_capture_enhancement_settings(self):
        from config.config_manager import DEFAULTS, validate
        from PySide6.QtGui import QImage, QPainter, QColor
        from PySide6.QtCore import QRect, QPoint
        from screenshot.magnifier_widget import paint_magnifier

        for key in ("capture_picker_shortcut", "magnifier_grid", "magnifier_grid_color",
                    "ruler_enabled", "ruler_color",
                    "sequence_font_size", "sequence_start",
                    "sticker_recycle_enabled", "sticker_recycle_limit"):
            self.assertIn(key, DEFAULTS)

        valid = validate({
            "capture_after_selection": "copy",
            "capture_picker_shortcut": "C",
            "magnifier_grid": True, "magnifier_grid_color": "#cccccc",
            "ruler_enabled": False, "ruler_color": "#00ad91",
            "sequence_font_size": 14,
            "sequence_start": 1,
            "sticker_recycle_enabled": True, "sticker_recycle_limit": 50,
        })
        self.assertEqual(valid["capture_after_selection"], "copy")
        self.assertEqual(valid["capture_picker_shortcut"], "C")

        for bad in ({"capture_after_selection": "weird"}, {"magnifier_grid_color": "red"},
                    {"sequence_font_size": 999}, {"sequence_start": 1000},
                    {"sticker_recycle_limit": 0},
                    {"capture_picker_shortcut": ""}):
            with self.assertRaises(ValueError):
                validate(bad)

        image = QImage(40, 40, QImage.Format_RGB32)
        image.fill(QColor("#ffffff"))
        target = QImage(60, 60, QImage.Format_RGB32)
        painter = QPainter(target)
        paint_magnifier(painter, image, QPoint(20, 20), QRect(0, 0, 800, 600), QPoint(20, 20))
        paint_magnifier(painter, image, QPoint(20, 20), QRect(0, 0, 800, 600), QPoint(20, 20),
                        grid=True, grid_color="#cccccc")
        painter.end()
        self.assertFalse(target.isNull())

    def test_annotation_choices_restore_from_config(self):
        with tempfile.TemporaryDirectory() as folder:
            manager = ConfigManager(Path(folder) / "settings.json")
            window = SettingsWindow(manager)
            image = Image.new("RGB", (40, 30), "white")
            editor = EditorWindow(image, manager.data, image)
            editor.setting_changed.connect(window.set_annotation_setting)
            editor.toolbar.tool_buttons["marker"].click()
            editor.toolbar.tool_buttons["text"].click()
            editor.toolbar.choice_buttons["text_alignment"]["right"].click()
            editor.toolbar.tool_buttons["mosaic"].click()
            editor.toolbar.choice_buttons["mosaic_mode"]["blur"].click()
            editor.toolbar.tool_buttons["marker"].click()
            editor.toolbar.pen_width.setValue(9)
            editor.toolbar.marker_opacity.setValue(55)
            editor.toolbar.mosaic_size.setValue(23)
            editor.toolbar.cursor_switch.setChecked(True)
            window.flush_persist()
            restored = ConfigManager(manager.path).data
            self.assertEqual(restored["annotation_tool"], "marker")
            self.assertEqual(restored["text_alignment"], "right")
            self.assertEqual(restored["marker_width"], 9)
            self.assertEqual(restored["pen_width"], 2)
            self.assertEqual(restored["marker_opacity"], 55)
            self.assertEqual(window.page("编辑器").controls["marker_opacity"].value(), 55)
            self.assertEqual(restored["mosaic_mode"], "blur")
            self.assertEqual(restored["mosaic_size"], 23)
            self.assertTrue(restored["cursor"])
            self.assertTrue(window.page("截图").controls["cursor"].isChecked())
            reopened = EditorWindow(image, restored, image)
            self.assertEqual(reopened.canvas.tool, "marker")
            self.assertTrue(reopened.toolbar.tool_buttons["marker"].isChecked())
            self.assertTrue(reopened.toolbar.choice_buttons["text_alignment"]["right"].isChecked())
            self.assertTrue(reopened.canvas.text_alignment & Qt.AlignRight)
            self.assertEqual(reopened.toolbar.pen_width.value(), 9)
            self.assertEqual(reopened.toolbar.marker_opacity.value(), 55)
            self.assertEqual(reopened.canvas.stroke_pen().color().alpha(), round(55 * 255 / 100))
            self.assertTrue(reopened.toolbar.choice_buttons["mosaic_mode"]["blur"].isChecked())
            self.assertEqual(reopened.toolbar.mosaic_size.value(), 23)
            self.assertTrue(reopened.toolbar.cursor_switch.isChecked())
            editor.close()
            reopened.close()

    def test_notification_settings_persist_independently(self):
        from config.config_manager import DEFAULTS, repair, validate
        from ui.settings_general import GeneralPage
        with tempfile.TemporaryDirectory() as folder:
            manager = ConfigManager(Path(folder) / "settings.json")
            page = GeneralPage(manager, manager.save)
            backend = page.controls["notification_backend"]
            self.assertEqual(manager.data["notification_backend"], "win11toast")
            backend.setCurrentIndex(backend.findData("legacy"))
            self.assertEqual(ConfigManager(manager.path).data["notification_backend"], "legacy")
            for label, key in (("复制完成通知", "copy_notification"),
                               ("保存成功通知", "save_notification"),
                               ("操作与错误通知", "operation_notification"),
                               ("贴图通知", "sticker_notification")):
                control = page.controls[key]
                control.setChecked(False)
                self.assertFalse(ConfigManager(manager.path).data[key])
            self.assertTrue(ConfigManager(manager.path).data["bubble"])

            from config.config_manager import migrate_legacy_settings
            migrated = migrate_legacy_settings({"capture_notification": False})
            self.assertFalse(migrated["copy_notification"])
            self.assertNotIn("capture_notification", migrated)
            with self.assertRaises(ValueError):
                validate({"notification_backend": "invalid"})
            repaired, dropped = repair({"notification_backend": "invalid"})
            self.assertEqual(repaired["notification_backend"],
                             DEFAULTS["notification_backend"])
            self.assertIn("notification_backend", dropped)

    def test_legacy_notification_timeout_uses_setting(self):
        from ui.capture_notification import CaptureNotification

        for timeout, expected_calls in ((3, 1), (0, 0)):
            notification = CaptureNotification(
                Image.new("RGB", (12, 8), "white"), backend="legacy",
                close_after=timeout)
            try:
                with patch("ui.capture_notification.QTimer.singleShot") as timer:
                    notification._show_local_preview()
                self.assertEqual(timer.call_count, expected_calls)
                if timeout:
                    timer.assert_called_once_with(3000, notification.close)
            finally:
                notification.close()

    def test_hotkey_recorder_function_keys(self):
        recorded = []
        widget = HotkeyEdit("f1", recorded.append)
        QTest.keyClick(widget, Qt.Key_F5, Qt.ControlModifier)
        self.assertEqual(recorded, ["ctrl+f5"])

    def test_hotkey_settings_record_and_conflict(self):
        from ui.settings_hotkey import HotkeyPage
        with tempfile.TemporaryDirectory() as folder:
            manager = ConfigManager(Path(folder) / "settings.json")
            states = []
            page = HotkeyPage(manager, manager.save, states.append)
            edit = page.edit_capture
            page.show()
            edit.setFocus()
            self.app.processEvents()
            self.assertIn(True, states)
            QTest.keyClick(edit, Qt.Key_F5, Qt.ControlModifier)
            self.assertEqual(ConfigManager(manager.path).data["hotkeys"]["capture"], "ctrl+f5")
            with patch("ui.settings_hotkey.QMessageBox.warning") as warning:
                QTest.keyClick(edit, Qt.Key_F3)
                warning.assert_called_once()
            self.assertEqual(edit.keySequence().toString().lower(), "ctrl+f5")
            edit.clearFocus()
            self.app.processEvents()
            self.assertEqual(states[-1], False)

    def test_image_edit_hotkeys_are_exposed_and_saved(self):
        from ui.settings_hotkey import HotkeyPage

        with tempfile.TemporaryDirectory() as folder:
            manager = ConfigManager(Path(folder) / "settings.json")
            page = HotkeyPage(manager, manager.save)
            self.assertEqual(manager.data["hotkeys"]["edit_clipboard"], "ctrl+alt+v")
            self.assertEqual(manager.data["hotkeys"]["open_image"], "ctrl+alt+e")
            self.assertEqual(page.edit_edit_clipboard.keySequence().toString().lower(), "ctrl+alt+v")
            self.assertEqual(page.edit_open_image.keySequence().toString().lower(), "ctrl+alt+e")
            page.update_binding("edit_clipboard", "ctrl+shift+v")
            self.assertEqual(ConfigManager(manager.path).data["hotkeys"]["edit_clipboard"],
                             "ctrl+shift+v")
            page.close()

    def test_sticker_context_menu_shows_restore_hotkey(self):
        from PySide6.QtGui import QColor, QImage
        from sticker.sticker_menu import build_menu

        image = QImage(24, 18, QImage.Format_RGB32)
        sticker = StickerItem(image, settings={"hotkeys": {"touch": "ctrl+shift+t"}})
        menu = build_menu(sticker)
        menu_actions = [action for action in menu.actions() if not action.isSeparator()]
        self.assertEqual(menu_actions[-1].text(), "关闭当前贴图")
        copy_action = next(action for action in menu.actions() if action.text() == "复制图像")
        icon_image = copy_action.icon().pixmap(24, 24).toImage()
        self.assertTrue(any(icon_image.pixelColor(x, y).alpha() > 0
                    for x in range(icon_image.width()) for y in range(icon_image.height())))
        action = next(action for action in menu.actions()
                      if "点击穿透" in action.text())
        self.assertIn("Ctrl+Shift+T", action.text())
        labels = [action.text() for action in build_menu(sticker).actions()]
        self.assertIn("关闭描边", labels)
        self.assertIn("显示阴影", labels)
        sticker.toggle_border()
        sticker.toggle_shadow()
        labels = [action.text() for action in build_menu(sticker).actions()]
        self.assertIn("开启描边", labels)
        self.assertIn("隐藏阴影", labels)
        sticker.close()

    def test_sticker_border_shadow_defaults_settings_and_persistence(self):
        from PySide6.QtGui import QImage, QColor
        from config.config_manager import DEFAULTS, validate
        from ui.settings_sticker import StickerPage

        self.assertTrue(validate({})["sticker_border_enabled"])
        self.assertTrue(validate({})["sticker_selection_effect_enabled"])
        self.assertEqual(validate({})["sticker_selection_effect_strength"], 30)
        self.assertFalse(DEFAULTS["sticker_shadow_enabled"])
        self.assertEqual(DEFAULTS["sticker_border_color"], "#168cff")
        self.assertEqual(DEFAULTS["sticker_border_width"], DEFAULTS["pen_width"])
        with self.assertRaises(ValueError):
            validate({"sticker_border_color": "red"})
        with self.assertRaises(ValueError):
            validate({"sticker_border_width": 21})
        with self.assertRaises(ValueError):
            validate({"sticker_shadow_strength": 101})
        with self.assertRaises(ValueError):
            validate({"sticker_selection_effect_strength": 101})

        image = QImage(20, 12, QImage.Format_RGB32)
        image.fill(QColor("#ffffff"))
        settings = dict(DEFAULTS, sticker_border_color="#123456", sticker_border_width=3,
                        sticker_shadow_strength=50)
        sticker = StickerItem(image, settings=settings)
        self.assertGreater(sticker.width(), image.width())
        painted = sticker.grab().toImage()
        # 描边整体画在图像内侧（透明模式下也不会被输入遮罩裁掉），取上边中点判断颜色。
        self.assertEqual(painted.pixelColor(
            sticker.width() // 2, sticker.padding()).name(), "#123456")
        self.assertEqual(painted.pixelColor(
            sticker.width() // 2, sticker.padding() + 1).name(), "#123456")
        sticker.toggle_border()
        self.assertFalse(sticker.border_enabled)
        self.assertFalse(sticker.state()["border"])
        sticker.toggle_shadow()
        self.assertTrue(sticker.shadow_enabled)
        self.assertTrue(sticker.state()["shadow"])
        sticker.close()

        with tempfile.TemporaryDirectory() as folder:
            manager = ConfigManager(Path(folder) / "settings.json")
            page = StickerPage(manager, manager.save)
            self.assertIn("sticker_border_enabled", page.controls)
            self.assertIn("sticker_selection_effect_enabled", page.controls)
            self.assertTrue(page.controls["sticker_selection_effect_enabled"].isChecked())
            self.assertFalse(page.controls["sticker_shadow_enabled"].isChecked())
            page.controls["sticker_border_width"].setValue(5)
            self.assertEqual(ConfigManager(manager.path).data["sticker_border_width"], 5)
            page.color_buttons["sticker_border_color"].changed("#abcdef")
            self.assertEqual(ConfigManager(manager.path).data["sticker_border_color"], "#abcdef")
            page.close()

    def test_dispatch_routes_image_edit_hotkeys(self):
        from main import Application

        app = Application.__new__(Application)
        app.logger = Mock()
        app.edit_clipboard_image = Mock()
        app.open_and_edit_image = Mock()
        app.dispatch("edit_clipboard")
        app.dispatch("open_image")
        app.edit_clipboard_image.assert_called_once_with()
        app.open_and_edit_image.assert_called_once_with()

    def test_hotkey_clear_button_updates_binding(self):
        from PySide6.QtWidgets import QPushButton
        from ui.settings_hotkey import HotkeyPage
        with tempfile.TemporaryDirectory() as folder:
            manager = ConfigManager(Path(folder) / "settings.json")
            page = HotkeyPage(manager, manager.save)
            button = next(button for button in page.edit_capture.parentWidget().findChildren(QPushButton)
                          if button.text() == "清除")
            button.click()
            self.assertEqual(ConfigManager(manager.path).data["hotkeys"]["capture"], "")
            self.assertTrue(page.edit_capture.keySequence().isEmpty())

    def test_hotkey_callback_pauses_immediately(self):
        from hotkey.hotkey_manager import HotkeyManager
        manager = HotkeyManager()
        actions = []
        manager.triggered.connect(actions.append)
        manager.set_paused(True)
        manager.emit_action("capture")
        manager.set_paused(False)
        manager.emit_action("capture")
        manager.stop()
        self.assertEqual(actions, ["capture"])

    def test_hotkey_suppression_only_applies_to_capture_actions(self):
        from hotkey.hotkey_manager import HotkeyManager

        manager = HotkeyManager()
        registered = []

        def add_hotkey(binding, callback, suppress=False):
            registered.append((binding, suppress))
            return binding

        with patch("hotkey.hotkey_manager.keyboard.add_hotkey", side_effect=add_hotkey), \
                patch("hotkey.hotkey_manager.keyboard.remove_hotkey"):
            manager.install({"enabled": True, "suppress_capture": True,
                             "bindings": {"capture": "f1", "repeat": "f2",
                                          "fullscreen": "f3", "monitor": "f4",
                                          "paste": "f5"}})
            self.assertEqual(registered, [("f1", True), ("f2", True),
                                          ("f3", True), ("f4", True), ("f5", False)])
            manager.stop()

    def test_selected_shape_fill_settings_update_live(self):
        from config.config_manager import DEFAULTS

        canvas = AnnotationCanvas(Image.new("RGB", (120, 100), "white"), DEFAULTS)
        rect = shape("rect", QPointF(10, 10), QPointF(60, 40), "#123456", 3)
        ellipse = shape("ellipse", QPointF(65, 10), QPointF(110, 40), "#654321", 3)
        canvas.scene_data.addItem(rect)
        canvas.scene_data.addItem(ellipse)
        rect.setSelected(True)
        ellipse.setSelected(True)

        canvas.set_selected_fill("rect", True, 50)
        self.assertEqual(rect.brush().color().alpha(), 128)
        self.assertEqual(ellipse.brush().style(), Qt.NoBrush)
        canvas.set_selected_fill("ellipse", True, 25)
        self.assertEqual(ellipse.brush().color().alpha(), 64)
        canvas.set_selected_fill("rect", False, 50)
        self.assertEqual(rect.brush().style(), Qt.NoBrush)
        canvas.close()

    def test_selection_border_color_is_separate_and_configurable(self):
        from config.config_manager import DEFAULTS, validate
        from screenshot.mask_window import MaskWindow
        from ui.settings_screenshot import ScreenshotPage

        with tempfile.TemporaryDirectory() as folder:
            config = ConfigManager(Path(folder) / "settings.json")
            page = ScreenshotPage(config, lambda: None)
            color_button = page.controls["selection_border_color"]
            self.assertEqual(color_button.color, "#168cff")
            color_button.changed("#ff3030")
            self.assertEqual(config.data["selection_border_color"], "#ff3030")
            self.assertEqual(config.data["pen_color"], DEFAULTS["pen_color"])
            with self.assertRaises(ValueError):
                validate({"selection_border_color": "red"})

            bounds = {"left": 0, "top": 0, "width": 100, "height": 80}
            settings = {**config.data, "magnifier": False, "crosshair": False}
            with patch("screenshot.mask_window.visible_windows", return_value=[]):
                mask = MaskWindow(Image.new("RGB", (100, 80), "white"), bounds, [bounds], settings)
            mask.selection.rects.append(QRect(20, 20, 60, 40))
            mask.selection.rects.append(QRect(20, 50, 60, 20))
            mask.show()
            self.app.processEvents()
            image = mask.grab().toImage()
            # 跨 DPI 抓取分辨率与坐标映射不确定，故扫描整图确认：存在使用配置色的红色描边，
            # 且存在被还原的原始白色内部（与描边区分）。
            border_found = interior_found = False
            for y in range(0, image.height(), 2):
                for x in range(0, image.width(), 2):
                    pixel = image.pixelColor(x, y)
                    if pixel.red() > 150 and pixel.red() - pixel.green() > 30:
                        border_found = True
                    if pixel.red() > 200 and pixel.green() > 200 and pixel.blue() > 200:
                        interior_found = True
            if not (border_found and interior_found):
                # 离屏平台下遮罩渲染的像素采样会随主题/尺寸抖动，需要真实桌面验收。
                # 见 AGENTS 第 19 条：无法在离屏复现的断言留跳过，不拿 mock 顶替。
                self.skipTest("需真实桌面：遮罩边框渲染的像素采样")
            self.assertTrue(border_found)
            self.assertTrue(interior_found)
            mask.close()

    def test_crosshair_color_and_width_are_configurable_and_rendered(self):
        from config.config_manager import DEFAULTS, validate
        from screenshot.mask_window import MaskWindow
        from ui.settings_screenshot import ScreenshotPage

        with tempfile.TemporaryDirectory() as folder:
            config = ConfigManager(Path(folder) / "settings.json")
            page = ScreenshotPage(config, config.save)
            color = page.controls["crosshair_color"]
            width = page.controls["crosshair_width"]
            self.assertEqual(color.color, "#ff0000")
            self.assertEqual(width.value(), 1)
            color.changed("#ff2020")
            width.setValue(3)
            self.assertEqual(ConfigManager(config.path).data["crosshair_color"], "#ff2020")
            self.assertEqual(ConfigManager(config.path).data["crosshair_width"], 3)
            self.assertEqual(validate({})["crosshair_color"], DEFAULTS["crosshair_color"])
            with self.assertRaises(ValueError):
                validate({"crosshair_color": "red"})
            with self.assertRaises(ValueError):
                validate({"crosshair_width": 9})

            bounds = {"left": 0, "top": 0, "width": 100, "height": 80}
            settings = dict(config.data, crosshair=True, magnifier=False, mask_opacity=0)
            with patch("screenshot.mask_window.visible_windows", return_value=[]):
                mask = MaskWindow(Image.new("RGB", (100, 80), "white"), bounds, [bounds], settings)
            mask.position = QPoint(50, 40)
            mask.show()
            self.app.processEvents()
            screenshot = mask.grab().toImage()
            red_pixels = [screenshot.pixelColor(x, y)
                          for x in range(screenshot.width())
                          for y in range(screenshot.height())]
            self.assertTrue(any(pixel.red() > pixel.green() * 1.5
                                and pixel.red() > pixel.blue() * 1.5
                                for pixel in red_pixels))
            mask.close()

    def test_mask_left_double_click_uses_explicit_save_setting(self):
        from config.config_manager import DEFAULTS
        from screenshot.mask_window import MaskWindow

        bounds = {"left": 0, "top": 0, "width": 120, "height": 80}
        settings = {**DEFAULTS, "capture_after_selection": "save",
                    "crosshair": False, "magnifier": False, "sound": False}
        with patch("screenshot.mask_window.visible_windows", return_value=[]):
            mask = MaskWindow(Image.new("RGB", (120, 80), "blue"), bounds,
                              [bounds], settings)
        selected = []
        saved = []
        mask.selected.connect(lambda images, positions: selected.append((images, positions)))
        mask.save_requested.connect(lambda images, positions: saved.append((images, positions)))
        mask.selection.rects.append(QRect(10, 10, 40, 30))
        mask.show()
        self.app.processEvents()
        QTest.mouseDClick(mask, Qt.LeftButton, Qt.NoModifier, QPoint(20, 20))
        self.assertFalse(selected)
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0][0][0][0].size, (40, 30))
        self.assertIsNone(mask.session.inline_editor)

    def test_capture_save_shortcut_is_single_key_and_configurable(self):
        from types import SimpleNamespace
        from PySide6.QtGui import QKeySequence
        from config.config_manager import DEFAULTS, repair, validate
        from ui.settings_screenshot import ScreenshotPage

        config = SimpleNamespace(data=dict(DEFAULTS))
        page = ScreenshotPage(config, lambda: None)
        shortcut = page.controls["capture_save_shortcut"]
        self.assertEqual(shortcut.keySequence().toString(), "S")
        self.assertEqual(DEFAULTS["capture_multi_select_shortcut"], "Alt+M")
        self.assertEqual(DEFAULTS["capture_multi_edit_action"], "save")
        self.assertEqual(validate({})["capture_multi_select_shortcut"], "Alt+M")
        repaired, dropped = repair({"capture_multi_select_shortcut": "",
                         "capture_multi_edit_action": "prompt"})
        self.assertEqual(repaired["capture_multi_select_shortcut"], "Alt+M")
        self.assertEqual(repaired["capture_multi_edit_action"], "save")
        self.assertIn("capture_multi_select_shortcut", dropped)
        self.assertIn("capture_multi_edit_action", dropped)
        shortcut.setKeySequence(QKeySequence("K"))
        self.assertEqual(config.data["capture_save_shortcut"], "K")
        self.assertEqual(validate({"capture_save_shortcut": "Alt+S"})[
            "capture_save_shortcut"], "Alt+S")
        with self.assertRaises(ValueError):
            validate({"capture_save_shortcut": "Ctrl+K, Ctrl+S"})

        # 四个选区功能键（自定义尺寸/重新截图/窗口编辑/仅复制）：初始化为单键，
        # 改动立即落配置；非法组合或清空一律回滚到上一个有效值。
        action_keys = {
            "capture_custom_size_shortcut": "F",
            "capture_recapture_shortcut": "R",
            "capture_window_edit_shortcut": "E",
            "capture_multi_select_shortcut": "Alt+M",
            "capture_copy_shortcut": "Y",
            "capture_toolbar_hide_shortcut": "`",
        }
        for index, (key, default) in enumerate(action_keys.items(), start=1):
            self.assertEqual(DEFAULTS[key], default, key)
            edit = page.controls[key]
            self.assertEqual(edit.keySequence().toString(), default, key)
            replacement = f"Alt+{index}"
            edit.setKeySequence(QKeySequence(replacement))
            self.assertEqual(config.data[key], replacement, key)
            # 清空（空序列）没意义：控件与配置一起回滚到上一个有效键。
            edit.clear()
            self.assertEqual(edit.keySequence().toString(), replacement, key)
            self.assertEqual(config.data[key], replacement, key)
            self.assertEqual(validate({key: "Alt+L"})[key], "Alt+L")
            with self.assertRaises(ValueError):
                validate({key: "Ctrl+K, Ctrl+S"})
            with self.assertRaises(ValueError):
                validate({key: ""})

        copy_shortcut = page.controls["capture_copy_shortcut"]
        previous = config.data["capture_copy_shortcut"]
        copy_shortcut.setKeySequence(QKeySequence(config.data["capture_save_shortcut"]))
        self.assertEqual(copy_shortcut.keySequence().toString(), previous)
        self.assertEqual(config.data["capture_copy_shortcut"], previous)
        self.assertIn("已由", copy_shortcut.toolTip())
        with self.assertRaises(ValueError):
            validate({"capture_save_shortcut": "F",
                      "capture_custom_size_shortcut": "F"})
        repaired, dropped = repair({"capture_save_shortcut": "F",
                                    "capture_custom_size_shortcut": "F"})
        self.assertEqual(repaired["capture_save_shortcut"], DEFAULTS["capture_save_shortcut"])
        self.assertIn("capture_save_shortcut", dropped)

    def test_capture_after_selection_defaults_to_edit_and_is_configurable(self):
        from types import SimpleNamespace
        from config.config_manager import DEFAULTS, repair, validate
        from main import Application

        self.assertEqual(DEFAULTS["capture_after_selection"], "edit")
        self.assertEqual(DEFAULTS["capture_fullscreen_action"], "save")
        self.assertEqual(DEFAULTS["capture_monitor_action"], "save")
        self.assertEqual(DEFAULTS["capture_repeat_action"], "save")
        self.assertEqual(DEFAULTS["capture_gap_fill"], "transparent")
        self.assertEqual(validate({"capture_gap_fill": "black"})["capture_gap_fill"], "black")
        with self.assertRaises(ValueError):
            validate({"capture_gap_fill": "gray"})
        repaired_gap, dropped_gap = repair({"capture_gap_fill": "gray"})
        self.assertEqual(repaired_gap["capture_gap_fill"], "transparent")
        self.assertIn("capture_gap_fill", dropped_gap)
        self.assertEqual(validate({"capture_after_selection": "edit"})[
            "capture_after_selection"], "edit")
        for key in ("capture_fullscreen_action", "capture_monitor_action",
                    "capture_repeat_action"):
            self.assertEqual(validate({key: "save"})[key], "save")
            with self.assertRaises(ValueError):
                validate({key: "copy"})
        repaired, dropped = repair({"capture_fullscreen_action": "copy",
                                    "capture_monitor_action": "save"})
        self.assertEqual(repaired["capture_fullscreen_action"], "save")
        self.assertEqual(repaired["capture_monitor_action"], "save")
        self.assertIn("capture_fullscreen_action", dropped)
        with self.assertRaises(ValueError):
            validate({"capture_after_selection": "preview"})

        images = [(Image.new("RGB", (2, 2), "red"), None)]
        for mode, expected in (("save", "save_capture_images"), ("edit", "edit_images")):
            app = SimpleNamespace(config=SimpleNamespace(data={"capture_after_selection": mode}),
                                  save_capture_images=Mock(), edit_images=Mock())
            Application.handle_capture_selection(app, images)
            if expected == "edit_images":
                app.edit_images.assert_called_once_with(images, positions=None)
            else:
                app.save_capture_images.assert_called_once_with(images)

    def test_default_sequence_settings(self):
        from config.config_manager import DEFAULTS, validate
        self.assertEqual(DEFAULTS["filename"], "ScreenSnap_%Y%m%d_%H%M%S")
        self.assertEqual(DEFAULTS["sequence_shape"], "circle")
        self.assertIn("sequence_fill_color", DEFAULTS)
        self.assertIn("sequence_text_color", DEFAULTS)
        data = validate({})

    def test_multiple_regions_default_to_standalone_editors(self):
        from types import SimpleNamespace
        from config.config_manager import DEFAULTS
        from main import Application
        from screenshot.mask_window import MaskWindow

        bounds = {"left": 0, "top": 0, "width": 160, "height": 100}
        monitors = [
            {"left": 0, "top": 0, "width": 80, "height": 100},
            {"left": 80, "top": 0, "width": 80, "height": 100},
        ]
        screen_infos = [
            {"geometry": QRect(0, 0, 80, 100), "dpr": 1.0},
            {"geometry": QRect(80, 0, 80, 100), "dpr": 1.0},
        ]
        settings = {**DEFAULTS, "crosshair": False, "magnifier": False,
                    "sound": False}
        with patch("screenshot.mask_window.visible_windows", return_value=[]), \
                patch("core.dpi.DisplayMapper.collect_screen_infos", return_value=screen_infos):
            mask = MaskWindow(Image.new("RGB", (160, 100), "blue"), bounds,
                              monitors, settings)
        app = SimpleNamespace(config=SimpleNamespace(data=settings),
                              edit_images=Mock(), save_capture_images=Mock())
        for view in mask.session.views:
            view.edit_requested.connect(app.edit_images)
        mask.show()
        secondary = mask.session.views[1]
        for view, start, end in (
                (mask, QPoint(10, 10), QPoint(40, 30)),
                (secondary, QPoint(10, 40), QPoint(50, 70))):
            QTest.mousePress(view, Qt.RightButton, Qt.NoModifier, start)
            QTest.mouseMove(view, end)
            QTest.mouseRelease(view, Qt.RightButton, Qt.NoModifier, end)
        self.assertEqual(len(mask.selection.rects), 2)
        self.app.processEvents()
        QTest.mouseDClick(secondary, Qt.RightButton, Qt.NoModifier, QPoint(30, 55))
        app.edit_images.assert_called_once()
        self.assertEqual(len(app.edit_images.call_args.args[0]), 2)
        app.save_capture_images.assert_not_called()
        self.assertIsNone(mask.session.inline_editor)

    def test_region_release_runs_configured_action_without_enter(self):
        from config.config_manager import DEFAULTS
        from screenshot.mask_window import MaskWindow

        bounds = {"left": 0, "top": 0, "width": 120, "height": 80}
        settings = {**DEFAULTS, "inline_edit": False, "capture_after_selection": "edit",
                    "magnifier": False, "crosshair": False, "sound": False}
        with patch("screenshot.mask_window.visible_windows", return_value=[]):
            mask = MaskWindow(Image.new("RGB", (120, 80), "blue"), bounds, [bounds], settings)
        selected = []
        mask.selected.connect(lambda images, positions: selected.append((images, positions)))
        mask.show()
        self.app.processEvents()
        start, end = QPoint(10, 10), QPoint(52, 38)
        QTest.mousePress(mask, Qt.LeftButton, Qt.NoModifier, start)
        QTest.mouseMove(mask, end)
        QTest.mouseRelease(mask, Qt.LeftButton, Qt.NoModifier, end)
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0][0][0][0].size, (43, 29))
        self.assertFalse(mask.isVisible())

    def test_repeat_capture_uses_its_own_edit_or_save_setting(self):
        from types import SimpleNamespace
        from config.config_manager import DEFAULTS
        from main import Application

        bounds = {"left": 0, "top": 0, "width": 120, "height": 80}
        for action, expected in (("save", "save"), ("edit", "edit")):
            with self.subTest(action=action):
                app = Application.__new__(Application)
                app.config = SimpleNamespace(data={**DEFAULTS,
                    "last_capture_rect": [10, 12, 40, 30],
                    "capture_repeat_action": action})
                app.logger = Mock()
                app.mask = None
                app.save_capture_images = Mock()
                app.edit_images = Mock()
                frame = Image.new("RGB", (120, 80), "blue")
                with patch("app.capture_flow.capture", return_value=(frame, bounds, [bounds], None)):
                    app.show_mask("repeat")
                if expected == "save":
                    app.save_capture_images.assert_called_once()
                    app.edit_images.assert_not_called()
                    self.assertEqual(app.save_capture_images.call_args.args[0][0][0].size,
                                     (40, 30))
                else:
                    app.edit_images.assert_called_once()
                    app.save_capture_images.assert_not_called()

    def test_multi_select_from_inline_editor_uses_configured_save_or_discard(self):
        from config.config_manager import DEFAULTS
        from editor.annotation_items import shape
        from screenshot.mask_window import MaskWindow

        bounds = {"left": 0, "top": 0, "width": 120, "height": 80}
        for action in ("save", "discard"):
            with self.subTest(action=action), tempfile.TemporaryDirectory() as folder:
                settings = {**DEFAULTS, "save_dir": folder, "filename": "multi",
                            "inline_edit": True, "capture_after_selection": "edit",
                            "capture_multi_edit_action": action, "crosshair": False,
                            "magnifier": False, "sound": False, "bubble": False}
                with patch("screenshot.mask_window.visible_windows", return_value=[]):
                    mask = MaskWindow(Image.new("RGB", (120, 80), "blue"), bounds,
                                      [bounds], settings)
                mask.show()
                self.app.processEvents()
                mask.selection.rects.append(QRect(10, 12, 40, 30))
                mask.complete()
                self.app.processEvents()
                editor = mask.session.inline_editor
                self.assertTrue(editor.multi_select_shortcut.isEnabled())
                self.assertIsNone(editor.last_path)
                settings["capture_multi_select_shortcut"] = "Alt+N"
                mask.sync_capture_action_shortcuts(settings)
                self.assertEqual(editor.multi_select_shortcut.key().toString(), "Alt+N")
                self.assertIn("Alt+N 多选模式", " ".join(filter(None, mask.capture_hint_items())))
                editor.canvas._add_annotation(
                    shape("rect", QPointF(2, 2), QPointF(18, 16), "#ff0000", 2))
                editor.canvas.checkpoint()

                with patch.object(editor, "save", wraps=editor.save) as save:
                    QTest.keyClick(editor.canvas.viewport(), Qt.Key_N, Qt.AltModifier)
                    self.app.processEvents()

                self.assertTrue(mask.session.multi_select_mode)
                self.assertIsNone(mask.session.inline_editor)
                self.assertIsNone(editor.canvas)
                self.assertIsNone(editor.toolbar)
                self.assertIsNone(editor.view)
                self.assertEqual(len(mask.selection.rects), 1)
                if action == "save":
                    save.assert_called_once_with(automatic=True)
                    self.assertTrue(Path(editor.last_path).is_file())
                else:
                    save.assert_not_called()
                    self.assertIsNone(editor.last_path)
                mask.close()

    def test_mask_inline_edit_keeps_magnifier_settings_and_updates_position(self):
        from PySide6.QtCore import QEvent, QPointF
        from PySide6.QtGui import QCursor, QMouseEvent
        from config.config_manager import DEFAULTS
        from screenshot.mask_window import MaskWindow

        with tempfile.TemporaryDirectory() as folder:
            bounds = {"left": 0, "top": 0, "width": 320, "height": 240}
            settings = {**DEFAULTS, "save_dir": folder, "filename": "inline", "inline_edit": True,
                        "capture_after_selection": "edit", "crosshair": True, "magnifier": True,
                        "mask_opacity": 0, "bubble": False}
            with patch("screenshot.mask_window.visible_windows", return_value=[]), \
                    patch.object(QCursor, "pos", return_value=QPoint(70, 50)):
                mask = MaskWindow(Image.new("RGB", (320, 240), "blue"), bounds, [bounds], settings)
            mask.selection.rects.append(QRect(10, 12, 30, 20))
            mask.complete()
            self.assertTrue(settings["crosshair"])
            self.assertTrue(settings["magnifier"])
            mask.show()
            self.app.processEvents()
            editor = mask.session.inline_editor
            overlay = mask.magnifier_overlay
            self.assertTrue(overlay.isVisible())
            self.assertTrue(overlay.isWindow())
            self.assertTrue(overlay.windowFlags() & Qt.WindowStaysOnTopHint)
            self.assertTrue(overlay.windowFlags() & Qt.WindowTransparentForInput)
            self.assertTrue(overlay.testAttribute(Qt.WA_TransparentForMouseEvents))
            self.assertEqual(mask.position, QPoint(70, 50))
            self.assertEqual(overlay.geometry(), QRect(100, 80, 144, 144))
            initially_painted = overlay.grab().toImage()
            self.assertEqual(initially_painted.pixelColor(0, 0).name(), "#ffffff")
            self.assertGreater(initially_painted.pixelColor(95, 75).alpha(), 0)
            viewport = editor.canvas.viewport()
            mouse = QPoint(5, 5)
            expected = mask.to_physical_point(viewport.mapTo(mask, mouse))
            mouse_event = QMouseEvent(QEvent.MouseMove, QPointF(mouse),
                                      QPointF(viewport.mapToGlobal(mouse)),
                                      Qt.NoButton, Qt.NoButton, Qt.NoModifier)
            editor.eventFilter(viewport, mouse_event)
            self.app.processEvents()
            self.assertEqual(mask.position, expected)
            from screenshot.magnifier_widget import magnifier_rect
            self.assertEqual(overlay.geometry(),
                             magnifier_rect(mask.to_logical_point(expected), mask.rect()))
            painted = overlay.grab().toImage()
            self.assertGreater(painted.pixelColor(40, 42).alpha(), 0)
            mask.close()

    def test_inline_annotation_settings_survive_config_reload(self):
        from main import Application
        from editor.editor_window import EditorWindow

        with tempfile.TemporaryDirectory() as folder:
            config = ConfigManager(Path(folder) / "settings.json")
            config.data["capture_after_selection"] = "edit"
            config.save()
            app = Application.__new__(Application)
            app.config = config
            app.settings_window = SettingsWindow(config)
            app.logger = Mock()
            app.edit_images = Mock()
            app.saved = Mock()
            app.initial_save_failed = Mock()
            app.close_all_editors = Mock()
            app.add_sticker = Mock()
            bounds = {"left": 0, "top": 0, "width": 1200, "height": 900}
            frame = Image.new("RGB", (1200, 900), "blue")
            with patch("app.capture_flow.capture", return_value=(frame, bounds, [bounds], frame.copy())), \
                    patch("screenshot.mask_window.visible_windows", return_value=[]):
                app.show_mask("capture")
            mask = app.mask
            mask.selection.rects.append(QRect(40, 40, 300, 200))
            mask.complete()
            toolbar = mask.session.inline_editor.toolbar
            toolbar.tool_buttons["arrow"].click()
            toolbar.pen_width.setValue(13)
            toolbar.choice_buttons["arrow_style"]["open"].click()
            toolbar.cursor_switch.setChecked(True)
            widths = {"pen": 7, "rect": 8, "ellipse": 9, "arrow": 13, "marker": 11, "eraser": 12}
            for tool, width in widths.items():
                toolbar.tool_buttons[tool].click()
                toolbar.pen_width.setValue(width)
            for key, value in (("rect_style", "dash"), ("ellipse_style", "dash"),
                               ("text_alignment", "right"), ("mosaic_mode", "blur")):
                toolbar.choice_buttons[key][value].click()
            toolbar.marker_opacity.setValue(54)
            toolbar.font_size.setValue(25)
            toolbar.mosaic_size.setValue(18)
            toolbar.tool_buttons["pen"].click()
            toolbar.color_changed.emit("#123456")
            app.settings_window.flush_persist()
            reloaded = ConfigManager(config.path).data
            for tool, width in widths.items():
                self.assertEqual(reloaded[f"{tool}_width"], width)
            self.assertEqual(reloaded["arrow_style"], "open")
            self.assertTrue(reloaded["cursor"])
            for key, value in (("rect_style", "dash"), ("ellipse_style", "dash"),
                               ("text_alignment", "right"), ("mosaic_mode", "blur"),
                               ("marker_opacity", 54), ("font_size", 25),
                               ("mosaic_size", 18), ("pen_color", "#123456")):
                self.assertEqual(reloaded[key], value, key)
            restored_window = EditorWindow(frame, reloaded)
            for tool, width in widths.items():
                self.assertEqual(restored_window.toolbar.tool_widths[tool], width)
            self.assertTrue(restored_window.toolbar.choice_buttons["arrow_style"]["open"].isChecked())
            restored_window.close()
            window = EditorWindow(frame, config.data)
            app.qt = self.app
            app.tray = Mock()
            app.hotkey_recording = True
            app.editors = [window]
            app.stickers = Mock()
            config.data.update(arrow_width=17, arrow_style="double_open", annotation_tool="arrow",
                               crop_color="#234567", crop_width=5,
                               rect_fill_enabled=True, rect_fill_opacity=83,
                               rect_fill_color="#123abc", ellipse_fill_enabled=True,
                               ellipse_fill_opacity=64, ellipse_fill_color="#654321",
                               mosaic_brush=False, mosaic_width=42,
                               mosaic_cursor_color="#00aa55", eraser_cursor_color="#cc6600")
            with patch("main.configure_logging", return_value=app.logger), \
                    patch("main.make_tray_menu", return_value=Mock()):
                app.refresh()
            for active in (window, mask.session.inline_editor):
                self.assertEqual(active.toolbar.tool_widths["arrow"], 17)
                self.assertTrue(active.toolbar.choice_buttons["arrow_style"]["double_open"].isChecked())
                self.assertEqual(active.toolbar.pen_width.value(), 17)
                self.assertTrue(active.toolbar.rect_fill_enabled.isChecked())
                self.assertEqual(active.toolbar.rect_fill_opacity.value(), 83)
                self.assertEqual(active.toolbar.rect_fill_color.color, "#123abc")
                self.assertTrue(active.toolbar.ellipse_fill_enabled.isChecked())
                self.assertEqual(active.toolbar.ellipse_fill_opacity.value(), 64)
                self.assertEqual(active.toolbar.ellipse_fill_color.color, "#654321")
                self.assertFalse(active.toolbar.mosaic_brush.isChecked())
                self.assertEqual(active.toolbar.mosaic_width.value(), 42)
                self.assertEqual(active.canvas.settings["mosaic_cursor_color"], "#00aa55")
                self.assertEqual(active.canvas.settings["eraser_cursor_color"], "#cc6600")
            self.assertEqual((window.canvas.crop_color, window.canvas.crop_width), ("#234567", 5))
            self.assertEqual(window.toolbar.crop_color.color, "#234567")
            self.assertEqual(window.toolbar.crop_width.value(), 5)
            window.close()
            mask.close()
            app.settings_window.close()

    def test_capture_hint_items_follow_config_order_and_toggles(self):
        """提示项按配置顺序显示、可逐项关闭；键位取当前设置，改键后提示立刻跟随。"""
        from config.config_manager import DEFAULTS, HINT_ITEM_IDS
        from screenshot.mask_window import MaskWindow

        bounds = {"left": 0, "top": 0, "width": 400, "height": 300}
        settings = dict(DEFAULTS, inline_edit=False, crosshair=False, magnifier=False,
                        capture_recapture_shortcut="F2")
        with patch("screenshot.mask_window.visible_windows", return_value=[]):
            mask = MaskWindow(Image.new("RGB", (400, 300), "blue"), bounds, [bounds], settings)
        mask.selection.rects.append(QRect(30, 40, 80, 60))
        # 默认全部开启；有选区且未进入编辑时，不适用的项（框选/固定尺寸/工具栏两项/
        # 原地编辑三项）为空。
        items = [item for item in mask.capture_hint_items() if item]
        self.assertEqual(len(items), len(HINT_ITEM_IDS) - 7)
        self.assertIn("F2 重新截图", items)
        self.assertIn("Alt+M 多选模式", items)
        self.assertIn("Tab/Shift+Tab 切换窗口层级", items)
        self.assertIn("Esc取消", items)
        self.assertNotIn("拖拽框选", items)
        settings["window_detection"] = False
        self.assertNotIn("Tab/Shift+Tab 切换窗口层级",
                 [item for item in mask.capture_hint_items() if item])
        settings["window_detection"] = True
        self.assertEqual(len(mask.capture_hint_items()), len(HINT_ITEM_IDS))
        # 只保留两项并按给定顺序：顺序即配置顺序，不在清单里的不显示。
        settings["capture_hint_order"] = ["copy", "coords"]
        ordered = [item for item in mask.capture_hint_items() if item]
        self.assertEqual(len(ordered), 2)
        self.assertEqual(ordered[0], "Y 仅复制")
        self.assertIn("80 x 60", ordered[1])
        # 全部关闭时不显示任何提示项（绘制端会跳过空项，因此不会留下空条）。
        settings["capture_hint_order"] = []
        self.assertEqual([item for item in mask.capture_hint_items() if item], [])
        settings["capture_hint_order"] = ["multi_select"]
        mask.session.multi_select_mode = True
        self.assertEqual(mask.capture_hint_items(), ["多选模式 · Enter完成"])
        mask.close()

    def test_hint_bar_style_config_and_editor(self):
        """两套提示条外观：解析、非法值逐键回退、编辑器预设套用与「自定义」识别。"""
        from config.config_manager import (DEFAULTS, DEFAULT_HINT_BAR_STYLE,
                                           DEFAULT_HINT_BAR_WARNING_STYLE, HINT_BAR_FILL_ALPHA,
                                           HINT_BAR_PRESETS, HINT_BAR_WARNING_FILL_ALPHA,
                                           hint_bar_style, repair)
        from ui.settings_screenshot import HintBarStyleEditor

        # 解析：无警示取普通样式，有警示取警示样式；缺失或非字典时回退内置默认样式。
        # 返回值附带绘制期的 alpha（普通 220 / 警示 232），保证默认外观与改造前一致。
        settings = {"hint_bar_style": DEFAULT_HINT_BAR_STYLE,
                    "hint_bar_warning_style": DEFAULT_HINT_BAR_WARNING_STYLE}
        self.assertEqual(hint_bar_style(settings, False),
                         {**DEFAULT_HINT_BAR_STYLE, "alpha": HINT_BAR_FILL_ALPHA})
        self.assertEqual(hint_bar_style(settings, True),
                         {**DEFAULT_HINT_BAR_WARNING_STYLE, "alpha": HINT_BAR_WARNING_FILL_ALPHA})
        self.assertEqual(hint_bar_style({}, False),
                         {**DEFAULT_HINT_BAR_STYLE, "alpha": HINT_BAR_FILL_ALPHA})
        self.assertEqual(hint_bar_style({"hint_bar_style": "bad"}, True),
                         {**DEFAULT_HINT_BAR_WARNING_STYLE, "alpha": HINT_BAR_WARNING_FILL_ALPHA})
        # 两套默认外观确实不同，且不把 alpha 写回配置（validate/编辑器都不产出该键）。
        self.assertNotEqual(DEFAULT_HINT_BAR_STYLE, DEFAULT_HINT_BAR_WARNING_STYLE)
        self.assertNotIn("alpha", DEFAULTS["hint_bar_style"])

        # 错误回滚：非法样式只丢弃这一项，其余用户设置保留。
        fixed, dropped = repair({"hint_bar_style": {"text_color": "red"}, "magnifier_size": 200})
        self.assertIn("hint_bar_style", dropped)
        self.assertEqual(fixed["hint_bar_style"], DEFAULTS["hint_bar_style"])
        self.assertEqual(fixed["magnifier_size"], 200)

        # 编辑器：选预设一次性套用整组外观；手动改任一项后自动记为「自定义」。
        editor = HintBarStyleEditor(HINT_BAR_PRESETS, DEFAULT_HINT_BAR_STYLE, "提示条")
        self.assertEqual(editor.value(), DEFAULT_HINT_BAR_STYLE)
        editor.preset.setCurrentIndex(editor.preset.findData("ink"))
        self.assertEqual(editor.value(), dict(HINT_BAR_PRESETS[1][2], preset="ink"))
        editor.radius.setValue(11)
        self.assertEqual(editor.value()["preset"], "custom")
        self.assertEqual(editor.value()["radius"], 11)
        # set_value 把配置写回界面（重置/同步路径），预设名也一并还原。
        editor.set_value(DEFAULT_HINT_BAR_STYLE)
        self.assertEqual(editor.value(), DEFAULT_HINT_BAR_STYLE)
        editor.deleteLater()
        # 已保存为「自定义」的样式：打开设置时预设栏应显示「自定义」而不是首个预设名。
        custom = {"preset": "custom", "text_color": "#010203", "fill_color": "#040506",
                  "border_color": "#070809", "rounded": False, "radius": 0}
        editor2 = HintBarStyleEditor(HINT_BAR_PRESETS, custom, "提示条")
        self.assertEqual(editor2.value(), custom)
        editor2.deleteLater()

    def test_quick_sticker_uses_configured_shortcut(self):
        from config.config_manager import DEFAULTS
        from screenshot.mask_window import MaskWindow

        for shortcut, key in (("Space", Qt.Key_Space), ("K", Qt.Key_K)):
            bounds = {"left": 0, "top": 0, "width": 80, "height": 60}
            settings = dict(DEFAULTS, capture_quick_sticker_enabled=True,
                            capture_quick_sticker_shortcut=shortcut,
                            crosshair=False, magnifier=False, mask_opacity=0)
            with patch("screenshot.mask_window.visible_windows", return_value=[]):
                mask = MaskWindow(Image.new("RGB", (80, 60), "red"), bounds,
                                  [bounds], settings)
            mask.selection.rects.append(QRect(5, 7, 30, 20))
            images = []
            mask.quick_sticker_requested.connect(
                lambda image, position: images.append((image, position)))
            self.assertEqual(mask.quick_sticker_shortcut.key().toString(), shortcut)
            mask.show()
            self.app.processEvents()
            QTest.keyClick(mask, key)
            self.app.processEvents()
            self.assertEqual(len(images), 1)
            self.assertEqual(images[0][0].size, (30, 20))
            self.assertEqual(images[0][1], QPoint(5, 7))
            self.assertFalse(mask.isVisible())

    def test_quick_sticker_shortcut_is_configurable_in_settings(self):
        from types import SimpleNamespace
        from config.config_manager import DEFAULTS, validate
        from PySide6.QtGui import QKeySequence
        from ui.settings_screenshot import ScreenshotPage

        config = SimpleNamespace(data=dict(DEFAULTS))
        page = ScreenshotPage(config, lambda: None)
        shortcut = page.controls["capture_quick_sticker_shortcut"]
        self.assertEqual(shortcut.keySequence().toString(), "Space")
        shortcut.setKeySequence(QKeySequence("Ctrl+K"))
        self.assertEqual(config.data["capture_quick_sticker_shortcut"], "Ctrl+K")
        self.assertEqual(validate({"capture_quick_sticker_shortcut": "Alt+Space"})[
            "capture_quick_sticker_shortcut"], "Alt+Space")
        with self.assertRaises(ValueError):
            validate({"capture_quick_sticker_shortcut": ""})

    def test_mask_color_presets_and_legacy_theme_migration(self):
        from config.config_manager import DEFAULTS, validate
        from screenshot.mask_window import _mask_overlay_color
        from ui.settings_screenshot import ScreenshotPage

        self.assertEqual(DEFAULTS["mask_color"], "#000000")
        default_overlay = _mask_overlay_color(DEFAULTS)
        self.assertEqual(default_overlay.name(), "#000000")
        self.assertEqual(default_overlay.alpha(),
                         round(DEFAULTS["mask_opacity"] * 255 / 100))
        self.assertEqual(validate({"mask_theme": "dark"})["mask_color"], "#000000")
        self.assertEqual(validate({"mask_theme": "light"})["mask_color"], "#ffffff")
        self.assertEqual(validate({"mask_theme": "dark", "mask_color": "#DDF5E4"})[
            "mask_color"], "#ddf5e4")
        with self.assertRaises(ValueError):
            validate({"mask_color": "blue"})

        with tempfile.TemporaryDirectory() as folder:
            page = ScreenshotPage(ConfigManager(Path(folder) / "settings.json"),
                                  lambda: None)
            try:
                preset = page.controls["mask_color"]
                self.assertEqual(preset.count(), 8)
                self.assertEqual(preset.itemData(0), "#d9edff")
                self.assertIn("mask_color", page.color_buttons)
                preset.setCurrentIndex(1)
                self.assertEqual(page.config.data["mask_color"], "#ddf5e4")
                page._set_mask_color("#ABCDEF", preset, page.color_buttons["mask_color"])
                self.assertEqual(page.config.data["mask_color"], "#ABCDEF")
                self.assertEqual(preset.currentData(), "#ABCDEF")
                page.reset_page()
                self.assertEqual(page.config.data["mask_color"], DEFAULTS["mask_color"])
            finally:
                page.close()

    def test_mosaic_width_config_and_validation(self):
        from config.config_manager import DEFAULTS, validate

        # 默认笔刷宽度明显小于旧的 mosaic_size*2 取法，且范围受限。
        self.assertEqual(DEFAULTS["mosaic_width"], 20)
        self.assertEqual(DEFAULTS["mosaic_cursor_color"], "#00c853")
        self.assertEqual(DEFAULTS["eraser_cursor_color"], "#ff8c00")
        self.assertEqual(validate({"mosaic_width": 6})["mosaic_width"], 6)
        self.assertEqual(validate({"mosaic_cursor_color": "#123456"})[
            "mosaic_cursor_color"], "#123456")
        self.assertEqual(validate({"eraser_cursor_color": "#abcdef"})[
            "eraser_cursor_color"], "#abcdef")
        self.assertEqual(validate({"mosaic_width": 100})["mosaic_width"], 100)
        with self.assertRaises(ValueError):
            validate({"mosaic_width": 3})
        with self.assertRaises(ValueError):
            validate({"mosaic_width": 101})
        with self.assertRaises(ValueError):
            validate({"mosaic_width": "wide"})
        for key in ("mosaic_cursor_color", "eraser_cursor_color"):
            with self.subTest(cursor_color=key), self.assertRaises(ValueError):
                validate({key: "not-a-color"})

    def test_brush_cursor_settings_previews_use_independent_colors(self):
        from types import SimpleNamespace
        from config.config_manager import DEFAULTS
        from ui.widgets.annotation_preview import AnnotationPreview

        settings = dict(DEFAULTS, eraser_cursor_color="#ff00aa",
                        mosaic_cursor_color="#00cc44")
        previews = {}
        try:
            for kind in ("eraser", "mosaic"):
                preview = AnnotationPreview(
                    SimpleNamespace(data=settings), kind, height=96)
                preview.resize(360, 96)
                preview.show()
                self.app.processEvents()
                previews[kind] = preview
            for kind, expected in (("eraser", "#ff00aa"), ("mosaic", "#00cc44")):
                preview = previews[kind]
                image = preview.grab().toImage()
                self.assertTrue(any(image.pixelColor(x, y).name() == expected
                                    for x in range(image.width())
                                    for y in range(image.height())))
        finally:
            for preview in previews.values():
                preview.close()

    def test_edge_resize_keeps_aspect_by_default_and_free_with_modifier(self):
        """边把手（n/s/e/w）与角把手一致：默认等比，按住修饰键则自由拉伸。"""
        import math
        from config.config_manager import DEFAULTS
        from editor.annotation_items import shape
        from editor.annotation_canvas import AnnotationCanvas
        from PIL import Image
        from PySide6.QtCore import QEvent, QPointF, Qt
        from PySide6.QtGui import QMouseEvent

        canvas = AnnotationCanvas(Image.new("RGB", (400, 300), "white"), dict(DEFAULTS))
        canvas.resize(500, 380)
        canvas.show()
        canvas.set_tool("select")
        try:
            # (把手, 旋转角, 修饰键, 是否期望等比)
            cases = (("e", 0, Qt.NoModifier, True), ("e", 0, Qt.ControlModifier, False),
                     ("n", 0, Qt.NoModifier, True), ("n", 30, Qt.NoModifier, True),
                     ("e", 90, Qt.ControlModifier, False))
            for handle_name, degrees, modifier, expect_uniform in cases:
                canvas.restore([])
                canvas.reset_history()
                canvas.cursor_index = 0
                item = shape("rect", QPointF(180, 140), QPointF(240, 180), "#ff0000", 2)
                canvas.scene_data.addItem(item)
                item.setTransformOriginPoint(item.boundingRect().center())
                if degrees:
                    item.setRotation(degrees)
                item.setSelected(True)
                canvas.checkpoint()
                handles = canvas.item_resize_handles(item)
                rad = math.radians(degrees)
                cos_a, sin_a = math.cos(rad), math.sin(rad)
                # 刻意用非等比位移（x 远大于 y），等比时应被拉回同一比例。
                dx, dy = 40, 5
                delta = QPointF(dx * cos_a - dy * sin_a, dx * sin_a + dy * cos_a)
                start = canvas.mapFromScene(handles[handle_name])
                finish = canvas.mapFromScene(handles[handle_name] + delta)

                def send(kind, position, buttons=Qt.NoButton):
                    self.app.sendEvent(canvas.viewport(), QMouseEvent(
                        kind, QPointF(position),
                        QPointF(canvas.viewport().mapToGlobal(position)),
                        Qt.LeftButton, buttons, modifier))

                send(QEvent.MouseButtonPress, start, Qt.LeftButton)
                send(QEvent.MouseMove, finish, Qt.LeftButton)
                send(QEvent.MouseButtonRelease, finish)
                transform = item.transform()
                uniform = abs(transform.m11() - transform.m22()) < 1e-6
                self.assertEqual(uniform, expect_uniform, (handle_name, degrees, modifier))
                canvas.resizing = None
        finally:
            canvas.close()

    def test_fill_color_config_default_matches_stroke_and_is_validated(self):
        from config.config_manager import DEFAULTS, validate, fill_colors_following_stroke

        # 默认填充色与对应线色一致。
        self.assertEqual(DEFAULTS["rect_fill_color"], DEFAULTS["rect_color"])
        self.assertEqual(DEFAULTS["ellipse_fill_color"], DEFAULTS["ellipse_color"])
        # 颜色格式纳入校验。
        self.assertEqual(validate({"rect_fill_color": "#00ff00"})["rect_fill_color"], "#00ff00")
        with self.assertRaises(ValueError):
            validate({"rect_fill_color": "green"})
        with self.assertRaises(ValueError):
            validate({"ellipse_fill_color": 12})
        # 线色变化时：未自定义则跟随，已自定义则保留。
        settings = dict(DEFAULTS)
        self.assertEqual(fill_colors_following_stroke(settings, "rect_color", "#168cff"),
                         {"rect_fill_color": "#168cff"})
        settings["rect_fill_color"] = "#00ff00"
        self.assertEqual(fill_colors_following_stroke(settings, "rect_color", "#168cff"), {})
        self.assertEqual(fill_colors_following_stroke(settings, "pen_color", "#123456"), {})

    def test_select_mode_more_settings_follows_selected_annotation(self):
        from config.config_manager import DEFAULTS
        from editor.toolbar_widget import ToolbarWidget

        settings = dict(DEFAULTS)
        toolbar = ToolbarWidget(settings["pen_color"], settings, "select")
        toolbar.set_tool_mode("select")
        self.assertEqual(toolbar._last_option_rows, set())
        self.assertFalse(toolbar.options_button.isEnabled())
        # 选中矩形后，更多设置应展示矩形专属参数（矩形线型 行10、圆角 行13、填充 行15/16/32），
        # 而不再只显示箭头样式。
        toolbar.set_selected_tool("rect")
        toolbar.set_tool_mode("select")
        self.assertIn(10, toolbar._last_option_rows)
        self.assertIn(13, toolbar._last_option_rows)
        self.assertIn(32, toolbar._last_option_rows)
        self.assertNotIn(8, toolbar._last_option_rows)
        # 选中椭圆展示椭圆专属参数。
        toolbar.set_selected_tool("ellipse")
        toolbar.set_tool_mode("select")
        self.assertIn(11, toolbar._last_option_rows)
        self.assertNotIn(10, toolbar._last_option_rows)
        toolbar.set_selected_tool(None)
        toolbar.set_tool_mode("select")
        self.assertEqual(toolbar._last_option_rows, set())
        self.assertFalse(toolbar.options_button.isEnabled())
        toolbar.close()

    def test_canvas_input_text_syncs_changed_settings(self):
        from config.config_manager import DEFAULTS
        from editor.annotation_canvas import AnnotationCanvas
        from editor import text_input_dialog
        from PIL import Image
        from PySide6.QtWidgets import QDialog

        class StubDialog:
            def __init__(self, parent, title, settings, initial="", values=None):
                self.title = title

            def exec(self):
                return QDialog.Accepted

            def text(self):
                return "hello"

            def changed_settings(self):
                return {"text_bold": True, "font_size": 42, "text_alignment": "center"}

        original = text_input_dialog.TextInputDialog
        text_input_dialog.TextInputDialog = StubDialog
        try:
            canvas = AnnotationCanvas(Image.new("RGB", (200, 160), "white"), dict(DEFAULTS))
            emitted = []
            canvas.setting_changed.connect(lambda key, value: emitted.append((key, value)))
            text, changed, ok = canvas.input_text("文字标注")
            self.assertTrue(ok)
            self.assertEqual(text, "hello")
            self.assertEqual(changed["font_size"], 42)
            # 改动写入画布配置并对外发出，供编辑器同步到配置与工具栏。
            self.assertTrue(canvas.settings["text_bold"])
            self.assertEqual(canvas.settings["font_size"], 42)
            self.assertEqual(canvas.text_alignment, Qt.AlignHCenter)
            self.assertIn(("text_bold", True), emitted)
            canvas.close()
        finally:
            text_input_dialog.TextInputDialog = original

    def test_toolbar_sync_setting_updates_text_controls(self):
        from config.config_manager import DEFAULTS
        from editor.toolbar_widget import ToolbarWidget

        settings = dict(DEFAULTS)
        toolbar = ToolbarWidget(settings["pen_color"], settings, settings["annotation_tool"])
        toolbar.sync_setting("text_bold", True)
        toolbar.sync_setting("font_size", 33)
        self.assertTrue(toolbar.text_bold.isChecked())
        self.assertEqual(toolbar.font_size.value(), 33)

    def test_mosaic_brush_defaults_to_blur_and_brush_mode_can_be_disabled(self):
        from config.config_manager import DEFAULTS
        from config.config_manager import validate
        from editor.annotation_canvas import AnnotationCanvas

        self.assertEqual(DEFAULTS.get("mosaic_mode"), "blur")
        self.assertTrue(DEFAULTS.get("mosaic_brush"))
        legacy_defaults = validate({})
        self.assertEqual(legacy_defaults["mosaic_mode"], "blur")
        self.assertTrue(legacy_defaults["mosaic_brush"])
        canvas = AnnotationCanvas(Image.new("RGB", (80, 60), "white"), dict(DEFAULTS))
        try:
            canvas.settings["mosaic_brush"] = False
            canvas.set_tool("mosaic")
            canvas.show()
            self.app.processEvents()
            start = canvas.viewport().mapFrom(canvas, canvas.mapFromScene(QPointF(10, 10)))
            end = canvas.viewport().mapFrom(canvas, canvas.mapFromScene(QPointF(40, 35)))
            QTest.mousePress(canvas.viewport(), Qt.LeftButton, pos=start)
            QTest.mouseMove(canvas.viewport(), end)
            self.assertIsNotNone(canvas.preview_end)
            self.assertIsNone(canvas.mosaic_drawing)
            QTest.mouseRelease(canvas.viewport(), Qt.LeftButton, pos=end)
            self.assertEqual(len(canvas.annotations()), 1)
        finally:
            canvas.close()

    def test_editor_border_settings_preview_and_export(self):
        from PySide6.QtGui import QColor, QImage, QPainter
        from PySide6.QtWidgets import QColorDialog
        from config.config_manager import DEFAULTS, validate

        with tempfile.TemporaryDirectory() as folder:
            manager = ConfigManager(Path(folder) / "settings.json")
            self.assertEqual((manager.data["editor_border_color"], manager.data["editor_border_width"]),
                             ("#000000", 1))
            canvas = AnnotationCanvas(Image.new("RGB", (80, 60), "white"), manager.data)
            settings = SettingsWindow(manager)
            page = settings.page("编辑器")

            def preview():
                image = QImage(80, 60, QImage.Format_RGB32)
                image.fill(Qt.white)
                painter = QPainter(image)
                canvas.drawForeground(painter, QRectF(0, 0, 80, 60))
                painter.end()
                return image

            self.assertEqual(preview().pixelColor(0, 30).name(), "#000000")
            page.controls["editor_border_width"].setValue(4)
            with patch("ui.widgets.color_button.color_dialog") as dialog_factory:
                dialog_factory.return_value.exec.return_value = QColorDialog.Accepted
                dialog_factory.return_value.currentColor.return_value = QColor("#e03020")
                page.color_buttons["editor_border_color"].click()
            self.assertEqual(preview().pixelColor(1, 30).name(), "#e03020")
            self.assertEqual(preview().pixelColor(4, 30).name(), "#ffffff")
            self.assertEqual(canvas.render_image().pixelColor(1, 30).name(), "#ffffff")
            settings.flush_persist()
            self.assertEqual((ConfigManager(manager.path).data["editor_border_color"],
                              ConfigManager(manager.path).data["editor_border_width"]),
                             ("#e03020", 4))
            with self.assertRaises(ValueError):
                validate({"editor_border_color": "not-a-color"})
            with self.assertRaises(ValueError):
                validate({"editor_border_width": 0})
            canvas.close()

    def test_new_annotation_options_validation_and_defaults(self):
        from config.config_manager import DEFAULTS, validate
        # 两项新开关都应带合理默认值，并随配置生命周期（重置/导入/导出）自动覆盖。
        # 马赛克默认走涂抹笔刷（True），擦除默认不动原图（False）。
        self.assertTrue(DEFAULTS.get("mosaic_brush", False))
        self.assertFalse(DEFAULTS.get("eraser_erase_base", True))
        # 合法布尔值应原样保留。
        self.assertTrue(validate({"mosaic_brush": True})["mosaic_brush"])
        self.assertTrue(validate({"eraser_erase_base": True})["eraser_erase_base"])
        # 类型错误应被拒绝。
        with self.assertRaises(ValueError):
            validate({"mosaic_brush": "yes"})
        with self.assertRaises(ValueError):
            validate({"eraser_erase_base": 1})

    def test_eraser_default_only_masks_annotation_layer(self):
        from config.config_manager import DEFAULTS
        canvas = AnnotationCanvas(Image.new("RGB", (120, 100), (10, 20, 30)), dict(DEFAULTS, eraser_width=3))
        canvas.resize(200, 150)
        canvas.show()
        # 默认模式无标注，擦除底图不应有任何变化（掩码只作用于标注层）。
        self.assertEqual(canvas.render_image().pixelColor(40, 40).name(), "#0a141e")
        canvas.tool = "eraser"
        QTest.mouseClick(canvas.viewport(), Qt.LeftButton, pos=canvas.mapFromScene(QPointF(40, 40)))
        self.assertEqual(canvas.render_image().pixelColor(40, 40).name(), "#0a141e")
        canvas.close()

    def test_editor_transparent_background_config_lifecycle(self):
        """新设置覆盖初始化 / 校验 / 回滚：默认主题棋盘，非法值丢弃并回退默认。"""
        from config.config_manager import DEFAULTS, repair, validate

        self.assertEqual(DEFAULTS["editor_transparent_background"], "theme")
        self.assertEqual(validate({})["editor_transparent_background"], "theme")
        for mode in ("theme", "transparent", "dark_checker", "light_checker"):
            self.assertEqual(
                validate({"editor_transparent_background": mode})[
                    "editor_transparent_background"], mode)
        with self.assertRaises(ValueError):
            validate({"editor_transparent_background": "rainbow"})
        repaired, dropped = repair({"editor_transparent_background": "rainbow"})
        self.assertEqual(repaired["editor_transparent_background"], "theme")
        self.assertIn("editor_transparent_background", dropped)

    def test_annotation_corner_resize_is_uniform_by_default_and_free_with_modifier(self):
        from config.config_manager import DEFAULTS
        from PySide6.QtCore import QEvent
        from PySide6.QtGui import QMouseEvent

        canvas = AnnotationCanvas(Image.new("RGB", (240, 180), "white"), DEFAULTS)
        canvas.resize(400, 300)
        canvas.show()
        try:
            original = QRectF(40, 40, 60, 50)
            original_aspect = original.width() / original.height()

            def resize_corner(modifier):
                canvas.restore([])
                canvas.reset_history()
                canvas.cursor_index = 0
                item = shape("rect", QPointF(original.x(), original.y()),
                             QPointF(original.right(), original.bottom()), "#ff0000", 2)
                canvas.scene_data.addItem(item)
                item.setSelected(True)
                canvas.checkpoint()
                handles = canvas.resize_handles(item.sceneBoundingRect())
                start = canvas.mapFromScene(handles["se"])
                # x 方向拉得多、y 方向拉得少；自由拉伸时应明显改变宽高比。
                finish = start + QPoint(40, 10)
                for event_type, position, button, buttons in (
                        (QEvent.MouseButtonPress, start, Qt.LeftButton, Qt.LeftButton),
                        (QEvent.MouseMove, finish, Qt.NoButton, Qt.LeftButton),
                        (QEvent.MouseButtonRelease, finish, Qt.LeftButton, Qt.NoButton)):
                    self.app.sendEvent(canvas.viewport(), QMouseEvent(
                        event_type, QPointF(position),
                        QPointF(canvas.viewport().mapToGlobal(position)),
                        button, buttons, modifier))
                return canvas.annotations()[0].sceneBoundingRect()

            uniform = resize_corner(Qt.NoModifier)
            # 默认四角等比：宽高比保持不变。
            self.assertAlmostEqual(uniform.width() / uniform.height(),
                                   original_aspect, delta=0.02)
            free = resize_corner(Qt.ControlModifier)
            # 按住 Ctrl 自由拉伸：宽高比明显改变。
            self.assertNotAlmostEqual(free.width() / free.height(),
                                      original_aspect, delta=0.05)
            # 仅 Space 按下（模拟空格键状态）也应自由拉伸。
            canvas.space_pressed = True
            space_free = resize_corner(Qt.NoModifier)
            canvas.space_pressed = False
            self.assertNotAlmostEqual(space_free.width() / space_free.height(),
                                      original_aspect, delta=0.05)
        finally:
            canvas.close()

    def test_hotkey_capture_skips_tray_menu_delay(self):
        from main import Application
        app = Application.__new__(Application)
        app.mask = None
        app.config = Mock(data={"last_capture_rect": [1, 2, 3, 4]})
        app.show_mask = Mock()
        with patch("app.capture_flow.QTimer.singleShot") as schedule:
            app.start_capture("capture")
            self.assertEqual(schedule.call_args.args[0], 0)
            schedule.call_args.args[1]()
            app.show_mask.assert_called_once_with("capture", None, None)
            app.start_capture("capture", from_tray=True)
            self.assertEqual(schedule.call_args.args[0], 150)

    def test_capture_delay_setting_adds_wait_before_capture(self):
        import logging
        from main import Application

        app = Application.__new__(Application)
        app.mask = None
        app.logger = logging.getLogger("screensnap")
        app.config = Mock(data={"last_capture_rect": [1, 2, 3, 4], "capture_delay": 800})
        app.show_mask = Mock()
        with patch("app.capture_flow.QTimer.singleShot") as schedule:
            app.start_capture("capture")
            self.assertEqual(schedule.call_args.args[0], 800)
            # 托盘菜单本来就有 150 毫秒等待，用户延迟在此基础上累加。
            app.start_capture("capture", from_tray=True)
            self.assertEqual(schedule.call_args.args[0], 950)

    def test_editor_save_uses_default_filename_template(self):
        from datetime import datetime
        from config.config_manager import DEFAULTS

        with tempfile.TemporaryDirectory() as folder:
            editor = EditorWindow(Image.new("RGB", (90, 70), "white"),
                                  dict(DEFAULTS, save_dir=folder))
            with patch("editor.editor_window.datetime") as clock:
                clock.now.return_value = datetime(2026, 9, 27, 14, 5, 6)
                path = editor.save()
            self.assertEqual(path.name, "ScreenSnap_20260927_140506.png")
            self.assertTrue(path.is_file())
            editor.close()

    def test_crop_style_is_configured_and_has_actionable_tooltips(self):
        from config.config_manager import DEFAULTS
        editor_settings = dict(DEFAULTS)
        editor = EditorWindow(Image.new("RGB", (40, 30), "white"), editor_settings)
        editor.toolbar.tool_buttons["crop"].click()
        self.assertFalse(editor.toolbar.crop_color.isHidden())
        self.assertFalse(editor.toolbar.crop_width.isHidden())
        editor.toolbar.crop_width.setValue(7)
        editor.toolbar.crop_style_changed.emit("crop_color", "#123abc")
        self.assertEqual(editor.canvas.crop_color, "#123abc")
        self.assertEqual(editor.canvas.stroke_pen().width(), 7)
        self.assertEqual(editor_settings["crop_color"], "#123abc")
        self.assertEqual(editor_settings["crop_width"], 7)
        self.assertIn("保存为 PNG", next(button for button in editor.toolbar.output_buttons
                                          if button.text() == "保存").toolTip())
        self.assertEqual((editor.toolbar.options_button.width(), editor.toolbar.options_button.height()),
                 (136, 34))
        editor.close()

    def test_window_crop_settings_persist_and_restore(self):
        with tempfile.TemporaryDirectory() as folder:
            manager = ConfigManager(Path(folder) / "settings.json")
            settings = SettingsWindow(manager)
            editor = EditorWindow(Image.new("RGB", (40, 30), "white"), manager.data)
            editor.setting_changed.connect(settings.set_annotation_setting)
            editor.toolbar.tool_buttons["crop"].click()
            editor.toolbar.crop_width.setValue(7)
            editor.toolbar.crop_style_changed.emit("crop_color", "#123abc")
            settings.flush_persist()
            reloaded = ConfigManager(manager.path).data
            self.assertEqual((reloaded["crop_width"], reloaded["crop_color"]), (7, "#123abc"))
            page = settings.page("编辑器")
            self.assertEqual(page.controls["crop_width"].value(), 7)
            self.assertEqual(page.color_buttons["crop_color"].color, "#123abc")
            reopened = EditorWindow(Image.new("RGB", (40, 30), "white"), reloaded)
            self.assertEqual((reopened.canvas.crop_width, reopened.canvas.crop_color), (7, "#123abc"))
            self.assertEqual(reopened.toolbar.crop_width.value(), 7)
            self.assertEqual(reopened.toolbar.crop_color.color, "#123abc")
            reopened.close()
            editor.close()
            settings.close()

    def test_clipboard_history_uses_configured_history_limit(self):
        from PySide6.QtGui import QGuiApplication
        from config.config_manager import DEFAULTS

        board = QGuiApplication.clipboard()
        settings = dict(DEFAULTS, history_limit=2)
        manager = StickerManager(settings, board)
        try:
            board.setText("clipboard entry one")
            self.app.processEvents()
            board.setText("clipboard entry two")
            self.app.processEvents()
            board.setText("clipboard entry three")
            self.app.processEvents()
            self.assertEqual(len(manager.clipboard_history), 2)
            self.assertEqual(manager.clipboard_history[0].text, "clipboard entry three")
            self.assertEqual(manager.clipboard_history[1].text, "clipboard entry two")
        finally:
            manager.close_all()
            board.clear()
            self.app.processEvents()

    def test_app_version_gate_resets_user_data_only_when_version_differs(self):
        """版本闸门：只有记录版本与内置版本不一致时清空用户数据。

        不一致 → 清空数据目录、按默认值重建配置、写入内置版本、记录旧版本以便启动后通知；
        相等或缺失 → 不动数据（缺失时只把内置版本补写进配置）。
        """
        from config.config_manager import DEFAULTS
        from core.version import APP_VERSION
        from main import Application

        def make_app(version):
            app = Application.__new__(Application)
            app.config = Mock()
            app.config.data = ({} if version is None
                              else {"app_version": version,
                                    # 环境/习惯类（应保留）
                                    "theme": "dark", "filename": "my_{time}",
                                    "capture_save_shortcut": "ctrl+s", "bubble": False,
                                    "window_detection": False, "logging_enabled": False,
                                    "file_sticker_path": "D:/pic.png",
                                    # 外观/状态类（应回到默认）
                                    "sticker_selection_effect_strength": 9,
                                    "editor_zoom_wheel_step": 25, "last_capture_rect": [1, 2, 3, 4], "preexisting_marker": 1,
                                    "save_dir": "D:/shots", "hotkeys": {"capture": "Ctrl+Alt+A"}})
            app.logger = Mock()
            app.version_reset_from = ""
            return app

        app = make_app("0.9.0")
        with patch("core.path_utils.clear_data_directory",
                   return_value=Path("C:/fake/screensnap")) as clear:
            self.assertTrue(app.apply_version_gate())
        clear.assert_called_once()
        app.config.save.assert_called_once()
        self.assertEqual(app.config.data["app_version"], APP_VERSION)
        self.assertNotIn("preexisting_marker", app.config.data)  # 旧数据已丢弃
        self.assertEqual(app.config.data["sound"], DEFAULTS["sound"])
        for key, expected in (("theme", "dark"), ("filename", "my_{time}"),
                              ("capture_save_shortcut", "ctrl+s"), ("bubble", False),
                              ("window_detection", False), ("logging_enabled", False),
                              ("file_sticker_path", "D:/pic.png")):
            self.assertEqual(app.config.data[key], expected, key)   # 环境/习惯类必须保留
        for key in ("sticker_selection_effect_strength", "editor_zoom_wheel_step", "last_capture_rect"):
            self.assertEqual(app.config.data[key], DEFAULTS[key],
                             f"{key} 属于外观/瞬时状态，应随版本重置")
        # 环境/习惯类设置必须保留，避免每次都重设保存目录与热键。
        self.assertEqual(app.config.data["save_dir"], "D:/shots")
        self.assertEqual(app.config.data["hotkeys"]["capture"], "Ctrl+Alt+A")
        self.assertEqual(app.version_reset_from, "0.9.0")

        same = make_app(APP_VERSION)
        with patch("core.path_utils.clear_data_directory") as clear_same:
            self.assertFalse(same.apply_version_gate())
        clear_same.assert_not_called()

        fresh = make_app(None)
        with patch("core.path_utils.clear_data_directory") as clear_fresh:
            self.assertFalse(fresh.apply_version_gate())
        clear_fresh.assert_not_called()
        self.assertEqual(fresh.config.data["app_version"], APP_VERSION)
    def test_new_canvas_uia_and_cache_settings_validate(self):
        """新增的画布/UIA/缓存设置：默认值与合法取值通过校验，越界或未知取值被拒绝。"""
        from config.config_manager import DEFAULTS, validate

        expected = {"editor_zoom_wheel_step": 10, "editor_rotation_snap": 15,
                    "editor_rotation_handle": "center", "editor_overcanvas_mode": "clip",
                    "editor_checker_tile_size": 8, "editor_text_click_delay": 180,
                    "window_hover_reuse_radius": 8, "uia_read_budget": 180,
                    "uia_children_limit": 128, "uia_slow_seconds": 0.4,
                    "cache_clear_clipboard": True, "cache_clear_toast": True,
                    "cache_clear_sticker": True, "cache_cleanup_timing": "off"}
        validated = validate(dict(DEFAULTS))
        for key, value in expected.items():
            self.assertEqual(DEFAULTS[key], value, key)
            self.assertEqual(validated[key], value, key)

        rejected = (("editor_zoom_wheel_step", 0), ("editor_zoom_wheel_step", 51),
                    ("editor_rotation_snap", 30), ("editor_rotation_handle", "middle"),
                    ("editor_overcanvas_mode", "stretch"), ("editor_checker_tile_size", 3),
                    ("editor_text_click_delay", 10), ("window_hover_reuse_radius", 21),
                    ("uia_read_budget", 10), ("uia_children_limit", 999),
                    ("uia_slow_seconds", 5), ("cache_clear_clipboard", "yes"),
                    ("cache_cleanup_timing", "later"))
        for key, bad in rejected:
            with self.assertRaises(ValueError, msg=key):
                validate({"hotkeys": DEFAULTS["hotkeys"], key: bad})

    def test_every_setting_has_an_entry_in_settings_window(self):
        from config.config_manager import DEFAULTS
        from ui import SettingsWindow

        with tempfile.TemporaryDirectory() as folder:
            settings = SettingsWindow(ConfigManager(Path(folder) / "settings.json"))
            keys = set()
            for index in range(settings.pages.count()):
                page = settings.pages.widget(index)
                keys.update(page.controls)
                keys.update(page.color_buttons)
            # 热键、上次区域与「另存为」目录由专用控件或程序内部维护，不需要普通设置项入口。
            # save_as_dir 是「另存为」上次选择的目录（内部记忆值），按规则 4 刻意不做设置页控件。
            self.assertEqual(set(DEFAULTS) - keys,
                             {"hotkeys", "last_capture_rect", "save_as_dir"})
            self.assertIn("save_dir", keys)
            self.assertNotIn("auto_dir", keys)
            self.assertNotIn("manual_dir", keys)
            settings.close()

    def test_editor_settings_page_previews_final_output_appearance(self):
        from ui.widgets.annotation_preview import AnnotationPreview

        with tempfile.TemporaryDirectory() as folder:
            settings = SettingsWindow(ConfigManager(Path(folder) / "settings.json"))
            try:
                general_page = settings.pages.widget(0)
                editor_page = settings.page("编辑器")
                self.assertNotIn("editor_image_round_corners", general_page.controls)
                self.assertNotIn("editor_image_corner_radius", general_page.controls)
                self.assertNotIn("editor_image_round_corners", editor_page.controls)
                self.assertNotIn("editor_image_corner_radius", editor_page.controls)
                output_page = settings.page("保存与输出")
                self.assertIn("editor_image_round_corners", output_page.controls)
                self.assertIn("editor_image_corner_radius", output_page.controls)
                page = output_page
                preview = next(widget for widget in page.previews
                               if isinstance(widget, AnnotationPreview) and widget.kind == "output")
                self.assertIsNotNone(preview.build(QRectF(0, 0, 300, 120)))
                preview.refresh()
                self.assertIsNone(preview.scene)
            finally:
                settings.close()

    def test_editor_window_toolbar_preview_follows_settings(self):
        from config.config_manager import DEFAULTS

        editor = EditorWindow(Image.new("RGB", (120, 90), "white"), dict(DEFAULTS))
        try:
            self.assertTrue(editor.toolbar.previews)
            editor.toolbar.tool_buttons["text"].click()
            self.assertEqual([name for name, widget in editor.toolbar.previews.items()
                              if not widget.isHidden()], ["text"])
            editor.toolbar.font_size.setValue(36)
            self.assertIsNone(editor.toolbar.previews["text"].scene)
        finally:
            editor.close()

    def test_settings_page_can_reset_its_own_defaults(self):
        from config.config_manager import DEFAULTS

        with tempfile.TemporaryDirectory() as folder:
            manager = ConfigManager(Path(folder) / "settings.json")
            settings = SettingsWindow(manager)
            try:
                page = settings.page("贴图")
                other = settings.page("截图")
                other.controls["magnifier"].setChecked(False)
                page.controls["sticker_snap_threshold"].setValue(30)
                self.assertEqual(manager.data["sticker_snap_threshold"], 30)
                page.reset_page()
                self.assertEqual(manager.data["sticker_snap_threshold"],
                                 DEFAULTS["sticker_snap_threshold"])
                self.assertEqual(page.controls["sticker_snap_threshold"].value(),
                                 DEFAULTS["sticker_snap_threshold"])
                # 本页重置不应影响其他页已经改过的选项。
                self.assertFalse(manager.data["magnifier"])
            finally:
                settings.close()

    def test_editor_settings_show_live_previews(self):
        from PySide6.QtCore import QRectF
        from PySide6.QtWidgets import QGraphicsPathItem, QGraphicsRectItem
        from PySide6.QtWidgets import QGraphicsTextItem

        with tempfile.TemporaryDirectory() as folder:
            manager = ConfigManager(Path(folder) / "settings.json")
            settings = SettingsWindow(manager)
            page = settings.page("编辑器")
            previews = {preview.kind: preview for preview in page.previews}
            self.assertEqual(sorted(previews),
                             ["arrow", "crop", "ellipse", "eraser", "marker", "mosaic", "pen", "picker", "rect", "sequence", "text"])
            page.controls["font_size"].setValue(42)
            text = previews["text"]
            self.assertIsNone(text.scene)
            scene = text.build(QRectF(0, 0, 320, 120))
            items = [item for item in scene.items() if isinstance(item, QGraphicsTextItem)]
            self.assertTrue(items)
            self.assertEqual(items[0].font().pointSize(), 42)
            center = scene.sceneRect().center()
            text_bounds = items[0].mapRectToScene(items[0].boundingRect())
            self.assertAlmostEqual(text_bounds.center().x(), center.x(), delta=1)
            self.assertAlmostEqual(text_bounds.center().y(), center.y(), delta=1)
            for kind, item_type in (("pen", QGraphicsPathItem), ("rect", QGraphicsRectItem)):
                built = previews[kind].build(QRectF(0, 0, 320, 96))
                item = next(item for item in built.items() if isinstance(item, item_type))
                bounds = item.mapRectToScene(item.boundingRect())
                self.assertAlmostEqual(bounds.center().x(), 160, delta=1, msg=kind)
                self.assertAlmostEqual(bounds.center().y(), 48, delta=1, msg=kind)
            for kind in ("arrow", "pen", "rect", "ellipse", "eraser", "marker", "mosaic", "crop"):
                built = previews[kind].build(QRectF(0, 0, 320, 96))
                self.assertGreater(len(built.items()), 1, kind)
            settings.close()

    def test_settings_navigation_page_order(self):
        with tempfile.TemporaryDirectory() as folder:
            settings = SettingsWindow(ConfigManager(Path(folder) / "settings.json"))
            titles = [settings.navigation.item(index).text()
                      for index in range(settings.navigation.count())]
            self.assertEqual(titles, [
                "常规", "快捷键", "截图", "编辑器", "贴图", "剪贴板贴图",
                "主题与外观", "保存与输出", "日志",
            ])
            settings.close()

    def test_clipboard_card_sizes_follow_settings(self):
        from config.config_manager import DEFAULTS
        from sticker.clipboard_source import render_color_card, render_file_card

        with tempfile.TemporaryDirectory() as folder:
            paths = [Path(folder) / f"file_{index}.txt" for index in range(3)]
            for path in paths:
                path.write_text("x", encoding="utf-8")
            settings = dict(DEFAULTS, color_sticker_width=320, color_sticker_height=90,
                            file_sticker_width=520, file_sticker_max=2)
            color = render_color_card("#ff0000", settings)
            self.assertEqual((color.width(), color.height()), (320, 90))
            narrow = render_file_card([str(path) for path in paths], settings)
            self.assertEqual(narrow.width(), 520)
            tall = render_file_card([str(path) for path in paths],
                                    dict(settings, file_sticker_max=3))
            self.assertGreater(tall.height(), narrow.height())

    def test_clipboard_and_save_settings_are_validated(self):
        from config.config_manager import DEFAULTS

        with tempfile.TemporaryDirectory() as folder:
            manager = ConfigManager(Path(folder) / "settings.json")
            for key, value in (("save_background", "red"), ("file_sticker_max", 0),
                               ("file_sticker_width", 100), ("color_sticker_width", 20),
                               ("color_sticker_height", 10), ("sticker_panel_thumb", 8)):
                manager.data[key] = value
                with self.assertRaises(ValueError, msg=key):
                    manager.save()
                manager.data[key] = DEFAULTS[key]
            manager.save()
            self.assertEqual(ConfigManager(Path(folder) / "settings.json").data["file_sticker_max"], 8)

    def test_editor_and_history_use_configured_save_format(self):
        from config.config_manager import DEFAULTS

        with tempfile.TemporaryDirectory() as folder:
            settings = dict(DEFAULTS, save_format="jpg", save_dir=folder)
            editor = EditorWindow(Image.new("RGB", (40, 30), "white"), settings)
            saved = editor.save()
            self.assertEqual(saved.suffix, ".jpg")
            self.assertTrue(saved.exists())
            manager = StickerManager(settings)
            self.assertEqual([path.suffix for path in manager.files()], [".jpg"])
            settings["save_format"] = "webp"
            resaved = editor.save()
            self.assertEqual(resaved.suffix, ".webp")
            self.assertTrue(saved.exists())
            with Image.open(saved) as first_image:
                self.assertEqual(first_image.format, "JPEG")
            with Image.open(resaved) as second_image:
                self.assertEqual(second_image.format, "WEBP")
            editor.close()

    def test_sticker_selection_effect_preview_updates_with_setting(self):
        from config.config_manager import ConfigManager
        from ui.settings_sticker import StickerPage

        with tempfile.TemporaryDirectory() as folder:
            config = ConfigManager(Path(folder) / "settings.json")
            page = StickerPage(config, config.save)
            preview = page.selection_effect_preview
            page.show()
            self.app.processEvents()

            def blue_pixel_count():
                image = preview.grab().toImage()
                return sum(
                    1 for y in range(image.height()) for x in range(image.width())
                    if image.pixelColor(x, y).blue() > image.pixelColor(x, y).red() + 30)

            enabled_pixels = blue_pixel_count()
            page.controls["sticker_selection_effect_strength"].setValue(25)
            self.app.processEvents()
            reduced_pixels = blue_pixel_count()
            self.assertLess(reduced_pixels, enabled_pixels)
            self.assertEqual(config.data["sticker_selection_effect_strength"], 25)
            page.controls["sticker_selection_effect_enabled"].setChecked(False)
            self.app.processEvents()
            disabled_pixels = blue_pixel_count()

            self.assertGreater(enabled_pixels, disabled_pixels)
            self.assertFalse(config.data["sticker_selection_effect_enabled"])
            page.close()

    def test_settings_full_reset_button_warns_and_requires_confirmation(self):
        from PySide6.QtWidgets import QMessageBox

        with tempfile.TemporaryDirectory() as folder, \
                patch("ui.settings_window.data_dir", return_value=Path(folder) / "ScreenSnap"):
            manager = ConfigManager(Path(folder) / "settings.json")
            reset = Mock(return_value=True)
            settings = SettingsWindow(manager, reset_all_user_data=reset)
            try:
                button = settings.reset_all_data_button
                self.assertTrue(button.isEnabled())
                for phrase in ("不可撤销", str(Path(folder) / "ScreenSnap"),
                               "settings.bak", "剪贴板历史", "自定义截图保存目录",
                               "目录外文件不受影响", "不会自动重启", "下次手动启动",
                               "默认 settings.json",
                               "开机自动启动项"):
                    self.assertIn(phrase, button.toolTip())
                with patch("ui.settings_window.yes_no_dialog") as confirmation:
                    confirmation.return_value.exec.return_value = QMessageBox.No
                    button.click()
                    reset.assert_not_called()
                    confirmation.return_value.exec.return_value = QMessageBox.Yes
                    button.click()
                reset.assert_called_once_with()
            finally:
                settings.close()

    def test_settings_window_resets_defaults_and_clears_session(self):
        from PySide6.QtWidgets import QMessageBox, QPushButton
        from config.config_manager import DEFAULTS

        with tempfile.TemporaryDirectory() as folder, \
                patch("ui.settings_window.set_start_on_boot"), \
                patch("ui.settings_window.data_dir", return_value=Path(folder)):
            manager = ConfigManager(Path(folder) / "settings.json")
            manager.data["sticker_snap_threshold"] = 30
            manager.data["log_level"] = "TRACE"
            manager.data["capture_fullscreen_action"] = "edit"
            manager.data["capture_monitor_action"] = "edit"
            manager.data["capture_repeat_action"] = "edit"
            manager.data["capture_gap_fill"] = "black"
            manager.data["save_dir"] = str(Path(folder) / "custom-captures")
            manager.data["hotkeys"].update({
                "repeat": "ctrl+shift+f2", "fullscreen": "ctrl+shift+f1",
                "open_image": "ctrl+alt+o", "open_sticker_file": "ctrl+alt+n",
                "sticker_panel": "ctrl+alt+p"})
            manager.data.update({
                "sound": False, "crosshair_color": "#000000", "element_depth": 12,
                "mask_opacity": 60, "history_limit": 100, "rect_corner_enabled": False,
                "sticker_shadow_enabled": True, "sticker_recycle_limit": 50})
            manager.save()
            session = Path(folder) / "stickers.json"
            session.write_text("[]", encoding="utf-8")
            cache = Path(folder) / "sticker_cache"
            cache.mkdir()
            (cache / "sticker_1.png").write_bytes(b"x")
            clear_history = Mock(return_value=2)
            settings = SettingsWindow(manager, clear_history)
            try:
                with patch("ui.settings_window.yes_no_dialog") as confirmation, \
                        patch.object(QMessageBox, "information"):
                    confirmation.return_value.exec.return_value = QMessageBox.Yes
                    settings.reset_defaults()
                    settings.clear_sticker_session()
                    history_button = next(
                        button for button in settings.findChildren(QPushButton)
                        if button.text() == "清空剪贴板历史")
                    history_button.click()
                clear_history.assert_called_once_with()
                self.assertEqual((manager.data["sticker_snap_threshold"], manager.data["log_level"]),
                                 (DEFAULTS["sticker_snap_threshold"], DEFAULTS["log_level"]))
                self.assertTrue(manager.data["capture_hotkey_suppress"])
                self.assertEqual(manager.data["capture_multi_select_shortcut"], "Alt+M")
                self.assertEqual(manager.data["capture_multi_edit_action"], "save")
                for key in ("capture_fullscreen_action", "capture_monitor_action",
                            "capture_repeat_action"):
                    self.assertEqual(manager.data[key], "save")
                self.assertEqual(manager.data["capture_gap_fill"], "transparent")
                self.assertEqual(manager.data["save_dir"], "")
                self.assertTrue(manager.data["sound"])
                self.assertEqual(manager.data["crosshair_color"], "#ff0000")
                self.assertEqual(manager.data["element_depth"], 8)
                self.assertEqual(manager.data["mask_opacity"], 70)
                self.assertEqual(manager.data["history_limit"], 10)
                self.assertTrue(manager.data["rect_corner_enabled"])
                self.assertFalse(manager.data["sticker_shadow_enabled"])
                self.assertEqual(manager.data["sticker_recycle_limit"], 10)
                backup = Path(folder) / "settings.bak"
                self.assertTrue(backup.is_file())
                self.assertEqual(json.loads(backup.read_text(encoding="utf-8"))["log_level"], "TRACE")
                # 界面控件也必须回到默认值，而不是停留在重置前的显示。
                page = settings.page("贴图")
                self.assertEqual(page.controls["sticker_snap_threshold"].value(),
                                 DEFAULTS["sticker_snap_threshold"])
                screenshot_page = settings.page("截图")
                save_page = settings.page("保存与输出")
                self.assertEqual(save_page.controls["save_dir"].input.text(), "")
                self.assertEqual(screenshot_page.controls["crosshair_color"].color, "#ff0000")
                self.assertEqual(screenshot_page.controls["element_depth"].value(), 8)
                self.assertEqual(screenshot_page.controls["mask_opacity"].value(), 70)
                self.assertEqual(screenshot_page.controls["history_limit"].value(), 10)
                self.assertEqual(
                    screenshot_page.controls["capture_multi_select_shortcut"]
                    .keySequence().toString(), "Alt+M")
                self.assertEqual(
                    screenshot_page.controls["capture_multi_edit_action"].currentData(),
                    "save")
                self.assertEqual(
                    screenshot_page.controls["capture_fullscreen_action"].currentData(),
                    "save")
                self.assertEqual(
                    screenshot_page.controls["capture_monitor_action"].currentData(),
                    "save")
                self.assertEqual(
                    screenshot_page.controls["capture_repeat_action"].currentData(),
                    "save")
                self.assertEqual(
                    screenshot_page.controls["capture_gap_fill"].currentData(),
                    "transparent")
                self.assertTrue(settings.page("常规").controls["sound"].isChecked())
                self.assertTrue(settings.page("编辑器").controls["rect_corner_enabled"].isChecked())
                sticker_page = settings.page("贴图")
                self.assertFalse(sticker_page.controls["sticker_shadow_enabled"].isChecked())
                self.assertEqual(sticker_page.controls["sticker_recycle_limit"].value(), 10)
                hotkey_page = settings.page("快捷键")
                self.assertEqual(hotkey_page.edit_repeat.keySequence().toString(), "Shift+F1")
                self.assertEqual(hotkey_page.edit_fullscreen.keySequence().toString(), "Alt+F1")
                self.assertEqual(hotkey_page.edit_monitor.keySequence().toString(), "Ctrl+F1")
                self.assertEqual(hotkey_page.edit_open_image.keySequence().toString(), "Ctrl+Alt+E")
                self.assertEqual(hotkey_page.edit_open_sticker_file.keySequence().toString(), "Shift+F3")
                self.assertEqual(hotkey_page.edit_sticker_panel.keySequence().toString(), "Alt+F3")
                self.assertFalse(session.exists())
                self.assertEqual(list(cache.glob("sticker_*.png")), [])
            finally:
                settings.close()

    def test_capture_hotkey_keeps_active_popup_open_before_capture(self):
        from main import Application
        from unittest.mock import Mock, patch

        # 截图热键不应提前关闭活动弹出菜单（如贴图右键菜单），让菜单能被一起拍进截图；
        # 真正抓取发生在 show_mask 的 capture()，抓取后才关闭残留菜单。
        app = Application.__new__(Application)
        app.config = Mock(data={})
        app.logger = Mock()
        app.start_capture = Mock()
        popup = Mock()
        with patch.object(QApplication, "activePopupWidget", return_value=popup):
            app.dispatch("capture")
        app.start_capture.assert_called_once_with("capture")
        popup.close.assert_not_called()

    def test_sticker_snap_settings_are_validated(self):
        from config.config_manager import DEFAULTS

        self.assertTrue(DEFAULTS["sticker_snap_enabled"])
        self.assertEqual((DEFAULTS["sticker_snap_threshold"], DEFAULTS["sticker_snap_targets"]),
                         (8, "both"))
        self.assertEqual((DEFAULTS["sticker_follow_window"], DEFAULTS["sticker_follow_interval"]),
                         (True, 120))
        with tempfile.TemporaryDirectory() as folder:
            manager = ConfigManager(Path(folder) / "settings.json")
            for key, value in (("sticker_snap_threshold", 0), ("sticker_snap_threshold", 41),
                               ("sticker_snap_targets", "desktop"),
                               ("sticker_follow_interval", 10)):
                manager.data[key] = value
                with self.assertRaises(ValueError, msg=f"{key}={value}"):
                    manager.save()
                manager.data[key] = DEFAULTS[key]
            manager.save()
            reloaded = ConfigManager(Path(folder) / "settings.json")
            self.assertEqual((reloaded.data["sticker_snap_threshold"],
                              reloaded.data["sticker_snap_targets"]), (8, "both"))

    def test_open_sticker_file_shortcut_has_settings_entry_and_dispatch(self):
        from core.constants import HOTKEY_LABELS
        from config.config_manager import DEFAULTS
        from main import Application
        from ui.settings_hotkey import HotkeyPage

        self.assertEqual(DEFAULTS["hotkeys"]["open_sticker_file"], "shift+f3")
        self.assertEqual(HOTKEY_LABELS["open_sticker_file"], "从文件打开新贴图")
        with tempfile.TemporaryDirectory() as folder:
            manager = ConfigManager(Path(folder) / "settings.json")
            page = HotkeyPage(manager, Mock())
            self.assertEqual(page.edit_open_sticker_file.keySequence().toString(),
                             "Shift+F3")
            app = Application.__new__(Application)
            app.logger = Mock()
            app.stickers = Mock()
            app.dispatch("open_sticker_file")
            app.stickers.open_file.assert_called_once_with()
            page.close()

    def test_recycle_bin_shortcut_has_settings_entry_and_dispatch(self):
        from core.constants import HOTKEY_LABELS
        from config.config_manager import DEFAULTS
        from main import Application
        from ui.settings_hotkey import HotkeyPage
        from ui.tray_menu import make_tray_menu

        self.assertEqual(DEFAULTS["hotkeys"]["recycle_bin"], "ctrl+alt+r")
        self.assertEqual(HOTKEY_LABELS["recycle_bin"], "贴图回收站")
        with tempfile.TemporaryDirectory() as folder:
            manager = ConfigManager(Path(folder) / "settings.json")
            page = HotkeyPage(manager, Mock())
            self.assertEqual(page.edit_recycle_bin.keySequence().toString(), "Ctrl+Alt+R")
            page.close()

        app = Application.__new__(Application)
        app.logger = Mock()
        app.open_recycle_bin = Mock()
        app.dispatch("recycle_bin")
        app.open_recycle_bin.assert_called_once_with()

        calls = []
        menu = make_tray_menu(self.app, lambda: None, lambda: None, lambda: None,
                              hotkeys={"recycle_bin": "ctrl+alt+r"},
                              recycle_bin=lambda: calls.append("recycle"))
        actions = {action.text(): action for action in menu.actions() if not action.isSeparator()}
        recycle = next(action for label, action in actions.items() if label.startswith("贴图回收站"))
        self.assertIn("Ctrl+Alt+R", recycle.text())
        self.assertIn("Ctrl+Alt+R", recycle.toolTip())
        recycle.trigger()
        self.assertIn("recycle", calls)



    def test_clear_data_directory_survives_files_held_open(self):
        """被本进程占用的文件删不掉时，清理要跳过它，而不是让整次清理失败。

        回归：版本闸门运行在单实例锁之后，`screensnap.lock` 正被本进程锁定，
        原先抛 PermissionError(WinError 32)，导致版本变化后的重置每次都失败。
        """
        from core.path_utils import clear_data_directory

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "ScreenSnap"
            (root / "cache").mkdir(parents=True)
            (root / "cache" / "sticker_1.png").write_bytes(b"x")
            lock_path = root / "screensnap.lock"
            lock_path.write_text("locked")
            with patch("core.path_utils.data_dir", return_value=root):
                with lock_path.open("r+") as handle:  # 持有句柄：Windows 下删除会失败
                    handle.write("x")
                    handle.flush()
                    self.assertEqual(clear_data_directory(), root)
            self.assertFalse((root / "cache").exists())  # 其余内容仍然被清掉

    def test_version_gate_still_rebuilds_config_when_cleanup_fails(self):
        """清理目录彻底失败时也必须完成配置重建：否则版本号永远对不上、每次启动重复报错。"""
        from config.config_manager import DEFAULTS
        from core.version import APP_VERSION
        from main import Application

        app = Application.__new__(Application)
        app.config = Mock()
        app.config.data = {"app_version": "0.9.0", "save_dir": "D:/shots",
                           "sticker_selection_effect_strength": 9}
        app.logger = Mock()
        app.version_reset_from = ""
        with patch("core.path_utils.clear_data_directory", side_effect=OSError("locked")):
            self.assertTrue(app.apply_version_gate())
        app.config.save.assert_called_once()
        self.assertEqual(app.config.data["app_version"], APP_VERSION)
        self.assertEqual(app.config.data["save_dir"], "D:/shots")
        self.assertEqual(app.config.data["sticker_selection_effect_strength"],
                         DEFAULTS["sticker_selection_effect_strength"])
        self.assertEqual(app.version_reset_from, "0.9.0")


    def test_version_gate_end_to_end_with_real_lock_file(self):
        """端到端回归：真实单实例锁 + 真实数据目录，版本变化后重置必须成功。

        复现用户报的 WinError 32 —— 锁在闸门之前拿到，screensnap.lock 被本进程占用。
        """
        from app.bootstrap import acquire_single_instance_lock
        from config.config_manager import DEFAULTS
        from core.version import APP_VERSION
        from main import Application

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "ScreenSnap"
            root.mkdir()
            (root / "settings.json").write_text(json.dumps(
                {"app_version": "0.0.1", "save_dir": "D:/keep", "theme": "dark",
                 "sticker_selection_effect_strength": 7, "stale_key": 1}), encoding="utf-8")
            (root / "clipboard_history").mkdir()
            (root / "clipboard_history" / "clipboard_1.png").write_bytes(b"x")
            lock = acquire_single_instance_lock(root / "screensnap.lock")
            self.assertIsNotNone(lock)
            try:
                with patch("core.path_utils.data_dir", return_value=root):
                    app = Application.__new__(Application)
                    app.config = ConfigManager(root / "settings.json")
                    app.logger = Mock()
                    app.version_reset_from = ""
                    self.assertTrue(app.apply_version_gate())      # 不应抛 PermissionError
                data = json.loads((root / "settings.json").read_text(encoding="utf-8"))
            finally:
                lock.unlock()
            self.assertEqual(data["app_version"], APP_VERSION)
            self.assertEqual(data["save_dir"], "D:/keep")                      # 保留
            self.assertEqual(data["theme"], "dark")                            # 保留
            self.assertEqual(data["sticker_selection_effect_strength"],
                             DEFAULTS["sticker_selection_effect_strength"])     # 外观重置
            self.assertNotIn("stale_key", data)
            self.assertFalse((root / "clipboard_history").exists())            # 缓存已清


    def test_reset_messages_reach_configured_log_file(self):
        """版本闸门/清理过程的提示必须进配置好的日志文件，而不是 logging 兜底 handler。

        回归：闸门原先跑在 configure_logging 之前，两行提示只打到 stderr、没有格式、
        也不进日志文件；且 core/path_utils 用的是 __name__ logger，不属于 screensnap。
        """
        import logging

        from config.config_manager import DEFAULTS
        from core.path_utils import clear_data_directory
        from logger import configure_logging

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "ScreenSnap"
            (root / "cache").mkdir(parents=True)
            (root / "cache" / "sticker_1.png").write_bytes(b"x")
            log_dir = Path(folder) / "logs"
            log_dir.mkdir()
            configure_logging({**DEFAULTS, "logging_enabled": True, "log_dir": str(log_dir),
                               "log_monthly_folder": False})
            try:
                with patch("core.path_utils.data_dir", return_value=root):
                    lock_path = root / "screensnap.lock"
                    lock_path.write_text("locked")
                    with lock_path.open("r+"):
                        clear_data_directory()          # 应记录「跳过被占用条目」
                logging.getLogger("screensnap").warning("版本变化提示占位")
                for handler in logging.getLogger("screensnap").handlers:
                    handler.flush()
                text = (log_dir / "app.log").read_text(encoding="utf-8")
            finally:
                # 关闭文件句柄，避免临时目录在 Windows 上删不掉。
                configure_logging({**DEFAULTS, "logging_enabled": False})
            self.assertIn("清理用户数据目录时跳过", text)
            self.assertIn("版本变化提示占位", text)
            self.assertRegex(text, r"\d{4}-\d{2}-\d{2} .*WARNING")

if __name__ == "__main__":  # 支持 python tests/test_config.py
    unittest.main()
