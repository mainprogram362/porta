import os
from pathlib import Path
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMenu

from apps.file_tools.file_manager.parent_move_workflow import (
    build_parent_move_preview,
    execute_parent_move_plan,
    undo_parent_move_plan,
)
from apps.file_tools.file_manager.window import FileManagerScreen
from apps.file_tools.file_manager import parent_move_workflow
from foundation.operation_progress import OperationFailure


def test_parent_move_partial_failure_preserves_completed_and_competing_entries(tmp_path, monkeypatch):
    folder = tmp_path / "parent" / "child"
    folder.mkdir(parents=True)
    first, second = folder / "first", folder / "second"
    first.write_text("first")
    second.write_text("second")
    plan = build_parent_move_preview((first, second)).plan
    original = parent_move_workflow.rename_noreplace

    def compete(source, destination):
        if source == second:
            destination.write_text("competitor")
        original(source, destination)

    monkeypatch.setattr(parent_move_workflow, "rename_noreplace", compete)
    with pytest.raises(OperationFailure) as failure:
        execute_parent_move_plan(plan)
    assert len(failure.value.records) == 1
    assert not first.exists()
    assert (folder.parent / "first").read_text() == "first"
    assert (folder.parent / "second").read_text() == "competitor"
    assert second.read_text() == "second"


def test_parent_move_undo_rejects_a_replaced_output(tmp_path):
    folder = tmp_path / "parent" / "child"
    folder.mkdir(parents=True)
    source = folder / "file"
    source.write_text("mine")
    plan = build_parent_move_preview((source,)).plan
    output = execute_parent_move_plan(plan)[0]
    output.rename(output.with_name("saved-original"))
    output.write_text("other writer")
    with pytest.raises(OSError, match="置き換わ"):
        undo_parent_move_plan(plan)
    assert output.read_text() == "other writer"
    assert not source.exists()


def test_parent_move_is_a_same_filesystem_rename_for_files_and_folders(tmp_path: Path):
    source_directory = tmp_path / "a" / "b" / "c"
    source_directory.mkdir(parents=True)
    source_file = source_directory / "movie.mp4"
    source_file.write_text("video", encoding="utf-8")
    source_folder = source_directory / "album"
    source_folder.mkdir()
    (source_folder / "inside.txt").write_text("inside", encoding="utf-8")

    preview = build_parent_move_preview((source_file, source_folder))

    assert preview.plan is not None
    outputs = execute_parent_move_plan(preview.plan)
    assert outputs == [tmp_path / "a" / "b" / "movie.mp4", tmp_path / "a" / "b" / "album"]
    assert outputs[0].read_text(encoding="utf-8") == "video"
    assert (outputs[1] / "inside.txt").read_text(encoding="utf-8") == "inside"

    restored = undo_parent_move_plan(preview.plan)
    assert restored == [source_file, source_folder]
    assert source_file.exists()
    assert (source_folder / "inside.txt").exists()


def test_parent_move_rejects_name_collisions_and_nested_targets(tmp_path: Path):
    source_directory = tmp_path / "a" / "b" / "c"
    source_directory.mkdir(parents=True)
    source_file = source_directory / "movie.mp4"
    source_file.write_text("video", encoding="utf-8")
    (tmp_path / "a" / "b" / "movie.mp4").write_text("taken", encoding="utf-8")

    collision = build_parent_move_preview((source_file,))
    assert collision.plan is None
    assert "同名" in collision.text

    nested_folder = source_directory / "album"
    nested_folder.mkdir()
    nested_file = nested_folder / "inside.txt"
    nested_file.write_text("inside", encoding="utf-8")
    nested = build_parent_move_preview((nested_folder, nested_file))
    assert nested.plan is None
    assert "親子関係" in nested.text


def test_file_manager_collects_parent_move_under_special_context_operations(tmp_path: Path):
    app = QApplication.instance() or QApplication([])
    source = tmp_path / "a" / "b" / "c" / "movie.mp4"
    source.parent.mkdir(parents=True)
    source.write_text("video", encoding="utf-8")
    screen = FileManagerScreen(lambda: None)
    try:
        screen.search_results_input.setPlainText(str(source))
        screen.search_results_input.select_all_items()
        menu = QMenu()
        screen._add_path_list_context_actions(menu, None, screen.search_results_input)
        special = next(action for action in menu.actions() if action.text() == "特殊操作").menu()
        action = next(
            action
            for action in special.actions()
            if "チェック済み1件を親フォルダへ移動" in action.text()
        )
        assert action.isEnabled()
    finally:
        screen.close()
