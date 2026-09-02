import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from gui import (
    TextValueWorkspaceDialog,
    TextWorkspaceRow,
    TextWorkspaceSource,
    TextWorkspaceTarget,
)


def _apply_previewed_operation(dialog: TextValueWorkspaceDialog) -> None:
    selected_count, changes, skipped = dialog._planned_operation_changes()
    dialog._apply_operation_changes(changes, selected_count=selected_count, skipped=skipped)


def _choose_operation(dialog: TextValueWorkspaceDialog, category: str, method: str) -> None:
    dialog.operation_combo.setCurrentIndex(dialog.operation_combo.findData(category))
    dialog.operation_method_combo.setCurrentIndex(dialog.operation_method_combo.findData(method))


def test_text_workspace_defaults_to_supplied_source_and_returns_only_nonblank_rows():
    QApplication.instance() or QApplication([])
    dialog = TextValueWorkspaceDialog(
        title="テスト",
        rows=(
            TextWorkspaceRow("one.mp4", "1. one.mp4", {"name": "one", "title": "既存題"}),
            TextWorkspaceRow("two.mp4", "2. two.mp4", {"name": "two", "title": ""}),
        ),
        sources=(TextWorkspaceSource("name", "ファイル名"), TextWorkspaceSource("title", "既存タイトル")),
        targets=(TextWorkspaceTarget("title.official", "正式タイトル"),),
        default_source_key="name",
        default_target_key="title.official",
    )
    try:
        assert dialog.table.item(0, 2).text() == "one"
        assert dialog._target_check_at(0).isChecked()
        assert dialog._target_check_at(1).isChecked()
        assert dialog.table.item(0, 4).text() == "one"
        assert dialog.table.item(1, 4).text() == "two"
        assert dialog.table.item(0, 3).text() == "one"
        assert dialog.table.item(1, 3).text() == "two"
        dialog.source_combo.setCurrentIndex(dialog.source_combo.findData("title"))
        assert dialog.table.item(0, 2).text() == "既存題"
        assert dialog.table.item(0, 3).text() == "one"
        dialog.source_combo.setCurrentIndex(dialog.source_combo.findData("name"))
        dialog._target_check_at(1).setChecked(False)
        assert dialog.table.item(1, 4).text() == "（対象外）"
        _choose_operation(dialog, "replace", "replace_text")
        dialog.find_input.setText("one")
        dialog.replacement_input.setText("ONE")
        assert dialog.table.item(0, 3).text() == "one"
        assert dialog.table.item(0, 4).text() == "ONE"
        dialog._apply_operation_to_output_box()
        assert dialog.table.item(0, 3).text() == "ONE"
        assert dialog.table.item(1, 3).text() == "two"
        dialog.table.item(1, 3).setText("")
        dialog._confirm()

        assert dialog.result is not None
        assert dialog.result.target_key == "title.official"
        assert dialog.result.entries[0].identifier == "one.mp4"
        assert len(dialog.result.entries) == 1
    finally:
        dialog.close()


def test_text_workspace_supports_position_regex_and_character_normalization():
    QApplication.instance() or QApplication([])
    dialog = TextValueWorkspaceDialog(
        title="テスト",
        rows=(TextWorkspaceRow("one.mp4", "1. one.mp4", {"name": "ＡＢ１２３"}),),
        sources=(TextWorkspaceSource("name", "ファイル名"),),
        targets=(TextWorkspaceTarget("title.official", "正式タイトル"),),
        default_source_key="name",
        default_target_key="title.official",
    )
    try:
        _choose_operation(dialog, "normalize", "normalize")
        dialog.normalization_combo.setCurrentIndex(dialog.normalization_combo.findData("letters_half"))
        _apply_previewed_operation(dialog)
        dialog.normalization_combo.setCurrentIndex(dialog.normalization_combo.findData("digits_half"))
        _apply_previewed_operation(dialog)
        assert dialog.table.item(0, 3).text() == "AB123"
        _choose_operation(dialog, "insert", "insert_position")
        dialog.position_input.setValue(2)
        dialog.replacement_input.setText("-")
        _apply_previewed_operation(dialog)
        assert dialog.table.item(0, 3).text() == "AB-123"
        _choose_operation(dialog, "regex", "regex_replace")
        dialog.find_input.setText(r"\d+")
        dialog.replacement_input.setText("番号")
        _apply_previewed_operation(dialog)
        assert dialog.table.item(0, 3).text() == "AB-番号"
    finally:
        dialog.close()


def test_text_workspace_keeps_only_one_immediate_operation_undo():
    QApplication.instance() or QApplication([])
    dialog = TextValueWorkspaceDialog(
        title="テスト",
        rows=(TextWorkspaceRow("one.mp4", "1. one.mp4", {"name": "one"}),),
        sources=(TextWorkspaceSource("name", "ファイル名"),),
        targets=(TextWorkspaceTarget("title.official", "正式タイトル"),),
        default_source_key="name",
        default_target_key="title.official",
    )
    try:
        _choose_operation(dialog, "insert", "insert_end")
        dialog.replacement_input.setText("!")
        _apply_previewed_operation(dialog)
        assert dialog.table.item(0, 3).text() == "one!"
        dialog._undo_last_operation()
        assert dialog.table.item(0, 3).text() == "one"
        assert not dialog.undo_operation_button.isEnabled()
    finally:
        dialog.close()


def test_text_workspace_can_merge_a_selected_field_and_clear_only_the_draft_output():
    QApplication.instance() or QApplication([])
    dialog = TextValueWorkspaceDialog(
        title="テスト",
        rows=(TextWorkspaceRow("one.mp4", "1. one.mp4", {"name": "clip", "series": "第35回"}),),
        sources=(TextWorkspaceSource("name", "ファイル名"), TextWorkspaceSource("series", "シリーズ")),
        targets=(TextWorkspaceTarget("title.official", "正式タイトル"),),
        default_source_key="name",
        default_target_key="title.official",
    )
    try:
        assert dialog.table.item(0, 3).text() == "clip"
        _choose_operation(dialog, "copy", "copy_append")
        assert not dialog.source_combo.isHidden()
        dialog.source_combo.setCurrentIndex(dialog.source_combo.findData("series"))
        dialog.separator_input.setText(" ")
        assert dialog.table.item(0, 3).text() == "clip"
        assert dialog.table.item(0, 4).text() == "clip 第35回"
        dialog._apply_operation_to_output_box()
        assert dialog.table.item(0, 3).text() == "clip 第35回"
        _choose_operation(dialog, "delete", "clear_output")
        assert dialog.source_combo.isHidden()
        assert dialog.table.item(0, 4).text() == ""
        dialog._apply_operation_to_output_box()
        assert dialog.table.item(0, 3).text() == ""
    finally:
        dialog.close()
