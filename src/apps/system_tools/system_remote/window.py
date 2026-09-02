"""A cautious GUI front end for broadly available Linux desktop requests."""

from __future__ import annotations

from collections.abc import Callable
import sys
from pathlib import Path
import shutil
import subprocess

from PySide6.QtCore import QProcess, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QWidget,
)

from foundation.linux_system import ActionAvailability, SystemAction, action_availability, system_status_lines
from foundation.process_control import active_main_operations, running_main_processes
from foundation.product import PRODUCT_NAME
from gui import AppHeader, AppPageLayout


PROJECT_ROOT = Path(__file__).resolve().parents[4]
OPEN_SPACE_DIR = PROJECT_ROOT.parent
CORE_MENU_PATH = PROJECT_ROOT / "core" / "main_menu.sh"


def open_core_menu_in_terminal() -> bool:
    """Open the fallback CORE menu without letting it relaunch this GUI."""
    if not CORE_MENU_PATH.is_file():
        return False
    for program, prefix in (
        ("x-terminal-emulator", ("-e",)),
        ("gnome-terminal", ("--",)),
        ("konsole", ("-e",)),
    ):
        if not shutil.which(program):
            continue
        try:
            subprocess.Popen([program, *prefix, "bash", str(CORE_MENU_PATH), "--menu-only"])
        except OSError:
            continue
        return True
    return False


