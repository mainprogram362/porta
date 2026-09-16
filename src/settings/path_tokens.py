"""One portable path grammar used by every persistent PORTA setting."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path


# ``@HOME`` reads naturally in a path (``@HOME/Downloads``) while avoiding
# the shell-variable syntax that users should not have to understand here.
HOME_TOKEN = "@HOME"
PORTA_TOKEN = "@PORTA"
USER_TOKEN = "@USER"
CONFIG_TOKEN = "@CONFIG"


@dataclass(frozen=True)
class PathSyntaxContext:
    """Known stable roots used to resolve a path written in a setting."""

    base_directory: Path
    project_root: Path
    user_root: Path | None = None
    config_directory: Path | None = None


def resolve_setting_path(value: str, *, context: PathSyntaxContext) -> Path:
    """Resolve the documented setting-path syntax to one absolute path.

    Accepted forms are ``@HOME``, ``@PORTA``, ``@USER``, ``@CONFIG``, an
    absolute path, and a path relative to the setting file itself. Shell
    expansion syntax is deliberately not a setting language.
    """
    # Settings may legitimately point to a name ending in a space.  JSON
    # already preserves the exact string, so do not reinterpret it here.
    text = value
    if not text:
        raise ValueError("パスは空欄にできません。")
    if "\x00" in text or text.startswith("~") or "$" in text:
        raise ValueError("~ や環境変数は使えません。@HOME などの共通記法を使ってください。")
    roots = {
        HOME_TOKEN: Path.home(),
        PORTA_TOKEN: context.project_root,
        USER_TOKEN: context.user_root,
        CONFIG_TOKEN: context.config_directory,
    }
    for token, root in roots.items():
        if text == token or text.startswith(f"{token}/"):
            if root is None:
                raise ValueError(f"{token} はこの設定ではまだ使えません。")
            suffix = text.removeprefix(token).lstrip("/")
            return Path(os.path.abspath(root / suffix))
    if text.startswith("@"):
        raise ValueError("未対応の位置記号です。@HOME、@PORTA、@USER、@CONFIGを使ってください。")
    path = Path(text)
    if not path.is_absolute():
        path = context.base_directory / path
    return Path(os.path.abspath(path))


def setting_path_text(value: str | Path, *, context: PathSyntaxContext) -> str:
    """Write one absolute path back with the most stable available token."""
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = context.base_directory / path
    target = Path(os.path.abspath(path))
    for token, root in (
        (CONFIG_TOKEN, context.config_directory),
        (USER_TOKEN, context.user_root),
        (PORTA_TOKEN, context.project_root),
        (HOME_TOKEN, Path.home()),
    ):
        if root is None:
            continue
        root_path = Path(os.path.abspath(root))
        try:
            relative = target.relative_to(root_path)
        except ValueError:
            continue
        return token if relative == Path(".") else f"{token}/{relative.as_posix()}"
    return str(target)


def expand_setting_path(value: str) -> str:
    """Compatibility helper for callers that only need the project and home roots."""
    root = Path(__file__).resolve().parents[2]
    return str(resolve_setting_path(value, context=PathSyntaxContext(root, root)))


def home_tokenized(value: str) -> str:
    """Compatibility formatter using the shared grammar's portable spelling."""
    root = Path(__file__).resolve().parents[2]
    return setting_path_text(value, context=PathSyntaxContext(root, root))
