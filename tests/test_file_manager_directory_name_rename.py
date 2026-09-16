import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMenu

from apps.file_tools.file_manager.directory_name_rename import (
    DirectoryNameRule,
    build_directory_name_rename_preview,
)
from apps.file_tools.file_manager.rename_workflow import execute_rename_plan
from apps.file_tools.file_manager.window import FileManagerScreen


def test_directory_name_can_be_appended_or_replace_the_file_name(tmp_path: Path):
    parent = tmp_path / "test"
    parent.mkdir()
    source = parent / "aiueo.mp4"
    source.write_text("video", encoding="utf-8")

    appended = build_directory_name_rename_preview(
        (source,), DirectoryNameRule("insert", "_[@insert]", insert_position="end")
    )
    assert appended.plan is not None
    assert appended.plan.renames[0].output == parent / "aiueo_test.mp4"
    execute_rename_plan(appended.plan)

    replaced_source = parent / "aiueo_test.mp4"
    replaced = build_directory_name_rename_preview(
        (replaced_source,), DirectoryNameRule("replace", "[@insert]")
    )
    assert replaced.plan is not None
    assert replaced.plan.renames[0].output == parent / "test.mp4"


def test_directory_name_rejects_only_same_directory_name_collisions(tmp_path: Path):
    first_parent = tmp_path / "one" / "test"
    second_parent = tmp_path / "two" / "test"
    first_parent.mkdir(parents=True)
    second_parent.mkdir(parents=True)
    first = first_parent / "a.mp4"
    second = second_parent / "a.mp4"
    first.write_text("first", encoding="utf-8")
    second.write_text("second", encoding="utf-8")

    allowed = build_directory_name_rename_preview(
        (first, second), DirectoryNameRule("replace", "[@insert]")
    )
    assert allowed.plan is not None
    assert [rename.output.name for rename in allowed.plan.renames] == ["test.mp4", "test.mp4"]

    same_parent_second = first_parent / "b.mp4"
    same_parent_second.write_text("other", encoding="utf-8")
    blocked = build_directory_name_rename_preview(
        (first, same_parent_second), DirectoryNameRule("replace", "[@insert]")
    )
    assert blocked.plan is None
    assert "重複" in blocked.text


def test_file_manager_shows_special_operations_for_checked_paths_only(tmp_path: Path):
    app = QApplication.instance() or QApplication([])
    source = tmp_path / "test" / "aiueo.mp4"
    source.parent.mkdir()
    source.write_text("video", encoding="utf-8")
    screen = FileManagerScreen(lambda: None)
    try:
        screen.search_results_input.setPlainText(str(source))
        screen.search_results_input.select_all_items()
        menu = QMenu()
        screen._add_path_list_context_actions(menu, None, screen.search_results_input)
        special = next(action for action in menu.actions() if action.text() == "特殊操作").menu()
        labels = [action.text() for action in special.actions()]
        assert any("チェック済み1件を親フォルダへ移動" in label for label in labels)
        assert any("チェック済み1件の親フォルダ名をファイル名へ" in label for label in labels)
        assert not any("この1件" in label for label in labels)
    finally:
        screen.close()
