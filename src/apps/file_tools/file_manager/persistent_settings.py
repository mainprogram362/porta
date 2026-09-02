"""Explicit, small, local-only settings for the file manager."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, TypedDict

from foundation.json_settings import (
    atomic_write_json,
    create_app_settings_file,
    editable_settings_text,
    settings_destination,
    validated_settings_status,
)
from foundation.path_tokens import expand_setting_path, home_tokenized

PROJECT_ROOT = Path(__file__).resolve().parents[4]
SETTINGS_FILE_NAME = "file_manager.json"
SETTINGS_PATH: Path | None = None

class FavoritePath(TypedDict):
    path: str
    initial_work_list: bool
    favorite: bool
    context_menu: bool


DEFAULT_SETTINGS: dict[str, list[FavoritePath]] = {
    "favorite_paths": [
        {
            "path": "@HOME",
            "initial_work_list": True,
            "favorite": True,
            "context_menu": True,
        },
        {
            "path": "",
            "initial_work_list": False,
            "favorite": False,
            "context_menu": False,
        },
        {
            "path": "",
            "initial_work_list": False,
            "favorite": False,
            "context_menu": False,
        },
        {
            "path": "",
            "initial_work_list": False,
            "favorite": False,
            "context_menu": False,
        },
        {
            "path": "",
            "initial_work_list": False,
            "favorite": False,
            "context_menu": False,
        },
    ],
}
_RETIRED_SETTINGS = {
    "default_work_list_paths",
    "registered_paths",
    "default_search_roots",
    "search_word_presets",
    "default_destination_paths",
}


def template_text() -> str:
    """Return the complete editable schema, without reading or writing a file."""
    return json.dumps(DEFAULT_SETTINGS, ensure_ascii=False, indent=2) + "\n"


def editable_text() -> str:
    """Return exactly the saved text, or the template when no local file exists."""
    return editable_settings_text(
        SETTINGS_FILE_NAME,
        template_text(),
        override=SETTINGS_PATH,
    )


def load_settings() -> dict[str, list[FavoritePath]]:
    """Load only validated defaults; invalid local text falls back to the template."""
    try:
        return validate_text(editable_text())
    except ValueError:
        return {key: list(value) for key, value in DEFAULT_SETTINGS.items()}


def settings_status() -> tuple[str, str]:
    return validated_settings_status(
        SETTINGS_FILE_NAME,
        validate_text,
        override=SETTINGS_PATH,
    )


def create_settings_file() -> Path:
    return create_app_settings_file(SETTINGS_FILE_NAME, template_text())


def validate_text(text: str) -> dict[str, list[FavoritePath]]:
    """Validate one path plus its three explicit display/use attributes."""
    try:
        raw: Any = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("JSONの形式が正しくありません。") from exc
    if not isinstance(raw, dict):
        raise ValueError("設定は { } で囲んだJSONオブジェクトにしてください。")
    # The next explicit save writes only the current, smaller schema.
    unknown = set(raw) - set(DEFAULT_SETTINGS) - _RETIRED_SETTINGS
    if unknown:
        raise ValueError("未対応の設定項目があります: " + ", ".join(sorted(unknown)))
    value = raw.get("favorite_paths")
    if value is None:
        return _migrate_legacy_settings(raw)
    if not isinstance(value, list):
        raise ValueError("favorite_paths はパス設定の配列にしてください。")
    result: list[FavoritePath] = []
    seen: set[str] = set()
    required = {"path", "initial_work_list", "favorite", "context_menu"}
    for index, item in enumerate(value, start=1):
        if not isinstance(item, dict) or set(item) != required:
            raise ValueError(
                "favorite_paths の各項目は path、initial_work_list、favorite、context_menu を持つJSONオブジェクトにしてください。"
            )
        raw_path = item["path"]
        if not isinstance(raw_path, str):
            raise ValueError(f"favorite_paths の {index} 件目の path は文字列にしてください。")
        if not all(isinstance(item[key], bool) for key in required - {"path"}):
            raise ValueError(f"favorite_paths の {index} 件目の属性は true または false にしてください。")
        path_text = raw_path.strip()
        # Empty slots are intentional: the editable template provides five
        # stable rows, while only rows with an actual path affect the UI.
        if not path_text:
            continue
        expanded = expand_setting_path(path_text)
        candidate = Path(expanded)
        if "\x00" in expanded or not candidate.is_absolute():
            raise ValueError(
                f"favorite_paths の {index} 件目は @HOME、~、または / から始まるパスにしてください。"
            )
        if expanded in seen:
            raise ValueError(f"favorite_paths に同じパスが重複しています: {expanded}")
        seen.add(expanded)
        result.append(
            FavoritePath(
                path=expanded,
                initial_work_list=item["initial_work_list"],
                favorite=item["favorite"],
                context_menu=item["context_menu"],
            )
        )
    return {"favorite_paths": result}


def _migrate_legacy_settings(raw: dict[str, Any]) -> dict[str, list[FavoritePath]]:
    """Keep prior saved choices usable until the user explicitly saves again."""
    by_path: dict[str, FavoritePath] = {}
    for key, attribute in (
        ("default_work_list_paths", "initial_work_list"),
        ("registered_paths", "context_menu"),
    ):
        values = raw.get(key, [])
        if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
            raise ValueError(f"{key} は文字列の配列にしてください。")
        for raw_path in values:
            if not raw_path.strip():
                continue
            expanded = expand_setting_path(raw_path.strip())
            if "\x00" in expanded or not Path(expanded).is_absolute():
                raise ValueError(f"{key} は @HOME、~、または / から始まるパスにしてください。")
            entry = by_path.setdefault(
                expanded,
                FavoritePath(
                    path=expanded,
                    initial_work_list=False,
                    favorite=False,
                    context_menu=False,
                ),
            )
            entry[attribute] = True  # type: ignore[literal-required]
    if by_path:
        return {"favorite_paths": list(by_path.values())}
    return {
        "favorite_paths": [
            FavoritePath(
                path=expand_setting_path(entry["path"]),
                initial_work_list=entry["initial_work_list"],
                favorite=entry["favorite"],
                context_menu=entry["context_menu"],
            )
            for entry in DEFAULT_SETTINGS["favorite_paths"]
            if entry["path"]
        ]
    }


def save_text(text: str) -> dict[str, list[FavoritePath]]:
    """Validate then atomically write settings only after an explicit save request."""
    settings = validate_text(text)
    raw: Any = json.loads(text)
    raw_entries = raw.get("favorite_paths")
    if isinstance(raw_entries, list):
        # Keep blank template slots visible after an explicit save, while the
        # returned runtime settings continue to ignore them.
        saved_entries = []
        for entry in raw_entries:
            assert isinstance(entry, dict)
            path_text = entry["path"].strip()
            saved_entries.append(
                {
                    "path": home_tokenized(expand_setting_path(path_text)) if path_text else "",
                    "initial_work_list": entry["initial_work_list"],
                    "favorite": entry["favorite"],
                    "context_menu": entry["context_menu"],
                }
            )
    else:
        saved_entries = [
            {
                "path": home_tokenized(entry["path"]),
                "initial_work_list": entry["initial_work_list"],
                "favorite": entry["favorite"],
                "context_menu": entry["context_menu"],
            }
            for entry in settings["favorite_paths"]
        ]
    saved = {"favorite_paths": saved_entries}
    path = settings_destination(SETTINGS_FILE_NAME, SETTINGS_PATH)
    atomic_write_json(path, saved)
    return settings
