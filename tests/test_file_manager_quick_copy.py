from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtWidgets import QApplication, QMenu

from apps.file_tools.file_manager import FileManagerScreen
from apps.file_tools.file_manager.copy_workflow import build_simple_copy_preview
from apps.file_tools.file_manager.quick_copy_dialog import CooperativeCopyThread, QuickCopyDialog


def test_file_manager_context_menu_offers_one_and_checked_real_copy(tmp_path: Path) -> None:
    QApplication.instance() or QApplication([])
    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    first.write_text("first", encoding="utf-8")
    second.write_text("second", encoding="utf-8")
    destination = tmp_path / "destination"
    destination.mkdir()
    screen = FileManagerScreen(lambda: None)
    try:
        screen.search_results_input.setPlainText(f"{first}\n{second}")
        screen._session_last_destination = str(destination)
        row = screen.search_results_input._tree.topLevelItem(0)
        menu = QMenu()
        screen._add_path_list_context_actions(menu, row, screen.search_results_input)

        real_menu = next(action.menu() for action in menu.actions() if action.text() == "実体操作")
        assert real_menu is not None
        labels = [action.text() for action in real_menu.actions()]
        assert f"この1件のみをコピー（{first.name}）…" in labels
        assert "チェック済み2件すべてをコピー…" in labels

        next(
            action for action in real_menu.actions() if action.text().startswith("この1件のみ")
        ).trigger()
        dialog = next(iter(screen._quick_copy_dialogs))
        assert dialog._sources == (first.resolve(),)
        assert dialog.destination_input.text() == str(destination)
        dialog.close()
    finally:
        for dialog in tuple(screen._quick_copy_dialogs):
            dialog.close()
        screen.close()


def test_quick_copy_window_freezes_sources_and_destination_from_parent(tmp_path: Path) -> None:
    QApplication.instance() or QApplication([])
    source = tmp_path / "source.txt"
    source.write_text("content", encoding="utf-8")
    first_destination = tmp_path / "first"
    second_destination = tmp_path / "second"
    first_destination.mkdir()
    second_destination.mkdir()
    screen = FileManagerScreen(lambda: None)
    try:
        screen._session_last_destination = str(first_destination)
        screen._open_quick_copy_dialog((source,))
        dialog = next(iter(screen._quick_copy_dialogs))

        screen._session_last_destination = str(second_destination)

        assert dialog._sources == (source,)
        assert dialog.destination_input.text() == str(first_destination)
        assert str(first_destination / source.name) in dialog.preview_text.toPlainText()
        dialog.close()
    finally:
        for dialog in tuple(screen._quick_copy_dialogs):
            dialog.close()
        screen.close()


def test_quick_copy_worker_pauses_at_file_boundary_and_resumes(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    first.write_text("first", encoding="utf-8")
    second.write_text("second", encoding="utf-8")
    destination = tmp_path / "destination"
    destination.mkdir()
    preview = build_simple_copy_preview(f"{first}\n{second}", str(destination))
    assert preview.plan is not None
    worker = CooperativeCopyThread(preview.plan)
    paused: list[bool] = []
    worker.paused_changed.connect(paused.append)
    worker.request_pause_at_next_file()
    worker.start()
    deadline = time.monotonic() + 5
    while True not in paused and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.005)

    assert True in paused
    assert (destination / first.name).is_file()
    assert not (destination / second.name).exists()

    worker.request_resume()
    assert worker.wait(5000)
    app.processEvents()
    assert (destination / second.name).read_text(encoding="utf-8") == "second"


def test_quick_copy_worker_cancellation_leaves_no_output(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    source = tmp_path / "source.bin"
    source.write_bytes(b"x" * 1024)
    destination = tmp_path / "destination"
    destination.mkdir()
    preview = build_simple_copy_preview(str(source), str(destination))
    assert preview.plan is not None
    worker = CooperativeCopyThread(preview.plan)
    cancelled: list[bool] = []
    worker.cancelled.connect(lambda: cancelled.append(True))
    worker.request_cancel()
    worker.start()

    assert worker.wait(5000)
    app.processEvents()
    assert cancelled == [True]
    assert list(destination.iterdir()) == []


def test_quick_copy_worker_copies_folder_tree_and_preserves_links(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    source = tmp_path / "source"
    nested = source / "nested"
    nested.mkdir(parents=True)
    (nested / "content.txt").write_text("content", encoding="utf-8")
    (source / "link.txt").symlink_to(Path("nested/content.txt"))
    destination = tmp_path / "destination"
    destination.mkdir()
    preview = build_simple_copy_preview(str(source), str(destination))
    assert preview.plan is not None
    worker = CooperativeCopyThread(preview.plan)
    worker.start()

    assert worker.wait(5000)
    app.processEvents()
    output = destination / source.name
    assert (output / "nested/content.txt").read_text(encoding="utf-8") == "content"
    assert (output / "link.txt").is_symlink()
    assert (output / "link.txt").readlink() == Path("nested/content.txt")


def test_quick_copy_worker_never_removes_an_output_occupied_after_preview(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    source = tmp_path / "source.txt"
    source.write_text("source", encoding="utf-8")
    destination = tmp_path / "destination"
    destination.mkdir()
    preview = build_simple_copy_preview(str(source), str(destination))
    assert preview.plan is not None
    occupied = destination / source.name
    occupied.write_text("external", encoding="utf-8")
    worker = CooperativeCopyThread(preview.plan)
    failures: list[str] = []
    worker.failed.connect(failures.append)
    worker.start()

    assert worker.wait(5000)
    app.processEvents()
    assert failures and "使用されました" in failures[0]
    assert occupied.read_text(encoding="utf-8") == "external"


def test_quick_copy_dialog_executes_copy_and_reports_result(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    source = tmp_path / "source.txt"
    source.write_text("content", encoding="utf-8")
    destination = tmp_path / "destination"
    destination.mkdir()
    dialog = QuickCopyDialog((source,), str(destination))
    try:
        dialog.start_copy()
        worker = dialog._worker
        assert worker is not None and worker.wait(5000)
        app.processEvents()

        assert (destination / source.name).read_text(encoding="utf-8") == "content"
        assert dialog.stage_label.text() == "コピー完了：1件"
        assert "コピー結果" in dialog.preview_text.toPlainText()
        assert dialog.close_button.isEnabled()
    finally:
        dialog.close()
