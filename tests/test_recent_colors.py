"""最近颜色：单池归一化、配置生命周期与 ColorButton 记录路径。"""

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from tests.base import CoreTests, Mock, unittest

from core.recent_colors import (DEFAULT_RECENT_COLORS, MAX_RECENT_COLORS,
                                normalize_color, normalize_colors, push_recent)


class RecentColorsTests(CoreTests):
    def test_normalize_and_push(self):
        self.assertEqual(normalize_color("#ABC"), "#aabbcc")
        self.assertEqual(normalize_color("#12AbEf"), "#12abef")
        self.assertEqual(normalize_color("red"), "")
        self.assertEqual(normalize_color(None), "")
        self.assertEqual(normalize_colors(["#ff0000", "#FF0000", "bad", "#00ff00"]),
                         ["#ff0000", "#00ff00"])
        updated = push_recent(["#ff0000", "#00ff00"], "#00ff00")
        self.assertEqual(updated, ["#00ff00", "#ff0000"])
        self.assertEqual(push_recent(["#ff0000"], "bad"), ["#ff0000"])
        many = push_recent([], "#%06x" % 0x123456)
        self.assertEqual(many, ["#123456"])
        self.assertEqual(len(normalize_colors(
            ["#%06x" % index for index in range(40)])), MAX_RECENT_COLORS)

    def test_config_lifecycle(self):
        from config.config_manager import DEFAULTS, PRESERVED_ON_VERSION_RESET
        from config.config_manager import repair, validate

        self.assertEqual(DEFAULTS["recent_colors"], list(DEFAULT_RECENT_COLORS))
        self.assertIn("recent_colors", PRESERVED_ON_VERSION_RESET)
        self.assertEqual(validate({"recent_colors": ["#ff0000", "#00ff00"]})[
            "recent_colors"], ["#ff0000", "#00ff00"])
        for invalid in ("#ff0000", ["red"], ["#ff0000", "#ff0000"],
                        ["#%06x" % index for index in range(MAX_RECENT_COLORS + 1)]):
            with self.subTest(value=invalid):
                with self.assertRaises(ValueError):
                    validate({"recent_colors": invalid})
        repaired, dropped = repair({"recent_colors": ["red"]})
        self.assertEqual(repaired["recent_colors"], list(DEFAULT_RECENT_COLORS))
        self.assertIn("recent_colors", dropped)

    def test_color_button_uses_shared_pool_and_records(self):
        from ui.widgets.color_button import (ColorButton, record_recent_color,
                                             set_recent_color_recorder,
                                             set_recent_colors_provider)

        recorded = []
        set_recent_colors_provider(lambda: ["#ff0000", "#00ff00"])
        set_recent_color_recorder(recorded.append)
        changed = []
        button = ColorButton("#ff0000", changed.append)
        try:
            self.assertIn("最近使用的颜色", button.toolTip())
            button.use_recent_color("#00ff00")
            self.assertEqual(button.color, "#00ff00")
            self.assertEqual(changed, ["#00ff00"])
            self.assertEqual(recorded, ["#00ff00"])
            record_recent_color("#123456")
            self.assertEqual(recorded[-1], "#123456")
        finally:
            button.close()
            set_recent_colors_provider(None)
            set_recent_color_recorder(None)

    def test_application_remembers_color_and_persists(self):
        from types import SimpleNamespace
        from config.config_manager import DEFAULTS
        from main import Application

        config = SimpleNamespace(data=dict(DEFAULTS, recent_colors=["#ff0000"]),
                                 save=Mock())
        app = Application.__new__(Application)
        app.config = config
        app.logger = Mock()
        app.remember_recent_color("#00ff00")
        self.assertEqual(config.data["recent_colors"][0], "#00ff00")
        config.save.assert_called_once()
        app.remember_recent_color("not-a-color")
        self.assertEqual(config.data["recent_colors"][0], "#00ff00")

    def test_color_button_keeps_settings_page_tooltip(self):
        """设置页颜色按钮的说明文案不能被右键提示覆盖（只能追加）。"""
        from ui.widgets.color_button import ColorButton

        button = ColorButton("#ff0000", lambda _value: None)
        try:
            button.setToolTip("矩形边框颜色（填充色见下一项，两者各自独立）。")
            button.set_color("#00ff00")
            tip = button.toolTip()
            self.assertIn("矩形边框颜色", tip)
            self.assertIn("右键可选择最近使用的颜色", tip)
        finally:
            button.close()
