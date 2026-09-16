"""Low-dependency, fail-closed discovery for PORTA user settings."""

from __future__ import annotations

import json
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from settings.path_tokens import PathSyntaxContext, resolve_setting_path, setting_path_text

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BOOTSTRAP_PATH = PROJECT_ROOT / "persistent_settings.json"
DEFAULT_USER_ROOT = "@PORTA/porta_user"


@dataclass(frozen=True)
class SettingsLocation:
    state: str
    detail: str
    directory: Path | None = None

    @property
    def usable(self) -> bool:
        return self.state == "ready" and self.directory is not None


@dataclass(frozen=True)
class ConfigResetResult:
    directory: Path
    backup_directory: Path | None
    created_files: tuple[Path, ...]


def bootstrap_template_text() -> str:
    return json.dumps({"user_root": "@PORTA/porta_user"}, ensure_ascii=False, indent=2) + "\n"


def create_bootstrap_template() -> Path:
    """Create the explicit default locator without replacing any existing one."""
    if BOOTSTRAP_PATH.exists() or BOOTSTRAP_PATH.is_symlink():
        raise FileExistsError(
            "入口ファイルは既にあります。既存の保存先を変える場合は、内容を明示的に保存してください。"
        )
    BOOTSTRAP_PATH.parent.mkdir(parents=True, exist_ok=True)
    BOOTSTRAP_PATH.write_text(bootstrap_template_text(), encoding="utf-8")
    return BOOTSTRAP_PATH


def bootstrap_editable_text() -> str:
    try:
        return BOOTSTRAP_PATH.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        return bootstrap_template_text()


def _bootstrap_context() -> PathSyntaxContext:
    return PathSyntaxContext(BOOTSTRAP_PATH.parent, PROJECT_ROOT)


def _location_value(text: str) -> str:
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("入口設定は JSON 形式にしてください。") from exc
    if not isinstance(raw, dict) or set(raw) != {"user_root"}:
        raise ValueError("入口設定は user_root だけを持つJSONオブジェクトにしてください。")
    value = raw["user_root"]
    if not isinstance(value, str):
        raise ValueError("user_root はパス文字列にしてください。")
    return value


def validate_bootstrap_text(text: str) -> str:
    """Validate locator text without reading or writing the real locator."""
    value = _location_value(text)
    resolve_setting_path(value, context=_bootstrap_context())
    return value


