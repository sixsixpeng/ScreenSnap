"""编辑器工具栏。"""

import logging
from functools import lru_cache

from PySide6.QtCore import Signal, Qt, QSize, QTimer, QSignalBlocker, QEvent, QRectF
from PySide6.QtGui import (QFont, QIcon, QPainter, QPainterPath, QPen, QPixmap, QColor,
                           QGuiApplication)
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import (QWidget, QHBoxLayout, QVBoxLayout, QGridLayout, QLabel,
                               QCheckBox, QToolButton, QMenu, QStyle, QButtonGroup,
                               QSlider, QFontComboBox, QWidgetAction, QSizePolicy,
                               QRadioButton, QSpinBox, QScrollArea, QFrame,
                               QApplication, QComboBox)
from editor.annotation_items import SEQUENCE_SHAPES, SEQUENCE_PRESETS
from ui.widgets.color_button import ColorButton
from ui.action_icons import action_icon
from config.config_manager import DEFAULTS, TOOL_WIDTH_KEYS
from core.constants import shortcut_label

# 每个工具在“更多设置”里对应的实时预览类型。
PREVIEW_KINDS = {"pen": "pen", "rect": "rect", "ellipse": "ellipse", "arrow": "arrow",
                 "marker": "marker", "mosaic": "mosaic", "text": "text", "eraser": "eraser",
                 "crop": "crop", "number": "sequence", "picker": "picker"}


@lru_cache(maxsize=1)
def settings_icon():
    pixmap = QPixmap(24, 24)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(QPen(QColor("#315c66"), 2, Qt.SolidLine, Qt.RoundCap))
    for y, knob in ((5, 9), (12, 16), (19, 7)):
        painter.drawLine(3, y, 21, y)
        painter.setBrush(QColor("#00ad91"))
        painter.drawEllipse(knob - 2, y - 2, 4, 4)
    painter.end()
    return QIcon(pixmap)


@lru_cache(maxsize=1)
def text_icon():
    pixmap = QPixmap(24, 24)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(QPen(QColor("#355f68"), 3, Qt.SolidLine, Qt.RoundCap))
    painter.drawLine(5, 5, 19, 5)
    painter.drawLine(12, 5, 12, 20)
    painter.end()
    return QIcon(pixmap)


@lru_cache(maxsize=32)
def annotation_icon(tool):
    pixmap = QPixmap(24, 24)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    ink = QColor("#315c66")
    painter.setPen(QPen(ink, 2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    painter.setBrush(Qt.NoBrush)
    if tool == "select":
        path = QPainterPath()
        path.moveTo(4, 2)
        path.lineTo(4, 19)
        path.lineTo(8, 15)
        path.lineTo(11, 22)
        path.lineTo(14, 21)
        path.lineTo(11, 14)
        path.lineTo(18, 14)
        path.closeSubpath()
        painter.setBrush(ink)
        painter.setPen(QPen(QColor("white"), 1))
        painter.drawPath(path)
    elif tool == "rect":
        painter.drawRect(4, 4, 16, 16)
    elif tool == "ellipse":
        painter.drawEllipse(4, 4, 16, 16)
    elif tool == "arrow":
        painter.drawLine(4, 20, 19, 5)
        painter.drawLine(11, 5, 19, 5)
        painter.drawLine(19, 5, 19, 13)
    elif tool in ("pen", "marker", "picker"):
        painter.save()
        painter.translate(12, 12)
        painter.rotate(-45)
        if tool == "pen":
            painter.setBrush(QColor("#dce9ec"))
            painter.drawRoundedRect(-3, -8, 6, 14, 1, 1)
            painter.drawLine(0, 6, 0, 10)
            painter.drawLine(-2, -6, 2, -6)
        elif tool == "marker":
            painter.setPen(QPen(QColor("#9b7400"), 1.3))
            painter.setBrush(QColor("#ffd43b"))
            painter.drawRoundedRect(-4, -8, 8, 14, 1, 1)
            painter.setBrush(QColor("#f3bd24"))
            painter.drawRect(-4, -8, 8, 3)
            painter.drawLine(0, 6, 0, 10)
        else:
            painter.setBrush(QColor("#dce9ec"))
            painter.drawRoundedRect(-3, -7, 6, 13, 2, 2)
            painter.drawEllipse(-4, -11, 8, 6)
            painter.drawLine(0, 6, 0, 10)
        painter.restore()
    elif tool == "crop":
        painter.drawEllipse(2, 3, 7, 7)
        painter.drawEllipse(2, 14, 7, 7)
        painter.drawLine(8, 8, 20, 3)
        painter.drawLine(8, 16, 20, 21)
        painter.drawLine(10, 11, 19, 19)
    elif tool == "text":
        painter.end()
        return text_icon()
    elif tool == "mosaic":
        painter.setBrush(QColor("#8fb4b5"))
        for x, y in ((4, 4), (12, 4), (4, 12), (12, 12)):
            painter.drawRect(x, y, 6, 6)
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(4, 4, 14, 14)
    elif tool == "number":
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(QColor("#3a3a3a"), 2))
        painter.drawEllipse(2, 2, 20, 20)
        font = QFont()
        font.setPixelSize(15)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor("#3a3a3a"))
        painter.drawText(QRectF(2, 2, 20, 20), Qt.AlignCenter, "1")
    painter.end()
    return QIcon(pixmap)


def rich_tooltip(title, detail, binding=""):
    """构造普通文本工具提示，保留系统默认提示外观。"""
    suffix = f"（快捷键: {binding}）" if binding else ""
    return f"{title}\n{detail}{suffix}"


class _OptionsPanel(QWidget):
    def showEvent(self, event):
        super().showEvent(event)
        if self.layout():
            self.layout().invalidate()
            self.layout().activate()


