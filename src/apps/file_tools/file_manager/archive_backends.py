"""Replaceable archive engines used by the file-manager extraction UI."""

from __future__ import annotations

from runtime import managed_process

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
import errno
import hashlib
import json
import os
from pathlib import Path
import platform
import pty
import re
import select
import signal
import subprocess
import termios
import tempfile
from typing import Final


PROJECT_ROOT = Path(__file__).resolve().parents[4]
SEVEN_ZIP_TOOL_DIR = (
    PROJECT_ROOT
    / "integrated_backends"
    / "7zip"
    / "26.02"
    / "linux-x86_64"
)
SEVEN_ZIP_MANIFEST = SEVEN_ZIP_TOOL_DIR / "manifest.json"


class ArchiveBackendError(RuntimeError):
    """Base error reported by an archive engine."""


class ArchiveBackendUnavailable(ArchiveBackendError):
    """Raised when no registered engine can run in the current environment."""


class ArchivePasswordRequired(ArchiveBackendError):
    """Raised when an archive prompted for a password but none was supplied."""


class ArchivePasswordRejected(ArchiveBackendError):
    """Raised when an archive engine rejected the supplied password."""


class ArchiveCommandCancelled(ArchiveBackendError):
    """Internal marker for a user-cancelled archive command."""


@dataclass(frozen=True)
class BackendAvailability:
    available: bool
    detail: str


@dataclass(frozen=True)
class ArchiveMember:
    path: str
    is_directory: bool
    link_target: str | None = None
    is_encrypted: bool = False
    size: int | None = None


ProgressCallback = Callable[[int], None]
ProcessCallback = Callable[[subprocess.Popen[bytes] | None], None]
CancelCheck = Callable[[], bool]


class ArchiveBackend(ABC):
    """Small interface that keeps the UI independent from one extraction tool."""

    backend_id: str
    display_name: str

    @abstractmethod
    def availability(self) -> BackendAvailability:
        """Report whether this backend can run here without changing the system."""

    @abstractmethod
    def list_members(
        self,
        archive: Path,
        *,
        password: str | None,
        cancelled: CancelCheck,
        process_changed: ProcessCallback,
    ) -> tuple[ArchiveMember, ...]:
        """Read the archive table before any extraction is attempted."""

    @abstractmethod
    def extract(
        self,
        archive: Path,
        destination: Path,
        *,
        password: str | None,
        progress: ProgressCallback,
        cancelled: CancelCheck,
        process_changed: ProcessCallback,
    ) -> None:
        """Extract into an empty, caller-owned staging directory."""

    def extract_members(
        self,
        archive: Path,
        destination: Path,
        members: tuple[str, ...],
        *,
        password: str | None,
        progress: ProgressCallback,
        cancelled: CancelCheck,
        process_changed: ProcessCallback,
    ) -> None:
        """Extract selected members; generic backends safely fall back to all."""
        self.extract(
            archive,
            destination,
            password=password,
            progress=progress,
            cancelled=cancelled,
            process_changed=process_changed,
        )

    @abstractmethod
    def test_archive(
        self,
        archive: Path,
        *,
        password: str | None,
        progress: ProgressCallback,
        cancelled: CancelCheck,
        process_changed: ProcessCallback,
    ) -> None:
        """Read and verify every stored item without writing output."""

    def test_members(
        self,
        archive: Path,
        members: tuple[str, ...],
        *,
        password: str | None,
        progress: ProgressCallback,
        cancelled: CancelCheck,
        process_changed: ProcessCallback,
    ) -> None:
        """Verify selected members; generic backends safely fall back to all."""
        self.test_archive(
            archive,
            password=password,
            progress=progress,
            cancelled=cancelled,
            process_changed=process_changed,
        )

    @abstractmethod
    def create_archive(
        self,
        sources: tuple[Path, ...],
        output: Path,
        *,
        archive_format: str,
        compression_level: int,
        password: str | None,
        hide_names: bool,
        progress: ProgressCallback,
        cancelled: CancelCheck,
        process_changed: ProcessCallback,
    ) -> None:
        """Create one new archive at a caller-owned, unoccupied path."""


