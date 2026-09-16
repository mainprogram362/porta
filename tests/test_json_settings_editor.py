import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLabel, QLineEdit, QPushButton

from gui.json_settings_editor import JsonSettingsEditor


def _string_list_validator(text: str) -> object:
    value = json.loads(text)
    if not isinstance(value, dict) or set(value) != {"items"}:
        raise ValueError("itemsだけが必要です")
    if not isinstance(value["items"], list) or not all(
        isinstance(item, str) for item in value["items"]
    ):
        raise ValueError("itemsは文字列配列です")
    return value


def test_normal_view_edits_and_reorders_the_same_json_document() -> None:
    QApplication.instance() or QApplication([])
    editor = JsonSettingsEditor(validate=_string_list_validator)
    editor.setPlainText('{"items": ["first", "second"]}')

    second = editor.field_editor("items[]")
    assert isinstance(second, QLineEdit)
    second.setText("changed\nvalue")

    # The repeated path points to the last rendered row. Its surrounding item
    # contains the matching move buttons.
    item = second.parentWidget()
    assert item is not None
    row = item.parentWidget()
    assert row is not None
    up = next(button for button in row.findChildren(QPushButton) if button.text() == "上へ")
    up.click()

    assert json.loads(editor.toPlainText()) == {
        "items": ["changed\nvalue", "first"]
    }
    assert r"\n" in editor.toPlainText()


def test_raw_mode_remains_direct_and_can_repair_invalid_source() -> None:
    QApplication.instance() or QApplication([])
    editor = JsonSettingsEditor(validate=_string_list_validator)
    editor.setPlainText('{"items": ["first"]}')
    editor.raw_button.click()
    editor.raw_editor.setPlainText('{"items": ["repaired"]}')

    assert editor.toPlainText() == '{"items": ["repaired"]}'
    editor.normal_button.click()
    assert editor.stack.currentIndex() == 0


def test_invalid_raw_source_falls_back_without_losing_source_text() -> None:
    QApplication.instance() or QApplication([])
    editor = JsonSettingsEditor(validate=_string_list_validator)
    invalid = '{"items": [}'
    editor.setPlainText(invalid)

    assert editor.stack.currentIndex() == 1
    assert editor.raw_editor.toPlainText() == invalid
    assert "通常表示を構築できない" in editor.notice.text()


def test_normal_renderer_failure_never_blocks_raw_editor() -> None:
    QApplication.instance() or QApplication([])

    def broken_normal_layer(_text: str) -> object:
        raise RuntimeError("normal renderer failed")

    source = '{"kept": "exact source"}'
    editor = JsonSettingsEditor(validate=broken_normal_layer)
    editor.setPlainText(source)

    assert editor.stack.currentIndex() == 1
    assert editor.raw_editor.toPlainText() == source
    assert editor.toPlainText() == source


def test_unavailable_user_space_shows_read_only_template() -> None:
    QApplication.instance() or QApplication([])
    editor = JsonSettingsEditor(validate=_string_list_validator)
    save = QPushButton("保存")
    template = QPushButton("雛形")
    editor.bind_save_button(save)
    editor.bind_edit_button(template)
    editor.setPlainText('{"items": []}')
    editor.set_source_state("missing_bootstrap", "入口設定がありません。")

    assert editor.isReadOnly()
    assert not save.isEnabled()
    assert not template.isEnabled()
    assert "内蔵雛形" in editor.notice.text()
    try:
        editor.toPlainText()
    except ValueError as exc:
        assert "保存できません" in str(exc)
    else:
        raise AssertionError("read-only fallback template must never be saved")


def test_missing_app_file_template_remains_editable_when_user_space_is_available() -> None:
    QApplication.instance() or QApplication([])
    editor = JsonSettingsEditor(validate=_string_list_validator)
    editor.setPlainText('{"items": []}')
    editor.set_source_state("missing_file", "このアプリの設定ファイルがありません。")

    assert not editor.isReadOnly()
    assert json.loads(editor.toPlainText()) == {"items": []}


def test_consumer_validator_decides_which_type_mismatch_blocks() -> None:
    QApplication.instance() or QApplication([])
    editor = JsonSettingsEditor(validate=_string_list_validator)
    editor.setPlainText('{"items": ["text"]}')
    item = editor.field_editor("items[]")
    assert isinstance(item, QLineEdit)
    item.setText("123")

    # A normal string input does not silently turn digits into a JSON number.
    assert json.loads(editor.toPlainText()) == {"items": ["123"]}


def test_normal_form_shows_exact_key_and_uses_plain_string_input() -> None:
    QApplication.instance() or QApplication([])
    editor = JsonSettingsEditor(validate=_string_list_validator)
    editor.setPlainText('{"items": ["text"]}')

    item = editor.field_editor("items[]")
    assert isinstance(item, QLineEdit)
    assert item.text() == "text"
    labels = [label.text() for label in editor.form_content.findChildren(QLabel)]
    assert "JSONキー: items" in labels
    assert any('保存値: "text"' in text for text in labels)


def test_number_field_rejects_a_string_literal() -> None:
    QApplication.instance() or QApplication([])

    def validate_number(text: str) -> object:
        value = json.loads(text)
        if not isinstance(value.get("count"), int):
            raise ValueError("countは整数です")
        return value

    editor = JsonSettingsEditor(validate=validate_number)
    editor.setPlainText('{"count": 2}')
    count = editor.field_editor("count")
    assert isinstance(count, QLineEdit)
    count.setText('"two"')
    try:
        editor.toPlainText()
    except ValueError as exc:
        assert "型が正しくありません" in str(exc)
    else:
        raise AssertionError("wrong scalar type must block saving")
