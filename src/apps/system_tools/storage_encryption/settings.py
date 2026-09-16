"""Explicit local-only settings for encrypted-storage mount presets.

The settings deliberately contain paths and display names only.  Passphrases,
key files, mapper names, mount history, and execution results are never saved.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from settings.json_settings import atomic_write_json, create_app_settings_file, settings_destination
from settings.persistent_settings import config_path_text, resolve_config_path, settings_file_status


SETTINGS_FILE_NAME = "storage_encryption.json"
# Test-only override; normal operation always resolves through the bootstrap file.
SETTINGS_PATH: Path | None = None
_ROOT_KEYS = {"container_favorites", "mount_point_favorites", "mount_presets"}
_PRESET_KEYS = {"name", "container_path", "mount_point"}
_SLOT_COUNT = 5


@dataclass(frozen=True)
class MountPreset:
    """One intentionally named pair of an encrypted source and mount point."""

    name: str
    container_path: str
    mount_point: str


@dataclass(frozen=True)
class StorageEncryptionSettings:
    """Validated, absolute paths ready to present in the mount screen."""

    container_favorites: tuple[str, ...]
    mount_point_favorites: tuple[str, ...]
    mount_presets: tuple[MountPreset, ...]


def template_text() -> str:
    """Return a complete non-sensitive JSON schema without writing it."""
    return json.dumps(
        {
            "container_favorites": ["" for _ in range(_SLOT_COUNT)],
            "mount_point_favorites": ["" for _ in range(_SLOT_COUNT)],
            "mount_presets": [
                {"name": "", "container_path": "", "mount_point": ""}
                for _ in range(_SLOT_COUNT)
            ],
        },
        ensure_ascii=False,
        indent=2,
    ) + "\n"


def editable_text() -> str:
    """Return saved raw JSON, or the template when the file is absent."""
    path = _settings_path_if_present()
    if path is None:
        return template_text()
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return template_text()


def validate_text(
    text: str, *, base_directory: Path | None = None
) -> StorageEncryptionSettings:
    """Validate the whole schema; a malformed subset is never adopted."""
    try:
        raw: Any = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("JSONの形式が正しくありません。") from exc
    if not isinstance(raw, dict) or set(raw) != _ROOT_KEYS:
        raise ValueError(
            "設定の最上位項目は container_favorites、mount_point_favorites、mount_presets のみです。"
        )
    if base_directory is None:
        base_directory = configuration_base_directory()

    container_favorites = _validate_path_list(
        raw["container_favorites"], "container_favorites", base_directory
    )
    mount_point_favorites = _validate_path_list(
        raw["mount_point_favorites"], "mount_point_favorites", base_directory
    )
    mount_presets = _validate_presets(raw["mount_presets"], base_directory)
    return StorageEncryptionSettings(container_favorites, mount_point_favorites, mount_presets)


def load_settings() -> StorageEncryptionSettings:
    """Load only a fully valid document; otherwise expose no saved paths."""
    path = _settings_path_if_present()
    if path is None:
        return StorageEncryptionSettings((), (), ())
    try:
        return validate_text(path.read_text(encoding="utf-8"), base_directory=path.parent)
    except (OSError, ValueError):
        return StorageEncryptionSettings((), (), ())


def settings_status() -> tuple[str, str]:
    """Describe whether one complete settings document can be adopted."""
    if SETTINGS_PATH is not None:
        path = SETTINGS_PATH
        if not path.is_file():
            return "missing_file", "このアプリの設定ファイルがありません。"
    else:
        status = settings_file_status(SETTINGS_FILE_NAME)
        if status.state != "ready" or status.directory is None:
            return status.state, status.detail
        path = status.directory
    try:
        validate_text(path.read_text(encoding="utf-8"), base_directory=path.parent)
    except OSError as exc:
        return "unreadable_file", f"設定ファイルを読めません: {exc}"
    except ValueError as exc:
        return "invalid_contents", f"設定の形式が正しくありません: {exc}"
    return "adopted", "設定を採用中です。"


def create_settings_file() -> Path:
    """Create this app's template only after an explicit user request."""
    return create_app_settings_file(SETTINGS_FILE_NAME, template_text())


