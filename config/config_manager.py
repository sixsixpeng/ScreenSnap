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


DEFAULTS = {
    "hotkeys_enabled": True,
    "start_on_boot": False,
    "hotkeys": {
        "capture": "f1", "repeat": "ctrl+shift+f2", "fullscreen": "ctrl+shift+f1",
        "monitor": "ctrl+f1", "paste": "f3",
        "edit_clipboard": "ctrl+alt+v", "open_image": "ctrl+alt+o",
        "previous": "ctrl+alt+left", "next": "ctrl+alt+right",
        "hide": "ctrl+shift+h", "close_all": "ctrl+shift+x",
        "touch": "ctrl+shift+t", "paste_clipboard": "ctrl+f3",
        "open_sticker_file": "ctrl+alt+n",
        "sticker_panel": "ctrl+alt+p",
        "sticker_rotate_left": "ctrl+alt+shift+left",
        "sticker_rotate_right": "ctrl+alt+shift+right",
        "recycle_bin": "ctrl+alt+r",
    },
    "capture_delay": 0,
    "capture_hotkey_suppress": False,
    "theme": "system",
    "bubble": True, "notification_backend": "win11toast",
    "capture_notification": True, "save_notification": True,
    "sticker_notification": True, "open_notification_file": True, "sound": False,
    "auto_dir": "", "manual_dir": "",
    "archive_by_month": True, "archive_by_day": False,
    "inline_edit": True,
    "capture_after_selection": "edit",
    "filename": "ScreenSnap_%Y%m%d_%H%M%S", "open_dir": False,
    "copy_saved_image": True, "copy_saved_path": False,
    "save_format": "png", "save_quality": 90, "save_background": "#ffffff",
    "sticker_border_enabled": True, "sticker_border_color": "#168cff",
    "sticker_border_width": 2, "sticker_shadow_enabled": True,
    "sticker_selection_effect_enabled": True,
    "sticker_selection_effect_strength": 30,
    "sticker_shadow_color": "#000000", "sticker_shadow_strength": 35,
    "sticker_background_mode": "transparent",
    "editor_image_round_corners": True, "editor_image_corner_radius": 16,
    "rect_corner_enabled": False, "rect_corner_radius": 12,
    "editor_image_border_enabled": False, "editor_image_border_width": 2,
    "editor_image_border_color": "#ffffff",
    "editor_image_shadow_enabled": False, "editor_image_shadow_size": 12,
    "editor_image_shadow_strength": 25, "editor_image_shadow_color": "#000000",
    "capture_quick_sticker_enabled": True,
    "capture_quick_sticker_shortcut": "Space",
    "capture_save_shortcut": "S",
    "text_sticker_font": "", "text_sticker_font_size": 18,
    "text_sticker_color": "#ffffff", "text_sticker_background": "#1f6f5c",
    "text_sticker_width": 420, "text_sticker_lines": 40,
    "clipboard_color_detection": True,
    "color_sticker_value": True, "color_sticker_width": 260, "color_sticker_height": 150,
    "file_sticker_path": True, "file_sticker_max": 8, "file_sticker_width": 360,
    "sticker_panel_thumb": 96,
    "sticker_snap_enabled": True, "sticker_snap_threshold": 8,
    "sticker_snap_targets": "both", "sticker_follow_window": True,
    "sticker_follow_sticker": True,
    "sticker_follow_interval": 120,
    "magnifier": True, "crosshair": True, "crosshair_color": "#000000", "crosshair_width": 1,
    "mask_color": "#000000", "mask_opacity": 60,
    "window_detection": True, "window_auto_select": False, "element_depth": 12,
    "window_hover_detect": True, "window_uia_detect": True,
    "uia_debug_tree": False,
    "window_hover_interval": 80, "window_hover_color": "#168cff",
    "window_hover_border_color": "#168cff",
    "window_hover_text_color": "#f4fffc",
    "window_hover_badge_color": "#102a31",
    "window_hover_border_width": 2, "window_hover_font_size": 12,
    "window_hover_opacity": 35, "window_hover_fill_mode": "reveal",
    "anchor_style": "border", "selection_border_color": "#168cff",
    "cursor": False, "history_limit": 100,
    "last_capture_rect": [],
    "annotation_tool": "select", "text_alignment": "left", "arrow_style": "filled",
    "rect_style": "solid", "ellipse_style": "solid",
    "rect_color": "#ff0000", "ellipse_color": "#ff0000",
    "arrow_color": "#ff0000", "marker_color": "#ff0000", "text_color": "#ff0000",
    "rect_fill_enabled": False, "rect_fill_opacity": 35,
    "ellipse_fill_enabled": False, "ellipse_fill_opacity": 35,
    "pen_width": 2, "rect_width": 2, "ellipse_width": 2, "arrow_width": 2,
    "marker_width": 2, "eraser_width": 30, "pen_color": "#ff0000",
    "crop_color": "#00ad91", "crop_width": 2,
    "editor_border_color": "#000000", "editor_border_width": 1,
    "marker_opacity": 38, "font": "", "font_size": 18,
    "line_spacing": 1.2, "mosaic_size": 10,
    "mosaic_mode": "blocks",
    # 截图取色与定位辅助增强
    "capture_picker_shortcut": "C",
    "magnifier_grid": True, "magnifier_grid_color": "#cccccc",
    "ruler_enabled": True, "ruler_color": "#00ad91",
    # 标注：序号
    "sequence_font_size": 14, "sequence_start": 1,
    "sequence_shape": "circle", "sequence_text_color": "#ffffff",
    "sequence_fill_color": "#ff0000",     "sequence_preset": "custom",
    # 贴图：回收站
    "sticker_recycle_enabled": True, "sticker_recycle_limit": 50,
    "logging_enabled": True, "log_level": "INFO", "log_when": "midnight", "log_dir": "",
    "log_monthly_folder": True,
}


