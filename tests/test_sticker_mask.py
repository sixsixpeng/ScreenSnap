"""贴图透明遮罩与显示坐标一致性；仅临时数据、内存剪贴板及离屏窗口。"""
import itertools
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) in sys.path:
    sys.path.remove(str(_ROOT))
sys.path.insert(0, str(_ROOT))

from PySide6.QtCore import QEvent, QPoint, QRect, Qt
from PySide6.QtGui import QColor, QImage, QPainter

from sticker.sticker_item import StickerItem
from tests.base import CoreTests
from tests import test_sticker


class StickerMaskTests(CoreTests):
    output_context = test_sticker.ClipboardOutputTests.output_context
    output_editor = test_sticker.ClipboardOutputTests.output_editor

    @staticmethod
    def image(kind="rounded", size=(161, 121)):
        fmt = QImage.Format_RGB32 if kind == "rgb" else QImage.Format_ARGB32
        image = QImage(*size, fmt)
        image.fill(QColor("#ee8844"))
        if kind in ("rounded", "hole"):
            image.fill(Qt.transparent)
            painter = QPainter(image)
            painter.setRenderHint(QPainter.Antialiasing)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor("#ee8844"))
            painter.drawRoundedRect(QRect(0, 0, *size), 20, 20)
            if kind == "hole":
                painter.setCompositionMode(QPainter.CompositionMode_Source)
                painter.fillRect(QRect(size[0] // 4, size[1] // 3, 24, 24), Qt.transparent)
                painter.fillRect(QRect(size[0] // 2, size[1] // 3, 24, 24), QColor(50, 80, 100, 128))
            painter.end()
        return image

    def make_item(self, image=None, **settings):
        options = dict(sticker_border_enabled=False, sticker_shadow_enabled=False)
        options.update(settings)
        item = StickerItem(self.image() if image is None else image, settings=options)
        self.addCleanup(item.deleteLater)
        self.addCleanup(item.close)
        return item

    def assert_mask_matches_image(self, item):
        # 独立按 paintEvent 的显式目标矩形渲染，不复用被测 createAlphaMask 链路。
        raster = QImage(item.size(), QImage.Format_ARGB32_Premultiplied)
        raster.fill(Qt.transparent)
        painter = QPainter(raster)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        painter.drawPixmap(item.image_rect(), item.pixmap)
        painter.end()
        region = item.mask()
        rect = item.image_rect()
        if item.background_mode != "transparent" or not item.pixmap.hasAlphaChannel():
            self.assertTrue(region.isEmpty())
            return
        self.assertFalse(region.isEmpty())
        # 采样全域，包括缩小后的伪圆角落点；只断言邻域稳定的透明/不透明区域。
        # 二值 alpha 与平滑 alpha 的抗锯齿边缘不属于本测试契约。
        step = max(1, min(rect.width(), rect.height()) // 30)
        for y in range(rect.top() + 1, rect.bottom(), step):
            for x in range(rect.left() + 1, rect.right(), step):
                values = [raster.pixelColor(x + dx, y + dy).alpha()
                          for dx, dy in ((0, 0), (-1, 0), (1, 0), (0, -1), (0, 1))]
                point = QPoint(x, y)
                if min(values) == 255:
                    self.assertTrue(region.contains(point), f"opaque pixel clipped at {point}, rect={rect}")
                elif max(values) == 0 and not item.shadow_enabled:
                    self.assertFalse(region.contains(point), f"transparent pixel filled at {point}, rect={rect}")

    def test_actual_qt_dpr_rounded_mask_has_no_internal_cutouts(self):
        item = self.make_item()
        self.assertEqual(item.device_pixel_ratio(), self.app.primaryScreen().devicePixelRatio())
        self.assertEqual(item.pixmap.devicePixelRatio(), item.device_pixel_ratio())
        self.assert_mask_matches_image(item)

    def test_mask_matrix_preserves_alpha_at_all_dpr_zoom_and_backgrounds(self):
        item = self.make_item()
        for ratio, scale, kind, background, border, shadow in itertools.product(
                (1.0, 1.25, 1.5, 1.75), (0.75, 1.0, 1.5),
                ("rgb", "rgba", "rounded", "hole"),
                ("transparent", "pseudo", "dark_checker", "light_checker"),
                (False, True), (False, True)):
            with self.subTest(ratio=ratio, scale=scale, kind=kind, background=background,
                              border=border, shadow=shadow), patch.object(
                                      item, "device_pixel_ratio", return_value=ratio):
                image = self.image(kind)
                before = image.copy()
                item.scale_factor = scale
                item.border_enabled = border
                item.shadow_enabled = shadow
                item.background_mode = background
                item.replace_image(image, "temporary.png")
                self.assert_mask_matches_image(item)
                self.assertEqual(image, before)
                self.assertEqual(item.original_pixmap.toImage().size(), image.size())

    def test_mask_handles_extreme_zoom_and_preserves_real_corners(self):
        item = self.make_item()
        for scale in (0.1, 1.1, 2.0, 10.0):
            with self.subTest(scale=scale):
                item.scale_factor = scale
                item.apply_style()
                self.assert_mask_matches_image(item)
        item.reset_size()
        self.assert_mask_matches_image(item)

    def test_apply_style_refreshes_mask_without_resize(self):
        item = self.make_item()
        before = item.size()
        item.background_mode = "pseudo"
        with patch.object(item, "update_input_mask", wraps=item.update_input_mask) as refresh:
            item.apply_style()
            self.assertEqual(item.size(), before)
            self.assertTrue(refresh.called)
        self.assertTrue(item.mask().isEmpty())
        item.background_mode = "transparent"
        item.apply_style()
        self.assert_mask_matches_image(item)

    def test_dpr_event_refreshes_mask_even_when_window_size_stays_equal(self):
        item = self.make_item(self.image(size=(160, 120)))
        with patch.object(item, "device_pixel_ratio", return_value=1.0):
            item.apply_style()
        before = item.size()
        item.scale_factor = 1.25
        with patch.object(item, "device_pixel_ratio", return_value=1.25), patch.object(
                item, "update_input_mask", wraps=item.update_input_mask) as refresh:
            self.app.sendEvent(item, QEvent(QEvent.Type.DevicePixelRatioChange))
            self.assertEqual(item.size(), before)
            self.assertEqual(item.pixmap.devicePixelRatio(), 1.25)
            self.assertTrue(refresh.called)
            self.assert_mask_matches_image(item)

    def test_square_rotation_refreshes_mask_without_resize(self):
        item = self.make_item(self.image("hole", size=(121, 121)))
        before = item.size()
        for action in (lambda: item.rotate(90), item.reset_rotation):
            with patch.object(item, "update_input_mask", wraps=item.update_input_mask) as refresh:
                action()
                self.assertEqual(item.size(), before)
                self.assertTrue(refresh.called)
                self.assert_mask_matches_image(item)

    def test_reported_entry_points_share_correct_mask_and_unchanged_saved_pixels(self):
        from main import Application

        for entry in ("space", "editor", "inline", "file"):
            with self.subTest(entry=entry), self.output_context(
                    editor_image_round_corners=True, editor_image_corner_radius=20,
                    sticker_border_enabled=False, sticker_shadow_enabled=False) as context:
                if entry in ("editor", "inline"):
                    editor = self.output_editor(context, entry == "inline")
                    expected = editor.output_image()
                    next(button for button in editor.toolbar.output_buttons
                         if button.text() == "贴图").click()
                    saved = next(context.folder.glob("output*.png"))
                else:
                    expected = self.image()
                    if entry == "space":
                        Application.add_quick_sticker(context.app, expected, QPoint(20, 20))
                        saved = next(context.folder.glob("output*.png"))
                    else:
                        saved = context.folder / "input.png"
                        self.assertTrue(expected.save(str(saved)))
                        with patch("sticker.sticker_manager.QFileDialog.getOpenFileNames",
                                   return_value=([str(saved)], "")):
                            self.assertTrue(context.manager.open_file())
                self.assertEqual(len(context.manager.items), 1)
                item = context.manager.items[0]
                self.assert_mask_matches_image(item)
                fmt = QImage.Format_ARGB32_Premultiplied
                self.assertEqual(QImage(str(saved)).convertToFormat(fmt), expected.convertToFormat(fmt))
                self.assertEqual(item.original_pixmap.toImage().convertToFormat(fmt), expected.convertToFormat(fmt))

    def test_mask_diagnostics_do_not_repeat_for_unchanged_state(self):
        item = self.make_item()
        with patch("sticker.sticker_item.logging.getLogger") as logger:
            for _ in range(30):
                item.update_input_mask()
            self.assertFalse(logger.return_value.debug.called)


if __name__ == "__main__":
    unittest.main()
