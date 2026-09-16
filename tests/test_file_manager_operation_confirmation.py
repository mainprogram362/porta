from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QEventLoop, QTimer

from apps.file_tools.file_manager import FileManagerScreen, has_independent_operation_windows


def test_normal_operation_starts_with_empty_destination_and_remembers_valid_input(tmp_path: Path):
    app = QApplication.instance() or QApplication([])
    source = tmp_path / "source.txt"
    destination = tmp_path / "destination"
    source.write_text("content", encoding="utf-8")
    destination.mkdir()
    screen = FileManagerScreen(lambda: None)
    try:
        screen.search_results_input.setPlainText(str(source))
        screen.show_operation_confirmation()
        first = next(iter(screen._operation_confirmation_dialogs))
        assert first.parentWidget() is None
        assert has_independent_operation_windows()
        assert first.destination_input.text() == ""
        assert not first.execute_button.isEnabled()

        first.destination_input.setText(str(destination))
        assert first.execute_button.isEnabled()
        assert screen._session_last_destination == str(destination)
        first.close()
        app.processEvents()

        screen.show_operation_confirmation()
        second = next(iter(screen._operation_confirmation_dialogs))
        assert second.destination_input.text() == str(destination)
    finally:
        for dialog in tuple(screen._operation_confirmation_dialogs):
            dialog.close()
        app.processEvents()
        screen.close()


def test_confirmation_window_is_the_only_place_that_executes_normal_copy(tmp_path: Path):
    app = QApplication.instance() or QApplication([])
    source = tmp_path / "source.txt"
    destination = tmp_path / "destination"
    source.write_text("content", encoding="utf-8")
    destination.mkdir()
    screen = FileManagerScreen(lambda: None)
    try:
        screen.search_results_input.setPlainText(str(source))
        assert screen.copy_button.text() == "プレビュー・実行…"
        screen.copy_button.click()
        dialog = next(iter(screen._operation_confirmation_dialogs))
        assert not (destination / source.name).exists()

        dialog.destination_input.setText(str(destination))
        dialog.execute_button.click()
        loop = QEventLoop()
        timeout = QTimer()
        timeout.setSingleShot(True)
        timeout.timeout.connect(loop.quit)
        dialog._worker.finished.connect(loop.quit)
        timeout.start(10000)
        loop.exec()
        timeout.stop()
        assert dialog._worker is None
        assert (destination / source.name).read_text(encoding="utf-8") == "content"
        assert "コピー完了" in dialog.status_label.text()
        assert "実行結果" in dialog.preview_text.toPlainText()
    finally:
        for dialog in tuple(screen._operation_confirmation_dialogs):
            dialog.close()
        app.processEvents()
        screen.close()


def test_one_to_one_destination_is_set_on_main_screen_and_frozen_in_confirmation(tmp_path: Path):
    app = QApplication.instance() or QApplication([])
    source = tmp_path / "source.txt"
    first_destination = tmp_path / "first"
    second_destination = tmp_path / "second"
    source.write_text("content", encoding="utf-8")
    first_destination.mkdir()
    second_destination.mkdir()
    screen = FileManagerScreen(lambda: None)
    try:
        screen.search_results_input.setPlainText(str(source))
        screen.copy_mode_combo.setCurrentIndex(1)
        screen.destination_input.setPlainText(str(first_destination))
        screen.show_operation_confirmation()
        dialog = next(iter(screen._operation_confirmation_dialogs))

        assert dialog.destination_input.isHidden()
        assert dialog.execute_button.isEnabled()
        assert str(first_destination / source.name) in dialog.preview_text.toPlainText()

        screen.destination_input.setPlainText(str(second_destination))
        assert str(first_destination / source.name) in dialog.preview_text.toPlainText()
        assert str(second_destination / source.name) not in dialog.preview_text.toPlainText()
    finally:
        for dialog in tuple(screen._operation_confirmation_dialogs):
            dialog.close()
        app.processEvents()
        screen.close()
