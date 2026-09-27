"""UI 使用的操作名称。"""

from PySide6.QtGui import QKeySequence

# 与默认热键配置中的动作键保持一致，设置页直接使用这些显示名称。
HOTKEY_LABELS = {
    "capture": "自由选区", "repeat": "上次位置截图", "fullscreen": "全屏截图", "monitor": "当前显示器",
    "edit_clipboard": "编辑剪贴板图片", "open_image": "打开并编辑图片",
    "paste": "贴上次截图", "previous": "上一张贴图", "next": "下一张贴图",
    "hide": "隐藏/显示贴图", "close_all": "关闭全部贴图",
    "touch": "恢复贴图交互",
}


def shortcut_label(bindings, action):
    binding = (bindings or {}).get(action, "")
    return QKeySequence(binding).toString(QKeySequence.NativeText) if binding else ""


def shortcut_suffix(bindings, action):
    shortcut = shortcut_label(bindings, action)
    return f" ({shortcut})" if shortcut else ""