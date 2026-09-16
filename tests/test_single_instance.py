"""Exercise real IPC, simultaneous starts, crash recovery and GUI delivery."""
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import time

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

from foundation.single_instance import MainInstance, InstanceConnectionError


PLAIN = dict(version=1, target=None, paths=[], record_id=None, record_output=None)
ROOT = Path(__file__).resolve().parents[1]
WORKER = """
import json, sys
from pathlib import Path
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from foundation.single_instance import MainInstance
app = QApplication([])
owner = MainInstance(Path(sys.argv[2]), directory=Path(sys.argv[1]))
try:
    primary = owner.start_or_forward(json.loads(sys.argv[3]))
    print('primary' if primary else 'forwarded', flush=True)
    if primary:
        owner.set_handler(lambda request: print(json.dumps(request), flush=True))
        QTimer.singleShot(int(sys.argv[4]), app.quit)
        app.exec()
finally:
    owner.close()
"""


@pytest.fixture
def ipc():
    QApplication.instance() or QApplication([])
    # Socket names must stay below Linux's Unix-domain path length limit.
    with tempfile.TemporaryDirectory(prefix="prt-single-", dir="/tmp") as directory:
        yield Path(directory)


@pytest.fixture(autouse=True)
def no_manager_polling(monkeypatch):
    from apps.launcher.work_center import WorkCenter
    monkeypatch.setattr(WorkCenter, "refresh", lambda self: None)


def worker(ipc, root=ROOT, request=None, lifetime=1500):
    return subprocess.Popen(
        [sys.executable, "-c", WORKER, str(ipc), str(root), json.dumps(request or PLAIN), str(lifetime)],
        env=dict(os.environ, QT_QPA_PLATFORM="offscreen", PYTHONDONTWRITEBYTECODE="1"),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )


def finish(child, *, pump=False):
    try:
        deadline = time.monotonic() + 12
        while child.poll() is None:
            assert time.monotonic() < deadline, "child did not finish"
            if pump:
                QTest.qWait(10)
            else:
                time.sleep(.01)
        output, error = child.communicate(timeout=2)
        assert child.returncode == 0, error
        return output.splitlines()
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)


def test_simultaneous_launches_elect_one_owner_and_deliver_once(ipc):
    children = [worker(ipc) for _ in range(3)]
    try:
        outputs = [finish(child) for child in children]
        assert sorted(lines[0] for lines in outputs) == ["forwarded", "forwarded", "primary"]
        owner = next(lines for lines in outputs if lines[0] == "primary")
        assert [json.loads(line) for line in owner[1:]] == [PLAIN, PLAIN]
        assert all(path.read_bytes() == b"" for path in ipc.glob("*.lock"))
        assert not list(ipc.glob("*.sock"))
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=5)


def test_crash_releases_lock_and_stale_socket_is_recovered(ipc):
    child = worker(ipc, lifetime=30000)
    try:
        # Polling the socket avoids a blocking readline if startup fails.
        deadline = time.monotonic() + 8
        while not list(ipc.glob("*.sock")):
            assert child.poll() is None
            assert time.monotonic() < deadline
            time.sleep(.01)
        child.kill()
        child.wait(timeout=5)
        assert list(ipc.glob("*.sock"))
        replacement = MainInstance(ROOT, directory=ipc)
        try:
            assert replacement.start_or_forward(PLAIN)
            assert stat.S_IMODE(replacement.lock_path.stat().st_mode) == 0o600
            assert stat.S_IMODE(Path(replacement.endpoint).stat().st_mode) & 0o077 == 0
        finally:
            replacement.close()
        assert not list(ipc.glob("*.sock"))
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)
        child.communicate(timeout=2)


def test_owner_with_no_event_loop_is_not_replaced_on_timeout(ipc):
    owner = MainInstance(ROOT, directory=ipc)
    secondary = MainInstance(ROOT, directory=ipc)
    third = MainInstance(ROOT, directory=ipc)
    try:
        assert owner.start_or_forward(PLAIN)
        endpoint = Path(owner.endpoint).stat().st_ino
        with pytest.raises(InstanceConnectionError):
            secondary.start_or_forward(PLAIN, timeout_ms=80)
        with pytest.raises(InstanceConnectionError):
            third.start_or_forward(PLAIN, timeout_ms=80)
        assert owner.server.isListening()
        assert Path(owner.endpoint).stat().st_ino == endpoint
    finally:
        owner.close()
        secondary.close()
        third.close()


