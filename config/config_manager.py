"""JSON 配置的加载、保存和导入导出。"""

import copy
import json
import logging
from time import time_ns
import re
from pathlib import Path

# 工具线宽键的间接映射：标注画布按工具名解析对应的 *_width 配置键，
# 因此 rect_width/ellipse_width/arrow_width/pen_width/marker_width/eraser_width 不会以字面量
# 直接出现在读取处。做“配置是否被使用”的静态审计时需先解析本表，否则会误报为死配置。
TOOL_WIDTH_KEYS = {
    "rect": "rect_width", "ellipse": "ellipse_width", "arrow": "arrow_width",
    "pen": "pen_width", "marker": "marker_width", "eraser": "eraser_width",
}

# 提示条可开关的提示项：id → 设置页里显示的名字（顺序＝默认显示顺序）。
# 这里是**唯一来源**：遮罩按这些 id 组织文案，设置页按同样的清单给出勾选项，
# 放在配置层而不是绘制模块，避免设置页导入时触发 screenshot 包初始化。
HINT_ITEMS = (
    ("coords", "鼠标坐标与选区尺寸"),
    ("drag_move", "拖动移动"),
    ("resize", "四角/边中点缩放"),
    ("select", "拖拽框选"),
    ("element_cycle", "Tab 切换窗口/控件层级"),
    ("edit", "双击/Enter 提交（编辑里双击空白保存）"),
    ("nudge", "WASD/方向键微调"),
    ("cancel", "Esc 取消"),
    ("save", "右键双击/快捷键保存"),
    ("quick_sticker", "快速贴图"),
    ("picker", "取色"),
    ("fixed_size", "固定尺寸"),
    ("recapture", "清除选择"),
    ("window_edit", "窗口编辑"),
    ("multi_select", "多选编辑模式"),
    ("copy", "仅复制"),
    ("toolbar_move", "移动原地编辑工具栏"),
    ("toolbar_hide", "隐藏/恢复原地编辑工具栏"),
    ("inline_edit", "原地编辑：双击标注二次编辑/删除"),
    ("inline_menu", "原地编辑：右键标注菜单"),
    ("inline_history", "原地编辑：撤销/重做"),
)
HINT_ITEM_IDS = tuple(item_id for item_id, _label in HINT_ITEMS)
HINT_LABELS = dict(HINT_ITEMS)

# 采集自检警示：截图选区包含本程序自身窗口时，在提示条里附加的暖色警示。
# 分类 id → 设置页显示名，也是"哪些自身窗口参与警示"的子开关清单。
# 放在配置层与提示项同理（避免设置页导入时触发 screenshot 包初始化）。
INTRUDER_WARNING_ITEMS = (
    ("settings", "设置窗口"),
    ("editor", "编辑器"),
    ("notification", "通知缩略图"),
    ("sticker", "贴图"),
    ("sticker_panel", "贴图管理"),
    ("recycle", "贴图回收站"),
    ("appearance", "外观弹层"),
    ("popup", "弹出菜单"),
    ("other", "本程序窗口"),
)
INTRUDER_WARNING_IDS = tuple(item_id for item_id, _label in INTRUDER_WARNING_ITEMS)
INTRUDER_WARNING_LABELS = dict(INTRUDER_WARNING_ITEMS)

# 提示条可配置的外观字段：预设一次性写入这些字段，设置页按同样顺序给出控件。
HINT_BAR_STYLE_FIELDS = ("text_color", "fill_color", "border_color", "rounded", "radius")

# 提示条样式预设：id → 显示名 → 各外观字段取值。
# 普通提示条与「采集自检警示」提示条各用一套、互不影响；设置页选中预设会一次性写入这些字段。
# 配色取自程序既有风格：深色底 #141c22 / 墨青 #102a31 / 强调蓝 #168cff / 暖色警示 #603410。
HINT_BAR_PRESETS = (
    ("dark", "深色半透明", {"text_color": "#ffffff", "fill_color": "#141c22",
                          "border_color": "#141c22", "rounded": True, "radius": 5}),
    ("ink", "墨青玻璃", {"text_color": "#eaf7f4", "fill_color": "#102a31",
                       "border_color": "#1f5a63", "rounded": True, "radius": 6}),
    ("paper", "浅色纸张", {"text_color": "#1b2a30", "fill_color": "#f4f7f8",
                         "border_color": "#c3ced6", "rounded": True, "radius": 6}),
    ("graphite", "石墨灰", {"text_color": "#eef2f4", "fill_color": "#273b44",
                          "border_color": "#3d5762", "rounded": True, "radius": 3}),
)
HINT_BAR_WARNING_PRESETS = (
    ("amber", "暖琥珀", {"text_color": "#ffeccf", "fill_color": "#603410",
                       "border_color": "#b06a1c", "rounded": True, "radius": 5}),
    ("alert_red", "警示红", {"text_color": "#fff0f0", "fill_color": "#6e1f24",
                           "border_color": "#c0383f", "rounded": True, "radius": 5}),
    ("sun", "亮黄警示", {"text_color": "#5a3a00", "fill_color": "#ffd75e",
                       "border_color": "#e0a33c", "rounded": True, "radius": 5}),
    ("amber_square", "琥珀直角", {"text_color": "#ffeccf", "fill_color": "#603410",
                                "border_color": "#b06a1c", "rounded": False, "radius": 0}),
)
HINT_BAR_PRESET_IDS = frozenset(preset_id for preset_id, _label, _values in HINT_BAR_PRESETS)
HINT_BAR_WARNING_PRESET_IDS = frozenset(
    preset_id for preset_id, _label, _values in HINT_BAR_WARNING_PRESETS)
# 两套默认样式：普通提示条沿用原有深色半透明外观，警示提示条沿用原有暖色警示外观。
DEFAULT_HINT_BAR_STYLE = {"preset": "dark", **HINT_BAR_PRESETS[0][2]}
DEFAULT_HINT_BAR_WARNING_STYLE = {"preset": "amber", **HINT_BAR_WARNING_PRESETS[0][2]}
# 填充色不透明度：沿用改造前的观感（普通 220 / 警示 232），不单独暴露为设置项。
HINT_BAR_FILL_ALPHA = 220
HINT_BAR_WARNING_FILL_ALPHA = 232


