"""Application-wide palette selection while preserving native Qt widget styles."""

import logging
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QEvent, QObject, QPointF, Qt
from PySide6.QtGui import QColor, QMouseEvent, QPalette
from PySide6.QtWidgets import (QAbstractSpinBox, QLineEdit, QStyle,
                               QStyleOptionSpinBox)

THEME_PALETTES = {
    "dark": {
        QPalette.Window: "#202124",
        QPalette.WindowText: "#e8eaed",
        QPalette.Base: "#171717",
        QPalette.AlternateBase: "#2b2d30",
        QPalette.ToolTipBase: "#303134",
        QPalette.ToolTipText: "#e8eaed",
        QPalette.Text: "#e8eaed",
        QPalette.Button: "#303134",
        QPalette.ButtonText: "#e8eaed",
        QPalette.Light: "#b8c2cc",
        QPalette.Mid: "#50545a",
        QPalette.Midlight: "#3b3e43",
        QPalette.Dark: "#171717",
        QPalette.Shadow: "#101010",
        QPalette.BrightText: "#ff6b6b",
        QPalette.Highlight: "#2f6096",
        QPalette.HighlightedText: "#ffffff",
        QPalette.PlaceholderText: "#9aa0a6",
        QPalette.Link: "#8ab4f8",
    },
    "light": {
        QPalette.Window: "#f0f0f0",
        QPalette.WindowText: "#202020",
        QPalette.Base: "#ffffff",
        QPalette.AlternateBase: "#f5f5f5",
        QPalette.ToolTipBase: "#f3f3f3",
        QPalette.ToolTipText: "#202020",
        QPalette.Text: "#202020",
        QPalette.Button: "#f0f0f0",
        QPalette.ButtonText: "#202020",
        QPalette.Light: "#ffffff",
        QPalette.Mid: "#c8c8c8",
        QPalette.Midlight: "#f7f7f7",
        QPalette.Dark: "#a0a0a0",
        QPalette.Shadow: "#808080",
        QPalette.BrightText: "#c62828",
        QPalette.Highlight: "#176b87",
        QPalette.HighlightedText: "#ffffff",
        QPalette.PlaceholderText: "#707070",
        QPalette.Link: "#075e89",
    },
}
THEME_DISABLED_TEXT = {"dark": "#9aa0a6", "light": "#5f6368"}


class _SpinBoxArrowEventRouter(QObject):
    """Forward arrow-zone events swallowed by the spinbox's overlapping line edit."""

    def __init__(self, application):
        super().__init__(application)
        self.application = application
        self._pressed_spinbox = None
        self._hover_spinbox = None

    def eventFilter(self, watched, event):
        if (self.application.property("screensnap_theme_mode") == "system" or
            not isinstance(watched, QLineEdit)):
            return False
        spinbox = watched.parentWidget()
        if not isinstance(spinbox, QAbstractSpinBox):
            return False
        event_type = event.type()
        if event_type not in (QEvent.MouseButtonPress, QEvent.MouseMove,
                              QEvent.MouseButtonRelease):
            return False
        global_point = watched.mapToGlobal(event.position().toPoint())
        point = spinbox.mapFromGlobal(global_point)
        option = QStyleOptionSpinBox()
        option.initFrom(spinbox)
        option.rect = spinbox.rect()
        style = spinbox.style()
        up = style.subControlRect(QStyle.ComplexControl.CC_SpinBox, option,
                      QStyle.SubControl.SC_SpinBoxUp, spinbox)
        down = style.subControlRect(QStyle.ComplexControl.CC_SpinBox, option,
                        QStyle.SubControl.SC_SpinBoxDown, spinbox)
        in_arrow = up.contains(point) or down.contains(point)
        routed = self._pressed_spinbox is spinbox
        hovering = self._hover_spinbox is spinbox

        should_forward = ((event_type == QEvent.MouseButtonPress and
                           event.button() == Qt.LeftButton and in_arrow) or
                          (event_type == QEvent.MouseMove and
                           (in_arrow or routed or hovering)) or
                          (event_type == QEvent.MouseButtonRelease and
                           (routed or (event.button() == Qt.LeftButton and in_arrow))))
        if not should_forward:
            return False

        forwarded = QMouseEvent(
            event_type, QPointF(point), QPointF(global_point), event.button(),
            event.buttons(), event.modifiers())
        QCoreApplication.sendEvent(spinbox, forwarded)
        if event_type == QEvent.MouseButtonPress:
            self._pressed_spinbox = spinbox
        elif event_type == QEvent.MouseMove:
            self._hover_spinbox = spinbox if in_arrow else None
        elif event_type == QEvent.MouseButtonRelease and event.button() == Qt.LeftButton:
            self._pressed_spinbox = None
        return True


