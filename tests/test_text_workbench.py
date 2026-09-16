import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from apps.text_tools.text_workbench import (
    TextOperationError,
    TextWorkbenchScreen,
    apply_strong_normalization,
    apply_text_operation,
)
from apps.text_tools.text_workbench.record_bundle_window import RecordBundleWindow
from foundation.record_bundle import TransientRecordBundleStore


def test_html_operations_extract_distinct_titles_and_visible_text():
    html = """
    <html><head><title> Example &amp; Test </title>
    <meta property="og:title" content="Example &amp; Test">
    <meta name="twitter:title" content="別タイトル"></head>
    <body><h1>主見出し</h1><script>secret()</script><p>本文 <b>です</b></p></body></html>
    """

    titles = apply_text_operation(html, "html_titles")
    assert titles.text.splitlines() == ["Example & Test", "別タイトル", "主見出し"]
    assert titles.affected_count == 3

    visible = apply_text_operation(html, "html_to_text")
    assert "主見出し" in visible.text
    assert "本文 です" in visible.text
    assert "secret" not in visible.text


def test_operations_can_be_chained_to_cut_filter_replace_and_deduplicate():
    source = "前置き\nSTART\n商品: りんご\n広告\n商品: みかん\n商品: りんご\nEND\n後書き"
    cut = apply_text_operation(source, "extract_between", "START", "END")
    filtered = apply_text_operation(cut.text, "keep_lines_text", "商品:")
    replaced = apply_text_operation(filtered.text, "replace_text", "商品: ", "")
    unique = apply_text_operation(replaced.text, "unique_lines")

    assert unique.text == "りんご\nみかん"
    assert unique.affected_count == 1


def test_regex_extract_uses_capture_groups_and_reports_invalid_patterns():
    result = apply_text_operation("ID=12 / ID=35", "regex_extract", r"ID=(\d+)")
    assert result.text == "12\n35"

    with pytest.raises(TextOperationError, match="正規表現"):
        apply_text_operation("value", "regex_extract", "[")


def test_missing_cut_marker_does_not_return_an_accidentally_empty_result():
    with pytest.raises(TextOperationError, match="見つかりません"):
        apply_text_operation("残したい本文", "keep_after", "存在しない区切り")


def test_strong_normalization_combines_builtin_and_ordered_custom_rules():
    result = apply_strong_normalization(
        "  ＡＢＣ—ＴＥＳＴ  \n(株)  Sample ",
        custom_rules_text="（株） => 株式会社\nSAMPLE => 見本",
    )
    assert result.text == "abc-test\n株式会社 見本"
    assert "独自ルール2件" in result.summary

    with pytest.raises(TextOperationError, match="置換前 => 置換後"):
        apply_strong_normalization("本文", custom_rules_text="形式が違う行")


def test_workbench_applies_operations_and_keeps_multi_step_undo_redo():
    QApplication.instance() or QApplication([])
    screen = TextWorkbenchScreen(lambda: None)
    try:
        screen.text_editor.setPlainText(" keep \nremove\n keep ")
        screen.select_operation("keep_lines_text")
        screen.parameter_input.setText("keep")
        screen.apply_operation()
        assert screen.text_editor.toPlainText() == " keep \n keep "

        screen.select_operation("trim_lines")
        screen.apply_operation()
        assert screen.text_editor.toPlainText() == "keep\nkeep"
        screen.undo_operation()
        assert screen.text_editor.toPlainText() == " keep \n keep "
        screen.undo_operation()
        assert screen.text_editor.toPlainText() == " keep \nremove\n keep "
        screen.redo_operation()
        assert screen.text_editor.toPlainText() == " keep \n keep "
        screen.restore_initial_text()
        assert screen.text_editor.toPlainText() == " keep \nremove\n keep "
    finally:
        screen.close()


def test_workbench_keeps_working_text_when_an_operation_is_invalid():
    QApplication.instance() or QApplication([])
    screen = TextWorkbenchScreen(lambda: None)
    try:
        screen.text_editor.setPlainText("元の内容")
        screen.select_operation("replace_text")
        screen.parameter_input.clear()
        screen.apply_operation()
        assert screen.text_editor.toPlainText() == "元の内容"
        assert "入力してください" in screen.status_label.text()
    finally:
        screen.close()


