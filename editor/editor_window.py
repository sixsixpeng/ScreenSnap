"""截图编辑窗口及导出操作。"""

import os
import re
import logging
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, Signal, QSignalBlocker, QMimeData
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (QMainWindow, QWidget, QVBoxLayout, QFileDialog,
                               QDialog, QDialogButtonBox, QDial, QDoubleSpinBox, QHBoxLayout, QLabel,
                               QStyle, QSlider, QSpinBox, QGraphicsView)

from core.image_io import (matches_saved_format, normalize_save_as_path, save_as_filter,
                           save_image, saved_extension)
from core.path_utils import resolved_dir, save_as_directory
from config.config_manager import TOOL_WIDTH_KEYS, fill_colors_following_stroke
from editor.annotation_canvas import AnnotationCanvas
from editor.image_effects import apply_output_effects, copy_saved_to_clipboard
from core.screen_capture import qimage_to_pillow
from editor.image_transform import transform
from editor.toolbar_widget import ToolbarWidget

# 2026-10-11：这个模块级助手**必须放在 class 之前** —— 上一版误插进类体内（列 0）⇒ 类提前结束、
# 之后所有方法都变成它的嵌套函数 ⇒ AttributeError（test_tools/test_zoom/test_app 共 9 条 ERROR）。
_CHINESE_DIALOGS_READY = [False]


def _ensure_chinese_dialogs():
    """让 Qt **自绘**对话框显示中文（2026-10-11 用户要求：重绘的界面注意汉化）。

    · 装载 Qt 自带的中文翻译（qtbase_zh_CN / qt_zh_CN）⇒ 标准按钮与标签变中文；
    · 只做一次；把 QTranslator 挂在 app 上保住引用，避免被 GC 掉；
    · 找不到翻译文件也不报错 —— 调用方还会显式 setLabelText 兜底。
    """
    if _CHINESE_DIALOGS_READY[0]:
        return
    _CHINESE_DIALOGS_READY[0] = True
    from PySide6.QtCore import QLibraryInfo, QTranslator
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        return
    path = QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)
    translator = QTranslator(app)
    if translator.load("qtbase_zh_CN", path) or translator.load("qt_zh_CN", path):
        app.installTranslator(translator)
        app._screensnap_translator = translator
        logging.getLogger("screensnap").debug("已装载 Qt 中文翻译: %s", path)


def save_as_sidebar_urls():
    """「另存为」自绘对话框的侧栏：计算机 + 桌面 + 用户文件夹 + 常用目录 + **所有磁盘**。

    2026-10-11 用户要求：原生对话框侧栏只有"我的电脑 / 用户文件夹"太窄，希望有桌面与所有磁盘。
    只在 **自绘**（多选另存为）路径使用；单选路径仍是 Windows 原生对话框（外观不变 ✓）。
    """
    from PySide6.QtCore import QStandardPaths, QUrl
    from PySide6.QtCore import QDir

    urls = [QUrl("file:")]                       # 「计算机」（= 我的电脑）
    for location in (QStandardPaths.DesktopLocation, QStandardPaths.HomeLocation,
                     QStandardPaths.DocumentsLocation, QStandardPaths.DownloadLocation,
                     QStandardPaths.PicturesLocation):
        path = QStandardPaths.writableLocation(location)
        if path:
            urls.append(QUrl.fromLocalFile(path))
    for drive in QDir.drives():                  # 所有磁盘（C:/ D:/ E:/ …）
        path = drive.absoluteFilePath()
        if path:
            urls.append(QUrl.fromLocalFile(path))
    deduped = []
    seen = set()
    for url in urls:
        key = url.toString()
        if key not in seen:
            seen.add(key)
            deduped.append(url)
    return deduped


ANNOTATION_COLOR_TOOLS = frozenset(("pen", "rect", "ellipse", "arrow", "marker", "text"))


