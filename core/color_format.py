"""取色复制的文本格式（2026-10-11，方案 B/C）。

用户要求：同一次取色能拿到不同的颜色表达；连续取色还能攒起来一次粘贴。
剪贴板的文本格式只能放**一个字符串**，因此：
· 单次复制 —— 按设置把颜色格式化成 hex / rgb / rgba / hsl，或 all（多行全都给）；
· 累积模式 —— 由调用方保留历史，把多次取色拼成多行文本（见 format_color_list）。
"""

FORMATS = ("hex", "rgb", "rgba", "hsl", "all")


def _channels(color):
    """把 #RRGGBB 解析成 (r, g, b)；解析失败返回 None。"""
    value = str(color or "").strip().lstrip("#")
    if len(value) != 6:
        return None
    try:
        return tuple(int(value[index:index + 2], 16) for index in (0, 2, 4))
    except ValueError:
        return None


def _hsl(rgb):
    r, g, b = (channel / 255 for channel in rgb)
    top, bottom = max(r, g, b), min(r, g, b)
    lightness = (top + bottom) / 2
    if top == bottom:
        return 0, 0, lightness
    delta = top - bottom
    saturation = delta / (2 - top - bottom) if lightness > 0.5 else delta / (top + bottom)
    if top == r:
        hue = ((g - b) / delta) % 6
    elif top == g:
        hue = (b - r) / delta + 2
    else:
        hue = (r - g) / delta + 4
    return hue * 60, saturation, lightness


def format_color(color, mode="hex"):
    """把 #RRGGBB 按 mode 格式化；解析失败时原样返回（不抛异常）。"""
    text = str(color or "").strip()
    rgb = _channels(text)
    if rgb is None:
        return text
    mode = str(mode or "hex").strip().lower()
    if mode not in FORMATS:
        mode = "hex"
    hex_text = "#%02x%02x%02x" % rgb
    if mode == "hex":
        return hex_text
    if mode == "rgb":
        return "rgb(%d, %d, %d)" % rgb
    if mode == "rgba":
        return "rgba(%d, %d, %d, 1)" % rgb
    hue, saturation, lightness = _hsl(rgb)
    hsl_text = "hsl(%d, %d%%, %d%%)" % (round(hue), round(saturation * 100), round(lightness * 100))
    if mode == "hsl":
        return hsl_text
    return "\n".join((hex_text, "rgb(%d, %d, %d)" % rgb, hsl_text))


# 常见 CSS 色名（用于颜色贴图上的可读名称；只收常用项，避免整表噪音）。
COLOR_NAMES = {
    "#000000": "Black", "#ffffff": "White", "#ff0000": "Red", "#00ff00": "Lime",
    "#0000ff": "Blue", "#ffff00": "Yellow", "#00ffff": "Cyan", "#ff00ff": "Magenta",
    "#c0c0c0": "Silver", "#808080": "Gray", "#800000": "Maroon", "#808000": "Olive",
    "#008000": "Green", "#800080": "Purple", "#008080": "Teal", "#000080": "Navy",
    "#ffa500": "Orange", "#ffc0cb": "Pink", "#a52a2a": "Brown", "#ffd700": "Gold",
    "#f0f8ff": "AliceBlue", "#faebd7": "AntiqueWhite", "#7fffd4": "Aquamarine",
    "#f5f5dc": "Beige", "#ffe4c4": "Bisque", "#8a2be2": "BlueViolet", "#deb887": "BurlyWood",
    "#5f9ea0": "CadetBlue", "#7fff00": "Chartreuse", "#d2691e": "Chocolate", "#ff7f50": "Coral",
    "#6495ed": "CornflowerBlue", "#dc143c": "Crimson", "#00008b": "DarkBlue", "#b8860b": "DarkGoldenrod",
    "#a9a9a9": "DarkGray", "#006400": "DarkGreen", "#ff8c00": "DarkOrange", "#9932cc": "DarkOrchid",
    "#8b0000": "DarkRed", "#483d8b": "DarkSlateBlue", "#2f4f4f": "DarkSlateGray", "#00ced1": "DarkTurquoise",
    "#9400d3": "DarkViolet", "#ff1493": "DeepPink", "#1e90ff": "DodgerBlue", "#b22222": "FireBrick",
    "#228b22": "ForestGreen", "#dcdcdc": "Gainsboro", "#ffdab9": "PeachPuff", "#cd853f": "Peru",
    "#ffc0cb": "Pink", "#dda0dd": "Plum", "#4169e1": "RoyalBlue", "#fa8072": "Salmon",
    "#a0522d": "Sienna", "#87ceeb": "SkyBlue", "#6a5acd": "SlateBlue", "#708090": "SlateGray",
    "#00ff7f": "SpringGreen", "#d2b48c": "Tan", "#d8bfd8": "Thistle", "#ff6347": "Tomato",
    "#40e0d0": "Turquoise", "#ee82ee": "Violet", "#f5deb3": "Wheat", "#9acd32": "YellowGreen",
}


def color_name(color):
    """返回常见 CSS 色名；查不到返回空串（贴图会跳过这一行）。"""
    rgb = _channels(color)
    if rgb is None:
        return ""
    return COLOR_NAMES.get("#%02x%02x%02x" % rgb, "")


def label_lines(color, mode="hex", with_name=False):
    """颜色贴图用的标签行：按 mode 出 1-3 行，可再补一行色名。"""
    text = format_color(color, mode)
    lines = [line for line in str(text).split(chr(10)) if line]
    if with_name:
        name = color_name(color)
        if name:
            lines.append(name)
    return lines


def format_color_list(colors, mode="hex"):
    """累积模式下把多个颜色拼成多行文本（每行一个颜色）。"""
    lines = []
    for color in colors:
        text = format_color(color, mode)
        if text and text not in lines:
            lines.append(text)
    return "\n".join(lines)