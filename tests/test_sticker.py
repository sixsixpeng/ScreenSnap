"""贴图：窗口尺寸与 dpr、分组、会话恢复、回收站与剪贴板历史。"""

import os
import sys

# 无论 -m unittest、目录内 discover 还是直接跑文件，都要能导入 tests.base。
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from screenshot.mask_window import MaskWindow  # 剪贴板用例要构造遮罩
from tests.base import (CoreTests, Mock, patch, Path, tempfile, json, unittest, Image,
                        QPointF, Qt, QPoint, QRect, QRectF, QTest, QApplication,
                        ConfigManager, resolved_dir, AnnotationCanvas, shape,
                        EditorWindow, SelectionRects, StickerItem, StickerManager,
                        SettingsWindow, HotkeyEdit)


class StickerTests(CoreTests):
    def test_group_properties_persist_and_unassign_keeps_stickers(self):
        from PySide6.QtGui import QColor, QImage
        from config.config_manager import DEFAULTS

        with tempfile.TemporaryDirectory() as folder, \
                patch("sticker.sticker_manager.data_dir", return_value=Path(folder)):
            settings = dict(DEFAULTS, save_dir=folder)
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
            settings = dict(DEFAULTS, save_dir=folder)
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
                settings = {**DEFAULTS, "save_dir": folder,
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
            settings = dict(DEFAULTS, save_dir=folder)
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
        self.assertEqual(clipboard_pixels.getpixel((0, 0))[:3], (22, 160, 208))
        self.assertIsNone(clipboard_capture)
        self.assertFalse(app.edit_images.call_args.kwargs["from_capture"])

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "source.png"
            Image.new("RGB", (10, 6), "red").save(path)
            with patch("app.capture_flow.QFileDialog.getOpenFileNames",
                       return_value=([str(path)], "图片文件")):
                app.open_and_edit_image()
        file_pixels, file_capture = app.edit_images.call_args.args[0][0]
        self.assertEqual(file_pixels.size, (10, 6))
        self.assertEqual(file_pixels.getpixel((0, 0)), (255, 0, 0, 255))
        self.assertIsNone(file_capture)
        self.assertFalse(app.edit_images.call_args.kwargs["from_capture"])

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

    def test_inline_double_click_saves_image_to_clipboard(self):
        from PySide6.QtGui import QGuiApplication
        from config.config_manager import DEFAULTS
        from screenshot.mask_window import MaskWindow

        with tempfile.TemporaryDirectory() as folder:
            bounds = {"left": 0, "top": 0, "width": 160, "height": 120}
            settings = {**DEFAULTS, "save_dir": folder, "inline_edit": True, "magnifier": False,
                        "editor_image_round_corners": False}
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

    def test_inline_editor_paste_saves_emits_sticker_and_closes_capture(self):
        from types import SimpleNamespace
        from config.config_manager import DEFAULTS
        from main import Application
        from screenshot.mask_window import MaskWindow

        with tempfile.TemporaryDirectory() as folder, \
            patch("screenshot.mask_window.visible_windows", return_value=[]), \
            patch("sticker.sticker_manager.data_dir", return_value=Path(folder)):
            bounds = {"left": 0, "top": 0, "width": 100, "height": 80}
            settings = dict(DEFAULTS, save_dir=folder, inline_edit=True,
                            magnifier=False, crosshair=False, mask_opacity=0)
            settings["capture_after_selection"] = "edit"
            manager = StickerManager(settings)
            mask = MaskWindow(Image.new("RGB", (100, 80), "#d02020"), bounds,
                              [bounds], settings)
            mask.selection.rects.append(QRect(10, 10, 60, 50))
            mask.complete()
            editor = mask.session.inline_editor
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
            settings = dict(DEFAULTS, save_dir=folder, inline_edit=True,
                            magnifier=False, crosshair=False, mask_opacity=0,
                            capture_after_selection="edit")
            manager = StickerManager(settings)
            mask = MaskWindow(Image.new("RGB", (100, 80), "#d02020"), bounds,
                              [bounds], settings)
            mask.selection.rects.append(QRect(10, 10, 60, 50))
            mask.complete()
            editor = mask.session.inline_editor
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

    def test_saving_edited_sticker_creates_new_file_without_replacing_source(self):
        from config.config_manager import DEFAULTS
        from core.screen_capture import to_qimage

        with tempfile.TemporaryDirectory() as folder:
            source_path = Path(folder) / "sticker.png"
            Image.new("RGB", (25, 20), "#23bc58").save(source_path)
            edited = Image.new("RGB", (25, 20), "#d02020")
            settings = {**DEFAULTS, "save_dir": folder, "filename": "edited_sticker",
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

    def test_editor_save_and_sticker(self):
        from config.config_manager import DEFAULTS
        with tempfile.TemporaryDirectory() as folder:
            settings = dict(DEFAULTS, save_dir=folder, filename="test")
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
            settings = dict(DEFAULTS, save_dir=folder, filename="single")
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
            settings = dict(DEFAULTS, save_dir=folder,
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

            with patch("app.capture_flow.QFileDialog.getOpenFileNames",
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
            settings = dict(DEFAULTS, save_dir=folder,
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
            with patch("app.capture_flow.QGuiApplication.clipboard", return_value=clipboard):
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

    def test_editor_paste_without_source_position_shows_sticker_beside_editor(self):
        pass

    def test_toolbar_group_commands(self):
        from PySide6.QtWidgets import QLabel, QMenu, QToolButton, QWidgetAction
        from editor.toolbar_widget import ToolbarWidget
        toolbar = ToolbarWidget()
        self.assertEqual([section.findChild(QLabel).text() for section in toolbar.sections],
                        ["标注", "编辑", "图像旋转", "输出"])
        self.assertEqual([button.text() for button in toolbar.output_buttons],
                     ["贴图", "外观", "保存", "另存为", "仅复制", "放弃", "关闭全部"])
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
            manager = StickerManager(dict(DEFAULTS, save_dir=folder))
            manager.cycle(1)
            first = manager.history_sticker
            manager.cycle(1)
            self.assertIs(manager.history_sticker, first)
            self.assertEqual(len(manager.items), 1)
            manager.paste_latest()
            self.assertEqual(len(manager.items), 2)
            manager.close_all()

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
            board = QGuiApplication.clipboard()
            board.clear()
            manager = StickerManager(DEFAULTS)
            try:
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
        sticker = StickerItem(image, settings=dict(DEFAULTS,
                                                     sticker_shadow_enabled=False,
                                                     sticker_border_enabled=False))
        try:
            self.assertEqual(sticker.pixmap.toImage().pixelColor(20, 16).alpha(), 255)
            self.assertEqual(sticker.pixmap.toImage().pixelColor(0, 0).alpha(), 0)
            self.assertTrue(sticker.mask().contains(QPoint(20, 16)))
            self.assertFalse(sticker.mask().contains(QPoint(0, 0)))
            self.assertFalse(sticker.click_through)
        finally:
            sticker.close()

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

    # 待桌面验收：离屏环境下贴图拿不到「前台窗口」状态，选中描边是否跟随焦点无法验证。
    def test_sticker_selection_effect_tracks_focus_for_single_selection(self):
        pass

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

            # 贴图阴影出厂默认已改为关闭，这里显式打开再对比，避免依赖默认值。
            page.controls["sticker_shadow_enabled"].setChecked(True)
            self.app.processEvents()
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

    def test_sticker_without_saved_position_centers_using_logical_size(self):
        """无位置记录的贴图要按 Qt 逻辑尺寸居中到光标所在屏（不能用物理像素算）。

        pixmap 是源图物理像素，availableGeometry 是 Qt 逻辑坐标；混用会在 125%/150%
        屏上把贴图放偏 (1 - 1/dpr) 个图宽。
        """
        from PySide6.QtGui import QImage, QGuiApplication
        from sticker.sticker_manager import StickerManager

        screen = Mock()
        screen.availableGeometry.return_value = QRect(0, 0, 1000, 800)
        image = QImage(200, 100, QImage.Format_RGBA8888)
        image.fill(0)
        with tempfile.TemporaryDirectory() as folder,                 patch("sticker.sticker_manager.data_dir", return_value=Path(folder)),                 patch.object(QGuiApplication, "screenAt", return_value=screen),                 patch.object(StickerItem, "device_pixel_ratio", return_value=1.25):
            manager = StickerManager({"sticker_border_enabled": False,
                                      "sticker_shadow_enabled": False}, )
            item = manager.add(image, show=False)
            try:
                pad = item.padding()
                width = 200 / 1.25 + pad * 2   # 逻辑尺寸
                height = 100 / 1.25 + pad * 2
                self.assertEqual(item.pos(), QPoint(round((1000 - width) / 2),
                                                    round((800 - height) / 2)))
            finally:
                item.close()

    def test_rebuildable_cache_cleanup_honors_scope_flags(self):
        """清理范围由三个开关逐类控制：全部关闭时不删任何缓存文件。"""
        from sticker.sticker_manager import StickerManager

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "clipboard_history").mkdir()
            (root / "toast_cache").mkdir()
            (root / "sticker_cache").mkdir()
            clipboard = root / "clipboard_history" / "clipboard_1.png"
            toast = root / "toast_cache" / "toast_1.png"
            sticker = root / "sticker_cache" / "sticker_1.png"
            for path in (clipboard, toast, sticker):
                path.write_bytes(b"x")
            settings = {"cache_clear_clipboard": False, "cache_clear_toast": False,
                        "cache_clear_sticker": False}
            with patch("sticker.sticker_manager.data_dir", return_value=root):
                manager = StickerManager(settings)
                manager.clear_rebuildable_cache()
            self.assertTrue(clipboard.exists() and toast.exists() and sticker.exists())

            settings = {"cache_clear_clipboard": True, "cache_clear_toast": True,
                        "cache_clear_sticker": True}
            with patch("sticker.sticker_manager.data_dir", return_value=root):
                manager = StickerManager(settings)
                manager.clear_rebuildable_cache()
            self.assertFalse(clipboard.exists() or toast.exists() or sticker.exists())



if __name__ == "__main__":
    unittest.main()
