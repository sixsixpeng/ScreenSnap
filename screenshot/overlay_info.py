"""只在遮罩绘制的提示，不污染原图。"""

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QFont, QPen


def paint_info(painter, position, selection, area=None, quick_sticker=False,
               save_shortcut="S", element_rect=None, element_size=None,
               element_border_color="#00d7aa", element_text_color="#ffffff",
               element_background_color="#141c22", element_font_size=12,
               element_border_width=1):
    """显示带底色的坐标和操作提示，避免文字融入明亮截图背景。"""
    size = f"  {selection.width()} x {selection.height()}" if selection else ""
    area = area or QRect(0, 0, painter.device().width(), painter.device().height())
    instructions = (
         "内部拖动移动 | 四角/边中点缩放 | Enter/左键双击编辑 | WASD/方向键微调 | Esc取消"
        if selection else
        "拖拽框选 | Enter/左键双击编辑 | WASD/方向键微调 | Esc取消 | Ctrl+F固定尺寸"
    )
    quick_shortcut = (quick_sticker if isinstance(quick_sticker, str) else "Space")
    quick_hint = f" | 按 {quick_shortcut} 快速贴图" if quick_sticker else ""
    save_hint = f" | 右键双击保存 | {save_shortcut}快速保存"
    text = f"{position.x()}, {position.y()}{size}  |  {instructions}{save_hint}{quick_hint}"
    metrics = painter.fontMetrics()
    max_width = min(1100, max(1, area.width() - 16))
    line = metrics.elidedText(text, Qt.ElideRight, max(1, max_width - 20))
    width = max_width
    height = metrics.height() + 12
    left = area.left() + max(0, (area.width() - width) // 2)
    top = area.top() + 8
    painter.save()
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(20, 28, 34, 220))
    painter.drawRoundedRect(QRect(left, top, width, height), 5, 5)
    painter.setPen(QColor("white"))
    painter.drawText(QRect(left + 10, top + 6, width - 20, metrics.height()),
                     Qt.AlignHCenter | Qt.AlignVCenter, line)
    if element_rect is not None and not element_rect.isEmpty():
        font = QFont()
        font.setPixelSize(max(1, int(element_font_size)))
        painter.setFont(font)
        badge_metrics = painter.fontMetrics()
        size = element_size or (element_rect.width(), element_rect.height())
        label = f"{size[0]} x {size[1]} px"
        label_width = badge_metrics.horizontalAdvance(label) + 16
        label_height = badge_metrics.height() + 8
        label_left = max(area.left() + 4, min(
            element_rect.center().x() - label_width // 2,
            area.right() - label_width - 3))
        label_top = element_rect.top() - label_height - 6
        if label_top < area.top() + 4:
            label_top = element_rect.bottom() + 7
        if label_top + label_height > area.bottom() - 3:
            label_top = max(area.top() + 4, element_rect.top() - label_height - 6)
        badge = QRect(label_left, label_top, label_width, label_height)
        painter.setPen(QPen(QColor(element_border_color), max(1, element_border_width)))
        painter.setBrush(QColor(element_background_color))
        painter.drawRoundedRect(badge, 4, 4)
        painter.setPen(QColor(element_text_color))
        painter.drawText(badge, Qt.AlignCenter, label)
    painter.restore()