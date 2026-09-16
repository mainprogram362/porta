"""Reusable dialog for appending safely derived columns to a record bundle."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from records.record_bundle import RecordBundle
from records.record_bundle_fields import add_extensionless_field, add_inserted_field, add_range_removed_field


class RecordBundleFieldDialog(QDialog):
    """Create one new field from a selected existing field without changing it."""

    bundle_created = Signal(object)

    def __init__(self, bundle: RecordBundle, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._bundle = bundle
        self.setWindowTitle("対応表 — 項目を複製して加工")
        self._build_ui()
        self._operation_changed()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        explanation = QLabel(
            "複製元の項目は変更せず、加工した値を新しい項目として末尾へ追加します。"
        )
        explanation.setWordWrap(True)
        layout.addWidget(explanation)

        source_row = QHBoxLayout()
        source_row.addWidget(QLabel("複製元の項目"))
        self.source_combo = QComboBox()
        for index, name in enumerate(self._bundle.field_names):
            self.source_combo.addItem(f"項目{index + 1}「{name}」", index)
        source_row.addWidget(self.source_combo, 1)
        layout.addLayout(source_row)

        operation_row = QHBoxLayout()
        operation_row.addWidget(QLabel("加える操作"))
        self.operation_combo = QComboBox()
        self.operation_combo.addItem("拡張子を削除", "remove_extension")
        self.operation_combo.addItem("指定範囲を削除", "remove_range")
        self.operation_combo.addItem("文字列を挿入", "insert")
        self.operation_combo.currentIndexChanged.connect(self._operation_changed)
        operation_row.addWidget(self.operation_combo, 1)
        layout.addLayout(operation_row)

        self.extension_note = QLabel(
            "最後の「.」から末尾までを削除します。例: movie.tar.gz → movie.tar。"
        )
        self.extension_note.setWordWrap(True)
        layout.addWidget(self.extension_note)

        self.range_widget = QWidget()
        range_layout = QHBoxLayout(self.range_widget)
        range_layout.setContentsMargins(0, 0, 0, 0)
        range_layout.addWidget(QLabel("開始"))
        self.range_start_anchor = QComboBox()
        self.range_start_anchor.addItem("先頭から", "start")
        self.range_start_anchor.addItem("末尾から", "end")
        range_layout.addWidget(self.range_start_anchor)
        self.range_start_spin = QSpinBox()
        self.range_start_spin.setRange(1, 1_000_000)
        range_layout.addWidget(self.range_start_spin)
        range_layout.addWidget(QLabel("文字目から　終了"))
        self.range_end_anchor = QComboBox()
        self.range_end_anchor.addItem("先頭から", "start")
        self.range_end_anchor.addItem("末尾から", "end")
        range_layout.addWidget(self.range_end_anchor)
        self.range_end_spin = QSpinBox()
        self.range_end_spin.setRange(1, 1_000_000)
        range_layout.addWidget(self.range_end_spin)
        range_layout.addWidget(QLabel("文字目までを削除（両端を含む）"))
        range_layout.addStretch(1)
        layout.addWidget(self.range_widget)

        self.insert_widget = QWidget()
        insert_layout = QHBoxLayout(self.insert_widget)
        insert_layout.setContentsMargins(0, 0, 0, 0)
        insert_layout.addWidget(QLabel("挿入する文字列"))
        self.insert_text = QLineEdit()
        self.insert_text.setPlaceholderText("例: _[title]")
        insert_layout.addWidget(self.insert_text, 1)
        insert_layout.addWidget(QLabel("位置"))
        self.insert_position = QComboBox()
        self.insert_position.addItem("先頭", "start")
        self.insert_position.addItem("末尾", "end")
        self.insert_position.addItem("指定位置", "index")
        self.insert_position.currentIndexChanged.connect(self._insert_position_changed)
        insert_layout.addWidget(self.insert_position)
        self.insert_index_label = QLabel("何文字目の前")
        insert_layout.addWidget(self.insert_index_label)
        self.insert_index = QSpinBox()
        self.insert_index.setRange(1, 1_000_000)
        insert_layout.addWidget(self.insert_index)
        layout.addWidget(self.insert_widget)

        name_row = QHBoxLayout()
        name_row.addWidget(QLabel("新しい項目名"))
        self.field_name_input = QLineEdit()
        self.field_name_input.setPlaceholderText("例: 拡張子なしの名前")
        name_row.addWidget(self.field_name_input, 1)
        layout.addLayout(name_row)

        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton("閉じる")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        apply = QPushButton("新しい項目を追加")
        apply.clicked.connect(self._apply)
        buttons.addWidget(apply)
        layout.addLayout(buttons)

    def _operation_changed(self, *_args: object) -> None:
        operation = self.operation_combo.currentData()
        self.extension_note.setVisible(operation == "remove_extension")
        self.range_widget.setVisible(operation == "remove_range")
        self.insert_widget.setVisible(operation == "insert")
        source_name = self.source_combo.currentText().split("「")[-1].rstrip("」")
        suffix = {
            "remove_extension": "拡張子なし",
            "remove_range": "範囲削除",
            "insert": "挿入後",
        }.get(operation, "加工後")
        if not self.field_name_input.text().strip():
            self.field_name_input.setPlaceholderText(f"例: {source_name}_{suffix}")
        self._insert_position_changed()

    def _insert_position_changed(self, *_args: object) -> None:
        custom = self.insert_position.currentData() == "index"
        self.insert_index.setVisible(custom)
        self.insert_index_label.setVisible(custom)

    def _apply(self) -> None:
        source_index = self.source_combo.currentData()
        if not isinstance(source_index, int):
            return
        operation = self.operation_combo.currentData()
        field_name = self.field_name_input.text()
        try:
            if operation == "remove_extension":
                updated = add_extensionless_field(self._bundle, source_index, field_name)
            elif operation == "remove_range":
                updated = add_range_removed_field(
                    self._bundle,
                    source_index,
                    field_name,
                    start_anchor=self.range_start_anchor.currentData(),
                    start_position=self.range_start_spin.value(),
                    end_anchor=self.range_end_anchor.currentData(),
                    end_position=self.range_end_spin.value(),
                )
            elif operation == "insert":
                updated = add_inserted_field(
                    self._bundle,
                    source_index,
                    field_name,
                    text=self.insert_text.text(),
                    position=self.insert_position.currentData(),
                    index=self.insert_index.value(),
                )
            else:
                raise ValueError("加工方法を選択してください。")
        except ValueError as exc:
            self.status_label.setText(str(exc))
            self.status_label.setStyleSheet("color: #b91c1c; font-weight: 600;")
            return
        self.bundle_created.emit(updated)
        self.accept()
