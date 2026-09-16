"""Read-only inspection window for the record table held by File Manager."""

from __future__ import annotations

from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from gui.layout_policy import preferred_window_size

from records.record_bundle import RecordBundle

from .record_linkage import RecordLinkageResult


class RecordBundleViewerDialog(QDialog):
    """Show exactly which immutable record snapshot File Manager received."""

    def __init__(
        self,
        bundle: RecordBundle,
        *,
        linked_field_index: int | None,
        linkage: RecordLinkageResult | None = None,
        source_description: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("PORTA — 受け取った対応表")
        self.resize(preferred_window_size(self))

        layout = QVBoxLayout(self)
        heading = QLabel("対応表・紐づけ結果")
        heading.setStyleSheet("font-size: 16px; font-weight: 600;")
        layout.addWidget(heading)
        if source_description:
            layout.addWidget(QLabel(source_description))

        title = bundle.title or "名称なし"
        layout.addWidget(
            QLabel(
                f"束名：{title}　｜　{len(bundle.rows)}レコード × "
                f"{len(bundle.field_names)}項目"
            )
        )
        if linked_field_index is None:
            linkage_text = "紐づけ項目：未選択（作業一覧の右クリックから選択）"
        else:
            field_name = bundle.field_names[linked_field_index]
            linked_rows = {
                link.row_identifier for link in linkage.links
            } if linkage is not None else set()
            linkage_text = (
                f"紐づけ項目：項目{linked_field_index + 1}「{field_name}」　｜　"
                f"紐づけ済みレコード {len(linked_rows)}件 / "
                f"未紐づけレコード {len(bundle.rows) - len(linked_rows)}件"
            )
            if linkage is not None and linkage.failures:
                linkage_text += f"　｜　対象パス側の失敗 {len(linkage.failures)}件"
        linkage_label = QLabel(linkage_text)
        linkage_label.setWordWrap(True)
        layout.addWidget(linkage_label)

        field_row = QHBoxLayout()
        field_row.addWidget(QLabel("右に表示する項目"))
        self.field_combo = QComboBox()
        for field_index, field_name in enumerate(bundle.field_names):
            self.field_combo.addItem(f"項目{field_index + 1}「{field_name}」", field_index)
        initial_field = linked_field_index if linked_field_index is not None else 0
        self.field_combo.setCurrentIndex(initial_field)
        self.field_combo.setToolTip(
            "レコードの順番は変えず、中央列に表示する対応表の項目だけを切り替えます。"
        )
        field_row.addWidget(self.field_combo, 1)
        layout.addLayout(field_row)

        self.table = QTableWidget(len(bundle.rows), 3)
        self.table.setHorizontalHeaderLabels(("レコード", "表示項目", "紐づけ結果（フルパス）"))
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        links_by_row = {
            link.row_identifier: link for link in linkage.links
        } if linkage is not None else {}
        failure_reasons_by_row: dict[str, str] = {}
        if linkage is not None:
            for failure in linkage.failures:
                if failure.reason == "ambiguous":
                    reason = "紐づけなし（対応表内に同じ値が複数）"
                elif failure.reason == "multiple_paths":
                    reason = "紐づけなし（同名の対象パスが複数）"
                else:
                    continue
                for identifier in failure.matching_row_identifiers:
                    failure_reasons_by_row[identifier] = reason

        for row_index, row in enumerate(bundle.rows):
            record_item = QTableWidgetItem(f"{row_index + 1}")
            record_item.setToolTip(f"内部識別子：{row.identifier}")
            self.table.setItem(row_index, 0, record_item)

            link = links_by_row.get(row.identifier)
            if linked_field_index is None:
                result_text = "未判定（紐づけ項目が未選択）"
                result_color = QColor("#eeeeee")
            elif link is not None:
                result_text = str(link.source)
                result_color = QColor("#dff3e4")
            else:
                result_text = failure_reasons_by_row.get(
                    row.identifier, "紐づけなし（完全一致する対象なし）"
                )
                result_color = QColor("#f8dddd")
            result_item = QTableWidgetItem(result_text)
            result_item.setToolTip(result_text)
            result_item.setBackground(QBrush(result_color))
            self.table.setItem(row_index, 2, result_item)

        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table, 1)
        self._bundle = bundle
        self._linked_field_index = linked_field_index
        self.field_combo.currentIndexChanged.connect(self._show_field)
        self._show_field(initial_field)

        note = QLabel(
            "この画面は開いた時点の情報を表示する独立したスナップショットです。"
            "ファイルマネージャー側で紐づけや名前を変更した後は、閉じてもう一度開くと更新されます。"
            "ここではセルを書き換えません。"
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        close_button = QPushButton("閉じる")
        close_button.clicked.connect(self.close)
        buttons.addWidget(close_button)
        layout.addLayout(buttons)

    def _show_field(self, combo_index: int) -> None:
        """Keep record rows fixed while replacing the one visible value column."""
        field_index = self.field_combo.itemData(combo_index)
        if not isinstance(field_index, int):
            return
        field_name = self._bundle.field_names[field_index]
        self.table.setHorizontalHeaderItem(
            1, QTableWidgetItem(f"項目{field_index + 1}「{field_name}」")
        )
        for row_index, row in enumerate(self._bundle.rows):
            value = row.values[field_index]
            item = QTableWidgetItem(value)
            item.setToolTip(value)
            if field_index == self._linked_field_index:
                item.setBackground(QBrush(QColor("#fff1c9")))
            self.table.setItem(row_index, 1, item)
