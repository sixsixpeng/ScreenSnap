"""以虚拟桌面像素坐标管理多个选区。"""

from PySide6.QtCore import QPoint, QRect


class SelectionRects:
    """保存多个局部选区，并区分新建、整体拖动与四角缩放。"""

    def __init__(self):
        self.rects = []
        self.start = None
        self.active = None
        self.dragging = None
        self.drag_origin = None
        self.resizing = None
        self.resize_anchor = None
        self.resize_point = None
        self.resize_axis = None
        self.resize_original = None
        self.nudge_corner = None
        self.nudge_index = None

    @staticmethod
    def handles_for(rect):
        """返回四角和四边中点的控制柄、固定锚点、光标方向与缩放轴。"""
        center = rect.center()
        return (
            (rect.topLeft(), rect.bottomRight(), "nwse", "both"),
            (rect.topRight(), rect.bottomLeft(), "nesw", "both"),
            (rect.bottomLeft(), rect.topRight(), "nesw", "both"),
            (rect.bottomRight(), rect.topLeft(), "nwse", "both"),
            (QPoint(center.x(), rect.top()), QPoint(center.x(), rect.bottom()), "vertical", "y"),
            (QPoint(rect.right(), center.y()), QPoint(rect.left(), center.y()), "horizontal", "x"),
            (QPoint(center.x(), rect.bottom()), QPoint(center.x(), rect.top()), "vertical", "y"),
            (QPoint(rect.left(), center.y()), QPoint(rect.right(), center.y()), "horizontal", "x"),
        )

    def handle_at(self, point):
        for index in range(len(self.rects) - 1, -1, -1):
            rect = self.rects[index]
            for handle, anchor, cursor, axis in self.handles_for(rect):
                if abs(handle.x() - point.x()) <= 12 and abs(handle.y() - point.y()) <= 12:
                    return index, anchor, cursor, handle, axis
        return None

    def corner_at(self, point):
        """兼容旧调用，只返回四角命中；新交互使用 handle_at。"""
        hit = self.handle_at(point)
        return hit[:4] if hit is not None and hit[4] == "both" else None

    def border_at(self, point):
        """非缩放手柄的边框窄带可用于拖动，编辑画布内部仍处理标注。"""
        if self.handle_at(point) is not None:
            return False
        for rect in reversed(self.rects):
            if (rect.adjusted(-6, -6, 6, 6).contains(point) and
                    not rect.adjusted(6, 6, -6, -6).contains(point)):
                return True
        return False

    def begin(self, point):
        """优先选中顶层选区的缩放手柄，再判断整体拖动或新建。"""
        hit = self.handle_at(point)
        if hit is not None:
            self.resizing, self.resize_anchor, _, corner, self.resize_axis = hit
            self.resize_anchor = QPoint(self.resize_anchor)
            self.resize_point = QPoint(corner)
            self.resize_original = QRect(self.rects[self.resizing])
            self.nudge_index = self.resizing
            return
        self.nudge_corner = None
        for index in range(len(self.rects) - 1, -1, -1):
            if self.rects[index].adjusted(-6, -6, 6, 6).contains(point):
                self.nudge_corner = None
                self.dragging = index
                self.drag_origin = QPoint(point)
                self.nudge_index = index
                return
        self.nudge_corner = None
        self.nudge_index = None
        self.start = QPoint(point)
        self.active = QRect(point, point)

    def update(self, point, x_edges=(), y_edges=()):
        """根据当前交互状态修改已有选区，或吸附新建选区的末端。"""
        if self.resizing is not None:
            self.resize_point = QPoint(point)
            self.rects[self.resizing] = self.resized_rect(
                self.resize_anchor, point, self.resize_axis, self.resize_original
            )
            return
        if self.dragging is not None:
            self.rects[self.dragging].translate(point - self.drag_origin)
            self.drag_origin = QPoint(point)
            return
        if self.start is None:
            return
        x, y = point.x(), point.y()
        for edge in x_edges:
            if abs(x - edge) <= 8:
                x = edge
        for edge in y_edges:
            if abs(y - edge) <= 8:
                y = edge
        self.active = QRect(self.start, QPoint(x, y)).normalized()

    def finish(self):
        """只把有效的新选区加入列表，拖动和缩放不会额外新增选区。"""
        if self.resizing is not None:
            self.nudge_corner = (self.resizing, self.resize_anchor, self.resize_point,
                                 self.resize_axis, self.resize_original)
            self.resizing = None
            self.resize_anchor = None
            self.resize_point = None
            self.resize_axis = None
            self.resize_original = None
            return
        if self.dragging is not None:
            self.dragging = None
            self.drag_origin = None
            return
        if self.active and self.active.width() > 2 and self.active.height() > 2:
            self.rects.append(self.active)
            self.nudge_index = len(self.rects) - 1
        self.active = None
        self.start = None

    def move_last(self, dx, dy):
        """键盘接管当前指针操作，再微调选区，避免后续鼠标事件回写旧位置。"""
        if self.resizing is not None or self.dragging is not None:
            self.finish()
        if self.nudge_corner is not None:
            index, anchor, point, axis, original = self.nudge_corner
            if axis == "x":
                dy = 0
            elif axis == "y":
                dx = 0
            point = point + QPoint(dx, dy)
            self.rects[index] = self.resized_rect(anchor, point, axis, original)
            self.nudge_corner = (index, anchor, point, axis, original)
            return
        if self.rects:
            index = self.nudge_index if self.nudge_index is not None else len(self.rects) - 1
            self.rects[index].translate(dx, dy)

    def fixed(self, point, width, height):
        """QRect 两端均包含在内，因此终点要减一像素。"""
        self.rects.append(QRect(point, QPoint(point.x() + width - 1, point.y() + height - 1)))
        self.nudge_corner = None
        self.nudge_index = len(self.rects) - 1

    @staticmethod
    def resized_rect(anchor, point, axis, original):
        if axis == "x":
            return QRect(min(anchor.x(), point.x()), original.top(),
                         max(1, abs(anchor.x() - point.x()) + 1), original.height())
        if axis == "y":
            return QRect(original.left(), min(anchor.y(), point.y()), original.width(),
                         max(1, abs(anchor.y() - point.y()) + 1))
        return QRect(anchor, point).normalized()