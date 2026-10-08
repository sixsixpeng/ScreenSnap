"""文字标注：输入、编辑、格式与文本框尺寸。"""

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


class TextTests(CoreTests):
    def test_editing_text_only_affects_that_annotation(self):
        from config.config_manager import DEFAULTS
        from editor.annotation_items import text_item, read_text_format
        from editor import text_input_dialog
        from PySide6.QtWidgets import QDialog

        settings = dict(DEFAULTS, text_bold=False, font_size=18,
                        text_color="#ff0000", text_width=0)
        canvas = AnnotationCanvas(Image.new("RGB", (320, 200), "white"), settings)
        first = text_item(QPointF(10, 10), "第一", canvas.settings, Qt.AlignLeft)
        second = text_item(QPointF(10, 60), "第二", canvas.settings, Qt.AlignLeft)
        canvas._add_annotation(first)
        canvas._add_annotation(second)

        class StubDialog:
            def __init__(self, *args, **kwargs):
                pass

            def exec(self):
                return QDialog.Accepted

            def text(self):
                return "第二"          # 文字不变，只改样式

            def changed_settings(self):
                return {"font_size": 40, "text_bold": True, "text_color": "#00ff00"}

        original = text_input_dialog.TextInputDialog
        text_input_dialog.TextInputDialog = StubDialog
        try:
            canvas.edit_text_item(second)
        finally:
            text_input_dialog.TextInputDialog = original

        # 编辑对话框的各项只作用于被编辑的标注。
        self.assertEqual(second.font().pointSize(), 40)
        self.assertTrue(read_text_format(second)[0])
        self.assertEqual(second.defaultTextColor().name(), "#00ff00")
        self.assertEqual(first.font().pointSize(), settings["font_size"])
        self.assertFalse(read_text_format(first)[0])
        # 不回写公共配置（新建才用公共配置作默认）。
        self.assertEqual(canvas.settings["font_size"], 18)
        self.assertFalse(canvas.settings["text_bold"])
        self.assertEqual(canvas.settings["text_color"], "#ff0000")
        canvas.close()

    def test_text_input_dialog_shows_live_preview(self):
        from unittest.mock import patch as _patch
        from PySide6.QtWidgets import QDialogButtonBox
        from config.config_manager import DEFAULTS
        from editor.text_input_dialog import TextInputDialog

        settings = dict(DEFAULTS)
        dialog = TextInputDialog(None, "文字标注", settings, "abc")
        buttons = dialog.findChild(QDialogButtonBox)
        self.assertEqual(buttons.button(QDialogButtonBox.Ok).text(), "确定")
        self.assertEqual(buttons.button(QDialogButtonBox.Cancel).text(), "取消")
        self.assertIsNotNone(getattr(dialog, "preview", None))
        self.assertEqual(dialog.preview.kind, "text")
        # 预览使用编辑框里正在输入的文字，而不是固定示例文案。
        self.assertEqual(dialog.preview.sample_text, "abc")
        dialog.editor.setPlainText("实时输入的文字")
        self.assertEqual(dialog.preview.sample_text, "实时输入的文字")
        # 自动模式显示按内容实测的宽度；改为固定值后显示该值。
        self.assertIn("自动", dialog.width_hint.text())
        dialog.text_width.setValue(200)
        self.assertIn("200", dialog.width_hint.text())
        # 高度同样显示实测/固定值，避免「自动高度看不到实际值」。
        self.assertIn("自动", dialog.height_hint.text())
        self.assertIn("px", dialog.height_hint.text())
        dialog.text_height.setValue(120)
        self.assertIn("120", dialog.height_hint.text())
        dialog.text_height.setValue(0)
        self.assertIn("自动", dialog.height_hint.text())
        # 固定宽度下输入换行会让自动高度变大，提示随之刷新。
        dialog.text_width.setValue(120)
        dialog.editor.setPlainText("短")
        narrow_hint = dialog.height_hint.text()
        dialog.editor.setPlainText("这是一段较长的文字，在窄框里会折成很多行，从而明显变高")
        self.assertNotEqual(dialog.height_hint.text(), narrow_hint)
        with _patch.object(dialog.preview, "refresh", wraps=dialog.preview.refresh) as refresh:
            dialog.font_size.setValue(30)
            dialog.text_width.setValue(240)
            dialog.checks["text_bold"].setChecked(True)
        # 每次参数改动都刷新预览，保证插入前能看到实际效果。
        self.assertEqual(refresh.call_count, 3)
        dialog.accept()

    def test_editing_text_applies_width_only_change(self):
        from config.config_manager import DEFAULTS
        from editor.annotation_items import text_item
        from editor import text_input_dialog
        from PySide6.QtWidgets import QDialog

        canvas = AnnotationCanvas(Image.new("RGB", (320, 200), "white"),
                                  dict(DEFAULTS, text_width=0))
        item = text_item(QPointF(10, 10), "原文", canvas.settings, Qt.AlignLeft)
        canvas._add_annotation(item)
        before = item.document().textWidth()
        self.assertNotEqual(before, 260)

        class StubDialog:
            def __init__(self, *args, **kwargs):
                pass

            def exec(self):
                return QDialog.Accepted

            def text(self):
                return "原文"          # 文字未变，只调了宽度

            def changed_settings(self):
                return {"text_width": 260}

        original = text_input_dialog.TextInputDialog
        text_input_dialog.TextInputDialog = StubDialog
        try:
            canvas.edit_text_item(item)
        finally:
            text_input_dialog.TextInputDialog = original
        # 只改宽度的编辑也要作用到该标注上，且不回写公共配置（编辑只针对该标注）。
        self.assertEqual(item.document().textWidth(), 260)
        self.assertEqual(canvas.settings["text_width"], 0)
        canvas.close()

    def test_text_edge_handle_resizes_box_width_and_corner_still_scales(self):
        from config.config_manager import DEFAULTS
        from editor.annotation_items import text_item
        from PySide6.QtCore import QEvent
        from PySide6.QtGui import QMouseEvent

        canvas = AnnotationCanvas(Image.new("RGB", (400, 300), "white"),
                                  dict(DEFAULTS, text_width=0))
        canvas.resize(520, 400)
        canvas.show()
        canvas.set_tool("select")
        item = text_item(QPointF(20, 20), "较长文字内容示例", canvas.settings, Qt.AlignLeft)
        canvas._add_annotation(item)
        item.setSelected(True)
        canvas.checkpoint()

        def drag(handle_point, offset):
            start = canvas.mapFromScene(handle_point)
            finish = start + offset

            def send(kind, pos, buttons):
                self.app.sendEvent(canvas.viewport(), QMouseEvent(
                    kind, QPointF(pos), QPointF(canvas.viewport().mapToGlobal(pos)),
                    Qt.LeftButton, buttons, Qt.NoModifier))

            send(QEvent.MouseButtonPress, start, Qt.LeftButton)
            send(QEvent.MouseMove, finish, Qt.LeftButton)
            send(QEvent.MouseButtonRelease, finish, Qt.NoButton)

        # 边中点（右边）→ 直接调整文本框宽度，不缩放整个标注。
        rect = item.sceneBoundingRect()
        before_width = item.document().textWidth()
        before_scale = item.scale()
        drag(QPointF(rect.right(), rect.center().y()), QPoint(40, 0))
        self.assertGreater(item.document().textWidth(), before_width + 20)
        self.assertAlmostEqual(item.scale(), before_scale, places=5)

        # 四角（右下）→ 仍是缩放整个标注，文本框宽度不变。
        canvas.undo()
        live = canvas.annotations()[0]
        live.setSelected(True)
        width_after = live.document().textWidth()
        corner = live.sceneBoundingRect()
        drag(QPointF(corner.right(), corner.bottom()), QPoint(24, 24))
        self.assertAlmostEqual(live.document().textWidth(), width_after, delta=2)
        # 缩放写进 transform（而非 setScale），故用矩阵判断四角仍是缩放。
        self.assertGreater(live.transform().m11(), 1.0)

        # 上下边中点 → 调整文本框高度（宽度、缩放都不变），同类型标注才走此路径。
        canvas.undo()
        live = canvas.annotations()[0]
        live.setSelected(True)
        scale = live.transform().m11()
        height_before = live.boundingRect().height()
        width_before = live.document().textWidth()
        box = live.sceneBoundingRect()
        drag(QPointF(box.center().x(), box.bottom()), QPoint(0, 30))
        self.assertAlmostEqual(live.boundingRect().height(),
                               height_before + 30 / scale, delta=2)
        self.assertEqual(live.fixed_height, live.boundingRect().height())
        self.assertAlmostEqual(live.document().textWidth(), width_before, delta=0.5)
        # 拖上边（n）时下边缘保持不动。
        bottom_before = live.sceneBoundingRect().bottom()
        height_after_s = live.boundingRect().height()
        top = live.sceneBoundingRect()
        drag(QPointF(top.center().x(), top.top()), QPoint(0, -20))
        self.assertAlmostEqual(live.boundingRect().height(),
                               height_after_s + 20 / scale, delta=2)
        self.assertAlmostEqual(live.sceneBoundingRect().bottom(), bottom_before, delta=2)
        canvas.close()

    def test_text_box_drag_resizes_smoothly_without_jumping(self):
        """文字框边中点拖动：宽度 = 按下宽度 + 累计位移，逐帧稳定，不出现跳跃放大。"""
        from config.config_manager import DEFAULTS
        from editor.annotation_items import text_item
        from PySide6.QtCore import QEvent
        from PySide6.QtGui import QMouseEvent

        canvas = AnnotationCanvas(Image.new("RGB", (400, 300), "white"),
                                  dict(DEFAULTS, text_width=200))
        canvas.resize(520, 400)
        canvas.show()
        canvas.set_tool("select")
        # 放在画布中部，避免拖拽越界被边界钳制（那属于预期行为，不是跳跃）。
        item = text_item(QPointF(150, 20), "多行文字示例内容", canvas.settings, Qt.AlignLeft)
        canvas._add_annotation(item)
        item.setSelected(True)
        canvas.checkpoint()
        origin_width = item.document().textWidth()
        self.assertEqual(origin_width, 200)

        rect = item.sceneBoundingRect()
        start = canvas.mapFromScene(QPointF(rect.right(), rect.center().y()))

        def send(kind, pos, buttons):
            self.app.sendEvent(canvas.viewport(), QMouseEvent(
                kind, QPointF(pos), QPointF(canvas.viewport().mapToGlobal(pos)),
                Qt.LeftButton, buttons, Qt.NoModifier))

        send(QEvent.MouseButtonPress, start, Qt.LeftButton)
        widths = []
        for offset in (10, 20, 30):
            send(QEvent.MouseMove, start + QPoint(offset, 0), Qt.LeftButton)
            widths.append(item.document().textWidth())
        # 每步都等于「按下宽度 + 累计位移」，不再叠加放大。
        for offset, width in zip((10, 20, 30), widths):
            self.assertAlmostEqual(width, origin_width + offset, delta=2.0)
        # 拖回起点即恢复原宽度，无累积漂移。
        send(QEvent.MouseMove, start, Qt.LeftButton)
        self.assertAlmostEqual(item.document().textWidth(), origin_width, delta=1.0)
        send(QEvent.MouseButtonRelease, start, Qt.NoButton)

        # 拖左边（w）时宽度增加且右边缘保持不动。
        left = canvas.mapFromScene(QPointF(item.sceneBoundingRect().left(),
                                           item.sceneBoundingRect().center().y()))
        right_before = item.sceneBoundingRect().right()
        send(QEvent.MouseButtonPress, left, Qt.LeftButton)
        send(QEvent.MouseMove, left - QPoint(40, 0), Qt.LeftButton)
        send(QEvent.MouseButtonRelease, left - QPoint(40, 0), Qt.NoButton)
        self.assertAlmostEqual(item.document().textWidth(), origin_width + 40, delta=2.0)
        self.assertAlmostEqual(item.sceneBoundingRect().right(), right_before, delta=2.0)

        # 已缩放的标注：场景位移按自身缩放换算，避免与光标脱节而跳跃。
        canvas.undo()
        live = canvas.annotations()[0]
        live.document().setTextWidth(60)
        live.setScale(2.0)
        live.setSelected(True)
        rect = live.sceneBoundingRect()
        start = canvas.mapFromScene(QPointF(rect.right(), rect.center().y()))
        send(QEvent.MouseButtonPress, start, Qt.LeftButton)
        send(QEvent.MouseMove, start + QPoint(40, 0), Qt.LeftButton)
        send(QEvent.MouseButtonRelease, start + QPoint(40, 0), Qt.NoButton)
        # 2 倍缩放下，40 px 场景位移对应 20 px 文本框宽度。
        self.assertAlmostEqual(live.document().textWidth(), 80, delta=2.0)
        canvas.close()

    def test_text_item_uses_shared_annotation_color(self):
        from config.config_manager import DEFAULTS
        from editor.annotation_items import text_item

        settings = dict(DEFAULTS, pen_color="#123456", text_color="#abcdef")
        item = text_item(QPointF(0, 0), "hello", settings, Qt.AlignLeft)
        self.assertEqual(item.defaultTextColor().name(), "#abcdef")

    def test_text_antialiasing_enabled_for_preview_and_export(self):
        from config.config_manager import DEFAULTS
        from PySide6.QtGui import QPainter

        canvas = AnnotationCanvas(Image.new("RGB", (120, 80), "white"), dict(DEFAULTS))
        self.assertTrue(canvas.renderHints() & QPainter.TextAntialiasing)
        render = canvas.scene_data.render
        with patch.object(canvas.scene_data, "render",
                          side_effect=lambda painter, *args: (
                              self.assertTrue(painter.testRenderHint(QPainter.TextAntialiasing)),
                              render(painter, *args))):
            canvas.render_image()
        canvas.close()

    def test_text_line_spacing_and_undo(self):
        from PySide6.QtCore import Qt
        from config.config_manager import DEFAULTS
        from editor.annotation_items import text_item
        canvas = AnnotationCanvas(Image.new("RGB", (120, 100), "white"), DEFAULTS)
        item = text_item(QPointF(0, 0), "第一行\n第二行", DEFAULTS, Qt.AlignRight)
        canvas.scene_data.addItem(item)
        canvas.checkpoint()
        canvas.undo()
        canvas.redo()
        restored = canvas.annotations()[0]
        self.assertEqual(restored.toPlainText(), "第一行\n第二行")
        self.assertTrue(restored.document().firstBlock().blockFormat().alignment() & Qt.AlignRight)

    def test_text_input_dialog_prefills_and_reports_changes(self):
        from config.config_manager import DEFAULTS
        from editor.text_input_dialog import TextInputDialog

        settings = dict(DEFAULTS)
        dialog = TextInputDialog(None, "文字标注", settings, "abc")
        # 默认套用当前配置，未改动时不产生回写。
        self.assertEqual(dialog.text(), "abc")
        self.assertEqual(dialog.font_size.value(), settings["font_size"])
        self.assertEqual(dialog.checks["text_bold"].isChecked(), settings["text_bold"])
        self.assertEqual(dialog.text_width.value(), settings["text_width"])
        self.assertEqual(dialog.changed_settings(), {})
        dialog.checks["text_bold"].setChecked(True)
        dialog.font_size.setValue(30)
        dialog.text_width.setValue(280)
        dialog.alignment.setCurrentIndex(dialog.alignment.findData("center"))
        changed = dialog.changed_settings()
        self.assertTrue(changed["text_bold"])
        self.assertEqual(changed["font_size"], 30)
        self.assertEqual(changed["text_width"], 280)
        self.assertEqual(changed["text_alignment"], "center")
        dialog.accept()

    def test_text_preview_fits_visible_area(self):
        from config.config_manager import DEFAULTS
        from ui.widgets.annotation_preview import AnnotationPreview
        from PySide6.QtCore import QRectF
        from PySide6.QtWidgets import QGraphicsPixmapItem

        class Config:
            def __init__(self, data):
                self.data = data

        for size in (18, 60, 200):
            settings = dict(DEFAULTS)
            settings["font_size"] = size
            widget = AnnotationPreview(Config(settings), "text", 120)
            widget.resize(320, 120)
            scene = widget.build(QRectF(widget.rect()))
            text = [item for item in scene.items()
                    if not isinstance(item, QGraphicsPixmapItem)][0]
            area = QRectF(widget.rect())
            self.assertTrue(area.contains(text.sceneBoundingRect()), size)

    def test_text_background_applies_and_round_trips(self):
        from config.config_manager import DEFAULTS
        from editor.annotation_items import text_item
        from editor.annotation_canvas import AnnotationCanvas
        from PIL import Image

        settings = dict(DEFAULTS)
        settings["text_background_enabled"] = True
        settings["text_background"] = "#112233"
        item = text_item(QPointF(0, 0), "你好", settings, Qt.AlignLeft)
        self.assertEqual(item.background_color, "#112233")
        # 关闭背景时不设置
        plain = text_item(QPointF(0, 0), "你好", dict(DEFAULTS), Qt.AlignLeft)
        self.assertIsNone(plain.background_color)
        # 序列化与还原均保留背景
        canvas = AnnotationCanvas(Image.new("RGB", (120, 100), "white"), settings)
        canvas.scene_data.addItem(item)
        records = canvas.snapshot()
        record = next(r for r in records if r["type"] == "QGraphicsTextItem")
        self.assertEqual(record["background"], "#112233")
        canvas2 = AnnotationCanvas(Image.new("RGB", (120, 100), "white"), settings)
        canvas2.restore(records)
        self.assertEqual(canvas2.annotations()[0].background_color, "#112233")
        canvas.close()
        canvas2.close()

    def test_text_format_flags_apply_and_round_trip(self):
        from config.config_manager import DEFAULTS
        from editor.annotation_items import text_item, read_text_format
        from editor.annotation_canvas import AnnotationCanvas
        from PIL import Image

        settings = dict(DEFAULTS)
        settings.update(text_bold=True, text_italic=True, text_underline=True, text_strikethrough=True)
        item = text_item(QPointF(0, 0), "你好", settings, Qt.AlignLeft)
        bold, italic, underline, strike = read_text_format(item)
        self.assertTrue(bold)
        self.assertTrue(italic)
        self.assertTrue(underline)
        self.assertTrue(strike)

        plain = text_item(QPointF(0, 0), "你好", dict(DEFAULTS), Qt.AlignLeft)
        self.assertFalse(read_text_format(plain)[0])

        canvas = AnnotationCanvas(Image.new("RGB", (120, 100), "white"), settings)
        canvas.scene_data.addItem(item)
        canvas.checkpoint()
        records = canvas.snapshot()  # 加粗状态
        rec = next(r for r in records if r["type"] == "QGraphicsTextItem")
        self.assertTrue(rec["bold"])
        item.setSelected(True)
        canvas.settings = dict(DEFAULTS)  # 全部关闭
        canvas.set_selected_text_format()
        self.assertFalse(read_text_format(item)[0])  # 实时更新为无粗体
        canvas2 = AnnotationCanvas(Image.new("RGB", (120, 100), "white"), settings)
        canvas2.restore(records)
        restored = read_text_format(canvas2.annotations()[0])
        self.assertTrue(restored[0])
        self.assertTrue(restored[3])
        canvas.close()
        canvas2.close()

    def test_existing_text_context_menu_edits_and_undoes(self):
        from config.config_manager import DEFAULTS
        from editor.annotation_items import text_item
        editor = EditorWindow(Image.new("RGB", (300, 180), "white"), DEFAULTS)
        canvas = editor.canvas
        canvas.scene_data.addItem(text_item(QPointF(10, 10), "原文", DEFAULTS, Qt.AlignRight))
        canvas.checkpoint()
        editor.show()
        self.app.processEvents()
        from PySide6.QtWidgets import QDialog
        with patch("editor.text_input_dialog.TextInputDialog") as dialog:
            dialog.return_value.exec.return_value = QDialog.Accepted
            dialog.return_value.text.return_value = "修改后"
            dialog.return_value.changed_settings.return_value = {}
            canvas.annotation_menu(canvas.annotations()[0]).actions()[0].trigger()
        self.assertEqual(dialog.call_args.args[3], "原文")
        self.assertEqual(canvas.annotations()[0].toPlainText(), "修改后")
        self.assertTrue(canvas.annotations()[0].document().firstBlock().blockFormat().alignment() & Qt.AlignRight)
        self.assertTrue(editor.isVisible())
        canvas.undo()
        self.assertEqual(canvas.annotations()[0].toPlainText(), "原文")
        editor.close()

    def test_double_click_select_tool_routes_text_to_edit_and_shape_to_delete(self):
        from config.config_manager import DEFAULTS
        from editor.annotation_items import text_item, shape as make_shape
        from unittest.mock import patch
        from PySide6.QtCore import QEvent
        from PySide6.QtGui import QMouseEvent
        canvas = AnnotationCanvas(Image.new("RGB", (200, 120), "white"), dict(DEFAULTS))
        rect = make_shape("rect", QPointF(10, 10), QPointF(65, 60), "#ff0000", 3)
        text = text_item(QPointF(90, 10), "保留", DEFAULTS, Qt.AlignLeft)
        canvas.scene_data.addItem(rect)
        canvas.scene_data.addItem(text)
        canvas.checkpoint()
        canvas.set_tool("select")

        def dbl(target):
            canvas.annotation_at = lambda *a, **k: target
            return QMouseEvent(QEvent.Type.MouseButtonDblClick, QPointF(0, 0),
                               Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)

        # 非文字标注：双击删除并可撤销还原
        canvas.mouseDoubleClickEvent(dbl(rect))
        self.assertEqual(len(canvas.annotations()), 1)
        self.assertEqual(canvas.annotations()[0].toPlainText(), "保留")
        canvas.undo()
        self.assertEqual(len(canvas.annotations()), 2)
        # 文字标注：双击编辑而非删除（撤销后从场景取最新文字项，避免陈旧引用）
        live_text = [a for a in canvas.annotations() if hasattr(a, "toPlainText")][0]
        from PySide6.QtWidgets import QDialog
        with patch("editor.text_input_dialog.TextInputDialog") as dialog:
            dialog.return_value.exec.return_value = QDialog.Accepted
            dialog.return_value.text.return_value = "修改后"
            dialog.return_value.changed_settings.return_value = {}
            canvas.mouseDoubleClickEvent(dbl(live_text))
        dialog.assert_called_once()
        self.assertEqual(len(canvas.annotations()), 2)
        edited = [a for a in canvas.annotations() if hasattr(a, "toPlainText")][0]
        self.assertEqual(edited.toPlainText(), "修改后")
        canvas.undo()
        restored = [a for a in canvas.annotations() if hasattr(a, "toPlainText")][0]
        self.assertEqual(restored.toPlainText(), "保留")
        canvas.close()

    def test_double_click_text_in_select_tool_edits_not_deletes(self):
        from config.config_manager import DEFAULTS
        from editor.annotation_items import text_item
        from unittest.mock import patch
        from PySide6.QtCore import QEvent
        from PySide6.QtGui import QMouseEvent
        canvas = AnnotationCanvas(Image.new("RGB", (200, 120), "white"), dict(DEFAULTS))
        text = text_item(QPointF(10, 10), "原文", DEFAULTS, Qt.AlignLeft)
        canvas.scene_data.addItem(text)
        canvas.set_tool("select")
        canvas.annotation_at = lambda *a, **k: text
        from PySide6.QtWidgets import QDialog
        with patch("editor.text_input_dialog.TextInputDialog") as dialog:
            dialog.return_value.exec.return_value = QDialog.Accepted
            dialog.return_value.text.return_value = "修改后"
            dialog.return_value.changed_settings.return_value = {}
            canvas.mouseDoubleClickEvent(QMouseEvent(QEvent.Type.MouseButtonDblClick, QPointF(0, 0),
                                                     Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))
        dialog.assert_called_once()
        self.assertEqual(dialog.call_args.args[3], "原文")
        self.assertEqual(len(canvas.annotations()), 1)
        self.assertEqual(canvas.annotations()[0].toPlainText(), "修改后")
        canvas.close()

    def test_visible_commands_have_text_and_icons(self):
        from PySide6.QtGui import QImage
        from PySide6.QtWidgets import QPushButton, QMenu, QWidgetAction
        from ui.tray_menu import make_tray_menu
        from sticker.sticker_menu import build_menu

        with tempfile.TemporaryDirectory() as folder:
            settings = SettingsWindow(ConfigManager(Path(folder) / "settings.json"))
            for button in settings.findChildren(QPushButton):
                self.assertTrue(button.text(), button.objectName())
                self.assertFalse(button.icon().isNull(), button.text())
            for index in range(settings.navigation.count()):
                item = settings.navigation.item(index)
                self.assertTrue(item.text())
                self.assertFalse(item.icon().isNull(), item.text())
            tray = make_tray_menu(self.app, lambda: None, lambda: None, lambda: None)
            for action in tray.actions():
                if not action.isSeparator():
                    self.assertTrue(action.text())
                    self.assertFalse(action.icon().isNull(), action.text())

            sticker = StickerItem(QImage(40, 30, QImage.Format_RGB32))
            sticker_menu = build_menu(sticker)
            for menu in (sticker_menu, *sticker_menu.findChildren(QMenu)):
                for action in menu.actions():
                    if not action.isSeparator() and not isinstance(action, QWidgetAction):
                        self.assertTrue(action.text())
                        self.assertFalse(action.icon().isNull(), action.text())
            for action in sticker_menu.actions():
                if action.menu() is not None and action.text() in ("旋转", "透明背景模式", "贴图分组", "透明度"):
                    self.assertIn("\n", action.toolTip(), action.text())
                    for child in action.menu().actions():
                        if not child.isSeparator() and not isinstance(child, QWidgetAction):
                            self.assertIn("\n", child.toolTip(), child.text())
            sticker.close()

    def test_dark_theme_menu_text_follows_palette(self):
        from PySide6.QtGui import QPalette
        from PySide6.QtWidgets import QComboBox, QMenu
        from ui.theme import apply_theme

        original_palette = QPalette(self.app.palette())
        original_mode = self.app.property("screensnap_theme_mode")
        original_sheet = self.app.styleSheet()
        try:
            apply_theme(self.app, "dark")
            self.app.processEvents()
            # 菜单文字使用调色板前景色，避免原生黑底黑字。
            self.assertIn("QMenu", self.app.styleSheet())
            text = self.app.palette().color(QPalette.WindowText).name()
            self.assertIn(text, self.app.styleSheet())
            self.assertIn("QComboBox QAbstractItemView", self.app.styleSheet())
            self.assertIn("background-color: #171717", self.app.styleSheet())
            self.assertIn("selection-color: #ffffff", self.app.styleSheet())
            combo = QComboBox()
            combo.addItems(["第一项", "第二项"])
            combo.show()
            combo.showPopup()
            self.app.processEvents()
            popup = combo.view()
            self.assertEqual(popup.palette().color(QPalette.Base).name(), "#171717")
            self.assertEqual(popup.palette().color(QPalette.Text).name(), "#e8eaed")
            # (4,4) 落在第一项上，而第一项是当前选中项：新的下拉样式用
            # QComboBox QAbstractItemView 的 selection-background-color（highlight）给它上色。
            self.assertEqual(popup.grab().toImage().pixelColor(4, 4).name(), "#2f6096")
            combo.close()
            menu = QMenu()
            menu.addAction("删除标注")
            menu.show()
            self.app.processEvents()
            try:
                image = menu.grab().toImage()
                colors = {image.pixelColor(x, y).name()
                          for y in range(image.height())
                          for x in range(image.width())}
                self.assertIn(text, colors)
            finally:
                menu.close()
            # 浅色主题保持原生菜单外观，不注入应用级样式表。
            apply_theme(self.app, "light")
            self.app.processEvents()
            self.assertIn("QMenu", self.app.styleSheet())
            self.assertIn("padding: 4px 18px 4px 4px", self.app.styleSheet())
        finally:
            self.app.setProperty("screensnap_theme_mode", original_mode)
            self.app.setPalette(original_palette)
            self.app.setStyleSheet(original_sheet)
            self.app.processEvents()


if __name__ == "__main__":  # 支持 python tests/test_editor.py
    unittest.main()


if __name__ == "__main__":
    unittest.main()
