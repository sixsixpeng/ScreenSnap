"""鼠标下的外部窗口查询，供贴图吸附与跟随使用。

只在 Windows 生效；命中测试使用物理像素，返回的矩形换算为 Qt 逻辑坐标，
这样贴图可以直接和自己的 QWidget 坐标比较。
"""

import ctypes
import logging
import os
from ctypes import wintypes
from dataclasses import dataclass

from PySide6.QtCore import QRect

from core.screen_mapping import logical_point_to_physical, native_rect, physical_rect_to_logical
from logger.log_rate import log_every

# 沿 Z 序取下一个窗口，用于跳过本进程的贴图。
GW_HWNDNEXT = 2
# GetAncestor 的取值：顶层根窗口。
GA_ROOT = 2
# 过小的窗口没有吸附价值。
MIN_SIZE = 24
# 系统隐形覆盖窗口与任务栏：整屏或条状，会抢走鼠标下的真正目标，
# 贴图吸附到它们上面没有意义（都是别人程序的悬浮条/任务栏）。
IGNORED_CLASSES = ("TabletModeCoverWindow", "Shell_TrayWnd", "Shell_SecondaryTrayWnd")
# 吸附目标的最小边长：过小的悬浮小窗（状态栏、挂件）会干扰水平/垂直吸附。
MIN_EDGE = 160
# DwmGetWindowAttribute 的属性编号：窗口是否被系统隐藏。
DWMWA_CLOAKED = 14
# GetWindowLong 的索引与扩展样式：悬浮工具窗口。
GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080
def log_once(logger, channel, key, message, *args):
    """按通道去重：状态不变只输出一次，状态变化（窗口移动/尺寸变化）后重新输出。"""
    log_every(logger, logging.DEBUG, channel, key, 0.0, message, *args)


@dataclass(frozen=True)
class WindowTarget:
    """一个可吸附的外部窗口。"""

    handle: int
    title: str
    class_name: str
    rect: QRect


def window_under_point(point):
    """返回鼠标（逻辑坐标）下最上层的外部窗口；没有可用窗口时返回 None。"""
    if os.name != "nt":
        return None
    logger = logging.getLogger("screensnap")
    physical = logical_point_to_physical(point)
    # 贴图与遮罩属于本进程且置顶，直接取它们下面第一个覆盖鼠标的外部窗口。
    handle = top_window_at(physical.x(), physical.y())
    if not handle:
        log_once(logger, "none", (point.x(), point.y()),
                 "鼠标下没有可吸附的窗口: 逻辑(%d,%d) 物理(%d,%d)",
                 point.x(), point.y(), physical.x(), physical.y())
        return None
    user32 = ctypes.windll.user32
    root = user32.GetAncestor(handle, GA_ROOT) or handle
    rect = window_logical_rect(root)
    if rect is None:
        log_once(logger, "reject", int(root),
                 "鼠标下的窗口不可吸附（过小或已隐藏）: hwnd=%d 物理点(%d,%d)",
                 int(root), physical.x(), physical.y())
        return None
    target = WindowTarget(int(root), window_text(user32, root), window_class(user32, root), rect)
    log_once(logger, "hit", (target.handle, rect.left(), rect.top(), rect.width(), rect.height()),
             "鼠标下窗口: hwnd=%d 标题=%r 类名=%r 逻辑矩形=(%d,%d,%dx%d) 物理点(%d,%d)",
             target.handle, target.title, target.class_name,
             rect.left(), rect.top(), rect.width(), rect.height(),
             physical.x(), physical.y())
    return target


def top_window_at(x, y):
    """按 Z 序找到第一个覆盖该物理点、可见且不属于本进程的顶层窗口。

    遮罩与贴图都是本进程置顶窗口，命中测试会先碰到它们；这里一次枚举并命中即停，
    比沿 Z 序逐个下移（可能上百个窗口）更快，也不会漏掉真正位于鼠标下的窗口。
    """
    if os.name != "nt":
        return None
    logger = logging.getLogger("screensnap")
    user32 = ctypes.windll.user32
    desktop = user32.GetDesktopWindow()
    shell = user32.GetShellWindow()
    pid = os.getpid()
    found = []
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def collect(handle, unused):
        if found:
            return False
        if (handle in (desktop, shell) or is_cloaked(handle)
                or window_class(user32, handle) in IGNORED_CLASSES
                or not user32.IsWindowVisible(handle) or user32.IsIconic(handle)
                or process_of(user32, handle) == pid):
            return True
        if not covers(user32, handle, x, y):
            return True
        found.append(int(handle))
        return False

    try:
        callback = callback_type(collect)
        user32.EnumWindows(callback, 0)
    except (OSError, ValueError) as error:
        logger.warning("枚举窗口失败，本次无法识别: %s", error)
        return None
    return found[0] if found else None


def is_cloaked(handle):
    """被系统隐藏（cloak）的窗口 IsWindowVisible 仍为真，识别与吸附都要跳过。"""
    try:
        value = ctypes.c_int(0)
        result = ctypes.windll.dwmapi.DwmGetWindowAttribute(
            handle, DWMWA_CLOAKED, ctypes.byref(value), ctypes.sizeof(value))
        return result == 0 and bool(value.value)
    except (OSError, ValueError, AttributeError):
        return False


