"""Windows 运行权限：判断当前是否提权、以管理员/标准权限重启自身。

设计口径：
* 提权走 `ShellExecuteW(runas)`，成功返回后由调用方退出当前进程；
* 降权不能再用 runas（子进程会继承提权令牌），改为取出当前令牌的 linked token
  （UAC 下就是登录用户的标准令牌），再用 `CreateProcessWithTokenW` 启动；
* 两者都只负责"拉起新进程"，是否等待 UAC、何时退出旧进程由调用方决定。
"""

import ctypes
import os
import subprocess
from ctypes import wintypes

from core.startup import launch_command, project_root
from logger import get_logger

ELEVATED_MARKER = "--elevated"
STANDARD_MARKER = "--standard"

RESULT_LAUNCHED = "launched"
RESULT_CANCELLED = "cancelled"
RESULT_FAILED = "failed"
RESULT_ALREADY = "already"

ERROR_CANCELLED = 1223
SEE_MASK_NOCLOSEPROCESS = 0x00000040
SW_HIDE = 0
SW_SHOWNORMAL = 1


def is_elevated():
    """当前进程是否拥有管理员令牌；非 Windows 一律按未提权处理。"""
    if os.name != "nt":
        return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        return False


def restart_as_admin():
    """以管理员权限拉起自身；返回 launched / cancelled / failed / already。"""
    if os.name != "nt":
        return RESULT_FAILED
    if is_elevated():
        return RESULT_ALREADY
    command = launch_command([ELEVATED_MARKER], windowed=True)
    return _shell_execute(command, wait_ms=None)


def restart_as_standard():
    """以标准权限拉起自身；仅在已提权时有意义。"""
    if os.name != "nt":
        return RESULT_FAILED
    if not is_elevated():
        return RESULT_ALREADY
    command = launch_command([STANDARD_MARKER], windowed=True)
    if _create_with_linked_token(command):
        return RESULT_LAUNCHED
    return RESULT_FAILED


def run_elevated(command, wait_ms=60000):
    """以管理员权限运行一条命令并等待结束，返回 (状态, 退出码)。

    用于"注册/删除开机计划任务"这类一次性助手：调用方是标准权限，助手需要
    管理员令牌才能操作 HIGHEST 级别的任务。状态为 cancelled 表示用户拒绝了 UAC。
    """
    if os.name != "nt":
        return RESULT_FAILED, -1
    if not is_elevated():
        status = _shell_execute(command, wait_ms=wait_ms, elevated=True)
        return status, -1 if status != RESULT_LAUNCHED else _last_exit_code()
    try:
        completed = subprocess.run(command, cwd=str(project_root()),
                                   creationflags=subprocess.CREATE_NO_WINDOW)
    except OSError as error:
        get_logger().warning("以管理员权限执行命令失败: %s", error)
        return RESULT_FAILED, -1
    return RESULT_LAUNCHED, completed.returncode


# ---------------------------------------------------------------------------
# ShellExecuteEx：既能提权，也能在需要时等待子进程结束
# ---------------------------------------------------------------------------

class _SHELLEXECUTEINFOW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("fMask", ctypes.c_ulong),
        ("hwnd", wintypes.HWND),
        ("lpVerb", wintypes.LPCWSTR),
        ("lpFile", wintypes.LPCWSTR),
        ("lpParameters", wintypes.LPCWSTR),
        ("lpDirectory", wintypes.LPCWSTR),
        ("nShow", ctypes.c_int),
        ("hInstApp", ctypes.c_void_p),
        ("lpIDList", ctypes.c_void_p),
        ("lpClass", wintypes.LPCWSTR),
        ("hkeyClass", ctypes.c_void_p),
        ("dwHotKey", wintypes.DWORD),
        ("hIcon", wintypes.HANDLE),
        ("hProcess", wintypes.HANDLE),
    ]


_last_process_exit_code = -1


def _last_exit_code():
    return _last_process_exit_code


def _shell_execute(command, wait_ms=None, elevated=True):
    """调用 ShellExecuteExW；elevated=False 时按普通权限打开。"""
    global _last_process_exit_code
    _last_process_exit_code = -1
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    shell32.ShellExecuteExW.argtypes = [ctypes.POINTER(_SHELLEXECUTEINFOW)]
    shell32.ShellExecuteExW.restype = wintypes.BOOL
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    info = _SHELLEXECUTEINFOW()
    info.cbSize = ctypes.sizeof(_SHELLEXECUTEINFOW)
    info.fMask = SEE_MASK_NOCLOSEPROCESS
    info.lpVerb = "runas" if elevated else "open"
    info.lpFile = command[0]
    info.lpParameters = subprocess.list2cmdline(command[1:]) if len(command) > 1 else None
    info.lpDirectory = str(project_root())
    info.nShow = SW_HIDE if wait_ms is not None else SW_SHOWNORMAL
    try:
        if not shell32.ShellExecuteExW(ctypes.byref(info)):
            error = ctypes.get_last_error()
            if error == ERROR_CANCELLED:
                get_logger().info("用户取消了管理员权限请求")
                return RESULT_CANCELLED
            get_logger().warning("请求管理员权限失败（WinError %s）", error)
            return RESULT_FAILED
        if not info.hProcess:
            # 极少数情况下（例如被重定向到已有实例）不会返回进程句柄。
            return RESULT_LAUNCHED
        if wait_ms is not None:
            kernel32.WaitForSingleObject(info.hProcess, int(wait_ms))
            code = wintypes.DWORD()
            if kernel32.GetExitCodeProcess(info.hProcess, ctypes.byref(code)):
                _last_process_exit_code = code.value
        kernel32.CloseHandle(info.hProcess)
        return RESULT_LAUNCHED
    except (AttributeError, OSError) as error:
        get_logger().warning("无法调用 ShellExecuteExW: %s", error)
        return RESULT_FAILED


