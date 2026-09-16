"""The encryption hub exposes only operations implemented by each backend."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from apps.system_tools.storage_encryption import EncryptionHubScreen
from apps.system_tools.storage_encryption.hub import ArchiveProtectionScreen, PlannedBackendScreen
from apps.system_tools.storage_encryption.veracrypt_window import VeraCryptScreen


def test_hub_offers_backends_and_only_marks_integrated_ones_as_available():
    QApplication.instance() or QApplication([])
    screen = EncryptionHubScreen(lambda: None)
    try:
        assert [screen.backend_combo.itemData(index) for index in range(screen.backend_combo.count())] == [
            "", "luks", "veracrypt", "cryptomator", "7z", "zip", "rar",
        ]
        screen.open_backend("veracrypt")
        assert isinstance(screen._stack.currentWidget(), VeraCryptScreen)
        assert screen.minimumSize().height() >= screen._stack.currentWidget().minimumSizeHint().height()
        assert screen.describe_work_state()["level"] in (1, 2)
        screen.open_backend("cryptomator")
        assert isinstance(screen._stack.currentWidget(), PlannedBackendScreen)
        screen.open_backend("luks")
        assert screen._stack.currentWidget().__class__.__name__ == "StorageEncryptionScreen"
    finally:
        screen.close()


def test_archive_screens_offer_matching_creation_and_extraction_paths(tmp_path):
    QApplication.instance() or QApplication([])
    source = tmp_path / "source.txt"
    source.write_text("contents", encoding="utf-8")
    archive = tmp_path / "existing.7z"
    archive.write_bytes(b"not opened by this test")
    screen = ArchiveProtectionScreen("7z", lambda: None)
    try:
        screen.paths.setPlainText(str(source))
        assert screen.operation_combo.currentData() == "compress"
        assert screen.open_button.isEnabled()
        assert "暗号化・圧縮" in screen.open_button.text()

        screen.operation_combo.setCurrentIndex(1)
        assert not screen.open_button.isEnabled()
        screen.paths.setPlainText(str(archive))
        assert screen.open_button.isEnabled()
        assert "解凍・復号" in screen.open_button.text()
    finally:
        screen.close()


def test_rar_is_explicitly_extraction_only(tmp_path):
    QApplication.instance() or QApplication([])
    archive = tmp_path / "archive.rar"
    archive.write_bytes(b"not opened by this test")
    screen = ArchiveProtectionScreen("rar", lambda: None)
    try:
        assert screen.operation_combo.count() == 1
        assert screen.operation_combo.currentData() == "extract"
        screen.paths.setPlainText(str(archive))
        assert screen.open_button.isEnabled()
        assert "解凍・復号" in screen.open_button.text()
    finally:
        screen.close()
