"""颜色选择按钮。"""

from PySide6.QtCore import QLibraryInfo, QSize, QTranslator, Qt
from PySide6.QtWidgets import QApplication, QWidget, QPushButton, QColorDialog, QLabel, QMenu
from PySide6.QtGui import QColor, QCursor, QIcon, QPainter, QPen, QPixmap

from logger import get_logger

# 最近颜色由应用层注入：ColorButton 不直接依赖配置对象，设置页与两个编辑器
# 因此共用同一个池子（右键色块即可选最近使用）。
_recent_colors_provider = None
_recent_color_recorder = None


def set_recent_colors_provider(provider):
    """注册“读取最近颜色列表”的回调；传 None 表示取消。"""
    global _recent_colors_provider
    _recent_colors_provider = provider


def set_recent_color_recorder(recorder):
    """注册“记录一个最近颜色”的回调；传 None 表示取消。"""
    global _recent_color_recorder
    _recent_color_recorder = recorder


def record_recent_color(color):
    """把用户确认的颜色写入最近池；没有注册回调时静默忽略。"""
    if _recent_color_recorder is None:
        return
    try:
        _recent_color_recorder(color)
    except Exception as error:  # noqa: BLE001 最近颜色失败绝不能影响选色本身
        # 失败边界留痕（DEBUG 即可）：用户仍能正常选色，但排查“最近颜色不更新”时有据可查。
        get_logger().debug("记录最近颜色失败: %s (%s)", color, error)

COMMON_COLORS = (
    "#ff0000", "#00a000", "#0000ff", "#000000", "#ffffff",
    "#ffff00", "#ff8000", "#800080", "#00ffff", "#808080",
    # 清新/低饱和备选，避免放大标注只能沿用基础高饱和色。
    "#00b8a9", "#2d9cdb", "#56ccf2", "#27ae60", "#9b51e0",
    "#f2994a", "#eb5757", "#bb6bd9",
)
_color_translator = None
_common_colors_initialized = False
_FALLBACK_LABELS = {
    "Basic colors": "基本颜色", "Custom colors": "自定义颜色",
    "Hue:": "色相：", "Sat:": "饱和度：", "Val:": "明度：",
    "Red:": "红：", "Green:": "绿：", "Blue:": "蓝：",
    "Alpha channel:": "透明度：", "HTML:": "颜色代码：",
    "Pick Screen Color": "屏幕取色", "Add to Custom Colors": "添加到自定义颜色",
    "OK": "确定", "Cancel": "取消",
}


def _outermost_window(widget):
    """向上回溯到最外层的顶层窗口（任意含 Window 位的窗口类型）。"""
    top = widget
    result = None
    while isinstance(top, QWidget):
        if top.windowFlags() & Qt.Window:
            result = top
        top = top.parent()
    return result


def color_dialog(initial, parent):
    """固定使用可翻译的 Qt 对话框，并在自定义色板中预置常用色。"""
    global _color_translator, _common_colors_initialized
    app = QApplication.instance()
    if _color_translator is None:
        translator = QTranslator(app)
        path = QLibraryInfo.path(QLibraryInfo.TranslationsPath)
        if translator.load("qtbase_zh_CN", path):
            app.installTranslator(translator)
            _color_translator = translator
    if not _common_colors_initialized:
        for index, value in enumerate(COMMON_COLORS):
            QColorDialog.setCustomColor(index, QColor(value))
        _common_colors_initialized = True
    # 取色对话框不能以“更多设置”这类 QMenu 弹出层为父：弹出层在对话框以模态打开时会关闭，
    # 而被其拥有的窗口会被窗口管理器一并隐藏，表现为“看不到 / 被压到最后”。同样也不要以
    # 置顶遮罩（Qt.Tool）为父，重挂载会破坏对话框外壳。因此只对普通顶层窗口（设置页、独立
    # 编辑器窗口）沿用父级以保留居中；遇到弹出层或置顶遮罩则改为无父的置顶独立窗口，
    # 既不随弹出层关闭而消失，也始终浮在置顶遮罩之上。
    # 取色对话框的所属窗口要“越过”可能关闭的弹出层，直接挂到最外层的稳定窗口（置顶遮罩）下。
    # 若以“更多设置”这类 QMenu 弹出层为父，弹出层在对话框以模态打开时会关闭，被其拥有的窗口
    # 会被窗口管理器一并隐藏（看不到 / 被压到最后）。遮罩始终稳定且置顶，对话框作为它的所属窗口
    # 既能恒浮其前、又能随截图一起关闭，且构造期指定父级不会破坏对话框外壳。
    dialog_parent = _outermost_window(parent)
    dialog = QColorDialog(QColor(initial), dialog_parent)
    dialog.setOption(QColorDialog.DontUseNativeDialog)
    dialog.setWindowTitle("选择颜色")
    dialog.setWindowFlag(Qt.WindowStaysOnTopHint)
    if _color_translator is None:
        for widget in (*dialog.findChildren(QLabel), *dialog.findChildren(QPushButton)):
            text = widget.text().replace("&", "")
            if text in _FALLBACK_LABELS:
                widget.setText(_FALLBACK_LABELS[text])
    return dialog