class EditorWindow(QMainWindow):
    """将工具栏命令接入画布，并统一处理保存、剪贴板与贴图输出。"""

    image_saved = Signal(str, object)
    sticker_requested = Signal(object)
    recapture_requested = Signal()
    close_all_requested = Signal()
    status = Signal(str)
    copy_done = Signal(object)
    save_as_dir_chosen = Signal(str)
    pen_color_changed = Signal(str)
    tool_color_changed = Signal(str, str)
    setting_changed = Signal(str, object)

    def __init__(self, image, settings, alternate=None, from_capture=False):
        super().__init__()
        self.settings = settings
        self.from_capture = from_capture
        self.round_corners = bool(settings.get("editor_image_round_corners", True))
        self.last_path = None
        self.setWindowTitle("截图编辑器")
        self.resize(1200, 760)
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(8, 6, 8, 8)
        layout.setSpacing(4)
        self.toolbar = ToolbarWidget(settings["pen_color"], settings,
                         show_capture_actions=False)
        self.canvas = AnnotationCanvas(image, settings, alternate)
        # D8 ④（2026-10-11）：画布会吞掉 Space（拖动画布用于它）⇒ 画布侧那份快捷键转成同一条贴图命令。
        self.canvas.sticker_shortcut.connect(lambda: self.toolbar.command.emit("paste"))
        # 2026-10-11：单键 A = 另存为（画布内处理，避免无修饰可打印键被 ShortcutOverride 吞掉）。
        self.canvas.save_as_requested.connect(lambda: self.toolbar.command.emit("save_as"))
        self.canvas.save_requested.connect(lambda: self.toolbar.command.emit("save"))
        # 2026-10-11 用户要求：独立编辑器补上「参考单图快速编辑」的快捷键（保存 / 仅复制 / 另存为）。
        # 全部走工具栏同一条命令，避免出现第二套实现（规则 12）。
        from PySide6.QtGui import QKeySequence, QShortcut

        registered = []
        for key, action, label in (
                # 2026-10-11：S / Ctrl+S / A **不**在这里注册 —— 窗口级 QShortcut 会抢在画布之前，
                # 用户按住**旋转手柄**或标注手柄微调时被误触发成"保存/另存为"。它们改由画布
                # keyPressEvent 在 W/A/S/D 微调分支**之后**处理 ⇒ 微调语境自动让位（与遮罩侧一致）。
                (str(settings.get("capture_copy_shortcut", "Y")), "copy_only", "仅复制"),
                (str(settings.get("editor_undo_shortcut", "Ctrl+Z")), "undo", "撤销"),
                (str(settings.get("editor_redo_shortcut", "Ctrl+Y")), "redo", "重做")):
            # 2026-10-11：**不**在这里绑定 reset —— 工具栏的 reset 是「重置当前标注参数」（不是复位缩放），
            # 用户实测按 Ctrl+0 会把标注参数/选中清掉。Ctrl+0 现由画布 keyPressEvent 直接 set_zoom(100)。
            sequence = QKeySequence(key)
            if sequence.isEmpty():
                continue
            shortcut = QShortcut(sequence, self)
            shortcut.setContext(Qt.WindowShortcut)
            shortcut.activated.connect(lambda name=action: self.toolbar.command.emit(name))
            registered.append(f"{key} ⇒ {label}")   # 循环内不记日志（规则 14：多选会连开 N 个编辑器）
        # 2026-10-11（用户澄清语义）：「复位缩放」= **画布**缩放回 100%，**不是**工具栏的「重置」
        # （那个是"重置当前标注参数" ✗，上一版误绑它导致按 Ctrl+0 把标注参数清掉）。
        # 画布缩放只有独立编辑器有（原地编辑器 wheel_zoom_enabled=False）⇒ 这里才注册；
        # Ctrl+0 没有双重语义，用窗口级快捷键是安全的（D14）；画布 keyPressEvent 里那条分支保留做双保险。
        zoom_sequence = QKeySequence(str(settings.get("editor_zoom_reset_shortcut", "Ctrl+0")))
        if not zoom_sequence.isEmpty():
            zoom_reset = QShortcut(zoom_sequence, self)
            zoom_reset.setContext(Qt.WindowShortcut)
            zoom_reset.activated.connect(lambda: self.canvas.set_zoom(100))
            registered.append(f"{zoom_sequence.toString()} ⇒ 画布缩放复位")

        if registered:
            logging.getLogger("screensnap").debug(
                "编辑器快捷键已注册（%d 个）：%s", len(registered), "；".join(registered))
        # 窗口编辑器支持 Ctrl+滚轮缩放（原地编辑画布太小、没有缩放控件，不启用）。
        self.canvas.wheel_zoom_enabled = True
        self.canvas.set_round_corner_preview(
            self.round_corners, settings.get("editor_image_corner_radius", 16))
        self.canvas.set_tool(next((key for key, button in self.toolbar.tool_buttons.items()
                       if button.isChecked()), "select"))
        self.last_color_tool = self.canvas.tool if self.canvas.tool in ANNOTATION_COLOR_TOOLS else "pen"
        self.toolbar.set_active_tool(self.canvas.tool, self.last_color_tool)
        self.canvas.setDragMode(QGraphicsView.RubberBandDrag if self.canvas.tool == "select"
                    else QGraphicsView.NoDrag)
        self.canvas.text_alignment = {"left": Qt.AlignLeft, "center": Qt.AlignHCenter,
                                      "right": Qt.AlignRight}.get(settings.get("text_alignment"), Qt.AlignLeft)
        self.toolbar.cursor_switch.setChecked(settings["cursor"])
        self.toolbar.cursor_switch.setEnabled(alternate is not None)
        self.toolbar.cursor_switch.toggled.connect(self.canvas.toggle_cursor)
        self.toolbar.cursor_switch.toggled.connect(
            lambda enabled: self.set_annotation_setting("cursor", enabled))
        self.canvas.changed.connect(self.update_cursor_switch)
        self.canvas.changed.connect(self.invalidate_rotation_reset)
        layout.addWidget(self.toolbar)
        layout.addWidget(self.canvas, 1)
        self.operation_tips = QLabel(
            "拖动绘制标注 | 选择工具可快速选中、移动或调整标注 | "
            "双击空白处保存并退出 | 滚轮上下滚动，Ctrl+滚轮缩放，Alt+滚轮横向移动 | "
            "用滑块或数值调整缩放 | 右键/中键拖动或空格拖动平移 | Esc 放弃编辑"
        )
        self.operation_tips.setObjectName("editorOperationTips")
        self.operation_tips.setWordWrap(True)
        layout.addWidget(self.operation_tips)
        zoom_row = QHBoxLayout()
        zoom_row.addWidget(QLabel("缩放"))
        self.zoom_slider = QSlider(Qt.Horizontal)
        self.zoom_slider.setRange(1, 800)
        self.zoom_slider.setValue(100)
        self.zoom_slider.setToolTip("图片和标注的显示缩放比例")
        zoom_row.addWidget(self.zoom_slider, 1)
        self.zoom_input = QSpinBox()
        self.zoom_input.setRange(1, 800)
        self.zoom_input.setValue(100)
        self.zoom_input.setSuffix("%")
        self.zoom_input.setToolTip("精确输入显示缩放比例")
        zoom_row.addWidget(self.zoom_input)
        layout.addLayout(zoom_row)
        self.zoom_slider.valueChanged.connect(self.canvas.set_zoom)
        self.zoom_input.valueChanged.connect(self.canvas.set_zoom)
        self.canvas.zoom_changed.connect(self.zoom_slider.setValue)
        self.canvas.zoom_changed.connect(self.zoom_input.setValue)
        self.canvas.selection_requested.connect(self.toolbar.tool_buttons["select"].click)
        self.canvas.selected_annotation_changed.connect(self._on_selected_annotation_changed)
        self.toolbar.options_button.menu().aboutToShow.connect(
            lambda: self.toolbar.set_selected_tool(self.canvas.selected_annotation_tool()))
        self.canvas.setting_changed.connect(self.apply_canvas_setting)
        self.setCentralWidget(container)
        self.toolbar.tool_changed.connect(self.set_tool)
        self.toolbar.color_changed.connect(self.set_pen_color)
        self.toolbar.setting_changed.connect(self.set_annotation_setting)
        self.toolbar.crop_style_changed.connect(self.set_crop_style)
        self.toolbar.command.connect(self.execute)
        # 不再把 Esc 绑定为“退出编辑”：取色、字体等模态对话框也用 Esc 关闭，
        # 窗口级快捷键会在对话框打开时一并触发，导致关掉对话框的同时直接退出编辑。
        self.canvas.confirmed.connect(lambda: self.execute("save"))
        # 不再把画布上的 Esc 绑定为“退出编辑”：取色、字体等模态框打开时焦点可能落在画布，
        # Esc 会被画布接收并直接关掉整个编辑窗口。改为仅取消当前选中，不关闭窗口。
        self.canvas.cancelled.connect(lambda: self.canvas.scene_data.clearSelection())
        self.canvas.color_picked.connect(self.apply_picked_color)

    def set_tool(self, tool):
        """将工具栏的标注工具切换同步到画布。"""
        if tool in ANNOTATION_COLOR_TOOLS:
            self.last_color_tool = tool
        self.canvas.set_tool(tool)
        self.canvas.selection_area = None
        self.canvas.selection_start = None
        self.canvas.selection_end = None
        self.canvas.setDragMode(QGraphicsView.RubberBandDrag if tool == "select" else QGraphicsView.NoDrag)
        self.canvas.eraser_point = None
        self.canvas.viewport().update()
        self.toolbar.set_active_tool(tool, self.last_color_tool)
        self.set_annotation_setting("annotation_tool", tool)

    def set_pen_color(self, color):
        """更新当前标注工具颜色，并同步已选图元与设置页。"""
        tool = self.canvas.tool if self.canvas.tool in ANNOTATION_COLOR_TOOLS else self.last_color_tool
        key = "pen_color" if tool == "pen" else f"{tool}_color"
        self.settings[key] = color
        self.toolbar.sync_tool_color(key, color)
        self.canvas.settings[key] = color
        self.canvas.set_selected_color(color)
        self.canvas.refresh_tool_cursor()
        self.canvas.update()
        self.tool_color_changed.emit(tool, color)
        if tool == "pen":
            self.pen_color_changed.emit(color)

    def apply_picked_color(self, color):
        """将取样色写入吸管前最近使用的可调色工具，并复制色值。"""
        self.set_pen_color(color)
        self.toolbar.set_active_tool(self.canvas.tool, self.last_color_tool)
        self.toolbar.pen_color.set_color(color)
        QGuiApplication.clipboard().setText(color)

    def apply_canvas_setting(self, key, value):
        """画布内入口（如文字输入对话框）改配置：写入配置并同步工具栏控件。"""
        self.set_annotation_setting(key, value)
        self.toolbar.sync_setting(key, value)

    def set_annotation_setting(self, key, value):
        """保存当前工具参数，并更新适用的已选标注。"""
        # 填充色默认与线色一致：仍等于旧线色时跟随变化，用户自定义过则保留。
        for fill_key, fill_value in fill_colors_following_stroke(self.settings, key, value).items():
            self.settings[fill_key] = fill_value
            self.canvas.settings[fill_key] = fill_value
            self.toolbar.sync_setting(fill_key, fill_value)
        self.settings[key] = value
        self.canvas.settings[key] = value
        if key in ("rect_fill_enabled", "rect_fill_opacity", "rect_fill_color",
                   "ellipse_fill_enabled", "ellipse_fill_opacity", "ellipse_fill_color",
                   "mosaic_cursor_color", "eraser_cursor_color", "mosaic_width",
                   "eraser_width", "mosaic_brush"):
            self.canvas.refresh_tool_cursor()
            self.canvas.viewport().update()
        if key == "editor_transparent_background":
            self.canvas.refresh_transparency_preview()
        if key in ("editor_toolbar_shadow_enabled", "editor_toolbar_shadow_color",
                   "editor_toolbar_shadow_strength"):
            self.toolbar.apply_toolbar_shadow()
        if key in self.toolbar.tool_color_buttons:
            self.toolbar.sync_tool_color(key, value)
            tool = (self.canvas.selected_annotation_tool() if self.canvas.tool == "select"
                    else self.canvas.tool)
            if key == f"{tool}_color":
                self.canvas.set_selected_color(value)
                self.canvas.update()
            # 当前工具光标颜色跟随该工具的取色项变化即时刷新。
            self.canvas.refresh_tool_cursor()
        if key == "editor_image_round_corners":
            self.round_corners = value
        if key in ("editor_image_round_corners", "editor_image_corner_radius"):
            self.canvas.set_round_corner_preview(
                self.round_corners, self.settings.get("editor_image_corner_radius", 16))
        if key in TOOL_WIDTH_KEYS.values():
            self.canvas.set_selected_width(value)
        elif key in ("rect_style", "ellipse_style"):
            self.canvas.set_selected_line_style(value)
        elif key == "arrow_style":
            self.canvas.set_selected_arrow_style(value)
        elif key in ("text_bold", "text_italic", "text_underline", "text_strikethrough"):
            self.canvas.set_selected_text_format()
        elif key in ("rect_fill_enabled", "rect_fill_opacity", "rect_fill_color"):
            self.canvas.set_selected_fill(
                "rect", self.settings.get("rect_fill_enabled", False),
                self.settings.get("rect_fill_opacity", 35),
                self.settings.get("rect_fill_color"))
        elif key in ("ellipse_fill_enabled", "ellipse_fill_opacity", "ellipse_fill_color"):
            self.canvas.set_selected_fill(
                "ellipse", self.settings.get("ellipse_fill_enabled", False),
                self.settings.get("ellipse_fill_opacity", 35),
                self.settings.get("ellipse_fill_color"))
        elif key == "font":
            self.canvas.set_selected_font(value)
        elif key == "font_size":
            self.canvas.set_selected_font_size(value)
        elif key == "text_width":
            self.canvas.set_selected_text_width(value)
        elif key == "text_height":
            self.canvas.set_selected_text_height(value)
        elif key == "text_alignment":
            self.canvas.text_alignment = {"left": Qt.AlignLeft, "center": Qt.AlignHCenter,
                                          "right": Qt.AlignRight}[value]
        self.canvas.update()
        self.setting_changed.emit(key, value)

    def _on_selected_annotation_changed(self, tool):
        """选中标注类型变化时，让工具栏「更多设置」展示该类型专属参数。"""
        self.toolbar.set_selected_tool(tool or None)

    def set_crop_style(self, key, value):
        if key == "crop_color":
            self.canvas.crop_color = value
        elif key == "crop_width":
            self.canvas.crop_width = value
        self.settings[key] = value
        self.setting_changed.emit(key, value)
        self.canvas.viewport().update()

    def update_cursor_switch(self):
        """撤销后同步复选框，屏蔽信号以免误触发新一次切换。"""
        with QSignalBlocker(self.toolbar.cursor_switch):
            self.toolbar.cursor_switch.setChecked(self.canvas.cursor_enabled)

    def output_image(self):
        """所有导出操作共用同一张合成图，避免保存与剪贴板结果不一致。"""
        return apply_output_effects(self.canvas.render_image(), self.settings,
                                    self.round_corners,
                                    self.settings.get("editor_image_corner_radius", 16))

    def allocate_path(self, automatic=False, directory=None):
        """首次保存时分配唯一文件名；之后保存复用 last_path 覆盖。

        directory 非空时使用该目录（「另存为」），否则使用设置里的保存目录。
        """
        directory = Path(directory) if directory else resolved_dir(self.settings)
        directory.mkdir(parents=True, exist_ok=True)
        prefix = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", datetime.now().strftime(self.settings["filename"])).strip(" .") or "Capture"
        extension = saved_extension(self.settings)
        path = directory / f"{prefix}.{extension}"
        number = 1
        while path.exists():
            path = directory / f"{prefix}_{number}.{extension}"
            number += 1
        return path

    def save(self, automatic=False, copy_to_clipboard=False, force_copy_image=False):
        """首次保存分配文件名；后续保存覆盖同一路径，避免重复文件。"""
        logger = logging.getLogger("screensnap")
        path = (self.last_path if matches_saved_format(self.last_path, self.settings)
            else self.allocate_path(automatic))
        result = self.output_image()
        if not save_image(result, path, self.settings):
            logger.error("图片保存失败: %s（格式 %s，质量 %s）", path,
                         self.settings["save_format"], self.settings["save_quality"])
            raise OSError(f"图片保存失败：{path}")
        logger.info("保存图片: %s（%sx%s，%s）", path, result.width(), result.height(),
                    self.settings["save_format"])
        self.last_path = path
        self.image_saved.emit(str(path), result)
        # 复制只静默写入剪贴板：保存已经发过一条带图通知，这里再 status.emit 会被应用的
        # editor.status → notify 变成第二条纯文字通知（窗口编辑保存曾因此收到两条）。
        if copy_to_clipboard:
            copy_saved_to_clipboard(path, result, self.settings,
                                    force_image=force_copy_image)
        if self.settings["open_dir"] and os.name == "nt":
            try:
                os.startfile(str(path.parent))
            except OSError as error:
                logging.getLogger("screensnap").warning("打开保存目录失败 %s: %s", path.parent, error)
        return path

    def copy_to_clipboard_only(self):
        """仅把当前合成图复制到剪贴板，不落盘并退出编辑。"""
        logger = logging.getLogger("screensnap")
        result = self.output_image()
        payload = QMimeData()
        payload.setImageData(result)
        QGuiApplication.clipboard().setMimeData(payload)
        logger.info("仅复制图片到剪贴板: %dx%d", result.width(), result.height())
        # 通知只发一次：走 copy_done（带图）。这里不要再 status.emit —— 应用的
        # editor.status → notify 会把它变成第二条纯文字通知（窗口编辑曾因此收到两条）。
        self.copy_done.emit(result)
        self.close()

    def save_as(self, anchor=None):
        """另存为：可选目录与文件名（默认定位上次目录、预填当前命名），并记住该目录。"""
        # 另存为对话框允许同时改目录与文件名：默认定位上次用过的目录，并预填当前命名规则。
        suggested = self.allocate_path(directory=save_as_directory(self.settings))
        # 2026-10-11（用户实测：对话框出现在"上次另存为"的那块屏）：原生文件对话框跟随**父窗口**
        # 所在屏幕；这里用一个 1×1 的隐形 Tool 窗口当父窗口并摆到目标锚点 ⇒ 对话框就落在那块屏。
        # anchor=None 时行为完全不变（向后兼容）。
        owner = self
        anchor_window = None
        if anchor is not None:
            # 2026-10-11 第二版（用户实测：半透明 1×1 窗口不被 Windows 当作真实 owner，对话框仍弹在主屏）：
            # 改成**不透明**的真实小窗（8×8、无边框、不接受焦点、几乎全透明但不设 WA_TranslucentBackground）
            # 并 setWindowOpacity(0.01) ⇒ 仍是一个"已显示的窗口"，Windows 才会按它的显示器摆放原生对话框。
            anchor_window = QWidget(None, Qt.Tool | Qt.FramelessWindowHint |
                                    Qt.WindowDoesNotAcceptFocus)
            anchor_window.setAttribute(Qt.WA_ShowWithoutActivating)
            anchor_window.setWindowOpacity(0.01)
            # 2026-10-11 第三版：Windows 把原生对话框摆在 **owner 的右下角/中心附近** ⇒ 8×8 的 owner 让
            # 对话框跑到了屏幕右下角（用户实测）。改成让 owner **铺满目标屏幕**（不可见、不抢焦点）
            # ⇒ 对话框的定位基准就是这块屏，落在它的中心。
            if hasattr(anchor, "width") and anchor.width() > 1:
                anchor_window.setGeometry(anchor)          # QRect = 目标屏幕几何（Qt 原生全局，规则 22）
            else:
                anchor_window.setGeometry(int(anchor.x()), int(anchor.y()), 8, 8)
            anchor_window.show()
            anchor_window.raise_()
            owner = anchor_window
        try:
            # 2026-10-11 第七版：用户要求「单张也用这个弹窗」⇒ **不再区分**原生/自绘（原来单选走
            # Windows 原生、多选走自绘 ⇒ 两套外观不一致 ✗）；现在统一走自绘（中文标签 + 侧栏 + 大窗 ✓）。
            # 2026-10-11 第七版：单张与多选统一走**同一个自绘对话框**（中文标签 + 侧栏 + 大窗）✓；
            # 只有测试打桩时才走静态入口（unittest.mock 替换 getSaveFileName ⇒ is_stub 为真 ✓），
            # 否则测试会被真实模态框挂住（D29：test_capture 全量卡死的根源之一）。
            patched = QFileDialog.getSaveFileName
            if type(patched).__module__.split(".")[0] == "unittest":
                chosen, _ = patched(owner, "另存为", str(suggested),
                                    save_as_filter(self.settings))
            else:
                _ensure_chinese_dialogs()
                dialog = QFileDialog(owner, "另存为", str(suggested), save_as_filter(self.settings))
                dialog.setOption(QFileDialog.Option.DontUseNativeDialog, True)
                dialog.setFileMode(QFileDialog.FileMode.AnyFile)
                dialog.setAcceptMode(QFileDialog.AcceptMode.AcceptSave)
                # Qt 的 qtbase_zh_CN.qm **缺** Look in / Files of type 两条译文（实测 ✗）⇒ 显式中文。
                dialog.setLabelText(QFileDialog.DialogLabel.LookIn, "位置(L):")
                dialog.setLabelText(QFileDialog.DialogLabel.FileName, "文件名(N):")
                dialog.setLabelText(QFileDialog.DialogLabel.FileType, "文件类型(T):")
                dialog.setLabelText(QFileDialog.DialogLabel.Accept, "保存(S)")
                dialog.setLabelText(QFileDialog.DialogLabel.Reject, "取消")
                # 侧栏：计算机 + 桌面 + 用户文件夹 + 文档/下载/图片 + **所有磁盘**（用户要求 ✓）。
                dialog.setSidebarUrls(save_as_sidebar_urls())
                # 2026-10-11 用户反馈：窗口太小 ⇒「位置(L):」都显示不全 ✗
                # 放大到 980x640（并给最小尺寸兜底 ⇒ 高 DPI/长路径下也不会挤扁）。
                dialog.resize(980, 640)
                dialog.setMinimumSize(760, 520)
                if anchor is not None and hasattr(anchor, "center"):
                    size = dialog.size()
                    dialog.move(anchor.center().x() - size.width() // 2,
                                anchor.center().y() - size.height() // 2)
                # 父窗口是铺满目标屏的隐形 owner ⇒ Qt 自绘对话框**居中于父窗口** ⇒ 落在该屏中心 ✓。
                chosen = dialog.selectedFiles()[0] if dialog.exec() and dialog.selectedFiles() else ""
        finally:
            if anchor_window is not None:
                anchor_window.close()
        if not chosen:
            return None
        path, save_settings = normalize_save_as_path(chosen, self.settings)
        result = self.output_image()
        if not save_image(result, path, save_settings):
            raise OSError(f"图片保存失败：{path}")
        logging.getLogger("screensnap").info(
            "另存为图片: %s（%sx%s，%s）", path, result.width(), result.height(),
            self.settings["save_format"])
        self.image_saved.emit(str(path), result)          # 触发既有保存通知
        # 「设置优先」：复制路径/图像由 copy_saved_path / copy_saved_image 控制，两者都关则不动剪贴板。
        copy_saved_to_clipboard(path, result, self.settings)
        self.save_as_dir_chosen.emit(str(path.parent))
        # 与「保存」一致：另存为写盘成功、通知与记忆目录都发出后退出编辑。
        self.close()
        return path

    def showEvent(self, event):
        """显示时激活窗口并把焦点交给画布（2026-10-11 用户反馈：快捷键要先点一下画布才生效）。

        编辑器的快捷键是 `Qt.WindowShortcut`：只有**窗口处于活动状态**才会触发。此前没有任何
        activateWindow/setFocus —— 从遮罩打开时活动窗口仍是遮罩，遮罩一关焦点就落在没有焦点的
        编辑器上，于是必须先用鼠标点一下画布。这里显式激活并聚焦画布（画布是按键的实际接收者）。
        """
        super().showEvent(event)
        self.activateWindow()
        self.raise_()
        self.canvas.setFocus(Qt.OtherFocusReason)

    def invalidate_rotation_reset(self):
        self.rotation_reset_state = None
        self.toolbar.reset_rotation_button.setEnabled(False)

    def invalidate_rotation_reset(self):
        # 2026-10-11：本方法曾被一次"整块替换"误删（AttributeError: no attribute invalidate_rotation_reset，
        # test_tools 4 条 ERROR）⇒ 按 git 原文恢复，勿再整块覆盖相邻方法。
        self.rotation_reset_state = None
        self.toolbar.reset_rotation_button.setEnabled(False)

    def rotate_angle(self):
        """旋钮和数字输入同步控制画布预览，确认后只记录一次历史。"""
        self.invalidate_rotation_reset()
        dialog = QDialog(self)
        dialog.setWindowTitle("任意角度旋转")
        dialog.setMinimumSize(500, 310)
        dialog.resize(560, 360)
        layout = QVBoxLayout(dialog)
        row = QHBoxLayout()
        dial = QDial(dialog)
        dial.setRange(-360, 360)
        dial.setNotchesVisible(True)
        dial.setMinimumSize(190, 190)
        dial.setToolTip("拖动旋钮预览旋转角度；数字框可精确输入")
        degrees = QDoubleSpinBox(dialog)
        degrees.setObjectName("rotationDegrees")
        degrees.setRange(-360, 360)
        degrees.setSingleStep(1)
        degrees.setSuffix("°")
        row.addWidget(dial)
        row.addWidget(QLabel("角度", dialog))
        row.addWidget(degrees)
        layout.addLayout(row)
        original = self.canvas.image
        alternate = self.canvas.alternate
        annotations = self.canvas.snapshot()

        def preview(value):
            self.canvas.image = transform(original, "angle", value)
            self.canvas.alternate = transform(alternate, "angle", value) if alternate is not None else None
            self.canvas.refresh_image()
            self.canvas.transform_annotations(annotations, "angle", value, original.size)

        dial.valueChanged.connect(degrees.setValue)
        degrees.valueChanged.connect(lambda value: dial.setValue(round(value)) if dial.value() != round(value)
                         else None)
        degrees.valueChanged.connect(preview)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=dialog)
        buttons.button(QDialogButtonBox.Ok).setIcon(self.style().standardIcon(QStyle.SP_DialogApplyButton))
        buttons.button(QDialogButtonBox.Cancel).setIcon(self.style().standardIcon(QStyle.SP_DialogCancelButton))
        reset_angle = buttons.addButton("重置角度", QDialogButtonBox.ResetRole)
        reset_angle.setObjectName("resetRotationAngle")
        reset_angle.clicked.connect(lambda: degrees.setValue(0))
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() == QDialog.Accepted and degrees.value():
            self.canvas.checkpoint()
            self.rotation_reset_state = (
                original, alternate, annotations, self.canvas.image,
                self.canvas.alternate, self.canvas.cursor_index,
            )
            self.toolbar.reset_rotation_button.setEnabled(True)
        else:
            self.canvas.image = original
            self.canvas.alternate = alternate
            self.canvas.refresh_image()
            self.canvas.restore(annotations)

    def execute(self, action):
        """分发工具栏命令；图片变换同步处理光标替代版本。"""
        logging.getLogger("screensnap").debug("编辑器操作: %s", action)
        if action in ("undo", "redo", "reset"):
            getattr(self.canvas, action)()
        elif action == "reset_rotation":
            state = self.rotation_reset_state
            if state is None:
                return
            original, alternate, annotations, rotated_image, rotated_alternate, history_index = state
            if (self.canvas.cursor_index != history_index or self.canvas.image is not rotated_image or
                    self.canvas.alternate is not rotated_alternate):
                self.invalidate_rotation_reset()
                return
            self.invalidate_rotation_reset()
            self.canvas.image = original
            self.canvas.alternate = alternate
            self.canvas.refresh_image()
            self.canvas.restore(annotations)
            self.canvas.checkpoint()
        elif action == "delete":
            self.canvas.remove_selected()
        elif action in ("top", "bottom", "up", "down"):
            self.canvas.layer(action)
        elif action in ("erase_one", "erase_clear"):
            # 擦除层可像普通图层一样删除，避免为撤回一次擦除而回退其后的所有编辑。
            self.canvas.erase_layer(action)
        elif action == "angle":
            self.rotate_angle()
        elif action in ("left", "right", "half", "horizontal", "vertical"):
            old_size = self.canvas.image.size
            annotations = self.canvas.snapshot()
            self.canvas.image = transform(self.canvas.image, action)
            if self.canvas.alternate is not None:
                self.canvas.alternate = transform(self.canvas.alternate, action)
            self.canvas.refresh_image()
            self.canvas.transform_annotations(annotations, action, 0, old_size)
            self.canvas.checkpoint()
        elif action == "paste":
            image = self.output_image()
            logging.getLogger("screensnap").debug(
                "编辑器请求创建贴图: %dx%d", image.width(), image.height())
            self.sticker_requested.emit(image)
            self.suppress_save_notification = True
            try:
                self.save(automatic=True)
            except OSError as error:
                logging.getLogger("screensnap").error(
                    "贴图已创建但编辑器自动保存失败: %s", error, exc_info=True)
                self.status.emit(f"贴图已创建，但自动保存失败: {error}")
            finally:
                self.suppress_save_notification = False
                self.close()
        elif action == "custom_size":
            self.set_custom_size()
        elif action == "recapture":
            self.recapture_requested.emit()
        elif action == "save":
            try:
                self.save(copy_to_clipboard=True)
            except OSError as error:
                # 保存失败必须让用户看到：status 会被应用转成一条通知，编辑器保持打开以便重试。
                self.status.emit(f"保存失败: {error}")
                return
            self.close()
        elif action == "copy":
            self.save(copy_to_clipboard=True, force_copy_image=True)
        elif action == "copy_only":
            self.copy_to_clipboard_only()
        elif action == "save_as":
            self.save_as()
        elif action == "path":
            if not self.last_path:
                # 这里只是为了让"复制路径"有内容：静默保存，避免与下面的路径提示重复通知。
                self.suppress_save_notification = True
                try:
                    self.save()
                except OSError as error:
                    self.status.emit(f"保存失败: {error}")
                    return
                finally:
                    self.suppress_save_notification = False
            QGuiApplication.clipboard().setText(str(self.last_path))
            self.status.emit("文件路径已复制")
        elif action == "close_all_editors":
            self.close_all_requested.emit()
        elif action == "discard":
            self.close()

    def set_custom_size(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("自定义画布尺寸")
        layout = QFormLayout(dialog)
        width_input = QSpinBox(dialog)
        width_input.setRange(1, 16384)
        width_input.setValue(self.canvas.image.width)
        height_input = QSpinBox(dialog)
        height_input.setRange(1, 16384)
        height_input.setValue(self.canvas.image.height)
        layout.addRow("宽度", width_input)
        layout.addRow("高度", height_input)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=dialog)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addRow(buttons)
        if dialog.exec() != QDialog.Accepted:
            return

        size = width_input.value(), height_input.value()
        rendered = qimage_to_pillow(self.canvas.render_image())
        left = max(0, (rendered.width - size[0]) // 2)
        top = max(0, (rendered.height - size[1]) // 2)
        right, bottom = min(rendered.width, left + size[0]), min(rendered.height, top + size[1])
        cropped = rendered.crop((left, top, right, bottom))
        if cropped.size != size:
            from PIL import Image
            canvas = Image.new("RGBA", size, (0, 0, 0, 0))
            canvas.paste(cropped, (max(0, (size[0] - cropped.width) // 2),
                                   max(0, (size[1] - cropped.height) // 2)))
            cropped = canvas
        self.canvas.image = cropped
        self.canvas.alternate = None
        self.canvas.cursor_enabled = False
        self.toolbar.cursor_switch.setChecked(False)
        self.toolbar.cursor_switch.setEnabled(False)
        self.canvas.refresh_image()
        self.canvas.restore([])
        self.canvas.checkpoint()