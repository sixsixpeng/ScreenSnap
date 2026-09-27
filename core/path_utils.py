"""用户数据目录。"""

import os
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


def resolved_dir(config, key):
    """自动与手动保存各有默认子目录；显式配置优先。"""
    default = captures_dir() / ("Auto" if key == "auto_dir" else "Manual")
    return Path(config.get(key) or default).expanduser()