"""Helpers for localized confirmation dialogs."""

from PySide6.QtWidgets import QMessageBox


def yes_no_dialog(parent, title, text, default=QMessageBox.No):
    """Build a confirmation box with explicit Chinese Yes/No labels."""
    dialog = QMessageBox(
        QMessageBox.Icon.Question,
        title,
        text,
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        parent,
    )
    dialog.setDefaultButton(default)
    dialog.button(QMessageBox.StandardButton.Yes).setText("是")
    dialog.button(QMessageBox.StandardButton.No).setText("否")
    return dialog