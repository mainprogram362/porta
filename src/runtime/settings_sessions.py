"""Ephemeral coordination for concurrently opened PORTA settings screens."""
from __future__ import annotations

import atexit
import json
import os
from pathlib import Path
import stat
import tempfile
import time
from uuid import uuid4

from runtime.process_registry import PORTA_ROOT, ProcessIdentity, identity_status, read_process, runtime_directory


def _directory(path: Path) -> None:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise PermissionError("設定画面の登録先を安全に使用できません。")


def _identity(data: object) -> ProcessIdentity:
    if not isinstance(data, dict):
        raise ValueError("設定画面のプロセス情報が不正です。")
    pid, ticks, boot = data.get("pid"), data.get("start_ticks"), data.get("boot_id")
    if type(pid) is not int or pid <= 0 or type(ticks) is not int or ticks < 0:
        raise ValueError("設定画面のプロセス情報が不正です。")
    if not isinstance(boot, str) or not boot or len(boot) > 64:
        raise ValueError("設定画面のプロセス情報が不正です。")
    return ProcessIdentity(pid, ticks, boot)


class SettingsSession:
    """Publish one screen and identify whether a newer settings screen exists."""

    def __init__(self, *, directory: Path | None = None, root: Path = PORTA_ROOT) -> None:
        if directory is None:
            registry_directory = runtime_directory()
            _directory(registry_directory)
            base = registry_directory / "settings-screens"
        else:
            base = directory
        self.directory = base
        self.root = str(root.resolve())
        self.identity = read_process(os.getpid()).identity
        self.session_id = uuid4().hex
        self.opened = time.monotonic_ns()
        self.path = base / f"{self.session_id}.json"
        self.error = ""
        self._closed = False
        self._publish()
        atexit.register(self.close)

    def _publish(self) -> None:
        temporary: str | None = None
        try:
            _directory(self.directory)
            fd, temporary = tempfile.mkstemp(prefix=".", suffix=".tmp", dir=self.directory)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(
                    {
                        "version": 1,
                        "session_id": self.session_id,
                        "root": self.root,
                        "opened": self.opened,
                        "identity": {
                            "pid": self.identity.pid,
                            "start_ticks": self.identity.start_ticks,
                            "boot_id": self.identity.boot_id,
                        },
                    },
                    stream,
                    ensure_ascii=False,
                )
            os.replace(temporary, self.path)
        except OSError as exc:
            self.error = f"設定画面の重複を確認できません: {exc}"
        finally:
            if temporary is not None:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass

    def active(self) -> tuple[dict[str, object], ...]:
        try:
            _directory(self.directory)
        except OSError as exc:
            self.error = f"設定画面の重複を確認できません: {exc}"
            return ()
        found: list[dict[str, object]] = []
        for path in self.directory.glob("*.json"):
            try:
                fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                with os.fdopen(fd, "r", encoding="utf-8") as stream:
                    if os.fstat(stream.fileno()).st_size > 16 * 1024:
                        continue
                    data = json.load(stream)
                session_id = data.get("session_id")
                opened = data.get("opened")
                if (
                    data.get("version") != 1
                    or data.get("root") != self.root
                    or not isinstance(session_id, str)
                    or len(session_id) != 32
                    or type(opened) is not int
                    or opened < 0
                    or path.name != f"{session_id}.json"
                ):
                    continue
                identity = _identity(data.get("identity"))
                if identity_status(identity) == "exited":
                    path.unlink(missing_ok=True)
                    continue
                found.append({"session_id": session_id, "opened": opened, "pid": identity.pid})
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                continue
        return tuple(sorted(found, key=lambda item: (item["opened"], item["session_id"])))

    def others(self) -> tuple[dict[str, object], ...]:
        return tuple(item for item in self.active() if item["session_id"] != self.session_id)

    def is_newest(self) -> bool:
        sessions = self.active()
        return not sessions or sessions[-1]["session_id"] == self.session_id

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self.path.unlink(missing_ok=True)
        except OSError:
            pass
