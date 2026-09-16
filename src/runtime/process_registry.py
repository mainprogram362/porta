"""Linux runtime inventory. No arguments, target paths, documents or history.

A process is identified by boot ID, PID and /proc start ticks. Each publisher
owns its own files; a child registering itself takes precedence over its parent.
The registry observes processes only. It never signals or terminates them.
"""
from __future__ import annotations

import atexit
from dataclasses import asdict, dataclass, replace
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
from threading import Event, RLock, Thread
import time
from uuid import uuid4

PORTA_ROOT = Path(__file__).resolve().parents[2]
_MAX_BYTES = 128 * 1024


@dataclass(frozen=True)
class ProcessIdentity:
    pid: int
    start_ticks: int
    boot_id: str

    @property
    def key(self) -> str:
        return hashlib.sha256(f"{self.boot_id}:{self.pid}:{self.start_ticks}".encode()).hexdigest()[:24]


@dataclass(frozen=True)
class ProcessStat:
    identity: ProcessIdentity
    parent_pid: int
    state: str


def read_process(pid: int, proc_root: Path = Path('/proc')) -> ProcessStat:
    """Parse stat after the last ')': comm may contain spaces and parentheses."""
    if type(pid) is not int or pid <= 0:
        raise ValueError('Invalid PID')
    raw = (proc_root / str(pid) / 'stat').read_text()
    fields = raw[raw.rindex(')') + 2:].split()
    boot = (proc_root / 'sys/kernel/random/boot_id').read_text().strip()
    return ProcessStat(ProcessIdentity(pid, int(fields[19]), boot), int(fields[1]), fields[0])


def identity_status(identity: ProcessIdentity, proc_root: Path = Path('/proc')) -> str:
    try:
        current = read_process(identity.pid, proc_root)
    except FileNotFoundError:
        return 'exited' if proc_root.is_dir() and not (proc_root / str(identity.pid)).exists() else 'unknown'
    except ProcessLookupError:
        return 'exited'
    except (OSError, ValueError, IndexError):
        return 'unknown'
    if current.identity != identity or current.state in {'Z', 'X', 'x'}:
        return 'exited'
    return 'stopped' if current.state in {'T', 't'} else 'alive'


def runtime_directory() -> Path:
    """Use the login runtime directory, with a short private /tmp fallback."""
    base = os.environ.get('XDG_RUNTIME_DIR')
    if base:
        candidate = Path(base)
        try:
            info = candidate.lstat()
            if candidate.is_absolute() and stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o700:
                return candidate / 'porta-processes'
        except OSError:
            pass
    return Path('/tmp') / f'porta-processes-{os.getuid()}'


def _private_directory(path: Path) -> None:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise PermissionError('プロセス登録先の所有者・権限を確認できません。')


def _identity(value: object) -> ProcessIdentity:
    if not isinstance(value, dict):
        raise ValueError('Invalid process identity')
    pid, ticks, boot = value.get('pid'), value.get('start_ticks'), value.get('boot_id')
    if type(pid) is not int or pid <= 0 or type(ticks) is not int or ticks < 0 or not isinstance(boot, str) or not boot or len(boot) > 64:
        raise ValueError('Invalid process identity')
    return ProcessIdentity(pid, ticks, boot)


@dataclass(frozen=True)
class ProcessInfo:
    identity: ProcessIdentity
    owner: ProcessIdentity
    root: str
    role: str
    label: str
    screen: str = ''
    state: str = ''
    activities: tuple[str, ...] = ()
    ownership: str = 'owned'
    heartbeat: float = 0
    observed: bool = False
    health: str = 'alive'
    owner_health: str = 'alive'

    @property
    def pid(self) -> int:
        return self.identity.pid


def _decode(value: object) -> ProcessInfo:
    if not isinstance(value, dict) or value.get('version') != 1:
        raise ValueError('Invalid registry version')
    for field in ('root', 'role', 'label', 'screen', 'state', 'ownership'):
        if not isinstance(value.get(field), str) or len(value[field]) > 4096:
            raise ValueError('Invalid registry field')
    jobs = value.get('activities')
    if not isinstance(jobs, list) or not all(isinstance(job, str) and len(job) <= 256 for job in jobs):
        raise ValueError('Invalid activity list')
    heartbeat = value.get('heartbeat', 0)
    if type(heartbeat) not in (int, float) or not 0 <= heartbeat < float('inf'):
        raise ValueError('Invalid heartbeat')
    return ProcessInfo(_identity(value.get('identity')), _identity(value.get('owner')),
                       value['root'], value['role'], value['label'], value['screen'], value['state'],
                       tuple(jobs), value['ownership'], heartbeat, bool(value.get('observed')))


