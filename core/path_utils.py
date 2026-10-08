"""用户数据目录。"""

import logging
import os
import shutil
from datetime import date
from pathlib import Path


def data_dir():
    """配置、日志和贴图缓存放在用户目录，不随源码位置移动。"""
    return Path(os.getenv("APPDATA") or Path.home() / ".config") / "ScreenSnap"


def clear_data_directory():
    """删除 ScreenSnap 用户数据目录，不创建替代目录或配置文件。

    进程自己正持有的文件在 Windows 上删不掉：单实例锁 `screensnap.lock` 由
    `acquire_single_instance_lock` 锁定，日志文件由 logging 打开。这类文件**跳过**而不是
    让整次清理抛错 —— 否则版本变化后的重置会每次都失败，用户数据永远清不掉。
    返回值仍是数据目录本身；被跳过的文件以 WARNING 记录，供用户手动删除。
    """
    root = data_dir()
    if root.name.casefold() != "screensnap":
        raise ValueError(f"拒绝清理非 ScreenSnap 数据目录: {root}")
    if root.is_symlink():
        raise OSError(f"拒绝清理符号链接数据目录: {root}")
    skipped = []

    def _skip_in_use(function, path, exc_info):
        # PermissionError(WinError 32/5) 表示文件被占用或无权限，跳过并记录，不中断清理。
        skipped.append(str(path))

    if root.exists():
        shutil.rmtree(root, onerror=_skip_in_use)
    if skipped:
        logging.getLogger("screensnap").warning(
            "清理用户数据目录时跳过 %d 个被占用/无权限的条目（可手动删除）：%s",
            len(skipped), ", ".join(skipped[:5]))
    return root


def captures_dir():
    """返回图片目录下的截图根目录。"""
    return Path.home() / "Pictures" / "ScreenSnap"


def configured_dir(config):
    """返回统一的图片保存根目录。"""
    default = captures_dir()
    return Path(config.get("save_dir") or default).expanduser()


def save_as_directory(settings):
    """「另存为」对话框的默认目录：上次用过的优先，没有或已失效则回落到保存目录。"""
    remembered = str(settings.get("save_as_dir") or "")
    if remembered and Path(remembered).is_dir():
        return remembered
    return str(resolved_dir(settings))


def resolved_dir(config, today=None):
    """按统一目录与归档设置返回图片保存位置。"""
    directory = configured_dir(config)
    archive_by_day = config.get("archive_by_day")
    archive_by_month = config.get("archive_by_month")
    if archive_by_day is None and archive_by_month is None:
        archive_enabled = config.get("archive_images", False)
        period = config.get("image_archive_period", "month")
        archive_by_day = archive_enabled and period == "day"
        archive_by_month = archive_enabled and period == "month"
    if archive_by_day or archive_by_month:
        today = today or date.today()
        folder = today.strftime("%Y-%m-%d" if archive_by_day else "%Y-%m")
        directory /= folder
    return directory