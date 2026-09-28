"""鼠标位置下的窗口与控件层级。

仅在 Windows 生效；通过 WindowFromPoint 得到最内层窗口后沿父窗口上溯，
形成“顶层窗口 -> 分组容器 -> 具体控件”的候选链，供 Tab 逐层切换选区。
自绘界面（浏览器、Electron 等）没有子窗口句柄，只能识别到顶层窗口。
"""

import ctypes
import logging
import os
from ctypes import wintypes

from core.window_snap import top_window_at
from logger.log_rate import log_every

# 比 DEBUG 更细的级别，只放最啰嗦的清单。
TRACE = 5
# GetAncestor 的取值：直接父窗口与顶层根窗口。
GA_PARENT = 1
GA_ROOT = 2
# 沿 Z 序取下一个窗口，用于跳过本进程的遮罩。
GW_HWNDNEXT = 2
# 过小的窗口没有识别价值，通常是不可见的工具窗口。
MIN_SIZE = 8


def element_chain(point, max_depth=3, use_uia=False, exclude_hwnd=None):
    """返回覆盖该点的窗口矩形链，由外到内；失败或非 Windows 时返回空列表。

    use_uia 为真时先用 UIA 识别自绘界面内部的控件，拿不到结果再退回窗口句柄。
    exclude_hwnd 是置顶遮罩句柄，查询瞬间让它命中穿透，UIA 才能越过它命中真实窗口。
    """
    if os.name != "nt" or max_depth < 1:
        return []
    if use_uia:
        from core.window_uia import element_chain as uia_chain

        chain = uia_chain(point, int(max_depth), exclude_hwnd)
        if chain:
            return chain
    try:
        return collect(point, int(max_depth), use_uia)
    except Exception as error:  # 系统 API 偶发失败，降级为不识别元素。
        logging.getLogger("screensnap").warning("识别窗口元素失败: %s", error)
        return []


def collect(point, max_depth, use_uia=False):
    """按鼠标位置取最内层窗口，再逐级上溯到顶层窗口。"""
    logger = logging.getLogger("screensnap")
    user32 = ctypes.windll.user32
    probe = wintypes.POINT(int(point[0]), int(point[1]))
    # 遮罩属于本进程且置顶，直接取它下面第一个覆盖鼠标的外部窗口。
    handle = top_window_at(probe.x, probe.y)
    log_every(logger, logging.DEBUG, "element-hit", (probe.x, probe.y, int(handle or 0)), 1.0,
              "元素识别: 物理点(%d,%d) 命中 hwnd=%d", probe.x, probe.y, int(handle or 0))
    if not handle:
        log_every(logger, logging.DEBUG, "element-miss", (probe.x, probe.y), 1.0,
                  "元素识别没有命中覆盖鼠标的外部窗口: 物理点(%d,%d)", probe.x, probe.y)
        return []
    root = user32.GetAncestor(handle, GA_ROOT) or handle
    chain = []
    current = handle
    while current:
        rectangle = rectangle_of(user32, current)
        if rectangle is not None and rectangle not in chain:
            chain.append(rectangle)
            log_every(logger, logging.DEBUG, f"element-layer{len(chain)}",
                      (int(current), rectangle), 1.0,
                      "元素识别第 %d 层: hwnd=%d 标题=%r 类名=%r 矩形=%s",
                      len(chain), int(current), title_of(user32, current),
                      class_of(user32, current), rectangle)
        if current == root:
            break
        parent = user32.GetAncestor(current, GA_PARENT)
        if not parent or parent == current:
            break
        current = parent
    chain.reverse()
    candidates = chain[:max_depth]
    log_every(logger, logging.INFO, "element-chain", tuple(candidates), 1.0,
              "识别到 %d 层窗口元素: %s", len(candidates), candidates)
    if len(candidates) == 1 and not use_uia:
        log_every(logger, logging.DEBUG, "element-hint", None, 5.0,
                  "只有 1 层元素且未启用 UIA：这可能是浏览器、Qt、Java 等自绘界面，"
                  "开启设置中的“优先用无障碍识别 (UIA)”才能读到它内部的控件")
    return candidates


def process_of(user32, handle):
    """返回窗口所属进程；取不到时返回 0，避免误跳过。"""
    identifier = wintypes.DWORD()
    user32.GetWindowThreadProcessId(handle, ctypes.byref(identifier))
    return identifier.value


def covers(user32, handle, x, y):
    """窗口矩形是否盖住该物理点；用于确认候选窗口真的在鼠标下方。"""
    bounds = wintypes.RECT()
    if not user32.GetWindowRect(handle, ctypes.byref(bounds)):
        return False
    return bounds.left <= x <= bounds.right and bounds.top <= y <= bounds.bottom


def title_of(user32, handle):
    """读取窗口标题，仅用于日志；失败不影响识别。"""
    try:
        length = user32.GetWindowTextLengthW(handle)
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(handle, buffer, length + 1)
        return buffer.value
    except (OSError, ValueError):
        return ""


def class_of(user32, handle):
    """读取窗口类名，自绘界面通常只能靠它区分。"""
    try:
        buffer = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(handle, buffer, 256)
        return buffer.value
    except (OSError, ValueError):
        return ""


def rectangle_of(user32, handle):
    """读取窗口物理矩形；过小或读取失败时返回 None。"""
    bounds = wintypes.RECT()
    if not user32.GetWindowRect(handle, ctypes.byref(bounds)):
        return None
    if (bounds.right - bounds.left < MIN_SIZE or bounds.bottom - bounds.top < MIN_SIZE):
        return None
    return (bounds.left, bounds.top, bounds.right, bounds.bottom)
