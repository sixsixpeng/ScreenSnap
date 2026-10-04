"""设置页里的提示条预览：按**当前配置**实时画出提示条与放大镜的样子。

与截图遮罩共用 `screenshot.hint_items` 的文案生成和 `screenshot.overlay_info`
的排版绘制，因此：

* 改键后预览立刻显示新键位（不是写死的旧键位）；
* 勾选/取消提示项、拖动排序，预览里的内容与顺序同步变化；
* 关掉「显示快捷键提示」或「实时放大镜」，预览里对应的部分立刻消失。
"""

from PySide6.QtCore import QRect, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

def _hint_items(*args, **kwargs):
    """延迟导入：`screenshot` 包导入时会连带遮罩，而遮罩又依赖 ui，模块级导入会成环。"""
    from screenshot.hint_items import hint_items

    return hint_items(*args, **kwargs)


def _draw_magnifier(painter, image, point, bounds, source_point, grid, grid_color, size):
    from screenshot.magnifier_widget import paint_magnifier

    paint_magnifier(painter, image, point, bounds, source_point, grid=grid,
                    grid_color=grid_color, size=size)


def _magnifier_rect(point, bounds, size):
    from screenshot.magnifier_widget import magnifier_rect

    return magnifier_rect(point, bounds, size)


def _bar_layout(metrics, area, anchor, items, per_line=0, cursor=None):
    from screenshot.overlay_info import info_bar_layout

    return info_bar_layout(metrics, area, anchor, items, per_line=per_line, cursor=cursor)


def _draw_bar(painter, bar, rows, warning, metrics, align_right=False):
    from screenshot.overlay_info import paint_info_bar

    paint_info_bar(painter, bar, rows, warning, metrics, align_right=align_right)

# 预览用的“假截图”：一块浅色画布 + 两个色块，用来体现放大镜的取样内容。
_PREVIEW_SAMPLE = QRect(0, 0, 64, 48)


class HintBarPreview(QWidget):
    """提示条 + 放大镜的实时预览（只读，不参与实际截图）。"""

    # 与其它设置页预览一致：用例按 kind 判断预览属于哪个分组。
    kind = "hints"

    def __init__(self, config):
        super().__init__()
        self.config = config
        self.setMinimumWidth(260)
        self.setFixedHeight(120)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def refresh(self):
        self.update()

    def _sample_image(self):
        """预览放大镜里看到的小图：两块色块，便于看出确实在放大。"""
        from PySide6.QtGui import QImage

        image = QImage(_PREVIEW_SAMPLE.size(), QImage.Format_ARGB32)
        image.fill(QColor("#f7f9fa"))
        painter = QPainter(image)
        painter.fillRect(QRect(0, 0, 24, 48), QColor("#4b91a5"))
        painter.fillRect(QRect(28, 16, 36, 16), QColor("#e0a33c"))
        painter.end()
        return image

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        area = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        painter.fillRect(area, QColor("#e8edf0"))
        # 假截图内容：左侧色块 + 右侧几条“文字行”，让提示条与放大镜有背景可对比。
        painter.fillRect(area.adjusted(12, 12, -area.width() * 0.55, -12), QColor("#4b91a5"))
        painter.fillRect(area.adjusted(area.width() * 0.52, 12, -12, -12), QColor("#f7f9fa"))
        painter.setPen(QPen(QColor("#b2c0c6"), 1))
        for row in range(3):
            y = int(area.top() + 30 + row * 16)
            painter.drawLine(int(area.left() + area.width() * 0.56), y, int(area.right() - 16), y)
        inner = QRect(int(area.left()) + 8, int(area.top()) + 8,
                      int(area.width()) - 16, int(area.height()) - 16)
        # 光标位置：偏上，因此提示条会贴在放大镜下方（与截图时一致）。
        cursor = inner.topLeft() + _point_offset(inner, 0.22, 0.30)
        data = self.config.data
        frame = QRect()
        if data.get("magnifier", True):
            size = int(data.get("magnifier_size", 140) or 140)
            frame = _magnifier_rect(cursor, inner, size)
            painter.setClipRect(inner)
            painter.translate(-frame.x(), -frame.y())
            _draw_magnifier(painter, self._sample_image(), cursor, inner, cursor,
                            data.get("magnifier_grid", True),
                            data.get("magnifier_grid_color", "#cccccc"), size)
            painter.translate(frame.x(), frame.y())
            painter.setClipping(False)
        anchor = frame if not frame.isEmpty() else QRect(cursor.x() + 32, cursor.y() + 32, 8, 8)
        items = _hint_items(data, (cursor.x(), cursor.y()), (240, 160))
        try:
            per_line = int(data.get("capture_hint_per_line", 0) or 0)
        except (TypeError, ValueError):
            per_line = 0
        bar, rows, align_right = _bar_layout(self.fontMetrics(), inner, anchor, items,
                                             per_line=max(0, min(per_line, 8)),
                                             cursor=(cursor.x(), cursor.y()))
        painter.save()
        painter.translate(inner.topLeft())
        _draw_bar(painter, bar, rows, None, self.fontMetrics(), align_right)
        painter.restore()
        painter.setPen(QPen(QColor("#8d9aa1"), 1, Qt.DashLine))
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(inner)
        if not items:
            painter.setPen(QColor("#6b767c"))
            painter.drawText(inner, Qt.AlignCenter, "快捷键提示已关闭")


def _point_offset(rect, fx, fy):
    from PySide6.QtCore import QPoint

    return QPoint(int(rect.width() * fx), int(rect.height() * fy))
