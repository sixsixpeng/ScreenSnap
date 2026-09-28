"""托盘菜单。"""

from PySide6.QtWidgets import QMenu, QStyle
from core.constants import shortcut_suffix


def make_tray_menu(app, capture, settings, quit_app, edit_clipboard=None, open_image=None,
                   hotkeys=None, open_sticker=None, paste_clipboard=None, sticker_panel=None):
    """只组装菜单动作，截图与退出逻辑仍由主程序持有。"""
    menu = QMenu()
    menu.addAction(app.style().standardIcon(QStyle.SP_DesktopIcon),
                   f"快速截图{shortcut_suffix(hotkeys, 'capture')}", capture)
    menu.addSeparator()
    for label, icon, action, suffix in [("贴剪贴板内容", QStyle.SP_FileDialogContentsView,
                                         paste_clipboard, "paste_clipboard"),
                                        ("贴图管理", QStyle.SP_FileDialogListView, sticker_panel,
                                         "sticker_panel")]:
        if action is None:
            continue
        menu.addAction(app.style().standardIcon(icon),
                       f"{label}{shortcut_suffix(hotkeys, suffix)}", action)
    clipboard_action = menu.addAction(
        app.style().standardIcon(QStyle.SP_FileDialogContentsView),
        f"编辑剪贴板图片{shortcut_suffix(hotkeys, 'edit_clipboard')}"
    )
    if edit_clipboard is not None:
        clipboard_action.triggered.connect(edit_clipboard)
    open_action = menu.addAction(
        app.style().standardIcon(QStyle.SP_DialogOpenButton),
        f"打开并编辑图片{shortcut_suffix(hotkeys, 'open_image')}"
    )
    if open_image is not None:
        open_action.triggered.connect(open_image)
    if open_sticker is not None:
        menu.addAction(app.style().standardIcon(QStyle.SP_DialogOpenButton),
                       "从文件打开新贴图", open_sticker)
    menu.addSeparator()
    menu.addAction(app.style().standardIcon(QStyle.SP_FileDialogDetailedView), "设置", settings)
    menu.addSeparator()
    menu.addAction(app.style().standardIcon(QStyle.SP_DialogCloseButton), "退出", quit_app)
    return menu