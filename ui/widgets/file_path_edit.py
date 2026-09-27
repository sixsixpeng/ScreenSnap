"""目录路径输入与浏览。"""

from PySide6.QtWidgets import QWidget, QHBoxLayout, QLineEdit, QPushButton, QFileDialog, QStyle


class FilePathEdit(QWidget):
    """在手输目录和文件夹浏览之间共享同一个设置回调。"""

    def __init__(self, value, changed):
        super().__init__()
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        self.input = QLineEdit(value)
        self.input.editingFinished.connect(lambda: changed(self.input.text()))
        button = QPushButton("浏览…")
        button.setIcon(self.style().standardIcon(QStyle.SP_DirOpenIcon))
        button.clicked.connect(self.browse)
        self.changed = changed
        row.addWidget(self.input)
        row.addWidget(button)

    def browse(self):
        """取消浏览时保留当前路径，不触发写入配置。"""
        folder = QFileDialog.getExistingDirectory(self, "选择目录", self.input.text())
        if folder:
            self.input.setText(folder)
            self.changed(folder)