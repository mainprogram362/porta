"""Interactive, non-persistent workspace for progressive text processing."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtGui import QTextOption
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from foundation.filesystem import detect_encoding
from records.record_bundle import RecordBundle
from gui import AppHeader, AppPageLayout, NoWheelComboBox
from gui.layout_policy import set_text_rows

from .operations import (
    TextOperationError,
    apply_strong_normalization,
    apply_text_operation,
)
from .record_structure_panel import RecordStructurePanel


@dataclass(frozen=True)
class _OperationChoice:
    key: str
    label: str
    parameter_label: str = ""
    parameter_hint: str = ""
    replacement_label: str = ""
    replacement_hint: str = ""
    supports_case: bool = False


_OPERATIONS = (
    _OperationChoice("html_titles", "HTMLのタイトル・見出し", supports_case=True),
    _OperationChoice("html_to_text", "HTMLタグを除去して本文化"),
    _OperationChoice(
        "regex_extract",
        "正規表現に一致した値",
        "正規表現",
        r"例: <title>(.*?)</title>（グループがあればグループ部分を出力）",
        supports_case=True,
    ),
    _OperationChoice("extract_urls", "URL", supports_case=True),
    _OperationChoice(
        "extract_between",
        "開始文字と終了文字の間",
        "開始文字",
        "例: <title>",
        "終了文字",
        "例: </title>",
        True,
    ),
    _OperationChoice(
        "keep_lines_text",
        "文字を含む行だけ残す",
        "含む文字",
        "この文字を含む行だけ残します",
        supports_case=True,
    ),
    _OperationChoice(
        "remove_lines_text",
        "文字を含む行を除外",
        "含む文字",
        "この文字を含む行を削ります",
        supports_case=True,
    ),
    _OperationChoice(
        "keep_lines_regex",
        "正規表現に一致する行だけ残す",
        "正規表現",
        r"例: ^商品名[:：]",
        supports_case=True,
    ),
    _OperationChoice(
        "remove_lines_regex",
        "正規表現に一致する行を除外",
        "正規表現",
        r"例: ^広告|スポンサー",
        supports_case=True,
    ),
    _OperationChoice(
        "keep_before",
        "最初の区切りより前だけ残す",
        "区切り文字",
        "区切り文字自体は残しません",
        supports_case=True,
    ),
    _OperationChoice(
        "keep_after",
        "最初の区切りより後だけ残す",
        "区切り文字",
        "区切り文字自体は残しません",
        supports_case=True,
    ),
    _OperationChoice(
        "replace_text",
        "文字列を置換",
        "置換前",
        "探す文字列",
        "置換後",
        "空欄なら削除",
        True,
    ),
    _OperationChoice(
        "replace_regex",
        "正規表現で置換",
        "正規表現",
        r"例: \s+",
        "置換後",
        r"\1 などのグループ参照も使用可能",
        True,
    ),
    _OperationChoice("trim_lines", "各行の前後空白を除去"),
    _OperationChoice("remove_blank_lines", "空行を除去"),
    _OperationChoice("unique_lines", "重複行を除去", supports_case=True),
    _OperationChoice("sort_lines", "行を昇順に並べ替え", supports_case=True),
    _OperationChoice("normalize_spaces", "行内の連続空白を1つにする"),
    _OperationChoice("normalize_nfkc", "全角英数字・互換文字を標準化"),
    _OperationChoice("strong_normalize", "強い正規化で表記を統一"),
)

_OPERATION_CATEGORIES = (
    (
        "extract",
        "抽出（文章全体から探す）",
        ("html_titles", "html_to_text", "regex_extract", "extract_urls", "extract_between"),
    ),
    (
        "line_filter",
        "行の選別（1行ずつ判定）",
        ("keep_lines_text", "remove_lines_text", "keep_lines_regex", "remove_lines_regex"),
    ),
    (
        "line_cleanup",
        "行の整理（1行ずつ加工・並べ替え）",
        ("trim_lines", "remove_blank_lines", "unique_lines", "sort_lines", "normalize_spaces"),
    ),
    (
        "replace_cut",
        "置換・カット（行を問わない）",
        ("keep_before", "keep_after", "replace_text", "replace_regex"),
    ),
    (
        "normalize",
        "表記の統一",
        ("normalize_nfkc", "strong_normalize"),
    ),
)

_OPERATION_CATEGORY_BY_KEY = {
    operation_key: category_key
    for category_key, _label, operation_keys in _OPERATION_CATEGORIES
    for operation_key in operation_keys
}


def _text_size(value: str) -> str:
    lines = 0 if not value else value.count("\n") + 1
    return f"{len(value):,}文字 / {lines:,}行"


class TextWorkbenchScreen(QWidget):
    """Apply inspectable transformations to an in-memory working copy."""

    _HISTORY_LIMIT = 50

    def describe_work_state(self):
        if self.text_editor.toPlainText() or self._undo_stack or self._redo_stack or self._initial_text:
            return {"level": 3, "reason": "文章・加工結果・取り消し履歴があります。保存状況を確認してください。"}
        return {"level": 2, "reason": "文章は空です。加工条件や対応表の設定を確認してください。"}

    def __init__(self, return_to_main: Callable[[], None]) -> None:
        super().__init__()
        self._return_to_main = return_to_main
        self._open_record_bundle: Callable[[RecordBundle], None] | None = None
        self._undo_stack: list[str] = []
        self._redo_stack: list[str] = []
        self._initial_text: str | None = None
        self._setting_working_text = False
        self._build_ui()

    def _build_ui(self) -> None:
        layout = AppPageLayout(self)
        header = AppHeader(self._return_to_main, title="テキスト加工ワークベンチ")
        header.content_layout.addStretch(1)
        header.content_layout.addWidget(QLabel("履歴・入力内容は保存しません"))
        layout.addWidget(header)

        guidance = QLabel(
            "テキストを貼り付けるかファイルから読み込み、そのまま加工を重ねます。"
            "最初の加工直前の内容へ復元でき、各操作も最大50段階まで戻せます。"
        )
        guidance.setWordWrap(True)
        layout.addWidget(guidance)

        work_box = QGroupBox("1. テキスト作業欄（直接編集可能）")
        work_layout = QVBoxLayout(work_box)
        self.text_editor = QPlainTextEdit()
        self.text_editor.setPlaceholderText(
            "Webページからコピーした文章、HTMLソース、ログ、一覧などを貼り付けます。"
        )
        self.text_editor.setWordWrapMode(QTextOption.WrapMode.NoWrap)
        self.text_editor.textChanged.connect(self._text_changed)
        work_layout.addWidget(self.text_editor, 1)
        work_actions = QHBoxLayout()
        load_button = QPushButton("テキストファイルを追加…")
        load_button.setToolTip("選んだファイルを読み取り、作業欄の末尾へ追加します。")
        load_button.clicked.connect(self.load_files)
        work_actions.addWidget(load_button)
        copy_button = QPushButton("クリップボードへコピー")
        copy_button.clicked.connect(self.copy_result)
        work_actions.addWidget(copy_button)
        save_button = QPushButton("UTF-8で保存…")
        save_button.setToolTip(
            "明示的に選んだファイルへ、現在の作業結果だけを書き込みます。"
        )
        save_button.clicked.connect(self.save_result)
        work_actions.addWidget(save_button)
        work_actions.addStretch(1)
        self.working_count_label = QLabel(_text_size(""))
        work_actions.addWidget(self.working_count_label)
        work_layout.addLayout(work_actions)
        layout.addWidget(work_box, 1)

        operation_box = QGroupBox("2. 作業欄へ加える操作")
        operation_layout = QVBoxLayout(operation_box)
        choice_row = QHBoxLayout()
        choice_row.addWidget(QLabel("分類"))
        self.operation_category_combo = NoWheelComboBox()
        for category_key, label, _operation_keys in _OPERATION_CATEGORIES:
            self.operation_category_combo.addItem(label, category_key)
        self.operation_category_combo.currentIndexChanged.connect(
            self._operation_category_changed
        )
        choice_row.addWidget(self.operation_category_combo)
        choice_row.addWidget(QLabel("操作"))
        self.operation_combo = NoWheelComboBox()
        self.operation_combo.currentIndexChanged.connect(self._operation_changed)
        choice_row.addWidget(self.operation_combo, 1)
        self.case_sensitive_check = QCheckBox("大文字・小文字を区別")
        self.case_sensitive_check.setChecked(True)
        choice_row.addWidget(self.case_sensitive_check)
        operation_layout.addLayout(choice_row)

        self.parameters_widget = QWidget()
        parameters = QHBoxLayout(self.parameters_widget)
        parameters.setContentsMargins(0, 0, 0, 0)
        self.parameter_label = QLabel()
        parameters.addWidget(self.parameter_label)
        self.parameter_input = QLineEdit()
        parameters.addWidget(self.parameter_input, 1)
        self.replacement_label = QLabel()
        parameters.addWidget(self.replacement_label)
        self.replacement_input = QLineEdit()
        parameters.addWidget(self.replacement_input, 1)
        operation_layout.addWidget(self.parameters_widget)

        self.strong_normalization_widget = QWidget()
        strong_layout = QVBoxLayout(self.strong_normalization_widget)
        strong_layout.setContentsMargins(0, 0, 0, 0)
        strong_options = QHBoxLayout()
        self.strong_width_check = QCheckBox("全角・互換文字を半角寄りへ")
        self.strong_width_check.setChecked(True)
        strong_options.addWidget(self.strong_width_check)
        self.strong_lower_check = QCheckBox("英字を小文字へ")
        self.strong_lower_check.setChecked(True)
        strong_options.addWidget(self.strong_lower_check)
        self.strong_spaces_check = QCheckBox("空白を整理")
        self.strong_spaces_check.setChecked(True)
        strong_options.addWidget(self.strong_spaces_check)
        self.strong_marks_check = QCheckBox("ダッシュ・引用符を統一")
        self.strong_marks_check.setChecked(True)
        strong_options.addWidget(self.strong_marks_check)
        strong_options.addStretch(1)
        strong_layout.addLayout(strong_options)
        self.strong_rules_input = QPlainTextEdit()
        set_text_rows(self.strong_rules_input, minimum=3, maximum=3)
        self.strong_rules_input.setPlaceholderText(
            "独自統一ルール（任意・1行1件・組み込み正規化の後に適用）\n"
            "（株） => 株式会社\nヴァイオリン => バイオリン"
        )
        strong_layout.addWidget(self.strong_rules_input)
        operation_layout.addWidget(self.strong_normalization_widget)

        operation_actions = QHBoxLayout()
        self.apply_button = QPushButton("この操作を適用")
        self.apply_button.clicked.connect(self.apply_operation)
        operation_actions.addWidget(self.apply_button)
        self.undo_button = QPushButton("1段戻す")
        self.undo_button.setEnabled(False)
        self.undo_button.clicked.connect(self.undo_operation)
        operation_actions.addWidget(self.undo_button)
        self.redo_button = QPushButton("1段進める")
        self.redo_button.setEnabled(False)
        self.redo_button.clicked.connect(self.redo_operation)
        operation_actions.addWidget(self.redo_button)
        self.restore_initial_button = QPushButton("加工前へ戻す")
        self.restore_initial_button.setEnabled(False)
        self.restore_initial_button.setToolTip("最初の加工を加える直前のテキストへ戻します。")
        self.restore_initial_button.clicked.connect(self.restore_initial_text)
        operation_actions.addWidget(self.restore_initial_button)
        clear_work_button = QPushButton("作業欄を空にする")
        clear_work_button.clicked.connect(self.clear_working_text)
        operation_actions.addWidget(clear_work_button)
        operation_actions.addStretch(1)
        self.status_label = QLabel(
            "入力内容・作業結果・操作履歴は今回の画面内だけに保持します。"
        )
        self.status_label.setWordWrap(True)
        operation_actions.addWidget(self.status_label, 1)
        operation_layout.addLayout(operation_actions)
        layout.addWidget(operation_box)
        self._operation_category_changed()

        self.record_structure_panel = RecordStructurePanel(
            self.text_editor, self._send_record_bundle
        )
        layout.addWidget(self.record_structure_panel)

    def set_record_bundle_callback(
        self, callback: Callable[[RecordBundle], None]
    ) -> None:
        """Attach the launcher-owned transient record workspace."""
        self._open_record_bundle = callback

    def open_record_bundle(self) -> None:
        """Compatibility entry: send the currently validated inline structure."""
        self.record_structure_panel.send_to_output()

    def _send_record_bundle(self, bundle: RecordBundle) -> None:
        if self._open_record_bundle is None:
            self.status_label.setText("一時対応表を開く機能へ接続できません。")
            return
        self._open_record_bundle(bundle)
        self.status_label.setText("手修正・出力用の対応表を別ウィンドウで開きました。")

    def receive_text(self, text: str) -> None:
        """Receive an explicit output from the transient record workspace."""
        self._replace_with_new_input(text)
        self.status_label.setText(
            "一時対応表の出力を受け取りました。ここから再び加工できます。"
        )

    def _current_choice(self) -> _OperationChoice:
        key = self.operation_combo.currentData()
        return next(choice for choice in _OPERATIONS if choice.key == key)

    def select_operation(self, operation_key: str) -> None:
        """Select an operation even when it belongs to another visible category."""
        category_key = _OPERATION_CATEGORY_BY_KEY.get(operation_key)
        if category_key is None:
            raise ValueError(f"未対応のテキスト操作です: {operation_key}")
        category_index = self.operation_category_combo.findData(category_key)
        self.operation_category_combo.setCurrentIndex(category_index)
        operation_index = self.operation_combo.findData(operation_key)
        self.operation_combo.setCurrentIndex(operation_index)

    def _operation_category_changed(self, *_args: object) -> None:
        category_key = self.operation_category_combo.currentData()
        operation_keys = next(
            keys
            for key, _label, keys in _OPERATION_CATEGORIES
            if key == category_key
        )
        self.operation_combo.blockSignals(True)
        self.operation_combo.clear()
        for choice in _OPERATIONS:
            if choice.key in operation_keys:
                self.operation_combo.addItem(choice.label, choice.key)
        self.operation_combo.blockSignals(False)
        self._operation_changed()

    def _operation_changed(self, *_args: object) -> None:
        choice = self._current_choice()
        has_parameter = bool(choice.parameter_label)
        has_replacement = bool(choice.replacement_label)
        self.parameters_widget.setVisible(has_parameter or has_replacement)
        self.parameter_label.setVisible(has_parameter)
        self.parameter_input.setVisible(has_parameter)
        self.parameter_label.setText(choice.parameter_label)
        self.parameter_input.setPlaceholderText(choice.parameter_hint)
        self.replacement_label.setVisible(has_replacement)
        self.replacement_input.setVisible(has_replacement)
        self.replacement_label.setText(choice.replacement_label)
        self.replacement_input.setPlaceholderText(choice.replacement_hint)
        self.case_sensitive_check.setVisible(choice.supports_case)
        self.strong_normalization_widget.setVisible(choice.key == "strong_normalize")

    def _text_changed(self) -> None:
        self.working_count_label.setText(_text_size(self.text_editor.toPlainText()))
        if not self._setting_working_text and self._redo_stack:
            self._redo_stack.clear()
        self._update_history_buttons()
        panel = getattr(self, "record_structure_panel", None)
        if panel is not None:
            panel.invalidate_source()

    def _set_working_text(self, value: str) -> None:
        self._setting_working_text = True
        try:
            self.text_editor.setPlainText(value)
        finally:
            self._setting_working_text = False
        self._update_history_buttons()

    def _remember_and_set(self, value: str) -> bool:
        current = self.text_editor.toPlainText()
        if value == current:
            return False
        self._undo_stack.append(current)
        if len(self._undo_stack) > self._HISTORY_LIMIT:
            del self._undo_stack[0]
        self._redo_stack.clear()
        self._set_working_text(value)
        return True

    def _update_history_buttons(self) -> None:
        self.undo_button.setEnabled(bool(self._undo_stack))
        self.redo_button.setEnabled(bool(self._redo_stack))
        self.restore_initial_button.setEnabled(
            self._initial_text is not None and self.text_editor.toPlainText() != self._initial_text
        )

    def _capture_initial_text(self) -> None:
        if self._initial_text is None:
            self._initial_text = self.text_editor.toPlainText()
            self._update_history_buttons()

    def _replace_with_new_input(self, value: str) -> None:
        self._undo_stack.clear()
        self._redo_stack.clear()
        self._initial_text = None
        self._set_working_text(value)

    def restore_initial_text(self) -> None:
        if self._initial_text is None:
            return
        changed = self._remember_and_set(self._initial_text)
        self.status_label.setText(
            "最初の加工直前の内容へ戻しました。"
            if changed
            else "すでに最初の加工直前の内容です。"
        )

    def clear_working_text(self) -> None:
        self._capture_initial_text()
        changed = self._remember_and_set("")
        self.status_label.setText(
            "作業欄を空にしました。1段戻すことができます。"
            if changed
            else "作業欄は空です。"
        )

    def apply_operation(self) -> None:
        choice = self._current_choice()
        self._capture_initial_text()
        try:
            if choice.key == "strong_normalize":
                result = apply_strong_normalization(
                    self.text_editor.toPlainText(),
                    normalize_width=self.strong_width_check.isChecked(),
                    lowercase=self.strong_lower_check.isChecked(),
                    normalize_spaces=self.strong_spaces_check.isChecked(),
                    normalize_marks=self.strong_marks_check.isChecked(),
                    custom_rules_text=self.strong_rules_input.toPlainText(),
                )
            else:
                result = apply_text_operation(
                    self.text_editor.toPlainText(),
                    choice.key,
                    self.parameter_input.text(),
                    self.replacement_input.text(),
                    case_sensitive=self.case_sensitive_check.isChecked(),
                )
        except TextOperationError as exc:
            self.status_label.setText(str(exc))
            return
        changed = self._remember_and_set(result.text)
        suffix = "" if changed else "（結果は変わりませんでした）"
        self.status_label.setText(f"{result.summary}。{suffix}")

    def undo_operation(self) -> None:
        if not self._undo_stack:
            return
        self._redo_stack.append(self.text_editor.toPlainText())
        self._set_working_text(self._undo_stack.pop())
        self.status_label.setText("直前の加工状態へ1段戻しました。")

    def redo_operation(self) -> None:
        if not self._redo_stack:
            return
        self._undo_stack.append(self.text_editor.toPlainText())
        self._set_working_text(self._redo_stack.pop())
        self.status_label.setText("戻した加工状態を1段進めました。")

    def load_files(self) -> None:
        paths, _selected_filter = QFileDialog.getOpenFileNames(
            self,
            "入力へ追加するテキストファイル",
            "",
            "テキスト・HTML (*.txt *.html *.htm *.csv *.json *.xml *.log);;すべてのファイル (*)",
        )
        if not paths:
            return
        values: list[str] = []
        failures: list[str] = []
        for path_text in paths:
            path = Path(path_text)
            try:
                values.append(path.read_text(encoding=detect_encoding(path), errors="replace"))
            except (OSError, UnicodeError) as exc:
                failures.append(f"{path.name}: {exc}")
        if values:
            current = self.text_editor.toPlainText()
            separator = "\n" if current and not current.endswith("\n") else ""
            self._replace_with_new_input(current + separator + "\n".join(values))
        message = f"{len(values)}件のファイルを作業欄へ追加しました。"
        if failures:
            message += f" {len(failures)}件は読み込めませんでした。"
            QMessageBox.warning(self, "一部のファイルを読み込めません", "\n".join(failures))
        self.status_label.setText(message)

    def copy_result(self) -> None:
        QApplication.clipboard().setText(self.text_editor.toPlainText())
        self.status_label.setText("現在の作業結果をクリップボードへコピーしました。")

    def save_result(self) -> None:
        path_text, _selected_filter = QFileDialog.getSaveFileName(
            self,
            "作業結果を保存",
            "result.txt",
            "テキストファイル (*.txt);;すべてのファイル (*)",
        )
        if not path_text:
            return
        path = Path(path_text)
        try:
            path.write_text(self.text_editor.toPlainText(), encoding="utf-8")
        except OSError as exc:
            QMessageBox.warning(self, "保存できません", str(exc))
            self.status_label.setText(
                "作業結果を保存できませんでした。作業欄は変更していません。"
            )
            return
        self.status_label.setText(f"UTF-8で保存しました: {path}")


def create_screen(return_to_main: Callable[[], None]) -> TextWorkbenchScreen:
    """Factory used by the central launcher catalog."""
    return TextWorkbenchScreen(return_to_main)
