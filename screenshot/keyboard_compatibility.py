"""截图临时键盘兜底，不触碰常驻管理器或库的全局注销函数。"""

import os
from PySide6.QtCore import QObject, Signal, Slot, QEvent, QTimer, Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import QApplication, QLineEdit, QTextEdit, QPlainTextEdit, QGraphicsView, QGraphicsTextItem
from shiboken6 import isValid
from core.hotkey_policy import shortcut_signature
from core.window_focus import offscreen
from logger import get_logger

# 三类截图期兜底；每项为五元组：
#   (配置键, 禁用用环境变量, 该兜底对应的快捷键配置键或 None, 默认键, 是否 suppress 原键)
# suppress=True 表示按键不再传给前台应用（工具栏隐藏键、Esc）；快速贴图用 False ——
# Space 在编辑器里仍兼作拖动画布，兜底只能「额外」触发一次，不能吞掉原路径。
FALLBACKS = {
    "toolbar": ("capture_toolbar_hotkey_fallback", "SCREENSNAP_NO_TOOLBAR_HOTKEY", "capture_toolbar_hide_shortcut", "`", True),
    "quick_sticker": ("capture_quick_sticker_hotkey_fallback", "SCREENSNAP_NO_QUICK_STICKER_HOTKEY", "capture_quick_sticker_shortcut", "Space", False),
    "escape": ("capture_escape_hotkey_fallback", "SCREENSNAP_NO_ESC_HOOK", None, "esc", True),
}


def fallback_state(kind, settings, override=False):
    """判定某个兜底当前是否可安装；返回的字符串本身就是要展示/上报的状态。

    判定顺序即优先级：环境变量禁用 → 本次截图临时对照 → 用户偏好关闭 →
    动作自身关闭（仅快速贴图）→ 与常驻全局绑定冲突（避免同键双注册）→
    平台不支持或离屏 → enabled。
    """
    key, environment, binding_key, default, _ = FALLBACKS[kind]
    if os.environ.get(environment) == "1":
        return "environment-disabled"
    if override:
        return "session-comparison-disabled"
    if not settings.get(key, True):
        return "preference-disabled"
    if kind == "quick_sticker" and not settings.get("capture_quick_sticker_enabled", True):
        return "action-disabled"
    signature = shortcut_signature(settings.get(binding_key, default) or default) if binding_key else shortcut_signature(default)
    if (signature and settings.get("hotkeys_enabled", True)
            and any(shortcut_signature(binding) == signature
                    for binding in settings.get("hotkeys", {}).values() if binding)):
        return "conflict-with-global-binding"
    if offscreen() or os.name != "nt":
        return "unsupported-or-offscreen"
    return "enabled"


def compatibility_status(settings, mask=None, next_override=False):
    try:
        active = mask is not None and isValid(mask) and mask.isVisible()
    except (RuntimeError, TypeError):
        active = False
    session = mask.session if active else None
    override = getattr(session, "compatibility_override", next_override)
    states = {kind: fallback_state(kind, settings, override) for kind in FALLBACKS}
    states["capture_active"] = active
    if active:
        bridge = getattr(session, "keyboard_bridge", None)
        for kind in ("toolbar", "quick_sticker"):
            if bridge is not None and kind in bridge.failures:
                states[kind] = "installation-failed"
        states["temporary_handles"] = len(bridge.handles) if bridge is not None else 0
        states["escape_installed"] = any(getattr(view, "escape_fallback", None) is not None for view in session.views)
    return states


