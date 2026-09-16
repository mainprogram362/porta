"""Inline record-boundary and field-extraction confirmation for the workbench."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QTextCharFormat, QTextCursor, QTextFormat
from PySide6.QtWidgets import (
    QCheckBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from records.record_bundle import CandidateFieldMatch, FieldExtractionRule, RecordBundle, RecordCandidate, RecordSplitResult, extract_candidate_field_matches, extract_candidate_fields, split_record_candidates, suggest_record_layout
from gui.flow_layout import FlowLayout
from gui.layout_policy import set_text_rows
from gui import NoWheelComboBox


_FIELD_COLORS = (
    ("#dbeafe", "#2563eb"),
    ("#dcfce7", "#16a34a"),
    ("#ffedd5", "#ea580c"),
    ("#f3e8ff", "#9333ea"),
    ("#fce7f3", "#db2777"),
    ("#cffafe", "#0891b2"),
    ("#fef3c7", "#ca8a04"),
    ("#e0e7ff", "#4f46e5"),
)


class _FieldRuleEditor(QWidget):
    """Edit one extraction rule while keeping every field visible as one row."""

    def __init__(
        self,
        index: int,
        field_name: str,
        on_change: Callable[[], None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._index = index
        self._on_change = on_change
        background, border = _FIELD_COLORS[index % len(_FIELD_COLORS)]

        layout = FlowLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(5)
        badge = QLabel(str(index + 1))
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge_side = badge.sizeHint().height()
        badge.setMinimumSize(badge_side, badge_side)
        badge.setToolTip(f"項目{index + 1}の表示色")
        badge.setStyleSheet(
            f"background: {background}; border: 2px solid {border}; "
            "border-radius: 4px; font-weight: 600; color: #202020;"
        )
        layout.addWidget(badge)

        self.name_input = QLineEdit(field_name)
        self.name_input.setPlaceholderText(f"項目{index + 1}")
        layout.addWidget(self.name_input)

        self.method_combo = NoWheelComboBox()
        self.method_combo.addItem("指定行をそのまま", "line")
        self.method_combo.addItem("指定行を分けてN番目", "split")
        self.method_combo.addItem("指定行の文字範囲", "slice")
        self.method_combo.addItem("候補全体から正規表現", "regex")
        self.method_combo.addItem("候補全体", "whole")
        layout.addWidget(self.method_combo)

        self.line_label = QLabel("行")
        layout.addWidget(self.line_label)
        self.line_spin = QSpinBox()
        self.line_spin.setRange(1, 10_000)
        self.line_spin.setValue(index + 1)
        layout.addWidget(self.line_spin)

        self.argument_label = QLabel("区切り")
        layout.addWidget(self.argument_label)
        self.argument_input = QLineEdit()
        layout.addWidget(self.argument_input)

        self.number_label = QLabel("番号")
        layout.addWidget(self.number_label)
        self.number_spin = QSpinBox()
        self.number_spin.setRange(1, 1_000)
        self.number_spin.setValue(1)
        layout.addWidget(self.number_spin)

        self.start_label = QLabel("先頭から")
        layout.addWidget(self.start_label)
        self.start_spin = QSpinBox()
        self.start_spin.setRange(0, 1_000_000)
        layout.addWidget(self.start_spin)
        self.end_label = QLabel("文字まで")
        layout.addWidget(self.end_label)
        self.end_spin = QSpinBox()
        self.end_spin.setRange(0, 1_000_000)
        self.end_spin.setSpecialValueText("末尾")
        layout.addWidget(self.end_spin)

        self.result_label = QLabel()
        self.result_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        layout.addWidget(self.result_label)

        self.method_combo.currentIndexChanged.connect(self._method_changed)
        self.name_input.textChanged.connect(self._notify_change)
        self.line_spin.valueChanged.connect(self._notify_change)
        self.argument_input.textChanged.connect(self._notify_change)
        self.number_spin.valueChanged.connect(self._notify_change)
        self.start_spin.valueChanged.connect(self._notify_change)
        self.end_spin.valueChanged.connect(self._notify_change)
        self._method_changed(notify=False)

    @property
    def color(self) -> tuple[str, str]:
        return _FIELD_COLORS[self._index % len(_FIELD_COLORS)]

    def to_rule(self) -> FieldExtractionRule:
        return FieldExtractionRule(
            field_name=self.name_input.text().strip(),
            method=self.method_combo.currentData(),
            line_number=self.line_spin.value(),
            argument=self.argument_input.text(),
            value_index=self.number_spin.value(),
            start_position=self.start_spin.value(),
            end_position=self.end_spin.value(),
        )

    def set_result(self, found: int, total: int) -> None:
        self.result_label.setText(f"{found}/{total}件")
        self.result_label.setStyleSheet(
            "color: #b91c1c; font-weight: 600;" if found == 0 else ""
        )

    def _notify_change(self, *_args: object) -> None:
        self._on_change()

    def _method_changed(self, *_args: object, notify: bool = True) -> None:
        method = self.method_combo.currentData()
        uses_line = method in {"line", "split", "slice"}
        uses_argument = method in {"split", "regex"}
        uses_number = method in {"split", "regex"}
        uses_range = method == "slice"
        self.line_label.setVisible(uses_line)
        self.line_spin.setVisible(uses_line)
        self.argument_label.setVisible(uses_argument)
        self.argument_input.setVisible(uses_argument)
        self.number_label.setVisible(uses_number)
        self.number_spin.setVisible(uses_number)
        self.start_label.setVisible(uses_range)
        self.start_spin.setVisible(uses_range)
        self.end_label.setVisible(uses_range)
        self.end_spin.setVisible(uses_range)
        if method == "regex":
            self.argument_label.setText("正規表現")
            self.argument_input.setPlaceholderText(r"例: ID=(\d+)")
            self.number_label.setText("グループ")
        elif method == "split":
            self.argument_label.setText("区切り")
            self.argument_input.setPlaceholderText("空欄なら空白")
            self.number_label.setText("番号")
        if notify:
            self._on_change()


class RecordStructurePanel(QGroupBox):
    """Split and annotate records before handing a bundle to the output window."""

    def __init__(
        self,
        source_editor: QPlainTextEdit,
        open_output: Callable[[RecordBundle], None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__("3. 簡易区切り・項目の確認", parent)
        self._source_editor = source_editor
        self._open_output = open_output
        self._split_result: RecordSplitResult | None = None
        self._bundle: RecordBundle | None = None
        self._editors: list[_FieldRuleEditor] = []
        self._title_choice_touched = False
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(5)

        split_row = QHBoxLayout()
        split_row.addWidget(QLabel("簡易区切り"))
        self.split_mode_combo = NoWheelComboBox()
        self.split_mode_combo.addItem("指定した行数ごと", "fixed_lines")
        self.split_mode_combo.addItem("指定文字列を含む行から", "contains_text")
        self.split_mode_combo.currentIndexChanged.connect(self._split_mode_changed)
        split_row.addWidget(self.split_mode_combo)
        self.lines_label = QLabel("1レコード")
        split_row.addWidget(self.lines_label)
        self.lines_spin = QSpinBox()
        self.lines_spin.setRange(1, 10_000)
        self.lines_spin.setValue(2)
        self.lines_spin.valueChanged.connect(self._invalidate_from_setting)
        split_row.addWidget(self.lines_spin)
        self.lines_suffix = QLabel("行")
        split_row.addWidget(self.lines_suffix)
        self.boundary_label = QLabel("ヒット文字列")
        split_row.addWidget(self.boundary_label)
        self.boundary_input = QLineEdit()
        self.boundary_input.setPlaceholderText("この文字列を含む行を次レコードの先頭にします")
        self.boundary_input.textChanged.connect(self._invalidate_from_setting)
        split_row.addWidget(self.boundary_input, 1)
        self.title_check = QCheckBox("先頭の非空行を束名にする")
        self.title_check.clicked.connect(self._title_clicked)
        split_row.addWidget(self.title_check)
        split_button = QPushButton("簡易区切りを確認")
        split_button.clicked.connect(self.split_source)
        split_row.addWidget(split_button)
        layout.addLayout(split_row)

        self.status_label = QLabel(
            "区切り後は、上の作業欄にレコード境界と項目色を重ねて表示します。"
        )
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        self.fields_widget = QWidget()
        fields_layout = QVBoxLayout(self.fields_widget)
        fields_layout.setContentsMargins(0, 0, 0, 0)
        fields_layout.setSpacing(4)
        field_header = QHBoxLayout()
        field_header.addWidget(QLabel("確認する項目数"))
        self.field_count_spin = QSpinBox()
        self.field_count_spin.setRange(2, len(_FIELD_COLORS))
        self.field_count_spin.setValue(2)
        field_header.addWidget(self.field_count_spin)
        rebuild_button = QPushButton("項目欄を作り直す")
        rebuild_button.clicked.connect(self.prepare_fields)
        field_header.addWidget(rebuild_button)
        field_header.addWidget(
            QLabel("色の位置を確認し、一部の空欄などは次の表で手修正できます。"),
            1,
        )
        fields_layout.addLayout(field_header)

        rules_scroll = QScrollArea()
        rules_scroll.setWidgetResizable(True)
        set_text_rows(rules_scroll, minimum=5, maximum=8)
        self.rules_container = QWidget()
        self.rules_layout = QVBoxLayout(self.rules_container)
        self.rules_layout.setContentsMargins(2, 2, 2, 2)
        self.rules_layout.setSpacing(2)
        self.rules_layout.addStretch(1)
        rules_scroll.setWidget(self.rules_container)
        fields_layout.addWidget(rules_scroll)

        output_row = QHBoxLayout()
        self.validation_label = QLabel()
        self.validation_label.setWordWrap(True)
        output_row.addWidget(self.validation_label, 1)
        self.output_button = QPushButton("確認した対応表を手修正・出力へ送る…")
        self.output_button.setEnabled(False)
        self.output_button.clicked.connect(self.send_to_output)
        output_row.addWidget(self.output_button)
        fields_layout.addLayout(output_row)
        layout.addWidget(self.fields_widget)
        self.fields_widget.setVisible(False)
        self._split_mode_changed()

    def invalidate_source(self) -> None:
        """Discard derived structure whenever the editable source changes."""
        if self._split_result is None:
            return
        self._clear_derived_state()
        self.status_label.setText(
            "作業欄が変わったため、古い区切り結果を無効にしました。"
            "もう一度「簡易区切りを確認」を押してください。"
        )

    def split_source(self) -> None:
        source = self._source_editor.toPlainText()
        if not source.strip():
            self._show_fatal("先にテキスト作業欄へ文字を入力してください。")
            return

        suggestion = suggest_record_layout(source)
        if not self._title_choice_touched and suggestion.use_first_line_as_title:
            self.title_check.setChecked(True)
        mode = self.split_mode_combo.currentData()
        try:
            result = split_record_candidates(
                source,
                mode=mode,
                lines_per_record=self.lines_spin.value(),
                boundary=self.boundary_input.text(),
                use_first_line_as_title=self.title_check.isChecked(),
                ignore_blank_lines=True,
                strip_values=True,
            )
        except ValueError as exc:
            self._show_fatal(str(exc))
            return

        self._split_result = result
        fatal_issue = next(
            (
                issue
                for issue in result.issues
                if "開始条件に一致する行がありません" in issue
                or "候補になるテキストがありません" in issue
            ),
            "",
        )
        if fatal_issue:
            self._bundle = None
            self.fields_widget.setVisible(False)
            self.output_button.setEnabled(False)
            self._show_fatal(fatal_issue)
            return

        if mode == "fixed_lines":
            suggested_count = min(max(self.lines_spin.value(), 2), len(_FIELD_COLORS))
        else:
            suggested_count = min(max(suggestion.field_count, 2), len(_FIELD_COLORS))
        self.field_count_spin.setValue(suggested_count)
        names = (
            suggestion.field_names
            if len(suggestion.field_names) == suggested_count
            else tuple(f"項目{index}" for index in range(1, suggested_count + 1))
        )
        self.prepare_fields(names=names)
        message = f"{len(result.candidates)}件のレコードに区切りました。"
        if result.issues:
            message += " " + " ".join(result.issues)
        self.status_label.setStyleSheet("")
        self.status_label.setText(message)

    def prepare_fields(
        self, _checked: bool = False, *, names: tuple[str, ...] | None = None
    ) -> None:
        del _checked
        if self._split_result is None:
            self._show_fatal("先に簡易区切りを実行してください。")
            return
        self._clear_editors()
        count = self.field_count_spin.value()
        if names is None or len(names) != count:
            names = tuple(f"項目{index}" for index in range(1, count + 1))
        for index, field_name in enumerate(names):
            editor = _FieldRuleEditor(index, field_name, self._rules_changed)
            self._editors.append(editor)
            self.rules_layout.insertWidget(self.rules_layout.count() - 1, editor)
        self.fields_widget.setVisible(True)
        self._refresh_rules()

    def send_to_output(self) -> None:
        self._refresh_rules()
        if self._bundle is None or not self.output_button.isEnabled():
            QMessageBox.warning(
                self,
                "出力画面へ進めません",
                self.validation_label.text() or "区切りと項目設定を確認してください。",
            )
            return
        self._open_output(self._bundle)

    def _rules_changed(self) -> None:
        if self._split_result is not None and self._editors:
            self._refresh_rules()

    def _refresh_rules(self) -> None:
        if self._split_result is None or not self._editors:
            return
        rules = tuple(editor.to_rule() for editor in self._editors)
        try:
            matches = extract_candidate_field_matches(self._split_result, rules)
            bundle = extract_candidate_fields(self._split_result, rules)
        except ValueError as exc:
            self._bundle = None
            self.output_button.setEnabled(False)
            self.validation_label.setText(str(exc))
            self.validation_label.setStyleSheet("color: #b91c1c; font-weight: 600;")
            self._apply_annotations(())
            return

        total = len(bundle.rows)
        all_blank_fields: list[str] = []
        missing_cells = 0
        for column, editor in enumerate(self._editors):
            found = sum(bool(row.values[column].strip()) for row in bundle.rows)
            editor.set_result(found, total)
            missing_cells += total - found
            if found == 0:
                all_blank_fields.append(bundle.field_names[column])

        self._bundle = bundle
        overlaps = self._apply_annotations(matches)
        if all_blank_fields:
            joined = "、".join(all_blank_fields)
            self.validation_label.setText(
                f"全レコードで空の項目があります: {joined}。"
                "抽出方法を直してください。"
            )
            self.validation_label.setStyleSheet("color: #b91c1c; font-weight: 600;")
            self.output_button.setEnabled(False)
            return

        details: list[str] = []
        if missing_cells:
            details.append(f"空欄{missing_cells}セルは次の表で手修正できます")
        if overlaps:
            details.append(f"色の重なり{overlaps}か所を確認してください")
        self.validation_label.setText(
            " / ".join(details) if details else "全項目を抽出できています。"
        )
        self.validation_label.setStyleSheet("color: #9a6700;" if details else "")
        self.output_button.setEnabled(True)

    def _apply_annotations(
        self, matches: tuple[tuple[CandidateFieldMatch, ...], ...]
    ) -> int:
        if self._split_result is None:
            self._source_editor.setExtraSelections([])
            return 0
        source_lines = self._source_editor.toPlainText().splitlines()
        selections: list[QTextEdit.ExtraSelection] = []
        overlap_count = 0
        for index, candidate in enumerate(self._split_result.candidates):
            candidate_matches = matches[index] if index < len(matches) else ()
            owners: list[int | None] = [None] * len(candidate.text)
            for field_index, match in enumerate(candidate_matches):
                if not match.value.strip() or not match.found:
                    continue
                assert match.start_offset is not None and match.end_offset is not None
                for offset in range(match.start_offset, match.end_offset):
                    if 0 <= offset < len(owners):
                        if owners[offset] is not None and owners[offset] != field_index:
                            overlap_count += 1
                        elif owners[offset] is None:
                            owners[offset] = field_index
            line_map = self._candidate_line_map(candidate, source_lines)
            record_range = self._candidate_source_range(candidate, source_lines)
            if record_range is not None:
                start, end = record_range
                record_selection = self._range_selection(start, end)
                record_selection.format.setBackground(
                    QColor("#f8fafc" if index % 2 == 0 else "#f1f5f9")
                )
                selections.append(record_selection)

            first_block = self._source_editor.document().findBlockByNumber(
                candidate.start_line - 1
            )
            if first_block.isValid():
                boundary = QTextEdit.ExtraSelection()
                boundary.cursor = QTextCursor(first_block)
                boundary.format.setBackground(QColor("#cbd5e1"))
                boundary.format.setFontWeight(600)
                boundary.format.setProperty(
                    QTextFormat.Property.FullWidthSelection, True
                )
                selections.append(boundary)

            for field_index, match in enumerate(candidate_matches):
                if not match.found or not match.value:
                    continue
                assert match.start_offset is not None and match.end_offset is not None
                source_start = self._candidate_offset_to_source(
                    candidate, line_map, match.start_offset
                )
                source_end = self._candidate_offset_to_source(
                    candidate, line_map, match.end_offset
                )
                if source_start is None or source_end is None or source_start >= source_end:
                    continue
                field_selection = self._range_selection(source_start, source_end)
                background, border = self._editors[field_index].color
                field_selection.format.setBackground(QColor(background))
                field_selection.format.setUnderlineColor(QColor(border))
                field_selection.format.setUnderlineStyle(
                    QTextCharFormat.UnderlineStyle.SingleUnderline
                )
                selections.append(field_selection)
        self._source_editor.setExtraSelections(selections)
        return overlap_count

    def _candidate_line_map(
        self, candidate: RecordCandidate, source_lines: list[str]
    ) -> tuple[tuple[int, int], ...]:
        mapped: list[tuple[int, int]] = []
        for line_number in range(candidate.start_line, candidate.end_line + 1):
            raw = source_lines[line_number - 1] if line_number <= len(source_lines) else ""
            value = raw.strip()
            if not value:
                continue
            expected_index = len(mapped)
            if expected_index >= len(candidate.lines):
                break
            expected = candidate.lines[expected_index]
            column = raw.find(expected)
            mapped.append((line_number, max(column, 0)))
        return tuple(mapped)

    def _candidate_offset_to_source(
        self,
        candidate: RecordCandidate,
        line_map: tuple[tuple[int, int], ...],
        offset: int,
    ) -> int | None:
        remaining = offset
        for index, value in enumerate(candidate.lines):
            if remaining <= len(value):
                if index >= len(line_map):
                    return None
                line_number, leading = line_map[index]
                block = self._source_editor.document().findBlockByNumber(line_number - 1)
                if not block.isValid():
                    return None
                source_line = block.text()
                return block.position() + self._qt_text_length(
                    source_line[: leading + remaining]
                )
            remaining -= len(value) + 1
        return None

    def _candidate_source_range(
        self, candidate: RecordCandidate, source_lines: list[str]
    ) -> tuple[int, int] | None:
        first = self._source_editor.document().findBlockByNumber(candidate.start_line - 1)
        last = self._source_editor.document().findBlockByNumber(candidate.end_line - 1)
        if not first.isValid() or not last.isValid():
            return None
        last_text = source_lines[candidate.end_line - 1]
        return first.position(), last.position() + self._qt_text_length(last_text)

    def _range_selection(self, start: int, end: int) -> QTextEdit.ExtraSelection:
        selection = QTextEdit.ExtraSelection()
        selection.cursor = QTextCursor(self._source_editor.document())
        selection.cursor.setPosition(start)
        selection.cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
        return selection

    @staticmethod
    def _qt_text_length(value: str) -> int:
        """Return QTextDocument's UTF-16 cursor length for Python text."""
        return len(value.encode("utf-16-le")) // 2

    def _split_mode_changed(self, *_args: object) -> None:
        uses_lines = self.split_mode_combo.currentData() == "fixed_lines"
        self.lines_label.setVisible(uses_lines)
        self.lines_spin.setVisible(uses_lines)
        self.lines_suffix.setVisible(uses_lines)
        self.boundary_label.setVisible(not uses_lines)
        self.boundary_input.setVisible(not uses_lines)
        self._invalidate_from_setting()

    def _title_clicked(self, _checked: bool) -> None:
        self._title_choice_touched = True
        self._invalidate_from_setting()

    def _invalidate_from_setting(self, *_args: object) -> None:
        if self._split_result is not None:
            self._clear_derived_state()
            self.status_label.setText(
                "区切り設定が変わりました。もう一度簡易区切りを確認してください。"
            )

    def _show_fatal(self, message: str) -> None:
        self._clear_derived_state()
        self.status_label.setText(message)
        self.status_label.setStyleSheet("color: #b91c1c; font-weight: 600;")

    def _clear_derived_state(self) -> None:
        self._split_result = None
        self._bundle = None
        self.output_button.setEnabled(False)
        self._source_editor.setExtraSelections([])
        self.fields_widget.setVisible(False)
        self._clear_editors()
        self.status_label.setStyleSheet("")

    def _clear_editors(self) -> None:
        for editor in self._editors:
            self.rules_layout.removeWidget(editor)
            editor.deleteLater()
        self._editors.clear()
