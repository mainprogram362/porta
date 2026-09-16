"""Author cartridges without executing them or claiming they are site-verified."""
import json
from pathlib import Path

from PySide6.QtWidgets import QFileDialog, QLabel, QPushButton, QPlainTextEdit, QWidget

from settings.user_space import configured_paths
from gui import AppHeader, AppPageLayout
from gui.flow_layout import FlowLayout
from automation.browser.cartridge import MAX_BYTES, parse_cartridge, template


def cartridge_directory():
    paths = configured_paths()
    return paths.local_data / "browser_cartridges" if paths else Path.home()


class CartridgeEditor(QWidget):
    def __init__(self, back):
        super().__init__()
        layout = AppPageLayout(self)
        layout.addWidget(AppHeader(back, title="ブラウザ・カートリッジ作成"))
        explanation = QLabel(
            "JSONを編集して形式を確認し、明示保存します。サイト上での動作確認とは別です。\n"
            "使用画面の『要素を調べる』でCSS selectorの一致件数を確認できます。\n"
            "action: check / extract / fill / click / wait_page。urlは完全一致です。\n"
            "fillはvalue、extractはattribute（text・href・src・value）を指定します。\n"
            "複数抽出はmany: true、limitは最大500。wait_pageはclick直後のみ、timeoutは最大30秒です。"
        )
        explanation.setWordWrap(True)
        layout.addWidget(explanation)
        self.editor = QPlainTextEdit(json.dumps(template(), ensure_ascii=False, indent=2))
        self._initial_text = self.editor.toPlainText()
        layout.addWidget(self.editor, 1)
        actions = FlowLayout()
        for title, callback in (("読み込む", self.load), ("雛形を末尾へ追加", self.add_step),
                                ("形式を確認", self.validate), ("名前を付けて保存", self.save)):
            button = QPushButton(title)
            button.clicked.connect(callback)
            actions.addWidget(button)
        layout.addLayout(actions)
        self.status = QLabel("未保存。入力内容や認証情報は、保存ボタンを押すとファイルに含まれます。")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

    def validate(self):
        try:
            data = parse_cartridge(self.editor.toPlainText())
            self.status.setText(f"形式確認済み：{data['name']} / {len(data['steps'])}手順。サイト適合性は未確認です。")
            return data
        except (ValueError, TypeError) as error:
            self.status.setText(str(error))
            return None

    def describe_work_state(self):
        if self.editor.toPlainText() != self._initial_text:
            return {"level": 3, "reason": "編集したカートリッジがあります。保存状況を確認してください。"}
        return {"level": 1, "reason": "初期の雛形を表示しています。"}

    def load(self):
        path, _ = QFileDialog.getOpenFileName(self, "カートリッジを読み込む（現在の編集内容を置換）", str(cartridge_directory()), "JSON (*.json)")
        if not path:
            return
        try:
            with open(path, "rb") as stream:
                raw = stream.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES:
                raise ValueError("256KiBを超えています。")
            text = raw.decode("utf-8")
            parse_cartridge(text)
            self.editor.setPlainText(text)
            self.status.setText(f"読み込みました: {path}")
        except (OSError, ValueError) as error:
            self.status.setText(str(error))

    def add_step(self):
        data = self.validate()
        if data is not None:
            data["steps"].append({"action": "check", "url": data["steps"][-1]["url"], "selector": "h1"})
            self.editor.setPlainText(json.dumps(data, ensure_ascii=False, indent=2))

    def save(self):
        data = self.validate()
        if data is None:
            return
        path, _ = QFileDialog.getSaveFileName(self, "カートリッジを保存", str(cartridge_directory() / "cartridge.json"), "JSON (*.json)")
        if not path:
            return
        try:
            # QFileDialog supplies explicit replacement confirmation.
            from PySide6.QtCore import QSaveFile, QIODevice
            output = QSaveFile(path)
            raw = (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
            if not output.open(QIODevice.OpenModeFlag.WriteOnly) or output.write(raw) != len(raw) or not output.commit():
                raise OSError(output.errorString())
            self.status.setText(f"保存しました: {path}")
        except OSError as error:
            self.status.setText(str(error))
