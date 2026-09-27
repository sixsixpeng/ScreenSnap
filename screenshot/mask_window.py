"""全屏统一遮罩与多选区事件分发。"""

from PySide6.QtCore import Qt, Signal, QPoint, QRect
from PySide6.QtGui import QColor, QCursor, QPainter, QPen
from PySide6.QtWidgets import QWidget, QDialog, QDialogButtonBox, QFormLayout, QSpinBox, QStyle

from core.screen_capture import to_qimage
from core.window_boundaries import visible_windows
from screenshot.selection_rect import SelectionRects
from screenshot.overlay_info import paint_info
from screenshot.magnifier_widget import paint_magnifier


class MaskWindow(QWidget):
    """覆盖虚拟桌面的交互窗口；原始像素始终保存在 Pillow 图像中。"""

    selected = Signal(object)
    last_region = Signal(object)

    def __init__(self, image, bounds, monitors, settings, mode="capture", alternate=None):
        super().__init__()
        self.image = image
        self.alternate = alternate
        self.preview = to_qimage(image)
        self.bounds = bounds
        self.monitors = monitors
        # 截图前只枚举一次窗口边界，避免鼠标移动时反复调用系统 API。
        self.window_edges = visible_windows()
        self.settings = settings
        self.selection = SelectionRects()
        self.position = QPoint(0, 0)
        self.resize_cursor = "nwse"
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setGeometry(bounds["left"], bounds["top"], bounds["width"], bounds["height"])
        if mode == "fullscreen":
            self.selection.rects.append(QRect(0, 0, bounds["width"], bounds["height"]))
        elif mode == "monitor":
            point = self.cursor().pos()
            monitor = next((screen for screen in monitors if
                            screen["left"] <= point.x() < screen["left"] + screen["width"] and
                            screen["top"] <= point.y() < screen["top"] + screen["height"]), monitors[0])
            self.selection.rects.append(QRect(monitor["left"] - bounds["left"],
                                              monitor["top"] - bounds["top"],
                                              monitor["width"], monitor["height"]))

    def showEvent(self, event):
        super().showEvent(event)
        self.activateWindow()
        self.setFocus(Qt.ActiveWindowFocusReason)

    def paintEvent(self, event):
        """遮罩、锚点和 HUD 仅绘制在窗口表面，不写入原始截图。"""
        painter = QPainter(self)
        painter.drawImage(self.rect(), self.preview)
        opacity = round(255 * self.settings.get("mask_opacity", 50) / 100)
        shade = QColor(0, 0, 0, opacity) if self.settings["mask_theme"] == "dark" else QColor(255, 255, 255, opacity)
        painter.fillRect(self.rect(), shade)
        for rect in self.selection.rects + ([self.selection.active] if self.selection.active else []):
            painter.setPen(QPen(QColor(self.settings.get("selection_border_color", "#ff0000")), 1))
            painter.drawImage(rect, self.preview, rect)
            painter.drawRect(rect)
            painter.setPen(QPen(QColor("#00d7aa"), 2))
            for handle, _, _, axis in self.selection.handles_for(rect):
                size = 11 if axis == "both" else 9
                anchor = QRect(handle.x() - size // 2, handle.y() - size // 2, size, size)
                painter.fillRect(anchor.adjusted(-1, -1, 1, 1), QColor("#ffffff"))
                if self.settings["anchor_style"] == "fill":
                    painter.fillRect(anchor, QColor("#00d7aa"))
                else:
                    painter.drawRect(anchor)
        if self.settings["crosshair"]:
            painter.setPen(QPen(QColor(self.settings.get("crosshair_color", "#ff0000")),
                                self.settings.get("crosshair_width", 1)))
            painter.drawLine(self.position.x(), 0, self.position.x(), self.height())
            painter.drawLine(0, self.position.y(), self.width(), self.position.y())
        global_position = self.position + QPoint(self.bounds["left"], self.bounds["top"])
        current_selection = self.selection.active or (
            self.selection.rects[-1] if self.selection.rects else None
        )
        for monitor in self.monitors:
            monitor_area = QRect(
                monitor["left"] - self.bounds["left"],
                monitor["top"] - self.bounds["top"],
                monitor["width"], monitor["height"],
            )
            paint_info(painter, global_position, current_selection, monitor_area)
        painter.setPen(QPen(QColor(self.settings.get("selection_border_color", "#ff0000")), 1))
        painter.setBrush(Qt.NoBrush)
        for rect in self.selection.rects + ([self.selection.active] if self.selection.active else []):
            painter.drawRect(rect)
        if self.settings["magnifier"]:
            paint_magnifier(painter, self.preview, self.position, self.rect())

    def mousePressEvent(self, event):
        """左键开始创建选区，已有选区的命中由选区对象判定。"""
        if event.button() == Qt.LeftButton:
            self.position = event.position().toPoint()
            hit = self.selection.handle_at(event.position().toPoint())
            if hit is not None:
                self.resize_cursor = hit[2]
                self.setCursor({"nwse": Qt.SizeFDiagCursor, "nesw": Qt.SizeBDiagCursor,
                                "horizontal": Qt.SizeHorCursor,
                                "vertical": Qt.SizeVerCursor}[self.resize_cursor])
            self.selection.begin(event.position().toPoint())

    def mouseMoveEvent(self, event):
        """将显示器及窗口的全局边缘转换为遮罩内的局部坐标。"""
        self.position = event.position().toPoint()
        hit = self.selection.handle_at(self.position)
        if self.selection.resizing is not None or hit is not None:
            cursor = self.resize_cursor if self.selection.resizing is not None else hit[2]
            self.resize_cursor = cursor
            cursor_shapes = {"nwse": Qt.SizeFDiagCursor, "nesw": Qt.SizeBDiagCursor,
                             "horizontal": Qt.SizeHorCursor, "vertical": Qt.SizeVerCursor}
            self.setCursor(cursor_shapes[cursor])
        elif self.selection.dragging is not None or any(rect.contains(self.position) for rect in self.selection.rects):
            self.setCursor(Qt.SizeAllCursor)
        else:
            self.setCursor(Qt.CrossCursor)
        if any((self.selection.start is not None, self.selection.dragging is not None,
            self.selection.resizing is not None)):
            x_edges = [coordinate for screen in self.monitors for coordinate in
                       (screen["left"] - self.bounds["left"], screen["left"] + screen["width"] - self.bounds["left"])]
            y_edges = [coordinate for screen in self.monitors for coordinate in
                       (screen["top"] - self.bounds["top"], screen["top"] + screen["height"] - self.bounds["top"])]
            x_edges.extend(edge - self.bounds["left"] for left, top, right, bottom in self.window_edges
                           for edge in (left, right))
            y_edges.extend(edge - self.bounds["top"] for left, top, right, bottom in self.window_edges
                           for edge in (top, bottom))
            self.selection.update(self.position, x_edges, y_edges)
        self.update()

    def mouseReleaseEvent(self, event):
        """结束当前选区的创建、拖动或缩放。"""
        if event.button() == Qt.LeftButton:
            self.position = event.position().toPoint()
            self.selection.finish()
            self.update()

    def mouseDoubleClickEvent(self, event):
        """双击提交所有有效选区，没有选区时仅关闭遮罩。"""
        self.complete()

    def keyPressEvent(self, event):
        """处理取消、提交、固定尺寸创建与最后选区的像素微调。"""
        key = event.key()
        if key == Qt.Key_Escape:
            self.close()
        elif key in (Qt.Key_Return, Qt.Key_Enter):
            self.complete()
        elif key == Qt.Key_F and event.modifiers() & Qt.ControlModifier:
            self.select_fixed_size()
        else:
            dx = (-1 if key in (Qt.Key_A, Qt.Key_Left) else 1 if key in (Qt.Key_D, Qt.Key_Right) else 0)
            dy = (-1 if key in (Qt.Key_W, Qt.Key_Up) else 1 if key in (Qt.Key_S, Qt.Key_Down) else 0)
            if dx or dy:
                self.selection.move_last(dx, dy)
                if self.selection.rects:
                    self.position += QPoint(dx, dy)
                    QCursor.setPos(self.mapToGlobal(self.position))
        self.update()

    def select_fixed_size(self):
        """一次输入宽高，确认后在当前光标位置创建选区。"""
        dialog = QDialog(self)
        dialog.setWindowTitle("固定选区")
        form = QFormLayout(dialog)
        width = QSpinBox(dialog)
        width.setRange(1, max(1, self.width()))
        width.setValue(min(640, width.maximum()))
        height = QSpinBox(dialog)
        height.setRange(1, max(1, self.height()))
        height.setValue(min(480, height.maximum()))
        form.addRow("宽度 (px)", width)
        form.addRow("高度 (px)", height)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=dialog)
        buttons.button(QDialogButtonBox.Ok).setIcon(self.style().standardIcon(QStyle.SP_DialogApplyButton))
        buttons.button(QDialogButtonBox.Cancel).setIcon(self.style().standardIcon(QStyle.SP_DialogCancelButton))
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec() == QDialog.Accepted:
            self.selection.fixed(self.position, width.value(), height.value())

    def complete(self):
        """从原图裁切每个选区，成对发出带/不带光标的图片。"""
        images = []
        last_rect = None
        for rect in self.selection.rects:
            # 移出遮罩的部分先裁到桌面范围；Pillow 的右下边界是开区间。
            clipped = rect.intersected(self.rect())
            if clipped.width() and clipped.height():
                area = (clipped.x(), clipped.y(), clipped.right() + 1, clipped.bottom() + 1)
                images.append((self.image.crop(area), self.alternate.crop(area) if self.alternate else None))
                last_rect = [clipped.x() + self.bounds["left"], clipped.y() + self.bounds["top"],
                             clipped.width(), clipped.height()]
        if images:
            self.hide()
            self.last_region.emit(last_rect)
            self.selected.emit(images)
        self.close()