"""One detached process owns all live correspondence-table windows in memory."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import QLockFile, Qt, QTimer
from PySide6.QtNetwork import QLocalServer
from PySide6.QtWidgets import QApplication, QMainWindow, QWidget, QVBoxLayout, QLabel, QListWidget, QPushButton, QMessageBox

from runtime.instance_presence import InstancePresence
from gui.process_tracking import PresenceHeartbeat
from gui.layout_policy import preferred_window_size

from records.record_bundle import TransientRecordBundleStore
from records.record_service import PORTA_ROOT, server_name, request, encode_bundle, decode_bundle
from apps.text_tools.text_workbench.record_bundle_window import RecordBundleWindow


class OwnedRecordWindow(RecordBundleWindow):
    def _sync_table_to_store(self):
        result = super()._sync_table_to_store()
        if hasattr(self, "document_changed") and not self._updating_table:
            self.document_changed()
        return result

    def _load_bundle(self, bundle):
        super()._load_bundle(bundle)
        if hasattr(self, "document_changed"):
            self.document_changed()

    def send_bundle_to_file_manager(self):
        bundle = self._sync_table_to_store()
        if bundle is not None and bundle.rows:
            self._send_to_file_manager(bundle)
            self.output_status_label.setText("この対応表を取得するファイルマネージャーを新しく開きました。")

    def closeEvent(self, event):
        from gui.work_lifecycle import running_work, dependent_windows
        from gui.work_state import describe_work
        # RecordBundleWindow.shutdown() is the explicit process/fixture
        # teardown path.  It has already erased the transient document, so a
        # second interactive confirmation here would be both misleading and
        # unsafe during QObject destruction.
        if getattr(self, "_shutting_down", False):
            self.retired()
            event.accept()
            return
        if getattr(self, "_porta_close_pending", False) or QApplication.activeModalWidget() is not None:
            event.ignore()
            return
        self._porta_close_pending = True
        try:
            if running_work(self) or dependent_windows(self) or describe_work(self)["level"] == 4:
                QMessageBox.information(self, "対応表を保持しています", "処理を完了・停止し、補助画面を閉じてから終了してください。")
                event.ignore()
                return
            if QMessageBox.question(self, "対応表を終了", "この対応表を終了しますか？ メモリ内の元データは破棄されます。取得済みのファイルマネージャーの内容は残ります。",
                                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                    QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            if running_work(self) or dependent_windows(self) or describe_work(self)["level"] == 4:
                event.ignore()
                return
            self.retired()
            event.accept()
        finally:
            self._porta_close_pending = False


class RecordCenter(QMainWindow):
    def __init__(self):
        super().__init__()
        self.entries = {}
        self.buffers = {}
        self.lock = QLockFile(str(Path(tempfile.gettempdir()) / (server_name() + ".lock")))
        if not self.lock.tryLock(500):
            raise RuntimeError("対応表管理は起動済みです。")
        self.setWindowTitle("PORTA — 独立した対応表")
        self.resize(preferred_window_size(self))
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.addWidget(QLabel("対応表はメモリ内だけで保持します。名前は未入力でも使えます。"))
        self.list = QListWidget()
        layout.addWidget(self.list)
        button = QPushButton("選択した対応表を開く")
        button.clicked.connect(self.open_selected)
        self.list.itemDoubleClicked.connect(lambda _: self.open_selected())
        layout.addWidget(button)
        self.setCentralWidget(body)
        self.server = QLocalServer(self)
        self.server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        if not self.server.listen(server_name()):
            QLocalServer.removeServer(server_name())
            if not self.server.listen(server_name()):
                raise RuntimeError(self.server.errorString())
        self.server.newConnection.connect(self.accept_connections)
        self._presence = InstancePresence(role="records", screen="一時対応表", state="対応表を管理中")
        self._presence_heartbeat = PresenceHeartbeat(self._presence, self)
        from runtime.work_process import directory, validate_command
        from runtime.single_instance import MainInstance
        self._work_id = uuid4().hex
        self._work_bridge = MainInstance(PORTA_ROOT / self._work_id, self, directory=directory(), validator=validate_command)
        try:
            if not self._work_bridge.start_or_forward({"version": 1, "op": "status"}):
                raise ValueError("作業IDが重複しました。")
            self._work_bridge.set_handler(self.work_command)
        except (OSError, RuntimeError, ValueError) as error:
            self._work_bridge.close()
            self.statusBar().showMessage("管理一覧に登録できません。対応表は利用できます: " + str(error))
        from apps.porta_control.work_overview import WorkCenter
        self._work_center = WorkCenter(self)
        manager = QPushButton("PORTAの画面")
        manager.clicked.connect(self.show_work_overview)
        layout.addWidget(manager)

    def show_work_overview(self):
        self._work_center.stopping = False
        self._work_center.show()
        self._work_center.raise_()
        self._work_center.activateWindow()

    def work_command(self, message):
        from .launch_requests import activate_window
        from gui.work_state import describe_work
        if message["op"] == "focus":
            activate_window(self)
        elif message["op"] == "close":
            QTimer.singleShot(0, self.request_work_close)
        return {"version": 1, "id": self._work_id, "app": "records", "pid": os.getpid(),
                "title": "一時対応表", "kept": False, **describe_work(self)}

    def describe_work_state(self):
        from gui.work_lifecycle import running_work
        if any(running_work(entry["window"]) for entry in self.entries.values()):
            return {"level": 4, "reason": "対応表に属する処理を実行中です。"}
        return {"level": 3 if self.entries else 1,
                "reason": f"対応表{len(self.entries)}枚を保持しています。終了は各対応表で確認します。"}

    def request_work_close(self):
        from .launch_requests import activate_window
        activate_window(self)
        if self.entries:
            QMessageBox.information(self, "対応表を保持しています", "終了したい対応表を開き、個別に閉じてください。")
        else:
            self.close()

    def snapshot(self, code):
        entry = self.entries[code]
        bundle = entry["window"]._bundle_from_table()
        if bundle is None:
            raise ValueError("対応表を読み取れません。")
        if bundle != entry["bundle"]:
            entry["revision"] += 1
            entry["bundle"] = bundle
        entry["window"].setWindowTitle(f"PORTA — {bundle.title or '無題'} [{code}] 第{entry['revision']}版")
        return {"id": code, "revision": entry["revision"], "bundle": encode_bundle(bundle)}

    def refresh_list(self):
        self.list.clear()
        for code in self.entries:
            snapshot = self.snapshot(code)
            bundle = snapshot["bundle"]
            self.list.addItem(f"{bundle['title'] or '無題'} [{code}] 第{snapshot['revision']}版 — {len(bundle['rows'])}レコード / {len(bundle['fields'])}項目")
            self.list.item(self.list.count() - 1).setData(Qt.ItemDataRole.UserRole, code)

    def open_selected(self):
        item = self.list.currentItem()
        if item:
            self.show_record(item.data(Qt.ItemDataRole.UserRole))

    def show_record(self, code):
        window = self.entries[code]["window"]
        window.showNormal()
        window.raise_()
        window.activateWindow()

    def retire(self, code):
        self.entries.pop(code, None)
        self.refresh_list()
        if not self.entries:
            QTimer.singleShot(0, self.close)

    def create_record(self, bundle):
        code = uuid4().hex[:12].upper()
        while code in self.entries:
            code = uuid4().hex[:12].upper()
        store = TransientRecordBundleStore()
        store.replace(bundle)
        window = OwnedRecordWindow(store, send_to_text_workbench=lambda _: self.launch_text(code),
                                   send_to_file_manager=lambda _: self.launch_manager(code))
        window.retired = lambda: self.retire(code)
        window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        # The editor is the authoritative document; make its identity visible.
        identity = QLabel(f"対応表ID：{code} ｜ 本体を閉じても保持します。名前は任意です。")
        window.centralWidget().widget().layout().insertWidget(0, identity)
        for label in window.findChildren(QLabel):
            if "×で隠しても" in label.text():
                label.setText("閉じるとこの対応表を破棄します")
        for button in window.findChildren(QPushButton):
            if button.text() == "束を破棄":
                button.clicked.disconnect()
                button.clicked.connect(window.close)
        self.entries[code] = {"window": window, "bundle": bundle, "revision": 1}
        window.document_changed = self.refresh_list
        window.bundle_title_input.editingFinished.connect(self.refresh_list)
        self.refresh_list()
        self.show_record(code)
        return self.snapshot(code)

    def launch_manager(self, code):
        self.snapshot(code)
        self._launch_porta(["--record-id", code])

    def launch_text(self, code):
        self._launch_porta(["--record-output", code])

    def _launch_porta(self, arguments):
        process = subprocess.Popen(
            [sys.executable, str(PORTA_ROOT / "scripts/main.py"), *arguments],
            cwd=PORTA_ROOT,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        from runtime.process_registry import get_registry
        get_registry().ignore(process.pid)

    def handle(self, message):
        action = message.get("action")
        if action == "ping":
            return {"ok": True}
        if action == "create":
            return {"ok": True, **self.create_record(decode_bundle(message["bundle"]))}
        if action in {"list", "show"}:
            self.refresh_list()
            if action == "show":
                for record_code in self.entries:
                    self.show_record(record_code)
            records = []
            for code in self.entries:
                snapshot = self.snapshot(code)
                bundle = snapshot.pop("bundle")
                from gui.work_state import describe_work
                state = describe_work(self.entries[code]["window"], include_process_activities=False)
                records.append({**snapshot, "title": bundle["title"], "rows": len(bundle["rows"]),
                                "fields": len(bundle["fields"]), "pid": os.getpid(), **state})
            return {"ok": True, "records": records}
        code = message.get("id")
        if code not in self.entries:
            return {"ok": False, "error": "元の対応表は終了済みです。取得済みの内容はそのまま使えます。"}
        if action == "get":
            return {"ok": True, **self.snapshot(code)}
        if action == "output":
            text = self.entries[code]["window"]._render_output()
            return {"ok": text is not None, "text": text}
        if action == "edit":
            self.show_record(code)
            return {"ok": True}
        if action == "close":
            QTimer.singleShot(0, self.entries[code]["window"].close)
            return {"ok": True}
        raise ValueError("不明な操作です。")

    def accept_connections(self):
        while self.server.hasPendingConnections():
            socket = self.server.nextPendingConnection()
            self.buffers[socket] = bytearray()
            socket.readyRead.connect(lambda s=socket: self.read_socket(s))
            socket.disconnected.connect(lambda s=socket: (self.buffers.pop(s, None), s.deleteLater()))
            if socket.bytesAvailable():
                self.read_socket(socket)

    def read_socket(self, socket):
        buffer = self.buffers[socket]
        buffer.extend(bytes(socket.readAll()))
        if b"\n" not in buffer and len(buffer) <= 32 * 1024 * 1024:
            return
        try:
            if len(buffer) > 32 * 1024 * 1024:
                raise ValueError("対応表が大きすぎます。")
            response = self.handle(json.loads(bytes(buffer).split(b"\n", 1)[0]))
        except (ValueError, KeyError, TypeError) as exc:
            response = {"ok": False, "error": str(exc)}
        socket.write(json.dumps(response, ensure_ascii=False).encode() + b"\n")
        socket.disconnectFromServer()

    def closeEvent(self, event):
        self._work_center.shutdown()
        if self._work_center.worker is not None and self._work_center.worker.isRunning():
            event.ignore()
            QTimer.singleShot(150, self.close)
            return
        if self.entries:
            self.hide()
            event.ignore()
        else:
            self._presence_heartbeat.close()
            self._work_bridge.close()
            self.server.close()
            self.lock.unlock()
            event.accept()
            QApplication.quit()


def run_record_center():
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    if request({"action": "show"}, 250).get("ok"):
        return 0
    try:
        window = RecordCenter()
    except RuntimeError:
        return 1
    # The service window is an implementation detail. Each correspondence
    # table is the user-visible window and appears in the unified overview.
    window.hide()
    try:
        return app.exec()
    finally:
        window._presence_heartbeat.close()
        window._work_bridge.close()
