"""VeraCrypt commands must remain capability-gated and secret-free."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

from apps.system_tools.storage_encryption.veracrypt import (
    VeraCryptCapability,
    VeraCreateRequest,
    VeraDismountRequest,
    create_command,
    dismount_command,
    mount_command,
    probe_backend,
    validate_create_request,
    validate_mount_request,
)
from apps.system_tools.storage_encryption.veracrypt_window import VeraCryptScreen


def _capability(*operations: str) -> VeraCryptCapability:
    return VeraCryptCapability(Path("/usr/bin/veracrypt"), "VeraCrypt test", frozenset(operations), "利用可能")


def test_probe_refuses_a_cli_without_stdin_security_support(tmp_path):
    executable = tmp_path / "veracrypt"
    executable.write_text("", encoding="utf-8")
    executable.chmod(0o700)

    def runner(arguments, **_kwargs):
        return subprocess.CompletedProcess(arguments, 0, "--text --non-interactive --mount --create --list --dismount")

    result = probe_backend(executable=executable, runner=runner)

    assert not result.available
    assert "--stdin" in result.detail


def test_probe_reports_operations_individually(tmp_path):
    executable = tmp_path / "veracrypt"
    executable.write_text("", encoding="utf-8")
    executable.chmod(0o700)

    def runner(arguments, **_kwargs):
        output = "VeraCrypt test" if "--version" in arguments else "--text --non-interactive --stdin --mount --list"
        return subprocess.CompletedProcess(arguments, 0, output)

    result = probe_backend(executable=executable, runner=runner)

    assert result.available
    assert result.operations == frozenset({"mount", "list"})
    assert not result.supports("create")


def test_probe_prefers_current_unmount_spelling(tmp_path):
    executable = tmp_path / "veracrypt"
    executable.write_text("", encoding="utf-8")
    executable.chmod(0o700)

    def runner(arguments, **_kwargs):
        output = "VeraCrypt test" if "--version" in arguments else "--text --non-interactive --stdin --unmount"
        return subprocess.CompletedProcess(arguments, 0, output)

    capability = probe_backend(executable=executable, runner=runner)
    _program, arguments = dismount_command(
        capability, VeraDismountRequest(tmp_path / "mounted-volume")
    )

    assert capability.supports("dismount")
    assert "--unmount" in arguments
    assert "--dismount" not in arguments


def test_mount_and_create_commands_never_contain_a_password(tmp_path):
    volume = tmp_path / "volume.hc"
    volume.write_bytes(b"container")
    mount_point = tmp_path / "mount"
    mount_point.mkdir()
    capability = _capability("mount", "create")
    mount = validate_mount_request(str(volume), str(mount_point), read_only=True)
    assert mount.is_valid
    mount_program, mount_arguments = mount_command(capability, mount.request)
    create_program, create_arguments = create_command(
        capability, VeraCreateRequest(tmp_path / "new.hc", 100, "ext4")
    )

    assert mount_program == create_program == "/usr/bin/veracrypt"
    assert "--stdin" in mount_arguments and "--stdin" in create_arguments
    assert "--mount-options=ro" in mount_arguments
    assert "secret-value" not in "\0".join((*mount_arguments, *create_arguments))
    assert not any(argument.startswith("--password") for argument in (*mount_arguments, *create_arguments))


def test_create_validation_refuses_existing_output(tmp_path):
    output = tmp_path / "existing.hc"
    output.write_bytes(b"keep")

    result = validate_create_request(str(output), 100, "ext4")

    assert not result.is_valid
    assert any("置き換えません" in message for message in result.messages)
    assert output.read_bytes() == b"keep"


def test_unavailable_backend_is_explained_and_disables_execution():
    QApplication.instance() or QApplication([])
    unavailable = VeraCryptCapability(None, "", frozenset(), "テスト用：CLIなし")
    screen = VeraCryptScreen(lambda: None, probe=lambda: unavailable)
    try:
        assert screen._probe_thread.wait(3000)
        QTest.qWait(20)
        assert "CLIなし" in screen.environment_status.text()
        assert not screen.operation_combo.isEnabled()
        assert not screen.password_box.isEnabled()
        assert not screen.execute_button.isEnabled()
    finally:
        screen.shutdown()
        screen.close()


def test_secret_bearing_backend_output_is_never_shown(tmp_path):
    QApplication.instance() or QApplication([])
    backend = tmp_path / "echo-secret"
    backend.write_text(
        "#!/bin/sh\nIFS= read -r secret\nprintf '%s\\n' \"$secret\" >&2\nexit 2\n",
        encoding="utf-8",
    )
    backend.chmod(0o700)
    capability = VeraCryptCapability(backend, "test", frozenset({"mount"}), "利用可能")
    screen = VeraCryptScreen(lambda: None, probe=lambda: capability)
    try:
        assert screen._probe_thread.wait(3000)
        QTest.qWait(20)
        secret = bytearray(b"do-not-display-this\n")
        screen._start_operation("mount", (str(backend), ()), secret=secret)
        for _attempt in range(100):
            if screen._process is None:
                break
            QTest.qWait(20)

        assert screen._process is None
        assert "do-not-display-this" not in screen.result.toPlainText()
        assert secret == bytearray(len(secret))
        assert "失敗しました" in screen.result.toPlainText()
    finally:
        screen.shutdown()
        screen.close()


def test_create_rechecks_the_output_after_final_confirmation(tmp_path, monkeypatch):
    QApplication.instance() or QApplication([])
    capability = VeraCryptCapability(
        Path("/usr/bin/veracrypt"), "test", frozenset({"create"}), "利用可能"
    )
    output = tmp_path / "new-volume.hc"
    screen = VeraCryptScreen(lambda: None, probe=lambda: capability)
    started: list[object] = []
    try:
        assert screen._probe_thread.wait(3000)
        QTest.qWait(20)
        screen.operation_combo.setCurrentIndex(screen.operation_combo.findData("create"))
        screen.output_input.setText(str(output))
        screen.size_input.setValue(10)
        screen.password_input.setText("temporary-secret")
        screen.password_confirmation.setText("temporary-secret")
        screen.check_inputs()
        assert screen.execute_button.isEnabled()

        def replace_target_during_confirmation(*_args, **_kwargs):
            output.write_bytes(b"pre-existing")
            return QMessageBox.StandardButton.Yes

        monkeypatch.setattr(QMessageBox, "question", replace_target_during_confirmation)
        monkeypatch.setattr(screen, "_start_operation", lambda *_args, **_kwargs: started.append(True))
        screen.confirm_execute()

        assert not started
        assert output.read_bytes() == b"pre-existing"
        assert "状態が変わったため実行しません" in screen.result.toPlainText()
    finally:
        screen.shutdown()
        screen.close()
