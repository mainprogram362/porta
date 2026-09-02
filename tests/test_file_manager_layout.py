import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QAbstractItemView, QApplication, QPushButton, QScrollArea

from apps.file_tools.file_manager import FileManagerScreen


def test_file_manager_uses_fixed_grid_panes_without_outer_scrollers():
    app = QApplication.instance() or QApplication([])
    screen = FileManagerScreen(lambda: None)
    try:
        screen.resize(1000, 760)
        screen.show()
        app.processEvents()
        assert screen.minimumSize().width() == 1020
        assert not screen.findChildren(QScrollArea)
        assert screen._workspace_layout.itemAtPosition(0, 0).widget() is screen.favorites_box
        assert screen._workspace_layout.itemAtPosition(0, 1).widget() is screen.search_results_box
        assert screen._workspace_layout.itemAtPosition(1, 0).widget() is screen.execution_bar
        assert screen._workspace_layout.itemAtPosition(2, 0).widget() is screen.state_bar
        state_position = screen._workspace_layout.getItemPosition(
            screen._workspace_layout.indexOf(screen.state_bar)
        )
        assert state_position == (2, 0, 1, 3)
        assert screen.output_operation_box.isHidden()
        assert screen.destination_box.parentWidget() is screen.output_operation_box
        assert screen.operation_box.parentWidget() is screen.execution_bar
        assert not hasattr(screen, "preview_sidebar")
        assert not hasattr(screen, "destination_line_input")
        assert screen.copy_button.text() == "実行内容を確認…"
    finally:
        screen.close()


def test_file_manager_uses_two_preparation_columns_for_one_to_one():
    app = QApplication.instance() or QApplication([])
    screen = FileManagerScreen(lambda: None)
    try:
        screen.resize(1000, 760)
        screen.show()
        app.processEvents()

        assert screen.operation_summary_label.text().startswith("操作:")
        screen.copy_mode_combo.setCurrentIndex(1)  # 一対一コピー
        app.processEvents()
        assert screen._workspace_layout.itemAtPosition(0, 0).widget() is screen.favorites_box
        assert screen._workspace_layout.itemAtPosition(0, 1).widget() is screen.search_results_box
        assert screen._workspace_layout.itemAtPosition(0, 2).widget() is screen.output_operation_box
        assert screen._workspace_layout.itemAtPosition(2, 0).widget() is screen.state_bar
        state_position = screen._workspace_layout.getItemPosition(
            screen._workspace_layout.indexOf(screen.state_bar)
        )
        assert state_position == (2, 0, 1, 3)
        output_position = screen._workspace_layout.getItemPosition(
            screen._workspace_layout.indexOf(screen.output_operation_box)
        )
        assert output_position == (0, 2, 1, 1)
        assert [screen._workspace_layout.columnMinimumWidth(column) for column in range(3)] == [
            120,
            240,
            240,
        ]
        assert screen.operation_summary_label.isVisible()
        assert screen.readiness_label.isVisible()
        assert screen._workspace_layout.rowStretch(0) == 1
        destination_button_texts = {
            button.text() for button in screen.destination_list_actions.findChildren(QPushButton)
        }
        assert {"一覧を空にする", "全件チェック", "全チェック解除"} <= destination_button_texts
        assert screen.destination_list_actions.isVisible()
        assert screen.destination_list_actions.pos().y() < screen.destination_input.pos().y()
        assert screen.output_operation_box.height() == screen.search_results_box.height()
        screen.copy_mode_combo.setCurrentIndex(0)  # 通常コピー
        app.processEvents()
        assert screen._workspace_layout.itemAtPosition(0, 1).widget() is screen.search_results_box
        assert screen.output_operation_box.isHidden()
        assert screen._workspace_layout.columnMinimumWidth(2) == 240
        screen.operation_combo.setCurrentIndex(4)  # リネーム
        app.processEvents()
        assert screen._workspace_layout.itemAtPosition(0, 2).widget() is screen.rename_operation_box
        assert screen._workspace_layout.columnMinimumWidth(2) == 240
        assert "border" in screen.rename_operation_box.styleSheet()
    finally:
        screen.close()


