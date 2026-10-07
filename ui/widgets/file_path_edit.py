"""目录路径输入与浏览。"""

from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QWidget, QHBoxLayout, QLineEdit, QPushButton, QFileDialog, QStyle


class FilePathEdit(QWidget):
    """在手输目录和文件夹浏览之间共享同一个设置回调。"""

    def __init__(self, value, changed, open_path=None):
        """路径输入框 + 目录选择/打开按钮。

        open_path 是可调用对象而不是固定目录：保存页要打开“当前生效目录”（含归档子目录），
        不能直接用输入框里的原始值。
        """
        super().__init__()
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        self.input = QLineEdit(value)
        self.input.editingFinished.connect(lambda: changed(self.input.text()))
        button = QPushButton("浏览…")
        button.setIcon(self.style().standardIcon(QStyle.SP_DirOpenIcon))
        button.clicked.connect(self.browse)
        self.open_button = QPushButton("打开目录")
        self.open_button.setIcon(self.style().standardIcon(QStyle.SP_DirOpenIcon))
        self.open_button.setToolTip("在资源管理器中打开当前保存目录")
        self.open_button.clicked.connect(lambda: self.open_directory(open_path))
        self.changed = changed
        row.addWidget(self.input)
        row.addWidget(button)
        row.addWidget(self.open_button)

    def open_directory(self, path=None):
        """打开当前保存目录；目录不存在时先创建，保留 Explorer 定位能力。"""
        if callable(path):
            path = path()
        directory = Path(path or self.input.text()).expanduser() if path or self.input.text() else None
        if directory is None:
            return False
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError:
            return False
        return QDesktopServices.openUrl(QUrl.fromLocalFile(str(directory)))

    def browse(self):
        """取消浏览时保留当前路径，不触发写入配置。"""
        folder = QFileDialog.getExistingDirectory(self, "选择目录", self.input.text())
        if folder:
            self.input.setText(folder)
            self.changed(folder)