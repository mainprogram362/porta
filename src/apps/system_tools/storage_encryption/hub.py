"""One entry point for encrypted volumes, vaults, and protected archives.

Backends retain their own safety checks.  This screen only chooses a backend
and transfers a frozen path list to an already-tested archive workflow.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from apps.file_tools.file_manager.quick_compress_dialog import QuickCompressDialog
from apps.file_tools.file_manager.quick_extract_dialog import QuickExtractDialog
from gui import AppHeader, AppPageLayout, NoWheelComboBox, add_path_list_input

from .window import StorageEncryptionScreen
from .veracrypt_window import VeraCryptScreen


_BACKENDS = (
    ("luks", "LUKS", "Linuxの暗号化コンテナ・暗号化ディスクを開いてマウントします。"),
    ("veracrypt", "VeraCrypt", "CLI環境を確認して、コンテナの作成・マウント・状態確認・解除を行います。"),
    ("cryptomator", "Cryptomator", "暗号化保管庫の作成・開閉は、対応準備中です。"),
    ("7z", "7z", "ファイル名も隠せる暗号化アーカイブを作成・解凍します。"),
    ("zip", "ZIP", "互換性を優先した暗号化ZIPを作成・解凍します。"),
    ("rar", "RAR（解凍のみ）", "RARアーカイブを確認・解凍します。作成は行いません。"),
)
_BACKEND_TITLES = {key: title for key, title, _description in _BACKENDS}


def _use_readable_font(widget: QWidget) -> None:
    """Give the security workflows a calm, legible baseline at half width."""
    font = widget.font()
    if font.pointSizeF() > 0:
        font.setPointSizeF(max(10.0, font.pointSizeF() + 1.0))
        widget.setFont(font)


class ArchiveProtectionScreen(QWidget):
    """Choose files, then open the established ZIP/7z/RAR operation window."""

    def __init__(self, backend: str, return_to_hub: Callable[[], None]) -> None:
        super().__init__()
        _use_readable_font(self)
        if backend not in {"7z", "zip", "rar"}:
            raise ValueError(f"未対応のアーカイブ方式です: {backend}")
        self.backend = backend
        self._return_to_hub = return_to_hub
        self._dialogs: set[QWidget] = set()

        layout = AppPageLayout(self)
        layout.addWidget(AppHeader(return_to_hub, title=f"{_BACKEND_TITLES[backend]}：保護ファイル"))
        description = QLabel(self._description())
        description.setWordWrap(True)
        layout.addWidget(description)

        operation_box = QGroupBox("操作")
        operation_layout = QHBoxLayout(operation_box)
        self.operation_combo = NoWheelComboBox()
        if backend == "rar":
            self.operation_combo.addItem("解凍・復号", "extract")
            self.operation_combo.setToolTip("RARの新規作成・暗号化はPORTAでは行いません。")
        else:
            self.operation_combo.addItem("圧縮・暗号化", "compress")
            self.operation_combo.addItem("解凍・復号", "extract")
        self.operation_combo.currentIndexChanged.connect(self._update_action)
        operation_layout.addWidget(self.operation_combo)
        operation_layout.addStretch(1)
        layout.addWidget(operation_box)

        source_box = QGroupBox("対象")
        source_layout = QVBoxLayout(source_box)
        self.paths = add_path_list_input(
            source_layout,
            rows=5,
            placeholder="ファイル・フォルダを入力またはドロップします。ここでは実行しません。",
            drop_replaces=True,
            show_controls=True,
            show_operation_targets=False,
            context_menu_operation_target_actions=False,
        )
        self.paths.show_full_paths()
        self.paths.textChanged.connect(self._update_action)
        layout.addWidget(source_box, 1)

        self.notice = QLabel()
        self.notice.setWordWrap(True)
        layout.addWidget(self.notice)
        controls = QHBoxLayout()
        self.open_button = QPushButton()
        self.open_button.clicked.connect(self.open_operation)
        controls.addWidget(self.open_button)
        controls.addStretch(1)
        layout.addLayout(controls)
        self._update_action()

    def _description(self) -> str:
        if self.backend == "7z":
            return (
                "7zではパスワード付き圧縮とファイル名・フォルダ構成の暗号化を使えます。"
                "パスワードはこの画面にも一覧にも保存しません。"
            )
        if self.backend == "zip":
            return (
                "ZIPは他の環境へ渡しやすい形式です。パスワード付きにできますが、"
                "ファイル名とフォルダ構成は隠せません。"
            )
        return "RARは同梱7-Zipで中身を確認・解凍できます。新規作成・再圧縮は行いません。"

    def _selected_paths(self) -> tuple[Path, ...]:
        return tuple(self.paths.paths(deduplicate=True))

    def _matching_archives(self, paths: tuple[Path, ...]) -> tuple[Path, ...]:
        suffix = "." + self.backend
        return tuple(path for path in paths if path.is_file() and path.name.lower().endswith(suffix))

    def _update_action(self, *_unused: object) -> None:
        operation = self.operation_combo.currentData()
        paths = self._selected_paths()
        if operation == "compress":
            self.open_button.setText(f"{_BACKEND_TITLES[self.backend]}の暗号化・圧縮設定を開く")
            self.notice.setText(
                "対象を固定してから、専用の確認画面でパスワード・出力先・圧縮率を入力します。"
                "その画面で実行を確定するまでファイルは変更しません。"
            )
            self.open_button.setEnabled(bool(paths))
            return
        archives = self._matching_archives(paths)
        self.open_button.setText(f"{_BACKEND_TITLES[self.backend]}の解凍・復号設定を開く")
        self.notice.setText(
            "対象を固定してから、専用の確認画面でパスワードの要否、解凍先、中身を確認します。"
            if archives else f".{self.backend} の既存ファイルを1件以上入力してください。"
        )
        self.open_button.setEnabled(bool(archives))

    def open_operation(self) -> None:
        paths = self._selected_paths()
        operation = self.operation_combo.currentData()
        if operation == "compress":
            if not paths:
                return
            dialog = QuickCompressDialog(paths)
            dialog.format_combo.setCurrentIndex(dialog.format_combo.findData(self.backend))
        else:
            archives = self._matching_archives(paths)
            if not archives:
                return
            dialog = QuickExtractDialog(archives)
        self._dialogs.add(dialog)
        dialog.destroyed.connect(lambda _object=None, current=dialog: self._dialogs.discard(current))
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def describe_work_state(self):
        if self._dialogs:
            return {"level": 3, "reason": "暗号化または解凍の確認画面を開いています。"}
        if self._selected_paths():
            return {"level": 2, "reason": f"{_BACKEND_TITLES[self.backend]}の対象を{len(self._selected_paths())}件入力しています。"}
        return {"level": 1, "reason": f"{_BACKEND_TITLES[self.backend]}で行う操作を選択しています。"}


class PlannedBackendScreen(QWidget):
    """An honest, non-operational shell for a backend not yet integrated."""

    def __init__(self, backend: str, return_to_hub: Callable[[], None]) -> None:
        super().__init__()
        _use_readable_font(self)
        layout = AppPageLayout(self)
        layout.addWidget(AppHeader(return_to_hub, title=f"{_BACKEND_TITLES[backend]}：準備中"))
        text = QLabel(self._text(backend))
        text.setWordWrap(True)
        text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(text)
        layout.addStretch(1)

    @staticmethod
    def _text(backend: str) -> str:
        return (
            "Cryptomator保管庫の作成、開く、ロック、状態確認をこの場所へ追加します。\n\n"
            "現時点ではCryptomatorを起動せず、保管庫、パスフレーズ、復旧情報は記録・送信しません。"
        )

    def describe_work_state(self):
        return {"level": 1, "reason": "この方式は対応準備中です。まだ外部アプリやファイルを操作しません。"}


class EncryptionHubScreen(QWidget):
    """Choose an encryption backend before showing its compatible operations."""

    def __init__(self, return_to_main: Callable[[], None]) -> None:
        super().__init__()
        _use_readable_font(self)
        self._return_to_main = return_to_main
        self._pages: dict[str, QWidget] = {}
        self._stack = QStackedWidget()
        layout = AppPageLayout(self)
        layout.addWidget(self._stack, 1)
        self._home = self._build_home()
        self._stack.addWidget(self._home)
        self._stack.currentChanged.connect(self._sync_visible_page_floor)
        self._sync_visible_page_floor()

    def _sync_visible_page_floor(self, *_unused: object) -> None:
        """Let the outer scroll container preserve the active page's rows.

        This hub contains a nested stack.  Without an explicit floor update,
        the outer PORTA scroll area only sees the compact chooser page and
        compresses a selected backend's labels and fields at minimum height.
        """
        page = self._stack.currentWidget()
        if page is None:
            return
        self.setMinimumSize(page.minimumSizeHint())
        self.updateGeometry()

    def _build_home(self) -> QWidget:
        page = QWidget()
        layout = AppPageLayout(page)
        layout.addWidget(AppHeader(self._return_to_main, title="暗号化・保護"))
        explanation = QLabel(
            "方式を先に選ぶと、その方式で安全に行える操作だけを表示します。"
            "パスワード、復旧情報、鍵の内容、実行履歴は保存しません。"
        )
        explanation.setWordWrap(True)
        layout.addWidget(explanation)
        box = QGroupBox("方式を選ぶ")
        box_layout = QVBoxLayout(box)
        self.backend_combo = NoWheelComboBox()
        self.backend_combo.addItem("方式を選択", "")
        for key, title, description in _BACKENDS:
            self.backend_combo.addItem(title, key)
            self.backend_combo.setItemData(self.backend_combo.count() - 1, description, Qt.ItemDataRole.ToolTipRole)
        self.backend_combo.currentIndexChanged.connect(self._combo_selected)
        box_layout.addWidget(self.backend_combo)
        button_grid = QGridLayout()
        button_grid.setContentsMargins(0, 0, 0, 0)
        button_grid.setHorizontalSpacing(8)
        button_grid.setVerticalSpacing(8)
        button_grid.setColumnStretch(0, 1)
        button_grid.setColumnStretch(1, 1)
        for index, (key, title, description) in enumerate(_BACKENDS):
            button = QPushButton(title)
            button.setToolTip(description)
            button.setMinimumHeight(self.fontMetrics().lineSpacing() + 12)
            button.clicked.connect(lambda _checked=False, value=key: self.open_backend(value))
            button_grid.addWidget(button, index // 2, index % 2)
        box_layout.addLayout(button_grid)
        layout.addWidget(box)
        note = QLabel(
            "LUKS、VeraCrypt、7z、ZIP、RAR解凍を利用できます。VeraCryptは安全なCLI機能を"
            "確認できた環境だけで有効になります。Cryptomatorは準備画面です。"
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        layout.addStretch(1)
        return page

    def _combo_selected(self, _index: int) -> None:
        backend = self.backend_combo.currentData()
        if backend:
            self.open_backend(str(backend))

    def open_backend(self, backend: str) -> None:
        if backend not in _BACKEND_TITLES:
            raise ValueError("未対応の暗号化方式です。")
        page = self._pages.get(backend)
        if page is None:
            if backend == "luks":
                page = StorageEncryptionScreen(self.show_home)
            elif backend == "veracrypt":
                page = VeraCryptScreen(self.show_home)
            elif backend in {"7z", "zip", "rar"}:
                page = ArchiveProtectionScreen(backend, self.show_home)
            else:
                page = PlannedBackendScreen(backend, self.show_home)
            self._pages[backend] = page
            self._stack.addWidget(page)
        self.backend_combo.blockSignals(True)
        self.backend_combo.setCurrentIndex(self.backend_combo.findData(backend))
        self.backend_combo.blockSignals(False)
        self._stack.setCurrentWidget(page)
        self._sync_visible_page_floor()

    def show_home(self) -> None:
        self._stack.setCurrentWidget(self._home)
        self.backend_combo.blockSignals(True)
        self.backend_combo.setCurrentIndex(0)
        self.backend_combo.blockSignals(False)
        self._sync_visible_page_floor()

    def describe_work_state(self):
        page = self._stack.currentWidget()
        describe = getattr(page, "describe_work_state", None)
        if callable(describe):
            return describe()
        return {"level": 1, "reason": "暗号化方式を選択しています。"}

    def shutdown(self) -> None:
        for page in self._pages.values():
            shutdown = getattr(page, "shutdown", None)
            if callable(shutdown):
                shutdown()


def create_screen(return_to_main: Callable[[], None]) -> EncryptionHubScreen:
    return EncryptionHubScreen(return_to_main)
