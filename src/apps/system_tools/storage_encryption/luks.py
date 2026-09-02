"""Small, fail-closed planning helpers for opening and mounting LUKS data."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
import shutil
import stat
from typing import Callable

from foundation.path import absolute_path


_STANDARD_COMMAND_PATHS: dict[str, tuple[Path, ...]] = {
    # Graphical desktop launches commonly omit sbin directories from PATH,
    # even though these are normal locations for system administration tools.
    "cryptsetup": (Path("/usr/sbin/cryptsetup"), Path("/sbin/cryptsetup")),
    "mount": (Path("/usr/bin/mount"), Path("/bin/mount")),
    "findmnt": (Path("/usr/bin/findmnt"), Path("/bin/findmnt")),
    "pkexec": (Path("/usr/bin/pkexec"), Path("/bin/pkexec")),
}


@dataclass(frozen=True)
class LuksMountRequest:
    """A validated local request, without any passphrase or persistent state."""

    container_path: Path
    mount_point: Path
    mapping_name: str


@dataclass(frozen=True)
class RequestValidation:
    """The complete local preflight result shown before any privileged action."""

    request: LuksMountRequest | None
    messages: tuple[str, ...]

    @property
    def is_valid(self) -> bool:
        return self.request is not None


def suggested_mapping_name(container_path: Path) -> str:
    """Create a deterministic private mapper name without storing it anywhere."""
    normalized = absolute_path(container_path)
    stem = re.sub(r"[^A-Za-z0-9_.+-]+", "_", normalized.stem).strip("_.") or "container"
    digest = hashlib.sha256(os.fsencode(str(normalized))).hexdigest()[:10]
    return f"porta_luks_{stem[:34]}_{digest}"


def validate_mount_request(
    container_text: str,
    mount_point_text: str,
    *,
    which: Callable[[str], str | None] = shutil.which,
) -> RequestValidation:
    """Check only facts that can be established without asking for privilege.

    The final LUKS signature check and all state-changing work are repeated in
    the privileged helper immediately before opening the container.
    """
    messages: list[str] = []
    container = _entered_absolute_path(container_text, "暗号化領域")
    mount_point = _entered_absolute_path(mount_point_text, "マウント先")
    if container is None:
        messages.append("暗号化領域の絶対パスを入力してください。")
    elif not _is_regular_file_or_block_device(container):
        messages.append("暗号化領域は存在する通常ファイルまたはブロックデバイスにしてください。")
    else:
        messages.append(f"暗号化領域: 確認しました（{container}）")
    if mount_point is None:
        messages.append("マウント先の絶対パスを入力してください。")
    elif not mount_point.is_dir():
        messages.append("マウント先は存在するフォルダにしてください。")
    elif os.path.ismount(mount_point):
        messages.append("マウント先は既に別のファイルシステムで使用中です。")
    else:
        try:
            next(mount_point.iterdir())
        except StopIteration:
            messages.append(f"マウント先: 空のフォルダを確認しました（{mount_point}）")
        except OSError as exc:
            messages.append(f"マウント先の中身を確認できません: {exc}")
        else:
            messages.append("マウント先フォルダは空にしてください。既存の内容を隠さないため中止します。")
    missing = [
        name
        for name in ("pkexec", "cryptsetup", "mount", "findmnt")
        if find_command(name, which=which) is None
    ]
    if missing:
        messages.append("必要なコマンドが見つかりません: " + "、".join(missing))
    if container is None or mount_point is None or missing:
        return RequestValidation(None, tuple(messages))
    if not _is_regular_file_or_block_device(container) or not mount_point.is_dir() or os.path.ismount(mount_point):
        return RequestValidation(None, tuple(messages))
    try:
        next(mount_point.iterdir())
    except StopIteration:
        pass
    except OSError:
        return RequestValidation(None, tuple(messages))
    else:
        return RequestValidation(None, tuple(messages))
    request = LuksMountRequest(container, mount_point, suggested_mapping_name(container))
    messages.append(
        "通常チェックを通過しました。実行時に管理者権限でLUKS形式を再確認してから開きます。"
    )
    messages.append(f"今回だけ使う解除名: {request.mapping_name}")
    return RequestValidation(request, tuple(messages))


def privileged_mount_command(
    request: LuksMountRequest, *, passphrase_byte_count: int
) -> tuple[str, tuple[str, ...]]:
    """Build one fixed-script polkit request with positional data only.

    The script never interpolates user paths.  The LUKS secret itself goes only
    through standard input; its byte count is passed separately so cryptsetup
    does not mistake a terminal-style trailing newline for part of the secret.
    The script checks the source, the empty mount point, the LUKS signature,
    and mapper collision again after privilege escalation; if mounting fails it
    closes only the mapper opened in this run.
    """
    script = r'''
set -eu
source_path=$1
mapping_name=$2
mount_point=$3
passphrase_byte_count=$4

fail() {
    printf '%s\n' "$1" >&2
    exit 1
}

if [ ! -f "$source_path" ] && [ ! -b "$source_path" ]; then
    fail "暗号化領域が通常ファイルまたはブロックデバイスではありません。"
fi
if [ ! -d "$mount_point" ]; then
    fail "マウント先フォルダがありません。"
fi
if findmnt -rn -M "$mount_point" >/dev/null 2>&1; then
    fail "マウント先は既に使用中です。"
fi
if [ -n "$(find "$mount_point" -mindepth 1 -maxdepth 1 -print -quit 2>/dev/null)" ]; then
    fail "マウント先フォルダが空ではありません。"
fi
if [ -e "/dev/mapper/$mapping_name" ]; then
    fail "今回の解除名は既に使用中です。何もしません。"
fi
if ! cryptsetup isLuks "$source_path" >/dev/null 2>&1; then
    fail "指定先をLUKSとして確認できません。"
fi
if ! cryptsetup open --type luks --key-file - --keyfile-size "$passphrase_byte_count" "$source_path" "$mapping_name"; then
    fail "LUKSを開けませんでした。"
fi
if ! mount "/dev/mapper/$mapping_name" "$mount_point"; then
    printf '%s\n' "マウントに失敗したため、今回開いたLUKSを閉じます。" >&2
    cryptsetup close "$mapping_name" || printf '%s\n' "自動で閉じられませんでした。状態を確認してください。" >&2
    exit 1
fi
printf '%s\n' "LUKSを開き、マウントしました: $mount_point"
'''
    return "pkexec", (
        "/bin/sh",
        "-c",
        script,
        "porta-place-luks-mount",
        str(request.container_path),
        request.mapping_name,
        str(request.mount_point),
        str(passphrase_byte_count),
    )


def find_command(
    name: str, *, which: Callable[[str], str | None] = shutil.which
) -> str | None:
    """Find a required Linux command even when a GUI launch omits sbin PATHs."""
    found = which(name)
    if found:
        return found
    for candidate in _STANDARD_COMMAND_PATHS.get(name, ()):
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def _entered_absolute_path(value: str, label: str) -> Path | None:
    text = value.strip()
    if not text:
        return None
    candidate = Path(text).expanduser()
    if not candidate.is_absolute():
        return None
    if "\x00" in text:
        return None
    return absolute_path(candidate)


def _is_regular_file_or_block_device(path: Path) -> bool:
    try:
        status = path.stat()
    except OSError:
        return False
    return stat.S_ISREG(status.st_mode) or stat.S_ISBLK(status.st_mode)
