"""用户界面包。

直接以静态绝对导入公开顶层 UI 组件，便于 PyInstaller 的静态分析识别，
避免运行时 `importlib.import_module` 这类动态导入被漏打包。"""

from ui.capture_notification import CaptureNotification
from ui.settings_window import SettingsWindow
from ui.sticker_panel import StickerPanel
from ui.tray_menu import make_tray_menu

__all__ = [
    "SettingsWindow",
    "CaptureNotification",
    "StickerPanel",
    "make_tray_menu",
]
