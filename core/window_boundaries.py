"""可见窗口边缘坐标；其他平台返回空列表。"""

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
        # 过滤极小窗口，避免不可见工具窗口干扰选区吸附。
        if user32.IsWindowVisible(handle) and not user32.IsIconic(handle):
            bounds = wintypes.RECT()
            if user32.GetWindowRect(handle, ctypes.byref(bounds)):
                if bounds.right - bounds.left > 40 and bounds.bottom - bounds.top > 40:
                    rectangles.append((bounds.left, bounds.top, bounds.right, bounds.bottom))
        return True

    callback = callback_type(collect)
    # 保持回调对象存活到 EnumWindows 返回，防止原生回调访问失效内存。
    user32.EnumWindows(callback, 0)
    return rectangles