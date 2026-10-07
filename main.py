"""常驻托盘的程序入口。"""

import ctypes
import logging
import os
import subprocess
import sys
import threading
from pathlib import Path


def enable_windows_dpi_awareness():
    """尽早启用每显示器 DPI 感知，减少 Windows 坐标虚拟化。"""
    if os.name != "nt":
        return
    try:
        # -4 == DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2。必须在 QApplication 前调用。
        if ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            return
    except (AttributeError, OSError):
        pass
    try:
        # 2 == PROCESS_PER_MONITOR_DPI_AWARE，兼容较旧的 Windows 版本。
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return
    except (AttributeError, OSError):
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except (AttributeError, OSError):
        pass


def enable_windows_app_id():
    """让任务栏按应用自身图标分组，而不是显示 python 解释器图标。"""
    if os.name != "nt":
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("ScreenSnap.Desktop")
    except (AttributeError, OSError) as error:
        logging.getLogger("screensnap").warning("无法设置任务栏应用标识: %s", error)


enable_windows_dpi_awareness()

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

from app.capture_flow import CaptureFlowMixin
from app.notification_flow import NotificationMixin
from app.sticker_flow import StickerFlowMixin


class Application(CaptureFlowMixin, StickerFlowMixin, NotificationMixin):
    """集中连接托盘、热键和窗口；具体业务交由各模块处理。"""

    # 日志处理器随设置重建，logger 对象本身保持不变。
    logger = logging.getLogger("screensnap")

    def __init__(self):
        self.qt = QApp(sys.argv)
        self.window_bounds_filter = WindowBoundsFilter(self.qt)
        self.qt.installEventFilter(self.window_bounds_filter)
        enable_windows_app_id()
        # 应用级图标会被设置、编辑器、贴图管理等所有未显式设置图标的窗口继承。
        self.qt.setWindowIcon(tray_icon())
        # 没有默认主窗口；关闭设置或编辑器时必须继续保持托盘常驻。
        self.qt.setQuitOnLastWindowClosed(False)
        self.config = ConfigManager(data_dir() / "settings.json")
        apply_theme(self.qt, self.config.data["theme"])
        configure_logging(self.config.data)
        if self.config.data["start_on_boot"]:
            try:
                set_start_on_boot(True)
            except OSError as error:
                self.logger.warning("无法同步开机启动设置: %s", error)
        self.hotkeys = HotkeyManager()
        # 键盘监听在工作线程执行，Qt 信号把热键动作送回 GUI 主线程。
        self.hotkeys.triggered.connect(self.dispatch)
        self.hotkeys.failed.connect(self.hotkey_error)
        self.stickers = StickerManager(self.config.data)
        # 「自动清理时机」设为启动时的话，等事件循环起来再清理，别拖慢启动。
        QTimer.singleShot(0, lambda: self.cleanup_cache_if_scheduled("start"))
        self.stickers.edit_requested.connect(self.edit_sticker)
        self.settings_window = SettingsWindow(
            self.config, self.stickers.clear_clipboard_history,
            self.stickers.clear_rebuildable_cache)
        self.settings_window.changed.connect(self.refresh)
        self.settings_window.recording.connect(self.pause_hotkeys)
        self.hotkey_recording = False
        self.tray = QSystemTrayIcon(self.qt.windowIcon(), self.qt)
        self.tray.setToolTip("ScreenSnap")
        self._notification_target_path = None
        self._notification_fallback = None
        self.notification_bridge = NotificationBridge(self)
        self.tray.messageClicked.connect(self.open_notification_target)
        self.sticker_panel = None
        self.menu = make_tray_menu(
            self.qt, lambda: self.start_capture("capture", from_tray=True), self.open_settings, self.qt.quit,
            lambda: self.dispatch("edit_clipboard"), lambda: self.dispatch("open_image"),
            self.config.data["hotkeys"],
            open_sticker=lambda: self.dispatch("open_sticker_file"),
            paste_clipboard=lambda: self.dispatch("paste_clipboard"),
            sticker_panel=self.open_sticker_panel,
            recycle_bin=self.open_recycle_bin,
        )
        self.tray.setContextMenu(self.menu)
        self.tray.activated.connect(self.tray_clicked)
        self.tray.show()
        self.editors = []
        self.recycle_window = None
        self.capture_notice = None
        # 持有顶层窗口引用，防止截图完成后窗口被 Python 提前回收。
        self.mask = None
        self.hotkeys.register(self.config.data)
        self.stickers.restore()
        self.qt.aboutToQuit.connect(self.shutdown)
        # 托盘显示后等事件循环启动，再请求系统显示就绪提示。
        QTimer.singleShot(0, self.announce_startup)


    def announce_startup(self):
        """事件循环开始后提示就绪，沿用托盘气泡总开关。"""
        self.logger.info("ScreenSnap 已启动")
        self.notify("已启动，可使用快捷键截图或右键托盘打开设置")


    def tray_clicked(self, reason):
        """仅在托盘双击时启动自由截图，右键留给菜单。"""
        if reason == QSystemTrayIcon.DoubleClick:
            self.start_capture("capture")


    def open_settings(self):
        """复用同一个设置窗口并将其带到前台。"""
        self.settings_window.show()
        self.settings_window.raise_()
        self.settings_window.activateWindow()


    def refresh(self):
        """配置落盘后更新热键、日志和已打开编辑器的标注颜色。"""
        self.stickers.refresh_selection_visuals()
        apply_theme(self.qt, self.config.data["theme"])
        configure_logging(self.config.data)
        self.menu = make_tray_menu(
            self.qt, lambda: self.start_capture("capture", from_tray=True), self.open_settings, self.qt.quit,
            lambda: self.dispatch("edit_clipboard"), lambda: self.dispatch("open_image"),
            self.config.data["hotkeys"],
            open_sticker=lambda: self.dispatch("open_sticker_file"),
            paste_clipboard=lambda: self.dispatch("paste_clipboard"),
            sticker_panel=self.open_sticker_panel,
            recycle_bin=self.open_recycle_bin,
        )
        self.tray.setContextMenu(self.menu)
        if not self.hotkey_recording:
            self.hotkeys.register(self.config.data)
        inline_editor = self.mask.session.inline_editor if isinstance(self.mask, MaskWindow) else None
        if isinstance(self.mask, MaskWindow):
            for view in self.mask.session.views:
                view.sync_quick_sticker_shortcut(self.config.data)
                view.sync_capture_save_shortcut(self.config.data)
                view.sync_capture_action_shortcuts(self.config.data)
                view.update()
        for editor in [*self.editors, *([inline_editor] if inline_editor is not None else [])]:
            for key in editor.toolbar.tool_color_buttons:
                value = self.config.data.get(key)
                if value is not None:
                    editor.settings[key] = value
                    editor.canvas.settings[key] = value
                    if key == f"{editor.canvas.tool}_color":
                        editor.canvas.set_selected_color(value)
            for key in ("rect_fill_enabled", "rect_fill_opacity", "rect_fill_color",
                        "ellipse_fill_enabled", "ellipse_fill_opacity", "ellipse_fill_color",
                        "mosaic_cursor_color", "eraser_cursor_color", "mosaic_width",
                        "mosaic_brush", "eraser_width"):
                value = self.config.data[key]
                editor.settings[key] = value
                editor.canvas.settings[key] = value
                editor.toolbar.sync_setting(key, value)
            editor.canvas.update()
            editor.canvas.refresh_tool_cursor()
            editor.canvas.viewport().update()
            editor.toolbar.sync_tool_colors(self.config.data)
            editor.toolbar.set_active_tool(editor.canvas.tool)
            editor.toolbar.sync_theme_icons()
            editor.round_corners = bool(self.config.data.get("editor_image_round_corners", True))
            editor.corner_radius = self.config.data.get("editor_image_corner_radius", 16)
            editor.canvas.set_round_corner_preview(editor.round_corners, editor.corner_radius)
            editor.toolbar.sync_appearance_controls(self.config.data)
            if editor is not inline_editor:
                editor.toolbar.crop_color.set_color(self.config.data["crop_color"])
                with QSignalBlocker(editor.toolbar.crop_width):
                    editor.toolbar.crop_width.setValue(self.config.data["crop_width"])
                editor.toolbar.crop_width_label.setText(f'{self.config.data["crop_width"]} px')
                editor.canvas.crop_color = self.config.data["crop_color"]
                editor.canvas.crop_width = self.config.data["crop_width"]
            for tool, key in TOOL_WIDTH_KEYS.items():
                editor.toolbar.tool_widths[tool] = self.config.data[key]
            for control, key in ((editor.toolbar.mosaic_size, "mosaic_size"),
                                 (editor.toolbar.marker_opacity, "marker_opacity")):
                with QSignalBlocker(control):
                    control.setValue(self.config.data[key])
            # 外部设置同步时屏蔽滑块信号，数值标签需单独刷新。
            editor.toolbar.mosaic_size_label.setText(f'{self.config.data["mosaic_size"]} px')
            editor.toolbar.marker_opacity_label.setText(f'{self.config.data["marker_opacity"]}%')
            with QSignalBlocker(editor.toolbar.font):
                editor.toolbar.font.setCurrentFont(QFont(self.config.data["font"] or "Microsoft YaHei"))
            with QSignalBlocker(editor.toolbar.font_size):
                editor.toolbar.font_size.setValue(self.config.data["font_size"])
            for key in ("mosaic_mode", "text_alignment", "arrow_style", "rect_style", "ellipse_style"):
                editor.toolbar.set_choice(key, self.config.data[key])
            editor.canvas.text_alignment = {"left": Qt.AlignLeft, "center": Qt.AlignHCenter,
                                            "right": Qt.AlignRight}[self.config.data["text_alignment"]]
            editor.toolbar.set_hotkeys(self.config.data["hotkeys"])
            tool = self.config.data["annotation_tool"]
            if editor is inline_editor and tool == "crop":
                tool = "select"
            editor.toolbar.tool_buttons[tool].setChecked(True)
            editor.canvas.set_tool(tool)
            editor.canvas.setDragMode(QGraphicsView.RubberBandDrag if tool == "select"
                                      else QGraphicsView.NoDrag)
            editor.toolbar.set_tool_mode(tool)
            with QSignalBlocker(editor.toolbar.cursor_switch):
                editor.toolbar.cursor_switch.setChecked(self.config.data["cursor"])
            editor.canvas.toggle_cursor(self.config.data["cursor"])
            editor.canvas.viewport().update()
        if self.mask is not None and self.mask.isVisible():
            for view in getattr(self.mask, "session", self.mask).views if hasattr(self.mask, "session") else [self.mask]:
                view.settings = self.config.data
            self.mask.update_all() if hasattr(self.mask, "update_all") else self.mask.update()
        self.stickers.refresh_style()
        self.logger.info("配置已更新")


    def pause_hotkeys(self, recording):
        """录制期间注销全局热键，结束后按最新配置重新注册。"""
        self.hotkey_recording = recording
        self.hotkeys.set_paused(recording)
        settings = dict(self.config.data)
        settings["hotkeys_enabled"] = False if recording else self.config.data["hotkeys_enabled"]
        self.hotkeys.register(settings)


    def hotkey_error(self, message):
        """即使关闭普通提示，也将热键注册失败报告给用户。"""
        self.logger.error("热键注册失败: %s", message)
        self.notify(f"热键注册失败: {message}")


    def dispatch(self, action):
        """在 Qt 主线程把热键动作分发给截图或贴图模块。"""
        self.logger.info("触发热键: %s", action)
        if action in ("capture", "fullscreen", "monitor", "repeat"):
            # 不在此关闭活动弹出菜单：贴图右键菜单等本程序菜单应当能被一起拍进截图；
            # 真正抓取发生在 show_mask 的 capture()，抓取后再由 show_mask 关闭残留菜单。
            self.start_capture(action)
        elif action == "paste":
            if self.stickers.paste_latest():
                item = self.stickers.active_sticker
                self.notify("已贴上次截图", "sticker_notification",
                            getattr(item, "source", None), "sticker_panel")
        elif action == "edit_clipboard":
            self.edit_clipboard_image()
        elif action == "open_image":
            self.open_and_edit_image()
        elif action in ("previous", "next"):
            self.stickers.cycle(1 if action == "previous" else -1)
        elif action == "hide":
            self.stickers.toggle_hidden()
        elif action == "close_all":
            self.stickers.close_all()
        elif action == "touch":
            self.stickers.restore_input()
        elif action == "paste_clipboard":
            self.paste_clipboard()
        elif action == "sticker_panel":
            self.open_sticker_panel()
        elif action == "open_sticker_file":
            self.stickers.open_file()
        elif action == "sticker_rotate_left":
            self.stickers.rotate_active(-90)
        elif action == "sticker_rotate_right":
            self.stickers.rotate_active(90)
        elif action == "recycle_bin":
            self.open_recycle_bin()
        else:
            self.logger.warning("未处理的热键动作: %s", action)


    def cleanup_cache_if_scheduled(self, when):
        """按「自动清理时机」在启动/退出时清理一次可重建缓存；失败只记日志不影响退出。"""
        if self.config.data.get("cache_cleanup_timing", "off") != when:
            return
        try:
            result = self.stickers.clear_rebuildable_cache()
        except (OSError, ValueError) as error:
            self.logger.warning("自动清理缓存失败(%s): %s", when, error)
            return
        self.logger.info("已按设置自动清理缓存(%s): %s", when, result)

    def shutdown(self):
        """退出前保存贴图会话并释放全局热键和托盘。"""
        # 退出前先保存贴图状态并注销系统热键，避免残留钩子和悬浮窗口。
        self.logger.info("正在退出，保存 %d 张贴图的会话", len(self.stickers.items))
        try:
            self.stickers.persist()
            self.stickers.persist_clipboard_history()
        except OSError as error:
            self.logger.error("保存贴图会话失败: %s", error, exc_info=True)
        # 会话先落盘再清理缓存，避免把本会话还引用着的贴图源文件当成孤儿删掉。
        self.cleanup_cache_if_scheduled("exit")
        self.hotkeys.stop()
        self.tray.hide()
        self.logger.info("已退出")



if __name__ == "__main__":
    instance_lock = acquire_single_instance_lock()
    if instance_lock is None:
        notify_existing_instance()
        sys.exit(0)
    install_exception_hooks()
    program = Application()
    sys.exit(program.qt.exec())