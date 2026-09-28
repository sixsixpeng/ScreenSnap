"""只在遮罩绘制的提示，不污染原图。"""

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor


def paint_info(painter, position, selection, area=None):
    """显示带底色的坐标和操作提示，避免文字融入明亮截图背景。"""
    size = f"  {selection.width()} x {selection.height()}" if selection else ""
    area = area or QRect(0, 0, painter.device().width(), painter.device().height())
    instructions = (
         "内部拖动移动 | 四角/边中点缩放 | Enter/双击快速进入编辑 | WASD/方向键微调 | Esc取消"
        if selection else
        "拖拽框选 | Enter/双击快速进入编辑 | WASD/方向键微调 | Esc取消 | Ctrl+F固定尺寸"
    )
    text = f"{position.x()}, {position.y()}{size}  |  {instructions}"
    metrics = painter.fontMetrics()
    max_width = min(730, max(1, area.width() - 16))
    if area.height() < 160:
        lines = [metrics.elidedText(text, Qt.ElideRight, max(1, max_width - 20))]
    else:
        lines = []
        for part in text.split(" | "):
            candidate = f"{lines[-1]} | {part}" if lines else part
            if lines and metrics.horizontalAdvance(candidate) > max_width - 20:
                lines.append(part)
            elif lines:
                lines[-1] = candidate
            else:
                lines.append(part)
    width = max_width
    height = metrics.height() * len(lines) + 12
    left = area.left() + max(0, (area.width() - width) // 2)
    top = area.top() + 8
    painter.save()
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(20, 28, 34, 220))
    painter.drawRoundedRect(QRect(left, top, width, height), 5, 5)
    painter.setPen(QColor("white"))
    for index, line in enumerate(lines):
        painter.drawText(QRect(left + 10, top + 6 + index * metrics.height(), width - 20,
                               metrics.height()), Qt.AlignHCenter | Qt.AlignVCenter, line)
    painter.restore()