def test_file_manager_keeps_main_destination_only_for_one_to_one_zip():
    QApplication.instance() or QApplication([])
    screen = FileManagerScreen(lambda: None)
    try:
        screen.operation_combo.setCurrentIndex(2)  # ZIP

        assert screen.destination_box.isHidden()
        assert screen.output_operation_box.isHidden()

        screen.copy_mode_combo.setCurrentIndex(1)  # 指定先へZIP

        assert screen.destination_box.isHidden()
        assert screen.output_operation_box.isHidden()

        screen.copy_mode_combo.setCurrentIndex(2)  # 一対一ZIP
        assert not screen.destination_box.isHidden()
        assert not screen.output_operation_box.isHidden()
    finally:
        screen.close()


def test_file_manager_main_destination_list_is_reserved_for_one_to_one_mode(tmp_path):
    QApplication.instance() or QApplication([])
    first = tmp_path / "first"
    second = tmp_path / "second"
    replacement = tmp_path / "replacement"
    first.mkdir()
    second.mkdir()
    replacement.mkdir()
    screen = FileManagerScreen(lambda: None)
    try:
        screen.destination_input.setPlainText(f"{first}\n{second}")

        assert screen.destination_input.paths() == [first.resolve(), second.resolve()]
        assert screen.destination_input.maximum_items() is None
        assert not screen.destination_input.drop_replaces
        assert screen.destination_input.isHidden()

        screen.copy_mode_combo.setCurrentIndex(1)  # 一対一コピー
        assert screen.destination_input.maximum_items() is None
        assert not screen.destination_input.drop_replaces
        assert not screen.destination_input.isHidden()
        screen.destination_input.append_dropped_paths(
            [replacement], replace=screen.destination_input.drop_replaces
        )
        assert screen.destination_input.paths() == [first.resolve(), second.resolve(), replacement.resolve()]
    finally:
        screen.close()


def test_file_manager_uses_checks_without_a_separate_operation_target_control():
    QApplication.instance() or QApplication([])
    screen = FileManagerScreen(lambda: None)
    try:
        button_texts = {button.text() for button in screen.findChildren(QPushButton)}
        assert not {"①へ追加", "③へ送る", "④へ追加"} & button_texts
        assert screen.search_results_box.title() == "作業一覧"
        assert screen.search_results_input._operation_target_column is None
    finally:
        screen.close()


def test_file_manager_confirmation_previews_only_checked_rows(tmp_path):
    QApplication.instance() or QApplication([])
    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    output = tmp_path / "output"
    first.write_text("first", encoding="utf-8")
    second.write_text("second", encoding="utf-8")
    output.mkdir()
    screen = FileManagerScreen(lambda: None)
    try:
        screen.search_results_input.setPlainText(f"{first}\n{second}")
        screen.search_results_input._set_item_selected(
            screen.search_results_input._tree.topLevelItem(1), False
        )
        screen.update_operation_preview()

        assert "チェック済み: 1 件" in screen.operation_summary_label.text()
        screen.show_operation_confirmation()
        dialog = next(iter(screen._operation_confirmation_dialogs))
        dialog.destination_input.setText(str(output))
        assert str(first) in dialog.preview_text.toPlainText()
        assert str(second) not in dialog.preview_text.toPlainText()
    finally:
        screen.close()


def test_file_manager_has_no_standalone_search_panel():
    QApplication.instance() or QApplication([])
    screen = FileManagerScreen(lambda: None)
    try:
        assert screen.search_results_input.parentWidget() is screen.search_results_box
        button_texts = {button.text() for button in screen.findChildren(QPushButton)}
        assert "検索" not in button_texts
        assert not hasattr(screen, "search_roots_input")
        assert "拡大" not in button_texts
        assert "メディア情報整理へ送る" not in button_texts
        assert "動画変換へ送る" not in button_texts
        assert screen.rename_panel.add_rule_button.minimumWidth() == 96
        screen.operation_combo.setCurrentIndex(4)
        screen.rename_panel.rule_kind_combo.setCurrentIndex(6)  # 文字列を置換
        assert screen.rename_panel.text_input.minimumWidth() == 130
        assert screen.rename_panel.replacement_input.minimumWidth() == 130
    finally:
        screen.close()


