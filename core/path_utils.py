"""用户数据目录。"""

import os
import shutil
from datetime import date
from pathlib import Path

from core.startup import project_root
from logger import get_logger


DATA_DIR_NAME = "data"


def data_dir():
    """配置、会话、历史与缓存统一放在运行文件所在目录的 data/ 下。"""
    return project_root() / DATA_DIR_NAME


def legacy_data_dir():
    """旧版用户数据位置，仅用于一次性迁移与清理。"""
    return Path(os.getenv("APPDATA") or Path.home() / ".config") / "ScreenSnap"


def clear_data_directory():
    """删除 ScreenSnap 用户数据目录，不创建替代目录或配置文件。

    进程自己正持有的文件在 Windows 上删不掉：单实例锁 `screensnap.lock` 由
    `acquire_single_instance_lock` 锁定，日志文件由 logging 打开。这类文件**跳过**而不是
    让整次清理抛错 —— 否则版本变化后的重置会每次都失败，用户数据永远清不掉。
    返回值仍是数据目录本身；被跳过的文件以 WARNING 记录，供用户手动删除。
    """
    root = data_dir()
    if root.name.casefold() not in (DATA_DIR_NAME, "screensnap"):
        raise ValueError(f"拒绝清理非 ScreenSnap data 目录: {root}")
    if root.is_symlink():
        raise OSError(f"拒绝清理符号链接数据目录: {root}")
    skipped = []

    def _skip_in_use(function, path, exc_info):
        # PermissionError(WinError 32/5) 表示文件被占用或无权限，跳过并记录，不中断清理。
        skipped.append(str(path))

    if root.exists():
        shutil.rmtree(root, onerror=_skip_in_use)
    if skipped:
        get_logger().warning(
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


def cache_root():
    """所有缓存目录的根：运行文件所在目录的 data/。"""
    return data_dir()


def cache_dir(name, settings=None):
    """图片类缓存目录：运行文件所在目录 data/<name>。"""
    del settings  # 缓存位置不再跟随保存目录
    root = cache_root() / name
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError:
        get_logger().warning("创建缓存目录失败: %s", root)
    return root


def _merge_tree(source, target):
    """把 source 下的文件/目录合并到 target；同名目标保留现有文件。"""
    migrated = 0
    target.mkdir(parents=True, exist_ok=True)
    for item in list(source.iterdir()):
        destination = target / item.name
        try:
            if item.is_dir():
                migrated += _merge_tree(item, destination)
                try:
                    item.rmdir()
                except OSError:
                    pass
            elif not destination.exists():
                shutil.move(str(item), str(destination))
                migrated += 1
        except OSError as error:
            get_logger().warning("迁移用户数据失败（跳过）: %s → %s: %s", item, destination, error)
    return migrated


def migrate_legacy_data():
    """把旧 %APPDATA%\\ScreenSnap 与旧运行目录缓存迁入 data/，然后尽量清理旧目录。"""
    new_root = data_dir()
    new_root.mkdir(parents=True, exist_ok=True)
    moved = 0
    old_root = legacy_data_dir()
    if old_root.exists() and old_root.resolve() != new_root.resolve():
        moved += _merge_tree(old_root, new_root)
        try:
            if not any(old_root.iterdir()):
                old_root.rmdir()
                get_logger().info("已迁移并清理旧 AppData 用户数据目录: %s", old_root)
            else:
                get_logger().warning("旧 AppData 目录仍有无法迁移的文件，可手动清理: %s", old_root)
        except OSError as error:
            get_logger().warning("清理旧 AppData 用户数据目录失败: %s: %s", old_root, error)
    # 旧版缓存曾直接放在运行文件根目录；迁入 data/，避免版本闸门清理时误碰程序文件。
    for name in ("sticker_cache", "clipboard_history", "toast_cache"):
        old_cache = project_root() / name
        new_cache = data_dir() / name
        if old_cache.is_dir() and old_cache.resolve() != new_cache.resolve():
            moved += _merge_tree(old_cache, new_cache)
            try:
                old_cache.rmdir()
            except OSError:
                pass
    if moved:
        get_logger().info("已迁移 %d 个用户数据/缓存文件到运行目录 data/", moved)
    return moved

def migrate_cache_dirs(settings):
    """把旧缓存搬到启动文件所在目录下，只搬一次。

    兼容两个历史位置：%APPDATA%\\ScreenSnap/<name> 与 <旧保存目录>/cache/<name>。
    返回迁移的文件数；搬不动（被占用等）就跳过并记 WARNING，绝不影响启动。
    """
    logger = get_logger()
    moved = migrate_legacy_data()
    old_roots = [legacy_data_dir()]
    if settings and settings.get("save_dir"):
        try:
            old_roots.append(configured_dir(settings) / "cache")
        except (TypeError, ValueError, OSError):
            pass
    for name in ("sticker_cache", "clipboard_history", "toast_cache"):
        new = cache_dir(name, settings)
        for old_root in old_roots:
            old = old_root / name
            if not old.is_dir() or old.resolve() == new.resolve():
                continue
            for item in old.iterdir():
                if not item.is_file():
                    continue
                target = new / item.name
                if target.exists():
                    continue
                try:
                    shutil.move(str(item), str(target))
                    moved += 1
                except OSError as error:
                    logger.warning("迁移缓存文件失败（跳过）: %s → %s: %s", item, target, error)
            try:
                if not any(old.iterdir()):
                    old.rmdir()
            except OSError:
                pass
    if moved:
        logger.info("已把 %d 个缓存图片迁移到启动目录下", moved)
    return moved
