"""Small shared editor for the explicit persistent-settings location."""

from __future__ import annotations

from settings.persistent_settings import bootstrap_editable_text, bootstrap_template_text, locate_settings_directory, save_bootstrap_text, validate_bootstrap_text
from collections.abc import Callable

from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QMessageBox, QVBoxLayout, QWidget

from .json_settings_editor import JsonFieldSpec, JsonSettingsEditor
from .layout_policy import preferred_window_size


def show_settings_location_editor(
    parent: QWidget,
    *,
    allow_save: Callable[[], bool] | None = None,
) -> None:
    dialog = QDialog(parent)
    dialog.setWindowTitle("永続設定の保存先入口")
    dialog.resize(preferred_window_size(dialog))
    layout = QVBoxLayout(dialog)
    explanation = QLabel(
        "PORTA直下のJSON入口設定には、ユーザー領域のパスを1つだけ保存します。"
        "@PORTA、@HOME、相対パス、絶対パスを使えます。設定は常に、そのユーザー領域の config/ から読みます。"
    )
    explanation.setWordWrap(True)
    layout.addWidget(explanation)
    editor = JsonSettingsEditor(
        validate=validate_bootstrap_text,
        path_keys={"user_root"},
        fields={
            "user_root": JsonFieldSpec(
                "porta_userの場所",
                "PORTAが設定・AI・辞書などを探すユーザー領域です。@PORTA、@HOME、相対パス、絶対パスを使えます。",
            )
        },
    )
    original_text = bootstrap_editable_text()
    editor.setPlainText(original_text)
    source = locate_settings_directory()
    # This editor establishes the user-root location itself, so it must stay
    # editable even when that location is missing or invalid.
    editor.set_source_state(source.state, source.detail, lock_unavailable=False)
    layout.addWidget(editor, 1)
    buttons = QDialogButtonBox()
    template = buttons.addButton("雛形へ戻す", QDialogButtonBox.ButtonRole.ResetRole)
    editor.bind_edit_button(template)
    save = buttons.addButton("入口を保存", QDialogButtonBox.ButtonRole.AcceptRole)
    editor.bind_save_button(save)
    close = buttons.addButton("閉じる", QDialogButtonBox.ButtonRole.RejectRole)
    template.clicked.connect(lambda: editor.setPlainText(bootstrap_template_text()))

    def save_entry() -> None:
        if allow_save is not None and not allow_save():
            return
        if bootstrap_editable_text() != original_text:
            QMessageBox.warning(
                dialog,
                "入口を保存できません",
                "この画面を開いた後に、別の画面で保存先入口が変更されました。"
                "現在の入力は保存していません。画面を閉じて、最新の設定から開き直してください。",
            )
            return
        try:
            save_bootstrap_text(editor.toPlainText())
        except ValueError as exc:
            QMessageBox.warning(dialog, "入口を保存できません", str(exc))
            return
        dialog.accept()

    save.clicked.connect(save_entry)
    close.clicked.connect(dialog.reject)
    layout.addWidget(buttons)
    dialog.exec()
