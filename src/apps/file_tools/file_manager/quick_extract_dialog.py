"""Independent extraction window backed by a replaceable archive engine."""

from __future__ import annotations

from runtime.runtime_activity import runtime_activity

import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
from threading import Condition, Event, Lock

from PySide6.QtCore import QThread, QTimer, Qt, Signal
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QProgressBar,
    QPushButton,
    QTextEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from foundation.path import path_entry_exists
from gui import PathLineInput
from gui.layout_policy import set_text_rows

from .archive_backends import (
    ArchiveCommandCancelled,
    ArchiveMember,
    select_archive_backend,
)
from .extract_workflow import (
    ArchiveInspection,
    ExtractPlan,
    build_extract_preview,
    probe_archive_access,
    retain_selected_members,
    revalidate_extract_plan,
    resolve_selected_archive_members,
    validate_archive_members,
    validate_extracted_tree,
)


class ArchiveInspectionThread(QThread):
    """Probe password access for each archive without blocking the dialog."""

    progress_changed = Signal(int, int)
    stage_changed = Signal(str)
    completed = Signal(object)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, plan: ExtractPlan, password: str | None) -> None:
        super().__init__()
        self._plan = plan
        self.password = password
        self._cancel_requested = Event()

    def request_cancel(self) -> None:
        self._cancel_requested.set()

    @runtime_activity('圧縮ファイルを確認・解凍中')
    def run(self) -> None:
        try:
            backend = select_archive_backend(self._plan.backend_id)
            total = len(self._plan.extractions) * 100
            results: list[ArchiveInspection] = []
            self.progress_changed.emit(0, total)
            for index, item in enumerate(self._plan.extractions):
                if self._cancel_requested.is_set():
                    raise ArchiveCommandCancelled("パスワード必要性の判定を中止しました。")
                self.stage_changed.emit(
                    f"鍵判定 {index + 1} / {len(self._plan.extractions)}：{item.archive.name}"
                )
                result = probe_archive_access(
                    backend,
                    item.archive,
                    password=self.password,
                    cancelled=self._cancel_requested.is_set,
                    process_changed=lambda _process: None,
                )
                results.append(result)
                self.progress_changed.emit((index + 1) * 100, total)
            self.completed.emit(results)
        except ArchiveCommandCancelled:
            self.cancelled.emit()
        except Exception as exc:
            self.failed.emit(f"{type(exc).__name__}: {exc}")


