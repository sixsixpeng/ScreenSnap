"""截图提示条的可配置项列表：勾选决定显示与否，顺序决定提示条里的排列。

列表的顺序**就是**提示的显示顺序：用户在设置里新勾选一项时，它会按当前所在行的
位置参与排序（即"以添加的顺序为准"），无需另外维护一份排序配置。
配置里只保存"勾选中的 id 顺序"，未勾选的行留在界面上但不写入配置。

排序支持两种方式：按住条目上下拖动，或选中后点上/下移按钮（拖动在列表较长时不好控制）。
"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QHBoxLayout, QListWidget, QListWidgetItem, QPushButton,
                               QVBoxLayout, QWidget)


class HintOrderList(QWidget):
    """提示项勾选 + 排序控件；值就是"已勾选的 id 顺序"列表。"""

    value_changed = Signal(list)

    def __init__(self, ids, labels, parent=None):
        super().__init__(parent)
        self._ids = tuple(ids)
        self._labels = dict(labels)
        # 控件自身也要有说明：设置页会检查分组内每个控件都带 tooltip。
        self.setToolTip("提示条要显示哪些键位提示，以及它们的显示顺序。\n"
                        "勾选＝显示，取消勾选＝不显示；顺序即截图时提示条里的排列顺序。\n"
                        "调整顺序：按住条目上下拖动，或选中后用「上移 / 下移」按钮。")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        self.list = QListWidget(self)
        self.list.setDragDropMode(QListWidget.InternalMove)
        self.list.setDefaultDropAction(Qt.MoveAction)
        self.list.setSelectionMode(QListWidget.SingleSelection)
        self.list.setToolTip("勾选要显示的提示项；按住条目上下拖动，或选中后用下面的按钮上/下移。\n"
                             "列表顺序就是截图时提示条里的顺序。")
        self.list.setMinimumHeight(150)
        layout.addWidget(self.list)
        buttons = QHBoxLayout()
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.setSpacing(6)
        self.up_button = QPushButton("上移", self)
        self.down_button = QPushButton("下移", self)
        self.up_button.setToolTip("把选中的提示项往上移动一位（顺序即提示条里的显示顺序）")
        self.down_button.setToolTip("把选中的提示项往下移动一位")
        buttons.addWidget(self.up_button)
        buttons.addWidget(self.down_button)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        self.up_button.clicked.connect(lambda: self.move_current(-1))
        self.down_button.clicked.connect(lambda: self.move_current(1))
        self.list.currentRowChanged.connect(self._sync_buttons)
        self.list.blockSignals(True)
        try:
            for item_id in self._ids:
                self.list.addItem(self._make_item(item_id, checked=True))
        finally:
            self.list.blockSignals(False)
        self.list.itemChanged.connect(self._on_changed)
        self.list.model().rowsMoved.connect(self._on_changed)
        self._sync_buttons()

    # ---- 兼容旧接口：这些方法直接代理给内部列表 ----

    def count(self):
        return self.list.count()

    def item(self, index):
        return self.list.item(index)

    def model(self):
        return self.list.model()

    def clear(self):
        return self.list.clear()

    def addItem(self, item):
        return self.list.addItem(item)

    def _make_item(self, item_id, checked):
        item = QListWidgetItem(self._labels.get(item_id, item_id))
        item.setData(Qt.UserRole, item_id)
        item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable | Qt.ItemIsDragEnabled |
                      Qt.ItemIsSelectable)
        item.setCheckState(Qt.Checked if checked else Qt.Unchecked)
        return item

    def _sync_buttons(self, *_args):
        row = self.list.currentRow()
        self.up_button.setEnabled(row > 0)
        self.down_button.setEnabled(0 <= row < self.list.count() - 1)

    def move_current(self, delta):
        """把选中行上移/下移一位；未选中或已到边界时不动作。"""
        row = self.list.currentRow()
        target = row + delta
        if row < 0 or not 0 <= target < self.list.count():
            return
        item = self.list.takeItem(row)
        self.list.insertItem(target, item)
        self.list.setCurrentRow(target)
        self._on_changed()

    def _on_changed(self, *_args):
        self.value_changed.emit(self.value())

    def value(self):
        """已勾选的提示项 id，按列表当前顺序。"""
        checked = []
        for index in range(self.list.count()):
            item = self.list.item(index)
            if item.checkState() == Qt.Checked:
                checked.append(item.data(Qt.UserRole))
        return checked

    def set_value(self, ids):
        """按配置恢复勾选与顺序；不在配置里的行取消勾选但保留在界面上。"""
        wanted = [item_id for item_id in (ids or ())]
        self.list.blockSignals(True)
        try:
            self.list.clear()
            for item_id in list(wanted) + [i for i in self._ids if i not in wanted]:
                self.list.addItem(self._make_item(item_id, item_id in wanted))
        finally:
            self.list.blockSignals(False)
        self._sync_buttons()
