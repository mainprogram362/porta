"""Frozen final preview and execution window for file-manager operations."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
)

from foundation.path import normalize_path
from gui import PathLineInput
from gui.operation_worker import OperationWorker
from gui.layout_policy import preferred_window_size

from .operation_service import (
    OperationKind,
    OperationPresentation,
    build_operation_presentation,
    execute_operation,
)
from .path_context import add_registered_paths_menu
from .rename_workflow import RenameRule


class OperationConfirmationDialog(QDialog):
    """Own one immutable source/settings snapshot and a final destination."""

    destination_remembered = Signal(str)
    operation_succeeded = Signal(object, object)
    operation_failed = Signal(str)

    def __init__(
        self,
        *,
        kind: OperationKind,
        targets: tuple[Path, ...],
        mode: str,
        destinations: tuple[Path, ...],
        rename_rules: tuple[RenameRule, ...],
        include_extension: bool,
        initial_destination: str,
        registered_paths: list[str],
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setWindowTitle("実行内容の最終確認")
        self.resize(preferred_window_size(self))
        self._kind = kind
        self._targets = targets
        self._mode = mode
        self._destinations = destinations
        self._rename_rules = rename_rules
        self._include_extension = include_extension
        self._presentation: OperationPresentation | None = None
        self._completed = False
        self._worker = None

        layout = QVBoxLayout(self)
        explanation = QLabel(
            "この画面を開いた時点の対象・方式・設定を固定しています。"
            "元のファイルマネージャーを変更しても、この確認内容は変わりません。"
        )
        explanation.setWordWrap(True)
        layout.addWidget(explanation)

        self.destination_label = QLabel("出力先")
        layout.addWidget(self.destination_label)
        self.destination_input = PathLineInput(drop_as="directory")
        self.destination_input.setPlaceholderText("出力先フォルダを入力またはドロップ")
        self.destination_input.setText(initial_destination)
        self.destination_input.set_context_menu_augmenter(
            lambda menu: add_registered_paths_menu(
                menu,
                registered_paths,
                title="登録パスを設定（置換）",
                choose_path=self._set_registered_destination,
            )
        )
        self.destination_input.textChanged.connect(self.refresh_preview)
        layout.addWidget(self.destination_input)

        self.summary_label = QLabel()
        self.summary_label.setWordWrap(True)
        layout.addWidget(self.summary_label)
        self.preview_text = QTextEdit()
        self.preview_text.setReadOnly(True)
        layout.addWidget(self.preview_text, 1)
        self.status_label = QLabel()
        self.status_label.setTextFormat(Qt.TextFormat.PlainText)
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        buttons = QDialogButtonBox()
        self.execute_button = QPushButton("実行する")
        self.execute_button.clicked.connect(self.execute_current)
        buttons.addButton(self.execute_button, QDialogButtonBox.ButtonRole.AcceptRole)
        close = buttons.addButton("閉じる", QDialogButtonBox.ButtonRole.RejectRole)
        close.clicked.connect(self.reject)
        self.cancel_button = buttons.addButton("処理を取り消す", QDialogButtonBox.ButtonRole.ActionRole)
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel_operation)
        layout.addWidget(buttons)

        self._uses_single_destination = kind in {"copy", "move"} and mode == "simple"
        self._uses_single_destination = self._uses_single_destination or (
            kind == "zip" and mode == "simple"
        )
        self.destination_label.setVisible(self._uses_single_destination)
        self.destination_input.setVisible(self._uses_single_destination)
        self.refresh_preview()

    def _set_registered_destination(self, value: str) -> None:
        self.destination_input.set_path_from_external_value(normalize_path(value))

    def _destination_text(self) -> str:
        if self._uses_single_destination:
            return self.destination_input.text()
        if self._mode == "one_to_one":
            return "\n".join(str(path) for path in self._destinations)
        return ""

    def refresh_preview(self) -> None:
        if self._completed or self._worker is not None:
            return
        presentation = build_operation_presentation(
            self._kind,
            "\n".join(str(path) for path in self._targets),
            self._destination_text(),
            mode=self._mode,
            target_count=len(self._targets),
            destination_count=(
                1 if self._uses_single_destination and self.destination_input.text()
                else (len(self._destinations) if self._mode == "one_to_one" else 0)
            ),
            rename_rules=list(self._rename_rules),
            include_extension=self._include_extension,
        )
        self._presentation = presentation
        self.summary_label.setText(" ｜ ".join(presentation.summary_lines))
        self.preview_text.setPlainText(presentation.text)
        self.execute_button.setText(presentation.execute_label)
        self.execute_button.setEnabled(presentation.is_ready)
        self.status_label.setText(
            "最終確認済み：実行できます。"
            if presentation.is_ready
            else "実行できません。プレビュー内の理由を確認してください。"
        )
        if (
            presentation.is_ready
            and self._uses_single_destination
            and self.destination_input.text()
        ):
            self.destination_remembered.emit(self.destination_input.text())

    def execute_current(self) -> None:
        """Rebuild once, then let the workflow revalidate immediately before writing."""
        if self._worker is not None or self._completed:
            return
        self.refresh_preview()
        presentation = self._presentation
        if presentation is None or not presentation.is_ready:
            return
        self.execute_button.setEnabled(False)
        self.status_label.setText("実行中です。この画面を閉じないでください。")
        self.destination_input.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self._worker = OperationWorker(lambda: execute_operation(presentation), self)
        self._worker.progress.connect(self._show_progress)
        self._worker.succeeded.connect(self._operation_finished)
        self._worker.failed.connect(self._operation_failed)
        self._worker.finished.connect(self._worker_finished)
        self._worker.start()

    def _show_progress(self, text):
        self.status_label.setText(f"処理中: {text}")

    def cancel_operation(self):
        if self._worker is not None:
            self._worker.requestInterruption()
            self.cancel_button.setEnabled(False)
            self.status_label.setText("取消を要求しました。安全に停止できる処理境界まで待っています。")

    def _operation_failed(self, error):
        self._completed = True
        records = getattr(error, "records", ())
        state = "一部完了" if records else "停止（結果を確認）"
        if getattr(error, "cancelled", False):
            state += "・取消"
        message = str(error)
        self.status_label.setText(f"{state}：再実行する場合は、残った対象で新しく確認画面を開いてください。")
        details = "\n".join(f"{row.state}: {row.source}\n → {row.output}" for row in records)
        self.preview_text.setPlainText(self.preview_text.toPlainText()
                                      + f"\n\n=== {state} ===\n{details}\n{message}")
        self.execute_button.setEnabled(False)
        self.operation_failed.emit(message)

    def _worker_finished(self):
        worker = self._worker
        self._worker = None
        self.cancel_button.setEnabled(False)
        if worker is not None:
            worker.deleteLater()

    def reject(self):
        if self._worker is not None:
            self.cancel_operation()
            return
        super().reject()

    def closeEvent(self, event):
        if self._worker is not None:
            self.cancel_operation()
            event.ignore()
            return
        super().closeEvent(event)

    def _operation_finished(self, results):
        presentation = self._presentation

        self._completed = True
        self.destination_input.setEnabled(False)
        self.execute_button.setEnabled(False)
        self.status_label.setText(f"{presentation.completed_label}：{len(results)}件")
        result_text = "\n".join(str(path) for path in results)
        self.preview_text.setPlainText(self.preview_text.toPlainText()
                                      + f"\n\n=== 実行結果 ===\n{presentation.result_label}:\n{result_text}")
        self.operation_succeeded.emit(presentation, results)
