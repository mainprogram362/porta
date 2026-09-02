"""Locations for standalone apps, separate from integrated runtime backends."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .persistent_settings import PROJECT_ROOT, create_settings_directory, locate_settings_directory

STANDALONE_APPS_RELATIVE_FILE_PATH = Path("shared") / "standalone_apps_location.txt"
EXTERNAL_RELATIVE_FILE_PATH = Path("shared") / "external_program_locations.txt"
STANDALONE_APPS_HEADER = "# PORTA_STANDALONE_APPS_LOCATION_V1"
EXTERNAL_HEADER = "# PORTA_EXTERNAL_PROGRAM_LOCATIONS_V1"
DEFAULT_STANDALONE_APPS_DIRECTORY = PROJECT_ROOT / "standalone_apps"


@dataclass(frozen=True)
class LauncherLocations:
    state: str
    detail: str
    file_path: Path | None
    directories: tuple[Path, ...] = ()


def standalone_apps_template_text() -> str:
    return (
        f"{STANDALONE_APPS_HEADER}\n"
        "# PORTAから切り離して単体起動できる、低依存ツールの置き場です。\n"
        "# この場所がなくてもPORTAとCoreは壊れず、独立ツールだけが利用できません。\n"
        "@PORTA/standalone_apps\n"
    )


def external_template_text() -> str:
    return (
        f"{EXTERNAL_HEADER}\n"
        "# 外部プログラム置き場を1行に1つ書きます。空欄なら何も探しません。\n"
        "# 絶対パス、またはこのTXTを基準にした相対パスを使用できます。\n"
    )


def parse_text(text: str, *, base_directory: Path) -> tuple[Path, ...]:
    result: list[Path] = []
    seen: set[Path] = set()
    for number, raw_line in enumerate(text.splitlines(), start=1):
        value = raw_line.strip()
        if not value or value.startswith("#"):
            continue
        if "\x00" in value or value.startswith("~") or "$" in value:
            raise ValueError(
                f"{number}行目は使用できません。~・環境変数・制御文字を使わずパスだけを書いてください。"
            )
        if value == "@PORTA" or value.startswith("@PORTA/"):
            suffix = value.removeprefix("@PORTA").lstrip("/")
            path = PROJECT_ROOT / suffix
        else:
            if value.startswith("@"):
                raise ValueError(f"{number}行目の位置記号は使用できません。")
            path = Path(value)
            if not path.is_absolute():
                path = base_directory / path
        path = Path(os.path.abspath(path))
        if path in seen:
            continue
        seen.add(path)
        result.append(path)
    return tuple(result)


def _load(relative_path: Path, label: str, *, exactly_one: bool) -> LauncherLocations:
    config = locate_settings_directory()
    if not config.usable or config.directory is None:
        return LauncherLocations(config.state, config.detail, None)
    file_path = config.directory / relative_path
    try:
        text = file_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return LauncherLocations("missing_file", f"{label}の位置設定がありません。", file_path)
    except OSError as exc:
        return LauncherLocations("unreadable_file", f"{label}の位置設定を読めません: {exc}", file_path)
    try:
        directories = parse_text(text, base_directory=file_path.parent)
    except ValueError as exc:
        return LauncherLocations("invalid_file", str(exc), file_path)
    if exactly_one and len(directories) != 1:
        return LauncherLocations(
            "invalid_file",
            "独立ツールの位置はコメントと空行を除いて1行だけ書いてください。",
            file_path,
        )
    return LauncherLocations(
        "ready",
        f"{label}の置き場を{len(directories)}件読み込みました。",
        file_path,
        directories,
    )


def load_standalone_apps_location() -> LauncherLocations:
    return _load(STANDALONE_APPS_RELATIVE_FILE_PATH, "独立ツール", exactly_one=True)


def load_external_locations() -> LauncherLocations:
    return _load(EXTERNAL_RELATIVE_FILE_PATH, "外部プログラム", exactly_one=False)


def _editable(template: str, loader: LauncherLocations) -> str:
    if loader.file_path is None:
        return template
    try:
        return loader.file_path.read_text(encoding="utf-8")
    except OSError:
        return template


def standalone_apps_editable_text() -> str:
    return _editable(standalone_apps_template_text(), load_standalone_apps_location())


def external_editable_text() -> str:
    return _editable(external_template_text(), load_external_locations())


def _save(relative_path: Path, text: str, *, exactly_one: bool) -> Path:
    config = create_settings_directory()
    file_path = config / relative_path
    directories = parse_text(text, base_directory=file_path.parent)
    if exactly_one and len(directories) != 1:
        raise ValueError("独立ツールの位置はコメントと空行を除いて1行だけ書いてください。")
    file_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = file_path.with_suffix(".txt.tmp")
    temporary.write_text(text.rstrip("\n") + "\n", encoding="utf-8")
    temporary.replace(file_path)
    return file_path


def save_standalone_apps_text(text: str) -> Path:
    return _save(STANDALONE_APPS_RELATIVE_FILE_PATH, text, exactly_one=True)


def save_external_text(text: str) -> Path:
    return _save(EXTERNAL_RELATIVE_FILE_PATH, text, exactly_one=False)
