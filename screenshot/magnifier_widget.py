"""绘制光标附近像素的放大预览。"""

from PySide6.QtCore import QRect
from PySide6.QtGui import QColor


def magnifier_rect(point, bounds):
    width = min(140, max(1, bounds.width() - 20))
    height = min(140, max(1, bounds.height() - 20))
    x = point.x() + 32
    y = point.y() + 32
    if x + width + 4 > bounds.width():
        x = point.x() - width - 32
    if y + height + 4 > bounds.height():
        y = point.y() - height - 32
    return QRect(
        min(max(x, 2), max(2, bounds.width() - width - 2)),
        min(max(y, 2), max(2, bounds.height() - height - 2)),
        width, height,
    ).adjusted(-2, -2, 2, 2)


def paint_magnifier(painter, image, point, bounds, source_point=None):
    """把光标周围的小块原图放大绘制到遮罩上的悬浮区域。"""
    source_point = source_point or point
    sample_width = min(20, image.width())
    sample_height = min(20, image.height())
    sample = QRect(
        min(max(0, source_point.x() - sample_width // 2), image.width() - sample_width),
        min(max(0, source_point.y() - sample_height // 2), image.height() - sample_height),
        sample_width, sample_height,
    )
    frame = magnifier_rect(point, bounds)
    target = frame.adjusted(2, 2, -2, -2)
    painter.fillRect(frame, QColor("#ffffff"))
    painter.drawImage(target, image, sample)
    painter.setPen(QColor("#ff5252"))
    painter.drawLine(target.center().x(), target.top(), target.center().x(), target.bottom())
    painter.drawLine(target.left(), target.center().y(), target.right(), target.center().y())