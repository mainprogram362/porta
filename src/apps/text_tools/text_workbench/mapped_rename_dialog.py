"""Preview and explicitly execute per-path names supplied by a record bundle."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

from apps.file_tools.file_manager.rename_workflow import (
    RenamePlan,
    build_mapped_rename_preview,
    execute_rename_plan,
)
from records.record_bundle import RecordBundle
from gui import NoWheelComboBox, PathListInput

from .rename_mapping import match_record_paths


class MappedRenameDialog(QDialog):
    """Match selected paths to rows, then use the shared safe rename workflow."""

    def __init__(self, bundle: RecordBundle, parent=None) -> None:
        super().__init__(parent)
        self._bundle = bundle
        self._plan: RenamePlan | None = None
        self.setWindowTitle("一時対応表からファイル名を変更")
        self._build_ui()
        self._refresh_preview()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        guidance = QLabel(
            "対応表の照合列を現在のファイル名へ結び、"
            "変更後の名前を別の列から取得します。"
            "一致が曖昧な場合や名前が衝突する場合は実行できません。"
        )
        guidance.setWordWrap(True)
        layout.addWidget(guidance)

        paths_box = QGroupBox("変更するファイル・フォルダ")
        paths_layout = QVBoxLayout(paths_box)
        self.path_input = PathListInput(
            rows=8,
            accepted_path_kind="all",
            show_controls=True,
            show_column_headers=True,
            path_column_label="変更元パス",
        )
        self.path_input.textChanged.connect(self._refresh_preview)
        paths_layout.addWidget(self.path_input)
        add_actions = QHBoxLayout()
        add_files = QPushButton("ファイルを追加…")
        add_files.clicked.connect(self._add_files)
        add_actions.addWidget(add_files)
        add_directory = QPushButton("フォルダを追加…")
        add_directory.clicked.connect(self._add_directory)
        add_actions.addWidget(add_directory)
        add_actions.addStretch(1)
        paths_layout.addLayout(add_actions)
        layout.addWidget(paths_box)

        settings = QHBoxLayout()
        settings.addWidget(QLabel("照合列"))
        self.key_combo = NoWheelComboBox()
        settings.addWidget(self.key_combo, 1)
        settings.addWidget(QLabel("変更後の名前列"))
        self.output_combo = NoWheelComboBox()
        settings.addWidget(self.output_combo, 1)
        for field_name in self._bundle.field_names:
            self.key_combo.addItem(field_name, field_name)
            self.output_combo.addItem(field_name, field_name)
        if self.output_combo.count() > 1:
            self.output_combo.setCurrentIndex(1)
        self.key_combo.currentIndexChanged.connect(self._refresh_preview)
        self.output_combo.currentIndexChanged.connect(self._refresh_preview)

        settings.addWidget(QLabel("照合方法"))
        self.mode_combo = NoWheelComboBox()
        self.mode_combo.addItem("名前と完全一致", "exact")
        self.mode_combo.addItem("名前に照合キーを含む", "contains")
        self.mode_combo.addItem("上から同じ順番（要同数）", "order")
        self.mode_combo.currentIndexChanged.connect(self._refresh_preview)
        settings.addWidget(self.mode_combo, 1)
        layout.addLayout(settings)

        options = QHBoxLayout()
        self.case_sensitive_check = QCheckBox("大文字・小文字を区別")
        self.case_sensitive_check.setChecked(True)
        self.case_sensitive_check.toggled.connect(self._refresh_preview)
        options.addWidget(self.case_sensitive_check)
        self.include_extension_check = QCheckBox("変更後の名前列に拡張子を含む")
        self.include_extension_check.setToolTip(
            "オフなら、ファイルの現在の拡張子を変更後の名前へ自動的に付けます。"
        )
        self.include_extension_check.toggled.connect(self._refresh_preview)
        options.addWidget(self.include_extension_check)
        options.addStretch(1)
        layout.addLayout(options)

        preview_box = QGroupBox("実行前プレビュー")
        preview_layout = QVBoxLayout(preview_box)
        self.preview_tree = QTreeWidget()
        self.preview_tree.setHeaderLabels(("変更元", "照合キー", "変更後"))
        self.preview_tree.setRootIsDecorated(False)
        self.preview_tree.setAlternatingRowColors(True)
        for column in range(3):
            self.preview_tree.header().setSectionResizeMode(
                column, self.preview_tree.header().ResizeMode.Stretch
            )
        preview_layout.addWidget(self.preview_tree, 1)
        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        self.status_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        preview_layout.addWidget(self.status_label)
        layout.addWidget(preview_box, 1)

        buttons = QDialogButtonBox()
        close_button = buttons.addButton("閉じる", QDialogButtonBox.ButtonRole.RejectRole)
        close_button.clicked.connect(self.reject)
        self.execute_button = buttons.addButton(
            "確認してリネーム…", QDialogButtonBox.ButtonRole.AcceptRole
        )
        self.execute_button.clicked.connect(self._execute)
        layout.addWidget(buttons)

    def _add_files(self) -> None:
        paths, _selected_filter = QFileDialog.getOpenFileNames(
            self, "変更するファイルを追加", "", "すべてのファイル (*)"
        )
        if paths:
            self.path_input.append_items(paths)

    def _add_directory(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "変更するフォルダを追加")
        if path:
            self.path_input.append_items((path,))

    def _refresh_preview(self, *_args: object) -> None:
        self.preview_tree.clear()
        paths = tuple(Path(value) for value in self.path_input.items(deduplicate=False))
        result = match_record_paths(
            self._bundle,
            paths,
            key_field=str(self.key_combo.currentData() or ""),
            output_field=str(self.output_combo.currentData() or ""),
            mode=self.mode_combo.currentData(),
            case_sensitive=self.case_sensitive_check.isChecked(),
        )
        if result.issues:
            self._plan = None
            self.execute_button.setEnabled(False)
            self.status_label.setText("\n".join(result.issues))
            for match in result.matches:
                self.preview_tree.addTopLevelItem(
                    QTreeWidgetItem((match.source.name, match.key, match.output_name))
                )
            return
        preview = build_mapped_rename_preview(
            ((match.source, match.output_name) for match in result.matches),
            include_extension=self.include_extension_check.isChecked(),
        )
        self._plan = preview.plan
        self.execute_button.setEnabled(preview.is_ready)
        if preview.plan is None:
            self.status_label.setText(preview.text)
            return
        key_by_source = {match.source: match.key for match in result.matches}
        for rename in preview.plan.renames:
            self.preview_tree.addTopLevelItem(
                QTreeWidgetItem(
                    (rename.source.name, key_by_source[rename.source], rename.output.name)
                )
            )
        self.status_label.setText(f"{len(preview.plan.renames)}件の変更予定を確認できます。")

    def _execute(self) -> None:
        plan = self._plan
        if plan is None:
            return
        answer = QMessageBox.question(
            self,
            "ファイル名変更の最終確認",
            f"表示中の{len(plan.renames)}件をリネームします。実行しますか？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            results = execute_rename_plan(plan)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "リネームできません", str(exc))
            self._refresh_preview()
            return
        self.path_input.setPlainText("\n".join(str(path) for path in results))
        self._plan = None
        self.execute_button.setEnabled(False)
        self.status_label.setText(f"{len(results)}件のファイル名を変更しました。")
        QMessageBox.information(self, "リネーム完了", self.status_label.text())
