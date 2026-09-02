#!/usr/bin/env python3
"""Small Nautilus Scripts bridge for PORTA's external-open receiver."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parents[1]
START = ROOT / "start.sh"
TARGET_BY_SCRIPT_NAME = {
    "PORTAへ送る": "choose",
    "PORTA-ファイルマネージャーで開く": "file-manager",
    "PORTA-メディア整理で開く": "media-organizer",
    "PORTA-動画エンコードで開く": "video-encoder",
}
_EARLY_EXIT_WAIT_SECONDS = 0.8


def selected_uris(environment: dict[str, str] | None = None) -> tuple[str, ...]:
    """Read Nautilus' newline-delimited URI selection without shell splitting."""
    values = os.environ if environment is None else environment
    return tuple(value for value in values.get("NAUTILUS_SCRIPT_SELECTED_URIS", "").splitlines() if value)


def target_from_invocation(script_name: str, explicit_target: str | None) -> str:
    """Use the common chooser unless an old named shortcut requests a route.

    Some Nautilus/GIO versions resolve a symbolic link before launching a
    script.  In that case ``sys.argv[0]`` is ``nautilus_handoff.py`` rather
    than the visible ``PORTAへ送る`` link name.  The current integration has
    only one action, so it must not depend on that implementation detail.
    """
    if explicit_target is not None:
        return explicit_target
    return TARGET_BY_SCRIPT_NAME.get(script_name, "choose")


def notify_launch_failure(message: str) -> None:
    """Show an actionable desktop message when PORTA exits before opening."""
    try:
        subprocess.run(
            ["notify-send", "PORTAを起動できません", message],
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        # Nautilus can still show stderr when it was invoked from a terminal.
        print(f"PORTAを起動できません: {message}", file=sys.stderr)


def notify_handoff_received(uri_count: int) -> None:
    """Give immediate feedback that Nautilus has run this bridge."""
    try:
        subprocess.run(
            ["notify-send", "PORTA", f"{uri_count}件を受け取り、PORTAを開いています。"],
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="NautilusからPORTAへ選択パスを渡します。")
    parser.add_argument(
        "target",
        nargs="?",
        choices=("choose", "file-manager", "media-organizer", "video-encoder"),
    )
    arguments = parser.parse_args(argv)
    try:
        target = target_from_invocation(Path(sys.argv[0]).name, arguments.target)
    except ValueError as exc:
        print(f"PORTA: {exc}", file=sys.stderr)
        return 2

    uris = selected_uris()
    notify_handoff_received(len(uris))
    # Let PORTA itself report an empty selection or invalid URI in its normal,
    # copyable validation dialog.  Detaching keeps Nautilus responsive while
    # the independent PORTA window remains open.
    try:
        process = subprocess.Popen(
            [str(START), "--external-open", target, "--", *uris],
            cwd=ROOT,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
    except OSError as exc:
        notify_launch_failure(str(exc))
        return 1
    # A running GUI process is the expected success case.  Only consume output
    # when it dies immediately, so bootstrapping errors are not invisible.
    deadline = time.monotonic() + _EARLY_EXIT_WAIT_SECONDS
    while process.poll() is None and time.monotonic() < deadline:
        time.sleep(0.05)
    if process.poll() is not None:
        _stdout, stderr = process.communicate()
        detail = stderr.strip() or "PORTAが画面を開く前に終了しました。"
        notify_launch_failure(detail)
        return process.returncode or 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
