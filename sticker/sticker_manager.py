"""贴图列表、目录历史与会话恢复。"""

import json
import logging
import hashlib
from time import time_ns
from pathlib import Path

import shiboken6
from PySide6.QtCore import Qt, QObject, Signal, QBuffer, QIODevice, QTimer, QPoint
from PySide6.QtGui import QCursor, QGuiApplication, QImage
from PySide6.QtWidgets import QFileDialog, QMessageBox
from PIL import Image as PillowImage

from core.path_utils import configured_dir, data_dir
from core.window_snap import window_logical_rect
from logger.log_rate import log_every
from sticker.clipboard_source import ClipboardSource, read_clipboard
from sticker.sticker_item import StickerItem

# 贴图会话中允许保留的原始剪贴板类型。
ORIGIN_KINDS = ("text", "html", "color", "files")
DEFAULT_HISTORY_LIMIT = 10
MAX_STICKER_IMAGE_DIMENSION = 4096


class StickerManager(QObject):
    """集中维护独立贴图、自动保存目录的历史及退出会话。"""

    changed = Signal()
    edit_requested = Signal(object)

    def __init__(self, settings, clipboard=None):
        super().__init__()
        self.settings = settings
        self.placement_states = self._load_placement_states()
        self.last_placement = (list(self.placement_states.values())[-1]
                       if self.placement_states else None)
        self.items = []
        self.recycle_bin = []
        self.active_sticker = None
        self.focused_sticker = None
        self.selected_items = set()
        # 正在退出：Qt 会关闭所有顶层窗口，那不是用户“关掉贴图”，
        # 不能因此把它们移出会话（否则退出时保存 0 张、重启不恢复）。
        self.quitting = False
        self.history_index = 0
        self.history_sticker = None
        self.clipboard = clipboard or QGuiApplication.clipboard()
        self.clipboard_history = []
        self.clipboard_history_keys = []
        self.clipboard_history_index = -1
        self._clipboard_persist_timer = QTimer(self)
        self._clipboard_persist_timer.setSingleShot(True)
        self._clipboard_persist_timer.setInterval(350)
        self._clipboard_persist_timer.timeout.connect(self.persist_clipboard_history)
        self._load_clipboard_history()
        self._persist_timer = QTimer(self)
        self._persist_timer.setSingleShot(True)
        self._persist_timer.setInterval(250)
        self._persist_timer.timeout.connect(self.persist)
        self.clipboard.dataChanged.connect(self._clipboard_changed)
        self._clipboard_changed()

    @staticmethod
    def _clipboard_source_key(source):
        buffer = QBuffer()
        buffer.open(QIODevice.WriteOnly)
        source.image.save(buffer, "PNG")
        image_digest = hashlib.sha256(bytes(buffer.data())).digest()
        buffer.close()
        return (source.kind, source.text, source.html, tuple(source.paths),
                source.image.width(), source.image.height(), image_digest)

    def _clipboard_changed(self):
        """保存剪贴板变化的独立快照，供连续热键轮询。"""
        try:
            source = read_clipboard(self.settings, self.clipboard)
            if source is None:
                return
            key = self._clipboard_source_key(source)
            if self.clipboard_history_keys and self.clipboard_history_keys[0] == key:
                return
            if key in self.clipboard_history_keys:
                index = self.clipboard_history_keys.index(key)
                self.clipboard_history.pop(index)
                self.clipboard_history_keys.pop(index)
            snapshot = ClipboardSource(source.kind, source.image.copy(),
                                       source.text, source.paths, source.html)
            self.clipboard_history.insert(0, snapshot)
            self.clipboard_history_keys.insert(0, key)
            self._trim_clipboard_history()
            self.clipboard_history_index = -1
            self._clipboard_persist_timer.start()
            logging.getLogger("screensnap").debug(
                "记录剪贴板历史: 类型=%s 当前共 %d 条", source.kind,
                len(self.clipboard_history))
        except Exception as error:
            logging.getLogger("screensnap").debug(
                "读取剪贴板历史失败: %s", error)

    def _load_clipboard_history(self):
        path = data_dir() / "clipboard_history.json"
        if not path.is_file():
            return
        try:
            records = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(records, list):
                raise ValueError("剪贴板历史必须是列表")
        except (OSError, UnicodeError, ValueError) as error:
            logging.getLogger("screensnap").warning("剪贴板历史无法读取: %s", error)
            return
        image_dir = data_dir() / "clipboard_history"
        known_kinds = {"image", "text", "html", "color", "files"}
        for record in records:
            if not isinstance(record, dict):
                continue
            kind = record.get("kind")
            filename = record.get("image")
            if (kind not in known_kinds or not isinstance(filename, str) or
                    Path(filename).name != filename):
                continue
            image_path = image_dir / filename
            image = QImage(str(image_path))
            if image.isNull():
                continue
            paths = record.get("paths", [])
            if not isinstance(paths, list) or any(not isinstance(value, str) for value in paths):
                paths = []
            source = ClipboardSource(kind, image, str(record.get("text", "")),
                                     paths, str(record.get("html", "")))
            key = self._clipboard_source_key(source)
            if key not in self.clipboard_history_keys:
                self.clipboard_history.append(source)
                self.clipboard_history_keys.append(key)
        previous_count = len(self.clipboard_history)
        self._trim_clipboard_history()
        if len(self.clipboard_history) != previous_count:
            self._clipboard_persist_timer.start()
        logging.getLogger("screensnap").info(
            "恢复剪贴板历史: %d 条", len(self.clipboard_history))

    def persist_clipboard_history(self):
        """把剪贴板历史图片和可再贴出的来源信息写入独立缓存。"""
        self._clipboard_persist_timer.stop()
        root = data_dir()
        image_dir = root / "clipboard_history"
        image_dir.mkdir(parents=True, exist_ok=True)
        records = []
        referenced = set()
        for source in self.clipboard_history:
            key = self._clipboard_source_key(source)
            digest = hashlib.sha256(repr(key).encode("utf-8", errors="replace")).hexdigest()
            filename = f"clipboard_{digest}.png"
            image_path = image_dir / filename
            if not image_path.is_file() or QImage(str(image_path)).isNull():
                temporary_image = image_path.with_suffix(".tmp.png")
                if not source.image.save(str(temporary_image), "PNG"):
                    continue
                temporary_image.replace(image_path)
            origin = source.origin()
            record = {"kind": source.kind, "image": filename}
            record.update({key_name: value for key_name, value in origin.items()
                           if key_name != "kind"})
            records.append(record)
            referenced.add(filename)
        path = root / "clipboard_history.json"
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(records, ensure_ascii=False, indent=2),
                             encoding="utf-8")
        temporary.replace(path)
        for cached_image in image_dir.glob("clipboard_*.png"):
            if cached_image.name not in referenced:
                try:
                    cached_image.unlink()
                except OSError as error:
                    logging.getLogger("screensnap").warning(
                        "无法清理剪贴板历史缓存 %s: %s", cached_image, error)

    def _trim_clipboard_history(self):
        """剪贴板快照与文件历史共用用户设置的历史数量上限。"""
        limit = max(1, int(self.settings.get("history_limit", DEFAULT_HISTORY_LIMIT)))
        del self.clipboard_history[limit:]
        del self.clipboard_history_keys[limit:]
        if self.clipboard_history_index >= len(self.clipboard_history):
            self.clipboard_history_index = -1

    def clear_clipboard_history(self):
        """清空本次运行期间捕获的剪贴板历史。"""
        count = len(self.clipboard_history)
        self.clipboard_history.clear()
        self.clipboard_history_keys.clear()
        self.clipboard_history_index = -1
        self._clipboard_persist_timer.stop()
        self.persist_clipboard_history()
        logging.getLogger("screensnap").info("清空剪贴板历史: %d 条", count)
        return count

    def clear_rebuildable_cache(self):
        """清理可重建图片缓存，不删除已保存截图或贴图会话引用的源文件。"""
        root = data_dir()
        clipboard_images = history_entries = sticker_images = toast_images = 0
        # 清理范围按设置逐类勾选；未勾选的类别完全不碰。
        if self.settings.get("cache_clear_clipboard", True):
            clipboard_dir = root / "clipboard_history"
            clipboard_images = len(list(clipboard_dir.glob("clipboard_*.png")))
            history_entries = self.clear_clipboard_history()
        if self.settings.get("cache_clear_sticker", True):
            # 活动贴图与回收站里的贴图仍引用着各自的源文件：这些源文件不能算“可重建缓存”。
            referenced_sources = (item.source for item in (*self.items, *self.recycle_bin)
                                  if item.source)
            sticker_images = self.cleanup_cache(referenced_sources)
        if self.settings.get("cache_clear_toast", True):
            # Toast 缩略图只是通知里的一次性预览，随时可按需重建，直接整目录清掉。
            for image in (root / "toast_cache").glob("*.png"):
                try:
                    image.unlink()
                    toast_images += 1
                except OSError as error:
                    logging.getLogger("screensnap").warning(
                        "无法清理 Toast 图片缓存 %s: %s", image, error)
        result = {"clipboard_entries": history_entries,
                  "clipboard_images": clipboard_images,
                  "orphan_sticker_images": sticker_images,
                  "toast_images": toast_images}
        logging.getLogger("screensnap").info("清理可重建图片缓存: %s", result)
        return result

    def add(self, image, source=None, origin=None, position=None, show=True):
        """创建并显示贴图；关闭时释放 Qt 对象并从列表中移除。"""
        if isinstance(image, PillowImage.Image):
            if max(image.size) > MAX_STICKER_IMAGE_DIMENSION:
                image = image.copy()
                image.thumbnail((MAX_STICKER_IMAGE_DIMENSION,
                                 MAX_STICKER_IMAGE_DIMENSION),
                                PillowImage.Resampling.LANCZOS)
            from core.screen_capture import to_qimage

            image = to_qimage(image)
        elif max(image.width(), image.height()) > MAX_STICKER_IMAGE_DIMENSION:
            image = image.scaled(
                MAX_STICKER_IMAGE_DIMENSION, MAX_STICKER_IMAGE_DIMENSION,
                Qt.KeepAspectRatio, Qt.SmoothTransformation)
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
        # 先套用一次样式：apply_style 会记录当前 padding（描边/阴影决定），
        # 否则首次切换描边时缓存还是 None，补偿被跳过 —— 表现为只漂一次。
        item.apply_style()
        image_anchor = QPoint(position) if position is not None else None
        if position is None:
            # 没有记录位置的贴图（首次贴出、剪贴板/文件/文字贴图等）：放到**光标所在显示器**的
            # 可用区域正中，而不是贴着鼠标坐标 —— 贴图通常不小，跟着鼠标容易一半在屏外、
            # 也刚好挡住用户正在看的位置。鼠标在这里只用来决定“哪块屏”。
            # 有记录位置的恢复路径（下面的 else）保持原样，不经过这里。
            screen = (QGuiApplication.screenAt(QCursor.pos())
                      or QGuiApplication.primaryScreen())
            area = screen.availableGeometry()
            # 必须用 Qt 逻辑尺寸（源图像素 ÷ 当前屏 dpr）与 availableGeometry 对齐；
            # 直接用 pixmap.width() 是物理像素，缩放屏上居中会偏。
            window = item.window_size()
            width, height = window.width(), window.height()
            position = QPoint(area.x() + (area.width() - width) // 2,
                              area.y() + (area.height() - height) // 2)
        else:
            position = position - QPoint(item.padding(), item.padding())
        item.move(position)
        if image_anchor is not None:
            image_origin = item.pos() + QPoint(item.padding(), item.padding())
            logging.getLogger("screensnap").debug(
                "贴图锚点应用: 请求Qt屏幕点=(%d,%d) 窗口原点=(%d,%d) 图像原点=(%d,%d) "
                "尺寸=(%dx%d) padding=%d",
                image_anchor.x(), image_anchor.y(), item.x(), item.y(),
                image_origin.x(), image_origin.y(), item.width(), item.height(), item.padding())
        item.edit_requested.connect(self.edit_requested)
        item.open_file_replace = lambda: self.open_file(item)
        item.open_file_new = self.open_file
        item.setAttribute(Qt.WA_DeleteOnClose)
        item.closed.connect(lambda: self.remove(item))
        item.state_changed.connect(self.schedule_persist)
        item.activated.connect(lambda current=item: self._activate_sticker(current))
        item.focus_changed.connect(self._sticker_focus_changed)
        item.selection_requested.connect(self.select_item)
        item.batch_moved.connect(self.move_selected)
        item.batch_scaled.connect(self.scale_selected)
        item.batch_opacity_changed.connect(self.set_selected_opacity)
        item.manager = self
        self.items.append(item)
        if show:
            self._remember_placement(item)
        if show:
            item.show()
        self.changed.emit()
        self.schedule_persist()
        logging.getLogger("screensnap").info("创建贴图: %s", source or "临时图片")
        return item

    @staticmethod
    def _placement_key(source):
        return str(Path(source).resolve())

    def _load_placement_states(self):
        path = data_dir() / "sticker_placements.json"
        try:
            records = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError):
            records = []
        if not isinstance(records, list):
            records = []
        try:
            session = json.loads((path.parent / "stickers.json").read_text(encoding="utf-8"))
            if isinstance(session, list):
                records.extend(session)
        except (OSError, UnicodeError, ValueError):
            pass
        states = {}
        for record in records:
            if (not isinstance(record, dict) or not isinstance(record.get("source"), str)
                    or type(record.get("x")) is not int or type(record.get("y")) is not int
                    or type(record.get("scale")) not in (int, float)
                    or not 0.1 <= record["scale"] <= 10):
                continue
            key = self._placement_key(record["source"])
            states.pop(key, None)
            states[key] = {
                key: record[key] for key in ("source", "x", "y", "scale", "width", "height")
                if key in record
            }
        return states

    def _save_placement_states(self):
        path = data_dir() / "sticker_placements.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        limit = max(1, int(self.settings.get("history_limit", DEFAULT_HISTORY_LIMIT)))
        records = list(self.placement_states.values())[-limit:]
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(records, ensure_ascii=False, indent=2),
                             encoding="utf-8")
        temporary.replace(path)

    def _remember_placement(self, item, save=True):
        if not item.source:
            return
        state = item.state()
        key = self._placement_key(item.source)
        self.placement_states.pop(key, None)
        self.placement_states[key] = {
            key: state[key] for key in ("source", "x", "y", "scale", "width", "height")
        }
        self.last_placement = self.placement_states[key]
        if save:
            try:
                self._save_placement_states()
            except (OSError, TypeError, ValueError) as error:
                logging.getLogger("screensnap").warning("保存贴图布局失败: %s", error)

    def _apply_placement(self, item, state):
        item.move(state["x"], state["y"])
        item.scale_factor = state["scale"]
        item.resize(item.window_size())
        if type(state.get("width")) is int and type(state.get("height")) is int:
            item.apply_style()  # 尺寸由 scale_factor 与当前屏 dpr 推导，忽略可能过期的历史尺寸

    def rotate_active(self, degrees):
        """旋转最近操作的贴图；未选中时使用最新创建且仍打开的贴图。"""
        item = self.active_sticker if self.active_sticker in self.items else None
        item = item or (self.items[-1] if self.items else None)
        if item is not None:
            item.rotate(degrees)
            return True
        return False

    def select_item(self, item, additive=False):
        if item not in self.items:
            return
        if not additive:
            if item not in self.selected_items:
                self.selected_items = {item}
        elif item in self.selected_items:
            self.selected_items.remove(item)
        else:
            self.selected_items.add(item)
        self.selected_items.intersection_update(self.items)
        self.active_sticker = item
        self._update_selection_visuals()
        logging.getLogger("screensnap").debug(
            "贴图多选更新: ctrl=%s 选中=%d 当前=%s", additive,
            len(self.selected_items), item.source or "临时图片")
        self.changed.emit()

    def set_selected_items(self, items):
        self.selected_items = {item for item in items if item in self.items}
        if self.selected_items:
            self.active_sticker = next(iter(self.selected_items))
        self._update_selection_visuals()
        logging.getLogger("screensnap").debug("管理面板更新贴图多选: %d 张", len(self.selected_items))

    def _activate_sticker(self, item):
        self.active_sticker = item
        if item.hasFocus():
            self.focused_sticker = item
        self._update_selection_visuals()

    def _sticker_focus_changed(self, item, focused):
        if focused:
            self.focused_sticker = item
            self.active_sticker = item
            self._update_selection_visuals()
            return
        QTimer.singleShot(0, lambda current=item: self._clear_sticker_focus(current)
                       if shiboken6.isValid(current) else None)

    def _clear_sticker_focus(self, item):
        if self.focused_sticker is item and not item.hasFocus():
            self.focused_sticker = None
            self._update_selection_visuals()

    def refresh_selection_visuals(self):
        self._update_selection_visuals()

    def _update_selection_visuals(self):
        focused_group = (self.focused_sticker.group_name
                         if self.focused_sticker in self.items else "")
        multi_selection = len(self.selected_items) > 1
        for candidate in self.items:
            candidate.selected_for_batch = candidate in self.selected_items
            candidate.selection_effect_active = (
                self.settings.get("sticker_selection_effect_enabled", True) and
                candidate.selected_for_batch and
                (multi_selection or self.focused_sticker is not None and
                 (candidate is self.focused_sticker or
                  bool(focused_group) and candidate.group_name == focused_group)))
            candidate.update()

    def batch_targets(self, source):
        selected = self.selected_items if source in self.selected_items else {source}
        return [item for item in selected if item is not source and item in self.items]

    def move_selected(self, source, delta):
        for item in self.batch_targets(source):
            item.move(item.pos() + delta)
        self.schedule_persist()

    def scale_selected(self, source, factor):
        for item in self.batch_targets(source):
            if item.locked:
                continue
            item.scale_factor = min(10, max(0.1, item.scale_factor * factor))
            item.resize(item.window_size())
            item.show_scale_hint()

    def set_selected_opacity(self, source, value):
        for item in self.batch_targets(source):
            item.setWindowOpacity(value)
            item.state_changed.emit()

    def close_selected(self):
        targets = list(self.selected_items)
        for item in targets:
            item.close()
        return len(targets)

    def group_names(self):
        return sorted({item.group_name for item in self.items if item.group_name})

    def assign_group(self, items, name):
        name = str(name or "").strip()
        for item in items:
            if item in self.items:
                item.group_name = name
                item.state_changed.emit()
        self._update_selection_visuals()
        self.changed.emit()
        self.schedule_persist()

    def set_group_properties(self, name, properties):
        """对组成员显式应用属性值，避免混合状态下使用 toggle。"""
        members = [item for item in self.items if item.group_name == name]
        if not members:
            return 0

        toggle_properties = {
            "locked": ("locked", "toggle_lock"),
            "always_on_top": ("always_on_top", "toggle_top"),
            "border_enabled": ("border_enabled", "toggle_border"),
            "shadow_enabled": ("shadow_enabled", "toggle_shadow"),
            "click_through": ("click_through", "toggle_click_through"),
        }
        for item in members:
            for key, value in properties.items():
                if key == "opacity":
                    item.set_opacity(min(1.0, max(0.1, float(value))))
                elif key in toggle_properties:
                    attribute, method_name = toggle_properties[key]
                    if bool(getattr(item, attribute)) != bool(value):
                        getattr(item, method_name)()
                elif key == "reset_size" and value:
                    item.reset_size()
                elif key == "rotation":
                    if value == "reset":
                        item.reset_rotation()
                    elif type(value) is int and -3600 <= value <= 3600:
                        item.rotate(value)
                else:
                    continue
                item.state_changed.emit()

        self.schedule_persist()
        logging.getLogger("screensnap").info(
            "统一设置贴图组属性: 组=%s 成员=%d 属性=%s", name, len(members),
            ",".join(sorted(properties)))
        return len(members)

    def set_group_visible(self, name, visible):
        for item in self.items:
            if item.group_name == name:
                item.setVisible(visible)
        self.schedule_persist()
        logging.getLogger("screensnap").info("切换贴图组显示: 组=%s 显示=%s", name, visible)

    def toggle_selected_visibility(self):
        selected = [item for item in self.selected_items if item in self.items]
        if not selected:
            return False
        visible = any(not item.isVisible() for item in selected)
        for item in selected:
            item.setVisible(visible)
        self.schedule_persist()
        return visible

    def close_group(self, name):
        members = [item for item in self.items if item.group_name == name]
        for item in members:
            item.close()
        self._persist_timer.stop()
        self.persist()
        if members:
            logging.getLogger("screensnap").info(
                "关闭贴图组并保存会话: %s (%d)", name, len(members))
        return len(members)

    def move_group(self, name, delta):
        members = [item for item in self.items if item.group_name == name]
        for item in members:
            item.move(item.pos() + delta)
        if members:
            self.schedule_persist()
        return len(members)

    def paste_clipboard(self, clipboard=None):
        """按当前内容到较早内容的顺序轮询剪贴板历史并创建新贴图。"""
        if clipboard is not None:
            source = read_clipboard(self.settings, clipboard)
            if source is None:
                return None
            return self.add(source.image, origin=source.origin())
        board = self.clipboard
        source = read_clipboard(self.settings, board)
        if source is not None and board is self.clipboard:
            self._remember_clipboard_source(source)
        self._trim_clipboard_history()
        if not self.clipboard_history:
            return None
        self.clipboard_history_index = (self.clipboard_history_index + 1) % len(self.clipboard_history)
        source = self.clipboard_history[self.clipboard_history_index]
        item = self.add(source.image.copy(), origin=source.origin())
        logging.getLogger("screensnap").info(
            "贴出剪贴板历史: %d/%d 类型=%s", self.clipboard_history_index + 1,
            len(self.clipboard_history), source.kind)
        return item

    def _remember_clipboard_source(self, source):
        """同步热键触发时的最新剪贴板内容，避免信号尚未派发造成漏项。"""
        key = self._clipboard_source_key(source)
        if not self.clipboard_history_keys or self.clipboard_history_keys[0] != key:
            snapshot = ClipboardSource(source.kind, source.image.copy(),
                                      source.text, source.paths, source.html)
            if key in self.clipboard_history_keys:
                index = self.clipboard_history_keys.index(key)
                self.clipboard_history.pop(index)
                self.clipboard_history_keys.pop(index)
            self.clipboard_history.insert(0, snapshot)
            self.clipboard_history_keys.insert(0, key)
            self._trim_clipboard_history()
            self.clipboard_history_index = -1

    def open_file(self, replace=None):
        """多选打开图片；替换模式用第一张替换当前窗口，其余创建新贴图。"""
        paths, _ = QFileDialog.getOpenFileNames(
            replace, "从文件打开贴图", "",
            "图片文件 (*.png *.jpg *.jpeg *.bmp *.gif *.webp *.tif *.tiff)",
        )
        if not paths:
            return False
        opened = 0
        failed = []
        for path in paths:
            image = QImage(path)
            if image.isNull():
                failed.append(path)
                logging.getLogger("screensnap").warning("无法读取贴图图片: %s", path)
                continue
            if replace is not None and opened == 0:
                replace.replace_image(image, path)
            else:
                self.add(image, path)
            opened += 1
        if failed:
            QMessageBox.warning(replace, "部分图片无法打开", "以下图片无法读取：\n" + "\n".join(failed))
        if opened:
            logging.getLogger("screensnap").info("从文件打开贴图: %d 张", opened)
        return opened > 0

    def remove(self, item):
        """贴图销毁后从会话列表移除，避免持久化已关闭窗口。"""
        if getattr(self, "quitting", False):
            # 退出中：窗口是被 Qt 关掉的，会话必须保留（用户 2026-10-11 反馈：
            # 退出时贴图在屏幕上，重启却不恢复）。这里只留痕、不移除、不进回收站。
            logging.getLogger("screensnap").debug(
                "退出中忽略贴图关闭: %s", item.source or "临时图片")
            return
        self._remember_placement(item)
        for follower in self.items:
            relation = follower.snap_target or {}
            if relation.get("target") == "sticker" and \
                    relation.get("sticker_id") == item.session_id:
                follower.release_snap()
        if self.history_sticker is item:
            self.history_sticker = None
        if self.active_sticker is item:
            self.active_sticker = None
        if self.focused_sticker is item:
            self.focused_sticker = None
        self.selected_items.discard(item)
        if item in self.items:
            self.items.remove(item)
            # 留痕（2026-10-11）：排查“退出时保存 0 张、重启不恢复贴图”。
            # 若退出路径意外先关窗口，这条日志会出现在「正在退出，保存 …」之前。
            logging.getLogger("screensnap").debug(
                "贴图移出会话: 剩余 %d 张（源=%s）", len(self.items), item.source or "临时图片")
            self._update_selection_visuals()
            self.changed.emit()
            self.schedule_persist()
            if self.settings.get("sticker_recycle_enabled", True) and item not in self.recycle_bin:
                # 进入回收站时关闭自动销毁，保留窗口以便之后从回收站恢复；
                # 清空回收站或超出上限时再显式 deleteLater。
                item.setAttribute(Qt.WA_DeleteOnClose, False)
                item.hide()
                self.recycle_bin.append(item)
                self._enforce_recycle_limit()
                logging.getLogger("screensnap").debug("贴图进入回收站: %s", item.source or "临时图片")
            else:
                # 不进回收站（关闭回收站开关或已不在回收站）则恢复关闭即销毁。
                item.setAttribute(Qt.WA_DeleteOnClose, True)
                logging.getLogger("screensnap").debug("移除贴图: %s", item.source or "临时图片")

    def _enforce_recycle_limit(self):
        """回收站超过上限时丢弃最旧的条目（已关闭隐藏，直接销毁）。"""
        limit = self.settings.get("sticker_recycle_limit", 10)
        while len(self.recycle_bin) > limit:
            old = self.recycle_bin.pop(0)
            old.deleteLater()

    def recycle_items(self):
        """返回当前回收站中的贴图（按进入顺序）。"""
        return list(self.recycle_bin)

    def recycle_restore(self, item):
        """把回收站中的贴图恢复回活动列表并重新显示。"""
        if item not in self.recycle_bin:
            return
        self.recycle_bin.remove(item)
        try:
            item.closed.disconnect()
        except (RuntimeError, TypeError):
            pass
        item.closed.connect(lambda: self.remove(item))
        item.setAttribute(Qt.WA_DeleteOnClose, True)
        self.items.append(item)
        if not item.isVisible():
            item.show()
        item.raise_()
        self._update_selection_visuals()
        self.changed.emit()
        self.schedule_persist()
        logging.getLogger("screensnap").info("从回收站恢复贴图: %s", item.source or "临时图片")

    def recycle_delete(self, item):
        """把回收站中的单张贴图彻底删除（不再恢复）。"""
        if item not in self.recycle_bin:
            return
        self.recycle_bin.remove(item)
        item.deleteLater()
        self.changed.emit()
        logging.getLogger("screensnap").info("从回收站彻底删除贴图: %s", item.source or "临时图片")

    def empty_recycle(self):
        """清空回收站，真正销毁其中所有贴图。"""
        for item in self.recycle_bin:
            item.deleteLater()
        self.recycle_bin.clear()
        self.changed.emit()
        logging.getLogger("screensnap").info("清空贴图回收站")

    def files(self):
        """按修改时间直接读取自动保存目录，不维护内部图片数据库。"""
        from core.image_io import saved_patterns

        folder = configured_dir(self.settings)
        if not folder.is_dir():
            logging.getLogger("screensnap").debug("自动保存目录不存在: %s", folder)
            return []
        available = []
        for pattern in saved_patterns():
            for path in folder.rglob(pattern):
                try:
                    available.append((path.stat().st_mtime, path))
                except OSError as error:
                    logging.getLogger("screensnap").debug("跳过无法读取的图片 %s: %s", path, error)
                    continue
        available.sort(reverse=True)
        return [path for _, path in available[:self.settings["history_limit"]]]

    def paste_latest(self):
        """按时间打开最新的尚未贴出的有效历史图片。"""
        opened = {str(Path(item.source).resolve()): item
                  for item in self.items if item.source}
        most_recent_open_item = None
        for source in self.files():
            existing_item = opened.get(str(source.resolve()))
            if existing_item is not None:
                if most_recent_open_item is None:
                    most_recent_open_item = existing_item
                continue
            image = QImage(str(source))
            if image.isNull():
                logging.getLogger("screensnap").warning("历史图片无法读取，已跳过: %s", source)
                continue
            item = self.add(image, source, show=False)
            placement = (self.placement_states.get(self._placement_key(source))
                         or self.last_placement)
            if placement is not None:
                self._apply_placement(item, placement)
            else:
                screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
                bounds = screen.availableGeometry()
                item.move(bounds.center() - QPoint(item.width() // 2, item.height() // 2))
            item.show()
            self._remember_placement(item)
            return True
        if most_recent_open_item is not None:
            most_recent_open_item.show()
            most_recent_open_item.raise_()
            most_recent_open_item.activateWindow()
            self.active_sticker = most_recent_open_item
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
        # 留痕（2026-10-11）：这条路径会清空 items，而它此前完全静默 ——
        # 排查“退出时保存 0 张、重启不恢复贴图”时，必须能看出是不是它干的。
        logging.getLogger("screensnap").info(
            "关闭全部贴图: %d 张（调用来源见同刻调用栈）", len(self.items))
        for item in self.items[:]:
            try:
                item.close()
            except RuntimeError as error:
                # 退出路径上 remove() 会早退，此时窗口的 C++ 对象可能已被 Qt 析构，
                # 对它调用 close() 会抛 RuntimeError（2026-10-11 审计 A-4 探针复现）。
                # 关闭全部是用户可见动作，绝不能因为一张已消失的贴图整体失败。
                logging.getLogger("screensnap").warning("关闭贴图时对象已销毁（已跳过）: %s", error)
        self.items.clear()
        self.selected_items.clear()
        self.history_sticker = None
        self._persist_timer.stop()
        self.changed.emit()

    def stop_pending_persistence(self):
        """取消排队中的会话与剪贴板写入，用于明确丢弃数据的完整重置流程。"""
        self._persist_timer.stop()
        self._clipboard_persist_timer.stop()

    def persist(self):
        """保存所有窗口的可序列化状态；无源贴图在创建时已写入私有缓存。"""
        try:
            path = data_dir() / "stickers.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            states = []
            failed = 0
            for item in self.items:
                try:
                    states.append(item.state())
                except Exception as error:  # noqa: BLE001 退出路径上窗口可能已被 Qt 析构
                    # 退出时 remove() 会早退，窗口对象可能已被 Qt 删除（WA_DeleteOnClose），
                    # item.state() 会抛 RuntimeError。逐张兜住，绝不让单张失败导致整次会话
                    # 写不出去或写成空数组（2026-10-11 爆炸半径审计 C-4）。
                    failed += 1
                    logging.getLogger("screensnap").warning(
                        "读取贴图状态失败（已跳过）: %s", error)
            if failed and not states:
                logging.getLogger("screensnap").warning(
                    "%d 张贴图状态都读取失败，保留上一次会话文件不覆盖", failed)
                return False
            # 留痕（2026-10-11）：退出时若这里 items=0，就说明贴图在退出前已被移除，
            # 问题在移除路径而不在持久化本身。
            logging.getLogger("screensnap").debug(
                "保存贴图会话: items=%d states=%d 可见=%d",
                len(self.items), len(states),
                sum(1 for item in self.items if item.isVisible()))
            for item in self.items:
                self._remember_placement(item, save=False)
            try:
                self._save_placement_states()
            except (OSError, TypeError, ValueError) as error:
                logging.getLogger("screensnap").warning("保存贴图布局失败: %s", error)
            temporary = path.with_suffix(".tmp")
            temporary.write_text(json.dumps(states, indent=2), encoding="utf-8")
            temporary.replace(path)
        except (OSError, TypeError, ValueError) as error:
            log_every(logging.getLogger("screensnap"), logging.ERROR,
                      "sticker_session_persist", str(error), 60,
                      "保存贴图会话失败: %s", error)
            return False
        self.cleanup_cache(state["source"] for state in states)
        return True

    def schedule_persist(self):
        """合并连续的拖动和缩放事件，延迟写入最新会话状态。"""
        self._persist_timer.start()

    def cleanup_cache(self, sources):
        """仅清理私有缓存中未被有效会话引用的贴图。"""
        # 贴图私有缓存：只有当前无人引用的 sticker_*.png 才可删，返回实际删除数量供上报。
        cache = data_dir() / "sticker_cache"
        referenced = {Path(source).resolve() for source in sources}
        removed = 0
        for file in cache.glob("sticker_*.png"):
            if file.resolve() not in referenced:
                try:
                    file.unlink()
                    removed += 1
                except OSError as error:
                    logging.getLogger("screensnap").warning("无法清理贴图缓存 %s: %s", file, error)
        return removed

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
        restored_items = {}
        pending_sticker_snaps = []
        for state in states:
            if not isinstance(state, dict):
                logger.warning("贴图会话记录格式错误，已跳过: %r", state)
                continue
            source = state.get("source")
            if (not isinstance(source, str) or not source or
                    any(type(state.get(key)) is not int for key in ("x", "y")) or
                    type(state.get("scale")) not in (int, float) or
                    not 0.1 <= state["scale"] <= 10 or
                                        (("width" in state or "height" in state) and
                                         (type(state.get("width")) is not int or
                                            type(state.get("height")) is not int or
                                            not 1 <= state["width"] <= 32768 or
                                            not 1 <= state["height"] <= 32768)) or
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
            item = self.add(image, source, origin, show=False)
            # 恢复端现场（与 add() 里的 image_anchor 日志成对）：存储值 vs 应用几何 + 屏幕 dpr/padding，
            # 用来判定偏移属于取整（不可避）还是 padding/frame 失配（可修）；state 键名也一并打印，避免猜错。
            try:
                _dpr = item.screen().devicePixelRatio() if item.screen() else None
            except (AttributeError, RuntimeError):
                _dpr = None
            logger.debug(
                "贴图恢复定位：存储键=%s 存储 x=%s y=%s w=%s h=%s scale=%s | 屏幕dpr=%s padding=%s 应用几何=%s",
                list(state.keys())[:12], state.get("x"), state.get("y"),
                state.get("width"), state.get("height"), state.get("scale"),
                _dpr, item.padding(), item.geometry().getRect())
            session_id = state.get("id")
            if not isinstance(session_id, str) or not session_id or session_id in restored_items:
                session_id = item.session_id
            item.session_id = session_id
            restored_items[session_id] = item
            restored_sources.append(source)
            item.move(state["x"], state["y"])
            item.scale_factor = state["scale"]
            rotation = state.get("rotation", 0)
            if type(rotation) is int and -3600 <= rotation <= 3600:
                item.rotate(rotation)
            item.border_enabled = state.get("border", item.settings.get("sticker_border_enabled", True))
            item.shadow_enabled = state.get("shadow", item.settings.get("sticker_shadow_enabled", False))
            # 吸附提示开关是每张贴图自己的（右键菜单可切），随会话恢复；
            # 老会话没有该键时沿用创建时从全局默认继承的值（2026-10-11 审计 C-B）。
            item.snap_hint_enabled = bool(state.get("snap_hint_enabled", item.snap_hint_enabled))
            item.group_name = str(state.get("group", ""))
            item.set_background_mode(state.get("background_mode",
                                              item.settings.get("sticker_background_mode", "transparent")))
            item.resize(item.window_size())
            if "width" in state and "height" in state:
                item.apply_style()  # 尺寸由 scale_factor 与当前屏 dpr 推导，忽略可能过期的历史尺寸
            item.setWindowOpacity(state["opacity"])
            item.locked = state["locked"]
            item.always_on_top = bool(state.get("top", True))
            item.setWindowFlag(Qt.WindowStaysOnTopHint, item.always_on_top)
            item.click_through = bool(state.get("click_through", False))
            item.setWindowFlag(Qt.WindowTransparentForInput, item.click_through)
            snap = state.get("snap")
            if isinstance(snap, dict) and snap.get("target") == "window":
                handle = snap.get("hwnd")
                rect = window_logical_rect(handle) if type(handle) is int else None
                if (rect is not None and type(snap.get("rel_x")) is int
                        and type(snap.get("rel_y")) is int):
                    item.apply_restored_snap(snap, rect)
                else:
                    logger.info("贴图吸附的目标窗口已失效，按原位置恢复: hwnd=%s", handle)
            elif isinstance(snap, dict) and snap.get("target") == "sticker":
                pending_sticker_snaps.append((item, snap))
        for item, snap in pending_sticker_snaps:
            target = restored_items.get(snap.get("sticker_id"))
            if target is not None and target is not item:
                item.apply_restored_sticker_snap(snap, target)
            else:
                logger.info("贴图跟随目标已失效，按原位置恢复: id=%s", snap.get("sticker_id"))
        for item in restored_items.values():
            item.show()
        self.cleanup_cache(restored_sources)
        if len(restored_sources) != len(states):
            logger.warning("贴图会话恢复不完整: %d/%d 张", len(restored_sources), len(states))
        else:
            logger.info("恢复贴图会话: %d 张", len(restored_sources))

    def refresh_style(self):
        """设置变更后刷新所有已打开贴图的外观。"""
        for item in self.items:
            item.apply_style()
