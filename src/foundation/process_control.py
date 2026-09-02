"""Narrow process discovery for explicit PORTA instance cleanup."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import signal

from .runtime_activity import RuntimeActivityInfo, active_runtime_activities


@dataclass(frozen=True)
class MainProcess:
    """A process proven to be running one exact ``scripts/main.py`` file."""

    pid: int
    command: tuple[str, ...]


def running_main_processes(
    main_script: Path,
    *,
    proc_root: Path = Path("/proc"),
) -> tuple[MainProcess, ...]:
    """Find only processes whose arguments resolve to this exact main script.

    The executable name is deliberately ignored: a virtual-environment Python,
    system Python, or a shell wrapper may all start this application.  Firefox,
    VS Code and unrelated Python processes cannot match unless they explicitly
    pass this project's exact ``scripts/main.py`` path as an argument.
    """
    expected = main_script.expanduser().resolve(strict=False)
    matches: list[MainProcess] = []
    try:
        entries = tuple(proc_root.iterdir())
    except OSError:
        return ()
    for entry in entries:
        if not entry.name.isdecimal():
            continue
        try:
            raw_arguments = (entry / "cmdline").read_bytes().split(b"\0")
            arguments = tuple(
                value.decode(errors="surrogateescape") for value in raw_arguments if value
            )
            if not arguments:
                continue
            cwd = Path(os.readlink(entry / "cwd"))
        except OSError:
            continue
        for argument in arguments[1:]:
            candidate = Path(argument).expanduser()
            resolved = (cwd / candidate).resolve(strict=False) if not candidate.is_absolute() else candidate.resolve(strict=False)
            if resolved == expected:
                matches.append(MainProcess(int(entry.name), arguments))
                break
    return tuple(sorted(matches, key=lambda process: process.pid))


def terminate_main_processes(main_script: Path, *, exclude_pid: int | None = None) -> tuple[int, ...]:
    """Send SIGTERM only to explicit instances of this project's main script."""
    terminated: list[int] = []
    for process in running_main_processes(main_script):
        if process.pid == exclude_pid:
            continue
        try:
            os.kill(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            continue
        except PermissionError:
            continue
        terminated.append(process.pid)
    return tuple(terminated)


def active_main_operations(
    main_script: Path,
    *,
    proc_root: Path = Path("/proc"),
    runtime_directory: Path | None = None,
) -> tuple[RuntimeActivityInfo, ...]:
    """Return active work reported by exact instances of this project only."""
    pids = {process.pid for process in running_main_processes(main_script, proc_root=proc_root)}
    if runtime_directory is None:
        return active_runtime_activities(pids)
    return active_runtime_activities(pids, directory=runtime_directory)
