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
        # 关闭会抢系统前台/注册系统键盘钩子的兜底逻辑，避免自动化测试干扰真实按键。
        from core import window_focus
        from screenshot import mask_window

        cls._foreground_fallback = window_focus.FOREGROUND_FALLBACK
        cls._escape_fallback = mask_window.ESCAPE_FALLBACK_ENABLED
        window_focus.FOREGROUND_FALLBACK = False
        mask_window.ESCAPE_FALLBACK_ENABLED = False

    @classmethod
    def tearDownClass(cls):
        from core import window_focus
        from screenshot import mask_window

        window_focus.FOREGROUND_FALLBACK = cls._foreground_fallback
        mask_window.ESCAPE_FALLBACK_ENABLED = cls._escape_fallback

    def test_group_properties_persist_and_unassign_keeps_stickers(self):
        from PySide6.QtGui import QColor, QImage
        from config.config_manager import DEFAULTS

        with tempfile.TemporaryDirectory() as folder, \
                patch("sticker.sticker_manager.data_dir", return_value=Path(folder)):
            settings = dict(DEFAULTS, auto_dir=folder)
            image = QImage(12, 8, QImage.Format_RGB32)
            image.fill(QColor("#23bc58"))
            manager = StickerManager(settings)
            first = manager.add(image, show=False)
            second = manager.add(image, show=False)
            try:
                first.locked = True
                second.locked = False
                first.border_enabled = False
                second.border_enabled = True
                manager.assign_group([first, second], "批量组")
                self.assertTrue(manager._persist_timer.isActive())

                self.assertEqual(manager.set_group_properties("批量组", {
                    "opacity": 0.55,
                    "locked": True,
                    "border_enabled": True,
                    "shadow_enabled": False,
                    "click_through": False,
                    "rotation": 37,
                }), 2)
                for item in (first, second):
                    self.assertAlmostEqual(item.windowOpacity(), 0.55, places=2)
                    self.assertTrue(item.locked)
                    self.assertTrue(item.border_enabled)
                    self.assertFalse(item.shadow_enabled)
                    self.assertFalse(item.click_through)
                    self.assertEqual(item.rotation_degrees, 37)

                manager.persist()
                states = json.loads((Path(folder) / "stickers.json").read_text(
                    encoding="utf-8"))
                self.assertEqual(len(states), 2)
                for state in states:
                    self.assertAlmostEqual(state["opacity"], 0.55, places=2)
                    self.assertTrue(state["locked"])
                    self.assertTrue(state["border"])
                    self.assertFalse(state["shadow"])
                    self.assertEqual(state["rotation"], 37)

                restored = StickerManager(settings)
                restored.restore()
                self.assertEqual(len(restored.items), 2)
                self.assertTrue(all(item.rotation_degrees == 37
                                    for item in restored.items))
                restored.close_all()

                source_paths = [Path(item.source) for item in (first, second)]
                manager.assign_group([first, second], "")
                self.assertTrue(all(item in manager.items for item in (first, second)))
                self.assertTrue(all(path.is_file() for path in source_paths))
                self.assertEqual([item.group_name for item in (first, second)], ["", ""])
            finally:
                manager._persist_timer.stop()
                manager.close_all()

    def test_sticker_shadow_does_not_dim_image(self):
        from PySide6.QtGui import QColor, QImage, QPixmap
        from config.config_manager import DEFAULTS

        settings = dict(DEFAULTS)
        settings["sticker_shadow_enabled"] = True
        settings["sticker_shadow_strength"] = 35
        settings["sticker_shadow_color"] = "#000000"
        settings["sticker_background_mode"] = "transparent"
        image = QImage(160, 100, QImage.Format_RGB32)
        image.fill(QColor("#ffffff"))
        item = StickerItem(image, source=None, settings=settings)
        item.setAttribute(Qt.WA_DeleteOnClose, False)
        try:
            w, h = item.width(), item.height()
            pix = QPixmap(w, h)
            pix.fill(QColor(0, 0, 0, 0))
            item.render(pix)
            res = pix.toImage()
            center = res.pixelColor(w // 2, h // 2)
            # 投影只应落在贴图外侧；主体像素应保持原亮度，不能被半透明阴影压暗。
            self.assertGreaterEqual(center.red(), 250)
            self.assertGreaterEqual(center.green(), 250)
            self.assertGreaterEqual(center.blue(), 250)
        finally:
            item.close()

    def test_sticker_close_group_persists_removed_members_immediately(self):
        from PySide6.QtGui import QColor, QImage
        from config.config_manager import DEFAULTS

        with tempfile.TemporaryDirectory() as folder, \
                patch("sticker.sticker_manager.data_dir", return_value=Path(folder)):
            settings = dict(DEFAULTS, auto_dir=folder)
            image = QImage(12, 8, QImage.Format_RGB32)
            image.fill(QColor("#23bc58"))
            manager = StickerManager(settings)
            first = manager.add(image, show=False)
            second = manager.add(image, show=False)
            retained = manager.add(image, show=False)
            restored = None
            try:
                manager.assign_group([first, second], "closing")
                manager.assign_group([retained], "retained")
                self.assertTrue(manager._persist_timer.isActive())

                self.assertEqual(manager.close_group("closing"), 2)
                self.assertFalse(manager._persist_timer.isActive())
                states = json.loads(
                    (Path(folder) / "stickers.json").read_text(encoding="utf-8"))
                self.assertEqual(len(states), 1)
                self.assertEqual(states[0]["group"], "retained")

                restored = StickerManager(settings)
                restored.restore()
                self.assertEqual(len(restored.items), 1)
                self.assertEqual(restored.group_names(), ["retained"])
            finally:
                manager.close_all()
                if restored is not None:
                    restored.close_all()
                self.app.processEvents()

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
            settings = dict(DEFAULTS, auto_dir=folder)
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
        with patch("main.capture", return_value=(Image.new("RGB", (200, 150)),
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

    def test_qimage_to_pillow_preserves_stride_and_alpha(self):
        from PySide6.QtGui import QColor, QImage
        from core.screen_capture import qimage_to_pillow

        rgb = QImage(3, 2, QImage.Format_RGB888)
        rgb.setPixelColor(2, 1, QColor(12, 34, 56))
        rgba = QImage(3, 2, QImage.Format_ARGB32_Premultiplied)
        rgba.fill(0)
        rgba.setPixelColor(1, 1, QColor(90, 40, 10, 128))

        converted_rgb = qimage_to_pillow(rgb)
        converted_rgba = qimage_to_pillow(rgba)
        self.assertEqual(converted_rgb.size, (3, 2))
        self.assertEqual(converted_rgb.getpixel((2, 1)), (12, 34, 56, 255))
        self.assertEqual(converted_rgba.getpixel((1, 1)),
                         tuple(rgba.pixelColor(1, 1).getRgb()))

    def test_config_roundtrip_and_conflict(self):
        from config.config_manager import DEFAULTS

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "settings.json"
            manager = ConfigManager(path)
            self.assertEqual(manager.data["capture_after_selection"], "edit")
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["capture_after_selection"], "edit")
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
            directory = settings.page("日志").findChild(FilePathEdit)
            directory.input.setText(str(Path(folder) / "logs"))
            directory.input.editingFinished.emit()
            settings.flush_persist()
            self.assertEqual(ConfigManager(manager.path).data["log_dir"],
                             directory.input.text())
            settings.close()

    def test_save_clipboard_options_persist_independently(self):
        with tempfile.TemporaryDirectory() as folder:
            manager = ConfigManager(Path(folder) / "settings.json")
            settings = SettingsWindow(manager)
            save_page = settings.page("保存与输出")
            image = save_page.controls["copy_saved_image"]
            path = save_page.controls["copy_saved_path"]
            self.assertTrue(image.isChecked())
            self.assertFalse(path.isChecked())
            image.setChecked(False)
            path.setChecked(True)
            settings.flush_persist()
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
                            "capture_after_selection": "edit", "magnifier": False,
                            "copy_saved_image": copy_image,
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
        from datetime import datetime
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
                                     root / "logs" / datetime.now().strftime("%Y-%m") / "app.log")
                    self.assertFalse(missing.exists())
                    settings["log_dir"] = str(existing)
                    logger = configure_logging(settings)
                    self.assertEqual(Path(logger.handlers[0].baseFilename),
                                     existing / datetime.now().strftime("%Y-%m") / "app.log")
                    settings["log_monthly_folder"] = False
                    logger = configure_logging(settings)
                    self.assertEqual(Path(logger.handlers[0].baseFilename), existing / "app.log")
                    settings["log_dir"] = ""
                    settings["log_monthly_folder"] = True
                    logger = configure_logging(settings)
                    self.assertEqual(Path(logger.handlers[0].baseFilename),
                                     root / "logs" / datetime.now().strftime("%Y-%m") / "app.log")
                finally:
                    configure_logging(dict(settings, logging_enabled=False))

    def test_monthly_log_handler_switches_folder_after_month_boundary(self):
        from datetime import datetime as RealDateTime
        from logging import LogRecord
        from logger import log_setup

        class FakeDateTime:
            current = RealDateTime(2026, 9, 30, 23, 59)

            @classmethod
            def now(cls):
                return cls.current

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with patch.object(log_setup, "datetime", FakeDateTime):
                handler = log_setup.MonthlyDirectoryTimedRotatingFileHandler(
                    root, "app.log", when="midnight", backupCount=14, encoding="utf-8")
                try:
                    record = LogRecord("test", 20, __file__, 1, "september", (), None)
                    handler.handle(record)
                    self.assertTrue((root / "2026-09" / "app.log").is_file())
                    FakeDateTime.current = RealDateTime(2026, 10, 1, 0, 1)
                    record.msg = "october"
                    handler.handle(record)
                    self.assertTrue((root / "2026-10" / "app.log").is_file())
                    self.assertIn("october", (root / "2026-10" / "app.log").read_text(encoding="utf-8"))
                finally:
                    handler.close()

    def test_image_archiving_paths_and_history_scan_all_periods(self):
        from datetime import date
        from types import SimpleNamespace
        from config.config_manager import DEFAULTS
        from core.path_utils import configured_dir, resolved_dir
        from sticker.sticker_manager import StickerManager

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "Auto"
            manual = Path(folder) / "Manual"
            settings = dict(DEFAULTS, auto_dir=str(root), manual_dir=str(manual),
                            archive_by_month=True, archive_by_day=False)
            self.assertEqual(resolved_dir(settings, "auto_dir", date(2026, 9, 30)),
                             root / "2026-09")
            self.assertEqual(resolved_dir(settings, "auto_dir", date(2026, 10, 1)),
                             root / "2026-10")
            settings["archive_by_month"] = False
            settings["archive_by_day"] = True
            self.assertEqual(resolved_dir(settings, "manual_dir", date(2026, 9, 30)),
                             manual / "2026-09-30")
            self.assertEqual(configured_dir(settings, "auto_dir"), root)

            for month, name in (("2026-09", "old.png"), ("2026-10", "new.png")):
                folder_path = root / month
                folder_path.mkdir(parents=True)
                Image.new("RGB", (2, 2), "white").save(folder_path / name)
            history = StickerManager.files(SimpleNamespace(settings=settings))
            self.assertEqual({path.name for path in history}, {"old.png", "new.png"})

    def test_settings_controls_have_initialized_defaults(self):
        from config.config_manager import DEFAULTS, validate
        from core.window_uia import DESCEND_LIMIT

        self.assertEqual(DEFAULTS["element_depth"], 12)
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

        self.assertEqual(DEFAULTS["element_depth"], 12)
        self.assertEqual(validate({"element_depth": 32})["element_depth"], 32)
        with self.assertRaises(ValueError):
            validate({"element_depth": 33})

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
            self.assertEqual(depth_control.value(), 12)
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

    def test_general_page_reset_restores_uia_and_hover_interval(self):
        from config.config_manager import DEFAULTS
        from ui.settings_window import SettingsWindow

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "settings.json"
            # 模拟用户把两项改成了非默认值。
            path.write_text(json.dumps({"window_uia_detect": False,
                                        "window_hover_interval": 300,
                                        "mask_color": "#FFE2EB",
                                        "mask_opacity": 72,
                                        "capture_after_selection": "edit"}), encoding="utf-8")
            manager = ConfigManager(path)
            self.assertFalse(manager.data["window_uia_detect"])
            self.assertEqual(manager.data["window_hover_interval"], 300)
            self.assertEqual(manager.data["mask_color"], "#ffe2eb")
            self.assertEqual(manager.data["mask_opacity"], 72)
            self.assertEqual(manager.data["capture_after_selection"], "edit")
            settings = SettingsWindow(manager)
            screenshot = settings.page("截图")
            self.assertEqual(screenshot.controls["capture_after_selection"].currentData(), "edit")
            self.assertIn("\n", screenshot.controls["capture_after_selection"].toolTip())
            screenshot.reset_page()
            # 单页重置应把本页涉及的项（含本轮新增项）还原为默认值并持久化。
            self.assertTrue(manager.data["window_uia_detect"])
            self.assertEqual(manager.data["window_hover_interval"],
                             DEFAULTS["window_hover_interval"])
            self.assertEqual(manager.data["mask_color"], DEFAULTS["mask_color"])
            self.assertEqual(manager.data["mask_opacity"], DEFAULTS["mask_opacity"])
            settings.flush_persist()
            self.assertEqual(ConfigManager(path).data["window_hover_interval"],
                             DEFAULTS["window_hover_interval"])
            self.assertEqual(ConfigManager(path).data["mask_color"], DEFAULTS["mask_color"])
            self.assertEqual(ConfigManager(path).data["mask_opacity"], DEFAULTS["mask_opacity"])
            self.assertEqual(manager.data["capture_after_selection"], "edit")
            self.assertEqual(screenshot.controls["capture_after_selection"].currentData(), "edit")
            settings.close()

    def test_new_feature_config_keys_initialized_and_reset(self):
        """本轮新增配置键（标尺/取色/序号/回收站）必须：
        ① 都写入 DEFAULTS 且类型正确；② 旧配置缺键时 validate 自动补齐默认值；
        ③ 「恢复本页默认」能把被改掉的新增项还原并持久化。"""
        from config.config_manager import DEFAULTS, validate, ConfigManager

        new_keys = {
            "capture_picker_shortcut": str, "magnifier_grid": bool,
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
        self.assertTrue(result["ruler_enabled"])
        self.assertTrue(result["magnifier_grid"])
        self.assertTrue(result["sticker_recycle_enabled"])
        self.assertEqual(result["sequence_shape"], "circle")

        from ui.settings_window import SettingsWindow
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "settings.json"
            # 模拟用户把若干新增项改成非默认值（含把标尺关掉）。
            path.write_text(json.dumps({
                "ruler_enabled": False, "ruler_color": "#123456",
                "sequence_shape": "star",
                "capture_after_selection": "edit",
            }), encoding="utf-8")
            manager = ConfigManager(path)
            self.assertFalse(manager.data["ruler_enabled"])
            settings = SettingsWindow(manager)
            screenshot = settings.page("截图")
            editor = settings.page("编辑器")
            # 截图页重置：标尺/标尺颜色回到默认。
            screenshot.reset_page()
            self.assertTrue(manager.data["ruler_enabled"])
            self.assertEqual(manager.data["ruler_color"], DEFAULTS["ruler_color"])

            # 编辑器页重置：序号形状回到默认。
            editor.reset_page()
            self.assertEqual(manager.data["sequence_shape"], DEFAULTS["sequence_shape"])
            settings.flush_persist()
            reloaded = ConfigManager(path).data
            self.assertTrue(reloaded["ruler_enabled"])
            self.assertEqual(reloaded["ruler_color"], DEFAULTS["ruler_color"])

            self.assertEqual(reloaded["sequence_shape"], DEFAULTS["sequence_shape"])
            settings.close()

    def test_text_item_uses_shared_annotation_color(self):
        from config.config_manager import DEFAULTS
        from editor.annotation_items import text_item

        settings = dict(DEFAULTS, pen_color="#123456", text_color="#abcdef")
        item = text_item(QPointF(0, 0), "hello", settings, Qt.AlignLeft)
        self.assertEqual(item.defaultTextColor().name(), "#abcdef")

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
        self.assertEqual(DEFAULTS["mask_opacity"], 60)
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

    def test_settings_pages_group_related_controls_and_scroll(self):
        from PySide6.QtWidgets import QGroupBox, QScrollArea

        with tempfile.TemporaryDirectory() as folder:
            settings = SettingsWindow(ConfigManager(Path(folder) / "settings.json"))
            settings.show()
            self.app.processEvents()
            expected = (
                ("截图", "截图后", ("inline_edit", "capture_after_selection")),
                ("截图", "定位辅助", ("crosshair", "crosshair_color", "crosshair_width")),
                ("截图", "截图快捷操作", ("capture_quick_sticker_enabled", "capture_quick_sticker_shortcut",
                                     "capture_save_shortcut")),
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
            )
            for page_title, title, keys in expected:
                page = settings.page(page_title)
                box = next(box for box in page.findChildren(QGroupBox) if box.title() == title)
                for key in keys:
                    control = page.controls.get(key) or page.color_buttons[key]
                    self.assertTrue(box.isAncestorOf(control), (title, key))
            preview_groups = {
                "截图": {"assist": "定位辅助", "hover": "窗口与控件识别",
                         "selection": "遮罩与选区"},
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
                    self.assertEqual(set(screenshot_previews), {"assist", "hover", "selection"})
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

    def test_filename_template_has_visible_format_guide(self):
        from PySide6.QtWidgets import QFormLayout, QLabel

        with tempfile.TemporaryDirectory() as folder:
            settings = SettingsWindow(ConfigManager(Path(folder) / "settings.json"))
            page = settings.page("保存与输出")
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

    def test_color_button_swatch_is_larger_outside_compact_controls(self):
        from PySide6.QtCore import QSize
        from ui.widgets.color_button import ColorButton

        standard = ColorButton("#12ab34", lambda _color: None)
        compact = ColorButton("#12ab34", lambda _color: None, compact=True)
        self.assertEqual(standard.iconSize(), QSize(48, 28))
        self.assertEqual(compact.iconSize(), QSize(18, 18))
        standard.set_color("#abcdef")
        pixmap = standard.icon().pixmap(standard.iconSize())
        self.assertEqual(pixmap.size(), QSize(48, 28))
        self.assertEqual(pixmap.toImage().pixelColor(24, 14).name(), "#abcdef")

    def test_color_dialog_stays_on_top(self):
        from PySide6.QtWidgets import QColorDialog
        from ui.widgets.color_button import color_dialog

        dialog = color_dialog("#12ab34", None)
        self.assertTrue(dialog.windowFlags() & Qt.WindowStaysOnTopHint)
        self.assertEqual(dialog.windowTitle(), "选择颜色")
        dialog.deleteLater()

    def test_color_dialog_parents_to_stable_outermost_window(self):
        from PySide6.QtWidgets import QWidget
        from ui.widgets.color_button import color_dialog

        # 模拟“更多设置”结构：置顶遮罩(Tool) -> 工具栏 -> QMenu 弹出层 -> 色块。
        mask = QWidget()
        mask.setWindowFlags(Qt.WindowStaysOnTopHint | Qt.Tool)
        toolbar = QWidget(mask)
        popup = QWidget(toolbar)  # 实际是 QMenu，这里只验证 owner 回溯与稳定父级。
        button = QWidget(popup)
        dialog = color_dialog("#12ab34", button)
        # 不能挂在会随取色关闭的弹出层下；应挂到最外层稳定窗口（遮罩），随截图关闭且不丢外壳。
        self.assertIs(dialog.parent(), mask)
        self.assertTrue(dialog.windowFlags() & Qt.WindowStaysOnTopHint)
        dialog.deleteLater()
        mask.deleteLater()

    def test_color_dialog_keeps_normal_window_parent(self):
        from PySide6.QtWidgets import QWidget
        from ui.widgets.color_button import color_dialog

        window = QWidget()
        window.setWindowFlags(Qt.Window)
        button = QWidget(window)
        dialog = color_dialog("#12ab34", button)
        self.assertIs(dialog.parent(), window)
        self.assertTrue(dialog.windowFlags() & Qt.WindowStaysOnTopHint)
        dialog.deleteLater()
        window.deleteLater()

    def test_picked_color_updates_active_annotation_color(self):
        from PySide6.QtGui import QGuiApplication
        from config.config_manager import DEFAULTS

        editor = EditorWindow(Image.new("RGB", (40, 30), "white"), dict(DEFAULTS))
        editor.apply_picked_color("#123456")
        self.assertEqual(editor.settings["pen_color"], "#123456")
        self.assertEqual(editor.canvas.settings["pen_color"], "#123456")
        self.assertEqual(editor.toolbar.pen_color.color, "#123456")
        self.assertEqual(QGuiApplication.clipboard().text(), "#123456")
        pen_color = editor.settings["pen_color"]
        editor.toolbar.tool_buttons["marker"].click()
        editor.toolbar.tool_buttons["picker"].click()
        self.assertFalse(editor.toolbar.options_button.isEnabled())
        self.assertIn("background: #e7ebeb", editor.toolbar.options_button.styleSheet())
        self.assertIn("color: #747e80", editor.toolbar.options_button.styleSheet())
        editor.apply_picked_color("#abcdef")
        self.assertEqual(editor.settings["marker_color"], "#abcdef")
        self.assertEqual(editor.settings["pen_color"], pen_color)
        self.assertEqual(editor.toolbar.active_color_label.text(), "marker 颜色")
        editor.close()











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
        # 提示条贴顶后只有上下各 4px 内边距，取样避开文字区域。
        backdrop = image.pixelColor(400, 2)
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
        # 提示条只占一行：文字高度 + 上下各 4px 内边距。
        self.assertEqual(painter.drawRoundedRect.call_args.args[0].height(), 16 + 8)
        painter.drawText.reset_mock()
        paint_info(painter, QPoint(20, 20), QRect(10, 10, 40, 30))
        selected_hint = " ".join(call.args[-1] for call in painter.drawText.call_args_list)
        self.assertIn("拖动移动", selected_hint)
        self.assertIn("Enter/双击编辑", selected_hint)
        self.assertIn("右键双击保存", selected_hint)
        self.assertIn("四角/边中点缩放", selected_hint)

    def test_capture_element_size_badge_tracks_selected_rect(self):
        from screenshot.overlay_info import paint_info

        painter = Mock()
        painter.fontMetrics.return_value.horizontalAdvance.side_effect = lambda text: len(text) * 8
        painter.fontMetrics.return_value.height.return_value = 16
        painter.fontMetrics.return_value.elidedText.side_effect = lambda text, *_: text
        element_rect = QRect(120, 130, 80, 30)
        paint_info(painter, QPoint(0, 0), None, QRect(0, 0, 500, 400),
                   element_rect=element_rect, element_size=(160, 60),
                   element_border_color="#54E0C5", element_text_color="#F2FFFC",
                   element_background_color="#103B3A", element_font_size=18)

        self.assertEqual(painter.drawRoundedRect.call_count, 2)
        badge = painter.drawRoundedRect.call_args_list[1].args[0]
        self.assertLess(badge.bottom(), element_rect.top())
        label_calls = [call.args[-1] for call in painter.drawText.call_args_list]
        self.assertIn("160 x 60 px", label_calls)
        self.assertEqual(painter.setFont.call_args.args[0].pixelSize(), 18)
        self.assertEqual(painter.setPen.call_args.args[0].name(), "#f2fffc")

    def test_hover_style_presets_save_and_custom_edits_switch_style(self):
        from config.config_manager import ConfigManager
        from ui.settings_screenshot import ScreenshotPage

        with tempfile.TemporaryDirectory() as folder:
            config = ConfigManager(Path(folder) / "settings.json")
            page = ScreenshotPage(config, config.save)
            mode_control = page.controls["window_hover_fill_mode"]
            form = mode_control.parentWidget().layout()
            mode_row, _ = form.getWidgetPosition(mode_control)
            style_row, _ = form.getWidgetPosition(page.hover_style_combo)
            self.assertLess(mode_row, style_row)
            page.hover_style_combo.setCurrentIndex(
                page.hover_style_combo.findData("amber"))

            self.assertEqual(config.data["window_hover_border_color"], "#ffd17a")
            self.assertEqual(config.data["window_hover_text_color"], "#fff9ee")
            self.assertEqual(config.data["window_hover_badge_color"], "#402b13")
            self.assertEqual(config.data["window_hover_font_size"], 12)

            page.controls["window_hover_border_width"].setValue(4)
            self.assertEqual(config.data["window_hover_border_width"], 4)
            self.assertEqual(page.hover_style_combo.currentData(), "custom")
            page._set_hover_color("window_hover_text_color", "#E4F0FF")
            self.assertEqual(config.data["window_hover_text_color"], "#e4f0ff")
            page.reset_page()
            self.assertEqual(page.hover_style_combo.currentData(), "custom")
            self.assertEqual(config.data["window_hover_border_width"], 2)

    def test_capture_hovered_component_size_hint_uses_candidate_rect(self):
        from config.config_manager import DEFAULTS
        from screenshot.mask_window import MaskWindow

        bounds = {"left": 0, "top": 0, "width": 120, "height": 90}
        settings = dict(DEFAULTS, window_detection=False, inline_edit=False,
                        crosshair=False, magnifier=False)
        with patch("screenshot.mask_window.visible_windows", return_value=[]):
            mask = MaskWindow(Image.new("RGB", (120, 90), "white"), bounds,
                              [bounds], settings)
        view = mask.session.views[0]
        view.hover_rect = QRect(17, 21, 83, 37)

        hint_rect, hint_size = view.element_size_hint(None)

        self.assertEqual(hint_size, (83, 37))
        self.assertEqual(hint_rect, view.to_logical_rect(view.hover_rect).toRect())
        mask.close()

    def test_capture_operation_tips_use_available_monitor_width(self):
        from screenshot.overlay_info import paint_info

        painter = Mock()
        painter.fontMetrics.return_value.horizontalAdvance.side_effect = lambda text: len(text) * 12
        painter.fontMetrics.return_value.height.return_value = 16
        painter.fontMetrics.return_value.elidedText.side_effect = lambda text, *_: text
        paint_info(painter, QPoint(20, 20), QRect(10, 10, 40, 30), QRect(0, 0, 1920, 1080))
        wide_rect = painter.drawRoundedRect.call_args.args[0]
        self.assertLessEqual(wide_rect.width(), 1920 - 16)
        self.assertEqual(wide_rect.left(), (1920 - wide_rect.width()) // 2)
        # 宽度贴合文字而不是占满可用宽度，并紧贴显示器顶部。
        self.assertLess(wide_rect.width(), 1920 - 16)
        self.assertEqual(wide_rect.top(), 0)
        self.assertTrue(all(call.args[1] & Qt.AlignHCenter for call in painter.drawText.call_args_list))
        self.assertTrue(painter.fontMetrics.return_value.elidedText.called)
        painter.drawText.reset_mock()
        paint_info(painter, QPoint(20, 20), QRect(10, 10, 40, 30), QRect(0, 0, 600, 300))
        self.assertLessEqual(painter.drawRoundedRect.call_args.args[0].width(), 584)
        self.assertEqual(painter.drawText.call_count, 1)
        self.assertIn("Esc取消", " ".join(call.args[-1] for call in painter.drawText.call_args_list))

    def test_capture_tips_keep_long_coordinates_visible_in_inline_edit(self):
        from screenshot.overlay_info import paint_info

        painter = Mock()
        painter.fontMetrics.return_value.horizontalAdvance.side_effect = lambda text: len(text) * 12
        painter.fontMetrics.return_value.height.return_value = 16
        painter.fontMetrics.return_value.elidedText.side_effect = lambda text, *_: text
        paint_info(painter, QPoint(-123456, 987654), QRect(20, 20, 800, 600),
                   QRect(0, 0, 1920, 1080))
        rect = painter.drawRoundedRect.call_args.args[0]
        self.assertLessEqual(rect.width(), 1920 - 16)
        self.assertEqual(rect.left(), (1920 - rect.width()) // 2)
        self.assertIn("-123456, 987654", " ".join(call.args[-1] for call in painter.drawText.call_args_list))
        self.assertEqual(painter.drawText.call_count, 1)

    def test_capture_tips_width_follows_coordinates_and_selection_size(self):
        from screenshot.overlay_info import paint_info

        def bar_width(position, selection=None):
            painter = Mock()
            painter.device.return_value.width.return_value = 1920
            painter.device.return_value.height.return_value = 1080
            painter.fontMetrics.return_value.horizontalAdvance.side_effect = lambda text: len(text) * 12
            painter.fontMetrics.return_value.height.return_value = 16
            painter.fontMetrics.return_value.elidedText.side_effect = lambda text, *_: text
            paint_info(painter, position, selection, QRect(0, 0, 1920, 1080))
            return painter.drawRoundedRect.call_args.args[0].width()

        short = bar_width(QPoint(1, 2))
        # 坐标位数增加（含负坐标显示器）时提示条必须跟着变宽，不能是固定宽度。
        self.assertGreater(bar_width(QPoint(-123456, 987654)), short)
        # 选区尺寸同样计入文案，出现选区后条宽也会变化。
        self.assertGreater(bar_width(QPoint(1, 2), QRect(0, 0, 800, 600)), short)

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
        self.assertLess(image.pixelColor(500, 2).red(), 100)
        self.assertLess(image.pixelColor(1500, 2).red(), 100)

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
        from ui.settings_general import GeneralPage
        with tempfile.TemporaryDirectory() as folder:
            manager = ConfigManager(Path(folder) / "settings.json")
            page = GeneralPage(manager, manager.save)
            backend = page.controls["notification_backend"]
            self.assertEqual(manager.data["notification_backend"], "win11toast")
            backend.setCurrentIndex(backend.findData("legacy"))
            self.assertEqual(ConfigManager(manager.path).data["notification_backend"], "legacy")
            for label, key in (("截图完成通知", "capture_notification"),
                               ("保存成功通知", "save_notification"),
                               ("贴图通知", "sticker_notification")):
                control = page.controls[key]
                control.setChecked(False)
                self.assertFalse(ConfigManager(manager.path).data[key])
            self.assertTrue(ConfigManager(manager.path).data["bubble"])

    def test_capture_notification_backend_switches_between_win_toast_and_legacy(self):
        from ui.capture_notification import CaptureNotification

        preview = CaptureNotification(Image.new("RGB", (12, 8), "white"), backend="legacy")
        preview._show_local_preview = Mock()
        try:
            with patch("ui.native_toast.show_native_toast") as native:
                preview.show_preview()
                native.assert_not_called()
                preview._show_local_preview.assert_called_once()
        finally:
            preview.close()

        preview = CaptureNotification(Image.new("RGB", (12, 8), "white"))
        preview._show_local_preview = Mock()
        try:
            with patch("ui.native_toast.cache_toast_image", return_value=Path("toast.png")), \
                    patch("ui.native_toast.show_native_toast", return_value=True) as native:
                preview.show_preview()
                native.assert_called_once()
                preview._show_local_preview.assert_not_called()
        finally:
            preview.close()

    def test_notification_switches_gate_only_matching_messages(self):
        from types import SimpleNamespace
        from config.config_manager import DEFAULTS
        from main import Application
        app = Application.__new__(Application)
        app.config = SimpleNamespace(data=DEFAULTS.copy())
        app.config.data["notification_backend"] = "legacy"
        app.tray = Mock()
        app.logger = Mock()
        app.stickers = Mock()
        app.settings_window = Mock()
        app.editors = []
        app.capture_notice = None
        with patch("main.QApplication.beep"), patch("main.CaptureNotification") as preview, \
            patch("main.QTimer"):
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

            app.config.data["notification_backend"] = "win11toast"
            app.notification_bridge = Mock()
            with patch("ui.native_toast.show_native_toast", return_value=True) as native:
                app.notify("native message", target_path="capture.png")
                native.assert_called_once()
                app.tray.showMessage.assert_not_called()
                native.call_args.kwargs["on_click"]()
                app.notification_bridge.activated.emit.assert_called_with(
                    ("capture.png", None))
            app.config.data["notification_backend"] = "legacy"
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

    def test_native_toast_caches_image_and_dispatches_async_click(self):
        from types import SimpleNamespace
        from PySide6.QtGui import QImage
        from ui.native_toast import cache_toast_image, show_native_toast

        with tempfile.TemporaryDirectory() as folder:
            with patch("ui.native_toast.data_dir", return_value=Path(folder)):
                image = QImage(32, 20, QImage.Format_RGB32)
                image.fill(Qt.green)
                path = cache_toast_image(image)
            self.assertTrue(path.is_file())
            self.assertFalse(QImage(str(path)).isNull())

            toast = Mock()
            click = Mock()
            failed = Mock()
            thread = Mock()
            with patch("ui.native_toast.os.name", "nt"), \
                    patch.dict("sys.modules", {"win11toast": SimpleNamespace(toast=toast)}), \
                    patch("ui.native_toast.Thread", return_value=thread) as create_thread:
                self.assertTrue(show_native_toast("截图", "1 张", path, click, failed))
            create_thread.assert_called_once()
            thread.start.assert_called_once_with()
            create_thread.call_args.kwargs["target"]()
            toast.assert_called_once_with(
                "截图", "1 张", image={"src": str(path.resolve()), "placement": "hero"},
                on_click=click)
            failed.assert_not_called()

    def test_native_toast_oserror_calls_fallback_without_traceback(self):
        from types import SimpleNamespace
        from ui.native_toast import show_native_toast

        error = OSError("notification platform unavailable")
        toast = Mock(side_effect=error)
        failed = Mock()
        thread = Mock()
        with patch("ui.native_toast.os.name", "nt"), \
                patch.dict("sys.modules", {"win11toast": SimpleNamespace(toast=toast)}), \
                patch("ui.native_toast.Thread", return_value=thread) as create_thread:
            self.assertTrue(show_native_toast("截图", on_failed=failed))
        with self.assertLogs("screensnap", level="DEBUG") as captured:
            create_thread.call_args.kwargs["target"]()
        failed.assert_called_once_with(error)
        self.assertNotIn("Traceback", "\n".join(captured.output))

    def test_capture_notification_uses_native_toast_and_latest_target(self):
        from ui.capture_notification import CaptureNotification

        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "capture.png"
            notification = CaptureNotification(Image.new("RGB", (40, 20), "green"))
            activated = []
            notification.file_activated.connect(activated.append)
            callback = Mock()
            with patch("ui.native_toast.cache_toast_image", return_value=Path(folder) / "toast.png"), \
                    patch("ui.native_toast.show_native_toast", return_value=True) as show_toast:
                notification.show_preview()
            show_toast.assert_called_once()
            self.assertFalse(notification.isVisible())
            notification.set_target_path(target)
            callback = show_toast.call_args.args[3]
            callback()
            self.app.processEvents()
            self.assertEqual(activated, [str(target)])
            notification.close()

    def test_capture_notification_falls_back_when_native_toast_unavailable(self):
        from ui.capture_notification import CaptureNotification

        notification = CaptureNotification(Image.new("RGB", (40, 20), "green"))
        with tempfile.TemporaryDirectory() as folder, \
                patch("ui.native_toast.cache_toast_image", return_value=Path(folder) / "toast.png"), \
                patch("ui.native_toast.show_native_toast", return_value=False):
            notification.show_preview()
        self.assertTrue(notification.isVisible())
        notification.close()

    def test_notification_click_reveals_target_file(self):
        from types import SimpleNamespace
        from main import Application
        from ui.capture_notification import CaptureNotification

        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "capture.png"
            target.write_bytes(b"image")
            notification = CaptureNotification(
                Image.new("RGB", (40, 20), "green"), target_path=target)
            activated = []
            notification.file_activated.connect(activated.append)
            notification.show()
            self.app.processEvents()
            QTest.mouseClick(notification, Qt.LeftButton, pos=QPoint(10, 10))
            self.assertEqual(activated, [str(target)])
            notification.close()

            app = Application.__new__(Application)
            app.config = SimpleNamespace(data={"open_notification_file": True})
            app._notification_target_path = str(target)
            app._notification_fallback = None
            with patch("main.subprocess.Popen") as launch:
                self.assertTrue(app.open_notification_target())
            launch.assert_called_once_with(["explorer.exe", f"/select,{target.resolve()}"])

            app.config.data["open_notification_file"] = False
            with patch("main.subprocess.Popen") as launch:
                self.assertFalse(app.open_notification_target())
            launch.assert_not_called()

            app._notification_target_path = None
            app._notification_fallback = "sticker_panel"
            app.open_sticker_panel = Mock()
            self.assertTrue(app.open_notification_target())
            app.open_sticker_panel.assert_called_once_with()
            self.assertFalse(app.open_notification_target(str(target) + ".missing"))
            app.open_sticker_panel.assert_called_once_with()

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
        self.assertNotEqual(clipboard.icon().pixmap(24, 24).toImage(),
                    open_image.icon().pixmap(24, 24).toImage())
        self.assertIn("Ctrl+Alt+V", clipboard.text())
        self.assertIn("Ctrl+Alt+O", open_image.text())
        self.assertTrue(all(action.toolTip().strip() for action in actions.values()))
        self.assertIn("Ctrl+Shift+F1", next(iter(actions)))
        clipboard.trigger()
        open_image.trigger()
        self.assertEqual(calls, ["clipboard", "open"])
        menu = make_tray_menu(self.app, lambda: None, lambda: None, lambda: None,
                      hotkeys={"open_sticker_file": "ctrl+alt+n"},
                      open_sticker=lambda: calls.append("sticker-hotkey"))
        new_sticker = next(action for action in menu.actions()
                   if action.text().startswith("从文件打开新贴图"))
        self.assertIn("Ctrl+Alt+N", new_sticker.text())
        new_sticker.trigger()
        self.assertEqual(calls[-1], "sticker-hotkey")
        menu = make_tray_menu(self.app, lambda: None, lambda: None, lambda: None,
                              open_sticker=lambda: calls.append("sticker"))
        next(action for action in menu.actions()
             if action.text() == "从文件打开新贴图").trigger()
        self.assertEqual(calls[-1], "sticker")

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
        self.assertTrue(validate({})["sticker_selection_effect_enabled"])
        self.assertEqual(validate({})["sticker_selection_effect_strength"], 30)
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
        self.assertFalse(sticker.shadow_enabled)
        self.assertFalse(sticker.state()["shadow"])
        sticker.close()

        with tempfile.TemporaryDirectory() as folder:
            manager = ConfigManager(Path(folder) / "settings.json")
            page = StickerPage(manager, manager.save)
            self.assertIn("sticker_border_enabled", page.controls)
            self.assertIn("sticker_selection_effect_enabled", page.controls)
            self.assertTrue(page.controls["sticker_selection_effect_enabled"].isChecked())
            page.controls["sticker_border_width"].setValue(5)
            self.assertEqual(ConfigManager(manager.path).data["sticker_border_width"], 5)
            page.color_buttons["sticker_border_color"].changed("#abcdef")
            self.assertEqual(ConfigManager(manager.path).data["sticker_border_color"], "#abcdef")
            page.close()

    def test_sticker_border_follows_rounded_corners_and_stays_square(self):
        from PySide6.QtCore import QRectF
        from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath
        from config.config_manager import DEFAULTS

        def build(round_corners, width=60, height=40, radius=12):
            image = QImage(width, height, QImage.Format_ARGB32)
            image.fill(QColor(0, 0, 0, 0))
            painter = QPainter(image)
            painter.setRenderHint(QPainter.Antialiasing, True)
            path = QPainterPath()
            if round_corners:
                path.addRoundedRect(QRectF(0, 0, width, height), radius, radius)
            else:
                path.addRect(QRectF(0, 0, width, height))
            painter.fillPath(path, QColor("#ffffff"))
            painter.end()
            return image

        settings = dict(DEFAULTS, sticker_border_width=4, sticker_border_color="#168cff",
                        sticker_shadow_enabled=False,
                        sticker_background_mode="transparent")
        for round_corners in (True, False):
            sticker = StickerItem(build(round_corners), settings=settings)
            sticker.resize(sticker.window_size())
            sticker.show()
            self.app.processEvents()
            rect = sticker.image_rect()
            radius = sticker.border_corner_radius(rect)
            painted = sticker.grab().toImage()
            # 描边完整落在图像内侧，透明模式的输入遮罩不会把它裁掉。
            self.assertEqual(painted.pixelColor(
                rect.center().x(), rect.top() + 1).name(), "#168cff")
            if round_corners:
                self.assertGreater(radius, 0.5)
                # 圆角外侧不再出现方形描边，描边沿圆弧走。
                self.assertEqual(painted.pixelColor(rect.left(), rect.top()).alpha(), 0)
                arc = painted.pixelColor(rect.left() + 5, rect.top() + 5)
                self.assertGreater(arc.blue(), 150)
                self.assertGreater(arc.blue(), arc.red() + 60)
            else:
                self.assertEqual(radius, 0.0)
                self.assertEqual(painted.pixelColor(
                    rect.left() + 1, rect.top() + 1).name(), "#168cff")
            sticker.close()

    def test_sticker_shadow_and_glow_follow_rounded_corners(self):
        """阴影与选中光晕同样跟随圆角，且不会被透明模式的输入遮罩裁掉。"""
        from PySide6.QtCore import QRectF
        from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath
        from config.config_manager import DEFAULTS

        def build(round_corners, width=60, height=40, radius=12):
            image = QImage(width, height, QImage.Format_ARGB32)
            image.fill(QColor(0, 0, 0, 0))
            painter = QPainter(image)
            painter.setRenderHint(QPainter.Antialiasing, True)
            path = QPainterPath()
            if round_corners:
                path.addRoundedRect(QRectF(0, 0, width, height), radius, radius)
            else:
                path.addRect(QRectF(0, 0, width, height))
            painter.fillPath(path, QColor("#ffffff"))
            painter.end()
            return image

        for round_corners in (True, False):
            settings = dict(DEFAULTS, sticker_border_width=2,
                            sticker_shadow_enabled=True, sticker_shadow_strength=35,
                            sticker_background_mode="light_checker")
            sticker = StickerItem(build(round_corners), settings=settings)
            sticker.resize(sticker.window_size())
            sticker.show()
            self.app.processEvents()
            rect = sticker.image_rect()
            offset = max(1, round(35 / 20))
            painted = sticker.grab().toImage()
            # 阴影需可见（透明模式也会裁掉），且沿贴图形状向右下偏移：右下 padding 处应有投影。
            bottom_right = painted.pixelColor(rect.right() + 1, rect.bottom() + 1)
            self.assertLess(bottom_right.red(), 200)
            top_left = painted.pixelColor(rect.left() + offset, rect.top() + offset)
            if round_corners:
                # 圆角处不应露出方形阴影角，左上 padding 应露出背景底色而非阴影。
                self.assertGreaterEqual(top_left.red(), 220)
            sticker.close()

            sticker = StickerItem(build(round_corners), settings=dict(
                DEFAULTS, sticker_border_enabled=True, sticker_shadow_enabled=True,
                sticker_background_mode="transparent"))
            sticker.resize(sticker.window_size())
            sticker.show()
            sticker.selection_effect_active = True
            sticker.update()
            self.app.processEvents()
            rect = sticker.image_rect()
            painted = sticker.grab().toImage()
            glow = sum(1 for y in range(rect.top(), rect.bottom())
                       for x in range(rect.left(), rect.right())
                       if painted.pixelColor(x, y).blue() >
                       painted.pixelColor(x, y).red() + 30)
            self.assertGreater(glow, 0)
            sticker.close()

    def test_transparent_checker_tiles_are_denser_in_previews_and_stickers(self):
        from PySide6.QtGui import QColor, QImage
        from core.constants import CHECKER_TILE_SIZE, checker_tile_size

        self.assertEqual(CHECKER_TILE_SIZE, 8)
        self.assertEqual(checker_tile_size(2), 16)
        self.assertEqual(checker_tile_size(0.25), 4)
        image = QImage(24, 24, QImage.Format_ARGB32)
        image.fill(Qt.transparent)
        sticker = StickerItem(image, settings={
            "sticker_border_enabled": False,
            "sticker_shadow_enabled": False,
            "sticker_background_mode": "dark_checker",
        })
        try:
            rendered = sticker.grab().toImage()
            self.assertEqual(rendered.pixelColor(0, 0).name(), "#252525")
            self.assertEqual(rendered.pixelColor(7, 0).name(), "#252525")
            self.assertEqual(rendered.pixelColor(8, 0).name(), "#3b3b3b")
            self.assertEqual(rendered.pixelColor(16, 0).name(), "#252525")
        finally:
            sticker.close()

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

    def test_paste_latest_activates_recent_sticker_when_all_history_is_open(self):
        from PySide6.QtGui import QImage, QColor
        from config.config_manager import DEFAULTS

        with tempfile.TemporaryDirectory() as folder:
            settings = dict(DEFAULTS, auto_dir=folder)
            source = Path(folder) / "latest.png"
            image = QImage(24, 18, QImage.Format_RGB32)
            image.fill(QColor("#23bc58"))
            self.assertTrue(image.save(str(source), "PNG"))
            manager = StickerManager(settings)
            item = manager.add(QImage(str(source)), source)
            try:
                self.assertTrue(manager.paste_latest())
                self.assertIs(manager.active_sticker, item)
                self.assertTrue(item.isVisible())
            finally:
                manager.close_all()
                manager._persist_timer.stop()

    def test_sticker_manager_shrinks_oversized_image_without_mutating_input(self):
        from PySide6.QtGui import QImage, QColor
        from config.config_manager import DEFAULTS

        with tempfile.TemporaryDirectory() as folder, \
                patch("sticker.sticker_manager.data_dir", return_value=Path(folder)):
            image = QImage(5000, 1000, QImage.Format_RGB32)
            image.fill(QColor("#23bc58"))
            manager = StickerManager(DEFAULTS)
            item = manager.add(image, show=False)
            try:
                self.assertEqual(image.size().toTuple(), (5000, 1000))
                self.assertEqual(item.pixmap.size().toTuple(), (4096, 819))
                self.assertEqual(QImage(item.source).size().toTuple(), (4096, 819))
            finally:
                item.close()
                manager._persist_timer.stop()

    def test_single_instance_lock_rejects_duplicate_and_releases(self):
        from main import acquire_single_instance_lock

        with tempfile.TemporaryDirectory() as folder:
            lock_path = Path(folder) / "screensnap.lock"
            first_lock = acquire_single_instance_lock(lock_path)
            self.assertIsNotNone(first_lock)
            try:
                self.assertIsNone(acquire_single_instance_lock(lock_path))
            finally:
                first_lock.unlock()

            next_lock = acquire_single_instance_lock(lock_path)
            self.assertIsNotNone(next_lock)
            next_lock.unlock()

    def test_sticker_manager_restores_position_and_scale_before_show(self):
        import json
        from PySide6.QtGui import QImage, QColor
        from config.config_manager import DEFAULTS
        from sticker.sticker_item import StickerItem

        with tempfile.TemporaryDirectory() as folder, \
                patch("sticker.sticker_manager.data_dir", return_value=Path(folder)):
            source = Path(folder) / "source.png"
            image = QImage(80, 40, QImage.Format_RGB32)
            image.fill(QColor("#23bc58"))
            self.assertTrue(image.save(str(source), "PNG"))
            state = {
                "id": "restore-test", "source": str(source), "x": 321, "y": 246,
                "scale": 2.5, "opacity": 1.0, "locked": False,
            }
            (Path(folder) / "stickers.json").write_text(
                json.dumps([state]), encoding="utf-8")
            manager = StickerManager(DEFAULTS)
            shown = []
            original_show = StickerItem.show

            def record_show(item):
                shown.append((item.pos(), item.scale_factor, item.size()))
                original_show(item)

            with patch.object(StickerItem, "show", record_show):
                manager.restore()

            self.assertEqual(len(shown), 1)
            position, scale, size = shown[0]
            self.assertEqual((position.x(), position.y()), (321, 246))
            self.assertEqual(scale, 2.5)
            self.assertEqual(size, manager.items[0].window_size())
            manager.close_all()

    def test_sticker_recycle_bin_keeps_closed_stickers_and_restores(self):
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QImage, QColor
        from config.config_manager import DEFAULTS

        with tempfile.TemporaryDirectory() as folder, \
                patch("sticker.sticker_manager.data_dir", return_value=Path(folder)):
            manager = StickerManager(DEFAULTS)
            item = manager.add(QImage(12, 8, QImage.Format_RGB32), show=False)
            self.assertEqual(len(manager.items), 1)
            item.close()
            # 进入回收站时关闭自动销毁，窗口保留以便之后恢复（修复“恢复后不显示但发通知”）。
            self.assertFalse(item.testAttribute(Qt.WA_DeleteOnClose))
            self.assertEqual(len(manager.items), 0)
            self.assertEqual(len(manager.recycle_items()), 1)
            manager.recycle_restore(item)
            self.assertEqual(len(manager.items), 1)
            self.assertEqual(len(manager.recycle_items()), 0)
            self.assertTrue(item.isVisible())
            # 恢复后重新开启关闭即销毁，避免再次关闭时泄漏。
            self.assertTrue(item.testAttribute(Qt.WA_DeleteOnClose))
            manager.empty_recycle()
            self.assertEqual(len(manager.recycle_items()), 0)
            manager.close_all()

    def test_recycle_window_builds_with_recycled_stickers(self):
        from PySide6.QtGui import QImage
        from config.config_manager import DEFAULTS
        from ui.recycle_window import RecycleWindow

        with tempfile.TemporaryDirectory() as folder, \
                patch("sticker.sticker_manager.data_dir", return_value=Path(folder)):
            manager = StickerManager(DEFAULTS)
            item = manager.add(QImage(12, 8, QImage.Format_RGB32), show=False)
            item.close()
            self.assertEqual(len(manager.recycle_items()), 1)
            # 构造回收站窗口会触发 RecycleRow 构建；历史上曾因 Qt6 已移除的
            # QStyle.SP_DialogTrashIcon 而崩溃，导致回收站始终打不开。
            window = RecycleWindow(manager, dict(DEFAULTS))
            self.assertEqual(window.list_layout.count(), 2)  # 1 行 + 1 个 stretch
            window.close()
            manager.close_all()

    def test_recycle_delete_removes_item(self):
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QImage
        from config.config_manager import DEFAULTS
        from sticker.sticker_item import StickerItem

        with tempfile.TemporaryDirectory() as folder, \
                patch("sticker.sticker_manager.data_dir", return_value=Path(folder)):
            manager = StickerManager(DEFAULTS)
            item = manager.add(QImage(12, 8, QImage.Format_RGB32), show=False)
            item.close()
            self.assertEqual(len(manager.recycle_items()), 1)
            manager.recycle_delete(item)
            self.assertEqual(len(manager.recycle_items()), 0)
            manager.close_all()

    def test_annotation_font_size_and_arrow_style_persist(self):
        from config.config_manager import DEFAULTS
        from config.config_manager import TOOL_WIDTH_KEYS

        with tempfile.TemporaryDirectory() as folder:
            manager = ConfigManager(Path(folder) / "settings.json")
            settings = SettingsWindow(manager)
            editor = EditorWindow(Image.new("RGB", (40, 30), "white"), manager.data)
            editor.setting_changed.connect(settings.set_annotation_setting)
            page = settings.page("编辑器")
            for key in (*TOOL_WIDTH_KEYS.values(), "arrow_style", "rect_style", "ellipse_style",
                        "marker_opacity", "font", "font_size", "text_alignment", "line_spacing",
                        "mosaic_mode", "mosaic_size", "annotation_tool"):
                self.assertIn(key, page.controls)
            self.assertIn("pen_color", page.color_buttons)
            for index, (tool, key) in enumerate(TOOL_WIDTH_KEYS.items(), 7):
                editor.toolbar.tool_buttons[tool].click()
                editor.toolbar.pen_width.setValue(index)
                self.assertEqual(manager.data[key], index)
            editor.toolbar.font_size.setValue(31)
            editor.toolbar.tool_buttons["arrow"].click()
            editor.toolbar.choice_buttons["arrow_style"]["open"].click()
            editor.toolbar.choice_buttons["arrow_style"]["double_filled"].click()
            self.assertEqual(manager.data["arrow_style"], "double_filled")
            editor.toolbar.choice_buttons["arrow_style"]["double"].click()
            self.assertEqual(manager.data["arrow_style"], "double")
            editor.toolbar.tool_buttons["text"].click()
            editor.toolbar.choice_buttons["text_alignment"]["center"].click()
            editor.toolbar.tool_buttons["mosaic"].click()
            self.assertEqual(manager.data["text_alignment"], "center")
            settings.flush_persist()
            restored = ConfigManager(manager.path).data
            for index, key in enumerate(TOOL_WIDTH_KEYS.values(), 7):
                self.assertEqual(restored[key], index)
            self.assertEqual(restored["arrow_style"], "double")
            self.assertEqual(restored["text_alignment"], "center")
            self.assertEqual(restored["font_size"], 31)
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
            with patch("main.QFileDialog.getOpenFileNames",
                       return_value=([str(path)], "图片文件")):
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
            self.app.processEvents()

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
            settings["capture_after_selection"] = "edit"
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
            self.assertEqual(color.color, "#000000")
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
        mask.selected.connect(lambda images, positions: selected.append((images, positions)))
        mask.last_region.connect(regions.append)
        mask.selection.rects.append(QRect(20, 10, 40, 20))
        mask.complete()
        self.assertEqual(selected[0][0][0][0].size, (40, 20))
        self.assertEqual(selected[0][1], [QPoint(10, 5)])
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
        mask.selected.connect(lambda images, positions: selected.append((images, positions)))
        mask.last_region.connect(regions.append)
        mask.selection.rects.append(QRect(100, 2210, 200, 100))
        mask.complete()
        self.assertEqual(selected[0][0][0][0].size, (200, 100))
        self.assertEqual(regions[0], [100, 2210, 200, 100])

    def test_mask_double_click_enters_inline_editor(self):
        from config.config_manager import DEFAULTS
        from screenshot.mask_window import MaskWindow

        with tempfile.TemporaryDirectory() as folder:
            bounds = {"left": 0, "top": 0, "width": 120, "height": 80}
            settings = {**DEFAULTS, "auto_dir": folder, "inline_edit": True,
                        "crosshair": False, "magnifier": False, "sound": False,
                        "bubble": False}
            with patch("screenshot.mask_window.visible_windows", return_value=[]):
                mask = MaskWindow(Image.new("RGB", (120, 80), "blue"), bounds,
                                  [bounds], settings)
            try:
                mask.selection.rects.append(QRect(10, 10, 40, 30))
                mask.show()
                self.app.processEvents()
                QTest.mouseDClick(mask, Qt.LeftButton, Qt.NoModifier, QPoint(20, 20))
                self.assertIsNotNone(mask.session.inline_editor)
                self.assertTrue(mask.session.inline_editor.canvas.isVisible())
            finally:
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
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0][0][0][0].size, (40, 30))
        self.assertFalse(saved)
        self.assertIsNone(mask.session.inline_editor)

    def test_mask_right_double_click_saves_without_inline_editor(self):
        from config.config_manager import DEFAULTS
        from screenshot.mask_window import MaskWindow

        bounds = {"left": 0, "top": 0, "width": 120, "height": 80}
        settings = {**DEFAULTS, "inline_edit": True,
                    "capture_after_selection": "edit",
                    "crosshair": False, "magnifier": False, "sound": False}
        with patch("screenshot.mask_window.visible_windows", return_value=[]):
            mask = MaskWindow(Image.new("RGB", (120, 80), "blue"), bounds,
                              [bounds], settings)
        requested = []
        selected = []
        mask.save_requested.connect(lambda images, positions: requested.append((images, positions)))
        mask.selected.connect(lambda images, positions: selected.append((images, positions)))
        mask.selection.rects.append(QRect(10, 10, 40, 30))
        mask.show()
        self.app.processEvents()
        QTest.mouseDClick(mask, Qt.RightButton, Qt.NoModifier, QPoint(20, 20))
        self.assertEqual(len(requested), 1)
        self.assertEqual(requested[0][0][0][0].size, (40, 30))
        self.assertFalse(selected)
        self.assertIsNone(mask.session.inline_editor)

    def test_capture_save_shortcut_is_single_key_and_configurable(self):
        from types import SimpleNamespace
        from PySide6.QtGui import QKeySequence
        from config.config_manager import DEFAULTS, validate
        from ui.settings_screenshot import ScreenshotPage

        config = SimpleNamespace(data=dict(DEFAULTS))
        page = ScreenshotPage(config, lambda: None)
        shortcut = page.controls["capture_save_shortcut"]
        self.assertEqual(shortcut.keySequence().toString(), "S")
        shortcut.setKeySequence(QKeySequence("K"))
        self.assertEqual(config.data["capture_save_shortcut"], "K")
        self.assertEqual(validate({"capture_save_shortcut": "Alt+S"})[
            "capture_save_shortcut"], "Alt+S")
        with self.assertRaises(ValueError):
            validate({"capture_save_shortcut": "Ctrl+K, Ctrl+S"})

    def test_capture_save_shortcut_saves_selected_region_directly(self):
        from config.config_manager import DEFAULTS
        from screenshot.mask_window import MaskWindow

        bounds = {"left": 0, "top": 0, "width": 120, "height": 80}
        settings = {**DEFAULTS, "capture_save_shortcut": "S",
                    "crosshair": False, "magnifier": False, "sound": False}
        with patch("screenshot.mask_window.visible_windows", return_value=[]):
            mask = MaskWindow(Image.new("RGB", (120, 80), "blue"), bounds,
                              [bounds], settings)
        saved = []
        selected = []
        regions = []
        mask.save_requested.connect(lambda images, positions: saved.append((images, positions)))
        mask.selected.connect(lambda images, positions: selected.append((images, positions)))
        mask.last_region.connect(regions.append)
        mask.selection.rects.append(QRect(10, 10, 40, 30))
        original_rect = QRect(mask.selection.rects[0])
        mask.show()
        self.app.processEvents()
        QTest.keyClick(mask, Qt.Key_S)
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0][0][0][0].size, (40, 30))
        self.assertEqual(regions, [[original_rect.x(), original_rect.y(),
                        original_rect.width(), original_rect.height()]])
        self.assertFalse(selected)
        self.assertIsNone(mask.session.inline_editor)

    def test_capture_after_selection_defaults_to_edit_and_is_configurable(self):
        from types import SimpleNamespace
        from config.config_manager import DEFAULTS, validate
        from main import Application

        self.assertEqual(DEFAULTS["capture_after_selection"], "edit")
        self.assertEqual(validate({"capture_after_selection": "edit"})[
            "capture_after_selection"], "edit")
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

    def test_capture_after_selection_copy_writes_clipboard_and_closes(self):
        from unittest.mock import Mock, patch
        from config.config_manager import DEFAULTS
        from screenshot.mask_window import MaskWindow

        bounds = {"left": 0, "top": 0, "width": 160, "height": 100}
        monitors = [{"left": 0, "top": 0, "width": 160, "height": 100}]
        screen_infos = [{"geometry": QRect(0, 0, 160, 100), "dpr": 1.0}]
        settings = {**DEFAULTS, "crosshair": False, "magnifier": False,
                    "sound": False, "capture_after_selection": "copy"}
        clipboard = Mock()
        with patch("screenshot.mask_window.visible_windows", return_value=[]), \
                patch("core.dpi.DisplayMapper.collect_screen_infos", return_value=screen_infos), \
                patch("PySide6.QtGui.QGuiApplication.clipboard", return_value=clipboard):
            mask = MaskWindow(Image.new("RGB", (160, 100), "blue"), bounds,
                              monitors, settings)
            copy_done = Mock()
            for view in mask.session.views:
                view.copy_done.connect(copy_done)
            mask.show()
            view = mask.session.views[0]
            QTest.mousePress(view, Qt.LeftButton, Qt.NoModifier, QPoint(10, 10))
            QTest.mouseMove(view, QPoint(60, 50))
            QTest.mouseRelease(view, Qt.LeftButton, Qt.NoModifier, QPoint(60, 50))
            self.app.processEvents()
            QTest.mouseDClick(view, Qt.LeftButton, Qt.NoModifier, QPoint(35, 30))
            self.app.processEvents()
        copy_done.assert_called_once()
        clipboard.setMimeData.assert_called_once()
        self.assertFalse(mask.isVisible())

    def test_annotation_sequence_supports_all_shapes(self):
        from editor.annotation_items import AnnotationSequenceItem
        from PySide6.QtGui import QImage, QPainter
        for shape in ("circle", "square", "triangle", "diamond",
                      "pentagon", "hexagon", "star"):
            item = AnnotationSequenceItem(3, "#168cff", "#ffffff", 16, shape, "")
            rect = item.boundingRect()
            self.assertAlmostEqual(rect.width(), rect.height())
            image = QImage(40, 40, QImage.Format_ARGB32)
            image.fill(0)
            painter = QPainter(image)
            item.paint(painter, None, None)
            painter.end()

    def test_sequence_preset_applies_shape_and_colors(self):
        from editor.annotation_items import apply_sequence_preset
        settings = {}
        apply_sequence_preset(settings, "green_star")
        self.assertEqual(settings["sequence_shape"], "star")
        self.assertEqual(settings["sequence_fill_color"], "#2e9e5b")
        # custom 预设不覆盖任何单项，原有组合保持不变。
        apply_sequence_preset(settings, "custom")
        self.assertEqual(settings["sequence_shape"], "star")

    def test_default_sequence_settings(self):
        from config.config_manager import DEFAULTS, validate
        self.assertEqual(DEFAULTS["filename"], "ScreenSnap_%Y%m%d_%H%M%S")
        self.assertEqual(DEFAULTS["sequence_shape"], "circle")
        self.assertIn("sequence_fill_color", DEFAULTS)
        self.assertIn("sequence_text_color", DEFAULTS)
        data = validate({})

    def test_capture_picker_hint_visibility(self):
        from unittest.mock import Mock, patch
        from config.config_manager import DEFAULTS
        from PySide6.QtCore import QPoint, QRect
        from screenshot.overlay_info import paint_info
        from screenshot.mask_window import MaskWindow

        # 取色提示已并入顶部提示栏（paint_info），不再有独立 QLabel。
        # 1) 取色模式下，paint_info 顶部提示栏始终显示取色说明（即便尚未取到色值）。
        painter = Mock()
        painter.fontMetrics.return_value.horizontalAdvance.side_effect = lambda text: len(text) * 8
        painter.fontMetrics.return_value.height.return_value = 16
        painter.fontMetrics.return_value.elidedText.side_effect = lambda text, *_: text
        paint_info(painter, QPoint(20, 20), None, QRect(0, 0, 800, 100), picker_mode=True)
        drawn = " ".join(call.args[-1] for call in painter.drawText.call_args_list)
        self.assertIn("取色", drawn)
        self.assertIn("退出取色", drawn)
        self.assertNotIn("拖拽框选", drawn)
        self.assertNotIn("拖动移动", drawn)
        # 2) 非取色模式仍显示原始操作说明。
        painter.drawText.reset_mock()
        paint_info(painter, QPoint(20, 20), None, QRect(0, 0, 800, 100))
        drawn = " ".join(call.args[-1] for call in painter.drawText.call_args_list)
        self.assertIn("拖拽框选", drawn)
        # 非取色模式顶部提示栏应说明如何进入取色模式。
        self.assertIn("取色", drawn)
        # 3) 真实遮罩不再持有独立 picker_hint 控件（避免与提示栏重叠）。
        bounds = {"left": 0, "top": 0, "width": 160, "height": 100}
        monitors = [{"left": 0, "top": 0, "width": 160, "height": 100}]
        screen_infos = [{"geometry": QRect(0, 0, 160, 100), "dpr": 1.0}]
        settings = {**DEFAULTS, "crosshair": False, "magnifier": False, "sound": False}
        with patch("screenshot.mask_window.visible_windows", return_value=[]), \
                patch("core.dpi.DisplayMapper.collect_screen_infos", return_value=screen_infos):
            mask = MaskWindow(Image.new("RGB", (160, 100), "blue"), bounds,
                              monitors, settings)
        self.assertFalse(hasattr(mask, "picker_hint"))
        mask.close()



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
            view.selected.connect(lambda images: Application.handle_capture_selection(app, images))
        mask.show()
        secondary = mask.session.views[1]
        for view, start, end in (
                (mask, QPoint(10, 10), QPoint(40, 30)),
                (secondary, QPoint(10, 40), QPoint(50, 70))):
            QTest.mousePress(view, Qt.LeftButton, Qt.NoModifier, start)
            QTest.mouseMove(view, end)
            QTest.mouseRelease(view, Qt.LeftButton, Qt.NoModifier, end)
        self.assertEqual(len(mask.selection.rects), 2)
        self.app.processEvents()
        QTest.mouseDClick(secondary, Qt.LeftButton, Qt.NoModifier, QPoint(30, 55))
        app.edit_images.assert_called_once()
        self.assertEqual(len(app.edit_images.call_args.args[0]), 2)
        app.save_capture_images.assert_not_called()
        self.assertIsNone(mask.session.inline_editor)

    def test_capture_editor_is_shown_before_initial_save_and_uses_one_notice(self):
        from types import SimpleNamespace
        from main import Application

        events = []

        class FakeSignal:
            def connect(self, callback):
                self.callback = callback

            def emit(self, *args):
                self.callback(*args)

        class FakeEditor:
            def __init__(self, image, settings, alternate, from_capture):
                self.image_saved = FakeSignal()
                self.sticker_requested = Mock()
                self.recapture_requested = Mock()
                self.close_all_requested = Mock()
                self.status = Mock()
                self.tool_color_changed = Mock()
                self.setting_changed = Mock()
                self.destroyed = Mock()
                self.toolbar = SimpleNamespace(set_active_tool=Mock())
                self.canvas = SimpleNamespace(tool="pen")
                self.attributes = {}
                self.save_calls = []

            def setAttribute(self, key, value=True):
                self.attributes[key] = value

            def show(self):
                events.append("show")

            def save(self, **kwargs):
                events.append("save")
                self.save_calls.append(kwargs)
                self.image_saved.emit("capture.png", Image.new("RGB", (4, 4), "red"))

        class FakeTimer:
            def __init__(self, parent):
                self.timeout = Mock()

            def setSingleShot(self, enabled):
                self.single_shot = enabled

            def start(self, interval):
                events.append(("timer", interval))

        app = Application.__new__(Application)
        app.config = SimpleNamespace(data={"bubble": False, "capture_notification": False,
                                           "sound": False})
        app.settings_window = SimpleNamespace(set_tool_color=Mock(), set_annotation_setting=Mock())
        app.editors = []
        app.logger = SimpleNamespace(info=Mock())
        app.capture_notice = None
        app.saved = Mock()
        image = Image.new("RGB", (4, 4), "red")
        with patch("main.EditorWindow", FakeEditor), patch("main.QTimer", FakeTimer):
            Application.edit_images(app, [(image, None)], from_capture=True)
        editor = app.editors[0]
        self.assertEqual(events, ["show", ("timer", 0)])
        self.assertTrue(editor.initial_capture_save_pending)
        Application.save_initial_capture(app, editor)
        self.assertEqual(events, ["show", ("timer", 0), "save"])
        self.assertEqual(editor.save_calls, [{"automatic": True}])
        self.assertFalse(editor.initial_capture_save_pending)
        app.saved.assert_called_once_with("capture.png", image, notify=False)

    def test_capture_initial_save_runs_after_real_editor_is_shown(self):
        from types import SimpleNamespace
        from PySide6.QtCore import QSize
        from config.config_manager import DEFAULTS
        from main import Application

        with tempfile.TemporaryDirectory() as folder:
            settings = {**DEFAULTS, "auto_dir": folder, "bubble": False,
                        "capture_notification": False, "sound": False}
            app = Application.__new__(Application)
            app.config = SimpleNamespace(data=settings)
            app.settings_window = SimpleNamespace(set_tool_color=Mock(),
                                                  set_annotation_setting=Mock())
            app.editors = []
            app.logger = Mock()
            app.capture_notice = None
            app.saved = Mock()
            app.initial_save_failed = Mock()
            app.close_all_editors = Mock()
            app.add_sticker = Mock()
            app.restart_capture = Mock()
            app.notify = Mock()

            Application.edit_images(app, [(Image.new("RGB", (8, 8), "red"), None)],
                                    from_capture=True)
            editor = app.editors[0]
            try:
                self.assertTrue(editor.isVisible())
                self.assertIsNone(editor.last_path)
                self.app.processEvents()
                self.assertIsNotNone(editor.last_path)
                self.assertTrue(editor.last_path.is_file())
                app.saved.assert_called_once()
                self.assertEqual(app.saved.call_args.args[0], str(editor.last_path))
                self.assertEqual(app.saved.call_args.args[1].size(), QSize(8, 8))
                self.assertFalse(app.saved.call_args.kwargs["notify"])
                app.initial_save_failed.assert_not_called()
            finally:
                editor.close()

    def test_mask_inline_edit_single_region_saves_and_closes_in_place(self):
        from config.config_manager import DEFAULTS
        from screenshot.mask_window import MaskWindow
        from PySide6.QtWidgets import QToolButton

        with tempfile.TemporaryDirectory() as folder:
            bounds = {"left": 0, "top": 0, "width": 120, "height": 80}
            settings = {**DEFAULTS, "auto_dir": folder, "filename": "inline", "inline_edit": True,
                        "capture_after_selection": "edit", "crosshair": False, "magnifier": False,
                        "mask_opacity": 0, "sound": False,
                        "bubble": False}
            with patch("screenshot.mask_window.visible_windows", return_value=[]):
                mask = MaskWindow(Image.new("RGB", (120, 80), "blue"), bounds, [bounds], settings)
            selected = []
            saved = []
            mask.selected.connect(lambda images, positions: selected.append((images, positions)))
            mask.image_saved.connect(lambda path, image: saved.append((path, image)))
            mask.selection.rects.append(QRect(10, 12, 30, 20))
            mask.complete()
            self.assertIsNone(mask.session.inline_editor.last_path)
            self.app.processEvents()
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
            settings["capture_after_selection"] = "edit"
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
            settings["capture_after_selection"] = "edit"
            with patch("screenshot.mask_window.visible_windows", return_value=[]):
                mask = MaskWindow(Image.new("RGB", (120, 80), "blue"), bounds, [bounds], settings)
            errors = []
            mask.save_failed.connect(errors.append)
            mask.selection.rects.append(QRect(10, 12, 30, 20))
            mask.complete()
            self.app.processEvents()
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

    def test_inline_options_menu_does_not_raise_magnifier_or_move_capture_cursor(self):
        from PySide6.QtCore import QEvent, QPointF
        from PySide6.QtGui import QCursor, QMouseEvent
        from config.config_manager import DEFAULTS
        from screenshot.mask_window import MaskWindow

        with tempfile.TemporaryDirectory() as folder:
            bounds = {"left": 0, "top": 0, "width": 1200, "height": 900}
            settings = {**DEFAULTS, "auto_dir": folder, "inline_edit": True, "magnifier": True}
            settings["capture_after_selection"] = "edit"
            settings["capture_after_selection"] = "edit"
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
            settings["capture_after_selection"] = "edit"
            settings["capture_after_selection"] = "edit"
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
            settings["capture_after_selection"] = "edit"
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
            settings["capture_after_selection"] = "edit"
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

    def test_mask_selection_previews_capture_round_corners(self):
        from config.config_manager import DEFAULTS
        from screenshot.mask_window import MaskWindow

        bounds = {"left": 0, "top": 0, "width": 500, "height": 400}
        settings = {**DEFAULTS, "crosshair": False, "mask_opacity": 100,
                    "editor_image_corner_radius": 24}
        with patch("screenshot.mask_window.visible_windows", return_value=[]):
            mask = MaskWindow(Image.new("RGB", (500, 400), "#2040c0"),
                              bounds, [bounds], settings)
        mask.selection.rects.append(QRect(100, 120, 240, 180))
        mask.show()
        self.app.processEvents()
        preview = mask.grab().toImage()
        rounded_corner = preview.pixelColor(104, 128).name()
        self.assertNotEqual(rounded_corner, "#2040c0")
        self.assertEqual(preview.pixelColor(150, 260).name(), "#2040c0")
        settings["editor_image_round_corners"] = False
        mask.update()
        self.app.processEvents()
        square_corner = mask.grab().toImage().pixelColor(104, 128).name()
        self.assertEqual(square_corner, "#2040c0")
        mask.close()

    def test_inline_resize_keeps_magnifier_above_selection(self):
        from config.config_manager import DEFAULTS
        from screenshot.mask_window import MaskWindow

        with tempfile.TemporaryDirectory() as folder:
            bounds = {"left": 0, "top": 0, "width": 900, "height": 700}
            settings = {**DEFAULTS, "auto_dir": folder, "inline_edit": True,
                        "magnifier": True, "crosshair": False, "mask_opacity": 0}
            settings["capture_after_selection"] = "edit"
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
            settings["capture_after_selection"] = "edit"
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
                        "capture_after_selection": "edit",
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
                        "capture_after_selection": "edit", "crosshair": False, "magnifier": False,
                        "mask_opacity": 0, "bubble": False}
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
            paste_mask.sticker_requested.connect(
                lambda image, position: pasted.append((image, position)))
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
            settings["capture_after_selection"] = "edit"
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
            settings["capture_after_selection"] = "edit"
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
                        "capture_after_selection": "edit", "crosshair": False, "magnifier": False,
                        "mask_opacity": 0, "bubble": False}
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
                        "capture_after_selection": "edit", "mask_opacity": 0, "bubble": False}
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
            settings["capture_after_selection"] = "edit"
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
                output_rows = {button.mapTo(editor.toolbar, QPoint(0, 0)).y()
                               for button in output_buttons}
                edit_rows = {button.mapTo(editor.toolbar, QPoint(0, 0)).y()
                             for button in editor.toolbar.edit_buttons}
                self.assertEqual(len(output_rows), 1)
                self.assertEqual(len(edit_rows), 1)
                self.assertEqual(output_rows, edit_rows)
                spacer = editor.output_edit_spacer
                output_right = max(button.mapTo(editor.toolbar, QPoint(button.width(), 0)).x()
                                   for button in output_buttons)
                spacer_left = spacer.mapTo(editor.toolbar, QPoint(0, 0)).x()
                edit_left = min(button.mapTo(editor.toolbar, QPoint(0, 0)).x()
                                for button in editor.toolbar.edit_buttons)
                self.assertEqual(spacer_left - output_right, 2)
                self.assertEqual(edit_left - spacer.mapTo(editor.toolbar,
                                                          QPoint(spacer.width(), 0)).x(), 2)
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
            settings["capture_after_selection"] = "edit"
            settings["capture_after_selection"] = "edit"
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

    def test_capture_escape_fallback_tracks_window_activation(self):
        """拿不到焦点时用全局钩子兜底 Esc，拿到焦点后立刻交还给 Qt。"""
        from config.config_manager import DEFAULTS
        from screenshot import mask_window
        from screenshot.mask_window import MaskWindow

        bounds = {"left": 0, "top": 0, "width": 120, "height": 80}
        settings = dict(DEFAULTS, crosshair=False, magnifier=False, window_detection=False)
        with patch("screenshot.mask_window.visible_windows", return_value=[]):
            mask = MaskWindow(Image.new("RGB", (120, 80), "white"), bounds, [bounds], settings)
        try:
            mask.show()
            self.app.processEvents()
            installed = []
            released = []
            mask._install_escape_fallback = lambda: (installed.append("esc"), "hook")[1]
            mask._release_escape_fallback = lambda: released.append("released")
            with patch.object(mask_window, "ESCAPE_FALLBACK_ENABLED", True):
                with patch.object(mask, "isActiveWindow", return_value=False):
                    mask._sync_escape_fallback()
                self.assertEqual(mask.escape_fallback, "hook")
                self.assertEqual(installed, ["esc"])
                with patch.object(mask, "isActiveWindow", return_value=True):
                    mask._sync_escape_fallback()
                self.assertEqual(released, ["released"])
                with patch.object(mask, "isActiveWindow", return_value=False):
                    mask._sync_escape_fallback()
                mask.close()
                # 关闭时一定释放；期间焦点变化触发的同步可能额外释放一次。
                self.assertGreaterEqual(len(released), 2)
        finally:
            mask.close()

    def test_capture_global_escape_signal_closes_capture(self):
        """全局钩子线程只发信号，关闭动作仍在 Qt 主线程完成。"""
        from config.config_manager import DEFAULTS
        from screenshot.mask_window import MaskWindow

        bounds = {"left": 0, "top": 0, "width": 120, "height": 80}
        settings = dict(DEFAULTS, crosshair=False, magnifier=False, window_detection=False)
        with patch("screenshot.mask_window.visible_windows", return_value=[]):
            mask = MaskWindow(Image.new("RGB", (120, 80), "white"), bounds, [bounds], settings)
        mask.show()
        self.app.processEvents()
        self.assertTrue(mask.isVisible())
        mask._global_escape()
        self.app.processEvents()
        self.assertFalse(mask.isVisible())

    def test_window_focus_reports_foreground_state(self):
        from PySide6.QtWidgets import QWidget
        from core import window_focus

        widget = QWidget()
        widget.show()
        self.app.processEvents()
        handle = window_focus.widget_handle(widget)
        self.assertNotEqual(handle, 0)
        with patch.object(window_focus.os, "name", "nt"), \
                patch.object(window_focus, "foreground_handle", return_value=handle):
            self.assertTrue(window_focus.activate_window(widget))
        with patch.object(window_focus.os, "name", "posix"):
            self.assertFalse(window_focus.activate_window(widget))
        widget.close()

    def test_mask_escape_before_selection_and_fixed_size(self):
        from PySide6.QtWidgets import QDialog
        from PySide6.QtWidgets import QPushButton
        from PySide6.QtCore import QRect
        from config.config_manager import DEFAULTS
        from screenshot.mask_window import MaskWindow
        bounds = {"left": 0, "top": 0, "width": 200, "height": 150}
        with patch("screenshot.mask_window.visible_windows", return_value=[]):
            mask = MaskWindow(Image.new("RGB", (200, 150)), bounds, [bounds], DEFAULTS)
        mask.show()
        self.app.processEvents()
        self.assertTrue(mask.hasFocus())
        self.assertFalse(mask.capture_actions.isVisible())
        mask.selection.rects.append(QRect(10, 10, 60, 40))
        mask.update_all()
        self.assertTrue(mask.capture_actions.isVisible())
        self.assertEqual({button.toolTip().splitlines()[0] for button in
                  mask.capture_actions.findChildren(QPushButton)},
             {"自定义尺寸", "重新截图", "窗口编辑"})
        QTest.keyClick(mask, Qt.Key_Escape)
        self.assertFalse(mask.isVisible())
        self.assertFalse(mask.selection.rects)

        with patch("screenshot.mask_window.visible_windows", return_value=[]):
            mask = MaskWindow(Image.new("RGB", (200, 150)), bounds, [bounds], DEFAULTS)
        from PySide6.QtWidgets import QWidget
        mask.show()
        self.app.processEvents()
        focus_widget = QWidget(mask)
        focus_widget.setFocusPolicy(Qt.StrongFocus)
        focus_widget.show()
        focus_widget.setFocus()
        QTest.keyClick(focus_widget, Qt.Key_Escape)
        self.app.processEvents()
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

    def test_capture_actions_below_hint_and_enter_window_editor(self):
        from config.config_manager import DEFAULTS
        from PySide6.QtWidgets import QPushButton
        from screenshot.mask_window import MaskWindow

        bounds = {"left": 0, "top": 0, "width": 1200, "height": 800}
        settings = dict(DEFAULTS, inline_edit=True, crosshair=False, magnifier=False)
        with patch("screenshot.mask_window.visible_windows", return_value=[]):
            mask = MaskWindow(Image.new("RGB", (1200, 800), "blue"), bounds,
                              [bounds], settings)
        mask.selection.rects.append(QRect(30, 40, 80, 60))
        mask.update_all()
        actions = mask.capture_actions
        self.assertLessEqual(abs(actions.x() - (mask.width() - actions.width()) // 2), 1)
        self.assertEqual(actions.y(), 8 + mask.fontMetrics().height() + 18)
        buttons = actions.findChildren(QPushButton)
        self.assertEqual([button.height() for button in buttons], [32, 32, 32, 32])
        self.assertFalse(buttons[1].icon().isNull())
        self.assertNotEqual(buttons[0].icon().pixmap(24, 24).toImage(),
                    buttons[1].icon().pixmap(24, 24).toImage())
        self.assertGreaterEqual(buttons[1].x() - buttons[0].geometry().right() - 1, 8)
        edited = []
        mask.edit_requested.connect(lambda images, positions: edited.append((images, positions)))
        next(button for button in actions.findChildren(QPushButton)
             if button.text() == "窗口编辑").click()
        self.assertEqual(len(edited), 1)
        self.assertEqual(edited[0][0][0][0].size, (80, 60))
        self.assertFalse(mask.isVisible())

    def test_capture_actions_exist_below_hint_on_each_monitor(self):
        from config.config_manager import DEFAULTS
        from PySide6.QtWidgets import QPushButton
        from screenshot.mask_window import MaskWindow

        bounds = {"left": 0, "top": 0, "width": 400, "height": 160}
        monitors = [
            {"left": 0, "top": 0, "width": 200, "height": 160},
            {"left": 200, "top": 0, "width": 200, "height": 160},
        ]
        screen_infos = [
            {"geometry": QRect(0, 0, 200, 160), "dpr": 1.0},
            {"geometry": QRect(200, 0, 200, 160), "dpr": 1.0},
        ]
        settings = dict(DEFAULTS, inline_edit=False, crosshair=False, magnifier=False)
        with patch("screenshot.mask_window.visible_windows", return_value=[]), \
                patch("core.dpi.DisplayMapper.collect_screen_infos", return_value=screen_infos):
            mask = MaskWindow(Image.new("RGB", (400, 160), "blue"), bounds,
                              monitors, settings)
        mask.selection.rects.append(QRect(30, 40, 80, 60))
        mask.update_all()
        mask.show()
        self.app.processEvents()
        self.assertEqual(len(mask.session.views), 2)
        for view in mask.session.views:
            actions = view.capture_actions
            self.assertTrue(actions.isVisible())
            self.assertEqual(actions.y(), 8 + view.fontMetrics().height() + 18)
            self.assertLessEqual(abs(actions.x() - (view.width() - actions.width()) // 2), 1)
            self.assertEqual(len(actions.findChildren(QPushButton)), 4)
        secondary_edit = mask.session.views[1].capture_action_buttons[2]
        self.assertEqual(secondary_edit.text(), "")
        edited = []
        mask.edit_requested.connect(lambda images, positions: edited.append((images, positions)))
        secondary_edit.click()
        self.assertEqual(len(edited), 1)
        mask.close()

    def test_inline_region_reset_keeps_rounded_preview(self):
        from config.config_manager import DEFAULTS
        from screenshot.mask_window import MaskWindow

        with tempfile.TemporaryDirectory() as folder:
            bounds = {"left": 0, "top": 0, "width": 100, "height": 80}
            settings = dict(DEFAULTS, auto_dir=folder, inline_edit=True,
                            magnifier=False, crosshair=False, mask_opacity=0)
            settings["capture_after_selection"] = "edit"
            with patch("screenshot.mask_window.visible_windows", return_value=[]):
                mask = MaskWindow(Image.new("RGB", (100, 80), "#d02020"), bounds,
                                  [bounds], settings)
            mask.selection.rects.append(QRect(10, 10, 60, 50))
            mask.complete()
            mask.show()
            self.app.processEvents()
            editor = mask.session.inline_editor
            self.assertTrue(editor.canvas.round_corner_preview)
            editor.reset_region(QRect(5, 7, 70, 60),
                                Image.new("RGB", (70, 60), "#d02020"), None, False)
            self.assertTrue(editor.canvas.round_corner_preview)
            self.assertEqual(editor.canvas.render_image().pixelColor(2, 2).name(), "#d02020")
            editor.last_path = Path(folder) / "already-saved.png"
            editor.initial_save_timer.stop()
            editor.reset_region(QRect(5, 7, 70, 60),
                                Image.new("RGB", (70, 60), "#d02020"), None, False)
            self.assertFalse(editor.initial_save_timer.isActive())
            mask.close()

    def test_inline_editor_paste_saves_emits_sticker_and_closes_capture(self):
        from types import SimpleNamespace
        from config.config_manager import DEFAULTS
        from main import Application
        from screenshot.mask_window import MaskWindow

        with tempfile.TemporaryDirectory() as folder, \
            patch("screenshot.mask_window.visible_windows", return_value=[]), \
            patch("sticker.sticker_manager.data_dir", return_value=Path(folder)):
            bounds = {"left": 0, "top": 0, "width": 100, "height": 80}
            settings = dict(DEFAULTS, auto_dir=folder, inline_edit=True,
                            magnifier=False, crosshair=False, mask_opacity=0)
            settings["capture_after_selection"] = "edit"
            manager = StickerManager(settings)
            mask = MaskWindow(Image.new("RGB", (100, 80), "#d02020"), bounds,
                              [bounds], settings)
            mask.selection.rects.append(QRect(10, 10, 60, 50))
            mask.complete()
            editor = mask.session.inline_editor
            editor.initial_save_timer.stop()
            emitted = []
            normal_saves = []
            silent_saves = []
            app = SimpleNamespace(stickers=manager, logger=Mock(), notify=Mock())
            mask.sticker_requested.connect(
                lambda image, position: emitted.append((image, position)))
            mask.sticker_requested.connect(
                lambda image, position: Application.add_sticker(app, image, position))
            mask.image_saved.connect(lambda path, image: normal_saves.append((path, image)))
            mask.image_saved_silently.connect(
                lambda path, image: silent_saves.append((path, image)))
            mask.image_saved_silently.connect(
                lambda path, image: Application.saved(app, path, image, notify=False))
            editor.execute("paste")
            self.assertEqual(len(emitted), 1)
            self.assertEqual(normal_saves, [])
            self.assertEqual(len(silent_saves), 1)
            self.assertTrue(Path(silent_saves[0][0]).is_file())
            self.assertEqual(len(manager.items), 1)
            self.assertTrue(manager.items[0].isVisible())
            self.assertEqual((manager.items[0].pixmap.width(), manager.items[0].pixmap.height()),
                             (60, 50))
            self.assertEqual(manager.items[0].pos() + QPoint(manager.items[0].padding(), manager.items[0].padding()),
                             emitted[0][1])
            self.assertFalse(mask.isVisible())
            manager.close_all()

    def test_application_connects_sticker_requests_for_each_screen_view(self):
        from types import SimpleNamespace
        from main import Application

        app = Application.__new__(Application)
        app.add_sticker = Mock()
        views = [
            SimpleNamespace(sticker_requested=Mock(), quick_sticker_requested=Mock()),
            SimpleNamespace(sticker_requested=Mock(), quick_sticker_requested=Mock()),
        ]

        Application._connect_sticker_signals(app, views)

        for view in views:
            view.sticker_requested.connect.assert_called_once_with(app.add_sticker)
            view.quick_sticker_requested.connect.assert_called_once_with(app.add_sticker)

    def test_inline_editor_paste_creates_sticker_when_save_fails(self):
        from config.config_manager import DEFAULTS
        from screenshot.mask_window import MaskWindow

        with tempfile.TemporaryDirectory() as folder, \
            patch("screenshot.mask_window.visible_windows", return_value=[]), \
            patch("sticker.sticker_manager.data_dir", return_value=Path(folder)):
            bounds = {"left": 0, "top": 0, "width": 100, "height": 80}
            settings = dict(DEFAULTS, auto_dir=folder, inline_edit=True,
                            magnifier=False, crosshair=False, mask_opacity=0,
                            capture_after_selection="edit")
            manager = StickerManager(settings)
            mask = MaskWindow(Image.new("RGB", (100, 80), "#d02020"), bounds,
                              [bounds], settings)
            mask.selection.rects.append(QRect(10, 10, 60, 50))
            mask.complete()
            editor = mask.session.inline_editor
            editor.initial_save_timer.stop()
            errors = []
            mask.save_failed.connect(errors.append)
            mask.sticker_requested.connect(
                lambda image, position: manager.add(image, position=position))
            with patch.object(editor, "save", side_effect=OSError("disk full")):
                editor.execute("paste")

            self.assertEqual(len(manager.items), 1)
            self.assertTrue(manager.items[0].isVisible())
            self.assertEqual(errors, ["贴图已创建，但自动保存失败: disk full"])
            self.assertFalse(mask.isVisible())
            manager.close_all()

    def test_inline_editor_color_picker_updates_last_annotation_tool(self):
        from config.config_manager import DEFAULTS
        from screenshot.mask_window import MaskWindow

        bounds = {"left": 0, "top": 0, "width": 100, "height": 80}
        settings = dict(DEFAULTS, inline_edit=True, magnifier=False,
                        crosshair=False, mask_opacity=0,
                        capture_after_selection="edit")
        mask = MaskWindow(Image.new("RGB", (100, 80), "white"), bounds,
                          [bounds], settings)
        mask.selection.rects.append(QRect(10, 10, 60, 50))
        mask.complete()
        editor = mask.session.inline_editor
        editor.initial_save_timer.stop()
        editor.set_tool("rect")
        editor.set_tool("select")
        editor.set_pen_color("#123456")

        self.assertEqual(settings["rect_color"], "#123456")
        self.assertEqual(settings["pen_color"], DEFAULTS["pen_color"])
        self.assertEqual(editor.toolbar.pen_color.color, "#123456")
        mask.close()

    def test_tool_color_buttons_are_independent_and_open_picker_in_both_editors(self):
        from PySide6.QtGui import QColor
        from PySide6.QtWidgets import QColorDialog
        from config.config_manager import DEFAULTS
        from editor.editor_window import EditorWindow
        from screenshot.mask_window import MaskWindow

        color_tools = (("pen_color", "pen"), ("marker_color", "marker"),
                   ("rect_color", "rect"), ("ellipse_color", "ellipse"),
                   ("text_color", "text"), ("arrow_color", "arrow"))
        color_keys = tuple(key for key, _ in color_tools)
        settings = dict(DEFAULTS)
        window_editor = EditorWindow(Image.new("RGB", (60, 40), "white"), settings)
        bounds = {"left": 0, "top": 0, "width": 100, "height": 80}
        with patch("screenshot.mask_window.visible_windows", return_value=[]):
            mask = MaskWindow(Image.new("RGB", (100, 80), "white"), bounds,
                              [bounds], dict(DEFAULTS, inline_edit=True,
                                             capture_after_selection="edit",
                                             magnifier=False, crosshair=False))
        try:
            mask.selection.rects.append(QRect(10, 10, 60, 50))
            mask.complete()
            inline_editor = mask.session.inline_editor
            inline_editor.initial_save_timer.stop()
            for editor in (window_editor, inline_editor):
                self.assertEqual(set(editor.toolbar.tool_color_buttons), set(color_keys))
                for index, (key, tool) in enumerate(color_tools):
                    editor.toolbar.tool_buttons[tool].click()
                    button = editor.toolbar.tool_color_buttons[key]
                    self.assertFalse(button.isHidden(), key)
                    before = {name: editor.settings[name] for name in color_keys}
                    color = f"#{index + 1:06x}"
                    with patch("ui.widgets.color_button.color_dialog") as dialog_factory:
                        dialog_factory.return_value.exec.return_value = QColorDialog.Accepted
                        dialog_factory.return_value.currentColor.return_value = QColor(color)
                        button.clicked.emit()
                    self.assertEqual(dialog_factory.call_count, 1, key)
                    self.assertEqual(editor.settings[key], color, key)
                    self.assertEqual(editor.canvas.settings[key], color, key)
                    self.assertEqual(button.color, color, key)
                    for other_key in color_keys:
                        if other_key != key:
                            self.assertEqual(editor.settings[other_key], before[other_key])
                    self.assertEqual(editor.toolbar.tool_color_buttons["rect_color"].toolTip()
                                     .count("矩形边框颜色"), 1)

                rect_button = editor.toolbar.tool_color_buttons["rect_color"]
                editor.toolbar.tool_buttons["rect"].click()
                self.assertFalse(editor.toolbar.rect_fill_enabled.isHidden())
                self.assertTrue(editor.toolbar.ellipse_fill_enabled.isHidden())
                with patch("ui.widgets.color_button.color_dialog") as dialog_factory:
                    dialog_factory.return_value.exec.return_value = QColorDialog.Accepted
                    dialog_factory.return_value.currentColor.return_value = QColor("#654321")
                    editor.toolbar.pen_color.clicked.emit()
                self.assertEqual(editor.settings["rect_color"], "#654321")
                self.assertEqual(rect_button.color, "#654321")
                editor.toolbar.tool_buttons["ellipse"].click()
                self.assertTrue(editor.toolbar.rect_fill_enabled.isHidden())
                self.assertFalse(editor.toolbar.ellipse_fill_enabled.isHidden())
        finally:
            window_editor.close()
            mask.close()

    def test_round_selection_mask_has_antialiased_edges(self):
        from config.config_manager import DEFAULTS
        from screenshot.mask_window import MaskWindow

        bounds = {"left": 0, "top": 0, "width": 100, "height": 100}
        settings = dict(DEFAULTS, editor_image_round_corners=True,
                        editor_image_corner_radius=16, magnifier=False,
                        crosshair=False, mask_opacity=100,
                        selection_border_color="#0000ff")
        with patch("screenshot.mask_window.visible_windows", return_value=[]):
            mask = MaskWindow(Image.new("RGB", (100, 100), "#ff0000"), bounds,
                              [bounds], settings)
        mask.selection.rects.append(QRect(20, 20, 60, 60))
        mask.show()
        self.app.processEvents()
        rendered = mask.grab().toImage()
        colors = {rendered.pixelColor(x, y).name()
                  for x in range(25, 36) for y in range(20, 31)}
        solid_colors = {"#ff0000", "#000000", "#0000ff", "#ffffff", "#00d7aa"}
        self.assertTrue(colors - solid_colors, colors)
        mask.close()

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

    def test_quick_sticker_shortcut_refreshes_on_active_mask(self):
        from config.config_manager import DEFAULTS
        from screenshot.mask_window import MaskWindow

        bounds = {"left": 0, "top": 0, "width": 80, "height": 60}
        settings = dict(DEFAULTS, capture_quick_sticker_enabled=False,
                        crosshair=False, magnifier=False, mask_opacity=0)
        with patch("screenshot.mask_window.visible_windows", return_value=[]):
            mask = MaskWindow(Image.new("RGB", (80, 60), "red"), bounds,
                              [bounds], settings)
        mask.selection.rects.append(QRect(5, 7, 30, 20))
        images = []
        mask.quick_sticker_requested.connect(
            lambda image, position: images.append((image, position)))
        mask.sync_quick_sticker_shortcut({
            **settings, "capture_quick_sticker_enabled": True,
            "capture_quick_sticker_shortcut": "K"})
        self.assertEqual(mask.quick_sticker_shortcut.key().toString(), "K")
        self.assertTrue(mask.quick_sticker_shortcut.isEnabled())
        mask.show()
        self.app.processEvents()
        QTest.keyClick(mask, Qt.Key_K)
        self.app.processEvents()
        self.assertEqual(len(images), 1)
        mask.close()

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

    def test_inline_escape_from_canvas_or_toolbar_closes_capture(self):
        from config.config_manager import DEFAULTS
        from screenshot.mask_window import MaskWindow

        with tempfile.TemporaryDirectory() as folder:
            bounds = {"left": 0, "top": 0, "width": 500, "height": 400}
            settings = {**DEFAULTS, "auto_dir": folder, "inline_edit": True,
                        "capture_after_selection": "edit", "magnifier": False}
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
        settings = {**DEFAULTS, "crosshair": False, "magnifier": False,
                    "mask_color": "#000000", "mask_opacity": 0}
        with patch("screenshot.mask_window.visible_windows", return_value=[]):
            mask = MaskWindow(Image.new("RGB", (200, 150), "white"), bounds, [bounds], settings)
        output = QImage(200, 150, QImage.Format_ARGB32)
        mask.render(output)
        self.assertEqual(output.pixelColor(100, 100).name(), "#ffffff")
        settings["mask_opacity"] = 100
        mask.render(output)
        self.assertEqual(output.pixelColor(100, 100).name(), "#000000")
        mask.close()

    def test_mask_color_presets_and_legacy_theme_migration(self):
        from config.config_manager import DEFAULTS, validate
        from screenshot.mask_window import _mask_overlay_color
        from ui.settings_screenshot import ScreenshotPage

        self.assertEqual(DEFAULTS["mask_color"], "#D9EDFF")
        default_overlay = _mask_overlay_color(DEFAULTS)
        self.assertEqual(default_overlay.name(), "#d9edff")
        self.assertEqual(default_overlay.alpha(), 128)
        self.assertEqual(validate({"mask_theme": "dark"})["mask_color"], "#000000")
        self.assertEqual(validate({"mask_theme": "light"})["mask_color"], "#FFFFFF")
        self.assertEqual(validate({"mask_theme": "dark", "mask_color": "#DDF5E4"})[
            "mask_color"], "#DDF5E4")
        with self.assertRaises(ValueError):
            validate({"mask_color": "blue"})

        with tempfile.TemporaryDirectory() as folder:
            page = ScreenshotPage(ConfigManager(Path(folder) / "settings.json"),
                                  lambda: None)
            try:
                preset = page.controls["mask_color"]
                self.assertEqual(preset.count(), 8)
                self.assertEqual(preset.itemData(0), "#D9EDFF")
                self.assertIn("mask_color", page.color_buttons)
                preset.setCurrentIndex(1)
                self.assertEqual(page.config.data["mask_color"], "#DDF5E4")
                page._set_mask_color("#ABCDEF", preset, page.color_buttons["mask_color"])
                self.assertEqual(page.config.data["mask_color"], "#ABCDEF")
                self.assertEqual(preset.currentData(), "#ABCDEF")
                page.reset_page()
                self.assertEqual(page.config.data["mask_color"], DEFAULTS["mask_color"])
            finally:
                page.close()

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

    def test_round_corner_editor_preview_is_display_only(self):
        from config.config_manager import DEFAULTS
        from editor.image_effects import apply_output_effects
        from PySide6.QtWidgets import QFrame

        settings = dict(DEFAULTS)
        source = Image.new("RGB", (120, 100), "#d02020")
        canvas = AnnotationCanvas(source, settings)
        canvas.setFrameShape(QFrame.NoFrame)
        canvas.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        canvas.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        canvas.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        canvas.resize(120, 100)
        canvas.set_round_corner_preview(True, 20)
        canvas.show()
        self.app.processEvents()
        preview = canvas.viewport().grab().toImage()
        corner = canvas.mapFromScene(QPointF(5, 5))
        center = canvas.mapFromScene(QPointF(60, 50))
        self.assertNotEqual(preview.pixelColor(corner).name(), "#d02020")
        self.assertEqual(preview.pixelColor(center).name(), "#d02020")
        self.assertEqual(canvas.render_image().pixelColor(5, 5).name(), "#d02020")
        output = apply_output_effects(canvas.render_image(), settings, True, 20)
        self.assertEqual(output.pixelColor(5, 5).alpha(), 0)
        self.assertEqual(output.pixelColor(5, 5).getRgb(), (0, 0, 0, 0))
        self.assertTrue(any(0 < output.pixelColor(x, y).alpha() < 255
                    for y in range(20) for x in range(20)))
        self.assertEqual(output.pixelColor(60, 50).alpha(), 255)
        settings.update(editor_image_border_enabled=True,
                editor_image_border_color="#ffffff",
                editor_image_border_width=8)
        bordered = apply_output_effects(canvas.render_image(), settings, True, 20)
        self.assertEqual(bordered.pixelColor(5, 5).getRgb(), (0, 0, 0, 0))
        self.assertEqual(bordered.pixelColor(60, 4).name(), "#ffffff")
        canvas.set_round_corner_preview(False, 20)
        self.app.processEvents()
        self.assertEqual(canvas.viewport().grab().toImage().pixelColor(corner).name(),
                         "#d02020")
        canvas.close()

    def test_annotation_sequence_serializes_and_restores(self):
        from editor.annotation_canvas import AnnotationCanvas
        from editor.annotation_items import AnnotationSequenceItem
        from config.config_manager import DEFAULTS
        from PySide6.QtCore import QPointF

        canvas = AnnotationCanvas(Image.new("RGB", (120, 100), "white"), dict(DEFAULTS))
        item = AnnotationSequenceItem(3, "#00ad91", "#ffffff", 16, "circle", "", QPointF(20, 20))
        canvas.scene_data.addItem(item)
        records = canvas.snapshot()
        self.assertEqual(records[0]["type"], "AnnotationSequenceItem")
        self.assertEqual(records[0]["number"], 3)
        self.assertEqual(records[0]["sequence_fill_color"], "#00ad91")
        self.assertEqual(records[0]["sequence_text_color"], "#ffffff")
        for it in canvas.annotations():
            canvas.scene_data.removeItem(it)
        canvas.restore(records)
        restored = [it for it in canvas.annotations()
                   if isinstance(it, AnnotationSequenceItem)]
        self.assertEqual(len(restored), 1)
        self.assertEqual(restored[0].number, 3)
        self.assertEqual(restored[0].sequence_fill_color, "#00ad91")
        self.assertEqual(restored[0].sequence_font_size, 16)

    def test_sequence_next_number_fills_gap_after_delete(self):
        from editor.annotation_canvas import AnnotationCanvas
        from editor.annotation_items import AnnotationSequenceItem
        from config.config_manager import DEFAULTS
        from PySide6.QtCore import QPointF

        canvas = AnnotationCanvas(Image.new("RGB", (120, 100), "white"), dict(DEFAULTS))
        for value in (1, 2, 3, 4):
            canvas.scene_data.addItem(
                AnnotationSequenceItem(value, "#ff0000", "#ffffff", 14, "circle", "", QPointF(10, 10)))
        # 删除中间一个（2），下一个编号应填补空缺而不是从最大值+1。
        for it in canvas.annotations():
            if isinstance(it, AnnotationSequenceItem) and it.number == 2:
                canvas.scene_data.removeItem(it)
        self.assertEqual(canvas.next_sequence_number(), 2)
        # 再删一个（3），剩余 1/4，最小空缺仍是 2（先填前面的空）。
        for it in canvas.annotations():
            if isinstance(it, AnnotationSequenceItem) and it.number == 3:
                canvas.scene_data.removeItem(it)
        self.assertEqual(canvas.next_sequence_number(), 2)
        canvas.close()

    def test_sequence_renumber_after_deleting_middle(self):
        from editor.annotation_canvas import AnnotationCanvas
        from editor.annotation_items import AnnotationSequenceItem
        from config.config_manager import DEFAULTS
        from PySide6.QtCore import QPointF

        canvas = AnnotationCanvas(Image.new("RGB", (120, 100), "white"), dict(DEFAULTS))

        def add(value):
            item = AnnotationSequenceItem(value, "#ff0000", "#ffffff", 14, "circle", "",
                                         QPointF(10, 10))
            canvas.scene_data.addItem(item)
            return item

        for value in range(1, 8):  # 连续 1..7
            add(value)
        # 删除中间连续多个：2/3/4/5。
        for it in canvas.annotations():
            if isinstance(it, AnnotationSequenceItem) and it.number in (2, 3, 4, 5):
                it.setSelected(True)
        canvas.remove_selected()
        numbers = sorted(it.number for it in canvas.annotations()
                        if isinstance(it, AnnotationSequenceItem))
        # 原 1/6/7 按序重排为 1/2/3，不再留空缺。
        self.assertEqual(numbers, [1, 2, 3])
        self.assertEqual(canvas.next_sequence_number(), 4)
        # 再删除中间单个（2），剩余 1/3 重排为 1/2。
        for it in canvas.annotations():
            if isinstance(it, AnnotationSequenceItem) and it.number == 2:
                it.setSelected(True)
        canvas.remove_selected()
        numbers = sorted(it.number for it in canvas.annotations()
                        if isinstance(it, AnnotationSequenceItem))
        self.assertEqual(numbers, [1, 2])
        canvas.close()

    def test_annotation_render_keeps_antialiased_edges_before_round_output(self):
        from config.config_manager import DEFAULTS
        from editor.image_effects import apply_output_effects

        canvas = AnnotationCanvas(Image.new("RGBA", (80, 80), (0, 0, 0, 0)), DEFAULTS)
        canvas.scene_data.addItem(shape("arrow", QPointF(8.3, 9.7),
                                        QPointF(70.6, 68.2), "#ff2020", 1))
        rendered = canvas.render_image()
        self.assertTrue(any(0 < rendered.pixelColor(x, y).alpha() < 255
                            for y in range(80) for x in range(80)))
        output = apply_output_effects(rendered, DEFAULTS, True, 14)
        self.assertEqual(output.pixelColor(0, 0).alpha(), 0)
        self.assertTrue(any(0 < output.pixelColor(x, y).alpha() < 255
                            for y in range(20) for x in range(20)))
        canvas.close()

    def test_output_border_covers_outer_image_ring_with_antialiased_corners(self):
        from editor.image_effects import apply_output_effects

        source = Image.new("RGB", (40, 32), "red")
        settings = {
            "editor_image_border_enabled": True,
            "editor_image_border_width": 8,
            "editor_image_border_color": "#ffffff",
            "editor_image_round_corners": False,
            "editor_image_shadow_enabled": False,
        }
        square = apply_output_effects(source, settings, False, 0)
        self.assertEqual(square.getpixel((20, 0))[:3], (255, 255, 255))
        self.assertEqual(square.getpixel((0, 16))[:3], (255, 255, 255))
        self.assertEqual(square.getpixel((20, 2))[:3], (255, 255, 255))
        self.assertEqual(square.getpixel((20, 12))[:3], (255, 0, 0))

        rounded = apply_output_effects(source, settings, True, 10)
        corner_alphas = [rounded.getpixel((x, y))[3]
                         for y in range(10) for x in range(10)]
        self.assertTrue(any(0 < alpha < 255 for alpha in corner_alphas))
        self.assertEqual(rounded.getpixel((20, 0))[:3], (255, 255, 255))

    def test_rounded_rectangle_survives_undo_redo(self):
        from config.config_manager import DEFAULTS
        canvas = AnnotationCanvas(Image.new("RGB", (120, 100), "white"), DEFAULTS)
        item = shape("rect", QPointF(10, 10), QPointF(60, 60), "#ff0000", 3,
                     corner_radius=12)
        canvas.scene_data.addItem(item)
        canvas.checkpoint()
        canvas.undo()
        canvas.redo()
        restored = canvas.annotations()[0]
        self.assertEqual(type(restored).__name__, "RoundedRectItem")
        self.assertEqual(restored.corner_radius, 12)
        self.assertEqual(restored.rect().width(), 50)

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

    def test_picker_tool_uses_custom_dropper_cursor(self):
        from config.config_manager import DEFAULTS

        canvas = AnnotationCanvas(Image.new("RGB", (120, 100), "white"), DEFAULTS)
        try:
            canvas.set_tool("picker")
            self.assertEqual(canvas.cursor().shape(), Qt.BitmapCursor)
            self.assertEqual(canvas.cursor().hotSpot(), QPoint(8, 26))
            canvas.set_tool("pen")
            self.assertEqual(canvas.cursor().shape(), Qt.ArrowCursor)
        finally:
            canvas.close()

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
        resized = item.sceneBoundingRect()
        self.assertGreater(resized.width(), 12)
        self.assertGreater(resized.height(), 12)
        self.assertAlmostEqual(item.sceneBoundingRect().topLeft().x(), fixed_corner.x(), delta=1)
        self.assertAlmostEqual(item.sceneBoundingRect().topLeft().y(), fixed_corner.y(), delta=1)
        self.assertEqual(canvas.render_image().pixelColor(24, 24).name(), "#ffffff")
        canvas.close()

    def test_annotation_items_resize_from_multiple_handles_and_undo(self):
        from config.config_manager import DEFAULTS
        from editor.annotation_items import text_item
        from PySide6.QtCore import QEvent
        from PySide6.QtGui import QMouseEvent

        canvas = AnnotationCanvas(Image.new("RGB", (240, 180), "white"), DEFAULTS)
        canvas.resize(400, 300)
        canvas.show()
        items = [
            shape("rect", QPointF(20, 20), QPointF(60, 60), "#ff0000", 2),
            shape("ellipse", QPointF(80, 20), QPointF(120, 60), "#00aa00", 2),
            shape("arrow", QPointF(140, 20), QPointF(180, 60), "#0000ff", 2),
            text_item(QPointF(20, 100), "Resize me", DEFAULTS, Qt.AlignLeft),
        ]
        try:
            for item in items:
                canvas.restore([])
                canvas.history = [(canvas.image, canvas.alternate, canvas.cursor_enabled, [])]
                canvas.cursor_index = 0
                canvas.scene_data.addItem(item)
                item.setSelected(True)
                canvas.checkpoint()
                original = item.sceneBoundingRect()
                handle = QPointF(original.right(), original.center().y())
                start = canvas.mapFromScene(handle)
                finish = start + QPoint(22, 0)
                self.app.sendEvent(canvas.viewport(), QMouseEvent(
                    QEvent.MouseButtonPress, QPointF(start),
                    QPointF(canvas.viewport().mapToGlobal(start)),
                    Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))
                self.app.sendEvent(canvas.viewport(), QMouseEvent(
                    QEvent.MouseMove, QPointF(finish),
                    QPointF(canvas.viewport().mapToGlobal(finish)),
                    Qt.NoButton, Qt.LeftButton, Qt.NoModifier))
                self.app.sendEvent(canvas.viewport(), QMouseEvent(
                    QEvent.MouseButtonRelease, QPointF(finish),
                    QPointF(canvas.viewport().mapToGlobal(finish)),
                    Qt.LeftButton, Qt.NoButton, Qt.NoModifier))
                resized = item.sceneBoundingRect()
                self.assertGreater(resized.width(), original.width())
                self.assertAlmostEqual(resized.height(), original.height(), delta=2)
                canvas.undo()
                restored = canvas.annotations()[0].sceneBoundingRect()
                self.assertAlmostEqual(restored.width(), original.width(), delta=1)
                self.assertAlmostEqual(restored.height(), original.height(), delta=1)
                canvas.redo()
                item.setSelected(False)
            handles = canvas.resize_handles(QRectF(10, 10, 100, 80))
            self.assertEqual(set(handles), {"nw", "n", "ne", "e", "se", "s", "sw", "w"})
        finally:
            canvas.close()

    def test_selected_annotation_resize_keeps_live_content_visible(self):
        from config.config_manager import DEFAULTS
        from PySide6.QtCore import QEvent
        from PySide6.QtGui import QMouseEvent

        canvas = AnnotationCanvas(Image.new("RGB", (120, 90), "white"), DEFAULTS)
        canvas.resize(240, 180)
        item = shape("rect", QPointF(20, 20), QPointF(60, 60), "#ff0000", 3)
        try:
            canvas.scene_data.addItem(item)
            canvas.show()
            item.setSelected(True)
            self.app.processEvents()
            original = item.sceneBoundingRect()
            handle = canvas.mapFromScene(QPointF(original.right(), original.center().y()))
            finish = handle + QPoint(18, 0)

            def red_ink_pixels():
                frame = canvas.viewport().grab().toImage()
                return sum(1 for y in range(frame.height()) for x in range(frame.width())
                           if (lambda pixel: pixel.red() > 180 and pixel.green() < 100
                               and pixel.blue() < 100)(frame.pixelColor(x, y)))

            selected_ink = red_ink_pixels()
            self.app.sendEvent(canvas.viewport(), QMouseEvent(
                QEvent.MouseButtonPress, QPointF(handle),
                QPointF(canvas.viewport().mapToGlobal(handle)),
                Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))
            self.app.sendEvent(canvas.viewport(), QMouseEvent(
                QEvent.MouseMove, QPointF(finish),
                QPointF(canvas.viewport().mapToGlobal(finish)),
                Qt.NoButton, Qt.LeftButton, Qt.NoModifier))
            self.app.processEvents()

            self.assertGreater(selected_ink, 0)
            self.assertGreater(red_ink_pixels(), selected_ink)
            self.assertGreater(item.sceneBoundingRect().width(), original.width())
        finally:
            canvas.close()

    def test_picker_cursor_and_appearance_toggle_shared_by_both_editors(self):
        from config.config_manager import DEFAULTS
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QPushButton
        from screenshot.mask_window import MaskWindow

        settings = dict(DEFAULTS, crosshair=False, magnifier=False)
        window_editor = EditorWindow(Image.new("RGB", (40, 30), "white"), settings)
        bounds = {"left": 0, "top": 0, "width": 120, "height": 80}
        with patch("screenshot.mask_window.visible_windows", return_value=[]):
            mask = MaskWindow(Image.new("RGB", (120, 80), "blue"), bounds, [bounds], settings)
        try:
            mask.selection.rects.append(QRect(10, 10, 40, 30))
            mask.complete()
            inline_editor = mask.session.inline_editor
            for toolbar in (window_editor.toolbar, inline_editor.toolbar):
                self.assertFalse(toolbar.appearance_toggle.icon().isNull())
                toolbar.show()
                QTest.mouseClick(toolbar.appearance_toggle, Qt.LeftButton)
                self.app.processEvents()
                self.assertTrue(toolbar.appearance_menu.isVisible())
                outside = QPushButton("outside")
                outside.show()
                QTest.mouseClick(outside, Qt.LeftButton)
                self.app.processEvents()
                self.assertFalse(toolbar.appearance_menu.isVisible())
                self.assertFalse(toolbar.appearance_toggle.isChecked())
                QTest.mouseClick(toolbar.appearance_toggle, Qt.LeftButton)
                self.app.processEvents()
                self.assertTrue(toolbar.appearance_menu.isVisible())
                QTest.mouseClick(toolbar.appearance_toggle, Qt.LeftButton)
                self.app.processEvents()
                self.assertFalse(toolbar.appearance_menu.isVisible())
                self.assertFalse(toolbar.appearance_toggle.isChecked())
                outside.close()
                self.assertIn("outputAppearancePanel", toolbar.appearance_panel.styleSheet())

            window_editor.set_tool("picker")
            inline_editor.set_tool("picker")
            for editor in (window_editor, inline_editor):
                cursor = editor.canvas.cursor()
                self.assertEqual(cursor.shape(), Qt.BitmapCursor)
                self.assertEqual(cursor.hotSpot(), QPoint(4, 28))
                self.assertFalse(cursor.pixmap().isNull())
        finally:
            window_editor.close()
            mask.close()

    def test_appearance_toggle_release_closes_in_both_editor_modes(self):
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QCursor
        from config.config_manager import DEFAULTS
        from screenshot.mask_window import MaskWindow

        settings = dict(DEFAULTS, crosshair=False, magnifier=False)
        window_editor = EditorWindow(Image.new("RGB", (40, 30), "white"), settings)
        bounds = {"left": 0, "top": 0, "width": 120, "height": 80}
        with patch("screenshot.mask_window.visible_windows", return_value=[]):
            mask = MaskWindow(Image.new("RGB", (120, 80), "blue"), bounds,
                              [bounds], settings)
        original_cursor_position = QCursor.pos()
        try:
            mask.selection.rects.append(QRect(10, 10, 40, 30))
            mask.complete()
            inline_editor = mask.session.inline_editor
            for toolbar in (window_editor.toolbar, inline_editor.toolbar):
                toolbar.show()
                QTest.mouseClick(toolbar.appearance_toggle, Qt.LeftButton)
                self.app.processEvents()
                self.assertTrue(toolbar.appearance_menu.isVisible())
                QCursor.setPos(toolbar.appearance_toggle.mapToGlobal(
                    toolbar.appearance_toggle.rect().center()))
                with patch("editor.toolbar_widget.QApplication.mouseButtons",
                           return_value=Qt.LeftButton):
                    toolbar.appearance_menu.hide()
                self.app.processEvents()
                self.assertTrue(toolbar.appearance_toggle.isChecked())
                toolbar.appearance_toggle.setChecked(False)
                toolbar.appearance_toggle.clicked.emit()
                self.app.processEvents()
                self.assertFalse(toolbar.appearance_menu.isVisible())
                QTest.mouseClick(toolbar.appearance_toggle, Qt.LeftButton)
                self.app.processEvents()
                self.assertTrue(toolbar.appearance_menu.isVisible())
        finally:
            QCursor.setPos(original_cursor_position)
            window_editor.close()
            mask.close()

    def test_annotation_resize_all_handles_keeps_opposite_anchor(self):
        from config.config_manager import DEFAULTS
        from PySide6.QtCore import QEvent
        from PySide6.QtGui import QMouseEvent

        canvas = AnnotationCanvas(Image.new("RGB", (240, 180), "white"), DEFAULTS)
        canvas.resize(400, 300)
        canvas.show()
        opposite = {"nw": "se", "n": "s", "ne": "sw", "e": "w",
                    "se": "nw", "s": "n", "sw": "ne", "w": "e"}
        deltas = {"nw": (-18, -14), "n": (0, -14), "ne": (18, -14), "e": (18, 0),
                  "se": (18, 14), "s": (0, 14), "sw": (-18, 14), "w": (-18, 0)}
        try:
            for handle_name, delta in deltas.items():
                canvas.restore([])
                canvas.history = [(canvas.image, canvas.alternate, canvas.cursor_enabled, [])]
                canvas.cursor_index = 0
                item = shape("rect", QPointF(40, 40), QPointF(100, 90), "#ff0000", 2)
                canvas.scene_data.addItem(item)
                item.setSelected(True)
                canvas.checkpoint()
                original = item.sceneBoundingRect()
                handles = canvas.resize_handles(original)
                anchor = handles[opposite[handle_name]]
                start = canvas.mapFromScene(handles[handle_name])
                finish = start + QPoint(*delta)
                for event_type, position, button, buttons in (
                        (QEvent.MouseButtonPress, start, Qt.LeftButton, Qt.LeftButton),
                        (QEvent.MouseMove, finish, Qt.NoButton, Qt.LeftButton),
                        (QEvent.MouseButtonRelease, finish, Qt.LeftButton, Qt.NoButton)):
                    self.app.sendEvent(canvas.viewport(), QMouseEvent(
                        event_type, QPointF(position),
                        QPointF(canvas.viewport().mapToGlobal(position)),
                        button, buttons, Qt.NoModifier))
                resized = item.sceneBoundingRect()
                self.assertAlmostEqual(resized.center().x() if handle_name in ("n", "s")
                                       else resized.left() if "w" in opposite[handle_name]
                                       else resized.right(),
                                       anchor.x(), delta=2, msg=handle_name)
                self.assertAlmostEqual(resized.center().y() if handle_name in ("e", "w")
                                       else resized.top() if "n" in opposite[handle_name]
                                       else resized.bottom(),
                                       anchor.y(), delta=2, msg=handle_name)
                if handle_name in ("e", "w", "ne", "nw", "se", "sw"):
                    self.assertGreater(resized.width(), original.width(), handle_name)
                if handle_name in ("n", "s", "ne", "nw", "se", "sw"):
                    self.assertGreater(resized.height(), original.height(), handle_name)
                canvas.undo()
                restored = canvas.annotations()[0].sceneBoundingRect()
                self.assertAlmostEqual(restored.width(), original.width(), delta=1, msg=handle_name)
                self.assertAlmostEqual(restored.height(), original.height(), delta=1, msg=handle_name)
                canvas.redo()
                redone = canvas.annotations()[0].sceneBoundingRect()
                self.assertAlmostEqual(redone.width(), resized.width(), delta=1, msg=handle_name)
                self.assertAlmostEqual(redone.height(), resized.height(), delta=1, msg=handle_name)
        finally:
            canvas.close()

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
                canvas.history = [(canvas.image, canvas.alternate, canvas.cursor_enabled, [])]
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
        from PySide6.QtCore import QEvent
        from PySide6.QtGui import QMouseEvent
        editor = EditorWindow(Image.new("RGB", (120, 100), "white"), dict(DEFAULTS))
        editor.resize(200, 180)
        editor.show()
        item = shape("rect", QPointF(10, 10), QPointF(20, 20), "#ff0000", 2)
        editor.canvas.scene_data.addItem(item)
        item.setSelected(True)
        self.app.processEvents()
        corner = editor.canvas.mapFromScene(item.sceneBoundingRect().bottomRight())
        self.app.sendEvent(editor.canvas.viewport(), QMouseEvent(
            QEvent.MouseMove, QPointF(corner),
            QPointF(editor.canvas.viewport().mapToGlobal(corner)),
            Qt.NoButton, Qt.NoButton, Qt.NoModifier))
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
                self.assertEqual(len(tool_rows) == 1, width in (1300, 2300, 3200), width)
            self.assertEqual(grid.getItemPosition(grid.indexOf(editor.toolbar.cursor_switch))[:2],
                             (0, 0), width)
            self.assertEqual(grid.indexOf(editor.toolbar.pen_color), -1, width)
            self.assertEqual(grid.getItemPosition(grid.indexOf(editor.toolbar.options_button))[:2],
                             (0, 1), width)
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
                          for index in range(editor.toolbar.tool_grid.count())}, {0})
        self.assertEqual(editor.canvas.alignment(), Qt.AlignCenter)
        editor.close()

    def test_toolbar_edit_rotation_gap_stays_compact(self):
        from config.config_manager import DEFAULTS
        editor = EditorWindow(Image.new("RGB", (120, 90), "white"), dict(DEFAULTS))
        editor.show()
        self.app.processEvents()
        self.app.processEvents()
        self.assertEqual(editor.width(), 1200)
        margins = editor.toolbar.section_layout.contentsMargins()
        self.assertLessEqual(margins.top(), 2)
        self.assertLessEqual(margins.bottom(), 2)
        self.assertLessEqual(editor.toolbar.section_layout.horizontalSpacing(), 2)
        self.assertLessEqual(editor.toolbar.section_layout.verticalSpacing(), 4)
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

    def test_recapture_restores_secondary_monitor_region_with_mixed_dpi(self):
        from PySide6.QtCore import QPoint, QRect
        from config.config_manager import DEFAULTS
        from core.dpi import DisplayMapper
        from screenshot.mask_window import MaskWindow

        bounds = {"left": 0, "top": 0, "width": 250, "height": 120}
        monitors = [
            {"left": 0, "top": 0, "width": 100, "height": 80},
            {"left": 100, "top": 0, "width": 150, "height": 120},
        ]
        infos = [{"geometry": QRect(0, 0, 100, 80), "dpr": 1.0},
                 {"geometry": QRect(100, 0, 100, 80), "dpr": 1.5}]
        mapper = DisplayMapper(bounds, monitors, infos)
        target_rect = [120, 30, 30, 20]
        with patch("screenshot.mask_window.DisplayMapper", return_value=mapper), \
             patch("screenshot.mask_window.QCursor.setPos") as set_cursor, \
             patch("screenshot.mask_window.QCursor.pos", return_value=QPoint(123, 26)), \
             patch("screenshot.mask_window.visible_windows", return_value=[]):
            mask = MaskWindow(Image.new("RGB", (250, 120), "white"), bounds, monitors,
                              dict(DEFAULTS, window_detection=False), initial_rect=target_rect)
        try:
            set_cursor.assert_called_once_with(QPoint(123, 26))
            self.assertEqual(mask.selection.rects, [QRect(120, 30, 30, 20)])
            self.assertEqual(mask.session.position, QPoint(134, 39))
            secondary = mask.session.views[1]
            context_events = []
            secondary.recapture_requested.connect(context_events.append)
            secondary.request_recapture()
            self.assertEqual(context_events[0]["monitor"], monitors[1])
            self.assertNotIn("rect", context_events[0])
        finally:
            mask.close()

    def test_restart_capture_forwards_monitor_context_or_last_editor_region(self):
        import logging
        from main import Application

        monitor = {"left": 1920, "top": 0, "width": 2560, "height": 1440}
        rect = [2000, 150, 440, 300]
        app = Application.__new__(Application)
        app.mask = Mock()
        app.config = Mock(data={"last_capture_rect": rect})
        app.logger = logging.getLogger("screensnap")
        app.start_capture = Mock()
        with patch("main.QTimer.singleShot") as schedule:
            app.restart_capture({"monitor": monitor, "rect": rect})
            schedule.call_args.args[1]()
            app.start_capture.assert_called_once_with("capture", initial_rect=None,
                                                      preferred_monitor=monitor)
            app.start_capture.reset_mock()
            editor = Mock()
            app.restart_capture(editor)
            schedule.call_args.args[1]()
            app.start_capture.assert_called_once_with("capture", initial_rect=None,
                                                      preferred_monitor=None)

    def test_slow_uia_query_retries_after_temporary_cooldown(self):
        from core import window_uia

        calls = []
        times = iter((0.0, 0.0, 0.5, 0.6, 0.7, 2.0, 2.1, 2.2))
        window_uia._disabled_until = 0.0
        with patch.object(window_uia, "module", return_value=object()), \
                patch.object(window_uia, "query", side_effect=lambda *args, **kwargs: calls.append(args) or []), \
                patch("core.window_uia.time.monotonic", side_effect=lambda: next(times)):
            window_uia.element_chain((10, 10))
            window_uia.element_chain((10, 10))
            window_uia.element_chain((10, 10))

        self.assertEqual(len(calls), 2)

    def test_show_mask_connects_recapture_for_every_monitor_view(self):
        from types import SimpleNamespace
        from main import Application

        primary, secondary = Mock(), Mock()
        primary.session = SimpleNamespace(views=[primary, secondary])
        primary.recapture_requested = Mock()
        secondary.recapture_requested = Mock()
        app = Application.__new__(Application)
        app.config = Mock(data={"cursor": False})
        app.logger = Mock()
        app._connect_sticker_signals = Mock()
        app.remember_region = Mock()
        app.handle_capture_selection = Mock()
        app.edit_images = Mock()
        app.save_capture_images = Mock()
        app.saved = Mock()
        app.initial_save_failed = Mock()
        app.close_all_editors = Mock()
        app.restart_capture = Mock()
        image = Image.new("RGB", (20, 10), "white")
        with patch("main.capture", return_value=(image, {}, [], None)), \
                patch("main.MaskWindow", return_value=primary), \
                patch("main.QTimer.singleShot"):
            app.show_mask("capture")

        primary.recapture_requested.connect.assert_called_once_with(app.restart_capture)
        secondary.recapture_requested.connect.assert_called_once_with(app.restart_capture)

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
            # 默认按月归档，图片落在月份子目录里。
            self.assertEqual(list(Path(folder).glob("**/*.png")), [first])
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

    def test_saving_edited_sticker_creates_new_file_without_replacing_source(self):
        from config.config_manager import DEFAULTS
        from core.screen_capture import to_qimage

        with tempfile.TemporaryDirectory() as folder:
            source_path = Path(folder) / "sticker.png"
            Image.new("RGB", (25, 20), "#23bc58").save(source_path)
            edited = Image.new("RGB", (25, 20), "#d02020")
            settings = {**DEFAULTS, "manual_dir": folder, "filename": "edited_sticker",
                        "copy_saved_image": False, "copy_saved_path": False}
            editor = EditorWindow(edited, settings, from_capture=False)
            try:
                saved_path = editor.save()
                self.assertNotEqual(Path(saved_path), source_path)
                self.assertTrue(Path(saved_path).exists())
                with Image.open(source_path) as original:
                    self.assertEqual(original.getpixel((10, 10))[:3], (35, 188, 88))
                with Image.open(saved_path) as saved:
                    self.assertEqual(saved.getpixel((10, 10))[:3], (208, 32, 32))
            finally:
                editor.close()

    def test_sticker_edit_menu_emits_image_copy_to_manager(self):
        from config.config_manager import DEFAULTS
        from PySide6.QtGui import QImage
        from sticker.sticker_manager import StickerManager
        from sticker.sticker_menu import build_menu

        image = QImage(32, 24, QImage.Format_ARGB32)
        image.fill(Qt.red)
        with tempfile.TemporaryDirectory() as folder, \
                patch("sticker.sticker_manager.data_dir", return_value=Path(folder)):
            manager = StickerManager(dict(DEFAULTS))
            sticker = manager.add(image)
            edits = []
            manager.edit_requested.connect(edits.append)
            menu = build_menu(sticker)
            try:
                action = next(action for action in menu.actions() if action.text() == "编辑")
                action.trigger()
                self.assertEqual(len(edits), 1)
                self.assertEqual(edits[0].size(), image.size())
                edits[0].fill(Qt.blue)
                self.assertEqual(sticker.pixmap.toImage().pixelColor(0, 0), Qt.red)
            finally:
                menu.close()
                sticker.close()
                manager._persist_timer.stop()

    def test_sticker_manager_caches_pillow_image_as_qimage(self):
        from config.config_manager import DEFAULTS
        from sticker.sticker_manager import StickerManager

        with tempfile.TemporaryDirectory() as folder, \
                patch("sticker.sticker_manager.data_dir", return_value=Path(folder)):
            manager = StickerManager(dict(DEFAULTS))
            item = manager.add(Image.new("RGBA", (16, 12), "#2a78bd"))
            try:
                self.assertEqual(item.pixmap.size().toTuple(), (16, 12))
                self.assertEqual(item.pixmap.toImage().pixelColor(5, 5).name(), "#2a78bd")
                self.assertTrue(Path(item.source).is_file())
            finally:
                item.close()
                manager._persist_timer.stop()

    def test_edit_sticker_opens_non_capture_editor_for_copy(self):
        from main import Application
        from PySide6.QtGui import QImage

        image = QImage(18, 14, QImage.Format_ARGB32)
        image.fill(Qt.green)
        application = Application.__new__(Application)
        application.edit_images = Mock()
        application.edit_sticker(image)
        images, = application.edit_images.call_args.args
        self.assertEqual(images[0][0].size(), image.size())
        self.assertIsNone(images[0][1])
        self.assertIs(application.edit_images.call_args.kwargs["from_capture"], False)

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

    def test_editor_paste_saves_emits_sticker_and_closes(self):
        from config.config_manager import DEFAULTS

        editor = EditorWindow(Image.new("RGB", (90, 70), "white"), DEFAULTS.copy())
        emitted = []
        editor.sticker_requested.connect(emitted.append)
        with patch.object(editor, "save") as save, patch.object(editor, "close") as close:
            editor.execute("paste")
        save.assert_called_once_with(automatic=True)
        close.assert_called_once_with()
        self.assertEqual(len(emitted), 1)
        self.assertIsNotNone(emitted[0])
        editor.close()

    def test_editor_paste_creates_visible_sticker_through_manager(self):
        from types import SimpleNamespace
        from config.config_manager import DEFAULTS
        from main import Application

        with tempfile.TemporaryDirectory() as folder, \
                patch("sticker.sticker_manager.data_dir", return_value=Path(folder)):
            settings = dict(DEFAULTS, auto_dir=folder, manual_dir=folder, filename="single")
            manager = StickerManager(settings)
            app = Application.__new__(Application)
            app.config = SimpleNamespace(data=settings)
            app.editors = []
            app.capture_notice = None
            app.logger = Mock()
            app.settings_window = Mock()
            app.stickers = manager
            app.notify = Mock()
            app.saved = Mock()
            requested_position = QPoint(120, 80)
            Application.edit_images(app, [(Image.new("RGB", (90, 70), "white"), None)],
                                    positions=[requested_position], from_capture=False)
            editor = app.editors[0]
            next(button for button in editor.toolbar.output_buttons
                 if button.text() == "贴图").click()
            self.app.processEvents()
            self.assertEqual(len(manager.items), 1)
            self.assertTrue(manager.items[0].isVisible())
            self.assertEqual((manager.items[0].pixmap.width(), manager.items[0].pixmap.height()),
                             (90, 70))
            item = manager.items[0]
            self.assertEqual(item.pos() + QPoint(item.padding(), item.padding()),
                             requested_position)
            self.assertTrue(list(Path(folder).rglob("single*.png")))
            manager.close_all()
            self.app.processEvents()

    def test_single_image_open_quick_edit_can_paste_as_sticker(self):
        from types import SimpleNamespace
        from config.config_manager import DEFAULTS
        from main import Application

        with tempfile.TemporaryDirectory() as folder, \
                patch("sticker.sticker_manager.data_dir", return_value=Path(folder)):
            source = Path(folder) / "quick-edit.png"
            Image.new("RGBA", (90, 70), "#2a78bd").save(source)
            settings = dict(DEFAULTS, auto_dir=folder, manual_dir=folder,
                            filename="quick-edit")
            manager = StickerManager(settings)
            app = Application.__new__(Application)
            app.config = SimpleNamespace(data=settings)
            app.editors = []
            app.capture_notice = None
            app.logger = Mock()
            app.settings_window = Mock()
            app.stickers = manager
            app.notify = Mock()
            app.saved = Mock()

            with patch("main.QFileDialog.getOpenFileNames",
                       return_value=([str(source)], "")):
                Application.open_and_edit_image(app)
            editor = app.editors[0]
            paste_button = next(button for button in editor.toolbar.output_buttons
                                if button.text() == "贴图")
            paste_button.click()
            self.app.processEvents()

            try:
                self.assertEqual(len(manager.items), 1)
                self.assertTrue(manager.items[0].isVisible())
                self.assertEqual(manager.items[0].pixmap.size().toTuple(), (90, 70))
                self.assertEqual(manager.items[0].pixmap.toImage().pixelColor(5, 5).name(),
                                 "#2a78bd")
            finally:
                manager.close_all()
                self.app.processEvents()

    def test_clipboard_image_quick_edit_can_paste_as_sticker(self):
        from types import SimpleNamespace
        from config.config_manager import DEFAULTS
        from main import Application
        from PySide6.QtGui import QImage, QColor

        with tempfile.TemporaryDirectory() as folder, \
                patch("sticker.sticker_manager.data_dir", return_value=Path(folder)):
            image = QImage(90, 70, QImage.Format_ARGB32)
            image.fill(QColor("#2a78bd"))
            settings = dict(DEFAULTS, auto_dir=folder, manual_dir=folder,
                            filename="clipboard-quick-edit")
            manager = StickerManager(settings)
            app = Application.__new__(Application)
            app.config = SimpleNamespace(data=settings)
            app.editors = []
            app.capture_notice = None
            app.logger = Mock()
            app.settings_window = Mock()
            app.stickers = manager
            app.notify = Mock()
            app.saved = Mock()

            clipboard = Mock()
            clipboard.image.return_value = image
            with patch("main.QGuiApplication.clipboard", return_value=clipboard):
                Application.edit_clipboard_image(app)
            editor = app.editors[0]
            paste_button = next(button for button in editor.toolbar.output_buttons
                                if button.text() == "贴图")
            paste_button.click()
            self.app.processEvents()

            try:
                self.assertEqual(len(manager.items), 1)
                self.assertTrue(manager.items[0].isVisible())
                self.assertEqual(manager.items[0].pixmap.size().toTuple(), (90, 70))
                self.assertEqual(manager.items[0].pixmap.toImage().pixelColor(5, 5).name(),
                                 "#2a78bd")
            finally:
                manager.close_all()
                self.app.processEvents()

    def test_capture_single_selection_can_paste_after_initial_autosave(self):
        from types import SimpleNamespace
        from config.config_manager import DEFAULTS
        from main import Application

        with tempfile.TemporaryDirectory() as folder, \
                patch("sticker.sticker_manager.data_dir", return_value=Path(folder)):
            settings = dict(DEFAULTS, auto_dir=folder, manual_dir=folder,
                            capture_after_selection="edit", bubble=False, sound=False)
            manager = StickerManager(settings)
            app = Application.__new__(Application)
            app.config = SimpleNamespace(data=settings)
            app.editors = []
            app.capture_notice = None
            app.logger = Mock()
            app.settings_window = Mock()
            app.stickers = manager
            app.notify = Mock()
            app.saved = Mock()

            Application.handle_capture_selection(
                app, [(Image.new("RGB", (90, 70), "#2a78bd"), None)],
                positions=[QPoint(160, 120)])
            editor = app.editors[0]
            self.app.processEvents()
            self.assertFalse(editor.initial_capture_save_pending)
            paste_button = next(button for button in editor.toolbar.output_buttons
                                if button.text() == "贴图")
            paste_button.click()
            self.app.processEvents()

            try:
                self.assertEqual(len(manager.items), 1)
                self.assertTrue(manager.items[0].isVisible())
                self.assertEqual(manager.items[0].pos() +
                                 QPoint(manager.items[0].padding(),
                                        manager.items[0].padding()),
                                 QPoint(160, 120))
                self.assertEqual(manager.items[0].pixmap.toImage().pixelColor(5, 5).name(),
                                 "#2a78bd")
                self.assertFalse(app.saved.call_args.kwargs["notify"])
            finally:
                manager.close_all()
                self.app.processEvents()

    def test_editor_paste_without_source_position_shows_sticker_beside_editor(self):
        from types import SimpleNamespace
        from config.config_manager import DEFAULTS
        from main import Application

        with tempfile.TemporaryDirectory() as folder, \
                patch("sticker.sticker_manager.data_dir", return_value=Path(folder)):
            settings = dict(DEFAULTS, auto_dir=folder, manual_dir=folder,
                            filename="single")
            manager = StickerManager(settings)
            app = Application.__new__(Application)
            app.config = SimpleNamespace(data=settings)
            app.editors = []
            app.capture_notice = None
            app.logger = Mock()
            app.settings_window = Mock()
            app.stickers = manager
            app.notify = Mock()
            app.saved = Mock()
            Application.edit_images(
                app, [(Image.new("RGB", (90, 70), "white"), None)],
                from_capture=False)
            editor = app.editors[0]
            editor_frame = editor.frameGeometry()
            next(button for button in editor.toolbar.output_buttons
                 if button.text() == "贴图").click()
            self.app.processEvents()

            self.assertEqual(len(manager.items), 1)
            item = manager.items[0]
            self.assertTrue(item.isVisible())
            self.assertFalse(item.geometry().intersects(editor_frame))
            manager.close_all()
            self.app.processEvents()

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
            ordered = [toolbar.cursor_switch, toolbar.options_button]
            ordered.extend(toolbar.tool_buttons[key] for key in "select pen marker rect ellipse text arrow mosaic eraser picker crop".split())
            for index, button in enumerate(ordered):
                row, column = divmod(index, columns)
                self.assertEqual(grid.getItemPosition(grid.indexOf(button)), (row, column, 1, 1))
            self.assertEqual(grid.indexOf(toolbar.pen_color), -1)
            self.assertEqual(toolbar.image_grid.indexOf(toolbar.cursor_switch), -1)
            self.assertEqual(toolbar.pen_color.text(), "颜色")
            self.assertNotIn("square", toolbar.tool_buttons)
            self.assertIn(toolbar.pen_color, toolbar.options_button.menu().findChildren(type(toolbar.pen_color)))
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
        first_row = [toolbar.cursor_switch, toolbar.options_button]
        first_row.extend(toolbar.tool_buttons[key] for key in "select pen marker rect".split())
        positions = [toolbar.tool_grid.getItemPosition(toolbar.tool_grid.indexOf(widget))
                     for widget in first_row]
        self.assertEqual(positions, [(0, column, 1, 1) for column in range(6)])
        self.assertEqual(toolbar.tool_grid.indexOf(toolbar.pen_color), -1)
        for widget in (toolbar.options_button, *toolbar.tool_buttons.values()):
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
        eraser = toolbar.tool_buttons["eraser"].icon().pixmap(24, 24).toImage()
        delete = next(button for button in toolbar.edit_buttons if button.text() == "删除").icon().pixmap(24, 24).toImage()
        self.assertNotEqual(eraser, delete)
        transform_icons = [toolbar.image_buttons[index].icon().pixmap(24, 24).toImage()
                           for index in (3, 4, 5, 6)]
        for index, icon_image in enumerate(transform_icons):
            self.assertNotIn(icon_image, transform_icons[:index])
        from ui.action_icons import action_icon
        for name in ("camera", "resize", "window_edit", "eraser", "rotate", "rotate_180",
                 "rotate_left", "rotate_right", "layer_top", "layer_bottom", "pin",
                     "flip_horizontal", "flip_vertical", "clipboard_image", "clipboard_edit",
                     "sticker", "layers",
                 "lock", "unlock", "border", "shadow", "visibility", "hidden", "group",
                 "group_add", "group_move",
                 "locate", "rename", "click_through", "settings"):
            icon_image = action_icon(name).pixmap(24, 24).toImage()
            self.assertTrue(any(icon_image.pixelColor(x, y).alpha() for x in range(24)
                    for y in range(24)), name)

    def test_toolbar_shortcut_tooltips_use_current_bindings(self):
        from config.config_manager import DEFAULTS
        settings = dict(DEFAULTS, hotkeys={**DEFAULTS["hotkeys"], "paste": "ctrl+alt+p"})
        editor = EditorWindow(Image.new("RGB", (30, 20), "white"), settings)
        paste = next(button for button in editor.toolbar.output_buttons if button.text() == "贴图")
        self.assertTrue(paste.toolTip().startswith("贴图\n"))
        self.assertIn("Ctrl+Alt+P", paste.toolTip())
        editor.toolbar.set_hotkeys({**settings["hotkeys"], "paste": "f9"})
        self.assertTrue(paste.toolTip().startswith("贴图\n"))
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

    def test_tool_widths_are_independent_and_options_follow_tool(self):
        from PySide6.QtCore import QRectF
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
            expected_range = (10, 100) if tool == "eraser" else (1, 50)
            self.assertEqual((toolbar.pen_width.minimum(), toolbar.pen_width.maximum()), expected_range)
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
        toolbar.tool_buttons["eraser"].click()
        self.assertFalse(toolbar.previews["eraser"].isHidden())
        eraser_scene = toolbar.previews["eraser"].build(QRectF(0, 0, 320, 96))
        self.assertGreater(len(eraser_scene.items()), 1)
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
                validate({key: 101 if key == "eraser_width" else 51})
        self.assertEqual(validate({})["eraser_width"], 30)

    def test_eraser_width_range_and_preview_follow_tool(self):
        from PySide6.QtCore import QRectF
        from config.config_manager import DEFAULTS, validate
        from editor.toolbar_widget import ToolbarWidget

        self.assertEqual(validate({})["eraser_width"], 30)
        for width in (9, 101):
            with self.assertRaises(ValueError):
                validate({"eraser_width": width})
        toolbar = ToolbarWidget(settings=dict(DEFAULTS))
        toolbar.tool_buttons["eraser"].click()
        self.assertEqual((toolbar.pen_width.minimum(), toolbar.pen_width.maximum()), (10, 100))
        self.assertEqual(toolbar.pen_width.value(), 30)
        self.assertFalse(toolbar.previews["eraser"].isHidden())
        scene = toolbar.previews["eraser"].build(QRectF(0, 0, 320, 96))
        self.assertGreater(len(scene.items()), 1)
        toolbar.pen_width.setValue(100)
        self.assertEqual(toolbar.tool_widths["eraser"], 100)
        toolbar.tool_buttons["pen"].click()
        self.assertEqual((toolbar.pen_width.minimum(), toolbar.pen_width.maximum()), (1, 50))
        self.assertTrue(toolbar.previews["eraser"].isHidden())
        toolbar.close()

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
        for tool, rows in (("pen", (0, 1, 12)), ("marker", (0, 1, 2, 12)),
             ("text", (0, 3, 4, 5, 12)), ("mosaic", (6, 7, 12)),
             ("arrow", (0, 1, 8, 12)),
             ("rect", (0, 1, 10, 12, 13, 14, 15, 16)),
             ("ellipse", (0, 1, 11, 12, 17, 18)),
             ("eraser", (1, 12)), ("crop", (0, 9, 12))):
            toolbar.tool_buttons[tool].click()
            self.app.processEvents()
            panel.adjustSize()
            panel.layout().activate()
            self.assertGreaterEqual(toolbar.options_button.menu().height(), panel.sizeHint().height())
            expected_labels = {
                0: {"pen": "画笔颜色", "marker": "记号笔颜色",
                    "text": "文字颜色", "arrow": "箭头颜色",
                    "rect": "矩形边框/填充颜色", "ellipse": "椭圆边框/填充颜色",
                    "crop": "裁剪框颜色"}.get(tool),
                1: "直径" if tool == "eraser" else "线宽",
                2: "透明度", 3: "字体", 4: "字号", 5: "对齐",
                6: "效果", 7: "颗粒", 8: "样式", 9: "裁剪线宽",
                10: "矩形线型", 11: "椭圆线型", 12: "预览",
                13: "矩形圆角", 14: "圆角半径", 15: "矩形填充",
                16: "填充透明度", 17: "椭圆填充", 18: "填充透明度",
            }
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
            if tool == "pen":
                self.assertLess(panel.sizeHint().height(), 250)
            if tool == "rect":
                self.assertLess(panel.sizeHint().height(), 440)
            if tool == "text":
                self.assertEqual(toolbar.font_size.value(), 18)
            if tool == "arrow":
                self.assertEqual(set(toolbar.choice_buttons["arrow_style"]),
                                  {"filled", "open", "double", "double_filled",
                                   "solid_line", "open_line", "solid_dash", "open_dash"})
                self.assertTrue(toolbar.choice_buttons["arrow_style"]["open"].isChecked() is False)
                self.assertGreaterEqual(panel.minimumWidth(), 760)
            if tool in ("rect", "ellipse"):
                self.assertLessEqual(panel.minimumWidth(), 520)
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
                     ["贴图", "外观", "保存", "放弃", "关闭全部"])
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
            for action in sticker_menu.actions():
                if action.menu() is not None and action.text() in ("旋转", "透明背景模式", "贴图分组", "透明度"):
                    self.assertIn("<br>", action.toolTip(), action.text())
                    for child in action.menu().actions():
                        if not child.isSeparator() and not isinstance(child, QWidgetAction):
                            self.assertIn("<br>", child.toolTip(), child.text())
            sticker.close()

    def test_capture_actions_belong_to_inline_editor_not_window_editor(self):
        from config.config_manager import DEFAULTS
        from editor.editor_window import EditorWindow
        from screenshot.mask_window import MaskWindow

        bounds = {"left": 0, "top": 0, "width": 120, "height": 80}
        settings = {**DEFAULTS, "crosshair": False, "magnifier": False}
        window_editor = EditorWindow(Image.new("RGB", (40, 30), "white"), settings)
        with patch("screenshot.mask_window.visible_windows", return_value=[]):
            mask = MaskWindow(Image.new("RGB", (120, 80), "blue"), bounds,
                              [bounds], settings)
        try:
            mask.selection.rects.append(QRect(10, 10, 40, 30))
            recapture_events = []
            mask.recapture_requested.connect(recapture_events.append)
            mask.complete()
            inline_editor = mask.session.inline_editor
            self.assertEqual(window_editor.toolbar.capture_action_buttons, [])
            window_editor.toolbar.reflow(1100)
            self.assertEqual(len(window_editor.toolbar.edit_buttons), 5)
            self.assertTrue(all(window_editor.toolbar.edit_grid.itemAtPosition(0, column)
                                is not None for column in range(5)))
            self.assertEqual([button.text() for button in inline_editor.toolbar.capture_action_buttons],
                             ["自定义尺寸", "重新截图"])
            output_grid = inline_editor.toolbar.output_grid
            self.assertEqual(output_grid.verticalSpacing(), 0)
            # 输出按钮与编辑按钮之间还有一个间隔占位控件。
            self.assertEqual(output_grid.count(),
                             len([button for button in inline_editor.toolbar.output_buttons
                                  if not button.isHidden()]) +
                             len(inline_editor.toolbar.edit_buttons) + 1)
            rows = {output_grid.getItemPosition(index)[0]
                    for index in range(output_grid.count())}
            # 输出组与编辑组合并为一行，减少原地编辑的纵向占用。
            self.assertEqual(rows, {0})
            inline_editor.toolbar.capture_action_buttons[1].click()
            self.assertEqual(recapture_events, [{"monitor": dict(mask.monitor)}])
        finally:
            window_editor.close()
            mask.close()

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

    def test_sticker_wheel_shows_scale_percent_for_one_second(self):
        from PySide6.QtGui import QImage, QWheelEvent
        from config.config_manager import DEFAULTS

        with tempfile.TemporaryDirectory() as folder, \
                patch("sticker.sticker_manager.data_dir", return_value=Path(folder)):
            manager = StickerManager(DEFAULTS)
            image = QImage(24, 18, QImage.Format_ARGB32)
            image.fill(Qt.red)
            first = manager.add(image)
            second = manager.add(image)
            manager.set_selected_items([first, second])
            first.scale_hint_timer.setInterval(30)
            second.scale_hint_timer.setInterval(30)
            event = QWheelEvent(QPointF(8, 8), QPointF(8, 8), QPoint(0, 0), QPoint(0, 120),
                                Qt.NoButton, Qt.NoModifier, Qt.ScrollUpdate, False)
            try:
                first.wheelEvent(event)
                self.assertEqual(first.scale_hint_text, "110%")
                self.assertEqual(second.scale_hint_text, "110%")
                self.assertTrue(first.scale_hint_timer.isActive())
                QTest.qWait(70)
                self.assertEqual(first.scale_hint_text, "")
                self.assertEqual(second.scale_hint_text, "")
            finally:
                manager.close_all()
                self.app.processEvents()

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
                item.scale_factor = 1.6
                item.resize(item.window_size())
                item.setWindowOpacity(0.65)
                QTest.qWait(350)
                saved_state = json.loads((Path(folder) / "stickers.json").read_text(encoding="utf-8"))[0]
                self.assertEqual((saved_state["x"], saved_state["y"]), (12, 25))
                self.assertEqual((saved_state["width"], saved_state["height"]),
                                 (item.width(), item.height()))
                self.assertAlmostEqual(saved_state["scale"], 1.6)
                self.assertAlmostEqual(saved_state["opacity"], 0.65, places=2)
                self.assertTrue(list((Path(folder) / "sticker_cache").glob("*.png")))
                manager.close_all()
                self.app.processEvents()
                restored = StickerManager(DEFAULTS)
                restored.restore()
                self.assertEqual(len(restored.items), 1)
                self.assertEqual((restored.items[0].x(), restored.items[0].y()), (12, 25))
                self.assertEqual((restored.items[0].width(), restored.items[0].height()),
                                 (saved_state["width"], saved_state["height"]))
                self.assertAlmostEqual(restored.items[0].scale_factor, 1.6)
                self.assertAlmostEqual(restored.items[0].windowOpacity(), 0.65, places=2)
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

    def test_paste_latest_skips_open_history_then_reactivates_latest(self):
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
                self.assertTrue(manager.paste_latest())
                self.assertEqual(Path(manager.active_sticker.source), paths[3])
                manager.items[1].close()
                self.assertTrue(manager.paste_latest())
                self.assertEqual(Path(manager.items[-1].source), paths[2])
                manager.items[-1].close()
                paths[2].unlink()
                self.assertTrue(manager.paste_latest())
                self.assertEqual(Path(manager.active_sticker.source), paths[3])
                paths[2].write_bytes(b"not an image")
                self.assertTrue(manager.paste_latest())
                self.assertEqual(Path(manager.active_sticker.source), paths[3])
            finally:
                manager.close_all()
                self.app.processEvents()

    def test_paste_latest_restores_last_position_and_scale(self):
        import os
        from PySide6.QtGui import QImage
        from config.config_manager import DEFAULTS

        with tempfile.TemporaryDirectory() as folder, \
                patch("sticker.sticker_manager.data_dir", return_value=Path(folder)):
            settings = dict(DEFAULTS, auto_dir=folder)
            source = Path(folder) / "latest.png"
            newer_source = Path(folder) / "newer.png"
            Image.new("RGB", (40, 30), "white").save(source)
            Image.new("RGB", (48, 36), "white").save(newer_source)
            os.utime(source, (10, 10))
            os.utime(newer_source, (20, 20))
            manager = StickerManager(settings)
            first = manager.add(QImage(str(source)), str(source))
            first.move(143, 257)
            first.scale_factor = 2.25
            first.resize(first.window_size())
            manager.persist()
            first.close()
            self.app.processEvents()

            manager = StickerManager(settings)
            self.assertTrue(manager.paste_latest())
            restored = manager.items[-1]
            self.assertEqual(Path(restored.source), newer_source)
            self.assertEqual(restored.pos(), QPoint(143, 257))
            self.assertEqual(restored.scale_factor, 2.25)
            restored.close()
            self.app.processEvents()

            manager = StickerManager(settings)
            self.assertTrue(manager.paste_latest())
            self.assertEqual(Path(manager.items[-1].source), newer_source)
            self.assertEqual(manager.items[-1].pos(), QPoint(143, 257))
            self.assertEqual(manager.items[-1].scale_factor, 2.25)
            manager.close_all()
            self.app.processEvents()

    def test_paste_latest_without_saved_layout_uses_screen_center(self):
        from PySide6.QtGui import QCursor, QGuiApplication
        from config.config_manager import DEFAULTS

        with tempfile.TemporaryDirectory() as folder, \
                patch("sticker.sticker_manager.data_dir", return_value=Path(folder)):
            source = Path(folder) / "latest.png"
            Image.new("RGB", (40, 30), "white").save(source)
            manager = StickerManager(dict(DEFAULTS, auto_dir=folder))
            screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
            bounds = screen.availableGeometry()

            self.assertTrue(manager.paste_latest())
            item = manager.items[-1]
            self.assertEqual(item.pos(), bounds.center() - QPoint(item.width() // 2,
                                                                    item.height() // 2))
            self.assertTrue((Path(folder) / "sticker_placements.json").is_file())
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
                with patch("sticker.sticker_manager.QFileDialog.getOpenFileNames", return_value=([], "")):
                    actions["从文件打开替换此贴图"].trigger()
                self.assertEqual(sticker.source, str(first))
                with patch("sticker.sticker_manager.QFileDialog.getOpenFileNames",
                           return_value=([str(second), str(first)], "")):
                    actions["从文件打开替换此贴图"].trigger()
                self.assertEqual(sticker.source, str(second))
                self.assertEqual(len(manager.items), 2)
                self.assertEqual(manager.items[-1].source, str(first))
                with patch("sticker.sticker_manager.QFileDialog.getOpenFileNames", return_value=([str(first)], "")):
                    actions["从文件打开新贴图"].trigger()
                self.assertEqual(len(manager.items), 3)
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

    def test_clipboard_paste_cycles_history_and_close_keeps_entries(self):
        from PySide6.QtGui import QGuiApplication
        from config.config_manager import DEFAULTS

        board = QGuiApplication.clipboard()
        with tempfile.TemporaryDirectory() as folder, \
                patch("sticker.sticker_manager.data_dir", return_value=Path(folder)):
            board.clear()
            manager = StickerManager(DEFAULTS, board)
            try:
                board.setText("older clipboard text")
                self.app.processEvents()
                board.setText("newer clipboard text")
                self.app.processEvents()
                self.assertEqual(len(manager.clipboard_history), 2)

                newest = manager.paste_clipboard()
                self.assertEqual(newest.origin["text"], "newer clipboard text")
                older = manager.paste_clipboard()
                self.assertEqual(older.origin["text"], "older clipboard text")
                older.close()
                self.app.processEvents()

                self.assertEqual(len(manager.clipboard_history), 2)
                self.assertEqual(manager.paste_clipboard().origin["text"],
                                 "newer clipboard text")
                self.assertEqual(manager.clear_clipboard_history(), 2)
                self.assertFalse(manager.clipboard_history)
            finally:
                manager.close_all()
                board.clear()
                self.app.processEvents()

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

    def test_clipboard_history_persists_across_manager_restart(self):
        from PySide6.QtGui import QGuiApplication
        from config.config_manager import DEFAULTS

        board = QGuiApplication.clipboard()
        with tempfile.TemporaryDirectory() as folder, \
                patch("sticker.sticker_manager.data_dir", return_value=Path(folder)):
            board.clear()
            manager = StickerManager(DEFAULTS, board)
            board.setText("survive restart")
            self.app.processEvents()
            self.assertEqual(len(manager.clipboard_history), 1)
            manager.persist_clipboard_history()
            manager.close_all()
            board.clear()
            self.app.processEvents()

            restored = StickerManager(DEFAULTS, board)
            try:
                self.assertEqual([source.text for source in restored.clipboard_history],
                                 ["survive restart"])
                item = restored.paste_clipboard()
                self.assertEqual(item.origin["text"], "survive restart")
                self.assertTrue((Path(folder) / "clipboard_history.json").is_file())
                self.assertEqual(len(list((Path(folder) / "clipboard_history").glob("*.png"))), 1)
            finally:
                restored.close_all()
                manager.close_all()
                board.clear()
                self.app.processEvents()

    def test_sticker_menu_offers_origin_actions_for_text_and_files(self):
        from PySide6.QtGui import QGuiApplication, QImage
        from PySide6.QtWidgets import QLabel, QSlider, QWidgetAction
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
                menu = build_menu(with_origin)
                self.assertIn("复制文字", {action.text() for action in menu.actions()})
                opacity_menu_action = next(action for action in menu.actions()
                                            if action.text() == "透明度")
                opacity_menu = opacity_menu_action.menu()
                opacity_action = next(action for action in opacity_menu.actions()
                                      if isinstance(action, QWidgetAction))
                opacity_widget = opacity_action.defaultWidget()
                slider = opacity_widget.findChild(QSlider)
                value_label = opacity_widget.findChild(QLabel)
                self.assertGreaterEqual(slider.minimumWidth(), 220)
                slider.setValue(42)
                self.assertEqual(value_label.text(), "42%")
                self.assertAlmostEqual(with_origin.windowOpacity(), 0.42, places=2)
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
                with patch.object(mask.mapper, "logical_global_to_physical_global",
                                  return_value=QPoint(20, 20)):
                    mask.poll_hover()
                # 悬停取最内层元素。
                self.assertEqual(mask.hover_rect, QRect(10, 10, 50, 40))
                mask.update()
                self.app.processEvents()
                rendered = mask.grab().toImage()
                # reveal 模式下悬停矩形内应透出原图。小屏上顶部提示条与尺寸徽标会压住
                # 矩形中间，单点取样不稳定，改为在矩形内多点判断是否出现原图白色。
                revealed = {rendered.pixelColor(x, y).name()
                            for x in range(12, 57, 3) for y in range(12, 48, 2)}
                self.assertIn("#ffffff", revealed)
                self.assertEqual(rendered.pixelColor(90, 70).name(), "#666666")
                self.assertEqual(settings["window_hover_fill_mode"], "reveal")
                QTest.mousePress(mask, Qt.LeftButton, Qt.NoModifier, QPoint(20, 20))
                QTest.mouseRelease(mask, Qt.LeftButton, Qt.NoModifier, QPoint(20, 20))
                self.assertEqual(mask.selection.rects, [QRect(10, 10, 50, 40)])
            finally:
                mask.close()

    def test_hover_detection_works_on_every_monitor(self):
        from PySide6.QtCore import QPoint, QRect
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
                with patch.object(second.mapper, "logical_global_to_physical_global",
                                  return_value=QPoint(120, 20)):
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
                             ["arrow", "crop", "ellipse", "eraser", "marker", "mosaic", "pen", "rect", "text"])
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

    def test_output_appearance_is_visible_and_preview_updates(self):
        from config.config_manager import DEFAULTS
        from PySide6.QtWidgets import QCheckBox
        from editor.toolbar_widget import ToolbarWidget, settings_icon

        toolbar = ToolbarWidget(settings=dict(DEFAULTS))
        commands = []
        toolbar.command.connect(commands.append)
        try:
            self.assertEqual(toolbar.output_preview.kind, "output")
            self.assertFalse(toolbar.appearance_menu.isVisible())
            self.assertIs(toolbar.appearance_details.window(), toolbar.appearance_menu)
            self.assertIs(toolbar.output_preview.window(), toolbar.appearance_menu)
            self.assertLessEqual(toolbar.output_preview.minimumHeight(), 128)
            for key, label in (("editor_image_round_corners", "启用圆角"),
                               ("editor_image_border_enabled", "启用边框"),
                               ("editor_image_shadow_enabled", "启用阴影")):
                self.assertIsInstance(toolbar.appearance_controls[key], QCheckBox)
                self.assertEqual(toolbar.appearance_controls[key].text(), label)
            self.assertEqual(toolbar.appearance_toggle.icon().pixmap(24, 24).toImage(),
                             settings_icon().pixmap(24, 24).toImage())
            self.assertEqual([button.text() for button in toolbar.output_buttons[:3]],
                             ["贴图", "外观", "保存"])
            self.assertFalse(toolbar.output_preview.isHidden())
            toolbar.appearance_toggle.click()
            self.app.processEvents()
            self.assertTrue(toolbar.appearance_menu.isVisible())
            toolbar.appearance_toggle.click()
            self.app.processEvents()
            self.assertFalse(toolbar.appearance_menu.isVisible())
            self.assertFalse(toolbar.appearance_toggle.isChecked())
            toolbar.appearance_toggle.click()
            self.app.processEvents()
            self.assertTrue(toolbar.appearance_toggle.isChecked())
            for width in (1300, 950, 700, 420, 1300):
                toolbar.resize(width, 760)
                self.app.processEvents()
                self.assertTrue(toolbar.appearance_menu.isVisible(), width)
            toolbar.appearance_menu.close()
            self.assertFalse(toolbar.appearance_menu.isVisible())
            self.assertFalse(toolbar.appearance_toggle.isChecked())
            toolbar.appearance_toggle.click()
            self.app.processEvents()
            self.assertTrue(toolbar.appearance_menu.isVisible())
            toolbar.appearance_menu.close()
            self.assertFalse(toolbar.output_preview.isHidden())
            action_texts = {action.text() for action in toolbar.options_button.menu().actions()}
            self.assertNotIn("输出图像外观", action_texts)
            self.assertIn("自定义尺寸", [button.text() for button in toolbar.edit_buttons])
            self.assertIn("重新截图", [button.text() for button in toolbar.edit_buttons])
            toolbar.edit_buttons[-2].click()
            toolbar.edit_buttons[-1].click()
            self.assertEqual(commands, ["custom_size", "recapture"])
            toolbar.output_preview.scene = "cached"
            toolbar.appearance_controls["editor_image_border_width"].setValue(8)
            self.assertIsNone(toolbar.output_preview.scene)
            setting_events = []
            toolbar.setting_changed.connect(lambda key, value: setting_events.append((key, value)))
            toolbar.sync_appearance_controls(dict(DEFAULTS,
                editor_image_round_corners=False,
                editor_image_corner_radius=24,
                editor_image_border_width=6))
            self.assertFalse(toolbar.appearance_controls["editor_image_round_corners"].isChecked())
            self.assertEqual(toolbar.appearance_controls["editor_image_corner_radius"].value(), 24)
            self.assertEqual(toolbar.appearance_value_labels["editor_image_corner_radius"].text(), "24 px")
            self.assertEqual(setting_events, [])
        finally:
            toolbar.close()

    def test_output_appearance_stays_open_while_adjusting_and_using_modal_picker(self):
        from PySide6.QtCore import QEvent, QPointF, Qt
        from PySide6.QtGui import QCursor, QMouseEvent
        from PySide6.QtWidgets import QDialog
        from config.config_manager import DEFAULTS
        from editor.toolbar_widget import ToolbarWidget

        toolbar = ToolbarWidget(settings=dict(DEFAULTS))
        original_cursor_position = QCursor.pos()
        try:
            toolbar.show()
            self.app.processEvents()
            QTest.mouseClick(toolbar.appearance_toggle, Qt.LeftButton)
            self.app.processEvents()
            self.assertTrue(toolbar.appearance_menu.isVisible())
            QTest.mouseClick(toolbar.appearance_toggle, Qt.LeftButton)
            self.app.processEvents()
            self.assertFalse(toolbar.appearance_menu.isVisible())
            QTest.mouseClick(toolbar.appearance_toggle, Qt.LeftButton)
            self.app.processEvents()
            self.assertTrue(toolbar.appearance_menu.isVisible())
            QCursor.setPos(toolbar.appearance_toggle.mapToGlobal(
                toolbar.appearance_toggle.rect().center()))
            with patch("editor.toolbar_widget.QApplication.mouseButtons",
                       return_value=Qt.LeftButton):
                toolbar.appearance_menu.hide()
            self.app.processEvents()
            self.assertTrue(toolbar.appearance_toggle.isChecked())
            toolbar.appearance_toggle.setChecked(False)
            toolbar.appearance_toggle.clicked.emit()
            self.assertFalse(toolbar.appearance_menu.isVisible())
            QTest.mouseClick(toolbar.appearance_toggle, Qt.LeftButton)
            self.app.processEvents()
            self.assertTrue(toolbar.appearance_menu.isVisible())
            QCursor.setPos(toolbar.appearance_toggle.mapToGlobal(
                toolbar.appearance_toggle.rect().center()))
            with patch("editor.toolbar_widget.QApplication.mouseButtons",
                       return_value=Qt.LeftButton):
                toolbar.appearance_menu.hide()
            self.app.processEvents()
            self.assertTrue(toolbar.appearance_toggle.isChecked())
            toolbar.appearance_toggle.setChecked(False)
            toolbar.appearance_toggle.clicked.emit()
            self.app.processEvents()
            self.assertFalse(toolbar.appearance_menu.isVisible())
            QTest.mouseClick(toolbar.appearance_toggle, Qt.LeftButton)
            self.app.processEvents()
            self.assertTrue(toolbar.appearance_menu.isVisible())
            QTest.mouseClick(
                toolbar.appearance_controls["editor_image_border_enabled"],
                Qt.LeftButton)
            self.app.processEvents()
            self.assertTrue(toolbar.appearance_menu.isVisible())

            picker = QDialog(toolbar.appearance_panel)
            picker.setModal(True)
            picker.show()
            self.app.processEvents()
            self.assertIs(self.app.activeModalWidget(), picker)
            outside_click = QMouseEvent(
                QEvent.MouseButtonPress, QPointF(), QPointF(-100, -100),
                Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
            toolbar.eventFilter(toolbar, outside_click)
            self.assertTrue(toolbar.appearance_menu.isVisible())
            picker.close()
        finally:
            QCursor.setPos(original_cursor_position)
            toolbar.close()

    def test_output_shadow_does_not_shrink_preview_subject(self):
        from PySide6.QtGui import QColor, QImage
        from config.config_manager import DEFAULTS
        from ui.widgets import annotation_preview
        from ui.widgets.annotation_preview import AnnotationPreview

        sample = QImage(40, 20, QImage.Format_RGB32)
        sample.fill(QColor("#ff0000"))

        def red_bounds(scene):
            image = scene.items()[0].pixmap().toImage()
            pixels = [(x, y) for y in range(image.height()) for x in range(image.width())
                      if image.pixelColor(x, y).red() > 220 and
                      image.pixelColor(x, y).green() < 30]
            return (min(x for x, _ in pixels), min(y for _, y in pixels),
                    max(x for x, _ in pixels), max(y for _, y in pixels))

        with patch.object(annotation_preview, "sample_image", return_value=sample):
            settings = dict(DEFAULTS, editor_image_round_corners=False,
                            editor_image_border_enabled=False,
                            editor_image_shadow_enabled=False)
            preview = AnnotationPreview(settings, "output")
            plain_bounds = red_bounds(preview.build(QRectF(0, 0, 320, 208)))
            settings["editor_image_shadow_enabled"] = True
            preview.refresh()
            shadow_bounds = red_bounds(preview.build(QRectF(0, 0, 320, 208)))

        self.assertLessEqual(abs((plain_bounds[2] - plain_bounds[0]) -
                                 (shadow_bounds[2] - shadow_bounds[0])), 1)
        self.assertLessEqual(abs((plain_bounds[3] - plain_bounds[1]) -
                                 (shadow_bounds[3] - shadow_bounds[1])), 1)

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

    def test_inline_editor_toolbar_has_secondary_appearance_menu(self):
        from screenshot.mask_window import InlineEditor, MaskWindow

        with tempfile.TemporaryDirectory() as folder:
            bounds = {"left": 0, "top": 0, "width": 120, "height": 80}
            settings = {**ConfigManager(Path(folder) / "settings.json").data,
                        "auto_dir": folder, "filename": "inline-appearance",
                        "inline_edit": True, "crosshair": False, "magnifier": False,
                        "capture_after_selection": "edit",
                        "mask_opacity": 0, "bubble": False}
            image = Image.new("RGB", (120, 80), "#d02020")
            with patch("screenshot.mask_window.visible_windows", return_value=[]):
                mask = MaskWindow(image, bounds, [bounds], settings)
            mask.selection.rects.append(QRect(20, 20, 80, 50))
            mask.show()
            self.app.processEvents()
            mask.complete()
            self.app.processEvents()
            editor = mask.session.inline_editor
            try:
                self.assertIsInstance(editor, InlineEditor)
                self.assertEqual([button.text() for button in editor.toolbar.output_buttons[:3]],
                                 ["贴图", "外观", "保存"])
                self.assertFalse(editor.toolbar.appearance_menu.isVisible())
                self.assertLessEqual(editor.toolbar.output_preview.minimumHeight(), 128)
                editor.toolbar.appearance_toggle.click()
                self.assertTrue(editor.toolbar.appearance_menu.isVisible())
                editor.toolbar.appearance_toggle.click()
                self.assertFalse(editor.toolbar.appearance_menu.isVisible())
            finally:
                mask.close()

    def test_rounded_sticker_mask_keeps_image_interactive(self):
        from PySide6.QtGui import QColor, QImage, QPainter
        from config.config_manager import DEFAULTS

        image = QImage(40, 32, QImage.Format_ARGB32)
        image.fill(Qt.transparent)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#d02020"))
        painter.drawRoundedRect(image.rect(), 10, 10)
        painter.end()
        sticker = StickerItem(image, settings=dict(DEFAULTS))
        try:
            self.assertEqual(sticker.pixmap.toImage().pixelColor(20, 16).alpha(), 255)
            self.assertEqual(sticker.pixmap.toImage().pixelColor(0, 0).alpha(), 0)
            self.assertTrue(sticker.mask().contains(QPoint(20, 16)))
            self.assertFalse(sticker.mask().contains(QPoint(0, 0)))
            self.assertFalse(sticker.click_through)
        finally:
            sticker.close()

    def test_mosaic_preview_modes_are_visually_distinct(self):
        from PySide6.QtCore import QRectF
        from PySide6.QtGui import QImage, QPainter
        from config.config_manager import DEFAULTS
        from ui.widgets.annotation_preview import AnnotationPreview

        settings = dict(DEFAULTS, mosaic_size=18)
        preview = AnnotationPreview(settings, "mosaic")
        rendered = []
        for mode in ("blocks", "fine", "blur"):
            settings["mosaic_mode"] = mode
            scene = preview.build(QRectF(0, 0, 320, 96))
            image = QImage(320, 96, QImage.Format_ARGB32)
            image.fill(Qt.transparent)
            painter = QPainter(image)
            try:
                scene.render(painter, QRectF(0, 0, 320, 96), scene.sceneRect())
            finally:
                painter.end()
            rendered.append(tuple(image.pixelColor(x, y).rgba()
                                  for y in range(0, 96, 2)
                                  for x in range(0, 320, 2)))
        self.assertEqual(len(set(rendered)), 3)
        preview.close()

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

    def test_uia_deepest_at_finds_small_control_beyond_eight_levels(self):
        import logging
        from types import SimpleNamespace
        from core.window_uia import deepest_at

        class Node:
            def __init__(self, name, control_type, rect, children=()):
                self.Name = name
                self.ControlTypeName = control_type
                left, top, right, bottom = rect
                self.BoundingRectangle = SimpleNamespace(
                    left=left, top=top, right=right, bottom=bottom)
                self.children = list(children)

            def GetChildren(self):
                return self.children

        target = Node("小按钮", "ButtonControl", (40, 40, 60, 60))
        root = target
        for depth in range(20):
            root = Node(f"容器{depth}", "PaneControl", (0, 0, 100, 100), [root])
        self.assertIs(deepest_at(root, 50, 50, logging.getLogger("test")), target)

    def test_uia_deepest_at_prefers_smallest_overlapping_child(self):
        import logging
        from types import SimpleNamespace
        from core.window_uia import deepest_at

        class Node:
            def __init__(self, name, control_type, rect, children=()):
                self.Name = name
                self.ControlTypeName = control_type
                left, top, right, bottom = rect
                self.BoundingRectangle = SimpleNamespace(
                    left=left, top=top, right=right, bottom=bottom)
                self.children = list(children)

            def GetChildren(self):
                return self.children

        large = Node("大面板", "PaneControl", (0, 0, 100, 100))
        small = Node("小按钮", "ButtonControl", (40, 40, 60, 60))
        root = Node("根", "PaneControl", (0, 0, 100, 100), [large, small])
        self.assertIs(deepest_at(root, 50, 50, logging.getLogger("test")), small)

        tiny = Node("微型按钮", "ButtonControl", (51, 51, 53, 53))
        tiny_root = Node("根", "PaneControl", (0, 0, 100, 100), [tiny])
        self.assertIs(deepest_at(tiny_root, 52, 52, logging.getLogger("test")), tiny)

    def test_uia_deepest_at_descends_into_composite_controls_only(self):
        import logging
        from types import SimpleNamespace
        from core.window_uia import deepest_at

        class Node:
            def __init__(self, name, control_type, rect, children=()):
                self.Name = name
                self.ControlTypeName = control_type
                left, top, right, bottom = rect
                self.BoundingRectangle = SimpleNamespace(
                    left=left, top=top, right=right, bottom=bottom)
                self.children = list(children)

            def GetChildren(self):
                return self.children

        logger = logging.getLogger("test")
        target = Node("下拉编辑区", "EditControl", (30, 30, 70, 70))
        for control_type in ("ComboBoxControl", "SplitButtonControl",
                             "SpinnerControl", "SliderControl",
                             "DateTimePickerControl", "CalendarControl",
                             "HeaderControl", "HeaderItemControl",
                             "DataGridRowControl"):
            with self.subTest(control_type=control_type):
                root = Node("复合控件", control_type, (20, 20, 80, 80), [target])
                self.assertIs(deepest_at(root, 50, 50, logger), target)

        icon = Node("按钮图标", "ImageControl", (45, 45, 55, 55))
        button = Node("按钮", "ButtonControl", (30, 30, 70, 70), [icon])
        self.assertIs(deepest_at(button, 50, 50, logger), button)

    def test_uia_deepest_at_searches_all_overlapping_container_branches(self):
        import logging
        from types import SimpleNamespace
        from core.window_uia import deepest_at

        class Node:
            def __init__(self, name, control_type, rect, children=()):
                self.Name = name
                self.ControlTypeName = control_type
                left, top, right, bottom = rect
                self.BoundingRectangle = SimpleNamespace(
                    left=left, top=top, right=right, bottom=bottom)
                self.children = list(children)

            def GetChildren(self):
                return self.children

        icon = Node("本地磁盘图标", "ImageControl", (46, 46, 54, 54))
        disk = Node("本地磁盘 (C:)", "DataItemControl", (40, 40, 60, 60), [icon])
        empty_pane = Node("", "PaneControl", (40, 40, 60, 60))
        root = Node("文件区域", "PaneControl", (0, 0, 100, 100),
                    [empty_pane, disk])

        self.assertIs(deepest_at(root, 50, 50, logging.getLogger("test")), icon)

    def test_uia_element_chain_walks_parents(self):
        import sys
        import os
        from types import SimpleNamespace
        from unittest.mock import Mock
        from PySide6.QtWidgets import QWidget
        from core import window_uia

        inner = Mock()
        inner.BoundingRectangle = SimpleNamespace(left=10, top=10, right=100, bottom=80)
        inner.Name = "按钮"
        inner.ControlTypeName = "ButtonControl"
        outer = Mock()
        outer.BoundingRectangle = SimpleNamespace(left=0, top=0, right=200, bottom=150)
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
        # UIA 控件即使只有几个像素，也要能参与命中测试。
        tiny = Mock()
        tiny.BoundingRectangle = SimpleNamespace(left=10, top=10, right=12, bottom=12)
        tiny.GetParentControl.return_value = None
        automation.ControlFromHandle.return_value = tiny
        with patch("core.window_uia.top_window_at", return_value=999), \
                patch.dict(sys.modules, {"uiautomation": automation}):
            self.assertEqual(window_uia.element_chain((11, 11), 3),
                             [(10, 10, 12, 12)])

        zero_area = Mock()
        zero_area.BoundingRectangle = SimpleNamespace(left=10, top=10, right=10, bottom=12)
        automation.ControlFromHandle.return_value = zero_area
        with patch("core.window_uia.top_window_at", return_value=999), \
                patch.dict(sys.modules, {"uiautomation": automation}):
            self.assertEqual(window_uia.element_chain((10, 11), 3), [])

        # 只有遮罩（screensnap_mask）会被忽略；贴图（仅 screensnap_overlay）不再被忽略，
        # 可被 UIA 识别命中。
        mask = QWidget()
        mask.setProperty("screensnap_mask", True)
        mask.setProperty("screensnap_overlay", True)
        mask.show()
        sticker = QWidget()
        sticker.setProperty("screensnap_overlay", True)
        sticker.show()
        self.app.processEvents()
        try:
            mask_control = Mock(NativeWindowHandle=int(mask.winId()))
            sticker_control = Mock(NativeWindowHandle=int(sticker.winId()))
            with patch("core.window_uia.process_of", return_value=os.getpid()):
                self.assertTrue(window_uia.own_control(mask_control))
                self.assertFalse(window_uia.own_control(sticker_control))
        finally:
            mask.close()
            sticker.close()

    def test_uia_structure_diagnostic_is_bounded_and_omits_values(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        from core.window_uia import format_control_tree

        class Node:
            def __init__(self, name, control_type, parent=None):
                self.Name = name
                self.ControlTypeName = control_type
                self.BoundingRectangle = SimpleNamespace(left=0, top=0, right=40, bottom=20)
                self.parent = parent
                self.children = []
                self.Value = "private value"

            def GetParentControl(self):
                return self.parent

            def GetChildren(self):
                return self.children

        window = Node("Editor", "WindowControl")
        group = Node("Tools", "GroupControl", window)
        target = Node("Save", "ButtonControl", group)
        icon = Node("Save icon", "ImageControl", target)
        sibling = Node("Cancel", "ButtonControl", group)
        target.children = [icon]
        group.children = [target, sibling]
        output = format_control_tree(target, Mock())

        self.assertIn("WindowControl name='Editor'", output)
        self.assertIn("GroupControl name='Tools'", output)
        self.assertIn("ButtonControl name='Save'", output)
        self.assertIn("ImageControl name='Save icon'", output)
        self.assertIn("ButtonControl name='Cancel'", output)
        self.assertNotIn("private value", output)
        self.assertLessEqual(len(output.splitlines()), 60)

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
                             ["arrow", "crop", "ellipse", "eraser", "marker", "mosaic", "pen", "rect", "text"])
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
                panel.list_widget.item(0).setSelected(True)
                self.assertEqual(manager.selected_items, {manager.items[0]})
                panel.refresh()
                self.assertEqual(manager.selected_items, {manager.items[0]})
                self.assertTrue(panel.list_widget.item(0).isSelected())
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

    def test_sticker_panel_row_click_supports_ctrl_toggle_and_shift_range(self):
        from PySide6.QtGui import QColor, QImage
        from PySide6.QtWidgets import QLabel
        from config.config_manager import DEFAULTS
        from ui.sticker_panel import StickerPanel

        with tempfile.TemporaryDirectory() as folder, \
                patch("sticker.sticker_manager.data_dir", return_value=Path(folder)):
            manager = StickerManager(dict(DEFAULTS))
            image = QImage(18, 12, QImage.Format_RGB32)
            image.fill(QColor("red"))
            stickers = [manager.add(image) for _ in range(3)]
            panel = StickerPanel(manager)
            try:
                panel.show()
                self.app.processEvents()
                rows = [panel.list_widget.itemWidget(panel.list_widget.item(index))
                        for index in range(3)]
                labels = [row.findChildren(QLabel) for row in rows]
                QTest.mouseClick(labels[0][1], Qt.LeftButton)
                self.assertEqual(manager.selected_items, {stickers[0]})
                QTest.mouseClick(labels[1][1], Qt.LeftButton, Qt.ControlModifier)
                self.assertEqual(manager.selected_items, {stickers[0], stickers[1]})
                QTest.mouseClick(labels[1][1], Qt.LeftButton, Qt.ControlModifier)
                self.assertEqual(manager.selected_items, {stickers[0]})
                QTest.mouseClick(labels[2][1], Qt.LeftButton, Qt.ShiftModifier)
                self.assertEqual(manager.selected_items, {stickers[0], stickers[1], stickers[2]})
            finally:
                panel.close()
                manager.close_all()
                self.app.processEvents()

    def test_sticker_ctrl_multi_select_batch_move_and_group_management(self):
        from PySide6.QtCore import QPoint
        from PySide6.QtGui import QImage
        from config.config_manager import DEFAULTS
        from ui.sticker_panel import StickerPanel

        with tempfile.TemporaryDirectory() as folder, \
                patch("sticker.sticker_manager.data_dir", return_value=Path(folder)), \
                patch("ui.sticker_panel.QInputDialog.getText", side_effect=[("refs", True),
                                                                            ("archive", True)]):
            manager = StickerManager(dict(DEFAULTS))
            image = QImage(24, 18, QImage.Format_RGB32)
            first, second, third, fourth = (manager.add(image) for _ in range(4))
            first.move(20, 30)
            second.move(80, 30)
            third.move(140, 30)
            fourth.move(200, 30)
            panel = StickerPanel(manager)
            try:
                QTest.mouseClick(first, Qt.LeftButton)
                QTest.mouseClick(second, Qt.LeftButton, Qt.ControlModifier)
                QTest.mouseClick(third, Qt.LeftButton, Qt.ControlModifier)
                QTest.mouseClick(fourth, Qt.LeftButton, Qt.ControlModifier)
                self.assertEqual(manager.selected_items, {first, second, third, fourth})
                self.assertTrue(first.selected_for_batch)
                self.assertTrue(second.selected_for_batch)
                self.assertTrue(third.selected_for_batch)
                self.assertTrue(fourth.selected_for_batch)
                QTest.mouseClick(first, Qt.LeftButton, Qt.ControlModifier)
                self.assertEqual(manager.selected_items, {second, third, fourth})
                QTest.mouseClick(first, Qt.LeftButton, Qt.ControlModifier)
                self.assertEqual(manager.selected_items, {first, second, third, fourth})
                QTest.mouseClick(first, Qt.LeftButton)
                self.assertEqual(manager.selected_items, {first, second, third, fourth})
                second_origin = second.pos()
                third_origin = third.pos()
                fourth_origin = fourth.pos()
                first.move(first.pos() + QPoint(7, -4))
                manager.move_selected(first, QPoint(7, -4))
                self.assertEqual(second.pos(), second_origin + QPoint(7, -4))
                self.assertEqual(third.pos(), third_origin + QPoint(7, -4))
                self.assertEqual(fourth.pos(), fourth_origin + QPoint(7, -4))

                panel.refresh()
                panel.list_widget.clearSelection()
                panel.list_widget.item(0).setSelected(True)
                panel.list_widget.item(1).setSelected(True)
                panel.sync_manager_selection()
                panel.create_group()
                self.assertEqual({first.group_name, second.group_name}, {"refs"})
                panel.group_combo.setCurrentIndex(panel.group_combo.findData("refs"))
                panel.rename_group()
                self.assertEqual({first.group_name, second.group_name}, {"archive"})
                panel.group_combo.setCurrentIndex(panel.group_combo.findData("archive"))
                first_before_group_move = first.pos()
                second_before_group_move = second.pos()
                third_before_group_move = third.pos()
                with patch("ui.sticker_panel.QInputDialog.getInt",
                           side_effect=[(12, True), (-5, True)]):
                    self.assertEqual(panel.move_group(), 2)
                self.assertEqual(first.pos(), first_before_group_move + QPoint(12, -5))
                self.assertEqual(second.pos(), second_before_group_move + QPoint(12, -5))
                self.assertEqual(third.pos(), third_before_group_move)
                panel.toggle_group()
                self.assertFalse(first.isVisible())
                self.assertFalse(second.isVisible())
                panel.toggle_group()
                self.assertTrue(first.isVisible())
                self.assertTrue(second.isVisible())
                manager.set_selected_items([first, second])
                self.assertFalse(manager.toggle_selected_visibility())
                self.assertFalse(first.isVisible())
                self.assertFalse(second.isVisible())
                self.assertTrue(manager.toggle_selected_visibility())
                self.assertTrue(first.isVisible())
                self.assertTrue(second.isVisible())
                panel.delete_group()
                self.assertEqual(manager.group_names(), [])
                self.assertTrue(first.isVisible())
                self.assertTrue(second.isVisible())

                manager.set_selected_items([first, second])
                self.assertEqual(manager.close_selected(), 2)
                self.app.processEvents()
                self.assertEqual(manager.items, [third, fourth])
            finally:
                panel.close()
                manager.close_all()
                self.app.processEvents()

    def test_sticker_ctrl_selection_effect_clears_when_item_is_deselected(self):
        from PySide6.QtCore import QEvent
        from PySide6.QtGui import QFocusEvent, QImage
        from config.config_manager import DEFAULTS

        with tempfile.TemporaryDirectory() as folder, \
                patch("sticker.sticker_manager.data_dir", return_value=Path(folder)):
            manager = StickerManager(dict(DEFAULTS))
            image = QImage(24, 18, QImage.Format_ARGB32)
            image.fill(Qt.transparent)
            first, second = manager.add(image), manager.add(image)
            try:
                manager.set_selected_items({first})
                self.app.sendEvent(first, QFocusEvent(QEvent.FocusIn, Qt.OtherFocusReason))
                self.app.processEvents()
                manager.select_item(second, additive=True)
                self.assertEqual(manager.selected_items, {first, second})
                self.assertTrue(first.selection_effect_active)
                self.assertTrue(second.selection_effect_active)

                manager.select_item(second, additive=True)
                self.assertEqual(manager.selected_items, {first})
                self.assertTrue(first.selection_effect_active)
                self.assertFalse(second.selection_effect_active)
            finally:
                manager.close_all()
                self.app.processEvents()

    def test_sticker_selection_effect_tracks_focus_for_single_selection(self):
        from PySide6.QtCore import QEvent
        from PySide6.QtGui import QFocusEvent, QImage
        from config.config_manager import DEFAULTS

        with tempfile.TemporaryDirectory() as folder, \
                patch("sticker.sticker_manager.data_dir", return_value=Path(folder)):
            # 关掉描边，蓝色像素只可能来自选中光晕，避免默认描边色影响统计。
            manager = StickerManager(dict(DEFAULTS, sticker_border_enabled=False))
            image = QImage(24, 18, QImage.Format_ARGB32)
            image.fill(Qt.transparent)
            first, sibling, other = (manager.add(image) for _ in range(3))
            try:
                manager.assign_group([first, sibling], "work")
                manager.assign_group([other], "personal")
                manager.set_selected_items({first})
                self.assertFalse(first.selection_effect_active)
                self.assertFalse(sibling.selection_effect_active)
                self.assertFalse(other.selection_effect_active)

                self.app.sendEvent(first, QFocusEvent(QEvent.FocusIn, Qt.OtherFocusReason))
                self.app.processEvents()
                self.assertTrue(first.selection_effect_active)
                self.assertFalse(sibling.selection_effect_active)
                self.assertFalse(other.selection_effect_active)
                rendered = first.grab().toImage()
                blue_pixels = sum(
                    1 for y in range(rendered.height())
                    for x in range(rendered.width())
                    if rendered.pixelColor(x, y).blue() >
                    rendered.pixelColor(x, y).red() + 30)
                self.assertGreater(blue_pixels, 0)

                manager.settings["sticker_selection_effect_enabled"] = False
                manager.refresh_selection_visuals()
                self.app.processEvents()
                rendered_without_effect = first.grab().toImage()
                blue_pixels_without_effect = sum(
                    1 for y in range(rendered_without_effect.height())
                    for x in range(rendered_without_effect.width())
                    if rendered_without_effect.pixelColor(x, y).blue() >
                    rendered_without_effect.pixelColor(x, y).red() + 30)
                self.assertEqual(blue_pixels_without_effect, 0)

                manager.settings["sticker_selection_effect_enabled"] = True
                manager.refresh_selection_visuals()
                self.app.processEvents()
                self.assertTrue(any(
                    first.grab().toImage().pixelColor(x, y).blue() >
                    first.grab().toImage().pixelColor(x, y).red() + 30
                    for y in range(first.height()) for x in range(first.width())))

                self.app.sendEvent(first, QFocusEvent(QEvent.FocusOut, Qt.OtherFocusReason))
                self.app.sendEvent(other, QFocusEvent(QEvent.FocusIn, Qt.OtherFocusReason))
                self.app.processEvents()
                self.assertFalse(first.selection_effect_active)
                self.assertFalse(sibling.selection_effect_active)
                self.assertFalse(other.selection_effect_active)

                self.app.sendEvent(other, QFocusEvent(QEvent.FocusOut, Qt.OtherFocusReason))
                self.app.processEvents()
                self.assertFalse(first.selection_effect_active)
                self.assertFalse(sibling.selection_effect_active)
                self.assertFalse(other.selection_effect_active)
            finally:
                manager.close_all()
                self.app.processEvents()

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

    def test_sticker_appearance_preview_tracks_border_and_shadow(self):
        from PySide6.QtGui import QColor
        from config.config_manager import ConfigManager
        from ui.settings_sticker import StickerPage

        with tempfile.TemporaryDirectory() as folder:
            config = ConfigManager(Path(folder) / "settings.json")
            page = StickerPage(config, config.save)
            preview = page.selection_effect_preview
            page.resize(480, 760)
            page.show()
            self.app.processEvents()

            def pixel_count(predicate):
                image = preview.grab().toImage()
                return sum(1 for y in range(image.height()) for x in range(image.width())
                           if predicate(image.pixelColor(x, y)))

            # 描边颜色由配置决定，按当前配置色判定，避免默认值调整后用例失效。
            border_color = QColor(config.data["sticker_border_color"])
            is_border = lambda color: (abs(color.red() - border_color.red()) < 40 and
                                       abs(color.green() - border_color.green()) < 40 and
                                       abs(color.blue() - border_color.blue()) < 40)
            border_pixels = pixel_count(is_border)
            self.assertGreater(border_pixels, 0)
            page.controls["sticker_border_enabled"].setChecked(False)
            self.app.processEvents()
            self.assertLess(pixel_count(is_border), border_pixels)

            with_shadow = preview.grab().toImage()
            page.controls["sticker_shadow_enabled"].setChecked(False)
            self.app.processEvents()
            without_shadow = preview.grab().toImage()
            changed_pixels = sum(
                1 for y in range(with_shadow.height()) for x in range(with_shadow.width())
                if with_shadow.pixelColor(x, y) != without_shadow.pixelColor(x, y))
            self.assertGreater(changed_pixels, 0)
            page.close()

    def test_sticker_selection_effect_strength_changes_rendered_glow(self):
        from PySide6.QtGui import QImage
        from config.config_manager import DEFAULTS
        from sticker.sticker_item import StickerItem

        image = QImage(40, 30, QImage.Format_ARGB32)
        image.fill(Qt.white)
        settings = dict(DEFAULTS, sticker_border_enabled=False,
                        sticker_shadow_enabled=False,
                        sticker_selection_effect_enabled=True)
        sticker = StickerItem(image, settings=settings)
        sticker.selection_effect_active = True

        def glow_pixels(strength):
            settings["sticker_selection_effect_strength"] = strength
            rendered = sticker.grab().toImage()
            return sum(1 for y in range(rendered.height())
                       for x in range(rendered.width())
                       if (color := rendered.pixelColor(x, y)).blue() > color.red() + 25)

        full_strength = glow_pixels(100)
        reduced_strength = glow_pixels(20)
        no_strength = glow_pixels(0)
        self.assertGreater(full_strength, reduced_strength)
        self.assertGreater(reduced_strength, no_strength)

    def test_sticker_panel_adds_files_to_group_and_closes_group(self):
        from PySide6.QtGui import QImage
        from config.config_manager import DEFAULTS
        from ui.sticker_panel import StickerPanel

        with tempfile.TemporaryDirectory() as folder, \
                patch("sticker.sticker_manager.data_dir", return_value=Path(folder)):
            path = Path(folder) / "group.png"
            image = QImage(24, 18, QImage.Format_ARGB32)
            image.fill(Qt.red)
            self.assertTrue(image.save(str(path)))
            manager = StickerManager(dict(DEFAULTS))
            panel = StickerPanel(manager)
            panel.group_combo.addItem("refs", "refs")
            panel.group_combo.setCurrentIndex(panel.group_combo.findData("refs"))
            try:
                with patch("ui.sticker_panel.QFileDialog.getOpenFileNames",
                           return_value=([str(path)], "")):
                    self.assertEqual(panel.add_files_to_group(), 1)
                self.assertEqual(len(manager.items), 1)
                self.assertEqual(manager.items[0].group_name, "refs")
                self.assertEqual(panel.close_group(), 1)
                self.app.processEvents()
                self.assertEqual(manager.items, [])
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
        self.assertTrue(all(action.toolTip().strip() for action in menu.actions()
                    if not action.isSeparator()))

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

    def test_sticker_snap_targets_other_visible_stickers_not_batch_members(self):
        from types import SimpleNamespace
        from PySide6.QtCore import QPoint
        from PySide6.QtGui import QImage
        from config.config_manager import DEFAULTS

        settings = dict(DEFAULTS, sticker_snap_targets="window", sticker_snap_threshold=8,
                        sticker_border_enabled=False, sticker_shadow_enabled=False)
        source = StickerItem(QImage(40, 30, QImage.Format_RGB32), settings=settings)
        target = StickerItem(QImage(40, 30, QImage.Format_RGB32), settings=settings)
        selected = StickerItem(QImage(40, 30, QImage.Format_RGB32), settings=settings)
        target.move(100, 90)
        selected.move(200, 200)
        manager = SimpleNamespace(items=[source, target, selected],
                                  selected_items={source, selected})
        source.manager = manager
        for item in manager.items:
            item.show()
        try:
            with patch("sticker.sticker_item.visible_targets", return_value=[]), \
                    patch("sticker.sticker_item.window_under_point", return_value=None):
                targets = source.snap_candidates(QPoint(60, 60))
                sticker_targets = [item for item in targets if item["kind"] == "sticker"]
                position = source.apply_snap(QPoint(52, 52), QPoint(60, 60))
            self.assertEqual([item["key"] for item in sticker_targets],
                             [f"sticker{target.session_id}"])
            self.assertEqual(position, QPoint(60, 60))
            self.assertEqual(source.snap_target["target"], "sticker")
            self.assertEqual(source.state()["snap"]["sticker_id"], target.session_id)
        finally:
            for item in manager.items:
                item.close()

    def test_sticker_follow_tracks_other_sticker_and_restores_relation(self):
        from PySide6.QtCore import QPoint
        from PySide6.QtGui import QGuiApplication, QImage
        from config.config_manager import DEFAULTS

        board = QGuiApplication.clipboard()
        with tempfile.TemporaryDirectory() as folder, \
                patch("sticker.sticker_manager.data_dir", return_value=Path(folder)):
            board.clear()
            manager = StickerManager(dict(DEFAULTS, sticker_follow_sticker=True), board)
            image = QImage(24, 18, QImage.Format_RGB32)
            image.fill(Qt.red)
            target = manager.add(image)
            follower = manager.add(image)
            target.move(100, 120)
            follower.move(130, 150)
            follower.snap_target = {"target": "sticker", "key": f"sticker{target.session_id}",
                                    "sticker_id": target.session_id, "edge": "left",
                                    "title": "贴图", "rel_x": 30, "rel_y": 30, "follow": True}
            follower.snap_hint = "left"
            follower.start_follow()
            target.move(140, 190)
            follower.poll_follow()
            self.assertEqual(follower.pos(), QPoint(170, 220))
            self.assertTrue(follower.following())
            manager.persist()
            manager.close_all()

            restored = StickerManager(dict(DEFAULTS, sticker_follow_sticker=True), board)
            restored.restore()
            restored_target = next(item for item in restored.items
                                   if item.session_id == target.session_id)
            restored_follower = next(item for item in restored.items
                                     if item.session_id == follower.session_id)
            self.assertTrue(restored_follower.following())
            self.assertFalse(restored_target.can_follow_sticker(restored_follower.session_id))
            restored_target.move(200, 240)
            restored_follower.poll_follow()
            self.assertEqual(restored_follower.pos(), QPoint(230, 270))
            restored_target.close()
            self.app.processEvents()
            self.assertIsNone(restored_follower.snap_target)
            self.assertFalse(restored_follower.following())
            restored.close_all()
            board.clear()
            self.app.processEvents()

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
        from PySide6.QtWidgets import QMessageBox, QPushButton
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
            clear_history = Mock(return_value=2)
            settings = SettingsWindow(manager, clear_history)
            try:
                with patch.object(QMessageBox, "question", return_value=QMessageBox.Yes), \
                        patch.object(QMessageBox, "information"):
                    settings.reset_defaults()
                    settings.clear_sticker_session()
                    history_button = next(
                        button for button in settings.findChildren(QPushButton)
                        if button.text() == "清空剪贴板历史")
                    history_button.click()
                clear_history.assert_called_once_with()
                self.assertEqual((manager.data["sticker_snap_threshold"], manager.data["log_level"]),
                                 (DEFAULTS["sticker_snap_threshold"], DEFAULTS["log_level"]))
                backup = Path(folder) / "settings.bak"
                self.assertTrue(backup.is_file())
                self.assertEqual(json.loads(backup.read_text(encoding="utf-8"))["log_level"], "TRACE")
                # 界面控件也必须回到默认值，而不是停留在重置前的显示。
                page = settings.page("贴图")
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

    def test_uia_excludes_mask_but_not_stickers_or_menus(self):
        from PySide6.QtWidgets import QWidget
        from core.window_snap import ignored_mask_window, ignored_app_window

        # 遮罩带 screensnap_mask + screensnap_overlay；贴图只带 screensnap_overlay。
        # UIA 识别只忽略遮罩，贴图、菜单等本程序窗口不再被忽略，可以被截图命中。
        mask = QWidget()
        mask.setProperty("screensnap_mask", True)
        mask.setProperty("screensnap_overlay", True)
        mask.show()
        sticker = QWidget()
        sticker.setProperty("screensnap_overlay", True)
        sticker.show()
        try:
            mask_handle = int(mask.winId())
            sticker_handle = int(sticker.winId())
            self.assertTrue(ignored_mask_window(mask_handle))
            self.assertFalse(ignored_mask_window(sticker_handle))
            # 贴图吸附逻辑仍忽略贴图，避免与“贴图间吸附”重复计数。
            self.assertTrue(ignored_app_window(sticker_handle))
            self.assertTrue(ignored_app_window(mask_handle))
        finally:
            mask.close()
            sticker.close()

    def test_uia_own_control_excludes_mask_only(self):
        from PySide6.QtWidgets import QWidget
        from core import window_uia

        # own_control 只穿透遮罩；贴图控件不再被忽略，可以进入 UIA 识别链。
        mask = QWidget()
        mask.setProperty("screensnap_mask", True)
        mask.show()
        sticker = QWidget()
        sticker.setProperty("screensnap_overlay", True)
        sticker.show()
        try:
            mask_handle = int(mask.winId())
            sticker_handle = int(sticker.winId())

            class FakeControl:
                def __init__(self, handle, parent=None):
                    self._handle = handle
                    self._parent = parent

                @property
                def NativeWindowHandle(self):
                    return self._handle

                def GetParentControl(self):
                    return self._parent

            self.assertFalse(window_uia.own_control(FakeControl(sticker_handle)))
            self.assertTrue(window_uia.own_control(FakeControl(mask_handle)))
        finally:
            mask.close()
            sticker.close()

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

    def test_open_sticker_file_shortcut_has_settings_entry_and_dispatch(self):
        from core.constants import HOTKEY_LABELS
        from config.config_manager import DEFAULTS
        from main import Application
        from ui.settings_hotkey import HotkeyPage

        self.assertEqual(DEFAULTS["hotkeys"]["open_sticker_file"], "ctrl+alt+n")
        self.assertEqual(HOTKEY_LABELS["open_sticker_file"], "从文件打开新贴图")
        with tempfile.TemporaryDirectory() as folder:
            manager = ConfigManager(Path(folder) / "settings.json")
            page = HotkeyPage(manager, Mock())
            self.assertEqual(page.edit_open_sticker_file.keySequence().toString(),
                             "Ctrl+Alt+N")
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

    def test_theme_setting_and_native_unchecked_checkbox(self):
        from PySide6.QtGui import QImage, QPainter, QPalette
        from PySide6.QtWidgets import QCheckBox, QStyle, QStyleOptionButton
        from config.config_manager import DEFAULTS
        from ui.theme import apply_theme

        self.assertEqual(DEFAULTS["theme"], "system")
        system_window_color = self.app.palette().color(QPalette.Window).name()
        apply_theme(self.app, "dark")
        self.assertEqual(self.app.palette().color(QPalette.Window).name(), "#202124")
        apply_theme(self.app, "light")
        self.assertEqual(self.app.palette().color(QPalette.Window).name(), "#f0f0f0")
        self.assertEqual(self.app.palette().color(QPalette.ToolTipBase).name(),
                 "#f3f3f3")
        apply_theme(self.app, "system")
        self.assertEqual(self.app.palette().color(QPalette.Window).name(),
                 system_window_color)
        with tempfile.TemporaryDirectory() as folder:
            manager = ConfigManager(Path(folder) / "settings.json")
            settings = SettingsWindow(manager)
            editor = EditorWindow(Image.new("RGB", (20, 20), "white"), manager.data)
            try:
                self.assertEqual(settings.palette().color(QPalette.Window).name(),
                                 system_window_color)
                self.assertEqual(editor.palette().color(QPalette.Window).name(),
                                 system_window_color)
                theme_control = settings.page("主题与外观").controls["theme"]
                self.assertEqual(theme_control.currentData(), "system")
                self.assertEqual({theme_control.itemData(index)
                                  for index in range(theme_control.count())},
                                 {"system", "dark", "light"})
                manager.data["theme"] = "sepia"
                with self.assertRaises(ValueError):
                    manager.save()
            finally:
                settings.close()
                editor.close()
                manager.data["theme"] = "system"

        checkbox = QCheckBox()
        checkbox.resize(24, 24)
        checkbox.ensurePolished()
        option = QStyleOptionButton()
        option.initFrom(checkbox)
        option.rect = checkbox.rect()
        option.state |= QStyle.State_Off
        image = QImage(24, 24, QImage.Format_ARGB32)
        image.fill(Qt.transparent)
        painter = QPainter(image)
        checkbox.style().drawPrimitive(QStyle.PE_IndicatorCheckBox, option,
                                       painter, checkbox)
        painter.end()
        colors = {image.pixelColor(x, y).rgba()
                  for x in range(24) for y in range(24)
                  if image.pixelColor(x, y).alpha()}
        self.assertGreater(len(colors), 1)

    def test_capture_mask_requests_activation_after_show(self):
        from main import Application

        class FakeMask:
            def __init__(self):
                self.calls = []
                self.window = FakeWindow(self.calls)

            def isVisible(self):
                return True

            def raise_(self):
                self.calls.append("raise")

            def windowHandle(self):
                return self.window

            def activateWindow(self):
                self.calls.append("activate-widget")

            def setFocus(self, reason):
                self.calls.append(("focus", reason))

        class FakeWindow:
            def __init__(self, calls):
                self.calls = calls

            def requestActivate(self):
                self.calls.append("activate-window")

        fake_mask = FakeMask()
        app = Application.__new__(Application)
        app.mask = fake_mask
        with patch("main.MaskWindow", FakeMask):
            app._activate_capture_mask()
        self.assertEqual([call[0] if isinstance(call, tuple) else call
                          for call in fake_mask.calls],
                         ["raise", "activate-window", "activate-widget", "focus"])


if __name__ == "__main__":
    unittest.main()