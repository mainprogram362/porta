"""Explicit location and structure for user-owned files outside the app."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from settings.persistent_settings import configured_user_root, configured_user_root_text, save_user_root_path


@dataclass(frozen=True)
class UserSpacePaths:
    """All standard locations derived from one explicitly selected root."""

    root: Path
    config: Path
    local_data: Path
    ai_models: Path
    ai_runners: Path
    dictionaries: Path
    templates: Path
    private: Path
    cache: Path

    def directories(self) -> tuple[Path, ...]:
        return (
            self.config,
            self.local_data,
            self.ai_models,
            self.ai_runners,
            self.dictionaries,
            self.templates,
            self.private,
            self.cache,
        )


def paths_for_root(root: Path) -> UserSpacePaths:
    """Derive the standard layout without assuming where the app is installed."""
    local_data = root / "local_data"
    return UserSpacePaths(
        root=root,
        config=root / "config",
        local_data=local_data,
        ai_models=local_data / "ai" / "models",
        ai_runners=local_data / "ai" / "runners",
        dictionaries=local_data / "dictionaries",
        templates=local_data / "templates",
        private=root / "private",
        cache=root / "cache",
    )


def configured_paths() -> UserSpacePaths | None:
    root = configured_user_root()
    return paths_for_root(root) if root is not None else None


def configured_root_text() -> str:
    """Return the user's absolute or relative path exactly as configured."""
    return configured_user_root_text()


def save_root_path(root_path: str) -> UserSpacePaths | None:
    root = save_user_root_path(root_path)
    return paths_for_root(root) if root is not None else None


def create_configured_directories() -> UserSpacePaths:
    paths = configured_paths()
    if paths is None:
        raise ValueError("先にユーザー領域の root_path を設定してください。")
    for directory in paths.directories():
        directory.mkdir(parents=True, exist_ok=True)
    return paths
