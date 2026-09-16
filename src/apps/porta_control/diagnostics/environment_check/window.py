"""Readable GUI for the read-only PORTA environment audit."""

from __future__ import annotations

from runtime.runtime_activity import runtime_activity

from collections.abc import Callable
from pathlib import Path

from gui import AppHeader, AppPageLayout
from gui.layout_policy import preferred_window_size, set_text_rows
from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTabWidget,
    QTextEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from apps.porta_control.configuration.templates import all_configuration_templates
from apps.porta_control.diagnostics.environment_check.audit import AuditItem, EnvironmentReport, run_environment_audit
from settings import persistent_settings


_LEVEL_DISPLAY = {
    "ok": ("正常", QColor("#1f7a3f")),
    "warning": ("注意", QColor("#a35a00")),
    "error": ("異常", QColor("#c62828")),
    "info": ("情報", QColor("#3569a8")),
}


class _AuditThread(QThread):
    completed = Signal(object)
    failed = Signal(str)

    @runtime_activity('環境を確認中')
    def run(self) -> None:
        try:
            report = run_environment_audit()
        except Exception as exc:  # keep an unexpected checker failure visible
            self.failed.emit(f"環境確認処理そのものが失敗しました: {exc}")
        else:
            self.completed.emit(report)


class EnvironmentCheckScreen(QWidget):
    """Inspect the whole known environment without repairing or writing it."""

    def describe_work_state(self):
        if self.tree.topLevelItemCount():
            return {"level": 3, "reason": "環境確認の結果を保持しています。閉じると表示結果は失われます。"}
        return {"level": 1, "reason": "環境確認の結果はまだありません。"}

    def __init__(self, return_to_main: Callable[[], None]) -> None:
        super().__init__()
        self._audit_thread: _AuditThread | None = None

        layout = AppPageLayout(self)
        header = AppHeader(return_to_main, title="環境整備・確認")
        header.content_layout.addStretch(1)
        templates = QPushButton("全雛形を見る")
        templates.clicked.connect(self.show_templates)
        header.content_layout.addWidget(templates)
        self.refresh_button = QPushButton("再チェック")
        self.refresh_button.clicked.connect(self.refresh)
        header.content_layout.addWidget(self.refresh_button)
        layout.addWidget(header)

        explanation = QLabel(
            "PORTAがコード上で参照する本体構造、porta_user、設定形式、内蔵雛形、"
            "Python環境、実行バックエンドを読み取り専用で確認します。"
            "この画面はファイルの作成・修復・設定変更を行いません。"
        )
        explanation.setWordWrap(True)
        layout.addWidget(explanation)

        self.summary = QLabel("確認を開始します…")
        self.summary.setWordWrap(True)
        self.summary.setStyleSheet("font-weight: 700; padding: 5px;")
        layout.addWidget(self.summary)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(("状態", "確認項目", "結果", "場所"))
        self.tree.setAlternatingRowColors(True)
        self.tree.setRootIsDecorated(True)
        self.tree.setUniformRowHeights(False)
        tree_header = self.tree.header()
        tree_header.setSectionResizeMode(0, tree_header.ResizeMode.ResizeToContents)
        tree_header.setSectionResizeMode(1, tree_header.ResizeMode.ResizeToContents)
        tree_header.setSectionResizeMode(2, tree_header.ResizeMode.Stretch)
        tree_header.setSectionResizeMode(3, tree_header.ResizeMode.Stretch)
        self.tree.itemSelectionChanged.connect(self._show_selected_detail)
        layout.addWidget(self.tree, 1)

        detail_title = QLabel("選択項目の詳細")
        detail_title.setStyleSheet("font-weight: 700;")
        layout.addWidget(detail_title)
        self.detail = QTextEdit()
        self.detail.setReadOnly(True)
        set_text_rows(self.detail, minimum=2, maximum=6)
        self.detail.setPlaceholderText("一覧の項目を選ぶと、判定内容とパスをここで確認できます。")
        layout.addWidget(self.detail)

        self.refresh()

    def refresh(self) -> None:
        if self._audit_thread is not None and self._audit_thread.isRunning():
            return
        self.refresh_button.setEnabled(False)
        self.summary.setText("確認中です… FFmpegなどには実際にバージョン照会を行います。")
        self.tree.clear()
        self.detail.clear()
        thread = _AuditThread(self)
        thread.completed.connect(self._show_report)
        thread.failed.connect(self._show_failure)
        thread.finished.connect(self._audit_finished)
        self._audit_thread = thread
        thread.start()

    def _audit_finished(self) -> None:
        thread = self._audit_thread
        self._audit_thread = None
        self.refresh_button.setEnabled(True)
        if thread is not None:
            thread.deleteLater()

    def _show_failure(self, detail: str) -> None:
        self.summary.setText(detail)
        self.summary.setStyleSheet("color: #c62828; font-weight: 700; padding: 5px;")

    def _show_report(self, report: EnvironmentReport) -> None:
        summary_color = (
            "#c62828"
            if report.count("error")
            else "#a35a00"
            if report.count("warning")
            else "#1f7a3f"
        )
        self.summary.setStyleSheet(f"color: {summary_color}; font-weight: 700; padding: 5px;")
        self.summary.setText(
            f"確認完了: 正常 {report.count('ok')} / 注意 {report.count('warning')} / "
            f"異常 {report.count('error')} / 情報 {report.count('info')}　"
            "（注意は未使用の任意機能や未作成設定を含みます）"
        )
        categories: dict[str, QTreeWidgetItem] = {}
        for item in report.items:
            parent = categories.get(item.category)
            if parent is None:
                parent = QTreeWidgetItem(self.tree, ("", item.category, "", ""))
                font = parent.font(1)
                font.setBold(True)
                parent.setFont(1, font)
                categories[item.category] = parent
            self._append_item(parent, item)
        for parent in categories.values():
            parent.setExpanded(True)
            child_levels = [parent.child(index).data(0, Qt.ItemDataRole.UserRole + 1) for index in range(parent.childCount())]
            strongest = (
                "error"
                if "error" in child_levels
                else "warning"
                if "warning" in child_levels
                else "ok"
                if "ok" in child_levels
                else "info"
            )
            label, color = _LEVEL_DISPLAY[strongest]
            parent.setText(0, label)
            parent.setForeground(0, color)
        self.tree.resizeColumnToContents(0)
        self.tree.resizeColumnToContents(1)

    def _append_item(self, parent: QTreeWidgetItem, item: AuditItem) -> None:
        label, color = _LEVEL_DISPLAY[item.level]
        path_text = str(item.path) if item.path is not None else ""
        short_detail = item.detail.splitlines()[0]
        child = QTreeWidgetItem(parent, (label, item.name, short_detail, path_text))
        child.setForeground(0, color)
        font = QFont(child.font(0))
        font.setBold(True)
        child.setFont(0, font)
        child.setData(0, Qt.ItemDataRole.UserRole, item)
        child.setData(0, Qt.ItemDataRole.UserRole + 1, item.level)
        child.setToolTip(2, item.detail)
        child.setToolTip(3, path_text)

    def _show_selected_detail(self) -> None:
        selected = self.tree.selectedItems()
        if not selected:
            self.detail.clear()
            return
        item = selected[0].data(0, Qt.ItemDataRole.UserRole)
        if not isinstance(item, AuditItem):
            self.detail.setPlainText(f"分類: {selected[0].text(1)}")
            return
        label = _LEVEL_DISPLAY[item.level][0]
        path = f"\n\n場所:\n{item.path}" if item.path is not None else ""
        self.detail.setPlainText(f"{label} / {item.category} / {item.name}\n\n{item.detail}{path}")

    def show_templates(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("PORTAの全設定雛形")
        dialog.resize(preferred_window_size(dialog))
        layout = QVBoxLayout(dialog)
        description = QLabel(
            "ここに表示するのはコード内の原本です。実際の設定ファイルは読み替えず、編集も保存もしません。"
            "設定ファイルが未作成の場合、各アプリは対応する原本をメモリ上で利用します。"
        )
        description.setWordWrap(True)
        layout.addWidget(description)
        tabs = QTabWidget()
        config = persistent_settings.locate_settings_directory()
        for template in all_configuration_templates():
            page = QWidget()
            page_layout = QVBoxLayout(page)
            if template.is_bootstrap:
                target: Path | None = persistent_settings.BOOTSTRAP_PATH
            elif config.directory is not None and template.relative_path is not None:
                target = config.directory / template.relative_path
            else:
                target = None
            location = QLabel(f"想定保存先: {target if target is not None else 'CONFIG位置を確定できません'}")
            location.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            location.setWordWrap(True)
            page_layout.addWidget(location)
            editor = QTextEdit()
            editor.setReadOnly(True)
            editor.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
            editor.setPlainText(template.template_text())
            page_layout.addWidget(editor, 1)
            tabs.addTab(page, template.title)
        layout.addWidget(tabs, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.exec()

    def shutdown(self) -> None:
        thread = self._audit_thread
        if thread is not None and thread.isRunning():
            thread.requestInterruption()
            thread.wait(30_000)


def create_screen(return_to_main: Callable[[], None]) -> EnvironmentCheckScreen:
    return EnvironmentCheckScreen(return_to_main)
