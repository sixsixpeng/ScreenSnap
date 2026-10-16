"""截图行为、画面识别和选区设置。"""

from PySide6.QtCore import QSignalBlocker, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QKeySequence, QPainter, QPen
from PySide6.QtWidgets import (QCheckBox, QComboBox, QHBoxLayout,
                               QSizePolicy, QSlider, QVBoxLayout, QWidget, QLabel)

from config.config_manager import (HINT_BAR_PRESETS, HINT_BAR_STYLE_FIELDS,
                                   HINT_BAR_WARNING_PRESETS, HINT_ITEM_IDS, HINT_LABELS,
                                   INTRUDER_WARNING_ITEMS)
from logger import get_logger
from ui.widgets.color_button import ColorButton
from ui.widgets.hint_order_list import HintOrderList
from ui.widgets.hint_preview import HintBarPreview, HintBarStylePreview
from ui.widgets.hotkey_edit import HotkeyEdit
from ui.widgets.tooltip import SettingsPage


def rich_tooltip(*args, **kwargs):
    """延迟导入避免 ui 包与 editor.toolbar_widget 的循环依赖。"""
    from editor.toolbar_widget import rich_tooltip as _rich_tooltip
    return _rich_tooltip(*args, **kwargs)


def _clamped_int(data, key, default, low, high):
    """从配置读一个整数并夹到 [low, high]；非法或缺失时返回默认值。"""
    try:
        value = int((data or {}).get(key, default))
    except (TypeError, ValueError):
        return default
    return min(max(value, low), high)


