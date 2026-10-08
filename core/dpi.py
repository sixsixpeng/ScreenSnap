"""Windows/Qt 高 DPI 与截图物理像素坐标辅助。"""

from dataclasses import dataclass
from math import inf
import logging
import os

from PySide6.QtCore import QPoint, QRect, QRectF
from PySide6.QtGui import QGuiApplication


@dataclass(frozen=True)
class ScreenMapping:
    """一块显示器在截图物理像素、内部逻辑坐标和 Qt 原生窗口坐标中的对应关系。"""

    physical: QRect
    logical: QRect
    dpr: float
    native: QRect


class DisplayMapper:
    """在 mss 物理像素坐标与 Qt 逻辑窗口坐标之间转换。"""

    def __init__(self, bounds, monitors, screen_infos=None, monitor_scales=None):
        self.bounds = dict(bounds)
        # 每块物理屏的真实缩放（Windows 报告）：用来消除尺寸歧义 —— 4K@150% 的 Qt 原生尺寸
        # 与真正的 2K@100% 完全相同，只看尺寸会配错屏并因此用错 dpr。测试可注入。
        self.monitor_scales = list(monitor_scales) if monitor_scales else None
        self.monitors = [dict(monitor) for monitor in monitors]
        self.physical_bounds = QRect(
            self.bounds["left"], self.bounds["top"],
            self.bounds["width"], self.bounds["height"],
        )
        self.mappings = self._build_mappings(screen_infos)
        self.logical_bounds = self._logical_union()

    @staticmethod
    def collect_screen_infos():
        """读取 Qt 当前屏幕信息；测试可注入 screen_infos 避免依赖真实显示器。"""
        if QGuiApplication.instance() is None:
            return []
        return [
            {"geometry": QRect(screen.geometry()), "dpr": float(screen.devicePixelRatio()) or 1.0}
            for screen in QGuiApplication.screens()
        ]

    def _build_mappings(self, screen_infos):
        physical_monitors = self.monitors or [self.bounds]
        screen_infos = list(self.collect_screen_infos() if screen_infos is None else screen_infos)
        physical_and_logical = []
        for index, monitor in enumerate(physical_monitors):
            physical = QRect(monitor["left"], monitor["top"], monitor["width"], monitor["height"])
            match_index = self._take_best_screen_index(physical, screen_infos,
                                                       self._monitor_scale(index, physical))
            if match_index is None:
                logical = QRect(physical)
                dpr = 1.0
            else:
                match = screen_infos.pop(match_index)
                logical = QRect(match["geometry"])
                dpr = max(float(match.get("dpr", 1.0)), 0.01)
            physical_and_logical.append((physical, logical, dpr))
        mappings = self._compact_mappings(physical_and_logical)
        return mappings or [ScreenMapping(QRect(self.physical_bounds), QRect(self.physical_bounds),
                                          1.0, QRect(self.physical_bounds))]

    @staticmethod
    def _scale_x(mapping):
        return mapping.logical.width() / mapping.physical.width() if mapping.physical.width() else 1.0

    @staticmethod
    def _scale_y(mapping):
        return mapping.logical.height() / mapping.physical.height() if mapping.physical.height() else 1.0

    @staticmethod
    def _overlaps(start_a, end_a, start_b, end_b):
        return max(start_a, start_b) < min(end_a, end_b)

    @staticmethod
    def _right(rect):
        return rect.left() + rect.width()

    @staticmethod
    def _bottom(rect):
        return rect.top() + rect.height()

    def _neighbor_rect(self, source, target):
        source_physical, source_logical = source
        target_physical, target_logical = target
        target_width, target_height = target_logical.width(), target_logical.height()
        source_scale_x = source_logical.width() / source_physical.width() if source_physical.width() else 1.0
        source_scale_y = source_logical.height() / source_physical.height() if source_physical.height() else 1.0
        source_right = self._right(source_physical)
        source_bottom = self._bottom(source_physical)
        target_right = self._right(target_physical)
        target_bottom = self._bottom(target_physical)

        if source_right == target_physical.left() and self._overlaps(
                source_physical.top(), source_bottom, target_physical.top(), target_bottom):
            top = source_logical.top() + round((target_physical.top() - source_physical.top()) * source_scale_y)
            return QRect(source_logical.left() + source_logical.width(), top, target_width, target_height)
        if target_right == source_physical.left() and self._overlaps(
                source_physical.top(), source_bottom, target_physical.top(), target_bottom):
            top = source_logical.top() + round((target_physical.top() - source_physical.top()) * source_scale_y)
            return QRect(source_logical.left() - target_width, top, target_width, target_height)
        if source_bottom == target_physical.top() and self._overlaps(
                source_physical.left(), source_right, target_physical.left(), target_right):
            left = source_logical.left() + round((target_physical.left() - source_physical.left()) * source_scale_x)
            return QRect(left, source_logical.top() + source_logical.height(), target_width, target_height)
        if target_bottom == source_physical.top() and self._overlaps(
                source_physical.left(), source_right, target_physical.left(), target_right):
            left = source_logical.left() + round((target_physical.left() - source_physical.left()) * source_scale_x)
            return QRect(left, source_logical.top() - target_height, target_width, target_height)
        return None

    def _compact_mappings(self, physical_and_logical):
        """按物理相邻关系平移屏幕，避免伪 gap；不改变单屏自身缩放比例。"""
        if not physical_and_logical:
            return []
        positioned = {}
        anchors = sorted(range(len(physical_and_logical)),
                         key=lambda index: (physical_and_logical[index][0].top(), physical_and_logical[index][0].left()))
        for anchor in anchors:
            if anchor in positioned:
                continue
            positioned[anchor] = QRect(physical_and_logical[anchor][1])
            changed = True
            while changed:
                changed = False
                for target_index, target_pair in enumerate(physical_and_logical):
                    if target_index in positioned:
                        continue
                    candidates = []
                    for source_index, source_logical in list(positioned.items()):
                        source = (physical_and_logical[source_index][0], source_logical)
                        logical = self._neighbor_rect(source, (target_pair[0], target_pair[1]))
                        if logical is not None:
                            candidates.append(logical)
                    if candidates:
                        original = target_pair[1]
                        positioned[target_index] = min(
                            candidates,
                            key=lambda rect: abs(rect.left() - original.left()) + abs(rect.top() - original.top()),
                        )
                        changed = True
        return [ScreenMapping(physical, positioned[index], dpr, logical)
                for index, (physical, logical, dpr) in enumerate(physical_and_logical)]

    def _monitor_scale(self, index, physical):
        """该物理屏的真实缩放：优先用注入值，否则问 Windows；拿不到返回 None。"""
        if self.monitor_scales is not None:
            return self.monitor_scales[index] if index < len(self.monitor_scales) else None
        return win32_monitor_scale(physical)

    @staticmethod
    def _take_best_screen_index(physical, candidates, scale=None):
        best_index = None
        best_score = inf
        scored = []  # (index, size_score, position_score, score, dpr, tolerance)
        for index, candidate in enumerate(candidates):
            logical = QRect(candidate["geometry"])
            dpr = max(float(candidate.get("dpr", 1.0)), 0.01)
            # 尺寸相同但缩放不同的屏（4K@150% ↔ 2K@100%）靠尺寸分不开，必须用实际缩放消歧，
            # 否则会用错 dpr 与物理偏移。
            # 缩放不匹配只做「软惩罚」：一致者优先（消除 4K@150% 与 2K@100% 的尺寸歧义），
            # 但若没有任何候选一致（合成布局、驱动上报异常）也不至于把屏全拒掉。
            scale_penalty = 0.0 if (scale is None or abs(dpr - scale) <= 0.05) else 1.0
            logical_score = float(abs(physical.width() - logical.width() * dpr) +
                                  abs(physical.height() - logical.height() * dpr))
            physical_score = float(abs(physical.width() - logical.width()) + abs(physical.height() - logical.height()))
            size_score = logical_score if logical_score <= physical_score else physical_score
            native_dx: int = abs(int(physical.left()) - int(logical.left()))
            native_dy: int = abs(int(physical.top()) - int(logical.top()))
            scaled_dx: int = abs(int(physical.left()) - int(round(float(logical.left()) * dpr)))
            scaled_dy: int = abs(int(physical.top()) - int(round(float(logical.top()) * dpr)))
            native_position_score: float = float(native_dx + native_dy)
            scaled_position_score: float = float(scaled_dx + scaled_dy)
            position_score = (native_position_score if native_position_score <= scaled_position_score
                              else scaled_position_score)
            tolerance = max(8.0, float(max(physical.width(), physical.height())) * 0.06)
            score = scale_penalty * 1e12 + size_score * 100000.0 + position_score
            scored.append((index, size_score, position_score, score, dpr, tolerance))
            if size_score <= tolerance and score < best_score:
                best_index = index
                best_score = score
        # 只在「缩放提示改变了择优结果」或「无缩放提示且存在同尺寸候选」时记录：配对选错正是
        # UIA 高亮偏移与截图/贴图落到邻屏的根因，平时不刷屏（第 14 条）。
        within = [item for item in scored if item[1] <= item[5]]
        if best_index is not None and within:
            smallest = min(item[1] for item in within)
            ambiguous = sum(1 for item in within if abs(item[1] - smallest) < 1e-6)
            plain = min(within, key=lambda item: item[1] * 100000.0 + item[2])
            if plain[0] != best_index:
                logging.getLogger('screensnap').debug(
                    '缩放消歧改变了屏幕配对: 物理(%d,%d,%dx%d) 选中 #%d(dpr=%.2f)，'
                    '仅按尺寸+位置会选 #%d(dpr=%.2f)；缩放提示=%s',
                    physical.left(), physical.top(), physical.width(), physical.height(),
                    best_index, scored[best_index][4], plain[0], plain[4], scale)
            elif scale is None and ambiguous > 1:
                logging.getLogger('screensnap').debug(
                    '无缩放提示且存在 %d 个同尺寸候选: 物理(%d,%d,%dx%d) 按尺寸+位置选 #%d(dpr=%.2f)',
                    ambiguous, physical.left(), physical.top(), physical.width(),
                    physical.height(), best_index, scored[best_index][4])
        return best_index

    def _logical_union(self):
        result = QRect(self.mappings[0].logical)
        for mapping in self.mappings[1:]:
            result = result.united(mapping.logical)
        return result

    @staticmethod
    def _distance_to_rect(point, rect):
        dx = max(rect.left() - point.x(), 0, point.x() - rect.right())
        dy = max(rect.top() - point.y(), 0, point.y() - rect.bottom())
        return dx * dx + dy * dy

    def _mapping_for_physical(self, point):
        for mapping in self.mappings:
            if mapping.physical.contains(point):
                return mapping
        return min(self.mappings, key=lambda mapping: self._distance_to_rect(point, mapping.physical))

    def _mapping_for_logical(self, point):
        for mapping in self.mappings:
            if mapping.logical.contains(point):
                return mapping
        return min(self.mappings, key=lambda mapping: self._distance_to_rect(point, mapping.logical))

    def _mapping_for_native(self, point):
        for mapping in self.mappings:
            if mapping.native.contains(point):
                return mapping
        return min(self.mappings, key=lambda mapping: self._distance_to_rect(point, mapping.native))

    def physical_global_to_logical_global(self, point):
        mapping = self._mapping_for_physical(point)
        x = mapping.logical.left() + (point.x() - mapping.physical.left()) * self._scale_x(mapping)
        y = mapping.logical.top() + (point.y() - mapping.physical.top()) * self._scale_y(mapping)
        return QPoint(round(x), round(y))

    def logical_global_to_physical_global(self, point):
        mapping = self._mapping_for_logical(point)
        x = mapping.physical.left() + (point.x() - mapping.logical.left()) / self._scale_x(mapping)
        y = mapping.physical.top() + (point.y() - mapping.logical.top()) / self._scale_y(mapping)
        return QPoint(round(x), round(y))

    def native_global_to_physical_global(self, point):
        mapping = self._mapping_for_native(point)
        scale_x = mapping.physical.width() / mapping.native.width() if mapping.native.width() else 1.0
        scale_y = mapping.physical.height() / mapping.native.height() if mapping.native.height() else 1.0
        x = mapping.physical.left() + (point.x() - mapping.native.left()) * scale_x
        y = mapping.physical.top() + (point.y() - mapping.native.top()) * scale_y
        return QPoint(round(x), round(y))

    def physical_global_to_native_global(self, point):
        mapping = self._mapping_for_physical(point)
        scale_x = mapping.native.width() / mapping.physical.width() if mapping.physical.width() else 1.0
        scale_y = mapping.native.height() / mapping.physical.height() if mapping.physical.height() else 1.0
        x = mapping.native.left() + (point.x() - mapping.physical.left()) * scale_x
        y = mapping.native.top() + (point.y() - mapping.physical.top()) * scale_y
        return QPoint(round(x), round(y))

    def physical_local_to_logical_local(self, point):
        global_point = QPoint(point.x() + self.bounds["left"], point.y() + self.bounds["top"])
        logical = self.physical_global_to_logical_global(global_point)
        return logical - self.logical_bounds.topLeft()

    def logical_local_to_physical_local(self, point):
        global_point = point + self.logical_bounds.topLeft()
        physical = self.logical_global_to_physical_global(global_point)
        return physical - self.physical_bounds.topLeft()

    def physical_global_to_local(self, point):
        return point - self.physical_bounds.topLeft()

    def physical_local_to_global(self, point):
        return point + self.physical_bounds.topLeft()

    def physical_local_to_native_global(self, point):
        physical = self.physical_local_to_global(point)
        mapping = self._mapping_for_physical(physical)
        native = self.physical_global_to_native_global(physical)
        logging.getLogger("screensnap").debug(
            "截图贴图锚点映射: 物理局部=(%d,%d) 物理全局=(%d,%d) 显示器物理=(%d,%d,%dx%d) "
            "Qt原生屏幕=(%d,%d,%dx%d) 锚点=(%d,%d)",
            point.x(), point.y(), physical.x(), physical.y(),
            mapping.physical.x(), mapping.physical.y(), mapping.physical.width(), mapping.physical.height(),
            mapping.native.x(), mapping.native.y(), mapping.native.width(), mapping.native.height(),
            native.x(), native.y())
        return native

    def physical_local_rect_to_logical_local_rect(self, rect):
        global_top_left = self.physical_local_to_global(rect.topLeft())
        mapping = self._mapping_for_physical(global_top_left)
        top_left = self.physical_global_to_logical_global(global_top_left) - self.logical_bounds.topLeft()
        return QRectF(top_left.x(), top_left.y(),
                      rect.width() * self._scale_x(mapping), rect.height() * self._scale_y(mapping))

    def physical_local_rect_to_logical_global_rect(self, rect):
        local = self.physical_local_rect_to_logical_local_rect(rect)
        origin = self.logical_bounds.topLeft()
        return local.translated(origin.x(), origin.y())

    def physical_local_rect_to_native_global_rect(self, rect):
        mapping = self._mapping_for_physical(self.physical_local_to_global(rect.topLeft()))
        return QRectF(mapping.native)

    def monitor_local_rect(self, monitor):
        return QRect(
            monitor["left"] - self.bounds["left"],
            monitor["top"] - self.bounds["top"],
            monitor["width"], monitor["height"],
        )

    def full_physical_local_rect(self):
        return QRect(0, 0, self.bounds["width"], self.bounds["height"])


