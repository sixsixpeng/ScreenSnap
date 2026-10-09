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
    "recycle_bin": "贴图回收站",
}


# 编辑器里由画布 keyPressEvent 直接处理的固定快捷键（不是用户可配置的全局热键）。
# 用 Qt 标准键推导文案，保证与 event.matches(QKeySequence.Undo/Redo) 永远一致。
# 只存枚举，QKeySequence 在 shortcut_label() 里按需构造：模块导入期还没有
# QGuiApplication，此时构造 Qt 对象会直接原生崩溃（0xC0000005，无任何输出）。
EDITOR_FIXED_KEYS = {
    "undo": QKeySequence.Undo,
    "redo": QKeySequence.Redo,
    "delete": QKeySequence.Delete,
}


def shortcut_label(bindings, action):
    """优先取用户配置的绑定，其次回落到编辑器内建固定键；都没有则返回空串。"""
    binding = (bindings or {}).get(action, "")
    if not binding:
        binding = EDITOR_FIXED_KEYS.get(action, "")
    return QKeySequence(binding).toString(QKeySequence.NativeText) if binding else ""


def shortcut_suffix(bindings, action):
    shortcut = shortcut_label(bindings, action)
    return f" ({shortcut})" if shortcut else ""

# 应用身份标识：toast 通知与 Windows 应用条目都用它，避免两处硬编码不一致。
APP_USER_MODEL_ID = "ScreenSnap.Desktop"
