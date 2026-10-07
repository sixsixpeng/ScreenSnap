"""启动和通知设置。"""

from ui.widgets.tooltip import SettingsPage


class GeneralPage(SettingsPage):
    """管理启动行为和通知偏好。"""

    def __init__(self, config, changed):
        super().__init__(config, changed)
        self.group("启动与通知")
        self.check("start_on_boot", "开机自动启动", "登录 Windows 后自动运行 ScreenSnap")
        self.check("bubble", "通知总开关", "关闭后不显示应用内预览、托盘提示和 Windows Toast；完成音效仍由独立开关控制")
        self.choice("notification_backend", "通知方式",
                [("Windows 11 原生通知", "win11toast"), ("旧版应用通知", "legacy")],
                "默认使用 Windows 11 原生通知；选择旧版时使用应用内图片预览和系统托盘文字通知。\n"
                "Windows 11 原生通知不可用或发送失败时会自动回退到旧版通知。")
        self.check("copy_notification", "复制完成通知", "截图复制、复制颜色等操作完成后显示提示")
        self.number("notification_timeout", "通知自动关闭时长 (秒)", 0, 60,
                "应用内图片预览按秒数关闭，托盘提示使用毫秒；Win11 原生通知仅支持系统 short/long 时长，无法精确到秒。0 表示应用内预览不自动关闭，Win11 通知使用 long")
        self.check("save_notification", "保存成功通知", "图片保存成功后显示托盘提示")
        self.check("operation_notification", "操作与错误通知",
               "控制启动、快捷键错误、剪贴板状态及其他未单独分类的操作提示")
        self.check("open_notification_file", "点击通知定位文件",
                   "点击带已保存图片路径的通知后，在资源管理器中打开文件所在目录并选中该图片；关闭后仍显示通知，但不再打开目录。")
        self.check("sticker_notification", "贴图通知", "创建或粘贴贴图后显示托盘提示")
        self.check("sound", "完成音效", "截图完成时播放提示音；独立于通知总开关和各类视觉通知，即使关闭通知仍可播放")