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
        self.group("截图时机")
        delay = self.number("capture_delay", "截图延迟 (ms)", 0, 5000,
                            "按下截图快捷键后等待多少毫秒再抓取画面，0 表示立即截图；\n"
                            "用于避开快捷键冲突导致的界面变化，或等待菜单、动画、网页渲染完成；\n"
                            "每 1000 毫秒约等于 1 秒，托盘菜单发起的截图会自动再加 150 毫秒")
        delay.setSingleStep(100)
        self.group("截图内容")
        self.check("cursor", "捕获鼠标", "把鼠标指针画进截图原图；系统光标无法读取时，开关前后结果可能相同")
        self.group("截图后")
        self.check("inline_edit", "原地编辑", "截图后直接在原位置标注；关闭后改为打开独立编辑器窗口")
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
        self.choice("mask_theme", "遮罩主题", [("深色", "dark"), ("浅色", "light")],
                    "选区之外区域的明暗：深色压暗背景突出选区，浅色适合浅色背景")
        self.number("mask_opacity", "遮罩不透明度 (%)", 0, 100,
                    "选区之外区域的压暗程度；0 为完全透明，100 为完全遮挡")
        self.check("window_detection", "识别窗口与控件",
                   "截图时按 Tab 把选区切到鼠标下的窗口、分组容器或控件；\n"
                   "自绘界面（浏览器、Electron）没有子窗口句柄，只能识别到顶层窗口")
        self.check("window_auto_select", "自动选中窗口",
                   "开始截图时直接选中鼠标下的窗口，无需按 Tab；关闭后只在按 Tab 时生效")
        self.check("window_hover_detect", "移动鼠标自动识别",
                   "鼠标移动时持续识别下方的窗口或控件并高亮可截图区域，\n"
                   "左键单击即可选中高亮区域，按住拖动仍然手绘选区；\n"
                   "想选外层窗口或父容器时按 Tab 逐层切换")
        self.check("window_uia_detect", "优先用无障碍识别 (UIA)",
                   "默认开启：截图识别会先用 Windows 无障碍树识别，能读到浏览器网页控件、\n"
                   "WPF、Qt、Electron 等自绘界面内部的按钮、标签、编辑框；\n"
                   "需要安装 uiautomation 库（pip install uiautomation），\n"
                   "没装或识别不到时自动退回窗口句柄识别，不会让识别失效；\n"
                   "关闭时只按窗口句柄识别，速度更快但读不到自绘界面内部")
        self.number("window_hover_interval", "悬停识别刷新间隔 (ms)", 16, 500,
                    "鼠标移动时刷新悬停高亮的间隔；数值越小刷新越快、越跟手（灵敏度越高），\n"
                    "但更频繁地调用系统识别接口；开启 UIA 时该间隔会自动翻倍以兼容较慢查询。\n"
                    "范围 16（最跟手）到 500（最省资源）毫秒")
        self.number("element_depth", "识别层级", 1, 8,
                    "从命中的最内层控件往上保留多少层祖先容器（窗口、分组、面板等），\n"
                    "供 Tab 逐层切换；最内层的小控件（按钮、列表项）始终会保留，不受此值影响。\n"
                    "数值越大，Tab 能切换到的外层容器越多；1 只保留最内层控件本身，\n"
                    "8 为上限。是否能读到小控件取决于无障碍识别本身，和这个值无关")
        border_key = "selection_border_color"
        border = ColorButton(config.data[border_key],
                     lambda color: self.update_value(border_key, color))
        border.setToolTip("只改变截图选择框的边框颜色，不影响标注颜色")
        self.controls[border_key] = border
        self.form.addRow("选区边框颜色", border)
        self.choice("anchor_style", "选区锚点", [("边框", "border"), ("填充", "fill")],
                    "选区四角与四边控制点的外观：边框为空心方块，填充为实心方块")
        self.group("历史")
        self.number("history_limit", "历史图片数量", 1, 10000,
                    "从自动保存目录读取的最大图片数量；\n"
                    "影响 F3 贴图、上一张/下一张轮换以及贴图管理窗口能看到的图片范围")