def test_record_bundle_window_builds_and_keeps_a_transient_table_when_hidden():
    QApplication.instance() or QApplication([])
    store = TransientRecordBundleStore()
    window = RecordBundleWindow(store, send_to_text_workbench=lambda _text: None)
    try:
        window.offer_source_text(
            "お笑い芸人のライブ集\n20220502\n第３回全国お笑い一回戦\n100mb\n"
            "20230312\n次のライブ\n200mb"
        )
        assert store.bundle is not None
        assert store.bundle.field_names == ("日付", "タイトル", "サイズ")
        assert window.table.rowCount() == 2
        window.create_output()
        assert "20220502" in window.output_preview.toPlainText()

        window.show()
        window.close()
        assert store.bundle is not None
        assert window.isHidden()
    finally:
        window.shutdown()


def test_record_bundle_window_applies_one_field_rule_at_a_time():
    QApplication.instance() or QApplication([])
    store = TransientRecordBundleStore()
    window = RecordBundleWindow(store, send_to_text_workbench=lambda _text: None)
    try:
        window.source_editor.setPlainText("key1\nred green\nkey2\nblue yellow")
        window.first_line_title_check.setChecked(False)
        window.lines_per_record_spin.setValue(2)
        window.field_count_spin.setValue(3)
        window.field_names_input.setText("キー, 色1, 色2")
        window.split_records()

        window.rule_field_combo.setCurrentIndex(window.rule_field_combo.findData("色1"))
        window.rule_method_combo.setCurrentIndex(window.rule_method_combo.findData("split"))
        window.rule_line_spin.setValue(2)
        window.rule_argument_input.clear()
        window.rule_index_spin.setValue(1)
        window.apply_field_rule()

        window.rule_field_combo.setCurrentIndex(window.rule_field_combo.findData("色2"))
        window.rule_method_combo.setCurrentIndex(window.rule_method_combo.findData("split"))
        window.rule_line_spin.setValue(2)
        window.rule_argument_input.clear()
        window.rule_index_spin.setValue(2)
        window.apply_field_rule()

        assert store.bundle is not None
        assert [row.values for row in store.bundle.rows] == [
            ("key1", "red", "green"),
            ("key2", "blue", "yellow"),
        ]
    finally:
        window.shutdown()


def test_inline_record_structure_colors_fields_and_allows_partial_manual_fix_later():
    QApplication.instance() or QApplication([])
    received = []
    screen = TextWorkbenchScreen(lambda: None)
    screen.set_record_bundle_callback(received.append)
    try:
        screen.text_editor.setPlainText(
            "束名\n20220502\n第1回\n100mb\n20230312\n第2回"
        )
        panel = screen.record_structure_panel
        panel.lines_spin.setValue(3)
        panel.split_source()

        assert panel.output_button.isEnabled()
        assert "空欄1セル" in panel.validation_label.text()
        assert len(screen.text_editor.extraSelections()) >= 6

        panel.send_to_output()
        assert len(received) == 1
        assert received[0].rows[1].values == ("20230312", "第2回", "")
    finally:
        screen.close()


def test_inline_record_structure_blocks_a_field_missing_from_every_record():
    QApplication.instance() or QApplication([])
    screen = TextWorkbenchScreen(lambda: None)
    try:
        screen.text_editor.setPlainText("A\nB\nC\nD")
        panel = screen.record_structure_panel
        panel.lines_spin.setValue(2)
        panel.title_check.setChecked(False)
        panel.split_source()
        panel.field_count_spin.setValue(3)
        panel.prepare_fields()

        assert not panel.output_button.isEnabled()
        assert "全レコードで空" in panel.validation_label.text()
    finally:
        screen.close()


def test_inline_record_structure_blocks_a_delimiter_that_never_matches():
    QApplication.instance() or QApplication([])
    screen = TextWorkbenchScreen(lambda: None)
    try:
        screen.text_editor.setPlainText("A\nB\nC\nD")
        panel = screen.record_structure_panel
        panel.split_mode_combo.setCurrentIndex(
            panel.split_mode_combo.findData("contains_text")
        )
        panel.boundary_input.setText("ID:")
        panel.split_source()

        assert panel._split_result is None
        assert not panel.output_button.isEnabled()
        assert "一致する行がありません" in panel.status_label.text()
    finally:
        screen.close()
