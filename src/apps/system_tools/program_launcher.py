"""Shared UI for configured non-Python foundation and external programs."""

from __future__ import annotations

from collections.abc import Callable

from foundation.shared_launchers import LauncherLocations
from gui import AppHeader, AppPageLayout
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGroupBox,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from apps.system_tools.external_app_launcher.discovery import (
    ApplicationLocation,
    DiscoveredApplication,
    launch,
    scan_location,
)


class ConfiguredProgramLauncherScreen(QWidget):
    """Discover ``start.sh`` programs from one low-dependency location file."""

    def __init__(
        self,
        return_to_main: Callable[[], None],
        *,
        title: str,
        explanation: str,
        settings_title: str,
        settings_warning: str,
        load_locations: Callable[[], LauncherLocations],
        editable_text: Callable[[], str],
        template_text: Callable[[], str],
        save_text: Callable[[str], object],
    ) -> None:
        super().__init__()
        self._title = title
        self._settings_title = settings_title
        self._settings_warning = settings_warning
        self._load_locations = load_locations
        self._editable_text = editable_text
        self._template_text = template_text
        self._save_text = save_text
        self._settings_dialog: QDialog | None = None
        self._settings_editor: QTextEdit | None = None

        layout = AppPageLayout(self)
        header = AppHeader(return_to_main, title=title, on_settings=self.show_settings)
        header.content_layout.addStretch(1)
        reload_button = QPushButton("再読み込み")
        reload_button.clicked.connect(self.reload_programs)
        header.content_layout.addWidget(reload_button)
        layout.addWidget(header)
        description = QLabel(explanation)
        description.setWordWrap(True)
        layout.addWidget(description)
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        box = QGroupBox(f"利用できる{title}")
        box_layout = QVBoxLayout(box)
        box_layout.setContentsMargins(8, 10, 8, 8)
        content = QWidget()
        content.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._programs_layout = QVBoxLayout(content)
        self._programs_layout.setContentsMargins(8, 8, 8, 8)
        self._programs_layout.setSpacing(6)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(content)
        box_layout.addWidget(scroll)
        layout.addWidget(box, 1)
        self.reload_programs()

    def reload_programs(self) -> None:
        while self._programs_layout.count():
            item = self._programs_layout.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        configured = self._load_locations()
        if configured.state != "ready":
            self.status.setText(
                f"{self._title}を読み込めません: {configured.detail}\n"
                "設定がなくてもPORTA本体には影響しません。Coreも引き続き起動できます。"
            )
            self._add_message("利用する場合は永続設定から置き場を登録してください。")
            return
        scans = tuple(
            scan_location(ApplicationLocation(number, path.name or str(path), path))
            for number, path in enumerate(configured.directories, start=1)
        )
        applications = tuple(app for scan in scans for app in scan.applications)
        self.status.setText(
            f"{configured.detail} {len(applications)}件のプログラムを見つけました。"
        )
        if not scans:
            self._add_message("置き場はまだ登録されていません。永続設定から追加できます。")
            return
        for scan in scans:
            panel = QFrame()
            panel.setFrameShape(QFrame.Shape.StyledPanel)
            panel.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
            panel_layout = QVBoxLayout(panel)
            panel_layout.setContentsMargins(10, 8, 10, 10)
            title = QLabel(f"{scan.location.number}. {scan.location.display_name}")
            title.setStyleSheet("font-weight: 600;")
            title.setWordWrap(True)
            panel_layout.addWidget(title)
            detail = QLabel(scan.detail)
            detail.setWordWrap(True)
            panel_layout.addWidget(detail)
            for application in scan.applications:
                button = QPushButton(application.name)
                button.setMinimumHeight(28)
                button.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
                button.setToolTip(f"起動: {application.launcher_path}")
                button.clicked.connect(
                    lambda _checked=False, target=application: self.launch_program(target)
                )
                panel_layout.addWidget(button)
            self._programs_layout.addWidget(panel)
        self._programs_layout.addStretch(1)

    def _add_message(self, text: str) -> None:
        message = QLabel(text)
        message.setWordWrap(True)
        self._programs_layout.addWidget(message)
        self._programs_layout.addStretch(1)

    def launch_program(self, application: DiscoveredApplication) -> None:
        try:
            launch(application)
        except OSError as exc:
            QMessageBox.warning(self, "起動できません", str(exc))
            return
        self.status.setText(f"起動要求を送りました: {application.name}")

    def show_settings(self) -> None:
        if self._settings_dialog is None:
            dialog = QDialog(self)
            dialog.setWindowTitle(self._settings_title)
            dialog.setMinimumSize(720, 420)
            layout = QVBoxLayout(dialog)
            warning = QLabel(self._settings_warning)
            warning.setWordWrap(True)
            layout.addWidget(warning)
            editor = QTextEdit()
            layout.addWidget(editor, 1)
            buttons = QDialogButtonBox()
            template = buttons.addButton("雛形へ戻す", QDialogButtonBox.ButtonRole.ResetRole)
            save = buttons.addButton("保存", QDialogButtonBox.ButtonRole.AcceptRole)
            close = buttons.addButton(QDialogButtonBox.StandardButton.Close)
            template.clicked.connect(lambda: editor.setPlainText(self._template_text()))
            save.clicked.connect(self.save_settings)
            close.clicked.connect(dialog.close)
            layout.addWidget(buttons)
            self._settings_dialog = dialog
            self._settings_editor = editor
        assert self._settings_editor is not None
        self._settings_editor.setPlainText(self._editable_text())
        self._settings_dialog.show()
        self._settings_dialog.raise_()
        self._settings_dialog.activateWindow()

    def save_settings(self) -> None:
        if self._settings_editor is None:
            return
        try:
            self._save_text(self._settings_editor.toPlainText())
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "設定を保存できません", str(exc))
            return
        if self._settings_dialog is not None:
            self._settings_dialog.close()
        self.reload_programs()
