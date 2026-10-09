"""屏幕坐标空间与多屏 / 混合 DPI：映射不变量矩阵、组合穷举、运行中显示器变化。"""

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


class DpiTests(CoreTests):
    def test_capture_operation_tips_use_available_monitor_width(self):
        from screenshot.overlay_info import paint_info

        painter = Mock()
        painter.fontMetrics.return_value.horizontalAdvance.side_effect = lambda text: len(text) * 12
        painter.fontMetrics.return_value.height.return_value = 16
        painter.fontMetrics.return_value.elidedText.side_effect = lambda text, *_: text
        anchor = QRect(200, 200, 140, 140)
        paint_info(painter, anchor, QRect(0, 0, 1920, 1080),
                   ["20, 20", "拖拽框选", "Esc取消"], max_width=1920)
        wide_rect = painter.drawRoundedRect.call_args.args[0]
        # 宽度贴合文字而不是占满可用宽度，且仍限制在屏内。
        self.assertLess(wide_rect.width(), 1920 - 16)
        self.assertGreaterEqual(wide_rect.left(), 4)
        self.assertLessEqual(wide_rect.right(), 1920 - 4)
        # 文字按屏边对齐：光标在屏幕左侧区域时左对齐（不再居中）。
        self.assertTrue(all(call.args[1] & Qt.AlignLeft
                            for call in painter.drawText.call_args_list))
        # 屏幕变窄时提示条不超宽，并自动换行（行数增加）。
        painter.drawText.reset_mock()
        paint_info(painter, anchor, QRect(0, 0, 600, 300), ["Esc取消", "甲" * 30, "乙" * 30],
                   max_width=600)
        self.assertLessEqual(painter.drawRoundedRect.call_args.args[0].width(), 600 - 16)
        self.assertGreater(painter.drawText.call_count, 1)
        self.assertIn("Esc取消",
                      " ".join(call.args[-1] for call in painter.drawText.call_args_list))

    def test_capture_operation_tips_follow_magnifier_on_each_monitor(self):
        from PySide6.QtGui import QColor, QImage, QPainter
        from screenshot.overlay_info import paint_info

        image = QImage(2000, 900, QImage.Format_ARGB32)
        image.fill(QColor("white"))
        painter = QPainter(image)
        # 每个显示器各画一条：提示条跟着各自的放大镜框走，而不是贴屏顶居中。
        first = paint_info(painter, QRect(200, 200, 140, 140), QRect(0, 0, 1000, 900),
                           ["20, 20", "拖拽框选"])
        second = paint_info(painter, QRect(1200, 200, 140, 140), QRect(1000, 0, 1000, 900),
                            ["20, 20", "拖拽框选"])
        painter.end()
        for bar, anchor in ((first, QRect(200, 200, 140, 140)), (second, QRect(1200, 200, 140, 140))):
            self.assertGreaterEqual(bar.top(), anchor.bottom())
            self.assertGreater(bar.center().x(), anchor.center().x() - 200)
            self.assertLess(bar.center().x(), anchor.center().x() + 200)
            self.assertLess(image.pixelColor(bar.center().x(), bar.top() + 1).red(), 100)
        # 提示条之外（含屏顶）保持原图。
        self.assertGreater(image.pixelColor(500, 2).red(), 200)
        self.assertGreater(image.pixelColor(1500, 2).red(), 200)

    def test_native_toast_failure_does_not_downgrade_saved_backend_and_retries_after_reload(self):
        from app.notification_flow import NotificationMixin
        from app.notifications import NotificationBridge

        class NotificationApp(NotificationMixin):
            pass

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "settings.json"
            config = ConfigManager(path)
            self.assertEqual(config.data["notification_backend"], "win11toast")
            app = NotificationApp()
            app.qt = self.app
            app.config = config
            app.tray = Mock()
            app.notification_bridge = NotificationBridge(app)

            with patch("ui.native_toast.show_native_toast", return_value=False) as native, \
                    patch("app.notification_flow.QApplication.beep"):
                app.notify("Native Toast unavailable")
            native.assert_called_once()
            app.tray.showMessage.assert_called_once()
            self.assertEqual(config.data["notification_backend"], "win11toast")
            self.assertEqual(ConfigManager(path).data["notification_backend"], "win11toast")

            failure_callbacks = []

            def start_toast(*args, on_failed=None, **kwargs):
                failure_callbacks.append(on_failed)
                return True

            with patch("ui.native_toast.show_native_toast", side_effect=start_toast), \
                    patch("app.notification_flow.QApplication.beep"):
                app.notify("Asynchronous Toast failure")
            failure_callbacks[0](RuntimeError("temporary WinRT failure"))
            self.app.processEvents()
            self.assertEqual(app.tray.showMessage.call_count, 2)
            self.assertEqual(config.data["notification_backend"], "win11toast")
            self.assertEqual(ConfigManager(path).data["notification_backend"], "win11toast")

            app.config = ConfigManager(path)
            with patch("ui.native_toast.show_native_toast", return_value=True) as native, \
                    patch("app.notification_flow.QApplication.beep"):
                app.notify("Native Toast recovered")
            native.assert_called_once()
            self.assertEqual(app.config.data["notification_backend"], "win11toast")

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
                self.assertTrue(show_native_toast("截图", "1 张", path, click,
                                                  failed, duration="short"))
            create_thread.assert_called_once()
            thread.start.assert_called_once_with()
            create_thread.call_args.kwargs["target"]()
            callbacks = toast.call_args.kwargs
            toast.assert_called_once_with(
                "截图", "1 张", image={"src": str(path.resolve()), "placement": "hero"},
                app_id="ScreenSnap.Desktop",       # 调用侧会显式带 app_id，旧期望漏了它
                on_click=callbacks["on_click"],
                on_dismissed=callbacks["on_dismissed"],
                on_failed=callbacks["on_failed"], duration="short")
            with self.assertLogs("screensnap", level="DEBUG"):
                callbacks["on_click"]("activated")
                callbacks["on_dismissed"]("dismissed")
            click.assert_called_once_with("activated")
            with self.assertLogs("screensnap", level="WARNING"):
                callbacks["on_failed"]("failure")
            failed.assert_called_once_with("failure")

    def test_native_toast_timeout_maps_to_supported_durations(self):
        from ui.native_toast import native_toast_duration

        self.assertEqual(native_toast_duration(0), "long")
        self.assertEqual(native_toast_duration(2), "short")
        self.assertEqual(native_toast_duration(7), "short")
        self.assertEqual(native_toast_duration(8), "long")

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

    def test_display_mapper_uses_native_qt_geometry_for_cursor_points(self):
        from core.dpi import DisplayMapper

        bounds = {"left": 0, "top": 0, "width": 3840, "height": 3240}
        monitors = [
            {"left": 0, "top": 0, "width": 3840, "height": 2160},
            {"left": 0, "top": 2160, "width": 1920, "height": 1080},
        ]
        mapper = DisplayMapper(bounds, monitors, screen_infos=[
            {"geometry": QRect(0, 0, 3072, 1728), "dpr": 1.25},
            {"geometry": QRect(0, 2160, 1920, 1080), "dpr": 1.0},
        ])
        cursor_point = QPoint(300, 2165)

        self.assertEqual(mapper.native_global_to_physical_global(cursor_point), cursor_point)
        self.assertEqual(mapper.physical_global_to_native_global(cursor_point), cursor_point)
        self.assertEqual(mapper.logical_global_to_physical_global(QPoint(300, 1733)),
                         cursor_point)

    def test_capture_sticker_position_uses_native_qt_screen_coordinates(self):
        from PySide6.QtCore import QPoint, QRect
        from config.config_manager import DEFAULTS
        from screenshot.mask_window import MaskWindow

        bounds = {"left": 0, "top": 0, "width": 3840, "height": 3240}
        monitors = [
            {"left": 0, "top": 0, "width": 3840, "height": 2160},
            {"left": 0, "top": 2160, "width": 1920, "height": 1080},
        ]
        screen_infos = [
            {"geometry": QRect(0, 0, 3072, 1728), "dpr": 1.25},
            {"geometry": QRect(0, 2160, 1920, 1080), "dpr": 1.0},
        ]
        settings = dict(DEFAULTS, inline_edit=False, capture_after_selection="edit",
                        magnifier=False, crosshair=False, sound=False)
        with patch("core.dpi.DisplayMapper.collect_screen_infos", return_value=screen_infos), \
                patch("screenshot.mask_window.visible_windows", return_value=[]):
            mask = MaskWindow(Image.new("RGB", (3840, 3240), "white"), bounds,
                              monitors, settings)
        emitted = []
        mask.edit_requested.connect(lambda images, positions: emitted.append(positions))
        try:
            mask.selection.rects.append(QRect(100, 2210, 50, 40))
            mask.complete(force_window=True)

            self.assertEqual(emitted[0][0], QPoint(100, 2210))
        finally:
            mask.close()

    def test_capture_sticker_position_on_origin_monitor_below_negative_dpi_monitor(self):
        from PySide6.QtCore import QPoint, QRect
        from config.config_manager import DEFAULTS
        from screenshot.mask_window import MaskWindow

        bounds = {"left": -751, "top": -2160, "width": 3840, "height": 3360}
        monitors = [
            {"left": 0, "top": 0, "width": 1920, "height": 1200},
            {"left": -751, "top": -2160, "width": 3840, "height": 2160},
        ]
        screen_infos = [
            {"geometry": QRect(0, 0, 1920, 1200), "dpr": 1.0},
            {"geometry": QRect(-751, -2160, 3072, 1728), "dpr": 1.25},
        ]
        settings = dict(DEFAULTS, inline_edit=False, capture_after_selection="edit",
                        magnifier=False, crosshair=False, sound=False)
        with patch("core.dpi.DisplayMapper.collect_screen_infos", return_value=screen_infos), \
                patch("screenshot.mask_window.visible_windows", return_value=[]):
            mask = MaskWindow(Image.new("RGB", (3840, 3360), "white"), bounds,
                              monitors, settings)
        emitted = []
        mask.edit_requested.connect(lambda images, positions: emitted.append(positions))
        try:
            local_selection = QRect(200 - bounds["left"], 150 - bounds["top"], 60, 40)
            mask.selection.rects.append(local_selection)
            mask.complete(force_window=True)

            self.assertEqual(emitted[0][0], QPoint(200, 150))
        finally:
            mask.close()

    def test_quick_sticker_position_uses_native_qt_screen_coordinates(self):
        from PySide6.QtCore import QPoint, QRect
        from config.config_manager import DEFAULTS
        from screenshot.mask_window import MaskWindow

        bounds = {"left": 0, "top": 0, "width": 3840, "height": 3240}
        monitors = [
            {"left": 0, "top": 0, "width": 3840, "height": 2160},
            {"left": 0, "top": 2160, "width": 1920, "height": 1080},
        ]
        screen_infos = [
            {"geometry": QRect(0, 0, 3072, 1728), "dpr": 1.25},
            {"geometry": QRect(0, 2160, 1920, 1080), "dpr": 1.0},
        ]
        settings = dict(DEFAULTS, crosshair=False, magnifier=False, sound=False)
        with patch("core.dpi.DisplayMapper.collect_screen_infos", return_value=screen_infos), \
                patch("screenshot.mask_window.visible_windows", return_value=[]):
            mask = MaskWindow(Image.new("RGB", (3840, 3240), "white"), bounds,
                              monitors, settings)
        emitted = []
        mask.quick_sticker_requested.connect(
            lambda image, position: emitted.append(position))
        try:
            mask.selection.rects.append(QRect(100, 2210, 50, 40))
            mask.trigger_quick_sticker()

            self.assertEqual(emitted, [QPoint(100, 2210)])
        finally:
            mask.close()

    def test_inline_sticker_position_uses_native_qt_screen_coordinates(self):
        from PySide6.QtCore import QPoint, QRect
        from config.config_manager import DEFAULTS
        from screenshot.mask_window import MaskWindow

        bounds = {"left": 0, "top": 0, "width": 3840, "height": 3240}
        monitors = [
            {"left": 0, "top": 0, "width": 3840, "height": 2160},
            {"left": 0, "top": 2160, "width": 1920, "height": 1080},
        ]
        screen_infos = [
            {"geometry": QRect(0, 0, 3072, 1728), "dpr": 1.25},
            {"geometry": QRect(0, 2160, 1920, 1080), "dpr": 1.0},
        ]
        with tempfile.TemporaryDirectory() as folder:
            settings = dict(DEFAULTS, save_dir=folder, inline_edit=True,
                            capture_after_selection="edit", magnifier=False,
                            crosshair=False, sound=False)
            with patch("core.dpi.DisplayMapper.collect_screen_infos", return_value=screen_infos), \
                    patch("screenshot.mask_window.visible_windows", return_value=[]):
                mask = MaskWindow(Image.new("RGB", (3840, 3240), "white"), bounds,
                                  monitors, settings)
            emitted = []
            mask.sticker_requested.connect(
                lambda image, position: emitted.append(position))
            try:
                mask.selection.rects.append(QRect(100, 2210, 50, 40))
                mask.complete()
                mask.session.inline_editor.execute("paste")

                self.assertEqual(emitted, [QPoint(100, 2210)])
            finally:
                mask.close()

    def test_preset_capture_sticker_position_uses_native_qt_screen_coordinates(self):
        from types import SimpleNamespace
        from PySide6.QtCore import QPoint, QRect
        from app.capture_flow import CaptureFlowMixin

        bounds = {"left": 0, "top": 0, "width": 3840, "height": 3240}
        monitors = [
            {"left": 0, "top": 0, "width": 3840, "height": 2160},
            {"left": 0, "top": 2160, "width": 1920, "height": 1080},
        ]
        screen_infos = [
            {"geometry": QRect(0, 0, 3072, 1728), "dpr": 1.25},
            {"geometry": QRect(0, 2160, 1920, 1080), "dpr": 1.0},
        ]
        app = SimpleNamespace(logger=Mock(), handle_capture_selection=Mock())
        with patch("core.dpi.DisplayMapper.collect_screen_infos", return_value=screen_infos):
            CaptureFlowMixin.complete_preset_capture(
                app, "monitor", Image.new("RGB", (3840, 3240), "white"),
                bounds, monitors, None, preferred_monitor=monitors[1])

        positions = app.handle_capture_selection.call_args.args[1]
        self.assertEqual(positions, [QPoint(0, 2160)])

    @unittest.skip("需真实桌面环境，见上方注释")
    def test_sticker_position_on_vertical_mixed_dpi_desktop(self):
        # 在 Qt 原点的小屏与上方负坐标 4K/125% 屏上验证新建贴图及重启恢复位置。
        pass

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
            mask = MaskWindow(Image.new("RGB", (3840, 3240), "white"), bounds, monitors, settings,
                              extra_intruders=[("弹出菜单", QRect(10, 10, 20, 20))])

        self.assertEqual([view.geometry() for view in mask.session.views],
                         [QRect(0, 0, 3072, 1728), QRect(0, 2160, 1920, 1080)])
        # 采集自检的外部线索也要落到其它显示器的遮罩上，否则只有主屏提示条会警示。
        self.assertEqual(mask.session.views[1].extra_intruders,
                         [("弹出菜单", QRect(10, 10, 20, 20))])
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

    def test_capture_action_hints_on_each_monitor_and_only_primary_has_shortcuts(self):
        """多显示器：每条提示条按当前设置显示功能键，快捷键只在主遮罩上创建。"""
        from config.config_manager import DEFAULTS
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
        settings = dict(DEFAULTS, inline_edit=False, crosshair=False, magnifier=False,
                        capture_recapture_shortcut="F2")
        with patch("screenshot.mask_window.visible_windows", return_value=[]), \
                patch("core.dpi.DisplayMapper.collect_screen_infos", return_value=screen_infos):
            mask = MaskWindow(Image.new("RGB", (400, 160), "blue"), bounds,
                              monitors, settings)
        mask.selection.rects.append(QRect(30, 40, 80, 60))
        mask.update_all()
        mask.show()
        self.app.processEvents()
        self.assertEqual(len(mask.session.views), 2)
        expected = [("F", "尺寸"), ("F2", "清除选择"),
                    ("E", "窗口编辑"), ("Alt+M", "多选"), ("Y", "仅复制")]
        for view in mask.session.views:
            self.assertEqual(view.capture_action_hints(), expected)
        # 快捷键是应用级的，只需主遮罩创建一份，避免多屏各注册一次互相抢键。
        self.assertIsNotNone(mask.capture_action_shortcuts)
        self.assertTrue(all(view.capture_action_shortcuts is None
                            for view in mask.session.views[1:]))
        # 提示条跟随放大镜：位置随光标变化，无需再"移入隐藏/移出恢复"。
        mask.position = QPoint(20, 20)
        mask.update_all()
        first = mask.session.views[0].magnifier_frame()
        mask.position = QPoint(150, 120)
        mask.update_all()
        self.assertNotEqual(first, mask.session.views[0].magnifier_frame())
        # 放大镜关闭时仍能算出一个跟随光标的框，提示条因此照常显示。
        mask.settings["magnifier"] = False
        mask.position = QPoint(150, 120)
        disabled_frame = mask.session.views[0].magnifier_frame()
        mask.settings["magnifier"] = True
        self.assertEqual(disabled_frame, mask.session.views[0].magnifier_frame())
        self.assertLessEqual((disabled_frame.topLeft() - mask.position).manhattanLength(), 400)
        # 放大镜尺寸可调：框随设置变大（仍受视图大小钳制），采样范围等比换算。
        from screenshot.magnifier_widget import sample_size
        mask.settings["magnifier_size"] = 100
        self.assertEqual(mask.session.views[0].magnifier_frame().width(), 104)
        self.assertEqual(sample_size(280), 40)
        self.assertEqual(sample_size(140), 20)
        mask.close()

    def test_info_bar_is_visible_only_on_cursor_monitor(self):
        from config.config_manager import DEFAULTS
        from screenshot.mask_window import MaskWindow

        bounds = {"left": 0, "top": 0, "width": 1800, "height": 700}
        monitors = [
            {"left": 0, "top": 0, "width": 900, "height": 700},
            {"left": 900, "top": 0, "width": 900, "height": 700},
        ]
        settings = {**DEFAULTS, "save_dir": tempfile.mkdtemp(),
                    "window_detection": False, "crosshair": False,
                    "magnifier": True, "sound": False}
        with patch("screenshot.mask_window.visible_windows", return_value=[]):
            mask = MaskWindow(Image.new("RGB", (1800, 700), "blue"), bounds,
                              monitors, settings)
        mask.show()
        self.app.processEvents()
        cursor = QPoint(1300, 350)
        for view in mask.session.views:
            view.position = QPoint(cursor)
        mask.update_all()
        bars = [view.info_bar for view in mask.session.views]
        visible = [index for index, bar in enumerate(bars) if bar.isVisible()]
        self.assertEqual(visible, [1])
        mask.close()

    def test_selection_spill_within_tolerance_trims_to_monitor(self):
        """最大化窗口不可见边框只多出几像素时按显示器收边；真正跨屏保持原样。"""
        from config.config_manager import DEFAULTS
        from screenshot.mask_window import MaskWindow

        bounds = {"left": 0, "top": 0, "width": 1920, "height": 1080}
        with patch("screenshot.mask_window.visible_windows", return_value=[]):
            mask = MaskWindow(Image.new("RGB", (1920, 1080), "white"), bounds,
                              [bounds], dict(DEFAULTS))
        try:
            # 最大化窗口的不可见边框：溢出小、保留率高 → 收边
            self.assertEqual(mask._trim_region_to_monitor(QRect(-8, -8, 1936, 1096)),
                             QRect(0, 0, 1920, 1080))
            # 溢出很大 → 真正跨屏，不动
            self.assertEqual(mask._trim_region_to_monitor(QRect(-500, 0, 2500, 1080)),
                             QRect(-500, 0, 2500, 1080))
            # 溢出虽小但只保留了不到一半面积 → 真正压在边上，不能收边
            self.assertEqual(mask._trim_region_to_monitor(QRect(1910, 500, 20, 100)),
                             QRect(1910, 500, 20, 100))
        finally:
            mask.close()

    def test_repeat_capture_crops_secondary_monitor_in_mixed_dpi_layout(self):
        from types import SimpleNamespace
        from main import Application
        from config.config_manager import DEFAULTS
        from screenshot.mask_window import MaskWindow

        bounds = {"left": -100, "top": 0, "width": 3200, "height": 1440}
        monitors = [{"left": -100, "top": 0, "width": 1920, "height": 1080},
                    {"left": 1820, "top": 0, "width": 1280, "height": 1440}]
        screen_infos = [{"geometry": QRect(0, 0, 1280, 720), "dpr": 1.5},
                        {"geometry": QRect(1280, 0, 1280, 1440), "dpr": 1.0}]
        fresh = Image.new("RGBA", (bounds["width"], bounds["height"]), (0, 0, 0, 0))
        fresh.paste((220, 20, 30, 255), (0, 0, 1920, 1080))
        fresh.paste((20, 180, 40, 255), (1920, 0, 3200, 1440))
        rect = [2200, 300, 80, 60]
        app = Application.__new__(Application)
        app.config = SimpleNamespace(data={**DEFAULTS, "last_capture_rect": rect,
                                           "capture_repeat_action": "save"})
        app.logger = Mock()
        app.save_capture_images = Mock()
        app.notify = Mock()

        with patch("app.capture_flow.capture", return_value=(fresh, bounds, monitors, None)) as grab, \
                patch("core.dpi.DisplayMapper.collect_screen_infos", return_value=screen_infos):
            app.show_mask("repeat")

        grab.assert_called_once_with(DEFAULTS["cursor"], alternatives=True, gap_fill="transparent")
        app.save_capture_images.assert_called_once()
        captured = app.save_capture_images.call_args.args[0][0][0]
        self.assertEqual(captured.size, (80, 60))
        self.assertEqual(captured.getpixel((0, 0)), (20, 180, 40, 255))
        self.assertEqual(captured.getpixel((79, 59)), (20, 180, 40, 255))


    def test_repeat_capture_after_secondary_drag_keeps_physical_monitor_origin(self):
        from types import SimpleNamespace
        from main import Application
        from screenshot.mask_window import MaskWindow
        from config.config_manager import DEFAULTS

        bounds = {"left": -100, "top": -40, "width": 3200, "height": 1440}
        monitors = [{"left": -100, "top": -40, "width": 1920, "height": 1080},
                    {"left": 1820, "top": -40, "width": 1280, "height": 1440}]
        screen_infos = [{"geometry": QRect(0, 0, 1280, 720), "dpr": 1.5},
                        {"geometry": QRect(1280, 0, 1280, 1440), "dpr": 1.0}]
        settings = {**DEFAULTS, "inline_edit": False, "capture_after_selection": "save",
                    "capture_repeat_action": "save", "crosshair": False,
                    "magnifier": False, "sound": False}
        with patch("screenshot.mask_window.visible_windows", return_value=[]), \
                patch("core.dpi.DisplayMapper.collect_screen_infos", return_value=screen_infos):
            mask = MaskWindow(Image.new("RGB", (3200, 1440), "white"), bounds, monitors, settings)
        app = Application.__new__(Application)
        app.config = SimpleNamespace(data=dict(settings, last_capture_rect=[]))
        app.config.save = Mock()
        app.mask = None
        app.logger = Mock()
        app.notify = Mock()
        app.remember_region = Application.remember_region.__get__(app)
        for view in mask.session.views:
            view.last_region.connect(app.remember_region)
        secondary = mask.session.views[1]
        mask.show()
        self.app.processEvents()
        QTest.mousePress(secondary, Qt.LeftButton, Qt.NoModifier, QPoint(20, 80))
        QTest.mouseMove(secondary, QPoint(70, 130))
        QTest.mouseRelease(secondary, Qt.LeftButton, Qt.NoModifier, QPoint(70, 130))
        self.assertEqual(app.config.data["last_capture_rect"], [1840, 40, 51, 51])

        fresh = Image.new("RGBA", (3200, 1440), (0, 0, 0, 0))
        fresh.paste((200, 10, 20, 255), (0, 0, 1920, 1080))
        fresh.paste((10, 190, 30, 255), (1920, 0, 3200, 1440))
        app.save_capture_images = Mock()
        with patch("app.capture_flow.capture", return_value=(fresh, bounds, monitors, None)):
            Application.show_mask(app, "repeat")
        crop = app.save_capture_images.call_args.args[0][0][0]
        self.assertEqual(crop.size, (51, 51))
        self.assertEqual(crop.getpixel((0, 0)), (10, 190, 30, 255))
        self.assertFalse(mask.isVisible())

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
        with patch("app.capture_flow.QTimer.singleShot") as schedule:
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

    def test_show_mask_connects_recapture_for_every_monitor_view(self):
        from types import SimpleNamespace
        from main import Application

        primary, secondary = Mock(), Mock()
        primary.session = SimpleNamespace(views=[primary, secondary])
        primary.recapture_requested = Mock()
        secondary.recapture_requested = Mock()
        primary.last_region = Mock()
        secondary.last_region = Mock()
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
        # show_mask 已搬到 app.capture_flow，构造用的 MaskWindow 也要在那里打桩；
        # main.MaskWindow 仍保留给 main 里的 isinstance 判断。
        with patch("app.capture_flow.capture", return_value=(image, {}, [], None)), \
                patch("main.MaskWindow", return_value=primary), \
                patch("app.capture_flow.MaskWindow", return_value=primary), \
                patch("app.capture_flow.QTimer.singleShot"):
            app.show_mask("capture")

        primary.recapture_requested.connect.assert_called_once_with(app.restart_capture)
        secondary.recapture_requested.connect.assert_called_once_with(app.restart_capture)
        primary.last_region.connect.assert_called_once_with(app.remember_region)
        secondary.last_region.connect.assert_called_once_with(app.remember_region)
        secondary.last_region.connect.call_args.args[0]([2200, 300, 80, 60])
        app.remember_region.assert_called_once_with([2200, 300, 80, 60])

    def test_display_mapper_full_cross_product_of_screen_variables(self):
        """全量笛卡尔积穷举：屏型 × 屏数 × 排列 × 间隙 × 原点 × 主屏顺序。

        维度：8 种屏型（1080/2K/4K × 100/125/150/175%，含「2K 面板跑 1080+125%」）× 屏数 2/3/4
        × 7 种排列（横排/竖排/网格/对角错位/带间隙/负原点/镜像）× 间隙 0/24/40 × 原点 4 种。
        每种组合逐屏断言：左上角对应、屏内往返可逆、不越出物理矩形、Qt 逻辑边长×dpr==物理边长。
        """
        from itertools import combinations_with_replacement
        from core.dpi import DisplayMapper

        profiles = (("1080@100%", 1920, 1080, 1.0),
                    ("1080@125%", 1920, 1080, 1.25),
                    ("1080@150%", 1920, 1080, 1.5),
                    ("2K@100%", 2560, 1440, 1.0),
                    ("2K@125%", 2560, 1440, 1.25),
                    ("4K@125%", 3840, 2160, 1.25),
                    ("4K@150%", 3840, 2160, 1.5),
                    ("4K@175%", 3840, 2160, 1.75))
        arrangements = ("row", "column", "grid", "staircase", "gap", "negative", "mirrored")
        gaps = (0, 24, 40)
        origins = ((0, 0), (-1920, 0), (0, -1080), (-2560, -1440))

        def build(specs, arrangement, gap, origin, model="primary"):
            monitors, screens = [], []
            x = y = nx = ny = 0
            primary_dpr = (tuple(reversed(specs)) if arrangement == "mirrored" else specs)[0][3]
            origin_physical_x, origin_physical_y = 0, 0
            native_clamp_x = native_clamp_y = 0
            columns = 2 if arrangement == "grid" else 1
            row_height = max(spec[2] for spec in specs) if arrangement == "grid" else 0
            native_row_height = (max(round(spec[2] / spec[3]) for spec in specs)
                                 if arrangement == "grid" else 0)
            ordered = tuple(reversed(specs)) if arrangement == "mirrored" else specs
            for index, (name, width, height, dpr) in enumerate(ordered):
                native_width, native_height = round(width / dpr), round(height / dpr)
                monitors.append({"left": x + origin[0], "top": y + origin[1],
                                 "width": width, "height": height})
                if model == "compact" or arrangement == "grid":
                    native_left, native_top = nx, ny
                else:
                    # 真实 Qt：物理排列按主屏缩放摆放，且保证逻辑矩形不重叠
                    #（Qt 在混合 DPI 下正是靠这一点避免屏幕互相压盖）。
                    native_left = round((x - origin_physical_x) / dpr)
                    native_top = round((y - origin_physical_y) / dpr)
                    if arrangement in ("row", "gap", "negative", "mirrored"):
                        native_left = max(native_left, native_clamp_x)
                        native_top = 0
                        native_clamp_x = native_left + native_width
                    elif arrangement == "column":
                        native_top = max(native_top, native_clamp_y)
                        native_left = 0
                        native_clamp_y = native_top + native_height
                    else:
                        native_left = max(native_left, native_clamp_x)
                        native_top = max(native_top, native_clamp_y)
                        native_clamp_x = native_left + native_width
                        native_clamp_y = native_top + native_height
                screens.append({"geometry": QRect(native_left + origin[0], native_top + origin[1],
                                                  native_width, native_height), "dpr": dpr})
                if arrangement in ("row", "gap", "negative", "mirrored"):
                    x += width + gap
                    nx += native_width
                elif arrangement == "staircase":
                    x += width + gap
                    y += height + gap
                    nx += native_width
                    ny += native_height
                elif arrangement == "column":
                    y += height + gap
                    ny += native_height
                else:
                    if index % columns == columns - 1:
                        x = 0
                        y += row_height
                        nx = 0
                        ny += native_row_height
                    else:
                        x += width + gap
                        nx += native_width
            bounds = {"left": min(m["left"] for m in monitors),
                      "top": min(m["top"] for m in monitors),
                      "width": max(m["left"] + m["width"] for m in monitors) - min(m["left"] for m in monitors),
                      "height": max(m["top"] + m["height"] for m in monitors) - min(m["top"] for m in monitors)}
            scales = [spec[3] for spec in ordered]
            return monitors, screens, bounds, scales

        checked = 0
        for count in (2, 3, 4):
            for specs in combinations_with_replacement(profiles, count):
                for arrangement in arrangements:
                    for gap in gaps:
                        for origin in origins:
                          for model in ("own_scaled", "compact"):
                            monitors, screens, bounds, scales = build(specs, arrangement, gap, origin, model)
                            mapper = DisplayMapper(bounds, monitors, screens, monitor_scales=scales)
                            name = (f"{count}屏 {arrangement} gap={gap} origin={origin} {model} "
                                    + "+".join(spec[0] for spec in specs))
                            self.assertEqual(len(mapper.mappings), count, name)
                            for mapping in mapper.mappings:
                                self.assertEqual(
                                    mapper.native_global_to_physical_global(mapping.native.topLeft()),
                                    mapping.physical.topLeft(), name)
                                physical = mapper.native_global_to_physical_global(mapping.native.center())
                                self.assertTrue(mapping.physical.contains(physical), name)
                                self.assertEqual(mapper.physical_global_to_native_global(physical),
                                                 mapping.native.center(), name)
                                self.assertAlmostEqual(mapping.native.width() * mapping.dpr,
                                                       float(mapping.physical.width()), delta=2, msg=name)
                                self.assertAlmostEqual(mapping.native.height() * mapping.dpr,
                                                       float(mapping.physical.height()), delta=2, msg=name)
                            checked += 1
        # 缩放上报不可信（GetDpiForMonitor 失败 / 驱动异常）时只要求不崩、逐屏可逆，
        # 不要求尺寸不变量（软惩罚会退回到尺寸+位置择优，这正是生产降级路径）。
        for specs in (profiles[0:2], profiles[3:5]):
            monitors, screens, bounds, _ = build(specs, "row", 0, (0, 0), "own_scaled")
            mapper = DisplayMapper(bounds, monitors, screens, monitor_scales=None)
            self.assertEqual(len(mapper.mappings), len(specs))
            for mapping in mapper.mappings:
                physical = mapper.native_global_to_physical_global(mapping.native.center())
                self.assertEqual(mapper.physical_global_to_native_global(physical),
                                 mapping.native.center())
            checked += 1
        self.assertGreaterEqual(checked, 40000, "组合数偏少，检查生成器")
        print(f"全量笛卡尔积组合数: {checked}")
    def test_display_mapper_exhaustive_two_three_four_screen_combinations(self):
        """穷举 2/3/4 块屏的所有分辨率×DPI 组合与三种排列方式。

        用 6 种常见屏型（1080@100、2K@100、2K 面板跑 1080+125%、4K@125、4K@150、1080@150）
        做组合（允许重复），每种组合再按横排 / 竖排 / 2x2 网格三种排列生成布局，逐个断言：
        逐屏左上角对应、屏内往返可逆、不越出该屏物理矩形、Qt 逻辑边长×dpr==物理边长。
        """
        from itertools import combinations_with_replacement
        from core.dpi import DisplayMapper

        profiles = (("1080@100", 1920, 1080, 1.0),
                    ("2K@100", 2560, 1440, 1.0),
                    ("1080+125%", 1920, 1080, 1.25),
                    ("4K@125", 3840, 2160, 1.25),
                    ("4K@150", 3840, 2160, 1.5),
                    ("1080@150", 1920, 1080, 1.5))

        def build(specs, arrangement):
            monitors, screens = [], []
            position = QPoint(0, 0)
            native_position = QPoint(0, 0)
            columns = 2 if arrangement == "grid" else 1
            # 排列维度：staircase=逐块沿对角线错位半屏；gap=显示器之间留物理间隙；
            # negative=整体挪到负坐标（主屏在右下、副屏在左上）。
            gapp = 40 if arrangement == "gap" else 0
            step_x = 0 if arrangement in ("column",) else 0
            row_height = max(spec[2] for spec in specs) if arrangement == "grid" else 0
            native_row_height = max(round(spec[2] / spec[3]) for spec in specs) if arrangement == "grid" else 0
            for index, (name, width, height, dpr) in enumerate(specs):
                native_width, native_height = round(width / dpr), round(height / dpr)
                cell = QPoint(position.x(), position.y())
                monitors.append({"left": cell.x(), "top": cell.y(),
                                 "width": width, "height": height})
                screens.append({"geometry": QRect(native_position.x(), native_position.y(),
                                                  native_width, native_height), "dpr": dpr})
                if arrangement == "row":
                    position += QPoint(width + gapp, 0)
                    native_position += QPoint(native_width, 0)
                elif arrangement == "staircase":
                    # 对角错开且不重叠（笔记本 + 斜上方外接屏这种摆法）。
                    position += QPoint(width + gapp, height + gapp)
                    native_position += QPoint(native_width, native_height)
                elif arrangement == "column":
                    position += QPoint(0, height + gapp)
                    native_position += QPoint(0, native_height)
                else:
                    if index % columns == columns - 1:
                        position = QPoint(0, position.y() + row_height)
                        native_position = QPoint(0, native_position.y() + native_row_height)
                    else:
                        position += QPoint(width, 0)
                        native_position += QPoint(native_width, 0)
            bounds = {"left": 0, "top": 0,
                      "width": max(m["left"] + m["width"] for m in monitors) - min(m["left"] for m in monitors),
                      "height": max(m["top"] + m["height"] for m in monitors) - min(m["top"] for m in monitors)}
            return monitors, screens, bounds

        checked = 0
        for count in (2, 3, 4):
            for specs in combinations_with_replacement(profiles, count):
                label = "+".join(spec[0] for spec in specs)
                for arrangement in ("row", "column", "grid", "staircase", "gap", "negative"):
                    monitors, screens, bounds = build(specs, arrangement)
                    mapper = DisplayMapper(bounds, monitors, screens)
                    name = f"{count}屏 {arrangement} {label}"
                    self.assertEqual(len(mapper.mappings), count, name)
                    for mapping in mapper.mappings:
                        self.assertEqual(
                            mapper.native_global_to_physical_global(mapping.native.topLeft()),
                            mapping.physical.topLeft(), name)
                        for native_point in (mapping.native.topLeft(), mapping.native.center(),
                                             mapping.native.bottomRight() - QPoint(1, 1)):
                            physical = mapper.native_global_to_physical_global(native_point)
                            self.assertTrue(mapping.physical.contains(physical),
                                            f"{name} {native_point} -> {physical}")
                            self.assertEqual(
                                mapper.physical_global_to_native_global(physical),
                                native_point, name)
                        self.assertAlmostEqual(mapping.native.width() * mapping.dpr,
                                               float(mapping.physical.width()), delta=2, msg=name)
                        self.assertAlmostEqual(mapping.native.height() * mapping.dpr,
                                               float(mapping.physical.height()), delta=2, msg=name)
                    checked += 1
        self.assertGreaterEqual(checked, 1200, "组合数偏少，检查生成器")
    def test_screen_change_moves_stickers_back_into_remaining_desktop(self):
        """显示器被拔掉后，落在它上面的贴图要搬回剩余桌面，而不是继续挂在不存在的位置。

        覆盖：屏被拔掉（贴图孤立 → 搬回并夹在剩余桌面内）、屏被插入/缩放变化（贴图仍在桌面内 → 不动）。
        """
        from PySide6.QtCore import QRect
        from PySide6.QtWidgets import QApplication
        from main import Application

        screen = Mock()
        screen.availableGeometry.return_value = QRect(0, 0, 1920, 1080)
        app = Application.__new__(Application)
        app.logger = Mock()
        app.watch_screens = Mock()
        app.stickers = Mock()
        outside = Mock()
        outside.frameGeometry.return_value = QRect(2600, 300, 200, 100)  # 已拔掉的右屏上
        inside = Mock()
        inside.frameGeometry.return_value = QRect(100, 100, 200, 100)
        app.stickers.items = [outside, inside]
        with patch("core.screen_mapping.clear_cache"), \
                patch.object(QApplication, "screens", return_value=[screen]):
            app.handle_screen_change()
        outside.move.assert_called_once_with(1720, 300)  # 夹进 1920x1080 的可用区域
        inside.move.assert_not_called()
        self.assertEqual(outside.apply_style.call_count, 1)

    def test_display_mapper_survives_screen_add_remove_and_parameter_change(self):
        """屏被拔掉/插入、某屏改分辨率或缩放、整体排列位移后，映射仍逐屏可逆。

        这是「运行中显示器变化」的映射层验证：每次变化后重新构建 DisplayMapper，
        逐屏断言左上角对应、往返可逆、不越出物理矩形、逻辑边长×dpr==物理边长。
        """
        from core.dpi import DisplayMapper

        def layout(specs):
            monitors, screens, x, nx = [], [], 0, 0
            for width, height, dpr in specs:
                native_width, native_height = round(width / dpr), round(height / dpr)
                monitors.append({"left": x, "top": 0, "width": width, "height": height})
                screens.append({"geometry": QRect(nx, 0, native_width, native_height), "dpr": dpr})
                x += width
                nx += native_width
            bounds = {"left": 0, "top": 0, "width": x, "height": max(m["height"] for m in monitors)}
            return monitors, screens, bounds

        four_k_125 = (3840, 2160, 1.25)
        full_hd = (1920, 1080, 1.0)
        two_k_150 = (2560, 1440, 1.5)
        two_k_100 = (2560, 1440, 1.0)
        base = (four_k_125, full_hd, two_k_150)
        transitions = (
            ("基线三屏", base),
            ("拔掉中间屏", (four_k_125, two_k_150)),
            ("插入第四块屏", base + (full_hd,)),
            ("第三块屏缩放 150%→125%", (four_k_125, full_hd, (2560, 1440, 1.25))),
            ("第二块屏分辨率 1080→1440 且缩放 100%→125%", (four_k_125, (2560, 1440, 1.25), two_k_150)),
            ("主屏换成 2K@100% 并减少一块", (two_k_100, full_hd)),
        )
        for name, specs in transitions:
            monitors, screens, bounds = layout(specs)
            mapper = DisplayMapper(bounds, monitors, screens)
            self.assertEqual(len(mapper.mappings), len(specs), name)
            for mapping in mapper.mappings:
                self.assertEqual(
                    mapper.native_global_to_physical_global(mapping.native.topLeft()),
                    mapping.physical.topLeft(), name)
                for native_point in (mapping.native.topLeft(), mapping.native.center(),
                                     mapping.native.bottomRight() - QPoint(1, 1)):
                    physical = mapper.native_global_to_physical_global(native_point)
                    self.assertTrue(mapping.physical.contains(physical), f"{name} {native_point}")
                    self.assertEqual(mapper.physical_global_to_native_global(physical),
                                     native_point, name)
                self.assertAlmostEqual(mapping.native.width() * mapping.dpr,
                                       float(mapping.physical.width()), delta=2, msg=name)
    def test_screen_change_refreshes_mapping_cache_and_stickers(self):
        """显示器分辨率/缩放变化后要清掉映射缓存并让贴图按新 dpr 重新对齐。

        中间某块屏改了分辨率或缩放时，贴图尺寸只在拖动时重算是不够的 —— 这里断言信号处理
        会清缓存、逐张刷新贴图，并在刷新后重新挂上新接入显示器的信号。
        """
        from main import Application

        app = Application.__new__(Application)
        app.stickers = Mock()
        app.stickers.items = [Mock(), Mock()]
        for item in app.stickers.items:
            item.frameGeometry.return_value = QRect(10, 10, 100, 80)
        app.logger = Mock()
        app.watch_screens = Mock()
        with patch("core.screen_mapping.clear_cache") as clear:
            app.handle_screen_change()
        clear.assert_called_once()
        for item in app.stickers.items:
            item.apply_style.assert_called_once()
        app.watch_screens.assert_called_once()

        # 已被 Qt 回收的贴图不能让刷新中断。
        broken = Mock()
        broken.apply_style.side_effect = RuntimeError("wrapped C/C++ object deleted")
        healthy = Mock()
        healthy.frameGeometry.return_value = QRect(10, 10, 100, 80)
        app.stickers.items = [broken, healthy]
        with patch("core.screen_mapping.clear_cache"):
            app.handle_screen_change()
        self.assertEqual(app.stickers.items[1].apply_style.call_count, 1)
    def test_display_mapper_round_trips_across_screen_dpi_matrix(self):
        """多种屏幕 / DPI 组合下，物理像素与 Qt 原生坐标必须逐屏可逆、比例正确。

        三个空间：Qt 原生全局(native，QCursor.pos/setGeometry 用)、遮罩内部紧凑坐标(logical，
        只在遮罩自己算局部时用)、Win32/mss 物理像素。跨空间换算只能走 native，混用就会在
        某块屏上整体偏移（UIA 高亮飘移、截图/贴图错位）。
        """
        from core.dpi import DisplayMapper

        cases = (
            ("单屏 100%", [{"left": 0, "top": 0, "width": 1920, "height": 1080}],
             [{"geometry": QRect(0, 0, 1920, 1080), "dpr": 1.0}],
             {"left": 0, "top": 0, "width": 1920, "height": 1080}),
            ("单屏 150%", [{"left": 0, "top": 0, "width": 3840, "height": 2160}],
             [{"geometry": QRect(0, 0, 2560, 1440), "dpr": 1.5}],
             {"left": 0, "top": 0, "width": 3840, "height": 2160}),
            ("水平双 100%", [{"left": 0, "top": 0, "width": 1920, "height": 1080},
                          {"left": 1920, "top": 0, "width": 1920, "height": 1080}],
             [{"geometry": QRect(0, 0, 1920, 1080), "dpr": 1.0},
              {"geometry": QRect(1920, 0, 1920, 1080), "dpr": 1.0}],
             {"left": 0, "top": 0, "width": 3840, "height": 1080}),
            ("水平混合 DPI：左 4K@125% + 右 1080p@100%",
             [{"left": 0, "top": 0, "width": 3840, "height": 2160},
              {"left": 3840, "top": 0, "width": 1920, "height": 1080}],
             [{"geometry": QRect(0, 0, 3072, 1728), "dpr": 1.25},
              {"geometry": QRect(3072, 0, 1920, 1080), "dpr": 1.0}],
             {"left": 0, "top": 0, "width": 5760, "height": 2160}),
            ("垂直混合 DPI：上 4K@125% + 下 1080p@100%",
             [{"left": 0, "top": -2160, "width": 3840, "height": 2160},
              {"left": 0, "top": 0, "width": 1920, "height": 1080}],
             [{"geometry": QRect(0, -1728, 3072, 1728), "dpr": 1.25},
              {"geometry": QRect(0, 0, 1920, 1080), "dpr": 1.0}],
             {"left": 0, "top": -2160, "width": 3840, "height": 3240}),
            ("2K 面板跑 1080 + 125%（右屏降分辨率再缩放）",
             [{"left": 0, "top": 0, "width": 2560, "height": 1440},
              {"left": 2560, "top": 0, "width": 1920, "height": 1080}],
             [{"geometry": QRect(0, 0, 2560, 1440), "dpr": 1.0},
              {"geometry": QRect(2560, 0, 1536, 864), "dpr": 1.25}],
             {"left": 0, "top": 0, "width": 4480, "height": 1440}),
            ("175% 缩放单屏", [{"left": 0, "top": 0, "width": 3840, "height": 2160}],
             [{"geometry": QRect(0, 0, 2194, 1234), "dpr": 1.75}],
             {"left": 0, "top": 0, "width": 3840, "height": 2160}),
            ("三屏混合：4K@150% + 2K@125% + 1080@100%",
             [{"left": 0, "top": 0, "width": 3840, "height": 2160},
              {"left": 3840, "top": 0, "width": 2560, "height": 1440},
              {"left": 6400, "top": 0, "width": 1920, "height": 1080}],
             [{"geometry": QRect(0, 0, 2560, 1440), "dpr": 1.5},
              {"geometry": QRect(2560, 0, 2048, 1152), "dpr": 1.25},
              {"geometry": QRect(4608, 0, 1920, 1080), "dpr": 1.0}],
             {"left": 0, "top": 0, "width": 8320, "height": 2160}),
            ("四屏 2x2：4K@125% 左上 / 1080@100% 右上 / 2K@150% 左下 / 1080@125% 右下",
             [{"left": 0, "top": 0, "width": 3840, "height": 2160},
              {"left": 3840, "top": 0, "width": 1920, "height": 1080},
              {"left": 0, "top": 2160, "width": 2560, "height": 1440},
              {"left": 2560, "top": 2160, "width": 1920, "height": 1080}],
             [{"geometry": QRect(0, 0, 3072, 1728), "dpr": 1.25},
              {"geometry": QRect(3072, 0, 1920, 1080), "dpr": 1.0},
              {"geometry": QRect(0, 1728, 1706, 960), "dpr": 1.5},
              {"geometry": QRect(1706, 1728, 1536, 864), "dpr": 1.25}],
             {"left": 0, "top": 0, "width": 5760, "height": 3240}),
            ("垂直混合 DPI + Qt 原生取整间隙（下屏 native y=32）",
             [{"left": 0, "top": -2160, "width": 3840, "height": 2160},
              {"left": 0, "top": 0, "width": 1920, "height": 1080}],
             [{"geometry": QRect(0, -1728, 3072, 1728), "dpr": 1.25},
              {"geometry": QRect(0, 32, 1920, 1080), "dpr": 1.0}],
             {"left": 0, "top": -2160, "width": 3840, "height": 3240}),
        )
        for name, monitors, screens, bounds in cases:
            mapper = DisplayMapper(bounds, monitors, screens)
            self.assertEqual(len(mapper.mappings), len(monitors), name)
            for mapping in mapper.mappings:
                self.assertEqual(
                    mapper.native_global_to_physical_global(mapping.native.topLeft()),
                    mapping.physical.topLeft(), name)
                points = (mapping.native.topLeft(), mapping.native.center(),
                          mapping.native.bottomRight() - QPoint(1, 1))
                for native_point in points:
                    physical = mapper.native_global_to_physical_global(native_point)
                    self.assertTrue(mapping.physical.contains(physical),
                                    f"{name} {native_point} -> {physical} 越出该屏物理矩形")
                    self.assertEqual(mapper.physical_global_to_native_global(physical),
                                     native_point, name)
                # 尺寸不变量：Qt 逻辑边长 × dpr == 物理边长（窗口/贴图尺寸必须按这个关系换算，
                # 否则在缩放屏上会把物理像素当成逻辑尺寸，图片被放大 dpr 倍）。
                self.assertAlmostEqual(mapping.native.width() * mapping.dpr,
                                       float(mapping.physical.width()), delta=2, msg=name)
                self.assertAlmostEqual(mapping.native.height() * mapping.dpr,
                                       float(mapping.physical.height()), delta=2, msg=name)
                self.assertEqual(
                    mapper.physical_global_to_native_global(mapping.physical.center()),
                    mapping.native.center(), name)
    def test_hover_cursor_conversion_uses_native_qt_space(self):
        """混合 DPI 纵向下，悬停/UIA 的光标换算必须走 Qt 原生(native)空间。

        紧凑(compact)后的 logical 与 Qt 原生 native 只要差一段（Qt 在混合 DPI 下会给屏幕之间留下
        取整间隙、或按物理排列摆位），用 logical 换算出的物理点就会整体偏移 —— 在小屏上正是
        UIA 高亮飘移的原因。
        """
        from core.dpi import DisplayMapper

        bounds = {"left": 0, "top": -2160, "width": 3840, "height": 3240}
        monitors = [{"left": 0, "top": -2160, "width": 3840, "height": 2160},
                    {"left": 0, "top": 0, "width": 1920, "height": 1080}]
        # Qt 原生：上屏 4K@125%（3072x1728），下屏 1080p@100%，但原生坐标带 32px 取整间隙。
        screen_infos = [{"geometry": QRect(0, -1728, 3072, 1728), "dpr": 1.25},
                        {"geometry": QRect(0, 32, 1920, 1080), "dpr": 1.0}]
        mapper = DisplayMapper(bounds, monitors, screen_infos)
        lower = next(m for m in mapper.mappings if m.physical.width() == 1920)
        # 紧凑空间把下屏贴到上屏正下方（0），与 Qt 原生的 32 不同 —— 这正是坑。
        self.assertEqual(lower.logical.getRect(), (0, 0, 1920, 1080))
        self.assertEqual(lower.native.getRect(), (0, 32, 1920, 1080))
        cursor = QPoint(500, 532)  # 小屏上、距原生上边缘 500px 处
        self.assertEqual(mapper.native_global_to_physical_global(cursor), QPoint(500, 500))
        self.assertEqual(mapper.physical_global_to_native_global(QPoint(500, 500)), cursor)
        # 对照：走紧凑 logical 会整体偏 32px，就是要避免的旧行为。
        self.assertEqual(mapper.logical_global_to_physical_global(cursor), QPoint(500, 532))
    @unittest.skip("需真实桌面环境，见上方注释")
    def test_uia_small_control_hover_on_vertical_mixed_dpi_desktop(self):
        # 在上下堆叠的 4K 125% 与 100% 小屏上检查顶部高亮位置及细小 UIA 控件切换。
        pass

    def test_uia_fullscreen_candidate_on_secondary_monitor_enters_inline_editor(self):
        from PySide6.QtCore import QPoint, QRect, Qt
        from PySide6.QtTest import QTest
        from config.config_manager import DEFAULTS
        from screenshot.mask_window import MaskWindow

        bounds = {"left": 0, "top": 0, "width": 160, "height": 100}
        monitors = [{"left": 0, "top": 0, "width": 80, "height": 100},
                    {"left": 80, "top": 0, "width": 80, "height": 100}]
        screen_infos = [{"geometry": QRect(0, 0, 80, 100), "dpr": 1.0},
                        {"geometry": QRect(80, 0, 80, 100), "dpr": 1.0}]
        settings = {**DEFAULTS, "inline_edit": True, "capture_after_selection": "save",
                    "magnifier": False, "crosshair": False, "sound": False,
                    "window_detection": True, "window_hover_detect": True}
        with patch("screenshot.mask_window.visible_windows", return_value=[]), \
                patch("core.dpi.DisplayMapper.collect_screen_infos", return_value=screen_infos):
            mask = MaskWindow(Image.new("RGB", (160, 100), "blue"), bounds, monitors, settings)
        try:
            secondary = mask.session.views[1]
            self.assertIsNone(secondary.capture_action_shortcuts)
            secondary.hover_rect = QRect(80, 0, 80, 100)
            mask.show()
            self.app.processEvents()
            QTest.mousePress(secondary, Qt.LeftButton, Qt.NoModifier, QPoint(20, 20))
            QTest.mouseRelease(secondary, Qt.LeftButton, Qt.NoModifier, QPoint(20, 20))
            self.assertIsNotNone(mask.session.inline_editor)
            self.assertIs(mask.session.inline_editor.view, secondary)
            self.assertIsNone(mask.session.inline_editor.last_path)
            self.assertFalse(mask.session.element_selected)
            self.assertFalse(mask.session.views[0].capture_action_shortcuts["multi_select"].isEnabled())
        finally:
            mask.close()

    def test_uia_candidate_spanning_monitors_uses_standalone_editor(self):
        from PySide6.QtCore import QPoint, QRect, Qt
        from PySide6.QtTest import QTest
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
        try:
            secondary = mask.session.views[1]
            secondary.hover_rect = QRect(70, 10, 30, 30)
            edited = []
            secondary.edit_requested.connect(lambda images, positions: edited.append((images, positions)))
            mask.show()
            self.app.processEvents()
            QTest.mousePress(secondary, Qt.LeftButton, Qt.NoModifier, QPoint(5, 20))
            QTest.mouseRelease(secondary, Qt.LeftButton, Qt.NoModifier, QPoint(5, 20))
            self.assertEqual(len(edited), 1)
            self.assertEqual(edited[0][0][0][0].size, (30, 30))
            self.assertIsNone(mask.session.inline_editor)
            self.assertFalse(mask.isVisible())
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
                with patch.object(second.mapper, "native_global_to_physical_global",
                                  return_value=QPoint(120, 20)):
                    second.poll_hover()
                # 非 primary 的遮罩也必须能识别，否则主屏永远没有高亮。
                self.assertEqual(second.hover_rect, QRect(120, 10, 60, 50))
            finally:
                mask.close()

    def test_uia_physical_rect_prefers_dwm_visible_bounds_and_falls_back(self):
        import ctypes
        from ctypes import wintypes
        from types import SimpleNamespace
        from core.window_uia import physical_rect_of, reset_visible_frame_cache

        class DwmCall:
            def __init__(self, result, rect):
                self.result = result
                self.rect = rect
                self.args = None

            def __call__(self, hwnd, attribute, output, size):
                self.args = (hwnd, attribute, size)
                target = ctypes.cast(output, ctypes.POINTER(wintypes.RECT)).contents
                target.left, target.top, target.right, target.bottom = self.rect
                return self.result

        # DWM 函数对象现在会被缓存，切换桩实现前先清缓存，否则第二次调用仍用上一次的函数。
        visible_call = DwmCall(0, (0, 0, 1920, 1080))
        reset_visible_frame_cache()
        try:
            with patch("core.window_uia.ctypes.WinDLL",
                       return_value=SimpleNamespace(DwmGetWindowAttribute=visible_call)):
                self.assertEqual(physical_rect_of(0x123456789), (0, 0, 1920, 1080))
        finally:
            reset_visible_frame_cache()
        self.assertEqual(visible_call.args[0].value, 0x123456789)
        self.assertEqual(visible_call.args[1], 9)

        failed_call = DwmCall(1, (0, 0, 0, 0))

        def get_window_rect(hwnd, output):
            target = ctypes.cast(output, ctypes.POINTER(wintypes.RECT)).contents
            target.left, target.top, target.right, target.bottom = (-8, -8, 1928, 1088)
            return 1

        reset_visible_frame_cache()
        try:
            with patch("core.window_uia.ctypes.WinDLL",
                       return_value=SimpleNamespace(DwmGetWindowAttribute=failed_call)), \
                    patch("core.window_uia.ctypes.windll.user32.GetWindowRect",
                          side_effect=get_window_rect):
                self.assertEqual(physical_rect_of(42), (-8, -8, 1928, 1088))
        finally:
            reset_visible_frame_cache()

    def test_theme_setting_and_native_unchecked_checkbox(self):
        from PySide6.QtGui import QImage, QPainter, QPalette
        from PySide6.QtWidgets import QCheckBox, QStyle, QStyleOptionButton
        from config.config_manager import DEFAULTS
        from ui.theme import apply_theme

        self.assertEqual(DEFAULTS["theme"], "system")
        system_window_color = self.app.palette().color(QPalette.Window).name()
        apply_theme(self.app, "dark")
        self.assertEqual(self.app.palette().color(QPalette.Window).name(), "#202124")
        dark_stylesheet = self.app.styleSheet()
        self.assertIn("QLineEdit", dark_stylesheet)
        self.assertIn("QCheckBox::indicator", dark_stylesheet)
        self.assertIn("QRadioButton::indicator", dark_stylesheet)
        self.assertIn("checkbox-check.svg", dark_stylesheet)
        self.assertIn("radio-dot.svg", dark_stylesheet)
        self.assertIn("background-color: #171717", dark_stylesheet)
        self.assertIn("color: #e8eaed", dark_stylesheet)
        apply_theme(self.app, "light")
        self.assertEqual(self.app.palette().color(QPalette.Window).name(), "#f0f0f0")
        self.assertNotIn("QLineEdit", self.app.styleSheet())
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

    def test_dark_theme_spinbox_keeps_native_side_by_side_arrows(self):
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import (QDoubleSpinBox, QSpinBox, QStyle,
                                       QStyleOptionSpinBox)
        from ui.theme import apply_theme

        apply_theme(self.app, "dark")
        try:
            stylesheet = self.app.styleSheet()
            self.assertIn("QSpinBox::up-button", stylesheet)
            self.assertIn("QSpinBox::down-button", stylesheet)
            self.assertIn("QSpinBox::up-button:hover", stylesheet)
            self.assertIn("QSpinBox::down-button:hover", stylesheet)
            self.assertIn("QSpinBox::up-button:pressed", stylesheet)
            self.assertIn("QSpinBox::down-button:pressed", stylesheet)
            self.assertIn("QSpinBox::up-button:hover", stylesheet)
            self.assertIn("QSpinBox::down-button:hover", stylesheet)
            self.assertIn("QSpinBox::up-button:pressed", stylesheet)
            self.assertIn("QSpinBox::down-button:pressed", stylesheet)
            self.assertNotIn("QSpinBox::up-arrow", stylesheet)
            self.assertNotIn("QSpinBox::down-arrow", stylesheet)
            self.assertIn("right: 28px", stylesheet)
            self.assertIn("right: 0px", stylesheet)
            for widget_type in (QSpinBox, QDoubleSpinBox):
                spinbox = widget_type()
                spinbox.setRange(0, 100)
                spinbox.setValue(50)
                spinbox.resize(150, 36)
                spinbox.show()
                self.app.processEvents()
                option = QStyleOptionSpinBox()
                option.initFrom(spinbox)
                option.rect = spinbox.rect()
                up = spinbox.style().subControlRect(
                    QStyle.CC_SpinBox, option, QStyle.SC_SpinBoxUp, spinbox)
                down = spinbox.style().subControlRect(
                    QStyle.CC_SpinBox, option, QStyle.SC_SpinBoxDown, spinbox)
                with self.subTest(widget=widget_type.__name__):
                    self.assertGreaterEqual(up.width(), 28)
                    self.assertGreaterEqual(up.height(), 28)
                    self.assertGreaterEqual(down.width(), 28)
                    self.assertGreaterEqual(down.height(), 28)
                    self.assertFalse(up.intersects(down))
                    self.assertLessEqual(abs(up.center().y() - down.center().y()), 2,
                                         (widget_type.__name__, up, down))
                    self.assertLess(up.center().x(), down.center().x(),
                                       (widget_type.__name__, up, down))
                    image = spinbox.grab().toImage()

                    def visible_arrow_point(rect):
                        pixels = [QPoint(x, y)
                                  for y in range(rect.top(), rect.bottom() + 1)
                                  for x in range(rect.left(), rect.right() + 1)
                                  if image.pixelColor(x, y).alpha() > 0 and
                                  image.pixelColor(x, y).lightness() > 150]
                        self.assertTrue(pixels, (widget_type.__name__, rect))
                        return QPoint(round(sum(point.x() for point in pixels) / len(pixels)),
                                      round(sum(point.y() for point in pixels) / len(pixels)))

                    up_arrow = visible_arrow_point(up)
                    down_arrow = visible_arrow_point(down)
                    original = spinbox.value()
                    QTest.mouseClick(spinbox, Qt.LeftButton, pos=up_arrow)
                    self.assertEqual(spinbox.value(), original + 1)
                    QTest.mouseClick(spinbox, Qt.LeftButton, pos=down_arrow)
                    self.assertEqual(spinbox.value(), original)
                spinbox.close()

            with tempfile.TemporaryDirectory() as folder:
                config = ConfigManager(Path(folder) / "settings.json")
                settings = SettingsWindow(config)
                settings.resize(760, 570)
                settings.show()
                editor_page = settings.page("编辑器")
                settings.navigation.setCurrentRow(
                    next(index for index in range(settings.navigation.count())
                         if settings.navigation.item(index).text() == "编辑器"))
                self.app.processEvents()
                spinbox = editor_page.controls["pen_width"]
                option = QStyleOptionSpinBox()
                option.initFrom(spinbox)
                option.rect = spinbox.rect()
                up = spinbox.style().subControlRect(
                    QStyle.CC_SpinBox, option, QStyle.SC_SpinBoxUp, spinbox)
                down = spinbox.style().subControlRect(
                    QStyle.CC_SpinBox, option, QStyle.SC_SpinBoxDown, spinbox)
                value = spinbox.value()
                self.assertTrue(spinbox.lineEdit().geometry().intersects(up))
                line_up = spinbox.lineEdit().mapFromGlobal(
                    spinbox.mapToGlobal(up.center()))
                QTest.mouseClick(spinbox.lineEdit(), Qt.LeftButton, pos=line_up)
                self.assertEqual(spinbox.value(), value + 1)
                QTest.mouseClick(spinbox, Qt.LeftButton, pos=down.center())
                self.assertEqual(spinbox.value(), value)
                settings.close()
        finally:
            apply_theme(self.app, "system")


if __name__ == "__main__":  # 支持 python tests/test_dpi.py
    unittest.main()
