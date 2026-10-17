"""启动和通知设置。"""

from config.config_manager import DEFAULTS
from ui.widgets.tooltip import SettingsPage

# 提示音选项（2026-10-11）：系统音 + 16 个随包内置音 + 自定义 wav。
# 用户要求：星号与感叹号在 Windows 里是**同一个 wav** ⇒ 合并；问号默认**未分配**（不发声）⇒ 去掉；
# 两者的历史取值仍被 validate/resolve_kind 接受（合并到 asterisk / 回落到 default），老配置不受影响。
# 默认全部使用内置音（不依赖系统声音方案），且各类默认音互不相同。
SOUND_OPTIONS = [("默认提示音（系统）", "default"),
                 ("星号 / 感叹号（系统，两者是同一个音）", "asterisk"),
                 ("关键停止（系统）", "hand"),
                 ("内置 1（单声）", "builtin1"), ("内置 2（上行两声）", "builtin2"),
                 ("内置 3（下行两声）", "builtin3"), ("内置 4（三连音）", "builtin4"),
                 ("内置 5（高频上行）", "builtin5"), ("内置 6（下行三声）", "builtin6"),
                 ("内置 7（柔和低音）", "builtin7"), ("内置 8（四连上行）", "builtin8"),
                 ("内置 9（双音和音）", "builtin9"), ("内置 10（双滴提示）", "builtin10"),
                 ("内置 11（上行三连）", "builtin11"), ("内置 12（双音下行）", "builtin12"),
                 ("内置 13（铃音）", "builtin13"), ("内置 14（轻快三连）", "builtin14"),
                 ("内置 15（低音双滴）", "builtin15"), ("内置 16（三音和弦）", "builtin16"),
                 ("内置 17（双音上行（中））", "builtin17"), ("内置 18（下行三声（中））", "builtin18"),
                 ("内置 19（铃音双声）", "builtin19"), ("内置 20（三滴提示）", "builtin20"),
                 ("内置 21（低沉单声）", "builtin21"), ("内置 22（四连下行）", "builtin22"),
                 ("内置 23（低音和音）", "builtin23"), ("内置 24（上行四连）", "builtin24"),
                 ("内置 25（高音双滴）", "builtin25"), ("内置 26（低音和弦）", "builtin26"),
                 ("内置 27（轻快双跳）", "builtin27"), ("内置 28（中音下行）", "builtin28"),
                 ("内置 29（柔和三连）", "builtin29"), ("内置 30（收尾成功音）", "builtin30"),
                 ("自定义 wav", "custom")]


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
        self.radios("notification_backend", "通知方式",
                [("Windows 11 原生通知", "win11toast"), ("旧版应用通知", "legacy")],
                "默认使用「旧版应用通知」：应用内图片预览 + 系统托盘文字通知，必定带缩略图。\n"
                "选择「Windows 11 原生通知」时走系统通知中心；\n"
                "原生通知不可用或发送失败会自动回退到旧版通知。")
        self._notification_identity_row()
        self.check("copy_notification", "复制完成通知", "截图复制、复制颜色等操作完成后显示提示")
        self.check("picker_notification", "取色通知", "取色复制色值后显示提示")
        self.number("notification_timeout", "通知自动关闭时长 (秒)", 0, 60,
                "应用内图片预览按秒数关闭；托盘提示使用毫秒。\n"
                "Win11 原生通知只支持系统的 short/long 时长，无法精确到秒。\n"
                "0 表示应用内预览不自动关闭、Win11 通知使用系统默认时长。")
        self.check("save_notification", "保存成功通知", "图片保存成功后显示托盘提示")
        self.check("operation_notification", "操作与错误通知",
               "控制启动、快捷键错误、剪贴板状态及其他未单独分类的操作提示")
        self.check("open_notification_file", "点击通知定位文件",
                   "点击带已保存图片路径的通知后，在资源管理器中打开文件所在目录并选中该图片；关闭后仍显示通知，但不再打开目录。")
        self.check("sticker_notification", "贴图通知", "创建或粘贴贴图后显示托盘提示")
        self.check("sound", "完成音效", "截图完成时播放提示音；独立于通知总开关和各类视觉通知，即使关闭通知仍可播放")
        # 通知音效（2026-10-11）：全局默认音 + 可选自定义 wav + 每类通知可单独覆盖。
        # 每个下拉**同行**配一个「试听」，听的就是该行实际会响的音（分类有覆盖听覆盖，空则听全局）。
        self._sound_choice("notification_sound", "提示音", SOUND_OPTIONS,
                "全局默认提示音，每行「试听」听的就是该行实际会响的音。\n"
                "· 默认提示音：Windows 声音方案里的「默认提示音」。\n"
                "· 星号 / 感叹号 / 问号 / 关键停止：系统声音里的同名事件，可在\n"
                "  「Windows 设置 > 声音 > 更多声音设置」换成任意 wav。\n"
                "  注意：Windows 默认把星号与感叹号指向同一个 wav、问号常常未分配，\n"
                "  所以听起来一样或没有声音 —— 这是系统设置，不是程序问题。\n"
                "· 内置 1-4：随包分发的 4 个短音，与系统方案无关，四个必定互不相同。\n"
                "· 自定义 wav：使用下面的文件。", category=None)
        self.text("notification_sound_file", "自定义声音文件",
                  "仅支持 .wav（用系统播放器，不引入额外依赖）。\n"
                  "文件被删除或路径失效时自动回落「默认提示音」，不影响通知本身；\n"
                  "留空同样按默认提示音处理。")
        for key, label, category in (("sound_copy", "仅复制", "copy_notification"),
                                     ("sound_save", "保存", "save_notification"),
                                     ("sound_sticker", "贴图", "sticker_notification"),
                                     ("sound_picker", "取色", "picker_notification"),
                                     ("sound_operation", "其它操作", "operation_notification")):
            self._sound_choice(key, label + "提示音",
                    [("跟随全局", "")] + SOUND_OPTIONS,
                    "留空表示跟随上面的全局提示音；\n"
                    "选择具体音色则只覆盖「" + label + "」这一类通知。\n"
                    "右侧「试听」听的就是这一行实际会响的音。", category=category)

    def _sound_choice(self, key, label, options, help_text, category=None):
        """提示音下拉 + **同一行**的「试听」按钮（2026-10-11 用户要求：每行一个试听，明确在听谁）。

        试听播放的是该行**实际会响**的音：分类行选了具体音色就听它，选「跟随全局」则听全局提示音；
        全局行直接听全局设置（custom 时用自定义文件，文件失效回落默认）。
        """
        from PySide6.QtWidgets import QComboBox, QHBoxLayout, QPushButton, QStyle, QWidget

        combo = QComboBox()
        for name, value in options:
            combo.addItem(name, value)
        combo.setCurrentIndex(combo.findData(self.config.data[key]))
        button = QPushButton("试听")
        # 项目约定：可见按钮必须同时有文字和图标（tests/editor/test_text 会检查）。
        button.setIcon(self.style().standardIcon(QStyle.SP_MediaPlay))
        button.setToolTip("试听这一行实际会响的音：本行选了具体音色就听它，选「跟随全局」则听全局提示音；\n"
                          "不受「完成音效」总开关限制（试听就是要能听到）。")
        button.clicked.connect(lambda checked=False, c=category: self._preview_sound(c))
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        row.addWidget(combo)
        row.addWidget(button)
        row.addStretch()
        container = QWidget()
        container.setLayout(row)
        # 下拉选项较多，只报数量；默认项按实际数据反查，避免写死音色名。
        index = combo.findData(DEFAULTS.get(key))
        default_name = combo.itemText(index) if index >= 0 else "跟随全局"
        combo.setToolTip(f"{help_text}\n取值：下拉列表中的 {combo.count()} 个选项，默认{default_name}")
        self.controls[key] = combo
        self.form.addRow(label, container)
        combo.currentIndexChanged.connect(
            lambda _index, k=key, w=combo: self.update_value(k, w.currentData()))
        return combo

    def _preview_sound(self, category=None):
        """播放某个提示音控件对应的音（与实际通知共用同一套解析规则）。

        category 为 None ⇒ 听全局 notification_sound；否则先看 sound_<category>，为空再跟随全局。
        """
        from core.sound import play_sound, resolve_kind

        key = {"copy_notification": "sound_copy", "save_notification": "sound_save",
               "sticker_notification": "sound_sticker", "picker_notification": "sound_picker",
               "operation_notification": "sound_operation"}.get(category or "", "")
        kind = self.config.data.get(key, "") if key else ""
        if not kind:
            kind = self.config.data.get("notification_sound", "default") or "default"
        if kind == "custom":
            resolved, target = resolve_kind("custom", self.config.data.get("notification_sound_file", ""))
            return play_sound(resolved, target)
        return play_sound(kind)

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
