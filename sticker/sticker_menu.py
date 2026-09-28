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
        ("复制图像", QStyle.SP_FileDialogContentsView, sticker.copy_image),
    ]:
        menu.addAction(sticker.style().standardIcon(icon), label, callback)
    if sticker.origin_text():
        label = "复制颜色值" if (sticker.origin or {}).get("kind") == "color" else "复制文字"
        menu.addAction(sticker.style().standardIcon(QStyle.SP_FileDialogContentsView), label,
                       sticker.copy_origin_text)
    if sticker.origin_paths():
        menu.addAction(sticker.style().standardIcon(QStyle.SP_DialogOpenButton), "打开原文件",
                       sticker.open_origin)
    for label, icon, callback in [
        ("关闭描边" if sticker.border_enabled else "开启描边", QStyle.SP_FileDialogDetailedView, sticker.toggle_border),
        ("隐藏阴影" if sticker.shadow_enabled else "显示阴影", QStyle.SP_FileDialogInfoView, sticker.toggle_shadow),
        ("关闭置顶" if sticker.always_on_top else "开启置顶", QStyle.SP_ArrowUp, sticker.toggle_top),
    ]:
        menu.addAction(sticker.style().standardIcon(icon), label, callback)
    snap = getattr(sticker, "snap_target", None)
    if isinstance(snap, dict) and snap.get("target") == "window":
        following = getattr(sticker, "following", lambda: False)()
        follow = menu.addAction(sticker.style().standardIcon(QStyle.SP_ArrowRight),
                                "取消跟随此窗口" if following else "跟随此窗口", sticker.toggle_follow)
        follow.setToolTip("跟随开启后，贴图会随吸附的窗口一起移动；关闭后贴图停在当前位置\n"
                          f"当前吸附：{snap.get('title') or snap.get('hwnd')}（{snap.get('edge')} 边）")
    if isinstance(snap, dict):
        detach = menu.addAction(sticker.style().standardIcon(QStyle.SP_DialogCancelButton),
                                "解除吸附", sticker.release_snap)
        detach.setToolTip("清除当前吸附关系，拖动时不再自动对齐该窗口或屏幕边缘")
    if getattr(sticker, "open_file_replace", None) is not None:
        menu.addAction(sticker.style().standardIcon(QStyle.SP_DialogOpenButton),
                       "从文件打开替换此贴图", sticker.open_file_replace)
        menu.addAction(sticker.style().standardIcon(QStyle.SP_DialogOpenButton),
                       "从文件打开新贴图", sticker.open_file_new)
    menu.addAction(sticker.style().standardIcon(QStyle.SP_DialogCloseButton),
                   "关闭当前贴图", sticker.close)
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