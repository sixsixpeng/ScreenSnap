"""贴图右键菜单。"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMenu, QWidgetAction, QSlider, QStyle
from core.constants import shortcut_suffix


def show_menu(sticker, position):
    """显示当前贴图的右键菜单。"""
    build_menu(sticker).exec(position)


def build_menu(sticker):
    """生成当前贴图的菜单，透明度滑块直接作用于该窗口。"""
    menu = QMenu(sticker)
    for label, icon, callback in [
        ("解锁" if sticker.locked else "锁定", QStyle.SP_DialogApplyButton, sticker.toggle_lock),
        ("重置大小", QStyle.SP_DialogResetButton, sticker.reset_size),
        ("复制图像", QStyle.SP_FileIcon, sticker.copy_image),
        ("关闭置顶" if sticker.always_on_top else "开启置顶", QStyle.SP_ArrowUp, sticker.toggle_top),
        ("关闭当前贴图", QStyle.SP_DialogCloseButton, sticker.close),
    ]:
        menu.addAction(sticker.style().standardIcon(icon), label, callback)
    opacity = menu.addMenu(sticker.style().standardIcon(QStyle.SP_FileDialogDetailedView), "透明度")
    action = QWidgetAction(opacity)
    slider = QSlider(Qt.Horizontal)
    slider.setRange(10, 100)
    slider.setValue(round(sticker.windowOpacity() * 100))
    slider.setToolTip("拖动滑块调整当前贴图透明度")
    slider.valueChanged.connect(lambda value: sticker.setWindowOpacity(value / 100))
    action.setDefaultWidget(slider)
    opacity.addAction(action)
    menu.addSeparator()
    restore_hint = shortcut_suffix(sticker.settings.get("hotkeys", {}), "touch")
    click_through_label = "取消点击穿透" if sticker.click_through else "启用点击穿透"
    if not sticker.click_through and restore_hint:
        click_through_label += f"（恢复交互{restore_hint}）"
    menu.addAction(sticker.style().standardIcon(QStyle.SP_DesktopIcon), click_through_label,
                   sticker.toggle_click_through)
    return menu