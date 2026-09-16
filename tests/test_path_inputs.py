import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from gui.composites import LineListInput, PathLineInput, PathListInput
from gui.composites.path_inputs import directory_for_path, standard_file_manager_directory
from PySide6.QtCore import QMimeData, QPoint, QPointF, QTimer, QUrl, Qt
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QDialog,
    QHeaderView,
    QPushButton,
    QStyleOptionViewItem,
    QTreeWidget,
)


def test_line_list_input_ignores_blank_lines_and_can_deduplicate():
    QApplication.instance() or QApplication([])
    widget = LineListInput()
    widget.setPlainText(" first \n\nsecond\nfirst\n")

    assert widget.items() == ["first", "second", "first"]
    assert widget.items(deduplicate=True) == ["first", "second"]


def test_path_list_input_parses_one_normalized_path_per_non_blank_line(tmp_path: Path):
    QApplication.instance() or QApplication([])
    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    # Paste padding is only trimmed when the unpadded entry actually exists;
    # otherwise whitespace is preserved as a possible part of the filename.
    first.touch()
    second.touch()
    widget = PathListInput()
    widget.setPlainText(f"\n{first}\n\n {second} \n")

    assert widget.paths() == [first.resolve(), second.resolve()]


def test_path_list_input_preserves_an_existing_filename_with_trailing_space(tmp_path: Path):
    QApplication.instance() or QApplication([])
    trailing_space_file = tmp_path / "movie.mp4 "
    trailing_space_file.write_text("content", encoding="utf-8")
    widget = PathListInput()

    widget.setPlainText(str(trailing_space_file))

    assert widget.paths() == [trailing_space_file]
    assert widget.items() == [str(trailing_space_file)]
    assert widget._tree.topLevelItem(0).text(1) == str(trailing_space_file)


def test_path_list_input_keeps_a_symbolic_link_name_and_explains_its_target(tmp_path: Path):
    QApplication.instance() or QApplication([])
    target = tmp_path / "target.txt"
    target.write_text("content", encoding="utf-8")
    link = tmp_path / "visible-link.txt"
    link.symlink_to(target)
    widget = PathListInput()

    widget.setPlainText(str(link))

    row = widget._tree.topLevelItem(0)
    assert widget.paths() == [link]
    assert row.text(1) == str(link)
    assert "リンク（ファイル）" in row.toolTip(widget._state_column)
    assert f"リンク先: {target}" in row.toolTip(1)


def test_path_list_input_can_limit_to_one_item_and_replace_it_by_drop(tmp_path: Path):
    QApplication.instance() or QApplication([])
    first = tmp_path / "first"
    second = tmp_path / "second"
    replacement = tmp_path / "replacement"
    first.mkdir()
    second.mkdir()
    replacement.mkdir()
    widget = PathListInput(
        accepted_path_kind="directory", drop_replaces=True, maximum_items=1
    )

    widget.setPlainText(f"{first}\n{second}")

    assert widget.paths() == [first.resolve()]
    assert widget._tree.topLevelItemCount() == 1  # No spare input row at capacity.
    widget.append_dropped_paths([replacement], replace=widget.drop_replaces)
    assert widget.paths() == [replacement.resolve()]

    widget.set_maximum_items(None)
    widget.set_drop_replaces(False)
    widget.append_dropped_paths([second], replace=widget.drop_replaces)

    assert widget.paths() == [replacement.resolve(), second.resolve()]
    assert widget._tree.topLevelItemCount() == 3  # Two paths plus the input row.


def test_path_list_input_uses_checkboxes_for_multi_selection(tmp_path: Path):
    QApplication.instance() or QApplication([])
    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    widget = PathListInput()
    widget.setPlainText(f"{first}\n{second}")

    widget.clear_item_selection()
    widget._set_item_selected(widget._tree.topLevelItem(1), True)

    assert widget.selected_paths() == [second.resolve()]
    assert widget.paths() == [first.resolve(), second.resolve()]


