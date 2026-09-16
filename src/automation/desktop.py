"""Linux desktop actions used by personal automation workflows.

This module is deliberately Linux-first. Keep desktop-specific commands in
this module rather than spreading them through apps or GUI code.
"""

from __future__ import annotations

import shutil
import sys

from foundation.path import PathLike, normalize_path
from runtime.process import run_command


def _require_linux_desktop() -> None:
    """Fail clearly when a Linux desktop-only helper is used elsewhere.

    Platform extension point: add a Windows implementation in this function
    and the public functions below only when Windows support is needed.
    """
    if sys.platform != "linux":
        raise OSError("このデスクトップ操作は現在 Linux 専用です。")


def open_target(path: PathLike) -> bool:
    """Open a file or directory with its Linux default application.

    Platform extension point: this is the sole place that invokes ``xdg-open``.
    """
    target = normalize_path(path)
    if not target.exists():
        raise FileNotFoundError(f"対象が見つかりません: {target}")

    _require_linux_desktop()
    if not shutil.which("xdg-open"):
        raise EnvironmentError("Linux環境では xdg-open コマンドが必要です。")

    result = run_command(["xdg-open", str(target)], capture_output=False)
    return result.returncode == 0


def reveal_in_file_manager(path: PathLike) -> bool:
    """Open the directory containing a target in the Linux file manager."""
    target = normalize_path(path)
    if not target.exists():
        raise FileNotFoundError(f"対象が見つかりません: {target}")
    return open_target(target if target.is_dir() else target.parent)