class ScreenshotEffectPreview(QWidget):
    def __init__(self, config, kind):
        super().__init__()
        self.config = config
        self.kind = kind
        self.setMinimumWidth(260)
        self.setFixedHeight(100)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def refresh(self):
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        area = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        painter.fillRect(area, QColor("#e8edf0"))
        painter.fillRect(area.adjusted(12, 12, -area.width() * 0.62, -12), QColor("#4b91a5"))
        painter.fillRect(area.adjusted(area.width() * 0.46, 12, -12, -12), QColor("#f7f9fa"))
        painter.setPen(QPen(QColor("#b2c0c6"), 1))
        for row in range(3):
            y = int(area.top() + 30 + row * 16)
            painter.drawLine(int(area.left() + area.width() * 0.5), y, int(area.right() - 16), y)
        if self.kind == "crosshair":
            self._paint_crosshair(painter, area)
        elif self.kind == "magnifier":
            self._paint_magnifier(painter, area)
        elif self.kind == "ruler":
            self._paint_ruler(painter, area)
        elif self.kind == "hover":
            self._paint_hover(painter, area, show_badge=True)
        elif self.kind == "hover_box":
            self._paint_hover(painter, area, show_badge=False)
        elif self.kind == "hover_badge":
            self._paint_hover_badge(painter, area)
        else:
            selected = area.adjusted(area.width() * 0.23, area.height() * 0.2,
                                     -area.width() * 0.23, -area.height() * 0.2)
            mask = QColor(self.config.data.get("mask_color", "#000000"))
            mask.setAlpha(round(self.config.data.get("mask_opacity", 70) * 2.55))
            painter.fillRect(area, mask)
            painter.save()
            painter.setClipRect(selected)
            painter.fillRect(selected, QColor("#f7f9fa"))
            painter.restore()
            border = QColor(self.config.data.get("selection_border_color", "#168cff"))
            border_w = _clamped_int(self.config.data, "selection_border_width", 1, 1, 4)
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(border, border_w))
            painter.drawRect(selected)
            # 锚点：四角与四边中点尺寸各自独立配置（与遮罩同口径，默认 6 / 6）。
            anchor_color = QColor(self.config.data.get("anchor_color", "#00d7aa"))
            corner = max(5.0, min(float(_clamped_int(self.config.data, "anchor_corner_size", 6, 5, 21)),
                                  min(selected.width(), selected.height()) * 0.4))
            edge = max(4.0, min(float(_clamped_int(self.config.data, "anchor_edge_size", 6, 4, 21)),
                                min(selected.width(), selected.height()) * 0.4))
            fill_style = self.config.data.get("anchor_style") == "fill"
            handles = (
                (selected.topLeft(), corner), (selected.topRight(), corner),
                (selected.bottomLeft(), corner), (selected.bottomRight(), corner),
                (QPointF(selected.center().x(), selected.top()), edge),
                (QPointF(selected.center().x(), selected.bottom()), edge),
                (QPointF(selected.left(), selected.center().y()), edge),
                (QPointF(selected.right(), selected.center().y()), edge),
            )
            for point, size in handles:
                painter.setPen(QPen(anchor_color, 1))
                painter.setBrush(anchor_color if fill_style else Qt.white)
                painter.drawRect(QRectF(point.x() - size / 2, point.y() - size / 2, size, size))
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(QColor("#c3ced6"), 1))
        painter.drawRect(area)

    def _paint_crosshair(self, painter, area):
        """十字线预览：与遮罩三选一的取色口径一致，功能关闭时淡显但仍画出。"""
        if not self.config.data.get("crosshair", True):
            painter.setOpacity(0.38)
        center = area.center()
        width = self.config.data.get("crosshair_width", 1)
        vertical = (int(center.x()), int(area.top()), int(center.x()), int(area.bottom()))
        horizontal = (int(area.left()), int(center.y()), int(area.right()), int(center.y()))
        # 预览与遮罩 _paint_crosshair 同一口径（三选一 + 各样式自己的参数），
        # 不能"选了样式/改了参数，预览却没变化"。
        mode = str(self.config.data.get("crosshair_mode", "invert"))
        if mode == "invert":
            strength = max(0, min(100, int(
                self.config.data.get("crosshair_invert_strength", 100) or 0)))
            gap = max(0, int(self.config.data.get("crosshair_invert_gap", 0) or 0))
            if strength > 0:
                if self.config.data.get("crosshair_invert_halo_enabled", False):
                    # 预览底色偏浅 ⇒ 与遮罩的"亮背景"口径一致，用亮底主线色当补偿描边。
                    painter.setPen(QPen(QColor(self.config.data.get(
                        "crosshair_contrast_light_bg_color", "#000000")), width + 2))
                    painter.drawLine(*vertical)
                    painter.drawLine(*horizontal)
                # 差值混合对预览自身的色块逐像素取反；交叉点只反一次（同遮罩，水平线断开）。
                painter.setCompositionMode(QPainter.CompositionMode_Difference)
                painter.setPen(QPen(QColor(255, 255, 255), width))
                painter.setOpacity(strength / 100.0)
                painter.drawLine(*vertical)
                if gap > 0:
                    painter.drawLine(horizontal[0], horizontal[1],
                                     vertical[0] - gap, horizontal[3])
                    painter.drawLine(vertical[0] + gap, horizontal[1],
                                     horizontal[2], horizontal[3])
                else:
                    painter.drawLine(*horizontal)
                painter.setOpacity(1.0)
                painter.setCompositionMode(QPainter.CompositionMode_SourceOver)
        elif mode == "contrast":
            halo_width = max(0, int(
                self.config.data.get("crosshair_contrast_halo_width", 2) or 0))
            if halo_width > 0:
                # 预览底色以浅灰/近白为主 ⇒ 用「亮底主线色」当主线、另一个颜色当描边。
                painter.setPen(QPen(QColor(self.config.data.get(
                    "crosshair_contrast_dark_bg_color", "#ffffff")), width + halo_width))
                painter.drawLine(*vertical)
                painter.drawLine(*horizontal)
                painter.setPen(QPen(QColor(self.config.data.get(
                    "crosshair_contrast_light_bg_color", "#000000")), width))
            else:
                painter.setPen(QPen(QColor(self.config.data.get("crosshair_color", "#ff0000")), width))
            painter.drawLine(*vertical)
            painter.drawLine(*horizontal)
        else:
            painter.setPen(QPen(QColor(self.config.data.get("crosshair_color", "#ff0000")), width))
            painter.drawLine(*vertical)
            painter.drawLine(*horizontal)
        painter.setOpacity(1.0)

    def _paint_magnifier(self, painter, area):
        """放大镜预览：方形镜片 + 放大后的像素格 + 像素网格（可关）+ 采样点红十字。

        与 `screenshot/magnifier_widget.py::paint_magnifier` 的绘制口径一致（方形、白框、
        网格、红色采样十字），不再画成圆形；镜片按「偏移距离」放到光标右下方，
        可见像素格数随「倍率」变化（倍率越大格子越少、越大），关闭时淡显。
        """
        settings = self.config.data
        if not bool(settings.get("magnifier", True)):
            painter.setOpacity(0.38)
        try:
            zoom = min(max(float(settings.get("magnifier_zoom", 7.0) or 7.0), 3.0), 12.0)
        except (TypeError, ValueError):
            zoom = 7.0
        size = int(settings.get("magnifier_size", 140) or 140)
        side = max(30.0, min(float(size) * 0.42, area.height() * 0.64))
        center = area.center()
        try:
            offset = int(settings.get("magnifier_offset", 32) or 32)
        except (TypeError, ValueError):
            offset = 32
        # 光标用十字示意；放大镜按偏移距离（等比缩小到预览内）放到右下方，并夹回预览区。
        shift = max(0.0, min(float(offset) * 0.5, (area.height() - side) / 2 - 2))
        left = min(max(center.x() + shift - side / 2, area.left() + 2),
                   area.right() - side - 2)
        top = min(max(center.y() + shift - side / 2, area.top() + 2),
                  area.bottom() - side - 2)
        lens = QRectF(left, top, side, side)
        # 光标十字
        painter.setPen(QPen(QColor("#273b44"), 1))
        painter.drawLine(QPointF(center.x() - 5, center.y()), QPointF(center.x() + 5, center.y()))
        painter.drawLine(QPointF(center.x(), center.y() - 5), QPointF(center.x(), center.y() + 5))
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#ffffff"))
        painter.drawRect(lens)
        inner = lens.adjusted(2, 2, -2, -2)
        # 可见像素格数 = 镜片边长 ÷ 倍率：倍率越大格子越少越大（与真实采样一致）。
        cells = int(min(max(round(side / zoom), 3), 12))
        palette = ("#4b91a5", "#f7f9fa", "#b2c0c6", "#00ad91", "#ffd54f", "#e8edf0")
        cell_w, cell_h = inner.width() / cells, inner.height() / cells
        for row in range(cells):
            for column in range(cells):
                painter.setBrush(QColor(palette[(row * 3 + column * 5) % len(palette)]))
                painter.drawRect(QRectF(inner.left() + column * cell_w,
                                        inner.top() + row * cell_h, cell_w, cell_h))
        if settings.get("magnifier_grid", True):
            painter.setPen(QPen(QColor(settings.get("magnifier_grid_color", "#cccccc")), 1, Qt.DotLine))
            painter.setBrush(Qt.NoBrush)
            for index in range(cells + 1):
                x = inner.left() + index * cell_w
                y = inner.top() + index * cell_h
                painter.drawLine(QPointF(x, inner.top()), QPointF(x, inner.bottom()))
                painter.drawLine(QPointF(inner.left(), y), QPointF(inner.right(), y))
        # 采样点十字：开关关闭则不画；颜色取当前设置（与遮罩同源）。
        if bool(settings.get("magnifier_crosshair_enabled", True)):
            painter.setPen(QPen(QColor(settings.get("magnifier_crosshair_color", "#ff5252")), 1.5))
            painter.setBrush(Qt.NoBrush)
            painter.drawLine(QPointF(inner.left(), inner.center().y()),
                             QPointF(inner.right(), inner.center().y()))
            painter.drawLine(QPointF(inner.center().x(), inner.top()),
                             QPointF(inner.center().x(), inner.bottom()))
        painter.setPen(QPen(QColor("#273b44"), 1))
        painter.drawRect(lens)
        painter.setOpacity(1.0)

    def _paint_ruler(self, painter, area):
        """标尺预览：沿预览边缘画像素刻度与数值，颜色/间距/主刻度倍数均取当前设置。"""
        settings = self.config.data
        if not bool(settings.get("ruler_enabled", False)):
            painter.setOpacity(0.38)
        try:
            step = int(settings.get("ruler_step", 10) or 10)
        except (TypeError, ValueError):
            step = 10
        if step not in (10, 20, 50, 100):
            step = 10
        try:
            multiple = int(settings.get("ruler_major_multiple", 5) or 5)
        except (TypeError, ValueError):
            multiple = 5
        if multiple not in (2, 5, 10):
            multiple = 5
        painter.setPen(QPen(QColor(settings.get("ruler_color", "#00ad91")), 1))
        font = painter.font()
        font.setPixelSize(9)
        painter.setFont(font)
        major = step * multiple
        small, big = (5, 9) if step >= 20 else (3, 6)
        left, top = int(area.left()), int(area.top())
        right, bottom = int(area.right()), int(area.bottom())
        for x in range(left, right + 1, step):
            length = big if (x - left) % major == 0 else small
            painter.drawLine(x, top, x, top + length)
            painter.drawLine(x, bottom - length, x, bottom)
            if (x - left) % major == 0 and x > left:
                painter.drawText(x + 2, bottom - length - 2, str(x - left))
        for y in range(top, bottom + 1, step):
            length = big if (y - top) % major == 0 else small
            painter.drawLine(left, y, left + length, y)
            painter.drawLine(right - length, y, right, y)
            if (y - top) % major == 0 and y > top:
                painter.drawText(left + length + 2, y - 2, str(y - top))
        painter.setOpacity(1.0)

    def _paint_hover(self, painter, area, show_badge):
        """候选框预览：遮罩 + 候选区域填充/边框（可选尺寸标签）。"""
        settings = self.config.data
        candidate = area.adjusted(area.width() * 0.28, area.height() * 0.2,
                                  -area.width() * 0.28, -area.height() * 0.24)
        painter.fillRect(area, QColor(0, 0, 0, 153))
        if settings.get("window_hover_fill_mode", "reveal") == "reveal":
            painter.save()
            painter.setClipRect(candidate)
            painter.fillRect(candidate, QColor("#f7f9fa"))
            painter.restore()
        else:
            fill = QColor(settings.get("window_hover_color", "#168CFF"))
            fill.setAlpha(round(settings.get("window_hover_opacity", 35) * 2.55))
            painter.fillRect(candidate, fill)
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(QColor(settings.get("window_hover_border_color", "#168cff")),
                            settings.get("window_hover_border_width", 2)))
        painter.drawRect(candidate)
        if show_badge:
            self._paint_badge(painter, candidate)

    def _paint_hover_badge(self, painter, area):
        """尺寸标签预览：突出候选区域上方的宽高标签（字号与配色随设置变化）。"""
        settings = self.config.data
        candidate = area.adjusted(area.width() * 0.24, area.height() * 0.34,
                                  -area.width() * 0.24, -area.height() * 0.34)
        painter.fillRect(area, QColor(0, 0, 0, 153))
        painter.save()
        painter.setClipRect(candidate)
        painter.fillRect(candidate, QColor("#f7f9fa"))
        painter.restore()
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(QColor(settings.get("window_hover_border_color", "#168cff")), 1, Qt.DashLine))
        painter.drawRect(candidate)
        self._paint_badge(painter, candidate)

    def _paint_badge(self, painter, candidate):
        """尺寸标签：内容/字号取设置，底色与文字色各自可配（与遮罩上的实际徽标同源）。"""
        from screenshot.hint_items import badge_text

        settings = self.config.data
        font_size = max(8, min(int(settings.get("window_hover_font_size", 8) or 8), 20))
        font = painter.font()
        font.setPixelSize(font_size)
        painter.setFont(font)
        content = str(settings.get("window_hover_badge_content", "size_class") or "size_class")
        # 预览用样例元素：宽 120、高 32；带坐标时给一个示意坐标。
        label = badge_text((120, 32), (480, 360), content)
        badge = QRectF(candidate.left(), candidate.top(), candidate.width() * 0.6,
                       max(16.0, font_size * 1.6))
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(settings.get("window_hover_badge_color", "#102A31")))
        painter.drawRoundedRect(badge, 3, 3)
        painter.setPen(QColor(settings.get("window_hover_text_color", "#F4FFFC")))
        painter.drawText(badge, Qt.AlignCenter, label)


