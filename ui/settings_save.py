"""自动与手动保存目录配置。"""

from PySide6.QtWidgets import QLabel

from ui.widgets.file_path_edit import FilePathEdit
from ui.widgets.tooltip import SettingsPage


class SaveOutputPage(SettingsPage):
    """设置输出图像外观，以及自动与手动保存行为。"""

    def __init__(self, config, changed):
        super().__init__(config, changed)
        self.group("输出图像外观")
        self.preview("output", 120)
        self.check("editor_image_round_corners", "输出透明圆角",
                   "将圆角外像素设为透明；截图选区预览、编辑器预览和最终输出共用此设置")
        self.number("editor_image_corner_radius", "输出圆角半径 (px)", 0, 100,
                    "0 表示直角；圆角会应用于截图、保存、复制和贴图输出")
        self.check("editor_image_border_enabled", "输出图像添加边框",
                   "边框写入最终输出图像")
        self.number("editor_image_border_width", "输出边框宽度 (px)", 0, 20,
                    "边框宽度；0 表示不绘制")
        self.color("editor_image_border_color", "输出边框颜色", "写入最终图像的边框颜色")
        self.check("editor_image_shadow_enabled", "输出图像添加阴影",
                   "阴影写入最终输出图像，阴影会增加输出画布尺寸")
        self.number("editor_image_shadow_size", "阴影模糊尺寸 (px)", 0, 60,
                    "阴影扩散与模糊尺寸；0 表示无扩散")
        self.number("editor_image_shadow_strength", "阴影强度 (%)", 0, 100,
                    "阴影不透明度；0 表示不绘制")
        self.color("editor_image_shadow_color", "输出阴影颜色", "写入最终图像的阴影颜色")
        self.group("自动保存")
        for key, label in [("auto_dir", "自动保存目录"), ("manual_dir", "手动保存目录")]:
            if key == "manual_dir":
                self.group("手动保存")
            widget = FilePathEdit(config.data[key], lambda value, name=key: self.update_value(name, value))
            widget.setToolTip("留空时使用图片文件夹中的 ScreenSnap/Auto 或 ScreenSnap/Manual；\n"
                              "启用归档后，图片会保存在该目录下自动创建的年月或日期子文件夹")
            self.controls[key] = widget
            self.form.addRow(label, widget)
        self.group("图片归档")
        archive_month = self.check(
            "archive_by_month", "按月归档",
            "自动保存和手动保存都按保存月份存入 YYYY-MM 子文件夹；跨月后自动使用新月份目录。")
        archive_day = self.check(
            "archive_by_day", "按日归档",
            "自动保存和手动保存都按保存日期存入 YYYY-MM-DD 子文件夹。开启后会自动关闭按月归档。")
        archive_month.toggled.connect(
            lambda checked: archive_day.setChecked(False) if checked else None)
        archive_day.toggled.connect(
            lambda checked: archive_month.setChecked(False) if checked else None)
        self.group("文件与目录")
        self.choice("save_format", "保存格式", [("PNG（无损）", "png"), ("JPEG", "jpg"),
                                            ("WebP", "webp"), ("BMP", "bmp")],
                    "自动与手动保存使用的图片格式")
        self.number("save_quality", "图片质量", 1, 100,
                    "仅 JPEG 与 WebP 生效；数值越大越清晰，文件也越大")
        self.color("save_background", "保存底色",
                    "JPEG 与 BMP 不支持透明通道，保存时透明区域会先合成到这个颜色")
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