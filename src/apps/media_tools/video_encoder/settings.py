"""Small, explicit persistent settings for the video encoder."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, TypedDict

from settings.json_settings import atomic_write_json, create_app_settings_file, editable_settings_text, settings_destination, validated_settings_status
from settings.persistent_settings import config_path_text, resolve_config_path


PROJECT_ROOT = Path(__file__).resolve().parents[4]
SETTINGS_FILE_NAME = "video_encoder.json"
SETTINGS_PATH: Path | None = None


class EncoderPathSetting(TypedDict):
    path: str
    initial_source: bool
    initial_output: bool
    context_menu: bool


class EncoderSettings(TypedDict):
    path_settings: list[EncoderPathSetting]
    ffmpeg_path: str
    ffprobe_path: str


_PATH_ATTRIBUTES = {
    "initial_source",
    "initial_output",
    "context_menu",
}
_LEGACY_PATH_ATTRIBUTES = {"source_context_menu", "output_context_menu"}
_CURRENT_KEYS = {"path_settings", "ffmpeg_path", "ffprobe_path"}
_LEGACY_KEYS = {
    "default_source_paths",
    "default_output_directory",
    "ffmpeg_path",
    "ffprobe_path",
}


def _blank_path(path: str = "", **attributes: bool) -> dict[str, str | bool]:
    """Build a readable template row; false attributes are intentionally omitted."""
    return {"path": path, **{key: True for key, value in attributes.items() if value}}


def default_settings() -> EncoderSettings:
    return {"path_settings": [], "ffmpeg_path": "", "ffprobe_path": ""}


def template_text() -> str:
    # Five stable blanks make the file easy to fill in. A blank path has no
    # effect. The first two rows document the available roles.
    template = {
        "path_settings": [
            _blank_path("", initial_source=True, context_menu=True),
            _blank_path("", initial_output=True, context_menu=True),
            _blank_path(),
            _blank_path(),
            _blank_path(),
        ],
        "ffmpeg_path": "",
        "ffprobe_path": "",
    }
    return json.dumps(template, ensure_ascii=False, indent=2) + "\n"


def help_text() -> str:
    """Explain the small path-role schema beside the editable JSON."""
    return (
        "path_settings では、1つの path に次の属性を付けられます。\n"
        "・initial_source: 起動時に変換対象一覧へ入れる\n"
        "・initial_output: 起動時の出力先欄へ入れる（true は1件だけ）\n"
        "・context_menu: 入力一覧と出力先欄、両方の右クリック候補へ出す\n"
        "\n"
        "書き方の例:\n"
        '{"path": "@HOME/Videos", "initial_source": true, "context_menu": true}\n'
        '{"path": "@HOME/Videos/encoded", "initial_output": true, "context_menu": true}\n'
        "\n"
        "同じ path に複数属性を書けます。属性を省略した場合は false です。\n"
        "path が空欄の行は無視します。@HOME、@PORTA、@USER、@CONFIG と相対パスも使えます。\n"
        "変換条件・履歴・実行結果は保存しません。FFmpegのパスが空なら、"
        "PORTA内バックエンド、パソコン本体の順に自動探索し、見つからなければ空欄になります。"
    )


def editable_text() -> str:
    return editable_settings_text(SETTINGS_FILE_NAME, template_text(), override=SETTINGS_PATH)


def _validated_tool_path(raw: dict[str, Any], key: str) -> str:
    value = raw.get(key, "")
    if not isinstance(value, str):
        raise ValueError(f"{key} は文字列にしてください。")
    return resolve_config_path(value) if value.strip() else ""


def _validate_path_settings(value: Any) -> list[EncoderPathSetting]:
    if not isinstance(value, list):
        raise ValueError("path_settings はパス設定の配列にしてください。")
    result: list[EncoderPathSetting] = []
    seen: set[str] = set()
    initial_outputs = 0
    allowed = {"path", *_PATH_ATTRIBUTES, *_LEGACY_PATH_ATTRIBUTES}
    for index, item in enumerate(value, start=1):
        if not isinstance(item, dict) or "path" not in item or set(item) - allowed:
            raise ValueError("path_settings の各項目には path と、対応する役割だけを指定してください。")
        raw_path = item["path"]
        if not isinstance(raw_path, str):
            raise ValueError(f"path_settings の {index} 件目の path は文字列にしてください。")
        for attribute in _PATH_ATTRIBUTES | _LEGACY_PATH_ATTRIBUTES:
            if attribute in item and not isinstance(item[attribute], bool):
                raise ValueError(
                    f"path_settings の {index} 件目の {attribute} は true または false にしてください。"
                )
        path_text = raw_path.strip()
        if not path_text:
            continue
        expanded = resolve_config_path(path_text)
        if "\x00" in expanded or not Path(expanded).is_absolute():
            raise ValueError(
                f"path_settings の {index} 件目は共通パス記法または絶対パスにしてください。"
            )
        if expanded in seen:
            raise ValueError(f"path_settings に同じパスが重複しています: {expanded}")
        seen.add(expanded)
        entry = EncoderPathSetting(
            path=expanded,
            initial_source=item.get("initial_source", False),
            initial_output=item.get("initial_output", False),
            context_menu=(
                item.get("context_menu", False)
                or item.get("source_context_menu", False)
                or item.get("output_context_menu", False)
            ),
        )
        initial_outputs += int(entry["initial_output"])
        result.append(entry)
    if initial_outputs > 1:
        raise ValueError("initial_output: true にできるパスは1件だけです。")
    return result


def _migrate_legacy_settings(raw: dict[str, Any]) -> EncoderSettings:
    source_paths = raw.get("default_source_paths", [])
    output_path = raw.get("default_output_directory", "")
    if not isinstance(source_paths, list) or not all(
        isinstance(value, str) and value.strip() for value in source_paths
    ):
        raise ValueError("default_source_paths はパス文字列の配列にしてください（空配列可）。")
    if not isinstance(output_path, str):
        raise ValueError("default_output_directory は文字列にしてください。")
    entries: list[dict[str, Any]] = [
        {"path": value, "initial_source": True} for value in source_paths
    ]
    if output_path.strip():
        existing = next((entry for entry in entries if entry["path"] == output_path), None)
        if existing is None:
            entries.append({"path": output_path, "initial_output": True})
        else:
            existing["initial_output"] = True
    return EncoderSettings(
        path_settings=_validate_path_settings(entries),
        ffmpeg_path=_validated_tool_path(raw, "ffmpeg_path"),
        ffprobe_path=_validated_tool_path(raw, "ffprobe_path"),
    )


def validate_text(text: str) -> EncoderSettings:
    try:
        raw: Any = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("JSONの形式が正しくありません。") from exc
    if not isinstance(raw, dict):
        raise ValueError("設定は { } で囲んだJSONオブジェクトにしてください。")
    if "path_settings" not in raw:
        unknown = set(raw) - _LEGACY_KEYS
        if unknown:
            raise ValueError("未対応の設定項目があります: " + ", ".join(sorted(unknown)))
        return _migrate_legacy_settings(raw)
    unknown = set(raw) - _CURRENT_KEYS
    if unknown:
        raise ValueError("未対応の設定項目があります: " + ", ".join(sorted(unknown)))
    return EncoderSettings(
        path_settings=_validate_path_settings(raw["path_settings"]),
        ffmpeg_path=_validated_tool_path(raw, "ffmpeg_path"),
        ffprobe_path=_validated_tool_path(raw, "ffprobe_path"),
    )


def paths_for(settings_value: EncoderSettings | dict[str, Any], attribute: str) -> list[str]:
    """Return paths assigned to one role, accepting legacy runtime dictionaries."""
    entries = settings_value.get("path_settings")
    if isinstance(entries, list):
        requested_attribute = (
            "context_menu"
            if attribute in {"source_context_menu", "output_context_menu"}
            else attribute
        )
        return [
            str(entry["path"])
            for entry in entries
            if isinstance(entry, dict)
            and (
                entry.get(requested_attribute)
                or (
                    requested_attribute == "context_menu"
                    and (entry.get("source_context_menu") or entry.get("output_context_menu"))
                )
            )
            and entry.get("path")
        ]
    if attribute == "initial_source":
        values = settings_value.get("default_source_paths", [])
        return list(values) if isinstance(values, list) else []
    if attribute == "initial_output":
        value = settings_value.get("default_output_directory", "")
        return [value] if isinstance(value, str) and value else []
    return []


def load_settings() -> EncoderSettings:
    try:
        return validate_text(editable_text())
    except ValueError:
        return default_settings()


def settings_status() -> tuple[str, str]:
    return validated_settings_status(SETTINGS_FILE_NAME, validate_text, override=SETTINGS_PATH)


def create_settings_file() -> Path:
    return create_app_settings_file(SETTINGS_FILE_NAME, template_text())


def _saved_path_entries(text: str, settings_value: EncoderSettings) -> list[dict[str, Any]]:
    """Keep blank slots, but save only true roles; omitted roles mean false."""
    raw: Any = json.loads(text)
    raw_entries = raw.get("path_settings")
    if not isinstance(raw_entries, list):
        raw_entries = settings_value["path_settings"]
    saved: list[dict[str, Any]] = []
    normalized_by_path = {entry["path"]: entry for entry in settings_value["path_settings"]}
    for raw_entry in raw_entries:
        if not isinstance(raw_entry, dict):
            continue
        path_value = raw_entry.get("path", "")
        path_text = path_value.strip() if isinstance(path_value, str) else ""
        if not path_text:
            blank: dict[str, Any] = {"path": ""}
            blank.update(
                {
                    attribute: True
                    for attribute in _PATH_ATTRIBUTES
                    if raw_entry.get(attribute) is True
                }
            )
            saved.append(blank)
            continue
        expanded = resolve_config_path(path_text)
        entry = normalized_by_path[expanded]
        output: dict[str, Any] = {"path": config_path_text(expanded)}
        output.update({attribute: True for attribute in _PATH_ATTRIBUTES if entry[attribute]})
        saved.append(output)
    return saved


def save_text(text: str) -> EncoderSettings:
    settings_value = validate_text(text)
    saved = {
        "path_settings": _saved_path_entries(text, settings_value),
        "ffmpeg_path": config_path_text(settings_value["ffmpeg_path"]) if settings_value["ffmpeg_path"] else "",
        "ffprobe_path": config_path_text(settings_value["ffprobe_path"]) if settings_value["ffprobe_path"] else "",
    }
    path = settings_destination(SETTINGS_FILE_NAME, SETTINGS_PATH)
    atomic_write_json(path, saved)
    return settings_value
