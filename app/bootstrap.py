"""应用启动辅助：QApp、单实例锁、异常钩子与日志周期。"""

import ctypes
import logging
import os
import subprocess
import sys
import threading
from pathlib import Path
from PySide6.QtCore import Qt, QPoint, QRect, QTimer, QSignalBlocker, QObject, Signal, Slot, QLockFile
from PySide6.QtGui import QIcon, QPixmap, QPainter, QColor, QPen, QGuiApplication, QFont, QCursor
from PySide6.QtWidgets import QApplication, QSystemTrayIcon, QGraphicsView, QFileDialog
from config import ConfigManager
from config.config_manager import TOOL_WIDTH_KEYS
from core import app_icon, data_dir, capture, qimage_to_pillow
from core.dpi import DisplayMapper
from core.startup import set_start_on_boot
from editor import EditorWindow
from hotkey import HotkeyManager
from logger import configure_logging
from screenshot import MaskWindow
from screenshot.mask_window import window_label
from sticker import StickerManager
from ui import SettingsWindow, make_tray_menu, CaptureNotification, StickerPanel
from ui.recycle_window import RecycleWindow
from ui.theme import apply_theme
from ui.window_bounds import WindowBoundsFilter
from PIL import Image

class QApp(QApplication):
    """重写 notify 捕获 Qt 事件/槽中的未处理异常并写入日志（默认会静默崩溃）。"""

    def notify(self, receiver, event):
        try:
            return super().notify(receiver, event)
        except Exception:
            logging.getLogger("screensnap").exception(
                "未捕获异常（事件类型 0x%x，接收者 %s）",
                event.type() if event is not None else -1,
                type(receiver).__name__ if receiver is not None else None)
            return False



def _log_uncaught(exc_type, exc_value, exc_traceback):
    """把未捕获异常写入 screensnap 日志；若日志尚未配置处理器，额外兜底写入文件。"""
    logger = logging.getLogger("screensnap")
    logger.exception("未捕获的全局异常", exc_info=(exc_type, exc_value, exc_traceback))
    # 日志处理器在 Application.__init__ 中才配置；在此之前崩溃也要保证写进文件。
    if not logger.handlers:
        try:
            log_dir = Path("logs") / _now_month()
            log_dir.mkdir(parents=True, exist_ok=True)
            import traceback as _tb
            with open(log_dir / "app.log", "a", encoding="utf-8") as fh:
                fh.write("\n=== 未捕获全局异常（日志未配置时的兜底写入）===\n")
                _tb.print_exception(exc_type, exc_value, exc_traceback, file=fh)
        except Exception:
            pass



def _now_month():
    from datetime import datetime
    return datetime.now().strftime("%Y-%m")



def install_exception_hooks():
    """安装全局兜底：主线程顶层异常、Qt 事件异常、子线程异常都写入日志，避免静默崩溃。"""
    sys.excepthook = _log_uncaught
    if hasattr(threading, "excepthook"):
        _orig = threading.excepthook

        def _thread_hook(args):
            try:
                _log_uncaught(args.exc_type, args.exc_value, args.exc_traceback)
            finally:
                _orig(args)

        threading.excepthook = _thread_hook



def acquire_single_instance_lock(path=None):
    """获取进程级应用锁；返回 None 表示已有实例持锁。"""
    lock_path = Path(path) if path is not None else data_dir() / "screensnap.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock = QLockFile(str(lock_path))
    return lock if lock.tryLock(0) else None



def notify_existing_instance():
    message = "ScreenSnap 已经在运行。请从系统托盘打开现有窗口，或退出现有实例后再启动。"
    if os.name == "nt":
        try:
            ctypes.windll.user32.MessageBoxW(None, message, "ScreenSnap", 0x40)
            return
        except (AttributeError, OSError):
            pass
    print(message, file=sys.stderr)