class _IntruderWarningList(QWidget):
    """采集自检警示的子项开关：一个复选框对应一类本程序窗口。

    值就是 `{分类 id: 是否参与警示}`，与配置里的 `intruder_warning_items` 一致，
    因此能被 `SettingsPage.sync_controls` 当作复合控件统一回填。
    """

    value_changed = Signal(dict)

    def __init__(self, items, values, parent=None):
        super().__init__(parent)
        self.setToolTip("选择哪些本程序窗口进入截图选区时给出采集自检警示；\n"
                        "取消勾选的类别即使被采进画面也不再提示。需先打开上方总开关。")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        self.boxes = {}
        for item_id, label in items:
            box = QCheckBox(label, self)
            box.setChecked(bool((values or {}).get(item_id, True)))
            box.setToolTip(f"选区包含「{label}」时是否在提示条里给出采集自检警示")
            box.toggled.connect(self._emit)
            self.boxes[item_id] = box
            layout.addWidget(box)

    def _emit(self, *_args):
        self.value_changed.emit(self.value())

    def value(self):
        return {item_id: box.isChecked() for item_id, box in self.boxes.items()}

    def set_value(self, values):
        values = values or {}
        for item_id, box in self.boxes.items():
            blocked = box.blockSignals(True)
            box.setChecked(bool(values.get(item_id, True)))
            box.blockSignals(blocked)

    def set_enabled(self, enabled):
        """总开关联动：关闭时子项整体置灰，但保留勾选状态。"""
        for box in self.boxes.values():
            box.setEnabled(enabled)


class HintBarStyleEditor(QWidget):
    """提示条外观编辑器：预设一键套用整组颜色与圆角，也可逐项自定义。

    值就是配置里的样式字典（`preset` + 文字/填充/描边色 + 圆角开关与半径），与
    `hint_bar_style` / `hint_bar_warning_style` 一致，可被设置页统一回填与重置。
    """

    value_changed = Signal(dict)

    def __init__(self, presets, values, purpose, parent=None):
        super().__init__(parent)
        self._presets = {preset_id: dict(fields) for preset_id, _label, fields in presets}
        values = dict(values or {})
        self.setToolTip("提示条的文字颜色、填充颜色、描边颜色与圆角；\n"
                        "选预设可一键套用整组外观，手动改动任一项后自动记为「自定义」。")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        self.preset = QComboBox(self)
        for preset_id, label, _fields in presets:
            self.preset.addItem(label, preset_id)
        self.preset.addItem("自定义", "custom")
        # 打开设置时先按保存的 preset 选中对应项（含「自定义」），再连接信号，
        # 避免构造期就套用首个预设而覆盖用户已有的自定义颜色。
        index = self.preset.findData(values.get("preset", "custom"))
        self.preset.setCurrentIndex(index if index >= 0 else self.preset.findData("custom"))
        self.preset.setToolTip("一键套用整组提示条外观；手动改动任一项后自动切换为「自定义」")
        layout.addLayout(self._row("预设", self.preset))
        self.text = self._color(values.get("text_color"), f"{purpose}文字颜色")
        self.fill = self._color(values.get("fill_color"), f"{purpose}填充颜色")
        self.border = self._color(values.get("border_color"), f"{purpose}描边颜色")
        layout.addLayout(self._row("文字颜色", self.text))
        layout.addLayout(self._row("填充颜色", self.fill))
        layout.addLayout(self._row("描边颜色", self.border))
        rounded_row = QWidget(self)
        rounded_layout = QHBoxLayout(rounded_row)
        rounded_layout.setContentsMargins(0, 0, 0, 0)
        rounded_layout.setSpacing(8)
        self.rounded = QCheckBox("圆角", rounded_row)
        self.rounded.setChecked(bool(values.get("rounded", True)))
        self.rounded.setToolTip("关闭后提示条四角为直角")
        self.radius = QSlider(Qt.Horizontal, rounded_row)
        self.radius.setRange(0, 20)
        self.radius.setValue(int(values.get("radius", 5) or 0))
        self.radius.setToolTip("提示条圆角半径（0–20 像素），仅在勾选「圆角」时生效")
        self.radius_value = QLabel(str(self.radius.value()), rounded_row)
        self.radius_value.setMinimumWidth(18)
        self.radius_value.setToolTip("当前圆角半径（像素）")
        rounded_layout.addWidget(self.rounded)
        rounded_layout.addWidget(self.radius, 1)
        rounded_layout.addWidget(self.radius_value)
        layout.addLayout(self._row("圆角半径", rounded_row))
        self.preset.currentIndexChanged.connect(self._apply_preset)
        self.rounded.toggled.connect(self._on_change)
        self.radius.valueChanged.connect(self._on_change)
        self._sync_radius()

    def _row(self, caption, widget):
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        label = QLabel(caption, self)
        label.setMinimumWidth(56)
        label.setToolTip(self.toolTip())
        row.addWidget(label)
        row.addWidget(widget, 1)
        return row

    def _color(self, color, purpose):
        button = ColorButton(color or "#000000", lambda _value: self._on_change(), purpose=purpose)
        button.setToolTip(f"选择{purpose}（改后记为「自定义」）")
        return button

    def _sync_radius(self):
        self.radius.setEnabled(self.rounded.isChecked())
        self.radius_value.setText(str(self.radius.value()))

    def _apply_preset(self, index):
        fields = self._presets.get(self.preset.itemData(index))
        if fields:
            for widget in (self.rounded, self.radius):
                widget.blockSignals(True)
            try:
                self.text.set_color(fields["text_color"])
                self.fill.set_color(fields["fill_color"])
                self.border.set_color(fields["border_color"])
                self.rounded.setChecked(bool(fields["rounded"]))
                self.radius.setValue(int(fields["radius"]))
            finally:
                for widget in (self.rounded, self.radius):
                    widget.blockSignals(False)
        self._on_change()

    def _on_change(self, *_args):
        """任一子项变化：刷新圆角显示、回算当前预设名，再把整组值通知出去。"""
        self._sync_radius()
        matched = self._matching_preset()
        self.preset.blockSignals(True)
        self.preset.setCurrentIndex(max(0, self.preset.findData(matched)))
        self.preset.blockSignals(False)
        self.value_changed.emit(self.value())

    def _matching_preset(self):
        current = self.value()
        for preset_id, fields in self._presets.items():
            if all(current.get(field) == fields[field] for field in HINT_BAR_STYLE_FIELDS):
                return preset_id
        return "custom"

    def value(self):
        return {
            "preset": self.preset.currentData(),
            "text_color": self.text.color,
            "fill_color": self.fill.color,
            "border_color": self.border.color,
            "rounded": self.rounded.isChecked(),
            "radius": int(self.radius.value()),
        }

    def set_value(self, values):
        values = dict(values or {})
        widgets = (self.rounded, self.radius, self.preset)
        for widget in widgets:
            widget.blockSignals(True)
        try:
            self.text.set_color(values.get("text_color") or "#ffffff")
            self.fill.set_color(values.get("fill_color") or "#141c22")
            self.border.set_color(values.get("border_color") or "#141c22")
            self.rounded.setChecked(bool(values.get("rounded", True)))
            self.radius.setValue(int(values.get("radius", 5) or 0))
            index = self.preset.findData(values.get("preset", "custom"))
            self.preset.setCurrentIndex(index if index >= 0 else self.preset.findData("custom"))
        finally:
            for widget in widgets:
                widget.blockSignals(False)
        self._sync_radius()


