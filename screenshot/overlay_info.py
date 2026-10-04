"""只在遮罩/浮层绘制的提示，不污染原图。

提示条不再贴顶居中，而是**跟着放大镜走**：画在放大镜框的下方（下方放不下就画上方），
因此不需要"鼠标移入隐藏、移出恢复"那套避让逻辑。放大镜不可用（关闭、模态弹窗等）时，
调用方仍传入按同一避让规则算出的框，提示条因此跟随光标显示。

这里把提示条拆成三块，便于浮层与遮罩各取所需：

* `info_bar_layout()`：算提示条矩形与分行文本（不绘制）；
* `paint_info_bar()`：把分行文本画成圆角条（浮层 `InfoBar` 用）；
* `paint_info_badge()`：画控件尺寸徽标（仍画在遮罩上，并避开提示条）。
"""

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QFont, QPen

from config.config_manager import HINT_BAR_FILL_ALPHA, HINT_BAR_WARNING_FILL_ALPHA

# 提示条内边距：横向与纵向都取最小值，宽度贴合文字而不是固定值，减少遮挡面积。
INFO_PADDING_X = 10
INFO_PADDING_Y = 4
INFO_SIDE_MARGIN = 8
# 提示条相邻两行内的分隔符，与提示项之间的分隔保持一致。
HINT_SEPARATOR = " | "
# 提示条最大宽度：超过就换行，而不是把一行拉得很长横穿屏幕。
HINT_BAR_MAX_WIDTH = 420
# 提示条与放大镜框之间的间距（像素）兜底值：实际取值来自配置 `capture_hint_gap`，默认紧贴。
HINT_BAR_GAP = 0
# 兜底样式：没有任何配置（如一次性绘制的旧调用）时使用；alpha 沿用改造前的观感。
FALLBACK_HINT_BAR_STYLE = {"text_color": "#ffffff", "fill_color": "#141c22",
                           "border_color": "#141c22", "rounded": True, "radius": 5,
                           "alpha": HINT_BAR_FILL_ALPHA}
FALLBACK_HINT_BAR_WARNING_STYLE = {"text_color": "#ffeccf", "fill_color": "#603410",
                                   "border_color": "#b06a1c", "rounded": True, "radius": 5,
                                   "alpha": HINT_BAR_WARNING_FILL_ALPHA}


def wrap_items(items, metrics, text_width, per_line=0):
    """把提示项排成若干行，返回每行文本。

    · 一行能放几个就放几个，放不下就整体挪到下一行——不截断单个提示项；
    · `per_line` > 0 时**每行最多放这么多个**（配置里的"每行提示数"），
      到数就换行，即使宽度还够；0 表示只按宽度自动换行。
    """
    rows = []
    current = ""
    count = 0
    for item in items:
        if not item:
            continue
        candidate = item if not current else current + HINT_SEPARATOR + item
        too_wide = current and metrics.horizontalAdvance(candidate) > text_width
        too_many = per_line > 0 and count >= per_line
        if current and (too_wide or too_many):
            rows.append(current)
            current = item
            count = 1
        else:
            current = candidate
            count += 1
    if current:
        rows.append(current)
    return rows