def win32_monitor_scale(rect):
    """问 Windows 要这块物理矩形所在显示器的实际缩放（96dpi = 100%）；拿不到返回 None。

    用来消除尺寸歧义：4K 面板跑 150% 时 Qt 原生尺寸是 2560x1440，与真正的 2K@100% 屏一模一样，
    只看尺寸会把两者配错屏、进而用错 dpr（贴图与截图尺寸随之被放大）。
    """
    if os.name != "nt":
        return None
    import ctypes
    from ctypes import wintypes

    class Point(ctypes.Structure):
        _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]

    try:
        user32 = ctypes.windll.user32
        shcore = ctypes.windll.shcore
        user32.MonitorFromPoint.argtypes = (Point, wintypes.DWORD)
        user32.MonitorFromPoint.restype = wintypes.HANDLE
        monitor = user32.MonitorFromPoint(Point(rect.center().x(), rect.center().y()), 2)
        dpi_x, dpi_y = wintypes.UINT(), wintypes.UINT()
        result = shcore.GetDpiForMonitor(monitor, 0, ctypes.byref(dpi_x), ctypes.byref(dpi_y))
        if monitor and result == 0 and dpi_x.value:
            return dpi_x.value / 96.0
    except Exception as error:  # 缺少 shcore、ctypes 调用约定差异等一律降级
        logging.getLogger("screensnap").debug("查询显示器缩放失败，跳过缩放消歧: %s", error)
    return None
