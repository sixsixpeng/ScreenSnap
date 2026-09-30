"""Application-wide palette selection while preserving native Qt widget styles."""

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette


def apply_theme(application, mode):
    """Apply the requested palette; system mode returns to the platform palette."""
    application.setProperty("screensnap_theme_mode", mode)
    hints = application.styleHints()
    if not application.property("screensnap_theme_listener"):
        signal = getattr(hints, "colorSchemeChanged", None)
        if signal is not None:
            signal.connect(lambda *_: _apply_system_palette(application))
            application.setProperty("screensnap_theme_listener", True)
    if mode == "system":
        _apply_system_palette(application)
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


def _apply_system_palette(application):
    if application.property("screensnap_theme_mode") == "system":
        # Windows 11's style standardPalette() can return the legacy beige palette.
        application.setPalette(QPalette())


def system_theme_name(application):
    """Return the current Qt platform color scheme as a stable mode name."""
    scheme = application.styleHints().colorScheme()
    if scheme == Qt.ColorScheme.Dark:
        return "dark"
    return "light"