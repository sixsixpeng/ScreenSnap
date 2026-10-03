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
                        ("橡皮擦", "eraser"), ("文字", "text"), ("序号", "number"),
                        ("马赛克", "mosaic"), ("取色", "picker"), ("裁剪", "crop"))], "新建编辑器时默认启用的工具")
        self.group("箭头")
        self.preview("arrow")
        self.add_color("arrow_color", "箭头颜色")
        self.choice("arrow_style", "箭头样式", [
                                ("箭头", "filled"), ("空心箭头", "open"), ("虚线箭头", "dashed"),
                                ("双向箭头", "double_filled"), ("双向空心箭头", "double_open"), ("双向虚线箭头", "double_dashed"),
                                ("线段", "line"), ("虚线", "dashed_line"),
                                ("箭杆矩形", "rect_filled"), ("空心箭杆", "rect_open"), ("虚线箭杆", "rect_dashed")],
                    "新建箭头的样式")
        self.check("arrow_chain", "箭头多段绘制",
                   "开启后，绘制一个箭头后可继续在上一个终点与下一个点之间连续绘制相连的多段箭头；"
                   "按鼠标右键结束连续绘制并保留已画图形。")
        self.number("arrow_width", "箭头线宽", 1, 50, "只影响对应工具")
        self.group("画笔")
        self.preview("pen")
        self.add_color("pen_color", "画笔颜色")
        self.number("pen_width", "画笔线宽", 1, 50, "只影响画笔工具")
        self.check("pen_chain", "画笔多段绘制",
                   "开启后，画笔将在上一个终点与下一个点之间连续绘制相连的多段直线；"
                   "按鼠标右键结束连续绘制并保留已画图形。")
        self.group("矩形")
        self.preview("rect")
        self.add_color("rect_color", "矩形颜色")
        self.number("rect_width", "矩形线宽", 1, 50, "只影响矩形工具")
        self.choice("rect_style", "矩形线型", [("实线", "solid"), ("虚线", "dash")],
                    "新建矩形使用的边框线型")
        self.check("rect_corner_enabled", "矩形标注使用圆角", "新建矩形标注使用圆角；实线和虚线均适用")
        self.number("rect_corner_radius", "矩形标注圆角半径 (px)", 0, 100,
                "圆角半径，0 表示直角；单张图片与窗口内编辑共用此设置")
        self.check("rect_fill_enabled", "矩形填充区域", "绘制矩形时以矩形工具颜色填充区域")
        self.number("rect_fill_opacity", "矩形填充不透明度 (%)", 0, 100,
                    "0 为完全透明，100 为完全不透明")
        self.group("椭圆")
        self.preview("ellipse")
        self.add_color("ellipse_color", "椭圆颜色")
        self.number("ellipse_width", "椭圆线宽", 1, 50, "只影响椭圆工具")
        self.choice("ellipse_style", "椭圆线型", [("实线", "solid"), ("虚线", "dash")],
                    "新建椭圆使用的边框线型")
        self.check("ellipse_fill_enabled", "椭圆填充区域", "绘制椭圆时以椭圆工具颜色填充区域")
        self.number("ellipse_fill_opacity", "椭圆填充不透明度 (%)", 0, 100,
                    "0 为完全透明，100 为完全不透明")
        self.group("橡皮擦")
        self.preview("eraser")
        self.number("eraser_width", "橡皮擦直径", 10, 100, "只影响橡皮擦工具，范围 10–100")
        self.check("eraser_erase_base", "同时擦除原图",
                   "开启后橡皮擦同时擦掉截图原图；关闭则只擦标注、露出原图（非破坏性）")
        self.group("荧光笔")
        self.preview("marker")
        self.add_color("marker_color", "荧光笔颜色")
        self.number("marker_width", "荧光笔线宽", 1, 50, "只影响对应工具")
        self.number("marker_opacity", "荧光笔不透明度", 1, 100, "越低越能看清原图；荧光笔也使用标注颜色")
        self.check("marker_chain", "荧光笔多段绘制",
                   "开启后，荧光笔将在上一个终点与下一个点之间连续绘制相连的多段直线；"
                   "按鼠标右键结束连续绘制并保留已画图形。")
        self.group("文字")
        self.preview("text", 120)
        self.add_color("text_color", "文字颜色")
        self.number("font_size", "文字大小", 6, 200, "默认文字字号")
        self.decimal("line_spacing", "文字行距", 0.5, 4, "多行文字的行间距倍率")
        self.choice("text_alignment", "文字对齐", [("左对齐", "left"), ("居中", "center"),
                                             ("右对齐", "right")], "新建文字标注的对齐方式")
        fonts = QFontDatabase.families()
        self.choice("font", "默认字体", [("系统默认", "")] + [(font, font) for font in fonts], "从已安装字体中选择")
        self.check("text_bold", "粗体", "新建文字标注默认加粗")
        self.check("text_italic", "斜体", "新建文字标注默认斜体")
        self.check("text_underline", "下划线", "新建文字标注默认加下划线")
        self.check("text_strikethrough", "删除线", "新建文字标注默认加删除线")
        self.check("text_background_enabled", "文字背景", "为新建文字标注添加背景色块")
        self.add_color("text_background", "文字背景色")
        self.group("序号标注")
        self.preview("sequence", 120)
        self.choice("sequence_shape", "序号形状",
                    [("圆形", "circle"), ("方形", "square"), ("三角形", "triangle"),
                     ("菱形", "diamond"), ("五边形", "pentagon"), ("六边形", "hexagon"),
                     ("星形", "star"), ("心形", "heart"), ("箭头", "arrow"),
                     ("对话气泡", "bubble"), ("云", "cloud"), ("十字", "plus"),
                     ("水滴", "drop")],
                    "序号标记的外轮廓形状；除常用几何形状外，还提供心形/箭头/气泡/云/十字/水滴等"
                    "更具表达力的发散形状，便于按场景强调")
        self.add_color("sequence_fill_color", "序号填充色")
        self.add_color("sequence_text_color", "序号文字色")
        self.number("sequence_font_size", "序号字号", 6, 200, "新建序号标注的数字字号")
        self.number("sequence_start", "序号起始值", 0, 999, "第一个序号的号码，后续自动递增")
        self.choice("sequence_preset", "预设组合",
                    [("自定义", "custom"), ("红圆", "red_circle"), ("蓝方", "blue_square"),
                     ("绿星", "green_star"), ("琥珀菱形", "amber_diamond"),
                     ("紫五边形", "purple_pentagon"), ("青六边形", "teal_hexagon"),
                     ("红心", "red_heart"), ("蓝箭头", "blue_arrow")],
                    "一键套用形状与配色组合；选自定义后可逐项自由调整")
        self.group("马赛克")
        self.preview("mosaic")
        self.choice("mosaic_mode", "马赛克类型", [("方块", "blocks"), ("毛玻璃", "blur"),
                             ("细粒", "fine")], "标注区域的像素处理方式")
        self.number("mosaic_size", "马赛克方块", 2, 100, "像素化块大小")
        self.check("mosaic_brush", "涂抹模式（自由笔刷）",
                   "开启后按住拖动可沿笔迹涂抹马赛克/模糊；关闭则为拖框选矩形")
        self.group("编辑区边框")
        self.number("editor_border_width", "编辑区边框粗细", 1, 12,
                    "只在编辑画布中显示，不写入图片")
        self.add_color("editor_border_color", "编辑区边框颜色")
        self.group("裁剪框")
        self.preview("crop", 84)
        self.add_color("crop_color", "裁剪框颜色")
        self.number("crop_width", "裁剪框线宽", 1, 12, "只影响编辑器裁剪框")
        self.group("取色")
        self.preview("picker", 120)
        # 序号预设组合：选择后立即套用形状与配色到对应键（自定义不覆盖）。
        preset_combo = self.controls.get("sequence_preset")
        if preset_combo is not None:
            preset_combo.currentIndexChanged.connect(
                lambda _i: self._apply_sequence_preset(preset_combo.currentData()))

    def _apply_sequence_preset(self, preset_key):
        from editor.annotation_items import SEQUENCE_PRESETS
        preset = SEQUENCE_PRESETS.get(preset_key)
        if not preset:
            return
        for sub_key, value in preset.items():
            self.update_value(sub_key, value)

    def add_color(self, key, label):
        help_text = {
            "pen_color": "画笔描边颜色，用于画笔工具绘制的线条。",
            "rect_color": "矩形边框颜色；开启矩形填充时也作为填充颜色。",
            "ellipse_color": "椭圆边框颜色；开启椭圆填充时也作为填充颜色。",
            "arrow_color": "箭头线条与箭头头部的颜色。",
            "marker_color": "荧光笔颜色；与荧光笔不透明度共同决定覆盖效果。",
            "text_color": "新建文字标注的默认文字颜色。",
            "sequence_fill_color": "新建序号标注标记的底色（形状填充颜色）。",
            "sequence_text_color": "新建序号标注中数字的文字颜色。",
            "editor_border_color": "编辑画布周围的边框颜色；只显示在编辑区，不会写入导出图片。",
            "crop_color": "编辑器裁剪框的线条颜色；只用于裁剪辅助显示。",
            "text_background": "新建文字标注背景色块的颜色；仅当“文字背景”开启时生效。",
        }[key]
        button = ColorButton(self.config.data[key],
                             lambda color: self.update_value(key, color))
        button.setToolTip(help_text)
        self.form.addRow(label, button)
        self.color_buttons[key] = button