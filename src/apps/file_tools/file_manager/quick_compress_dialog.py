"""Independent ZIP/7z creation window using the bundled archive backend."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
from threading import Condition, Lock

from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
)

from foundation.path import path_entry_exists
from gui import NoWheelComboBox, PathLineInput

from .archive_backends import ArchiveCommandCancelled, select_archive_backend
from .compress_workflow import (
    CompressionPlan,
    build_compression_preview,
    revalidate_compression_plan,
    source_file_bytes,
)
from .extract_workflow import validate_archive_members


class CooperativeCompressThread(QThread):
    """Create and verify archives while controlling the external process group."""

    progress_changed = Signal(int, int)
    stage_changed = Signal(str)
    paused_changed = Signal(bool)
    succeeded = Signal(object)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, plan: CompressionPlan, password: str | None) -> None:
        super().__init__()
        self._plan = plan
        self._password = password if plan.password_enabled else None
        self._condition = Condition()
        self._process_lock = Lock()
        self._process: subprocess.Popen[bytes] | None = None
        self._pause_requested = False
        self._cancel_requested = False
        self._paused_reported = False
        self._owned_outputs: list[Path] = []
        self._staging_directory: Path | None = None

    def request_pause(self) -> None:
        with self._condition:
            self._pause_requested = True
        self._set_process_paused(True)
        self._report_paused(True)

    def request_resume(self) -> None:
        with self._condition:
            self._pause_requested = False
            self._condition.notify_all()
        self._set_process_paused(False)
        self._report_paused(False)

    def request_cancel(self) -> None:
        with self._condition:
            self._cancel_requested = True
            self._pause_requested = False
            self._condition.notify_all()
        self._set_process_paused(False)

    def run(self) -> None:
        try:
            revalidate_compression_plan(self._plan)
            self._validate_free_space()
            backend = select_archive_backend(self._plan.backend_id)
            total = len(self._plan.archives) * 100
            self.progress_changed.emit(0, total)
            for index, item in enumerate(self._plan.archives):
                self._checkpoint()
                before = _source_signature(item.sources)
                self._staging_directory = Path(
                    tempfile.mkdtemp(prefix=".porta-compress-", dir=item.destination)
                )
                staging_output = self._staging_directory / item.output.name
                self.stage_changed.emit(
                    f"{index + 1} / {len(self._plan.archives)}：圧縮中 {item.output.name}"
                )
                backend.create_archive(
                    item.sources,
                    staging_output,
                    archive_format=self._plan.archive_format,
                    compression_level=self._plan.compression_level,
                    password=self._password,
                    hide_names=self._plan.hide_names,
                    progress=lambda percent, offset=index: self.progress_changed.emit(
                        offset * 100 + int(percent * 0.8), total
                    ),
                    cancelled=self._is_cancelled,
                    process_changed=self._process_changed,
                )
                self._checkpoint()
                self.stage_changed.emit(
                    f"{index + 1} / {len(self._plan.archives)}：作成結果を全件検査中"
                )
                backend.test_archive(
                    staging_output,
                    password=self._password,
                    progress=lambda percent, offset=index: self.progress_changed.emit(
                        offset * 100 + 80 + int(percent * 0.2), total
                    ),
                    cancelled=self._is_cancelled,
                    process_changed=self._process_changed,
                )
                members = backend.list_members(
                    staging_output,
                    password=self._password,
                    cancelled=self._is_cancelled,
                    process_changed=self._process_changed,
                )
                validate_archive_members(members)
                _verify_top_level_names(item.sources, members)
                if _source_signature(item.sources) != before:
                    raise OSError("圧縮中に対象の名前、容量または更新日時が変化しました。")
                if path_entry_exists(item.output):
                    raise FileExistsError(
                        f"処理中に出力先が使用されたため確定しません: {item.output}"
                    )
                staging_output.rename(item.output)
                self._owned_outputs.append(item.output)
                self._remove_staging_directory()
                self.progress_changed.emit((index + 1) * 100, total)
            self.succeeded.emit(list(self._owned_outputs))
        except ArchiveCommandCancelled:
            self._cleanup()
            self.cancelled.emit()
        except Exception as exc:  # Worker errors belong in the independent window.
            self._cleanup()
            self.failed.emit(
                f"{type(exc).__name__}: {exc}\n\n"
                "今回の圧縮で作成した出力は削除を試みました。"
                "対象、出力先、共通パスワードと空き容量を確認して再実行できます。"
            )

    def _validate_free_space(self) -> None:
        required: dict[Path, int] = {}
        for item in self._plan.archives:
            estimate = source_file_bytes(item.sources) + 4 * 1024 * 1024
            required[item.destination] = required.get(item.destination, 0) + estimate
        for destination, size in required.items():
            if shutil.disk_usage(destination).free < size:
                raise OSError(f"圧縮先の空き容量が不足しています: {destination}")

    def _checkpoint(self) -> None:
        with self._condition:
            if self._cancel_requested:
                raise ArchiveCommandCancelled("圧縮を中止しました。")
            while self._pause_requested and not self._cancel_requested:
                self._report_paused(True)
                self._condition.wait()
            if self._cancel_requested:
                raise ArchiveCommandCancelled("圧縮を中止しました。")
        self._report_paused(False)

    def _is_cancelled(self) -> bool:
        with self._condition:
            return self._cancel_requested

    def _process_changed(self, process: subprocess.Popen[bytes] | None) -> None:
        with self._process_lock:
            self._process = process
        if process is not None:
            with self._condition:
                paused = self._pause_requested
            if paused:
                self._set_process_paused(True)

    def _set_process_paused(self, paused: bool) -> None:
        with self._process_lock:
            process = self._process
            if process is None or process.poll() is not None:
                return
            try:
                os.killpg(process.pid, signal.SIGSTOP if paused else signal.SIGCONT)
            except ProcessLookupError:
                return

    def _report_paused(self, paused: bool) -> None:
        if self._paused_reported == paused:
            return
        self._paused_reported = paused
        self.paused_changed.emit(paused)

    def _remove_staging_directory(self) -> None:
        if self._staging_directory is not None:
            shutil.rmtree(self._staging_directory, ignore_errors=True)
            self._staging_directory = None

    def _cleanup(self) -> None:
        self._remove_staging_directory()
        for output in reversed(self._owned_outputs):
            output.unlink(missing_ok=True)
        self._owned_outputs.clear()


class QuickCompressDialog(QDialog):
    """Parentless compression UI with simple defaults and optional advanced modes."""

    def __init__(self, sources: tuple[Path, ...]) -> None:
        super().__init__(None)
        self.setWindowTitle(f"圧縮ファイルを作成（{len(sources)}件）")
        self.setMinimumSize(760, 760)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self._sources = sources
        self._plan: CompressionPlan | None = None
        self._worker: CooperativeCompressThread | None = None
        self._running = False
        self._paused = False
        self._saved_destination = str(sources[0].parent) if sources else ""
        self._hide_names_preference = True

        layout = QVBoxLayout(self)
        explanation = QLabel(
            "既定は7z・パスワード付き・ファイル名暗号化・低めの圧縮率です。"
            "対象と設定は元のファイルマネージャーから独立しています。"
        )
        explanation.setWordWrap(True)
        layout.addWidget(explanation)

        layout.addWidget(QLabel(f"圧縮対象（{len(sources)}件）"))
        self.sources_text = QTextEdit()
        self.sources_text.setReadOnly(True)
        self.sources_text.setMaximumHeight(110)
        self.sources_text.setPlainText("\n".join(str(path) for path in sources))
        layout.addWidget(self.sources_text)

        format_row = QHBoxLayout()
        format_row.addWidget(QLabel("圧縮形式"))
        self.format_combo = NoWheelComboBox()
        self.format_combo.addItem("7z（推奨・ファイル名暗号化対応）", "7z")
        self.format_combo.addItem("ZIP（互換性優先・ファイル名は見える）", "zip")
        self.format_combo.currentIndexChanged.connect(self._format_changed)
        format_row.addWidget(self.format_combo, 1)
        layout.addLayout(format_row)

        password_row = QHBoxLayout()
        self.password_enabled = QCheckBox("パスワード付き")
        self.password_enabled.setChecked(True)
        self.password_enabled.toggled.connect(self._password_mode_changed)
        password_row.addWidget(self.password_enabled)
        self.password_input = QLineEdit()
        self.password_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_input.setPlaceholderText("選択対象に使う共通パスワード")
        self.password_input.textChanged.connect(self.refresh_preview)
        password_row.addWidget(self.password_input, 1)
        self.show_password = QCheckBox("表示")
        self.show_password.toggled.connect(self._toggle_password_visibility)
        password_row.addWidget(self.show_password)
        self.hide_names = QCheckBox("ファイル名も隠す")
        self.hide_names.setChecked(True)
        self.hide_names.toggled.connect(self._hide_names_changed)
        password_row.addWidget(self.hide_names)
        layout.addLayout(password_row)
        self.encryption_notice = QLabel(
            "7zのヘッダー暗号化を使用し、内容だけでなくファイル名とフォルダ構成も隠します。"
        )
        self.encryption_notice.setWordWrap(True)
        layout.addWidget(self.encryption_notice)

        name_row = QHBoxLayout()
        name_row.addWidget(QLabel("圧縮ファイル名"))
        self.archive_name_input = QLineEdit(_default_archive_name(sources))
        self.archive_name_input.setPlaceholderText("拡張子を除く名前")
        self.archive_name_input.textChanged.connect(self.refresh_preview)
        name_row.addWidget(self.archive_name_input, 1)
        self.extension_label = QLabel(".7z")
        name_row.addWidget(self.extension_label)
        layout.addLayout(name_row)

        layout.addWidget(QLabel("出力先フォルダ"))
        self.destination_input = PathLineInput(drop_as="directory")
        self.destination_input.setPlaceholderText("フォルダを入力、またはドラッグして上書き")
        self.destination_input.setText(self._saved_destination)
        self.destination_input.textChanged.connect(self._destination_changed)
        layout.addWidget(self.destination_input)
        self.destination_notice = QLabel(
            "画面を開いた時点で、最初の圧縮対象があるフォルダを設定しています。"
        )
        self.destination_notice.setWordWrap(True)
        layout.addWidget(self.destination_notice)

        self.advanced_group = QGroupBox("上級設定（必要な場合のみ有効化）")
        self.advanced_group.setCheckable(True)
        self.advanced_group.setChecked(False)
        advanced_layout = QVBoxLayout(self.advanced_group)
        level_row = QHBoxLayout()
        level_row.addWidget(QLabel("圧縮率"))
        self.level_combo = NoWheelComboBox()
        for label, value in (
            ("保存のみ（0）", 0),
            ("最速（1）", 1),
            ("低め・高速（3）", 3),
            ("標準（5）", 5),
            ("高め（7）", 7),
            ("最高（9）", 9),
        ):
            self.level_combo.addItem(label, value)
        self.level_combo.setCurrentIndex(2)
        self.level_combo.currentIndexChanged.connect(self.refresh_preview)
        level_row.addWidget(self.level_combo, 1)
        advanced_layout.addLayout(level_row)
        self.individual_mode = QCheckBox(
            "対象を1つずつ個別に圧縮する（フォルダはフォルダ全体で1対象）"
        )
        self.individual_mode.toggled.connect(self._individual_mode_changed)
        advanced_layout.addWidget(self.individual_mode)
        self.in_place_mode = QCheckBox("各対象と同じ場所に、その場で圧縮する")
        self.in_place_mode.setEnabled(False)
        self.in_place_mode.toggled.connect(self._in_place_mode_changed)
        advanced_layout.addWidget(self.in_place_mode)
        advanced_explanation = QLabel(
            "その場で圧縮は個別圧縮時だけ使用できます。オンにすると上の出力先は使用しません。"
        )
        advanced_explanation.setWordWrap(True)
        advanced_layout.addWidget(advanced_explanation)
        self.advanced_group.toggled.connect(self._advanced_mode_changed)
        layout.addWidget(self.advanced_group)

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
        layout.addWidget(self.progress)

        controls = QHBoxLayout()
        self.start_button = QPushButton("確定して圧縮開始")
        self.start_button.clicked.connect(self.start_compress)
        controls.addWidget(self.start_button)
        self.pause_button = QPushButton("一時停止")
        self.pause_button.setEnabled(False)
        self.pause_button.clicked.connect(self.toggle_pause)
        controls.addWidget(self.pause_button)
        self.cancel_button = QPushButton("中止")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel_compress)
        controls.addWidget(self.cancel_button)
        layout.addLayout(controls)

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
        individual = self.advanced_group.isChecked() and self.individual_mode.isChecked()
        in_place = individual and self.in_place_mode.isChecked()
        preview = build_compression_preview(
            self._sources,
            self.destination_input.text(),
            archive_format=str(self.format_combo.currentData()),
            archive_name=self.archive_name_input.text(),
            compression_level=int(self.level_combo.currentData()),
            password_enabled=self.password_enabled.isChecked(),
            password=self.password_input.text(),
            hide_names=self.hide_names.isChecked(),
            individual=individual,
            in_place=in_place,
        )
        self._plan = preview.plan
        self.preview_text.setPlainText(preview.text)
        self.start_button.setEnabled(preview.plan is not None)
        self.stage_label.setText("実行可能" if preview.plan is not None else "設定を確認してください")

    def _format_changed(self) -> None:
        archive_format = str(self.format_combo.currentData())
        self.extension_label.setText(f".{archive_format}")
        if archive_format == "zip":
            if self.hide_names.isChecked():
                self._hide_names_preference = True
            self.hide_names.setChecked(False)
            self.hide_names.setEnabled(False)
            self.encryption_notice.setText(
                "ZIPでは内容を暗号化できますが、ファイル名とフォルダ構成は隠せません。"
            )
        else:
            self.hide_names.setEnabled(self.password_enabled.isChecked())
            self.hide_names.setChecked(
                self._hide_names_preference and self.password_enabled.isChecked()
            )
            self.encryption_notice.setText(
                "7zのヘッダー暗号化を使用し、内容だけでなくファイル名とフォルダ構成も隠します。"
            )
        self.refresh_preview()

    def _password_mode_changed(self, enabled: bool) -> None:
        self.password_input.setEnabled(enabled)
        self.show_password.setEnabled(enabled)
        if not enabled:
            if self.hide_names.isChecked():
                self._hide_names_preference = True
            self.hide_names.setChecked(False)
            self.hide_names.setEnabled(False)
        elif str(self.format_combo.currentData()) == "7z":
            self.hide_names.setEnabled(True)
            self.hide_names.setChecked(self._hide_names_preference)
        self.refresh_preview()

    def _hide_names_changed(self, checked: bool) -> None:
        if checked:
            self._hide_names_preference = True
        self.refresh_preview()

    def _advanced_mode_changed(self, enabled: bool) -> None:
        if not enabled:
            self.in_place_mode.setChecked(False)
            self.individual_mode.setChecked(False)
        self.refresh_preview()

    def _individual_mode_changed(self, enabled: bool) -> None:
        self.in_place_mode.setEnabled(enabled and self.advanced_group.isChecked())
        if not enabled:
            self.in_place_mode.setChecked(False)
        self.archive_name_input.setEnabled(not enabled)
        self.refresh_preview()

    def _in_place_mode_changed(self, enabled: bool) -> None:
        if enabled:
            current = self.destination_input.text().strip()
            if current:
                self._saved_destination = current
            self.destination_input.blockSignals(True)
            self.destination_input.clear()
            self.destination_input.blockSignals(False)
            self.destination_input.setEnabled(False)
            self.destination_notice.setText(
                "その場で圧縮モードのため、この出力先は使用できません。"
                "各対象と同じフォルダへ個別に出力します。"
            )
        else:
            self.destination_input.setEnabled(True)
            if not self.destination_input.text().strip():
                self.destination_input.setText(self._saved_destination)
            self.destination_notice.setText(
                "画面を開いた時点で、最初の圧縮対象があるフォルダを設定しています。"
            )
        self.refresh_preview()

    def _destination_changed(self, value: str) -> None:
        if self.destination_input.isEnabled() and value.strip():
            self._saved_destination = value.strip()
        self.refresh_preview()

    def start_compress(self) -> None:
        self.refresh_preview()
        if self._plan is None:
            return
        self._running = True
        self._paused = False
        self._set_inputs_enabled(False)
        self.start_button.setEnabled(False)
        self.pause_button.setEnabled(True)
        self.cancel_button.setEnabled(True)
        self.close_button.setEnabled(False)
        self.progress.setValue(0)
        self.stage_label.setText("圧縮を開始します…")
        worker = CooperativeCompressThread(
            self._plan,
            self.password_input.text() if self.password_enabled.isChecked() else None,
        )
        self._worker = worker
        worker.progress_changed.connect(self._show_progress)
        worker.stage_changed.connect(self.stage_label.setText)
        worker.paused_changed.connect(self._show_paused)
        worker.succeeded.connect(self._compress_succeeded)
        worker.failed.connect(self._compress_failed)
        worker.cancelled.connect(self._compress_cancelled)
        worker.finished.connect(self._worker_finished)
        worker.start()

    def toggle_pause(self) -> None:
        if self._worker is None:
            return
        if self._paused:
            self._worker.request_resume()
            self.stage_label.setText("再開しています…")
        else:
            self._worker.request_pause()
            self.stage_label.setText("一時停止を要求しました…")

    def cancel_compress(self) -> None:
        if self._worker is None:
            return
        self._worker.request_cancel()
        self.stage_label.setText("中止して今回の出力を片付けています…")
        self.cancel_button.setEnabled(False)

    def _show_progress(self, completed: int, total: int) -> None:
        value = 0 if total <= 0 else int(1000 * completed / total)
        self.progress.setValue(max(0, min(1000, value)))

    def _show_paused(self, paused: bool) -> None:
        self._paused = paused
        self.pause_button.setText("再開" if paused else "一時停止")
        if paused:
            self.stage_label.setText("一時停止中")

    def _compress_succeeded(self, outputs: object) -> None:
        paths = [Path(path) for path in outputs] if isinstance(outputs, list) else []
        self.progress.setValue(1000)
        self.stage_label.setText(f"圧縮・検査完了：{len(paths)}件")
        self.preview_text.append("\n\n圧縮結果:\n" + "\n".join(str(path) for path in paths))
        self._finish_run()

    def _compress_failed(self, message: str) -> None:
        self.stage_label.setText("圧縮エラー。設定を修正して再実行できます。")
        self.preview_text.append("\n\nエラー:\n" + message)
        self._finish_run()

    def _compress_cancelled(self) -> None:
        self.stage_label.setText("圧縮を中止しました。今回の出力は削除を試みました。")
        self._finish_run()

    def _finish_run(self) -> None:
        self._running = False
        self._set_inputs_enabled(True)
        self.pause_button.setEnabled(False)
        self.pause_button.setText("一時停止")
        self.cancel_button.setEnabled(False)
        self.close_button.setEnabled(True)
        self.start_button.setEnabled(self._plan is not None)
        password_enabled = self.password_enabled.isChecked()
        archive_format = str(self.format_combo.currentData())
        individual = self.advanced_group.isChecked() and self.individual_mode.isChecked()
        self.password_input.setEnabled(password_enabled)
        self.show_password.setEnabled(password_enabled)
        self.hide_names.setEnabled(password_enabled and archive_format == "7z")
        self.archive_name_input.setEnabled(not individual)
        self.in_place_mode.setEnabled(individual)
        self.destination_input.setEnabled(not self.in_place_mode.isChecked())

    def _set_inputs_enabled(self, enabled: bool) -> None:
        for widget in (
            self.format_combo,
            self.level_combo,
            self.password_enabled,
            self.password_input,
            self.show_password,
            self.hide_names,
            self.archive_name_input,
            self.destination_input,
            self.advanced_group,
        ):
            widget.setEnabled(enabled)
        if enabled and self.in_place_mode.isChecked():
            self.destination_input.setEnabled(False)

    def _worker_finished(self) -> None:
        if self._worker is not None:
            self._worker.deleteLater()
        self._worker = None

    def _toggle_password_visibility(self, visible: bool) -> None:
        mode = QLineEdit.EchoMode.Normal if visible else QLineEdit.EchoMode.Password
        self.password_input.setEchoMode(mode)

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        if self._running and self._worker is not None:
            self.cancel_compress()
            event.ignore()
            return
        super().closeEvent(event)


def _default_archive_name(sources: tuple[Path, ...]) -> str:
    if not sources:
        return "archive"
    if len(sources) == 1:
        return sources[0].name
    return f"{sources[0].name}_ほか{len(sources) - 1}件"


def _source_signature(sources: tuple[Path, ...]) -> tuple[tuple[str, int, int], ...]:
    values: list[tuple[str, int, int]] = []
    for source in sources:
        paths = (source, *source.rglob("*")) if source.is_dir() else (source,)
        for path in paths:
            metadata = path.stat(follow_symlinks=False)
            values.append((str(path), metadata.st_size, metadata.st_mtime_ns))
    return tuple(values)


def _verify_top_level_names(sources, members) -> None:  # type: ignore[no-untyped-def]
    expected = {source.name for source in sources}
    actual = {
        member.path.replace("\\", "/").split("/", 1)[0]
        for member in members
        if member.path
    }
    if actual != expected:
        raise OSError(
            "作成した圧縮ファイルの最上位項目が対象と一致しません。\n"
            f"予定: {sorted(expected)}\n実際: {sorted(actual)}"
        )
