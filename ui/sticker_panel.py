"""贴图管理窗口。"""

from PySide6.QtCore import Qt, QSize, QPoint, QEvent, QItemSelection, QItemSelectionModel
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QListWidget,
                               QListWidgetItem, QLabel, QPushButton, QStyle,
                               QAbstractItemView, QComboBox, QInputDialog, QMessageBox,
                               QFileDialog, QDialog, QFormLayout, QCheckBox,
                               QDoubleSpinBox, QButtonGroup,
                               QRadioButton, QSpinBox, QGroupBox)
from PySide6.QtGui import QImage
from ui.action_icons import action_icon


class StickerRow(QWidget):
    """管理窗口中的一行：缩略图、说明和常用操作。"""

    def __init__(self, sticker, panel):
        super().__init__()
        self.sticker = sticker
        self.panel = panel
        self.selection_targets = [self]
        self.installEventFilter(self)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        width = panel.thumb_width()
        height = width * 3 // 4
        thumb = QLabel()
        thumb.setFixedSize(QSize(width, height))
        thumb.setAlignment(Qt.AlignCenter)
        thumb.setPixmap(sticker.thumbnail(width, height))
        self.selection_targets.append(thumb)
        thumb.installEventFilter(self)
        layout.addWidget(thumb)
        size = sticker.pixmap.size()
        info = QLabel(f"{sticker.describe()}\n{size.width()}×{size.height()} · ({sticker.x()}, {sticker.y()})")
        info.setWordWrap(True)
        self.selection_targets.append(info)
        info.installEventFilter(self)
        layout.addWidget(info, 1)
        for label, icon, callback in [("定位", QStyle.SP_ArrowRight, self.locate),
                                      ("显示" if not sticker.isVisible() else "隐藏",
                                       QStyle.SP_TitleBarMinButton, self.toggle),
                                      ("关闭", QStyle.SP_TitleBarCloseButton, self.close_sticker)]:
            button = QPushButton(label)
            button.setIcon(action_icon("locate") if label == "定位" else
                           action_icon("hidden" if label == "隐藏" else "visibility")
                           if label in ("隐藏", "显示") else self.style().standardIcon(icon))
            button.setFixedWidth(72)
            button.clicked.connect(callback)
            layout.addWidget(button)

    def eventFilter(self, watched, event):
        if (watched in self.selection_targets and
                event.type() == QEvent.MouseButtonPress and
                event.button() == Qt.LeftButton):
            self.panel.select_sticker(self.sticker, event.modifiers())
            return True
        return super().eventFilter(watched, event)

    def locate(self):
        self.panel.locate(self.panel.index_of(self.sticker))

    def toggle(self):
        self.sticker.setVisible(not self.sticker.isVisible())
        self.panel.refresh()

    def close_sticker(self):
        self.sticker.close()


