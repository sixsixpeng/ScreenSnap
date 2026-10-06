"""托盘菜单。"""

from PySide6.QtWidgets import QMenu
from core.constants import shortcut_suffix, shortcut_label
from ui.action_icons import action_icon


def make_tray_menu(app, capture, settings, quit_app, edit_clipboard=None, open_image=None,
                   hotkeys=None, open_sticker=None, paste_clipboard=None, sticker_panel=None,
                   recycle_bin=None):
    """只组装菜单动作，截图与退出逻辑仍由主程序持有。"""
    menu = QMenu()

    def add_action(icon, text, help_text, callback=None):
        action = menu.addAction(icon, text)
        action.setToolTip(help_text)
        if callback is not None:
            action.triggered.connect(callback)
        return action

    add_action(action_icon("camera"),
               f"快速截图{shortcut_suffix(hotkeys, 'capture')}",
               "立即启动全屏截图与区域选择。", capture)
    menu.addSeparator()
    for label, icon, action, suffix in [("贴剪贴板内容", action_icon("clipboard_image"),
                                         paste_clipboard, "paste_clipboard"),
                                        ("贴图管理", action_icon("sticker"), sticker_panel,
                                         "sticker_panel"),
                                        ("贴图回收站", action_icon("recycle"), recycle_bin,
                                         "recycle_bin")]:
        if action is None:
            continue
        if suffix == "paste_clipboard":
            help_text = "将剪贴板中的图片或文件内容作为新贴图显示。"
        elif suffix == "recycle_bin":
            binding = shortcut_label(hotkeys, "recycle_bin")
            help_text = "打开贴图回收站窗口，恢复或彻底删除已关闭的贴图。" + (
                f"（快捷键: {binding}）" if binding else "")
        else:
            help_text = "打开贴图管理窗口，查找和管理当前贴图。"
        add_action(icon, f"{label}{shortcut_suffix(hotkeys, suffix)}", help_text, action)
    clipboard_action = add_action(
        action_icon("clipboard_edit"),
        f"编辑剪贴板图片{shortcut_suffix(hotkeys, 'edit_clipboard')}",
        "将剪贴板中的图片载入编辑器；剪贴板不是图片时不会打开。"
    )
    if edit_clipboard is not None:
        clipboard_action.triggered.connect(edit_clipboard)
    open_action = add_action(
        action_icon("edit"),
        f"打开并编辑图片{shortcut_suffix(hotkeys, 'open_image')}",
        "从文件选择一张图片，在编辑器中标注、变换并保存。"
    )
    if open_image is not None:
        open_action.triggered.connect(open_image)
    if open_sticker is not None:
        add_action(action_icon("sticker"),
                   f"从文件打开新贴图{shortcut_suffix(hotkeys, 'open_sticker_file')}",
                   "选择图片文件并直接创建一个贴图窗口。", open_sticker)
    menu.addSeparator()
    add_action(action_icon("settings"), "设置", "打开设置窗口，调整截图、保存、快捷键和外观。", settings)
    menu.addSeparator()
    add_action(action_icon("exit"), "退出",
               "关闭 ScreenSnap 并结束后台运行。", quit_app)
    return menu