_SPINBOX_ARROW_ROUTER = None


def _install_spinbox_arrow_router(application):
    global _SPINBOX_ARROW_ROUTER
    if (_SPINBOX_ARROW_ROUTER is None or
            _SPINBOX_ARROW_ROUTER.application is not application):
        _SPINBOX_ARROW_ROUTER = _SpinBoxArrowEventRouter(application)
        application.installEventFilter(_SPINBOX_ARROW_ROUTER)

# Windows 原生风格只按调色板填菜单底色，菜单文字却沿用固定的深色，深色主题下会出现
# 黑底黑字。这里仅对 QMenu 覆盖前景与背景（其余控件仍走原生风格），并保留勾选标记、
# 子菜单箭头与禁用态。
MENU_STYLESHEET_TEMPLATE = """
QMenu {{
    background-color: {window};
    color: {text};
    border: 1px solid {border};
    padding: 4px;
}}
QMenu::item {{
    padding: 4px 18px 4px 4px;
    background: transparent;
}}
QMenu::item:selected {{
    background-color: {highlight};
    color: {highlighted_text};
}}
QMenu::item:disabled {{
    color: {disabled};
}}
QMenu::separator {{
    height: 1px;
    background: {border};
    margin: 4px 8px;
}}
"""

CONTROL_STYLESHEET_TEMPLATE = """
QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox, QComboBox,
QDateEdit, QTimeEdit, QDateTimeEdit {{
    background-color: {base};
    color: {text};
    border: 1px solid {border};
    selection-background-color: {highlight};
    selection-color: {highlighted_text};
}}
/* 深色主题下 QComboBox 弹出列表默认沿用系统浅色底，和深色输入框反差过大；
   这里显式给下拉项上底色/文字/选中态，并去掉聚焦时的点线框。 */
QComboBox QAbstractItemView {{
    background-color: {base};
    color: {text};
    border: 1px solid {border};
    selection-background-color: {highlight};
    selection-color: {highlighted_text};
    outline: 0;
}}
QComboBox QAbstractItemView::item:disabled {{
    color: {disabled};
}}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QSpinBox:focus,
QDoubleSpinBox:focus, QComboBox:focus, QDateEdit:focus, QTimeEdit:focus,
QDateTimeEdit:focus {{
    border-color: {highlight};
}}
QSpinBox:hover, QDoubleSpinBox:hover, QDateEdit:hover,
QTimeEdit:hover, QDateTimeEdit:hover {{
    border-color: {text};
}}
QSpinBox::up-button, QDoubleSpinBox::up-button,
QDateEdit::up-button, QTimeEdit::up-button, QDateTimeEdit::up-button,
QSpinBox::down-button, QDoubleSpinBox::down-button,
QDateEdit::down-button, QTimeEdit::down-button, QDateTimeEdit::down-button {{
    subcontrol-origin: border;
    width: 28px;
    height: 28px;
}}
QSpinBox::up-button, QDoubleSpinBox::up-button,
QDateEdit::up-button, QTimeEdit::up-button, QDateTimeEdit::up-button {{
    subcontrol-position: right center;
    right: 28px;
}}
QSpinBox::down-button, QDoubleSpinBox::down-button,
QDateEdit::down-button, QTimeEdit::down-button, QDateTimeEdit::down-button {{
    subcontrol-position: right center;
    right: 0px;
}}
QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover,
QDateEdit::up-button:hover, QTimeEdit::up-button:hover, QDateTimeEdit::up-button:hover,
QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover,
QDateEdit::down-button:hover, QTimeEdit::down-button:hover, QDateTimeEdit::down-button:hover {{
    background-color: {midlight};
}}
QSpinBox::up-button:pressed, QDoubleSpinBox::up-button:pressed,
QDateEdit::up-button:pressed, QTimeEdit::up-button:pressed, QDateTimeEdit::up-button:pressed,
QSpinBox::down-button:pressed, QDoubleSpinBox::down-button:pressed,
QDateEdit::down-button:pressed, QTimeEdit::down-button:pressed, QDateTimeEdit::down-button:pressed {{
    background-color: {highlight};
}}
QLineEdit:disabled, QTextEdit:disabled, QPlainTextEdit:disabled,
QSpinBox:disabled, QDoubleSpinBox:disabled, QComboBox:disabled,
QDateEdit:disabled, QTimeEdit:disabled, QDateTimeEdit:disabled {{
    background-color: {disabled_base};
    color: {disabled};
    border-color: {disabled_border};
    selection-background-color: {disabled_base};
    selection-color: {disabled};
}}
QCheckBox::indicator, QRadioButton::indicator {{
    width: 18px;
    height: 18px;
    border: 2px solid {indicator_border};
    background-color: {indicator_base};
}}
QCheckBox::indicator {{
    border-radius: 3px;
}}
QRadioButton::indicator {{
    border-radius: 9px;
}}
QCheckBox::indicator:hover, QRadioButton::indicator:hover {{
    border-color: {text};
}}
QCheckBox::indicator:checked {{
    border-color: {highlight};
    background-color: {highlight};
    image: url("{checkbox_check}");
}}
QRadioButton::indicator:checked {{
    border-color: {highlight};
    background-color: {highlight};
    image: url("{radio_dot}");
}}
QCheckBox::indicator:disabled, QRadioButton::indicator:disabled {{
    border-color: {disabled_border};
    background-color: {disabled_base};
}}
QCheckBox::indicator:checked:disabled {{
    border-color: {disabled_border};
    background-color: {disabled_highlight};
    image: url("{checkbox_check}");
}}
QRadioButton::indicator:checked:disabled {{
    border-color: {disabled_border};
    background-color: {disabled_highlight};
    image: url("{radio_dot}");
}}
"""


