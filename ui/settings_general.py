"""启动和通知设置。"""

from ui.widgets.tooltip import SettingsPage


class GeneralPage(SettingsPage):
    """管理启动行为和通知偏好。"""

    def __init__(self, config, changed):
        super().__init__(config, changed)
        self.group("启动与通知")
        self.check("start_on_boot", "开机自动启动", "登录 Windows 后自动运行 ScreenSnap")
        self.check("bubble", "托盘气泡", "总开关：显示系统托盘提示")
        self.choice("notification_backend", "通知方式",
                [("Win11 Toast", "win11toast"), ("旧版应用通知", "legacy")],
                "默认使用 Windows 11 原生 Toast；选择旧版时使用应用内图片预览和系统托盘文字通知。\n"
                "Win11 Toast 不可用或发送失败时会自动回退到旧版通知。")
        self.check("capture_notification", "截图完成通知", "选区确认后显示包含截图缩略图的提示")
        self.number("notification_timeout", "通知自动关闭时长 (秒)", 0, 60,
                    "截图完成与保存成功提示的自动关闭时间；0 表示不自动关闭，需点击关闭")
        self.check("save_notification", "保存成功通知", "图片保存成功后显示托盘提示")
        self.check("open_notification_file", "点击通知定位文件",
                   "点击带已保存图片路径的通知后，在资源管理器中打开文件所在目录并选中该图片；关闭后仍显示通知，但不再打开目录。")
        self.check("sticker_notification", "贴图通知", "创建或粘贴贴图后显示托盘提示")
        self.check("sound", "完成音效", "完成截图时播放提示音")