# 版本变化重置用户数据时**保留**的键。
#
# 判据：保留 = 「环境 / 使用习惯」，即用户显式选过一次、且与数据格式无关的偏好；
#       重置 = 「外观样式 / 工具默认态 / 瞬时状态」，这些正是新版本要重新决定的。
# 每加一个键都要想清楚：新版语义变了还保留会不会出问题（路径与开关通常安全，外观与数据语义要谨慎）。
# validate() 会忽略未知键，所以即使新版本删掉了某个保留键，也只是被丢弃而不会让启动失败。
PRESERVED_ON_VERSION_RESET = (
    # 环境与路径：用户选的存放位置，跟着版本重置会让图片散落或找不到
    "save_dir", "save_as_dir", "log_dir", "file_sticker_path",
    # 开机与系统注册：开机自启在系统里已注册，配置重置但注册还在会造成状态不一致
    "start_on_boot",
    # 按键习惯：键盘流用户最在意的部分，且与数据格式无关
    "hotkeys", "hotkeys_enabled",
    "capture_quick_sticker_shortcut", "capture_save_shortcut", "capture_custom_size_shortcut",
    "capture_recapture_shortcut", "capture_window_edit_shortcut", "capture_multi_select_shortcut",
    "capture_copy_shortcut", "capture_toolbar_hide_shortcut", "capture_picker_shortcut",
    # 输出组织习惯：与 save_dir 配套，重置会让归档方式突变
    "filename", "save_format", "archive_by_month", "archive_by_day", "image_archive_period",
    # 打扰程度：通知开关是"被烦过才关"的偏好，不该被升级重新打开
    "bubble", "notification_timeout", "copy_notification", "picker_notification", "save_notification",
    "sticker_notification",
    # 功能开关：用户显式关掉过的能力（识别、放大镜、标尺、诊断、回收站等）必须记住
    "window_detection", "window_hover_detect", "magnifier", "magnifier_size", "magnifier_grid",
    "cursor", "ruler_enabled", "uia_debug_tree", "sticker_recycle_enabled",
    "intruder_warning_enabled", "capture_delay", "capture_hotkey_suppress",
    "logging_enabled", "log_monthly_folder",
    # 明暗主题：属于个人偏好而非版本语义（值不合法时 validate/repair 会兜底）
    "theme",
)
# 明确**不保留**（走 DEFAULTS，让新版本的观感与语义生效）：
#   * 样式类：sticker_*、editor_*、rect_*、arrow_*、pen_*、marker_*、mosaic_*、hint_bar_*、
#     capture_hint_*、sequence_*、window_hover_* 的颜色/宽度/透明度/圆角/阴影；
#   * 工具默认态：annotation_tool、arrow_chain、pen_chain、rect_style、rect_fill_enabled、
#     ellipse_fill_enabled、mosaic_mode、editor_overcanvas_mode、editor_rotation_*、
#     editor_zoom_wheel_step、editor_checker_tile_size、editor_text_click_delay；
#   * 瞬时状态：last_capture_rect、color_sticker_value、clipboard_color_detection、
#     capture_multi_edit_action、capture_fullscreen_action（与当前屏幕/上次操作有关）；
#   * app_version 本身（重置后写回内置版本）。

