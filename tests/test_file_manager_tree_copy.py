import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QApplication

from apps.file_tools.file_manager import FileManagerScreen
from apps.file_tools.file_manager.tree_copy_dialog import (
    TreeCopyDialog,
    build_tree_snapshot,
    render_tree_snapshot,
)


def test_tree_snapshot_keeps_four_levels_and_renders_a_shallower_view(tmp_path: Path) -> None:
    root = tmp_path / "root"
    leaf = root / "one" / "two" / "three" / "four.txt"
    leaf.parent.mkdir(parents=True)
    leaf.write_text("x", encoding="utf-8")

    snapshot = build_tree_snapshot((root,))

    three_levels = render_tree_snapshot(snapshot, display_depth=3)
    four_levels = render_tree_snapshot(snapshot, display_depth=4)
    assert f"[1] {root}" in three_levels
    assert "one/" in three_levels
    assert "three/" in three_levels
    assert "four.txt" not in three_levels
    assert "four.txt" in four_levels


def test_tree_snapshot_marks_the_point_where_its_entry_limit_stops(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    for name in ("a.txt", "b.txt", "c.txt"):
        (root / name).write_text(name, encoding="utf-8")

    snapshot = build_tree_snapshot((root,), maximum_entries=2)

    assert snapshot.limit_reached
    assert "表示上限1000件に達したため、以降は取得していません。" in render_tree_snapshot(
        snapshot, display_depth=1
    )


def test_tree_copy_dialog_replaces_manual_edits_when_depth_changes(tmp_path: Path) -> None:
    QApplication.instance() or QApplication([])
    root = tmp_path / "root"
    (root / "child").mkdir(parents=True)
    dialog = TreeCopyDialog((root,))
    try:
        dialog.text_edit.setPlainText("手動の編集")
        dialog.depth_spin.setValue(2)
        assert "手動の編集" not in dialog.text_edit.toPlainText()
        assert f"[1] {root}" in dialog.text_edit.toPlainText()
    finally:
        dialog.close()


def test_file_manager_offers_four_tree_actions_and_ignores_files(tmp_path: Path) -> None:
    QApplication.instance() or QApplication([])
    directory = tmp_path / "directory"
    directory.mkdir()
    file_path = tmp_path / "file.txt"
    file_path.write_text("x", encoding="utf-8")
    screen = FileManagerScreen(lambda: None)
    try:
        paths = screen.search_results_input
        paths.setPlainText(f"{directory}\n{file_path}")
        row_position = paths._tree.visualRect(
            paths._tree.indexFromItem(paths._tree.topLevelItem(1), 1)
        ).center()
        menu = paths._tree._build_path_context_menu(row_position or QPoint(1, 1))
        copy_menu = next(
            action.menu() for action in menu.actions() if action.text() == "パス・ファイル名コピー"
        )
        assert copy_menu is not None
        actions = {action.text(): action for action in copy_menu.actions()}
        assert {
            "ツリー：この1件を表示…",
            "ツリー：チェック済みフォルダを表示（1件）…",
            "ツリー：選択中フォルダを表示（0件）…",
            "ツリー：全件のフォルダを表示（1件）…",
        } <= set(actions)
        assert not actions["ツリー：この1件を表示…"].isEnabled()
    finally:
        screen.close()
