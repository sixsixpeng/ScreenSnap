"""标注样式预设：一组现有配置键的命名组合。

预设只写入**已有的**标注参数键，不新增配置项，也不改变当前工具的行为；用户选中
预设后仍可在同一面板里继续微调。两个编辑器共用同一个 `ToolbarWidget`，因此这里
只维护一份数据，避免两套预设漂移。
"""

# 常用描边/填充色：名称用于预设标签，值直接写入 *_color 键。
_STROKE_COLORS = (
    ("红", "#ff0000"),
    ("橙", "#ff8c00"),
    ("琥珀", "#ffb300"),
    ("绿", "#00a000"),
    ("青", "#00ad91"),
    ("蓝", "#168cff"),
    ("紫", "#7c4dff"),
    ("粉", "#ff4d94"),
    ("白", "#ffffff"),
    ("黑", "#000000"),
)


def _rect_presets():
    """矩形 20 个：10 个不填充 + 10 个填充（含圆角/透明度变化）。"""
    presets = []
    for index, (name, color) in enumerate(_STROKE_COLORS):
        presets.append((
            f"rect_{index:02d}",
            f"{name}色{'虚线' if index % 2 else '实线'}",
            {
                "rect_color": color,
                "rect_width": 3 if index % 3 == 0 else 2,
                "rect_style": "dash" if index % 2 else "solid",
                "rect_corner_enabled": index >= 7,
                "rect_corner_radius": 8,
                "rect_fill_enabled": False,
            },
        ))
    for index, (name, color) in enumerate(_STROKE_COLORS):
        presets.append((
            f"rect_fill_{index:02d}",
            f"{name}色填充",
            {
                "rect_color": color,
                "rect_width": 2,
                "rect_style": "solid",
                "rect_corner_enabled": True,
                "rect_corner_radius": 12 if index % 2 else 6,
                "rect_fill_enabled": True,
                "rect_fill_opacity": (20, 35, 50)[index % 3],
                "rect_fill_color": color,
            },
        ))
    return tuple(presets)


def _ellipse_presets():
    """椭圆 20 个：10 个不填充 + 10 个填充。"""
    presets = []
    for index, (name, color) in enumerate(_STROKE_COLORS):
        presets.append((
            f"ellipse_{index:02d}",
            f"{name}色{'虚线' if index % 2 else '实线'}",
            {
                "ellipse_color": color,
                "ellipse_width": 3 if index % 3 == 0 else 2,
                "ellipse_style": "dash" if index % 2 else "solid",
                "ellipse_fill_enabled": False,
            },
        ))
    for index, (name, color) in enumerate(_STROKE_COLORS):
        presets.append((
            f"ellipse_fill_{index:02d}",
            f"{name}色填充",
            {
                "ellipse_color": color,
                "ellipse_width": 2,
                "ellipse_style": "solid",
                "ellipse_fill_enabled": True,
                "ellipse_fill_opacity": (20, 35, 50)[index % 3],
                "ellipse_fill_color": color,
            },
        ))
    return tuple(presets)


def _pen_presets():
    widths = (2, 3, 4, 6)
    return tuple((
        f"pen_{index:02d}",
        f"{name}色{widths[index % len(widths)]}px",
        {"pen_color": color, "pen_width": widths[index % len(widths)]},
    ) for index, (name, color) in enumerate(_STROKE_COLORS))


def _marker_presets():
    widths = (4, 6, 8)
    opacities = (25, 40, 55)
    return tuple((
        f"marker_{index:02d}",
        f"{name}色{widths[index % 3]}px·{opacities[index % 3]}%",
        {
            "marker_color": color,
            "marker_width": widths[index % 3],
            "marker_opacity": opacities[index % 3],
        },
    ) for index, (name, color) in enumerate(_STROKE_COLORS))


def _arrow_presets():
    styles = (
        ("红实心", "#ff0000", "filled", 2),
        ("橙实心", "#ff8c00", "filled", 3),
        ("琥珀实心", "#ffb300", "filled", 2),
        ("绿实心", "#00a000", "filled", 2),
        ("蓝实心", "#168cff", "filled", 3),
        ("紫实心", "#7c4dff", "filled", 2),
        ("白实心", "#ffffff", "filled", 2),
        ("黑实心", "#000000", "filled", 2),
        ("蓝色双向", "#168cff", "double_filled", 2),
        ("红色曲线", "#ff0000", "curve_filled", 3),
        ("蓝色曲线空心", "#168cff", "curve_open", 3),
        ("紫色曲线虚线", "#7c4dff", "curve_open_dashed", 3),
    )
    return tuple((
        f"arrow_{index:02d}", label,
        {"arrow_color": color, "arrow_style": style, "arrow_width": width},
    ) for index, (label, color, style, width) in enumerate(styles))


