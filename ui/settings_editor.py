"""编辑器工具的默认样式。"""

from PySide6.QtCore import QRectF, QSignalBlocker, Qt
from PySide6.QtGui import QBrush, QColor, QFontDatabase, QPainter, QPalette, QPen, QPixmap
from PySide6.QtWidgets import (QCheckBox, QComboBox, QGroupBox, QHBoxLayout,
                               QSizePolicy, QSpinBox, QWidget)

from editor.annotation_presets import matching_preset, preset_values, presets_for
from logger import get_logger
from ui.widgets.color_button import ColorButton
from ui.widgets.tooltip import SettingsPage


class EditorEffectPreview(QWidget):
    """编辑器页的辅助预览：透明棋盘 / 编辑区边框 / 工具栏光晕。

    这三项都是"改了只能靠反复打开编辑器看效果"的显示类设置，因此各给一块小预览，
    直接读当前配置绘制；与 `SettingsPage.preview` 生成的标注预览一样支持 refresh()，
    设置变化后即时重绘。
    """

    def __init__(self, config, kind):
        super().__init__()
        self.config = config
        self.kind = kind
        self.setMinimumWidth(260)
        self.setFixedHeight(84)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def refresh(self):
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        area = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        data = self.config.data
        if self.kind == "transparent":
            self._paint_transparent(painter, area, data)
        elif self.kind == "editor_border":
            self._paint_border(painter, area, data)
        else:
            self._paint_toolbar_shadow(painter, area, data)
        painter.setBrush(Qt.NoBrush)
        # 预览外框属于窗口装饰（不是示意内容），跟随调色板：深色主题用中灰、浅色主题也不会
        # 出现"浅灰框几乎看不见"的固定色问题。
        painter.setPen(QPen(self.palette().color(QPalette.Mid), 1))
        painter.drawRect(area)
        painter.end()

    @staticmethod
    def _clamped(data, key, default, low, high):
        try:
            value = int((data or {}).get(key, default))
        except (TypeError, ValueError):
            return default
        return min(max(value, low), high)

    def _paint_transparent(self, painter, area, data):
        """透明像素预览底纹：与画布 `transparent_checker_brush` 同一套配色/边长逻辑。"""
        mode = str(data.get("editor_transparent_background", "theme"))
        tile = self._clamped(data, "editor_checker_tile_size", 8, 4, 32)
        dark_theme = self.palette().base().color().lightness() < 128
        if mode == "transparent":
            painter.fillRect(area, self.palette().base().color())
            # 纯透明模式没有棋盘，文字必须跟随主题前景色（固定 #6b767c 在深色底上对比不足）。
            painter.setPen(self.palette().color(QPalette.WindowText))
            painter.drawText(area, Qt.AlignCenter, "纯透明：不画棋盘，透明处与画布底色一致")
            return
        if mode == "dark_checker":
            first, second = "#252525", "#3b3b3b"
        elif mode == "light_checker":
            first, second = "#f0f0f0", "#c8c8c8"
        elif dark_theme:
            first, second = "#3b3f45", "#4c5158"
        else:
            first, second = "#e0e3e6", "#f1f3f5"
        # 说明文字压在棋盘上：颜色必须跟着**棋盘**走，而不是跟着窗口主题。
        # 反例（2026-10-17 黑暗模式巡检）：固定 #33413f 在深色棋盘上几乎看不见。
        # 走到这里只剩 theme / dark_checker / light_checker 三种（transparent 已提前返回）。
        if mode == "dark_checker" or (mode == "theme" and dark_theme):
            caption_color = QColor("#e8eaed")
        else:
            caption_color = QColor("#202020")
        pixmap = QPixmap(tile * 2, tile * 2)
        pixmap.fill(QColor(first))
        tile_painter = QPainter(pixmap)
        tile_painter.fillRect(0, 0, tile, tile, QColor(second))
        tile_painter.fillRect(tile, tile, tile, tile, QColor(second))
        tile_painter.end()
        painter.fillRect(area, QBrush(pixmap))
        # 叠一个半透明色块示意"这里本来有内容"。
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(0, 173, 145, 170))
        painter.drawEllipse(QRectF(area.center().x() - 26, area.center().y() - 18, 52, 36))
        painter.setPen(caption_color)
        painter.setBrush(Qt.NoBrush)
        painter.drawText(area.adjusted(6, 0, -6, -4), Qt.AlignBottom | Qt.AlignHCenter,
                         f"透明棋盘 {tile}px（只影响编辑显示，不写入图片）")

    def _paint_border(self, painter, area, data):
        """编辑区边框：宽度/颜色直接反映在预览外框上。"""
        width = self._clamped(data, "editor_border_width", 1, 1, 12)
        color = QColor(data.get("editor_border_color", "#000000"))
        painter.fillRect(area, QColor("#e8edf0"))
        inner = area.adjusted(10, 10, -10, -10)
        painter.fillRect(inner, QColor("#4b91a5"))
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(color, width))
        painter.drawRect(area.adjusted(width / 2, width / 2, -width / 2, -width / 2))
        painter.setPen(QColor("#eef4f6"))
        painter.drawText(inner, Qt.AlignCenter, f"编辑区边框 {width}px（不写入导出图片）")

    def _paint_toolbar_shadow(self, painter, area, data):
        """编辑工具栏光晕：开关/强度/颜色直接反映在小工具栏四周的阴影上。"""
        enabled = bool(data.get("editor_toolbar_shadow_enabled", True))
        strength = self._clamped(data, "editor_toolbar_shadow_strength", 60, 0, 100)
        color = QColor(data.get("editor_toolbar_shadow_color", "#000000"))
        painter.fillRect(area, QColor("#cfd8dd"))
        bar = QRectF(area.center().x() - 92, area.center().y() - 15, 184, 30)
        if enabled and strength:
            blur = max(2, round(strength * 0.4))
            offset = max(1, round(blur * 0.3))
            for expand in range(blur, 0, -max(1, blur // 3)):
                alpha = round(255 * strength / 100 * (1 - expand / (blur + 1)))
                shade = QColor(color)
                shade.setAlpha(max(0, alpha))
                painter.setPen(Qt.NoPen)
                painter.setBrush(shade)
                painter.drawRoundedRect(
                    bar.adjusted(-expand, -expand + offset, expand, expand + offset), 8, 8)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#f7f9fa"))
        painter.drawRoundedRect(bar, 8, 8)
        painter.setPen(QColor("#5b666c"))
        painter.drawText(bar, Qt.AlignCenter, "编辑工具栏")


class EditorPage(SettingsPage):
    """编辑器默认标注样式；字体列表从系统安装字体中读取。"""

    def __init__(self, config, changed):
        super().__init__(config, changed)
        self.color_buttons = {}
        self._preset_combos = {}
        self.group("默认工具")
        self.choice("annotation_tool", "默认标注工具",
                    [(label, key) for label, key in (
                        ("选择", "select"), ("画笔", "pen"), ("记号笔", "marker"),
                        ("箭头", "arrow"), ("矩形", "rect"), ("椭圆", "ellipse"),
                        ("序号", "number"), ("文字", "text"),
                        ("放大", "zoom"), ("马赛克", "mosaic"), ("橡皮擦", "eraser"),
                        ("裁剪", "crop"),
                        ("取色", "picker"))],
                    "新建编辑器默认启用的工具；只影响初始选择，之后可在工具栏随时切换。")
        self.group("箭头")
        self.preview("arrow")
        self._add_annotation_preset("arrow")
        self.add_color("arrow_color", "箭头颜色")
        self.choice("arrow_style", "箭头样式", [
                                ("箭头", "filled"), ("空心箭头", "open"), ("虚线箭头", "dashed"),
                                ("双向箭头", "double_filled"), ("双向空心箭头", "double_open"), ("双向虚线箭头", "double_dashed"),
                                ("箭杆矩形", "rect_filled"), ("空心箭杆", "rect_open"), ("虚线箭杆", "rect_dashed"),
                                ("曲线箭头", "curve_filled"), ("曲线空心箭头", "curve_open"),
                                ("曲线虚线箭头", "curve_open_dashed"),
                                ("线段", "line"), ("虚线", "dashed_line")],
                    "新建箭头使用的样式（单向/双向、实线/虚线、箭杆/曲线等）；只影响新建箭头。")
        self.radios("arrow_curve_direction", "曲线箭头弯曲方向",
                    [("向右弯", "right"), ("向左弯", "left")],
                    "只对曲线箭头生效；默认向右弯。选中曲线箭头时可在「更多设置」或按快捷键即时切换该对象的方向；快捷键默认 Alt+C，可在设置 > 快捷键修改。")
        self.check("arrow_chain", "箭头折线（多段直线）",
                   "开启后，绘制一个箭头后可继续在上一个终点与下一个点之间连续绘制相连的多段箭头；"
                   "按鼠标右键结束连续绘制并保留已画图形。")
        self.number("arrow_width", "箭头线宽", 1, 50,
                    "箭头标注的线条粗细；只影响箭头工具，不改画笔、矩形等其它工具。")
        self.group("画笔")
        self.preview("pen")
        self._add_annotation_preset("pen")
        self.add_color("pen_color", "画笔颜色")
        self.number("pen_width", "画笔线宽", 1, 50, "画笔标注的线条粗细；只影响画笔工具。")
        self.check("pen_chain", "画笔折线（多段直线）",
                   "开启后，画笔将在上一个终点与下一个点之间连续绘制相连的多段直线；"
                   "按鼠标右键结束连续绘制并保留已画图形。")
        self.group("矩形")
        self.preview("rect")
        self._add_annotation_preset("rect")
        self.add_color("rect_color", "矩形边框颜色")
        self.number("rect_width", "矩形线宽", 1, 50, "矩形标注的边框粗细；只影响矩形工具，不改填充。")
        self.radios("rect_style", "矩形线型", [("实线", "solid"), ("虚线", "dash")],
                    "新建矩形的边框线型（实线或虚线）；只影响新建矩形，不改填充。")
        self.check_number("rect_corner_enabled", "rect_corner_radius", "矩形标注圆角 (px)", 0, 100,
                          "勾选后新建矩形标注使用圆角；右侧数值为圆角半径，0 表示直角；"
                          "实线和虚线均适用，单张图片与窗口内编辑共用此设置")
        self.check("rect_fill_enabled", "矩形填充区域", "绘制矩形时填充区域")
        self.add_color("rect_fill_color", "矩形填充颜色")
        self.percent_slider("rect_fill_opacity", "矩形填充不透明度 (%)",
                    "0 为完全透明，100 为完全不透明")
        self.group("椭圆")
        self.preview("ellipse")
        self._add_annotation_preset("ellipse")
        self.add_color("ellipse_color", "椭圆边框颜色")
        self.number("ellipse_width", "椭圆线宽", 1, 50, "椭圆标注的边框粗细；只影响椭圆工具，不改填充。")
        self.radios("ellipse_style", "椭圆线型", [("实线", "solid"), ("虚线", "dash")],
                    "新建椭圆的边框线型（实线或虚线）；只影响新建椭圆，不改填充。")
        self.check("ellipse_fill_enabled", "椭圆填充区域", "绘制椭圆时填充区域")
        self.add_color("ellipse_fill_color", "椭圆填充颜色")
        self.percent_slider("ellipse_fill_opacity", "椭圆填充不透明度 (%)",
                    "0 为完全透明，100 为完全不透明")
        self.group("橡皮擦")
        self.preview("eraser")
        self._add_annotation_preset("eraser")
        self.number("eraser_width", "橡皮擦直径", 10, 100, "只影响橡皮擦工具，范围 10–100")
        self.add_color("eraser_cursor_color", "示意圈颜色")
        self.radios("editor_straight_modifier", "直线绘制修饰键", [
            ("Ctrl 或 Alt（任一）", "either"), ("仅 Ctrl", "ctrl"), ("仅 Alt", "alt"),
            ("关闭（全部按自由笔迹）", "off")],
            "画笔 / 记号笔 / 马赛克 / 橡皮擦**按住拖动**时，按住所选修饰键即切换为直线；选「关闭」则始终为自由笔迹")
        self.check("eraser_brush", "涂抹模式（自由笔刷）",
                   "默认：按住拖动沿笔迹擦除（按住 Ctrl 或 Alt 可直线擦除）；关闭后改为拖框选矩形，擦除框内内容")
        self.check("eraser_erase_base", "同时擦除原图",
                   "开启后新擦除会同时擦掉截图原图（只影响之后的擦除）；关闭则只擦标注、露出原图（非破坏性）。已有擦除可在该处右键单独设置")
        self.group("记号笔")
        self.preview("marker")
        self._add_annotation_preset("marker")
        self.add_color("marker_color", "记号笔颜色")
        self.number("marker_width", "记号笔线宽", 1, 50, "记号笔标注的线条粗细；只影响记号笔工具。")
        self.percent_slider("marker_opacity", "记号笔不透明度",
                    "记号笔颜色的不透明度；越低越能看清底图，记号笔仍使用标注颜色。",
                    minimum=1)
        self.check("marker_chain", "记号笔折线（多段直线）",
                   "开启后，记号笔将在上一个终点与下一个点之间连续绘制相连的多段直线；"
                   "按鼠标右键结束连续绘制并保留已画图形。")
        self.group("文字")
        self.preview("text", 120)
        self._add_annotation_preset("text")
        self.add_color("text_color", "文字颜色")
        self.number("font_size", "文字大小", 6, 200,
                    "编辑器文字标注的默认字号；之后可在工具栏临时调整，不改写此项。")
        self.number("text_width", "文字宽度", 0, 2000, "新建文字标注的文本框宽度（像素）；超出后自动换行，0 表示按内容自动")
        self.number("text_height", "文字高度", 0, 2000, "新建文字标注的文本框高度（像素）；超出部分裁剪，0 表示按内容自动")
        self.decimal("line_spacing", "文字行距", 0.5, 4,
                     "多行文字的行间距倍率；只影响文字排版，不改变字号。")
        self.radios("text_alignment", "文字对齐", [("左对齐", "left"), ("居中", "center"),
                                             ("右对齐", "right")],
                    "新建文字标注的对齐方式；只影响多行文字的排版。")
        fonts = QFontDatabase.families()
        self.choice("font", "默认字体", [("系统默认", "")] + [(font, font) for font in fonts], "从已安装字体中选择")
        self.check("text_bold", "粗体", "新建文字标注是否默认加粗；可在工具栏临时切换，不改写此项。")
        self.check("text_italic", "斜体", "新建文字标注是否默认斜体；可在工具栏临时切换，不改写此项。")
        self.check("text_underline", "下划线", "新建文字标注是否默认加下划线；可在工具栏临时切换。")
        self.check("text_strikethrough", "删除线", "新建文字标注是否默认加删除线；可在工具栏临时切换。")
        self.check("text_background_enabled", "文字背景",
                   "是否为新建文字标注添加背景色块；颜色由下方「文字背景色」决定。")
        self.add_color("text_background", "文字背景色")
        self.number("text_background_padding", "文字背景内边距 (px)", 0, 32,
                    "背景色块相对文字内容向外扩展的像素；0 表示与文字框完全贴合。")
        self.number("text_background_radius", "文字背景圆角 (px)", 0, 32,
                    "背景色块的圆角半径；0 表示直角。")
        self.percent_slider("text_background_opacity", "文字背景不透明度 (%)",
                    "背景色块的不透明度；100 表示完全不透明。")
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
        self.number("sequence_font_size", "序号字号", 6, 200,
                    "序号标注中数字的字号；不影响普通文字标注。")
        self.number("sequence_start", "序号起始值", 0, 999,
                    "第一个序号的号码，后续按绘制顺序自动递增；只影响新建序号。")
        self.radios("sequence_preset", "预设组合",
                    [("自定义", "custom"), ("红圆", "red_circle"), ("蓝方", "blue_square"),
                     ("绿星", "green_star"), ("琥珀菱形", "amber_diamond"),
                     ("紫五边形", "purple_pentagon"), ("青六边形", "teal_hexagon"),
                     ("红心", "red_heart"), ("蓝箭头", "blue_arrow"),
                     ("橙气泡", "orange_bubble"), ("绿云朵", "green_cloud")],
                    "一键套用形状与配色组合；选自定义后可逐项自由调整")
        self.group("局部放大")
        self.preview("zoom", 120)
        self.number("zoom_annotation_factor", "放大倍数", 1, 16,
                    "「放大」工具框选区域后生成副本的放大倍数；1 表示原尺寸，4 表示四倍。"
                    "只影响之后新建的局部放大标注。")
        self.radios("zoom_annotation_style", "镜片样式",
                    [("圆形", "circle"), ("矩形", "rect")],
                    "放大标注的镜片外轮廓；默认圆形。圆形会裁掉四角，矩形显示完整图像。")
        self.percent_slider("zoom_annotation_opacity", "放大标注不透明度 (%)",
                    "放大镜片与源区域标记的整体不透明度；100 表示完全不透明。")
        self.number("zoom_annotation_max_size", "最大边长 (px)", 80, 1200,
                    "放大标注镜片的最长边上限；80–1200 像素，默认 480。\n"
                    "框选大区域或倍率过高时按比例缩小，避免无限膨胀；不影响源图。")
        self.color_check_number("zoom_annotation_color", "zoom_annotation_border_enabled",
                                "zoom_annotation_border_width", "放大镜线条", "绘制",
                                1, 12, " px",
                                "放大镜镜片线条颜色、是否绘制和粗细；默认不绘制线条。",
                                default_on=False)
        self.check_number("zoom_annotation_rect_rounded", "zoom_annotation_rect_radius",
                         "矩形圆角", 0, 60,
                         "矩形镜片是否使用圆角以及圆角半径；关闭后为直角，圆形镜片不受影响。",
                         " px")
        self.color_check_number("zoom_annotation_glow_color", "zoom_annotation_glow_enabled",
                                "zoom_annotation_glow_width", "放大镜光晕", "绘制",
                                0, 30, " px",
                                "放大镜外圈光晕颜色、是否绘制和宽度；关闭开关后不绘制光晕。")
        self.percent_slider("zoom_annotation_glow_opacity", "光晕不透明度 (%)",
                    "镜片光晕的透明度；0 表示不可见，100 表示最明显。")
        self.add_color("zoom_annotation_source_color", "源区域框线颜色")
        self.radios("zoom_annotation_source_marker", "源区域标记",
                    [("虚线", "dash"), ("实线", "solid"), ("关闭", "off")],
                    "放大标注源区域轮廓的线型；关闭后只保留镜片。")
        self.check("zoom_annotation_source_corners", "矩形源区域角标",
                   "矩形直角模式下是否绘制四角短实线；圆角模式自动不绘制。")
        self.group("马赛克")
        self.preview("mosaic")
        self._add_annotation_preset("mosaic")
        self.radios("mosaic_mode", "马赛克类型", [("方块", "blocks"), ("毛玻璃", "blur"),
                             ("细粒", "fine")],
                    "马赛克工具的处理方式：方块像素化、毛玻璃模糊或细粒；只影响马赛克工具。")
        self.number("mosaic_size", "马赛克方块", 2, 100,
                    "马赛克块的像素粒度，数值越大色块越粗；只影响马赛克工具。")
        self.check("mosaic_brush", "涂抹模式（自由笔刷）",
                   "开启后按住拖动可沿笔迹涂抹马赛克/模糊；关闭则为拖框选矩形")
        self.number("mosaic_width", "涂抹笔刷宽度 (px)", 4, 100,
                    "涂抹模式下的笔刷直径，数值越大单次涂抹覆盖越宽")
        self.add_color("mosaic_cursor_color", "示意圈颜色")
        self.group("编辑区边框")
        self._effect_preview("editor_border")
        self.number("editor_border_width", "编辑区边框粗细", 1, 12,
                    "只在编辑画布中显示，不写入图片")
        self.add_color("editor_border_color", "编辑区边框颜色")
        # 透明像素的预览底色：只影响编辑画布显示，导出仍按真实 alpha 写出，所以单独成组。
        self.group("透明背景")
        self._effect_preview("transparent")
        self.radios("editor_transparent_background", "透明区域显示",
                    [("跟随主题棋盘", "theme"), ("暗色棋盘", "dark_checker"),
                     ("亮色棋盘", "light_checker"), ("纯透明", "transparent")],
                    "编辑画布中透明像素（多显示器间隙、擦除镂空、圆角外）的预览底色；"
                    "只影响编辑时的显示，不写入导出图片")
        self.group("编辑工具栏")
        self._effect_preview("toolbar_shadow")
        self.check("editor_toolbar_shadow_enabled", "工具栏阴影光晕",
                   "给编辑工具栏加一圈阴影光晕，编辑大图且整片接近底色（如都是浅色）时也能一眼找到工具栏")
        self.color("editor_toolbar_shadow_color", "阴影颜色", "工具栏阴影/光晕颜色；建议选与画面成对比的深色或浅色")
        self.number("editor_toolbar_shadow_strength", "阴影强度", 0, 100,
                    "0 表示不绘制；数值越大越明显，模糊范围也越大")
        self.group("画布与标注交互")
        self.number("editor_zoom_wheel_step", "滚轮缩放步进 (%)", 1, 50,
                    "按住 Ctrl 滚动滚轮时每一格的缩放百分比，范围 1–50。\n调大：缩放更快、几格就能到目标倍率，但容易一步跳过想要的尺寸。\n调小：更细腻，但要滚更多次；1% 接近逐像素微调。\n不影响工具栏上的放大/缩小按钮与“适应窗口”，只影响滚轮步长。")
        self.radios("editor_rotation_snap", "旋转吸附", [(f"{value}°", value) for value in (0, 5, 15, 45)],
                    "旋转标注时按住 Shift 吸附到多少度的整数倍。\n5°/15°：适合把标注摆正或做小幅微调；45°：适合快速转成对角。\n0°：完全不吸附，可停在任意角度（适合对齐非直角），代价是想转 90°/180° 只能靠目测。\n不按 Shift 时始终自由旋转，此设置不生效。")
        self.radios("editor_rotation_handle", "旋转按钮位置",
                    [("标注中心", "center"), ("顶部外侧", "top")],
                    "单选标注时旋转按钮画在哪里。\n标注中心：按钮随图形移动/旋转、永远在图形内，缩放或旋转后一定点得到；代价是它会盖住标注中心的内容（细窄的箭头、线段尤其明显）。\n顶部外侧：不挡内容、更接近常见习惯；代价是标注贴近画布上缘、或放大后顶边跑到可视区外时，按钮会看不见也点不到。\n只影响单选；多选时按包围盒中心旋转。")
        self.radios("editor_overcanvas_mode", "标注超出画布",
                    [("直接裁切", "clip"), ("禁止移出", "block"), ("允许超出", "allow")],
                    "把标注画/拖到画布之外时的处理方式。三种模式都遵守同一条："
                    "画布外的部分在编辑视图与导出里都看不见（编辑视图用底色盖住画布外区域，与导出结果一致）。"
                    "区别在于落点会不会被夹住：\n"
                    "直接裁切（默认）：用工具绘制时落点被夹在画布边缘（画不出去）；已有标注可以被拖/转/推出去，"
                    "但出去后就被底色盖住看不见（几何位置仍保留，可用撤销或方向键拉回）。\n"
                    "禁止移出：绘制同样画不出去，且已有标注一旦被移出边界就立刻平移回画布内（只平移、不缩放；"
                    "拖动、旋转、缩放手柄、方向键微调都会拉回）—— 想紧贴边缘摆放时会觉得「推不出去」。\n"
                    "允许超出：绘制与移动都不夹取落点（指针在哪就画到哪、标注能推到哪就停在哪），"
                    "出界部分同样被盖住/裁掉，但几何位置按真实坐标保留。\n"
                    "只影响本次编辑的标注，不改变已保存的图片。")
        self.number("editor_checker_tile_size", "棋盘格边长 (px)", 4, 32,
                    "透明像素预览棋盘的方格边长，范围 4–32 像素。\n调小：格子密、更接近“这里真的透明”的观感，但可能显得花、有摩尔纹。\n调大：更清楚，但会盖住较多细节，小面积透明处反而看不清。\n只影响显示，不会写进导出的图片。")
        self.number("editor_text_click_delay", "文字单击等待 (ms)", 60, 400,
                    "文字工具单击后等待多久才弹出输入框，用来与“双击保存”区分，范围 60–400ms。\n调大：更不容易把想双击保存的动作误判成两次单击，但每次加文字都要多等一会儿。\n调小：更跟手，但手速偏慢时可能误弹输入框，挡住刚画的标注。\n实际等待不会超过系统双击间隔；系统双击间隔很短时此值会被压缩。")
        self.group("裁剪框")
        self.preview("crop", 84)
        self._add_annotation_preset("crop")
        self.add_color("crop_color", "裁剪框颜色")
        self.number("crop_width", "裁剪框线宽", 1, 12,
                    "编辑器裁剪框的线条宽度；仅编辑时显示，不写入导出图片。")
        self.group("取色")
        self.preview("picker", 120)
        # 序号预设组合：选择后立即套用形状与配色到对应键（自定义不覆盖）。
        preset_control = self.controls.get("sequence_preset")
        if preset_control is not None:
            preset_control.changed.connect(self._apply_sequence_preset)
        self._reorder_editor_groups()

    def _reorder_editor_groups(self):
        """设置页标注分组与编辑工具栏工具顺序一致；不受代码构建顺序影响。"""
        order = [
            "默认工具", "画笔", "记号笔", "箭头", "矩形", "椭圆",
            "序号标注", "文字", "局部放大", "马赛克", "橡皮擦",
            "裁剪框", "取色", "编辑区边框",
            "透明背景", "编辑工具栏", "画布与标注交互",
        ]
        boxes = {}
        for index in range(self.groups.count()):
            widget = self.groups.itemAt(index).widget()
            if isinstance(widget, QGroupBox):
                boxes[widget.title()] = widget
        for box in boxes.values():
            self.groups.removeWidget(box)
        for index, title in enumerate(order):
            box = boxes.get(title)
            if box is not None:
                self.groups.insertWidget(index, box)
        # 未来新增分组若忘记写进 order，也必须保留在布局里；否则会被 removeWidget
        # 后悬空在 (0,0)，表现为与“默认工具”等分组重叠。
        known = set(order)
        next_index = len([title for title in order if title in boxes])
        for title, box in boxes.items():
            if title not in known:
                self.groups.insertWidget(next_index, box)
                next_index += 1

    def _add_annotation_preset(self, tool):
        """在工具预览下方插入「样式预设」下拉（与编辑器工具栏同一份预设数据）。"""
        combo = QComboBox()
        combo.addItem("自定义", "custom")
        for preset_id, label, _values in presets_for(tool):
            combo.addItem(label, preset_id)
        matched = matching_preset(tool, self.config.data)
        index = combo.findData(matched)
        combo.setCurrentIndex(index if index >= 0 else 0)
        combo.setToolTip("一键套用该工具的样式组合；套用后仍可在本页或编辑器工具栏逐项微调")
        combo.currentIndexChanged.connect(
            lambda _index, target=tool, control=combo:
            self._apply_annotation_preset(target, control))
        self._preset_combos[tool] = combo
        self._add_row("样式预设", combo)
        return combo

    def _apply_annotation_preset(self, tool, combo):
        """设置页套用预设：写入该工具的现有配置键并刷新本页控件/预览。"""
        preset_id = combo.currentData()
        values = preset_values(tool, preset_id) if preset_id else {}
        if not values:
            return
        self.config.data.update(values)
        self.changed()
        self.sync_controls()
        self.refresh_previews()
        get_logger().debug("设置页套用标注样式预设: 工具=%s 预设=%s 项数=%d",
                           tool, preset_id, len(values))

    def _sync_preset_choices(self):
        """按当前配置把各工具的预设下拉反选到匹配项；被改坏则回到「自定义」。"""
        for tool, combo in getattr(self, "_preset_combos", {}).items():
            matched = matching_preset(tool, self.config.data)
            with QSignalBlocker(combo):
                index = combo.findData(matched)
                combo.setCurrentIndex(index if index >= 0 else 0)

    def update_value(self, key, value):
        """控件改动写回配置后，同步各工具预设下拉的当前匹配项。"""
        super().update_value(key, value)
        self._sync_preset_choices()

    def sync_controls(self):
        super().sync_controls()
        self._sync_preset_choices()

    def _apply_sequence_preset(self, preset_key):
        from editor.annotation_items import SEQUENCE_PRESETS
        preset = SEQUENCE_PRESETS.get(preset_key)
        if not preset:
            return
        for sub_key, value in preset.items():
            self.update_value(sub_key, value)


    def color_check(self, color_key, check_key, label, check_label, help_text):
        """颜色按钮与开关放在同一行，避免两个独立设置各占一行。"""
        button = ColorButton(self.config.data[color_key],
                             lambda color: self.update_value(color_key, color))
        box = QCheckBox(check_label)
        box.setChecked(bool(self.config.data[check_key]))
        button.setToolTip(help_text)
        box.setToolTip(help_text + "\n取值：开 / 关；默认开启。")
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        row.addWidget(button)
        row.addWidget(box)
        row.addStretch()
        container = QWidget()
        container.setLayout(row)
        container.setToolTip(help_text)
        button._setting_label = label
        box._setting_label = label
        self.controls[color_key] = button
        self.controls[check_key] = box
        self.color_buttons[color_key] = button
        self.form.addRow(label, container)
        box.toggled.connect(lambda value: self.update_value(check_key, value))
        return container


    def color_check_number(self, color_key, check_key, number_key, label,
                           check_label, low, high, suffix, help_text, default_on=True):
        """颜色、绘制开关和数值同一行：线条/光晕都按这个紧凑布局。"""
        button = ColorButton(self.config.data[color_key],
                             lambda color: self.update_value(color_key, color))
        box = QCheckBox(check_label)
        box.setChecked(bool(self.config.data[check_key]))
        spin = QSpinBox()
        spin.setRange(low, high)
        spin.setValue(self.config.data[number_key])
        if suffix:
            spin.setSuffix(suffix)
        button.setToolTip(help_text)
        box.setToolTip(help_text + "\n取值：开 / 关；默认" + ("开启" if default_on else "关闭") + "。")
        spin.setToolTip(self._with_value_help(number_key, help_text, spin))
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        # 颜色 + 开关 + 数值放在同一行；4px 间距保证 780px 设置窗口内不出现横向滚动。
        row.setSpacing(4)
        row.addWidget(button)
        row.addWidget(box)
        row.addWidget(spin)
        row.addStretch()
        container = QWidget()
        container.setLayout(row)
        container.setToolTip(spin.toolTip())
        button._setting_label = label
        box._setting_label = label
        spin._setting_label = label
        self.controls[color_key] = button
        self.controls[check_key] = box
        self.controls[number_key] = spin
        self.color_buttons[color_key] = button
        self.form.addRow(label, container)
        box.toggled.connect(lambda value: self.update_value(check_key, value))
        spin.valueChanged.connect(lambda value: self.update_value(number_key, value))
        return container

    def _effect_preview(self, kind):
        """插入编辑器页专用的显示类预览（透明棋盘 / 编辑区边框 / 工具栏光晕）。"""
        preview = EditorEffectPreview(self.config, kind)
        self.previews.append(preview)
        self.form.addRow(preview)
        return preview

    def add_color(self, key, label):
        help_text = {
            "pen_color": "画笔描边颜色，用于画笔工具绘制的线条。",
            "rect_color": "矩形边框颜色（填充色见下一项，两者各自独立）。",
            "ellipse_color": "椭圆边框颜色（填充色见下一项，两者各自独立）。",
            "arrow_color": "箭头线条与箭头头部的颜色。",
            "marker_color": "记号笔颜色；与记号笔不透明度共同决定覆盖效果。",
            "text_color": "新建文字标注的默认文字颜色。",
            "sequence_fill_color": "新建序号标注标记的底色（形状填充颜色）。",
            "sequence_text_color": "新建序号标注中数字的文字颜色。",
            "editor_border_color": "编辑画布周围的边框颜色；只显示在编辑区，不会写入导出图片。",
            "crop_color": "编辑器裁剪框的线条颜色；只用于裁剪辅助显示。",
            "text_background": "新建文字标注背景色块的颜色；仅当“文字背景”开启时生效。",
            "zoom_annotation_color": "放大镜镜片边框颜色；关闭开关后不绘制线条，与光晕颜色独立。",
            "zoom_annotation_glow_color": "放大镜镜片外圈光晕的独立颜色；默认使用贴图蓝色 #168cff。",
            "zoom_annotation_source_color": "放大标注源区域虚线框/角标的颜色；与镜片线条和光晕颜色独立。",
            "rect_fill_color": "矩形标注的填充颜色（与边框颜色各自独立，默认值相同）。",
            "ellipse_fill_color": "椭圆标注的填充颜色；与边框颜色各自独立，默认值相同。",
            "mosaic_cursor_color": "编辑画布中马赛克涂抹范围示意圆圈的颜色。",
            "eraser_cursor_color": "编辑画布中橡皮擦直径示意圆圈的颜色。",
        }[key]
        button = ColorButton(self.config.data[key],
                             lambda color: self.update_value(key, color))
        button.setToolTip(help_text)
        # 记录界面标签：配置变更日志用它把键名翻译成用户看得懂的功能说明。
        button._setting_label = label
        self.form.addRow(label, button)
        self.color_buttons[key] = button