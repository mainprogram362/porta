"""VeraCrypt UI backed only by a capability-checked command-line program."""

from __future__ import annotations

from collections.abc import Callable
from PySide6.QtCore import QProcess, QThread, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from gui import AppHeader, AppPageLayout, NoWheelComboBox, PathLineInput
from gui.layout_policy import set_text_rows
from gui.process_tracking import track_qprocess
from runtime.runtime_activity import RuntimeActivity, begin_runtime_activity

from .veracrypt import (
    RequestValidation,
    VeraCreateRequest,
    VeraCryptCapability,
    VeraDismountRequest,
    VeraMountRequest,
    create_command,
    dismount_command,
    list_command,
    mount_command,
    probe_backend,
    validate_create_request,
    validate_dismount_request,
    validate_mount_request,
)


_OPERATION_LABELS = {
    "mount": "既存コンテナを開く・マウント",
    "create": "新しいファイル型コンテナを作成",
    "list": "マウント状態を確認",
    "dismount": "安全に解除",
}


class _ProbeThread(QThread):
    completed = Signal(object)

    def __init__(self, operation: Callable[[], VeraCryptCapability], parent: QWidget) -> None:
        super().__init__(parent)
        self._operation = operation

    def run(self) -> None:
        try:
            result = self._operation()
        except Exception as exc:
            result = VeraCryptCapability(None, "", frozenset(), f"VeraCrypt環境の確認に失敗しました: {exc}")
        self.completed.emit(result)


