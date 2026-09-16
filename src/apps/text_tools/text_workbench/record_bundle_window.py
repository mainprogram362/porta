"""Non-modal, session-only editor for ordered record bundles."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from PySide6.QtCore import Qt

from PySide6.QtGui import QBrush, QColor, QTextOption
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QDialog,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from records.record_bundle import FieldExtractionRule, RecordBundle, RecordBundleRow, RecordSplitResult, TransientRecordBundleStore, extract_candidate_fields, incomplete_row_numbers, parse_field_names, render_bundle_column, render_bundle_json, render_bundle_template, render_bundle_tsv, split_record_candidates, suggest_record_layout
from records.record_bundle_editing import insert_text_into_field, remove_characters_from_field, sort_bundle_rows
from gui import NoWheelComboBox
from gui.layout_policy import preferred_window_size, set_item_view_rows, set_text_rows
from gui.record_bundle_field_dialog import RecordBundleFieldDialog

class RecordBundleWindow(QMainWindow):
    """Keep one editable correspondence table alive while PORTA is running."""

    def __init__(
        self,
        store: TransientRecordBundleStore,
        *,
        send_to_text_workbench: Callable[[str], None],
        send_to_file_manager: Callable[[RecordBundle], None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._store = store
        self._send_to_text_workbench = send_to_text_workbench
        self._send_to_file_manager = send_to_file_manager
        self._field_names: tuple[str, ...] = ()
        self._split_result: RecordSplitResult | None = None
        self._field_rules: dict[str, FieldExtractionRule] = {}
        self._row_identifiers: list[str] = []
        self._updating_table = False
        self._loading_rule = False
        self._shutting_down = False
        self._manual_identifier = 0
        self._field_transform_dialogs: set[RecordBundleFieldDialog] = set()
        self._field_text_windows: set[QDialog] = set()
        self.setWindowTitle("PORTA — 一時対応表")
        self.resize(preferred_window_size(self))
        self._build_ui()
        if store.bundle is not None:
            self._load_bundle(store.bundle)

    @property
    def source_text(self) -> str:
        return self.source_editor.toPlainText()

    @property
    def has_bundle(self) -> bool:
        return self._store.bundle is not None and bool(self._store.bundle.rows)

    def _build_ui(self) -> None:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        central = QWidget()
        scroll.setWidget(central)
        self.setCentralWidget(scroll)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(10, 8, 10, 10)
        layout.setSpacing(6)

        heading = QHBoxLayout()
        title = QLabel("一時対応表 — 手修正・出力")
        title.setStyleSheet("font-size: 16px; font-weight: 600;")
        heading.addWidget(title)
        heading.addStretch(1)
        lifetime_status = QLabel("×で隠してもPORTA終了まで対応表を保持します")
        lifetime_status.setStyleSheet("color: palette(placeholder-text); font-size: 11px;")
        heading.addWidget(lifetime_status)
        layout.addLayout(heading)

        source_box = QGroupBox("1. 取り込み元をレコード候補に区切る")
        source_layout = QVBoxLayout(source_box)
        self.source_editor = QPlainTextEdit()
        set_text_rows(self.source_editor, minimum=4, maximum=4)
        self.source_editor.setWordWrapMode(QTextOption.WrapMode.NoWrap)
        self.source_editor.setPlaceholderText(
            "テキスト加工ワークベンチから渡すか、ここへ直接貼り付けます。"
        )
        self.source_editor.textChanged.connect(self._source_changed)
        source_layout.addWidget(self.source_editor)

        split_settings = QHBoxLayout()
        split_settings.addWidget(QLabel("区切り方法"))
        self.split_mode_combo = NoWheelComboBox()
        self.split_mode_combo.addItem("指定した行数ごと", "fixed_lines")
        self.split_mode_combo.addItem("空行ごとのブロック", "blank_blocks")
        self.split_mode_combo.addItem("指定文字で始まる行から次の開始行まで", "start_text")
        self.split_mode_combo.addItem("正規表現に一致する行から次の開始行まで", "start_regex")
        self.split_mode_combo.currentIndexChanged.connect(self._split_mode_changed)
        split_settings.addWidget(self.split_mode_combo, 1)
        self.lines_per_record_label = QLabel("1レコードの行数")
        split_settings.addWidget(self.lines_per_record_label)
        self.lines_per_record_spin = QSpinBox()
        self.lines_per_record_spin.setRange(1, 10_000)
        self.lines_per_record_spin.setValue(2)
        self.lines_per_record_spin.valueChanged.connect(self._invalidate_split)
        split_settings.addWidget(self.lines_per_record_spin)
        self.boundary_label = QLabel("開始条件")
        split_settings.addWidget(self.boundary_label)
        self.boundary_input = QLineEdit()
        self.boundary_input.textChanged.connect(self._invalidate_split)
        split_settings.addWidget(self.boundary_input, 1)
        self.boundary_case_check = QCheckBox("大文字・小文字を区別")
        self.boundary_case_check.setChecked(True)
        self.boundary_case_check.toggled.connect(self._invalidate_split)
        split_settings.addWidget(self.boundary_case_check)
        source_layout.addLayout(split_settings)

        split_options = QHBoxLayout()
        self.first_line_title_check = QCheckBox("先頭の非空行を束名として除外")
        self.first_line_title_check.setChecked(True)
        self.first_line_title_check.toggled.connect(self._invalidate_split)
        split_options.addWidget(self.first_line_title_check)
        self.ignore_blank_check = QCheckBox("空行を行数に含めない")
        self.ignore_blank_check.setChecked(True)
        self.ignore_blank_check.toggled.connect(self._invalidate_split)
        split_options.addWidget(self.ignore_blank_check)
        self.strip_values_check = QCheckBox("各行の前後空白を除去")
        self.strip_values_check.setChecked(True)
        self.strip_values_check.toggled.connect(self._invalidate_split)
        split_options.addWidget(self.strip_values_check)
        split_options.addStretch(1)
        split_button = QPushButton("1. レコード候補を区切る")
        split_button.clicked.connect(self.split_records)
        split_options.addWidget(split_button)
        self.candidate_status_label = QLabel("まだ区切っていません。")
        split_options.addWidget(self.candidate_status_label)
        source_layout.addLayout(split_options)

        self.candidate_tree = QTreeWidget()
        self.candidate_tree.setHeaderLabels(("候補", "元の行", "内容プレビュー"))
        self.candidate_tree.setRootIsDecorated(False)
        set_item_view_rows(self.candidate_tree, minimum=3, maximum=3)
        self.candidate_tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.candidate_tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.candidate_tree.header().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        source_layout.addWidget(self.candidate_tree)
        layout.addWidget(source_box)
        source_box.setVisible(False)

        extraction_box = QGroupBox("2. 各レコード候補の中から項目を分離する")
        extraction_layout = QVBoxLayout(extraction_box)
        fields = QHBoxLayout()
        fields.addWidget(QLabel("作る項目数"))
        self.field_count_spin = QSpinBox()
        self.field_count_spin.setRange(2, 10)
        self.field_count_spin.setValue(2)
        self.field_count_spin.valueChanged.connect(self._field_count_changed)
        fields.addWidget(self.field_count_spin)
        fields.addWidget(QLabel("項目名"))
        self.field_names_input = QLineEdit("キー, 値")
        self.field_names_input.setPlaceholderText("例: 日付, タイトル, サイズ")
        fields.addWidget(self.field_names_input, 1)
        prepare_fields = QPushButton("2. 項目構成を準備")
        prepare_fields.setToolTip("最初は項目1=1行目、項目2=2行目…として準備します。")
        prepare_fields.clicked.connect(self.prepare_field_rules)
        fields.addWidget(prepare_fields)
        extraction_layout.addLayout(fields)

        rule_choice = QHBoxLayout()
        rule_choice.addWidget(QLabel("設定する項目"))
        self.rule_field_combo = NoWheelComboBox()
        self.rule_field_combo.currentIndexChanged.connect(self._load_selected_rule)
        rule_choice.addWidget(self.rule_field_combo, 1)
        rule_choice.addWidget(QLabel("取り出し方"))
        self.rule_method_combo = NoWheelComboBox()
        self.rule_method_combo.addItem("指定行をそのまま", "line")
        self.rule_method_combo.addItem("指定行を区切ってN番目", "split")
        self.rule_method_combo.addItem("指定行の文字位置", "slice")
        self.rule_method_combo.addItem("候補全体から正規表現グループ", "regex")
        self.rule_method_combo.addItem("候補全体をそのまま", "whole")
        self.rule_method_combo.currentIndexChanged.connect(self._rule_method_changed)
        rule_choice.addWidget(self.rule_method_combo, 2)
        self.rule_case_check = QCheckBox("大文字・小文字を区別")
        self.rule_case_check.setChecked(True)
        rule_choice.addWidget(self.rule_case_check)
        extraction_layout.addLayout(rule_choice)

        rule_parameters = QHBoxLayout()
        self.rule_line_label = QLabel("候補内の行番号")
        rule_parameters.addWidget(self.rule_line_label)
        self.rule_line_spin = QSpinBox()
        self.rule_line_spin.setRange(1, 10_000)
        rule_parameters.addWidget(self.rule_line_spin)
        self.rule_argument_label = QLabel("区切り文字")
        rule_parameters.addWidget(self.rule_argument_label)
        self.rule_argument_input = QLineEdit()
        rule_parameters.addWidget(self.rule_argument_input, 1)
        self.rule_index_label = QLabel("取得番号")
        rule_parameters.addWidget(self.rule_index_label)
        self.rule_index_spin = QSpinBox()
        self.rule_index_spin.setRange(1, 1_000)
        rule_parameters.addWidget(self.rule_index_spin)
        self.rule_start_label = QLabel("先頭から除く文字数")
        rule_parameters.addWidget(self.rule_start_label)
        self.rule_start_spin = QSpinBox()
        self.rule_start_spin.setRange(0, 1_000_000)
        rule_parameters.addWidget(self.rule_start_spin)
        self.rule_end_label = QLabel("取得を止める位置（0=末尾）")
        rule_parameters.addWidget(self.rule_end_label)
        self.rule_end_spin = QSpinBox()
        self.rule_end_spin.setRange(0, 1_000_000)
        rule_parameters.addWidget(self.rule_end_spin)
        apply_rule = QPushButton("この項目の抽出規則を反映")
        apply_rule.clicked.connect(self.apply_field_rule)
        rule_parameters.addWidget(apply_rule)
        extraction_layout.addLayout(rule_parameters)

        self.rule_tree = QTreeWidget()
        self.rule_tree.setHeaderLabels(("項目", "抽出規則"))
        self.rule_tree.setRootIsDecorated(False)
        set_item_view_rows(self.rule_tree, minimum=2, maximum=2)
        self.rule_tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.rule_tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        extraction_layout.addWidget(self.rule_tree)
        layout.addWidget(extraction_box)
        extraction_box.setVisible(False)
        self._split_mode_changed()
        self._rule_method_changed()

        table_box = QGroupBox("1. 対応表（必要なセルを手修正できます）")
        table_layout = QVBoxLayout(table_box)
        bundle_row = QHBoxLayout()
        bundle_row.addWidget(QLabel("束名"))
        self.bundle_title_input = QLineEdit()
        self.bundle_title_input.setPlaceholderText("任意。例: お笑い芸人のライブ集")
        self.bundle_title_input.editingFinished.connect(self._sync_table_to_store)
        bundle_row.addWidget(self.bundle_title_input, 1)
        self.table_status_label = QLabel("まだ対応表を作成していません。")
        bundle_row.addWidget(self.table_status_label)
        table_layout.addLayout(bundle_row)

        self.table = QTableWidget()
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.itemChanged.connect(self._table_item_changed)
        table_layout.addWidget(self.table, 1)
        row_actions = QHBoxLayout()
        add_row = QPushButton("空の行を追加")
        add_row.clicked.connect(self.add_empty_row)
        row_actions.addWidget(add_row)
        delete_rows = QPushButton("選択行を削除")
        delete_rows.clicked.connect(self.delete_selected_rows)
        row_actions.addWidget(delete_rows)
        move_up = QPushButton("選択行を上へ")
        move_up.clicked.connect(lambda: self.move_current_row(-1))
        row_actions.addWidget(move_up)
        move_down = QPushButton("選択行を下へ")
        move_down.clicked.connect(lambda: self.move_current_row(1))
        row_actions.addWidget(move_down)
        add_derived_field = QPushButton("項目を複製して加工…")
        add_derived_field.setToolTip(
            "既存の項目を残したまま、拡張子削除・範囲削除・文字列挿入をした新しい項目を追加します。"
        )
        add_derived_field.clicked.connect(self.open_field_transform_dialog)
        row_actions.addWidget(add_derived_field)
        row_actions.addStretch(1)
        file_manager_button = QPushButton("この対応表をファイル操作へ渡す…")
        file_manager_button.setToolTip(
            "ファイルマネージャーで項目を選び、作業一覧の名前と完全一致で紐づけます。"
        )
        file_manager_button.clicked.connect(self.send_bundle_to_file_manager)
        row_actions.addWidget(file_manager_button)
        discard_button = QPushButton("束を破棄")
        discard_button.clicked.connect(self.discard_bundle)
        row_actions.addWidget(discard_button)
        table_layout.addLayout(row_actions)

        batch_box = QGroupBox("項目ごとの簡易編集")
        batch_layout = QVBoxLayout(batch_box)
        sort_row = QHBoxLayout()
        sort_row.addWidget(QLabel("対象の項目"))
        self.batch_field_combo = NoWheelComboBox()
        self.batch_field_combo.setMinimumContentsLength(8)
        sort_row.addWidget(self.batch_field_combo, 1)
        sort_row.addWidget(QLabel("並べ替え基準"))
        self.sort_mode_combo = NoWheelComboBox()
        self.sort_mode_combo.addItem("文字順", "text")
        self.sort_mode_combo.addItem("数値順", "number")
        self.sort_mode_combo.addItem("文字数順", "length")
        sort_row.addWidget(self.sort_mode_combo)
        self.sort_descending_check = QCheckBox("降順")
        sort_row.addWidget(self.sort_descending_check)
        sort_button = QPushButton("この項目で並べ替え")
        sort_button.clicked.connect(self.sort_rows_by_selected_field)
        sort_row.addWidget(sort_button)
        text_window_button = QPushButton("この項目をテキストで開く")
        text_window_button.clicked.connect(self.open_selected_field_as_text)
        sort_row.addWidget(text_window_button)
        batch_layout.addLayout(sort_row)

        edit_row = QHBoxLayout()
        edit_row.addWidget(QLabel("全行へ"))
        self.batch_edit_combo = NoWheelComboBox()
        self.batch_edit_combo.addItem("指定位置から削除", "remove")
        self.batch_edit_combo.addItem("指定位置の前へ挿入", "insert")
        self.batch_edit_combo.currentIndexChanged.connect(self._batch_edit_changed)
        edit_row.addWidget(self.batch_edit_combo)
        edit_row.addWidget(QLabel("何文字目"))
        self.batch_position_spin = QSpinBox()
        self.batch_position_spin.setRange(1, 1_000_000)
        edit_row.addWidget(self.batch_position_spin)
        self.batch_count_label = QLabel("から削除する文字数")
        edit_row.addWidget(self.batch_count_label)
        self.batch_count_spin = QSpinBox()
        self.batch_count_spin.setRange(1, 1_000_000)
        self.batch_count_spin.setValue(1)
        edit_row.addWidget(self.batch_count_spin)
        self.batch_insert_label = QLabel("挿入する文字列")
        edit_row.addWidget(self.batch_insert_label)
        self.batch_insert_input = QLineEdit()
        self.batch_insert_input.setPlaceholderText("例: prefix_")
        edit_row.addWidget(self.batch_insert_input, 1)
        apply_batch_edit = QPushButton("全行へ反映")
        apply_batch_edit.clicked.connect(self.apply_batch_field_edit)
        edit_row.addWidget(apply_batch_edit)
        batch_layout.addLayout(edit_row)
        self.batch_edit_note = QLabel()
        self.batch_edit_note.setWordWrap(True)
        batch_layout.addWidget(self.batch_edit_note)
        table_layout.addWidget(batch_box)
        self._batch_edit_changed()
        layout.addWidget(table_box, 1)

        output_box = QGroupBox("2. 出力（作成するまでファイルには書き込みません）")
        output_layout = QVBoxLayout(output_box)
        output_settings = QHBoxLayout()
        output_settings.addWidget(QLabel("形式"))
        self.output_format_combo = NoWheelComboBox()
        self.output_format_combo.addItem("TSV（表計算へ貼り付け）", "tsv")
        self.output_format_combo.addItem("JSON（構造を保持）", "json")
        self.output_format_combo.addItem("テンプレート（1レコード1行）", "template")
        self.output_format_combo.addItem("選択した1列", "column")
        self.output_format_combo.currentIndexChanged.connect(self._output_format_changed)
        output_settings.addWidget(self.output_format_combo)
        self.include_headers_check = QCheckBox("項目名を含める")
        self.include_headers_check.setChecked(True)
        output_settings.addWidget(self.include_headers_check)
        self.output_field_combo = NoWheelComboBox()
        output_settings.addWidget(self.output_field_combo)
        self.template_input = QLineEdit()
        self.template_input.setPlaceholderText("例: {日付}_{タイトル}")
        output_settings.addWidget(self.template_input, 1)
        create_output = QPushButton("出力を作成")
        create_output.clicked.connect(self.create_output)
        output_settings.addWidget(create_output)
        output_layout.addLayout(output_settings)

        self.output_preview = QPlainTextEdit()
        self.output_preview.setReadOnly(True)
        set_text_rows(self.output_preview, minimum=5, maximum=5)
        self.output_preview.setWordWrapMode(QTextOption.WrapMode.NoWrap)
        self.output_preview.setPlaceholderText(
            "形式を選び「出力を作成」を押すと、ここに表示します。"
        )
        output_layout.addWidget(self.output_preview)
        output_actions = QHBoxLayout()
        copy_output = QPushButton("出力をクリップボードへコピー")
        copy_output.clicked.connect(self.copy_output)
        output_actions.addWidget(copy_output)
        send_output = QPushButton("テキスト加工ワークベンチへ渡す")
        send_output.clicked.connect(self.send_output_to_workbench)
        output_actions.addWidget(send_output)
        save_output = QPushButton("UTF-8で保存…")
        save_output.clicked.connect(self.save_output)
        output_actions.addWidget(save_output)
        output_actions.addStretch(1)
        self.output_status_label = QLabel()
        self.output_status_label.setWordWrap(True)
        output_actions.addWidget(self.output_status_label, 1)
        output_layout.addLayout(output_actions)
        layout.addWidget(output_box)
        self._output_format_changed()

    def offer_source_text(self, text: str) -> None:
        """Replace the import draft, suggest a layout and immediately build a preview."""
        self.source_editor.setPlainText(text)
        suggestion = suggest_record_layout(text)
        self.split_mode_combo.setCurrentIndex(self.split_mode_combo.findData("fixed_lines"))
        self.lines_per_record_spin.setValue(suggestion.field_count)
        self.field_count_spin.blockSignals(True)
        self.field_count_spin.setValue(suggestion.field_count)
        self.field_count_spin.blockSignals(False)
        self.field_names_input.setText(", ".join(suggestion.field_names))
        self.first_line_title_check.setChecked(suggestion.use_first_line_as_title)
        self.build_bundle()

    def load_prepared_bundle(self, bundle: RecordBundle) -> None:
        """Receive a visually confirmed bundle for manual correction and output."""
        self._split_result = None
        self._field_rules.clear()
        self._store.replace(bundle)
        self.template_input.setText(
            "_".join("{" + name + "}" for name in bundle.field_names[:2])
        )
        self._load_bundle(bundle)

    def _source_changed(self) -> None:
        self._invalidate_split()

    def _invalidate_split(self, *_args: object) -> None:
        if self._split_result is None:
            return
        self._split_result = None
        self._field_rules.clear()
        self.candidate_tree.clear()
        self.rule_tree.clear()
        self.rule_field_combo.clear()
        self.candidate_status_label.setText(
            "取り込み元または区切り設定が変わりました。"
            "レコード候補を区切り直してください。"
        )

    def _split_mode_changed(self, *_args: object) -> None:
        mode = self.split_mode_combo.currentData()
        uses_line_count = mode == "fixed_lines"
        uses_boundary = mode in {"start_text", "start_regex"}
        self.lines_per_record_label.setVisible(uses_line_count)
        self.lines_per_record_spin.setVisible(uses_line_count)
        self.boundary_label.setVisible(uses_boundary)
        self.boundary_input.setVisible(uses_boundary)
        self.boundary_case_check.setVisible(uses_boundary)
        self.ignore_blank_check.setVisible(mode != "blank_blocks")
        self.boundary_label.setText(
            "開始正規表現" if mode == "start_regex" else "開始文字"
        )
        self.boundary_input.setPlaceholderText(
            r"例: ^\d{8}$" if mode == "start_regex" else "例: ITEM:"
        )
        self._invalidate_split()

    def _field_count_changed(self, count: int) -> None:
        try:
            current = parse_field_names(self.field_names_input.text(), count)
        except ValueError:
            current = ()
        if not current:
            self.field_names_input.setText(", ".join(f"項目{index}" for index in range(1, count + 1)))

    def build_bundle(self) -> None:
        """Compatibility entry: run both the boundary and default field stages."""
        self.split_records()

    def split_records(self) -> None:
        try:
            result = split_record_candidates(
                self.source_editor.toPlainText(),
                mode=self.split_mode_combo.currentData(),
                lines_per_record=self.lines_per_record_spin.value(),
                boundary=self.boundary_input.text(),
                use_first_line_as_title=self.first_line_title_check.isChecked(),
                ignore_blank_lines=self.ignore_blank_check.isChecked(),
                strip_values=self.strip_values_check.isChecked(),
                case_sensitive=self.boundary_case_check.isChecked(),
            )
        except ValueError as exc:
            self.candidate_status_label.setText(str(exc))
            return
        self._split_result = result
        self.candidate_tree.clear()
        for index, candidate in enumerate(result.candidates, start=1):
            preview = " / ".join(candidate.lines)
            item = QTreeWidgetItem(
                (
                    f"候補{index}",
                    f"{candidate.start_line}～{candidate.end_line}",
                    preview,
                )
            )
            item.setToolTip(2, candidate.text)
            self.candidate_tree.addTopLevelItem(item)
        message = f"{len(result.candidates)}件のレコード候補に区切りました。"
        if result.issues:
            message += " " + " ".join(result.issues)
        self.candidate_status_label.setText(message)
        self.prepare_field_rules()

    def prepare_field_rules(self) -> None:
        if self._split_result is None:
            self.candidate_status_label.setText("先にレコード候補を区切ってください。")
            return
        try:
            field_names = parse_field_names(
                self.field_names_input.text(), self.field_count_spin.value()
            )
        except ValueError as exc:
            self.table_status_label.setText(str(exc))
            return
        self._field_rules = {
            field_name: FieldExtractionRule(field_name, method="line", line_number=index)
            for index, field_name in enumerate(field_names, start=1)
        }
        self.rule_field_combo.blockSignals(True)
        self.rule_field_combo.clear()
        for field_name in field_names:
            self.rule_field_combo.addItem(field_name, field_name)
        self.rule_field_combo.blockSignals(False)
        self.template_input.setText(
            "_".join("{" + name + "}" for name in field_names[:2])
        )
        self._refresh_rule_tree()
        self._load_selected_rule()
        self._apply_all_field_rules()

    def _load_selected_rule(self, *_args: object) -> None:
        field_name = str(self.rule_field_combo.currentData() or "")
        rule = self._field_rules.get(field_name)
        if rule is None:
            return
        self._loading_rule = True
        try:
            self.rule_method_combo.setCurrentIndex(
                self.rule_method_combo.findData(rule.method)
            )
            self.rule_line_spin.setValue(rule.line_number)
            self.rule_argument_input.setText(rule.argument)
            self.rule_index_spin.setValue(rule.value_index)
            self.rule_start_spin.setValue(rule.start_position)
            self.rule_end_spin.setValue(rule.end_position)
            self.rule_case_check.setChecked(rule.case_sensitive)
        finally:
            self._loading_rule = False
        self._rule_method_changed()

    def _rule_method_changed(self, *_args: object) -> None:
        method = self.rule_method_combo.currentData()
        uses_line = method in {"line", "split", "slice"}
        uses_argument = method in {"split", "regex"}
        uses_index = method in {"split", "regex"}
        uses_positions = method == "slice"
        self.rule_line_label.setVisible(uses_line)
        self.rule_line_spin.setVisible(uses_line)
        self.rule_argument_label.setVisible(uses_argument)
        self.rule_argument_input.setVisible(uses_argument)
        self.rule_index_label.setVisible(uses_index)
        self.rule_index_spin.setVisible(uses_index)
        self.rule_start_label.setVisible(uses_positions)
        self.rule_start_spin.setVisible(uses_positions)
        self.rule_end_label.setVisible(uses_positions)
        self.rule_end_spin.setVisible(uses_positions)
        self.rule_case_check.setVisible(method == "regex")
        if method == "regex":
            self.rule_argument_label.setText("正規表現")
            self.rule_argument_input.setPlaceholderText(r"例: ID=(\d+)")
            self.rule_index_label.setText("グループ番号")
        elif method == "split":
            self.rule_argument_label.setText("区切り文字")
            self.rule_argument_input.setPlaceholderText("空欄なら連続空白で分割")
            self.rule_index_label.setText("何番目")

    def apply_field_rule(self) -> None:
        field_name = str(self.rule_field_combo.currentData() or "")
        if not field_name:
            self.table_status_label.setText("項目構成を先に準備してください。")
            return
        rule = FieldExtractionRule(
            field_name=field_name,
            method=self.rule_method_combo.currentData(),
            line_number=self.rule_line_spin.value(),
            argument=self.rule_argument_input.text(),
            value_index=self.rule_index_spin.value(),
            start_position=self.rule_start_spin.value(),
            end_position=self.rule_end_spin.value(),
            case_sensitive=self.rule_case_check.isChecked(),
        )
        self._field_rules[field_name] = rule
        self._refresh_rule_tree()
        self._apply_all_field_rules()

    def _apply_all_field_rules(self) -> None:
        if self._split_result is None or not self._field_rules:
            return
        try:
            rules = tuple(self._field_rules[field_name] for field_name in self._field_rules)
            bundle = extract_candidate_fields(self._split_result, rules)
        except ValueError as exc:
            self.table_status_label.setText(str(exc))
            return
        self._store.replace(bundle)
        self._load_bundle(bundle)

    def _refresh_rule_tree(self) -> None:
        self.rule_tree.clear()
        for field_name, rule in self._field_rules.items():
            self.rule_tree.addTopLevelItem(
                QTreeWidgetItem((field_name, self._describe_field_rule(rule)))
            )

    @staticmethod
    def _describe_field_rule(rule: FieldExtractionRule) -> str:
        if rule.method == "line":
            return f"候補内の{rule.line_number}行目をそのまま"
        if rule.method == "split":
            separator = f"「{rule.argument}」" if rule.argument else "連続空白"
            return f"{rule.line_number}行目を{separator}で分割し、{rule.value_index}番目"
        if rule.method == "slice":
            end = str(rule.end_position) if rule.end_position else "末尾"
            return f"{rule.line_number}行目の文字位置{rule.start_position}～{end}"
        if rule.method == "regex":
            return f"正規表現「{rule.argument}」のグループ{rule.value_index}"
        return "候補全体をそのまま"

    def _load_bundle(self, bundle: RecordBundle) -> None:
        self._updating_table = True
        try:
            self._field_names = bundle.field_names
            self._row_identifiers = [row.identifier for row in bundle.rows]
            self.bundle_title_input.setText(bundle.title)
            self.table.clear()
            self.table.setColumnCount(len(bundle.field_names))
            self.table.setHorizontalHeaderLabels(bundle.field_names)
            self.table.setRowCount(len(bundle.rows))
            self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
            for row_index, row in enumerate(bundle.rows):
                for column_index, value in enumerate(row.values):
                    self.table.setItem(row_index, column_index, QTableWidgetItem(value))
            self.output_field_combo.clear()
            previous_batch_field = self.batch_field_combo.currentData()
            self.batch_field_combo.clear()
            for field_name in bundle.field_names:
                self.output_field_combo.addItem(field_name, field_name)
            for index, field_name in enumerate(bundle.field_names):
                self.batch_field_combo.addItem(f"項目{index + 1}「{field_name}」", index)
            if isinstance(previous_batch_field, int):
                previous_index = self.batch_field_combo.findData(previous_batch_field)
                if previous_index >= 0:
                    self.batch_field_combo.setCurrentIndex(previous_index)
            if not self.template_input.text() and bundle.field_names:
                fields = bundle.field_names[:2]
                self.template_input.setText("_".join("{" + name + "}" for name in fields))
        finally:
            self._updating_table = False
        self._refresh_bundle_status()
        self.output_preview.clear()
        self.output_status_label.clear()

    def _bundle_from_table(self) -> RecordBundle:
        if not self._field_names:
            raise ValueError("先に取り込み元テキストから対応表を作成してください。")
        rows = tuple(
            RecordBundleRow(
                self._row_identifiers[row_index],
                tuple(
                    self.table.item(row_index, column_index).text()
                    if self.table.item(row_index, column_index) is not None
                    else ""
                    for column_index in range(self.table.columnCount())
                ),
            )
            for row_index in range(self.table.rowCount())
        )
        return RecordBundle(self.bundle_title_input.text().strip(), self._field_names, rows)

    def _sync_table_to_store(self) -> RecordBundle | None:
        if self._updating_table or not self._field_names:
            return self._store.bundle
        try:
            bundle = self._bundle_from_table()
        except ValueError as exc:
            self.table_status_label.setText(str(exc))
            return None
        self._store.replace(bundle)
        self._refresh_bundle_status()
        self.output_preview.clear()
        self.output_status_label.setText(
            "対応表を編集しました。必要なら出力を作り直してください。"
        )
        return bundle

    def _table_item_changed(self, _item: QTableWidgetItem) -> None:
        self._sync_table_to_store()

    def _refresh_bundle_status(self) -> None:
        bundle = self._store.bundle
        if bundle is None:
            self.table_status_label.setText("まだ対応表を作成していません。")
            return
        incomplete = incomplete_row_numbers(bundle)
        self._updating_table = True
        try:
            for row_index, row in enumerate(bundle.rows):
                for column_index, value in enumerate(row.values):
                    item = self.table.item(row_index, column_index)
                    if item is None:
                        continue
                    is_blank = not value.strip()
                    item.setBackground(QBrush(QColor("#fff1c9")) if is_blank else QBrush())
                    item.setForeground(QBrush(QColor("#202020")) if is_blank else QBrush())
                    item.setToolTip(
                        "値が空です。必要なら入力してください。"
                        if is_blank
                        else value
                    )
        finally:
            self._updating_table = False
        if incomplete:
            numbers = ", ".join(map(str, incomplete[:8]))
            suffix = "…" if len(incomplete) > 8 else ""
            self.table_status_label.setText(
                f"{len(bundle.rows)}件 / 空欄を含む行: {numbers}{suffix}"
            )
        else:
            self.table_status_label.setText(f"{len(bundle.rows)}件 / 全行に値があります")

    def _new_identifier(self) -> str:
        existing = set(self._row_identifiers)
        while True:
            self._manual_identifier += 1
            candidate = f"manual-{self._manual_identifier:06d}"
            if candidate not in existing:
                return candidate

    def _selected_batch_field_index(self) -> int | None:
        index = self.batch_field_combo.currentData()
        return index if isinstance(index, int) else None

    def _replace_bundle_after_simple_edit(self, bundle: RecordBundle, message: str) -> None:
        self._store.replace(bundle)
        self._load_bundle(bundle)
        self.output_status_label.setText(message)

    def sort_rows_by_selected_field(self) -> None:
        bundle = self._sync_table_to_store()
        index = self._selected_batch_field_index()
        if bundle is None or index is None:
            self.table_status_label.setText("先に対応表と操作する項目を用意してください。")
            return
        try:
            updated = sort_bundle_rows(
                bundle, index, mode=self.sort_mode_combo.currentData(),
                descending=self.sort_descending_check.isChecked(),
            )
        except ValueError as exc:
            self.table_status_label.setText(str(exc))
            return
        direction = "降順" if self.sort_descending_check.isChecked() else "昇順"
        self._replace_bundle_after_simple_edit(
            updated, f"項目「{bundle.field_names[index]}」を{self.sort_mode_combo.currentText()}・{direction}で並べ替えました。"
        )

    def _batch_edit_changed(self, *_args: object) -> None:
        inserting = self.batch_edit_combo.currentData() == "insert"
        self.batch_count_label.setVisible(not inserting)
        self.batch_count_spin.setVisible(not inserting)
        self.batch_insert_label.setVisible(inserting)
        self.batch_insert_input.setVisible(inserting)
        self.batch_edit_note.setText(
            "短い値は指定位置より後を削除しません。"
            if not inserting
            else "短い値で指定位置が末尾より後なら、文字列を末尾へ追加します。"
        )

    def apply_batch_field_edit(self) -> None:
        bundle = self._sync_table_to_store()
        index = self._selected_batch_field_index()
        if bundle is None or index is None:
            self.table_status_label.setText("先に対応表と操作する項目を用意してください。")
            return
        try:
            if self.batch_edit_combo.currentData() == "remove":
                updated = remove_characters_from_field(
                    bundle, index, position=self.batch_position_spin.value(), count=self.batch_count_spin.value()
                )
                detail = f"{self.batch_position_spin.value()}文字目から{self.batch_count_spin.value()}文字"
            else:
                updated = insert_text_into_field(
                    bundle, index, position=self.batch_position_spin.value(), text=self.batch_insert_input.text()
                )
                detail = f"{self.batch_position_spin.value()}文字目の前へ文字列"
        except ValueError as exc:
            self.table_status_label.setText(str(exc))
            return
        self._replace_bundle_after_simple_edit(
            updated, f"項目「{bundle.field_names[index]}」の全{len(bundle.rows)}行に{detail}を反映しました。"
        )

    def open_selected_field_as_text(self) -> None:
        bundle = self._sync_table_to_store()
        index = self._selected_batch_field_index()
        if bundle is None or index is None:
            self.table_status_label.setText("先に対応表と操作する項目を用意してください。")
            return
        dialog = QDialog(self)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        dialog.setWindowTitle(f"対応表 — 項目「{bundle.field_names[index]}」のテキスト")
        dialog.resize(620, 480)
        layout = QVBoxLayout(dialog)
        note = QLabel(f"{len(bundle.rows)}行を、項目「{bundle.field_names[index]}」だけで表示しています。")
        note.setWordWrap(True)
        layout.addWidget(note)
        editor = QPlainTextEdit()
        editor.setReadOnly(True)
        editor.setWordWrapMode(QTextOption.WrapMode.NoWrap)
        editor.setPlainText("\n".join(row.values[index] for row in bundle.rows))
        layout.addWidget(editor, 1)
        actions = QHBoxLayout()
        copy_button = QPushButton("すべてコピー")
        copy_button.clicked.connect(lambda: QApplication.clipboard().setText(editor.toPlainText()))
        actions.addWidget(copy_button)
        actions.addStretch(1)
        close_button = QPushButton("閉じる")
        close_button.clicked.connect(dialog.close)
        actions.addWidget(close_button)
        layout.addLayout(actions)
        dialog.destroyed.connect(lambda _object=None, current=dialog: self._field_text_windows.discard(current))
        self._field_text_windows.add(dialog)
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def open_field_transform_dialog(self) -> None:
        """Append a safely derived column while retaining every original value."""
        bundle = self._sync_table_to_store()
        if bundle is None:
            return
        dialog = RecordBundleFieldDialog(bundle, parent=self)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        dialog.setWindowModality(Qt.WindowModality.WindowModal)
        dialog.bundle_created.connect(self._replace_bundle_from_field_transform)
        dialog.destroyed.connect(
            lambda _object=None, current=dialog: self._field_transform_dialogs.discard(current)
        )
        self._field_transform_dialogs.add(dialog)
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _replace_bundle_from_field_transform(self, bundle: object) -> None:
        if not isinstance(bundle, RecordBundle):
            return
        self._store.replace(bundle)
        self._load_bundle(bundle)
        self.output_status_label.setText(
            f"項目「{bundle.field_names[-1]}」を追加しました。元の項目は変更していません。"
        )

    def add_empty_row(self) -> None:
        if not self._field_names:
            self.table_status_label.setText("先に対応表を作成してください。")
            return
        bundle = self._bundle_from_table()
        rows = (*bundle.rows, RecordBundleRow(self._new_identifier(), ("",) * len(self._field_names)))
        updated = RecordBundle(bundle.title, bundle.field_names, rows)
        self._store.replace(updated)
        self._load_bundle(updated)
        self.table.setCurrentCell(self.table.rowCount() - 1, 0)

    def delete_selected_rows(self) -> None:
        selected = sorted({index.row() for index in self.table.selectionModel().selectedRows()})
        if not selected:
            self.table_status_label.setText("削除する行を選択してください。")
            return
        bundle = self._bundle_from_table()
        rows = tuple(row for index, row in enumerate(bundle.rows) if index not in selected)
        updated = RecordBundle(bundle.title, bundle.field_names, rows)
        self._store.replace(updated)
        self._load_bundle(updated)

    def move_current_row(self, offset: int) -> None:
        current = self.table.currentRow()
        destination = current + offset
        if current < 0 or destination < 0 or destination >= self.table.rowCount():
            return
        bundle = self._bundle_from_table()
        rows = list(bundle.rows)
        rows[current], rows[destination] = rows[destination], rows[current]
        updated = RecordBundle(bundle.title, bundle.field_names, tuple(rows))
        self._store.replace(updated)
        self._load_bundle(updated)
        self.table.setCurrentCell(destination, 0)
        self.table.selectRow(destination)

    def discard_bundle(self) -> None:
        if self.has_bundle:
            answer = QMessageBox.question(
                self,
                "一時対応表を破棄",
                "現在の対応表と取り込み元を消去しますか？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self._store.clear()
        self._field_names = ()
        self._split_result = None
        self._field_rules.clear()
        self._row_identifiers.clear()
        self.source_editor.clear()
        self.candidate_tree.clear()
        self.rule_tree.clear()
        self.rule_field_combo.clear()
        self.bundle_title_input.clear()
        self.table.clear()
        self.table.setRowCount(0)
        self.table.setColumnCount(0)
        self.output_preview.clear()
        self.output_status_label.clear()
        self._refresh_bundle_status()

    def _output_format_changed(self, *_args: object) -> None:
        output_format = self.output_format_combo.currentData()
        self.include_headers_check.setVisible(output_format == "tsv")
        self.output_field_combo.setVisible(output_format == "column")
        self.template_input.setVisible(output_format == "template")

    def _render_output(self) -> str | None:
        bundle = self._sync_table_to_store()
        if bundle is None:
            return None
        output_format = self.output_format_combo.currentData()
        try:
            if output_format == "tsv":
                return render_bundle_tsv(
                    bundle, include_headers=self.include_headers_check.isChecked()
                )
            if output_format == "json":
                return render_bundle_json(bundle)
            if output_format == "template":
                return render_bundle_template(bundle, self.template_input.text())
            return render_bundle_column(bundle, str(self.output_field_combo.currentData() or ""))
        except ValueError as exc:
            self.output_status_label.setText(str(exc))
            return None

    def create_output(self) -> str | None:
        output = self._render_output()
        if output is None:
            return None
        self.output_preview.setPlainText(output)
        self.output_status_label.setText(f"{len(output):,}文字の出力を作成しました。")
        return output

    def copy_output(self) -> None:
        output = self.create_output()
        if output is None:
            return
        QApplication.clipboard().setText(output)
        self.output_status_label.setText("作成した出力をクリップボードへコピーしました。")

    def send_output_to_workbench(self) -> None:
        output = self.create_output()
        if output is None:
            return
        self._send_to_text_workbench(output)
        self.output_status_label.setText("出力をテキスト加工ワークベンチへ渡しました。")

    def save_output(self) -> None:
        output = self.create_output()
        if output is None:
            return
        path_text, _selected_filter = QFileDialog.getSaveFileName(
            self,
            "対応表の出力を保存",
            "records.tsv" if self.output_format_combo.currentData() == "tsv" else "records.txt",
            "すべてのファイル (*)",
        )
        if not path_text:
            return
        path = Path(path_text)
        try:
            path.write_text(output, encoding="utf-8")
        except OSError as exc:
            QMessageBox.warning(self, "保存できません", str(exc))
            return
        self.output_status_label.setText(f"UTF-8で保存しました: {path}")

    def send_bundle_to_file_manager(self) -> None:
        bundle = self._sync_table_to_store()
        if bundle is None or not bundle.rows:
            QMessageBox.information(
                self,
                "対応表がありません",
                "先に1件以上の対応表を作成してください。",
            )
            return
        if self._send_to_file_manager is None:
            QMessageBox.information(
                self,
                "ファイル操作を開けません",
                "この画面からファイルマネージャーへの受け渡し先がありません。",
            )
            return
        self._send_to_file_manager(bundle)
        self.output_status_label.setText(
            "対応表をファイルマネージャーへ渡しました。右クリックから紐づけ項目を選べます。"
        )
        self.hide()

    def closeEvent(self, event) -> None:  # type: ignore[override]
        if self._shutting_down:
            event.accept()
            return
        self.hide()
        event.ignore()

    def shutdown(self) -> None:
        """Erase transient content and permit the window to close with PORTA."""
        self._shutting_down = True
        self._store.clear()
        self._field_names = ()
        self._split_result = None
        self._field_rules.clear()
        self._row_identifiers.clear()
        self.source_editor.clear()
        self.candidate_tree.clear()
        self.rule_tree.clear()
        self.rule_field_combo.clear()
        self.bundle_title_input.clear()
        self.table.clear()
        self.table.setRowCount(0)
        self.table.setColumnCount(0)
        self.output_preview.clear()
        self.close()
