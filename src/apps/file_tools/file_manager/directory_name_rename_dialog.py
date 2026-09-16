"""Separate preview/execution window for inserting a parent directory name."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from gui import NoWheelComboBox
from gui.layout_policy import preferred_window_size

from .directory_name_rename import (
    DirectoryNameRule,
    build_directory_name_rename_preview,
)
from .rename_workflow import RenamePreview, execute_rename_plan


class DirectoryNameRenameDialog(QDialog):
    """Preview filename changes made from each file's immediate parent directory."""

    paths_renamed = Signal(object)

    def __init__(self, paths: Iterable[Path], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._paths = tuple(paths)
        self._preview = RenamePreview(None, "")
        self.setWindowTitle("PORTA — 親フォルダ名をファイル名へ")
        self.resize(preferred_window_size(self))
        self._build_ui()
        self.refresh_preview()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        heading = QLabel("親フォルダ名をファイル名へ追加・置換")
        heading.setStyleSheet("font-size: 16px; font-weight: 600;")
        layout.addWidget(heading)
        hint = QLabel(
            "各ファイルの直上のフォルダ名を [@insert] に差し込みます。"
            "例：test/aiueo.mp4 に、末尾へ _[@insert] を追加 → aiueo_test.mp4"
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)

        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel("操作"))
        self.mode_combo = NoWheelComboBox()
        self.mode_combo.addItem("ファイル名へ挿入", "insert")
        self.mode_combo.addItem("ファイル名そのものを置換", "replace")
        self.mode_combo.currentIndexChanged.connect(self._mode_changed)
        mode_row.addWidget(self.mode_combo, 1)
        mode_row.addWidget(QLabel("挿入位置"))
        self.position_combo = NoWheelComboBox()
        self.position_combo.addItem("先頭", "start")
        self.position_combo.addItem("末尾", "end")
        self.position_combo.addItem("指定文字位置", "position")
        self.position_combo.setCurrentIndex(1)
        self.position_combo.currentIndexChanged.connect(self._position_changed)
        mode_row.addWidget(self.position_combo, 1)
        self.position_spin = QSpinBox()
        self.position_spin.setRange(1, 1_000_000)
        self.position_spin.setValue(1)
        self.position_spin.setToolTip("名前本体の何文字目の手前へ挿入するかを、1から数えます。")
        self.position_spin.valueChanged.connect(self.refresh_preview)
        mode_row.addWidget(self.position_spin)
        layout.addLayout(mode_row)

        template_row = QHBoxLayout()
        template_row.addWidget(QLabel("装飾"))
        self.template_input = QLineEdit("_[@insert]")
        self.template_input.setToolTip("[@insert] が各ファイルの親フォルダ名に置き換わります。")
        self.template_input.textChanged.connect(self.refresh_preview)
        template_row.addWidget(self.template_input, 1)
        token_button = QPushButton("[@insert] を挿入")
        token_button.clicked.connect(lambda: self.template_input.insert("[@insert]"))
        template_row.addWidget(token_button)
        layout.addLayout(template_row)

        self.extension_check = QCheckBox("現在の拡張子を維持する")
        self.extension_check.setChecked(True)
        self.extension_check.toggled.connect(self.refresh_preview)
        layout.addWidget(self.extension_check)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(("元のパス", "親フォルダ名", "変更後の名前"))
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(True)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table, 1)

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
        self._mode_changed()

    def _mode_changed(self, *_args: object) -> None:
        inserting = self.mode_combo.currentData() == "insert"
        self.position_combo.setVisible(inserting)
        self.position_spin.setVisible(inserting and self.position_combo.currentData() == "position")
        self.refresh_preview()

    def _position_changed(self, *_args: object) -> None:
        self.position_spin.setVisible(self.position_combo.currentData() == "position")
        self.refresh_preview()

    def _current_rule(self) -> DirectoryNameRule:
        return DirectoryNameRule(
            mode=str(self.mode_combo.currentData()),  # type: ignore[arg-type]
            template=self.template_input.text(),
            insert_position=str(self.position_combo.currentData()),  # type: ignore[arg-type]
            position=self.position_spin.value(),
            preserve_extension=self.extension_check.isChecked(),
        )

    def refresh_preview(self, *_args: object) -> None:
        if not hasattr(self, "table"):
            return
        self._preview = build_directory_name_rename_preview(self._paths, self._current_rule())
        outputs = (
            [rename.output.name for rename in self._preview.plan.renames]
            if self._preview.plan is not None
            else ["—" for _path in self._paths]
        )
        self.table.setRowCount(len(self._paths))
        for row, (path, output_name) in enumerate(zip(self._paths, outputs)):
            self.table.setItem(row, 0, QTableWidgetItem(str(path)))
            self.table.setItem(row, 1, QTableWidgetItem(path.parent.name))
            self.table.setItem(row, 2, QTableWidgetItem(output_name))
        if self._preview.plan is None:
            self.status_label.setText(self._preview.text.replace("\n", " "))
            self.status_label.setStyleSheet("color: #b44;")
        else:
            self.status_label.setText(
                f"実行可能：{len(self._preview.plan.renames)}件。"
                "同じフォルダ内での同名衝突があれば実行しません。"
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
            "親フォルダ名をファイル名へ",
            f"{len(plan.renames)}件の名前を変更します。\n実行しますか？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        changes = {str(rename.source): str(rename.output) for rename in plan.renames}
        try:
            execute_rename_plan(plan)
        except OSError as exc:
            QMessageBox.warning(self, "名前を変更できません", str(exc))
            self.refresh_preview()
            return
        self.paths_renamed.emit(changes)
        QMessageBox.information(self, "名前を変更しました", f"{len(plan.renames)}件を変更しました。")
        self.accept()
