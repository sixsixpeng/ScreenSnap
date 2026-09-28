"""设置导航容器。"""

import copy
import logging

from PySide6.QtCore import Qt, Signal, QSignalBlocker
from PySide6.QtWidgets import (QWidget, QHBoxLayout, QVBoxLayout, QListWidget, QStackedWidget,
                               QPushButton, QFileDialog, QMessageBox, QStyle, QListWidgetItem)

from config.config_manager import DEFAULTS
from core.path_utils import data_dir
from ui.settings_general import GeneralPage
from ui.settings_hotkey import HotkeyPage
from ui.settings_save import SavePage
from ui.settings_editor import EditorPage
from ui.settings_log import LogPage
from ui.settings_sticker import StickerPage
from ui.settings_clipboard import ClipboardPage
from core.startup import set_start_on_boot


class SettingsWindow(QWidget):
    """仅负责设置分类的导航、配置保存与 JSON 导入导出。"""

    changed = Signal()
    recording = Signal(bool)

    def __init__(self, config):
        super().__init__()
        self.config = config
        self._saved_start_on_boot = config.data["start_on_boot"]
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
        }
        for label, icon, method in [("导入配置", QStyle.SP_DialogOpenButton, self.import_settings),
                                    ("导出配置", QStyle.SP_DialogSaveButton, self.export_settings),
                                    ("恢复全部默认", QStyle.SP_DialogResetButton, self.reset_defaults),
                                    ("清理贴图会话", QStyle.SP_DialogDiscardButton,
                                     self.clear_sticker_session)]:
            button = QPushButton(label)
            button.setIcon(self.style().standardIcon(icon))
            button.setToolTip(hints[label])
            button.clicked.connect(method)
            buttons.addWidget(button)
        buttons.addStretch()
        outer.addLayout(buttons)

    def populate(self):
        """将每个设置分类注册为独立页面。"""
        for title, icon, page in [("常规", QStyle.SP_DesktopIcon, GeneralPage),
                                  ("快捷键", QStyle.SP_ComputerIcon, HotkeyPage),
                                  ("保存", QStyle.SP_DialogSaveButton, SavePage),
                                  ("编辑器", QStyle.SP_FileDialogDetailedView, EditorPage),
                                  ("日志", QStyle.SP_FileIcon, LogPage),
                                  ("贴图", QStyle.SP_DesktopIcon, StickerPage),
                                  ("剪贴板贴图", QStyle.SP_FileDialogContentsView, ClipboardPage)]:
            self.navigation.addItem(QListWidgetItem(self.style().standardIcon(icon), title))
            self.pages.addWidget(page(self.config, self.persist, self.recording.emit)
                                 if page is HotkeyPage else page(self.config, self.persist))

    def persist(self):
        """仅在保存成功后通知托盘程序刷新热键和日志设置。"""
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

    def set_pen_color(self, color):
        """编辑器改色后同步设置页按钮并立即保存默认画笔颜色。"""
        self.config.data["pen_color"] = color
        editor_page = self.pages.widget(3)
        editor_page.color_buttons["pen_color"].set_color(color)
        editor_page.refresh_previews()
        self.persist()

    def set_annotation_setting(self, key, value):
        """从编辑器工具栏更改默认标注参数并写入设置文件。"""
        self.config.data[key] = value
        page = self.pages.widget(0 if key == "cursor" else 3)
        control = page.color_buttons[key] if key == "crop_color" else page.controls[key]
        with QSignalBlocker(control):
            if key == "crop_color":
                control.set_color(value)
            elif key == "cursor":
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

    def populate_pages_only(self):
        """配置导入后重建页面内容，保留原有导航分类和选中项。"""
        for page in (GeneralPage, HotkeyPage, SavePage, EditorPage, LogPage, StickerPage, ClipboardPage):
            self.pages.addWidget(page(self.config, self.persist, self.recording.emit)
                                 if page is HotkeyPage else page(self.config, self.persist))

    def export_settings(self):
        """把当前有效配置导出到用户选定的 JSON 路径。"""
        path, _ = QFileDialog.getSaveFileName(self, "导出配置", "settings.json", "JSON (*.json)")
        if path:
            self.config.export_to(path)