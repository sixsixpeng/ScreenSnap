"""常驻托盘的程序入口。"""

import ctypes
import logging
import os
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
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
    except (AttributeError, OSError) as error:
        logging.getLogger("screensnap").warning("无法设置任务栏应用标识: %s", error)


enable_windows_dpi_awareness()

from PySide6.QtCore import Qt, QPoint, QRect, QTimer, QSignalBlocker, QObject, Signal, Slot, QLockFile
from PySide6.QtGui import QIcon, QPixmap, QPainter, QColor, QPen, QGuiApplication, QFont, QCursor
from PySide6.QtWidgets import QApplication, QSystemTrayIcon, QGraphicsView, QFileDialog, QMessageBox

from config import ConfigManager
from config.config_manager import TOOL_WIDTH_KEYS
from core import app_icon, data_dir, capture, qimage_to_pillow
from core.constants import APP_USER_MODEL_ID
from core.version import APP_VERSION
from core.dpi import DisplayMapper
from core.path_utils import clear_data_directory
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
        # 先装日志、再做版本闸门：闸门要写「跳过被占用文件」「已清空数据目录」这类 WARNING/INFO，
        # 若日志尚未配置，它们只会走 logging 的兜底 handler（仅 stderr、无格式、且不进日志文件）。
        configure_logging(self.config.data)
        # 版本闸门：配置里记录的版本与内置版本不一致时，先清空用户数据再启动。
        self.version_reset_from = ""
        if self.apply_version_gate():
            # 重置后配置已回到默认（保留项除外），日志开关/等级/目录可能变化，重新应用一次。
            configure_logging(self.config.data)
        # 显示器分辨率/缩放/增减变化时刷新坐标缓存与贴图（中间屏被改动的情形）。
        self.watch_screens()
        apply_theme(self.qt, self.config.data["theme"])
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
        self.reset_user_data_requested = False
        # 「自动清理时机」设为启动时的话，等事件循环起来再清理，别拖慢启动。
        QTimer.singleShot(0, lambda: self.cleanup_cache_if_scheduled("start"))
        self.stickers.edit_requested.connect(self.edit_sticker)
        self.settings_window = SettingsWindow(
            self.config, self.stickers.clear_clipboard_history,
            self.stickers.clear_rebuildable_cache,
            reset_all_user_data=self.request_full_user_data_reset)
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
            self.qt, lambda: self.start_capture("capture", from_tray=True), self.open_settings, self.quit_app,
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


    def desktop_available_rect(self):
        """所有显示器可用区域的并集（Qt 原生坐标）：用于判断贴图是否落在已拔掉的屏上。"""
        application = QGuiApplication.instance()
        if application is None:
            return QRect()
        rect = QRect()
        for screen in application.screens():
            rect = rect.united(screen.availableGeometry())
        return rect

    def watch_screens(self):
        """给每块显示器挂上分辨率/缩放变化信号（新接入的屏也要挂，否则收不到后续变化）。"""
        application = QGuiApplication.instance()
        if application is None:
            return
        watched = getattr(self, "_watched_screens", None)
        if watched is None:
            watched = self._watched_screens = set()
            application.screenAdded.connect(self.handle_screen_change)
            application.screenRemoved.connect(self.handle_screen_change)
        for screen in application.screens():
            if id(screen) in watched:
                continue
            watched.add(id(screen))
            screen.geometryChanged.connect(self.handle_screen_change)
            screen.logicalDotsPerInchChanged.connect(self.handle_screen_change)
            screen.availableGeometryChanged.connect(self.handle_screen_change)

    def handle_screen_change(self, *args):
        """显示器分辨率/缩放/增减变化后刷新坐标缓存与贴图尺寸。

        中间某块屏改了分辨率或缩放时，屏幕映射缓存与贴图的 dpr 都可能失效：这里清掉映射缓存、
        让每张贴图按新 dpr 重新对齐窗口尺寸与绘制比例，并给新接入的屏补挂信号。
        进行中的截图遮罩不做迁移（选区与预览都是按当时分辨率抓的），由用户重新截图。
        """
        from core.screen_mapping import clear_cache as clear_mapping_cache

        clear_mapping_cache()
        desktop = self.desktop_available_rect()
        refreshed = 0
        rescued = 0
        for item in list(self.stickers.items):
            try:
                item.apply_style()
                refreshed += 1
                # 显示器被拔掉后，原来落在它上面的贴图坐标已不存在：搬回剩余桌面，
                # 否则贴图还在会话里却永远看不见（用户会以为丢了）。
                frame = item.frameGeometry()
                if not desktop.isNull() and not desktop.intersects(frame):
                    item.move(min(max(frame.left(), desktop.left()),
                                   desktop.right() - frame.width() + 1),
                              min(max(frame.top(), desktop.top()),
                                  desktop.bottom() - frame.height() + 1))
                    rescued += 1
            except RuntimeError:  # 贴图对象可能已被 Qt 回收
                continue
        self.watch_screens()
        self.logger.info("检测到显示器变化：已清空屏幕映射缓存、刷新 %d 张贴图、搬回 %d 张超出桌面的贴图",
                         refreshed, rescued)

    def apply_version_gate(self):
        """版本变化时清空用户数据目录，返回这次是否发生了重置。

        旧版本的配置、贴图会话、剪贴板历史与缓存可能和新版本不兼容，所以按「先删数据再启动」
        处理：删除后立刻用 DEFAULTS 重建配置并写入内置版本号，重置结果由 announce_startup 通知用户。
        记录为空表示首次运行（或刚重置过），只写入内置版本、不删数据。
        """
        stored = str(self.config.data.get("app_version") or "")
        if not stored:
            # 首次运行：把内置版本写进配置，之后版本一致就正常启动。
            self.config.data["app_version"] = APP_VERSION
            try:
                self.config.save()
            except OSError as error:
                self.logger.error("首次运行写入版本号失败: %s", error, exc_info=True)
            return False
        if stored == APP_VERSION:
            return False
        import copy

        from config.config_manager import DEFAULTS, PRESERVED_ON_VERSION_RESET
        from core.path_utils import clear_data_directory

        removed = None
        try:
            removed = clear_data_directory()
        except (OSError, ValueError) as error:
            # 目录整体删不掉（权限/被杀软锁定）时不能直接返回：那样版本号永远对不上，
            # 每次启动都会重试并报错。这里继续重建配置，残留文件由用户手动清理。
            self.logger.error("版本变化后清理用户数据目录失败，仍继续重建配置: %s", error, exc_info=True)
        kept = {key: copy.deepcopy(self.config.data[key])
                for key in PRESERVED_ON_VERSION_RESET if key in self.config.data}
        if removed is None:
            self.logger.warning("检测到版本变化 %s → %s，用户数据目录未能完全清理，"
                                "已按新版本重建配置并保留 %d 项设置", stored, APP_VERSION, len(kept))
        else:
            self.logger.warning("检测到版本变化 %s → %s，已清空用户数据目录 %s（保留 %d 项设置）",
                                stored, APP_VERSION, removed, len(kept))
        self.config.data.clear()
        self.config.data.update(copy.deepcopy(DEFAULTS))
        self.config.data.update(kept)
        self.config.data["app_version"] = APP_VERSION
        try:
            self.config.save()
        except OSError as error:
            # 写盘失败不能让启动崩掉：数据目录可能不可写（只读盘/权限/杀软）。
            self.logger.error("版本重置后写入配置失败: %s", error, exc_info=True)
        self.version_reset_from = stored
        return True

    def announce_startup(self):
        """事件循环开始后提示就绪，沿用托盘气泡总开关。"""
        self.logger.info("ScreenSnap 已启动")
        if getattr(self, "version_reset_from", ""):
            # 版本变化导致数据被清空时必须明确告知，避免用户以为设置/贴图丢了。
            self.notify(f"版本已更新（{self.version_reset_from} → {APP_VERSION}），用户数据已重置")
            return
        restarted = os.environ.get("SCREENSNAP_RESTARTED", "")
        if restarted:
            # 看护进程在异常退出后拉起本进程时设置的标记：只提示一次，且优先于普通启动提示。
            self.logger.warning("崩溃后自动重启（上次退出码=%s）", restarted)
            self.notify(f"ScreenSnap 上次异常退出（code={restarted}），已自动重启")
            return
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


    def request_full_user_data_reset(self):
        """停止当前实例，待持久化任务结束后由入口删除用户数据。"""
        if self.reset_user_data_requested:
            return True
        try:
            set_start_on_boot(False)
        except OSError as error:
            self.logger.error("彻底重置时无法关闭开机启动: %s", error, exc_info=True)
            QMessageBox.warning(self.settings_window, "无法清空全部数据",
                                f"关闭 Windows 开机启动项失败，未执行清理：\n{error}")
            return False
        self.reset_user_data_requested = True
        self.logger.warning("用户请求清空全部数据并退出: %s", data_dir())
        self.qt.quit()
        return True


    def refresh(self):
        """配置落盘后更新热键、日志和已打开编辑器的标注颜色。"""
        self.stickers.refresh_selection_visuals()
        apply_theme(self.qt, self.config.data["theme"])
        configure_logging(self.config.data)
        self.menu = make_tray_menu(
            self.qt, lambda: self.start_capture("capture", from_tray=True), self.open_settings, self.quit_app,
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

    def quit_app(self):
        """托盘“退出”入口：先标记正在退出，再结束事件循环。

        Qt 在退出时会关闭所有顶层窗口，贴图窗口的 closeEvent 会走 manager.remove()，
        若不加这个标记，会话会在 shutdown() 保存之前被清空（表现为“退出时贴图在屏幕上，
        重启却不恢复”，2026-10-11 真机反馈，日志已证实）。
        """
        try:
            self.stickers.quitting = True
        except Exception:  # noqa: BLE001 退出路径绝不能因清理失败而卡住
            pass
        self.qt.quit()

    def shutdown(self):
        """退出前保存贴图会话并释放全局热键和托盘。"""
        if self.reset_user_data_requested:
            self.logger.info("清空全部数据：跳过贴图会话和剪贴板历史写回")
            self.stickers.stop_pending_persistence()
            self.hotkeys.stop()
            self.tray.hide()
            return
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


def delete_user_data_after_shutdown(instance_lock):
    """释放当前实例后删除用户数据目录，不重建配置或启动新进程。"""
    instance_lock.unlock()
    logging.shutdown()
    try:
        clear_data_directory()
    except Exception as error:
        message = f"ScreenSnap 用户数据删除失败：\n{error}"
        if os.name == "nt":
            try:
                ctypes.windll.user32.MessageBoxW(None, message, "ScreenSnap 删除失败", 0x10)
            except (AttributeError, OSError):
                print(message, file=sys.stderr)
        else:
            print(message, file=sys.stderr)
        return False
    return True



def _crash_test_fault():
    """仅供 --crash-test 使用：制造一次真实的原生访问违规（0xC0000005），"""
    """用来验证「崩溃 → faulthandler 落盘 → 看护 1 秒后自动重启」这条链路。"""
    logging.getLogger("screensnap").warning("崩溃复现开关触发：即将制造访问违规（--crash-test）")
    # 注意：ctypes.string_at(0) 在本环境会被 Python 转成 OSError 并被
    # bootstrap 的未捕获异常钩子接住 —— 进程不死，看护也就不会重启（实测）。
    # faulthandler._sigsegv() 是真正的段错误，不可被 Python 捕获，进程直接终止。
    import faulthandler
    _sigsegv = getattr(faulthandler, "_sigsegv", None)
    if _sigsegv is not None:
        _sigsegv()
    else:
        import os
        os.abort()


if __name__ == "__main__":
    # --crash-test N：只让“首次启动”崩一次。必须在交棒给看护**之前**解析并把参数摘掉，
    # 否则看护会把 --crash-test 原样传给每个子进程 —— 表现为无限连续重启（实测）。
    # 秒数改用环境变量传递；看护在重启子进程时会清掉它（supervisor.py）。
    if "--crash-test" in sys.argv:
        _index = sys.argv.index("--crash-test")
        try:
            _delay = float(sys.argv[_index + 1])
            del sys.argv[_index:_index + 2]
        except (IndexError, ValueError):
            _delay = 5.0
            del sys.argv[_index]
        os.environ["SCREENSNAP_CRASH_TEST"] = str(_delay)
    # 正常启动也带崩溃自动重启：首次启动把控制权交给看护循环（supervisor.main），
    # 由它把本程序作为子进程运行并在异常退出后重启。已由看护启动（SUPERVISED=1）
    # 或设置 SCREENSNAP_NO_WATCHDOG=1 时直接运行，避免递归。
    if (os.environ.get("SCREENSNAP_SUPERVISED") != "1"
            and os.environ.get("SCREENSNAP_NO_WATCHDOG") != "1"):
        import supervisor
        sys.exit(supervisor.main(sys.argv[1:]))
    instance_lock = acquire_single_instance_lock()
    if instance_lock is None:
        notify_existing_instance()
        sys.exit(0)
    install_exception_hooks()
    program = Application()
    if "--crash-test" in sys.argv:
        # 例：python main.py --crash-test 8  —— 8 秒后制造一次真实原生崩溃。
        _index = sys.argv.index("--crash-test")
        try:
            _delay = float(sys.argv[_index + 1])
        except (IndexError, ValueError):
            _delay = 5.0
            if "--crash-test" in sys.argv:
                del sys.argv[sys.argv.index("--crash-test")]
        # 只在首次启动崩一次：把秒数转成环境变量、把参数从 argv 摘掉，
        # 这样看护重启出来的子进程不会再崩（否则会连续重启，实测过）。
        os.environ["SCREENSNAP_CRASH_TEST"] = str(_delay)
        del sys.argv[_index:_index + 2]
    _crash_delay = os.environ.get("SCREENSNAP_CRASH_TEST")
    if _crash_delay:
        QTimer.singleShot(int(float(_crash_delay) * 1000), _crash_test_fault)
    exit_code = program.qt.exec()
    if program.reset_user_data_requested:
        if not delete_user_data_after_shutdown(instance_lock):
            sys.exit(1)
        sys.exit(0)
    sys.exit(exit_code)