class ColorButton(QPushButton):
    """打开系统取色对话框，确认选择后再通知设置页。"""

    def __init__(self, color, changed, compact=False, purpose="标注颜色"):
        super().__init__()
        self.color = color
        self.changed = changed
        self.compact = compact
        self.purpose = purpose
        self.update_color()
        self.clicked.connect(self.choose)
        # 左键仍是“打开完整取色器”；右键只提供最近颜色与一个“更多颜色”入口。
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self.show_recent_menu)

    def update_color(self):
        # 设置页留出更大的色样区域；工具栏保持紧凑，避免挤压控制项。
        swatch_size = QSize(18, 18) if self.compact else QSize(48, 28)
        self.setIconSize(swatch_size)
        swatch = QPixmap(swatch_size)
        swatch.fill(QColor(self.color))
        if not self.compact:
            painter = QPainter(swatch)
            painter.setPen(QPen(QColor("#718087"), 1))
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(swatch.rect().adjusted(0, 0, -1, -1))
            painter.end()
        self.setIcon(QIcon(swatch))
        self.setText("" if self.property("icon_only") else
                 "颜色" if self.compact else f"选择颜色 {self.color}")
        if self.compact:
            self.setToolTip(f"选择{self.purpose}：{self.color}\n右键可选择最近使用的颜色")
        else:
            # 设置页会为同一个按钮设置功能说明：只能在原说明末尾追加右键提示，
            # 不能整条覆盖（否则“矩形边框颜色…”这类帮助文案会丢失）。
            tip = self.toolTip().split("\n右键可选择最近使用的颜色")[0]
            if tip:
                self.setToolTip(f"{tip}\n右键可选择最近使用的颜色")
            else:
                self.setToolTip("右键可选择最近使用的颜色")

    def set_color(self, color):
        self.color = color
        self.update_color()

    def show_recent_menu(self, position=None):
        """右键菜单：最近使用的颜色 + 完整取色器。"""
        menu = QMenu(self)
        colors = []
        if _recent_colors_provider is not None:
            try:
                colors = list(_recent_colors_provider() or [])
            except Exception as error:  # noqa: BLE001 最近颜色读取失败不影响取色
                get_logger().debug("读取最近颜色失败: %s", error)
                colors = []
        if colors:
            heading = menu.addAction("最近使用")
            heading.setEnabled(False)
            for color in colors:
                swatch = QPixmap(14, 14)
                swatch.fill(QColor(color))
                action = menu.addAction(QIcon(swatch), color)
                action.triggered.connect(
                    lambda checked=False, value=color: self.use_recent_color(value))
            menu.addSeparator()
        more = menu.addAction("更多颜色…")
        more.triggered.connect(self.choose)
        point = self.mapToGlobal(position) if position is not None else QCursor.pos()
        menu.exec(point)
        menu.deleteLater()

    def use_recent_color(self, color):
        """选中一个最近颜色：与手动选色走同一条通知与记录路径。"""
        self.set_color(color)
        self.changed(self.color)
        record_recent_color(self.color)

    def choose(self):
        """只有确认有效颜色后才改变色块并触发设置保存。"""
        dialog = color_dialog(self.color, self)
        if dialog.exec() == QColorDialog.Accepted:
            color = dialog.currentColor()
            self.set_color(color.name())
            self.changed(self.color)
            record_recent_color(self.color)