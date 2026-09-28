"""自动与手动保存目录配置。"""

from PySide6.QtWidgets import QLabel

from ui.widgets.file_path_edit import FilePathEdit
from ui.widgets.tooltip import SettingsPage


class SavePage(SettingsPage):
    """独立设置自动与手动保存目录、命名和目录打开方式。"""

    def __init__(self, config, changed):
        super().__init__(config, changed)
        self.group("自动保存")
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
        self.group("手动保存后复制")
        self.check("copy_saved_image", "图片", "双击保存和保存按钮将合成图片放入剪贴板")
        self.check("copy_saved_path", "文件路径文字", "双击保存和保存按钮将保存路径作为文字放入剪贴板")