"""统一保存目录、归档与图片缓存管理。"""

import logging

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QLabel, QMessageBox, QPushButton, QStyle

from core.path_utils import configured_dir, data_dir
from ui.widgets.confirmation import yes_no_dialog
from ui.widgets.file_path_edit import FilePathEdit
from ui.widgets.tooltip import SettingsPage


class SaveOutputPage(SettingsPage):
    """设置输出图像外观、统一保存位置和缓存维护。"""

    def __init__(self, config, changed, clear_cache=None):
        super().__init__(config, changed)
        # 设置页不直接持有贴图管理器：清理缓存通过回调注入，避免 ui 反向依赖 sticker。
        self.clear_cache_callback = clear_cache
        self.group("输出图像外观")
        self.preview("output", 120)
        self.check("editor_image_round_corners", "输出透明圆角",
                   "将圆角外像素设为透明；截图选区预览、编辑器预览和最终输出共用此设置")
        self.number("editor_image_corner_radius", "输出圆角半径 (px)", 0, 100,
                    "0 表示直角；圆角会应用于截图、保存、复制和贴图输出")
        self.check("editor_image_border_enabled", "输出图像添加边框",
                   "边框写入最终输出图像")
        self.number("editor_image_border_width", "输出边框宽度 (px)", 0, 20,
                    "边框宽度；0 表示不绘制")
        self.color("editor_image_border_color", "输出边框颜色", "写入最终图像的边框颜色")
        self.check("editor_image_shadow_enabled", "输出图像添加阴影",
                   "阴影写入最终输出图像，阴影会增加输出画布尺寸")
        self.number("editor_image_shadow_size", "阴影模糊尺寸 (px)", 0, 60,
                    "阴影扩散与模糊尺寸；0 表示无扩散")
        self.number("editor_image_shadow_strength", "阴影强度 (%)", 0, 100,
                    "阴影不透明度；0 表示不绘制")
        self.color("editor_image_shadow_color", "输出阴影颜色", "写入最终图像的阴影颜色")
        self.group("保存位置")
        widget = FilePathEdit(
            config.data["save_dir"],
            lambda value: self.update_value("save_dir", value),
            open_path=lambda: configured_dir(self.config.data))
        widget.setToolTip("截图自动保存、手动保存和历史图片共用此目录；留空使用图片文件夹中的 ScreenSnap。\n"
                          "自动/手动保存均遵守下方归档设置。")
        self.controls["save_dir"] = widget
        self.form.addRow("图片保存目录", widget)
        self.group("图片归档")
        archive_month = self.check(
            "archive_by_month", "按月归档",
            "所有保存都按保存月份存入 YYYY-MM 子文件夹；跨月后自动使用新月份目录。")
        archive_day = self.check(
            "archive_by_day", "按日归档",
            "所有保存都按保存日期存入 YYYY-MM-DD 子文件夹。开启后会自动关闭按月归档。")
        archive_month.toggled.connect(
            lambda checked: archive_day.setChecked(False) if checked else None)
        archive_day.toggled.connect(
            lambda checked: archive_month.setChecked(False) if checked else None)
        self.group("文件与目录")
        self.choice("save_format", "保存格式", [("PNG（无损）", "png"), ("JPEG", "jpg"),
                                            ("WebP", "webp"), ("BMP", "bmp")],
                    "自动与手动保存使用的图片格式")
        self.number("save_quality", "图片质量", 1, 100,
                    "仅 JPEG 与 WebP 生效；数值越大越清晰，文件也越大")
        self.color("save_background", "保存底色",
                    "JPEG 与 BMP 不支持透明通道，保存时透明区域会先合成到这个颜色")
        self.text("filename", "文件名模板", "使用 strftime 时间占位符生成文件名")
        hint = QLabel("%Y 四位年    %m 月    %d 日\n"
                  "%H 时（24 小时制）    %M 分    %S 秒\n"
                  "_ 等普通字符会原样保留")
        hint.setWordWrap(True)
        self.form.addRow("", hint)
        self.check("open_dir", "保存后打开目录", "每次保存成功后打开资源管理器")
        self.group("手动保存后复制")
        self.check("copy_saved_image", "图片", "双击保存和保存按钮将合成图片放入剪贴板")
        self.check("copy_saved_path", "文件路径文字", "双击保存和保存按钮将保存路径作为文字放入剪贴板")
        self.group("缓存管理")
        self.check("cache_clear_clipboard", "清理剪贴板历史",
                   "点“立即清理缓存”或自动清理时，是否连同剪贴板历史（内存记录 + 历史图片）一起清空。\n关闭：剪贴板历史保留，之后仍能按时间顺序贴出旧图；代价是该目录会持续变大。\n与贴图无关：已创建的贴图与回收站内容都不受这里影响。")
        self.check("cache_clear_toast", "清理 Toast 缩略图",
                   "是否清理通知（Toast/托盘气泡）用过的缩略图缓存。\n这些图随时可按需重建，所以清理一般无感；关闭只是让缓存目录继续变大。\n不影响真实截图与贴图文件。")
        self.check("cache_clear_sticker", "清理孤儿贴图缓存",
                   "是否清理“孤儿”贴图私有缓存。\n只删当前没有任何活动贴图或回收站贴图引用的文件；会话仍在引用的源文件永远不会被删。\n关闭：这些不再使用的缓存文件会一直留在缓存目录里。")
        self.choice("cache_cleanup_timing", "自动清理时机",
                    [("仅手动", "off"), ("启动时", "start"), ("退出时", "exit")],
                    "除“立即清理”按钮外，是否在启动或退出时按上面的范围自动清理一次缓存。\n仅手动：只在点按钮时清理（默认）。\n启动时：界面起来之后在后台清理，不拖慢启动。\n退出时：先保存贴图会话再清理，避免把本会话仍在引用的源文件当成孤儿删掉。\n自动清理同样遵守上面三个范围开关。")
        self.form.addRow(self._cache_controls())

    def _cache_controls(self):
        from PySide6.QtWidgets import QHBoxLayout, QWidget

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        self.clear_cache_button = QPushButton("清理可重建缓存")
        self.clear_cache_button.setIcon(self.style().standardIcon(QStyle.SP_DialogDiscardButton))
        self.clear_cache_button.setToolTip(
            "清理剪贴板历史图片、Toast 缩略图及未被活动贴图引用的私有缓存；\n"
            "不会删除已保存截图或当前贴图的源文件。")
        # 没有注入清理回调（例如独立构造设置页的测试/工具场景）时按钮置灰，避免点了没反应。
        self.clear_cache_button.setEnabled(self.clear_cache_callback is not None)
        self.clear_cache_button.clicked.connect(self.clear_rebuildable_cache)
        self.open_cache_button = QPushButton("打开缓存目录")
        self.open_cache_button.setIcon(self.style().standardIcon(QStyle.SP_DirOpenIcon))
        self.open_cache_button.setToolTip("打开 ScreenSnap 缓存根目录")
        self.open_cache_button.clicked.connect(self.open_cache_directory)
        row.addWidget(self.clear_cache_button)
        row.addWidget(self.open_cache_button)
        row.addStretch()
        container = QWidget()
        container.setLayout(row)
        return container

    def open_cache_directory(self):
        """打开缓存根目录；目录不存在就先建出来，再交给系统资源管理器。"""
        directory = data_dir()
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            logging.getLogger("screensnap").warning("无法创建缓存目录 %s: %s", directory, error)
            QMessageBox.warning(self, "无法打开缓存目录", str(error))
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(directory)))

    def clear_rebuildable_cache(self):
        """先确认再清理：这是删除操作，必须让用户明确知道删掉的是什么、保留的是什么。"""
        if self.clear_cache_callback is None:
            return
        if yes_no_dialog(
                self, "清理可重建缓存",
                "清除剪贴板历史记录及其图片、Toast 缩略图和未引用的贴图缓存？\n"
                "已保存截图和当前贴图源文件不会删除。是否继续？").exec() != QMessageBox.Yes:
            return
        try:
            result = self.clear_cache_callback()
        except (OSError, ValueError) as error:
            logging.getLogger("screensnap").error("清理图片缓存失败: %s", error, exc_info=True)
            QMessageBox.warning(self, "清理缓存失败", str(error))
            return
        logging.getLogger("screensnap").info("设置页清理可重建缓存: %s", result)
        QMessageBox.information(
            self, "缓存清理完成",
            "剪贴板历史：{clipboard_entries} 条；剪贴板图片：{clipboard_images} 张；"
            "孤儿贴图缓存：{orphan_sticker_images} 张；Toast 缩略图：{toast_images} 张。"
            .format(**result))