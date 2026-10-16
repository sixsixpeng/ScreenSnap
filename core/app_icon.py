"""程序图标：优先使用随包分发的多尺寸 ICO，缺失或损坏时回退到 PNG。"""

import sys
from pathlib import Path

from PySide6.QtGui import QIcon

from logger import get_logger

# 两份图标是同一张图；ICO 含 16–256 多尺寸，在托盘和任务栏更清晰。
ICON_FILES = ("icon.ico", "icon.png")


def icon_dir():
    """图标目录：源码与 PyInstaller 均统一使用 ui/assets。"""
    bundled = getattr(sys, "_MEIPASS", None)
    root = Path(bundled) if bundled else Path(__file__).resolve().parent.parent
    return root / "ui" / "assets"


def app_icon():
    """返回可用尺寸最全的程序图标；没有可用文件时返回空图标由调用方兜底。"""
    for name in ICON_FILES:
        path = icon_dir() / name
        if not path.is_file():
            continue
        icon = QIcon(str(path))
        if not icon.isNull():
            return icon
        get_logger().warning("图标文件无法读取，尝试下一个候选: %s", path)
    get_logger().warning("未找到图标文件 %s，将使用内置绘制的图标", ICON_FILES)
    return QIcon()
