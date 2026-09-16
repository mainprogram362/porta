"""Read-only view of PORTA processes and their current activities."""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QCheckBox, QDialog, QHBoxLayout, QLabel, QPushButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout

from runtime.process_registry import get_registry, PORTA_ROOT

from runtime.process_control import process_inventory
from gui.layout_policy import preferred_window_size

_ROLES = {'main': '管理本体', 'work': '独立した作業', 'records': '一時対応表', 'worker': '内部処理',
          'external': '外部プログラム', 'descendant': '関連プロセス', 'unregistered': '未登録の本体'}
_HEALTH = {'alive': '起動中', 'stopped': '一時停止', 'unknown': '生存確認不可',
           'unresponsive': '画面の応答を未確認', 'exited': '終了済み'}


class ProcessCenterDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle('PORTA — プロセス詳細')
        self.resize(preferred_window_size(self))
        layout = QVBoxLayout(self)
        explanation = QLabel('PORTA画面・対応表サービス・内部処理の診断情報を表示します。実行履歴は残しません。\n'
                             '外部プログラムの独立した常駐先は追跡できない場合があります。')
        explanation.setWordWrap(True)
        layout.addWidget(explanation)
        actions = QHBoxLayout()
        self.all_roots = QCheckBox('別の配置場所のPORTAも表示')
        self.all_roots.toggled.connect(self.refresh)
        actions.addWidget(self.all_roots)
        actions.addStretch(1)
        refresh = QPushButton('更新')
        refresh.clicked.connect(self.refresh)
        actions.addWidget(refresh)
        layout.addLayout(actions)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(('PID', '役割', '画面・処理', '状態', '実行中の作業', '起動元PID', 'PORTA配置場所'))
        self.tree.setRootIsDecorated(False)
        self.tree.setAlternatingRowColors(True)
        header = self.tree.header()
        for column in (0, 1, 3, 5):
            header.setSectionResizeMode(column, header.ResizeMode.ResizeToContents)
        for column in (2, 4, 6):
            header.setSectionResizeMode(column, header.ResizeMode.Stretch)
        layout.addWidget(self.tree)
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self.refresh)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.refresh()
        self._timer.start()

    def hideEvent(self, event) -> None:
        self._timer.stop()
        super().hideEvent(event)

    def refresh(self) -> None:
        selected = self.tree.currentItem()
        selected_id = selected.data(0, Qt.ItemDataRole.UserRole) if selected else None
        scroll = self.tree.verticalScrollBar().value()
        try:
            items = process_inventory(root=None if self.all_roots.isChecked() else PORTA_ROOT)
        except OSError as exc:
            self.status.setText(f'一覧を更新できません: {exc}')
            return
        self.tree.clear()
        for process in items:
            state = _HEALTH[process.health]
            if process.state:
                state += f' / {process.state}'
            if process.owner != process.identity and process.owner_health == 'exited':
                state += '（起動元は終了）'
            if process.ownership == 'external':
                state += '（外部管理）'
            row = QTreeWidgetItem(self.tree, (str(process.pid), _ROLES.get(process.role, process.role),
                                  process.screen or process.label, state, ' / '.join(process.activities),
                                  str(process.owner.pid) if process.owner != process.identity else '—', process.root))
            row.setData(0, Qt.ItemDataRole.UserRole, process.identity.key)
            if process.identity.key == selected_id:
                self.tree.setCurrentItem(row)
        self.tree.verticalScrollBar().setValue(scroll)
        error = get_registry().error
        self.status.setText(error or f'{len(items)} プロセス。約1秒ごとに更新します。未登録の本体は再起動すると画面・作業情報も表示されます。')
