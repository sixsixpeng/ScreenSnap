"""ScreenSnap 测试共享基类：公共导入、QApplication 夹具与断言辅助。"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from PIL import Image
from PySide6.QtCore import QPointF, Qt, QPoint, QRect, QRectF
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from config.config_manager import ConfigManager
from core.path_utils import resolved_dir
from editor.annotation_canvas import AnnotationCanvas
from editor.annotation_items import shape
from editor.editor_window import EditorWindow
from screenshot.selection_rect import SelectionRects
from sticker.sticker_item import StickerItem
from sticker.sticker_manager import StickerManager
from ui.settings_window import SettingsWindow
from ui.widgets.hotkey_edit import HotkeyEdit



class CoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        # 关闭会抢系统前台/注册系统键盘钩子的兜底逻辑，避免自动化测试干扰真实按键。
        from core import window_focus
        from screenshot import mask_window

        cls._foreground_fallback = window_focus.FOREGROUND_FALLBACK
        cls._escape_fallback = mask_window.ESCAPE_FALLBACK_ENABLED
        window_focus.FOREGROUND_FALLBACK = False
        mask_window.ESCAPE_FALLBACK_ENABLED = False

    @classmethod
    def tearDownClass(cls):
        from core import window_focus
        from screenshot import mask_window

        window_focus.FOREGROUND_FALLBACK = cls._foreground_fallback
        mask_window.ESCAPE_FALLBACK_ENABLED = cls._escape_fallback


    def _check_options_after_double_click_delete(self, editor):
        from PySide6.QtCore import QTimer
        from config.config_manager import TOOL_WIDTH_KEYS

        canvas, toolbar = editor.canvas, editor.toolbar
        toolbar.tool_buttons["select"].click()
        rect = shape("rect", QPointF(40, 40), QPointF(120, 100), "#ff0000", 3,
                     corner_radius=12, fill_enabled=True, fill_opacity=100)
        ellipse = shape("ellipse", QPointF(170, 40), QPointF(250, 100), "#00ff00", 3,
                        fill_enabled=True, fill_opacity=100)
        canvas.scene_data.addItem(rect)
        canvas.scene_data.addItem(ellipse)
        canvas.checkpoint()
        rect.setSelected(True)
        ellipse.setSelected(True)
        QTest.mouseDClick(canvas.viewport(), Qt.LeftButton,
                         pos=canvas.mapFromScene(QPointF(210, 70)))
        QTest.mouseRelease(canvas.viewport(), Qt.LeftButton,
                           pos=canvas.mapFromScene(QPointF(210, 70)))
        self.app.processEvents()
        self.assertEqual(canvas.annotations(), [rect])
        self.assertEqual(canvas.selected_annotation_tool(), "")
        self.assertIsNone(toolbar.selected_tool)
        self.assertEqual(toolbar._last_option_rows, set())
        self.assertFalse(toolbar.options_button.isEnabled())
        QTest.mouseClick(toolbar.options_button, Qt.LeftButton)
        self.assertFalse(toolbar.options_button.menu().isVisible())

        QTest.mouseClick(canvas.viewport(), Qt.LeftButton,
                         pos=canvas.mapFromScene(QPointF(80, 70)))
        self.app.processEvents()
        self.assertEqual(canvas.selected_annotation_tool(), "rect")
        self.assertEqual(toolbar.selected_tool, "rect")
        self.assertEqual(toolbar._last_option_rows, {0, 1, 10, 12, 13, 15, 16, 32})
        self.assertEqual(toolbar.options_button.text(), "矩形设置")
        button, menu = toolbar.options_button, toolbar.options_button.menu()
        toolbar.set_selected_tool("ellipse")
        opened = []

        def inspect_menu():
            try:
                opened.append((menu.isVisible(), toolbar.tool_color_buttons["rect_color"].isVisible(),
                               toolbar.previews["rect"].isVisible(),
                               toolbar.option_rows[8][0].isVisible(), menu.width()))
                toolbar.pen_width.setValue(7)
            finally:
                menu.hide()

        QTimer.singleShot(0, inspect_menu)
        QTest.mouseClick(button, Qt.LeftButton)
        self.app.processEvents()
        self.assertEqual(opened[0][:4], (True, True, True, False))
        self.assertGreaterEqual(opened[0][4], toolbar.option_panel_width("rect"))
        self.assertEqual(rect.pen().width(), 7)
        self.assertEqual(editor.settings[TOOL_WIDTH_KEYS["rect"]], 7)
        self.assertEqual(toolbar.pen_width_label.text(), "7 px")
        toolbar.setting_changed.emit("rect_color", "#123456")
        self.assertEqual(rect.pen().color().name(), "#123456")

        canvas.scene_data.clearSelection()
        self.assertFalse(button.isEnabled())
        canvas.undo()
        self.assertFalse(button.isEnabled())
        canvas.redo()
        self.assertFalse(button.isEnabled())
        restored = canvas.annotations()[0]
        restored.setSelected(True)
        self.assertEqual(toolbar.selected_tool, "rect")
        self.assertTrue(button.isEnabled())
        QTest.mouseDClick(canvas.viewport(), Qt.LeftButton,
                         pos=canvas.mapFromScene(QPointF(80, 70)))
        QTest.mouseRelease(canvas.viewport(), Qt.LeftButton,
                           pos=canvas.mapFromScene(QPointF(80, 70)))
        self.assertEqual(canvas.annotations(), [])
        self.assertFalse(button.isEnabled())
        self.assertEqual(toolbar._last_option_rows, set())

    def _assert_annotation_tool_selection_style(self, toolbar, dark):
        from PySide6.QtGui import QColor, QImage, QPainter, QPalette
        from PySide6.QtWidgets import QStyle, QStyleOptionToolButton

        backgrounds = ("#484848", "#535353", "#606060") if dark else (
            "#dedede", "#d3d3d3", "#c4c4c4")
        border = QColor("#aaaaaa" if dark else "#666666")
        foreground = QColor("#f5f5f5" if dark else "#202020")
        toolbar.tool_buttons["rect"].click()
        button = toolbar.tool_buttons["rect"]
        self.assertEqual(sum(item.isChecked() for item in toolbar.tool_buttons.values()), 1)
        self.assertTrue(button.property("annotationTool"))
        self.assertFalse(toolbar.options_button.property("annotationTool"))
        button.ensurePolished()
        size = button.size()
        for extra, background in zip((QStyle.State_None, QStyle.State_MouseOver,
                                      QStyle.State_Sunken), backgrounds):
            with self.subTest(dark=dark, state=extra):
                option = QStyleOptionToolButton()
                option.initFrom(button)
                option.rect = button.rect()
                option.state = QStyle.State_Enabled | QStyle.State_On | extra
                option.text = "Tool"
                option.toolButtonStyle = Qt.ToolButtonTextOnly
                image = QImage(size, QImage.Format_ARGB32)
                image.fill(Qt.transparent)
                painter = QPainter(image)
                button.style().drawComplexControl(QStyle.CC_ToolButton, option, painter, button)
                painter.end()
                self.assertEqual(image.pixelColor(size.width() // 2, 3), QColor(background))
                self.assertEqual(image.pixelColor(size.width() // 2, 0), border)
                self.assertTrue(any(
                    image.pixelColor(x, y) == foreground
                    for x in range(2, size.width() - 2)
                    for y in range(5, size.height() - 5)))
        toolbar.tool_buttons["ellipse"].click()
        self.assertFalse(button.isChecked())
        self.assertTrue(toolbar.tool_buttons["ellipse"].isChecked())
        self.assertEqual(button.size(), size)
        self.assertEqual(sum(item.isChecked() for item in toolbar.tool_buttons.values()), 1)
    def _pen_drag(self, canvas, start, end, modifiers=Qt.KeyboardModifiers()):
        """在画布上用给定修饰键从 start 拖到 end，模拟一次笔划。"""
        from PySide6.QtCore import QPointF
        from PySide6.QtTest import QTest
        pa = canvas.mapFromScene(QPointF(*start))
        pb = canvas.mapFromScene(QPointF(*end))
        QTest.mousePress(canvas.viewport(), Qt.LeftButton, modifiers, pos=pa)
        QTest.mouseMove(canvas.viewport(), pb)
        QTest.mouseRelease(canvas.viewport(), Qt.LeftButton, modifiers, pos=pb)
