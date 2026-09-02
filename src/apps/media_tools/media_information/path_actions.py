"""Non-destructive direct-child expansion for the media-information input list."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

from foundation.path import normalize_path
from foundation.path_inspection import inspect_path


SearchMode = Literal["contains", "regex"]
ItemKind = Literal["all", "file", "directory"]


def direct_children_for_selected_folders(
    selected_paths: list[Path],
    query: str,
    *,
    mode: SearchMode = "contains",
    item_kind: ItemKind = "all",
) -> tuple[tuple[Path, ...], int]:
    """Return matching immediate children of selected real folders only.

    Selected files intentionally produce no children. Symbolic-link folders
    are not traversed, so a list action cannot silently leave its selected
    tree or follow a cycle.
    """
    folders = [path for path in selected_paths if inspect_path(path).kind == "directory"]
    if not folders:
        return (), 0
    include, exclude = _parse_query(query)
    matches = _name_matcher(include, exclude, mode)
    children: list[Path] = []
    for folder in folders:
        for child in sorted(folder.iterdir(), key=lambda path: path.name.casefold()):
            if item_kind == "file" and not child.is_file():
                continue
            if item_kind == "directory" and not child.is_dir():
                continue
            if matches(child.name):
                children.append(normalize_path(child))
    return tuple(children), len(folders)


def _parse_query(query: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    include: list[str] = []
    exclude: list[str] = []
    for raw_term in re.split(r"[,\n]", query):
        term = raw_term.strip()
        if not term:
            continue
        if term.startswith("-"):
            if term[1:].strip():
                exclude.append(term[1:].strip())
        else:
            include.append(term)
    return tuple(include), tuple(exclude)


def _name_matcher(
    include: tuple[str, ...], exclude: tuple[str, ...], mode: SearchMode
):
    if mode == "regex":
        try:
            include_patterns = tuple(re.compile(term) for term in include)
            exclude_patterns = tuple(re.compile(term) for term in exclude)
        except re.error as exc:
            raise ValueError("正規表現の書式が正しくありません。") from exc

        return lambda name: all(pattern.search(name) for pattern in include_patterns) and not any(
            pattern.search(name) for pattern in exclude_patterns
        )
    if mode == "contains":
        return lambda name: all(term.casefold() in name.casefold() for term in include) and not any(
            term.casefold() in name.casefold() for term in exclude
        )
    raise ValueError("未対応の検索方式です。")
