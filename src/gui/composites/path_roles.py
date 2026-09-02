"""Private Qt data roles shared by path-list view components."""

from PySide6.QtCore import Qt


INPUT_ROW_ROLE = int(Qt.ItemDataRole.UserRole) + 1
SELECTION_ROLE = int(Qt.ItemDataRole.UserRole) + 2
ROW_TOKEN_ROLE = int(Qt.ItemDataRole.UserRole) + 3
VIRTUAL_ROW_ROLE = int(Qt.ItemDataRole.UserRole) + 4
PATH_LIST_DRAG_SOURCE_MIME = "application/x-porta-place-path-list-source"