def _text_presets():
    presets = (
        ("红字小号", "#ff0000", 16, False, False, "#fff3a0"),
        ("红字粗体", "#ff0000", 24, True, False, "#fff3a0"),
        ("橙字粗体", "#ff8c00", 20, True, False, "#fff3a0"),
        ("绿字小号", "#00a000", 16, False, False, "#fff3a0"),
        ("蓝字小号", "#168cff", 16, False, False, "#fff3a0"),
        ("紫字粗体", "#7c4dff", 22, True, False, "#fff3a0"),
        ("白字黑底", "#ffffff", 18, False, True, "#202020"),
        ("黑字黄底", "#202020", 18, False, True, "#ffd54f"),
        ("白字红底", "#ffffff", 20, True, True, "#d32f2f"),
        ("灰字注释", "#9aa0a6", 14, False, False, "#fff3a0"),
    )
    return tuple((
        f"text_{index:02d}", label,
        {
            "font_size": size,
            "text_color": color,
            "text_bold": bold,
            "text_background_enabled": background,
            "text_background": background_color,
            "text_alignment": "left",
        },
    ) for index, (label, color, size, bold, background, background_color) in enumerate(presets))


def _mosaic_presets():
    presets = (
        ("轻毛玻璃", "blur", 6, True, 16),
        ("中毛玻璃", "blur", 10, True, 20),
        ("重毛玻璃", "blur", 16, True, 24),
        ("轻方块", "blocks", 6, True, 16),
        ("中方块", "blocks", 10, True, 20),
        ("重方块", "blocks", 18, True, 24),
        ("细颗粒", "fine", 4, True, 14),
        ("中颗粒", "fine", 8, True, 18),
        ("粗颗粒", "fine", 14, True, 22),
        ("框选毛玻璃", "blur", 12, False, 20),
    )
    return tuple((
        f"mosaic_{index:02d}", label,
        {
            "mosaic_mode": mode,
            "mosaic_size": size,
            "mosaic_brush": brush,
            "mosaic_width": width,
        },
    ) for index, (label, mode, size, brush, width) in enumerate(presets))


def _eraser_presets():
    presets = (
        ("细笔迹", 10, True, False),
        ("小笔迹", 16, True, False),
        ("中笔迹", 24, True, False),
        ("大笔迹", 32, True, False),
        ("特大笔迹", 44, True, False),
        ("细框选", 20, False, False),
        ("中框选", 40, False, False),
        ("大框选", 60, False, False),
        ("中框选·擦原图", 40, False, True),
        ("大框选·擦原图", 60, False, True),
    )
    return tuple((
        f"eraser_{index:02d}", label,
        {
            "eraser_width": width,
            "eraser_brush": brush,
            "eraser_erase_base": erase_base,
        },
    ) for index, (label, width, brush, erase_base) in enumerate(presets))


def _crop_presets():
    widths = (1, 2, 3, 4)
    return tuple((
        f"crop_{index:02d}",
        f"{name}色{widths[index % 4]}px",
        {"crop_color": color, "crop_width": widths[index % 4]},
    ) for index, (name, color) in enumerate(_STROKE_COLORS))


# 工具 → (预设 id, 显示名, {配置键: 值})。序号已有独立预设，不在这里重复。
ANNOTATION_PRESETS = {
    "rect": _rect_presets(),
    "ellipse": _ellipse_presets(),
    "pen": _pen_presets(),
    "marker": _marker_presets(),
    "arrow": _arrow_presets(),
    "text": _text_presets(),
    "mosaic": _mosaic_presets(),
    "eraser": _eraser_presets(),
    "crop": _crop_presets(),
}

# 预设会碰到的全部配置键：设置页套用后，已打开编辑器的工具栏控件也按这份清单同步。
PRESET_SETTING_KEYS = frozenset(
    key for presets in ANNOTATION_PRESETS.values()
    for _preset_id, _label, values in presets for key in values
)


def presets_for(tool):
    """返回某工具的预设元组；没有预设时返回空元组。"""
    return ANNOTATION_PRESETS.get(tool, ())


def preset_values(tool, preset_id):
    """按 id 取预设值；未知 id 返回空字典（表示“自定义”）。"""
    for current_id, _label, values in presets_for(tool):
        if current_id == preset_id:
            return dict(values)
    return {}


def matching_preset(tool, settings):
    """返回与当前配置完全匹配的预设 id；没有匹配时返回 "custom"。

    只比较预设自身包含的键，避免“线宽匹配但颜色已被改掉”也误判为预设。
    """
    for preset_id, _label, values in presets_for(tool):
        if all(settings.get(key) == value for key, value in values.items()):
            return preset_id
    return "custom"