def is_tool_window(user32, handle):
    """WS_EX_TOOLWINDOW 是悬浮工具条/挂件窗口，不适合作为吸附目标。"""
    try:
        style = user32.GetWindowLongW(handle, GWL_EXSTYLE)
    except (OSError, ValueError, AttributeError):
        return False
    return bool(style & WS_EX_TOOLWINDOW)


def visible_targets():
    """枚举可吸附的可见窗口（排除本进程与桌面），供拖动开始时缓存一次。"""
    if os.name != "nt":
        return []
    logger = logging.getLogger("screensnap")
    user32 = ctypes.windll.user32
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    desktop = user32.GetDesktopWindow()
    shell = user32.GetShellWindow()
    pid = os.getpid()
    targets = []

    def collect(handle, unused):
        if handle in (desktop, shell) or is_cloaked(handle) or not user32.IsWindowVisible(handle) \
                or user32.IsIconic(handle):
            return True
        if window_class(user32, handle) in IGNORED_CLASSES or process_of(user32, handle) == pid:
            return True
        bounds = wintypes.RECT()
        if not user32.GetWindowRect(handle, ctypes.byref(bounds)):
            return True
        physical = native_rect(bounds)
        # 悬浮小窗（状态栏、桌面挂件）会抢走水平/垂直吸附，这里要求一定大小。
        if physical.width() < MIN_EDGE or physical.height() < MIN_EDGE or is_tool_window(user32, handle):
            logger.debug("跳过不适合吸附的窗口: hwnd=%d 类名=%r 尺寸=%dx%d",
                         int(handle), window_class(user32, handle),
                         physical.width(), physical.height())
            return True
        targets.append(WindowTarget(int(handle), window_text(user32, handle),
                                    window_class(user32, handle),
                                    physical_rect_to_logical(physical)))
        return True

    try:
        callback = callback_type(collect)
        user32.EnumWindows(callback, 0)
    except (OSError, ValueError) as error:
        logger.warning("枚举可见窗口失败，本次只吸附屏幕边缘: %s", error)
        return []
    logger.debug("可吸附窗口 %d 个: %s", len(targets),
                 [(target.handle, target.title or target.class_name,
                   (target.rect.left(), target.rect.top(),
                    target.rect.width(), target.rect.height())) for target in targets])
    return targets


def window_logical_rect(handle):
    """读取窗口的逻辑矩形；窗口失效、隐藏、最小化或过小时返回 None。"""
    if os.name != "nt" or not handle:
        return None
    user32 = ctypes.windll.user32
    if not user32.IsWindow(handle):
        return None
    if not user32.IsWindowVisible(handle) or user32.IsIconic(handle):
        return None
    bounds = wintypes.RECT()
    if not user32.GetWindowRect(handle, ctypes.byref(bounds)):
        logging.getLogger("screensnap").debug("读取窗口矩形失败: hwnd=%d", int(handle))
        return None
    if bounds.right - bounds.left < MIN_SIZE or bounds.bottom - bounds.top < MIN_SIZE:
        return None
    physical = native_rect(bounds)
    result = physical_rect_to_logical(physical)
    # 跟随轮询与拖动都会高频调用，同一矩形最多每秒记一次。
    log_every(logging.getLogger("screensnap"), logging.DEBUG, "rect",
              (int(handle), result.left(), result.top(), result.width(), result.height()), 1.0,
              "窗口矩形: hwnd=%d 物理(%d,%d,%dx%d) → 逻辑(%d,%d,%dx%d)",
              int(handle), physical.left(), physical.top(), physical.width(), physical.height(),
              result.left(), result.top(), result.width(), result.height())
    return result


def window_present(handle):
    """句柄是否仍指向存在的窗口；最小化和隐藏都算存在。"""
    if os.name != "nt" or not handle:
        return False
    return bool(ctypes.windll.user32.IsWindow(handle))


def window_alive(handle):
    """跟随前与恢复会话时确认目标窗口仍然存在且可见。"""
    if not window_present(handle):
        return False
    user32 = ctypes.windll.user32
    return bool(user32.IsWindowVisible(handle)) and not user32.IsIconic(handle)


def covers(user32, handle, x, y):
    """窗口矩形是否盖住该物理点；用于确认候选窗口真的在鼠标下方。"""
    bounds = wintypes.RECT()
    if not user32.GetWindowRect(handle, ctypes.byref(bounds)):
        return False
    return bounds.left <= x <= bounds.right and bounds.top <= y <= bounds.bottom


def process_of(user32, handle):
    """返回窗口所属进程；取不到时返回 0，避免误跳过。"""
    identifier = wintypes.DWORD()
    user32.GetWindowThreadProcessId(handle, ctypes.byref(identifier))
    return identifier.value


def window_text(user32, handle):
    """读取窗口标题，仅用于日志与提示，失败不影响吸附。"""
    try:
        length = user32.GetWindowTextLengthW(handle)
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(handle, buffer, length + 1)
        return buffer.value
    except (OSError, ValueError):
        return ""


def window_class(user32, handle):
    """读取窗口类名，自绘界面通常只能靠它区分。"""
    try:
        buffer = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(handle, buffer, 256)
        return buffer.value
    except (OSError, ValueError):
        return ""
