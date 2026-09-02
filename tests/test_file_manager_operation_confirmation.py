from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from apps.file_tools.file_manager import FileManagerScreen


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
        screen.close()


def test_confirmation_window_is_the_only_place_that_executes_normal_copy(tmp_path: Path):
    QApplication.instance() or QApplication([])
    source = tmp_path / "source.txt"
    destination = tmp_path / "destination"
    source.write_text("content", encoding="utf-8")
    destination.mkdir()
    screen = FileManagerScreen(lambda: None)
    try:
        screen.search_results_input.setPlainText(str(source))
        assert screen.copy_button.text() == "実行内容を確認…"
        screen.copy_button.click()
        dialog = next(iter(screen._operation_confirmation_dialogs))
        assert not (destination / source.name).exists()

        dialog.destination_input.setText(str(destination))
        dialog.execute_button.click()
        assert (destination / source.name).read_text(encoding="utf-8") == "content"
        assert screen.results_button.isEnabled()
        assert "コピー完了" in screen._execution_results[-1]
    finally:
        screen.close()


def test_one_to_one_destination_is_set_on_main_screen_and_frozen_in_confirmation(tmp_path: Path):
    QApplication.instance() or QApplication([])
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
        screen.close()
