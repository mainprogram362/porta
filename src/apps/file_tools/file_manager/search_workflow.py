"""In-memory direct-child search used by the file-manager path workbench."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

from foundation.path import normalize_path

SearchMode = Literal["contains", "regex"]
ItemKind = Literal["all", "file", "directory"]
SearchScope = Literal["direct", "skip_two_levels"]
MatchField = Literal["name", "path"]


def parse_query(query: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Parse comma/newline terms; a leading ``-`` marks an exclusion term."""
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


def select_paths_by_conditions(
    paths: tuple[Path, ...],
    query: str,
    *,
    mode: SearchMode = "contains",
    item_kind: ItemKind = "all",
    match_field: MatchField = "name",
) -> tuple[Path, ...]:
    """Return current-list paths matching transient checkbox conditions."""
    include, exclude = parse_query(query)
    if mode == "regex":
        try:
            include_patterns = tuple(re.compile(term) for term in include)
            exclude_patterns = tuple(re.compile(term) for term in exclude)
        except re.error as exc:
            raise ValueError("正規表現の書式が正しくありません。") from exc

        def matches(value: str) -> bool:
            return all(pattern.search(value) for pattern in include_patterns) and not any(
                pattern.search(value) for pattern in exclude_patterns
            )
    elif mode == "contains":

        def matches(value: str) -> bool:
            folded = value.casefold()
            return all(term.casefold() in folded for term in include) and not any(
                term.casefold() in folded for term in exclude
            )
    else:
        raise ValueError("未対応の検索方式です。")

    if match_field not in {"name", "path"}:
        raise ValueError("未対応の照合範囲です。")

    selected: list[Path] = []
    for path in paths:
        if item_kind == "file" and not path.is_file():
            continue
        if item_kind == "directory" and not path.is_dir():
            continue
        if item_kind not in {"all", "file", "directory"}:
            raise ValueError("未対応の種別です。")
        value = path.name if match_field == "name" else str(path)
        if matches(value):
            selected.append(path)
    return tuple(selected)


def search_direct_children(
    directory_text: str,
    query: str,
    *,
    mode: SearchMode = "contains",
    item_kind: ItemKind = "all",
) -> tuple[Path, ...]:
    """Search only the immediate children of one directory; never save results."""
    return search_direct_children_from_roots(
        directory_text, query, mode=mode, item_kind=item_kind
    )


def search_direct_children_from_roots(
    roots_text: str,
    query: str,
    *,
    mode: SearchMode = "contains",
    item_kind: ItemKind = "all",
) -> tuple[Path, ...]:
    """Search immediate children of every listed directory, in input order."""
    roots = tuple(
        dict.fromkeys(
            normalize_path(line.strip())
            for line in roots_text.splitlines()
            if line.strip()
        )
    )
    if not roots:
        raise ValueError("探索対象フォルダを1行に1件以上入力してください。")
    invalid_roots = [root for root in roots if not root.is_dir()]
    if invalid_roots:
        raise NotADirectoryError("探索対象に存在しないフォルダまたはファイルが含まれています。")

    include, exclude = parse_query(query)
    if mode == "regex":
        try:
            include_patterns = tuple(re.compile(term) for term in include)
            exclude_patterns = tuple(re.compile(term) for term in exclude)
        except re.error as exc:
            raise ValueError("正規表現の書式が正しくありません。") from exc

        def matches(name: str) -> bool:
            return all(pattern.search(name) for pattern in include_patterns) and not any(
                pattern.search(name) for pattern in exclude_patterns
            )

    elif mode == "contains":

        def matches(name: str) -> bool:
            folded_name = name.casefold()
            return all(term.casefold() in folded_name for term in include) and not any(
                term.casefold() in folded_name for term in exclude
            )

    else:
        raise ValueError("未対応の検索方式です。")

    results: list[Path] = []
    for root in roots:
        for child in sorted(root.iterdir(), key=lambda path: path.name.casefold()):
            if item_kind == "file" and not child.is_file():
                continue
            if item_kind == "directory" and not child.is_dir():
                continue
            if matches(child.name):
                results.append(normalize_path(child))
    return tuple(results)


def search_skip_two_levels_from_roots(
    roots_text: str,
    query: str,
    *,
    mode: SearchMode = "contains",
    item_kind: ItemKind = "all",
) -> tuple[Path, ...]:
    """Search only items at depth two, ignoring every direct child of each root."""
    roots = tuple(
        dict.fromkeys(
            normalize_path(line.strip())
            for line in roots_text.splitlines()
            if line.strip()
        )
    )
    if not roots:
        raise ValueError("探索対象フォルダを1行に1件以上入力してください。")
    if any(not root.is_dir() for root in roots):
        raise NotADirectoryError("探索対象に存在しないフォルダまたはファイルが含まれています。")

    # Reuse the direct-search matcher by searching a temporary candidate list
    # would be wasteful; keep matching rules identical here.
    include, exclude = parse_query(query)
    if mode == "regex":
        try:
            include_patterns = tuple(re.compile(term) for term in include)
            exclude_patterns = tuple(re.compile(term) for term in exclude)
        except re.error as exc:
            raise ValueError("正規表現の書式が正しくありません。") from exc

        def matches(name: str) -> bool:
            return all(pattern.search(name) for pattern in include_patterns) and not any(
                pattern.search(name) for pattern in exclude_patterns
            )
    elif mode == "contains":
        def matches(name: str) -> bool:
            folded_name = name.casefold()
            return all(term.casefold() in folded_name for term in include) and not any(
                term.casefold() in folded_name for term in exclude
            )
    else:
        raise ValueError("未対応の検索方式です。")

    results: list[Path] = []
    for root in roots:
        first_level = sorted(
            (path for path in root.iterdir() if path.is_dir() and not path.is_symlink()),
            key=lambda path: path.name.casefold(),
        )
        for folder in first_level:
            candidates = sorted(folder.iterdir(), key=lambda path: path.name.casefold())
            for candidate in candidates:
                if item_kind == "file" and not candidate.is_file():
                    continue
                if item_kind == "directory" and not candidate.is_dir():
                    continue
                if matches(candidate.name):
                    results.append(normalize_path(candidate))
    return tuple(results)
