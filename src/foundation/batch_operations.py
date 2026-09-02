"""Reusable batch operations for file-manager style workflows.

These helpers are intentionally generic: they operate on lists of files/folders
that were discovered by foundation.batch and can be reused by app-level
workflows or GUI actions.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Iterable, Sequence

from .filesystem import copy_or_move, ensure_unique_destination
from .path import PathLike, normalize_path


def batch_copy(
    paths: Iterable[PathLike], dst_root: PathLike, *, create_dirs: bool = True
) -> list[Path]:
    """Copy every path into dst_root, preserving the filename or creating a unique name."""
    destination_root = normalize_path(dst_root)
    if create_dirs:
        destination_root.mkdir(parents=True, exist_ok=True)

    results: list[Path] = []
    for raw in paths:
        src = normalize_path(raw)
        if not src.exists():
            continue
        target = destination_root / src.name
        target = ensure_unique_destination(target)
        copy_or_move(src, target, mode="copy", overwrite=False, create_dirs=create_dirs)
        results.append(target)
    return results


def batch_move(
    paths: Iterable[PathLike], dst_root: PathLike, *, create_dirs: bool = True
) -> list[Path]:
    """Move every path into dst_root, preserving the filename or creating a unique name."""
    destination_root = normalize_path(dst_root)
    if create_dirs:
        destination_root.mkdir(parents=True, exist_ok=True)

    results: list[Path] = []
    for raw in paths:
        src = normalize_path(raw)
        if not src.exists():
            continue
        target = destination_root / src.name
        target = ensure_unique_destination(target)
        copy_or_move(src, target, mode="move", overwrite=False, create_dirs=create_dirs)
        results.append(target)
    return results


def batch_zip(
    paths: Iterable[PathLike], dst_root: PathLike, *, zip_suffix: str = ".zip"
) -> list[Path]:
    """Zip each target file into dst_root."""
    destination_root = normalize_path(dst_root)
    destination_root.mkdir(parents=True, exist_ok=True)

    archives: list[Path] = []
    for raw in paths:
        src = normalize_path(raw)
        if not src.is_file():
            continue
        archive_path = destination_root / f"{src.stem}{zip_suffix}"
        archive_path = ensure_unique_destination(archive_path)
        shutil.make_archive(
            str(archive_path.with_suffix("")),
            "zip",
            root_dir=str(src.parent),
            base_dir=src.name,
        )
        archives.append(archive_path)
    return archives


def filter_paths(
    paths: Iterable[PathLike],
    *,
    include_names: Sequence[str] | None = None,
    exclude_names: Sequence[str] | None = None,
    extensions: Sequence[str] | None = None,
    only_files: bool = True,
    only_dirs: bool = False,
) -> list[Path]:
    """Generic file/directory filtering helper for UI-driven selection logic."""
    include = tuple(include_names or ())
    exclude = tuple(exclude_names or ())
    allowed_extensions = {ext.lower().lstrip(".") for ext in (extensions or ())}

    results: list[Path] = []
    for raw in paths:
        path = normalize_path(raw)
        if not path.exists():
            continue

        if only_files and not path.is_file():
            continue
        if only_dirs and not path.is_dir():
            continue

        name = path.name.lower()
        if include and not any(pattern.lower() in name for pattern in include):
            continue
        if exclude and any(pattern.lower() in name for pattern in exclude):
            continue
        if allowed_extensions and path.is_file():
            suffix = path.suffix.lower().lstrip(".")
            if suffix and suffix not in allowed_extensions:
                continue

        results.append(path)
    return results
