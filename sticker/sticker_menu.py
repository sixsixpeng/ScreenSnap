"""贴图右键菜单。"""

from PySide6.QtCore import Qt
from PySide6.QtGui import QActionGroup, QIcon
from PySide6.QtWidgets import (QMenu, QWidgetAction, QSlider, QStyle, QWidget,
                               QHBoxLayout, QLabel)
from core.constants import shortcut_suffix
from ui.action_icons import action_icon
from editor.toolbar_widget import rich_tooltip


def show_menu(sticker, position):
    """显示当前贴图的右键菜单。"""
    build_menu(sticker).exec(position)


def build_menu(sticker):
    """生成当前贴图的菜单，透明度滑块直接作用于该窗口。"""
    menu = QMenu(sticker)
    for label, icon, callback in [
        ("解锁" if sticker.locked else "锁定", action_icon("unlock" if sticker.locked else "lock"), sticker.toggle_lock),
        ("重置大小", action_icon("resize"), sticker.reset_size),
        ("复制图像", action_icon("clipboard_image"), sticker.copy_image),
        ("编辑", action_icon("edit"),
         lambda: sticker.edit_requested.emit(sticker.pixmap.toImage().copy())),
    ]:
        menu.addAction(icon if isinstance(icon, QIcon) else sticker.style().standardIcon(icon), label, callback)
    rotate = menu.addMenu(action_icon("rotate"), "旋转")
    rotate.setToolTip(rich_tooltip("旋转", "旋转当前贴图或恢复原方向。"))
    rotate.menuAction().setToolTip(rotate.toolTip())
    left = rotate.addAction(action_icon("rotate_left"),
                            "逆时针 90°" + shortcut_suffix(sticker.settings.get("hotkeys", {}), "sticker_rotate_left"),
                            lambda: sticker.rotate(-90))
    left.setToolTip(rich_tooltip("逆时针 90°", "将当前贴图向左旋转四分之一圈。"))
    right = rotate.addAction(action_icon("rotate_right"),
                             "顺时针 90°" + shortcut_suffix(sticker.settings.get("hotkeys", {}), "sticker_rotate_right"),
                             lambda: sticker.rotate(90))
    right.setToolTip(rich_tooltip("顺时针 90°", "将当前贴图向右旋转四分之一圈。"))
    reset_rotation = rotate.addAction(sticker.style().standardIcon(QStyle.SP_DialogResetButton),
                                      "恢复原方向", sticker.reset_rotation)
    reset_rotation.setToolTip(rich_tooltip("恢复原方向", "清除旋转角度，恢复贴图原始方向。"))
    background = menu.addMenu(action_icon("background"), "透明背景模式")
    background.setToolTip(rich_tooltip("透明背景模式", "更改当前贴图透明区域的预览底色。"))
    background.menuAction().setToolTip(background.toolTip())
    modes = QActionGroup(background)
    modes.setExclusive(True)
    for label, mode in (("透明", "transparent"), ("伪透明", "pseudo"),
                        ("暗色棋盘", "dark_checker"), ("亮色棋盘", "light_checker")):
        action = background.addAction(action_icon("background"), label)
        action.setCheckable(True)
        action.setChecked(sticker.background_mode == mode)
        modes.addAction(action)
        action.triggered.connect(lambda checked=False, value=mode: sticker.set_background_mode(value))
        action.setToolTip(rich_tooltip(label, "仅更改透明图像的显示背景，不修改贴图像素。"))
    manager = getattr(sticker, "manager", None)
    if manager is not None:
        groups = menu.addMenu(action_icon("group"), "贴图分组")
        groups.setToolTip(rich_tooltip("贴图分组", "将当前贴图归入一个管理分组。"))
        groups.menuAction().setToolTip(groups.toolTip())
        groups.addAction(action_icon("group_move"), "未分组",
                 lambda: manager.assign_group([sticker], ""))
        groups.addSeparator()
        for group_name in manager.group_names():
            groups.addAction(action_icon("group_move"), group_name,
                             lambda checked=False, value=group_name:
                             manager.assign_group([sticker], value))
    if sticker.origin_text():
        kind = (sticker.origin or {}).get("kind")
        label = "复制颜色值" if kind == "color" else "复制富文本" if kind == "html" else "复制文字"
        menu.addAction(sticker.style().standardIcon(QStyle.SP_FileDialogContentsView), label,
                       sticker.copy_origin_text)
    if sticker.origin_paths():
        menu.addAction(sticker.style().standardIcon(QStyle.SP_DialogOpenButton), "打开原文件",
                       sticker.open_origin)
    for label, icon, callback in [
        ("关闭描边" if sticker.border_enabled else "开启描边", action_icon("border"), sticker.toggle_border),
        ("隐藏阴影" if sticker.shadow_enabled else "显示阴影", action_icon("shadow"), sticker.toggle_shadow),
        ("关闭置顶" if sticker.always_on_top else "开启置顶", action_icon("pin"), sticker.toggle_top),
    ]:
        menu.addAction(icon if isinstance(icon, QIcon) else sticker.style().standardIcon(icon), label, callback)
    snap = getattr(sticker, "snap_target", None)
    if isinstance(snap, dict) and snap.get("target") in ("window", "sticker"):
        following = getattr(sticker, "following", lambda: False)()
        target_label = "此窗口" if snap.get("target") == "window" else "此贴图"
        follow = menu.addAction(action_icon("locate"),
                                f"取消跟随{target_label}" if following else f"跟随{target_label}",
                                sticker.toggle_follow)
        follow.setToolTip("跟随开启后，贴图会随吸附目标一起移动；关闭后贴图停在当前位置\n"
                          f"当前吸附：{snap.get('title') or snap.get('hwnd')}（{snap.get('edge')} 边）")
    if isinstance(snap, dict):
        detach = menu.addAction(sticker.style().standardIcon(QStyle.SP_DialogCancelButton),
                                "解除吸附", sticker.release_snap)
        detach.setToolTip("清除当前吸附关系，拖动时不再自动对齐该窗口或屏幕边缘")
    if getattr(sticker, "open_file_replace", None) is not None:
        menu.addAction(action_icon("sticker"),
                       "从文件打开替换此贴图", sticker.open_file_replace)
        menu.addAction(sticker.style().standardIcon(QStyle.SP_DialogOpenButton),
                       "从文件打开新贴图", sticker.open_file_new)
    opacity = menu.addMenu(action_icon("opacity"), "透明度")
    opacity.setToolTip(rich_tooltip("透明度", "调整当前贴图整体的不透明程度。"))
    opacity.menuAction().setToolTip(opacity.toolTip())
    action = QWidgetAction(opacity)
    opacity_control = QWidget()
    opacity_layout = QHBoxLayout(opacity_control)
    opacity_layout.setContentsMargins(12, 8, 12, 8)
    opacity_layout.setSpacing(10)
    slider = QSlider(Qt.Horizontal)
    slider.setRange(10, 100)
    slider.setValue(round(sticker.windowOpacity() * 100))
    slider.setMinimumWidth(220)
    slider.setMinimumHeight(26)
    slider.setToolTip(rich_tooltip("透明度", "拖动滑块调整当前贴图的不透明程度。"))
    value_label = QLabel(f"{slider.value()}%")
    value_label.setMinimumWidth(42)
    value_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
    slider.valueChanged.connect(lambda value: (sticker.set_opacity(value / 100),
                                                value_label.setText(f"{value}%")))
    opacity_layout.addWidget(slider)
    opacity_layout.addWidget(value_label)
    action.setDefaultWidget(opacity_control)
    opacity.addAction(action)
    menu.addSeparator()
    restore_hint = shortcut_suffix(sticker.settings.get("hotkeys", {}), "touch")
    click_through_label = "取消点击穿透" if sticker.click_through else "启用点击穿透"
    if not sticker.click_through and restore_hint:
        click_through_label += f"（恢复交互{restore_hint}）"
    menu.addAction(action_icon("click_through"), click_through_label,
                   sticker.toggle_click_through)
    menu.addSeparator()
    menu.addAction(sticker.style().standardIcon(QStyle.SP_DialogCloseButton),
                   "关闭当前贴图", sticker.close)
    return menu