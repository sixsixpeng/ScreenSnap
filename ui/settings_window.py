"""设置导航容器。"""

import copy
import logging

from PySide6.QtCore import Signal, QSignalBlocker, QTimer
from PySide6.QtWidgets import (QApplication, QWidget, QHBoxLayout, QVBoxLayout, QListWidget, QStackedWidget,
                               QPushButton, QFileDialog, QMessageBox, QStyle, QListWidgetItem)

from config.config_manager import DEFAULTS
from core.path_utils import data_dir
from ui.settings_general import GeneralPage
from ui.settings_screenshot import ScreenshotPage
from ui.settings_appearance import AppearancePage
from ui.settings_hotkey import HotkeyPage
from ui.settings_save import SaveOutputPage
from ui.settings_editor import EditorPage
from ui.settings_log import LogPage
from ui.settings_sticker import StickerPage
from ui.settings_clipboard import ClipboardPage
from core.startup import set_start_on_boot
from ui.action_icons import action_icon


class SettingsWindow(QWidget):
    """仅负责设置分类的导航、配置保存与 JSON 导入导出。"""

    changed = Signal()
    recording = Signal(bool)

    def __init__(self, config, clear_clipboard_history=None):
        super().__init__()
        self.config = config
        self.clear_clipboard_history_callback = clear_clipboard_history
        self._saved_start_on_boot = config.data["start_on_boot"]
        self._persist_timer = QTimer(self)
        self._persist_timer.setSingleShot(True)
        self._persist_timer.setInterval(250)
        self._persist_timer.timeout.connect(self._write_pending)
        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self.flush_persist)
        self.setWindowTitle("ScreenSnap · 设置")
        self.resize(760, 570)
        outer = QVBoxLayout(self)
        row = QHBoxLayout()
        self.navigation = QListWidget()
        self.navigation.setFixedWidth(130)
        self.pages = QStackedWidget()
        row.addWidget(self.navigation)
        row.addWidget(self.pages)
        outer.addLayout(row)
        self.populate()
        self.navigation.currentRowChanged.connect(lambda index: self.pages.setCurrentIndex(index))
        self.navigation.setCurrentRow(0)
        buttons = QHBoxLayout()
        hints = {
            "导入配置": "从 JSON 文件导入设置，导入后立即重建界面并刷新热键",
            "导出配置": "把当前生效的设置导出成 JSON 文件，便于备份或换机",
            "恢复全部默认": "最终手段：先把当前设置备份成同目录下的 settings.bak，再把所有页的选项改回默认值；\n"
                            "只想重置某一类设置时，用该页底部的“恢复本页默认”",
            "清理贴图会话": "删除贴图会话文件 stickers.json 与贴图私有缓存，下次启动不再恢复贴图；\n"
                            "当前已打开的贴图不受影响，退出时仍会重新保存会话",
            "清空剪贴板历史": "清空本次运行期间捕获的剪贴板内容；已创建的贴图不会受影响",
        }
        for label, icon, method in [("导入配置", QStyle.SP_DialogOpenButton, self.import_settings),
                                    ("恢复全部默认", QStyle.SP_DialogResetButton, self.reset_defaults),
                                    ("清理贴图会话", QStyle.SP_DialogDiscardButton,
                                     self.clear_sticker_session)]:
            button = QPushButton(label)
            button.setIcon(self.style().standardIcon(icon))
            button.setToolTip(hints[label])
            button.clicked.connect(method)
            buttons.addWidget(button)
        clipboard_history_button = QPushButton("清空剪贴板历史")
        clipboard_history_button.setIcon(self.style().standardIcon(QStyle.SP_DialogDiscardButton))
        clipboard_history_button.setToolTip(hints["清空剪贴板历史"])
        clipboard_history_button.setEnabled(clear_clipboard_history is not None)
        clipboard_history_button.clicked.connect(self.clear_clipboard_history)
        buttons.addWidget(clipboard_history_button)
        buttons.addStretch()
        outer.addLayout(buttons)

    def populate(self):
        """将每个设置分类注册为独立页面。"""
        for title, icon, page in [("常规", QStyle.SP_DesktopIcon, GeneralPage),
                      ("快捷键", QStyle.SP_ComputerIcon, HotkeyPage),
                      ("截图", QStyle.SP_DesktopIcon, ScreenshotPage),
                      ("编辑器", QStyle.SP_FileDialogDetailedView, EditorPage),
                      ("贴图", QStyle.SP_DesktopIcon, StickerPage),
                      ("剪贴板贴图", QStyle.SP_FileDialogContentsView, ClipboardPage),
                      ("主题与外观", QStyle.SP_ComputerIcon, AppearancePage),
                      ("保存与输出", QStyle.SP_DialogSaveButton, SaveOutputPage),
                      ("日志", QStyle.SP_FileIcon, LogPage)]:
            custom_icon = {"截图": "camera", "主题与外观": "settings",
                           "保存与输出": "output", "日志": "logs",
                           "贴图": "sticker"}.get(title)
            icon = action_icon(custom_icon) if custom_icon else self.style().standardIcon(icon)
            self.navigation.addItem(QListWidgetItem(icon, title))
            self.pages.addWidget(page(self.config, self.persist, self.recording.emit)
                                 if page is HotkeyPage else page(self.config, self.persist))

    def persist(self):
        """合并短时间内的设置变化，避免滑块连续操作反复写入整份 JSON。"""
        if self.config.data["start_on_boot"] != self._saved_start_on_boot:
            self._persist_timer.stop()
            self._write_pending()
            return
        self._persist_timer.start()

    def _write_pending(self):
        """持久化当前最后一版配置，并在成功后刷新运行中的应用设置。"""
        previous = self._saved_start_on_boot
        changed = self.config.data["start_on_boot"] != previous
        try:
            if changed:
                set_start_on_boot(self.config.data["start_on_boot"])
            self.config.save()
            self._saved_start_on_boot = self.config.data["start_on_boot"]
            self.changed.emit()
        except (ValueError, OSError) as error:
            if changed:
                try:
                    set_start_on_boot(previous)
                except OSError:
                    pass
                self.config.data["start_on_boot"] = previous
                control = self.pages.widget(0).controls["start_on_boot"]
                with QSignalBlocker(control):
                    control.setChecked(previous)
            QMessageBox.warning(self, "设置未保存", str(error))

    def flush_persist(self):
        """关闭设置页或退出应用前写入尚未落盘的最后一版配置。"""
        if not self._persist_timer.isActive():
            return True
        self._persist_timer.stop()
        self._write_pending()
        return True

    def closeEvent(self, event):
        self.flush_persist()
        super().closeEvent(event)

    def set_pen_color(self, color):
        """编辑器改色后同步设置页按钮并立即保存默认画笔颜色。"""
        self.config.data["pen_color"] = color
        editor_page = self.page("编辑器")
        editor_page.color_buttons["pen_color"].set_color(color)
        editor_page.refresh_previews()
        self.persist()

    def set_tool_color(self, tool, color):
        """从编辑器同步单个标注工具颜色并保存设置。"""
        key = "pen_color" if tool == "pen" else f"{tool}_color"
        page = self.page("编辑器")
        if key not in page.color_buttons:
            logging.getLogger("screensnap").warning("编辑器报告未知工具颜色: %s", tool)
            return
        self.config.data[key] = color
        page.color_buttons[key].set_color(color)
        page.refresh_previews()
        logging.getLogger("screensnap").debug("同步标注工具颜色: tool=%s color=%s", tool, color)
        self.persist()

    def set_annotation_setting(self, key, value):
        """从编辑器工具栏更改默认标注参数并写入设置文件。"""
        self.config.data[key] = value
        if key == "cursor":
            page = self.page("截图")
        elif key.startswith("editor_image_"):
            page = self.page("保存与输出")
        else:
            page = self.page("编辑器")
        control = page.color_buttons[key] if key in page.color_buttons else page.controls[key]
        with QSignalBlocker(control):
            if key in page.color_buttons:
                control.set_color(value)
            elif isinstance(value, bool):
                control.setChecked(value)
            elif key in ("font", "mosaic_mode", "annotation_tool", "text_alignment", "arrow_style",
                         "rect_style", "ellipse_style"):
                control.setCurrentIndex(control.findData(value))
            else:
                control.setValue(value)
        page.refresh_previews()
        self.persist()

    def import_settings(self):
        """导入后重建页面控件，避免显示仍停留在导入前的值。"""
        path, _ = QFileDialog.getOpenFileName(self, "导入配置", "", "JSON (*.json)")
        if path:
            self._persist_timer.stop()
            previous = copy.deepcopy(self.config.data)
            try:
                self.config.import_from(path)
                set_start_on_boot(self.config.data["start_on_boot"])
            except (ValueError, OSError) as error:
                self.config.data.clear()
                self.config.data.update(previous)
                self.config.save()
                QMessageBox.warning(self, "导入失败", str(error))
                return
            self._saved_start_on_boot = self.config.data["start_on_boot"]
            self.rebuild_pages()

    def rebuild_pages(self):
        """替换页面容器并按当前配置重建所有分类页面，随后通知程序刷新。"""
        old = self.pages
        # 导航回调访问 self.pages，替换容器后仍指向新的页面栈。
        self.pages = QStackedWidget()
        self.layout().itemAt(0).layout().replaceWidget(old, self.pages)
        old.deleteLater()
        self.populate_pages_only()
        self.pages.setCurrentIndex(self.navigation.currentRow())
        self.changed.emit()

    def reset_defaults(self):
        """先备份当前配置，再把所有设置恢复成默认值并重建界面。"""
        backup = self.config.path.with_suffix(".bak")
        if QMessageBox.question(
                self, "恢复默认设置",
                f"当前设置会先备份到：\n{backup}\n然后全部恢复为默认值。是否继续？",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        self._persist_timer.stop()
        try:
            if self.config.path.exists():
                backup.write_bytes(self.config.path.read_bytes())
            self.config.data.clear()
            self.config.data.update(copy.deepcopy(DEFAULTS))
            set_start_on_boot(self.config.data["start_on_boot"])
            self.config.save()
        except (OSError, ValueError) as error:
            logging.getLogger("screensnap").error("恢复默认设置失败: %s", error)
            QMessageBox.warning(self, "恢复默认设置失败", str(error))
            return
        self._saved_start_on_boot = self.config.data["start_on_boot"]
        self.rebuild_pages()
        logging.getLogger("screensnap").info("已恢复默认设置，原配置备份到 %s", backup)
        QMessageBox.information(self, "已恢复默认设置", f"原配置已备份到：\n{backup}")

    def clear_sticker_session(self):
        """删除贴图会话与私有缓存，方便从空白状态验证贴图功能。"""
        session = data_dir() / "stickers.json"
        cache = data_dir() / "sticker_cache"
        if QMessageBox.question(
                self, "清理贴图会话",
                "删除贴图会话文件与贴图私有缓存，下次启动不再恢复贴图。\n"
                "当前已打开的贴图不受影响，退出时仍会重新保存。是否继续？",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        removed = 0
        try:
            if session.exists():
                session.unlink()
                removed += 1
            for file in cache.glob("sticker_*.png"):
                file.unlink()
                removed += 1
        except OSError as error:
            logging.getLogger("screensnap").warning("清理贴图会话失败: %s", error)
            QMessageBox.warning(self, "清理未完成", str(error))
            return
        logging.getLogger("screensnap").info("清理贴图会话: 删除 %d 个文件", removed)
        QMessageBox.information(self, "已清理贴图会话", f"共删除 {removed} 个文件。")

    def clear_clipboard_history(self):
        """确认后清空 StickerManager 持有的内存剪贴板历史。"""
        callback = self.clear_clipboard_history_callback
        if callback is None:
            return
        if QMessageBox.question(
                self, "清空剪贴板历史",
                "清空本次运行期间捕获的剪贴板历史？已创建的贴图不会受影响。",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        count = callback()
        QMessageBox.information(self, "已清空剪贴板历史", f"共清空 {count} 条记录。")

    def populate_pages_only(self):
        """配置导入后重建页面内容，保留原有导航分类和选中项。"""
        for page in (GeneralPage, HotkeyPage, ScreenshotPage, EditorPage, StickerPage,
                 ClipboardPage, AppearancePage, SaveOutputPage, LogPage):
            self.pages.addWidget(page(self.config, self.persist, self.recording.emit)
                                 if page is HotkeyPage else page(self.config, self.persist))

    def page(self, title):
        """按导航分类标题查找设置页，避免页面顺序变化破坏同步回调。"""
        for index in range(self.navigation.count()):
            if self.navigation.item(index).text() == title:
                return self.pages.widget(index)
        raise KeyError(f"设置分类不存在: {title}")

    def export_settings(self):
        """把当前有效配置导出到用户选定的 JSON 路径。"""
        path, _ = QFileDialog.getSaveFileName(self, "导出配置", "settings.json", "JSON (*.json)")
        if path:
            self.config.export_to(path)