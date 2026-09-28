"""Windows/Qt 高 DPI 与截图物理像素坐标辅助。"""

from dataclasses import dataclass
from math import inf

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

    def __init__(self, bounds, monitors, screen_infos=None):
        self.bounds = dict(bounds)
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
        for monitor in physical_monitors:
            physical = QRect(monitor["left"], monitor["top"], monitor["width"], monitor["height"])
            match_index = self._take_best_screen_index(physical, screen_infos)
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

    @staticmethod
    def _take_best_screen_index(physical, candidates):
        best_index = None
        best_score = inf
        for index, candidate in enumerate(candidates):
            logical = QRect(candidate["geometry"])
            dpr = max(float(candidate.get("dpr", 1.0)), 0.01)
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
            score = size_score * 100000.0 + position_score
            if size_score <= tolerance and score < best_score:
                best_index = index
                best_score = score
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







