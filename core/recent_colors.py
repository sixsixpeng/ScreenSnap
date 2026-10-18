"""最近使用颜色：一个跨工具、跨编辑器共用的单池。

颜色历史只服务“快速再选一次”，不替代各工具自己的当前颜色（`pen_color`、
`rect_color`……）。池子在新颜色被确认时把该颜色移到最前面，去重并限制长度。
"""

import re

MAX_RECENT_COLORS = 12

# 首次启动的种子色：覆盖常用标注色，避免空池时右键菜单什么也没有。
DEFAULT_RECENT_COLORS = [
    "#ff0000", "#ff8c00", "#ffb300", "#00a000", "#00ad91", "#168cff",
    "#7c4dff", "#ff4d94", "#ffffff", "#000000",
]

_HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


def normalize_color(value):
    """把 #RGB / #RRGGBB 规整成小写 #rrggbb；无效值返回空串。"""
    text = str(value or "").strip().lower()
    if re.fullmatch(r"#[0-9a-f]{3}", text):
        text = "#" + "".join(character * 2 for character in text[1:])
    return text if _HEX_RE.fullmatch(text) else ""


def normalize_colors(values, limit=MAX_RECENT_COLORS):
    """过滤无效色、去重并截断；保留原有先后顺序。"""
    result = []
    for value in values or []:
        color = normalize_color(value)
        if color and color not in result:
            result.append(color)
        if len(result) >= limit:
            break
    return result


def push_recent(values, color, limit=MAX_RECENT_COLORS):
    """把颜色移到池子最前面；无效颜色不改变原池。"""
    normalized = normalize_color(color)
    if not normalized:
        return normalize_colors(values, limit)
    return [normalized] + [item for item in normalize_colors(values, limit)
                           if item != normalized][:max(0, limit - 1)]
