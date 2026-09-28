"""剪贴板内容贴图的渲染样式。"""

from PySide6.QtGui import QFontDatabase

from ui.widgets.tooltip import SettingsPage


class ClipboardPage(SettingsPage):
    """文字、颜色与文件贴出后的卡片样式。"""

    def __init__(self, config, changed):
        super().__init__(config, changed)
        self.group("文字贴图")
        fonts = QFontDatabase.families()
        self.choice("text_sticker_font", "字体", [("系统默认", "")] + [(font, font) for font in fonts],
                    "复制文字后贴出的便签使用的字体")
        self.number("text_sticker_font_size", "字号", 6, 72, "便签文字的字号，同时影响卡片内边距")
        self.color("text_sticker_color", "文字颜色", "便签文字颜色")
        self.color("text_sticker_background", "背景颜色", "便签背景颜色，可使用深色底浅色字")
        self.number("text_sticker_width", "最大宽度", 160, 1600, "超过该宽度会自动折行")
        self.number("text_sticker_lines", "最大行数", 1, 500, "超出部分会被截断，避免超长文本生成巨大图片")
        self.check("clipboard_color_detection", "识别颜色值",
                   "复制 #RGB、#RRGGBB 或裸六位十六进制时贴出色块而不是这段文字；"
                   "关闭后这类内容一律按文字贴出")
        self.group("颜色贴图")
        self.check("color_sticker_value", "标注色值", "在颜色卡片左下角显示十六进制色值")
        self.number("color_sticker_width", "色块宽度", 80, 800, "颜色贴图的卡片宽度")
        self.number("color_sticker_height", "色块高度", 60, 600, "颜色贴图的卡片高度")
        self.group("文件贴图")
        self.check("file_sticker_path", "显示所在目录", "在文件名下方用小字显示文件所在目录")
        self.number("file_sticker_max", "最多展示", 1, 20, "一次复制多个文件时卡片最多列出的条目数，其余只统计数量")
        self.number("file_sticker_width", "卡片宽度", 240, 800, "文件贴图的卡片宽度，影响文件名可用长度")
