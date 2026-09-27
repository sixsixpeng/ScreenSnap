"""绘制光标附近像素的放大预览。"""

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor


def paint_magnifier(painter, image, point, bounds):
    """把光标周围的小块原图放大绘制到遮罩上的悬浮区域。"""
    sample = QRect(max(0, point.x() - 10), max(0, point.y() - 10), 20, 20)
    target = QRect(min(point.x() + 20, bounds.width() - 126),
                   min(point.y() + 20, bounds.height() - 126), 120, 120)
    painter.fillRect(target.adjusted(-2, -2, 2, 2), QColor("#ffffff"))
    painter.drawImage(target, image, sample)
    painter.setPen(QColor("#ff5252"))
    painter.drawLine(target.center().x(), target.top(), target.center().x(), target.bottom())
    painter.drawLine(target.left(), target.center().y(), target.right(), target.center().y())