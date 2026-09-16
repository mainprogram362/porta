"""Explicit local-only settings for the YouTube downloader."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from settings.json_settings import atomic_write_json, create_app_settings_file, editable_settings_text, settings_destination, validated_settings_status
from settings.persistent_settings import config_path_text, resolve_config_path


PROJECT_ROOT = Path(__file__).resolve().parents[4]
SETTINGS_FILE_NAME = "youtube_downloader.json"
SETTINGS_PATH: Path | None = None


def default_settings() -> dict[str, str]:
    """Return safe per-user defaults without requiring a configuration file."""
    home = str(Path.home())
    return {
        "download_output_directory": home,
        "metadata_export_directory": home,
        # An empty value deliberately means "follow this run's video output
        # directory".  No catalog is created until the user checks the
        # explicit catalog option in the download screen.
        "catalog_json_path": "",
    }


def template_text() -> str:
    return json.dumps(
        {key: config_path_text(value) if value else "" for key, value in default_settings().items()},
        ensure_ascii=False,
        indent=2,
    ) + "\n"


def editable_text() -> str:
    """Return saved text exactly, or a complete template when it does not exist."""
    return editable_settings_text(
        SETTINGS_FILE_NAME,
        template_text(),
        override=SETTINGS_PATH,
    )


def validate_text(text: str) -> dict[str, str]:
    """Accept only the small, documented settings schema."""
    try:
        raw: Any = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("JSONの形式が正しくありません。") from exc
    if not isinstance(raw, dict):
        raise ValueError("設定は { } で囲んだJSONオブジェクトにしてください。")
    defaults = default_settings()
    unknown = set(raw) - set(defaults)
    if unknown:
        raise ValueError("未対応の設定項目があります: " + ", ".join(sorted(unknown)))
    result: dict[str, str] = {}
    for key, fallback in defaults.items():
        value = raw.get(key, fallback)
        if key == "catalog_json_path" and value == "":
            result[key] = ""
            continue
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{key} は空でないパス文字列にしてください。")
        result[key] = resolve_config_path(value.strip())
    return result


def load_settings() -> dict[str, str]:
    """Use safe defaults when local settings are absent or malformed."""
    try:
        return validate_text(editable_text())
    except ValueError:
        return default_settings()


def settings_status() -> tuple[str, str]:
    return validated_settings_status(
        SETTINGS_FILE_NAME,
        validate_text,
        override=SETTINGS_PATH,
    )


def create_settings_file() -> Path:
    return create_app_settings_file(SETTINGS_FILE_NAME, template_text())


def save_text(text: str) -> dict[str, str]:
    """Atomically persist settings only after an explicit save action."""
    settings = validate_text(text)
    saved = {key: config_path_text(value) if value else "" for key, value in settings.items()}
    path = settings_destination(SETTINGS_FILE_NAME, SETTINGS_PATH)
    atomic_write_json(path, saved)
    return settings