def test_malformed_message_is_rejected_and_fragmented_message_is_delivered_once(ipc):
    owner = MainInstance(ROOT, directory=ipc)
    received = []
    client = """
import json, socket, sys, time
with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as peer:
    peer.settimeout(3)
    peer.connect(sys.argv[1])
    for part in json.loads(sys.argv[2]):
        peer.sendall(part.encode())
        time.sleep(.015)
    response = b''
    while b'\\n' not in response:
        part = peer.recv(4096)
        if not part:
            raise RuntimeError('missing acknowledgement')
        response += part
    print(response.decode().strip())
"""
    try:
        assert owner.start_or_forward(PLAIN)
        owner.set_handler(received.append)
        raw = json.dumps(PLAIN) + "\n"
        for parts, expected in [(["{invalid}\n"], False),
                                ([json.dumps(dict(PLAIN, command="arbitrary")) + "\n"], False),
                                ([raw[:12], raw[12:-1], "\n"], True)]:
            child = subprocess.Popen([sys.executable, "-c", client, owner.endpoint, json.dumps(parts)],
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            output = finish(child, pump=True)
            assert json.loads(output[0]) == {"ok": expected}
        assert received == [PLAIN]
    finally:
        owner.close()


def test_installation_identity_resolves_symlinks_and_separates_copies(ipc, tmp_path):
    root = tmp_path / "porta"
    root.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(root, target_is_directory=True)
    first = MainInstance(root, directory=ipc)
    same = MainInstance(alias, directory=ipc)
    other = MainInstance(tmp_path / "other", directory=ipc)
    try:
        assert first.endpoint == same.endpoint
        assert first.start_or_forward(PLAIN)
        assert other.start_or_forward(PLAIN)
        assert first.endpoint != other.endpoint
    finally:
        for instance in (first, same, other):
            instance.close()


@pytest.mark.parametrize("kind", ["symlink", "fifo", "permissions"])
def test_unsafe_lock_is_rejected_without_touching_target(ipc, kind):
    instance = MainInstance(ROOT, directory=ipc)
    target = ipc / "untouched"
    target.write_text("private content")
    if kind == "symlink":
        instance.lock_path.symlink_to(target)
    elif kind == "fifo":
        os.mkfifo(instance.lock_path, mode=0o600)
    else:
        instance.lock_path.touch(mode=0o644)
        instance.lock_path.chmod(0o644)
    with pytest.raises(InstanceConnectionError):
        instance.start_or_forward(PLAIN)
    assert target.read_text() == "private content"
    assert not instance.server.isListening()



def test_forwarded_activation_shows_menu_and_restores_minimized_window(ipc):
    from apps.launcher.main_menu import MainMenuWindow
    from apps.launcher.launch_requests import LaunchRequests
    window = MainMenuWindow()
    owner = MainInstance(ROOT, directory=ipc)
    try:
        window.show_category("media_tools")
        count = window._screens.count()
        window.showMinimized()
        router = LaunchRequests(window)
        assert owner.start_or_forward(PLAIN)
        owner.set_handler(router.submit)
        assert finish(worker(ipc), pump=True) == ["forwarded"]
        assert window._screens.currentWidget() is window._menu_screen
        assert window._screens.count() == count
        assert not window.isMinimized()
        window.showMaximized()
        router.submit(PLAIN)
        assert window.windowState() & Qt.WindowState.WindowMaximized
    finally:
        owner.close()
        window.close()


def test_modal_dialog_queues_forwarded_paths_for_detached_work(ipc, tmp_path, monkeypatch):
    from apps.launcher.main_menu import MainMenuWindow
    from apps.launcher.launch_requests import LaunchRequests
    received = []
    source = tmp_path / "外部ファイル.txt"
    source.write_text("content")
    window = MainMenuWindow()
    monkeypatch.setattr(window, "_open_local_work", lambda definition, **kwargs: received.append((definition, kwargs)))
    owner = MainInstance(ROOT, directory=ipc)
    dialog = QDialog(window)
    try:
        window.show_category("file_tools")
        previous = window._screens.currentWidget()
        router = LaunchRequests(window)
        assert owner.start_or_forward(PLAIN)
        owner.set_handler(router.submit)
        dialog.setModal(True)
        dialog.show()
        QTest.qWait(10)
        request = dict(PLAIN, target="file-manager", paths=[str(source)])
        assert finish(worker(ipc, request=request), pump=True) == ["forwarded"]
        router._drain()
        assert not received
        dialog.accept()
        router._drain()
        assert len(received) == 1
        assert received[0][0].key == "file_manager"
        assert received[0][1]["paths"] == (source,)
        assert received[0][1]["action"] == "browse"
        assert window._screens.currentWidget() is previous
        assert all(path.read_bytes() == b"" for path in ipc.glob("*.lock"))
    finally:
        dialog.close()
        owner.close()
        window.close()

def test_disappeared_forwarded_path_does_not_create_screen(ipc, tmp_path, monkeypatch):
    from apps.launcher.main_menu import MainMenuWindow
    from apps.launcher.launch_requests import LaunchRequests

    window = MainMenuWindow()
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args))
    try:
        original = window._screens.currentWidget()
        router = LaunchRequests(window)
        router.submit(dict(PLAIN, target="file-manager", paths=[str(tmp_path / "missing")]))
        router._drain()
        assert warnings
        assert window._screens.currentWidget() is original
    finally:
        window.close()



