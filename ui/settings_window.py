"""设置导航容器。"""

import copy

from PySide6.QtCore import Qt, Signal, QSignalBlocker
from PySide6.QtWidgets import (QWidget, QHBoxLayout, QVBoxLayout, QListWidget, QStackedWidget,
                               QPushButton, QFileDialog, QMessageBox, QStyle, QListWidgetItem)

from ui.settings_general import GeneralPage
from ui.settings_hotkey import HotkeyPage
from ui.settings_save import SavePage
from ui.settings_editor import EditorPage
from ui.settings_log import LogPage
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
        for label, icon, method in [("导入配置", QStyle.SP_DialogOpenButton, self.import_settings),
                                    ("导出配置", QStyle.SP_DialogSaveButton, self.export_settings)]:
            button = QPushButton(label)
            button.setIcon(self.style().standardIcon(icon))
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
                                  ("日志", QStyle.SP_FileIcon, LogPage)]:
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
        self.persist()

    def set_annotation_setting(self, key, value):
        """从编辑器工具栏更改默认标注参数并写入设置文件。"""
        self.config.data[key] = value
        control = self.pages.widget(0 if key == "cursor" else 3).controls[key]
        with QSignalBlocker(control):
            if key == "cursor":
                control.setChecked(value)
            elif key in ("font", "mosaic_mode", "annotation_tool", "text_alignment", "arrow_style"):
                control.setCurrentIndex(control.findData(value))
            else:
                control.setValue(value)
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
            old = self.pages
            # 导航回调访问 self.pages，替换容器后仍指向新的页面栈。
            self.pages = QStackedWidget()
            self.layout().itemAt(0).layout().replaceWidget(old, self.pages)
            old.deleteLater()
            self.populate_pages_only()
            self.pages.setCurrentIndex(self.navigation.currentRow())
            self.changed.emit()

    def populate_pages_only(self):
        """配置导入后重建页面内容，保留原有导航分类和选中项。"""
        for page in (GeneralPage, HotkeyPage, SavePage, EditorPage, LogPage):
            self.pages.addWidget(page(self.config, self.persist, self.recording.emit)
                                 if page is HotkeyPage else page(self.config, self.persist))

    def export_settings(self):
        """把当前有效配置导出到用户选定的 JSON 路径。"""
        path, _ = QFileDialog.getSaveFileName(self, "导出配置", "settings.json", "JSON (*.json)")
        if path:
            self.config.export_to(path)