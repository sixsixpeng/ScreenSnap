"""屏幕物理矩形与 Qt 逻辑矩形的对应关系。

Windows 的 GetWindowRect 返回物理像素，而 QWidget 使用逻辑坐标；在高 DPI 或
多显示器下两者并不相等，贴图吸附前必须换算，否则吸附位置会按比例偏移。
"""

import logging
import os
import time
from dataclasses import dataclass

from PySide6.QtCore import QPoint, QRect
from PySide6.QtGui import QGuiApplication

# 显示器信息变化不频繁，拖动过程中按时间复用，避免每帧重新枚举。
CACHE_SECONDS = 1.0
# 坐标换算每帧都会发生，放到比 DEBUG 更细的级别，避免刷屏。
TRACE = 5
_cache = {"stamp": 0.0, "mappings": []}


@dataclass(frozen=True)
class ScreenMapping:
    """一块显示器的设备名、物理矩形、Qt 逻辑矩形与设备像素比。"""

    name: str
    physical: QRect
    logical: QRect
    dpr: float


def clear_cache():
    """显示器增减或缩放变化后调用，丢弃已缓存的映射。"""
    _cache["stamp"] = 0.0
    _cache["mappings"] = []


def screen_mappings():
    """返回当前显示器映射；没有 Qt 实例时返回空列表由调用方降级。"""
    now = time.monotonic()
    if _cache["mappings"] and now - _cache["stamp"] < CACHE_SECONDS:
        return _cache["mappings"]
    mappings = build_mappings()
    _cache["stamp"] = now
    # 内容没变就不重复记录，避免每次刷新缓存都刷一条日志。
    if mappings != _cache["mappings"]:
        _cache["mappings"] = mappings
        logging.getLogger("screensnap").debug(
            "屏幕映射 %d 块: %s", len(mappings),
            " | ".join(describe(mapping) for mapping in mappings) or "无 Qt 屏幕")
    return _cache["mappings"]


def describe(mapping):
    """把一块显示器的映射写成一行日志，便于核对换算是否正确。"""
    return (f"{mapping.name} 逻辑({mapping.logical.left()},{mapping.logical.top()},"
            f"{mapping.logical.width()}x{mapping.logical.height()})"
            f" 物理({mapping.physical.left()},{mapping.physical.top()},"
            f"{mapping.physical.width()}x{mapping.physical.height()})"
            f" dpr={mapping.dpr:.2f}")


def build_mappings():
    """读取 Qt 屏幕的矩形与缩放，再和 Windows 物理屏幕配对。"""
    application = QGuiApplication.instance()
    if application is None:
        return []
    logical = [{"name": screen.name(), "geometry": QRect(screen.geometry()),
                "dpr": float(screen.devicePixelRatio()) or 1.0}
               for screen in application.screens()]
    return pair_screens(logical, enum_physical_monitors())


def pair_screens(logical, physical):
    """按设备名配对，配不上时按屏幕排列顺序一一对应，最后才用尺寸比例兜底。

    Qt 的 QScreen.name() 在 Windows 上通常是显示器型号（如 T2752Q），而 Windows
    给出的是 \\\\.\\DISPLAYn，两者对不上；此时若改用尺寸匹配，遇到两块分辨率相同
    的显示器就会配反，导致水平坐标整体偏移一整块屏。按排列顺序配对可以避免。
    """
    logger = logging.getLogger("screensnap")
    mappings = []
    pending = list(logical)
    ordered = sorted(physical, key=lambda item: (item[1].left(), item[1].top()))
    used = set()
    for index, (name, rect) in enumerate(ordered):
        found = next((i for i, item in enumerate(pending) if same_device(item["name"], name)), None)
        if found is None:
            continue
        item = pending.pop(found)
        used.add(index)
        mappings.append(ScreenMapping(item["name"], rect, item["geometry"], item["dpr"]))
        logger.log(TRACE, "屏幕配对[设备名]: Qt %r 逻辑(%d,%d,%dx%d) ↔ %s 物理(%d,%d,%dx%d)",
                     item["name"], item["geometry"].left(), item["geometry"].top(),
                     item["geometry"].width(), item["geometry"].height(), name,
                     rect.left(), rect.top(), rect.width(), rect.height())
    rest = [(name, rect) for index, (name, rect) in enumerate(ordered) if index not in used]
    ordered_pending = sorted(pending, key=lambda item: (item["geometry"].left(),
                                                        item["geometry"].top()))
    for (name, rect), item in zip(rest, ordered_pending):
        mappings.append(ScreenMapping(item["name"], rect, item["geometry"], item["dpr"]))
        logger.log(TRACE, "屏幕配对[排列顺序]: Qt %r 逻辑(%d,%d,%dx%d) ↔ %s 物理(%d,%d,%dx%d)",
                     item["name"], item["geometry"].left(), item["geometry"].top(),
                     item["geometry"].width(), item["geometry"].height(), name,
                     rect.left(), rect.top(), rect.width(), rect.height())
    for item in ordered_pending[len(rest):]:
        # 没有物理矩形可用时按缩放比例推导，保证同一屏幕内比例仍然正确。
        mappings.append(ScreenMapping(item["name"], scaled_rect(item["geometry"], item["dpr"]),
                                      item["geometry"], item["dpr"]))
        logger.log(TRACE, "屏幕配对[无物理矩形]: Qt %r 逻辑(%d,%d,%dx%d) 按 dpr=%.2f 推导",
                     item["name"], item["geometry"].left(), item["geometry"].top(),
                     item["geometry"].width(), item["geometry"].height(), item["dpr"])
    return mappings


