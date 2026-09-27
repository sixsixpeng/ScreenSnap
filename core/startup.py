"""Manage the current user's Windows startup entry."""

import subprocess
import sys
from pathlib import Path


RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
ENTRY_NAME = "ScreenSnap"


def is_frozen():
    """PyInstaller 设置 sys.frozen，Nuitka 只在编译模块注入 __compiled__。"""
    return getattr(sys, "frozen", False) or "__compiled__" in globals()


def set_start_on_boot(enabled):
    if sys.platform != "win32":
        if enabled:
            raise OSError("开机自动启动仅支持 Windows")
        return

    import winreg

    if enabled:
        if is_frozen():
            command = [sys.executable]
        else:
            python = Path(sys.executable).with_name("pythonw.exe")
            command = [str(python if python.exists() else sys.executable),
                       str(Path(__file__).resolve().parents[1] / "main.py")]
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                                winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, ENTRY_NAME, 0, winreg.REG_SZ,
                              subprocess.list2cmdline(command))
    else:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                                winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, ENTRY_NAME)
        except FileNotFoundError:
            pass