def configuration_base_directory() -> Path | None:
    """Return the stable base used to expand relative paths in this JSON."""
    if SETTINGS_PATH is not None:
        return SETTINGS_PATH.parent
    status = settings_file_status(SETTINGS_FILE_NAME)
    if status.state in {"ready", "missing_file", "not_file"} and status.directory is not None:
        return status.directory.parent
    return None


def save_text(text: str) -> StorageEncryptionSettings:
    """Validate then atomically persist exactly the explicitly entered text."""
    path = settings_destination(SETTINGS_FILE_NAME, SETTINGS_PATH)
    settings = validate_text(text, base_directory=path.parent)
    raw = json.loads(text)
    raw["container_favorites"] = [
        config_path_text(value, base_directory=path.parent) if value.strip() else ""
        for value in raw["container_favorites"]
    ]
    raw["mount_point_favorites"] = [
        config_path_text(value, base_directory=path.parent) if value.strip() else ""
        for value in raw["mount_point_favorites"]
    ]
    for preset in raw["mount_presets"]:
        if preset["container_path"].strip():
            preset["container_path"] = config_path_text(
                preset["container_path"], base_directory=path.parent
            )
            preset["mount_point"] = config_path_text(
                preset["mount_point"], base_directory=path.parent
            )
    atomic_write_json(path, raw)
    return settings


def _settings_path_if_present() -> Path | None:
    if SETTINGS_PATH is not None:
        return SETTINGS_PATH if SETTINGS_PATH.is_file() else None
    status = settings_file_status(SETTINGS_FILE_NAME)
    return status.directory if status.state == "ready" and status.directory is not None else None


def _validate_path_list(value: Any, key: str, base_directory: Path | None) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{key} はパス文字列の配列にしてください。")
    paths: list[str] = []
    for index, item in enumerate(value, start=1):
        text = item.strip()
        if not text:
            continue
        paths.append(_expand_path(text, f"{key} の {index} 件目", base_directory))
    return tuple(paths)


def _validate_presets(value: Any, base_directory: Path | None) -> tuple[MountPreset, ...]:
    if not isinstance(value, list):
        raise ValueError("mount_presets はプリセットの配列にしてください。")
    presets: list[MountPreset] = []
    names: set[str] = set()
    for index, item in enumerate(value, start=1):
        prefix = f"mount_presets[{index}]"
        if not isinstance(item, dict) or set(item) != _PRESET_KEYS:
            raise ValueError(f"{prefix} には name、container_path、mount_point の3項目が必要です。")
        values = {key: item[key] for key in _PRESET_KEYS}
        if not all(isinstance(item_value, str) for item_value in values.values()):
            raise ValueError(f"{prefix} の3項目はすべて文字列にしてください。")
        name = values["name"].strip()
        container_text = values["container_path"].strip()
        mount_text = values["mount_point"].strip()
        if not any((name, container_text, mount_text)):
            continue
        if not all((name, container_text, mount_text)):
            raise ValueError(
                f"{prefix} は未使用なら3項目すべてを空欄にしてください。"
                "使う場合は名前・コンテナ・マウント先をすべて入力してください。"
            )
        if name in names:
            raise ValueError("mount_presets の名前は重複させないでください。")
        names.add(name)
        presets.append(
            MountPreset(
                name=name,
                container_path=_expand_path(container_text, f"{prefix}.container_path", base_directory),
                mount_point=_expand_path(mount_text, f"{prefix}.mount_point", base_directory),
            )
        )
    return tuple(presets)


def _expand_path(value: str, label: str, base_directory: Path | None) -> str:
    try:
        return resolve_config_path(value, base_directory=base_directory)
    except ValueError as exc:
        raise ValueError(f"{label}: {exc}") from exc
