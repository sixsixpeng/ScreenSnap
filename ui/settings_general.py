"""启动和通知设置。"""

from ui.widgets.tooltip import SettingsPage


class GeneralPage(SettingsPage):
    """管理启动行为和通知偏好。"""

    def __init__(self, config, changed):
        super().__init__(config, changed)
        self.group("版本与数据")
        self.text("app_version", "程序版本号",
                  "内置版本号会记录在这里（可在设置里修改）。启动时如果记录的版本与内置版本不一致，\n"
                  "会先清空整个用户数据目录（配置、贴图会话、剪贴板历史、缓存）再启动，并给出提示 ——\n"
                  "因此改动此值等同于主动触发一次数据重置；留空表示首次运行，会直接写入内置版本。\n"
                  "当前内置版本见 core/version.py 的 APP_VERSION。")
        self.group("启动与通知")
        self.check("start_on_boot", "开机自动启动", "登录 Windows 后自动运行 ScreenSnap")
        self.check("bubble", "通知总开关", "关闭后不显示应用内预览、托盘提示和 Windows Toast；完成音效仍由独立开关控制")
        self.choice("notification_backend", "通知方式",
                [("Windows 11 原生通知", "win11toast"), ("旧版应用通知", "legacy")],
                "默认使用 Windows 11 原生通知；选择旧版时使用应用内图片预览和系统托盘文字通知。\n"
                "Windows 11 原生通知不可用或发送失败时会自动回退到旧版通知。")
        self._notification_identity_row()
        self.check("copy_notification", "复制完成通知", "截图复制、复制颜色等操作完成后显示提示")
        self.check("picker_notification", "取色通知", "取色复制色值后显示提示")
        self.number("notification_timeout", "通知自动关闭时长 (秒)", 0, 60,
                "应用内图片预览按秒数关闭，托盘提示使用毫秒；Win11 原生通知仅支持系统 short/long 时长，无法精确到秒。0 表示应用内预览不自动关闭，Win11 通知使用系统默认时长。")
        self.check("save_notification", "保存成功通知", "图片保存成功后显示托盘提示")
        self.check("operation_notification", "操作与错误通知",
               "控制启动、快捷键错误、剪贴板状态及其他未单独分类的操作提示")
        self.check("open_notification_file", "点击通知定位文件",
                   "点击带已保存图片路径的通知后，在资源管理器中打开文件所在目录并选中该图片；关闭后仍显示通知，但不再打开目录。")
        self.check("sticker_notification", "贴图通知", "创建或粘贴贴图后显示托盘提示")
        self.check("sound", "完成音效", "截图完成时播放提示音；独立于通知总开关和各类视觉通知，即使关闭通知仍可播放")

    def _notification_identity_row(self):
        """一次性环境动作（规则 4：不做成常驻配置项）：注册 Windows 通知身份。

        Windows 只在系统里存在「带 ScreenSnap AUMID 的开始菜单快捷方式」时，才允许原生通知使用
        该身份，并在「设置 → 系统 → 通知」里列出独立条目（可单独静音）。首次运行会自动创建；
        这里显示当前状态并提供手动（重新）注册入口。
        """
        from PySide6.QtWidgets import (QHBoxLayout, QLabel, QPushButton, QStyle,
                                       QWidget)

        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        button = QPushButton("注册通知身份")
        # 本项目的约定：可见按钮必须同时有文字和图标（tests/editor/test_text 会检查）。
        button.setIcon(self.style().standardIcon(QStyle.SP_DialogApplyButton))
        button.setToolTip("在开始菜单创建带 ScreenSnap 应用标识的快捷方式。\n"
                          "注册后 Windows「设置 → 系统 → 通知」会出现独立的 ScreenSnap 条目，可单独开关；\n"
                          "未注册时通知照常弹出，只是系统里显示为 Python。首次运行会自动注册。")
        status = QLabel()
        button.clicked.connect(lambda checked=False: self._register_identity(status))
        layout.addWidget(button)
        layout.addWidget(status, 1)
        self.form.addRow("Windows 通知身份", row)
        self._refresh_identity_status(status)

    def _refresh_identity_status(self, status):
        from core import app_identity

        if not app_identity.is_supported():
            status.setText("当前系统不支持")
        elif app_identity.read_app_id(app_identity.shortcut_path()):
            status.setText("已注册（系统里已有独立入口）")
        else:
            status.setText("未注册（通知仍可用，但系统里没有独立条目）")
        return status

    def _register_identity(self, status):
        from core import app_identity

        state = app_identity.ensure_registered()
        self._refresh_identity_status(status)
        if state not in ("created", "exists"):
            status.setText("注册失败（详见日志；通知仍可用）")
