"""Compact editor for ordered batch-rename rules."""

from __future__ import annotations

from PySide6.QtCore import Signal, Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QSpinBox,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from apps.file_tools.file_manager.rename_workflow import RenameRule
from gui import NoWheelComboBox
from gui.flow_layout import FlowLayout
from gui.layout_policy import set_item_view_rows


class RenamePanel(QWidget):
    """Edit a small ordered rule list without expanding the whole workspace."""

    rules_changed = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._rules: list[RenameRule] = []
        self._build_ui()
        self._update_rule_fields()

    def rules(self) -> tuple[RenameRule, ...]:
        """Return the current rules in the order they will be applied."""
        return tuple(self._rules)

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        extension_settings = QHBoxLayout()
        self.include_extension_checkbox = QCheckBox("拡張子を含めて操作する")
        self.include_extension_checkbox.setChecked(False)
        extension_settings.addWidget(self.include_extension_checkbox)
        extension_settings.addStretch()
        layout.addLayout(extension_settings)

        settings = QHBoxLayout()
        self.regex_checkbox = QCheckBox("正規表現")
        self.regex_checkbox.setToolTip(
            "文字列の削除・置換だけで使えます。通常はオフのまま、入力をそのまま探します。"
            "置換では \\1 などのキャプチャ参照も使えます。"
        )
        settings.addWidget(self.regex_checkbox)

        self.rule_kind_combo = NoWheelComboBox()
        self.rule_kind_combo.addItem("先頭に追加", "prepend")
        self.rule_kind_combo.addItem("指定位置に挿入", "insert")
        self.rule_kind_combo.addItem("末尾に追加", "append")
        self.rule_kind_combo.addItem("範囲を削除", "remove_range")
        self.rule_kind_combo.addItem("末尾から削除", "trim_end")
        self.rule_kind_combo.addItem("文字列を削除", "remove_text")
        self.rule_kind_combo.addItem("文字列を置換", "replace")
        self.rule_kind_combo.addItem("名前内の空白をすべて削除", "remove_spaces")
        self.rule_kind_combo.addItem("全角を半角へ統一…", "normalize_width")
        settings.addWidget(self.rule_kind_combo, 1)
        layout.addLayout(settings)

        fields = FlowLayout()
        self.first_label = QLabel()
        fields.addWidget(self.first_label)
        self.first_spin = QSpinBox()
        self.first_spin.setRange(0, 1_000_000)
        fields.addWidget(self.first_spin)
        self.second_label = QLabel()
        fields.addWidget(self.second_label)
        self.second_spin = QSpinBox()
        self.second_spin.setRange(0, 1_000_000)
        fields.addWidget(self.second_spin)
        self.text_input = QLineEdit()
        fields.addWidget(self.text_input)
        self.replacement_input = QLineEdit()
        fields.addWidget(self.replacement_input)
        self.add_rule_button = QPushButton("ルールを追加")
        self.add_rule_button.clicked.connect(self.add_rule)
        fields.addWidget(self.add_rule_button)
        layout.addLayout(fields)

        self.width_options = QWidget()
        width_layout = QHBoxLayout(self.width_options)
        width_layout.setContentsMargins(0, 0, 0, 0)
        width_layout.addWidget(QLabel("半角にするもの"))
        self.width_letters = QCheckBox("英字")
        self.width_digits = QCheckBox("数字")
        self.width_symbols = QCheckBox("記号")
        for checkbox in (self.width_letters, self.width_digits, self.width_symbols):
            checkbox.setChecked(True)
            width_layout.addWidget(checkbox)
        self.width_options.setToolTip("初期状態はすべて。Ａ→A、１→1、！→!。ーは変更しません。")
        width_layout.addStretch(1)
        layout.addWidget(self.width_options)

        self.rule_list = QListWidget()
        set_item_view_rows(self.rule_list, minimum=2, maximum=4, header=False)
        layout.addWidget(self.rule_list)
        actions = QHBoxLayout()
        delete_button = QPushButton("選択を削除")
        delete_button.clicked.connect(self.delete_selected_rule)
        actions.addWidget(delete_button)
        clear_button = QPushButton("全て空にする")
        clear_button.clicked.connect(self.clear_rules)
        actions.addWidget(clear_button)
        layout.addLayout(actions)
        self.status_label = QLabel()
        self.status_label.setVisible(False)
        layout.addWidget(self.status_label)
        self.preview_label = QLabel("今回のリネーム予定（変更前 → 変更後）")
        layout.addWidget(self.preview_label)
        self.preview_tree = QTreeWidget()
        self.preview_tree.setHeaderLabels(("変更前のファイル名", "変更後のファイル名"))
        self.preview_tree.setRootIsDecorated(False)
        set_item_view_rows(self.preview_tree, minimum=4)
        self.preview_tree.setTextElideMode(Qt.TextElideMode.ElideNone)
        self.preview_tree.header().setStretchLastSection(False)
        self.preview_tree.header().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.preview_tree.header().setResizeContentsPrecision(-1)
        layout.addWidget(self.preview_tree, 1)
        self.set_preview((), "リネーム規則を追加すると、ここに変更予定を表示します。")

        self.rule_kind_combo.currentIndexChanged.connect(self._update_rule_fields)
        self.include_extension_checkbox.toggled.connect(self.rules_changed)
        self.regex_checkbox.toggled.connect(self._update_rule_fields)

    def _set_status(self, text: str) -> None:
        """Show brief feedback only when the rule editor has something to report."""
        self.status_label.setText(text)
        self.status_label.setVisible(bool(text))

    def include_extension(self) -> bool:
        """Return whether rules should operate on full file names including suffixes."""
        return self.include_extension_checkbox.isChecked()

    def set_preview(
        self, pairs: tuple[tuple[str, str], ...], message: str = ""
    ) -> None:
        """Show the current checked files' planned names without changing them."""
        self.preview_tree.clear()
        if message:
            item = QTreeWidgetItem((message, ""))
            item.setDisabled(True)
            self.preview_tree.addTopLevelItem(item)
            return
        for before, after in pairs:
            item = QTreeWidgetItem((before, after))
            item.setToolTip(0, before)
            item.setToolTip(1, after)
            self.preview_tree.addTopLevelItem(item)

    def _update_rule_fields(self) -> None:
        kind = self.rule_kind_combo.currentData()
        uses_text = kind in {"prepend", "insert", "append", "remove_text", "replace"}
        uses_first = kind in {"insert", "remove_range", "trim_end"}
        uses_second = kind == "remove_range"
        uses_replacement = kind == "replace"
        uses_regex = kind in {"remove_text", "replace"}
        self.text_input.setVisible(uses_text)
        self.replacement_input.setVisible(uses_replacement)
        self.regex_checkbox.setVisible(uses_regex)
        self.first_label.setVisible(uses_first)
        self.first_spin.setVisible(uses_first)
        self.second_label.setVisible(uses_second)
        self.second_spin.setVisible(uses_second)
        self.width_options.setVisible(kind == "normalize_width")
        self.first_label.setText(
            "位置" if kind == "insert" else "開始" if kind == "remove_range" else "文字数"
        )
        self.second_label.setText("終了")
        self.text_input.setPlaceholderText(
            "追加する文字列"
            if kind in {"prepend", "insert", "append"}
            else "正規表現" if self.regex_checkbox.isChecked() else "置換・削除する文字列"
        )
        self.replacement_input.setPlaceholderText(
            "置換後の文字列（空欄可、\\1 など可）"
            if self.regex_checkbox.isChecked()
            else "置換後の文字列（空欄可）"
        )
        self.first_spin.setMinimum(1 if kind in {"insert", "remove_range", "trim_end"} else 0)
        self.second_spin.setMinimum(1)

    def add_rule(self) -> None:
        """Add the compactly entered rule to the ordered rule list."""
        kind = self.rule_kind_combo.currentData()
        text = self.text_input.text()
        if kind in {"prepend", "insert", "append", "remove_text", "replace"} and not text:
            self._set_status("必要な文字列を入力してください。")
            return
        rule = RenameRule(
            kind=kind,
            text=text,
            replacement=self.replacement_input.text(),
            first=self.first_spin.value(),
            second=self.second_spin.value(),
            use_regex=self.regex_checkbox.isChecked() if kind in {"remove_text", "replace"} else False,
            width_letters=self.width_letters.isChecked(),
            width_digits=self.width_digits.isChecked(),
            width_symbols=self.width_symbols.isChecked(),
        )
        self._rules.append(rule)
        self.text_input.clear()
        self.replacement_input.clear()
        self._refresh_rule_list()
        self._set_status("ルールを追加しました。")
        self.rules_changed.emit()

    def move_selected_rule(self, offset: int) -> None:
        row = self.rule_list.currentRow()
        destination = row + offset
        if row < 0 or destination < 0 or destination >= len(self._rules):
            return
        self._rules[row], self._rules[destination] = self._rules[destination], self._rules[row]
        self._refresh_rule_list(selected_row=destination)
        self.rules_changed.emit()

    def delete_selected_rule(self) -> None:
        row = self.rule_list.currentRow()
        if row < 0:
            return
        self._rules.pop(row)
        self._refresh_rule_list()
        self.rules_changed.emit()

    def clear_rules(self) -> None:
        if not self._rules:
            return
        self._rules.clear()
        self._refresh_rule_list()
        self.rules_changed.emit()

    def reset_to_defaults(self) -> None:
        """Restore the rename editor's initial in-memory settings."""
        self.include_extension_checkbox.setChecked(False)
        self.regex_checkbox.setChecked(False)
        self.rule_kind_combo.setCurrentIndex(0)
        self.first_spin.setValue(0)
        self.second_spin.setValue(0)
        self.text_input.clear()
        self.replacement_input.clear()
        for checkbox in (self.width_letters, self.width_digits, self.width_symbols):
            checkbox.setChecked(True)
        self._rules.clear()
        self._refresh_rule_list()
        self._set_status("")
        self.rules_changed.emit()

    def restore_rules(self, rules: tuple[RenameRule, ...], *, include_extension: bool) -> None:
        """Restore persisted-in-memory rule state for the file-manager undo history."""
        self._rules = list(rules)
        self.include_extension_checkbox.setChecked(include_extension)
        self._refresh_rule_list()
        self.rules_changed.emit()

    def _refresh_rule_list(self, *, selected_row: int | None = None) -> None:
        self.rule_list.clear()
        self.rule_list.addItems(_describe_rule(rule) for rule in self._rules)
        if selected_row is not None:
            self.rule_list.setCurrentRow(selected_row)


