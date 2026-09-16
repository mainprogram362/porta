"""One independent application screen per process, with reconnectable control."""
from __future__ import annotations

import json
import os
import sys
from uuid import uuid4

from PySide6.QtCore import QSize, QTimer, Qt
from PySide6.QtWidgets import QApplication, QMainWindow, QMessageBox, QPushButton, QLabel, QInputDialog, QSizePolicy, QStyle

from runtime.work_process import ROOT, MAX_BYTES, directory, validate_command, spawn, open_manager
from runtime.single_instance import MainInstance
from runtime.instance_presence import InstancePresence
from gui.process_tracking import PresenceHeartbeat
from gui.current_page_stack import ScrollablePageStack
from gui.layout_policy import preferred_window_size, usable_window_floor
from gui.wrapping_toolbar import WrappingToolBar
from gui.work_state import describe_work, STATE_NAMES
from gui.app_header import AppHeader
from gui.work_lifecycle import running_work, dependent_windows
from gui.composites.path_support import open_in_standard_file_manager
from .catalog import app_for_key
from .launch_requests import activate_window


def launch_app(key):
    from runtime.transient_paths import take_media_paths, take_video_encode_paths
    paths = take_media_paths() if key == "media_information" else take_video_encode_paths() if key == "video_encoder" else ()
    return spawn({"app": key, "paths": list(paths)})


def launch_intent(intent):
    keys = {"file-manager": "file_manager", "media-organizer": "media_information", "video-encoder": "video_encoder"}
    return spawn({"app": keys[intent.target], "paths": [str(p) for p in intent.paths], "action": intent.action})


