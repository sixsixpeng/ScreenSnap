"""常驻托盘的程序入口。"""

import ctypes
import os
import sys


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


enable_windows_dpi_awareness()

from PySide6.QtCore import Qt, QRect, QTimer, QSignalBlocker
from PySide6.QtGui import QIcon, QPixmap, QPainter, QColor, QPen, QGuiApplication, QFont
from PySide6.QtWidgets import QApplication, QSystemTrayIcon, QGraphicsView, QFileDialog

from config import ConfigManager
from config.config_manager import TOOL_WIDTH_KEYS
from core import data_dir, capture, qimage_to_pillow
from core.startup import set_start_on_boot
from editor import EditorWindow
from hotkey import HotkeyManager
from logger import configure_logging
from screenshot import MaskWindow
from sticker import StickerManager
from ui import SettingsWindow, make_tray_menu, CaptureNotification
from ui.widgets.tooltip import TOOLTIP_STYLE
from PIL import Image


def tray_icon():
    """现场绘制高分辨率取景框图标，缩放后依然能辨认截图入口。"""
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


class Application:
    """集中连接托盘、热键和窗口；具体业务交由各模块处理。"""

    def __init__(self):
        self.qt = QApplication(sys.argv)
        self.qt.setWindowIcon(tray_icon())
        # 没有默认主窗口；关闭设置或编辑器时必须继续保持托盘常驻。
        self.qt.setQuitOnLastWindowClosed(False)
        self.qt.setStyleSheet(TOOLTIP_STYLE)
        self.config = ConfigManager(data_dir() / "settings.json")
        self.logger = configure_logging(self.config.data)
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
        self.settings_window = SettingsWindow(self.config)
        self.settings_window.changed.connect(self.refresh)
        self.settings_window.recording.connect(self.pause_hotkeys)
        self.hotkey_recording = False
        self.tray = QSystemTrayIcon(self.qt.windowIcon(), self.qt)
        self.tray.setToolTip("ScreenSnap")
        self.menu = make_tray_menu(
            self.qt, lambda: self.start_capture("capture", from_tray=True), self.open_settings, self.qt.quit,
            lambda: self.dispatch("edit_clipboard"), lambda: self.dispatch("open_image"),
            self.config.data["hotkeys"],
            open_sticker=self.stickers.open_file,
        )
        self.tray.setContextMenu(self.menu)
        self.tray.activated.connect(self.tray_clicked)
        self.tray.show()
        self.editors = []
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
        self.logger = configure_logging(self.config.data)
        self.menu = make_tray_menu(
            self.qt, lambda: self.start_capture("capture", from_tray=True), self.open_settings, self.qt.quit,
            lambda: self.dispatch("edit_clipboard"), lambda: self.dispatch("open_image"),
            self.config.data["hotkeys"],
        )
        self.tray.setContextMenu(self.menu)
        if not self.hotkey_recording:
            self.hotkeys.register(self.config.data)
        inline_editor = self.mask.session.inline_editor if isinstance(self.mask, MaskWindow) else None
        for editor in [*self.editors, *([inline_editor] if inline_editor is not None else [])]:
            editor.toolbar.pen_color.set_color(self.config.data["pen_color"])
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
            editor.canvas.tool = tool
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
        self.tray.showMessage("热键注册失败", message)

    def dispatch(self, action):
        """在 Qt 主线程把热键动作分发给截图或贴图模块。"""
        self.logger.info("触发热键: %s", action)
        if action in ("capture", "fullscreen", "monitor", "repeat"):
            self.start_capture(action)
        elif action == "paste":
            if self.stickers.paste_latest():
                self.notify("已贴上次截图", "sticker_notification")
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

    def edit_clipboard_image(self):
        """读取系统剪贴板中的图像并交给截图编辑器。"""
        image = QGuiApplication.clipboard().image()
        if image.isNull():
            self.notify("剪贴板中没有图片")
            return
        try:
            source = qimage_to_pillow(image)
        except (OSError, ValueError) as error:
            self.notify(f"无法读取剪贴板图片: {error}")
            return
        self.edit_images([(source, None)], from_capture=False)

    def open_and_edit_image(self):
        """选择磁盘图片并在截图编辑器中打开。"""
        path, _ = QFileDialog.getOpenFileName(
            None, "打开并编辑图片", "",
            "图片文件 (*.png *.jpg *.jpeg *.bmp *.gif *.webp *.tif *.tiff)",
        )
        if not path:
            return
        try:
            with Image.open(path) as opened:
                source = opened.convert("RGBA").copy()
        except (OSError, ValueError) as error:
            self.notify(f"无法打开图片: {error}")
            return
        self.edit_images([(source, None)], from_capture=False)

    def start_capture(self, mode, from_tray=False):
        """跳过已显示的遮罩；仅托盘菜单发起的截图等待菜单关闭。"""
        if self.mask is not None and self.mask.isVisible():
            return
        if mode == "repeat" and not self.config.data["last_capture_rect"]:
            self.notify("暂无上次截图区域")
            return
        # 托盘菜单发起时稍后捕获，避免菜单进入截图。
        QTimer.singleShot(150 if from_tray else 0, lambda: self.show_mask(mode))

    def show_mask(self, mode):
        """保存两版画面并显示覆盖虚拟桌面的选区遮罩。"""
        # 同一时刻保留带光标和不带光标的画面，供编辑器临时切换。
        image, bounds, monitors, alternate = capture(self.config.data["cursor"], alternatives=True)
        if mode == "repeat":
            rect = QRect(*self.config.data["last_capture_rect"]).intersected(
                QRect(bounds["left"], bounds["top"], bounds["width"], bounds["height"]))
            if rect.isEmpty():
                self.notify("上次截图区域已不在当前屏幕")
                return
            left, top = rect.x() - bounds["left"], rect.y() - bounds["top"]
            crop = (left, top, left + rect.width(), top + rect.height())
            self.edit_images([(image.crop(crop), alternate.crop(crop) if alternate else None)])
            return
        self.mask = MaskWindow(image, bounds, monitors, self.config.data, mode, alternate)
        self.mask.setAttribute(Qt.WA_DeleteOnClose)
        self.mask.last_region.connect(self.remember_region)
        if hasattr(self, "settings_window"):
            self.mask.annotation_setting_changed.connect(self.settings_window.set_annotation_setting)
            self.mask.pen_color_changed.connect(self.settings_window.set_pen_color)
        self.mask.selected.connect(self.edit_images)
        self.mask.image_saved.connect(self.saved)
        self.mask.save_failed.connect(self.initial_save_failed)
        self.mask.close_all_requested.connect(self.close_all_editors)
        self.mask.sticker_requested.connect(self.add_sticker)
        self.mask.destroyed.connect(lambda obj=None, current=self.mask: setattr(self, "mask", None)
                                    if self.mask is current else None)
        self.mask.show()
        self.mask.raise_()
        self.mask.activateWindow()
        self.mask.setFocus()
        for view in self.mask.session.views:
            view.magnifier_overlay.sync()
        self.logger.info("开始截图: %s", mode)

    def remember_region(self, rect):
        self.config.data["last_capture_rect"] = rect
        self.config.save()

    def edit_images(self, images, from_capture=True):
        """为每个选区打开独立编辑器，并连接保存与贴图输出。"""
        # 每个选区对应独立编辑器，保持多选区之间的编辑状态互不影响。
        for image, alternate in images:
            editor = EditorWindow(image, self.config.data, alternate)
            editor.image_saved.connect(self.saved)
            editor.sticker_requested.connect(self.add_sticker)
            editor.close_all_requested.connect(self.close_all_editors)
            editor.status.connect(self.notify)
            editor.pen_color_changed.connect(self.settings_window.set_pen_color)
            editor.setting_changed.connect(self.settings_window.set_annotation_setting)
            editor.destroyed.connect(lambda obj=None, current=editor: self.editors.remove(current) if current in self.editors else None)
            editor.setAttribute(Qt.WA_DeleteOnClose)
            self.editors.append(editor)
            if from_capture:
                try:
                    editor.save(automatic=True)
                except OSError as error:
                    self.initial_save_failed(f"初始保存失败: {error}")
            editor.show()
        if from_capture and images and self.config.data["bubble"] and self.config.data["capture_notification"]:
            if self.capture_notice is not None:
                self.capture_notice.close()
            self.capture_notice = CaptureNotification(images[0][0], len(images))
            self.capture_notice.show_preview()
        if from_capture and self.config.data["sound"]:
            QApplication.beep()
        self.logger.info("完成截图: %s 张" if from_capture else "打开图片编辑: %s 张", len(images))

    def initial_save_failed(self, message):
        self.logger.exception("%s", message)
        self.notify(message)

    def close_all_editors(self):
        """关闭当前全部截图编辑器窗口。"""
        for editor in list(self.editors):
            editor.close()

    def add_sticker(self, image):
        """创建新贴图并根据独立设置决定是否显示托盘提示。"""
        self.stickers.add(image)
        self.notify("已创建贴图", "sticker_notification")

    def saved(self, path, image=None):
        """记录保存结果并按保存通知设置显示提示。"""
        self.logger.info("图片已保存: %s", path)
        if image is not None and self.config.data["bubble"] and self.config.data["save_notification"]:
            self.show_image_notice(image, "图片已保存", str(path))
        elif image is None:
            self.notify(f"图片已保存: {path}", "save_notification")
        if image is not None and self.config.data["sound"]:
            QApplication.beep()

    def show_image_notice(self, image, title, detail=""):
        """新的图片提示替换旧提示，避免多个窗口堆叠。"""
        if self.capture_notice is not None:
            self.capture_notice.close()
        self.capture_notice = CaptureNotification(image, title=title, detail=detail)
        self.capture_notice.show_preview()

    def notify(self, message, setting=None):
        """总开关与分类开关控制气泡；音效仍由音效设置独立控制。"""
        if self.config.data["bubble"] and (setting is None or self.config.data[setting]):
            self.tray.showMessage("ScreenSnap", message, QSystemTrayIcon.Information, 2500)
        if self.config.data["sound"]:
            QApplication.beep()

    def shutdown(self):
        """退出前保存贴图会话并释放全局热键和托盘。"""
        # 退出前先保存贴图状态并注销系统热键，避免残留钩子和悬浮窗口。
        self.stickers.persist()
        self.hotkeys.stop()
        self.tray.hide()


if __name__ == "__main__":
    program = Application()
    sys.exit(program.qt.exec())