def test_file_manager_rename_panel_lists_live_before_and_after_names(tmp_path):
    QApplication.instance() or QApplication([])
    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    first.write_text("first", encoding="utf-8")
    second.write_text("second", encoding="utf-8")
    screen = FileManagerScreen(lambda: None)
    try:
        screen.search_results_input.setPlainText(f"{first}\n{second}")
        screen.search_results_input.select_all_items()
        screen.operation_combo.setCurrentIndex(4)  # リネーム
        screen.rename_panel.rule_kind_combo.setCurrentIndex(2)  # 末尾に追加
        screen.rename_panel.text_input.setText("_done")
        screen.rename_panel.add_rule()

        preview = screen.rename_panel.preview_tree
        assert preview.topLevelItemCount() == 2
        assert preview.topLevelItem(0).text(0) == "first.txt"
        assert preview.topLevelItem(0).text(1) == "first_done.txt"
        assert preview.topLevelItem(1).text(0) == "second.txt"
        assert preview.topLevelItem(1).text(1) == "second_done.txt"
    finally:
        screen.close()


def test_file_manager_keeps_execution_results_only_in_memory_until_cleared():
    QApplication.instance() or QApplication([])
    screen = FileManagerScreen(lambda: None)
    try:
        assert not screen.results_button.isEnabled()
        screen._record_execution_result("コピー完了", "1件")
        screen._record_execution_result("ZIP作成完了", "2件")
        assert screen.results_button.isEnabled()
        screen.show_execution_results()
        assert "コピー完了" in screen._results_text.toPlainText()
        assert "ZIP作成完了" in screen._results_text.toPlainText()
        screen.clear_execution_results()
        assert not screen.results_button.isEnabled()
        assert screen._execution_results == []
    finally:
        screen.close()


def test_file_manager_places_direct_work_list_actions_at_the_top():
    QApplication.instance() or QApplication([])
    screen = FileManagerScreen(lambda: None)
    try:
        button_texts = {button.text() for button in screen.search_results_box.findChildren(QPushButton)}
        assert {"一覧を空にする", "全件チェック", "全チェック解除", "条件で選別 ▸"} <= button_texts
        assert "全対象" not in button_texts
        assert "全対象解除" not in button_texts
    finally:
        screen.close()


def test_file_manager_collapsible_conditions_update_checks_without_removing_rows(tmp_path):
    app = QApplication.instance() or QApplication([])
    keep = tmp_path / "keep.mp4"
    excluded = tmp_path / "sample.mp4"
    other = tmp_path / "notes.txt"
    for path in (keep, excluded, other):
        path.write_bytes(b"")
    screen = FileManagerScreen(lambda: None)
    try:
        screen.search_results_input.setPlainText(f"{keep}\n{excluded}\n{other}")
        screen.search_results_input._tree.topLevelItem(2).setSelected(True)
        assert screen.selection_filter_panel.isHidden()

        screen.selection_filter_toggle.click()
        assert not screen.selection_filter_panel.isHidden()
        assert screen.selection_filter_toggle.text() == "条件で選別 ◂"

        screen.selection_filter_query.setText(".mp4, -sample")
        screen.apply_selection_filter()

        assert screen.search_results_input.paths() == [keep, excluded, other]
        assert screen.search_results_input.selected_paths() == [keep]
        assert screen.search_results_input.row_selected_paths() == [other]
        assert "条件一致 1 件" in screen.status_label.text()
    finally:
        screen.close()


def test_file_manager_favorites_width_is_reduced_to_three_fifths_of_previous_space():
    app = QApplication.instance() or QApplication([])
    screen = FileManagerScreen(lambda: None)
    try:
        screen.resize(1020, 720)
        screen.show()
        app.processEvents()
        assert screen.favorites_box.width() == 168
        assert screen.favorites_box.maximumWidth() == 168
    finally:
        screen.close()


