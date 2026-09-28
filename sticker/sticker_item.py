"""独立无边框贴图窗口。"""

import logging
import os
from pathlib import Path

from PySide6.QtCore import Qt, QPoint, QRect, QSize, Signal, QTimer
from PySide6.QtGui import QGuiApplication, QPixmap, QPainter, QColor, QPen, QImage
from PySide6.QtWidgets import QWidget

from core.window_snap import (visible_targets, window_logical_rect, window_present,
                              window_under_point)
from logger.log_context import log_scope
from logger.log_rate import log_every
from sticker.sticker_menu import show_menu
from sticker.sticker_snap import axis_candidates, snap_offsets


class StickerItem(QWidget):
    """独立悬浮贴图，单独维护尺寸、位置与输入状态。"""

    closed = Signal()

    def __init__(self, image, source=None, settings=None, origin=None):
        super().__init__()
        self.source = str(source) if source else None
        self.image = None if self.source else image
        self.origin = dict(origin) if isinstance(origin, dict) else None
        self.settings = settings or {}
        self.pixmap = QPixmap.fromImage(image)
        self.locked = False
        self.click_through = False
        self.always_on_top = True
        self.border_enabled = self.settings.get("sticker_border_enabled", True)
        self.shadow_enabled = self.settings.get("sticker_shadow_enabled", True)
        self.scale_factor = 1.0
        self.drag_origin = None
        # 吸附关系：记录贴到了哪个目标以及相对偏移，跟随窗口时按它重新定位。
        self.snap_target = None
        self.snap_hint = None
        self.follow_timer = None
        self.snap_candidate_keys = None
        # 拖动开始时枚举一次可见窗口，拖动过程中复用。
        self.snap_windows = None
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.resize(self.window_size())
        self.setFocusPolicy(Qt.StrongFocus)

    def padding(self):
        border = self.settings.get("sticker_border_width", 2) if self.border_enabled else 0
        shadow = round(self.settings.get("sticker_shadow_strength", 35) / 5) if self.shadow_enabled else 0
        return max(0, border, shadow)

    def image_rect(self):
        pad = self.padding()
        return QRect(pad, pad, max(1, self.width() - pad * 2), max(1, self.height() - pad * 2))

    def window_size(self):
        pad = self.padding()
        image_size = self.pixmap.size() * self.scale_factor
        return QSize(image_size.width() + pad * 2, image_size.height() + pad * 2)

    def apply_style(self):
        self.resize(self.window_size())
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        rect = self.image_rect()
        if self.shadow_enabled and self.settings.get("sticker_shadow_strength", 35):
            color = QColor(self.settings.get("sticker_shadow_color", "#000000"))
            strength = self.settings.get("sticker_shadow_strength", 35)
            color.setAlpha(round(255 * strength / 100))
            offset = max(1, round(strength / 20))
            painter.fillRect(rect.translated(offset, offset), color)
        painter.drawPixmap(rect, self.pixmap)
        if self.border_enabled and self.settings.get("sticker_border_width", 2):
            painter.setPen(QPen(QColor(self.settings.get("sticker_border_color", "#00ad91")),
                                self.settings.get("sticker_border_width", 2)))
            painter.drawRect(rect.adjusted(0, 0, -1, -1))
        if self.snap_hint:
            # 吸附生效时沿图像外沿画一圈虚线，Padding 不足 1px 时不可见。
            painter.setPen(QPen(QColor("#00ad91"), 1, Qt.DashLine))
            painter.drawRect(rect.adjusted(-1, -1, 1, 1))

    def mousePressEvent(self, event):
        # 记录全局鼠标到窗口左上角的偏移，跨显示器拖动仍保持原抓取位置。
        if event.button() == Qt.RightButton:
            show_menu(self, event.globalPosition().toPoint())
        elif event.button() == Qt.LeftButton and not self.locked:
            cursor = event.globalPosition().toPoint()
            self.drag_origin = cursor - self.pos()
            logging.getLogger("screensnap").debug(
                "开始拖动贴图: 位置(%d,%d) 尺寸(%dx%d) 源=%s 吸附=%s 目标=%s 阈值=%s",
                self.x(), self.y(), self.width(), self.height(), self.source or "临时图片",
                self.snap_enabled(), self.settings.get("sticker_snap_targets", "both"),
                self.settings.get("sticker_snap_threshold", 8))
            self.begin_snap_session(cursor)
            self.setFocus()

    def mouseMoveEvent(self, event):
        """以全局坐标拖动窗口，跨显示器时不重置抓取偏移；按住 Alt 临时不吸附。"""
        if self.drag_origin is None:
            return
        cursor = event.globalPosition().toPoint()
        position = cursor - self.drag_origin
        if self.snap_enabled() and not (event.modifiers() & Qt.AltModifier):
            position = self.apply_snap(position, cursor)
        else:
            self.release_snap()
        self.move(position)

    def mouseReleaseEvent(self, event):
        """释放拖动状态，避免下一次鼠标移动继续平移。"""
        self.drag_origin = None
        self.refresh_snap_relation()

    def snap_enabled(self):
        """吸附总开关；配置缺失时按默认开启处理。"""
        return bool(self.settings.get("sticker_snap_enabled", True))

    def begin_snap_session(self, cursor):
        """拖动开始时枚举一次所有可见窗口，拖动过程中复用以避免每帧调用系统 API。"""
        if not self.snap_enabled():
            self.snap_windows = []
            return
        self.snap_windows = visible_targets()
        under = window_under_point(cursor)
        logging.getLogger("screensnap").debug(
            "拖动开始: 鼠标(%d,%d) 可吸附窗口 %d 个 鼠标下窗口=%r",
            cursor.x(), cursor.y(), len(self.snap_windows),
            (under.title or under.class_name) if under else None)

    def snap_candidates(self, cursor):
        """按配置收集吸附目标：屏幕工作区边缘与所有可见窗口的边缘。"""
        mode = self.settings.get("sticker_snap_targets", "both")
        targets = []
        if mode in ("screen", "both"):
            for index, screen in enumerate(QGuiApplication.screens()):
                available = screen.availableGeometry()
                # right/bottom 转成开区间，贴图边缘与之对齐时偏移正好为 0，不留 1 像素缝。
                targets.append({"key": f"screen{index}", "kind": "screen", "title": screen.name(),
                                "left": available.left(), "top": available.top(),
                                "right": available.right() + 1, "bottom": available.bottom() + 1,
                                "allow_outside": False, "priority": 0})
        if mode in ("window", "both"):
            if self.snap_windows is None:
                self.begin_snap_session(cursor)
            for target in self.snap_windows or []:
                rect = target.rect
                # 同距离时优先贴到窗口，这样最大化窗口（与屏幕边缘重合）也能跟随。
                targets.append({"key": f"win{target.handle}", "kind": "window",
                                "hwnd": target.handle, "title": target.title,
                                "left": rect.left(), "top": rect.top(),
                                "right": rect.right() + 1, "bottom": rect.bottom() + 1,
                                "allow_outside": True, "priority": 1})
        keys = tuple(item["key"] for item in targets)
        # 拖动时每帧都会查询，候选集合不变就只记一次日志。
        if keys != self.snap_candidate_keys:
            self.snap_candidate_keys = keys
            logging.getLogger("screensnap").debug(
                "贴图吸附候选 %d 个: %s", len(targets), " | ".join(
                    f"{item['key']}({item['kind']}) 逻辑矩形({item['left']},{item['top']},"
                    f"{item['right'] - item['left'] + 1}x{item['bottom'] - item['top'] + 1})"
                    for item in targets) or "无")
        return targets

    def log_label(self):
        """日志里标识这张贴图，优先使用源文件名。"""
        return Path(self.source).name if self.source else "临时贴图"

    def apply_snap(self, position, cursor):
        """把拖动位置吸附到最近的屏幕或窗口边缘，返回修正后的位置。"""
        # 让这一段产生的日志都带上贴图标识，便于区分多张贴图。
        with log_scope(self.log_label()):
            return self.compute_snap(position, cursor)

    def compute_snap(self, position, cursor):
        """吸附计算主体，已在调用方的日志作用域内。"""
        threshold = self.settings.get("sticker_snap_threshold", 8)
        rect = (position.x(), position.y(), position.x() + self.width(), position.y() + self.height())
        targets = self.snap_candidates(cursor)
        dx, dy, hit = snap_offsets(rect, targets, threshold)
        self.log_snap_probe(rect, targets, threshold, dx, dy, hit)
        if hit is None:
            self.release_snap()
            return position
        snapped = QPoint(position.x() + dx, position.y() + dy)
        self.attach_snap(hit, targets, snapped)
        return snapped

    def log_snap_probe(self, rect, targets, threshold, dx, dy, hit):
        """记录吸附计算明细；拖动时按时间节流，贴上或脱离的时刻立即记录。"""
        state = (hit, dx, dy) if hit else None
        target = next((item for item in targets if item["key"] == (hit or ("", ""))[0]), None)
        horizontal, vertical = axis_candidates(rect, targets)
        # 两个方向各自的最小偏移，用于判断“左右吸不到”是候选问题还是阈值问题。
        best_x = min(horizontal, key=lambda item: abs(item[0])) if horizontal else None
        best_y = min(vertical, key=lambda item: abs(item[0])) if vertical else None
        log_every(logging.getLogger("screensnap"), logging.DEBUG, "snap-probe", state, 0.5,
                  "吸附计算: 贴图(%d,%d,%dx%d) 阈值=%d 候选=%d 个 | 水平最近=%s 垂直最近=%s"
                  " → 位移(%d,%d) 命中=%s 目标矩形=%s",
                  rect[0], rect[1], rect[2] - rect[0], rect[3] - rect[1], threshold, len(targets),
                  best_x or "无", best_y or "无", dx, dy, hit,
                  (target["left"], target["top"], target["right"], target["bottom"])
                  if target else "无")

    def attach_snap(self, hit, targets, position):
        """记录吸附关系；贴到窗口且开启跟随时启动跟随定时器。"""
        key, edge = hit
        target = next((item for item in targets if item["key"] == key), None)
        if target is None:
            return
        relation = {"target": target["kind"], "key": key, "edge": edge,
                    "title": target.get("title") or ""}
        if target["kind"] == "window":
            relation["hwnd"] = target["hwnd"]
            relation["rel_x"] = position.x() - target["left"]
            relation["rel_y"] = position.y() - target["top"]
        # 相对偏移随拖动每帧变化，比较身份时要排除，否则每次移动都记一条日志。
        identity = {key: relation[key] for key in ("target", "key", "edge", "title", "hwnd")
                    if key in relation}
        previous = {key: self.snap_target[key] for key in identity
                    if self.snap_target and key in self.snap_target}
        changed = identity != previous
        self.snap_target = relation
        self.snap_hint = edge
        if not changed:
            return
        logging.getLogger("screensnap").info(
            "贴图已吸附到%s %s：%s 边，位置(%d,%d)",
            "窗口" if relation["target"] == "window" else "屏幕工作区",
            relation["title"] or relation["key"], edge, position.x(), position.y())
        if relation["target"] == "window" and self.settings.get("sticker_follow_window", True):
            self.start_follow()
        elif relation["target"] != "window":
            self.stop_follow()

    def release_snap(self):
        """离开吸附目标，或在菜单里手动解除吸附与跟随。"""
        if self.snap_target is None:
            return
        logging.getLogger("screensnap").info("贴图解除吸附: %s %s",
                                            self.snap_target.get("target"),
                                            self.snap_target.get("title") or self.snap_target.get("key"))
        self.snap_target = None
        self.snap_hint = None
        self.stop_follow()

    def refresh_snap_relation(self):
        """位置或尺寸变化后重新记录与目标窗口的相对偏移。"""
        relation = self.snap_target
        if not relation or relation.get("target") != "window":
            return
        rect = window_logical_rect(relation.get("hwnd"))
        if rect is None:
            return
        relation["rel_x"] = self.x() - rect.left()
        relation["rel_y"] = self.y() - rect.top()

    def following(self):
        """当前是否正在跟随目标窗口。"""
        return self.follow_timer is not None and self.follow_timer.isActive()

    def start_follow(self):
        """开启跟随定时器，使贴图随目标窗口移动。"""
        if self.follow_timer is None:
            self.follow_timer = QTimer(self)
            self.follow_timer.timeout.connect(self.poll_follow)
        interval = max(30, int(self.settings.get("sticker_follow_interval", 120)))
        if self.follow_timer.interval() != interval:
            self.follow_timer.setInterval(interval)
        if self.follow_timer.isActive():
            return
        self.follow_timer.start()
        logging.getLogger("screensnap").info("开启窗口跟随: hwnd=%s 标题=%r 间隔=%dms",
                                            (self.snap_target or {}).get("hwnd"),
                                            (self.snap_target or {}).get("title"), interval)

    def stop_follow(self):
        """停止跟随，贴图保留在当前位置。"""
        if self.follow_timer is None or not self.follow_timer.isActive():
            return
        self.follow_timer.stop()
        logging.getLogger("screensnap").info("停止窗口跟随: hwnd=%s 标题=%r",
                                            (self.snap_target or {}).get("hwnd"),
                                            (self.snap_target or {}).get("title"))

    def toggle_follow(self):
        """右键菜单切换跟随；没有吸附到窗口时保持原状。"""
        if self.following():
            self.stop_follow()
        elif (self.snap_target or {}).get("target") == "window":
            self.start_follow()
        else:
            logging.getLogger("screensnap").debug("贴图没有吸附到窗口，跳过跟随切换")

    def poll_follow(self):
        """按目标窗口的最新位置移动贴图；窗口消失时自动解除吸附。"""
        relation = self.snap_target or {}
        handle = relation.get("hwnd")
        rect = window_logical_rect(handle)
        if rect is None:
            if not window_present(handle):
                # 只有窗口真的被销毁才解除吸附。
                logging.getLogger("screensnap").warning(
                    "跟随的目标窗口已关闭，自动解除吸附: hwnd=%s 标题=%r",
                    handle, relation.get("title"))
                self.snap_target = None
                self.snap_hint = None
                self.stop_follow()
                return
            # 最小化或隐藏时保留吸附关系，等窗口恢复后继续跟随。
            log_every(logging.getLogger("screensnap"), logging.DEBUG, "follow-idle",
                      handle, 2.0,
                      "目标窗口已最小化或隐藏，暂停跟随: hwnd=%s 标题=%r",
                      handle, relation.get("title"))
            return
        expected = QPoint(rect.left() + relation.get("rel_x", 0), rect.top() + relation.get("rel_y", 0))
        if expected != self.pos():
            # 跟随是定时器轮询，同一位置最多每秒记一次。
            log_every(logging.getLogger("screensnap"), logging.DEBUG, "follow",
                      (handle, expected.x(), expected.y()), 1.0,
                      "跟随窗口移动: (%d,%d) → (%d,%d) 标题=%r",
                      self.pos().x(), self.pos().y(), expected.x(), expected.y(),
                      relation.get("title"))
            self.move(expected)

    def apply_restored_snap(self, snap, rect):
        """会话恢复时按记录的相对偏移重新贴到原窗口，但不自动开启跟随。"""
        self.snap_target = {key: snap[key] for key in
                            ("target", "key", "edge", "title", "hwnd", "rel_x", "rel_y") if key in snap}
        self.snap_hint = snap.get("edge")
        self.move(rect.left() + snap.get("rel_x", 0), rect.top() + snap.get("rel_y", 0))
        logging.getLogger("screensnap").info("恢复贴图吸附: 窗口 %r（%s 边）",
                                            snap.get("title") or snap.get("hwnd"), snap.get("edge"))

    def wheelEvent(self, event):
        """未锁定时按固定倍率缩放，并限制最小、最大尺寸。"""
        if self.locked:
            return
        factor = 1.1 if event.angleDelta().y() > 0 else 1 / 1.1
        self.scale_factor = min(10, max(0.1, self.scale_factor * factor))
        self.resize(self.window_size())

    def keyPressEvent(self, event):
        """Esc 仅关闭当前贴图，方向键在未锁定时微移窗口。"""
        if event.key() == Qt.Key_Escape:
            # 每个贴图独立关闭，其他贴图窗口保持原状。
            self.close()
            return
        if event.key() in (Qt.Key_Left, Qt.Key_Right, Qt.Key_Up, Qt.Key_Down,
                           Qt.Key_A, Qt.Key_D, Qt.Key_W, Qt.Key_S) and not self.locked:
            self.move(self.pos() + QPoint((event.key() in (Qt.Key_Right, Qt.Key_D)) -
                                           (event.key() in (Qt.Key_Left, Qt.Key_A)),
                                           (event.key() in (Qt.Key_Down, Qt.Key_S)) -
                                           (event.key() in (Qt.Key_Up, Qt.Key_W))))
            # 键盘微调后同步吸附偏移，避免下一次跟随把贴图拉回原处。
            self.refresh_snap_relation()

    def toggle_lock(self):
        """锁定后禁止鼠标拖动、滚轮缩放和方向键微调。"""
        self.locked = not self.locked

    def toggle_click_through(self):
        """穿透后无法右键自身，需通过恢复交互的全局热键重新启用输入。"""
        self.click_through = not self.click_through
        self.setWindowFlag(Qt.WindowTransparentForInput, self.click_through)
        self.show()

    def reset_size(self):
        """恢复原始像素大小，不更换贴图内容。"""
        self.scale_factor = 1.0
        self.resize(self.window_size())

    def replace_image(self, image, source):
        """历史图片轮换时保留贴图窗口的当前位置和缩放倍率。"""
        self.source = str(source)
        self.image = None
        self.origin = None
        self.pixmap = QPixmap.fromImage(image)
        self.resize(self.window_size())
        self.update()

    def closeEvent(self, event):
        self.closed.emit()
        super().closeEvent(event)

    def copy_image(self):
        """复制原始图像而非带窗口缩放效果的预览。"""
        image = QImage(self.source) if self.source else self.image
        if image is not None and not image.isNull():
            QGuiApplication.clipboard().setImage(image)

    def toggle_top(self):
        """切换置顶标志后重新显示窗口，使窗口管理器应用新状态。"""
        self.always_on_top = not self.always_on_top
        self.setWindowFlag(Qt.WindowStaysOnTopHint, self.always_on_top)
        self.show()

    def toggle_border(self):
        self.border_enabled = not self.border_enabled
        self.apply_style()

    def toggle_shadow(self):
        self.shadow_enabled = not self.shadow_enabled
        self.apply_style()

    def state(self):
        """返回可写入 JSON 的窗口状态，源路径必须为字符串。"""
        snap = dict(self.snap_target) if self.snap_target else None
        if snap is not None:
            # 句柄必须转成普通整数才能写入 JSON。
            snap["hwnd"] = int(snap.get("hwnd", 0))
        return {"source": self.source, "x": self.x(), "y": self.y(),
                "scale": self.scale_factor, "opacity": self.windowOpacity(),
                "locked": self.locked, "top": self.always_on_top,
                "click_through": self.click_through, "border": self.border_enabled,
                "shadow": self.shadow_enabled, "origin": self.origin, "snap": snap}

    def thumbnail(self, width=96, height=72):
        """贴图管理窗口使用的等比缩略图。"""
        return self.pixmap.scaled(width, height, Qt.KeepAspectRatio, Qt.SmoothTransformation)

    def describe(self):
        """一句话说明贴图内容，优先使用剪贴板原始内容。"""
        origin = self.origin or {}
        kind = origin.get("kind")
        if kind == "text":
            first = next((line for line in str(origin.get("text", "")).splitlines() if line.strip()), "")
            return f"文字 · {first[:24]}" if first else "文字"
        if kind == "color":
            return f"颜色 {origin.get('text', '')}"
        if kind == "files":
            paths = origin.get("paths") or []
            name = Path(paths[0]).name if paths else "文件"
            return f"文件 · {name}" + (f" 等 {len(paths)} 项" if len(paths) > 1 else "")
        if self.source:
            return Path(self.source).name
        return "贴图"

    def origin_text(self):
        """文字与颜色贴图可复制回剪贴板的原始文本。"""
        origin = self.origin or {}
        return origin.get("text") if origin.get("kind") in ("text", "color") else None

    def origin_paths(self):
        """文件贴图记录的原始路径，恢复后仍可直接打开。"""
        origin = self.origin or {}
        if origin.get("kind") != "files":
            return []
        return [path for path in origin.get("paths") or [] if isinstance(path, str) and os.path.exists(path)]

    def copy_origin_text(self):
        """把文字或颜色值放回剪贴板。"""
        text = self.origin_text()
        if text:
            QGuiApplication.clipboard().setText(text)

    def open_origin(self):
        """用系统默认程序打开文件或所在目录。"""
        paths = self.origin_paths()
        if not paths:
            return False
        if os.name != "nt":
            return False
        try:
            for path in paths[:8]:
                os.startfile(path)
        except OSError:
            return False
        return True
