"""A content-sized grid which chooses its column count from available width."""
from __future__ import annotations

import math
from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtWidgets import QLayout


class ResponsiveGridLayout(QLayout):
    """Lay out controls in as many columns as their size hints permit."""

    def __init__(self, parent=None, *, maximum_columns: int | None = None, spacing: int = 6) -> None:
        super().__init__(parent)
        self._items = []
        self._maximum_columns = maximum_columns
        self.setSpacing(spacing)

    def addItem(self, item) -> None:
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index):
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index):
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientation.Horizontal

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self._arrange(QRect(0, 0, width, 0), measure=True)

    def setGeometry(self, rect: QRect) -> None:
        super().setGeometry(rect)
        self._arrange(rect, measure=False)

    def minimumSize(self) -> QSize:
        items = self._visible_items()
        if not items:
            return QSize()
        left, top, right, bottom = self.getContentsMargins()
        return QSize(max(item.minimumSize().width() for item in items) + left + right,
                     max(item.minimumSize().height() for item in items) + top + bottom)

    def sizeHint(self) -> QSize:
        items = self._visible_items()
        if not items:
            return self.minimumSize()
        columns = min(len(items), self._maximum_columns or len(items))
        cell_width = max(item.sizeHint().width() for item in items)
        heights = self._row_heights(items, columns, cell_width)
        left, top, right, bottom = self.getContentsMargins()
        return QSize(columns * cell_width + (columns - 1) * self.spacing() + left + right,
                     sum(heights) + (len(heights) - 1) * self.spacing() + top + bottom)

    def _visible_items(self):
        return [item for item in self._items if not item.isEmpty()]

    def _columns_for_width(self, items, width: int) -> int:
        maximum = min(len(items), self._maximum_columns or len(items))
        required = max(max(item.minimumSize().width(), item.sizeHint().width()) for item in items)
        return max(1, min(maximum, (max(0, width) + self.spacing()) // (required + self.spacing())))

    @staticmethod
    def _item_height(item, width: int) -> int:
        widget = item.widget()
        if widget is not None and widget.hasHeightForWidth():
            measured = widget.heightForWidth(width)
            if measured >= 0:
                return max(item.minimumSize().height(), measured)
        return max(item.minimumSize().height(), item.sizeHint().height())

    def _row_heights(self, items, columns: int, cell_width: int):
        return [max(self._item_height(item, cell_width) for item in items[start:start + columns])
                for start in range(0, len(items), columns)]

    def _arrange(self, rect: QRect, *, measure: bool) -> int:
        items = self._visible_items()
        left, top, right, bottom = self.getContentsMargins()
        if not items:
            return top + bottom
        available = max(0, rect.width() - left - right)
        columns = self._columns_for_width(items, available)
        cell_width = max(0, (available - (columns - 1) * self.spacing()) // columns)
        heights = self._row_heights(items, columns, cell_width)
        y = rect.y() + top
        for index, item in enumerate(items):
            row, column = divmod(index, columns)
            if not measure:
                x = rect.x() + left + column * (cell_width + self.spacing())
                item.setGeometry(QRect(x, y, cell_width, heights[row]))
            if column == columns - 1 or index == len(items) - 1:
                y += heights[row] + self.spacing()
        return y - rect.y() - self.spacing() + bottom
