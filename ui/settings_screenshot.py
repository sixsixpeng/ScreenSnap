"""截图行为、画面识别和选区设置。"""

from PySide6.QtCore import QSignalBlocker, QRectF, Qt
from PySide6.QtGui import QColor, QKeySequence, QPainter, QPen
from PySide6.QtWidgets import QKeySequenceEdit, QComboBox, QSizePolicy, QWidget

from ui.widgets.color_button import ColorButton
from ui.widgets.tooltip import SettingsPage


def rich_tooltip(*args, **kwargs):
    """延迟导入避免 ui 包与 editor.toolbar_widget 的循环依赖。"""
    from editor.toolbar_widget import rich_tooltip as _rich_tooltip
    return _rich_tooltip(*args, **kwargs)


class ScreenshotEffectPreview(QWidget):
    def __init__(self, config, kind):
        super().__init__()
        self.config = config
        self.kind = kind
        self.setMinimumWidth(260)
        self.setFixedHeight(100)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def refresh(self):
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        area = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        painter.fillRect(area, QColor("#e8edf0"))
        painter.fillRect(area.adjusted(12, 12, -area.width() * 0.62, -12), QColor("#4b91a5"))
        painter.fillRect(area.adjusted(area.width() * 0.46, 12, -12, -12), QColor("#f7f9fa"))
        painter.setPen(QPen(QColor("#b2c0c6"), 1))
        for row in range(3):
            y = int(area.top() + 30 + row * 16)
            painter.drawLine(int(area.left() + area.width() * 0.5), y, int(area.right() - 16), y)
        if self.kind == "assist":
            center = area.center()
            if self.config.data.get("crosshair", True):
                color = QColor(self.config.data.get("crosshair_color", "#ff0000"))
                painter.setPen(QPen(color, self.config.data.get("crosshair_width", 1)))
                painter.drawLine(int(center.x()), int(area.top()), int(center.x()), int(area.bottom()))
                painter.drawLine(int(area.left()), int(center.y()), int(area.right()), int(center.y()))
            if self.config.data.get("magnifier", True):
                lens = QRectF(center.x() - 27, center.y() - 22, 54, 44)
                painter.setPen(QPen(QColor("#273b44"), 2))
                painter.setBrush(QColor(255, 255, 255, 230))
                painter.drawEllipse(lens)
                painter.setPen(QPen(QColor("#e34b5f"), 2))
                painter.drawLine(int(center.x() - 14), int(center.y()), int(center.x() + 14), int(center.y()))
                painter.drawLine(int(center.x()), int(center.y() - 12), int(center.x()), int(center.y() + 12))
        elif self.kind == "hover":
            candidate = area.adjusted(area.width() * 0.28, area.height() * 0.2,
                                      -area.width() * 0.28, -area.height() * 0.24)
            painter.fillRect(area, QColor(0, 0, 0, 153))
            if self.config.data.get("window_hover_fill_mode", "reveal") == "reveal":
                painter.save()
                painter.setClipRect(candidate)
                painter.fillRect(candidate, QColor("#f7f9fa"))
                painter.restore()
            else:
                fill = QColor(self.config.data.get("window_hover_color", "#168CFF"))
                fill.setAlpha(round(self.config.data.get("window_hover_opacity", 35) * 2.55))
                painter.fillRect(candidate, fill)
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(QColor(self.config.data.get("window_hover_border_color", "#168cff")),
                                self.config.data.get("window_hover_border_width", 2)))
            painter.drawRect(candidate)
            badge = QRectF(candidate.left(), candidate.top(), candidate.width() * 0.54, 18)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(self.config.data.get("window_hover_badge_color", "#102A31")))
            painter.drawRoundedRect(badge, 3, 3)
            painter.setPen(QColor(self.config.data.get("window_hover_text_color", "#F4FFFC")))
            painter.drawText(badge, Qt.AlignCenter, "控件 120 x 32")
        else:
            selected = area.adjusted(area.width() * 0.23, area.height() * 0.2,
                                     -area.width() * 0.23, -area.height() * 0.2)
            mask = QColor(self.config.data.get("mask_color", "#000000"))
            mask.setAlpha(round(self.config.data.get("mask_opacity", 60) * 2.55))
            painter.fillRect(area, mask)
            painter.save()
            painter.setClipRect(selected)
            painter.fillRect(selected, QColor("#f7f9fa"))
            painter.restore()
            border = QColor(self.config.data.get("selection_border_color", "#168cff"))
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(border, 2))
            painter.drawRect(selected)
            for point in (selected.topLeft(), selected.topRight(), selected.bottomLeft(), selected.bottomRight()):
                painter.setBrush(border if self.config.data.get("anchor_style") == "fill" else Qt.white)
                painter.drawRect(QRectF(point.x() - 3, point.y() - 3, 7, 7))
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(QColor("#c3ced6"), 1))
        painter.drawRect(area)


