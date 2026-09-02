"""Filesystem and desktop helpers shared by path input widgets."""

from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import QMimeData, QUrl
from PySide6.QtGui import QDesktopServices

from foundation.path import normalize_path
from foundation.path_inspection import inspect_path


def local_paths_from_mime(mime_data: QMimeData) -> list[Path]:
    """Extract local file-system paths from a drag-and-drop payload."""
    if not mime_data.hasUrls():
        return []
    return [normalize_path(url.toLocalFile()) for url in mime_data.urls() if url.isLocalFile()]


def directory_for_path(path: Path) -> Path:
    """Return a directory itself, or the parent directory of a file."""
    return path if inspect_path(path).kind in {"directory", "symlink_directory"} else path.parent


def standard_file_manager_directory(path: Path | None = None) -> Path:
    """Return a real directory safe to hand to the desktop file manager."""
    if path is not None:
        info = inspect_path(path)
        if info.kind in {"directory", "symlink_directory"}:
            return info.path
        if info.kind in {"file", "symlink_file", "other", "symlink_other"}:
            parent = info.path.parent
            if parent.is_dir():
                return parent
    for fallback in (Path.home(), Path("/home"), Path("/")):
        if fallback.is_dir():
            return fallback
    return Path("/")


def open_in_standard_file_manager(path: Path | None = None) -> bool:
    """Ask the desktop to open a path's directory, or a safe fallback."""
    directory = standard_file_manager_directory(path)
    return QDesktopServices.openUrl(QUrl.fromLocalFile(str(directory)))


def direct_child_counts(path: Path) -> tuple[int, int, str]:
    """Return immediate file and directory counts without descending recursively."""
    files = 0
    directories = 0
    try:
        with os.scandir(path) as entries:
            for entry in entries:
                if entry.is_dir(follow_symlinks=False):
                    directories += 1
                elif entry.is_file(follow_symlinks=False):
                    files += 1
    except OSError as exc:
        return 0, 0, str(exc)
    return files, directories, ""
