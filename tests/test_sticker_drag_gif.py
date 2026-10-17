# -*- coding: utf-8 -*-
"""贴图文件拖放与 GIF 动图：路径加载、拖入替换/新建、拖出 MIME、动画状态恢复。"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from PySide6.QtCore import QMimeData, QPoint, QPointF, Qt, QUrl
from PySide6.QtGui import QDragEnterEvent, QDropEvent, QImage
from PIL import Image

from tests.base import CoreTests, QApplication
from config.config_manager import DEFAULTS, PRESERVED_ON_VERSION_RESET, validate


def make_png(folder, name, color=(255, 0, 0)):
    path = Path(folder) / name
    Image.new("RGB", (32, 24), color).save(path)
    return path


def make_gif(folder, name="anim.gif"):
    path = Path(folder) / name
    frames = [Image.new("RGB", (32, 24), (255, 0, 0)),
              Image.new("RGB", (32, 24), (0, 0, 255))]
    frames[0].save(path, save_all=True, append_images=frames[1:], duration=120, loop=0)
    return path


class StickerDragGifTests(CoreTests):
    def _manager(self, folder):
        from sticker.sticker_manager import StickerManager

        return StickerManager(dict(DEFAULTS))

    def test_config_keys_validate_and_are_preserved(self):
        self.assertIs(DEFAULTS["sticker_drag_enabled"], True)
        self.assertIs(DEFAULTS["sticker_drag_replace"], True)
        self.assertIs(DEFAULTS["sticker_gif_enabled"], True)
        self.assertIs(DEFAULTS["sticker_gif_autoplay"], True)
        self.assertEqual(DEFAULTS["sticker_gif_speed"], 100)
        for key in ("sticker_drag_enabled", "sticker_drag_replace", "sticker_gif_enabled",
                    "sticker_gif_autoplay", "sticker_gif_speed"):
            self.assertIn(key, PRESERVED_ON_VERSION_RESET)
        self.assertEqual(validate({"sticker_gif_speed": 25})["sticker_gif_speed"], 25)
        self.assertEqual(validate({"sticker_gif_speed": 400})["sticker_gif_speed"], 400)
        for bad in (24, 401):
            with self.subTest(speed=bad), self.assertRaises(ValueError):
                validate({"sticker_gif_speed": bad})

    def test_add_paths_creates_cascade_and_replaces_first(self):
        with tempfile.TemporaryDirectory() as folder, \
                patch("sticker.sticker_manager.data_dir", return_value=Path(folder)):
            manager = self._manager(folder)
            manager.publish_output = Mock()
            first = make_png(folder, "a.png", (255, 0, 0))
            second = make_png(folder, "b.png", (0, 255, 0))
            added, failed = manager.add_paths([str(first), str(second)],
                                              position=QPoint(100, 120))
            self.assertEqual(len(added), 2)
            self.assertEqual(failed, [])
            self.assertNotEqual(added[0].pos(), added[1].pos(),
                                "多文件拖入应按 24px 级联排布")
            original_pos = added[0].pos()
            replacement = make_png(folder, "c.png", (0, 0, 255))
            replaced, _ = manager.add_paths([str(replacement)], replace=added[0])
            self.assertEqual(replaced, [added[0]])
            self.assertEqual(added[0].source, str(replacement))
            self.assertEqual(added[0].pos(), original_pos, "替换必须保留原位置")
            for item in manager.items:
                item.close()
            self.app.processEvents()

    def test_sticker_drop_replaces_or_creates_with_shift(self):
        with tempfile.TemporaryDirectory() as folder, \
                patch("sticker.sticker_manager.data_dir", return_value=Path(folder)), \
                patch("sticker.sticker_item.QDrag"):
            manager = self._manager(folder)
            manager.publish_output = Mock()
            first = make_png(folder, "a.png")
            target = manager.add(QImage(str(first)), str(first))
            replacement = make_png(folder, "b.png", (0, 255, 0))

            def drop(path, modifiers=Qt.NoModifier):
                mime = QMimeData()
                mime.setUrls([QUrl.fromLocalFile(str(path))])
                event = QDropEvent(QPointF(20, 20), Qt.CopyAction, mime,
                                   Qt.LeftButton, modifiers)
                target.dropEvent(event)
                return event

            event = drop(replacement)
            self.assertEqual(target.source, str(replacement), "默认应替换当前贴图")
            self.assertTrue(event.isAccepted())
            self.assertEqual(len(manager.items), 1)

            third = make_png(folder, "c.png", (0, 0, 255))
            drop(third, Qt.ShiftModifier)
            self.assertEqual(len(manager.items), 2, "Shift 拖入应新建贴图")
            for item in manager.items:
                item.close()
            self.app.processEvents()

    def test_drag_out_mime_has_url_and_image(self):
        with tempfile.TemporaryDirectory() as folder:
            path = make_png(folder, "drag.png")
            from sticker.sticker_item import StickerItem
            item = StickerItem(QImage(str(path)), str(path), dict(DEFAULTS))
            try:
                with patch("sticker.sticker_item.QDrag") as drag_class:
                    drag = drag_class.return_value
                    item._start_drag_out()
                mime = drag.setMimeData.call_args[0][0]
                self.assertTrue(mime.hasUrls(), "拖出必须提供文件 URL")
                self.assertTrue(mime.hasImage(), "拖出必须提供图片数据")
                drag.exec.assert_called_once()
            finally:
                item.close()
                self.app.processEvents()

    def test_gif_plays_pauses_steps_and_restores_state(self):
        with tempfile.TemporaryDirectory() as folder:
            path = make_gif(folder)
            from sticker.sticker_item import StickerItem
            item = StickerItem(QImage(str(path)), str(path), dict(DEFAULTS))
            try:
                self.app.processEvents()
                self.assertTrue(item.animated, "GIF 应进入动画模式")
                self.assertIsNotNone(item.movie)
                self.assertGreater(item.movie.frameCount(), 1)
                first_frame = item.pixmap.toImage()
                item.movie.jumpToFrame(1)
                self.app.processEvents()
                self.assertNotEqual(first_frame.constBits(), item.pixmap.toImage().constBits(),
                                    "frameChanged 应把当前帧写回 pixmap")
                item.step_animation_frame(1)
                self.assertTrue(item.animation_paused)
                state = item.state()
                self.assertIn("animation", state)
                self.assertIn(state["animation"]["frame"], range(item.movie.frameCount()))
                self.assertTrue(state["animation"]["paused"])

                item.set_animation_speed(200)
                self.assertEqual(item.animation_speed, 200)
                item.restore_animation_state({"speed": 50, "frame": 0, "paused": False})
                self.assertEqual(item.animation_speed, 50)
                self.assertFalse(item.animation_paused)
            finally:
                item.close()
                self.app.processEvents()

    def test_gif_animation_state_survives_session_restore(self):
        with tempfile.TemporaryDirectory() as folder, \
                patch("sticker.sticker_manager.data_dir", return_value=Path(folder)):
            from sticker.sticker_manager import StickerManager
            path = make_gif(folder)
            manager = StickerManager(dict(DEFAULTS))
            manager.publish_output = Mock()
            item = manager.add(QImage(str(path)), str(path))
            item.set_animation_speed(200)
            item.step_animation_frame(1)
            self.assertTrue(manager.persist())

            restored = StickerManager(dict(DEFAULTS))
            restored.restore()
            try:
                self.assertEqual(len(restored.items), 1)
                copy = restored.items[0]
                self.assertTrue(copy.animated)
                self.assertEqual(copy.animation_speed, 200)
                self.assertTrue(copy.animation_paused)
            finally:
                for sticker in restored.items:
                    sticker.close()
                item.close()
                self.app.processEvents()

    def test_gif_switch_off_keeps_first_frame(self):
        with tempfile.TemporaryDirectory() as folder:
            path = make_gif(folder)
            from sticker.sticker_item import StickerItem
            settings = dict(DEFAULTS, sticker_gif_enabled=False)
            item = StickerItem(QImage(str(path)), str(path), settings)
            try:
                self.assertFalse(item.animated)
                self.assertIsNone(item.movie)
            finally:
                item.close()
                self.app.processEvents()

    def test_panel_viewport_forwards_drag_enter(self):
        from sticker.sticker_manager import StickerManager
        from ui.sticker_panel import StickerPanel

        with tempfile.TemporaryDirectory() as folder, \
                patch("sticker.sticker_manager.data_dir", return_value=Path(folder)):
            path = make_png(folder, "panel.png")
            manager = StickerManager(dict(DEFAULTS))
            panel = StickerPanel(manager)
            try:
                mime = QMimeData()
                mime.setUrls([QUrl.fromLocalFile(str(path))])
                enter = QDragEnterEvent(QPointF(5, 5), Qt.CopyAction, mime, Qt.LeftButton,
                                        Qt.NoModifier)
                QApplication.sendEvent(panel.list_widget.viewport(), enter)
                self.assertTrue(enter.isAccepted(), "管理面板列表区域也应接收拖入")

                manager.settings["sticker_drag_enabled"] = False
                blocked = QDragEnterEvent(QPointF(5, 5), Qt.CopyAction, mime, Qt.LeftButton,
                                          Qt.NoModifier)
                QApplication.sendEvent(panel.list_widget.viewport(), blocked)
                self.assertFalse(blocked.isAccepted(), "关闭拖放后不得接收")
            finally:
                panel.close()
                self.app.processEvents()

    def test_editor_drop_emits_paths_without_replacing(self):
        from tests.base import EditorWindow
        from config.config_manager import DEFAULTS

        with tempfile.TemporaryDirectory() as folder:
            path = make_png(folder, "drop.png")
            editor = EditorWindow(Image.new("RGB", (32, 24), "white"), dict(DEFAULTS))
            observed = []
            editor.files_dropped.connect(observed.extend)
            try:
                mime = QMimeData()
                mime.setUrls([QUrl.fromLocalFile(str(path))])
                event = Mock()
                event.mimeData.return_value = mime
                editor.dropEvent(event)
                self.assertEqual(observed, [str(path)])

                enter = QDragEnterEvent(QPointF(5, 5), Qt.CopyAction, mime, Qt.LeftButton,
                                        Qt.NoModifier)
                QApplication.sendEvent(editor.canvas.viewport(), enter)
                self.assertTrue(enter.isAccepted(),
                                "画布视口上的拖入事件必须被转发给编辑器窗口")
            finally:
                editor.close()
                self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
