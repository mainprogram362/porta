"""Reusable, non-persistent workspace for preparing one text value per row.

The dialog deliberately does not know about files, JSON, media, or a storage
format.  A caller supplies stable row identifiers, selectable source texts and
allowed target fields; the caller decides what to do with the returned values.
That lets a media patch creator and a later generic text tool share this exact
editing surface.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)


@dataclass(frozen=True)
class TextWorkspaceSource:
    """One input column which a caller makes available to the user."""

    key: str
    label: str


@dataclass(frozen=True)
class TextWorkspaceTarget:
    """One allowed destination field for the generated text."""

    key: str
    label: str


@dataclass(frozen=True)
class TextWorkspaceRow:
    """One editable row with a caller-owned stable matching identifier."""

    identifier: str
    label: str
    source_values: dict[str, str]


@dataclass(frozen=True)
class TextWorkspaceEntry:
    """One non-blank result, still independent from any patch format."""

    identifier: str
    value: str


@dataclass(frozen=True)
class TextWorkspaceResult:
    """The selected target, source and per-row output values."""

    target_key: str
    source_key: str
    entries: tuple[TextWorkspaceEntry, ...]


class TextValueWorkspaceDialog(QDialog):
    """A result-first text workbench with direct editing and scoped operations.

    No operation history is kept.  Every operation changes only the currently
    displayed output cells, and those cells alone become the returned result.
    This makes bulk adjustment inspectable and reversible by direct editing.
    """

    _TARGET_COLUMN = 0
    _IDENTIFIER_COLUMN = 1
    _SOURCE_COLUMN = 2
    _OUTPUT_COLUMN = 3
    _PREDICTION_COLUMN = 4

    def __init__(
        self,
        *,
        title: str,
        rows: tuple[TextWorkspaceRow, ...],
        sources: tuple[TextWorkspaceSource, ...],
        targets: tuple[TextWorkspaceTarget, ...],
        default_source_key: str | None = None,
        default_target_key: str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        if not rows:
            raise ValueError("テキスト作業場には1件以上の行が必要です。")
        if not sources:
            raise ValueError("テキスト作業場には入力元を1つ以上指定してください。")
        if not targets:
            raise ValueError("テキスト作業場には対象項目を1つ以上指定してください。")
        if len({row.identifier for row in rows}) != len(rows) or any(not row.identifier.strip() for row in rows):
            raise ValueError("テキスト作業場の行識別子が空または重複しています。")

        self._rows_by_identifier = {row.identifier: row for row in rows}
        self._row_order = tuple(row.identifier for row in rows)
        self._sources = sources
        self._targets = targets
        self._result: TextWorkspaceResult | None = None
        self._bulk_updating = False
        self._undo_outputs: dict[str, str] | None = None
        self.setWindowTitle(title)
        self.setMinimumSize(980, 800)
        self._build_ui(rows, default_source_key, default_target_key)

    @property
    def result(self) -> TextWorkspaceResult | None:
        """Return a result only after the user explicitly confirms it."""
        return self._result

    def _build_ui(
        self,
        rows: tuple[TextWorkspaceRow, ...],
        default_source_key: str | None,
        default_target_key: str | None,
    ) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(6)

        information_box = QGroupBox("全体情報")
        information_layout = QVBoxLayout(information_box)
        guidance = QLabel(
            "ここでは元データを変更しません。最終的に出力予定欄に入っている文字列だけが結果です。"
            "共通操作の履歴は保存しません。"
        )
        guidance.setWordWrap(True)
        information_layout.addWidget(guidance)
        choices = QHBoxLayout()
        choices.addWidget(QLabel("更新する項目"))
        self.target_combo = QComboBox()
        for target in self._targets:
            self.target_combo.addItem(target.label, target.key)
        target_index = self.target_combo.findData(default_target_key)
        self.target_combo.setCurrentIndex(target_index if target_index >= 0 else 0)
        choices.addWidget(self.target_combo, 1)
        choices.addWidget(QLabel("並び順"))
        self.sort_combo = QComboBox()
        self.sort_combo.addItem("元の順番", "original")
        self.sort_combo.addItem("照合用の識別子（昇順）", "identifier")
        self.sort_combo.addItem("入力元の文字列（昇順）", "source")
        self.sort_combo.addItem("出力予定（昇順）", "output")
        self.sort_combo.addItem("操作の予測（昇順）", "prediction")
        self.sort_combo.currentIndexChanged.connect(self._sort_rows)
        choices.addWidget(self.sort_combo, 1)
        information_layout.addLayout(choices)
        selection_row = QHBoxLayout()
        select_all_button = QPushButton("全選択")
        select_all_button.clicked.connect(lambda: self._set_all_targets(True))
        selection_row.addWidget(select_all_button)
        clear_selection_button = QPushButton("全解除")
        clear_selection_button.clicked.connect(lambda: self._set_all_targets(False))
        selection_row.addWidget(clear_selection_button)
        invert_selection_button = QPushButton("選択を反転")
        invert_selection_button.clicked.connect(self._invert_targets)
        selection_row.addWidget(invert_selection_button)
        selection_row.addStretch(1)
        self.count_label = QLabel()
        selection_row.addWidget(self.count_label)
        information_layout.addLayout(selection_row)
        layout.addWidget(information_box)

        output_box = QGroupBox("出力予定一覧（各行は直接編集できます）")
        output_box.setMinimumHeight(360)
        output_layout = QVBoxLayout(output_box)
        self.table = QTableWidget(len(rows), 5)
        self.table.setHorizontalHeaderLabels(
            ("操作対象", "照合用の識別子", "参照中の項目データ", "出力予定", "操作の予測")
        )
        self.table.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked | QAbstractItemView.EditTrigger.EditKeyPressed
        )
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectItems)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setMinimumHeight(310)
        self.table.horizontalHeader().setStretchLastSection(False)
        self.table.horizontalHeader().setSectionResizeMode(
            self._TARGET_COLUMN, self.table.horizontalHeader().ResizeMode.ResizeToContents
        )
        self.table.horizontalHeader().setSectionResizeMode(
            self._IDENTIFIER_COLUMN, self.table.horizontalHeader().ResizeMode.Stretch
        )
        self.table.horizontalHeader().setSectionResizeMode(
            self._SOURCE_COLUMN, self.table.horizontalHeader().ResizeMode.Stretch
        )
        self.table.horizontalHeader().setSectionResizeMode(
            self._OUTPUT_COLUMN, self.table.horizontalHeader().ResizeMode.Stretch
        )
        self.table.horizontalHeader().setSectionResizeMode(
            self._PREDICTION_COLUMN, self.table.horizontalHeader().ResizeMode.Stretch
        )
        for index, row in enumerate(rows):
            check = QCheckBox()
            check.setChecked(True)
            check.setToolTip("チェック済みの行だけへ、下の共通操作を適用します。")
            check.toggled.connect(self._target_selection_changed)
            self.table.setCellWidget(index, self._TARGET_COLUMN, check)
            identifier = QTableWidgetItem(row.identifier)
            identifier.setFlags(identifier.flags() & ~Qt.ItemFlag.ItemIsEditable)
            identifier.setToolTip(row.label)
            self.table.setItem(index, self._IDENTIFIER_COLUMN, identifier)
            source = QTableWidgetItem()
            source.setFlags(source.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(index, self._SOURCE_COLUMN, source)
            output = QTableWidgetItem()
            output.setToolTip("この行だけの出力予定。空欄ならパッチには入りません。")
            self.table.setItem(index, self._OUTPUT_COLUMN, output)
            prediction = QTableWidgetItem()
            prediction.setFlags(prediction.flags() & ~Qt.ItemFlag.ItemIsEditable)
            prediction.setToolTip("現在選んでいる共通操作を適用した場合の予測です。出力予定はまだ変更しません。")
            self.table.setItem(index, self._PREDICTION_COLUMN, prediction)
        self.table.itemChanged.connect(self._output_changed)
        output_layout.addWidget(self.table, 1)
        layout.addWidget(output_box, 1)

        operation_box = QGroupBox("選択行への共通操作")
        operation_layout = QVBoxLayout(operation_box)
        operation_help = QLabel(
            "操作はチェック済みの行だけへ、現在の出力予定を基準に反映します。"
            "「入力元をコピー」だけは、選んだ入力元の文字列を基準にします。"
        )
        operation_help.setWordWrap(True)
        operation_layout.addWidget(operation_help)
        self.operation_form = QFormLayout()
        self.operation_combo = QComboBox()
        self.operation_combo.addItem("項目データを取り込む", "copy")
        self.operation_combo.addItem("削除", "delete")
        self.operation_combo.addItem("置換", "replace")
        self.operation_combo.addItem("挿入", "insert")
        self.operation_combo.addItem("数字・英字の統一", "normalize")
        self.operation_combo.addItem("正規表現（上級）", "regex")
        self.operation_combo.currentIndexChanged.connect(self._refresh_operation_methods)
        self.operation_form.addRow("操作", self.operation_combo)
        self.operation_method_combo = QComboBox()
        self.operation_method_combo.currentIndexChanged.connect(self._configure_operation_controls)
        self.operation_form.addRow("やり方", self.operation_method_combo)
        self.source_combo = QComboBox()
        for source in self._sources:
            self.source_combo.addItem(source.label, source.key)
        source_index = self.source_combo.findData(default_source_key)
        self.source_combo.setCurrentIndex(source_index if source_index >= 0 else 0)
        self.source_combo.currentIndexChanged.connect(self._source_changed)
        self.operation_form.addRow("取り込む項目", self.source_combo)
        self.find_input = QLineEdit()
        self.find_input.setPlaceholderText("削除・置換する文字列、または正規表現")
        self.find_input.textChanged.connect(self._update_operation_prediction)
        self.operation_form.addRow("検索・対象", self.find_input)
        self.replacement_input = QLineEdit()
        self.replacement_input.setPlaceholderText("置換後、または挿入する文字列")
        self.replacement_input.textChanged.connect(self._update_operation_prediction)
        self.operation_form.addRow("置換・挿入", self.replacement_input)
        self.separator_input = QLineEdit()
        self.separator_input.setPlaceholderText("例: 半角スペース、 - 、第")
        self.separator_input.textChanged.connect(self._update_operation_prediction)
        self.operation_form.addRow("間に入れる文字", self.separator_input)
        self.position_input = QSpinBox()
        self.position_input.setRange(0, 1_000_000)
        self.position_input.setToolTip("先頭を 0 とする文字位置です。文字列より大きい位置は末尾として扱います。")
        self.position_input.valueChanged.connect(self._update_operation_prediction)
        self.operation_form.addRow("位置", self.position_input)
        self.length_input = QSpinBox()
        self.length_input.setRange(0, 1_000_000)
        self.length_input.setValue(1)
        self.length_input.setToolTip("位置範囲操作で扱う文字数です。")
        self.length_input.valueChanged.connect(self._update_operation_prediction)
        self.operation_form.addRow("文字数", self.length_input)
        self.condition_input = QLineEdit()
        self.condition_input.setPlaceholderText("この文字列を含む行だけ（空欄なら全行）")
        self.condition_input.textChanged.connect(self._update_operation_prediction)
        self.operation_form.addRow("適用条件", self.condition_input)
        self.normalization_combo = QComboBox()
        self.normalization_combo.addItem("全角数字を半角へ", "digits_half")
        self.normalization_combo.addItem("半角数字を全角へ", "digits_full")
        self.normalization_combo.addItem("全角英字を半角へ", "letters_half")
        self.normalization_combo.addItem("半角英字を全角へ", "letters_full")
        self.normalization_combo.addItem("英字を小文字へ", "letters_lower")
        self.normalization_combo.addItem("英字を大文字へ", "letters_upper")
        self.normalization_combo.currentIndexChanged.connect(self._update_operation_prediction)
        self.operation_form.addRow("統一方法", self.normalization_combo)
        operation_layout.addLayout(self.operation_form)
        operation_buttons = QHBoxLayout()
        self.apply_operation_button = QPushButton("出力予定へ反映")
        self.apply_operation_button.setToolTip("操作結果をそのまま出力予定一覧へ入れます。必要なら一覧を直接編集してください。")
        self.apply_operation_button.clicked.connect(self._apply_operation_to_output_box)
        operation_buttons.addWidget(self.apply_operation_button)
        self.undo_operation_button = QPushButton("ひとつ前に戻す")
        self.undo_operation_button.setToolTip("直前に確定した共通操作の前の出力予定へ戻します。手入力後は使えません。")
        self.undo_operation_button.setEnabled(False)
        self.undo_operation_button.clicked.connect(self._undo_last_operation)
        operation_buttons.addWidget(self.undo_operation_button)
        operation_buttons.addStretch(1)
        self.operation_status_label = QLabel()
        self.operation_status_label.setStyleSheet("font-weight: 600;")
        operation_buttons.addWidget(self.operation_status_label)
        operation_layout.addLayout(operation_buttons)
        layout.addWidget(operation_box)

        buttons = QDialogButtonBox()
        buttons.addButton("戻る", QDialogButtonBox.ButtonRole.RejectRole).clicked.connect(self.reject)
        buttons.addButton("この内容を確定", QDialogButtonBox.ButtonRole.AcceptRole).clicked.connect(self._confirm)
        layout.addWidget(buttons)
        self._refresh_source_column()
        self._initialize_outputs_from_source()
        self._refresh_operation_methods()
        self._configure_operation_controls()
        self._refresh_count()

    def _source_changed(self) -> None:
        """Change only the available source material, never an edited result."""
        self._refresh_source_column()
        self._update_operation_prediction()

    def _initialize_outputs_from_source(self) -> None:
        """Give a filename-based workbench a useful editable starting result once."""
        self._bulk_updating = True
        try:
            for index in range(self.table.rowCount()):
                source_item = self.table.item(index, self._SOURCE_COLUMN)
                output_item = self.table.item(index, self._OUTPUT_COLUMN)
                assert source_item is not None and output_item is not None
                output_item.setText(source_item.text())
        finally:
            self._bulk_updating = False

    def _refresh_source_column(self) -> None:
        source_key = str(self.source_combo.currentData() or "")
        self._bulk_updating = True
        try:
            for index in range(self.table.rowCount()):
                identifier = self._identifier_at(index)
                source_item = self.table.item(index, self._SOURCE_COLUMN)
                assert source_item is not None
                source_item.setText(self._rows_by_identifier[identifier].source_values.get(source_key, ""))
        finally:
            self._bulk_updating = False

    def _sort_rows(self) -> None:
        kind = str(self.sort_combo.currentData() or "original")
        if kind == "original":
            self._sort_by_key(lambda identifier: self._row_order.index(identifier))
            return
        column = {
            "identifier": self._IDENTIFIER_COLUMN,
            "source": self._SOURCE_COLUMN,
            "output": self._OUTPUT_COLUMN,
            "prediction": self._PREDICTION_COLUMN,
        }[kind]
        self.table.sortItems(column, Qt.SortOrder.AscendingOrder)

    def _sort_by_key(self, key) -> None:
        """Restore caller order without tying state to current visual row numbers."""
        rows: list[tuple[str, str, bool]] = []
        for index in range(self.table.rowCount()):
            identifier = self._identifier_at(index)
            output_item = self.table.item(index, self._OUTPUT_COLUMN)
            check = self._target_check_at(index)
            rows.append((identifier, output_item.text() if output_item is not None else "", check.isChecked()))
        rows.sort(key=lambda row: key(row[0]))
        self._bulk_updating = True
        try:
            for index, (identifier, output, checked) in enumerate(rows):
                identifier_item = self.table.item(index, self._IDENTIFIER_COLUMN)
                source_item = self.table.item(index, self._SOURCE_COLUMN)
                output_item = self.table.item(index, self._OUTPUT_COLUMN)
                assert identifier_item is not None and source_item is not None and output_item is not None
                identifier_item.setText(identifier)
                source_item.setText(
                    self._rows_by_identifier[identifier].source_values.get(
                        str(self.source_combo.currentData() or ""), ""
                    )
                )
                output_item.setText(output)
                self._target_check_at(index).setChecked(checked)
        finally:
            self._bulk_updating = False
        self._refresh_count()
        self._update_operation_prediction()

    def _target_selection_changed(self, *_unused: object) -> None:
        if self._bulk_updating:
            return
        self._refresh_count()
        self._update_operation_prediction()

    def _set_all_targets(self, checked: bool) -> None:
        self._bulk_updating = True
        try:
            for index in range(self.table.rowCount()):
                self._target_check_at(index).setChecked(checked)
        finally:
            self._bulk_updating = False
        self._refresh_count()
        self._update_operation_prediction()

    def _invert_targets(self) -> None:
        self._bulk_updating = True
        try:
            for index in range(self.table.rowCount()):
                check = self._target_check_at(index)
                check.setChecked(not check.isChecked())
        finally:
            self._bulk_updating = False
        self._refresh_count()
        self._update_operation_prediction()

    def _clear_outputs(self) -> None:
        self._bulk_updating = True
        try:
            for index in range(self.table.rowCount()):
                output_item = self.table.item(index, self._OUTPUT_COLUMN)
                assert output_item is not None
                output_item.setText("")
        finally:
            self._bulk_updating = False
        self._refresh_count()
        self._update_operation_prediction()

    def _refresh_operation_methods(self) -> None:
        """Choose a specific method only after the user chose its large operation group."""
        category = str(self.operation_combo.currentData() or "copy")
        methods = {
            "copy": (
                ("出力予定を上書き", "copy_replace"),
                ("出力予定の先頭へ追加", "copy_prepend"),
                ("出力予定の末尾へ追加", "copy_append"),
            ),
            "delete": (
                ("一致した文字列を削除", "delete_text"),
                ("位置範囲を削除", "delete_range"),
                ("出力予定を空にする", "clear_output"),
            ),
            "replace": (
                ("一致した文字列を置換", "replace_text"),
                ("位置範囲を置換", "replace_range"),
            ),
            "insert": (
                ("先頭へ挿入", "insert_start"),
                ("末尾へ挿入", "insert_end"),
                ("位置を指定して挿入", "insert_position"),
            ),
            "normalize": (("数字・英字の表記を統一", "normalize"),),
            "regex": (("正規表現で置換", "regex_replace"),),
        }[category]
        self.operation_method_combo.blockSignals(True)
        try:
            self.operation_method_combo.clear()
            for label, value in methods:
                self.operation_method_combo.addItem(label, value)
        finally:
            self.operation_method_combo.blockSignals(False)
        self._configure_operation_controls()

    def _selected_operation_kind(self) -> str:
        return str(self.operation_method_combo.currentData() or "")

    def _configure_operation_controls(self) -> None:
        kind = self._selected_operation_kind()
        is_copy = kind.startswith("copy_")
        is_regex = kind == "regex_replace"
        is_normalize = kind == "normalize"
        is_range = kind in {"delete_range", "replace_range"}
        is_position_insert = kind == "insert_position"
        needs_find = kind in {"delete_text", "replace_text", "regex_replace"}
        needs_replacement = kind in {"replace_text", "replace_range", "insert_start", "insert_end", "insert_position", "regex_replace"}
        needs_separator = kind in {"copy_prepend", "copy_append"}
        self._set_operation_widget_visible(self.source_combo, is_copy)
        self._set_operation_widget_visible(self.find_input, needs_find)
        self._set_operation_widget_visible(self.replacement_input, needs_replacement)
        self._set_operation_widget_visible(self.separator_input, needs_separator)
        self._set_operation_widget_visible(self.position_input, is_range or is_position_insert)
        self._set_operation_widget_visible(self.length_input, is_range)
        self._set_operation_widget_visible(self.condition_input, not is_regex)
        self._set_operation_widget_visible(self.normalization_combo, is_normalize)
        labels = {
            "copy_replace": "選んだ項目データで、出力予定を上書きします。",
            "copy_prepend": "選んだ項目データを、出力予定の先頭へ追加します。",
            "copy_append": "選んだ項目データを、出力予定の末尾へ追加します。",
            "delete_text": "検索・対象に一致する文字列を、全て削除します。",
            "replace_text": "検索・対象に一致する文字列を、置換・挿入の文字列へ全て置き換えます。",
            "delete_range": "位置から文字数分を削除します。",
            "clear_output": "チェック済み行の出力予定を空欄にします。この行は保存するパッチから外れます。",
            "replace_range": "位置から文字数分を、置換・挿入の文字列へ置き換えます。",
            "insert_start": "置換・挿入の文字列を先頭へ追加します。",
            "insert_end": "置換・挿入の文字列を末尾へ追加します。",
            "insert_position": "位置へ置換・挿入の文字列を追加します。",
            "regex_replace": "正規表現だけで置換します。適用条件との併用はできません。",
            "normalize": "選んだ数字・英字だけを統一します。",
        }
        self.operation_status_label.setText(labels.get(kind, ""))
        self.condition_input.setPlaceholderText(
            "この文字列を含む項目データだけ（空欄なら全行）"
            if is_copy
            else "この文字列を含む出力予定だけ（空欄なら全行）"
        )
        self._update_operation_prediction()

    def _set_operation_widget_visible(self, widget: QWidget, visible: bool) -> None:
        """Hide both a form field and its label, avoiding empty operation rows."""
        widget.setVisible(visible)
        label = self.operation_form.labelForField(widget)
        if label is not None:
            label.setVisible(visible)

    def _apply_operation_to_output_box(self) -> None:
        """Put the calculated result directly in the visible output-preview table."""
        try:
            selected_count, changes, skipped = self._planned_operation_changes()
        except ValueError as exc:
            self.operation_status_label.setText(str(exc))
            return
        if not changes:
            suffix = f" / 条件外 {skipped} 件" if skipped else ""
            self.operation_status_label.setText(f"チェック済み {selected_count} 件に変更はありません{suffix}")
            return
        self._apply_operation_changes(changes, selected_count=selected_count, skipped=skipped)

    def _planned_operation_changes(self) -> tuple[int, list[tuple[str, str, str]], int]:
        """Calculate changes from current visible values without mutating them."""
        selected_count, results, skipped = self._planned_operation_results()
        return selected_count, [result for result in results if result[1] != result[2]], skipped

    def _planned_operation_results(self) -> tuple[int, list[tuple[str, str, str]], int]:
        """Calculate a result for every selected row, including unchanged rows."""
        selected_rows = [index for index in range(self.table.rowCount()) if self._target_check_at(index).isChecked()]
        if not selected_rows:
            raise ValueError("操作対象の行を1件以上チェックしてください。")
        kind = self._selected_operation_kind()
        is_copy = kind.startswith("copy_")
        transform = None if is_copy else self._operation_transform(kind)
        condition = "" if kind == "regex_replace" else self.condition_input.text()
        results: list[tuple[str, str, str]] = []
        skipped = 0
        for index in selected_rows:
            identifier = self._identifier_at(index)
            source_item = self.table.item(index, self._SOURCE_COLUMN)
            output_item = self.table.item(index, self._OUTPUT_COLUMN)
            assert source_item is not None and output_item is not None
            before = output_item.text()
            operation_input = source_item.text() if is_copy else before
            if condition and condition not in operation_input:
                skipped += 1
                results.append((identifier, before, before))
                continue
            updated = (
                self._merge_source_into_output(kind, source_item.text(), before)
                if is_copy
                else transform(before)
            )
            results.append((identifier, before, updated))
        return len(selected_rows), results, skipped

    def _merge_source_into_output(self, kind: str, source: str, output: str) -> str:
        """Bring one selected field into the draft without erasing it on blank input."""
        if not source:
            return output
        separator = self.separator_input.text()
        if kind == "copy_replace":
            return source
        if kind == "copy_prepend":
            return source + (separator if output else "") + output
        if kind == "copy_append":
            return output + (separator if output else "") + source
        raise ValueError("項目データの取り込み方法を選択してください。")

    def _update_operation_prediction(self, *_unused: object) -> None:
        """Keep the always-visible prediction column in sync without touching output."""
        try:
            selected_count, results, skipped = self._planned_operation_results()
        except ValueError as exc:
            self._set_prediction_values(None)
            self.apply_operation_button.setEnabled(False)
            self.operation_status_label.setText(str(exc))
            return
        predictions = {identifier: after for identifier, _before, after in results}
        changed_count = sum(before != after for _identifier, before, after in results)
        self._set_prediction_values(predictions)
        self.apply_operation_button.setEnabled(bool(changed_count))
        suffix = f" / 条件外 {skipped} 件" if skipped else ""
        self.operation_status_label.setText(
            f"操作の予測: チェック済み {selected_count} 件中、変更 {changed_count} 件{suffix}"
        )

    def _set_prediction_values(self, predictions: dict[str, str] | None) -> None:
        for index in range(self.table.rowCount()):
            prediction_item = self.table.item(index, self._PREDICTION_COLUMN)
            assert prediction_item is not None
            if not self._target_check_at(index).isChecked():
                prediction_item.setText("（対象外）")
                continue
            if predictions is None:
                prediction_item.setText("（入力待ち）")
                continue
            prediction_item.setText(predictions.get(self._identifier_at(index), ""))

    def _apply_operation_changes(
        self, changes: list[tuple[str, str, str]], *, selected_count: int, skipped: int
    ) -> None:
        """Apply one result-preview update and retain only its previous visible state."""
        self._undo_outputs = self._output_values()
        updated_outputs = {identifier: after for identifier, _before, after in changes}
        self._set_output_values(updated_outputs)
        self.undo_operation_button.setEnabled(True)
        suffix = f" / 条件外 {skipped} 件" if skipped else ""
        self.operation_status_label.setText(
            f"チェック済み {selected_count} 件へ反映、変更 {len(changes)} 件{suffix}。"
            "必要なら「ひとつ前に戻す」を使えます。"
        )

    def _undo_last_operation(self) -> None:
        if self._undo_outputs is None:
            self.operation_status_label.setText("戻せる共通操作はありません。")
            return
        self._set_output_values(self._undo_outputs)
        self._discard_undo()
        self.operation_status_label.setText("直前の共通操作を取り消しました。")

    def _output_values(self) -> dict[str, str]:
        return {
            self._identifier_at(index): self.table.item(index, self._OUTPUT_COLUMN).text()
            for index in range(self.table.rowCount())
            if self.table.item(index, self._OUTPUT_COLUMN) is not None
        }

    def _set_output_values(self, values: dict[str, str]) -> None:
        self._bulk_updating = True
        try:
            for index in range(self.table.rowCount()):
                identifier = self._identifier_at(index)
                if identifier not in values:
                    continue
                output_item = self.table.item(index, self._OUTPUT_COLUMN)
                assert output_item is not None
                output_item.setText(values[identifier])
        finally:
            self._bulk_updating = False
        self._refresh_count()
        self._update_operation_prediction()

    def _discard_undo(self) -> None:
        self._undo_outputs = None
        if hasattr(self, "undo_operation_button"):
            self.undo_operation_button.setEnabled(False)

    def _operation_transform(self, kind: str):
        find = self.find_input.text()
        replacement = self.replacement_input.text()
        position = self.position_input.value()
        length = self.length_input.value()
        if kind == "delete_text":
            if not find:
                raise ValueError("削除する文字列を入力してください。")
            return lambda text: text.replace(find, "")
        if kind == "replace_text":
            if not find:
                raise ValueError("置換する元の文字列を入力してください。")
            return lambda text: text.replace(find, replacement)
        if kind == "delete_range":
            return lambda text: text[:position] + text[position + length :]
        if kind == "clear_output":
            return lambda _text: ""
        if kind == "replace_range":
            return lambda text: text[:position] + replacement + text[position + length :]
        if kind == "insert_start":
            return lambda text: replacement + text
        if kind == "insert_end":
            return lambda text: text + replacement
        if kind == "insert_position":
            return lambda text: text[:position] + replacement + text[position:]
        if kind == "regex_replace":
            if not find:
                raise ValueError("正規表現を入力してください。")
            try:
                pattern = re.compile(find)
            except re.error as exc:
                raise ValueError(f"正規表現の書式が正しくありません: {exc}") from exc
            try:
                pattern.sub(replacement, "")
            except re.error as exc:
                raise ValueError(f"置換文字列の書式が正しくありません: {exc}") from exc
            return lambda text: pattern.sub(replacement, text)
        if kind == "normalize":
            return _normalization_transform(str(self.normalization_combo.currentData() or ""))
        raise ValueError("未対応の共通操作です。")

    def _output_changed(self, item: QTableWidgetItem) -> None:
        if item.column() == self._OUTPUT_COLUMN and not self._bulk_updating:
            self._discard_undo()
            self._refresh_count()
            self._update_operation_prediction()

    def _refresh_count(self, *_unused: object) -> None:
        selected = sum(self._target_check_at(index).isChecked() for index in range(self.table.rowCount()))
        outputs = sum(
            bool((self.table.item(index, self._OUTPUT_COLUMN).text() if self.table.item(index, self._OUTPUT_COLUMN) else "").strip())
            for index in range(self.table.rowCount())
        )
        self.count_label.setText(f"操作対象: {selected} / {self.table.rowCount()} 件　出力予定: {outputs} 件")

    def _identifier_at(self, row: int) -> str:
        item = self.table.item(row, self._IDENTIFIER_COLUMN)
        assert item is not None
        return item.text()

    def _target_check_at(self, row: int) -> QCheckBox:
        check = self.table.cellWidget(row, self._TARGET_COLUMN)
        assert isinstance(check, QCheckBox)
        return check

    def _confirm(self) -> None:
        entries: list[TextWorkspaceEntry] = []
        for index in range(self.table.rowCount()):
            identifier = self._identifier_at(index)
            output_item = self.table.item(index, self._OUTPUT_COLUMN)
            value = output_item.text().strip() if output_item is not None else ""
            if value:
                entries.append(TextWorkspaceEntry(identifier, value))
        if not entries:
            self.operation_status_label.setText("出力予定がありません。1件以上入力してください。")
            return
        self._result = TextWorkspaceResult(
            target_key=str(self.target_combo.currentData() or ""),
            source_key=str(self.source_combo.currentData() or ""),
            entries=tuple(entries),
        )
        self.accept()


def _normalization_transform(kind: str):
    digit_to_half = str.maketrans("０１２３４５６７８９", "0123456789")
    digit_to_full = str.maketrans("0123456789", "０１２３４５６７８９")
    letter_to_half = str.maketrans(
        "ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚ",
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz",
    )
    letter_to_full = str.maketrans(
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz",
        "ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚ",
    )
    transforms = {
        "digits_half": lambda text: text.translate(digit_to_half),
        "digits_full": lambda text: text.translate(digit_to_full),
        "letters_half": lambda text: text.translate(letter_to_half),
        "letters_full": lambda text: text.translate(letter_to_full),
        "letters_lower": lambda text: text.lower(),
        "letters_upper": lambda text: text.upper(),
    }
    transform = transforms.get(kind)
    if transform is None:
        raise ValueError("文字種の統一方法を選択してください。")
    return transform