class VeraCryptScreen(QWidget):
    """Create, mount, inspect, and dismount through a verified VeraCrypt CLI."""

    def __init__(
        self,
        return_to_hub: Callable[[], None],
        *,
        probe: Callable[[], VeraCryptCapability] = probe_backend,
    ) -> None:
        super().__init__()
        self._use_readable_font()
        self._return_to_hub = return_to_hub
        self._capability = VeraCryptCapability(None, "", frozenset(), "環境を確認中です。")
        self._validated: VeraMountRequest | VeraCreateRequest | VeraDismountRequest | str | None = None
        self._process: QProcess | None = None
        self._process_operation = ""
        self._activity: RuntimeActivity | None = None
        self._build_ui()
        self._set_environment_pending()
        self._probe_thread = _ProbeThread(probe, self)
        self._probe_thread.completed.connect(self._probe_finished)
        self._probe_thread.finished.connect(self._probe_thread_finished)
        self._probe_thread.start()

    def _build_ui(self) -> None:
        layout = AppPageLayout(self)
        layout.addWidget(AppHeader(self._leave_screen, title="VeraCrypt"))
        explanation = QLabel(
            "確認済みのVeraCrypt CLIだけを使います。パスワードはコマンド引数・設定・"
            "結果表示へ入れず、実行開始時に標準入力へ一度だけ渡して入力欄を消去します。"
        )
        explanation.setWordWrap(True)
        layout.addWidget(explanation)

        environment_box = QGroupBox("バックエンド環境")
        environment_layout = QVBoxLayout(environment_box)
        self.environment_status = QLabel("確認中です…")
        self.environment_status.setWordWrap(True)
        self.environment_status.setTextFormat(Qt.TextFormat.PlainText)
        self.environment_status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        environment_layout.addWidget(self.environment_status)
        self.recheck_button = QPushButton("環境を再確認")
        self.recheck_button.clicked.connect(self.recheck_environment)
        environment_layout.addWidget(self.recheck_button)
        layout.addWidget(environment_box)

        operation_box = QGroupBox("操作")
        operation_layout = QHBoxLayout(operation_box)
        self.operation_combo = NoWheelComboBox()
        for key, label in _OPERATION_LABELS.items():
            self.operation_combo.addItem(label, key)
        self.operation_combo.currentIndexChanged.connect(self._operation_changed)
        operation_layout.addWidget(self.operation_combo, 1)
        layout.addWidget(operation_box)

        self.mount_box = QGroupBox("既存コンテナを開く")
        mount_layout = QFormLayout(self.mount_box)
        mount_layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        self.volume_input = PathLineInput(drop_as="full_path")
        self.volume_input.setPlaceholderText("既存のVeraCryptコンテナまたはデバイスの絶対パス")
        mount_layout.addRow("コンテナ", self.volume_input)
        self.mount_point_input = PathLineInput(drop_as="directory")
        self.mount_point_input.setPlaceholderText("空の既存フォルダの絶対パス")
        mount_layout.addRow("マウント先", self.mount_point_input)
        self.read_only_check = QCheckBox("読み取り専用で開く")
        mount_layout.addRow("モード", self.read_only_check)
        layout.addWidget(self.mount_box)

        self.create_box = QGroupBox("新しいファイル型コンテナを作成")
        create_layout = QFormLayout(self.create_box)
        create_layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        self.output_input = PathLineInput(drop_as="full_path")
        self.output_input.setPlaceholderText("まだ存在しないコンテナファイルの絶対パス")
        create_layout.addRow("新規コンテナ", self.output_input)
        self.size_input = QSpinBox()
        self.size_input.setRange(10, 16 * 1024 * 1024)
        self.size_input.setValue(1024)
        self.size_input.setSuffix(" MiB")
        create_layout.addRow("容量", self.size_input)
        self.filesystem_combo = NoWheelComboBox()
        self.filesystem_combo.addItem("ext4（Linux向け）", "ext4")
        self.filesystem_combo.addItem("exFAT（複数OS向け）", "exFAT")
        self.filesystem_combo.addItem("FAT（小容量・互換性優先）", "FAT")
        create_layout.addRow("内部形式", self.filesystem_combo)
        create_note = QLabel(
            "既存ファイルやデバイスは作成対象にできません。通常ボリューム、AES、SHA-512を使い、"
            "乱数源にはOSの /dev/urandom を指定します。"
        )
        create_note.setWordWrap(True)
        create_layout.addRow("", create_note)
        layout.addWidget(self.create_box)

        self.dismount_box = QGroupBox("安全に解除")
        dismount_layout = QFormLayout(self.dismount_box)
        self.dismount_target_input = PathLineInput(drop_as="full_path")
        self.dismount_target_input.setPlaceholderText("コンテナまたはマウント先の絶対パス")
        dismount_layout.addRow("解除対象", self.dismount_target_input)
        layout.addWidget(self.dismount_box)

        self.password_box = QGroupBox("今回だけ使う秘密情報")
        password_layout = QFormLayout(self.password_box)
        self.password_input = QLineEdit()
        self.password_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_input.setPlaceholderText("VeraCryptパスワード（保存しません）")
        password_layout.addRow("パスワード", self.password_input)
        self.password_confirmation = QLineEdit()
        self.password_confirmation.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_confirmation.setPlaceholderText("作成時だけ同じ値を再入力")
        password_layout.addRow("確認", self.password_confirmation)
        self.show_password_check = QCheckBox("入力中だけ表示")
        self.show_password_check.toggled.connect(self._show_password)
        password_layout.addRow("", self.show_password_check)
        layout.addWidget(self.password_box)

        self.result = QPlainTextEdit()
        self.result.setReadOnly(True)
        set_text_rows(self.result, minimum=5, maximum=10)
        self.result.setPlaceholderText("環境確認、入力確認、実行結果を表示します。")
        layout.addWidget(self.result, 1)
        actions = QHBoxLayout()
        self.check_button = QPushButton("入力を確認")
        self.check_button.clicked.connect(self.check_inputs)
        actions.addWidget(self.check_button)
        self.execute_button = QPushButton("確認後に実行")
        self.execute_button.clicked.connect(self.confirm_execute)
        actions.addWidget(self.execute_button)
        actions.addStretch(1)
        layout.addLayout(actions)

        for widget in (
            self.volume_input,
            self.mount_point_input,
            self.output_input,
            self.dismount_target_input,
            self.password_input,
            self.password_confirmation,
        ):
            widget.textChanged.connect(self._input_changed)
        self.read_only_check.toggled.connect(self._input_changed)
        self.size_input.valueChanged.connect(self._input_changed)
        self.filesystem_combo.currentIndexChanged.connect(self._input_changed)
        self._operation_changed()

    def _use_readable_font(self) -> None:
        """Use a legible local baseline while respecting larger desktop fonts."""
        font = self.font()
        if font.pointSizeF() > 0:
            font.setPointSizeF(max(10.0, font.pointSizeF() + 1.0))
            self.setFont(font)

    def _set_environment_pending(self) -> None:
        self.operation_combo.setEnabled(False)
        self._set_input_groups_enabled(False)
        self.check_button.setEnabled(False)
        self.execute_button.setEnabled(False)
        self.recheck_button.setEnabled(False)
        self.environment_status.setText("VeraCrypt CLIと安全な標準入力対応を確認中です…")

    def recheck_environment(self) -> None:
        thread = getattr(self, "_probe_thread", None)
        if thread is not None and thread.isRunning():
            return
        self._set_environment_pending()
        self._probe_thread = _ProbeThread(probe_backend, self)
        self._probe_thread.completed.connect(self._probe_finished)
        self._probe_thread.finished.connect(self._probe_thread_finished)
        self._probe_thread.start()

    def _probe_thread_finished(self) -> None:
        thread = self._probe_thread
        self._probe_thread = None
        if thread is not None:
            thread.deleteLater()

    def _probe_finished(self, capability: VeraCryptCapability) -> None:
        self._capability = capability
        executable = str(capability.executable) if capability.executable else "見つかりません"
        version = f"\n版: {capability.version}" if capability.version else ""
        self.environment_status.setText(f"実行ファイル: {executable}{version}\n{capability.detail}")
        self.recheck_button.setEnabled(True)
        model = self.operation_combo.model()
        first_supported = -1
        for index in range(self.operation_combo.count()):
            supported = capability.supports(str(self.operation_combo.itemData(index)))
            item = model.item(index)
            if item is not None:
                item.setEnabled(supported)
            if supported and first_supported < 0:
                first_supported = index
        self.operation_combo.setEnabled(first_supported >= 0)
        if first_supported < 0:
            self.result.setPlainText("この環境ではVeraCryptを安全に操作できません。上の理由を確認してください。")
            self.check_button.setEnabled(False)
            self.execute_button.setEnabled(False)
            return
        self._set_input_groups_enabled(True)
        if not capability.supports(str(self.operation_combo.currentData())):
            self.operation_combo.setCurrentIndex(first_supported)
        self.check_button.setEnabled(True)
        self._operation_changed()

    def _operation_changed(self, *_unused: object) -> None:
        self._clear_secret_fields()
        operation = str(self.operation_combo.currentData())
        self.mount_box.setVisible(operation == "mount")
        self.create_box.setVisible(operation == "create")
        self.dismount_box.setVisible(operation == "dismount")
        self.password_box.setVisible(operation in {"mount", "create"})
        self.password_confirmation.setVisible(operation == "create")
        self.password_box.layout().labelForField(self.password_confirmation).setVisible(operation == "create")
        self.check_button.setText("状態を更新" if operation == "list" else "入力を確認")
        self.execute_button.setText(_OPERATION_LABELS.get(operation, "確認後に実行"))
        self._invalidate("操作に必要な入力を確認してください。")

    def _input_changed(self, *_unused: object) -> None:
        if self._validated is not None:
            self._invalidate("入力が変わりました。もう一度確認してください。")

    def _invalidate(self, message: str) -> None:
        self._validated = None
        self.execute_button.setEnabled(False)
        if self._process is None and self._capability.available:
            self.result.setPlainText(message)

    def check_inputs(self) -> None:
        if self._process is not None:
            return
        operation = str(self.operation_combo.currentData())
        if not self._capability.supports(operation):
            self.result.setPlainText("この環境のVeraCrypt CLIは選択した操作に対応していません。")
            return
        if operation == "mount":
            validation = validate_mount_request(
                self.volume_input.text(), self.mount_point_input.text(), read_only=self.read_only_check.isChecked()
            )
            if not self.password_input.text():
                validation = RequestValidation(None, (*validation.messages, "VeraCryptパスワードを入力してください。"))
        elif operation == "create":
            validation = validate_create_request(
                self.output_input.text(), self.size_input.value(), str(self.filesystem_combo.currentData())
            )
            password = self.password_input.text()
            if not password:
                validation = RequestValidation(None, (*validation.messages, "VeraCryptパスワードを入力してください。"))
            elif password != self.password_confirmation.text():
                validation = RequestValidation(None, (*validation.messages, "確認用パスワードが一致しません。"))
        elif operation == "dismount":
            validation = validate_dismount_request(self.dismount_target_input.text())
        else:
            self._validated = "list"
            self._start_operation("list", list_command(self._capability), secret=None)
            return
        self._validated = validation.request  # type: ignore[assignment]
        self.result.setPlainText("\n".join(validation.messages))
        self.execute_button.setEnabled(validation.is_valid)

    def confirm_execute(self) -> None:
        request = self._validated
        operation = str(self.operation_combo.currentData())
        if request is None or not self._capability.supports(operation):
            self.check_inputs()
            return
        if isinstance(request, VeraMountRequest):
            target = f"コンテナ: {request.volume}\nマウント先: {request.mount_point}"
            command = mount_command(self._capability, request)
        elif isinstance(request, VeraCreateRequest):
            target = f"新規コンテナ: {request.output}\n容量: {request.size_mib} MiB\n内部形式: {request.filesystem}"
            command = create_command(self._capability, request)
        elif isinstance(request, VeraDismountRequest):
            target = f"解除対象: {request.target}"
            command = dismount_command(self._capability, request)
        else:
            return
        if QMessageBox.question(
            self,
            "VeraCrypt操作の最終確認",
            f"{_OPERATION_LABELS[operation]}を実行します。\n\n{target}\n\n"
            "実行直前にもVeraCrypt側で検査し、エラーなら変更を継続しません。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            self._clear_secret_fields()
            self.result.setPlainText("VeraCrypt操作を中止しました。")
            return
        if isinstance(request, VeraMountRequest):
            current = validate_mount_request(
                self.volume_input.text(),
                self.mount_point_input.text(),
                read_only=self.read_only_check.isChecked(),
            )
        elif isinstance(request, VeraCreateRequest):
            current = validate_create_request(
                self.output_input.text(),
                self.size_input.value(),
                str(self.filesystem_combo.currentData()),
            )
        else:
            current = validate_dismount_request(self.dismount_target_input.text())
        if not current.is_valid or current.request != request:
            self._clear_secret_fields()
            self._validated = None
            self.execute_button.setEnabled(False)
            self.result.setPlainText(
                "確認後に対象の状態が変わったため実行しません。もう一度入力を確認してください。\n"
                + "\n".join(current.messages)
            )
            return
        secret = self._take_secret() if operation in {"mount", "create"} else None
        self._start_operation(operation, command, secret=secret)

    def _take_secret(self) -> bytearray:
        secret = bytearray(self.password_input.text().encode("utf-8"))
        secret.append(0x0A)
        self._clear_secret_fields()
        return secret

    def _clear_secret_fields(self) -> None:
        self.password_input.clear()
        self.password_confirmation.clear()
        self.show_password_check.setChecked(False)

    def _leave_screen(self) -> None:
        self._clear_secret_fields()
        self._return_to_hub()

    def _start_operation(
        self,
        operation: str,
        command: tuple[str, tuple[str, ...]],
        *,
        secret: bytearray | None,
    ) -> None:
        program, arguments = command
        process = QProcess(self)
        track_qprocess(process, f"VeraCrypt：{_OPERATION_LABELS[operation]}")
        process.setProgram(program)
        process.setArguments(list(arguments))
        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        process.finished.connect(self._operation_finished)
        process.errorOccurred.connect(self._operation_error)
        self._process = process
        self._process_operation = operation
        try:
            self._activity = begin_runtime_activity(f"VeraCrypt：{_OPERATION_LABELS[operation]}")
        except OSError:
            self._activity = None
        self.operation_combo.setEnabled(False)
        self._set_input_groups_enabled(False)
        self.check_button.setEnabled(False)
        self.execute_button.setEnabled(False)
        self.result.setPlainText("VeraCryptへ操作を依頼しています…")
        process.start()
        if secret is not None:
            process.write(bytes(secret))
            process.closeWriteChannel()
            for index in range(len(secret)):
                secret[index] = 0

    def _finish_activity(self) -> None:
        if self._activity is not None:
            self._activity.close()
            self._activity = None

    def _operation_finished(self, exit_code: int, _exit_status: object) -> None:
        process, operation = self._process, self._process_operation
        if process is None:
            return
        if operation in {"mount", "create"}:
            # A modified third-party build could echo standard input.  Do not
            # decode it and create another immutable Python string.
            process.readAll()
            output = ""
        else:
            output = bytes(process.readAll()).decode(errors="replace")
        self._process = None
        self._process_operation = ""
        process.deleteLater()
        self._finish_activity()
        self.operation_combo.setEnabled(self._capability.available)
        self._set_input_groups_enabled(self._capability.available)
        self.check_button.setEnabled(self._capability.available)
        self._validated = None
        if exit_code == 0:
            if operation == "list":
                self.result.setPlainText(_safe_output(output) or "マウント中のVeraCryptボリュームはありません。")
            else:
                self.result.setPlainText(f"{_OPERATION_LABELS[operation]}が完了しました。状態確認で結果を確認できます。")
        else:
            # Secret-bearing operations never expose backend output.  Some
            # third-party builds may echo prompts or input unexpectedly.
            if operation in {"mount", "create"}:
                detail = "パスワード、権限、形式、マウント先、必要なOS機能を確認してください。"
            else:
                detail = _safe_output(output) or "VeraCryptから詳細が返されませんでした。"
            self.result.setPlainText(f"{_OPERATION_LABELS[operation]}に失敗しました（終了コード {exit_code}）。\n{detail}")

    def _operation_error(self, _error: QProcess.ProcessError) -> None:
        process, operation = self._process, self._process_operation
        if process is None:
            return
        detail = process.errorString()
        process.readAll()
        self._process = None
        self._process_operation = ""
        process.deleteLater()
        self._finish_activity()
        self.operation_combo.setEnabled(self._capability.available)
        self._set_input_groups_enabled(self._capability.available)
        self.check_button.setEnabled(self._capability.available)
        self._validated = None
        self.result.setPlainText(f"{_OPERATION_LABELS.get(operation, 'VeraCrypt操作')}を開始できませんでした。\n{detail}")

    def _show_password(self, visible: bool) -> None:
        mode = QLineEdit.EchoMode.Normal if visible else QLineEdit.EchoMode.Password
        self.password_input.setEchoMode(mode)
        self.password_confirmation.setEchoMode(mode)

    def _set_input_groups_enabled(self, enabled: bool) -> None:
        self.mount_box.setEnabled(enabled)
        self.create_box.setEnabled(enabled)
        self.dismount_box.setEnabled(enabled)
        self.password_box.setEnabled(enabled)

    def describe_work_state(self):
        if self._process is not None:
            return {"level": 4, "reason": f"VeraCryptの{_OPERATION_LABELS.get(self._process_operation, '操作')}を実行中です。"}
        if self._validated is not None or any((self.volume_input.text(), self.output_input.text(), self.dismount_target_input.text())):
            return {"level": 3, "reason": "VeraCryptの入力または確認済み操作を保持しています。"}
        if not self._capability.available:
            return {"level": 1, "reason": "VeraCryptバックエンドを利用できません。"}
        return {"level": 2, "reason": "VeraCryptの操作を選択しています。"}

    def shutdown(self) -> None:
        self._clear_secret_fields()
        thread = getattr(self, "_probe_thread", None)
        if thread is not None:
            try:
                if thread.isRunning():
                    thread.wait(10_000)
            except RuntimeError:
                self._probe_thread = None


def _safe_output(value: str) -> str:
    """Keep diagnostics readable while removing terminal control sequences."""
    cleaned = "".join(character for character in value if character in "\n\t" or ord(character) >= 32)
    return cleaned.strip()[:32_000]
