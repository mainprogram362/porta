"""Ephemeral, local-only activity markers used to protect active work."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import json
import os
from pathlib import Path
import tempfile
from threading import RLock
from uuid import uuid4


RUNTIME_DIRECTORY = Path(tempfile.gettempdir()) / f"porta-place-runtime-{os.getuid()}"
_LOCK = RLock()
_OWN_ACTIVITIES: dict[str, str] = {}


@dataclass(frozen=True)
class RuntimeActivityInfo:
    """One active operation reported by a currently running app process."""

    pid: int
    label: str


class RuntimeActivity:
    """A removable marker for one process-local operation in progress."""

    def __init__(self, token: str) -> None:
        self._token = token
        self._closed = False

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        with _LOCK:
            _OWN_ACTIVITIES.pop(self._token, None)
            _write_own_activities()

    def __enter__(self) -> "RuntimeActivity":
        return self

    def __exit__(self, *_unused: object) -> None:
        self.close()


def begin_runtime_activity(label: str) -> RuntimeActivity:
    """Mark an explicit operation as active until the returned handle is closed.

    The marker contains only a generic operation label and this process ID.  It
    is written under ``/tmp`` while active and is removed as soon as all work in
    the process finishes; it is not a history or an execution log.
    """
    cleaned = " ".join(label.split())
    if not cleaned:
        raise ValueError("実行中の操作名を指定してください。")
    token = uuid4().hex
    with _LOCK:
        _OWN_ACTIVITIES[token] = cleaned
        try:
            _write_own_activities()
        except OSError:
            _OWN_ACTIVITIES.pop(token, None)
            raise
    return RuntimeActivity(token)


@contextmanager
def runtime_activity(label: str):  # type: ignore[no-untyped-def]
    """Context-manager form for a synchronous operation."""
    activity = begin_runtime_activity(label)
    try:
        yield activity
    finally:
        activity.close()


def active_runtime_activities(
    pids: set[int],
    *,
    directory: Path = RUNTIME_DIRECTORY,
) -> tuple[RuntimeActivityInfo, ...]:
    """Read current markers for already-validated process IDs only."""
    activities: list[RuntimeActivityInfo] = []
    for pid in sorted(pids):
        try:
            payload = json.loads((directory / f"{pid}.json").read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            continue
        if payload.get("pid") != pid or not isinstance(payload.get("activities"), list):
            continue
        for label in payload["activities"]:
            if isinstance(label, str) and label.strip():
                activities.append(RuntimeActivityInfo(pid, label))
    return tuple(activities)


def _write_own_activities() -> None:
    RUNTIME_DIRECTORY.mkdir(mode=0o700, parents=True, exist_ok=True)
    status_path = RUNTIME_DIRECTORY / f"{os.getpid()}.json"
    if not _OWN_ACTIVITIES:
        try:
            status_path.unlink()
        except FileNotFoundError:
            pass
        try:
            RUNTIME_DIRECTORY.rmdir()
        except OSError:
            # Another PORTA process may still own a marker in this directory.
            pass
        return
    temporary_path = RUNTIME_DIRECTORY / f".{os.getpid()}-{uuid4().hex}.tmp"
    payload = {"pid": os.getpid(), "activities": list(_OWN_ACTIVITIES.values())}
    try:
        temporary_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        os.chmod(temporary_path, 0o600)
        temporary_path.replace(status_path)
    finally:
        try:
            temporary_path.unlink()
        except FileNotFoundError:
            pass
