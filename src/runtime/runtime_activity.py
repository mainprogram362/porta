"""In-process jobs published through the same inventory as PORTA processes."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from runtime.process_registry import ProcessRegistry, get_registry, list_processes


@dataclass(frozen=True)
class RuntimeActivityInfo:
    pid: int
    label: str


class RuntimeActivity:
    def __init__(self, registry: ProcessRegistry, token: str) -> None:
        self._registry = registry
        self._token = token
        self._closed = False

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._registry.end_activity(self._token)

    def __enter__(self) -> RuntimeActivity:
        return self

    def __exit__(self, *_unused: object) -> None:
        self.close()


def begin_runtime_activity(label: str) -> RuntimeActivity:
    registry = get_registry()
    return RuntimeActivity(registry, registry.begin_activity(label))


@contextmanager
def runtime_activity(label: str):
    activity = begin_runtime_activity(label)
    try:
        yield activity
    finally:
        activity.close()


def active_runtime_activities(pids: set[int], *, directory: Path | None = None,
                              proc_root: Path = Path('/proc')) -> tuple[RuntimeActivityInfo, ...]:
    return tuple(RuntimeActivityInfo(item.pid, label)
                 for item in list_processes(directory=directory, root=None, proc_root=proc_root)
                 if item.pid in pids for label in item.activities)