class GroupSettingsDialog(QDialog):
    """为组成员设置统一属性值；复选框是目标值而非状态切换。"""

    def __init__(self, parent, manager, group_name):
        super().__init__(parent)
        self.manager = manager
        self.group_name = group_name
        members = [item for item in manager.items if item.group_name == group_name]
        self.setWindowTitle(f"设置贴图组：{group_name}")
        self.resize(400, 430)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        layout.addLayout(form)

        self.mixed_state_help = QLabel("设置修改后立即应用到组内贴图。")
        self.mixed_state_help.setWordWrap(True)
        layout.addWidget(self.mixed_state_help)

        self.opacity = QDoubleSpinBox()
        self.opacity.setRange(0.1, 1.0)
        self.opacity.setSingleStep(0.05)
        self.opacity.setDecimals(2)
        self.opacity.setSuffix(" 不透明度")
        if members:
            self.opacity.setValue(sum(item.windowOpacity() for item in members) / len(members))
        form.addRow("透明度", self.opacity)

        self.visibility = QComboBox()
        self.visibility.addItem("保持各成员当前显隐", None)
        self.visibility.addItem("统一显示", True)
        self.visibility.addItem("统一隐藏", False)
        form.addRow("组内贴图显隐", self.visibility)

        self.checks = {}
        for label, key, attribute in (
                ("锁定", "locked", "locked"),
                ("置顶", "always_on_top", "always_on_top"),
                ("描边", "border_enabled", "border_enabled"),
                ("阴影", "shadow_enabled", "shadow_enabled"),
                ("点击穿透", "click_through", "click_through")):
            choice = QComboBox()
            choice.addItem("保持各成员当前值", None)
            choice.addItem("统一开启", True)
            choice.addItem("统一关闭", False)
            self.checks[key] = choice
            form.addRow(label, choice)

        self.reset_size = QCheckBox("重置所有成员大小")
        form.addRow(self.reset_size)
        rotation_box = QGroupBox("旋转角度")
        rotation_layout = QVBoxLayout(rotation_box)
        self.rotation_buttons = QButtonGroup(self)
        for label, degrees in (("逆时针 90°", -90), ("180°", 180),
                               ("顺时针 90°", 90)):
            radio = QRadioButton(label)
            radio.setObjectName(f"rotationPreset{degrees}")
            radio.toggled.connect(
                lambda checked, angle=degrees: self.rotate_group(angle) if checked else None)
            self.rotation_buttons.addButton(radio)
            rotation_layout.addWidget(radio)
        custom_row = QHBoxLayout()
        self.custom_rotation_radio = QRadioButton("自定义角度")
        self.custom_rotation_radio.setObjectName("customRotationRadio")
        self.rotation_buttons.addButton(self.custom_rotation_radio)
        self.custom_rotation = QSpinBox()
        self.custom_rotation.setObjectName("customRotationDegrees")
        self.custom_rotation.setRange(-3600, 3600)
        self.custom_rotation.setSuffix("°")
        self.custom_rotation.setToolTip("输入旋转角度，正数顺时针，负数逆时针")
        custom_row.addWidget(self.custom_rotation_radio)
        custom_row.addWidget(self.custom_rotation, 1)
        rotation_layout.addLayout(custom_row)
        self.reset_rotation_button = QPushButton("恢复原方向")
        self.reset_rotation_button.clicked.connect(
            lambda: self.manager.set_group_properties(
                self.group_name, {"rotation": "reset"}))
        rotation_layout.addWidget(self.reset_rotation_button)
        layout.addWidget(rotation_box)
        self.custom_rotation.editingFinished.connect(self.apply_custom_rotation)

        self.opacity.valueChanged.connect(
            lambda value: self.manager.set_group_properties(
                self.group_name, {"opacity": value}))
        self.visibility.currentIndexChanged.connect(self.apply_visibility)
        for key, choice in self.checks.items():
            choice.currentIndexChanged.connect(
                lambda _, name=key, widget=choice: self.apply_group_property(
                    name, widget.currentData()))
        self.reset_size.toggled.connect(
            lambda checked: self.apply_group_property("reset_size", True) if checked else None)

    def apply_group_property(self, key, value):
        if value is not None:
            self.manager.set_group_properties(self.group_name, {key: value})

    def apply_visibility(self, _index):
        visible = self.visibility.currentData()
        if visible is not None:
            self.manager.set_group_visible(self.group_name, visible)

    def rotate_group(self, degrees):
        self.manager.set_group_properties(self.group_name, {"rotation": degrees})

    def apply_custom_rotation(self):
        if not self.custom_rotation_radio.isChecked():
            self.custom_rotation_radio.setChecked(True)
        degrees = self.custom_rotation.value()
        if degrees:
            self.rotate_group(degrees)


