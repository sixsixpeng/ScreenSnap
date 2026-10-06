"""Small, consistent icons for application actions without a clear Qt glyph."""

from functools import lru_cache

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import (QColor, QIcon, QIconEngine, QPainter, QPainterPath,
                           QPen, QPixmap, QPalette)
from PySide6.QtWidgets import QApplication


def action_icon(name):
    return QIcon(_PaletteActionIconEngine(name))


class _PaletteActionIconEngine(QIconEngine):
    def __init__(self, name):
        super().__init__()
        self.name = name

    def clone(self):
        return _PaletteActionIconEngine(self.name)

    def paint(self, painter, rect, mode, state):
        pixmap = self.pixmap(rect.size(), mode, state)
        painter.drawPixmap(QRectF(rect), pixmap, QRectF(pixmap.rect()))

    def pixmap(self, size, mode, state):
        application = QApplication.instance()
        palette = application.palette() if application is not None else QPalette()
        ink = palette.color(QPalette.WindowText).name()
        accent = palette.color(QPalette.Highlight).name()
        return _render_action_icon(self.name, ink, accent).pixmap(size, mode, state)


@lru_cache(maxsize=192)
def _render_action_icon(name, ink_color, accent_color):
    pixmap = QPixmap(24, 24)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    ink = QColor(ink_color)
    accent = QColor(accent_color)
    painter.setPen(QPen(ink, 1.8, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    painter.setBrush(Qt.NoBrush)

    if name == "camera":
        painter.drawRoundedRect(QRectF(3, 7, 18, 13), 2, 2)
        painter.drawRoundedRect(QRectF(7, 4, 6, 3), 1, 1)
        painter.drawEllipse(QRectF(9, 10, 6, 6))
    elif name == "resize":
        painter.drawRect(QRectF(5, 5, 14, 14))
        painter.drawLine(2, 8, 2, 2)
        painter.drawLine(2, 2, 8, 2)
        painter.drawLine(16, 2, 22, 2)
        painter.drawLine(22, 2, 22, 8)
        painter.drawLine(2, 16, 2, 22)
        painter.drawLine(2, 22, 8, 22)
        painter.drawLine(16, 22, 22, 22)
        painter.drawLine(22, 22, 22, 16)
    elif name in ("window_edit", "edit"):
        painter.drawRoundedRect(QRectF(2.5, 4, 16, 15), 1.5, 1.5)
        painter.drawLine(3, 8, 18, 8)
        painter.setPen(QPen(accent, 2.2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        painter.drawLine(12, 19, 20, 11)
        painter.drawLine(18.5, 10, 21, 12.5)
    elif name == "eraser":
        path = QPainterPath()
        path.moveTo(4, 15)
        path.lineTo(12, 5)
        path.quadTo(13, 4, 14, 5)
        path.lineTo(21, 11)
        path.lineTo(14, 20)
        path.lineTo(8, 20)
        path.closeSubpath()
        painter.drawPath(path)
        painter.drawLine(8, 16, 15, 21)
    elif name in ("rotate", "rotate_180", "rotate_left", "rotate_right"):
        start_angle = 35 if name != "rotate_left" else 145
        span_angle = 285 if name != "rotate_right" else -285
        painter.drawArc(QRectF(4, 4, 16, 16), start_angle * 16, span_angle * 16)
        path = QPainterPath()
        if name == "rotate_left":
            path.moveTo(6, 3)
            path.lineTo(3, 8)
            path.lineTo(9, 8)
        else:
            path.moveTo(18, 3)
            path.lineTo(21, 8)
            path.lineTo(15, 8)
        path.closeSubpath()
        painter.setBrush(accent)
        painter.setPen(Qt.NoPen)
        painter.drawPath(path)
        if name == "rotate_180":
            painter.setPen(QPen(accent, 1.5))
            painter.drawLine(7, 12, 17, 12)
    elif name in ("flip_horizontal", "flip_vertical"):
        painter.drawRoundedRect(QRectF(3, 4, 18, 16), 1.5, 1.5)
        painter.setPen(QPen(accent, 1.5, Qt.DashLine))
        if name == "flip_horizontal":
            painter.drawLine(12, 4, 12, 20)
            painter.setPen(QPen(ink, 1.8, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            painter.drawLine(9, 9, 5, 12)
            painter.drawLine(5, 12, 9, 15)
            painter.drawLine(15, 9, 19, 12)
            painter.drawLine(19, 12, 15, 15)
        else:
            painter.drawLine(3, 12, 21, 12)
            painter.setPen(QPen(ink, 1.8, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            painter.drawLine(9, 9, 12, 5)
            painter.drawLine(12, 5, 15, 9)
            painter.drawLine(9, 15, 12, 19)
            painter.drawLine(12, 19, 15, 15)
    elif name in ("clipboard_image", "clipboard_edit", "sticker"):
        if name in ("clipboard_image", "clipboard_edit"):
            # 剪贴板底板 + 顶部夹子 + 内部“图片”符号（山+太阳）
            painter.drawRoundedRect(QRectF(4, 5, 16, 16), 2.5, 2.5)
            painter.drawRoundedRect(QRectF(9, 2, 6, 4), 1, 1)
            painter.drawLine(7, 7, 17, 7)
            painter.drawRoundedRect(QRectF(7, 9.5, 10, 8.5), 1.5, 1.5)
            painter.setPen(QPen(accent, 1.5))
            painter.drawEllipse(QRectF(13.5, 10.5, 2, 2))
            painter.setPen(QPen(ink, 1.6, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            painter.drawLine(7.8, 16.5, 10.5, 13.8)
            painter.drawLine(10.5, 13.8, 12.5, 15.6)
            painter.drawLine(12.5, 15.6, 14.5, 13.4)
            painter.drawLine(14.5, 13.4, 16.2, 16.5)
        else:
            painter.drawRoundedRect(QRectF(3, 6, 14, 14), 2, 2)
            painter.drawRoundedRect(QRectF(8, 3, 13, 14), 2, 2)
        if name == "sticker":
            painter.drawRect(QRectF(8, 10, 8, 6))
            painter.drawEllipse(QRectF(9, 11, 2, 2))
            painter.drawLine(8, 16, 11, 13)
            painter.drawLine(11, 13, 13, 15)
            painter.drawLine(13, 15, 16, 12)
        if name == "clipboard_edit":
            painter.setPen(QPen(accent, 2, Qt.SolidLine, Qt.RoundCap))
            painter.drawLine(14, 20, 21, 13)
            painter.drawLine(19, 12, 22, 15)
    elif name == "recycle":
        painter.setPen(QPen(ink, 1.8))
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(QRectF(5, 8, 14, 13), 2, 2)
        painter.drawLine(8, 8, 8, 4)
        painter.drawLine(16, 8, 16, 4)
        painter.drawLine(6, 4, 18, 4)
        painter.drawLine(9, 12, 9, 18)
        painter.drawLine(15, 12, 15, 18)
    elif name == "layers":
        for offset in (0, 5, 10):
            path = QPainterPath()
            path.moveTo(12, 3 + offset)
            path.lineTo(21, 8 + offset)
            path.lineTo(12, 13 + offset)
            path.lineTo(3, 8 + offset)
            path.closeSubpath()
            painter.drawPath(path)
            if offset == 5:
                break
        painter.drawLine(4, 16, 12, 21)
        painter.drawLine(12, 21, 20, 16)
    elif name in ("lock", "unlock"):
        painter.drawRoundedRect(QRectF(4, 10, 16, 11), 2, 2)
        if name == "lock":
            painter.drawArc(QRectF(7, 3, 10, 13), 0, 180 * 16)
        else:
            painter.drawArc(QRectF(7, 3, 10, 13), 35 * 16, 145 * 16)
            painter.drawLine(7, 6, 4, 4)
        painter.setPen(QPen(accent, 1.8))
        painter.drawLine(12, 14, 12, 17)
    elif name in ("border", "shadow"):
        painter.drawRect(QRectF(4, 4, 14, 14))
        if name == "shadow":
            painter.setPen(QPen(accent, 1.7))
            painter.drawLine(8, 21, 21, 21)
            painter.drawLine(21, 8, 21, 21)
            painter.drawLine(18, 18, 21, 21)
    elif name in ("visibility", "hidden"):
        path = QPainterPath()
        path.moveTo(2, 12)
        path.quadTo(12, 2, 22, 12)
        path.quadTo(12, 22, 2, 12)
        painter.drawPath(path)
        painter.drawEllipse(QRectF(9, 9, 6, 6))
        if name == "hidden":
            painter.setPen(QPen(accent, 2.2, Qt.SolidLine, Qt.RoundCap))
            painter.drawLine(4, 20, 20, 4)
    elif name in ("group", "group_add", "group_move"):
        painter.drawRoundedRect(QRectF(2, 6, 20, 14), 2, 2)
        painter.drawLine(3, 6, 8, 6)
        painter.drawLine(8, 6, 10, 4)
        painter.drawLine(10, 4, 15, 4)
        painter.drawEllipse(QRectF(7, 10, 4, 4))
        painter.drawEllipse(QRectF(14, 10, 4, 4))
        painter.drawArc(QRectF(5, 14, 8, 6), 0, 180 * 16)
        painter.drawArc(QRectF(12, 14, 8, 6), 0, 180 * 16)
        if name == "group_add":
            painter.setPen(QPen(accent, 2))
            painter.drawLine(17, 2, 17, 8)
            painter.drawLine(14, 5, 20, 5)
        elif name == "group_move":
            painter.setPen(QPen(accent, 2, Qt.SolidLine, Qt.RoundCap))
            painter.drawLine(3, 3, 9, 3)
            painter.drawLine(6, 1, 9, 3)
            painter.drawLine(6, 5, 9, 3)
    elif name in ("layer_top", "layer_bottom"):
        painter.drawLine(5, 8, 12, 4)
        painter.drawLine(12, 4, 19, 8)
        painter.drawLine(5, 11, 12, 15)
        painter.drawLine(12, 15, 19, 11)
        painter.drawLine(5, 14, 12, 18)
        painter.drawLine(12, 18, 19, 14)
        if name == "layer_top":
            painter.drawLine(12, 2, 12, 7)
            painter.drawLine(9, 5, 12, 2)
            painter.drawLine(15, 5, 12, 2)
        else:
            painter.drawLine(12, 22, 12, 17)
            painter.drawLine(9, 19, 12, 22)
            painter.drawLine(15, 19, 12, 22)
    elif name == "pin":
        painter.drawLine(8, 3, 16, 3)
        painter.drawLine(10, 3, 10, 9)
        painter.drawLine(14, 3, 14, 9)
        painter.drawLine(7, 9, 17, 9)
        painter.drawLine(9, 9, 12, 15)
        painter.drawLine(15, 9, 12, 15)
        painter.drawLine(12, 15, 12, 22)
    elif name == "locate":
        painter.drawEllipse(QRectF(3, 3, 18, 18))
        painter.drawEllipse(QRectF(8, 8, 8, 8))
        painter.setBrush(accent)
        painter.drawEllipse(QRectF(10, 10, 4, 4))
        painter.setPen(QPen(accent, 1.6))
        painter.drawLine(12, 1, 12, 4)
        painter.drawLine(12, 20, 12, 23)
        painter.drawLine(1, 12, 4, 12)
        painter.drawLine(20, 12, 23, 12)
    elif name == "rename":
        painter.drawLine(4, 19, 7, 18)
        painter.drawLine(7, 18, 18, 7)
        painter.drawLine(15, 5, 20, 10)
        painter.drawLine(4, 19, 3, 21)
    elif name == "click_through":
        path = QPainterPath()
        path.moveTo(5, 2)
        path.lineTo(5, 18)
        path.lineTo(9, 14)
        path.lineTo(12, 21)
        path.lineTo(15, 20)
        path.lineTo(12, 13)
        path.lineTo(19, 13)
        path.closeSubpath()
        painter.drawPath(path)
        painter.setPen(QPen(accent, 1.8))
        painter.drawLine(16, 5, 22, 5)
    elif name == "settings":
        painter.drawLine(3, 6, 21, 6)
        painter.drawLine(3, 12, 21, 12)
        painter.drawLine(3, 18, 21, 18)
        painter.setBrush(accent)
        for x, y in ((9, 6), (16, 12), (7, 18)):
            painter.drawEllipse(QRectF(x - 2, y - 2, 4, 4))
    elif name == "background":
        painter.setPen(Qt.NoPen)
        for row in range(3):
            for column in range(3):
                if (row + column) % 2 == 0:
                    painter.setBrush(accent if row == 1 and column == 1 else ink)
                    painter.drawRect(4 + column * 6, 4 + row * 6, 6, 6)
    elif name == "opacity":
        painter.setPen(QPen(ink, 1.8))
        painter.setBrush(Qt.NoBrush)
        painter.drawEllipse(QRectF(3, 3, 18, 18))
        painter.setPen(Qt.NoPen)
        painter.setBrush(accent)
        painter.drawPie(QRectF(3, 3, 18, 18), 90 * 16, 180 * 16)
    elif name == "output":
        painter.drawRoundedRect(QRectF(3, 4, 18, 16), 2, 2)
        painter.setPen(QPen(accent, 2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        painter.drawLine(6, 16, 10, 12)
        painter.drawLine(10, 12, 13, 15)
        painter.drawLine(13, 15, 18, 9)
    elif name == "logs":
        for y in (6, 12, 18):
            painter.drawEllipse(QRectF(3, y - 1, 2, 2))
            painter.drawLine(8, y, 21, y)
    elif name == "exit":
        painter.drawRoundedRect(QRectF(3, 3, 12, 18), 1.5, 1.5)
        painter.setPen(QPen(accent, 2.2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        painter.drawLine(10, 12, 21, 12)
        painter.drawLine(17, 8, 21, 12)
        painter.drawLine(17, 16, 21, 12)

    painter.end()
    return QIcon(pixmap)