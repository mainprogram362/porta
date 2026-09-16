"""Separate preview/execution window for record-linked filesystem items."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from gui import NoWheelComboBox
from gui.layout_policy import preferred_window_size

from .linked_record_rename import LinkedRenameRule, build_linked_rename_preview
from .record_linkage import RecordLinkageResult
from .rename_workflow import RenamePreview, execute_rename_plan


class LinkedRecordOperationsDialog(QDialog):
    """Build a safe batch rename from one frozen linkage result."""

    paths_renamed = Signal(object)

    def __init__(
        self,
        linkage: RecordLinkageResult,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._linkage = linkage
        self._preview = RenamePreview(None, "")
        self.setWindowTitle("PORTA — 紐づけファイル一括操作")
        self.resize(preferred_window_size(self))
        self._build_ui()
        self._choose_initial_output_field()
        self._refresh_rule_visibility()
        self.refresh_preview()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        heading = QLabel("紐づけファイル一括操作")
        heading.setStyleSheet("font-size: 16px; font-weight: 600;")
        layout.addWidget(heading)
        title = self._linkage.bundle.title or "名称なし"
        layout.addWidget(
            QLabel(
                f"対応表「{title}」 / {len(self._linkage.links)}件 / "
                f"項目{self._linkage.field_index + 1}「{self._linkage.field_name}」の名前と完全一致"
            )
        )

        simple_box = QGroupBox("初心者向けルール")
        simple_layout = QVBoxLayout(simple_box)
        first_row = QHBoxLayout()
        first_row.addWidget(QLabel("1. 名前のどこを変える"))
        self.mode_combo = NoWheelComboBox()
        self.mode_combo.addItem("ファイル名を置き換える", "replace")
        self.mode_combo.addItem("ファイル名の前に追加", "prepend")
        self.mode_combo.addItem("ファイル名の末尾に追加", "append")
        self.mode_combo.currentIndexChanged.connect(self.refresh_preview)
        first_row.addWidget(self.mode_combo, 1)
        simple_layout.addLayout(first_row)

        second_row = QHBoxLayout()
        second_row.addWidget(QLabel("2. 対応表から使う項目"))
        self.output_field_combo = NoWheelComboBox()
        for index, field_name in enumerate(self._linkage.bundle.field_names):
            self.output_field_combo.addItem(f"項目{index + 1}「{field_name}」", index)
        second_row.addWidget(self.output_field_combo, 1)
        simple_layout.addLayout(second_row)

        third_row = QHBoxLayout()
        third_row.addWidget(QLabel("3. 追加・置換する内容"))
        self.simple_template_input = QLineEdit()
        self.simple_template_input.setPlaceholderText("例: _[title:[@2]]")
        self.simple_template_input.setToolTip(
            "[@1]、[@2]…が各レコードの項目値に置き換わります。周囲は普通の文字として追加できます。"
        )
        self.simple_template_input.textChanged.connect(self.refresh_preview)
        self.output_field_combo.currentIndexChanged.connect(self._set_simple_token)
        third_row.addWidget(self.simple_template_input, 1)
        insert_token = QPushButton("選択項目 [@N] を挿入")
        insert_token.clicked.connect(self._insert_selected_token)
        third_row.addWidget(insert_token)
        simple_layout.addLayout(third_row)
        example = QLabel("例：末尾に「_[title:[@2]]」を追加 → 現在名_[title:項目2の値]")
        example.setWordWrap(True)
        simple_layout.addWidget(example)
        layout.addWidget(simple_box)
        self.simple_box = simple_box

        self.advanced_check = QCheckBox("上級モード（正規表現で現在名を置換）")
        self.advanced_check.toggled.connect(self._refresh_rule_visibility)
        layout.addWidget(self.advanced_check)
        self.advanced_box = QGroupBox("上級ルール")
        advanced_layout = QVBoxLayout(self.advanced_box)
        regex_row = QHBoxLayout()
        regex_row.addWidget(QLabel("検索する正規表現"))
        self.regex_pattern_input = QLineEdit()
        self.regex_pattern_input.setPlaceholderText(r"例: ^(\d{4}).*$")
        self.regex_pattern_input.textChanged.connect(self.refresh_preview)
        regex_row.addWidget(self.regex_pattern_input, 1)
        advanced_layout.addLayout(regex_row)
        replacement_row = QHBoxLayout()
        replacement_row.addWidget(QLabel("置換後（\\1 と [@N] を使用可）"))
        self.regex_replacement_input = QLineEdit()
        self.regex_replacement_input.setPlaceholderText(r"例: \1_[@2]")
        self.regex_replacement_input.textChanged.connect(self.refresh_preview)
        replacement_row.addWidget(self.regex_replacement_input, 1)
        advanced_layout.addLayout(replacement_row)
        layout.addWidget(self.advanced_box)

        self.preserve_extension_check = QCheckBox("ファイルは現在の拡張子を維持する")
        self.preserve_extension_check.setChecked(True)
        self.preserve_extension_check.setToolTip(
            "フォルダ名には拡張子という扱いをせず、常に名前全体を変更します。"
        )
        self.preserve_extension_check.toggled.connect(self.refresh_preview)
        layout.addWidget(self.preserve_extension_check)

        self.preview_table = QTableWidget(0, 3)
        self.preview_table.setHorizontalHeaderLabels(("現在のパス", "紐づけ先", "変更後の名前"))
        self.preview_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.preview_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.preview_table.setAlternatingRowColors(True)
        header = self.preview_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.preview_table, 1)

        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        close_button = QPushButton("閉じる")
        close_button.clicked.connect(self.reject)
        buttons.addWidget(close_button)
        self.execute_button = QPushButton("この内容で名前を変更…")
        self.execute_button.clicked.connect(self.execute)
        buttons.addWidget(self.execute_button)
        layout.addLayout(buttons)

    def _choose_initial_output_field(self) -> None:
        initial = next(
            (
                index
                for index in range(len(self._linkage.bundle.field_names))
                if index != self._linkage.field_index
            ),
            self._linkage.field_index,
        )
        self.output_field_combo.setCurrentIndex(initial)
        self._set_simple_token()

    def _selected_token(self) -> str:
        field_index = int(self.output_field_combo.currentData())
        return f"[@{field_index + 1}]"

    def _set_simple_token(self, *_args: object) -> None:
        self.simple_template_input.setText(self._selected_token())

    def _insert_selected_token(self) -> None:
        self.simple_template_input.insert(self._selected_token())

    def _refresh_rule_visibility(self, *_args: object) -> None:
        advanced = self.advanced_check.isChecked()
        self.simple_box.setVisible(not advanced)
        self.advanced_box.setVisible(advanced)
        self.refresh_preview()

    def _current_rule(self) -> LinkedRenameRule:
        if self.advanced_check.isChecked():
            return LinkedRenameRule(
                "regex",
                self.regex_replacement_input.text(),
                regex_pattern=self.regex_pattern_input.text(),
                preserve_extension=self.preserve_extension_check.isChecked(),
            )
        return LinkedRenameRule(
            str(self.mode_combo.currentData()),  # type: ignore[arg-type]
            self.simple_template_input.text(),
            preserve_extension=self.preserve_extension_check.isChecked(),
        )

    def refresh_preview(self, *_args: object) -> None:
        if not hasattr(self, "preview_table"):
            return
        self._preview = build_linked_rename_preview(
            self._linkage.links,
            self._current_rule(),
        )
        self.preview_table.setRowCount(len(self._linkage.links))
        outputs = (
            [rename.output.name for rename in self._preview.plan.renames]
            if self._preview.plan is not None
            else ["—" for _link in self._linkage.links]
        )
        for row_index, (link, output_name) in enumerate(zip(self._linkage.links, outputs)):
            self.preview_table.setItem(row_index, 0, QTableWidgetItem(str(link.source)))
            self.preview_table.setItem(row_index, 1, QTableWidgetItem(link.display_text))
            self.preview_table.setItem(row_index, 2, QTableWidgetItem(output_name))
        if self._preview.plan is None:
            detail = self._preview.text.replace("\n", " ")
            self.status_label.setText(detail)
            self.status_label.setStyleSheet("color: #b44;")
        else:
            self.status_label.setText(
                f"実行可能：{len(self._preview.plan.renames)}件。まだファイルは変更していません。"
            )
            self.status_label.setStyleSheet("")
        self.execute_button.setEnabled(self._preview.is_ready)

    def execute(self) -> None:
        self.refresh_preview()
        plan = self._preview.plan
        if plan is None:
            QMessageBox.warning(self, "実行できません", self._preview.text)
            return
        answer = QMessageBox.question(
            self,
            "名前を一括変更",
            f"{len(plan.renames)}件のファイル・フォルダ名を変更します。\n"
            "この操作を実行しますか？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        path_changes = {str(rename.source): str(rename.output) for rename in plan.renames}
        try:
            execute_rename_plan(plan)
        except OSError as exc:
            QMessageBox.warning(self, "名前を変更できません", str(exc))
            self.refresh_preview()
            return
        self.paths_renamed.emit(path_changes)
        QMessageBox.information(self, "名前を変更しました", f"{len(plan.renames)}件を変更しました。")
        self.accept()
