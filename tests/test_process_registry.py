"""Identity, ownership, failure handling and actual child lifecycle regressions."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import time

import pytest

from foundation import managed_process, process_registry as registry, runtime_activity
from foundation.instance_presence import InstancePresence


@pytest.fixture
def inventory(tmp_path, monkeypatch):
    instance = registry.ProcessRegistry(tmp_path / 'runtime', monitor=False)
    monkeypatch.setattr(registry, '_default', instance)
    try:
        yield instance
    finally:
        instance.close()


def entries(inventory):
    return registry.list_processes(directory=inventory.directory)


def test_stat_identity_handles_parentheses_and_pid_reuse(tmp_path):
    boot = tmp_path / 'sys/kernel/random/boot_id'
    boot.parent.mkdir(parents=True)
    boot.write_text('boot-one')
    process = tmp_path / '123'
    process.mkdir()
    fields = ['S', '12'] + ['0'] * 17 + ['9876']
    (process / 'stat').write_text('123 (worker (name) space) ' + ' '.join(fields))
    identity = registry.read_process(123, tmp_path).identity
    assert identity.start_ticks == 9876
    assert registry.read_process(123, tmp_path).parent_pid == 12
    assert registry.identity_status(identity, tmp_path) == 'alive'
    fields[-1] = '9877'
    (process / 'stat').write_text('123 (new process) ' + ' '.join(fields))
    assert registry.identity_status(identity, tmp_path) == 'exited'
    fields[-1] = '9876'
    (process / 'stat').write_text('123 (new process) ' + ' '.join(fields))
    boot.write_text('boot-two')
    assert registry.identity_status(identity, tmp_path) == 'exited'
    boot.unlink()
    assert registry.identity_status(identity, tmp_path) == 'unknown'


def test_presence_leases_and_jobs_share_one_process_record(inventory):
    first = InstancePresence(registry=inventory)
    second = InstancePresence(registry=inventory)
    first.update('テキスト', '編集中')
    second.heartbeat()
    with runtime_activity.runtime_activity('コピー中'):
        assert len(entries(inventory)) == 1
        assert entries(inventory)[0].screen == 'テキスト'
        assert entries(inventory)[0].activities == ('コピー中',)
        first.close()
        assert len(entries(inventory)) == 1
    second.close()
    assert entries(inventory) == ()
    assert not list(inventory.directory.glob('*.json'))


def test_concurrent_jobs_unregister_without_losing_other_jobs(inventory):
    presence = InstancePresence(registry=inventory)
    permanent = runtime_activity.begin_runtime_activity('長い作業')
    def work(number):
        with runtime_activity.runtime_activity(f'処理 {number}'):
            assert any(item.activities for item in entries(inventory))
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(work, range(24)))
    assert entries(inventory)[0].activities == ('長い作業',)
    permanent.close()
    presence.close()


def test_dead_identity_is_pruned_but_read_failure_is_not(inventory, monkeypatch):
    presence = InstancePresence(registry=inventory)
    stale = replace(inventory.identity, start_ticks=inventory.identity.start_ticks + 1)
    inventory._write(registry.ProcessInfo(stale, inventory.identity, inventory.root, 'worker', '古いプロセス'))
    assert len(entries(inventory)) == 1
    assert not inventory.path_for(stale).exists()
    monkeypatch.setattr(registry, 'read_process', lambda *args: (_ for _ in ()).throw(PermissionError()))
    assert entries(inventory)[0].health == 'unknown'
    assert presence.path.exists()
    presence.close()


def test_stale_gui_pulse_means_unconfirmed_not_exited(inventory):
    presence = InstancePresence(registry=inventory)
    item = entries(inventory)[0]
    inventory._write(replace(item, heartbeat=time.monotonic() - 30))
    assert entries(inventory)[0].health == 'unresponsive'
    assert presence.path.exists()
    presence.heartbeat()
    assert entries(inventory)[0].health == 'alive'
    presence.close()


def test_registration_is_private_and_does_not_store_command_arguments(inventory):
    presence = InstancePresence(registry=inventory)
    process = managed_process.popen([sys.executable, '-c', 'import time; time.sleep(10)', 'SECRET_TARGET'], label='テスト処理')
    try:
        rows = entries(inventory)
        child = next(row for row in rows if row.pid == process.pid)
        assert child.owner == inventory.identity
        assert child.label == 'テスト処理'
        raw = b''.join(path.read_bytes() for path in inventory.directory.glob('*.json'))
        assert b'SECRET_TARGET' not in raw
        assert b'time.sleep' not in raw
        assert inventory.directory.stat().st_mode & 0o777 == 0o700
        assert all(path.stat().st_mode & 0o777 == 0o600 for path in inventory.directory.glob('*.json'))
        os.kill(process.pid, signal.SIGSTOP)
        deadline = time.monotonic() + 2
        while registry.identity_status(child.identity) != 'stopped' and time.monotonic() < deadline:
            time.sleep(.01)
        assert next(row for row in entries(inventory) if row.pid == process.pid).health == 'stopped'
    finally:
        os.kill(process.pid, signal.SIGCONT)
        process.terminate()
        process.wait(timeout=5)
        inventory.observe()
        presence.close()
    assert not inventory.path_for(child.identity).exists()


def test_external_processes_are_never_registered_or_observed(inventory):
    presence = InstancePresence(registry=inventory)
    child = managed_process.popen([sys.executable, '-c', 'import time; time.sleep(10)'], label='外部アプリ', role='external', ownership='external')
    try:
        presence.close()
        inventory.observe()
        assert not any(row.pid == child.pid for row in entries(inventory))
        inventory.close()
        assert child.poll() is None
    finally:
        child.terminate()
        child.wait(timeout=5)


def test_old_external_record_is_removed_when_read(inventory):
    item = registry.ProcessInfo(
        inventory.identity, inventory.identity, inventory.root,
        'external', '古い外部アプリ', ownership='external',
    )
    path = inventory.path_for(item.identity)
    inventory._write(item)
    assert entries(inventory) == ()
    assert not path.exists()


def test_registry_failure_does_not_prevent_external_process_operation(inventory, tmp_path):
    inventory.directory = tmp_path / 'invalid'
    inventory.directory.write_text('not a directory')
    process = managed_process.popen([sys.executable, '-c', 'print("done")'], label='テスト', stdout=subprocess.PIPE, text=True)
    stdout, _ = process.communicate(timeout=5)
    assert stdout.strip() == 'done'
    assert inventory.error


def test_symlink_directory_is_rejected_without_writing_through_it(inventory, tmp_path):
    target = tmp_path / 'target'
    target.mkdir(mode=0o700)
    inventory.directory.symlink_to(target, target_is_directory=True)
    presence = InstancePresence(registry=inventory)
    assert inventory.error
    assert not list(target.iterdir())
    with pytest.raises(PermissionError):
        entries(inventory)
    presence.close()


def test_malformed_and_symlink_entries_do_not_break_listing(inventory, tmp_path):
    presence = InstancePresence(registry=inventory)
    (inventory.directory / 'bad.json').write_text('[]')
    (inventory.directory / 'broken.json').write_text('{')
    os.mkfifo(inventory.directory / 'fifo.json')
    target = tmp_path / 'target.json'
    target.write_text(presence.path.read_text())
    (inventory.directory / 'symlink.json').symlink_to(target)
    assert len(entries(inventory)) == 1
    presence.close()


def test_root_filter_keeps_other_installations_separate(inventory):
    presence = InstancePresence(registry=inventory)
    assert registry.list_processes(directory=inventory.directory, root=Path('/another/porta')) == ()
    assert len(registry.list_processes(directory=inventory.directory, root=None)) == 1
    presence.close()


def test_observer_discovers_unwrapped_child_processes(inventory):
    presence = InstancePresence(registry=inventory)
    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(10)'])
    try:
        inventory.observe()
        row = next(row for row in entries(inventory) if row.pid == child.pid)
        assert row.observed
        assert row.owner == inventory.identity
    finally:
        child.terminate()
        child.wait(timeout=5)
        inventory.observe()
        presence.close()


def test_self_registration_retains_parent_relationship(inventory):
    presence = InstancePresence(role='records', registry=inventory)
    parent = replace(inventory.identity, pid=99999999)
    item = registry.ProcessInfo(inventory.identity, parent, inventory.root, 'records', '起動要求', ownership='independent')
    value = {**registry.asdict(item), 'version': 1}
    (inventory.directory / f'{item.identity.key}-{parent.key}.json').write_text(json.dumps(value))
    row = entries(inventory)[0]
    assert row.screen == 'メインメニュー'
    assert row.role == 'records'
    assert row.owner == parent
    assert row.owner_health == 'exited'
    presence.close()


def test_abrupt_owner_exit_keeps_live_child_visible(tmp_path):
    runtime = tmp_path / 'login'
    runtime.mkdir(mode=0o700)
    environment = dict(os.environ, XDG_RUNTIME_DIR=str(runtime), PYTHONDONTWRITEBYTECODE='1')
    code = '''
import os, sys, subprocess
from foundation.instance_presence import InstancePresence
from foundation.managed_process import popen
presence = InstancePresence()
child = popen([sys.executable, '-c', 'import time; time.sleep(30)'], label='生存する子プロセス',
              stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
print(child.pid, flush=True)
os._exit(0)
'''
    parent = subprocess.run([sys.executable, '-c', code], env=environment, capture_output=True, text=True, timeout=10, check=True)
    child_pid = int(parent.stdout.strip())
    identity = registry.read_process(child_pid).identity
    try:
        rows = registry.list_processes(directory=runtime / 'porta-processes')
        child = next(item for item in rows if item.pid == child_pid)
        assert child.health == 'alive'
        assert child.owner_health == 'exited'
        assert child.owner != child.identity
        assert not any(item.pid == child.owner.pid for item in rows)
    finally:
        if registry.identity_status(identity) in {'alive', 'stopped'}:
            os.kill(child_pid, signal.SIGTERM)


def test_cli_lists_current_process_without_registering_itself(tmp_path):
    runtime = tmp_path / 'login'
    runtime.mkdir(mode=0o700)
    inventory = registry.ProcessRegistry(runtime / 'porta-processes', monitor=False)
    presence = InstancePresence(registry=inventory)
    environment = dict(os.environ, XDG_RUNTIME_DIR=str(runtime), PYTHONDONTWRITEBYTECODE='1')
    try:
        query = subprocess.Popen([sys.executable, str(registry.PORTA_ROOT / 'scripts/main.py'), '--list-processes'],
                                 env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            stdout, stderr = query.communicate(timeout=10)
        finally:
            if query.poll() is None:
                query.kill()
                query.wait(timeout=5)
        assert query.returncode == 0, stderr
        rows = json.loads(stdout)
        assert any(row['identity']['pid'] == os.getpid() and row['role'] == 'main' for row in rows)
        assert not any(row['identity']['pid'] == query.pid for row in rows)
    finally:
        presence.close()
        inventory.close()


@pytest.mark.parametrize('arguments, role', [([], 'main'), (['--record-center'], 'records')])
def test_real_gui_entrypoint_registers_once_and_cleans_up_after_exit(tmp_path, arguments, role):
    import tempfile
    runtime = tmp_path / 'login'
    runtime.mkdir(mode=0o700)
    # Qt's Unix socket path must remain short; this also isolates the live
    # correspondence service from the user's currently running PORTA.
    with tempfile.TemporaryDirectory(prefix='prt-ipc-', dir='/tmp') as ipc:
        environment = dict(os.environ, XDG_RUNTIME_DIR=str(runtime), TMPDIR=ipc,
                           QT_QPA_PLATFORM='offscreen', PYTHONDONTWRITEBYTECODE='1')
        child = subprocess.Popen([sys.executable, str(registry.PORTA_ROOT / 'scripts/main.py'), *arguments],
                                 env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        try:
            deadline = time.monotonic() + 10
            while True:
                rows = registry.list_processes(directory=runtime / 'porta-processes')
                own = [item for item in rows if item.pid == child.pid]
                if own and own[0].heartbeat > 0:
                    break
                assert child.poll() is None, child.stderr.read().decode()
                assert time.monotonic() < deadline, 'GUI registration timed out'
                time.sleep(.05)
            assert len(own) == 1
            assert own[0].role == role
            assert own[0].health == 'alive'
        finally:
            child.terminate()
            child.wait(timeout=5)
            child.stderr.close()
        assert not any(item.pid == child.pid for item in registry.list_processes(directory=runtime / 'porta-processes'))
