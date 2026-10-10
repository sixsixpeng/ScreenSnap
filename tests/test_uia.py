"""无障碍识别与悬停：UIA 下钻、命中穿透、悬停复用与节流。"""

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


class UiaTests(CoreTests):
    def test_element_chain_starts_from_the_same_deepest_control_as_hover(self):
        """回归（用户 2026-10-11，资源管理器实测）：Tab 只能在窗口与几个大容器间循环，
        回不到鼠标下的小元素；原因是 query() 的非悬停路径直接用 control_at 的结果往上爬，
        少了悬停那次的 deepest_at 下钻。这里用桩断言二者一致（不触发真实 UIA）。"""
        import logging
        from unittest.mock import patch as _patch
        from core import window_uia

        shallow = object()
        deep = object()
        calls = {}

        def fake_control_at(*a, **kw):
            return shallow

        def fake_deepest_at(control, x, y, logger, limit=None, cache=None, read_budget=None):
            calls["deepest_from"] = control
            return deep

        def fake_climb(control, *a, **kw):
            calls["climb_from"] = control
            return ["chain"]

        with _patch.object(window_uia, "control_at", fake_control_at), \
                _patch.object(window_uia, "deepest_at", fake_deepest_at), \
                _patch.object(window_uia, "climb", fake_climb), \
                _patch.object(window_uia, "top_window_at", lambda *a, **kw: 12345), \
                _patch.object(window_uia, "physical_rect_of", lambda *a, **kw: (0, 0, 10, 10)), \
                _patch.object(window_uia, "module", lambda: object()):
            chain = window_uia.query(object(), (5, 5), 8, logging.getLogger("t"))
        self.assertEqual(chain, ["chain"])
        self.assertIs(calls.get("deepest_from"), shallow, "应先对点上控件做一次下钻")
        self.assertIs(calls.get("climb_from"), deep,
                      "往上爬必须从下钻后的最深控件开始，否则 Tab 回不到小元素")

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

    def test_capture_element_size_badge_tracks_selected_rect(self):
        from screenshot.overlay_info import paint_info

        painter = Mock()
        painter.fontMetrics.return_value.horizontalAdvance.side_effect = lambda text: len(text) * 8
        painter.fontMetrics.return_value.height.return_value = 16
        painter.fontMetrics.return_value.elidedText.side_effect = lambda text, *_: text
        element_rect = QRect(120, 130, 80, 30)
        paint_info(painter, QRect(10, 10, 140, 140), QRect(0, 0, 500, 400), ["20, 20"],
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

    def test_multi_select_shortcut_enters_mode_and_forces_single_region_window_editor(self):
        from config.config_manager import DEFAULTS
        from screenshot.mask_window import MaskWindow

        bounds = {"left": 0, "top": 0, "width": 160, "height": 100}
        settings = {**DEFAULTS, "inline_edit": True, "crosshair": False,
                    "magnifier": False, "sound": False}
        with patch("screenshot.mask_window.visible_windows", return_value=[]):
            mask = MaskWindow(Image.new("RGB", (160, 100), "blue"), bounds,
                              [{"left": 0, "top": 0, "width": 160, "height": 100}], settings)
        routed = []
        selected = []
        mask.edit_requested.connect(lambda images, positions: routed.append((images, positions)))
        mask.selected.connect(lambda images, positions: selected.append(images))
        mask.show()
        self.app.processEvents()
        mask.setFocus()
        QTest.keyClick(mask, Qt.Key_M, Qt.AltModifier)
        self.app.processEvents()
        self.assertTrue(mask.session.multi_select_mode)
        self.assertIn("多选模式", " ".join(filter(None, mask.capture_hint_items())))

        mask.selection.rects.append(QRect(12, 14, 40, 30))
        mask.complete()
        self.assertEqual(len(routed), 1)
        self.assertEqual(len(routed[0][0]), 1)
        self.assertEqual(selected, [])
        self.assertIsNone(mask.session.inline_editor)
        mask.close()

    def test_inline_toolbar_first_hover_does_not_raise_visible_magnifier(self):
        from PySide6.QtCore import QEvent
        from PySide6.QtGui import QMouseEvent
        from PySide6.QtWidgets import QToolButton
        from config.config_manager import DEFAULTS
        from screenshot.mask_window import MaskWindow

        with tempfile.TemporaryDirectory() as folder:
            bounds = {"left": 0, "top": 0, "width": 1200, "height": 900}
            settings = {**DEFAULTS, "save_dir": folder, "inline_edit": True, "magnifier": True}
            settings["capture_after_selection"] = "edit"
            with patch("screenshot.mask_window.visible_windows", return_value=[]):
                mask = MaskWindow(Image.new("RGB", (1200, 900), "blue"), bounds, [bounds], settings)
            mask.selection.rects.append(QRect(40, 40, 300, 200))
            mask.complete()
            mask.show()
            mask.update_all()
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

    def test_capture_action_shortcuts_replace_buttons_and_open_window_editor(self):
        """选区阶段的四个按钮已移除，改由可配置快捷键触发；提示条第二行按当前设置显示。"""
        from config.config_manager import DEFAULTS
        from PySide6.QtGui import QKeySequence
        from PySide6.QtWidgets import QPushButton
        from screenshot.mask_window import MaskWindow

        bounds = {"left": 0, "top": 0, "width": 1200, "height": 800}
        settings = dict(DEFAULTS, inline_edit=True, crosshair=False, magnifier=False)
        with patch("screenshot.mask_window.visible_windows", return_value=[]):
            mask = MaskWindow(Image.new("RGB", (1200, 800), "blue"), bounds,
                              [bounds], settings)
        # 按钮连容器一起移除，遮罩上不再有任何操作按钮。
        self.assertFalse(hasattr(mask, "capture_actions"))
        self.assertEqual(mask.findChildren(QPushButton), [])
           # 检查尺寸、清除选择、窗口编辑、多选和仅复制快捷键。
        self.assertEqual(
            {name: shortcut.key().toString(QKeySequence.PortableText)
             for name, shortcut in mask.capture_action_shortcuts.items()},
            {"custom_size": "F", "recapture": "R",
               "window_edit": "E", "multi_select": "Alt+M", "copy": "Y"})

        # 无选区不提示；有选区（未进入编辑）补第二行，取值即当前设置。
        self.assertIsNone(mask.capture_action_hints())
        mask.selection.rects.append(QRect(30, 40, 80, 60))
        mask.update_all()
        self.assertEqual(mask.capture_action_hints(),
                         [("F", "尺寸"), ("R", "清除选择"),
                          ("E", "窗口编辑"), ("Alt+M", "多选"),
                          ("Y", "仅复制")])
        # 遮罩与配置共用同一个 dict：改设置后提示与快捷键一起变，不是写死文案。
        settings["capture_recapture_shortcut"] = "Alt+R"
        settings["capture_window_edit_shortcut"] = "Ctrl+E"
        mask.sync_capture_action_shortcuts(settings)
        self.assertEqual(mask.capture_action_hints()[1], ("Alt+R", "清除选择"))
        self.assertEqual(mask.capture_action_shortcuts["window_edit"].key().toString(
            QKeySequence.PortableText), "Ctrl+E")

        # 真按键即可触发（单键，无需组合）：按 Y（仅复制）把选区写入剪贴板并关闭遮罩。
        copied = []
        mask.copy_done.connect(copied.append)
        mask.show()
        self.app.processEvents()
        QTest.keyClick(mask, Qt.Key_Y)
        self.assertEqual(len(copied), 1)
        self.assertEqual(copied[0].size, (80, 60))
        self.assertFalse(mask.isVisible())

        # 改键后立即生效：新的 Alt+R（清除选择）只清空选区，不重截、不关遮罩。
        with patch("screenshot.mask_window.visible_windows", return_value=[]):
            second = MaskWindow(Image.new("RGB", (1200, 800), "blue"), bounds,
                                [bounds], settings)
        second.selection.rects.append(QRect(30, 40, 80, 60))
        recaptured = []
        second.recapture_requested.connect(recaptured.append)
        second.show()
        self.app.processEvents()
        QTest.keyClick(second, Qt.Key_R, Qt.AltModifier)
        self.assertEqual(recaptured, [])                 # 不请求重截（不刷新画面）
        self.assertEqual(second.selection.rects, [])     # 选区被清空
        self.assertTrue(second.isVisible())              # 遮罩不关闭（不动鼠标）
        self.assertFalse(mask.isVisible())
        second.close()                                   # 新语义下 R 不再关遮罩，手动关掉以免抢后续用例的活动窗口

        settings["inline_edit"] = True
        with patch("screenshot.mask_window.visible_windows", return_value=[]):
            third = MaskWindow(Image.new("RGB", (1200, 800), "blue"), bounds,
                               [bounds], settings)
        third.selection.rects.append(QRect(30, 40, 80, 60))
        edited = []
        third.edit_requested.connect(lambda images, positions: edited.append(images))
        third.show()
        self.app.processEvents()
        QTest.keyClick(third, Qt.Key_E, Qt.ControlModifier)
        self.assertEqual(len(edited), 1)
        self.assertEqual(edited[0][0][0].size, (80, 60))
        self.assertFalse(third.isVisible())

        settings["capture_recapture_shortcut"] = "R"
        with patch("screenshot.mask_window.visible_windows", return_value=[]):
            fourth = MaskWindow(Image.new("RGB", (1200, 800), "blue"), bounds,
                                [bounds], settings)
        fourth.selection.rects.append(QRect(30, 40, 80, 60))
        default_recaptures = []
        fourth.recapture_requested.connect(default_recaptures.append)
        fourth.show()
        self.app.processEvents()
        # 注意：R 现在是「清除选择」（不重截、不关遮罩），见下方断言。
        QTest.keyClick(fourth, Qt.Key_R)
        self.assertEqual(default_recaptures, [])          # R 不再重截（不刷新画面）
        self.assertEqual(fourth.selection.rects, [])      # 只清空选区
        self.assertTrue(fourth.isVisible())               # 不关遮罩（不动鼠标）
        fourth.close()

        settings["capture_window_edit_shortcut"] = "E"
        with patch("screenshot.mask_window.visible_windows", return_value=[]):
            fifth = MaskWindow(Image.new("RGB", (1200, 800), "blue"), bounds,
                               [bounds], settings)
        fifth.selection.rects.append(QRect(30, 40, 80, 60))
        default_edits = []
        fifth.edit_requested.connect(lambda images, positions: default_edits.append(images))
        fifth.show()
        self.app.processEvents()
        QTest.keyClick(fifth, Qt.Key_E)
        self.assertEqual(len(default_edits), 1)
        self.assertEqual(default_edits[0][0][0].size, (80, 60))
        self.assertFalse(fifth.isVisible())

    def test_window_options_refresh_after_double_click_delete(self):
        from config.config_manager import DEFAULTS
        editor = EditorWindow(Image.new("RGB", (320, 200), "white"), dict(DEFAULTS))
        editor.show()
        self.app.processEvents()
        try:
            self._check_options_after_double_click_delete(editor)
        finally:
            editor.close()

    def test_window_editor_eraser_removes_annotation_via_toolbar_button(self):
        from PySide6.QtTest import QTest
        from config.config_manager import DEFAULTS
        from editor.annotation_items import shape
        from PIL import Image

        editor = EditorWindow(Image.new("RGB", (120, 100), "white"), dict(DEFAULTS))
        editor.show()
        self.app.processEvents()
        editor.canvas.scene_data.addItem(
            shape("rect", QPointF(20, 20), QPointF(80, 80), "#ff0000", 4))
        editor.canvas.checkpoint()
        # 通过真实工具栏按钮点击切换到橡皮擦。
        QTest.mouseClick(editor.toolbar.tool_buttons["eraser"], Qt.LeftButton)
        self.assertEqual(editor.canvas.tool, "eraser")
        QTest.mousePress(editor.canvas.viewport(), Qt.LeftButton,
                         pos=editor.canvas.mapFromScene(QPointF(50, 20)))
        QTest.mouseMove(editor.canvas.viewport(), pos=editor.canvas.mapFromScene(QPointF(56, 20)))
        QTest.mouseRelease(editor.canvas.viewport(), Qt.LeftButton,
                           pos=editor.canvas.mapFromScene(QPointF(56, 20)))
        self.assertEqual(editor.canvas.render_image().pixelColor(50, 20).name(), "#ffffff")
        editor.close()

    def test_ctrl_wheel_zooms_window_editor_around_cursor(self):
        """窗口编辑器 Ctrl+滚轮缩放并锚定光标下的画面；底部提示同步；原地编辑不启用。"""
        from PySide6.QtCore import QPointF
        from PySide6.QtGui import QWheelEvent
        from PySide6.QtWidgets import QLabel
        from config.config_manager import DEFAULTS

        editor = EditorWindow(Image.new("RGB", (1200, 900), "white"), DEFAULTS)
        editor.resize(520, 420)
        editor.show()
        self.app.processEvents()
        self.assertTrue(editor.canvas.wheel_zoom_enabled)
        tips = next(label.text() for label in editor.findChildren(QLabel)
                    if label.objectName() == "editorOperationTips")
        self.assertIn("Ctrl+滚轮缩放", tips)
        try:
            editor.canvas.set_zoom(100)
            self.app.processEvents()
            point = QPoint(220, 160)
            before = editor.canvas.mapToScene(point)
            event = QWheelEvent(QPointF(point),
                                QPointF(editor.canvas.viewport().mapToGlobal(point)),
                                QPoint(0, 0), QPoint(0, 120), Qt.NoButton,
                                Qt.ControlModifier, Qt.ScrollUpdate, False)
            self.app.sendEvent(editor.canvas.viewport(), event)
            self.assertEqual(editor.zoom_input.value(), 110)
            after = editor.canvas.mapToScene(point)
            self.assertAlmostEqual(after.x(), before.x(), delta=2.0)
            self.assertAlmostEqual(after.y(), before.y(), delta=2.0)
        finally:
            editor.close()

        # 原地编辑画布不启用滚轮缩放，避免缩进去后没有控件复位。
        from screenshot.mask_window import MaskWindow
        with tempfile.TemporaryDirectory() as folder:
            bounds = {"left": 0, "top": 0, "width": 400, "height": 300}
            settings = {**DEFAULTS, "save_dir": folder, "inline_edit": True,
                        "magnifier": False, "capture_after_selection": "edit"}
            with patch("screenshot.mask_window.visible_windows", return_value=[]):
                mask = MaskWindow(Image.new("RGB", (400, 300), "white"), bounds,
                                  [bounds], settings)
            mask.selection.rects.append(QRect(20, 20, 200, 150))
            mask.complete()
            try:
                self.assertFalse(mask.session.inline_editor.canvas.wheel_zoom_enabled)
            finally:
                mask.close()

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

    def test_window_editor_copy_only_copies_without_saving_and_closes(self):
        from config.config_manager import DEFAULTS
        from editor.editor_window import EditorWindow
        from PySide6.QtGui import QImage

        editor = EditorWindow(Image.new("RGB", (48, 32), "green"), dict(DEFAULTS))
        image = QImage(48, 32, QImage.Format_ARGB32)
        image.fill(Qt.green)
        clipboard = Mock()
        try:
            editor.show()
            self.app.processEvents()
            with patch("editor.editor_window.QGuiApplication.clipboard",
                       return_value=clipboard), \
                    patch.object(editor, "output_image", return_value=image), \
                    patch.object(editor, "save") as save:
                button = next(button for button, action in editor.toolbar.command_buttons
                              if action == "copy_only")
                button.click()

            clipboard.setMimeData.assert_called_once()
            self.assertTrue(clipboard.setMimeData.call_args.args[0].hasImage())
            save.assert_not_called()
            self.assertFalse(editor.isVisible())
            self.assertIsNone(editor.last_path)
        finally:
            editor.close()

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

    def test_sticker_window_size_follows_screen_device_pixel_ratio(self):
        """贴图窗口尺寸必须是逻辑尺寸 = 物理像素 ÷ 屏幕 dpr（+ 两倍内边距）。

        1080 + 125% 这类屏幕下，若把物理像素当 Qt 尺寸，贴图窗口与图片会放大 dpr 倍
        （用户实测：2K 面板跑 1080+125% 时贴图比原图大一圈）。
        """
        from PySide6.QtGui import QImage
        from sticker.sticker_item import StickerItem

        image = QImage(200, 100, QImage.Format_RGBA8888)
        image.fill(0)
        item = StickerItem(image, None, {"sticker_border_enabled": False,
                                         "sticker_shadow_enabled": False})
        try:
            pad = item.padding()
            with patch.object(StickerItem, "device_pixel_ratio", return_value=1.0):
                item.apply_style()
                size = item.window_size()
                self.assertEqual((size.width(), size.height()), (200 + pad * 2, 100 + pad * 2))
                self.assertEqual(item.pixmap.devicePixelRatio(), 1.0)
            with patch.object(StickerItem, "device_pixel_ratio", return_value=1.25):
                item.apply_style()
                size = item.window_size()
                self.assertEqual((size.width(), size.height()), (160 + pad * 2, 80 + pad * 2))
                self.assertEqual(item.pixmap.devicePixelRatio(), 1.25)
                self.assertEqual(item.image_logical_size().width(), 160)
        finally:
            item.close()
    def test_window_element_detection_cycles_selection_with_tab(self):
        from PySide6.QtCore import QRect, Qt
        from PySide6.QtTest import QTest
        from config.config_manager import DEFAULTS
        from screenshot.mask_window import MaskWindow

        bounds = {"left": 0, "top": 0, "width": 100, "height": 80}
        settings = dict(DEFAULTS, inline_edit=False, magnifier=False, crosshair=False, window_detection=True,
                        element_depth=3, capture_after_selection="save")
        chain = [(0, 0, 100, 80), (10, 10, 60, 50), (20, 20, 40, 30)]
        with patch("screenshot.mask_window.visible_windows", return_value=[]), \
             patch("screenshot.mask_window.element_chain", return_value=chain):
            mask = MaskWindow(Image.new("RGB", (100, 80), "white"), bounds, [bounds], settings)
            edited = []
            mask.edit_requested.connect(lambda images, positions: edited.append(images))
            # 悬停识别要求遮罩可见（截图结束时不再查询系统窗口）。
            mask.show()
            self.app.processEvents()
            self.assertEqual(mask.element_chain, chain)
            QTest.keyClick(mask, Qt.Key_Tab)
            self.assertEqual(mask.selection.rects, [QRect(0, 0, 100, 80)])
            QTest.keyClick(mask, Qt.Key_Tab)
            self.assertEqual(mask.selection.rects, [QRect(10, 10, 50, 40)])
            self.assertFalse(mask.auto_complete_after_show)
            self.assertEqual(mask.selection.rects, [QRect(10, 10, 50, 40)])
            QTest.keyClick(mask, Qt.Key_Tab, Qt.ShiftModifier)
            self.assertEqual(mask.selection.rects, [QRect(0, 0, 100, 80)])
            QTest.mouseClick(mask, Qt.LeftButton, Qt.NoModifier, QPoint(20, 20))
            self.assertEqual(len(edited), 1)
            self.assertEqual(edited[0][0][0].size, (100, 80))
            mask.close()
            auto = MaskWindow(Image.new("RGB", (100, 80), "white"), bounds, [bounds],
                              dict(settings, window_auto_select=True))
            self.assertEqual(auto.selection.rects, [QRect(0, 0, 100, 80)])
            self.assertTrue(auto.auto_complete_after_show)
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
        # 提示条画在光标附近；本用例只校验悬停高亮，关掉提示项避免它盖住取样区。
        settings = dict(DEFAULTS, inline_edit=False, magnifier=False, crosshair=False, window_detection=True,
                        window_hover_detect=True, element_depth=3, capture_hint_order=[],
                        capture_after_selection="save")
        chain = [(0, 0, 100, 80), (10, 10, 60, 50)]
        with patch("screenshot.mask_window.visible_windows", return_value=[]), \
                patch("screenshot.mask_window.element_chain", return_value=chain):
            mask = MaskWindow(Image.new("RGB", (100, 80), "white"), bounds, [bounds], settings)
            try:
                edited = []
                mask.edit_requested.connect(lambda images, positions: edited.append((images, positions)))
                # 悬停识别要求遮罩可见（截图结束后不再查询系统窗口）。
                mask.show()
                self.app.processEvents()
                mask.hover_stamp = 0.0
                with patch.object(mask.mapper, "native_global_to_physical_global",
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
                # 遮罩不透明度来自配置（默认 70），按配置推算未选中区域的灰度，避免写死后过期。
                level = 255 - int(settings["mask_opacity"] * 255 / 100)
                self.assertEqual(rendered.pixelColor(90, 70).name(),
                                 "#%02x%02x%02x" % (level, level, level))
                self.assertEqual(settings["window_hover_fill_mode"], "reveal")
                QTest.mousePress(mask, Qt.LeftButton, Qt.NoModifier, QPoint(20, 20))
                QTest.mouseRelease(mask, Qt.LeftButton, Qt.NoModifier, QPoint(20, 20))
                self.assertEqual(len(edited), 1)
                self.assertEqual(edited[0][0][0][0].size, (50, 40))
                self.assertFalse(mask.isVisible())
            finally:
                mask.close()

    def test_hover_reuse_expires_after_small_pointer_movement(self):
        from PySide6.QtCore import QPoint, QRect
        from config.config_manager import DEFAULTS
        from screenshot.mask_window import MaskWindow

        bounds = {"left": 0, "top": 0, "width": 100, "height": 80}
        settings = dict(DEFAULTS, inline_edit=False, magnifier=False, crosshair=False,
                        window_detection=True, window_hover_detect=True,
                        capture_hint_order=[])
        with patch("screenshot.mask_window.visible_windows", return_value=[]):
            mask = MaskWindow(Image.new("RGB", (100, 80), "white"), bounds, [bounds], settings)
        try:
            mask.show()
            self.app.processEvents()
            mask.position = QPoint(21, 20)
            mask.hover_source_rect = None
            mask.hover_rect = QRect(0, 0, 100, 80)
            mask.hover_query_point = QPoint(20, 20)
            mask.hover_stamp = 1.0
            with patch.object(mask.mapper, "native_global_to_physical_global",
                              return_value=QPoint(21, 20)), \
                    patch.object(mask, "detect_hover") as detect_hover:
                with patch("screenshot.mask_window.time.monotonic", return_value=1.2):
                    mask.poll_hover()
                detect_hover.assert_not_called()
                with patch("screenshot.mask_window.time.monotonic", return_value=1.5):
                    mask.poll_hover()
                detect_hover.assert_called_once()
                settings["window_hover_reuse_radius"] = 0
                mask.hover_stamp = 1.5
                with patch("screenshot.mask_window.time.monotonic", return_value=2.0):
                    mask.poll_hover()
                self.assertEqual(detect_hover.call_count, 2)
        finally:
            mask.close()

    def test_multi_select_ui_element_click_appends_without_completing(self):
        from PySide6.QtCore import QPoint, QRect, Qt
        from PySide6.QtTest import QTest
        from config.config_manager import DEFAULTS
        from screenshot.mask_window import MaskWindow

        bounds = {"left": 0, "top": 0, "width": 100, "height": 80}
        settings = dict(DEFAULTS, magnifier=False, crosshair=False,
                        capture_hint_order=[])
        chain = [(10, 10, 60, 50)]
        with patch("screenshot.mask_window.visible_windows", return_value=[]), \
                patch("screenshot.mask_window.element_chain", return_value=chain):
            mask = MaskWindow(Image.new("RGB", (100, 80), "white"), bounds,
                              [bounds], settings)
        edited = []
        mask.edit_requested.connect(lambda images, positions: edited.append(images))
        try:
            mask.show()
            self.app.processEvents()
            mask.session.multi_select_mode = True
            mask.selection.rects.append(QRect(65, 10, 25, 25))
            mask.hover_rect = QRect(10, 10, 50, 40)
            QTest.mouseClick(mask, Qt.LeftButton, Qt.NoModifier, QPoint(20, 20))
            self.assertEqual(mask.selection.rects,
                             [QRect(65, 10, 25, 25), QRect(10, 10, 50, 40)])
            self.assertFalse(edited)
            self.assertTrue(mask.isVisible())
            QTest.keyClick(mask, Qt.Key_Return)
            self.assertEqual(len(edited), 1)
            self.assertEqual(len(edited[0]), 2)
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

    def test_uia_property_cache_reads_each_property_once(self):
        from types import SimpleNamespace
        from core import window_uia

        class CountingControl:
            def __init__(self):
                self._rect = SimpleNamespace(left=0, top=0, right=10, bottom=10)
                self.reads = {"name": 0, "type": 0, "handle": 0, "rect": 0}

            @property
            def Name(self):
                self.reads["name"] += 1
                return "控件"

            @property
            def ControlTypeName(self):
                self.reads["type"] += 1
                return "ButtonControl"

            @property
            def NativeWindowHandle(self):
                self.reads["handle"] += 1
                return 42

            @property
            def BoundingRectangle(self):
                self.reads["rect"] += 1
                return self._rect

        control = CountingControl()
        cache = window_uia._PropertyCache()
        for _ in range(3):
            self.assertEqual(window_uia._name_of(control, cache), "控件")
            self.assertEqual(window_uia._type_of(control, cache), "ButtonControl")
            self.assertEqual(window_uia._handle_of(control, cache), 42)
            self.assertEqual(window_uia._rect_of(control, cache), (0, 0, 10, 10))
        # 每个属性只跨进程读一次，重复查询命中缓存。
        self.assertEqual(control.reads, {"name": 1, "type": 1, "handle": 1, "rect": 1})
        # 未传缓存时退回直读，保证其它调用方的旧行为不变。
        self.assertEqual(window_uia._name_of(control, None), "控件")
        self.assertEqual(window_uia._name_of(control, None), "控件")
        self.assertEqual(control.reads["name"], 3)

    def test_uia_climb_skips_name_type_reads_when_debug_disabled(self):
        import logging
        from types import SimpleNamespace
        from core.window_uia import climb

        class CountingControl:
            def __init__(self):
                self.reads = {"name": 0, "type": 0}

            @property
            def Name(self):
                self.reads["name"] += 1
                return "按钮"

            @property
            def ControlTypeName(self):
                self.reads["type"] += 1
                return "ButtonControl"

            @property
            def NativeWindowHandle(self):
                return 42

            @property
            def BoundingRectangle(self):
                return SimpleNamespace(left=0, top=0, right=10, bottom=10)

            def GetParentControl(self):
                return None

        control = CountingControl()
        silent = logging.getLogger("test.climb.silent")
        silent.setLevel(logging.INFO)
        # 默认 INFO 日志下不读取名称/类型，避免为不输出的日志付出跨进程开销。
        self.assertEqual(climb(control, 42, 3, silent), [(0, 0, 10, 10)])
        self.assertEqual(control.reads, {"name": 0, "type": 0})

        loud = logging.getLogger("test.climb.loud")
        loud.setLevel(logging.DEBUG)
        with self.assertLogs(loud, level="DEBUG"):
            self.assertEqual(climb(control, 42, 3, loud), [(0, 0, 10, 10)])
        # 打开 DEBUG 后仍照常输出名称/类型，日志内容没有丢失。
        self.assertGreater(control.reads["name"], 0)
        self.assertGreater(control.reads["type"], 0)

    def test_uia_climb_uses_visible_root_frame_for_maximized_window(self):
        import logging
        from types import SimpleNamespace
        from core.window_uia import climb

        class Control:
            def __init__(self, handle, rect, parent=None):
                self.NativeWindowHandle = handle
                self.BoundingRectangle = SimpleNamespace(
                    left=rect[0], top=rect[1], right=rect[2], bottom=rect[3])
                self.parent = parent

            @property
            def ControlTypeName(self):
                return "WindowControl" if self.NativeWindowHandle == 42 else "PaneControl"

            @property
            def Name(self):
                return "maximized window" if self.NativeWindowHandle == 42 else "client area"

            def GetParentControl(self):
                return self.parent

        root = Control(42, (-8, -8, 1928, 1088))
        client_rect = (0, 30, 1920, 1050)
        client = Control(0, client_rect, root)
        visible_frame = (0, 0, 1920, 1080)
        result = climb(client, 42, 3, logging.getLogger("uia.frame"), visible_frame)
        self.assertEqual(result, [visible_frame, client_rect])

    def test_uia_climb_clamps_oversized_child_to_visible_frame(self):
        """子控件矩形比窗口可见框还大（虚拟滚动区/DPI 虚拟化）时收边，避免误判跨屏。"""
        import logging
        from types import SimpleNamespace
        from core.window_uia import climb

        class Control:
            def __init__(self, handle, rect, parent=None):
                self.NativeWindowHandle = handle
                self.BoundingRectangle = SimpleNamespace(
                    left=rect[0], top=rect[1], right=rect[2], bottom=rect[3])
                self.parent = parent

            @property
            def ControlTypeName(self):
                return "PaneControl"

            @property
            def Name(self):
                return "pane"

            def GetParentControl(self):
                return self.parent

        root = Control(42, (0, 0, 1920, 1080))
        child = Control(0, (-8, 30, 1920, 1050), root)
        result = climb(child, 42, 3, logging.getLogger("uia.clamp"), (0, 0, 1920, 1080))
        self.assertEqual(result, [(0, 0, 1920, 1080), (0, 30, 1920, 1050)])

    def test_uia_deepest_at_reads_name_only_for_ties(self):
        import logging
        from types import SimpleNamespace
        from core.window_uia import deepest_at

        class CountingControl:
            def __init__(self, name, control_type, rect, children=()):
                self._name = name
                self._type = control_type
                self._rect = SimpleNamespace(
                    left=rect[0], top=rect[1], right=rect[2], bottom=rect[3])
                self.children = list(children)
                self.name_reads = 0

            @property
            def Name(self):
                self.name_reads += 1
                return self._name

            @property
            def ControlTypeName(self):
                return self._type

            @property
            def BoundingRectangle(self):
                return self._rect

            def GetChildren(self):
                return self.children

        logger = logging.getLogger("test")
        unique = CountingControl("小按钮", "ButtonControl", (40, 40, 60, 60))
        root = CountingControl("根", "PaneControl", (0, 0, 100, 100), [unique])
        # 唯一最小候选时无需读名称即返回。
        self.assertIs(deepest_at(root, 50, 50, logger), unique)
        self.assertEqual(unique.name_reads, 0)

        # 同名尺寸并列时，仍按原规则优先取有名者。
        named = CountingControl("有名字", "ButtonControl", (40, 40, 60, 60))
        unnamed = CountingControl("", "ButtonControl", (40, 40, 60, 60))
        tied_root = CountingControl("根", "PaneControl", (0, 0, 100, 100),
                                    [unnamed, named])
        self.assertIs(deepest_at(tied_root, 50, 50, logger), named)

    def test_uia_own_control_stops_at_foreign_process(self):
        import os
        from unittest.mock import patch
        from core import window_uia

        class FakeControl:
            def __init__(self, handle, parent=None):
                self._handle = handle
                self._parent = parent

            @property
            def NativeWindowHandle(self):
                return self._handle

            def GetParentControl(self):
                return self._parent

        child = FakeControl(100, FakeControl(200))
        with patch("core.window_uia.process_of", return_value=os.getpid() + 1) as process:
            self.assertFalse(window_uia.own_control(child))
        # 上溯到其它进程即可断定不是本程序遮罩，无需继续走满父链。
        process.assert_called_once()

    def test_uia_deepest_at_respects_read_budget(self):
        """子控件极多时下钻读取有预算上限，不会逐个读完整棵子树。"""
        import logging
        from types import SimpleNamespace
        from core.window_uia import deepest_at

        class Counting:
            reads = 0

            def __init__(self, name, rect, children=(), control_type="PaneControl"):
                self._name = name
                self._rect = rect
                self._type = control_type
                self.children = list(children)

            @property
            def BoundingRectangle(self):
                Counting.reads += 1
                return SimpleNamespace(left=self._rect[0], top=self._rect[1],
                                       right=self._rect[2], bottom=self._rect[3])

            @property
            def ControlTypeName(self):
                return self._type

            @property
            def Name(self):
                return self._name

            def GetChildren(self):
                return list(self.children)

        Counting.reads = 0
        huge = Counting("huge", (0, 0, 200, 200),
                        [Counting(f"c{index}", (0, 0, 200, 200)) for index in range(500)])
        result = deepest_at(huge, 50, 50, logging.getLogger("test.budget"), read_budget=10)
        self.assertIsNotNone(result)
        # 预算 10，允许少量记账误差，但绝不该把 500 个子控件都读一遍。
        self.assertLessEqual(Counting.reads, 14)

    def test_uia_deepest_at_honors_read_budget_beyond_fixed_visit_cap(self):
        import logging
        from types import SimpleNamespace
        from core.window_uia import deepest_at

        class Control:
            def __init__(self, name, rect, children=(), control_type="PaneControl"):
                self.Name = name
                self.ControlTypeName = control_type
                self.BoundingRectangle = SimpleNamespace(
                    left=rect[0], top=rect[1], right=rect[2], bottom=rect[3])
                self.children = list(children)

            def GetChildren(self):
                return self.children

        target = Control("small button", (45, 45, 55, 55), control_type="ButtonControl")
        branches = [
            Control(f"branch {index}", (0, 0, 100, 100),
                    [target] if index == 69 else ())
            for index in range(70)
        ]
        root = Control("root", (0, 0, 100, 100), branches)

        result = deepest_at(root, 50, 50, logging.getLogger("test.visit_budget"),
                            read_budget=600)

        self.assertIs(result, target)

    def test_uia_deepest_only_skips_parent_chain(self):
        """悬停用的 deepest_only 只取最内层，不再沿父链上溯。"""
        from types import SimpleNamespace
        from core import window_uia

        class Control:
            NativeWindowHandle = 7
            BoundingRectangle = SimpleNamespace(left=10, top=10, right=50, bottom=40)
            ControlTypeName = "ButtonControl"
            Name = "按钮"

            def GetParentControl(self):
                return None

        with patch.object(window_uia, "module", return_value=object()), \
                patch.object(window_uia, "top_window_at", return_value=999), \
                patch.object(window_uia, "physical_rect_of", return_value=(0, 0, 100, 100)), \
                patch.object(window_uia, "control_at", return_value=Control()), \
                patch.object(window_uia, "climb") as climb:
            rects = window_uia.element_chain((30, 30), 8, deepest_only=True)
        self.assertEqual(rects, [(10, 10, 50, 40)])
        climb.assert_not_called()

    def test_uia_click_through_guards_and_caches_original_exstyle(self):
        """鼠标按下时不切换穿透（避免点击透传）；原始扩展样式按句柄只读一次。"""
        from types import SimpleNamespace
        from core import window_uia

        with patch.object(window_uia, "mouse_button_down", return_value=True):
            self.assertIsNone(window_uia.set_click_through(4321))

        reads, writes = [], []
        fake_user32 = SimpleNamespace(
            GetWindowLongW=lambda hwnd, index: reads.append(hwnd) or 0x10,
            SetWindowLongW=lambda hwnd, index, style: writes.append(style))
        hwnd = 987654
        window_uia._exstyle_cache.pop(int(hwnd), None)
        try:
            with patch.object(window_uia, "mouse_button_down", return_value=False), \
                    patch.object(window_uia.ctypes, "windll", SimpleNamespace(user32=fake_user32)):
                window_uia.set_click_through(hwnd)()
                window_uia.set_click_through(hwnd)()
        finally:
            window_uia.forget_click_through(hwnd)
        # 两次查询只读一次原始样式（缓存生效），共写四次（两次进入穿透 + 两次还原）。
        self.assertEqual(len(reads), 1)
        self.assertEqual(len(writes), 4)

    def test_uia_direct_children_walks_child_siblings_incrementally(self):
        """增量枚举要在子控件上取下一个兄弟；取到父控件的兄弟会把无关控件当子控件。"""
        import logging
        from core.window_uia import direct_children

        class Fake:
            def __init__(self, name):
                self.name = name
                self.next = None
                self.first = None

            def GetFirstChildControl(self):
                return self.first

            def GetNextSiblingControl(self):
                return self.next

        parent = Fake("parent")
        siblings = [Fake(f"c{index}") for index in range(3)]
        for left, right in zip(siblings, siblings[1:]):
            left.next = right
        parent.first = siblings[0]
        parent.next = Fake("outsider")  # 父控件自己的下一个兄弟，绝不能被当成子控件

        logger = logging.getLogger("test.children")
        self.assertEqual([child.name for child in direct_children(parent, logger)],
                         ["c0", "c1", "c2"])
        self.assertEqual([child.name for child in direct_children(parent, logger, limit=2)],
                         ["c0", "c1"])

        class Legacy:
            def __init__(self):
                self.children = [Fake("a"), Fake("b")]

            def GetChildren(self):
                return list(self.children)

        # 没有增量接口时退回 GetChildren。
        self.assertEqual([child.name for child in direct_children(Legacy(), logger)], ["a", "b"])

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

    # 待桌面验收：离屏环境下 QWidget 句柄取不到窗口所属进程与窗口属性，own_control 无法判定。
    @unittest.skip("需真实桌面环境，见上方注释")
    def test_uia_own_control_excludes_mask_only(self):
        pass

    def test_window_annotation_tool_selection_style_in_light_and_dark_theme(self):
        from PySide6.QtGui import QPalette
        from config.config_manager import DEFAULTS
        from ui.theme import apply_theme

        original_palette = QPalette(self.app.palette())
        original_mode = self.app.property("screensnap_theme_mode")
        editor = EditorWindow(Image.new("RGB", (120, 80), "white"), dict(DEFAULTS))
        try:
            for theme in ("light", "dark", "light"):
                apply_theme(self.app, theme)
                self.app.processEvents()
                self._assert_annotation_tool_selection_style(editor.toolbar, theme == "dark")
        finally:
            editor.close()
            self.app.setProperty("screensnap_theme_mode", original_mode)
            self.app.setPalette(original_palette)
            self.app.processEvents()


if __name__ == "__main__":  # 支持 python tests/test_uia.py
    unittest.main()
