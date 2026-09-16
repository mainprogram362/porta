import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QThread, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QInputDialog, QMessageBox

from apps.file_tools.file_manager import operation_confirmation_dialog as confirmation
from apps.launcher.catalog import app_for_key
from apps.launcher.main_menu import MainMenuWindow
from foundation.operation_progress import completed, checkpoint


def make_dialog(tmp_path):
    source = tmp_path / "source"
    source.write_text("content")
    destination = tmp_path / "destination"
    destination.mkdir()
    return confirmation.OperationConfirmationDialog(
        kind="copy", targets=(source,), mode="simple", destinations=(),
        rename_rules=(), include_extension=False,
        initial_destination=str(destination), registered_paths=[])


def finish(dialog):
    for _ in range(500):
        if dialog._worker is None:
            return
        QTest.qWait(10)
    raise AssertionError("operation did not finish")


def test_normal_operation_keeps_gui_responsive_and_rejects_duplicate_execution(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    dialog = make_dialog(tmp_path)
    ticks, calls = [], []
    timer = QTimer()
    timer.setInterval(10)
    timer.timeout.connect(lambda: ticks.append(1))

    def execute(_):
        calls.append(QThread.currentThread() != app.thread())
        time.sleep(.12)
        return []

    monkeypatch.setattr(confirmation, "execute_operation", execute)
    try:
        timer.start()
        dialog.execute_current()
        dialog.execute_current()
        finish(dialog)
        assert calls == [True]
        assert len(ticks) >= 2
    finally:
        timer.stop()
        finish(dialog)
        dialog.close()


def test_failure_reports_completed_stages_and_disables_blind_retry(tmp_path, monkeypatch):
    QApplication.instance() or QApplication([])
    dialog = make_dialog(tmp_path)

    def execute(_):
        completed("source A", "output A", "コピー完了")
        raise OSError("second item failed")

    monkeypatch.setattr(confirmation, "execute_operation", execute)
    try:
        dialog.execute_current()
        finish(dialog)
        assert "一部完了" in dialog.status_label.text()
        assert "output A" in dialog.preview_text.toPlainText()
        assert "second item failed" in dialog.preview_text.toPlainText()
        assert not dialog.execute_button.isEnabled()
    finally:
        finish(dialog)
        dialog.close()


def test_closing_running_dialog_requests_cancel_and_waits(tmp_path, monkeypatch):
    QApplication.instance() or QApplication([])
    dialog = make_dialog(tmp_path)

    def execute(_):
        for _ in range(100):
            time.sleep(.01)
            checkpoint()
        return []

    monkeypatch.setattr(confirmation, "execute_operation", execute)
    try:
        dialog.show()
        dialog.execute_current()
        assert not dialog.close()
        finish(dialog)
        assert "取消" in dialog.status_label.text()
        assert dialog.close()
    finally:
        finish(dialog)
        dialog.close()


def test_work_window_waits_for_its_task_without_discarding_another_work(monkeypatch, work_host_factory):
    QApplication.instance() or QApplication([])
    first = work_host_factory("text_workbench")
    second = work_host_factory("text_workbench")
    first.work_screen.text_editor.setPlainText("retain first")
    first.show()
    second.show()
    class Task(QThread):
        def run(self):
            self.msleep(150)
    task = Task(second.work_screen)
    monkeypatch.setattr(QMessageBox, "information", lambda *args: None)
    try:
        task.start()
        assert not second.close()
        assert first.work_screen.text_editor.toPlainText() == "retain first"
        task.wait()
        monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.StandardButton.Yes)
        assert second.close()
        assert first.isVisible()
        assert first.work_screen.text_editor.toPlainText() == "retain first"
    finally:
        task.wait()
        first.hide()
        second.hide()


def test_work_name_and_keep_mark_are_independent(monkeypatch, work_host_factory):
    QApplication.instance() or QApplication([])
    first = work_host_factory("text_workbench")
    second = work_host_factory("text_workbench")
    monkeypatch.setattr(QInputDialog, "getText", lambda *args, **kwargs: ("資料の整理", True))
    first.rename()
    first.keep_button.setChecked(True)
    state = first.command({"op": "status"})
    assert state["title"] == "資料の整理"
    assert state["kept"] is True
    assert second.command({"op": "status"})["kept"] is False
    assert first.work_id != second.work_id