def test_path_list_input_batches_whole_list_checkbox_changes(tmp_path: Path):
    QApplication.instance() or QApplication([])
    paths = [tmp_path / f"item-{index}.txt" for index in range(12)]
    widget = PathListInput()
    widget.setPlainText("\n".join(str(path) for path in paths))
    changes: list[None] = []
    widget.selectionChanged.connect(lambda: changes.append(None))

    widget.clear_item_selection()
    assert len(changes) == 1
    assert widget.selected_paths() == []

    widget.select_all_items()
    assert len(changes) == 2
    assert widget.selected_paths() == [path.resolve() for path in paths]


def test_path_list_input_exposes_path_click_without_changing_checkbox_selection(tmp_path: Path):
    QApplication.instance() or QApplication([])
    path = tmp_path / "movie.mp4"
    widget = PathListInput()
    widget.setPlainText(str(path))
    clicked: list[tuple[object, int]] = []
    widget.itemClicked.connect(lambda row, column: clicked.append((row, column)))

    row = widget._tree.topLevelItem(0)
    widget._on_tree_item_clicked(row, 1)

    assert clicked == [(row, 1)]
    assert widget.selected_paths() == [path.resolve()]


def test_path_list_input_can_opt_in_to_replacing_a_folder_by_selected_direct_children(
    tmp_path: Path,
):
    QApplication.instance() or QApplication([])
    before = tmp_path / "before.txt"
    folder = tmp_path / "collection"
    child_file = folder / "episode-01.mp4"
    child_directory = folder / "extras"
    after = tmp_path / "after.txt"
    folder.mkdir()
    child_file.write_text("content", encoding="utf-8")
    child_directory.mkdir()
    widget = PathListInput(
        show_operation_targets=True,
        double_click_directory_selection=True,
    )
    widget.setPlainText(f"{before}\n{folder}\n{after}")
    folder_row = widget._tree.topLevelItem(1)
    widget._set_item_selected(folder_row, False)
    widget._set_operation_target(folder_row, False)
    propagated_double_clicks: list[tuple[object, int]] = []
    widget.itemDoubleClicked.connect(
        lambda row, column: propagated_double_clicks.append((row, column))
    )
    widget._choose_direct_children = (  # type: ignore[method-assign]
        lambda _directory, _candidates, *, maximum_choices: (child_directory, child_file)
    )

    widget._on_tree_item_double_clicked(folder_row, 1)

    assert widget.paths() == [before.resolve(), child_directory.resolve(), child_file.resolve(), after.resolve()]
    assert widget.selected_paths() == [before.resolve(), after.resolve()]
    assert widget.operation_target_paths() == [before.resolve(), after.resolve()]
    assert propagated_double_clicks == []


def test_direct_child_chooser_checks_only_a_single_available_candidate_by_default(tmp_path: Path):
    app = QApplication.instance() or QApplication([])
    widget = PathListInput()
    child = tmp_path / "only.mp4"
    child.write_bytes(b"test")
    observed: list[list[Qt.CheckState]] = []

    def inspect_and_close() -> None:
        dialog = next(
            candidate
            for candidate in app.topLevelWidgets()
            if isinstance(candidate, QDialog) and candidate.windowTitle() == "直下項目を選択"
        )
        choices = dialog.findChild(QTreeWidget)
        assert choices is not None
        observed.append(
            [choices.topLevelItem(index).checkState(0) for index in range(choices.topLevelItemCount())]
        )
        dialog.reject()

    QTimer.singleShot(0, inspect_and_close)
    assert widget._choose_direct_children(tmp_path, (child,), maximum_choices=None) is None
    assert observed == [[Qt.CheckState.Checked]]


def test_path_list_double_click_child_selection_cannot_change_a_locked_list(tmp_path: Path):
    QApplication.instance() or QApplication([])
    folder = tmp_path / "collection"
    child = folder / "episode-01.mp4"
    folder.mkdir()
    child.write_text("content", encoding="utf-8")
    widget = PathListInput(double_click_directory_selection=True)
    widget.setPlainText(str(folder))
    folder_row = widget._tree.topLevelItem(0)
    widget.set_paths_locked(True)
    propagated_double_clicks: list[tuple[object, int]] = []
    widget.itemDoubleClicked.connect(
        lambda row, column: propagated_double_clicks.append((row, column))
    )
    widget._choose_direct_children = (  # type: ignore[method-assign]
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("chooser must not open"))
    )

    widget._on_tree_item_double_clicked(folder_row, 1)

    assert widget.paths() == [folder.resolve()]
    assert propagated_double_clicks == [(folder_row, 1)]


