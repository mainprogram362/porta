"""Combo box behavior shared by dense mouse-driven screens."""

from __future__ import annotations

from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QComboBox


class NoWheelComboBox(QComboBox):
    """A combo box whose selection cannot change through mouse-wheel scrolling."""

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802
        event.ignore()
