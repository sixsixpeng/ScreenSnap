"""贴图列表、目录历史与会话恢复。"""

import json
import logging
from time import time_ns
from pathlib import Path

from PySide6.QtCore import Qt, QObject, Signal
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QFileDialog, QMessageBox

from core.path_utils import data_dir, resolved_dir
from core.window_snap import window_logical_rect
from sticker.clipboard_source import read_clipboard
from sticker.sticker_item import StickerItem

# 贴图会话中允许保留的原始剪贴板类型。
ORIGIN_KINDS = ("text", "color", "files")


class StickerManager(QObject):
    """集中维护独立贴图、自动保存目录的历史及退出会话。"""

    changed = Signal()

    def __init__(self, settings):
        super().__init__()
        self.settings = settings
        self.items = []
        self.history_index = 0
        self.history_sticker = None

    def add(self, image, source=None, origin=None):
        """创建并显示贴图；关闭时释放 Qt 对象并从列表中移除。"""
        if source is None:
            cache = data_dir() / "sticker_cache"
            cache.mkdir(parents=True, exist_ok=True)
            stamp = time_ns()
            source = cache / f"sticker_{stamp}.png"
            # 同一时钟刻度内连续创建时追加序号，避免覆盖已有的缓存文件。
            number = 1
            while source.exists():
                source = cache / f"sticker_{stamp}_{number}.png"
                number += 1
            if not image.save(str(source), "PNG"):
                raise OSError(f"无法保存贴图缓存: {source}")
        item = StickerItem(image, str(source), self.settings, origin)
        item.open_file_replace = lambda: self.open_file(item)
        item.open_file_new = self.open_file
        item.setAttribute(Qt.WA_DeleteOnClose)
        item.closed.connect(lambda: self.remove(item))
        item.destroyed.connect(lambda: self.remove(item))
        self.items.append(item)
        item.show()
        self.changed.emit()
        logging.getLogger("screensnap").info("创建贴图: %s", source or "临时图片")
        return item

    def paste_clipboard(self, clipboard=None):
        """把剪贴板中的图片、文件、颜色或文字直接贴成新窗口。"""
        source = read_clipboard(self.settings, clipboard)
        if source is None:
            return None
        item = self.add(source.image, origin=source.origin())
        logging.getLogger("screensnap").info("贴出剪贴板内容: %s", source.kind)
        return item

    def open_file(self, replace=None):
        """从磁盘打开图片，替换指定窗口或创建独立贴图。"""
        path, _ = QFileDialog.getOpenFileName(
            replace, "从文件打开贴图", "",
            "图片文件 (*.png *.jpg *.jpeg *.bmp *.gif *.webp *.tif *.tiff)",
        )
        if not path:
            return False
        image = QImage(path)
        if image.isNull():
            logging.getLogger("screensnap").warning("无法读取贴图图片: %s", path)
            QMessageBox.warning(replace, "无法打开贴图", f"无法读取图片：{path}")
            return False
        logging.getLogger("screensnap").info("从文件打开贴图: %s", "替换当前窗口" if replace else path)
        if replace is None:
            self.add(image, path)
        else:
            replace.replace_image(image, path)
        return True

    def remove(self, item):
        """贴图销毁后从会话列表移除，避免持久化已关闭窗口。"""
        if item in self.items:
            self.items.remove(item)
            self.changed.emit()
            logging.getLogger("screensnap").debug("移除贴图: %s", item.source or "临时图片")

    def files(self):
        """按修改时间直接读取自动保存目录，不维护内部图片数据库。"""
        from core.image_io import saved_patterns

        folder = resolved_dir(self.settings, "auto_dir")
        if not folder.is_dir():
            logging.getLogger("screensnap").debug("自动保存目录不存在: %s", folder)
            return []
        available = []
        for pattern in saved_patterns():
            for path in folder.glob(pattern):
                try:
                    available.append((path.stat().st_mtime, path))
                except OSError as error:
                    logging.getLogger("screensnap").debug("跳过无法读取的图片 %s: %s", path, error)
                    continue
        available.sort(reverse=True)
        return [path for _, path in available[:self.settings["history_limit"]]]

    def paste_latest(self):
        """按时间打开最新的尚未贴出的有效历史图片。"""
        opened = {str(Path(item.source).resolve()) for item in self.items if item.source}
        for source in self.files():
            if str(source.resolve()) in opened:
                continue
            image = QImage(str(source))
            if image.isNull():
                logging.getLogger("screensnap").warning("历史图片无法读取，已跳过: %s", source)
                continue
            self.add(image, source)
            return True
        logging.getLogger("screensnap").debug("自动保存目录中没有可贴出的新图片")
        return False

    def cycle(self, delta):
        """上一张/下一张复用历史贴图；普通粘贴仍创建独立实例。"""
        files = self.files()
        if not files:
            logging.getLogger("screensnap").debug("没有可轮换的历史图片")
            return
        for _ in files:
            self.history_index = (self.history_index + delta) % len(files)
            source = files[self.history_index]
            image = QImage(str(source))
            if image.isNull():
                logging.getLogger("screensnap").warning("历史图片无法读取，已跳过: %s", source)
                continue
            if self.history_sticker in self.items:
                self.history_sticker.replace_image(image, source)
                logging.getLogger("screensnap").info("切换历史贴图: %s", source)
            else:
                self.history_sticker = self.add(image, source)
            return

    def toggle_hidden(self):
        """按当前是否存在可见贴图统一隐藏或显示全部贴图。"""
        visible = any(item.isVisible() for item in self.items)
        for item in self.items:
            item.setVisible(not visible)
        self.changed.emit()

    def restore_input(self):
        """仅恢复已启用点击穿透的贴图，以便再次通过鼠标操作。"""
        for item in self.items:
            if item.click_through:
                item.toggle_click_through()

    def close_all(self):
        """关闭所有窗口并清除历史贴图引用，避免继续复用旧实例。"""
        for item in self.items[:]:
            item.close()
        self.items.clear()
        self.history_sticker = None
        self.changed.emit()

    def persist(self):
        """保存所有窗口的可序列化状态；无源贴图在创建时已写入私有缓存。"""
        path = data_dir() / "stickers.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        states = [item.state() for item in self.items]
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(states, indent=2), encoding="utf-8")
        temporary.replace(path)
        self.cleanup_cache(state["source"] for state in states)
        logging.getLogger("screensnap").info("保存贴图会话: %d 张", len(self.items))

    def cleanup_cache(self, sources):
        """仅清理私有缓存中未被有效会话引用的贴图。"""
        cache = data_dir() / "sticker_cache"
        referenced = {Path(source).resolve() for source in sources}
        for file in cache.glob("sticker_*.png"):
            if file.resolve() not in referenced:
                try:
                    file.unlink()
                except OSError as error:
                    logging.getLogger("screensnap").warning("无法清理贴图缓存 %s: %s", file, error)

    def restore(self):
        """跳过已丢失的源文件，避免下次启动恢复出空白贴图。"""
        path = data_dir() / "stickers.json"
        if not path.exists():
            logging.getLogger("screensnap").debug("没有贴图会话文件，跳过恢复")
            self.cleanup_cache(())
            return
        try:
            states = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(states, list):
                raise ValueError("贴图会话必须是列表")
        except (OSError, UnicodeError, ValueError) as error:
            backup = path.with_name(f"{path.name}.broken-{time_ns()}")
            path.replace(backup)
            logging.getLogger("screensnap").warning("贴图会话无效，已备份到 %s: %s", backup, error)
            return
        logger = logging.getLogger("screensnap")
        restored_sources = []
        for state in states:
            if not isinstance(state, dict):
                logger.warning("贴图会话记录格式错误，已跳过: %r", state)
                continue
            source = state.get("source")
            if (not isinstance(source, str) or not source or
                    any(type(state.get(key)) is not int for key in ("x", "y")) or
                    type(state.get("scale")) not in (int, float) or
                    not 0.1 <= state["scale"] <= 10 or
                    type(state.get("opacity")) not in (int, float) or
                    not 0 <= state["opacity"] <= 1 or
                    type(state.get("locked")) is not bool or
                    not Path(source).is_file()):
                logger.warning("贴图会话记录无效或源文件已丢失，已跳过: %s", source)
                continue
            image = QImage(source)
            if image.isNull():
                logger.warning("贴图源文件无法读取，已跳过: %s", source)
                continue
            origin = state.get("origin")
            if not (isinstance(origin, dict) and origin.get("kind") in ORIGIN_KINDS):
                origin = None
            item = self.add(image, source, origin)
            restored_sources.append(source)
            item.move(state["x"], state["y"])
            item.scale_factor = state["scale"]
            item.border_enabled = state.get("border", item.settings.get("sticker_border_enabled", True))
            item.shadow_enabled = state.get("shadow", item.settings.get("sticker_shadow_enabled", True))
            item.resize(item.window_size())
            item.setWindowOpacity(state["opacity"])
            item.locked = state["locked"]
            if not state.get("top", True):
                item.toggle_top()
            if state.get("click_through", False):
                item.toggle_click_through()
            snap = state.get("snap")
            if isinstance(snap, dict) and snap.get("target") == "window":
                handle = snap.get("hwnd")
                rect = window_logical_rect(handle) if type(handle) is int else None
                if (rect is not None and type(snap.get("rel_x")) is int
                        and type(snap.get("rel_y")) is int):
                    item.apply_restored_snap(snap, rect)
                else:
                    logger.info("贴图吸附的目标窗口已失效，按原位置恢复: hwnd=%s", handle)
        self.cleanup_cache(restored_sources)
        if len(restored_sources) != len(states):
            logger.warning("贴图会话恢复不完整: %d/%d 张", len(restored_sources), len(states))
        else:
            logger.info("恢复贴图会话: %d 张", len(restored_sources))

    def refresh_style(self):
        """设置变更后刷新所有已打开贴图的外观。"""
        for item in self.items:
            item.apply_style()
