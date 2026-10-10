"""暂未归类的其它用例（按用例名未命中任何分类规则）。"""

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


class MiscTests(CoreTests):
    def test_hotkey_health_timer_interval_configurable_and_preventive_reinstall(self):
        """用户选择「甲」（2026-10-11）：10 秒探活 + 60 秒探针两个定时器合并成一个**可配周期**
        （默认 15 秒，设置页 3–300 秒）；每跳先探活，监听线程正常时**再排一次主动探针** ——
        真机证明只有探针能发现「Windows 静默摘钩但监听线程还活着」这种形态。"""
        import time as _time
        from unittest.mock import patch as _patch
        from config.config_manager import DEFAULTS, validate
        from hotkey import hotkey_manager as hm

        self.assertEqual(DEFAULTS["hotkey_health_interval"], 60)
        self.assertEqual(validate({})["hotkey_health_interval"], 60)
        for bad in (0, 2, 14, 601, 9999):
            with self.assertRaises(ValueError, msg=f"{bad} 应被拒绝"):
                validate({"hotkey_health_interval": bad})

        class FakeKeyboard:
            class _listener:
                listening = True

            def add_hotkey(self, binding, callback, suppress=False):
                return "h:" + binding

            def remove_hotkey(self, handle):
                pass

            def send(self, binding):
                pass

        with _patch.object(hm, "keyboard", FakeKeyboard()):
            manager = hm.HotkeyManager()
            try:
                # ① 设置里的值经 register() 生效
                manager.register({"hotkeys_enabled": True,
                                  "hotkeys": {"capture": "ctrl+alt+a"},
                                  "capture_hotkey_suppress": False,
                                  "hotkey_health_interval": 20})
                self.assertEqual(manager.health_timer.interval(), 20_000)
                # ② 越界与非法值一律夹紧/回落，绝不让定时器跑飞
                manager.set_health_interval(1)
                self.assertEqual(manager.health_timer.interval(), hm.MIN_HEALTH_SECONDS * 1000)
                manager.set_health_interval(9999)
                self.assertEqual(manager.health_timer.interval(), hm.MAX_HEALTH_SECONDS * 1000)
                manager.set_health_interval("坏值")
                self.assertEqual(manager.health_timer.interval(), hm.DEFAULT_HEALTH_SECONDS * 1000)
                # ③ 健康时的一跳必须做一次预防性重装（合并定时器的真正意义）
                manager._last_request = {"enabled": True,
                                         "bindings": {"capture": "ctrl+alt+a"},
                                         "suppress_capture": False}
                self.assertTrue(manager.listener_alive())
                manager.check_health()
                deadline = _time.monotonic() + 3
                while manager.preventive_reinstalls == 0 and _time.monotonic() < deadline:
                    _time.sleep(0.02)
                self.assertEqual(manager.preventive_reinstalls, 1,
                                 "健康时的一跳应做预防性重装（静默摘钩无法探测，只能定期刷新）")
                deadline = _time.monotonic() + 3
                while not manager.handles and _time.monotonic() < deadline:
                    _time.sleep(0.02)
                self.assertTrue(manager.handles, "预防性重装后热键句柄必须重建")
            finally:
                manager.stop()

    def test_hotkey_callback_trace_is_actually_logged(self):
        """回归（2026-10-11 真机）：热键回调留痕曾写成 `from core.log_rate import log_every`，
        而实际模块是 `logger.log_rate` ⇒ ImportError 被 `except` 静默吞掉 ⇒「热键回调」留痕
        永远 0 条，日志里明明有「触发热键」却查不出输入是否到达钩子。"""
        from hotkey.hotkey_manager import HotkeyManager

        manager = HotkeyManager()
        try:
            with self.assertLogs("screensnap", level="DEBUG") as captured:
                manager.set_paused(False)
                manager.emit_action("capture")
            self.assertTrue(any("热键回调" in line for line in captured.output),
                            "热键触发必须留下可核对的留痕（模块路径不能写错）")
            self.assertTrue(any("来源=keyboard钩子" in line for line in captured.output))
            # 暂停期间不应记录也不应转发
            manager.set_paused(True)
            manager.emit_action("capture")
        finally:
            manager.stop()
    def test_hotkey_manager_reinstalls_when_listener_dies(self):
        """回归（用户 2026-10-11）：安全软件拖慢钩子回调时，Windows 会按 LowLevelHooksTimeout
        静默移除 WH_KEYBOARD_LL 钩子，表现为「被拦一次后所有热键永久失效」且没有任何报错。

        这里用假 keyboard 模块验证体检逻辑：① 监听线程不可用 ⇒ 重装一次；
        ② 重装有限速（30 秒内不重复）；③ 监听线程正常 ⇒ 什么都不做。"""
        import time as _time
        from unittest.mock import patch as _patch
        from hotkey import hotkey_manager as hm

        class FakeListener:
            listening = True

        class FakeKeyboard:
            def __init__(self):
                self._listener = FakeListener()
                self.added = []
                self.removed = []

            def add_hotkey(self, binding, callback, suppress=False):
                self.added.append(binding)
                return "handle-" + binding

            def remove_hotkey(self, handle):
                self.removed.append(handle)

        fake = FakeKeyboard()
        with _patch.object(hm, "keyboard", fake):
            manager = hm.HotkeyManager()
            try:
                manager.register({"hotkeys_enabled": True,
                                  "hotkeys": {"capture": "ctrl+alt+a"},
                                  "capture_hotkey_suppress": False})
                deadline = _time.monotonic() + 3
                while not manager.handles and _time.monotonic() < deadline:
                    _time.sleep(0.02)
                self.assertTrue(manager.handles, "应完成一次安装")
                self.assertTrue(manager.listener_alive())
                manager.check_health()
                self.assertEqual(manager.reinstall_count, 0, "监听线程正常时不应重装")
                # 模拟钩子被系统摘掉：库监听线程没了
                fake._listener = None
                self.assertFalse(manager.listener_alive())
                manager.check_health()
                self.assertEqual(manager.reinstall_count, 1, "监听线程不可用时应重装一次")
                manager.check_health()
                self.assertEqual(manager.reinstall_count, 1, "重装必须限速，不能刷屏")
                manager._last_retry = 0.0
                manager.check_health()
                self.assertEqual(manager.reinstall_count, 2, "超过限速窗口后允许再次重装")
                # 按需体检（托盘双击截图）：不受限速约束，必须能立刻恢复
                manager._last_retry = _time.monotonic()
                manager.check_health()
                self.assertEqual(manager.reinstall_count, 2, "自动体检仍受限速约束")
                manager.check_health(force=True)
                self.assertEqual(manager.reinstall_count, 3, "按需体检应跳过限速，立刻重装")
                # 关键：监听线程看起来正常时，按需体检也必须重装 ——
                # Windows 静默摘钩时监听线程仍然存活，只靠标志位判断永远恢复不了。
                fake._listener = FakeListener()
                self.assertTrue(manager.listener_alive())
                manager.check_health(force=True)
                self.assertEqual(manager.reinstall_count, 4, "监听线程正常也应按需重装")
            finally:
                manager.stop()

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

    def test_rebuildable_cache_cleanup_removes_only_generated_files(self):
        from PySide6.QtGui import QColor, QImage
        from config.config_manager import DEFAULTS

        with tempfile.TemporaryDirectory() as folder, \
                patch("sticker.sticker_manager.data_dir", return_value=Path(folder)):
            manager = StickerManager(DEFAULTS)
            image = QImage(12, 8, QImage.Format_RGB32)
            image.fill(QColor("red"))
            active = manager.add(image)
            recycled = manager.add(image)
            manager.items.remove(recycled)
            manager.recycle_bin.append(recycled)
            recycled.hide()
            active_path = Path(active.source)
            recycled_path = Path(recycled.source)

            cache = Path(folder) / "sticker_cache"
            cache.mkdir(exist_ok=True)
            orphan = cache / "sticker_orphan.png"
            Image.new("RGB", (4, 4), "blue").save(orphan)
            clipboard_dir = Path(folder) / "clipboard_history"
            clipboard_dir.mkdir()
            old_clipboard = clipboard_dir / "clipboard_unused.png"
            Image.new("RGB", (4, 4), "green").save(old_clipboard)
            toast_dir = Path(folder) / "toast_cache"
            toast_dir.mkdir()
            toast = toast_dir / "toast.png"
            Image.new("RGB", (4, 4), "yellow").save(toast)
            saved_dir = Path(folder) / "saved"
            saved_dir.mkdir()
            saved = saved_dir / "capture.png"
            Image.new("RGB", (4, 4), "black").save(saved)

            result = manager.clear_rebuildable_cache()
            self.assertEqual(result["orphan_sticker_images"], 1)
            self.assertEqual(result["clipboard_images"], 1)
            self.assertEqual(result["toast_images"], 1)
            self.assertTrue(active_path.exists())
            self.assertTrue(recycled_path.exists())
            self.assertTrue(saved.exists())
            self.assertFalse(orphan.exists())
            self.assertFalse(old_clipboard.exists())
            self.assertFalse(toast.exists())
            manager.close_all()
            recycled.close()
            self.app.processEvents()

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
            root = Path(folder) / "ScreenSnaps"
            settings = dict(DEFAULTS, save_dir=str(root),
                            archive_by_month=True, archive_by_day=False)
            self.assertEqual(resolved_dir(settings, date(2026, 9, 30)),
                             root / "2026-09")
            self.assertEqual(resolved_dir(settings, date(2026, 10, 1)),
                             root / "2026-10")
            settings["archive_by_month"] = False
            settings["archive_by_day"] = True
            self.assertEqual(resolved_dir(settings, date(2026, 9, 30)),
                             root / "2026-09-30")
            self.assertEqual(configured_dir(settings), root)

            for month, name in (("2026-09", "old.png"), ("2026-10", "new.png")):
                folder_path = root / month
                folder_path.mkdir(parents=True)
                Image.new("RGB", (2, 2), "white").save(folder_path / name)
            history = StickerManager.files(SimpleNamespace(settings=settings))
            self.assertEqual({path.name for path in history}, {"old.png", "new.png"})

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

    def test_shape_constraint_keys_and_geometry(self):
        from editor.annotation_canvas import (constrained_shape_endpoint,
                                              shape_constraint_active)

        for modifier in (Qt.ControlModifier, Qt.AltModifier):
            self.assertTrue(shape_constraint_active(modifier))
        self.assertFalse(shape_constraint_active(Qt.NoModifier))

        start = QPointF(40, 50)
        endpoint = constrained_shape_endpoint(start, QPointF(100, 70))
        self.assertEqual(endpoint, QPointF(100, 110))
        reverse = constrained_shape_endpoint(start, QPointF(20, 35))
        self.assertEqual(reverse, QPointF(20, 30))

        from editor.toolbar_widget import ToolbarWidget
        from config.config_manager import DEFAULTS
        toolbar = ToolbarWidget(settings=dict(DEFAULTS))
        try:
            self.assertIn("Ctrl", toolbar.tool_buttons["rect"].toolTip())
            self.assertIn("Alt", toolbar.tool_buttons["rect"].toolTip())
            self.assertNotIn("Space", toolbar.tool_buttons["ellipse"].toolTip())
            for tool in ("pen", "marker", "mosaic"):
                self.assertIn("Ctrl", toolbar.tool_buttons[tool].toolTip())
                self.assertIn("Alt", toolbar.tool_buttons[tool].toolTip())
                self.assertNotIn("Space", toolbar.tool_buttons[tool].toolTip())
            for tool in ("rect", "ellipse"):
                self.assertIn("Ctrl", toolbar.tool_buttons[tool].toolTip())
                self.assertIn("Alt", toolbar.tool_buttons[tool].toolTip())
                self.assertNotIn("Space", toolbar.tool_buttons[tool].toolTip())
            self.assertNotIn("Space", toolbar.pen_chain.toolTip())
            self.assertNotIn("Space", toolbar.marker_chain.toolTip())
        finally:
            toolbar.close()

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

    def test_right_drag_waits_for_confirmation_before_entering_edit_flow(self):
        from config.config_manager import DEFAULTS
        from screenshot.mask_window import MaskWindow

        bounds = {"left": 0, "top": 0, "width": 120, "height": 80}
        settings = {**DEFAULTS, "inline_edit": False, "capture_after_selection": "edit",
                    "magnifier": False, "crosshair": False, "sound": False}
        with patch("screenshot.mask_window.visible_windows", return_value=[]):
            mask = MaskWindow(Image.new("RGB", (120, 80), "blue"), bounds, [bounds], settings)
        routed = []
        selected = []
        mask.edit_requested.connect(lambda images, positions: routed.append(images))
        mask.selected.connect(lambda images, positions: selected.append(images))
        mask.show()
        self.app.processEvents()
        start, end = QPoint(15, 12), QPoint(55, 42)
        QTest.mousePress(mask, Qt.RightButton, Qt.NoModifier, start)
        QTest.mouseMove(mask, end)
        QTest.mouseRelease(mask, Qt.RightButton, Qt.NoModifier, end)
        self.assertEqual(selected, [])
        self.assertTrue(mask.isVisible())
        self.assertEqual(len(mask.selection.rects), 1)
        # 新语义：Enter 只对“多选选区”生效；只有一块时 Enter 被忽略（不是 Esc、也不提交）。
        QTest.keyClick(mask, Qt.Key_Return)
        self.assertEqual(routed, [])
        self.assertTrue(mask.isVisible())
        # 用左键双击确认（该手势对单块同样有效），验证“确认后才进入编辑流程”。
        QTest.mouseDClick(mask, Qt.LeftButton, Qt.NoModifier, end)
        self.assertEqual(len(routed), 1)
        self.assertEqual(routed[0][0][0].size, (41, 31))
        self.assertEqual(selected, [])
        self.assertFalse(mask.isVisible())

    def test_info_bar_alignment_follows_screen_side(self):
        """提示条与放大镜边缘对齐：放大镜在光标右侧（贴左）时左对齐，翻到左侧时右对齐。"""
        from PySide6.QtCore import QRect
        from PySide6.QtGui import QFont, QFontMetrics
        from screenshot.overlay_info import info_bar_layout

        metrics = QFontMetrics(QFont())
        area = QRect(0, 0, 600, 400)
        items = ["20, 20  200 x 100", "拖动移动"]
        # 放大镜在光标右侧（普通情形）：提示条左边直接对齐放大镜左边，不再额外偏移；文字左对齐。
        cursor = (100, 30)
        right_anchor = QRect(132, 62, 140, 140)
        bar, _rows, align_right = info_bar_layout(metrics, area, right_anchor, items,
                                                  cursor=cursor)
        self.assertFalse(align_right)
        self.assertEqual(bar.left(), right_anchor.left())
        # 默认间距为 0：提示条上边紧贴放大镜下边（相邻不重叠）。
        self.assertEqual(bar.top(), right_anchor.bottom() + 1)
        # 间距可配置：gap 越大，提示条离放大镜越远。
        gap_bar, _rows, _align = info_bar_layout(metrics, area, right_anchor, items,
                                                 cursor=cursor, gap=12)
        self.assertEqual(gap_bar.top(), right_anchor.bottom() + 1 + 12)
        # 放大镜翻到光标左侧（贴右沿）：提示条右边直接对齐放大镜右边，文字右对齐。
        left_anchor = QRect(area.right() - 172, 62, 140, 140)
        bar2, _rows2, align_right2 = info_bar_layout(metrics, area, left_anchor, items,
                                                     cursor=(area.right() - 40, 30))
        self.assertTrue(align_right2)
        # 条右边缘对齐放大镜右边缘（QRect.right() 已含 -1）。
        self.assertEqual(bar2.right(), left_anchor.right())

    def test_shape_uses_fill_color_and_falls_back_to_stroke(self):
        from config.config_manager import DEFAULTS
        from editor.annotation_items import shape
        from PySide6.QtCore import QPointF

        # 指定填充色：与线条颜色相互独立。
        item = shape("rect", QPointF(10, 10), QPointF(60, 50), "#ff0000", 2,
                     fill_enabled=True, fill_opacity=100, fill_color="#00ff00")
        self.assertEqual(item.brush().color().name(), "#00ff00")
        self.assertEqual(item.pen().color().name(), "#ff0000")
        # 未指定时沿用线条颜色（旧行为不变）。
        fallback = shape("ellipse", QPointF(10, 10), QPointF(60, 50), "#ff0000", 2,
                         fill_enabled=True, fill_opacity=100)
        self.assertEqual(fallback.brush().color().name(), "#ff0000")
        self.assertEqual(DEFAULTS["rect_fill_color"], "#ff0000")

    def test_set_selected_fill_applies_custom_color(self):
        from config.config_manager import DEFAULTS
        from editor.annotation_items import shape
        from editor.annotation_canvas import AnnotationCanvas
        from PIL import Image
        from PySide6.QtCore import QPointF

        canvas = AnnotationCanvas(Image.new("RGB", (200, 160), "white"), dict(DEFAULTS))
        item = shape("rect", QPointF(20, 20), QPointF(80, 60), "#ff0000", 2,
                     fill_enabled=True, fill_opacity=100)
        canvas.scene_data.addItem(item)
        item.setSelected(True)
        canvas.set_selected_fill("rect", True, 100, "#00ad91")
        self.assertEqual(item.brush().color().name(), "#00ad91")
        canvas.close()

    def test_inline_options_refresh_after_double_click_delete(self):
        from config.config_manager import DEFAULTS
        from screenshot.mask_window import MaskWindow
        with tempfile.TemporaryDirectory() as folder:
            bounds = {"left": 0, "top": 0, "width": 1200, "height": 900}
            settings = {**DEFAULTS, "save_dir": folder, "inline_edit": True,
                        "magnifier": False, "capture_after_selection": "edit"}
            with patch("screenshot.mask_window.visible_windows", return_value=[]):
                mask = MaskWindow(Image.new("RGB", (1200, 900), "white"), bounds, [bounds], settings)
            mask.selection.rects.append(QRect(50, 50, 320, 200))
            mask.complete()
            mask.show()
            self.app.processEvents()
            try:
                self._check_options_after_double_click_delete(mask.session.inline_editor)
            finally:
                mask.close()

    def test_drag_shows_alignment_guides_when_edges_close(self):
        from config.config_manager import DEFAULTS
        from editor.annotation_items import shape
        from PySide6.QtCore import QPointF
        canvas = AnnotationCanvas(Image.new("RGB", (200, 200), "white"), dict(DEFAULTS))
        a = shape("rect", QPointF(96, 10), QPointF(136, 50), "#ff0000", 3)
        b = shape("rect", QPointF(100, 10), QPointF(140, 50), "#0000ff", 3)
        canvas.scene_data.addItem(a)
        canvas.scene_data.addItem(b)
        a.setSelected(True)
        canvas._update_alignment_guides()
        self.assertTrue(canvas.alignment_guides)
        vertical = [g for g in canvas.alignment_guides if abs(g.x1() - g.x2()) < 1e-6]
        self.assertTrue(vertical)
        # 选中项被吸附，使其左边缘与另一标注（含线宽扩张后的）左边缘对齐。
        self.assertAlmostEqual(a.sceneBoundingRect().left(), b.sceneBoundingRect().left(), delta=0.6)
        self.assertAlmostEqual(vertical[0].x1(), b.sceneBoundingRect().left(), delta=0.6)
        canvas.close()

    def test_no_alignment_guides_when_far_apart(self):
        from config.config_manager import DEFAULTS
        from editor.annotation_items import shape
        from PySide6.QtCore import QPointF
        canvas = AnnotationCanvas(Image.new("RGB", (200, 200), "white"), dict(DEFAULTS))
        a = shape("rect", QPointF(10, 10), QPointF(50, 50), "#ff0000", 3)
        b = shape("rect", QPointF(150, 120), QPointF(190, 160), "#0000ff", 3)
        canvas.scene_data.addItem(a)
        canvas.scene_data.addItem(b)
        a.setSelected(True)
        canvas._update_alignment_guides()
        self.assertEqual(canvas.alignment_guides, [])
        canvas.close()

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

    def test_click_once_tools_do_not_duplicate_on_double_click(self):
        """单击立即执行；同一位置的双击只表示保存，不重复动作、不留多余标注。"""
        from unittest.mock import Mock, patch
        from config.config_manager import DEFAULTS
        from editor.annotation_canvas import TEXT_CLICK_COMMIT_MS
        from editor.annotation_items import AnnotationSequenceItem

        text_delay = min(QApplication.doubleClickInterval(), TEXT_CLICK_COMMIT_MS)

        # 序号：单击立即落下；双击保存，并撤掉这一拍刚落的序号。
        number_canvas = AnnotationCanvas(Image.new("RGB", (100, 80), "white"), dict(DEFAULTS))
        number_canvas.resize(240, 180)
        number_canvas.show()
        self.app.processEvents()
        number_canvas.set_tool("number")
        position = number_canvas.mapFromScene(QPointF(24, 24))
        confirmed = []
        number_canvas.confirmed.connect(lambda: confirmed.append(True))
        QTest.mousePress(number_canvas.viewport(), Qt.LeftButton, pos=position)
        QTest.mouseRelease(number_canvas.viewport(), Qt.LeftButton, pos=position)
        self.assertEqual(sum(isinstance(item, AnnotationSequenceItem)
                             for item in number_canvas.annotations()), 1)

        number_canvas.restore([])
        number_canvas.reset_history()
        QTest.mousePress(number_canvas.viewport(), Qt.LeftButton, pos=position)
        QTest.mouseRelease(number_canvas.viewport(), Qt.LeftButton, pos=position)
        QTest.mouseDClick(number_canvas.viewport(), Qt.LeftButton, pos=position)
        self.app.processEvents()
        self.assertEqual(sum(isinstance(item, AnnotationSequenceItem)
                             for item in number_canvas.annotations()), 0)
        self.assertEqual(confirmed, [True])
        number_canvas.close()

        # 文字：单击要等双击窗口过去才弹输入框；同位置双击取消弹框并保存。
        text_canvas = AnnotationCanvas(Image.new("RGB", (100, 80), "white"), dict(DEFAULTS))
        text_canvas.resize(240, 180)
        text_canvas.show()
        self.app.processEvents()
        text_canvas.set_tool("text")
        position = text_canvas.mapFromScene(QPointF(24, 24))
        input_text = Mock(return_value=("one text", False, True))
        with patch.object(text_canvas, "input_text", input_text):
            QTest.mousePress(text_canvas.viewport(), Qt.LeftButton, pos=position)
            QTest.mouseRelease(text_canvas.viewport(), Qt.LeftButton, pos=position)
            self.assertEqual(input_text.call_count, 0)
            QTest.qWait(text_delay + 30)
            self.assertEqual(input_text.call_count, 1)
        self.assertEqual(len(text_canvas.annotations()), 1)

        text_canvas.restore([])
        text_canvas.reset_history()
        input_text.reset_mock()
        confirmed = []
        text_canvas.confirmed.connect(lambda: confirmed.append(True))
        with patch.object(text_canvas, "input_text", input_text):
            QTest.mousePress(text_canvas.viewport(), Qt.LeftButton, pos=position)
            QTest.mouseRelease(text_canvas.viewport(), Qt.LeftButton, pos=position)
            QTest.mouseDClick(text_canvas.viewport(), Qt.LeftButton, pos=position)
            self.app.processEvents()
        self.assertEqual(input_text.call_count, 0)
        self.assertEqual(len(text_canvas.annotations()), 0)
        self.assertEqual(confirmed, [True])
        text_canvas.close()

        # 吸管：单击立即取色；双击保存，不重复取色。
        picker_canvas = AnnotationCanvas(Image.new("RGB", (100, 80), "#336699"), dict(DEFAULTS))
        picker_canvas.resize(240, 180)
        picker_canvas.show()
        self.app.processEvents()
        picker_canvas.set_tool("picker")
        position = picker_canvas.mapFromScene(QPointF(24, 24))
        picked = []
        confirmed = []
        picker_canvas.color_picked.connect(picked.append)
        picker_canvas.confirmed.connect(lambda: confirmed.append(True))
        with patch("PySide6.QtGui.QGuiApplication.clipboard"):
            QTest.mousePress(picker_canvas.viewport(), Qt.LeftButton, pos=position)
            QTest.mouseRelease(picker_canvas.viewport(), Qt.LeftButton, pos=position)
            self.assertEqual(picked, ["#336699"])
            QTest.mouseDClick(picker_canvas.viewport(), Qt.LeftButton, pos=position)
            self.app.processEvents()
        self.assertEqual(picked, ["#336699"])
        self.assertEqual(confirmed, [True])
        picker_canvas.close()

    def test_click_once_tools_keep_rapid_clicks_and_respect_image_bounds(self):
        """单击生效的工具：快速两次单击不能互相顶掉，画布空白处的双击也不落标注。"""
        from config.config_manager import DEFAULTS
        from editor.annotation_items import AnnotationSequenceItem

        canvas = AnnotationCanvas(Image.new("RGB", (60, 40), "white"), dict(DEFAULTS))
        canvas.resize(300, 220)
        canvas.show()
        self.app.processEvents()
        canvas.set_tool("number")

        first = canvas.mapFromScene(QPointF(15, 15))
        second = canvas.mapFromScene(QPointF(45, 30))
        QTest.mousePress(canvas.viewport(), Qt.LeftButton, pos=first)
        QTest.mouseRelease(canvas.viewport(), Qt.LeftButton, pos=first)
        QTest.mousePress(canvas.viewport(), Qt.LeftButton, pos=second)
        QTest.mouseRelease(canvas.viewport(), Qt.LeftButton, pos=second)
        self.app.processEvents()
        self.assertEqual(sum(isinstance(item, AnnotationSequenceItem)
                             for item in canvas.annotations()), 2)

        # 图片（场景）之外的画布空白：单击与双击都不应落标注。
        canvas.restore([])
        canvas.reset_history()
        outside = canvas.mapFromScene(QPointF(75, 35))
        QTest.mousePress(canvas.viewport(), Qt.LeftButton, pos=outside)
        QTest.mouseRelease(canvas.viewport(), Qt.LeftButton, pos=outside)
        QTest.mouseDClick(canvas.viewport(), Qt.LeftButton, pos=outside)
        self.app.processEvents()
        self.assertEqual([item for item in canvas.annotations()
                          if isinstance(item, AnnotationSequenceItem)], [])
        canvas.close()

    def test_double_click_save_leaves_no_dot_or_zero_size_shape(self):
        """单击会落“点”的工具：橡皮擦双击保存前撤销点；矩形/椭圆/箭头孤立单击不落零尺寸图形。"""
        from config.config_manager import DEFAULTS

        def strokes(canvas):
            return sum(len(item.strokes) for item in canvas.erase_items())

        def canvas_for(tool, **extra):
            canvas = AnnotationCanvas(Image.new("RGB", (120, 100), "white"),
                                      dict(DEFAULTS, **extra))
            canvas.resize(240, 200)
            canvas.show()
            self.app.processEvents()
            canvas.set_tool(tool)
            return canvas

        def clean_double_click(canvas, scene_point):
            pos = canvas.mapFromScene(QPointF(*scene_point))
            QTest.mousePress(canvas.viewport(), Qt.LeftButton, pos=pos)
            QTest.mouseRelease(canvas.viewport(), Qt.LeftButton, pos=pos)
            QTest.mouseDClick(canvas.viewport(), Qt.LeftButton, pos=pos)
            self.app.processEvents()

        # 橡皮擦：单击会落一个点，双击保存前撤销，不残留白点。
        eraser = canvas_for("eraser")
        confirmed = []
        eraser.confirmed.connect(lambda: confirmed.append(True))
        clean_double_click(eraser, (40, 40))
        self.assertEqual(strokes(eraser), 0)
        self.assertEqual(confirmed, [True])
        eraser.close()

        # 矩形/椭圆/箭头的孤立单击不再落零尺寸图形；双击保存。
        for tool in ("rect", "ellipse", "arrow"):
            canvas = canvas_for(tool)
            confirmed = []
            canvas.confirmed.connect(lambda: confirmed.append(True))
            pos = canvas.mapFromScene(QPointF(40, 40))
            QTest.mousePress(canvas.viewport(), Qt.LeftButton, pos=pos)
            QTest.mouseRelease(canvas.viewport(), Qt.LeftButton, pos=pos)
            self.app.processEvents()
            self.assertEqual(canvas.annotations(), [], tool)
            clean_double_click(canvas, (40, 40))
            self.assertEqual(canvas.annotations(), [], tool)
            self.assertEqual(confirmed, [True], tool)
            canvas.close()

        # 涂抹马赛克：真实拖动后双击保存，不能误撤销那一次涂抹。
        mosaic = canvas_for("mosaic", mosaic_brush=True)
        confirmed = []
        mosaic.confirmed.connect(lambda: confirmed.append(True))
        start = mosaic.mapFromScene(QPointF(20, 20))
        end = mosaic.mapFromScene(QPointF(80, 70))
        QTest.mousePress(mosaic.viewport(), Qt.LeftButton, pos=start)
        QTest.mouseMove(mosaic.viewport(), end)
        QTest.mouseRelease(mosaic.viewport(), Qt.LeftButton, pos=end)
        self.app.processEvents()
        self.assertEqual(len(mosaic.annotations()), 1)
        clean_double_click(mosaic, (100, 90))
        self.assertEqual(len(mosaic.annotations()), 1)
        self.assertEqual(confirmed, [True])
        mosaic.close()

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
        center = item.sceneBoundingRect().center()
        # 图形中心是旋转按钮：那里显示旋转光标。
        QTest.mouseMove(editor.canvas.viewport(),
                        pos=editor.canvas.mapFromScene(center))
        self.assertFalse(editor.canvas.cursor().pixmap().isNull())
        # 图形内部、避开中心按钮的位置显示移动光标。
        QTest.mouseMove(editor.canvas.viewport(),
                        pos=editor.canvas.mapFromScene(QPointF(center.x() - 12, center.y())))
        self.assertEqual(editor.canvas.cursor().shape(), Qt.SizeAllCursor)
        QTest.mouseMove(editor.canvas.viewport(),
                        pos=editor.canvas.mapFromScene(item.sceneBoundingRect().bottomRight()))
        self.assertEqual(editor.canvas.cursor().shape(), Qt.SizeFDiagCursor)
        editor.close()

    def test_intruder_warning_master_and_per_item_switches(self):
        """采集自检警示默认关闭；开启后按子项过滤参与警示的窗口分类。"""
        from PySide6.QtWidgets import QDialog
        from config.config_manager import DEFAULTS
        from screenshot.mask_window import MaskWindow, window_category

        bounds = {"left": 0, "top": 0, "width": 200, "height": 150}
        # 默认关闭：即使选区内有本程序窗口也不在提示条里附加警示。
        with patch("screenshot.mask_window.visible_windows", return_value=[]):
            mask = MaskWindow(Image.new("RGB", (200, 150), "white"), bounds, [bounds],
                              dict(DEFAULTS, inline_edit=False, magnifier=False))
        mask.show()
        self.app.processEvents()
        mask.selection.rects.append(QRect(40, 30, 100, 80))
        dialog = QDialog()
        dialog.setGeometry(QRect(60, 50, 60, 40))
        dialog.show()
        self.app.processEvents()
        self.assertIn(dialog, mask.intruding_windows())
        self.assertEqual(window_category(dialog), "other")
        self.assertIsNone(mask.self_check_warning())

        # 打开总开关但关掉「本程序窗口」子项：该分类的窗口不再提示。
        mask.settings["intruder_warning_enabled"] = True
        mask.settings["intruder_warning_items"] = dict(
            DEFAULTS["intruder_warning_items"], other=False)
        self.assertIsNone(mask.self_check_warning())
        # 重新勾选该子项后恢复提示。
        mask.settings["intruder_warning_items"]["other"] = True
        self.assertIn("本程序窗口", mask.self_check_warning())
        dialog.close()
        mask.close()

    def test_manual_save_writes_image(self):
        from config.config_manager import DEFAULTS
        with tempfile.TemporaryDirectory() as folder:
            settings = {**DEFAULTS, "save_dir": folder, "filename": "manual_save",
                        "archive_by_month": False}
            editor = EditorWindow(Image.new("RGB", (25, 20), "#23bc58"), settings)
            editor.execute("save")
            self.assertFalse(editor.isVisible())
            with Image.open(Path(folder) / "manual_save.png") as saved:
                self.assertEqual(saved.getpixel((10, 10))[:3], (35, 188, 88))

    def test_clear_rebuildable_cache_preserves_saved_and_referenced_images(self):
        from config.config_manager import DEFAULTS
        from PySide6.QtGui import QColor, QImage

        with tempfile.TemporaryDirectory() as folder, \
                patch("sticker.sticker_manager.data_dir", return_value=Path(folder)):
            manager = StickerManager(DEFAULTS)
            image = QImage(12, 8, QImage.Format_RGB32)
            image.fill(QColor("red"))
            active = manager.add(image)
            recycled = manager.add(image)
            manager.items.remove(recycled)
            manager.recycle_bin.append(recycled)
            recycled.hide()
            active_path = Path(active.source)
            recycled_path = Path(recycled.source)

            cache = Path(folder) / "sticker_cache"
            cache.mkdir(exist_ok=True)
            orphan_path = cache / "sticker_orphan.png"
            Image.new("RGB", (4, 4), "blue").save(orphan_path)
            clipboard_dir = Path(folder) / "clipboard_history"
            clipboard_dir.mkdir()
            stale_clipboard = clipboard_dir / "clipboard_old.png"
            Image.new("RGB", (4, 4), "green").save(stale_clipboard)
            toast_dir = Path(folder) / "toast_cache"
            toast_dir.mkdir()
            toast_image = toast_dir / "toast_old.png"
            Image.new("RGB", (4, 4), "yellow").save(toast_image)
            saved_dir = Path(folder) / "saved"
            saved_dir.mkdir()
            saved_image = saved_dir / "capture.png"
            Image.new("RGB", (4, 4), "black").save(saved_image)

            result = manager.clear_rebuildable_cache()
            self.assertEqual(result["orphan_sticker_images"], 1)
            self.assertEqual(result["clipboard_images"], 1)
            self.assertEqual(result["toast_images"], 1)
            self.assertTrue(active_path.is_file())
            self.assertTrue(recycled_path.is_file())
            self.assertFalse(orphan_path.exists())
            self.assertFalse(stale_clipboard.exists())
            self.assertFalse(toast_image.exists())
            self.assertTrue(saved_image.is_file())
            manager.close_all()
            recycled.close()
            self.app.processEvents()

    def test_paste_latest_restores_last_position_and_scale(self):
        import os
        from PySide6.QtGui import QImage
        from config.config_manager import DEFAULTS

        with tempfile.TemporaryDirectory() as folder, \
                patch("sticker.sticker_manager.data_dir", return_value=Path(folder)):
            settings = dict(DEFAULTS, save_dir=folder)
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
            manager = StickerManager(dict(DEFAULTS, save_dir=folder))
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
            manager = StickerManager(dict(DEFAULTS, save_dir=folder))
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

    def test_save_image_keeps_existing_file_when_encoding_fails(self):
        from PySide6.QtGui import QImage
        from core.image_io import save_image

        class PartialImage:
            @staticmethod
            def hasAlphaChannel():
                return False

            @staticmethod
            def save(path, _format, _quality):
                Path(path).write_bytes(b"partial output")
                return False

        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "capture.png"
            target.write_bytes(b"original image")
            self.assertFalse(save_image(PartialImage(), target, {"save_format": "png"}))
            self.assertEqual(target.read_bytes(), b"original image")
            self.assertEqual(list(Path(folder).iterdir()), [target])

            image = QImage(4, 4, QImage.Format_RGB32)
            image.fill(Qt.red)
            with patch("core.image_io.os.replace", side_effect=OSError("replace failed")):
                with self.assertRaises(OSError):
                    save_image(image, target, {"save_format": "png"})
            self.assertEqual(target.read_bytes(), b"original image")
            self.assertEqual(list(Path(folder).iterdir()), [target])

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

    def test_log_rate_throttles_repeated_state(self):
        from logger.log_rate import throttled

        self.assertTrue(throttled("unit", "same", 1.0))
        self.assertFalse(throttled("unit", "same", 1.0))
        self.assertTrue(throttled("unit", "changed", 1.0))

    def test_full_user_data_reset_requests_shutdown_and_skips_persistence(self):
        from main import Application

        app = Application.__new__(Application)
        app.reset_user_data_requested = False
        app.logger = Mock()
        app.qt = Mock()
        app.settings_window = Mock()
        with patch("main.set_start_on_boot") as set_start_on_boot:
            self.assertTrue(Application.request_full_user_data_reset(app))
        set_start_on_boot.assert_called_once_with(False)
        app.qt.quit.assert_called_once_with()

        app.stickers = Mock()
        app.hotkeys = Mock()
        app.tray = Mock()
        Application.shutdown(app)
        app.stickers.stop_pending_persistence.assert_called_once_with()
        app.stickers.persist.assert_not_called()
        app.stickers.persist_clipboard_history.assert_not_called()
        app.hotkeys.stop.assert_called_once_with()
        app.tray.hide.assert_called_once_with()

    def test_delete_after_full_reset_does_not_recreate_data_or_launch(self):
        import subprocess
        from main import delete_user_data_after_shutdown

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "ScreenSnap"
            (root / "sticker_cache").mkdir(parents=True)
            (root / "settings.json").write_text("old settings", encoding="utf-8")
            (root / "sticker_cache" / "old.png").write_bytes(b"old")
            lock = Mock()
            with patch("core.path_utils.data_dir", return_value=root), \
                    patch("main.logging.shutdown"), \
                    patch.object(subprocess, "Popen") as launch:
                self.assertTrue(delete_user_data_after_shutdown(lock))

            lock.unlock.assert_called_once_with()
            self.assertFalse(root.exists())
            launch.assert_not_called()

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

    def test_popup_geometry_clamps_to_each_screen_edge(self):
        from PySide6.QtCore import QSize
        from ui.window_bounds import anchored_popup_geometry, clamp_geometry

        bounds = QRect(100, 100, 800, 600)
        cases = [
            (QRect(20, 250, 150, 100), QRect(100, 250, 150, 100)),
            (QRect(850, 250, 150, 100), QRect(750, 250, 150, 100)),
            (QRect(250, 20, 150, 100), QRect(250, 100, 150, 100)),
            (QRect(250, 650, 150, 100), QRect(250, 600, 150, 100)),
        ]
        for original, expected in cases:
            with self.subTest(original=original):
                self.assertEqual(clamp_geometry(original, bounds), expected)
        popup = anchored_popup_geometry(
            QRect(1740, 500, 60, 34), QSize(900, 400),
            QRect(0, 0, 1920, 1080))
        self.assertEqual(popup, QRect(900, 534, 900, 400))
        lower_right = anchored_popup_geometry(
            QRect(1740, 1000, 60, 34), QSize(900, 400),
            QRect(0, 0, 1920, 1080))
        self.assertEqual(lower_right, QRect(900, 600, 900, 400))

    def test_shown_popup_menu_is_moved_inside_screen(self):
        from PySide6.QtCore import QPoint
        from PySide6.QtWidgets import QMenu
        from ui.window_bounds import WindowBoundsFilter

        bounds_filter = WindowBoundsFilter(self.app)
        self.app.installEventFilter(bounds_filter)
        menu = QMenu()
        menu.addAction("菜单项")
        menu.resize(180, 120)
        area = self.app.primaryScreen().availableGeometry()
        try:
            self.assertTrue(bounds_filter._should_constrain(menu))
            menu.popup(area.topLeft() + QPoint(20, 20))
            self.app.processEvents()
            menu.move(area.right() + 100, area.bottom() + 100)
            self.assertFalse(area.contains(menu.frameGeometry()))
            bounds_filter._constrain(menu)
            self.app.processEvents()
            self.assertTrue(area.contains(menu.frameGeometry()))
        finally:
            menu.close()
            self.app.removeEventFilter(bounds_filter)


if __name__ == "__main__":  # 支持 python tests/test_misc.py
    unittest.main()
