"""截图流程：抓屏、遮罩、选区结果分发、编辑器打开与保存。"""

import ctypes
import logging
import os
import subprocess
import sys
import threading
from pathlib import Path
import shiboken6
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


class CaptureFlowMixin:
    """截图流程：抓屏、遮罩、选区结果分发、编辑器打开与保存。"""

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


    def complete_preset_capture(self, mode, image, bounds, monitors, alternate,
                                preferred_monitor=None, initial_rect=None):
        """全屏 / 当前显示器：不显示遮罩，直接按“截图后”设置保存或进入编辑器。"""
        mapper = DisplayMapper(bounds, monitors)
        full = QRect(bounds["left"], bounds["top"], bounds["width"], bounds["height"])
        if initial_rect:
            region = QRect(*initial_rect).intersected(full)
        elif mode == "fullscreen":
            region = full
        else:
            if preferred_monitor:
                target = QPoint(preferred_monitor["left"] + preferred_monitor["width"] // 2,
                                preferred_monitor["top"] + preferred_monitor["height"] // 2)
            else:
                target = mapper.native_global_to_physical_global(QCursor.pos())
            monitor = next((screen for screen in monitors if
                            screen["left"] <= target.x() < screen["left"] + screen["width"] and
                            screen["top"] <= target.y() < screen["top"] + screen["height"]),
                           monitors[0] if monitors else bounds)
            region = QRect(monitor["left"], monitor["top"], monitor["width"], monitor["height"])
        if region.isEmpty():
            self.logger.warning("预设截图区域无效: mode=%s", mode)
            return
        local = region.translated(-bounds["left"], -bounds["top"])
        area = (local.x(), local.y(), local.x() + local.width(), local.y() + local.height())
        images = [(image.crop(area), alternate.crop(area) if alternate else None)]
        position = mapper.physical_local_to_native_global(local.topLeft())
        self.logger.info("预设截图直接完成: mode=%s 区域=%s", mode, tuple(region.getRect()))
        self.handle_capture_selection(images, [position], mode=mode)


    def popup_intruders(self):
        """抓屏瞬间仍在屏幕上的本程序弹出菜单，返回 [(中文名, 屏幕矩形)]。

        这类菜单抓屏之后才关闭（避免它悬浮在遮罩之上），因此像素已经进了冻结帧，
        而那时它已不在窗口列表里、枚举不到；只有抓屏前记下的矩形能让遮罩在选区覆盖
        它时给出警示。托盘菜单自身有 150 毫秒等待，抓屏时通常已关闭，故不重复报告。
        """
        popup = QApplication.activePopupWidget()
        if popup is None or popup is getattr(self, "menu", None):
            return []
        rect = popup.frameGeometry()
        if rect.width() <= 0 or rect.height() <= 0:
            return []
        return [(window_label(popup), rect)]


    def show_mask(self, mode, initial_rect=None, preferred_monitor=None):
        """保存两版画面并显示覆盖虚拟桌面的选区遮罩。

        抓屏前**不动**本程序的任何窗口：用户常拿程序自身的贴图/设置/编辑器当内容来
        测截图，抓屏就是抓屏；哪些自身窗口进了画面只通过遮罩的提示条告知
        （`MaskWindow.self_check_warning`），不替用户关窗口。
        """
        # 同一时刻保留带光标和不带光标的画面，供编辑器临时切换。
        image, bounds, monitors, alternate = capture(
            self.config.data["cursor"], alternatives=True,
            gap_fill=self.config.data.get("capture_gap_fill", "transparent"))
        # 抓取完成后再关闭本程序残留的活动弹出菜单（如贴图右键菜单），
        # 让它留在冻结画面里，又不会继续悬浮在遮罩之上；关闭前先记下它的位置，
        # 好在选区覆盖它时补一句"已进入画面"的警示。
        popup_intruders = self.popup_intruders()
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
            images = [(image.crop(crop), alternate.crop(crop) if alternate else None)]
            if self.config.data.get("capture_repeat_action", "save") == "save":
                self.save_capture_images(images)
            else:
                self.edit_images(images)
            return
        if mode in ("fullscreen", "monitor"):
            # 全屏/当前显示器是按整块屏固定的预设区域，没有可框选内容；以前会先弹出遮罩
            # 再靠 QTimer 自动完成，视觉上闪一下、还卡一帧。这里直接裁好按设置交出去。
            self.complete_preset_capture(mode, image, bounds, monitors, alternate,
                                         preferred_monitor, initial_rect)
            return
        self.mask = MaskWindow(image, bounds, monitors, self.config.data, mode, alternate,
                       initial_rect=initial_rect, preferred_monitor=preferred_monitor,
                       extra_intruders=popup_intruders)
        self.mask.setAttribute(Qt.WA_DeleteOnClose)
        if hasattr(self, "settings_window"):
            self.mask.annotation_setting_changed.connect(self.settings_window.set_annotation_setting)
            self.mask.tool_color_changed.connect(self.settings_window.set_tool_color)
        for view in self.mask.session.views:
            view.last_region.connect(self.remember_region)
            view.selected.connect(
                lambda images, positions, source=view.mode:
                self.handle_capture_selection(images, positions, mode=source))
            view.edit_requested.connect(self.edit_images)
            view.save_requested.connect(self.save_capture_images)
            view.copy_done.connect(self.notify_capture_copied)
            view.save_failed.connect(self.initial_save_failed)
            view.save_as_dir_chosen.connect(self.remember_save_as_dir)
            view.picker_copied.connect(self.notify_color_picked)
            view.image_saved.connect(self.saved)
            view.image_saved_silently.connect(
                lambda path, image: self.saved(path, image, notify=False))
            view.recapture_requested.connect(self.restart_capture)
        self._connect_sticker_signals(self.mask.session.views)
        self.mask.close_all_requested.connect(self.close_all_editors)
        self.mask.destroyed.connect(lambda obj=None, current=self.mask: setattr(self, "mask", None)
                                    if self.mask is current else None)
        self.mask.show()
        self.mask.raise_()
        self.mask.activateWindow()
        if self.mask.auto_complete_after_show:
            mask = self.mask
            QTimer.singleShot(0, lambda: mask.complete() if shiboken6.isValid(mask) and mask.isVisible() else None)
        self.mask.setFocus(Qt.ActiveWindowFocusReason)
        QTimer.singleShot(0, self._activate_capture_mask)
        for view in self.mask.session.views:
            view.magnifier_overlay.sync()
        self.logger.info("开始截图: %s", mode)


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
        self.logger.debug("记住上次截图区域: %s", rect)


    def edit_images(self, images, positions=None, from_capture=True):
        """为每个选区打开独立编辑器，并连接保存与贴图输出。"""
        # 每个选区对应独立编辑器，保持多选区之间的编辑状态互不影响。
        for index, (image, alternate) in enumerate(images):
            # 先取交接槽，再做留痕 —— 先前留痕写在取槽之前，pending 还没赋值就被引用，
            # 一按 E 就抛 UnboundLocalError、编辑器根本打不开（2026-10-11 真机日志定位）。
            from screenshot.mask_window import take_pending_editor_annotations

            pending = take_pending_editor_annotations()
            logging.getLogger("screensnap").info(
                "新建编辑器：光标图=%s 尺寸=%s 说明=%s",
                "有" if alternate is not None else "无",
                getattr(alternate, "size", None),
                "来自原地编辑转交" if pending else "本次截图")
            logging.getLogger("screensnap").info(
                "edit_images 收到交接快照：%d 条（index=%d）", len(pending), index)
            editor = EditorWindow(image, self.config.data, alternate,
                                  from_capture=from_capture)
            # 从原地编辑器按 E 转交过来的标注：以对象形式恢复，保证在窗口编辑里仍可选中/编辑
            # （2026-10-11 用户反馈：以前只交合成图，标注变成像素后无法再编辑）。
            if pending and index == 0:
                try:
                    editor.canvas.restore(pending)
                except Exception as error:  # noqa: BLE001 恢复失败不影响打开编辑器
                    logging.getLogger("screensnap").warning(
                        "恢复转交过来的标注失败: %s", error)
                else:
                    logging.getLogger("screensnap").info(
                        "已从原地编辑器恢复 %d 个标注对象", len(pending))
            editor.sticker_position = positions[index] if positions and index < len(positions) else None
            # 编辑器必须出现在"被截取区域所在的那块屏"：Qt 默认把新窗口放在主屏居中，
            # 于是副屏划选、鼠标留在主屏时编辑器开在主屏，用户看到的就是"按 E 没反应"。
            anchor = editor.sticker_position
            if anchor is not None:
                screen = QGuiApplication.screenAt(anchor) or QGuiApplication.primaryScreen()
                if screen is not None:
                    area = screen.availableGeometry()
                    frame = editor.frameGeometry()
                    frame.moveCenter(anchor)
                    editor.move(
                        min(max(frame.left(), area.left()), area.right() - frame.width() + 1),
                        min(max(frame.top(), area.top()), area.bottom() - frame.height() + 1))
                    logging.getLogger("screensnap").info(
                        "编辑器定位到选区所在屏: 锚点=(%d,%d) 屏幕=%s 窗口=%s",
                        anchor.x(), anchor.y(), area.getRect(), editor.geometry().getRect())
            editor.copy_done.connect(self.notify_capture_copied)
            editor.save_as_dir_chosen.connect(self.remember_save_as_dir)
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
        if from_capture and self.config.data["sound"]:
            QApplication.beep()
        self.logger.info("完成截图: %s 张" if from_capture else "打开图片编辑: %s 张", len(images))


    def editor_saved(self, editor, path, image=None):
        """只处理用户显式保存或贴图触发的保存。"""
        notify = not getattr(editor, "suppress_save_notification", False)
        self.saved(path, image, notify=notify, notification_setting="save_notification")


    def handle_capture_selection(self, images, positions=None, mode=None):
        """按用户默认偏好保存截图，或打开编辑器继续处理。"""
        action_key = {"fullscreen": "capture_fullscreen_action",
                      "monitor": "capture_monitor_action",
                      "repeat": "capture_repeat_action"}.get(mode)
        action = (self.config.data.get(action_key, "save") if action_key else
                  self.config.data.get("capture_after_selection", "save"))
        if action == "edit":
            self.edit_images(images, positions=positions)
        else:
            self.save_capture_images(images)


    def save_capture_images(self, images, positions=None, notify=True, copy_to_clipboard=True):
        """直接保存选区图片，但沿用编辑器最终效果与手动保存配置。"""
        for image, alternate in images:
            editor = EditorWindow(image, self.config.data, alternate, from_capture=True)
            editor.copy_done.connect(self.notify_capture_copied)
            editor.save_as_dir_chosen.connect(self.remember_save_as_dir)
            # notify=False 时仍记录日志与历史，只是不弹保存提示（快速贴图只要贴图那一条提示）。
            editor.image_saved.connect(
                lambda path, image, flag=notify: self.saved(path, image, notify=flag))
            try:
                editor.save(automatic=True, copy_to_clipboard=copy_to_clipboard)
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

