"""不依赖真实显示器和全局键盘钩子的操作测试。"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from PIL import Image
from PySide6.QtCore import QPointF, Qt, QPoint, QRect, QRectF
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from config.config_manager import ConfigManager
from core.path_utils import resolved_dir
from editor.annotation_canvas import AnnotationCanvas
from editor.annotation_items import shape
from editor.editor_window import EditorWindow
from screenshot.selection_rect import SelectionRects
from sticker.sticker_item import StickerItem
from sticker.sticker_manager import StickerManager
from ui.settings_window import SettingsWindow
from ui.widgets.hotkey_edit import HotkeyEdit


class CoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_package_public_api(self):
        from config import ConfigManager as PublicConfigManager
        from core import capture, data_dir
        from editor import EditorWindow as PublicEditorWindow
        from hotkey import HotkeyManager
        from logger import configure_logging
        from screenshot import MaskWindow
        from sticker import StickerManager as PublicStickerManager
        from ui import CaptureNotification, SettingsWindow as PublicSettingsWindow, make_tray_menu
        from sticker.sticker_manager import StickerManager
        from ui.capture_notification import CaptureNotification as Preview
        from screenshot.mask_window import MaskWindow as Mask

        self.assertIs(PublicConfigManager, ConfigManager)
        self.assertIs(PublicEditorWindow, EditorWindow)
        self.assertIs(PublicStickerManager, StickerManager)
        self.assertIs(PublicSettingsWindow, SettingsWindow)
        self.assertIs(CaptureNotification, Preview)
        self.assertIs(MaskWindow, Mask)
        for exported in (capture, data_dir, HotkeyManager, configure_logging, make_tray_menu):
            self.assertTrue(callable(exported))

    def test_config_roundtrip_and_conflict(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "settings.json"
            manager = ConfigManager(path)
            manager.data["hotkeys"]["capture"] = "ctrl+q"
            manager.save()
            self.assertEqual(ConfigManager(path).data["hotkeys"]["capture"], "ctrl+q")
            path.write_text(json.dumps({"hotkeys": {"paste": "ctrl+q", "capture": "ctrl+q"}}))
            with self.assertRaises(ValueError):
                ConfigManager(path)
            path.write_text(json.dumps({"hotkeys": {"paste": "shift+ctrl+q",
                                                     "capture": "ctrl+shift+q"}}))
            with self.assertRaises(ValueError):
                ConfigManager(path)

    def test_log_directory_setting_persists(self):
        from ui.widgets.file_path_edit import FilePathEdit

        with tempfile.TemporaryDirectory() as folder:
            manager = ConfigManager(Path(folder) / "settings.json")
            settings = SettingsWindow(manager)
            directory = settings.pages.widget(4).findChild(FilePathEdit)
            directory.input.setText(str(Path(folder) / "logs"))
            directory.input.editingFinished.emit()
            self.assertEqual(ConfigManager(manager.path).data["log_dir"],
                             directory.input.text())
            settings.close()

    def test_logging_uses_existing_directory_or_startup_fallback(self):
        from config.config_manager import DEFAULTS
        from logger.log_setup import configure_logging

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            missing = root / "missing" / "custom"
            existing = root / "existing"
            existing.mkdir()
            settings = dict(DEFAULTS, log_dir=str(missing))
            with patch("logger.log_setup.sys.argv", [str(root / "main.py")]):
                try:
                    logger = configure_logging(settings)
                    self.assertEqual(Path(logger.handlers[0].baseFilename),
                                     root / "logs" / "app.log")
                    self.assertFalse(missing.exists())
                    settings["log_dir"] = str(existing)
                    logger = configure_logging(settings)
                    self.assertEqual(Path(logger.handlers[0].baseFilename),
                                     existing / "app.log")
                    settings["log_dir"] = ""
                    logger = configure_logging(settings)
                    self.assertEqual(Path(logger.handlers[0].baseFilename),
                                     root / "logs" / "app.log")
                finally:
                    configure_logging(dict(settings, logging_enabled=False))

    def test_settings_controls_have_initialized_defaults(self):
        from config.config_manager import DEFAULTS, validate

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "settings.json"
            path.write_text("{}", encoding="utf-8")
            manager = ConfigManager(path)
            self.assertEqual(manager.data, validate({}))
            self.assertEqual(set(manager.data), set(DEFAULTS))
            self.assertFalse(manager.data["start_on_boot"])
            self.assertEqual(manager.data["filename"], "_%Y%m%d_%H%M%S")
            settings = SettingsWindow(manager)
            for index in range(settings.pages.count()):
                for key in settings.pages.widget(index).controls:
                    self.assertIn(key, manager.data)
            self.assertFalse(settings.pages.widget(0).controls["start_on_boot"].isChecked())
            settings.close()

    def test_text_item_uses_shared_annotation_color(self):
        from config.config_manager import DEFAULTS
        from editor.annotation_items import text_item

        settings = dict(DEFAULTS, pen_color="#123456", text_color="#abcdef")
        item = text_item(QPointF(0, 0), "hello", settings, Qt.AlignLeft)
        self.assertEqual(item.defaultTextColor().name(), "#123456")

    def test_settings_pages_group_related_controls_and_scroll(self):
        from PySide6.QtWidgets import QGroupBox, QScrollArea

        with tempfile.TemporaryDirectory() as folder:
            settings = SettingsWindow(ConfigManager(Path(folder) / "settings.json"))
            settings.show()
            self.app.processEvents()
            expected = (
                (0, "定位辅助", ("crosshair", "crosshair_color", "crosshair_width")),
                (0, "遮罩与选区", ("mask_theme", "selection_border_color", "anchor_style")),
                (3, "箭头", ("arrow_style", "arrow_width")),
                (3, "公共标注样式", ("pen_color",)),
                (3, "文字", ("font", "font_size", "line_spacing")),
                (3, "马赛克", ("mosaic_mode", "mosaic_size")),
                (3, "编辑区边框", ("editor_border_width", "editor_border_color")),
            )
            for index, title, keys in expected:
                page = settings.pages.widget(index)
                box = next(box for box in page.findChildren(QGroupBox) if box.title() == title)
                for key in keys:
                    control = page.controls.get(key) or page.color_buttons[key]
                    self.assertTrue(box.isAncestorOf(control), (title, key))
            for index in range(settings.pages.count()):
                page = settings.pages.widget(index)
                self.assertTrue(page.findChildren(QGroupBox), index)
                self.assertIsNotNone(page.findChild(QScrollArea))
            editor_scroll = settings.pages.widget(3).findChild(QScrollArea)
            self.assertGreater(editor_scroll.widget().sizeHint().height(),
                               editor_scroll.viewport().height())
            settings.close()

    def test_filename_template_has_visible_format_guide(self):
        from PySide6.QtWidgets import QLabel

        with tempfile.TemporaryDirectory() as folder:
            settings = SettingsWindow(ConfigManager(Path(folder) / "settings.json"))
            page = settings.pages.widget(2)
            field_row, _ = page.form.getWidgetPosition(page.controls["filename"])
            guide = next(label for label in page.findChildren(QLabel)
                         if "%Y 四位年" in label.text())
            guide_row, _ = page.form.getWidgetPosition(guide)
            self.assertEqual(guide_row, field_row + 1)
            for token in ("%Y", "%m", "%d", "%H", "%M", "%S", "_"):
                self.assertIn(token, guide.text())
            settings.close()

    def test_start_on_boot_registry_command_and_removal(self):
        import sys
        from types import SimpleNamespace
        from core.startup import ENTRY_NAME, RUN_KEY, set_start_on_boot

        if sys.platform != "win32":
            self.skipTest("Windows current-user Run key")
        with patch("core.startup.sys", SimpleNamespace(platform="win32", frozen=True,
                                                       executable=r"C:\Program Files\ScreenSnap.exe")), \
             patch("winreg.CreateKeyEx") as create, patch("winreg.SetValueEx") as set_value, \
             patch("winreg.OpenKey") as open_key, patch("winreg.DeleteValue") as delete:
            set_start_on_boot(True)
            self.assertEqual(create.call_args.args[:2], (sys.modules["winreg"].HKEY_CURRENT_USER,
                                                         RUN_KEY))
            self.assertEqual(set_value.call_args.args[1], ENTRY_NAME)
            self.assertEqual(set_value.call_args.args[-1], '"C:\\Program Files\\ScreenSnap.exe"')
            with patch("core.startup.sys", SimpleNamespace(platform="win32", frozen=False,
                                                           executable=r"C:\Python\python.exe")):
                set_start_on_boot(True)
            source_command = set_value.call_args.args[-1]
            self.assertIn("python", source_command.lower())
            self.assertIn("main.py", source_command)
            self.assertNotIn("ScreenSnap.exe", source_command)
            set_start_on_boot(False)
            open_key.assert_called_once()
            self.assertEqual(delete.call_args.args[1], ENTRY_NAME)

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

    def test_legacy_product_directories_migrate_to_screensnap(self):
        import os
        from core.path_utils import captures_dir, data_dir

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            appdata = root / "appdata"
            legacy_data = appdata / "SnipasteClone"
            legacy_data.mkdir(parents=True)
            (legacy_data / "settings.json").write_text("{}", encoding="utf-8")
            with patch.dict(os.environ, {"APPDATA": str(appdata)}):
                self.assertEqual(data_dir(), appdata / "ScreenSnap")
            self.assertTrue((appdata / "ScreenSnap" / "settings.json").is_file())
            self.assertFalse(legacy_data.exists())

            pictures = root / "Pictures"
            legacy_captures = pictures / "SnipasteClone"
            legacy_captures.mkdir(parents=True)
            (legacy_captures / "capture.png").write_bytes(b"image")
            with patch.object(Path, "home", return_value=root):
                self.assertEqual(captures_dir(), pictures / "ScreenSnap")
            self.assertTrue((pictures / "ScreenSnap" / "capture.png").is_file())

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
        with tempfile.TemporaryDirectory() as folder:
            manager = ConfigManager(Path(folder) / "settings.json")
            self.assertNotEqual(resolved_dir(manager.data, "auto_dir"),
                                resolved_dir(manager.data, "manual_dir"))
            imported = Path(folder) / "import.json"
            imported.write_text(json.dumps({"pen_width": 7}), encoding="utf-8")
            window = SettingsWindow(manager)
            with patch("ui.settings_window.QFileDialog.getOpenFileName", return_value=(str(imported), "")):
                window.import_settings()
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
                editor.toolbar.pen_color.click()
            self.assertEqual(editor.canvas.settings["pen_color"], "#12ab34")
            self.assertEqual(ConfigManager(manager.path).data["pen_color"], "#12ab34")
            self.assertEqual(settings.pages.widget(3).color_buttons["pen_color"].color, "#12ab34")
            settings.pages.widget(3).update_value("pen_color", "#ff0000")
            self.assertEqual(editor.canvas.settings["pen_color"], "#ff0000")
            self.assertEqual(ConfigManager(manager.path).data["pen_color"], "#ff0000")
            editor.close()

    def test_picked_color_updates_active_annotation_color(self):
        from PySide6.QtGui import QGuiApplication
        from config.config_manager import DEFAULTS

        editor = EditorWindow(Image.new("RGB", (40, 30), "white"), dict(DEFAULTS))
        editor.apply_picked_color("#123456")
        self.assertEqual(editor.settings["pen_color"], "#123456")
        self.assertEqual(editor.canvas.settings["pen_color"], "#123456")
        self.assertEqual(editor.toolbar.pen_color.color, "#123456")
        self.assertEqual(QGuiApplication.clipboard().text(), "#123456")
        editor.close()

    def test_editor_operation_tips_are_visible(self):
        from config.config_manager import DEFAULTS

        editor = EditorWindow(Image.new("RGB", (90, 70), "white"), dict(DEFAULTS))
        editor.show()
        self.app.processEvents()
        self.assertTrue(editor.operation_tips.isVisible())
        self.assertIn("双击空白处完成保存", editor.operation_tips.text())
        self.assertIn("Esc 放弃编辑", editor.operation_tips.text())
        editor.close()

    def test_capture_operation_tips_have_contrast_backdrop(self):
        from PySide6.QtGui import QColor, QImage, QPainter
        from screenshot.overlay_info import paint_info

        image = QImage(800, 100, QImage.Format_ARGB32)
        image.fill(QColor("white"))
        painter = QPainter(image)
        paint_info(painter, QPoint(20, 20), None, QRect(0, 0, 800, 100))
        painter.end()
        backdrop = image.pixelColor(400, 10)
        self.assertNotEqual(backdrop.name(), "#ffffff")
        self.assertLess(backdrop.red(), 100)
        self.assertEqual(backdrop.alpha(), 255)
        self.assertEqual(image.pixelColor(400, 50).name(), "#ffffff")

        painter = Mock()
        painter.device.return_value.width.return_value = 800
        painter.device.return_value.height.return_value = 100
        painter.fontMetrics.return_value.horizontalAdvance.side_effect = lambda text: len(text) * 8
        painter.fontMetrics.return_value.height.return_value = 16
        painter.fontMetrics.return_value.elidedText.side_effect = lambda text, mode, width: text
        paint_info(painter, QPoint(20, 20), None)
        self.assertIn("拖拽框选", painter.drawText.call_args_list[-1].args[-1])
        self.assertEqual(painter.drawText.call_count, 1)
        self.assertLessEqual(painter.drawRoundedRect.call_args.args[0].height(), 32)
        paint_info(painter, QPoint(20, 20), QRect(10, 10, 40, 30))
        selected_hint = painter.drawText.call_args_list[-1].args[-1]
        self.assertIn("内部拖动移动", selected_hint)
        self.assertIn("边中点单向缩放", selected_hint)

    def test_capture_operation_tips_are_centered_on_each_monitor(self):
        from PySide6.QtGui import QColor, QImage, QPainter
        from screenshot.overlay_info import paint_info

        image = QImage(2000, 900, QImage.Format_ARGB32)
        image.fill(QColor("white"))
        painter = QPainter(image)
        paint_info(painter, QPoint(0, 0), None, QRect(0, 0, 1000, 900))
        paint_info(painter, QPoint(0, 0), None, QRect(1000, 0, 1000, 900))
        painter.end()
        self.assertGreater(image.pixelColor(49, 20).red(), 200)
        self.assertLess(image.pixelColor(500, 9).red(), 100)
        self.assertLess(image.pixelColor(1500, 9).red(), 100)

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
            restored = ConfigManager(manager.path).data
            self.assertEqual(restored["annotation_tool"], "marker")
            self.assertEqual(restored["text_alignment"], "right")
            self.assertEqual(restored["marker_width"], 9)
            self.assertEqual(restored["pen_width"], 3)
            self.assertEqual(restored["marker_opacity"], 55)
            self.assertEqual(window.pages.widget(3).controls["marker_opacity"].value(), 55)
            self.assertEqual(restored["mosaic_mode"], "blur")
            self.assertEqual(restored["mosaic_size"], 23)
            self.assertTrue(restored["cursor"])
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
        from ui.settings_general import GeneralPage
        with tempfile.TemporaryDirectory() as folder:
            manager = ConfigManager(Path(folder) / "settings.json")
            page = GeneralPage(manager, manager.save)
            for label, key in (("截图完成通知", "capture_notification"),
                               ("保存成功通知", "save_notification"),
                               ("贴图通知", "sticker_notification")):
                control = page.controls[key]
                control.setChecked(False)
                self.assertFalse(ConfigManager(manager.path).data[key])
            self.assertTrue(ConfigManager(manager.path).data["bubble"])

    def test_notification_switches_gate_only_matching_messages(self):
        from types import SimpleNamespace
        from config.config_manager import DEFAULTS
        from main import Application
        app = Application.__new__(Application)
        app.config = SimpleNamespace(data=DEFAULTS.copy())
        app.tray = Mock()
        app.logger = Mock()
        app.stickers = Mock()
        app.settings_window = Mock()
        app.editors = []
        app.capture_notice = None
        with patch("main.QApplication.beep"), patch("main.CaptureNotification") as preview:
            app.config.data["capture_notification"] = False
            with patch("main.EditorWindow"):
                app.edit_images([(Image.new("RGB", (20, 20)), None)])
            app.tray.showMessage.assert_not_called()
            preview.assert_not_called()
            app.config.data["capture_notification"] = True
            with patch("main.EditorWindow"):
                app.edit_images([(Image.new("RGB", (20, 20), "red"), None)])
            self.assertEqual(preview.call_args.args[0].getpixel((0, 0)), (255, 0, 0))
            self.assertEqual(preview.call_args.args[1], 1)
            preview.return_value.show_preview.assert_called_once()
            app.tray.reset_mock()

            app.config.data["save_notification"] = False
            app.saved("capture.png")
            app.tray.showMessage.assert_not_called()
            app.config.data["save_notification"] = True
            app.saved("capture.png")
            self.assertIn("图片已保存", app.tray.showMessage.call_args.args[1])
            app.tray.reset_mock()

            app.config.data["sticker_notification"] = False
            app.add_sticker(object())
            app.stickers.add.assert_called_once()
            app.tray.showMessage.assert_not_called()
            app.stickers.paste_latest.return_value = True
            app.dispatch("paste")
            app.tray.showMessage.assert_not_called()
            app.config.data["sticker_notification"] = True
            app.add_sticker(object())
            self.assertIn("已创建贴图", app.tray.showMessage.call_args.args[1])
            app.tray.reset_mock()
            app.dispatch("paste")
            self.assertIn("已贴上次截图", app.tray.showMessage.call_args.args[1])
            app.tray.reset_mock()
            app.config.data["bubble"] = False
            app.saved("capture.png")
            app.tray.showMessage.assert_not_called()
            preview.reset_mock()
            with patch("main.EditorWindow"):
                app.edit_images([(Image.new("RGB", (20, 20)), None)])
            preview.assert_not_called()

    def test_capture_notification_contains_image(self):
        from PySide6.QtWidgets import QLabel
        from PySide6.QtGui import QImage, QColor
        from ui.capture_notification import CaptureNotification
        notification = CaptureNotification(Image.new("RGB", (40, 20), "#23bc58"), 2)
        self.assertIn("2 张", notification.findChildren(QLabel)[0].text())
        preview = notification.preview.pixmap().toImage()
        self.assertEqual(preview.pixelColor(preview.width() // 2, preview.height() // 2).name(), "#23bc58")
        notification.close()
        saved = QImage(30, 20, QImage.Format_RGB32)
        saved.fill(QColor("#23bc58"))
        notification = CaptureNotification(saved, title="图片已保存", detail="capture.png")
        self.assertEqual(notification.preview.pixmap().toImage().pixelColor(15, 10).name(), "#23bc58")
        notification.close()

    def test_startup_notice_and_tray_icon(self):
        from types import SimpleNamespace
        from config.config_manager import DEFAULTS
        from main import Application, tray_icon

        icon = tray_icon()
        self.assertFalse(icon.isNull())
        picture = icon.pixmap(32, 32).toImage()
        self.assertGreater(picture.pixelColor(picture.width() // 2, picture.height() // 2).alpha(), 0)
        self.assertGreater(picture.pixelColor(picture.width() * 3 // 4, picture.height() * 3 // 4).alpha(), 0)

        app = Application.__new__(Application)
        app.config = SimpleNamespace(data=DEFAULTS.copy())
        app.tray = Mock()
        with patch("main.QApplication.beep"):
            app.announce_startup()
            self.assertIn("已启动", app.tray.showMessage.call_args.args[1])
            app.tray.reset_mock()
            app.config.data["bubble"] = False
            app.announce_startup()
            app.tray.showMessage.assert_not_called()

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
            self.assertEqual(manager.data["hotkeys"]["open_image"], "ctrl+alt+o")
            self.assertEqual(page.edit_edit_clipboard.keySequence().toString().lower(), "ctrl+alt+v")
            self.assertEqual(page.edit_open_image.keySequence().toString().lower(), "ctrl+alt+o")
            page.update_binding("edit_clipboard", "ctrl+shift+v")
            self.assertEqual(ConfigManager(manager.path).data["hotkeys"]["edit_clipboard"],
                             "ctrl+shift+v")
            page.close()

    def test_tray_menu_dispatches_image_edit_actions(self):
        from ui.tray_menu import make_tray_menu

        calls = []
        menu = make_tray_menu(self.app, lambda: calls.append("capture"), lambda: None,
                              lambda: None, lambda: calls.append("clipboard"),
                              lambda: calls.append("open"),
                              {"capture": "ctrl+shift+f1", "edit_clipboard": "ctrl+alt+v",
                               "open_image": "ctrl+alt+o"})
        actions = {action.text(): action for action in menu.actions() if not action.isSeparator()}
        clipboard = next(action for label, action in actions.items() if label.startswith("编辑剪贴板图片"))
        open_image = next(action for label, action in actions.items() if label.startswith("打开并编辑图片"))
        self.assertIn("Ctrl+Alt+V", clipboard.text())
        self.assertIn("Ctrl+Alt+O", open_image.text())
        self.assertIn("Ctrl+Shift+F1", next(iter(actions)))
        clipboard.trigger()
        open_image.trigger()
        self.assertEqual(calls, ["clipboard", "open"])

    def test_sticker_context_menu_shows_restore_hotkey(self):
        from PySide6.QtGui import QImage
        from sticker.sticker_menu import build_menu

        image = QImage(24, 18, QImage.Format_RGB32)
        sticker = StickerItem(image, settings={"hotkeys": {"touch": "ctrl+shift+t"}})
        action = next(action for action in build_menu(sticker).actions()
                      if "点击穿透" in action.text())
        self.assertIn("Ctrl+Shift+T", action.text())
        sticker.close()

    def test_annotation_font_size_and_arrow_style_persist(self):
        from config.config_manager import DEFAULTS

        with tempfile.TemporaryDirectory() as folder:
            manager = ConfigManager(Path(folder) / "settings.json")
            settings = SettingsWindow(manager)
            editor = EditorWindow(Image.new("RGB", (40, 30), "white"), manager.data)
            editor.setting_changed.connect(settings.set_annotation_setting)
            editor.toolbar.font_size.setValue(31)
            editor.toolbar.tool_buttons["arrow"].click()
            editor.toolbar.choice_buttons["arrow_style"]["open"].click()
            editor.toolbar.choice_buttons["arrow_style"]["double_filled"].click()
            self.assertEqual(ConfigManager(manager.path).data["arrow_style"], "double_filled")
            editor.toolbar.choice_buttons["arrow_style"]["double"].click()
            self.assertEqual(ConfigManager(manager.path).data["arrow_style"], "double")
            editor.toolbar.tool_buttons["text"].click()
            editor.toolbar.choice_buttons["text_alignment"]["center"].click()
            editor.toolbar.tool_buttons["mosaic"].click()
            self.assertEqual(ConfigManager(manager.path).data["text_alignment"], "center")
            editor.close()
            settings.close()

    def test_clipboard_and_file_images_open_in_editor(self):
        from types import SimpleNamespace
        from config.config_manager import DEFAULTS
        from main import Application
        from PySide6.QtGui import QColor, QImage

        app = Application.__new__(Application)
        app.config = SimpleNamespace(data=dict(DEFAULTS))
        app.notify = Mock()
        app.edit_images = Mock()
        clipboard_image = QImage(12, 8, QImage.Format_RGB32)
        clipboard_image.fill(QColor("#16a0d0"))
        self.app.clipboard().setImage(clipboard_image)
        app.edit_clipboard_image()
        clipboard_pixels, clipboard_capture = app.edit_images.call_args.args[0][0]
        self.assertEqual(clipboard_pixels.size, (12, 8))
        self.assertEqual(clipboard_pixels.getpixel((0, 0)), (22, 160, 208))
        self.assertIsNone(clipboard_capture)
        self.assertFalse(app.edit_images.call_args.kwargs["from_capture"])

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "source.png"
            Image.new("RGB", (10, 6), "red").save(path)
            with patch("main.QFileDialog.getOpenFileName", return_value=(str(path), "图片文件")):
                app.open_and_edit_image()
        file_pixels, file_capture = app.edit_images.call_args.args[0][0]
        self.assertEqual(file_pixels.size, (10, 6))
        self.assertEqual(file_pixels.getpixel((0, 0)), (255, 0, 0, 255))
        self.assertIsNone(file_capture)
        self.assertFalse(app.edit_images.call_args.kwargs["from_capture"])

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

    def test_external_image_edit_does_not_show_capture_notice(self):
        from types import SimpleNamespace
        from config.config_manager import DEFAULTS
        from main import Application

        app = Application.__new__(Application)
        app.config = SimpleNamespace(data=dict(DEFAULTS, sound=True))
        app.editors = []
        app.capture_notice = None
        app.logger = Mock()
        app.settings_window = Mock()
        with patch("main.EditorWindow") as editor_factory, \
                patch("main.CaptureNotification") as notice, \
                patch("main.QApplication.beep") as beep:
            app.edit_images([(Image.new("RGB", (20, 12), "blue"), None)], from_capture=False)
        editor_factory.assert_called_once()
        notice.assert_not_called()
        beep.assert_not_called()

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

    def test_arrow_has_filled_head_and_round_stroke(self):
        from PySide6.QtCore import QPointF
        arrow = shape("arrow", QPointF(10, 10), QPointF(90, 10), "#ff5252", 5)
        self.assertEqual(arrow.brush().color().name(), "#ff5252")
        self.assertEqual(arrow.pen().capStyle(), Qt.RoundCap)
        self.assertTrue(arrow.path().contains(QPointF(80, 10)))

    def test_arrow_styles_include_filled_open_and_double(self):
        from PySide6.QtCore import QPointF
        from config.config_manager import validate

        start, end = QPointF(10, 10), QPointF(90, 10)
        filled = shape("arrow", start, end, "#ff0000", 4, "filled")
        open_arrow = shape("arrow", start, end, "#ff0000", 4, "open")
        double = shape("arrow", start, end, "#ff0000", 4, "double")
        double_filled = shape("arrow", start, end, "#ff0000", 4, "double_filled")
        self.assertEqual(filled.brush().color().name(), "#ff0000")
        self.assertEqual(open_arrow.brush().style(), Qt.NoBrush)
        self.assertEqual(double.brush().style(), Qt.NoBrush)
        self.assertEqual(double_filled.brush().color().name(), "#ff0000")
        self.assertEqual(double.path(), double_filled.path())
        shaft_end = open_arrow.path().elementAt(1)
        self.assertLess(shaft_end.x, end.x())
        self.assertGreater(double.path().elementCount(), open_arrow.path().elementCount())
        for arrow in (filled, open_arrow):
            head = arrow.path().elementAt(2)
            self.assertGreaterEqual(end.x() - head.x, 28)
            self.assertGreaterEqual(head.y - end.y(), 10)
        double_head = double.path().elementAt(1)
        self.assertGreaterEqual(double_head.x - start.x(), 24)
        self.assertGreaterEqual(double_head.y - start.y(), 10)
        angled = shape("arrow", QPointF(10, 10), QPointF(70, 50), "#ff0000", 4, "filled")
        self.assertGreater(angled.path().boundingRect().height(), 20)
        first = angled.path().elementAt(0)
        self.assertEqual(angled.path().currentPosition(), QPointF(first.x, first.y))
        self.assertEqual(validate({"arrow_style": "open"})["arrow_style"], "open")
        self.assertEqual(validate({"arrow_style": "double_filled"})["arrow_style"], "double_filled")
        self.assertEqual(validate({"arrow_style": "double"})["arrow_style"], "double")
        with self.assertRaises(ValueError):
            validate({"arrow_style": "dotted"})

    def test_arrow_outline_keeps_clear_shaft_and_sloped_shoulders(self):
        from math import atan2, degrees
        from PySide6.QtCore import QPointF
        from PySide6.QtGui import QPainterPathStroker

        start, end = QPointF(10, 10), QPointF(170, 10)
        for style in ("filled", "open", "double", "double_filled"):
            for width in (1, 4, 12, 24):
                arrow = shape("arrow", start, end, "#ff0000", width, style)
                path = arrow.path()
                stroker = QPainterPathStroker()
                stroker.setWidth(width)
                if style in ("open", "double"):
                    self.assertFalse(stroker.createStroke(path).contains(QPointF(90, 10)),
                                     (style, width))
                double_headed = style in ("double", "double_filled")
                inner = path.elementAt(2 if double_headed else 1)
                outer = path.elementAt(1 if double_headed else 2)
                tip = path.elementAt(0 if double_headed else 3)
                tip_angle = 2 * degrees(atan2(abs(outer.y - tip.y),
                                              abs(outer.x - tip.x)))
                self.assertGreater(tip_angle, 65, (style, width))
                self.assertLess(tip_angle, 75, (style, width))
                if double_headed:
                    self.assertLess(inner.x, outer.x)
                else:
                    self.assertGreater(inner.x, outer.x)
                self.assertLess(inner.y, outer.y)
                angle = degrees(atan2(outer.y - inner.y, abs(inner.x - outer.x)))
                self.assertLess(angle, 90, (style, width))
                head_length = (outer.x - start.x()) if double_headed else (end.x() - outer.x)
                if double_headed:
                    self.assertAlmostEqual(outer.x - inner.x, head_length * 0.25, delta=0.01)
                else:
                    self.assertLessEqual(inner.x - outer.x, head_length * 0.22 + 0.01)
                    if width == 4:
                        self.assertGreater(inner.x - outer.x, head_length * 0.18)
                if double_headed:
                    other_outer, other_inner = path.elementAt(4), path.elementAt(3)
                    other_tip = path.elementAt(5)
                    other_tip_angle = 2 * degrees(atan2(abs(other_outer.y - other_tip.y),
                                                        abs(other_tip.x - other_outer.x)))
                    self.assertGreater(other_tip_angle, 65, (style, width))
                    self.assertLess(other_tip_angle, 75, (style, width))
                    self.assertGreater(other_inner.x, other_outer.x)
                    other_angle = degrees(atan2(other_outer.y - other_inner.y,
                                                other_inner.x - other_outer.x))
                    self.assertLess(other_angle, 90, width)
                    self.assertAlmostEqual(other_inner.x - other_outer.x,
                                           head_length * 0.25, delta=0.01)
                self.assertGreater(outer.y - start.y(), 13 if width == 4 else 0)

    def test_short_wide_double_arrows_keep_both_tips_sharp(self):
        from math import atan2, degrees
        from PySide6.QtCore import QPointF
        from PySide6.QtGui import QPainterPathStroker

        for style in ("double", "double_filled"):
            arrow = shape("arrow", QPointF(10, 10), QPointF(90, 10), "#ff0000", 24, style)
            path = arrow.path()
            self.assertLess(path.elementAt(2).x, path.elementAt(1).x)
            self.assertGreater(path.elementAt(3).x, path.elementAt(4).x)
            for tip, shoulder in ((0, 1), (5, 4)):
                point, base = path.elementAt(tip), path.elementAt(shoulder)
                angle = 2 * degrees(atan2(abs(base.y - point.y), abs(base.x - point.x)))
                self.assertGreater(angle, 65, style)
                self.assertLess(angle, 75, style)
            if style == "double":
                stroker = QPainterPathStroker()
                stroker.setWidth(24)
                self.assertFalse(stroker.createStroke(path).contains(QPointF(50, 10)))

    def test_exported_diagonal_arrows_have_antialiased_edges(self):
        from config.config_manager import DEFAULTS

        for style in ("open", "double", "double_filled"):
            canvas = AnnotationCanvas(Image.new("RGB", (120, 100), "white"), dict(DEFAULTS))
            canvas.scene_data.addItem(shape("arrow", QPointF(12, 18), QPointF(104, 69),
                                            "#ff0000", 3, style))
            image = canvas.render_image()
            self.assertTrue(any(0 < image.pixelColor(x, y).green() < 255
                                for y in range(10, 80) for x in range(8, 110)), style)
            canvas.close()

    def test_other_vector_annotations_have_antialiased_edges(self):
        from config.config_manager import DEFAULTS

        for tool in ("rect", "ellipse", "pen"):
            canvas = AnnotationCanvas(Image.new("RGB", (120, 100), "white"), dict(DEFAULTS))
            canvas.scene_data.addItem(shape(tool, QPointF(12, 18), QPointF(104, 69),
                                            "#ff0000", 3))
            image = canvas.render_image()
            self.assertTrue(any(0 < image.pixelColor(x, y).green() < 255
                                for y in range(10, 80) for x in range(8, 110)), tool)
            canvas.close()

    def test_text_antialiasing_enabled_for_preview_and_export(self):
        from config.config_manager import DEFAULTS
        from PySide6.QtGui import QPainter

        canvas = AnnotationCanvas(Image.new("RGB", (120, 80), "white"), dict(DEFAULTS))
        self.assertTrue(canvas.renderHints() & QPainter.TextAntialiasing)
        render = canvas.scene_data.render
        with patch.object(canvas.scene_data, "render",
                          side_effect=lambda painter, *args: (
                              self.assertTrue(painter.testRenderHint(QPainter.TextAntialiasing)),
                              render(painter, *args))):
            canvas.render_image()
        canvas.close()

    def test_selection_multiple(self):
        selection = SelectionRects()
        from PySide6.QtCore import QPoint
        for start, end in ((QPoint(2, 2), QPoint(35, 35)), (QPoint(50, 50), QPoint(90, 90))):
            selection.begin(start)
            selection.update(end)
            selection.finish()
        self.assertEqual(len(selection.rects), 2)
        selection.fixed(QPoint(5, 5), 40, 30)
        self.assertEqual((selection.rects[-1].width(), selection.rects[-1].height()), (40, 30))
        selection.begin(QPoint(20, 20))
        selection.update(QPoint(30, 30))
        selection.finish()
        self.assertEqual((selection.rects[-1].x(), selection.rects[-1].y()), (15, 15))
        corner = selection.rects[-1].bottomRight()
        selection.begin(corner)
        selection.update(QPoint(corner.x() + 20, corner.y() + 10))
        selection.finish()
        self.assertEqual((selection.rects[-1].width(), selection.rects[-1].height()), (60, 40))

    def test_selection_resizes_from_all_four_corners(self):
        selection = SelectionRects()
        for corner, opposite in ((QPoint(10, 10), QPoint(49, 39)),
                                 (QPoint(49, 10), QPoint(10, 39)),
                                 (QPoint(10, 39), QPoint(49, 10)),
                                 (QPoint(49, 39), QPoint(10, 10))):
            selection.rects = [QRect(10, 10, 40, 30)]
            selection.begin(corner)
            self.assertEqual(selection.resize_anchor, opposite)
            selection.update(corner + QPoint(2, 2))
            selection.finish()
            self.assertEqual(len(selection.rects), 1)
            self.assertIsNone(selection.resizing)

    def test_selection_resizes_from_all_four_edge_midpoints(self):
        cases = (
            (QPoint(30, 10), QPoint(30, 6), "y", "vertical", "top"),
            (QPoint(50, 25), QPoint(56, 25), "x", "horizontal", "right"),
            (QPoint(30, 40), QPoint(30, 46), "y", "vertical", "bottom"),
            (QPoint(10, 25), QPoint(4, 25), "x", "horizontal", "left"),
        )
        original = QRect(10, 10, 41, 31)
        for handle, target, axis, cursor, side in cases:
            selection = SelectionRects()
            selection.rects = [QRect(original)]
            hit = selection.handle_at(handle)
            self.assertIsNotNone(hit, side)
            self.assertEqual(hit[2:], (cursor, handle, axis), side)
            selection.begin(handle)
            selection.update(target)
            resized = selection.rects[0]
            self.assertEqual(resized.width() == original.width(), axis == "y", side)
            self.assertEqual(resized.height() == original.height(), axis == "x", side)
            if side == "top":
                self.assertEqual(resized.bottom(), original.bottom())
            elif side == "right":
                self.assertEqual(resized.left(), original.left())
            elif side == "bottom":
                self.assertEqual(resized.top(), original.top())
            else:
                self.assertEqual(resized.right(), original.right())
            selection.finish()
            before_nudge = QRect(selection.rects[0])
            selection.move_last(1, 1)
            self.assertEqual(abs(selection.rects[0].width() - before_nudge.width()),
                             1 if axis == "x" else 0)
            self.assertEqual(abs(selection.rects[0].height() - before_nudge.height()),
                             1 if axis == "y" else 0)

    def test_click_near_resize_corner_keeps_size_before_keyboard_nudge(self):
        selection = SelectionRects()
        selection.fixed(QPoint(10, 10), 40, 30)
        click = selection.rects[0].bottomRight() - QPoint(5, 5)
        selection.begin(click)
        selection.finish()
        selection.move_last(1, 0)
        self.assertEqual(selection.rects[0], QRect(10, 10, 41, 30))

    def test_keyboard_takes_over_corner_drag_without_mouse_snapback(self):
        selection = SelectionRects()
        selection.fixed(QPoint(10, 10), 40, 30)
        original_corner = selection.rects[0].bottomRight()
        selection.begin(original_corner)
        selection.update(original_corner + QPoint(12, 8))
        selection.move_last(1, 0)
        expected = QRect(QPoint(10, 10), original_corner + QPoint(13, 8)).normalized()
        self.assertEqual(selection.rects[0], expected)
        self.assertIsNone(selection.resizing)
        selection.update(original_corner + QPoint(20, 20))
        selection.finish()
        self.assertEqual(selection.rects[0], expected)

    def test_selection_border_color_is_separate_and_configurable(self):
        from config.config_manager import DEFAULTS, validate
        from screenshot.mask_window import MaskWindow
        from ui.settings_general import GeneralPage

        with tempfile.TemporaryDirectory() as folder:
            config = ConfigManager(Path(folder) / "settings.json")
            page = GeneralPage(config, lambda: None)
            color_button = page.controls["selection_border_color"]
            self.assertEqual(color_button.color, "#ff0000")
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
            self.assertEqual(image.pixelColor(50, 20).name(), "#ff3030")
            self.assertNotEqual(image.pixelColor(50, 21).name(), "#ff3030")
            self.assertEqual(image.pixelColor(50, 50).name(), "#ff3030")
            mask.close()

    def test_crosshair_color_and_width_are_configurable_and_rendered(self):
        from config.config_manager import DEFAULTS, validate
        from screenshot.mask_window import MaskWindow
        from ui.settings_general import GeneralPage

        with tempfile.TemporaryDirectory() as folder:
            config = ConfigManager(Path(folder) / "settings.json")
            page = GeneralPage(config, config.save)
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
            self.assertEqual(screenshot.pixelColor(50, 55).name(), "#ff2020")
            self.assertEqual(screenshot.pixelColor(49, 55).name(), "#ff2020")
            mask.close()

    def test_keyboard_nudges_selected_region_after_fixed_or_corner(self):
        selection = SelectionRects()
        selection.fixed(QPoint(10, 10), 40, 30)
        selection.fixed(QPoint(90, 90), 40, 30)
        selection.begin(QPoint(30, 25))
        selection.finish()
        selection.move_last(1, -1)
        self.assertEqual(selection.rects[0].topLeft(), QPoint(11, 9))
        self.assertEqual(selection.rects[1].topLeft(), QPoint(90, 90))
        selection.begin(selection.rects[0].bottomRight())
        selection.update(QPoint(53, 40))
        selection.finish()
        selection.fixed(QPoint(150, 90), 20, 20)
        selection.move_last(-1, 1)
        self.assertEqual(selection.rects[-1].topLeft(), QPoint(149, 91))
        self.assertIsNone(selection.nudge_corner)

    def test_mask_drag_resizes_each_corner(self):
        from PySide6.QtCore import QPoint, QPointF, QEvent
        from PySide6.QtGui import QMouseEvent
        from config.config_manager import DEFAULTS
        from screenshot.mask_window import MaskWindow
        bounds = {"left": 0, "top": 0, "width": 200, "height": 150}
        with patch("screenshot.mask_window.visible_windows", return_value=[]):
            mask = MaskWindow(Image.new("RGB", (200, 150)), bounds, [bounds], DEFAULTS)
        mask.show()
        QTest.mousePress(mask, Qt.LeftButton, pos=QPoint(20, 20))
        QTest.mouseMove(mask, QPoint(90, 70))
        QTest.mouseRelease(mask, Qt.LeftButton, pos=QPoint(90, 70))
        self.assertEqual(len(mask.selection.rects), 1)
        for corner in ("topLeft", "topRight", "bottomLeft", "bottomRight"):
            original = QRect(20, 20, 71, 51)
            mask.selection.rects[0] = original
            origin = getattr(original, corner)()
            target = origin + QPoint(4, 4)
            mask.mouseMoveEvent(QMouseEvent(QEvent.MouseMove, QPointF(origin), QPointF(origin),
                                           Qt.NoButton, Qt.NoButton, Qt.NoModifier))
            expected_cursor = Qt.SizeFDiagCursor if corner in ("topLeft", "bottomRight") else Qt.SizeBDiagCursor
            self.assertEqual(mask.cursor().shape(), expected_cursor, corner)
            QTest.mousePress(mask, Qt.LeftButton, pos=origin)
            self.assertEqual(mask.selection.resizing, 0, corner)
            QTest.mouseMove(mask, target)
            QTest.mouseRelease(mask, Qt.LeftButton, pos=target)
            self.assertEqual(len(mask.selection.rects), 1)
            self.assertNotEqual(mask.selection.rects[0], original, corner)
            for key, dx, dy in ((Qt.Key_Left, -1, 0), (Qt.Key_D, 1, 0),
                                (Qt.Key_Up, 0, -1), (Qt.Key_S, 0, 1),
                                (Qt.Key_A, -1, 0), (Qt.Key_Right, 1, 0),
                                (Qt.Key_W, 0, -1), (Qt.Key_Down, 0, 1)):
                before = mask.selection.nudge_corner[2]
                QTest.keyClick(mask, key)
                self.assertEqual(mask.selection.nudge_corner[2], before + QPoint(dx, dy))
            self.assertEqual(mask.selection.rects[0], QRect(mask.selection.nudge_corner[1], target).normalized())
        mask.close()

    def test_mask_drag_resizes_from_each_edge_midpoint(self):
        from PySide6.QtCore import QPoint, QPointF, QEvent
        from PySide6.QtGui import QMouseEvent
        from config.config_manager import DEFAULTS
        from screenshot.mask_window import MaskWindow

        bounds = {"left": 0, "top": 0, "width": 140, "height": 100}
        with patch("screenshot.mask_window.visible_windows", return_value=[]):
            mask = MaskWindow(Image.new("RGB", (140, 100)), bounds, [bounds], DEFAULTS)
        original = QRect(20, 20, 61, 41)
        cases = ((QPoint(50, 20), QPoint(50, 14), Qt.SizeVerCursor),
                 (QPoint(80, 40), QPoint(86, 40), Qt.SizeHorCursor),
                 (QPoint(50, 60), QPoint(50, 66), Qt.SizeVerCursor),
                 (QPoint(20, 40), QPoint(14, 40), Qt.SizeHorCursor))
        mask.show()
        self.app.processEvents()
        for handle, target, cursor in cases:
            mask.selection.rects = [QRect(original)]
            event = QMouseEvent(QEvent.MouseMove, QPointF(handle), QPointF(handle),
                                Qt.NoButton, Qt.NoButton, Qt.NoModifier)
            mask.mouseMoveEvent(event)
            self.assertEqual(mask.cursor().shape(), cursor)
            QTest.mousePress(mask, Qt.LeftButton, pos=handle)
            self.assertEqual(mask.selection.resizing, 0)
            QTest.mouseMove(mask, target)
            QTest.mouseRelease(mask, Qt.LeftButton, pos=target)
            self.assertNotEqual(mask.selection.rects[0], original)
        mask.close()

    def test_mask_resize_cursor_keeps_active_handle_direction(self):
        from config.config_manager import DEFAULTS
        from screenshot.mask_window import MaskWindow
        bounds = {"left": 0, "top": 0, "width": 200, "height": 150}
        with patch("screenshot.mask_window.visible_windows", return_value=[]):
            mask = MaskWindow(Image.new("RGB", (200, 150)), bounds, [bounds], DEFAULTS)
        mask.selection.rects = [QRect(20, 20, 71, 51)]
        mask.show()
        self.app.processEvents()
        QTest.mousePress(mask, Qt.LeftButton, pos=QPoint(20, 20))
        self.assertEqual(mask.cursor().shape(), Qt.SizeFDiagCursor)
        QTest.mouseMove(mask, QPoint(90, 20))
        self.assertEqual(mask.cursor().shape(), Qt.SizeFDiagCursor)
        QTest.mouseRelease(mask, Qt.LeftButton, pos=QPoint(90, 20))
        mask.close()

    def test_mask_keyboard_nudge_moves_pointer_with_selection(self):
        from config.config_manager import DEFAULTS
        from screenshot.mask_window import MaskWindow
        bounds = {"left": 30, "top": 40, "width": 200, "height": 150}
        with patch("screenshot.mask_window.visible_windows", return_value=[]):
            mask = MaskWindow(Image.new("RGB", (200, 150)), bounds, [bounds], DEFAULTS)
        mask.show()
        self.app.processEvents()
        with patch("screenshot.mask_window.QCursor.setPos") as move_pointer:
            QTest.keyClick(mask, Qt.Key_D)
            move_pointer.assert_not_called()
            mask.selection.fixed(QPoint(20, 20), 60, 40)
            QTest.mousePress(mask, Qt.LeftButton, pos=QPoint(40, 40))
            QTest.mouseMove(mask, QPoint(45, 43))
            QTest.mouseRelease(mask, Qt.LeftButton, pos=QPoint(45, 43))
            for key, delta in ((Qt.Key_D, QPoint(1, 0)), (Qt.Key_Left, QPoint(-1, 0)),
                               (Qt.Key_W, QPoint(0, -1)), (Qt.Key_Down, QPoint(0, 1)),
                               (Qt.Key_A, QPoint(-1, 0)), (Qt.Key_Right, QPoint(1, 0)),
                               (Qt.Key_S, QPoint(0, 1)), (Qt.Key_Up, QPoint(0, -1))):
                before_position = QPoint(mask.position)
                before_rect = QRect(mask.selection.rects[0])
                QTest.keyClick(mask, key)
                self.assertEqual(mask.position, before_position + delta)
                self.assertEqual(mask.selection.rects[0], before_rect.translated(delta))
                move_pointer.assert_called_with(mask.mapToGlobal(mask.position))
            corner = mask.selection.rects[0].bottomRight()
            QTest.mousePress(mask, Qt.LeftButton, pos=corner)
            QTest.mouseRelease(mask, Qt.LeftButton, pos=corner)
            QTest.keyClick(mask, Qt.Key_Right)
            self.assertEqual(mask.position, corner + QPoint(1, 0))
            self.assertEqual(mask.selection.nudge_corner[2], corner + QPoint(1, 0))
            move_pointer.assert_called_with(mask.mapToGlobal(mask.position))
        mask.close()

    def test_mask_escape_before_selection_and_fixed_size(self):
        from PySide6.QtWidgets import QDialog
        from config.config_manager import DEFAULTS
        from screenshot.mask_window import MaskWindow
        bounds = {"left": 0, "top": 0, "width": 200, "height": 150}
        with patch("screenshot.mask_window.visible_windows", return_value=[]):
            mask = MaskWindow(Image.new("RGB", (200, 150)), bounds, [bounds], DEFAULTS)
        mask.show()
        self.app.processEvents()
        self.assertTrue(mask.hasFocus())
        QTest.keyClick(mask, Qt.Key_Escape)
        self.assertFalse(mask.isVisible())
        self.assertFalse(mask.selection.rects)

        with patch("screenshot.mask_window.visible_windows", return_value=[]):
            mask = MaskWindow(Image.new("RGB", (200, 150)), bounds, [bounds], DEFAULTS)
        mask.position = QPoint(0, 0)
        with patch("screenshot.mask_window.QDialog.exec", return_value=QDialog.Rejected):
            QTest.keyClick(mask, Qt.Key_F, Qt.ControlModifier)
        self.assertFalse(mask.selection.rects)
        with patch("screenshot.mask_window.QDialog.exec", return_value=QDialog.Accepted):
            QTest.keyClick(mask, Qt.Key_F, Qt.ControlModifier)
        self.assertEqual((mask.selection.rects[-1].width(), mask.selection.rects[-1].height()), (200, 150))
        mask.close()

    def test_mask_opacity_changes_unselected_pixels(self):
        from PySide6.QtGui import QImage
        from config.config_manager import DEFAULTS
        from screenshot.mask_window import MaskWindow
        bounds = {"left": 0, "top": 0, "width": 200, "height": 150}
        settings = {**DEFAULTS, "crosshair": False, "magnifier": False, "mask_opacity": 0}
        with patch("screenshot.mask_window.visible_windows", return_value=[]):
            mask = MaskWindow(Image.new("RGB", (200, 150), "white"), bounds, [bounds], settings)
        output = QImage(200, 150, QImage.Format_ARGB32)
        mask.render(output)
        self.assertEqual(output.pixelColor(100, 100).name(), "#ffffff")
        settings["mask_opacity"] = 100
        mask.render(output)
        self.assertEqual(output.pixelColor(100, 100).name(), "#000000")
        mask.close()

    def test_text_line_spacing_and_undo(self):
        from PySide6.QtCore import Qt
        from config.config_manager import DEFAULTS
        from editor.annotation_items import text_item
        canvas = AnnotationCanvas(Image.new("RGB", (120, 100), "white"), DEFAULTS)
        item = text_item(QPointF(0, 0), "第一行\n第二行", DEFAULTS, Qt.AlignRight)
        canvas.scene_data.addItem(item)
        canvas.checkpoint()
        canvas.undo()
        canvas.redo()
        restored = canvas.annotations()[0]
        self.assertEqual(restored.toPlainText(), "第一行\n第二行")
        self.assertTrue(restored.document().firstBlock().blockFormat().alignment() & Qt.AlignRight)

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
            page = settings.pages.widget(3)

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
            self.assertEqual((ConfigManager(manager.path).data["editor_border_color"],
                              ConfigManager(manager.path).data["editor_border_width"]),
                             ("#e03020", 4))
            with self.assertRaises(ValueError):
                validate({"editor_border_color": "not-a-color"})
            with self.assertRaises(ValueError):
                validate({"editor_border_width": 0})
            canvas.close()

    def test_canvas_undo_export(self):
        from config.config_manager import DEFAULTS
        canvas = AnnotationCanvas(Image.new("RGB", (120, 100), "white"), DEFAULTS)
        canvas.scene_data.addItem(shape("rect", QPointF(10, 10), QPointF(60, 60), "#ff0000", 3))
        canvas.checkpoint()
        self.assertEqual(len(canvas.annotations()), 1)
        canvas.undo()
        self.assertEqual(len(canvas.annotations()), 0)
        canvas.redo()
        self.assertEqual(len(canvas.annotations()), 1)
        self.assertEqual(canvas.render_image().size().width(), 120)

    def test_select_rect_by_its_interior(self):
        from PySide6.QtTest import QTest
        from config.config_manager import DEFAULTS
        canvas = AnnotationCanvas(Image.new("RGB", (120, 100), "white"), DEFAULTS)
        canvas.resize(200, 150)
        canvas.show()
        item = shape("rect", QPointF(10, 10), QPointF(60, 60), "#ff0000", 3)
        canvas.scene_data.addItem(item)
        QTest.mouseClick(canvas.viewport(), Qt.LeftButton, pos=canvas.mapFromScene(QPointF(35, 35)))
        self.assertIn(item, canvas.scene_data.selectedItems())
        canvas.close()

    def test_dragging_selected_annotation_repaints_full_viewport(self):
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QGraphicsView
        from config.config_manager import DEFAULTS

        canvas = AnnotationCanvas(Image.new("RGB", (120, 100), "white"), DEFAULTS)
        canvas.resize(200, 150)
        canvas.show()
        item = shape("rect", QPointF(20, 20), QPointF(55, 55), "#ff0000", 3)
        canvas.scene_data.addItem(item)
        item.setSelected(True)
        self.app.processEvents()
        self.assertEqual(canvas.viewportUpdateMode(), QGraphicsView.FullViewportUpdate)

        start = canvas.mapFromScene(item.sceneBoundingRect().center())
        end = start + QPoint(35, 25)
        QTest.mousePress(canvas.viewport(), Qt.LeftButton, pos=start)
        QTest.mouseMove(canvas.viewport(), pos=end)
        QTest.mouseRelease(canvas.viewport(), Qt.LeftButton, pos=end)
        self.app.processEvents()
        self.assertGreater(item.pos().x(), 20)
        self.assertGreater(item.pos().y(), 20)
        canvas.close()

    def test_select_annotations_with_drag_box(self):
        from PySide6.QtTest import QTest
        from config.config_manager import DEFAULTS
        canvas = AnnotationCanvas(Image.new("RGB", (120, 100), "white"), DEFAULTS)
        canvas.resize(200, 150)
        canvas.show()
        item = shape("rect", QPointF(20, 20), QPointF(50, 50), "#ff0000", 3)
        canvas.scene_data.addItem(item)
        QTest.mousePress(canvas.viewport(), Qt.LeftButton, pos=canvas.mapFromScene(QPointF(5, 5)))
        QTest.mouseMove(canvas.viewport(), pos=canvas.mapFromScene(QPointF(65, 65)))
        QTest.mouseRelease(canvas.viewport(), Qt.LeftButton, pos=canvas.mapFromScene(QPointF(65, 65)))
        self.assertIn(item, canvas.scene_data.selectedItems())
        canvas.close()

    def test_selection_box_stays_visible_but_is_not_exported(self):
        from config.config_manager import DEFAULTS
        canvas = AnnotationCanvas(Image.new("RGB", (120, 100), "white"), DEFAULTS)
        canvas.resize(200, 150)
        canvas.show()
        start = canvas.mapFromScene(QPointF(10, 10))
        end = canvas.mapFromScene(QPointF(70, 60))
        QTest.mousePress(canvas.viewport(), Qt.LeftButton, pos=start)
        QTest.mouseMove(canvas.viewport(), pos=end)
        QTest.mouseRelease(canvas.viewport(), Qt.LeftButton, pos=end)
        self.assertEqual(canvas.selection_area, QRectF(10, 10, 60, 50))
        self.assertFalse(canvas.scene_data.selectedItems())
        self.assertEqual(canvas.render_image().pixelColor(10, 10).name(), "#ffffff")
        QTest.mouseClick(canvas.viewport(), Qt.LeftButton, pos=canvas.mapFromScene(QPointF(90, 80)))
        self.assertIsNone(canvas.selection_area)
        canvas.close()

    def test_right_drag_pans_zoomed_canvas_then_restores_marker(self):
        from config.config_manager import DEFAULTS
        for button in (Qt.RightButton, Qt.MiddleButton):
            with self.subTest(button=button):
                canvas = AnnotationCanvas(Image.new("RGB", (800, 600), "white"), dict(DEFAULTS))
                canvas.resize(220, 180)
                canvas.show()
                canvas.set_zoom(200)
                canvas.tool = "marker"
                horizontal = canvas.horizontalScrollBar()
                vertical = canvas.verticalScrollBar()
                horizontal.setValue(horizontal.maximum() // 2)
                vertical.setValue(vertical.maximum() // 2)
                before = (horizontal.value(), vertical.value())
                QTest.mousePress(canvas.viewport(), button, pos=QPoint(110, 90))
                self.assertEqual(canvas.cursor().shape(), Qt.ClosedHandCursor)
                QTest.mouseMove(canvas.viewport(), pos=QPoint(70, 60))
                QTest.mouseRelease(canvas.viewport(), button, pos=QPoint(70, 60))
                self.assertGreater(horizontal.value(), before[0])
                self.assertGreater(vertical.value(), before[1])
                self.assertEqual(canvas.cursor().shape(), Qt.ArrowCursor)
                self.assertEqual(canvas.tool, "marker")
                self.assertFalse(canvas.annotations())
                QTest.mousePress(canvas.viewport(), Qt.LeftButton, pos=QPoint(80, 80))
                QTest.mouseMove(canvas.viewport(), pos=QPoint(110, 80))
                QTest.mouseRelease(canvas.viewport(), Qt.LeftButton, pos=QPoint(110, 80))
                self.assertEqual(len(canvas.annotations()), 1)
                canvas.close()

    def test_right_button_does_not_run_active_annotation_tool(self):
        from config.config_manager import DEFAULTS
        canvas = AnnotationCanvas(Image.new("RGB", (300, 200), "white"), dict(DEFAULTS))
        canvas.resize(180, 130)
        canvas.show()
        canvas.set_zoom(200)
        confirmations = []
        canvas.confirmed.connect(lambda: confirmations.append(True))
        with patch("PySide6.QtWidgets.QInputDialog.getMultiLineText",
                   side_effect=AssertionError("right click must not edit text")):
            for tool in ("text", "picker", "eraser", "select"):
                canvas.tool = tool
                QTest.mousePress(canvas.viewport(), Qt.RightButton, pos=QPoint(70, 50))
                QTest.mouseMove(canvas.viewport(), pos=QPoint(55, 50))
                QTest.mouseRelease(canvas.viewport(), Qt.RightButton, pos=QPoint(55, 50))
                self.assertEqual(canvas.tool, tool)
                self.assertFalse(canvas.annotations())
            QTest.mouseDClick(canvas.viewport(), Qt.RightButton, pos=QPoint(70, 50))
        self.assertFalse(confirmations)
        canvas.close()

    def test_eraser_removes_only_brushed_pixels_and_undo_restores(self):
        from PySide6.QtTest import QTest
        from config.config_manager import DEFAULTS
        canvas = AnnotationCanvas(Image.new("RGB", (120, 100), "white"), dict(DEFAULTS))
        canvas.resize(200, 150)
        canvas.show()
        canvas.scene_data.addItem(shape("rect", QPointF(20, 20), QPointF(80, 80), "#ff0000", 4))
        canvas.checkpoint()
        canvas.tool = "eraser"
        before = canvas.render_image()
        self.assertEqual(before.pixelColor(50, 20).name(), "#ff0000")
        QTest.mousePress(canvas.viewport(), Qt.LeftButton, pos=canvas.mapFromScene(QPointF(50, 20)))
        QTest.mouseMove(canvas.viewport(), pos=canvas.mapFromScene(QPointF(55, 20)))
        QTest.mouseRelease(canvas.viewport(), Qt.LeftButton, pos=canvas.mapFromScene(QPointF(55, 20)))
        after = canvas.render_image()
        self.assertEqual(after.pixelColor(50, 20).name(), "#ffffff")
        self.assertEqual(after.pixelColor(20, 50).name(), "#ff0000")
        canvas.undo()
        self.assertEqual(canvas.render_image().pixelColor(50, 20), before.pixelColor(50, 20))
        canvas.redo()
        self.assertEqual(canvas.render_image().pixelColor(50, 20).name(), "#ffffff")
        canvas.close()

    def test_marker_is_translucent_wide_and_survives_undo(self):
        from PySide6.QtTest import QTest
        from config.config_manager import DEFAULTS, validate
        settings = dict(DEFAULTS)
        settings["pen_color"] = "#ff0000"
        settings["marker_opacity"] = 50
        self.assertEqual(validate({"annotation_tool": "marker"})["annotation_tool"], "marker")
        canvas = AnnotationCanvas(Image.new("RGB", (120, 100), "white"), settings)
        canvas.resize(200, 150)
        canvas.show()
        canvas.tool = "marker"
        QTest.mousePress(canvas.viewport(), Qt.LeftButton, pos=canvas.mapFromScene(QPointF(20, 30)))
        QTest.mouseMove(canvas.viewport(), pos=canvas.mapFromScene(QPointF(80, 30)))
        QTest.mouseRelease(canvas.viewport(), Qt.LeftButton, pos=canvas.mapFromScene(QPointF(80, 30)))
        item = canvas.annotations()[0]
        self.assertGreaterEqual(item.pen().width(), 12)
        self.assertEqual(item.pen().color().alpha(), 128)
        self.assertNotEqual(canvas.render_image().pixelColor(40, 30).name(), "#ff0000")
        canvas.undo()
        self.assertEqual(len(canvas.annotations()), 0)
        canvas.redo()
        self.assertEqual(canvas.annotations()[0].pen().color().alpha(), 128)
        self.assertEqual(canvas.annotations()[0].pen().capStyle(), Qt.RoundCap)
        canvas.close()

    def test_highlighter_opacity_validation_and_legacy_wide_tool(self):
        from config.config_manager import DEFAULTS, validate
        for opacity in (0, 101):
            with self.assertRaises(ValueError):
                validate({"marker_opacity": opacity})
        self.assertEqual(validate({"annotation_tool": "wide"})["annotation_tool"], "select")
        self.assertEqual(validate({"annotation_tool": "square"})["annotation_tool"], "select")
        self.assertEqual(validate({"marker_opacity": 1})["marker_opacity"], 1)
        settings = dict(DEFAULTS, marker_opacity=100)
        canvas = AnnotationCanvas(Image.new("RGB", (120, 100), "white"), settings)
        canvas.tool = "marker"
        self.assertEqual(canvas.stroke_pen().color().alpha(), 255)
        self.assertGreater(canvas.stroke_pen().width(), settings["pen_width"])
        canvas.tool = "pen"
        self.assertEqual(canvas.stroke_pen().color().alpha(), 255)
        settings["marker_opacity"] = 1
        self.assertEqual(canvas.stroke_pen().color().alpha(), 255)
        canvas.tool = "marker"
        self.assertEqual(canvas.stroke_pen().color().alpha(), round(255 / 100))

    def test_eraser_partially_clears_pixmap_annotation(self):
        from PySide6.QtGui import QPixmap, QColor
        from PySide6.QtWidgets import QGraphicsPixmapItem
        from config.config_manager import DEFAULTS
        canvas = AnnotationCanvas(Image.new("RGB", (120, 100), "white"), dict(DEFAULTS))
        canvas.resize(200, 150)
        canvas.show()
        pixmap = QPixmap(50, 50)
        pixmap.fill(QColor("#ff0000"))
        item = QGraphicsPixmapItem(pixmap)
        item.setPos(QPointF(20, 20))
        canvas.scene_data.addItem(item)
        canvas.checkpoint()
        canvas.tool = "eraser"
        self.assertEqual(canvas.render_image().pixelColor(40, 40).name(), "#ff0000")
        self.assertIn(item, canvas.scene_data.items(QRectF(35, 35, 10, 10)))
        QTest.mouseMove(canvas.viewport(), pos=canvas.mapFromScene(QPointF(40, 40)))
        self.assertIsNotNone(canvas.eraser_point)
        QTest.mouseClick(canvas.viewport(), Qt.LeftButton, pos=canvas.mapFromScene(QPointF(40, 40)))
        self.assertIs(canvas.annotations()[0], item)
        self.assertEqual(canvas.annotations()[0].pixmap().toImage().pixelColor(20, 20).alpha(), 0)
        self.assertEqual(canvas.render_image().pixelColor(40, 40).name(), "#ffffff")
        self.assertEqual(canvas.render_image().pixelColor(60, 60).name(), "#ff0000")
        canvas.undo()
        self.assertEqual(canvas.render_image().pixelColor(40, 40).name(), "#ff0000")
        canvas.close()

    def test_mosaic_preview_border_ignores_annotation_width(self):
        from PySide6.QtGui import QPainter, QImage
        from config.config_manager import DEFAULTS
        settings = dict(DEFAULTS)
        canvas = AnnotationCanvas(Image.new("RGB", (120, 100), "white"), settings)
        canvas.tool = "mosaic"
        canvas.start = QPointF(10, 10)
        canvas.preview_end = QPointF(80, 60)
        previews = []
        for width in (1, 50):
            settings["pen_width"] = width
            image = QImage(120, 100, QImage.Format_ARGB32)
            image.fill(Qt.transparent)
            painter = QPainter(image)
            try:
                canvas.drawForeground(painter, QRectF(0, 0, 120, 100))
            finally:
                painter.end()
            previews.append(image)
        self.assertEqual(previews[0], previews[1])

    def test_mosaic_modes_produce_distinct_pixels(self):
        from editor.annotation_canvas import mosaic_image
        sample = Image.new("RGB", (60, 40), "white")
        for row in range(40):
            for column in range(60):
                sample.putpixel((column, row), (column * 4, row * 6, 0))
        results = [mosaic_image(sample, mode, 12) for mode in ("blocks", "fine", "blur")]
        self.assertTrue(all(result.size == sample.size for result in results))
        self.assertEqual(len({result.tobytes() for result in results}), 3)

    def test_selected_annotation_resize_handle_not_exported(self):
        from config.config_manager import DEFAULTS
        from PySide6.QtCore import QEvent
        from PySide6.QtGui import QMouseEvent
        canvas = AnnotationCanvas(Image.new("RGB", (120, 100), "white"), DEFAULTS)
        canvas.resize(200, 180)
        canvas.show()
        item = shape("rect", QPointF(10, 10), QPointF(20, 20), "#ff0000", 2)
        canvas.scene_data.addItem(item)
        item.setSelected(True)
        fixed_corner = item.sceneBoundingRect().topLeft()
        self.app.processEvents()
        corner = canvas.mapFromScene(item.sceneBoundingRect().bottomRight())
        def hover(position):
            self.app.sendEvent(canvas.viewport(), QMouseEvent(
                QEvent.MouseMove, QPointF(position),
                QPointF(canvas.viewport().mapToGlobal(position)),
                Qt.NoButton, Qt.NoButton, Qt.NoModifier))

        hover(QPoint(5, 5))
        hover(corner)
        self.assertEqual(canvas.cursor().shape(), Qt.SizeFDiagCursor)
        hover(QPoint(5, 5))
        self.assertEqual(canvas.cursor().shape(), Qt.ArrowCursor)
        hover(corner)
        QTest.mousePress(canvas.viewport(), Qt.LeftButton, pos=corner)
        QTest.mouseMove(canvas.viewport(), corner + QPoint(30, 20))
        QTest.mouseRelease(canvas.viewport(), Qt.LeftButton, pos=corner + QPoint(30, 20))
        self.assertGreater(item.scale(), 1)
        self.assertAlmostEqual(item.sceneBoundingRect().topLeft().x(), fixed_corner.x(), delta=1)
        self.assertAlmostEqual(item.sceneBoundingRect().topLeft().y(), fixed_corner.y(), delta=1)
        self.assertEqual(canvas.render_image().pixelColor(24, 24).name(), "#ffffff")
        canvas.close()

    def test_annotations_stay_inside_centered_image(self):
        from config.config_manager import DEFAULTS
        from editor.annotation_items import text_item
        canvas = AnnotationCanvas(Image.new("RGB", (120, 100), "white"), dict(DEFAULTS))
        canvas.resize(260, 220)
        canvas.show()
        self.app.processEvents()
        self.assertEqual(canvas.alignment(), Qt.AlignCenter)
        image = canvas.sceneRect()
        for tool in ("pen", "marker", "rect", "ellipse", "arrow", "mosaic", "text"):
            canvas.tool = tool
            outside = canvas.mapFromScene(QPointF(-12, -12))
            with patch("editor.annotation_canvas.QInputDialog.getMultiLineText") as dialog:
                QTest.mousePress(canvas.viewport(), Qt.LeftButton, pos=outside)
                QTest.mouseRelease(canvas.viewport(), Qt.LeftButton, pos=outside)
                dialog.assert_not_called()
            self.assertFalse(canvas.annotations(), tool)
        for tool in ("pen", "marker", "rect", "ellipse", "arrow", "mosaic"):
            canvas.tool = tool
            start = canvas.mapFromScene(QPointF(45, 40))
            outside = canvas.mapFromScene(QPointF(150, 130))
            QTest.mousePress(canvas.viewport(), Qt.LeftButton, pos=start)
            QTest.mouseMove(canvas.viewport(), outside)
            QTest.mouseRelease(canvas.viewport(), Qt.LeftButton, pos=outside)
            item = canvas.annotations()[-1]
            self.assertTrue(image.contains(item.sceneBoundingRect()), tool)
        text = text_item(QPointF(115, 95), "A long text annotation", DEFAULTS, Qt.AlignLeft)
        canvas.scene_data.addItem(text)
        canvas.checkpoint()
        self.assertTrue(image.contains(text.sceneBoundingRect()))
        canvas.close()

    def test_selected_annotation_move_and_resize_stay_inside_image(self):
        from config.config_manager import DEFAULTS
        canvas = AnnotationCanvas(Image.new("RGB", (120, 100), "white"), dict(DEFAULTS))
        canvas.resize(260, 220)
        canvas.show()
        item = shape("rect", QPointF(20, 20), QPointF(40, 40), "#ff0000", 2)
        canvas.scene_data.addItem(item)
        canvas.tool = "select"
        self.app.processEvents()
        center = canvas.mapFromScene(item.sceneBoundingRect().center())
        QTest.mousePress(canvas.viewport(), Qt.LeftButton, pos=center)
        QTest.mouseMove(canvas.viewport(), center + QPoint(150, 120))
        QTest.mouseRelease(canvas.viewport(), Qt.LeftButton, pos=center + QPoint(150, 120))
        self.assertTrue(canvas.sceneRect().contains(item.sceneBoundingRect()))
        item.setSelected(True)
        corner = canvas.mapFromScene(item.sceneBoundingRect().bottomRight())
        QTest.mousePress(canvas.viewport(), Qt.LeftButton, pos=corner)
        QTest.mouseMove(canvas.viewport(), corner + QPoint(150, 120))
        QTest.mouseRelease(canvas.viewport(), Qt.LeftButton, pos=corner + QPoint(150, 120))
        self.assertTrue(canvas.sceneRect().contains(item.sceneBoundingRect()))
        canvas.close()

    def test_switching_annotation_tool_resets_resize_cursor(self):
        from config.config_manager import DEFAULTS
        from editor.annotation_items import shape
        from editor.editor_window import EditorWindow
        editor = EditorWindow(Image.new("RGB", (120, 100), "white"), dict(DEFAULTS))
        editor.resize(200, 180)
        editor.show()
        item = shape("rect", QPointF(10, 10), QPointF(20, 20), "#ff0000", 2)
        editor.canvas.scene_data.addItem(item)
        item.setSelected(True)
        self.app.processEvents()
        corner = editor.canvas.mapFromScene(item.sceneBoundingRect().bottomRight())
        QTest.mouseMove(editor.canvas.viewport(), corner)
        self.assertEqual(editor.canvas.cursor().shape(), Qt.SizeFDiagCursor)
        editor.set_tool("pen")
        self.assertEqual(editor.canvas.cursor().shape(), Qt.ArrowCursor)
        editor.close()

    def test_toolbar_resets_last_angle_without_discarding_later_edits(self):
        from PySide6.QtWidgets import QDialog, QDoubleSpinBox
        from config.config_manager import DEFAULTS

        source = Image.new("RGB", (40, 20), "red")
        editor = EditorWindow(source, dict(DEFAULTS))

        def confirm_rotation(dialog):
            dialog.findChild(QDoubleSpinBox, "rotationDegrees").setValue(45)
            return QDialog.Accepted

        with patch("PySide6.QtWidgets.QDialog.exec", new=confirm_rotation):
            editor.rotate_angle()
        reset_button = editor.toolbar.reset_rotation_button
        self.assertTrue(reset_button.isEnabled())
        reset_button.click()
        self.assertEqual(editor.canvas.image.tobytes(), source.tobytes())
        self.assertFalse(reset_button.isEnabled())

        with patch("PySide6.QtWidgets.QDialog.exec", new=confirm_rotation):
            editor.rotate_angle()
        item = shape("rect", QPointF(2, 2), QPointF(8, 8), "#0000ff", 1)
        editor.canvas.scene_data.addItem(item)
        editor.canvas.checkpoint()
        self.assertFalse(reset_button.isEnabled())
        self.assertIsNot(editor.canvas.image, source)
        editor.close()

    def test_drag_preview_visible_but_not_exported(self):
        from config.config_manager import DEFAULTS
        canvas = AnnotationCanvas(Image.new("RGB", (120, 100), "white"), DEFAULTS)
        canvas.resize(240, 200)
        canvas.tool = "rect"
        canvas.show()
        self.app.processEvents()
        start = canvas.mapFromScene(QPointF(10, 10))
        end = canvas.mapFromScene(QPointF(60, 50))
        QTest.mousePress(canvas.viewport(), Qt.LeftButton, pos=start)
        QTest.mouseMove(canvas.viewport(), end)
        self.app.processEvents()
        self.assertIsNotNone(canvas.preview_end)
        self.assertEqual(len(canvas.annotations()), 0)
        self.assertEqual(canvas.render_image().pixelColor(10, 30).name(), "#ffffff")
        self.assertNotEqual(canvas.viewport().grab().toImage().pixelColor(canvas.mapFromScene(QPointF(10, 30))).name(),
                            "#ffffff")
        QTest.mouseRelease(canvas.viewport(), Qt.LeftButton, pos=end)
        self.assertEqual(len(canvas.annotations()), 1)
        self.assertIsNone(canvas.preview_end)
        canvas.close()

    def test_all_drag_tools_show_preview(self):
        from config.config_manager import DEFAULTS
        for tool in ("rect", "ellipse", "arrow", "pen", "marker", "mosaic", "crop"):
            with self.subTest(tool=tool):
                canvas = AnnotationCanvas(Image.new("RGB", (120, 100), "white"), DEFAULTS)
                canvas.resize(240, 200)
                canvas.tool = tool
                canvas.show()
                self.app.processEvents()
                before = canvas.viewport().grab().toImage().bits().tobytes()
                QTest.mousePress(canvas.viewport(), Qt.LeftButton, pos=canvas.mapFromScene(QPointF(10, 10)))
                QTest.mouseMove(canvas.viewport(), canvas.mapFromScene(QPointF(65, 55)))
                self.app.processEvents()
                self.assertNotEqual(canvas.viewport().grab().toImage().bits().tobytes(), before)
                self.assertEqual(canvas.render_image().pixelColor(30, 30).name(), "#ffffff")
                QTest.mouseRelease(canvas.viewport(), Qt.LeftButton,
                                   pos=canvas.mapFromScene(QPointF(65, 55)))
                canvas.close()

    def test_existing_text_context_menu_edits_and_undoes(self):
        from config.config_manager import DEFAULTS
        from editor.annotation_items import text_item
        editor = EditorWindow(Image.new("RGB", (300, 180), "white"), DEFAULTS)
        canvas = editor.canvas
        canvas.scene_data.addItem(text_item(QPointF(10, 10), "原文", DEFAULTS, Qt.AlignRight))
        canvas.checkpoint()
        editor.show()
        self.app.processEvents()
        with patch("editor.annotation_canvas.QInputDialog.getMultiLineText", return_value=("修改后", True)) as dialog:
            canvas.annotation_menu(canvas.annotations()[0]).actions()[0].trigger()
        self.assertEqual(dialog.call_args.args[3], "原文")
        self.assertEqual(canvas.annotations()[0].toPlainText(), "修改后")
        self.assertTrue(canvas.annotations()[0].document().firstBlock().blockFormat().alignment() & Qt.AlignRight)
        self.assertTrue(editor.isVisible())
        canvas.undo()
        self.assertEqual(canvas.annotations()[0].toPlainText(), "原文")
        editor.close()

    def test_double_click_shape_selects_without_finishing(self):
        from config.config_manager import DEFAULTS
        editor = EditorWindow(Image.new("RGB", (180, 120), "white"), DEFAULTS)
        item = shape("rect", QPointF(10, 10), QPointF(70, 60), "#ff0000", 3)
        editor.canvas.scene_data.addItem(item)
        editor.toolbar.tool_buttons["arrow"].click()
        editor.show()
        self.app.processEvents()
        QTest.mouseDClick(editor.canvas.viewport(), Qt.LeftButton,
                         pos=editor.canvas.mapFromScene(QPointF(12, 30)))
        self.assertTrue(editor.isVisible())
        self.assertTrue(item.isSelected())
        self.assertTrue(editor.toolbar.tool_buttons["select"].isChecked())
        self.assertEqual(editor.canvas.tool, "select")
        QTest.mouseMove(editor.canvas.viewport(), pos=editor.canvas.mapFromScene(QPointF(12, 30)))
        self.assertEqual(editor.canvas.cursor().shape(), Qt.SizeAllCursor)
        QTest.mouseMove(editor.canvas.viewport(),
                        pos=editor.canvas.mapFromScene(item.sceneBoundingRect().bottomRight()))
        self.assertEqual(editor.canvas.cursor().shape(), Qt.SizeFDiagCursor)
        editor.close()

    def test_double_click_deletes_only_hit_annotation_and_undo_restores(self):
        from config.config_manager import DEFAULTS
        from editor.annotation_items import text_item
        canvas = AnnotationCanvas(Image.new("RGB", (200, 120), "white"), dict(DEFAULTS))
        canvas.resize(300, 190)
        first = shape("rect", QPointF(10, 10), QPointF(65, 60), "#ff0000", 3)
        second = text_item(QPointF(90, 10), "保留", DEFAULTS, Qt.AlignLeft)
        canvas.scene_data.addItem(first)
        canvas.scene_data.addItem(second)
        canvas.checkpoint()
        canvas.show()
        self.app.processEvents()
        second.setSelected(True)
        QTest.mouseDClick(canvas.viewport(), Qt.LeftButton, pos=canvas.mapFromScene(QPointF(12, 30)))
        self.assertEqual(len(canvas.annotations()), 1)
        self.assertIs(canvas.annotations()[0], second)
        canvas.undo()
        self.assertEqual(len(canvas.annotations()), 2)
        QTest.mouseDClick(canvas.viewport(), Qt.LeftButton, pos=canvas.mapFromScene(QPointF(95, 18)))
        self.assertEqual(len(canvas.annotations()), 1)
        canvas.undo()
        self.assertEqual(len(canvas.annotations()), 2)
        canvas.close()

    def test_right_click_annotation_menu_deletes_and_undo_restores(self):
        from config.config_manager import DEFAULTS
        canvas = AnnotationCanvas(Image.new("RGB", (140, 100), "white"), dict(DEFAULTS))
        canvas.resize(220, 160)
        canvas.scene_data.addItem(shape("rect", QPointF(10, 10), QPointF(65, 60), "#ff0000", 3))
        canvas.checkpoint()
        canvas.show()
        self.app.processEvents()
        point = canvas.mapFromScene(QPointF(12, 30))
        with patch.object(canvas, "show_annotation_menu") as show_menu:
            QTest.mousePress(canvas.viewport(), Qt.RightButton, pos=point)
            QTest.mouseRelease(canvas.viewport(), Qt.RightButton, pos=point)
        show_menu.assert_called_once()
        canvas.annotation_menu(canvas.annotations()[0]).actions()[-1].trigger()
        self.assertFalse(canvas.annotations())
        canvas.undo()
        self.assertEqual(len(canvas.annotations()), 1)
        canvas.close()

    def test_editor_zoom_controls_and_wheel_keep_annotations_aligned(self):
        from PySide6.QtCore import QPointF
        from PySide6.QtGui import QWheelEvent
        from config.config_manager import DEFAULTS
        editor = EditorWindow(Image.new("RGB", (180, 120), "white"), DEFAULTS)
        item = shape("rect", QPointF(10, 10), QPointF(30, 30), "#ff0000", 3)
        editor.canvas.scene_data.addItem(item)
        original_width = item.sceneBoundingRect().width()
        editor.show()
        self.app.processEvents()
        editor.zoom_input.setValue(175)
        self.assertEqual(editor.zoom_slider.value(), 175)
        self.assertAlmostEqual(editor.canvas.transform().m11(), 1.75)
        self.assertAlmostEqual(item.sceneBoundingRect().width() * editor.canvas.transform().m11(),
                               original_width * 1.75)
        editor.zoom_slider.setValue(400)
        self.assertEqual(editor.zoom_input.value(), 400)
        editor.resize(480, 360)
        self.app.processEvents()
        position = QPointF(50, 50)
        vertical = editor.canvas.verticalScrollBar()
        horizontal = editor.canvas.horizontalScrollBar()
        vertical.setValue(vertical.maximum() // 2)
        horizontal.setValue(horizontal.maximum() // 2)
        old_zoom = editor.zoom_input.value()
        old_vertical = vertical.value()
        expected_vertical_step = max(1, vertical.singleStep())
        event = QWheelEvent(position, position, QPoint(0, 0), QPoint(0, 120),
                            Qt.NoButton, Qt.NoModifier, Qt.ScrollUpdate, False)
        self.app.sendEvent(editor.canvas.viewport(), event)
        self.assertEqual(editor.zoom_input.value(), old_zoom)
        self.assertNotEqual(vertical.value(), old_vertical)
        self.assertEqual(old_vertical - vertical.value(), round(expected_vertical_step * 1.5))
        self.assertEqual(editor.canvas.render_image().size(), editor.canvas.base.pixmap().size())
        item.setSelected(True)
        old_item_scale = item.scale()
        old_horizontal = horizontal.value()
        expected_horizontal_step = max(1, horizontal.singleStep())
        ctrl_event = QWheelEvent(position, position, QPoint(0, 0), QPoint(0, 120),
                     Qt.NoButton, Qt.ControlModifier, Qt.ScrollUpdate, False)
        self.app.sendEvent(editor.canvas.viewport(), ctrl_event)
        self.assertNotEqual(horizontal.value(), old_horizontal)
        self.assertEqual(old_horizontal - horizontal.value(), round(expected_horizontal_step * 1.5))
        old_horizontal = horizontal.value()
        alt_event = QWheelEvent(position, position, QPoint(0, 0), QPoint(0, 120),
                                Qt.NoButton, Qt.AltModifier, Qt.ScrollUpdate, False)
        self.app.sendEvent(editor.canvas.viewport(), alt_event)
        self.assertEqual(editor.zoom_input.value(), old_zoom)
        self.assertNotEqual(horizontal.value(), old_horizontal)
        self.assertEqual(old_horizontal - horizontal.value(), round(expected_horizontal_step * 1.5))
        self.assertEqual(item.scale(), old_item_scale)
        editor.close()

    def test_toolbar_wraps_groups_at_window_width(self):
        from config.config_manager import DEFAULTS
        from PySide6.QtWidgets import QLabel, QToolButton
        editor = EditorWindow(Image.new("RGB", (120, 90), "white"), DEFAULTS)
        editor.resize(780, 600)
        editor.show()
        self.app.processEvents()
        labels = [label for label in editor.toolbar.findChildren(QLabel)
              if label.text() in ("标注", "编辑", "图像旋转", "输出")]
        self.assertIs(editor.toolbar.image_buttons[0], editor.toolbar.reset_rotation_button)
        for width, rows in ((3200, 1), (2300, 3), (1300, 3), (1100, 3), (950, 3),
                (800, 4), (700, 4), (699, 4), (660, 4), (659, 4),
                (500, 4), (420, 4)):
            editor.resize(width, 760)
            self.app.processEvents()
            self.app.processEvents()
            grid = editor.toolbar.tool_grid
            tool_rows = {grid.getItemPosition(index)[0] for index in range(grid.count())}
            if width in (1300, 2300, 3200):
                self.assertEqual(len(tool_rows) == 1, width in (2300, 3200), width)
            self.assertEqual(grid.getItemPosition(grid.indexOf(editor.toolbar.cursor_switch))[:2],
                             (0, 0), width)
            self.assertEqual(grid.getItemPosition(grid.indexOf(editor.toolbar.pen_color))[:2],
                             (0, 1), width)
            self.assertEqual(grid.getItemPosition(grid.indexOf(editor.toolbar.options_button))[:2], (0, 2), width)
            if width == 1300:
                wide_height = editor.toolbar.height()
            top_positions = {label.mapTo(editor.toolbar, QPoint(0, 0)).y() for label in labels}
            self.assertEqual(len(top_positions), rows)
            self.assertLessEqual(editor.toolbar.height(), editor.canvas.geometry().top())
            sections = [QRect(section.mapTo(editor.toolbar, QPoint(0, 0)), section.size())
                        for section in editor.toolbar.sections]
            for index, section in enumerate(sections):
                self.assertLessEqual(section.right(), editor.toolbar.width(), (width, index, section))
                for other in sections[index + 1:]:
                    self.assertFalse(section.intersects(other), (width, section, other))
            for section, buttons in ((editor.toolbar.sections[1], editor.toolbar.edit_buttons),
                                     (editor.toolbar.sections[2], editor.toolbar.image_buttons),
                                     (editor.toolbar.sections[3], editor.toolbar.output_buttons)):
                self.assertLessEqual(buttons[0].mapTo(section, QPoint(0, 0)).x(), 4,
                                     (width, buttons[0].text()))
            output_rectangles = [QRect(button.mapTo(editor.toolbar, QPoint(0, 0)), button.size())
                                for button in editor.toolbar.output_buttons]
            for index, rectangle in enumerate(output_rectangles):
                self.assertLessEqual(rectangle.right(), editor.toolbar.width(), (width, rectangle))
                for other in output_rectangles[index + 1:]:
                    self.assertFalse(rectangle.intersects(other), (width, rectangle, other))
            for button in editor.toolbar.tool_buttons.values():
                self.assertLessEqual(button.mapTo(editor.toolbar, QPoint(button.width(), 0)).x(),
                                     editor.toolbar.width())
                self.assertGreaterEqual(button.height(), 40)
            drawing_buttons = list(editor.toolbar.tool_buttons.values())
            drawing_buttons.append(next(button for button in editor.toolbar.findChildren(QToolButton)
                                        if button.text() == "更多设置"))
            drawing_buttons.append(editor.toolbar.pen_color)
            drawing_buttons.append(editor.toolbar.cursor_switch)
            rectangles = [QRect(button.mapTo(editor.toolbar, QPoint(0, 0)), button.size())
                          for button in drawing_buttons]
            for index, rectangle in enumerate(rectangles):
                self.assertLessEqual(rectangle.right(), editor.toolbar.width(),
                                     (width, drawing_buttons[index].text()))
                for other_index in range(index + 1, len(rectangles)):
                    self.assertFalse(rectangle.intersects(rectangles[other_index]),
                                     (width, drawing_buttons[index].text(), rectangle,
                                      drawing_buttons[other_index].text(), rectangles[other_index],
                                      editor.toolbar.height(), editor.toolbar.section_layout.sizeHint().height(),
                                      editor.toolbar.tool_grid.sizeHint().height()))
        editor.resize(1300, 760)
        self.app.processEvents()
        self.app.processEvents()
        self.assertEqual(editor.toolbar.height(), wide_height)
        self.assertEqual({editor.toolbar.tool_grid.getItemPosition(index)[0]
                          for index in range(editor.toolbar.tool_grid.count())}, {0, 1})
        self.assertEqual(editor.canvas.alignment(), Qt.AlignCenter)
        editor.close()

    def test_toolbar_edit_rotation_gap_stays_compact(self):
        from config.config_manager import DEFAULTS
        editor = EditorWindow(Image.new("RGB", (120, 90), "white"), dict(DEFAULTS))
        editor.show()
        self.app.processEvents()
        self.app.processEvents()
        self.assertEqual(editor.width(), 1200)
        editing, rotation = editor.toolbar.sections[1:3]
        initial_gap = rotation.mapTo(editor.toolbar, QPoint(0, 0)).x() - editing.mapTo(
            editor.toolbar, QPoint(editing.width(), 0)).x()
        self.assertLessEqual(initial_gap, 24)
        for width in (900, 950, 1100, 1199, 1200, 1250, 1300, 1700, 2100, 2300, 2600, 1100):
            editor.resize(width, 760)
            self.app.processEvents()
            self.app.processEvents()
            editing, rotation = editor.toolbar.sections[1:3]
            editing_end = editing.mapTo(editor.toolbar, QPoint(editing.width(), 0)).x()
            rotation_start = rotation.mapTo(editor.toolbar, QPoint(0, 0)).x()
            self.assertLessEqual(rotation_start - editing_end, 24, width)
            self.assertGreaterEqual(rotation_start - editing_end, 0, width)
        editor.close()

    def test_transform_undo_and_redo(self):
        from config.config_manager import DEFAULTS
        editor = EditorWindow(Image.new("RGB", (120, 80), "white"), DEFAULTS)
        editor.execute("right")
        self.assertEqual(editor.canvas.image.size, (80, 120))
        editor.execute("undo")
        self.assertEqual(editor.canvas.image.size, (120, 80))
        editor.execute("redo")
        self.assertEqual(editor.canvas.image.size, (80, 120))

    def test_arbitrary_rotation_uses_transparent_expansion_and_reset_button(self):
        from PySide6.QtWidgets import QDialog, QDoubleSpinBox, QPushButton
        from config.config_manager import DEFAULTS
        from editor.image_transform import transform

        source = Image.new("RGB", (40, 20), "red")
        rotated = transform(source, "angle", 45)
        self.assertEqual(rotated.mode, "RGBA")
        self.assertEqual(rotated.getpixel((0, 0))[3], 0)
        self.assertEqual(rotated.getpixel((rotated.width // 2, rotated.height // 2))[3], 255)

        editor = EditorWindow(source, dict(DEFAULTS))
        observed = []

        def reset_and_accept(dialog):
            degrees = dialog.findChild(QDoubleSpinBox, "rotationDegrees")
            degrees.setValue(45)
            observed.append((editor.canvas.image.mode, editor.canvas.image.size))
            reset = dialog.findChild(QPushButton, "resetRotationAngle")
            reset.click()
            self.assertEqual(editor.canvas.image.size, source.size)
            self.assertEqual(editor.canvas.image.tobytes(), source.tobytes())
            return QDialog.Accepted

        with patch("PySide6.QtWidgets.QDialog.exec", new=reset_and_accept):
            editor.rotate_angle()
        self.assertEqual(observed[0][0], "RGBA")
        self.assertNotEqual(observed[0][1], source.size)
        editor.close()

    def test_capture_cursor_variants_share_one_frame(self):
        from core.screen_capture import capture
        bounds = {"left": 0, "top": 0, "width": 30, "height": 30}
        shot = Mock(size=(30, 30), rgb=Image.new("RGB", (30, 30), "white").tobytes())
        with patch("core.screen_capture.mss.mss") as factory, patch(
            "core.screen_capture.native_cursor", return_value=(
                Image.new("RGBA", (2, 2), (255, 0, 0, 255)), 10, 10)):
            grabber = factory.return_value.__enter__.return_value
            grabber.monitors = [bounds, bounds]
            grabber.grab.return_value = shot
            plain, _, _, with_cursor = capture(False, alternatives=True)
            self.assertEqual(grabber.grab.call_count, 1)
            self.assertNotEqual(plain.tobytes(), with_cursor.tobytes())
            self.assertEqual(plain.getpixel((0, 0)), with_cursor.getpixel((0, 0)))
            self.assertEqual(with_cursor.getpixel((10, 10)), (255, 0, 0))

    def test_capture_preview_preserves_pixels_after_source_changes(self):
        from core.screen_capture import to_qimage
        for mode, color in (("RGB", (17, 89, 203)), ("RGBA", (17, 89, 203, 120))):
            source = Image.new(mode, (3, 2), color)
            preview = to_qimage(source)
            source.paste((0,) * len(color), (0, 0, 3, 2))
            pixel = preview.pixelColor(2, 1)
            self.assertEqual((pixel.red(), pixel.green(), pixel.blue()), color[:3])
            self.assertEqual(pixel.alpha(), color[3] if mode == "RGBA" else 255)

    def test_hotkey_capture_skips_tray_menu_delay(self):
        from main import Application
        app = Application.__new__(Application)
        app.mask = None
        app.config = Mock(data={"last_capture_rect": [1, 2, 3, 4]})
        app.show_mask = Mock()
        with patch("main.QTimer.singleShot") as schedule:
            app.start_capture("capture")
            self.assertEqual(schedule.call_args.args[0], 0)
            schedule.call_args.args[1]()
            app.show_mask.assert_called_once_with("capture")
            app.start_capture("capture", from_tray=True)
            self.assertEqual(schedule.call_args.args[0], 150)

    def test_repeat_capture_remembers_global_region_and_crops_fresh_frame(self):
        from main import Application
        from screenshot.mask_window import MaskWindow
        from config.config_manager import DEFAULTS
        from ui.settings_hotkey import HotkeyPage
        bounds = {"left": -100, "top": 30, "width": 200, "height": 150}
        with tempfile.TemporaryDirectory() as folder:
            manager = ConfigManager(Path(folder) / "settings.json")
            page = HotkeyPage(manager, manager.save)
            self.assertEqual(page.edit_repeat.keySequence().toString().lower(),
                             manager.data["hotkeys"]["repeat"])
            with patch("screenshot.mask_window.visible_windows", return_value=[]):
                mask = MaskWindow(Image.new("RGB", (200, 150), "white"), bounds,
                                  [bounds], manager.data)
            app = Application.__new__(Application)
            app.config = manager
            app.mask = None
            app.logger = Mock()
            app.notify = Mock()
            app.edit_images = Mock()
            app.start_capture = Mock()
            app.dispatch("repeat")
            app.start_capture.assert_called_once_with("repeat")
            app.start_capture = Application.start_capture.__get__(app)
            mask.last_region.connect(app.remember_region)
            mask.selection.rects.append(QRect(20, 25, 30, 20))
            mask.complete()
            self.assertEqual(ConfigManager(manager.path).data["last_capture_rect"], [-80, 55, 30, 20])
            fresh = Image.new("RGB", (200, 150), "#18bb44")
            with patch("main.capture", return_value=(fresh, bounds, [bounds], None)) as grab:
                app.show_mask("repeat")
            grab.assert_called_once_with(DEFAULTS["cursor"], alternatives=True)
            image, alternate = app.edit_images.call_args.args[0][0]
            self.assertEqual(image.size, (30, 20))
            self.assertEqual(image.getpixel((10, 10)), (24, 187, 68))
            self.assertIsNone(alternate)
            self.assertIsNone(app.mask)
            app.config.data["last_capture_rect"] = []
            app.start_capture("repeat")
            app.notify.assert_called_with("暂无上次截图区域")

    def test_cursor_switch_and_undo(self):
        from config.config_manager import DEFAULTS
        normal = Image.new("RGB", (40, 40), "white")
        cursor = Image.new("RGB", (40, 40), "black")
        editor = EditorWindow(normal, DEFAULTS, cursor)
        editor.toolbar.cursor_switch.setChecked(True)
        self.assertEqual(editor.canvas.image.getpixel((0, 0)), (0, 0, 0))
        editor.execute("undo")
        self.assertEqual(editor.canvas.image.getpixel((0, 0)), (255, 255, 255))
        self.assertFalse(editor.toolbar.cursor_switch.isChecked())

    def test_edit_selected_annotation_and_copy_both_formats(self):
        from PySide6.QtCore import QPointF
        from PySide6.QtGui import QGuiApplication
        from config.config_manager import DEFAULTS
        from editor.annotation_items import text_item
        with tempfile.TemporaryDirectory() as folder:
            settings = {**DEFAULTS, "manual_dir": folder, "open_dir": False}
            editor = EditorWindow(Image.new("RGB", (60, 40), "white"), settings)
            item = shape("rect", QPointF(2, 2), QPointF(25, 20), "#ff0000", 3)
            editor.canvas.scene_data.addItem(item)
            item.setSelected(True)
            editor.toolbar.tool_buttons["rect"].click()
            editor.toolbar.pen_width.setValue(7)
            editor.set_pen_color("#00ff00")
            self.assertEqual(item.pen().width(), 7)
            self.assertEqual(item.pen().color().name(), "#00ff00")
            item.setSelected(False)
            text = text_item(QPointF(4, 4), "可编辑", settings, Qt.AlignLeft)
            editor.canvas.scene_data.addItem(text)
            text.setSelected(True)
            editor.set_annotation_setting("font", "Courier New")
            self.assertEqual(text.font().family(), "Courier New")
            saved = []
            editor.image_saved.connect(lambda path, image: saved.append((path, image)))
            editor.execute("save")
            self.assertEqual(saved[-1][0], str(editor.last_path))
            self.assertFalse(saved[-1][1].isNull())
            mime = QGuiApplication.clipboard().mimeData()
            self.assertTrue(mime.hasImage())
            self.assertTrue(mime.hasText())
            self.assertEqual(mime.text(), str(editor.last_path))
            editor.close()

    def test_finish_without_auto_save_emits_image(self):
        from config.config_manager import DEFAULTS
        settings = {**DEFAULTS, "auto_save": False, "auto_copy": False}
        editor = EditorWindow(Image.new("RGB", (25, 20), "#23bc58"), settings)
        images = []
        editor.image_completed.connect(images.append)
        editor.finish()
        self.assertEqual(len(images), 1)
        self.assertEqual(images[0].pixelColor(10, 10).name(), "#23bc58")

    def test_angle_preview_cancel_and_confirm(self):
        from PySide6.QtWidgets import QDialog, QDoubleSpinBox
        from config.config_manager import DEFAULTS
        editor = EditorWindow(Image.new("RGB", (40, 20), "red"), DEFAULTS.copy())
        baseline = editor.canvas.image
        history = editor.canvas.cursor_index

        def cancel(dialog):
            from PySide6.QtWidgets import QDial
            dial = dialog.findChild(QDial)
            self.assertGreaterEqual(dial.minimumWidth(), 190)
            self.assertGreaterEqual(dialog.minimumWidth(), 500)
            dialog.findChild(QDoubleSpinBox).setValue(30)
            self.assertNotEqual(editor.canvas.image.size, baseline.size)
            return QDialog.Rejected

        with patch("editor.editor_window.QDialog.exec", cancel):
            editor.execute("angle")
        self.assertIs(editor.canvas.image, baseline)
        self.assertEqual(editor.canvas.cursor_index, history)

        def accept(dialog):
            dialog.findChild(QDoubleSpinBox).setValue(30)
            return QDialog.Accepted

        with patch("editor.editor_window.QDialog.exec", accept):
            editor.execute("angle")
        self.assertNotEqual(editor.canvas.image.size, baseline.size)
        self.assertEqual(editor.canvas.cursor_index, history + 1)
        editor.close()

    def test_transform_preserves_editable_annotations(self):
        from config.config_manager import DEFAULTS
        editor = EditorWindow(Image.new("RGB", (120, 80), "white"), DEFAULTS.copy())
        item = shape("rect", QPointF(10, 10), QPointF(30, 25), "#ff0000", 3)
        editor.canvas.scene_data.addItem(item)
        editor.canvas.checkpoint()
        editor.execute("left")
        self.assertEqual(len(editor.canvas.annotations()), 1)
        moved = editor.canvas.annotations()[0]
        self.assertNotEqual(moved.sceneBoundingRect().center(), item.sceneBoundingRect().center())
        moved.setSelected(True)
        editor.canvas.set_selected_width(7)
        self.assertEqual(moved.pen().width(), 7)
        editor.execute("undo")
        self.assertEqual(editor.canvas.annotations()[0].pen().width(), 3)
        editor.execute("undo")
        self.assertEqual(editor.canvas.image.size, (120, 80))
        self.assertEqual(len(editor.canvas.annotations()), 1)
        editor.close()

    def test_editor_save_and_sticker(self):
        from config.config_manager import DEFAULTS
        with tempfile.TemporaryDirectory() as folder:
            settings = dict(DEFAULTS, manual_dir=folder, filename="test")
            editor = EditorWindow(Image.new("RGB", (90, 70), "white"), settings)
            path = editor.save()
            self.assertTrue(path.is_file())
            sticker = StickerItem(editor.output_image(), path)
            sticker.resize(45, 35)
            self.assertEqual(sticker.state()["source"], str(path))
            sticker.close()

    def test_editor_save_uses_default_filename_template(self):
        from datetime import datetime
        from config.config_manager import DEFAULTS

        with tempfile.TemporaryDirectory() as folder:
            editor = EditorWindow(Image.new("RGB", (90, 70), "white"),
                                  dict(DEFAULTS, manual_dir=folder))
            with patch("editor.editor_window.datetime") as clock:
                clock.now.return_value = datetime(2026, 9, 27, 14, 5, 6)
                path = editor.save()
            self.assertEqual(path.name, "_20260927_140506.png")
            self.assertTrue(path.is_file())
            editor.close()

    def test_editor_double_click_saves_and_escape_discards(self):
        from config.config_manager import DEFAULTS
        with tempfile.TemporaryDirectory() as folder:
            settings = dict(DEFAULTS, auto_dir=folder, filename="double_click")
            editor = EditorWindow(Image.new("RGB", (90, 70), "white"), settings)
            editor.show()
            self.app.processEvents()
            QTest.mouseDClick(editor.canvas.viewport(), Qt.LeftButton, pos=QPoint(35, 30))
            self.assertFalse(editor.isVisible())
            self.assertEqual(len(list(Path(folder).glob("*.png"))), 1)
            discarded = EditorWindow(Image.new("RGB", (90, 70)), settings)
            discarded.show()
            discarded.canvas.setFocus()
            QTest.keyClick(discarded.canvas.viewport(), Qt.Key_Escape)
            self.assertFalse(discarded.isVisible())
            self.assertEqual(len(list(Path(folder).glob("*.png"))), 1)

    def test_tool_icons_select_annotation_mode(self):
        from PySide6.QtWidgets import QToolButton
        from editor.toolbar_widget import ToolbarWidget
        toolbar = ToolbarWidget()
        actions = []
        toolbar.tool_changed.connect(actions.append)
        toolbar.tool_buttons["arrow"].click()
        self.assertEqual(actions, ["arrow"])
        self.assertTrue(toolbar.tool_buttons["arrow"].isChecked())
        self.assertFalse(toolbar.tool_buttons["select"].isChecked())
        text = toolbar.tool_buttons["text"]
        self.assertFalse(text.icon().isNull())
        self.assertTrue(any(text.icon().pixmap(24, 24).toImage().pixelColor(x, y).alpha() > 0
                            for x in range(24) for y in range(24)))
        for width in (420, 600, 950, 2400):
            toolbar.reflow(width)
            grid = toolbar.tool_grid
            columns = toolbar._layout_mode[1]
            ordered = [toolbar.cursor_switch, toolbar.pen_color, toolbar.options_button]
            ordered.extend(toolbar.tool_buttons[key] for key in "select pen marker rect ellipse text arrow mosaic eraser picker crop".split())
            for index, button in enumerate(ordered):
                row, column = divmod(index, columns)
                self.assertEqual(grid.getItemPosition(grid.indexOf(button)), (row, column, 1, 1))
            self.assertEqual(toolbar.image_grid.indexOf(toolbar.cursor_switch), -1)
            self.assertEqual(toolbar.pen_color.text(), "颜色")
            self.assertNotIn("square", toolbar.tool_buttons)
            self.assertNotIn(toolbar.pen_color, toolbar.options_button.menu().findChildren(type(toolbar.pen_color)))
            self.assertEqual(toolbar.cursor_switch.text(), "显示鼠标")
            self.assertIsNot(toolbar.cursor_switch.parentWidget(), toolbar.options_button.menu())
        paste = next(button for button in toolbar.findChildren(QToolButton) if button.text() == "贴图")
        self.assertFalse(paste.icon().isNull())

    def test_annotation_options_sliders_show_live_values(self):
        from editor.toolbar_widget import ToolbarWidget
        from PySide6.QtWidgets import QWidgetAction, QLabel
        toolbar = ToolbarWidget(settings={"pen_width": 7, "mosaic_size": 18})
        toolbar.tool_buttons["pen"].click()
        panel = next(action.defaultWidget() for action in toolbar.options_button.menu().actions()
                     if isinstance(action, QWidgetAction))
        self.assertGreaterEqual(panel.minimumWidth(), 430)
        self.assertGreaterEqual(toolbar.options_button.menu().sizeHint().width(), 430)
        panel.show()
        self.app.processEvents()
        for row in (0,):
            label = panel.layout().itemAtPosition(row, 0).widget()
            control = panel.layout().itemAtPosition(row, 1).widget()
            self.assertIsInstance(label, QLabel)
            self.assertEqual(label.alignment(), Qt.AlignRight | Qt.AlignVCenter)
            self.assertLess(label.geometry().right(), control.geometry().left())
            self.assertAlmostEqual(label.geometry().center().y(), control.geometry().center().y(), delta=1)
        for slider in (toolbar.pen_width, toolbar.marker_opacity, toolbar.mosaic_size):
            self.assertGreaterEqual(slider.minimumWidth(), 220)
            self.assertGreaterEqual(slider.minimumHeight(), 38)
        self.assertEqual(toolbar.pen_width_label.text(), "7 px")
        self.assertEqual(toolbar.mosaic_size_label.text(), "18 px")
        changes = []
        toolbar.setting_changed.connect(lambda key, value: changes.append((key, value)))
        toolbar.pen_width.setValue(14)
        toolbar.tool_buttons["marker"].click()
        toolbar.marker_opacity.setValue(65)
        toolbar.mosaic_size.setValue(100)
        self.assertEqual(toolbar.pen_width_label.text(),
                 f"{toolbar.tool_widths['marker']} px")
        self.assertEqual(toolbar.marker_opacity_label.text(), "65%")
        self.assertEqual(toolbar.mosaic_size_label.text(), "100 px")
        self.assertIn(("pen_width", 14), changes)
        self.assertIn(("marker_opacity", 65), changes)
        self.assertIn(("mosaic_size", 100), changes)

    def test_toolbar_primary_tool_order_and_annotation_icons(self):
        from editor.toolbar_widget import ToolbarWidget

        toolbar = ToolbarWidget()
        toolbar.reflow(1100)
        first_row = [toolbar.cursor_switch, toolbar.pen_color, toolbar.options_button]
        first_row.extend(toolbar.tool_buttons[key] for key in "select pen marker rect".split())
        positions = [toolbar.tool_grid.getItemPosition(toolbar.tool_grid.indexOf(widget))
                     for widget in first_row]
        self.assertEqual(positions, [(0, column, 1, 1) for column in range(7)])
        self.assertEqual(toolbar.tool_grid.getItemPosition(toolbar.tool_grid.indexOf(toolbar.pen_color)),
                         (0, 1, 1, 1))
        for widget in (toolbar.options_button, toolbar.pen_color, *toolbar.tool_buttons.values()):
            item = toolbar.tool_grid.itemAt(toolbar.tool_grid.indexOf(widget))
            self.assertTrue(item.alignment() & Qt.AlignVCenter, widget.text())

        marker = toolbar.tool_buttons["marker"].icon().pixmap(24, 24).toImage()
        self.assertTrue(any(marker.pixelColor(x, y).alpha() and
                            marker.pixelColor(x, y).red() > 220 and
                            marker.pixelColor(x, y).green() > 160 and
                            marker.pixelColor(x, y).blue() < 100
                            for x in range(marker.width()) for y in range(marker.height())))
        self.assertFalse(toolbar.tool_buttons["arrow"].icon().isNull())
        self.assertFalse(toolbar.tool_buttons["crop"].icon().isNull())

    def test_toolbar_shortcut_tooltips_use_current_bindings(self):
        from config.config_manager import DEFAULTS
        settings = dict(DEFAULTS, hotkeys={**DEFAULTS["hotkeys"], "paste": "ctrl+alt+p"})
        editor = EditorWindow(Image.new("RGB", (30, 20), "white"), settings)
        paste = next(button for button in editor.toolbar.output_buttons if button.text() == "贴图")
        self.assertIn("Ctrl+Alt+P", paste.toolTip())
        editor.toolbar.set_hotkeys({**settings["hotkeys"], "paste": "f9"})
        self.assertIn("F9", paste.toolTip())
        self.assertNotIn("Ctrl+Alt+P", paste.toolTip())
        editor.close()

    def test_crop_style_is_local_and_has_actionable_tooltips(self):
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
        self.assertNotIn("crop_color", editor_settings)
        self.assertNotIn("crop_width", editor_settings)
        self.assertIn("保存为 PNG", next(button for button in editor.toolbar.output_buttons
                                          if button.text() == "保存").toolTip())
        self.assertEqual((editor.toolbar.options_button.width(), editor.toolbar.options_button.height()),
                 (136, 34))
        editor.close()

    def test_tool_widths_are_independent_and_options_follow_tool(self):
        from config.config_manager import DEFAULTS, TOOL_WIDTH_KEYS, validate
        from editor.toolbar_widget import ToolbarWidget
        settings = dict(DEFAULTS)
        toolbar = ToolbarWidget(settings=settings)
        changes = []
        toolbar.setting_changed.connect(lambda key, value: changes.append((key, value)))
        self.assertIn("background: #b44726", toolbar.options_button.styleSheet())
        self.assertIn("QToolButton:disabled", toolbar.options_button.styleSheet())
        for tool, width in (("rect", 8), ("ellipse", 11), ("arrow", 14),
                            ("pen", 17), ("marker", 20), ("eraser", 23)):
            toolbar.tool_buttons[tool].click()
            self.assertTrue(toolbar.options_button.isEnabled())
            self.assertEqual(toolbar.options_button.text(), f"{toolbar.tool_buttons[tool].text()}设置")
            self.assertEqual(toolbar.option_rows[0][0].text(), "直径" if tool == "eraser" else "线宽")
            self.assertFalse(toolbar.pen_width.isHidden())
            toolbar.pen_width.setValue(width)
            self.assertIn((TOOL_WIDTH_KEYS[tool], width), changes)
            settings[TOOL_WIDTH_KEYS[tool]] = width
        for tool, width in (("rect", 8), ("ellipse", 11), ("arrow", 14),
                            ("pen", 17), ("marker", 20), ("eraser", 23)):
            toolbar.tool_buttons[tool].click()
            self.assertEqual(toolbar.pen_width.value(), width)
        toolbar.tool_buttons["marker"].click()
        self.assertFalse(toolbar.marker_opacity.isHidden())
        toolbar.tool_buttons["text"].click()
        self.assertTrue(toolbar.pen_width.isHidden())
        self.assertFalse(toolbar.font.isHidden())
        toolbar.tool_buttons["mosaic"].click()
        self.assertFalse(toolbar.mosaic_size.isHidden())
        self.assertTrue(toolbar.font.isHidden())
        toolbar.tool_buttons["select"].click()
        self.assertFalse(toolbar.options_button.isEnabled())
        self.assertEqual(toolbar.options_button.text(), "更多设置")
        self.assertEqual(validate(settings)["marker_width"], 20)
        for key in TOOL_WIDTH_KEYS.values():
            with self.assertRaises(ValueError):
                validate({key: 51})

    def test_multirow_tool_options_fit_and_labels_are_vertically_centered(self):
        from PySide6.QtWidgets import QWidgetAction
        from editor.toolbar_widget import ToolbarWidget

        toolbar = ToolbarWidget()
        panel = next(action.defaultWidget() for action in toolbar.options_button.menu().actions()
                     if isinstance(action, QWidgetAction))
        panel.show()
        for tool, rows in (("pen", (0,)), ("marker", (0, 1)), ("text", (2, 3, 4)),
               ("mosaic", (5, 6)), ("arrow", (0, 7)), ("eraser", (0,)),
               ("crop", (8, 9))):
            toolbar.tool_buttons[tool].click()
            self.app.processEvents()
            panel.adjustSize()
            panel.layout().activate()
            self.assertGreaterEqual(toolbar.options_button.menu().height(), panel.sizeHint().height())
            expected_labels = {0: "直径" if tool == "eraser" else "线宽",
                               1: "透明度", 2: "字体", 3: "字号", 4: "对齐",
                               5: "效果", 6: "颗粒", 7: "样式",
                               8: "裁剪颜色", 9: "裁剪线宽"}
            for row in rows:
                self.assertEqual(panel.layout().itemAtPosition(row, 0).widget().text(),
                                 expected_labels[row])
                row_widgets = []
                for index in range(panel.layout().count()):
                    item = panel.layout().itemAt(index)
                    item_row, _, _, _ = panel.layout().getItemPosition(index)
                    widget = item.widget()
                    if item_row == row and widget is not None and not widget.isHidden():
                        row_widgets.append(widget)
                self.assertGreaterEqual(len(row_widgets), 2)
                centers = [widget.geometry().center().y() for widget in row_widgets]
                self.assertLessEqual(max(centers) - min(centers), 3,
                                     f"row {row} is not vertically centered")
            if tool == "text":
                self.assertEqual(toolbar.font_size.value(), 18)
            if tool == "arrow":
                self.assertEqual(set(toolbar.choice_buttons["arrow_style"]),
                                 {"filled", "open", "double", "double_filled"})
                self.assertTrue(toolbar.choice_buttons["arrow_style"]["open"].isChecked() is False)
        panel.close()

    def test_canvas_uses_each_tools_width(self):
        from config.config_manager import DEFAULTS, TOOL_WIDTH_KEYS
        from PySide6.QtGui import QColor, QPixmap
        from PySide6.QtWidgets import QGraphicsPixmapItem
        settings = dict(DEFAULTS, rect_width=8, ellipse_width=11, arrow_width=14,
                        pen_width=17, marker_width=20, eraser_width=23)
        canvas = AnnotationCanvas(Image.new("RGB", (120, 100), "white"), settings)
        for tool, key in TOOL_WIDTH_KEYS.items():
            canvas.tool = tool
            if tool != "eraser":
                self.assertEqual(canvas.tool_width(), settings[key])
        canvas.tool = "marker"
        self.assertEqual(canvas.stroke_pen().width(), 100)
        settings["eraser_width"] = 4
        pixmap = QPixmap(80, 80)
        pixmap.fill(QColor("#ff0000"))
        canvas.scene_data.addItem(QGraphicsPixmapItem(pixmap))
        canvas.erase_segment(QPointF(40, 40), QPointF(40, 40))
        image = canvas.render_image()
        self.assertEqual(image.pixelColor(40, 40).name(), "#ffffff")
        self.assertEqual(image.pixelColor(45, 40).name(), "#ff0000")

    def test_color_dialog_chinese_labels_and_common_colors(self):
        from PySide6.QtGui import QColor
        from PySide6.QtWidgets import QColorDialog, QLabel
        from ui.widgets.color_button import COMMON_COLORS, color_dialog
        dialog = color_dialog("#123456", None)
        self.assertEqual(dialog.windowTitle(), "选择颜色")
        self.assertTrue(dialog.testOption(QColorDialog.DontUseNativeDialog))
        self.assertEqual(dialog.currentColor().name(), "#123456")
        labels = [label.text() for label in dialog.findChildren(QLabel)]
        self.assertTrue(any("颜色" in label for label in labels), labels)
        self.assertTrue(all("colors" not in label.lower() for label in labels), labels)
        for index, hex_color in enumerate(COMMON_COLORS):
            self.assertEqual(QColorDialog.customColor(index), QColor(hex_color))
        self.assertTrue({"#ff0000", "#00a000", "#0000ff", "#000000", "#ffffff"}
                        <= set(COMMON_COLORS))
        dialog.close()

    def test_toolbar_group_commands(self):
        from PySide6.QtWidgets import QLabel, QMenu, QToolButton, QWidgetAction
        from editor.toolbar_widget import ToolbarWidget
        toolbar = ToolbarWidget()
        self.assertEqual([section.findChild(QLabel).text() for section in toolbar.sections],
                        ["标注", "编辑", "图像旋转", "输出"])
        self.assertEqual([button.text() for button in toolbar.output_buttons],
                     ["贴图", "保存", "完成", "放弃"])
        commands = []
        toolbar.command.connect(commands.append)
        buttons = toolbar.findChildren(QToolButton)
        self.assertTrue(all(button.text() and not button.icon().isNull() for button in buttons))
        self.assertTrue(all(action.text() and not action.icon().isNull()
                for menu in toolbar.findChildren(QMenu) for action in menu.actions()
                if not isinstance(action, QWidgetAction)))
        options = next(button for button in buttons if button.text() == "更多设置")
        self.assertIsNotNone(options.menu())
        save = next(button for button in buttons if button.text() == "保存")
        save.click()
        next(button for button in toolbar.findChildren(QToolButton)
             if button.text() == "旋转 180°").click()
        next(action for menu in toolbar.findChildren(QMenu) for action in menu.actions()
             if action.text() == "置顶").trigger()
        self.assertEqual(commands, ["save", "half", "top"])

    def test_visible_commands_have_text_and_icons(self):
        from PySide6.QtGui import QImage
        from PySide6.QtWidgets import QPushButton, QMenu, QWidgetAction
        from ui.tray_menu import make_tray_menu
        from sticker.sticker_menu import build_menu

        with tempfile.TemporaryDirectory() as folder:
            settings = SettingsWindow(ConfigManager(Path(folder) / "settings.json"))
            for button in settings.findChildren(QPushButton):
                self.assertTrue(button.text(), button.objectName())
                self.assertFalse(button.icon().isNull(), button.text())
            for index in range(settings.navigation.count()):
                item = settings.navigation.item(index)
                self.assertTrue(item.text())
                self.assertFalse(item.icon().isNull(), item.text())
            tray = make_tray_menu(self.app, lambda: None, lambda: None, lambda: None)
            for action in tray.actions():
                if not action.isSeparator():
                    self.assertTrue(action.text())
                    self.assertFalse(action.icon().isNull(), action.text())

            sticker = StickerItem(QImage(40, 30, QImage.Format_RGB32))
            sticker_menu = build_menu(sticker)
            for menu in (sticker_menu, *sticker_menu.findChildren(QMenu)):
                for action in menu.actions():
                    if not action.isSeparator() and not isinstance(action, QWidgetAction):
                        self.assertTrue(action.text())
                        self.assertFalse(action.icon().isNull(), action.text())
            sticker.close()

    def test_sticker_wasd_and_arrows_move_one_pixel(self):
        from PySide6.QtGui import QImage
        item = StickerItem(QImage(40, 30, QImage.Format_RGB32))
        item.move(50, 50)
        for key, dx, dy in ((Qt.Key_Left, -1, 0), (Qt.Key_A, -1, 0),
                            (Qt.Key_Right, 1, 0), (Qt.Key_D, 1, 0),
                            (Qt.Key_Up, 0, -1), (Qt.Key_W, 0, -1),
                            (Qt.Key_Down, 0, 1), (Qt.Key_S, 0, 1)):
            before = item.pos()
            QTest.keyClick(item, key)
            self.assertEqual(item.pos(), before + QPoint(dx, dy))
        item.toggle_lock()
        QTest.keyClick(item, Qt.Key_D)
        self.assertEqual(item.pos(), QPoint(50, 50))
        item.close()

    def test_sticker_escape_closes_only_current(self):
        from config.config_manager import DEFAULTS
        manager = StickerManager(DEFAULTS)
        image = EditorWindow(Image.new("RGB", (30, 30)), DEFAULTS).output_image()
        first = manager.add(image)
        second = manager.add(image)
        first.setFocus()
        QTest.keyClick(first, Qt.Key_Escape)
        self.app.processEvents()
        self.assertNotIn(first, manager.items)
        self.assertTrue(second.isVisible())
        manager.close_all()

    def test_sticker_session_and_closed_item(self):
        from config.config_manager import DEFAULTS
        with tempfile.TemporaryDirectory() as folder:
            with patch("sticker.sticker_manager.data_dir", return_value=Path(folder)):
                manager = StickerManager(DEFAULTS)
                item = manager.add(EditorWindow(Image.new("RGB", (50, 40)), DEFAULTS).output_image())
                item.move(12, 25)
                manager.persist()
                self.assertTrue((Path(folder) / "sticker_cache" / "sticker_0.png").is_file())
                manager.close_all()
                self.app.processEvents()
                restored = StickerManager(DEFAULTS)
                restored.restore()
                self.assertEqual(len(restored.items), 1)
                self.assertEqual((restored.items[0].x(), restored.items[0].y()), (12, 25))
                restored.items[0].close()
                self.app.processEvents()
                self.assertEqual(len(restored.items), 0)

    def test_history_switch_reuses_one_sticker(self):
        from config.config_manager import DEFAULTS
        with tempfile.TemporaryDirectory() as folder:
            for index in range(3):
                Image.new("RGB", (20 + index, 20), "white").save(Path(folder) / f"{index}.png")
            manager = StickerManager(dict(DEFAULTS, auto_dir=folder))
            manager.cycle(1)
            first = manager.history_sticker
            manager.cycle(1)
            self.assertIs(manager.history_sticker, first)
            self.assertEqual(len(manager.items), 1)
            manager.paste_latest()
            self.assertEqual(len(manager.items), 2)
            manager.close_all()


if __name__ == "__main__":
    unittest.main()