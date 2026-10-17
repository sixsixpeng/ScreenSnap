"""独立无边框贴图窗口。"""

import logging
import os
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import (Qt, QPoint, QRect, QRectF, QSize, Signal, QTimer,
                            QEvent, QUrl, QMimeData)
from PySide6.QtGui import (QGuiApplication, QPixmap, QPainter, QColor, QPen,
                           QImage, QTransform, QBitmap, QRegion, QPainterPath,
                           QDrag, QMovie, QCursor)
from PySide6.QtWidgets import QWidget, QApplication

from core.image_io import IMAGE_FILE_SUFFIXES

from core.window_snap import (visible_targets, window_logical_rect, window_present,
                              window_under_point)
from core.constants import checker_tile_size
from editor.image_effects import copy_image_to_clipboard
from logger import get_logger
from logger.log_context import log_scope
from logger.log_rate import log_every
from sticker.sticker_menu import show_menu
from sticker.sticker_snap import axis_candidates, snap_offsets


class StickerItem(QWidget):
    """独立悬浮贴图，单独维护尺寸、位置与输入状态。"""

    closed = Signal()
    state_changed = Signal()
    activated = Signal()
    focus_changed = Signal(object, bool)
    selection_requested = Signal(object, bool)
    batch_moved = Signal(object, object)
    batch_scaled = Signal(object, float)
    batch_opacity_changed = Signal(object, float)
    edit_requested = Signal(object)

    def __init__(self, image, source=None, settings=None, origin=None):
        super().__init__()
        self.setProperty("screensnap_overlay", True)
        self.session_id = uuid4().hex
        self.source = str(source) if source else None
        self.image = None if self.source else image
        self.origin = dict(origin) if isinstance(origin, dict) else None
        self.settings = settings or {}
        self.original_pixmap = QPixmap.fromImage(image)
        self.pixmap = QPixmap(self.original_pixmap)
        self.rotation_degrees = 0
        # 动图状态：GIF 由 QMovie 驱动，pixmap 每帧替换；静态贴图保持原有路径。
        self.movie = None
        self.animated = False
        self.animation_paused = False
        self.animation_speed = int(self.settings.get("sticker_gif_speed", 100))
        self._animated_corner_radius = None
        self._drag_out_start = None
        self._drag_out_active = False
        self.setAcceptDrops(bool(self.settings.get("sticker_drag_enabled", True)))
        self.locked = False
        self.click_through = False
        self.always_on_top = True
        self.border_enabled = self.settings.get("sticker_border_enabled", True)
        self.shadow_enabled = self.settings.get("sticker_shadow_enabled", False)
        self.background_mode = self.settings.get("sticker_background_mode", "transparent")
        self.group_name = ""
        self.selected_for_batch = False
        self.selection_effect_active = False
        self.scale_factor = 1.0
        self.scale_hint_text = ""
        self.scale_hint_timer = QTimer(self)
        self.scale_hint_timer.setSingleShot(True)
        self.scale_hint_timer.setInterval(1000)
        self.scale_hint_timer.timeout.connect(self.clear_scale_hint)
        # 吸附提示在「仅吸附瞬间」模式下的淡出计时（时长由对象自己的 snap_hint_duration 决定）。
        self.snap_hint_timer = QTimer(self)
        self.snap_hint_timer.setSingleShot(True)
        self.snap_hint_timer.timeout.connect(self.clear_snap_hint)
        self.drag_origin = None
        # 吸附关系：记录贴到了哪个目标以及相对偏移，跟随窗口时按它重新定位。
        self.snap_target = None
        self.snap_hint = None
        # 吸附提示样式：创建时从全局默认【继承一次】，之后由本对象自己持有。
        # 用户 2026-10-11 明确：全局开关只在初始化时影响对象，之后改全局不追溯已有贴图。
        self.snap_hint_enabled = bool(self.settings.get("sticker_snap_hint_enabled", True))
        self.snap_hint_color = self.settings.get("sticker_snap_hint_color", "#00ad91")
        self.snap_hint_width = int(self.settings.get("sticker_snap_hint_width", 1))
        self.snap_hint_style = self.settings.get("sticker_snap_hint_style", "dash")
        self.snap_hint_mode = self.settings.get("sticker_snap_hint_mode", "always")
        self.snap_hint_dragged = False
        self.snap_hint_duration = int(self.settings.get("sticker_snap_hint_duration", 600))
        self.snap_hint_inset = float(self.settings.get("sticker_snap_hint_inset", 2))
        self.follow_timer = None
        self.snap_candidate_keys = None
        # 拖动开始时枚举一次可见窗口，拖动过程中复用。
        self.snap_windows = None
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        # 贴图像素是源图的物理像素；窗口尺寸必须按屏幕 dpr 折算成 Qt 逻辑尺寸，
        # 否则在 125%/150% 屏上窗口与图片会被整体放大 dpr 倍。
        self._pixmap_ratio = None
        self._setup_animation()
        self._apply_pixmap_ratio()
        self.resize(self.window_size())
        self.update_input_mask()
        self.setFocusPolicy(Qt.StrongFocus)

    def image_corner_radius(self):
        """按图像左上角估算圆角半径（源图像像素）；直角或透明留白返回 0。"""
        if getattr(self, "animated", False) and self._animated_corner_radius is not None:
            # 动图逐帧测圆角没有意义，也会拖慢播放：用首帧测一次后固定。
            return self._animated_corner_radius
        key = (self.pixmap.cacheKey(), self.scale_factor)
        if getattr(self, "_corner_radius_key", None) == key:
            return self._corner_radius
        radius = 0.0
        source = self.pixmap.toImage()
        if not source.isNull():
            # 圆角边缘有抗锯齿，按“接近完全不透明”判定，避免把半透明边缘算进半径。
            opaque = lambda color: color.alpha() >= 250
            limit = min(source.width(), source.height()) // 2
            measured = 0
            for offset in range(limit):
                if opaque(source.pixelColor(offset, 0)) and opaque(source.pixelColor(0, offset)):
                    break
                measured = offset + 1
            else:
                # 整条边都是透明的：不是圆角，按方形描边处理。
                measured = 0
            # 圆角的透明区应是圆弧：对角线同距离处已经不透明，否则视为透明留白。
            if measured and opaque(source.pixelColor(measured, measured)):
                radius = float(measured)
        self._corner_radius_key = key
        self._corner_radius = radius
        if getattr(self, "animated", False):
            self._animated_corner_radius = radius
        return radius

    # ------------------------------------------------------------------
    # 动图（GIF / 动态 WebP）：QMovie 逐帧驱动，其它绘制/交互复用静态路径
    # ------------------------------------------------------------------

    def _setup_animation(self):
        """源文件是动图且未禁用时启用 QMovie；否则保持静态首帧。"""
        self.movie = None
        self.animated = False
        self.animation_paused = False
        self._animated_corner_radius = None
        source = self.source
        if not source or not self.settings.get("sticker_gif_enabled", True):
            if source and Path(source).suffix.casefold() in (".gif", ".webp"):
                get_logger().debug("GIF 动图未启用，按静态首帧显示: %s", source)
            return
        if Path(source).suffix.casefold() not in (".gif", ".webp"):
            return
        movie = QMovie(source, parent=self)
        if not movie.isValid() or movie.frameCount() <= 1:
            movie.deleteLater()
            get_logger().debug("GIF 无有效动画帧，按静态首帧显示: 帧数=%s 源=%s",
                               movie.frameCount(), source)
            return
        if max(movie.frameRect().width(), movie.frameRect().height()) > 4096:
            # 与静态贴图的尺寸上限保持一致：超大 GIF 只显示首帧，避免逐帧解码/缩放卡顿。
            movie.deleteLater()
            get_logger().warning("GIF 尺寸超过 4096，按静态首帧显示: %s", source)
            return
        movie.setCacheMode(QMovie.CacheNone)
        movie.setSpeed(max(25, min(400, self.animation_speed)))
        movie.frameChanged.connect(self._on_animation_frame)
        self.movie = movie
        self.animated = True
        movie.start()
        if not self.settings.get("sticker_gif_autoplay", True):
            self.animation_paused = True
            movie.setPaused(True)
        get_logger().debug(
            "贴图动图初始化: 帧数=%d 速度=%d%% 自动播放=%s 源=%s",
            movie.frameCount(), self.animation_speed,
            not self.animation_paused, source)

    def _stop_animation(self):
        if self.movie is not None:
            try:
                self.movie.stop()
                self.movie.deleteLater()
            except RuntimeError:
                pass
            get_logger().debug("停止 GIF 动图: 源=%s", self.source or "临时图片")
        self.movie = None
        self.animated = False
        self.animation_paused = False

    def _on_animation_frame(self, frame_number):
        # QMovie.frameChanged 在 Qt6 传的是帧号（int），不是 QPixmap；必须自己取当前帧。
        if not self.animated or self.movie is None:
            return
        pixmap = self.movie.currentPixmap()
        if pixmap.isNull():
            get_logger().debug("GIF 当前帧为空: 帧号=%s 源=%s",
                               frame_number, self.source or "临时图片")
            return
        self.original_pixmap = pixmap
        self._render_animation_frame()

    def _render_animation_frame(self):
        """把当前动图帧按现有旋转规则写入 pixmap；不重建遮罩、不重算 padding。"""
        pixmap = self.original_pixmap
        if self.rotation_degrees:
            pixmap = pixmap.transformed(
                QTransform().rotate(self.rotation_degrees), Qt.SmoothTransformation)
        self.pixmap = pixmap
        ratio = self.device_pixel_ratio()
        self.original_pixmap.setDevicePixelRatio(ratio)
        self.pixmap.setDevicePixelRatio(ratio)
        if self.size() != self.window_size():
            # 帧尺寸变化（极少见）才走完整样式刷新，普通 GIF 每帧尺寸一致。
            self.apply_style()
        else:
            self.update()

    def animation_state(self):
        """会话持久化的动图状态；非动图返回 None。"""
        if not self.animated or self.movie is None:
            return None
        return {"speed": self.animation_speed,
                "frame": self.movie.currentFrameNumber(),
                "paused": self.animation_paused}

    def restore_animation_state(self, payload):
        """恢复会话里的速度/帧号/暂停状态；非法值按默认处理。"""
        if not self.animated or self.movie is None or not isinstance(payload, dict):
            return
        speed = payload.get("speed")
        if type(speed) is int and 25 <= speed <= 400:
            self.animation_speed = speed
            self.movie.setSpeed(speed)
        frame = payload.get("frame")
        count = self.movie.frameCount()
        if type(frame) is int and count > 0 and 0 <= frame < count:
            self.movie.jumpToFrame(frame)
        paused = bool(payload.get("paused", False))
        self.animation_paused = paused
        self.movie.setPaused(paused)
        get_logger().debug("恢复 GIF 动图状态: 速度=%d%% 帧=%d/%d 暂停=%s 源=%s",
                           self.animation_speed, self.movie.currentFrameNumber(),
                           self.movie.frameCount(), paused, self.source or "临时图片")

    def set_animation_speed(self, speed):
        if not self.animated or self.movie is None:
            return
        previous = self.animation_speed
        self.animation_speed = max(25, min(400, int(speed)))
        self.movie.setSpeed(self.animation_speed)
        get_logger().info("调整 GIF 播放速度: %d%% → %d%% 源=%s",
                          previous, self.animation_speed, self.source or "临时图片")
        self.state_changed.emit()

    def toggle_animation(self):
        if not self.animated or self.movie is None:
            return
        self.animation_paused = not self.animation_paused
        self.movie.setPaused(self.animation_paused)
        if not self.animation_paused and self.movie.state() != QMovie.Running:
            self.movie.start()
        get_logger().info("GIF 动图%s: 源=%s", "暂停" if self.animation_paused else "继续",
                          self.source or "临时图片")
        self.state_changed.emit()

    def step_animation_frame(self, delta):
        if not self.animated or self.movie is None:
            return
        count = self.movie.frameCount()
        if count <= 0:
            return
        self.animation_paused = True
        self.movie.setPaused(True)
        self.movie.jumpToFrame((self.movie.currentFrameNumber() + delta) % count)
        get_logger().debug("GIF 逐帧: %+d → 第 %d/%d 帧 源=%s",
                           delta, self.movie.currentFrameNumber(), count,
                           self.source or "临时图片")
        self.state_changed.emit()

    def border_corner_radius(self, rect):
        """描边圆角半径（窗口像素）：跟随贴图圆角，直角贴图为 0。"""
        radius = self.image_corner_radius() * self.scale_factor / self.device_pixel_ratio()
        return max(0.0, min(radius, rect.width() / 2.0, rect.height() / 2.0))

    def shadow_blur(self):
        """阴影模糊半径（窗口像素），由阴影强度推导；关闭时为 0。"""
        if not self.shadow_enabled:
            return 0
        return max(2, round(self.settings.get("sticker_shadow_strength", 35) * 0.4))

    def shadow_offset(self):
        """阴影向下偏移（窗口像素），让投影更自然；关闭时为 0。"""
        if not self.shadow_enabled:
            return 0
        return max(1, round(self.shadow_blur() * 0.3))

    def padding(self):
        border = self.settings.get("sticker_border_width", 2) if self.border_enabled else 0
        shadow = round(self.settings.get("sticker_shadow_strength", 35) / 5) if self.shadow_enabled else 0
        return max(0, border, shadow)

    def image_rect(self):
        pad = self.padding()
        return QRect(pad, pad, max(1, self.width() - pad * 2), max(1, self.height() - pad * 2))

    def device_pixel_ratio(self):
        """当前所在屏幕的像素比；拿不到屏幕时按 1.0 处理。"""
        screen = self.screen()
        if screen is None:
            return 1.0
        return max(1.0, float(screen.devicePixelRatio()) or 1.0)

    def _apply_pixmap_ratio(self):
        """把屏幕像素比写到 Pixmap 上，让 Qt 按物理像素 1:1 绘制。"""
        ratio = self.device_pixel_ratio()
        previous = getattr(self, "_pixmap_ratio", None)
        if previous == ratio:
            return
        self._pixmap_ratio = ratio
        self.original_pixmap.setDevicePixelRatio(ratio)
        self.pixmap.setDevicePixelRatio(ratio)
        # 只在比例真的变化时记录（典型是拖到不同缩放的屏）——这正是「贴图窗口或图片
        # 变大/变小」的现场；每帧调用不会产生日志（第 14 条）。
        get_logger().debug(
            '贴图像素比变化: %s → %.2f，物理 %dx%d，逻辑 %dx%d',
            previous, ratio, self.pixmap.width(), self.pixmap.height(),
            max(1, round(self.pixmap.width() / ratio)),
            max(1, round(self.pixmap.height() / ratio)))

    def image_logical_size(self):
        """图像在窗口里的逻辑尺寸（物理像素 ÷ 屏幕 dpr）。"""
        ratio = self.device_pixel_ratio()
        return QSize(max(1, round(self.pixmap.width() / ratio)),
                     max(1, round(self.pixmap.height() / ratio)))

    def window_size(self):
        pad = self.padding()
        image_size = self.image_logical_size() * self.scale_factor
        return QSize(image_size.width() + pad * 2, image_size.height() + pad * 2)

    def moveEvent(self, event):
        super().moveEvent(event)
        # 跨屏拖动后像素比会变，重新对齐 Pixmap 比例与窗口尺寸，保证图片始终 1:1。
        if self._pixmap_ratio != self.device_pixel_ratio():
            self.apply_style()
        self.state_changed.emit()

    def event(self, event):
        result = super().event(event)
        # 窗口跨屏的 DPR 通知可能晚于 moveEvent，且逻辑尺寸不一定变化。
        # 构造期事件也会到达这里，必须等 Pixmap/样式状态初始化完成。
        if event.type() == QEvent.Type.DevicePixelRatioChange and hasattr(self, "_pixmap_ratio"):
            self.apply_style()
        return result

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.update_input_mask()
        self.state_changed.emit()

    def apply_style(self):
        # 描边/阴影开关会改变 padding，而图像是按 padding 内缩绘制的：
        # 只 resize 不补偿位置，内容就会跟着内缩量漂移（关描边时实测漂 5px）。
        # 这里保持“图像锚点”不变：窗口位置按 padding 的变化量反向补偿。
        pad_before = getattr(self, "_pad_cache", None)
        self._apply_pixmap_ratio()
        self.resize(self.window_size())
        pad_after = self.padding()
        if pad_before is not None and pad_before != pad_after:
            delta = pad_before - pad_after
            self.move(self.x() + delta, self.y() + delta)
            get_logger().debug(
                "贴图样式变化补偿: padding %d→%d 位置(%d,%d)→(%d,%d)",
                pad_before, pad_after, self.x() - delta, self.y() - delta, self.x(), self.y())
        self._pad_cache = pad_after
        # 背景/DPR/alpha 改变时即使 QSize 相同，也不能依赖 resizeEvent 更新窗口裁剪。
        self.update_input_mask()
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        rect = self.image_rect()
        if self.background_mode in ("dark_checker", "light_checker"):
            base = QColor("#252525" if self.background_mode == "dark_checker" else "#f0f0f0")
            alternate = QColor("#3b3b3b" if self.background_mode == "dark_checker" else "#c8c8c8")
            tile = checker_tile_size(self.scale_factor)
            for row, top in enumerate(range(rect.top(), rect.bottom() + 1, tile)):
                for column, left in enumerate(range(rect.left(), rect.right() + 1, tile)):
                    painter.fillRect(left, top, min(tile, rect.right() - left + 1),
                                     min(tile, rect.bottom() - top + 1),
                                     base if (row + column) % 2 == 0 else alternate)
        # 先画图片，再画投影：投影裁剪在贴图形状之外（偏移形状减去原形状），
        # 既保证不会压在图片上导致整体变暗，圆角透明处也不会在贴图内部露黑；
        # 图片先用干净画笔状态绘制，避免半透明阴影画笔影响贴图主体亮度。
        painter.drawPixmap(rect, self.pixmap)
        if self.shadow_enabled:
            # 把阴影画到独立透明图层再用 drawPixmap 合成，避免半透明笔刷直接作用于主画布
            #（在 WA_TranslucentBackground 下会让贴图主体被压暗）；阴影只落在贴图形状之外。
            strength = self.settings.get("sticker_shadow_strength", 35)
            color = QColor(self.settings.get("sticker_shadow_color", "#000000"))
            radius = self.border_corner_radius(rect)
            base = 2
            blur = self.shadow_blur()
            shadow_pix = QPixmap(self.size())
            shadow_pix.fill(Qt.transparent)
            sp = QPainter(shadow_pix)
            sp.setRenderHint(QPainter.Antialiasing)
            sp.setPen(Qt.NoPen)
            layers = max(1, blur // 3)
            for k in range(layers):
                off = base + k
                alpha = round(255 * strength / 100 * (1 - k / (layers + 1)))
                shade = QColor(color)
                shade.setAlpha(max(0, alpha))
                sp.setBrush(shade)
                offset_rect = QRectF(rect).translated(off, off)
                outer = QPainterPath()
                if radius > 0.5:
                    outer.addRoundedRect(offset_rect, radius, radius)
                else:
                    outer.addRect(offset_rect)
                inner = QPainterPath()
                if radius > 0.5:
                    inner.addRoundedRect(QRectF(rect), radius, radius)
                else:
                    inner.addRect(QRectF(rect))
                sp.fillPath(outer.subtracted(inner), shade)
            sp.end()
            painter.drawPixmap(0, 0, shadow_pix)
        if self.border_enabled and self.settings.get("sticker_border_width", 2):
            width = float(self.settings.get("sticker_border_width", 2))
            painter.setPen(QPen(QColor(self.settings.get("sticker_border_color", "#168cff")),
                                width))
            # 透明模式下窗口遮罩只保留图像不透明区域，描边因此整体画在图像内侧：
            # 圆角贴图沿圆角走，直角贴图保持直角，两种情况下都不会被裁掉。
            half = width / 2.0
            frame = QRectF(rect).adjusted(half, half, -half, -half)
            radius = self.border_corner_radius(rect)
            painter.setRenderHint(QPainter.Antialiasing, True)
            if radius > 0.5:
                corner = max(0.0, radius - half)
                painter.drawRoundedRect(frame, corner, corner)
            else:
                painter.drawRect(frame)
            painter.setRenderHint(QPainter.Antialiasing, False)
        if self.snap_hint and self.snap_hint_enabled:
            # 吸附虚线画在图像内侧：画在外沿时透明模式会被输入遮罩裁掉，圆角贴图同样跟随圆角。
            # 颜色/线型/线宽/内缩全部取自本对象（创建时从全局默认继承一次，见 __init__）。
            # 虚线框带圆角 ⇒ 必须单独开抗锯齿（上面描边分支结尾把抗锯齿关掉了，见 2026-10-16 统一补齐）。
            painter.setRenderHint(QPainter.Antialiasing, True)
            style = {"solid": Qt.SolidLine, "dash": Qt.DashLine,
                     "dot": Qt.DotLine, "dash_dot": Qt.DashDotLine}.get(
                         self.snap_hint_style, Qt.DashLine)
            painter.setPen(QPen(QColor(self.snap_hint_color), self.snap_hint_width, style))
            radius = self.border_corner_radius(rect)
            inset = max(0.0, self.snap_hint_inset)
            hint_frame = QRectF(rect).adjusted(inset, inset, -inset - 1, -inset - 1)
            if radius > 0.5:
                painter.drawRoundedRect(hint_frame, max(0.0, radius - 0.5),
                                        max(0.0, radius - 0.5))
            else:
                painter.drawRect(hint_frame.toRect())
        if (self.selection_effect_active and
            self.settings.get("sticker_selection_effect_enabled", True)):
            painter.setRenderHint(QPainter.Antialiasing)
            painter.setBrush(Qt.NoBrush)
            strength = self.settings.get("sticker_selection_effect_strength", 30)
            # 光晕围绕图像矩形向内画：原来按窗口矩形画会落在 padding 上，
            # 透明模式下被输入遮罩裁掉；圆角半径也跟随贴图圆角，不再固定 5px。
            radius = self.border_corner_radius(rect)
            for color, width, inset in ((QColor(40, 139, 255, round(42 * strength / 100)), 8, 5),
                                        (QColor(40, 139, 255, round(96 * strength / 100)), 5, 3),
                                        (QColor(72, 165, 255, round(230 * strength / 100)), 2, 2)):
                painter.setPen(QPen(color, width, Qt.SolidLine, Qt.RoundCap,
                                    Qt.RoundJoin))
                corner = max(0.0, radius - inset)
                painter.drawRoundedRect(QRectF(rect).adjusted(
                    inset, inset, -inset - 1, -inset - 1), corner, corner)
        if self.scale_hint_text:
            painter.save()
            painter.setRenderHint(QPainter.Antialiasing)
            font = painter.font()
            font.setBold(True)
            font.setPixelSize(13)
            painter.setFont(font)
            width = painter.fontMetrics().horizontalAdvance(self.scale_hint_text) + 20
            hint = QRect((self.width() - width) // 2,
                         max(4, (self.height() - 28) // 2), width, 28)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(18, 42, 46, 225))
            painter.drawRoundedRect(hint, 6, 6)
            painter.setPen(QColor("#ffffff"))
            painter.drawText(hint, Qt.AlignCenter, self.scale_hint_text)
            painter.restore()

    def show_scale_hint(self):
        """显示当前缩放百分比，并在一秒后自动清除。"""
        self.scale_hint_text = f"{round(self.scale_factor * 100)}%"
        self.scale_hint_timer.start()
        self.update()

    def clear_scale_hint(self):
        self.scale_hint_text = ""
        self.update()

    def set_background_mode(self, mode):
        if mode not in ("transparent", "pseudo", "dark_checker", "light_checker"):
            get_logger().warning("忽略无效贴图背景模式: %r", mode)
            return
        self.background_mode = mode
        self.update_input_mask()
        self.update()
        self.state_changed.emit()

    def update_input_mask(self):
        """按窗口逻辑坐标构造遮罩；setMask 同时影响命中与可见区域。

        圆角是靠这个区域裁出来的（逐像素二值），**刻意不做抗锯齿**：同一个区域还要承担
        "圆角外侧不吃鼠标"的命中语义，改成抗锯齿绘制就得另建命中判定 + `WM_NCHITTEST`
        穿透，属于交互语义变更，已评估并放弃（见 ACCEPTANCE.md「已知限制」与 AGENTS D48）。
        """
        if getattr(self, "animated", False):
            # 动图每帧 alpha 都可能变化；逐帧 createAlphaMask 会明显拖慢播放。
            # 折中：动图使用整块窗口（等价 pseudo），透明区域也可拖动/命中。
            self.clearMask()
            return
        rect = self.image_rect()
        key = (self.pixmap.cacheKey(), self.pixmap.devicePixelRatio(),
               self.size().width(), self.size().height(), rect, self.background_mode,
               self.shadow_enabled, self.shadow_blur(), self.shadow_offset())
        if getattr(self, "_input_mask_log_key", None) != key:
            self._input_mask_log_key = key
            get_logger().debug(
                "贴图透明遮罩更新: 模式=%s 图像=%dx%d DPR=%.2f 逻辑区域=%s 阴影=%s",
                self.background_mode, self.pixmap.width(), self.pixmap.height(),
                self.pixmap.devicePixelRatio(), rect, self.shadow_enabled)
        if self.background_mode != "transparent" or self.pixmap.isNull():
            self.clearMask()
            return
        mask = QImage(self.size(), QImage.Format_Mono)
        mask.fill(0)
        source = self.pixmap.toImage()
        if source.isNull() or not source.hasAlphaChannel():
            self.clearMask()
            return
        opaque = source.createAlphaMask().scaled(
            max(1, rect.width()), max(1, rect.height()), Qt.IgnoreAspectRatio,
            Qt.SmoothTransformation)
        # 2026-10-14：createAlphaMask/scaled 保留源 Pixmap 的 DPR；这里已缩到
        # 逻辑尺寸，点式 drawImage 会再除一次 DPR，把右上/右下/左下透明角挪到
        # 图片内部。窗口 QRegion 必须使用 DPR=1 的逻辑栅格，不能改源图来补偿。
        opaque.setDevicePixelRatio(1.0)
        painter = QPainter(mask)
        painter.drawImage(rect.topLeft(), opaque)
        painter.end()
        region = QRegion(QBitmap.fromImage(mask))
        # 阴影画在内边距里、绕贴图一圈，需把这块区域也纳入命中区，否则阴影会被遮罩裁掉看不见。
        if self.shadow_enabled:
            margin = self.shadow_blur() + self.shadow_offset() + 1
            expanded = rect.adjusted(-margin, -margin, margin, margin) & self.rect()
            region |= QRegion(expanded)
        self.setMask(region)

    def clear_snap_hint(self):
        """清掉当前吸附提示虚线（淡出计时到期或被开关关闭时调用）。"""
        self.snap_hint_timer.stop()
        if self.snap_hint is not None:
            self.snap_hint = None
            self.update()

    def toggle_snap_hint(self):
        """右键菜单：开/关这张贴图的吸附提示虚线（只影响显示，不动吸附关系与跟随）。"""
        self.snap_hint_enabled = not self.snap_hint_enabled
        if not self.snap_hint_enabled:
            self.snap_hint = None
        self.update()
        self.state_changed.emit()
        get_logger().info(
            "贴图吸附提示显示: %s（源=%s）",
            "开" if self.snap_hint_enabled else "关", self.source or "临时图片")

    def mousePressEvent(self, event):
        # 记录全局鼠标到窗口左上角的偏移，跨显示器拖动仍保持原抓取位置。
        self.activated.emit()
        self._drag_out_start = None
        if (event.button() == Qt.LeftButton and not self.locked
                and self.settings.get("sticker_drag_enabled", True)
                and event.modifiers() & Qt.ShiftModifier):
            # Shift+左键 = 拖出到其他应用；不进入移动/吸附流程。
            self._drag_out_start = event.globalPosition().toPoint()
            self.selection_requested.emit(self, False)
            self.setFocus()
            return
        if event.button() == Qt.LeftButton:
            # 本次按下是否真的拖动过：决定松开后吸附提示是保留还是清掉（见 mouseReleaseEvent）。
            self.snap_hint_dragged = False
            self.selection_requested.emit(self, bool(event.modifiers() & Qt.ControlModifier))
            self.setFocus()
        if event.button() == Qt.RightButton:
            show_menu(self, event.globalPosition().toPoint())
        elif event.button() == Qt.LeftButton and not self.locked:
            cursor = event.globalPosition().toPoint()
            self.drag_origin = cursor - self.pos()
            get_logger().debug(
                "开始拖动贴图: 位置(%d,%d) 尺寸(%dx%d) 源=%s 吸附=%s 目标=%s 阈值=%s",
                self.x(), self.y(), self.width(), self.height(), self.source or "临时图片",
                self.snap_enabled(), self.settings.get("sticker_snap_targets", "both"),
                self.settings.get("sticker_snap_threshold", 8))
            self.begin_snap_session(cursor)

    def focusInEvent(self, event):
        super().focusInEvent(event)
        self.focus_changed.emit(self, True)

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        self.focus_changed.emit(self, False)

    def mouseMoveEvent(self, event):
        """以全局坐标拖动窗口，跨显示器时不重置抓取偏移；按住 Alt 临时不吸附。"""
        if self._drag_out_start is not None:
            cursor = event.globalPosition().toPoint()
            if (cursor - self._drag_out_start).manhattanLength() >= QApplication.startDragDistance():
                self._start_drag_out()
            return
        if self.drag_origin is None:
            return
        cursor = event.globalPosition().toPoint()
        position = cursor - self.drag_origin
        if self.snap_enabled() and not (event.modifiers() & Qt.AltModifier):
            position = self.apply_snap(position, cursor)
        else:
            self.release_snap()
        old_position = self.pos()
        self.move(position)
        delta = self.pos() - old_position
        if delta.x() or delta.y():
            self.snap_hint_dragged = True   # 真的移动过 ⇒ 松开后按模式保留/淡出提示
            self.batch_moved.emit(self, delta)

    def mouseReleaseEvent(self, event):
        """释放拖动状态，避免下一次鼠标移动继续平移。"""
        self._drag_out_start = None
        self.drag_origin = None
        self.refresh_snap_relation()
        # 吸附提示虚线的收尾（只动提示，不动 snap_target —— 吸附关系与跟随照旧）：
        # 判据是「这次按下后到底拖动过没有」，不是 snap_target —— 后者在松开时可能已被
        # refresh_snap_relation() 改写，用它会导致提示被立刻掐掉（表现为“闪一下就没”）。
        # · 没拖动（单纯点一下贴图）⇒ 直接清掉，绝不显示；
        # · 拖动过 + mode=always（默认）⇒ 常显，直到吸附关系解除（release_snap 清）；
        # · 拖动过 + mode=drag ⇒ 按 snap_hint_duration 淡出（0 = 立即消失）。
        if self.snap_hint is not None:
            if not getattr(self, "snap_hint_dragged", False):
                # 单纯点一下：按【当前吸附状态】重新推导，而不是无条件清掉 ——
                # 贴图本来就贴着目标时，提示属于“常显”，点几下不该把它弄没（用户反馈）。
                # 没吸附（或总开关关掉）时推导结果就是 None ⇒ 也不会“点一下闪出来”。
                edge = (self.snap_target or {}).get("edge") if self.snap_target else None
                self.snap_hint = edge if (edge and self.snap_hint_enabled) else None
                self.update()
            elif self.snap_hint_mode == "drag":
                if self.snap_hint_duration > 0:
                    self.snap_hint_timer.start(self.snap_hint_duration)
                else:
                    self.clear_snap_hint()
        self.state_changed.emit()

    # ------------------------------------------------------------------
    # 文件拖放：拖入替换/新建，Shift+左键拖出到其他应用
    # ------------------------------------------------------------------

    def _drag_enabled(self):
        return bool(self.settings.get("sticker_drag_enabled", True))

    def _start_drag_out(self):
        """Shift+左键拖出：同时提供文件 URL 与图片数据，由目标应用选择。"""
        if self._drag_out_active:
            return
        self._drag_out_active = True
        self._drag_out_start = None
        source = self.source or "临时图片"
        try:
            mime = QMimeData()
            image = self.pixmap.toImage()
            if not image.isNull():
                mime.setImageData(image)
            has_url = bool(self.source and Path(self.source).is_file())
            if has_url:
                mime.setUrls([QUrl.fromLocalFile(str(Path(self.source).resolve()))])
            drag = QDrag(self)
            drag.setMimeData(mime)
            preview = self.pixmap
            if preview.width() > 360:
                preview = preview.scaledToWidth(360, Qt.SmoothTransformation)
            drag.setPixmap(preview)
            hotspot = self.mapFromGlobal(QCursor.pos())
            hotspot.setX(max(0, min(hotspot.x(), max(0, preview.width() - 1))))
            hotspot.setY(max(0, min(hotspot.y(), max(0, preview.height() - 1))))
            drag.setHotSpot(hotspot)
            get_logger().debug("开始拖出贴图: 源=%s 文件URL=%s 图片=%s",
                               source, has_url, not image.isNull())
            action = drag.exec(Qt.CopyAction)
            get_logger().info("拖出贴图结束: 动作=%s 源=%s", int(action), source)
        except Exception:  # noqa: BLE001 拖出失败要留现场，Qt 事件异常默认会被吞
            get_logger().exception("拖出贴图失败: 源=%s", source)
        finally:
            self._drag_out_active = False
            self._drag_out_start = None

    @staticmethod
    def _dropped_image_paths(mime):
        paths = []
        for url in mime.urls():
            if not url.isLocalFile():
                continue
            path = Path(url.toLocalFile())
            if path.is_file() and path.suffix.casefold() in IMAGE_FILE_SUFFIXES:
                paths.append(str(path))
        return paths

    def dragEnterEvent(self, event):
        paths = self._dropped_image_paths(event.mimeData())
        if not self._drag_enabled():
            get_logger().debug("贴图拖入忽略: 拖放总开关已关闭 源=%s", self.source or "临时图片")
            return
        if paths:
            get_logger().debug("贴图拖入可接收: 文件=%d 源=%s",
                               len(paths), self.source or "临时图片")
            event.acceptProposedAction()
        else:
            get_logger().debug("贴图拖入忽略: 没有可用的本地图片 URL 源=%s",
                               self.source or "临时图片")

    def dragMoveEvent(self, event):
        if self._drag_enabled() and self._dropped_image_paths(event.mimeData()):
            event.acceptProposedAction()

    def dropEvent(self, event):
        paths = self._dropped_image_paths(event.mimeData())
        manager = getattr(self, "manager", None)
        source = self.source or "临时图片"
        if not self._drag_enabled():
            get_logger().debug("贴图拖入丢弃: 拖放总开关已关闭 源=%s", source)
            return
        if not paths:
            get_logger().debug("贴图拖入丢弃: 没有可用的本地图片 URL 源=%s", source)
            return
        if manager is None:
            get_logger().warning("贴图拖入失败: 当前贴图没有关联管理器 源=%s 文件=%d",
                                 source, len(paths))
            return
        new_only = bool(event.modifiers() & Qt.ShiftModifier) or \
            not self.settings.get("sticker_drag_replace", True)
        # QDropEvent 提供的是窗口内局部坐标；Qt6 的 QDropEvent 没有 globalPosition()。
        anchor = self.mapToGlobal(event.position().toPoint())
        mode = "新建" if new_only else "替换"
        try:
            if new_only:
                added, failed = manager.add_paths(paths, position=anchor)
            else:
                added, failed = manager.add_paths(paths, replace=self, position=anchor)
        except Exception:  # noqa: BLE001 事件异常默认被 Qt 吞掉，必须留现场
            get_logger().exception("贴图拖入处理失败: 模式=%s 文件=%d 源=%s 落点=(%d,%d)",
                                   mode, len(paths), source, anchor.x(), anchor.y())
            return
        get_logger().info("贴图拖入完成: 模式=%s 文件=%d 成功=%d 失败=%d 落点=(%d,%d) 源=%s",
                          mode, len(paths), len(added), len(failed),
                          anchor.x(), anchor.y(), source)
        event.acceptProposedAction()

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
        get_logger().debug(
            "拖动开始: 鼠标(%d,%d) 可吸附窗口 %d 个 鼠标下窗口=%r",
            cursor.x(), cursor.y(), len(self.snap_windows),
            (under.title or under.class_name) if under else None)

    def snap_candidates(self, cursor):
        """按配置收集屏幕、普通窗口和其他可见贴图的边缘。"""
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
            manager = getattr(self, "manager", None)
            if manager is not None:
                moving = manager.selected_items if self in manager.selected_items else {self}
                for candidate in manager.items:
                    if candidate in moving or not candidate.isVisible():
                        continue
                    rect = candidate.geometry()
                    targets.append({"key": f"sticker{candidate.session_id}", "kind": "sticker",
                                    "sticker_id": candidate.session_id,
                                    "title": Path(candidate.source).name if candidate.source else "贴图",
                                    "left": rect.x(), "top": rect.y(),
                                    "right": rect.x() + rect.width(),
                                    "bottom": rect.y() + rect.height(),
                                    "allow_outside": True, "priority": 2})
        keys = tuple(item["key"] for item in targets)
        # 拖动时每帧都会查询，候选集合不变就只记一次日志。
        if keys != self.snap_candidate_keys:
            self.snap_candidate_keys = keys
            get_logger().debug(
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
        log_every(get_logger(), logging.DEBUG, "snap-probe", state, 0.5,
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
        if target["kind"] in ("window", "sticker"):
            relation["rel_x"] = position.x() - target["left"]
            relation["rel_y"] = position.y() - target["top"]
        if target["kind"] == "window":
            relation["hwnd"] = target["hwnd"]
            relation["follow"] = bool(self.settings.get("sticker_follow_window", True))
        elif target["kind"] == "sticker":
            relation["sticker_id"] = target["sticker_id"]
            relation["follow"] = (bool(self.settings.get("sticker_follow_sticker", True)) and
                                   self.can_follow_sticker(target["sticker_id"]))
        # 相对偏移随拖动每帧变化，比较身份时要排除，否则每次移动都记一条日志。
        identity = {key: relation[key] for key in
                    ("target", "key", "edge", "title", "hwnd", "sticker_id")
                    if key in relation}
        previous = {key: self.snap_target[key] for key in identity
                    if self.snap_target and key in self.snap_target}
        changed = identity != previous
        if not changed and self.snap_target:
            relation["follow"] = self.snap_target.get("follow", self.following())
        self.snap_target = relation
        self.snap_hint = edge
        if not changed:
            return
        target_label = {"window": "窗口", "sticker": "贴图",
                        "screen": "屏幕工作区"}.get(relation["target"], "目标")
        get_logger().info(
            "贴图已吸附到%s %s：%s 边，位置(%d,%d)",
            target_label,
            relation["title"] or relation["key"], edge, position.x(), position.y())
        if relation["target"] == "window" and relation.get("follow"):
            self.start_follow()
        elif relation["target"] == "sticker" and relation.get("follow"):
            self.start_follow()
        elif relation["target"] not in ("window", "sticker") or not relation.get("follow"):
            self.stop_follow()

    def can_follow_sticker(self, target_id):
        """拒绝形成贴图跟随环路；跟随关系保持单向有向无环。"""
        manager = getattr(self, "manager", None)
        target = next((item for item in manager.items
                       if item.session_id == target_id), None) if manager else None
        visited = set()
        while target is not None:
            if target is self or target.session_id in visited:
                return False
            visited.add(target.session_id)
            relation = target.snap_target or {}
            if relation.get("target") != "sticker" or not target.following():
                break
            next_id = relation.get("sticker_id")
            target = next((item for item in manager.items
                           if item.session_id == next_id), None)
        return True

    def release_snap(self):
        """离开吸附目标，或在菜单里手动解除吸附与跟随。"""
        if self.snap_target is None:
            return
        get_logger().info("贴图解除吸附: %s %s",
                                            self.snap_target.get("target"),
                                            self.snap_target.get("title") or self.snap_target.get("key"))
        self.snap_target = None
        self.snap_hint = None
        self.stop_follow()

    def refresh_snap_relation(self):
        """位置或尺寸变化后重新记录与目标的相对偏移。"""
        relation = self.snap_target
        if not relation:
            return
        if relation.get("target") == "window":
            rect = window_logical_rect(relation.get("hwnd"))
            if rect is None:
                return
            target_x, target_y = rect.left(), rect.top()
        elif relation.get("target") == "sticker":
            manager = getattr(self, "manager", None)
            target = next((item for item in manager.items
                           if item.session_id == relation.get("sticker_id")), None) if manager else None
            if target is None:
                return
            target_x, target_y = target.x(), target.y()
        else:
            return
        relation["rel_x"] = self.x() - target_x
        relation["rel_y"] = self.y() - target_y

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
        get_logger().info("开启窗口跟随: hwnd=%s 标题=%r 间隔=%dms",
                                            (self.snap_target or {}).get("hwnd"),
                                            (self.snap_target or {}).get("title"), interval)

    def stop_follow(self):
        """停止跟随，贴图保留在当前位置。"""
        if self.follow_timer is None or not self.follow_timer.isActive():
            return
        self.follow_timer.stop()
        get_logger().info("停止窗口跟随: hwnd=%s 标题=%r",
                                            (self.snap_target or {}).get("hwnd"),
                                            (self.snap_target or {}).get("title"))

    def toggle_follow(self):
        """右键菜单切换当前吸附目标的跟随状态。"""
        relation = self.snap_target or {}
        if self.following():
            relation["follow"] = False
            self.stop_follow()
        elif relation.get("target") == "window":
            relation["follow"] = True
            self.start_follow()
        elif (relation.get("target") == "sticker" and
              self.can_follow_sticker(relation.get("sticker_id"))):
            relation["follow"] = True
            self.start_follow()
        else:
            get_logger().debug("贴图没有可跟随的吸附目标，跳过跟随切换")

    def poll_follow(self):
        """按目标窗口或贴图的最新位置移动；目标关闭时自动解除吸附。"""
        relation = self.snap_target or {}
        if relation.get("target") == "sticker":
            manager = getattr(self, "manager", None)
            target = next((item for item in manager.items
                           if item.session_id == relation.get("sticker_id")), None) if manager else None
            if target is None:
                self.snap_target = None
                self.snap_hint = None
                self.stop_follow()
                return
            if not target.isVisible():
                return
            expected = QPoint(target.x() + relation.get("rel_x", 0),
                              target.y() + relation.get("rel_y", 0))
            if expected != self.pos():
                self.move(expected)
            return
        handle = relation.get("hwnd")
        rect = window_logical_rect(handle)
        if rect is None:
            if not window_present(handle):
                # 只有窗口真的被销毁才解除吸附。
                get_logger().warning(
                    "跟随的目标窗口已关闭，自动解除吸附: hwnd=%s 标题=%r",
                    handle, relation.get("title"))
                self.snap_target = None
                self.snap_hint = None
                self.stop_follow()
                return
            # 最小化或隐藏时保留吸附关系，等窗口恢复后继续跟随。
            log_every(get_logger(), logging.DEBUG, "follow-idle",
                      handle, 2.0,
                      "目标窗口已最小化或隐藏，暂停跟随: hwnd=%s 标题=%r",
                      handle, relation.get("title"))
            return
        expected = QPoint(rect.left() + relation.get("rel_x", 0), rect.top() + relation.get("rel_y", 0))
        if expected != self.pos():
            # 跟随是定时器轮询，同一位置最多每秒记一次。
            log_every(get_logger(), logging.DEBUG, "follow",
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
        get_logger().info("恢复贴图吸附: 窗口 %r（%s 边）",
                                            snap.get("title") or snap.get("hwnd"), snap.get("edge"))

    def apply_restored_sticker_snap(self, snap, target):
        """恢复对另一张贴图的相对位置和跟随关系。"""
        self.snap_target = {key: snap[key] for key in
                            ("target", "key", "edge", "title", "sticker_id",
                             "rel_x", "rel_y", "follow") if key in snap}
        self.snap_hint = snap.get("edge")
        self.move(target.x() + snap.get("rel_x", 0), target.y() + snap.get("rel_y", 0))
        if snap.get("follow", self.settings.get("sticker_follow_sticker", True)) and \
                self.can_follow_sticker(target.session_id):
            self.snap_target["follow"] = True
            self.start_follow()
        else:
            self.snap_target["follow"] = False

    def wheelEvent(self, event):
        """未锁定时按固定倍率缩放，并限制最小、最大尺寸。"""
        if self.locked:
            return
        factor = 1.1 if event.angleDelta().y() > 0 else 1 / 1.1
        self.scale_factor = min(10, max(0.1, self.scale_factor * factor))
        self.apply_style()
        self.batch_scaled.emit(self, factor)
        self.show_scale_hint()
        event.accept()

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
        self.state_changed.emit()

    def set_opacity(self, value):
        self.setWindowOpacity(value)
        self.batch_opacity_changed.emit(self, value)
        self.state_changed.emit()

    def toggle_click_through(self):
        """穿透后无法右键自身，需通过恢复交互的全局热键重新启用输入。"""
        self.click_through = not self.click_through
        self.setWindowFlag(Qt.WindowTransparentForInput, self.click_through)
        self.show()
        self.state_changed.emit()

    def reset_size(self):
        """恢复原始像素大小，不更换贴图内容。"""
        self.scale_factor = 1.0
        self.apply_style()
        self.state_changed.emit()

    def rotate(self, degrees):
        """按指定角度旋转贴图显示，不修改源图片文件。"""
        self.rotation_degrees = (self.rotation_degrees + degrees) % 360
        if self.animated:
            self._render_animation_frame()
        else:
            self.pixmap = self.original_pixmap.transformed(
                QTransform().rotate(self.rotation_degrees), Qt.SmoothTransformation)
            self.apply_style()
        self.state_changed.emit()

    def reset_rotation(self):
        self.rotation_degrees = 0
        if self.animated:
            self._render_animation_frame()
        else:
            self.pixmap = QPixmap(self.original_pixmap)
            self.apply_style()
        self.state_changed.emit()

    def replace_image(self, image, source):
        """历史图片轮换时保留贴图窗口的当前位置和缩放倍率。"""
        self._stop_animation()
        self._animated_corner_radius = None
        self.source = str(source)
        self.image = None
        self.origin = None
        self.original_pixmap = QPixmap.fromImage(image)
        self.rotation_degrees = 0
        self.pixmap = QPixmap(self.original_pixmap)
        # 新文件可能是 GIF：重新判断是否需要动图播放。
        self._setup_animation()
        # 新 Pixmap 没有旧对象的 DPR；同屏也必须重新写入，不能命中比例缓存早退。
        self._pixmap_ratio = None
        self._apply_pixmap_ratio()
        self.resize(self.window_size())
        # 同尺寸替换不保证产生 resizeEvent；新 alpha/source 必须立即生效并安排保存。
        self.update_input_mask()
        self.update()
        self.state_changed.emit()
        get_logger().debug(
            "替换贴图完成: 物理=%dx%d DPR=%.2f 缩放=%.2f",
            self.pixmap.width(), self.pixmap.height(), self._pixmap_ratio, self.scale_factor)

    def closeEvent(self, event):
        self._stop_animation()
        self.closed.emit()
        super().closeEvent(event)

    def copy_image(self):
        """复制原始图像而非带窗口缩放效果的预览；动图复制当前帧。"""
        if self.animated and self.movie is not None:
            image = self.movie.currentImage()
            if not image.isNull():
                return copy_image_to_clipboard(image)
        image = QImage(self.source) if self.source else self.image
        if image is None or image.isNull():
            image = self.original_pixmap.toImage()
        return copy_image_to_clipboard(image)

    def toggle_top(self):
        """切换置顶标志后重新显示窗口，使窗口管理器应用新状态。"""
        self.always_on_top = not self.always_on_top
        self.setWindowFlag(Qt.WindowStaysOnTopHint, self.always_on_top)
        self.show()

    def toggle_border(self):
        self.border_enabled = not self.border_enabled
        self.apply_style()
        self.state_changed.emit()

    def toggle_shadow(self):
        self.shadow_enabled = not self.shadow_enabled
        self.apply_style()
        self.state_changed.emit()

    def state(self):
        """返回可写入 JSON 的窗口状态，源路径必须为字符串。"""
        snap = (dict(self.snap_target) if self.snap_target
                and self.snap_target.get("target") in ("window", "sticker") else None)
        if snap is not None and snap.get("target") == "window":
            # 句柄必须转成普通整数才能写入 JSON。
            snap["hwnd"] = int(snap.get("hwnd", 0))
        state = {"id": self.session_id, "source": self.source, "x": self.x(), "y": self.y(),
            "width": self.width(), "height": self.height(),
            "scale": self.scale_factor, "opacity": self.windowOpacity(),
            "rotation": self.rotation_degrees,
                "locked": self.locked, "top": self.always_on_top,
                "click_through": self.click_through, "border": self.border_enabled,
                "shadow": self.shadow_enabled, "background_mode": self.background_mode,
                "snap_hint_enabled": bool(self.snap_hint_enabled),
                "group": self.group_name, "origin": self.origin, "snap": snap}
        animation = self.animation_state()
        if animation:
            state["animation"] = animation
        return state

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
        return origin.get("text") if origin.get("kind") in ("text", "html", "color") else None

    def origin_paths(self):
        """文件贴图记录的原始路径，恢复后仍可直接打开。"""
        origin = self.origin or {}
        if origin.get("kind") != "files":
            return []
        return [path for path in origin.get("paths") or [] if isinstance(path, str) and os.path.exists(path)]

    def copy_origin_text(self):
        """把文字/颜色或富文本内容放回剪贴板。"""
        text = self.origin_text()
        if (self.origin or {}).get("kind") == "html":
            from PySide6.QtCore import QMimeData
            mime = QMimeData()
            mime.setHtml(self.origin.get("html", ""))
            mime.setText(text or "")
            QGuiApplication.clipboard().setMimeData(mime)
        elif text:
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