DEFAULTS = {
    "hotkeys_enabled": True,
    "start_on_boot": False,
    "hotkeys": {
        "capture": "f1", "repeat": "shift+f1", "fullscreen": "alt+f1",
        "monitor": "ctrl+f1", "paste": "f3",
        "edit_clipboard": "ctrl+alt+v", "open_image": "ctrl+alt+e",
        "previous": "ctrl+alt+left", "next": "ctrl+alt+right",
        "hide": "ctrl+shift+h", "close_all": "ctrl+shift+x",
        "touch": "ctrl+shift+t", "paste_clipboard": "ctrl+f3",
        "open_sticker_file": "shift+f3",
        "sticker_panel": "alt+f3",
        "sticker_rotate_left": "ctrl+alt+shift+left",
        "sticker_rotate_right": "ctrl+alt+shift+right",
        "recycle_bin": "ctrl+alt+r",
    },
    "capture_delay": 0,
    "capture_hotkey_suppress": True,
    # 全局热键体检周期（秒）：每跳先探活（线程死了立刻重装），正常时做一次预防性重装。
    # 真机实测无法用合成键探测「Windows 静默摘钩」（keyboard 不响应自身注入的事件），
    # 所以改为定期刷新钩子：数值越小恢复越快，代价是刷新更频繁。
    "hotkey_health_interval": 60,
    "theme": "system",
    "bubble": True, "notification_backend": "win11toast",
    "notification_timeout": 2,
    "copy_notification": True,
    "picker_notification": True,
    "save_notification": True, "operation_notification": True,
    "sticker_notification": True, "open_notification_file": True, "sound": True,
    "save_dir": "",
    "save_as_dir": "",  # 「另存为」上次选择的目录（内部记忆值，不占设置页控件）
    "archive_by_month": True, "archive_by_day": False,
    # 缓存清理范围（逐类开关）与自动清理时机：off 只手动 / start 启动时 / exit 退出时。
    "cache_clear_clipboard": True, "cache_clear_toast": True, "cache_clear_sticker": True,
    "cache_cleanup_timing": "off",
    "inline_edit": True,
    "capture_after_selection": "edit",
    "capture_fullscreen_action": "save",
    "capture_monitor_action": "save",
    "capture_repeat_action": "save",
    "capture_gap_fill": "transparent",
    "filename": "ScreenSnap_%Y%m%d_%H%M%S", "open_dir": False,
    "copy_saved_image": True, "copy_saved_path": False,
    "save_format": "png", "save_quality": 90, "save_background": "#ffffff",
    "sticker_border_enabled": True, "sticker_border_color": "#168cff",
    "sticker_border_width": 2, "sticker_shadow_enabled": False,
    "sticker_selection_effect_enabled": True,
    "sticker_selection_effect_strength": 30,
    "sticker_shadow_color": "#000000", "sticker_shadow_strength": 35,
    "sticker_background_mode": "transparent",
    "editor_image_round_corners": True, "editor_image_corner_radius": 16,
    "rect_corner_enabled": True, "rect_corner_radius": 12,
    "editor_image_border_enabled": False, "editor_image_border_width": 2,
    "editor_image_border_color": "#ffffff",
    "editor_image_shadow_enabled": False, "editor_image_shadow_size": 12,
    "editor_image_shadow_strength": 25, "editor_image_shadow_color": "#000000",
    "capture_quick_sticker_enabled": True,
    "capture_quick_sticker_shortcut": "Space",
    "capture_save_shortcut": "S",
    # 截图遮罩操作键：改为可配置快捷键；多选模式默认 Alt+M。
    "capture_custom_size_shortcut": "F",
    "capture_recapture_shortcut": "R",
    "capture_window_edit_shortcut": "E",
    "capture_multi_select_shortcut": "Alt+M",
    "capture_multi_edit_action": "save",
    "capture_copy_shortcut": "Y",
    # 原地编辑工具栏的隐藏键：只靠快捷键触发，界面上不加隐藏/显示按钮。
    "capture_toolbar_hide_shortcut": "`",
    "text_sticker_font": "", "text_sticker_font_size": 18,
    "text_sticker_color": "#ffffff", "text_sticker_background": "#1f6f5c",
    "text_sticker_width": 420, "text_sticker_lines": 40,
    "clipboard_color_detection": True,
    "color_sticker_value": True, "color_sticker_width": 260, "color_sticker_height": 150,
    "file_sticker_path": True, "file_sticker_max": 8, "file_sticker_width": 360,
    "sticker_panel_thumb": 96,
    "sticker_snap_enabled": True, "sticker_snap_threshold": 8,
    # 吸附提示样式：每张贴图在创建时继承这些默认值，之后由对象自己持有
    # （全局改动只影响之后新建的贴图，不追溯已存在的贴图）。
    "sticker_snap_hint_enabled": True, "sticker_snap_hint_color": "#00ad91",
    "sticker_snap_hint_width": 1, "sticker_snap_hint_style": "dash",
    "sticker_snap_hint_mode": "always", "sticker_snap_hint_duration": 600,
    "sticker_snap_hint_inset": 2, "sticker_snap_hint_preset": "default",
    "sticker_snap_targets": "both", "sticker_follow_window": True,
    "sticker_follow_sticker": True,
    "sticker_follow_interval": 120,
    "magnifier": True, "crosshair": True, "crosshair_color": "#ff0000", "crosshair_width": 1,
    "mask_color": "#000000", "mask_opacity": 70,
    "window_detection": True, "window_auto_select": False, "element_depth": 8,
    "window_hover_detect": True, "window_uia_detect": True,
    "uia_debug_tree": False,
    # 悬停与 UIA 的性能参数：复用半径 0 表示每次都重新查询；预算/熔断给慢机器留出放宽空间。
    # 上次成功启动的版本；与内置版本不一致时启动阶段会重置用户数据（见 core/version.py）。
    "app_version": "",
    "window_hover_reuse_radius": 8,
    "uia_read_budget": 180, "uia_children_limit": 128, "uia_slow_seconds": 0.4,
    "window_hover_interval": 80, "window_hover_color": "#168cff",
    "window_hover_border_color": "#168cff",
    "window_hover_text_color": "#f4fffc",
    "window_hover_badge_color": "#102a31",
    "window_hover_border_width": 2, "window_hover_font_size": 12,
    "window_hover_opacity": 35, "window_hover_fill_mode": "reveal",
    "anchor_style": "border", "selection_border_color": "#168cff",
    "cursor": False, "history_limit": 10,
    "last_capture_rect": [],
    "annotation_tool": "select", "text_alignment": "left", "arrow_style": "filled",
    "arrow_chain": False,
    "pen_chain": False, "marker_chain": False,
    "rect_style": "solid", "ellipse_style": "solid",
    "rect_color": "#ff0000", "ellipse_color": "#ff0000",
    "arrow_color": "#ff0000", "marker_color": "#ff0000", "text_color": "#ff0000",
    "text_background_enabled": False, "text_background": "#fff3a0",
    "text_width": 0, "text_height": 0,
    "text_bold": False, "text_italic": False, "text_underline": False, "text_strikethrough": False,
    "rect_fill_enabled": False, "rect_fill_opacity": 35, "rect_fill_color": "#ff0000",
    "ellipse_fill_enabled": False, "ellipse_fill_opacity": 35, "ellipse_fill_color": "#ff0000",
    "pen_width": 2, "rect_width": 2, "ellipse_width": 2, "arrow_width": 2,
    "marker_width": 4, "eraser_width": 30, "pen_color": "#ff0000",
    "mosaic_cursor_color": "#00c853", "eraser_cursor_color": "#ff8c00",
    "crop_color": "#00ad91", "crop_width": 2,
    "editor_border_color": "#000000", "editor_border_width": 1,
    # 编辑器透明像素（多屏间隙、擦除镂空、圆角外）的预览底色：只影响显示，不写入图片。
    "editor_transparent_background": "theme",
    # 编辑工具栏阴影光晕：小面积工具栏在接近底色的画面上会融进去，加一圈光晕便于定位。
    "editor_toolbar_shadow_enabled": True,
    "editor_toolbar_shadow_color": "#000000",
    "editor_toolbar_shadow_strength": 60,
    # 画布导航与标注交互的可调阈值（此前写死在 annotation_canvas 里）。
    "editor_zoom_wheel_step": 10,
    "editor_rotation_snap": 15,
    "editor_overcanvas_mode": "clip",
    "editor_rotation_handle": "center",
    "editor_checker_tile_size": 8,
    "editor_text_click_delay": 180,
    "marker_opacity": 38, "font": "", "font_size": 18,
    "line_spacing": 1.2, "mosaic_size": 10,
    "mosaic_mode": "blur", "mosaic_brush": True, "mosaic_width": 20,
    "eraser_erase_base": False,
    # 截图取色与定位辅助增强
    "capture_picker_shortcut": "C",
    "magnifier_grid": True, "magnifier_grid_color": "#cccccc",
    # 放大镜尺寸（像素，正方形边长）：采样区域按同一缩放倍率等比换算，见 magnifier_widget。
    "magnifier_size": 140,
    # 提示条要显示的提示项，**列表本身就是开关与顺序**：不在列表里的不显示，
    # 顺序即显示顺序（设置页里勾选与拖动排序，顺序以用户添加的为准）。
    "capture_hint_order": list(HINT_ITEM_IDS),
    # 快捷键提示总开关：关掉后整条提示条不画（放大镜等其它定位辅助不受影响）。
    "capture_hints_enabled": True,
    # 每行提示数：默认每行 2 个；0 表示不限制，只按宽度自动换行。
    "capture_hint_per_line": 2,
    # 采集自检警示：总开关默认关闭；打开后，选区包含本程序自身窗口时在提示条里附加暖色警示。
    # 子项决定哪些自身窗口参与警示（分类见 INTRUDER_WARNING_ITEMS）。
    "intruder_warning_enabled": False,
    "intruder_warning_items": {item_id: True for item_id in INTRUDER_WARNING_IDS},
    # 提示条与放大镜之间的间距（像素）：0 表示紧贴放大镜边缘。
    "capture_hint_gap": 0,
    # 两套提示条外观：普通提示条与采集自检警示提示条各自独立，互不影响。
    "hint_bar_style": copy.deepcopy(DEFAULT_HINT_BAR_STYLE),
    "hint_bar_warning_style": copy.deepcopy(DEFAULT_HINT_BAR_WARNING_STYLE),
    "ruler_enabled": True, "ruler_color": "#00ad91",
    # 标注：序号
    "sequence_font_size": 14, "sequence_start": 1,
    "sequence_shape": "circle", "sequence_text_color": "#ffffff",
    "sequence_fill_color": "#ff0000",     "sequence_preset": "custom",
    # 贴图：回收站
    "sticker_recycle_enabled": True, "sticker_recycle_limit": 10,
    "logging_enabled": True, "log_level": "INFO", "log_when": "midnight", "log_dir": "",
    "log_monthly_folder": True,
}

