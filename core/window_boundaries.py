"""可见窗口边缘坐标；其他平台返回空列表。"""

import logging
import os


def visible_windows():
    """仅在 Windows 上枚举可见且未最小化的窗口，用于选区吸附。"""
    if os.name != "nt":
        return []
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    rectangles = []
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def collect(handle, unused):
        # ctypes 回调里抛异常无法向上传播（返回值会变成未定义，EnumWindows 可能提前中止），
        # 所以回调体一律整体兜底，并且始终返回 True（2026-10-11 全局崩溃排查）。
        try:
            # 过滤极小窗口，避免不可见工具窗口干扰选区吸附。
            if user32.IsWindowVisible(handle) and not user32.IsIconic(handle):
                bounds = wintypes.RECT()
                if user32.GetWindowRect(handle, ctypes.byref(bounds)):
                    if bounds.right - bounds.left > 40 and bounds.bottom - bounds.top > 40:
                        rectangles.append((bounds.left, bounds.top, bounds.right, bounds.bottom))
        except Exception:  # noqa: BLE001 单个窗口读取失败只跳过它
            pass
        return True

    try:
        callback = callback_type(collect)
        # 保持回调对象存活到 EnumWindows 返回，防止原生回调访问失效内存。
        user32.EnumWindows(callback, 0)
    except (OSError, ValueError) as error:
        # 系统枚举偶发失败，降级为不吸附，不影响截图本身。
        logging.getLogger("screensnap").warning("枚举窗口失败，本次不提供窗口吸附: %s", error)
        return []
    logger = logging.getLogger("screensnap")
    logger.debug("枚举到 %d 个可见窗口", len(rectangles))
    for index, (left, top, right, bottom) in enumerate(rectangles):
        logger.debug("可见窗口[%d]: 物理(%d,%d,%dx%d)", index, left, top,
                     right - left, bottom - top)
    return rectangles