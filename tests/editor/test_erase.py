"""擦除与马赛克：笔迹、分组、实时预览与合成。"""

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


class EraseTests(CoreTests):
    def test_pen_marker_and_mosaic_draw_straight_with_ctrl_or_alt(self):
        from editor.annotation_canvas import AnnotationCanvas
        from PIL import Image
        from PySide6.QtCore import QEvent, QPointF, Qt
        from PySide6.QtGui import QMouseEvent
        from config.config_manager import DEFAULTS
        from editor.annotation_items import AnnotationPathItem, AnnotationPixmapItem

        for tool in ("pen", "marker"):
            for gesture, modifier in (("ctrl", Qt.ControlModifier),
                                      ("alt", Qt.AltModifier)):
                canvas = AnnotationCanvas(Image.new("RGB", (300, 200), "white"),
                                          dict(DEFAULTS))
                canvas.resize(400, 300)
                canvas.show()
                canvas.set_tool(tool)
                self.app.processEvents()
                self._pen_drag(canvas, (20, 20), (80, 60), modifier)
                with self.subTest(tool=tool, gesture=gesture):
                    self.assertEqual(len(canvas.annotations()), 1)
                    self.assertIsInstance(canvas.annotations()[0], AnnotationPathItem)
                    self.assertEqual(canvas.annotations()[0].path().elementCount(), 2)
                canvas.close()

        for gesture, modifier in (("ctrl", Qt.ControlModifier),
                      ("alt", Qt.AltModifier)):
            canvas = AnnotationCanvas(Image.new("RGB", (300, 200), "white"),
                                      dict(DEFAULTS))
            canvas.resize(400, 300)
            canvas.show()
            canvas.set_tool("mosaic")
            self.app.processEvents()
            start = QPointF(canvas.mapFromScene(QPointF(20, 20)))
            finish = QPointF(canvas.mapFromScene(QPointF(80, 60)))

            def send(kind, point, button, buttons):
                self.app.sendEvent(canvas.viewport(), QMouseEvent(
                    kind, point, QPointF(canvas.viewport().mapToGlobal(point.toPoint())),
                    button, buttons, modifier))

            send(QEvent.MouseButtonPress, start, Qt.LeftButton, Qt.LeftButton)
            send(QEvent.MouseMove, finish, Qt.NoButton, Qt.LeftButton)
            with self.subTest(tool="mosaic", gesture=gesture):
                self.assertTrue(canvas.straight_drawing)
                self.assertEqual(canvas.mosaic_drawing.elementCount(), 2)
            send(QEvent.MouseButtonRelease, finish, Qt.LeftButton, Qt.NoButton)
            if gesture == "space":
                canvas.keyReleaseEvent(QKeyEvent(QEvent.KeyRelease, Qt.Key_Space,
                                                 Qt.NoModifier))
            self.assertEqual(len(canvas.annotations()), 1)
            self.assertIsInstance(canvas.annotations()[0], AnnotationPixmapItem)
            canvas.close()

    def test_mosaic_brush_width_drives_stroke_size(self):
        from config.config_manager import DEFAULTS
        from editor.annotation_canvas import AnnotationCanvas
        from editor.annotation_items import AnnotationPixmapItem
        from PIL import Image
        from PySide6.QtCore import QPointF, Qt
        from PySide6.QtGui import QPainterPath

        def stroke_width(width):
            canvas = AnnotationCanvas(Image.new("RGB", (200, 200), "white"),
                                      dict(DEFAULTS, mosaic_brush=True, mosaic_width=width))
            canvas.tool = "mosaic"
            path = QPainterPath(QPointF(60, 100))
            path.lineTo(QPointF(140, 100))
            canvas._commit_mosaic_brush(path)
            items = [item for item in canvas.annotations()
                     if isinstance(item, AnnotationPixmapItem)]
            self.assertEqual(len(items), 1)
            # 横向笔迹：覆盖层高度即笔刷粗细（宽度还含笔迹长度）。
            return items[0].pixmap().height()

        narrow = stroke_width(10)
        wide = stroke_width(60)
        # 笔刷越宽，覆盖层越粗。
        self.assertGreater(wide, narrow)
        # 窄笔刷不应再由 mosaic_size*2 决定（旧行为约 40，明显更粗）。
        self.assertLess(narrow, 20)
        self.assertGreater(wide, 40)

    def test_mosaic_brush_commits_stroked_overlay(self):
        from config.config_manager import DEFAULTS
        from editor.annotation_items import AnnotationPixmapItem
        from PySide6.QtGui import QPainterPath, QImage
        canvas = AnnotationCanvas(Image.new("RGB", (200, 200), "white"), dict(DEFAULTS))
        canvas.settings["mosaic_brush"] = True
        canvas.tool = "mosaic"
        path = QPainterPath(QPointF(50, 50))
        path.lineTo(QPointF(150, 50))
        canvas._commit_mosaic_brush(path)
        items = canvas.annotations()
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertIsInstance(item, AnnotationPixmapItem)
        img = item.pixmap().toImage().convertToFormat(QImage.Format_ARGB32)
        # 笔迹中心应为不透明，笔迹外的圆角之外应为完全透明。
        center = QPoint(100, 50) - item.offset().toPoint()
        self.assertGreater(img.pixelColor(center).alpha(), 0)
        self.assertEqual(img.pixelColor(0, 0).alpha(), 0)
        canvas.close()

    def test_mosaic_brush_preview_is_visible_before_release_and_not_committed(self):
        from config.config_manager import DEFAULTS
        from PySide6.QtCore import QEvent
        from PySide6.QtGui import QMouseEvent
        from editor.annotation_canvas import AnnotationCanvas

        source = Image.new("RGB", (80, 60), "#ed2020")
        for y in range(source.height):
            for x in range(40, source.width):
                source.putpixel((x, y), (25, 45, 230))
        canvas = AnnotationCanvas(source, dict(DEFAULTS))
        try:
            canvas.settings["mosaic_mode"] = "blur"
            canvas.settings["mosaic_brush"] = True
            canvas.settings["mosaic_width"] = 16
            canvas.settings["mosaic_size"] = 10
            canvas.set_tool("mosaic")
            canvas.resize(160, 120)
            canvas.show()
            self.app.processEvents()
            start = canvas.mapFromScene(QPointF(15, 25))
            finish = canvas.mapFromScene(QPointF(55, 25))
            viewport_finish = canvas.viewport().mapFrom(canvas, finish)
            viewport_sample = canvas.viewport().mapFrom(
                canvas, canvas.mapFromScene(QPointF(40, 25)))
            before = canvas.viewport().grab().toImage()
            canvas.mousePressEvent(QMouseEvent(
                QEvent.MouseButtonPress, QPointF(start), QPointF(start),
                Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))
            canvas.mouseMoveEvent(QMouseEvent(
                QEvent.MouseMove, QPointF(finish), QPointF(finish),
                Qt.NoButton, Qt.LeftButton, Qt.NoModifier))
            self.app.processEvents()
            during = canvas.viewport().grab().toImage()
            self.assertIsNotNone(canvas.mosaic_preview)
            self.assertEqual(len(canvas.annotations()), 0)
            self.assertNotEqual(before, during)
            source_color = before.pixelColor(viewport_sample)
            preview_color = during.pixelColor(viewport_sample)
            self.assertNotEqual(preview_color, source_color)
            self.assertGreater(preview_color.red(), 25)
            self.assertGreater(preview_color.blue(), 45)
            canvas.mouseReleaseEvent(QMouseEvent(
                QEvent.MouseButtonRelease, QPointF(finish), QPointF(finish),
                Qt.LeftButton, Qt.NoButton, Qt.NoModifier))
            self.assertIsNone(canvas.mosaic_preview)
            self.assertEqual(len(canvas.annotations()), 1)
        finally:
            canvas.close()

    def test_eraser_removes_only_brushed_pixels_and_undo_restores(self):
        from PySide6.QtTest import QTest
        from config.config_manager import DEFAULTS
        canvas = AnnotationCanvas(Image.new("RGB", (120, 100), "white"), dict(DEFAULTS))
        canvas.resize(200, 150)
        canvas.show()
        canvas.scene_data.addItem(shape("rect", QPointF(20, 20), QPointF(80, 80), "#ff0000", 4))
        canvas.checkpoint()
        canvas.tool = "eraser"
        before = canvas.render_image()
        self.assertEqual(before.pixelColor(50, 20).name(), "#ff0000")
        QTest.mousePress(canvas.viewport(), Qt.LeftButton, pos=canvas.mapFromScene(QPointF(50, 20)))
        QTest.mouseMove(canvas.viewport(), pos=canvas.mapFromScene(QPointF(55, 20)))
        QTest.mouseRelease(canvas.viewport(), Qt.LeftButton, pos=canvas.mapFromScene(QPointF(55, 20)))
        after = canvas.render_image()
        self.assertEqual(after.pixelColor(50, 20).name(), "#ffffff")
        self.assertEqual(after.pixelColor(20, 50).name(), "#ff0000")
        canvas.undo()
        self.assertEqual(canvas.render_image().pixelColor(50, 20), before.pixelColor(50, 20))
        canvas.redo()
        self.assertEqual(canvas.render_image().pixelColor(50, 20).name(), "#ffffff")
        canvas.close()

    def test_eraser_visible_on_live_view_not_only_export(self):
        from PySide6.QtTest import QTest
        from PySide6.QtGui import QImage, QPainter
        from config.config_manager import DEFAULTS
        canvas = AnnotationCanvas(Image.new("RGB", (120, 100), "white"), dict(DEFAULTS))
        canvas.resize(200, 150)
        canvas.show()
        canvas.scene_data.addItem(shape("rect", QPointF(20, 20), QPointF(80, 80), "#ff0000", 4))
        canvas.checkpoint()
        canvas.tool = "eraser"
        QTest.mousePress(canvas.viewport(), Qt.LeftButton, pos=canvas.mapFromScene(QPointF(50, 20)))
        QTest.mouseMove(canvas.viewport(), pos=canvas.mapFromScene(QPointF(56, 20)))
        QTest.mouseRelease(canvas.viewport(), Qt.LeftButton, pos=canvas.mapFromScene(QPointF(56, 20)))
        # 擦除标志应已置位，并驱动实时视图按遮罩合成（露出白色底图而非红色标注）。
        self.assertTrue(canvas.erase_mask_dirty)
        vp = canvas.viewport()
        visible = canvas.mapToScene(vp.rect()).boundingRect()
        img = QImage(vp.size(), QImage.Format_ARGB32)
        img.fill(0)
        painter = QPainter(img)
        painter.translate(-visible.x(), -visible.y())
        canvas._paint_erased_composite(painter)
        painter.end()
        px = int(round(50 - visible.x()))
        py = int(round(20 - visible.y()))
        self.assertEqual(img.pixelColor(px, py).name(), "#ffffff")
        # 撤销后标志复位，合成停止，标注恢复为红色。
        canvas.undo()
        self.assertFalse(canvas.erase_mask_dirty)
        img2 = QImage(vp.size(), QImage.Format_ARGB32)
        img2.fill(0)
        painter2 = QPainter(img2)
        painter2.translate(-visible.x(), -visible.y())
        canvas._paint_erased_composite(painter2)
        painter2.end()
        self.assertEqual(img2.pixelColor(px, py).name(), "#ff0000")
        canvas.close()

    def test_eraser_works_when_stroke_starts_on_annotation_and_on_empty_space(self):
        from PySide6.QtTest import QTest
        from config.config_manager import DEFAULTS
        from editor.annotation_items import shape
        from PIL import Image

        canvas = AnnotationCanvas(Image.new("RGB", (120, 100), "white"), dict(DEFAULTS))
        canvas.resize(200, 150)
        canvas.show()
        canvas.scene_data.addItem(shape("rect", QPointF(20, 20), QPointF(80, 80), "#ff0000", 4))
        canvas.checkpoint()

        def stroke(start, end):
            QTest.mousePress(canvas.viewport(), Qt.LeftButton,
                             pos=canvas.mapFromScene(QPointF(*start)))
            QTest.mouseMove(canvas.viewport(), pos=canvas.mapFromScene(QPointF(*end)))
            QTest.mouseRelease(canvas.viewport(), Qt.LeftButton,
                               pos=canvas.mapFromScene(QPointF(*end)))

        for zoom in (100, 200, 50):
            canvas.set_zoom(zoom)
            canvas.set_tool("eraser")
            canvas.settings["eraser_width"] = 16
            with self.subTest(f"缩放{zoom}% 起笔在标注上"):
                stroke((50, 20), (56, 20))
                self.assertEqual(canvas.render_image().pixelColor(50, 20).name(), "#ffffff")
                canvas.undo()
            with self.subTest(f"缩放{zoom}% 起笔在空白处划过标注"):
                stroke((5, 5), (56, 20))
                # 非破坏式擦除：无论起笔在标注上还是空白处，划过处都应被擦除为白色。
                self.assertEqual(canvas.render_image().pixelColor(50, 20).name(), "#ffffff")
                canvas.undo()
        canvas.close()

    def test_eraser_live_viewport_updates_during_drag_and_erase_base(self):
        from PySide6.QtTest import QTest
        from config.config_manager import DEFAULTS
        # A：擦除标注时，拖动途中真实视口就应显示擦除（不能只在松开时才生效）。
        canvas = AnnotationCanvas(Image.new("RGB", (120, 100), "white"), dict(DEFAULTS))
        canvas.resize(240, 200)
        canvas.show()
        canvas.scene_data.addItem(shape("rect", QPointF(20, 20), QPointF(80, 80), "#ff0000", 4))
        canvas.checkpoint()
        canvas.set_tool("eraser")
        canvas.settings["eraser_width"] = 16
        press = canvas.mapFromScene(QPointF(50, 20))
        QTest.mousePress(canvas.viewport(), Qt.LeftButton, pos=press)
        QTest.mouseMove(canvas.viewport(), pos=canvas.mapFromScene(QPointF(58, 20)))
        self.app.processEvents()
        during = canvas.viewport().grab().toImage()
        # 取样点要避开橡皮擦光标圆环：圆环画在“移动后的指针”处（半径 = eraser_width/2），
        # 正好盖住起始按下点，取那里会采到圆环描边而不是擦除结果。
        sample = canvas.mapFromScene(QPointF(54, 20))
        self.assertEqual(during.pixelColor(sample).name(), "#ffffff", "拖动途中未实时擦除")
        QTest.mouseRelease(canvas.viewport(), Qt.LeftButton,
                           pos=canvas.mapFromScene(QPointF(58, 20)))
        canvas.close()

    def test_eraser_erase_base_live_viewport_clears_background(self):
        from PySide6.QtTest import QTest
        from PySide6.QtGui import QColor, QPalette
        from config.config_manager import DEFAULTS
        settings = dict(DEFAULTS, eraser_width=16)
        canvas = AnnotationCanvas(Image.new("RGB", (120, 100), (10, 20, 30)), settings)
        canvas.resize(240, 200)
        canvas.show()
        canvas.set_tool("eraser")
        canvas.settings["eraser_erase_base"] = True
        press = canvas.mapFromScene(QPointF(50, 50))
        QTest.mousePress(canvas.viewport(), Qt.LeftButton, pos=press)
        QTest.mouseMove(canvas.viewport(), pos=canvas.mapFromScene(QPointF(56, 50)))
        self.app.processEvents()
        live = canvas.viewport().grab().toImage()
        for base_color, expected in (("#000000", {"#3b3f45", "#4c5158"}),
                                     ("#ffffff", {"#e0e3e6", "#f1f3f5"})):
            palette = canvas.palette()
            palette.setColor(QPalette.Base, QColor(base_color))
            canvas.setPalette(palette)
            self.app.processEvents()
            live = canvas.viewport().grab().toImage()
            self.assertIn(live.pixelColor(press).name(), expected)
        QTest.mouseRelease(canvas.viewport(), Qt.LeftButton,
                           pos=canvas.mapFromScene(QPointF(56, 50)))
        self.assertEqual(canvas.render_image().pixelColor(50, 50).alpha(), 0)
        canvas.close()

    def test_editor_eraser_shows_live_erasure_during_drag(self):
        from PySide6.QtTest import QTest
        from config.config_manager import DEFAULTS
        for context in ("window", "inline"):
            with self.subTest(context=context):
                if context == "window":
                    editor = EditorWindow(Image.new("RGB", (200, 160), "white"), dict(DEFAULTS))
                    editor.show()
                    self.app.processEvents()
                    canvas = editor.canvas
                    cleanup = editor.close
                else:
                    from screenshot.mask_window import MaskWindow
                    folder = tempfile.mkdtemp()
                    bounds = {"left": 0, "top": 0, "width": 1200, "height": 900}
                    settings = {**DEFAULTS, "save_dir": folder, "inline_edit": True,
                                "magnifier": False, "capture_after_selection": "edit"}
                    with patch("screenshot.mask_window.visible_windows", return_value=[]):
                        mask = MaskWindow(Image.new("RGB", (1200, 900), "white"),
                                          bounds, [bounds], settings)
                    mask.selection.rects.append(QRect(30, 30, 300, 220))
                    mask.complete()
                    mask.show()
                    self.app.processEvents()
                    canvas = mask.session.inline_editor.canvas
                    cleanup = mask.close
                try:
                    canvas.scene_data.addItem(
                        shape("rect", QPointF(20, 20), QPointF(120, 100), "#ff0000", 4))
                    canvas.checkpoint()
                    canvas.set_tool("eraser")
                    canvas.settings["eraser_width"] = 18
                    press = canvas.mapFromScene(QPointF(60, 20))
                    QTest.mousePress(canvas.viewport(), Qt.LeftButton, pos=press)
                    # 光标移到远处再采样，避免落在橡皮擦虚线圆环（预览）上。
                    QTest.mouseMove(canvas.viewport(), pos=canvas.mapFromScene(QPointF(170, 90)))
                    self.app.processEvents()
                    during = canvas.viewport().grab().toImage()
                    self.assertEqual(during.pixelColor(press).name(), "#ffffff",
                                     f"{context} 拖动途中未实时擦除")
                    QTest.mouseRelease(canvas.viewport(), Qt.LeftButton,
                                       pos=canvas.mapFromScene(QPointF(170, 90)))
                finally:
                    cleanup()

    def test_inline_editor_eraser_removes_annotation_via_mouse(self):
        from PySide6.QtTest import QTest
        from config.config_manager import DEFAULTS
        from editor.annotation_items import shape
        from PySide6.QtCore import QRect
        from PIL import Image
        from screenshot.mask_window import MaskWindow
        from unittest.mock import patch
        import tempfile

        with tempfile.TemporaryDirectory() as folder:
            bounds = {"left": 0, "top": 0, "width": 120, "height": 100}
            settings = dict(DEFAULTS, save_dir=folder, inline_edit=True,
                            magnifier=False, crosshair=False, mask_opacity=0)
            settings["capture_after_selection"] = "edit"
            with patch("screenshot.mask_window.visible_windows", return_value=[]):
                mask = MaskWindow(Image.new("RGB", (120, 100), "white"), bounds, [bounds], settings)
            mask.selection.rects.append(QRect(0, 0, 120, 100))
            mask.complete()
            mask.show()
            self.app.processEvents()
            editor = mask.session.inline_editor
            editor.canvas.scene_data.addItem(
                shape("rect", QPointF(20, 20), QPointF(80, 80), "#ff0000", 4))
            editor.canvas.checkpoint()
            # 通过工具栏切到橡皮擦，再在矩形上拖动。
            editor.toolbar.tool_changed.emit("eraser")
            self.assertEqual(editor.canvas.tool, "eraser")
            QTest.mousePress(editor.canvas.viewport(), Qt.LeftButton,
                             pos=editor.canvas.mapFromScene(QPointF(50, 20)))
            QTest.mouseMove(editor.canvas.viewport(), pos=editor.canvas.mapFromScene(QPointF(56, 20)))
            QTest.mouseRelease(editor.canvas.viewport(), Qt.LeftButton,
                               pos=editor.canvas.mapFromScene(QPointF(56, 20)))
            self.assertEqual(editor.canvas.render_image().pixelColor(50, 20).name(), "#ffffff")
            # 实时画布视图也应显示擦除后的镂空（不再是红色标注）。
            editor.canvas.viewport().update()
            self.app.processEvents()
            vp_img = editor.canvas.viewport().grab().toImage()
            vp_pos = editor.canvas.mapFromScene(QPointF(50, 20))
            self.assertEqual(vp_img.pixelColor(vp_pos.x(), vp_pos.y()).name(), "#ffffff")
            mask.close()

    def test_eraser_keeps_later_annotations_above_earlier_strokes(self):
        """擦除只影响其下方（更早绘制）的标注；擦除后新画的标注在擦除之上，
        二次擦除又能作用于其间新增的标注——覆盖多层叠加场景。"""
        from PySide6.QtTest import QTest
        from config.config_manager import DEFAULTS
        from PIL import Image

        canvas = AnnotationCanvas(Image.new("RGB", (160, 120), "white"),
                                  dict(DEFAULTS, eraser_width=24))
        canvas.resize(320, 240)
        canvas.show()

        def erase(point):
            pos = canvas.mapFromScene(QPointF(*point))
            QTest.mousePress(canvas.viewport(), Qt.LeftButton, pos=pos)
            QTest.mouseRelease(canvas.viewport(), Qt.LeftButton, pos=pos)
            self.app.processEvents()

        def filled(start, end, color):
            return shape("rect", QPointF(*start), QPointF(*end), color, 2,
                         fill_enabled=True, fill_opacity=100)

        # 红色实心块，先擦掉左侧一小块。
        canvas._add_annotation(filled((20, 20), (140, 100), "#ff0000"))
        canvas.checkpoint()
        canvas.set_tool("eraser")
        erase((40, 60))
        self.assertEqual(canvas.render_image().pixelColor(40, 60).name(), "#ffffff")
        self.assertEqual(canvas.render_image().pixelColor(100, 60).name(), "#ff0000")

        # 擦除之后新画的蓝色块位于擦除之上：不被旧擦除挖空，并覆盖下方红色。
        canvas._add_annotation(filled((70, 40), (130, 80), "#0000ff"))
        canvas.checkpoint()
        self.assertEqual(canvas.render_image().pixelColor(100, 60).name(), "#0000ff")
        self.assertEqual(canvas.render_image().pixelColor(40, 60).name(), "#ffffff")
        # 实时画布同样成立：新蓝色块不被旧擦除挖空（此时光标仍在 (40,60)，不干扰采样）。
        live = canvas.viewport().grab().toImage()
        self.assertEqual(live.pixelColor(canvas.mapFromScene(QPointF(100, 60))).name(), "#0000ff")

        # 二次擦除作用于其之前的全部内容（含新画的蓝色块），旧擦除区域不受影响。
        erase((100, 60))
        self.assertEqual(canvas.render_image().pixelColor(100, 60).name(), "#ffffff")
        self.assertEqual(canvas.render_image().pixelColor(40, 60).name(), "#ffffff")
        # 擦除层按 z 分层存在且有序。
        zs = [item.zValue() for item in canvas.erase_items()]
        self.assertGreaterEqual(len(zs), 2)
        self.assertEqual(zs, sorted(zs))

        # 撤销二次擦除后蓝色块恢复（擦除层随历史往返）。
        canvas.undo()
        self.assertEqual(canvas.render_image().pixelColor(100, 60).name(), "#0000ff")
        self.assertEqual(len(canvas.erase_items()), 1)
        canvas.close()

    def test_eraser_erase_base_option_clears_underlying_image(self):
        from config.config_manager import DEFAULTS
        canvas = AnnotationCanvas(Image.new("RGB", (120, 100), (10, 20, 30)),
                                  dict(DEFAULTS, eraser_width=3, eraser_erase_base=True))
        canvas.resize(200, 150)
        canvas.show()
        self.assertEqual(canvas.render_image().pixelColor(40, 40).alpha(), 255)
        canvas.tool = "eraser"
        QTest.mouseClick(canvas.viewport(), Qt.LeftButton, pos=canvas.mapFromScene(QPointF(40, 40)))
        # 开启擦原图后，擦除处底图变为透明（alpha 归零）。
        self.assertEqual(canvas.render_image().pixelColor(40, 40).alpha(), 0)
        canvas.close()

    def test_mosaic_preview_border_ignores_annotation_width(self):
        from PySide6.QtGui import QPainter, QImage
        from config.config_manager import DEFAULTS
        settings = dict(DEFAULTS)
        canvas = AnnotationCanvas(Image.new("RGB", (120, 100), "white"), settings)
        canvas.tool = "mosaic"
        canvas.start = QPointF(10, 10)
        canvas.preview_end = QPointF(80, 60)
        previews = []
        for width in (1, 50):
            settings["pen_width"] = width
            image = QImage(120, 100, QImage.Format_ARGB32)
            image.fill(Qt.transparent)
            painter = QPainter(image)
            try:
                canvas.drawForeground(painter, QRectF(0, 0, 120, 100))
            finally:
                painter.end()
            previews.append(image)
        self.assertEqual(previews[0], previews[1])

    def test_mosaic_modes_produce_distinct_pixels(self):
        from editor.annotation_canvas import mosaic_image
        sample = Image.new("RGB", (60, 40), "white")
        for row in range(40):
            for column in range(60):
                sample.putpixel((column, row), (column * 4, row * 6, 0))
        results = [mosaic_image(sample, mode, 12) for mode in ("blocks", "fine", "blur")]
        self.assertTrue(all(result.size == sample.size for result in results))
        self.assertEqual(len({result.tobytes() for result in results}), 3)

    def test_erase_layer_hit_shape_limited_to_strokes(self):
        """擦除层命中范围限定在笔迹上：整图矩形会让任何场景命中测试被整屏挡住。"""
        from config.config_manager import DEFAULTS
        from editor.annotation_items import EraseMaskItem

        canvas = AnnotationCanvas(Image.new("RGB", (200, 140), "white"), dict(DEFAULTS))
        canvas.erase_segment(QPointF(20, 40), QPointF(120, 40))
        item = canvas.erase_items()[0]
        self.assertIsInstance(item, EraseMaskItem)
        # boundingRect 仍是整幅图（渲染需要），但 shape 只覆盖笔迹附近。
        self.assertTrue(item.boundingRect().contains(QPointF(10, 120)))
        self.assertFalse(item.shape().contains(QPointF(10, 120)))
        self.assertTrue(item.shape().contains(QPointF(70, 40)))
        # 未被擦过的位置不再命中擦除层（旧实现的整图 shape 会到处命中）。
        hits = [other for other in canvas.scene_data.items(
            QPointF(10, 120), Qt.IntersectsItemShape, Qt.DescendingOrder, canvas.transform())
            if isinstance(other, EraseMaskItem)]
        self.assertEqual(hits, [])
        canvas.close()

    def test_erased_annotation_still_editable_and_deletable(self):
        """被橡皮擦覆盖过的标注：命中测试跳过擦除层，右键与双击仍作用于标注本身。"""
        from config.config_manager import DEFAULTS
        from editor.annotation_items import EraseMaskItem, text_item
        from PySide6.QtCore import QEvent
        from PySide6.QtGui import QMouseEvent
        from PySide6.QtWidgets import QDialog

        canvas = AnnotationCanvas(Image.new("RGB", (200, 140), "white"),
                                  dict(DEFAULTS, eraser_width=24))
        canvas.resize(300, 220)
        canvas.show()
        text = text_item(QPointF(20, 20), "被擦过的文字", canvas.settings, Qt.AlignLeft)
        rect = shape("rect", QPointF(20, 70), QPointF(120, 120), "#ff0000", 3)
        canvas._add_annotation(text)
        canvas._add_annotation(rect)
        canvas.checkpoint()
        # 在文字与矩形上各擦一段，生成 z 高于标注、覆盖其上的擦除层。
        canvas.erase_segment(QPointF(20, 36), QPointF(120, 36))
        canvas.erase_segment(QPointF(20, 96), QPointF(120, 96))
        canvas.checkpoint()
        masks = [item for item in canvas.scene_data.items() if isinstance(item, EraseMaskItem)]
        self.assertTrue(masks)
        self.assertGreater(max(item.zValue() for item in masks),
                           max(text.zValue(), rect.zValue()))

        # 关键：命中测试跳过擦除层，返回被擦的标注本身（旧实现返回擦除层，下面两步全部失效）。
        self.assertIs(canvas.annotation_at(QPointF(40, 36)), text)
        self.assertIs(canvas.annotation_at(QPointF(40, 96)), rect)

        def send(kind, scene_point, button, buttons):
            viewport = canvas.viewport()
            pos = canvas.mapFromScene(scene_point)
            self.app.sendEvent(viewport, QMouseEvent(
                kind, QPointF(pos), QPointF(viewport.mapToGlobal(pos)),
                button, buttons, Qt.NoModifier))
            self.app.processEvents()

        def double_click(scene_point):
            send(QEvent.Type.MouseButtonDblClick, scene_point, Qt.LeftButton, Qt.LeftButton)

        # 普通单击也应选中被擦过的标注（选中后才能用 Delete 键删除）。
        canvas.set_tool("select")
        send(QEvent.Type.MouseButtonPress, QPointF(60, 36), Qt.LeftButton, Qt.LeftButton)
        send(QEvent.Type.MouseButtonRelease, QPointF(60, 36), Qt.LeftButton, Qt.NoButton)
        selected = canvas.scene_data.selectedItems()
        self.assertEqual(len(selected), 1)
        self.assertTrue(hasattr(selected[0], "toPlainText"))
        canvas.remove_selected()
        self.assertEqual(len(canvas.annotations()), 1)
        canvas.undo()
        self.assertEqual(len(canvas.annotations()), 2)

        # 双击被擦过的矩形 → 删除（旧实现命中擦除层，删不掉）。
        canvas.set_tool("select")
        double_click(QPointF(40, 96))
        self.assertEqual(len(canvas.annotations()), 1)
        self.assertTrue(hasattr(canvas.annotations()[0], "toPlainText"))
        canvas.undo()
        self.assertEqual(len(canvas.annotations()), 2)

        # 双击被擦过的文字 → 打开编辑对话框而不是删除。
        canvas.set_tool("select")
        with patch("editor.text_input_dialog.TextInputDialog") as dialog:
            dialog.return_value.exec.return_value = QDialog.Rejected
            double_click(QPointF(40, 36))
        dialog.assert_called_once()
        self.assertEqual(len(canvas.annotations()), 2)

        # 右键被擦过的文字 → 菜单作用于该标注本身，删除后可撤销还原。
        with patch.object(canvas, "show_annotation_menu") as show_menu:
            send(QEvent.Type.MouseButtonPress, QPointF(40, 36), Qt.RightButton, Qt.RightButton)
            send(QEvent.Type.MouseButtonRelease, QPointF(40, 36), Qt.RightButton, Qt.NoButton)
        show_menu.assert_called_once()
        target = show_menu.call_args.args[0]
        self.assertIs(target, canvas.annotation_at(QPointF(40, 36)))
        self.assertTrue(hasattr(target, "toPlainText"))
        canvas.annotation_menu(target).actions()[-1].trigger()
        self.assertEqual(len(canvas.annotations()), 1)
        canvas.undo()
        self.assertEqual(len(canvas.annotations()), 2)
        canvas.close()

    def test_erase_layer_can_be_deleted_like_a_layer(self):
        """擦除层可作为图层删除：不影响其后画的标注，且可撤销/重做。"""
        from config.config_manager import DEFAULTS

        canvas = AnnotationCanvas(Image.new("RGB", (120, 90), "white"),
                                  dict(DEFAULTS, eraser_width=14))
        canvas.resize(240, 200)
        canvas.show()
        canvas._add_annotation(shape("rect", QPointF(10, 10), QPointF(60, 60), "#ff0000", 3,
                                     fill_enabled=True, fill_opacity=100, fill_color="#ff0000"))
        canvas.checkpoint()
        canvas.erase_segment(QPointF(20, 35), QPointF(50, 35))
        canvas.checkpoint()
        # 第一次擦除之后又画了新标注，需要新开一层，故两次擦除是两个独立图层。
        canvas._add_annotation(shape("rect", QPointF(70, 10), QPointF(110, 60), "#00ff00", 3,
                                     fill_enabled=True, fill_opacity=100, fill_color="#00ff00"))
        canvas.checkpoint()
        canvas.erase_segment(QPointF(80, 35), QPointF(100, 35))
        canvas.checkpoint()
        self.assertEqual(len(canvas.erase_items()), 2)
        self.assertEqual(canvas.render_image().pixelColor(20, 35).name(), "#ffffff")
        self.assertEqual(canvas.render_image().pixelColor(80, 35).name(), "#ffffff")

        # 删除最近一层：只恢复第二个矩形，第一个矩形仍保持擦除，标注一条没少。
        self.assertEqual(canvas.erase_layer("erase_one"), 1)
        self.assertEqual(len(canvas.erase_items()), 1)
        self.assertEqual(len(canvas.annotations()), 2)
        image = canvas.render_image()
        self.assertEqual(image.pixelColor(80, 35).name(), "#00ff00")
        self.assertEqual(image.pixelColor(20, 35).name(), "#ffffff")

        # 按点删除：只删掉在这一带留下笔迹的层。
        self.assertTrue(canvas.erased_at(QPointF(20, 35)))
        self.assertEqual(canvas.erase_layer("erase_at", QPointF(20, 35)), 1)
        self.assertEqual(canvas.erase_items(), [])
        self.assertFalse(canvas.erased_at(QPointF(20, 35)))
        self.assertEqual(canvas.render_image().pixelColor(20, 35).name(), "#ff0000")
        self.assertEqual(len(canvas.annotations()), 2)

        # 删除擦除层同样进入历史：撤销恢复、重做再删除。
        canvas.undo()
        self.assertEqual(len(canvas.erase_items()), 1)
        self.assertEqual(canvas.render_image().pixelColor(20, 35).name(), "#ffffff")
        canvas.redo()
        self.assertEqual(canvas.erase_items(), [])

        # 清除全部擦除：把所有擦除层一并删除，标注不受影响。
        canvas.undo()
        self.assertEqual(canvas.erase_layer("erase_clear"), 1)
        self.assertEqual(canvas.erase_items(), [])
        self.assertEqual(len(canvas.annotations()), 2)
        canvas.close()

    def test_right_click_erased_area_shows_erase_layer_menu(self):
        """右键被擦除区域（该处没有标注）→ 提供删除擦除层入口，无需回退后续编辑。"""
        from config.config_manager import DEFAULTS
        from PySide6.QtCore import QEvent
        from PySide6.QtGui import QMouseEvent

        canvas = AnnotationCanvas(Image.new("RGB", (120, 90), "white"),
                                  dict(DEFAULTS, eraser_width=14))
        canvas.resize(240, 200)
        canvas.show()
        canvas.set_tool("select")
        canvas._add_annotation(shape("rect", QPointF(10, 10), QPointF(60, 60), "#ff0000", 3))
        canvas.checkpoint()
        canvas.erase_segment(QPointF(80, 70), QPointF(110, 70))   # 只擦空白区域
        canvas.checkpoint()
        outside = QPointF(95, 70)
        self.assertIsNone(canvas.annotation_at(outside))
        self.assertTrue(canvas.erased_at(outside))

        def send(kind, button, buttons):
            viewport = canvas.viewport()
            pos = canvas.mapFromScene(outside)
            self.app.sendEvent(viewport, QMouseEvent(
                kind, QPointF(pos), QPointF(viewport.mapToGlobal(pos)),
                button, buttons, Qt.NoModifier))
            self.app.processEvents()

        with patch.object(canvas, "show_erase_menu") as show_menu:
            send(QEvent.Type.MouseButtonPress, Qt.RightButton, Qt.RightButton)
            send(QEvent.Type.MouseButtonRelease, Qt.RightButton, Qt.NoButton)
        show_menu.assert_called_once()
        called = show_menu.call_args.args[0]
        self.assertAlmostEqual(called.x(), outside.x(), delta=1)
        self.assertAlmostEqual(called.y(), outside.y(), delta=1)

        menu = canvas.erase_menu(outside)
        self.assertEqual([action.text() for action in menu.actions()],
                         ["删除此处擦除", "删除最近一次擦除", "清除全部擦除", "此处同时擦除原图"])
        self.assertFalse(menu.actions()[3].isChecked())
        self.assertTrue(menu.actions()[3].isCheckable())
        menu.actions()[0].trigger()
        self.assertEqual(canvas.erase_items(), [])
        self.assertEqual(len(canvas.annotations()), 1)
        canvas.undo()
        self.assertEqual(len(canvas.erase_items()), 1)
        canvas.close()

    def test_erase_at_and_last_remove_only_their_own_stroke(self):
        """同一擦除层内的多笔擦除：「删除此处擦除 / 删除最近一次擦除」只删对应笔迹。"""
        from config.config_manager import DEFAULTS

        canvas = AnnotationCanvas(Image.new("RGB", (120, 90), "white"),
                                  dict(DEFAULTS, eraser_width=14))
        canvas.resize(240, 200)
        canvas.show()
        canvas._add_annotation(shape("rect", QPointF(10, 10), QPointF(110, 80), "#ff0000", 3,
                                     fill_enabled=True, fill_opacity=100, fill_color="#ff0000"))
        canvas.checkpoint()
        # 两次独立的擦除，且两次之间没有新标注 → 合并写入同一个擦除层。
        canvas.erase_segment(QPointF(25, 30), QPointF(90, 30))
        canvas.erase_segment(QPointF(25, 60), QPointF(90, 60))
        canvas.checkpoint()
        self.assertEqual(len(canvas.erase_items()), 1)
        image = canvas.render_image()
        self.assertEqual(image.pixelColor(50, 30).name(), "#ffffff")
        self.assertEqual(image.pixelColor(50, 60).name(), "#ffffff")

        # 「删除此处擦除」只清掉经过该点的那一次擦除，另一笔保持。
        self.assertEqual(canvas.erase_layer("erase_at", QPointF(50, 30)), 1)
        self.assertEqual(len(canvas.erase_items()), 1)
        image = canvas.render_image()
        self.assertEqual(image.pixelColor(50, 30).name(), "#ff0000")
        self.assertEqual(image.pixelColor(50, 60).name(), "#ffffff")
        self.assertFalse(canvas.erased_at(QPointF(50, 30)))
        self.assertTrue(canvas.erased_at(QPointF(50, 60)))

        # 「删除最近一次擦除」删掉剩下的那一笔，此时擦除内容才清空。
        self.assertEqual(canvas.erase_layer("erase_one"), 1)
        self.assertEqual(canvas.erase_items(), [])
        self.assertEqual(canvas.render_image().pixelColor(50, 60).name(), "#ff0000")

        # 撤销逐次恢复：先恢复最后一笔，再恢复两笔。
        canvas.undo()
        self.assertEqual(len(canvas.erase_items()), 1)
        self.assertTrue(canvas.erased_at(QPointF(50, 60)))
        canvas.undo()
        self.assertTrue(canvas.erased_at(QPointF(50, 30)))
        self.assertTrue(canvas.erased_at(QPointF(50, 60)))
        self.assertEqual(len(canvas.annotations()), 1)
        canvas.close()

    def test_erase_base_is_recorded_per_stroke(self):
        """「同时擦除原图」按每一次擦除记录：之后切换开关不会改动已有擦除。"""
        from config.config_manager import DEFAULTS

        settings = dict(DEFAULTS, eraser_width=16, eraser_erase_base=False)
        canvas = AnnotationCanvas(Image.new("RGB", (120, 100), (10, 20, 30)), settings)
        canvas.resize(240, 200)
        canvas.show()
        # 第一笔在开关关闭时擦（只擦标注、露出原图），第二笔在开启后擦（连原图一起擦）。
        canvas.erase_segment(QPointF(30, 30), QPointF(40, 30))
        canvas.settings["eraser_erase_base"] = True
        canvas.erase_segment(QPointF(30, 70), QPointF(40, 70))
        canvas.checkpoint()
        image = canvas.render_image()
        self.assertEqual(image.pixelColor(35, 30).name(), "#0a141e")
        self.assertEqual(image.pixelColor(35, 70).alpha(), 0)

        # 再把开关关掉：已有两笔的各自效果都保持不变（不会被统一成“不擦原图”）。
        canvas.settings["eraser_erase_base"] = False
        image = canvas.render_image()
        self.assertEqual(image.pixelColor(35, 30).name(), "#0a141e")
        self.assertEqual(image.pixelColor(35, 70).alpha(), 0)

        # 逐笔查询与改写：只改被点中的那一笔。
        self.assertFalse(canvas.erase_base_at(QPointF(35, 30)))
        self.assertTrue(canvas.erase_base_at(QPointF(35, 70)))
        self.assertEqual(canvas.set_erase_base(QPointF(35, 30), True), 1)
        self.assertTrue(canvas.erase_base_at(QPointF(35, 30)))
        image = canvas.render_image()
        self.assertEqual(image.pixelColor(35, 30).alpha(), 0)
        self.assertEqual(image.pixelColor(35, 70).alpha(), 0)

        # 撤销恢复该笔的标记与原图。
        canvas.undo()
        self.assertFalse(canvas.erase_base_at(QPointF(35, 30)))
        self.assertEqual(canvas.render_image().pixelColor(35, 30).name(), "#0a141e")
        canvas.close()

    def test_layer_menu_deletes_erase_layer_in_both_editors(self):
        """「层级」菜单的删除擦除命令在独立编辑器与原地编辑器都应生效。"""
        from PySide6.QtWidgets import QToolButton
        from config.config_manager import DEFAULTS

        for context in ("window", "inline"):
            with self.subTest(context=context):
                if context == "window":
                    editor = EditorWindow(Image.new("RGB", (140, 100), "white"),
                                          dict(DEFAULTS, eraser_width=14))
                    editor.show()
                    self.app.processEvents()
                    cleanup = editor.close
                else:
                    from screenshot.mask_window import MaskWindow
                    folder = tempfile.mkdtemp()
                    bounds = {"left": 0, "top": 0, "width": 1200, "height": 900}
                    settings = {**DEFAULTS, "save_dir": folder, "inline_edit": True,
                                "magnifier": False, "capture_after_selection": "edit",
                                "eraser_width": 14}
                    with patch("screenshot.mask_window.visible_windows", return_value=[]):
                        mask = MaskWindow(Image.new("RGB", (1200, 900), "white"),
                                          bounds, [bounds], settings)
                    mask.selection.rects.append(QRect(30, 30, 300, 220))
                    mask.complete()
                    mask.show()
                    self.app.processEvents()
                    editor = mask.session.inline_editor
                    cleanup = mask.close
                try:
                    canvas = editor.canvas
                    canvas._add_annotation(shape("rect", QPointF(10, 10), QPointF(60, 60),
                                                 "#ff0000", 3))
                    canvas.erase_segment(QPointF(20, 35), QPointF(50, 35))
                    canvas.checkpoint()
                    self.assertEqual(len(canvas.erase_items()), 1)
                    menu = next(button.menu() for button in editor.toolbar.findChildren(QToolButton)
                                if button.text() == "层级" and button.menu() is not None)
                    texts = [action.text() for action in menu.actions()]
                    self.assertIn("删除最近一次擦除", texts)
                    self.assertIn("清除全部擦除", texts)
                    next(action for action in menu.actions()
                         if action.data() == "erase_one").trigger()
                    self.assertEqual(canvas.erase_items(), [])
                    self.assertEqual(len(canvas.annotations()), 1)
                    canvas.undo()
                    self.assertEqual(len(canvas.erase_items()), 1)
                finally:
                    cleanup()

    def test_eraser_width_range_and_preview_follow_tool(self):
        from PySide6.QtCore import QRectF
        from config.config_manager import DEFAULTS, validate
        from editor.toolbar_widget import ToolbarWidget

        self.assertEqual(validate({})["eraser_width"], 30)
        for width in (9, 101):
            with self.assertRaises(ValueError):
                validate({"eraser_width": width})
        toolbar = ToolbarWidget(settings=dict(DEFAULTS))
        toolbar.tool_buttons["eraser"].click()
        self.assertEqual((toolbar.pen_width.minimum(), toolbar.pen_width.maximum()), (10, 100))
        self.assertEqual(toolbar.pen_width.value(), 30)
        self.assertFalse(toolbar.previews["eraser"].isHidden())
        scene = toolbar.previews["eraser"].build(QRectF(0, 0, 320, 96))
        self.assertGreater(len(scene.items()), 1)
        toolbar.pen_width.setValue(100)
        self.assertEqual(toolbar.tool_widths["eraser"], 100)
        toolbar.tool_buttons["pen"].click()
        self.assertEqual((toolbar.pen_width.minimum(), toolbar.pen_width.maximum()), (1, 50))
        self.assertTrue(toolbar.previews["eraser"].isHidden())
        toolbar.close()

    def test_mosaic_preview_modes_are_visually_distinct(self):
        from PySide6.QtCore import QRectF
        from PySide6.QtGui import QImage, QPainter
        from config.config_manager import DEFAULTS
        from ui.widgets.annotation_preview import AnnotationPreview

        settings = dict(DEFAULTS, mosaic_size=18)
        preview = AnnotationPreview(settings, "mosaic")
        rendered = []
        for mode in ("blocks", "fine", "blur"):
            settings["mosaic_mode"] = mode
            scene = preview.build(QRectF(0, 0, 320, 96))
            image = QImage(320, 96, QImage.Format_ARGB32)
            image.fill(Qt.transparent)
            painter = QPainter(image)
            try:
                scene.render(painter, QRectF(0, 0, 320, 96), scene.sceneRect())
            finally:
                painter.end()
            rendered.append(tuple(image.pixelColor(x, y).rgba()
                                  for y in range(0, 96, 2)
                                  for x in range(0, 320, 2)))
        self.assertEqual(len(set(rendered)), 3)
        preview.close()


if __name__ == "__main__":
    unittest.main()
