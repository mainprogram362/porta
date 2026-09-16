"""A reversible, same-filesystem parent-folder move window."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .parent_move_workflow import (
    ParentMovePlan,
    build_parent_move_preview,
    execute_parent_move_plan,
    undo_parent_move_plan,
)
from gui.layout_policy import preferred_window_size


class ParentMoveDialog(QDialog):
    """Show one frozen move plan and retain its undo only until this dialog closes."""

    paths_moved = Signal(object)
    paths_restored = Signal(object)

    def __init__(self, paths: Iterable[Path], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._paths = tuple(paths)
        self._plan: ParentMovePlan | None = None
        self._has_completed_move = False
        self.setWindowTitle("PORTA — 親フォルダへ移動")
        self.resize(preferred_window_size(self))
        self._build_ui()
        self._build_preview()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        heading = QLabel("親フォルダへ移動")
        heading.setStyleSheet("font-size: 16px; font-weight: 600;")
        layout.addWidget(heading)
        explanation = QLabel(
            "例：a/b/c/d.mp4 → a/b/d.mp4。"
            "同じファイルシステム上の名前変更だけを許可し、コピーを伴う移動は中止します。"
        )
        explanation.setWordWrap(True)
        layout.addWidget(explanation)

        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(("移動前", "移動後"))
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(True)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table, 1)

        self.status_label = QLabel()
        self.status_label.setTextFormat(Qt.TextFormat.PlainText)
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        close_button = QPushButton("閉じる")
        close_button.clicked.connect(self.reject)
        buttons.addWidget(close_button)
        self.undo_button = QPushButton("戻す")
        self.undo_button.setToolTip("このウィンドウを開いている間に実行した今回の移動だけを戻します。")
        self.undo_button.clicked.connect(self.undo)
        self.undo_button.setEnabled(False)
        buttons.addWidget(self.undo_button)
        self.execute_button = QPushButton("この内容で移動…")
        self.execute_button.clicked.connect(self.execute)
        buttons.addWidget(self.execute_button)
        layout.addLayout(buttons)

    def _build_preview(self) -> None:
        preview = build_parent_move_preview(self._paths)
        self._plan = preview.plan
        self.table.setRowCount(0)
        if self._plan is not None:
            self.table.setRowCount(len(self._plan.moves))
            for row, move in enumerate(self._plan.moves):
                self.table.setItem(row, 0, QTableWidgetItem(str(move.source)))
                self.table.setItem(row, 1, QTableWidgetItem(str(move.destination)))
        self.status_label.setText(preview.text)
        self.status_label.setStyleSheet("" if preview.is_ready else "color: #b44;")
        self.execute_button.setEnabled(preview.is_ready and not self._has_completed_move)

    def execute(self) -> None:
        plan = self._plan
        if plan is None:
            return
        answer = QMessageBox.question(
            self,
            "親フォルダへ移動",
            f"{len(plan.moves)}件を親フォルダへ移動します。\n"
            "同名衝突や別ファイルシステムがあれば実行せず中止します。\n"
            "実行しますか？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            execute_parent_move_plan(plan)
        except OSError as exc:
            self._show_partial_failure(exc, self.paths_moved)
            return
        self._has_completed_move = True
        self.execute_button.setEnabled(False)
        self.undo_button.setEnabled(True)
        self.status_label.setText(
            f"{len(plan.moves)}件を移動しました。"
            "このウィンドウを閉じるまで「戻す」で今回の移動を戻せます。"
        )
        self.status_label.setStyleSheet("")
        self.paths_moved.emit({str(move.source): str(move.destination) for move in plan.moves})

    def undo(self) -> None:
        plan = self._plan
        if plan is None or not self._has_completed_move:
            return
        try:
            undo_parent_move_plan(plan)
        except OSError as exc:
            self._show_partial_failure(exc, self.paths_restored)
            return
        self._has_completed_move = False
        self.undo_button.setEnabled(False)
        self.execute_button.setEnabled(True)
        self.status_label.setText(f"{len(plan.moves)}件を元の場所へ戻しました。")
        self.status_label.setStyleSheet("")
        self.paths_restored.emit({str(move.destination): str(move.source) for move in plan.moves})

    def _show_partial_failure(self, error, signal):
        records = getattr(error, "records", ())
        details = "\n".join(f"{row.state}: {row.source}\n → {row.output}" for row in records)
        self.status_label.setText(f"停止しました。結果を確認し、残った対象で開き直してください。\n{error}\n{details}")
        self.status_label.setStyleSheet("color: #b44;")
        self.execute_button.setEnabled(False)
        self.undo_button.setEnabled(False)
        if records:
            signal.emit({row.source: row.output for row in records})
