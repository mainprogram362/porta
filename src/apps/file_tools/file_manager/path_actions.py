"""File-manager-specific, non-destructive operations on path-list selections."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from foundation.path_inspection import inspect_path

from .search_workflow import search_direct_children_from_roots


def direct_children_for_selected_folders(
    selected_paths: list[Path],
    query: str,
    *,
    mode: Literal["contains", "regex"],
    item_kind: Literal["all", "file", "directory"],
) -> tuple[tuple[Path, ...], int]:
    """Return only direct matching children; selected files deliberately yield none."""
    # A link to a directory remains a selectable item, but expanding it would
    # start enumerating an unrelated target tree.  Keep that traversal opt-in
    # rather than making it an invisible consequence of this list action.
    folders = [path for path in selected_paths if inspect_path(path).kind == "directory"]
    if not folders:
        return (), 0
    children = search_direct_children_from_roots(
        "\n".join(str(path) for path in folders), query, mode=mode, item_kind=item_kind
    )
    return children, len(folders)
