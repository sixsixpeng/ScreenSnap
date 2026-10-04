"""绘制光标附近像素的放大预览。"""

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QPen

# 放大镜尺寸（正方形边长）与缩放倍率：采样区域 = 尺寸 / 倍率，
# 因此 140 px 的默认尺寸仍是 20 px 采样，与原行为一致；用户放大尺寸时视野同步变大。
MAGNIFIER_DEFAULT_SIZE = 140
MAGNIFIER_MIN_SIZE = 100
MAGNIFIER_MAX_SIZE = 320
MAGNIFIER_ZOOM = 7.0
# 放大镜偏移光标的距离（像素）：默认在右下，贴边时翻到左上。
MAGNIFIER_OFFSET = 32


def sample_size(size):
    """按放大镜尺寸换算采样区域边长（像素），保证缩放倍率恒定。"""
    return max(1, int(round(max(1, size) / MAGNIFIER_ZOOM)))


def magnifier_rect(point, bounds, size=MAGNIFIER_DEFAULT_SIZE):
    width = min(size, max(1, bounds.width() - 20))
    height = min(size, max(1, bounds.height() - 20))
    x = point.x() + MAGNIFIER_OFFSET
    y = point.y() + MAGNIFIER_OFFSET
    if x + width + 4 > bounds.width():
        x = point.x() - width - MAGNIFIER_OFFSET
    if y + height + 4 > bounds.height():
        y = point.y() - height - MAGNIFIER_OFFSET
    return QRect(
        min(max(x, 2), max(2, bounds.width() - width - 2)),
        min(max(y, 2), max(2, bounds.height() - height - 2)),
        width, height,
    ).adjusted(-2, -2, 2, 2)


def paint_magnifier(painter, image, point, bounds, source_point=None, grid=False,
                    grid_color=None, size=MAGNIFIER_DEFAULT_SIZE):
    """把光标周围的小块原图放大绘制到遮罩上的悬浮区域。"""
    source_point = source_point or point
    side = sample_size(size)
    sample_width = min(side, image.width())
    sample_height = min(side, image.height())
    sample = QRect(
        min(max(0, source_point.x() - sample_width // 2), image.width() - sample_width),
        min(max(0, source_point.y() - sample_height // 2), image.height() - sample_height),
        sample_width, sample_height,
    )
    frame = magnifier_rect(point, bounds, size)
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
