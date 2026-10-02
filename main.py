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
from PySide6.QtGui import QIcon, QPixmap, QPainter, QColor, QPen, QGuiApplication, QFont
from PySide6.QtWidgets import QApplication, QSystemTrayIcon, QGraphicsView, QFileDialog

from config import ConfigManager
from config.config_manager import TOOL_WIDTH_KEYS
from core import app_icon, data_dir, capture, qimage_to_pillow
from core.startup import set_start_on_boot
from editor import EditorWindow
from hotkey import HotkeyManager
from logger import configure_logging
from screenshot import MaskWindow
from sticker import StickerManager
from ui import SettingsWindow, make_tray_menu, CaptureNotification, StickerPanel
from ui.recycle_window import RecycleWindow
from ui.theme import apply_theme
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
            "ScreenSnap", message, QSystemTrayIcon.Information, 2500)


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


def acquire_single_instance_lock(path=None):
    """获取进程级应用锁；返回 None 表示已有实例持锁。"""
    lock_path = Path(path) if path is not None else data_dir() / "screensnap.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock = QLockFile(str(lock_path))
    return lock if lock.tryLock(0) else None


class QApp(QApplication):
    """重写 notify 捕获 Qt 事件/槽中的未处理异常并写入日志（默认会静默崩溃）。"""

    def notify(self, receiver, event):
        try:
            return super().notify(receiver, event)
        except Exception:
            logging.getLogger("screensnap").exception(
                "未捕获异常（事件类型 0x%x，接收者 %s）",
                event.type() if event is not None else -1,
                type(receiver).__name__ if receiver is not None else None)
            return False


def _log_uncaught(exc_type, exc_value, exc_traceback):
    """把未捕获异常写入 screensnap 日志；若日志尚未配置处理器，额外兜底写入文件。"""
    logger = logging.getLogger("screensnap")
    logger.exception("未捕获的全局异常", exc_info=(exc_type, exc_value, exc_traceback))
    # 日志处理器在 Application.__init__ 中才配置；在此之前崩溃也要保证写进文件。
    if not logger.handlers:
        try:
            log_dir = Path("logs") / _now_month()
            log_dir.mkdir(parents=True, exist_ok=True)
            import traceback as _tb
            with open(log_dir / "app.log", "a", encoding="utf-8") as fh:
                fh.write("\n=== 未捕获全局异常（日志未配置时的兜底写入）===\n")
                _tb.print_exception(exc_type, exc_value, exc_traceback, file=fh)
        except Exception:
            pass


def _now_month():
    from datetime import datetime
    return datetime.now().strftime("%Y-%m")


def install_exception_hooks():
    """安装全局兜底：主线程顶层异常、Qt 事件异常、子线程异常都写入日志，避免静默崩溃。"""
    sys.excepthook = _log_uncaught
    if hasattr(threading, "excepthook"):
        _orig = threading.excepthook

        def _thread_hook(args):
            try:
                _log_uncaught(args.exc_type, args.exc_value, args.exc_traceback)
            finally:
                _orig(args)

        threading.excepthook = _thread_hook


