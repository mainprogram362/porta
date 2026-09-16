import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import sys
from pathlib import Path
from dataclasses import replace

from PySide6.QtCore import QProcess, Qt
from PySide6.QtWidgets import QApplication
import pytest

from foundation import process_registry
from foundation.instance_presence import InstancePresence
from gui.process_tracking import PresenceHeartbeat, track_qprocess
from apps.launcher import process_center


@pytest.fixture
def inventory(tmp_path, monkeypatch):
    QApplication.instance() or QApplication([])
    registry = process_registry.ProcessRegistry(tmp_path / 'runtime', monitor=False)
    monkeypatch.setattr(process_registry, '_default', registry)
    try:
        yield registry
    finally:
        registry.close()


def test_qprocess_success_and_failure_have_correct_registration(inventory):
    process = QProcess()
    track_qprocess(process, 'テスト実行')
    process.start(sys.executable, ['-c', 'import time; time.sleep(10)'])
    try:
        assert process.waitForStarted(3000)
        pid = int(process.processId())
        rows = process_registry.list_processes(directory=inventory.directory)
        assert any(item.pid == pid and item.label == 'テスト実行' for item in rows)
    finally:
        process.kill()
        assert process.waitForFinished(3000)
        inventory.observe()
    assert not any(item.pid == pid for item in process_registry.list_processes(directory=inventory.directory))
    failed = QProcess()
    track_qprocess(failed, '起動失敗')
    failed.start('/definitely-missing-porta-executable')
    assert not failed.waitForStarted(3000)
    assert not any(item.label == '起動失敗' for item in process_registry.list_processes(directory=inventory.directory))


def test_inventory_refresh_retains_selection_and_stops_when_hidden(inventory, monkeypatch):
    presence = InstancePresence(registry=inventory)
    heartbeat = PresenceHeartbeat(presence, QApplication.instance())
    rows = list(process_registry.list_processes(directory=inventory.directory))
    monkeypatch.setattr(process_center, 'process_inventory', lambda **kwargs: tuple(rows))
    dialog = process_center.ProcessCenterDialog()
    try:
        dialog.show()
        assert dialog._timer.isActive()
        dialog.tree.setCurrentItem(dialog.tree.topLevelItem(0))
        rows[0] = replace(rows[0], activities=('コピー中',))
        dialog.refresh()
        assert dialog.tree.currentItem().data(0, Qt.ItemDataRole.UserRole) == inventory.identity.key
        assert dialog.tree.currentItem().text(4) == 'コピー中'
        dialog.hide()
        assert not dialog._timer.isActive()
        assert presence.path.exists()
    finally:
        dialog.close()
        heartbeat.close()
        heartbeat.deleteLater()
    assert not presence.path.exists()
