"""Explicit, user-owned locations for generic external program launchers."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from settings.persistent_settings import config_path_context, config_path_text, create_settings_directory, locate_settings_directory
from settings.path_tokens import resolve_setting_path


EXTERNAL_RELATIVE_FILE_PATH = Path("shared") / "external_program_locations.json"


@dataclass(frozen=True)
class LauncherLocations:
    state: str
    detail: str
    file_path: Path | None
    directories: tuple[Path, ...] = ()


def external_template_text() -> str:
    return json.dumps(
        {"locations": []},
        ensure_ascii=False,
        indent=2,
    ) + "\n"


def parse_text(text: str, *, base_directory: Path) -> tuple[Path, ...]:
    """Read the universal path syntax from the generic locations JSON."""
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("外部プログラム設定は JSON 形式にしてください。") from exc
    if not isinstance(raw, dict) or set(raw) != {"locations"}:
        raise ValueError("外部プログラム設定は locations だけを持つJSONオブジェクトにしてください。")
    values = raw["locations"]
    if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
        raise ValueError("locations はパス文字列の配列にしてください。")
    result: list[Path] = []
    seen: set[Path] = set()
    context = config_path_context(base_directory)
    for number, raw_value in enumerate(values, start=1):
        value = raw_value.strip()
        if not value:
            continue
        try:
            path = resolve_setting_path(value, context=context)
        except ValueError as exc:
            raise ValueError(f"locations の {number} 件目: {exc}") from exc
        if path not in seen:
            seen.add(path)
            result.append(path)
    return tuple(result)


def load_external_locations() -> LauncherLocations:
    config = locate_settings_directory()
    if not config.usable or config.directory is None:
        return LauncherLocations(config.state, config.detail, None)
    file_path = config.directory / EXTERNAL_RELATIVE_FILE_PATH
    try:
        text = file_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return LauncherLocations("missing_file", "外部プログラムの位置設定がありません。", file_path)
    except OSError as exc:
        return LauncherLocations("unreadable_file", f"外部プログラムの位置設定を読めません: {exc}", file_path)
    try:
        directories = parse_text(text, base_directory=file_path.parent)
    except ValueError as exc:
        return LauncherLocations("invalid_file", str(exc), file_path)
    return LauncherLocations("ready", f"外部プログラムの置き場を{len(directories)}件読み込みました。", file_path, directories)


def external_editable_text() -> str:
    loaded = load_external_locations()
    if loaded.file_path is None:
        return external_template_text()


def external_status() -> tuple[str, str]:
    loaded = load_external_locations()
    return loaded.state, loaded.detail


def validate_external_text(text: str) -> tuple[Path, ...]:
    """Validate with the same relative base that an explicit save will use."""
    config = locate_settings_directory()
    if config.directory is not None:
        base = (config.directory / EXTERNAL_RELATIVE_FILE_PATH).parent
    else:
        base = Path(__file__).resolve().parents[4]
    return parse_text(text, base_directory=base)
    try:
        return loaded.file_path.read_text(encoding="utf-8")
    except OSError:
        return external_template_text()


def save_external_text(text: str) -> Path:
    config = create_settings_directory()
    file_path = config / EXTERNAL_RELATIVE_FILE_PATH
    directories = parse_text(text, base_directory=file_path.parent)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = file_path.with_suffix(".json.tmp")
    saved = {"locations": [config_path_text(path, base_directory=file_path.parent) for path in directories]}
    temporary.write_text(json.dumps(saved, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(file_path)
    return file_path
