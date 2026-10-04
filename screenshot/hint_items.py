"""选区提示条的文案生成。

抽到这里是为了让**遮罩**与**设置页预览**共用同一份逻辑：设置页预览直接读当前配置，
因此改键后预览里显示的也是最新键位，不会出现"预览是写死的旧键位"。

文案只依赖「设置 + 当前阶段状态」，不依赖 Qt 控件，也不做任何绘制。
"""

from config.config_manager import HINT_ITEM_IDS

# 选区阶段的四个功能键：名称 → 配置键 → 默认键 → 提示条里显示的中文名。
# 原先由遮罩上的「自定义尺寸 / 重新截图 / 窗口编辑 / 仅复制」四个按钮触发；
# 按钮已移除，统一改为可配置快捷键，并在提示条里按**当前设置**展示。
CAPTURE_ACTION_KEYS = (
    ("custom_size", "capture_custom_size_shortcut", "F", "尺寸"),
    ("recapture", "capture_recapture_shortcut", "R", "重新截图"),
    ("window_edit", "capture_window_edit_shortcut", "E", "窗口编辑"),
    ("copy", "capture_copy_shortcut", "Y", "仅复制"),
)

# 原地编辑工具栏的隐藏键：配置键 → 默认键。隐藏只由快捷键触发，不加任何按钮，
# 因此提示条是它唯一的发现入口（进入原地编辑时显示，隐藏后显示"显示工具栏"）。
TOOLBAR_HIDE_KEY = ("capture_toolbar_hide_shortcut", "`")


def hint_texts(settings, position, selection, inline=False, picker=False,
               picker_color=None, toolbar_hidden=False):
    """提示项 id → 文案；当前阶段不适用的项给出空串（绘制端会跳过）。

    · `selection` 为 `(宽, 高)` 表示已有选区，`None` 表示还在框选阶段；
    · `inline` 为真表示已进入原地编辑；
    · `picker` 为真表示处于取色态（只保留取色相关说明）。
    """
    save_key = str(settings.get("capture_save_shortcut", "S"))
    picker_key = str(settings.get("capture_picker_shortcut", "C") or "C")
    fixed_key = str(settings.get("capture_custom_size_shortcut", "F"))
    quick = (str(settings.get("capture_quick_sticker_shortcut", "Space"))
             if settings.get("capture_quick_sticker_enabled", False) else "")
    hide_key = str(settings.get(*TOOLBAR_HIDE_KEY))
    action_keys = {name: str(settings.get(key, default))
                   for name, key, default, _label in CAPTURE_ACTION_KEYS}
    if picker:
        # 取色态下只保留与取色相关的说明，避免与取样操作抢注意力。
        color_text = f"取色 {picker_color}  |  " if picker_color else ""
        return {"coords": f"{position[0]}, {position[1]}  |  {color_text}",
                "edit": "Alt/Ctrl+左键 取样复制到剪贴板",
                "cancel": f"{picker_key}/Esc 退出取色"}
    size = f"  {selection[0]} x {selection[1]}" if selection else ""
    return {
        "coords": f"{position[0]}, {position[1]}{size}",
        "drag_move": "拖动移动" if selection and not inline else "",
        "resize": "四角/边中点缩放" if selection and not inline else "",
        "select": "拖拽框选" if selection is None else "",
        "edit": ("Enter/双击编辑" if not inline else "双击空白提交") if selection else "",
        "nudge": "WASD/方向键微调" if selection and not inline else "",
        "cancel": "Esc取消" if not inline else "Esc放弃编辑",
        "save": f"右键双击保存 | {save_key}保存" if not inline else "",
        "quick_sticker": f"{quick} 贴图" if quick else "",
        "picker": f"{picker_key} 取色" if not inline else "",
        "fixed_size": f"{fixed_key} 固定尺寸" if selection is None else "",
        "recapture": (f"{action_keys['recapture']} 重新截图"
                      if selection and not inline else ""),
        "window_edit": (f"{action_keys['window_edit']} 窗口编辑"
                        if selection and not inline else ""),
        "copy": f"{action_keys['copy']} 仅复制" if selection and not inline else "",
        "toolbar_move": "拖动边缘/空白处 移动工具栏" if inline else "",
        "toolbar_hide": (f"{hide_key} {'显示工具栏' if toolbar_hidden else '隐藏工具栏'}"
                         if inline else ""),
    }


def hint_items(settings, position, selection, inline=False, picker=False,
               picker_color=None, toolbar_hidden=False):
    """按配置顺序给出提示条内容；总开关关闭或列表为空时返回空列表（整条不画）。"""
    if not settings.get("capture_hints_enabled", True):
        return []
    order = settings.get("capture_hint_order")
    if order is None:
        order = list(HINT_ITEM_IDS)
    texts = hint_texts(settings, position, selection, inline=inline, picker=picker,
                       picker_color=picker_color, toolbar_hidden=toolbar_hidden)
    return [texts.get(item, "") for item in order]