def test_record_output_and_record_id_open_local_work(ipc, monkeypatch):
    from apps.launcher.main_menu import MainMenuWindow
    from apps.launcher.launch_requests import LaunchRequests
    from foundation import record_service
    received = []
    monkeypatch.setattr(record_service, "request", lambda _: {"ok": True, "text": "対応表の出力"})
    window = MainMenuWindow()
    monkeypatch.setattr(window, "_open_local_work",
                        lambda definition, **kwargs: received.append({"app": definition.key, **kwargs}))
    try:
        previous = window._screens.currentWidget()
        router = LaunchRequests(window)
        router.submit(dict(PLAIN, record_output="OUTPUT"))
        router._drain()
        router.submit(dict(PLAIN, record_id="TABLE"))
        router._drain()
        assert received == [{"app": "text_workbench", "text": "対応表の出力"},
                            {"app": "file_manager", "record_id": "TABLE"}]
        assert window._screens.currentWidget() is previous
    finally:
        window.close()

def test_long_editor_temp_path_uses_short_socket_location(monkeypatch, tmp_path):
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path / ("x" * 100)))
    instance = MainInstance(ROOT)
    assert instance.directory == Path("/tmp") / f"porta-main-{os.getuid()}"
    assert len(os.fsencode(instance.endpoint)) < 104
    instance.close()


def test_real_entrypoint_reuses_pid_and_forwards_relative_external_path(ipc):
    from foundation.process_registry import list_processes

    runtime = ipc / "login"
    runtime.mkdir(mode=0o700)
    environment = dict(os.environ, TMPDIR=str(ipc), XDG_RUNTIME_DIR=str(runtime),
                       QT_QPA_PLATFORM="offscreen", PYTHONDONTWRITEBYTECODE="1")
    command = [sys.executable, str(ROOT / "scripts/main.py")]
    # Observe the receiving tab inside the existing window process.
    observed = ipc / "received-tab.json"
    observer = """
import json, runpy, sys
from pathlib import Path
root, report = Path(sys.argv[1]), Path(sys.argv[2])
sys.path.insert(0, str(root / 'src'))
from apps.launcher.main_menu import MainMenuWindow
original = MainMenuWindow._open_local_work
def observe(self, definition, **kwargs):
    screen = original(self, definition, **kwargs)
    if definition.key == 'file_manager':
        temporary = report.with_suffix('.tmp')
        temporary.write_text(json.dumps({
            'paths': screen.search_results_input.toPlainText().splitlines(),
            'selected': self._work_tabs.stack.currentWidget() is screen,
            'same_window': screen.window() is self,
        }))
        temporary.replace(report)
    return screen
MainMenuWindow._open_local_work = observe
sys.argv = [str(root / 'scripts/main.py')]
runpy.run_path(sys.argv[0], run_name='__main__')
"""
    child = subprocess.Popen([sys.executable, "-c", observer, str(ROOT), str(observed)],
                             env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 10
        while True:
            rows = list_processes(directory=runtime / "porta-processes")
            if any(item.pid == child.pid and item.screen == "メインメニュー" for item in rows):
                break
            assert child.poll() is None
            assert time.monotonic() < deadline
            time.sleep(.025)
        for args in ([], ["--external-open", "file-manager", "relative.txt"]):
            (ipc / "relative.txt").write_text("input")
            result = subprocess.run(command + args, env=environment, cwd=ipc,
                                    capture_output=True, timeout=10)
            assert result.returncode == 0, result.stderr.decode()
        deadline = time.monotonic() + 5
        while True:
            rows = list_processes(directory=runtime / "porta-processes")
            mains = [item for item in rows if item.role == "main"]
            assert [item.pid for item in mains] == [child.pid]
            if observed.exists():
                result = json.loads(observed.read_text())
                assert str(ipc / "relative.txt") in result["paths"]
                assert result["selected"] and result["same_window"]
                assert not any(item.role == "work" for item in rows)
                break
            assert time.monotonic() < deadline, mains
            time.sleep(.025)
    finally:
        child.terminate()
        child.wait(timeout=5)
        child.stderr.close()
