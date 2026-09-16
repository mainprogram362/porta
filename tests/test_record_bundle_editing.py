from foundation.record_bundle import RecordBundle, RecordBundleRow
from foundation.record_bundle_editing import (
    insert_text_into_field,
    remove_characters_from_field,
    sort_bundle_rows,
)


def test_editor_applies_column_edit_and_opens_a_column_text_window():
    from PySide6.QtWidgets import QApplication, QPlainTextEdit
    from apps.text_tools.text_workbench.record_bundle_window import RecordBundleWindow
    from foundation.record_bundle import TransientRecordBundleStore

    application = QApplication.instance() or QApplication([])
    editor = RecordBundleWindow(TransientRecordBundleStore(), send_to_text_workbench=lambda _text: None)
    try:
        editor.load_prepared_bundle(_bundle())
        editor.batch_field_combo.setCurrentIndex(editor.batch_field_combo.findData(1))
        editor.batch_edit_combo.setCurrentIndex(editor.batch_edit_combo.findData("insert"))
        editor.batch_position_spin.setValue(2)
        editor.batch_insert_input.setText("_")
        editor.apply_batch_field_edit()
        assert [row.values[1] for row in editor._store.bundle.rows] == ["c_cc", "a_", "b_b"]

        editor.open_selected_field_as_text()
        assert len(editor._field_text_windows) == 1
        text_window = next(iter(editor._field_text_windows))
        assert text_window.findChild(QPlainTextEdit).toPlainText() == "c_cc\na_\nb_b"
        text_window.close()
        application.processEvents()
    finally:
        editor.shutdown()
        editor.deleteLater()


def _bundle() -> RecordBundle:
    return RecordBundle(
        "table", ("番号", "名前"), (
            RecordBundleRow("three", ("3", "ccc")),
            RecordBundleRow("ten", ("10", "a")),
            RecordBundleRow("two", ("2", "bb")),
        )
    )


def test_sort_orders_complete_rows_and_keeps_identifiers():
    result = sort_bundle_rows(_bundle(), 0, mode="number")
    assert [(row.identifier, row.values) for row in result.rows] == [
        ("two", ("2", "bb")), ("three", ("3", "ccc")), ("ten", ("10", "a")),
    ]
    assert [row.identifier for row in sort_bundle_rows(_bundle(), 1, mode="length", descending=True).rows] == [
        "three", "two", "ten",
    ]


def test_batch_character_edits_are_applied_to_one_field_only():
    removed = remove_characters_from_field(_bundle(), 1, position=2, count=2)
    assert [row.values for row in removed.rows] == [("3", "c"), ("10", "a"), ("2", "b")]
    inserted = insert_text_into_field(removed, 1, position=2, text="_")
    assert [row.values for row in inserted.rows] == [("3", "c_"), ("10", "a_"), ("2", "b_")]


def test_batch_edits_validate_their_input():
    try:
        insert_text_into_field(_bundle(), 1, position=0, text="x")
    except ValueError as error:
        assert "1以上" in str(error)
    else:
        raise AssertionError("expected invalid position")
