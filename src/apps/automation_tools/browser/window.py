"""Select an exact connected Firefox tab, inspect it and execute finite steps."""
from __future__ import annotations

import json
import uuid

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFileDialog, QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QPushButton,
    QTreeWidget, QTreeWidgetItem, QWidget,
)

from gui import AppHeader, AppPageLayout
from gui.flow_layout import FlowLayout
from automation.browser.cartridge import MAX_BYTES, parse_cartridge
from .editor import cartridge_directory
from automation.browser.transport import discover, request
from .worker import BrowserWorker


class BrowserScreen(QWidget):
    def __init__(self, back):
        super().__init__()
        self._worker = None
        self._cartridge = None
        self._target = None
        self._run_id = None
        self._next_step = 0
        self._continuous = False
        self._stop = False
        self._pending_action = None
        self._job = ""
        layout = AppPageLayout(self)
        layout.addWidget(AppHeader(back, title="Firefox・カートリッジ使用"))
        hint = QLabel("各Firefoxの拡張機能から接続してください。接続先 → ウィンドウ → タブの順に表示します。\n"
                      "候補更新は接続済みFirefoxだけを列挙します。未導入・切断中・閲覧権限のない候補は取得できません。")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["接続先 / ウィンドウ / タブ", "URL", "状態・識別ID"])
        self.tree.setTextElideMode(Qt.TextElideMode.ElideNone)
        self.tree.header().setStretchLastSection(False)
        for column in range(3):
            self.tree.header().setSectionResizeMode(column, self.tree.header().ResizeMode.ResizeToContents)
        self.tree.itemSelectionChanged.connect(self._selection_changed)
        layout.addWidget(self.tree, 1)
        actions = FlowLayout()
        self.refresh_button = self._button(actions, "Firefox候補を更新", self.refresh)
        self.focus_button = self._button(actions, "選択タブを前面で確認", self.focus_target)
        self.load_button = self._button(actions, "カートリッジを読み込む", self.load)
        layout.addLayout(actions)
        self.selection_label = QLabel("操作対象：未選択")
        self.selection_label.setTextFormat(Qt.TextFormat.PlainText)
        self.selection_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.selection_label.setWordWrap(True)
        layout.addWidget(self.selection_label)
        self.selector = QLineEdit()
        self.selector.setPlaceholderText("調べるCSS selector（例：input[name=q]、main a[href]）")
        layout.addWidget(self.selector)
        controls = FlowLayout()
        self.inspect_button = self._button(controls, "要素を調べる（操作なし）", self.inspect)
        self.prepare_button = self._button(controls, "対象を固定して準備", self.prepare)
        self.step_button = self._button(controls, "次の1手順を実行", lambda: self.advance(False))
        self.run_button = self._button(controls, "残りを連続実行", lambda: self.advance(True))
        self.stop_button = self._button(controls, "停止・対象の固定を解除", self.stop)
        layout.addLayout(controls)
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setPlaceholderText("読み込んだ全手順と入力値を表示します。読み込みだけでは実行しません。")
        layout.addWidget(self.preview, 1)
        self.result = QPlainTextEdit()
        self.result.setReadOnly(True)
        self.result.setPlaceholderText("抽出結果・実行した手順・停止理由。明示保存するまでメモリ内だけです。")
        layout.addWidget(self.result, 1)
        save_actions = FlowLayout()
        self._button(save_actions, "結果を名前を付けて保存", self.save_results)
        layout.addLayout(save_actions)
        self.status = QLabel("未接続。導入手順は docs/browser-automation.md を参照してください。")
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self._buttons()

    @staticmethod
    def _button(layout, title, callback):
        button = QPushButton(title)
        button.clicked.connect(callback)
        layout.addWidget(button)
        return button

    def _buttons(self):
        busy = self._worker is not None
        locked = self._run_id is not None
        self.refresh_button.setEnabled(not busy and not locked)
        self.load_button.setEnabled(not busy and not locked)
        self.tree.setEnabled(not busy and not locked)
        self.selector.setEnabled(not busy and not locked)
        selected = self.selected_target() is not None
        self.focus_button.setEnabled(selected and not busy and not locked)
        self.inspect_button.setEnabled(selected and not busy and not locked)
        self.prepare_button.setEnabled(selected and self._cartridge is not None and not busy and not locked)
        ready = locked and not busy and not self._stop
        self.step_button.setEnabled(ready)
        self.run_button.setEnabled(ready)
        self.stop_button.setEnabled(locked)

    def _start(self, job, operation):
        if self._worker is not None:
            return
        self._job = job
        self._worker = BrowserWorker(operation, self)
        self._worker.succeeded.connect(self._success)
        self._worker.failed.connect(self._failure)
        self._worker.finished.connect(self._finished)
        self._buttons()
        self._worker.start()

    def selected_target(self):
        item = self.tree.currentItem()
        return item.data(0, Qt.ItemDataRole.UserRole) if item is not None else None

    def _selection_changed(self):
        target = self.selected_target()
        self.selection_label.setText("操作対象：未選択" if target is None else
            f"{target['label']} / ウィンドウ{target['windowId']} / タブ{target['tabId']}\n{target['title']}\n{target['url']}")
        if hasattr(self, "status"):
            self._buttons()

    def refresh(self):
        self.status.setText("接続されているFirefoxを列挙しています…")
        self._start("inventory", discover)

    def focus_target(self):
        target = self.selected_target()
        if target:
            self._start("focus", lambda: request(target["endpoint"], {"op": "focus", "target": target}))

    def load(self):
        path, _ = QFileDialog.getOpenFileName(self, "カートリッジを読み込む", str(cartridge_directory()), "JSON (*.json)")
        if not path:
            return
        try:
            with open(path, "rb") as stream:
                raw = stream.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES:
                raise ValueError("256KiBを超えています。")
            data = parse_cartridge(raw.decode("utf-8"))
            self._cartridge = data
            self.preview.setPlainText(json.dumps(data, ensure_ascii=False, indent=2))
            self.status.setText(f"{data['name']}：{len(data['steps'])}手順。形式確認済み・サイト上の動作は未確認です。")
        except (ValueError, OSError) as error:
            self.status.setText(str(error))
        self._buttons()

    def prepare(self):
        target = self.selected_target()
        if target is None or self._cartridge is None:
            return
        if target["url"] != self._cartridge["steps"][0]["url"]:
            self.status.setText("起点タブのURLと最初の手順のURLが一致しません。候補を更新するか、定義を確認してください。")
            return
        self._target = dict(target)
        self._run_id = uuid.uuid4().hex
        self._next_step = 0
        self._continuous = False
        self._stop = False
        command = self._command("begin")
        command.update(url=target["url"], origins=self._cartridge["origins"])
        self._start("begin", lambda: request(target["endpoint"], command))

    def _command(self, op):
        return {"op": op, "target": self._target, "runId": self._run_id}

    def advance(self, continuous):
        if not self._run_id or self._worker is not None or self._stop:
            return
        remaining = self._cartridge["steps"][self._next_step:]
        checked = remaining if continuous else remaining[:1]
        if any(step["action"] in {"fill", "click"} for step in checked):
            if QMessageBox.question(self, "入力・クリックの実行", "選択タブに、表示中の定義どおり入力・クリックします。\n"
                "入力だけで保存・送信されるサイトもあります。内容と対象を確認しましたか？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
                return
        self._continuous = continuous
        self._execute_step()

    def _execute_step(self):
        if self._stop or self._next_step >= len(self._cartridge["steps"]):
            self._release()
            return
        step = self._cartridge["steps"][self._next_step]
        command = self._command("step")
        command["step"] = step
        endpoint = self._target["endpoint"]
        self.status.setText(f"手順{self._next_step + 1}: {step['action']} を実行中")
        self._start("step", lambda: request(endpoint, command))

    def stop(self):
        self._stop = True
        self._continuous = False
        self.status.setText("停止要求済み。送出済みの1手順の応答を待ち、後続を止めます。最大35秒です。")
        if self._worker is None:
            self._release()

    def _release(self):
        if not self._run_id:
            return
        command = self._command("end")
        endpoint = self._target["endpoint"]
        self._start("end", lambda: request(endpoint, command, timeout=3))

    def inspect(self):
        target = self.selected_target()
        selector = self.selector.text().strip()
        if target is None or not selector:
            self.status.setText("タブとCSS selectorを指定してください。")
            return
        run_id = uuid.uuid4().hex
        from automation.browser.cartridge import web_origin
        try:
            origin = web_origin(target["url"])
        except ValueError as error:
            self.status.setText(str(error))
            return

        def operation():
            base = {"target": target, "runId": run_id}
            request(target["endpoint"], {**base, "op": "begin", "url": target["url"], "origins": [origin]})
            try:
                return request(target["endpoint"], {**base, "op": "inspect", "step": {
                    "action": "extract", "url": target["url"], "selector": selector, "many": True}})
            finally:
                request(target["endpoint"], {**base, "op": "end"}, timeout=3)
        self._start("inspect", operation)

    def _success(self, result):
        if self._job == "inventory":
            sessions, errors = result
            self.tree.clear()
            for session in sessions:
                root = QTreeWidgetItem(self.tree, [session["label"], "", "接続 " + session["connectionId"][:12] + " / プロファイル " + session["profileId"][:12]])
                for window in session["windows"]:
                    states = ["プライベート" if window["incognito"] else "通常", window["state"]]
                    if window["focused"]:
                        states.append("前面")
                    branch = QTreeWidgetItem(root, [f"ウィンドウ {window['id']}（{len(window['tabs'])}タブ）", "", " / ".join(states)])
                    for tab in window["tabs"]:
                        state = f"ID {tab['id']}" + (" / 選択中" if tab["active"] else "") + (" / 固定" if tab["pinned"] else "")
                        if not tab["supported"]:
                            state += " / 操作対象外"
                        leaf = QTreeWidgetItem(branch, [f"{tab['index'] + 1}. {tab['title']}", tab["url"], state])
                        if tab["supported"]:
                            leaf.setData(0, Qt.ItemDataRole.UserRole, {"endpoint": session["endpoint"],
                                "connectionId": session["connectionId"], "windowId": window["id"], "tabId": tab["id"],
                                "url": tab["url"], "title": tab["title"], "label": session["label"]})
            self.tree.expandAll()
            self.status.setText(f"接続済みFirefox：{len(sessions)}候補。未導入・権限外の候補は含みません。" + ("\n" + "\n".join(errors) if errors else ""))
        elif self._job == "begin":
            self.result.appendPlainText(f"対象固定: {self._target['label']} / タブ{self._target['tabId']}\n{result['url']}")
            self.status.setText("準備できました。次の1手順、または残りを連続実行できます。操作権は10分で失効します。")
        elif self._job == "step":
            self.result.appendPlainText(f"手順{self._next_step + 1}: " + json.dumps(result, ensure_ascii=False, indent=2))
            self._next_step += 1
            self.status.setText(f"{self._next_step}/{len(self._cartridge['steps'])}手順の応答を受信しました。")
            if self._continuous or self._next_step >= len(self._cartridge["steps"]):
                self._pending_action = self._execute_step
        elif self._job == "end":
            self._run_id = None
            self.status.setText("停止して対象の固定を解除しました。" if self._stop else "全手順の処理を終了しました。サイト側の確定は結果を確認してください。")
        else:
            self.result.appendPlainText(json.dumps(result, ensure_ascii=False, indent=2))

    def _failure(self, message):
        self._continuous = False
        self._stop = True
        self.status.setText("停止: " + message)
        self.result.appendPlainText("停止: " + message + "\n送出済みの入力・クリックは取り消していません。応答不明ならサイト側を確認してください。")
        if self._job in {"begin", "end"}:
            self._run_id = None
        elif self._run_id:
            self._pending_action = self._release

    def _finished(self):
        worker = self._worker
        self._worker = None
        if worker:
            worker.deleteLater()
        pending, self._pending_action = self._pending_action, None
        self._buttons()
        if self._stop and self._run_id:
            self._release()
        elif pending:
            pending()

    def save_results(self):
        path, _ = QFileDialog.getSaveFileName(self, "表示中の結果を保存", "browser-results.txt", "Text (*.txt)")
        if not path:
            return
        from PySide6.QtCore import QSaveFile, QIODevice
        output = QSaveFile(path)
        raw = self.result.toPlainText().encode("utf-8")
        if not output.open(QIODevice.OpenModeFlag.WriteOnly) or output.write(raw) != len(raw) or not output.commit():
            self.status.setText("保存できません: " + output.errorString())

    def closeEvent(self, event):
        if self._worker is not None or self._run_id:
            self.stop()
            event.ignore()
            return
        super().closeEvent(event)

    def shutdown(self):
        if self._worker is not None or self._run_id:
            self.stop()

    def describe_work_state(self):
        if self._run_id:
            return {"level": 3, "reason": "対象タブを固定し、実行位置を保持しています。"}
        if self._cartridge is not None or self.result.toPlainText():
            return {"level": 3, "reason": "操作定義または取得結果を保持しています。"}
        if self.selected_target() is not None or self.selector.text():
            return {"level": 2, "reason": "タブ・要素を選択しています。"}
        return {"level": 1, "reason": "操作するカートリッジは未読込です。"}
