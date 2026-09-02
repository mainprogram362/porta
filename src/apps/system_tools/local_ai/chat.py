"""Transient chat screen backed by the shared, local-only AI session."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
import shlex
import time

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QWidget,
)
from gui import AppHeader, AppPageLayout

from . import settings
from .runtime import LocalAiSession


_SYSTEM_MESSAGE = {
    "role": "system",
    "content": "あなたはローカルで動く会話アシスタントです。日本語で簡潔かつ正確に答えてください。",
}
_CONTEXT_MESSAGE_LIMIT = 20


class LocalAiChatScreen(QWidget):
    """A chat whose transcript and context disappear when this screen closes."""

    def __init__(self, return_to_main: Callable[[], None]) -> None:
        super().__init__()
        self._return_to_main = return_to_main
        self._session: LocalAiSession | None = None
        self._conversation: list[dict[str, str]] = []
        self._request_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="local-ai-chat")
        self._request: Future[str] | None = None
        self._is_shutdown = False
        self._load_started_at: float | None = None
        self._load_delay_reported = False
        self._load_timer = QTimer(self)
        self._load_timer.setInterval(250)
        self._load_timer.timeout.connect(self._update_load_state)
        self._request_timer = QTimer(self)
        self._request_timer.setInterval(100)
        self._request_timer.timeout.connect(self._collect_response)
        self.setMinimumSize(720, 560)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = AppPageLayout(self)
        header = AppHeader(self.return_to_main, title="ローカルAI チャット")
        header.content_layout.addStretch(1)
        layout.addWidget(header)
        explanation = QLabel(
            "会話とAIの待機プロセスは、この画面を閉じると消えます。会話内容・履歴・応答は保存しません。"
        )
        explanation.setWordWrap(True)
        layout.addWidget(explanation)
        self._status = QLabel("AIは未読み込みです。")
        self._status.setWordWrap(True)
        layout.addWidget(self._status)
        layout.addWidget(QLabel("実行状況（この画面を閉じると消去・ファイル保存なし）"))
        self._activity = QPlainTextEdit()
        self._activity.setReadOnly(True)
        self._activity.setFixedHeight(150)
        self._activity.setPlaceholderText("AIを読み込むと、使用するファイルと現在の処理をここに表示します。")
        layout.addWidget(self._activity)
        self._transcript = QPlainTextEdit()
        self._transcript.setReadOnly(True)
        self._transcript.setPlaceholderText("AIを読み込むと、ここに今回だけの会話を表示します。")
        layout.addWidget(self._transcript, 1)
        self._input = QPlainTextEdit()
        self._input.setFixedHeight(88)
        self._input.setPlaceholderText("メッセージを入力")
        layout.addWidget(self._input)
        actions = QHBoxLayout()
        self._load_button = QPushButton("AIを読み込む")
        self._load_button.clicked.connect(self.load_ai)
        actions.addWidget(self._load_button)
        self._send_button = QPushButton("送信")
        self._send_button.setEnabled(False)
        self._send_button.clicked.connect(self.send_message)
        actions.addWidget(self._send_button)
        clear_button = QPushButton("会話を消去")
        clear_button.clicked.connect(self.clear_conversation)
        actions.addWidget(clear_button)
        self._stop_button = QPushButton("AIを終了")
        self._stop_button.setEnabled(False)
        self._stop_button.clicked.connect(self.stop_ai)
        actions.addWidget(self._stop_button)
        actions.addStretch(1)
        layout.addLayout(actions)
        self._append_activity("画面を開きました。AIプロセスはまだ起動していません。")
        self._append_activity("会話履歴・実行状況は、この画面のメモリ内だけに置きます。")

    def load_ai(self) -> None:
        if self._session is not None:
            return
        configured = settings.load_settings()
        if not configured["runner_path"] or not configured["model_path"]:
            self._append_activity("起動を中止: runner_path または model_path が未設定です。")
            QMessageBox.information(
                self,
                "ローカルAI設定が必要です",
                "ローカルAI設定で runner_path と model_path を保存してから読み込んでください。",
            )
            return
        session = LocalAiSession(Path(configured["runner_path"]), Path(configured["model_path"]))
        self._append_activity("ローカルAI設定を読み込みました。会話・履歴ファイルは読み込みません。")
        self._append_activity(f"実行ファイル: {session.runner_path}")
        self._append_activity(f"モデル: {session.model_path}{_file_size_suffix(session.model_path)}")
        self._append_activity("モデルはこの場所からメモリへ読み込みます。モデルのコピーや会話ログの保存はしません。")
        try:
            session.start()
        except (OSError, ValueError) as exc:
            self._append_activity(f"起動に失敗: {exc}")
            QMessageBox.warning(self, "AIを読み込めません", str(exc))
            return
        self._session = session
        self._load_started_at = time.monotonic()
        self._load_delay_reported = False
        self._load_button.setEnabled(False)
        self._stop_button.setEnabled(True)
        self._status.setText("AIモデルを読み込んでいます。初回は少し時間がかかります。")
        self._append_activity(f"プロセスを開始しました（PID {session.process_id}）。")
        self._append_activity("実行コマンド: " + shlex.join(session.launch_command))
        self._append_activity(f"接続先は {session.endpoint} のみです。外部ネットワークへ待ち受けません。")
        self._append_activity("現在: GGUFモデルを読み込み、会話サーバーの準備を待っています。")
        self._load_timer.start()

    def _update_load_state(self) -> None:
        session = self._session
        if session is None:
            self._load_timer.stop()
            return
        if session.is_ready():
            self._load_timer.stop()
            self._status.setText("AIを読み込みました。この画面を閉じるまで、今回だけ会話できます。")
            self._append_activity("準備完了: モデルを読み込みました。現在は入力待ちです。")
            self._send_button.setEnabled(True)
            self._input.setFocus()
            return
        exit_code = session.process_exit_code
        if exit_code is not None:
            self._load_timer.stop()
            self._status.setText(f"AIの起動に失敗しました（終了コード {exit_code}）。")
            self._append_activity(f"起動に失敗: プロセスが終了しました（終了コード {exit_code}）。")
            self._session = None
            self._load_button.setEnabled(True)
            self._stop_button.setEnabled(False)
            return
        if (
            self._load_started_at is not None
            and time.monotonic() - self._load_started_at > 120
            and not self._load_delay_reported
        ):
            self._status.setText("AIモデルを読み込んでいます。メモリ状況によっては時間がかかります。")
            self._append_activity("読み込み継続中: 2分を超えています。メモリ状況によっては時間がかかります。")
            self._load_delay_reported = True

    def send_message(self) -> None:
        session = self._session
        prompt = self._input.toPlainText().strip()
        if session is None or not session.is_ready() or not prompt or self._request is not None:
            return
        self._input.clear()
        self._conversation.append({"role": "user", "content": prompt})
        self._append_transcript("あなた", prompt)
        context = [_SYSTEM_MESSAGE, *self._conversation[-_CONTEXT_MESSAGE_LIMIT:]]
        self._request = self._request_pool.submit(session.chat, context)
        self._send_button.setEnabled(False)
        self._status.setText("AIが応答を作成しています。")
        self._append_activity(
            f"現在: localhost へ会話要求を送信しました（今回の文脈 {len(context) - 1} 件）。"
        )
        self._request_timer.start()

    def _collect_response(self) -> None:
        request = self._request
        if request is None or not request.done():
            return
        self._request_timer.stop()
        self._request = None
        try:
            response = request.result()
        except RuntimeError as exc:
            self._append_transcript("エラー", str(exc))
            self._status.setText("AIの応答を取得できませんでした。")
            self._append_activity(f"応答取得に失敗: {exc}")
        else:
            self._conversation.append({"role": "assistant", "content": response})
            self._append_transcript("AI", response)
            self._status.setText("AIを読み込み済みです。")
            self._append_activity("応答を受信しました。会話本文は画面内だけにあり、保存していません。")
        if self._session is not None and self._session.is_ready():
            self._send_button.setEnabled(True)

    def clear_conversation(self) -> None:
        self._conversation.clear()
        self._transcript.clear()
        self._status.setText("今回の会話を消去しました。AIは読み込み済みのままです。")
        self._append_activity("会話表示と会話文脈をメモリから消去しました。AIプロセスは起動したままです。")

    def stop_ai(self) -> None:
        self._load_timer.stop()
        self._request_timer.stop()
        session = self._session
        if session is not None:
            self._append_activity(f"AIプロセスを終了します（PID {session.process_id}）。")
            session.stop()
            self._append_activity("AIプロセスを終了しました。モデルを使うためのメモリは解放対象になります。")
        self._session = None
        self._request = None
        self._load_button.setEnabled(True)
        self._send_button.setEnabled(False)
        self._stop_button.setEnabled(False)
        self._status.setText("AIを終了しました。会話は画面を閉じると消えます。")

    def return_to_main(self) -> None:
        self.shutdown()
        self._return_to_main()

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self.shutdown()
        super().closeEvent(event)

    def shutdown(self) -> None:
        """Stop this screen's disposable server when its owning app exits."""
        if self._is_shutdown:
            return
        self._is_shutdown = True
        self.stop_ai()
        self._request_pool.shutdown(wait=False, cancel_futures=True)

    def _append_transcript(self, speaker: str, message: str) -> None:
        previous = self._transcript.toPlainText()
        block = f"{speaker}:\n{message}"
        self._transcript.setPlainText(f"{previous}\n\n{block}" if previous else block)
        scrollbar = self._transcript.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def _append_activity(self, message: str) -> None:
        """Append an in-memory operational event without recording chat text."""
        stamp = time.strftime("%H:%M:%S")
        previous = self._activity.toPlainText()
        self._activity.setPlainText(f"{previous}\n[{stamp}] {message}" if previous else f"[{stamp}] {message}")
        scrollbar = self._activity.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())


def _file_size_suffix(path: Path) -> str:
    """Show the current model file size when it can be read without failing startup."""
    try:
        size = path.stat().st_size
    except OSError:
        return "（サイズ確認不可）"
    return f"（{size / 1024 / 1024:.0f} MiB）"
