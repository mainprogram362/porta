"""Validated PORTA inventory and the existing explicit force-close command."""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
import signal

from runtime.process_registry import PORTA_ROOT, ProcessIdentity, ProcessInfo, identity_status, list_processes, read_process
from runtime.runtime_activity import RuntimeActivityInfo


@dataclass(frozen=True)
class MainProcess:
    pid: int
    command: tuple[str, ...]
    identity: ProcessIdentity


def _script_argument(arguments: tuple[str, ...]) -> str | None:
    """An editor or 'python other.py scripts/main.py' is not a PORTA process."""
    if not arguments or not re.fullmatch(r'(?:python(?:\d+(?:\.\d+)*)?|pypy\d*)', Path(arguments[0]).name):
        return None
    index = 1
    while index < len(arguments):
        argument = arguments[index]
        if argument in {'-c', '-m'} or argument.startswith(('-c', '-m')):
            return None
        if argument == '--':
            return arguments[index + 1] if index + 1 < len(arguments) else None
        if argument in {'-W', '-X'}:
            index += 2
            continue
        if argument.startswith('-'):
            index += 1
            continue
        return argument
    return None


def _main_arguments(entry: Path, expected: Path) -> tuple[str, ...] | None:
    arguments = tuple(value.decode(errors='surrogateescape')
                      for value in (entry / 'cmdline').read_bytes().split(b'\0') if value)
    if '--list-processes' in arguments or '--close-instances' in arguments:
        return None
    script = _script_argument(arguments)
    if script is None:
        return None
    path = Path(script)
    if not path.is_absolute():
        path = Path(os.readlink(entry / 'cwd')) / path
    return arguments if path.resolve() == expected else None


def running_main_processes(main_script: Path, *, proc_root: Path = Path('/proc')) -> tuple[MainProcess, ...]:
    """Discover this installation's Python entrypoints, including older versions."""
    expected = main_script.resolve()
    matches = []
    try:
        entries = tuple(proc_root.iterdir())
    except OSError:
        return ()
    for entry in entries:
        if not entry.name.isdecimal():
            continue
        try:
            if entry.stat().st_uid != os.getuid():
                continue
            before = read_process(int(entry.name), proc_root)
            arguments = _main_arguments(entry, expected)
            after = read_process(int(entry.name), proc_root)
            if arguments and before.identity == after.identity and after.state not in {'Z', 'X', 'x'}:
                matches.append(MainProcess(int(entry.name), arguments, before.identity))
        except (OSError, ValueError, IndexError):
            continue
    return tuple(sorted(matches, key=lambda item: item.pid))


def process_inventory(*, root: Path | None = PORTA_ROOT, directory: Path | None = None,
                      proc_root: Path = Path('/proc')) -> tuple[ProcessInfo, ...]:
    """Unregistered entrypoints remain visible but never claim GUI/job health."""
    registered = list_processes(root=root, directory=directory, proc_root=proc_root)
    found = {item.identity: item for item in registered}
    roots = {root.resolve()} if root is not None else {PORTA_ROOT, *(Path(item.root) for item in registered)}
    for installation in roots:
        for process in running_main_processes(installation / 'scripts/main.py', proc_root=proc_root):
            if process.identity not in found:
                found[process.identity] = ProcessInfo(process.identity, process.identity, str(installation),
                    'unregistered', 'PORTA（未登録）', state='画面・作業情報は未登録',
                    health=identity_status(process.identity, proc_root))
    return tuple(sorted(found.values(), key=lambda item: item.pid))


def terminate_main_processes(main_script: Path, *, exclude_pid: int | None = None) -> tuple[int, ...]:
    """Existing explicit SIGTERM operation, pinned to the validated OS process.

    Never fall back to an unpinned PID if pidfd is unavailable. This command is
    still force-close, not an orderly GUI shutdown, and is not exposed in the UI.
    """
    terminated = []
    for process in running_main_processes(main_script):
        if process.pid == exclude_pid:
            continue
        descriptor = None
        try:
            descriptor = os.pidfd_open(process.pid)
            if read_process(process.pid).identity != process.identity:
                continue
            if not _main_arguments(Path('/proc') / str(process.pid), main_script.resolve()):
                continue
            signal.pidfd_send_signal(descriptor, signal.SIGTERM)
            terminated.append(process.pid)
        except (OSError, ValueError, IndexError, AttributeError):
            continue
        finally:
            if descriptor is not None:
                os.close(descriptor)
    return tuple(terminated)


def active_main_operations(main_script: Path, *, proc_root: Path = Path('/proc'),
                           runtime_directory: Path | None = None) -> tuple[RuntimeActivityInfo, ...]:
    """Read jobs from this installation's identity-validated registry."""
    return tuple(RuntimeActivityInfo(item.pid, label)
                 for item in list_processes(root=main_script.resolve().parents[1],
                     proc_root=proc_root, directory=runtime_directory)
                 for label in item.activities)
