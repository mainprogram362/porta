"""Explicit, minimal local settings for the media-information workspace."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from settings.json_settings import atomic_write_json, create_app_settings_file, editable_settings_text, settings_destination, validated_settings_status
from settings.persistent_settings import config_path_text, resolve_config_path
from media.mpv_player import (
    default_shortcut_settings,
    upgrade_shortcut_settings,
    validate_shortcut_settings,
)


PROJECT_ROOT = Path(__file__).resolve().parents[4]
SETTINGS_FILE_NAME = "media_information.json"
SETTINGS_PATH: Path | None = None


DISPLAYABLE_PATH_COLUMNS = (
    "種別",
    "サイズ",
    "ファイル数",
    "メディア数",
    "拡張子",
    "解像度",
    "タグ",
    "評価",
    "見どころ",
)


def default_settings() -> dict[str, Any]:
    """Return explicit defaults without paths, history, or hidden state."""
    return {
        "registered_paths": [],
        "mpv_path": "/usr/bin/mpv",
        "mpv_tag_choices": [],
        "_mpv_tag_choices_help": (
            "mpv再生中に t を押すと、ここに登録したタグを選べます。"
            "見どころ範囲を ] で確定した際のコメント欄でも同じ候補を選べますが、そこではコメントとしてだけ記録します。"
            "選択画面では、この一覧以外のタグも直接入力できます。"
        ),
        # The path and state icon stay structural. This list controls only
        # optional fact columns.
        "default_visible_columns": ["サイズ", "評価", "見どころ"],
        # Core keys are stable and custom mpv keys are advanced, so keep this
        # compact section at the end of the user-facing template.
        "mpv_shortcuts": default_shortcut_settings(),
    }


def template_text() -> str:
    return json.dumps(default_settings(), ensure_ascii=False, indent=2) + "\n"


def editable_text() -> str:
    """Return saved source text, or the complete template when absent."""
    return editable_settings_text(
        SETTINGS_FILE_NAME,
        template_text(),
        override=SETTINGS_PATH,
        transform=_merged_editable_text,
    )


def _merged_editable_text(text: str) -> str:
    """Show newly added default sections without silently writing the file."""
    try:
        raw = json.loads(text)
    except json.JSONDecodeError:
        return text
    if not isinstance(raw, dict):
        return text
    # These were older saved destination/behavior values.  Destinations are
    # now session-only and single-JSON autofill is unconditional, so omit both
    # instead of making an otherwise usable existing file invalid.
    raw = dict(raw)
    raw.pop("catalog_json_path", None)
    raw.pop("auto_fill_single_json_path", None)
    raw.pop("_mpv_shortcuts_help", None)
    defaults = default_settings()
    if not set(raw).issubset(defaults):
        return text
    merged = dict(defaults)
    merged.update(raw)
    if "mpv_shortcuts" in raw:
        merged["mpv_shortcuts"] = upgrade_shortcut_settings(raw["mpv_shortcuts"])
    return json.dumps(merged, ensure_ascii=False, indent=2) + "\n"


def validate_text(text: str) -> dict[str, Any]:
    """Accept only documented local settings and never retain history."""
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
    registered_raw = raw.get("registered_paths", defaults["registered_paths"])
    if not isinstance(registered_raw, list) or not all(isinstance(value, str) for value in registered_raw):
        raise ValueError("registered_paths はパス文字列の配列にしてください。")
    registered_paths: list[str] = []
    for index, path_text in enumerate(registered_raw, start=1):
        text_value = path_text.strip()
        if not text_value:
            continue
        expanded = resolve_config_path(text_value)
        if "\x00" in expanded or not Path(expanded).is_absolute():
            raise ValueError(
                f"registered_paths の {index} 件目は共通パス記法または絶対パスにしてください。"
            )
        registered_paths.append(expanded)
    mpv_path = raw.get("mpv_path", defaults["mpv_path"])
    if not isinstance(mpv_path, str) or not mpv_path.strip():
        raise ValueError("mpv_path は実行ファイルの絶対パスにしてください。")
    try:
        resolved_mpv_path = resolve_config_path(mpv_path)
    except ValueError as exc:
        raise ValueError("mpv_path は共通パス記法または絶対パスにしてください。") from exc
    if not Path(resolved_mpv_path).is_absolute():
        raise ValueError("mpv_path は実行ファイルの絶対パスにしてください。")
    visible = raw.get("default_visible_columns", defaults["default_visible_columns"])
    if not isinstance(visible, list) or not all(isinstance(label, str) for label in visible):
        raise ValueError("default_visible_columns は表示する項目名の配列にしてください。")
    unknown_columns = set(visible) - set(DISPLAYABLE_PATH_COLUMNS)
    if unknown_columns:
        raise ValueError("表示できない項目があります: " + ", ".join(sorted(unknown_columns)))
    normalized_columns = [label for label in DISPLAYABLE_PATH_COLUMNS if label in visible]
    shortcuts = validate_shortcut_settings(raw.get("mpv_shortcuts", defaults["mpv_shortcuts"]))
    tag_choices = _validate_mpv_tag_choices(raw.get("mpv_tag_choices", defaults["mpv_tag_choices"]))
    tag_choices_help = raw.get("_mpv_tag_choices_help", defaults["_mpv_tag_choices_help"])
    if not isinstance(tag_choices_help, str):
        raise ValueError("_mpv_tag_choices_help は説明用の文字列にしてください。")
    return {
        "registered_paths": registered_paths,
        "mpv_path": resolved_mpv_path,
        "mpv_shortcuts": shortcuts,
        "mpv_tag_choices": tag_choices,
        "_mpv_tag_choices_help": tag_choices_help,
        "default_visible_columns": normalized_columns,
    }


def _validate_mpv_tag_choices(raw: Any) -> list[str]:
    """Validate compact reusable choices without turning them into history."""
    if not isinstance(raw, list) or not all(isinstance(value, str) for value in raw):
        raise ValueError("mpv_tag_choices はタグ文字列の一覧にしてください。")
    result: list[str] = []
    for value in raw:
        tag = value.strip()
        if not tag or "\n" in tag or "\r" in tag:
            raise ValueError("mpv_tag_choices のタグは空欄・改行なしの文字列にしてください。")
        if tag not in result:
            result.append(tag)
    return result
def load_settings() -> dict[str, Any]:
    """Use safe defaults if the optional local file is absent or invalid."""
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


def save_text(text: str) -> dict[str, Any]:
    """Atomically save only after the user explicitly chooses to save."""
    settings = validate_text(text)
    raw: Any = json.loads(text)
    defaults = default_settings()
    saved = {key: raw.get(key, defaults[key]) for key in defaults}
    saved["registered_paths"] = [config_path_text(path) for path in settings["registered_paths"]]
    saved["mpv_path"] = config_path_text(settings["mpv_path"])
    saved["mpv_shortcuts"] = settings["mpv_shortcuts"]
    saved["mpv_tag_choices"] = settings["mpv_tag_choices"]
    path = settings_destination(SETTINGS_FILE_NAME, SETTINGS_PATH)
    atomic_write_json(path, saved)
    return settings
