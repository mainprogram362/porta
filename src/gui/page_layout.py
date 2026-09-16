"""Compact page-level layout shared by application screens."""

from __future__ import annotations

from PySide6.QtWidgets import QVBoxLayout, QWidget


class AppPageLayout(QVBoxLayout):
    """Use one predictable outer margin and rhythm for every tool page."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setContentsMargins(10, 8, 10, 10)
        self.setSpacing(6)

    def heightForWidth(self, width: int) -> int:  # noqa: N802
        # Scroll viewports ask for this as a lower bound. Text editors and
        # trees may shrink below their preferred height; wrapped labels may not.
        # Qt's minimumHeightForWidth calls the virtual heightForWidth to fill
        # its cache first. Use the base implementation during that call.
        if getattr(self, "_measuring_minimum_height", False):
            return super().heightForWidth(width)
        self._measuring_minimum_height = True
        try:
            return self.minimumHeightForWidth(width)
        finally:
            self._measuring_minimum_height = False