class SevenZipStandaloneBackend(ArchiveBackend):
    """Bundled official 7zzs binary, isolated behind the backend interface."""

    backend_id = "bundled-7zzs"
    display_name = "同梱 7-Zip 7zzs 26.02"

    def __init__(self, manifest_path: Path = SEVEN_ZIP_MANIFEST) -> None:
        self._manifest_path = manifest_path

    def availability(self) -> BackendAvailability:
        try:
            manifest = self._load_manifest()
            expected_system = str(manifest["system"])
            expected_machine = str(manifest["machine"])
            executable = self._executable(manifest)
            if platform.system() != expected_system:
                return BackendAvailability(
                    False,
                    f"対象OSが違います（必要: {expected_system}、現在: {platform.system()}）。",
                )
            if _normalized_machine(platform.machine()) != expected_machine:
                return BackendAvailability(
                    False,
                    f"対象CPUが違います（必要: {expected_machine}、現在: {platform.machine()}）。",
                )
            if not executable.is_file():
                return BackendAvailability(False, f"実行ファイルがありません: {executable}")
            if not os.access(executable, os.X_OK):
                return BackendAvailability(False, f"実行権限がありません: {executable}")
            actual_hash = _sha256(executable)
            expected_hash = str(manifest["sha256"])
            if actual_hash != expected_hash:
                return BackendAvailability(False, "同梱7zzsの整合性検査に失敗しました。")
            probe = managed_process.run(
                [str(executable), "-h"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                check=False,
                timeout=10,
                env=_tool_environment(),
                label='7-Zip',
            )
            if probe.returncode != 0:
                detail = probe.stderr.decode("utf-8", errors="replace").strip()
                return BackendAvailability(False, detail or "同梱7zzsを起動できません。")
        except (KeyError, OSError, ValueError, json.JSONDecodeError) as exc:
            return BackendAvailability(False, f"同梱7zzsの構成を確認できません: {exc}")
        except subprocess.TimeoutExpired:
            return BackendAvailability(False, "同梱7zzsの起動確認が時間切れになりました。")
        return BackendAvailability(True, self.display_name)

    def list_members(
        self,
        archive: Path,
        *,
        password: str | None,
        cancelled: CancelCheck,
        process_changed: ProcessCallback,
    ) -> tuple[ArchiveMember, ...]:
        output = self._run(
            ["l", "-slt", "-ba", "-bd", str(archive)],
            password=password,
            cancelled=cancelled,
            process_changed=process_changed,
        )
        return _parse_technical_listing(output)

    def extract(
        self,
        archive: Path,
        destination: Path,
        *,
        password: str | None,
        progress: ProgressCallback,
        cancelled: CancelCheck,
        process_changed: ProcessCallback,
    ) -> None:
        self._run(
            [
                "x",
                str(archive),
                f"-o{destination}",
                "-y",
                "-aos",
                "-bsp1",
                "-bb1",
            ],
            password=password,
            progress=progress,
            cancelled=cancelled,
            process_changed=process_changed,
        )

    def extract_members(
        self,
        archive: Path,
        destination: Path,
        members: tuple[str, ...],
        *,
        password: str | None,
        progress: ProgressCallback,
        cancelled: CancelCheck,
        process_changed: ProcessCallback,
    ) -> None:
        self._run_for_members(
            "x",
            archive,
            members,
            password=password,
            progress=progress,
            cancelled=cancelled,
            process_changed=process_changed,
            extra_arguments=(f"-o{destination}", "-y", "-aos"),
        )

    def test_archive(
        self,
        archive: Path,
        *,
        password: str | None,
        progress: ProgressCallback,
        cancelled: CancelCheck,
        process_changed: ProcessCallback,
    ) -> None:
        self._run(
            ["t", str(archive), "-bd", "-bsp1", "-bb1"],
            password=password,
            progress=progress,
            cancelled=cancelled,
            process_changed=process_changed,
        )

    def test_members(
        self,
        archive: Path,
        members: tuple[str, ...],
        *,
        password: str | None,
        progress: ProgressCallback,
        cancelled: CancelCheck,
        process_changed: ProcessCallback,
    ) -> None:
        self._run_for_members(
            "t",
            archive,
            members,
            password=password,
            progress=progress,
            cancelled=cancelled,
            process_changed=process_changed,
        )

    def create_archive(
        self,
        sources: tuple[Path, ...],
        output: Path,
        *,
        archive_format: str,
        compression_level: int,
        password: str | None,
        hide_names: bool,
        progress: ProgressCallback,
        cancelled: CancelCheck,
        process_changed: ProcessCallback,
    ) -> None:
        if archive_format not in {"7z", "zip"}:
            raise ValueError(f"未対応の圧縮形式です: {archive_format}")
        if compression_level not in {0, 1, 3, 5, 7, 9}:
            raise ValueError(f"未対応の圧縮レベルです: {compression_level}")
        if hide_names and (archive_format != "7z" or not password):
            raise ValueError("ファイル名暗号化はパスワード付き7zでのみ使用できます。")
        arguments = [
            "a",
            str(output),
            *(str(source) for source in sources),
            f"-t{archive_format}",
            f"-mx={compression_level}",
            "-y",
            "-sse",
            "-bsp1",
            "-bb1",
        ]
        if password:
            arguments.append("-p")
            if archive_format == "7z" and hide_names:
                arguments.append("-mhe=on")
            elif archive_format == "zip":
                arguments.append("-mem=AES256")
        self._run(
            arguments,
            password=password,
            progress=progress,
            cancelled=cancelled,
            process_changed=process_changed,
        )

    def _load_manifest(self) -> dict[str, object]:
        value = json.loads(self._manifest_path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("manifest.jsonの形式が正しくありません。")
        return value

    def _executable(self, manifest: dict[str, object] | None = None) -> Path:
        current = manifest if manifest is not None else self._load_manifest()
        name = str(current["executable"])
        if Path(name).name != name:
            raise ValueError("実行ファイル名が不正です。")
        return self._manifest_path.parent / name

    def _run(
        self,
        arguments: list[str],
        *,
        password: str | None,
        cancelled: CancelCheck,
        process_changed: ProcessCallback,
        progress: ProgressCallback | None = None,
    ) -> str:
        availability = self.availability()
        if not availability.available:
            raise ArchiveBackendUnavailable(availability.detail)
        executable = self._executable()
        return _run_in_password_terminal(
            executable,
            arguments,
            password=password,
            progress=progress,
            cancelled=cancelled,
            process_changed=process_changed,
        )

    def _run_for_members(
        self,
        command: str,
        archive: Path,
        members: tuple[str, ...],
        *,
        password: str | None,
        progress: ProgressCallback,
        cancelled: CancelCheck,
        process_changed: ProcessCallback,
        extra_arguments: tuple[str, ...] = (),
    ) -> None:
        if not members:
            raise ValueError("検査・解凍するアーカイブ内項目がありません。")
        if any("\n" in member or "\r" in member or "\x00" in member for member in members):
            raise ValueError("制御文字を含むアーカイブ内パスは個別処理できません。")
        list_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                prefix="porta-7zip-members-",
                suffix=".txt",
                delete=False,
            ) as member_file:
                member_file.write("\n".join(members) + "\n")
                list_path = Path(member_file.name)
            self._run(
                [
                    command,
                    str(archive),
                    *extra_arguments,
                    "-bd",
                    "-bsp1",
                    "-bb1",
                    "-spd",
                    "-scsUTF-8",
                    f"@{list_path}",
                ],
                password=password,
                progress=progress,
                cancelled=cancelled,
                process_changed=process_changed,
            )
        finally:
            if list_path is not None:
                list_path.unlink(missing_ok=True)


BackendFactory = Callable[[], ArchiveBackend]
ARCHIVE_BACKEND_FACTORIES: tuple[BackendFactory, ...] = (SevenZipStandaloneBackend,)


def archive_backend_candidates() -> tuple[ArchiveBackend, ...]:
    """Return candidates in preference order; later backends can be appended here."""
    return tuple(factory() for factory in ARCHIVE_BACKEND_FACTORIES)


def select_archive_backend(backend_id: str | None = None) -> ArchiveBackend:
    """Select the first usable candidate, or one already frozen into a plan."""
    failures: list[str] = []
    for backend in archive_backend_candidates():
        if backend_id is not None and backend.backend_id != backend_id:
            continue
        availability = backend.availability()
        if availability.available:
            return backend
        failures.append(f"{backend.display_name}: {availability.detail}")
    if backend_id is not None and not failures:
        failures.append(f"登録されていないバックエンドです: {backend_id}")
    detail = "\n".join(failures) or "解凍バックエンドが登録されていません。"
    raise ArchiveBackendUnavailable(detail)


def _run_in_password_terminal(
    executable: Path,
    arguments: list[str],
    *,
    password: str | None,
    progress: ProgressCallback | None,
    cancelled: CancelCheck,
    process_changed: ProcessCallback,
) -> str:
    """Run 7zzs in a private PTY so a password never appears in argv or logs."""
    master_fd, slave_fd = pty.openpty()
    attributes = termios.tcgetattr(slave_fd)
    attributes[3] &= ~termios.ECHO
    termios.tcsetattr(slave_fd, termios.TCSANOW, attributes)
    process: subprocess.Popen[bytes] | None = None
    captured = bytearray()
    prompt_tail = ""
    try:
        process = managed_process.popen(
            [str(executable), *arguments],
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            close_fds=True,
            start_new_session=True,
            env=_tool_environment(),
            label='7-Zip',
        )
        os.close(slave_fd)
        slave_fd = -1
        os.set_blocking(master_fd, False)
        process_changed(process)
        while process.poll() is None:
            if cancelled():
                _stop_process_group(process)
                raise ArchiveCommandCancelled("解凍を中止しました。")
            readable, _, _ = select.select([master_fd], [], [], 0.1)
            if not readable:
                continue
            chunk = _read_pty(master_fd)
            if chunk is None or not chunk:
                continue
            captured.extend(chunk)
            if len(captured) > _MAX_TOOL_OUTPUT:
                _stop_process_group(process)
                raise ArchiveBackendError("解凍ツールの出力が上限を超えました。")
            decoded = chunk.decode("utf-8", errors="replace")
            prompt_tail = (prompt_tail + decoded)[-512:]
            if "Enter password" in prompt_tail:
                if not password:
                    _stop_process_group(process)
                    raise ArchivePasswordRequired("この圧縮ファイルにはパスワードが必要です。")
                os.write(master_fd, password.encode("utf-8") + b"\n")
                prompt_tail = ""
            if progress is not None:
                matches = _PROGRESS_PATTERN.findall(decoded)
                if matches:
                    progress(max(0, min(100, int(matches[-1]))))
        while True:
            readable, _, _ = select.select([master_fd], [], [], 0.1)
            if not readable:
                break
            chunk = _read_pty(master_fd)
            if chunk is None:
                continue
            if not chunk:
                break
            captured.extend(chunk)
        output = captured.decode("utf-8", errors="replace")
        if process.returncode != 0:
            lowered = output.casefold()
            if "wrong password" in lowered or "password is incorrect" in lowered:
                raise ArchivePasswordRejected("パスワードが正しくありません。")
            raise ArchiveBackendError(_command_error_message(process.returncode, output))
        if progress is not None:
            progress(100)
        return output
    finally:
        process_changed(None)
        if process is not None and process.poll() is None:
            _stop_process_group(process)
        if slave_fd >= 0:
            os.close(slave_fd)
        os.close(master_fd)


def _read_pty(master_fd: int) -> bytes | None:
    try:
        return os.read(master_fd, 65536)
    except BlockingIOError:
        return None
    except OSError as exc:
        if exc.errno == errno.EIO:
            return b""
        raise


def _stop_process_group(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGCONT)
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=2)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()


