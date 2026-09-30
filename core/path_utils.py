"""用户数据目录。"""

import os
from datetime import date
from pathlib import Path


def _renamed_directory(parent, old_name):
    """首次升级时迁移旧品牌目录，保持用户配置、缓存和截图可用。"""
    legacy = Path(parent) / old_name
    current = Path(parent) / "ScreenSnap"
    if current.exists() or not legacy.exists():
        return current
    try:
        legacy.rename(current)
    except OSError:
        return legacy
    return current


def data_dir():
    """配置、日志和贴图缓存放在用户目录，不随源码位置移动。"""
    return _renamed_directory(os.getenv("APPDATA") or Path.home() / ".config", "SnipasteClone")


def captures_dir():
    """返回图片目录下的截图根目录。"""
    return _renamed_directory(Path.home() / "Pictures", "SnipasteClone")


def configured_dir(config, key):
    """返回未应用归档日期的自动或手动保存根目录。"""
    default = captures_dir() / ("Auto" if key == "auto_dir" else "Manual")
    return Path(config.get(key) or default).expanduser()


def resolved_dir(config, key, today=None):
    """按设置返回自动或手动保存目录，并可追加年月/日期归档子目录。"""
    directory = configured_dir(config, key)
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