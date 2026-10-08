"""设置页与控件：分组、预览、主题、对话框与提示。"""

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


class UiTests(CoreTests):
    def test_color_button_swatch_is_larger_outside_compact_controls(self):
        from PySide6.QtCore import QSize
        from ui.widgets.color_button import ColorButton

        standard = ColorButton("#12ab34", lambda _color: None)
        compact = ColorButton("#12ab34", lambda _color: None, compact=True)
        self.assertEqual(standard.iconSize(), QSize(48, 28))
        self.assertEqual(compact.iconSize(), QSize(18, 18))
        standard.set_color("#abcdef")
        pixmap = standard.icon().pixmap(standard.iconSize())
        self.assertEqual(pixmap.size(), QSize(48, 28))
        self.assertEqual(pixmap.toImage().pixelColor(24, 14).name(), "#abcdef")

    def test_color_dialog_stays_on_top(self):
        from PySide6.QtWidgets import QColorDialog
        from ui.widgets.color_button import color_dialog

        dialog = color_dialog("#12ab34", None)
        self.assertTrue(dialog.windowFlags() & Qt.WindowStaysOnTopHint)
        self.assertEqual(dialog.windowTitle(), "选择颜色")
        dialog.deleteLater()

    def test_color_dialog_parents_to_stable_outermost_window(self):
        from PySide6.QtWidgets import QWidget
        from ui.widgets.color_button import color_dialog

        # 模拟“更多设置”结构：置顶遮罩(Tool) -> 工具栏 -> QMenu 弹出层 -> 色块。
        mask = QWidget()
        mask.setWindowFlags(Qt.WindowStaysOnTopHint | Qt.Tool)
        toolbar = QWidget(mask)
        popup = QWidget(toolbar)  # 实际是 QMenu，这里只验证 owner 回溯与稳定父级。
        button = QWidget(popup)
        dialog = color_dialog("#12ab34", button)
        # 不能挂在会随取色关闭的弹出层下；应挂到最外层稳定窗口（遮罩），随截图关闭且不丢外壳。
        self.assertIs(dialog.parent(), mask)
        self.assertTrue(dialog.windowFlags() & Qt.WindowStaysOnTopHint)
        dialog.deleteLater()
        mask.deleteLater()

    def test_drag_preview_visible_but_not_exported(self):
        from config.config_manager import DEFAULTS
        canvas = AnnotationCanvas(Image.new("RGB", (120, 100), "white"), DEFAULTS)
        canvas.resize(240, 200)
        canvas.tool = "rect"
        canvas.show()
        self.app.processEvents()
        start = canvas.mapFromScene(QPointF(10, 10))
        end = canvas.mapFromScene(QPointF(60, 50))
        QTest.mousePress(canvas.viewport(), Qt.LeftButton, pos=start)
        QTest.mouseMove(canvas.viewport(), end)
        self.app.processEvents()
        self.assertIsNotNone(canvas.preview_end)
        self.assertEqual(len(canvas.annotations()), 0)
        self.assertEqual(canvas.render_image().pixelColor(10, 30).name(), "#ffffff")
        self.assertNotEqual(canvas.viewport().grab().toImage().pixelColor(canvas.mapFromScene(QPointF(10, 30))).name(),
                            "#ffffff")
        QTest.mouseRelease(canvas.viewport(), Qt.LeftButton, pos=end)
        self.assertEqual(len(canvas.annotations()), 1)
        self.assertIsNone(canvas.preview_end)
        canvas.close()

    def test_all_drag_tools_show_preview(self):
        from config.config_manager import DEFAULTS
        for tool in ("rect", "ellipse", "arrow", "pen", "marker", "mosaic", "crop"):
            with self.subTest(tool=tool):
                canvas = AnnotationCanvas(Image.new("RGB", (120, 100), "white"), DEFAULTS)
                canvas.resize(240, 200)
                canvas.tool = tool
                canvas.show()
                self.app.processEvents()
                before = canvas.viewport().grab().toImage().bits().tobytes()
                QTest.mousePress(canvas.viewport(), Qt.LeftButton, pos=canvas.mapFromScene(QPointF(10, 10)))
                QTest.mouseMove(canvas.viewport(), canvas.mapFromScene(QPointF(65, 55)))
                self.app.processEvents()
                self.assertNotEqual(canvas.viewport().grab().toImage().bits().tobytes(), before)
                self.assertEqual(canvas.render_image().pixelColor(30, 30).name(), "#ffffff")
                QTest.mouseRelease(canvas.viewport(), Qt.LeftButton,
                                   pos=canvas.mapFromScene(QPointF(65, 55)))
                canvas.close()

    def test_angle_preview_cancel_and_confirm(self):
        from PySide6.QtWidgets import QDialog, QDoubleSpinBox
        from config.config_manager import DEFAULTS
        editor = EditorWindow(Image.new("RGB", (40, 20), "red"), DEFAULTS.copy())
        baseline = editor.canvas.image
        history = editor.canvas.cursor_index

        def cancel(dialog):
            from PySide6.QtWidgets import QDial
            dial = dialog.findChild(QDial)
            self.assertGreaterEqual(dial.minimumWidth(), 190)
            self.assertGreaterEqual(dialog.minimumWidth(), 500)
            dialog.findChild(QDoubleSpinBox).setValue(30)
            self.assertNotEqual(editor.canvas.image.size, baseline.size)
            return QDialog.Rejected

        with patch("editor.editor_window.QDialog.exec", cancel):
            editor.execute("angle")
        self.assertIs(editor.canvas.image, baseline)
        self.assertEqual(editor.canvas.cursor_index, history)

        def accept(dialog):
            dialog.findChild(QDoubleSpinBox).setValue(30)
            return QDialog.Accepted

        with patch("editor.editor_window.QDialog.exec", accept):
            editor.execute("angle")
        self.assertNotEqual(editor.canvas.image.size, baseline.size)
        self.assertEqual(editor.canvas.cursor_index, history + 1)
        editor.close()

    def test_color_dialog_chinese_labels_and_common_colors(self):
        from PySide6.QtGui import QColor
        from PySide6.QtWidgets import QColorDialog, QLabel
        from ui.widgets.color_button import COMMON_COLORS, color_dialog
        dialog = color_dialog("#123456", None)
        self.assertEqual(dialog.windowTitle(), "选择颜色")
        self.assertTrue(dialog.testOption(QColorDialog.DontUseNativeDialog))
        self.assertEqual(dialog.currentColor().name(), "#123456")
        labels = [label.text() for label in dialog.findChildren(QLabel)]
        self.assertTrue(any("颜色" in label for label in labels), labels)
        self.assertTrue(all("colors" not in label.lower() for label in labels), labels)
        for index, hex_color in enumerate(COMMON_COLORS):
            self.assertEqual(QColorDialog.customColor(index), QColor(hex_color))
        self.assertTrue({"#ff0000", "#00a000", "#0000ff", "#000000", "#ffffff"}
                        <= set(COMMON_COLORS))
        dialog.close()

    def test_output_appearance_is_visible_and_preview_updates(self):
        from config.config_manager import DEFAULTS
        from PySide6.QtCore import QRect, Qt
        from PySide6.QtWidgets import QCheckBox
        from editor.toolbar_widget import ToolbarWidget, settings_icon
        from ui.window_bounds import WindowBoundsFilter, anchored_popup_geometry

        toolbar = ToolbarWidget(settings=dict(DEFAULTS))
        screen = self.app.primaryScreen()
        available = screen.availableGeometry()
        toolbar.resize(700, 500)
        toolbar.move(available.left() + 40, available.top() + 40)
        toolbar.show()
        self.app.processEvents()
        commands = []
        toolbar.command.connect(commands.append)
        try:
            self.assertEqual(toolbar.output_preview.kind, "output")
            self.assertFalse(toolbar.appearance_menu.isVisible())
            self.assertIs(toolbar.appearance_details.window(), toolbar.appearance_menu)
            self.assertIs(toolbar.output_preview.window(), toolbar.appearance_menu)
            self.assertLessEqual(toolbar.output_preview.minimumHeight(), 128)
            for key, label in (("editor_image_round_corners", "启用圆角"),
                               ("editor_image_border_enabled", "启用边框"),
                               ("editor_image_shadow_enabled", "启用阴影")):
                self.assertIsInstance(toolbar.appearance_controls[key], QCheckBox)
                self.assertEqual(toolbar.appearance_controls[key].text(), label)
            self.assertEqual(toolbar.appearance_toggle.icon().pixmap(24, 24).toImage(),
                             settings_icon().pixmap(24, 24).toImage())
            self.assertEqual([button.text() for button in toolbar.output_buttons[:3]],
                             ["贴图", "外观", "保存"])
            self.assertFalse(toolbar.output_preview.isHidden())
            toolbar.appearance_toggle.click()
            self.app.processEvents()
            self.assertTrue(toolbar.appearance_menu.isVisible())
            self.assertFalse(WindowBoundsFilter._should_constrain(
                toolbar.appearance_menu))
            anchor = QRect(
                toolbar.appearance_toggle.mapToGlobal(
                    toolbar.appearance_toggle.rect().topLeft()),
                toolbar.appearance_toggle.size())
            expected = anchored_popup_geometry(
                anchor, toolbar.appearance_menu.size(), available)
            self.assertEqual(toolbar.appearance_menu.geometry().topLeft(),
                             expected.topLeft())
            self.assertTrue(available.contains(toolbar.appearance_menu.geometry()))
            self.assertEqual(toolbar.appearance_scroll.horizontalScrollBarPolicy(),
                             Qt.ScrollBarAsNeeded)
            toolbar.appearance_toggle.click()
            self.app.processEvents()
            self.assertFalse(toolbar.appearance_menu.isVisible())
            self.assertFalse(toolbar.appearance_toggle.isChecked())
            toolbar.appearance_toggle.click()
            self.app.processEvents()
            self.assertTrue(toolbar.appearance_toggle.isChecked())
            for width in (1300, 950, 700, 420, 1300):
                toolbar.resize(width, 760)
                self.app.processEvents()
                self.assertTrue(toolbar.appearance_menu.isVisible(), width)
            toolbar.appearance_menu.close()
            self.assertFalse(toolbar.appearance_menu.isVisible())
            self.assertFalse(toolbar.appearance_toggle.isChecked())
            toolbar.appearance_toggle.click()
            self.app.processEvents()
            self.assertTrue(toolbar.appearance_menu.isVisible())
            toolbar.appearance_menu.close()
            self.assertFalse(toolbar.output_preview.isHidden())
            action_texts = {action.text() for action in toolbar.options_button.menu().actions()}
            self.assertNotIn("输出图像外观", action_texts)
            self.assertIn("自定义尺寸", [button.text() for button in toolbar.edit_buttons])
            self.assertIn("重新截图", [button.text() for button in toolbar.edit_buttons])
            toolbar.edit_buttons[-2].click()
            toolbar.edit_buttons[-1].click()
            self.assertEqual(commands, ["custom_size", "recapture"])
            toolbar.output_preview.scene = "cached"
            toolbar.appearance_controls["editor_image_border_width"].setValue(8)
            self.assertIsNone(toolbar.output_preview.scene)
            setting_events = []
            toolbar.setting_changed.connect(lambda key, value: setting_events.append((key, value)))
            toolbar.sync_appearance_controls(dict(DEFAULTS,
                editor_image_round_corners=False,
                editor_image_corner_radius=24,
                editor_image_border_width=6))
            self.assertFalse(toolbar.appearance_controls["editor_image_round_corners"].isChecked())
            self.assertEqual(toolbar.appearance_controls["editor_image_corner_radius"].value(), 24)
            self.assertEqual(toolbar.appearance_value_labels["editor_image_corner_radius"].text(), "24 px")
            self.assertEqual(setting_events, [])
        finally:
            toolbar.close()

    def test_output_shadow_does_not_shrink_preview_subject(self):
        from PySide6.QtGui import QColor, QImage
        from config.config_manager import DEFAULTS
        from ui.widgets import annotation_preview
        from ui.widgets.annotation_preview import AnnotationPreview

        sample = QImage(40, 20, QImage.Format_RGB32)
        sample.fill(QColor("#ff0000"))

        def red_bounds(scene):
            image = scene.items()[0].pixmap().toImage()
            pixels = [(x, y) for y in range(image.height()) for x in range(image.width())
                      if image.pixelColor(x, y).red() > 220 and
                      image.pixelColor(x, y).green() < 30]
            return (min(x for x, _ in pixels), min(y for _, y in pixels),
                    max(x for x, _ in pixels), max(y for _, y in pixels))

        with patch.object(annotation_preview, "sample_image", return_value=sample):
            settings = dict(DEFAULTS, editor_image_round_corners=False,
                            editor_image_border_enabled=False,
                            editor_image_shadow_enabled=False)
            preview = AnnotationPreview(settings, "output")
            plain_bounds = red_bounds(preview.build(QRectF(0, 0, 320, 208)))
            settings["editor_image_shadow_enabled"] = True
            preview.refresh()
            shadow_bounds = red_bounds(preview.build(QRectF(0, 0, 320, 208)))

        self.assertLessEqual(abs((plain_bounds[2] - plain_bounds[0]) -
                                 (shadow_bounds[2] - shadow_bounds[0])), 1)
        self.assertLessEqual(abs((plain_bounds[3] - plain_bounds[1]) -
                                 (shadow_bounds[3] - shadow_bounds[1])), 1)

    def test_confirmation_dialog_buttons_are_chinese(self):
        from PySide6.QtWidgets import QMessageBox
        from ui.widgets.confirmation import yes_no_dialog

        dialog = yes_no_dialog(None, "确认", "是否继续？")
        try:
            self.assertEqual(dialog.button(QMessageBox.Yes).text(), "是")
            self.assertEqual(dialog.button(QMessageBox.No).text(), "否")
            self.assertEqual(dialog.defaultButton(), dialog.button(QMessageBox.No))
        finally:
            dialog.close()

    def test_checkbox_and_radio_indicators_remain_distinct_in_both_themes(self):
        from PySide6.QtGui import QColor
        from PySide6.QtWidgets import QCheckBox, QRadioButton
        from ui.theme import apply_theme

        def render(widget, checked, enabled=True):
            widget.setChecked(checked)
            widget.setEnabled(enabled)
            self.assertEqual(widget.isChecked(), checked,
                             (type(widget).__name__, checked, enabled))
            widget.show()
            self.app.processEvents()
            image = widget.grab().toImage()
            return tuple(image.pixel(x, y) for y in range(image.height())
                         for x in range(image.width()))

        try:
            for mode in ("dark", "light"):
                apply_theme(self.app, mode)
                for widget_type in (QCheckBox, QRadioButton):
                    widget = widget_type()
                    if isinstance(widget, QRadioButton):
                        widget.setAutoExclusive(False)
                    widget.setFixedSize(24, 24)
                    off = render(widget, False)
                    on = render(widget, True)
                    disabled_off = render(widget, False, False)
                    disabled_on = render(widget, True, False)
                    self.assertNotEqual(off, on, (mode, widget_type.__name__, "checked"))
                    self.assertNotEqual(disabled_off, disabled_on,
                                        (mode, widget_type.__name__, "disabled checked"))
                    self.assertNotEqual(on, disabled_on,
                                        (mode, widget_type.__name__, "disabled"))
                    if mode == "dark":
                        visible_edge_pixels = sum(
                            1 for pixel in off
                            if QColor.fromRgba(pixel).alpha() and
                            QColor.fromRgba(pixel).lightness() >= 150)
                        self.assertGreaterEqual(visible_edge_pixels, 8,
                                                (widget_type.__name__, visible_edge_pixels))
                        white_marker_pixels = sum(
                            1 for pixel in on
                            if QColor.fromRgba(pixel).alpha() and
                            QColor.fromRgba(pixel).red() >= 245 and
                            QColor.fromRgba(pixel).green() >= 245 and
                            QColor.fromRgba(pixel).blue() >= 245)
                        self.assertGreater(white_marker_pixels, 0,
                                           (widget_type.__name__, "missing marker"))
                    widget.close()
        finally:
            apply_theme(self.app, "system")

    def test_shown_dialog_is_moved_inside_screen(self):
        from ui.window_bounds import WindowBoundsFilter
        from PySide6.QtWidgets import QDialog

        bounds_filter = WindowBoundsFilter(self.app)
        self.app.installEventFilter(bounds_filter)
        dialog = QDialog()
        dialog.resize(180, 120)
        area = self.app.primaryScreen().availableGeometry()
        dialog.move(area.right() + 100, area.bottom() + 100)
        try:
            dialog.show()
            self.app.processEvents()
            self.app.processEvents()
            self.assertTrue(area.contains(dialog.frameGeometry()))
        finally:
            dialog.close()
            self.app.removeEventFilter(bounds_filter)

    def test_action_icons_follow_light_and_dark_palette(self):
        from ui.action_icons import action_icon
        from ui.theme import apply_theme
        from PySide6.QtGui import QPainter, QPalette, QPixmap

        def icon_colors():
            image = icon.pixmap(24, 24).toImage()
            return {
                image.pixelColor(x, y).name()
                for y in range(image.height()) for x in range(image.width())
                if image.pixelColor(x, y).alpha() > 0
            }

        try:
            icon = action_icon("window_edit")
            apply_theme(self.app, "dark")
            target = QPixmap(24, 24)
            target.fill(Qt.transparent)
            painter = QPainter(target)
            icon.paint(painter, QRect(0, 0, 24, 24))
            painter.end()
            self.assertGreater(sum(
                target.toImage().pixelColor(x, y).alpha() > 0
                for y in range(24) for x in range(24)), 0)
            dark_colors = icon_colors()
            self.assertIn("#e8eaed", dark_colors)
            self.assertIn(self.app.palette().color(QPalette.Highlight).name(), dark_colors)
            apply_theme(self.app, "light")
            light_colors = icon_colors()
            self.assertIn("#202020", light_colors)
            self.assertIn("#176b87", light_colors)
        finally:
            apply_theme(self.app, "system")

    def test_dark_theme_buttons_and_highlights_have_readable_contrast(self):
        from PySide6.QtGui import QColor, QPalette
        from PySide6.QtWidgets import QLineEdit, QPushButton, QToolButton
        from ui.theme import apply_theme

        def luminance(color):
            channels = [color.redF(), color.greenF(), color.blueF()]
            linear = [value / 12.92 if value <= 0.04045 else
                      ((value + 0.055) / 1.055) ** 2.4 for value in channels]
            return sum(weight * value for weight, value in
                       zip((0.2126, 0.7152, 0.0722), linear))

        def contrast(first, second):
            high, low = sorted((luminance(first), luminance(second)), reverse=True)
            return (high + 0.05) / (low + 0.05)

        apply_theme(self.app, "dark")
        try:
            palette = self.app.palette()
            button = palette.color(QPalette.Button)
            button_text = palette.color(QPalette.ButtonText)
            highlight = palette.color(QPalette.Highlight)
            highlighted_text = palette.color(QPalette.HighlightedText)
            self.assertGreaterEqual(contrast(button, button_text), 4.5)
            self.assertGreaterEqual(
                contrast(palette.color(QPalette.Midlight), button_text), 4.5)
            self.assertGreaterEqual(contrast(palette.color(QPalette.Mid), button_text), 4.5)
            self.assertGreaterEqual(contrast(highlight, highlighted_text), 4.5)
            disabled_text = palette.color(QPalette.Disabled, QPalette.ButtonText)
            disabled_button = palette.color(QPalette.Disabled, QPalette.Button)
            self.assertGreaterEqual(contrast(disabled_button, disabled_text), 4.5)
            self.assertEqual(palette.color(QPalette.Midlight).name(), "#3b3e43")
            stylesheet = self.app.styleSheet()
            for selector in ("QPushButton, QToolButton", "QMenu::item:selected",
                             "QLineEdit, QTextEdit"):
                self.assertIn(selector, stylesheet)
            self.assertIn(button.name(), stylesheet)
            self.assertIn(button_text.name(), stylesheet)
            push_button = QPushButton("操作")
            push_button.setFixedSize(120, 40)
            tool_button = QToolButton()
            tool_button.setText("工具")
            tool_button.setFixedSize(120, 40)
            disabled_push_button = QPushButton("禁用")
            disabled_push_button.setFixedSize(120, 40)
            disabled_push_button.setEnabled(False)
            enabled_input = QLineEdit("启用")
            enabled_input.setFixedSize(160, 40)
            disabled_input = QLineEdit("禁用")
            disabled_input.setFixedSize(160, 40)
            disabled_input.setEnabled(False)
            for widget in (push_button, tool_button, disabled_push_button,
                           enabled_input, disabled_input):
                # 离屏环境下光标固定停在 (10,10)，顶层控件默认落在 (0,0) 会被判成 hover，
                # grab() 就渲染成悬停底色（Midlight）而不是常态底色（Button）；先挪开再取样。
                widget.move(600, 600)
            self.assertEqual(push_button.palette().color(QPalette.Button).name(),
                             button.name())
            self.assertEqual(tool_button.palette().color(QPalette.ButtonText).name(),
                             button_text.name())
            push_button.show()
            tool_button.show()
            disabled_push_button.show()
            enabled_input.show()
            disabled_input.show()
            self.app.processEvents()
            for widget in (push_button, tool_button):
                self.assertEqual(widget.grab().toImage().pixelColor(10, 20).name(),
                                 button.name())
            self.assertNotEqual(
                disabled_push_button.grab().toImage().pixelColor(10, 20).name(),
                push_button.grab().toImage().pixelColor(10, 20).name())
            enabled_input_image = enabled_input.grab().toImage()
            disabled_input_image = disabled_input.grab().toImage()
            self.assertNotEqual(
                enabled_input_image.pixelColor(145, 20).name(),
                disabled_input_image.pixelColor(145, 20).name())
            push_button.close()
            tool_button.close()
            disabled_push_button.close()
            enabled_input.close()
            disabled_input.close()
            apply_theme(self.app, "light")
            self.assertNotIn("QPushButton, QToolButton", self.app.styleSheet())
            light_palette = self.app.palette()
            light_disabled_text = light_palette.color(
                QPalette.Disabled, QPalette.ButtonText)
            light_disabled_surface = light_palette.color(
                QPalette.Disabled, QPalette.Button)
            self.assertGreaterEqual(
                contrast(light_disabled_surface, light_disabled_text), 4.5)
            enabled_light_button = QPushButton("操作")
            enabled_light_tool_button = QToolButton()
            enabled_light_tool_button.setText("工具")
            disabled_light_tool_button = QToolButton()
            disabled_light_tool_button.setText("禁用工具")
            disabled_light_tool_button.setEnabled(False)
            disabled_light_button = QPushButton("禁用")
            disabled_light_button.setEnabled(False)
            enabled_light_input = QLineEdit("启用")
            disabled_light_input = QLineEdit("禁用")
            disabled_light_input.setEnabled(False)
            for widget in (enabled_light_button, enabled_light_tool_button,
                           disabled_light_tool_button,
                           disabled_light_button,
                           enabled_light_input, disabled_light_input):
                widget.setFixedSize(160, 40)
                widget.show()
            self.app.processEvents()
            self.assertNotEqual(
                enabled_light_button.grab().toImage().pixelColor(10, 20).name(),
                disabled_light_button.grab().toImage().pixelColor(10, 20).name())
            self.assertEqual(
                enabled_light_tool_button.palette().color(QPalette.Button).name(),
                light_palette.color(QPalette.Button).name())
            self.assertNotEqual(
                enabled_light_tool_button.grab().toImage().pixelColor(10, 20).name(),
                disabled_light_tool_button.grab().toImage().pixelColor(10, 20).name())
            self.assertNotEqual(
                enabled_light_input.grab().toImage().pixelColor(145, 20).name(),
                disabled_light_input.grab().toImage().pixelColor(145, 20).name())
            for widget in (enabled_light_button, enabled_light_tool_button,
                           disabled_light_tool_button,
                           disabled_light_button,
                           enabled_light_input, disabled_light_input):
                widget.close()
        finally:
            apply_theme(self.app, "system")

    def test_theme_palette_propagates_to_widget_families(self):
        from PySide6.QtGui import QPalette
        from PySide6.QtWidgets import (QCheckBox, QColorDialog, QComboBox,
                                       QDateEdit, QDateTimeEdit, QDialog,
                                       QDoubleSpinBox, QFileDialog, QFontComboBox,
                                       QGroupBox, QKeySequenceEdit, QLabel,
                                       QLineEdit, QListWidget, QMenu, QPlainTextEdit,
                                       QProgressBar, QPushButton, QRadioButton,
                                       QScrollBar, QSlider, QSpinBox, QTextEdit,
                                       QTimeEdit, QDial)
        from ui.theme import apply_theme, THEME_DISABLED_TEXT, THEME_PALETTES

        widget_types = (QLabel, QCheckBox, QRadioButton, QPushButton, QLineEdit,
                        QTextEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox,
                        QComboBox, QFontComboBox, QDateEdit, QTimeEdit,
                        QDateTimeEdit, QKeySequenceEdit, QListWidget, QGroupBox,
                        QSlider, QDial, QScrollBar, QProgressBar, QMenu, QDialog,
                        QColorDialog, QFileDialog)

        def luminance(color):
            channels = [color.redF(), color.greenF(), color.blueF()]
            linear = [value / 12.92 if value <= 0.04045 else
                      ((value + 0.055) / 1.055) ** 2.4 for value in channels]
            return sum(weight * value for weight, value in
                       zip((0.2126, 0.7152, 0.0722), linear))

        try:
            for mode in ("dark", "light"):
                apply_theme(self.app, mode)
                expected = THEME_PALETTES[mode]
                expected_text = expected[QPalette.WindowText]
                expected_highlight = expected[QPalette.Highlight]
                expected_disabled = THEME_DISABLED_TEXT[mode]
                disabled_base = self.app.palette().color(
                    QPalette.Disabled, QPalette.Base)
                disabled_text = self.app.palette().color(
                    QPalette.Disabled, QPalette.Text)
                high, low = sorted((luminance(disabled_base),
                                    luminance(disabled_text)), reverse=True)
                self.assertGreaterEqual((high + 0.05) / (low + 0.05), 4.5, mode)
                for widget_type in widget_types:
                    with self.subTest(mode=mode, widget=widget_type.__name__):
                        widget = widget_type()
                        palette = widget.palette()
                        self.assertEqual(palette.color(QPalette.Active,
                                                        QPalette.WindowText).name(),
                                         expected_text)
                        self.assertEqual(palette.color(QPalette.Active,
                                                       QPalette.Base).name(),
                                         expected[QPalette.Base])
                        self.assertEqual(palette.color(QPalette.Active,
                                                       QPalette.Highlight).name(),
                                         expected_highlight)
                        self.assertEqual(palette.color(QPalette.Disabled,
                                                       QPalette.Text).name(),
                                         expected_disabled)
                        widget.close()
        finally:
            apply_theme(self.app, "system")


if __name__ == "__main__":  # 支持 python tests/test_ui.py
    unittest.main()
