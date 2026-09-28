"""贴图外观设置页。"""

from ui.widgets.tooltip import SettingsPage


class StickerPage(SettingsPage):
    """配置新贴图默认描边与阴影效果，以及贴图管理窗口的显示方式。"""

    def __init__(self, config, changed):
        super().__init__(config, changed)
        self.group("贴图外观")
        self.check("sticker_border_enabled", "默认开启描边", "新建贴图默认显示外圈描边；单个贴图可在右键菜单单独切换")
        self.color("sticker_border_color", "描边颜色", "设置新建贴图默认描边颜色")
        self.number("sticker_border_width", "描边宽度", 0, 20, "设置新建贴图默认描边宽度，0 表示不绘制描边")
        self.check("sticker_shadow_enabled", "默认显示阴影", "新建贴图默认显示阴影；单个贴图可在右键菜单单独切换")
        self.color("sticker_shadow_color", "阴影颜色", "设置新建贴图默认阴影颜色")
        self.number("sticker_shadow_strength", "阴影强度", 0, 100, "设置新建贴图默认阴影强度，0 表示不绘制阴影")
        self.group("拖动与吸附")
        self.check("sticker_snap_enabled", "拖动时自动吸附",
                   "拖动贴图靠近屏幕工作区边缘或其他窗口边缘时自动对齐；\n"
                   "按住 Alt 拖动可临时取消吸附，用于精细摆放")
        self.number("sticker_snap_threshold", "吸附触发距离", 1, 40,
                    "贴图边缘距离目标多近时才吸附，单位是逻辑像素（与系统缩放无关）；\n"
                    "数值越大越容易吸附，过大时贴图会“黏手”，建议 4 到 12")
        self.choice("sticker_snap_targets", "吸附目标",
                    [("屏幕与工作区边缘", "screen"), ("其他窗口边缘", "window"),
                     ("两者都吸附", "both")],
                    "屏幕：贴到显示器可用区域的四边，任务栏占位会被排除；\n"
                    "窗口：贴到鼠标下方那个窗口的四边（可贴到窗口外侧）；\n"
                    "两者：同时考虑上面两类目标，取距离最近的一个")
        self.check("sticker_follow_window", "吸附到窗口后跟随移动",
                   "贴到某个窗口后，该窗口移动或改变位置时贴图跟着一起走；\n"
                   "窗口关闭会自动解除吸附；也可在贴图右键菜单单独开关跟随")
        self.number("sticker_follow_interval", "跟随刷新间隔", 30, 1000,
                    "跟随开启后每隔多少毫秒读取一次目标窗口位置，单位毫秒；\n"
                    "数值越小跟得越紧但占用略多 CPU，100 到 200 毫秒通常已经跟手")
        self.group("贴图管理窗口")
        self.number("sticker_panel_thumb", "缩略图宽度", 48, 200,
                    "管理窗口中每行缩略图的宽度，高度按 4:3 推导；重新打开窗口后生效")

