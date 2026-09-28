"""贴图列表、目录历史与会话恢复。"""

import json
import logging
from time import time_ns
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QFileDialog, QMessageBox

from core.path_utils import data_dir, resolved_dir
from sticker.sticker_item import StickerItem


class StickerManager:
    """集中维护独立贴图、自动保存目录的历史及退出会话。"""

    def __init__(self, settings):
        self.settings = settings
        self.items = []
        self.history_index = 0
        self.history_sticker = None

    def add(self, image, source=None):
        """创建并显示贴图；关闭时释放 Qt 对象并从列表中移除。"""
        if source is None:
            cache = data_dir() / "sticker_cache"
            cache.mkdir(parents=True, exist_ok=True)
            source = cache / f"sticker_{time_ns()}.png"
            if not image.save(str(source), "PNG"):
                raise OSError(f"无法保存贴图缓存: {source}")
        item = StickerItem(image, str(source), self.settings)
        item.open_file_replace = lambda: self.open_file(item)
        item.open_file_new = self.open_file
        item.setAttribute(Qt.WA_DeleteOnClose)
        item.closed.connect(lambda: self.remove(item))
        item.destroyed.connect(lambda: self.remove(item))
        self.items.append(item)
        item.show()
        logging.getLogger("screensnap").info("创建贴图: %s", source or "临时图片")
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
            QMessageBox.warning(replace, "无法打开贴图", f"无法读取图片：{path}")
            return False
        if replace is None:
            self.add(image, path)
        else:
            replace.replace_image(image, path)
        return True

    def remove(self, item):
        """贴图销毁后从会话列表移除，避免持久化已关闭窗口。"""
        if item in self.items:
            self.items.remove(item)

    def files(self):
        """按修改时间直接读取自动保存目录，不维护内部图片数据库。"""
        folder = resolved_dir(self.settings, "auto_dir")
        if not folder.is_dir():
            return []
        available = []
        for path in folder.glob("*.png"):
            try:
                available.append((path.stat().st_mtime, path))
            except OSError:
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
                continue
            self.add(image, source)
            return True
        return False

    def cycle(self, delta):
        """上一张/下一张复用历史贴图；普通粘贴仍创建独立实例。"""
        files = self.files()
        for _ in files:
            self.history_index = (self.history_index + delta) % len(files)
            source = files[self.history_index]
            image = QImage(str(source))
            if image.isNull():
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
        restored_sources = []
        for state in states:
            if not isinstance(state, dict):
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
                continue
            image = QImage(source)
            if image.isNull():
                continue
            item = self.add(image, source)
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
        self.cleanup_cache(restored_sources)

    def refresh_style(self):
        """设置变更后刷新所有已打开贴图的外观。"""
        for item in self.items:
            item.apply_style()
