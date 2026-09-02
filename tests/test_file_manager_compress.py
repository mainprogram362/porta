from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QApplication, QMenu

from apps.file_tools.file_manager.archive_backends import (
    ArchivePasswordRequired,
    select_archive_backend,
)
from apps.file_tools.file_manager.compress_workflow import build_compression_preview
from apps.file_tools.file_manager.quick_compress_dialog import (
    CooperativeCompressThread,
    QuickCompressDialog,
)
from apps.file_tools.file_manager.window import FileManagerScreen


def _ready_preview(
    sources: tuple[Path, ...],
    destination: Path,
    *,
    archive_format: str = "7z",
    archive_name: str = "archive",
    individual: bool = False,
    in_place: bool = False,
):
    preview = build_compression_preview(
        sources,
        str(destination),
        archive_format=archive_format,
        archive_name=archive_name,
        compression_level=3,
        password_enabled=True,
        password="shared-password",
        hide_names=archive_format == "7z",
        individual=individual,
        in_place=in_place,
    )
    assert preview.plan is not None
    return preview


def test_compression_dialog_uses_requested_simple_defaults(tmp_path: Path) -> None:
    QApplication.instance() or QApplication([])
    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    first.write_text("first", encoding="utf-8")
    second.write_text("second", encoding="utf-8")
    dialog = QuickCompressDialog((first, second))
    try:
        assert dialog.format_combo.currentData() == "7z"
        assert dialog.level_combo.currentData() == 3
        assert dialog.password_enabled.isChecked()
        assert dialog.hide_names.isChecked()
        assert not dialog.advanced_group.isChecked()
        assert dialog.destination_input.text() == str(first.parent)
        assert not dialog.start_button.isEnabled()
        assert "共通パスワードを入力" in dialog.preview_text.toPlainText()

        dialog.password_input.setText("shared-password")

        assert dialog.start_button.isEnabled()
        assert dialog._plan is not None
        assert len(dialog._plan.archives) == 1
        assert dialog._plan.archives[0].sources == (first, second)
    finally:
        dialog.close()


def test_zip_mode_explains_that_names_are_visible(tmp_path: Path) -> None:
    QApplication.instance() or QApplication([])
    source = tmp_path / "source.txt"
    source.write_text("source", encoding="utf-8")
    dialog = QuickCompressDialog((source,))
    try:
        dialog.password_input.setText("shared-password")
        dialog.format_combo.setCurrentIndex(1)

        assert dialog.format_combo.currentData() == "zip"
        assert not dialog.hide_names.isChecked()
        assert not dialog.hide_names.isEnabled()
        assert "ファイル名とフォルダ構成は隠せません" in dialog.encryption_notice.text()
        assert dialog._plan is not None and dialog._plan.archive_format == "zip"
    finally:
        dialog.close()


def test_individual_in_place_mode_clears_and_disables_destination(tmp_path: Path) -> None:
    QApplication.instance() or QApplication([])
    first_directory = tmp_path / "first"
    second_directory = tmp_path / "second"
    first_directory.mkdir()
    second_directory.mkdir()
    first = first_directory / "one.txt"
    second = second_directory / "two.txt"
    first.write_text("one", encoding="utf-8")
    second.write_text("two", encoding="utf-8")
    dialog = QuickCompressDialog((first, second))
    try:
        dialog.password_input.setText("shared-password")
        dialog.advanced_group.setChecked(True)
        dialog.individual_mode.setChecked(True)
        assert dialog.in_place_mode.isEnabled()
        dialog.in_place_mode.setChecked(True)

        assert dialog.destination_input.text() == ""
        assert not dialog.destination_input.isEnabled()
        assert "その場で圧縮モード" in dialog.destination_notice.text()
        assert dialog._plan is not None
        assert [item.destination for item in dialog._plan.archives] == [
            first_directory,
            second_directory,
        ]
        assert [item.output.name for item in dialog._plan.archives] == [
            "one.txt.7z",
            "two.txt.7z",
        ]

        dialog.in_place_mode.setChecked(False)
        assert dialog.destination_input.isEnabled()
        assert dialog.destination_input.text() == str(first_directory)
    finally:
        dialog.close()