def test_path_list_input_keeps_an_explicit_virtual_row_out_of_path_apis(tmp_path: Path):
    QApplication.instance() or QApplication([])
    path = tmp_path / "movie.mp4"
    widget = PathListInput()
    widget.setPlainText(str(path))
    widget.append_virtual_item("manual:one", "仮登録: 未収集作品")

    assert widget.paths() == [path.resolve()]
    assert widget.selected_paths() == [path.resolve()]
    assert widget.selected_item_tokens() == [str(path.resolve()), "manual:one"]


def test_path_list_input_toggles_selection_from_anywhere_in_its_selection_cell(tmp_path: Path):
    app = QApplication.instance() or QApplication([])
    first = tmp_path / "first.txt"
    first.write_text("content", encoding="utf-8")
    widget = PathListInput()
    widget.setPlainText(str(first))
    widget.show()
    app.processEvents()

    item = widget._tree.topLevelItem(0)
    row = widget._tree.visualItemRect(item)
    QTest.mouseClick(
        widget._tree.viewport(),
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
        QPoint(2, row.center().y()),
    )

    assert not widget._item_is_selected(item)
    assert widget.selected_items() == []
    selection_button = widget._tree.itemWidget(item, 0)
    assert selection_button is not None
    assert selection_button.text() == ""


def test_path_list_can_keep_selection_and_execution_targets_separate(tmp_path: Path):
    QApplication.instance() or QApplication([])
    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    widget = PathListInput(show_operation_targets=True)
    widget.setPlainText(f"{first}\n{second}")

    widget.clear_item_selection()
    widget._set_operation_target(widget._tree.topLevelItem(1), False)

    assert widget.selected_items() == []
    assert widget.operation_target_paths() == [first.resolve()]
    assert widget.operation_target_count() == 1


def test_path_list_input_can_remove_or_retain_checked_rows_without_touching_paths(tmp_path: Path):
    QApplication.instance() or QApplication([])
    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    third = tmp_path / "third.txt"
    first.write_text("first", encoding="utf-8")
    third.write_text("third", encoding="utf-8")
    widget = PathListInput()
    widget.setPlainText(f"{first}\n{second}\n{third}")
    widget.clear_item_selection()
    widget._set_item_selected(widget._tree.topLevelItem(0), True)
    widget._set_item_selected(widget._tree.topLevelItem(2), True)

    widget.retain_checked_items()

    assert widget.paths() == [first.resolve(), third.resolve()]
    assert first.exists()
    assert third.exists()
    widget.remove_checked_items()
    assert widget.items() == []


def test_path_list_input_can_remove_blue_selected_rows_without_deleting_paths(tmp_path: Path):
    QApplication.instance() or QApplication([])
    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    first.write_text("first", encoding="utf-8")
    second.write_text("second", encoding="utf-8")
    widget = PathListInput(enable_row_selection=True)
    widget.setPlainText(f"{first}\n{second}")
    widget._tree.topLevelItem(1).setSelected(True)

    assert widget.remove_row_selected_items() == 1
    assert widget.paths() == [first.resolve()]
    assert first.exists()
    assert second.exists()


def test_path_list_input_checks_new_rows_by_default_and_can_select_or_clear_all():
    QApplication.instance() or QApplication([])
    widget = PathListInput()
    widget.setPlainText("/tmp/first\n/tmp/second")

    assert widget.selected_items() == ["/tmp/first", "/tmp/second"]
    assert widget.selection_toggle_button.text() == "全解除"
    widget.clear_item_selection()
    assert widget.selected_items() == []
    assert widget.selection_toggle_button.text() == "全選択"
    widget.toggle_item_selection()
    assert widget.selected_items() == ["/tmp/first", "/tmp/second"]
    assert widget.selection_toggle_button.text() == "全解除"


