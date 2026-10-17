"""开机以管理员身份启动：通过 Windows 计划任务实现免 UAC 的登录自启。

为什么不用 HKCU Run：登录时由资源管理器启动的进程只能拿到标准令牌，想提权就必然弹 UAC。
计划任务可以配置"使用最高权限运行"，登录时由任务计划程序直接以完整令牌启动，不再询问。

口径：
* 计划任务是"开机自启"的另一种实现，与 Run 键互斥：启用任务时删除 Run 键；
  关闭任务后如果仍要开机自启，再由 sync_autostart() 写回 Run 键。
* 创建/删除 HIGHEST 任务需要管理员权限：当前进程已提权就直接执行，否则用
  `--admin-autostart install|remove` 以 runas 拉起一个一次性助手（只弹一次 UAC）。
"""

import locale
import os
import subprocess

from core import elevation, startup
from logger import get_logger

TASK_NAME = "ScreenSnapAdminStartup"
HELPER_FLAG = "--admin-autostart"
CREATE_NO_WINDOW = 0x08000000


def task_command():
    """计划任务要执行的命令行（打包后是 exe，源码模式是 pythonw + main.py）。"""
    return subprocess.list2cmdline(startup.launch_command(windowed=True))


def task_exists():
    """查询当前用户的任务是否存在；非 Windows 一律返回 False。"""
    if os.name != "nt":
        return False
    completed = _run(["schtasks", "/Query", "/TN", TASK_NAME], quiet=True)
    return completed.returncode == 0


def install_task():
    """创建（或覆盖）登录时以最高权限运行的任务；调用方需已提权。"""
    completed = _run(["schtasks", "/Create", "/F", "/TN", TASK_NAME,
                      "/SC", "ONLOGON", "/RL", "HIGHEST", "/TR", task_command()])
    return _result(completed, "创建")


def remove_task():
    """删除计划任务；不存在时视为成功。调用方需已提权。"""
    if not task_exists():
        return True, "任务不存在"
    completed = _run(["schtasks", "/Delete", "/F", "/TN", TASK_NAME])
    return _result(completed, "删除")


def set_enabled(enabled):
    """启用/关闭"开机以管理员身份启动"，返回 (是否成功, 说明)。

    需要时会弹一次 UAC（创建或删除 HIGHEST 任务）。用户取消 UAC 时返回
    `(False, "cancelled")`，调用方应把界面开关还原。
    """
    if os.name != "nt":
        return False, "仅支持 Windows"
    if enabled:
        # 总是 /Create /F 覆盖：程序目录可能被移动/重命名，已存在的任务命令也需要跟着更新。
        status, code = _run_privileged("install")
        if status == elevation.RESULT_CANCELLED:
            return False, "cancelled"
        if status != elevation.RESULT_LAUNCHED or code != 0 or not task_exists():
            return False, "创建计划任务失败（详见日志）"
        startup.remove_run_entry()
        get_logger().info("已创建/更新开机管理员计划任务: %s", task_command())
        return True, "已创建"
    if not task_exists():
        return True, "任务不存在"
    status, code = _run_privileged("remove")
    if status == elevation.RESULT_CANCELLED:
        return False, "cancelled"
    if status != elevation.RESULT_LAUNCHED or code != 0 or task_exists():
        return False, "删除计划任务失败（详见日志）"
    get_logger().info("已删除开机管理员计划任务")
    return True, "已删除"


def sync_autostart(data):
    """按配置把系统里的开机自启状态同步到唯一一种实现。

    * 管理员模式：只保留计划任务，删除 Run 键（避免登录时启动两份）；
    * 标准模式：按 start_on_boot 写/删 Run 键；发现残留任务时尽力清理。
    返回计划任务当前是否存在，供调用方提示。
    """
    if data.get("start_on_boot_admin"):
        startup.remove_run_entry()
        if elevation.is_elevated():
            # 已在管理员上下文：顺手覆盖任务，保证命令指向当前程序路径（目录移动后自愈）。
            ok, message = install_task()
            if not ok:
                get_logger().warning("刷新开机管理员计划任务失败: %s", message)
            return ok
        exists = task_exists()
        if not exists:
            get_logger().warning("配置要求开机以管理员身份启动，但计划任务不存在")
        return exists
    if data.get("start_on_boot"):
        startup.set_start_on_boot(True)
    else:
        startup.set_start_on_boot(False)
    if task_exists():
        if elevation.is_elevated():
            remove_task()
            get_logger().info("已清理与当前配置不符的开机管理员计划任务")
        else:
            get_logger().warning("检测到残留的开机管理员计划任务，当前无管理员权限无法清理")
        return False
    return False


def helper_main(argv):
    """`--admin-autostart install|remove` 入口：以管理员权限执行一次任务操作后退出。"""
    action = ""
    if HELPER_FLAG in argv:
        index = argv.index(HELPER_FLAG)
        if index + 1 < len(argv):
            action = argv[index + 1]
    if action == "install":
        ok, message = install_task()
    elif action == "remove":
        ok, message = remove_task()
    else:
        ok, message = False, "未知操作"
    get_logger().info("计划任务助手(%s): %s", action, message)
    return 0 if ok else 1


def _run_privileged(action):
    """通过 runas 拉起自己的助手模式并等待结束。"""
    return elevation.run_elevated(startup.launch_command([HELPER_FLAG, action], windowed=True))


def _run(command, quiet=False):
    """调用 schtasks；隐藏控制台窗口，输出按 Windows 控制台代码页解码后写日志。

    /Query 查不到任务是正常状态（未启用），用 quiet 避免每次启动都写 WARNING。
    """
    try:
        completed = subprocess.run(command, capture_output=True,
                                   creationflags=CREATE_NO_WINDOW)
    except OSError as error:
        if not quiet:
            get_logger().warning("无法执行 %s: %s", command[0], error)
        return subprocess.CompletedProcess(command, 1, b"", b"")
    if completed.returncode != 0 and not quiet:
        get_logger().warning("命令失败(%s): %s", completed.returncode,
                             _decode(completed.stdout + completed.stderr))
    return completed


def _decode(data):
    """schtasks 输出用的是控制台代码页（中文系统为 GBK），不能用 UTF-8 解。"""
    if not data:
        return ""
    encoding = "mbcs" if os.name == "nt" else locale.getpreferredencoding(False)
    return data.decode(encoding, "replace").strip()


def _result(completed, action):
    if completed.returncode == 0:
        return True, f"{action}成功"
    return False, f"{action}失败: {_decode(completed.stdout + completed.stderr)}"