CAPTURE_SHORTCUT_KEYS = (
    "capture_quick_sticker_shortcut", "capture_save_shortcut",
    "capture_picker_shortcut", "capture_custom_size_shortcut",
    "capture_recapture_shortcut", "capture_window_edit_shortcut",
    "capture_multi_select_shortcut", "capture_copy_shortcut",
    "capture_toolbar_hide_shortcut",
)


def _save_formats():
    """校验保存格式时再导入，避免配置模块加载时拉起截图依赖。"""
    from core.image_io import SAVE_FORMATS

    return SAVE_FORMATS


# 形状填充色默认与对应的线条颜色一致：用户自定义过填充色后不再跟随。
LINKED_FILL_COLORS = {"rect_color": "rect_fill_color", "ellipse_color": "ellipse_fill_color"}


def fill_colors_following_stroke(settings, key, value):
    """线色变化时返回需一并跟随的填充色项；未联动时返回空字典。"""
    fill_key = LINKED_FILL_COLORS.get(key)
    if fill_key and settings.get(fill_key) == settings.get(key):
        return {fill_key: value}
    return {}


def canonical_hotkey(binding):
    """只为冲突检测排序组合键，不改变实际注册时的按键顺序。"""
    parts = [part.strip().lower() for part in binding.split("+")]
    if not all(parts) or len(parts) != len(set(parts)):
        raise ValueError("快捷键组合格式错误")
    return "+".join(sorted(parts))


