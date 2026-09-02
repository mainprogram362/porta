"""Screen for the shared portable local-AI file locations."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtWidgets import QDialogButtonBox, QLabel, QMessageBox, QTextEdit, QWidget

from foundation.user_space import configured_paths
from gui import AppHeader, AppPageLayout
from gui.persistent_settings import create_app_settings_file, show_settings_location_editor

from . import settings


class LocalAiSettingsScreen(QWidget):
    def __init__(self, return_to_main: Callable[[], None]) -> None:
        super().__init__()
        self._return_to_main = return_to_main
        self.setMinimumSize(640, 390)
        layout = AppPageLayout(self)
        header = AppHeader(return_to_main, title="ローカルAI設定")
        header.content_layout.addStretch(1)
        layout.addWidget(header)
        user_paths = configured_paths()
        placement = (
            f"runner: {user_paths.ai_runners} / model: {user_paths.ai_models}"
            if user_paths is not None
            else "ユーザー領域が未設定です。先に設定画面で root_path を指定してください。"
        )
        explanation = QLabel(
            "全アプリ共通で使う、ローカルAIの二つのファイル場所だけを保存します。\n"
            "runner_path は llama.cpp の llama-server 実行ファイル、model_path は GGUF モデルファイルの絶対パスです。\n"
            f"今後の標準配置先は {placement}\n"
            "ここへファイルを置いてから、その実際のパスを下の設定へ入力します。\n"
            "ここでAIを起動・常駐させることはありません。各アプリが必要なときだけこの設定を読みます。"
        )
        explanation.setWordWrap(True)
        layout.addWidget(explanation)
        status = QLabel()
        status.setWordWrap(True)
        layout.addWidget(status)
        editor = QTextEdit()
        editor.setPlainText(settings.editable_text())
        layout.addWidget(editor, 1)
        buttons = QDialogButtonBox()
        template = buttons.addButton("雛形へ戻す", QDialogButtonBox.ButtonRole.ResetRole)
        location = buttons.addButton("保存先入口", QDialogButtonBox.ButtonRole.ActionRole)
        create = buttons.addButton("設定を作成", QDialogButtonBox.ButtonRole.ActionRole)
        save = buttons.addButton("保存", QDialogButtonBox.ButtonRole.AcceptRole)
        template.clicked.connect(lambda: editor.setPlainText(settings.template_text()))
        location.clicked.connect(lambda: show_settings_location_editor(self))
        create.clicked.connect(lambda: self._create_settings(status))
        save.clicked.connect(lambda: self._save_settings(editor, status))
        layout.addWidget(buttons)
        self._refresh_status(status)

    def _refresh_status(self, label: QLabel) -> None:
        state, detail = settings.settings_status()
        configured = settings.load_settings()
        ready = bool(configured["runner_path"] and configured["model_path"])
        label.setText(f"設定状態: {state} — {detail}\nAIファイル指定: {'完了' if ready else '未設定'}")

    def _create_settings(self, status: QLabel) -> None:
        try:
            create_app_settings_file(self, settings.create_settings_file)
        finally:
            self._refresh_status(status)

    def _save_settings(self, editor: QTextEdit, status: QLabel) -> None:
        try:
            settings.save_text(editor.toPlainText())
        except ValueError as exc:
            QMessageBox.warning(self, "設定を保存できません", str(exc))
            return
        self._refresh_status(status)
        QMessageBox.information(self, "保存しました", "ローカルAIの共通ファイル場所を保存しました。")


def create_screen(return_to_main: Callable[[], None]) -> LocalAiSettingsScreen:
    return LocalAiSettingsScreen(return_to_main)
