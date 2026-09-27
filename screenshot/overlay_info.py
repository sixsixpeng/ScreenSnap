"""只在遮罩绘制的提示，不污染原图。"""

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor


def paint_info(painter, position, selection, area=None):
    """显示带底色的坐标和操作提示，避免文字融入明亮截图背景。"""
    size = f"  {selection.width()} x {selection.height()}" if selection else ""
    area = area or QRect(0, 0, painter.device().width(), painter.device().height())
    instructions = (
        "内部拖动移动 | 四角缩放 | 边中点单向缩放 | WASD/方向键微调 | Enter完成 | Esc取消"
        if selection else
        "拖拽框选 | Enter完成 | 双击完成 | WASD/方向键微调 | Esc取消 | Ctrl+F固定尺寸"
    )
    text = f"{position.x()}, {position.y()}{size}  |  {instructions}"
    metrics = painter.fontMetrics()
    max_width = min(860, max(1, area.width() - 16))
    width = min(max_width, metrics.horizontalAdvance(text) + 20)
    text = metrics.elidedText(text, Qt.ElideRight, max(1, width - 20))
    height = metrics.height() + 12
    left = area.left() + max(0, (area.width() - width) // 2)
    top = area.top() + 8
    painter.save()
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(20, 28, 34, 220))
    painter.drawRoundedRect(QRect(left, top, width, height), 5, 5)
    painter.setPen(QColor("white"))
    painter.drawText(QRect(left + 10, top + 2, width - 20, height - 4),
                     Qt.AlignLeft | Qt.AlignVCenter, text)
    painter.restore()