def _validate_hint_bar_style(key, value):
    """校验一套提示条外观；预设与颜色/圆角/半径都必须是合法值。"""
    defaults = (DEFAULT_HINT_BAR_WARNING_STYLE if key == "hint_bar_warning_style"
                else DEFAULT_HINT_BAR_STYLE)
    valid_ids = (HINT_BAR_WARNING_PRESET_IDS if key == "hint_bar_warning_style"
                 else HINT_BAR_PRESET_IDS)
    if not isinstance(value, dict):
        raise ValueError("提示条样式必须是字典")
    preset = value.get("preset", defaults["preset"])
    result = {"preset": preset if preset in valid_ids else "custom"}
    for field in ("text_color", "fill_color", "border_color"):
        color = value.get(field, defaults[field])
        if not isinstance(color, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
            raise ValueError("提示条样式颜色必须是六位十六进制颜色")
        result[field] = color.lower()
    rounded = value.get("rounded", defaults["rounded"])
    if type(rounded) is not bool:
        raise ValueError("提示条圆角开关必须是布尔值")
    radius = value.get("radius", defaults["radius"])
    if type(radius) is not int or not 0 <= radius <= 20:
        raise ValueError("提示条圆角半径必须是 0 到 20 的整数")
    result["rounded"] = rounded
    result["radius"] = radius
    return result


def hint_bar_style(settings, warning):
    """按是否出现采集自检警示，取对应的提示条外观（缺省回退内置默认样式）。

    返回值在样式字段外附带 `alpha`：填充色不透明度取该套样式的默认值，
    以保证默认外观与改造前一致（普通 220 / 警示 232）；该键只在绘制期使用，不落盘。
    """
    key = "hint_bar_warning_style" if warning else "hint_bar_style"
    fallback = DEFAULT_HINT_BAR_WARNING_STYLE if warning else DEFAULT_HINT_BAR_STYLE
    style = (settings or {}).get(key)
    if not isinstance(style, dict):
        style = fallback
    alpha = HINT_BAR_WARNING_FILL_ALPHA if warning else HINT_BAR_FILL_ALPHA
    return {**style, "alpha": alpha}


def validate(data):
    """只接受已知键和正确类型，拒绝冲突热键。"""
    if not isinstance(data, dict):
        raise ValueError("配置必须是 JSON 对象")
    # 用默认值补齐旧版本配置，避免缺少新选项时界面访问失败。
    result = copy.deepcopy(DEFAULTS)
    data = dict(data)
    data.pop("auto_dir", None)
    data.pop("manual_dir", None)
    if "archive_by_month" not in data and "archive_by_day" not in data:
        if "archive_images" in data:
            if type(data["archive_images"]) is not bool:
                raise ValueError("旧版图片归档开关必须是布尔值")
            if data.get("image_archive_period", "month") not in ("month", "day"):
                raise ValueError("旧版图片归档周期必须是 month 或 day")
            archived = data.get("archive_images") is True
            period = data.get("image_archive_period", "month")
            result["archive_by_month"] = archived and period == "month"
            result["archive_by_day"] = archived and period == "day"
    if "mask_color" not in data:
        legacy_theme = data.get("mask_theme")
        if legacy_theme == "dark":
            result["mask_color"] = "#000000"
        elif legacy_theme == "light":
            result["mask_color"] = "#FFFFFF"
    for key, value in data.items():
        if key not in result:
            continue
        if key == "hotkeys":
            if not isinstance(value, dict):
                raise ValueError("快捷键配置格式错误")
            for action, binding in value.items():
                if action in result[key]:
                    if not isinstance(binding, str):
                        raise ValueError("快捷键必须是字符串")
                    result[key][action] = binding.strip().lower()
        elif type(value) is not type(result[key]):
            raise ValueError(f"配置项 {key} 类型错误")
        elif key == "selection_border_color" and not re.fullmatch(r"#[0-9a-fA-F]{6}", value):
            raise ValueError("截图选区边框颜色必须是六位十六进制颜色")
        elif key == "window_hover_color" and not re.fullmatch(r"#[0-9a-fA-F]{6}", value):
            raise ValueError("控件识别高亮颜色必须是六位十六进制颜色")
        elif key == "window_hover_fill_mode" and value not in ("fill", "reveal"):
            raise ValueError("控件候选框显示模式无效")
        elif key in ("window_hover_border_color", "window_hover_text_color",
                     "window_hover_badge_color") and not re.fullmatch(r"#[0-9a-fA-F]{6}", value):
            raise ValueError("控件提示样式颜色必须是六位十六进制颜色")
        elif key == "mask_color" and not re.fullmatch(r"#[0-9a-fA-F]{6}", value):
            raise ValueError("截图遮罩颜色必须是六位十六进制颜色")
        elif key == "editor_border_color" and not re.fullmatch(r"#[0-9a-fA-F]{6}", value):
            raise ValueError("编辑区边框颜色必须是六位十六进制颜色")
        elif key == "editor_border_width" and not 1 <= value <= 12:
            raise ValueError("编辑区边框宽度必须在 1 到 12 像素之间")
        elif key == "editor_transparent_background" and value not in (
                "theme", "transparent", "dark_checker", "light_checker"):
            raise ValueError("未知的编辑器透明背景样式")
        elif key == "editor_toolbar_shadow_color" and not re.fullmatch(r"#[0-9a-fA-F]{6}", value):
            raise ValueError("工具栏阴影颜色必须是六位十六进制颜色")
        elif key == "editor_toolbar_shadow_strength" and not 0 <= value <= 100:
            raise ValueError("工具栏阴影强度必须在 0 到 100 之间")
        # 数值键先判类型再比范围：repair/import_from 只捕获 ValueError，
        # 直接拿字符串比范围会抛 TypeError 穿透逐键回退，导致整份配置加载失败。
        elif key == "editor_zoom_wheel_step" and (
                type(value) is not int or not 1 <= value <= 50):
            raise ValueError("滚轮缩放步进必须是 1 到 50 之间的整数")
        elif key == "editor_rotation_snap" and value not in (0, 5, 15, 45):
            raise ValueError("旋转吸附角度只能是 0、5、15 或 45 度")
        elif key == "editor_overcanvas_mode" and value not in ("clip", "block"):
            raise ValueError("画布外标注处理只能是裁切或禁止移出")
        elif key == "editor_rotation_handle" and value not in ("center", "top"):
            raise ValueError("旋转按钮位置只能是标注中心或顶部外侧")
        elif key == "editor_checker_tile_size" and (
                type(value) is not int or not 4 <= value <= 32):
            raise ValueError("透明棋盘格边长必须是 4 到 32 之间的整数")
        elif key == "editor_text_click_delay" and (
                type(value) is not int or not 60 <= value <= 400):
            raise ValueError("文字单击等待必须是 60 到 400 之间的整数毫秒")
        elif key == "window_hover_reuse_radius" and (
                type(value) is not int or not 0 <= value <= 20):
            raise ValueError("悬停结果复用半径必须是 0 到 20 之间的整数像素")
        elif key == "uia_read_budget" and (
                type(value) is not int or not 60 <= value <= 600):
            raise ValueError("UIA 读取预算必须是 60 到 600 之间的整数")
        elif key == "uia_children_limit" and (
                type(value) is not int or not 32 <= value <= 512):
            raise ValueError("UIA 单层子控件上限必须是 32 到 512 之间的整数")
        elif key == "uia_slow_seconds" and (
                type(value) not in (int, float) or not 0.1 <= value <= 2.0):
            raise ValueError("UIA 熔断阈值必须是 0.1 到 2.0 之间的秒数")
        elif key == "picker_notification" and not isinstance(value, bool):
            raise ValueError("「取色通知」必须是布尔值")
        elif key == "save_as_dir" and not isinstance(value, str):
            raise ValueError("「另存为」目录必须是字符串")
        elif key == "app_version" and not isinstance(value, str):
            raise ValueError("程序版本号必须是字符串")
        elif key == "cache_cleanup_timing" and value not in ("off", "start", "exit"):
            raise ValueError("自动清理缓存时机只能是关闭、启动时或退出时")
        elif key == "crop_color" and not re.fullmatch(r"#[0-9a-fA-F]{6}", value):
            raise ValueError("裁剪框颜色必须是六位十六进制颜色")
        elif key == "crop_width" and not 1 <= value <= 12:
            raise ValueError("裁剪框线宽必须在 1 到 12 像素之间")
        elif key in ("capture_quick_sticker_shortcut", "capture_save_shortcut",
                     "capture_custom_size_shortcut", "capture_recapture_shortcut",
                     "capture_window_edit_shortcut", "capture_multi_select_shortcut",
                     "capture_copy_shortcut",
                     "capture_toolbar_hide_shortcut"):
            from PySide6.QtGui import QKeySequence

            sequence = QKeySequence(value.strip())
            if sequence.isEmpty() or sequence.count() > 1:
                raise ValueError("截图快捷键必须是一个有效按键或组合")
            result[key] = sequence.toString(QKeySequence.PortableText)
        elif key in ("sticker_border_color", "sticker_shadow_color") and not re.fullmatch(r"#[0-9a-fA-F]{6}", value):
            raise ValueError("贴图颜色必须是六位十六进制颜色")
        elif key in ("pen_color", "rect_color", "ellipse_color", "arrow_color", "marker_color", "text_color", "text_background", "rect_fill_color", "ellipse_fill_color", "mosaic_cursor_color", "eraser_cursor_color") and not re.fullmatch(r"#[0-9a-fA-F]{6}", value):
            raise ValueError("标注工具颜色必须是六位十六进制颜色")
        elif key in ("rect_fill_opacity", "ellipse_fill_opacity") and not 0 <= value <= 100:
            raise ValueError("形状填充不透明度必须在 0 到 100 之间")
        elif key in ("editor_image_border_color", "editor_image_shadow_color") and not re.fullmatch(r"#[0-9a-fA-F]{6}", value):
            raise ValueError("输出边框或阴影颜色必须是六位十六进制颜色")
        elif key == "sticker_border_width" and not 0 <= value <= 20:
            raise ValueError("贴图描边宽度必须在 0 到 20 像素之间")
        elif key == "sticker_shadow_strength" and not 0 <= value <= 100:
            raise ValueError("贴图阴影强度必须在 0 到 100 之间")
        elif key == "sticker_selection_effect_strength" and not 0 <= value <= 100:
            raise ValueError("贴图选中光晕强度必须在 0 到 100 之间")
        elif key == "sticker_background_mode" and value not in (
                "transparent", "pseudo", "dark_checker", "light_checker"):
            raise ValueError("未知贴图透明背景模式")
        elif key in ("editor_image_corner_radius", "rect_corner_radius") and not 0 <= value <= 100:
            raise ValueError("圆角半径必须在 0 到 100 像素之间")
        elif key == "editor_image_border_width" and not 0 <= value <= 20:
            raise ValueError("输出边框宽度必须在 0 到 20 像素之间")
        elif key == "editor_image_shadow_size" and not 0 <= value <= 60:
            raise ValueError("输出阴影尺寸必须在 0 到 60 像素之间")
        elif key == "editor_image_shadow_strength" and not 0 <= value <= 100:
            raise ValueError("输出阴影强度必须在 0 到 100 之间")
        elif key == "save_format" and value not in _save_formats():
            raise ValueError("未知保存格式")
        elif key == "save_quality" and not 1 <= value <= 100:
            raise ValueError("保存质量必须在 1 到 100 之间")
        elif key in ("text_sticker_color", "text_sticker_background") and not re.fullmatch(r"#[0-9a-fA-F]{6}", value):
            raise ValueError("文字贴图颜色必须是六位十六进制颜色")
        elif key == "text_sticker_font_size" and not 6 <= value <= 72:
            raise ValueError("文字贴图字号必须在 6 到 72 之间")
        elif key == "text_sticker_width" and not 160 <= value <= 1600:
            raise ValueError("文字贴图最大宽度必须在 160 到 1600 像素之间")
        elif key == "text_sticker_lines" and not 1 <= value <= 500:
            raise ValueError("文字贴图最大行数必须在 1 到 500 之间")
        elif key == "color_sticker_width" and not 80 <= value <= 800:
            raise ValueError("颜色贴图宽度必须在 80 到 800 像素之间")
        elif key == "color_sticker_height" and not 60 <= value <= 600:
            raise ValueError("颜色贴图高度必须在 60 到 600 像素之间")
        elif key == "file_sticker_max" and not 1 <= value <= 20:
            raise ValueError("文件贴图最多展示数量必须在 1 到 20 之间")
        elif key == "file_sticker_width" and not 240 <= value <= 800:
            raise ValueError("文件贴图宽度必须在 240 到 800 像素之间")
        elif key == "sticker_panel_thumb" and not 48 <= value <= 200:
            raise ValueError("贴图管理缩略图宽度必须在 48 到 200 像素之间")
        elif key == "sticker_snap_threshold" and not 1 <= value <= 40:
            raise ValueError("贴图吸附距离必须在 1 到 40 像素之间")
        elif key == "sticker_snap_targets" and value not in ("screen", "window", "both"):
            raise ValueError("未知贴图吸附目标")
        elif key == "hotkey_health_interval" and not 15 <= value <= 600:
            raise ValueError("热键体检间隔必须在 15 到 600 秒之间")
        elif key == "sticker_snap_hint_color" and not re.fullmatch(r"#[0-9a-fA-F]{6}", value):
            raise ValueError("吸附提示颜色必须是六位十六进制颜色")
        elif key == "sticker_snap_hint_width" and not 1 <= value <= 6:
            raise ValueError("吸附提示线宽必须在 1 到 6 像素之间")
        elif key == "sticker_snap_hint_style" and value not in ("solid", "dash", "dot", "dash_dot"):
            raise ValueError("未知吸附提示线型")
        elif key == "sticker_snap_hint_mode" and value not in ("drag", "always"):
            raise ValueError("未知吸附提示显示时机")
        elif key == "sticker_snap_hint_duration" and not 0 <= value <= 3000:
            raise ValueError("吸附提示淡出延时必须在 0 到 3000 毫秒之间")
        elif key == "sticker_snap_hint_inset" and not 0 <= value <= 8:
            raise ValueError("吸附提示内缩距离必须在 0 到 8 像素之间")
        elif key == "sticker_snap_hint_preset" and value not in (
                "default", "blue_solid", "orange_dot", "white_contrast", "custom"):
            raise ValueError("未知吸附提示预设")
        elif key == "notification_backend" and value not in ("win11toast", "legacy"):
            raise ValueError("未知通知方式")
        elif key == "notification_timeout" and not 0 <= value <= 60:
            raise ValueError("通知时长必须在 0 到 60 秒之间")
        elif key == "theme" and value not in ("system", "dark", "light"):
            raise ValueError("未知应用主题")
        elif key == "sticker_follow_interval" and not 30 <= value <= 1000:
            raise ValueError("贴图跟随刷新间隔必须在 30 到 1000 毫秒之间")
        elif key == "save_background" and not re.fullmatch(r"#[0-9a-fA-F]{6}", value):
            raise ValueError("保存底色必须是六位十六进制颜色")
        elif key == "log_level" and str(value).upper() not in (
            "ERROR", "WARNING", "INFO", "DEBUG", "TRACE"):
            raise ValueError("未知日志等级")
        elif key == "log_when" and value not in ("midnight", "H"):
            raise ValueError("未知日志分割周期")
        elif key == "capture_delay" and not 0 <= value <= 5000:
            raise ValueError("截图延迟必须在 0 到 5000 毫秒之间")
        elif key == "crosshair_color" and not re.fullmatch(r"#[0-9a-fA-F]{6}", value):
            raise ValueError("十字线颜色必须是六位十六进制颜色")
        elif key == "crosshair_width" and not 1 <= value <= 8:
            raise ValueError("十字线宽度必须在 1 到 8 像素之间")
        elif key == "mask_opacity" and not 0 <= value <= 100:
            raise ValueError("遮罩透明度必须在 0 到 100 之间")
        elif key == "magnifier_size" and not 100 <= value <= 320:
            raise ValueError("放大镜尺寸必须在 100 到 320 像素之间")
        elif key == "capture_hint_per_line" and not 0 <= value <= 8:
            raise ValueError("每行提示数必须在 0 到 8 之间（0 表示按宽度自动换行）")
        elif key == "capture_hint_gap" and not 0 <= value <= 40:
            raise ValueError("提示条与放大镜间距必须在 0 到 40 像素之间")
        elif key in ("hint_bar_style", "hint_bar_warning_style"):
            # 提示条外观：只认已知字段与合法取值，非法颜色/圆角/半径直接回退默认样式。
            result[key] = _validate_hint_bar_style(key, value)
            continue
        elif key == "capture_hint_order":
            # 提示项列表既是开关也是顺序：只保留已知 id，去重且保持用户顺序。
            if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
                raise ValueError("提示项配置必须是字符串列表")
            seen = []
            for item in value:
                if item in HINT_ITEM_IDS and item not in seen:
                    seen.append(item)
            result[key] = seen
            continue
        elif key == "intruder_warning_items":
            # 子开关：只认已知分类 id，其余忽略；缺失的分类回退为开启。
            if not isinstance(value, dict):
                raise ValueError("采集自检警示子项必须是字典")
            result[key] = {item_id: bool(value.get(item_id, True))
                           for item_id in INTRUDER_WARNING_IDS}
            continue
        elif key == "element_depth" and not 1 <= value <= 32:
            raise ValueError("窗口元素识别层级必须在 1 到 32 之间")
        elif key in ("archive_by_month", "archive_by_day", "cache_clear_clipboard",
                     "cache_clear_toast", "cache_clear_sticker") and type(value) is not bool:
            raise ValueError("图片归档与缓存清理开关必须是布尔值")
        elif key == "window_hover_interval" and not 16 <= value <= 500:
            raise ValueError("悬停识别刷新间隔必须在 16 到 500 毫秒之间")
        elif key == "window_hover_opacity" and not 0 <= value <= 100:
            raise ValueError("控件识别高亮不透明度必须在 0 到 100 之间")
        elif key == "window_hover_border_width" and not 1 <= value <= 6:
            raise ValueError("控件提示边线宽必须在 1 到 6 像素之间")
        elif key == "window_hover_font_size" and not 8 <= value <= 32:
            raise ValueError("控件尺寸标签字号必须在 8 到 32 像素之间")
        elif key == "marker_opacity" and not 1 <= value <= 100:
            raise ValueError("荧光笔不透明度必须在 1 到 100 之间")
        elif key == "font_size" and not 6 <= value <= 200:
            raise ValueError("字号必须在 6 到 200 之间")
        elif key == "text_width" and not 0 <= value <= 2000:
            raise ValueError("文字宽度必须在 0 到 2000 像素之间")
        elif key == "text_height" and not 0 <= value <= 2000:
            raise ValueError("文字高度必须在 0 到 2000 像素之间")
        elif key == "eraser_width" and not 10 <= value <= 100:
            raise ValueError("橡皮擦直径必须在 10 到 100 之间")
        elif key == "mosaic_width" and not 4 <= value <= 100:
            raise ValueError("马赛克涂抹笔刷宽度必须在 4 到 100 之间")
        elif key in ("pen_width", "rect_width", "ellipse_width", "arrow_width",
                     "marker_width") and not 1 <= value <= 50:
            raise ValueError("标注线宽必须在 1 到 50 之间")
        elif key == "annotation_tool" and value not in (
            "select", "rect", "ellipse", "arrow", "pen", "eraser", "text",
            "mosaic", "picker", "crop", "marker", "number"
        ):
            if value in ("wide", "square"):
                result[key] = "select"
            else:
                raise ValueError("未知标注工具")
        elif key == "text_alignment" and value not in ("left", "center", "right"):
            raise ValueError("未知文字对齐方式")
        elif key == "capture_after_selection" and value not in ("save", "edit", "copy"):
            raise ValueError("截图选区后的行为必须是仅保存、进入编辑或仅复制")
        elif key in ("capture_fullscreen_action", "capture_monitor_action",
                     "capture_repeat_action") and value not in ("save", "edit"):
            raise ValueError("全屏、显示器和上次截图的完成动作必须是保存或编辑")
        elif key == "capture_gap_fill" and value not in ("transparent", "black", "white"):
            raise ValueError("显示器间隙填充必须是透明、黑色或白色")
        elif key == "capture_multi_edit_action" and value not in ("save", "discard"):
            raise ValueError("进入多选前的编辑处理必须是保存或丢弃")
        elif key == "arrow_style" and value not in (
            "filled", "open", "dashed", "double_filled", "double_open", "double_dashed",
            "line", "dashed_line", "rect_filled", "rect_open", "rect_dashed",
        ):
            raise ValueError("未知箭头样式")
        elif key in ("rect_style", "ellipse_style") and value not in ("solid", "dash"):
            raise ValueError("未知线型样式")
        elif key == "last_capture_rect" and (value and (
            len(value) != 4 or any(type(number) is not int for number in value) or
            value[2] <= 0 or value[3] <= 0
        )):
            raise ValueError("上次截图区域格式错误")
        elif key == "capture_picker_shortcut":
            from PySide6.QtGui import QKeySequence
            sequence = QKeySequence(value.strip())
            if sequence.isEmpty() or sequence.count() > 1:
                raise ValueError("取色快捷键必须是一个有效按键或组合")
            result[key] = sequence.toString(QKeySequence.PortableText)
        elif key in ("magnifier_grid_color", "ruler_color",
                      "sequence_text_color", "sequence_fill_color") and not re.fullmatch(r"#[0-9a-fA-F]{6}", value):
            raise ValueError("颜色必须是六位十六进制颜色")
        elif key == "sequence_font_size" and not 6 <= value <= 200:
            raise ValueError("序号字号必须在 6 到 200 之间")
        elif key == "sequence_shape" and value not in (
                "circle", "square", "triangle", "diamond", "pentagon", "hexagon", "star",
                "heart", "arrow", "bubble", "cloud", "plus", "drop"):
            raise ValueError("未知序号形状")
        elif key == "sequence_preset" and value not in (
                "custom", "red_circle", "blue_square", "green_star",
                "amber_diamond", "purple_pentagon", "teal_hexagon",
                "red_heart", "blue_arrow"):
            raise ValueError("未知序号预设")
        elif key == "sequence_start" and not 0 <= value <= 999:
            raise ValueError("序号起始值必须在 0 到 999 之间")
        elif key == "history_limit" and not 1 <= value <= 10000:
            raise ValueError("历史图片数量必须在 1 到 10000 之间")
        elif key == "sticker_recycle_limit" and not 1 <= value <= 200:
            raise ValueError("贴图回收站上限必须在 1 到 200 之间")
        else:
            result[key] = value
    bindings = [canonical_hotkey(binding) for binding in result["hotkeys"].values() if binding]
    if len(bindings) != len(set(bindings)):
        raise ValueError("快捷键冲突：不能为多个操作设置同一热键")
    capture_bindings = [canonical_hotkey(result[key]) for key in CAPTURE_SHORTCUT_KEYS
                        if result[key]]
    if len(capture_bindings) != len(set(capture_bindings)):
        raise ValueError("截图快捷键冲突：不能为多个截图操作设置同一按键")
    if result["archive_by_month"] and result["archive_by_day"]:
        raise ValueError("按月和按日归档不能同时开启")
    # 颜色值统一规范为小写，避免大小写不一致带来的比较/日志隐患（如 #168CFF 与 #168cff）。
    # 仅作用于六位十六进制颜色字符串；同时兜底历史配置或手改的大写值。
    for key, value in result.items():
        if isinstance(value, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", value):
            result[key] = value.lower()
    return result


# 旧箭头样式值到新样式 key 的迁移映射；旧 key 不再保留，加载时统一迁走。
ARROW_STYLE_LEGACY = {
    "filled": "filled",
    "open": "open",
    "double_filled": "double_filled",
    "double": "double_open",
    "solid_line": "line",
    "open_line": "rect_open",
    "solid_dash": "dashed_line",
    "open_dash": "rect_dashed",
}


def migrate_legacy_settings(data):
    if not isinstance(data, dict):
        return data
    migrated = dict(data)
    old_auto_dir = migrated.pop("auto_dir", "")
    old_manual_dir = migrated.pop("manual_dir", "")
    if not migrated.get("save_dir"):
        migrated["save_dir"] = old_auto_dir or old_manual_dir or ""
    for old_key, new_key in (
            ("capture_round_corners", "editor_image_round_corners"),
            ("capture_corner_radius", "editor_image_corner_radius"),
            ("capture_notification", "copy_notification")):
        if old_key in migrated:
            migrated.setdefault(new_key, migrated[old_key])
            del migrated[old_key]
    default_updates = {
        "sound": (False, True),
        "crosshair_color": ("#000000", "#ff0000"),
        "element_depth": (12, 8),
        "mask_opacity": (60, 70),
        "history_limit": (100, 10),
        "rect_corner_enabled": (False, True),
        "sticker_shadow_enabled": (True, False),
        "sticker_recycle_limit": (50, 10),
        "marker_width": (2, 4),
        # 实机实测：旧默认 180 时单次悬停查询约 65ms 且每次都跑满预算，故与复用半径一起下调。
        "window_hover_reuse_radius": (4, 8),
    }
    for key, (old_default, new_default) in default_updates.items():
        if migrated.get(key) == old_default:
            migrated[key] = new_default
    hotkeys = migrated.get("hotkeys")
    if isinstance(hotkeys, dict):
        hotkeys = dict(hotkeys)
        for key, old_default, new_default in (
                ("repeat", "ctrl+shift+f2", "shift+f1"),
                ("fullscreen", "ctrl+shift+f1", "alt+f1"),
                ("open_image", "ctrl+alt+o", "ctrl+alt+e"),
                ("open_sticker_file", "ctrl+alt+n", "shift+f3"),
                ("sticker_panel", "ctrl+alt+p", "alt+f3")):
            if hotkeys.get(key) == old_default:
                try:
                    target = canonical_hotkey(new_default)
                except ValueError:
                    continue
                occupied = set()
                for name, binding in hotkeys.items():
                    if name == key or not isinstance(binding, str) or not binding:
                        continue
                    try:
                        occupied.add(canonical_hotkey(binding))
                    except ValueError:
                        continue
                if target not in occupied:
                    hotkeys[key] = new_default
        migrated["hotkeys"] = hotkeys
    legacy = migrated.get("arrow_style")
    if legacy in ARROW_STYLE_LEGACY:
        migrated["arrow_style"] = ARROW_STYLE_LEGACY[legacy]
    # 新加的原地编辑提示项：旧配置的 capture_hint_order 里没有它们。若列表仍是上一版
    # 的完整默认（用户没自定义过），追加到末尾，升级后能直接看到；自定义过的列表不动，
    # 也不会在用户取消勾选后再被追加回来。
    order = migrated.get("capture_hint_order")
    if isinstance(order, list):
        new_items = ["inline_edit", "inline_menu", "inline_history"]
        legacy_default = [item_id for item_id in HINT_ITEM_IDS if item_id not in new_items]
        if order == legacy_default:
            migrated["capture_hint_order"] = legacy_default + new_items
    return migrated


def repair(data):
    """逐键回退：只丢弃非法配置项，尽量保留用户的其余设置。"""
    result = copy.deepcopy(DEFAULTS)
    dropped = []
    for key, value in data.items():
        if key not in result or key == "hotkeys":
            continue
        try:
            result[key] = validate({key: value})[key]
        except ValueError:
            dropped.append(key)
    hotkeys = data.get("hotkeys")
    if isinstance(hotkeys, dict):
        # 热键需整体校验，冲突时只能整组回退到默认绑定。
        try:
            result["hotkeys"] = validate({"hotkeys": hotkeys})["hotkeys"]
        except ValueError:
            dropped.append("hotkeys")
    try:
        validate({key: result[key] for key in CAPTURE_SHORTCUT_KEYS})
    except ValueError:
        for key in CAPTURE_SHORTCUT_KEYS:
            result[key] = DEFAULTS[key]
        dropped.append("capture_shortcuts")
    return result, dropped


class ConfigManager:
    """在用户配置文件和内存中的完整配置之间同步。"""

    def __init__(self, path):
        self.path = Path(path)
        self.data = self.load()

    def load(self):
        """按缺失、无法解析、单项非法三种情况分别回退，并保留可排查的现场。"""
        # 本次载入的现场：哪些键被回退成默认、哪些未知旧键被忽略（供启动提示与日志使用）。
        self.repaired_keys = []
        self.dropped_keys = []
        logger = logging.getLogger("screensnap")
        if not self.path.exists():
            self.data = copy.deepcopy(DEFAULTS)
            self.save_safely()
            logger.info("未找到配置文件，已创建默认配置: %s", self.path)
            return self.data
        try:
            text = self.path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as error:
            # 无权限或磁盘异常时不改动原文件，本次会话退回默认设置。
            logger.error("无法读取配置文件，本次使用默认设置: %s (%s)", self.path, error)
            self.data = copy.deepcopy(DEFAULTS)
            return self.data
        loaded = None
        raw = None
        try:
            raw = json.loads(text)
            loaded = migrate_legacy_settings(raw)
            self.data = validate(loaded)
        except ValueError as error:
            backup = self.backup()
            if isinstance(loaded, dict):
                self.data, dropped = repair(loaded)
                # repair() 的第二个返回值是「被回退成默认的键」；未知旧键另有判定。
                self.repaired_keys = list(dropped or [])
                self.dropped_keys = [k for k in loaded
                                   if k not in self.data]
                logging.getLogger("screensnap").warning(
                    "配置修复：回退 %d 项 %s；忽略未知键 %d 项 %s",
                    len(self.repaired_keys), self.repaired_keys[:8],
                    len(self.dropped_keys), self.dropped_keys[:8])
                logger.warning("配置文件无效，已备份到 %s；回退 %s 等 %d 项: %s", backup,
                               "、".join(dropped[:5]) or "冲突热键", len(dropped), error)
            else:
                self.data = copy.deepcopy(DEFAULTS)
                logger.warning("配置文件无法解析，已备份到 %s 并全部回退默认值: %s", backup, error)
            self.save_safely()
            return self.data
        migrated = loaded != raw
        if self.data != loaded or migrated:
            missing = sorted(set(self.data) - set(loaded))
            self.save_safely()
            if missing:
                logger.info("配置缺少 %d 项，已用默认值补齐: %s", len(missing), "、".join(missing[:5]))
            elif migrated:
                logger.info("已迁移旧版配置并保存: %s", self.path)
        logger.debug("已加载配置: %s", self.path)
        return self.data

    def backup(self):
        """把无法使用的配置文件改名保留，避免覆盖用户现场。"""
        backup = self.path.with_name(f"{self.path.name}.broken-{time_ns()}")
        try:
            self.path.replace(backup)
        except OSError as error:
            logging.getLogger("screensnap").error("无法备份配置文件 %s: %s", self.path, error)
            return None
        return backup

    def save_safely(self):
        """落盘失败时保留内存配置，保证程序仍能继续运行。"""
        try:
            self.save()
            return True
        except OSError as error:
            logging.getLogger("screensnap").error("无法写入配置文件 %s: %s", self.path, error)
            return False

    def save(self):
        """先校验再替换目标文件，避免写到一半留下损坏的 JSON。"""
        validated = validate(self.data)
        self.data.clear()
        self.data.update(validated)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.path)

    def export_to(self, path):
        """导出经过校验的当前设置，不改变程序正在使用的配置路径。"""
        Path(path).write_text(json.dumps(validate(self.data), ensure_ascii=False, indent=2), encoding="utf-8")

    def import_from(self, path):
        """导入成功后才更新内存与本机配置，错误文件不会覆盖原设置。"""
        imported = validate(migrate_legacy_settings(
            json.loads(Path(path).read_text(encoding="utf-8"))))
        self.data.clear()
        self.data.update(imported)
        self.save()