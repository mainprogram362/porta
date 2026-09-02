"""Composable text inputs and selectable local-path tables."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Literal

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtGui import (
    QDragEnterEvent,
    QDragMoveEvent,
    QDropEvent,
    QColor,
    QBrush,
    QIcon,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from foundation.path import normalize_path
from foundation.path_inspection import PathInfo, inspect_path

from .line_inputs import LineListInput, PathLineInput
from .no_wheel_combo import NoWheelComboBox
from .path_roles import (
    INPUT_ROW_ROLE as _INPUT_ROW_ROLE,
    ROW_TOKEN_ROLE as _ROW_TOKEN_ROLE,
    SELECTION_ROLE as _SELECTION_ROLE,
    VIRTUAL_ROW_ROLE as _VIRTUAL_ROW_ROLE,
)
from .path_support import (
    direct_child_counts as _direct_child_counts,
    directory_for_path,
    local_paths_from_mime,
    open_in_standard_file_manager,
    standard_file_manager_directory,
)
from .path_tree import InputRowDelegate, PathTreeWidget

__all__ = [
    "LineListInput",
    "PathLineInput",
    "PathListInput",
    "add_directory_path_input",
    "add_line_list_input",
    "add_path_list_input",
    "directory_for_path",
    "local_paths_from_mime",
    "open_in_standard_file_manager",
    "standard_file_manager_directory",
]

_ICON_DIRECTORY = Path(__file__).resolve().parents[1] / "assets" / "icons"
_STATE_ICON_NAMES = {
    "file": "file.svg",
    "directory": "folder.svg",
    "other": "other.svg",
    "symlink_file": "link_file.svg",
    "symlink_directory": "link_folder.svg",
    "symlink_other": "link_other.svg",
    "symlink_broken": "link_broken.svg",
    "missing": "missing.svg",
    "unavailable": "unavailable.svg",
}
_STATE_LABELS = {
    "file": "ファイル",
    "directory": "フォルダ",
    "other": "その他",
    "symlink_file": "リンク（ファイル）",
    "symlink_directory": "リンク（フォルダ）",
    "symlink_other": "リンク（その他）",
    "symlink_broken": "壊れたリンク：リンク先が見つかりません",
    "missing": "存在しない：このパスは見つかりません",
    "unavailable": "確認不可：権限または接続を確認してください",
}
_CHANGE_MARK_COLORS = {
    "added": QColor("#d9f4df"),
    "removed": QColor("#f8dddd"),
    "changed": QColor("#fff1c9"),
}
_CHANGE_MARK_LABELS = {
    "added": "追加予定",
    "removed": "削除予定",
    "changed": "変更予定",
}


class PathListInput(QWidget):
    """Editable path rows with checkbox-based multi-selection and state display."""

    textChanged = Signal()
    selectionChanged = Signal()
    operationTargetsChanged = Signal()
    rowSelectionChanged = Signal()
    actionPerformed = Signal(str)
    itemClicked = Signal(object, int)
    itemDoubleClicked = Signal(object, int)

    def __init__(
        self,
        *,
        rows: int = 6,
        accepted_path_kind: Literal["all", "directory"] = "all",
        drop_replaces: bool = False,
        maximum_items: int | None = None,
        show_controls: bool = True,
        supplemental_column_label: str = "",
        supplemental_columns: tuple[str, ...] = (),
        path_column_resizable: bool = False,
        stretch_supplemental_column_label: str = "",
        show_operation_targets: bool = False,
        supplemental_before_operation_targets: bool = False,
        path_column_label: str = "パス",
        show_column_headers: bool = False,
        context_menu_selection_actions: bool = True,
        context_menu_operation_target_actions: bool = True,
        context_menu_copy_actions: bool = True,
        double_click_directory_selection: bool = False,
        double_click_directory_replaces_all: bool = False,
        enable_row_selection: bool = False,
        selection_editable_when_locked: bool = False,
        direct_child_filter: Callable[[Path], bool] | None = None,
    ) -> None:
        super().__init__()
        if supplemental_column_label and supplemental_columns:
            raise ValueError("補助列は supplemental_column_label または supplemental_columns のどちらかで指定してください。")
        if maximum_items is not None and maximum_items < 1:
            raise ValueError("maximum_items は 1 以上、または None を指定してください。")
        self.setAcceptDrops(True)
        self.setToolTip(
            "外部からドロップすると一覧を置換します。"
            if drop_replaces
            else "外部からドロップすると一覧へ追加します。"
        )
        self._accepted_path_kind = accepted_path_kind
        self.drop_replaces = drop_replaces
        self._maximum_items = maximum_items
        self._paths_locked = False
        self._show_operation_targets = show_operation_targets
        self._context_menu_selection_actions = context_menu_selection_actions
        self._context_menu_operation_target_actions = context_menu_operation_target_actions
        self._context_menu_copy_actions = context_menu_copy_actions
        self._double_click_directory_selection = double_click_directory_selection
        self._double_click_directory_replaces_all = double_click_directory_replaces_all
        self._enable_row_selection = enable_row_selection
        self._selection_editable_when_locked = selection_editable_when_locked
        self._direct_child_filter = direct_child_filter
        self._path_column_resizable = path_column_resizable
        self._path_column_label = path_column_label or "パス"
        self._supplemental_labels = (
            (supplemental_column_label,)
            if supplemental_column_label
            else tuple(label for label in supplemental_columns if label)
        )
        supplemental_start = 2
        self._operation_target_column = (
            supplemental_start + len(self._supplemental_labels)
            if show_operation_targets and supplemental_before_operation_targets
            else (2 if show_operation_targets else None)
        )
        if show_operation_targets and not supplemental_before_operation_targets:
            supplemental_start = 3
        self._supplemental_columns = {
            label: supplemental_start + index for index, label in enumerate(self._supplemental_labels)
        }
        self._supplemental_column = next(iter(self._supplemental_columns.values()), None)
        if stretch_supplemental_column_label and stretch_supplemental_column_label not in self._supplemental_columns:
            raise ValueError("伸長する補助列は supplemental_columns に含めてください。")
        self._stretch_supplemental_column_label = stretch_supplemental_column_label
        self._state_column = max(
            [1, *self._supplemental_columns.values(), self._operation_target_column or 1]
        ) + 1
        self._remove_column = self._state_column + 1
        self._normalizing = False
        self._context_menu_augmenter: Callable[[QMenu, QTreeWidgetItem | None], None] | None = None
        self._exclusive_context_menu_builder: Callable[
            [QTreeWidgetItem | None, int], QMenu | None
        ] | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._tree = PathTreeWidget(self)
        self._tree.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
            if enable_row_selection
            else QAbstractItemView.SelectionMode.NoSelection
        )
        self._tree.itemSelectionChanged.connect(self.rowSelectionChanged)
        self._refresh_edit_triggers()
        sort_controls = QHBoxLayout()
        sort_controls.setContentsMargins(0, 0, 0, 0)
        self.sort_combo = NoWheelComboBox()
        self.sort_combo.addItem("手動順", "manual")
        self.sort_combo.addItem("名前 ↑", "name_asc")
        self.sort_combo.addItem("名前 ↓", "name_desc")
        self.sort_combo.addItem("パス ↑", "path_asc")
        self.sort_combo.addItem("パス ↓", "path_desc")
        self.sort_combo.addItem("状態順", "state")
        self.sort_combo.setToolTip("この一覧だけを並べ替えます。")
        sort_controls.addWidget(self.sort_combo, 1)
        sort_button = QPushButton("ソート")
        sort_button.setToolTip("選択した方法でこの一覧を並べ替えます。")
        sort_button.clicked.connect(self.sort_items)
        sort_controls.addWidget(sort_button)
        self.selection_toggle_button = QPushButton()
        self.selection_toggle_button.clicked.connect(self.toggle_item_selection)
        sort_controls.addWidget(self.selection_toggle_button)
        clear_items_button = QPushButton("空にする")
        clear_items_button.setToolTip("この一覧の全行を除外します。実ファイル・フォルダは削除しません。")
        clear_items_button.clicked.connect(self.clear_items)
        sort_controls.addWidget(clear_items_button)
        if show_controls:
            layout.addLayout(sort_controls)
        self._tree.setColumnCount(self._remove_column + 1)
        # The first and final columns contain self-explanatory controls (check
        # and ×), so keep both headers blank and give data columns the space.
        headers = ["", self._path_column_label]
        if supplemental_before_operation_targets:
            headers.extend(self._supplemental_labels)
        if show_operation_targets:
            headers.append("操作対象")
        if not supplemental_before_operation_targets:
            headers.extend(self._supplemental_labels)
        headers.extend(["", ""])
        self._tree.setHeaderLabels(headers)
        self._tree.setRootIsDecorated(False)
        self._tree.setTextElideMode(Qt.TextElideMode.ElideLeft)
        self._tree.setItemDelegateForColumn(1, InputRowDelegate(self._tree))
        header = self._tree.header()
        # Column order is structural.  Allow width adjustment, but never let
        # dragging a header reorder another column unexpectedly.
        header.setSectionsMovable(False)
        header.setCascadingSectionResizes(False)
        # The selection control is intentionally compact.  Qt's default
        # minimum section size is wider than this control, so lower it here.
        header.setMinimumSectionSize(24)
        # QTreeWidget stretches the final column by default.  Here the final
        # column is only the × control, so that behaviour would waste path
        # space; only the path column may consume remaining width.
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        if self._operation_target_column is not None:
            header.setSectionResizeMode(self._operation_target_column, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(
            1,
            QHeaderView.ResizeMode.Interactive
            if path_column_resizable
            else QHeaderView.ResizeMode.Stretch,
        )
        if path_column_resizable:
            self._tree.setColumnWidth(1, 300)
        # Selection, status and the UI-only removal control must not steal width from
        # the actual path, which is the only column that benefits from growth.
        for label, column in self._supplemental_columns.items():
            header.setSectionResizeMode(
                column,
                QHeaderView.ResizeMode.Stretch
                if label == self._stretch_supplemental_column_label
                or (
                    len(self._supplemental_columns) == 1
                    and not path_column_resizable
                    and not supplemental_before_operation_targets
                )
                else QHeaderView.ResizeMode.Interactive,
            )
            if len(self._supplemental_columns) > 1 or supplemental_before_operation_targets:
                self._tree.setColumnWidth(column, 120)
        header.setSectionResizeMode(self._state_column, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(self._remove_column, QHeaderView.ResizeMode.Fixed)
        self._tree.setColumnWidth(0, 24)
        if self._operation_target_column is not None:
            self._tree.setColumnWidth(self._operation_target_column, 50)
        self._tree.setColumnWidth(self._state_column, 30)
        self._tree.setColumnWidth(self._remove_column, 34)
        # Most path inputs remain compact without labels.  Information-dense
        # lists can opt in so values such as "1920×1080" are not ambiguous.
        header_height = 26 if show_column_headers else 0
        self._tree.setMinimumHeight(max(30, rows * 24 + header_height))
        self._tree.setHeaderHidden(not show_column_headers)
        column_description = "左から：チェック、パス"
        if self._operation_target_column is not None:
            column_description += "、実行対象"
        self._tree.setToolTip(column_description + "、状態、一覧から除外。")
        self._tree.itemChanged.connect(self._on_item_changed)
        self._tree.itemClicked.connect(self._on_tree_item_clicked)
        self._tree.itemDoubleClicked.connect(self._on_tree_item_double_clicked)
        layout.addWidget(self._tree)
        self._normalize_input_row()
        self._update_selection_toggle_button()

    def set_maximum_items(self, maximum_items: int | None) -> None:
        """Limit data rows without changing the meaning of each path row.

        A one-item destination can share this widget with multi-destination
        lists.  When the limit becomes smaller, the visible first row is kept
        and later rows are discarded from this transient UI list only.
        """
        if maximum_items is not None and maximum_items < 1:
            raise ValueError("maximum_items は 1 以上、または None を指定してください。")
        if self._maximum_items == maximum_items:
            return
        self._maximum_items = maximum_items
        values = self.items()
        if maximum_items is not None and len(values) > maximum_items:
            self.setPlainText("\n".join(values[:maximum_items]))
            return
        self._normalize_input_row()
        self._update_selection_toggle_button()

    def maximum_items(self) -> int | None:
        """Return the current data-row limit, if this list has one."""
        return self._maximum_items

    def set_drop_replaces(self, replaces: bool) -> None:
        """Choose whether an external drop replaces or appends path rows."""
        self.drop_replaces = replaces
        self.setToolTip(
            "外部からドロップすると一覧を置換します。"
            if replaces
            else "外部からドロップすると一覧へ追加します。"
        )

    def set_visible_rows(self, rows: int, *, fixed: bool = False) -> None:
        """Adjust the compact height while retaining the same path-table API."""
        height = max(30, rows * 24)
        self._tree.setMinimumHeight(height)
        if fixed:
            # A tree row itself fits in ``height`` but its styled frame needs
            # a little room above and below.  Keep the visual one-line form
            # without clipping that frame.
            self.setFixedHeight(height + 4)
            return
        self.setMinimumHeight(height)
        self.setMaximumHeight(16_777_215)

    def set_double_click_directory_selection(self, enabled: bool) -> None:
        """Choose whether a folder double click can replace it with selected children.

        This is deliberately opt-in because some owning screens already use a
        double click for an editor or another domain-specific action.  When it
        is enabled, normal inline path editing remains available with F2 while
        a double click on an ordinary folder opens the child chooser instead.
        """
        if self._double_click_directory_selection == enabled:
            return
        self._double_click_directory_selection = enabled
        self._refresh_edit_triggers()

    def double_click_directory_selection_enabled(self) -> bool:
        """Return whether the opt-in direct-child chooser is active."""
        return self._double_click_directory_selection

    def setPlaceholderText(self, _text: str) -> None:  # noqa: N802
        """Compatibility method; the trailing input row supplies its own hint."""

    def set_context_menu_augmenter(
        self, augmenter: Callable[[QMenu, QTreeWidgetItem | None], None] | None
    ) -> None:
        """Let an owning app append app-specific actions without changing this widget."""
        self._context_menu_augmenter = augmenter

    def set_exclusive_context_menu_builder(
        self,
        builder: Callable[[QTreeWidgetItem | None, int], QMenu | None] | None,
    ) -> None:
        """Optionally replace the normal menu for a specific row and column."""
        self._exclusive_context_menu_builder = builder

    def set_supplemental_texts(self, values: dict[str, str]) -> None:
        """Show app-owned, non-path text beside matching rows when enabled.

        The text is presentation-only. It never changes the path list data,
        checkbox state, drag-and-drop behaviour or any filesystem item.
        """
        if self._supplemental_column is None:
            return
        first_label = self._supplemental_labels[0]
        self.set_supplemental_values(
            {path: {first_label: value} for path, value in values.items()}
        )

    def set_supplemental_values(self, values: dict[str, dict[str, str]]) -> None:
        """Show app-owned values in one or more named supplemental columns.

        Unknown labels are ignored.  Values are presentation-only and never
        alter the path, checkbox state or filesystem data.
        """
        if not self._supplemental_columns:
            return
        normalized = {
            str(normalize_path(path)): row_values
            for path, row_values in values.items()
        }
        for index in range(self._tree.topLevelItemCount()):
            item = self._tree.topLevelItem(index)
            if self._is_input_row(item):
                continue
            path_text = item.text(1).strip()
            token = self.item_token(item)
            row_values = values.get(token)
            if row_values is None:
                row_values = normalized.get(str(normalize_path(path_text)), {})
            for label, column in self._supplemental_columns.items():
                text = str(row_values.get(label, ""))
                if item.text(column) != text:
                    item.setText(column, text)
                if item.toolTip(column) != text:
                    item.setToolTip(column, text)

    def supplemental_column_labels(self) -> tuple[str, ...]:
        """Return the app-owned supplemental column labels in display order."""
        return self._supplemental_labels

    def reorder_items(self, tokens: Iterable[str]) -> None:
        """Reorder visible data rows by token without changing their values.

        Owners use this when their durable in-memory model has been sorted.
        The trailing editable input row and every checkbox/control stay intact.
        """
        requested = [str(token) for token in tokens]
        rows = [
            self._tree.topLevelItem(index)
            for index in range(self._tree.topLevelItemCount())
            if not self._is_input_row(self._tree.topLevelItem(index))
        ]
        by_token = {self.item_token(item): item for item in rows}
        ordered = [by_token.pop(token) for token in requested if token in by_token]
        ordered.extend(item for item in rows if self.item_token(item) in by_token)
        if ordered == rows:
            return
        input_row = next(
            (
                item
                for item in (
                    self._tree.topLevelItem(index)
                    for index in range(self._tree.topLevelItemCount())
                )
                if self._is_input_row(item)
            ),
            None,
        )
        for item in rows:
            self._tree.takeTopLevelItem(self._tree.indexOfTopLevelItem(item))
        if input_row is not None:
            self._tree.takeTopLevelItem(self._tree.indexOfTopLevelItem(input_row))
        for item in ordered:
            self._tree.addTopLevelItem(item)
            self._add_selection_button(item)
            self._add_operation_target_button(item)
            self._add_remove_button(item)
        if input_row is not None:
            self._tree.addTopLevelItem(input_row)

    def set_hidden_item_tokens(self, tokens: Iterable[str]) -> None:
        """Hide rows for filtering without removing them or changing checks."""
        hidden = {str(token) for token in tokens}
        for index in range(self._tree.topLevelItemCount()):
            item = self._tree.topLevelItem(index)
            if self._is_input_row(item):
                continue
            item.setHidden(self.item_token(item) in hidden)

    def visible_item_tokens(self) -> list[str]:
        """Return visible row identities in their current visual order."""
        return [
            self.item_token(item)
            for index in range(self._tree.topLevelItemCount())
            if not self._is_input_row(item := self._tree.topLevelItem(index))
            and not item.isHidden()
        ]

    def set_item_display_text(self, token: str, text: str, *, tooltip: str | None = None) -> None:
        """Change one row's presentation text without changing its identity."""
        for index in range(self._tree.topLevelItemCount()):
            item = self._tree.topLevelItem(index)
            if not self._is_input_row(item) and self.item_token(item) == token:
                item.setText(1, text)
                item.setToolTip(1, text if tooltip is None else tooltip)
                return

    def supplemental_column_index(self, label: str) -> int | None:
        """Return the table column for one app-owned presentation label."""
        return self._supplemental_columns.get(label)

    def set_supplemental_column_visible(self, label: str, visible: bool) -> None:
        """Show or hide one app-owned column without changing its values."""
        column = self._supplemental_columns.get(label)
        if column is not None:
            self._tree.setColumnHidden(column, not visible)

    def set_supplemental_cell_change_kinds(self, values: dict[str, dict[str, str]]) -> None:
        """Mark app-owned cells as pending additions, removals or changes.

        This affects only the presentation of a pending model. Callers keep
        ownership of deciding what counts as a semantic change.
        """
        for index in range(self._tree.topLevelItemCount()):
            item = self._tree.topLevelItem(index)
            if self._is_input_row(item):
                continue
            marks = values.get(self.item_token(item), {})
            for label, column in self._supplemental_columns.items():
                kind = marks.get(label, "")
                color = _CHANGE_MARK_COLORS.get(kind)
                brush = QBrush(color) if color is not None else QBrush()
                if item.background(column) != brush:
                    item.setBackground(column, brush)
                text = item.text(column)
                tooltip = (
                    f"{text}\n読み取り時から: {_CHANGE_MARK_LABELS[kind]}"
                    if kind
                    else text
                )
                if item.toolTip(column) == tooltip:
                    continue
                if kind:
                    item.setToolTip(column, tooltip)
                else:
                    item.setToolTip(column, tooltip)

    def supplemental_column_visible(self, label: str) -> bool:
        """Return whether one app-owned supplemental column is visible."""
        column = self._supplemental_columns.get(label)
        return column is not None and not self._tree.isColumnHidden(column)

    def set_supplemental_column_width(self, label: str, width: int) -> None:
        """Set a practical width for an app-owned supplemental column."""
        column = self._supplemental_columns.get(label)
        if column is not None:
            self._tree.setColumnWidth(column, max(30, width))

    def set_supplemental_column_fixed(self, label: str, width: int | None = None) -> None:
        """Keep one compact app-owned column at the right-side fixed width.

        This is useful for structural facts such as a file/folder kind.  It
        deliberately affects presentation only; paths, check state and input
        behaviour stay unchanged.
        """
        column = self._supplemental_columns.get(label)
        if column is None:
            return
        self._tree.header().setSectionResizeMode(column, QHeaderView.ResizeMode.Fixed)
        if width is not None:
            self._tree.setColumnWidth(column, max(30, width))

    def set_supplemental_widget(self, path: str, label: str, widget: QWidget | None) -> None:
        """Place an app-owned control in one supplemental cell for one path."""
        column = self._supplemental_columns.get(label)
        if column is None:
            return
        normalized = str(normalize_path(path))
        for index in range(self._tree.topLevelItemCount()):
            item = self._tree.topLevelItem(index)
            if self._is_input_row(item):
                continue
            if self.item_token(item) != path and str(normalize_path(item.text(1))) != normalized:
                continue
            old = self._tree.itemWidget(item, column)
            if old is not None:
                self._tree.removeItemWidget(item, column)
                old.deleteLater()
            if widget is not None:
                # A cell widget is the complete visual representation.  Keep
                # the underlying item text empty so Qt never paints it a
                # second time behind that widget.
                item.setText(column, "")
                self._tree.setItemWidget(item, column, widget)
            return

    def set_paths_locked(self, locked: bool) -> None:
        """Lock path editing, dropping, removal and checkbox changes in-place."""
        if self._paths_locked == locked:
            return
        self._paths_locked = locked
        self.setAcceptDrops(not locked)
        self._tree.setAcceptDrops(not locked)
        self._tree.viewport().setAcceptDrops(not locked)
        self._refresh_edit_triggers()
        for index in range(self._tree.topLevelItemCount()):
            item = self._tree.topLevelItem(index)
            flags = item.flags()
            if self._is_input_row(item):
                flags = (
                    flags & ~Qt.ItemFlag.ItemIsEditable
                    if locked
                    else flags | Qt.ItemFlag.ItemIsEditable
                )
            else:
                flags = flags & ~Qt.ItemFlag.ItemIsEditable if locked else flags | Qt.ItemFlag.ItemIsEditable
                selection_button = self._tree.itemWidget(item, 0)
                if selection_button is not None:
                    selection_button.setEnabled(not locked or self._selection_editable_when_locked)
                remove_button = self._tree.itemWidget(item, self._remove_column)
                if remove_button is not None:
                    remove_button.setEnabled(not locked)
            item.setFlags(flags)
        self._update_selection_toggle_button()

    def _refresh_edit_triggers(self) -> None:
        """Keep direct-child selection and inline path editing from competing."""
        if self._paths_locked:
            triggers = QAbstractItemView.EditTrigger.NoEditTriggers
        elif self._double_click_directory_selection:
            triggers = QAbstractItemView.EditTrigger.EditKeyPressed
        else:
            triggers = (
                QAbstractItemView.EditTrigger.DoubleClicked
                | QAbstractItemView.EditTrigger.EditKeyPressed
            )
        self._tree.setEditTriggers(triggers)

    def _augment_context_menu(self, menu: QMenu, item: QTreeWidgetItem | None) -> None:
        if self._context_menu_augmenter is not None:
            self._context_menu_augmenter(menu, item)

    def _build_exclusive_context_menu(
        self, item: QTreeWidgetItem | None, column: int
    ) -> QMenu | None:
        if self._exclusive_context_menu_builder is None:
            return None
        return self._exclusive_context_menu_builder(item, column)

    def toPlainText(self) -> str:  # noqa: N802
        """Return data paths in the former one-path-per-line form."""
        return "\n".join(self.items())

    def setPlainText(self, text: str) -> None:  # noqa: N802
        """Replace data rows with non-blank lines from text."""
        self._tree.clear()
        self._append_items(text.splitlines())
        self._normalize_input_row()
        self._update_selection_toggle_button()
        self.textChanged.emit()

    def items(self, *, deduplicate: bool = False) -> list[str]:
        """Return non-blank path texts, optionally removing duplicates."""
        values = [
            self._tree.topLevelItem(index).text(1).strip()
            for index in range(self._tree.topLevelItemCount())
            if not self._is_input_row(self._tree.topLevelItem(index))
            and self._tree.topLevelItem(index).text(1).strip()
        ]
        return list(dict.fromkeys(values)) if deduplicate else values

    def paths(self, *, deduplicate: bool = False) -> list[Path]:
        """Return normalized paths for each current data row."""
        return [
            normalize_path(item.text(1).strip())
            for index in range(self._tree.topLevelItemCount())
            if not self._is_input_row(item := self._tree.topLevelItem(index))
            and not self.is_virtual_item(item)
            and item.text(1).strip()
        ]

    def selected_items(self, *, deduplicate: bool = False) -> list[str]:
        """Return checkbox-selected data rows in their visual order."""
        values = [
            self._tree.topLevelItem(index).text(1).strip()
            for index in range(self._tree.topLevelItemCount())
            if not self._is_input_row(self._tree.topLevelItem(index))
            and self._item_is_selected(self._tree.topLevelItem(index))
            and self._tree.topLevelItem(index).text(1).strip()
        ]
        return list(dict.fromkeys(values)) if deduplicate else values

    def selected_paths(self, *, deduplicate: bool = False) -> list[Path]:
        """Return normalized paths for selected data rows."""
        values = [
            str(normalize_path(item.text(1).strip()))
            for index in range(self._tree.topLevelItemCount())
            if not self._is_input_row(item := self._tree.topLevelItem(index))
            and not self.is_virtual_item(item)
            and self._item_is_selected(item)
            and item.text(1).strip()
        ]
        if deduplicate:
            values = list(dict.fromkeys(values))
        return [Path(value) for value in values]

    def row_selected_paths(self, *, deduplicate: bool = False) -> list[Path]:
        """Return blue-highlighted rows, independently of check and target states."""
        values = [
            str(normalize_path(item.text(1).strip()))
            for item in self._tree.selectedItems()
            if not self._is_input_row(item)
            and not self.is_virtual_item(item)
            and item.text(1).strip()
        ]
        if deduplicate:
            values = list(dict.fromkeys(values))
        return [Path(value) for value in values]

    def row_selected_item_tokens(self, *, deduplicate: bool = False) -> list[str]:
        """Return blue-highlighted identities, including app-owned virtual rows."""
        values = [
            self.item_token(item)
            for item in self._tree.selectedItems()
            if not self._is_input_row(item)
        ]
        return list(dict.fromkeys(values)) if deduplicate else values

    def set_row_selected_items_checked(self, checked: bool) -> int:
        """Change checks only on blue-highlighted rows and return the change count."""
        changed = 0
        for item in self._tree.selectedItems():
            if self._set_item_selected(
                item,
                checked,
                refresh_toggle=False,
                emit_selection_changed=False,
            ):
                changed += 1
        if changed:
            self._update_selection_toggle_button()
            self.selectionChanged.emit()
        return changed

    def item_token(self, item: QTreeWidgetItem) -> str:
        """Return a row's app-owned identity, distinct from its display text."""
        token = item.data(1, _ROW_TOKEN_ROLE)
        return str(token) if token else str(normalize_path(item.text(1).strip()))

    @staticmethod
    def is_virtual_item(item: QTreeWidgetItem) -> bool:
        """Return whether this row is app-owned display data rather than a path."""
        return bool(item.data(1, _VIRTUAL_ROW_ROLE))

    def selected_item_tokens(self, *, deduplicate: bool = False) -> list[str]:
        """Return selected row identities, including explicit virtual rows."""
        values = [
            self.item_token(item)
            for index in range(self._tree.topLevelItemCount())
            if not self._is_input_row(item := self._tree.topLevelItem(index))
            and self._item_is_selected(item)
        ]
        return list(dict.fromkeys(values)) if deduplicate else values

    def item_is_selected(self, item: QTreeWidgetItem | None) -> bool:
        """Return one row's checkbox state without scanning the whole list."""
        return item is not None and not self._is_input_row(item) and self._item_is_selected(item)

    def append_virtual_item(self, token: str, display_text: str, *, tooltip: str = "") -> None:
        """Append an app-owned non-path row without pretending it exists on disk.

        This is intended for explicit records such as a manual placeholder.
        Normal path APIs intentionally continue to return only filesystem paths.
        """
        normalized_token = token.strip()
        display = display_text.strip()
        if not normalized_token or not display:
            raise ValueError("仮想行には識別子と表示名が必要です。")
        if normalized_token in {
            self.item_token(item)
            for index in range(self._tree.topLevelItemCount())
            if not self._is_input_row(item := self._tree.topLevelItem(index))
        }:
            raise ValueError("同じ識別子の仮想行は追加できません。")
        item = self._new_virtual_item(normalized_token, display, tooltip)
        self._tree.insertTopLevelItem(self._input_row_index(), item)
        self._add_selection_button(item)
        self._add_operation_target_button(item)
        self._add_remove_button(item)
        if self._paths_locked:
            selection_button = self._tree.itemWidget(item, 0)
            if selection_button is not None:
                selection_button.setEnabled(self._selection_editable_when_locked)
            remove_button = self._tree.itemWidget(item, self._remove_column)
            if remove_button is not None:
                remove_button.setEnabled(False)
        self._update_selection_toggle_button()
        self.textChanged.emit()

    def operation_target_items(self, *, deduplicate: bool = False) -> list[str]:
        """Return paths explicitly included in execution when that column is enabled."""
        if self._operation_target_column is None:
            return self.items(deduplicate=deduplicate)
        values = [
            item.text(1).strip()
            for index in range(self._tree.topLevelItemCount())
            if not self._is_input_row(item := self._tree.topLevelItem(index))
            and self._operation_target_enabled(item)
            and item.text(1).strip()
        ]
        return list(dict.fromkeys(values)) if deduplicate else values

    def operation_target_paths(self, *, deduplicate: bool = False) -> list[Path]:
        return [
            normalize_path(value)
            for value in self.operation_target_items(deduplicate=deduplicate)
        ]

    def operation_target_count(self) -> int:
        return len(self.operation_target_items())

    def snapshot(self) -> tuple[tuple[object, ...], ...]:
        """Return transient path and checkbox state for UI-only undo/redo."""
        return tuple(
            (item.text(1).strip(), self._item_is_selected(item), self._operation_target_enabled(item))
            if self._operation_target_column is not None
            else (item.text(1).strip(), self._item_is_selected(item))
            for index in range(self._tree.topLevelItemCount())
            if not self._is_input_row(item := self._tree.topLevelItem(index))
        )

    def restore_snapshot(self, rows: tuple[tuple[object, ...], ...]) -> None:
        """Restore transient path and checkbox state without touching the filesystem."""
        self.setPlainText("\n".join(str(row[0]) for row in rows))
        for index, row in enumerate(rows):
            checked = bool(row[1])
            self._set_item_selected(self._tree.topLevelItem(index), checked)
            if self._operation_target_column is not None and len(row) > 2:
                self._set_operation_target(self._tree.topLevelItem(index), bool(row[2]))

    def append_items(self, values: Iterable[str]) -> None:
        """Append accepted path rows and inspect their current states."""
        if self._append_items(values):
            self._normalize_input_row()
            self.textChanged.emit()

    def replace_operation_target_items(self, values: Iterable[str]) -> None:
        """Replace enabled execution rows in-place, preserving unrelated candidates."""
        replacements = [value.strip() for value in values if value.strip()]
        targets = [
            self._tree.topLevelItem(index)
            for index in range(self._tree.topLevelItemCount())
            if not self._is_input_row(self._tree.topLevelItem(index))
            and self._operation_target_enabled(self._tree.topLevelItem(index))
        ]
        if len(replacements) != len(targets):
            raise ValueError("操作対象と置換結果の件数が一致しません。")
        for item, value in zip(targets, replacements, strict=True):
            item.setText(1, value)
            self._set_item_state(item, inspect_path(value))
        self._normalize_input_row()
        self.textChanged.emit()

    def replace_checked_item_values(self, values: Iterable[str]) -> None:
        """Replace checkbox-selected rows in place without involving blue selection."""
        replacements = [value.strip() for value in values if value.strip()]
        checked_items = [
            self._tree.topLevelItem(index)
            for index in range(self._tree.topLevelItemCount())
            if not self._is_input_row(self._tree.topLevelItem(index))
            and self._item_is_selected(self._tree.topLevelItem(index))
        ]
        if len(replacements) != len(checked_items):
            raise ValueError("チェック済み項目と置換結果の件数が一致しません。")
        for item, value in zip(checked_items, replacements, strict=True):
            item.setText(1, value)
            self._set_item_state(item, inspect_path(value))
        self._normalize_input_row()
        self.textChanged.emit()

    def append_dropped_paths(self, paths: Iterable[Path], *, replace: bool = False) -> None:
        """Add dropped paths, or replace the list when this list is configured to do so."""
        count = self.add_external_paths(paths, replace=replace)
        action = "置換" if replace else "追加"
        self.actionPerformed.emit(f"ドロップで一覧を{action}しました（{count}件）。")

    def add_external_paths(self, paths: Iterable[Path], *, replace: bool = False) -> int:
        """Apply paths with this list's file/directory and replacement policy.

        This is shared by external drops and explicit app-owned choices such
        as registered paths.  It changes only the transient UI list.
        """
        values = [
            directory_for_path(path) if self._accepted_path_kind == "directory" else path
            for path in paths
        ]
        if replace:
            self.setPlainText("\n".join(str(path) for path in values))
            return len(self.items())
        before = len(self.items())
        self.append_items(str(path) for path in values)
        return len(self.items()) - before

    def remove_checked_items(self) -> None:
        """Remove checked rows from this UI list without deleting real paths."""
        count = self._remove_rows(
            self._item_is_selected
        )
        self.actionPerformed.emit(f"チェック済み{count}件を一覧から除外しました。")

    def select_all_items(self) -> None:
        """Check every data row in this UI-only list."""
        self._set_all_check_states(Qt.CheckState.Checked)
        self.actionPerformed.emit("一覧の全行をチェックしました。")

    def clear_item_selection(self) -> None:
        """Clear every data-row checkbox in this UI-only list."""
        self._set_all_check_states(Qt.CheckState.Unchecked)
        self.actionPerformed.emit("一覧の全行のチェックを外しました。")

    def update_checked_paths(self, paths: Iterable[Path], *, mode: str = "replace") -> int:
        """Update checkboxes by exact path without changing rows or blue selection."""
        if mode not in {"replace", "add", "remove"}:
            raise ValueError("未対応のチェック更新方式です。")
        matched = {str(normalize_path(path)) for path in paths}
        changed = 0
        for row in range(self._input_row_index()):
            item = self._tree.topLevelItem(row)
            value = item.text(1).strip()
            is_match = str(normalize_path(value)) in matched if value else False
            before = self._item_is_selected(item)
            if mode == "replace":
                after = is_match
            elif mode == "add":
                after = before or is_match
            else:
                after = before and not is_match
            if after != before:
                self._set_item_selected(item, after)
                changed += 1
        self._update_selection_toggle_button()
        if changed:
            self.textChanged.emit()
        return changed

    def toggle_item_selection(self) -> None:
        """Clear all checks when complete, otherwise check every data row."""
        if self._all_items_checked():
            self.clear_item_selection()
        else:
            self.select_all_items()

    def retain_checked_items(self) -> None:
        """Keep checked rows and remove every other row from this UI list."""
        count = self._remove_rows(
            lambda item: not self._item_is_selected(item)
        )
        self.actionPerformed.emit(f"チェック済み以外{count}件を一覧から除外しました。")

    def remove_duplicate_items(self) -> None:
        """Keep the first occurrence of each path in visual order."""
        seen: set[str] = set()

        def is_duplicate(item: QTreeWidgetItem) -> bool:
            value = item.text(1).strip()
            if value in seen:
                return True
            seen.add(value)
            return False

        count = self._remove_rows(is_duplicate)
        self.actionPerformed.emit(f"重複{count}件を一覧から除外しました。")

    def remove_missing_items(self) -> None:
        """Remove only rows whose current path state is missing."""
        count = self._remove_rows(lambda item: inspect_path(item.text(1).strip()).kind == "missing")
        self.actionPerformed.emit(f"存在しない項目{count}件を一覧から除外しました。")

    def remove_item(self, item: QTreeWidgetItem) -> None:
        """Remove one row through its × control without touching its real path."""
        row = self._tree.indexOfTopLevelItem(item)
        if row < 0 or self._is_input_row(item):
            return
        self._tree.takeTopLevelItem(row)
        self._normalize_input_row()
        self._update_selection_toggle_button()
        self.textChanged.emit()
        self.actionPerformed.emit("1件を一覧から除外しました。")

    def _remove_rows(self, should_remove) -> int:  # type: ignore[no-untyped-def]
        """Remove matching data rows while preserving the single input row."""
        rows = [
            index
            for index in range(self._tree.topLevelItemCount())
            if not self._is_input_row(self._tree.topLevelItem(index))
            and should_remove(self._tree.topLevelItem(index))
        ]
        if not rows:
            return 0
        for index in reversed(rows):
            self._tree.takeTopLevelItem(index)
        self._normalize_input_row()
        self._update_selection_toggle_button()
        self.textChanged.emit()
        return len(rows)

    def clear_items(self) -> None:
        """Empty data rows from this UI list without affecting files or folders."""
        count = len(self.items())
        self._tree.clear()
        self._normalize_input_row()
        self._update_selection_toggle_button()
        self.textChanged.emit()
        self.actionPerformed.emit(f"一覧を空にしました（{count}件）。")

    def replace_checked_items(self, values: Iterable[str]) -> None:
        """Remove checked rows and append replacement paths within this list only."""
        remaining = [
            (item.text(1).strip(), self._item_is_selected(item))
            for index in range(self._tree.topLevelItemCount())
            if not self._is_input_row(item := self._tree.topLevelItem(index))
            and not self._item_is_selected(item)
        ]
        additions = [value.strip() for value in values if value.strip() and self._accepts_path(value)]
        self.setPlainText("\n".join([path for path, _checked in remaining] + additions))
        for index, (_path, checked) in enumerate(remaining):
            self._set_item_selected(self._tree.topLevelItem(index), checked)

    def replace_row_selected_items(self, values: Iterable[str]) -> None:
        """Replace only blue-highlighted rows; checkbox and target meanings stay separate."""
        selected = set(self._tree.selectedItems())
        remaining = [
            (
                item.text(1).strip(),
                self._item_is_selected(item),
                self._operation_target_enabled(item),
            )
            for index in range(self._tree.topLevelItemCount())
            if not self._is_input_row(item := self._tree.topLevelItem(index))
            and item not in selected
        ]
        additions = [value.strip() for value in values if value.strip() and self._accepts_path(value)]
        self.setPlainText("\n".join((*[path for path, _checked, _target in remaining], *additions)))
        for index, (_path, checked, operation_target) in enumerate(remaining):
            item = self._tree.topLevelItem(index)
            self._set_item_selected(item, checked)
            self._set_operation_target(item, operation_target)

    def sort_items(self, mode: str | None = None) -> None:
        """Sort only this UI list; manual order remains available for pairing."""
        mode = mode or self.sort_combo.currentData()
        if mode == "manual":
            return
        values = self.items()
        if mode == "name_asc":
            values.sort(key=lambda value: (Path(value).name.casefold(), value.casefold()))
        elif mode == "name_desc":
            values.sort(key=lambda value: (Path(value).name.casefold(), value.casefold()), reverse=True)
        elif mode == "path_asc":
            values.sort(key=str.casefold)
        elif mode == "path_desc":
            values.sort(key=str.casefold, reverse=True)
        elif mode == "state":
            state_order = {
                "directory": 0,
                "file": 1,
                "symlink_directory": 2,
                "symlink_file": 3,
                "other": 4,
                "symlink_other": 5,
                "symlink_broken": 6,
                "missing": 7,
                "unavailable": 8,
            }
            values.sort(
                key=lambda value: (state_order[inspect_path(value).kind], value.casefold())
            )
        else:
            return
        self.setPlainText("\n".join(values))

    def refresh_states(self) -> tuple[PathInfo, ...]:
        """Refresh only the visible state columns; never alter path data."""
        infos: list[PathInfo] = []
        for index in range(self._tree.topLevelItemCount()):
            item = self._tree.topLevelItem(index)
            if self._is_input_row(item):
                continue
            info = inspect_path(item.text(1).strip())
            self._set_item_state(item, info)
            infos.append(info)
        return tuple(infos)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802
        if self._is_own_path_drag(event):
            event.ignore()
            return
        if self._paths_locked:
            event.ignore()
            return
        if local_paths_from_mime(event.mimeData()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:  # noqa: N802
        if self._is_own_path_drag(event) or self._paths_locked:
            event.ignore()
            return
        if local_paths_from_mime(event.mimeData()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802
        if self._is_own_path_drag(event):
            event.ignore()
            return
        if self._paths_locked:
            event.ignore()
            return
        paths = local_paths_from_mime(event.mimeData())
        if not paths:
            event.ignore()
            return
        self.append_dropped_paths(
            paths,
            replace=self.drop_replaces,
        )
        event.acceptProposedAction()

    def _is_own_path_drag(self, event: QEvent) -> bool:
        """Use the table's per-instance marker for every drop entry point."""
        return self._tree._is_own_path_drag(event)

    def _append_items(self, values: Iterable[str]) -> bool:
        additions = [
            value.strip()
            for value in values
            if value.strip() and self._accepts_path(value.strip())
        ]
        if self._maximum_items is not None:
            remaining = self._maximum_items - len(self.items())
            additions = additions[:max(0, remaining)]
        for value in additions:
            item = self._new_data_item(value)
            self._tree.insertTopLevelItem(self._input_row_index(), item)
            self._add_selection_button(item)
            self._add_operation_target_button(item)
            self._add_remove_button(item)
        return bool(additions)

    def _new_data_item(self, value: str) -> QTreeWidgetItem:
        values = [""] * (self._remove_column + 1)
        values[1] = value
        item = QTreeWidgetItem(values)
        item.setData(1, _ROW_TOKEN_ROLE, str(normalize_path(value)))
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
        item.setData(0, _SELECTION_ROLE, True)
        item.setToolTip(0, "選択の切替")
        self._set_item_state(item, inspect_path(value))
        return item

    def _new_virtual_item(self, token: str, display: str, tooltip: str) -> QTreeWidgetItem:
        """Build a visible row which deliberately has no filesystem path."""
        values = [""] * (self._remove_column + 1)
        values[1] = display
        item = QTreeWidgetItem(values)
        item.setData(1, _ROW_TOKEN_ROLE, token)
        item.setData(1, _VIRTUAL_ROW_ROLE, True)
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        item.setData(0, _SELECTION_ROLE, True)
        message = tooltip or "実在パスを持たない仮登録です。"
        item.setToolTip(0, "選択の切替")
        item.setToolTip(1, message)
        item.setIcon(self._state_column, QIcon(str(_ICON_DIRECTORY / _STATE_ICON_NAMES["other"])))
        item.setToolTip(self._state_column, "仮登録：実ファイルなし")
        return item

    @staticmethod
    def _update_selection_cell_button(button: QToolButton) -> None:
        checked = button.isChecked()
        button.setText("✓" if checked else "")
        button.setToolTip("チェックを外します。" if checked else "チェックを入れます。")
        button.setStyleSheet(
            "QToolButton { background: #2f7d32; color: white; border: 1px solid #236126; font-weight: 700; padding: 0; }"
            if checked
            else "QToolButton { background: palette(base); color: palette(text); border: 1px solid #888; padding: 0; }"
        )

    @staticmethod
    def _item_is_selected(item: QTreeWidgetItem) -> bool:
        return bool(item.data(0, _SELECTION_ROLE))

    def _set_item_selected(
        self,
        item: QTreeWidgetItem,
        selected: bool,
        *,
        refresh_toggle: bool = True,
        emit_selection_changed: bool = True,
    ) -> bool:
        """Set one check state and report whether it actually changed."""
        if self._is_input_row(item) or self._item_is_selected(item) == selected:
            return False
        item.setData(0, _SELECTION_ROLE, selected)
        self._set_selection_button(item, selected)
        if refresh_toggle:
            self._update_selection_toggle_button()
        if emit_selection_changed:
            self.selectionChanged.emit()
        return True

    def _set_selection_button(self, item: QTreeWidgetItem, checked: bool) -> None:
        button = self._tree.itemWidget(item, 0)
        if isinstance(button, QToolButton):
            button.blockSignals(True)
            button.setChecked(checked)
            button.blockSignals(False)
            self._update_selection_cell_button(button)

    def _selection_cell_toggled(self, item: QTreeWidgetItem, button: QToolButton) -> None:
        self._set_item_selected(item, button.isChecked())

    def _add_selection_button(self, item: QTreeWidgetItem) -> None:
        button = QToolButton()
        button.setCheckable(True)
        button.setChecked(self._item_is_selected(item))
        button.setFixedSize(24, 22)
        self._update_selection_cell_button(button)
        button.toggled.connect(
            lambda _checked=False, row=item, control=button: self._selection_cell_toggled(row, control)
        )
        self._tree.setItemWidget(item, 0, button)

    def _operation_target_enabled(self, item: QTreeWidgetItem) -> bool:
        if self._operation_target_column is None:
            return True
        button = self._tree.itemWidget(item, self._operation_target_column)
        return isinstance(button, QToolButton) and button.isChecked()

    def _set_operation_target(self, item: QTreeWidgetItem, enabled: bool) -> None:
        if self._operation_target_column is None:
            return
        button = self._tree.itemWidget(item, self._operation_target_column)
        if isinstance(button, QToolButton):
            button.blockSignals(True)
            button.setChecked(enabled)
            button.blockSignals(False)
            self._update_operation_target_button(button)

    @staticmethod
    def _update_operation_target_button(button: QToolButton) -> None:
        enabled = button.isChecked()
        button.setText("対象" if enabled else "除外")
        button.setToolTip("この項目を実行に含めます。" if enabled else "この項目を実行から除外します。")
        button.setStyleSheet(
            "QToolButton { background: #2f7d32; color: white; font-weight: 600; }"
            if enabled
            else "QToolButton { background: #707070; color: white; }"
        )

    def _operation_target_toggled(self, button: QToolButton) -> None:
        self._update_operation_target_button(button)
        self.operationTargetsChanged.emit()

    def _add_operation_target_button(self, item: QTreeWidgetItem) -> None:
        if self._operation_target_column is None:
            return
        button = QToolButton()
        button.setCheckable(True)
        button.setChecked(True)
        button.setFixedSize(46, 22)
        self._update_operation_target_button(button)
        button.toggled.connect(lambda _checked=False, control=button: self._operation_target_toggled(control))
        self._tree.setItemWidget(item, self._operation_target_column, button)

    def _set_item_state(self, item: QTreeWidgetItem, info: PathInfo) -> None:
        item.setText(self._state_column, "")
        item.setIcon(self._state_column, QIcon(str(_ICON_DIRECTORY / _STATE_ICON_NAMES[info.kind])))
        item.setToolTip(1, self._path_tooltip(info))
        item.setToolTip(0, "選択の切替")
        item.setToolTip(self._state_column, self._state_tooltip(info))

    @staticmethod
    def _state_tooltip(info: PathInfo) -> str:
        label = _STATE_LABELS[info.kind]
        return f"{label}\nリンク先: {info.link_target}" if info.link_target is not None else label

    @staticmethod
    def _path_tooltip(info: PathInfo) -> str:
        tooltip = str(info.path)
        if info.link_target is not None:
            tooltip += f"\nリンク先: {info.link_target}"
        return tooltip

    def _refresh_path_tooltip(self, item: QTreeWidgetItem) -> None:
        """Attach a full path and immediate child counts to one hovered path."""
        if self._is_input_row(item) or self.is_virtual_item(item):
            return
        info = inspect_path(item.text(1).strip())
        tooltip = self._path_tooltip(info)
        if info.kind in {"directory", "symlink_directory"}:
            files, directories, error = _direct_child_counts(info.path)
            if error:
                tooltip += f"\n直下: 確認不可（{error}）"
            else:
                tooltip += f"\n直下: ファイル {files} 件 / フォルダ {directories} 件"
        elif info.kind in {"file", "symlink_file"}:
            tooltip += "\n直下: ファイルのため対象外"
        else:
            tooltip += "\n直下: 確認できません"
        item.setToolTip(1, tooltip)

    def _accepts_path(self, value: str) -> bool:
        if self._accepted_path_kind == "all":
            return True
        return inspect_path(value).kind in {"directory", "symlink_directory"}

    def _on_item_changed(self, item: QTreeWidgetItem, column: int) -> None:
        if column == 0:
            return
        if self._normalizing or column != 1:
            return
        self._normalizing = True
        try:
            self._normalize_input_row()
        finally:
            self._normalizing = False
        self.textChanged.emit()

    def _on_tree_item_double_clicked(self, item: QTreeWidgetItem, column: int) -> None:
        """Run an enabled folder chooser, otherwise expose the double click."""
        if not self._is_input_row(item):
            if self._handle_double_click_directory_replace_all(item, column):
                return
            if self._handle_double_click_directory_selection(item, column):
                return
            self.itemDoubleClicked.emit(item, column)

    def _handle_double_click_directory_replace_all(
        self, item: QTreeWidgetItem, column: int
    ) -> bool:
        """Browse one ordinary folder by replacing the complete transient list."""
        if (
            not self._double_click_directory_replaces_all
            or self._paths_locked
            or column != 1
            or self.is_virtual_item(item)
        ):
            return False
        info = inspect_path(item.text(1).strip())
        if info.kind != "directory":
            return False
        try:
            candidates = self._direct_child_candidates(info.path)
        except OSError as exc:
            message = f"直下を読み取れませんでした。\n{exc}"
            self.actionPerformed.emit(message)
            QMessageBox.warning(self, "フォルダを開けません", message)
            return True
        self.setPlainText("\n".join(str(path) for path in candidates))
        self.actionPerformed.emit(
            f"フォルダを開き、一覧を直下 {len(candidates)} 件で置き換えました。"
        )
        return True

    def open_directory_item(self, item: QTreeWidgetItem) -> bool:
        """Apply the file-manager-style complete-list replacement to one row."""
        enabled = self._double_click_directory_replaces_all
        self._double_click_directory_replaces_all = True
        try:
            return self._handle_double_click_directory_replace_all(item, 1)
        finally:
            self._double_click_directory_replaces_all = enabled

    def choose_direct_children_for_item(self, item: QTreeWidgetItem) -> bool:
        """Expose the former choose-and-replace-one-row operation to context menus."""
        enabled = self._double_click_directory_selection
        self._double_click_directory_selection = True
        try:
            return self._handle_double_click_directory_selection(item, 1)
        finally:
            self._double_click_directory_selection = enabled

    def _handle_double_click_directory_selection(
        self, item: QTreeWidgetItem, column: int
    ) -> bool:
        """Replace one unlocked folder row with children selected in a dialog.

        ``True`` means the opt-in feature consumed the double click, including
        cancellation and a reported error.  Files, virtual rows, other columns
        and every locked list retain their owner's pre-existing double-click
        behaviour.
        """
        if (
            not self._double_click_directory_selection
            or self._paths_locked
            or column != 1
            or self.is_virtual_item(item)
        ):
            return False
        info = inspect_path(item.text(1).strip())
        # Do not follow directory symlinks implicitly.  They can point outside
        # the collection a user meant to browse, so ordinary folders alone are
        # eligible for this convenient shortcut.
        if info.kind != "directory":
            return False
        try:
            candidates = self._direct_child_candidates(info.path)
        except OSError as exc:
            message = f"直下を読み取れませんでした。\n{exc}"
            self.actionPerformed.emit(message)
            QMessageBox.warning(self, "直下を選べません", message)
            return True
        if not candidates:
            message = "選べる直下項目がありません。元のフォルダは変更していません。"
            self.actionPerformed.emit(message)
            QMessageBox.information(self, "直下を選べません", message)
            return True

        current_count = len(self.items())
        maximum_choices = (
            None
            if self._maximum_items is None
            else max(1, self._maximum_items - current_count + 1)
        )
        chosen = self._choose_direct_children(
            info.path, candidates, maximum_choices=maximum_choices
        )
        if chosen is None:
            return True
        candidate_values = {str(path) for path in candidates}
        selected = tuple(
            path
            for path in chosen
            if str(normalize_path(path)) in candidate_values
        )
        if not selected:
            return True
        if maximum_choices is not None and len(selected) > maximum_choices:
            # The normal dialog prevents this, but keeping the guard here
            # makes the state safe even if a caller later supplies a custom
            # chooser implementation.
            message = f"この一覧には最大 {maximum_choices} 件まで追加できます。変更していません。"
            self.actionPerformed.emit(message)
            QMessageBox.warning(self, "直下を選べません", message)
            return True
        self._replace_row_with_paths(item, selected)
        self.actionPerformed.emit(
            f"フォルダを選択した直下 {len(selected)} 件で置き換えました。"
        )
        return True

    def _direct_child_candidates(self, directory: Path) -> tuple[Path, ...]:
        """Return this list's acceptable direct children in a stable order."""
        children = (
            normalize_path(child)
            for child in directory.iterdir()
            if self._accepts_path(str(child))
            and (self._direct_child_filter is None or self._direct_child_filter(child))
        )
        return tuple(sorted(children, key=lambda path: (path.name.casefold(), str(path).casefold())))

    def _choose_direct_children(
        self,
        directory: Path,
        candidates: tuple[Path, ...],
        *,
        maximum_choices: int | None,
    ) -> tuple[Path, ...] | None:
        """Ask for direct children without making any filesystem changes."""
        dialog = QDialog(self)
        dialog.setWindowTitle("直下項目を選択")
        dialog.setMinimumSize(620, 360)
        layout = QVBoxLayout(dialog)
        explanation = QLabel(
            "追加したい直下項目へチェックを入れてください。確定すると元のフォルダ行だけを外し、"
            "チェック済みの項目を同じ位置へ追加します。候補が1件だけのときだけ、最初からチェックします。"
        )
        explanation.setWordWrap(True)
        layout.addWidget(explanation)
        directory_label = QLabel(f"対象フォルダ: {directory}")
        directory_label.setWordWrap(True)
        layout.addWidget(directory_label)
        if maximum_choices is not None:
            layout.addWidget(QLabel(f"この一覧には最大 {maximum_choices} 件まで選べます。"))

        choices = QTreeWidget()
        choices.setRootIsDecorated(False)
        choices.setHeaderLabels(["名前", "種別", "パス"])
        choices.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        for path in candidates:
            info = inspect_path(path)
            item = QTreeWidgetItem([path.name, _STATE_LABELS[info.kind], str(path)])
            item.setData(0, Qt.ItemDataRole.UserRole, str(path))
            item.setToolTip(0, str(path))
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                0,
                Qt.CheckState.Checked if len(candidates) == 1 else Qt.CheckState.Unchecked,
            )
            choices.addTopLevelItem(item)
        choices.setColumnWidth(0, 180)
        choices.setColumnWidth(1, 120)
        layout.addWidget(choices, 1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok
        )
        choose_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        if choose_button is not None:
            choose_button.setText("チェック済みを置換")
            changing_checks = False

            def update_accept_enabled(changed_item: QTreeWidgetItem | None = None) -> None:
                nonlocal changing_checks
                if changing_checks:
                    return
                checked_items = [
                    choices.topLevelItem(index)
                    for index in range(choices.topLevelItemCount())
                    if choices.topLevelItem(index).checkState(0) == Qt.CheckState.Checked
                ]
                if maximum_choices == 1 and changed_item in checked_items and len(checked_items) > 1:
                    changing_checks = True
                    for item in checked_items:
                        if item is not changed_item:
                            item.setCheckState(0, Qt.CheckState.Unchecked)
                    changing_checks = False
                    checked_items = [changed_item]
                choose_button.setEnabled(
                    bool(checked_items)
                    and (maximum_choices is None or len(checked_items) <= maximum_choices)
                )

            choices.itemChanged.connect(lambda item, _column: update_accept_enabled(item))
            update_accept_enabled()
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        return tuple(
            normalize_path(str(item.data(0, Qt.ItemDataRole.UserRole)))
            for index in range(choices.topLevelItemCount())
            if (item := choices.topLevelItem(index)).checkState(0) == Qt.CheckState.Checked
        )

    def _replace_row_with_paths(self, item: QTreeWidgetItem, paths: tuple[Path, ...]) -> None:
        """Replace one data row in-place while retaining its row-control states."""
        row_index = self._tree.indexOfTopLevelItem(item)
        if row_index < 0:
            return
        selected = self._item_is_selected(item)
        operation_target = self._operation_target_enabled(item)
        self._tree.takeTopLevelItem(row_index)
        for offset, path in enumerate(paths):
            replacement = self._new_data_item(str(path))
            replacement.setData(0, _SELECTION_ROLE, selected)
            self._tree.insertTopLevelItem(row_index + offset, replacement)
            self._add_selection_button(replacement)
            self._add_operation_target_button(replacement)
            self._set_operation_target(replacement, operation_target)
            self._add_remove_button(replacement)
        self._normalize_input_row()
        self._update_selection_toggle_button()
        self.textChanged.emit()
        self.selectionChanged.emit()
        if self._operation_target_column is not None:
            self.operationTargetsChanged.emit()

    def _on_tree_item_clicked(self, item: QTreeWidgetItem, column: int) -> None:
        """Expose a data-row click without changing checkbox selection semantics."""
        if not self._is_input_row(item):
            self.itemClicked.emit(item, column)

    def _normalize_input_row(self) -> None:
        """Keep one input row only while this list has room for another item."""
        already_normalizing = self._normalizing
        self._normalizing = True
        try:
            for row in range(self._tree.topLevelItemCount() - 1, -1, -1):
                item = self._tree.topLevelItem(row)
                value = item.text(1).strip()
                if not value or not self._accepts_path(value):
                    self._tree.takeTopLevelItem(row)
                    continue
                if self._is_input_row(item):
                    item.setData(1, _INPUT_ROW_ROLE, False)
                    item.setData(0, _SELECTION_ROLE, True)
                    self._set_item_state(item, inspect_path(value))
                    self._add_selection_button(item)
                    self._add_operation_target_button(item)
                    self._add_remove_button(item)
            if self._maximum_items is not None:
                for row in range(self._tree.topLevelItemCount() - 1, self._maximum_items - 1, -1):
                    self._tree.takeTopLevelItem(row)
            if self._maximum_items is None or len(self.items()) < self._maximum_items:
                input_row = QTreeWidgetItem([""] * (self._remove_column + 1))
                input_row.setData(1, _INPUT_ROW_ROLE, True)
                input_row.setToolTip(1, "ダブルクリックしてパスを追加します。")
                input_row.setFlags(
                    (input_row.flags() | Qt.ItemFlag.ItemIsEditable)
                    & ~Qt.ItemFlag.ItemIsSelectable
                )
                self._tree.addTopLevelItem(input_row)
        finally:
            self._normalizing = already_normalizing

    def _add_remove_button(self, item: QTreeWidgetItem) -> None:
        remove_button = QToolButton()
        remove_button.setText("×")
        remove_button.setFixedSize(26, 22)
        remove_button.setToolTip("この一覧から除外します。実ファイルは削除しません。")
        remove_button.clicked.connect(lambda _checked=False, row=item: self.remove_item(row))
        self._tree.setItemWidget(item, self._remove_column, remove_button)

    def _set_all_check_states(self, state: Qt.CheckState) -> None:
        """Change every checkbox as one UI update, then emit one change event.

        Consumers may use ``selectionChanged`` to rebuild a preview for all
        selected rows.  Emitting once per row turns a simple all-select into
        repeated whole-list work, so this intentionally batches the update.
        """
        changed = False
        for index in range(self._tree.topLevelItemCount()):
            item = self._tree.topLevelItem(index)
            if not self._is_input_row(item):
                changed = self._set_item_selected(
                    item,
                    state == Qt.CheckState.Checked,
                    refresh_toggle=False,
                    emit_selection_changed=False,
                ) or changed
        self._update_selection_toggle_button()
        if changed:
            self.selectionChanged.emit()

    def _all_items_checked(self) -> bool:
        """Return whether at least one data row exists and every row is checked."""
        data_items = [
            self._tree.topLevelItem(index)
            for index in range(self._tree.topLevelItemCount())
            if not self._is_input_row(self._tree.topLevelItem(index))
        ]
        return bool(data_items) and all(
            self._item_is_selected(item) for item in data_items
        )

    def _update_selection_toggle_button(self) -> None:
        """Reflect the next whole-list checkbox action in its visible button."""
        has_items = any(
            not self._is_input_row(self._tree.topLevelItem(index))
            for index in range(self._tree.topLevelItemCount())
        )
        all_checked = self._all_items_checked()
        self.selection_toggle_button.setEnabled(has_items and not self._paths_locked)
        self.selection_toggle_button.setText("全解除" if all_checked else "全選択")
        self.selection_toggle_button.setToolTip(
            "この一覧の全行のチェックを外します。"
            if all_checked
            else "この一覧の全行をチェックします。"
        )

    def _add_cleanup_actions(self, menu: QMenu, *, include_operation_targets: bool = True) -> None:
        """Populate the shared, UI-only cleanup actions for a path list."""
        organize_menu = menu.addMenu("一覧を整理")
        retain_checked_action = organize_menu.addAction("チェック済みだけ残す（他を除外）")
        retain_checked_action.setToolTip("実ファイル・フォルダは削除しません。")
        retain_checked_action.triggered.connect(self.retain_checked_items)
        remove_checked_action = organize_menu.addAction("チェック済みを一覧から除外")
        remove_checked_action.setToolTip("実ファイル・フォルダは削除しません。")
        remove_checked_action.triggered.connect(self.remove_checked_items)
        organize_menu.addSeparator()
        duplicate_action = organize_menu.addAction("重複を除外")
        duplicate_action.setToolTip("同じパスは最初の1件だけ残します。")
        duplicate_action.triggered.connect(self.remove_duplicate_items)
        missing_action = organize_menu.addAction("存在しない項目を除外")
        missing_action.setToolTip("状態が「存在しない」の行を一覧から除外します。")
        missing_action.triggered.connect(self.remove_missing_items)
        clear_action = organize_menu.addAction("一覧を空にする")
        clear_action.setToolTip("実ファイル・フォルダは削除しません。")
        clear_action.triggered.connect(self.clear_items)
        if self._operation_target_column is not None and include_operation_targets:
            targets_menu = menu.addMenu("操作対象")
            all_targets = targets_menu.addAction("全件を対象にする")
            all_targets.triggered.connect(lambda: self._set_all_operation_targets(True))
            no_targets = targets_menu.addAction("全件を除外する")
            no_targets.triggered.connect(lambda: self._set_all_operation_targets(False))

    def _set_all_operation_targets(self, enabled: bool) -> None:
        for index in range(self._tree.topLevelItemCount()):
            item = self._tree.topLevelItem(index)
            if not self._is_input_row(item):
                self._set_operation_target(item, enabled)
        self.operationTargetsChanged.emit()

    def set_all_operation_targets(self, enabled: bool) -> None:
        """Set every execution-target control without exposing row internals."""
        if self._operation_target_column is None or self._paths_locked:
            return
        self._set_all_operation_targets(enabled)

    def _input_row_index(self) -> int:
        count = self._tree.topLevelItemCount()
        return count - 1 if count and self._is_input_row(self._tree.topLevelItem(count - 1)) else count

    @staticmethod
    def _is_input_row(item: QTreeWidgetItem) -> bool:
        return bool(item.data(1, _INPUT_ROW_ROLE))


def add_line_list_input(layout: QVBoxLayout, *, rows: int = 6) -> LineListInput:
    """Create and add a generic line-oriented text input."""
    widget = LineListInput(rows=rows)
    layout.addWidget(widget)
    return widget


def add_directory_path_input(
    layout: QVBoxLayout,
    *,
    default: str = "",
    placeholder: str = "",
) -> PathLineInput:
    """Create and add a one-line directory-oriented path input."""
    widget = PathLineInput(default, drop_as="directory")
    widget.setPlaceholderText(placeholder)
    layout.addWidget(widget)
    return widget


def add_path_list_input(
    layout: QVBoxLayout,
    *,
    rows: int = 6,
    placeholder: str = "",
    accepted_path_kind: Literal["all", "directory"] = "all",
    drop_replaces: bool = False,
    maximum_items: int | None = None,
    show_controls: bool = True,
    supplemental_column_label: str = "",
    show_operation_targets: bool = False,
    supplemental_before_operation_targets: bool = False,
    path_column_label: str = "パス",
    context_menu_selection_actions: bool = True,
    context_menu_operation_target_actions: bool = True,
    context_menu_copy_actions: bool = True,
    double_click_directory_selection: bool = False,
    double_click_directory_replaces_all: bool = False,
    enable_row_selection: bool = False,
    direct_child_filter: Callable[[Path], bool] | None = None,
) -> PathListInput:
    """Create and add a selectable path table with a trailing input row."""
    widget = PathListInput(
        rows=rows,
        accepted_path_kind=accepted_path_kind,
        drop_replaces=drop_replaces,
        maximum_items=maximum_items,
        show_controls=show_controls,
        supplemental_column_label=supplemental_column_label,
        show_operation_targets=show_operation_targets,
        supplemental_before_operation_targets=supplemental_before_operation_targets,
        path_column_label=path_column_label,
        context_menu_selection_actions=context_menu_selection_actions,
        context_menu_operation_target_actions=context_menu_operation_target_actions,
        context_menu_copy_actions=context_menu_copy_actions,
        double_click_directory_selection=double_click_directory_selection,
        double_click_directory_replaces_all=double_click_directory_replaces_all,
        enable_row_selection=enable_row_selection,
        direct_child_filter=direct_child_filter,
    )
    widget.setPlaceholderText(placeholder)
    layout.addWidget(widget)
    return widget
