"""截图：遮罩、选区、预设直出、提示条与放大镜、光标与 Toast。"""

import os
import sys

# 无论 -m unittest、目录内 discover 还是直接跑文件，都要能导入 tests.base。
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from pathlib import Path
from tests.base import (CoreTests, Mock, patch, Path, tempfile, json, unittest, Image,
                        QPointF, Qt, QPoint, QRect, QRectF, QTest, QApplication,
                        ConfigManager, resolved_dir, AnnotationCanvas, shape,
                        EditorWindow, SelectionRects, StickerItem, StickerManager,
                        SettingsWindow, HotkeyEdit)


class CaptureTests(CoreTests):
    def test_capture_operation_tips_have_contrast_backdrop(self):
        from PySide6.QtGui import QColor, QImage, QPainter
        from screenshot.overlay_info import paint_info

        anchor = QRect(300, 240, 140, 140)
        area = QRect(0, 0, 800, 600)
        image = QImage(800, 600, QImage.Format_ARGB32)
        image.fill(QColor("white"))
        painter = QPainter(image)
        bar = paint_info(painter, anchor, area, ["20, 20", "拖拽框选", "Esc取消"])
        painter.end()
        # 提示条画在放大镜下方，整条有对比底色（不是白底）。
        self.assertGreaterEqual(bar.top(), anchor.bottom())
        backdrop = image.pixelColor(bar.center().x(), bar.top() + 1)
        self.assertNotEqual(backdrop.name(), "#ffffff")
        self.assertLess(backdrop.red(), 100)
        self.assertEqual(backdrop.alpha(), 255)
        # 提示条之外仍是原图（提示只画在遮罩表面）。
        self.assertEqual(image.pixelColor(400, 20).name(), "#ffffff")

        painter = Mock()
        painter.device.return_value.width.return_value = 800
        painter.device.return_value.height.return_value = 600
        painter.fontMetrics.return_value.horizontalAdvance.side_effect = lambda text: len(text) * 8
        painter.fontMetrics.return_value.height.return_value = 16
        painter.fontMetrics.return_value.elidedText.side_effect = lambda text, *_: text
        bar = paint_info(painter, anchor, area, ["拖动移动", "四角/边中点缩放"])
        rows = [call.args[-1] for call in painter.drawText.call_args_list]
        # 一行放得下就不换行，提示项之间用 ` | ` 分隔。
        self.assertEqual(rows, ["拖动移动 | 四角/边中点缩放"])
        self.assertEqual(painter.drawRoundedRect.call_args.args[0].height(), 16 + 8)
        self.assertEqual(bar, painter.drawRoundedRect.call_args.args[0])
        # 超宽时按项换行（不截断单个提示项），高度随行数增长。
        painter.drawText.reset_mock()
        painter.drawRoundedRect.reset_mock()
        paint_info(painter, anchor, area, ["甲" * 40, "乙" * 40])
        rows = [call.args[-1] for call in painter.drawText.call_args_list]
        self.assertEqual(rows, ["甲" * 40, "乙" * 40])
        self.assertEqual(painter.drawRoundedRect.call_args.args[0].height(), 16 * 2 + 8)
        # 空项（当前阶段不适用）直接跳过，不留下多余分隔符。
        painter.drawText.reset_mock()
        paint_info(painter, anchor, area, ["20, 20", "", "拖拽框选"])
        self.assertEqual([call.args[-1] for call in painter.drawText.call_args_list],
                         ["20, 20 | 拖拽框选"])

    def test_capture_tips_keep_long_coordinates_visible_in_inline_edit(self):
        from screenshot.overlay_info import paint_info

        painter = Mock()
        painter.fontMetrics.return_value.horizontalAdvance.side_effect = lambda text: len(text) * 12
        painter.fontMetrics.return_value.height.return_value = 16
        painter.fontMetrics.return_value.elidedText.side_effect = lambda text, *_: text
        paint_info(painter, QRect(400, 400, 140, 140), QRect(0, 0, 1920, 1080),
                   ["-123456, 987654", "拖拽框选"])
        rect = painter.drawRoundedRect.call_args.args[0]
        self.assertLessEqual(rect.width(), 1920 - 16)
        self.assertGreaterEqual(rect.left(), 4)
        self.assertLessEqual(rect.right(), 1920 - 4)
        # 负坐标位数多时也不截断提示项本身。
        self.assertIn("-123456, 987654",
                      " ".join(call.args[-1] for call in painter.drawText.call_args_list))

    def test_capture_tips_width_follows_coordinates_and_selection_size(self):
        from screenshot.overlay_info import paint_info

        def bar_width(items):
            painter = Mock()
            painter.device.return_value.width.return_value = 1920
            painter.device.return_value.height.return_value = 1080
            painter.fontMetrics.return_value.horizontalAdvance.side_effect = lambda text: len(text) * 12
            painter.fontMetrics.return_value.height.return_value = 16
            painter.fontMetrics.return_value.elidedText.side_effect = lambda text, *_: text
            bar = paint_info(painter, QRect(400, 400, 140, 140), QRect(0, 0, 1920, 1080), items,
                             max_width=1920)
            return bar.width()

        short = bar_width(["1, 2"])
        # 坐标位数增加（含负坐标显示器）时提示条必须跟着变宽，不能是固定宽度。
        self.assertGreater(bar_width(["-123456, 987654"]), short)
        # 选区尺寸同样计入文案，出现选区后条宽也会变化。
        self.assertGreater(bar_width(["1, 2  800 x 600"]), short)
        # 提示项全部为空（都被关掉）时不画提示条，也不留空条。
        self.assertEqual(bar_width(["", ""]), 0)

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
                self.assertEqual(native.call_args.kwargs["duration"], "short")
                preview._show_local_preview.assert_not_called()
        finally:
            preview.close()

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

    def test_capture_notification_async_toast_failure_shows_local_preview(self):
        from ui.capture_notification import CaptureNotification

        notification = CaptureNotification(Image.new("RGB", (40, 20), "green"))
        with tempfile.TemporaryDirectory() as folder, \
                patch("ui.native_toast.cache_toast_image", return_value=Path(folder) / "toast.png"), \
                patch("ui.native_toast.show_native_toast", return_value=True) as native:
            notification.show_preview()
            failure_callback = native.call_args.args[4]
            failure_callback(RuntimeError("temporary WinRT failure"))
            self.app.processEvents()
        self.assertEqual(notification.backend, "win11toast")
        self.assertTrue(notification.isVisible())
        notification.close()

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
        with patch("app.capture_flow.EditorWindow") as editor_factory, \
                patch("app.notification_flow.CaptureNotification") as notice, \
                patch("main.QApplication.beep") as beep:
            app.edit_images([(Image.new("RGB", (20, 12), "blue"), None)], from_capture=False)
        editor_factory.assert_called_once()
        notice.assert_not_called()
        beep.assert_not_called()

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
            # 新语义：微调要求"握着控制点"（按住左键，或抓住过留下 nudge_corner）
            mask._grip_down = True
            for key, dx, dy in ((Qt.Key_Left, -1, 0), (Qt.Key_D, 1, 0),
                                (Qt.Key_Up, 0, -1), (Qt.Key_S, 0, 1),
                                (Qt.Key_A, -1, 0), (Qt.Key_Right, 1, 0),
                                (Qt.Key_W, 0, -1), (Qt.Key_Down, 0, 1)):
                before = mask.selection.nudge_corner[2]
                if key == Qt.Key_S:
                    mask.save_selection()      # S 走快捷保存通道；按住时等于下移微调
                else:
                    QTest.keyClick(mask, key)
                self.assertEqual(mask.selection.nudge_corner[2], before + QPoint(dx, dy))
            self.assertEqual(mask.selection.rects[0], QRect(mask.selection.nudge_corner[1], target).normalized())
            mask._grip_down = False
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
            # 新规则：必须抓住手柄/边线才允许微调 —— 把指针放到选区左上角的手柄上
            # （nudge_corner 仍为 None，因此走"位移累计"分支，断言 position+delta）。
            mask.position = QPoint(20, 20)
            mask._grip_down = True          # 新语义：按住左键期间才允许微调
            mask.selection.nudge_corner = None   # 走"位移累计"分支，断言 position+delta
            for key, delta in ((Qt.Key_D, QPoint(1, 0)), (Qt.Key_Left, QPoint(-1, 0)),
                               (Qt.Key_W, QPoint(0, -1)), (Qt.Key_Down, QPoint(0, 1)),
                               (Qt.Key_A, QPoint(-1, 0)), (Qt.Key_Right, QPoint(1, 0)),
                               (Qt.Key_S, QPoint(0, 1)), (Qt.Key_Up, QPoint(0, -1))):
                before_position = QPoint(mask.position)
                before_rect = QRect(mask.selection.rects[0])
                if key == Qt.Key_S:
                    mask.save_selection()      # S 走快捷保存通道；按住时等于下移微调
                else:
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
        with patch("app.capture_flow.capture", return_value=(frame, bounds, [bounds], frame.copy())), \
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

    def test_sequence_preset_applies_shape_and_colors(self):
        from editor.annotation_items import apply_sequence_preset
        settings = {}
        apply_sequence_preset(settings, "green_star")
        self.assertEqual(settings["sequence_shape"], "star")
        self.assertEqual(settings["sequence_fill_color"], "#2e9e5b")
        # custom 预设不覆盖任何单项，原有组合保持不变。
        apply_sequence_preset(settings, "custom")
        self.assertEqual(settings["sequence_shape"], "star")

    def test_capture_picker_hint_visibility(self):
        from unittest.mock import Mock, patch
        from config.config_manager import DEFAULTS
        from PySide6.QtCore import QPoint, QRect
        from screenshot.overlay_info import paint_info
        from screenshot.mask_window import MaskWindow

        # 取色说明并入提示条（paint_info），不再有独立 QLabel。
        bounds = {"left": 0, "top": 0, "width": 160, "height": 100}
        monitors = [{"left": 0, "top": 0, "width": 160, "height": 100}]
        screen_infos = [{"geometry": QRect(0, 0, 160, 100), "dpr": 1.0}]
        settings = {**DEFAULTS, "crosshair": False, "magnifier": False, "sound": False}
        with patch("screenshot.mask_window.visible_windows", return_value=[]), \
                patch("core.dpi.DisplayMapper.collect_screen_infos", return_value=screen_infos):
            mask = MaskWindow(Image.new("RGB", (160, 100), "blue"), bounds,
                              monitors, settings)
        # 1) 取色模式下，提示项只保留取色说明（即便尚未取到色值）。
        mask.picker_mode = True
        picked = " ".join(mask.capture_hint_items())
        self.assertIn("退出取色", picked)
        self.assertNotIn("左拖松开按设置", picked)
        self.assertNotIn("拖动移动", picked)
        # 2) 非取色模式仍显示原始操作说明，并提示如何进入取色模式。
        mask.picker_mode = False
        normal = " ".join(mask.capture_hint_items())
        self.assertIn("左拖松开按设置", normal)
        self.assertIn("取色", normal)
        # 3) 真实遮罩不再持有独立 picker_hint 控件（避免与提示栏重叠）。
        self.assertFalse(hasattr(mask, "picker_hint"))
        mask.close()

    def test_capture_picker_shortcut_honors_modifiers_and_runtime_rebinding(self):
        from config.config_manager import DEFAULTS
        from screenshot.mask_window import MaskWindow

        bounds = {"left": 0, "top": 0, "width": 160, "height": 100}
        settings = dict(DEFAULTS, capture_picker_shortcut="C",
                        crosshair=False, magnifier=False, sound=False)
        with patch("screenshot.mask_window.visible_windows", return_value=[]):
            mask = MaskWindow(Image.new("RGB", (160, 100), "blue"), bounds,
                              [bounds], settings)
        try:
            mask.show()
            self.app.processEvents()
            QTest.keyClick(mask, Qt.Key_C)
            self.assertTrue(mask.picker_mode)
            QTest.keyClick(mask, Qt.Key_C)
            self.assertFalse(mask.picker_mode)

            settings["capture_picker_shortcut"] = "Alt+C"
            mask.sync_capture_action_shortcuts(settings)
            QTest.keyClick(mask, Qt.Key_C)
            self.assertFalse(mask.picker_mode)
            QTest.keyClick(mask, Qt.Key_C, Qt.AltModifier)
            self.assertTrue(mask.picker_mode)

            settings["capture_picker_shortcut"] = "F2"
            mask.sync_capture_action_shortcuts(settings)
            QTest.keyClick(mask, Qt.Key_C, Qt.AltModifier)
            self.assertTrue(mask.picker_mode)
            QTest.keyClick(mask, Qt.Key_F2)
            self.assertFalse(mask.picker_mode)
        finally:
            mask.close()



    def test_right_drags_collect_multiple_regions_until_enter(self):
        from config.config_manager import DEFAULTS
        from screenshot.mask_window import MaskWindow

        bounds = {"left": 0, "top": 0, "width": 140, "height": 100}
        settings = {**DEFAULTS, "inline_edit": True, "capture_after_selection": "save",
                    "magnifier": False, "crosshair": False, "sound": False}
        with patch("screenshot.mask_window.visible_windows", return_value=[]):
            mask = MaskWindow(Image.new("RGB", (140, 100), "blue"), bounds, [bounds], settings)
        routed = []
        mask.edit_requested.connect(lambda images, positions: routed.append((images, positions)))
        mask.show()
        self.app.processEvents()
        for start, end in ((QPoint(10, 10), QPoint(40, 35)),
                   (QPoint(20, 20), QPoint(60, 50))):
            QTest.mousePress(mask, Qt.RightButton, Qt.NoModifier, start)
            QTest.mouseMove(mask, end)
            QTest.mouseRelease(mask, Qt.RightButton, Qt.NoModifier, end)
            self.assertEqual(routed, [])
            self.assertTrue(mask.isVisible())
        self.assertEqual(len(mask.selection.rects), 2)
        QTest.keyClick(mask, Qt.Key_Return)
        self.assertEqual(len(routed), 1)
        self.assertEqual(len(routed[0][0]), 2)
        self.assertEqual([image.size for image, alternate in routed[0][0]],
                         [(31, 26), (41, 31)])
        self.assertIsNone(mask.session.inline_editor)
        self.assertFalse(mask.isVisible())

    def test_preset_capture_modes_bypass_mask_and_apply_action(self):
        """全屏/当前显示器不再弹遮罩，裁好后直接按设置保存或进编辑器。"""
        from types import SimpleNamespace
        from config.config_manager import DEFAULTS
        from main import Application

        bounds = {"left": 0, "top": 0, "width": 120, "height": 80}
        monitors = [bounds]
        frame = Image.new("RGB", (120, 80), "blue")
        for mode, setting, action, expect_edit in (
                ("fullscreen", "capture_fullscreen_action", "save", False),
                ("fullscreen", "capture_fullscreen_action", "edit", True),
                ("monitor", "capture_monitor_action", "save", False),
                ("monitor", "capture_monitor_action", "edit", True)):
            with self.subTest(mode=mode, action=action):
                app = Application.__new__(Application)
                app.config = SimpleNamespace(data={**DEFAULTS, setting: action})
                app.logger = Mock()
                app.mask = None
                app.save_capture_images = Mock()
                app.edit_images = Mock()
                with patch("app.capture_flow.capture", return_value=(frame, bounds, monitors, None)), \
                        patch.object(Application, "popup_intruders", return_value=[]):
                    app.show_mask(mode)
                self.assertIsNone(app.mask)
                if expect_edit:
                    app.edit_images.assert_called_once()
                    app.save_capture_images.assert_not_called()
                    images, positions = app.edit_images.call_args.args[0], app.edit_images.call_args.kwargs["positions"]
                else:
                    app.save_capture_images.assert_called_once()
                    app.edit_images.assert_not_called()
                    images = app.save_capture_images.call_args.args[0]
                    positions = None
                self.assertEqual(images[0][0].size, (120, 80))
                if positions is not None:
                    self.assertEqual(len(positions), 1)

    def test_mask_inline_edit_single_region_saves_and_closes_in_place(self):
        from config.config_manager import DEFAULTS
        from screenshot.mask_window import MaskWindow
        from PySide6.QtWidgets import QToolButton

        with tempfile.TemporaryDirectory() as folder:
            bounds = {"left": 0, "top": 0, "width": 120, "height": 80}
            settings = {**DEFAULTS, "save_dir": folder, "filename": "inline", "inline_edit": True,
                        "capture_after_selection": "edit", "crosshair": False, "magnifier": False,
                        "mask_opacity": 0, "sound": False,
                        "bubble": False, "editor_image_round_corners": False}
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
            self.assertEqual(saved, [])
            self.assertEqual(list(Path(folder).iterdir()), [])
            mask.session.inline_editor.canvas.image = Image.new("RGB", (30, 20), "red")
            mask.session.inline_editor.canvas.refresh_image()
            mask.session.inline_editor.execute("save")
            self.assertEqual(len(saved), 1)
            path = Path(saved[0][0])
            self.assertTrue(path.is_file())
            self.assertRegex(path.name, r"^inline(?:_\d+)?\.png$")
            self.assertFalse(mask.isVisible())
            with Image.open(path) as opened:
                self.assertEqual(opened.getpixel((0, 0))[:3], (255, 0, 0))

    def test_inline_options_menu_does_not_raise_magnifier_or_move_capture_cursor(self):
        from PySide6.QtCore import QEvent, QPointF
        from PySide6.QtGui import QCursor, QMouseEvent
        from config.config_manager import DEFAULTS
        from screenshot.mask_window import MaskWindow

        with tempfile.TemporaryDirectory() as folder:
            bounds = {"left": 0, "top": 0, "width": 1200, "height": 900}
            settings = {**DEFAULTS, "save_dir": folder, "inline_edit": True, "magnifier": True}
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

    def test_mask_selection_draws_magnifier_on_mask_surface(self):
        from PySide6.QtGui import QCursor
        from config.config_manager import DEFAULTS
        from screenshot.mask_window import MaskWindow

        bounds = {"left": 0, "top": 0, "width": 320, "height": 240}
        # 提示条会画在光标附近，本用例只校验放大镜，故关掉全部提示项避免遮挡取样点。
        settings = {**DEFAULTS, "magnifier": True, "crosshair": False, "mask_opacity": 0,
                    "capture_hint_order": []}
        with patch("screenshot.mask_window.visible_windows", return_value=[]), \
                patch.object(QCursor, "pos", return_value=QPoint(70, 50)):
            mask = MaskWindow(Image.new("RGB", (320, 240), "blue"), bounds, [bounds], settings)
        mask.show()
        self.app.processEvents()
        self.assertTrue(mask.magnifier_overlay.isVisible())
        self.assertEqual(mask.magnifier_overlay.grab().toImage().pixelColor(0, 0).name(), "#ffffff")
        # 放大镜是独立浮层窗口，遮罩表面仍是原图；关掉提示项后遮罩上不再画提示条。
        self.assertTrue(mask.info_bar_rect.isEmpty())
        self.assertEqual(mask.grab().toImage().pixelColor(88, 68).name(), "#0000ff")
        mask.close()

    def test_mask_selection_previews_capture_round_corners(self):
        from config.config_manager import DEFAULTS
        from screenshot.mask_window import MaskWindow

        bounds = {"left": 0, "top": 0, "width": 500, "height": 400}
        # 提示条会画在光标附近，本用例只校验圆角预览，故关掉全部提示项避免遮挡取样点。
        settings = {**DEFAULTS, "crosshair": False, "mask_opacity": 100,
                    "editor_image_corner_radius": 24, "capture_hint_order": []}
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
            settings = {**DEFAULTS, "save_dir": folder, "inline_edit": True,
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

    def test_mask_inline_edit_output_actions_close_and_route(self):
        from config.config_manager import DEFAULTS
        from screenshot.mask_window import MaskWindow

        def inline_mask(folder, name):
            bounds = {"left": 0, "top": 0, "width": 80, "height": 60}
            settings = {**DEFAULTS, "save_dir": folder, "filename": name, "inline_edit": True,
                        "capture_after_selection": "edit", "crosshair": False, "magnifier": False,
                        "mask_opacity": 0, "bubble": False, "editor_image_round_corners": False}
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
            # 进入编辑器不落盘：此刻还没有保存路径，路径来自下面这次显式保存。
            self.assertIsNone(editor.last_path)
            editor.canvas.image = Image.new("RGB", (30, 20), "red")
            editor.canvas.refresh_image()
            save_button = next(button for button, action in editor.toolbar.command_buttons if action == "save")
            from PySide6.QtGui import QGuiApplication
            QGuiApplication.clipboard().clear()
            save_button.click()
            self.app.processEvents()
            initial_path = editor.last_path
            self.assertIsNotNone(initial_path)
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
            settings = {**DEFAULTS, "save_dir": folder, "inline_edit": True, "magnifier": False}
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

    def test_mask_inline_edit_locks_selection_layer(self):
        from config.config_manager import DEFAULTS
        from screenshot.mask_window import MaskWindow

        with tempfile.TemporaryDirectory() as folder:
            bounds = {"left": 0, "top": 0, "width": 120, "height": 80}
            settings = {**DEFAULTS, "save_dir": folder, "filename": "inline", "inline_edit": True,
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

    def test_global_escape_after_mask_deleted_does_not_crash_hook(self):
        """遮罩销毁后旧 Esc 兜底回调再次触发：只能安全退出，不能打断键盘钩子线程。"""
        import shiboken6
        import sys
        from unittest.mock import Mock
        from PySide6.QtCore import QEvent
        from config.config_manager import DEFAULTS
        from screenshot.mask_window import MaskWindow

        removed = []

        def fake_add_hotkey(binding, callback, suppress=False):
            return "escape-handle"

        def fake_remove_hotkey(handle):
            removed.append(handle)

        fake_keyboard = Mock(add_hotkey=fake_add_hotkey, remove_hotkey=fake_remove_hotkey)
        bounds = {"left": 0, "top": 0, "width": 120, "height": 80}
        settings = dict(DEFAULTS, crosshair=False, magnifier=False, window_detection=False)
        with patch("screenshot.mask_window.visible_windows", return_value=[]), \
                patch.dict(sys.modules, {"keyboard": fake_keyboard}):
            mask = MaskWindow(Image.new("RGB", (120, 80), "white"), bounds, [bounds], settings)
            mask.escape_fallback = mask._install_escape_fallback()
            self.assertEqual(mask.escape_fallback, "escape-handle")
            callback = mask._global_escape
            mask.setAttribute(Qt.WA_DeleteOnClose)
            mask.close()
            self.app.sendPostedEvents(None, QEvent.DeferredDelete)
            self.app.processEvents()
            # 关闭路径必须释放兜底句柄，否则遮罩销毁后回调仍会被钩子调用。
            self.assertFalse(shiboken6.isValid(mask))
            self.assertEqual(removed, ["escape-handle"])
            try:
                callback()
            except RuntimeError as error:
                self.fail(f"遮罩销毁后的 Esc 兜底回调抛出了异常: {error}")

    def test_capture_hints_explain_left_and_right_capture_flows(self):
        from config.config_manager import DEFAULTS
        from screenshot.hint_items import hint_texts

        settings = dict(DEFAULTS)
        initial = hint_texts(settings, (0, 0), None)
        self.assertIn("左拖松开按设置", initial["select"])
        self.assertIn("UIA点击快编", initial["select"])
        self.assertIn("右拖追加", initial["select"])
        self.assertIn("S快速保存", initial["save"])
        self.assertIn("右键双击直存", initial["save"])
        collecting = hint_texts(settings, (0, 0), (20, 15), right_capture=True)
        self.assertIn("Enter/双击确认进入编辑", collecting["edit"])
        self.assertIn("右键继续框选", collecting["multi_select"])
        self.assertNotIn("右键双击直存", collecting["save"])

    def test_inline_hints_only_show_supported_actions(self):
        """原地编辑提示只发当前工具实际支持的动作：二次编辑要选择工具，快速贴图始终不发。"""
        from config.config_manager import DEFAULTS
        from screenshot.hint_items import hint_texts

        base = dict(DEFAULTS, capture_quick_sticker_enabled=True,
                    capture_quick_sticker_shortcut="Space")
        select_hints = hint_texts(base, (10, 20), (80, 60), inline=True,
                                  inline_tool="select")
        pen_hints = hint_texts(base, (10, 20), (80, 60), inline=True,
                               inline_tool="pen")
        # 二次编辑/删除只在选择工具下直接支持
        self.assertIn("双击文字编辑", select_hints["inline_edit"])
        self.assertEqual(pen_hints["inline_edit"], "")
        # 右键菜单与撤销在两种工具下都支持
        self.assertTrue(select_hints["inline_menu"])
        self.assertTrue(pen_hints["inline_menu"])
        self.assertTrue(select_hints["inline_history"])
        self.assertTrue(pen_hints["inline_history"])
        # 快速贴图快捷键在原地编辑里被禁用，不提示；选区阶段仍提示
        self.assertEqual(select_hints["quick_sticker"], "")
        self.assertEqual(pen_hints["quick_sticker"], "")
        self.assertIn("贴图", hint_texts(base, (10, 20), (80, 60))["quick_sticker"])

    def test_info_bar_flips_with_magnifier_and_never_covers_cursor(self):
        """贴下沿时提示条跟着放大镜翻到上方，且不横跨光标位置。"""
        from PySide6.QtCore import QPoint, QRect
        from PySide6.QtGui import QFont, QFontMetrics
        from screenshot.magnifier_widget import magnifier_rect
        from screenshot.overlay_info import info_bar_layout

        metrics = QFontMetrics(QFont())
        area = QRect(0, 0, 600, 400)
        items = ["20, 20  200 x 100", "拖动移动", "Esc取消"]
        # 光标贴近下沿：放大镜自身已翻到光标上方（magnifier_rect 的既有行为）。
        cursor = (200, area.bottom() - 20)
        anchor = magnifier_rect(QPoint(*cursor), area, 140)
        self.assertLess(anchor.bottom(), cursor[1])
        bar, _rows, _align = info_bar_layout(metrics, area, anchor, items, cursor=cursor)
        # 提示条也在光标上方，且与放大镜不重叠——两条同侧，不会盖住光标中心。
        self.assertLessEqual(bar.bottom(), cursor[1])
        self.assertFalse(bar.intersects(anchor))
        self.assertLessEqual(bar.bottom(), anchor.top())
        # 光标在上半屏时仍贴在放大镜下方，同样不横跨光标。
        upper_cursor = (200, 60)
        upper_anchor = magnifier_rect(QPoint(*upper_cursor), area, 140)
        upper_bar, _rows2, _ = info_bar_layout(metrics, area, upper_anchor, items,
                                               cursor=upper_cursor)
        self.assertGreaterEqual(upper_bar.top(), upper_cursor[1])
        self.assertFalse(upper_bar.intersects(upper_anchor))

    def test_hint_per_line_controls_items_per_row(self):
        """「每行提示数」：按数量强制换行，0 表示只按宽度自动换行。"""
        from PySide6.QtCore import QRect
        from PySide6.QtGui import QFont, QFontMetrics
        from screenshot.overlay_info import info_bar_layout

        metrics = QFontMetrics(QFont())
        area = QRect(0, 0, 900, 400)
        anchor = QRect(400, 60, 140, 140)
        items = ["甲", "乙", "丙", "丁"]
        # 每行两个：恰好两行，且每行只有一个分隔符。
        bar, rows, _align = info_bar_layout(metrics, area, anchor, items, per_line=2)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0].count(" | "), 1)
        self.assertEqual(rows[1].count(" | "), 1)
        # 每行一个：四行。
        _bar, rows_one, _ = info_bar_layout(metrics, area, anchor, items, per_line=1)
        self.assertEqual(len(rows_one), 4)
        # 0（不限制）时宽度足够，全部挤在一行。
        _bar, rows_auto, _ = info_bar_layout(metrics, area, anchor, items, per_line=0)
        self.assertEqual(len(rows_auto), 1)
        # 行数变多时提示条变高（同一块区域内）。
        tall = info_bar_layout(metrics, area, anchor, items, per_line=1)[0]
        short = info_bar_layout(metrics, area, anchor, items, per_line=0)[0]
        self.assertGreater(tall.height(), short.height())

    def test_hint_order_buttons_move_items(self):
        """顺序既支持拖动，也支持选中后上移/下移按钮。"""
        from config.config_manager import HINT_ITEM_IDS, HINT_LABELS
        from ui.widgets.hint_order_list import HintOrderList

        widget = HintOrderList(HINT_ITEM_IDS, HINT_LABELS)
        self.assertTrue(widget.toolTip().strip())
        # 未选中时两个按钮都不可用。
        widget.list.setCurrentRow(-1)
        self.assertFalse(widget.up_button.isEnabled())
        self.assertFalse(widget.down_button.isEnabled())
        # 选中第二项后上移：顺序变化并写回值。
        widget.list.setCurrentRow(1)
        self.assertTrue(widget.up_button.isEnabled())
        widget.up_button.click()
        self.assertEqual(widget.value()[0], HINT_ITEM_IDS[1])
        self.assertEqual(widget.list.currentRow(), 0)
        # 首项时上移不可用，下移可用；下移后回到原位。
        self.assertFalse(widget.up_button.isEnabled())
        widget.down_button.click()
        self.assertEqual(widget.value()[0], HINT_ITEM_IDS[0])
        # 末项时下移不可用。
        widget.list.setCurrentRow(widget.count() - 1)
        self.assertFalse(widget.down_button.isEnabled())

    def test_capture_hints_master_switch_and_master_reset(self):
        """「显示快捷键提示」总开关：关掉后整条不显示，放大镜等其它辅助不受影响。"""
        from config.config_manager import DEFAULTS
        from screenshot.hint_items import hint_items

        settings = dict(DEFAULTS, crosshair=False, magnifier=True)
        items = lambda: [item for item in hint_items(settings, (10, 20), (80, 60)) if item]
        self.assertTrue(items())
        settings["capture_hints_enabled"] = False
        self.assertEqual(items(), [])
        # 总开关只影响提示条，放大镜开关不变。
        self.assertTrue(settings["magnifier"])

    def test_hint_preview_tracks_latest_keys_and_switches(self):
        """设置页预览：改键后立刻显示新键位；关掉提示/放大镜后对应部分消失。"""
        from PySide6.QtGui import QPainter, QImage
        from config.config_manager import DEFAULTS
        from screenshot.hint_items import hint_items
        from types import SimpleNamespace
        from ui.widgets.hint_preview import HintBarPreview

        config = SimpleNamespace(data=dict(DEFAULTS, capture_recapture_shortcut="R"))
        preview = HintBarPreview(config)
        self.assertEqual(preview.kind, "hints")
        preview.resize(320, 120)
        # 预览必须能整体绘制出来（关掉/开启各画一次，确保 paintEvent 不抛错）。
        for data in ({"magnifier": True}, {"magnifier": False},
                     {"capture_hints_enabled": False}):
            config.data.update(data)
            image = QImage(320, 120, QImage.Format_ARGB32)
            image.fill(Qt.white)
            preview.render(image)
            self.assertEqual(image.size(), preview.size())
        # 预览不持有键位快照：每次都读当前配置，因此改键后显示的是新键位。
        config.data["capture_hints_enabled"] = True
        config.data["capture_recapture_shortcut"] = "F9"
        text = " ".join(hint_items(config.data, (10, 20), (300, 200)))
        self.assertIn("F9 重新截图", text)
        config.data["capture_hints_enabled"] = False
        self.assertEqual([item for item in hint_items(config.data, (10, 20), (300, 200))
                          if item], [])

    def test_hint_order_list_toggles_and_reorders(self):
        """设置页提示项列表：勾选即开关，拖动即顺序，值可直接写回配置。"""
        from PySide6.QtCore import QModelIndex
        from config.config_manager import HINT_ITEM_IDS, HINT_LABELS
        from ui.widgets.hint_order_list import HintOrderList

        widget = HintOrderList(HINT_ITEM_IDS, HINT_LABELS)
        self.assertEqual(widget.value(), list(HINT_ITEM_IDS))
        # 取消勾选第一项后，它不再出现在值里（界面行仍在原处）。
        widget.item(0).setCheckState(Qt.Unchecked)
        self.assertEqual(widget.value(), list(HINT_ITEM_IDS[1:]))
        self.assertEqual(widget.count(), len(HINT_ITEM_IDS))
        # 重新勾选后按当前行位置参与排序。
        widget.item(0).setCheckState(Qt.Checked)
        self.assertEqual(widget.value(), list(HINT_ITEM_IDS))
        # 拖动排序：把末项移到首位，值顺序随之变化。
        widget.model().moveRow(QModelIndex(), len(HINT_ITEM_IDS) - 1, QModelIndex(), 0)
        self.assertEqual(widget.value()[0], HINT_ITEM_IDS[-1])
        # set_value 恢复配置顺序与勾选，未勾选的项留在界面上。
        widget.set_value(["copy", "coords"])
        self.assertEqual(widget.value(), ["copy", "coords"])
        self.assertEqual(widget.count(), len(HINT_ITEM_IDS))
        unchecked = [widget.item(i) for i in range(widget.count())
                     if widget.item(i).checkState() == Qt.Unchecked]
        self.assertEqual(len(unchecked), len(HINT_ITEM_IDS) - 2)

    def test_inline_region_reset_keeps_rounded_preview(self):
        from config.config_manager import DEFAULTS
        from screenshot.mask_window import MaskWindow

        with tempfile.TemporaryDirectory() as folder:
            bounds = {"left": 0, "top": 0, "width": 100, "height": 80}
            settings = dict(DEFAULTS, save_dir=folder, inline_edit=True,
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
            editor.reset_region(QRect(5, 7, 70, 60),
                                Image.new("RGB", (70, 60), "#d02020"), None, False)
            self.assertEqual(editor.last_path, Path(folder) / "already-saved.png")
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

    def test_shape_cursors_follow_stroke_fill_color_and_opacity(self):
        from config.config_manager import DEFAULTS

        settings = dict(DEFAULTS, rect_color="#ff0000", rect_fill_enabled=True,
                        rect_fill_color="#00ff00", rect_fill_opacity=50,
                        ellipse_color="#0000ff", ellipse_fill_enabled=False)
        canvas = AnnotationCanvas(Image.new("RGB", (120, 100), "white"), settings)
        try:
            for tool, expected_fill in (("rect", (0, 255, 0)),
                                        ("ellipse", None)):
                canvas.set_tool(tool)
                cursor_image = canvas.cursor().pixmap().toImage()
                fill_pixel = cursor_image.pixelColor(18, 15)
                if expected_fill is not None:
                    self.assertEqual((fill_pixel.red(), fill_pixel.green(), fill_pixel.blue()),
                                     expected_fill)
                    self.assertGreater(fill_pixel.alpha(), 110)
                    self.assertLess(fill_pixel.alpha(), 145)
                else:
                    self.assertEqual(fill_pixel.alpha(), 0)
                outline = cursor_image.pixelColor(9, 15)
                self.assertGreater(outline.alpha(), 0)
                self.assertNotEqual(outline.name(), "#000000")
        finally:
            canvas.close()

    def test_capture_cursor_variants_share_one_frame(self):
        from core.screen_capture import capture
        bounds = {"left": -10, "top": 0, "width": 30, "height": 20}
        left = {"left": -10, "top": 0, "width": 10, "height": 20}
        right = {"left": 10, "top": 0, "width": 10, "height": 20}
        left_shot = Mock(size=(10, 20), rgb=Image.new("RGB", (10, 20), "red").tobytes())
        right_shot = Mock(size=(10, 20), rgb=Image.new("RGB", (10, 20), "blue").tobytes())
        with patch("core.screen_capture.mss.mss") as factory, patch(
            "core.screen_capture.native_cursor", return_value=(
                Image.new("RGBA", (2, 2), (0, 255, 0, 255)), -5, 5)):
            grabber = factory.return_value.__enter__.return_value
            grabber.monitors = [bounds, left, right]
            grabber.grab.side_effect = [left_shot, right_shot]
            plain, _, _, with_cursor = capture(False, alternatives=True)
            self.assertEqual(grabber.grab.call_count, 2)
            self.assertNotEqual(plain.tobytes(), with_cursor.tobytes())
            self.assertEqual(plain.getpixel((0, 0)), (255, 0, 0, 255))
            self.assertEqual(plain.getpixel((15, 0)), (0, 0, 0, 0))
            self.assertEqual(plain.getpixel((25, 0)), (0, 0, 255, 255))
            self.assertEqual(with_cursor.getpixel((5, 5)), (0, 255, 0, 255))
            for gap_fill, expected in (("transparent", (0, 0, 0, 0)),
                                       ("black", (0, 0, 0, 255)),
                                       ("white", (255, 255, 255, 255))):
                grabber.grab.side_effect = [left_shot, right_shot]
                image, _, _, _ = capture(False, alternatives=True, gap_fill=gap_fill)
                # 纯色间隙用 RGB 画布（省内存、贴屏更快），只有透明间隙需要 RGBA；
                # 因此这里按模式只比较颜色通道，并顺带锁住模式本身。
                self.assertEqual(image.getpixel((15, 0))[:3], expected[:3], gap_fill)
                self.assertEqual(image.mode,
                                 "RGBA" if gap_fill == "transparent" else "RGB", gap_fill)
                if gap_fill == "transparent":
                    from PySide6.QtGui import QImage
                    from config.config_manager import DEFAULTS
                    from core.image_io import save_image
                    from editor.annotation_canvas import AnnotationCanvas

                    with tempfile.TemporaryDirectory() as folder:
                        path = Path(folder) / "gap.png"
                        canvas = AnnotationCanvas(image, dict(DEFAULTS))
                        try:
                            frame = canvas.render_image()
                            self.assertEqual(frame.pixelColor(15, 0).alpha(), 0)
                            self.assertTrue(save_image(frame, path, {"save_format": "png"}))
                            reopened = QImage(str(path))
                            self.assertEqual(reopened.pixelColor(15, 0).alpha(), 0)
                        finally:
                            canvas.close()

    def test_capture_preview_preserves_pixels_after_source_changes(self):
        from core.screen_capture import to_qimage
        for mode, color in (("RGB", (17, 89, 203)), ("RGBA", (17, 89, 203, 120))):
            source = Image.new(mode, (3, 2), color)
            preview = to_qimage(source)
            source.paste((0,) * len(color), (0, 0, 3, 2))
            pixel = preview.pixelColor(2, 1)
            self.assertEqual((pixel.red(), pixel.green(), pixel.blue()), color[:3])
            self.assertEqual(pixel.alpha(), color[3] if mode == "RGBA" else 255)

    def test_preset_capture_modes_do_not_overwrite_last_region(self):
        """全屏 / 当前显示器是整屏预设，不能把“上次截图区域”覆盖成主屏整块。"""
        from config.config_manager import DEFAULTS
        from screenshot.mask_window import MaskWindow

        bounds = {"left": 0, "top": 0, "width": 120, "height": 80}
        settings = {**DEFAULTS, "crosshair": False, "magnifier": False, "sound": False}
        for mode in ("fullscreen", "monitor"):
            with patch("screenshot.mask_window.visible_windows", return_value=[]):
                preset = MaskWindow(Image.new("RGB", (120, 80), "blue"), bounds,
                                    [bounds], settings, mode=mode)
            try:
                regions = []
                preset.last_region.connect(regions.append)
                preset.complete()
                self.assertEqual(regions, [], mode)
            finally:
                preset.close()

        with patch("screenshot.mask_window.visible_windows", return_value=[]):
            free = MaskWindow(Image.new("RGB", (120, 80), "blue"), bounds,
                              [bounds], settings, mode="capture")
        try:
            regions = []
            free.last_region.connect(regions.append)
            free.selection.rects.append(QRect(10, 10, 40, 30))
            free.complete()
            self.assertEqual(regions, [[10, 10, 40, 30]])
        finally:
            free.close()

    def test_capture_does_no_preprocessing_of_own_windows(self):
        """抓屏前不动本程序的任何窗口：截图就是截图，不替用户关通知/贴图/设置。"""
        import logging
        from PySide6.QtWidgets import QWidget
        from main import Application

        app = Application.__new__(Application)
        app.mask = None
        app.config = Mock(data={})
        app.logger = logging.getLogger("screensnap")
        notice = QWidget()
        notice.show()
        app.capture_notice = notice
        tooltip = QWidget(None, Qt.ToolTip)
        tooltip.show()
        self.app.processEvents()
        # 抓屏前没有任何"自身窗口预处理"入口。
        self.assertFalse(hasattr(app, "pre_capture_self_check"))
        self.assertTrue(notice.isVisible())
        self.assertTrue(tooltip.isVisible())
        self.assertIs(app.capture_notice, notice)
        notice.close()
        tooltip.close()

    def test_mask_warns_when_selection_covers_own_window(self):
        """采集自检：选区内含本程序窗口时给出警示，移开后不再警示。"""
        from PySide6.QtWidgets import QDialog
        from config.config_manager import DEFAULTS
        from screenshot.mask_window import MaskWindow

        bounds = {"left": 0, "top": 0, "width": 200, "height": 150}
        with patch("screenshot.mask_window.visible_windows", return_value=[]):
            mask = MaskWindow(Image.new("RGB", (200, 150), "white"), bounds, [bounds],
                              dict(DEFAULTS, inline_edit=False, magnifier=False,
                                   intruder_warning_enabled=True))
        mask.show()
        self.app.processEvents()
        mask.selection.rects.append(QRect(40, 30, 100, 80))

        # 一个落在选区内的本程序对话框会被检出，并在提示里点名。
        dialog = QDialog()
        dialog.setGeometry(QRect(60, 50, 60, 40))
        dialog.show()
        self.app.processEvents()
        self.assertIn(dialog, mask.intruding_windows())
        warning = mask.self_check_warning()
        self.assertIsNotNone(warning)
        self.assertIn("本程序窗口", warning)
        self.assertIn("重截", warning)

        # 移出选区后不再算作干扰。
        dialog.setGeometry(QRect(180, 130, 18, 18))
        self.app.processEvents()
        self.assertNotIn(dialog, mask.intruding_windows())
        dialog.close()
        mask.close()

    def test_show_mask_passes_self_check_hints(self):
        """show_mask 把抓屏前记录的弹出菜单矩形交给遮罩；不再传系统通知提示。"""
        from types import SimpleNamespace
        from main import Application

        bounds = {"left": 0, "top": 0, "width": 200, "height": 150}
        app = Application.__new__(Application)
        app.mask = None
        app.config = Mock(data={"cursor": False})
        app.logger = Mock()
        app.popup_intruders = Mock(return_value=[("弹出菜单", QRect(5, 5, 10, 10))])
        for name in ("_connect_sticker_signals", "remember_region", "handle_capture_selection",
                     "edit_images", "save_capture_images", "saved", "initial_save_failed",
                     "close_all_editors", "restart_capture"):
            setattr(app, name, Mock())
        primary = Mock()
        primary.session = SimpleNamespace(views=[])
        image = Image.new("RGB", (20, 10), "white")
        with patch("app.capture_flow.capture", return_value=(image, bounds, [bounds], None)), \
                patch("main.MaskWindow", return_value=primary), \
                patch("app.capture_flow.MaskWindow", return_value=primary) as mask_cls, \
                patch("app.capture_flow.QTimer.singleShot"):
            app.show_mask("capture")
        self.assertEqual(mask_cls.call_args.kwargs["extra_intruders"],
                         [("弹出菜单", QRect(5, 5, 10, 10))])
        # 系统通知不再提示：既没有这个方法，也不会作为参数传下去。
        self.assertNotIn("notification_note", mask_cls.call_args.kwargs)
        self.assertFalse(hasattr(app, "recent_notification_note"))
        # 抓屏前不做任何自身窗口预处理。
        self.assertFalse(hasattr(app, "pre_capture_self_check"))

    def test_capture_single_selection_can_paste_after_initial_autosave(self):
        from types import SimpleNamespace
        from config.config_manager import DEFAULTS
        from main import Application

        with tempfile.TemporaryDirectory() as folder, \
                patch("sticker.sticker_manager.data_dir", return_value=Path(folder)):
            settings = dict(DEFAULTS, save_dir=folder,
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
            self.assertIsNone(editor.last_path)
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
                self.assertIsNotNone(editor.last_path)
                self.assertTrue(editor.last_path.is_file())
                self.assertFalse(app.saved.call_args.kwargs["notify"])
            finally:
                manager.close_all()
                self.app.processEvents()

    # 待桌面验收：离屏屏 800x800 而编辑器最小宽度就撑满整屏，贴图没有侧边空间可放，必然落进编辑器范围。
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
        with patch("main.MaskWindow", FakeMask), \
                patch("app.capture_flow.MaskWindow", FakeMask):
            app._activate_capture_mask()
        self.assertEqual([call[0] if isinstance(call, tuple) else call
                          for call in fake_mask.calls],
                         ["raise", "activate-window", "activate-widget", "focus"])

    def test_capture_completion_sound_is_independent_of_notification_switches(self):
        from types import SimpleNamespace
        from config.config_manager import DEFAULTS
        from main import Application

        settings = dict(DEFAULTS, bubble=False, copy_notification=False,
                sound=True)
        app = Application.__new__(Application)
        app.config = SimpleNamespace(data=settings)
        app.logger = Mock()
        app.settings_window = Mock()
        app.editors = []
        app.capture_notice = None
        with patch("main.QApplication.beep") as beep, \
                patch("app.capture_flow.EditorWindow"), patch("main.QTimer"), \
                patch("app.notification_flow.CaptureNotification") as preview:
            app.edit_images([(Image.new("RGB", (12, 8), "white"), None)])
        beep.assert_called_once()
        preview.assert_not_called()

    def test_capture_editors_open_without_saving_and_close_all(self):
        from types import SimpleNamespace
        from config.config_manager import DEFAULTS
        from main import Application

        with tempfile.TemporaryDirectory() as folder:
            app = Application.__new__(Application)
            app.config = SimpleNamespace(data=dict(DEFAULTS, save_dir=folder, filename="capture", sound=False,
                                                   bubble=False, copy_notification=False))
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
            # 进入编辑器本身不落盘：只有用户显式保存（或贴图）才写文件。
            self.assertEqual([editor.last_path for editor in app.editors], [None, None])
            self.assertEqual(list(Path(folder).glob("*.png")), [])
            app.editors[0].close_all_requested.emit()
            self.app.processEvents()
            self.assertTrue(all(not editor.isVisible() for editor in app.editors))

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

    def test_mask_double_click_enters_inline_editor(self):
        from config.config_manager import DEFAULTS
        from screenshot.mask_window import MaskWindow

        with tempfile.TemporaryDirectory() as folder:
            bounds = {"left": 0, "top": 0, "width": 120, "height": 80}
            settings = {**DEFAULTS, "save_dir": folder, "inline_edit": True,
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

    def test_mask_right_double_click_saves_existing_selection_directly(self):
        from config.config_manager import DEFAULTS
        from screenshot.mask_window import MaskWindow

        bounds = {"left": 0, "top": 0, "width": 120, "height": 80}
        settings = {**DEFAULTS, "inline_edit": True,
                    "capture_after_selection": "save",
                    "crosshair": False, "magnifier": False, "sound": False}
        with patch("screenshot.mask_window.visible_windows", return_value=[]):
            mask = MaskWindow(Image.new("RGB", (120, 80), "blue"), bounds,
                              [bounds], settings)
        saved = []
        selected = []
        mask.save_requested.connect(lambda images, positions: saved.append((images, positions)))
        mask.selected.connect(lambda images, positions: selected.append((images, positions)))
        mask.selection.rects.append(QRect(10, 10, 40, 30))
        mask.show()
        self.app.processEvents()
        QTest.mousePress(mask, Qt.RightButton, Qt.NoModifier, QPoint(20, 20))
        QTest.mouseRelease(mask, Qt.RightButton, Qt.NoModifier, QPoint(20, 20))
        QTest.mouseDClick(mask, Qt.RightButton, Qt.NoModifier, QPoint(20, 20))
        self.assertFalse(mask.isVisible())
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0][0][0][0].size, (40, 30))
        self.assertEqual(selected, [])
        self.assertIsNone(mask.session.inline_editor)
        self.assertFalse(mask.session.right_capture_mode)

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

    def test_right_confirm_cross_screen_region_uses_standalone_editor(self):
        from config.config_manager import DEFAULTS
        from screenshot.mask_window import MaskWindow

        bounds = {"left": 0, "top": 0, "width": 160, "height": 100}
        monitors = [{"left": 0, "top": 0, "width": 80, "height": 100},
                    {"left": 80, "top": 0, "width": 80, "height": 100}]
        screen_infos = [{"geometry": QRect(0, 0, 80, 100), "dpr": 1.0},
                        {"geometry": QRect(80, 0, 80, 100), "dpr": 1.0}]
        settings = {**DEFAULTS, "inline_edit": True, "capture_after_selection": "save",
                    "magnifier": False, "crosshair": False, "sound": False}
        with patch("screenshot.mask_window.visible_windows", return_value=[]), \
                patch("core.dpi.DisplayMapper.collect_screen_infos", return_value=screen_infos):
            mask = MaskWindow(Image.new("RGB", (160, 100), "blue"), bounds, monitors, settings)
        routed = []
        mask.edit_requested.connect(lambda images, positions: routed.append((images, positions)))
        mask.session.right_capture_mode = True
        mask.selection.rects.append(QRect(70, 10, 30, 30))
        mask.complete()
        self.assertEqual(len(routed), 1)
        self.assertEqual(len(routed[0][0]), 1)
        self.assertEqual(routed[0][0][0][0].size, (30, 30))
        self.assertIsNone(mask.session.inline_editor)
        self.assertFalse(mask.isVisible())

    def test_preset_capture_modes_use_independent_edit_or_save_actions(self):
        from types import SimpleNamespace
        from config.config_manager import DEFAULTS
        from screenshot.mask_window import MaskWindow

        bounds = {"left": 0, "top": 0, "width": 120, "height": 80}
        mode_actions = (("fullscreen", "capture_fullscreen_action", "save", "save"),
                ("monitor", "capture_monitor_action", "save", "save"),
                        ("repeat", "capture_repeat_action", "save", "save"))
        for mode, setting, action, expected_signal in mode_actions:
            with self.subTest(mode=mode):
                settings = {**DEFAULTS, "inline_edit": False, "capture_after_selection": "edit",
                            setting: action, "magnifier": False, "crosshair": False}
                with patch("screenshot.mask_window.visible_windows", return_value=[]):
                    mask = MaskWindow(Image.new("RGB", (120, 80), "blue"), bounds,
                                      [bounds], settings, mode=mode)
                self.assertEqual(mask.auto_complete_after_show,
                                 mode in ("fullscreen", "monitor"))
                saved, edited = [], []
                mask.save_requested.connect(lambda images, positions: saved.append(images))
                mask.selected.connect(lambda images, positions: edited.append(images))
                mask.selection.rects.append(QRect(10, 12, 36, 24))
                mask.complete()
                self.assertEqual(bool(saved), expected_signal == "save")
                self.assertEqual(bool(edited), expected_signal == "edit")
                mask.close()

            settings = {**DEFAULTS, "inline_edit": True,
                    "capture_monitor_action": "edit", "magnifier": False,
                    "crosshair": False}
            with patch("screenshot.mask_window.visible_windows", return_value=[]):
                monitor_mask = MaskWindow(Image.new("RGB", (120, 80), "blue"), bounds,
                              [bounds], settings, mode="monitor")
            selected = []
            monitor_mask.selected.connect(
                lambda images, positions: selected.append((images, positions)))
            monitor_mask.complete()
            self.assertEqual(len(selected), 1)
            self.assertEqual(len(selected[0][0]), 1)
            self.assertIsNone(monitor_mask.session.inline_editor)
            monitor_mask.close()

            from main import Application
            app = SimpleNamespace(config=SimpleNamespace(data={**settings}),
                                  edit_images=Mock(), save_capture_images=Mock())
            Application.handle_capture_selection(
                app, selected[0][0], selected[0][1], mode="monitor")
            app.edit_images.assert_called_once_with(selected[0][0], positions=selected[0][1])
            app.save_capture_images.assert_not_called()

    def test_capture_editor_opens_without_saving(self):
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
                self.copy_done = Mock()          # 应用接线：仅复制通知
                self.save_as_dir_chosen = Mock()  # 应用接线：记住另存为目录

            def setAttribute(self, key, value=True):
                self.attributes[key] = value

            def show(self):
                events.append("show")

            def save(self, **kwargs):
                events.append("save")
                self.save_calls.append(kwargs)
                self.image_saved.emit("capture.png", Image.new("RGB", (4, 4), "red"))

        app = Application.__new__(Application)
        app.config = SimpleNamespace(data={"bubble": False, "copy_notification": False,
                                           "sound": False})
        app.settings_window = SimpleNamespace(set_tool_color=Mock(), set_annotation_setting=Mock())
        app.editors = []
        app.logger = SimpleNamespace(info=Mock())
        app.capture_notice = None
        app.saved = Mock()
        image = Image.new("RGB", (4, 4), "red")
        with patch("app.capture_flow.EditorWindow", FakeEditor):
            Application.edit_images(app, [(image, None)], from_capture=True)
        editor = app.editors[0]
        self.assertEqual(events, ["show"])
        self.assertEqual(editor.save_calls, [])
        app.saved.assert_not_called()

    def test_capture_editor_saves_only_after_explicit_action(self):
        from types import SimpleNamespace
        from PySide6.QtCore import QSize
        from config.config_manager import DEFAULTS
        from main import Application

        with tempfile.TemporaryDirectory() as folder:
            settings = {**DEFAULTS, "save_dir": folder, "bubble": False,
                        "copy_notification": False, "sound": False}
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
                self.assertIsNone(editor.last_path)
                self.assertEqual(list(Path(folder).iterdir()), [])
                app.saved.assert_not_called()
                saved_path = editor.save()
                self.assertEqual(saved_path, editor.last_path)
                self.assertTrue(saved_path.is_file())
                app.saved.assert_called_once()
                self.assertEqual(app.saved.call_args.args[0], str(editor.last_path))
                self.assertEqual(app.saved.call_args.args[1].size(), QSize(8, 8))
                self.assertTrue(app.saved.call_args.kwargs["notify"])
                app.initial_save_failed.assert_not_called()
            finally:
                editor.close()

    def test_mask_inline_cursor_switch_and_toolbar_repositions_after_resize(self):
        from PySide6.QtWidgets import QStyle, QStyleOptionButton
        from config.config_manager import DEFAULTS
        from screenshot.mask_window import MaskWindow
        from ui.theme import apply_theme

        # 应用主题样式表：工具栏按钮固定 24px，主题的指示器是 22px（居中差 1px）；
        # 不套样式表时是默认 14px 指示器靠左，差值 5px。这个用例单独跑也必须成立。
        apply_theme(self.app, "dark")

        with tempfile.TemporaryDirectory() as folder:
            bounds = {"left": 0, "top": 0, "width": 1200, "height": 900}
            settings = {**DEFAULTS, "save_dir": folder, "filename": "inline", "inline_edit": True,
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
                self.assertEqual(button.width(), 24)
                self.assertEqual(button.height(), 24)
            output = editor.toolbar.output_grid
            first_row = [output.itemAtPosition(0, column).widget()
                         for column in range(output.columnCount()) if output.itemAtPosition(0, column)]
            self.assertEqual(len({button.y() for button in first_row}), 1)
            tools = editor.toolbar.tool_grid
            for column in range(tools.columnCount()):
                widgets = [tools.itemAtPosition(row, column).widget()
                           for row in range(tools.rowCount()) if tools.itemAtPosition(row, column)]
                self.assertEqual(len({button.x() for button in widgets}), 1)

    def test_mask_inline_toolbar_avoids_selection_when_space_allows(self):
        from config.config_manager import DEFAULTS
        from screenshot.mask_window import MaskWindow

        with tempfile.TemporaryDirectory() as folder:
            bounds = {"left": 0, "top": 0, "width": 1200, "height": 900}
            settings = {**DEFAULTS, "save_dir": folder, "filename": "inline", "inline_edit": True,
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

    def test_info_bar_is_overlay_above_inline_canvas_and_follows_magnifier(self):
        """提示条是遮罩的子控件并抬在编辑画布之上：光标进选区也不会被内容盖住。"""
        from config.config_manager import (DEFAULTS, HINT_BAR_FILL_ALPHA,
                                           HINT_BAR_WARNING_FILL_ALPHA)
        from screenshot.mask_window import InfoBar, MaskWindow

        bounds = {"left": 0, "top": 0, "width": 900, "height": 700}
        settings = {**DEFAULTS, "save_dir": tempfile.mkdtemp(), "inline_edit": True,
                    "crosshair": False, "magnifier": True, "mask_opacity": 0,
                    "capture_after_selection": "edit", "sound": False}
        with patch("screenshot.mask_window.visible_windows", return_value=[]):
            mask = MaskWindow(Image.new("RGB", (900, 700), "blue"), bounds, [bounds], settings)
        mask.selection.rects.append(QRect(300, 300, 200, 150))
        mask.complete()
        mask.show()
        self.app.processEvents()
        editor = mask.session.inline_editor
        bar = mask.info_bar
        self.assertIsInstance(bar, InfoBar)
        self.assertIs(bar.parent(), mask)
        self.assertTrue(bar.testAttribute(Qt.WA_TransparentForMouseEvents))
        # 光标移到选区内部（原地编辑画布所在处）：提示条仍在，且被抬到画布之上。
        mask.position = QPoint(400, 380)
        mask.update_all()
        self.assertFalse(bar.bar.isEmpty())
        self.assertTrue(bar.isVisible())
        children = mask.children()
        self.assertGreater(children.index(bar), children.index(editor.canvas))
        # 位置跟着放大镜：换光标位置后条的位置随之变化。
        first = QRect(bar.geometry())
        mask.position = QPoint(60, 60)
        mask.update_all()
        self.assertNotEqual(first, bar.geometry())
        # 无警示时用普通样式；出现采集自检警示时整条换成警示样式（附绘制期 alpha）。
        self.assertEqual(bar.style,
                         {**settings["hint_bar_style"], "alpha": HINT_BAR_FILL_ALPHA})
        with patch.object(mask, "self_check_warning", return_value="选区内有贴图"):
            mask.update_all()
        self.assertEqual(bar.style,
                         {**settings["hint_bar_warning_style"],
                          "alpha": HINT_BAR_WARNING_FILL_ALPHA})
        # 关掉总开关后整条消失。
        settings["capture_hints_enabled"] = False
        mask.update_all()
        self.assertFalse(bar.isVisible())
        mask.close()

    def test_inline_editor_copy_only_copies_without_saving_and_closes_capture(self):
        from config.config_manager import DEFAULTS
        from PySide6.QtGui import QImage
        from screenshot.mask_window import MaskWindow

        bounds = {"left": 0, "top": 0, "width": 100, "height": 80}
        settings = dict(DEFAULTS, inline_edit=True, capture_after_selection="edit",
                        crosshair=False, magnifier=False, sound=False)
        with patch("screenshot.mask_window.visible_windows", return_value=[]):
            mask = MaskWindow(Image.new("RGB", (100, 80), "red"), bounds,
                              [bounds], settings)
        mask.selection.rects.append(QRect(10, 10, 60, 50))
        mask.complete()
        editor = mask.session.inline_editor
        clipboard = Mock()
        try:
            mask.show()
            self.app.processEvents()
            with patch("screenshot.mask_window.QGuiApplication.clipboard",
                       return_value=clipboard), \
                    patch.object(editor, "save") as save:
                button = next(button for button, action in editor.toolbar.command_buttons
                              if action == "copy_only")
                button.click()

            clipboard.setMimeData.assert_called_once()
            payload = clipboard.setMimeData.call_args.args[0]
            self.assertTrue(payload.hasImage())
            save.assert_not_called()
            self.assertFalse(mask.isVisible())
            self.assertIsNone(editor.last_path)
        finally:
            mask.close()

    def test_inline_escape_from_canvas_or_toolbar_closes_capture(self):
        from config.config_manager import DEFAULTS
        from screenshot.mask_window import MaskWindow

        with tempfile.TemporaryDirectory() as folder:
            bounds = {"left": 0, "top": 0, "width": 500, "height": 400}
            settings = {**DEFAULTS, "save_dir": folder, "inline_edit": True,
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

    def test_mosaic_cursor_ring_follows_pointer_and_width(self):
        from config.config_manager import DEFAULTS
        from editor.annotation_canvas import AnnotationCanvas
        from PIL import Image
        from PySide6.QtCore import QEvent, QPointF, Qt
        from PySide6.QtGui import QMouseEvent

        canvas = AnnotationCanvas(Image.new("RGB", (200, 200), "white"),
                                  dict(DEFAULTS, mosaic_brush=True, mosaic_width=24))
        canvas.resize(300, 260)
        canvas.show()
        canvas.tool = "mosaic"
        self.assertIsNone(canvas.mosaic_point)
        viewport_point = canvas.mapFromScene(QPointF(100, 100))
        canvas.mouseMoveEvent(QMouseEvent(
            QEvent.MouseMove, QPointF(viewport_point),
            QPointF(canvas.viewport().mapToGlobal(viewport_point)),
            Qt.NoButton, Qt.NoButton, Qt.NoModifier))
        self.assertIsNotNone(canvas.mosaic_point)
        self.assertAlmostEqual(canvas.mosaic_point.x(), 100, delta=1)
        self.assertAlmostEqual(canvas.mosaic_point.y(), 100, delta=1)
        # 光标圆环半径取笔刷宽度的一半。
        self.assertAlmostEqual(
            max(2, canvas.settings.get("mosaic_width", 20) / 2), 12, delta=0.01)
        canvas.close()

    def test_single_selection_rotation_handle_rotates_and_round_trips(self):
        from config.config_manager import DEFAULTS
        from editor.annotation_items import shape
        from PySide6.QtCore import QEvent, QPointF, Qt
        from PySide6.QtGui import QMouseEvent
        canvas = AnnotationCanvas(Image.new("RGB", (200, 200), "white"), dict(DEFAULTS))
        item = shape("rect", QPointF(80, 80), QPointF(140, 140), "#ff0000", 3)
        canvas.scene_data.addItem(item)
        item.setSelected(True)
        canvas.checkpoint()
        handle = canvas.rotation_handle_position(item)
        canvas.show()
        self.app.processEvents()
        handle_position = canvas.mapFromScene(handle)
        canvas._update_resize_cursor(handle_position)
        rotation_cursor = canvas.cursor()
        self.assertFalse(rotation_cursor.pixmap().isNull())
        canvas._begin_rotation(item, handle)
        self.assertFalse(canvas.cursor().pixmap().isNull())
        # 旋转按钮在图形中心：第一次移动确定起始方向，第二次才产生角度。
        canvas._rotate_to(QPointF(handle.x() + 40, handle.y()), snap=False)
        canvas._rotate_to(QPointF(handle.x(), handle.y() + 40), snap=False)
        self.assertGreater(abs(item.rotation()), 30)
        outside = canvas.mapFromScene(QPointF(10, 10))
        QTest.mouseRelease(canvas.viewport(), Qt.LeftButton, pos=outside)
        self.assertIsNone(canvas.rotating)
        self.assertEqual(canvas.cursor().shape(), Qt.ArrowCursor)
        canvas.undo()
        self.assertEqual(len(canvas.annotations()), 1)
        self.assertAlmostEqual(canvas.annotations()[0].rotation(), 0, delta=0.5)
        canvas.redo()
        self.assertEqual(len(canvas.annotations()), 1)
        self.assertGreater(abs(canvas.annotations()[0].rotation()), 30)
        canvas.close()

    def test_rotation_keeps_position_and_handle_cursors_follow_angle(self):
        """旋转支点切换不跳位；旋转后四角/边中点手柄的光标方向随角度换算。"""
        from config.config_manager import DEFAULTS
        from editor.annotation_items import shape

        canvas = AnnotationCanvas(Image.new("RGB", (300, 220), "white"), dict(DEFAULTS))
        canvas.resize(400, 320)
        canvas.show()
        try:
            item = shape("rect", QPointF(80, 60), QPointF(180, 140), "#ff0000", 2)
            canvas.scene_data.addItem(item)
            item.setSelected(True)
            item.setRotation(30)
            # 旋转按钮在图形中心：随图形旋转、始终落在图形内部。
            center_scene = item.mapToScene(item.boundingRect().center())
            handle = canvas.rotation_handle_position(item)
            self.assertAlmostEqual(handle.x(), center_scene.x(), delta=0.01)
            self.assertAlmostEqual(handle.y(), center_scene.y(), delta=0.01)
            self.assertTrue(item.contains(item.mapFromScene(handle)))
            # 模拟“刚缩放过”：旋转支点停在某个角而不是中心，按下旋转手柄不应跳位。
            item.setTransformOriginPoint(item.boundingRect().topLeft())
            before = item.sceneBoundingRect()
            canvas._begin_rotation(item, canvas.rotation_handle_position(item))
            after = item.sceneBoundingRect()
            for name, got, want in (("x", after.x(), before.x()), ("y", after.y(), before.y()),
                                    ("w", after.width(), before.width()),
                                    ("h", after.height(), before.height())):
                self.assertAlmostEqual(got, want, delta=0.01, msg=name)
            canvas.rotating = None

            for rotation, cursors in (
                    (0, {"e": Qt.SizeHorCursor, "n": Qt.SizeVerCursor,
                         "se": Qt.SizeFDiagCursor, "ne": Qt.SizeBDiagCursor}),
                    (90, {"e": Qt.SizeVerCursor, "n": Qt.SizeHorCursor,
                          "se": Qt.SizeBDiagCursor, "ne": Qt.SizeFDiagCursor})):
                item.setRotation(rotation)
                for handle, expected_cursor in cursors.items():
                    self.assertEqual(canvas._rotated_handle_cursor(handle, item),
                                     expected_cursor, (rotation, handle))
        finally:
            canvas.close()

    def test_eraser_non_destructive_masks_pixmap_annotation(self):
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
        self.assertIs(canvas.annotations()[0], item)
        QTest.mouseClick(canvas.viewport(), Qt.LeftButton, pos=canvas.mapFromScene(QPointF(40, 40)))
        self.assertIsNotNone(canvas.eraser_point)
        # 非破坏：标注项仍是原对象，像素未被改写。
        self.assertIs(canvas.annotations()[0], item)
        self.assertEqual(canvas.annotations()[0].pixmap().toImage().pixelColor(20, 20).alpha(), 255)
        # 渲染时擦除处露出底图（白），未擦处仍为红。
        self.assertEqual(canvas.render_image().pixelColor(40, 40).name(), "#ffffff")
        self.assertEqual(canvas.render_image().pixelColor(60, 60).name(), "#ff0000")
        canvas.undo()
        self.assertEqual(canvas.render_image().pixelColor(40, 40).name(), "#ff0000")
        canvas.close()

    def test_mosaic_brush_does_not_show_selection_rectangle(self):
        from PySide6.QtGui import QPainter, QImage
        from config.config_manager import DEFAULTS

        settings = dict(DEFAULTS)
        canvas = AnnotationCanvas(Image.new("RGB", (100, 80), "white"), settings)
        canvas.tool = "mosaic"
        canvas.start = QPointF(10, 10)
        canvas.preview_end = QPointF(80, 60)

        def render_foreground():
            image = QImage(100, 80, QImage.Format_ARGB32)
            image.fill(Qt.transparent)
            painter = QPainter(image)
            try:
                canvas.drawForeground(painter, QRectF(0, 0, 100, 80))
            finally:
                painter.end()
            return image

        settings["mosaic_brush"] = True
        brush_preview = render_foreground()
        self.assertEqual(brush_preview.pixelColor(20, 45).alpha(), 0)

        settings["mosaic_brush"] = False
        rectangle_preview = render_foreground()
        self.assertGreater(rectangle_preview.pixelColor(20, 45).alpha(), 0)
        canvas.close()

    def test_picker_tool_uses_custom_dropper_cursor(self):
        from config.config_manager import DEFAULTS

        canvas = AnnotationCanvas(Image.new("RGB", (120, 100), "white"), DEFAULTS)
        try:
            canvas.set_tool("picker")
            self.assertEqual(canvas.cursor().shape(), Qt.BitmapCursor)
            self.assertEqual(canvas.cursor().hotSpot(), QPoint(28, 28))
            self.assertNotEqual(canvas._picker_cursor.pixmap().toImage(),
                                canvas._picker_pressed_cursor.pixmap().toImage())
            canvas.set_tool("pen")
            self.assertEqual(canvas.cursor().shape(), Qt.BitmapCursor)
            self.assertEqual(canvas.cursor().hotSpot(), QPoint(5, 27))
        finally:
            canvas.close()

    def test_eraser_and_mosaic_use_circle_only_cursor(self):
        from config.config_manager import DEFAULTS

        canvas = AnnotationCanvas(Image.new("RGB", (120, 100), "white"), dict(DEFAULTS))
        try:
            for tool in ("eraser", "mosaic"):
                canvas.set_tool(tool)
                self.assertEqual(canvas.cursor().shape(), Qt.BlankCursor)
                self.assertRegex(canvas.settings[f"{tool}_cursor_color"], r"^#[0-9a-f]{6}$")
        finally:
            canvas.close()

    def test_drawing_tools_use_tool_glyph_cursor_with_hotspot(self):
        from config.config_manager import DEFAULTS
        from editor.annotation_canvas import TOOL_CURSOR_HOTSPOTS

        canvas = AnnotationCanvas(Image.new("RGB", (120, 100), "white"), DEFAULTS)
        try:
            for tool, hotspot in TOOL_CURSOR_HOTSPOTS.items():
                canvas.set_tool(tool)
                self.assertEqual(canvas.cursor().shape(), Qt.BitmapCursor, tool)
                self.assertEqual(canvas.cursor().hotSpot(), QPoint(*hotspot), tool)
                self.assertFalse(canvas.cursor().pixmap().isNull(), tool)
            # 其余工具不占用字形光标，回退为系统箭头。
            canvas.set_tool("select")
            self.assertEqual(canvas.cursor().shape(), Qt.ArrowCursor)
        finally:
            canvas.close()

    def test_tool_cursor_pressed_state_on_press_and_release(self):
        from config.config_manager import DEFAULTS
        from PySide6.QtCore import QEvent
        from PySide6.QtGui import QMouseEvent

        canvas = AnnotationCanvas(Image.new("RGB", (120, 100), "white"), DEFAULTS)
        try:
            def pixels(cursor):
                return bytes(cursor.pixmap().toImage().constBits())

            for tool in ("pen", "picker"):
                canvas.set_tool(tool)
                normal = canvas.cursor()
                self.assertFalse(canvas._left_button_down)
                canvas.mousePressEvent(QMouseEvent(
                    QEvent.MouseButtonPress, QPointF(20, 20), QPointF(20, 20),
                    Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))
                self.assertTrue(canvas._left_button_down, tool)
                pressed = canvas.cursor()
                self.assertNotEqual(pixels(pressed), pixels(normal), tool)
                self.assertEqual(pressed.hotSpot(), normal.hotSpot(), tool)
                canvas.mouseReleaseEvent(QMouseEvent(
                    QEvent.MouseButtonRelease, QPointF(20, 20), QPointF(20, 20),
                    Qt.LeftButton, Qt.NoButton, Qt.NoModifier))
                self.assertFalse(canvas._left_button_down, tool)
                self.assertEqual(pixels(canvas.cursor()), pixels(normal), tool)
        finally:
            canvas.close()

    def test_tool_cursor_follows_current_color(self):
        from config.config_manager import DEFAULTS

        canvas = AnnotationCanvas(Image.new("RGB", (120, 100), "white"), DEFAULTS)
        try:
            def pixels(cursor):
                return bytes(cursor.pixmap().toImage().constBits())

            canvas.set_tool("pen")
            before = pixels(canvas.cursor())
            canvas.settings["pen_color"] = "#00ff00"
            canvas.refresh_tool_cursor()
            self.assertNotEqual(pixels(canvas.cursor()), before)
            # 非当前工具的取色项变化不应影响当前光标。
            after = pixels(canvas.cursor())
            canvas.settings["rect_color"] = "#0000ff"
            canvas.refresh_tool_cursor()
            self.assertEqual(pixels(canvas.cursor()), after)
        finally:
            canvas.close()

    def test_text_tool_cursor_not_stuck_after_dialog(self):
        from config.config_manager import DEFAULTS
        from PySide6.QtCore import QEvent
        from PySide6.QtGui import QMouseEvent

        canvas = AnnotationCanvas(Image.new("RGB", (120, 100), "white"), DEFAULTS)
        try:
            canvas.set_tool("text")
            canvas.input_text = Mock(return_value=(None, {}, False))
            canvas.mousePressEvent(QMouseEvent(
                QEvent.MouseButtonPress, QPointF(20, 20), QPointF(20, 20),
                Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))
            # 文字工具点击即弹对话框，不进入按下态，关闭后光标仍是常规字形。
            self.assertFalse(canvas._left_button_down)
            self.assertEqual(canvas.cursor().shape(), Qt.BitmapCursor)
            self.assertEqual(canvas.cursor().hotSpot(), QPoint(7, 25))
        finally:
            canvas.close()

    def test_tool_cursor_restored_after_right_pan_and_outside_scene(self):
        from config.config_manager import DEFAULTS
        from PySide6.QtCore import QEvent
        from PySide6.QtGui import QMouseEvent

        canvas = AnnotationCanvas(Image.new("RGB", (120, 100), "white"), DEFAULTS)
        try:
            canvas.set_tool("pen")
            # 悬停到场景外：应恢复工具字形光标，而不是被清成箭头。
            canvas._update_resize_cursor(QPoint(-50, -50))
            self.assertEqual(canvas.cursor().shape(), Qt.BitmapCursor)
            self.assertEqual(canvas.cursor().hotSpot(), QPoint(5, 27))
            # 右键平移期间临时抓手光标，松开后回到工具字形光标。
            canvas.mousePressEvent(QMouseEvent(
                QEvent.MouseButtonPress, QPointF(30, 30), QPointF(30, 30),
                Qt.RightButton, Qt.RightButton, Qt.NoModifier))
            self.assertEqual(canvas.cursor().shape(), Qt.ClosedHandCursor)
            canvas.mouseReleaseEvent(QMouseEvent(
                QEvent.MouseButtonRelease, QPointF(30, 30), QPointF(30, 30),
                Qt.RightButton, Qt.NoButton, Qt.NoModifier))
            self.assertEqual(canvas.cursor().shape(), Qt.BitmapCursor)
            self.assertEqual(canvas.cursor().hotSpot(), QPoint(5, 27))
        finally:
            canvas.close()

    def test_tool_cursor_ignores_view_zoom(self):
        from config.config_manager import DEFAULTS

        canvas = AnnotationCanvas(Image.new("RGB", (120, 100), "white"), DEFAULTS)
        try:
            canvas.set_tool("rect")
            canvas.set_zoom(250)
            # 位图与热点按逻辑像素固定，缩放视图不改变光标定位精度。
            self.assertEqual(canvas.cursor().shape(), Qt.BitmapCursor)
            self.assertEqual(canvas.cursor().hotSpot(), QPoint(6, 26))
            self.assertEqual(canvas.cursor().pixmap().size(), canvas._tool_cursor("rect").pixmap().size())
        finally:
            canvas.close()

    def test_picker_cursor_and_appearance_toggle_shared_by_both_editors(self):
        from config.config_manager import DEFAULTS
        from PySide6.QtCore import QEvent
        from PySide6.QtGui import QMouseEvent
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
                outside.setGeometry(2000, 1500, 120, 30)
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
                self.assertEqual(toolbar.appearance_panel.objectName(), "outputAppearancePanel")

            window_editor.set_tool("picker")
            inline_editor.set_tool("picker")
            for editor in (window_editor, inline_editor):
                cursor = editor.canvas.cursor()
                self.assertEqual(cursor.shape(), Qt.BitmapCursor)
                self.assertEqual(cursor.hotSpot(), QPoint(28, 28))
                self.assertFalse(cursor.pixmap().isNull())
                normal_pixels = bytes(cursor.pixmap().toImage().constBits())
                editor.canvas.mousePressEvent(QMouseEvent(
                    QEvent.MouseButtonPress, QPointF(10, 10), QPointF(10, 10),
                    Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))
                pressed = editor.canvas.cursor()
                self.assertTrue(editor.canvas._left_button_down)
                self.assertNotEqual(bytes(pressed.pixmap().toImage().constBits()),
                                    normal_pixels)
                self.assertEqual(pressed.hotSpot(), cursor.hotSpot())
                editor.canvas.mouseReleaseEvent(QMouseEvent(
                    QEvent.MouseButtonRelease, QPointF(10, 10), QPointF(10, 10),
                    Qt.LeftButton, Qt.NoButton, Qt.NoModifier))
                self.assertFalse(editor.canvas._left_button_down)
                self.assertEqual(bytes(editor.canvas.cursor().pixmap().toImage().constBits()),
                                 normal_pixels)
        finally:
            window_editor.close()
            mask.close()

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
        self.assertEqual(editor.canvas.cursor().shape(), Qt.BitmapCursor)
        self.assertEqual(editor.canvas.cursor().hotSpot(), QPoint(5, 27))
        editor.close()

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
            manager.data["capture_repeat_action"] = "edit"
            with patch("screenshot.mask_window.visible_windows", return_value=[]):
                mask = MaskWindow(Image.new("RGB", (200, 150), "white"), bounds,
                                  [bounds], manager.data)
            app = Application.__new__(Application)
            app.config = manager
            app.mask = None
            app.logger = Mock()
            app.notify = Mock()
            app.edit_images = Mock()
            app.save_capture_images = Mock()
            app.start_capture = Mock()
            app.dispatch("repeat")
            app.start_capture.assert_called_once_with("repeat")
            app.start_capture = Application.start_capture.__get__(app)
            mask.last_region.connect(app.remember_region)
            mask.selection.rects.append(QRect(20, 25, 30, 20))
            mask.complete()
            self.assertEqual(ConfigManager(manager.path).data["last_capture_rect"], [-80, 55, 30, 20])
            fresh = Image.new("RGB", (200, 150), "#18bb44")
            with patch("app.capture_flow.capture", return_value=(fresh, bounds, [bounds], None)) as grab:
                app.show_mask("repeat")
            grab.assert_called_once_with(DEFAULTS["cursor"], alternatives=True,
                                         gap_fill="transparent")
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

    def test_editor_saves_over_existing_capture_path(self):
        from config.config_manager import DEFAULTS

        with tempfile.TemporaryDirectory() as folder:
            settings = dict(DEFAULTS, save_dir=folder, filename="capture", open_dir=False)
            editor = EditorWindow(Image.new("RGB", (20, 12), "blue"), settings)
            first = editor.save(automatic=True)
            manual_target = editor.allocate_path(automatic=False)
            automatic_target = editor.allocate_path(automatic=True)
            self.assertEqual(manual_target.parent, automatic_target.parent)
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

    def test_editor_double_click_saves_and_escape_discards(self):
        from config.config_manager import DEFAULTS
        from PySide6.QtGui import QGuiApplication
        with tempfile.TemporaryDirectory() as folder:
            settings = dict(DEFAULTS, save_dir=folder, filename="double_click",
                            archive_by_month=False)
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
            # Esc 现仅清除当前选中、不再直接关闭编辑器（避免模态框内 Esc 误关窗口）。
            self.assertTrue(discarded.isVisible())
            self.assertEqual(len(list(Path(folder).glob("*.png"))), 1)

    def test_inline_annotation_tool_selection_style_in_light_and_dark_theme(self):
        from PySide6.QtGui import QPalette
        from config.config_manager import DEFAULTS
        from screenshot.mask_window import MaskWindow
        from ui.theme import apply_theme

        original_palette = QPalette(self.app.palette())
        original_mode = self.app.property("screensnap_theme_mode")
        with tempfile.TemporaryDirectory() as folder:
            bounds = {"left": 0, "top": 0, "width": 600, "height": 400}
            settings = {**DEFAULTS, "save_dir": folder, "inline_edit": True,
                        "capture_after_selection": "edit", "crosshair": False,
                        "magnifier": False, "bubble": False, "sound": False}
            with patch("screenshot.mask_window.visible_windows", return_value=[]):
                mask = MaskWindow(Image.new("RGB", (600, 400), "white"), bounds, [bounds], settings)
            try:
                mask.selection.rects.append(QRect(50, 50, 300, 150))
                mask.complete()
                editor = mask.session.inline_editor
                self.assertEqual(editor.toolbar.tool_buttons["rect"].toolButtonStyle(),
                                 Qt.ToolButtonIconOnly)
                for theme in ("light", "dark", "light"):
                    apply_theme(self.app, theme)
                    self.app.processEvents()
                    self._assert_annotation_tool_selection_style(editor.toolbar, theme == "dark")
            finally:
                mask.close()
                self.app.setProperty("screensnap_theme_mode", original_mode)
                self.app.setPalette(original_palette)
                self.app.processEvents()

    def test_quick_sticker_state_is_shared_between_views(self):
        """快速贴图的按住/待发/已消费状态必须按 session 共享。

        回归：每个视图各存一份时，空格只会武装被激活的那个视图，左键落在另一个屏幕
        的视图上就不进入快速贴图（表现为“只有一个屏幕能按住空格快速贴图”）。
        """
        from config.config_manager import DEFAULTS
        from screenshot.mask_window import MaskWindow
        from unittest.mock import patch as patcher

        bounds = {"left": 0, "top": 0, "width": 120, "height": 80}
        settings = {**DEFAULTS, "save_dir": tempfile.gettempdir(), "magnifier": False,
                    "capture_quick_sticker_enabled": True}
        with patcher("screenshot.mask_window.visible_windows", return_value=[]):
            mask = MaskWindow(Image.new("RGB", (120, 80), "blue"), bounds, [bounds], settings)
        try:
            # 一个视图武装 → 同一 session 的另一个视图也必须看到
            mask.quick_sticker_armed = True
            self.assertTrue(mask.session.quick_sticker_armed)
            other = MaskWindow.__new__(MaskWindow)      # 模拟同 session 的另一个视图
            other.session = mask.session
            self.assertTrue(other.quick_sticker_armed)

            # 已消费标记共享：第二个视图不会再触发第二次
            mask.selection.rects.append(QRect(10, 10, 40, 30))
            requests = []
            mask.quick_sticker_requested.connect(lambda image, pos: requests.append(pos))
            mask.trigger_quick_sticker()
            mask.trigger_quick_sticker()
            self.assertEqual(len(requests), 1)
            self.assertTrue(mask.session.quick_sticker_consumed)
        finally:
            mask.close()

    def test_capture_action_delegates_to_the_view_that_owns_the_selection(self):
        """选区阶段的快捷键只在主遮罩上创建，动作必须委派给拥有选区的视图。

        回归：E/R/S/F/C 等快捷键挂在主遮罩视图上，动作却读该视图自己的 selection —— 在
        另一块屏框选后按键会落到没有选区的视图上（表现为只有一块屏有效）。
        """
        from config.config_manager import DEFAULTS
        from screenshot.mask_window import MaskWindow
        from unittest.mock import Mock, patch as patcher

        bounds = {"left": 0, "top": 0, "width": 120, "height": 80}
        settings = {**DEFAULTS, "save_dir": tempfile.gettempdir(), "magnifier": False}
        with patcher("screenshot.mask_window.visible_windows", return_value=[]):
            mask = MaskWindow(Image.new("RGB", (120, 80), "blue"), bounds, [bounds], settings)
        try:
            # 造一个"另一块屏"的视图：同一 session，monitor_rect 覆盖选区所在位置。
            # selection 是 session 共享的一份，归属由"选区与哪个视图的 monitor_rect 相交"决定。
            other = MaskWindow.__new__(MaskWindow)
            other.session = mask.session
            other.monitor_rect = QRect(200, 0, 120, 80)
            mask.session.views.append(other)
            mask.selection.rects.append(QRect(210, 5, 30, 20))
            owner = mask._owner_view()
            self.assertIs(owner, other)                      # 选区所在的视图优先

            calls = []
            other.request_recapture = lambda: calls.append("recapture")
            mask.request_recapture()                          # 在主遮罩上触发
            self.assertEqual(calls, ["recapture"])            # 已委派给拥有选区的视图
        finally:
            mask.session.views = [v for v in mask.session.views if v is not other]
            mask.close()

    def test_clear_selection_resets_without_recapturing(self):
        """R（清除选择）：清空选区并回到未选择状态，不重新抓屏、不移动鼠标。

        回归：R 原来发 recapture_requested 并关闭遮罩 —— 等于重新取最新画面并把鼠标带回
        起始屏；现在只清空选区。
        """
        from config.config_manager import DEFAULTS
        from screenshot.mask_window import MaskWindow
        from unittest.mock import patch as patcher

        bounds = {"left": 0, "top": 0, "width": 120, "height": 80}
        settings = {**DEFAULTS, "save_dir": tempfile.gettempdir(), "magnifier": False}
        with patcher("screenshot.mask_window.visible_windows", return_value=[]):
            mask = MaskWindow(Image.new("RGB", (120, 80), "blue"), bounds, [bounds], settings)
        try:
            mask.selection.rects.append(QRect(10, 10, 40, 30))
            mask.session.multi_select_mode = True
            mask.session.right_capture_mode = True
            recaptured, closed = [], []
            mask.recapture_requested.connect(lambda ctx: recaptured.append(ctx))
            mask.close = lambda *args, **kwargs: closed.append(True)

            mask.clear_selection()

            self.assertEqual(mask.selection.rects, [])          # 选区清空
            self.assertFalse(mask.session.multi_select_mode)     # 多选状态复位
            self.assertFalse(mask.session.right_capture_mode)
            self.assertEqual(recaptured, [])                     # 不重新抓屏
            self.assertEqual(closed, [])                         # 不关闭遮罩/不动鼠标
        finally:
            mask.close()

    def test_quick_save_shortcut_saves_right_button_selection_without_editor(self):
        """S（快速保存）：右键元素选区也必须直接保存，不进入编辑器。

        回归：complete() 里 right_capture_mode/uia_selection 分支无视 save_direct，
        按 S（save_direct=True）仍然 edit_requested → 表现为「右键选区后按 S 进了窗口编辑」。
        """
        from config.config_manager import DEFAULTS
        from screenshot.mask_window import MaskWindow
        from unittest.mock import patch as patcher

        bounds = {"left": 0, "top": 0, "width": 120, "height": 80}
        settings = {**DEFAULTS, "save_dir": tempfile.gettempdir(), "magnifier": False,
                    "capture_after_selection": "edit", "inline_edit": True}
        with patcher("screenshot.mask_window.visible_windows", return_value=[]):
            mask = MaskWindow(Image.new("RGB", (120, 80), "blue"), bounds, [bounds], settings)
        try:
            mask.selection.rects.append(QRect(10, 10, 40, 30))
            mask.session.right_capture_mode = True
            edits, saves = [], []
            mask.edit_requested.connect(lambda images, positions: edits.append(images))
            mask.save_requested.connect(lambda images, positions=None: saves.append(images))
            mask.save_selection()
            self.assertEqual(edits, [])            # 不进编辑器
            self.assertEqual(len(saves), 1)        # 直接保存
        finally:
            mask.close()

    def test_quick_save_shortcut_saves_inline_editor(self):
        """S（快速保存）：原地编辑已打开时保存该编辑器，而不是毫无响应。"""
        from config.config_manager import DEFAULTS
        from screenshot.mask_window import MaskWindow
        from unittest.mock import patch as patcher

        with tempfile.TemporaryDirectory() as folder:
            bounds = {"left": 0, "top": 0, "width": 120, "height": 80}
            settings = {**DEFAULTS, "save_dir": folder, "magnifier": False, "inline_edit": True,
                        "capture_after_selection": "edit", "filename": "quick_save"}
            with patcher("screenshot.mask_window.visible_windows", return_value=[]):
                mask = MaskWindow(Image.new("RGB", (120, 80), "blue"), bounds, [bounds], settings)
            try:
                mask.selection.rects.append(QRect(10, 10, 60, 40))
                mask.complete()
                self.app.processEvents()
                self.assertIsNotNone(mask.session.inline_editor)      # 已进入原地编辑
                saved = []
                mask.image_saved.connect(lambda path, image: saved.append(path))
                mask.save_selection()                                  # 等价于按 S
                self.assertEqual(len(saved), 1)                        # 真的保存了
                self.assertTrue(Path(saved[0]).exists())
            finally:
                mask.close()

    def test_fixed_size_creates_resizes_and_keeps_right_selection(self):
        """F（固定尺寸）：未选则新建；左键已选则按中心改尺寸；右键已选则不动它们。"""
        from config.config_manager import DEFAULTS
        from screenshot.mask_window import MaskWindow
        from unittest.mock import patch as patcher

        bounds = {"left": 0, "top": 0, "width": 200, "height": 100}
        settings = {**DEFAULTS, "save_dir": tempfile.gettempdir(), "magnifier": False}
        with patcher("screenshot.mask_window.visible_windows", return_value=[]):
            mask = MaskWindow(Image.new("RGB", (200, 100), "blue"), bounds, [bounds], settings)
        try:
            mask.position = QPoint(50, 40)
            mask.apply_fixed_size(60, 30)                     # ① 未选择 → 新建
            self.assertEqual(len(mask.selection.rects), 1)
            first = mask.selection.rects[0]
            self.assertEqual((first.width(), first.height()), (60, 30))

            mask.apply_fixed_size(80, 20)                     # ② 左键已选 → 改尺寸不新增
            self.assertEqual(len(mask.selection.rects), 1)
            self.assertEqual((first.width(), first.height()), (80, 20))

            mask.session.right_capture_mode = True            # ③ 右键模式 → 不动已有区域
            mask.apply_fixed_size(40, 25)
            self.assertEqual(len(mask.selection.rects), 2)
            self.assertEqual((first.width(), first.height()), (80, 20))   # 原区域未被改动
            self.assertEqual((mask.selection.rects[1].width(),
                              mask.selection.rects[1].height()), (40, 25))
        finally:
            mask.close()

    def test_picker_mode_available_while_inline_editing(self):
        """C（取色）在原地编辑下也能开启，且不退出编辑器；提示语在三种界面都提示。"""
        from config.config_manager import DEFAULTS
        from screenshot.mask_window import MaskWindow
        from unittest.mock import patch as patcher

        with tempfile.TemporaryDirectory() as folder:
            bounds = {"left": 0, "top": 0, "width": 120, "height": 80}
            settings = {**DEFAULTS, "save_dir": folder, "magnifier": False, "inline_edit": True,
                        "capture_after_selection": "edit", "filename": "picker"}
            with patcher("screenshot.mask_window.visible_windows", return_value=[]):
                mask = MaskWindow(Image.new("RGB", (120, 80), "blue"), bounds, [bounds], settings)
            try:
                # 未选择状态：取色可用
                self.assertTrue(mask.capture_picker_shortcut.isEnabled())
                mask.toggle_picker_mode()
                self.assertTrue(mask.picker_mode)
                mask.toggle_picker_mode()
                self.assertFalse(mask.picker_mode)

                # 多选状态：取色仍可用
                mask.session.multi_select_mode = True
                mask.toggle_picker_mode()
                self.assertTrue(mask.picker_mode)
                mask.toggle_picker_mode()
                mask.session.multi_select_mode = False

                # 原地编辑：快捷键仍启用、能开启取色，且编辑器不被关闭
                mask.selection.rects.append(QRect(10, 10, 60, 40))
                mask.complete()
                self.app.processEvents()
                self.assertIsNotNone(mask.session.inline_editor)
                self.assertTrue(mask.capture_picker_shortcut.isEnabled())
                mask.toggle_picker_mode()
                self.assertTrue(mask.picker_mode)                       # 取色已开启
                self.assertIsNotNone(mask.session.inline_editor)        # 编辑器仍在（不退出）
            finally:
                mask.close()

    def test_capture_action_targets_the_view_that_owns_the_selection(self):
        """两屏：选区在副屏时，主屏视图上的快捷键动作必须落到副屏视图（回归 E/S/F 跑错屏）。

        selection 是 session 共享的一份，各视图 rects 相同 —— 用"谁的 rects 非空"判定归属
        会永远选中列表里第一个视图，于是主屏划选后按 F 弹到副屏、副屏划选后按 E 进不去。
        """
        from config.config_manager import DEFAULTS
        from screenshot.mask_window import MaskWindow
        from unittest.mock import patch as patcher

        bounds = {"left": 0, "top": 0, "width": 200, "height": 100}
        monitors = [{"left": 0, "top": 0, "width": 100, "height": 100},
                    {"left": 100, "top": 0, "width": 100, "height": 100}]
        screen_infos = [{"geometry": QRect(0, 0, 100, 100), "dpr": 1.0},
                        {"geometry": QRect(100, 0, 100, 100), "dpr": 1.0}]
        settings = {**DEFAULTS, "save_dir": tempfile.gettempdir(), "magnifier": False,
                    "inline_edit": True, "capture_after_selection": "save"}
        with patcher("screenshot.mask_window.visible_windows", return_value=[]), \
                patcher("core.dpi.DisplayMapper.collect_screen_infos", return_value=screen_infos):
            mask = MaskWindow(Image.new("RGB", (200, 100), "blue"), bounds, monitors, settings)
        try:
            views = [v for v in mask.session.views if v is not None]
            self.assertGreaterEqual(len(views), 2)                    # 两块屏各一个视图
            left_view = next(v for v in views if v.monitor_rect.center().x() < 100)
            right_view = next(v for v in views if v.monitor_rect.center().x() >= 100)

            # 选区落在副屏（右半）：owner 必须是副屏视图，而不是列表第一个
            mask.selection.rects.append(QRect(120, 10, 40, 30))
            self.assertIs(mask._owner_view(), right_view)

            routed = []
            right_view.complete_in_window_editor = lambda: routed.append("right")
            mask.complete_in_window_editor()
            self.assertEqual(routed, ["right"])                        # 动作落到副屏视图

            # 选区换到主屏（左半）：owner 必须跟着回到主屏视图
            mask.selection.rects[:] = [QRect(10, 10, 40, 30)]
            self.assertIs(mask._owner_view(), left_view)
        finally:
            mask.close()

    def test_owner_view_across_screen_layouts_resolutions_and_dpi(self):
        """跨屏归属组合验证：屏数 × 排布 × 分辨率 × DPI × 跨屏选区（规则 22/26）。

        每个布局都把选区分别放进每一块屏，断言 _owner_view() 选中该屏的视图；
        再放一个跨屏选区，断言选中相交面积更大的那块屏的视图。
        回归：selection 是 session 共享的一份，用"谁的 rects 非空"判定会永远选第一个视图。
        """
        from config.config_manager import DEFAULTS
        from screenshot.mask_window import MaskWindow
        from unittest.mock import patch as patcher

        # (名称, 物理显示器矩形, 每屏 dpr)
        layouts = [
            ("双屏并排", [(0, 0, 800, 600), (800, 0, 800, 600)], (1.0, 1.0)),
            ("双屏上下", [(0, 0, 800, 600), (0, 600, 800, 600)], (1.0, 1.0)),
            ("双屏负原点", [(-800, 0, 800, 600), (0, 0, 800, 600)], (1.0, 1.0)),
            ("双屏混合 DPI", [(0, 0, 800, 600), (800, 0, 960, 540)], (1.0, 1.5)),
            ("三屏并排", [(0, 0, 800, 600), (800, 0, 800, 600), (1600, 0, 800, 600)],
             (1.0, 1.0, 1.0)),
            ("四屏 2x2", [(0, 0, 800, 600), (800, 0, 800, 600),
                          (0, 600, 800, 600), (800, 600, 800, 600)],
             (1.0, 1.25, 1.5, 1.0)),
        ]
        settings = {**DEFAULTS, "save_dir": tempfile.gettempdir(), "magnifier": False}
        for name, monitors, scales in layouts:
            # 物理总范围与各屏物理局部矩形（遮罩用物理局部坐标）
            left = min(m[0] for m in monitors)
            top = min(m[1] for m in monitors)
            right = max(m[0] + m[2] for m in monitors)
            bottom = max(m[1] + m[3] for m in monitors)
            bounds = {"left": left, "top": top, "width": right - left, "height": bottom - top}
            monitor_dicts = [{"left": m[0], "top": m[1], "width": m[2], "height": m[3]}
                             for m in monitors]
            # Qt 逻辑布局 = 物理 / dpr，按物理顺序紧凑排列（与真实混合 DPI 一致）
            screen_infos, x = [], 0
            for (mx, my, mw, mh), scale in zip(monitors, scales):
                screen_infos.append({"geometry": QRect(x, 0, int(mw / scale), int(mh / scale)),
                                     "dpr": float(scale)})
                x += int(mw / scale)
            with patcher("screenshot.mask_window.visible_windows", return_value=[]), \
                    patcher("core.dpi.DisplayMapper.collect_screen_infos", return_value=screen_infos):
                mask = MaskWindow(Image.new("RGB", (bounds["width"], bounds["height"]), "blue"),
                                  bounds, monitor_dicts, settings)
            try:
                views = [v for v in mask.session.views if v is not None]
                self.assertGreaterEqual(len(views), len(monitors), name)
                # ① 选区分别放进每一块屏 → 归属必须是那块屏的视图
                for index, (mx, my, mw, mh) in enumerate(monitors):
                    local = QRect(mx - left + 20, my - top + 20, 100, 80)
                    mask.selection.rects[:] = [local]
                    owner = mask._owner_view()
                    self.assertTrue(owner.monitor_rect.contains(local.center()),
                                    "%s：第 %d 块屏的选区归属错了" % (name, index + 1))
                # ② 跨屏选区 → 归属相交面积更大的那块屏
                first, second = monitors[0], monitors[1]
                cross = QRect(first[0] - left + first[2] - 30, first[1] - top + 20, 60, 80)
                mask.selection.rects[:] = [cross]
                owner = mask._owner_view()
                overlap_first = cross.intersected(
                    QRect(first[0] - left, first[1] - top, first[2], first[3]))
                overlap_second = cross.intersected(
                    QRect(second[0] - left, second[1] - top, second[2], second[3]))
                expected = first if (overlap_first.width() * overlap_first.height() >=
                                     overlap_second.width() * overlap_second.height()) else second
                self.assertTrue(
                    owner.monitor_rect.contains(QRect(expected[0] - left + 5,
                                                      expected[1] - top + 5, 1, 1).center()),
                    "%s：跨屏选区归属错了" % name)
            finally:
                mask.close()



if __name__ == "__main__":
    unittest.main()


if __name__ == "__main__":
    unittest.main()


if __name__ == "__main__":
    unittest.main()


if __name__ == "__main__":
    unittest.main()


    def test_quick_save_is_nudge_within_window(self):
        """0.3 秒内刚微调过（或正按住左键）时，按 S = 下移微调，不保存。"""
        import time as _time
        from config.config_manager import DEFAULTS
        from screenshot.mask_window import MaskWindow
        bounds = {"left": 0, "top": 0, "width": 200, "height": 150}
        with patch("screenshot.mask_window.visible_windows", return_value=[]):
            mask = MaskWindow(Image.new("RGB", (200, 150)), bounds, [bounds], DEFAULTS)
        try:
            mask.selection.rects.append(QRect(20, 20, 60, 40))
            mask.selection.nudge_corner = (0, QPoint(20, 20), QPoint(20, 20), "both", QRect(20, 20, 60, 40))
            mask._nudge_at = _time.monotonic()
            mask._grip_down = True
            with patch.object(mask, "complete") as complete:
                mask.save_selection()
            complete.assert_not_called()
        finally:
            mask.close()

    def test_quick_save_saves_after_window_expires(self):
        """超过 0.3 秒且未按住左键时，按 S = 快捷保存。"""
        import time as _time
        from config.config_manager import DEFAULTS
        from screenshot.mask_window import MaskWindow
        bounds = {"left": 0, "top": 0, "width": 200, "height": 150}
        with patch("screenshot.mask_window.visible_windows", return_value=[]):
            mask = MaskWindow(Image.new("RGB", (200, 150)), bounds, [bounds], DEFAULTS)
        try:
            mask.selection.rects.append(QRect(20, 20, 60, 40))
            mask.selection.nudge_corner = None
            mask._grip_down = False
            mask._nudge_at = _time.monotonic() - 1.0
            with patch.object(mask, "complete") as complete:
                mask.save_selection()
            complete.assert_called_once()
        finally:
            mask.close()
