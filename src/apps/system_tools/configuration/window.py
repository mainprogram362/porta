"""Explicit recovery screen for the optional local CONFIG directory."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from foundation import shared_launchers, user_space
from foundation.persistent_settings import (
    BOOTSTRAP_PATH,
    create_bootstrap_template,
    locate_settings_directory,
    reset_config,
)
from gui import AppHeader, AppPageLayout
from gui.persistent_settings import show_settings_location_editor
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from apps.file_tools.file_manager import persistent_settings as file_manager_settings
from apps.media_tools.media_information import settings as media_information_settings
from apps.media_tools.video_encoder import settings as video_encoder_settings
from apps.media_tools.youtube_downloader import settings as youtube_downloader_settings
from apps.system_tools.local_ai import settings as local_ai_settings
from apps.system_tools.storage_encryption import settings as storage_encryption_settings


def nautilus_integration_tutorial_text() -> str:
    """Describe the retained receiver without installing anything on the host."""
    root = Path(__file__).resolve().parents[4]
    return (
        "外部アプリ連携（現在は保留）\n\n"
        "PORTAは、OS本体やホームフォルダへ連携用ファイル、シンボリックリンク、"
        "デスクトップ項目を自動作成しません。\n"
        "Nautilusの右クリックへ登録する従来手順も、PORTAを移動したときに壊れたリンクを"
        "残すため撤廃しました。現在、この画面から行う外部連携の設定作業はありません。\n\n"
        "受信後のパス検査・用途選択・各アプリへの転送というPORTA内部の根幹機能は残しています。"
        "将来、OS側に設置物を残さない受け渡し方式が決まったときに再利用できます。\n\n"
        "内部受信コマンド（開発・動作確認用）\n"
        f"'{root / 'start.sh'}' --external-open choose -- /絶対/パス1 /絶対/パス2\n\n"
        "このコマンドはPORTA自身を起動するだけで、OSへ登録物を作成しません。\n"
    )


class ConfigurationScreen(QWidget):
    def __init__(self, return_to_main: Callable[[], None]) -> None:
        super().__init__()
        self._return_to_main = return_to_main
        layout = AppPageLayout(self)
        header = AppHeader(return_to_main, title="設定")
        header.content_layout.addStretch(1)
        layout.addWidget(header)
        layout.addWidget(QLabel("履歴や作業内容は保存しません。ここは明示的に作る既定設定だけを扱います。"))
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        actions = QHBoxLayout()
        location = QPushButton("保存先入口を編集")
        location.clicked.connect(lambda: show_settings_location_editor(self))
        actions.addWidget(location)
        standalone_apps = QPushButton("独立ツール位置")
        standalone_apps.clicked.connect(
            lambda: self.show_program_locations(
                "独立ツールの位置",
                shared_launchers.standalone_apps_editable_text,
                shared_launchers.standalone_apps_template_text,
                shared_launchers.save_standalone_apps_text,
            )
        )
        actions.addWidget(standalone_apps)
        external = QPushButton("外部プログラム位置")
        external.clicked.connect(
            lambda: self.show_program_locations(
                "外部プログラムの位置",
                shared_launchers.external_editable_text,
                shared_launchers.external_template_text,
                shared_launchers.save_external_text,
            )
        )
        actions.addWidget(external)
        tutorial = QPushButton("環境整備チュートリアル")
        tutorial.setToolTip("Nautilusなど、外部アプリからPORTAを呼び出す手動設定を確認します。")
        tutorial.clicked.connect(self.show_environment_tutorial)
        actions.addWidget(tutorial)
        self.reset = QPushButton("CONFIGを初期化")
        self.reset.setToolTip("現在のCONFIGを日時付きで退避し、全アプリの設定雛形を作り直します。")
        self.reset.clicked.connect(self.reset_config)
        actions.addWidget(self.reset)
        actions.addStretch(1)
        layout.addLayout(actions)

        user_box = QGroupBox("外部ユーザー領域")
        user_layout = QVBoxLayout(user_box)
        user_layout.addWidget(
            QLabel(
                "CONFIG・AIモデル・辞書などを一つにまとめる任意の外部領域です。"
                "絶対パス、または案内札を基準にした相対パスで指定できます。"
            )
        )
        user_row = QHBoxLayout()
        self.user_root_input = QLineEdit(user_space.configured_root_text())
        self.user_root_input.setPlaceholderText("例: porta_user / ../porta_user / /任意の場所/porta_user")
        user_row.addWidget(self.user_root_input, 1)
        create_locator = QPushButton("直下へ看板の雛形を作成")
        create_locator.setToolTip(
            "PORTA本体直下に persistent_settings_location.txt を作成します。"
            "既に看板がある場合は上書きしません。"
        )
        create_locator.clicked.connect(self.create_user_root_locator_template)
        user_row.addWidget(create_locator)
        browse = QPushButton("選択")
        browse.clicked.connect(self.select_user_root)
        user_row.addWidget(browse)
        save_root = QPushButton("場所を保存")
        save_root.clicked.connect(self.save_user_root)
        user_row.addWidget(save_root)
        create_root = QPushButton("構造を作成")
        create_root.clicked.connect(self.create_user_structure)
        user_row.addWidget(create_root)
        user_layout.addLayout(user_row)
        self.user_status = QLabel()
        self.user_status.setWordWrap(True)
        user_layout.addWidget(self.user_status)
        layout.addWidget(user_box)
        layout.addStretch(1)
        self.refresh()

    def show_environment_tutorial(self) -> None:
        """Show relocatable, copyable setup directions for desktop integration."""
        dialog = QDialog(self)
        dialog.setWindowTitle("環境整備チュートリアル")
        dialog.resize(860, 650)
        layout = QVBoxLayout(dialog)
        introduction = QLabel(
            "外部アプリ側の右クリック機能は環境ごとに違うため、送信側だけを手動で登録します。"
            "PORTA側は同じ受信方式で全件を先に検査し、問題が1件でもあれば全件を取り込みません。"
        )
        introduction.setWordWrap(True)
        layout.addWidget(introduction)

        tabs = QTabWidget()
        external_page = QWidget()
        external_layout = QVBoxLayout(external_page)
        guide = QTextEdit()
        guide.setReadOnly(True)
        guide.setPlainText(nautilus_integration_tutorial_text())
        external_layout.addWidget(guide)
        tabs.addTab(external_page, "外部アプリ連携")
        layout.addWidget(tabs, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.exec()

    def refresh(self) -> None:
        config_location = locate_settings_directory()
        self.reset.setEnabled(
            config_location.state in {"ready", "missing_directory"}
            and config_location.directory is not None
        )
        if config_location.directory is None:
            self.status.setText(
                f"CONFIG: 保存先を確認できません。入口を設定してください。\n入口: {BOOTSTRAP_PATH}"
            )
        elif config_location.state == "missing_directory":
            self.status.setText(
                f"CONFIG: 未作成です。初期化すると全雛形を作成します。\n場所: {config_location.directory}"
            )
        else:
            self.status.setText(
                "CONFIG: 使用中です。初期化時は現在の内容を日時付きで退避します。\n"
                f"場所: {config_location.directory}"
            )
        paths = user_space.configured_paths()
        if paths is None:
            self.user_status.setText("ユーザー領域: 未設定")
        else:
            state = "作成済み" if paths.root.is_dir() else "場所だけ設定済み・未作成"
            self.user_status.setText(f"ユーザー領域: {state}\n場所: {paths.root}")

    def select_user_root(self) -> None:
        selected = QFileDialog.getExistingDirectory(self, "外部ユーザー領域を選択")
        if selected:
            self.user_root_input.setText(selected)

    def create_user_root_locator_template(self) -> None:
        """Offer deliberate recovery of the missing user-root locator only."""
        try:
            path = create_bootstrap_template()
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "看板の雛形を作成できません", str(exc))
            return
        self.user_root_input.setText(user_space.configured_root_text())
        self.refresh()
        QMessageBox.information(
            self,
            "看板の雛形を作成しました",
            f"作成先: {path}\n\n"
            "これは既定の「porta_user」を指す看板だけです。"
            "ユーザー領域のフォルダや設定はまだ作成していません。",
        )

    def save_user_root(self) -> None:
        try:
            user_space.save_root_path(self.user_root_input.text())
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "ユーザー領域を保存できません", str(exc))
            return
        self.refresh()
        QMessageBox.information(self, "保存しました", "外部ユーザー領域の場所を保存しました。")

    def create_user_structure(self) -> None:
        try:
            user_space.save_root_path(self.user_root_input.text())
            paths = user_space.create_configured_directories()
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "ユーザー領域を作成できません", str(exc))
            return
        self.refresh()
        QMessageBox.information(self, "作成しました", f"ユーザー領域を確認しました。\n{paths.root}")

    def show_program_locations(
        self,
        title: str,
        editable_text: Callable[[], str],
        template_text: Callable[[], str],
        save_text: Callable[[str], object],
    ) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle(title)
        dialog.resize(720, 420)
        layout = QVBoxLayout(dialog)
        explanation = QLabel(
            "PythonとPORTA Coreが共通で確認するプログラム置き場です。"
            "1行に1パスを書き、各置き場の直下から start.sh を持つフォルダを読み込みます。"
        )
        explanation.setWordWrap(True)
        layout.addWidget(explanation)
        editor = QTextEdit(editable_text())
        layout.addWidget(editor, 1)
        buttons = QDialogButtonBox()
        template = buttons.addButton("雛形へ戻す", QDialogButtonBox.ButtonRole.ResetRole)
        save = buttons.addButton("保存", QDialogButtonBox.ButtonRole.AcceptRole)
        close = buttons.addButton(QDialogButtonBox.StandardButton.Close)
        template.clicked.connect(lambda: editor.setPlainText(template_text()))

        def save_shared() -> None:
            try:
                save_text(editor.toPlainText())
            except (OSError, ValueError) as exc:
                QMessageBox.warning(dialog, f"{title}を保存できません", str(exc))
                return
            dialog.accept()

        save.clicked.connect(save_shared)
        close.clicked.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.exec()

    def reset_config(self) -> None:
        location = locate_settings_directory()
        if location.directory is None:
            QMessageBox.warning(self, "CONFIGを初期化できません", location.detail)
            return
        answer = QMessageBox.warning(
            self,
            "CONFIGを初期化",
            "全アプリの設定を初期値へ戻します。\n\n"
            f"対象: {location.directory}\n"
            "現在のCONFIGフォルダは、同じ場所へ日時付きで退避します。\n"
            "初期化後は必要な個人設定を改めて入力してください。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            result = reset_config(
                {
                    file_manager_settings.SETTINGS_FILE_NAME: file_manager_settings.template_text(),
                    video_encoder_settings.SETTINGS_FILE_NAME: video_encoder_settings.template_text(),
                    youtube_downloader_settings.SETTINGS_FILE_NAME: youtube_downloader_settings.template_text(),
                    media_information_settings.SETTINGS_FILE_NAME: media_information_settings.template_text(),
                    storage_encryption_settings.SETTINGS_FILE_NAME: storage_encryption_settings.template_text(),
                    local_ai_settings.SETTINGS_FILE_NAME: local_ai_settings.template_text(),
                    str(shared_launchers.STANDALONE_APPS_RELATIVE_FILE_PATH): shared_launchers.standalone_apps_template_text(),
                    str(shared_launchers.EXTERNAL_RELATIVE_FILE_PATH): shared_launchers.external_template_text(),
                }
            )
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "CONFIGを初期化できません", str(exc))
        else:
            backup_text = (
                f"\n初期化前の退避先: {result.backup_directory}"
                if result.backup_directory is not None
                else "\n初期化前のCONFIGは存在しませんでした。"
            )
            QMessageBox.information(
                self,
                "CONFIGを初期化しました",
                f"{len(result.created_files)}個の設定雛形を作成しました。{backup_text}",
            )
        self.refresh()


def create_screen(return_to_main: Callable[[], None]) -> ConfigurationScreen:
    return ConfigurationScreen(return_to_main)