def _save_formats():
    """校验保存格式时再导入，避免配置模块加载时拉起截图依赖。"""
    from core.image_io import SAVE_FORMATS

    return SAVE_FORMATS


def canonical_hotkey(binding):
    """只为冲突检测排序组合键，不改变实际注册时的按键顺序。"""
    parts = [part.strip().lower() for part in binding.split("+")]
    if not all(parts) or len(parts) != len(set(parts)):
        raise ValueError("快捷键组合格式错误")
    return "+".join(sorted(parts))


def validate(data):
    """只接受已知键和正确类型，拒绝冲突热键。"""
    if not isinstance(data, dict):
        raise ValueError("配置必须是 JSON 对象")
    # 用默认值补齐旧版本配置，避免缺少新选项时界面访问失败。
    result = copy.deepcopy(DEFAULTS)
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
        elif key == "crop_color" and not re.fullmatch(r"#[0-9a-fA-F]{6}", value):
            raise ValueError("裁剪框颜色必须是六位十六进制颜色")
        elif key == "crop_width" and not 1 <= value <= 12:
            raise ValueError("裁剪框线宽必须在 1 到 12 像素之间")
        elif key in ("capture_quick_sticker_shortcut", "capture_save_shortcut"):
            from PySide6.QtGui import QKeySequence

            sequence = QKeySequence(value.strip())
            if sequence.isEmpty() or sequence.count() > 1:
                raise ValueError("截图快捷键必须是一个有效按键或组合")
            result[key] = sequence.toString(QKeySequence.PortableText)
        elif key in ("sticker_border_color", "sticker_shadow_color") and not re.fullmatch(r"#[0-9a-fA-F]{6}", value):
            raise ValueError("贴图颜色必须是六位十六进制颜色")
        elif key in ("pen_color", "rect_color", "ellipse_color", "arrow_color", "marker_color", "text_color") and not re.fullmatch(r"#[0-9a-fA-F]{6}", value):
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
        elif key == "notification_backend" and value not in ("win11toast", "legacy"):
            raise ValueError("未知通知方式")
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
        elif key == "element_depth" and not 1 <= value <= 32:
            raise ValueError("窗口元素识别层级必须在 1 到 32 之间")
        elif key in ("archive_by_month", "archive_by_day") and type(value) is not bool:
            raise ValueError("图片归档开关必须是布尔值")
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
        elif key == "eraser_width" and not 10 <= value <= 100:
            raise ValueError("橡皮擦直径必须在 10 到 100 之间")
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
        elif key == "arrow_style" and value not in (
            "filled", "open", "double", "double_filled",
            "solid_line", "open_line", "solid_dash", "open_dash",
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
        elif key == "sticker_recycle_limit" and not 1 <= value <= 200:
            raise ValueError("贴图回收站上限必须在 1 到 200 之间")
        else:
            result[key] = value
    bindings = [canonical_hotkey(binding) for binding in result["hotkeys"].values() if binding]
    if len(bindings) != len(set(bindings)):
        raise ValueError("快捷键冲突：不能为多个操作设置同一热键")
    if result["archive_by_month"] and result["archive_by_day"]:
        raise ValueError("按月和按日归档不能同时开启")
    # 颜色值统一规范为小写，避免大小写不一致带来的比较/日志隐患（如 #168CFF 与 #168cff）。
    # 仅作用于六位十六进制颜色字符串；同时兜底历史配置或手改的大写值。
    for key, value in result.items():
        if isinstance(value, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", value):
            result[key] = value.lower()
    return result


def migrate_legacy_settings(data):
    if not isinstance(data, dict):
        return data
    migrated = dict(data)
    for old_key, new_key in (
            ("capture_round_corners", "editor_image_round_corners"),
            ("capture_corner_radius", "editor_image_corner_radius")):
        if old_key in migrated:
            migrated.setdefault(new_key, migrated[old_key])
            del migrated[old_key]
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
    return result, dropped


class ConfigManager:
    """在用户配置文件和内存中的完整配置之间同步。"""

    def __init__(self, path):
        self.path = Path(path)
        self.data = self.load()

    def load(self):
        """按缺失、无法解析、单项非法三种情况分别回退，并保留可排查的现场。"""
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
        try:
            loaded = migrate_legacy_settings(json.loads(text))
            self.data = validate(loaded)
        except ValueError as error:
            backup = self.backup()
            if isinstance(loaded, dict):
                self.data, dropped = repair(loaded)
                logger.warning("配置文件无效，已备份到 %s；回退 %s 等 %d 项: %s", backup,
                               "、".join(dropped[:5]) or "冲突热键", len(dropped), error)
            else:
                self.data = copy.deepcopy(DEFAULTS)
                logger.warning("配置文件无法解析，已备份到 %s 并全部回退默认值: %s", backup, error)
            self.save_safely()
            return self.data
        if self.data != loaded:
            missing = sorted(set(self.data) - set(loaded))
            self.save_safely()
            logger.info("配置缺少 %d 项，已用默认值补齐: %s", len(missing), "、".join(missing[:5]))
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