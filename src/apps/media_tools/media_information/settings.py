"""Explicit, minimal local settings for the media-information workspace."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from foundation.json_settings import (
    atomic_write_json,
    create_app_settings_file,
    editable_settings_text,
    settings_destination,
    validated_settings_status,
)
from foundation.path_tokens import expand_setting_path
from media.mpv_player import (
    default_shortcut_settings,
    shortcut_help,
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
        "auto_fill_single_json_path": True,
        "registered_paths": [],
        "mpv_path": "/usr/bin/mpv",
        "mpv_shortcuts": default_shortcut_settings(),
        "_mpv_shortcuts_help": shortcut_help(),
        "mpv_tag_choices": [],
        "_mpv_tag_choices_help": (
            "mpv再生中に t を押すと、ここに登録したタグを選べます。"
            "見どころ範囲を ] で確定した際のコメント欄でも同じ候補を選べますが、そこではコメントとしてだけ記録します。"
            "選択画面では、この一覧以外のタグも直接入力できます。"
        ),
        # The path and state icon stay structural. This list controls only
        # optional fact columns.
        "default_visible_columns": ["サイズ", "評価", "見どころ"],
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
    # ``catalog_json_path`` was an older saved destination.  Destinations are
    # now session-only, so omit it when presenting an existing settings file
    # instead of making the whole file invalid.
    raw = dict(raw)
    raw.pop("catalog_json_path", None)
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
    auto_fill_single_json_path = raw.get(
        "auto_fill_single_json_path", defaults["auto_fill_single_json_path"]
    )
    if not isinstance(auto_fill_single_json_path, bool):
        raise ValueError("auto_fill_single_json_path は true または false にしてください。")
    registered_raw = raw.get("registered_paths", defaults["registered_paths"])
    if not isinstance(registered_raw, list) or not all(isinstance(value, str) for value in registered_raw):
        raise ValueError("registered_paths はパス文字列の配列にしてください。")
    registered_paths: list[str] = []
    for index, path_text in enumerate(registered_raw, start=1):
        text_value = path_text.strip()
        if not text_value:
            continue
        expanded = expand_setting_path(text_value)
        if "\x00" in expanded or not Path(expanded).is_absolute():
            raise ValueError(
                f"registered_paths の {index} 件目は @HOME、~、または / から始まるパスにしてください。"
            )
        registered_paths.append(expanded)
    mpv_path = raw.get("mpv_path", defaults["mpv_path"])
    if not isinstance(mpv_path, str) or not mpv_path.strip():
        raise ValueError("mpv_path は実行ファイルの絶対パスにしてください。")
    if not Path(mpv_path).expanduser().is_absolute():
        raise ValueError("mpv_path は実行ファイルの絶対パスにしてください。")
    visible = raw.get("default_visible_columns", defaults["default_visible_columns"])
    if not isinstance(visible, list) or not all(isinstance(label, str) for label in visible):
        raise ValueError("default_visible_columns は表示する項目名の配列にしてください。")
    unknown_columns = set(visible) - set(DISPLAYABLE_PATH_COLUMNS)
    if unknown_columns:
        raise ValueError("表示できない項目があります: " + ", ".join(sorted(unknown_columns)))
    normalized_columns = [label for label in DISPLAYABLE_PATH_COLUMNS if label in visible]
    shortcuts = validate_shortcut_settings(raw.get("mpv_shortcuts", defaults["mpv_shortcuts"]))
    help_text = raw.get("_mpv_shortcuts_help", defaults["_mpv_shortcuts_help"])
    if not isinstance(help_text, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in help_text.items()
    ):
        raise ValueError("_mpv_shortcuts_help は説明用の文字列一覧にしてください。")
    tag_choices = _validate_mpv_tag_choices(raw.get("mpv_tag_choices", defaults["mpv_tag_choices"]))
    tag_choices_help = raw.get("_mpv_tag_choices_help", defaults["_mpv_tag_choices_help"])
    if not isinstance(tag_choices_help, str):
        raise ValueError("_mpv_tag_choices_help は説明用の文字列にしてください。")
    return {
        "auto_fill_single_json_path": auto_fill_single_json_path,
        "registered_paths": registered_paths,
        "mpv_path": str(Path(mpv_path).expanduser()),
        "mpv_shortcuts": shortcuts,
        "_mpv_shortcuts_help": dict(help_text),
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
    saved["mpv_shortcuts"] = settings["mpv_shortcuts"]
    saved["mpv_tag_choices"] = settings["mpv_tag_choices"]
    path = settings_destination(SETTINGS_FILE_NAME, SETTINGS_PATH)
    atomic_write_json(path, saved)
    return settings
