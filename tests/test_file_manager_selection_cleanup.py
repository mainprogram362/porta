import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from apps.file_tools.file_manager import FileManagerScreen


def _actions(screen):
    return {action.text(): action for button in (screen.check_menu_button, screen.list_cleanup_button)
            for action in button.menu().actions()}


def test_operation_scope_selection_and_frozen_preview(tmp_path):
    app = QApplication.instance() or QApplication([])
    first, second = tmp_path / "first.txt", tmp_path / "second.txt"
    first.touch()
    second.touch()
    screen = FileManagerScreen(lambda: None)
    try:
        source = screen.search_results_input
        source.setPlainText(f"{first}\n{second}")
        source.clear_item_selection()
        assert screen.target_scope_combo.currentData() == "checked"
        assert not screen.copy_button.isEnabled()
        source._tree.topLevelItem(1).setSelected(True)
        screen.target_scope_combo.setCurrentIndex(1)
        assert screen._operation_targets() == (second,)
        assert "選択中: 1 件" in screen.operation_summary_label.text()
        screen.copy_button.click()
        dialog = next(iter(screen._operation_confirmation_dialogs))
        source._tree.clearSelection()
        assert not screen.copy_button.isEnabled()
        assert dialog._targets == (second,)
        screen.target_scope_combo.setCurrentIndex(2)
        assert screen._operation_targets() == (first, second)
        assert screen.copy_button.isEnabled()
    finally:
        for dialog in tuple(screen._operation_confirmation_dialogs):
            dialog.close()
        app.processEvents()
        screen.close()


def test_normal_archive_operations_use_checked_targets_and_reject_mixed_extraction(tmp_path, monkeypatch):
    QApplication.instance() or QApplication([])
    archive, ordinary = tmp_path / "sample.zip", tmp_path / "note.txt"
    archive.touch()
    ordinary.touch()
    screen = FileManagerScreen(lambda: None)
    opened = []
    monkeypatch.setattr(screen, "_open_quick_extract_dialog", lambda paths: opened.append(paths))
    monkeypatch.setattr(screen, "_open_quick_compress_dialog", lambda paths: opened.append(paths))
    try:
        screen.search_results_input.setPlainText(f"{archive}\n{ordinary}")
        screen.search_results_input.select_all_items()
        screen.operation_combo.setCurrentIndex(screen.operation_combo.findData("extract"))
        screen.show_operation_confirmation()
        assert not opened
        assert not screen.copy_button.isEnabled()
        screen.search_results_input.setPlainText(str(archive))
        screen.search_results_input.select_all_items()
        screen.show_operation_confirmation()
        assert opened == [(archive,)]
        screen.operation_combo.setCurrentIndex(screen.operation_combo.findData("zip"))
        screen.copy_mode_combo.setCurrentIndex(screen.copy_mode_combo.findData("archive"))
        screen.show_operation_confirmation()
        assert opened == [(archive,), (archive,)]
    finally:
        screen.close()


def test_work_list_selected_rows_can_be_checked_unchecked_and_removed(tmp_path):
    QApplication.instance() or QApplication([])
    first, second, third = (tmp_path / name for name in ("first.txt", "second.txt", "third.txt"))
    for path in (first, second, third):
        path.write_text(path.name, encoding="utf-8")
    screen = FileManagerScreen(lambda: None)
    try:
        work_list = screen.search_results_input
        work_list.setPlainText(f"{first}\n{second}\n{third}")
        work_list.clear_item_selection()
        work_list._tree.topLevelItem(0).setSelected(True)
        work_list._tree.topLevelItem(2).setSelected(True)
        actions = _actions(screen)

        actions["選択対象にチェックを入れる"].trigger()
        assert work_list.selected_paths() == [first.resolve(), third.resolve()]
        assert "選択対象 2件をチェックしました" in screen.status_label.text()

        actions["選択対象のチェックを外す"].trigger()
        assert work_list.selected_paths() == []
        assert "選択対象 2件のチェックを外しました" in screen.status_label.text()

        actions["選択対象をリストから消す"].trigger()
        assert work_list.paths() == [second.resolve()]
        assert all(path.exists() for path in (first, second, third))
        assert "選択対象 2件をリストから除外しました" in screen.status_label.text()
    finally:
        screen.close()


def test_work_list_selected_rows_actions_require_blue_selection(tmp_path):
    QApplication.instance() or QApplication([])
    path = tmp_path / "item.txt"
    path.write_text("content", encoding="utf-8")
    screen = FileManagerScreen(lambda: None)
    try:
        screen.search_results_input.setPlainText(str(path))
        _actions(screen)["選択対象をリストから消す"].trigger()
        assert screen.search_results_input.paths() == [path.resolve()]
        assert "対象行を青く選択" in screen.status_label.text()
    finally:
        screen.close()
