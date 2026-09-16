"""Detached read-only candidate details for Media Information."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from media.file_attributes import MediaItem

from .workflow import editable_attribute_rows


_DETACHED_WINDOWS: list[QDialog] = []


def _selected_table_text(table: QTableWidget) -> str:
    """Copy selected cells as a compact TSV block, preserving table positions."""
    indexes = table.selectedIndexes()
    if not indexes:
        return ""
    rows = sorted({index.row() for index in indexes})
    columns = sorted({index.column() for index in indexes})
    selected = {(index.row(), index.column()) for index in indexes}
    return "\n".join(
        "\t".join(
            table.item(row, column).text()
            if (row, column) in selected and table.item(row, column) is not None
            else ""
            for column in columns
        )
        for row in rows
    )


def _show_expanded_value(parent: QDialog, *, label: str, value: str) -> QDialog:
    """Show one untruncated value in a selectable, resizable reader."""
    dialog = QDialog(parent)
    dialog.setWindowTitle(f"詳細値: {label}")
    layout = QVBoxLayout(dialog)
    layout.addWidget(QLabel(label))
    text = QPlainTextEdit(value)
    text.setReadOnly(True)
    text.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
    text.setToolTip("内容を選択してコピーできます。")
    layout.addWidget(text, 1)
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
    copy_button = buttons.addButton("内容をコピー", QDialogButtonBox.ButtonRole.ActionRole)
    copy_button.clicked.connect(lambda: QGuiApplication.clipboard().setText(value))
    buttons.rejected.connect(dialog.reject)
    buttons.accepted.connect(dialog.accept)
    layout.addWidget(buttons)
    dialog.exec()
    return dialog


def show_detached_item_details(item: MediaItem) -> QDialog:
    """Open an independent attribute snapshot with no workbench callbacks."""
    dialog = QDialog()
    dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
    dialog.setModal(False)
    dialog.setWindowTitle("詳細項目（読み取り専用）")
    layout = QVBoxLayout(dialog)
    layout.addWidget(QLabel("対象パス（確認用。JSONには保存しません）"))
    path_input = QLineEdit(str(item.path) if item.path is not None else "仮登録（実在パスなし）")
    path_input.setReadOnly(True)
    layout.addWidget(path_input)
    layout.addWidget(QLabel("この項目が持つJSON属性"))
    table = QTableWidget(0, 5)
    table.setHorizontalHeaderLabels(["項目", "値", "型", "出所", "編集可否"])
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectItems)
    table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
    table.setAlternatingRowColors(True)
    table.horizontalHeader().setStretchLastSection(False)
    table.horizontalHeader().setSectionResizeMode(0, table.horizontalHeader().ResizeMode.Stretch)
    table.horizontalHeader().setSectionResizeMode(1, table.horizontalHeader().ResizeMode.Stretch)
    for column in (2, 3, 4):
        table.horizontalHeader().setSectionResizeMode(
            column, table.horizontalHeader().ResizeMode.ResizeToContents
        )
    rows = editable_attribute_rows(item)
    table.setRowCount(len(rows))
    for row_index, values in enumerate(rows):
        for column, value in enumerate(values):
            cell = QTableWidgetItem(value)
            if column in {0, 1}:
                cell.setToolTip(value)
            table.setItem(row_index, column, cell)
    layout.addWidget(table, 1)
    actions = QHBoxLayout()
    copy_button = QPushButton("選択セルをコピー")
    copy_button.setToolTip("選択したセルを表形式でクリップボードへコピーします。Ctrl+C でもコピーできます。")

    def copy_selected() -> None:
        text = _selected_table_text(table)
        if text:
            QGuiApplication.clipboard().setText(text)

    copy_button.clicked.connect(copy_selected)
    QShortcut(QKeySequence.StandardKey.Copy, table).activated.connect(copy_selected)
    expand_button = QPushButton("選択セルを展開…")
    expand_button.setToolTip("長い内容を別の大きな画面で確認します。セルをダブルクリックしても開けます。")

    def expand_selected() -> None:
        selected = table.selectedItems()
        if not selected:
            return
        selected_item = selected[0]
        header = table.horizontalHeaderItem(selected_item.column())
        label = header.text() if header is not None else "値"
        _show_expanded_value(dialog, label=label, value=selected_item.text())

    expand_button.clicked.connect(expand_selected)
    table.itemDoubleClicked.connect(lambda _cell: expand_selected())

    def show_table_context_menu(position) -> None:  # type: ignore[no-untyped-def]
        cell = table.itemAt(position)
        if cell is None:
            return
        table.clearSelection()
        cell.setSelected(True)
        table.setCurrentItem(cell)
        menu = QMenu(table)
        copy_action = menu.addAction("このセルをコピー")
        copy_action.triggered.connect(copy_selected)
        expand_action = menu.addAction("このセルを展開…")
        expand_action.triggered.connect(expand_selected)
        menu.exec(table.viewport().mapToGlobal(position))

    table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
    table.customContextMenuRequested.connect(show_table_context_menu)
    actions.addWidget(copy_button)
    actions.addWidget(expand_button)
    actions.addStretch(1)
    close_button = QPushButton("閉じる")
    close_button.clicked.connect(dialog.close)
    actions.addWidget(close_button)
    layout.addLayout(actions)

    _DETACHED_WINDOWS.append(dialog)

    def forget_dialog(_destroyed: object = None) -> None:
        if dialog in _DETACHED_WINDOWS:
            _DETACHED_WINDOWS.remove(dialog)

    dialog.destroyed.connect(forget_dialog)
    dialog.show()
    dialog.raise_()
    dialog.activateWindow()
    return dialog