def save_bootstrap_text(text: str) -> Path:
    """Validate and atomically save the JSON user-root locator."""
    value = _location_value(text)
    resolved = resolve_setting_path(value, context=_bootstrap_context())
    BOOTSTRAP_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = BOOTSTRAP_PATH.with_suffix(".json.tmp")
    saved = {"user_root": setting_path_text(resolved, context=_bootstrap_context())}
    temporary.write_text(json.dumps(saved, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(BOOTSTRAP_PATH)
    return BOOTSTRAP_PATH


def config_path_context(base_directory: Path | None = None) -> PathSyntaxContext:
    """Build the standard context used by every app-owned setting file."""
    user_root = configured_user_root()
    config_directory = user_root / "config" if user_root is not None else None
    return PathSyntaxContext(
        base_directory or config_directory or BOOTSTRAP_PATH.parent,
        PROJECT_ROOT,
        user_root,
        config_directory,
    )


def resolve_config_path(value: str, *, base_directory: Path | None = None) -> str:
    return str(resolve_setting_path(value, context=config_path_context(base_directory)))


def config_path_text(value: str | Path, *, base_directory: Path | None = None) -> str:
    return setting_path_text(value, context=config_path_context(base_directory))


def _read_user_root() -> tuple[Path, str]:
    try:
        text = BOOTSTRAP_PATH.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ValueError("設定保存先の入口ファイルがありません。") from exc
    except OSError as exc:
        raise ValueError("設定保存先の入口ファイルを読めません。") from exc
    value = _location_value(text)
    return resolve_setting_path(value, context=_bootstrap_context()), value


def configured_user_root() -> Path | None:
    try:
        root, _text = _read_user_root()
    except ValueError:
        return None
    return root


def configured_user_root_text() -> str:
    try:
        _root, text = _read_user_root()
    except ValueError:
        return ""
    return text


def save_user_root_path(value: str) -> Path:
    """Save one common-grammar path inside the JSON locator."""
    text = value
    save_bootstrap_text(json.dumps({"user_root": text}, ensure_ascii=False))
    return resolve_setting_path(text, context=_bootstrap_context())


def locate_settings_directory() -> SettingsLocation:
    try:
        user_root, _text = _read_user_root()
    except ValueError as exc:
        state = "missing_bootstrap" if not BOOTSTRAP_PATH.exists() else "invalid_bootstrap"
        return SettingsLocation(state, str(exc))
    directory = user_root / "config"
    if not directory.exists():
        return SettingsLocation("missing_directory", "設定保存先フォルダがありません。", directory)
    if not directory.is_dir():
        return SettingsLocation("not_directory", "設定保存先がフォルダではありません。", directory)
    return SettingsLocation("ready", "設定保存先フォルダを確認しました。", directory)


def settings_file_status(filename: str) -> SettingsLocation:
    location = locate_settings_directory()
    if not location.usable:
        return location
    assert location.directory is not None
    path = location.directory / filename
    if not path.exists():
        return SettingsLocation("missing_file", "このアプリの設定ファイルがありません。", path)
    if not path.is_file():
        return SettingsLocation("not_file", "このアプリの設定先がファイルではありません。", path)
    return SettingsLocation("ready", "設定ファイルを確認しました。", path)


def create_settings_directory() -> Path:
    try:
        root, _text = _read_user_root()
    except ValueError as exc:
        raise ValueError("先に有効なユーザー領域を入口ファイルへ設定してください。") from exc
    directory = root / "config"
    directory.mkdir(parents=True, exist_ok=True)
    if not directory.is_dir():
        raise ValueError("設定保存先フォルダを作成できません。")
    return directory


def create_settings_file(filename: str, template: str) -> Path:
    if not _safe_relative_setting_path(filename):
        raise ValueError("設定ファイル名が安全ではありません。")
    directory = create_settings_directory()
    path = directory / filename
    if path.exists():
        raise ValueError("設定ファイルは既にあります。雛形で上書きする場合は編集画面から保存してください。")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(template, encoding="utf-8")
    return path


def reset_config(files: dict[str, str]) -> ConfigResetResult:
    """Replace CONFIG with templates while retaining the previous directory."""
    location = locate_settings_directory()
    if location.state not in {"ready", "missing_directory"} or location.directory is None:
        raise ValueError("先に有効なCONFIG保存先を入口ファイルへ設定してください。")
    directory = location.directory
    if directory.is_symlink():
        raise ValueError("CONFIG保存先がシンボリックリンクのため、初期化しません。")
    if directory.exists() and not directory.is_dir():
        raise ValueError("CONFIG保存先がフォルダではありません。")
    if not files:
        raise ValueError("作成する設定雛形がありません。")
    for filename, text in files.items():
        if not _safe_relative_setting_path(filename):
            raise ValueError("設定雛形のファイル名が安全ではありません。")
        if not isinstance(text, str):
            raise ValueError("設定雛形の内容は文字列にしてください。")

    directory.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{directory.name}_reset_", dir=directory.parent))
    backup: Path | None = None
    try:
        created = []
        for filename, text in files.items():
            path = staging / filename
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
            created.append(directory / filename)

        if directory.exists():
            backup = _unique_config_backup_path(directory)
            directory.replace(backup)
        try:
            staging.replace(directory)
        except OSError:
            if backup is not None and backup.exists() and not directory.exists():
                backup.replace(directory)
            raise
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        raise
    return ConfigResetResult(directory, backup, tuple(created))


def _safe_relative_setting_path(value: str) -> bool:
    path = Path(value)
    return bool(value) and not path.is_absolute() and all(part not in {"", ".", ".."} for part in path.parts)


def _unique_config_backup_path(directory: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = directory.with_name(f"{directory.name}_backup_{stamp}")
    candidate = base
    number = 2
    while candidate.exists():
        candidate = directory.with_name(f"{base.name}_{number}")
        number += 1
    return candidate
