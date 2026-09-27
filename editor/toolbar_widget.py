"""编辑器工具栏。"""

from PySide6.QtCore import Signal, Qt, QSize, QTimer, QSignalBlocker
from PySide6.QtGui import QFont, QIcon, QPainter, QPainterPath, QPen, QPixmap, QColor
from PySide6.QtWidgets import (QWidget, QHBoxLayout, QVBoxLayout, QGridLayout, QLabel,
                               QCheckBox, QToolButton, QMenu, QStyle, QButtonGroup,
                               QSlider, QFontComboBox, QWidgetAction, QSizePolicy,
                               QRadioButton, QSpinBox)
from ui.widgets.color_button import ColorButton
from config.config_manager import TOOL_WIDTH_KEYS
from core.constants import shortcut_label


def settings_icon():
    pixmap = QPixmap(24, 24)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(QPen(QColor("#ffffff"), 2, Qt.SolidLine, Qt.RoundCap))
    for y, knob in ((5, 9), (12, 16), (19, 7)):
        painter.drawLine(3, y, 21, y)
        painter.setBrush(QColor("#ffe0ca"))
        painter.drawEllipse(knob - 2, y - 2, 4, 4)
    painter.end()
    return QIcon(pixmap)


def text_icon():
    pixmap = QPixmap(24, 24)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    font = QFont("", 17, QFont.Bold)
    painter.setFont(font)
    painter.setPen(QColor("#355f68"))
    painter.drawText(pixmap.rect(), Qt.AlignCenter, "T")
    painter.end()
    return QIcon(pixmap)


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
    painter.end()
    return QIcon(pixmap)


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

    def __init__(self, pen_color="#ff5252", settings=None):
        super().__init__()
        settings = settings or {}
        self.hotkeys = settings.get("hotkeys", {})
        self.choice_buttons = {}
        self.command_buttons = []
        self.menu_buttons = []
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.sections = []
        self.section_layout = QGridLayout(self)
        self.section_layout.setContentsMargins(8, 4, 8, 4)
        self.section_layout.setHorizontalSpacing(12)
        self.section_layout.setVerticalSpacing(6)
        self._layout_mode = None

        drawing = self.group("标注")
        tool_grid = QGridLayout()
        tool_grid.setContentsMargins(0, 0, 0, 0)
        tool_grid.setHorizontalSpacing(5)
        tool_grid.setVerticalSpacing(6)
        drawing.addLayout(tool_grid)
        self.tools = QButtonGroup(self)
        self.tools.setExclusive(True)
        self.tool_buttons = {}
        for label, key in [("选择", "select"), ("画笔", "pen"),
                           ("荧光笔", "marker"), ("文字", "text"), ("箭头", "arrow"),
                           ("矩形", "rect"), ("椭圆", "ellipse"), ("橡皮擦", "eraser"),
                           ("马赛克", "mosaic"), ("取色", "picker"), ("裁剪", "crop")]:
            button = QToolButton()
            icon = self.style().standardIcon(QStyle.SP_TrashIcon) if key == "eraser" else annotation_icon(key)
            button.setIcon(icon)
            button.setIconSize(QSize(20, 20))
            button.setText(label)
            button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
            tool_tips = {
                "select": "选择、移动或调整现有标注",
                "pen": "自由绘制线条；线宽和颜色可在更多设置中调整",
                "marker": "绘制半透明荧光标记",
                "text": "单击画布输入文字；字体、字号和对齐可在更多设置中调整",
                "arrow": "拖动绘制箭头；可选实心、空心或双向样式",
                "rect": "拖动绘制矩形",
                "ellipse": "拖动绘制椭圆",
                "eraser": "拖过标注以擦除内容",
                "mosaic": "拖动区域应用马赛克效果",
                "picker": "从截图中取色并设为当前标注颜色",
                "crop": "拖动裁剪截图；边框颜色和线宽为本次编辑单独设置",
            }
            button.setToolTip(tool_tips[key])
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
        self.tool_widths = {tool: settings.get(key, settings.get("pen_width", 3))
                    for tool, key in TOOL_WIDTH_KEYS.items()}
        self.pen_color = ColorButton(pen_color, self.color_changed.emit, compact=True)
        self.pen_color.setMinimumHeight(40)
        self.pen_color.setToolTip("标注颜色")
        self.pen_width = QSlider(Qt.Horizontal)
        self.pen_width.setRange(1, 50)
        self.pen_width.setValue(self.tool_widths.get(settings.get("annotation_tool"), 3))
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
                            ("实心双向", "double_filled"), ("空心双向", "double")),
            settings.get("arrow_style", "filled"), "设置新箭头的箭头头部样式")
        self.arrow_style.setToolTip("设置新箭头的箭头头部样式")
        self.crop_color = ColorButton("#00ad91",
                          lambda color: self.crop_style_changed.emit("crop_color", color),
                          compact=True, purpose="本次裁剪框颜色")
        self.crop_color.setMinimumHeight(36)
        self.crop_width = QSlider(Qt.Horizontal)
        self.crop_width.setRange(1, 12)
        self.crop_width.setValue(2)
        self.crop_width.setMinimumWidth(240)
        self.crop_width.setMinimumHeight(36)
        self.crop_width.setToolTip("设置当前截图裁剪框的边线宽度；不保存到全局配置")
        self.crop_width_label = QLabel("2 px")
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
        options.setStyleSheet(
            "QToolButton { background: #b44726; color: white; border: 1px solid #8e3219; "
            "border-radius: 4px; font-weight: 600; padding: 0 8px; }"
            "QToolButton:enabled:hover { background: #99371b; }"
            "QToolButton:enabled:pressed { background: #782811; }"
            "QToolButton:disabled { background: #607e83; color: white; "
            "border-color: #4c696e; }"
        )
        options.setToolTip("线宽、透明度、字体、对齐、效果和箭头")
        options.setPopupMode(QToolButton.InstantPopup)
        menu = QMenu(options)
        panel = _OptionsPanel(menu)
        panel.setMinimumWidth(430)
        panel_layout = QGridLayout(panel)
        panel_layout.setContentsMargins(16, 14, 16, 14)
        panel_layout.setHorizontalSpacing(12)
        panel_layout.setVerticalSpacing(10)
        panel_layout.setColumnStretch(1, 1)
        panel_layout.addWidget(QLabel("线宽"), 0, 0)
        panel_layout.addWidget(self.pen_width, 0, 1)
        panel_layout.addWidget(self.pen_width_label, 0, 2)
        panel_layout.addWidget(QLabel("透明度"), 1, 0)
        panel_layout.addWidget(self.marker_opacity, 1, 1)
        panel_layout.addWidget(self.marker_opacity_label, 1, 2)
        panel_layout.addWidget(QLabel("字体"), 2, 0)
        panel_layout.addWidget(self.font, 2, 1, 1, 2)
        panel_layout.addWidget(QLabel("字号"), 3, 0)
        panel_layout.addWidget(self.font_size, 3, 1, 1, 2)
        panel_layout.addWidget(QLabel("对齐"), 4, 0)
        panel_layout.addWidget(self.alignment, 4, 1, 1, 2)
        panel_layout.addWidget(QLabel("效果"), 5, 0)
        panel_layout.addWidget(self.mosaic_mode, 5, 1, 1, 2)
        panel_layout.addWidget(QLabel("颗粒"), 6, 0)
        panel_layout.addWidget(self.mosaic_size, 6, 1)
        panel_layout.addWidget(self.mosaic_size_label, 6, 2)
        panel_layout.addWidget(QLabel("样式"), 7, 0)
        panel_layout.addWidget(self.arrow_style, 7, 1, 1, 2)
        panel_layout.addWidget(QLabel("裁剪颜色"), 8, 0)
        panel_layout.addWidget(self.crop_color, 8, 1, 1, 2)
        panel_layout.addWidget(QLabel("裁剪线宽"), 9, 0)
        panel_layout.addWidget(self.crop_width, 9, 1)
        panel_layout.addWidget(self.crop_width_label, 9, 2)
        for row in range(10):
            label = panel_layout.itemAtPosition(row, 0).widget()
            label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            label.setMinimumHeight(38)
        for index in range(panel_layout.count()):
            widget = panel_layout.itemAt(index).widget()
            if widget is not None:
                panel_layout.setAlignment(widget, Qt.AlignVCenter)
        self.option_rows = [tuple(panel_layout.itemAtPosition(row, column).widget()
                                  for column in range(3) if panel_layout.itemAtPosition(row, column))
                            for row in range(10)]
        panel_action = QWidgetAction(menu)
        panel_action.setDefaultWidget(panel)
        menu.addAction(panel_action)
        options.setMenu(menu)
        self.options_button = options
        for tool, button in self.tool_buttons.items():
            button.clicked.connect(lambda checked=False, selected=tool: self.set_tool_mode(selected))
        self.set_tool_mode(next(tool for tool, button in self.tool_buttons.items() if button.isChecked()))

        editing = self.group("编辑")
        self.edit_grid = QGridLayout()
        self.edit_grid.setSpacing(3)
        editing.addLayout(self.edit_grid)
        editing.addStretch(1)
        self.edit_buttons = []
        for label, key, icon in [("撤销", "undo", QStyle.SP_ArrowBack),
                                 ("重做", "redo", QStyle.SP_ArrowForward),
                     ("删除", "delete", QStyle.SP_TrashIcon)]:
            self.edit_buttons.append(self.button(self.edit_grid, label, key, icon))
        self.edit_buttons.append(self.menu_button(self.edit_grid, "层级", [("置顶", "top"), ("置底", "bottom"),
                                          ("上移一层", "up"), ("下移一层", "down")]))
        self.edit_buttons.append(self.button(self.edit_grid, "重置", "reset", QStyle.SP_BrowserReload))

        image = self.group("图像旋转")
        self.image_grid = QGridLayout()
        self.image_grid.setSpacing(3)
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
            self.image_buttons.append(button)
            if action == "reset_rotation":
                self.reset_rotation_button = button
                button.setToolTip("重置最近一次任意角度旋转；后续编辑后自动禁用")
                button.setEnabled(False)

        output = self.group("输出")
        self.output_grid = QGridLayout()
        self.output_grid.setSpacing(3)
        output.addLayout(self.output_grid)
        output.addStretch(1)
        self.output_buttons = [self.button(self.output_grid, label, action, icon)
                               for label, action, icon in [("贴图", "paste", QStyle.SP_DesktopIcon),
                                                           ("保存", "save", QStyle.SP_DialogSaveButton),
                                                           ("完成", "finish", QStyle.SP_DialogApplyButton),
                                                           ("放弃", "discard", QStyle.SP_DialogCancelButton)]]
        self.edit_image_row = QWidget(self)
        self.edit_image_layout = QHBoxLayout(self.edit_image_row)
        self.edit_image_layout.setContentsMargins(0, 0, 0, 0)
        self.edit_image_layout.setSpacing(self.section_layout.horizontalSpacing())
        self.edit_image_layout.addStretch()
        self.reflow(1100)

    def change_tool_width(self, value):
        tool = next((key for key, button in self.tool_buttons.items() if button.isChecked()), None)
        if tool in TOOL_WIDTH_KEYS:
            self.tool_widths[tool] = value
            self.setting_changed.emit(TOOL_WIDTH_KEYS[tool], value)

    def set_tool_mode(self, tool):
        """只展示当前工具可用的参数；切换时不改写别的工具的线宽。"""
        rows = ({0} if tool in TOOL_WIDTH_KEYS else set())
        if tool == "marker":
            rows.add(1)
        elif tool == "text":
            rows.update((2, 3, 4))
        elif tool == "mosaic":
            rows.update((5, 6))
        elif tool == "arrow":
            rows.update((0, 7))
        elif tool == "crop":
            rows.update((8, 9))
        # 同一个弹出面板只展示当前工具的参数，切换时保留各自的线宽。
        for index, widgets in enumerate(self.option_rows):
            for widget in widgets:
                widget.setVisible(index in rows)
        self.options_button.setEnabled(bool(rows))
        name = self.tool_buttons[tool].text()
        self.options_button.setText(f"{name}设置" if rows else "更多设置")
        descriptions = {
            "crop": "设置本次裁剪框的颜色和线宽，不影响全局配置",
            "text": "设置新文字的字体、字号与对齐方式",
            "arrow": "设置新箭头的线宽和箭头样式",
            "mosaic": "设置马赛克类型和颗粒大小",
            "marker": "设置荧光笔线宽和透明度",
            "eraser": "设置橡皮擦直径",
        }
        self.options_button.setToolTip(descriptions.get(tool, f"设置{name}参数") if rows else
                                       "当前工具没有可调整的专属参数")
        self.option_rows[0][0].setText("直径" if tool == "eraser" else "线宽")
        if tool in TOOL_WIDTH_KEYS:
            with QSignalBlocker(self.pen_width):
                self.pen_width.setValue(self.tool_widths[tool])
            self.pen_width_label.setText(f"{self.pen_width.value()} px")
        panel = self.options_button.menu().actions()[0].defaultWidget()
        panel.updateGeometry()
        panel.adjustSize()
        panel_layout = panel.layout()
        panel_layout.invalidate()
        panel_layout.activate()
        self.options_button.menu().setMinimumHeight(0)
        self.options_button.menu().setMinimumHeight(panel.sizeHint().height() + 4)
        self.options_button.menu().adjustSize()

    def group(self, title):
        """标题紧贴自己的操作，不依赖其他组的宽度。"""
        container = QWidget()
        container.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Preferred)
        column = QVBoxLayout(container)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(2)
        label = QLabel(title)
        label.setStyleSheet("color: #425b61; font-weight: 600;")
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
        tool_order = [self.cursor_switch, self.pen_color, self.options_button,
                      self.tool_buttons["select"],
                      *(self.tool_buttons[key] for key in
                        ("pen", "marker", "rect", "ellipse", "text", "arrow",
                         "mosaic", "eraser", "picker", "crop"))]
        gap = self.tool_grid.horizontalSpacing()
        tool_widths = [max(widget.sizeHint().width(), widget.minimumWidth())
                       for widget in tool_order]
        for widget, required_width in zip(tool_order, tool_widths):
            widget.setMinimumWidth(required_width)
        drawing_width = sum(tool_widths) + gap * (len(tool_order) - 1)
        other_widths = [sum(button.sizeHint().width() for button in buttons) +
                        grid.horizontalSpacing() * (len(buttons) - 1)
                        for grid, buttons in ((self.edit_grid, self.edit_buttons),
                                              (self.image_grid, self.image_buttons),
                                              (self.output_grid, self.output_buttons))]
        margins = self.section_layout.contentsMargins()
        available = width - margins.left() - margins.right()
        wide_layout = available >= drawing_width + sum(other_widths) + 3 * self.section_layout.horizontalSpacing()
        if wide_layout:
            positions = [(0, 0, 1), (0, 1, 1), (0, 2, 1), (0, 3, 1)]
        elif width >= 1200:
            positions = [(0, 0, 7), (1, 0, 7), (2, 0, 7)]
        elif width >= 875:
            positions = [(0, 0, 5), (1, 0, 5), (2, 0, 5)]
        else:
            positions = [(0, 0, 1), (1, 0, 1), (2, 0, 1), (3, 0, 1)]
        drawing_space = (available - sum(other_widths) - 3 * self.section_layout.horizontalSpacing()
                         if wide_layout else available)
        columns = 2
        for count in range(2, len(tool_order) + 1):
            needed = sum(max(tool_widths[index] for index in range(column, len(tool_order), count))
                         for column in range(count)) + gap * (count - 1)
            if needed <= drawing_space:
                columns = count
        required_width = sum(max(tool_widths[index] for index in range(column, len(tool_order), columns))
                             for column in range(columns)) + gap * (columns - 1)
        self.sections[0].setMinimumWidth(required_width)
        image_columns = (8 if available >= other_widths[0] + other_widths[1] +
                 self.section_layout.horizontalSpacing() else 4 if width >= 500 else 3)
        mode = (tuple(positions), columns, image_columns)
        if mode == self._layout_mode:
            return
        self._layout_mode = mode
        trailing_column = 3 if wide_layout else 6 if width >= 1200 else 4 if width >= 875 else 0
        for index in range(7):
            self.section_layout.setColumnStretch(index, int(index == trailing_column))
        while self.tool_grid.count():
            self.tool_grid.takeAt(0)
        for index, button in enumerate(tool_order):
            row, column = divmod(index, columns)
            self.tool_grid.addWidget(button, row, column, Qt.AlignVCenter)
        for grid, buttons, count in ((self.edit_grid, self.edit_buttons, 3 if width < 500 else 5),
                         (self.image_grid, self.image_buttons, image_columns),
                         (self.output_grid, self.output_buttons, 2 if width < 500 else 4)):
            for index, button in enumerate(buttons):
                grid.addWidget(button, index // count, index % count, Qt.AlignVCenter)
        for container in self.sections:
            self.section_layout.removeWidget(container)
        self.section_layout.removeWidget(self.edit_image_row)
        for container in self.sections[1:3]:
            self.edit_image_layout.removeWidget(container)
        if wide_layout or width < 875:
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
        self.setFixedHeight(self.section_layout.sizeHint().height())
        QTimer.singleShot(0, self.sync_height)

    def sync_height(self):
        self.section_layout.invalidate()
        self.section_layout.activate()
        height = self.section_layout.sizeHint().height()
        if self.height() != height:
            self.setFixedHeight(height)

    def minimumSizeHint(self):
        return QSize(320, 0)

    def resizeEvent(self, event):
        self.reflow(event.size().width())
        super().resizeEvent(event)

    def button(self, layout, label, action, icon):
        """常用命令同时展示 Qt 标准图标和文字，完整名称留在悬浮提示中。"""
        button = QToolButton()
        button.setIcon(self.style().standardIcon(icon))
        button.setText(label)
        button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        descriptions = {
            "undo": "撤销上一步编辑操作", "redo": "重做已撤销的操作",
            "delete": "删除选中的标注", "reset": "重置当前标注参数",
            "paste": "将当前合成图像作为独立贴图打开",
            "save": "保存为 PNG，并将图像和保存路径复制到剪贴板",
            "finish": "按完成设置保存或复制图像，然后关闭编辑器",
            "discard": "放弃编辑并关闭窗口，不保存当前更改",
            "left": "向左旋转 90°", "right": "向右旋转 90°",
            "half": "旋转 180°", "angle": "预览并应用自定义旋转角度",
            "reset_rotation": "恢复最近一次任意角度旋转；后续编辑后不可用",
            "horizontal": "沿水平方向翻转截图及标注",
            "vertical": "沿垂直方向翻转截图及标注",
        }
        binding = shortcut_label(self.hotkeys, action)
        tooltip = descriptions.get(action, label)
        button.setToolTip(f"{tooltip} (快捷键: {binding})" if binding else tooltip)
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
        button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        button.setToolTip({"层级": "调整选中标注的前后层级"}.get(label, label))
        button.setPopupMode(QToolButton.InstantPopup)
        menu = QMenu(button)
        action_icons = {"top": QStyle.SP_ArrowUp, "bottom": QStyle.SP_ArrowDown,
                        "up": QStyle.SP_ArrowUp, "down": QStyle.SP_ArrowDown,
                        "left": QStyle.SP_ArrowBack, "right": QStyle.SP_ArrowForward,
                        "half": QStyle.SP_BrowserReload, "angle": QStyle.SP_BrowserReload,
                        "horizontal": QStyle.SP_ArrowLeft, "vertical": QStyle.SP_ArrowDown,
                        "copy": QStyle.SP_FileIcon, "path": QStyle.SP_FileLinkIcon}
        for title, action in actions:
            menu_action = menu.addAction(self.style().standardIcon(action_icons[action]), title,
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

    def radio_options(self, key, options, selected, tooltip):
        container = QWidget(self)
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        group = QButtonGroup(self)
        buttons = {}
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

    def set_hotkeys(self, hotkeys):
        self.hotkeys = hotkeys or {}
        for button, action in self.command_buttons:
            binding = shortcut_label(self.hotkeys, action)
            button.setToolTip(f"{button.text()} (快捷键: {binding})" if binding else button.text())
        for menu_button in self.menu_buttons:
            for action in menu_button.menu().actions():
                binding = shortcut_label(self.hotkeys, action.data())
                if action.data() and binding:
                    action.setText(f"{action.property('base_label')} ({binding})")
                elif action.data():
                    action.setText(action.property("base_label"))