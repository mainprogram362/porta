"""Single registry for every user-editable PORTA configuration template."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
import shutil
import tempfile

from settings import persistent_settings
from settings.user_space import UserSpacePaths, paths_for_root

from apps.file_tools.file_manager import persistent_settings as file_manager_settings
from apps.media_tools.media_information import settings as media_information_settings
from apps.media_tools.text_thread_viewer import settings as text_thread_viewer_settings
from apps.media_tools.video_encoder import settings as video_encoder_settings
from apps.media_tools.youtube_downloader import settings as youtube_downloader_settings
from apps.system_tools.local_ai import settings as local_ai_settings
from apps.system_tools.storage_encryption import settings as storage_encryption_settings
from apps.system_tools.external_app_launcher import locations as external_locations


TemplateValidator = Callable[[str, Path], object]


@dataclass(frozen=True)
class ConfigurationTemplate:
    """One code-owned template and the same parser used by its consumer."""

    key: str
    title: str
    relative_path: Path | None
    template_text: Callable[[], str]
    editable_text: Callable[[], str]
    validate: TemplateValidator

    @property
    def is_bootstrap(self) -> bool:
        return self.relative_path is None


@dataclass(frozen=True)
class UserSpaceTemplateResult:
    """A newly created user-space skeleton, without any user data."""

    root: Path
    directories: tuple[Path, ...]
    config_files: tuple[Path, ...]


def _plain_validator(validate: Callable[[str], object]) -> TemplateValidator:
    return lambda text, _base: validate(text)


def _validate_storage(text: str, base: Path) -> object:
    return storage_encryption_settings.validate_text(text, base_directory=base)


def all_configuration_templates() -> tuple[ConfigurationTemplate, ...]:
    """Return every template in display/reset order, including the root locator."""
    return (
        ConfigurationTemplate(
            "bootstrap",
            "ユーザー領域の入口",
            None,
            persistent_settings.bootstrap_template_text,
            persistent_settings.bootstrap_editable_text,
            _plain_validator(persistent_settings.validate_bootstrap_text),
        ),
        ConfigurationTemplate(
            "file_manager",
            "ファイルマネージャー",
            Path(file_manager_settings.SETTINGS_FILE_NAME),
            file_manager_settings.template_text,
            file_manager_settings.editable_text,
            _plain_validator(file_manager_settings.validate_text),
        ),
        ConfigurationTemplate(
            "video_encoder",
            "動画エンコード・圧縮",
            Path(video_encoder_settings.SETTINGS_FILE_NAME),
            video_encoder_settings.template_text,
            video_encoder_settings.editable_text,
            _plain_validator(video_encoder_settings.validate_text),
        ),
        ConfigurationTemplate(
            "youtube_downloader",
            "YouTube ダウンローダー",
            Path(youtube_downloader_settings.SETTINGS_FILE_NAME),
            youtube_downloader_settings.template_text,
            youtube_downloader_settings.editable_text,
            _plain_validator(youtube_downloader_settings.validate_text),
        ),
        ConfigurationTemplate(
            "media_information",
            "メディア情報ワークスペース",
            Path(media_information_settings.SETTINGS_FILE_NAME),
            media_information_settings.template_text,
            media_information_settings.editable_text,
            _plain_validator(media_information_settings.validate_text),
        ),
        ConfigurationTemplate(
            "text_thread_viewer",
            "テキストスレッドビューア",
            Path(text_thread_viewer_settings.SETTINGS_FILE_NAME),
            text_thread_viewer_settings.template_text,
            text_thread_viewer_settings.editable_text,
            _plain_validator(text_thread_viewer_settings.validate_text),
        ),
        ConfigurationTemplate(
            "storage_encryption",
            "ストレージ・暗号化",
            Path(storage_encryption_settings.SETTINGS_FILE_NAME),
            storage_encryption_settings.template_text,
            storage_encryption_settings.editable_text,
            _validate_storage,
        ),
        ConfigurationTemplate(
            "local_ai",
            "ローカルAI",
            Path(local_ai_settings.SETTINGS_FILE_NAME),
            local_ai_settings.template_text,
            local_ai_settings.editable_text,
            _plain_validator(local_ai_settings.validate_text),
        ),
        ConfigurationTemplate(
            "external_program_locations",
            "外部プログラムの位置",
            external_locations.EXTERNAL_RELATIVE_FILE_PATH,
            external_locations.external_template_text,
            external_locations.external_editable_text,
            lambda text, base: external_locations.parse_text(text, base_directory=base),
        ),
    )


def config_reset_templates() -> dict[str, str]:
    """Return all CONFIG templates; the fixed root locator is intentionally excluded."""
    return {
        str(template.relative_path): template.template_text()
        for template in all_configuration_templates()
        if template.relative_path is not None
    }


def create_user_space_template(root: Path) -> UserSpaceTemplateResult:
    """Create a new complete user-space skeleton without changing its locator.

    The target must not exist.  A staging directory in its parent prevents a
    partially generated user space from appearing at the requested name.
    """
    target = root.expanduser()
    if not target.is_absolute() or target.name in {"", ".", ".."}:
        raise ValueError("生成先は名前を持つ絶対パスにしてください。")
    if target.exists() or target.is_symlink():
        raise FileExistsError(f"生成先は既に存在します: {target}")
    parent = target.parent
    parent.mkdir(parents=True, exist_ok=True)
    if not parent.is_dir():
        raise ValueError(f"生成先の親フォルダを作成できません: {parent}")

    staging = Path(tempfile.mkdtemp(prefix=f".{target.name}_new_", dir=parent))
    try:
        staged_paths: UserSpacePaths = paths_for_root(staging)
        for directory in staged_paths.directories():
            directory.mkdir(parents=True, exist_ok=True)
        config_files: list[Path] = []
        for relative_path, text in config_reset_templates().items():
            relative = Path(relative_path)
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError(f"設定雛形のパスが安全ではありません: {relative_path}")
            destination = staging / "config" / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(text, encoding="utf-8")
            config_files.append(target / "config" / relative)
        if target.exists() or target.is_symlink():
            raise FileExistsError(f"生成先は既に存在します: {target}")
        staging.rename(target)
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        raise

    paths = paths_for_root(target)
    return UserSpaceTemplateResult(target, paths.directories(), tuple(config_files))


def create_single_configuration_template(
    template: ConfigurationTemplate, destination: Path
) -> Path:
    """Create one selected configuration template without replacing a file."""
    if template.relative_path is None:
        raise ValueError("ユーザー領域の入口設定は、この操作では生成できません。")
    target = destination.expanduser()
    if not target.is_absolute() or target.name in {"", ".", ".."}:
        raise ValueError("作成先はファイル名を含む絶対パスにしてください。")
    if target.exists() or target.is_symlink():
        raise FileExistsError(f"作成先は既に存在します: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.parent.is_dir():
        raise ValueError(f"作成先フォルダを作成できません: {target.parent}")
    target.write_text(template.template_text(), encoding="utf-8")
    return target
