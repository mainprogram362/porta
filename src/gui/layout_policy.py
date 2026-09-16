"""Shared semantic sizing helpers based on the active font and style."""
from PySide6.QtCore import QSize
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import QStyle


def usable_window_floor(widget, *, text_columns: int = 52, text_rows: int = 18) -> QSize:
    metrics = QFontMetrics(widget.font())
    return bounded_to_available(
        widget, QSize(metrics.horizontalAdvance("0") * text_columns, metrics.lineSpacing() * text_rows)
    )


def preferred_window_size(widget, *, text_columns: int = 72, text_rows: int = 25) -> QSize:
    return usable_window_floor(widget, text_columns=text_columns, text_rows=text_rows)


def bounded_to_available(widget, size: QSize) -> QSize:
    """Keep a font-derived request on screen; overflow remains scrollable."""
    screen = widget.screen()
    if screen is None:
        return size
    available = screen.availableGeometry().size()
    return QSize(min(size.width(), available.width() * 9 // 10),
                 min(size.height(), available.height() * 9 // 10))


def text_area_height(widget, rows: int) -> int:
    """Measure visible text rows using the widget's actual font and frame."""
    document_margin = int(widget.document().documentMargin() * 2) if hasattr(widget, "document") else 0
    frame = widget.frameWidth() * 2 if hasattr(widget, "frameWidth") else 0
    return widget.fontMetrics().lineSpacing() * rows + document_margin + frame


def set_text_rows(widget, *, minimum: int, maximum: int | None = None) -> None:
    widget.setMinimumHeight(text_area_height(widget, minimum))
    if maximum is not None:
        widget.setMaximumHeight(text_area_height(widget, maximum))


def item_view_height(widget, rows: int, *, header=True) -> int:
    row = widget.fontMetrics().lineSpacing() + widget.style().pixelMetric(QStyle.PixelMetric.PM_FocusFrameVMargin) * 2
    heading = widget.header().sizeHint().height() if header and hasattr(widget, "header") else 0
    return row * rows + heading + widget.frameWidth() * 2


def set_item_view_rows(widget, *, minimum: int, maximum: int | None = None, header=True) -> None:
    widget.setMinimumHeight(item_view_height(widget, minimum, header=header))
    if maximum is not None:
        widget.setMaximumHeight(item_view_height(widget, maximum, header=header))
