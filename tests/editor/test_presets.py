"""标注样式预设与曲线箭头的契约用例。"""

import os
import sys
from pathlib import Path

# 向上找到仓库根（含 main.py），无论从哪一层、以哪种方式运行都能导入 tests.base。
_ROOT = Path(__file__).resolve().parent
while not (_ROOT / "main.py").exists() and _ROOT.parent != _ROOT:
    _ROOT = _ROOT.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tests.base import (CoreTests, unittest, QPointF, Qt, Image, Path, tempfile,
                        ConfigManager, EditorWindow, SettingsWindow)

from editor.annotation_items import SEQUENCE_PRESETS, curved_arrow_polygon, shape
from editor.annotation_presets import (ANNOTATION_PRESETS, matching_preset,
                                       preset_values, presets_for)


class AnnotationPresetTests(CoreTests):
    def test_preset_counts_cover_every_annotation_tool(self):
        """每种标注至少 10 个预设；矩形/椭圆至少 20 个（含填充与不填充）。"""
        for tool in ("rect", "ellipse"):
            self.assertGreaterEqual(len(ANNOTATION_PRESETS[tool]), 20, tool)
            fill_modes = {values.get(f"{tool}_fill_enabled")
                          for _preset_id, _label, values in ANNOTATION_PRESETS[tool]}
            self.assertEqual(fill_modes, {False, True}, f"{tool} 必须同时覆盖填充与不填充")
        for tool in ("pen", "marker", "arrow", "text", "mosaic", "eraser", "crop"):
            self.assertGreaterEqual(len(ANNOTATION_PRESETS[tool]), 10, tool)
        self.assertGreaterEqual(len(SEQUENCE_PRESETS) - 1, 10, "序号预设（不含自定义）至少 10 个")

    def test_preset_ids_labels_and_values_are_unique_and_valid(self):
        """预设 id/名称不得重复，且每个值都必须能通过配置校验。"""
        from config.config_manager import validate

        for tool, presets in ANNOTATION_PRESETS.items():
            ids = [preset_id for preset_id, _label, _values in presets]
            labels = [label for _preset_id, label, _values in presets]
            self.assertEqual(len(ids), len(set(ids)), tool)
            self.assertEqual(len(labels), len(set(labels)), tool)
            seen = set()
            for preset_id, label, values in presets:
                with self.subTest(tool=tool, preset=preset_id):
                    self.assertTrue(values, f"{tool}/{label} 不能是空预设")
                    fingerprint = tuple(sorted(values.items()))
                    self.assertNotIn(fingerprint, seen, f"{tool}/{label} 与已有预设完全相同")
                    seen.add(fingerprint)
                    for key, value in values.items():
                        validate({key: value})

    def test_matching_preset_round_trips_for_every_preset(self):
        """用预设值构造配置后必须能反查到该预设，而不是落到自定义。"""
        from config.config_manager import DEFAULTS

        for tool, presets in ANNOTATION_PRESETS.items():
            for preset_id, label, values in presets:
                with self.subTest(tool=tool, preset=preset_id):
                    settings = dict(DEFAULTS, **values)
                    self.assertEqual(matching_preset(tool, settings), preset_id, label)

    def test_preset_lookup_and_custom_fallback(self):
        self.assertEqual(preset_values("rect", "missing"), {})
        self.assertEqual(preset_values("rect", "custom"), {})
        self.assertGreater(len(presets_for("rect")), 0)
        self.assertEqual(presets_for("select"), ())

    def test_toolbar_applies_preset_and_marks_custom_on_manual_change(self):
        """工具栏套用预设会写入全部键；手动改一项后下拉回到“自定义”。"""
        from config.config_manager import DEFAULTS
        from editor.toolbar_widget import ToolbarWidget

        settings = dict(DEFAULTS)
        toolbar = ToolbarWidget(settings=settings)
        try:
            toolbar.set_tool_mode("rect")
            emitted = []
            toolbar.setting_changed.connect(lambda key, value: emitted.append((key, value)))
            target = next(item for item in presets_for("rect") if item[2].get("rect_fill_enabled"))
            toolbar.preset_combo.setCurrentIndex(toolbar.preset_combo.findData(target[0]))
            self.assertEqual(toolbar.preset_combo.currentData(), target[0])
            emitted_keys = {key for key, _value in emitted}
            self.assertEqual(emitted_keys, set(target[2]))
            self.assertTrue(toolbar.rect_fill_enabled.isChecked())
            # 手动改一个被预设覆盖的键 ⇒ 下拉必须显示自定义，避免界面与实际值不一致。
            toolbar.choice_buttons["rect_style"]["dash"].setChecked(True)
            self.assertEqual(toolbar.preset_combo.currentData(), "custom")
        finally:
            toolbar.close()

    def test_preset_syncs_standalone_editor_and_settings_page(self):
        """独立编辑器套用预设后，配置与设置页色块同时更新（三处同步的一环）。"""
        with tempfile.TemporaryDirectory() as folder:
            manager = ConfigManager(Path(folder) / "settings.json")
            settings_window = SettingsWindow(manager)
            editor = EditorWindow(Image.new("RGB", (120, 80), "white"), manager.data)
            try:
                editor.setting_changed.connect(settings_window.set_annotation_setting)
                editor.toolbar.set_tool_mode("rect")
                target = next(item for item in presets_for("rect")
                              if item[2].get("rect_fill_enabled"))
                editor.toolbar.preset_combo.setCurrentIndex(
                    editor.toolbar.preset_combo.findData(target[0]))
                self.app.processEvents()
                self.assertEqual(manager.data["rect_fill_enabled"],
                                 target[2]["rect_fill_enabled"])
                self.assertEqual(manager.data["rect_color"], target[2]["rect_color"])
                control = settings_window.page("编辑器").color_buttons["rect_color"]
                self.assertEqual(control.color, target[2]["rect_color"])
                toolbar = editor.toolbar
                self.assertEqual(toolbar.rect_fill_color.color,
                                 target[2]["rect_fill_color"])
            finally:
                editor.close()
                settings_window.close()

    def test_curved_arrow_is_a_valid_option_with_curved_geometry(self):
        """曲线箭头是箭头样式的附加选项，默认样式与既有样式不受影响。"""
        from config.config_manager import validate

        self.assertEqual(validate({"arrow_style": "curve_filled"})["arrow_style"],
                         "curve_filled")
        self.assertEqual(validate({"arrow_style": "curve_open"})["arrow_style"],
                         "curve_open")
        self.assertEqual(validate({"arrow_style": "curve_open_dashed"})["arrow_style"],
                         "curve_open_dashed")
        points = curved_arrow_polygon(QPointF(0, 0), QPointF(200, 0), 4, "right")
        self.assertIsNotNone(points)
        self.assertGreater(len(points), 10)
        ys = [point.y() for point in points]
        self.assertGreater(max(ys), 0, "向右弯应偏离起止直线的一侧")
        left = curved_arrow_polygon(QPointF(0, 0), QPointF(200, 0), 4, "left")
        self.assertIsNotNone(left)
        self.assertLess(min(point.y() for point in left), 0, "向左弯应落在另一侧")
        self.assertLess(max(point.y() for point in left), max(ys))
        item = shape("arrow", QPointF(0, 0), QPointF(200, 0), "#ff0000", 4,
                     "curve_filled")
        self.assertGreater(item.path().elementCount(), 20)
        self.assertEqual(item.brush().style(), Qt.SolidPattern)
        self.assertEqual(item.arrow_style, "curve_filled")
        self.assertEqual(item.arrow_curve_direction, "right")
        left_item = shape("arrow", QPointF(0, 0), QPointF(200, 0), "#ff0000", 4,
                          "curve_filled", arrow_curve_direction="left")
        self.assertEqual(left_item.arrow_curve_direction, "left")
        self.assertNotEqual(left_item.path().boundingRect(), item.path().boundingRect())
        # 退化输入（起止点重合）不产生异常，也不产生可见路径。
        degenerate = shape("arrow", QPointF(0, 0), QPointF(0, 0), "#ff0000", 4,
                           "curve_filled")
        self.assertEqual(degenerate.path().elementCount(), 0)

    def test_preset_creates_single_undo_point(self):
        """一次套用样式预设（多个键）只产生一个撤销点。"""
        from config.config_manager import DEFAULTS
        from editor.annotation_items import shape

        editor = EditorWindow(Image.new("RGB", (300, 200), "white"), dict(DEFAULTS))
        try:
            canvas = editor.canvas
            rect = shape("rect", QPointF(20, 20), QPointF(120, 90), "#ff0000", 2)
            canvas.scene_data.addItem(rect)
            rect.setSelected(True)
            canvas.checkpoint()
            before = len(canvas.history)
            editor.toolbar.set_tool_mode("rect")
            target = next(item for item in presets_for("rect")
                          if item[2].get("rect_fill_enabled"))
            editor.toolbar.preset_combo.setCurrentIndex(
                editor.toolbar.preset_combo.findData(target[0]))
            self.app.processEvents()
            self.assertEqual(len(canvas.history), before + 1, "套用预设应只记一个撤销点")
            self.assertEqual(rect.pen().color().name(), target[2]["rect_color"])
            canvas.undo()
            self.assertEqual(len(canvas.history), before + 1)
            self.assertEqual(canvas.cursor_index, before - 1)
        finally:
            editor.close()

    def test_toolbar_preset_row_is_at_top_and_hidden_without_presets(self):
        """「样式预设」必须是参数面板第一行；没有预设的工具应隐藏该行。"""
        from config.config_manager import DEFAULTS
        from editor.toolbar_widget import ToolbarWidget

        toolbar = ToolbarWidget(settings=dict(DEFAULTS))
        try:
            panel = toolbar._options_panel_widget()
            outer = panel.layout()
            self.assertIs(outer.itemAt(0).widget(), toolbar.preset_row)
            toolbar.set_tool_mode("rect")
            self.assertFalse(toolbar.preset_row.isHidden(), "矩形应显示样式预设")
            toolbar.set_tool_mode("picker")
            self.assertTrue(toolbar.preset_row.isHidden(), "取色没有样式预设，应隐藏该行")
        finally:
            toolbar.close()

    def test_settings_page_preset_below_preview_and_applies(self):
        """设置页每个工具的样式预设位于该工具预览下方；套用后写配置、手动改回自定义。"""
        with tempfile.TemporaryDirectory() as folder:
            manager = ConfigManager(Path(folder) / "settings.json")
            window = SettingsWindow(manager)
            try:
                page = window.page("编辑器")
                for tool in ("rect", "ellipse", "pen", "marker", "arrow", "text",
                             "mosaic", "eraser", "crop"):
                    self.assertIn(tool, page._preset_combos, tool)
                self.assertIn("rect", page._preset_combos)
                combo = page._preset_combos["rect"]
                form = combo.parentWidget().layout()
                preview = next(item for item in page.previews
                               if getattr(item, "kind", None) == "rect")
                self.assertGreater(form.indexOf(combo), form.indexOf(preview),
                                   "预设下拉必须在预览下方")
                target = next(item for item in presets_for("rect")
                              if item[2].get("rect_fill_enabled"))
                pen_before = manager.data["pen_color"]
                combo.setCurrentIndex(combo.findData(target[0]))
                self.assertEqual(manager.data["rect_color"], target[2]["rect_color"])
                self.assertEqual(manager.data["pen_color"], pen_before,
                                 "矩形预设不得改写画笔颜色")
                self.assertEqual(combo.currentData(), target[0])
                page.update_value("rect_color", "#00a000")
                self.assertEqual(combo.currentData(), "custom")
            finally:
                window.close()

    def test_preset_keys_stay_inside_their_tool_namespace(self):
        """预设只能写自己的工具键；跨工具键会污染别的标注/设置。"""
        from config.config_manager import DEFAULTS

        allowed = {
            "rect": {"rect_color", "rect_width", "rect_style", "rect_corner_enabled",
                     "rect_corner_radius", "rect_fill_enabled", "rect_fill_opacity",
                     "rect_fill_color"},
            "ellipse": {"ellipse_color", "ellipse_width", "ellipse_style",
                        "ellipse_fill_enabled", "ellipse_fill_opacity", "ellipse_fill_color"},
            "pen": {"pen_color", "pen_width"},
            "marker": {"marker_color", "marker_width", "marker_opacity"},
            "arrow": {"arrow_color", "arrow_style", "arrow_width"},
            "text": {"font_size", "text_color", "text_bold", "text_background_enabled",
                     "text_background", "text_alignment"},
            "mosaic": {"mosaic_mode", "mosaic_size", "mosaic_brush", "mosaic_width"},
            "eraser": {"eraser_width", "eraser_brush", "eraser_erase_base"},
            "crop": {"crop_color", "crop_width"},
        }
        for tool, presets in ANNOTATION_PRESETS.items():
            for preset_id, label, values in presets:
                with self.subTest(tool=tool, preset=preset_id):
                    self.assertTrue(set(values) <= allowed[tool],
                                    f"{tool}/{label} 写了别的工具键: {set(values) - allowed[tool]}")
                    for key in values:
                        self.assertIn(key, DEFAULTS)

    def test_preset_combo_follows_external_setting_sync(self):
        """外部直接 sync_setting 改配置后，预设下拉要回到“自定义”而不是显示旧预设。"""
        from config.config_manager import DEFAULTS
        from editor.toolbar_widget import ToolbarWidget

        settings = dict(DEFAULTS)
        toolbar = ToolbarWidget(settings=settings)
        try:
            toolbar.set_tool_mode("rect")
            target = next(item for item in presets_for("rect")
                          if item[2].get("rect_fill_enabled"))
            toolbar.preset_combo.setCurrentIndex(toolbar.preset_combo.findData(target[0]))
            self.assertEqual(toolbar.preset_combo.currentData(), target[0])
            settings["rect_color"] = "#00a000"
            settings["rect_fill_color"] = "#00a000"
            toolbar.sync_setting("rect_color", "#00a000")
            self.assertEqual(toolbar.preset_combo.currentData(), "custom")
        finally:
            toolbar.close()
