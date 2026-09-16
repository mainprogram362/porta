"""In-process work tabs with peer-visible status and explicit detachment."""
from __future__ import annotations

import os
from uuid import uuid4

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtWidgets import (
    QHBoxLayout, QMenu, QMessageBox, QPushButton, QStackedWidget, QTabBar,
    QStyle, QVBoxLayout, QWidget,
)

from gui.composites.path_support import open_in_standard_file_manager
from gui.work_lifecycle import dependent_windows, running_work
from gui.work_state import STATE_NAMES, describe_work
from runtime.single_instance import MainInstance
from runtime.work_process import ROOT, directory, inventory, spawn, validate_command


class CompactTabBar(QTabBar):
    def tabSizeHint(self, index):  # noqa: N802
        size = super().tabSizeHint(index)
        return QSize(min(size.width(), self.fontMetrics().horizontalAdvance("あ") * 16), size.height())


_COMPACT_TITLES = {
    "ファイルマネージャー": "ファイル",
    "テキスト加工ワークベンチ": "テキスト",
    "動画エンコード・圧縮（プロトタイプ）": "動画変換",
    "メディア情報ワークスペース": "メディア情報",
    "Firefox・カートリッジ使用": "Firefox操作",
    "ブラウザ・カートリッジ作成": "操作作成",
}
_STATE_MARKS = {1: "○", 2: "•", 3: "◆", 4: "▶", None: "?"}


