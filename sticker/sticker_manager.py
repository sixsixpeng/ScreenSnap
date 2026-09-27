"""贴图列表、目录历史与会话恢复。"""

import json
import logging
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage

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
        item = StickerItem(image, str(source) if source else None, self.settings)
        item.setAttribute(Qt.WA_DeleteOnClose)
        item.destroyed.connect(lambda: self.remove(item))
        self.items.append(item)
        item.show()
        logging.getLogger("screensnap").info("创建贴图: %s", source or "临时图片")
        return item

    def remove(self, item):
        """贴图销毁后从会话列表移除，避免持久化已关闭窗口。"""
        if item in self.items:
            self.items.remove(item)

    def files(self):
        """按修改时间直接读取自动保存目录，不维护内部图片数据库。"""
        folder = resolved_dir(self.settings, "auto_dir")
        if not folder.is_dir():
            return []
        return sorted(folder.glob("*.png"), key=lambda path: path.stat().st_mtime, reverse=True)[:self.settings["history_limit"]]

    def paste_latest(self):
        """读取自动保存目录中最新图片；无历史文件时不创建空贴图。"""
        files = self.files()
        if files:
            self.add(QImage(str(files[0])), files[0])
            return True
        return False

    def cycle(self, delta):
        """上一张/下一张复用历史贴图；普通粘贴仍创建独立实例。"""
        files = self.files()
        if files:
            self.history_index = (self.history_index + delta) % len(files)
            source = files[self.history_index]
            image = QImage(str(source))
            if self.history_sticker in self.items:
                self.history_sticker.replace_image(image, source)
                logging.getLogger("screensnap").info("切换历史贴图: %s", source)
            else:
                self.history_sticker = self.add(image, source)

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
        """临时贴图先写入私有缓存，再保存所有窗口的可序列化状态。"""
        path = data_dir() / "stickers.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        cache = data_dir() / "sticker_cache"
        cache.mkdir(parents=True, exist_ok=True)
        for index, item in enumerate(self.items):
            if not item.source:
                source = cache / f"sticker_{index}.png"
                if not item.image.save(str(source), "PNG"):
                    raise OSError(f"无法保存贴图缓存: {source}")
                item.source = str(source)
        path.write_text(json.dumps([item.state() for item in self.items], indent=2), encoding="utf-8")
        logging.getLogger("screensnap").info("保存贴图会话: %d 张", len(self.items))

    def restore(self):
        """跳过已丢失的源文件，避免下次启动恢复出空白贴图。"""
        path = data_dir() / "stickers.json"
        if not path.exists():
            return
        for state in json.loads(path.read_text(encoding="utf-8")):
            source = state.get("source")
            if not source or not Path(source).is_file():
                continue
            item = self.add(QImage(source), source)
            item.move(state["x"], state["y"])
            item.scale_factor = state["scale"]
            item.resize(item.pixmap.size() * item.scale_factor)
            item.setWindowOpacity(state["opacity"])
            item.locked = state["locked"]
            if not state.get("top", True):
                item.toggle_top()
            if state.get("click_through", False):
                item.toggle_click_through()