HOVER_STYLE_PRESETS = (
    ("海湾青", "lagoon", {
        "window_hover_color": "#0c887b",
        "window_hover_border_color": "#54e0c5",
        "window_hover_text_color": "#f2fffc",
        "window_hover_badge_color": "#103b3a",
        "window_hover_opacity": 17,
        "window_hover_border_width": 2,
        "window_hover_font_size": 12,
    }),
    ("冰蓝", "ice", {
        "window_hover_color": "#286cb3",
        "window_hover_border_color": "#83c9ff",
        "window_hover_text_color": "#f4faff",
        "window_hover_badge_color": "#152e47",
        "window_hover_opacity": 18,
        "window_hover_border_width": 2,
        "window_hover_font_size": 12,
    }),
    ("琥珀", "amber", {
        "window_hover_color": "#a96812",
        "window_hover_border_color": "#ffd17a",
        "window_hover_text_color": "#fff9ee",
        "window_hover_badge_color": "#402b13",
        "window_hover_opacity": 18,
        "window_hover_border_width": 2,
        "window_hover_font_size": 12,
    }),
    ("玫瑰", "rose", {
        "window_hover_color": "#a8385c",
        "window_hover_border_color": "#ff9eb8",
        "window_hover_text_color": "#fff5f8",
        "window_hover_badge_color": "#421d2b",
        "window_hover_opacity": 17,
        "window_hover_border_width": 2,
        "window_hover_font_size": 12,
    }),
)


