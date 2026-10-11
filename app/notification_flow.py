"""通知与输出结果：复制/保存/操作提示与通知跳转。"""

import ctypes
import logging
import os
import subprocess
import sys
import threading
from pathlib import Path
from PySide6.QtCore import Qt, QPoint, QRect, QTimer, QSignalBlocker, QObject, Signal, Slot, QLockFile
from PySide6.QtGui import QIcon, QPixmap, QPainter, QColor, QImage, QPen, QGuiApplication, QFont, QCursor
from PySide6.QtWidgets import QApplication, QSystemTrayIcon, QGraphicsView, QFileDialog
from config import ConfigManager
from config.config_manager import TOOL_WIDTH_KEYS
from core import app_icon, data_dir, capture, qimage_to_pillow
from core.path_utils import configured_dir
from core.sound import play_sound
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

# 保住"发射后不管"的子进程句柄：Popen 对象被 GC 回收时若子进程仍在运行，
# Python 会打印 ResourceWarning: subprocess NNNN is still running（2026-10-11 用户实测）。
_SPAWNED = []


def _spawn_detached(command):
    """启动一个不等待的进程（explorer 等），并保住句柄避免 ResourceWarning。"""
    try:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        return None
    _SPAWNED.append(process)
    _SPAWNED[:] = [item for item in _SPAWNED if item.poll() is None]   # 顺手清理已结束的
    return process


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
            self.show_image_notice(image, "已复制到剪贴板", category="copy_notification")
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
        """取色模式：色值已复制到剪贴板，提示带上色块缩略图（2026-10-11：除复制文字外都带图）。"""
        if self.config.data.get("bubble", True) and self.config.data.get("picker_notification", True):
            try:
                swatch = QImage(160, 90, QImage.Format_RGB32)
                swatch.fill(QColor(color))
                self.show_image_notice(swatch, f"已复制颜色 {color}", category="picker_notification")
                return
            except (TypeError, ValueError) as error:  # noqa: BLE001 色值异常时退回文字
                self.logger.warning("取色通知色块失败，退回文字通知: %s", error)
        self.notify(f"已复制颜色 {color}", "picker_notification")


    def saved(self, path, image=None, notify=True, notification_setting="save_notification"):
        """记录保存结果并按对应的成功通知设置显示提示。"""
        self.logger.info("图片已保存: %s", path)
        # 记住最近一次真正落盘的路径：其后的「已创建贴图」通知点击应跳到保存目录而不是缓存。
        self._last_saved_path = str(path)
        if (notify and image is not None and self.config.data["bubble"]
                and self.config.data.get(notification_setting, True)):
            self.show_image_notice(image, "图片已保存", str(path), path,
                                   category=notification_setting)
        elif notify and image is None:
            self.notify(f"图片已保存: {path}", notification_setting, path)
        # 音效移入 show_image_notice()（2026-10-11）：与文字通知的 notify() 一致 ——
        # 只有**真的显示了通知**才响；否则这里会在分类开关关闭时仍然响（旧行为不一致）。


    def _play_notification_sound(self, category=None):
        """按通知分类播放提示音（2026-10-11 A+B+C）：sound_<category> → notification_sound → default。

        受 sound 总开关控制；播放失败在 core.sound 内部已兜底，绝不影响通知本身。
        """
        if not self.config.data.get("sound", True):
            return None
        key = {"copy_notification": "sound_copy", "save_notification": "sound_save",
               "sticker_notification": "sound_sticker", "picker_notification": "sound_picker",
               "operation_notification": "sound_operation"}.get(category or "", "")
        kind = self.config.data.get(key, "") if key else ""
        if not kind:
            kind = self.config.data.get("notification_sound", "default") or "default"
        if kind == "custom":
            # 先解析再播放：文件不存在时这里就回落 default，调用方（与用例）看到的是**最终**音色，
            # core.sound 内部还有一层兜底。
            from core.sound import resolve_kind

            resolved, target = resolve_kind("custom", self.config.data.get("notification_sound_file", ""))
            return play_sound(resolved, target)
        return play_sound(kind)

    def show_image_notice(self, image, title, detail="", target_path=None, category=None):
        """新的图片提示替换旧提示，避免多个窗口堆叠。

        点击跳转（2026-10-11 用户要求：**所有带图通知都要能跳转**）：
        · 有具体文件 ⇒ 资源管理器里选中它；
        · 目标是应用数据目录内的私有缓存（贴图的 sticker_xxx.png 等）⇒ 忽略，改跳保存目录；
        · 没有文件（仅复制 / 取色等）⇒ 跳**统一保存目录**。
        """
        target_path = self._sane_notification_target(target_path)
        if self.capture_notice is not None:
            self.capture_notice.close()
        self.capture_notice = CaptureNotification(image, title=title, detail=detail,
                              target_path=target_path,
                              backend=self.config.data["notification_backend"],
                              close_after=self.config.data.get("notification_timeout", 2),
                              settings=self.config.data)
        self.capture_notice.file_activated.connect(self.open_notification_target)
        self.capture_notice.show_preview()
        # 音效统一放在这里（2026-10-11）：按通知分类解析音色（sound_<category> → 全局 → default），
        # 受 sound 总开关控制。带图通知原先绕过了 notify() 的 beep，导致「仅复制 / 取色 /
        # 已创建贴图 / 贴出剪贴板」在开启音效时全都没声音（这次的回归就是这么来的）。
        self._play_notification_sound(category)


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
        # 音色按分类解析（2026-10-11 A+B+C）；总开关在 _play_notification_sound 内部判断。
        self._play_notification_sound(category)


    def _sane_notification_target(self, target_path):
        """把通知点击目标规整成用户能打开的位置（2026-10-11 用户要求：**不要过滤**）。

        规则：① 只要目标存在（文件或目录）就直接用它 —— 包括贴图私有缓存这类已落盘的图片，
        缓存也能跳，不做数据目录过滤；② 完全没有目标时才回落到统一保存目录，
        保证「带图通知都能点」。
        """
        candidates = []
        if target_path:
            candidates.append(str(target_path))
        for candidate in candidates:
            if os.path.exists(candidate):
                return candidate
        try:
            return str(configured_dir(self.config.data))
        except (TypeError, ValueError, OSError):
            return None

    def open_notification_target(self, path=None):
        """在资源管理器中选中文件；没有文件的贴图提示则打开贴图面板。"""
        target = str(path) if path else getattr(self, "_notification_target_path", None)
        if target and os.path.isfile(target):
            if not self.config.data.get("open_notification_file", True):
                return False
            _spawn_detached(["explorer.exe", f"/select,{os.path.abspath(target)}"])
            return True
        # 目录（例如统一保存目录）：直接打开该目录（2026-10-11：带图通知都要能跳转）。
        if target and os.path.isdir(target):
            if not self.config.data.get("open_notification_file", True):
                return False
            _spawn_detached(["explorer.exe", os.path.abspath(target)])
            return True
        if path is None and getattr(self, "_notification_fallback", None) == "sticker_panel":
            self.open_sticker_panel()
            return True
        return False

