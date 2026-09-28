"""贴图外观设置页。"""

from ui.widgets.color_button import ColorButton
from ui.widgets.tooltip import SettingsPage


class StickerPage(SettingsPage):
    """配置新贴图默认描边与阴影效果。"""

    def __init__(self, config, changed):
        super().__init__(config, changed)
        self.color_buttons = {}
        self.group("贴图外观")
        self.check("sticker_border_enabled", "默认开启描边", "新建贴图默认显示外圈描边；单个贴图可在右键菜单单独切换")
        self.color("sticker_border_color", "描边颜色", "设置新建贴图默认描边颜色")
        self.number("sticker_border_width", "描边宽度", 0, 20, "设置新建贴图默认描边宽度，0 表示不绘制描边")
        self.check("sticker_shadow_enabled", "默认显示阴影", "新建贴图默认显示阴影；单个贴图可在右键菜单单独切换")
        self.color("sticker_shadow_color", "阴影颜色", "设置新建贴图默认阴影颜色")
        self.number("sticker_shadow_strength", "阴影强度", 0, 100, "设置新建贴图默认阴影强度，0 表示不绘制阴影")

    def color(self, key, label, help_text):
        button = ColorButton(self.config.data[key], lambda color, current=key: self.update_value(current, color))
        button.setToolTip(help_text)
        self.controls[key] = button
        self.color_buttons[key] = button
        self.form.addRow(label, button)
        return button

