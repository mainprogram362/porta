"""A toolbar whose controls wrap instead of imposing their combined width."""

from PySide6.QtWidgets import QToolBar, QWidget

from .flow_layout import FlowLayout


class WrappingToolBar(QToolBar):
    def __init__(self, title: str, parent=None) -> None:
        super().__init__(title, parent)
        self._content = QWidget(self)
        self._flow = FlowLayout(self._content)
        super().addWidget(self._content)

    def add_control(self, widget) -> None:
        self._flow.addWidget(widget)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        # QToolBar itself does not honor a child's height-for-width. Feed the
        # measured wrapped height back after its inner geometry is allocated.
        chrome = max(0, self.height() - self._content.height())
        required = self._flow.heightForWidth(max(0, self._content.width())) + chrome
        if required > 0 and self.minimumHeight() != required:
            self.setMinimumHeight(required)
            self.updateGeometry()

    def wrapped_height_for_width(self, width: int) -> int:
        chrome_width = max(0, self.width() - self._content.width())
        chrome_height = max(0, self.height() - self._content.height())
        return self._flow.heightForWidth(max(0, width - chrome_width)) + chrome_height