def apply_theme(application, mode):
    """Apply the requested palette; system mode returns to the platform palette."""
    application.setProperty("screensnap_theme_mode", mode)
    _install_spinbox_arrow_router(application)
    hints = application.styleHints()
    if not application.property("screensnap_theme_listener"):
        signal = getattr(hints, "colorSchemeChanged", None)
        if signal is not None:
            signal.connect(lambda *_: _on_system_scheme_changed(application))
            application.setProperty("screensnap_theme_listener", True)
    if mode == "system":
        _apply_system_palette(application)
        _apply_menu_stylesheet(application)
        return

    palette = QPalette()
    values = THEME_PALETTES[mode]
    for role, color in values.items():
        palette.setColor(role, QColor(color))
    disabled = QColor(THEME_DISABLED_TEXT[mode])
    disabled_surface = QColor(values[QPalette.AlternateBase])
    palette.setColor(QPalette.Disabled, QPalette.WindowText, disabled)
    palette.setColor(QPalette.Disabled, QPalette.Text, disabled)
    palette.setColor(QPalette.Disabled, QPalette.ButtonText, disabled)
    palette.setColor(QPalette.Disabled, QPalette.Base, disabled_surface)
    palette.setColor(QPalette.Disabled, QPalette.Button, disabled_surface)
    palette.setColor(QPalette.Disabled, QPalette.Mid, disabled)
    disabled_highlight = QColor("#465462" if mode == "dark" else "#c4d8e8")
    palette.setColor(QPalette.Disabled, QPalette.Highlight, disabled_highlight)
    palette.setColor(QPalette.Disabled, QPalette.HighlightedText, disabled)
    application.setPalette(palette)
    _apply_menu_stylesheet(application)


def _apply_system_palette(application):
    if application.property("screensnap_theme_mode") == "system":
        # Windows 11's style standardPalette() can return the legacy beige palette.
        application.setPalette(QPalette())


