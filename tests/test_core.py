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
        from config.config_manager import DEFAULTS

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "settings.json"
            manager = ConfigManager(path)
            self.assertEqual({DEFAULTS[key] for key in ("pen_width", "rect_width", "ellipse_width",
                                                        "arrow_width", "marker_width")}, {2})
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
                                        "arrow_width": 14, "eraser_width": 9,
                                        "mosaic_size": 23}), encoding="utf-8")
            restored = ConfigManager(path).data
            self.assertEqual((restored["pen_color"], restored["pen_width"],
                              restored["arrow_width"], restored["mosaic_size"]),
                             ("#12ab34", 7, 14, 23))
            self.assertEqual((restored["copy_saved_image"], restored["copy_saved_path"]), (True, False))
            self.assertEqual(ToolbarWidget().tool_widths["arrow"], 2)
            self.assertEqual(ToolbarWidget().tool_widths["eraser"], 30)
            self.assertEqual(restored["eraser_width"], 9)
            for values, expected_width in ((restored, 9), (ConfigManager(Path(folder) / "fresh.json").data, 30)):
                toolbar = ToolbarWidget(settings=values)
                toolbar.tool_buttons["eraser"].click()
                self.assertEqual(toolbar.pen_width.value(), expected_width)
                toolbar.close()

    def test_legacy_completion_settings_are_removed_on_load(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "settings.json"
            path.write_text(json.dumps({"auto_save": False, "auto_copy": False,
                                        "manual_dir": folder}), encoding="utf-8")
            manager = ConfigManager(path)
            self.assertEqual(manager.data["manual_dir"], folder)
            self.assertNotIn("auto_save", manager.data)
            self.assertNotIn("auto_copy", json.loads(path.read_text(encoding="utf-8")))

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

    def test_save_clipboard_options_persist_independently(self):
        with tempfile.TemporaryDirectory() as folder:
            manager = ConfigManager(Path(folder) / "settings.json")
            settings = SettingsWindow(manager)
            save_page = settings.pages.widget(2)
            image = save_page.controls["copy_saved_image"]
            path = save_page.controls["copy_saved_path"]
            self.assertTrue(image.isChecked())
            self.assertFalse(path.isChecked())
            image.setChecked(False)
            path.setChecked(True)
            reloaded = ConfigManager(manager.path).data
            self.assertFalse(reloaded["copy_saved_image"])
            self.assertTrue(reloaded["copy_saved_path"])
            settings.close()

    @patch("PySide6.QtGui.QGuiApplication.clipboard")
    def test_save_clipboard_options_apply_to_both_editors(self, clipboard_source):
        from PySide6.QtCore import QMimeData
        from PySide6.QtGui import QGuiApplication
        from config.config_manager import DEFAULTS
        from screenshot.mask_window import MaskWindow

        clipboard = clipboard_source.return_value
        clipboard.mimeData.return_value = QMimeData()
        def set_text(text):
            payload = QMimeData()
            payload.setText(text)
            clipboard.mimeData.return_value = payload
        clipboard.setText.side_effect = set_text
        clipboard.setMimeData.side_effect = lambda payload: setattr(clipboard.mimeData, "return_value", payload)
        with tempfile.TemporaryDirectory() as folder:
            bounds = {"left": 0, "top": 0, "width": 80, "height": 60}
            for copy_image, copy_path in ((True, False), (False, True), (True, True), (False, False)):
                settings = {**DEFAULTS, "auto_dir": folder, "manual_dir": folder,
                            "magnifier": False, "copy_saved_image": copy_image,
                            "copy_saved_path": copy_path}
                window = EditorWindow(Image.new("RGB", (80, 60), "red"), settings)
                with patch("screenshot.mask_window.visible_windows", return_value=[]):
                    mask = MaskWindow(Image.new("RGB", (80, 60), "blue"), bounds, [bounds], settings)
                mask.selection.rects.append(QRect(5, 5, 40, 30))
                mask.complete()
                for active in (window, mask.session.inline_editor):
                    QGuiApplication.clipboard().setText("previous clipboard")
                    saved_path = active.save(copy_to_clipboard=True)
                    mime = QGuiApplication.clipboard().mimeData()
                    self.assertEqual(mime.hasImage(), copy_image)
                    if copy_path:
                        self.assertEqual(mime.text(), str(saved_path))
                    else:
                        self.assertEqual(mime.text(), "" if copy_image else "previous clipboard")
                    del mime
                    if not copy_image and not copy_path:
                        active.execute("copy")
                        self.assertTrue(QGuiApplication.clipboard().mimeData().hasImage())
                mask.close()
                window.close()
            self.app.processEvents()

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

    def test_general_page_reset_restores_uia_and_hover_interval(self):
        from config.config_manager import DEFAULTS
        from ui.settings_window import SettingsWindow

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "settings.json"
            # 模拟用户把两项改成了非默认值。
            path.write_text(json.dumps({"window_uia_detect": False,
                                        "window_hover_interval": 300}), encoding="utf-8")
            manager = ConfigManager(path)
            self.assertFalse(manager.data["window_uia_detect"])
            self.assertEqual(manager.data["window_hover_interval"], 300)
            settings = SettingsWindow(manager)
            general = settings.pages.widget(0)
            general.reset_page()
            # 单页重置应把本页涉及的项（含本轮新增项）还原为默认值并持久化。
            self.assertTrue(manager.data["window_uia_detect"])
            self.assertEqual(manager.data["window_hover_interval"],
                             DEFAULTS["window_hover_interval"])
            self.assertEqual(ConfigManager(path).data["window_hover_interval"],
                             DEFAULTS["window_hover_interval"])
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
        from PySide6.QtWidgets import QFormLayout, QLabel

        with tempfile.TemporaryDirectory() as folder:
            settings = SettingsWindow(ConfigManager(Path(folder) / "settings.json"))
            page = settings.pages.widget(2)
            form = next(form for form in page.findChildren(QFormLayout)
                        if form.getWidgetPosition(page.controls["filename"])[0] >= 0)
            field_row, _ = form.getWidgetPosition(page.controls["filename"])
            guide = next(label for label in page.findChildren(QLabel)
                         if "%Y 四位年" in label.text())
            guide_row, _ = form.getWidgetPosition(guide)
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
        self.assertIn("双击空白处保存并退出", editor.operation_tips.text())
        self.assertIn("快速选中", editor.operation_tips.text())
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
        self.assertIn("拖拽框选", " ".join(call.args[-1] for call in painter.drawText.call_args_list))
        self.assertGreaterEqual(painter.drawText.call_count, 1)
        self.assertEqual(painter.drawRoundedRect.call_args.args[0].height(),
                 16 * painter.drawText.call_count + 12)
        painter.drawText.reset_mock()
        paint_info(painter, QPoint(20, 20), QRect(10, 10, 40, 30))
        selected_hint = " ".join(call.args[-1] for call in painter.drawText.call_args_list)
        self.assertIn("内部拖动移动", selected_hint)
        self.assertIn("Enter/双击快速进入编辑", selected_hint)
        self.assertIn("四角/边中点缩放", selected_hint)

    def test_capture_operation_tips_use_available_monitor_width(self):
        from screenshot.overlay_info import paint_info

        painter = Mock()
        painter.fontMetrics.return_value.horizontalAdvance.side_effect = lambda text: len(text) * 12
        painter.fontMetrics.return_value.height.return_value = 16
        paint_info(painter, QPoint(20, 20), QRect(10, 10, 40, 30), QRect(0, 0, 1920, 1080))
        wide_rect = painter.drawRoundedRect.call_args.args[0]
        self.assertLessEqual(wide_rect.width(), 1920 - 16)
        self.assertEqual(wide_rect.left(), (1920 - wide_rect.width()) // 2)
        self.assertTrue(all(call.args[1] & Qt.AlignHCenter for call in painter.drawText.call_args_list))
        self.assertFalse(painter.fontMetrics.return_value.elidedText.called)
        painter.drawText.reset_mock()
        paint_info(painter, QPoint(20, 20), QRect(10, 10, 40, 30), QRect(0, 0, 600, 300))
        self.assertLessEqual(painter.drawRoundedRect.call_args.args[0].width(), 584)
        self.assertGreater(painter.drawText.call_count, 1)
        self.assertIn("Esc取消", " ".join(call.args[-1] for call in painter.drawText.call_args_list))

    def test_capture_tips_keep_long_coordinates_visible_in_inline_edit(self):
        from screenshot.overlay_info import paint_info

        painter = Mock()
        painter.fontMetrics.return_value.horizontalAdvance.side_effect = lambda text: len(text) * 12
        painter.fontMetrics.return_value.height.return_value = 16
        paint_info(painter, QPoint(-123456, 987654), QRect(20, 20, 800, 600),
                   QRect(0, 0, 1920, 1080))
        rect = painter.drawRoundedRect.call_args.args[0]
        self.assertLessEqual(rect.width(), 1920 - 16)
        self.assertEqual(rect.left(), (1920 - rect.width()) // 2)
        self.assertIn("-123456, 987654", " ".join(call.args[-1] for call in painter.drawText.call_args_list))
        self.assertFalse(painter.fontMetrics.return_value.elidedText.called)

    def test_capture_operation_tips_are_centered_on_each_monitor(self):
        from PySide6.QtGui import QColor, QImage, QPainter
        from screenshot.overlay_info import paint_info

        image = QImage(2000, 900, QImage.Format_ARGB32)
        image.fill(QColor("white"))
        painter = QPainter(image)
        paint_info(painter, QPoint(0, 0), None, QRect(0, 0, 1000, 900))
        paint_info(painter, QPoint(0, 0), None, QRect(1000, 0, 1000, 900))
        painter.end()
        self.assertGreater(image.pixelColor(4, 20).red(), 200)
        self.assertGreater(image.pixelColor(995, 20).red(), 200)
        self.assertGreater(image.pixelColor(1004, 20).red(), 200)
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
            self.assertEqual(restored["pen_width"], 2)
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
        from core.app_icon import ICON_FILES, app_icon, icon_dir
        from main import Application, drawn_icon, tray_icon

        self.assertTrue(any((icon_dir() / name).is_file() for name in ICON_FILES))
        icon = tray_icon()
        self.assertFalse(icon.isNull())
        self.assertFalse(app_icon().isNull())
        picture = icon.pixmap(64, 64).toImage()
        opaque = sum(1 for x in range(picture.width()) for y in range(picture.height())
                     if picture.pixelColor(x, y).alpha() > 0)
        self.assertGreater(opaque, 50)
        # 图标文件中心留空；内置绘制图标中心有实心圆点，可借此确认用的是文件图标。
        self.assertEqual(picture.pixelColor(picture.width() // 2, picture.height() // 2).alpha(), 0)
        drawn = drawn_icon().pixmap(64, 64).toImage()
        self.assertGreater(drawn.pixelColor(drawn.width() // 2, drawn.height() // 2).alpha(), 0)

        # 未显式设置图标的窗口应继承应用级图标（设置、编辑器等窗口都靠这条链路）。
        from PySide6.QtWidgets import QApplication, QWidget

        application = QApplication.instance()
        application.setWindowIcon(icon)
        window = QWidget()
        self.assertFalse(window.windowIcon().isNull())
        self.assertEqual(window.windowIcon().cacheKey(), icon.cacheKey())
        window.close()

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
        menu = make_tray_menu(self.app, lambda: None, lambda: None, lambda: None,
                              open_sticker=lambda: calls.append("sticker"))
        next(action for action in menu.actions()
             if action.text() == "从文件打开新贴图").trigger()
        self.assertEqual(calls[-1], "sticker")

    def test_sticker_context_menu_shows_restore_hotkey(self):
        from PySide6.QtGui import QImage
        from sticker.sticker_menu import build_menu

        image = QImage(24, 18, QImage.Format_RGB32)
        sticker = StickerItem(image, settings={"hotkeys": {"touch": "ctrl+shift+t"}})
        menu = build_menu(sticker)
        copy_action = next(action for action in menu.actions() if action.text() == "复制图像")
        icon_image = copy_action.icon().pixmap(24, 24).toImage()
        self.assertTrue(any(icon_image.pixelColor(x, y).alpha() > 0
                    for x in range(icon_image.width()) for y in range(icon_image.height())))
        action = next(action for action in menu.actions()
                      if "点击穿透" in action.text())
        self.assertIn("Ctrl+Shift+T", action.text())
        labels = [action.text() for action in build_menu(sticker).actions()]
        self.assertIn("关闭描边", labels)
        self.assertIn("隐藏阴影", labels)
        sticker.toggle_border()
        sticker.toggle_shadow()
        labels = [action.text() for action in build_menu(sticker).actions()]
        self.assertIn("开启描边", labels)
        self.assertIn("显示阴影", labels)
        sticker.close()

    def test_sticker_border_shadow_defaults_settings_and_persistence(self):
        from PySide6.QtGui import QImage, QColor
        from config.config_manager import DEFAULTS, validate
        from ui.settings_sticker import StickerPage

        self.assertTrue(validate({})["sticker_border_enabled"])
        with self.assertRaises(ValueError):
            validate({"sticker_border_color": "red"})
        with self.assertRaises(ValueError):
            validate({"sticker_border_width": 21})
        with self.assertRaises(ValueError):
            validate({"sticker_shadow_strength": 101})

        image = QImage(20, 12, QImage.Format_RGB32)
        image.fill(QColor("#ffffff"))
        settings = dict(DEFAULTS, sticker_border_color="#123456", sticker_border_width=3,
                        sticker_shadow_strength=50)
        sticker = StickerItem(image, settings=settings)
        self.assertGreater(sticker.width(), image.width())
        painted = sticker.grab().toImage()
        self.assertEqual(painted.pixelColor(sticker.padding(), sticker.padding()).name(), "#123456")
        sticker.toggle_border()
        self.assertFalse(sticker.border_enabled)
        self.assertFalse(sticker.state()["border"])
        sticker.toggle_shadow()
        self.assertFalse(sticker.shadow_enabled)
        self.assertFalse(sticker.state()["shadow"])
        sticker.close()

        with tempfile.TemporaryDirectory() as folder:
            manager = ConfigManager(Path(folder) / "settings.json")
            page = StickerPage(manager, manager.save)
            self.assertIn("sticker_border_enabled", page.controls)
            page.controls["sticker_border_width"].setValue(5)
            self.assertEqual(ConfigManager(manager.path).data["sticker_border_width"], 5)
            page.color_buttons["sticker_border_color"].changed("#abcdef")
            self.assertEqual(ConfigManager(manager.path).data["sticker_border_color"], "#abcdef")
            page.close()

    def test_sticker_manager_caches_source_on_add(self):
        from PySide6.QtGui import QImage, QColor
        from config.config_manager import DEFAULTS

        with tempfile.TemporaryDirectory() as folder, \
                patch("sticker.sticker_manager.data_dir", return_value=Path(folder)):
            image = QImage(12, 8, QImage.Format_RGB32)
            image.fill(QColor("#23bc58"))
            manager = StickerManager(DEFAULTS)
            item = manager.add(image)
            self.assertIsNotNone(item.source)
            self.assertTrue(Path(item.source).is_file())
            self.assertIsNone(item.image)
            manager.persist()
            state = json.loads((Path(folder) / "stickers.json").read_text(encoding="utf-8"))[0]
            self.assertEqual(state["source"], item.source)
            item.close()

    def test_annotation_font_size_and_arrow_style_persist(self):
        from config.config_manager import DEFAULTS
        from config.config_manager import TOOL_WIDTH_KEYS

        with tempfile.TemporaryDirectory() as folder:
            manager = ConfigManager(Path(folder) / "settings.json")
            settings = SettingsWindow(manager)
            editor = EditorWindow(Image.new("RGB", (40, 30), "white"), manager.data)
            editor.setting_changed.connect(settings.set_annotation_setting)
            page = settings.pages.widget(3)
            for key in (*TOOL_WIDTH_KEYS.values(), "arrow_style", "rect_style", "ellipse_style",
                        "marker_opacity", "font", "font_size", "text_alignment", "line_spacing",
                        "mosaic_mode", "mosaic_size", "annotation_tool"):
                self.assertIn(key, page.controls)
            self.assertIn("pen_color", page.color_buttons)
            for index, (tool, key) in enumerate(TOOL_WIDTH_KEYS.items(), 7):
                editor.toolbar.tool_buttons[tool].click()
                editor.toolbar.pen_width.setValue(index)
                self.assertEqual(ConfigManager(manager.path).data[key], index)
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

    def test_capture_editors_initial_save_and_close_all(self):
        from types import SimpleNamespace
        from config.config_manager import DEFAULTS
        from main import Application

        with tempfile.TemporaryDirectory() as folder:
            app = Application.__new__(Application)
            app.config = SimpleNamespace(data=dict(DEFAULTS, auto_dir=folder, filename="capture", sound=False,
                                                   bubble=False, capture_notification=False))
            app.editors = []
            app.capture_notice = None
            app.logger = Mock()
            app.settings_window = Mock()
            app.saved = Mock()
            app.completed = Mock()
            app.add_sticker = Mock()
            app.notify = Mock()
            app.edit_images([(Image.new("RGB", (20, 12), "blue"), None),
                             (Image.new("RGB", (10, 8), "red"), None)])

            self.assertEqual(len(app.editors), 2)
            paths = [editor.last_path for editor in app.editors]
            self.assertTrue(all(path and path.is_file() for path in paths))
            self.assertEqual({path.name for path in paths}, {"capture.png", "capture_1.png"})
            app.editors[0].close_all_requested.emit()
            self.app.processEvents()
            self.assertTrue(all(not editor.isVisible() for editor in app.editors))

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
        solid_line = shape("arrow", start, end, "#ff0000", 4, "solid_line")
        open_line = shape("arrow", start, end, "#ff0000", 4, "open_line")
        solid_dash = shape("arrow", start, end, "#ff0000", 4, "solid_dash")
        open_dash = shape("arrow", start, end, "#ff0000", 4, "open_dash")
        self.assertEqual(filled.brush().color().name(), "#ff0000")
        self.assertEqual(open_arrow.brush().style(), Qt.NoBrush)
        self.assertEqual(double.brush().style(), Qt.NoBrush)
        self.assertEqual(double_filled.brush().color().name(), "#ff0000")
        self.assertEqual(double.path(), double_filled.path())
        self.assertEqual(solid_line.path().elementCount(), 2)
        self.assertGreater(open_line.path().elementCount(), solid_line.path().elementCount())
        self.assertEqual(solid_dash.pen().style(), Qt.DashLine)
        self.assertEqual(open_dash.pen().style(), Qt.DashLine)
        self.assertEqual(open_dash.brush().style(), Qt.NoBrush)
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
        self.assertEqual(validate({"arrow_style": "solid_line"})["arrow_style"], "solid_line")
        self.assertEqual(validate({"arrow_style": "open_dash"})["arrow_style"], "open_dash")
        with self.assertRaises(ValueError):
            validate({"arrow_style": "dotted"})

    def test_rect_and_ellipse_support_solid_and_dash_styles(self):
        from PySide6.QtCore import QPointF
        from config.config_manager import validate

        start, end = QPointF(10, 10), QPointF(60, 40)
        rect_solid = shape("rect", start, end, "#ff0000", 3, "solid")
        rect_dash = shape("rect", start, end, "#ff0000", 3, "dash")
        ellipse_dash = shape("ellipse", start, end, "#ff0000", 3, "dash")
        self.assertEqual(rect_solid.pen().style(), Qt.SolidLine)
        self.assertEqual(rect_dash.pen().style(), Qt.DashLine)
        self.assertEqual(ellipse_dash.pen().style(), Qt.DashLine)
        self.assertEqual(validate({"rect_style": "dash"})["rect_style"], "dash")
        self.assertEqual(validate({"ellipse_style": "solid"})["ellipse_style"], "solid")
        with self.assertRaises(ValueError):
            validate({"rect_style": "dot"})

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

    def test_selection_border_moves_without_resizing_or_creating_region(self):
        selection = SelectionRects()
        selection.rects = [QRect(20, 20, 100, 80)]
        border = QPoint(48, 16)
        self.assertTrue(selection.border_at(border))
        selection.begin(border)
        self.assertEqual(selection.dragging, 0)
        selection.update(border + QPoint(17, 9))
        selection.finish()
        self.assertEqual(selection.rects, [QRect(37, 29, 100, 80)])
        self.assertFalse(selection.border_at(QPoint(0, 0)))
        self.assertFalse(selection.border_at(QPoint(37, 29)))

    def test_inline_border_drag_recrops_and_keeps_canvas_inside_unhandled(self):
        from PySide6.QtCore import QEvent, QPointF
        from PySide6.QtGui import QMouseEvent
        from config.config_manager import DEFAULTS
        from screenshot.mask_window import MaskWindow

        with tempfile.TemporaryDirectory() as folder:
            bounds = {"left": 0, "top": 0, "width": 320, "height": 240}
            settings = {**DEFAULTS, "auto_dir": folder, "inline_edit": True, "magnifier": False}
            image = Image.new("RGB", (320, 240), "blue")
            image.paste("red", (35, 35, 155, 125))
            with patch("screenshot.mask_window.visible_windows", return_value=[]):
                mask = MaskWindow(image, bounds, [bounds], settings)
            mask.selection.rects.append(QRect(30, 30, 100, 80))
            mask.complete()
            editor = mask.session.inline_editor
            viewport = editor.canvas.viewport()
            inside = QPoint(50, 50)
            event = QMouseEvent(QEvent.MouseButtonPress, QPointF(inside), QPointF(inside),
                                Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
            self.assertFalse(editor.eventFilter(viewport, event))
            start = QPoint(58, 30)
            QTest.mousePress(mask, Qt.LeftButton, pos=start)
            self.assertEqual(mask.selection.dragging, 0)
            QTest.mouseMove(mask, start + QPoint(15, 8))
            QTest.mouseRelease(mask, Qt.LeftButton, pos=start + QPoint(15, 8))
            self.assertEqual(mask.selection.rects, [QRect(45, 38, 100, 80)])
            self.assertEqual(editor.canvas.image.getpixel((0, 0)), (255, 0, 0))
            self.assertEqual(editor.canvas.geometry(), mask.to_logical_rect(mask.selection.rects[0]).toRect())
            border = viewport.mapFrom(mask, QPoint(73, 39))
            moved = viewport.mapFrom(mask, QPoint(83, 45))
            press = QMouseEvent(QEvent.MouseButtonPress, QPointF(border), QPointF(border),
                                Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
            self.assertTrue(editor.eventFilter(viewport, press))
            move = QMouseEvent(QEvent.MouseMove, QPointF(moved), QPointF(moved),
                               Qt.NoButton, Qt.LeftButton, Qt.NoModifier)
            self.assertTrue(editor.eventFilter(viewport, move))
            release = QMouseEvent(QEvent.MouseButtonRelease, QPointF(moved), QPointF(moved),
                                  Qt.LeftButton, Qt.NoButton, Qt.NoModifier)
            self.assertTrue(editor.eventFilter(viewport, release))
            self.assertEqual(mask.selection.rects, [QRect(55, 44, 100, 80)])
            mask.close()

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
        from PySide6.QtCore import QEvent, QPointF
        from PySide6.QtGui import QMouseEvent
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
            mask.mouseMoveEvent(QMouseEvent(QEvent.MouseMove, QPointF(45, 38), QPointF(45, 38),
                                           Qt.NoButton, Qt.NoButton, Qt.NoModifier))
            self.assertEqual(mask.position, QPoint(45, 38))
            self.assertIsNone(mask.selection.handle_at(mask.position))
            before = QRect(mask.selection.rects[0])
            QTest.keyClick(mask, Qt.Key_Down)
            self.assertEqual(mask.selection.rects[0], before.translated(0, 1))
            self.assertIsNone(mask.selection.nudge_corner)
        mask.close()

    def test_display_mapper_handles_mixed_dpi_screens(self):
        from core.dpi import DisplayMapper

        bounds = {"left": 0, "top": 0, "width": 5760, "height": 2160}
        monitors = [
            {"left": 0, "top": 0, "width": 1920, "height": 1080},
            {"left": 1920, "top": 0, "width": 3840, "height": 2160},
        ]
        mapper = DisplayMapper(bounds, monitors, screen_infos=[
            {"geometry": QRect(0, 0, 1920, 1080), "dpr": 1.0},
            {"geometry": QRect(1920, 0, 3072, 1728), "dpr": 1.25},
        ])

        self.assertEqual(mapper.logical_bounds, QRect(0, 0, 4992, 1728))
        self.assertEqual(mapper.physical_local_to_logical_local(QPoint(2045, 125)), QPoint(2020, 100))
        self.assertEqual(mapper.logical_local_to_physical_local(QPoint(2020, 100)), QPoint(2045, 125))
        self.assertEqual(mapper.physical_local_rect_to_logical_local_rect(QRect(1920, 0, 125, 125)).toRect(),
                         QRect(1920, 0, 100, 100))

    def test_display_mapper_removes_vertical_gap_after_scaled_top_screen(self):
        from core.dpi import DisplayMapper

        bounds = {"left": 0, "top": 0, "width": 3840, "height": 3240}
        monitors = [
            {"left": 0, "top": 0, "width": 3840, "height": 2160},
            {"left": 0, "top": 2160, "width": 1920, "height": 1080},
        ]
        mapper = DisplayMapper(bounds, monitors, screen_infos=[
            {"geometry": QRect(0, 0, 3072, 1728), "dpr": 1.25},
            # 一些混合 DPI 环境会把下屏 top 报成物理 2160，不能让它变成遮罩空白。
            {"geometry": QRect(0, 2160, 1920, 1080), "dpr": 1.0},
        ])

        self.assertEqual(mapper.logical_bounds, QRect(0, 0, 3072, 2808))
        bottom = mapper.monitor_local_rect(monitors[1])
        self.assertEqual(mapper.physical_local_rect_to_logical_local_rect(bottom).toRect(),
                         QRect(0, 1728, 1920, 1080))
        self.assertEqual(mapper.physical_local_to_logical_local(QPoint(100, 2210)), QPoint(100, 1778))
        self.assertEqual(mapper.logical_local_to_physical_local(QPoint(100, 1778)), QPoint(100, 2210))
        self.assertEqual(mapper.physical_local_rect_to_native_global_rect(bottom).toRect(),
                         QRect(0, 2160, 1920, 1080))

    def test_display_mapper_does_not_rescale_when_qt_geometry_is_physical(self):
        from core.dpi import DisplayMapper

        bounds = {"left": 0, "top": 0, "width": 3840, "height": 3240}
        monitors = [
            {"left": 0, "top": 0, "width": 3840, "height": 2160},
            {"left": 0, "top": 2160, "width": 1920, "height": 1080},
        ]
        mapper = DisplayMapper(bounds, monitors, screen_infos=[
            # 在部分 DPI-aware 进程中，Qt geometry 已是物理尺寸；此时不能再除以 1.25。
            {"geometry": QRect(0, 0, 3840, 2160), "dpr": 1.25},
            {"geometry": QRect(0, 2160, 1920, 1080), "dpr": 1.0},
        ])

        self.assertEqual(mapper.logical_bounds, QRect(0, 0, 3840, 3240))
        top = mapper.monitor_local_rect(monitors[0])
        bottom = mapper.monitor_local_rect(monitors[1])
        self.assertEqual(mapper.physical_local_rect_to_logical_local_rect(top).toRect(),
                         QRect(0, 0, 3840, 2160))
        self.assertEqual(mapper.physical_local_rect_to_logical_local_rect(bottom).toRect(),
                         QRect(0, 2160, 1920, 1080))
        self.assertEqual(mapper.physical_local_to_logical_local(QPoint(3000, 1000)), QPoint(3000, 1000))
        self.assertEqual(mapper.logical_local_to_physical_local(QPoint(100, 2300)), QPoint(100, 2300))

    def test_display_mapper_keeps_qt_geometry_size_for_common_dpi_combinations(self):
        from core.dpi import DisplayMapper

        cases = (
            # 两个都 100%。
            ((1920, 1080, 1.0, 1920, 1080), (1920, 1080, 1.0, 1920, 1080)),
            # 一个 125%，一个 100%。
            ((3840, 2160, 1.25, 3072, 1728), (1920, 1080, 1.0, 1920, 1080)),
            # 两个都 125%。
            ((3840, 2160, 1.25, 3072, 1728), (2560, 1440, 1.25, 2048, 1152)),
            # 两个都缩放但比例不同。
            ((3840, 2160, 1.25, 3072, 1728), (2880, 1620, 1.5, 1920, 1080)),
            # Qt 已返回物理尺寸时，即使 DPR 不是 1，也不能再次缩放。
            ((3840, 2160, 1.25, 3840, 2160), (1920, 1080, 1.0, 1920, 1080)),
        )
        for left, right in cases:
            with self.subTest(left=left, right=right):
                left_pw, left_ph, left_dpr, left_lw, left_lh = left
                right_pw, right_ph, right_dpr, right_lw, right_lh = right
                bounds = {"left": 0, "top": 0, "width": left_pw + right_pw,
                          "height": max(left_ph, right_ph)}
                monitors = [
                    {"left": 0, "top": 0, "width": left_pw, "height": left_ph},
                    {"left": left_pw, "top": 0, "width": right_pw, "height": right_ph},
                ]
                mapper = DisplayMapper(bounds, monitors, screen_infos=[
                    {"geometry": QRect(0, 0, left_lw, left_lh), "dpr": left_dpr},
                    {"geometry": QRect(left_lw, 0, right_lw, right_lh), "dpr": right_dpr},
                ])

                self.assertEqual(mapper.physical_local_rect_to_logical_local_rect(
                    mapper.monitor_local_rect(monitors[0])).toRect(), QRect(0, 0, left_lw, left_lh))
                self.assertEqual(mapper.physical_local_rect_to_logical_local_rect(
                    mapper.monitor_local_rect(monitors[1])).toRect(), QRect(left_lw, 0, right_lw, right_lh))

    def test_display_mapper_matches_same_resolution_different_dpi_by_position(self):
        from core.dpi import DisplayMapper

        bounds = {"left": 0, "top": 0, "width": 7680, "height": 2160}
        monitors = [
            {"left": 0, "top": 0, "width": 3840, "height": 2160},
            {"left": 3840, "top": 0, "width": 3840, "height": 2160},
        ]
        mapper = DisplayMapper(bounds, monitors, screen_infos=[
            # 故意把右屏放前面；两个屏物理分辨率相同，只靠尺寸会错配。
            {"geometry": QRect(3072, 0, 2560, 1440), "dpr": 1.5},
            {"geometry": QRect(0, 0, 3072, 1728), "dpr": 1.25},
        ])

        self.assertEqual(mapper.physical_local_rect_to_logical_local_rect(
            mapper.monitor_local_rect(monitors[0])).toRect(), QRect(0, 0, 3072, 1728))
        self.assertEqual(mapper.physical_local_rect_to_logical_local_rect(
            mapper.monitor_local_rect(monitors[1])).toRect(), QRect(3072, 0, 2560, 1440))

    def test_display_mapper_handles_three_and_four_screen_mixed_dpi_layouts(self):
        from core.dpi import DisplayMapper

        bounds = {"left": 0, "top": 0, "width": 6720, "height": 3240}
        monitors = [
            {"left": 0, "top": 0, "width": 3840, "height": 2160},
            {"left": 3840, "top": 0, "width": 2880, "height": 1620},
            {"left": 0, "top": 2160, "width": 1920, "height": 1080},
            {"left": 1920, "top": 2160, "width": 2560, "height": 1440},
        ]
        mapper = DisplayMapper(bounds, monitors, screen_infos=[
            {"geometry": QRect(0, 0, 3072, 1728), "dpr": 1.25},
            {"geometry": QRect(3072, 0, 1920, 1080), "dpr": 1.5},
            {"geometry": QRect(0, 1728, 1920, 1080), "dpr": 1.0},
            {"geometry": QRect(1920, 1728, 2048, 1152), "dpr": 1.25},
        ])

        expected = [QRect(0, 0, 3072, 1728), QRect(3072, 0, 1920, 1080),
                    QRect(0, 1728, 1920, 1080), QRect(1920, 1728, 2048, 1152)]
        self.assertEqual([mapper.physical_local_rect_to_logical_local_rect(
            mapper.monitor_local_rect(monitor)).toRect() for monitor in monitors], expected)
        for monitor in monitors:
            center = QPoint(monitor["left"] - bounds["left"] + monitor["width"] // 2,
                            monitor["top"] - bounds["top"] + monitor["height"] // 2)
            self.assertEqual(mapper.logical_local_to_physical_local(
                mapper.physical_local_to_logical_local(center)), center)

    def test_display_mapper_removes_horizontal_gap_after_scaled_left_screen(self):
        from core.dpi import DisplayMapper

        bounds = {"left": 0, "top": 0, "width": 5760, "height": 2160}
        monitors = [
            {"left": 0, "top": 0, "width": 3840, "height": 2160},
            {"left": 3840, "top": 0, "width": 1920, "height": 1080},
        ]
        mapper = DisplayMapper(bounds, monitors, screen_infos=[
            {"geometry": QRect(0, 0, 3072, 1728), "dpr": 1.25},
            {"geometry": QRect(3840, 0, 1920, 1080), "dpr": 1.0},
        ])

        self.assertEqual(mapper.logical_bounds, QRect(0, 0, 4992, 1728))
        right = mapper.monitor_local_rect(monitors[1])
        self.assertEqual(mapper.physical_local_rect_to_logical_local_rect(right).toRect(),
                         QRect(3072, 0, 1920, 1080))

    def test_mask_uses_logical_geometry_but_crops_physical_pixels(self):
        from config.config_manager import DEFAULTS
        from screenshot.mask_window import MaskWindow

        bounds = {"left": 0, "top": 0, "width": 200, "height": 100}
        screen_infos = [{"geometry": QRect(0, 0, 100, 50), "dpr": 2.0}]
        settings = {**DEFAULTS, "inline_edit": False}
        with patch("screenshot.mask_window.visible_windows", return_value=[]), \
                patch("core.dpi.DisplayMapper.collect_screen_infos", return_value=screen_infos):
            mask = MaskWindow(Image.new("RGB", (200, 100), "white"), bounds, [bounds], settings)
        self.assertEqual(mask.geometry(), QRect(0, 0, 100, 50))
        self.assertEqual(mask.to_physical_point(QPoint(10, 5)), QPoint(20, 10))
        self.assertEqual(mask.to_logical_rect(QRect(20, 10, 40, 20)).toRect(), QRect(10, 5, 20, 10))

        selected = []
        regions = []
        mask.selected.connect(selected.append)
        mask.last_region.connect(regions.append)
        mask.selection.rects.append(QRect(20, 10, 40, 20))
        mask.complete()
        self.assertEqual(selected[0][0][0].size, (40, 20))
        self.assertEqual(regions[0], [20, 10, 40, 20])

    def test_mask_close_releases_images_and_application_reference(self):
        from types import SimpleNamespace
        from config.config_manager import DEFAULTS
        from main import Application

        bounds = {"left": 0, "top": 0, "width": 80, "height": 60}
        frame = Image.new("RGB", (80, 60), "blue")
        app = Application.__new__(Application)
        app.config = SimpleNamespace(data=dict(DEFAULTS, inline_edit=False))
        app.logger = Mock()
        app.remember_region = Mock()
        app.edit_images = Mock()
        app.saved = Mock()
        app.close_all_editors = Mock()
        app.add_sticker = Mock()
        with patch("main.capture", return_value=(frame, bounds, [bounds], frame.copy())), \
                patch("screenshot.mask_window.visible_windows", return_value=[]):
            app.show_mask("capture")
        mask = app.mask
        self.assertIsNotNone(mask.image)
        mask.close()
        self.app.processEvents()
        self.assertIsNone(mask.image)
        self.assertIsNone(mask.alternate)
        self.assertIsNone(mask.preview)
        self.assertIsNone(app.mask)

    def test_mask_vertical_mixed_dpi_layout_has_no_gap_and_crops_bottom_screen(self):
        from config.config_manager import DEFAULTS
        from screenshot.mask_window import MaskWindow

        bounds = {"left": 0, "top": 0, "width": 3840, "height": 3240}
        monitors = [
            {"left": 0, "top": 0, "width": 3840, "height": 2160},
            {"left": 0, "top": 2160, "width": 1920, "height": 1080},
        ]
        settings = {**DEFAULTS, "crosshair": False, "magnifier": False, "mask_opacity": 0,
                    "inline_edit": False}
        screen_infos = [
            {"geometry": QRect(0, 0, 3072, 1728), "dpr": 1.25},
            {"geometry": QRect(0, 2160, 1920, 1080), "dpr": 1.0},
        ]
        with patch("screenshot.mask_window.visible_windows", return_value=[]), \
                patch("core.dpi.DisplayMapper.collect_screen_infos", return_value=screen_infos):
            mask = MaskWindow(Image.new("RGB", (3840, 3240), "white"), bounds, monitors, settings)

        self.assertEqual([view.geometry() for view in mask.session.views],
                         [QRect(0, 0, 3072, 1728), QRect(0, 2160, 1920, 1080)])
        self.assertEqual(mask.session.views[1].logical_window_offset(), QPoint(0, 1728))
        self.assertEqual(mask.session.views[1].to_physical_point(QPoint(0, 0)), QPoint(0, 2160))
        self.assertEqual(mask.to_logical_rect(QRect(0, 2160, 1920, 1080)).toRect(),
                         QRect(0, 1728, 1920, 1080))
        self.assertEqual(mask.session.views[1].to_logical_rect(QRect(0, 2160, 1920, 1080)).toRect(),
                         QRect(0, 0, 1920, 1080))
        selected = []
        regions = []
        mask.selected.connect(selected.append)
        mask.last_region.connect(regions.append)
        mask.selection.rects.append(QRect(100, 2210, 200, 100))
        mask.complete()
        self.assertEqual(selected[0][0][0].size, (200, 100))
        self.assertEqual(regions[0], [100, 2210, 200, 100])

    def test_mask_inline_edit_single_region_saves_and_closes_in_place(self):
        from config.config_manager import DEFAULTS
        from screenshot.mask_window import MaskWindow
        from PySide6.QtWidgets import QToolButton

        with tempfile.TemporaryDirectory() as folder:
            bounds = {"left": 0, "top": 0, "width": 120, "height": 80}
            settings = {**DEFAULTS, "auto_dir": folder, "filename": "inline", "inline_edit": True,
                        "crosshair": False, "magnifier": False, "mask_opacity": 0, "sound": False,
                        "bubble": False}
            with patch("screenshot.mask_window.visible_windows", return_value=[]):
                mask = MaskWindow(Image.new("RGB", (120, 80), "blue"), bounds, [bounds], settings)
            selected = []
            saved = []
            mask.selected.connect(selected.append)
            mask.image_saved.connect(lambda path, image: saved.append((path, image)))
            mask.selection.rects.append(QRect(10, 12, 30, 20))
            mask.complete()
            self.assertFalse(selected)
            self.assertIsNotNone(mask.session.inline_editor)
            self.assertEqual(mask.session.inline_editor.toolbar.objectName(), "inlineCaptureToolbar")
            self.assertIn("background: #f7fbfc", mask.session.inline_editor.toolbar.styleSheet())
            self.assertIn("QCheckBox", mask.session.inline_editor.toolbar.styleSheet())
            self.assertIn("subcontrol-position: center", mask.session.inline_editor.toolbar.styleSheet())
            self.assertNotIn("indicator:checked", mask.session.inline_editor.toolbar.styleSheet())
            self.assertIn("background: #237a8a", mask.session.inline_editor.toolbar.styleSheet())
            visible_buttons = [button for button in mask.session.inline_editor.toolbar.findChildren(QToolButton)
                               if not button.isHidden()]
            self.assertTrue(visible_buttons)
            self.assertTrue(all(button.toolButtonStyle() == Qt.ToolButtonIconOnly for button in visible_buttons))
            self.assertTrue(all(button.width() <= 34 for button in visible_buttons))
            self.assertEqual(mask.session.inline_editor.toolbar.cursor_switch.text(), "")
            self.assertEqual(mask.session.inline_editor.toolbar.options_button.text(), "")
            toolbar = mask.session.inline_editor.toolbar
            self.assertTrue(toolbar.sections[1].isHidden())
            self.assertTrue(toolbar.tool_buttons["crop"].isHidden())
            mask.session.inline_editor.apply_picked_color("#123456")
            self.assertEqual(toolbar.pen_color.text(), "")
            self.assertEqual(toolbar.pen_color.color, "#123456")
            output_positions = [toolbar.output_grid.getItemPosition(toolbar.output_grid.indexOf(button))[:2]
                                for button in toolbar.output_buttons if not button.isHidden()]
            edit_positions = [toolbar.output_grid.getItemPosition(toolbar.output_grid.indexOf(button))[:2]
                              for button in toolbar.edit_buttons]
            self.assertTrue(all(row == 0 for row, column in output_positions + edit_positions))
            self.assertLess(max(column for row, column in output_positions),
                            min(column for row, column in edit_positions))
            path = Path(saved[0][0])
            self.assertTrue(path.is_file())
            self.assertEqual(path.name, "inline.png")
            mask.session.inline_editor.canvas.image = Image.new("RGB", (30, 20), "red")
            mask.session.inline_editor.canvas.refresh_image()
            mask.session.inline_editor.execute("save")
            self.assertFalse(mask.isVisible())
            with Image.open(path) as opened:
                self.assertEqual(opened.getpixel((0, 0))[:3], (255, 0, 0))

    def test_inline_double_click_saves_image_to_clipboard(self):
        from PySide6.QtGui import QGuiApplication
        from config.config_manager import DEFAULTS
        from screenshot.mask_window import MaskWindow

        with tempfile.TemporaryDirectory() as folder:
            bounds = {"left": 0, "top": 0, "width": 160, "height": 120}
            settings = {**DEFAULTS, "auto_dir": folder, "inline_edit": True, "magnifier": False}
            with patch("screenshot.mask_window.visible_windows", return_value=[]):
                mask = MaskWindow(Image.new("RGB", (160, 120), "red"), bounds, [bounds], settings)
            mask.selection.rects.append(QRect(20, 20, 80, 60))
            mask.complete()
            mask.show()
            self.app.processEvents()
            QGuiApplication.clipboard().clear()
            QTest.mouseDClick(mask.session.inline_editor.canvas.viewport(), Qt.LeftButton, pos=QPoint(25, 20))
            self.app.processEvents()
            self.assertFalse(mask.isVisible())
            mime = QGuiApplication.clipboard().mimeData()
            self.assertTrue(mime.hasImage())
            self.assertFalse(mime.hasText())
            self.assertEqual(QGuiApplication.clipboard().image().pixelColor(0, 0).name(), "#ff0000")

    def test_inline_first_save_failure_keeps_editor_open_for_retry(self):
        from config.config_manager import DEFAULTS
        from screenshot.mask_window import MaskWindow

        with tempfile.TemporaryDirectory() as folder:
            blocked = Path(folder) / "blocked"
            blocked.write_text("not a directory")
            bounds = {"left": 0, "top": 0, "width": 120, "height": 80}
            settings = {**DEFAULTS, "auto_dir": str(blocked), "filename": "inline",
                        "inline_edit": True, "magnifier": False}
            with patch("screenshot.mask_window.visible_windows", return_value=[]):
                mask = MaskWindow(Image.new("RGB", (120, 80), "blue"), bounds, [bounds], settings)
            errors = []
            mask.save_failed.connect(errors.append)
            mask.selection.rects.append(QRect(10, 12, 30, 20))
            mask.complete()
            self.assertEqual(len(errors), 1)
            self.assertIn("blocked", errors[0])
            self.assertIsNotNone(mask.session.inline_editor)
            self.assertFalse(mask.session.completing)
            settings["auto_dir"] = folder
            self.assertEqual(mask.session.inline_editor.save(automatic=True).name, "inline.png")
            mask.close()

    def test_mask_inline_edit_keeps_magnifier_settings_and_updates_position(self):
        from PySide6.QtCore import QEvent, QPointF
        from PySide6.QtGui import QCursor, QMouseEvent
        from config.config_manager import DEFAULTS
        from screenshot.mask_window import MaskWindow

        with tempfile.TemporaryDirectory() as folder:
            bounds = {"left": 0, "top": 0, "width": 320, "height": 240}
            settings = {**DEFAULTS, "auto_dir": folder, "filename": "inline", "inline_edit": True,
                        "crosshair": True, "magnifier": True, "mask_opacity": 0, "bubble": False}
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

    def test_inline_options_menu_does_not_raise_magnifier_or_move_capture_cursor(self):
        from PySide6.QtCore import QEvent, QPointF
        from PySide6.QtGui import QCursor, QMouseEvent
        from config.config_manager import DEFAULTS
        from screenshot.mask_window import MaskWindow

        with tempfile.TemporaryDirectory() as folder:
            bounds = {"left": 0, "top": 0, "width": 1200, "height": 900}
            settings = {**DEFAULTS, "auto_dir": folder, "inline_edit": True, "magnifier": True}
            with patch("screenshot.mask_window.visible_windows", return_value=[]), \
                    patch.object(QCursor, "pos", return_value=QPoint(350, 350)):
                mask = MaskWindow(Image.new("RGB", (1200, 900), "blue"), bounds, [bounds], settings)
            mask.selection.rects.append(QRect(40, 40, 100, 80))
            mask.complete()
            mask.show()
            self.app.processEvents()
            editor = mask.session.inline_editor
            editor.toolbar.tool_buttons["arrow"].click()
            overlay = mask.magnifier_overlay
            original_position = QPoint(mask.position)
            self.assertTrue(overlay.isVisible())

            menu = editor.toolbar.options_button.menu()
            menu.popup(mask.mapToGlobal(QPoint(400, 300)))
            self.app.processEvents()
            self.assertTrue(menu.isVisible())
            self.assertFalse(overlay.isVisible())
            panel = menu.actions()[0].defaultWidget()
            mouse = QMouseEvent(QEvent.MouseMove, QPointF(20, 20), QPointF(panel.mapToGlobal(QPoint(20, 20))),
                                Qt.NoButton, Qt.NoButton, Qt.NoModifier)
            editor.eventFilter(panel, mouse)
            self.assertEqual(mask.position, original_position)
            mask.update_all()
            self.assertFalse(overlay.isVisible())
            menu.hide()
            self.app.processEvents()
            self.assertTrue(overlay.isVisible())
            self.assertEqual(mask.position, original_position)
            mask.close()

    def test_inline_options_menu_resizes_on_first_open_for_each_tool(self):
        from config.config_manager import DEFAULTS
        from screenshot.mask_window import MaskWindow

        with tempfile.TemporaryDirectory() as folder:
            bounds = {"left": 0, "top": 0, "width": 1200, "height": 900}
            settings = {**DEFAULTS, "auto_dir": folder, "inline_edit": True, "magnifier": False}
            with patch("screenshot.mask_window.visible_windows", return_value=[]):
                mask = MaskWindow(Image.new("RGB", (1200, 900), "blue"), bounds, [bounds], settings)
            mask.selection.rects.append(QRect(40, 40, 100, 80))
            mask.complete()
            mask.show()
            toolbar = mask.session.inline_editor.toolbar
            menu = toolbar.options_button.menu()
            panel = menu.actions()[0].defaultWidget()
            widths = []
            for tool in ("arrow", "pen", "text", "eraser", "arrow"):
                toolbar.tool_buttons[tool].click()
                menu.popup(mask.mapToGlobal(QPoint(400, 300)))
                self.app.processEvents()
                widths.append(menu.width())
                self.assertGreaterEqual(menu.width(), panel.width(), tool)
                for row in toolbar.option_rows:
                    for widget in row:
                        if widget.isVisible():
                            self.assertLessEqual(widget.geometry().right(), panel.width(), tool)
                menu.hide()
                self.app.processEvents()
            self.assertGreater(widths[0], widths[1], widths)
            self.assertGreater(widths[2], widths[3], widths)
            self.assertEqual(widths[0], widths[4], widths)
            mask.close()

    def test_inline_toolbar_first_hover_does_not_raise_visible_magnifier(self):
        from PySide6.QtCore import QEvent
        from PySide6.QtGui import QMouseEvent
        from PySide6.QtWidgets import QToolButton
        from config.config_manager import DEFAULTS
        from screenshot.mask_window import MaskWindow

        with tempfile.TemporaryDirectory() as folder:
            bounds = {"left": 0, "top": 0, "width": 1200, "height": 900}
            settings = {**DEFAULTS, "auto_dir": folder, "inline_edit": True, "magnifier": True}
            with patch("screenshot.mask_window.visible_windows", return_value=[]):
                mask = MaskWindow(Image.new("RGB", (1200, 900), "blue"), bounds, [bounds], settings)
            mask.selection.rects.append(QRect(40, 40, 300, 200))
            mask.complete()
            mask.show()
            self.app.processEvents()
            button = mask.session.inline_editor.toolbar.tool_buttons["arrow"]
            overlay = mask.magnifier_overlay
            self.assertTrue(button.toolTip())
            self.assertTrue(overlay.isVisible())
            buttons = [widget for widget in mask.session.inline_editor.toolbar.findChildren(QToolButton)
                       if widget.isVisible() and widget.toolTip()]
            self.assertIn(mask.session.inline_editor.toolbar.options_button, buttons)
            with patch.object(overlay, "raise_") as raise_overlay, \
                    patch.object(mask, "mouseMoveEvent", wraps=mask.mouseMoveEvent) as move_mask:
                QTest.mouseMove(button, pos=button.rect().center())
                self.app.processEvents()
                raise_overlay.assert_not_called()
                move_mask.reset_mock()
                for hovered in buttons:
                    center = hovered.rect().center()
                    move = QMouseEvent(QEvent.MouseMove, QPointF(center), QPointF(hovered.mapToGlobal(center)),
                                       Qt.NoButton, Qt.NoButton, Qt.NoModifier)
                    mask.session.inline_editor.eventFilter(hovered, move)
                move_mask.assert_not_called()
            mask.close()

    def test_inline_annotation_settings_survive_config_reload(self):
        from main import Application
        from editor.editor_window import EditorWindow

        with tempfile.TemporaryDirectory() as folder:
            config = ConfigManager(Path(folder) / "settings.json")
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
            with patch("main.capture", return_value=(frame, bounds, [bounds], frame.copy())), \
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
            toolbar.color_changed.emit("#123456")
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
            config.data.update(arrow_width=17, arrow_style="double", annotation_tool="arrow",
                               crop_color="#234567", crop_width=5)
            with patch("main.configure_logging", return_value=app.logger), \
                    patch("main.make_tray_menu", return_value=Mock()):
                app.refresh()
            for active in (window, mask.session.inline_editor):
                self.assertEqual(active.toolbar.tool_widths["arrow"], 17)
                self.assertTrue(active.toolbar.choice_buttons["arrow_style"]["double"].isChecked())
                self.assertEqual(active.toolbar.pen_width.value(), 17)
            self.assertEqual((window.canvas.crop_color, window.canvas.crop_width), ("#234567", 5))
            self.assertEqual(window.toolbar.crop_color.color, "#234567")
            self.assertEqual(window.toolbar.crop_width.value(), 5)
            window.close()
            mask.close()
            app.settings_window.close()

    def test_inline_options_button_opens_after_keyboard_nudge(self):
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QWidget
        from config.config_manager import DEFAULTS
        from screenshot.mask_window import MaskWindow

        with tempfile.TemporaryDirectory() as folder:
            bounds = {"left": 0, "top": 0, "width": 1200, "height": 900}
            settings = {**DEFAULTS, "auto_dir": folder, "inline_edit": True, "magnifier": False}
            with patch("screenshot.mask_window.visible_windows", return_value=[]):
                mask = MaskWindow(Image.new("RGB", (1200, 900), "blue"), bounds, [bounds], settings)
            mask.selection.rects.append(QRect(40, 40, 300, 200))
            mask.complete()
            mask.show()
            editor = mask.session.inline_editor
            editor.canvas.setFocus()
            self.app.processEvents()
            editor.toolbar.tool_buttons["arrow"].click()
            with patch("screenshot.mask_window.QCursor.setPos"):
                QTest.keyClick(editor.canvas, Qt.Key_Right)
            self.assertEqual(mask.selection.rects[0], QRect(41, 40, 300, 200))
            button = editor.toolbar.options_button
            opened = []
            def capture_menu():
                opened.append(button.menu().isVisible())
                button.menu().hide()
            QTimer.singleShot(0, capture_menu)
            QTest.mouseClick(button, Qt.LeftButton)
            self.app.processEvents()
            self.assertEqual(opened, [True])
            corner = mask.selection.rects[0].bottomRight()
            viewport = editor.canvas.viewport()
            with patch.object(mask, "grabMouse", wraps=mask.grabMouse) as grab_mouse, \
                    patch.object(mask, "releaseMouse", wraps=mask.releaseMouse) as release_mouse:
                QTest.mousePress(viewport, Qt.LeftButton, pos=viewport.mapFrom(mask, corner))
                self.assertIsNotNone(mask.selection.resizing)
                grab_mouse.assert_called_once()
                if self.app.platformName() == "windows":
                    self.assertIs(QWidget.mouseGrabber(), mask)
                with patch("screenshot.mask_window.QCursor.setPos"):
                    QTest.keyClick(editor.canvas, Qt.Key_Right)
                release_mouse.assert_called_once()
                if self.app.platformName() == "windows":
                    self.assertIsNot(QWidget.mouseGrabber(), mask)
            self.assertIsNone(mask.selection.resizing)
            QTimer.singleShot(0, capture_menu)
            QTest.mouseClick(button, Qt.LeftButton)
            self.app.processEvents()
            self.assertEqual(opened, [True, True])
            mask.close()

    def test_mask_selection_draws_magnifier_on_mask_surface(self):
        from PySide6.QtGui import QCursor
        from config.config_manager import DEFAULTS
        from screenshot.mask_window import MaskWindow

        bounds = {"left": 0, "top": 0, "width": 320, "height": 240}
        settings = {**DEFAULTS, "magnifier": True, "crosshair": False, "mask_opacity": 0}
        with patch("screenshot.mask_window.visible_windows", return_value=[]), \
                patch.object(QCursor, "pos", return_value=QPoint(70, 50)):
            mask = MaskWindow(Image.new("RGB", (320, 240), "blue"), bounds, [bounds], settings)
        mask.show()
        self.app.processEvents()
        self.assertTrue(mask.magnifier_overlay.isVisible())
        self.assertEqual(mask.magnifier_overlay.grab().toImage().pixelColor(0, 0).name(), "#ffffff")
        self.assertEqual(mask.grab().toImage().pixelColor(88, 68).name(), "#0000ff")
        mask.close()

    def test_inline_resize_keeps_magnifier_above_selection(self):
        from config.config_manager import DEFAULTS
        from screenshot.mask_window import MaskWindow

        with tempfile.TemporaryDirectory() as folder:
            bounds = {"left": 0, "top": 0, "width": 900, "height": 700}
            settings = {**DEFAULTS, "auto_dir": folder, "inline_edit": True,
                        "magnifier": True, "crosshair": False, "mask_opacity": 0}
            with patch("screenshot.mask_window.visible_windows", return_value=[]):
                mask = MaskWindow(Image.new("RGB", (900, 700), "blue"), bounds, [bounds], settings)
            mask.selection.rects.append(QRect(200, 200, 120, 80))
            mask.complete()
            mask.show()
            self.app.processEvents()
            QTest.mousePress(mask, Qt.LeftButton, pos=QPoint(319, 279))
            QTest.mouseMove(mask, QPoint(420, 340))
            self.app.processEvents()
            self.assertFalse(mask.session.inline_editor.toolbar.isVisible())
            self.assertTrue(mask.magnifier_overlay.isVisible())
            self.assertEqual(mask.magnifier_overlay.grab().toImage().pixelColor(0, 0).name(), "#ffffff")
            QTest.mouseRelease(mask, Qt.LeftButton, pos=QPoint(420, 340))
            self.app.processEvents()
            self.assertTrue(mask.magnifier_overlay.isVisible())
            mask.close()

    def test_selection_resize_keeps_magnifier_visible(self):
        from config.config_manager import DEFAULTS
        from screenshot.mask_window import MaskWindow

        bounds = {"left": 0, "top": 0, "width": 900, "height": 700}
        settings = {**DEFAULTS, "magnifier": True, "crosshair": False, "mask_opacity": 0}
        with patch("screenshot.mask_window.visible_windows", return_value=[]):
            mask = MaskWindow(Image.new("RGB", (900, 700), "blue"), bounds, [bounds], settings)
        mask.selection.rects.append(QRect(200, 200, 120, 80))
        mask.show()
        self.app.processEvents()
        QTest.mousePress(mask, Qt.LeftButton, pos=QPoint(319, 279))
        QTest.mouseMove(mask, QPoint(420, 340))
        self.app.processEvents()
        self.assertTrue(mask.magnifier_overlay.isVisible())
        self.assertEqual(mask.magnifier_overlay.grab().toImage().pixelColor(0, 0).name(), "#ffffff")
        QTest.mouseRelease(mask, Qt.LeftButton, pos=QPoint(420, 340))
        mask.close()

    def test_inline_editor_replaces_persisted_crop_tool(self):
        from config.config_manager import DEFAULTS
        from screenshot.mask_window import MaskWindow

        with tempfile.TemporaryDirectory() as folder:
            bounds = {"left": 0, "top": 0, "width": 240, "height": 180}
            settings = {**DEFAULTS, "auto_dir": folder, "inline_edit": True,
                        "annotation_tool": "crop", "magnifier": False}
            with patch("screenshot.mask_window.visible_windows", return_value=[]):
                mask = MaskWindow(Image.new("RGB", (240, 180), "blue"), bounds, [bounds], settings)
            mask.selection.rects.append(QRect(20, 20, 80, 60))
            mask.complete()
            editor = mask.session.inline_editor
            self.assertEqual(editor.canvas.tool, "select")
            self.assertTrue(editor.toolbar.tool_buttons["crop"].isHidden())
            self.assertTrue(editor.toolbar.tool_buttons["select"].isChecked())
            mask.close()

    def test_inline_editor_activates_the_persisted_annotation_tool(self):
        from PySide6.QtWidgets import QGraphicsView
        from config.config_manager import DEFAULTS
        from screenshot.mask_window import MaskWindow

        with tempfile.TemporaryDirectory() as folder:
            bounds = {"left": 0, "top": 0, "width": 240, "height": 180}
            settings = {**DEFAULTS, "auto_dir": folder, "filename": "inline-arrow",
                        "inline_edit": True, "annotation_tool": "arrow", "magnifier": False,
                        "crosshair": False, "mask_opacity": 0, "bubble": False}
            with patch("screenshot.mask_window.visible_windows", return_value=[]):
                mask = MaskWindow(Image.new("RGB", (240, 180), "blue"), bounds, [bounds], settings)
            mask.selection.rects.append(QRect(20, 20, 80, 60))
            mask.complete()
            editor = mask.session.inline_editor

            self.assertTrue(editor.toolbar.tool_buttons["arrow"].isChecked())
            self.assertFalse(editor.toolbar.tool_buttons["select"].isChecked())
            self.assertEqual(editor.canvas.tool, "arrow")
            self.assertEqual(editor.canvas.dragMode(), QGraphicsView.NoDrag)
            mask.close()

    def test_mask_inline_edit_output_actions_close_and_route(self):
        from config.config_manager import DEFAULTS
        from screenshot.mask_window import MaskWindow

        def inline_mask(folder, name):
            bounds = {"left": 0, "top": 0, "width": 80, "height": 60}
            settings = {**DEFAULTS, "auto_dir": folder, "filename": name, "inline_edit": True,
                        "crosshair": False, "magnifier": False, "mask_opacity": 0, "bubble": False}
            with patch("screenshot.mask_window.visible_windows", return_value=[]):
                mask = MaskWindow(Image.new("RGB", (80, 60), "blue"), bounds, [bounds], settings)
            mask.selection.rects.append(QRect(5, 6, 30, 20))
            mask.show()
            self.app.processEvents()
            mask.complete()
            self.app.processEvents()
            return mask

        with tempfile.TemporaryDirectory() as folder:
            paste_mask = inline_mask(folder, "paste")
            pasted = []
            paste_mask.sticker_requested.connect(pasted.append)
            paste_button = next(button for button, action in paste_mask.session.inline_editor.toolbar.command_buttons
                                if action == "paste")
            close_all_button = next(button for button, action in paste_mask.session.inline_editor.toolbar.command_buttons
                                    if action == "close_all_editors")
            self.assertFalse(close_all_button.isVisible())
            paste_button.click()
            self.app.processEvents()
            self.assertEqual(len(pasted), 1)
            self.assertTrue(all(not view.isVisible() for view in paste_mask.session.views))

            save_mask = inline_mask(folder, "save")
            saved = []
            save_mask.image_saved.connect(lambda path, image: saved.append(path))
            editor = save_mask.session.inline_editor
            initial_path = editor.last_path
            editor.canvas.image = Image.new("RGB", (30, 20), "red")
            editor.canvas.refresh_image()
            save_button = next(button for button, action in editor.toolbar.command_buttons if action == "save")
            from PySide6.QtGui import QGuiApplication
            QGuiApplication.clipboard().clear()
            save_button.click()
            self.app.processEvents()
            self.assertIn(str(initial_path), saved)
            self.assertTrue(QGuiApplication.clipboard().mimeData().hasImage())
            self.assertFalse(QGuiApplication.clipboard().mimeData().hasText())
            self.assertEqual(QGuiApplication.clipboard().image().pixelColor(0, 0).name(), "#ff0000")
            self.assertTrue(all(not view.isVisible() for view in save_mask.session.views))
            with Image.open(initial_path) as opened:
                self.assertEqual(opened.getpixel((0, 0))[:3], (255, 0, 0))

            discard_mask = inline_mask(folder, "discard")
            saved = []
            discard_mask.image_saved.connect(lambda path, image: saved.append(path))
            discard_button = next(button for button, action in discard_mask.session.inline_editor.toolbar.command_buttons
                                  if action == "discard")
            discard_button.click()
            self.app.processEvents()
            self.assertFalse(saved)
            self.assertTrue(all(not view.isVisible() for view in discard_mask.session.views))

    def test_inline_repeated_handle_nudges_keep_cursor_on_handle(self):
        from PySide6.QtCore import QSize
        from config.config_manager import DEFAULTS
        from screenshot.mask_window import MaskWindow

        with tempfile.TemporaryDirectory() as folder:
            bounds = {"left": 0, "top": 0, "width": 420, "height": 320}
            settings = {**DEFAULTS, "auto_dir": folder, "inline_edit": True, "magnifier": False}
            with patch("screenshot.mask_window.visible_windows", return_value=[]):
                mask = MaskWindow(Image.new("RGB", (420, 320), "blue"), bounds, [bounds], settings)
            mask.selection.rects.append(QRect(60, 60, 120, 90))
            mask.complete()
            mask.show()
            self.app.processEvents()
            with patch("screenshot.mask_window.QCursor.setPos") as move_pointer:
                for index, invalid_key, valid_key in (
                        (4, Qt.Key_Left, Qt.Key_W), (5, Qt.Key_Up, Qt.Key_D),
                        (6, Qt.Key_Right, Qt.Key_S), (7, Qt.Key_Down, Qt.Key_A),
                        (0, None, Qt.Key_Left), (1, None, Qt.Key_Right),
                        (2, None, Qt.Key_Left), (3, None, Qt.Key_Right)):
                    mask.selection.rects[0] = QRect(60, 60, 120, 90)
                    mask.selection.nudge_corner = None
                    handle = mask.selection.handles_for(mask.selection.rects[0])[index][0]
                    QTest.mousePress(mask, Qt.LeftButton, pos=handle)
                    QTest.mouseRelease(mask, Qt.LeftButton, pos=handle)
                    original = QRect(mask.selection.rects[0])
                    for _ in range(16):
                        QTest.keyClick(mask, invalid_key or valid_key)
                        self.assertIsNotNone(mask.selection.nudge_corner)
                        self.assertEqual(mask.position, mask.selection.nudge_corner[2])
                        self.assertIsNotNone(mask.selection.handle_at(mask.position))
                        move_pointer.assert_called_with(mask.mapToGlobal(mask.to_logical_point(mask.position)))
                    if invalid_key is not None:
                        self.assertEqual(mask.selection.rects[0], original)
                        QTest.keyClick(mask, valid_key)
                        self.assertEqual(mask.selection.rects[0].size() - original.size(),
                                         QSize(1, 0) if index in (5, 7) else QSize(0, 1))
                        self.assertIsNotNone(mask.selection.nudge_corner)
            mask.close()

    def test_inline_keyboard_nudges_handle_or_entire_region(self):
        from PySide6.QtCore import QEvent, QPointF
        from PySide6.QtGui import QMouseEvent
        from config.config_manager import DEFAULTS
        from screenshot.mask_window import MaskWindow

        with tempfile.TemporaryDirectory() as folder:
            bounds = {"left": 0, "top": 0, "width": 320, "height": 240}
            settings = {**DEFAULTS, "auto_dir": folder, "inline_edit": True,
                        "magnifier": False, "cursor": False}
            image = Image.new("RGB", (320, 240), "blue")
            image.paste("red", (44, 44, 160, 130))
            with patch("screenshot.mask_window.visible_windows", return_value=[]):
                mask = MaskWindow(image, bounds, [bounds], settings)
            mask.selection.rects.append(QRect(40, 40, 100, 80))
            mask.complete()
            mask.show()
            editor = mask.session.inline_editor
            editor.canvas.setFocus()
            self.app.processEvents()
            self.assertTrue(editor.canvas.hasFocus())
            corner = mask.selection.rects[0].bottomRight()
            QTest.mousePress(mask, Qt.LeftButton, pos=corner)
            QTest.mouseRelease(mask, Qt.LeftButton, pos=corner)
            with patch("screenshot.mask_window.QCursor.setPos"):
                QTest.keyClick(mask, Qt.Key_D)
                self.assertEqual(mask.selection.rects[0], QRect(40, 40, 101, 80))
                editor.canvas.setFocus()
                self.app.processEvents()
                self.assertTrue(editor.canvas.hasFocus())
                QTest.keyClick(editor.canvas.viewport(), Qt.Key_D)
                self.assertEqual(mask.selection.rects[0], QRect(40, 40, 102, 80))
                self.assertIsNotNone(mask.selection.nudge_corner)
                self.assertTrue(editor.canvas.hasFocus())
                inside = editor.canvas.viewport().mapFrom(mask, QPoint(85, 75))
                press = QMouseEvent(QEvent.MouseButtonPress, QPointF(inside), QPointF(inside),
                                    Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
                self.assertFalse(editor.eventFilter(editor.canvas.viewport(), press))
                self.assertEqual(mask.position, QPoint(85, 75))
                for key, delta in ((Qt.Key_A, QPoint(-1, 0)), (Qt.Key_Right, QPoint(1, 0)),
                                   (Qt.Key_W, QPoint(0, -1)), (Qt.Key_Down, QPoint(0, 1)),
                                   (Qt.Key_S, QPoint(0, 1)), (Qt.Key_Up, QPoint(0, -1)),
                                   (Qt.Key_D, QPoint(1, 0)), (Qt.Key_Left, QPoint(-1, 0))):
                    before = QRect(mask.selection.rects[0])
                    QTest.keyClick(editor.canvas, key)
                    self.assertEqual(mask.selection.rects[0], before.translated(delta))
                    self.assertIsNone(mask.selection.nudge_corner)
                    self.assertEqual(editor.canvas.geometry(), mask.to_logical_rect(mask.selection.rects[0]).toRect())
                    self.assertTrue(editor.canvas.hasFocus())
                self.assertEqual(editor.canvas.image.getpixel((5, 5)), (255, 0, 0))
                before = QRect(mask.selection.rects[0])
                QTest.keyClick(editor.canvas, Qt.Key_D, Qt.ControlModifier)
                self.assertEqual(mask.selection.rects[0], before)
            mask.close()

    def test_mask_inline_edit_locks_selection_layer(self):
        from config.config_manager import DEFAULTS
        from screenshot.mask_window import MaskWindow

        with tempfile.TemporaryDirectory() as folder:
            bounds = {"left": 0, "top": 0, "width": 120, "height": 80}
            settings = {**DEFAULTS, "auto_dir": folder, "filename": "inline", "inline_edit": True,
                        "crosshair": False, "magnifier": False, "mask_opacity": 0, "bubble": False}
            with patch("screenshot.mask_window.visible_windows", return_value=[]):
                mask = MaskWindow(Image.new("RGB", (120, 80), "blue"), bounds, [bounds], settings)
            mask.selection.rects.append(QRect(10, 10, 30, 20))
            mask.complete()
            self.assertIsNotNone(mask.session.inline_editor)
            QTest.mousePress(mask, Qt.LeftButton, pos=QPoint(60, 45))
            QTest.mouseMove(mask, QPoint(90, 70))
            QTest.mouseRelease(mask, Qt.LeftButton, pos=QPoint(90, 70))
            self.assertEqual(mask.selection.rects, [QRect(10, 10, 30, 20)])
            self.assertIsNone(mask.selection.active)

            QTest.mousePress(mask, Qt.LeftButton, pos=QPoint(39, 29))
            self.assertFalse(mask.session.inline_editor.canvas.isVisible())
            self.assertFalse(mask.session.inline_editor.toolbar.isVisible())
            QTest.mouseMove(mask, QPoint(50, 40))
            QTest.mouseRelease(mask, Qt.LeftButton, pos=QPoint(50, 40))
            self.assertEqual(mask.selection.rects[0], QRect(QPoint(10, 10), QPoint(50, 40)).normalized())
            self.assertEqual(mask.session.inline_editor.rect, mask.selection.rects[0])
            self.assertEqual(mask.session.inline_editor.canvas.image.size, (41, 31))
            self.assertFalse(mask.session.inline_editor.canvas.isHidden())
            self.assertFalse(mask.session.inline_editor.toolbar.isHidden())

    def test_mask_inline_cursor_switch_and_toolbar_repositions_after_resize(self):
        from PySide6.QtWidgets import QStyle, QStyleOptionButton
        from config.config_manager import DEFAULTS
        from screenshot.mask_window import MaskWindow

        with tempfile.TemporaryDirectory() as folder:
            bounds = {"left": 0, "top": 0, "width": 1200, "height": 900}
            settings = {**DEFAULTS, "auto_dir": folder, "filename": "inline", "inline_edit": True,
                        "cursor": False, "crosshair": False, "magnifier": False,
                        "mask_opacity": 0, "bubble": False}
            base = Image.new("RGB", (1200, 900), "blue")
            cursor = Image.new("RGB", (1200, 900), "red")
            with patch("screenshot.mask_window.visible_windows", return_value=[]):
                mask = MaskWindow(base, bounds, [bounds], settings, alternate=cursor)
            mask.selection.rects.append(QRect(900, 500, 120, 80))
            mask.complete()
            editor = mask.session.inline_editor
            self.assertEqual(editor.canvas.image.getpixel((0, 0)), (0, 0, 255))
            cursor_switch = editor.toolbar.cursor_switch
            option = QStyleOptionButton()
            option.initFrom(cursor_switch)
            indicator = cursor_switch.style().subElementRect(QStyle.SE_CheckBoxIndicator,
                                                              option, cursor_switch)
            self.assertLessEqual(abs(indicator.center().x() - cursor_switch.rect().center().x()), 1)
            self.assertLessEqual(abs(indicator.center().y() - cursor_switch.rect().center().y()), 1)
            unchecked = cursor_switch.grab().toImage()
            cursor_switch.setChecked(True)
            checked = cursor_switch.grab().toImage()
            self.assertTrue(any(unchecked.pixelColor(x, y) != checked.pixelColor(x, y)
                                for y in range(indicator.top(), indicator.bottom() + 1)
                                for x in range(indicator.left(), indicator.right() + 1)))
            self.assertEqual(editor.canvas.image.getpixel((0, 0)), (255, 0, 0))
            self.assertEqual(cursor_switch.text(), "")
            self.assertTrue(cursor_switch.isChecked())

            old_toolbar = QRect(editor.toolbar.geometry())
            QTest.mousePress(mask, Qt.LeftButton, pos=QPoint(1019, 579))
            QTest.mouseMove(mask, QPoint(1120, 650))
            QTest.mouseRelease(mask, Qt.LeftButton, pos=QPoint(1120, 650))
            new_toolbar = editor.toolbar.geometry()
            self.assertNotEqual(new_toolbar, old_toolbar)
            self.assertLessEqual(abs(new_toolbar.right() - editor.canvas.geometry().right()), 24,
                                 (new_toolbar, editor.canvas.geometry(), editor.toolbar.sizeHint(),
                                  [(section.sizeHint(), section.minimumWidth(), section.geometry())
                                   for section in editor.toolbar.sections],
                                  editor.toolbar.tool_grid.sizeHint(), editor.toolbar.output_grid.sizeHint()))
            self.assertEqual(editor.canvas.image.getpixel((0, 0)), (255, 0, 0))
            for button in (editor.toolbar.tool_buttons["select"], editor.toolbar.tool_buttons["pen"],
                           *editor.toolbar.output_buttons[:4], *editor.toolbar.edit_buttons):
                self.assertEqual(button.width(), 30)
                self.assertEqual(button.height(), 30)
            output = editor.toolbar.output_grid
            first_row = [output.itemAtPosition(0, column).widget()
                         for column in range(output.columnCount()) if output.itemAtPosition(0, column)]
            self.assertEqual(len({button.y() for button in first_row}), 1)
            tools = editor.toolbar.tool_grid
            for column in range(tools.columnCount()):
                widgets = [tools.itemAtPosition(row, column).widget()
                           for row in range(tools.rowCount()) if tools.itemAtPosition(row, column)]
                self.assertEqual(len({button.x() for button in widgets}), 1)

    def test_inline_tool_rows_align_with_toolbar_side(self):
        from config.config_manager import DEFAULTS
        from screenshot.mask_window import MaskWindow

        with tempfile.TemporaryDirectory() as folder:
            bounds = {"left": 0, "top": 0, "width": 1200, "height": 900}
            settings = {**DEFAULTS, "auto_dir": folder, "inline_edit": True, "magnifier": False}
            with patch("screenshot.mask_window.visible_windows", return_value=[]):
                mask = MaskWindow(Image.new("RGB", (1200, 900), "blue"), bounds, [bounds], settings)
            mask.selection.rects.append(QRect(40, 400, 80, 80))
            mask.complete()
            mask.show()
            self.app.processEvents()
            editor = mask.session.inline_editor
            for x, width, align_right in ((40, 80, False), (900, 80, True),
                                          (40, 320, False), (850, 320, True),
                                          (40, 80, False), (900, 80, True)):
                editor.rect = QRect(x, 400, width, 80)
                editor.position_widgets()
                self.app.processEvents()
                self.assertGreater(editor.toolbar.height(), 60)
                self.assertLessEqual(editor.toolbar.height(), 110,
                                     (editor.toolbar.geometry(), editor.toolbar.sizeHint()))
                grid = editor.toolbar.tool_grid
                tools = [grid.itemAt(index).widget() for index in range(grid.count())
                         if not grid.itemAt(index).widget().isHidden()]
                self.assertTrue(all(grid.getItemPosition(grid.indexOf(button))[0] == 0
                                    for button in tools))
                self.assertEqual(len({button.mapTo(editor.toolbar, QPoint(0, 0)).y()
                                      for button in tools}), 1)
                drawing, output = editor.toolbar.sections[0], editor.toolbar.sections[3]
                self.assertLess(drawing.geometry().bottom(), output.geometry().top())
                self.assertEqual(drawing.geometry().right() if align_right else drawing.geometry().left(),
                                 output.geometry().right() if align_right else output.geometry().left(),
                                 (editor.toolbar.geometry(), drawing.geometry(), output.geometry()))
                output_buttons = [button for button in editor.toolbar.output_buttons
                                  if not button.isHidden()]
                buttons = tools + output_buttons + editor.toolbar.edit_buttons
                self.assertEqual(len({button.mapTo(editor.toolbar, QPoint(0, 0)).y()
                                      for button in output_buttons + editor.toolbar.edit_buttons}), 1)
                self.assertLess(max(button.mapTo(editor.toolbar, QPoint(0, button.height())).y()
                                    for button in tools),
                                min(button.mapTo(editor.toolbar, QPoint(0, 0)).y()
                                    for button in output_buttons + editor.toolbar.edit_buttons))
                self.assertTrue(all(0 <= button.mapTo(editor.toolbar, QPoint(0, 0)).x()
                                    and button.mapTo(editor.toolbar, QPoint(button.width(), 0)).x()
                                    <= editor.toolbar.width() for button in buttons),
                                (editor.toolbar.geometry(), drawing.geometry(), output.geometry()))
                tool_edge = (max(button.mapTo(editor.toolbar, QPoint(button.width(), 0)).x()
                                 for button in tools) if align_right else
                             min(button.mapTo(editor.toolbar, QPoint(0, 0)).x() for button in tools))
                output_edge = (max(button.mapTo(editor.toolbar, QPoint(button.width(), 0)).x()
                                   for button in output_buttons + editor.toolbar.edit_buttons)
                               if align_right else
                               min(button.mapTo(editor.toolbar, QPoint(0, 0)).x()
                                   for button in output_buttons + editor.toolbar.edit_buttons))
                self.assertEqual(tool_edge, output_edge,
                                 (editor.toolbar.geometry(), drawing.geometry(), output.geometry(),
                                  tools[0].mapTo(editor.toolbar, QPoint(0, 0)),
                                                                    output_buttons[0].mapTo(editor.toolbar, QPoint(0, 0)),
                                                                    [(button.objectName(), button.mapTo(editor.toolbar, QPoint(0, 0)).x(),
                                                                        button.width(), button.isHidden()) for button in tools]))
            mask.close()

    def test_mask_inline_toolbar_avoids_selection_when_space_allows(self):
        from config.config_manager import DEFAULTS
        from screenshot.mask_window import MaskWindow

        with tempfile.TemporaryDirectory() as folder:
            bounds = {"left": 0, "top": 0, "width": 1200, "height": 900}
            settings = {**DEFAULTS, "auto_dir": folder, "filename": "inline", "inline_edit": True,
                        "crosshair": False, "magnifier": False, "mask_opacity": 0, "bubble": False}
            with patch("screenshot.mask_window.visible_windows", return_value=[]):
                mask = MaskWindow(Image.new("RGB", (1200, 900), "blue"), bounds, [bounds], settings)
            mask.selection.rects.append(QRect(260, 720, 160, 80))
            mask.complete()
            toolbar = mask.session.inline_editor.toolbar.geometry()
            selection = mask.session.inline_editor.canvas.geometry().adjusted(-16, -16, 16, 16)
            self.assertTrue(mask.rect().contains(toolbar))
            self.assertFalse(toolbar.intersects(selection))

    def test_mask_creates_one_window_per_monitor_and_secondary_escape_closes_all(self):
        from config.config_manager import DEFAULTS
        from screenshot.mask_window import MaskWindow

        bounds = {"left": 0, "top": 0, "width": 6720, "height": 3240}
        monitors = [
            {"left": 0, "top": 0, "width": 3840, "height": 2160},
            {"left": 3840, "top": 0, "width": 2880, "height": 1620},
            {"left": 0, "top": 2160, "width": 1920, "height": 1080},
            {"left": 1920, "top": 2160, "width": 2560, "height": 1440},
        ]
        settings = {**DEFAULTS, "crosshair": False, "magnifier": False, "mask_opacity": 0}
        screen_infos = [
            {"geometry": QRect(0, 0, 3072, 1728), "dpr": 1.25},
            {"geometry": QRect(3072, 0, 1920, 1080), "dpr": 1.5},
            {"geometry": QRect(0, 1728, 1920, 1080), "dpr": 1.0},
            {"geometry": QRect(1920, 1728, 2048, 1152), "dpr": 1.25},
        ]
        with patch("screenshot.mask_window.visible_windows", return_value=[]), \
                patch("core.dpi.DisplayMapper.collect_screen_infos", return_value=screen_infos):
            mask = MaskWindow(Image.new("RGB", (6720, 3240), "white"), bounds, monitors, settings)

        self.assertEqual(len(mask.session.views), 4)
        self.assertEqual([view.geometry() for view in mask.session.views],
                         [screen["geometry"] for screen in screen_infos])
        mask.show()
        self.app.processEvents()
        QTest.keyClick(mask.session.views[2], Qt.Key_Escape)
        self.app.processEvents()
        self.assertTrue(all(not view.isVisible() for view in mask.session.views))

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

    def test_inline_escape_from_canvas_or_toolbar_closes_capture(self):
        from config.config_manager import DEFAULTS
        from screenshot.mask_window import MaskWindow

        with tempfile.TemporaryDirectory() as folder:
            bounds = {"left": 0, "top": 0, "width": 500, "height": 400}
            settings = {**DEFAULTS, "auto_dir": folder, "inline_edit": True,
                        "magnifier": False}
            for focus_toolbar in (False, True):
                with patch("screenshot.mask_window.visible_windows", return_value=[]):
                    mask = MaskWindow(Image.new("RGB", (500, 400), "blue"), bounds, [bounds], settings)
                mask.selection.rects.append(QRect(80, 80, 120, 90))
                mask.complete()
                mask.show()
                self.app.processEvents()
                editor = mask.session.inline_editor
                target = editor.toolbar.options_button if focus_toolbar else editor.canvas.viewport()
                QTest.keyClick(target, Qt.Key_Escape)
                self.app.processEvents()
                self.assertFalse(mask.isVisible())
                self.assertIsNone(mask.session.inline_editor)

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
        canvas = AnnotationCanvas(Image.new("RGB", (120, 100), "white"), dict(DEFAULTS, eraser_width=3))
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

    def test_capture_delay_setting_adds_wait_before_capture(self):
        import logging
        from main import Application

        app = Application.__new__(Application)
        app.mask = None
        app.logger = logging.getLogger("screensnap")
        app.config = Mock(data={"last_capture_rect": [1, 2, 3, 4], "capture_delay": 800})
        app.show_mask = Mock()
        with patch("main.QTimer.singleShot") as schedule:
            app.start_capture("capture")
            self.assertEqual(schedule.call_args.args[0], 800)
            # 托盘菜单本来就有 150 毫秒等待，用户延迟在此基础上累加。
            app.start_capture("capture", from_tray=True)
            self.assertEqual(schedule.call_args.args[0], 950)

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
            manager.data["inline_edit"] = False
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
            settings = {**DEFAULTS, "manual_dir": folder, "open_dir": False,
                        "copy_saved_image": True, "copy_saved_path": True}
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

    def test_editor_saves_over_existing_capture_path(self):
        from config.config_manager import DEFAULTS

        with tempfile.TemporaryDirectory() as folder:
            settings = dict(DEFAULTS, auto_dir=folder, manual_dir=folder, filename="capture", open_dir=False)
            editor = EditorWindow(Image.new("RGB", (20, 12), "blue"), settings)
            first = editor.save(automatic=True)
            first_mtime = first.stat().st_mtime_ns
            editor.canvas.image = Image.new("RGB", (20, 12), "red")
            editor.canvas.refresh_image()
            second = editor.save(copy_to_clipboard=True)
            self.assertEqual(second, first)
            self.assertEqual(list(Path(folder).glob("*.png")), [first])
            self.assertGreaterEqual(first.stat().st_mtime_ns, first_mtime)
            with Image.open(first) as saved:
                self.assertEqual(saved.getpixel((0, 0))[:3], (255, 0, 0))
            editor.canvas.image = Image.new("RGB", (20, 12), "#23bc58")
            editor.canvas.refresh_image()
            editor.execute("save")
            self.assertFalse(editor.isVisible())
            with Image.open(first) as saved:
                self.assertEqual(saved.getpixel((0, 0))[:3], (35, 188, 88))

    def test_manual_save_writes_image(self):
        from config.config_manager import DEFAULTS
        with tempfile.TemporaryDirectory() as folder:
            settings = {**DEFAULTS, "manual_dir": folder, "filename": "manual_save"}
            editor = EditorWindow(Image.new("RGB", (25, 20), "#23bc58"), settings)
            editor.execute("save")
            self.assertFalse(editor.isVisible())
            with Image.open(Path(folder) / "manual_save.png") as saved:
                self.assertEqual(saved.getpixel((10, 10))[:3], (35, 188, 88))

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
        from PySide6.QtGui import QGuiApplication
        with tempfile.TemporaryDirectory() as folder:
            settings = dict(DEFAULTS, manual_dir=folder, filename="double_click")
            editor = EditorWindow(Image.new("RGB", (90, 70), "white"), settings)
            editor.show()
            self.app.processEvents()
            QGuiApplication.clipboard().clear()
            QTest.mouseDClick(editor.canvas.viewport(), Qt.LeftButton, pos=QPoint(35, 30))
            self.assertFalse(editor.isVisible())
            self.assertEqual(len(list(Path(folder).glob("*.png"))), 1)
            self.assertTrue(QGuiApplication.clipboard().mimeData().hasImage())
            self.assertFalse(QGuiApplication.clipboard().mimeData().hasText())
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
        self.assertGreaterEqual(panel.minimumWidth(), 340)
        self.assertLessEqual(panel.minimumWidth(), 380)
        self.assertGreaterEqual(toolbar.options_button.menu().sizeHint().width(), 320)
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
        text = toolbar.tool_buttons["text"].icon().pixmap(24, 24).toImage()
        self.assertGreater(text.pixelColor(7, 5).alpha(), 0)
        self.assertGreater(text.pixelColor(12, 17).alpha(), 0)
        self.assertEqual(text.pixelColor(0, 0).alpha(), 0)
        self.assertFalse(toolbar.tool_buttons["arrow"].icon().isNull())
        self.assertFalse(toolbar.tool_buttons["crop"].icon().isNull())

    def test_toolbar_shortcut_tooltips_use_current_bindings(self):
        from config.config_manager import DEFAULTS
        settings = dict(DEFAULTS, hotkeys={**DEFAULTS["hotkeys"], "paste": "ctrl+alt+p"})
        editor = EditorWindow(Image.new("RGB", (30, 20), "white"), settings)
        paste = next(button for button in editor.toolbar.output_buttons if button.text() == "贴图")
        self.assertTrue(paste.toolTip().startswith("<b>贴图</b><br>"))
        self.assertIn("Ctrl+Alt+P", paste.toolTip())
        editor.toolbar.set_hotkeys({**settings["hotkeys"], "paste": "f9"})
        self.assertTrue(paste.toolTip().startswith("<b>贴图</b><br>"))
        self.assertIn("F9", paste.toolTip())
        self.assertNotIn("Ctrl+Alt+P", paste.toolTip())
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
            reloaded = ConfigManager(manager.path).data
            self.assertEqual((reloaded["crop_width"], reloaded["crop_color"]), (7, "#123abc"))
            page = settings.pages.widget(3)
            self.assertEqual(page.controls["crop_width"].value(), 7)
            self.assertEqual(page.color_buttons["crop_color"].color, "#123abc")
            reopened = EditorWindow(Image.new("RGB", (40, 30), "white"), reloaded)
            self.assertEqual((reopened.canvas.crop_width, reopened.canvas.crop_color), (7, "#123abc"))
            self.assertEqual(reopened.toolbar.crop_width.value(), 7)
            self.assertEqual(reopened.toolbar.crop_color.color, "#123abc")
            reopened.close()
            editor.close()
            settings.close()

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
            if tool == "rect":
                self.assertFalse(toolbar.rect_style.isHidden())
            if tool == "ellipse":
                self.assertFalse(toolbar.ellipse_style.isHidden())
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

    def test_options_menu_resizes_on_first_open_after_tool_change(self):
        from editor.toolbar_widget import ToolbarWidget

        toolbar = ToolbarWidget()
        toolbar.show()
        menu = toolbar.options_button.menu()
        panel = menu.actions()[0].defaultWidget()
        widths = []
        for tool in ("arrow", "pen", "text", "eraser", "arrow"):
            toolbar.tool_buttons[tool].click()
            menu.popup(toolbar.mapToGlobal(QPoint(100, 100)))
            self.app.processEvents()
            widths.append(menu.width())
            self.assertGreaterEqual(menu.width(), panel.width(), (tool, menu.size(), panel.size()))
            self.assertGreaterEqual(panel.width(), toolbar.option_panel_width(tool), tool)
            for row in toolbar.option_rows:
                for widget in row:
                    if widget.isVisible():
                        self.assertLessEqual(widget.geometry().right(), panel.width(),
                                             (tool, widget.geometry(), panel.size()))
            menu.hide()
            self.app.processEvents()
        self.assertGreater(widths[0], widths[1], widths)
        self.assertGreater(widths[2], widths[3], widths)
        self.assertEqual(widths[0], widths[4], widths)
        toolbar.close()

    def test_multirow_tool_options_fit_and_labels_are_vertically_centered(self):
        from PySide6.QtWidgets import QWidgetAction
        from editor.toolbar_widget import ToolbarWidget

        toolbar = ToolbarWidget()
        panel = next(action.defaultWidget() for action in toolbar.options_button.menu().actions()
                     if isinstance(action, QWidgetAction))
        panel.show()
        for tool, rows in (("pen", (0,)), ("marker", (0, 1)), ("text", (2, 3, 4)),
               ("mosaic", (5, 6)), ("arrow", (0, 7)), ("rect", (0, 10)),
               ("ellipse", (0, 11)), ("eraser", (0,)),
               ("crop", (8, 9))):
            toolbar.tool_buttons[tool].click()
            self.app.processEvents()
            panel.adjustSize()
            panel.layout().activate()
            self.assertGreaterEqual(toolbar.options_button.menu().height(), panel.sizeHint().height())
            expected_labels = {0: "直径" if tool == "eraser" else "线宽",
                               1: "透明度", 2: "字体", 3: "字号", 4: "对齐",
                               5: "效果", 6: "颗粒", 7: "样式",
                               8: "裁剪颜色", 9: "裁剪线宽",
                               10: "矩形线型", 11: "椭圆线型"}
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
                                  {"filled", "open", "double", "double_filled",
                                   "solid_line", "open_line", "solid_dash", "open_dash"})
                self.assertTrue(toolbar.choice_buttons["arrow_style"]["open"].isChecked() is False)
                self.assertGreaterEqual(panel.minimumWidth(), 760)
            if tool in ("rect", "ellipse"):
                self.assertLessEqual(panel.minimumWidth(), 380)
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

    def test_canvas_uses_rect_and_ellipse_line_styles(self):
        from config.config_manager import DEFAULTS

        settings = dict(DEFAULTS, rect_style="dash", ellipse_style="dash")
        canvas = AnnotationCanvas(Image.new("RGB", (120, 100), "white"), settings)
        for tool in ("rect", "ellipse"):
            canvas.tool = tool
            QTest.mousePress(canvas.viewport(), Qt.LeftButton, pos=canvas.mapFromScene(QPointF(10, 10)))
            QTest.mouseMove(canvas.viewport(), canvas.mapFromScene(QPointF(60, 40)))
            QTest.mouseRelease(canvas.viewport(), Qt.LeftButton, pos=canvas.mapFromScene(QPointF(60, 40)))
            self.assertEqual(canvas.annotations()[-1].pen().style(), Qt.DashLine)
        canvas.annotations()[-1].setSelected(True)
        canvas.set_selected_line_style("solid")
        self.assertEqual(canvas.annotations()[-1].pen().style(), Qt.SolidLine)
        canvas.close()

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
                     ["贴图", "保存", "放弃", "关闭全部"])
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
        next(button for button in buttons if button.text() == "关闭全部").click()
        next(button for button in toolbar.findChildren(QToolButton)
             if button.text() == "旋转 180°").click()
        next(action for menu in toolbar.findChildren(QMenu) for action in menu.actions()
             if action.text() == "置顶").trigger()
        self.assertEqual(commands, ["save", "close_all_editors", "half", "top"])

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
                self.assertTrue(list((Path(folder) / "sticker_cache").glob("*.png")))
                manager.close_all()
                self.app.processEvents()
                restored = StickerManager(DEFAULTS)
                restored.restore()
                self.assertEqual(len(restored.items), 1)
                self.assertEqual((restored.items[0].x(), restored.items[0].y()), (12, 25))
                restored.items[0].close()
                self.app.processEvents()
                self.assertEqual(len(restored.items), 0)

    def test_sticker_restore_skips_invalid_records_and_backs_up_corrupt_session(self):
        from config.config_manager import DEFAULTS
        from PySide6.QtGui import QColor, QImage

        with tempfile.TemporaryDirectory() as folder, \
                patch("sticker.sticker_manager.data_dir", return_value=Path(folder)):
            path = Path(folder) / "stickers.json"
            path.write_text("{invalid", encoding="utf-8")
            manager = StickerManager(DEFAULTS)
            manager.restore()
            self.assertEqual(manager.items, [])
            self.assertEqual(next(Path(folder).glob("stickers.json.broken-*")).read_text(encoding="utf-8"),
                             "{invalid")
            path.write_text(json.dumps([None, {}, {"source": "missing.png", "x": 1}]), encoding="utf-8")
            manager.restore()
            self.assertEqual(manager.items, [])
            image = QImage(12, 8, QImage.Format_RGB32)
            image.fill(QColor("red"))
            item = manager.add(image)
            valid = item.state()
            manager.persist()
            manager.close_all()
            self.app.processEvents()
            path.write_text(json.dumps([{}, valid, {"source": valid["source"]}]), encoding="utf-8")
            restored = StickerManager(DEFAULTS)
            restored.restore()
            self.assertEqual(len(restored.items), 1)
            self.assertTrue(Path(valid["source"]).exists())
            restored.close_all()

    def test_sticker_restore_without_session_cleans_orphan_cache(self):
        from config.config_manager import DEFAULTS
        from PySide6.QtGui import QColor, QImage

        with tempfile.TemporaryDirectory() as folder, \
                patch("sticker.sticker_manager.data_dir", return_value=Path(folder)):
            manager = StickerManager(DEFAULTS)
            image = QImage(12, 8, QImage.Format_RGB32)
            image.fill(QColor("red"))
            item = manager.add(image)
            cached = Path(item.source)
            manager.close_all()
            self.app.processEvents()
            manager.restore()
            self.assertFalse(cached.exists())

    def test_sticker_cache_removes_only_unreferenced_images_after_persist(self):
        from config.config_manager import DEFAULTS
        from PySide6.QtGui import QColor, QImage

        with tempfile.TemporaryDirectory() as folder, \
                patch("sticker.sticker_manager.data_dir", return_value=Path(folder)):
            manager = StickerManager(DEFAULTS)
            image = QImage(12, 8, QImage.Format_RGB32)
            image.fill(QColor("red"))
            retained = manager.add(image)
            removed = manager.add(image)
            retained_path = Path(retained.source)
            removed_path = Path(removed.source)
            removed.close()
            self.app.processEvents()
            manager.persist()
            self.assertTrue(retained_path.exists())
            self.assertFalse(removed_path.exists())
            manager.close_all()
            self.app.processEvents()
            manager.persist()
            self.assertFalse(retained_path.exists())

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

    def test_paste_latest_skips_open_deleted_and_invalid_history(self):
        from config.config_manager import DEFAULTS
        import os

        with tempfile.TemporaryDirectory() as folder:
            paths = [Path(folder) / f"{index}.png" for index in range(4)]
            for index, path in enumerate(paths):
                Image.new("RGB", (20 + index, 20), "white").save(path)
                os.utime(path, (index + 10, index + 10))
            manager = StickerManager(dict(DEFAULTS, auto_dir=folder))
            try:
                for path in reversed(paths):
                    self.assertTrue(manager.paste_latest())
                    self.assertEqual(Path(manager.items[-1].source), path)
                self.assertFalse(manager.paste_latest())
                manager.items[1].close()
                self.assertTrue(manager.paste_latest())
                self.assertEqual(Path(manager.items[-1].source), paths[2])
                manager.items[-1].close()
                paths[2].unlink()
                self.assertFalse(manager.paste_latest())
                paths[2].write_bytes(b"not an image")
                self.assertFalse(manager.paste_latest())
            finally:
                manager.close_all()
                self.app.processEvents()

    def test_history_cycle_skips_deleted_and_unreadable_files(self):
        from config.config_manager import DEFAULTS

        with tempfile.TemporaryDirectory() as folder:
            paths = [Path(folder) / f"{index}.png" for index in range(3)]
            for path in paths:
                Image.new("RGB", (20, 20), "white").save(path)
            manager = StickerManager(dict(DEFAULTS, auto_dir=folder))
            try:
                with patch.object(manager, "files", return_value=paths):
                    paths[1].unlink()
                    manager.cycle(1)
                    self.assertEqual(manager.history_sticker.source, str(paths[2]))
                    paths[0].write_bytes(b"invalid")
                    paths[2].unlink()
                    manager.cycle(1)
                    self.assertEqual(manager.history_sticker.source, str(paths[2]))
            finally:
                manager.close_all()
                self.app.processEvents()

    def test_open_sticker_file_replaces_or_adds_without_changing_on_cancel(self):
        from PySide6.QtGui import QImage
        from config.config_manager import DEFAULTS
        from sticker.sticker_menu import build_menu

        with tempfile.TemporaryDirectory() as folder:
            first = Path(folder) / "first.png"
            second = Path(folder) / "second.png"
            Image.new("RGB", (20, 20), "red").save(first)
            Image.new("RGB", (40, 20), "blue").save(second)
            manager = StickerManager(DEFAULTS)
            sticker = manager.add(QImage(str(first)), first)
            try:
                actions = {action.text(): action for action in build_menu(sticker).actions()}
                with patch("sticker.sticker_manager.QFileDialog.getOpenFileName", return_value=("", "")):
                    actions["从文件打开替换此贴图"].trigger()
                self.assertEqual(sticker.source, str(first))
                with patch("sticker.sticker_manager.QFileDialog.getOpenFileName", return_value=(str(second), "")):
                    actions["从文件打开替换此贴图"].trigger()
                self.assertEqual(sticker.source, str(second))
                self.assertEqual(len(manager.items), 1)
                with patch("sticker.sticker_manager.QFileDialog.getOpenFileName", return_value=(str(first), "")):
                    actions["从文件打开新贴图"].trigger()
                self.assertEqual(len(manager.items), 2)
                self.assertEqual(manager.items[-1].source, str(first))
            finally:
                manager.close_all()
                self.app.processEvents()

    def test_clipboard_color_values_are_recognized(self):
        from sticker.clipboard_source import parse_color

        self.assertEqual(parse_color("#ff0000"), "#ff0000")
        self.assertEqual(parse_color("1a2B3c"), "#1a2b3c")
        self.assertEqual(parse_color("#abc"), "#aabbcc")
        for value in ("", "hello", "#12", "#12345", "rgb(1, 2, 3)"):
            self.assertIsNone(parse_color(value))

    def test_clipboard_source_renders_text_color_and_file_cards(self):
        from PySide6.QtCore import QMimeData, QUrl
        from PySide6.QtGui import QGuiApplication
        from config.config_manager import DEFAULTS
        from sticker.clipboard_source import (read_clipboard, render_color_card,
                                              render_file_card, render_text_card)

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "note.txt"
            path.write_text("hello", encoding="utf-8")
            card = render_text_card("第一行\n第二行", DEFAULTS)
            self.assertFalse(card.isNull())
            self.assertGreater(card.height(), 20)
            color = render_color_card("#ff0000", DEFAULTS)
            self.assertEqual((color.width(), color.height()), (260, 150))
            self.assertFalse(render_file_card([str(path)], DEFAULTS).isNull())
            board = QGuiApplication.clipboard()
            try:
                board.setText("#ff0000")
                self.assertEqual(read_clipboard(DEFAULTS).kind, "color")
                board.setText("普通文字")
                text = read_clipboard(DEFAULTS)
                self.assertEqual((text.kind, text.text), ("text", "普通文字"))
                mime = QMimeData()
                mime.setUrls([QUrl.fromLocalFile(str(path))])
                board.setMimeData(mime)
                files = read_clipboard(DEFAULTS)
                self.assertEqual(files.kind, "files")
                self.assertEqual([Path(item) for item in files.paths], [path])
            finally:
                board.clear()

    def test_paste_clipboard_creates_sticker_and_restores_origin(self):
        from PySide6.QtGui import QGuiApplication
        from config.config_manager import DEFAULTS

        with tempfile.TemporaryDirectory() as folder, \
                patch("sticker.sticker_manager.data_dir", return_value=Path(folder)):
            manager = StickerManager(DEFAULTS)
            board = QGuiApplication.clipboard()
            try:
                board.clear()
                self.assertIsNone(manager.paste_clipboard())
                board.setText("待办：检查缓存")
                item = manager.paste_clipboard()
                self.assertIsNotNone(item)
                self.assertEqual(item.origin, {"kind": "text", "text": "待办：检查缓存"})
                self.assertTrue(Path(item.source).exists())
                manager.persist()
                restored = StickerManager(DEFAULTS)
                restored.restore()
                self.assertEqual(len(restored.items), 1)
                self.assertEqual(restored.items[0].origin.get("text"), "待办：检查缓存")
                restored.close_all()
            finally:
                board.clear()
                manager.close_all()
                self.app.processEvents()

    def test_sticker_menu_offers_origin_actions_for_text_and_files(self):
        from PySide6.QtGui import QGuiApplication, QImage
        from config.config_manager import DEFAULTS
        from sticker.sticker_menu import build_menu

        with tempfile.TemporaryDirectory() as folder, \
                patch("sticker.sticker_manager.data_dir", return_value=Path(folder)):
            manager = StickerManager(DEFAULTS)
            try:
                image = QImage(12, 8, QImage.Format_RGB32)
                plain = manager.add(image)
                with_origin = manager.add(image, origin={"kind": "text", "text": "示例"})
                self.assertNotIn("复制文字", {action.text() for action in build_menu(plain).actions()})
                self.assertIn("复制文字", {action.text() for action in build_menu(with_origin).actions()})
                with_origin.copy_origin_text()
                self.assertEqual(QGuiApplication.clipboard().text(), "示例")
            finally:
                QGuiApplication.clipboard().clear()
                manager.close_all()
                self.app.processEvents()

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
            # 热键与上次区域由专用控件或程序内部维护，不需要普通设置项入口。
            self.assertEqual(set(DEFAULTS) - keys, {"hotkeys", "last_capture_rect"})
            settings.close()

    def test_window_element_detection_cycles_selection_with_tab(self):
        from PySide6.QtCore import QRect, Qt
        from PySide6.QtTest import QTest
        from config.config_manager import DEFAULTS
        from screenshot.mask_window import MaskWindow

        bounds = {"left": 0, "top": 0, "width": 100, "height": 80}
        settings = dict(DEFAULTS, magnifier=False, crosshair=False, window_detection=True,
                        element_depth=3)
        chain = [(0, 0, 100, 80), (10, 10, 60, 50), (20, 20, 40, 30)]
        with patch("screenshot.mask_window.visible_windows", return_value=[]), \
             patch("screenshot.mask_window.element_chain", return_value=chain):
            mask = MaskWindow(Image.new("RGB", (100, 80), "white"), bounds, [bounds], settings)
            # 悬停识别要求遮罩可见（截图结束时不再查询系统窗口）。
            mask.show()
            self.app.processEvents()
            self.assertEqual(mask.element_chain, chain)
            QTest.keyClick(mask, Qt.Key_Tab)
            self.assertEqual(mask.selection.rects, [QRect(0, 0, 100, 80)])
            QTest.keyClick(mask, Qt.Key_Tab)
            self.assertEqual(mask.selection.rects, [QRect(10, 10, 50, 40)])
            QTest.keyClick(mask, Qt.Key_Tab, Qt.ShiftModifier)
            self.assertEqual(mask.selection.rects, [QRect(0, 0, 100, 80)])
            mask.close()
            auto = MaskWindow(Image.new("RGB", (100, 80), "white"), bounds, [bounds],
                              dict(settings, window_auto_select=True))
            self.assertEqual(auto.selection.rects, [QRect(0, 0, 100, 80)])
            auto.close()
            disabled = MaskWindow(Image.new("RGB", (100, 80), "white"), bounds, [bounds],
                                  dict(settings, window_detection=False))
            self.assertEqual(disabled.element_chain, [])
            disabled.close()

    def test_mask_hover_detection_highlights_and_click_selects(self):
        from PySide6.QtCore import QPoint, QRect, Qt
        from PySide6.QtTest import QTest
        from config.config_manager import DEFAULTS
        from screenshot.mask_window import MaskWindow

        bounds = {"left": 0, "top": 0, "width": 100, "height": 80}
        settings = dict(DEFAULTS, magnifier=False, crosshair=False, window_detection=True,
                        window_hover_detect=True, element_depth=3)
        chain = [(0, 0, 100, 80), (10, 10, 60, 50)]
        with patch("screenshot.mask_window.visible_windows", return_value=[]), \
                patch("screenshot.mask_window.element_chain", return_value=chain):
            mask = MaskWindow(Image.new("RGB", (100, 80), "white"), bounds, [bounds], settings)
            try:
                # 悬停识别要求遮罩可见（截图结束后不再查询系统窗口）。
                mask.show()
                self.app.processEvents()
                mask.hover_stamp = 0.0
                mask.poll_hover()
                # 悬停取最内层元素。
                self.assertEqual(mask.hover_rect, QRect(10, 10, 50, 40))
                QTest.mousePress(mask, Qt.LeftButton, Qt.NoModifier, QPoint(20, 20))
                QTest.mouseRelease(mask, Qt.LeftButton, Qt.NoModifier, QPoint(20, 20))
                self.assertEqual(mask.selection.rects, [QRect(10, 10, 50, 40)])
            finally:
                mask.close()

    def test_hover_detection_works_on_every_monitor(self):
        from PySide6.QtCore import QRect
        from config.config_manager import DEFAULTS
        from screenshot.mask_window import MaskWindow

        bounds = {"left": 0, "top": 0, "width": 200, "height": 100}
        monitors = [{"left": 0, "top": 0, "width": 100, "height": 100},
                    {"left": 100, "top": 0, "width": 100, "height": 100}]
        settings = dict(DEFAULTS, magnifier=False, crosshair=False)
        chain = [(120, 10, 180, 60)]
        with patch("screenshot.mask_window.visible_windows", return_value=[]), \
                patch("screenshot.mask_window.element_chain", return_value=chain):
            mask = MaskWindow(Image.new("RGB", (200, 100), "white"), bounds, monitors, settings)
            try:
                mask.show()
                self.app.processEvents()
                second = mask.session.views[1]
                self.assertFalse(second.primary)
                second.hover_stamp = 0.0
                second.poll_hover()
                # 非 primary 的遮罩也必须能识别，否则主屏永远没有高亮。
                self.assertEqual(second.hover_rect, QRect(120, 10, 60, 50))
            finally:
                mask.close()

    def test_follow_keeps_snap_when_window_minimized(self):
        from PySide6.QtCore import QPoint, QRect
        from PySide6.QtGui import QImage
        from config.config_manager import DEFAULTS

        settings = dict(DEFAULTS, sticker_snap_targets="window", sticker_follow_window=True)
        sticker = StickerItem(QImage(40, 30, QImage.Format_RGB32), settings=settings)
        target = Mock(handle=1234, title="记事本", class_name="Notepad", rect=QRect(100, 100, 300, 200))
        try:
            with patch("sticker.sticker_item.visible_targets", return_value=[target]), \
                    patch("sticker.sticker_item.window_under_point", return_value=target):
                sticker.apply_snap(QPoint(104, 105), QPoint(120, 120))
            self.assertTrue(sticker.following())
            # 最小化只是读不到矩形，句柄仍在，吸附关系要保留。
            with patch("sticker.sticker_item.window_logical_rect", return_value=None), \
                    patch("sticker.sticker_item.window_present", return_value=True):
                sticker.poll_follow()
            self.assertIsNotNone(sticker.snap_target)
            self.assertTrue(sticker.following())
            # 窗口真正关闭才解除吸附与跟随。
            with patch("sticker.sticker_item.window_logical_rect", return_value=None), \
                    patch("sticker.sticker_item.window_present", return_value=False):
                sticker.poll_follow()
            self.assertIsNone(sticker.snap_target)
            self.assertFalse(sticker.following())
        finally:
            sticker.release_snap()
            sticker.close()

    def test_toolbar_options_panel_shows_live_preview(self):
        from config.config_manager import DEFAULTS
        from editor.toolbar_widget import ToolbarWidget

        toolbar = ToolbarWidget(settings=dict(DEFAULTS))
        try:
            self.assertEqual(sorted(toolbar.previews),
                             ["arrow", "crop", "marker", "mosaic", "shapes", "text"])
            # 弹窗未显示时 isVisible 恒为 False，这里只看控件自身的显式隐藏状态。
            toolbar.tool_buttons["arrow"].click()
            self.assertEqual([name for name, widget in toolbar.previews.items() if not widget.isHidden()],
                             ["arrow"])
            toolbar.tool_buttons["mosaic"].click()
            self.assertEqual([name for name, widget in toolbar.previews.items() if not widget.isHidden()],
                             ["mosaic"])
            # 预览行要计入弹窗高度。
            menu = toolbar.options_button.menu()
            panel = menu.actions()[0].defaultWidget()
            menu.show()
            self.app.processEvents()
            self.assertGreater(panel.height(), 120)
            # 改参数后丢弃缓存，下一次绘制按新值重建。
            preview = toolbar.previews["mosaic"]
            preview.scene = "cached"
            toolbar.mosaic_size.setValue(30)
            self.assertIsNone(preview.scene)
            toolbar.tool_buttons["select"].click()
            self.assertEqual([widget for widget in toolbar.previews.values() if widget.isVisible()],
                             [])
        finally:
            toolbar.close()

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

    def test_element_chain_prefers_uia_and_falls_back(self):
        import sys
        from unittest.mock import Mock
        from core import window_elements, window_uia

        # UIA 可用时直接用它的结果。
        with patch("core.window_uia.element_chain", return_value=[(5, 5, 6, 6)]), \
                patch.object(window_elements, "collect", return_value=[(0, 0, 1, 1)]) as fallback:
            self.assertEqual(window_elements.element_chain((1, 1), 3, use_uia=True),
                             [(5, 5, 6, 6)])
            fallback.assert_not_called()
        # UIA 拿不到结果时退回窗口句柄。
        with patch("core.window_uia.element_chain", return_value=[]), \
                patch.object(window_elements, "collect", return_value=[(0, 0, 1, 1)]):
            self.assertEqual(window_elements.element_chain((1, 1), 3, use_uia=True),
                             [(0, 0, 1, 1)])

    def test_element_chain_survives_uia_flag(self):
        from core import window_elements

        # collect 必须接受 use_uia，否则提示日志会抛 NameError，识别整体失效。
        with patch("core.window_uia.element_chain", return_value=[]), \
                patch.object(window_elements, "top_window_at", return_value=None):
            self.assertEqual(window_elements.element_chain((1, 1), 3, use_uia=True), [])
            self.assertEqual(window_elements.element_chain((1, 1), 3), [])

    def test_uia_element_chain_walks_parents(self):
        import sys
        from unittest.mock import Mock
        from core import window_uia

        inner = Mock()
        inner.BoundingRectangle = (10, 10, 100, 80)
        inner.Name = "按钮"
        inner.ControlTypeName = "ButtonControl"
        outer = Mock()
        outer.BoundingRectangle = (0, 0, 200, 150)
        outer.Name = "窗口"
        outer.ControlTypeName = "WindowControl"
        inner.GetParentControl.return_value = outer
        outer.GetParentControl.return_value = None
        automation = Mock()
        automation.ControlFromHandle.return_value = inner
        with patch("core.window_uia.top_window_at", return_value=999), \
                patch.dict(sys.modules, {"uiautomation": automation}):
            self.assertTrue(window_uia.available())
            self.assertEqual(window_uia.element_chain((50, 50), 3),
                             [(0, 0, 200, 150), (10, 10, 100, 80)])
        # 过小的控件被过滤。
        tiny = Mock()
        tiny.BoundingRectangle = (10, 10, 12, 12)
        tiny.GetParentControl.return_value = None
        automation.ControlFromHandle.return_value = tiny
        with patch("core.window_uia.top_window_at", return_value=999), \
                patch.dict(sys.modules, {"uiautomation": automation}):
            self.assertEqual(window_uia.element_chain((11, 11), 3), [])

    def test_settings_page_can_reset_its_own_defaults(self):
        from config.config_manager import DEFAULTS

        with tempfile.TemporaryDirectory() as folder:
            manager = ConfigManager(Path(folder) / "settings.json")
            settings = SettingsWindow(manager)
            try:
                page = settings.pages.widget(5)
                other = settings.pages.widget(0)
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
        from PySide6.QtWidgets import QGraphicsTextItem

        with tempfile.TemporaryDirectory() as folder:
            manager = ConfigManager(Path(folder) / "settings.json")
            settings = SettingsWindow(manager)
            page = settings.pages.widget(3)
            previews = {preview.kind: preview for preview in page.previews}
            self.assertEqual(sorted(previews), ["arrow", "crop", "marker", "mosaic", "shapes", "text"])
            page.controls["font_size"].setValue(42)
            text = previews["text"]
            self.assertIsNone(text.scene)
            scene = text.build(QRectF(0, 0, 320, 120))
            items = [item for item in scene.items() if isinstance(item, QGraphicsTextItem)]
            self.assertTrue(items)
            self.assertEqual(items[0].font().pointSize(), 42)
            for kind in ("arrow", "shapes", "marker", "mosaic", "crop"):
                built = previews[kind].build(QRectF(0, 0, 320, 96))
                self.assertGreater(len(built.items()), 1, kind)
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

    def test_clipboard_color_detection_switch_changes_kind(self):
        from PySide6.QtGui import QGuiApplication
        from config.config_manager import DEFAULTS
        from sticker.clipboard_source import read_clipboard

        board = QGuiApplication.clipboard()
        try:
            board.setText("#ff0000")
            self.assertEqual(read_clipboard(DEFAULTS).kind, "color")
            self.assertEqual(read_clipboard(dict(DEFAULTS, clipboard_color_detection=False)).kind,
                             "text")
        finally:
            board.clear()

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

    def test_save_background_is_used_for_opaque_formats(self):
        from PySide6.QtGui import QColor, QImage
        from config.config_manager import DEFAULTS
        from core.image_io import save_image

        with tempfile.TemporaryDirectory() as folder:
            image = QImage(20, 10, QImage.Format_ARGB32)
            image.fill(Qt.transparent)
            target = Path(folder) / "flat.jpg"
            self.assertTrue(save_image(image, target, dict(DEFAULTS, save_format="jpg",
                                                           save_background="#ff0000")))
            pixel = QColor(QImage(str(target)).pixel(5, 5))
            self.assertGreater(pixel.red(), 200)
            self.assertLess(pixel.green(), 60)
            self.assertLess(pixel.blue(), 60)

    def test_save_format_and_quality_control_output(self):
        from PySide6.QtGui import QColor, QImage
        from config.config_manager import DEFAULTS
        from core.image_io import save_image, saved_extension

        with tempfile.TemporaryDirectory() as folder:
            manager = ConfigManager(Path(folder) / "settings.json")
            self.assertEqual((manager.data["save_format"], manager.data["save_quality"]), ("png", 90))
            self.assertEqual(saved_extension({"save_format": "jpg"}), "jpg")
            self.assertEqual(saved_extension({}), "png")
            image = QImage(30, 20, QImage.Format_ARGB32)
            image.fill(QColor("red"))
            for name, settings in (("shot.jpg", {"save_format": "jpg", "save_quality": 60}),
                                   ("shot.webp", {"save_format": "webp", "save_quality": 80})):
                target = Path(folder) / name
                self.assertTrue(save_image(image, target, settings))
                self.assertTrue(target.exists())
            manager.data["save_format"] = "tiff"
            with self.assertRaises(ValueError):
                manager.save()
            manager.data["save_format"] = "png"
            manager.data["save_quality"] = 0
            with self.assertRaises(ValueError):
                manager.save()
            manager.data["save_quality"] = 90
            manager.save()
            self.assertEqual(ConfigManager(Path(folder) / "settings.json").data["save_format"], "png")

    def test_editor_and_history_use_configured_save_format(self):
        from config.config_manager import DEFAULTS

        with tempfile.TemporaryDirectory() as folder:
            settings = dict(DEFAULTS, save_format="jpg", auto_dir=folder, manual_dir=folder)
            editor = EditorWindow(Image.new("RGB", (40, 30), "white"), settings)
            saved = editor.save()
            self.assertEqual(saved.suffix, ".jpg")
            self.assertTrue(saved.exists())
            editor.close()
            manager = StickerManager(settings)
            self.assertEqual([path.suffix for path in manager.files()], [".jpg"])

    def test_sticker_panel_lists_and_locates_open_stickers(self):
        from PySide6.QtGui import QColor, QImage
        from config.config_manager import DEFAULTS
        from ui.sticker_panel import StickerPanel

        with tempfile.TemporaryDirectory() as folder, \
                patch("sticker.sticker_manager.data_dir", return_value=Path(folder)):
            manager = StickerManager(DEFAULTS)
            panel = StickerPanel(manager)
            events = []
            manager.changed.connect(lambda: events.append(len(manager.items)))
            try:
                image = QImage(12, 8, QImage.Format_RGB32)
                image.fill(QColor("red"))
                manager.add(image)
                manager.add(image)
                self.assertEqual(events[-2:], [1, 2])
                panel.refresh()
                self.assertEqual(panel.list_widget.count(), 2)
                self.assertEqual(panel.index_of(manager.items[0]), 0)
                panel.locate(0)
                panel.locate(9)
                manager.items[-1].close()
                self.app.processEvents()
                panel.refresh()
                self.assertEqual(panel.list_widget.count(), 1)
            finally:
                panel.close()
                manager.close_all()
                self.app.processEvents()

    def test_tray_menu_exposes_clipboard_and_panel_entries(self):
        from ui.tray_menu import make_tray_menu

        menu = make_tray_menu(self.app, lambda: None, lambda: None, lambda: None,
                              hotkeys={"paste_clipboard": "ctrl+f3", "sticker_panel": "ctrl+alt+p"},
                              paste_clipboard=lambda: None, sticker_panel=lambda: None)
        labels = [action.text() for action in menu.actions()]
        self.assertTrue(any(label.startswith("贴剪贴板内容") for label in labels), labels)
        self.assertTrue(any(label.startswith("贴图管理") for label in labels), labels)

    def test_clipboard_plain_text_falls_back_to_html(self):
        from PySide6.QtCore import QMimeData
        from sticker.clipboard_source import plain_text

        mime = QMimeData()
        mime.setHtml("<p>第一行<br>第二行 &amp; 结尾</p>")
        text = plain_text(mime)
        self.assertIn("第一行", text)
        self.assertIn("第二行", text)
        self.assertIn("&", text)
        empty = QMimeData()
        self.assertEqual(plain_text(empty), "")

    def test_sticker_snap_offsets_align_to_nearest_edge(self):
        from sticker.sticker_snap import snap_offsets

        window = {"key": "window", "left": 100, "top": 100, "right": 400, "bottom": 300,
                  "allow_outside": True}
        screen = {"key": "screen0", "left": 0, "top": 0, "right": 1920, "bottom": 1080,
                  "allow_outside": False}
        # 窗口内对齐：贴到窗口左上角。
        self.assertEqual(snap_offsets((104, 105, 204, 205), [window], 8), (-4, -5, ("window", "left")))
        # 窗口外贴合：贴图左沿贴到窗口右沿。
        self.assertEqual(snap_offsets((406, 100, 506, 200), [window], 8), (-6, 0, ("window", "right")))
        # 屏幕只做内对齐，不做外贴合。
        self.assertEqual(snap_offsets((5, 5, 105, 105), [screen], 8), (-5, -5, ("screen0", "left")))
        # 超出阈值不吸附，保持自由拖动。
        self.assertEqual(snap_offsets((140, 140, 240, 240), [window], 8), (0, 0, None))

    def test_sticker_snap_attaches_window_and_follows(self):
        from PySide6.QtCore import QPoint, QRect
        from PySide6.QtGui import QImage
        from config.config_manager import DEFAULTS

        settings = dict(DEFAULTS, sticker_snap_threshold=8, sticker_snap_targets="window",
                        sticker_follow_window=True, sticker_follow_interval=60)
        sticker = StickerItem(QImage(40, 30, QImage.Format_RGB32), settings=settings)
        target = Mock(handle=1234, title="记事本", class_name="Notepad", rect=QRect(100, 100, 300, 200))
        try:
            with patch("sticker.sticker_item.visible_targets", return_value=[target]), \
                    patch("sticker.sticker_item.window_under_point", return_value=target):
                position = sticker.apply_snap(QPoint(104, 105), QPoint(120, 120))
            self.assertEqual((position.x(), position.y()), (100, 100))
            self.assertEqual((sticker.snap_target["hwnd"], sticker.snap_target["edge"]), (1234, "left"))
            self.assertEqual(sticker.state()["snap"]["hwnd"], 1234)
            self.assertTrue(sticker.following())
            # 超出阈值时不吸附。
            with patch("sticker.sticker_item.visible_targets", return_value=[target]), \
                    patch("sticker.sticker_item.window_under_point", return_value=target):
                free = sticker.apply_snap(QPoint(300, 240), QPoint(320, 250))
            self.assertEqual((free.x(), free.y()), (300, 240))
            # 目标窗口关闭时自动解除吸附与跟随。
            with patch("sticker.sticker_item.window_logical_rect", return_value=None):
                sticker.poll_follow()
            self.assertIsNone(sticker.snap_target)
            self.assertFalse(sticker.following())
            sticker.settings["sticker_snap_enabled"] = False
            self.assertFalse(sticker.snap_enabled())
        finally:
            sticker.release_snap()
            sticker.close()

    def test_screen_mapping_pairs_devices_and_converts(self):
        from PySide6.QtCore import QRect
        from core.screen_mapping import pair_screens, physical_rect_to_logical

        logical = [{"name": "\\\\.\\DISPLAY1", "geometry": QRect(0, 0, 1280, 720), "dpr": 1.5},
                   {"name": "\\\\.\\DISPLAY2", "geometry": QRect(1280, 0, 960, 540), "dpr": 1.0}]
        # 副屏物理起点 1920 对应逻辑起点 1280，简单乘以缩放比例会得到错误结果。
        physical = [("\\\\.\\DISPLAY2", QRect(1920, 0, 960, 540)),
                    ("\\\\.\\DISPLAY1", QRect(0, 0, 1920, 1080))]
        mappings = pair_screens(logical, physical)
        self.assertEqual(len(mappings), 2)
        first = next(item for item in mappings if item.name == "\\\\.\\DISPLAY1")
        self.assertEqual((first.physical, first.logical),
                         (QRect(0, 0, 1920, 1080), QRect(0, 0, 1280, 720)))
        with patch("core.screen_mapping.screen_mappings", return_value=mappings):
            self.assertEqual(physical_rect_to_logical(QRect(2000, 100, 300, 200)),
                             QRect(1360, 100, 300, 200))

    def test_sticker_snap_prefers_window_over_screen(self):
        from sticker.sticker_snap import snap_offsets

        screen = {"key": "screen0", "left": 0, "top": 0, "right": 1920, "bottom": 1080,
                  "allow_outside": False, "priority": 0}
        # 最大化窗口与屏幕边缘重合，同距离时应优先贴到窗口，否则无法开启跟随。
        window = {"key": "win1", "left": 0, "top": 0, "right": 1920, "bottom": 1040,
                  "allow_outside": True, "priority": 1}
        dx, dy, hit = snap_offsets((4, 4, 104, 104), [screen, window], 8)
        self.assertEqual((dx, dy), (-4, -4))
        self.assertEqual(hit[0], "win1")

    def test_log_rate_throttles_repeated_state(self):
        from logger.log_rate import throttled

        self.assertTrue(throttled("unit", "same", 1.0))
        self.assertFalse(throttled("unit", "same", 1.0))
        self.assertTrue(throttled("unit", "changed", 1.0))

    def test_settings_window_resets_defaults_and_clears_session(self):
        from PySide6.QtWidgets import QMessageBox
        from config.config_manager import DEFAULTS

        with tempfile.TemporaryDirectory() as folder, \
                patch("ui.settings_window.set_start_on_boot"), \
                patch("ui.settings_window.data_dir", return_value=Path(folder)):
            manager = ConfigManager(Path(folder) / "settings.json")
            manager.data["sticker_snap_threshold"] = 30
            manager.data["log_level"] = "TRACE"
            manager.save()
            session = Path(folder) / "stickers.json"
            session.write_text("[]", encoding="utf-8")
            cache = Path(folder) / "sticker_cache"
            cache.mkdir()
            (cache / "sticker_1.png").write_bytes(b"x")
            settings = SettingsWindow(manager)
            try:
                with patch.object(QMessageBox, "question", return_value=QMessageBox.Yes), \
                        patch.object(QMessageBox, "information"):
                    settings.reset_defaults()
                    settings.clear_sticker_session()
                self.assertEqual((manager.data["sticker_snap_threshold"], manager.data["log_level"]),
                                 (DEFAULTS["sticker_snap_threshold"], DEFAULTS["log_level"]))
                backup = Path(folder) / "settings.bak"
                self.assertTrue(backup.is_file())
                self.assertEqual(json.loads(backup.read_text(encoding="utf-8"))["log_level"], "TRACE")
                # 界面控件也必须回到默认值，而不是停留在重置前的显示。
                page = settings.pages.widget(5)
                self.assertEqual(page.controls["sticker_snap_threshold"].value(),
                                 DEFAULTS["sticker_snap_threshold"])
                self.assertFalse(session.exists())
                self.assertEqual(list(cache.glob("sticker_*.png")), [])
            finally:
                settings.close()

    def test_window_hit_ignores_invisible_system_windows(self):
        from core.window_snap import IGNORED_CLASSES, is_cloaked, top_window_at

        # 平板模式覆盖窗口整屏大小且不可见，会抢走鼠标下的真正目标。
        self.assertIn("TabletModeCoverWindow", IGNORED_CLASSES)
        # 无效句柄不应抛异常，识别失败时直接返回空结果。
        self.assertFalse(is_cloaked(0))
        self.assertIsInstance(top_window_at(0, 0), (int, type(None)))

    def test_window_under_point_builds_target(self):
        from PySide6.QtCore import QPoint, QRect
        from core.window_snap import window_under_point

        # 命中后要能构造出目标信息（曾因缺 user32/skipped 变量而抛 NameError）。
        with patch("core.window_snap.top_window_at", return_value=1234), \
                patch("core.window_snap.window_logical_rect", return_value=QRect(10, 10, 100, 80)):
            target = window_under_point(QPoint(50, 50))
        self.assertIsNotNone(target)
        self.assertEqual(target.handle, 1234)
        self.assertEqual(target.rect, QRect(10, 10, 100, 80))

    def test_screen_mapping_pairs_by_position_when_names_differ(self):
        from PySide6.QtCore import QRect
        from core.screen_mapping import pair_screens

        # Qt 屏幕名是显示器型号，与 Windows 设备名对不上；两块屏分辨率又相同，
        # 只能按排列顺序配对，配反会让水平坐标整体偏移一整块屏。
        logical = [{"name": "T2752Q", "geometry": QRect(0, 0, 2560, 1440), "dpr": 1.0},
                   {"name": "DELL U2723", "geometry": QRect(2560, 0, 2560, 1440), "dpr": 1.0}]
        physical = [("\\\\.\\DISPLAY2", QRect(2560, 0, 2560, 1440)),
                    ("\\\\.\\DISPLAY1", QRect(0, 0, 2560, 1440))]
        mappings = {mapping.name: mapping for mapping in pair_screens(logical, physical)}
        self.assertEqual(mappings["T2752Q"].physical, QRect(0, 0, 2560, 1440))
        self.assertEqual(mappings["DELL U2723"].physical, QRect(2560, 0, 2560, 1440))

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


if __name__ == "__main__":
    unittest.main()