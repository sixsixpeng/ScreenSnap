"""通知与输出结果：复制/保存/操作提示与通知跳转。"""

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
from app.bootstrap import (QApp, acquire_single_instance_lock, install_exception_hooks,
                           notify_existing_instance)
from app.notifications import NotificationBridge, drawn_icon, tray_icon


class NotificationMixin:
    """通知与输出结果：复制/保存/操作提示与通知跳转。"""

    def notify_capture_copied(self, image=None):
        """仅复制模式：选区已写入剪贴板，发送带缩略图的图片提示。"""
        if image is None:
            self.notify("已复制到剪贴板", "copy_notification")
            return
        if (self.config.data.get("bubble", True)
                and self.config.data.get("copy_notification", True)):
            # 与「图片已保存」走同一条路径：按「通知方式」设置选后端（默认 Win11 Toast）。
            # 老通知（本地预览）只在两种情况下出现：设置为老通知，或 Toast 发送失败时
            # CaptureNotification 的 native_failed → 本地预览自动回退。
            self.show_image_notice(image, "已复制到剪贴板")
        else:
            self.notify("已复制到剪贴板", "copy_notification")


    def remember_save_as_dir(self, directory):
        """记住「另存为」选择的目录，供下次打开对话框时默认定位。"""
        if not directory:
            return
        self.config.data["save_as_dir"] = str(directory)
        try:
            self.config.save()
        except OSError as error:
            self.logger.error("保存「另存为」目录失败: %s", error, exc_info=True)

    def notify_color_picked(self, color):
        """取色模式：色值已复制到剪贴板，发一次轻量提示。"""
        self.notify(f"已复制颜色 {color}", "copy_notification")


    def saved(self, path, image=None, notify=True, notification_setting="save_notification"):
        """记录保存结果并按对应的成功通知设置显示提示。"""
        self.logger.info("图片已保存: %s", path)
        if (notify and image is not None and self.config.data["bubble"]
                and self.config.data.get(notification_setting, True)):
            self.show_image_notice(image, "图片已保存", str(path), path)
        elif notify and image is None:
            self.notify(f"图片已保存: {path}", notification_setting, path)
        if notify and image is not None and self.config.data["sound"]:
            QApplication.beep()


    def show_image_notice(self, image, title, detail="", target_path=None):
        """新的图片提示替换旧提示，避免多个窗口堆叠。"""
        if self.capture_notice is not None:
            self.capture_notice.close()
        self.capture_notice = CaptureNotification(image, title=title, detail=detail,
                              target_path=target_path,
                              backend=self.config.data["notification_backend"],
                              close_after=self.config.data.get("notification_timeout", 2))
        self.capture_notice.file_activated.connect(self.open_notification_target)
        self.capture_notice.show_preview()


    def notify(self, message, setting=None, target_path=None, fallback=None):
        """总开关与分类开关控制气泡；音效仍由音效设置独立控制。"""
        category = setting or "operation_notification"
        if (self.config.data.get("bubble", True)
            and self.config.data.get(category, True)):
            self._notification_target_path = str(target_path) if target_path else None
            self._notification_fallback = fallback
            if self.config.data["notification_backend"] == "win11toast":
                from ui.native_toast import native_toast_duration, show_native_toast

                target = (self._notification_target_path, fallback)
                bridge = self.notification_bridge
                sent = show_native_toast(
                    "ScreenSnap", message,
                    on_click=lambda *args: bridge.activated.emit(target),
                    on_failed=lambda error: bridge.failed.emit((message, target)),
                    duration=native_toast_duration(
                        self.config.data.get("notification_timeout", 2)))
                timeout = int(self.config.data.get("notification_timeout", 2)) * 1000
                if not sent:
                    self.tray.showMessage(
                        "ScreenSnap", message, QSystemTrayIcon.Information, timeout)
            else:
                self.tray.showMessage(
                    "ScreenSnap", message, QSystemTrayIcon.Information,
                    int(self.config.data.get("notification_timeout", 2)) * 1000)
        if self.config.data["sound"]:
            QApplication.beep()


    def open_notification_target(self, path=None):
        """在资源管理器中选中文件；没有文件的贴图提示则打开贴图面板。"""
        target = str(path) if path else getattr(self, "_notification_target_path", None)
        if target and os.path.isfile(target):
            if not self.config.data.get("open_notification_file", True):
                return False
            subprocess.Popen(["explorer.exe", f"/select,{os.path.abspath(target)}"])
            return True
        if path is None and getattr(self, "_notification_fallback", None) == "sticker_panel":
            self.open_sticker_panel()
            return True
        return False