class ToolbarWidget(QWidget):
    """只发出工具和命令信号，不直接修改图片或持有编辑状态。"""

    tool_changed = Signal(str)
    command = Signal(str)
    color_changed = Signal(str)
    setting_changed = Signal(str, object)
    crop_style_changed = Signal(str, object)

    def __init__(self, pen_color=DEFAULTS["pen_color"], settings=None,
                 show_capture_actions=True):
        super().__init__()
        settings = settings or {}
        self.settings = settings
        self.hotkeys = settings.get("hotkeys", {})
        self.choice_buttons = {}
        self.command_buttons = []
        self.menu_buttons = []
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.sections = []
        self.section_layout = QGridLayout(self)
        self.section_layout.setContentsMargins(8, 2, 8, 2)
        self.section_layout.setHorizontalSpacing(2)
        self.section_layout.setVerticalSpacing(3)
        self._layout_mode = None

        self._apply_hover_style()

        drawing = self.group("标注")
        tool_grid = QGridLayout()
        tool_grid.setContentsMargins(0, 0, 0, 0)
        tool_grid.setHorizontalSpacing(5)
        tool_grid.setVerticalSpacing(4)
        drawing.addLayout(tool_grid)
        self.tools = QButtonGroup(self)
        self.tools.setExclusive(True)
        self.tool_buttons = {}
        for label, key in [("选择", "select"), ("画笔", "pen"),
                           ("记号笔", "marker"), ("箭头", "arrow"), ("矩形", "rect"),
                           ("椭圆", "ellipse"), ("序号", "number"), ("文字", "text"),
                           ("橡皮擦", "eraser"), ("马赛克", "mosaic"), ("取色", "picker"), ("裁剪", "crop")]:
            button = QToolButton()
            icon = action_icon("eraser") if key == "eraser" else annotation_icon(key)
            button.setIcon(icon)
            button.setIconSize(QSize(20, 20))
            button.setText(label)
            button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
            tool_tips = {
                "select": "选择已有标注；可移动、缩放或调整层级",
                "pen": "按住并拖动自由绘制线条；使用当前颜色和画笔线宽",
                "marker": "绘制半透明重点标记；适合高亮文字或区域",
                "text": "单击图片添加文字；字体、字号和对齐可在更多设置中调整",
                "arrow": "拖动绘制箭头、线段或虚线；样式、线宽和颜色可调整",
                "rect": "拖动绘制矩形边框；支持实线或虚线",
                "ellipse": "拖动绘制椭圆边框；支持实线或虚线",
                "eraser": "拖过已有标注，将经过的标注内容擦除",
                "mosaic": "拖动区域添加马赛克；可选择方块、毛玻璃或细粒效果",
                "picker": "从图片中取色，并设置为后续标注颜色",
                "number": "单击图片放置步骤序号；号码自动递增，颜色与字号在设置中调整",
                "crop": "拖动裁剪图片；裁剪框颜色和线宽会保存为下次编辑的默认值",
            }
            button.setToolTip(rich_tooltip(label, tool_tips[key]))
            button.setAccessibleName(label)
            button.setCheckable(True)
            button.setMinimumHeight(40)
            button.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
            button.clicked.connect(lambda checked=False, selected=key: self.tool_changed.emit(selected))
            self.tools.addButton(button)
            self.tool_buttons[key] = button
            tool_grid.addWidget(button, (len(self.tool_buttons) - 1) // 6,
                                (len(self.tool_buttons) - 1) % 6)
        self.tool_buttons[settings.get("annotation_tool", "select") if settings.get("annotation_tool") in self.tool_buttons else "select"].setChecked(True)
        self.tool_grid = tool_grid
        self.tool_widths = {tool: settings.get(key, DEFAULTS[key])
                    for tool, key in TOOL_WIDTH_KEYS.items()}
        self.current_tool = settings.get("annotation_tool", "pen")
        active_color = settings.get(f"{self.current_tool}_color", pen_color)
        self.pen_color = ColorButton(active_color, self.color_changed.emit, compact=True)
        self.pen_color.setMinimumHeight(40)
        self.pen_color.setToolTip("标注颜色")
        self.tool_color_buttons = {}
        tool_color_options = (
            ("pen_color", "画笔颜色", "画笔描边颜色"),
            ("marker_color", "记号笔颜色", "记号笔颜色"),
            ("rect_color", "矩形边框/填充颜色", "矩形边框颜色；开启填充时也作为填充颜色"),
            ("ellipse_color", "椭圆边框/填充颜色", "椭圆边框颜色；开启填充时也作为填充颜色"),
            ("text_color", "文字颜色", "新建文字标注的默认文字颜色"),
            ("arrow_color", "箭头颜色", "箭头线条与箭头头部颜色"),
        )
        for key, label, purpose in tool_color_options:
            color_button = ColorButton(
                settings.get(key, DEFAULTS[key]),
                lambda color, setting_key=key: self.setting_changed.emit(setting_key, color),
                compact=True, purpose=purpose)
            color_button.setMinimumHeight(34)
            self.tool_color_buttons[key] = color_button
        self.pen_width = QSlider(Qt.Horizontal)
        initial_tool = settings.get("annotation_tool", "pen")
        self.pen_width.setRange(10, 100) if initial_tool == "eraser" else self.pen_width.setRange(1, 50)
        self.pen_width.setValue(self.tool_widths.get(settings.get("annotation_tool"), DEFAULTS["pen_width"]))
        self.pen_width.setMinimumWidth(220)
        self.pen_width.setMinimumHeight(38)
        self.pen_width.setToolTip("标注线条粗细")
        self.pen_width_label = QLabel(f"{self.pen_width.value()} px")
        self.pen_width_label.setMinimumWidth(52)
        self.pen_width_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.pen_width.valueChanged.connect(lambda value: self.pen_width_label.setText(f"{value} px"))
        self.pen_width.valueChanged.connect(self.change_tool_width)
        self.marker_opacity = QSlider(Qt.Horizontal)
        self.marker_opacity.setRange(1, 100)
        self.marker_opacity.setValue(settings.get("marker_opacity", 38))
        self.marker_opacity.setMinimumWidth(220)
        self.marker_opacity.setMinimumHeight(38)
        self.marker_opacity.setToolTip("荧光笔不透明度；数值越低，底图越清晰")
        self.marker_opacity_label = QLabel(f"{self.marker_opacity.value()}%")
        self.marker_opacity_label.setMinimumWidth(52)
        self.marker_opacity_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.marker_opacity.valueChanged.connect(
            lambda value: self.marker_opacity_label.setText(f"{value}%"))
        self.marker_opacity.valueChanged.connect(
            lambda value: self.setting_changed.emit("marker_opacity", value))
        self.font = QFontComboBox()
        self.font.setMinimumWidth(220)
        if settings.get("font"):
            self.font.setCurrentFont(QFont(settings["font"]))
        self.font.setToolTip("新建文字使用的字体")
        self.font.currentFontChanged.connect(lambda font: self.setting_changed.emit("font", font.family()))
        self.font_size = QSpinBox()
        self.font_size.setRange(6, 200)
        self.font_size.setValue(settings.get("font_size", 18))
        self.font_size.setSuffix(" pt")
        self.font_size.setToolTip("新建文字使用的字号")
        self.font_size.valueChanged.connect(lambda value: self.setting_changed.emit("font_size", value))
        self.mosaic_mode = self.radio_options(
            "mosaic_mode", (("方块", "blocks"), ("毛玻璃", "blur"), ("细粒", "fine")),
            settings.get("mosaic_mode", "blocks"), "马赛克效果")
        self.mosaic_mode.setToolTip("马赛克效果")
        self.mosaic_size = QSlider(Qt.Horizontal)
        self.mosaic_size.setRange(2, 100)
        self.mosaic_size.setValue(settings.get("mosaic_size", 12))
        self.mosaic_size.setMinimumWidth(220)
        self.mosaic_size.setMinimumHeight(38)
        self.mosaic_size.setToolTip("马赛克颗粒度")
        self.mosaic_size_label = QLabel(f"{self.mosaic_size.value()} px")
        self.mosaic_size_label.setMinimumWidth(52)
        self.mosaic_size_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.mosaic_size.valueChanged.connect(
            lambda value: self.mosaic_size_label.setText(f"{value} px"))
        self.mosaic_size.valueChanged.connect(lambda value: self.setting_changed.emit("mosaic_size", value))
        self.alignment = self.radio_options(
            "text_alignment", (("左对齐", "left"), ("居中", "center"), ("右对齐", "right")),
            settings.get("text_alignment", "left"), "新建文字标注的对齐方式")
        self.alignment.setToolTip("新建文字标注的对齐方式")
        self.arrow_style = self.radio_options(
            "arrow_style", (("实心", "filled"), ("空心", "open"),
                            ("实心双向", "double_filled"), ("空心双向", "double"),
                            ("实心线段", "solid_line"), ("空心线段", "open_line"),
                            ("实心虚线", "solid_dash"), ("空心虚线", "open_dash")),
            settings.get("arrow_style", "filled"), "设置新箭头的箭头头部样式")
        self.arrow_style.setToolTip("设置新箭头的箭头头部样式")
        self.rect_style = self.radio_options(
            "rect_style", (("实线", "solid"), ("虚线", "dash")),
            settings.get("rect_style", "solid"), "设置新矩形的边框线型")
        self.rect_style.setToolTip("设置新矩形使用实线或虚线边框")
        self.rect_corner_enabled = QCheckBox("圆角矩形")
        self.rect_corner_enabled.setChecked(settings.get("rect_corner_enabled", False))
        self.rect_corner_enabled.toggled.connect(
            lambda value: self.setting_changed.emit("rect_corner_enabled", value))
        self.rect_corner_radius = QSlider(Qt.Horizontal)
        self.rect_corner_radius.setRange(0, 100)
        self.rect_corner_radius.setValue(settings.get("rect_corner_radius", 12))
        self.rect_corner_radius_label = QLabel(f"{self.rect_corner_radius.value()} px")
        self.rect_corner_radius_label.setMinimumWidth(52)
        self.rect_corner_radius.valueChanged.connect(
            lambda value: self.rect_corner_radius_label.setText(f"{value} px"))
        self.rect_corner_radius.valueChanged.connect(
            lambda value: self.setting_changed.emit("rect_corner_radius", value))
        self.rect_fill_enabled = QCheckBox("填充区域")
        self.rect_fill_enabled.setChecked(settings.get("rect_fill_enabled", False))
        self.rect_fill_enabled.toggled.connect(lambda value: self.setting_changed.emit("rect_fill_enabled", value))
        self.rect_fill_opacity = QSlider(Qt.Horizontal)
        self.rect_fill_opacity.setRange(0, 100)
        self.rect_fill_opacity.setValue(settings.get("rect_fill_opacity", 35))
        self.rect_fill_opacity_label = QLabel(f"{self.rect_fill_opacity.value()}%")
        self.rect_fill_opacity.valueChanged.connect(lambda value: self.rect_fill_opacity_label.setText(f"{value}%"))
        self.rect_fill_opacity.valueChanged.connect(lambda value: self.setting_changed.emit("rect_fill_opacity", value))
        self.ellipse_fill_enabled = QCheckBox("填充区域")
        self.ellipse_fill_enabled.setChecked(settings.get("ellipse_fill_enabled", False))
        self.ellipse_fill_enabled.toggled.connect(lambda value: self.setting_changed.emit("ellipse_fill_enabled", value))
        self.ellipse_fill_opacity = QSlider(Qt.Horizontal)
        self.ellipse_fill_opacity.setRange(0, 100)
        self.ellipse_fill_opacity.setValue(settings.get("ellipse_fill_opacity", 35))
        self.ellipse_fill_opacity_label = QLabel(f"{self.ellipse_fill_opacity.value()}%")
        self.ellipse_fill_opacity.valueChanged.connect(lambda value: self.ellipse_fill_opacity_label.setText(f"{value}%"))
        self.ellipse_fill_opacity.valueChanged.connect(lambda value: self.setting_changed.emit("ellipse_fill_opacity", value))
        self.ellipse_style = self.radio_options(
            "ellipse_style", (("实线", "solid"), ("虚线", "dash")),
            settings.get("ellipse_style", "solid"), "设置新椭圆的边框线型")
        self.ellipse_style.setToolTip("设置新椭圆使用实线或虚线边框")
        self.crop_color = ColorButton(settings.get("crop_color", DEFAULTS["crop_color"]),
                          lambda color: self.crop_style_changed.emit("crop_color", color),
                          compact=True, purpose="裁剪框颜色")
        self.crop_color.setMinimumHeight(36)
        self.crop_width = QSlider(Qt.Horizontal)
        self.crop_width.setRange(1, 12)
        self.crop_width.setValue(settings.get("crop_width", DEFAULTS["crop_width"]))
        self.crop_width.setMinimumWidth(240)
        self.crop_width.setMinimumHeight(36)
        self.crop_width.setToolTip("设置裁剪框边线宽度，并保存为下次编辑的默认值")
        self.crop_width_label = QLabel(f"{self.crop_width.value()} px")
        self.crop_width_label.setMinimumWidth(52)
        self.crop_width_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.crop_width.valueChanged.connect(lambda value: self.crop_width_label.setText(f"{value} px"))
        self.crop_width.valueChanged.connect(lambda value: self.crop_style_changed.emit("crop_width", value))
        self.cursor_switch = QCheckBox("显示鼠标")
        self.cursor_switch.setToolTip("切换当前截图光标，并保存为下次截图默认值")
        self.cursor_switch.setFixedHeight(34)
        self.cursor_switch.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        options = QToolButton()
        options.setText("更多设置")
        options.setIcon(settings_icon())
        options.setIconSize(QSize(20, 20))
        options.setFixedSize(136, 34)
        options.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        options.setToolTip(rich_tooltip("更多设置", "调整当前工具的线宽、样式、透明度、字体或效果"))
        options.setPopupMode(QToolButton.InstantPopup)
        menu = QMenu(options)
        panel = _OptionsPanel(menu)
        panel.setMinimumWidth(430)
        panel_layout = QGridLayout(panel)
        panel_layout.setContentsMargins(8, 6, 8, 6)
        panel_layout.setHorizontalSpacing(12)
        panel_layout.setVerticalSpacing(4)
        panel_layout.setColumnStretch(1, 1)
        panel_layout.setColumnStretch(3, 1)
        from ui.widgets.annotation_preview import AnnotationPreview

        preview_box = QWidget()
        preview_layout = QVBoxLayout(preview_box)
        preview_layout.setContentsMargins(0, 0, 0, 0)
        screen = QGuiApplication.primaryScreen()
        usable = screen.availableGeometry().height() if screen is not None else 900
        self.previews = {}
        for kind in ("text", "arrow", "pen", "rect", "ellipse", "marker", "mosaic", "eraser", "crop", "sequence", "picker"):
            widget = AnnotationPreview(settings, kind, 88 if usable < 800 else 104)
            widget.setMinimumWidth(200)
            widget.setVisible(False)
            self.previews[kind] = widget
            preview_layout.addWidget(widget)
        self.active_color_label = QLabel("当前工具颜色")
        panel_layout.addWidget(self.active_color_label, 0, 0)
        self.crop_color.setParent(panel)
        panel_layout.addWidget(self.crop_color, 0, 1, 1, 2)
        for key, _, _ in tool_color_options:
            button = self.tool_color_buttons[key]
            button.setParent(panel)
            panel_layout.addWidget(button, 0, 1, 1, 2)
            button.hide()
        self.active_color_label.hide()
        self.pen_color.hide()
        panel_layout.addWidget(QLabel("线宽"), 1, 0)
        panel_layout.addWidget(self.pen_width, 1, 1)
        panel_layout.addWidget(self.pen_width_label, 1, 2)
        panel_layout.addWidget(QLabel("透明度"), 2, 0)
        panel_layout.addWidget(self.marker_opacity, 2, 1)
        panel_layout.addWidget(self.marker_opacity_label, 2, 2)
        panel_layout.addWidget(QLabel("字体"), 3, 0)
        panel_layout.addWidget(self.font, 3, 1, 1, 2)
        panel_layout.addWidget(QLabel("字号"), 4, 0)
        panel_layout.addWidget(self.font_size, 4, 1, 1, 2)
        panel_layout.addWidget(QLabel("对齐"), 5, 0)
        panel_layout.addWidget(self.alignment, 5, 1, 1, 2)
        panel_layout.addWidget(QLabel("效果"), 6, 0)
        panel_layout.addWidget(self.mosaic_mode, 6, 1, 1, 2)
        panel_layout.addWidget(QLabel("颗粒"), 7, 0)
        panel_layout.addWidget(self.mosaic_size, 7, 1)
        panel_layout.addWidget(self.mosaic_size_label, 7, 2)
        panel_layout.addWidget(QLabel("样式"), 8, 0)
        panel_layout.addWidget(self.arrow_style, 8, 1, 1, 2)
        panel_layout.addWidget(QLabel("裁剪线宽"), 9, 0)
        panel_layout.addWidget(self.crop_width, 9, 1)
        panel_layout.addWidget(self.crop_width_label, 9, 2)
        panel_layout.addWidget(QLabel("矩形线型"), 10, 0)
        panel_layout.addWidget(self.rect_style, 10, 1, 1, 2)
        panel_layout.addWidget(QLabel("椭圆线型"), 11, 0)
        panel_layout.addWidget(self.ellipse_style, 11, 1, 1, 2)
        panel_layout.addWidget(QLabel("预览"), 12, 0)
        panel_layout.addWidget(preview_box, 12, 1, 1, 2)
        panel_layout.addWidget(QLabel("矩形圆角"), 13, 0)
        panel_layout.addWidget(self.rect_corner_enabled, 13, 1, 1, 2)
        panel_layout.addWidget(QLabel("圆角半径"), 14, 0)
        panel_layout.addWidget(self.rect_corner_radius, 14, 1)
        panel_layout.addWidget(self.rect_corner_radius_label, 14, 2)
        panel_layout.addWidget(QLabel("矩形填充"), 15, 0)
        panel_layout.addWidget(self.rect_fill_enabled, 15, 1, 1, 2)
        panel_layout.addWidget(QLabel("填充透明度"), 16, 0)
        panel_layout.addWidget(self.rect_fill_opacity, 16, 1)
        panel_layout.addWidget(self.rect_fill_opacity_label, 16, 2)
        panel_layout.addWidget(QLabel("椭圆填充"), 17, 0)
        panel_layout.addWidget(self.ellipse_fill_enabled, 17, 1, 1, 2)
        panel_layout.addWidget(QLabel("填充透明度"), 18, 0)
        panel_layout.addWidget(self.ellipse_fill_opacity, 18, 1)
        panel_layout.addWidget(self.ellipse_fill_opacity_label, 18, 2)
        # 序号标注专属参数：形状、填充色、文字色、字号、起始值与预设组合。
        sequence_shape_options = (
            ("圆形", "circle"), ("方形", "square"), ("三角", "triangle"),
            ("菱形", "diamond"), ("五边形", "pentagon"), ("六边形", "hexagon"),
            ("星形", "star"), ("心形", "heart"), ("箭头", "arrow"),
            ("气泡", "bubble"), ("云", "cloud"), ("十字", "plus"),
            ("水滴", "drop"))
        self.sequence_shape = self.radio_options(
            "sequence_shape", sequence_shape_options,
            settings.get("sequence_shape", "circle"), "序号标记的形状",
            columns=(len(sequence_shape_options) + 1) // 2)
        self.sequence_fill = ColorButton(settings.get("sequence_fill_color", "#ff0000"),
                          lambda color: self.setting_changed.emit("sequence_fill_color", color),
                          compact=True, purpose="序号填充颜色")
        self.sequence_text = ColorButton(settings.get("sequence_text_color", "#ffffff"),
                          lambda color: self.setting_changed.emit("sequence_text_color", color),
                          compact=True, purpose="序号文字颜色")
        self.sequence_size = QSpinBox()
        self.sequence_size.setRange(6, 200)
        self.sequence_size.setValue(settings.get("sequence_font_size", 14))
        self.sequence_size.setSuffix(" pt")
        self.sequence_size.setToolTip("序号数字字号")
        self.sequence_size.valueChanged.connect(lambda value: self.setting_changed.emit("sequence_font_size", value))
        self.sequence_start = QSpinBox()
        self.sequence_start.setRange(0, 999)
        self.sequence_start.setValue(settings.get("sequence_start", 1))
        self.sequence_start.setToolTip("第一个序号的号码，后续自动递增")
        self.sequence_start.valueChanged.connect(lambda value: self.setting_changed.emit("sequence_start", value))
        self.sequence_preset = QComboBox()
        for label, key in (("自定义", "custom"), ("红圆", "red_circle"), ("蓝方", "blue_square"),
                           ("绿星", "green_star"), ("琥珀菱形", "amber_diamond"),
                           ("紫五边形", "purple_pentagon"), ("青六边形", "teal_hexagon"),
                           ("红心", "red_heart"), ("蓝箭头", "blue_arrow")):
            self.sequence_preset.addItem(label, key)
        self.sequence_preset.setCurrentIndex(self.sequence_preset.findData(settings.get("sequence_preset", "custom")))
        self.sequence_preset.setToolTip("一键套用形状与配色组合；选自定义后可逐项自由调整")
        self.sequence_preset.currentIndexChanged.connect(
            lambda _i: self._apply_sequence_preset(self.sequence_preset.currentData()))
        panel_layout.addWidget(QLabel("序号形状"), 19, 0)
        panel_layout.addWidget(self.sequence_shape, 19, 1, 1, 2)
        panel_layout.addWidget(QLabel("填充颜色"), 20, 0)
        panel_layout.addWidget(self.sequence_fill, 20, 1, 1, 2)
        panel_layout.addWidget(QLabel("文字颜色"), 21, 0)
        panel_layout.addWidget(self.sequence_text, 21, 1, 1, 2)
        panel_layout.addWidget(QLabel("序号字号"), 22, 0)
        panel_layout.addWidget(self.sequence_size, 22, 1, 1, 2)
        panel_layout.addWidget(QLabel("起始值"), 23, 0)
        panel_layout.addWidget(self.sequence_start, 23, 1, 1, 2)
        panel_layout.addWidget(QLabel("预设组合"), 24, 0)
        panel_layout.addWidget(self.sequence_preset, 24, 1, 1, 2)
        self.sequence_rows = (19, 20, 21, 22, 23, 24)
        option_row_count = 25
        for row in range(option_row_count):
            label_item = panel_layout.itemAtPosition(row, 0)
            label = label_item.widget() if label_item is not None else None
            if label is not None:
                label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
                label.setMinimumHeight(28)
        for index in range(panel_layout.count()):
            widget = panel_layout.itemAt(index).widget()
            if widget is not None:
                panel_layout.setAlignment(widget, Qt.AlignVCenter)
        self.option_rows = [tuple(panel_layout.itemAtPosition(row, column).widget()
                    for column in range(3) if panel_layout.itemAtPosition(row, column))
                    for row in range(option_row_count)]
        # 任何参数变化都重绘预览，弹窗尺寸随后按内容重新计算。
        self.setting_changed.connect(lambda *args: self.refresh_previews())
        self.crop_style_changed.connect(lambda *args: self.refresh_previews())
        self.color_changed.connect(lambda *args: self.refresh_previews())
        panel_action = QWidgetAction(menu)
        panel_action.setDefaultWidget(panel)
        menu.addAction(panel_action)
        self.appearance_panel = QWidget()
        appearance_outer = QVBoxLayout(self.appearance_panel)
        appearance_outer.setContentsMargins(10, 8, 10, 8)
        appearance_outer.setSpacing(8)
        self.appearance_details = QWidget(self.appearance_panel)
        appearance_outer.addWidget(self.appearance_details)
        appearance_layout = QGridLayout(self.appearance_details)
        appearance_layout.setContentsMargins(6, 4, 6, 4)
        appearance_layout.setHorizontalSpacing(6)
        appearance_layout.setVerticalSpacing(4)
        self.appearance_controls = {}
        self.appearance_value_labels = {}

        def appearance_check(key, label):
            control = QCheckBox(label)
            control.setChecked(settings.get(key, DEFAULTS[key]))
            control.toggled.connect(lambda value, setting=key:
                                    self.setting_changed.emit(setting, value))
            self.appearance_controls[key] = control
            return control

        def appearance_slider(key, label, low, high):
            slider = QSlider(Qt.Horizontal)
            slider.setRange(low, high)
            slider.setValue(settings.get(key, DEFAULTS[key]))
            slider.setMinimumWidth(60)
            value_label = QLabel(f"{slider.value()}" + ("%" if "strength" in key else " px"))
            value_label.setMinimumWidth(32)
            self.appearance_value_labels[key] = value_label
            slider.valueChanged.connect(
                lambda value, target=value_label, setting=key:
                (target.setText(f"{value}" + ("%" if "strength" in setting else " px")),
                 self.setting_changed.emit(setting, value)))
            self.appearance_controls[key] = slider
            return slider, value_label

        appearance_layout.addWidget(appearance_check("editor_image_round_corners", "启用圆角"), 0, 0, 1, 3)
        appearance_layout.addWidget(QLabel("半径"), 1, 0)
        radius_slider, radius_label = appearance_slider("editor_image_corner_radius", "圆角半径", 0, 100)
        appearance_layout.addWidget(radius_slider, 1, 1)
        appearance_layout.addWidget(radius_label, 1, 2)
        appearance_layout.addWidget(appearance_check("editor_image_border_enabled", "启用边框"), 0, 3, 1, 3)
        appearance_layout.addWidget(QLabel("宽度"), 1, 3)
        border_slider, border_label = appearance_slider("editor_image_border_width", "边框宽度", 0, 20)
        appearance_layout.addWidget(border_slider, 1, 4)
        appearance_layout.addWidget(border_label, 1, 5)
        border_color = ColorButton(settings.get("editor_image_border_color", "#ffffff"),
                                   lambda value: self.setting_changed.emit("editor_image_border_color", value),
                                   compact=True, purpose="输出边框颜色")
        appearance_layout.addWidget(QLabel("颜色"), 2, 3)
        appearance_layout.addWidget(border_color, 2, 4, 1, 2)
        self.appearance_controls["editor_image_border_color"] = border_color
        appearance_layout.addWidget(appearance_check("editor_image_shadow_enabled", "启用阴影"), 0, 6, 1, 3)
        appearance_layout.addWidget(QLabel("尺寸"), 1, 6)
        shadow_size_slider, shadow_size_label = appearance_slider("editor_image_shadow_size", "阴影尺寸", 0, 60)
        appearance_layout.addWidget(shadow_size_slider, 1, 7)
        appearance_layout.addWidget(shadow_size_label, 1, 8)
        appearance_layout.addWidget(QLabel("强度"), 2, 6)
        shadow_strength_slider, shadow_strength_label = appearance_slider(
            "editor_image_shadow_strength", "阴影强度", 0, 100)
        appearance_layout.addWidget(shadow_strength_slider, 2, 7)
        appearance_layout.addWidget(shadow_strength_label, 2, 8)
        shadow_color = ColorButton(settings.get("editor_image_shadow_color", "#000000"),
                                   lambda value: self.setting_changed.emit("editor_image_shadow_color", value),
                                   compact=True, purpose="输出阴影颜色")
        appearance_layout.addWidget(QLabel("颜色"), 3, 6)
        appearance_layout.addWidget(shadow_color, 3, 7, 1, 2)
        self.appearance_controls["editor_image_shadow_color"] = shadow_color
        from ui.widgets.annotation_preview import AnnotationPreview
        self.output_preview = AnnotationPreview(settings, "output", 36)
        self.output_preview.setMinimumHeight(128)
        self.output_preview.setMinimumWidth(320)
        preview_box = QWidget(self.appearance_panel)
        preview_layout = QVBoxLayout(preview_box)
        preview_layout.setContentsMargins(0, 0, 0, 0)
        preview_layout.addWidget(QLabel("最终输出预览"))
        preview_layout.addWidget(self.output_preview)
        appearance_outer.addWidget(preview_box)
        self.appearance_toggle = None
        self.setting_changed.connect(lambda *args: self.output_preview.refresh())
        self.crop_style_changed.connect(lambda *args: self.output_preview.refresh())
        self.color_changed.connect(lambda *args: self.output_preview.refresh())
        options.setMenu(menu)
        self.options_button = options
        for tool, button in self.tool_buttons.items():
            button.clicked.connect(lambda checked=False, selected=tool: self.set_tool_mode(selected))
        self.set_tool_mode(next(tool for tool, button in self.tool_buttons.items() if button.isChecked()))

        editing = self.group("编辑")
        self.edit_grid = QGridLayout()
        self.edit_grid.setSpacing(1)
        editing.addLayout(self.edit_grid)
        editing.addStretch(1)
        self.edit_buttons = []
        for label, key, icon in [("撤销", "undo", QStyle.SP_ArrowBack),
                                 ("重做", "redo", QStyle.SP_ArrowForward),
                     ("删除", "delete", QStyle.SP_TrashIcon)]:
            self.edit_buttons.append(self.button(self.edit_grid, label, key, icon))
        self.edit_buttons.append(self.menu_button(self.edit_grid, "层级", [("置顶", "top"), ("置底", "bottom"),
                                          ("上移一层", "up"), ("下移一层", "down")]))
        self.edit_buttons.append(self.button(self.edit_grid, "重置", "reset", QStyle.SP_DialogResetButton))
        self.capture_action_buttons = []
        if show_capture_actions:
            self.capture_action_buttons.append(self.button(self.edit_grid, "自定义尺寸", "custom_size",
                         action_icon("resize")))
            self.capture_action_buttons.append(self.button(self.edit_grid, "重新截图", "recapture",
                         action_icon("camera")))
            self.edit_buttons.extend(self.capture_action_buttons)
        image = self.group("图像旋转")
        self.image_grid = QGridLayout()
        self.image_grid.setSpacing(1)
        image.addLayout(self.image_grid)
        image.addStretch(1)
        self.image_buttons = []
        for label, action, icon in [("重置角度", "reset_rotation", QStyle.SP_DialogResetButton),
                         ("左转 90°", "left", QStyle.SP_ArrowBack),
                                     ("右转 90°", "right", QStyle.SP_ArrowForward),
                                     ("旋转 180°", "half", QStyle.SP_BrowserReload),
                                     ("任意角度", "angle", QStyle.SP_DialogResetButton),
                                     ("水平翻转", "horizontal", QStyle.SP_ArrowLeft),
                                     ("垂直翻转", "vertical", QStyle.SP_ArrowDown)]:
            button = self.button(self.image_grid, label, action, icon)
            image_icons = {"left": "rotate_left", "right": "rotate_right",
                           "half": "rotate_180", "angle": "rotate",
                           "horizontal": "flip_horizontal", "vertical": "flip_vertical"}
            if action in image_icons:
                button.setIcon(action_icon(image_icons[action]))
            self.image_buttons.append(button)
            if action == "reset_rotation":
                self.reset_rotation_button = button
                button.setToolTip(rich_tooltip("重置角度", "撤销最近一次任意角度旋转；后续编辑后自动禁用"))
                button.setEnabled(False)

        output = self.group("输出")
        self.output_grid = QGridLayout()
        self.output_grid.setSpacing(1)
        output.addLayout(self.output_grid)
        self.output_buttons = [self.button(self.output_grid, label, action, icon)
                               for label, action, icon in [("贴图", "paste", QStyle.SP_DesktopIcon),
                                                           ("保存", "save", QStyle.SP_DialogSaveButton),
                                                           ("仅复制", "copy_only", action_icon("clipboard_image")),
                                                           ("放弃", "discard", QStyle.SP_DialogCancelButton),
                                                           ("关闭全部", "close_all_editors", QStyle.SP_DialogCloseButton)]]
        next(button for button in self.output_buttons if button.text() == "贴图").setIcon(action_icon("sticker"))
        self.appearance_toggle = QToolButton(self)
        self.appearance_toggle.setIcon(settings_icon())
        self.appearance_toggle.setText("外观")
        self.appearance_toggle.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.appearance_toggle.setCheckable(True)
        self.appearance_toggle.setToolTip(rich_tooltip("外观", "设置最终输出圆角、边框、阴影并查看预览"))
        self.appearance_toggle.setAccessibleName("输出图像外观")
        self.appearance_toggle.setFixedHeight(34)
        self.output_grid.addWidget(self.appearance_toggle, 0, self.output_grid.count())
        self.output_buttons.insert(1, self.appearance_toggle)
        self.appearance_menu = QFrame(
            self, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowDoesNotAcceptFocus)
        self.appearance_menu.setObjectName("outputAppearancePopup")
        self.appearance_menu.setFixedWidth(960)
        self.appearance_panel.setMinimumWidth(944)
        self.appearance_panel.setObjectName("outputAppearancePanel")
        self.appearance_scroll = QScrollArea()
        self.appearance_scroll.setWidgetResizable(True)
        self.appearance_scroll.setFrameShape(QFrame.NoFrame)
        self.appearance_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.appearance_scroll.setWidget(self.appearance_panel)
        popup_layout = QVBoxLayout(self.appearance_menu)
        popup_layout.setContentsMargins(0, 0, 0, 0)
        popup_layout.addWidget(self.appearance_scroll)
        self.appearance_toggle.clicked.connect(self.toggle_appearance_menu)
        QGuiApplication.instance().installEventFilter(self)
        self.destroyed.connect(self._remove_appearance_event_filter)
        output.addStretch(1)
        self.edit_image_row = QWidget(self)
        self.edit_image_layout = QHBoxLayout(self.edit_image_row)
        self.edit_image_layout.setContentsMargins(0, 0, 0, 0)
        self.edit_image_layout.setSpacing(self.section_layout.horizontalSpacing())
        self.edit_image_layout.addStretch()
        self.reflow(1100)

    def _apply_hover_style(self):
        """给工具按钮加一层非常轻微的悬停/按下反馈，保持系统原生外观。

        只作用于 QToolButton（不含颜色按钮等 QPushButton），浅/深色主题各自取
        近乎透明的叠加色，避免明显色块变化；边框平时透明，悬停时浅描边以防布局跳动。
        """
        from PySide6.QtCore import Qt
        app = QApplication.instance()
        dark = False
        if app is not None:
            try:
                dark = app.styleHints().colorScheme() == Qt.ColorScheme.Dark
            except Exception:
                dark = False
        if dark:
            hover_bg = "rgba(255, 255, 255, 0.10)"
            hover_border = "rgba(255, 255, 255, 0.22)"
            pressed_bg = "rgba(255, 255, 255, 0.16)"
        else:
            hover_bg = "rgba(0, 0, 0, 0.06)"
            hover_border = "rgba(0, 0, 0, 0.14)"
            pressed_bg = "rgba(0, 0, 0, 0.10)"
        self.setStyleSheet(f"""
            QToolButton {{
                border: 1px solid transparent;
                border-radius: 4px;
            }}
            QToolButton:hover {{
                background: {hover_bg};
                border: 1px solid {hover_border};
            }}
            QToolButton:pressed {{
                background: {pressed_bg};
            }}
        """)

    def refresh_previews(self):
        """参数变化后重绘“更多设置”里的实时预览。"""
        for widget in self.previews.values():
            widget.refresh()
        self.output_preview.refresh()

    def change_tool_width(self, value):
        tool = next((key for key, button in self.tool_buttons.items() if button.isChecked()), None)
        if tool in TOOL_WIDTH_KEYS:
            self.tool_widths[tool] = value
            self.setting_changed.emit(TOOL_WIDTH_KEYS[tool], value)

    def set_tool_mode(self, tool):
        """只展示当前工具可用的参数；切换时不改写别的工具的线宽。"""
        self.set_active_tool(tool)
        rows = ({1} if tool in TOOL_WIDTH_KEYS else set())
        if tool == "eraser":
            self.tool_widths[tool] = max(10, min(100, self.tool_widths[tool]))
            with QSignalBlocker(self.pen_width):
                self.pen_width.setRange(10, 100)
                self.pen_width.setValue(self.tool_widths[tool])
        elif tool in TOOL_WIDTH_KEYS:
            with QSignalBlocker(self.pen_width):
                self.pen_width.setRange(1, 50)
                self.pen_width.setValue(self.tool_widths[tool])
        if tool == "marker":
            rows.add(2)
        elif tool == "text":
            rows.update((3, 4, 5))
        elif tool == "mosaic":
            rows.update((6, 7))
        elif tool == "arrow":
            rows.add(8)
        elif tool == "crop":
            rows.add(9)
        elif tool == "rect":
            rows.update((10, 13, 14, 15, 16))
        elif tool == "ellipse":
            rows.update((11, 17, 18))
        elif tool == "number":
            rows.update(self.sequence_rows)
        if tool in ("pen", "rect", "ellipse", "arrow", "marker", "text"):
            rows.add(0)
        elif tool == "crop":
            rows.add(0)
        # 有对应预览的工具才显示预览行，其余工具隐藏以节省弹窗高度。
        kind = PREVIEW_KINDS.get(tool)
        if kind:
            rows.add(12)
        for name, widget in self.previews.items():
            widget.setVisible(name == kind)
        if kind:
            self.previews[kind].refresh()
        # 同一个弹出面板只展示当前工具的参数，切换时保留各自的线宽。
        for index, widgets in enumerate(self.option_rows):
            for widget in widgets:
                widget.setVisible(index in rows)
        color_keys = {
            "pen": "pen_color", "marker": "marker_color", "rect": "rect_color",
            "ellipse": "ellipse_color", "text": "text_color", "arrow": "arrow_color",
        }
        for key, button in self.tool_color_buttons.items():
            button.setVisible(color_keys.get(tool) == key)
        self.crop_color.setVisible(tool == "crop")
        self.active_color_label.setVisible(tool in color_keys or tool == "crop")
        color_labels = {
            "pen": "画笔颜色", "marker": "记号笔颜色", "rect": "矩形边框/填充颜色",
            "ellipse": "椭圆边框/填充颜色", "text": "文字颜色", "arrow": "箭头颜色",
            "crop": "裁剪框颜色",
        }
        self.active_color_label.setText(color_labels.get(tool, "当前工具颜色"))
        self.options_button.setEnabled(bool(rows))
        name = self.tool_buttons[tool].text()
        self.options_button.setText(f"{name}设置" if rows else "更多设置")
        descriptions = {
            "crop": "设置裁剪框的颜色和线宽，并保存为下次编辑的默认值",
            "text": "设置新文字的字体、字号与对齐方式",
            "arrow": "设置新箭头的线宽和箭头样式",
            "rect": "设置新矩形的线宽和线型",
            "ellipse": "设置新椭圆的线宽和线型",
            "mosaic": "设置马赛克类型和颗粒大小",
            "marker": "设置记号笔线宽和透明度",
            "eraser": "设置橡皮擦直径",
            "number": "设置序号标记的形状、填充色、文字色与字号",
            "picker": "预览取色放大镜与像素网格；取到的色值会设为当前标注颜色",
        }
        self.options_button.setToolTip(rich_tooltip(
            f"{name}设置" if rows else "更多设置",
            descriptions.get(tool, f"设置{name}参数") if rows else "当前工具没有可调整的专属参数",
        ))
        self.option_rows[1][0].setText("直径" if tool == "eraser" else "线宽")
        self.pen_width.setToolTip("橡皮擦直径：10–100 px" if tool == "eraser"
                      else "标注线条粗细：1–50 px")
        if tool in TOOL_WIDTH_KEYS:
            with QSignalBlocker(self.pen_width):
                self.pen_width.setValue(self.tool_widths[tool])
            self.pen_width_label.setText(f"{self.pen_width.value()} px")
        panel = self.options_button.menu().actions()[0].defaultWidget()
        panel.setMinimumWidth(0)
        panel.setMaximumWidth(16777215)
        panel.setMinimumWidth(self.option_panel_width(tool))
        panel_layout = panel.layout()
        panel_layout.invalidate()
        panel_layout.activate()
        panel.setFixedWidth(max(self.option_panel_width(tool), panel.sizeHint().width()))
        panel.updateGeometry()
        panel.adjustSize()
        panel_layout.invalidate()
        panel_layout.activate()
        menu = self.options_button.menu()
        menu.setFixedWidth(panel.width())
        menu.setMinimumHeight(0)
        menu.setMinimumHeight(panel.sizeHint().height() + 4)
        menu.adjustSize()

    @staticmethod
    def option_panel_width(tool):
        """不同工具的选项数量不同，动态控制更多设置弹窗宽度。"""
        widths = {
            "arrow": 760,
            "text": 540,
            "marker": 480,
            "mosaic": 430,
            "crop": 430,
            "rect": 480,
            "ellipse": 480,
            "pen": 480,
            "eraser": 340,
            "number": 480,
        }
        return widths.get(tool, 340)

    def group(self, title):
        """标题紧贴自己的操作，不依赖其他组的宽度。"""
        container = QWidget()
        container.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Preferred)
        column = QVBoxLayout(container)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(1)
        label = QLabel(title)
        header = QHBoxLayout()
        header.addWidget(label)
        header.addStretch()
        column.addLayout(header)
        if not hasattr(self, "section_headers"):
            self.section_headers = []
        self.section_headers.append(header)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        column.addLayout(row)
        self.sections.append(container)
        return row

    def reflow(self, width):
        tool_order = [self.cursor_switch, self.options_button,
                      self.tool_buttons["select"],
                      *(self.tool_buttons[key] for key in
                        ("pen", "marker", "rect", "ellipse", "number", "text", "arrow",
                                                 "mosaic", "eraser", "picker", "crop")
                        if key != "crop" or not self.property("inline_edit"))]
        gap = self.tool_grid.horizontalSpacing()
        tool_widths = ([widget.width() for widget in tool_order] if self.property("inline_edit") else
                       [max(widget.sizeHint().width(), widget.minimumWidth())
                        for widget in tool_order])
        drawing_width = sum(tool_widths) + gap * (len(tool_order) - 1)
        other_widths = [sum(button.sizeHint().width() for button in buttons) +
                max(grid.horizontalSpacing(), 3) * (len(buttons) - 1)
                        for grid, buttons in ((self.edit_grid, self.edit_buttons),
                                              (self.image_grid, self.image_buttons),
                                              (self.output_grid, self.output_buttons))]
        margins = self.section_layout.contentsMargins()
        available = width - margins.left() - margins.right()
        wide_layout = available >= drawing_width + sum(other_widths) + 3 * max(
            self.section_layout.horizontalSpacing(), 12)
        if self.property("inline_edit"):
            positions = [(0, 0, 1), (2, 0, 1), (2, 1, 1), (1, 0, 1)]
        elif wide_layout:
            positions = [(0, 0, 1), (0, 1, 1), (0, 2, 1), (0, 3, 1)]
        elif width >= 1200:
            positions = [(0, 0, 7), (1, 0, 7), (2, 0, 7)]
        elif width >= 875:
            positions = [(0, 0, 5), (1, 0, 5), (2, 0, 5)]
        else:
            positions = [(0, 0, 1), (1, 0, 1), (2, 0, 1), (3, 0, 1)]
        drawing_space = (available - sum(other_widths) - 3 * self.section_layout.horizontalSpacing()
                         if wide_layout else available)
        if self.property("inline_edit"):
            columns = len(tool_order)
        else:
            columns = 2
            for count in range(2, len(tool_order) + 1):
                needed = sum(max(tool_widths[index] for index in range(column, len(tool_order), count))
                             for column in range(count)) + gap * (count - 1)
                if needed <= drawing_space:
                    columns = count
        self.sections[0].setMinimumWidth(0)
        image_columns = (8 if available >= other_widths[0] + other_widths[1] +
                 self.section_layout.horizontalSpacing() else 4 if width >= 500 else 3)
        mode = (tuple(positions), columns, image_columns)
        if mode == self._layout_mode:
            return
        self._layout_mode = mode
        trailing_column = (None if self.property("inline_edit") else
                   3 if wide_layout else 6 if width >= 1200 else 4 if width >= 875 else 0)
        for index in range(7):
            self.section_layout.setColumnStretch(index, int(index == trailing_column))
        while self.tool_grid.count():
            self.tool_grid.takeAt(0)
        for index, button in enumerate(tool_order):
            row, column = divmod(index, columns)
            self.tool_grid.addWidget(button, row, column, Qt.AlignVCenter)
        for grid, buttons, count in ((self.edit_grid, self.edit_buttons, 3 if width < 500 else 5),
                         (self.image_grid, self.image_buttons, image_columns),
                 (self.output_grid, self.output_buttons,
                  3 if width < 500 else len(self.output_buttons))):
            for index, button in enumerate(buttons):
                grid.addWidget(button, index // count, index % count, Qt.AlignVCenter)
        for container in self.sections:
            self.section_layout.removeWidget(container)
        self.section_layout.removeWidget(self.edit_image_row)
        for container in self.sections[1:3]:
            self.edit_image_layout.removeWidget(container)
        if self.property("inline_edit") or wide_layout or width < 875:
            for container, (row, column, span) in zip(self.sections, positions):
                self.section_layout.addWidget(container, row, column, 1, span,
                                              Qt.AlignLeft | Qt.AlignTop)
        else:
            for container in self.sections[1:3]:
                self.edit_image_layout.insertWidget(self.edit_image_layout.count() - 1,
                                                    container, 0, Qt.AlignTop)
            for container, (row, column, span) in zip(
                    (self.sections[0], self.edit_image_row, self.sections[3]), positions):
                self.section_layout.addWidget(container, row, column, 1, span,
                                              Qt.AlignLeft | Qt.AlignTop)
        self.setMinimumHeight(0)
        self.setMaximumHeight(16777215)
        for grid in (self.tool_grid, self.edit_grid, self.image_grid, self.output_grid):
            grid.invalidate()
        for container in self.sections:
            container.layout().invalidate()
        self.section_layout.invalidate()
        self.section_layout.activate()
        self.updateGeometry()
        QTimer.singleShot(0, self.sync_height)

    def set_active_tool(self, tool, color_tool=None):
        self.current_tool = tool
        color_tool = color_tool or tool
        color = self.settings.get(f"{color_tool}_color", self.settings.get("pen_color", "#ff0000"))
        self.pen_color.set_color(color)
        self.active_color_label.setText(f"{color_tool} 颜色")

    def sync_tool_color(self, key, color):
        button = self.tool_color_buttons.get(key)
        if button is not None:
            button.set_color(color)
        if key == f"{self.current_tool}_color":
            self.pen_color.set_color(color)

    def sync_tool_colors(self, settings):
        for key, button in self.tool_color_buttons.items():
            button.set_color(settings.get(key, DEFAULTS[key]))
        self.set_active_tool(self.current_tool)

    def sync_height(self):
        try:
            self.section_layout.invalidate()
            self.section_layout.activate()
            self.updateGeometry()
            parent_layout = self.parentWidget().layout() if self.parentWidget() else None
            if parent_layout is not None:
                parent_layout.invalidate()
                parent_layout.activate()
        except RuntimeError:
            # 窗口快速关闭时，QTimer 延迟回调可能晚于底层 Qt 对象释放。
            return

    def minimumSizeHint(self):
        return QSize(320, 0)

    def resizeEvent(self, event):
        self.reflow(event.size().width())
        super().resizeEvent(event)

    def button(self, layout, label, action, icon):
        """常用命令同时展示 Qt 标准图标和文字，完整名称留在悬浮提示中。"""
        button = QToolButton()
        button.setIcon(icon if isinstance(icon, QIcon) else self.style().standardIcon(icon))
        button.setText(label)
        button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        descriptions = {
            "undo": "撤销上一步编辑操作", "redo": "重做已撤销的操作",
            "delete": "删除选中的标注", "reset": "重置当前标注参数",
            "paste": "将当前合成图像作为独立贴图打开",
            "save": "保存为 PNG，复制图像和路径，然后退出编辑",
            "copy_only": "仅把当前合成图像复制到剪贴板，不保存文件、不退出编辑",
            "discard": "放弃编辑并关闭窗口，不保存当前更改",
            "close_all_editors": "关闭当前已打开的全部截图编辑窗口",
            "left": "向左旋转 90°", "right": "向右旋转 90°",
            "half": "旋转 180°", "angle": "预览并应用自定义旋转角度",
            "reset_rotation": "恢复最近一次任意角度旋转；后续编辑后不可用",
            "horizontal": "沿水平方向翻转截图及标注",
            "vertical": "沿垂直方向翻转截图及标注",
            "custom_size": "按指定宽高重设选区，中心位置保持不变，并立即按新尺寸重新载入该区域画面",
            "recapture": "放弃当前画面并重新进入截图，可等画面变化后再框选同一区域",
        }
        binding = shortcut_label(self.hotkeys, action)
        tooltip = descriptions.get(action, label)
        button.setProperty("tooltip_detail", tooltip)
        button.setToolTip(rich_tooltip(label, tooltip, binding))
        button.setAccessibleName(label)
        button.setFixedHeight(34)
        button.clicked.connect(lambda: self.command.emit(action))
        self.command_buttons.append((button, action))
        layout.addWidget(button, 0, layout.count())
        return button

    def menu_button(self, layout, label, actions):
        """低频操作收入菜单，避免所有按钮占满一行。"""
        button = QToolButton()
        button.setText(label)
        menu_icons = {"层级": QStyle.SP_FileDialogDetailedView,
                  "旋转 / 翻转": QStyle.SP_BrowserReload,
                  "复制": QStyle.SP_FileDialogContentsView}
        button.setIcon(self.style().standardIcon(menu_icons[label]))
        if label == "图层":
            button.setIcon(action_icon("layers"))
        elif label == "旋转 / 翻转":
            button.setIcon(action_icon("rotate"))
        button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        button.setToolTip({"层级": "调整选中标注的前后层级"}.get(label, label))
        button.setPopupMode(QToolButton.InstantPopup)
        menu = QMenu(button)
        action_icons = {"top": QStyle.SP_ArrowUp, "bottom": QStyle.SP_ArrowDown,
                        "up": QStyle.SP_ArrowUp, "down": QStyle.SP_ArrowDown,
                        "left": QStyle.SP_ArrowBack, "right": QStyle.SP_ArrowForward,
                        "half": QStyle.SP_BrowserReload, "angle": QStyle.SP_BrowserReload,
                        "horizontal": QStyle.SP_ArrowLeft, "vertical": QStyle.SP_ArrowDown,
                        "copy": QStyle.SP_FileDialogContentsView, "path": QStyle.SP_FileLinkIcon}
        for title, action in actions:
            icon = action_icons[action]
            if action in ("top", "bottom", "left", "right", "half", "angle", "horizontal", "vertical"):
                icon = action_icon({"top": "layer_top", "bottom": "layer_bottom",
                                    "left": "rotate_left", "right": "rotate_right",
                                    "half": "rotate_180", "angle": "rotate",
                                    "horizontal": "flip_horizontal", "vertical": "flip_vertical"}[action])
            elif action == "copy":
                icon = action_icon("clipboard_image")
            else:
                icon = self.style().standardIcon(icon)
            menu_action = menu.addAction(icon, title,
                                         lambda checked=False, current=action: self.command.emit(current))
            menu_action.setData(action)
            menu_action.setProperty("base_label", title)
            binding = shortcut_label(self.hotkeys, action)
            if binding:
                menu_action.setText(f"{title} ({binding})")
            menu_action.setToolTip({
                "top": "将标注移到最上层", "bottom": "将标注移到最底层",
                "up": "将标注上移一层", "down": "将标注下移一层",
                "copy": "复制当前合成图像", "path": "复制图像文件路径",
            }.get(action, title))
        button.setMenu(menu)
        button.setMinimumHeight(34)
        self.menu_buttons.append(button)
        layout.addWidget(button, 0, layout.count())
        return button

    def radio_options(self, key, options, selected, tooltip, columns=0):
        container = QWidget(self)
        group = QButtonGroup(self)
        buttons = {}
        if columns and columns > 0:
            layout = QGridLayout(container)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setHorizontalSpacing(10)
            layout.setVerticalSpacing(4)
            for index, (label, value) in enumerate(options):
                button = QRadioButton(label, container)
                group.addButton(button)
                button.setChecked(value == selected)
                button.toggled.connect(
                    lambda checked, current=value: checked and self.setting_changed.emit(key, current))
                row, column = divmod(index, columns)
                layout.addWidget(button, row, column)
                buttons[value] = button
        else:
            layout = QHBoxLayout(container)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(10)
            for label, value in options:
                button = QRadioButton(label, container)
                group.addButton(button)
                button.setChecked(value == selected)
                button.toggled.connect(
                    lambda checked, current=value: checked and self.setting_changed.emit(key, current))
                layout.addWidget(button)
                buttons[value] = button
            layout.addStretch(1)
        container.setToolTip(tooltip)
        self.choice_buttons[key] = buttons
        return container

    def set_choice(self, key, value):
        for option, button in self.choice_buttons[key].items():
            with QSignalBlocker(button):
                button.setChecked(option == value)

    def _apply_sequence_preset(self, preset_key):
        """套用序号预设组合，同步本地控件与画布设置。"""
        from editor.annotation_items import SEQUENCE_PRESETS
        preset = SEQUENCE_PRESETS.get(preset_key)
        if not preset:
            return
        for key, value in preset.items():
            self.setting_changed.emit(key, value)
            if key == "sequence_shape":
                self.set_choice("sequence_shape", value)
            elif key == "sequence_fill_color":
                self.sequence_fill.set_color(value)
            elif key == "sequence_text_color":
                self.sequence_text.set_color(value)
        self.refresh_previews()

    def toggle_appearance_menu(self, checked=False):
        if not checked:
            self._hide_appearance_menu()
            return
        with QSignalBlocker(self.appearance_toggle):
            self.appearance_toggle.setChecked(True)
        self.output_preview.refresh()
        screen = self.appearance_toggle.screen() or QGuiApplication.primaryScreen()
        available_height = screen.availableGeometry().height() if screen else 800
        max_height = max(220, available_height - 72)
        self.appearance_panel.adjustSize()
        panel_height = self.appearance_panel.sizeHint().height()
        self.appearance_scroll.setFixedHeight(min(panel_height, max_height))
        self.appearance_menu.setMaximumHeight(max_height + 20)
        position = self.appearance_toggle.mapToGlobal(
            self.appearance_toggle.rect().bottomLeft())
        popup_size = self.appearance_menu.sizeHint()
        screen = self.appearance_toggle.screen() or QGuiApplication.primaryScreen()
        if screen is not None:
            available = screen.availableGeometry()
            if position.y() + min(panel_height, max_height) > available.bottom():
                position.setY(self.appearance_toggle.mapToGlobal(
                    self.appearance_toggle.rect().topLeft()).y() - min(panel_height, max_height))
            position.setX(min(position.x(), available.right() - popup_size.width()))
        self.appearance_menu.move(position)
        self.appearance_menu.show()
        self.appearance_menu.raise_()
        logging.getLogger("screensnap").debug("打开输出外观弹层")

    def eventFilter(self, watched, event):
        if watched is self.appearance_menu and event.type() == QEvent.Hide:
            self._appearance_menu_hidden()
        if self.appearance_menu.isVisible() and event.type() == QEvent.MouseButtonPress:
            if (watched is not self.appearance_toggle and
                    not self._is_appearance_menu_interaction(watched, event)):
                self._hide_appearance_menu()
        return super().eventFilter(watched, event)

    def _is_appearance_menu_interaction(self, watched, event):
        if (watched is self.appearance_menu or
                isinstance(watched, QWidget) and
                self.appearance_menu.isAncestorOf(watched)):
            return True
        if QApplication.activeModalWidget() is not None:
            return True
        return self.appearance_menu.frameGeometry().contains(
            event.globalPosition().toPoint())

    def _hide_appearance_menu(self):
        self.appearance_menu.hide()

    def _appearance_menu_hidden(self):
        cursor_position = self.appearance_toggle.mapFromGlobal(QCursor.pos())
        if (QApplication.mouseButtons() & Qt.LeftButton and
                self.appearance_toggle.rect().contains(cursor_position)):
            return
        if self.appearance_toggle.isChecked():
            with QSignalBlocker(self.appearance_toggle):
                self.appearance_toggle.setChecked(False)
        logging.getLogger("screensnap").debug("关闭输出外观弹层")

    def _remove_appearance_event_filter(self):
        app = QGuiApplication.instance()
        if app is not None:
            app.removeEventFilter(self)

    def sync_appearance_controls(self, settings):
        for key, control in self.appearance_controls.items():
            value = settings.get(key, DEFAULTS[key])
            if isinstance(control, ColorButton):
                control.set_color(value)
            else:
                with QSignalBlocker(control):
                    if isinstance(control, QCheckBox):
                        control.setChecked(bool(value))
                    else:
                        control.setValue(value)
                if key in self.appearance_value_labels:
                    suffix = "%" if "strength" in key else " px"
                    self.appearance_value_labels[key].setText(f"{value}{suffix}")
        self.output_preview.refresh()

    def set_hotkeys(self, hotkeys):
        self.hotkeys = hotkeys or {}
        for button, action in self.command_buttons:
            binding = shortcut_label(self.hotkeys, action)
            detail = button.property("tooltip_detail") or button.text()
            button.setToolTip(rich_tooltip(button.text(), detail, binding))
        for menu_button in self.menu_buttons:
            for action in menu_button.menu().actions():
                binding = shortcut_label(self.hotkeys, action.data())
                if action.data() and binding:
                    action.setText(f"{action.property('base_label')} ({binding})")
                elif action.data():
                    action.setText(action.property("base_label"))