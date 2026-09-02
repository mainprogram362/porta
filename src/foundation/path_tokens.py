"""Explicit portable path tokens accepted only in PORTA settings."""

from __future__ import annotations

from pathlib import Path


# ``@HOME`` reads naturally in a path (``@HOME/Downloads``) while avoiding
# the shell-variable syntax that users should not have to understand here.
HOME_TOKEN = "@HOME"
_LEGACY_HOME_TOKEN = "${HOME}"


def expand_setting_path(value: str) -> str:
    """Expand the documented ``@HOME`` token and legacy home spellings."""
    for token in (HOME_TOKEN, _LEGACY_HOME_TOKEN):
        if value == token:
            return str(Path.home())
        if value.startswith(f"{token}/"):
            return str(Path.home() / value.removeprefix(f"{token}/"))
    return str(Path(value).expanduser())


def home_tokenized(value: str) -> str:
    """Represent a built-in home default without recording the user name."""
    home = str(Path.home())
    if value == home:
        return HOME_TOKEN
    if value.startswith(f"{home}/"):
        return f"{HOME_TOKEN}/{value.removeprefix(f'{home}/')}"
    return value
