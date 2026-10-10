"""贴图流程：新建/替换贴图、剪贴板贴图、管理面板与回收站。"""

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


class StickerFlowMixin:
    """贴图流程：新建/替换贴图、剪贴板贴图、管理面板与回收站。"""

    def _connect_sticker_signals(self, views):
        for view in views:
            view.sticker_requested.connect(self.add_sticker)
            view.quick_sticker_requested.connect(self.add_quick_sticker)


    def add_quick_sticker(self, image, position=None):
        """Space 快速贴图：先按统一保存目录落盘（与编辑器“贴图”一致），再创建贴图。"""
        # 编辑器路径的落盘由编辑器自己完成（save(automatic=True)），快速贴图没有编辑器，
        # 因此在这里补一次保存；保存失败不阻断贴图，只记日志并提示。
        try:
            # notify=False：只通知“已创建贴图”，不弹“图片已保存”；
            # copy_to_clipboard=False：快速贴图不改动剪贴板（贴图本身才是产物）。
            self.save_capture_images([(image, None)], notify=False, copy_to_clipboard=False)
        except (OSError, TypeError, ValueError, RuntimeError) as error:
            self.logger.error("快速贴图保存失败: %s", error, exc_info=True)
            self.notify(f"快速贴图保存失败: {error}")
        self.add_sticker(image, position)


    def add_sticker(self, image, position=None):
        """创建新贴图并根据独立设置决定是否显示托盘提示。"""
        try:
            item = self.stickers.add(image, position=position)
            item.raise_()
            item.activateWindow()
        except (OSError, TypeError, ValueError, RuntimeError) as error:
            self.logger.error("创建贴图失败: %s", error, exc_info=True)
            self.notify(f"创建贴图失败: {error}")
            return
        self.logger.info("贴图已创建并置于前台: %s", getattr(item, "source", "临时图片"))
        self.notify("已创建贴图", "sticker_notification",
                getattr(item, "source", None), "sticker_panel")


    def edit_sticker(self, image):
        """在独立编辑器中编辑贴图副本；首次保存会分配新文件名。"""
        self.edit_images([(image.copy(), None)], from_capture=False)


    def paste_clipboard(self):
        """把剪贴板中的图片、文件、颜色或文字直接贴到屏幕上。"""
        item = self.stickers.paste_clipboard()
        if item is None:
            self.logger.debug("剪贴板中没有可贴出的图片、文件、颜色或文字")
            self.notify("剪贴板中没有可贴出的内容")
            return
        self.notify("已贴出剪贴板内容", "sticker_notification",
                getattr(item, "source", None), "sticker_panel")


    def open_sticker_panel(self):
        """复用同一个贴图管理窗口，打开时按当前贴图重建列表。"""
        if self.sticker_panel is None:
            self.sticker_panel = StickerPanel(self.stickers)
        self.logger.debug("打开贴图管理窗口: %d 张贴图", len(self.stickers.items))
        self.sticker_panel.refresh()
        self.sticker_panel.show()
        self.sticker_panel.raise_()
        self.sticker_panel.activateWindow()


    def open_recycle_bin(self):
        """复用同一个回收站窗口，打开时按当前回收内容重建列表。"""
        if self.recycle_window is None:
            self.recycle_window = RecycleWindow(self.stickers, self.config)
        self.logger.debug("打开贴图回收站: %d 张", len(self.stickers.recycle_items()))
        self.recycle_window.refresh()
        self.recycle_window.show()
        self.recycle_window.raise_()
        self.recycle_window.activateWindow()

