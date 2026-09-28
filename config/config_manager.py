"""JSON 配置的加载、保存和导入导出。"""

import copy
import json
import logging
from time import time_ns
import re
from pathlib import Path

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
        "sticker_panel": "ctrl+alt+p",
    },
    "capture_delay": 0,
    "bubble": True, "capture_notification": True, "save_notification": True,
    "sticker_notification": True, "sound": False,
    "auto_dir": "", "manual_dir": "",
    "inline_edit": True,
    "filename": "_%Y%m%d_%H%M%S", "open_dir": False,
    "copy_saved_image": True, "copy_saved_path": False,
    "save_format": "png", "save_quality": 90, "save_background": "#ffffff",
    "sticker_border_enabled": True, "sticker_border_color": "#00ad91",
    "sticker_border_width": 2, "sticker_shadow_enabled": True,
    "sticker_shadow_color": "#000000", "sticker_shadow_strength": 35,
    "text_sticker_font": "", "text_sticker_font_size": 18,
    "text_sticker_color": "#ffffff", "text_sticker_background": "#1f6f5c",
    "text_sticker_width": 420, "text_sticker_lines": 40,
    "clipboard_color_detection": True,
    "color_sticker_value": True, "color_sticker_width": 260, "color_sticker_height": 150,
    "file_sticker_path": True, "file_sticker_max": 8, "file_sticker_width": 360,
    "sticker_panel_thumb": 96,
    "sticker_snap_enabled": True, "sticker_snap_threshold": 8,
    "sticker_snap_targets": "both", "sticker_follow_window": True,
    "sticker_follow_interval": 120,
    "magnifier": True, "crosshair": True, "crosshair_color": "#ff0000", "crosshair_width": 1,
    "mask_theme": "dark", "mask_opacity": 50,
    "window_detection": True, "window_auto_select": False, "element_depth": 3,
    "window_hover_detect": True, "window_uia_detect": True,
    "window_hover_interval": 80,
    "anchor_style": "border", "selection_border_color": "#ff0000",
    "cursor": False, "history_limit": 100,
    "last_capture_rect": [],
    "annotation_tool": "select", "text_alignment": "left", "arrow_style": "filled",
    "rect_style": "solid", "ellipse_style": "solid",
    "pen_width": 2, "rect_width": 2, "ellipse_width": 2, "arrow_width": 2,
    "marker_width": 2, "eraser_width": 30, "pen_color": "#ff0000",
    "crop_color": "#00ad91", "crop_width": 2,
    "editor_border_color": "#000000", "editor_border_width": 1,
    "marker_opacity": 38, "font": "", "font_size": 18,
    "line_spacing": 1.2, "mosaic_size": 10,
    "mosaic_mode": "blocks",
    "logging_enabled": True, "log_level": "INFO", "log_when": "midnight", "log_dir": "",
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
        elif key == "editor_border_color" and not re.fullmatch(r"#[0-9a-fA-F]{6}", value):
            raise ValueError("编辑区边框颜色必须是六位十六进制颜色")
        elif key == "editor_border_width" and not 1 <= value <= 12:
            raise ValueError("编辑区边框宽度必须在 1 到 12 像素之间")
        elif key == "crop_color" and not re.fullmatch(r"#[0-9a-fA-F]{6}", value):
            raise ValueError("裁剪框颜色必须是六位十六进制颜色")
        elif key == "crop_width" and not 1 <= value <= 12:
            raise ValueError("裁剪框线宽必须在 1 到 12 像素之间")
        elif key in ("sticker_border_color", "sticker_shadow_color") and not re.fullmatch(r"#[0-9a-fA-F]{6}", value):
            raise ValueError("贴图颜色必须是六位十六进制颜色")
        elif key == "sticker_border_width" and not 0 <= value <= 20:
            raise ValueError("贴图描边宽度必须在 0 到 20 像素之间")
        elif key == "sticker_shadow_strength" and not 0 <= value <= 100:
            raise ValueError("贴图阴影强度必须在 0 到 100 之间")
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
        elif key == "element_depth" and not 1 <= value <= 8:
            raise ValueError("窗口元素识别层级必须在 1 到 8 之间")
        elif key == "window_hover_interval" and not 16 <= value <= 500:
            raise ValueError("悬停识别刷新间隔必须在 16 到 500 毫秒之间")
        elif key == "marker_opacity" and not 1 <= value <= 100:
            raise ValueError("荧光笔不透明度必须在 1 到 100 之间")
        elif key == "font_size" and not 6 <= value <= 200:
            raise ValueError("字号必须在 6 到 200 之间")
        elif key in ("pen_width", "rect_width", "ellipse_width", "arrow_width",
                     "marker_width", "eraser_width") and not 1 <= value <= 50:
            raise ValueError("标注线宽必须在 1 到 50 之间")
        elif key == "annotation_tool" and value not in (
            "select", "rect", "ellipse", "arrow", "pen", "eraser", "text",
            "mosaic", "picker", "crop", "marker"
        ):
            if value in ("wide", "square"):
                result[key] = "select"
            else:
                raise ValueError("未知标注工具")
        elif key == "text_alignment" and value not in ("left", "center", "right"):
            raise ValueError("未知文字对齐方式")
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
        else:
            result[key] = value
    bindings = [canonical_hotkey(binding) for binding in result["hotkeys"].values() if binding]
    if len(bindings) != len(set(bindings)):
        raise ValueError("快捷键冲突：不能为多个操作设置同一热键")
    return result


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
            loaded = json.loads(text)
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
        imported = validate(json.loads(Path(path).read_text(encoding="utf-8")))
        self.data.clear()
        self.data.update(imported)
        self.save()