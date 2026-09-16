"""PySide6 screen for explicit YouTube inspection, download and list export."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from threading import Event
from typing import Any

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from media import (
    ChannelExportReport,
    CatalogWriteReport,
    DEFAULT_CATALOG_FILENAME,
    CreatorCollectionResult,
    CreatorOverview,
    CreatorProgress,
    DownloadReport,
    VideoRecord,
    collect_creator_videos,
    download_videos,
    export_creator_videos,
    inspect_videos,
    po_token_provider_status,
    resolve_catalog_path,
    split_urls,
    write_video_catalog,
    wpc_provider_status,
)
from runtime.runtime_activity import runtime_activity
from gui import AppHeader, AppPageLayout, JsonFieldSpec, JsonSettingsEditor
from gui.layout_policy import preferred_window_size, set_text_rows

from . import settings


class _TaskThread(QThread):
    """Run a network or file task outside the GUI thread without retaining it."""

    result_ready = Signal(object)
    failed = Signal(str)
    progress = Signal(object)

    def __init__(self, task: Callable[[], object], parent: QWidget) -> None:
        super().__init__(parent)
        self._task = task
        self._cancel_requested = Event()

    def request_cancel(self) -> None:
        self._cancel_requested.set()

    def is_cancel_requested(self) -> bool:
        return self._cancel_requested.is_set()

    def run(self) -> None:
        try:
            self.result_ready.emit(self._task())
        except Exception as exc:
            self.failed.emit(str(exc) or exc.__class__.__name__)


def _duration_text(value: int | None) -> str:
    if value is None:
        return ""
    hours, remaining = divmod(value, 3600)
    minutes, seconds = divmod(remaining, 60)
    return f"{hours}:{minutes:02}:{seconds:02}" if hours else f"{minutes}:{seconds:02}"


class YouTubeDownloaderScreen(QWidget):
    """A non-persistent working screen for two explicit YouTube tasks."""

    def describe_work_state(self):
        if self._inspected_videos or self._creator_videos:
            return {"level": 3, "reason": f"確認済み動画{len(self._inspected_videos)}件・収集結果{len(self._creator_videos)}件を保持しています。"}
        return {"level": 2, "reason": "URL・保存先・取得方法の設定段階です。"}

    def __init__(self, return_to_main: Callable[[], None]) -> None:
        super().__init__()
        self._return_to_main = return_to_main
        self._settings = settings.load_settings()
        self._inspected_videos: list[VideoRecord] = []
        self._creator_videos: list[VideoRecord] = []
        self._task: _TaskThread | None = None
        self._collecting_creator = False
        self._settings_dialog: QDialog | None = None
        self._settings_editor: JsonSettingsEditor | None = None
        self._build_ui()

    def _build_ui(self) -> None:
        layout = AppPageLayout(self)
        header = AppHeader(
            self._return_to_main,
            title="YouTube ダウンローダー",
            on_settings=self.show_settings,
            settings_tooltip="保存先だけを確認・編集します。履歴やURLは保存しません。",
        )
        header.content_layout.addStretch(1)
        layout.addWidget(header)
        layout.addWidget(
            QLabel("情報確認後にだけ実行します。利用権・サービス規約を確認できる動画だけを扱ってください。")
        )

        tabs = QTabWidget()
        tabs.addTab(self._build_download_tab(), "動画をダウンロード")
        tabs.addTab(self._build_creator_tab(), "投稿者の動画一覧を出力")
        layout.addWidget(tabs, 1)
        self._refresh_destination_labels()

        self.notice = QPlainTextEdit()
        self.notice.setReadOnly(True)
        self.notice.setMaximumBlockCount(200)
        set_text_rows(self.notice, minimum=2, maximum=5)
        self.notice.setPlaceholderText("実行内容と結果をここに表示します。選択してコピーできます。")
        layout.addWidget(self.notice)

    def _build_download_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        input_box = QGroupBox("① 動画URLを一行ずつ入力")
        input_layout = QVBoxLayout(input_box)
        self.video_urls_editor = QTextEdit()
        self.video_urls_editor.setPlaceholderText("https://www.youtube.com/watch?v=...\nhttps://youtu.be/...")
        set_text_rows(self.video_urls_editor, minimum=3)
        input_layout.addWidget(self.video_urls_editor)
        buttons = QHBoxLayout()
        self.inspect_button = QPushButton("情報を確認")
        self.inspect_button.clicked.connect(self.inspect_download_urls)
        buttons.addWidget(self.inspect_button)
        clear_button = QPushButton("入力を空にする")
        clear_button.clicked.connect(self._clear_download_input)
        buttons.addWidget(clear_button)
        buttons.addStretch(1)
        input_layout.addLayout(buttons)
        layout.addWidget(input_box)

        preview_box = QGroupBox("② 確認結果")
        preview_layout = QVBoxLayout(preview_box)
        self.download_table = self._create_table()
        preview_layout.addWidget(self.download_table)
        layout.addWidget(preview_box, 1)

        execute_row = QHBoxLayout()
        execute_row.addWidget(QLabel("動画の保存先"))
        self.download_destination_input = QLineEdit()
        self.download_destination_input.setToolTip(
            "今回のダウンロード先です。編集しても永続設定は変わりません。"
        )
        self.download_destination_input.textChanged.connect(
            lambda _text: self._sync_linked_catalog_path()
        )
        execute_row.addWidget(self.download_destination_input, 1)
        self.download_button = QPushButton("③ 確認済み動画をダウンロード")
        self.download_button.setEnabled(False)
        self.download_button.clicked.connect(self.download_checked_videos)
        execute_row.addWidget(self.download_button)
        layout.addLayout(execute_row)

        catalog_box = QGroupBox("任意：動画情報を管理JSONへ記録")
        catalog_layout = QVBoxLayout(catalog_box)
        self.catalog_enabled_checkbox = QCheckBox("今回成功した動画の情報を管理JSONへ記録する")
        self.catalog_enabled_checkbox.setToolTip(
            "チェックした今回だけ、成功した動画のYouTube情報と保存先をJSON台帳へ追加します。"
            "URL履歴は、ここで明示した管理JSON以外には保存しません。"
        )
        self.catalog_enabled_checkbox.toggled.connect(self._update_catalog_controls)
        catalog_layout.addWidget(self.catalog_enabled_checkbox)

        catalog_row = QHBoxLayout()
        catalog_row.addWidget(QLabel("管理JSON"))
        self.catalog_link_checkbox = QCheckBox("動画の保存先と同じ")
        self.catalog_link_checkbox.setToolTip(
            f"通常は動画の保存先に {DEFAULT_CATALOG_FILENAME} を自動指定します。"
            "管理JSON欄を直接編集すると、別の保存先へ切り替わります。"
        )
        self.catalog_link_checkbox.toggled.connect(self._on_catalog_link_toggled)
        catalog_row.addWidget(self.catalog_link_checkbox)
        self.catalog_path_input = QLineEdit()
        self.catalog_path_input.setToolTip(
            "フォルダを指定すると video_catalog.json を使います。"
            "ファイルを指定する場合は .json で終わらせてください。"
        )
        self.catalog_path_input.textEdited.connect(self._on_catalog_path_edited)
        catalog_row.addWidget(self.catalog_path_input, 1)
        catalog_layout.addLayout(catalog_row)
        layout.addWidget(catalog_box)

        provider_ready, provider_message = po_token_provider_status()
        self.po_token_checkbox = QCheckBox("高画質PO Token補助を使う（Cookieなし・保存なし）")
        self.po_token_checkbox.setChecked(provider_ready)
        self.po_token_checkbox.setEnabled(provider_ready)
        self.po_token_checkbox.setToolTip(
            f"{provider_message}\n"
            "通常経路と同一の最高画質形式だけを再試行します。"
            "URL・Cookie・Token・履歴は保存しません。"
        )
        layout.addWidget(self.po_token_checkbox)

        wpc_ready, wpc_message = wpc_provider_status()
        self.wpc_checkbox = QCheckBox("一時ブラウザPO Token補助も使う（Cookieなし・実行後に消去）")
        # WPC launches a real browser and is experimental. Availability only
        # means prerequisites exist; never start it automatically.
        self.wpc_checkbox.setChecked(False)
        self.wpc_checkbox.setEnabled(wpc_ready)
        self.wpc_checkbox.setToolTip(
            f"{wpc_message}\n"
            "実験的な補助です。チェックした場合だけ、BgUtil方式でも403のときにChromiumを一時起動します。"
            "既存ブラウザのプロファイルは読まず、専用のメモリ上プロファイルを終了後に削除します。"
        )
        layout.addWidget(self.wpc_checkbox)
        return tab

    def _build_creator_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        input_box = QGroupBox("① 投稿者・チャンネルURL")
        input_layout = QFormLayout(input_box)
        self.creator_url_input = QLineEdit()
        self.creator_url_input.setPlaceholderText("https://www.youtube.com/@投稿者名")
        input_layout.addRow("URL", self.creator_url_input)
        buttons = QHBoxLayout()
        self.collect_button = QPushButton("動画・Shortsの一覧を取得")
        self.collect_button.clicked.connect(self.collect_creator_inventory)
        buttons.addWidget(self.collect_button)
        self.cancel_collection_button = QPushButton("取得を停止")
        self.cancel_collection_button.setEnabled(False)
        self.cancel_collection_button.clicked.connect(self.cancel_creator_collection)
        buttons.addWidget(self.cancel_collection_button)
        buttons.addStretch(1)
        input_layout.addRow("", buttons)
        self.creator_progress_label = QLabel("未取得")
        input_layout.addRow("進捗", self.creator_progress_label)
        layout.addWidget(input_box)

        preview_box = QGroupBox("② 取得結果")
        preview_layout = QVBoxLayout(preview_box)
        preview_layout.addWidget(
            QLabel("通常動画とShortsを別々に取得し、同じ動画URLは1件にまとめます。")
        )
        self.creator_table = self._create_table()
        preview_layout.addWidget(self.creator_table)
        layout.addWidget(preview_box, 1)

        execute_row = QHBoxLayout()
        execute_row.addWidget(QLabel("一覧の出力先"))
        self.export_destination_input = QLineEdit()
        self.export_destination_input.setToolTip(
            "今回の情報ファイルの出力先です。編集しても永続設定は変わりません。"
        )
        execute_row.addWidget(self.export_destination_input, 1)
        self.export_button = QPushButton("③ URL一覧と情報CSVを書き出す")
        self.export_button.setEnabled(False)
        self.export_button.clicked.connect(self.export_creator_inventory)
        execute_row.addWidget(self.export_button)
        layout.addLayout(execute_row)
        return tab

    @staticmethod
    def _create_table() -> QTableWidget:
        table = QTableWidget(0, 5)
        table.setHorizontalHeaderLabels(["種別", "題名", "URL", "投稿日時", "長さ"])
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setAlternatingRowColors(True)
        header = table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(1, header.ResizeMode.Stretch)
        header.setSectionResizeMode(2, header.ResizeMode.Stretch)
        for column in (0, 3, 4):
            header.setSectionResizeMode(column, header.ResizeMode.ResizeToContents)
        return table

    def _refresh_destination_labels(self) -> None:
        self.download_destination_input.setText(self._settings["download_output_directory"])
        self.export_destination_input.setText(self._settings["metadata_export_directory"])
        configured_catalog_path = self._settings["catalog_json_path"]
        self.catalog_link_checkbox.setChecked(not bool(configured_catalog_path))
        if configured_catalog_path:
            self.catalog_path_input.setText(configured_catalog_path)
        else:
            self._sync_linked_catalog_path()
        self._update_catalog_controls()

    def _linked_catalog_path(self) -> Path | None:
        destination_text = self.download_destination_input.text().strip()
        if not destination_text:
            return None
        return resolve_catalog_path("", output_directory=Path(destination_text).expanduser())

    def _sync_linked_catalog_path(self) -> None:
        if not self.catalog_link_checkbox.isChecked():
            return
        linked_path = self._linked_catalog_path()
        self.catalog_path_input.setText(str(linked_path) if linked_path is not None else "")

    def _on_catalog_link_toggled(self, checked: bool) -> None:
        if checked:
            self._sync_linked_catalog_path()
        self._update_catalog_controls()

    def _on_catalog_path_edited(self, _text: str) -> None:
        if self.catalog_link_checkbox.isChecked():
            self.catalog_link_checkbox.setChecked(False)

    def _update_catalog_controls(self) -> None:
        enabled = self.catalog_enabled_checkbox.isChecked()
        self.catalog_link_checkbox.setEnabled(enabled)
        self.catalog_path_input.setEnabled(enabled and not self.catalog_link_checkbox.isChecked())

    def _destination_path(self, input_widget: QLineEdit, *, purpose: str) -> Path | None:
        value = input_widget.text().strip()
        if not value:
            self._notify(f"{purpose}を入力してください。")
            return None
        return Path(value).expanduser()

    def _show_records(self, table: QTableWidget, records: list[VideoRecord]) -> None:
        table.setRowCount(len(records))
        for row, record in enumerate(records):
            self._set_record_row(table, row, record)

    @staticmethod
    def _set_record_row(table: QTableWidget, row: int, record: VideoRecord) -> None:
        kind = "Shorts" if record.source_kind == "short" else "動画"
        values = [kind, record.title, record.url, record.upload_date, _duration_text(record.duration_seconds)]
        for column, value in enumerate(values):
            item = QTableWidgetItem(value)
            if column in {1, 2}:
                item.setToolTip(value)
            table.setItem(row, column, item)

    def _append_record(self, table: QTableWidget, record: VideoRecord) -> None:
        row = table.rowCount()
        table.insertRow(row)
        self._set_record_row(table, row, record)

    def _notify(self, *lines: str) -> None:
        self.notice.setPlainText("\n".join(line for line in lines if line))

    def _clear_download_input(self) -> None:
        self.video_urls_editor.clear()
        self._inspected_videos = []
        self.download_table.setRowCount(0)
        self.download_button.setEnabled(False)
        self._notify("動画URLの入力と確認結果を空にしました。")

    def _start_task(
        self,
        task: Callable[[], object],
        on_result: Callable[[Any], None],
        *,
        status: str,
        activity_label: str | None = None,
    ) -> None:
        if self._task is not None:
            return
        self._set_busy(True)
        self._notify(status)
        def tracked_task() -> object:
            with runtime_activity(activity_label or "動画情報を確認中"):
                return task()

        thread = _TaskThread(tracked_task, self)
        self._task = thread
        thread.result_ready.connect(on_result)
        thread.failed.connect(lambda message: self._notify("処理できませんでした。", message))
        thread.finished.connect(self._finish_task)
        thread.start()

    def _finish_task(self) -> None:
        thread = self._task
        self._task = None
        self._collecting_creator = False
        self._set_busy(False)
        if thread is not None:
            thread.deleteLater()

    def _set_busy(self, busy: bool) -> None:
        self.inspect_button.setEnabled(not busy)
        self.collect_button.setEnabled(not busy)
        self.download_button.setEnabled(not busy and bool(self._inspected_videos))
        self.export_button.setEnabled(not busy and bool(self._creator_videos))
        self.cancel_collection_button.setEnabled(busy and self._collecting_creator)

    def inspect_download_urls(self) -> None:
        urls = split_urls(self.video_urls_editor.toPlainText())
        if not urls:
            self._notify("動画URLを一行ずつ入力してください。")
            return
        self._inspected_videos = []
        self.download_button.setEnabled(False)
        self._start_task(
            lambda: inspect_videos(urls),
            self._on_download_inspected,
            status=f"{len(urls)} 件の動画情報を確認しています。",
        )

    def _on_download_inspected(self, result: object) -> None:
        records, errors = result
        self._inspected_videos = list(records)
        self._show_records(self.download_table, self._inspected_videos)
        lines = [f"確認完了: {len(records)} 件をダウンロード候補にしました。"]
        if errors:
            lines.extend(errors)
        self._notify(*lines)
        self.download_button.setEnabled(bool(self._inspected_videos))

    def download_checked_videos(self) -> None:
        if not self._inspected_videos:
            self._notify("先に動画情報を確認してください。")
            return
        destination = self._destination_path(self.download_destination_input, purpose="動画の保存先")
        if destination is None:
            return
        catalog_path: Path | None = None
        if self.catalog_enabled_checkbox.isChecked():
            try:
                catalog_path = resolve_catalog_path(
                    self.catalog_path_input.text(), output_directory=destination
                )
            except ValueError as exc:
                self._notify("管理JSONの保存先を確認してください。", str(exc))
                return
        answer = QMessageBox.question(
            self,
            "ダウンロードを実行",
            f"{len(self._inspected_videos)} 件を次の場所へダウンロードします。\n"
            f"{destination}"
            + (f"\n\n成功分の情報を次の管理JSONへ記録します。\n{catalog_path}" if catalog_path else "")
            + "\n\n実行しますか？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        records = list(self._inspected_videos)
        use_po_token_provider = self.po_token_checkbox.isChecked()
        use_wpc_provider = self.wpc_checkbox.isChecked()

        def task() -> object:
            report = download_videos(
                records,
                destination,
                allow_po_token_provider=use_po_token_provider,
                allow_wpc_provider=use_wpc_provider,
            )
            if catalog_path is None:
                return report, None
            try:
                return report, write_video_catalog(catalog_path, report.catalog_videos)
            except Exception as exc:
                # The downloads themselves succeeded.  Report a catalog error
                # separately instead of presenting the entire run as failed.
                return report, exc

        self._start_task(
            task,
            self._on_download_finished,
            status=(
                f"{len(records)} 件のダウンロードを開始しました。"
                + (" 同一品質の一時PO Token補助を有効にしています。" if use_po_token_provider else "")
                + (" 一時ブラウザPO Token補助も有効にしています。" if use_wpc_provider else "")
            ),
            activity_label="動画ダウンロード中",
        )

    def _on_download_finished(self, result: object) -> None:
        report, catalog_result = result
        assert isinstance(report, DownloadReport)
        lines = [
            f"ダウンロード完了: 成功 {report.succeeded} 件 / 失敗 {report.failed} 件",
            *report.messages,
        ]
        if isinstance(catalog_result, CatalogWriteReport):
            lines.append(
                f"管理JSONを更新: 追加 {catalog_result.added} 件 / 更新 {catalog_result.updated} 件"
            )
            lines.append(f"管理JSON: {catalog_result.path}")
        elif isinstance(catalog_result, Exception):
            lines.append(f"管理JSONへ記録できませんでした: {catalog_result}")
        self._notify(*lines)

    def collect_creator_inventory(self) -> None:
        creator_url = self.creator_url_input.text().strip()
        if not creator_url:
            self._notify("投稿者・チャンネルURLを入力してください。")
            return
        self._creator_videos = []
        self.export_button.setEnabled(False)
        self.creator_table.setRowCount(0)
        self._collecting_creator = True
        self._set_busy(True)
        self.creator_progress_label.setText("大枠の一覧と件数を取得しています。")
        self._notify("投稿者の通常動画とShortsの大枠を取得しています。")
        thread: _TaskThread

        def task() -> object:
            with runtime_activity("投稿者一覧を取得中"):
                return collect_creator_videos(
                    creator_url,
                    on_overview=thread.progress.emit,
                    on_progress=thread.progress.emit,
                    should_cancel=thread.is_cancel_requested,
                )

        thread = _TaskThread(task, self)
        self._task = thread
        thread.progress.connect(self._on_creator_progress)
        thread.result_ready.connect(self._on_creator_collected)
        thread.failed.connect(lambda message: self._notify("一覧を取得できませんでした。", message))
        thread.finished.connect(self._finish_task)
        thread.start()

    def cancel_creator_collection(self) -> None:
        if self._task is None or not self._collecting_creator:
            return
        self._task.request_cancel()
        self.cancel_collection_button.setEnabled(False)
        self.creator_progress_label.setText("停止を受け付けました。現在の1件が終わり次第停止します。")
        self._notify("停止を受け付けました。現在の通信が終わり次第、取得済み分を残して停止します。")

    def _on_creator_progress(self, event: object) -> None:
        if isinstance(event, CreatorOverview):
            self.creator_progress_label.setText(
                f"大枠を取得: 通常動画 {event.video_total} 件 / Shorts {event.short_total} 件 / 合計 {event.total} 件"
            )
            self._notify(
                "大枠を取得しました。新しい順に詳細を確認して一覧へ追加します。",
                f"通常動画 {event.video_total} 件 / Shorts {event.short_total} 件 / 合計 {event.total} 件",
            )
            return
        if not isinstance(event, CreatorProgress):
            return
        kind = "Shorts" if event.source_kind == "short" else "通常動画"
        self.creator_progress_label.setText(
            f"{kind} {event.completed_in_kind} / {event.total_in_kind} 件  "
            f"（全体 {event.completed_total} / {event.total} 件）"
        )
        if event.record is not None:
            self._append_record(self.creator_table, event.record)

    def _on_creator_collected(self, result: object) -> None:
        assert isinstance(result, CreatorCollectionResult)
        self._creator_videos = list(result.records)
        lines = [
            (f"途中で停止: {len(result.records)} / {result.overview.total} 件を取得しました。")
            if result.cancelled
            else f"一覧取得完了: {len(result.records)} 件を出力候補にしました。"
        ]
        if result.errors:
            lines.extend(result.errors)
        self._notify(*lines)
        if result.cancelled:
            self.creator_progress_label.setText(
                f"途中で停止: 取得済み {len(result.records)} / {result.overview.total} 件"
            )
        else:
            self.creator_progress_label.setText(
                f"完了: 取得済み {len(result.records)} / {result.overview.total} 件"
            )
        self.export_button.setEnabled(bool(self._creator_videos))

    def export_creator_inventory(self) -> None:
        if not self._creator_videos:
            self._notify("先に投稿者の動画一覧を取得してください。")
            return
        destination = self._destination_path(self.export_destination_input, purpose="一覧の出力先")
        if destination is None:
            return
        records = list(self._creator_videos)
        label = self.creator_url_input.text().strip()
        self._start_task(
            lambda: export_creator_videos(
                records, destination, creator_label=label
            ),
            self._on_creator_exported,
            status=f"{len(records)} 件のURL一覧と情報CSVを書き出しています。",
            activity_label="動画一覧を書き出し中",
        )

    def _on_creator_exported(self, result: object) -> None:
        report = result
        assert isinstance(report, ChannelExportReport)
        self._notify(
            f"{report.count} 件を書き出しました。",
            f"URL一覧: {report.urls_path}",
            f"情報CSV: {report.details_path}",
        )

    def show_settings(self) -> None:
        if self._settings_dialog is None:
            dialog = QDialog(self)
            dialog.setWindowTitle("YouTube ダウンローダーの永続設定")
            dialog.resize(preferred_window_size(dialog))
            layout = QVBoxLayout(dialog)
            self._settings_status_label = QLabel()
            layout.addWidget(self._settings_status_label)
            layout.addWidget(
                QLabel(
                    "保存するのは2つの出力先と任意の管理JSON保存先だけです。"
                    "管理JSON自体は、画面で明示してダウンロードに成功したときだけ作成・更新します。"
                    "URL履歴、実行履歴、認証情報は保存しません。"
                )
            )
            editor = JsonSettingsEditor(
                validate=settings.validate_text,
                path_keys={
                    "download_output_directory",
                    "metadata_export_directory",
                    "catalog_json_path",
                },
                fields={
                    "download_output_directory": JsonFieldSpec("動画の保存先", "ダウンロードした動画を保存するフォルダです。"),
                    "metadata_export_directory": JsonFieldSpec("一覧の保存先", "取得した動画情報の一覧を保存するフォルダです。"),
                    "catalog_json_path": JsonFieldSpec("管理JSONの保存先", "空欄なら、その実行で選んだ動画保存先に従います。"),
                },
            )
            layout.addWidget(editor, 1)
            buttons = QDialogButtonBox()
            template_button = buttons.addButton("雛形へ戻す", QDialogButtonBox.ButtonRole.ResetRole)
            editor.bind_edit_button(template_button)
            save_button = buttons.addButton("保存", QDialogButtonBox.ButtonRole.AcceptRole)
            editor.bind_save_button(save_button)
            close_button = buttons.addButton(QDialogButtonBox.StandardButton.Close)
            template_button.clicked.connect(lambda: editor.setPlainText(settings.template_text()))
            save_button.clicked.connect(self.save_settings)
            close_button.clicked.connect(dialog.close)
            layout.addWidget(buttons)
            self._settings_dialog = dialog
            self._settings_editor = editor
        assert self._settings_editor is not None
        self._settings_editor.setPlainText(settings.editable_text())
        state, detail = settings.settings_status()
        self._settings_editor.set_source_state(state, detail)
        self._settings_status_label.setText(f"設定状態: {state} — {detail}")
        self._settings_dialog.show()
        self._settings_dialog.raise_()
        self._settings_dialog.activateWindow()

    def save_settings(self) -> None:
        if self._settings_editor is None:
            return
        try:
            self._settings = settings.save_text(self._settings_editor.toPlainText())
        except ValueError as exc:
            QMessageBox.warning(self, "設定を保存できません", str(exc))
            return
        self._refresh_destination_labels()
        self._notify("永続設定を保存しました。保存先は次の実行から使われます。")
        if self._settings_dialog is not None:
            self._settings_dialog.close()


def create_screen(return_to_main: Callable[[], None]) -> YouTubeDownloaderScreen:
    """Factory used by the central launcher catalog."""
    return YouTubeDownloaderScreen(return_to_main)
