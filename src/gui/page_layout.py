"""Compact page-level layout shared by application screens."""

from __future__ import annotations

from PySide6.QtWidgets import QVBoxLayout, QWidget


class AppPageLayout(QVBoxLayout):
    """Use one predictable outer margin and rhythm for every tool page."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setContentsMargins(10, 8, 10, 10)
        self.setSpacing(6)
