"""One explicit shared configuration for portable local-AI files."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from foundation.path_tokens import expand_setting_path
from foundation.json_settings import (
    atomic_write_json,
    create_app_settings_file,
    editable_settings_text,
    settings_destination,
    validated_settings_status,
)


SETTINGS_FILE_NAME = "local_ai.json"
SETTINGS_PATH: Path | None = None


def default_settings() -> dict[str, str]:
    """Keep model locations shared, optional, and free of runtime state."""
    return {
        "runner_path": "",
        "model_path": "",
    }


def template_text() -> str:
    return json.dumps(default_settings(), ensure_ascii=False, indent=2) + "\n"


def editable_text() -> str:
    """Show a complete editable document without creating it while reading."""
    return editable_settings_text(
        SETTINGS_FILE_NAME,
        template_text(),
        override=SETTINGS_PATH,
        transform=_merged_editable_text,
    )


def _merged_editable_text(text: str) -> str:
    """Fill newly introduced keys without silently rewriting saved data."""
    try:
        raw: Any = json.loads(text)
    except json.JSONDecodeError:
        return template_text()
    if not isinstance(raw, dict) or not set(raw).issubset(default_settings()):
        return text
    merged = default_settings()
    merged.update(raw)
    return json.dumps(merged, ensure_ascii=False, indent=2) + "\n"


def validate_text(text: str) -> dict[str, str]:
    try:
        raw: Any = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("JSONの形式が正しくありません。") from exc
    if not isinstance(raw, dict) or set(raw) - set(default_settings()):
        raise ValueError("設定項目は runner_path と model_path のみです。")
    defaults = default_settings()
    return {
        "runner_path": _validate_optional_absolute_path(raw.get("runner_path", defaults["runner_path"]), "runner_path"),
        "model_path": _validate_optional_absolute_path(raw.get("model_path", defaults["model_path"]), "model_path"),
    }


def _validate_optional_absolute_path(value: Any, key: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{key} は空欄、または絶対パスにしてください。")
    text = value.strip()
    if not text:
        return ""
    expanded = expand_setting_path(text)
    if "\x00" in expanded or not Path(expanded).is_absolute():
        raise ValueError(f"{key} は @HOME、~、または / から始まる絶対パスにしてください。")
    return str(Path(expanded))


def load_settings() -> dict[str, str]:
    try:
        return validate_text(editable_text())
    except ValueError:
        return default_settings()


def settings_status() -> tuple[str, str]:
    return validated_settings_status(
        SETTINGS_FILE_NAME,
        validate_text,
        override=SETTINGS_PATH,
        invalid_detail="ローカルAI設定の内容が形式不正です。",
    )


def create_settings_file() -> Path:
    return create_app_settings_file(SETTINGS_FILE_NAME, template_text())


def save_text(text: str) -> dict[str, str]:
    settings = validate_text(text)
    path = settings_destination(SETTINGS_FILE_NAME, SETTINGS_PATH)
    atomic_write_json(path, settings)
    return settings