def test_path_list_input_can_remove_duplicates_and_missing_rows(tmp_path: Path):
    QApplication.instance() or QApplication([])
    existing = tmp_path / "existing.txt"
    existing.write_text("content", encoding="utf-8")
    missing = tmp_path / "missing.txt"
    widget = PathListInput()
    widget.setPlainText(f"{existing}\n{existing}\n{missing}")

    widget.remove_duplicate_items()
    assert widget.paths() == [existing.resolve(), missing.resolve()]
    widget.remove_missing_items()

    assert widget.paths() == [existing.resolve()]
    assert existing.exists()


def test_path_list_input_can_clear_all_items(tmp_path: Path):
    QApplication.instance() or QApplication([])
    widget = PathListInput()
    widget.setPlainText(f"{tmp_path / 'first'}\n{tmp_path / 'second'}")

    widget.clear_items()

    assert widget.items() == []
    assert widget.toPlainText() == ""


def test_path_list_input_sorts_by_name_and_path_on_request(tmp_path: Path):
    QApplication.instance() or QApplication([])
    widget = PathListInput()
    first = tmp_path / "zeta.txt"
    second = tmp_path / "alpha.txt"
    widget.setPlainText(f"{first}\n{second}")

    widget.sort_combo.setCurrentIndex(1)  # 名前 ↑
    widget.sort_items()

    assert widget.items() == [str(second), str(first)]

    widget.sort_combo.setCurrentIndex(4)  # パス ↓
    widget.sort_items()
    assert widget.items() == sorted([str(first), str(second)], key=str.casefold, reverse=True)


def test_path_list_input_disables_drag_and_uses_full_cell_selection_buttons():
    QApplication.instance() or QApplication([])
    widget = PathListInput()
    widget.setPlainText("/tmp/first\n/tmp/second")

    assert widget._tree.dragDropMode() == QAbstractItemView.DragDropMode.NoDragDrop
    assert not widget._tree.dragEnabled()
    assert widget._tree.selectionMode() == QAbstractItemView.SelectionMode.NoSelection
    first_button = widget._tree.itemWidget(widget._tree.topLevelItem(0), 0)
    assert first_button is not None and first_button.isCheckable()
    assert widget._tree.itemWidget(
        widget._tree.topLevelItem(widget._tree.topLevelItemCount() - 1), 0
    ) is None


def test_path_list_context_menu_is_available_on_blank_space_and_path_rows(tmp_path: Path):
    app = QApplication.instance() or QApplication([])
    file_path = tmp_path / "item.txt"
    file_path.write_text("content", encoding="utf-8")
    widget = PathListInput()
    widget.setPlainText(str(file_path))
    widget.resize(500, 200)
    widget.show()
    app.processEvents()

    blank_menu = widget._tree._build_path_context_menu(QPoint(1, 400))
    blank_actions = {action.text() for action in blank_menu.actions() if not action.isSeparator()}
    assert {"チェック", "一覧を整理", "並べ替え", "ホームフォルダを開く"} <= blank_actions
    assert "チェック済みフォルダを直下項目へ展開" not in blank_actions
    assert "パスをコピー" not in blank_actions

    path_position = widget._tree.visualRect(widget._tree.indexFromItem(widget._tree.topLevelItem(0), 1)).center()
    path_menu = widget._tree._build_path_context_menu(path_position)
    path_actions = {action.text() for action in path_menu.actions() if not action.isSeparator()}
    assert {"パスをコピー", "標準ファイルマネージャーで開く"} <= path_actions
    assert "一覧から除外" not in path_actions
    widget.close()


def test_path_line_input_and_path_list_use_safe_standard_file_manager_locations(tmp_path: Path):
    QApplication.instance() or QApplication([])
    folder = tmp_path / "folder"
    file_path = folder / "item.txt"
    folder.mkdir()
    file_path.write_text("content", encoding="utf-8")
    missing = tmp_path / "missing"
    line_input = PathLineInput(str(file_path))

    line_actions = {
        action.text() for action in line_input._build_context_menu().actions() if not action.isSeparator()
    }

    assert "標準ファイルマネージャーで開く" in line_actions
    assert standard_file_manager_directory(folder) == folder.resolve()
    assert standard_file_manager_directory(file_path) == folder.resolve()
    assert standard_file_manager_directory(missing).is_dir()
    assert standard_file_manager_directory().is_dir()


