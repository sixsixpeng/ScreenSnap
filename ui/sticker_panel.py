"""贴图管理窗口。"""

from PySide6.QtCore import Qt, QSize
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QListWidget,
                               QListWidgetItem, QLabel, QPushButton, QStyle)


class StickerRow(QWidget):
    """管理窗口中的一行：缩略图、说明和常用操作。"""

    def __init__(self, sticker, panel):
        super().__init__()
        self.sticker = sticker
        self.panel = panel
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        width = panel.thumb_width()
        height = width * 3 // 4
        thumb = QLabel()
        thumb.setFixedSize(QSize(width, height))
        thumb.setAlignment(Qt.AlignCenter)
        thumb.setPixmap(sticker.thumbnail(width, height))
        layout.addWidget(thumb)
        size = sticker.pixmap.size()
        info = QLabel(f"{sticker.describe()}\n{size.width()}×{size.height()} · ({sticker.x()}, {sticker.y()})")
        info.setWordWrap(True)
        layout.addWidget(info, 1)
        for label, icon, callback in [("定位", QStyle.SP_ArrowRight, self.locate),
                                      ("显示" if not sticker.isVisible() else "隐藏",
                                       QStyle.SP_TitleBarMinButton, self.toggle),
                                      ("关闭", QStyle.SP_TitleBarCloseButton, self.close_sticker)]:
            button = QPushButton(label)
            button.setIcon(self.style().standardIcon(icon))
            button.setFixedWidth(72)
            button.clicked.connect(callback)
            layout.addWidget(button)

    def locate(self):
        self.panel.locate(self.panel.index_of(self.sticker))

    def toggle(self):
        self.sticker.setVisible(not self.sticker.isVisible())
        self.panel.refresh()

    def close_sticker(self):
        self.sticker.close()


class StickerPanel(QWidget):
    """列出当前所有贴图，支持定位、隐藏与关闭。"""

    def __init__(self, manager):
        super().__init__()
        self.manager = manager
        self.setWindowTitle("ScreenSnap · 贴图管理")
        self.resize(540, 460)
        layout = QVBoxLayout(self)
        buttons = QHBoxLayout()
        self.summary = QLabel("共 0 张贴图")
        buttons.addWidget(self.summary)
        buttons.addStretch()
        for label, icon, method in [("刷新", QStyle.SP_BrowserReload, self.refresh),
                                    ("隐藏/显示", QStyle.SP_DesktopIcon, self.manager.toggle_hidden),
                                    ("关闭全部", QStyle.SP_DialogCloseButton, self.manager.close_all)]:
            button = QPushButton(label)
            button.setIcon(self.style().standardIcon(icon))
            button.clicked.connect(method)
            buttons.addWidget(button)
        layout.addLayout(buttons)
        self.list_widget = QListWidget()
        self.list_widget.setSpacing(2)
        self.list_widget.itemDoubleClicked.connect(lambda item: self.locate(item.data(Qt.UserRole)))
        layout.addWidget(self.list_widget, 1)
        self.stickers = []
        self.manager.changed.connect(self.refresh_if_visible)
        self.refresh()

    def refresh_if_visible(self):
        """只在窗口打开时刷新，避免后台频繁重建列表。"""
        if self.isVisible():
            self.refresh()

    def thumb_width(self):
        """缩略图宽度取自贴图设置，高度按 4:3 推导。"""
        return int((self.manager.settings or {}).get("sticker_panel_thumb", 96))

    def refresh(self):
        """重建列表内容；贴图数量变化时同步标题统计。"""
        self.list_widget.clear()
        self.stickers = list(self.manager.items)
        for index, sticker in enumerate(self.stickers):
            item = QListWidgetItem(self.list_widget)
            # 只保存序号，避免贴图被销毁后 Qt 变体仍持有窗口指针。
            item.setData(Qt.UserRole, index)
            row = StickerRow(sticker, self)
            item.setSizeHint(row.sizeHint())
            self.list_widget.addItem(item)
            self.list_widget.setItemWidget(item, row)
        self.summary.setText(f"共 {len(self.stickers)} 张贴图")

    def index_of(self, sticker):
        """返回贴图在当前列表中的位置，已关闭时返回 None。"""
        for index, current in enumerate(self.stickers):
            if current is sticker:
                return index
        return None

    def locate(self, index):
        """把指定贴图带到前台，隐藏状态下先恢复显示。"""
        if not isinstance(index, int) or not 0 <= index < len(self.stickers):
            return
        sticker = self.stickers[index]
        if not sticker.isVisible():
            sticker.setVisible(True)
        sticker.show()
        sticker.raise_()
        sticker.activateWindow()
