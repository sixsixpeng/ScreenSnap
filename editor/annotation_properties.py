"""单个标注的参数编辑对话框。

与「更多设置」不同：这里的改动只作用于当前选中的这一个标注，不写回默认配置，
也不影响之后新画的标注。所有修改由调用方在对话框确认后记一次撤销点。
"""

from PySide6.QtCore import Qt
from PySide6.QtGui import QPen, QColor, QPixmap, QTransform
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                               QFileDialog, QFormLayout, QHBoxLayout, QLabel,
                               QMessageBox, QPushButton, QSpinBox, QVBoxLayout, QWidget)

from editor.annotation_items import (AnnotationRectItem, RoundedRectItem,
                                     AnnotationEllipseItem, AnnotationPathItem,
                                     AnnotationPixmapItem, AnnotationZoomItem, AnnotationSequenceItem,
                                     editable, shape, annotation_locked,
                                     set_annotation_locked)
from ui.action_icons import action_icon
from ui.widgets.color_button import ColorButton
from ui.widgets.opacity_slider import OpacitySlider

ARROW_STYLE_OPTIONS = (
    ("箭头", "filled"), ("空心箭头", "open"), ("虚线箭头", "dashed"),
    ("双向箭头", "double_filled"), ("双向空心箭头", "double_open"),
    ("双向虚线箭头", "double_dashed"),
    ("箭杆矩形", "rect_filled"), ("空心箭杆", "rect_open"),
    ("虚线箭杆", "rect_dashed"), ("曲线箭头", "curve_filled"),
    ("曲线空心箭头", "curve_open"), ("曲线虚线箭头", "curve_open_dashed"),
    ("线段", "line"), ("虚线", "dashed_line"),
)

SEQUENCE_SHAPE_OPTIONS = (
    ("圆形", "circle"), ("方形", "square"), ("三角", "triangle"),
    ("菱形", "diamond"), ("五边形", "pentagon"), ("六边形", "hexagon"),
    ("星形", "star"), ("心形", "heart"), ("箭头", "arrow"),
    ("气泡", "bubble"), ("云", "cloud"), ("十字", "plus"), ("水滴", "drop"),
)


def _row(*widgets):
    widget = QWidget()
    layout = QHBoxLayout(widget)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(8)
    for child in widgets:
        if isinstance(child, str):
            layout.addWidget(QLabel(child))
        else:
            layout.addWidget(child)
    layout.addStretch(1)
    return widget


def _color(value):
    color = QColor(value)
    return color.name() if color.isValid() else "#ff0000"


def _brush_state(item):
    brush = item.brush()
    if brush.style() == Qt.NoBrush:
        return False, "#ff0000", 35
    color = brush.color()
    return True, color.name(), round(color.alpha() * 100 / 255)


def _pen_style(item):
    return "dash" if item.pen().style() == Qt.DashLine else "solid"


def _line_opacity(item):
    return round(item.pen().color().alpha() * 100 / 255)