# ---------------------------------------------------------------------------
# 降权：用 linked token 启动标准权限进程
# ---------------------------------------------------------------------------

TOKEN_QUERY = 0x0008
TOKEN_DUPLICATE = 0x0002
TOKEN_LINKED = 19
SECURITY_IMPERSONATION = 2
TOKEN_PRIMARY = 1
MAXIMUM_ALLOWED = 0x02000000
LOGON_WITH_PROFILE = 1
CREATE_UNICODE_ENVIRONMENT = 0x00000400


class _TOKEN_LINKED_TOKEN(ctypes.Structure):
    _fields_ = [("LinkedToken", wintypes.HANDLE)]


class _STARTUPINFOW(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("lpReserved", wintypes.LPWSTR),
        ("lpDesktop", wintypes.LPWSTR),
        ("lpTitle", wintypes.LPWSTR),
        ("dwX", wintypes.DWORD),
        ("dwY", wintypes.DWORD),
        ("dwXSize", wintypes.DWORD),
        ("dwYSize", wintypes.DWORD),
        ("dwXCountChars", wintypes.DWORD),
        ("dwYCountChars", wintypes.DWORD),
        ("dwFillAttribute", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("wShowWindow", wintypes.WORD),
        ("cbReserved2", wintypes.WORD),
        ("lpReserved2", ctypes.POINTER(ctypes.c_byte)),
        ("hStdInput", wintypes.HANDLE),
        ("hStdOutput", wintypes.HANDLE),
        ("hStdError", wintypes.HANDLE),
    ]


class _PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("hProcess", wintypes.HANDLE),
        ("hThread", wintypes.HANDLE),
        ("dwProcessId", wintypes.DWORD),
        ("dwThreadId", wintypes.DWORD),
    ]


def _create_with_linked_token(command):
    """用当前提权令牌的 linked token 启动普通权限进程。

    只拷贝和创建进程，不等待结果：新进程会自己去抢单实例锁，旧进程随后退出。
    UAC 被关闭或当前没有 linked token 时返回 False，由界面提示用户手动重启。
    """
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    advapi32.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                          ctypes.POINTER(wintypes.HANDLE)]
    advapi32.OpenProcessToken.restype = wintypes.BOOL
    advapi32.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                             ctypes.c_void_p, wintypes.DWORD,
                                             ctypes.POINTER(wintypes.DWORD)]
    advapi32.GetTokenInformation.restype = wintypes.BOOL
    advapi32.DuplicateTokenEx.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.c_void_p,
                                          ctypes.c_int, ctypes.c_int,
                                          ctypes.POINTER(wintypes.HANDLE)]
    advapi32.DuplicateTokenEx.restype = wintypes.BOOL
    advapi32.CreateProcessWithTokenW.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                                  wintypes.LPCWSTR, wintypes.LPWSTR,
                                                  wintypes.DWORD, ctypes.c_void_p,
                                                  wintypes.LPCWSTR,
                                                  ctypes.POINTER(_STARTUPINFOW),
                                                  ctypes.POINTER(_PROCESS_INFORMATION)]
    advapi32.CreateProcessWithTokenW.restype = wintypes.BOOL
    token = wintypes.HANDLE()
    linked = _TOKEN_LINKED_TOKEN()
    primary = wintypes.HANDLE()
    try:
        if not advapi32.OpenProcessToken(kernel32.GetCurrentProcess(),
                                         TOKEN_QUERY | TOKEN_DUPLICATE,
                                         ctypes.byref(token)):
            raise ctypes.WinError(ctypes.get_last_error())
        size = wintypes.DWORD()
        if not advapi32.GetTokenInformation(token, TOKEN_LINKED,
                                            ctypes.byref(linked),
                                            ctypes.sizeof(linked),
                                            ctypes.byref(size)):
            raise ctypes.WinError(ctypes.get_last_error())
        if not advapi32.DuplicateTokenEx(linked.LinkedToken, MAXIMUM_ALLOWED, None,
                                         SECURITY_IMPERSONATION, TOKEN_PRIMARY,
                                         ctypes.byref(primary)):
            raise ctypes.WinError(ctypes.get_last_error())
        startup = _STARTUPINFOW()
        startup.cb = ctypes.sizeof(_STARTUPINFOW)
        process = _PROCESS_INFORMATION()
        command_line = ctypes.create_unicode_buffer(subprocess.list2cmdline(command))
        if not advapi32.CreateProcessWithTokenW(
                primary, LOGON_WITH_PROFILE, None, command_line,
                CREATE_UNICODE_ENVIRONMENT, None, str(project_root()),
                ctypes.byref(startup), ctypes.byref(process)):
            raise ctypes.WinError(ctypes.get_last_error())
        kernel32.CloseHandle(process.hThread)
        kernel32.CloseHandle(process.hProcess)
        return True
    except (AttributeError, OSError) as error:
        get_logger().warning("以标准权限重启失败: %s", error)
        return False
    finally:
        for handle in (primary, linked.LinkedToken, token):
            if handle and handle.value:
                kernel32.CloseHandle(handle)
