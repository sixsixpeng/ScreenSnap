"""JSON 配置的加载、保存和导入导出。"""

import copy
import json
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
        "touch": "ctrl+shift+t",
    },
    "bubble": True, "capture_notification": True, "save_notification": True,
    "sticker_notification": True, "sound": False, "auto_copy": True,
    "auto_save": True, "auto_dir": "", "manual_dir": "",
    "filename": "_%Y%m%d_%H%M%S", "open_dir": False,
    "magnifier": True, "crosshair": True, "crosshair_color": "#ff0000", "crosshair_width": 1,
    "mask_theme": "dark", "mask_opacity": 50,
    "anchor_style": "border", "selection_border_color": "#ff0000",
    "cursor": False, "history_limit": 100,
    "last_capture_rect": [],
    "annotation_tool": "select", "text_alignment": "left", "arrow_style": "filled",
    "pen_width": 3, "rect_width": 3, "ellipse_width": 3, "arrow_width": 3,
    "marker_width": 3, "eraser_width": 3, "pen_color": "#ff5252",
    "editor_border_color": "#000000", "editor_border_width": 1,
    "marker_opacity": 38, "font": "", "font_size": 18,
    "line_spacing": 1.2, "mosaic_size": 12,
    "mosaic_mode": "blocks",
    "logging_enabled": True, "log_level": "INFO", "log_when": "midnight", "log_dir": "",
}


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
        elif key == "crosshair_color" and not re.fullmatch(r"#[0-9a-fA-F]{6}", value):
            raise ValueError("十字线颜色必须是六位十六进制颜色")
        elif key == "crosshair_width" and not 1 <= value <= 8:
            raise ValueError("十字线宽度必须在 1 到 8 像素之间")
        elif key == "mask_opacity" and not 0 <= value <= 100:
            raise ValueError("遮罩透明度必须在 0 到 100 之间")
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
        elif key == "arrow_style" and value not in ("filled", "open", "double", "double_filled"):
            raise ValueError("未知箭头样式")
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


class ConfigManager:
    """在用户配置文件和内存中的完整配置之间同步。"""

    def __init__(self, path):
        self.path = Path(path)
        self.data = self.load()

    def load(self):
        """启动时加载配置，首次使用时落盘完整默认设置。"""
        if not self.path.exists():
            self.data = copy.deepcopy(DEFAULTS)
            self.save()
            return self.data
        loaded = json.loads(self.path.read_text(encoding="utf-8"))
        self.data = validate(loaded)
        if self.data != loaded:
            self.save()
        return self.data

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