def test_path_list_replaces_checked_rows_without_touching_unchecked_rows(tmp_path: Path):
    QApplication.instance() or QApplication([])
    checked_file = tmp_path / "checked.txt"
    checked_folder = tmp_path / "checked-folder"
    unchecked_file = tmp_path / "keep.txt"
    replacement = tmp_path / "child.txt"
    widget = PathListInput()
    widget.setPlainText(f"{checked_file}\n{checked_folder}\n{unchecked_file}")
    widget._set_item_selected(widget._tree.topLevelItem(2), False)

    widget.replace_checked_items([str(replacement)])

    assert widget.paths() == [unchecked_file.resolve(), replacement.resolve()]
    assert not widget._item_is_selected(widget._tree.topLevelItem(0))
    assert widget._item_is_selected(widget._tree.topLevelItem(1))


def test_operation_target_button_updates_its_wording_and_target_membership(tmp_path: Path):
    QApplication.instance() or QApplication([])
    item_path = tmp_path / "item.txt"
    item_path.write_text("content", encoding="utf-8")
    widget = PathListInput(show_operation_targets=True)
    widget.setPlainText(str(item_path))

    button = widget._tree.itemWidget(widget._tree.topLevelItem(0), widget._operation_target_column)
    assert button.text() == "対象"
    button.click()
    assert button.text() == "除外"
    assert widget.operation_target_items() == []
    button.click()
    assert button.text() == "対象"
    assert widget.operation_target_items() == [str(item_path)]


def test_path_list_input_keeps_checkbox_clickable_and_ui_columns_noneditable():
    app = QApplication.instance() or QApplication([])
    widget = PathListInput()
    widget.setPlainText("/tmp/item")
    widget.resize(500, 200)
    widget.show()
    app.processEvents()
    item = widget._tree.topLevelItem(0)

    checkbox_index = widget._tree.indexFromItem(item, 0)
    state_index = widget._tree.indexFromItem(item, 2)
    delegate = widget._tree.itemDelegateForColumn(1)
    assert delegate.createEditor(widget._tree, QStyleOptionViewItem(), checkbox_index) is None
    assert delegate.createEditor(widget._tree, QStyleOptionViewItem(), state_index) is None

    QTest.mouseClick(
        widget._tree.viewport(),
        Qt.MouseButton.LeftButton,
        pos=widget._tree.visualRect(checkbox_index).center(),
    )
    assert not widget._item_is_selected(item)
    widget.close()


def test_directory_only_path_list_rejects_files(tmp_path: Path):
    QApplication.instance() or QApplication([])
    directory = tmp_path / "folder"
    directory.mkdir()
    file_path = tmp_path / "file.txt"
    file_path.write_text("content", encoding="utf-8")
    widget = PathListInput(accepted_path_kind="directory")

    widget.append_items([str(file_path), str(directory)])

    assert widget.paths() == [directory.resolve()]


def test_path_list_input_accepts_an_external_file_drop_without_enabling_row_dragging(
    tmp_path: Path,
):
    QApplication.instance() or QApplication([])
    dropped = tmp_path / "dropped.txt"
    dropped.write_text("content", encoding="utf-8")
    widget = PathListInput()
    mime_data = QMimeData()
    mime_data.setUrls([QUrl.fromLocalFile(str(dropped))])
    event = QDropEvent(
        QPointF(1, 1),
        Qt.DropAction.CopyAction,
        mime_data,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )

    assert widget._tree.viewportEvent(event)
    assert widget.paths() == [dropped.resolve()]
    assert widget._tree.dragDropMode() == QAbstractItemView.DragDropMode.NoDragDrop


def test_directory_only_path_list_uses_a_dropped_file_parent_directory(tmp_path: Path):
    QApplication.instance() or QApplication([])
    directory = tmp_path / "folder"
    directory.mkdir()
    dropped = directory / "dropped.txt"
    dropped.write_text("content", encoding="utf-8")
    widget = PathListInput(accepted_path_kind="directory")
    mime_data = QMimeData()
    mime_data.setUrls([QUrl.fromLocalFile(str(dropped))])
    event = QDropEvent(
        QPointF(1, 1),
        Qt.DropAction.CopyAction,
        mime_data,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )

    assert widget._tree.viewportEvent(event)
    assert widget.paths() == [directory.resolve()]


