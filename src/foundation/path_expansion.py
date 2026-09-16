"""Shared, read-only expansion of direct children from path-list selections."""

from __future__ import annotations

from pathlib import Path
import re
from typing import Literal

from foundation.path import normalize_path
from foundation.path_inspection import inspect_path


def _terms(query: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    included: list[str] = []
    excluded: list[str] = []
    for raw in re.split(r"[,\n]", query):
        term = raw.strip()
        if not term:
            continue
        if term.startswith("-") and term[1:].strip():
            excluded.append(term[1:].strip())
        else:
            included.append(term)
    return tuple(included), tuple(excluded)


def direct_children_for_selected_folders(
    selected_paths: list[Path],
    query: str,
    *,
    mode: Literal["contains", "regex"],
    item_kind: Literal["all", "file", "directory"],
) -> tuple[tuple[Path, ...], int]:
    """Return matching direct children of ordinary selected folders only."""
    folders = [path for path in selected_paths if inspect_path(path).kind == "directory"]
    if not folders:
        return (), 0
    if item_kind not in {"all", "file", "directory"}:
        raise ValueError("未対応の種別です。")
    included, excluded = _terms(query)
    if mode == "regex":
        try:
            include_patterns = tuple(re.compile(term) for term in included)
            exclude_patterns = tuple(re.compile(term) for term in excluded)
        except re.error as exc:
            raise ValueError("正規表現の書式が正しくありません。") from exc

        def matches(name: str) -> bool:
            return all(pattern.search(name) for pattern in include_patterns) and not any(
                pattern.search(name) for pattern in exclude_patterns
            )
    elif mode == "contains":

        def matches(name: str) -> bool:
            folded = name.casefold()
            return all(term.casefold() in folded for term in included) and not any(
                term.casefold() in folded for term in excluded
            )
    else:
        raise ValueError("未対応の検索方式です。")

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
