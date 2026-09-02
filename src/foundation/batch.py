"""Batch file discovery and selection helpers for personal file-manager workflows.

This module is intentionally generic and reusable. It supports recursive
collection of files and folders, inclusion/exclusion rules, and grouping by
name so app-level code can build a file-manager UI without hardcoding every
search rule in each screen.
"""

from __future__ import annotations

import fnmatch
from collections import defaultdict
from pathlib import Path
from typing import Iterable, Iterator, Sequence

from .path import PathLike, normalize_path


def _matches_name(
    name: str, patterns: Sequence[str] | None, *, match_case: bool = False
) -> bool:
    """Return True when a filename matches any include/exclude pattern."""
    if not patterns:
        return True
    compare_name = name if match_case else name.lower()
    for pattern in patterns:
        test = pattern if match_case else pattern.lower()
        if fnmatch.fnmatch(compare_name, test):
            return True
    return False


def iter_paths(
    root: PathLike,
    *,
    recursive: bool = True,
    include_files: bool = True,
    include_dirs: bool = False,
    include_patterns: Sequence[str] | None = None,
    exclude_patterns: Sequence[str] | None = None,
    max_depth: int | None = None,
    match_case: bool = False,
    _seen_dirs: set[Path] | None = None,
) -> Iterator[Path]:
    """Yield matching files and/or folders under a root path.

    Examples:
      - include_patterns=("*.txt", "*.md")
      - exclude_patterns=("*.tmp", "__pycache__")
      - max_depth=2
    """
    root_path = normalize_path(root)
    if not root_path.exists():
        raise FileNotFoundError(f"対象のルートが存在しません: {root_path}")
    if max_depth is not None and max_depth < 0:
        raise ValueError("max_depth は 0 以上で指定してください")

    seen_dirs = _seen_dirs if _seen_dirs is not None else set()
    seen_dirs.add(root_path.resolve())

    def visit(current: Path, depth: int = 0) -> Iterator[Path]:
        if max_depth is not None and depth > max_depth:
            return

        try:
            entries = sorted(current.iterdir(), key=lambda p: p.name.lower())
        except OSError:
            return

        for entry in entries:
            entry_name = entry.name
            if exclude_patterns and _matches_name(
                entry_name, exclude_patterns, match_case=match_case
            ):
                continue

            try:
                resolved_entry = entry.resolve(strict=False)
            except OSError:
                resolved_entry = entry

            if entry.is_symlink() and resolved_entry in seen_dirs:
                continue

            if entry.is_dir():
                if include_dirs and _matches_name(
                    entry_name, include_patterns, match_case=match_case
                ):
                    yield entry
                if recursive and (max_depth is None or depth < max_depth):
                    target_dir = resolved_entry if entry.is_symlink() else entry
                    if target_dir.resolve() not in seen_dirs:
                        seen_dirs.add(target_dir.resolve())
                        yield from visit(target_dir, depth + 1)
                continue

            if (
                entry.is_file()
                and include_files
                and _matches_name(entry_name, include_patterns, match_case=match_case)
            ):
                yield entry

    yield from visit(root_path, 0)


def collect_items(
    root: PathLike,
    *,
    recursive: bool = True,
    include_files: bool = True,
    include_dirs: bool = False,
    include_names: Sequence[str] | None = None,
    exclude_names: Sequence[str] | None = None,
    extensions: Sequence[str] | None = None,
    max_depth: int | None = None,
    required_subdir: str | Sequence[str] | None = None,
    match_case: bool = False,
) -> list[Path]:
    """Collect items under a root by name patterns and optional subtree rules.

    This is the main discovery helper used by file-manager style tools.
    """
    root_path = normalize_path(root)

    required_subdirs = (
        [required_subdir]
        if isinstance(required_subdir, str)
        else (required_subdir or [])
    )
    include_patterns = [f"*{name}*" for name in (include_names or [])]
    if extensions:
        include_patterns.extend(f"*.{ext.lstrip('.').lower()}" for ext in extensions)
        if not match_case:
            include_patterns = [p.lower() for p in include_patterns]

    results: list[Path] = []
    for item in iter_paths(
        root_path,
        recursive=recursive,
        include_files=include_files,
        include_dirs=include_dirs,
        include_patterns=include_patterns or None,
        exclude_patterns=exclude_names,
        max_depth=max_depth,
        match_case=match_case,
    ):
        if not item.exists():
            continue

        if required_subdirs:
            rel = item.relative_to(root_path)
            rel_parts = rel.parts
            if not any(part in required_subdirs for part in rel_parts):
                continue

        if extensions and item.is_file():
            suffix = item.suffix.lower().lstrip(".")
            allowed = {ext.lower().lstrip(".") for ext in extensions}
            if suffix and suffix not in allowed:
                continue

        results.append(item)

    return sorted(results, key=lambda p: str(p).lower())


def group_by_name(paths: Iterable[PathLike]) -> dict[str, list[Path]]:
    """Group path objects by their filename/basename.

    Useful for operations such as 'collect all README files across many folders'.
    """
    grouped: dict[str, list[Path]] = defaultdict(list)
    for raw in paths:
        path = normalize_path(raw)
        grouped[path.name].append(path)
    return dict(sorted(grouped.items()))


def find_common_named_items(
    root: PathLike,
    *,
    names: Sequence[str] | str,
    recursive: bool = True,
    include_files: bool = True,
    include_dirs: bool = True,
    max_depth: int | None = None,
) -> dict[str, list[Path]]:
    """Find items whose names match a common name across many folders.

    Example usage:
      find_common_named_items("/path/project", names=("readme.txt", "picture"))
    returns a mapping like {'readme.txt': [Path(...), Path(...)]}
    """
    if isinstance(names, str):
        target_names = [names]
    else:
        target_names = list(names)

    all_items = collect_items(
        root,
        recursive=recursive,
        include_files=include_files,
        include_dirs=include_dirs,
        include_names=target_names,
        max_depth=max_depth,
    )
    return group_by_name(all_items)


if __name__ == "__main__":
    demo_root = normalize_path(".")
    found = collect_items(
        demo_root,
        recursive=True,
        include_files=True,
        include_dirs=False,
        include_names=("readme", "README"),
        max_depth=3,
    )
    print(f"Demo discovery count: {len(found)}")
    for item in found[:10]:
        print(item)