class CaptureKeyboardBridge(QObject):
    requested = Signal(str, int)

    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
        self.handles = {}
        self.bindings = {}
        self.generations = {"toolbar": 0, "quick_sticker": 0}
        self.failures = set()
        self._sync_pending = False
        self.requested.connect(self.deliver)
        QApplication.instance().installEventFilter(self)

    def input_owned_by_editor(self):
        if QApplication.activeModalWidget() is not None or QApplication.activePopupWidget() is not None:
            return True
        focus = QApplication.focusWidget()
        if focus is None or not isValid(focus):
            return False
        if isinstance(focus, (QLineEdit, QTextEdit, QPlainTextEdit)):
            return True
        if isinstance(focus, QGraphicsView):
            scene = focus.scene()
            item = scene.focusItem() if scene is not None else None
            return isinstance(item, QGraphicsTextItem) and bool(item.textInteractionFlags() & Qt.TextEditable)
        return False

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.Show, QEvent.Hide, QEvent.FocusIn, QEvent.FocusOut, QEvent.WindowActivate, QEvent.WindowDeactivate):
            if not self._sync_pending and isValid(self.owner) and not self.owner.session.closing:
                self._sync_pending = True
                QTimer.singleShot(0, self._sync_after_focus)
        return False

    def _sync_after_focus(self):
        self._sync_pending = False
        if isValid(self.owner) and not self.owner.session.closing:
            self.owner.sync_keyboard_compatibility()

    def release(self, kind):
        self.generations[kind] += 1
        handle = self.handles.pop(kind, None)
        self.bindings.pop(kind, None)
        if handle is None:
            return
        try:
            import keyboard
            keyboard.remove_hotkey(handle)
        except Exception as error:  # noqa: BLE001 不让一个坏句柄阻断其他清理
            get_logger().warning("截图临时热键释放失败: kind=%s type=%s", kind, type(error).__name__,
                                                    extra={"screensnap_metadata_only": True})

    def sync(self, kind):
        owner = self.owner
        session = owner.session
        visible = not session.closing and any(isValid(view) and view.isVisible() for view in session.views)
        state = fallback_state(kind, owner.settings, getattr(session, "compatibility_override", False))
        if not visible or getattr(session, "keyboard_recording", False) or state != "enabled" or self.input_owned_by_editor():
            self.release(kind)
            return
        _, _, key, default, suppress = FALLBACKS[kind]
        binding = QKeySequence(owner.settings.get(key, default)).toString() or default
        if self.bindings.get(kind) == binding and kind in self.handles:
            return
        self.release(kind)
        generation = self.generations[kind]
        try:
            import keyboard
            self.handles[kind] = keyboard.add_hotkey(binding,
                lambda current=kind, gen=generation: self.fire(current, gen), suppress=suppress)
            self.bindings[kind] = binding
            self.failures.discard(kind)
            # 这是内部机制就绪（非用户可见里程碑），且可能随截图焦点变化多次发生 ⇒ DEBUG（规则 13/14）。
            get_logger().debug("截图临时热键就绪: kind=%s suppress=%s", kind, suppress,
                                                  extra={"screensnap_metadata_only": True})
        except Exception as error:  # noqa: BLE001 失败仍保留Qt原路径
            self.failures.add(kind)
            get_logger().warning("截图临时热键安装失败: kind=%s type=%s", kind, type(error).__name__,
                                                    extra={"screensnap_metadata_only": True})

    def fire(self, kind, generation):
        try:
            self.requested.emit(kind, generation)
        except RuntimeError:
            pass  # 已析构：绝不在钩子线程访问Qt对象、卸载钩子或写日志。

    @Slot(str, int)
    def deliver(self, kind, generation):
        owner = self.owner
        if not isValid(owner) or owner.session.closing or generation != self.generations.get(kind) or kind not in self.handles:
            return
        if not any(isValid(view) and view.isVisible() for view in owner.session.views):
            return
        if self.input_owned_by_editor() or getattr(owner.session, "keyboard_recording", False):
            return
        if kind == "toolbar":
            owner.toggle_inline_toolbar()
        else:
            owner.trigger_quick_sticker()


def bridge_for(view):
    bridge = getattr(view.session, "keyboard_bridge", None)
    if bridge is None:
        owner = next((item for item in view.session.views if item.primary), view)
        bridge = CaptureKeyboardBridge(owner)
        view.session.keyboard_bridge = bridge
    return bridge