HOVER_STYLE_PRESETS = (
    ("海湾青", "lagoon", {
        "window_hover_color": "#0c887b",
        "window_hover_border_color": "#54e0c5",
        "window_hover_text_color": "#f2fffc",
        "window_hover_badge_color": "#103b3a",
        "window_hover_opacity": 17,
        "window_hover_border_width": 2,
        "window_hover_font_size": 12,
    }),
    ("冰蓝", "ice", {
        "window_hover_color": "#286cb3",
        "window_hover_border_color": "#83c9ff",
        "window_hover_text_color": "#f4faff",
        "window_hover_badge_color": "#152e47",
        "window_hover_opacity": 18,
        "window_hover_border_width": 2,
        "window_hover_font_size": 12,
    }),
    ("琥珀", "amber", {
        "window_hover_color": "#a96812",
        "window_hover_border_color": "#ffd17a",
        "window_hover_text_color": "#fff9ee",
        "window_hover_badge_color": "#402b13",
        "window_hover_opacity": 18,
        "window_hover_border_width": 2,
        "window_hover_font_size": 12,
    }),
    ("玫瑰", "rose", {
        "window_hover_color": "#a8385c",
        "window_hover_border_color": "#ff9eb8",
        "window_hover_text_color": "#fff5f8",
        "window_hover_badge_color": "#421d2b",
        "window_hover_opacity": 17,
        "window_hover_border_width": 2,
        "window_hover_font_size": 12,
    }),
)


