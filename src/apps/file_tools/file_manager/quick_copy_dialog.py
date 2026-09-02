"""Independent, progress-aware copy window opened from the file-manager menu."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
from threading import Condition

from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
)

from foundation.path import path_entry_exists
from gui import PathLineInput

from .copy_workflow import CopyPlan, build_copy_preview


class _CopyCancelled(Exception):
    """Internal cooperative-cancellation marker."""


class CooperativeCopyThread(QThread):
    """Copy a frozen plan while honoring pause and cancellation checkpoints."""

    progress_changed = Signal(object, object)
    stage_changed = Signal(str)
    paused_changed = Signal(bool)
    succeeded = Signal(object)
    failed = Signal(str)
    cancelled = Signal()

    _CHUNK_SIZE = 4 * 1024 * 1024

    def __init__(self, plan: CopyPlan) -> None:
        super().__init__()
        self._plan = plan
        self._condition = Condition()
        self._pause_requested = False
        self._pause_at_boundary = False
        self._cancel_requested = False
        self._paused_reported = False
        self._copied_bytes = 0
        self._total_bytes = 0
        self._owned_roots: list[Path] = []

    def request_pause(self) -> None:
        with self._condition:
            self._pause_requested = True

    def request_resume(self) -> None:
        with self._condition:
            self._pause_requested = False
            self._pause_at_boundary = False
            self._condition.notify_all()

    def request_pause_at_next_file(self, enabled: bool = True) -> None:
        with self._condition:
            self._pause_at_boundary = enabled
            if not enabled:
                self._condition.notify_all()

    def request_cancel(self) -> None:
        with self._condition:
            self._cancel_requested = True
            self._condition.notify_all()

    def run(self) -> None:
        try:
            self._validate_plan()
            self._total_bytes = sum(_source_file_bytes(item.source) for item in self._plan.copies)
            self.progress_changed.emit(0, self._total_bytes)
            for index, item in enumerate(self._plan.copies, start=1):
                self._checkpoint()
                self.stage_changed.emit(
                    f"{index} / {len(self._plan.copies)}：{item.source.name} をコピー中"
                )
                self._copy_entry(item.source, item.output)
                self.stage_changed.emit(
                    f"{index} / {len(self._plan.copies)}：{item.source.name} を確認済み"
                )
            self.progress_changed.emit(self._total_bytes, self._total_bytes)
            self.succeeded.emit([item.output for item in self._plan.copies])
        except _CopyCancelled:
            _remove_owned_outputs(self._owned_roots)
            self.cancelled.emit()
        except Exception as exc:  # Worker failures must be presented in its owning window.
            _remove_owned_outputs(self._owned_roots)
            self.failed.emit(
                f"{type(exc).__name__}: {exc}\n\n"
                "今回のコピーで作成した出力は削除を試みました。"
                "コピー元、出力先の権限、空き容量を確認してから再実行できます。"
            )

    def _validate_plan(self) -> None:
        for item in self._plan.copies:
            if not path_entry_exists(item.source):
                raise FileNotFoundError(f"コピー元が見つかりません: {item.source}")
            if not item.destination.is_dir():
                raise NotADirectoryError(f"出力先フォルダが見つかりません: {item.destination}")
            if path_entry_exists(item.output):
                raise FileExistsError(f"プレビュー後に出力先が使用されました: {item.output}")
            if (
                not item.source.is_symlink()
                and item.source.is_dir()
                and item.destination.is_relative_to(item.source)
            ):
                raise ValueError(f"フォルダ自身または配下へはコピーできません: {item.source}")
        required: dict[Path, int] = {}
        for item in self._plan.copies:
            required[item.destination] = required.get(item.destination, 0) + _source_file_bytes(
                item.source
            )
        for destination, size in required.items():
            if shutil.disk_usage(destination).free < size:
                raise OSError(f"出力先の空き容量が不足しています: {destination}")

    def _checkpoint(self) -> None:
        with self._condition:
            if self._cancel_requested:
                raise _CopyCancelled
            while self._pause_requested and not self._cancel_requested:
                if not self._paused_reported:
                    self._paused_reported = True
                    self.paused_changed.emit(True)
                self._condition.wait()
            if self._cancel_requested:
                raise _CopyCancelled
            if self._paused_reported:
                self._paused_reported = False
                self.paused_changed.emit(False)

    def _file_boundary(self) -> None:
        with self._condition:
            if self._pause_at_boundary:
                self._pause_at_boundary = False
                self._pause_requested = True
        self._checkpoint()

    def _copy_entry(self, source: Path, output: Path) -> None:
        if source.is_symlink():
            os.symlink(os.readlink(source), output, target_is_directory=source.is_dir())
            self._owned_roots.append(output)
            self._file_boundary()
            return
        if source.is_file():
            self._copy_file(source, output, top_level=True)
            return
        if source.is_dir():
            output.mkdir()
            self._owned_roots.append(output)
            self._copy_directory_contents(source, output)
            shutil.copystat(source, output, follow_symlinks=False)
            return
        raise ValueError(f"通常ファイル・フォルダ・リンク以外はコピーできません: {source}")

    def _copy_directory_contents(self, source: Path, output: Path) -> None:
        with os.scandir(source) as entries:
            children = sorted(entries, key=lambda entry: entry.name.casefold())
        for entry in children:
            self._checkpoint()
            child_source = Path(entry.path)
            child_output = output / entry.name
            self.stage_changed.emit(f"コピー中：{child_source}")
            if entry.is_symlink():
                os.symlink(
                    os.readlink(child_source),
                    child_output,
                    target_is_directory=entry.is_dir(follow_symlinks=True),
                )
                self._file_boundary()
            elif entry.is_dir(follow_symlinks=False):
                child_output.mkdir()
                self._copy_directory_contents(child_source, child_output)
                shutil.copystat(child_source, child_output, follow_symlinks=False)
            elif entry.is_file(follow_symlinks=False):
                self._copy_file(child_source, child_output)
            else:
                raise ValueError(f"未対応のファイル種別です: {child_source}")

    def _copy_file(self, source: Path, output: Path, *, top_level: bool = False) -> None:
        expected_size = source.stat().st_size
        with source.open("rb") as reader, output.open("xb") as writer:
            if top_level:
                self._owned_roots.append(output)
            while True:
                self._checkpoint()
                chunk = reader.read(self._CHUNK_SIZE)
                if not chunk:
                    break
                writer.write(chunk)
                self._copied_bytes += len(chunk)
                self.progress_changed.emit(self._copied_bytes, self._total_bytes)
        shutil.copystat(source, output, follow_symlinks=False)
        if source.stat().st_size != expected_size or output.stat().st_size != expected_size:
            raise OSError(f"コピー中にファイルサイズが変化しました: {source}")
        self._file_boundary()


def _source_file_bytes(source: Path) -> int:
    if source.is_symlink():
        return 0
    if source.is_file():
        return source.stat().st_size
    if not source.is_dir():
        return 0
    total = 0
    pending = [source]
    while pending:
        directory = pending.pop()
        with os.scandir(directory) as entries:
            for entry in entries:
                if entry.is_symlink():
                    continue
                if entry.is_dir(follow_symlinks=False):
                    pending.append(Path(entry.path))
                elif entry.is_file(follow_symlinks=False):
                    total += entry.stat(follow_symlinks=False).st_size
    return total


def _remove_owned_outputs(created_roots: list[Path]) -> None:
    """Remove only top-level paths owned by this failed/cancelled invocation."""
    for output in reversed(list(dict.fromkeys(created_roots))):
        try:
            if output.is_symlink() or output.is_file():
                output.unlink(missing_ok=True)
            elif output.is_dir():
                shutil.rmtree(output, ignore_errors=True)
        except OSError:
            continue


class QuickCopyDialog(QDialog):
    """A frozen, parentless copy workflow with its own destination and state."""

    def __init__(self, sources: tuple[Path, ...], destination_text: str = "") -> None:
        super().__init__(None)
        self.setWindowTitle(f"実体コピー（{len(sources)}件）")
        self.setMinimumSize(720, 620)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self._sources = sources
        self._plan: CopyPlan | None = None
        self._worker: CooperativeCopyThread | None = None
        self._running = False
        self._paused = False

        layout = QVBoxLayout(self)
        explanation = QLabel(
            "この画面の対象と出力先は、開いた時点で元画面から切り離されています。"
            "ここで変更しても元のファイルマネージャー画面へは反映しません。"
        )
        explanation.setWordWrap(True)
        layout.addWidget(explanation)

        layout.addWidget(QLabel(f"コピー対象（{len(sources)}件）"))
        self.sources_text = QTextEdit()
        self.sources_text.setReadOnly(True)
        self.sources_text.setMaximumHeight(120)
        self.sources_text.setPlainText("\n".join(str(path) for path in sources))
        layout.addWidget(self.sources_text)

        layout.addWidget(QLabel("出力先フォルダ"))
        self.destination_input = PathLineInput(drop_as="directory")
        self.destination_input.setPlaceholderText("出力先フォルダを入力またはドロップ")
        self.destination_input.setText(destination_text)
        self.destination_input.textChanged.connect(self.refresh_preview)
        layout.addWidget(self.destination_input)

        layout.addWidget(QLabel("簡易プレビュー"))
        self.preview_text = QTextEdit()
        self.preview_text.setReadOnly(True)
        layout.addWidget(self.preview_text, 1)

        self.stage_label = QLabel("待機中")
        self.stage_label.setWordWrap(True)
        layout.addWidget(self.stage_label)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setValue(0)
        self.progress.setFormat("進捗 %p%")
        layout.addWidget(self.progress)

        run_controls = QHBoxLayout()
        self.start_button = QPushButton("確定してコピー開始")
        self.start_button.clicked.connect(self.start_copy)
        run_controls.addWidget(self.start_button)
        self.pause_button = QPushButton("一時停止")
        self.pause_button.setEnabled(False)
        self.pause_button.clicked.connect(self.toggle_pause)
        run_controls.addWidget(self.pause_button)
        self.boundary_button = QPushButton("次のファイル区切りで一時停止")
        self.boundary_button.setCheckable(True)
        self.boundary_button.setEnabled(False)
        self.boundary_button.toggled.connect(self.pause_at_next_file)
        run_controls.addWidget(self.boundary_button)
        self.cancel_button = QPushButton("中止")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel_copy)
        run_controls.addWidget(self.cancel_button)
        layout.addLayout(run_controls)

        bottom = QHBoxLayout()
        bottom.addStretch(1)
        self.close_button = QPushButton("閉じる")
        self.close_button.clicked.connect(self.close)
        bottom.addWidget(self.close_button)
        layout.addLayout(bottom)
        self.refresh_preview()

    def refresh_preview(self) -> None:
        if self._running:
            return
        preview = build_copy_preview(
            "\n".join(str(path) for path in self._sources),
            self.destination_input.text(),
            mode="simple",
        )
        self._plan = preview.plan
        self.preview_text.setPlainText(preview.text)
        self.start_button.setEnabled(preview.plan is not None)
        self.stage_label.setText("実行可能" if preview.plan is not None else "入力内容を確認してください")

    def start_copy(self) -> None:
        self.refresh_preview()
        if self._plan is None:
            return
        self._running = True
        self._paused = False
        self.destination_input.setEnabled(False)
        self.start_button.setEnabled(False)
        self.pause_button.setEnabled(True)
        self.boundary_button.setEnabled(True)
        self.cancel_button.setEnabled(True)
        self.close_button.setEnabled(False)
        self.stage_label.setText("コピーを開始します…")
        self.progress.setValue(0)
        worker = CooperativeCopyThread(self._plan)
        self._worker = worker
        worker.progress_changed.connect(self._show_progress)
        worker.stage_changed.connect(self.stage_label.setText)
        worker.paused_changed.connect(self._show_paused)
        worker.succeeded.connect(self._copy_succeeded)
        worker.failed.connect(self._copy_failed)
        worker.cancelled.connect(self._copy_cancelled)
        worker.finished.connect(self._worker_finished)
        worker.start()

    def toggle_pause(self) -> None:
        worker = self._worker
        if worker is None:
            return
        if self._paused:
            worker.request_resume()
            self.stage_label.setText("再開しています…")
        else:
            worker.request_pause()
            self.stage_label.setText("一時停止を要求しました…")

    def pause_at_next_file(self, enabled: bool) -> None:
        if self._worker is not None:
            self._worker.request_pause_at_next_file(enabled)
        if enabled:
            self.stage_label.setText("次のファイル完了時に一時停止します")

    def cancel_copy(self) -> None:
        if self._worker is None:
            return
        self._worker.request_cancel()
        self.stage_label.setText("中止して今回の出力を片付けています…")
        self.cancel_button.setEnabled(False)

    def _show_progress(self, copied: int, total: int) -> None:
        value = 0 if total <= 0 else int(1000 * copied / total)
        self.progress.setValue(max(0, min(1000, value)))
        self.progress.setFormat(f"進捗 %p%（{_format_bytes(copied)} / {_format_bytes(total)}）")

    def _show_paused(self, paused: bool) -> None:
        self._paused = paused
        self.pause_button.setText("再開" if paused else "一時停止")
        if paused:
            self.boundary_button.setChecked(False)
            self.stage_label.setText("一時停止中")

    def _copy_succeeded(self, outputs: object) -> None:
        paths = [Path(path) for path in outputs] if isinstance(outputs, list) else []
        self.progress.setValue(1000)
        self.stage_label.setText(f"コピー完了：{len(paths)}件")
        self.preview_text.append("\n\nコピー結果:\n" + "\n".join(str(path) for path in paths))
        self._finish_run()

    def _copy_failed(self, message: str) -> None:
        self.stage_label.setText("コピーエラー。内容を修正して再実行できます。")
        self.preview_text.append("\n\nエラー:\n" + message)
        self._finish_run()

    def _copy_cancelled(self) -> None:
        self.stage_label.setText("コピーを中止しました。今回の出力は削除を試みました。")
        self._finish_run()

    def _finish_run(self) -> None:
        self._running = False
        self.destination_input.setEnabled(True)
        self.pause_button.setEnabled(False)
        self.pause_button.setText("一時停止")
        self.boundary_button.setChecked(False)
        self.boundary_button.setEnabled(False)
        self.cancel_button.setEnabled(False)
        self.close_button.setEnabled(True)
        self.start_button.setEnabled(self._plan is not None)

    def _worker_finished(self) -> None:
        if self._worker is not None:
            self._worker.deleteLater()
        self._worker = None

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        if self._running and self._worker is not None:
            self.cancel_copy()
            event.ignore()
            return
        super().closeEvent(event)


def _format_bytes(value: int) -> str:
    size = float(max(value, 0))
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{int(value)} B"