def list_processes(*, directory: Path | None = None, root: Path | None = PORTA_ROOT,
                   proc_root: Path = Path('/proc')) -> tuple[ProcessInfo, ...]:
    """Read a validated snapshot; never delete files on a read or access failure."""
    directory = directory or runtime_directory()
    if not directory.exists():
        return ()
    info = directory.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise PermissionError('プロセス登録先の所有者・権限を確認できません。')
    found: dict[ProcessIdentity, ProcessInfo] = {}
    origins: dict[ProcessIdentity, ProcessInfo] = {}
    for path in directory.glob('*.json'):
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd, 'rb') as stream:
                entry_stat = os.fstat(stream.fileno())
                if not stat.S_ISREG(entry_stat.st_mode) or entry_stat.st_size > _MAX_BYTES:
                    continue
                item = _decode(json.loads(stream.read(_MAX_BYTES + 1)))
            if path.name != f'{item.identity.key}-{item.owner.key}.json':
                continue
            if root is not None and item.root != str(root.resolve()):
                continue
            if item.ownership == 'external' or item.role == 'external':
                # External applications own their own lifetime and process
                # trees.  Old releases wrote these records; remove them as
                # part of the migration instead of showing them as PORTA work.
                path.unlink(missing_ok=True)
                continue
            health = identity_status(item.identity, proc_root)
            if health == 'exited':
                # The filename includes the exact start identity: a newer
                # process reusing this PID cannot own this entry.
                path.unlink(missing_ok=True)
                continue
            # A live PID with an old GUI pulse is not proof of a hang or death.
            if health == 'alive' and item.heartbeat and time.monotonic() - item.heartbeat > 10:
                health = 'unresponsive'
            item = replace(item, health=health, owner_health=identity_status(item.owner, proc_root))
            if item.owner != item.identity:
                origin = origins.get(item.identity)
                if origin is None or (origin.observed and not item.observed):
                    origins[item.identity] = item
            existing = found.get(item.identity)
            priority = (item.identity == item.owner, not item.observed)
            if existing is None or priority > (existing.identity == existing.owner, not existing.observed):
                found[item.identity] = item
        except (OSError, ValueError, TypeError, KeyError):
            continue
    for identity, item in tuple(found.items()):
        origin = origins.get(identity)
        if item.owner == identity and origin is not None:
            # Keep the child's own GUI/job state and its original launcher.
            found[identity] = replace(item, owner=origin.owner, owner_health=origin.owner_health,
                                      ownership=origin.ownership)
    return tuple(sorted(found.values(), key=lambda item: item.pid))


