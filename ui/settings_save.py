"""自动与手动保存目录配置。"""

from PySide6.QtWidgets import QLabel

from ui.widgets.file_path_edit import FilePathEdit
from ui.widgets.tooltip import SettingsPage


class SavePage(SettingsPage):
    """独立设置自动与手动保存目录、命名和目录打开方式。"""

    def __init__(self, config, changed):
        super().__init__(config, changed)
        self.group("自动保存")
        self.check("auto_save", "完成后自动保存", "双击完成编辑时保存到自动目录")
        for key, label in [("auto_dir", "自动保存目录"), ("manual_dir", "手动保存目录")]:
            if key == "manual_dir":
                self.group("手动保存")
            widget = FilePathEdit(config.data[key], lambda value, name=key: self.update_value(name, value))
            widget.setToolTip("留空时使用图片文件夹中的 ScreenSnap/Auto 或 ScreenSnap/Manual")
            self.form.addRow(label, widget)
        self.group("文件与目录")
        self.text("filename", "文件名模板", "使用 strftime 时间占位符生成文件名")
        hint = QLabel("%Y 四位年    %m 月    %d 日\n"
                  "%H 时（24 小时制）    %M 分    %S 秒\n"
                  "_ 等普通字符会原样保留")
        hint.setWordWrap(True)
        self.form.addRow("", hint)
        self.check("open_dir", "保存后打开目录", "每次保存成功后打开资源管理器")