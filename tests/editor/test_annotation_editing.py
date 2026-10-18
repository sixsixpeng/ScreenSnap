"""标注对象级编辑：复制、锁定、参数对话框与文字背景增强。"""

import os
import sys
from pathlib import Path

# 向上找到仓库根（含 main.py），保证 tests.base 可导入。
_ROOT = Path(__file__).resolve().parent
while not (_ROOT / "main.py").exists() and _ROOT.parent != _ROOT:
    _ROOT = _ROOT.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tests.base import (CoreTests, unittest, QPointF, Qt, Image, QTest)

from config.config_manager import DEFAULTS
from editor.annotation_canvas import AnnotationCanvas
from editor.annotation_items import (AnnotationSequenceItem, annotation_locked,
                                     shape, text_item)
from editor.annotation_properties import AnnotationPropertiesDialog
from editor.editor_window import EditorWindow


class AnnotationEditingTests(CoreTests):
    def _canvas(self, **overrides):
        settings = dict(DEFAULTS, **overrides)
        canvas = AnnotationCanvas(Image.new("RGB", (320, 220), "white"), settings)
        self.addCleanup(canvas.close)
        return canvas

    def test_duplicate_selected_offsets_and_supports_undo_redo(self):
        canvas = self._canvas()
        rect = shape("rect", QPointF(30, 30), QPointF(100, 80), "#ff0000", 3)
        canvas.scene_data.addItem(rect)
        rect.setSelected(True)
        canvas.checkpoint()

        self.assertTrue(canvas.duplicate_selected())
        self.assertEqual(len(canvas.annotations()), 2)
        selected = canvas.scene_data.selectedItems()
        self.assertEqual(len(selected), 1, "复制后应只选中副本")
        copy = selected[0]
        self.assertIsNot(copy, rect)
        self.assertEqual(copy.pos(), rect.pos() + QPointF(12, 12))

        canvas.undo()
        self.assertEqual(len(canvas.annotations()), 1)
        canvas.redo()
        self.assertEqual(len(canvas.annotations()), 2)

    def test_duplicate_sequence_gets_next_number(self):
        canvas = self._canvas()
        first = AnnotationSequenceItem(1, "#ff0000", "#ffffff", 14, "circle")
        canvas.scene_data.addItem(first)
        first.setSelected(True)
        canvas.duplicate_selected()
        numbers = sorted(item.number for item in canvas.annotations()
                         if isinstance(item, AnnotationSequenceItem))
        self.assertEqual(numbers, [1, 2])

    def test_locked_annotation_cannot_be_deleted_or_duplicated(self):
        canvas = self._canvas()
        rect = shape("rect", QPointF(20, 20), QPointF(90, 70), "#ff0000", 3)
        canvas.scene_data.addItem(rect)
        rect.setSelected(True)
        canvas.checkpoint()

        canvas.set_item_locked(rect, True)
        self.assertTrue(annotation_locked(rect))
        from PySide6.QtWidgets import QGraphicsItem
        self.assertFalse(rect.flags() & QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        self.assertFalse(rect.isSelected())
        canvas.scene_data.clearSelection()
        canvas.remove_selected()
        self.assertEqual(len(canvas.annotations()), 1)
        self.assertFalse(canvas.duplicate_selected())
        self.assertEqual(len(canvas.annotations()), 1)

        # 锁定状态随撤销/重做快照保留。
        canvas.undo()
        canvas.redo()
        restored = [item for item in canvas.annotations() if not isinstance(
            item, AnnotationSequenceItem)][0]
        self.assertTrue(annotation_locked(restored))

        menu = canvas.annotation_menu(restored)
        self.assertEqual(menu.actions()[0].text(), "解锁标注")
        self.assertFalse(menu.actions()[-1].isEnabled())
        menu.actions()[0].trigger()
        self.assertFalse(annotation_locked(restored))
        self.assertTrue(restored.flags() & QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)

    def test_properties_dialog_only_changes_the_target_item(self):
        canvas = self._canvas()
        rect = shape("rect", QPointF(40, 40), QPointF(140, 100), "#ff0000", 2)
        canvas.scene_data.addItem(rect)
        before = dict(canvas.settings)
        dialog = AnnotationPropertiesDialog(None, rect)
        try:
            dialog.controls["color"].set_color("#00ff00")
            dialog.controls["width"].setValue(7)
            dialog.controls["style"].setCurrentIndex(
                dialog.controls["style"].findData("dash"))
            dialog.controls["fill_enabled"].setChecked(True)
            dialog.controls["fill_color"].set_color("#0000ff")
            dialog.controls["fill_opacity"].setValue(50)
            dialog.controls["corner"].setValue(10)
            dialog.apply()
        finally:
            dialog.deleteLater()
        changed = canvas.annotations()[0]
        self.assertIsNot(changed, rect, "圆角从 0 改为正数时应替换为圆角矩形")
        self.assertEqual(changed.corner_radius, 10)
        self.assertEqual(changed.pen().color().name(), "#00ff00")
        self.assertEqual(changed.pen().style(), Qt.DashLine)
        self.assertEqual(changed.brush().color().name(), "#0000ff")
        self.assertEqual(changed.brush().color().alpha(), 128)
        # 只改对象：默认配置完全不变。
        self.assertEqual(canvas.settings, before)

    def test_text_background_padding_radius_and_opacity(self):
        item = text_item(QPointF(20, 20), "文字", dict(
            DEFAULTS, text_background_enabled=True, text_background_padding=6,
            text_background_radius=4, text_background_opacity=50,
            text_width=180, text_height=0), Qt.AlignLeft)
        content = item.content_rect()
        self.assertEqual(item.boundingRect().width(), content.width() + 12)
        self.assertEqual(item.boundingRect().height(), content.height() + 12)
        self.assertEqual(item.background_radius, 4)
        self.assertEqual(item.background_opacity, 50)

        image = Image.new("RGB", (260, 160), "white")
        canvas = AnnotationCanvas(image, dict(DEFAULTS))
        try:
            canvas.scene_data.addItem(item)
            rendered = canvas.render_image()
            # 左侧内边距区域应出现半透明黄色背景（50% 叠在白色底图上）。
            pixel = rendered.pixelColor(16, 30)
            from PySide6.QtGui import QColor
            self.assertNotEqual(pixel, QColor("#ffffff"))
            self.assertGreater(pixel.green(), pixel.blue())
        finally:
            canvas.close()

    def test_output_appearance_sync_gate(self):
        editor = EditorWindow(Image.new("RGB", (120, 90), "white"), dict(DEFAULTS))
        try:
            events = []
            editor.setting_changed.connect(
                lambda key, value: events.append((key, value)))
            editor.set_annotation_setting("editor_image_border_width", 9)
            self.assertNotIn(("editor_image_border_width", 9), events,
                             "未同步默认配置时不得写回设置")
            self.assertEqual(editor.appearance_overrides["editor_image_border_width"], 9)
            self.assertEqual(editor.output_settings()["editor_image_border_width"], 9)
            self.assertNotEqual(editor.settings.get("editor_image_border_width"), 9,
                                "共享配置不得被本次输出改写")

            editor.set_annotation_setting("editor_output_sync_default", True)
            self.assertIn(("editor_image_border_width", 9), events,
                          "打开同步时应把当前外观值一并写回")
            editor.set_annotation_setting("editor_image_border_width", 7)
            self.assertIn(("editor_image_border_width", 7), events)
        finally:
            editor.close()

    def test_inline_editor_output_appearance_sync_gate(self):
        from unittest.mock import patch
        from PySide6.QtCore import QRect
        from screenshot.mask_window import MaskWindow
        from tests.base import ConfigManager, tempfile, Path

        with tempfile.TemporaryDirectory() as folder:
            bounds = {"left": 0, "top": 0, "width": 120, "height": 80}
            settings = {**ConfigManager(Path(folder) / "settings.json").data,
                        "save_dir": folder, "filename": "inline-sync",
                        "inline_edit": True, "crosshair": False, "magnifier": False,
                        "capture_after_selection": "edit",
                        "mask_opacity": 0, "bubble": False}
            with patch("screenshot.mask_window.visible_windows", return_value=[]):
                mask = MaskWindow(Image.new("RGB", (120, 80), "#d02020"),
                                  bounds, [bounds], settings)
            try:
                mask.selection.rects.append(QRect(20, 20, 80, 50))
                mask.show()
                self.app.processEvents()
                mask.complete()
                self.app.processEvents()
                editor = mask.session.inline_editor
                events = []
                mask.session.views[0].annotation_setting_changed.connect(
                    lambda key, value: events.append((key, value)))

                editor.set_annotation_setting("editor_image_border_width", 9)
                self.assertNotIn(("editor_image_border_width", 9), events)
                self.assertEqual(editor.appearance_overrides["editor_image_border_width"], 9)
                self.assertNotEqual(editor.settings.get("editor_image_border_width"), 9)

                editor.set_annotation_setting("editor_output_sync_default", True)
                self.assertIn(("editor_image_border_width", 9), events)
                self.assertEqual(editor.settings["editor_image_border_width"], 9)
            finally:
                mask.close()

    def test_zoom_tool_creates_magnified_copy(self):
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QGraphicsPixmapItem

        canvas = self._canvas(zoom_annotation_factor=3)
        canvas.set_tool("zoom")
        start = canvas.mapFromScene(QPointF(20, 20))
        end = canvas.mapFromScene(QPointF(60, 50))
        QTest.mousePress(canvas.viewport(), Qt.LeftButton, pos=start)
        QTest.mouseMove(canvas.viewport(), end)
        QTest.mouseRelease(canvas.viewport(), Qt.LeftButton, pos=end)
        self.app.processEvents()
        items = [item for item in canvas.annotations()
                 if isinstance(item, QGraphicsPixmapItem)]
        self.assertEqual(len(items), 1)
        item = items[0]
        # 框选 40x30，按 3 倍生成 120x90 的放大副本。
        self.assertEqual(item.pixmap().width(), 120)
        self.assertEqual(item.pixmap().height(), 90)
        self.assertGreaterEqual(item.pos().x(), 60)


    def test_zoom_tool_respects_max_size_when_drawing(self):
        """绘制时自动按最大边长封顶，而不是先生成超大副本。"""
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QGraphicsPixmapItem

        canvas = self._canvas(zoom_annotation_factor=3, zoom_annotation_max_size=80)
        canvas.set_tool("zoom")
        start = canvas.mapFromScene(QPointF(20, 20))
        end = canvas.mapFromScene(QPointF(60, 50))
        QTest.mousePress(canvas.viewport(), Qt.LeftButton, pos=start)
        QTest.mouseMove(canvas.viewport(), end)
        QTest.mouseRelease(canvas.viewport(), Qt.LeftButton, pos=end)
        self.app.processEvents()
        items = [item for item in canvas.annotations()
                 if isinstance(item, QGraphicsPixmapItem)]
        self.assertEqual(len(items), 1)
        self.assertEqual((items[0].pixmap().width(), items[0].pixmap().height()), (80, 60))

    def test_insert_image_adds_scaled_annotation(self):
        from PySide6.QtWidgets import QGraphicsPixmapItem
        from tests.base import tempfile, Path

        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "insert.png"
            Image.new("RGB", (400, 200), "#00a000").save(source)
            canvas = self._canvas()
            item = canvas.add_pixmap_file(source)
            self.assertIsNotNone(item)
            self.assertIsInstance(item, QGraphicsPixmapItem)
            self.assertEqual((item.pixmap().width(), item.pixmap().height()), (256, 128))
            self.assertTrue(item.isSelected())
            canvas.undo()
            self.assertEqual([entry for entry in canvas.annotations()
                              if isinstance(entry, QGraphicsPixmapItem)], [])




    def test_image_tool_double_click_text_still_edits_existing_text(self):
        """图片工具不能吞掉文字双击编辑：已有文字仍走 edit_text_item，不弹插入框。"""
        from unittest.mock import patch

        editor = EditorWindow(Image.new("RGB", (320, 220), "white"), dict(DEFAULTS))
        editor.show()
        self.app.processEvents()
        try:
            text = text_item(QPointF(40, 40), "已有文字", dict(DEFAULTS), Qt.AlignLeft)
            editor.canvas.scene_data.addItem(text)
            editor.toolbar.tool_buttons["image"].click()
            inserted = []
            editor.canvas.image_insert_requested.connect(inserted.append)
            center = text.mapToScene(text.boundingRect().center())
            with patch.object(editor.canvas, "edit_text_item") as edit,                     patch("editor.editor_window.QFileDialog.getOpenFileName",
                          return_value=("", "")):
                QTest.mouseDClick(editor.canvas.viewport(), Qt.LeftButton,
                                  pos=editor.canvas.mapFromScene(center))
            self.assertTrue(edit.called)
            self.assertEqual(inserted, [])
        finally:
            editor.close()

    def test_image_tool_does_not_insert_on_existing_annotation_or_handle(self):
        """图片工具点在已有标注/手柄上时，应走选择/拖动/缩放，不得弹插入文件框。"""
        from unittest.mock import patch
        from tests.base import tempfile, Path

        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "existing.png"
            Image.new("RGB", (40, 30), "#00a000").save(source)
            editor = EditorWindow(Image.new("RGB", (320, 220), "white"), dict(DEFAULTS))
            editor.show()
            self.app.processEvents()
            try:
                item = editor.canvas.add_pixmap_file(source)
                editor.canvas.scene_data.clearSelection()
                editor.toolbar.tool_buttons["image"].click()
                self.assertEqual(editor.canvas.tool, "image")
                inserted = []
                editor.canvas.image_insert_requested.connect(inserted.append)
                with patch("editor.editor_window.QFileDialog.getOpenFileName",
                           return_value=("", "")):
                    body = item.mapToScene(item.boundingRect().center())
                    QTest.mouseClick(editor.canvas.viewport(), Qt.LeftButton,
                                     pos=editor.canvas.mapFromScene(body))
                    self.assertTrue(item.isSelected())
                    sub = editor.canvas.item_resize_handles(item)["se"]
                    QTest.mouseClick(editor.canvas.viewport(), Qt.LeftButton,
                                     pos=editor.canvas.mapFromScene(sub))
                    rotation = editor.canvas.rotation_handle_position(item)
                    QTest.mouseClick(editor.canvas.viewport(), Qt.LeftButton,
                                     pos=editor.canvas.mapFromScene(rotation))
                self.assertEqual(inserted, [])
                confirmed = []
                editor.canvas.confirmed.connect(confirmed.append)
                with patch("editor.editor_window.QFileDialog.getOpenFileName",
                           return_value=("", "")):
                    QTest.mouseDClick(
                        editor.canvas.viewport(), Qt.LeftButton,
                        pos=editor.canvas.mapFromScene(QPointF(10, 10)))
                self.assertEqual(confirmed, [])
                self.assertEqual(len([entry for entry in editor.canvas.annotations()
                                      if isinstance(entry, type(item))]), 1)
            finally:
                editor.close()

    def test_insert_image_tool_places_at_canvas_click_and_has_label(self):
        """插入图片是工具：先选中，再点画布位置，不能点按钮就居中插入。"""
        from unittest.mock import patch
        from tests.base import tempfile, Path
        from PySide6.QtCore import QPoint
        from PySide6.QtWidgets import QGraphicsPixmapItem

        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "insert.png"
            Image.new("RGB", (40, 30), "#00a000").save(source)
            editor = EditorWindow(Image.new("RGB", (320, 220), "white"), dict(DEFAULTS))
            editor.show()
            self.app.processEvents()
            try:
                button = editor.toolbar.tool_buttons["image"]
                self.assertEqual(button.text(), "图片")
                button.click()
                self.assertEqual(editor.canvas.tool, "image")
                point = editor.canvas.mapFromScene(QPointF(160, 110))
                with patch("editor.editor_window.QFileDialog.getOpenFileName",
                           return_value=(str(source), "")):
                    QTest.mouseClick(editor.canvas.viewport(), Qt.LeftButton, pos=point)
                items = [item for item in editor.canvas.annotations()
                         if isinstance(item, QGraphicsPixmapItem)]
                self.assertEqual(len(items), 1)
                expected = editor.canvas.mapToScene(point)
                center = items[0].pos() + QPointF(items[0].pixmap().width() / 2,
                                                 items[0].pixmap().height() / 2)
                self.assertAlmostEqual(center.x(), expected.x(), delta=2)
                self.assertAlmostEqual(center.y(), expected.y(), delta=2)
            finally:
                editor.close()

    def test_new_settings_pass_validation(self):
        from config.config_manager import validate

        self.assertEqual(validate({"text_background_padding": 8})["text_background_padding"], 8)
        self.assertEqual(validate({"text_background_radius": 6})["text_background_radius"], 6)
        self.assertEqual(validate({"text_background_opacity": 60})["text_background_opacity"], 60)
        self.assertEqual(validate({"zoom_annotation_factor": 3})["zoom_annotation_factor"], 3)
        self.assertEqual(validate({"zoom_annotation_factor": 12})["zoom_annotation_factor"], 12)
        self.assertEqual(validate({"zoom_annotation_max_size": 600})["zoom_annotation_max_size"], 600)
        self.assertEqual(validate({"zoom_annotation_color": "#123abc"})["zoom_annotation_color"],
                         "#123abc")
        for key, bad in (("text_background_padding", 33),
                         ("text_background_radius", -1),
                         ("text_background_opacity", 101),
                         ("zoom_annotation_factor", 17),
                         ("zoom_annotation_max_size", 79),
                         ("zoom_annotation_max_size", 1201),
                         ("zoom_annotation_color", "#12345")):
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate({key: bad})


    def test_zoom_tool_selection_keeps_config_valid(self):
        """切换到「放大」工具不能写入非法 annotation_tool（否则整份配置保存失败）。"""
        from config.config_manager import validate
        from tests.base import ConfigManager, Path, tempfile

        validate({"annotation_tool": "zoom"})
        with tempfile.TemporaryDirectory() as folder:
            manager = ConfigManager(Path(folder) / "settings.json")
            editor = EditorWindow(Image.new("RGB", (120, 80), "white"), manager.data)
            try:
                editor.set_tool("zoom")
                manager.save()
            finally:
                editor.close()
        self.assertEqual(manager.data["annotation_tool"], "zoom")

    def test_settings_window_sync_toggle_routes_and_persists(self):
        """编辑器「同步修改默认配置」开关必须能路由到保存与输出页并真正落盘。"""
        from tests.base import ConfigManager, Path, SettingsWindow, tempfile

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "settings.json"
            manager = ConfigManager(path)
            window = SettingsWindow(manager)
            try:
                window.set_annotation_setting("editor_output_sync_default", True)
                window.flush_persist()
                self.assertTrue(manager.data["editor_output_sync_default"])
                control = window.page("保存与输出").controls["editor_output_sync_default"]
                self.assertTrue(control.isChecked())
                window.set_annotation_setting("editor_output_sync_default", False)
                window.flush_persist()
                self.assertFalse(manager.data["editor_output_sync_default"])
                reloaded = ConfigManager(path)
                self.assertFalse(reloaded.data["editor_output_sync_default"])
            finally:
                window.close()

    def test_properties_dialog_lock_survives_corner_replacement(self):
        """参数对话框同时改圆角并勾选锁定时，锁定必须落在替换后的图元上。"""
        from PySide6.QtWidgets import QGraphicsItem

        canvas = self._canvas()
        rect = shape("rect", QPointF(40, 40), QPointF(140, 100), "#ff0000", 2)
        canvas.scene_data.addItem(rect)
        rect.setSelected(True)
        dialog = AnnotationPropertiesDialog(None, rect)
        try:
            dialog.controls["corner"].setValue(10)
            dialog.locked_check.setChecked(True)
            returned = dialog.apply()
        finally:
            dialog.deleteLater()
        self.assertIsNot(returned, rect)
        self.assertIs(returned.scene(), canvas.scene_data)
        self.assertTrue(annotation_locked(returned))
        self.assertFalse(returned.flags() & QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        self.assertFalse(returned.isSelected())

    def test_locked_text_cannot_be_edited_by_double_click(self):
        """锁定后的文字标注不响应双击编辑（右键菜单同样只提供解锁）。"""
        from unittest.mock import patch
        from PySide6.QtCore import QEvent
        from PySide6.QtGui import QMouseEvent

        canvas = self._canvas()
        item = text_item(QPointF(20, 20), "锁定文字", dict(DEFAULTS), Qt.AlignLeft)
        canvas.scene_data.addItem(item)
        canvas.set_item_locked(item, True)
        center = item.mapToScene(item.boundingRect().center())
        position = canvas.mapFromScene(center)
        event = QMouseEvent(QEvent.MouseButtonDblClick, QPointF(position),
                            QPointF(canvas.viewport().mapToGlobal(position)),
                            Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
        with patch.object(canvas, "input_text", return_value=(None, {}, False)) as dialog:
            canvas.mouseDoubleClickEvent(event)
        dialog.assert_not_called()

    def test_arrow_color_survives_style_change_and_width_sync(self):
        """改箭头颜色后再换样式（含预设路径）不能把颜色/线宽回退成旧值。"""
        canvas = self._canvas()
        arrow = shape("arrow", QPointF(20, 20), QPointF(160, 80), "#ff0000", 3, "filled")
        canvas.scene_data.addItem(arrow)
        arrow.setSelected(True)
        canvas.set_selected_color("#168cff")
        self.assertEqual(arrow.line_color, "#168cff")
        canvas.set_selected_width(6)
        canvas.set_selected_arrow_style("double_filled")
        self.assertEqual(arrow.arrow_style, "double_filled")
        self.assertEqual(arrow.pen().color().name(), "#168cff")
        self.assertEqual(arrow.line_color, "#168cff")
        self.assertAlmostEqual(arrow.pen().widthF(), 6.0, places=3)
        self.assertAlmostEqual(arrow.line_width, 6.0, places=3)

    def test_ctrl_d_duplicates_but_ctrl_shift_d_does_not(self):
        """Ctrl+D 复制；带 Shift/Alt 的 Ctrl+D 不触发（避免多修饰键误复制）。"""
        from PySide6.QtCore import QEvent
        from PySide6.QtGui import QKeyEvent

        canvas = self._canvas()
        rect = shape("rect", QPointF(20, 20), QPointF(90, 70), "#ff0000", 3)
        canvas.scene_data.addItem(rect)
        rect.setSelected(True)
        # 先记录一个基线历史节点，undo 才能回到“只有原标注”的状态。
        canvas.checkpoint()
        canvas.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_D, Qt.ControlModifier))
        self.assertEqual(len(canvas.annotations()), 2)
        canvas.undo()
        self.assertEqual(len(canvas.annotations()), 1)
        rect = canvas.annotations()[0]
        rect.setSelected(True)
        canvas.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_D,
                                       Qt.ControlModifier | Qt.ShiftModifier))
        self.assertEqual(len(canvas.annotations()), 1)

    def test_insert_image_clears_previous_selection(self):
        """插入图片后旧标注不应保持选中（否则会变成多选、参数面板置灰）。"""
        from tests.base import Path, tempfile

        canvas = self._canvas()
        rect = shape("rect", QPointF(20, 20), QPointF(90, 70), "#ff0000", 3)
        canvas.scene_data.addItem(rect)
        rect.setSelected(True)
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "insert.png"
            Image.new("RGB", (40, 30), "#00a000").save(source)
            item = canvas.add_pixmap_file(source)
        selected = canvas.scene_data.selectedItems()
        self.assertEqual(selected, [item])

    def test_text_background_padding_does_not_jump_on_first_vertical_resize(self):
        """首次拖上下手柄时，背景 padding 不能被 fixed_height 再算一次（高度跳 2×padding）。"""
        canvas = self._canvas()
        item = text_item(QPointF(20, 20), "背景文字", dict(
            DEFAULTS, text_background_enabled=True, text_background_padding=6,
            text_width=180), Qt.AlignLeft)
        canvas.scene_data.addItem(item)
        item.setSelected(True)
        before = item.boundingRect().height()
        canvas.resize_start = QPointF(0, 0)
        canvas.text_resize_origin = None
        canvas._resize_text_box(item, "s", QPointF(0, 0))
        self.assertAlmostEqual(item.boundingRect().height(), before, places=3)

    def test_text_background_padding_area_is_hit_target(self):
        """可见的背景内边距区域应能命中文字标注（否则点色块边缘选不中）。"""
        canvas = self._canvas()
        item = text_item(QPointF(20, 20), "背景文字", dict(
            DEFAULTS, text_background_enabled=True, text_background_padding=6,
            text_width=180), Qt.AlignLeft)
        canvas.scene_data.addItem(item)
        rect = item.boundingRect()
        point = item.mapToScene(QPointF(rect.left() + 2, rect.center().y()))
        self.assertIs(canvas.annotation_at(point), item)

    def test_parameter_dialog_without_change_adds_no_history(self):
        """参数对话框没有改动时，画布不应多记一个撤销点。"""
        from unittest.mock import patch

        canvas = self._canvas()
        rect = shape("rect", QPointF(20, 20), QPointF(90, 70), "#ff0000", 3)
        canvas.scene_data.addItem(rect)
        rect.setSelected(True)
        canvas.checkpoint()
        before = len(canvas.history)
        with patch("editor.annotation_properties.edit_annotation_parameters",
                   return_value=(rect, False)):
            canvas.edit_annotation_parameters(rect)
        self.assertEqual(len(canvas.history), before)

    def test_text_background_style_applies_to_selected_text(self):
        """文字背景开关/颜色应能作用到已选文字（预设里的文字背景色才真正生效）。"""
        canvas = self._canvas()
        item = text_item(QPointF(20, 20), "文字", dict(DEFAULTS, text_width=160), Qt.AlignLeft)
        canvas.scene_data.addItem(item)
        item.setSelected(True)
        canvas.set_selected_text_background_style(enabled=True)
        self.assertIsNotNone(item.background_color)
        canvas.set_selected_text_background_style(color="#000000")
        self.assertEqual(item.background_color, "#000000")
        canvas.set_selected_text_background_style(enabled=False)
        self.assertIsNone(item.background_color)


    def test_other_tool_color_and_width_do_not_touch_selected_rect(self):
        """画笔/取色参数只能改画笔类型，不能染到仍选中的矩形。"""
        canvas = self._canvas()
        rect = shape("rect", QPointF(20, 20), QPointF(120, 80), "#00ff00", 3)
        canvas.scene_data.addItem(rect)
        rect.setSelected(True)
        canvas.set_selected_color("#123456", "pen")
        canvas.set_selected_width(8, "pen")
        self.assertEqual(rect.pen().color().name(), "#00ff00")
        self.assertAlmostEqual(rect.pen().widthF(), 3.0, places=3)
        canvas.set_selected_color("#123456", "rect")
        canvas.set_selected_width(8, "rect")
        self.assertEqual(rect.pen().color().name(), "#123456")
        self.assertAlmostEqual(rect.pen().widthF(), 8.0, places=3)

    def test_editor_pen_color_does_not_recolor_selected_rect(self):
        """复现：选中矩形后切到画笔，设置画笔颜色不得改矩形。"""
        editor = EditorWindow(Image.new("RGB", (240, 160), "white"), dict(DEFAULTS))
        try:
            rect = shape("rect", QPointF(20, 20), QPointF(120, 80), "#00ff00", 3)
            editor.canvas.scene_data.addItem(rect)
            rect.setSelected(True)
            editor.set_tool("pen")
            editor.set_annotation_setting("pen_color", "#123456")
            self.assertEqual(rect.pen().color().name(), "#00ff00")
            self.assertAlmostEqual(rect.pen().widthF(), 3.0, places=3)
        finally:
            editor.close()

    def test_pen_preset_does_not_redraw_selected_rect(self):
        """套用画笔预设时，仍选中的矩形不能被画笔预设颜色/线宽覆盖。"""
        from editor.annotation_presets import presets_for

        editor = EditorWindow(Image.new("RGB", (240, 160), "white"), dict(DEFAULTS))
        try:
            rect = shape("rect", QPointF(20, 20), QPointF(120, 80), "#00ff00", 3)
            editor.canvas.scene_data.addItem(rect)
            rect.setSelected(True)
            editor.set_tool("pen")
            editor.toolbar.set_tool_mode("pen")
            target = presets_for("pen")[0]
            editor.toolbar.preset_combo.setCurrentIndex(
                editor.toolbar.preset_combo.findData(target[0]))
            self.app.processEvents()
            self.assertEqual(rect.pen().color().name(), "#00ff00")
            self.assertAlmostEqual(rect.pen().widthF(), 3.0, places=3)
        finally:
            editor.close()

    def test_picker_color_marks_current_tool_preset_custom(self):
        """取色改掉当前工具颜色后，预设下拉必须回到「自定义」。"""
        from editor.annotation_presets import presets_for

        editor = EditorWindow(Image.new("RGB", (240, 160), "white"), dict(DEFAULTS))
        try:
            editor.set_tool("pen")
            editor.toolbar.set_tool_mode("pen")
            target = presets_for("pen")[0]
            editor.toolbar.preset_combo.setCurrentIndex(
                editor.toolbar.preset_combo.findData(target[0]))
            self.assertEqual(editor.toolbar.preset_combo.currentData(), target[0])
            editor.set_pen_color("#123456")
            self.assertEqual(editor.toolbar.preset_combo.currentData(), "custom")
        finally:
            editor.close()

    def test_inline_editor_pen_color_does_not_recolor_rect(self):
        """原地编辑器同样不能把画笔颜色写到选中的矩形上（规则 7）。"""
        from unittest.mock import patch
        from PySide6.QtCore import QRect
        from screenshot.mask_window import MaskWindow
        from tests.base import ConfigManager, Path, tempfile

        with tempfile.TemporaryDirectory() as folder:
            bounds = {"left": 0, "top": 0, "width": 160, "height": 100}
            settings = {**ConfigManager(Path(folder) / "settings.json").data,
                        "save_dir": folder, "filename": "inline-color",
                        "inline_edit": True, "crosshair": False, "magnifier": False,
                        "capture_after_selection": "edit", "mask_opacity": 0,
                        "bubble": False}
            with patch("screenshot.mask_window.visible_windows", return_value=[]):
                mask = MaskWindow(Image.new("RGB", (160, 100), "#d02020"),
                                  bounds, [bounds], settings)
            try:
                mask.selection.rects.append(QRect(20, 20, 100, 60))
                mask.show()
                self.app.processEvents()
                mask.complete()
                self.app.processEvents()
                editor = mask.session.inline_editor
                rect = shape("rect", QPointF(10, 10), QPointF(60, 40), "#00ff00", 3)
                editor.canvas.scene_data.addItem(rect)
                rect.setSelected(True)
                editor.set_tool("pen")
                editor.set_annotation_setting("pen_color", "#123456")
                self.assertEqual(rect.pen().color().name(), "#00ff00")
                # 原地编辑器的预设套用同样不能碰选中的矩形。
                from editor.annotation_presets import presets_for
                editor.toolbar.set_tool_mode("pen")
                target = presets_for("pen")[0]
                editor.toolbar.preset_combo.setCurrentIndex(
                    editor.toolbar.preset_combo.findData(target[0]))
                self.app.processEvents()
                self.assertEqual(rect.pen().color().name(), "#00ff00")
                # 原地编辑器改矩形颜色也必须只改对象，不改默认配置。
                editor.set_tool("rect")
                editor.set_annotation_setting("rect_color", "#123456")
                self.assertEqual(rect.pen().color().name(), "#123456")
                self.assertNotEqual(editor.settings["rect_color"], "#123456")
            finally:
                mask.close()


    def test_switching_to_pen_and_opening_options_does_not_apply_preset(self):
        """只切换工具并打开「更多设置」不能把画笔预设应用到仍选中的矩形。"""
        editor = EditorWindow(Image.new("RGB", (240, 160), "white"), dict(DEFAULTS))
        try:
            rect = shape("rect", QPointF(20, 20), QPointF(120, 80), "#00ff00", 3)
            editor.canvas.scene_data.addItem(rect)
            rect.setSelected(True)
            editor.set_tool("pen")
            editor.toolbar.set_tool_mode("pen")
            editor.toolbar._populate_annotation_presets("pen")
            self.app.processEvents()
            self.assertEqual(rect.pen().color().name(), "#00ff00")
            self.assertAlmostEqual(rect.pen().widthF(), 3.0, places=3)
        finally:
            editor.close()


    def test_selected_rect_style_edit_does_not_write_defaults(self):
        """选中矩形后改颜色/线宽/填充只改对象，不动默认配置。"""
        editor = EditorWindow(Image.new("RGB", (240, 160), "white"), dict(DEFAULTS))
        try:
            rect = shape("rect", QPointF(20, 20), QPointF(120, 80), "#00ff00", 3)
            editor.canvas.scene_data.addItem(rect)
            rect.setSelected(True)
            defaults = dict(editor.settings)
            editor.set_annotation_setting("rect_color", "#123456")
            editor.set_annotation_setting("rect_width", 7)
            editor.set_annotation_setting("rect_fill_enabled", True)
            self.assertEqual(rect.pen().color().name(), "#123456")
            self.assertAlmostEqual(rect.pen().widthF(), 7.0, places=3)
            self.assertNotEqual(rect.brush().style(), Qt.NoBrush)
            for key in ("rect_color", "rect_width", "rect_fill_enabled",
                        "rect_fill_color", "rect_fill_opacity"):
                self.assertEqual(editor.settings[key], defaults[key], key)
        finally:
            editor.close()

    def test_rect_preset_on_selected_rect_keeps_defaults(self):
        """选中矩形套用矩形预设：对象变，默认配置与设置页不跟着变。"""
        from editor.annotation_presets import presets_for

        editor = EditorWindow(Image.new("RGB", (240, 160), "white"), dict(DEFAULTS))
        try:
            rect = shape("rect", QPointF(20, 20), QPointF(120, 80), "#00ff00", 3)
            editor.canvas.scene_data.addItem(rect)
            rect.setSelected(True)
            defaults = dict(editor.settings)
            editor.toolbar.set_tool_mode("rect")
            target = next(item for item in presets_for("rect")
                          if item[2].get("rect_fill_enabled"))
            editor.toolbar.preset_combo.setCurrentIndex(
                editor.toolbar.preset_combo.findData(target[0]))
            self.app.processEvents()
            current = editor.canvas.scene_data.selectedItems()[0]
            self.assertEqual(current.pen().color().name(), target[2]["rect_color"])
            self.assertNotEqual(current.brush().style(), Qt.NoBrush)
            for key, value in target[2].items():
                self.assertEqual(editor.settings[key], defaults[key], key)
        finally:
            editor.close()

    def test_duplicate_preserves_object_style_without_defaults(self):
        """复制保留对象自己的颜色/线宽/填充，不改默认配置。"""
        editor = EditorWindow(Image.new("RGB", (240, 160), "white"), dict(DEFAULTS))
        try:
            rect = shape("rect", QPointF(20, 20), QPointF(120, 80), "#00ff00", 4,
                         fill_enabled=True, fill_opacity=55, fill_color="#00ff00")
            editor.canvas.scene_data.addItem(rect)
            rect.setSelected(True)
            defaults = dict(editor.settings)
            editor.canvas.checkpoint()
            editor.canvas.duplicate_selected()
            copy = editor.canvas.scene_data.selectedItems()[0]
            self.assertIsNot(copy, rect)
            self.assertEqual(copy.pen().color().name(), "#00ff00")
            self.assertAlmostEqual(copy.pen().widthF(), 4.0, places=3)
            self.assertNotEqual(copy.brush().style(), Qt.NoBrush)
            self.assertEqual(copy.brush().color().name(), "#00ff00")
            for key in ("rect_color", "rect_width", "rect_fill_color", "rect_fill_opacity"):
                self.assertEqual(editor.settings[key], defaults[key], key)
        finally:
            editor.close()


    def test_text_object_edits_do_not_write_defaults(self):
        """选中文字后改颜色/字号/加粗/背景只改对象，不写默认配置。"""
        editor = EditorWindow(Image.new("RGB", (240, 160), "white"), dict(DEFAULTS))
        try:
            item = text_item(QPointF(20, 20), "对象文字", dict(DEFAULTS, text_width=160),
                             Qt.AlignLeft)
            editor.canvas.scene_data.addItem(item)
            item.setSelected(True)
            defaults = dict(editor.settings)
            editor.set_annotation_setting("text_color", "#123456")
            editor.set_annotation_setting("font_size", 30)
            editor.set_annotation_setting("text_bold", True)
            editor.set_annotation_setting("text_background_enabled", True)
            self.assertEqual(item.defaultTextColor().name(), "#123456")
            self.assertEqual(item.font().pointSize(), 30)
            from editor.annotation_items import read_text_format
            self.assertTrue(read_text_format(item)[0])
            self.assertIsNotNone(item.background_color)
            for key in ("text_color", "font_size", "text_bold", "text_background_enabled"):
                self.assertEqual(editor.settings[key], defaults[key], key)
        finally:
            editor.close()

    def test_arrow_object_edits_do_not_write_defaults(self):
        """选中箭头后改颜色/线宽/样式只改对象，不写默认配置。"""
        editor = EditorWindow(Image.new("RGB", (240, 160), "white"), dict(DEFAULTS))
        try:
            arrow = shape("arrow", QPointF(20, 20), QPointF(160, 80), "#00ff00", 3, "filled")
            editor.canvas.scene_data.addItem(arrow)
            arrow.setSelected(True)
            defaults = dict(editor.settings)
            editor.set_annotation_setting("arrow_color", "#123456")
            editor.set_annotation_setting("arrow_width", 6)
            editor.set_annotation_setting("arrow_style", "double_filled")
            self.assertEqual(arrow.pen().color().name(), "#123456")
            self.assertAlmostEqual(arrow.pen().widthF(), 6.0, places=3)
            self.assertEqual(arrow.arrow_style, "double_filled")
            for key in ("arrow_color", "arrow_width", "arrow_style"):
                self.assertEqual(editor.settings[key], defaults[key], key)
        finally:
            editor.close()

    def test_marker_object_edits_do_not_write_defaults(self):
        """选中记号笔后改颜色/透明度只改对象，不写默认配置；100% 不透明仍保持记号笔身份。"""
        from PySide6.QtGui import QColor, QPainterPath, QPen
        from editor.annotation_items import AnnotationPathItem, editable

        editor = EditorWindow(Image.new("RGB", (240, 160), "white"), dict(DEFAULTS))
        try:
            path = QPainterPath(QPointF(20, 20))
            path.lineTo(QPointF(120, 80))
            item = AnnotationPathItem(path)
            pen = QPen(QColor(255, 0, 0, 255), 6)
            item.setPen(pen)
            item.marker = True
            editable(item)
            editor.canvas.scene_data.addItem(item)
            item.setSelected(True)
            defaults = dict(editor.settings)
            editor.set_annotation_setting("marker_color", "#123456")
            editor.set_annotation_setting("marker_opacity", 80)
            self.assertEqual(item.pen().color().name(), "#123456")
            self.assertEqual(item.pen().color().alpha(), round(255 * 80 / 100))
            self.assertEqual(editor.settings["marker_color"], defaults["marker_color"])
            self.assertEqual(editor.settings["marker_opacity"], defaults["marker_opacity"])
        finally:
            editor.close()


    def test_sequence_object_edits_do_not_write_defaults(self):
        """选中序号后改形状/颜色/字号只改对象，不写默认配置。"""
        from editor.annotation_items import AnnotationSequenceItem

        editor = EditorWindow(Image.new("RGB", (240, 160), "white"), dict(DEFAULTS))
        try:
            item = AnnotationSequenceItem(3, "#ff0000", "#ffffff", 14, "circle")
            editor.canvas.scene_data.addItem(item)
            item.setSelected(True)
            defaults = dict(editor.settings)
            editor.set_annotation_setting("sequence_fill_color", "#123456")
            editor.set_annotation_setting("sequence_text_color", "#000000")
            editor.set_annotation_setting("sequence_font_size", 22)
            editor.set_annotation_setting("sequence_shape", "star")
            self.assertEqual(item.sequence_fill_color, "#123456")
            self.assertEqual(item.sequence_text_color, "#000000")
            self.assertEqual(item.sequence_font_size, 22)
            self.assertEqual(item.sequence_shape, "star")
            for key in ("sequence_fill_color", "sequence_text_color",
                        "sequence_font_size", "sequence_shape"):
                self.assertEqual(editor.settings[key], defaults[key], key)
        finally:
            editor.close()


    def test_arrow_curve_direction_object_local_and_shortcut(self):
        """选中曲线箭头改弯向只改对象；Alt+C 快捷键同样不应改默认配置。"""
        editor = EditorWindow(Image.new("RGB", (320, 220), "white"), dict(DEFAULTS))
        try:
            arrow = shape("arrow", QPointF(20, 60), QPointF(220, 60),
                          "#ff0000", 4, "curve_filled")
            editor.canvas.scene_data.addItem(arrow)
            arrow.setSelected(True)
            defaults = dict(editor.settings)
            editor.set_annotation_setting("arrow_curve_direction", "left")
            self.assertEqual(arrow.arrow_curve_direction, "left")
            self.assertEqual(editor.settings["arrow_curve_direction"], defaults["arrow_curve_direction"])
            editor.set_tool("arrow")
            editor.canvas.setFocus()
            QTest.keyClick(editor.canvas, Qt.Key_C, Qt.AltModifier)
            self.app.processEvents()
            self.assertEqual(arrow.arrow_curve_direction, "right")
            self.assertEqual(editor.settings["arrow_curve_direction"], defaults["arrow_curve_direction"])
        finally:
            editor.close()


    def test_arrow_properties_dialog_can_change_curve_direction(self):
        """对象级「编辑参数」也能改曲线箭头弯向，且只改当前箭头。"""
        arrow = shape("arrow", QPointF(20, 40), QPointF(200, 40), "#ff0000", 4,
                      "curve_filled", arrow_curve_direction="left")
        dialog = AnnotationPropertiesDialog(None, arrow)
        try:
            self.assertEqual(dialog.controls["arrow_curve_direction"].currentData(), "left")
            dialog.controls["arrow_curve_direction"].setCurrentIndex(
                dialog.controls["arrow_curve_direction"].findData("right"))
            dialog.apply()
            self.assertEqual(arrow.arrow_curve_direction, "right")
        finally:
            dialog.deleteLater()


    def test_zoom_annotation_object_settings_and_source_marker(self):
        """放大标注的倍率/镜片/透明度只改对象，源区域框存在且快照保留。"""
        from PySide6.QtGui import QColor, QPixmap
        from PySide6.QtCore import QRectF
        from editor.annotation_items import AnnotationZoomItem, editable

        canvas = AnnotationCanvas(Image.new("RGB", (400, 300), "white"), dict(DEFAULTS))
        try:
            source = QPixmap(60, 40)
            source.fill(QColor("#88ccff"))
            item = editable_zoom = editable(AnnotationZoomItem(
                source, QRectF(20, 20, 60, 40), 2, "circle", "#00ad91", 2, 80))
            editable_zoom.setPos(QPointF(220, 40))
            canvas.scene_data.addItem(editable_zoom)
            canvas.checkpoint()
            editable_zoom.setSelected(True)
            defaults = dict(canvas.settings)
            self.assertIsNotNone(editable_zoom._source_marker())
            canvas.settings["zoom_annotation_color"] = "#123456"
            canvas.set_tool("zoom")
            self.assertEqual(canvas._current_cursor_color(), "#123456")
            canvas.set_selected_zoom_settings(zoom_annotation_factor=3,
                                              zoom_annotation_style="rect",
                                              zoom_annotation_color="#123456",
                                              zoom_annotation_opacity=50)
            self.assertEqual(editable_zoom.zoom_factor, 3)
            self.assertEqual(editable_zoom.pixmap().width(), 180)
            self.assertEqual(editable_zoom.zoom_style, "rect")
            self.assertEqual(editable_zoom.zoom_border_color, "#123456")
            self.assertAlmostEqual(editable_zoom.opacity(), 0.5, places=3)
            # 直接经画布对象级 setter 不应改默认配置；默认值由编辑器入口控制。
            self.assertEqual(canvas.settings["zoom_annotation_factor"],
                             defaults["zoom_annotation_factor"])
            canvas.undo()
            restored = [entry for entry in canvas.annotations()
                        if getattr(entry, "zoom_factor", None) is not None][0]
            self.assertEqual(restored.zoom_factor, 2)
            self.assertEqual(restored.zoom_style, "circle")
            self.assertAlmostEqual(restored.opacity(), 0.8, places=3)
        finally:
            canvas.close()

    def test_zoom_properties_dialog_edits_factor_style_opacity(self):
        """对象参数对话框可改放大倍率、镜片形状和透明度。"""
        from PySide6.QtGui import QColor, QPixmap
        from PySide6.QtCore import QRectF
        from editor.annotation_items import AnnotationZoomItem

        source = QPixmap(40, 30)
        source.fill(QColor("#88ccff"))
        item = AnnotationZoomItem(source, QRectF(10, 10, 40, 30), 2, "circle",
                                  "#00ad91", 2, 100)
        dialog = AnnotationPropertiesDialog(None, item)
        try:
            dialog.controls["zoom_factor"].setValue(3)
            dialog.controls["zoom_style"].setCurrentIndex(
                dialog.controls["zoom_style"].findData("rect"))
            dialog.controls["zoom_max_size"].setValue(240)
            dialog.controls["zoom_color"].set_color("#abcdef")
            dialog.controls["opacity"].setValue(45)
            dialog.apply()
            self.assertEqual(item.zoom_factor, 3)
            self.assertEqual(item.zoom_style, "rect")
            self.assertEqual(item.zoom_border_color, "#abcdef")
            self.assertEqual(item.zoom_max_size, 240)
            self.assertAlmostEqual(item.opacity(), 0.45, places=3)
        finally:
            dialog.deleteLater()



    def test_zoom_tool_alt_creates_square_source(self):
        """放大工具按 Ctrl/Alt 时源选区应为正方形，圆形镜片才能得到真正的圆。"""
        from PySide6.QtCore import QPoint

        canvas = self._canvas(zoom_annotation_style="circle")
        try:
            canvas.show()
            self.app.processEvents()
            canvas.set_tool("zoom")
            start = QPoint(40, 40)
            end = QPoint(150, 120)
            QTest.mousePress(canvas.viewport(), Qt.LeftButton, Qt.AltModifier, start)
            QTest.mouseMove(canvas.viewport(), end)
            QTest.mouseRelease(canvas.viewport(), Qt.LeftButton, Qt.AltModifier, end)
            zooms = [item for item in canvas.annotations()
                     if getattr(item, "zoom_factor", None) is not None]
            self.assertEqual(len(zooms), 1)
            rect = zooms[0].source_rect
            self.assertAlmostEqual(rect.width(), rect.height(), delta=1.0)
        finally:
            canvas.close()

    def test_zoom_annotation_max_size_caps_large_source(self):
        """大区域/高倍率时按最大边长等比缩小，不能无限增大。"""
        from PySide6.QtGui import QColor, QPixmap
        from PySide6.QtCore import QRectF
        from editor.annotation_items import AnnotationZoomItem

        source = QPixmap(300, 200)
        source.fill(QColor("#88ccff"))
        item = AnnotationZoomItem(source, QRectF(0, 0, 300, 200), 3,
                                  "circle", "#00ad91", 2, 100, 480)
        item.setPos(QPointF(400, 80))
        self.assertEqual((item.pixmap().width(), item.pixmap().height()), (480, 320))
        self.assertLess(item.zoom_display_scale, 1.0)
        item.set_zoom_max_size(240)
        self.assertEqual((item.pixmap().width(), item.pixmap().height()), (240, 160))
        item.set_zoom_max_size(480)
        item.set_zoom_factor(4)
        item.set_zoom_factor(20)
        self.assertEqual(item.zoom_factor, 16)
        self.assertEqual((item.pixmap().width(), item.pixmap().height()), (480, 320))
        self.assertIsNotNone(item._source_marker())


    def test_replace_image_preserves_position_and_undoes(self):
        """替换图片只换内容，保留位置，并可撤销。"""
        from unittest.mock import patch
        from tests.base import tempfile, Path
        from PySide6.QtWidgets import QGraphicsPixmapItem

        with tempfile.TemporaryDirectory() as folder:
            first = Path(folder) / "first.png"
            second = Path(folder) / "second.png"
            Image.new("RGB", (40, 30), "#ff0000").save(first)
            Image.new("RGB", (20, 60), "#00ff00").save(second)
            editor = EditorWindow(Image.new("RGB", (320, 220), "white"), dict(DEFAULTS))
            try:
                item = editor.canvas.add_pixmap_file(first)
                self.assertIsNotNone(item)
                item.setSelected(True)
                position = QPointF(item.pos())
                self.assertEqual(item.image_source_path, str(first))
                with patch("editor.annotation_canvas.QFileDialog.getOpenFileName",
                           return_value=(str(second), "")):
                    replaced = editor.canvas.replace_image_item(item)
                self.assertIs(replaced, item)
                self.assertEqual(item.pos(), position)
                self.assertEqual(item.image_source_path, str(second))
                self.assertEqual(item.pixmap().height(), 30)
                self.assertEqual(item.pixmap().toImage().pixelColor(0, 0).name(), "#00ff00")
                editor.canvas.undo()
                restored = [entry for entry in editor.canvas.annotations()
                            if isinstance(entry, QGraphicsPixmapItem)][0]
                self.assertEqual(restored.image_source_path, str(first))
                self.assertEqual(restored.pixmap().toImage().pixelColor(0, 0).name(), "#ff0000")
            finally:
                editor.close()

    def test_image_properties_dialog_has_replace_button_and_current_position(self):
        """图片编辑对话框包含当前位置/大小、当前图片与替换按钮。"""
        from unittest.mock import patch
        from tests.base import tempfile, Path
        from PySide6.QtWidgets import QPushButton

        with tempfile.TemporaryDirectory() as folder:
            first = Path(folder) / "first.png"
            second = Path(folder) / "second.png"
            Image.new("RGB", (40, 30), "#ff0000").save(first)
            Image.new("RGB", (20, 60), "#00ff00").save(second)
            editor = EditorWindow(Image.new("RGB", (320, 220), "white"), dict(DEFAULTS))
            try:
                item = editor.canvas.add_pixmap_file(first)
                item.setSelected(True)
                position = QPointF(item.pos())
                dialog = AnnotationPropertiesDialog(None, item)
                try:
                    replace = next(button for button in dialog.findChildren(QPushButton)
                                   if button.text() == "替换图片…")
                    with patch("editor.annotation_properties.QFileDialog.getOpenFileName",
                               return_value=(str(second), "")):
                        replace.click()
                    self.assertIsNotNone(dialog._replacement_pixmap)
                    dialog.apply()
                    self.assertEqual(item.pos(), position)
                    self.assertEqual(item.image_source_path, str(second))
                    self.assertEqual(item.pixmap().toImage().pixelColor(0, 0).name(), "#00ff00")
                finally:
                    dialog.deleteLater()
            finally:
                editor.close()


if __name__ == "__main__":
    unittest.main()
