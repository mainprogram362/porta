"""Keep Qt windows and deferred deletion isolated between tests."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication, QWidget
from shiboken6 import isValid


@pytest.fixture(autouse=True)
def no_live_manager_discovery(monkeypatch):
    """GUI unit tests must not contact other running PORTA instances."""
    from apps.launcher.work_center import WorkCenter
    monkeypatch.setattr(WorkCenter, "refresh", lambda self: None)


@pytest.fixture
def work_host_factory(monkeypatch):
    """Construct actual work windows with a simulated IPC server, no child launch."""
    from apps.launcher import work_window
    from apps.system_tools.environment_check.window import EnvironmentCheckScreen

    class Server:
        def __init__(self, *args, **kwargs):
            pass
        def start_or_forward(self, _):
            return True
        def set_handler(self, handler):
            self.handler = handler
        def close(self):
            pass

    monkeypatch.setattr(work_window, "MainInstance", Server)
    monkeypatch.setattr(EnvironmentCheckScreen, "refresh", lambda self: None)
    hosts = []
    def create(key, **payload):
        host = work_window.WorkWindow({"version": 1, "app": key, **payload})
        hosts.append(host)
        return host
    yield create
    for host in hosts:
        host.timer.stop()
        host.server.close()
        host.heartbeat.close()
        shutdown = getattr(host.work_screen, "shutdown", None)
        if callable(shutdown):
            shutdown()
        host.hide()


@pytest.fixture(autouse=True)
def release_test_windows():
    app = QApplication.instance()
    previous = set(app.topLevelWidgets()) if app is not None else set()
    yield
    app = QApplication.instance()
    if app is None:
        return
    # close() generally just hides a QWidget. Keeping hidden test windows
    # alive leaks layouts, callbacks and posted events into later font tests.
    windows = set(app.topLevelWidgets()) - previous
    for window in windows:
        if isValid(window):
            # A parent can be torn down by the fixture even when its normal
            # closeEvent correctly refuses to close while a child tab owns a
            # worker.  Give every owned screen its explicit shutdown hook
            # before Qt destroys child QThreads.
            for owner in [window, *window.findChildren(QWidget)]:
                shutdown = getattr(owner, "shutdown", None)
                if callable(shutdown):
                    shutdown()
            window.hide()
            window.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
