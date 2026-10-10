"""让 Windows 把 ScreenSnap 当成独立应用，从而在通知设置里单独开关它的通知。

Windows 的规则：Windows 11 原生 Toast 只有在「系统里存在一个带该 AppUserModelID 的开始菜单
快捷方式」时才允许使用该身份，也才会在「设置 → 系统 → 通知」里列出**独立条目**（用户可单独静音
或关闭横幅）。没有它，`win11toast` 传 `app_id` 会抛异常，只能回退到默认身份（系统里显示为 Python）。

本模块负责创建/检查那个快捷方式：只创建一次、之后每次启动只做幂等检查，**失败不抛异常** ——
注册失败时通知照旧弹出，只是系统里没有独立入口（见 ui/native_toast.py 的回退）。
"""

import logging
import os
import sys
from pathlib import Path

from core.constants import APP_USER_MODEL_ID
from core.startup import is_frozen

SHORTCUT_NAME = "ScreenSnap.lnk"
SHORTCUT_DESCRIPTION = "ScreenSnap 截图与贴图工具"

# 历史身份：非打包应用在系统通知列表里**直接显示 AUMID 字符串**，所以 2026-10-11 把
# "ScreenSnap.Desktop" 改成更干净的 "ScreenSnap"。旧身份必须退役，否则通知设置里会留下一条
# 打不开也删不掉的「幽灵条目」（键还在，但再没有进程以它发通知）。
LEGACY_APP_USER_MODEL_IDS = ("ScreenSnap.Desktop",)

# 通知设置根键：每个身份一个子键，存该身份的静音/横幅开关等。
_NOTIFICATION_SETTINGS_KEY = ("Software\\Microsoft\\Windows\\CurrentVersion"
                             "\\Notifications\\Settings")


def retire_legacy_identities():
    """删掉旧身份的 Windows 通知设置子键（只动本模块列出的历史身份，失败不抛异常）。"""
    logger = logging.getLogger("screensnap")
    if not is_supported():
        return []
    retired = []
    try:
        import winreg

        for legacy in LEGACY_APP_USER_MODEL_IDS:
            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER,
                                 _NOTIFICATION_SETTINGS_KEY + "\\" + legacy)
                retired.append(legacy)
            except FileNotFoundError:
                pass
    except Exception as error:  # noqa: BLE001 - 清理失败无关紧要
        logger.debug("退役旧通知身份失败（忽略）: %s", error)
    if retired:
        logger.info("已退役旧通知身份（避免通知设置里留下幽灵条目）: %s", ", ".join(retired))
    return retired

# Shell 属性存储的打开方式：GPS_READWRITE = 2。写 System.AppUserModelID 必须走属性存储，
# WScript.Shell 只认识 TargetPath/Arguments 这些常规字段。
_GPS_READWRITE = 2


def is_supported():
    return os.name == "nt"


def start_menu_programs():
    """当前用户的开始菜单 Programs 目录（不需要管理员权限）。"""
    base = os.environ.get("APPDATA")
    if not base:
        return None
    return Path(base) / "Microsoft" / "Windows" / "Start Menu" / "Programs"


def shortcut_path():
    folder = start_menu_programs()
    return None if folder is None else folder / SHORTCUT_NAME


def _project_root():
    return Path(__file__).resolve().parents[1]


def target_command():
    """快捷方式的启动命令：打包时是 exe 自身，源码时是 pythonw + main.py（与开机自启同一口径）。"""
    if is_frozen():
        return [sys.executable]
    python = Path(sys.executable).with_name("pythonw.exe")
    return [str(python if python.exists() else sys.executable),
            str(_project_root() / "main.py")]


def working_directory():
    return str(Path(sys.executable).resolve().parent if is_frozen() else _project_root())


def icon_location():
    if is_frozen():
        return sys.executable
    icon = _project_root() / "ui" / "assets" / "icon.ico"
    return str(icon if icon.exists() else sys.executable)


def read_app_id(path):
    """读 .lnk 的 System.AppUserModelID；读不到返回 None（缺 pywin32 或属性不存在都算未注册）。"""
    if path is None:
        return None
    try:
        from win32com.propsys import propsys, pscon

        store = propsys.SHGetPropertyStoreFromParsingName(
            str(path), None, 0, propsys.IID_IPropertyStore)
        value = store.GetValue(pscon.PKEY_AppUserModel_ID)
        return None if value is None else str(value.GetValue())
    except Exception:  # noqa: BLE001 - 依赖缺失/COM 失败一律视为「未知」
        return None


def _write_app_id(path):
    from win32com.propsys import propsys, pscon

    store = propsys.SHGetPropertyStoreFromParsingName(
        str(path), None, _GPS_READWRITE, propsys.IID_IPropertyStore)
    store.SetValue(pscon.PKEY_AppUserModel_ID, propsys.PROPVARIANTType(APP_USER_MODEL_ID))
    store.Commit()


def _create_shortcut(path):
    import win32com.client

    shell = win32com.client.Dispatch("WScript.Shell")
    link = shell.CreateShortCut(str(path))
    command = target_command()
    link.TargetPath = command[0]
    link.Arguments = " ".join(
        ('"%s"' % item) if " " in item else item for item in command[1:])
    link.WorkingDirectory = working_directory()
    link.IconLocation = icon_location()
    link.Description = SHORTCUT_DESCRIPTION
    link.Save()


def ensure_registered(path=None):
    """幂等注册通知身份。返回 created / exists / unsupported / unavailable，永不抛异常。

    path 参数供测试注入临时路径使用（避免在开发机上真的写开始菜单）。
    """
    logger = logging.getLogger("screensnap")
    if not is_supported():
        return "unsupported"
    target = Path(path) if path is not None else shortcut_path()
    if target is None:
        return "unavailable"
    try:
        if target.exists() and read_app_id(target) == APP_USER_MODEL_ID:
            logger.debug("通知身份已注册: %s", target)
            return "exists"
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            # 存在但身份不对（旧版本创建的）⇒ 优先**直接改属性**：文件常被 Shell 短暂占用，
            # 删不掉时若直接放弃，用户就永远迁移不过去（2026-10-11 探针实测踩到）。
            try:
                # 写入成功即视为迁移完成：**不能依赖随后的读回** —— Shell 的属性存储会给出
                # 陈旧值（2026-10-11 探针实测：刚写完读回是 None），据此判断会误判成「身份没改成」
                # 进而去删文件，文件又被 Shell 占用 ⇒ 整个迁移失败。
                _write_app_id(target)
                logger.info("已把通知身份迁移为 %s: %s", APP_USER_MODEL_ID, target)
                retire_legacy_identities()
                return "created"
            except Exception as error:  # noqa: BLE001 - 改属性失败就走重建
                logger.debug("改写通知身份属性失败，改为重建快捷方式: %s", error)
            target.unlink()
        _create_shortcut(target)
        _write_app_id(target)
        logger.info("已注册通知身份（Windows「设置 → 通知」将出现独立的 ScreenSnap 条目）: %s",
                    target)
        # 身份换代时顺手退役旧身份的通知设置键（幂等；只在真的重建过快捷方式之后做）。
        retire_legacy_identities()
        return "created"
    except Exception as error:  # noqa: BLE001 - 注册失败不影响通知本身
        logger.warning("注册通知身份失败（通知仍会正常弹出，只是系统里没有独立入口）: %s", error)
        return "unavailable"