def test_compress_worker_creates_verified_header_encrypted_7z(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    folder = tmp_path / "folder"
    folder.mkdir()
    (folder / "content.txt").write_text("content", encoding="utf-8")
    destination = tmp_path / "destination"
    destination.mkdir()
    preview = _ready_preview((folder,), destination, archive_name="secret")
    assert preview.plan is not None
    worker = CooperativeCompressThread(preview.plan, "shared-password")
    results: list[list[Path]] = []
    worker.succeeded.connect(results.append)
    worker.start()

    assert worker.wait(10000)
    app.processEvents()
    output = destination / "secret.7z"
    assert results == [[output]]
    backend = select_archive_backend()
    try:
        backend.list_members(
            output,
            password=None,
            cancelled=lambda: False,
            process_changed=lambda _process: None,
        )
    except ArchivePasswordRequired:
        pass
    else:
        raise AssertionError("7z header names were visible without a password")
    members = backend.list_members(
        output,
        password="shared-password",
        cancelled=lambda: False,
        process_changed=lambda _process: None,
    )
    assert members and members[0].path == "folder"


def test_compress_worker_creates_password_zip_with_visible_name(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    source = tmp_path / "content.txt"
    source.write_text("content", encoding="utf-8")
    destination = tmp_path / "destination"
    destination.mkdir()
    preview = _ready_preview(
        (source,), destination, archive_format="zip", archive_name="secret"
    )
    assert preview.plan is not None
    worker = CooperativeCompressThread(preview.plan, "shared-password")
    worker.start()

    assert worker.wait(10000)
    app.processEvents()
    output = destination / "secret.zip"
    backend = select_archive_backend()
    members = backend.list_members(
        output,
        password=None,
        cancelled=lambda: False,
        process_changed=lambda _process: None,
    )
    assert [member.path for member in members] == ["content.txt"]
    assert members[0].is_encrypted
    backend.test_archive(
        output,
        password="shared-password",
        progress=lambda _percent: None,
        cancelled=lambda: False,
        process_changed=lambda _process: None,
    )


def test_individual_compression_creates_one_archive_per_target(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    first = tmp_path / "one.txt"
    folder = tmp_path / "folder"
    first.write_text("one", encoding="utf-8")
    folder.mkdir()
    (folder / "inside.txt").write_text("inside", encoding="utf-8")
    destination = tmp_path / "destination"
    destination.mkdir()
    preview = _ready_preview(
        (first, folder), destination, individual=True, archive_name="ignored"
    )
    assert preview.plan is not None
    worker = CooperativeCompressThread(preview.plan, "shared-password")
    worker.start()

    assert worker.wait(10000)
    app.processEvents()
    assert (destination / "one.txt.7z").is_file()
    assert (destination / "folder.7z").is_file()


def test_compress_worker_cancelled_before_start_leaves_no_output(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    source = tmp_path / "source.txt"
    source.write_text("source", encoding="utf-8")
    destination = tmp_path / "destination"
    destination.mkdir()
    preview = _ready_preview((source,), destination)
    assert preview.plan is not None
    worker = CooperativeCompressThread(preview.plan, "shared-password")
    cancelled: list[bool] = []
    worker.cancelled.connect(lambda: cancelled.append(True))
    worker.request_cancel()
    worker.start()

    assert worker.wait(5000)
    app.processEvents()
    assert cancelled == [True]
    assert list(destination.iterdir()) == []


def test_file_manager_menu_opens_compression_window_for_clicked_item(
    tmp_path: Path,
) -> None:
    QApplication.instance() or QApplication([])
    source = tmp_path / "source.txt"
    source.write_text("source", encoding="utf-8")
    screen = FileManagerScreen(lambda: None)
    try:
        screen.search_results_input.setPlainText(str(source))
        row = screen.search_results_input._tree.topLevelItem(0)
        menu = QMenu()
        screen._add_path_list_context_actions(menu, row, screen.search_results_input)
        real_menu = next(action.menu() for action in menu.actions() if action.text() == "実体操作")
        assert real_menu is not None
        labels = [action.text() for action in real_menu.actions()]
        assert f"この1件を圧縮（{source.name}）…" in labels
        assert "チェック済み1件を圧縮…" in labels

        next(action for action in real_menu.actions() if action.text().startswith("この1件を圧縮")).trigger()
        dialog = next(iter(screen._quick_compress_dialogs))
        assert dialog._sources == (source.resolve(),)
        assert dialog.destination_input.text() == str(source.parent.resolve())
        dialog.close()
    finally:
        for dialog in tuple(screen._quick_compress_dialogs):
            dialog.close()
        screen.close()


def test_compression_dialog_runs_and_keeps_the_result_visible(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    source = tmp_path / "source.txt"
    source.write_text("source", encoding="utf-8")
    dialog = QuickCompressDialog((source,))
    try:
        dialog.password_input.setText("shared-password")
        dialog.archive_name_input.setText("result")
        dialog.start_compress()
        worker = dialog._worker
        assert worker is not None and worker.wait(10000)
        app.processEvents()

        assert (tmp_path / "result.7z").is_file()
        assert dialog.stage_label.text() == "圧縮・検査完了：1件"
        assert "圧縮結果" in dialog.preview_text.toPlainText()
        assert dialog.close_button.isEnabled()
    finally:
        dialog.close()
