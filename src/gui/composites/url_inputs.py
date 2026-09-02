"""Reusable URL-only text inputs with additive external drag and drop."""

from __future__ import annotations

from urllib.parse import urlparse

from PySide6.QtCore import QMimeData, Signal
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import QPlainTextEdit


def _urls_from_mime(mime: QMimeData) -> list[str]:
    values = [url.toString().strip() for url in mime.urls()]
    if not values and mime.hasText():
        values = [line.strip() for line in mime.text().splitlines()]
    return [
        value
        for value in values
        if urlparse(value).scheme.casefold() in {"http", "https"} and urlparse(value).netloc
    ]


class UrlListTextEdit(QPlainTextEdit):
    """Append dropped web URLs as new lines without replacing current input."""

    dropRejected = Signal(str)

    def append_urls(self, urls: list[str]) -> None:
        """Append valid URLs after the current last line."""
        accepted = [
            value.strip()
            for value in urls
            if urlparse(value.strip()).scheme.casefold() in {"http", "https"}
            and urlparse(value.strip()).netloc
        ]
        if not accepted:
            self.dropRejected.emit("http または https のURLだけを追加できます。")
            return
        current = self.toPlainText().rstrip()
        self.setPlainText("\n".join((current, *accepted)) if current else "\n".join(accepted))
        cursor = self.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        self.setTextCursor(cursor)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802
        if _urls_from_mime(event.mimeData()):
            event.acceptProposedAction()
            return
        event.ignore()

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802
        urls = _urls_from_mime(event.mimeData())
        if not urls:
            self.dropRejected.emit("http または https のURLだけを追加できます。")
            event.ignore()
            return
        self.append_urls(urls)
        event.acceptProposedAction()
