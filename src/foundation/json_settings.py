"""Shared mechanics for explicit, app-owned JSON settings files.

Schemas remain in each app.  This module only centralizes path discovery,
read fallback, status reporting and atomic replacement.
"""

from __future__ import annotations

from collections.abc import Callable
import json
from pathlib import Path
from typing import Any

from .persistent_settings import create_settings_directory, settings_file_status


def readable_settings_path(filename: str, override: Path | None = None) -> Path | None:
    """Return the explicitly configured readable file, without creating it."""
    if override is not None:
        return override if override.is_file() else None
    status = settings_file_status(filename)
    return status.directory if status.state == "ready" and status.directory is not None else None


def editable_settings_text(
    filename: str,
    template: str,
    *,
    override: Path | None = None,
    transform: Callable[[str], str] | None = None,
) -> str:
    """Read source text or return a complete template when unavailable."""
    path = readable_settings_path(filename, override)
    if path is None:
        return template
    try:
        text = path.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        return template
    return transform(text) if transform is not None else text


def validated_settings_status(
    filename: str,
    validate: Callable[[str], object],
    *,
    override: Path | None = None,
    invalid_detail: str = "設定ファイルの内容が形式不正です。既定値を使用します。",
) -> tuple[str, str]:
    """Return the common settings status after applying an app schema."""
    if override is not None:
        if not override.is_file():
            return "missing_file", "このアプリの設定ファイルがありません。"
        path = override
    else:
        status = settings_file_status(filename)
        if status.state != "ready" or status.directory is None:
            return status.state, status.detail
        path = status.directory
    try:
        validate(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "invalid_contents", invalid_detail
    return "adopted", "設定を採用中です。"


def create_app_settings_file(filename: str, template: str) -> Path:
    """Create one missing app settings file without replacing existing data."""
    path = create_settings_directory() / filename
    if not path.exists():
        path.write_text(template, encoding="utf-8")
    return path


def settings_destination(filename: str, override: Path | None = None) -> Path:
    """Resolve the destination only at an explicit save point."""
    return override if override is not None else create_settings_directory() / filename


def atomic_write_json(path: Path, value: Any) -> Path:
    """Replace one JSON file atomically after its app has validated the value."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)
    return path
