"""Small shared editor for the explicit persistent-settings location."""

from __future__ import annotations

from collections.abc import Callable

from foundation.persistent_settings import (
    DEFAULT_USER_ROOT,
    configured_user_root_text,
    save_user_root_path,
)
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QLineEdit, QMessageBox, QVBoxLayout, QWidget


def show_settings_location_editor(parent: QWidget) -> None:
    dialog = QDialog(parent)
    dialog.setWindowTitle("永続設定の保存先入口")
    dialog.setMinimumSize(640, 180)
    layout = QVBoxLayout(dialog)
    layout.addWidget(
        QLabel(
            "PORTA直下の案内札には、ユーザー領域の絶対パスまたは案内札基準の相対パスを"
            "1つだけ保存します。設定は常に、そのユーザー領域の config/ から読みます。"
        )
    )
    editor = QLineEdit(configured_user_root_text() or DEFAULT_USER_ROOT)
    editor.setPlaceholderText("例: porta_user / ../porta_user / /任意の場所/porta_user")
    layout.addWidget(editor)
    buttons = QDialogButtonBox()
    template = buttons.addButton("雛形へ戻す", QDialogButtonBox.ButtonRole.ResetRole)
    save = buttons.addButton("入口を保存", QDialogButtonBox.ButtonRole.AcceptRole)
    close = buttons.addButton("閉じる", QDialogButtonBox.ButtonRole.RejectRole)
    template.clicked.connect(lambda: editor.setText(DEFAULT_USER_ROOT))

    def save_entry() -> None:
        try:
            save_user_root_path(editor.text())
        except ValueError as exc:
            QMessageBox.warning(dialog, "入口を保存できません", str(exc))
            return
        dialog.accept()

    save.clicked.connect(save_entry)
    close.clicked.connect(dialog.reject)
    layout.addWidget(buttons)
    dialog.exec()


def create_app_settings_file(parent: QWidget, create: Callable[[], object]) -> None:
    """Run only an explicit setup action and turn setup failures into UI text."""
    try:
        create()
    except (OSError, ValueError) as exc:
        QMessageBox.warning(parent, "設定を作成できません", str(exc))
        return
    QMessageBox.information(parent, "設定を作成しました", "設定保存先フォルダと、このアプリの雛形設定を確認しました。")
