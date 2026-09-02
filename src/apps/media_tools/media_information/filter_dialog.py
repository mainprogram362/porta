"""Compact editor for AND-connected workspace display conditions."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from media.file_attributes import STANDARD_CATALOG_FIELDS

from .browsing import FilterRule, WorkspaceRecord


_TEXT_OPERATORS = (
    ("含む", "contains"),
    ("含まない", "not_contains"),
    ("完全一致", "equals"),
    ("入力あり", "exists"),
    ("未入力", "missing"),
)
_RATING_OPERATORS = (
    ("以上", "at_least"),
    ("以下", "at_most"),
    ("等しい", "equals"),
    ("入力あり", "exists"),
    ("未入力", "missing"),
)


class _RuleRow(QWidget):
    def __init__(
        self,
        fields: tuple[tuple[str, str], ...],
        remove: Callable[["_RuleRow"], None],
        rule: FilterRule | None = None,
    ) -> None:
        super().__init__()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.field_combo = QComboBox()
        for label, key in fields:
            self.field_combo.addItem(label, key)
        layout.addWidget(self.field_combo, 2)
        self.operator_combo = QComboBox()
        layout.addWidget(self.operator_combo, 1)
        self.value_input = QLineEdit()
        self.value_input.setPlaceholderText("条件の値")
        layout.addWidget(self.value_input, 2)
        remove_button = QPushButton("削除")
        remove_button.clicked.connect(lambda: remove(self))
        layout.addWidget(remove_button)
        if rule is not None:
            field_index = self.field_combo.findData(rule.field_key)
            if field_index >= 0:
                self.field_combo.setCurrentIndex(field_index)
        self.field_combo.currentIndexChanged.connect(self._refresh_operators)
        self._refresh_operators()
        if rule is not None:
            operator_index = self.operator_combo.findData(rule.operator)
            if operator_index >= 0:
                self.operator_combo.setCurrentIndex(operator_index)
            self.value_input.setText(rule.value)
        self.operator_combo.currentIndexChanged.connect(self._refresh_value_state)
        self._refresh_value_state()

    def _refresh_operators(self) -> None:
        previous = self.operator_combo.currentData()
        operators = (
            _RATING_OPERATORS
            if self.field_combo.currentData() == "review.score"
            else _TEXT_OPERATORS
        )
        self.operator_combo.clear()
        for label, key in operators:
            self.operator_combo.addItem(label, key)
        previous_index = self.operator_combo.findData(previous)
        if previous_index >= 0:
            self.operator_combo.setCurrentIndex(previous_index)
        self._refresh_value_state()

    def _refresh_value_state(self) -> None:
        needs_value = self.operator_combo.currentData() not in {"exists", "missing"}
        self.value_input.setEnabled(needs_value)
        self.value_input.setPlaceholderText(
            "0〜10（例: 8）"
            if self.field_combo.currentData() == "review.score" and needs_value
            else "条件の値"
        )

    def rule(self) -> FilterRule:
        operator = str(self.operator_combo.currentData())
        value = (
            self.value_input.text().strip()
            if operator not in {"exists", "missing"}
            else ""
        )
        return FilterRule(str(self.field_combo.currentData()), operator, value)


class DetailedFilterDialog(QDialog):
    """Build multiple display conditions away from the main toolbar."""

    def __init__(
        self,
        records: tuple[WorkspaceRecord, ...],
        current_rules: tuple[FilterRule, ...],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("表示対象の詳細条件")
        self.setMinimumSize(760, 420)
        self._fields = _field_choices(records)
        self._rows: list[_RuleRow] = []
        layout = QVBoxLayout(self)
        explanation = QLabel(
            "すべての条件を満たす記録だけを表示します。評価は ★0〜★10 の数値で指定します。"
        )
        explanation.setWordWrap(True)
        layout.addWidget(explanation)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        self._rows_layout = QVBoxLayout(content)
        self._rows_layout.addStretch(1)
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)
        add_button = QPushButton("条件を追加")
        add_button.clicked.connect(self._add_rule)
        layout.addWidget(add_button)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._accept_if_valid)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        for rule in current_rules:
            self._add_rule(rule)
        if not current_rules:
            self._add_rule(FilterRule("review.score", "at_least", "8"))

    @property
    def rules(self) -> tuple[FilterRule, ...]:
        return tuple(row.rule() for row in self._rows)

    def _add_rule(self, rule: FilterRule | None = None) -> None:
        if len(self._rows) >= 30:
            return
        row = _RuleRow(self._fields, self._remove_rule, rule)
        self._rows.append(row)
        self._rows_layout.insertWidget(self._rows_layout.count() - 1, row)

    def _remove_rule(self, row: _RuleRow) -> None:
        if row in self._rows:
            self._rows.remove(row)
            row.deleteLater()

    def _accept_if_valid(self) -> None:
        for rule in self.rules:
            if rule.operator not in {"exists", "missing"} and not rule.value:
                self._show_error("値が必要な条件には、条件の値を入力してください。")
                return
            if rule.field_key == "review.score" and rule.operator in {
                "at_least",
                "at_most",
                "equals",
            }:
                try:
                    rating = float(rule.value)
                except ValueError:
                    self._show_error("評価には0〜10の数値を入力してください。")
                    return
                if not 0 <= rating <= 10:
                    self._show_error("評価には0〜10の数値を入力してください。")
                    return
        self.accept()

    def _show_error(self, message: str) -> None:
        QMessageBox.warning(self, "表示対象の詳細条件", message)


def _field_choices(
    records: tuple[WorkspaceRecord, ...],
) -> tuple[tuple[str, str], ...]:
    standard = [(field.label, field.key) for field in STANDARD_CATALOG_FIELDS]
    standard_keys = {key for _label, key in standard}
    extra_keys = sorted(
        {
            attribute.key
            for record in records
            for attribute in record.attributes
            if attribute.key not in standard_keys
        }
    )
    return tuple((*standard, *((key, key) for key in extra_keys)))