class CooperativeExtractThread(QThread):
    """Run one frozen extraction plan and control its external process group."""

    progress_changed = Signal(int, int)
    stage_changed = Signal(str)
    paused_changed = Signal(bool)
    succeeded = Signal(object)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, plan: ExtractPlan, password: str | None) -> None:
        super().__init__()
        self._plan = plan
        self._password = password
        self._condition = Condition()
        self._process_lock = Lock()
        self._process: subprocess.Popen[bytes] | None = None
        self._pause_requested = False
        self._cancel_requested = False
        self._paused_reported = False
        self._owned_outputs: list[Path] = []
        self._staging: Path | None = None

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

    @runtime_activity('圧縮ファイルを確認・解凍中')
    def run(self) -> None:
        try:
            revalidate_extract_plan(self._plan)
            backend = select_archive_backend(self._plan.backend_id)
            total = len(self._plan.extractions) * 100
            self.progress_changed.emit(0, total)
            for index, item in enumerate(self._plan.extractions):
                self._checkpoint()
                original = item.archive.stat()
                self.stage_changed.emit(
                    f"{index + 1} / {len(self._plan.extractions)}：内容を安全確認中 {item.archive.name}"
                )
                members = backend.list_members(
                    item.archive,
                    password=self._password,
                    cancelled=self._is_cancelled,
                    process_changed=self._process_changed,
                )
                validate_archive_members(members)
                concrete_members: tuple[str, ...] | None = None
                if item.members is not None:
                    concrete_members = resolve_selected_archive_members(
                        members, item.members
                    )
                current = item.archive.stat()
                if (current.st_size, current.st_mtime_ns) != (
                    original.st_size,
                    original.st_mtime_ns,
                ):
                    raise OSError(f"確認中に圧縮ファイルが変化しました: {item.archive}")
                self._checkpoint()
                self._staging = Path(
                    tempfile.mkdtemp(prefix=".porta-extract-", dir=item.destination)
                )
                self.stage_changed.emit(
                    f"{index + 1} / {len(self._plan.extractions)}："
                    + (
                        f"選択した{len(concrete_members)}項目だけ解凍・破損確認中 "
                        if concrete_members is not None
                        else "全内容を解凍・破損確認中 "
                    )
                    + item.archive.name
                )
                extract_arguments = dict(
                    password=self._password,
                    progress=lambda percent, offset=index: self.progress_changed.emit(
                        offset * 100 + percent, total
                    ),
                    cancelled=self._is_cancelled,
                    process_changed=self._process_changed,
                )
                if concrete_members is None:
                    backend.extract(item.archive, self._staging, **extract_arguments)
                else:
                    backend.extract_members(
                        item.archive,
                        self._staging,
                        concrete_members,
                        **extract_arguments,
                    )
                validate_extracted_tree(self._staging)
                if item.members is not None:
                    retain_selected_members(self._staging, item.members)
                    validate_extracted_tree(self._staging)
                if path_entry_exists(item.output):
                    raise FileExistsError(
                        f"処理中に出力先が使用されたため確定しません: {item.output}"
                    )
                self._staging.rename(item.output)
                self._staging = None
                self._owned_outputs.append(item.output)
                self.progress_changed.emit((index + 1) * 100, total)
                self.stage_changed.emit(
                    f"{index + 1} / {len(self._plan.extractions)}：安全確認済み {item.archive.name}"
                )
            self.succeeded.emit(list(self._owned_outputs))
        except ArchiveCommandCancelled:
            self._cleanup()
            self.cancelled.emit()
        except Exception as exc:  # Worker failures must be shown in the dialog.
            self._cleanup()
            self.failed.emit(
                f"{type(exc).__name__}: {exc}\n\n"
                "今回の解凍で作成した出力は削除を試みました。"
                "パスワード、圧縮ファイル、解凍先の権限と空き容量を確認して再実行できます。"
            )

    def _checkpoint(self) -> None:
        with self._condition:
            if self._cancel_requested:
                raise ArchiveCommandCancelled("解凍を中止しました。")
            while self._pause_requested and not self._cancel_requested:
                self._report_paused(True)
                self._condition.wait()
            if self._cancel_requested:
                raise ArchiveCommandCancelled("解凍を中止しました。")
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

    def _cleanup(self) -> None:
        if self._staging is not None:
            shutil.rmtree(self._staging, ignore_errors=True)
            self._staging = None
        for output in reversed(self._owned_outputs):
            shutil.rmtree(output, ignore_errors=True)
        self._owned_outputs.clear()