class StickerPanel(QWidget):
    """列出当前所有贴图，支持定位、隐藏与关闭。"""

    def __init__(self, manager):
        super().__init__()
        self.manager = manager
        self.setWindowTitle("ScreenSnap · 贴图管理")
        self.resize(760, 500)
        layout = QVBoxLayout(self)
        summary_row = QHBoxLayout()
        self.summary = QLabel("共 0 张贴图")
        summary_row.addWidget(self.summary)
        summary_row.addStretch()
        self.group_combo = QComboBox()
        self.group_combo.setMinimumWidth(120)
        summary_row.addWidget(self.group_combo)
        layout.addLayout(summary_row)
        self.group_combo.currentIndexChanged.connect(
            self.update_batch_settings_button)
        group_buttons = QHBoxLayout()
        for label, icon, method in [
                ("归组", QStyle.SP_DirIcon, self.assign_selected_group),
                ("新建组", QStyle.SP_FileDialogNewFolder, self.create_group),
                ("从文件添加", QStyle.SP_DialogOpenButton, self.add_files_to_group),
                ("移动组", QStyle.SP_ArrowRight, self.move_group),
                ("重命名", QStyle.SP_FileDialogDetailedView, self.rename_group),
                ("删除组", QStyle.SP_TrashIcon, self.delete_group),
                ("显隐组", QStyle.SP_TitleBarShadeButton, self.toggle_group),
                ("关闭组", QStyle.SP_DialogCloseButton, self.close_group)]:
            self.add_button(group_buttons, label, icon, method)
        layout.addLayout(group_buttons)
        selection_buttons = QHBoxLayout()
        for label, icon, method in [("关闭所选", QStyle.SP_DialogCloseButton,
                                     self.manager.close_selected),
                                    ("隐藏/显示所选", QStyle.SP_DesktopIcon,
                                     self.toggle_selected_visibility),
                                    ("全部隐藏/显示", QStyle.SP_DesktopIcon,
                                     self.manager.toggle_hidden),
                                    ("刷新", QStyle.SP_BrowserReload, self.refresh),
                                    ("批量设置", QStyle.SP_FileDialogContentsView,
                                     self.edit_group),
                                    ("关闭全部", QStyle.SP_DialogCloseButton,
                                     self.manager.close_all)]:
            button = self.add_button(selection_buttons, label, icon, method)
            if label == "批量设置":
                self.batch_settings_button = button
                self.batch_settings_button.setObjectName("batchGroupSettingsButton")
                font = button.font()
                font.setBold(True)
                button.setFont(font)
                button.setToolTip("统一设置当前贴图组的成员属性")
        layout.addLayout(selection_buttons)
        self.list_widget = QListWidget()
        self.list_widget.setSpacing(2)
        self.list_widget.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.list_widget.itemSelectionChanged.connect(self.sync_manager_selection)
        self.list_widget.itemDoubleClicked.connect(lambda item: self.locate(item.data(Qt.UserRole)))
        layout.addWidget(self.list_widget, 1)
        self.stickers = []
        self._selection_anchor = None
        self.manager.changed.connect(self.refresh_if_visible)
        self.refresh()

    def refresh_if_visible(self):
        """只在窗口打开时刷新，避免后台频繁重建列表。"""
        if self.isVisible():
            self.refresh()

    def thumb_width(self):
        """缩略图宽度取自贴图设置，高度按 4:3 推导。"""
        return int((self.manager.settings or {}).get("sticker_panel_thumb", 96))

    def refresh(self):
        """重建列表内容；贴图数量变化时同步标题统计。"""
        self.stickers = list(self.manager.items)
        selected = set(self.manager.selected_items).intersection(self.stickers)
        self.list_widget.blockSignals(True)
        self.list_widget.clear()
        for index, sticker in enumerate(self.stickers):
            item = QListWidgetItem(self.list_widget)
            # 只保存序号，避免贴图被销毁后 Qt 变体仍持有窗口指针。
            item.setData(Qt.UserRole, index)
            row = StickerRow(sticker, self)
            item.setSizeHint(row.sizeHint())
            self.list_widget.addItem(item)
            self.list_widget.setItemWidget(item, row)
            if sticker in selected:
                item.setSelected(True)
        self.list_widget.blockSignals(False)
        self.manager.set_selected_items(selected)
        self.group_combo.blockSignals(True)
        current = self.group_combo.currentData()
        self.group_combo.clear()
        self.group_combo.addItem("未分组", "")
        for name in self.manager.group_names():
            self.group_combo.addItem(name, name)
        self.group_combo.setCurrentIndex(max(0, self.group_combo.findData(current)))
        self.group_combo.blockSignals(False)
        self.update_batch_settings_button()
        self.summary.setText(f"共 {len(self.stickers)} 张贴图 · 已选 {len(self.manager.selected_items)} 张")

    def update_batch_settings_button(self, *_):
        self.batch_settings_button.setEnabled(bool(self.group_combo.currentData()))

    def add_button(self, layout, label, icon, callback):
        button = QPushButton(label)
        custom_icons = {"归组": "group_move", "新建组": "group_add", "重命名": "rename",
                "显隐组": "visibility", "隐藏/显示": "visibility",
                "隐藏/显示所选": "visibility"}
        button.setIcon(action_icon(custom_icons[label]) if label in custom_icons else
                       self.style().standardIcon(icon))
        button.clicked.connect(callback)
        layout.addWidget(button)
        return button

    def sync_manager_selection(self):
        selected = [self.stickers[item.data(Qt.UserRole)]
                    for item in self.list_widget.selectedItems()
                    if 0 <= item.data(Qt.UserRole) < len(self.stickers)]
        self.manager.set_selected_items(selected)
        self.summary.setText(f"共 {len(self.stickers)} 张贴图 · 已选 {len(selected)} 张")

    def select_sticker(self, sticker, modifiers):
        """将自绘行内的鼠标点击转换成列表选择，支持 Ctrl 切换与 Shift 范围。"""
        try:
            row = self.stickers.index(sticker)
        except ValueError:
            return
        model = self.list_widget.model()
        selection_model = self.list_widget.selectionModel()
        index = model.index(row, 0)
        if modifiers & Qt.ShiftModifier:
            anchor = self._selection_anchor
            if anchor is None or not 0 <= anchor < len(self.stickers):
                anchor = self.list_widget.currentRow()
            if anchor < 0:
                anchor = row
            selection = QItemSelection(model.index(min(anchor, row), 0),
                                       model.index(max(anchor, row), 0))
            selection_model.select(
                selection,
                QItemSelectionModel.ClearAndSelect | QItemSelectionModel.Rows,
            )
            selection_model.setCurrentIndex(index, QItemSelectionModel.NoUpdate)
        elif modifiers & Qt.ControlModifier:
            selection_model.select(
                index, QItemSelectionModel.Toggle | QItemSelectionModel.Rows,
            )
            selection_model.setCurrentIndex(index, QItemSelectionModel.NoUpdate)
        else:
            selection_model.select(
                index, QItemSelectionModel.ClearAndSelect | QItemSelectionModel.Rows,
            )
            selection_model.setCurrentIndex(index, QItemSelectionModel.NoUpdate)
            self._selection_anchor = row

    def selected_stickers(self):
        return [self.stickers[item.data(Qt.UserRole)] for item in self.list_widget.selectedItems()
                if 0 <= item.data(Qt.UserRole) < len(self.stickers)]

    def assign_selected_group(self):
        selected = self.selected_stickers()
        self.manager.assign_group(selected, self.group_combo.currentData())
        self.refresh()

    def add_files_to_group(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "从文件添加贴图", "",
            "图片文件 (*.png *.jpg *.jpeg *.bmp *.gif *.webp *.tif *.tiff)",
        )
        if not paths:
            return 0
        group_name = self.group_combo.currentData() or ""
        added = []
        failed = []
        for path in paths:
            image = QImage(path)
            if image.isNull():
                failed.append(path)
                continue
            added.append(self.manager.add(image, path))
        if group_name and added:
            self.manager.assign_group(added, group_name)
        if failed:
            QMessageBox.warning(self, "部分图片无法打开", "以下图片无法读取：\n" + "\n".join(failed))
        self.refresh()
        return len(added)

    def create_group(self):
        name, accepted = QInputDialog.getText(self, "新建贴图组", "组名称")
        if not accepted or not name.strip():
            return
        existing = self.manager.group_names()
        if name.strip() in existing:
            QMessageBox.information(self, "贴图组", "该名称已存在")
            return
        selected = self.selected_stickers()
        if not selected:
            QMessageBox.information(self, "贴图组", "请先多选要加入新组的贴图")
            return
        self.manager.assign_group(selected, name)
        self.refresh()

    def rename_group(self):
        old_name = self.group_combo.currentData()
        if not old_name:
            return
        name, accepted = QInputDialog.getText(self, "重命名贴图组", "新名称", text=old_name)
        if accepted and name.strip() and name.strip() != old_name:
            self.manager.assign_group([item for item in self.manager.items if item.group_name == old_name], name)
            self.refresh()

    def edit_group(self):
        name = self.group_combo.currentData()
        if not name:
            return
        dialog = GroupSettingsDialog(self, self.manager, name)
        if dialog.exec():
            self.refresh()

    def delete_group(self):
        name = self.group_combo.currentData()
        if name:
            self.manager.assign_group([item for item in self.manager.items if item.group_name == name], "")
            self.refresh()

    def close_group(self):
        name = self.group_combo.currentData()
        if name:
            closed = self.manager.close_group(name)
            self.refresh()
            return closed
        return 0

    def move_group(self):
        name = self.group_combo.currentData()
        if not name:
            return 0
        delta_x, accepted = QInputDialog.getInt(
            self, f"移动贴图组：{name}", "水平位移（像素，正数向右）", 0, -10000, 10000)
        if not accepted:
            return 0
        delta_y, accepted = QInputDialog.getInt(
            self, f"移动贴图组：{name}", "垂直位移（像素，正数向下）", 0, -10000, 10000)
        if not accepted:
            return 0
        return self.manager.move_group(name, QPoint(delta_x, delta_y))

    def toggle_group(self):
        name = self.group_combo.currentData()
        members = [item for item in self.manager.items if item.group_name == name] if name else []
        if members:
            self.manager.set_group_visible(name, any(not item.isVisible() for item in members))
            self.refresh()

    def toggle_selected_visibility(self):
        self.manager.toggle_selected_visibility()
        self.refresh()

    def index_of(self, sticker):
        """返回贴图在当前列表中的位置，已关闭时返回 None。"""
        for index, current in enumerate(self.stickers):
            if current is sticker:
                return index
        return None

    def locate(self, index):
        """把指定贴图带到前台，隐藏状态下先恢复显示。"""
        if not isinstance(index, int) or not 0 <= index < len(self.stickers):
            return
        sticker = self.stickers[index]
        if not sticker.isVisible():
            sticker.setVisible(True)
        sticker.show()
        sticker.raise_()
        sticker.activateWindow()
