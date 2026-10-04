"""Application-wide palette selection while preserving native Qt widget styles."""

import logging

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette

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
    padding: 4px 28px 4px 24px;
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


def apply_theme(application, mode):
    """Apply the requested palette; system mode returns to the platform palette."""
    application.setProperty("screensnap_theme_mode", mode)
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
    if mode == "dark":
        values = {
            QPalette.Window: "#202124",
            QPalette.WindowText: "#e8eaed",
            QPalette.Base: "#171717",
            QPalette.AlternateBase: "#2b2d30",
            QPalette.ToolTipBase: "#303134",
            QPalette.ToolTipText: "#202124",
            QPalette.Text: "#e8eaed",
            QPalette.Button: "#303134",
            QPalette.ButtonText: "#e8eaed",
            QPalette.BrightText: "#ff6b6b",
            QPalette.Highlight: "#4c8bf5",
            QPalette.HighlightedText: "#ffffff",
            QPalette.PlaceholderText: "#9aa0a6",
            QPalette.Link: "#8ab4f8",
        }
    else:
        values = {
            QPalette.Window: "#f0f0f0",
            QPalette.WindowText: "#202020",
            QPalette.Base: "#ffffff",
            QPalette.AlternateBase: "#f5f5f5",
            QPalette.ToolTipBase: "#f3f3f3",
            QPalette.ToolTipText: "#202020",
            QPalette.Text: "#202020",
            QPalette.Button: "#f0f0f0",
            QPalette.ButtonText: "#202020",
            QPalette.BrightText: "#ff0000",
            QPalette.Highlight: "#308cc6",
            QPalette.HighlightedText: "#ffffff",
            QPalette.PlaceholderText: "#707070",
            QPalette.Link: "#0000ff",
        }

    for role, color in values.items():
        palette.setColor(role, QColor(color))
    palette.setColor(QPalette.Disabled, QPalette.WindowText, QColor("#888888"))
    palette.setColor(QPalette.Disabled, QPalette.Text, QColor("#888888"))
    palette.setColor(QPalette.Disabled, QPalette.ButtonText, QColor("#888888"))
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


def _apply_menu_stylesheet(application):
    """深色主题下覆盖菜单文字色，避免 Windows 原生风格画出黑底黑字；浅色保持原生外观。"""
    palette = application.palette()
    dark = palette.color(QPalette.Window).lightness() < 128
    stylesheet = _menu_stylesheet(palette) if dark else ""
    if application.styleSheet() == stylesheet:
        return
    application.setStyleSheet(stylesheet)
    logging.getLogger("screensnap").debug(
        "菜单样式刷新: %s", "深色" if dark else "浅色（原生）")


def system_theme_name(application):
    """Return the current Qt platform color scheme as a stable mode name."""
    scheme = application.styleHints().colorScheme()
    if scheme == Qt.ColorScheme.Dark:
        return "dark"
    return "light"