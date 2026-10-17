"""Manage the current user's Windows startup entry."""

import subprocess
import sys
from pathlib import Path


RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
ENTRY_NAME = "ScreenSnap"


def is_frozen():
    """PyInstaller 设置 sys.frozen，Nuitka 只在编译模块注入 __compiled__。"""
    return getattr(sys, "frozen", False) or "__compiled__" in globals()


def project_root():
    """当前运行的根目录：打包后是 exe 所在目录，源码模式是仓库根目录。"""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def python_executable():
    """源码模式优先用 pythonw，避免看护/提权助手弹出控制台窗口。"""
    if is_frozen():
        return sys.executable
    python = Path(sys.executable).with_name("pythonw.exe")
    return str(python if python.exists() else sys.executable)


def launch_command(extra=(), windowed=False):
    """拉起本程序自身的命令行（不含环境变量）。

    打包后 `sys.executable` 就是 exe 本身，不能再拼 main.py；源码模式显式跑 main.py。
    看护、提权、降权、计划任务都复用这一份，避免四处各写一套。

    windowed=True 时源码模式优先用 pythonw.exe（开机自启/提权助手不应弹控制台）；
    看护必须保持 sys.executable，否则控制台调试信息会被吞掉。
    """
    if is_frozen():
        return [sys.executable, *extra]
    executable = python_executable() if windowed else sys.executable
    return [executable, str(project_root() / "main.py"), *extra]


def set_start_on_boot(enabled):
    if sys.platform != "win32":
        if enabled:
            raise OSError("开机自动启动仅支持 Windows")
        return

    import winreg

    if enabled:
        command = launch_command(windowed=True)
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                                winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, ENTRY_NAME, 0, winreg.REG_SZ,
                              subprocess.list2cmdline(command))
    else:
        remove_run_entry()


def remove_run_entry():
    """删除 HKCU Run 项；不存在时静默返回。"""
    if sys.platform != "win32":
        return
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                            winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, ENTRY_NAME)
    except FileNotFoundError:
        pass


def run_entry_exists():
    """当前用户注册表中是否存在开机自启项。"""
    if sys.platform != "win32":
        return False
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                            winreg.KEY_QUERY_VALUE) as key:
            winreg.QueryValueEx(key, ENTRY_NAME)
        return True
    except FileNotFoundError:
        return False
