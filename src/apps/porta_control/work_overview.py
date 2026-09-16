"""One user-facing overview of every PORTA window and correspondence table."""
from __future__ import annotations

from collections import deque

from PySide6.QtCore import QThread, Signal, QTimer, Qt
from PySide6.QtWidgets import (
    QComboBox, QDialog, QHeaderView, QLabel, QPushButton, QTreeWidget,
    QTreeWidgetItem, QVBoxLayout,
)

from gui.flow_layout import FlowLayout
from gui.layout_policy import preferred_window_size
from gui.work_state import STATE_NAMES
from runtime.process_control import process_inventory
from runtime.process_registry import PORTA_ROOT
from runtime.work_process import inventory as work_inventory


_KIND_NAMES = {
    "menu": "メニュー", "tab": "タブ", "window": "別ウィンドウ",
    "record": "対応表", "unknown": "確認が必要",
}


def unified_inventory() -> list[dict]:
    """Combine UI protocols; process records only fill discovery gaps."""
    items: list[dict] = []
    work_items = work_inventory()
    responsive_pids = {
        item.get("pid") for item in work_items
        if type(item.get("pid")) is int and item.get("app") != "records"
    }
    for item in work_items:
        if item.get("app") == "records":
            continue
        kind = "tab" if item.get("host") == "tab" else "window"
        items.append({"kind": kind, "item_id": "work:" + item["endpoint"], **item})

    processes = process_inventory(root=PORTA_ROOT)
    for process in processes:
        if process.role == "main":
            items.append({
                "kind": "menu", "item_id": f"menu:{process.identity.key}",
                "title": process.screen or "メインメニュー",
                "level": 1 if process.health == "alive" else None,
                "reason": process.state or "PORTAのメニューです。",
                "pid": process.pid, "health": process.health,
            })
        elif process.role == "work" and process.pid not in responsive_pids:
            items.append({
                "kind": "unknown", "item_id": f"unknown:{process.identity.key}",
                "title": process.screen or "応答を確認できない作業画面", "level": None,
                "reason": "プロセスは存在しますが、画面の管理通信に応答していません。",
                "pid": process.pid, "health": process.health,
            })
        elif process.role == "unregistered":
            items.append({
                "kind": "unknown", "item_id": f"unknown:{process.identity.key}",
                "title": "管理情報のないPORTA", "level": None,
                "reason": "以前の方式で起動した可能性があります。画面から状態を取得できません。",
                "pid": process.pid, "health": process.health,
            })

    from records import record_service
    response = record_service.request({"action": "list"}, timeout_ms=700)
    if response.get("ok") and isinstance(response.get("records"), list):
        for record in response["records"]:
            if not isinstance(record, dict) or not isinstance(record.get("id"), str):
                continue
            items.append({
                "kind": "record", "item_id": "record:" + record["id"],
                "title": record.get("title") or "無題の対応表",
                "level": record.get("level") if record.get("level") in (1, 2, 3, 4) else 3,
                "reason": record.get("reason") or
                          f"第{record.get('revision', '?')}版・{record.get('rows', '?')}行・{record.get('fields', '?')}項目",
                "pid": record.get("pid", "不明"), "record_id": record["id"],
            })
    else:
        for process in processes:
            if process.role == "records":
                items.append({
                    "kind": "unknown", "item_id": f"records:{process.identity.key}",
                    "title": "状態を確認できない対応表", "level": None,
                    "reason": "対応表プロセスは存在しますが、個別の対応表を取得できません。",
                    "pid": process.pid, "health": process.health,
                })

    order = {"menu": 0, "tab": 1, "window": 2, "record": 3, "unknown": 4}
    return sorted(items, key=lambda item: (order[item["kind"]], str(item.get("title", "")), str(item["item_id"])))


class Query(QThread):
    result = Signal(object)

    def __init__(self, operation, parent):
        super().__init__(parent)
        self.operation = operation

    def run(self):
        try:
            self.result.emit((True, self.operation()))
        except Exception as error:
            self.result.emit((False, str(error)))


