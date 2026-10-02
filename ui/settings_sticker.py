"""贴图外观设置页。"""

from ui.widgets.tooltip import SettingsPage
from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget
from core.constants import CHECKER_TILE_SIZE


class BackgroundModePreview(QWidget):
    """小型透明图示例，随默认背景模式切换实时重绘。"""

    def __init__(self, config, mode, parent=None):
        super().__init__(parent)
        self.config = config
        self.mode = mode
        self.setMinimumHeight(90)

    def set_mode(self, mode):
        self.mode = mode
        self.update()

    def refresh(self):
        self.set_mode(self.config.data.get("sticker_background_mode", "transparent"))

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        area = self.rect().adjusted(1, 1, -1, -1)
        image_area = area.adjusted(24, 10, -24, -12)
        checker = self.mode in ("transparent", "pseudo", "dark_checker", "light_checker")
        if checker:
            colors = (("#242424", "#3b3b3b") if self.mode == "dark_checker"
                      else ("#f4f4f4", "#cccccc"))
            tile = CHECKER_TILE_SIZE
            if self.mode not in ("dark_checker", "light_checker"):
                colors = ("#f4f4f4", "#bfc7ca")
            painter.fillRect(area, QColor("#202020"))
            for row, top in enumerate(range(image_area.top(), image_area.bottom() + 1, tile)):
                for column, left in enumerate(range(image_area.left(), image_area.right() + 1, tile)):
                    painter.fillRect(left, top, tile, tile,
                                     QColor(colors[(row + column) % 2]))
        else:
            painter.fillRect(area, QColor("#202020"))

        content = QPainterPath()
        content.moveTo(image_area.left() + image_area.width() * 0.08, image_area.center().y())
        content.cubicTo(image_area.left() + image_area.width() * 0.08, image_area.top(),
                        image_area.left() + image_area.width() * 0.42, image_area.top(),
                        image_area.left() + image_area.width() * 0.52, image_area.top() + image_area.height() * 0.28)
        content.cubicTo(image_area.right(), image_area.top() + image_area.height() * 0.12,
                        image_area.right(), image_area.bottom(),
                        image_area.left() + image_area.width() * 0.56, image_area.bottom() - image_area.height() * 0.12)
        content.cubicTo(image_area.left() + image_area.width() * 0.35, image_area.bottom(),
                        image_area.left(), image_area.bottom() - image_area.height() * 0.2,
                        image_area.left() + image_area.width() * 0.08, image_area.center().y())
        content.closeSubpath()
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(0, 173, 145, 220))
        painter.drawPath(content)
        if self.mode == "pseudo":
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(QColor("#ffffff"), 1, Qt.DashLine))
            painter.drawRect(image_area.adjusted(1, 1, -1, -1))
        painter.setPen(QColor("#ffffff"))
        caption = {"transparent": "透明 · 像素外不可点击",
                   "pseudo": "伪透明 · 整张贴图可拖动",
                   "dark_checker": "暗色棋盘 · 整张贴图可拖动",
                   "light_checker": "亮色棋盘 · 整张贴图可拖动"}.get(self.mode, "")
        painter.drawText(area.adjusted(8, 0, -8, -3), Qt.AlignBottom | Qt.AlignHCenter, caption)


