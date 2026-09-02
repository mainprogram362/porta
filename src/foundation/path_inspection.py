"""Small, non-persistent inspection helpers for local file-system paths."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Literal

from .path import PathLike, normalize_path

PathKind = Literal[
    "file",
    "directory",
    "other",
    "symlink_file",
    "symlink_directory",
    "symlink_other",
    "symlink_broken",
    "missing",
    "unavailable",
]


@dataclass(frozen=True)
class PathInfo:
    """A snapshot of one path's current state, never stored by this module."""

    path: Path
    exists: bool
    kind: PathKind
    link_target: Path | None = None

    @property
    def is_operable(self) -> bool:
        """Whether this path entry can be safely passed to a file operation."""
        return self.kind not in {"missing", "unavailable"}


def inspect_path(raw_path: PathLike) -> PathInfo:
    """Return the current local state of a path without modifying anything."""
    path = normalize_path(raw_path)
    try:
        path.lstat()
    except FileNotFoundError:
        return PathInfo(path=path, exists=False, kind="missing")
    except OSError:
        return PathInfo(path=path, exists=False, kind="unavailable")

    if path.is_symlink():
        try:
            target = path.resolve(strict=False)
        except OSError:
            target = None
        try:
            if not path.exists():
                return PathInfo(path=path, exists=False, kind="symlink_broken", link_target=target)
            if path.is_dir():
                return PathInfo(path=path, exists=True, kind="symlink_directory", link_target=target)
            if path.is_file():
                return PathInfo(path=path, exists=True, kind="symlink_file", link_target=target)
        except OSError:
            return PathInfo(path=path, exists=False, kind="unavailable", link_target=target)
        return PathInfo(path=path, exists=True, kind="symlink_other", link_target=target)

    try:
        if path.is_dir():
            return PathInfo(path=path, exists=True, kind="directory")
        if path.is_file():
            return PathInfo(path=path, exists=True, kind="file")
    except OSError:
        return PathInfo(path=path, exists=False, kind="unavailable")
    return PathInfo(path=path, exists=True, kind="other")


def inspect_paths(paths: Iterable[PathLike]) -> tuple[PathInfo, ...]:
    """Inspect paths in order, returning independent current-state snapshots."""
    return tuple(inspect_path(path) for path in paths)