class WorkTabs(QWidget):
    """One PORTA window process owns its ordinary tabs."""

    def __init__(self, menu):
        super().__init__(menu)
        self.menu = menu
        self.entries: dict[str, dict] = {}
        self.bar = CompactTabBar()
        self.bar.setElideMode(Qt.TextElideMode.ElideRight)
        self.bar.setMovable(True)
        self.bar.setExpanding(False)
        self.bar.setTabsClosable(True)
        self.bar.setUsesScrollButtons(True)
        self.stack = QStackedWidget()
        self.stack.setMinimumSize(0, 0)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        tab_row = QWidget()
        tab_layout = QHBoxLayout(tab_row)
        tab_layout.setContentsMargins(0, 0, 0, 0)
        tab_layout.setSpacing(4)
        tab_layout.addWidget(self.bar, 1)
        self.explorer_button = QPushButton()
        self.explorer_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DirOpenIcon))
        self.explorer_button.setIconSize(QSize(18, 18))
        self.explorer_button.setAccessibleName("ファイルエクスプローラーを開く")
        self.explorer_button.setToolTip("ファイルエクスプローラーをホームフォルダで開きます。")
        self.explorer_button.setFixedSize(34, 34)
        self.explorer_button.clicked.connect(lambda _checked=False: open_in_standard_file_manager())
        tab_layout.addWidget(self.explorer_button)
        self.overview_button = QPushButton("☰")
        self.overview_button.setAccessibleName("PORTAの画面")
        self.overview_button.setToolTip("すべてのPORTAウィンドウ、タブ、対応表を確認します。")
        self.overview_button.clicked.connect(menu.show_work_overview)
        tab_layout.addWidget(self.overview_button)
        layout.addWidget(tab_row)
        layout.addWidget(self.stack, 1)

        self._add(menu._screens, "メニュー", None)
        self.bar.currentChanged.connect(self.select)
        self.bar.tabCloseRequested.connect(self.close_tab)
        self.bar.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.bar.customContextMenuRequested.connect(self.context_menu)
        self.timer = QTimer(self)
        self.timer.setInterval(750)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()
        self._transfer_timer = QTimer(self)
        self._transfer_timer.setInterval(100)
        self._transfer_timer.timeout.connect(self._confirm_transfers)
        self._transfers: dict[str, str] = {}

    def _add(self, page, title, tab_id):
        self.stack.addWidget(page)
        index = self.bar.addTab(title)
        self.bar.setTabData(index, tab_id)
        if tab_id is None:
            for side in (QTabBar.ButtonPosition.LeftSide, QTabBar.ButtonPosition.RightSide):
                self.bar.setTabButton(index, side, None)
        return index

    def add_work(self, definition, screen):
        tab_id = uuid4().hex
        server = MainInstance(ROOT / tab_id, self.menu, directory=directory(), validator=validate_command)
        management_error = ""
        try:
            if not server.start_or_forward({"version": 1, "op": "status"}):
                raise ValueError("タブ識別情報が重複しました。")
        except (OSError, RuntimeError, ValueError) as error:
            server.close()
            management_error = str(error)
        self.entries[tab_id] = {
            "definition": definition, "screen": screen, "title": definition.title,
            "server": server, "management_error": management_error,
        }
        if not management_error:
            server.set_handler(lambda request, work_id=tab_id: self.command(work_id, request))
        self.bar.setCurrentIndex(self._add(screen, definition.title, tab_id))
        self.refresh()
        return screen

    def command(self, tab_id, request):
        entry = self.entries.get(tab_id)
        if entry is None:
            raise ValueError("対象のタブは終了しました。")
        if request["op"] == "focus":
            self.bar.setCurrentIndex(self._index(tab_id))
            from apps.launcher.launch_requests import activate_window
            activate_window(self.menu)
        elif request["op"] == "close":
            QTimer.singleShot(0, lambda: self.close_tab(self._index(tab_id)))
        definition = entry["definition"]
        return {
            "version": 1, "id": tab_id, "app": getattr(definition, "key", "external_choose"),
            "title": entry["title"], "pid": os.getpid(), "kept": False, "host": "tab",
            **describe_work(entry["screen"], include_process_activities=False),
        }

    def _index(self, tab_id):
        return next((i for i in range(self.bar.count()) if self.bar.tabData(i) == tab_id), -1)

    def select(self, index):
        if index < 0:
            return
        self.stack.setCurrentIndex(index)
        tab_id = self.bar.tabData(index)
        if tab_id is None:
            self.menu._refresh_menu_floor()
            return
        self.menu.setMinimumSize(self.menu._application_window_floor())
        self.menu.setWindowTitle(f"PORTA — {self.entries[tab_id]['title']}")

    def show_menu(self):
        self.bar.setCurrentIndex(0)

    def refresh(self):
        for tab_id, entry in tuple(self.entries.items()):
            index = self._index(tab_id)
            if index < 0:
                continue
            state = describe_work(entry["screen"], include_process_activities=False)
            title = _COMPACT_TITLES.get(entry["title"], entry["title"])
            self.bar.setTabText(index, f"{title} {_STATE_MARKS.get(state['level'], '?')}")
            detail = state["reason"]
            if entry["management_error"]:
                detail += "\n一覧へ登録できません: " + entry["management_error"]
            self.bar.setTabToolTip(index, f"{entry['title']}\n{STATE_NAMES.get(state['level'], '状態不明')}：{detail}")

    def _detach_payload(self, entry):
        screen = entry["screen"]
        if running_work(screen) or dependent_windows(screen):
            return None, "実行中の処理または補助画面があるため分離できません。"
        if getattr(screen, "_record_link_bundle", None) is not None:
            return None, "対応表を参照中のファイルマネージャーは分離できません。"
        from apps.text_tools.text_workbench import TextWorkbenchScreen
        from apps.file_tools.file_manager import FileManagerScreen
        if isinstance(screen, TextWorkbenchScreen):
            return {"app": "text_workbench", "text": screen.text_editor.toPlainText()}, ""
        if isinstance(screen, FileManagerScreen):
            state = screen._capture_ui_state()
            if state["rename_rules"] or state["destinations"] or screen._operation_presentation is not None:
                return None, "リネーム規則・宛先・操作予定があるファイルマネージャーは分離できません。"
            return {"app": "file_manager", "paths": [row[0] for row in state["results"]]}, ""
        return None, "この画面は安全な分離にまだ対応していません。"

    def detach(self, tab_id):
        payload, reason = self._detach_payload(self.entries[tab_id])
        if payload is None:
            QMessageBox.information(self.menu, "分離できません", reason)
            return
        transfer_id = uuid4().hex
        payload["transfer_id"] = transfer_id
        try:
            spawn(payload)
        except (OSError, ValueError) as error:
            QMessageBox.warning(self.menu, "分離できません", str(error))
            return
        self._transfers[transfer_id] = tab_id
        self._transfer_timer.start()
        self.menu.statusBar().showMessage("新しいウィンドウで作業を確認しています。元のタブは確認後に閉じます。")

    def _confirm_transfers(self):
        try:
            items = inventory()
        except OSError:
            return
        received = {item.get("transfer_id") for item in items}
        for transfer_id, tab_id in tuple(self._transfers.items()):
            if transfer_id in received:
                self._transfers.pop(transfer_id)
                if tab_id in self.entries:
                    self._remove(tab_id, close=True)
                self.menu.statusBar().showMessage("作業を別ウィンドウへ移しました。")
        if not self._transfers:
            self._transfer_timer.stop()

    def close_tab(self, index):
        if index < 0:
            return
        tab_id = self.bar.tabData(index)
        if tab_id is None or tab_id not in self.entries:
            return
        entry = self.entries[tab_id]
        screen = entry["screen"]
        state = describe_work(screen, include_process_activities=False)
        if running_work(screen) or dependent_windows(screen) or state["level"] == 4:
            QMessageBox.information(self.menu, "作業を継続しています", state["reason"])
            return
        if state["level"] in (2, 3, None) and QMessageBox.question(
            self.menu, "この作業を閉じる", entry["title"] + "\n" + state["reason"] +
            "\n入力・途中結果を破棄しますか？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return
        self._remove(tab_id, close=True)

    def _remove(self, tab_id, *, close):
        entry = self.entries.pop(tab_id)
        entry["server"].close()
        screen = entry["screen"]
        if close:
            shutdown = getattr(screen, "shutdown", None)
            if callable(shutdown):
                shutdown()
            screen.close()
        index = self._index(tab_id)
        self.stack.removeWidget(screen)
        self.bar.removeTab(index)
        screen.deleteLater()

    def context_menu(self, point):
        index = self.bar.tabAt(point)
        if index < 0:
            return
        tab_id = self.bar.tabData(index)
        if tab_id not in self.entries:
            return
        payload, reason = self._detach_payload(self.entries[tab_id])
        menu = QMenu(self)
        action = menu.addAction("別ウィンドウへ分離")
        action.setEnabled(payload is not None)
        action.setToolTip(reason or "このタブを新しい同格のPORTAウィンドウへ移します。")
        action.triggered.connect(lambda: self.detach(tab_id))
        menu.addAction("作業を閉じる", lambda: self.close_tab(index))
        menu.exec(self.bar.mapToGlobal(point))

    def running_reason(self):
        for entry in self.entries.values():
            screen = entry["screen"]
            state = describe_work(screen, include_process_activities=False)
            if running_work(screen) or dependent_windows(screen) or state["level"] == 4:
                return entry["title"] + "：" + state["reason"]
        return None

    def shutdown(self):
        self.timer.stop()
        self._transfer_timer.stop()
        for tab_id in tuple(self.entries):
            self._remove(tab_id, close=True)