def _parse_technical_listing(output: str) -> tuple[ArchiveMember, ...]:
    members: list[ArchiveMember] = []
    current: dict[str, str] = {}
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line:
            if "Path" in current:
                members.append(_member_from_fields(current))
            current = {}
            continue
        if " = " in raw_line:
            key, value = raw_line.split(" = ", 1)
            current[key.strip()] = value
    if "Path" in current:
        members.append(_member_from_fields(current))
    return tuple(members)


def _member_from_fields(fields: dict[str, str]) -> ArchiveMember:
    try:
        size = int(fields["Size"])
    except (KeyError, ValueError):
        size = None
    return ArchiveMember(
        fields["Path"],
        fields.get("Folder") == "+",
        fields.get("Symbolic Link") or fields.get("Hard Link"),
        fields.get("Encrypted") == "+",
        size,
    )


def _command_error_message(return_code: int | None, output: str) -> str:
    meaningful = [line.strip() for line in output.replace("\r", "\n").splitlines() if line.strip()]
    excerpt = "\n".join(meaningful[-8:])
    return f"7zzsがエラー終了しました（終了コード: {return_code}）。" + (
        f"\n{excerpt}" if excerpt else ""
    )


def _normalized_machine(value: str) -> str:
    aliases = {"amd64": "x86_64", "x64": "x86_64", "aarch64": "arm64"}
    return aliases.get(value.casefold(), value.casefold())


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _tool_environment() -> dict[str, str]:
    environment = os.environ.copy()
    environment["LC_ALL"] = "C.UTF-8"
    return environment


_PROGRESS_PATTERN: Final[re.Pattern[str]] = re.compile(r"(?<!\d)(\d{1,3})%")
_MAX_TOOL_OUTPUT: Final[int] = 32 * 1024 * 1024
