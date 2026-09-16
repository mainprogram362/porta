"""Fail-closed VeraCrypt CLI discovery, validation, and command planning.

Secrets are deliberately absent from every dataclass and command argument.
The caller supplies a passphrase to the child process through standard input
only after showing the final execution confirmation.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import stat
import subprocess
from typing import Callable

from foundation.path import absolute_path


_KNOWN_EXECUTABLES = (
    Path(__file__).resolve().parents[4] / "integrated_backends" / "veracrypt" / "VeraCrypt.AppImage",
    Path("/usr/bin/veracrypt"),
    Path("/usr/local/bin/veracrypt"),
    Path("/opt/veracrypt/veracrypt"),
)
_REQUIRED_BASE_OPTIONS = ("--text", "--non-interactive", "--stdin")
_OPERATION_OPTIONS = {
    "mount": ("--mount",),
    "create": ("--create",),
    "list": ("--list",),
    # New releases call this operation "unmount"; older supported releases
    # expose the same operation as "dismount".
    "dismount": ("--unmount", "--dismount"),
}


@dataclass(frozen=True)
class VeraCryptCapability:
    executable: Path | None
    version: str
    operations: frozenset[str]
    detail: str
    unmount_option: str = "--dismount"

    @property
    def available(self) -> bool:
        return self.executable is not None and bool(self.operations)

    def supports(self, operation: str) -> bool:
        return operation in self.operations


@dataclass(frozen=True)
class VeraMountRequest:
    volume: Path
    mount_point: Path
    read_only: bool


@dataclass(frozen=True)
class VeraCreateRequest:
    output: Path
    size_mib: int
    filesystem: str


@dataclass(frozen=True)
class VeraDismountRequest:
    target: Path


@dataclass(frozen=True)
class RequestValidation:
    request: object | None
    messages: tuple[str, ...]

    @property
    def is_valid(self) -> bool:
        return self.request is not None


def find_executable(*, which: Callable[[str], str | None] = shutil.which) -> Path | None:
    """Return one absolute, executable regular file without guessing a bundle."""
    candidates = [*_KNOWN_EXECUTABLES]
    found = which("veracrypt")
    if found:
        candidates.append(Path(found))
    for candidate in candidates:
        try:
            resolved = candidate.resolve(strict=True)
            info = resolved.stat()
        except OSError:
            continue
        if stat.S_ISREG(info.st_mode) and os.access(resolved, os.X_OK):
            return resolved
    return None


def probe_backend(
    *,
    executable: Path | None = None,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> VeraCryptCapability:
    """Verify the installed CLI and discover each usable operation.

    Merely finding a binary is insufficient.  A build without non-interactive
    stdin support would force a secret into argv or an uncontrolled prompt, so
    PORTA refuses to use it.
    """
    program = executable or find_executable()
    if program is None:
        return VeraCryptCapability(
            None,
            "",
            frozenset(),
            "VeraCrypt CLIが見つかりません。VeraCryptを導入した環境で再確認してください。",
        )
    environment = dict(os.environ)
    environment.update({"LC_ALL": "C", "LANG": "C"})
    try:
        help_result = runner(
            [str(program), "--text", "--help"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=4,
            check=False,
            env=environment,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return VeraCryptCapability(program, "", frozenset(), f"VeraCrypt CLIを確認できません: {exc}")
    help_text = help_result.stdout or ""
    missing_base = [option for option in _REQUIRED_BASE_OPTIONS if option not in help_text]
    if help_result.returncode != 0 or missing_base:
        detail = (
            "安全な非対話CLIに必要な機能がありません: " + "、".join(missing_base)
            if missing_base
            else f"VeraCryptのヘルプ取得に失敗しました（終了コード {help_result.returncode}）。"
        )
        return VeraCryptCapability(program, "", frozenset(), detail)
    operations = frozenset(
        operation
        for operation, options in _OPERATION_OPTIONS.items()
        if any(option in help_text for option in options)
    )
    if not operations:
        return VeraCryptCapability(program, "", operations, "対応するVeraCrypt操作を確認できません。")
    version = "版情報なし"
    try:
        version_result = runner(
            [str(program), "--text", "--version"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=4,
            check=False,
            env=environment,
        )
        first_line = (version_result.stdout or "").strip().splitlines()
        if version_result.returncode == 0 and first_line:
            version = first_line[0][:200]
    except (OSError, subprocess.SubprocessError):
        pass
    operation_names = {"mount": "マウント", "create": "作成", "list": "一覧", "dismount": "解除"}
    unmount_option = "--unmount" if "--unmount" in help_text else "--dismount"
    return VeraCryptCapability(
        program,
        version,
        operations,
        "利用可能: " + "、".join(operation_names[item] for item in _OPERATION_OPTIONS if item in operations),
        unmount_option,
    )


def validate_mount_request(volume_text: str, mount_text: str, *, read_only: bool) -> RequestValidation:
    messages: list[str] = []
    volume = _absolute_entered_path(volume_text)
    mount_point = _absolute_entered_path(mount_text)
    if volume is None:
        messages.append("既存コンテナまたはデバイスの絶対パスを入力してください。")
    elif not _is_file_or_block_device(volume):
        messages.append("既存コンテナは通常ファイルまたはブロックデバイスにしてください。")
    else:
        messages.append(f"コンテナ: 確認しました（{volume}）")
    if mount_point is None:
        messages.append("マウント先の絶対パスを入力してください。")
    elif not mount_point.is_dir():
        messages.append("マウント先は存在するフォルダにしてください。")
    elif os.path.ismount(mount_point):
        messages.append("マウント先は既に使用中です。")
    else:
        try:
            next(mount_point.iterdir())
        except StopIteration:
            messages.append(f"マウント先: 空のフォルダを確認しました（{mount_point}）")
        except OSError as exc:
            messages.append(f"マウント先を確認できません: {exc}")
        else:
            messages.append("既存内容を隠さないため、マウント先は空にしてください。")
    if volume is None or mount_point is None or not _is_file_or_block_device(volume):
        return RequestValidation(None, tuple(messages))
    if not mount_point.is_dir() or os.path.ismount(mount_point):
        return RequestValidation(None, tuple(messages))
    try:
        next(mount_point.iterdir())
    except StopIteration:
        return RequestValidation(VeraMountRequest(volume, mount_point, read_only), tuple(messages))
    except OSError:
        pass
    return RequestValidation(None, tuple(messages))


def validate_create_request(output_text: str, size_mib: int, filesystem: str) -> RequestValidation:
    messages: list[str] = []
    output = _absolute_entered_path(output_text)
    valid_output = False
    if output is None:
        messages.append("新しいコンテナの絶対パスを入力してください。")
    elif output.exists() or output.is_symlink():
        messages.append("出力先が既に存在します。VeraCrypt作成では置き換えません。")
    elif not output.parent.is_dir():
        messages.append("出力先の親フォルダがありません。")
    elif not os.access(output.parent, os.W_OK):
        messages.append("出力先の親フォルダへ書き込めません。")
    else:
        try:
            free = shutil.disk_usage(output.parent).free
        except OSError as exc:
            messages.append(f"出力先の空き容量を確認できません: {exc}")
        else:
            required = size_mib * 1024 * 1024
            if free < required:
                messages.append("出力先の空き容量がコンテナ容量より少ないため作成できません。")
            else:
                valid_output = True
                messages.append(f"新規コンテナ: 未使用の出力先を確認しました（{output}）")
    if not 10 <= size_mib <= 16 * 1024 * 1024:
        messages.append("容量は10 MiB以上、16 TiB以下にしてください。")
    if filesystem not in {"ext4", "exFAT", "FAT"}:
        messages.append("未対応の内部ファイルシステムです。")
    request = VeraCreateRequest(output, size_mib, filesystem) if valid_output and 10 <= size_mib <= 16 * 1024 * 1024 and filesystem in {"ext4", "exFAT", "FAT"} else None
    return RequestValidation(request, tuple(messages))


def validate_dismount_request(target_text: str) -> RequestValidation:
    target = _absolute_entered_path(target_text)
    if target is None:
        return RequestValidation(None, ("解除するコンテナまたはマウント先の絶対パスを入力してください。",))
    if not target.exists():
        return RequestValidation(None, ("解除対象が存在しません。状態一覧を更新して確認してください。",))
    return RequestValidation(VeraDismountRequest(target), (f"解除対象: 確認しました（{target}）",))


def mount_command(capability: VeraCryptCapability, request: VeraMountRequest) -> tuple[str, tuple[str, ...]]:
    program = _program_for(capability, "mount")
    arguments = [
        "--text", "--non-interactive", "--stdin", "--mount",
        str(request.volume), str(request.mount_point),
        "--pim=0", "--keyfiles=", "--protect-hidden=no",
    ]
    if request.read_only:
        arguments.append("--mount-options=ro")
    return program, tuple(arguments)


def create_command(capability: VeraCryptCapability, request: VeraCreateRequest) -> tuple[str, tuple[str, ...]]:
    program = _program_for(capability, "create")
    return program, (
        "--text", "--non-interactive", "--stdin", "--create", str(request.output),
        "--volume-type=normal", f"--size={request.size_mib}M", "--encryption=AES",
        "--hash=SHA-512", f"--filesystem={request.filesystem}", "--pim=0", "--keyfiles=",
        "--random-source=/dev/urandom",
    )


def list_command(capability: VeraCryptCapability) -> tuple[str, tuple[str, ...]]:
    return _program_for(capability, "list"), ("--text", "--non-interactive", "--list")


def dismount_command(capability: VeraCryptCapability, request: VeraDismountRequest) -> tuple[str, tuple[str, ...]]:
    return _program_for(capability, "dismount"), (
        "--text", "--non-interactive", capability.unmount_option, str(request.target),
    )


def _program_for(capability: VeraCryptCapability, operation: str) -> str:
    if capability.executable is None or not capability.supports(operation):
        raise ValueError("この環境のVeraCrypt CLIは選択した操作に対応していません。")
    return str(capability.executable)


def _absolute_entered_path(text: str) -> Path | None:
    if not text.strip() or "\x00" in text:
        return None
    candidate = Path(text.strip()).expanduser()
    return absolute_path(candidate) if candidate.is_absolute() else None


def _is_file_or_block_device(path: Path) -> bool:
    try:
        mode = path.stat().st_mode
    except OSError:
        return False
    return stat.S_ISREG(mode) or stat.S_ISBLK(mode)
