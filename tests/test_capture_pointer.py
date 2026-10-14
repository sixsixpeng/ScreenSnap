"""跨屏抓取的局部/全局事件来源与连续物理端点回归（不代替真机验收）。"""
import itertools
import sys
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) in sys.path:
    sys.path.remove(str(_ROOT))
sys.path.insert(0, str(_ROOT))

from PIL import Image, ImageDraw
from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QPointF, QRect, Qt
from PySide6.QtGui import QMouseEvent

from config.config_manager import DEFAULTS
from core.dpi import DisplayMapper
from screenshot.mask_window import MaskSession, MaskWindow
from screenshot.selection_rect import SelectionRects
from tests.base import CoreTests


class CapturePointerTests(CoreTests):
    @staticmethod
    def layouts():
        # 首项来自日志：上方4K@125%、下方1920x1200@100%，主屏在下且横向错开。
        yield ([(-751, -2160, 3840, 2160), (0, 0, 1920, 1200)],
               [(-751, -2160, 3072, 1728), (0, 0, 1920, 1200)], [1.25, 1.0])
        for scale, compact in itertools.product((1.0, 1.25, 1.5, 1.75), (False, True)):
            width, height = round(3840 / scale), round(2160 / scale)
            yield ([(0, 0, 3840, 2160), (0, 2160, 1920, 1080)],
                   [(0, 0, width, height), (0, height if compact else 2160, 1920, 1080)],
                   [scale, 1.0])
        yield ([(0, 0, 2560, 1440), (0, 1440, 2560, 1440)],
               [(0, 0, 2560, 1440), (0, 1440, 2560, 1440)], [1.0, 1.0])
        yield ([(0, -1440, 2560, 1440), (0, 0, 1920, 1080)],
               [(0, -1440, 2048, 1152), (0, 0, 1097, 617)], [1.25, 1.75])
        yield ([(-2160, 0, 2160, 3840), (0, 0, 1920, 1080)],
               [(-2160, 0, 1728, 3072), (0, 0, 1920, 1080)], [1.25, 1.0])
        yield ([(0, 0, 1920, 1080), (1920, 0, 2560, 1440), (0, 1440, 3840, 2160)],
               [(0, 0, 1920, 1080), (1920, 0, 2048, 1152), (0, 1440, 2560, 1440)],
               [1.0, 1.25, 1.5])
        yield ([(0, 0, 1920, 1080), (1920, 0, 2560, 1440),
                (0, 1440, 3840, 2160), (3840, 1440, 1920, 1080)],
               [(0, 0, 1920, 1080), (1920, 0, 2048, 1152),
                (0, 1440, 2560, 1440), (3840, 1440, 1097, 617)],
               [1.0, 1.25, 1.5, 1.75])

    @contextmanager
    def fixture(self, layout=None, trusted_scales=True, **overrides):
        rectangles, native, scales = layout or next(self.layouts())
        physical = QRect(*rectangles[0])
        for rect in rectangles[1:]:
            physical = physical.united(QRect(*rect))
        bounds = dict(left=physical.x(), top=physical.y(), width=physical.width(), height=physical.height())
        monitors = [dict(left=x, top=y, width=w, height=h) for x, y, w, h in rectangles]
        infos = [dict(geometry=QRect(*rect), dpr=scale) for rect, scale in zip(native, scales)]
        mapper = DisplayMapper(bounds, monitors, screen_infos=infos,
                               monitor_scales=scales if trusted_scales else None)
        session = MaskSession(mapper)
        settings = dict(DEFAULTS, window_detection=False, window_hover_detect=False,
                        window_uia_detect=False, inline_edit=False, magnifier=False,
                        crosshair=False, capture_after_selection="save",
                        capture_quick_sticker_enabled=False, editor_image_round_corners=False,
                        editor_image_border_enabled=False, editor_image_shadow_enabled=False)
        settings.update(overrides)
        image = Image.new("RGB", (physical.width(), physical.height()), "white")
        draw = ImageDraw.Draw(image)
        for index, rect in enumerate(rectangles):
            x, y, w, h = rect
            draw.rectangle((x - physical.x(), y - physical.y(), x + w - 1 - physical.x(),
                            y + h - 1 - physical.y()), fill="red" if index == 0 else "blue")
        with patch("screenshot.mask_window.visible_windows", return_value=[]), \
                patch("screenshot.mask_window.QGuiApplication.keyboardModifiers", return_value=Qt.KeyboardModifier.NoModifier), \
                patch("screenshot.mask_window.QCursor.setPos") as move_cursor:
            views = [MaskWindow(image, bounds, monitors, settings, session=session,
                                monitor=monitor, primary=index == len(monitors) - 1)
                     for index, monitor in enumerate(monitors)]
            try:
                yield SimpleNamespace(views=views, mapper=mapper, session=session, image=image,
                                      rectangles=rectangles, native=native, move_cursor=move_cursor)
            finally:
                for view in views:
                    view.close()
                    view.deleteLater()
                session.views.clear()
                QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    @staticmethod
    def native_point(context, physical):
        # 全局点独立按目标屏构造，不通过待测视图的局部转换来生成预期。
        for rect, native in zip(context.rectangles, context.native):
            if QRect(*rect).contains(physical):
                x, y, w, h = rect
                nx, ny, nw, nh = native
                return QPointF(nx + (physical.x() - x) * nw / w,
                               ny + (physical.y() - y) * nh / h)
        raise AssertionError("physical fixture point must be on a monitor")

    def mouse(self, context, source, physical, kind, button, model="source_scaled"):
        global_point = self.native_point(context, physical)
        view = context.views[source]
        if model == "native_offset":
            local = global_point - QPointF(view.mapToGlobal(QPoint()))
        else:
            x, y, w, h = context.rectangles[source]
            _, _, nw, nh = context.native[source]
            local = QPointF((physical.x() - x) * nw / w, (physical.y() - y) * nh / h)
        buttons = Qt.MouseButton.NoButton if kind == QEvent.Type.MouseButtonRelease else button
        event_button = Qt.MouseButton.NoButton if kind == QEvent.Type.MouseMove else button
        return QMouseEvent(kind, local, global_point, event_button, buttons, Qt.KeyboardModifier.NoModifier)

    def test_continuous_cross_screen_drag_uses_global_position_not_source_scale(self):
        combinations = 0
        for (index, layout), trusted in itertools.product(enumerate(self.layouts()), (False, True)):
            with self.fixture(layout, trusted_scales=trusted) as context:
                targets = ((0, 1), (1, 0)) if len(context.views) == 2 else tuple(
                    (source, target) for source in range(len(context.views))
                    for target in range(len(context.views)) if source != target)
                for (source, target), model, button in itertools.product(
                        targets, ("native_offset", "source_scaled"),
                        (Qt.MouseButton.LeftButton, Qt.MouseButton.RightButton)):
                    view = context.views[source]
                    view.selection.finish()
                    view.selection.rects.clear()
                    context.session.right_capture_mode = False
                    start = QRect(*context.rectangles[source]).center()
                    destination = QRect(*context.rectangles[target])
                    # 不靠近任何其他屏幕的边缘，吸附的精确边界由既有用例单独验证。
                    physical_points = [QPoint(destination.x() + destination.width() * 37 // 100,
                                              destination.y() + round(destination.height() * part))
                                       for part in (0.01, 0.1, 0.2, 0.22, 0.225, 0.23, 0.3, 0.6, 0.95)]
                    local_start = start - context.mapper.physical_bounds.topLeft()
                    with self.subTest(layout=index, trusted=trusted, source=source, target=target, model=model, button=button), \
                            patch.object(view, "complete") as complete:
                        view.mousePressEvent(self.mouse(context, source, start, QEvent.Type.MouseButtonPress, button, model))
                        self.assertEqual(view.selection.start, local_start)
                        for point in physical_points:
                            view.mouseMoveEvent(self.mouse(context, source, point, QEvent.Type.MouseMove, button, model))
                            expected = point - context.mapper.physical_bounds.topLeft()
                            self.assertEqual(view.position, expected)
                            self.assertEqual(view.selection.start, local_start)
                            self.assertEqual(view.selection.active, SelectionRects.resized_rect(local_start, expected, "both", QRect()))
                        final = physical_points[-1] + QPoint(1, 1)
                        view.mouseReleaseEvent(self.mouse(context, source, final, QEvent.Type.MouseButtonRelease, button, model))
                        expected_final = final - context.mapper.physical_bounds.topLeft()
                        self.assertEqual(view.position, expected_final)
                        self.assertEqual(view.selection.rects[-1], SelectionRects.resized_rect(local_start, expected_final, "both", QRect()))
                        self.assertEqual(complete.called, button == Qt.MouseButton.LeftButton)
                        context.move_cursor.assert_not_called()
                        combinations += 1
        print("cross-screen pointer combinations:", combinations)

    def test_reported_vertical_gap_does_not_jump_back_after_270_pixels(self):
        with self.fixture() as context:
            view = context.views[0]
            start = QPoint(800, -900)
            view.mousePressEvent(self.mouse(context, 0, start, QEvent.Type.MouseButtonPress, Qt.MouseButton.RightButton))
            previous = None
            for y in (250, 260, 266, 267, 268, 269, 270, 271, 272, 273, 274, 280, 600, 1199):
                point = QPoint(800, y)
                view.mouseMoveEvent(self.mouse(context, 0, point, QEvent.Type.MouseMove, Qt.MouseButton.RightButton))
                self.assertEqual(view.position, point - context.mapper.physical_bounds.topLeft())
                self.assertGreaterEqual(view.selection.active.bottom(), 2160)
                if previous is not None:
                    self.assertGreaterEqual(view.selection.active.bottom(), previous)
                previous = view.selection.active.bottom()

    def test_badge_poll_does_not_replace_an_active_drag_or_keyboard_endpoint(self):
        for keyboard in (False, True):
            with self.subTest(keyboard=keyboard), self.fixture() as context:
                view = context.views[0]
                start, end = QPoint(800, -900), QPoint(800, 300)
                view.mousePressEvent(self.mouse(context, 0, start, QEvent.Type.MouseButtonPress, Qt.MouseButton.RightButton, "native_offset"))
                view.mouseMoveEvent(self.mouse(context, 0, end, QEvent.Type.MouseMove, Qt.MouseButton.RightButton, "native_offset"))
                if keyboard:
                    self.assertTrue(view._nudge_active_region(0, 1))
                wanted = QPoint(view.position)
                region = QRect(view.selection.active)
                for current in context.views:
                    with patch.object(current, "_live_cursor_physical", return_value=QPoint(10, 10)), \
                            patch.object(current, "_sync_size_badge"):
                        current._badge_poll_tick()
                self.assertEqual(view.position, wanted)
                self.assertEqual(view.selection.active, region)

    def test_live_cursor_and_picker_do_not_reconstruct_widget_local_coordinates(self):
        with self.fixture() as context:
            point = QPoint(800, 300)
            native = self.native_point(context, point).toPoint()
            expected = point - context.mapper.physical_bounds.topLeft()
            for view in context.views:
                with patch("screenshot.mask_window.QCursor.pos", return_value=native), \
                        patch.object(view, "mapFromGlobal", side_effect=AssertionError("must use native global directly")):
                    self.assertEqual(view._live_cursor_physical(), expected)
            with patch("screenshot.mask_window.QCursor.pos", return_value=native), \
                    patch.object(context.views[0], "focus_view_for_position", return_value=context.views[1]), \
                    patch.object(context.views[1], "mapFromGlobal", side_effect=AssertionError("no local round trip")), \
                    patch.object(context.views[1], "sample_color_at") as sample:
                context.views[0].sample_color_at_cursor()
                sample.assert_called_once_with(expected)

    def test_alt_click_picker_uses_global_event_position(self):
        with self.fixture() as context:
            point = QPoint(800, 300)
            view = context.views[0]
            event = self.mouse(context, 0, point, QEvent.Type.MouseButtonPress, Qt.MouseButton.LeftButton)
            with patch("screenshot.mask_window.QGuiApplication.keyboardModifiers", return_value=Qt.KeyboardModifier.AltModifier), \
                    patch.object(view, "sample_color_at") as sample:
                view.mousePressEvent(event)
                sample.assert_called_once_with(point - context.mapper.physical_bounds.topLeft())

    def test_inline_handle_hit_uses_forwarded_global_not_child_local_position(self):
        with self.fixture(inline_edit=True, capture_after_selection="edit") as context:
            view = context.views[0]
            region = QRect(300, 300, 500, 350)
            view.selection.rects.append(region)
            view.complete()
            editor = context.session.inline_editor
            self.assertIsNotNone(editor)
            physical = region.topLeft() + context.mapper.physical_bounds.topLeft()
            event = QMouseEvent(QEvent.Type.MouseButtonPress, QPointF(50, 50),
                                self.native_point(context, physical), Qt.MouseButton.LeftButton,
                                Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
            with patch.object(view, "grabMouse") as grab:
                self.assertTrue(editor.eventFilter(editor.canvas.viewport(), event))
                self.assertIsNotNone(view.selection.resizing)
                grab.assert_called_once()

    def test_cross_screen_quick_sticker_keeps_final_crop_and_native_anchor(self):
        with self.fixture(capture_quick_sticker_enabled=True) as context:
            view = context.views[0]
            start, end = QPoint(500, -600), QPoint(800, 700)
            view.mousePressEvent(self.mouse(context, 0, start, QEvent.Type.MouseButtonPress, Qt.MouseButton.RightButton))
            view.mouseMoveEvent(self.mouse(context, 0, end, QEvent.Type.MouseMove, Qt.MouseButton.RightButton))
            view.quick_sticker_pending = True
            outputs = []
            view.quick_sticker_requested.connect(lambda image, position: outputs.append((image, position)))
            view.mouseReleaseEvent(self.mouse(context, 0, end, QEvent.Type.MouseButtonRelease, Qt.MouseButton.RightButton))
            self.assertEqual(len(outputs), 1)
            image, position = outputs[0]
            self.assertEqual(image.size, (301, 1301))
            self.assertEqual(image.getpixel((0, 0))[:3], (255, 0, 0))
            self.assertEqual(image.getpixel((300, 1300))[:3], (0, 0, 255))
            self.assertEqual(position, self.native_point(context, start).toPoint())

    def test_coordinate_mismatch_diagnostics_are_throttled(self):
        import logging
        from logger import log_rate

        with self.fixture() as context:
            view = context.views[0]
            event = self.mouse(context, 0, QPoint(800, 300), QEvent.Type.MouseMove, Qt.MouseButton.RightButton)
            with patch.dict(log_rate._stamps, {}, clear=True), \
                    patch("logger.log_rate.time.monotonic", return_value=1.0), \
                    patch.object(logging.getLogger("screensnap"), "log") as write:
                for _ in range(30):
                    view._mouse_event_position(event)
                self.assertEqual(write.call_count, 1)

    def test_release_keeps_keyboard_controlled_endpoint(self):
        with self.fixture() as context:
            view = context.views[0]
            start, end = QPoint(800, -900), QPoint(900, 300)
            view.mousePressEvent(self.mouse(context, 0, start, QEvent.Type.MouseButtonPress, Qt.MouseButton.RightButton, "native_offset"))
            view.mouseMoveEvent(self.mouse(context, 0, end, QEvent.Type.MouseMove, Qt.MouseButton.RightButton, "native_offset"))
            self.assertTrue(view._nudge_active_region(1, 1))
            wanted = QPoint(view.position)
            view.mouseReleaseEvent(self.mouse(context, 0, QPoint(801, 310), QEvent.Type.MouseButtonRelease, Qt.MouseButton.RightButton))
            self.assertEqual(view.position, wanted)
            self.assertEqual(view.selection.rects[-1].bottomRight(), wanted)


if __name__ == "__main__":
    unittest.main()