def test_file_manager_keeps_checkbox_bulk_actions_out_of_context_menus_and_has_no_target_menu(tmp_path):
    QApplication.instance() or QApplication([])
    item = tmp_path / "item.txt"
    item.write_text("content", encoding="utf-8")
    screen = FileManagerScreen(lambda: None)
    try:
        paths = screen.search_results_input
        paths.setPlainText(str(item))
        row_position = paths._tree.visualRect(paths._tree.indexFromItem(paths._tree.topLevelItem(0), 1)).center()
        menu = paths._tree._build_path_context_menu(row_position)
        actions = {action.text() for action in menu.actions() if not action.isSeparator()}

        assert "チェック" not in actions
        assert "操作対象" not in actions
        assert "一覧を整理" in actions
    finally:
        screen.close()


def test_file_manager_expands_checked_folders_in_the_same_list(tmp_path):
    QApplication.instance() or QApplication([])
    folder = tmp_path / "folder"
    folder.mkdir()
    child_file = folder / "child.txt"
    child_folder = folder / "child-folder"
    child_file.write_text("content", encoding="utf-8")
    child_folder.mkdir()
    checked_file = tmp_path / "remove.txt"
    checked_file.write_text("content", encoding="utf-8")
    untouched = tmp_path / "untouched.txt"
    untouched.write_text("content", encoding="utf-8")
    screen = FileManagerScreen(lambda: None)
    try:
        source = screen.search_results_input
        source.setPlainText(f"{checked_file}\n{folder}\n{untouched}")
        source._set_item_selected(source._tree.topLevelItem(2), False)

        screen.expand_checked_directories(source, with_query=False)

        assert source.paths() == [untouched.resolve(), child_folder.resolve(), child_file.resolve()]
        assert screen.status_label.text().startswith("通知：作業一覧：チェック済み2件を置換")
    finally:
        screen.close()


def test_file_manager_adds_its_specific_actions_to_generic_path_menus(tmp_path):
    QApplication.instance() or QApplication([])
    file_path = tmp_path / "item.txt"
    file_path.write_text("content", encoding="utf-8")
    screen = FileManagerScreen(lambda: None)
    try:
        paths = screen.search_results_input
        paths.setPlainText(str(file_path))
        menu = paths._tree._build_path_context_menu(QPoint(1, 400))
        actions = {action.text() for action in menu.actions() if not action.isSeparator()}
        assert {"展開", "他のツールへ送る"} <= actions
        expand_menu = next(action.menu() for action in menu.actions() if action.text() == "展開")
        assert expand_menu is not None
        assert {
            "チェック済みフォルダを展開",
            "チェック済みに条件を指定して展開…",
            "選択中フォルダを展開",
            "選択中に条件を指定して展開…",
        } <= {action.text() for action in expand_menu.actions() if not action.isSeparator()}
    finally:
        screen.close()


def test_file_manager_copies_checked_paths_and_names_as_newline_separated_text(tmp_path):
    QApplication.instance() or QApplication([])
    first = tmp_path / "first file.txt"
    second = tmp_path / "second.mp4"
    first.write_text("first", encoding="utf-8")
    second.write_bytes(b"second")
    screen = FileManagerScreen(lambda: None)
    try:
        paths = screen.search_results_input
        paths.setPlainText(f"{first}\n{second}")
        row_position = paths._tree.visualRect(
            paths._tree.indexFromItem(paths._tree.topLevelItem(0), 1)
        ).center()
        menu = paths._tree._build_path_context_menu(row_position)
        copy_menu = next(action.menu() for action in menu.actions() if action.text() == "パス・ファイル名コピー")
        assert copy_menu is not None
        actions = {action.text(): action for action in copy_menu.actions()}
        assert {
            "パス：この1件をコピー",
            "パス：チェック済みをコピー",
            "パス：選択中をコピー",
            "パス：全件をコピー",
            "ファイル名：この1件をコピー",
            "ファイル名：チェック済みをコピー",
            "ファイル名：選択中をコピー",
            "ファイル名：全件をコピー",
        } <= set(actions)

        actions["パス：チェック済みをコピー"].trigger()
        assert QApplication.clipboard().text() == f"{first.resolve()}\n{second.resolve()}"
        actions["ファイル名：チェック済みをコピー"].trigger()
        assert QApplication.clipboard().text() == "first file.txt\nsecond.mp4"
        paths._tree.clearSelection()
        paths._tree.topLevelItem(1).setSelected(True)
        actions["パス：選択中をコピー"].trigger()
        assert QApplication.clipboard().text() == str(second.resolve())
    finally:
        screen.close()


