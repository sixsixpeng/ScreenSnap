"""工具交互：工具栏、选择、拖动、对齐与命令路由。"""

import os
import sys
from pathlib import Path

# 向上找到仓库根（含 main.py），无论从哪一层、以哪种方式运行都能导入 tests.base。
_ROOT = Path(__file__).resolve().parent
while not (_ROOT / "main.py").exists() and _ROOT.parent != _ROOT:
    _ROOT = _ROOT.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tests.base import (CoreTests, Mock, patch, Path, tempfile, json, unittest, Image,
                        QPointF, Qt, QPoint, QRect, QRectF, QTest, QApplication,
                        ConfigManager, resolved_dir, AnnotationCanvas, shape,
                        EditorWindow, SelectionRects, StickerItem, StickerManager,
                        SettingsWindow, HotkeyEdit)


class ToolsTests(CoreTests):
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

    def test_right_collection_double_click_confirms_into_inline_editor(self):
        from config.config_manager import DEFAULTS
        from screenshot.mask_window import MaskWindow

        bounds = {"left": 0, "top": 0, "width": 120, "height": 80}
        settings = {**DEFAULTS, "inline_edit": True, "capture_after_selection": "save",
                    "crosshair": False, "magnifier": False, "sound": False}
        with patch("screenshot.mask_window.visible_windows", return_value=[]):
            mask = MaskWindow(Image.new("RGB", (120, 80), "blue"), bounds, [bounds], settings)
        mask.show()
        self.app.processEvents()
        start, end = QPoint(10, 10), QPoint(50, 40)
        QTest.mousePress(mask, Qt.RightButton, Qt.NoModifier, start)
        QTest.mouseMove(mask, end)
        QTest.mouseRelease(mask, Qt.RightButton, Qt.NoModifier, end)
        self.assertTrue(mask.session.right_capture_mode)
        QTest.mousePress(mask, Qt.RightButton, Qt.NoModifier, QPoint(20, 20))
        QTest.mouseRelease(mask, Qt.RightButton, Qt.NoModifier, QPoint(20, 20))
        QTest.mouseDClick(mask, Qt.RightButton, Qt.NoModifier, QPoint(20, 20))
        self.assertTrue(mask.isVisible())
        self.assertIsNotNone(mask.session.inline_editor)
        self.assertIsNone(mask.session.inline_editor.last_path)

    def test_inline_tool_rows_align_with_toolbar_side(self):
        from config.config_manager import DEFAULTS
        from screenshot.mask_window import MaskWindow

        with tempfile.TemporaryDirectory() as folder:
            bounds = {"left": 0, "top": 0, "width": 1200, "height": 900}
            settings = {**DEFAULTS, "save_dir": folder, "inline_edit": True, "magnifier": False}
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
                self.assertGreater(editor.toolbar.height(), 45)
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

    def test_inline_editor_output_contains_no_ui(self):
        """未做编辑时，内联编辑的画布渲染必须与冻结帧选区逐点一致（不含按钮/提示等 UI）。"""
        from config.config_manager import DEFAULTS
        from screenshot.mask_window import MaskWindow

        frozen = Image.new("RGB", (200, 150))
        for y in range(150):
            for x in range(200):
                frozen.putpixel((x, y), ((x * 3) % 256, (y * 5) % 256, ((x + y) * 7) % 256))
        bounds = {"left": 0, "top": 0, "width": 200, "height": 150}
        with patch("screenshot.mask_window.visible_windows", return_value=[]):
            mask = MaskWindow(frozen, bounds, [bounds],
                              dict(DEFAULTS, inline_edit=True, magnifier=False,
                                   capture_after_selection="edit"))
        mask.selection.rects.append(QRect(40, 30, 100, 80))
        mask.complete()
        mask.show()
        self.app.processEvents()
        editor = mask.session.inline_editor
        self.assertIsNotNone(editor)
        rendered = editor.canvas.render_image()
        self.assertEqual((rendered.width(), rendered.height()), (100, 80))
        for x, y in ((0, 0), (50, 40), (99, 79), (10, 70), (99, 0)):
            expected = frozen.getpixel((40 + x, 30 + y))
            color = rendered.pixelColor(x, y)
            self.assertEqual((color.red(), color.green(), color.blue()), expected, (x, y))
        mask.close()

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

    def test_toolbar_options_panel_shows_live_preview(self):
        from config.config_manager import DEFAULTS
        from editor.toolbar_widget import ToolbarWidget

        toolbar = ToolbarWidget(settings=dict(DEFAULTS))
        try:
            self.assertEqual(sorted(toolbar.previews),
                             ["arrow", "crop", "ellipse", "eraser", "marker", "mosaic", "pen", "picker", "rect", "sequence", "text"])
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

    def test_inline_editor_toolbar_has_secondary_appearance_menu(self):
        from screenshot.mask_window import InlineEditor, MaskWindow

        with tempfile.TemporaryDirectory() as folder:
            bounds = {"left": 0, "top": 0, "width": 120, "height": 80}
            settings = {**ConfigManager(Path(folder) / "settings.json").data,
                        "save_dir": folder, "filename": "inline-appearance",
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

    def test_inline_editor_resave_uses_changed_format_extension(self):
        from config.config_manager import DEFAULTS
        from screenshot.mask_window import MaskWindow

        with tempfile.TemporaryDirectory() as folder:
            bounds = {"left": 0, "top": 0, "width": 120, "height": 80}
            settings = dict(DEFAULTS, save_dir=folder, filename="inline_format",
                            save_format="png", inline_edit=True,
                            capture_after_selection="edit", crosshair=False,
                            magnifier=False, mask_opacity=0, bubble=False)
            with patch("screenshot.mask_window.visible_windows", return_value=[]):
                mask = MaskWindow(Image.new("RGB", (120, 80), "blue"), bounds,
                                  [bounds], settings)
            try:
                mask.selection.rects.append(QRect(10, 10, 30, 20))
                mask.complete()
                editor = mask.session.inline_editor
                first = editor.save(automatic=True)
                settings["save_format"] = "jpg"
                second = editor.save(automatic=True)
                self.assertEqual(first.suffix, ".png")
                self.assertEqual(second.suffix, ".jpg")
                self.assertTrue(first.exists())
                with Image.open(first) as first_image:
                    self.assertEqual(first_image.format, "PNG")
                with Image.open(second) as second_image:
                    self.assertEqual(second_image.format, "JPEG")
            finally:
                mask.close()

    def test_clear_data_directory_removes_all_contents_without_recreating_directory(self):
        from core.path_utils import clear_data_directory

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "ScreenSnap"
            nested = root / "clipboard_history"
            nested.mkdir(parents=True)
            (root / "stickers.json").write_text("old session", encoding="utf-8")
            (nested / "old.png").write_bytes(b"old cache")

            with patch("core.path_utils.data_dir", return_value=root):
                self.assertEqual(clear_data_directory(), root)

            self.assertFalse(root.exists())

    def test_dark_theme_sequence_tool_icon_contrasts_with_button_surface(self):
        from PySide6.QtGui import QPalette
        from config.config_manager import DEFAULTS
        from editor.toolbar_widget import ToolbarWidget, annotation_icon
        from ui.theme import apply_theme

        toolbar = ToolbarWidget(settings=dict(DEFAULTS))
        try:
            toolbar.show()
            self.app.processEvents()
            light_icon = toolbar.tool_buttons["number"].icon().pixmap(24, 24).toImage()
            apply_theme(self.app, "dark")
            toolbar.sync_theme_icons()
            self.app.processEvents()
            dark_icon = toolbar.tool_buttons["number"].icon().pixmap(24, 24).toImage()
            self.assertNotEqual(bytes(light_icon.constBits()), bytes(dark_icon.constBits()))
            background = self.app.palette().color(QPalette.Button)
            direct_icon = annotation_icon("number").pixmap(24, 24).toImage()
            contrasting = sum(
                1 for y in range(direct_icon.height())
                for x in range(direct_icon.width())
                if direct_icon.pixelColor(x, y).alpha() > 0 and
                direct_icon.pixelColor(x, y).value() > background.value() + 45)
            self.assertGreater(contrasting, 20)
        finally:
            toolbar.close()
            apply_theme(self.app, "system")

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
                cell = panel.layout().itemAtPosition(row, 0)
                if cell is None:  # 该工具不占此行（面板按当前工具动态建行）
                    continue
                self.assertEqual(cell.widget().text(), expected_labels[row])
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
                                  {"filled", "open", "dashed", "double_filled", "double_open",
                                   "double_dashed", "line", "dashed_line", "rect_filled",
                                   "rect_open", "rect_dashed"})
                self.assertTrue(toolbar.choice_buttons["arrow_style"]["open"].isChecked() is False)
                self.assertGreaterEqual(panel.minimumWidth(), 760)
            if tool in ("rect", "ellipse"):
                self.assertLessEqual(panel.minimumWidth(), 520)
        panel.close()


if __name__ == "__main__":
    unittest.main()


if __name__ == "__main__":
    unittest.main()
