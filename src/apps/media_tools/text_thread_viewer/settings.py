"""Persistent favorite locations for the read-only text-thread viewer."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, TypedDict

from settings.json_settings import atomic_write_json, create_app_settings_file, editable_settings_text, settings_destination, validated_settings_status
from settings.persistent_settings import config_path_text, resolve_config_path


SETTINGS_FILE_NAME = "text_thread_viewer.json"
SETTINGS_PATH: Path | None = None


class FavoritePath(TypedDict):
    path: str
    initial_input: bool


def default_settings() -> dict[str, list[FavoritePath]]:
    return {"favorite_paths": []}


def template_text() -> str:
    """Keep one visible blank row while an empty path has no effect."""
    return json.dumps(
        {"favorite_paths": [{"path": "", "initial_input": False}]},
        ensure_ascii=False,
        indent=2,
    ) + "\n"


def editable_text() -> str:
    return editable_settings_text(SETTINGS_FILE_NAME, template_text(), override=SETTINGS_PATH)


def validate_text(text: str) -> dict[str, list[FavoritePath]]:
    try:
        raw: Any = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("JSONの形式が正しくありません。") from exc
    if not isinstance(raw, dict) or set(raw) != {"favorite_paths"}:
        raise ValueError("設定項目は favorite_paths だけにしてください。")
    entries = raw["favorite_paths"]
    if not isinstance(entries, list):
        raise ValueError("favorite_paths はパス設定の配列にしてください。")
    result: list[FavoritePath] = []
    seen: set[str] = set()
    for index, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict) or set(entry) != {"path", "initial_input"}:
            raise ValueError(
                "favorite_paths の各項目には path と initial_input が必要です。"
            )
        path_text = entry["path"]
        initial = entry["initial_input"]
        if not isinstance(path_text, str) or not isinstance(initial, bool):
            raise ValueError(
                f"favorite_paths の {index} 件目は、pathを文字列、initial_inputをtrue/falseにしてください。"
            )
        path_text = path_text.strip()
        if not path_text:
            continue
        expanded = resolve_config_path(path_text)
        if "\x00" in expanded or not Path(expanded).is_absolute():
            raise ValueError(
                f"favorite_paths の {index} 件目は共通パス記法または絶対パスにしてください。"
            )
        if expanded in seen:
            raise ValueError(f"favorite_paths に同じパスが重複しています: {expanded}")
        seen.add(expanded)
        result.append(FavoritePath(path=expanded, initial_input=initial))
    return {"favorite_paths": result}


def load_settings() -> dict[str, list[FavoritePath]]:
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


def save_text(text: str) -> dict[str, list[FavoritePath]]:
    settings = validate_text(text)
    raw: Any = json.loads(text)
    saved_entries = []
    for entry in raw["favorite_paths"]:
        path_text = entry["path"].strip()
        saved_entries.append(
            {
                "path": config_path_text(resolve_config_path(path_text)) if path_text else "",
                "initial_input": entry["initial_input"],
            }
        )
    atomic_write_json(
        settings_destination(SETTINGS_FILE_NAME, SETTINGS_PATH),
        {"favorite_paths": saved_entries},
    )
    return settings