def same_device(left, right):
    """Qt 与 Windows 的设备名前缀不同，只比较最后的 DISPLAYn 部分。"""
    first = normalize_device(left)
    return bool(first) and first == normalize_device(right)


def normalize_device(name):
    return str(name or "").replace("\\\\.\\", "").strip().lower()


def scaled_rect(rect, dpr):
    """按缩放比例推导物理矩形，仅在拿不到真实物理矩形时使用。"""
    return QRect(round(rect.left() * dpr), round(rect.top() * dpr),
                 round(rect.width() * dpr), round(rect.height() * dpr))


def enum_physical_monitors():
    """枚举 Windows 物理屏幕矩形；非 Windows 返回空列表。"""
    if os.name != "nt":
        return []
    import ctypes
    from ctypes import wintypes

    class MonitorInfo(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                    ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD),
                    ("szDevice", wintypes.WCHAR * 32)]

    user32 = ctypes.windll.user32
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC,
                                       ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)
    monitors = []

    def collect(handle, unused, rect, data):
        info = MonitorInfo()
        info.cbSize = ctypes.sizeof(info)
        if user32.GetMonitorInfoW(handle, ctypes.byref(info)):
            monitors.append((info.szDevice, native_rect(info.rcMonitor)))
        return True

    try:
        callback = callback_type(collect)
        user32.EnumDisplayMonitors(None, None, callback, 0)
    except (OSError, ValueError) as error:
        logging.getLogger("screensnap").warning("枚举显示器失败，吸附退化为按缩放比例换算: %s", error)
        return []
    logging.getLogger("screensnap").debug("枚举到 %d 块物理显示器: %s", len(monitors),
                                         [name for name, _ in monitors])
    return monitors


def native_rect(bounds):
    """把 Win32 RECT 转成 Qt 矩形。"""
    return QRect(bounds.left, bounds.top, bounds.right - bounds.left, bounds.bottom - bounds.top)


def distance(rect, point):
    """点到矩形的平方距离，用于挑选离目标最近的那块屏幕。"""
    dx = max(rect.left() - point.x(), 0, point.x() - rect.right())
    dy = max(rect.top() - point.y(), 0, point.y() - rect.bottom())
    return dx * dx + dy * dy


def mapping_for_physical(point):
    """找到覆盖该物理点的屏幕；落在缝隙里时取最近的一块。"""
    mappings = screen_mappings()
    if not mappings:
        return None
    for mapping in mappings:
        if mapping.physical.contains(point):
            return mapping
    return min(mappings, key=lambda item: distance(item.physical, point))


def mapping_for_logical(point):
    """找到覆盖该逻辑点的屏幕；落在缝隙里时取最近的一块。"""
    mappings = screen_mappings()
    if not mappings:
        return None
    for mapping in mappings:
        if mapping.logical.contains(point):
            return mapping
    return min(mappings, key=lambda item: distance(item.logical, point))


def scale_of(mapping):
    """返回该屏幕物理像素到逻辑坐标的换算比例。"""
    scale_x = mapping.logical.width() / mapping.physical.width() if mapping.physical.width() else 1.0
    scale_y = mapping.logical.height() / mapping.physical.height() if mapping.physical.height() else 1.0
    return scale_x, scale_y


def physical_rect_to_logical(rect):
    """把 Win32 物理像素矩形换算成 Qt 逻辑矩形。"""
    mapping = mapping_for_physical(rect.center())
    if mapping is None:
        return QRect(rect)
    scale_x, scale_y = scale_of(mapping)
    result = QRect(
        round(mapping.logical.left() + (rect.left() - mapping.physical.left()) * scale_x),
        round(mapping.logical.top() + (rect.top() - mapping.physical.top()) * scale_y),
        round(rect.width() * scale_x),
        round(rect.height() * scale_y),
    )
    logging.getLogger("screensnap").log(
        TRACE,
        "物理矩形(%d,%d,%dx%d) → 逻辑矩形(%d,%d,%dx%d) 屏幕=%s",
        rect.left(), rect.top(), rect.width(), rect.height(),
        result.left(), result.top(), result.width(), result.height(), mapping.name)
    return result


def logical_point_to_physical(point):
    """把 Qt 逻辑点换算成 Win32 物理像素点，供窗口命中测试使用。"""
    mapping = mapping_for_logical(point)
    if mapping is None:
        return QPoint(point)
    scale_x, scale_y = scale_of(mapping)
    result = QPoint(
        round(mapping.physical.left() + (point.x() - mapping.logical.left()) / scale_x),
        round(mapping.physical.top() + (point.y() - mapping.logical.top()) / scale_y),
    )
    logging.getLogger("screensnap").log(TRACE, "逻辑点(%d,%d) → 物理点(%d,%d) 屏幕=%s",
                                          point.x(), point.y(), result.x(), result.y(), mapping.name)
    return result
