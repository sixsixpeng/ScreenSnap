"""UI 使用的操作名称。"""

from PySide6.QtGui import QKeySequence

# Shared checker size for transparent-image previews and sticker backgrounds.
CHECKER_TILE_SIZE = 8


def checker_tile_size(scale=1.0):
    return max(4, round(CHECKER_TILE_SIZE * scale))


# 与默认热键配置中的动作键保持一致，设置页直接使用这些显示名称。
HOTKEY_LABELS = {
    "capture": "自由选区", "repeat": "上次位置截图", "fullscreen": "全屏截图", "monitor": "当前显示器",
    "edit_clipboard": "编辑剪贴板图片", "open_image": "打开并编辑图片",
    "paste": "贴上次截图", "previous": "上一张贴图", "next": "下一张贴图",
    "hide": "隐藏/显示贴图", "close_all": "关闭全部贴图",
    "touch": "恢复贴图交互", "paste_clipboard": "贴剪贴板内容",
    "open_sticker_file": "从文件打开新贴图",
    "sticker_panel": "贴图管理窗口",
    "sticker_rotate_left": "贴图逆时针旋转",
    "sticker_rotate_right": "贴图顺时针旋转",
}


def shortcut_label(bindings, action):
    binding = (bindings or {}).get(action, "")
    return QKeySequence(binding).toString(QKeySequence.NativeText) if binding else ""


def shortcut_suffix(bindings, action):
    shortcut = shortcut_label(bindings, action)
    return f" ({shortcut})" if shortcut else ""