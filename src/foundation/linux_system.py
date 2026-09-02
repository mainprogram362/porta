"""Small, explicit Linux desktop actions with no saved state.

This module only describes commands.  The GUI asks for confirmation and starts
them; tests can inspect the commands without ever changing the host system.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import os
import shutil
import subprocess


@dataclass(frozen=True)
class SystemAction:
    """One deliberate OS request and its user-facing explanation."""

    key: str
    title: str
    description: str
    confirmation: str
    program: str
    arguments: tuple[str, ...]


@dataclass(frozen=True)
class ActionAvailability:
    """Whether an action can be requested without guessing about the system."""

    action: SystemAction | None
    reason: str = ""

    @property
    def available(self) -> bool:
        return self.action is not None


_ACTION_TEXT = {
    "lock": ("画面をロック", "現在のセッションをロックします。作業は終了しません。", "画面をロックしますか？"),
    "suspend": ("スリープ", "メモリを保持したまま待機状態へ移ります。", "スリープしますか？ 保存していない作業を確認してください。"),
    "hibernate": ("ハイバネート", "メモリ内容を保存して電源を落とします。", "ハイバネートしますか？ 保存していない作業を確認してください。"),
    "logout": ("ログアウト", "現在のデスクトップセッションを終了します。", "ログアウトしますか？ 保存していない作業を確認してください。"),
    "reboot": ("再起動", "OSを再起動します。", "再起動しますか？ 保存していない作業を確認してください。"),
    "poweroff": ("シャットダウン", "OSを終了して電源を切ります。", "シャットダウンしますか？ 保存していない作業を確認してください。"),
}


def action_availability(
    key: str,
    *,
    environment: Mapping[str, str] | None = None,
    which: Callable[[str], str | None] = shutil.which,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> ActionAvailability:
    """Build an action only when the needed Linux interface is available.

    A disabled action is preferable to a guessed command: desktop environments
    and init systems vary, while ``systemctl``/``loginctl`` are the usual
    systemd-based Linux interfaces.
    """
    if key not in _ACTION_TEXT:
        return ActionAvailability(None, "未対応のシステム操作です。")
    env = os.environ if environment is None else environment
    title, description, confirmation = _ACTION_TEXT[key]

    if key in {"lock", "logout"}:
        session_id = env.get("XDG_SESSION_ID", "").strip()
        if session_id and which("loginctl"):
            verb = "lock-session" if key == "lock" else "terminate-session"
            return ActionAvailability(
                SystemAction(key, title, description, confirmation, "loginctl", (verb, session_id))
            )
        if key == "lock" and which("xdg-screensaver"):
            return ActionAvailability(
                SystemAction(key, title, description, confirmation, "xdg-screensaver", ("lock",))
            )
        return ActionAvailability(
            None,
            "現在のデスクトップセッションを安全に特定できないため、何もしません。",
        )

    if not which("systemctl"):
        return ActionAvailability(None, "この環境では systemctl を確認できないため、何もしません。")

    if key in {"suspend", "hibernate"}:
        capability = f"can-{key}"
        try:
            result = run(
                ["systemctl", capability],
                capture_output=True,
                text=True,
                check=False,
                timeout=3,
            )
        except (OSError, subprocess.SubprocessError):
            return ActionAvailability(None, f"{capability} を確認できないため、何もしません。")
        if result.returncode != 0 or result.stdout.strip() != "yes":
            return ActionAvailability(None, f"この環境は {title} に対応していません。")

    return ActionAvailability(
        SystemAction(key, title, description, confirmation, "systemctl", (key,))
    )


def system_status_lines(
    *,
    which: Callable[[str], str | None] = shutil.which,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> tuple[str, ...]:
    """Return short read-only status lines without recording them anywhere."""
    lines = ["OS操作: Linux標準の systemd / xdg 接口を確認します。"]
    if not which("systemctl"):
        return tuple(lines + ["systemctl: 見つかりません。電源操作は表示しません。"])
    try:
        result = run(
            ["systemctl", "is-system-running"],
            capture_output=True,
            text=True,
            check=False,
            timeout=3,
        )
        state = result.stdout.strip() or "確認できません"
    except (OSError, subprocess.SubprocessError):
        state = "確認できません"
    lines.append(f"systemd: {state}")
    return tuple(lines)
