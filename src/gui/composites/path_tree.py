"""Internal tree surface used by :class:`PathListInput`."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

from PySide6.QtCore import QEvent, QMimeData, Qt, QUrl
from PySide6.QtGui import QDrag, QDragEnterEvent, QDragMoveEvent, QDropEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QMenu,
    QStyle,
    QStyleOptionViewItem,
    QStyledItemDelegate,
    QTreeWidget,
    QTreeWidgetItem,
)

from foundation.path import normalize_path

from .path_roles import INPUT_ROW_ROLE, PATH_LIST_DRAG_SOURCE_MIME
from .path_support import local_paths_from_mime, open_in_standard_file_manager

if TYPE_CHECKING:
    from .path_inputs import PathListInput


class InputRowDelegate(QStyledItemDelegate):
    """Draw a trailing editable input row without treating it as path data."""

    def createEditor(self, parent, option, index):  # type: ignore[no-untyped-def]
        owner = self.parent()._owner  # type: ignore[union-attr]
        if index.column() != 1 or owner._paths_locked or not index.data(INPUT_ROW_ROLE):
            return None
        return super().createEditor(parent, option, index)

    def paint(self, painter, option, index) -> None:  # type: ignore[no-untyped-def]
        if index.column() != 1 or not index.data(INPUT_ROW_ROLE):
            super().paint(painter, option, index)
            return
        styled_option = QStyleOptionViewItem(option)
        self.initStyleOption(styled_option, index)
        styled_option.text = ""
        style = styled_option.widget.style() if styled_option.widget else QApplication.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, styled_option, painter)
        painter.save()
        painter.setPen(Qt.GlobalColor.gray)
        painter.drawText(
            styled_option.rect.adjusted(6, 0, -6, 0),
            Qt.AlignmentFlag.AlignVCenter,
            "ロック中：ロック解除でパスを変更"
            if self.parent()._owner._paths_locked  # type: ignore[union-attr]
            else "ダブルクリックしてパスを追加",
        )
        painter.restore()


class PathTreeWidget(QTreeWidget):
    """Path table surface with selection, context actions and external drops."""

    def __init__(self, owner: PathListInput) -> None:
        super().__init__()
        self._owner = owner
        self._own_drag_token = uuid4().hex.encode("ascii")
        self._path_drag_start = None
        self._path_drag_item: QTreeWidgetItem | None = None
        self.setAcceptDrops(True)
        self.viewport().setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.NoDragDrop)
        self.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed
        )
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_path_context_menu)

    def mousePressEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        position = event.position().toPoint()
        item = self.itemAt(position)
        if (
            event.button() == Qt.MouseButton.LeftButton
            and item is not None
            and self.columnAt(position.x()) == 0
            and not self._owner._is_input_row(item)
            and not self._owner._paths_locked
        ):
            self._owner._set_item_selected(item, not self._owner._item_is_selected(item))
            event.accept()
            return
        if (
            event.button() == Qt.MouseButton.LeftButton
            and item is not None
            and self.columnAt(position.x()) == 1
            and not self._owner._is_input_row(item)
            and not self._owner.is_virtual_item(item)
            and item.text(1).strip()
            and not self._owner._paths_locked
        ):
            self._path_drag_start = position
            self._path_drag_item = item
        else:
            self._path_drag_start = None
            self._path_drag_item = None
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        position = event.position().toPoint()
        if self._path_drag_start is not None and self._path_drag_item is not None:
            if (position - self._path_drag_start).manhattanLength() >= QApplication.startDragDistance():
                path = normalize_path(self._path_drag_item.text(1).strip())
                mime_data = QMimeData()
                mime_data.setUrls([QUrl.fromLocalFile(str(path))])
                mime_data.setData(PATH_LIST_DRAG_SOURCE_MIME, self._own_drag_token)
                drag = QDrag(self)
                drag.setMimeData(mime_data)
                drag.exec(Qt.DropAction.CopyAction)
                self._path_drag_start = None
                self._path_drag_item = None
                return
        super().mouseMoveEvent(event)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802
        if self._is_own_path_drag(event):
            event.ignore()
            return
        if local_paths_from_mime(event.mimeData()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:  # noqa: N802
        if self._is_own_path_drag(event) or self._owner._paths_locked:
            event.ignore()
            return
        if local_paths_from_mime(event.mimeData()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802
        if self._is_own_path_drag(event) or self._owner._paths_locked:
            event.ignore()
            return
        paths = local_paths_from_mime(event.mimeData())
        if not paths:
            event.ignore()
            return
        self._owner.append_dropped_paths(paths, replace=self._owner.drop_replaces)
        event.acceptProposedAction()

    def viewportEvent(self, event: QEvent) -> bool:  # noqa: N802
        """Handle drops and lazily enrich the tooltip for a hovered path."""
        if event.type() == QEvent.Type.ToolTip:
            item = self.itemAt(event.pos())  # type: ignore[attr-defined]
            if item is not None:
                self._owner._refresh_path_tooltip(item)
            return super().viewportEvent(event)
        if event.type() not in {QEvent.Type.DragEnter, QEvent.Type.DragMove, QEvent.Type.Drop}:
            return super().viewportEvent(event)
        if self._is_own_path_drag(event) or self._owner._paths_locked:
            event.ignore()  # type: ignore[attr-defined]
            return True
        paths = local_paths_from_mime(event.mimeData())  # type: ignore[attr-defined]
        if not paths:
            event.ignore()  # type: ignore[attr-defined]
            return True
        if event.type() == QEvent.Type.Drop:
            self._owner.append_dropped_paths(paths, replace=self._owner.drop_replaces)
        event.acceptProposedAction()  # type: ignore[attr-defined]
        return True

    def _is_own_path_drag(self, event: QEvent) -> bool:
        """Reject a path drag returning to the table that emitted it."""
        source_getter = getattr(event, "source", None)
        if callable(source_getter) and source_getter() is self:
            return True
        mime_getter = getattr(event, "mimeData", None)
        if not callable(mime_getter):
            return False
        try:
            return bytes(mime_getter().data(PATH_LIST_DRAG_SOURCE_MIME)) == self._own_drag_token
        except (AttributeError, TypeError):
            return False

    def _show_path_context_menu(self, position) -> None:  # type: ignore[no-untyped-def]
        menu = self._build_path_context_menu(position)
        menu.exec(self.viewport().mapToGlobal(position))

    def _build_path_context_menu(self, position) -> QMenu:  # type: ignore[no-untyped-def]
        """Build common list actions plus row-specific actions."""
        item = self.itemAt(position)
        column = self.columnAt(position.x())
        exclusive_menu = self._owner._build_exclusive_context_menu(item, column)
        if exclusive_menu is not None:
            return exclusive_menu
        menu = QMenu(self)
        if item is not None and not self._owner._is_input_row(item) and item.text(1).strip():
            path_text = item.text(1).strip()
            is_virtual = self._owner.is_virtual_item(item)
            if not self._owner._paths_locked and self._owner._context_menu_selection_actions:
                checked = self._owner._item_is_selected(item)
                check_action = menu.addAction("チェックを外す" if checked else "チェックを入れる")
                check_action.triggered.connect(
                    lambda: self._owner._set_item_selected(item, not checked)
                )
            if self._owner._context_menu_copy_actions:
                copy_action = menu.addAction("表示名をコピー" if is_virtual else "パスをコピー")
                copy_action.triggered.connect(lambda: QApplication.clipboard().setText(path_text))

        if menu.actions():
            menu.addSeparator()
        if (
            item is not None
            and not self._owner._is_input_row(item)
            and not self._owner.is_virtual_item(item)
            and item.text(1).strip()
        ):
            open_action = menu.addAction("標準ファイルマネージャーで開く")
            open_action.setToolTip("このパスの場所を標準ファイルマネージャーで開きます。")
            open_action.triggered.connect(
                lambda: open_in_standard_file_manager(normalize_path(item.text(1).strip()))
            )
        else:
            open_action = menu.addAction("ホームフォルダを開く")
            open_action.setToolTip("パス以外の場所では、安全なホームフォルダを開きます。")
            open_action.triggered.connect(open_in_standard_file_manager)

        if not self._owner._paths_locked:
            if self._owner._context_menu_selection_actions:
                menu.addSeparator()
                checks_menu = menu.addMenu("チェック")
                checks_menu.addAction("すべてにチェックを入れる").triggered.connect(
                    self._owner.select_all_items
                )
                checks_menu.addAction("すべてのチェックを外す").triggered.connect(
                    self._owner.clear_item_selection
                )
            elif menu.actions():
                menu.addSeparator()
            self._owner._add_cleanup_actions(
                menu,
                include_operation_targets=self._owner._context_menu_operation_target_actions,
            )
            menu.addSeparator()
            sort_menu = menu.addMenu("並べ替え")
            for label, mode in (
                ("手動順（現在の順）", "manual"),
                ("名前 ↑", "name_asc"),
                ("名前 ↓", "name_desc"),
                ("パス ↑", "path_asc"),
                ("パス ↓", "path_desc"),
                ("状態順", "state"),
            ):
                action = sort_menu.addAction(label)
                action.triggered.connect(
                    lambda _checked=False, selected_mode=mode: self._owner.sort_items(selected_mode)
                )
        self._owner._augment_context_menu(menu, item)
        return menu
