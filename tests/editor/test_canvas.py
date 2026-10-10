"""画布与标注对象：场景、撤销重做、裁剪、棋盘格与图形属性。"""

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


class CanvasTests(CoreTests):
    def test_export_cache_matches_source_pixels_and_refreshes_after_crop(self):
        """回归（2026-10-11 性能优化，防误伤）：render_image() 复用底图导出缓存后必须

        ① **逐像素等于**原始 to_qimage 转换（导出结果不得因缓存而改变）；
        ② 底图变化（裁剪/重置/还原）后缓存必须重建，否则导出会拿到旧图。
        """
        from config.config_manager import DEFAULTS
        from core.screen_capture import to_qimage

        editor = EditorWindow(Image.new("RGB", (60, 40), "#123456"), dict(DEFAULTS))
        canvas = editor.canvas
        expected = to_qimage(canvas.image)
        rendered = canvas.render_image()
        self.assertEqual(rendered.size(), expected.size(), "导出尺寸必须与底图一致")
        for point in ((0, 0), (30, 20), (59, 39)):
            self.assertEqual(rendered.pixelColor(*point).rgb(),
                             expected.pixelColor(*point).rgb(),
                             "导出缓存不得改变像素 %s" % (point,))
        canvas.image = canvas.image.crop((5, 5, 35, 25))
        canvas.refresh_image()
        self.assertEqual(canvas.render_image().size(), to_qimage(canvas.image).size(),
                         "裁剪后导出缓存必须重建")
        editor.close()

    def test_promoting_inline_editor_carries_annotation_objects(self):
        """回归（2026-10-11 用户反馈）：快速编辑里画了标注后按 E 进窗口编辑，标注必须以**对象**交接，

        不能只交一张烘焙好的图片 —— 否则那些标注无法再选中/移动/改样式。这里断言：
        ① 交出去的图是**底图**（标注位置仍是背景色，未被烘焙）；② 标注快照能被新编辑器 restore 回来。
        """
        from config.config_manager import DEFAULTS
        from screenshot.mask_window import MaskWindow
        from editor.annotation_items import AnnotationRectItem

        bounds = {"left": 0, "top": 0, "width": 120, "height": 80}
        settings = {**DEFAULTS, "filename": "inline", "inline_edit": True, "magnifier": False}
        settings["capture_after_selection"] = "edit"
        with patch("screenshot.mask_window.visible_windows", return_value=[]):
            mask = MaskWindow(Image.new("RGB", (120, 80), "blue"), bounds, [bounds], settings)
        routed = []
        mask.edit_requested.connect(lambda images, positions: routed.append((images, positions)))
        try:
            mask.selection.rects.append(QRect(10, 12, 60, 40))
            mask.complete()
            self.app.processEvents()
            editor = mask.session.inline_editor
            self.assertIsNotNone(editor, "原地编辑应已创建")
            canvas = editor.canvas
            for origin in (QPointF(2, 2), QPointF(18, 10)):
                item = AnnotationRectItem(QRectF(origin.x(), origin.y(), 12, 9))
                canvas.scene_data.addItem(item)
            self.assertEqual(len(canvas.annotations()), 2, "先画两个标注")
            mask._promote_inline_editor_to_window()
            self.app.processEvents()
            self.assertEqual(len(routed), 1, "应发出一次转交")
            image = routed[0][0][0][0]                       # [(image, alternate)], [position]
            self.assertEqual(image.size, (60, 40), "交出去的应是选区尺寸的底图")
            pixel = image.convert("RGB").getpixel((8, 6))     # 标注所在处
            self.assertNotEqual(pixel, (255, 0, 0), "标注不得被烘焙进交出去的图片")
            from screenshot.mask_window import take_pending_editor_annotations

            records = take_pending_editor_annotations()
            self.assertTrue(records, "应通过模块级交接槽暂存标注快照")
            self.assertFalse(take_pending_editor_annotations(), "交接槽取一次即清空")
            window = EditorWindow(image, dict(DEFAULTS))
            window.canvas.restore(records)
            self.assertEqual(len(window.canvas.annotations()), 2,
                             "新编辑器应恢复出同样数量的标注对象")
            window.close()
        finally:
            mask.close()

    def test_annotation_points_clamp_to_canvas_in_both_editors(self):
        """契约（2026-10-11 用户要求「这个特性在两个编辑器都要有」）：独立编辑与原地编辑

        都必须是「标注最远只到画布边缘、指针可自由移出」。两者共用同一个 AnnotationCanvas，
        本用例对**两个实例**分别断言，避免以后只改一处（规则 7 / 29）。
        """
        from config.config_manager import DEFAULTS
        from screenshot.mask_window import MaskWindow
        standalone = EditorWindow(Image.new("RGB", (80, 60), "white"), dict(DEFAULTS))
        bounds = {"left": 0, "top": 0, "width": 120, "height": 80}
        settings = {**DEFAULTS, "filename": "inline", "inline_edit": True, "magnifier": False}
        settings["capture_after_selection"] = "edit"
        with patch("screenshot.mask_window.visible_windows", return_value=[]):
            mask = MaskWindow(Image.new("RGB", (120, 80), "blue"), bounds, [bounds], settings)
        try:
            mask.selection.rects.append(QRect(10, 12, 60, 40))
            mask.complete()
            self.app.processEvents()
            inline = mask.session.inline_editor
            self.assertIsNotNone(inline, "原地编辑应已创建")
            for label, canvas in (("独立编辑", standalone.canvas), ("原地编辑", inline.canvas)):
                with self.subTest(editor=label):
                    rect = canvas.sceneRect()
                    outside = QPointF(rect.left() - 30, rect.bottom() + 45)
                    self.assertEqual(canvas.image_point(outside),
                                     QPointF(rect.left() - canvas.OVERFLOW_MARGIN,
                                             rect.bottom() + canvas.OVERFLOW_MARGIN),
                                     "画布外的标注点必须夹到画布边缘（%s）" % label)
                    inside = QPointF(rect.center())
                    self.assertEqual(canvas.image_point(inside), inside,
                                     "画布内的点保持不变（%s）" % label)
            # 框选走原始场景点：同一个点在框选路径上不被夹（与标注几何区分开）
            canvas = standalone.canvas
            outer = QPointF(canvas.sceneRect().left() - 30, canvas.sceneRect().bottom() + 45)
            self.assertNotEqual(canvas.image_point(outer), outer, "标注路径要夹")
        finally:
            mask.close()
            standalone.close()

    def test_annotation_points_stop_at_canvas_edge_but_selection_does_not(self):
        """契约（2026-10-11 用户明确要求）：标注最远只到画布边缘，指针可以自由移出；

        框选（选区橡皮筋）必须用**原始**场景点 —— 否则画布外的按下/右键会被算成边缘那一点，
        影响选择与右键菜单。image_point() 只服务于标注几何，selection_end 不走它。
        """
        from config.config_manager import DEFAULTS
        editor = EditorWindow(Image.new("RGB", (80, 60), "white"), dict(DEFAULTS))
        canvas = editor.canvas
        bounds = canvas.sceneRect()
        outer = QPointF(bounds.left() - 40, bounds.top() - 25)
        margin = canvas.OVERFLOW_MARGIN
        self.assertEqual(canvas.image_point(outer),
                         QPointF(bounds.left() - margin, bounds.top() - margin),
                         "画布外的标注点必须夹到画布边缘")
        far = QPointF(bounds.right() + 120, bounds.bottom() + 90)
        self.assertEqual(canvas.image_point(far),
                         QPointF(bounds.right() + margin, bounds.bottom() + margin))
        inside = QPointF(bounds.center())
        self.assertEqual(canvas.image_point(inside), inside, "画布内的点保持不变")
        source = canvas.image_point.__code__.co_names
        self.assertIn("sceneRect", source, "image_point 仍按画布矩形夹取")
        editor.close()

    def test_text_input_dialog_is_kept_on_top_of_the_mask(self):
        """回归（2026-10-10 真机反馈）：原地编辑里文字输入弹窗被置顶遮罩挡住看不见。

        这里用桩替换对话框，断言画布在弹出前设置了 WindowStaysOnTopHint 并抬升窗口。
        """
        from unittest.mock import patch as _patch
        from config.config_manager import DEFAULTS
        from PySide6.QtWidgets import QDialog

        editor = EditorWindow(Image.new("RGB", (60, 60), "white"), dict(DEFAULTS))
        calls = {}

        class StubDialog(QDialog):
            def __init__(self, parent, *a, **kw):
                super().__init__(parent)
                calls["flags"] = 0
                calls["raised"] = False

            def setWindowFlag(self, flag, on=True):
                calls["flags"] = flag
                return super().setWindowFlag(flag, on)

            def raise_(self):
                calls["raised"] = True
                return super().raise_()

            def activateWindow(self):
                calls["activated"] = True
                return super().activateWindow()

            def exec(self):
                return QDialog.Rejected

        with _patch("editor.text_input_dialog.TextInputDialog", StubDialog):
            editor.canvas.input_text("文字")
        self.assertEqual(calls.get("flags"), Qt.WindowStaysOnTopHint,
                         "文字输入对话框必须置顶，否则会被截图遮罩挡住")
        self.assertTrue(calls.get("raised"), "应显式抬升对话框")
        self.assertTrue(calls.get("activated"), "应激活对话框")
    def test_clicking_blank_exits_edit_state_and_clicking_an_item_keeps_only_it(self):
        """用户 2026-10-10：点击空白应让所有标注退出编辑态；点击某个标注则其它退出。"""
        from PySide6.QtCore import QPoint, QRectF, Qt
        from PySide6.QtGui import QPen
        from PySide6.QtTest import QTest
        from config.config_manager import DEFAULTS
        from editor.annotation_items import AnnotationRectItem, editable

        editor = EditorWindow(Image.new("RGB", (220, 180), "white"), dict(DEFAULTS))
        editor.resize(380, 320)
        editor.show()
        canvas = editor.canvas
        self.app.processEvents()
        first = editable(AnnotationRectItem(QRectF(0, 0, 50, 40)))
        first.setPen(QPen(Qt.red, 4))
        second = editable(AnnotationRectItem(QRectF(0, 0, 50, 40)))
        second.setPen(QPen(Qt.blue, 4))
        canvas.scene_data.addItem(first)
        canvas.scene_data.addItem(second)
        first.setPos(30, 30)
        second.setPos(120, 90)
        canvas.set_tool("pen")
        first.setSelected(True)
        second.setSelected(True)
        self.assertEqual(len(canvas.scene_data.selectedItems()), 2, "准备：两个都在编辑态")
        blank = canvas.mapFromScene(canvas.sceneRect().topLeft() + QRectF(6, 6, 0, 0).topLeft())
        QTest.mouseClick(canvas.viewport(), Qt.LeftButton, pos=blank)
        self.app.processEvents()
        self.assertEqual(canvas.scene_data.selectedItems(), [], "点击空白应退出所有标注的编辑态")
        self.assertEqual(canvas.tool, "pen", "点击空白不应切换工具")
        # 点击某个标注：只保留它，另一个退出编辑态
        first.setSelected(True)
        second.setSelected(True)
        spot = canvas.mapFromScene(second.mapToScene(second.boundingRect().center()))
        QTest.mouseClick(canvas.viewport(), Qt.LeftButton, pos=spot)
        self.app.processEvents()
        selected = canvas.scene_data.selectedItems()
        self.assertEqual(len(selected), 1, "点击某个标注后应只剩它在编辑态")
        self.assertIs(selected[0], second, "留下的应是点中的那个标注")
        self.assertEqual(canvas.tool, "pen", "点击标注不应切换工具")
        editor.close()
    def test_handles_stay_draggable_with_a_drawing_tool(self):
        """回归（用户 2026-10-10）：准备拖控制点却变成绘图。

        任何工具下按在【选中标注的控制点】都必须进入缩放而不是落笔；
        同时空白处框选仍只属于选择工具（不得放开）。
        """
        from PySide6.QtCore import QPoint, QRectF, Qt
        from PySide6.QtGui import QPen
        from PySide6.QtTest import QTest
        from config.config_manager import DEFAULTS
        from editor.annotation_items import AnnotationRectItem, editable

        editor = EditorWindow(Image.new("RGB", (200, 160), "white"), dict(DEFAULTS))
        editor.resize(360, 300)
        editor.show()
        canvas = editor.canvas
        self.app.processEvents()
        item = editable(AnnotationRectItem(QRectF(0, 0, 80, 60)))
        item.setPen(QPen(Qt.red, 4))
        canvas.scene_data.addItem(item)
        item.setPos(50, 40)
        canvas.set_tool("pen")
        item.setSelected(True)
        handles = canvas.item_resize_handles(item)
        corner = canvas.mapFromScene(handles["se"])
        count = len(canvas.annotations())
        QTest.mousePress(canvas.viewport(), Qt.LeftButton, pos=corner)
        self.app.processEvents()
        self.assertIs(canvas.resizing, item, "按在控制点上应进入缩放，而不是落笔")
        self.assertIsNone(getattr(canvas, "_deferred_pen", None), "按控制点不应触发延迟落笔")
        QTest.mouseMove(canvas.viewport(), corner + QPoint(20, 16))
        QTest.mouseRelease(canvas.viewport(), Qt.LeftButton, pos=corner + QPoint(20, 16))
        self.app.processEvents()
        self.assertEqual(len(canvas.annotations()), count, "拖控制点不应新增标注")
        # 说明：真实拖动放量在离屏合成事件里走不到（QTest.mouseMove 不带按键位），
        # 这里只断言「进入了缩放抓取 + 没有落笔 + 没有新增标注」；
        # 拖动放量的正确性由既有的控制点/按键微调用例覆盖。
        self.assertEqual(canvas.tool, "pen", "拖控制点不应切换工具")
        editor.close()
    def test_click_enters_edit_state_while_drag_still_draws(self):
        """规则 1（用户 2026-10-10）：点击=编辑、拖动=画图。

        · 用画笔【单击】已有标注 ⇒ 该标注进入编辑态（被选中），工具不切换、不留下点状笔迹；
        · 用画笔【按住拖动】⇒ 照常画图（在已有标注上也能画）。
        """
        from PySide6.QtCore import QPoint, QRectF, Qt
        from PySide6.QtGui import QPen
        from PySide6.QtTest import QTest
        from config.config_manager import DEFAULTS
        from editor.annotation_items import AnnotationRectItem, editable

        editor = EditorWindow(Image.new("RGB", (200, 160), "white"), dict(DEFAULTS))
        editor.resize(360, 300)
        editor.show()
        canvas = editor.canvas
        self.app.processEvents()
        # 必须走 editable()：否则图元没有 ItemIsSelectable，setSelected 会被静默忽略，
        # 用例就会误报“没能进入编辑态”（真实代码建标注时都会调用 editable）。
        item = editable(AnnotationRectItem(QRectF(0, 0, 70, 50)))
        item.setPen(QPen(Qt.red, 4))
        canvas.scene_data.addItem(item)
        item.setPos(60, 50)
        canvas.set_tool("pen")
        center = canvas.mapFromScene(item.mapToScene(item.boundingRect().center()))
        count = len(canvas.annotations())
        # ① 单击已有标注 ⇒ 进入编辑态（注意 undo 无人调用，对象身份不变）
        QTest.mouseClick(canvas.viewport(), Qt.LeftButton, pos=center)
        self.app.processEvents()
        selected = canvas.scene_data.selectedItems()
        self.assertEqual(len(selected), 1, "单击已有标注应进入编辑态")
        self.assertIs(selected[0], item, "进入编辑态的应是那个标注本身")
        self.assertEqual(canvas.tool, "pen", "进入编辑态不应切换工具")
        self.assertEqual(len(canvas.annotations()), count, "单击不应留下点状笔迹")
        # ② 按住拖动 ⇒ 仍然画图（用项目自带的 _pen_drag 助手，与其它画笔用例同源）
        canvas.scene_data.clearSelection()
        self._pen_drag(canvas, (20, 20), (60, 45))
        self.app.processEvents()
        self.assertEqual(len(canvas.annotations()), count + 1, "拖动应正常画出新标注")
        editor.close()
    def test_rotation_aware_resize_direction_and_rotate_handle_keys(self):
        """① 旋转后缩放方向以【当前实际朝向】为准；② 旋转手柄 + 方向键 = 旋转（↑/→ 顺时针）。

        方向规则（用户 2026-10-10 定义）：把屏幕方向用标注自身的场景变换逆映射到它的
        局部轴，再决定放大还是缩小 —— 所以旋转 180° 后，屏幕「→」对 e 手柄是「往左移」＝缩小。
        """
        from PySide6.QtCore import QRectF
        from PySide6.QtWidgets import QGraphicsRectItem
        from config.config_manager import DEFAULTS
        from editor.annotation_items import AnnotationRectItem

        editor = EditorWindow(Image.new("RGB", (80, 80), "white"), dict(DEFAULTS))
        canvas = editor.canvas
        item = AnnotationRectItem(QRectF(0, 0, 20, 20))
        item.setFlag(QGraphicsRectItem.GraphicsItemFlag.ItemIsSelectable, True)
        canvas.scene_data.addItem(item)
        canvas.scene_data.clearSelection()
        item.setSelected(True)
        # ② 旋转手柄 + 方向键 ⇒ 旋转（每次 1 度，→ 顺时针＝角度变大）
        canvas._active_handle_name = "rotate"
        angle0 = item.rotation()
        self.assertTrue(canvas.rotate_selected(1), "旋转手柄应能旋转")
        self.assertGreater(item.rotation(), angle0, "→ 应为顺时针（角度增加）")
        before_center = item.mapToScene(item.boundingRect().center())
        canvas.rotate_selected(-1)
        after_center = item.mapToScene(item.boundingRect().center())
        self.assertAlmostEqual(before_center.x(), after_center.x(), places=3, msg="旋转中心不漂")
        self.assertAlmostEqual(before_center.y(), after_center.y(), places=3, msg="旋转中心不漂")
        # ① 未旋转：e 手柄在右边 ⇒ 屏幕 → 放大
        item.setRotation(0.0)
        canvas._active_handle_name = "e"
        size0 = item.mapToScene(item.boundingRect()).boundingRect().size()
        canvas.resize_selected(1, 0)
        size1 = item.mapToScene(item.boundingRect()).boundingRect().size()
        self.assertGreater(size1.width(), size0.width(), "未旋转时 → 应放大")
        # ① 旋转 180°：e 手柄视觉上跑到左边 ⇒ 同一个屏幕 → 应该缩小（以实际方向为准）
        item.setRotation(180.0)
        size2 = item.mapToScene(item.boundingRect()).boundingRect().size()
        canvas.resize_selected(1, 0)
        size3 = item.mapToScene(item.boundingRect()).boundingRect().size()
        self.assertLess(size3.width(), size2.width(),
                        "旋转 180° 后 e 手柄在左侧 ⇒ 屏幕 → 应变小（按当前实际方向）")
        # 反向对称：旋转 180° 后屏幕 ← 应放大
        size4 = item.mapToScene(item.boundingRect()).boundingRect().size()
        canvas.resize_selected(-1, 0)
        size5 = item.mapToScene(item.boundingRect()).boundingRect().size()
        self.assertGreater(size5.width(), size4.width(), "旋转 180° 后 ← 应放大")
    def test_all_annotation_types_cover_nudge_and_resize(self):
        """规则 29：全部标注类型逐个核对「微调移动 + 手柄等比缩放」。"""
        from PySide6.QtCore import QPointF, QRectF
        from PySide6.QtGui import QPainterPath, QPixmap
        from PySide6.QtWidgets import QGraphicsRectItem
        from config.config_manager import DEFAULTS
        from editor.annotation_items import (
            AnnotationRectItem, AnnotationEllipseItem, AnnotationPathItem, AnnotationTextItem,
            AnnotationPixmapItem, AnnotationSequenceItem, EraseMaskItem, RoundedRectItem)

        def pen_path():
            path = QPainterPath(QPointF(0, 0))
            path.lineTo(10, 0)
            path.lineTo(10, 10)
            return path

        editor = EditorWindow(Image.new("RGB", (80, 80), "white"), dict(DEFAULTS))
        canvas = editor.canvas
        factories = {
            "rect": lambda: AnnotationRectItem(QRectF(0, 0, 10, 10)),
            "ellipse": lambda: AnnotationEllipseItem(QRectF(0, 0, 10, 10)),
            "rounded": lambda: RoundedRectItem(QRectF(0, 0, 10, 10), 3),
            "pen": lambda: AnnotationPathItem(pen_path()),
            "text": lambda: AnnotationTextItem("x"),
            "pixmap": lambda: AnnotationPixmapItem(QPixmap(12, 12)),
            "sequence": lambda: AnnotationSequenceItem(1),
        }
        covered = []
        for name, make in factories.items():
            with self.subTest(annotation=name):
                item = make()
                item.setFlag(QGraphicsRectItem.GraphicsItemFlag.ItemIsSelectable, True)
                canvas.scene_data.addItem(item)
                canvas.scene_data.clearSelection()
                item.setSelected(True)
                self.assertIn(item, canvas.scene_data.selectedItems(), f"{name}: 应可选中")
                center0 = item.mapToScene(item.boundingRect().center())
                self.assertTrue(canvas.nudge_selected(1, 0), f"{name}: 移动应生效")
                center1 = item.mapToScene(item.boundingRect().center())
                self.assertNotAlmostEqual(center0.x(), center1.x(), places=3, msg=f"{name}: 移动应改变中心")
                size0 = item.mapToScene(item.boundingRect()).boundingRect().size()
                canvas._active_handle_name = "se"
                self.assertTrue(canvas.resize_selected(1, 0), f"{name}: 手柄缩放应生效")
                size1 = item.mapToScene(item.boundingRect()).boundingRect().size()
                self.assertGreater(size1.width(), size0.width(), f"{name}: 宽度应变大")
                self.assertGreater(size1.height(), size0.height(), f"{name}: 等比 ⇒ 高度也要变大")
                center2 = item.mapToScene(item.boundingRect().center())
                self.assertAlmostEqual(center1.x(), center2.x(), places=3, msg=f"{name}: 缩放中心不漂")
                covered.append(name)
        erase = EraseMaskItem(None, 20, 20)
        self.assertFalse(bool(erase.flags() & QGraphicsRectItem.GraphicsItemFlag.ItemIsSelectable),
                         "橡皮层按设计不可选中（不参与微调）")
        self.assertFalse(bool(erase.flags() & QGraphicsRectItem.GraphicsItemFlag.ItemIsMovable),
                         "橡皮层按设计不可移动")
        print("  已覆盖标注类型:", ", ".join(covered), "+ 橡皮层(设计上不可选中)")
        self.assertGreaterEqual(len(covered), 7, "七种类型都要真跑过")

    def test_both_editors_share_canvas_and_handle_scaling(self):
        """规则 7：两个编辑器（独立 EditorWindow / 原地 InlineEditor）行为一致。

        bounds 必须是 dict（MaskWindow 会遍历它）；原地编辑器在 mask.complete() 之后才建立
        —— 这两点照抄通过用例 test_canvas.py 的既有夹具。
        """
        from PySide6.QtCore import QPointF, QRect, QRectF
        from PySide6.QtGui import QPainterPath
        from PySide6.QtWidgets import QGraphicsRectItem
        from config.config_manager import DEFAULTS
        from screenshot.mask_window import MaskWindow
        from editor.annotation_items import AnnotationRectItem, AnnotationPathItem

        standalone = EditorWindow(Image.new("RGB", (80, 80), "white"), dict(DEFAULTS))
        bounds = {"left": 0, "top": 0, "width": 120, "height": 80}
        settings = {**DEFAULTS, "filename": "inline", "inline_edit": True, "magnifier": False}
        settings["capture_after_selection"] = "edit"
        with patch("screenshot.mask_window.visible_windows", return_value=[]):
            mask = MaskWindow(Image.new("RGB", (120, 80), "blue"), bounds, [bounds], settings)
        mask.selection.rects.append(QRect(10, 12, 60, 40))
        mask.complete()
        self.app.processEvents()
        inline = mask.session.inline_editor
        self.assertIsNotNone(inline, "原地编辑应已创建")
        self.assertEqual(type(inline.canvas).__name__, type(standalone.canvas).__name__,
                         "两个编辑器应共享同一个画布类")
        for label, canvas in (("独立编辑", standalone.canvas), ("原地编辑", inline.canvas)):
            with self.subTest(editor=label):
                item = AnnotationRectItem(QRectF(0, 0, 10, 10))
                item.setFlag(QGraphicsRectItem.GraphicsItemFlag.ItemIsSelectable, True)
                canvas.scene_data.addItem(item)
                canvas.scene_data.clearSelection()
                item.setSelected(True)
                size0 = item.mapToScene(item.boundingRect()).boundingRect().size()
                canvas._active_handle_name = "se"
                self.assertTrue(canvas.resize_selected(1, 0), f"{label}: 手柄缩放应生效")
                size1 = item.mapToScene(item.boundingRect()).boundingRect().size()
                self.assertGreater(size1.width(), size0.width(), f"{label}: 宽度应变大")
                self.assertGreater(size1.height(), size0.height(), f"{label}: 等比 ⇒ 高度也要变大")
                canvas._active_handle_name = None
                pos0 = item.pos()
                self.assertTrue(canvas.nudge_selected(1, 0), f"{label}: 移动应生效")
                self.assertNotEqual(item.pos(), pos0, f"{label}: 移动应改变位置")
        print("  两个编辑器均通过（共享 %s）" % type(standalone.canvas).__name__)

    def test_resize_and_nudge_cover_every_annotation_type(self):
        """逐类型核对：微调移动 + 手柄等比缩放，覆盖画布里所有标注类型。

        表驱动（规则 26/29）：每一种类型单独一个 subTest，构造失败也算该类型未通过 ——
        不允许用沉默跳过把「没验到」伪装成通过。
        """
        from PySide6.QtCore import QRectF, QPointF
        from PySide6.QtGui import QPainterPath
        from PySide6.QtWidgets import QGraphicsRectItem, QGraphicsView
        from config.config_manager import DEFAULTS
        from editor.annotation_items import (
            AnnotationRectItem, AnnotationEllipseItem, AnnotationPathItem,
            AnnotationTextItem, RoundedRectItem)

        def _pen_path():
            path = QPainterPath(QPointF(0, 0))
            path.lineTo(10, 0)
            path.lineTo(10, 10)
            return path

        editor = EditorWindow(Image.new("RGB", (80, 80), "white"), dict(DEFAULTS))
        canvas = editor.canvas
        factories = {
            "rect": lambda: AnnotationRectItem(QRectF(0, 0, 10, 10)),
            "ellipse": lambda: AnnotationEllipseItem(QRectF(0, 0, 10, 10)),
            "rounded": lambda: RoundedRectItem(QRectF(0, 0, 10, 10), 3),
            # 路径必须有非退化几何（单点路径包围盒是 0x0，会把断言测成"没变化"）。
            "pen(path)": lambda: AnnotationPathItem(_pen_path()),
            "text": lambda: AnnotationTextItem("x"),
        }
        results = []
        for name, make in factories.items():
            with self.subTest(annotation=name):
                item = make()
                item.setFlag(QGraphicsRectItem.GraphicsItemFlag.ItemIsSelectable, True)
                canvas.scene_data.addItem(item)
                canvas.scene_data.clearSelection()
                item.setSelected(True)
                self.assertIn(item, canvas.scene_data.selectedItems())
                center_before = item.mapToScene(item.boundingRect().center())
                moved = canvas.nudge_selected(1, 0)
                center_after_move = item.mapToScene(item.boundingRect().center())
                self.assertTrue(moved, f"{name}: 移动应生效")
                self.assertNotAlmostEqual(center_before.x(), center_after_move.x(), places=3,
                                          msg=f"{name}: 移动后中心应改变")
                size_before = item.mapToScene(item.boundingRect()).boundingRect().size()
                canvas._active_handle_name = "se"
                scaled = canvas.resize_selected(1, 0)
                size_after = item.mapToScene(item.boundingRect()).boundingRect().size()
                self.assertTrue(scaled, f"{name}: 缩放应生效")
                self.assertGreater(size_after.width(), size_before.width(), f"{name}: 宽度应变大")
                self.assertGreater(size_after.height(), size_before.height(), f"{name}: 等比 ⇒ 高度也要变大")
                center_final = item.mapToScene(item.boundingRect().center())
                self.assertAlmostEqual(center_after_move.x(), center_final.x(), places=3,
                                       msg=f"{name}: 缩放中心不应漂移")
                self.assertAlmostEqual(center_after_move.y(), center_final.y(), places=3,
                                       msg=f"{name}: 缩放中心不应漂移")
                results.append(name)
        print("  已覆盖标注类型:", ", ".join(results))
        self.assertGreaterEqual(len(results), 3, "至少三种类型要真跑过")

    def test_resize_selected_scales_rect_item_by_one_pixel(self):
        """手柄上的键盘缩放：宽、高各 ±1px，且 QRect/QRectF 两种 rect() 都要生效。

        回归用例（2026-10-10）：resize_selected 曾因只认 QRectF、以及漏 import QRect（NameError）
        而在真机上整条路静默失效 —— 按键压住标注手柄完全不见缩放。
        """
        from PySide6.QtWidgets import QGraphicsRectItem
        from config.config_manager import DEFAULTS

        editor = EditorWindow(Image.new("RGB", (60, 60), "white"), dict(DEFAULTS))
        canvas = editor.canvas
        item = QGraphicsRectItem(0, 0, 10, 10)
        item.setFlag(QGraphicsRectItem.GraphicsItemFlag.ItemIsSelectable, True)
        item.setFlag(QGraphicsRectItem.GraphicsItemFlag.ItemIsMovable, True)
        canvas.scene_data.addItem(item)
        item.setSelected(True)
        self.assertIn(item, canvas.scene_data.selectedItems(), "前置条件：标注必须处于选中状态")
        # 新契约（2026-10-10 用户定义）：角标 = 以【中心】为基准等比拉伸，四条边一起动。
        before_center = item.mapToScene(item.boundingRect().center())
        self.assertTrue(canvas.resize_selected(1, 0), "角标向右应放大")
        self.assertGreater(item.rect().width(), 10, "宽度应变大")
        self.assertGreater(item.rect().height(), 10, "等比拉伸时高度也要跟着变大")
        self.assertAlmostEqual(item.rect().width() / item.rect().height(), 1.0, places=3,
                               msg="等比：长宽比应保持 1:1")
        after_center = item.mapToScene(item.boundingRect().center())
        self.assertAlmostEqual(before_center.x(), after_center.x(), places=3, msg="中心不应漂移")
        self.assertAlmostEqual(before_center.y(), after_center.y(), places=3, msg="中心不应漂移")
        # 窗口编辑（独立编辑器）的按键路径：按住手柄 ⇒ 缩放；未按手柄 ⇒ 移动。
        from PySide6.QtTest import QTest
        from PySide6.QtCore import Qt as _Qt
        canvas._active_handle_name = "se"
        w_before = item.rect().width()
        QTest.keyClick(canvas, _Qt.Key_Right)
        self.assertGreater(item.rect().width(), w_before,
                           "窗口编辑里按住手柄按 → 应缩放（不是整体移动）")
        canvas._active_handle_name = None
        pos_before = item.pos()
        QTest.keyClick(canvas, _Qt.Key_Right)
        self.assertNotEqual(item.pos(), pos_before, "未按手柄时按 → 应移动图形")
        # 边中点同样等比（用户确认：不是「只拉一个轴」）：宽高都要变，且长宽比不变。
        canvas._active_handle_name = "n"
        w0, h0 = item.rect().width(), item.rect().height()
        self.assertTrue(canvas.resize_selected(0, -1), "上中点按 ↑ 应缩放")
        self.assertGreater(item.rect().width(), w0, "边中点缩放时宽度也要变（等比）")
        self.assertGreater(item.rect().height(), h0, "边中点缩放时高度也要变（等比）")
        self.assertAlmostEqual(item.rect().width() / item.rect().height(), 1.0, places=3,
                               msg="边中点也要保持长宽比")

    def test_picked_color_updates_active_annotation_color(self):
        from PySide6.QtGui import QGuiApplication
        from config.config_manager import DEFAULTS

        editor = EditorWindow(Image.new("RGB", (40, 30), "white"), dict(DEFAULTS))
        editor.apply_picked_color("#123456")
        self.assertEqual(editor.settings["pen_color"], "#123456")
        self.assertEqual(editor.canvas.settings["pen_color"], "#123456")
        self.assertEqual(editor.toolbar.pen_color.color, "#123456")
        self.assertEqual(QGuiApplication.clipboard().text(), "#123456")
        pen_color = editor.settings["pen_color"]
        editor.toolbar.tool_buttons["marker"].click()
        editor.toolbar.tool_buttons["picker"].click()
        # 取色工具现在也带“更多设置”预览（放大镜/像素网格），options 按钮保持可用。
        self.assertTrue(editor.toolbar.options_button.isEnabled())
        editor.apply_picked_color("#abcdef")
        self.assertEqual(editor.settings["marker_color"], "#abcdef")
        self.assertEqual(editor.settings["pen_color"], pen_color)
        self.assertEqual(editor.toolbar.active_color_label.text(), "marker 颜色")
        editor.close()











    def test_annotation_font_size_and_arrow_style_persist(self):
        from config.config_manager import DEFAULTS
        from config.config_manager import TOOL_WIDTH_KEYS

        with tempfile.TemporaryDirectory() as folder:
            manager = ConfigManager(Path(folder) / "settings.json")
            settings = SettingsWindow(manager)
            editor = EditorWindow(Image.new("RGB", (40, 30), "white"), manager.data)
            editor.setting_changed.connect(settings.set_annotation_setting)
            page = settings.page("编辑器")
            for key in (*TOOL_WIDTH_KEYS.values(), "arrow_style", "rect_style", "ellipse_style",
                        "marker_opacity", "font", "font_size", "text_alignment", "line_spacing",
                        "mosaic_mode", "mosaic_size", "annotation_tool"):
                self.assertIn(key, page.controls)
            self.assertIn("pen_color", page.color_buttons)
            for index, (tool, key) in enumerate(TOOL_WIDTH_KEYS.items(), 7):
                editor.toolbar.tool_buttons[tool].click()
                editor.toolbar.pen_width.setValue(index)
                self.assertEqual(manager.data[key], index)
            editor.toolbar.font_size.setValue(31)
            editor.toolbar.tool_buttons["arrow"].click()
            editor.toolbar.choice_buttons["arrow_style"]["open"].click()
            editor.toolbar.choice_buttons["arrow_style"]["double_filled"].click()
            self.assertEqual(manager.data["arrow_style"], "double_filled")
            editor.toolbar.choice_buttons["arrow_style"]["double_open"].click()
            self.assertEqual(manager.data["arrow_style"], "double_open")
            editor.toolbar.tool_buttons["text"].click()
            editor.toolbar.choice_buttons["text_alignment"]["center"].click()
            editor.toolbar.tool_buttons["mosaic"].click()
            self.assertEqual(manager.data["text_alignment"], "center")
            settings.flush_persist()
            restored = ConfigManager(manager.path).data
            for index, key in enumerate(TOOL_WIDTH_KEYS.values(), 7):
                self.assertEqual(restored[key], index)
            self.assertEqual(restored["arrow_style"], "double_open")
            self.assertEqual(restored["text_alignment"], "center")
            self.assertEqual(restored["font_size"], 31)
            editor.close()
            settings.close()

    def test_arrow_has_filled_head_and_round_stroke(self):
        from PySide6.QtCore import QPointF
        arrow = shape("arrow", QPointF(10, 10), QPointF(90, 10), "#ff5252", 5)
        self.assertEqual(arrow.brush().color().name(), "#ff5252")
        self.assertEqual(arrow.pen().capStyle(), Qt.RoundCap)
        self.assertTrue(arrow.path().contains(QPointF(80, 10)))

    def test_arrow_styles_include_filled_open_and_double(self):
        from PySide6.QtCore import QPointF
        from config.config_manager import validate

        start, end = QPointF(10, 10), QPointF(90, 10)
        filled = shape("arrow", start, end, "#ff0000", 4, "filled")
        open_arrow = shape("arrow", start, end, "#ff0000", 4, "open")
        double_open = shape("arrow", start, end, "#ff0000", 4, "double_open")
        double_filled = shape("arrow", start, end, "#ff0000", 4, "double_filled")
        line = shape("arrow", start, end, "#ff0000", 4, "line")
        rect_open = shape("arrow", start, end, "#ff0000", 4, "rect_open")
        dashed_line = shape("arrow", start, end, "#ff0000", 4, "dashed_line")
        rect_dashed = shape("arrow", start, end, "#ff0000", 4, "rect_dashed")
        self.assertEqual(filled.brush().color().name(), "#ff0000")
        self.assertEqual(open_arrow.brush().style(), Qt.NoBrush)
        self.assertEqual(double_open.brush().style(), Qt.NoBrush)
        self.assertEqual(double_filled.brush().color().name(), "#ff0000")
        self.assertEqual(double_open.path(), double_filled.path())
        self.assertEqual(line.path().elementCount(), 2)
        self.assertGreater(rect_open.path().elementCount(), line.path().elementCount())
        self.assertEqual(dashed_line.pen().style(), Qt.DashLine)
        self.assertEqual(rect_dashed.pen().style(), Qt.DashLine)
        self.assertEqual(rect_dashed.brush().style(), Qt.NoBrush)
        shaft_end = open_arrow.path().elementAt(1)
        self.assertLess(shaft_end.x, end.x())
        self.assertGreater(double_open.path().elementCount(), open_arrow.path().elementCount())
        for arrow in (filled, open_arrow):
            head = arrow.path().elementAt(2)
            self.assertGreaterEqual(end.x() - head.x, 28)
            self.assertGreaterEqual(head.y - end.y(), 10)
        double_head = double_open.path().elementAt(1)
        self.assertGreaterEqual(double_head.x - start.x(), 24)
        self.assertGreaterEqual(double_head.y - start.y(), 10)
        angled = shape("arrow", QPointF(10, 10), QPointF(70, 50), "#ff0000", 4, "filled")
        self.assertGreater(angled.path().boundingRect().height(), 20)
        first = angled.path().elementAt(0)
        self.assertEqual(angled.path().currentPosition(), QPointF(first.x, first.y))
        self.assertEqual(validate({"arrow_style": "open"})["arrow_style"], "open")
        self.assertEqual(validate({"arrow_style": "double_filled"})["arrow_style"], "double_filled")
        self.assertEqual(validate({"arrow_style": "double_open"})["arrow_style"], "double_open")
        self.assertEqual(validate({"arrow_style": "line"})["arrow_style"], "line")
        self.assertEqual(validate({"arrow_style": "rect_dashed"})["arrow_style"], "rect_dashed")
        with self.assertRaises(ValueError):
            validate({"arrow_style": "dotted"})

    def test_rect_and_ellipse_support_solid_and_dash_styles(self):
        from PySide6.QtCore import QPointF
        from config.config_manager import validate

        start, end = QPointF(10, 10), QPointF(60, 40)
        rect_solid = shape("rect", start, end, "#ff0000", 3, "solid")
        rect_dash = shape("rect", start, end, "#ff0000", 3, "dash")
        ellipse_dash = shape("ellipse", start, end, "#ff0000", 3, "dash")
        self.assertEqual(rect_solid.pen().style(), Qt.SolidLine)
        self.assertEqual(rect_dash.pen().style(), Qt.DashLine)
        self.assertEqual(ellipse_dash.pen().style(), Qt.DashLine)
        self.assertEqual(validate({"rect_style": "dash"})["rect_style"], "dash")
        self.assertEqual(validate({"ellipse_style": "solid"})["ellipse_style"], "solid")
        with self.assertRaises(ValueError):
            validate({"rect_style": "dot"})

    def test_arrow_chain_draws_connected_segments_and_right_click_finishes(self):
        from editor.annotation_canvas import AnnotationCanvas
        from PIL import Image
        from PySide6.QtCore import Qt, QPointF
        from PySide6.QtTest import QTest
        from config.config_manager import DEFAULTS

        settings = dict(DEFAULTS)
        settings["arrow_chain"] = True
        canvas = AnnotationCanvas(Image.new("RGB", (300, 200), "white"), settings)
        canvas.resize(400, 300)
        canvas.show()
        canvas.set_tool("arrow")
        self.app.processEvents()

        def drag(start, end):
            pa = canvas.mapFromScene(QPointF(*start))
            pb = canvas.mapFromScene(QPointF(*end))
            QTest.mousePress(canvas.viewport(), Qt.LeftButton, pos=pa)
            QTest.mouseMove(canvas.viewport(), pos=pb)
            QTest.mouseRelease(canvas.viewport(), Qt.LeftButton, pos=pb)

        # 第一段：从 (20,20) 拖到 (80,60)。
        drag((20, 20), (80, 60))
        self.assertEqual(len(canvas.annotations()), 1)
        self.assertTrue(canvas.chain_active)
        self.assertEqual(canvas.start, QPointF(80, 60))  # 锚点停在上一终点

        # 第二段：复用 (80,60) 为起点继续到 (150,120)。
        drag((150, 120), (150, 120))
        self.assertEqual(len(canvas.annotations()), 2)
        self.assertTrue(canvas.chain_active)
        self.assertEqual(canvas.start, QPointF(150, 120))

        # 箭头路径为闭合三角形，elementAt(0) 即本段起点；验证存在从 (20,20) 与从 (80,60) 起步的两段，
        # 后者起点恰为第一段终点，证明多段相连。
        starts = [QPointF(a.path().elementAt(0).x, a.path().elementAt(0).y) for a in canvas.annotations()]
        self.assertIn(QPointF(20, 20), starts)
        self.assertIn(QPointF(80, 60), starts)

        # 右键结束连续绘制，保留已画图形并复位链状态。
        pr = canvas.mapFromScene(QPointF(200, 200))
        QTest.mousePress(canvas.viewport(), Qt.RightButton, pos=pr)
        self.assertFalse(canvas.chain_active)
        self.assertIsNone(canvas.start)
        self.assertEqual(len(canvas.annotations()), 2)
        canvas.close()

    def test_arrow_single_shot_without_chain(self):
        from editor.annotation_canvas import AnnotationCanvas
        from PIL import Image
        from PySide6.QtCore import Qt, QPointF
        from PySide6.QtTest import QTest
        from config.config_manager import DEFAULTS

        canvas = AnnotationCanvas(Image.new("RGB", (300, 200), "white"), DEFAULTS)
        canvas.resize(400, 300)
        canvas.show()
        canvas.set_tool("arrow")
        self.app.processEvents()
        pa = canvas.mapFromScene(QPointF(20, 20))
        pb = canvas.mapFromScene(QPointF(80, 60))
        QTest.mousePress(canvas.viewport(), Qt.LeftButton, pos=pa)
        QTest.mouseMove(canvas.viewport(), pos=pb)
        QTest.mouseRelease(canvas.viewport(), Qt.LeftButton, pos=pb)
        self.assertEqual(len(canvas.annotations()), 1)
        self.assertFalse(canvas.chain_active)
        self.assertIsNone(canvas.start)
        canvas.close()

    def test_pen_straight_line_with_ctrl_alt_is_single_segment(self):
        from editor.annotation_canvas import AnnotationCanvas
        from PIL import Image
        from PySide6.QtCore import Qt, QPointF
        from config.config_manager import DEFAULTS

        canvas = AnnotationCanvas(Image.new("RGB", (300, 200), "white"), dict(DEFAULTS))
        canvas.resize(400, 300)
        canvas.show()
        canvas.set_tool("pen")
        self.app.processEvents()
        self._pen_drag(canvas, (20, 20), (80, 60), Qt.ControlModifier | Qt.AltModifier)
        self.assertEqual(len(canvas.annotations()), 1)
        item = canvas.annotations()[0]
        # 直线只含 moveTo + lineTo 两个节点，而非自由手绘的连续轨迹。
        self.assertEqual(item.path().elementCount(), 2)
        self.assertEqual(QPointF(item.path().elementAt(0).x, item.path().elementAt(0).y), QPointF(20, 20))
        self.assertFalse(canvas.chain_active)
        self.assertIsNone(canvas.start)
        canvas.close()

    def test_pen_and_marker_straight_line_with_ctrl_or_alt_alone(self):
        """按住 Ctrl 或 Alt 任意一个就应画直线（需求是「或」，不是「同时」）。"""
        from editor.annotation_canvas import AnnotationCanvas
        from PIL import Image
        from PySide6.QtCore import Qt, QPointF
        from config.config_manager import DEFAULTS

        for tool in ("pen", "marker"):
            for modifier in (Qt.ControlModifier, Qt.AltModifier):
                canvas = AnnotationCanvas(Image.new("RGB", (300, 200), "white"), dict(DEFAULTS))
                canvas.resize(400, 300)
                canvas.show()
                canvas.set_tool(tool)
                self.app.processEvents()
                self._pen_drag(canvas, (20, 20), (80, 60), modifier)
                self.assertEqual(len(canvas.annotations()), 1, (tool, modifier))
                item = canvas.annotations()[0]
                self.assertEqual(item.path().elementCount(), 2, (tool, modifier))
                self.assertEqual(
                    QPointF(item.path().elementAt(0).x, item.path().elementAt(0).y),
                    QPointF(20, 20), (tool, modifier))
                canvas.close()

    def test_space_is_canvas_pan_not_drawing_constraint(self):
        from editor.annotation_canvas import AnnotationCanvas
        from PIL import Image
        from PySide6.QtCore import QEvent, QPointF, Qt
        from PySide6.QtGui import QKeyEvent, QMouseEvent
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QGraphicsView
        from config.config_manager import DEFAULTS

        for index, tool in enumerate(("pen", "marker", "mosaic", "rect", "ellipse")):
            canvas = AnnotationCanvas(Image.new("RGB", (300, 200), "white"),
                                      dict(DEFAULTS))
            canvas.resize(160, 120)
            canvas.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
            canvas.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
            canvas.setSceneRect(0, 0, 600, 400)
            canvas.show()
            canvas.set_tool(tool)
            self.app.processEvents()
            canvas.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Space,
                                           Qt.NoModifier))
            with self.subTest(tool=tool):
                self.assertEqual(canvas.dragMode(), QGraphicsView.ScrollHandDrag)
                self.assertFalse(canvas._straight_gesture_active())
                self.assertFalse(canvas._shape_constraint_active())
            self.assertGreater(canvas.horizontalScrollBar().maximum(), 0)
            self.assertGreater(canvas.verticalScrollBar().maximum(), 0)
            start = canvas.mapFromScene(QPointF(85, 70))
            finish = canvas.mapFromScene(QPointF(60, 50))
            before = (canvas.horizontalScrollBar().value(),
                      canvas.verticalScrollBar().value())
            QTest.mousePress(canvas.viewport(), Qt.LeftButton, pos=start)
            self.app.sendEvent(canvas.viewport(), QMouseEvent(
                QEvent.MouseMove, QPointF(finish),
                QPointF(canvas.viewport().mapToGlobal(finish)),
                Qt.NoButton, Qt.LeftButton, Qt.NoModifier))
            after_drag = (canvas.horizontalScrollBar().value(),
                          canvas.verticalScrollBar().value())
            self.assertTrue(canvas.space_pan_active)
            if index == 0:
                canvas.keyReleaseEvent(QKeyEvent(QEvent.KeyRelease, Qt.Key_Space,
                                                 Qt.NoModifier))
                self.assertEqual(canvas.dragMode(), QGraphicsView.ScrollHandDrag)
            QTest.mouseRelease(canvas.viewport(), Qt.LeftButton, pos=finish)
            if index != 0:
                canvas.keyReleaseEvent(QKeyEvent(QEvent.KeyRelease, Qt.Key_Space,
                                                 Qt.NoModifier))
            with self.subTest(tool=tool):
                self.assertNotEqual(after_drag, before)
                self.assertEqual(canvas.annotations(), [])
                self.assertFalse(canvas.space_pan_active)
                self.assertEqual(canvas.dragMode(), QGraphicsView.NoDrag)
            canvas.close()

    def test_pen_straight_line_when_modifier_pressed_after_start(self):
        """鼠标按下之后才按住 Ctrl，拖动途中也应切换为直线（修「前几次触发不了」的时序问题）。"""
        from editor.annotation_canvas import AnnotationCanvas
        from PIL import Image
        from PySide6.QtCore import QEvent, QPointF, Qt
        from PySide6.QtGui import QMouseEvent
        from config.config_manager import DEFAULTS

        canvas = AnnotationCanvas(Image.new("RGB", (300, 200), "white"), dict(DEFAULTS))
        canvas.resize(400, 300)
        canvas.show()
        canvas.set_tool("pen")
        self.app.processEvents()

        def send(kind, scene_point, modifiers, buttons=Qt.NoButton):
            point = canvas.mapFromScene(QPointF(*scene_point))
            self.app.sendEvent(canvas.viewport(), QMouseEvent(
                kind, QPointF(point), QPointF(canvas.viewport().mapToGlobal(point)),
                Qt.LeftButton, buttons, modifiers))

        with patch("editor.annotation_canvas.QApplication.keyboardModifiers",
                   return_value=Qt.NoModifier):
            send(QEvent.MouseButtonPress, (20, 20), Qt.NoModifier, Qt.LeftButton)
            self.assertFalse(canvas.straight_drawing)
            send(QEvent.MouseMove, (80, 60), Qt.ControlModifier, Qt.LeftButton)
            self.assertTrue(canvas.straight_drawing)
            send(QEvent.MouseButtonRelease, (80, 60), Qt.ControlModifier)
        self.assertEqual(len(canvas.annotations()), 1)
        self.assertEqual(canvas.annotations()[0].path().elementCount(), 2)
        canvas.close()

    def test_pen_without_modifier_stays_freehand(self):
        """不按 Ctrl/Alt 且多段绘制关闭时，仍是自由手绘（节点多于直线）。"""
        from editor.annotation_canvas import AnnotationCanvas
        from PIL import Image
        from PySide6.QtCore import QPointF
        from PySide6.QtTest import QTest
        from PySide6.QtCore import Qt
        from config.config_manager import DEFAULTS

        canvas = AnnotationCanvas(Image.new("RGB", (300, 200), "white"), dict(DEFAULTS))
        canvas.resize(400, 300)
        canvas.show()
        canvas.set_tool("pen")
        self.app.processEvents()
        QTest.mousePress(canvas.viewport(), Qt.LeftButton,
                         pos=canvas.mapFromScene(QPointF(20, 20)))
        QTest.mouseMove(canvas.viewport(), canvas.mapFromScene(QPointF(40, 30)))
        QTest.mouseMove(canvas.viewport(), canvas.mapFromScene(QPointF(60, 45)))
        QTest.mouseMove(canvas.viewport(), canvas.mapFromScene(QPointF(80, 60)))
        QTest.mouseRelease(canvas.viewport(), Qt.LeftButton,
                           pos=canvas.mapFromScene(QPointF(80, 60)))
        self.assertEqual(len(canvas.annotations()), 1)
        self.assertGreater(canvas.annotations()[0].path().elementCount(), 2)
        canvas.close()

    def test_rect_and_ellipse_draw_square_with_modifier(self):
        from editor.annotation_canvas import AnnotationCanvas
        from PIL import Image
        from PySide6.QtCore import QEvent, QPointF, Qt
        from PySide6.QtGui import QMouseEvent
        from config.config_manager import DEFAULTS

        for tool, modifier in (("rect", Qt.ControlModifier),
                               ("ellipse", Qt.AltModifier)):
            canvas = AnnotationCanvas(Image.new("RGB", (300, 200), "white"),
                                      dict(DEFAULTS))
            canvas.resize(400, 300)
            canvas.show()
            canvas.set_tool(tool)
            self.app.processEvents()

            def send(kind, scene_point, modifiers, buttons):
                point = canvas.mapFromScene(QPointF(*scene_point))
                canvas_point = QPointF(point)
                self.app.sendEvent(canvas.viewport(), QMouseEvent(
                    kind, canvas_point,
                    QPointF(canvas.viewport().mapToGlobal(point)),
                    Qt.LeftButton, buttons, modifiers))

            try:
                with patch("editor.annotation_canvas.QApplication.keyboardModifiers",
                           return_value=Qt.NoModifier):
                    send(QEvent.MouseButtonPress, (40, 40), Qt.NoModifier, Qt.LeftButton)
                    send(QEvent.MouseMove, (100, 70), modifier, Qt.LeftButton)
                    self.assertAlmostEqual(canvas.preview_end.x() - canvas.start.x(),
                                           canvas.preview_end.y() - canvas.start.y())
                    send(QEvent.MouseButtonRelease, (100, 70), modifier, Qt.NoButton)
                    bounds = canvas.annotations()[0].boundingRect()
                    self.assertAlmostEqual(bounds.width(), bounds.height(), places=4)
            finally:
                canvas.close()

    def test_pen_straight_line_without_second_point_draws_nothing(self):
        from editor.annotation_canvas import AnnotationCanvas
        from PIL import Image
        from PySide6.QtCore import Qt
        from config.config_manager import DEFAULTS

        canvas = AnnotationCanvas(Image.new("RGB", (300, 200), "white"), dict(DEFAULTS))
        canvas.resize(400, 300)
        canvas.show()
        canvas.set_tool("pen")
        self.app.processEvents()
        # 按住 Ctrl+Alt 但按下后未拖出第二点（原地松开），不应产生痕迹。
        self._pen_drag(canvas, (40, 40), (40, 40), Qt.ControlModifier | Qt.AltModifier)
        self.assertEqual(len(canvas.annotations()), 0)
        self.assertFalse(canvas.chain_active)
        canvas.close()

    def test_pen_freehand_isolated_click_draws_nothing(self):
        from editor.annotation_canvas import AnnotationCanvas
        from PIL import Image
        from config.config_manager import DEFAULTS

        canvas = AnnotationCanvas(Image.new("RGB", (300, 200), "white"), dict(DEFAULTS))
        canvas.resize(400, 300)
        canvas.show()
        canvas.set_tool("pen")
        self.app.processEvents()
        # 自由画笔单击无拖动，同样不留下孤立点。
        self._pen_drag(canvas, (40, 40), (40, 40))
        self.assertEqual(len(canvas.annotations()), 0)
        canvas.close()

    def test_pen_freehand_is_smoothed_on_commit(self):
        from editor.annotation_canvas import AnnotationCanvas
        from PIL import Image
        from PySide6.QtCore import Qt, QPointF
        from PySide6.QtTest import QTest
        from config.config_manager import DEFAULTS

        canvas = AnnotationCanvas(Image.new("RGB", (300, 200), "white"), dict(DEFAULTS))
        canvas.resize(400, 300)
        canvas.show()
        canvas.set_tool("pen")
        self.app.processEvents()
        # 多次鼠标移动画一条弯曲轨迹（非直线、非 Ctrl+Alt）。
        pa = canvas.mapFromScene(QPointF(20, 100))
        pb = canvas.mapFromScene(QPointF(60, 40))
        pc = canvas.mapFromScene(QPointF(100, 120))
        pd = canvas.mapFromScene(QPointF(140, 60))
        QTest.mousePress(canvas.viewport(), Qt.LeftButton, pos=pa)
        QTest.mouseMove(canvas.viewport(), pb)
        QTest.mouseMove(canvas.viewport(), pc)
        QTest.mouseMove(canvas.viewport(), pd)
        QTest.mouseRelease(canvas.viewport(), Qt.LeftButton, pos=pd)
        self.assertEqual(len(canvas.annotations()), 1)
        item = canvas.annotations()[0]
        # 平滑后路径含曲线元素（多于直线的两节点），且起止点保持不变。
        self.assertGreater(item.path().elementCount(), 2)
        self.assertAlmostEqual(item.path().elementAt(0).x, 20, delta=1)
        self.assertAlmostEqual(item.path().elementAt(0).y, 100, delta=1)
        last = item.path().elementAt(item.path().elementCount() - 1)
        self.assertAlmostEqual(last.x, 140, delta=1)
        self.assertAlmostEqual(last.y, 60, delta=1)
        canvas.close()

    def test_pen_chain_draws_connected_straight_segments(self):
        from editor.annotation_canvas import AnnotationCanvas
        from PIL import Image
        from PySide6.QtCore import Qt, QPointF
        from PySide6.QtTest import QTest
        from config.config_manager import DEFAULTS

        settings = dict(DEFAULTS)
        settings["pen_chain"] = True
        canvas = AnnotationCanvas(Image.new("RGB", (300, 200), "white"), settings)
        canvas.resize(400, 300)
        canvas.show()
        canvas.set_tool("pen")
        self.app.processEvents()
        self._pen_drag(canvas, (20, 20), (80, 60))
        self.assertEqual(len(canvas.annotations()), 1)
        self.assertTrue(canvas.chain_active)
        self.assertEqual(canvas.start, QPointF(80, 60))  # 锚点停在上一终点
        # 第二段复用上一终点继续到 (150,120)，且每段都是直线（两节点）。
        self._pen_drag(canvas, (150, 120), (150, 120))
        self.assertEqual(len(canvas.annotations()), 2)
        self.assertEqual(canvas.annotations()[1].path().elementCount(), 2)
        starts = [QPointF(a.path().elementAt(0).x, a.path().elementAt(0).y) for a in canvas.annotations()]
        self.assertIn(QPointF(20, 20), starts)
        self.assertIn(QPointF(80, 60), starts)
        # 右键结束连续绘制，保留已画图形并复位链状态。
        pr = canvas.mapFromScene(QPointF(200, 200))
        QTest.mousePress(canvas.viewport(), Qt.RightButton, pos=pr)
        self.assertFalse(canvas.chain_active)
        self.assertIsNone(canvas.start)
        self.assertEqual(len(canvas.annotations()), 2)
        canvas.close()

    def test_marker_chain_draws_connected_straight_segments(self):
        from editor.annotation_canvas import AnnotationCanvas
        from PIL import Image
        from PySide6.QtCore import Qt, QPointF
        from config.config_manager import DEFAULTS

        settings = dict(DEFAULTS)
        settings["marker_chain"] = True
        canvas = AnnotationCanvas(Image.new("RGB", (300, 200), "white"), settings)
        canvas.resize(400, 300)
        canvas.show()
        canvas.set_tool("marker")
        self.app.processEvents()
        self._pen_drag(canvas, (20, 20), (80, 60))
        self.assertEqual(len(canvas.annotations()), 1)
        self.assertTrue(canvas.chain_active)
        self._pen_drag(canvas, (150, 120), (150, 120))
        self.assertEqual(len(canvas.annotations()), 2)
        self.assertEqual(canvas.annotations()[0].path().elementCount(), 2)
        canvas.close()

    def test_arrow_outline_keeps_clear_shaft_and_sloped_shoulders(self):
        from math import atan2, degrees
        from PySide6.QtCore import QPointF
        from PySide6.QtGui import QPainterPathStroker

        start, end = QPointF(10, 10), QPointF(170, 10)
        for style in ("filled", "open", "double_open", "double_filled"):
            for width in (1, 4, 12, 24):
                arrow = shape("arrow", start, end, "#ff0000", width, style)
                path = arrow.path()
                stroker = QPainterPathStroker()
                stroker.setWidth(width)
                if style in ("open", "double_open"):
                    self.assertFalse(stroker.createStroke(path).contains(QPointF(90, 10)),
                                     (style, width))
                double_headed = style in ("double_open", "double_filled")
                inner = path.elementAt(2 if double_headed else 1)
                outer = path.elementAt(1 if double_headed else 2)
                tip = path.elementAt(0 if double_headed else 3)
                tip_angle = 2 * degrees(atan2(abs(outer.y - tip.y),
                                              abs(outer.x - tip.x)))
                self.assertGreater(tip_angle, 65, (style, width))
                self.assertLess(tip_angle, 75, (style, width))
                if double_headed:
                    self.assertLess(inner.x, outer.x)
                else:
                    self.assertGreater(inner.x, outer.x)
                self.assertLess(inner.y, outer.y)
                angle = degrees(atan2(outer.y - inner.y, abs(inner.x - outer.x)))
                self.assertLess(angle, 90, (style, width))
                head_length = (outer.x - start.x()) if double_headed else (end.x() - outer.x)
                if double_headed:
                    self.assertAlmostEqual(outer.x - inner.x, head_length * 0.25, delta=0.01)
                else:
                    self.assertLessEqual(inner.x - outer.x, head_length * 0.22 + 0.01)
                    if width == 4:
                        self.assertGreater(inner.x - outer.x, head_length * 0.18)
                if double_headed:
                    other_outer, other_inner = path.elementAt(4), path.elementAt(3)
                    other_tip = path.elementAt(5)
                    other_tip_angle = 2 * degrees(atan2(abs(other_outer.y - other_tip.y),
                                                        abs(other_tip.x - other_outer.x)))
                    self.assertGreater(other_tip_angle, 65, (style, width))
                    self.assertLess(other_tip_angle, 75, (style, width))
                    self.assertGreater(other_inner.x, other_outer.x)
                    other_angle = degrees(atan2(other_outer.y - other_inner.y,
                                                other_inner.x - other_outer.x))
                    self.assertLess(other_angle, 90, width)
                    self.assertAlmostEqual(other_inner.x - other_outer.x,
                                           head_length * 0.25, delta=0.01)
                self.assertGreater(outer.y - start.y(), 13 if width == 4 else 0)

    def test_short_wide_double_arrows_keep_both_tips_sharp(self):
        from math import atan2, degrees
        from PySide6.QtCore import QPointF
        from PySide6.QtGui import QPainterPathStroker

        for style in ("double_open", "double_filled"):
            arrow = shape("arrow", QPointF(10, 10), QPointF(90, 10), "#ff0000", 24, style)
            path = arrow.path()
            self.assertLess(path.elementAt(2).x, path.elementAt(1).x)
            self.assertGreater(path.elementAt(3).x, path.elementAt(4).x)
            for tip, shoulder in ((0, 1), (5, 4)):
                point, base = path.elementAt(tip), path.elementAt(shoulder)
                angle = 2 * degrees(atan2(abs(base.y - point.y), abs(base.x - point.x)))
                self.assertGreater(angle, 65, style)
                self.assertLess(angle, 75, style)
            if style == "double_open":
                stroker = QPainterPathStroker()
                stroker.setWidth(24)
                self.assertFalse(stroker.createStroke(path).contains(QPointF(50, 10)))

    def test_exported_diagonal_arrows_have_antialiased_edges(self):
        from config.config_manager import DEFAULTS

        for style in ("open", "double_open", "double_filled"):
            canvas = AnnotationCanvas(Image.new("RGB", (120, 100), "white"), dict(DEFAULTS))
            canvas.scene_data.addItem(shape("arrow", QPointF(12, 18), QPointF(104, 69),
                                            "#ff0000", 3, style))
            image = canvas.render_image()
            self.assertTrue(any(0 < image.pixelColor(x, y).green() < 255
                                for y in range(10, 80) for x in range(8, 110)), style)
            canvas.close()

    def test_other_vector_annotations_have_antialiased_edges(self):
        from config.config_manager import DEFAULTS

        for tool in ("rect", "ellipse", "pen"):
            canvas = AnnotationCanvas(Image.new("RGB", (120, 100), "white"), dict(DEFAULTS))
            canvas.scene_data.addItem(shape(tool, QPointF(12, 18), QPointF(104, 69),
                                            "#ff0000", 3))
            image = canvas.render_image()
            self.assertTrue(any(0 < image.pixelColor(x, y).green() < 255
                                for y in range(10, 80) for x in range(8, 110)), tool)
            canvas.close()

    def test_inline_border_drag_recrops_and_keeps_canvas_inside_unhandled(self):
        from PySide6.QtCore import QEvent, QPointF
        from PySide6.QtGui import QMouseEvent
        from config.config_manager import DEFAULTS
        from screenshot.mask_window import MaskWindow

        with tempfile.TemporaryDirectory() as folder:
            bounds = {"left": 0, "top": 0, "width": 320, "height": 240}
            settings = {**DEFAULTS, "save_dir": folder, "inline_edit": True, "magnifier": False}
            settings["capture_after_selection"] = "edit"
            image = Image.new("RGB", (320, 240), "blue")
            image.paste("red", (35, 35, 155, 125))
            with patch("screenshot.mask_window.visible_windows", return_value=[]):
                mask = MaskWindow(image, bounds, [bounds], settings)
            mask.selection.rects.append(QRect(30, 30, 100, 80))
            mask.complete()
            editor = mask.session.inline_editor
            viewport = editor.canvas.viewport()
            inside = QPoint(50, 50)
            event = QMouseEvent(QEvent.MouseButtonPress, QPointF(inside), QPointF(inside),
                                Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
            self.assertFalse(editor.eventFilter(viewport, event))
            start = QPoint(58, 30)
            QTest.mousePress(mask, Qt.LeftButton, pos=start)
            self.assertEqual(mask.selection.dragging, 0)
            QTest.mouseMove(mask, start + QPoint(15, 8))
            QTest.mouseRelease(mask, Qt.LeftButton, pos=start + QPoint(15, 8))
            self.assertEqual(mask.selection.rects, [QRect(45, 38, 100, 80)])
            self.assertEqual(editor.canvas.image.getpixel((0, 0)), (255, 0, 0))
            self.assertEqual(editor.canvas.geometry(), mask.to_logical_rect(mask.selection.rects[0]).toRect())
            border = viewport.mapFrom(mask, QPoint(73, 39))
            moved = viewport.mapFrom(mask, QPoint(83, 45))
            press = QMouseEvent(QEvent.MouseButtonPress, QPointF(border), QPointF(border),
                                Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
            self.assertTrue(editor.eventFilter(viewport, press))
            move = QMouseEvent(QEvent.MouseMove, QPointF(moved), QPointF(moved),
                               Qt.NoButton, Qt.LeftButton, Qt.NoModifier)
            self.assertTrue(editor.eventFilter(viewport, move))
            release = QMouseEvent(QEvent.MouseButtonRelease, QPointF(moved), QPointF(moved),
                                  Qt.LeftButton, Qt.NoButton, Qt.NoModifier)
            self.assertTrue(editor.eventFilter(viewport, release))
            self.assertEqual(mask.selection.rects, [QRect(55, 44, 100, 80)])
            mask.close()

    def test_annotation_sequence_supports_all_shapes(self):
        from editor.annotation_items import AnnotationSequenceItem
        from PySide6.QtGui import QImage, QPainter
        for shape in ("circle", "square", "triangle", "diamond",
                      "pentagon", "hexagon", "star"):
            item = AnnotationSequenceItem(3, "#168cff", "#ffffff", 16, shape, "")
            rect = item.boundingRect()
            self.assertAlmostEqual(rect.width(), rect.height())
            image = QImage(40, 40, QImage.Format_ARGB32)
            image.fill(0)
            painter = QPainter(image)
            item.paint(painter, None, None)
            painter.end()

    def test_inline_editor_double_click_outside_canvas_saves(self):
        from config.config_manager import DEFAULTS
        from screenshot.mask_window import MaskWindow

        with tempfile.TemporaryDirectory() as folder:
            bounds = {"left": 0, "top": 0, "width": 140, "height": 100}
            settings = {**DEFAULTS, "save_dir": folder,
                        "archive_by_month": False, "inline_edit": True,
                        "capture_after_selection": "edit", "magnifier": False,
                        "crosshair": False, "capture_hint_order": [], "sound": False}
            with patch("screenshot.mask_window.visible_windows", return_value=[]):
                mask = MaskWindow(Image.new("RGB", (140, 100), "white"), bounds, [bounds], settings)
            mask.selection.rects.append(QRect(10, 10, 40, 30))
            mask.complete()
            editor = mask.session.inline_editor
            self.assertIsNotNone(editor)
            mask.show()
            self.app.processEvents()
            QTest.mouseDClick(mask, Qt.LeftButton, Qt.NoModifier, QPoint(120, 80))
            self.assertFalse(mask.isVisible())
            self.assertIsNone(mask.session.inline_editor)
            self.assertTrue(list(Path(folder).glob("*.png")))

    def test_inline_first_save_failure_keeps_editor_open_for_retry(self):
        from config.config_manager import DEFAULTS
        from screenshot.mask_window import MaskWindow

        with tempfile.TemporaryDirectory() as folder:
            blocked = Path(folder) / "blocked"
            blocked.write_text("not a directory")
            bounds = {"left": 0, "top": 0, "width": 120, "height": 80}
            settings = {**DEFAULTS, "save_dir": str(blocked), "filename": "inline",
                        "inline_edit": True, "magnifier": False}
            settings["capture_after_selection"] = "edit"
            with patch("screenshot.mask_window.visible_windows", return_value=[]):
                mask = MaskWindow(Image.new("RGB", (120, 80), "blue"), bounds, [bounds], settings)
            errors = []
            mask.save_failed.connect(errors.append)
            mask.selection.rects.append(QRect(10, 12, 30, 20))
            mask.complete()
            self.app.processEvents()
            # 进入编辑器不写盘（AGENTS 第 11 条）：此时不该有任何保存动作或失败。
            self.assertEqual(errors, [])
            self.assertIsNotNone(mask.session.inline_editor)
            self.assertFalse(mask.session.completing)
            # 显式保存到不可写目录时才走失败分支，且编辑器必须保持打开以便重试。
            try:
                mask.session.inline_editor.save(automatic=True)
            except OSError as error:  # 未走到 save_failed 时以异常形式报告，同样算失败分支
                errors.append(str(error))
            self.app.processEvents()
            self.assertTrue(errors)
            self.assertIn("blocked", errors[0])
            self.assertIsNotNone(mask.session.inline_editor)
            settings["save_dir"] = folder
            self.assertEqual(mask.session.inline_editor.save(automatic=True).name, "inline.png")
            mask.close()

    def test_inline_options_button_opens_after_keyboard_nudge(self):
        from PySide6.QtCore import QPoint, QTimer
        from PySide6.QtWidgets import QApplication, QWidget
        from config.config_manager import DEFAULTS
        from screenshot.mask_window import MaskWindow

        with tempfile.TemporaryDirectory() as folder:
            bounds = {"left": 0, "top": 0, "width": 1200, "height": 900}
            settings = {**DEFAULTS, "save_dir": folder, "inline_edit": True, "magnifier": False}
            settings["capture_after_selection"] = "edit"
            with patch("screenshot.mask_window.visible_windows", return_value=[]):
                mask = MaskWindow(Image.new("RGB", (1200, 900), "blue"), bounds, [bounds], settings)
            mask.selection.rects.append(QRect(40, 40, 300, 200))
            mask.complete()
            mask.show()
            editor = mask.session.inline_editor
            editor.canvas.setFocus()
            self.app.processEvents()
            editor.toolbar.tool_buttons["arrow"].click()
            # 契约（D17 起）：必须先【抓住控制点】再按键才会微调 ——
            # 旧契约「不抓点、方向键整体平移选区」已被守卫明确移除，这里按现行契约重写。
            viewport = editor.canvas.viewport()
            corner = mask.selection.rects[0].bottomRight()
            QTest.mousePress(viewport, Qt.LeftButton, pos=viewport.mapFrom(mask, corner))
            self.assertIsNotNone(mask.selection.resizing, "按在角手柄上应进入缩放抓取")
            with patch("screenshot.mask_window.QCursor.setPos"):
                QTest.keyClick(editor.canvas, Qt.Key_Right)
            self.assertEqual(mask.selection.rects[0], QRect(40, 40, 301, 200),
                             "抓住右下角后按 → ⇒ x 轴 +1px")
            QTest.mouseRelease(viewport, Qt.LeftButton, pos=viewport.mapFrom(mask, corner))
            button = editor.toolbar.options_button
            opened = []
            def capture_menu():
                opened.append(button.menu().isVisible())
                button.menu().hide()
            QTimer.singleShot(0, capture_menu)
            QTest.mouseClick(button, Qt.LeftButton)
            self.app.processEvents()
            self.assertEqual(opened, [True])
            corner = mask.selection.rects[0].bottomRight()
            QTest.mousePress(viewport, Qt.LeftButton, pos=viewport.mapFrom(mask, corner))
            with patch("screenshot.mask_window.QCursor.setPos"):
                QTest.keyClick(editor.canvas, Qt.Key_Right)
            self.assertEqual(mask.selection.rects[0], QRect(40, 40, 302, 200),
                             "再次抓住右下角按 → ⇒ x 轴再 +1px")
            QTest.mouseRelease(viewport, Qt.LeftButton, pos=viewport.mapFrom(mask, corner))
            QTimer.singleShot(0, capture_menu)
            QTest.mouseClick(button, Qt.LeftButton)
            self.app.processEvents()
            self.assertEqual(opened, [True, True])
            corner = mask.selection.rects[0].bottomRight()
            viewport = editor.canvas.viewport()
            with patch.object(mask, "grabMouse", wraps=mask.grabMouse) as grab_mouse, \
                    patch.object(mask, "releaseMouse", wraps=mask.releaseMouse) as release_mouse:
                QTest.mousePress(viewport, Qt.LeftButton, pos=viewport.mapFrom(mask, corner))
                self.assertIsNotNone(mask.selection.resizing)
                grab_mouse.assert_called_once()
                if self.app.platformName() == "windows":
                    self.assertIs(QWidget.mouseGrabber(), mask)
                with patch("screenshot.mask_window.QCursor.setPos"):
                    QTest.keyClick(editor.canvas, Qt.Key_Right)
                release_mouse.assert_called_once()
                if self.app.platformName() == "windows":
                    self.assertIsNot(QWidget.mouseGrabber(), mask)
            self.assertIsNone(mask.selection.resizing)
            QTimer.singleShot(0, capture_menu)
            QTest.mouseClick(button, Qt.LeftButton)
            self.app.processEvents()
            self.assertEqual(opened, [True, True, True])
            corner = mask.selection.rects[0].bottomRight()
            drag_to = corner + QPoint(-12, -8)
            with patch.object(mask, "grabMouse", wraps=mask.grabMouse) as grab_mouse, \
                    patch.object(mask, "releaseMouse", wraps=mask.releaseMouse) as release_mouse:
                QTest.mousePress(viewport, Qt.LeftButton, pos=viewport.mapFrom(mask, corner))
                QTest.mouseMove(viewport, viewport.mapFrom(mask, drag_to))
                QTest.mouseRelease(viewport, Qt.LeftButton, pos=viewport.mapFrom(mask, drag_to))
                grab_mouse.assert_called_once()
                release_mouse.assert_called_once()
            if self.app.platformName() == "windows":
                self.assertIsNot(QWidget.mouseGrabber(), mask)
            self.app.processEvents()
            hit_widget = QApplication.widgetAt(button.mapToGlobal(button.rect().center()))
            self.assertTrue(hit_widget is button or button.isAncestorOf(hit_widget))
            self.assertIsNone(mask.selection.resizing)
            QTimer.singleShot(0, capture_menu)
            QTest.mouseClick(button, Qt.LeftButton)
            self.app.processEvents()
            self.assertEqual(opened, [True, True, True, True])
            corner = mask.selection.rects[0].bottomRight()
            drag_to = corner + QPoint(-8, -6)
            QTest.mousePress(viewport, Qt.LeftButton, pos=viewport.mapFrom(mask, corner))
            QTest.mouseMove(viewport, viewport.mapFrom(mask, drag_to))
            QTest.mouseRelease(viewport, Qt.LeftButton, pos=viewport.mapFrom(mask, drag_to))
            if self.app.platformName() == "windows":
                self.assertIsNot(QWidget.mouseGrabber(), mask)
            self.app.processEvents()
            hit_widget = QApplication.widgetAt(button.mapToGlobal(button.rect().center()))
            self.assertTrue(hit_widget is button or button.isAncestorOf(hit_widget))
            QTimer.singleShot(0, capture_menu)
            QTest.mouseClick(button, Qt.LeftButton)
            self.app.processEvents()
            self.assertEqual(opened, [True, True, True, True, True])
            rect_before_move = QRect(mask.selection.rects[0])
            move_start = QPoint(rect_before_move.left() + 24, rect_before_move.top())
            move_end = move_start + QPoint(14, 9)
            QTest.mousePress(viewport, Qt.LeftButton,
                             pos=viewport.mapFrom(mask, move_start))
            QTest.mouseMove(viewport, viewport.mapFrom(mask, move_end))
            QTest.mouseRelease(viewport, Qt.LeftButton,
                               pos=viewport.mapFrom(mask, move_end))
            self.app.processEvents()
            self.assertNotEqual(mask.selection.rects[0], rect_before_move)
            hit_widget = QApplication.widgetAt(button.mapToGlobal(button.rect().center()))
            self.assertTrue(hit_widget is button or button.isAncestorOf(hit_widget))
            QTimer.singleShot(0, capture_menu)
            QTest.mouseClick(button, Qt.LeftButton)
            self.app.processEvents()
            self.assertEqual(opened, [True, True, True, True, True, True])
            rect_before_second_move = QRect(mask.selection.rects[0])
            move_start = QPoint(rect_before_second_move.left() + 24,
                                rect_before_second_move.top())
            move_end = move_start + QPoint(-11, 8)
            QTest.mousePress(viewport, Qt.LeftButton,
                             pos=viewport.mapFrom(mask, move_start))
            QTest.mouseMove(viewport, viewport.mapFrom(mask, move_end))
            QTest.mouseRelease(viewport, Qt.LeftButton,
                               pos=viewport.mapFrom(mask, move_end))
            self.app.processEvents()
            self.assertNotEqual(mask.selection.rects[0], rect_before_second_move)
            if self.app.platformName() == "windows":
                self.assertIsNot(QWidget.mouseGrabber(), mask)
            hit_widget = QApplication.widgetAt(button.mapToGlobal(button.rect().center()))
            self.assertTrue(hit_widget is button or button.isAncestorOf(hit_widget))
            QTimer.singleShot(0, capture_menu)
            QTest.mouseClick(button, Qt.LeftButton)
            self.app.processEvents()
            self.assertEqual(opened, [True, True, True, True, True, True, True])
            button_point = mask.to_physical_point(
                button.mapTo(mask, button.rect().center()))
            mask.selection.rects[0] = QRect(
                button_point.x() - 100, button_point.y(), 200, 120)
            QTimer.singleShot(0, capture_menu)
            QTest.mouseClick(button, Qt.LeftButton)
            self.app.processEvents()
            self.assertEqual(opened, [True, True, True, True, True, True, True, True])
            mask.close()

    def test_inline_editor_replaces_persisted_crop_tool(self):
        from config.config_manager import DEFAULTS
        from screenshot.mask_window import MaskWindow

        with tempfile.TemporaryDirectory() as folder:
            bounds = {"left": 0, "top": 0, "width": 240, "height": 180}
            settings = {**DEFAULTS, "save_dir": folder, "inline_edit": True,
                        "annotation_tool": "crop", "magnifier": False}
            settings["capture_after_selection"] = "edit"
            with patch("screenshot.mask_window.visible_windows", return_value=[]):
                mask = MaskWindow(Image.new("RGB", (240, 180), "blue"), bounds, [bounds], settings)
            mask.selection.rects.append(QRect(20, 20, 80, 60))
            mask.complete()
            editor = mask.session.inline_editor
            self.assertEqual(editor.canvas.tool, "select")
            self.assertTrue(editor.toolbar.tool_buttons["crop"].isHidden())
            self.assertTrue(editor.toolbar.tool_buttons["select"].isChecked())
            mask.close()

    def test_inline_editor_activates_the_persisted_annotation_tool(self):
        from PySide6.QtWidgets import QGraphicsView
        from config.config_manager import DEFAULTS
        from screenshot.mask_window import MaskWindow

        with tempfile.TemporaryDirectory() as folder:
            bounds = {"left": 0, "top": 0, "width": 240, "height": 180}
            settings = {**DEFAULTS, "save_dir": folder, "filename": "inline-arrow",
                        "inline_edit": True, "annotation_tool": "arrow", "magnifier": False,
                        "capture_after_selection": "edit",
                        "crosshair": False, "mask_opacity": 0, "bubble": False}
            with patch("screenshot.mask_window.visible_windows", return_value=[]):
                mask = MaskWindow(Image.new("RGB", (240, 180), "blue"), bounds, [bounds], settings)
            mask.selection.rects.append(QRect(20, 20, 80, 60))
            mask.complete()
            editor = mask.session.inline_editor

            self.assertTrue(editor.toolbar.tool_buttons["arrow"].isChecked())
            self.assertFalse(editor.toolbar.tool_buttons["select"].isChecked())
            self.assertEqual(editor.canvas.tool, "arrow")
            self.assertEqual(editor.canvas.dragMode(), QGraphicsView.NoDrag)
            mask.close()

    def test_inline_editor_color_picker_updates_last_annotation_tool(self):
        from config.config_manager import DEFAULTS
        from screenshot.mask_window import MaskWindow

        bounds = {"left": 0, "top": 0, "width": 100, "height": 80}
        settings = dict(DEFAULTS, inline_edit=True, magnifier=False,
                        crosshair=False, mask_opacity=0,
                        capture_after_selection="edit")
        mask = MaskWindow(Image.new("RGB", (100, 80), "white"), bounds,
                          [bounds], settings)
        mask.selection.rects.append(QRect(10, 10, 60, 50))
        mask.complete()
        editor = mask.session.inline_editor
        editor.set_tool("rect")
        editor.set_tool("select")
        editor.set_pen_color("#123456")

        self.assertEqual(settings["rect_color"], "#123456")
        self.assertEqual(settings["pen_color"], DEFAULTS["pen_color"])
        self.assertEqual(editor.toolbar.pen_color.color, "#123456")
        mask.close()

    def test_tool_color_buttons_are_independent_and_open_picker_in_both_editors(self):
        from PySide6.QtGui import QColor
        from PySide6.QtWidgets import QColorDialog
        from config.config_manager import DEFAULTS
        from editor.editor_window import EditorWindow
        from screenshot.mask_window import MaskWindow

        color_tools = (("pen_color", "pen"), ("marker_color", "marker"),
                   ("rect_color", "rect"), ("ellipse_color", "ellipse"),
                   ("text_color", "text"), ("arrow_color", "arrow"))
        color_keys = tuple(key for key, _ in color_tools)
        settings = dict(DEFAULTS)
        window_editor = EditorWindow(Image.new("RGB", (60, 40), "white"), settings)
        bounds = {"left": 0, "top": 0, "width": 100, "height": 80}
        with patch("screenshot.mask_window.visible_windows", return_value=[]):
            mask = MaskWindow(Image.new("RGB", (100, 80), "white"), bounds,
                              [bounds], dict(DEFAULTS, inline_edit=True,
                                             capture_after_selection="edit",
                                             magnifier=False, crosshair=False))
        try:
            mask.selection.rects.append(QRect(10, 10, 60, 50))
            mask.complete()
            inline_editor = mask.session.inline_editor
            for editor in (window_editor, inline_editor):
                self.assertEqual(set(editor.toolbar.tool_color_buttons), set(color_keys))
                for index, (key, tool) in enumerate(color_tools):
                    editor.toolbar.tool_buttons[tool].click()
                    button = editor.toolbar.tool_color_buttons[key]
                    self.assertFalse(button.isHidden(), key)
                    before = {name: editor.settings[name] for name in color_keys}
                    color = f"#{index + 1:06x}"
                    with patch("ui.widgets.color_button.color_dialog") as dialog_factory:
                        dialog_factory.return_value.exec.return_value = QColorDialog.Accepted
                        dialog_factory.return_value.currentColor.return_value = QColor(color)
                        button.clicked.emit()
                    self.assertEqual(dialog_factory.call_count, 1, key)
                    self.assertEqual(editor.settings[key], color, key)
                    self.assertEqual(editor.canvas.settings[key], color, key)
                    self.assertEqual(button.color, color, key)
                    for other_key in color_keys:
                        if other_key != key:
                            self.assertEqual(editor.settings[other_key], before[other_key])
                    self.assertEqual(editor.toolbar.tool_color_buttons["rect_color"].toolTip()
                                     .count("矩形边框颜色"), 1)

                rect_button = editor.toolbar.tool_color_buttons["rect_color"]
                editor.toolbar.tool_buttons["rect"].click()
                self.assertFalse(editor.toolbar.rect_fill_enabled.isHidden())
                self.assertTrue(editor.toolbar.ellipse_fill_enabled.isHidden())
                with patch("ui.widgets.color_button.color_dialog") as dialog_factory:
                    dialog_factory.return_value.exec.return_value = QColorDialog.Accepted
                    dialog_factory.return_value.currentColor.return_value = QColor("#654321")
                    editor.toolbar.pen_color.clicked.emit()
                self.assertEqual(editor.settings["rect_color"], "#654321")
                self.assertEqual(rect_button.color, "#654321")
                editor.toolbar.tool_buttons["ellipse"].click()
                self.assertTrue(editor.toolbar.rect_fill_enabled.isHidden())
                self.assertFalse(editor.toolbar.ellipse_fill_enabled.isHidden())
        finally:
            window_editor.close()
            mask.close()

    def test_toolbar_fill_color_controls_exist(self):
        from config.config_manager import DEFAULTS
        from editor.toolbar_widget import ToolbarWidget

        settings = dict(DEFAULTS)
        toolbar = ToolbarWidget(settings["pen_color"], settings, settings["annotation_tool"])
        self.assertTrue(hasattr(toolbar, "rect_fill_color"))
        self.assertTrue(hasattr(toolbar, "ellipse_fill_color"))
        toolbar.sync_setting("rect_fill_color", "#123456")
        toolbar.sync_setting("ellipse_fill_color", "#654321")

    def test_selected_arrow_style_can_be_changed_later(self):
        from config.config_manager import DEFAULTS
        from editor.annotation_items import shape
        from PySide6.QtCore import QPointF
        canvas = AnnotationCanvas(Image.new("RGB", (200, 200), "white"), dict(DEFAULTS))
        item = shape("arrow", QPointF(20, 20), QPointF(120, 60), "#ff0000", 4, arrow_style="filled")
        canvas.scene_data.addItem(item)
        canvas.checkpoint()
        item.setSelected(True)
        self.assertEqual(item.arrow_style, "filled")
        canvas.set_selected_arrow_style("double_filled")
        self.assertEqual(item.arrow_style, "double_filled")
        self.assertTrue(item.brush().style() != Qt.NoBrush)
        canvas.undo()
        restored = [a for a in canvas.annotations() if getattr(a, "arrow_style", None) is not None][0]
        self.assertEqual(restored.arrow_style, "filled")
        # 矩形线型切换不影响箭头
        canvas.set_selected_line_style("dash")
        self.assertEqual(restored.arrow_style, "filled")
        canvas.close()

    def test_canvas_undo_export(self):
        from config.config_manager import DEFAULTS
        canvas = AnnotationCanvas(Image.new("RGB", (120, 100), "white"), DEFAULTS)
        canvas.scene_data.addItem(shape("rect", QPointF(10, 10), QPointF(60, 60), "#ff0000", 3))
        canvas.checkpoint()
        self.assertEqual(len(canvas.annotations()), 1)
        canvas.undo()
        self.assertEqual(len(canvas.annotations()), 0)
        canvas.redo()
        self.assertEqual(len(canvas.annotations()), 1)
        self.assertEqual(canvas.render_image().size().width(), 120)

    def test_undo_redo_keyboard_shortcuts(self):
        from config.config_manager import DEFAULTS
        from PySide6.QtTest import QTest
        canvas = AnnotationCanvas(Image.new("RGB", (120, 100), "white"), DEFAULTS)
        canvas.scene_data.addItem(shape("rect", QPointF(10, 10), QPointF(60, 60), "#ff0000", 3))
        base = canvas.cursor_index
        canvas.checkpoint()
        canvas.checkpoint()
        self.assertEqual(canvas.cursor_index, base + 2)
        # Ctrl+Z 撤销（各平台一致）
        QTest.keyClick(canvas, Qt.Key_Z, Qt.ControlModifier)
        self.assertEqual(canvas.cursor_index, base + 1)
        # 重做：覆盖 Windows(Ctrl+Y) 与 macOS(Ctrl+Shift+Z) 两种标准
        QTest.keyClick(canvas, Qt.Key_Y, Qt.ControlModifier)
        QTest.keyClick(canvas, Qt.Key_Z, Qt.ControlModifier | Qt.ShiftModifier)
        self.assertEqual(canvas.cursor_index, base + 2)
        canvas.close()

    def test_annotation_sequence_serializes_and_restores(self):
        from editor.annotation_canvas import AnnotationCanvas
        from editor.annotation_items import AnnotationSequenceItem
        from config.config_manager import DEFAULTS
        from PySide6.QtCore import QPointF

        canvas = AnnotationCanvas(Image.new("RGB", (120, 100), "white"), dict(DEFAULTS))
        item = AnnotationSequenceItem(3, "#00ad91", "#ffffff", 16, "circle", "", QPointF(20, 20))
        canvas.scene_data.addItem(item)
        records = canvas.snapshot()
        self.assertEqual(records[0]["type"], "AnnotationSequenceItem")
        self.assertEqual(records[0]["number"], 3)
        self.assertEqual(records[0]["sequence_fill_color"], "#00ad91")
        self.assertEqual(records[0]["sequence_text_color"], "#ffffff")
        for it in canvas.annotations():
            canvas.scene_data.removeItem(it)
        canvas.restore(records)
        restored = [it for it in canvas.annotations()
                   if isinstance(it, AnnotationSequenceItem)]
        self.assertEqual(len(restored), 1)
        self.assertEqual(restored[0].number, 3)
        self.assertEqual(restored[0].sequence_fill_color, "#00ad91")
        self.assertEqual(restored[0].sequence_font_size, 16)

    def test_annotation_render_keeps_antialiased_edges_before_round_output(self):
        from config.config_manager import DEFAULTS
        from editor.image_effects import apply_output_effects

        canvas = AnnotationCanvas(Image.new("RGBA", (80, 80), (0, 0, 0, 0)), DEFAULTS)
        canvas.scene_data.addItem(shape("arrow", QPointF(8.3, 9.7),
                                        QPointF(70.6, 68.2), "#ff2020", 1))
        rendered = canvas.render_image()
        self.assertTrue(any(0 < rendered.pixelColor(x, y).alpha() < 255
                            for y in range(80) for x in range(80)))
        output = apply_output_effects(rendered, DEFAULTS, True, 14)
        self.assertEqual(output.pixelColor(0, 0).alpha(), 0)
        self.assertTrue(any(0 < output.pixelColor(x, y).alpha() < 255
                            for y in range(20) for x in range(20)))
        canvas.close()

    def test_rounded_rectangle_survives_undo_redo(self):
        from config.config_manager import DEFAULTS
        canvas = AnnotationCanvas(Image.new("RGB", (120, 100), "white"), DEFAULTS)
        item = shape("rect", QPointF(10, 10), QPointF(60, 60), "#ff0000", 3,
                     corner_radius=12)
        canvas.scene_data.addItem(item)
        canvas.checkpoint()
        canvas.undo()
        canvas.redo()
        restored = canvas.annotations()[0]
        self.assertEqual(type(restored).__name__, "RoundedRectItem")
        self.assertEqual(restored.corner_radius, 12)
        self.assertEqual(restored.rect().width(), 50)

    def test_dragging_selected_annotation_repaints_full_viewport(self):
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QGraphicsView
        from config.config_manager import DEFAULTS

        canvas = AnnotationCanvas(Image.new("RGB", (120, 100), "white"), DEFAULTS)
        canvas.resize(200, 150)
        canvas.show()
        item = shape("rect", QPointF(20, 20), QPointF(55, 55), "#ff0000", 3)
        canvas.scene_data.addItem(item)
        item.setSelected(True)
        self.app.processEvents()
        self.assertEqual(canvas.viewportUpdateMode(), QGraphicsView.FullViewportUpdate)

        # 旋转按钮在图形中心，移动请从图形内部、但避开中心按钮的位置拖。
        center = item.sceneBoundingRect().center()
        start = canvas.mapFromScene(QPointF(center.x() - 10, center.y() - 10))
        end = start + QPoint(35, 25)
        QTest.mousePress(canvas.viewport(), Qt.LeftButton, pos=start)
        QTest.mouseMove(canvas.viewport(), pos=end)
        QTest.mouseRelease(canvas.viewport(), Qt.LeftButton, pos=end)
        self.app.processEvents()
        self.assertGreater(item.pos().x(), 20)
        self.assertGreater(item.pos().y(), 20)
        canvas.close()

    def test_select_annotations_with_drag_box(self):
        from PySide6.QtTest import QTest
        from config.config_manager import DEFAULTS
        canvas = AnnotationCanvas(Image.new("RGB", (120, 100), "white"), DEFAULTS)
        canvas.resize(200, 150)
        canvas.show()
        item = shape("rect", QPointF(20, 20), QPointF(50, 50), "#ff0000", 3)
        canvas.scene_data.addItem(item)
        QTest.mousePress(canvas.viewport(), Qt.LeftButton, pos=canvas.mapFromScene(QPointF(5, 5)))
        QTest.mouseMove(canvas.viewport(), pos=canvas.mapFromScene(QPointF(65, 65)))
        QTest.mouseRelease(canvas.viewport(), Qt.LeftButton, pos=canvas.mapFromScene(QPointF(65, 65)))
        self.assertIn(item, canvas.scene_data.selectedItems())
        canvas.close()

    def test_right_button_does_not_run_active_annotation_tool(self):
        from config.config_manager import DEFAULTS
        canvas = AnnotationCanvas(Image.new("RGB", (300, 200), "white"), dict(DEFAULTS))
        canvas.resize(180, 130)
        canvas.show()
        canvas.set_zoom(200)
        confirmations = []
        canvas.confirmed.connect(lambda: confirmations.append(True))
        with patch("editor.text_input_dialog.TextInputDialog",
                   side_effect=AssertionError("right click must not edit text")):
            for tool in ("text", "picker", "eraser", "select"):
                canvas.tool = tool
                QTest.mousePress(canvas.viewport(), Qt.RightButton, pos=QPoint(70, 50))
                QTest.mouseMove(canvas.viewport(), pos=QPoint(55, 50))
                QTest.mouseRelease(canvas.viewport(), Qt.RightButton, pos=QPoint(55, 50))
                self.assertEqual(canvas.tool, tool)
                self.assertFalse(canvas.annotations())
            QTest.mouseDClick(canvas.viewport(), Qt.RightButton, pos=QPoint(70, 50))
        self.assertFalse(confirmations)
        canvas.close()

    def test_selected_annotation_tool_reports_type_and_clears_on_multi(self):
        from config.config_manager import DEFAULTS
        from editor.annotation_items import shape, text_item
        from PySide6.QtCore import QPointF, Qt
        canvas = AnnotationCanvas(Image.new("RGB", (120, 100), "white"), dict(DEFAULTS))
        rect = shape("rect", QPointF(10, 10), QPointF(60, 60), "#ff0000", 4)
        ellipse = shape("ellipse", QPointF(10, 10), QPointF(60, 60), "#00ff00", 4)
        text = text_item(QPointF(10, 10), "字", canvas.settings, Qt.AlignLeft)
        canvas.scene_data.addItem(rect)
        canvas.scene_data.addItem(ellipse)
        canvas.scene_data.addItem(text)
        # 单个选中时返回对应工具类型。
        canvas.scene_data.clearSelection()
        rect.setSelected(True)
        self.assertEqual(canvas.selected_annotation_tool(), "rect")
        canvas.scene_data.clearSelection()
        ellipse.setSelected(True)
        self.assertEqual(canvas.selected_annotation_tool(), "ellipse")
        canvas.scene_data.clearSelection()
        text.setSelected(True)
        self.assertEqual(canvas.selected_annotation_tool(), "text")
        # 多选或空选时返回空串，不展示单选标注的专属参数。
        rect.setSelected(True)
        ellipse.setSelected(True)
        text.setSelected(True)
        self.assertEqual(canvas.selected_annotation_tool(), "")
        canvas.scene_data.clearSelection()
        self.assertEqual(canvas.selected_annotation_tool(), "")
        canvas.close()

    def test_marker_is_translucent_wide_and_survives_undo(self):
        from PySide6.QtTest import QTest
        from config.config_manager import DEFAULTS, validate
        settings = dict(DEFAULTS)
        settings["pen_color"] = "#ff0000"
        settings["marker_opacity"] = 50
        self.assertEqual(validate({"annotation_tool": "marker"})["annotation_tool"], "marker")
        canvas = AnnotationCanvas(Image.new("RGB", (120, 100), "white"), settings)
        canvas.resize(200, 150)
        canvas.show()
        canvas.tool = "marker"
        QTest.mousePress(canvas.viewport(), Qt.LeftButton, pos=canvas.mapFromScene(QPointF(20, 30)))
        QTest.mouseMove(canvas.viewport(), pos=canvas.mapFromScene(QPointF(80, 30)))
        QTest.mouseRelease(canvas.viewport(), Qt.LeftButton, pos=canvas.mapFromScene(QPointF(80, 30)))
        item = canvas.annotations()[0]
        self.assertGreaterEqual(item.pen().width(), 12)
        self.assertEqual(item.pen().color().alpha(), 128)
        self.assertNotEqual(canvas.render_image().pixelColor(40, 30).name(), "#ff0000")
        canvas.undo()
        self.assertEqual(len(canvas.annotations()), 0)
        canvas.redo()
        self.assertEqual(canvas.annotations()[0].pen().color().alpha(), 128)
        self.assertEqual(canvas.annotations()[0].pen().capStyle(), Qt.RoundCap)
        canvas.close()

    def test_editor_transparent_background_modes(self):
        """编辑器透明背景是独立设置：主题棋盘 / 暗棋盘 / 亮棋盘 / 纯透明。"""
        from config.config_manager import DEFAULTS

        def palette(canvas):
            image = canvas.corner_preview_brush.textureImage()
            if image.isNull():
                return None
            return {image.pixelColor(0, 0).name(),
                    image.pixelColor(image.width() // 2, 0).name()}

        dark = AnnotationCanvas(Image.new("RGB", (40, 30), "white"),
                                dict(DEFAULTS, editor_transparent_background="dark_checker"))
        light = AnnotationCanvas(Image.new("RGB", (40, 30), "white"),
                                 dict(DEFAULTS, editor_transparent_background="light_checker"))
        plain = AnnotationCanvas(Image.new("RGB", (40, 30), "white"),
                                 dict(DEFAULTS, editor_transparent_background="transparent"))
        theme = AnnotationCanvas(Image.new("RGB", (40, 30), "white"), dict(DEFAULTS))
        try:
            self.assertEqual(palette(dark), {"#252525", "#3b3b3b"})
            self.assertEqual(palette(light), {"#f0f0f0", "#c8c8c8"})
            # 纯透明：主题纯色、不铺棋盘；theme 默认铺主题感知棋盘。
            self.assertIsNone(palette(plain))
            self.assertIsNotNone(palette(theme))
        finally:
            for canvas in (dark, light, plain, theme):
                canvas.close()

    def test_edge_fill_rect_is_not_pushed_inside_canvas(self):
        """贴边填充的矩形不再被挤进画布内：整条边都应被填充覆盖。"""
        from config.config_manager import DEFAULTS

        canvas = AnnotationCanvas(Image.new("RGB", (60, 40), "white"), dict(DEFAULTS))
        try:
            item = shape("rect", QPointF(0, 0), QPointF(60, 40), "#ff0000", 4,
                         fill_enabled=True, fill_opacity=100)
            canvas._add_annotation(item)
            canvas.checkpoint()
            out = canvas.render_image()
            for point in ((0, 0), (59, 0), (0, 39), (59, 39)):
                self.assertEqual(out.pixelColor(*point).name(), "#ff0000", point)
        finally:
            canvas.close()

    def test_annotations_reach_canvas_edge_without_being_pushed_inside(self):
        """画布外不落笔；从画布内拖到画布外时几何夹到边缘，但不被缩放/平移挤回画布内。"""
        from config.config_manager import DEFAULTS
        from editor.annotation_items import text_item
        canvas = AnnotationCanvas(Image.new("RGB", (120, 100), "white"), dict(DEFAULTS))
        canvas.resize(260, 220)
        canvas.show()
        self.app.processEvents()
        self.assertEqual(canvas.alignment(), Qt.AlignCenter)
        image = canvas.sceneRect()
        for tool in ("pen", "marker", "rect", "ellipse", "arrow", "mosaic", "text"):
            canvas.tool = tool
            outside = canvas.mapFromScene(QPointF(-12, -12))
            with patch("editor.text_input_dialog.TextInputDialog") as dialog:
                QTest.mousePress(canvas.viewport(), Qt.LeftButton, pos=outside)
                QTest.mouseRelease(canvas.viewport(), Qt.LeftButton, pos=outside)
                dialog.assert_not_called()
            self.assertFalse(canvas.annotations(), tool)
        for tool in ("pen", "marker", "rect", "ellipse", "arrow", "mosaic"):
            canvas.tool = tool
            start = canvas.mapFromScene(QPointF(45, 40))
            outside = canvas.mapFromScene(QPointF(150, 130))
            QTest.mousePress(canvas.viewport(), Qt.LeftButton, pos=start)
            QTest.mouseMove(canvas.viewport(), outside)
            QTest.mouseRelease(canvas.viewport(), Qt.LeftButton, pos=outside)
            item = canvas.annotations()[-1]
            # 没有被缩小时才算“没挤”；几何到达右/下边缘（画笔外扩允许略微超过）。
            self.assertAlmostEqual(item.scale(), 1.0, delta=0.001, msg=tool)
            self.assertGreaterEqual(item.sceneBoundingRect().right(), image.right() - 2, tool)
            self.assertGreaterEqual(item.sceneBoundingRect().bottom(), image.bottom() - 2, tool)
        text = text_item(QPointF(115, 95), "A long text annotation", DEFAULTS, Qt.AlignLeft)
        canvas.scene_data.addItem(text)
        canvas.checkpoint()
        # 画布内的文字位置完全不动；超出右边缘的部分靠绘制时裁掉。
        self.assertEqual(text.pos(), QPointF(115, 95))
        canvas.close()

    def test_right_click_annotation_menu_deletes_and_undo_restores(self):
        from config.config_manager import DEFAULTS
        canvas = AnnotationCanvas(Image.new("RGB", (140, 100), "white"), dict(DEFAULTS))
        canvas.resize(220, 160)
        canvas.scene_data.addItem(shape("rect", QPointF(10, 10), QPointF(65, 60), "#ff0000", 3))
        canvas.checkpoint()
        canvas.show()
        self.app.processEvents()
        point = canvas.mapFromScene(QPointF(12, 30))
        with patch.object(canvas, "show_annotation_menu") as show_menu:
            QTest.mousePress(canvas.viewport(), Qt.RightButton, pos=point)
            QTest.mouseRelease(canvas.viewport(), Qt.RightButton, pos=point)
        show_menu.assert_called_once()
        canvas.annotation_menu(canvas.annotations()[0]).actions()[-1].trigger()
        self.assertFalse(canvas.annotations())
        canvas.undo()
        self.assertEqual(len(canvas.annotations()), 1)
        canvas.close()

    def test_transform_undo_and_redo(self):
        from config.config_manager import DEFAULTS
        editor = EditorWindow(Image.new("RGB", (120, 80), "white"), DEFAULTS)
        editor.execute("right")
        self.assertEqual(editor.canvas.image.size, (80, 120))
        editor.execute("undo")
        self.assertEqual(editor.canvas.image.size, (120, 80))
        editor.execute("redo")
        self.assertEqual(editor.canvas.image.size, (80, 120))

    def test_edit_selected_annotation_and_copy_both_formats(self):
        from PySide6.QtCore import QPointF
        from PySide6.QtGui import QGuiApplication
        from config.config_manager import DEFAULTS
        from editor.annotation_items import text_item
        with tempfile.TemporaryDirectory() as folder:
            settings = {**DEFAULTS, "save_dir": folder, "open_dir": False,
                        "copy_saved_image": True, "copy_saved_path": True}
            editor = EditorWindow(Image.new("RGB", (60, 40), "white"), settings)
            item = shape("rect", QPointF(2, 2), QPointF(25, 20), "#ff0000", 3)
            editor.canvas.scene_data.addItem(item)
            item.setSelected(True)
            editor.toolbar.tool_buttons["rect"].click()
            editor.toolbar.pen_width.setValue(7)
            editor.set_pen_color("#00ff00")
            self.assertEqual(item.pen().width(), 7)
            self.assertEqual(item.pen().color().name(), "#00ff00")
            item.setSelected(False)
            text = text_item(QPointF(4, 4), "可编辑", settings, Qt.AlignLeft)
            editor.canvas.scene_data.addItem(text)
            text.setSelected(True)
            editor.set_annotation_setting("font", "Courier New")
            self.assertEqual(text.font().family(), "Courier New")
            saved = []
            editor.image_saved.connect(lambda path, image: saved.append((path, image)))
            editor.execute("save")
            self.assertEqual(saved[-1][0], str(editor.last_path))
            self.assertFalse(saved[-1][1].isNull())
            mime = QGuiApplication.clipboard().mimeData()
            self.assertTrue(mime.hasImage())
            self.assertTrue(mime.hasText())
            self.assertEqual(mime.text(), str(editor.last_path))
            editor.close()

    def test_transform_preserves_editable_annotations(self):
        from config.config_manager import DEFAULTS
        editor = EditorWindow(Image.new("RGB", (120, 80), "white"), DEFAULTS.copy())
        item = shape("rect", QPointF(10, 10), QPointF(30, 25), "#ff0000", 3)
        editor.canvas.scene_data.addItem(item)
        editor.canvas.checkpoint()
        editor.execute("left")
        self.assertEqual(len(editor.canvas.annotations()), 1)
        moved = editor.canvas.annotations()[0]
        self.assertNotEqual(moved.sceneBoundingRect().center(), item.sceneBoundingRect().center())
        moved.setSelected(True)
        editor.canvas.set_selected_width(7)
        self.assertEqual(moved.pen().width(), 7)
        editor.execute("undo")
        self.assertEqual(editor.canvas.annotations()[0].pen().width(), 3)
        editor.execute("undo")
        self.assertEqual(editor.canvas.image.size, (120, 80))
        self.assertEqual(len(editor.canvas.annotations()), 1)
        editor.close()

    def test_tool_icons_select_annotation_mode(self):
        from PySide6.QtWidgets import QToolButton
        from editor.toolbar_widget import ToolbarWidget
        toolbar = ToolbarWidget()
        actions = []
        toolbar.tool_changed.connect(actions.append)
        toolbar.tool_buttons["arrow"].click()
        self.assertEqual(actions, ["arrow"])
        self.assertTrue(toolbar.tool_buttons["arrow"].isChecked())
        self.assertFalse(toolbar.tool_buttons["select"].isChecked())
        text = toolbar.tool_buttons["text"]
        self.assertFalse(text.icon().isNull())
        self.assertTrue(any(text.icon().pixmap(24, 24).toImage().pixelColor(x, y).alpha() > 0
                            for x in range(24) for y in range(24)))
        for width in (420, 600, 950, 2400):
            toolbar.reflow(width)
            grid = toolbar.tool_grid
            columns = toolbar._layout_mode[1]
            ordered = [toolbar.cursor_switch, toolbar.options_button]
            ordered.extend(toolbar.tool_buttons[key] for key in "select pen marker rect ellipse number text arrow mosaic eraser picker crop".split())
            for index, button in enumerate(ordered):
                row, column = divmod(index, columns)
                self.assertEqual(grid.getItemPosition(grid.indexOf(button)), (row, column, 1, 1))
            self.assertEqual(grid.indexOf(toolbar.pen_color), -1)
            self.assertEqual(toolbar.image_grid.indexOf(toolbar.cursor_switch), -1)
            self.assertEqual(toolbar.pen_color.text(), "颜色")
            self.assertNotIn("square", toolbar.tool_buttons)
            # 当前 UI 已不再把独立的"当前颜色"按钮放进 options 菜单（改用各工具颜色按钮 + active_color_label）。
            self.assertEqual(toolbar.cursor_switch.text(), "显示鼠标")
            self.assertIsNot(toolbar.cursor_switch.parentWidget(), toolbar.options_button.menu())
        paste = next(button for button in toolbar.findChildren(QToolButton) if button.text() == "贴图")
        self.assertFalse(paste.icon().isNull())

    def test_annotation_options_sliders_show_live_values(self):
        from editor.toolbar_widget import ToolbarWidget
        from PySide6.QtWidgets import QWidgetAction, QLabel
        toolbar = ToolbarWidget(settings={"pen_width": 7, "mosaic_size": 18})
        toolbar.tool_buttons["pen"].click()
        panel = next(action.defaultWidget() for action in toolbar.options_button.menu().actions()
                     if isinstance(action, QWidgetAction))
        self.assertGreaterEqual(panel.minimumWidth(), 340)
        self.assertLessEqual(panel.minimumWidth(), 500)
        self.assertGreaterEqual(toolbar.options_button.menu().sizeHint().width(), 320)
        panel.show()
        self.app.processEvents()
        for row in (1,):
            label = panel.layout().itemAtPosition(row, 0).widget()
            control = panel.layout().itemAtPosition(row, 1).widget()
            self.assertIsInstance(label, QLabel)
            self.assertEqual(label.alignment(), Qt.AlignRight | Qt.AlignVCenter)
            self.assertLess(label.geometry().right(), control.geometry().left())
            self.assertAlmostEqual(label.geometry().center().y(), control.geometry().center().y(), delta=1)
        for slider in (toolbar.pen_width, toolbar.marker_opacity, toolbar.mosaic_size):
            self.assertGreaterEqual(slider.minimumWidth(), 220)
            self.assertGreaterEqual(slider.minimumHeight(), 38)
        self.assertEqual(toolbar.pen_width_label.text(), "7 px")
        self.assertEqual(toolbar.mosaic_size_label.text(), "18 px")
        changes = []
        toolbar.setting_changed.connect(lambda key, value: changes.append((key, value)))
        toolbar.pen_width.setValue(14)
        toolbar.tool_buttons["marker"].click()
        toolbar.marker_opacity.setValue(65)
        toolbar.mosaic_size.setValue(100)
        self.assertEqual(toolbar.pen_width_label.text(),
                 f"{toolbar.tool_widths['marker']} px")
        self.assertEqual(toolbar.marker_opacity_label.text(), "65%")
        self.assertEqual(toolbar.mosaic_size_label.text(), "100 px")
        self.assertIn(("pen_width", 14), changes)
        self.assertIn(("marker_opacity", 65), changes)
        self.assertIn(("mosaic_size", 100), changes)

    def test_toolbar_primary_tool_order_and_annotation_icons(self):
        from editor.toolbar_widget import ToolbarWidget

        toolbar = ToolbarWidget()
        toolbar.reflow(1100)
        first_row = [toolbar.cursor_switch, toolbar.options_button]
        first_row.extend(toolbar.tool_buttons[key] for key in "select pen marker rect".split())
        positions = [toolbar.tool_grid.getItemPosition(toolbar.tool_grid.indexOf(widget))
                     for widget in first_row]
        self.assertEqual(positions, [(0, column, 1, 1) for column in range(6)])
        self.assertEqual(toolbar.tool_grid.indexOf(toolbar.pen_color), -1)
        for widget in (toolbar.options_button, *toolbar.tool_buttons.values()):
            item = toolbar.tool_grid.itemAt(toolbar.tool_grid.indexOf(widget))
            self.assertTrue(item.alignment() & Qt.AlignVCenter, widget.text())

        marker = toolbar.tool_buttons["marker"].icon().pixmap(24, 24).toImage()
        self.assertTrue(any(marker.pixelColor(x, y).alpha() and
                            marker.pixelColor(x, y).red() > 220 and
                            marker.pixelColor(x, y).green() > 160 and
                            marker.pixelColor(x, y).blue() < 100
                            for x in range(marker.width()) for y in range(marker.height())))
        text = toolbar.tool_buttons["text"].icon().pixmap(24, 24).toImage()
        self.assertGreater(text.pixelColor(7, 5).alpha(), 0)
        self.assertGreater(text.pixelColor(12, 17).alpha(), 0)
        self.assertEqual(text.pixelColor(0, 0).alpha(), 0)
        self.assertFalse(toolbar.tool_buttons["arrow"].icon().isNull())
        self.assertFalse(toolbar.tool_buttons["crop"].icon().isNull())
        eraser = toolbar.tool_buttons["eraser"].icon().pixmap(24, 24).toImage()
        delete = next(button for button in toolbar.edit_buttons if button.text() == "删除").icon().pixmap(24, 24).toImage()
        self.assertNotEqual(eraser, delete)
        transform_icons = [toolbar.image_buttons[index].icon().pixmap(24, 24).toImage()
                           for index in (3, 4, 5, 6)]
        for index, icon_image in enumerate(transform_icons):
            self.assertNotIn(icon_image, transform_icons[:index])
        from ui.action_icons import action_icon
        for name in ("camera", "resize", "window_edit", "eraser", "rotate", "rotate_180",
                 "rotate_left", "rotate_right", "layer_top", "layer_bottom", "pin",
                     "flip_horizontal", "flip_vertical", "clipboard_image", "clipboard_edit",
                     "sticker", "layers",
                 "lock", "unlock", "border", "shadow", "visibility", "hidden", "group",
                 "group_add", "group_move",
                 "locate", "rename", "click_through", "settings"):
            icon_image = action_icon(name).pixmap(24, 24).toImage()
            self.assertTrue(any(icon_image.pixelColor(x, y).alpha() for x in range(24)
                    for y in range(24)), name)

    def test_tool_widths_are_independent_and_options_follow_tool(self):
        from PySide6.QtCore import QRectF
        from config.config_manager import DEFAULTS, TOOL_WIDTH_KEYS, validate
        from editor.toolbar_widget import ToolbarWidget
        settings = dict(DEFAULTS)
        toolbar = ToolbarWidget(settings=settings)
        changes = []
        toolbar.setting_changed.connect(lambda key, value: changes.append((key, value)))
        for tool, width in (("rect", 8), ("ellipse", 11), ("arrow", 14),
                            ("pen", 17), ("marker", 20), ("eraser", 23)):
            toolbar.tool_buttons[tool].click()
            self.assertTrue(toolbar.options_button.isEnabled())
            self.assertEqual(toolbar.options_button.text(), f"{toolbar.tool_buttons[tool].text()}设置")
            self.assertEqual(toolbar.option_rows[1][0].text(), "直径" if tool == "eraser" else "线宽")
            self.assertFalse(toolbar.pen_width.isHidden())
            expected_range = (10, 100) if tool == "eraser" else (1, 50)
            self.assertEqual((toolbar.pen_width.minimum(), toolbar.pen_width.maximum()), expected_range)
            if tool == "rect":
                self.assertFalse(toolbar.rect_style.isHidden())
            if tool == "ellipse":
                self.assertFalse(toolbar.ellipse_style.isHidden())
            toolbar.pen_width.setValue(width)
            self.assertIn((TOOL_WIDTH_KEYS[tool], width), changes)
            settings[TOOL_WIDTH_KEYS[tool]] = width
        for tool, width in (("rect", 8), ("ellipse", 11), ("arrow", 14),
                            ("pen", 17), ("marker", 20), ("eraser", 23)):
            toolbar.tool_buttons[tool].click()
            self.assertEqual(toolbar.pen_width.value(), width)
        toolbar.tool_buttons["eraser"].click()
        self.assertFalse(toolbar.previews["eraser"].isHidden())
        eraser_scene = toolbar.previews["eraser"].build(QRectF(0, 0, 320, 96))
        self.assertGreater(len(eraser_scene.items()), 1)
        toolbar.tool_buttons["marker"].click()
        self.assertFalse(toolbar.marker_opacity.isHidden())
        toolbar.tool_buttons["text"].click()
        self.assertTrue(toolbar.pen_width.isHidden())
        self.assertFalse(toolbar.font.isHidden())
        toolbar.tool_buttons["mosaic"].click()
        self.assertFalse(toolbar.mosaic_size.isHidden())
        self.assertTrue(toolbar.font.isHidden())
        toolbar.tool_buttons["select"].click()
        self.assertFalse(toolbar.options_button.isEnabled())
        self.assertEqual(toolbar.options_button.text(), "更多设置")
        self.assertEqual(validate(settings)["marker_width"], 20)
        for key in TOOL_WIDTH_KEYS.values():
            with self.assertRaises(ValueError):
                validate({key: 101 if key == "eraser_width" else 51})
        self.assertEqual(validate({})["eraser_width"], 30)

    def test_canvas_uses_each_tools_width(self):
        from config.config_manager import DEFAULTS, TOOL_WIDTH_KEYS
        from PySide6.QtGui import QColor, QPixmap
        from PySide6.QtWidgets import QGraphicsPixmapItem
        settings = dict(DEFAULTS, rect_width=8, ellipse_width=11, arrow_width=14,
                        pen_width=17, marker_width=20, eraser_width=23)
        canvas = AnnotationCanvas(Image.new("RGB", (120, 100), "white"), settings)
        for tool, key in TOOL_WIDTH_KEYS.items():
            canvas.tool = tool
            if tool != "eraser":
                self.assertEqual(canvas.tool_width(), settings[key])
        canvas.tool = "marker"
        self.assertEqual(canvas.stroke_pen().width(), 100)
        settings["eraser_width"] = 4
        pixmap = QPixmap(80, 80)
        pixmap.fill(QColor("#ff0000"))
        canvas.scene_data.addItem(QGraphicsPixmapItem(pixmap))
        canvas.erase_segment(QPointF(40, 40), QPointF(40, 40))
        image = canvas.render_image()
        self.assertEqual(image.pixelColor(40, 40).name(), "#ffffff")
        self.assertEqual(image.pixelColor(45, 40).name(), "#ff0000")

    def test_canvas_uses_rect_and_ellipse_line_styles(self):
        from config.config_manager import DEFAULTS

        settings = dict(DEFAULTS, rect_style="dash", ellipse_style="dash")
        canvas = AnnotationCanvas(Image.new("RGB", (120, 100), "white"), settings)
        for tool in ("rect", "ellipse"):
            canvas.tool = tool
            QTest.mousePress(canvas.viewport(), Qt.LeftButton, pos=canvas.mapFromScene(QPointF(10, 10)))
            QTest.mouseMove(canvas.viewport(), canvas.mapFromScene(QPointF(60, 40)))
            QTest.mouseRelease(canvas.viewport(), Qt.LeftButton, pos=canvas.mapFromScene(QPointF(60, 40)))
            self.assertEqual(canvas.annotations()[-1].pen().style(), Qt.DashLine)
        canvas.annotations()[-1].setSelected(True)
        canvas.set_selected_line_style("solid")
        self.assertEqual(canvas.annotations()[-1].pen().style(), Qt.SolidLine)
        canvas.close()

    def test_paste_latest_skips_open_history_then_reactivates_latest(self):
        from config.config_manager import DEFAULTS
        import os

        with tempfile.TemporaryDirectory() as folder:
            paths = [Path(folder) / f"{index}.png" for index in range(4)]
            for index, path in enumerate(paths):
                Image.new("RGB", (20 + index, 20), "white").save(path)
                os.utime(path, (index + 10, index + 10))
            manager = StickerManager(dict(DEFAULTS, save_dir=folder))
            try:
                for path in reversed(paths):
                    self.assertTrue(manager.paste_latest())
                    self.assertEqual(Path(manager.items[-1].source), path)
                self.assertTrue(manager.paste_latest())
                self.assertEqual(Path(manager.active_sticker.source), paths[3])
                manager.items[1].close()
                self.assertTrue(manager.paste_latest())
                self.assertEqual(Path(manager.items[-1].source), paths[2])
                manager.items[-1].close()
                paths[2].unlink()
                self.assertTrue(manager.paste_latest())
                self.assertEqual(Path(manager.active_sticker.source), paths[3])
                paths[2].write_bytes(b"not an image")
                self.assertTrue(manager.paste_latest())
                self.assertEqual(Path(manager.active_sticker.source), paths[3])
            finally:
                manager.close_all()
                self.app.processEvents()

    def test_output_appearance_stays_open_while_adjusting_and_using_modal_picker(self):
        from PySide6.QtCore import QEvent, QPointF, Qt
        from PySide6.QtGui import QCursor, QMouseEvent
        from PySide6.QtWidgets import QDialog
        from config.config_manager import DEFAULTS
        from editor.toolbar_widget import ToolbarWidget

        toolbar = ToolbarWidget(settings=dict(DEFAULTS))
        original_cursor_position = QCursor.pos()
        try:
            toolbar.show()
            self.app.processEvents()
            QTest.mouseClick(toolbar.appearance_toggle, Qt.LeftButton)
            self.app.processEvents()
            self.assertTrue(toolbar.appearance_menu.isVisible())
            QTest.mouseClick(toolbar.appearance_toggle, Qt.LeftButton)
            self.app.processEvents()
            self.assertFalse(toolbar.appearance_menu.isVisible())
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
            self.assertFalse(toolbar.appearance_menu.isVisible())
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
            QTest.mouseClick(
                toolbar.appearance_controls["editor_image_border_enabled"],
                Qt.LeftButton)
            self.app.processEvents()
            self.assertTrue(toolbar.appearance_menu.isVisible())

            picker = QDialog(toolbar.appearance_panel)
            picker.setModal(True)
            picker.show()
            self.app.processEvents()
            self.assertIs(self.app.activeModalWidget(), picker)
            outside_click = QMouseEvent(
                QEvent.MouseButtonPress, QPointF(), QPointF(-100, -100),
                Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
            toolbar.eventFilter(toolbar, outside_click)
            self.assertTrue(toolbar.appearance_menu.isVisible())
            picker.close()
        finally:
            QCursor.setPos(original_cursor_position)
            toolbar.close()


    def test_save_as_directory_prefers_remembered_then_configured(self):
        """「另存为」默认目录：上次用过的优先；已失效或从未用过则回落到设置的保存目录。"""
        from config.config_manager import DEFAULTS
        from core.path_utils import resolved_dir, save_as_directory

        with tempfile.TemporaryDirectory() as folder:
            remembered = Path(folder) / "remembered"
            remembered.mkdir()
            settings = dict(DEFAULTS, save_dir=folder, save_as_dir=str(remembered))
            self.assertEqual(save_as_directory(settings), str(remembered))
            gone = Path(folder) / "gone"
            stale = dict(settings, save_as_dir=str(gone))
            self.assertEqual(save_as_directory(stale), str(resolved_dir(stale)))
            fresh = dict(settings, save_as_dir="")
            self.assertEqual(save_as_directory(fresh), str(resolved_dir(fresh)))

    def test_editor_save_as_uses_chosen_directory_and_remembers_it(self):
        """独立编辑器「另存为」：写进所选目录的新文件、发记住目录信号、不改当前文件。"""
        from config.config_manager import DEFAULTS
        from editor.editor_window import EditorWindow

        with tempfile.TemporaryDirectory() as folder, tempfile.TemporaryDirectory() as target:
            settings = dict(DEFAULTS, save_dir=folder, filename="as_%H%M%S", save_format="png")
            editor = EditorWindow(Image.new("RGB", (40, 30), "white"), settings)
            remembered, saved = [], []
            editor.save_as_dir_chosen.connect(remembered.append)
            editor.image_saved.connect(lambda path, image: saved.append(path))
            try:
                with patch("editor.editor_window.QFileDialog.getSaveFileName",
                           return_value=(str(Path(target) / "自定义名字.png"), "PNG")):
                    path = editor.save_as()
                self.assertIsNotNone(path)
                self.assertEqual(Path(path).parent, Path(target))
                self.assertEqual(Path(path).name, "自定义名字.png")   # 文件名可改
                self.assertTrue(Path(path).exists())
                self.assertEqual(remembered, [target])
                self.assertEqual(saved, [str(path)])
                self.assertIsNone(editor.last_path)
                # 「设置优先」：copy_saved_path 开则复制路径；两个开关都关则不动剪贴板
                from PySide6.QtGui import QGuiApplication
                clipboard = QGuiApplication.clipboard()
                clipboard.setText("哨兵")
                settings["copy_saved_path"] = False
                settings["copy_saved_image"] = False
                with patch("editor.editor_window.QFileDialog.getSaveFileName",
                           return_value=(str(Path(target) / "自定义名字.png"), "PNG")):
                    editor.save_as()
                self.assertEqual(clipboard.text(), "哨兵")
                settings["copy_saved_path"] = True
                with patch("editor.editor_window.QFileDialog.getSaveFileName",
                           return_value=(str(Path(target) / "自定义名字.png"), "PNG")):
                    copied_path = editor.save_as()
                self.assertEqual(clipboard.text(), str(copied_path))
                settings["copy_saved_path"] = False

                # 与「保存」一致：另存为完成后关闭编辑器
                editor.close = Mock()
                with patch("editor.editor_window.QFileDialog.getSaveFileName",
                           return_value=(str(Path(target) / "自定义名字.png"), "PNG")):
                    editor.save_as()
                editor.close.assert_called_once()   # 另存为不改变后续「保存」的目标

                # 工具栏按钮确实接在同一条命令上：点击「另存为」→ 选择目录 → 再写一份
                buttons = [item for item in editor.toolbar.output_buttons
                           if item.text() == "另存为"]
                self.assertEqual(len(buttons), 1)
                before_click = len(saved)
                with patch("editor.editor_window.QFileDialog.getSaveFileName",
                           return_value=(str(Path(target) / "自定义名字.png"), "PNG")):
                    buttons[0].click()
                self.assertEqual(len(saved), before_click + 1)
                self.assertEqual(Path(saved[-1]).parent, Path(target))
            finally:
                editor.close()

    def test_inline_editor_save_as_and_copy_only_notification(self):
        """原地编辑器：另存为写盘+记忆目录+关闭遮罩；仅复制发 copy_done（应用据此通知）。"""
        from config.config_manager import DEFAULTS
        from screenshot.mask_window import MaskWindow

        with tempfile.TemporaryDirectory() as folder, tempfile.TemporaryDirectory() as target:
            def make_mask(name):
                bounds = {"left": 0, "top": 0, "width": 120, "height": 80}
                settings = {**DEFAULTS, "save_dir": folder, "inline_edit": True,
                            "magnifier": False, "capture_after_selection": "edit",
                            "filename": name}
                with patch("screenshot.mask_window.visible_windows", return_value=[]):
                    mask = MaskWindow(Image.new("RGB", (120, 80), "blue"), bounds, [bounds], settings)
                mask.selection.rects.append(QRect(10, 10, 60, 40))
                mask.complete()
                self.app.processEvents()
                return mask

            # ① 另存为：写进所选目录、记住目录、发既有保存信号，最后关闭遮罩（与「保存」一致）
            mask = make_mask("inline_saveas")
            editor = mask.session.inline_editor
            remembered, saved, closed, forwarded = [], [], [], []
            mask.save_as_dir_chosen.connect(forwarded.append)   # 遮罩要转发给应用，否则不写回配置
            editor.save_as_dir_chosen.connect(remembered.append)
            editor.saved.connect(lambda path, image: saved.append(path))
            mask.close = lambda *args, **kwargs: closed.append(True)
            with patch("screenshot.mask_window.QFileDialog.getSaveFileName",
                       return_value=(str(Path(target) / "原地另存.png"), "")):
                path = editor.save_as()
            self.assertEqual(Path(path).parent, Path(target))
            self.assertEqual(Path(path).name, "原地另存.png")       # 文件名可改
            self.assertTrue(Path(path).exists())
            self.assertEqual(remembered, [target])
            self.assertEqual(forwarded, [target])
            self.assertEqual(saved, [str(path)])
            self.assertEqual(len(closed), 1)

            # ② 仅复制：必须发 copy_done（应用据此按「复制完成通知」发通知）
            mask2 = make_mask("inline_copy")
            copied = []
            mask2.copy_done.connect(copied.append)
            mask2.session.inline_editor.execute("copy_only")
            self.assertEqual(len(copied), 1)

    def test_copy_only_notifies_once_with_image_for_both_paths(self):
        """仅复制：快速（遮罩 Y）与窗口编辑（原地工具栏）都应恰好发一次带图通知。

        回归：独立编辑器曾同时发 status（应用把 status 接到 notify）与 copy_done，
        使窗口编辑收到「一条纯文字 + 一条带图」；快速路径需确认 copy_done 确实发出。
        """
        from config.config_manager import DEFAULTS
        from screenshot.mask_window import MaskWindow

        with tempfile.TemporaryDirectory() as folder:
            bounds = {"left": 0, "top": 0, "width": 120, "height": 80}
            settings = {**DEFAULTS, "save_dir": folder, "inline_edit": True,
                        "magnifier": False, "capture_after_selection": "edit",
                        "filename": "copy_once"}

            def make_mask():
                with patch("screenshot.mask_window.visible_windows", return_value=[]):
                    mask = MaskWindow(Image.new("RGB", (120, 80), "blue"), bounds, [bounds], settings)
                mask.selection.rects.append(QRect(10, 10, 60, 40))
                mask.complete()
                self.app.processEvents()
                return mask

            # ① 快速路径：遮罩「仅复制」（Y）→ copy_done 恰好一次且带图
            quick = make_mask()
            quick_copies = []
            quick.copy_done.connect(quick_copies.append)
            quick.copy_selection_to_clipboard()
            self.assertEqual(len(quick_copies), 1)
            self.assertIsNotNone(quick_copies[0])

            # ② 窗口编辑路径：原地工具栏「仅复制」→ 同一信号恰好一次
            inline = make_mask()
            inline_copies = []
            inline.copy_done.connect(inline_copies.append)
            inline.session.inline_editor.execute("copy_only")
            self.assertEqual(len(inline_copies), 1)
            self.assertIsNotNone(inline_copies[0])

            # ③ 独立编辑器：status 不再重复通知（否则应用会多发一条纯文字）
            from editor.editor_window import EditorWindow
            standalone = EditorWindow(Image.new("RGB", (30, 20), "white"), dict(DEFAULTS))
            statuses, copies = [], []
            standalone.status.connect(statuses.append)
            standalone.copy_done.connect(copies.append)
            standalone.copy_to_clipboard_only()
            self.assertEqual(statuses, [])
            self.assertEqual(len(copies), 1)
            self.assertIsNotNone(copies[0])