class WorkCenter(QDialog):
    """Show ordinary windows and tables; keep process mechanics in details."""

    summary_changed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("PORTAの画面")
        self.resize(preferred_window_size(self))
        self.worker = None
        self.stopping = False
        self.items: list[dict] = []
        self.ready = False
        self._commands = deque()
        self._process_center = None

        layout = QVBoxLayout(self)
        self.summary = QLabel("PORTAの画面を確認中")
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        self.filter = QComboBox()
        for title, value in (
            ("すべて", "all"), ("処理実行中", "running"), ("作業中・保持", "keep"),
            ("初期状態・設定段階", "shallow"), ("状態不明", "unknown"),
        ):
            self.filter.addItem(title, value)
        self.filter.currentIndexChanged.connect(self.render)
        layout.addWidget(self.filter)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["画面", "種類", "状態", "内容"])
        self.tree.setTextElideMode(Qt.TextElideMode.ElideNone)
        self.tree.header().setStretchLastSection(False)
        for column in (1, 2):
            self.tree.header().setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.header().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.tree.setColumnWidth(0, self.fontMetrics().horizontalAdvance("画面名を表示するための幅"))
        self.tree.itemSelectionChanged.connect(self.show_detail)
        self.tree.itemDoubleClicked.connect(lambda *_: self.send("focus"))
        layout.addWidget(self.tree, 1)

        self.detail = QLabel("画面を選択すると、現在の状態をここに表示します。")
        self.detail.setWordWrap(True)
        self.detail.setTextFormat(Qt.TextFormat.PlainText)
        self.detail.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.detail)

        actions = FlowLayout()
        self.refresh_button = QPushButton("更新")
        self.focus_button = QPushButton("選択した画面を表示")
        self.close_button = QPushButton("画面側で終了を確認")
        self.settings_button = QPushButton("設定を開く")
        self.environment_button = QPushButton("環境診断")
        self.process_button = QPushButton("プロセス詳細")
        for button, callback in (
            (self.refresh_button, self.refresh), (self.focus_button, lambda: self.send("focus")),
            (self.close_button, lambda: self.send("close")),
            (self.settings_button, lambda: self.open_control("settings")),
            (self.environment_button, lambda: self.open_control("environment")),
            (self.process_button, self.show_process_details),
        ):
            button.clicked.connect(callback)
            actions.addWidget(button)
        layout.addLayout(actions)

        self.message = QLabel(
            "ここにはPORTAのメニュー、作業画面、対応表をまとめて表示します。"
            "PIDや内部処理は「プロセス詳細」で確認できます。"
        )
        self.message.setWordWrap(True)
        self.message.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.message)
        self.timer = QTimer(self)
        self.timer.setInterval(3000)
        self.timer.timeout.connect(self.refresh)

    def showEvent(self, event):
        super().showEvent(event)
        self.stopping = False
        self.timer.start()
        self.refresh()

    def hideEvent(self, event):
        self.timer.stop()
        super().hideEvent(event)

    def refresh(self):
        if not self.stopping:
            self.start(unified_inventory, self.received)

    def start(self, operation, callback, *, enqueue=False):
        if self.stopping:
            return
        if self.worker is not None:
            if enqueue and len(self._commands) < 16:
                self._commands.append((operation, callback))
                self.message.setText("状態更新の後に、選択した画面へ要求を送ります。")
            elif enqueue:
                self.message.setText("要求が混み合っています。今回の要求は受け付けていません。")
            return
        self.worker = Query(operation, self)
        self.worker.result.connect(callback)
        self.worker.finished.connect(self._query_finished)
        self.worker.start()

    def _query_finished(self):
        worker, self.worker = self.worker, None
        worker.deleteLater()
        if self._commands and not self.stopping:
            operation, callback = self._commands.popleft()
            self.start(operation, callback)

    def received(self, result):
        ok, items = result
        if not ok:
            self.ready = False
            self.message.setText("状態取得に失敗。表示は前回の情報です: " + items)
            self.summary_changed.emit("PORTAの画面：取得失敗")
            return
        self.items = items
        self.ready = True
        self.render()
        running = sum(item.get("level") == 4 for item in items)
        unknown = sum(item.get("level") is None for item in items)
        text = f"PORTAの画面：{len(items)}件 ／ 処理実行中：{running}件 ／ 状態不明：{unknown}件"
        self.summary.setText(text)
        self.summary_changed.emit(text)

    def render(self, *_):
        selected = self.tree.currentItem()
        selected_id = selected.data(0, Qt.ItemDataRole.UserRole) if selected else None
        self.tree.clear()
        mode = self.filter.currentData()
        for item in self.items:
            level = item.get("level")
            visible = {"all": True, "running": level == 4,
                       "keep": level == 3 or item.get("kept"),
                       "shallow": level in (1, 2), "unknown": level is None}[mode]
            if not visible:
                continue
            row = QTreeWidgetItem(self.tree, [
                str(item.get("title", "名称不明")), _KIND_NAMES[item["kind"]],
                STATE_NAMES.get(level, "状態不明"), str(item.get("reason", "")),
            ])
            row.setData(0, Qt.ItemDataRole.UserRole, item["item_id"])
            for column in range(4):
                row.setToolTip(column, row.text(column))
            if item["item_id"] == selected_id:
                self.tree.setCurrentItem(row)
        self.show_detail()

    def selected_item(self):
        row = self.tree.currentItem()
        if row is None:
            return None
        item_id = row.data(0, Qt.ItemDataRole.UserRole)
        return next((item for item in self.items if item["item_id"] == item_id), None)

    def show_detail(self):
        item = self.selected_item()
        if item is None:
            self.detail.setText("画面を選択してください。")
            self.focus_button.setEnabled(False)
            self.close_button.setEnabled(False)
            return
        self.detail.setText(f"{item.get('title', '名称不明')}\n{STATE_NAMES.get(item.get('level'), '状態不明')}："
                            f"{item.get('reason', '')}")
        self.focus_button.setEnabled(item["kind"] in {"menu", "tab", "window", "record"})
        self.close_button.setEnabled(item["kind"] in {"tab", "window", "record"})

    @staticmethod
    def _send_command(item, operation):
        if item["kind"] in {"tab", "window"}:
            from runtime.work_process import request
            return request(item["endpoint"], operation, timeout=2)
        if item["kind"] == "record":
            from records import record_service
            action = "edit" if operation == "focus" else "close"
            response = record_service.request({"action": action, "id": item["record_id"]})
            if not response.get("ok"):
                raise OSError(response.get("error", "対応表が要求を受け付けませんでした。"))
            return response
        if item["kind"] == "menu" and operation == "focus":
            from runtime.work_process import open_manager
            open_manager()
            return {}
        raise ValueError("この項目にはその操作を行えません。")

    def send(self, operation):
        item = self.selected_item()
        if item is not None:
            self.start(lambda: self._send_command(item, operation), self.sent, enqueue=True)

    def sent(self, result):
        ok, value = result
        self.message.setText("要求を送りました。対象画面を確認してください。" if ok
                             else "応答を確認できません。再送していません: " + str(value))
        if ok:
            QTimer.singleShot(150, self.refresh)

    def show_process_details(self):
        if self._process_center is None:
            from apps.porta_control.diagnostics.process_center import ProcessCenterDialog
            self._process_center = ProcessCenterDialog(self)
        self._process_center.show()
        self._process_center.raise_()
        self._process_center.activateWindow()

    def open_control(self, section):
        local_open = getattr(self.parentWidget(), "show_control_section", None)
        if callable(local_open):
            local_open(section)
            self.message.setText("このPORTAウィンドウに管理タブを開きました。")
            return
        from runtime.work_process import spawn
        try:
            spawn({"app": "porta_control", "section": section})
            self.message.setText("新しいPORTA管理画面を開きました。")
        except (OSError, ValueError) as error:
            self.message.setText(str(error))

    def shutdown(self):
        self.stopping = True
        self._commands.clear()
        self.timer.stop()
        if self._process_center is not None:
            self._process_center.close()
