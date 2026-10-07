"""贴图回收站窗口：展示并管理已关闭进入回收站的贴图。"""

from pathlib import Path

from PySide6.QtCore import Qt, QSize
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                               QPushButton, QScrollArea, QMessageBox, QStyle)

from ui.action_icons import action_icon
from ui.widgets.confirmation import yes_no_dialog


class RecycleRow(QWidget):
    """回收站中的一行：缩略图、名称与恢复/删除操作。"""

    def __init__(self, item, window):
        super().__init__()
        self.item = item
        self.window = window
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        width, height = 96, 72
        thumb = QLabel()
        thumb.setFixedSize(QSize(width, height))
        thumb.setAlignment(Qt.AlignCenter)
        thumb.setPixmap(item.thumbnail(width, height))
        layout.addWidget(thumb)
        name = Path(item.source).name if item.source else "临时图片"
        info = QLabel(f"{name}\n{item.pixmap.width()}×{item.pixmap.height()}")
        info.setWordWrap(True)
        layout.addWidget(info, 1)
        restore = QPushButton("恢复")
        restore.setIcon(action_icon("sticker"))
        restore.clicked.connect(self.restore)
        layout.addWidget(restore)
        delete = QPushButton("删除")
        delete.setIcon(self.style().standardIcon(QStyle.SP_DialogDiscardButton))
        delete.clicked.connect(self.delete)
        layout.addWidget(delete)

    def restore(self):
        self.window.manager.recycle_restore(self.item)
        self.window.refresh()

    def delete(self):
        self.window.manager.recycle_delete(self.item)
        self.window.refresh()


class RecycleWindow(QWidget):
    """管理已关闭进入回收站的贴图：单张恢复/删除，或一键清空。"""

    def __init__(self, manager, settings, parent=None):
        super().__init__(parent)
        self.manager = manager
        self.settings = settings
        self.setWindowTitle("贴图回收站")
        self.setMinimumSize(440, 340)
        layout = QVBoxLayout(self)
        toolbar = QHBoxLayout()
        refresh = QPushButton("刷新")
        refresh.clicked.connect(self.refresh)
        clear = QPushButton("清空回收站")
        clear.setIcon(self.style().standardIcon(QStyle.SP_DialogDiscardButton))
        clear.clicked.connect(self.clear_all)
        toolbar.addWidget(refresh)
        toolbar.addStretch(1)
        toolbar.addWidget(clear)
        layout.addLayout(toolbar)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.container = QWidget()
        self.list_layout = QVBoxLayout(self.container)
        self.list_layout.setContentsMargins(0, 0, 0, 0)
        self.scroll.setWidget(self.container)
        layout.addWidget(self.scroll, 1)
        self.empty_label = QLabel("回收站为空")
        self.empty_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.empty_label)
        # 回收内容变化（如超出上限自动清理）且窗口可见时自动刷新列表。
        manager.changed.connect(lambda: self.refresh() if self.isVisible() else None)
        self.refresh()

    def refresh(self):
        """依据回收站当前内容重建列表。"""
        while self.list_layout.count():
            child = self.list_layout.takeAt(0).widget()
            if child is not None:
                child.deleteLater()
        items = self.manager.recycle_items()
        self.empty_label.setVisible(not items)
        for item in items:
            self.list_layout.addWidget(RecycleRow(item, self))
        self.list_layout.addStretch(1)

    def clear_all(self):
        if not self.manager.recycle_items():
            return
        answer = yes_no_dialog(
            self, "清空回收站", "确定要彻底删除回收站中的全部贴图吗？此操作不可恢复。"
        ).exec()
        if answer == QMessageBox.Yes:
            self.manager.empty_recycle()
            self.refresh()

    def showEvent(self, event):
        self.refresh()
        super().showEvent(event)
