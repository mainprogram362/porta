"""A splitter which reports wrapped child height to a scroll viewport."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QSplitter


class AdaptiveSplitter(QSplitter):
    def hasHeightForWidth(self) -> bool:  # noqa: N802
        return any(widget.isVisible() and widget.hasHeightForWidth()
                   for widget in (self.widget(index) for index in range(self.count())))

    @staticmethod
    def _child_height(widget, width: int) -> int:
        if widget.hasHeightForWidth():
            measured = widget.heightForWidth(width)
            if measured >= 0:
                return max(widget.minimumSizeHint().height(), measured)
        return widget.minimumSizeHint().height()

    def heightForWidth(self, width: int) -> int:  # noqa: N802
        widgets = [self.widget(index) for index in range(self.count()) if self.widget(index).isVisible()]
        if not widgets:
            return 0
        handles = self.handleWidth() * max(0, len(widgets) - 1)
        available = max(0, width - handles)
        if self.orientation() == Qt.Orientation.Vertical:
            return sum(self._child_height(widget, available) for widget in widgets) + handles

        current = [max(0, widget.width()) for widget in widgets]
        total = sum(current)
        if total <= 0:
            current = [max(1, widget.sizeHint().width()) for widget in widgets]
            total = sum(current)
        widths = [max(1, available * value // total) for value in current]
        return max(self._child_height(widget, child_width)
                   for widget, child_width in zip(widgets, widths))
