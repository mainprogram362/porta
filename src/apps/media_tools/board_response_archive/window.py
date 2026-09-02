"""Screen for explicitly checking, previewing and saving thread responses."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from gui import AppHeader, AppPageLayout, PathLineInput, UrlListTextEdit
from media import (
    ShitarabaBatchInspection,
    ShitarabaSaveReport,
    inspect_thread_urls,
    render_replies,
    save_replies,
)


class _InspectionThread(QThread):
    """Keep the explicit network check outside the Qt event loop."""

    completed = Signal(object)

    def __init__(self, urls: str, parent: QWidget) -> None:
        super().__init__(parent)
        self._urls = urls

    def run(self) -> None:
        self.completed.emit(inspect_thread_urls(self._urls))


class _SaveThread(QThread):
    """Write explicitly approved response text outside the GUI event loop."""

    completed = Signal(object)
    failed = Signal(str)

    def __init__(self, threads: tuple, directory: str, parent: QWidget) -> None:
        super().__init__(parent)
        self._threads = threads
        self._directory = directory

    def run(self) -> None:
        try:
            self.completed.emit(save_replies(self._threads, self._directory))
        except Exception as exc:
            self.failed.emit(str(exc) or exc.__class__.__name__)


class BoardResponseArchiveScreen(QWidget):
    """A non-persistent, explicit response-only thread archive workflow."""

    def __init__(self, return_to_main: Callable[[], None]) -> None:
        super().__init__()
        self._return_to_main = return_to_main
        self._task: QThread | None = None
        self._inspection: ShitarabaBatchInspection | None = None
        self._previewed = False
        self._build_ui()

    def _build_ui(self) -> None:
        layout = AppPageLayout(self)
        header = AppHeader(self._return_to_main, title="したらばレス保存")
        header.content_layout.addStretch(1)
        layout.addWidget(header)

        input_box = QGroupBox("したらばスレッドURL（最大100件・1行1URL）")
        input_layout = QVBoxLayout(input_box)
        input_layout.addWidget(
            QLabel("公開スレッドURLだけを確認します。Cookie・ログイン情報・URL履歴は使いません。")
        )
        self.url_input = UrlListTextEdit()
        self.url_input.setPlaceholderText(
            "https://jbbs.shitaraba.net/bbs/read.cgi/カテゴリ/掲示板ID/スレッドID/\n"
            "1行につき1スレッド。個別レス番号、l50、範囲指定などの末尾は無視します。"
        )
        self.url_input.setFixedHeight(220)
        self.url_input.setToolTip("最大100件です。個別レスURLや末尾 l50・範囲指定のURLも対応します。")
        self.url_input.textChanged.connect(self._clear_inspection)
        self.url_input.dropRejected.connect(self._show_drop_rejection)
        input_layout.addWidget(self.url_input)
        controls = QHBoxLayout()
        self.inspect_button = QPushButton("URL・接続・形式を確認")
        self.inspect_button.clicked.connect(self.inspect_urls)
        controls.addWidget(self.inspect_button)
        clear_button = QPushButton("入力を空にする")
        clear_button.clicked.connect(self.url_input.clear)
        controls.addWidget(clear_button)
        controls.addStretch(1)
        input_layout.addLayout(controls)
        layout.addWidget(input_box)

        check_box = QGroupBox("確認結果")
        check_layout = QVBoxLayout(check_box)
        self.check_result = QPlainTextEdit()
        self.check_result.setReadOnly(True)
        self.check_result.setFixedHeight(112)
        self.check_result.setPlaceholderText("確認すると、各URLの接続・レス形式・件数をここに表示します。")
        check_layout.addWidget(self.check_result)
        extract_row = QHBoxLayout()
        self.extract_button = QPushButton("確認済みレスをプレビュー")
        self.extract_button.setEnabled(False)
        self.extract_button.setToolTip("確認済みの全スレッドから、レスだけを表示します。")
        self.extract_button.clicked.connect(self.extract_replies)
        extract_row.addWidget(self.extract_button)
        extract_row.addStretch(1)
        check_layout.addLayout(extract_row)
        layout.addWidget(check_box)

        replies_box = QGroupBox("確認プレビュー（今回だけメモリ上に保持）")
        replies_layout = QVBoxLayout(replies_box)
        self.replies_editor = QPlainTextEdit()
        self.replies_editor.setReadOnly(True)
        self.replies_editor.setPlaceholderText(
            "確認に合格した全スレッドのレスだけを表示します。プレビュー後に保存できます。"
        )
        replies_layout.addWidget(self.replies_editor)
        layout.addWidget(replies_box, 1)

        save_box = QGroupBox("出力先フォルダ")
        save_layout = QHBoxLayout(save_box)
        self.destination_input = PathLineInput(drop_as="directory")
        self.destination_input.setPlaceholderText("存在する出力先フォルダを入力またはドロップ")
        self.destination_input.setToolTip("保存時にだけ、このフォルダへスレッドごとのUTF-8テキストを作成します。")
        self.destination_input.textChanged.connect(self._update_save_enabled)
        save_layout.addWidget(self.destination_input, 1)
        self.save_button = QPushButton("確認済みレスを保存")
        self.save_button.setEnabled(False)
        self.save_button.setToolTip("確認とプレビューを済ませた全スレッドを、1件ずつ別テキストに保存します。")
        self.save_button.clicked.connect(self.save_previewed_replies)
        save_layout.addWidget(self.save_button)
        layout.addWidget(save_box)

    def _clear_inspection(self, *_args: object) -> None:
        if self._task is not None:
            return
        self._inspection = None
        self._previewed = False
        self.extract_button.setEnabled(False)
        self.save_button.setEnabled(False)
        self.check_result.clear()
        self.replies_editor.clear()

    def _show_drop_rejection(self, message: str) -> None:
        self.check_result.setPlainText(message)

    def inspect_urls(self) -> None:
        if self._task is not None:
            return
        value = self.url_input.toPlainText().strip()
        if not value:
            self.check_result.setPlainText("スレッドURLを1件以上入力してください。")
            return
        self._inspection = None
        self._previewed = False
        self.extract_button.setEnabled(False)
        self.save_button.setEnabled(False)
        self.replies_editor.clear()
        self.check_result.setPlainText("URL・接続・レス形式を確認しています。")
        self.inspect_button.setEnabled(False)
        self._task = _InspectionThread(value, self)
        self._task.completed.connect(self._inspection_finished)
        self._task.finished.connect(self._task_finished)
        self._task.start()

    def _inspection_finished(self, result: object) -> None:
        inspection = result
        assert isinstance(inspection, ShitarabaBatchInspection)
        self._inspection = inspection if inspection.is_ready else None
        self.extract_button.setEnabled(inspection.is_ready)
        self._previewed = False
        lines: list[str] = []
        for index, item in enumerate(inspection.inspections, start=1):
            if not item.ok or item.thread is None:
                lines.append(f"{index}. 不合格: {item.input_url or '入力'}\n   {item.message}")
                continue
            title = item.thread.title or "（題名なし）"
            lines.append(f"{index}. 合格: {title} — {len(item.thread.replies)} 件")
        if not inspection.is_ready:
            lines.append("保存は行いません。すべてのURLが合格するように入力を修正してください。")
        else:
            lines.append("全件合格。次に「確認済みレスをプレビュー」を押してください。")
        self.check_result.setPlainText("\n".join(lines))
        self._update_save_enabled()

    def _task_finished(self) -> None:
        self._task = None
        self.inspect_button.setEnabled(True)
        self._update_save_enabled()

    def extract_replies(self) -> None:
        if self._inspection is None or not self._inspection.is_ready:
            self.check_result.setPlainText("先に全URLの接続・形式確認を済ませてください。")
            return
        blocks = [
            f"===== {thread.title or '題名なし'} =====\n{render_replies(thread)}"
            for thread in self._inspection.threads
        ]
        self.replies_editor.setPlainText("\n\n".join(blocks))
        self._previewed = True
        self._update_save_enabled()

    def _update_save_enabled(self, *_args: object) -> None:
        destination = self.destination_input.path()
        ready = (
            self._task is None
            and self._inspection is not None
            and self._inspection.is_ready
            and self._previewed
            and destination is not None
            and destination.is_dir()
        )
        self.save_button.setEnabled(ready)

    def save_previewed_replies(self) -> None:
        if self._inspection is None or not self._inspection.is_ready or not self._previewed:
            self.check_result.setPlainText("先に全件確認とプレビューを済ませてください。")
            return
        destination = self.destination_input.path()
        if destination is None:
            self.check_result.setPlainText("出力先フォルダを入力してください。")
            return
        self.save_button.setEnabled(False)
        self.check_result.appendPlainText("確認済みレスを保存しています。")
        self._task = _SaveThread(self._inspection.threads, str(destination), self)
        self._task.completed.connect(self._save_finished)
        self._task.failed.connect(self._save_failed)
        self._task.finished.connect(self._task_finished)
        self._task.start()

    def _save_finished(self, result: object) -> None:
        report = result
        assert isinstance(report, ShitarabaSaveReport)
        paths = "\n".join(str(path) for path in report.paths)
        self.check_result.appendPlainText(f"保存完了: {len(report.paths)} ファイル\n{paths}")

    def _save_failed(self, message: str) -> None:
        self.check_result.appendPlainText(f"保存できませんでした: {message}")


def create_screen(return_to_main: Callable[[], None]) -> BoardResponseArchiveScreen:
    """Factory used by the central launcher catalog."""
    return BoardResponseArchiveScreen(return_to_main)
