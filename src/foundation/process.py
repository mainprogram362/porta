"""Small, platform-neutral helpers for invoking external commands."""

from __future__ import annotations

import subprocess


def run_command(
    cmd: list[str],
    *,
    capture_output: bool = True,
    timeout: int | None = None,
    check: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Run an external command without invoking a shell.

    The helper itself is not Linux-specific. Features that require a particular
    desktop command must declare that requirement at their own boundary.
    """
    try:
        return subprocess.run(
            cmd,
            capture_output=capture_output,
            text=True,
            check=check,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError):
        raise RuntimeError("外部コマンドの実行に失敗しました。") from None