def test_path_line_input_accepts_a_file_drop_as_its_parent_directory(tmp_path: Path):
    QApplication.instance() or QApplication([])
    directory = tmp_path / "folder"
    directory.mkdir()
    dropped = directory / "dropped.txt"
    dropped.write_text("content", encoding="utf-8")
    widget = PathLineInput(drop_as="directory")
    mime_data = QMimeData()
    mime_data.setUrls([QUrl.fromLocalFile(str(dropped))])
    enter_event = QDragEnterEvent(
        QPoint(1, 1),
        Qt.DropAction.CopyAction,
        mime_data,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    widget.dragEnterEvent(enter_event)
    assert enter_event.isAccepted()
    drop_event = QDropEvent(
        QPointF(1, 1),
        Qt.DropAction.CopyAction,
        mime_data,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    widget.dropEvent(drop_event)

    assert widget.path() == directory.resolve()


def test_path_line_input_can_transform_a_drop_and_report_non_local_data(tmp_path: Path):
    QApplication.instance() or QApplication([])
    directory = tmp_path / "catalogs"
    directory.mkdir()
    video = directory / "clip.mp4"
    video.write_text("content", encoding="utf-8")
    widget = PathLineInput(
        drop_transform=lambda path: path if path.suffix == ".json" else path.parent
    )
    rejected: list[str] = []
    widget.dropRejected.connect(rejected.append)

    path_mime = QMimeData()
    path_mime.setUrls([QUrl.fromLocalFile(str(video))])
    widget.dropEvent(
        QDropEvent(
            QPointF(1, 1),
            Qt.DropAction.CopyAction,
            path_mime,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
    )
    assert widget.path() == directory.resolve()

    text_mime = QMimeData()
    text_mime.setText("not a local path")
    widget.dropEvent(
        QDropEvent(
            QPointF(1, 1),
            Qt.DropAction.CopyAction,
            text_mime,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
    )
    assert rejected == ["ローカルのファイルまたはフォルダのパスだけを受け付けます。"]


def test_configured_drop_replaces_path_list_contents(tmp_path: Path):
    QApplication.instance() or QApplication([])
    existing = tmp_path / "existing.txt"
    dropped = tmp_path / "dropped.txt"
    existing.write_text("", encoding="utf-8")
    dropped.write_text("", encoding="utf-8")
    widget = PathListInput(drop_replaces=True)
    widget.setPlainText(str(existing))

    mime_data = QMimeData()
    mime_data.setUrls([QUrl.fromLocalFile(str(dropped))])
    event = QDropEvent(
        QPointF(1, 1),
        Qt.DropAction.CopyAction,
        mime_data,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )

    assert widget._tree.viewportEvent(event)

    assert widget.paths() == [dropped.resolve()]


def test_path_list_ignores_a_drag_returning_to_its_own_list():
    QApplication.instance() or QApplication([])
    widget = PathListInput(drop_replaces=True)

    class SelfDragEvent:
        def source(self):
            return widget._tree

    class OtherDragEvent:
        def source(self):
            return object()

    assert widget._tree._is_own_path_drag(SelfDragEvent())
    assert not widget._tree._is_own_path_drag(OtherDragEvent())

    own_mime = QMimeData()
    own_mime.setData("application/x-porta-place-path-list-source", widget._tree._own_drag_token)

    class MimeOnlySelfDragEvent:
        def mimeData(self):
            return own_mime

    assert widget._tree._is_own_path_drag(MimeOnlySelfDragEvent())


def test_path_list_parent_ignores_its_own_drag_marker(tmp_path: Path):
    QApplication.instance() or QApplication([])
    existing = tmp_path / "existing.txt"
    existing.write_text("content", encoding="utf-8")
    widget = PathListInput()
    widget.setPlainText(str(existing))
    own_mime = QMimeData()
    own_mime.setUrls([QUrl.fromLocalFile(str(existing))])
    own_mime.setData("application/x-porta-place-path-list-source", widget._tree._own_drag_token)
    event = QDropEvent(
        QPointF(0, 0),
        Qt.DropAction.CopyAction,
        own_mime,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )

    widget.dropEvent(event)

    assert widget.paths() == [existing.resolve()]
    assert not event.isAccepted()


def test_path_list_input_turns_the_trailing_input_row_into_a_path(tmp_path: Path):
    QApplication.instance() or QApplication([])
    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    widget = PathListInput()
    widget.setPlainText(str(first))

    widget._tree.topLevelItem(widget._tree.topLevelItemCount() - 1).setText(1, str(second))

    assert widget.paths() == [first.resolve(), second.resolve()]
    assert widget._tree.topLevelItem(widget._tree.topLevelItemCount() - 1).text(1) == ""


def test_path_list_input_displays_current_file_kind_and_state(tmp_path: Path):
    QApplication.instance() or QApplication([])
    file_path = tmp_path / "file.txt"
    file_path.write_text("content", encoding="utf-8")
    missing = tmp_path / "missing.txt"
    widget = PathListInput()
    widget.setPlainText(f"{file_path}\n{missing}")

    assert widget._tree.topLevelItem(0).text(2) == ""
    assert not widget._tree.topLevelItem(0).icon(2).isNull()
    assert widget._tree.topLevelItem(0).toolTip(2) == "ファイル"
    assert widget._tree.topLevelItem(1).toolTip(2).startswith("存在しない")
    assert widget._tree.textElideMode() == Qt.TextElideMode.ElideLeft
    assert widget._tree.topLevelItem(0).toolTip(1) == str(file_path.resolve())


def test_path_list_input_sizes_fixed_control_columns_from_the_active_style():
    QApplication.instance() or QApplication([])
    widget = PathListInput()
    header = widget._tree.header()

    assert not header.stretchLastSection()
    assert header.sectionResizeMode(0) == QHeaderView.ResizeMode.Fixed
    assert header.sectionResizeMode(1) == QHeaderView.ResizeMode.Stretch
    assert header.sectionResizeMode(2) == QHeaderView.ResizeMode.Fixed
    assert header.sectionResizeMode(3) == QHeaderView.ResizeMode.Fixed
    assert widget._tree.columnWidth(0) == widget._selection_control_size.width()
    assert widget._tree.columnWidth(3) == widget._remove_control_size.width()
    assert widget._selection_control_size.height() >= widget.fontMetrics().lineSpacing()
    assert widget._tree.isHeaderHidden()
    button_texts = {button.text() for button in widget.findChildren(QPushButton)}
    assert {"全選択", "空にする"} <= button_texts


def test_path_list_input_can_use_a_contextual_path_column_label():
    QApplication.instance() or QApplication([])
    widget = PathListInput(path_column_label="出力先パス")

    assert widget._tree.headerItem().text(1) == "出力先パス"


def test_path_list_input_shows_direct_child_counts_in_a_hovered_directory_tooltip(tmp_path: Path):
    QApplication.instance() or QApplication([])
    directory = tmp_path / "directory"
    directory.mkdir()
    (directory / "file-one.txt").write_text("one", encoding="utf-8")
    (directory / "file-two.txt").write_text("two", encoding="utf-8")
    (directory / "child").mkdir()
    widget = PathListInput()
    widget.setPlainText(str(directory))

    item = widget._tree.topLevelItem(0)
    widget._refresh_path_tooltip(item)

    assert item.toolTip(1) == (
        f"{directory.resolve()}\n直下: ファイル 2 件 / フォルダ 1 件"
    )


def test_path_list_input_can_show_app_owned_supplemental_text_without_changing_paths(tmp_path: Path):
    QApplication.instance() or QApplication([])
    file_path = tmp_path / "item.mp4"
    file_path.write_bytes(b"sample")
    widget = PathListInput(supplemental_column_label="項目")
    widget.setPlainText(str(file_path))

    widget.set_supplemental_texts({str(file_path): "拡張子: .mp4 / サイズ: 6 B"})

    row = widget._tree.topLevelItem(0)
    assert widget.paths() == [file_path.resolve()]
    assert widget._tree.columnCount() == 5
    assert row.text(2) == "拡張子: .mp4 / サイズ: 6 B"
    assert row.toolTip(2) == "拡張子: .mp4 / サイズ: 6 B"


def test_path_list_input_supports_multiple_optional_columns_and_path_locking(tmp_path: Path):
    QApplication.instance() or QApplication([])
    file_path = tmp_path / "item.mp4"
    file_path.write_bytes(b"sample")
    widget = PathListInput(supplemental_columns=("拡張子", "解像度", "出力予定"))
    widget.setPlainText(str(file_path))

    widget.set_supplemental_values(
        {str(file_path): {"拡張子": ".mp4", "解像度": "1920×1080", "出力予定": "変更なし"}}
    )
    widget.set_supplemental_column_visible("解像度", False)
    widget.set_paths_locked(True)

    row = widget._tree.topLevelItem(0)
    assert row.text(2) == ".mp4"
    assert row.text(3) == "1920×1080"
    assert not widget.supplemental_column_visible("解像度")
    assert not row.flags() & Qt.ItemFlag.ItemIsEditable
    assert not widget._tree.itemWidget(row, 0).isEnabled()
    assert not widget._tree.itemWidget(row, 6).isEnabled()

    widget.set_paths_locked(False)

    assert row.flags() & Qt.ItemFlag.ItemIsEditable
    assert widget._tree.itemWidget(row, 0).isEnabled()
    assert widget._tree.itemWidget(row, 6).isEnabled()


def test_path_list_input_can_show_column_headers_for_fact_values(tmp_path: Path):
    QApplication.instance() or QApplication([])
    widget = PathListInput(
        supplemental_columns=("解像度", "評価"),
        show_column_headers=True,
    )

    assert not widget._tree.isHeaderHidden()
    assert widget._tree.headerItem().text(1) == "パス"
    assert widget._tree.headerItem().text(2) == "解像度"
    assert widget._tree.headerItem().text(3) == "評価"


def test_path_list_input_can_keep_right_edge_filled_with_resizable_path_column(tmp_path: Path):
    QApplication.instance() or QApplication([])
    file_path = tmp_path / "item.mp4"
    file_path.write_bytes(b"sample")
    widget = PathListInput(
        supplemental_columns=("現在値", "出力予定"),
        path_column_resizable=True,
        stretch_supplemental_column_label="出力予定",
    )
    widget.setPlainText(str(file_path))
    widget.set_paths_locked(True)

    row = widget._tree.topLevelItem(0)

    assert widget._tree.header().sectionResizeMode(1) == QHeaderView.ResizeMode.Interactive
    assert widget._tree.header().sectionResizeMode(3) == QHeaderView.ResizeMode.Stretch
    assert not row.flags() & Qt.ItemFlag.ItemIsEditable


def test_path_list_input_can_fix_a_structural_supplemental_column_on_the_right():
    QApplication.instance() or QApplication([])
    widget = PathListInput(supplemental_columns=("評価", "種別"))

    widget.set_supplemental_column_fixed("種別", 60)

    kind_column = widget.supplemental_column_index("種別")
    assert kind_column is not None
    assert widget._tree.header().sectionResizeMode(kind_column) == QHeaderView.ResizeMode.Fixed
    assert widget._tree.columnWidth(kind_column) == 60


def test_path_list_header_keeps_control_labels_blank_and_column_order_fixed():
    QApplication.instance() or QApplication([])
    widget = PathListInput(show_column_headers=True)

    assert widget._tree.headerItem().text(0) == ""
    assert widget._tree.headerItem().text(widget._remove_column) == ""
    assert not widget._tree.header().sectionsMovable()
    assert not widget._tree.header().cascadingSectionResizes()


def test_directory_for_path_uses_file_parent_and_directory_itself(tmp_path: Path):
    directory = tmp_path / "folder"
    directory.mkdir()
    file_path = directory / "item.txt"
    file_path.write_text("content", encoding="utf-8")

    assert directory_for_path(directory) == directory
    assert directory_for_path(file_path) == directory