class ArchiveContentsDialog(QDialog):
    """Choose archive members, then return one execution request to the parent."""

    def __init__(
        self,
        archive: Path,
        members: tuple[ArchiveMember, ...],
        selected: tuple[str, ...] | None,
        destination_description: str,
        parent: QDialog,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"個別ファイル展開：{archive.name}")
        self.request_extract = False
        layout = QVBoxLayout(self)
        explanation = QLabel(
            "解凍する中身にチェックを入れます。"
            "この画面は実行せず、選択結果を元の解凍画面へ返します。"
        )
        explanation.setWordWrap(True)
        layout.addWidget(explanation)
        archive_label = QLabel(f"圧縮ファイル: {archive}")
        archive_label.setWordWrap(True)
        layout.addWidget(archive_label)
        destination_label = QLabel(f"引き継ぐ解凍先: {destination_description}")
        destination_label.setWordWrap(True)
        layout.addWidget(destination_label)

        actions = QHBoxLayout()
        check_all = QPushButton("全件チェック")
        check_all.clicked.connect(lambda: self._set_all_checked(True))
        actions.addWidget(check_all)
        clear_all = QPushButton("全チェック解除")
        clear_all.clicked.connect(lambda: self._set_all_checked(False))
        actions.addWidget(clear_all)
        actions.addStretch(1)
        layout.addLayout(actions)

        self.members_tree = QTreeWidget()
        self.members_tree.setHeaderLabels(
            ("解凍", "種別", "展開後サイズ", "アーカイブ内パス")
        )
        members_header = self.members_tree.header()
        for column in (0, 1, 2):
            members_header.setSectionResizeMode(column, members_header.ResizeMode.ResizeToContents)
        members_header.setSectionResizeMode(3, members_header.ResizeMode.Stretch)
        selected_values = set(selected) if selected is not None else None
        for member in members:
            path = str(getattr(member, "path", ""))
            if not path:
                continue
            kind = "フォルダ" if bool(getattr(member, "is_directory", False)) else "ファイル"
            if getattr(member, "link_target", None) is not None:
                kind = "リンク"
            size = _format_member_size(
                getattr(member, "size", None),
                bool(getattr(member, "is_directory", False)),
            )
            item = QTreeWidgetItem(("", kind, size, path))
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                0,
                Qt.CheckState.Checked
                if selected_values is None or path in selected_values
                else Qt.CheckState.Unchecked,
            )
            item.setToolTip(2, _member_size_tooltip(getattr(member, "size", None)))
            item.setToolTip(3, path)
            self.members_tree.addTopLevelItem(item)
        layout.addWidget(self.members_tree, 1)

        buttons = QHBoxLayout()
        cancel = QPushButton("キャンセル")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        buttons.addStretch(1)
        save = QPushButton("選択だけ保存して元画面へ戻る")
        save.clicked.connect(self.accept)
        buttons.addWidget(save)
        execute = QPushButton("選択内容で解凍を依頼")
        execute.clicked.connect(self._accept_and_request_extract)
        buttons.addWidget(execute)
        layout.addLayout(buttons)

    def _set_all_checked(self, checked: bool) -> None:
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        for row in range(self.members_tree.topLevelItemCount()):
            self.members_tree.topLevelItem(row).setCheckState(0, state)

    def selected_members(self) -> tuple[str, ...]:
        return tuple(
            self.members_tree.topLevelItem(row).text(3)
            for row in range(self.members_tree.topLevelItemCount())
            if self.members_tree.topLevelItem(row).checkState(0) == Qt.CheckState.Checked
        )

    def _accept_and_request_extract(self) -> None:
        if not self.selected_members():
            self.setWindowTitle("個別ファイル展開：1件以上選んでください")
            return
        self.request_extract = True
        self.accept()


def _format_member_size(size: int | None, is_directory: bool) -> str:
    if is_directory:
        return "—"
    if size is None:
        return "不明"
    return f"{size:,} バイト"


def _member_size_tooltip(size: int | None) -> str:
    if size is None:
        return "展開後サイズを取得できませんでした。"
    return f"展開後サイズ: {size:,} バイト"