class ProcessRegistry:
    """One publisher per process, shared by GUI presence, activities and children."""
    def __init__(self, directory: Path | None = None, *, root: Path = PORTA_ROOT,
                 monitor: bool = True) -> None:
        self.directory = directory or runtime_directory()
        self.root = str(root.resolve())
        self.identity = read_process(os.getpid()).identity
        self._lock = RLock()
        self._leases: dict[str, tuple[str, str, str, float]] = {}
        self._heartbeat = 0.0
        self._activities: dict[str, str] = {}
        self._children: dict[ProcessIdentity, ProcessInfo] = {}
        self._ignored: set[ProcessIdentity] = set()
        self._stop = Event()
        self._thread: Thread | None = None
        self._monitor_enabled = monitor
        self.error = ''

    def path_for(self, identity: ProcessIdentity) -> Path:
        return self.directory / f'{identity.key}-{self.identity.key}.json'

    def _write(self, item: ProcessInfo) -> None:
        temporary: str | None = None
        try:
            _private_directory(self.directory)
            fd, temporary = tempfile.mkstemp(prefix='.', suffix='.tmp', dir=self.directory)
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                data = asdict(item)
                data['version'] = 1
                json.dump(data, stream, ensure_ascii=False)
            os.replace(temporary, self.path_for(item.identity))
            self.error = ''
        except OSError as exc:
            # Observation must not break an already-started copy/encoder/player.
            self.error = f'プロセス情報を登録できません: {exc}'
        finally:
            if temporary is not None:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass

    def _remove(self, identity: ProcessIdentity) -> None:
        try:
            self.path_for(identity).unlink(missing_ok=True)
        except OSError as exc:
            self.error = f'プロセス情報を解除できません: {exc}'

    def _publish(self) -> None:
        if not self._leases and not self._activities and not self._children:
            self._remove(self.identity)
            return
        role, screen, state, _ = next(reversed(self._leases.values())) if self._leases else ('worker', '', '処理中', 0)
        self._write(ProcessInfo(self.identity, self.identity, self.root, role,
                               '一時対応表' if role == 'records' else 'PORTA', screen, state,
                               tuple(self._activities.values()), 'independent' if role in {'records', 'work'} else 'owned',
                               self._heartbeat if self._leases else 0))
        if self._monitor_enabled and self._thread is None and not self._stop.is_set():
            self._thread = Thread(target=self._watch, name='porta-process-observer', daemon=True)
            self._thread.start()

    def acquire(self, role: str, screen: str, state: str) -> str:
        with self._lock:
            token = uuid4().hex
            self._leases[token] = (role, screen, state, 0)
            self._publish()
            return token

    def update(self, token: str, screen: str, state: str, *, pulse: bool = False) -> None:
        with self._lock:
            if token not in self._leases:
                return
            if pulse:
                self._heartbeat = time.monotonic()
            else:
                role, _, _, heartbeat = self._leases.pop(token)
                self._leases[token] = (role, screen[:256], state[:256], heartbeat)
            self._publish()

    def release(self, token: str) -> None:
        with self._lock:
            self._leases.pop(token, None)
            self._publish()

    def begin_activity(self, label: str) -> str:
        label = ' '.join(label.split())
        if not label:
            raise ValueError('実行中の操作名を指定してください。')
        with self._lock:
            token = uuid4().hex
            self._activities[token] = label[:256]
            self._publish()
            return token

    def end_activity(self, token: str) -> None:
        with self._lock:
            self._activities.pop(token, None)
            self._publish()

    def activity_labels(self) -> tuple[str, ...]:
        """Current in-process jobs, without disk I/O or exposing mutable state."""
        with self._lock:
            return tuple(self._activities.values())

    def track(self, pid: int, label: str, *, role: str = 'worker', ownership: str = 'owned') -> None:
        if role == 'external' or ownership == 'external':
            return
        if type(pid) is not int or pid <= 0 or pid == self.identity.pid:
            return
        try:
            current = read_process(pid)
            if current.state in {'Z', 'X', 'x'}:
                return
        except (OSError, ValueError, IndexError):
            return  # May have finished between start and registration.
        with self._lock:
            item = ProcessInfo(current.identity, self.identity, self.root, role, label[:256], ownership=ownership)
            self._children[item.identity] = item
            self._write(item)
            self._publish()

    def ignore(self, pid: int) -> None:
        """Exclude an externally owned launch before descendant observation.

        This is in-memory exclusion only.  It deliberately creates no
        registry entry and does not affect the launched application's process.
        """
        if type(pid) is not int or pid <= 0:
            return
        try:
            identity = read_process(pid).identity
        except (OSError, ValueError, IndexError):
            return
        with self._lock:
            self._ignored.add(identity)
            item = self._children.pop(identity, None)
            if item is not None:
                self._remove(identity)
            self._publish()

    def observe(self) -> None:
        """Keep observed descendants identifiable even if their parent later exits."""
        # Reading every /proc entry can be slow on a busy machine. Never hold
        # the registry lock during that scan: GUI work uses the same lock to
        # publish and finish activities, and must not wait behind discovery.
        with self._lock:
            children = dict(self._children)
            ignored = set(self._ignored)
            active = bool(self._leases or self._activities or children)
        child_health = {identity: identity_status(identity) for identity in children}
        ignored_health = {identity: identity_status(identity) for identity in ignored}
        live_children = {
            identity: item for identity, item in children.items()
            if child_health[identity] in {'alive', 'stopped'}
        }
        if not active:
            with self._lock:
                self._publish()
            return

        roots = {self.identity.pid: ('owned', self.identity)}
        roots.update({key.pid: (item.ownership, key) for key, item in live_children.items()})
        processes = []
        for path in Path('/proc').iterdir():
            if path.name.isdecimal():
                try:
                    processes.append(read_process(int(path.name)))
                except (OSError, ValueError, IndexError):
                    pass
        discovered: list[ProcessInfo] = []
        while True:
            added = False
            for current in processes:
                if (current.identity in ignored or current.identity.pid in roots
                        or current.parent_pid not in roots
                        or current.state in {'Z', 'X', 'x'}):
                    continue
                ownership, _parent = roots[current.parent_pid]
                roots[current.identity.pid] = (ownership, current.identity)
                discovered.append(ProcessInfo(
                    current.identity, self.identity, self.root, 'descendant',
                    '関連する子プロセス', ownership=ownership, observed=True,
                ))
                added = True
            if not added:
                break

        with self._lock:
            for identity, health in child_health.items():
                if health == 'exited' and self._children.get(identity) is children[identity]:
                    self._children.pop(identity)
                    self._remove(identity)
            for identity, health in ignored_health.items():
                if health not in {'alive', 'stopped'}:
                    self._ignored.discard(identity)
            for item in discovered:
                if item.identity in self._ignored or item.identity in self._children:
                    continue
                self._children[item.identity] = item
                self._write(item)
            self._publish()

    def _watch(self) -> None:
        while not self._stop.wait(1):
            try:
                self.observe()
            except OSError as exc:
                self.error = f'プロセスを確認できません: {exc}'

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
        with self._lock:
            self._remove(self.identity)
            for identity in self._children:
                if identity_status(identity) == 'exited':
                    self._remove(identity)
            # Live independent PORTA work remains registered for reconnection.


_default: ProcessRegistry | None = None
_default_lock = RLock()


def get_registry() -> ProcessRegistry:
    global _default
    with _default_lock:
        if _default is None or _default.identity.pid != os.getpid():
            _default = ProcessRegistry()
            atexit.register(_default.close)
        return _default