def test_file_manager_blue_selection_uses_standard_multi_selection_without_changing_checks(tmp_path):
    app = QApplication.instance() or QApplication([])
    values = [tmp_path / name for name in ("first", "second", "third")]
    screen = FileManagerScreen(lambda: None)
    try:
        paths = screen.search_results_input
        paths.setPlainText("\n".join(str(path) for path in values))
        screen.show()
        app.processEvents()
        tree = paths._tree
        assert tree.selectionMode() == QAbstractItemView.SelectionMode.ExtendedSelection

        tree.setFocus()
        QTest.keyClick(tree, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
        assert paths.row_selected_paths() == values

        paths._set_item_selected(tree.topLevelItem(1), False)
        assert paths.row_selected_paths() == values
        assert paths.selected_paths() == [values[0], values[2]]

        tree.clearSelection()
        tree.setCurrentItem(tree.topLevelItem(0))
        QTest.keyClick(tree, Qt.Key.Key_Down, Qt.KeyboardModifier.ShiftModifier)
        assert paths.row_selected_paths() == values[:2]
    finally:
        screen.close()


def test_file_manager_double_click_opens_folder_and_replaces_the_whole_list(tmp_path):
    folder = tmp_path / "folder"
    folder.mkdir()
    child_file = folder / "child.txt"
    child_folder = folder / "nested"
    child_file.write_text("content", encoding="utf-8")
    child_folder.mkdir()
    unrelated = tmp_path / "unrelated.txt"
    unrelated.write_text("content", encoding="utf-8")
    screen = FileManagerScreen(lambda: None)
    try:
        paths = screen.search_results_input
        paths.setPlainText(f"{unrelated}\n{folder}")
        folder_row = paths._tree.topLevelItem(1)

        paths._on_tree_item_double_clicked(folder_row, 1)

        assert paths.paths() == [child_file, child_folder]
        assert unrelated not in paths.paths()
    finally:
        screen.close()


def test_former_double_click_child_chooser_is_available_in_expand_menu(tmp_path):
    folder = tmp_path / "folder"
    folder.mkdir()
    child = folder / "child.txt"
    child.write_text("content", encoding="utf-8")
    unrelated = tmp_path / "unrelated.txt"
    unrelated.write_text("content", encoding="utf-8")
    screen = FileManagerScreen(lambda: None)
    try:
        paths = screen.search_results_input
        paths.setPlainText(f"{folder}\n{unrelated}")
        folder_row = paths._tree.topLevelItem(0)
        paths._choose_direct_children = (  # type: ignore[method-assign]
            lambda _directory, _candidates, maximum_choices: (child,)
        )
        row_position = paths._tree.visualRect(
            paths._tree.indexFromItem(folder_row, 1)
        ).center()
        menu = paths._tree._build_path_context_menu(row_position)
        expand_menu = next(action.menu() for action in menu.actions() if action.text() == "展開")
        choose_action = next(
            action for action in expand_menu.actions() if action.text() == "この1件の直下を選んで展開…"
        )

        choose_action.trigger()

        assert paths.paths() == [child, unrelated]
    finally:
        screen.close()


def test_file_manager_selected_expansion_is_independent_from_checks(tmp_path):
    folder = tmp_path / "folder"
    folder.mkdir()
    child = folder / "child.txt"
    child.write_text("content", encoding="utf-8")
    checked = tmp_path / "checked.txt"
    checked.write_text("content", encoding="utf-8")
    screen = FileManagerScreen(lambda: None)
    try:
        paths = screen.search_results_input
        paths.setPlainText(f"{checked}\n{folder}")
        paths._tree.clearSelection()
        paths._tree.topLevelItem(1).setSelected(True)
        paths._set_item_selected(paths._tree.topLevelItem(0), False)
        paths._set_item_selected(paths._tree.topLevelItem(1), False)

        screen.expand_directories(paths, scope="selected", with_query=False)

        assert paths.paths() == [checked, child]
        assert not paths.item_is_selected(paths._tree.topLevelItem(0))
        assert screen.status_label.text().startswith("通知：作業一覧：選択中1件を置換")
    finally:
        screen.close()