def info_bar_layout(metrics, area, anchor, items, warning=None, max_width=HINT_BAR_MAX_WIDTH,
                    per_line=0, cursor=None, gap=HINT_BAR_GAP):
    """算提示条矩形、分行文本与对齐方式，返回 `(bar, rows, align_right)`。

    · `items` 是已按配置顺序排好的提示项文本，空串会跳过（调用方可以把"当前不适用"留空）；
    · `warning` 非空时作为**第一个提示项**并以警示样式显示（提示选区内可能含本程序窗口）；
    · `anchor` 是放大镜框（逻辑坐标），`area` 是当前显示器的逻辑矩形；
    · `per_line` 是"每行提示数"配置（0 表示只按宽度自动换行）；
    · `gap` 是提示条与放大镜框之间的间距（配置 `capture_hint_gap`，默认 0 表示紧贴）；
    · `cursor` 是光标（逻辑坐标，与 `anchor` 同一坐标系）：
      - **纵向**：放大镜贴屏幕下沿翻到光标上方时，提示条也跟着翻到放大镜上方，
        两条始终在光标同一侧，不会反过来盖住光标中心；
      - **横向**：提示条与放大镜的边**直接对齐**（放大镜在光标右侧就左对齐其左边，
        在左侧就右对齐其右边），不再额外朝光标外侧偏移；被屏边推回时按推回后的那一侧决定文字对齐。
      `cursor` 为 None（例如一次性绘制的旧调用）时保持"贴在放大镜下方 + 居中"的老行为。
    """
    contents = list(items or ())
    if warning:
        contents.insert(0, f"⚠ {warning}")
    # 单行可用宽度：既不超出一条提示条的最大宽度，也不超出屏幕可用宽度。
    text_width = max(1, min(max_width, area.width() - INFO_SIDE_MARGIN * 2)
                     - INFO_PADDING_X * 2)
    rows = wrap_items(contents, metrics, text_width, per_line=per_line)
    if not rows:
        # 没有任何提示项（全部关闭、或当前阶段都不适用）时整条不画，
        # 既不留下一个空条，也不让"没有提示"凭空变成一块遮挡。
        return QRect(), [], False
    # 行数上限：提示条高度不超过所在区域，屏幕很小时也不至于铺满整个画面。
    max_rows = max(1, (area.height() - 8 - INFO_PADDING_Y * 2) // max(1, metrics.height()))
    rows = rows[:max_rows]
    width = min(max_width, max(metrics.horizontalAdvance(row) for row in rows)
                + INFO_PADDING_X * 2)
    height = metrics.height() * len(rows) + INFO_PADDING_Y * 2
    if cursor is None:
        # 旧行为：贴在放大镜下方，下方放不下就翻到上方。
        top = anchor.bottom() + 1 + gap
        if top + height > area.bottom() - 4:
            top = anchor.top() - height - gap
        ideal_left = anchor.center().x() - width // 2
        left = min(max(ideal_left, area.left() + 4),
                   max(area.left() + 4, area.right() - width - 4))
        align_right = ideal_left + width > area.right() - 4
        return QRect(left, top, width, height), rows, align_right
    # 纵向：放大镜在光标上方（贴下沿已翻转）时，提示条也放到放大镜上方；
    # 否则放在放大镜下方。这样两条都不横跨光标，不会遮住光标中心。
    # cursor 允许是 QPoint 或 (x, y)：调用方传的是视图逻辑坐标。
    cursor_x, cursor_y = ((cursor.x(), cursor.y()) if hasattr(cursor, "x") else cursor)
    magnifier_above = anchor.bottom() <= cursor_y + gap
    if magnifier_above:
        top = anchor.top() - height - gap
        if top < area.top() + 4:  # 上方也放不下，退回下方（极端窄屏）
            top = anchor.bottom() + 1 + gap
    else:
        # 下方：提示条上边紧贴放大镜下边再留出 gap 像素，gap=0 时两者相邻而不重叠。
        top = anchor.bottom() + 1 + gap
        if top + height > area.bottom() - 4:
            top = anchor.top() - height - gap
    top = min(max(top, area.top() + 4), max(area.top() + 4, area.bottom() - height - 4))
    if anchor.left() >= cursor_x:
        # 放大镜在光标右侧：提示条左边直接对齐放大镜左边；文字左对齐。
        left = anchor.left()
        align_right = False
        if left + width > area.right() - 4:
            left = area.right() - 4 - width
            align_right = True
    else:
        # 放大镜在光标左侧（贴右沿已翻转）：提示条右边直接对齐放大镜右边；文字右对齐。
        left = anchor.right() + 1 - width
        align_right = True
        if left < area.left() + 4:
            left = area.left() + 4
            align_right = False
    left = min(max(left, area.left() + 4), max(area.left() + 4, area.right() - width - 4))
    return QRect(left, top, width, height), rows, align_right


def paint_info_bar(painter, bar, rows, style=None, metrics=None, align_right=False):
    """把 `info_bar_layout()` 的分行文本画成圆角提示条（浮层用，背景透明由父级提供）。

    `style` 是配置层 `hint_bar_style()` 解析出的外观（文字/填充/描边色、圆角开关与半径）；
    为空时按普通提示条兜底样式绘制。`align_right` 为真时文字右对齐，否则左对齐。
    """
    if bar.isEmpty() or not rows:
        return
    metrics = metrics or painter.fontMetrics()
    style = style or FALLBACK_HINT_BAR_STYLE
    align = Qt.AlignRight if align_right else Qt.AlignLeft
    fill = QColor(style.get("fill_color") or FALLBACK_HINT_BAR_STYLE["fill_color"])
    fill.setAlpha(max(0, min(255, int(style.get("alpha", HINT_BAR_FILL_ALPHA)))))
    border = QColor(style.get("border_color") or fill.name())
    radius = max(0, int(style.get("radius", 5) or 0)) if style.get("rounded", True) else 0
    painter.save()
    painter.setPen(QPen(border, 1))
    painter.setBrush(fill)
    painter.drawRoundedRect(bar, radius, radius)
    painter.setPen(QColor(style.get("text_color") or FALLBACK_HINT_BAR_STYLE["text_color"]))
    for index, row in enumerate(rows):
        painter.drawText(QRect(bar.left() + INFO_PADDING_X,
                               bar.top() + INFO_PADDING_Y + index * metrics.height(),
                               bar.width() - INFO_PADDING_X * 2, metrics.height()),
                         align | Qt.AlignVCenter, row)
    painter.restore()


def paint_info(painter, anchor, area, items, warning=None, element_rect=None,
               element_size=None, element_border_color="#00d7aa",
               element_text_color="#ffffff", element_background_color="#141c22",
               element_font_size=12, element_border_width=1,
               max_width=HINT_BAR_MAX_WIDTH, per_line=0, style=None):
    """一次性画提示条与尺寸徽标，返回提示条矩形（排版逻辑与浮层共用）。

    遮罩实际用的是拆分版：提示条由 `InfoBar` 子控件绘制（保证压在编辑画布之上），
    遮罩只画 `paint_info_badge()`。这个整体版本保留了"在给定 painter 上画全"的
    行为，供需要一次性绘制的调用方与用例使用。
    """
    area = area or QRect(0, 0, painter.device().width(), painter.device().height())
    metrics = painter.fontMetrics()
    bar, rows, align_right = info_bar_layout(metrics, area, anchor, items, warning=warning,
                                             max_width=max_width, per_line=per_line)
    if bar.isEmpty():
        return QRect()
    if style is None:
        style = FALLBACK_HINT_BAR_WARNING_STYLE if warning else FALLBACK_HINT_BAR_STYLE
    paint_info_bar(painter, bar, rows, style, metrics, align_right)
    paint_info_badge(painter, area, element_rect, element_size,
                     element_border_color=element_border_color,
                     element_text_color=element_text_color,
                     element_background_color=element_background_color,
                     element_font_size=element_font_size,
                     element_border_width=element_border_width, bar=bar)
    return bar


def paint_info_badge(painter, area, element_rect, element_size=None,
                     element_border_color="#00d7aa", element_text_color="#ffffff",
                     element_background_color="#141c22", element_font_size=12,
                     element_border_width=1, bar=None):
    """在遮罩上画控件尺寸徽标；`bar` 是提示条矩形，重叠时改贴控件下方。"""
    if element_rect is None or element_rect.isEmpty():
        return QRect()
    bar = bar or QRect()
    font = QFont()
    font.setPixelSize(max(1, int(element_font_size)))
    painter.save()
    painter.setFont(font)
    badge_metrics = painter.fontMetrics()
    size = element_size or (element_rect.width(), element_rect.height())
    label = f"{size[0]} x {size[1]} px"
    label_width = badge_metrics.horizontalAdvance(label) + 16
    label_height = badge_metrics.height() + 8
    label_left = max(area.left() + 4, min(
        element_rect.center().x() - label_width // 2,
        area.right() - label_width - 3))
    # 徽标默认贴在控件上方；与提示条重叠时改贴控件下方，再越界才回到上方。
    label_top = element_rect.top() - label_height - 6
    if QRect(label_left, label_top, label_width, label_height).intersects(bar):
        label_top = element_rect.bottom() + 7
    if label_top + label_height > area.bottom() - 3:
        label_top = max(area.top() + 4, element_rect.top() - label_height - 6)
    badge = QRect(label_left, label_top, label_width, label_height)
    painter.setPen(QPen(QColor(element_border_color), max(1, element_border_width)))
    painter.setBrush(QColor(element_background_color))
    painter.drawRoundedRect(badge, 4, 4)
    painter.setPen(QColor(element_text_color))
    painter.drawText(badge, Qt.AlignCenter, label)
    painter.restore()
    return badge
