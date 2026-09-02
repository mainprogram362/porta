from __future__ import annotations

from pathlib import Path
import subprocess
import time
import zipfile

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QDialog, QMenu

from apps.file_tools.file_manager import archive_backends
from apps.file_tools.file_manager import quick_extract_dialog as quick_extract_module
from apps.file_tools.file_manager.archive_backends import (
    ArchiveBackend,
    ArchiveMember,
    ArchivePasswordRequired,
    BackendAvailability,
    SEVEN_ZIP_TOOL_DIR,
    archive_backend_candidates,
    select_archive_backend,
)
from apps.file_tools.file_manager.extract_workflow import (
    ExtractPlan,
    PlannedExtraction,
    build_extract_preview,
    inspect_archive,
    probe_archive_access,
    resolve_selected_archive_members,
)
from apps.file_tools.file_manager.quick_extract_dialog import (
    ArchiveContentsDialog,
    CooperativeExtractThread,
    QuickExtractDialog,
)
from apps.file_tools.file_manager.window import FileManagerScreen


def _write_zip(path: Path, name: str = "folder/content.txt", content: str = "content") -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(name, content)


def _wait_for_inspection(dialog: QuickExtractDialog, app: QApplication) -> None:
    deadline = time.monotonic() + 5
    while dialog._inspection_worker is None and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.005)
    worker = dialog._inspection_worker
    assert worker is not None and worker.wait(5000)
    app.processEvents()


def test_bundled_7zzs_is_the_first_and_only_current_candidate() -> None:
    candidates = archive_backend_candidates()

    assert [backend.backend_id for backend in candidates] == ["bundled-7zzs"]
    assert candidates[0].availability().available
    assert select_archive_backend().backend_id == "bundled-7zzs"


def test_backend_selection_can_later_fall_through_to_another_candidate(monkeypatch) -> None:
    class FakeBackend(ArchiveBackend):
        backend_id = "fake"
        display_name = "fake"

        def __init__(self, available: bool) -> None:
            self._available = available

        def availability(self) -> BackendAvailability:
            return BackendAvailability(self._available, "test")

        def list_members(self, archive, *, password, cancelled, process_changed):
            return ()

        def extract(
            self,
            archive,
            destination,
            *,
            password,
            progress,
            cancelled,
            process_changed,
        ) -> None:
            return None

        def test_archive(
            self,
            archive,
            *,
            password,
            progress,
            cancelled,
            process_changed,
        ) -> None:
            return None

        def create_archive(
            self,
            sources,
            output,
            *,
            archive_format,
            compression_level,
            password,
            hide_names,
            progress,
            cancelled,
            process_changed,
        ) -> None:
            return None

    monkeypatch.setattr(
        archive_backends,
        "ARCHIVE_BACKEND_FACTORIES",
        (lambda: FakeBackend(False), lambda: FakeBackend(True)),
    )

    assert select_archive_backend().backend_id == "fake"


def test_bundled_backend_accepts_password_without_putting_it_in_api_arguments(
    tmp_path: Path,
) -> None:
    archive = tmp_path / "secret.7z"
    executable = SEVEN_ZIP_TOOL_DIR / "7zzs"
    subprocess.run(
        [
            str(executable),
            "a",
            str(archive),
            "/etc/hostname",
            "-ptest-password",
            "-mhe=on",
            "-y",
        ],
        check=True,
        stdout=subprocess.DEVNULL,
    )
    backend = select_archive_backend()

    try:
        backend.list_members(
            archive,
            password=None,
            cancelled=lambda: False,
            process_changed=lambda _process: None,
        )
    except ArchivePasswordRequired:
        pass
    else:
        raise AssertionError("password-protected archive did not request a password")

    members = backend.list_members(
        archive,
        password="test-password",
        cancelled=lambda: False,
        process_changed=lambda _process: None,
    )
    assert [member.path for member in members] == ["hostname"]


