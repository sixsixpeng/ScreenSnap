"""用户数据目录。"""

import os
from datetime import date
from pathlib import Path


def data_dir():
    """配置、日志和贴图缓存放在用户目录，不随源码位置移动。"""
    return Path(os.getenv("APPDATA") or Path.home() / ".config") / "ScreenSnap"


def captures_dir():
    """返回图片目录下的截图根目录。"""
    return Path.home() / "Pictures" / "ScreenSnap"


def configured_dir(config):
    """返回统一的图片保存根目录。"""
    default = captures_dir()
    return Path(config.get("save_dir") or default).expanduser()


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