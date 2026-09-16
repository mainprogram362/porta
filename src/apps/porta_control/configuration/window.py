"""Explicit recovery screen for the optional local CONFIG directory."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from settings import user_space
from settings.persistent_settings import BOOTSTRAP_PATH, create_bootstrap_template, locate_settings_directory, reset_config
from gui import AppHeader, AppPageLayout, JsonFieldSpec, JsonSettingsEditor
from gui.persistent_settings import show_settings_location_editor
from gui.layout_policy import preferred_window_size
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QComboBox,
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

from apps.porta_control.configuration.templates import (
    ConfigurationTemplate,
    all_configuration_templates,
    config_reset_templates,
    create_single_configuration_template,
    create_user_space_template,
)
from apps.system_tools.external_app_launcher import locations as external_locations
from gui.flow_layout import FlowLayout
from runtime.settings_sessions import SettingsSession
from PySide6.QtCore import QTimer


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
    def describe_work_state(self):
        if self.user_root_input.text() != user_space.configured_root_text():
            return {"level": 3, "reason": "ユーザー領域の入力が現在の保存設定と異なります。"}
        return {"level": 1, "reason": "設定の入口画面です。編集中の補助画面は別途確認します。"}

    def __init__(self, return_to_main: Callable[[], None]) -> None:
        super().__init__()
        self._return_to_main = return_to_main
        self._settings_session = SettingsSession()
        self._user_root_baseline = user_space.configured_root_text()
        layout = AppPageLayout(self)
        header = AppHeader(return_to_main, title="設定")
        header.content_layout.addStretch(1)
        layout.addWidget(header)
        layout.addWidget(QLabel("履歴や作業内容は保存しません。ここは明示的に作る既定設定だけを扱います。"))
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.coordination_status = QLabel()
        self.coordination_status.setWordWrap(True)
        layout.addWidget(self.coordination_status)
        actions = FlowLayout()
        location = QPushButton("保存先入口を編集")
        location.clicked.connect(self.show_settings_location_editor)
        actions.addWidget(location)
        external = QPushButton("外部プログラム位置")
        external.clicked.connect(
            lambda: self.show_program_locations(
                "外部プログラムの位置",
                external_locations.external_editable_text,
                external_locations.external_template_text,
                external_locations.save_external_text,
                external_locations.validate_external_text,
                external_locations.external_status,
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
        layout.addLayout(actions)

        user_box = QGroupBox("外部ユーザー領域")
        user_layout = QVBoxLayout(user_box)
        user_layout.addWidget(
            QLabel(
                "CONFIG・AIモデル・辞書などを一つにまとめる任意の外部領域です。"
                "@PORTA・@HOME・絶対パス、または入口設定を基準にした相対パスで指定できます。"
            )
        )
        user_row = QHBoxLayout()
        self.user_root_input = QLineEdit(user_space.configured_root_text())
        self.user_root_input.setPlaceholderText("例: @PORTA/porta_user / @HOME/porta_user / /任意の場所/porta_user")
        user_row.addWidget(self.user_root_input, 1)
        create_locator = QPushButton("入口の雛形を作成")
        create_locator.setToolTip(
            "PORTA本体直下に persistent_settings.json を作成します。"
            "既に入口設定がある場合は上書きしません。"
        )
        create_locator.clicked.connect(self.create_user_root_locator_template)
        user_row.addWidget(create_locator)
        browse = QPushButton("選択")
        browse.clicked.connect(self.select_user_root)
        user_row.addWidget(browse)
        save_root = QPushButton("場所を保存")
        save_root.clicked.connect(self.save_user_root)
        user_row.addWidget(save_root)
        user_layout.addLayout(user_row)
        self.user_status = QLabel()
        self.user_status.setWordWrap(True)
        user_layout.addWidget(self.user_status)
        layout.addWidget(user_box)

        recovery_box = QGroupBox("構造・雛形の再生成")
        recovery_layout = QVBoxLayout(recovery_box)
        recovery_layout.addWidget(
            QLabel(
                "既存の場所は上書きせず、[new] を付けた新しい作成先へ雛形を出します。"
                "入口設定や現在使用中のユーザー領域は変更しません。"
            )
        )
        recovery_actions = FlowLayout()
        whole_template = QPushButton("porta_user全体を新規生成…")
        whole_template.clicked.connect(self.show_user_space_template_dialog)
        recovery_actions.addWidget(whole_template)
        single_template = QPushButton("設定雛形を1つ新規生成…")
        single_template.clicked.connect(self.show_single_template_dialog)
        recovery_actions.addWidget(single_template)
        recovery_layout.addLayout(recovery_actions)
        layout.addWidget(recovery_box)
        layout.addStretch(1)
        for label in self.findChildren(QLabel):
            label.setWordWrap(True)
        self._coordination_timer = QTimer(self)
        self._coordination_timer.setInterval(2000)
        self._coordination_timer.timeout.connect(self.refresh_coordination)
        self._coordination_timer.start()
        self.destroyed.connect(lambda: self._settings_session.close())
        self.refresh()
        self.refresh_coordination()

    def refresh_coordination(self) -> None:
        others = self._settings_session.others()
        if self._settings_session.error:
            self.coordination_status.setText("注意: " + self._settings_session.error)
        elif others:
            self.coordination_status.setText(
                f"注意: 別の設定画面が{len(others)}件開いています。"
                "それぞれの表示は独立しています。古い内容による上書きは保存時に停止します。"
            )
        else:
            self.coordination_status.clear()

    def _allow_setting_write(self) -> bool:
        self.refresh_coordination()
        if self._settings_session.is_newest():
            return True
        QMessageBox.warning(
            self,
            "この設定画面からは保存できません",
            "この画面より後に開かれた設定画面があります。現在の入力は保存していません。"
            "新しい設定画面を使うか、閉じた後に最新の設定から開き直してください。",
        )
        return False

    def show_settings_location_editor(self) -> None:
        show_settings_location_editor(self, allow_save=self._allow_setting_write)
        self.refresh()

    def show_environment_tutorial(self) -> None:
        """Show relocatable, copyable setup directions for desktop integration."""
        dialog = QDialog(self)
        dialog.setWindowTitle("環境整備チュートリアル")
        dialog.resize(preferred_window_size(dialog))
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
        if not self._allow_setting_write():
            return
        try:
            path = create_bootstrap_template()
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "看板の雛形を作成できません", str(exc))
            return
        self.user_root_input.setText(user_space.configured_root_text())
        self.refresh()
        QMessageBox.information(
            self,
            "入口設定の雛形を作成しました",
            f"作成先: {path}\n\n"
            "これは既定の「@PORTA/porta_user」を指す入口設定だけです。"
            "ユーザー領域のフォルダや設定はまだ作成していません。",
        )

    def save_user_root(self) -> None:
        if not self._allow_setting_write():
            return
        if user_space.configured_root_text() != self._user_root_baseline:
            QMessageBox.warning(
                self,
                "ユーザー領域を保存できません",
                "この画面を開いた後に、別の画面でユーザー領域が変更されました。"
                "現在の入力は保存していません。最新の設定から開き直してください。",
            )
            return
        try:
            user_space.save_root_path(self.user_root_input.text())
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "ユーザー領域を保存できません", str(exc))
            return
        self._user_root_baseline = user_space.configured_root_text()
        self.refresh()
        QMessageBox.information(self, "保存しました", "外部ユーザー領域の場所を保存しました。")

    def show_user_space_template_dialog(self) -> None:
        """Choose one disconnected destination for a complete user-space skeleton."""
        dialog = QDialog(self)
        dialog.setWindowTitle("porta_user全体を新規生成")
        layout = QVBoxLayout(dialog)
        layout.addWidget(
            QLabel(
                "フォルダ構造と全アプリの設定JSON雛形だけを生成します。"
                "AIモデル・辞書・キャッシュ・個人データは作成・コピーしません。"
            )
        )
        row = QHBoxLayout()
        destination_input = QLineEdit(str(BOOTSTRAP_PATH.parent / "porta_user[new]"))
        row.addWidget(destination_input, 1)
        choose_parent = QPushButton("親フォルダを選択")
        row.addWidget(choose_parent)
        layout.addLayout(row)
        buttons = QDialogButtonBox()
        create = buttons.addButton("全体を新規生成", QDialogButtonBox.ButtonRole.AcceptRole)
        close = buttons.addButton("閉じる", QDialogButtonBox.ButtonRole.RejectRole)
        layout.addWidget(buttons)

        def select_parent() -> None:
            selected = QFileDialog.getExistingDirectory(
                dialog, "新しい porta_user 雛形の親フォルダを選択", str(BOOTSTRAP_PATH.parent)
            )
            if selected:
                destination_input.setText(str(Path(selected) / "porta_user[new]"))

        def create_template() -> None:
            destination = Path(destination_input.text().strip()).expanduser()
            try:
                result = create_user_space_template(destination)
            except (OSError, ValueError) as exc:
                QMessageBox.warning(dialog, "porta_user雛形を生成できません", str(exc))
                return
            QMessageBox.information(
                dialog,
                "porta_user雛形を生成しました",
                f"作成先: {result.root}\n"
                f"フォルダ: {len(result.directories) + 1}個\n"
                f"設定JSON雛形: {len(result.config_files)}個\n\n"
                "入口設定は変更していません。使う場合だけ、保存先入口を編集してください。",
            )
            dialog.accept()

        choose_parent.clicked.connect(select_parent)
        create.clicked.connect(create_template)
        close.clicked.connect(dialog.reject)
        dialog.exec()

    def show_single_template_dialog(self) -> None:
        """Create one selected config template at an explicit new file path."""
        templates = tuple(
            template for template in all_configuration_templates() if template.relative_path is not None
        )
        dialog = QDialog(self)
        dialog.setWindowTitle("設定雛形を1つ新規生成")
        layout = QVBoxLayout(dialog)
        layout.addWidget(
            QLabel(
                "雛形を1つだけ新しいJSONファイルとして作成します。"
                "認識済みのユーザー領域がある場合は、その config/ を初期入力にします。"
            )
        )
        selector = QComboBox()
        for template in templates:
            selector.addItem(template.title, template)
        layout.addWidget(selector)
        row = QHBoxLayout()
        destination_input = QLineEdit()
        row.addWidget(destination_input, 1)
        choose_parent = QPushButton("親フォルダを選択")
        row.addWidget(choose_parent)
        layout.addLayout(row)
        buttons = QDialogButtonBox()
        create = buttons.addButton("雛形を新規生成", QDialogButtonBox.ButtonRole.AcceptRole)
        close = buttons.addButton("閉じる", QDialogButtonBox.ButtonRole.RejectRole)
        layout.addWidget(buttons)

        def selected_template() -> ConfigurationTemplate:
            template = selector.currentData()
            assert isinstance(template, ConfigurationTemplate)
            return template

        def default_destination(template: ConfigurationTemplate) -> Path:
            assert template.relative_path is not None
            user_paths = user_space.configured_paths()
            config_directory = (
                user_paths.config
                if user_paths is not None
                else BOOTSTRAP_PATH.parent / "porta_user[new]" / "config"
            )
            relative = template.relative_path
            return config_directory / relative.with_name(
                f"{relative.stem}[new]{relative.suffix}"
            )

        def refresh_destination() -> None:
            destination_input.setText(str(default_destination(selected_template())))

        def select_parent() -> None:
            selected = QFileDialog.getExistingDirectory(
                dialog, "設定雛形の親フォルダを選択", str(BOOTSTRAP_PATH.parent)
            )
            if selected:
                filename = default_destination(selected_template()).name
                destination_input.setText(str(Path(selected) / filename))

        def create_template() -> None:
            template = selected_template()
            destination = Path(destination_input.text().strip()).expanduser()
            try:
                created = create_single_configuration_template(template, destination)
            except (OSError, ValueError) as exc:
                QMessageBox.warning(dialog, "設定雛形を生成できません", str(exc))
                return
            QMessageBox.information(
                dialog,
                "設定雛形を生成しました",
                f"雛形: {template.title}\n作成先: {created}\n\n"
                "入口設定や現在使用中のユーザー領域は変更していません。",
            )
            dialog.accept()

        selector.currentIndexChanged.connect(lambda _index: refresh_destination())
        choose_parent.clicked.connect(select_parent)
        create.clicked.connect(create_template)
        close.clicked.connect(dialog.reject)
        refresh_destination()
        dialog.exec()

    def show_program_locations(
        self,
        title: str,
        editable_text: Callable[[], str],
        template_text: Callable[[], str],
        save_text: Callable[[str], object],
        validate_text: Callable[[str], object],
        source_status: Callable[[], tuple[str, str]],
    ) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle(title)
        dialog.resize(preferred_window_size(dialog))
        layout = QVBoxLayout(dialog)
        explanation = QLabel(
            "PORTAが明示的に確認する外部プログラム置き場です。"
            "JSONの locations 配列へパスを書き、各置き場の直下から start.sh を持つフォルダを読み込みます。"
        )
        explanation.setWordWrap(True)
        layout.addWidget(explanation)
        editor = JsonSettingsEditor(
            validate=validate_text,
            path_keys={"locations"},
            fields={
                "locations": JsonFieldSpec(
                    "外部プログラムの置き場",
                    "各場所の直下から start.sh を持つプログラム用フォルダを探します。",
                )
            },
        )
        original_text = editable_text()
        editor.setPlainText(original_text)
        editor.set_source_state(*source_status())
        layout.addWidget(editor, 1)
        buttons = QDialogButtonBox()
        template = buttons.addButton("雛形へ戻す", QDialogButtonBox.ButtonRole.ResetRole)
        editor.bind_edit_button(template)
        save = buttons.addButton("保存", QDialogButtonBox.ButtonRole.AcceptRole)
        editor.bind_save_button(save)
        close = buttons.addButton(QDialogButtonBox.StandardButton.Close)
        template.clicked.connect(lambda: editor.setPlainText(template_text()))

        def save_shared() -> None:
            if not self._allow_setting_write():
                return
            if editable_text() != original_text:
                QMessageBox.warning(
                    dialog,
                    f"{title}を保存できません",
                    "この画面を開いた後に、別の画面で同じ設定が変更されました。"
                    "現在の入力は保存していません。最新の設定から開き直してください。",
                )
                return
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
        if not self._allow_setting_write():
            return
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
            result = reset_config(config_reset_templates())
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

    def shutdown(self) -> None:
        self._coordination_timer.stop()
        self._settings_session.close()


def create_screen(return_to_main: Callable[[], None]) -> ConfigurationScreen:
    return ConfigurationScreen(return_to_main)