def _on_system_scheme_changed(application):
    """系统颜色模式变化时重刷调色板与菜单样式（仅系统主题需要）。"""
    if application.property("screensnap_theme_mode") != "system":
        return
    _apply_system_palette(application)
    _apply_menu_stylesheet(application)


def _menu_stylesheet(palette):
    """按当前调色板生成仅作用于 QMenu 的样式表。"""
    return MENU_STYLESHEET_TEMPLATE.format(
        window=palette.color(QPalette.Window).name(),
        text=palette.color(QPalette.WindowText).name(),
        border=palette.color(QPalette.Mid).name(),
        highlight=palette.color(QPalette.Highlight).name(),
        highlighted_text=palette.color(QPalette.HighlightedText).name(),
        disabled=palette.color(QPalette.Disabled, QPalette.WindowText).name())


def _control_stylesheet(palette):
    return CONTROL_STYLESHEET_TEMPLATE.format(
        base=palette.color(QPalette.Base).name(),
        text=palette.color(QPalette.Text).name(),
        border=palette.color(QPalette.Mid).name(),
            midlight=palette.color(QPalette.Midlight).name(),
        alternate=palette.color(QPalette.AlternateBase).name(),
        highlight=palette.color(QPalette.Highlight).name(),
        highlighted_text=palette.color(QPalette.HighlightedText).name(),
        disabled=palette.color(QPalette.Disabled, QPalette.Text).name(),
        disabled_base=palette.color(QPalette.Disabled, QPalette.Base).name(),
        disabled_border=palette.color(QPalette.Disabled, QPalette.Mid).name(),
        disabled_highlight=palette.color(QPalette.Disabled, QPalette.Highlight).name(),
        indicator_border=palette.color(QPalette.Light).name(),
        indicator_base=palette.color(QPalette.AlternateBase).name(),
        checkbox_check=(Path(__file__).parent / "assets" / "checkbox-check.svg").as_posix(),
        radio_dot=(Path(__file__).parent / "assets" / "radio-dot.svg").as_posix())

def _button_stylesheet(palette):
    return """QPushButton, QToolButton {{
    background-color: {button};
    color: {button_text};
    border: 1px solid {border};
}}
QPushButton:hover, QToolButton:hover {{
    background-color: {button_hover};
}}
QPushButton:pressed, QToolButton:pressed {{
    background-color: {button_pressed};
}}
QPushButton:disabled, QToolButton:disabled {{
    background-color: {disabled_button};
    color: {disabled_button_text};
    border-color: {disabled_border};
}}""".format(
        button=palette.color(QPalette.Button).name(),
        button_text=palette.color(QPalette.ButtonText).name(),
        border=palette.color(QPalette.Mid).name(),
        button_hover=palette.color(QPalette.Midlight).name(),
        button_pressed=palette.color(QPalette.Mid).name(),
        disabled_button=palette.color(QPalette.Disabled, QPalette.Button).name(),
        disabled_button_text=palette.color(QPalette.Disabled, QPalette.ButtonText).name(),
        disabled_border=palette.color(QPalette.Disabled, QPalette.Mid).name())


def _apply_menu_stylesheet(application):
    """深色主题下覆盖菜单文字色，避免 Windows 原生风格画出黑底黑字；浅色保持原生外观。"""
    palette = application.palette()
    mode = application.property("screensnap_theme_mode")
    dark = (mode == "dark" or
            palette.color(QPalette.Window).lightness() < 128)
    stylesheet = _menu_stylesheet(palette)
    if dark:
        stylesheet += _control_stylesheet(palette)
    if palette.color(QPalette.Window).lightness() < 128:
        stylesheet += _button_stylesheet(palette)
    if application.styleSheet() == stylesheet:
        return
    application.setStyleSheet(stylesheet)
    logging.getLogger("screensnap").debug(
        "菜单样式刷新: %s", mode)


def system_theme_name(application):
    """Return the current Qt platform color scheme as a stable mode name."""
    scheme = application.styleHints().colorScheme()
    if scheme == Qt.ColorScheme.Dark:
        return "dark"
    return "light"