def _describe_rule(rule: RenameRule) -> str:
    if rule.kind == "prepend":
        return f"先頭に「{rule.text}」を追加"
    if rule.kind == "insert":
        return f"{rule.first}文字目に「{rule.text}」を挿入"
    if rule.kind == "append":
        return f"末尾に「{rule.text}」を追加"
    if rule.kind == "remove_range":
        return f"{rule.first}〜{rule.second}文字目を削除"
    if rule.kind == "remove_text":
        return _match_description(rule, "削除")
    if rule.kind == "replace":
        prefix = "正規表現 " if rule.use_regex else ""
        return f"{prefix}「{rule.text}」を「{rule.replacement}」へ置換"
    if rule.kind == "remove_spaces":
        return "名前内の空白をすべて削除"
    if rule.kind == "normalize_width":
        groups = []
        if rule.width_letters:
            groups.append("英字")
        if rule.width_digits:
            groups.append("数字")
        if rule.width_symbols:
            groups.append("記号")
        return "全角を半角へ統一（" + "・".join(groups or ["対象なし"]) + "）"
    return f"末尾から{rule.first}文字を削除"


def _match_description(rule: RenameRule, action: str) -> str:
    prefix = "正規表現 " if rule.use_regex else ""
    return f"{prefix}「{rule.text}」を{action}"