class SystemRemoteScreen(QWidget):
    """Expose safe, explicit desktop actions without saving any state."""

    def __init__(self, return_to_main: Callable[[], None]) -> None:
        super().__init__()
        self._return_to_main = return_to_main
        self._process: QProcess | None = None
        self._close_instances_process: QProcess | None = None
        self._action_buttons: dict[str, QPushButton] = {}
        self.setMinimumSize(720, 560)
        self._build_ui()
        self.refresh_status()

    def _build_ui(self) -> None:
        layout = AppPageLayout(self)
        header = AppHeader(self._return_to_main, title="システム操作")
        header.content_layout.addStretch(1)
        refresh_button = QPushButton("状態を更新")
        refresh_button.clicked.connect(self.refresh_status)
        header.content_layout.addWidget(refresh_button)
        layout.addWidget(header)
        layout.addWidget(
            QLabel(
                "状態を保存せず、明示したOS操作だけを実行します。"
                "対応を安全に確認できない操作は実行しません。"
            )
        )

        daily_box = QGroupBox("日常・保険")
        daily_layout = QHBoxLayout(daily_box)
        open_workspace = QPushButton("作業場所を開く")
        open_workspace.setToolTip(f"{PRODUCT_NAME} の作業場所を標準ファイルマネージャーで開きます。")
        open_workspace.clicked.connect(self.open_workspace)
        daily_layout.addWidget(open_workspace)
        core_button = QPushButton("PORTA Coreを開く")
        core_button.setToolTip("Python側が使いにくい場合の、低依存な端末メニューを別端末で開きます。")
        core_button.clicked.connect(self.open_core_menu)
        daily_layout.addWidget(core_button)
        close_porta_place = QPushButton(f"{PRODUCT_NAME} を問答無用で全終了")
        close_porta_place.setToolTip(
            f"この作業場所の {PRODUCT_NAME} をすべて終了します。実行中の作業も中断します。"
        )
        close_porta_place.clicked.connect(self.close_all_porta_place_instances)
        daily_layout.addWidget(close_porta_place)
        layout.addWidget(daily_box)

        session_box = QGroupBox("セッション")
        session_layout = QGridLayout(session_box)
        self._add_action_button(session_layout, "lock", 0, 0)
        self._add_action_button(session_layout, "logout", 0, 1)
        layout.addWidget(session_box)

        power_box = QGroupBox("電源")
        power_layout = QGridLayout(power_box)
        for column, key in enumerate(("suspend", "hibernate", "reboot", "poweroff")):
            self._add_action_button(power_layout, key, 0, column)
        layout.addWidget(power_box)

        self.status = QPlainTextEdit()
        self.status.setReadOnly(True)
        self.status.setFixedHeight(110)
        self.status.setPlaceholderText("状態と実行結果をここに表示します。選択してコピーできます。")
        layout.addWidget(self.status)

    def _add_action_button(self, layout: QGridLayout, key: str, row: int, column: int) -> None:
        title = action_availability(key).action
        button = QPushButton(title.title if title else _action_title(key))
        button.clicked.connect(lambda _checked=False, action_key=key: self.request_action(action_key))
        layout.addWidget(button, row, column)
        self._action_buttons[key] = button

    def refresh_status(self) -> None:
        lines = list(system_status_lines())
        main_script = PROJECT_ROOT / "scripts" / "main.py"
        lines.append(f"{PRODUCT_NAME} 起動数: {len(running_main_processes(main_script))} 件")
        active = active_main_operations(main_script)
        if active:
            lines.append(
                f"{PRODUCT_NAME} 実行中: " + " / ".join(
                    f"PID {activity.pid}: {activity.label}" for activity in active
                )
            )
        else:
            lines.append(f"{PRODUCT_NAME} 実行中: なし")
        for key, button in self._action_buttons.items():
            availability = action_availability(key)
            button.setEnabled(availability.available)
            button.setToolTip(
                availability.action.description if availability.action else availability.reason
            )
            lines.append(f"{_action_title(key)}: " + ("利用可能" if availability.available else availability.reason))
        self.status.setPlainText("\n".join(lines))

    def request_action(self, key: str) -> None:
        availability = action_availability(key)
        if not availability.available:
            self.status.setPlainText(f"{_action_title(key)} は実行しません。\n{availability.reason}")
            return
        action = availability.action
        assert action is not None
        answer = QMessageBox.question(
            self,
            action.title,
            action.confirmation + "\n\nOS側の認証画面が出た場合は、その指示に従ってください。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            self.status.setPlainText(f"{action.title}を中止しました。")
            return
        self._start_action(action)

    def _start_action(self, action: SystemAction) -> None:
        if self._process is not None:
            self.status.setPlainText("別のOS操作を実行中です。完了を待ってください。")
            return
        process = QProcess(self)
        process.setProgram(action.program)
        process.setArguments(list(action.arguments))
        process.finished.connect(
            lambda exit_code, _exit_status, requested=action: self._finish_action(requested, exit_code)
        )
        process.errorOccurred.connect(lambda _error, requested=action: self._report_process_error(requested))
        self._process = process
        self.status.setPlainText(f"{action.title}をOSへ要求しています。")
        process.start()

    def _finish_action(self, action: SystemAction, exit_code: int) -> None:
        process = self._process
        self._process = None
        detail = ""
        if process is not None:
            detail = bytes(process.readAllStandardError()).decode(errors="replace").strip()
        if exit_code == 0:
            self.status.setPlainText(f"{action.title}をOSへ要求しました。")
        else:
            self.status.setPlainText(
                f"{action.title}をOSへ要求できませんでした。" + (f"\n{detail}" if detail else "")
            )

    def _report_process_error(self, action: SystemAction) -> None:
        if self._process is None:
            return
        detail = self._process.errorString()
        self.status.setPlainText(f"{action.title}を開始できませんでした。\n{detail}")

    def open_workspace(self) -> None:
        if QDesktopServices.openUrl(QUrl.fromLocalFile(str(OPEN_SPACE_DIR))):
            self.status.setPlainText("作業場所を標準ファイルマネージャーで開く要求を送りました。")
        else:
            self.status.setPlainText("作業場所を標準ファイルマネージャーで開けませんでした。")

    def open_core_menu(self) -> None:
        if not CORE_MENU_PATH.is_file():
            self.status.setPlainText("PORTA Coreが見つかりません。")
            return
        if open_core_menu_in_terminal():
            self.status.setPlainText("PORTA Coreを別の端末で開きました。")
            return
        self.status.setPlainText("利用可能な端末アプリが見つからないため、PORTA Coreを開けませんでした。")

    def close_all_porta_place_instances(self) -> None:
        """Use a short helper so this screen can close itself after a final check."""
        main_script = PROJECT_ROOT / "scripts" / "main.py"
        if self._close_instances_process is not None:
            self.status.setPlainText(f"{PRODUCT_NAME} の終了可否を確認中です。")
            return
        instances = running_main_processes(main_script)
        if not instances:
            self.status.setPlainText(f"終了できる {PRODUCT_NAME} は見つかりませんでした。")
            return
        answer = QMessageBox.question(
            self,
            f"{PRODUCT_NAME} を問答無用で全終了",
            f"この作業場所の {PRODUCT_NAME} を {len(instances)} 件、すべて終了します。\n\n"
            "この画面自身も閉じます。実行中のファイル操作・ダウンロード・動画変換も中断します。\n"
            "保存されていない画面上の内容は失われるため、本当に全終了するときだけ実行してください。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            self.status.setPlainText(f"{PRODUCT_NAME} の全終了を中止しました。")
            return
        process = QProcess(self)
        process.setProgram(sys.executable)
        process.setArguments([str(main_script), "--close-instances"])
        process.setWorkingDirectory(str(PROJECT_ROOT))
        process.finished.connect(self._finish_close_all_porta_place_instances)
        process.errorOccurred.connect(self._report_close_all_porta_place_error)
        self._close_instances_process = process
        self.status.setPlainText(f"{PRODUCT_NAME} を問答無用で全終了しています。")
        process.start()

    def _finish_close_all_porta_place_instances(self, exit_code: int, _status: object) -> None:
        process = self._close_instances_process
        self._close_instances_process = None
        detail = ""
        if process is not None:
            detail = (
                bytes(process.readAllStandardOutput()).decode(errors="replace")
                + bytes(process.readAllStandardError()).decode(errors="replace")
            ).strip()
            process.deleteLater()
        if exit_code != 0:
            self.status.setPlainText(
                f"{PRODUCT_NAME} の全終了を開始できませんでした。"
                + (f"\n{detail}" if detail else "")
            )

    def _report_close_all_porta_place_error(self, _error: object) -> None:
        process = self._close_instances_process
        self._close_instances_process = None
        detail = process.errorString() if process is not None else ""
        self.status.setPlainText(f"{PRODUCT_NAME} の全終了を開始できませんでした。\n" + detail)


def _action_title(key: str) -> str:
    availability: ActionAvailability = action_availability(key)
    return availability.action.title if availability.action else {
        "lock": "画面をロック",
        "suspend": "スリープ",
        "hibernate": "ハイバネート",
        "logout": "ログアウト",
        "reboot": "再起動",
        "poweroff": "シャットダウン",
    }[key]


def create_screen(return_to_main: Callable[[], None]) -> SystemRemoteScreen:
    """Factory used by the central launcher catalog."""
    return SystemRemoteScreen(return_to_main)
