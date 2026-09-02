"""Choose what PORTA should do with an already validated external selection."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtWidgets import (
    QGridLayout,
    QGroupBox,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from apps.file_tools.file_manager.extract_workflow import is_supported_archive_path
from foundation.external_open import ExternalOpenIntent
from gui import AppHeader, AppPageLayout


class ExternalOpenChooserScreen(QWidget):
    """Display one non-persistent routing decision after common validation."""

    def __init__(
        self,
        paths: tuple[Path, ...],
        choose: Callable[[ExternalOpenIntent], None],
        cancel: Callable[[], None],
    ) -> None:
        super().__init__()
        self.paths = paths
        self._choose = choose
        self.setMinimumSize(760, 560)

        layout = AppPageLayout(self)
        layout.addWidget(AppHeader(cancel, title="受け取ったパスの操作"))
        explanation = QLabel(
            f"外部ファイルマネージャーから{len(paths)}件を受け取り、全件の入口検査に通りました。"
            "ここでは実ファイルを変更しません。次に行う操作を選んでください。"
        )
        explanation.setWordWrap(True)
        layout.addWidget(explanation)

        received_box = QGroupBox(f"今回だけの受信一覧（{len(paths)}件）")
        received_layout = QVBoxLayout(received_box)
        received = QPlainTextEdit("\n".join(str(path) for path in paths))
        received.setReadOnly(True)
        received.setMinimumHeight(130)
        received_layout.addWidget(received)
        layout.addWidget(received_box)

        actions = QGroupBox("何をしますか")
        action_layout = QGridLayout(actions)
        self._add_action(
            action_layout,
            0,
            0,
            "ファイル一覧で開く",
            "PORTAのファイルマネージャーへ渡し、作業一覧として開きます。",
            "file-manager",
            "browse",
        )
        self._add_action(
            action_layout,
            0,
            1,
            "コピー画面へ",
            "受信した全件をコピー対象にした独立画面を開きます。",
            "file-manager",
            "copy",
        )
        self._add_action(
            action_layout,
            1,
            0,
            "圧縮画面へ",
            "受信した全件を圧縮対象にした独立画面を開きます。",
            "file-manager",
            "compress",
        )
        extract = self._add_action(
            action_layout,
            1,
            1,
            "解凍画面へ",
            "受信したZIP・7z・RARを解凍対象として開きます。",
            "file-manager",
            "extract",
        )
        all_archives = bool(paths) and all(is_supported_archive_path(path) for path in paths)
        extract.setEnabled(all_archives)
        if not all_archives:
            extract.setToolTip("全件が対応圧縮ファイル（ZIP・7z・RAR）の場合だけ選べます。")
        self._add_action(
            action_layout,
            2,
            0,
            "メディア整理へ",
            "受信一覧をメディア情報整理へ渡し、対象確認から始めます。",
            "media-organizer",
            "inspect",
        )
        self._add_action(
            action_layout,
            2,
            1,
            "動画エンコードへ",
            "受信一覧を動画エンコーダーへ渡し、変換設定から始めます。",
            "video-encoder",
            "encode",
        )
        action_layout.setColumnStretch(0, 1)
        action_layout.setColumnStretch(1, 1)
        layout.addWidget(actions)
        layout.addStretch(1)

    def _add_action(
        self,
        layout: QGridLayout,
        row: int,
        column: int,
        title: str,
        description: str,
        target: str,
        action: str,
    ) -> QPushButton:
        button = QPushButton(f"{title}\n{description}")
        button.setMinimumHeight(62)
        button.clicked.connect(
            lambda _checked=False: self._choose(
                ExternalOpenIntent(target=target, action=action, paths=self.paths)  # type: ignore[arg-type]
            )
        )
        layout.addWidget(button, row, column)
        return button
