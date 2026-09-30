"""全局界面外观设置。"""

from ui.widgets.tooltip import SettingsPage


class AppearancePage(SettingsPage):
    """管理影响所有应用窗口的主题偏好。"""

    def __init__(self, config, changed):
        super().__init__(config, changed)
        self.group("应用主题")
        self.choice(
            "theme", "颜色模式",
            [("跟随系统", "system"), ("明亮", "light"), ("黑暗", "dark")],
            "跟随 Windows 当前应用颜色模式，或固定使用明亮/黑暗主题。",
        )