def test_extract_worker_extracts_to_a_staged_named_folder(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    archive = tmp_path / "sample.zip"
    _write_zip(archive)
    destination = tmp_path / "destination"
    destination.mkdir()
    preview = build_extract_preview((archive,), str(destination))
    assert preview.plan is not None
    worker = CooperativeExtractThread(preview.plan, None)
    results: list[list[Path]] = []
    worker.succeeded.connect(results.append)
    worker.start()

    assert worker.wait(5000)
    app.processEvents()
    output = destination / "sample"
    assert (output / "folder/content.txt").read_text(encoding="utf-8") == "content"
    assert results == [[output]]
    assert not list(destination.glob(".porta-extract-*"))


def test_archive_inspection_classifies_normal_password_and_damage(tmp_path: Path) -> None:
    backend = select_archive_backend()
    normal = tmp_path / "normal.zip"
    _write_zip(normal)
    encrypted = tmp_path / "encrypted.7z"
    executable = SEVEN_ZIP_TOOL_DIR / "7zzs"
    subprocess.run(
        [
            str(executable),
            "a",
            str(encrypted),
            "/etc/hostname",
            "-ptest-password",
            "-mhe=on",
            "-y",
        ],
        check=True,
        stdout=subprocess.DEVNULL,
    )
    damaged = tmp_path / "damaged.zip"
    with zipfile.ZipFile(damaged, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("payload.txt", "unique-damage-payload")
    data = bytearray(damaged.read_bytes())
    marker = data.index(b"unique-damage-payload")
    data[marker] ^= 0xFF
    damaged.write_bytes(data)

    def inspect(path: Path, password: str | None = None):
        return inspect_archive(
            backend,
            path,
            password=password,
            progress=lambda _percent: None,
            cancelled=lambda: False,
            process_changed=lambda _process: None,
        )

    assert inspect(normal).state == "normal"
    assert inspect(encrypted).state == "password_required"
    assert inspect(encrypted, "test-password").state == "password_verified"
    assert inspect(encrypted, "wrong").state == "password_rejected"
    assert inspect(damaged).state == "damaged"


def test_extract_worker_rejects_parent_path_before_extraction(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    archive = tmp_path / "unsafe.zip"
    _write_zip(archive, "../escape.txt", "unsafe")
    destination = tmp_path / "destination"
    destination.mkdir()
    preview = build_extract_preview((archive,), str(destination))
    assert preview.plan is not None
    worker = CooperativeExtractThread(preview.plan, None)
    failures: list[str] = []
    worker.failed.connect(failures.append)
    worker.start()

    assert worker.wait(5000)
    app.processEvents()
    assert failures and "危険な格納パス" in failures[0]
    assert not (tmp_path / "escape.txt").exists()
    assert list(destination.iterdir()) == []


def test_extract_worker_cancelled_before_start_leaves_no_output(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    archive = tmp_path / "sample.zip"
    _write_zip(archive)
    destination = tmp_path / "destination"
    destination.mkdir()
    preview = build_extract_preview((archive,), str(destination))
    assert preview.plan is not None
    worker = CooperativeExtractThread(preview.plan, None)
    cancelled: list[bool] = []
    worker.cancelled.connect(lambda: cancelled.append(True))
    worker.request_cancel()
    worker.start()

    assert worker.wait(5000)
    app.processEvents()
    assert cancelled == [True]
    assert list(destination.iterdir()) == []


def test_file_manager_menu_opens_independent_extract_window(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    archive = tmp_path / "sample.zip"
    normal = tmp_path / "normal.txt"
    _write_zip(archive)
    normal.write_text("normal", encoding="utf-8")
    destination = tmp_path / "destination"
    destination.mkdir()
    screen = FileManagerScreen(lambda: None)
    try:
        screen.search_results_input.setPlainText(f"{archive}\n{normal}")
        screen._session_last_destination = str(destination)
        row = screen.search_results_input._tree.topLevelItem(0)
        menu = QMenu()
        screen._add_path_list_context_actions(menu, row, screen.search_results_input)
        real_menu = next(action.menu() for action in menu.actions() if action.text() == "実体操作")
        assert real_menu is not None
        labels = [action.text() for action in real_menu.actions()]
        assert f"この1件を解凍（{archive.name}）…" in labels
        assert "チェック済み1件の圧縮ファイルを解凍…" in labels

        next(action for action in real_menu.actions() if action.text().startswith("この1件を解凍")).trigger()
        dialog = next(iter(screen._quick_extract_dialogs))
        screen._session_last_destination = str(tmp_path)
        _wait_for_inspection(dialog, app)

        assert dialog._archives == (archive.resolve(),)
        assert dialog.destination_input.text() == str(destination)
        assert "同梱 7-Zip 7zzs 26.02" in dialog.preview_text.toPlainText()
        assert "パスワード不要" in dialog.inspection_text.toPlainText()
        assert f"パス: {archive.resolve()}" in dialog.inspection_text.toPlainText()
        assert "中身を個別選択" in dialog.inspection_text.toPlainText()
        assert dialog.in_place_checkbox.isChecked()
        assert not dialog.destination_input.isEnabled()
        dialog.close()
    finally:
        for dialog in tuple(screen._quick_extract_dialogs):
            dialog.close()
        screen.close()


def test_extract_dialog_runs_and_reports_result(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    archive = tmp_path / "sample.zip"
    _write_zip(archive)
    destination = tmp_path / "destination"
    destination.mkdir()
    dialog = QuickExtractDialog((archive,), str(destination))
    try:
        _wait_for_inspection(dialog, app)
        assert dialog.start_button.isEnabled()
        dialog.start_extract()
        worker = dialog._worker
        assert worker is not None and worker.wait(5000)
        app.processEvents()

        assert (tmp_path / "sample/folder/content.txt").is_file()
        assert dialog.stage_label.text() == "解凍完了：1件"
        assert "解凍結果" in dialog.preview_text.toPlainText()
        assert dialog.close_button.isEnabled()
    finally:
        dialog.close()


def test_extract_dialog_applies_one_password_to_every_selected_archive(
    tmp_path: Path,
) -> None:
    app = QApplication.instance() or QApplication([])
    normal = tmp_path / "normal.zip"
    _write_zip(normal)
    encrypted = tmp_path / "encrypted.7z"
    executable = SEVEN_ZIP_TOOL_DIR / "7zzs"
    subprocess.run(
        [
            str(executable),
            "a",
            str(encrypted),
            "/etc/hostname",
            "-pshared-password",
            "-mhe=on",
            "-y",
        ],
        check=True,
        stdout=subprocess.DEVNULL,
    )
    destination = tmp_path / "destination"
    destination.mkdir()
    dialog = QuickExtractDialog((normal, encrypted), str(destination))
    try:
        _wait_for_inspection(dialog, app)
        assert not dialog.password_box.isHidden()
        assert "パスワードが必要（未解除）" in (
            dialog.inspection_text.toPlainText()
        )
        assert not dialog.start_button.isEnabled()

        dialog.password_input.setText("shared-password")
        dialog.start_inspection()
        _wait_for_inspection(dialog, app)

        report = dialog.inspection_text.toPlainText()
        assert "パスワードが必要（解除済み）" in report
        assert f"パス: {normal}" in report
        assert f"パス: {encrypted}" in report
        assert "選択した全ファイルへ適用" in report
        assert dialog.start_button.isEnabled()
    finally:
        dialog.close()


def test_password_probe_does_not_run_damage_test(tmp_path: Path) -> None:
    archive = tmp_path / "sample.zip"
    archive.write_bytes(b"placeholder")

    class ProbeBackend(ArchiveBackend):
        backend_id = "probe"
        display_name = "probe"

        def __init__(self) -> None:
            self.test_calls = 0

        def availability(self):
            return BackendAvailability(True, "ready")

        def list_members(self, archive, *, password, cancelled, process_changed):
            return (ArchiveMember("inside.txt", False),)

        def extract(self, archive, destination, *, password, progress, cancelled, process_changed):
            raise AssertionError("not used")

        def test_archive(self, archive, *, password, progress, cancelled, process_changed):
            self.test_calls += 1
            raise AssertionError("the initial password probe must not test all data")

        def create_archive(self, sources, output, *, archive_format, compression_level, password, hide_names, progress, cancelled, process_changed):
            raise AssertionError("not used")

    backend = ProbeBackend()
    result = probe_archive_access(
        backend,
        archive,
        password=None,
        cancelled=lambda: False,
        process_changed=lambda _process: None,
    )

    assert result.state == "normal"
    assert [member.path for member in result.members] == ["inside.txt"]
    assert backend.test_calls == 0


def test_extract_preview_defaults_can_target_each_archive_parent(tmp_path: Path) -> None:
    first_parent = tmp_path / "first"
    second_parent = tmp_path / "second"
    first_parent.mkdir()
    second_parent.mkdir()
    first = first_parent / "same.zip"
    second = second_parent / "same.zip"
    _write_zip(first)
    _write_zip(second)

    preview = build_extract_preview((first, second), "", in_place=True)

    assert preview.plan is not None
    assert [item.output for item in preview.plan.extractions] == [
        first_parent / "same",
        second_parent / "same",
    ]
    assert "その場モード" in preview.text


def test_extract_dialog_releases_destination_when_in_place_is_unchecked(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    archive = tmp_path / "sample.zip"
    _write_zip(archive)
    destination = tmp_path / "destination"
    destination.mkdir()
    dialog = QuickExtractDialog((archive,), str(destination))
    try:
        _wait_for_inspection(dialog, app)
        assert dialog.in_place_checkbox.isChecked()
        assert not dialog.destination_input.isEnabled()

        dialog.in_place_checkbox.setChecked(False)

        assert dialog.destination_input.isEnabled()
        assert dialog._plan is not None
        assert dialog._plan.extractions[0].output == destination / "sample"
    finally:
        dialog.close()


def test_individual_member_selection_is_executed_by_parent_worker(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    archive = tmp_path / "selected.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as value:
        value.writestr("keep.txt", "keep")
        value.writestr("drop.txt", "drop")
    dialog = QuickExtractDialog((archive,), "")
    try:
        _wait_for_inspection(dialog, app)
        dialog._selected_members[archive] = ("keep.txt",)
        dialog.refresh_preview()
        assert "個別選択: 1 項目" in dialog.preview_text.toPlainText()

        dialog.start_extract()
        worker = dialog._worker
        assert worker is not None and worker.wait(5000)
        app.processEvents()

        assert (tmp_path / "selected/keep.txt").read_text(encoding="utf-8") == "keep"
        assert not (tmp_path / "selected/drop.txt").exists()
        assert dialog.stage_label.text() == "解凍完了：1件"
    finally:
        dialog.close()


def test_individual_selection_does_not_test_or_extract_unselected_damage(
    tmp_path: Path,
) -> None:
    app = QApplication.instance() or QApplication([])
    archive = tmp_path / "partly-damaged.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as value:
        value.writestr("keep.txt", b"selected-content")
        value.writestr("broken.txt", b"unique-unselected-damaged-content")
    data = bytearray(archive.read_bytes())
    marker = data.index(b"unique-unselected-damaged-content")
    data[marker] ^= 0xFF
    archive.write_bytes(data)

    preview = build_extract_preview(
        (archive,),
        "",
        in_place=True,
        selected_members={archive: ("keep.txt",)},
    )
    assert preview.plan is not None
    worker = CooperativeExtractThread(preview.plan, None)
    results: list[list[Path]] = []
    failures: list[str] = []
    worker.succeeded.connect(results.append)
    worker.failed.connect(failures.append)
    worker.start()

    assert worker.wait(5000)
    app.processEvents()
    assert failures == []
    assert results == [[tmp_path / "partly-damaged"]]
    assert (tmp_path / "partly-damaged/keep.txt").read_bytes() == b"selected-content"
    assert not (tmp_path / "partly-damaged/broken.txt").exists()


def test_extract_worker_does_not_run_a_separate_damage_test(
    tmp_path: Path, monkeypatch
) -> None:
    app = QApplication.instance() or QApplication([])
    archive = tmp_path / "sample.zip"
    archive.write_bytes(b"placeholder")
    destination = tmp_path / "destination"
    destination.mkdir()
    output = destination / "sample"

    class OnePassBackend(ArchiveBackend):
        backend_id = "one-pass"
        display_name = "one-pass"

        def availability(self):
            return BackendAvailability(True, "ready")

        def list_members(self, archive, *, password, cancelled, process_changed):
            return (ArchiveMember("inside.txt", False, size=7),)

        def extract(self, archive, destination, *, password, progress, cancelled, process_changed):
            (destination / "inside.txt").write_text("content", encoding="utf-8")
            progress(100)

        def test_archive(self, archive, *, password, progress, cancelled, process_changed):
            raise AssertionError("事前の破損検査を実行してはいけません")

        def create_archive(self, sources, output, *, archive_format, compression_level, password, hide_names, progress, cancelled, process_changed):
            raise AssertionError("not used")

    backend = OnePassBackend()
    monkeypatch.setattr(quick_extract_module, "revalidate_extract_plan", lambda _plan: None)
    monkeypatch.setattr(
        quick_extract_module, "select_archive_backend", lambda _backend_id: backend
    )
    plan = ExtractPlan(
        (PlannedExtraction(archive, destination, output),),
        backend.backend_id,
        backend.display_name,
    )
    worker = CooperativeExtractThread(plan, None)
    failures: list[str] = []
    worker.failed.connect(failures.append)
    worker.start()

    assert worker.wait(5000)
    app.processEvents()
    assert failures == []
    assert (output / "inside.txt").read_text(encoding="utf-8") == "content"


def test_selected_directory_resolves_only_to_its_descendants() -> None:
    members = (
        ArchiveMember("chosen", True),
        ArchiveMember("chosen/first.txt", False),
        ArchiveMember("chosen/nested", True),
        ArchiveMember("chosen/nested/second.txt", False),
        ArchiveMember("other.txt", False),
    )

    assert resolve_selected_archive_members(members, ("chosen",)) == (
        "chosen",
        "chosen/first.txt",
        "chosen/nested",
        "chosen/nested/second.txt",
    )


def test_archive_contents_dialog_returns_checked_members_to_parent(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    parent = QuickExtractDialog((), "")
    dialog = ArchiveContentsDialog(
        tmp_path / "sample.zip",
        (
            ArchiveMember("first.txt", False, size=1536),
            ArchiveMember("folder", True),
        ),
        None,
        "その場モード",
        parent,
    )
    try:
        dialog.members_tree.topLevelItem(0).setCheckState(0, Qt.CheckState.Unchecked)
        dialog._accept_and_request_extract()
        app.processEvents()

        assert dialog.request_extract
        assert dialog.selected_members() == ("folder",)
        assert dialog.members_tree.headerItem().text(2) == "展開後サイズ"
        assert dialog.members_tree.topLevelItem(0).text(2) == "1,536 バイト"
        assert dialog.members_tree.topLevelItem(1).text(2) == "—"
    finally:
        dialog.close()
        parent.close()


def test_contents_window_returns_execution_request_to_original_dialog(
    tmp_path: Path, monkeypatch
) -> None:
    app = QApplication.instance() or QApplication([])
    archive = tmp_path / "sample.zip"
    _write_zip(archive)
    parent = QuickExtractDialog((archive,), "")
    try:
        _wait_for_inspection(parent, app)
        callbacks = []

        class FakeContentsDialog:
            request_extract = True

            def __init__(self, *args):
                self.args = args

            def exec(self):
                return QDialog.DialogCode.Accepted

            def selected_members(self):
                return ("folder/content.txt",)

        monkeypatch.setattr(quick_extract_module, "ArchiveContentsDialog", FakeContentsDialog)
        monkeypatch.setattr(
            quick_extract_module.QTimer,
            "singleShot",
            lambda _delay, callback: callbacks.append(callback),
        )

        parent.open_current_archive_contents()

        assert parent._selected_members[archive] == ("folder/content.txt",)
        assert len(callbacks) == 1
        assert callbacks[0].__self__ is parent
        assert callbacks[0].__name__ == "start_extract"
        assert "元画面で解凍開始" in parent.stage_label.text()
    finally:
        parent.close()


def test_file_manager_double_click_on_archive_opens_extract_dialog(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    archive = tmp_path / "sample.zip"
    _write_zip(archive)
    screen = FileManagerScreen(lambda: None)
    try:
        paths = screen.search_results_input
        paths.setPlainText(str(archive))

        paths._on_tree_item_double_clicked(paths._tree.topLevelItem(0), 1)

        assert len(screen._quick_extract_dialogs) == 1
        dialog = next(iter(screen._quick_extract_dialogs))
        assert dialog._archives == (archive,)
        _wait_for_inspection(dialog, app)
        dialog.close()
    finally:
        for dialog in tuple(screen._quick_extract_dialogs):
            dialog.close()
        screen.close()
