"""Small text and single-path input widgets."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from html import escape
from pathlib import Path
from typing import Literal

from PySide6.QtCore import QEvent, Signal
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import QLineEdit, QMenu, QTextEdit, QToolTip

from foundation.path import normalize_path, path_text_from_input

from .path_support import (
    directory_for_path,
    local_paths_from_mime,
    open_in_standard_file_manager,
)
from ..layout_policy import set_text_rows


class LineListInput(QTextEdit):
    """Multiline input where each non-blank line is one logical text item."""

    def __init__(self, *, rows: int = 6) -> None:
        super().__init__()
        set_text_rows(self, minimum=min(3, rows), maximum=max(3, rows))

    def items(self, *, deduplicate: bool = False) -> list[str]:
        """Return trimmed, non-blank lines without altering visible text."""
        values = [line.strip() for line in self.toPlainText().splitlines() if line.strip()]
        return list(dict.fromkeys(values)) if deduplicate else values

    def append_items(self, values: Iterable[str]) -> None:
        """Append non-blank values as complete new lines."""
        additions = [value.strip() for value in values if value.strip()]
        if not additions:
            return
        existing = self.toPlainText().rstrip()
        appended = "\n".join(additions)
        self.setPlainText(f"{existing}\n{appended}" if existing else appended)


class PathLineInput(QLineEdit):
    """One local path, replaced by a dropped path or its directory."""

    dropRejected = Signal(str)

    def __init__(
        self,
        text: str = "",
        *,
        drop_as: Literal["directory", "full_path"] = "full_path",
        drop_transform: Callable[[Path], Path] | None = None,
    ) -> None:
        super().__init__(text)
        self._drop_as = drop_as
        self._drop_transform = drop_transform
        self._context_menu_augmenter: Callable[[QMenu], None] | None = None
        self.setAcceptDrops(True)

    def event(self, event):
        if event.type() == QEvent.Type.ToolTip and self.text():
            # Preserve instructions as well as the exact, unelided path.
            text = escape(self.text())
            if self.toolTip():
                text += "<br><br>" + escape(self.toolTip())
            QToolTip.showText(event.globalPos(), text, self)
            return True
        return super().event(event)

    def path(self) -> Path | None:
        """Return the entered path, or ``None`` for a blank input."""
        # Whitespace is a valid first or last character of a filesystem name.
        # Only the truly empty field means no path.
        text = path_text_from_input(self.text())
        return normalize_path(text) if text else None

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802
        if event.mimeData().hasUrls() or event.mimeData().hasText():
            event.acceptProposedAction()
        else:
            event.ignore()

    def set_context_menu_augmenter(self, augmenter: Callable[[QMenu], None] | None) -> None:
        """Append owner-specific actions while retaining normal text actions."""
        self._context_menu_augmenter = augmenter

    def _build_context_menu(self) -> QMenu:
        """Build the normal line-edit menu plus any owner-provided actions."""
        menu = self.createStandardContextMenu()
        if menu.actions():
            menu.addSeparator()
        open_action = menu.addAction("標準ファイルマネージャーで開く")
        open_action.setToolTip("入力パスの場所を開きます。空欄・存在しないパスではホームを開きます。")
        open_action.triggered.connect(lambda: open_in_standard_file_manager(self.path()))
        if self._context_menu_augmenter is not None:
            self._context_menu_augmenter(menu)
        return menu

    def contextMenuEvent(self, event) -> None:  # type: ignore[no-untyped-def, N802]
        self._build_context_menu().exec(event.globalPos())

    def set_path_from_external_value(self, path: Path) -> Path:
        """Apply one external or registered path using this input's drop policy."""
        dropped = normalize_path(path)
        value = (
            self._drop_transform(dropped)
            if self._drop_transform is not None
            else directory_for_path(dropped)
            if self._drop_as == "directory"
            else dropped
        )
        self.setText(str(value))
        return value

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802
        paths = local_paths_from_mime(event.mimeData())
        if not paths:
            self.dropRejected.emit("ローカルのファイルまたはフォルダのパスだけを受け付けます。")
            event.ignore()
            return
        self.set_path_from_external_value(paths[0])
        event.acceptProposedAction()
