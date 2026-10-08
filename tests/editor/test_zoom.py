"""缩放与旋转：滚轮、适应窗口、旋转吸附与手柄。"""

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


class ZoomTests(CoreTests):
    def test_inline_options_menu_resizes_on_first_open_for_each_tool(self):
        from config.config_manager import DEFAULTS
        from screenshot.mask_window import MaskWindow

        with tempfile.TemporaryDirectory() as folder:
            bounds = {"left": 0, "top": 0, "width": 1200, "height": 900}
            settings = {**DEFAULTS, "save_dir": folder, "inline_edit": True, "magnifier": False}
            settings["capture_after_selection"] = "edit"
            settings["capture_after_selection"] = "edit"
            with patch("screenshot.mask_window.visible_windows", return_value=[]):
                mask = MaskWindow(Image.new("RGB", (1200, 900), "blue"), bounds, [bounds], settings)
            mask.selection.rects.append(QRect(40, 40, 100, 80))
            mask.complete()
            mask.show()
            toolbar = mask.session.inline_editor.toolbar
            menu = toolbar.options_button.menu()
            panel = menu.actions()[0].defaultWidget()
            widths = []
            for tool in ("arrow", "pen", "text", "eraser", "arrow"):
                toolbar.tool_buttons[tool].click()
                menu.popup(mask.mapToGlobal(QPoint(400, 300)))
                self.app.processEvents()
                widths.append(menu.width())
                self.assertGreaterEqual(menu.width(), panel.width(), tool)
                for row in toolbar.option_rows:
                    for widget in row:
                        if widget.isVisible():
                            self.assertLessEqual(widget.geometry().right(), panel.width(), tool)
                menu.hide()
                self.app.processEvents()
            self.assertGreater(widths[0], widths[1], widths)
            self.assertGreater(widths[2], widths[3], widths)
            self.assertEqual(widths[0], widths[4], widths)
            mask.close()

    def test_corner_resize_keeps_aspect_after_rotation_and_free_with_modifier(self):
        """四角默认等比、按住修饰键自由拉伸的规则，旋转后依然成立。"""
        import math
        from config.config_manager import DEFAULTS
        from editor.annotation_items import shape
        from editor.annotation_canvas import AnnotationCanvas
        from PIL import Image
        from PySide6.QtCore import QPointF, Qt, QEvent
        from PySide6.QtGui import QMouseEvent

        canvas = AnnotationCanvas(Image.new("RGB", (400, 300), "white"), dict(DEFAULTS))
        canvas.resize(500, 380)
        canvas.show()
        canvas.set_tool("select")
        try:
            # (旋转角, 修饰键, 是否期望等比)
            cases = ((0, Qt.NoModifier, True), (0, Qt.ControlModifier, False),
                     (30, Qt.NoModifier, True), (30, Qt.ControlModifier, False),
                     (90, Qt.NoModifier, True))
            for degrees, modifier, expect_uniform in cases:
                canvas.restore([])
                canvas.reset_history()
                canvas.cursor_index = 0
                item = shape("rect", QPointF(180, 140), QPointF(240, 180), "#ff0000", 2)
                canvas.scene_data.addItem(item)
                # 与真实旋转一致：绕中心旋转。
                item.setTransformOriginPoint(item.boundingRect().center())
                if degrees:
                    item.setRotation(degrees)
                item.setSelected(True)
                canvas.checkpoint()
                handles = canvas.item_resize_handles(item)
                rad = math.radians(degrees)
                cos_a, sin_a = math.cos(rad), math.sin(rad)
                # 刻意用非等比位移（x 远大于 y），等比时应被拉回同一比例。
                dx, dy = 40, 5
                delta = QPointF(dx * cos_a - dy * sin_a, dx * sin_a + dy * cos_a)
                start = canvas.mapFromScene(handles["se"])
                finish = canvas.mapFromScene(handles["se"] + delta)

                def send(kind, position, buttons=Qt.NoButton):
                    self.app.sendEvent(canvas.viewport(), QMouseEvent(
                        kind, QPointF(position),
                        QPointF(canvas.viewport().mapToGlobal(position)),
                        Qt.LeftButton, buttons, modifier))

                send(QEvent.MouseButtonPress, start, Qt.LeftButton)
                send(QEvent.MouseMove, finish, Qt.LeftButton)
                send(QEvent.MouseButtonRelease, finish)
                transform = item.transform()
                uniform = abs(transform.m11() - transform.m22()) < 1e-6
                self.assertEqual(uniform, expect_uniform, (degrees, modifier))
                canvas.resizing = None
        finally:
            canvas.close()

    def test_rotated_item_keeps_resize_handles(self):
        from config.config_manager import DEFAULTS
        from editor.annotation_items import shape
        from PySide6.QtCore import QPointF
        canvas = AnnotationCanvas(Image.new("RGB", (200, 200), "white"), dict(DEFAULTS))
        item = shape("rect", QPointF(80, 80), QPointF(140, 140), "#ff0000", 3)
        canvas.scene_data.addItem(item)
        item.setRotation(45)
        item.setSelected(True)
        canvas.viewport().update()
        # 旋转与缩放共存：旋转后 8 个缩放手柄依然命中（且随项一起转）。
        handles = canvas.item_resize_handles(item)
        self.assertEqual(len(handles), 8)
        for name, position in handles.items():
            self.assertEqual(canvas.resize_handle_at(handles, position), name)
        # 手柄确实贴在旋转后的标注上（不再是轴对齐包围盒的角）。
        rect = item.boundingRect()
        self.assertAlmostEqual(handles["nw"].x(),
                               item.mapToScene(rect.topLeft()).x(), delta=0.01)
        self.assertAlmostEqual(handles["nw"].y(),
                               item.mapToScene(rect.topLeft()).y(), delta=0.01)
        canvas.close()

    def test_resize_rotated_item_keeps_anchor_and_rotation(self):
        import math
        from config.config_manager import DEFAULTS
        from editor.annotation_items import shape
        from PySide6.QtCore import QEvent, QPointF
        from PySide6.QtGui import QMouseEvent

        # 画布留足边距，避免缩放后触发 constrain_item 的边界夹取干扰锚点校验。
        canvas = AnnotationCanvas(Image.new("RGB", (400, 300), "white"), dict(DEFAULTS))
        canvas.resize(500, 380)
        canvas.show()
        opposite = {"nw": "se", "n": "s", "ne": "sw", "e": "w",
                    "se": "nw", "s": "n", "sw": "ne", "w": "e"}
        # 局部坐标下向外拖动的位移（角落手柄会等比缩放，故只校验“变大”与锚点不动）。
        drags = {"se": (24, 18), "nw": (-24, -18), "e": (24, 0), "s": (0, 18)}
        try:
            for degrees in (30, 45, 90):
                for handle_name, (dx, dy) in drags.items():
                    canvas.restore([])
                    canvas.reset_history()
                    canvas.cursor_index = 0
                    item = shape("rect", QPointF(180, 140), QPointF(240, 180), "#ff0000", 2)
                    canvas.scene_data.addItem(item)
                    # 与真实旋转一致：绕标注中心旋转（_begin_rotation 也会先设原点），
                    # 否则会绕局部原点把标注甩出画布并触发边界夹取。
                    item.setTransformOriginPoint(item.boundingRect().center())
                    item.setRotation(degrees)
                    item.setSelected(True)
                    canvas.checkpoint()
                    handles = canvas.item_resize_handles(item)
                    anchor = handles[opposite[handle_name]]
                    area_before = (item.sceneBoundingRect().width()
                                   * item.sceneBoundingRect().height())
                    rad = math.radians(degrees)
                    cos_a, sin_a = math.cos(rad), math.sin(rad)
                    # 把局部位移旋回场景，模拟沿标注自身方向拖动。
                    delta = QPointF(dx * cos_a - dy * sin_a, dx * sin_a + dy * cos_a)
                    start = canvas.mapFromScene(handles[handle_name])
                    finish = canvas.mapFromScene(handles[handle_name] + delta)
                    for event_type, position, button, buttons in (
                            (QEvent.MouseButtonPress, start, Qt.LeftButton, Qt.LeftButton),
                            (QEvent.MouseMove, finish, Qt.NoButton, Qt.LeftButton),
                            (QEvent.MouseButtonRelease, finish, Qt.LeftButton, Qt.NoButton)):
                        self.app.sendEvent(canvas.viewport(), QMouseEvent(
                            event_type, QPointF(position),
                            QPointF(canvas.viewport().mapToGlobal(position)),
                            button, buttons, Qt.NoModifier))
                    area_after = (item.sceneBoundingRect().width()
                                  * item.sceneBoundingRect().height())
                    self.assertGreater(area_after, area_before, (degrees, handle_name))
                    self.assertAlmostEqual(item.rotation(), degrees, delta=0.01,
                                           msg=(degrees, handle_name))
                    anchor_now = canvas.item_resize_handles(item)[opposite[handle_name]]
                    self.assertAlmostEqual(anchor_now.x(), anchor.x(), delta=2,
                                           msg=(degrees, handle_name))
                    self.assertAlmostEqual(anchor_now.y(), anchor.y(), delta=2,
                                           msg=(degrees, handle_name))
        finally:
            canvas.close()

    def test_right_drag_pans_zoomed_canvas_then_restores_marker(self):
        from config.config_manager import DEFAULTS
        for button in (Qt.RightButton, Qt.MiddleButton):
            with self.subTest(button=button):
                canvas = AnnotationCanvas(Image.new("RGB", (800, 600), "white"), dict(DEFAULTS))
                canvas.resize(220, 180)
                canvas.show()
                canvas.set_zoom(200)
                canvas.tool = "marker"
                horizontal = canvas.horizontalScrollBar()
                vertical = canvas.verticalScrollBar()
                horizontal.setValue(horizontal.maximum() // 2)
                vertical.setValue(vertical.maximum() // 2)
                before = (horizontal.value(), vertical.value())
                QTest.mousePress(canvas.viewport(), button, pos=QPoint(110, 90))
                self.assertEqual(canvas.cursor().shape(), Qt.ClosedHandCursor)
                QTest.mouseMove(canvas.viewport(), pos=QPoint(70, 60))
                QTest.mouseRelease(canvas.viewport(), button, pos=QPoint(70, 60))
                self.assertGreater(horizontal.value(), before[0])
                self.assertGreater(vertical.value(), before[1])
                # 松开后必须退出拖动光标；具体形态取决于当前工具（记号笔是位图光标）。
                self.assertNotEqual(canvas.cursor().shape(), Qt.ClosedHandCursor)
                self.assertEqual(canvas.tool, "marker")
                self.assertFalse(canvas.annotations())
                QTest.mousePress(canvas.viewport(), Qt.LeftButton, pos=QPoint(80, 80))
                QTest.mouseMove(canvas.viewport(), pos=QPoint(110, 80))
                QTest.mouseRelease(canvas.viewport(), Qt.LeftButton, pos=QPoint(110, 80))
                self.assertEqual(len(canvas.annotations()), 1)
                canvas.close()

    def test_selected_annotation_resize_handle_not_exported(self):
        from config.config_manager import DEFAULTS
        from PySide6.QtCore import QEvent
        from PySide6.QtGui import QMouseEvent
        canvas = AnnotationCanvas(Image.new("RGB", (120, 100), "white"), DEFAULTS)
        canvas.resize(200, 180)
        canvas.show()
        item = shape("rect", QPointF(10, 10), QPointF(20, 20), "#ff0000", 2)
        canvas.scene_data.addItem(item)
        item.setSelected(True)
        fixed_corner = item.sceneBoundingRect().topLeft()
        self.app.processEvents()
        corner = canvas.mapFromScene(item.sceneBoundingRect().bottomRight())
        def hover(position):
            self.app.sendEvent(canvas.viewport(), QMouseEvent(
                QEvent.MouseMove, QPointF(position),
                QPointF(canvas.viewport().mapToGlobal(position)),
                Qt.NoButton, Qt.NoButton, Qt.NoModifier))

        hover(QPoint(5, 5))
        hover(corner)
        self.assertEqual(canvas.cursor().shape(), Qt.SizeFDiagCursor)
        hover(QPoint(5, 5))
        self.assertEqual(canvas.cursor().shape(), Qt.ArrowCursor)
        hover(corner)
        QTest.mousePress(canvas.viewport(), Qt.LeftButton, pos=corner)
        QTest.mouseMove(canvas.viewport(), corner + QPoint(30, 20))
        QTest.mouseRelease(canvas.viewport(), Qt.LeftButton, pos=corner + QPoint(30, 20))
        resized = item.sceneBoundingRect()
        self.assertGreater(resized.width(), 12)
        self.assertGreater(resized.height(), 12)
        self.assertAlmostEqual(item.sceneBoundingRect().topLeft().x(), fixed_corner.x(), delta=1)
        self.assertAlmostEqual(item.sceneBoundingRect().topLeft().y(), fixed_corner.y(), delta=1)
        self.assertEqual(canvas.render_image().pixelColor(24, 24).name(), "#ffffff")
        canvas.close()

    def test_annotation_items_resize_from_multiple_handles_and_undo(self):
        from config.config_manager import DEFAULTS
        from editor.annotation_items import text_item
        from PySide6.QtCore import QEvent
        from PySide6.QtGui import QMouseEvent

        canvas = AnnotationCanvas(Image.new("RGB", (240, 180), "white"), DEFAULTS)
        canvas.resize(400, 300)
        canvas.show()

        def build(kind):
            return {
                "rect": lambda: shape("rect", QPointF(20, 20), QPointF(60, 60), "#ff0000", 2),
                "ellipse": lambda: shape("ellipse", QPointF(80, 20), QPointF(120, 60), "#00aa00", 2),
                "arrow": lambda: shape("arrow", QPointF(140, 20), QPointF(180, 60), "#0000ff", 2),
                "text": lambda: text_item(QPointF(20, 100), "Resize me", DEFAULTS, Qt.AlignLeft),
            }[kind]

        def drag(start, finish, modifier):
            self.app.sendEvent(canvas.viewport(), QMouseEvent(
                QEvent.MouseButtonPress, QPointF(start),
                QPointF(canvas.viewport().mapToGlobal(start)),
                Qt.LeftButton, Qt.LeftButton, modifier))
            self.app.sendEvent(canvas.viewport(), QMouseEvent(
                QEvent.MouseMove, QPointF(finish),
                QPointF(canvas.viewport().mapToGlobal(finish)),
                Qt.NoButton, Qt.LeftButton, modifier))
            self.app.sendEvent(canvas.viewport(), QMouseEvent(
                QEvent.MouseButtonRelease, QPointF(finish),
                QPointF(canvas.viewport().mapToGlobal(finish)),
                Qt.LeftButton, Qt.NoButton, modifier))

        try:
            # 四种图元 × 两种修饰键：默认所有把手等比，按住 Ctrl 单轴自由拉伸。
            for kind in ("rect", "ellipse", "arrow", "text"):
                for modifier, uniform in ((Qt.NoModifier, True), (Qt.ControlModifier, False)):
                    canvas.restore([])
                    canvas.reset_history()
                    canvas.cursor_index = 0
                    item = build(kind)()
                    canvas._add_annotation(item)
                    item.setSelected(True)
                    canvas.checkpoint()
                    original = item.sceneBoundingRect()
                    original_text_width = (item.document().textWidth()
                                           if kind == "text" else None)
                    handle = QPointF(original.right(), original.center().y())
                    start = canvas.mapFromScene(handle)
                    finish = start + QPoint(22, 0)
                    drag(start, finish, modifier)
                    # 撤销/重做会按快照重建图元，需从画布重新取当前图元。
                    current = canvas.annotations()[0]
                    resized = current.sceneBoundingRect()
                    if kind == "text":
                        # 文字标注的边中点直接调整文本框宽度，不做整体缩放。
                        self.assertGreater(current.document().textWidth(),
                                           original_text_width, modifier)
                        self.assertAlmostEqual(current.transform().m11(), 1.0, places=5)
                    else:
                        self.assertGreater(resized.width(), original.width(), (kind, modifier))
                        if uniform:
                            self.assertAlmostEqual(resized.width() / original.width(),
                                                   resized.height() / original.height(),
                                                   delta=0.05, msg=f"{kind} 默认应等比")
                        else:
                            self.assertAlmostEqual(resized.height(), original.height(), delta=2,
                                                   msg=f"{kind} 按住 Ctrl 应单轴")
                    canvas.undo()
                    restored_item = canvas.annotations()[0]
                    restored = restored_item.sceneBoundingRect()
                    self.assertAlmostEqual(restored.width(), original.width(), delta=1)
                    self.assertAlmostEqual(restored.height(), original.height(), delta=1)
                    if kind == "text":
                        self.assertAlmostEqual(restored_item.document().textWidth(),
                                               original_text_width, delta=1)
                    canvas.redo()
            handles = canvas.resize_handles(QRectF(10, 10, 100, 80))
            self.assertEqual(set(handles), {"nw", "n", "ne", "e", "se", "s", "sw", "w"})
        finally:
            canvas.close()

    def test_selected_annotation_resize_keeps_live_content_visible(self):
        from config.config_manager import DEFAULTS
        from PySide6.QtCore import QEvent
        from PySide6.QtGui import QMouseEvent

        canvas = AnnotationCanvas(Image.new("RGB", (120, 90), "white"), DEFAULTS)
        canvas.resize(240, 180)
        item = shape("rect", QPointF(20, 20), QPointF(60, 60), "#ff0000", 3)
        try:
            canvas.scene_data.addItem(item)
            canvas.show()
            item.setSelected(True)
            self.app.processEvents()
            original = item.sceneBoundingRect()
            handle = canvas.mapFromScene(QPointF(original.right(), original.center().y()))
            finish = handle + QPoint(18, 0)

            def red_ink_pixels():
                frame = canvas.viewport().grab().toImage()
                return sum(1 for y in range(frame.height()) for x in range(frame.width())
                           if (lambda pixel: pixel.red() > 180 and pixel.green() < 100
                               and pixel.blue() < 100)(frame.pixelColor(x, y)))

            selected_ink = red_ink_pixels()
            self.app.sendEvent(canvas.viewport(), QMouseEvent(
                QEvent.MouseButtonPress, QPointF(handle),
                QPointF(canvas.viewport().mapToGlobal(handle)),
                Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))
            self.app.sendEvent(canvas.viewport(), QMouseEvent(
                QEvent.MouseMove, QPointF(finish),
                QPointF(canvas.viewport().mapToGlobal(finish)),
                Qt.NoButton, Qt.LeftButton, Qt.NoModifier))
            self.app.processEvents()

            self.assertGreater(selected_ink, 0)
            self.assertGreater(red_ink_pixels(), selected_ink)
            self.assertGreater(item.sceneBoundingRect().width(), original.width())
        finally:
            canvas.close()

    def test_annotation_resize_all_handles_keeps_opposite_anchor(self):
        from config.config_manager import DEFAULTS
        from PySide6.QtCore import QEvent
        from PySide6.QtGui import QMouseEvent

        canvas = AnnotationCanvas(Image.new("RGB", (240, 180), "white"), DEFAULTS)
        canvas.resize(400, 300)
        canvas.show()
        opposite = {"nw": "se", "n": "s", "ne": "sw", "e": "w",
                    "se": "nw", "s": "n", "sw": "ne", "w": "e"}
        deltas = {"nw": (-18, -14), "n": (0, -14), "ne": (18, -14), "e": (18, 0),
                  "se": (18, 14), "s": (0, 14), "sw": (-18, 14), "w": (-18, 0)}
        try:
            for handle_name, delta in deltas.items():
                canvas.restore([])
                canvas.reset_history()
                canvas.cursor_index = 0
                item = shape("rect", QPointF(40, 40), QPointF(100, 90), "#ff0000", 2)
                canvas.scene_data.addItem(item)
                item.setSelected(True)
                canvas.checkpoint()
                original = item.sceneBoundingRect()
                handles = canvas.resize_handles(original)
                anchor = handles[opposite[handle_name]]
                start = canvas.mapFromScene(handles[handle_name])
                finish = start + QPoint(*delta)
                for event_type, position, button, buttons in (
                        (QEvent.MouseButtonPress, start, Qt.LeftButton, Qt.LeftButton),
                        (QEvent.MouseMove, finish, Qt.NoButton, Qt.LeftButton),
                        (QEvent.MouseButtonRelease, finish, Qt.LeftButton, Qt.NoButton)):
                    self.app.sendEvent(canvas.viewport(), QMouseEvent(
                        event_type, QPointF(position),
                        QPointF(canvas.viewport().mapToGlobal(position)),
                        button, buttons, Qt.NoModifier))
                resized = item.sceneBoundingRect()
                self.assertAlmostEqual(resized.center().x() if handle_name in ("n", "s")
                                       else resized.left() if "w" in opposite[handle_name]
                                       else resized.right(),
                                       anchor.x(), delta=2, msg=handle_name)
                self.assertAlmostEqual(resized.center().y() if handle_name in ("e", "w")
                                       else resized.top() if "n" in opposite[handle_name]
                                       else resized.bottom(),
                                       anchor.y(), delta=2, msg=handle_name)
                if handle_name in ("e", "w", "ne", "nw", "se", "sw"):
                    self.assertGreater(resized.width(), original.width(), handle_name)
                if handle_name in ("n", "s", "ne", "nw", "se", "sw"):
                    self.assertGreater(resized.height(), original.height(), handle_name)
                canvas.undo()
                restored = canvas.annotations()[0].sceneBoundingRect()
                self.assertAlmostEqual(restored.width(), original.width(), delta=1, msg=handle_name)
                self.assertAlmostEqual(restored.height(), original.height(), delta=1, msg=handle_name)
                canvas.redo()
                redone = canvas.annotations()[0].sceneBoundingRect()
                self.assertAlmostEqual(redone.width(), resized.width(), delta=1, msg=handle_name)
                self.assertAlmostEqual(redone.height(), resized.height(), delta=1, msg=handle_name)
        finally:
            canvas.close()

    def test_selected_annotation_move_and_resize_can_overflow_canvas(self):
        """选中标注的移动/缩放不再被夹回画布内；允许超出，超出部分只在绘制时裁掉。"""
        from config.config_manager import DEFAULTS
        canvas = AnnotationCanvas(Image.new("RGB", (120, 100), "white"), dict(DEFAULTS))
        canvas.resize(260, 220)
        canvas.show()
        item = shape("rect", QPointF(20, 20), QPointF(40, 40), "#ff0000", 2)
        canvas.scene_data.addItem(item)
        canvas.tool = "select"
        self.app.processEvents()
        box_center = item.sceneBoundingRect().center()
        grab = canvas.mapFromScene(QPointF(box_center.x() - 10, box_center.y() - 10))
        QTest.mousePress(canvas.viewport(), Qt.LeftButton, pos=grab)
        QTest.mouseMove(canvas.viewport(), grab + QPoint(150, 120))
        QTest.mouseRelease(canvas.viewport(), Qt.LeftButton, pos=grab + QPoint(150, 120))
        self.assertFalse(canvas.sceneRect().contains(item.sceneBoundingRect()))

        # 缩放：先把图形放回画布内，再把右下角拖到画布外，尺寸变大且允许超出。
        item.setPos(QPointF(0, 0))
        item.setSelected(True)
        before_width = item.sceneBoundingRect().width()
        corner = canvas.mapFromScene(item.sceneBoundingRect().bottomRight())
        QTest.mousePress(canvas.viewport(), Qt.LeftButton, pos=corner)
        QTest.mouseMove(canvas.viewport(), corner + QPoint(120, 100))
        QTest.mouseRelease(canvas.viewport(), Qt.LeftButton, pos=corner + QPoint(120, 100))
        self.assertGreater(item.sceneBoundingRect().width(), before_width)
        self.assertFalse(canvas.sceneRect().contains(item.sceneBoundingRect()))
        canvas.close()

    def test_editor_zoom_controls_and_wheel_keep_annotations_aligned(self):
        from PySide6.QtCore import QPointF
        from PySide6.QtGui import QWheelEvent
        from config.config_manager import DEFAULTS
        editor = EditorWindow(Image.new("RGB", (180, 120), "white"), DEFAULTS)
        item = shape("rect", QPointF(10, 10), QPointF(30, 30), "#ff0000", 3)
        editor.canvas.scene_data.addItem(item)
        original_width = item.sceneBoundingRect().width()
        editor.show()
        self.app.processEvents()
        editor.zoom_input.setValue(175)
        self.assertEqual(editor.zoom_slider.value(), 175)
        self.assertAlmostEqual(editor.canvas.transform().m11(), 1.75)
        self.assertAlmostEqual(item.sceneBoundingRect().width() * editor.canvas.transform().m11(),
                               original_width * 1.75)
        editor.zoom_slider.setValue(400)
        self.assertEqual(editor.zoom_input.value(), 400)
        editor.resize(480, 360)
        self.app.processEvents()
        position = QPointF(50, 50)
        vertical = editor.canvas.verticalScrollBar()
        horizontal = editor.canvas.horizontalScrollBar()
        vertical.setValue(vertical.maximum() // 2)
        horizontal.setValue(horizontal.maximum() // 2)
        old_zoom = editor.zoom_input.value()
        old_vertical = vertical.value()
        expected_vertical_step = max(1, vertical.singleStep())
        event = QWheelEvent(position, position, QPoint(0, 0), QPoint(0, 120),
                            Qt.NoButton, Qt.NoModifier, Qt.ScrollUpdate, False)
        self.app.sendEvent(editor.canvas.viewport(), event)
        self.assertEqual(editor.zoom_input.value(), old_zoom)
        self.assertNotEqual(vertical.value(), old_vertical)
        self.assertEqual(old_vertical - vertical.value(), round(expected_vertical_step * 1.5))
        self.assertEqual(editor.canvas.render_image().size(), editor.canvas.base.pixmap().size())
        item.setSelected(True)
        old_item_scale = item.scale()
        # Ctrl+滚轮：只缩放显示比例，不改标注自身尺寸，也不再横向滚动。
        old_horizontal = horizontal.value()
        ctrl_event = QWheelEvent(position, position, QPoint(0, 0), QPoint(0, 120),
                     Qt.NoButton, Qt.ControlModifier, Qt.ScrollUpdate, False)
        self.app.sendEvent(editor.canvas.viewport(), ctrl_event)
        self.assertEqual(editor.zoom_input.value(), old_zoom + 10)
        self.assertEqual(item.scale(), old_item_scale)
        # Alt+滚轮：仍映射为横向移动。
        old_horizontal = horizontal.value()
        expected_horizontal_step = max(1, horizontal.singleStep())
        alt_event = QWheelEvent(position, position, QPoint(0, 0), QPoint(0, 120),
                                Qt.NoButton, Qt.AltModifier, Qt.ScrollUpdate, False)
        self.app.sendEvent(editor.canvas.viewport(), alt_event)
        self.assertNotEqual(horizontal.value(), old_horizontal)
        self.assertEqual(old_horizontal - horizontal.value(), round(expected_horizontal_step * 1.5))
        self.assertEqual(item.scale(), old_item_scale)
        editor.close()

    def test_toolbar_edit_rotation_gap_stays_compact(self):
        from config.config_manager import DEFAULTS
        editor = EditorWindow(Image.new("RGB", (120, 90), "white"), dict(DEFAULTS))
        editor.show()
        self.app.processEvents()
        self.app.processEvents()
        self.assertEqual(editor.width(), 1200)
        margins = editor.toolbar.section_layout.contentsMargins()
        self.assertLessEqual(margins.top(), 2)
        self.assertLessEqual(margins.bottom(), 2)
        self.assertLessEqual(editor.toolbar.section_layout.horizontalSpacing(), 2)
        self.assertLessEqual(editor.toolbar.section_layout.verticalSpacing(), 4)
        editing, rotation = editor.toolbar.sections[1:3]
        initial_gap = rotation.mapTo(editor.toolbar, QPoint(0, 0)).x() - editing.mapTo(
            editor.toolbar, QPoint(editing.width(), 0)).x()
        self.assertLessEqual(initial_gap, 24)
        for width in (900, 950, 1100, 1199, 1200, 1250, 1300, 1700, 2100, 2300, 2600, 1100):
            editor.resize(width, 760)
            self.app.processEvents()
            self.app.processEvents()
            editing, rotation = editor.toolbar.sections[1:3]
            editing_end = editing.mapTo(editor.toolbar, QPoint(editing.width(), 0)).x()
            rotation_start = rotation.mapTo(editor.toolbar, QPoint(0, 0)).x()
            self.assertLessEqual(rotation_start - editing_end, 24, width)
            self.assertGreaterEqual(rotation_start - editing_end, 0, width)
        editor.close()

    def test_arbitrary_rotation_uses_transparent_expansion_and_reset_button(self):
        from PySide6.QtWidgets import QDialog, QDoubleSpinBox, QPushButton
        from config.config_manager import DEFAULTS
        from editor.image_transform import transform

        source = Image.new("RGB", (40, 20), "red")
        rotated = transform(source, "angle", 45)
        self.assertEqual(rotated.mode, "RGBA")
        self.assertEqual(rotated.getpixel((0, 0))[3], 0)
        self.assertEqual(rotated.getpixel((rotated.width // 2, rotated.height // 2))[3], 255)

        editor = EditorWindow(source, dict(DEFAULTS))
        observed = []

        def reset_and_accept(dialog):
            degrees = dialog.findChild(QDoubleSpinBox, "rotationDegrees")
            degrees.setValue(45)
            observed.append((editor.canvas.image.mode, editor.canvas.image.size))
            reset = dialog.findChild(QPushButton, "resetRotationAngle")
            reset.click()
            self.assertEqual(editor.canvas.image.size, source.size)
            self.assertEqual(editor.canvas.image.tobytes(), source.tobytes())
            return QDialog.Accepted

        with patch("PySide6.QtWidgets.QDialog.exec", new=reset_and_accept):
            editor.rotate_angle()
        self.assertEqual(observed[0][0], "RGBA")
        self.assertNotEqual(observed[0][1], source.size)
        editor.close()

    def test_options_menu_resizes_on_first_open_after_tool_change(self):
        from editor.toolbar_widget import ToolbarWidget

        toolbar = ToolbarWidget()
        toolbar.show()
        menu = toolbar.options_button.menu()
        panel = menu.actions()[0].defaultWidget()
        widths = []
        for tool in ("arrow", "pen", "text", "eraser", "arrow"):
            toolbar.tool_buttons[tool].click()
            menu.popup(toolbar.mapToGlobal(QPoint(100, 100)))
            self.app.processEvents()
            widths.append(menu.width())
            self.assertGreaterEqual(menu.width(), panel.width(), (tool, menu.size(), panel.size()))
            self.assertGreaterEqual(panel.width(), toolbar.option_panel_width(tool), tool)
            for row in toolbar.option_rows:
                for widget in row:
                    if widget.isVisible():
                        self.assertLessEqual(widget.geometry().right(), panel.width(),
                                             (tool, widget.geometry(), panel.size()))
            menu.hide()
            self.app.processEvents()
        self.assertGreater(widths[0], widths[1], widths)
        self.assertGreater(widths[2], widths[3], widths)
        self.assertEqual(widths[0], widths[4], widths)
        toolbar.close()


if __name__ == "__main__":
    unittest.main()