class AnnotationPropertiesDialog(QDialog):
    """按图元类型动态生成参数控件。"""

    def __init__(self, parent, item):
        super().__init__(parent)
        self.item = item
        self.replacement = None
        self._replacement_pixmap = None
        self._replacement_path = None
        self.setWindowTitle("编辑标注参数")
        self.setMinimumWidth(360)
        # 原地编辑时画布挂在置顶遮罩下，普通对话框需要显式置顶才能看见。
        self.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.setSpacing(8)
        layout.addLayout(form)
        self.controls = {}
        if isinstance(item, (AnnotationRectItem, RoundedRectItem)):
            self._build_shape(form, corner=True)
        elif isinstance(item, AnnotationEllipseItem):
            self._build_shape(form, corner=False)
        elif isinstance(item, AnnotationPathItem):
            if getattr(item, "arrow_style", None) is not None:
                self._build_arrow(form)
            else:
                self._build_stroke(form, marker=item.pen().color().alpha() < 255)
        elif isinstance(item, AnnotationSequenceItem):
            self._build_sequence(form)
        elif isinstance(item, AnnotationZoomItem):
            self._build_zoom(form)
        elif isinstance(item, AnnotationPixmapItem):
            self._build_pixmap(form)
        self.locked_check = QCheckBox("锁定该标注（禁止拖动、缩放、旋转与删除）")
        self.locked_check.setChecked(annotation_locked(item))
        layout.addWidget(self.locked_check)
        # 初始值快照：确认时据此判断是否真的改动，避免“直接点确定”也产生一个撤销点。
        self._initial_values = {key: self._value(key) for key in self.controls}
        self._initial_locked = annotation_locked(item)
        self.modified = False
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("确定")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.raise_()
        self.activateWindow()

    # ---- 控件构建 -------------------------------------------------
    def _color_control(self, key, value, purpose):
        button = ColorButton(_color(value), lambda _value: None, compact=True,
                             purpose=purpose)
        self.controls[key] = button
        return button

    def _opacity(self, key, value, low=0, high=100):
        control = OpacitySlider(value, low, high)
        self.controls[key] = control
        return control

    def _spin(self, key, value, low, high, suffix=""):
        control = QSpinBox()
        control.setRange(low, high)
        control.setValue(int(value))
        if suffix:
            control.setSuffix(suffix)
        self.controls[key] = control
        return control

    def _build_stroke(self, form, marker=False):
        item = self.item
        form.addRow("线条颜色", self._color_control("color", item.pen().color(), "标注线条"))
        form.addRow("线宽", self._spin("width", item.pen().widthF(), 1, 50, " px"))
        if marker:
            form.addRow("不透明度", self._opacity("opacity", _line_opacity(item), 1, 100))

    def _build_shape(self, form, corner):
        item = self.item
        self._build_stroke(form)
        style = QComboBox()
        style.addItem("实线", "solid")
        style.addItem("虚线", "dash")
        style.setCurrentIndex(max(0, style.findData(_pen_style(item))))
        self.controls["style"] = style
        form.addRow("线型", style)
        if corner:
            radius = getattr(item, "corner_radius", 0)
            self.controls["corner"] = self._spin("corner", radius, 0, 100, " px")
            form.addRow("圆角", self.controls["corner"])
        filled, fill_color, fill_opacity = _brush_state(item)
        enabled = QCheckBox("填充区域")
        enabled.setChecked(filled)
        self.controls["fill_enabled"] = enabled
        form.addRow("填充", enabled)
        form.addRow("填充颜色", self._color_control("fill_color", fill_color, "填充颜色"))
        form.addRow("填充不透明度",
                    self._opacity("fill_opacity", fill_opacity))

    def _build_arrow(self, form):
        item = self.item
        self._build_stroke(form)
        style = QComboBox()
        for label, value in ARROW_STYLE_OPTIONS:
            style.addItem(label, value)
        style.setCurrentIndex(max(0, style.findData(getattr(item, "arrow_style", "filled"))))
        self.controls["arrow_style"] = style
        form.addRow("箭头样式", style)
        direction = QComboBox()
        direction.addItem("向右弯", "right")
        direction.addItem("向左弯", "left")
        direction.setCurrentIndex(max(0, direction.findData(
            getattr(item, "arrow_curve_direction", "right"))))
        self.controls["arrow_curve_direction"] = direction
        form.addRow("曲线弯曲方向", direction)

    def _build_sequence(self, form):
        item = self.item
        self.controls["number"] = self._spin("number", item.number, 0, 9999)
        form.addRow("序号", self.controls["number"])
        shape_box = QComboBox()
        for label, value in SEQUENCE_SHAPE_OPTIONS:
            shape_box.addItem(label, value)
        shape_box.setCurrentIndex(max(0, shape_box.findData(item.sequence_shape)))
        self.controls["sequence_shape"] = shape_box
        form.addRow("形状", shape_box)
        form.addRow("填充颜色", self._color_control(
            "sequence_fill_color", item.sequence_fill_color, "序号填充颜色"))
        form.addRow("文字颜色", self._color_control(
            "sequence_text_color", item.sequence_text_color, "序号文字颜色"))
        form.addRow("字号", self._spin(
            "sequence_font_size", item.sequence_font_size, 6, 200, " pt"))

    def _build_pixmap(self, form):
        item = self.item
        preview = QLabel()
        preview.setAlignment(Qt.AlignCenter)
        preview.setMinimumHeight(96)
        preview.setPixmap(item.pixmap().scaled(220, 120, Qt.KeepAspectRatio,
                                               Qt.SmoothTransformation))
        self._image_preview = preview
        form.addRow("当前图片", preview)
        position = QLabel(
            f"位置 ({item.pos().x():.0f}, {item.pos().y():.0f})"
            f" · 大小 {item.pixmap().width()}×{item.pixmap().height()}")
        form.addRow("位置/大小", position)
        self._image_path_label = QLabel(getattr(item, "image_source_path", "") or "（未记录来源）")
        self._image_path_label.setWordWrap(True)
        form.addRow("来源", self._image_path_label)
        replace = QPushButton("替换图片…")
        replace.setIcon(action_icon("image_replace"))
        replace.setToolTip("选择一张新图片替换当前图片，保留当前图片位置")
        replace.clicked.connect(self._choose_replacement)
        form.addRow("", replace)
        form.addRow("不透明度", self._opacity(
            "opacity", round(self.item.opacity() * 100)))

    def _choose_replacement(self):
        from core.image_io import IMAGE_FILE_FILTER

        path, _ = QFileDialog.getOpenFileName(self, "替换图片", "", IMAGE_FILE_FILTER)
        if not path:
            return
        pixmap = QPixmap(path)
        if pixmap.isNull():
            QMessageBox.warning(self, "替换图片失败", "无法读取所选图片")
            return
        old_size = self.item.pixmap().size()
        if old_size.isValid() and old_size.width() > 0 and old_size.height() > 0:
            pixmap = pixmap.scaled(old_size, Qt.KeepAspectRatio,
                                   Qt.SmoothTransformation)
        self._replacement_pixmap = pixmap
        self._replacement_path = path
        self._image_preview.setPixmap(pixmap.scaled(220, 120, Qt.KeepAspectRatio,
                                                   Qt.SmoothTransformation))
        self._image_path_label.setText(path)

    def _build_zoom(self, form):
        item = self.item
        form.addRow("放大倍数", self._spin("zoom_factor", item.zoom_factor, 1, 16, "×"))
        style = QComboBox()
        style.addItem("圆形", "circle")
        style.addItem("矩形", "rect")
        style.setCurrentIndex(max(0, style.findData(item.zoom_style)))
        self.controls["zoom_style"] = style
        form.addRow("镜片样式", style)
        form.addRow("颜色", self._color_control(
            "zoom_color", item.zoom_border_color, "放大标注颜色"))
        form.addRow("最大边长", self._spin(
            "zoom_max_size", item.zoom_max_size, 80, 1200, " px"))
        form.addRow("不透明度", self._opacity(
            "opacity", round(item.opacity() * 100)))

    # ---- 应用 -----------------------------------------------------
    def _value(self, key):
        control = self.controls.get(key)
        if isinstance(control, ColorButton):
            return control.color
        if isinstance(control, QComboBox):
            return control.currentData()
        if isinstance(control, QCheckBox):
            return control.isChecked()
        return control.value() if control is not None else None

    def apply(self):
        """把对话框里的值写入图元；返回替换后的图元（矩形圆角变化时可能换类）。"""
        item = self.item
        locked = self.locked_check.isChecked()
        if isinstance(item, (AnnotationRectItem, RoundedRectItem)):
            self._apply_shape(item, corner=True)
        elif isinstance(item, AnnotationEllipseItem):
            self._apply_shape(item, corner=False)
        elif isinstance(item, AnnotationPathItem):
            if "arrow_style" in self.controls:
                self._apply_arrow(item)
            else:
                self._apply_stroke(item, marker="opacity" in self.controls)
        elif isinstance(item, AnnotationSequenceItem):
            self._apply_sequence(item)
        elif isinstance(item, AnnotationZoomItem):
            self._apply_zoom(item)
        elif isinstance(item, AnnotationPixmapItem):
            if self._replacement_pixmap is not None:
                # 只换图，不移动：当前图片位置/层级/选中状态保持不变。
                item.setPixmap(self._replacement_pixmap)
                item.image_source_path = self._replacement_path or ""
            item.setOpacity(self._value("opacity") / 100)
        final_item = self.replacement or item
        # 圆角换类时 item 已被移出场景（replacement 才是场景里的图元）：锁定必须作用在最终图元上，
        # 否则“同时改圆角 + 勾选锁定”会作用到已移除的旧对象，新图元仍可拖动/删除。
        set_annotation_locked(final_item, locked)
        self.modified = (locked != self._initial_locked
                         or self._replacement_pixmap is not None
                         or any(self._value(key) != value
                                for key, value in self._initial_values.items()))
        return final_item

    def _apply_stroke(self, item, marker=False):
        color = QColor(self._value("color"))
        if marker:
            color.setAlpha(round(255 * self._value("opacity") / 100))
        pen = item.pen()
        pen.setColor(color)
        pen.setWidthF(float(self._value("width")))
        item.setPen(pen)

    def _apply_shape(self, item, corner):
        self._apply_stroke(item)
        pen = item.pen()
        pen.setStyle(Qt.DashLine if self._value("style") == "dash" else Qt.SolidLine)
        item.setPen(pen)
        if self._value("fill_enabled"):
            color = QColor(self._value("fill_color"))
            color.setAlpha(round(255 * self._value("fill_opacity") / 100))
            item.setBrush(color)
        else:
            item.setBrush(Qt.NoBrush)
        if corner:
            item = self._apply_corner(item, self._value("corner"))
            self.replacement = item

    def _apply_corner(self, item, radius):
        """圆角需要在 RoundedRectItem / AnnotationRectItem 之间切换类型。"""
        radius = max(0, int(radius or 0))
        is_rounded = isinstance(item, RoundedRectItem)
        if radius > 0 and not is_rounded:
            replacement = RoundedRectItem(item.rect(), radius)
        elif radius <= 0 and is_rounded:
            replacement = AnnotationRectItem(item.rect())
        else:
            if is_rounded:
                item.corner_radius = radius
            item.update()
            return item
        self._copy_state(item, replacement)
        scene = item.scene()
        if scene is not None:
            scene.removeItem(item)
            scene.addItem(replacement)
        if not annotation_locked(replacement):
            replacement.setSelected(True)
        return replacement

    @staticmethod
    def _copy_state(source, target):
        target.setPen(QPen(source.pen()))
        target.setBrush(source.brush())
        target.setPos(source.pos())
        target.setTransformOriginPoint(source.transformOriginPoint())
        target.setScale(source.scale())
        target.setTransform(QTransform(source.transform()))
        target.setRotation(source.rotation())
        target.setZValue(source.zValue())
        target.setOpacity(source.opacity())
        editable(target)
        target.locked = annotation_locked(source)
        if target.locked:
            set_annotation_locked(target, True)

    def _apply_arrow(self, item):
        color = self._value("color")
        width = float(self._value("width"))
        style = self._value("arrow_style")
        direction = self._value("arrow_curve_direction")
        rebuilt = shape("arrow", item.start, item.end, color, width, arrow_style=style,
                        arrow_curve_direction=direction)
        item.setPath(rebuilt.path())
        item.setPen(rebuilt.pen())
        item.setBrush(rebuilt.brush())
        item.arrow_style = style
        item.arrow_curve_direction = direction
        item.line_color = color
        item.line_width = width

    def _apply_zoom(self, item):
        item.set_zoom_max_size(int(self._value("zoom_max_size")))
        item.set_zoom_factor(int(self._value("zoom_factor")))
        item.set_zoom_style(self._value("zoom_style"))
        item.set_zoom_border_color(self._value("zoom_color"))
        item.set_zoom_opacity(int(self._value("opacity")))

    def _apply_sequence(self, item):
        item.prepareGeometryChange()
        item.number = int(self._value("number"))
        item.sequence_shape = self._value("sequence_shape")
        item.sequence_fill_color = self._value("sequence_fill_color")
        item.sequence_text_color = self._value("sequence_text_color")
        item.sequence_font_size = int(self._value("sequence_font_size"))
        item._radius = max(11, item.sequence_font_size + 2)
        item.update()


def edit_annotation_parameters(parent, item):
    """弹窗编辑单个标注参数。

    返回 (最终图元, 是否有改动)：圆角变化会换成另一种矩形类，调用方必须用返回的图元；
    取消或没有改动时返回 (None, False)，调用方据此决定是否记撤销点。
    """
    dialog = AnnotationPropertiesDialog(parent, item)
    if dialog.exec() != QDialog.Accepted:
        return None, False
    final_item = dialog.apply()
    return final_item, dialog.modified