class ScreenshotPage(SettingsPage):
    """集中管理从触发截图到确认选区的设置。"""

    def __init__(self, config, changed, recording=None):
        super().__init__(config, changed)
        # 由 settings_window 传入 self.recording.emit：录制快捷键时暂停全局热键
        # （与「设置 > 快捷键」页完全一致的接线；没有它就会一边录一边真的触发截图）。
        self.recording = recording
        self.group("截图时机")
        delay = self.number("capture_delay", "截图延迟 (ms)", 0, 5000,
                            "按下截图快捷键后等待多少毫秒再抓取画面，0 表示立即截图；\n"
                            "用于避开快捷键冲突导致的界面变化，或等待菜单、动画、网页渲染完成；\n"
                            "每 1000 毫秒约等于 1 秒，托盘菜单发起的截图会自动再加 150 毫秒")
        delay.setSingleStep(100)
        self.group("截图内容")
        self.check("cursor", "捕获鼠标", "把鼠标指针画进截图原图；系统光标无法读取时，开关前后结果可能相同")
        self.radios("capture_gap_fill", "显示器间隙填充",
                [("透明", "transparent"), ("纯黑", "black"), ("纯白", "white")],
                "多显示器拼接时，虚拟桌面矩形中不属于任何显示器的区域如何填充；默认透明")
        self.group("截图后")
        self.check("inline_edit", "原地编辑",
                   rich_tooltip("原地编辑", "仅单屏单选区在截图位置编辑；多选区、跨屏选区或关闭此项时使用独立编辑器。"))
        self.radios("capture_after_selection", "截图确认后",
                    [("仅保存，不打开编辑器", "save"), ("进入编辑器", "edit"),
                     ("仅复制到剪贴板", "copy")],
                    rich_tooltip("截图选区后的动作", "普通左键手绘区域松开后执行；UIA/悬停候选左键点击直接进入编辑；\n"
                                 "右键拖拽进入收集态后可继续添加区域，按 Enter 或双击确认并进入编辑；\n"
                                 "已有选区时普通右键双击始终直接保存；\n"
                                 "单区域按原地编辑设置处理，多区域/跨屏进入独立编辑器。此右键流程不受本设置影响；\n"
                                 "快速保存按键始终直接保存，窗口编辑按钮始终可手动打开编辑器；\n"
                                 "选择“仅复制到剪贴板”时不会落盘，也不进入编辑器；全屏、当前显示器和上次区域动作可在下面分别配置。"))
        self.radios("capture_fullscreen_action", "全屏截图后",
                [("进入编辑器", "edit"), ("直接保存", "save")],
                "全屏热键截取后自动进入编辑或保存，不再需要确认整屏选区")
        self.radios("capture_monitor_action", "当前显示器截图后",
                [("进入编辑器", "edit"), ("直接保存", "save")],
                "当前显示器热键截取后自动进入编辑或保存，不再需要确认整屏选区")
        self.radios("capture_repeat_action", "上次区域截图后",
                [("进入编辑器", "edit"), ("直接保存", "save")],
                "上次区域热键截取后自动进入编辑或保存；直接保存沿用原有输出、剪贴板和通知逻辑")
        self.group("截图快捷操作")
        self.check("capture_quick_sticker_enabled", "启用快速贴图快捷键",
                   "选好截图区域后按指定按键立即贴图，不进入编辑")
        self._shortcut("capture_quick_sticker_shortcut", "快速贴图按键", "Space",
                       "选区确认前可按此按键组合直接贴成贴图；支持单个按键或带修饰键的组合")
        self._shortcut("capture_save_shortcut", "快速保存按键", "S",
                       "选区存在时直接保存；沿用输出外观、保存格式、剪贴板和成功通知设置")
        self._shortcut("capture_picker_shortcut", "取色按键", "C",
                       "选区确认前按此键在光标处取色一次；取色态下左键取样会把色值复制到剪贴板")
        self._shortcut("capture_picker_format_shortcut", "切换取色表达式按键", "X",
                       "遮罩内循环切换取色复制到剪贴板的表达式（十六进制 / rgb / rgba / hsl / 全部三行）；切换后按一次取色键即按该表达式复制；顶部提示栏会显示当前表达式。")
        self._shortcut("capture_custom_size_shortcut", "自定义尺寸按键", "F",
                       "选区存在时按此键打开“自定义尺寸”对话框，按指定宽高重建选区")
        self._shortcut("capture_recapture_shortcut", "清除选择按键", "R",
                       "放弃当前冻结画面，回到同一显示器重新框选（不保留当前标注）")
        self._shortcut("capture_window_edit_shortcut", "窗口编辑按键", "E",
                       "把当前选区送进独立编辑器窗口，使用完整工具栏编辑")
        self._shortcut("capture_multi_select_shortcut", "多选编辑模式按键", "Alt+M",
                   "选区阶段进入多选收集；原地编辑中按下会先按下方策略处理当前编辑，再继续选择")
        self.radios("capture_multi_edit_action", "切换多选时处理编辑",
                [("保存当前编辑", "save"), ("丢弃当前编辑", "discard")],
                "原地编辑内容有修改时，无弹窗地按此设置保存或丢弃；未修改则直接继续")
        self._shortcut("capture_copy_shortcut", "仅复制按键", "Y",
                       "把整屏截图（有选区时取选区）写入剪贴板并关闭遮罩，不落盘、不进入编辑器")
        self._shortcut("capture_toolbar_hide_shortcut", "隐藏工具栏按键", "`",
                       "原地编辑中按此键临时隐藏/恢复工具栏，方便查看与操作被它遮住的内容；"
                       "工具栏也可按住空白处拖动，这个键在顶部提示条里有说明")
        self.group("操作提示")
        self.check("capture_hints_enabled", "显示快捷键提示",
                   "在截图时于放大镜旁显示快捷键与操作提示；关闭后整条提示不显示，\n"
                   "放大镜、十字线等其它定位辅助不受影响")
        hint_list = HintOrderList(HINT_ITEM_IDS, HINT_LABELS)
        hint_list.set_value(self.config.data.get("capture_hint_order"))
        self.controls["capture_hint_order"] = hint_list
        self.form.addRow("提示项与顺序", hint_list)
        hint_list.value_changed.connect(
            lambda value: self.update_value("capture_hint_order", value))
        self.number("capture_hint_per_line", "每行提示数", 0, 8,
                    "每行最多显示几个提示项（1–8）；0 表示不限制，只按宽度自动换行。\n"
                    "配合上面的顺序列表，可以把重要的键位放在第一行。")
        self.number("capture_hint_gap", "与放大镜间距 (px)", 0, 40,
                    "提示条与放大镜框之间的间距（0–40 像素）；0 表示提示条边缘紧贴放大镜。\n"
                    "提示条的左右边始终与放大镜的左/右边对齐，不再额外偏移。")
        # 实时预览：与截图遮罩共用同一套文案与排版，改键/勾选/排序后立刻能看到效果。
        self._hint_preview = HintBarPreview(self.config)
        self.previews.append(self._hint_preview)
        self.form.addRow(self._hint_preview)
        self.group("选区内含本程序窗口时提示")
        # 总开关默认关闭：打开后选区包含本程序自身窗口时才在提示条里附加暖色警示。
        master = self.check(
            "intruder_warning_enabled", "选区内含本程序窗口时提示",
            "打开后，截图选区包含本程序自身的窗口（设置窗口、编辑器、通知缩略图、贴图等）时，\n"
            "在放大镜旁的提示条里附加一句暖色警示，提醒这些窗口会被一起采进画面；默认关闭。\n"
            "用下面的子项选择哪些窗口参与提示。")
        intruder_list = _IntruderWarningList(
            INTRUDER_WARNING_ITEMS, self.config.data.get("intruder_warning_items"))
        self.controls["intruder_warning_items"] = intruder_list
        self.form.addRow("警示窗口类型", intruder_list)
        intruder_list.value_changed.connect(
            lambda value: self.update_value("intruder_warning_items", value))
        intruder_list.set_enabled(master.isChecked())
        master.toggled.connect(intruder_list.set_enabled)
        self._intruder_master = master
        self._intruder_list = intruder_list
        # 两套提示条外观互相独立：上面先预览普通样式，再把警示样式接在采集自检警示之后。
        self.group("提示条外观")
        normal_preview = HintBarStylePreview(self.config, "hint_bar_style", warning=False)
        self.previews.append(normal_preview)
        self.form.addRow(normal_preview)
        normal_editor = HintBarStyleEditor(
            HINT_BAR_PRESETS, self.config.data.get("hint_bar_style"), "提示条")
        self.controls["hint_bar_style"] = normal_editor
        self.form.addRow("提示条外观", normal_editor)
        normal_editor.value_changed.connect(
            lambda value: self.update_value("hint_bar_style", value))
        self.group("警示条外观")
        warning_preview = HintBarStylePreview(self.config, "hint_bar_warning_style", warning=True)
        self.previews.append(warning_preview)
        self.form.addRow(warning_preview)
        warning_editor = HintBarStyleEditor(
            HINT_BAR_WARNING_PRESETS, self.config.data.get("hint_bar_warning_style"), "警示条")
        self.controls["hint_bar_warning_style"] = warning_editor
        self.form.addRow("警示条外观", warning_editor)
        warning_editor.value_changed.connect(
            lambda value: self.update_value("hint_bar_warning_style", value))
        # 放大镜/十字线/标尺各自独立成组，每组顶部放一块只反映本组设置的预览。
        self.group("放大镜")
        self._effect_preview("magnifier")
        self.check("magnifier", "实时放大镜", "截图时放大鼠标附近像素")
        self.number("magnifier_size", "放大镜尺寸 (px)", 100, 320,
                    "放大镜为正方形，边长 100–320 像素；采样区域按同一缩放倍率等比换算，\n"
                    "调大尺寸会同时看到更大范围，不会变成更模糊的放大")
        self.decimal("magnifier_zoom", "放大镜倍率 (×)", 3, 12,
                     "放大镜的放大倍率，范围 3–12 倍，步进 0.1。\n"
                     "调大：采样范围更小、看到的内容被放得更大，适合对准 1px 级细节；\n"
                     "调小：一屏能看到的范围更大，便于定位相对位置；\n"
                     "与「放大镜尺寸」配合决定采样区域（采样边长 ≈ 尺寸 ÷ 倍率）。")
        self.number("magnifier_offset", "放大镜偏移 (px)", 8, 80,
                    "放大镜相对光标的偏移距离，范围 8–80 像素，默认 32。\n"
                    "调大：放大镜离光标更远、不易挡住光标附近的内容；\n"
                    "调小：贴近光标更跟手；\n"
                    "靠近屏幕边缘时仍会自动翻到光标另一侧，此值只影响贴边前的默认距离。")
        self.check("magnifier_grid", "放大镜像素网格", "放大镜内叠加像素网格线，便于 1px 级对齐")
        grid_color = ColorButton(config.data["magnifier_grid_color"],
                                 lambda color: self.update_value("magnifier_grid_color", color))
        grid_color.setToolTip("设置放大镜像素网格颜色；仅在放大镜开启时生效")
        grid_color._setting_label = "网格颜色"
        self.controls["magnifier_grid_color"] = grid_color
        self.form.addRow("网格颜色", grid_color)
        self.check("magnifier_crosshair_enabled", "采样点十字",
                   "在放大镜中心画一个小十字，标记当前被采样的那个像素。\n"
                   "打开：一眼看清采样点落在哪（默认）；\n"
                   "关闭：镜片更干净，适合只看放大后的内容时。")
        crosshair_color = ColorButton(
            config.data["magnifier_crosshair_color"],
            lambda color: self.update_value("magnifier_crosshair_color", color))
        crosshair_color.setToolTip("放大镜采样点十字的颜色；建议用与画面反差大的颜色便于辨认")
        crosshair_color._setting_label = "十字颜色"
        self.controls["magnifier_crosshair_color"] = crosshair_color
        self.color_buttons["magnifier_crosshair_color"] = crosshair_color
        self.form.addRow("十字颜色", crosshair_color)
        self.group("十字线")
        self._effect_preview("crosshair")
        self.check("crosshair", "全屏十字线", "在截图遮罩上显示定位辅助线")
        # 2026-10-14（用户要求）：三种样式改为**一组互斥单选按钮**（原来要「自适应开关 + 自适应方式」
        # 两个控件才表达出三种状态），并只把**当前样式真正生效**的参数行留在界面上：
        #   单色 → 十字线颜色；双色描边 → 亮度分界 + 双色 + 描边加宽量；
        #   逐像素反色 → 反色强度 + 交叉点间隙 + 中灰补偿。线宽三种样式共用，始终显示。
        crosshair_rows = {"solid": [], "contrast": [], "invert": []}
        self._crosshair_form = self.form
        self._crosshair_rows = crosshair_rows
        self.radios(
            "crosshair_mode", "十字线样式",
            [("单色", "solid"), ("双色描边", "contrast"), ("逐像素反色", "invert")],
            "单色：用「十字线颜色」画一条实心线；\n"
            "双色描边：按所在区域亮度取两种颜色互为衬底，任意背景都可见；\n"
            "逐像素反色：对线下方像素取反，颜色随背景自然变化（接近中灰时可开中灰补偿）。",
            on_change=lambda value: self._sync_crosshair_rows())
        crosshair_color = ColorButton(config.data["crosshair_color"],
                                      lambda color: self.update_value("crosshair_color", color))
        crosshair_color.setToolTip("仅「单色」样式使用；双色描边与逐像素反色各按自己的参数取色")
        self.controls["crosshair_color"] = crosshair_color
        self.form.addRow("十字线颜色", crosshair_color)
        crosshair_rows["solid"].append(self.form.rowCount() - 1)
        self.number("crosshair_width", "十字线宽度 (px)", 1, 8,
                    "三种样式共用的线条宽度（描边加宽量、交叉点间隙都以它为基准）")
        self.number("crosshair_contrast_threshold", "双色：亮度分界 (0–255)", 0, 255,
                    "采样亮度低于该值视为暗背景、用「暗底主线色」，高于则两者对调"
                    "（双色描边；默认 128 与旧的黑白对一致）")
        crosshair_rows["contrast"].append(self.form.rowCount() - 1)
        dark_bg = ColorButton(config.data["crosshair_contrast_dark_bg_color"],
                              lambda color: self.update_value("crosshair_contrast_dark_bg_color", color))
        dark_bg.setToolTip("暗背景（亮度低于分界值）上的主线色；另一个颜色自动成为它的描边")
        self.controls["crosshair_contrast_dark_bg_color"] = dark_bg
        self.form.addRow("双色：暗底主线色", dark_bg)
        crosshair_rows["contrast"].append(self.form.rowCount() - 1)
        light_bg = ColorButton(config.data["crosshair_contrast_light_bg_color"],
                               lambda color: self.update_value("crosshair_contrast_light_bg_color", color))
        light_bg.setToolTip("亮背景（亮度高于分界值）上的主线色；另一个颜色自动成为它的描边")
        self.controls["crosshair_contrast_light_bg_color"] = light_bg
        self.form.addRow("双色：亮底主线色", light_bg)
        crosshair_rows["contrast"].append(self.form.rowCount() - 1)
        self.number("crosshair_contrast_halo_width", "双色：描边加宽量 (px)", 0, 8,
                    "描边比主线加宽多少像素；0 表示不描边（退回单色）")
        crosshair_rows["contrast"].append(self.form.rowCount() - 1)
        self.number("crosshair_invert_strength", "反色：强度 (%)", 0, 100,
                    "按不透明度混合的反色强度：100 = 完全反色，0 = 不绘制；"
                    "背景偏中灰时可适当调低（逐像素反色）")
        crosshair_rows["invert"].append(self.form.rowCount() - 1)
        self.number("crosshair_invert_gap", "反色：交叉点间隙 (px)", 0, 16,
                    "水平线在竖线两侧断开这么多像素，避免交叉点被反色两次而抵消；"
                    "0 = 不断开（交叉点会还原成底色）（逐像素反色）")
        crosshair_rows["invert"].append(self.form.rowCount() - 1)
        self.check("crosshair_invert_halo_enabled", "反色：中灰补偿描边",
                   "采样到接近亮度分界的背景时额外补一条细描边，避免反色后对比不足"
                   "（逐像素反色）")
        crosshair_rows["invert"].append(self.form.rowCount() - 1)
        self._sync_crosshair_rows()
        self.group("标尺")
        self._effect_preview("ruler")
        self.check("ruler_enabled", "标尺", "在截图遮罩边缘显示像素标尺，辅助定位与测量")
        self.radios("ruler_step", "刻度间距 (px)", [("10", 10), ("20", 20), ("50", 50), ("100", 100)],
                    "标尺上相邻小刻度之间的像素间距。\n"
                    "调小：刻度更密、定位更精确，但边缘会更花；\n"
                    "调大：更清爽，适合只看大范围位置。\n"
                    "间距 ≥20px 时大刻度线更长，便于一眼区分主次。")
        self.radios("ruler_major_multiple", "主刻度倍数",
                    [("2 倍", 2), ("5 倍", 5), ("10 倍", 10)],
                    "每隔多少个小刻度出现一条带数值的主刻度（主刻度 = 间距 × 倍数）。\n"
                    "倍数越大，标数字的位置越稀疏；默认 5 倍。")
        ruler_color = ColorButton(config.data["ruler_color"],
                                  lambda color: self.update_value("ruler_color", color))
        ruler_color.setToolTip("设置标尺刻度与数值颜色")
        ruler_color._setting_label = "标尺颜色"
        self.controls["ruler_color"] = ruler_color
        self.form.addRow("标尺颜色", ruler_color)
        self.group("窗口与控件识别")
        # 基础识别项直接放在本组（原先嵌套的「基础识别」小节已提升为平级分组，避免框中框）。
        self.check("window_detection", "识别窗口与控件",
                   "截图时按 Tab 把选区切到鼠标下的窗口、分组容器或控件；\n"
                   "自绘界面（浏览器、Electron）没有子窗口句柄，只能识别到顶层窗口")
        self.check("window_auto_select", "自动选中窗口",
                   "开始截图时直接选中鼠标下的窗口，无需按 Tab；关闭后只在按 Tab 时生效")
        self.check("window_hover_detect", "移动鼠标自动识别",
                   "鼠标移动时持续识别下方的窗口或控件并高亮可截图区域，\n"
                   "左键单击即可选中高亮区域，按住拖动仍然手绘选区；\n"
                   "想选外层窗口或父容器时按 Tab 逐层切换")
        self._effect_preview("hover")
        self.group("候选框外观")
        self._effect_preview("hover_box")
        fill_mode = self.radios("window_hover_fill_mode", "候选区域显示方式",
                    (("透出原图（无填充）", "reveal"),
                     ("半透明颜色填充", "fill")),
                    "透出模式会移除候选区域上的遮罩并显示原始截图，边框和尺寸标签仍保留。")
        self._hover_box_controls(config)
        hover_opacity = self.number("window_hover_opacity", "候选框填充不透明度(%)", 0, 100,
                "调整蓝色高亮区域的填充浓淡；35% 默认值可突出目标，同时保留底层内容可见。\n"
            "边框保持实色，便于看清识别范围").valueChanged.connect(
                self._mark_hover_style_custom)
        hover_opacity_control = self.controls["window_hover_opacity"]
        hover_opacity_control.setEnabled(fill_mode.value() == "fill")
        fill_mode.changed.connect(
            lambda _value: hover_opacity_control.setEnabled(fill_mode.value() == "fill"))
        # 透出模式下候选框不填充，填充颜色同样不参与绘制，随模式一起置灰。
        hover_fill_control = self.controls["window_hover_color"]
        hover_fill_control.setEnabled(fill_mode.value() == "fill")
        hover_fill_control.setToolTip("自定义候选框填充颜色；仅在“半透明颜色填充”模式下生效，"
                                      "透出原图模式下候选框没有填充")
        fill_mode.changed.connect(
            lambda _value: hover_fill_control.setEnabled(fill_mode.value() == "fill"))
        self.group("数字尺寸显示")
        self._effect_preview("hover_badge")
        self._hover_badge_controls(config)
        self.group("无障碍识别与精度")
        self.check("window_uia_detect", "优先用无障碍识别 (UIA)",
                   "默认开启：截图识别会先用 Windows 无障碍树识别，能读到浏览器网页控件、\n"
                   "WPF、Qt、Electron 等自绘界面内部的按钮、标签、编辑框；\n"
                   "需要安装 uiautomation 库（pip install uiautomation），\n"
                   "没装或识别不到时自动退回窗口句柄识别，不会让识别失效；\n"
                   "关闭时只按窗口句柄识别，速度更快但读不到自绘界面内部")
        self.number("window_hover_interval", "悬停识别刷新间隔 (ms)", 16, 500,
                    "鼠标移动时刷新悬停高亮的间隔；数值越小刷新越快、越跟手（灵敏度越高），\n"
                    "但更频繁地调用系统识别接口；开启 UIA 时该间隔会自动翻倍以兼容较慢查询。\n"
                    "范围 16（最跟手）到 500（最省资源）毫秒")
        self.number("element_depth", "识别层级", 1, 32,
                    "从命中的最内层控件往上保留多少层祖先容器（窗口、分组、面板等），\n"
                    "供 Tab 逐层切换；最内层的小控件（按钮、列表项）始终会保留，不受此值影响。\n"
                    "数值越大，Tab 能切换到的外层容器越多；1 只保留最内层控件本身，\n"
                "32 为上限。默认 12 层；提高后可遍历更深的祖先链，但可能增加 Tab 切换候选。\n"
                "UIA 命中后还会向下查找最多 24 层以寻找具体控件；此设置只控制向上保留的祖先层数。\n"
                "悬停高亮只取最内层元素，不读祖先链，因此不受此值影响")
        self.number("window_hover_reuse_radius", "悬停结果复用半径 (px)", 0, 20,
                    "决定允许复用上次悬停结果的移动距离，范围 0–20px。光标仍在上次元素内时，坐标没变会继续复用；发生小幅移动后最多复用两个 UIA 刷新周期，随后重新识别，避免停在小控件上一直保留父级高亮。\n调大：更省 CPU；代价是切换小控件后，高亮最多延后两个刷新周期。\n调小：更灵敏；代价是查询更频繁 —— 实测在 2560×1440 下每 160ms 一次查询、单次约 65ms 时，UI 线程约四成时间花在识别上，鼠标会明显发涩。\n0 = 关闭复用，每个刷新周期都重新识别。建议 4–8；弱机器或 4K 多屏建议 8–12。")
        self.number("uia_read_budget", "UIA 读取预算", 60, 600,
                    "一次下钻查询最多读取多少个控件的属性，范围 60–600，是控制单次识别耗时上限的主要开关。\n调大：浏览器/Electron/虚拟化列表这类很深的界面里更容易找到真正的深层控件；代价是单次查询更慢。\n调小：更顺滑；代价是可能提前放弃下钻，只能选中外面的大容器（例如整块网页区域而不是那个按钮）。\n实测：旧默认 180 时单次悬停查询平均约 65ms、峰值约 110ms，而且每次都把预算跑满；在 60–600 之间继续按机器调节，调大更准、调小更顺。\n预算耗尽时日志会记录“下钻达到读取预算”，可据此判断该调大还是调小。注意：查询在 UI 线程执行，调得过大时遮罩会跟着一起卡。")
        self.number("uia_children_limit", "UIA 单层子控件上限", 32, 512,
                    "下钻时单层最多枚举多少个子控件，范围 32–512。\n调大：同一层里项目很多时不容易漏（例如第 200 个列表项、工具栏末尾的按钮）；代价是每层枚举更慢。\n调小：更快；代价是同一层里排在后面的控件可能识别不到，只能选到父容器。\n风险：网页页面单层子控件可能上百，此值调得过大再叠加“读取预算”，会把单次查询明显拖长。建议与读取预算一起调整，先动读取预算。")
        self.number("uia_slow_seconds", "UIA 熔断阈值 (秒)", 0.1, 2.0,
                    "单次查询超过这个秒数就判定无障碍树异常：暂停一段时间，并退回按窗口句柄识别（只能整窗高亮），范围 0.1–2.0。\n调大：容忍偶发的慢查询、识别更准；代价是卡顿会持续更久 —— 查询在 UI 线程执行，遮罩会一起卡住。\n调小：更快止损；代价是慢机器或复杂界面上可能频繁熔断，长期退化成整窗识别、选不到具体控件。\n实测：正常情况下单次约 65ms，默认 0.4 秒只在真正异常时触发。若日志频繁出现熔断，正确做法是调小读取预算，而不是放宽此值。")
        self.decimal("uia_slow_cooldown", "UIA 熔断冷却 (秒)", 0.2, 5.0,
                     "触发熔断后暂停多久再重试 UIA，范围 0.2–5.0 秒，步进 0.1。\n"
                     "调大：给卡顿的系统更多恢复时间、减少反复卡；代价是这段时间内只能整窗识别。\n"
                     "调小：更快恢复精细识别；代价是若界面持续异常会频繁重试、反复卡顿。\n"
                     "与「熔断阈值」配合：阈值决定多慢算异常，本项决定异常后歇多久。")
        self.check("uia_debug_tree", "记录 UIA 结构诊断",
                   "开启后，每次 UIA 命中会在应用日志中记录目标控件的祖先链及父级下的组件子树（最多 60 个节点、向下 8 层，命中分支优先）。\n"
               "只记录名称、控件类型和屏幕矩形，不读取控件值；控件名称仍可能包含应用标题或文件名，请分享日志前先检查并在诊断后关闭。")
        self.group("遮罩与选区")
        self._mask_color_controls(config)
        self.number("mask_opacity", "遮罩不透明度 (%)", 0, 100,
                "选区之外颜色叠加的浓度；0 为完全透明，100 为完全遮挡")
        border_key = "selection_border_color"
        border = ColorButton(config.data[border_key],
                             lambda color: self.update_value(border_key, color))
        border.setToolTip("只改变截图选择框的边框颜色，不影响标注颜色")
        border._setting_label = "选区边框颜色"
        self.controls[border_key] = border
        self.form.addRow("选区边框颜色", border)
        self.number("selection_border_width", "选区边框宽度 (px)", 1, 4,
                    "截图选择框的边框粗细，范围 1–4 像素（默认 1）。\n"
                    "调大：选区边界在复杂画面上更醒目；代价是会略微遮挡选区边缘的像素。\n"
                    "与「候选框边线宽度」口径一致，便于统一视觉。")
        self.radios("anchor_style", "选区锚点", [("边框", "border"), ("填充", "fill")],
                    "选区四角与四边控制点的外观：边框为空心方块，填充为实心方块")
        anchor_color = ColorButton(config.data["anchor_color"],
                                   lambda color: self.update_value("anchor_color", color))
        anchor_color.setToolTip("选区四角/四边控制点的颜色；「边框」样式下为方块描边色，「填充」样式下为方块填充色")
        anchor_color._setting_label = "选区锚点颜色"
        self.controls["anchor_color"] = anchor_color
        self.color_buttons["anchor_color"] = anchor_color
        self.form.addRow("选区锚点颜色", anchor_color)
        self.number("anchor_corner_size", "四角锚点大小 (px)", 5, 21,
                    "选区四个角上控制点的边长，范围 5–21 像素（默认 6）。\n"
                    "调大：四角更容易抓住；调小：更少遮挡画面。\n"
                    "四角与四边中点分别配置，可按手感搭配（四角略大更好抓、边中点略小更少遮挡）。")
        self.number("anchor_edge_size", "四边中点锚点大小 (px)", 4, 21,
                    "选区四条边中点控制点的边长，范围 4–21 像素（默认 6）。\n"
                    "调大：边中点更容易抓住；调小：更少遮挡画面。\n"
                    "与「四角锚点大小」互不影响；边中点略小便于与四角区分。")
        self._effect_preview("selection")
        self.group("历史")
        self.number("history_limit", "历史图片数量", 1, 10000,
                    "从自动保存目录读取的最大图片数量；\n"
                    "影响贴上次截图、上一张/下一张轮换以及贴图管理窗口能看到的图片范围")
        # 2026-10-16（用户要求）：本页原来的「键盘兼容性」分组 + 对照面板整体移到「快捷键」页。

    def _effect_preview(self, kind):
        preview = ScreenshotEffectPreview(self.config, kind)
        self.previews.append(preview)
        self.form.addRow(preview)
        return preview

    def _hover_box_controls(self, config):
        """「候选框外观」组控件：风格预设 + 填充/边线颜色 + 边线宽度。"""
        combo = QComboBox()
        for label, key, _style in HOVER_STYLE_PRESETS:
            combo.addItem(label, key)
        combo.addItem("自定义", "custom")
        combo.setToolTip("一键套用整组候选框与尺寸标签配色；\n"
                         "套用后仍可逐项微调，手动改动任一项会自动切回「自定义」")
        self.hover_style_combo = combo
        self.form.addRow("候选框风格", combo)

        preset_keys = tuple(HOVER_STYLE_PRESETS[0][2])
        current = {key: config.data[key] for key in preset_keys}
        selected = (next((key for _label, key, style in HOVER_STYLE_PRESETS
                          if style == current), "custom")
                    if config.data.get("window_hover_fill_mode", "reveal") == "fill"
                    else "custom")
        combo.setCurrentIndex(combo.findData(selected))
        combo.currentIndexChanged.connect(
            lambda _index: self._apply_hover_style(combo.currentData()))

        self._hover_color("window_hover_color", "候选框填充颜色",
                          "候选框填充色；仅「半透明颜色填充」模式下可见，\n"
                          "透出原图模式下候选框没有填充")
        self._hover_color("window_hover_border_color", "候选框边线颜色",
                          "候选框四周的边线颜色；透出模式与填充模式都会绘制")
        self.number("window_hover_border_width", "候选框边线宽度(px)", 1, 6,
                    "候选框边线粗细，范围 1–6 像素：\n"
                    "调大更醒目，但会略微遮挡紧贴边线的细节").valueChanged.connect(
                        self._mark_hover_style_custom)

    def _hover_badge_controls(self, config):
        """「数字尺寸显示」组控件：宽高标签的内容/字号/配色、靠近隐藏容差。"""
        self.radios("window_hover_badge_content", "标签内容",
                    [("仅宽 × 高", "size"), ("宽 × 高 + 坐标", "size_pos"),
                     ("宽 × 高 + 控件名", "size_class")],
                    "候选区域上方尺寸标签显示的内容：\n"
                    "仅宽 × 高：只显示元素宽高（如「120 x 32」，默认）；\n"
                    "宽 × 高 + 坐标：额外附上元素左上角的屏幕物理坐标（如「120 x 32  @ 480,360」）；\n"
                    "宽 × 高 + 控件名：额外附上悬停控件名（如「120 x 32 · 按钮」）。\n"
                    "控件名取自无障碍识别的控件类型；未开 UIA 时用窗口类名，识别不到时只显示宽高。")
        self._hover_color("window_hover_text_color", "尺寸文字颜色",
                          "候选区域宽高标签（如「控件 120 x 32」）内的文字颜色")
        self._hover_color("window_hover_badge_color", "尺寸标签底色",
                          "候选区域宽高标签的背景底色，与文字颜色共同决定可读性")
        self.number("window_hover_font_size", "尺寸标签字号(px)", 8, 32,
                    "候选区域宽高标签的文字大小，范围 8–32 像素：\n"
                    "调大更易读，但标签会变长、可能盖住候选区域上边缘").valueChanged.connect(
                        self._mark_hover_style_custom)
        # 2026-10-14：尺寸标签的“光标靠近即隐藏”容差改为可自由配置（不属于候选框风格预设）。
        self.number("window_hover_badge_hide_margin", "尺寸标签隐藏距离(px)", 0, 400,
                    "光标进入尺寸标签四周该距离内即隐藏标签，移开立即恢复；\n"
                    "0 表示只有压在标签本身上才隐藏")

    def sync_controls(self):
        super().sync_controls()
        # 恢复默认/导入配置后，十字线的行可见性也要跟着当前样式重算。
        self._sync_crosshair_rows()
        if hasattr(self, "_intruder_list"):
            # 恢复默认/导入后，子项开关的可用状态跟随总开关一起刷新。
            self._intruder_list.set_enabled(self._intruder_master.isChecked())
        if not hasattr(self, "hover_style_combo"):
            return
        preset_keys = tuple(HOVER_STYLE_PRESETS[0][2])
        current = {key: self.config.data[key] for key in preset_keys}
        selected = (next((key for _label, key, style in HOVER_STYLE_PRESETS
                          if style == current), "custom")
                    if self.config.data.get("window_hover_fill_mode", "reveal") == "fill"
                    else "custom")
        with QSignalBlocker(self.hover_style_combo):
            self.hover_style_combo.setCurrentIndex(
                self.hover_style_combo.findData(selected))

    def _sync_crosshair_rows(self):
        """只显示当前十字线样式真正生效的参数行（其余整行隐藏，避免"改了没效果"的项）。"""
        rows = getattr(self, "_crosshair_rows", None)
        form = getattr(self, "_crosshair_form", None)
        if not rows or form is None:
            return
        mode = str(self.config.data.get("crosshair_mode", "invert"))
        for name, indexes in rows.items():
            for index in indexes:
                if 0 <= index < form.rowCount():
                    form.setRowVisible(index, name == mode)

    def _hover_color(self, key, label, tooltip=None):
        button = ColorButton(self.config.data[key],
                             lambda color, current=key:
                             self._set_hover_color(current, color))
        button.setToolTip(tooltip or f"自定义{label}")
        button._setting_label = label
        self.controls[key] = button
        self.color_buttons[key] = button
        self.form.addRow(label, button)

    def _set_hover_color(self, key, color):
        self.update_value(key, color)
        self._mark_hover_style_custom()

    def _mark_hover_style_custom(self, *_args):
        combo = getattr(self, "hover_style_combo", None)
        if combo is not None and combo.currentData() != "custom":
            combo.setCurrentIndex(combo.findData("custom"))

    def _apply_hover_style(self, key):
        style = next((values for _label, preset_key, values in HOVER_STYLE_PRESETS
                      if preset_key == key), None)
        if style is None:
            return
        self.config.data.update(style)
        self.config.data["window_hover_fill_mode"] = "fill"
        fill_mode = self.controls.get("window_hover_fill_mode")
        if fill_mode is not None:
            # 2026-10-14：候选区域显示方式改为单选按钮组 ⇒ set_value 不发信号，无需 QSignalBlocker。
            fill_mode.set_value("fill")
            self.controls["window_hover_opacity"].setEnabled(True)
            if "window_hover_color" in self.controls:
                self.controls["window_hover_color"].setEnabled(True)
        for name in ("window_hover_color", "window_hover_border_color",
                     "window_hover_text_color", "window_hover_badge_color"):
            self.color_buttons[name].set_color(style[name])
        for name in ("window_hover_opacity", "window_hover_border_width",
                     "window_hover_font_size"):
            control = self.controls[name]
            with QSignalBlocker(control):
                control.setValue(style[name])
        self.changed()

    def _mask_color_controls(self, config):
        key = "mask_color"
        presets = (
            ("清透蓝", "#d9edff"),
            ("薄荷绿", "#ddf5e4"),
            ("奶油黄", "#fff3cc"),
            ("樱花粉", "#ffe2eb"),
            ("淡紫", "#eee7ff"),
            ("柔白", "#ffffff"),
            ("石墨黑", "#000000"),
        )
        current = config.data[key]
        combo = QComboBox()
        for label, color in presets:
            combo.addItem(label, color)
        combo.addItem(f"自定义 ({current})", current)
        combo.setCurrentIndex(combo.findData(current))
        button = ColorButton(current,
                     lambda color: self._set_mask_color(color, combo, button))
        button.setToolTip("自定义选区外遮罩颜色")
        combo.setToolTip("选择清新亮色预设，也可用旁边的按钮自定义颜色")
        combo.currentIndexChanged.connect(
            lambda _index: self._select_mask_color(combo, button))
        self.controls[key] = combo
        self.color_buttons[key] = button
        self.form.addRow("遮罩颜色预设", combo)
        self.form.addRow("自定义遮罩颜色", button)

    def _select_mask_color(self, combo, button):
        color = combo.currentData()
        if color:
            self.update_value("mask_color", color)
            button.set_color(color)

    def _set_mask_color(self, color, combo, button):
        self.update_value("mask_color", color)
        index = combo.findData(color)
        if index < 0:
            index = combo.count() - 1
            combo.setItemText(index, f"自定义 ({color})")
            combo.setItemData(index, color)
        combo.setCurrentIndex(index)
        button.set_color(color)

    def _shortcut(self, key, label, default, help_text):
        # 与「设置 > 快捷键」页共用 HotkeyEdit（规则 12）：裸 QKeySequenceEdit 有两个坑 ——
        # ① 录制时不暂停全局热键，按 Space/S/F1 会同时真的触发截图，根本录不进去；
        # ② 不做键名规范化（meta→windows、return→enter、del→delete、统一小写），
        #    录出来的值落盘后 keyboard 库认不出来，表现为「界面上有、实际按不出来」。
        sequence = HotkeyEdit(self.config.data.get(key, default),
                              lambda text, setting=key, fallback=default:
                              self._shortcut_changed(setting, fallback, text))
        sequence.setToolTip(help_text + "；录制期间暂停全局热键")
        sequence.setProperty("help_text", help_text)
        if self.recording is not None:
            sequence.recording.connect(self.recording)
        self.controls[key] = sequence
        self.form.addRow(label, sequence)

    def _shortcut_changed(self, key, default, text):
        """键位变化（text 已由 HotkeyEdit 规范化为 keyboard 库格式）：空值或非法组合一律**回滚**到上一个有效值。

        清空控件得到的空序列没有意义（按什么键都触发不了），非法组合也不能落盘；
        两种情况下都把控件与配置恢复成上一个有效键（没有则用默认键），避免这个功能
        在界面上看着有、实际按不出来。
        """
        from config.config_manager import validate

        text = text or ""
        if text:
            try:
                validate({key: text})
            except ValueError:
                text = ""
        if text:
            from config.config_manager import CAPTURE_SHORTCUT_KEYS

            candidate = QKeySequence(text).toString(QKeySequence.PortableText).casefold()
            conflicts = [
                other_key for other_key in CAPTURE_SHORTCUT_KEYS
                if other_key != key
                and self.config.data.get(other_key)
                and QKeySequence(self.config.data[other_key]).toString(
                    QKeySequence.PortableText).casefold() == candidate
            ]
            if conflicts:
                fallback = self.config.data.get(key) or default
                with QSignalBlocker(self.controls[key]):
                    self.controls[key].setKeySequence(QKeySequence(fallback))
                get_logger().info(
                    "截图快捷键 %s 与 %s 冲突，已回滚为 %s",
                    key, conflicts[0], fallback)
                self.controls[key].setToolTip(
                    f"{self.controls[key].property('help_text')}\n"
                    f"此快捷键已由“{conflicts[0]}”使用；请输入未占用的按键")
                return
        if not text:
            fallback = self.config.data.get(key) or default
            with QSignalBlocker(self.controls[key]):
                self.controls[key].setKeySequence(QKeySequence(fallback))
            get_logger().info("快捷键 %s 非法或为空，回滚为 %s", key, fallback)
            return
        self.controls[key].setToolTip(self.controls[key].property("help_text"))
        self.update_value(key, text)