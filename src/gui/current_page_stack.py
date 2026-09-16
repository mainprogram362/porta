"""Size a retained-page stack from the visible page, not hidden workspaces."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QSize
from PySide6.QtWidgets import QFrame, QLayout, QScrollArea, QStackedWidget


class CurrentPageStack(QStackedWidget):
    """Keep work alive while letting smaller pages use a smaller window.

    QStackedLayout normally combines every page's size requirements. Disable
    its automatic minimum on the stack itself and expose only the current
    page's requirements to the parent layout. The page's own limits remain.
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.layout().setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        self.currentChanged.connect(self._current_changed)

    def _current_changed(self, _index: int) -> None:
        self.updateGeometry()

    def _with_margins(self, size: QSize) -> QSize:
        margins = self.contentsMargins()
        return size + QSize(margins.left() + margins.right(), margins.top() + margins.bottom())

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        page = self.currentWidget()
        if page is None:
            return self._with_margins(QSize(0, 0))
        return self._with_margins(page.minimumSizeHint().expandedTo(page.minimumSize()))

    def sizeHint(self) -> QSize:  # noqa: N802
        page = self.currentWidget()
        if page is None:
            return self.minimumSizeHint()
        return self._with_margins(page.sizeHint()).expandedTo(self.minimumSizeHint())

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        page = self.currentWidget()
        return page is not None and page.hasHeightForWidth()

    def heightForWidth(self, width: int) -> int:  # noqa: N802
        page = self.currentWidget()
        if page is None or not page.hasHeightForWidth():
            return -1
        margins = self.contentsMargins()
        height = page.heightForWidth(max(0, width - margins.left() - margins.right()))
        if height < 0:
            return -1
        return max(self.minimumSizeHint().height(), height + margins.top() + margins.bottom())


class PageScrollArea(QScrollArea):
    def setWidget(self, page):  # noqa: N802
        self._page_floor = page.minimumSize()
        self._update_page_minimum(page)
        super().setWidget(page)
        page.installEventFilter(self)

    def _update_page_minimum(self, page):
        layout = page.layout()
        required = self._page_floor
        if layout is not None:
            required = required.expandedTo(layout.minimumSize())
        width = max(required.width(), self.viewport().width())
        if page.hasHeightForWidth():
            height = page.heightForWidth(width)
            if height >= 0:
                required = required.expandedTo(QSize(required.width(), height))
        if page.minimumSize() != required:
            page.setMinimumSize(required)

    def eventFilter(self, watched, event):  # noqa: N802
        if watched is self.widget() and event.type() == QEvent.Type.LayoutRequest:
            self._update_page_minimum(watched)
        return super().eventFilter(watched, event)

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        page = self.widget()
        if page is not None:
            self._update_page_minimum(page)


class ScrollablePageStack(CurrentPageStack):
    """Retain pages and their scroll positions without forcing window growth.

    Public page access still returns the actual tool, so workspace switching
    and explicit handoffs do not need to know about the viewport.
    """

    def addWidget(self, page):  # noqa: N802
        area = PageScrollArea()
        area.setFrameShape(QFrame.Shape.NoFrame)
        area.setWidgetResizable(True)
        area.setWidget(page)
        return QStackedWidget.addWidget(self, area)

    def widget(self, index):
        area = QStackedWidget.widget(self, index)
        return area.widget() if area is not None else None

    def currentWidget(self):  # noqa: N802
        return self.widget(self.currentIndex())

    def indexOf(self, page):  # noqa: N802
        for index in range(self.count()):
            if self.widget(index) is page:
                return index
        return -1

    def setCurrentWidget(self, page):  # noqa: N802
        index = self.indexOf(page)
        if index >= 0:
            self.setCurrentIndex(index)

    def removeWidget(self, page):  # noqa: N802
        index = self.indexOf(page)
        if index >= 0:
            area = QStackedWidget.widget(self, index)
            area.takeWidget()
            QStackedWidget.removeWidget(self, area)
            area.deleteLater()

    def minimumSizeHint(self):  # noqa: N802
        return QSize(320, 240)

    def hasHeightForWidth(self):  # noqa: N802
        return False

    def heightForWidth(self, width):  # noqa: N802
        return -1
