"""绘制光标附近像素的放大预览。"""

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QPen


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


def paint_magnifier(painter, image, point, bounds, source_point=None, grid=False,
                    grid_color=None):
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
    if grid and sample.width() and sample.height():
        painter.save()
        painter.setPen(QPen(QColor(grid_color or "#cccccc"), 1, Qt.DotLine))
        cell_w = target.width() / sample.width()
        cell_h = target.height() / sample.height()
        for index in range(sample.width() + 1):
            x = target.left() + index * cell_w
            painter.drawLine(x, target.top(), x, target.bottom())
        for index in range(sample.height() + 1):
            y = target.top() + index * cell_h
            painter.drawLine(target.left(), y, target.right(), y)
        painter.restore()
    painter.setPen(QColor("#ff5252"))
    painter.drawLine(target.center().x(), target.top(), target.center().x(), target.bottom())
    painter.drawLine(target.left(), target.center().y(), target.right(), target.center().y())