class StickerSelectionPreview(QWidget):
    """实时预览贴图默认阴影、描边与选中光晕。"""

    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.config = config
        self.setFixedHeight(92)
        self.setMinimumWidth(260)

    def refresh(self):
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        area = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        painter.fillRect(area, QColor("#edf2f4"))
        sticker = QRectF(area.center().x() - 54, area.center().y() - 23, 108, 46)
        if self.config.data.get("sticker_shadow_enabled", True):
            shadow_strength = self.config.data.get("sticker_shadow_strength", 35)
            base = QColor(self.config.data.get("sticker_shadow_color", "#000000"))
            blur = max(2, round(shadow_strength * 0.4))
            offset = max(1, round(blur * 0.3))
            painter.setPen(Qt.NoPen)
            # 多层叠加近似柔和阴影，绕贴图一圈而非只在右下露出一条。
            for expand in range(blur, 0, -max(1, blur // 3)):
                alpha = round(255 * shadow_strength / 100 * (1 - expand / (blur + 1)))
                color = QColor(base)
                color.setAlpha(max(0, alpha))
                painter.setBrush(color)
                painter.drawRoundedRect(
                    sticker.adjusted(-expand, -expand + offset, expand, expand + offset),
                    6, 6)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#ffffff"))
        painter.drawRoundedRect(sticker, 4, 4)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#dce5e9"))
        painter.drawRoundedRect(sticker.adjusted(10, 9, -10, -9), 2, 2)
        if (self.config.data.get("sticker_border_enabled", True) and
                self.config.data.get("sticker_border_width", 2)):
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(QColor(self.config.data.get("sticker_border_color", "#ff0000")),
                                self.config.data.get("sticker_border_width", 2)))
            painter.drawRoundedRect(sticker, 4, 4)
        strength = self.config.data.get("sticker_selection_effect_strength", 30)
        if (self.config.data.get("sticker_selection_effect_enabled", True) and strength):
            for color, width, inset in ((QColor(40, 139, 255, 42), 8, 5),
                        (QColor(40, 139, 255, 96), 5, 3),
                        (QColor(72, 165, 255, 230), 2, 2)):
                    color.setAlpha(round(color.alpha() * strength / 100))
                    painter.setPen(QPen(color, width, Qt.SolidLine, Qt.RoundCap,
                                        Qt.RoundJoin))
                    painter.setBrush(Qt.NoBrush)
                    painter.drawRoundedRect(
                        sticker.adjusted(inset, inset, -inset, -inset), 6, 6)
        painter.end()


class StickerPage(SettingsPage):
    """配置新贴图默认描边与阴影效果，以及贴图管理窗口的显示方式。"""

    def __init__(self, config, changed):
        super().__init__(config, changed)
        self.group("透明图像背景")
        mode = self.choice("sticker_background_mode", "默认背景模式",
                   [("透明", "transparent"), ("伪透明", "pseudo"),
                    ("暗色棋盘", "dark_checker"), ("亮色棋盘", "light_checker")],
                   "仅影响含透明通道的贴图；每张贴图也可在右键菜单中单独调整")
        self.background_preview = BackgroundModePreview(config, config.data["sticker_background_mode"])
        self.form.addRow(self.background_preview)
        self.previews.append(self.background_preview)
        mode.currentIndexChanged.connect(lambda: self.background_preview.set_mode(mode.currentData()))
        self.group("贴图外观")
        self.check("sticker_selection_effect_enabled", "选中时显示光晕",
               "控制贴图获得焦点或进入多选状态时显示的蓝色选中光晕")
        self.number("sticker_selection_effect_strength", "光晕强度 (%)", 0, 100,
                    "调整贴图选中光晕的浓度；0 为不显示，默认 30%")
        self.selection_effect_preview = StickerSelectionPreview(config)
        self.previews.append(self.selection_effect_preview)
        self.form.addRow(self.selection_effect_preview)
        self.check("sticker_border_enabled", "默认开启描边", "新建贴图默认显示外圈描边；单个贴图可在右键菜单单独切换")
        self.color("sticker_border_color", "描边颜色", "设置新建贴图默认描边颜色")
        self.number("sticker_border_width", "描边宽度", 0, 20, "设置新建贴图默认描边宽度，0 表示不绘制描边")
        self.check("sticker_shadow_enabled", "默认显示阴影", "新建贴图默认显示阴影；单个贴图可在右键菜单单独切换")
        self.color("sticker_shadow_color", "阴影颜色", "设置新建贴图默认阴影颜色")
        self.number("sticker_shadow_strength", "阴影强度", 0, 100, "设置新建贴图默认阴影强度，0 表示不绘制阴影")
        self.group("拖动与吸附")
        self.check("sticker_snap_enabled", "拖动时自动吸附",
                   "拖动贴图靠近屏幕工作区、普通窗口或其他贴图边缘时自动对齐；\n"
                   "按住 Alt 拖动可临时取消吸附，用于精细摆放")
        self.number("sticker_snap_threshold", "吸附触发距离", 1, 40,
                    "贴图边缘距离目标多近时才吸附，单位是逻辑像素（与系统缩放无关）；\n"
                    "数值越大越容易吸附，过大时贴图会“黏手”，建议 4 到 12")
        self.choice("sticker_snap_targets", "吸附目标",
                    [("屏幕与工作区边缘", "screen"), ("窗口和贴图边缘", "window"),
                     ("两者都吸附", "both")],
                    "屏幕：贴到显示器可用区域的四边，任务栏占位会被排除；\n"
                    "窗口和贴图：贴到普通窗口或其他可见贴图的四边（可贴到目标外侧）；\n"
                    "两者：同时考虑上面两类目标，取距离最近的一个")
        self.check("sticker_follow_window", "吸附到窗口后跟随移动",
                   "贴到某个窗口后，该窗口移动或改变位置时贴图跟着一起走；\n"
                   "窗口关闭会自动解除吸附；也可在贴图右键菜单单独开关跟随")
        self.check("sticker_follow_sticker", "吸附到贴图后跟随移动",
               "贴图吸附到另一张贴图后，目标贴图移动时本贴图保持相对位置跟随；\n"
               "目标贴图关闭后自动解除跟随")
        self.number("sticker_follow_interval", "跟随刷新间隔", 30, 1000,
                    "跟随开启后每隔多少毫秒读取一次目标窗口位置，单位毫秒；\n"
                    "数值越小跟得越紧但占用略多 CPU，100 到 200 毫秒通常已经跟手")
        self.group("贴图管理窗口")
        self.number("sticker_panel_thumb", "缩略图宽度", 48, 200,
                    "管理窗口中每行缩略图的宽度，高度按 4:3 推导；重新打开窗口后生效")
        self.group("回收站")
        self.check("sticker_recycle_enabled", "关闭贴图进入回收站",
                   "关闭的贴图先进入回收站（右键菜单可恢复），避免误删；关闭此选项则直接删除")
        self.number("sticker_recycle_limit", "回收站上限", 1, 200,
                    "回收站最多保留的贴图数量，超出时丢弃最早进入的项")

