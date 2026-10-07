"""托盘图标与通知桥：由 main 组装，保持 main 可直接导入这些名字。"""

import ctypes
import logging
import os
import subprocess
import sys
import threading
from pathlib import Path
from PySide6.QtCore import Qt, QPoint, QRect, QTimer, QSignalBlocker, QObject, Signal, Slot, QLockFile
from PySide6.QtGui import QIcon, QPixmap, QPainter, QColor, QPen, QGuiApplication, QFont, QCursor
from PySide6.QtWidgets import QApplication, QSystemTrayIcon, QGraphicsView, QFileDialog
from config import ConfigManager
from config.config_manager import TOOL_WIDTH_KEYS
from core import app_icon, data_dir, capture, qimage_to_pillow
from core.dpi import DisplayMapper
from core.startup import set_start_on_boot
from editor import EditorWindow
from hotkey import HotkeyManager
from logger import configure_logging
from screenshot import MaskWindow
from screenshot.mask_window import window_label
from sticker import StickerManager
from ui import SettingsWindow, make_tray_menu, CaptureNotification, StickerPanel
from ui.recycle_window import RecycleWindow
from ui.theme import apply_theme
from ui.window_bounds import WindowBoundsFilter
from PIL import Image

class NotificationBridge(QObject):
    """将 Toast 的后台线程回调安全地送回 Qt 主线程。"""

    activated = Signal(object)
    failed = Signal(object)

    def __init__(self, application):
        super().__init__(application.qt)
        self.application = application
        self.activated.connect(self._activate)
        self.failed.connect(self._fallback)

    @Slot(object)
    def _activate(self, target):
        path, fallback = target
        self.application._notification_target_path = path
        self.application._notification_fallback = fallback
        self.application.open_notification_target(path)

    @Slot(object)
    def _fallback(self, payload):
        message, target = payload
        path, fallback = target
        self.application._notification_target_path = path
        self.application._notification_fallback = fallback
        self.application.tray.showMessage(
            "ScreenSnap", message, QSystemTrayIcon.Information,
            int(self.application.config.data.get("notification_timeout", 2)) * 1000)



def drawn_icon():
    """现场绘制高分辨率取景框图标，仅在没有图标文件时兜底。"""
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor("#18343b"))
    painter.drawRoundedRect(2, 2, 60, 60, 14, 14)
    painter.setPen(QPen(QColor("#f5fbfa"), 5, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    for points in [((15, 26), (15, 15), (26, 15)),
                   ((38, 15), (49, 15), (49, 26)),
                   ((49, 38), (49, 49), (38, 49)),
                   ((26, 49), (15, 49), (15, 38))]:
        painter.drawLine(*points[0], *points[1])
        painter.drawLine(*points[1], *points[2])
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor("#30d2a2"))
    painter.drawEllipse(26, 26, 12, 12)
    painter.end()
    pixmap.setDevicePixelRatio(2)
    return QIcon(pixmap)



def tray_icon():
    """托盘与应用图标：优先使用项目图标文件，缺失时用内置绘制图标。"""
    icon = app_icon()
    return icon if not icon.isNull() else drawn_icon()


