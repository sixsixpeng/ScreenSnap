"""全屏毫秒时钟：用于连续截图/性能测试。

运行：
    python tools/screenshot_test_clock.py

快捷键：
    Esc / Q  退出
    F11      最大化/还原
    Space    暂停/继续时间
    C        切换配色

设计目的：
* 时间精确到毫秒，连续截图保存后可直接比较时间判断是否拿到了旧画面。
* 底部色条每 40ms 移动一格，画面不是静态的，更容易发现重复帧。
* 默认在鼠标所在显示器最大化，多屏时不会跑到另一块屏。
"""
from __future__ import annotations

import sys
from datetime import datetime

from PySide6.QtCore import QPoint, QRect, Qt, QTimer
from PySide6.QtGui import QColor, QCursor, QFont, QGuiApplication, QPainter
from PySide6.QtWidgets import QApplication, QLabel, QSizePolicy, QVBoxLayout, QWidget

THEMES = (
    ("#05080a", "#d9f7ef", "#168cff", "#18323d"),
    ("#101014", "#fff2d6", "#ff9f43", "#3a2a1c"),
    ("#07110d", "#d6ffe8", "#4ade80", "#173b2a"),
)


class PulseBar(QWidget):
    """底部移动色条；每格代表约 40ms，截图里能直接看出帧是否变化。"""

    SEGMENTS = 24

    def __init__(self, parent=None):
        super().__init__(parent)
        self.active = 0
        self.accent = QColor("#168cff")
        self.track = QColor("#18323d")
        self.setMinimumHeight(42)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_state(self, active, accent, track):
        changed = (active != self.active or accent != self.accent or track != self.track)
        self.active = active
        self.accent = QColor(accent)
        self.track = QColor(track)
        if changed:
            self.update()

    def paintEvent(self, event):
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        width = max(1, self.width())
        height = max(1, self.height())
        gap = 6
        cell = max(2.0, (width - gap * (self.SEGMENTS - 1)) / self.SEGMENTS)
        for index in range(self.SEGMENTS):
            left = index * (cell + gap)
            color = self.accent if index == self.active else self.track
            painter.fillRect(QRect(int(left), 0, int(cell), height), color)
        painter.end()


class ClockWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("ScreenSnap 截图测试时钟")
        self.resize(1280, 800)
        self.setAutoFillBackground(True)
        self.theme_index = 0
        self.paused = False
        self.last_now = datetime.now()

        self.time_label = QLabel(self)
        self.time_label.setAlignment(Qt.AlignCenter)
        self.time_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        self.date_label = QLabel(self)
        self.date_label.setAlignment(Qt.AlignCenter)

        self.hint = QLabel("Esc / Q 退出    F11 最大化/还原    Space 暂停    C 换色", self)
        self.hint.setAlignment(Qt.AlignCenter)

        self.bar = PulseBar(self)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(12)
        layout.addWidget(self.date_label)
        layout.addWidget(self.time_label, 1)
        layout.addWidget(self.bar)
        layout.addWidget(self.hint)

        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.PreciseTimer)
        self.timer.setInterval(16)
        self.timer.timeout.connect(self._tick)
        self._apply_theme()
        self._set_fonts()
        self.timer.start()

    def _apply_theme(self):
        background, foreground, accent, track = THEMES[self.theme_index]
        self.setStyleSheet(
            f"QWidget {{ background: {background}; color: {foreground}; }}"
            f"QLabel {{ color: {foreground}; }}"
        )
        self.bar.set_state(self.bar.active, accent, track)
        self.hint.setStyleSheet(f"color: {foreground};")

    def _set_fonts(self):
        height = max(480, self.height())
        time_font = QFont("Consolas")
        time_font.setStyleHint(QFont.Monospace)
        time_font.setWeight(QFont.Bold)
        time_font.setPixelSize(max(72, int(height * 0.19)))
        self.time_label.setFont(time_font)

        date_font = QFont("Consolas")
        date_font.setStyleHint(QFont.Monospace)
        date_font.setPixelSize(max(20, int(height * 0.035)))
        self.date_label.setFont(date_font)
        self.hint.setFont(date_font)

    def showEvent(self, event):
        super().showEvent(event)
        screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        if screen is not None:
            self.setGeometry(screen.availableGeometry())
        self._set_fonts()
        self.raise_()
        self.activateWindow()
        self.setFocus(Qt.ActiveWindowFocusReason)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._set_fonts()

    def _tick(self):
        if not self.paused:
            self.last_now = datetime.now()
        moment = self.last_now
        self.time_label.setText(
            f"{moment:%H:%M:%S}.{moment.microsecond // 1000:03d}"
        )
        self.date_label.setText(moment.strftime("%Y-%m-%d  %A"))
        self.bar.set_state(
            (moment.microsecond // 40000) % PulseBar.SEGMENTS,
            THEMES[self.theme_index][2],
            THEMES[self.theme_index][3],
        )

    def keyPressEvent(self, event):
        key = event.key()
        if key in (Qt.Key_Escape, Qt.Key_Q):
            self.close()
        elif key == Qt.Key_F11:
            if self.isFullScreen() or self.isMaximized():
                self.showNormal()
            else:
                self.showMaximized()
        elif key == Qt.Key_Space:
            self.paused = not self.paused
        elif key == Qt.Key_C:
            self.theme_index = (self.theme_index + 1) % len(THEMES)
            self._apply_theme()
        else:
            super().keyPressEvent(event)


def main():
    app = QApplication(sys.argv)
    window = ClockWindow()
    screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
    if screen is not None:
        window.move(screen.availableGeometry().topLeft())
    window.showMaximized()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
