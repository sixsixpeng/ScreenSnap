"""常规、遮罩与截图行为设置。"""

from ui.widgets.tooltip import SettingsPage
from ui.widgets.color_button import ColorButton


class GeneralPage(SettingsPage):
    """管理遮罩外观、截图行为与托盘提示开关。"""

    def __init__(self, config, changed):
        super().__init__(config, changed)
        self.group("启动与通知")
        self.check("start_on_boot", "开机自动启动", "登录 Windows 后自动运行 ScreenSnap")
        self.check("bubble", "托盘气泡", "总开关：显示系统托盘提示")
        self.check("capture_notification", "截图完成通知", "选区确认后显示包含截图缩略图的提示")
        self.check("save_notification", "保存成功通知", "图片保存成功后显示托盘提示")
        self.check("sticker_notification", "贴图通知", "创建或粘贴贴图后显示托盘提示")
        self.check("sound", "完成音效", "完成截图时播放提示音")
        self.group("截图内容")
        self.check("auto_copy", "自动复制图片", "确认编辑后复制到剪贴板")
        self.check("cursor", "捕获鼠标", "在截图原图中包含鼠标指针")
        self.group("定位辅助")
        self.check("magnifier", "实时放大镜", "截图时放大鼠标附近像素")
        self.check("crosshair", "全屏十字线", "在截图遮罩上显示定位辅助线")
        crosshair_color = ColorButton(config.data["crosshair_color"],
                          lambda color: self.update_value("crosshair_color", color))
        crosshair_color.setToolTip("设置截图定位十字线颜色")
        self.controls["crosshair_color"] = crosshair_color
        self.form.addRow("十字线颜色", crosshair_color)
        self.number("crosshair_width", "十字线宽度 (px)", 1, 8,
                "设置截图定位十字线宽度")
        self.group("遮罩与选区")
        self.choice("mask_theme", "遮罩主题", [("深色", "dark"), ("浅色", "light")], "改变截图遮罩颜色")
        self.number("mask_opacity", "遮罩不透明度 (%)", 0, 100, "调整截图时选区外遮罩的不透明程度")
        border_key = "selection_border_color"
        border = ColorButton(config.data[border_key],
                     lambda color: self.update_value(border_key, color))
        border.setToolTip("只改变截图选择框的边框颜色，不影响标注颜色")
        self.controls[border_key] = border
        self.form.addRow("选区边框颜色", border)
        self.choice("anchor_style", "选区锚点", [("边框", "border"), ("填充", "fill")], "调整选区锚点外观")
        self.group("历史")
        self.number("history_limit", "历史图片数量", 1, 10000, "从自动保存目录读取的最大图片数量")