class QuickExtractDialog(QDialog):
    """A parentless extraction workflow whose inputs are frozen on opening."""

    def __init__(self, archives: tuple[Path, ...], destination_text: str = "") -> None:
        super().__init__(None)
        self.setWindowTitle(f"圧縮ファイルを解凍（{len(archives)}件）")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self._archives = archives
        self._plan: ExtractPlan | None = None
        self._worker: CooperativeExtractThread | None = None
        self._inspection_worker: ArchiveInspectionThread | None = None
        self._inspections: tuple[ArchiveInspection, ...] = ()
        self._inspection_ready = False
        self._selected_members: dict[Path, tuple[str, ...]] = {}
        self._pending_inspection = False
        self._close_after_inspection = False
        self._running = False
        self._paused = False

        layout = QVBoxLayout(self)
        explanation = QLabel(
            "この画面は元のファイルマネージャーから独立しています。"
            "対象と解凍先は開いた時点の値で、ここでの変更は元画面へ反映しません。"
        )
        explanation.setWordWrap(True)
        layout.addWidget(explanation)

        archives_header = QHBoxLayout()
        archives_header.addWidget(QLabel(f"圧縮ファイル（{len(archives)}件）"))
        archives_header.addStretch(1)
        self.contents_button = QPushButton("選択中の中身を選ぶ…")
        self.contents_button.setEnabled(False)
        self.contents_button.clicked.connect(self.open_current_archive_contents)
        archives_header.addWidget(self.contents_button)
        layout.addLayout(archives_header)
        self.archives_list = QListWidget()
        set_text_rows(self.archives_list, minimum=2, maximum=5)
        for archive in archives:
            item = QListWidgetItem(archive.name)
            item.setData(Qt.ItemDataRole.UserRole, str(archive))
            item.setToolTip(str(archive))
            self.archives_list.addItem(item)
        if self.archives_list.count():
            self.archives_list.setCurrentRow(0)
        self.archives_list.itemDoubleClicked.connect(self.open_archive_contents)
        self.archives_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.archives_list.customContextMenuRequested.connect(self._show_archive_menu)
        layout.addWidget(self.archives_list)

        self.in_place_checkbox = QCheckBox(
            "その場モード：圧縮ファイルと同じ場所に個別フォルダを作る"
        )
        self.in_place_checkbox.setChecked(True)
        self.in_place_checkbox.toggled.connect(self._destination_mode_changed)
        layout.addWidget(self.in_place_checkbox)
        layout.addWidget(QLabel("解凍先フォルダ（その場モードを外した場合）"))
        self.destination_input = PathLineInput(drop_as="directory")
        self.destination_input.setPlaceholderText("解凍先フォルダを入力またはドロップ")
        self.destination_input.setText(destination_text)
        self.destination_input.setEnabled(False)
        self.destination_input.setToolTip("その場モード中は使用しません。チェックを外すと入力できます。")
        self.destination_input.textChanged.connect(self.refresh_preview)
        layout.addWidget(self.destination_input)

        self.password_box = QWidget()
        password_row = QHBoxLayout()
        password_row.setContentsMargins(0, 0, 0, 0)
        self.password_box.setLayout(password_row)
        password_row.addWidget(QLabel("全件共通パスワード（必要な場合のみ）"))
        self.password_input = QLineEdit()
        self.password_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_input.setPlaceholderText("選択した全圧縮ファイルへ同じ値を適用")
        self.password_input.textChanged.connect(self._password_changed)
        self.password_input.editingFinished.connect(self.start_inspection)
        password_row.addWidget(self.password_input, 1)
        self.show_password = QCheckBox("表示")
        self.show_password.toggled.connect(self._toggle_password_visibility)
        password_row.addWidget(self.show_password)
        layout.addWidget(self.password_box)

        self.password_policy_label = QLabel(
            "パスワードは1種類だけです。入力した同じパスワードを、"
            "選択したすべての圧縮ファイルへ適用します。"
            "圧縮ファイルごとに別のパスワードを指定するモードはありません。"
        )
        self.password_policy_label.setWordWrap(True)
        layout.addWidget(self.password_policy_label)
        self.password_box.hide()
        self.password_policy_label.hide()

        inspection_header = QHBoxLayout()
        inspection_header.addWidget(QLabel("パスワード必要性の事前判定（フルパス表示）"))
        inspection_header.addStretch(1)
        self.inspect_button = QPushButton("鍵の要否を再確認")
        self.inspect_button.clicked.connect(self.start_inspection)
        inspection_header.addWidget(self.inspect_button)
        layout.addLayout(inspection_header)
        self.inspection_text = QTextEdit()
        self.inspection_text.setReadOnly(True)
        set_text_rows(self.inspection_text, minimum=7)
        self.inspection_text.setPlainText("同梱7-Zipでパスワードが必要かを確認します…")
        layout.addWidget(self.inspection_text, 1)

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

        controls = QHBoxLayout()
        self.start_button = QPushButton("確定して解凍開始")
        self.start_button.clicked.connect(self.start_extract)
        controls.addWidget(self.start_button)
        self.pause_button = QPushButton("一時停止")
        self.pause_button.setEnabled(False)
        self.pause_button.clicked.connect(self.toggle_pause)
        controls.addWidget(self.pause_button)
        self.cancel_button = QPushButton("中止")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel_extract)
        controls.addWidget(self.cancel_button)
        layout.addLayout(controls)

        bottom = QHBoxLayout()
        bottom.addStretch(1)
        self.close_button = QPushButton("閉じる")
        self.close_button.clicked.connect(self.close)
        bottom.addWidget(self.close_button)
        layout.addLayout(bottom)
        self.refresh_preview()
        QTimer.singleShot(0, self.start_inspection)

    def refresh_preview(self) -> None:
        if self._running:
            return
        preview = build_extract_preview(
            self._archives,
            self.destination_input.text(),
            in_place=self.in_place_checkbox.isChecked(),
            selected_members=self._selected_members,
        )
        self._plan = preview.plan
        self.preview_text.setPlainText(preview.text)
        self.start_button.setEnabled(preview.plan is not None and self._inspection_ready)
        if preview.plan is None:
            self.stage_label.setText("入力内容を確認してください")
        elif self._inspection_worker is None and not self._inspection_ready:
            self.stage_label.setText("パスワード必要性の事前判定が必要です")

    def _destination_mode_changed(self, in_place: bool) -> None:
        self.destination_input.setEnabled(not in_place and not self._running)
        self.destination_input.setToolTip(
            "その場モード中は使用しません。チェックを外すと入力できます。"
            if in_place
            else "全圧縮ファイルの解凍先となるフォルダです。"
        )
        self.refresh_preview()

    def _show_archive_menu(self, position) -> None:  # type: ignore[no-untyped-def]
        item = self.archives_list.itemAt(position)
        if item is None:
            return
        self.archives_list.setCurrentItem(item)
        menu = QMenu(self.archives_list)
        action = menu.addAction("この圧縮ファイルの中身を選んで解凍…")
        action.setEnabled(self._inspection_ready)
        action.triggered.connect(lambda _checked=False, row=item: self.open_archive_contents(row))
        if not self._inspection_ready:
            action.setToolTip("パスワードの確認完了後に使用できます。")
        menu.exec(self.archives_list.mapToGlobal(position))

    def open_current_archive_contents(self) -> None:
        item = self.archives_list.currentItem()
        if item is not None:
            self.open_archive_contents(item)

    def open_archive_contents(self, item: QListWidgetItem) -> None:
        if not self._inspection_ready:
            self.stage_label.setText(
                "まずパスワード必要性の判定を完了し、必要な場合は正しい値を入力してください。"
            )
            return
        archive = Path(str(item.data(Qt.ItemDataRole.UserRole)))
        inspection = next(
            (result for result in self._inspections if result.archive == archive), None
        )
        if inspection is None or not inspection.members:
            self.stage_label.setText(f"中身一覧を表示できません: {archive.name}")
            return
        destination = (
            f"{archive.parent} 内の「{archive.stem}」フォルダ"
            if self.in_place_checkbox.isChecked()
            else self.destination_input.text() or "未入力"
        )
        dialog = ArchiveContentsDialog(
            archive,
            inspection.members,
            self._selected_members.get(archive),
            destination,
            self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            self.stage_label.setText(f"個別選択をキャンセルしました: {archive.name}")
            return
        selected = dialog.selected_members()
        if not selected:
            self.stage_label.setText(
                f"中身が1件も選ばれていないため、変更していません: {archive.name}"
            )
            return
        self._selected_members[archive] = selected
        self.refresh_preview()
        self.stage_label.setText(
            f"個別選択を受け取りました: {archive.name} / {len(selected)}項目。"
            "解凍処理はこの元画面が行います。"
        )
        if dialog.request_extract:
            self.stage_label.setText(
                f"個別選択 {len(selected)}項目を受信。元画面で解凍開始を準備します…"
            )
            QTimer.singleShot(0, self.start_extract)

    def start_inspection(self) -> None:
        if self._running:
            return
        if self._inspection_worker is not None:
            self._pending_inspection = True
            self._inspection_worker.request_cancel()
            self.stage_label.setText("現在の鍵判定を更新しています…")
            return
        self.refresh_preview()
        if self._plan is None:
            self.inspection_text.setPlainText("解凍先と圧縮ファイルを確認してから鍵の要否を判定します。")
            return
        self._inspection_ready = False
        self._inspections = ()
        self.start_button.setEnabled(False)
        self.inspect_button.setEnabled(False)
        self.progress.setValue(0)
        self.inspection_text.setPlainText(
            f"判定ツール: {self._plan.backend_name}\n"
            "選択した圧縮ファイルのパスワード必要性だけを先に確認しています…"
        )
        self.stage_label.setText("パスワードが必要かを判定中…")
        password = self.password_input.text() or None
        worker = ArchiveInspectionThread(self._plan, password)
        self._inspection_worker = worker
        worker.progress_changed.connect(self._show_progress)
        worker.stage_changed.connect(self.stage_label.setText)
        worker.completed.connect(self._inspection_completed)
        worker.failed.connect(self._inspection_failed)
        worker.cancelled.connect(self._inspection_cancelled)
        worker.finished.connect(self._inspection_worker_finished)
        worker.start()

    def _password_changed(self) -> None:
        if self._running:
            return
        self._inspection_ready = False
        self._selected_members.clear()
        self.start_button.setEnabled(False)
        self.contents_button.setEnabled(False)
        if self._inspection_worker is None:
            self.stage_label.setText("共通パスワードが変わりました。状態を再確認してください。")

    def _inspection_completed(self, values: object) -> None:
        worker = self._inspection_worker
        results = tuple(values) if isinstance(values, list) else ()
        if worker is None or (worker.password or "") != self.password_input.text():
            self._pending_inspection = True
            return
        self._inspections = tuple(
            value for value in results if isinstance(value, ArchiveInspection)
        )
        self._inspection_ready = bool(self._inspections) and all(
            result.can_extract for result in self._inspections
        )
        password_needed = any(
            result.state in {"password_required", "password_verified", "password_rejected"}
            for result in self._inspections
        )
        self.password_box.setVisible(password_needed)
        self.password_policy_label.setVisible(password_needed)
        self.contents_button.setEnabled(self._inspection_ready and not self._running)
        self.inspection_text.setPlainText(self._inspection_report())
        self.progress.setValue(1000)
        if self._inspection_ready:
            backend_name = self._plan.backend_name if self._plan is not None else "解凍ツール"
            self.stage_label.setText(
                f"{backend_name}でパスワード確認完了。"
                "破損検査は解凍開始後、出力を確定する前に行います。"
            )
        elif any(result.state == "password_required" for result in self._inspections):
            self.stage_label.setText("パスワード付きです。全件共通パスワードを入力して再確認してください。")
        else:
            self.stage_label.setText("解凍できない圧縮ファイルがあります。判定結果を確認してください。")
        self.start_button.setEnabled(self._plan is not None and self._inspection_ready)

    def _inspection_report(self) -> str:
        backend_name = self._plan.backend_name if self._plan is not None else "未選択"
        lines = [
            f"判定に使用したツール: {backend_name}",
            "先行判定: パスワードが必要かどうかのみ",
            "パスワード方式: 入力した1つのパスワードを選択した全ファイルへ適用",
            "",
        ]
        for result in self._inspections:
            marker = "OK" if result.can_extract else "要確認"
            lines.extend(
                [
                    f"[{marker}] {result.display_status}",
                    f"パス: {result.archive}",
                    f"詳細: {result.detail}",
                    "",
                ]
            )
        if self._inspection_ready:
            lines.append(
                "全件のパスワード判定に成功しました。"
                "中身を個別選択するか、そのまま全件解凍できます。"
            )
        else:
            lines.append("すべてが解凍可能になるまで解凍開始ボタンは有効になりません。")
        return "\n".join(lines).rstrip()

    def _inspection_failed(self, message: str) -> None:
        self._inspection_ready = False
        self.contents_button.setEnabled(False)
        self.inspection_text.setPlainText("鍵判定エラー:\n" + message)
        self.stage_label.setText("パスワード必要性の判定に失敗しました。")

    def _inspection_cancelled(self) -> None:
        if not self._pending_inspection and not self._close_after_inspection:
            self.stage_label.setText("パスワード必要性の判定を中止しました。")

    def _inspection_worker_finished(self) -> None:
        if self._inspection_worker is not None:
            self._inspection_worker.deleteLater()
        self._inspection_worker = None
        self.inspect_button.setEnabled(not self._running)
        if self._close_after_inspection:
            self._close_after_inspection = False
            QTimer.singleShot(0, self.close)
            return
        if self._pending_inspection:
            self._pending_inspection = False
            QTimer.singleShot(0, self.start_inspection)

    def start_extract(self) -> None:
        self.refresh_preview()
        if self._plan is None or not self._inspection_ready:
            if self._plan is not None and self._inspection_worker is None:
                self.start_inspection()
            return
        self._running = True
        self._paused = False
        self.in_place_checkbox.setEnabled(False)
        self.destination_input.setEnabled(False)
        self.password_input.setEnabled(False)
        self.show_password.setEnabled(False)
        self.inspect_button.setEnabled(False)
        self.contents_button.setEnabled(False)
        self.start_button.setEnabled(False)
        self.pause_button.setEnabled(True)
        self.cancel_button.setEnabled(True)
        self.close_button.setEnabled(False)
        self.progress.setValue(0)
        self.stage_label.setText("圧縮ファイルを確認します…")
        password = self.password_input.text() or None
        worker = CooperativeExtractThread(self._plan, password)
        self._worker = worker
        worker.progress_changed.connect(self._show_progress)
        worker.stage_changed.connect(self.stage_label.setText)
        worker.paused_changed.connect(self._show_paused)
        worker.succeeded.connect(self._extract_succeeded)
        worker.failed.connect(self._extract_failed)
        worker.cancelled.connect(self._extract_cancelled)
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

    def cancel_extract(self) -> None:
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

    def _extract_succeeded(self, outputs: object) -> None:
        paths = [Path(path) for path in outputs] if isinstance(outputs, list) else []
        self.progress.setValue(1000)
        self.stage_label.setText(f"解凍完了：{len(paths)}件")
        self.preview_text.append("\n\n解凍結果:\n" + "\n".join(str(path) for path in paths))
        self._finish_run()

    def _extract_failed(self, message: str) -> None:
        self.stage_label.setText("解凍エラー。内容を修正して再実行できます。")
        self.preview_text.append("\n\nエラー:\n" + message)
        self._finish_run()

    def _extract_cancelled(self) -> None:
        self.stage_label.setText("解凍を中止しました。今回の出力は削除を試みました。")
        self._finish_run()

    def _finish_run(self) -> None:
        self._running = False
        self.in_place_checkbox.setEnabled(True)
        self.destination_input.setEnabled(not self.in_place_checkbox.isChecked())
        self.password_input.setEnabled(True)
        self.show_password.setEnabled(True)
        self.inspect_button.setEnabled(True)
        self.contents_button.setEnabled(self._inspection_ready)
        self.pause_button.setEnabled(False)
        self.pause_button.setText("一時停止")
        self.cancel_button.setEnabled(False)
        self.close_button.setEnabled(True)
        self.start_button.setEnabled(self._plan is not None and self._inspection_ready)

    def _worker_finished(self) -> None:
        if self._worker is not None:
            self._worker.deleteLater()
        self._worker = None

    def _toggle_password_visibility(self, visible: bool) -> None:
        mode = QLineEdit.EchoMode.Normal if visible else QLineEdit.EchoMode.Password
        self.password_input.setEchoMode(mode)

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        if self._running and self._worker is not None:
            self.cancel_extract()
            event.ignore()
            return
        if self._inspection_worker is not None:
            self._pending_inspection = False
            self._close_after_inspection = True
            self._inspection_worker.request_cancel()
            event.ignore()
            return
        super().closeEvent(event)