class WorkWindow(QMainWindow):
    def __init__(self, payload):
        super().__init__()
        self.work_id = uuid4().hex
        self.transfer_id = payload.get("transfer_id")
        if self.transfer_id is not None and (not isinstance(self.transfer_id, str) or len(self.transfer_id) > 64):
            raise ValueError("作業移動の識別情報が不正です。")
        self.kept = False
        self._closing = False
        self.key = payload.get("app", "external_choose")
        definition = app_for_key(self.key)
        if definition is None and self.key != "external_choose":
            raise ValueError("未対応の作業です。")
        self.title = definition.title if definition else "受信したパスの操作"
        paths = payload.get("paths", [])
        if not isinstance(paths, list) or not all(isinstance(p, str) for p in paths):
            raise ValueError("引き継ぐパスの形式が不正です。")
        from runtime.transient_paths import offer_media_paths, offer_video_encode_paths
        if self.key == "media_information":
            offer_media_paths(paths)
        if self.key == "video_encoder":
            offer_video_encode_paths(paths)
        if definition:
            self.work_screen = definition.create_screen(open_manager)
        else:
            from .external_open import ExternalOpenChooserScreen
            from pathlib import Path
            self.work_screen = ExternalOpenChooserScreen(tuple(Path(p) for p in paths), launch_intent, open_manager)
        from apps.file_tools.file_manager import FileManagerScreen
        from apps.text_tools.text_workbench import TextWorkbenchScreen
        if isinstance(self.work_screen, FileManagerScreen):
            self.work_screen.set_open_media_tool_callback(launch_app)
            if paths:
                self.work_screen.receive_external_paths(paths, action=payload.get("action", "browse"))
            if payload.get("record_id"):
                self.work_screen.acquire_record(payload["record_id"])
        if isinstance(self.work_screen, TextWorkbenchScreen):
            self.work_screen.set_record_bundle_callback(self.open_record)
            if "text" in payload:
                self.work_screen.receive_text(payload["text"])
        if self.key == "porta_control" and payload.get("section") is not None:
            section = payload["section"]
            if section not in {"settings", "environment"}:
                raise ValueError("未対応のPORTA管理画面です。")
            from apps.porta_control.configuration import create_screen as settings
            from apps.porta_control.diagnostics.environment_check import create_screen as environment
            self.work_screen.open_section(section, {"settings": settings, "environment": environment}[section])
        stack = ScrollablePageStack()
        stack.addWidget(self.work_screen)
        stack.setCurrentWidget(self.work_screen)
        self.setCentralWidget(stack)
        self.resize(preferred_window_size(self))
        self.setMinimumSize(usable_window_floor(self))
        toolbar = WrappingToolBar("この作業", self)
        toolbar.setMovable(False)
        toolbar.setFloatable(False)
        self.label = QLabel()
        self.label.setTextFormat(Qt.TextFormat.PlainText)
        self.statusBar().addWidget(self.label, 1)
        self.reason_label = QLabel()
        self.reason_label.setTextFormat(Qt.TextFormat.PlainText)
        # A wrapped permanent status-bar widget makes QMainWindow give it a
        # share of the entire window height.  Keep the compact summary on one
        # line and expose the complete reason through its tooltip.
        self.reason_label.setWordWrap(False)
        self.reason_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.reason_label.setMinimumWidth(0)
        self.statusBar().addWidget(self.reason_label, 3)
        from apps.porta_control.work_overview import WorkCenter
        self._work_center = WorkCenter(self)
        for name, callback in (("PORTAの画面", self.show_work_overview), ("メニュー", open_manager),
                               ("名前", self.rename), ("この作業を閉じる", self.close)):
            button = QPushButton(name)
            button.clicked.connect(callback)
            toolbar.add_control(button)
        self.keep_button = QPushButton("残す")
        self.keep_button.setCheckable(True)
        self.keep_button.toggled.connect(self.set_kept)
        toolbar.add_control(self.keep_button)
        for header in self.work_screen.findChildren(AppHeader):
            header.use_work_toolbar(toolbar)
        self.explorer_button = QPushButton()
        self.explorer_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DirOpenIcon))
        self.explorer_button.setIconSize(QSize(18, 18))
        self.explorer_button.setAccessibleName("ファイルエクスプローラーを開く")
        self.explorer_button.setToolTip("ファイルエクスプローラーをホームフォルダで開きます。")
        self.explorer_button.setFixedSize(34, 34)
        self.explorer_button.clicked.connect(lambda _checked=False: open_in_standard_file_manager())
        toolbar.add_control(self.explorer_button)
        self.addToolBar(toolbar)
        self.presence = InstancePresence(role="work", screen=self.title, state="状態を確認中")
        self.heartbeat = PresenceHeartbeat(self.presence, self)
        self.server = MainInstance(ROOT / self.work_id, self, directory=directory(), validator=validate_command)
        self._management_error = ""
        try:
            if not self.server.start_or_forward({"version": 1, "op": "status"}):
                raise ValueError("作業IDが重複しました。")
            self.server.set_handler(self.command)
        except (OSError, RuntimeError, ValueError) as error:
            self.server.close()
            self._management_error = "管理一覧に登録できません: " + str(error)
        self.timer = QTimer(self)
        self.timer.setInterval(2000)
        self.timer.timeout.connect(self.update_state)
        self.timer.start()
        self.update_state()

    def show_work_overview(self):
        self._work_center.stopping = False
        self._work_center.show()
        self._work_center.raise_()
        self._work_center.activateWindow()

    def set_kept(self, value):
        self.kept = value
        self.update_state()

    def rename(self):
        value, accepted = QInputDialog.getText(self, "作業の名前", "名前", text=self.title)
        if accepted and value.strip():
            self.title = value.strip()[:200]
            self.update_state()

    def update_state(self):
        state = describe_work(self.work_screen)
        if self._management_error:
            state = {"level": None, "reason": self._management_error + "。このウィンドウの作業は利用できます。"}
        level = state["level"]
        self.label.setText(("残す · " if self.kept else "") + STATE_NAMES.get(level, "状態不明"))
        self.statusBar().setToolTip(state["reason"] + f"\nPID {os.getpid()} / 作業 {self.work_id}")
        self.label.setToolTip(state["reason"])
        self.reason_label.setText(state["reason"][:160])
        self.reason_label.setToolTip(state["reason"])
        self.setWindowTitle(f"PORTA — {self.title} · {self.work_id[:8]}")
        # User titles can contain document names; keep them out of the disk registry.
        self.presence.update(self.key, f"作業 {self.work_id[:8]} · " + (f"段階{level}" if level else "状態不明"))

    def command(self, request):
        if request["op"] == "focus":
            activate_window(self)
        elif request["op"] == "close":
            QTimer.singleShot(0, self.close)
        return {"version": 1, "id": self.work_id, "app": self.key, "title": self.title,
                "transfer_id": self.transfer_id,
                "pid": os.getpid(), "kept": self.kept, "host": "window",
                **describe_work(self.work_screen)}

    def open_record(self, bundle=None):
        from records import record_service
        if bundle is not None:
            record_service.create(bundle)
        else:
            self.show_work_overview()

    def closeEvent(self, event):
        if self._closing:
            event.ignore()
            return
        self._closing = True
        try:
            self._work_center.shutdown()
            if self._work_center.worker is not None and self._work_center.worker.isRunning():
                event.ignore()
                QTimer.singleShot(150, self.close)
                return
            activate_window(self)
            if QApplication.activeModalWidget() is not None:
                event.ignore()
                return
            if running_work(self.work_screen) or dependent_windows(self.work_screen):
                QMessageBox.information(self, "作業を継続しています", "処理を完了・停止し、補助画面を閉じてから終了してください。")
                event.ignore()
                return
            state = describe_work(self.work_screen)
            if state["level"] == 4:
                QMessageBox.information(self, "処理実行中", state["reason"])
                event.ignore()
                return
            if QMessageBox.question(self, "この作業を閉じる", self.title + "\n" + state["reason"] +
                                    "\nこの作業の入力・途中結果を破棄して閉じますか？",
                                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                    QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            if running_work(self.work_screen) or dependent_windows(self.work_screen) or describe_work(self.work_screen)["level"] == 4:
                event.ignore()
                return
            shutdown = getattr(self.work_screen, "shutdown", None)
            if callable(shutdown):
                shutdown()
            if running_work(self.work_screen) or not self.work_screen.close():
                event.ignore()
                return
            self.timer.stop()
            self.server.close()
            self.heartbeat.close()
            event.accept()
        finally:
            self._closing = False


def run_work(raw=None):
    app = QApplication.instance() or QApplication([])
    app.setApplicationName("PORTA")
    window = None
    try:
        if raw is None:
            raw = sys.stdin.buffer.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ValueError("作業の引き継ぎが大きすぎます。")
        payload = json.loads(raw)
        if not isinstance(payload, dict) or type(payload.get("version")) is not int or payload["version"] != 1:
            raise ValueError("未対応の作業起動形式です。")
        window = WorkWindow(payload)
        window.show()
        return app.exec()
    except Exception as error:
        QMessageBox.critical(None, "作業を開けません", str(error))
        return 1
    finally:
        if window is not None:
            window.server.close()
            window.heartbeat.close()
