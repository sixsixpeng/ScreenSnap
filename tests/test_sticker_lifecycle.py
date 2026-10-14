"""贴图缓存引用、剪贴板清空、持久化与同尺寸替换的回归。"""
import itertools
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) in sys.path:
    sys.path.remove(str(_ROOT))
sys.path.insert(0, str(_ROOT))

from PIL import Image
from PySide6.QtCore import QPoint, QRect, Qt, QMimeData, QUrl
from PySide6.QtGui import QImage, QPainter
from PySide6.QtTest import QTest

from sticker.sticker_manager import StickerManager
from tests.base import CoreTests
from tests import test_sticker


class StickerLifecycleTests(CoreTests):
    output_context = test_sticker.ClipboardOutputTests.output_context

    def add_item(self, context, image=None, **kwargs):
        if image is None:
            image = Image.new("RGB", (80, 60), "red")
        return context.manager.add(image, position=QPoint(20, 20), **kwargs)

    def test_persist_preserves_recycled_source_and_restore_remains_readable(self):
        for default_directory in (False, True):
            options = {"save_dir": ""} if default_directory else {}
            with self.subTest(default_directory=default_directory), self.output_context(
                    sticker_recycle_enabled=True, **options) as context:
                item = self.add_item(context)
                source = Path(item.source)
                item.close()
                QTest.qWait(320)
                self.assertIn(item, context.manager.recycle_bin)
                self.assertTrue(source.is_file())
                self.assertFalse(QImage(str(source)).isNull())
                context.manager.recycle_restore(item)
                self.assertTrue(context.manager.persist())
                restored = StickerManager(context.settings, clipboard=context.board)
                try:
                    restored.restore()
                    self.assertEqual(len(restored.items), 1)
                    self.assertEqual(restored.items[0].source, str(source))
                    self.assertFalse(restored.items[0].pixmap.isNull())
                finally:
                    restored.quitting = True
                    restored.close_all()
                    restored.stop_pending_persistence()
                    restored.deleteLater()
                    self.app.processEvents()

    def test_cleanup_cache_reclaims_sources_after_recycle_removal(self):
        for action in ("delete", "empty", "limit"):
            with self.subTest(action=action), self.output_context(
                    sticker_recycle_enabled=True, sticker_recycle_limit=1) as context:
                active = self.add_item(context)
                active.hide()
                recycled = self.add_item(context)
                removed_source = Path(recycled.source)
                recycled.close()
                context.manager.cleanup_cache(())
                self.assertTrue(Path(active.source).exists())
                self.assertTrue(removed_source.exists())
                if action == "delete":
                    context.manager.recycle_delete(recycled)
                elif action == "empty":
                    context.manager.empty_recycle()
                else:
                    newest = self.add_item(context)
                    newest.close()
                    self.assertEqual(context.manager.recycle_bin, [newest])
                context.manager.cleanup_cache(())
                self.assertFalse(removed_source.exists())
                self.assertTrue(Path(active.source).exists())
                for item in context.manager.recycle_bin:
                    self.assertTrue(Path(item.source).exists())

    def test_cleanup_cache_preserves_shared_source_until_last_reference_removed(self):
        with self.output_context(sticker_recycle_enabled=True) as context:
            first = self.add_item(context)
            source = Path(first.source)
            second = self.add_item(context, QImage(str(source)), source=source)
            first.close()
            context.manager.recycle_delete(first)
            context.manager.cleanup_cache(())
            self.assertTrue(source.exists())
            second.close()
            context.manager.cleanup_cache(())
            self.assertTrue(source.exists())
            context.manager.empty_recycle()
            context.manager.cleanup_cache(())
            self.assertFalse(source.exists())

    def test_restore_without_session_preserves_current_recycle_references(self):
        with self.output_context(sticker_recycle_enabled=True) as context:
            item = self.add_item(context)
            source = Path(item.source)
            item.close()
            context.manager.stop_pending_persistence()
            orphan = source.parent / "sticker_orphan.png"
            Image.new("RGB", (10, 10), "blue").save(orphan)
            context.manager.restore()
            self.assertTrue(source.exists())
            self.assertFalse(orphan.exists())

    def test_clear_history_ignores_self_notifications_until_explicit_paste(self):
        for kind, flags, clear in itertools.product(
                ("image", "text", "html", "color", "files"),
                ((True, False), (False, True), (True, True), (False, False)),
                ("history", "cache")):
            with self.subTest(kind=kind, flags=flags, clear=clear), self.output_context(
                    copy_saved_image=flags[0], copy_saved_path=flags[1]) as context:
                payload = QMimeData()
                if kind == "image":
                    image = QImage(30, 20, QImage.Format.Format_RGB32)
                    image.fill(Qt.GlobalColor.red)
                    payload.setImageData(image)
                elif kind == "html":
                    payload.setHtml("<b>temporary review</b>")
                    payload.setText("temporary review")
                elif kind == "files":
                    file = context.folder / "source.txt"
                    file.write_text("temporary", encoding="utf-8")
                    payload.setUrls([QUrl.fromLocalFile(str(file))])
                else:
                    payload.setText("#123456" if kind == "color" else "temporary review")
                context.board.setMimeData(payload)
                self.assertIsNotNone(context.manager.paste_clipboard())
                if clear == "history":
                    context.manager.clear_clipboard_history()
                else:
                    context.manager.clear_rebuildable_cache()
                self.assertEqual(context.manager.clipboard_history, [])
                self.assertEqual(json.loads((context.folder / "clipboard_history.json").read_text()), [])
                # 只有开启图片复制时才产生需要被忽略的内部回写。
                if flags[0]:
                    for _ in range(3):
                        context.board.dataChanged.emit()
                    self.assertEqual(context.manager.clipboard_history, [])
                item = context.manager.paste_clipboard()
                self.assertIsNotNone(item)
                self.assertEqual(item.origin["kind"], "image" if flags[0] else kind)
                self.assertEqual(len(context.manager.clipboard_history), 1)
                self.assertEqual(context.manager.clipboard_history_index, 0)
                self.assertTrue(context.manager._clipboard_persist_timer.isActive())
                for _ in range(3):
                    context.board.dataChanged.emit()
                self.assertEqual(len(context.manager.clipboard_history), 1)
                self.assertEqual(context.manager.clipboard_history_index, 0)
                context.manager.persist_clipboard_history()
                self.assertEqual(len(json.loads((context.folder / "clipboard_history.json").read_text())), 1)

    def test_external_clipboard_change_after_clear_is_recorded(self):
        with self.output_context() as context:
            context.board.setText("old temporary card")
            context.manager.paste_clipboard()
            context.manager.clear_clipboard_history()
            context.board.setText("new external temporary card")
            self.assertIsNone(context.manager._published_clipboard_key)
            self.assertEqual(context.manager.clipboard_history[0].text, "new external temporary card")
            self.assertEqual(context.manager.paste_clipboard().origin["text"], "new external temporary card")

    def test_empty_clipboard_after_clear_does_not_restore_old_content(self):
        with self.output_context() as context:
            context.board.setText("temporary card")
            context.manager.paste_clipboard()
            context.manager.clear_clipboard_history()
            context.board.setMimeData(QMimeData())
            self.assertIsNone(context.manager.paste_clipboard())
            self.assertEqual(context.manager.clipboard_history, [])

    def test_persist_mixed_bad_and_good_windows_saves_single_snapshots(self):
        with self.output_context() as context:
            good = self.add_item(context)
            source = Path(good.source).parent / "sticker_bad.png"
            Image.new("RGB", (10, 10), "blue").save(source)
            bad = Mock(source=str(source))
            bad.state.side_effect = RuntimeError("deleted window")
            bad.isVisible.side_effect = RuntimeError("deleted window")
            context.manager.items.append(bad)
            try:
                with patch.object(good, "state", wraps=good.state) as state, \
                        patch.object(good, "isVisible", side_effect=RuntimeError("visibility unavailable")):
                    self.assertTrue(context.manager.persist())
                    self.assertEqual(state.call_count, 1)
                bad.state.assert_called_once()
                bad.isVisible.assert_not_called()
                saved = json.loads((context.folder / "stickers.json").read_text())
                self.assertEqual([record["source"] for record in saved], [good.source])
                self.assertEqual(context.manager.last_placement["source"], good.source)
                self.assertTrue(source.exists())
                self.assertFalse((context.folder / "stickers.tmp").exists())
            finally:
                context.manager.items.remove(bad)

    def test_persist_all_bad_preserves_previous_session_and_skips_cleanup(self):
        with self.output_context() as context:
            item = self.add_item(context)
            session = context.folder / "stickers.json"
            previous = '[{"id":"previous"}]'
            session.write_text(previous)
            with patch.object(item, "state", side_effect=RuntimeError("deleted window")), \
                    patch.object(context.manager, "cleanup_cache") as cleanup:
                self.assertFalse(context.manager.persist())
                cleanup.assert_not_called()
            self.assertEqual(session.read_text(), previous)
            self.assertTrue(Path(item.source).exists())

    def test_persist_placement_failures_do_not_block_session(self):
        for method in ("_remember_placement", "_save_placement_states"):
            with self.subTest(method=method), self.output_context() as context:
                item = self.add_item(context)
                with patch.object(context.manager, method, side_effect=RuntimeError("layout unavailable")):
                    self.assertTrue(context.manager.persist())
                self.assertEqual(json.loads((context.folder / "stickers.json").read_text())[0]["source"], item.source)

    def test_persist_replace_failure_preserves_previous_session_and_sources(self):
        with self.output_context() as context:
            item = self.add_item(context)
            session = context.folder / "stickers.json"
            session.write_text('[{"id":"previous"}]')
            with patch.object(Path, "replace", side_effect=OSError("file in use")), \
                    patch.object(context.manager, "cleanup_cache") as cleanup:
                self.assertFalse(context.manager.persist())
                cleanup.assert_not_called()
            self.assertEqual(session.read_text(), '[{"id":"previous"}]')
            self.assertTrue(Path(item.source).exists())

    def test_persist_empty_session_still_clears_previous_session(self):
        with self.output_context(sticker_recycle_enabled=False) as context:
            item = self.add_item(context)
            source = Path(item.source)
            self.assertTrue(context.manager.persist())
            item.close()
            self.assertTrue(context.manager.persist())
            self.assertEqual(json.loads((context.folder / "stickers.json").read_text()), [])
            self.assertFalse(source.exists())

    @staticmethod
    def half_image(left):
        image = QImage(80, 60, QImage.Format.Format_ARGB32)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        painter.fillRect(QRect(0 if left else 40, 0, 40, 60), Qt.GlobalColor.red)
        painter.end()
        return image

    def test_same_size_replace_refreshes_mask_preserving_geometry_and_dpr(self):
        for ratio, scale, background, shadow, shown in itertools.product(
                (1.0, 1.25, 1.5, 1.75), (0.75, 1.5),
                ("transparent", "pseudo", "dark_checker", "light_checker"), (False, True), (False, True)):
            with self.subTest(ratio=ratio, scale=scale, background=background,
                              shadow=shadow, shown=shown), self.output_context(
                                      sticker_shadow_enabled=shadow) as context:
                item = self.add_item(context, self.half_image(True), show=shown)
                with patch.object(item, "device_pixel_ratio", return_value=ratio):
                    item.scale_factor = scale
                    item.apply_style()
                    item.set_background_mode(background)
                    before = item.geometry()
                    changed = Mock()
                    item.state_changed.connect(changed)
                    item.replace_image(self.half_image(False), context.folder / "replacement.png")
                    self.assertEqual(item.geometry(), before)
                    self.assertEqual(item.scale_factor, scale)
                    self.assertEqual(item.pixmap.devicePixelRatio(), ratio)
                    self.assertEqual(item.original_pixmap.devicePixelRatio(), ratio)
                    self.assertTrue(changed.called)
                    rect = item.image_rect()
                    left = rect.topLeft() + QPoint(rect.width() // 4, rect.height() // 2)
                    right = rect.topLeft() + QPoint(rect.width() * 3 // 4, rect.height() // 2)
                    mask = item.mask()
                    if background == "transparent" and not shadow:
                        self.assertFalse(mask.isEmpty())
                        self.assertFalse(mask.contains(left))
                        self.assertTrue(mask.contains(right))
                    elif background != "transparent":
                        self.assertTrue(mask.isEmpty())
                    else:
                        self.assertTrue(mask.contains(right))
                    opaque = QImage(80, 60, QImage.Format.Format_RGB32)
                    opaque.fill(Qt.GlobalColor.blue)
                    item.replace_image(opaque, context.folder / "opaque.png")
                    self.assertTrue(item.mask().isEmpty())

    def test_file_replace_and_history_cycle_refresh_same_size_mask_and_persist(self):
        for entry in ("file", "history"):
            with self.subTest(entry=entry), self.output_context(sticker_shadow_enabled=False) as context:
                first = context.folder / "first.png"
                second = context.folder / "second.png"
                self.half_image(True).save(str(first))
                self.half_image(False).save(str(second))
                item = self.add_item(context, QImage(str(first)), source=first)
                item.set_background_mode("transparent")
                if entry == "file":
                    with patch("sticker.sticker_manager.QFileDialog.getOpenFileNames", return_value=([str(second)], "")):
                        self.assertTrue(context.manager.open_file(replace=item))
                else:
                    context.manager.history_sticker = item
                    context.manager.history_index = 0
                    with patch.object(context.manager, "files", return_value=[first, second]):
                        context.manager.cycle(1)
                    self.assertEqual(len(context.manager.items), 1)
                self.assertEqual(item.source, str(second))
                rect = item.image_rect()
                self.assertFalse(item.mask().contains(rect.topLeft() + QPoint(rect.width() // 4, rect.height() // 2)))
                self.assertTrue(context.manager._persist_timer.isActive())
                QTest.qWait(320)
                self.assertEqual(json.loads((context.folder / "stickers.json").read_text())[0]["source"], str(second))


if __name__ == "__main__":
    unittest.main()
