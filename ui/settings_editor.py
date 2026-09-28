"""编辑器工具的默认样式。"""

from PySide6.QtGui import QFontDatabase
from ui.widgets.color_button import ColorButton
from ui.widgets.tooltip import SettingsPage


class EditorPage(SettingsPage):
    """编辑器默认标注样式；字体列表从系统安装字体中读取。"""

    def __init__(self, config, changed):
        super().__init__(config, changed)
        self.color_buttons = {}
        self.group("默认工具")
        self.choice("annotation_tool", "默认标注工具",
                    [(label, key) for label, key in (("选择", "select"), ("矩形", "rect"),
                        ("椭圆", "ellipse"), ("箭头", "arrow"), ("画笔", "pen"), ("荧光笔", "marker"),
                        ("橡皮擦", "eraser"), ("文字", "text"), ("马赛克", "mosaic"),
                        ("取色", "picker"), ("裁剪", "crop"))], "新建编辑器时默认启用的工具")
        self.group("公共标注样式")
        self.add_color("pen_color", "标注颜色")
        self.group("箭头")
        self.choice("arrow_style", "箭头样式", [("实心", "filled"), ("空心", "open"),
                                ("实心双向", "double_filled"), ("空心双向", "double"),
                               ("实心线段", "solid_line"), ("空心线段", "open_line"),
                               ("实心虚线", "solid_dash"), ("空心虚线", "open_dash")],
                    "新建箭头的箭头头部样式")
        self.number("arrow_width", "箭头线宽", 1, 50, "只影响对应工具")
        self.group("线条与形状")
        self.number("pen_width", "画笔线宽", 1, 50, "只影响画笔工具")
        for key, label in (("rect_width", "矩形线宽"), ("ellipse_width", "椭圆线宽"),
                           ("eraser_width", "橡皮擦宽度")):
            self.number(key, label, 1, 50, "只影响对应工具")
        self.choice("rect_style", "矩形线型", [("实线", "solid"), ("虚线", "dash")],
                    "新建矩形使用的边框线型")
        self.choice("ellipse_style", "椭圆线型", [("实线", "solid"), ("虚线", "dash")],
                    "新建椭圆使用的边框线型")
        self.group("荧光笔")
        self.number("marker_width", "荧光笔线宽", 1, 50, "只影响对应工具")
        self.number("marker_opacity", "荧光笔不透明度", 1, 100, "越低越能看清原图；荧光笔也使用标注颜色")
        self.group("文字")
        self.number("font_size", "文字大小", 6, 200, "默认文字字号")
        self.decimal("line_spacing", "文字行距", 0.5, 4, "多行文字的行间距倍率")
        self.choice("text_alignment", "文字对齐", [("左对齐", "left"), ("居中", "center"),
                                              ("右对齐", "right")], "新建文字标注的对齐方式")
        fonts = QFontDatabase.families()
        self.choice("font", "默认字体", [("系统默认", "")] + [(font, font) for font in fonts], "从已安装字体中选择")
        self.group("马赛克")
        self.choice("mosaic_mode", "马赛克类型", [("方块", "blocks"), ("毛玻璃", "blur"),
                             ("细粒", "fine")], "标注区域的像素处理方式")
        self.number("mosaic_size", "马赛克方块", 2, 100, "像素化块大小")
        self.group("编辑区边框")
        self.number("editor_border_width", "编辑区边框粗细", 1, 12,
                    "只在编辑画布中显示，不写入图片")
        self.add_color("editor_border_color", "编辑区边框颜色")
        self.group("裁剪框")
        self.add_color("crop_color", "裁剪框颜色")
        self.number("crop_width", "裁剪框线宽", 1, 12, "只影响编辑器裁剪框")

    def add_color(self, key, label):
        button = ColorButton(self.config.data[key],
                             lambda color: self.update_value(key, color))
        button.setToolTip("点击打开颜色选择器")
        self.form.addRow(label, button)
        self.color_buttons[key] = button