class ScreenshotPage(SettingsPage):
    """集中管理从触发截图到确认选区的设置。"""

    def __init__(self, config, changed):
        super().__init__(config, changed)
        self.group("截图时机")
        delay = self.number("capture_delay", "截图延迟 (ms)", 0, 5000,
                            "按下截图快捷键后等待多少毫秒再抓取画面，0 表示立即截图；\n"
                            "用于避开快捷键冲突导致的界面变化，或等待菜单、动画、网页渲染完成；\n"
                            "每 1000 毫秒约等于 1 秒，托盘菜单发起的截图会自动再加 150 毫秒")
        delay.setSingleStep(100)
        self.group("截图内容")
        self.check("cursor", "捕获鼠标", "把鼠标指针画进截图原图；系统光标无法读取时，开关前后结果可能相同")
        self.group("截图后")
        self.check("inline_edit", "原地编辑",
                   rich_tooltip("原地编辑", "仅单屏单选区在截图位置编辑；多选区、跨屏选区或关闭此项时使用独立编辑器。"))
        self.choice("capture_after_selection", "截图确认后",
                    [("仅保存，不打开编辑器", "save"), ("进入编辑器", "edit")],
                    rich_tooltip("截图确认后", "Enter、左键双击或确认选区后执行的默认动作；\n"
                                 "右键双击和快速保存始终直接保存，窗口编辑按钮始终可手动打开编辑器。"))
        self.group("截图快捷操作")
        self.check("capture_quick_sticker_enabled", "启用快速贴图快捷键",
                   "选好截图区域后按指定按键立即贴图，不进入编辑")
        self._shortcut("capture_quick_sticker_shortcut", "快速贴图按键", "Space",
                       "选区确认前可按此按键组合直接贴成贴图；支持单个按键或带修饰键的组合")
        self._shortcut("capture_save_shortcut", "快速保存按键", "S",
                       "选区存在时直接保存；沿用输出外观、保存格式、剪贴板和成功通知设置")
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
        self._effect_preview("assist")
        self.group("窗口与控件识别")
        self.check("window_detection", "识别窗口与控件",
                   "截图时按 Tab 把选区切到鼠标下的窗口、分组容器或控件；\n"
                   "自绘界面（浏览器、Electron）没有子窗口句柄，只能识别到顶层窗口")
        self.check("window_auto_select", "自动选中窗口",
                   "开始截图时直接选中鼠标下的窗口，无需按 Tab；关闭后只在按 Tab 时生效")
        self.check("window_hover_detect", "移动鼠标自动识别",
                   "鼠标移动时持续识别下方的窗口或控件并高亮可截图区域，\n"
                   "左键单击即可选中高亮区域，按住拖动仍然手绘选区；\n"
                   "想选外层窗口或父容器时按 Tab 逐层切换")
        self._effect_preview("hover")
        fill_mode = self.choice("window_hover_fill_mode", "候选区域显示方式",
                    (("透出原图（无填充）", "reveal"),
                     ("半透明颜色填充", "fill")),
                    "透出模式会移除候选区域上的遮罩并显示原始截图，边框和尺寸标签仍保留。")
        self._hover_style_controls(config)
        hover_opacity = self.number("window_hover_opacity", "候选框填充不透明度(%)", 0, 100,
                "调整蓝色高亮区域的填充浓淡；35% 默认值可突出目标，同时保留底层内容可见。\n"
            "边框保持实色，便于看清识别范围").valueChanged.connect(
                self._mark_hover_style_custom)
        hover_opacity_control = self.controls["window_hover_opacity"]
        hover_opacity_control.setEnabled(fill_mode.currentData() == "fill")
        fill_mode.currentIndexChanged.connect(
            lambda _index: hover_opacity_control.setEnabled(fill_mode.currentData() == "fill"))
        # 透出模式下候选框不填充，填充颜色同样不参与绘制，随模式一起置灰。
        hover_fill_control = self.controls["window_hover_color"]
        hover_fill_control.setEnabled(fill_mode.currentData() == "fill")
        hover_fill_control.setToolTip("自定义候选框填充颜色；仅在“半透明颜色填充”模式下生效，"
                                      "透出原图模式下候选框没有填充")
        fill_mode.currentIndexChanged.connect(
            lambda _index: hover_fill_control.setEnabled(fill_mode.currentData() == "fill"))
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
        self.number("element_depth", "识别层级", 1, 32,
                    "从命中的最内层控件往上保留多少层祖先容器（窗口、分组、面板等），\n"
                    "供 Tab 逐层切换；最内层的小控件（按钮、列表项）始终会保留，不受此值影响。\n"
                    "数值越大，Tab 能切换到的外层容器越多；1 只保留最内层控件本身，\n"
                "32 为上限。默认 12 层；提高后可遍历更深的祖先链，但可能增加 Tab 切换候选。\n"
                "UIA 命中后还会向下查找最多 24 层以寻找具体控件；此设置只控制向上保留的祖先层数")
        self.check("uia_debug_tree", "记录 UIA 结构诊断",
                   "开启后，每次 UIA 命中会在应用日志中记录目标控件的祖先链及父级下的组件子树（最多 60 个节点、向下 8 层，命中分支优先）。\n"
               "只记录名称、控件类型和屏幕矩形，不读取控件值；控件名称仍可能包含应用标题或文件名，请分享日志前先检查并在诊断后关闭。")
        self.group("遮罩与选区")
        self._mask_color_controls(config)
        self.number("mask_opacity", "遮罩不透明度 (%)", 0, 100,
                "选区之外颜色叠加的浓度；0 为完全透明，100 为完全遮挡")
        border_key = "selection_border_color"
        border = ColorButton(config.data[border_key],
                             lambda color: self.update_value(border_key, color))
        border.setToolTip("只改变截图选择框的边框颜色，不影响标注颜色")
        self.controls[border_key] = border
        self.form.addRow("选区边框颜色", border)
        self.choice("anchor_style", "选区锚点", [("边框", "border"), ("填充", "fill")],
                    "选区四角与四边控制点的外观：边框为空心方块，填充为实心方块")
        self._effect_preview("selection")
        self.group("历史")
        self.number("history_limit", "历史图片数量", 1, 10000,
                    "从自动保存目录读取的最大图片数量；\n"
                    "影响贴上次截图、上一张/下一张轮换以及贴图管理窗口能看到的图片范围")

    def _effect_preview(self, kind):
        preview = ScreenshotEffectPreview(self.config, kind)
        self.previews.append(preview)
        self.form.addRow(preview)
        return preview

    def _hover_style_controls(self, config):
        combo = QComboBox()
        for label, key, _style in HOVER_STYLE_PRESETS:
            combo.addItem(label, key)
        combo.addItem("自定义", "custom")
        self.hover_style_combo = combo
        self.form.addRow("候选框风格", combo)

        preset_keys = tuple(HOVER_STYLE_PRESETS[0][2])
        current = {key: config.data[key] for key in preset_keys}
        selected = (next((key for _label, key, style in HOVER_STYLE_PRESETS
                          if style == current), "custom")
                    if config.data.get("window_hover_fill_mode", "reveal") == "fill"
                    else "custom")
        combo.setCurrentIndex(combo.findData(selected))
        combo.currentIndexChanged.connect(
            lambda _index: self._apply_hover_style(combo.currentData()))

        self._hover_color("window_hover_color", "候选框填充颜色")
        self._hover_color("window_hover_border_color", "候选框边线颜色")
        self._hover_color("window_hover_text_color", "尺寸文字颜色")
        self._hover_color("window_hover_badge_color", "尺寸标签底色")
        self.number("window_hover_border_width", "候选框边线宽度(px)", 1, 6,
                    "调整 UIA/窗口候选框边线粗细").valueChanged.connect(
                        self._mark_hover_style_custom)
        self.number("window_hover_font_size", "尺寸标签字号(px)", 8, 32,
                    "调整候选区域宽高标签的文字大小").valueChanged.connect(
                        self._mark_hover_style_custom)

    def sync_controls(self):
        super().sync_controls()
        if not hasattr(self, "hover_style_combo"):
            return
        preset_keys = tuple(HOVER_STYLE_PRESETS[0][2])
        current = {key: self.config.data[key] for key in preset_keys}
        selected = (next((key for _label, key, style in HOVER_STYLE_PRESETS
                          if style == current), "custom")
                    if self.config.data.get("window_hover_fill_mode", "reveal") == "fill"
                    else "custom")
        with QSignalBlocker(self.hover_style_combo):
            self.hover_style_combo.setCurrentIndex(
                self.hover_style_combo.findData(selected))

    def _hover_color(self, key, label):
        button = ColorButton(self.config.data[key],
                             lambda color, current=key:
                             self._set_hover_color(current, color))
        button.setToolTip(f"自定义{label}")
        self.controls[key] = button
        self.color_buttons[key] = button
        self.form.addRow(label, button)

    def _set_hover_color(self, key, color):
        self.update_value(key, color)
        self._mark_hover_style_custom()

    def _mark_hover_style_custom(self, *_args):
        combo = getattr(self, "hover_style_combo", None)
        if combo is not None and combo.currentData() != "custom":
            combo.setCurrentIndex(combo.findData("custom"))

    def _apply_hover_style(self, key):
        style = next((values for _label, preset_key, values in HOVER_STYLE_PRESETS
                      if preset_key == key), None)
        if style is None:
            return
        self.config.data.update(style)
        self.config.data["window_hover_fill_mode"] = "fill"
        fill_mode = self.controls.get("window_hover_fill_mode")
        if fill_mode is not None:
            with QSignalBlocker(fill_mode):
                fill_mode.setCurrentIndex(fill_mode.findData("fill"))
            self.controls["window_hover_opacity"].setEnabled(True)
            if "window_hover_color" in self.controls:
                self.controls["window_hover_color"].setEnabled(True)
        for name in ("window_hover_color", "window_hover_border_color",
                     "window_hover_text_color", "window_hover_badge_color"):
            self.color_buttons[name].set_color(style[name])
        for name in ("window_hover_opacity", "window_hover_border_width",
                     "window_hover_font_size"):
            control = self.controls[name]
            with QSignalBlocker(control):
                control.setValue(style[name])
        self.changed()

    def _mask_color_controls(self, config):
        key = "mask_color"
        presets = (
            ("清透蓝", "#d9edff"),
            ("薄荷绿", "#ddf5e4"),
            ("奶油黄", "#fff3cc"),
            ("樱花粉", "#ffe2eb"),
            ("淡紫", "#eee7ff"),
            ("柔白", "#ffffff"),
            ("石墨黑", "#000000"),
        )
        current = config.data[key]
        combo = QComboBox()
        for label, color in presets:
            combo.addItem(label, color)
        combo.addItem(f"自定义 ({current})", current)
        combo.setCurrentIndex(combo.findData(current))
        button = ColorButton(current,
                     lambda color: self._set_mask_color(color, combo, button))
        button.setToolTip("自定义选区外遮罩颜色")
        combo.setToolTip("选择清新亮色预设，也可用旁边的按钮自定义颜色")
        combo.currentIndexChanged.connect(
            lambda _index: self._select_mask_color(combo, button))
        self.controls[key] = combo
        self.color_buttons[key] = button
        self.form.addRow("遮罩颜色预设", combo)
        self.form.addRow("自定义遮罩颜色", button)

    def _select_mask_color(self, combo, button):
        color = combo.currentData()
        if color:
            self.update_value("mask_color", color)
            button.set_color(color)

    def _set_mask_color(self, color, combo, button):
        self.update_value("mask_color", color)
        index = combo.findData(color)
        if index < 0:
            index = combo.count() - 1
            combo.setItemText(index, f"自定义 ({color})")
            combo.setItemData(index, color)
        combo.setCurrentIndex(index)
        button.set_color(color)

    def _shortcut(self, key, label, default, help_text):
        sequence = QKeySequenceEdit(QKeySequence(self.config.data.get(key, default)))
        sequence.setMaximumSequenceLength(1)
        sequence.setToolTip(help_text)
        self.controls[key] = sequence
        self.form.addRow(label, sequence)
        sequence.keySequenceChanged.connect(
            lambda value, setting=key, fallback=default: self._shortcut_changed(setting, fallback, value))

    def _shortcut_changed(self, key, default, sequence):
        if sequence.isEmpty():
            self.controls[key].setKeySequence(QKeySequence(default))
            return
        self.update_value(key, sequence.toString(QKeySequence.PortableText))