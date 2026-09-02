"""One deliberate GUI for opening and mounting encrypted storage."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QProcess
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from foundation.path import absolute_path
from foundation.runtime_activity import RuntimeActivity, begin_runtime_activity
from gui import AppHeader, AppPageLayout
from gui.composites import NoWheelComboBox, PathLineInput
from gui.persistent_settings import create_app_settings_file, show_settings_location_editor

from . import settings
from .luks import LuksMountRequest, privileged_mount_command, validate_mount_request


def _add_favorite_paths_menu(
    menu,
    paths: tuple[str, ...],
    *,
    title: str,
    choose_path: Callable[[str], None],
) -> None:  # type: ignore[no-untyped-def]
    """Add a path-specific chooser only to its matching input field."""
    if not paths:
        return
    if menu.actions():
        menu.addSeparator()
    favorite_menu = menu.addMenu(title)
    favorite_menu.setToolTipsVisible(True)
    for path_text in paths:
        action = favorite_menu.addAction(path_text)
        action.setToolTip(path_text)
        action.triggered.connect(lambda _checked=False, value=path_text: choose_path(value))


class StorageEncryptionScreen(QWidget):
    """Open and mount LUKS data after repeated local and privileged checks."""

    def __init__(self, return_to_main: Callable[[], None]) -> None:
        super().__init__()
        self._return_to_main = return_to_main
        self._settings = settings.load_settings()
        self._validated_request: LuksMountRequest | None = None
        self._process: QProcess | None = None
        self._activity: RuntimeActivity | None = None
        self._settings_dialog: QDialog | None = None
        self._settings_editor: QTextEdit | None = None
        self._settings_status: QLabel | None = None
        self._settings_base: QLabel | None = None
        self.setMinimumSize(760, 540)
        self._build_ui()
        self._reload_favorites()
        self._invalidate_check("コンテナとマウント先を入力し、「入力を確認」を押してください。")

    def _build_ui(self) -> None:
        layout = AppPageLayout(self)

        header = AppHeader(
            self._return_to_main,
            title="暗号化領域を開く・マウント",
            on_settings=self.show_settings,
        )
        header.content_layout.addStretch(1)
        layout.addWidget(header)
        note = QLabel(
            "パスフレーズ・鍵・履歴は保存しません。"
            "実行時はOSの認証画面だけを使い、LUKS形式とマウント先を改めて確認します。"
        )
        note.setWordWrap(True)
        layout.addWidget(note)

        format_box = QGroupBox("暗号化形式")
        format_layout = QHBoxLayout(format_box)
        self.format_combo = NoWheelComboBox()
        self.format_combo.addItem("LUKS（現在対応）", "luks")
        self.format_combo.setToolTip("初期版はLUKSだけに対応しています。")
        format_layout.addWidget(self.format_combo)
        format_layout.addStretch(1)
        layout.addWidget(format_box)

        paths_box = QGroupBox("開く対象")
        paths_layout = QFormLayout(paths_box)
        paths_layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)

        self.container_input = PathLineInput(drop_as="full_path")
        self.container_input.setPlaceholderText("LUKSコンテナまたは /dev/... の絶対パス")
        self.container_input.setToolTip(
            "通常ファイル型のLUKSコンテナ、または /dev/... のブロックデバイスを指定します。"
            "相対パスを入力した場合は、このアプリの起動場所を基準に絶対パスへ変換します。"
        )
        self.container_input.set_context_menu_augmenter(
            lambda menu: _add_favorite_paths_menu(
                menu,
                self._settings.container_favorites,
                title="お気に入りのコンテナを入力",
                choose_path=self._set_container_path,
            )
        )
        self.container_input.textChanged.connect(self._on_input_changed)
        self.container_input.editingFinished.connect(
            lambda: self._normalize_visible_path(self.container_input)
        )
        container_row = QHBoxLayout()
        container_row.setContentsMargins(0, 0, 0, 0)
        container_row.addWidget(self.container_input, 1)
        self.container_favorites_combo = NoWheelComboBox()
        self.container_favorites_combo.setMinimumWidth(190)
        self.container_favorites_combo.setToolTip("選ぶと、コンテナ欄をその値で直ちに上書きします。")
        self.container_favorites_combo.currentIndexChanged.connect(self._apply_container_favorite)
        container_row.addWidget(self.container_favorites_combo)
        paths_layout.addRow("暗号化領域", container_row)

        self.mount_point_input = PathLineInput(drop_as="directory")
        self.mount_point_input.setPlaceholderText("空のマウント先フォルダの絶対パス")
        self.mount_point_input.setToolTip(
            "空の既存フォルダだけを指定できます。ファイルをドロップした場合は親フォルダを入力します。"
            "相対パスを入力した場合は、このアプリの起動場所を基準に絶対パスへ変換します。"
        )
        self.mount_point_input.set_context_menu_augmenter(
            lambda menu: _add_favorite_paths_menu(
                menu,
                self._settings.mount_point_favorites,
                title="お気に入りのマウント先を入力",
                choose_path=self._set_mount_point,
            )
        )
        self.mount_point_input.textChanged.connect(self._on_input_changed)
        self.mount_point_input.editingFinished.connect(
            lambda: self._normalize_visible_path(self.mount_point_input)
        )
        mount_row = QHBoxLayout()
        mount_row.setContentsMargins(0, 0, 0, 0)
        mount_row.addWidget(self.mount_point_input, 1)
        self.mount_favorites_combo = NoWheelComboBox()
        self.mount_favorites_combo.setMinimumWidth(190)
        self.mount_favorites_combo.setToolTip("選ぶと、マウント先欄をその値で直ちに上書きします。")
        self.mount_favorites_combo.currentIndexChanged.connect(self._apply_mount_favorite)
        mount_row.addWidget(self.mount_favorites_combo)
        paths_layout.addRow("マウント先", mount_row)

        self.passphrase_input = QLineEdit()
        self.passphrase_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.passphrase_input.setPlaceholderText("LUKSのパスフレーズ（保存しません）")
        self.passphrase_input.setToolTip(
            "この実行だけに使います。お気に入り・永続設定・通知には保存せず、"
            "開く・マウントを開始した直後にこの欄から消去します。"
        )
        self.passphrase_input.textChanged.connect(self._update_mount_button)
        paths_layout.addRow("LUKSパスフレーズ", self.passphrase_input)

        self.preset_combo = NoWheelComboBox()
        self.preset_combo.setMinimumWidth(240)
        self.preset_combo.setToolTip("選ぶと、コンテナとマウント先の両方を直ちに上書きします。")
        self.preset_combo.currentIndexChanged.connect(self._apply_mount_preset)
        preset_row = QHBoxLayout()
        preset_row.setContentsMargins(0, 0, 0, 0)
        preset_row.addWidget(self.preset_combo, 1)
        paths_layout.addRow("コンテナ＋マウント先", preset_row)
        layout.addWidget(paths_box)

        self.check_result = QPlainTextEdit()
        self.check_result.setReadOnly(True)
        self.check_result.setMinimumHeight(118)
        self.check_result.setPlaceholderText("入力確認の結果と、実行結果をここに表示します。選択してコピーできます。")
        layout.addWidget(self.check_result, 1)

        footer = QHBoxLayout()
        check_button = QPushButton("入力を確認")
        check_button.clicked.connect(self.check_inputs)
        footer.addWidget(check_button)
        self.mount_button = QPushButton("開く・マウント")
        self.mount_button.setToolTip("確認済みの内容だけを、OSの認証画面を通して開いてマウントします。")
        self.mount_button.clicked.connect(self.request_mount)
        footer.addWidget(self.mount_button)
        footer.addStretch(1)
        layout.addLayout(footer)

    def _reload_favorites(self) -> None:
        """Reload only complete settings and rebuild the non-persistent controls."""
        self._settings = settings.load_settings()
        self._fill_path_combo(
            self.container_favorites_combo,
            self._settings.container_favorites,
            "コンテナのお気に入りなし",
        )
        self._fill_path_combo(
            self.mount_favorites_combo,
            self._settings.mount_point_favorites,
            "マウント先のお気に入りなし",
        )
        self.preset_combo.clear()
        self.preset_combo.addItem("両方のプリセットを選択", -1)
        for index, preset in enumerate(self._settings.mount_presets):
            self.preset_combo.addItem(preset.name, index)

    @staticmethod
    def _fill_path_combo(combo: QComboBox, paths: tuple[str, ...], placeholder: str) -> None:
        combo.clear()
        combo.addItem(placeholder, "")
        for path_text in paths:
            combo.addItem(path_text, path_text)

    def _apply_container_favorite(self, _index: int | None = None) -> None:
        path_text = str(self.container_favorites_combo.currentData() or "")
        if path_text:
            self._set_container_path(path_text)

    def _apply_mount_favorite(self, _index: int | None = None) -> None:
        path_text = str(self.mount_favorites_combo.currentData() or "")
        if path_text:
            self._set_mount_point(path_text)

    def _apply_mount_preset(self, _index: int | None = None) -> None:
        index = self.preset_combo.currentData()
        if not isinstance(index, int) or index < 0 or index >= len(self._settings.mount_presets):
            return
        preset = self._settings.mount_presets[index]
        self.container_input.setText(preset.container_path)
        self.mount_point_input.setText(preset.mount_point)
        self._invalidate_check(f"プリセット「{preset.name}」を両方の欄へ入力しました。")

    def _set_container_path(self, path_text: str) -> None:
        self.container_input.setText(str(absolute_path(Path(path_text).expanduser())))
        self._invalidate_check("コンテナのお気に入りを入力しました。内容を確認してください。")

    def _set_mount_point(self, path_text: str) -> None:
        self.mount_point_input.setText(str(absolute_path(Path(path_text).expanduser())))
        self._invalidate_check("マウント先のお気に入りを入力しました。内容を確認してください。")

    def _normalize_visible_path(self, input_widget: PathLineInput) -> None:
        """Keep every visible path absolute even when the user types relative text."""
        text = input_widget.text().strip()
        if not text:
            return
        candidate = Path(text).expanduser()
        if not candidate.is_absolute():
            candidate = Path.cwd() / candidate
        normalized = str(absolute_path(candidate))
        if input_widget.text() != normalized:
            input_widget.setText(normalized)

    def _on_input_changed(self) -> None:
        if self._validated_request is not None:
            self._invalidate_check("入力が変わりました。もう一度「入力を確認」を押してください。")

    def _update_mount_button(self) -> None:
        self.mount_button.setEnabled(
            self._validated_request is not None and bool(self.passphrase_input.text()) and self._process is None
        )

    def _invalidate_check(self, message: str) -> None:
        self._validated_request = None
        self._update_mount_button()
        self.check_result.setPlainText(message)

    def check_inputs(self) -> None:
        if self._process is not None:
            self.check_result.setPlainText("マウント操作中は入力を確認できません。完了を待ってください。")
            return
        if self.format_combo.currentData() != "luks":
            self._invalidate_check("この形式にはまだ対応していません。")
            return
        validation = validate_mount_request(self.container_input.text(), self.mount_point_input.text())
        self._validated_request = validation.request
        messages = list(validation.messages)
        if validation.is_valid and not self.passphrase_input.text():
            self._validated_request = None
            messages.append("LUKSパスフレーズを入力してください。値は保存しません。")
        self._update_mount_button()
        self.check_result.setPlainText("\n".join(messages))

    def request_mount(self) -> None:
        request = self._validated_request
        if request is None:
            self.check_inputs()
            return
        answer = QMessageBox.question(
            self,
            "暗号化領域を開く・マウント",
            "次の暗号化領域を開き、空のフォルダへマウントします。\n\n"
            f"暗号化領域: {request.container_path}\n"
            f"マウント先: {request.mount_point}\n\n"
            "OSの認証画面が出ます。パスフレーズ・鍵・実行履歴はこのアプリに保存しません。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            self.check_result.setPlainText("開く・マウントを中止しました。")
            return
        self._start_mount(request)

    def _start_mount(self, request: LuksMountRequest) -> None:
        secret = bytearray(self.passphrase_input.text().encode("utf-8"))
        program, arguments = privileged_mount_command(
            request, passphrase_byte_count=len(secret)
        )
        process = QProcess(self)
        process.setProgram(program)
        process.setArguments(list(arguments))
        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        process.finished.connect(self._finish_mount)
        process.errorOccurred.connect(self._report_mount_error)
        self._process = process
        try:
            self._activity = begin_runtime_activity("暗号化領域を開く・マウント")
        except OSError:
            self._activity = None
        self.passphrase_input.clear()
        self._update_mount_button()
        self.check_result.setPlainText(
            "OSの認証を待っています。パスフレーズ欄は消去しました。"
            "暗号化領域を確認して開き、マウントします。"
        )
        process.start()
        process.write(bytes(secret))
        process.closeWriteChannel()
        for index in range(len(secret)):
            secret[index] = 0

    def _finish_mount(self, exit_code: int, _exit_status: object) -> None:
        process = self._process
        self._process = None
        if self._activity is not None:
            self._activity.close()
            self._activity = None
        output = ""
        if process is not None:
            output = bytes(process.readAll()).decode(errors="replace").strip()
        if exit_code == 0:
            self.check_result.setPlainText(output or "LUKSを開き、マウントしました。")
        else:
            self.check_result.setPlainText(
                "開く・マウントに失敗しました。" + (f"\n{output}" if output else "")
            )
        self._validated_request = None
        self._update_mount_button()

    def _report_mount_error(self, _error: QProcess.ProcessError) -> None:
        process = self._process
        if process is None:
            return
        self._process = None
        if self._activity is not None:
            self._activity.close()
            self._activity = None
        self.check_result.setPlainText(f"開く・マウントを開始できませんでした。\n{process.errorString()}")
        self._update_mount_button()

    def show_settings(self) -> None:
        if self._settings_dialog is None:
            dialog = QDialog(self)
            dialog.setWindowTitle("暗号化領域マウントの永続設定")
            dialog.resize(780, 520)
            layout = QVBoxLayout(dialog)
            status = QLabel()
            status.setWordWrap(True)
            layout.addWidget(status)
            base = QLabel()
            base.setWordWrap(True)
            layout.addWidget(base)
            help_text = QLabel(
                "パスフレーズ・鍵・実行履歴は書かないでください。"
                "container_favorites は暗号化コンテナ用、mount_point_favorites はマウント先用です。"
                "mount_presets は名前・コンテナ・マウント先の3項目を1組として登録します。"
                "空欄だけの枠は無視されます。"
                "絶対パス、@HOME、~、またはこの設定JSONの場所を基準にした相対パスを使えます。"
                "画面上へ入力するときは、すべて絶対パスに変換されます。"
            )
            help_text.setWordWrap(True)
            layout.addWidget(help_text)
            editor = QTextEdit()
            layout.addWidget(editor, 1)
            buttons = QDialogButtonBox()
            template = buttons.addButton("雛形へ戻す", QDialogButtonBox.ButtonRole.ResetRole)
            location = buttons.addButton("保存先入口", QDialogButtonBox.ButtonRole.ActionRole)
            create = buttons.addButton("保存先・設定を作成", QDialogButtonBox.ButtonRole.ActionRole)
            save = buttons.addButton("保存", QDialogButtonBox.ButtonRole.AcceptRole)
            close = buttons.addButton(QDialogButtonBox.StandardButton.Close)
            template.clicked.connect(lambda: editor.setPlainText(settings.template_text()))
            location.clicked.connect(lambda: show_settings_location_editor(dialog))
            create.clicked.connect(lambda: self.create_settings_file(dialog))
            save.clicked.connect(self.save_settings)
            close.clicked.connect(dialog.close)
            layout.addWidget(buttons)
            self._settings_dialog = dialog
            self._settings_editor = editor
            self._settings_status = status
            self._settings_base = base
        assert self._settings_editor is not None
        assert self._settings_status is not None
        assert self._settings_base is not None
        self._settings_editor.setPlainText(settings.editable_text())
        state, detail = settings.settings_status()
        self._settings_status.setText(f"設定状態: {state} — {detail}")
        base = settings.configuration_base_directory()
        self._settings_base.setText(
            f"相対パスの基準（この設定JSONのフォルダ）: {base}"
            if base is not None
            else "相対パスの基準: 確認できません。保存先入口を先に確認してください。"
        )
        self._settings_dialog.show()
        self._settings_dialog.raise_()
        self._settings_dialog.activateWindow()

    def create_settings_file(self, parent: QWidget) -> None:
        create_app_settings_file(parent, settings.create_settings_file)
        if self._settings_editor is not None:
            self._settings_editor.setPlainText(settings.editable_text())

    def save_settings(self) -> None:
        if self._settings_editor is None:
            return
        try:
            settings.save_text(self._settings_editor.toPlainText())
        except ValueError as exc:
            QMessageBox.warning(self, "設定を保存できません", str(exc))
            return
        if self._settings_dialog is not None:
            self._settings_dialog.close()
        self._reload_favorites()
        self._invalidate_check("永続設定を保存しました。お気に入りを入力してから内容を確認してください。")


def create_screen(return_to_main: Callable[[], None]) -> StorageEncryptionScreen:
    return StorageEncryptionScreen(return_to_main)