class Application:
    """集中连接托盘、热键和窗口；具体业务交由各模块处理。"""

    # 日志处理器随设置重建，logger 对象本身保持不变。
    logger = logging.getLogger("screensnap")

    def __init__(self):
        self.qt = QApp(sys.argv)
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
        self.stickers.edit_requested.connect(self.edit_sticker)
        self.settings_window = SettingsWindow(
            self.config, self.stickers.clear_clipboard_history)
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
                view.update()
        for editor in [*self.editors, *([inline_editor] if inline_editor is not None else [])]:
            for key in editor.toolbar.tool_color_buttons:
                value = self.config.data.get(key)
                if value is not None:
                    editor.settings[key] = value
                    editor.canvas.settings[key] = value
                    if key == f"{editor.canvas.tool}_color":
                        editor.canvas.set_selected_color(value)
            editor.canvas.update()
            editor.toolbar.sync_tool_colors(self.config.data)
            editor.toolbar.set_active_tool(editor.canvas.tool)
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

    def edit_clipboard_image(self):
        """读取系统剪贴板中的图像并交给截图编辑器。"""
        image = QGuiApplication.clipboard().image()
        if image.isNull():
            self.logger.debug("剪贴板中没有图片，无法打开编辑器")
            self.notify("剪贴板中没有图片；文字、颜色或文件可用“贴剪贴板内容”直接贴出")
            return
        try:
            source = qimage_to_pillow(image)
        except (OSError, ValueError) as error:
            self.logger.warning("读取剪贴板图片失败: %s", error)
            self.notify(f"无法读取剪贴板图片: {error}")
            return
        self.logger.info("编辑剪贴板图片: %sx%s", image.width(), image.height())
        self.edit_images([(source, None)], from_capture=False)

    def open_and_edit_image(self):
        """多选磁盘图片并分别打开截图编辑器。"""
        paths, _ = QFileDialog.getOpenFileNames(
            None, "打开并编辑图片", "",
            "图片文件 (*.png *.jpg *.jpeg *.bmp *.gif *.webp *.tif *.tiff)",
        )
        if not paths:
            return
        images = []
        failed = []
        for path in paths:
            try:
                with Image.open(path) as opened:
                    images.append((opened.convert("RGBA").copy(), None))
            except (OSError, ValueError) as error:
                self.logger.warning("打开图片失败 %s: %s", path, error)
                failed.append(path)
        if failed:
            self.notify(f"无法打开 {len(failed)} 张图片")
        if not images:
            return
        self.logger.info("多选打开图片: %d 张", len(images))
        self.edit_images(images, from_capture=False)

    def start_capture(self, mode, from_tray=False, initial_rect=None, preferred_monitor=None):
        """跳过已显示的遮罩；仅托盘菜单发起的截图等待菜单关闭。"""
        if self.mask is not None and self.mask.isVisible():
            self.logger.debug("遮罩已显示，忽略截图请求: %s", mode)
            return
        if mode == "repeat" and not self.config.data["last_capture_rect"]:
            self.logger.debug("尚无上次截图区域，跳过重复截图")
            self.notify("暂无上次截图区域")
            return
        # 托盘菜单发起时稍后捕获，避免菜单进入截图；用户设置的延迟在此基础上累加。
        delay = int(self.config.data.get("capture_delay", 0) or 0)
        waiting = (150 if from_tray else 0) + delay
        if delay:
            self.logger.info("截图延迟 %d 毫秒后显示遮罩: %s", delay, mode)
        QTimer.singleShot(waiting, lambda: self.show_mask(mode, initial_rect, preferred_monitor))

    def show_mask(self, mode, initial_rect=None, preferred_monitor=None):
        """保存两版画面并显示覆盖虚拟桌面的选区遮罩。"""
        # 同一时刻保留带光标和不带光标的画面，供编辑器临时切换。
        image, bounds, monitors, alternate = capture(self.config.data["cursor"], alternatives=True)
        # 抓取完成后再关闭本程序残留的活动弹出菜单（如贴图右键菜单），
        # 让它留在冻结画面里，又不会继续悬浮在遮罩之上。
        popup = QApplication.activePopupWidget()
        if popup is not None and popup is not getattr(self, "menu", None):
            popup.close()
        if mode == "repeat":
            rect = QRect(*self.config.data["last_capture_rect"]).intersected(
                QRect(bounds["left"], bounds["top"], bounds["width"], bounds["height"]))
            if rect.isEmpty():
                self.logger.warning("上次截图区域已不在屏幕范围内: %s",
                                    self.config.data["last_capture_rect"])
                self.notify("上次截图区域已不在当前屏幕")
                return
            left, top = rect.x() - bounds["left"], rect.y() - bounds["top"]
            crop = (left, top, left + rect.width(), top + rect.height())
            self.edit_images([(image.crop(crop), alternate.crop(crop) if alternate else None)])
            return
        self.mask = MaskWindow(image, bounds, monitors, self.config.data, mode, alternate,
                       initial_rect=initial_rect, preferred_monitor=preferred_monitor)
        self.mask.setAttribute(Qt.WA_DeleteOnClose)
        self.mask.last_region.connect(self.remember_region)
        if hasattr(self, "settings_window"):
            self.mask.annotation_setting_changed.connect(self.settings_window.set_annotation_setting)
            self.mask.tool_color_changed.connect(self.settings_window.set_tool_color)
        for view in self.mask.session.views:
            view.selected.connect(self.handle_capture_selection)
            view.edit_requested.connect(self.edit_images)
            view.save_requested.connect(self.save_capture_images)
            view.copy_done.connect(self.notify_capture_copied)
            view.picker_copied.connect(self.notify_color_picked)
            view.image_saved.connect(self.saved)
            view.image_saved_silently.connect(
                lambda path, image: self.saved(path, image, notify=False))
            view.save_failed.connect(self.initial_save_failed)
            view.recapture_requested.connect(self.restart_capture)
        self._connect_sticker_signals(self.mask.session.views)
        self.mask.close_all_requested.connect(self.close_all_editors)
        self.mask.destroyed.connect(lambda obj=None, current=self.mask: setattr(self, "mask", None)
                                    if self.mask is current else None)
        self.mask.show()
        self.mask.raise_()
        self.mask.activateWindow()
        self.mask.setFocus(Qt.ActiveWindowFocusReason)
        QTimer.singleShot(0, self._activate_capture_mask)
        for view in self.mask.session.views:
            view.magnifier_overlay.sync()
        self.logger.info("开始截图: %s", mode)

    def _connect_sticker_signals(self, views):
        for view in views:
            view.sticker_requested.connect(self.add_sticker)
            view.quick_sticker_requested.connect(self.add_sticker)

    def _activate_capture_mask(self):
        """显示完成后再次请求 Windows 将键盘焦点交给截图遮罩。"""
        if not isinstance(self.mask, MaskWindow) or not self.mask.isVisible():
            return
        self.mask.raise_()
        window = self.mask.windowHandle()
        if window is not None:
            window.requestActivate()
        self.mask.activateWindow()
        self.mask.setFocus(Qt.ActiveWindowFocusReason)

    def remember_region(self, rect):
        self.config.data["last_capture_rect"] = rect
        self.config.save()

    def edit_images(self, images, positions=None, from_capture=True):
        """为每个选区打开独立编辑器，并连接保存与贴图输出。"""
        # 每个选区对应独立编辑器，保持多选区之间的编辑状态互不影响。
        capture_editor = None
        for index, (image, alternate) in enumerate(images):
            editor = EditorWindow(image, self.config.data, alternate,
                                  from_capture=from_capture)
            editor.sticker_position = positions[index] if positions and index < len(positions) else None
            if from_capture and capture_editor is None:
                capture_editor = editor
            editor.initial_capture_save_pending = bool(from_capture)
            editor.image_saved.connect(
                lambda path, saved_image, current=editor:
                self.editor_saved(current, path, saved_image))

            def add_editor_sticker(result, current=editor):
                position = current.sticker_position
                if position is None:
                    frame = current.frameGeometry()
                    screen = current.screen().availableGeometry()
                    right = frame.right() + 12
                    left = frame.left() - result.width() - 12
                    if right + result.width() <= screen.right() + 1:
                        x = right
                    elif left >= screen.left():
                        x = left
                    else:
                        x = screen.left() + 12
                    y = max(screen.top(), min(
                        frame.top() + 36,
                        screen.bottom() - result.height() + 1))
                    position = QPoint(x, y)
                self.add_sticker(result, position)

            editor.sticker_requested.connect(add_editor_sticker)
            editor.recapture_requested.connect(lambda current=editor: self.restart_capture(current))
            editor.close_all_requested.connect(self.close_all_editors)
            editor.status.connect(self.notify)
            editor.tool_color_changed.connect(self.settings_window.set_tool_color)
            editor.setting_changed.connect(self.settings_window.set_annotation_setting)
            editor.destroyed.connect(lambda obj=None, current=editor: self.editors.remove(current) if current in self.editors else None)
            editor.setAttribute(Qt.WA_DeleteOnClose)
            self.editors.append(editor)
            editor.show()
            if from_capture:
                editor.initial_save_timer = QTimer(editor)
                editor.initial_save_timer.setSingleShot(True)
                editor.initial_save_timer.timeout.connect(
                    lambda current=editor: self.save_initial_capture(current))
                editor.initial_save_timer.start(0)
        if from_capture and images and self.config.data["bubble"] and self.config.data["capture_notification"]:
            if self.capture_notice is not None:
                self.capture_notice.close()
            self.capture_notice = CaptureNotification(
                images[0][0], len(images),
                backend=self.config.data["notification_backend"])
            self.capture_notice.file_activated.connect(self.open_notification_target)
            if capture_editor is not None:
                capture_editor.capture_notification = self.capture_notice
            self.capture_notice.show_preview()
        if from_capture and self.config.data["sound"]:
            QApplication.beep()
        self.logger.info("完成截图: %s 张" if from_capture else "打开图片编辑: %s 张", len(images))

    def save_initial_capture(self, editor):
        """窗口显示后再做初始落盘，避免双击等待图片编码和文件写入。"""
        try:
            editor.save(automatic=True)
        except OSError as error:
            editor.initial_capture_save_pending = False
            self.initial_save_failed(f"初始保存失败: {error}")

    def editor_saved(self, editor, path, image=None):
        """首次自动落盘由截图预览说明，不再额外替换一次成功图片通知。"""
        notify = (not editor.initial_capture_save_pending and
              not getattr(editor, "suppress_save_notification", False))
        capture_notification = getattr(editor, "capture_notification", None)
        if capture_notification is not None:
            capture_notification.set_target_path(path)
        editor.initial_capture_save_pending = False
        self.saved(path, image, notify=notify)

    def handle_capture_selection(self, images, positions=None):
        """按用户默认偏好保存截图，或打开编辑器继续处理。"""
        if self.config.data.get("capture_after_selection", "save") == "edit":
            self.edit_images(images, positions=positions)
        else:
            self.save_capture_images(images)

    def notify_capture_copied(self, image=None):
        """仅复制模式：选区已写入剪贴板，发送带缩略图的图片提示。"""
        if image is None:
            self.notify("已复制到剪贴板", "capture_notification")
            return
        if self.config.data["capture_notification"]:
            # 复制反馈的核心是“看到复制了什么”，强制本地预览以保证缩略图一定可见；
            # 不依赖系统 Toast 是否能可靠附带图片（Win11 Toast 的 hero 图有时不显示）。
            notice = CaptureNotification(image, title="已复制到剪贴板", backend="legacy")
            notice.file_activated.connect(self.open_notification_target)
            if self.capture_notice is not None:
                self.capture_notice.close()
            self.capture_notice = notice
            notice.show_preview()
        else:
            self.notify("已复制到剪贴板", "capture_notification")

    def notify_color_picked(self, color):
        """取色模式：色值已复制到剪贴板，发一次轻量提示。"""
        self.notify(f"已复制颜色 {color}", "capture_notification")

    def save_capture_images(self, images, positions=None):
        """直接保存选区图片，但沿用编辑器最终效果与手动保存配置。"""
        for image, alternate in images:
            editor = EditorWindow(image, self.config.data, alternate, from_capture=True)
            editor.image_saved.connect(self.saved)
            try:
                editor.save(automatic=True, copy_to_clipboard=True)
            except OSError as error:
                self.initial_save_failed(f"直接保存截图失败: {error}")
            finally:
                editor.close()

    def initial_save_failed(self, message):
        self.logger.exception("%s", message)
        self.notify(message)

    def close_all_editors(self):
        """关闭当前全部截图编辑器窗口。"""
        for editor in list(self.editors):
            editor.close()

    def restart_capture(self, context=None):
        """关闭当前编辑窗口/遮罩后重新进入实时截图选区。"""
        if isinstance(context, dict):
            preferred_monitor = context.get("monitor")
        else:
            preferred_monitor = None
        editor = None if isinstance(context, dict) else context
        if editor is not None:
            editor.close()
        if self.mask is not None:
            self.mask.close()
        QTimer.singleShot(0, lambda: self.start_capture(
            "capture", initial_rect=None, preferred_monitor=preferred_monitor))

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

    def saved(self, path, image=None, notify=True):
        """记录保存结果并按保存通知设置显示提示。"""
        self.logger.info("图片已保存: %s", path)
        if notify and image is not None and self.config.data["bubble"] and self.config.data["save_notification"]:
            self.show_image_notice(image, "图片已保存", str(path), path)
        elif notify and image is None:
            self.notify(f"图片已保存: {path}", "save_notification", path)
        if notify and image is not None and self.config.data["sound"]:
            QApplication.beep()

    def show_image_notice(self, image, title, detail="", target_path=None):
        """新的图片提示替换旧提示，避免多个窗口堆叠。"""
        if self.capture_notice is not None:
            self.capture_notice.close()
        self.capture_notice = CaptureNotification(image, title=title, detail=detail,
                              target_path=target_path,
                              backend=self.config.data["notification_backend"])
        self.capture_notice.file_activated.connect(self.open_notification_target)
        self.capture_notice.show_preview()

    def notify(self, message, setting=None, target_path=None, fallback=None):
        """总开关与分类开关控制气泡；音效仍由音效设置独立控制。"""
        if self.config.data["bubble"] and (setting is None or self.config.data[setting]):
            self._notification_target_path = str(target_path) if target_path else None
            self._notification_fallback = fallback
            if self.config.data["notification_backend"] == "win11toast":
                from ui.native_toast import show_native_toast

                target = (self._notification_target_path, fallback)
                bridge = self.notification_bridge
                sent = show_native_toast(
                    "ScreenSnap", message,
                    on_click=lambda *args: bridge.activated.emit(target),
                    on_failed=lambda error: bridge.failed.emit((message, target)))
                if not sent:
                    self.tray.showMessage(
                        "ScreenSnap", message, QSystemTrayIcon.Information, 2500)
            else:
                self.tray.showMessage(
                    "ScreenSnap", message, QSystemTrayIcon.Information, 2500)
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

    def shutdown(self):
        """退出前保存贴图会话并释放全局热键和托盘。"""
        # 退出前先保存贴图状态并注销系统热键，避免残留钩子和悬浮窗口。
        self.logger.info("正在退出，保存 %d 张贴图的会话", len(self.stickers.items))
        try:
            self.stickers.persist()
            self.stickers.persist_clipboard_history()
        except OSError as error:
            self.logger.error("保存贴图会话失败: %s", error, exc_info=True)
        self.hotkeys.stop()
        self.tray.hide()
        self.logger.info("已退出")


if __name__ == "__main__":
    instance_lock = acquire_single_instance_lock()
    if instance_lock is None:
        sys.exit(0)
    install_exception_hooks()
    program = Application()
    sys.exit(program.qt.exec())