"""Separate part editing from explicit main-ledger management."""

from __future__ import annotations

import json
from collections.abc import Callable

from PySide6.QtCore import QRegularExpression, Qt
from PySide6.QtGui import QDoubleValidator, QRegularExpressionValidator
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QStackedWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from gui import AppHeader, AppPageLayout, PathLineInput, PathListInput
from media.catalog import CatalogAttribute
from media.file_attributes import (
    STANDARD_CATALOG_FIELDS,
    catalog_field_for_key,
    parse_catalog_field_value,
    preset_values_for_catalog_field,
)
from media.ledger import (
    COLLECTION_STATUSES,
    LedgerWork,
    MediaLedger,
    MediaPart,
    MediaPartsDocument,
    add_work,
    adopt_parts,
    attribute_value,
    empty_ledger,
    load_ledger,
    load_parts,
    merge_parts,
    replace_attribute,
    replace_part_attributes,
    replace_parts_attribute,
    replace_work_attributes,
    save_ledger,
    save_parts,
    work_for_id,
)
from media.review_patch import (
    MediaReviewPatch,
    ReviewPatchMergePlan,
    apply_review_patch_merge,
    load_review_patch,
    plan_review_patch_merge,
    review_patch_merge_summary,
)


def _display(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return " / ".join(str(item) for item in value)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return str(value)


def _value(attributes: tuple[CatalogAttribute, ...], key: str) -> str:
    return _display(attribute_value(attributes, key))


def _part_title(part: MediaPart) -> str:
    return _value(part.attributes, "title.official") or _value(part.attributes, "file.name.observed") or "（名称未設定）"


def _work_title(work: LedgerWork) -> str:
    return _value(work.attributes, "title.official") or next(
        (_value(file.attributes, "file.name.observed") for file in work.files if _value(file.attributes, "file.name.observed")),
        "（名称未設定）",
    )


def _attribute_meaning(key: str) -> str:
    """Return a human label without hiding the durable machine-readable key."""
    field = catalog_field_for_key(key)
    return field.label if field is not None else ""


class MediaLedgerScreen(QWidget):
    """Main-ledger workbench; ID-free part editing lives in Media Information."""

    def __init__(self, return_to_main: Callable[[], None]) -> None:
        super().__init__()
        self._return_to_main = return_to_main
        self._parts: list[MediaPart] = []
        self._selected_part_index: int | None = None
        self._review_patch: MediaReviewPatch | None = None
        self._review_merge_plan: ReviewPatchMergePlan | None = None
        self._review_merge_parts_snapshot: tuple[MediaPart, ...] | None = None
        self._review_patch_report_text = ""
        self._pending_parts: tuple[MediaPart, ...] = ()
        self._ledger: MediaLedger | None = None
        self._selected_work_id: str | None = None
        # The side-by-side item editor contains two independent edit scopes.
        # Keep both fully visible at the launcher minimum instead of clipping
        # controls below the fold on a 680px-tall window.
        self.setMinimumSize(1080, 780)
        self._build_ui()
        self._refresh_parts()
        self._refresh_ledger()

    def _build_ui(self) -> None:
        layout = AppPageLayout(self)
        header = AppHeader(self._return_to_main, title="メディア本台帳")
        header.content_layout.addStretch(1)
        layout.addWidget(header)
        layout.addWidget(
            QLabel(
                "本台帳（作品ID・ファイルID） — パーツの作成・共通編集・評価パッチ結合は「メディア情報整理」で行います。"
            )
        )

        self.stack = QStackedWidget()
        # Keep the former page instantiated during this migration so older
        # in-memory callers remain valid, but do not expose two competing
        # editing destinations in the normal UI.  New parts enter below as a
        # read-only import candidate for the main ledger.
        self.stack.addWidget(self._build_parts_page())
        self.stack.addWidget(self._build_ledger_page())
        self.stack.setCurrentIndex(1)
        layout.addWidget(self.stack, 1)

        self.status_label = QLabel("本台帳を開くか作成し、メディア情報整理で作ったパーツJSONを明示的に追加できます。")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)
    def _build_parts_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        source_box = QGroupBox("読み込むパーツJSON（複数可）")
        source_layout = QVBoxLayout(source_box)
        self.parts_paths = PathListInput(rows=2, accepted_path_kind="all", show_controls=False)
        self.parts_paths.setToolTip("メディア情報整理のJSON、またはIDなしのパーツJSONを複数追加できます。")
        source_layout.addWidget(self.parts_paths)
        source_actions = QHBoxLayout()
        load_button = QPushButton("パーツを読み込む")
        load_button.clicked.connect(self.load_part_sources)
        source_actions.addWidget(load_button)
        clear_button = QPushButton("一覧を空にする")
        clear_button.clicked.connect(self.parts_paths.clear_items)
        source_actions.addWidget(clear_button)
        source_actions.addStretch(1)
        source_layout.addLayout(source_actions)
        layout.addWidget(source_box)

        workspace = QHBoxLayout()
        workspace.setSpacing(8)
        list_box = QGroupBox("パーツ一覧（IDなし）")
        list_box.setMinimumWidth(250)
        list_layout = QVBoxLayout(list_box)
        self.part_tree = QTreeWidget()
        self.part_tree.setHeaderLabels(["候補", "タイトル", "ファイル名"])
        self.part_tree.setRootIsDecorated(False)
        self.part_tree.setAlternatingRowColors(True)
        self.part_tree.setColumnWidth(0, 52)
        self.part_tree.itemSelectionChanged.connect(self._part_selected)
        list_layout.addWidget(self.part_tree, 1)
        workspace.addWidget(list_box, 2)

        editor_box = QGroupBox("パーツ編集")
        editor_box.setMinimumWidth(760)
        editor_layout = QHBoxLayout(editor_box)
        editor_layout.setSpacing(8)

        individual_column = QVBoxLayout()
        self.part_detail_label = QLabel("パーツを選択してください。")
        self.part_detail_label.setStyleSheet("font-weight: bold;")
        individual_column.addWidget(self.part_detail_label)
        self.part_attribute_tree = QTreeWidget()
        self.part_attribute_tree.setHeaderLabels(["項目", "意味", "値", "型", "出所"])
        self.part_attribute_tree.setRootIsDecorated(False)
        self.part_attribute_tree.setAlternatingRowColors(True)
        self.part_attribute_tree.itemSelectionChanged.connect(self._part_attribute_selected)
        self.part_attribute_tree.setMinimumWidth(390)
        individual_column.addWidget(self.part_attribute_tree, 1)

        individual_box = QGroupBox("選択中の候補だけ")
        individual_layout = QVBoxLayout(individual_box)
        edit_form = QFormLayout()
        self.part_key_input = QLineEdit()
        self.part_key_input.setMinimumWidth(270)
        self.part_key_input.setPlaceholderText("例: classification.tag / custom.note")
        self.part_value_input = QLineEdit()
        self.part_value_input.setMinimumWidth(270)
        self.part_value_input.setPlaceholderText("文字列として上書きします。空欄なら項目を消します。")
        edit_form.addRow("項目", self.part_key_input)
        edit_form.addRow("値", self.part_value_input)
        individual_layout.addLayout(edit_form)
        edit_actions = QHBoxLayout()
        replace_button = QPushButton("項目を上書き")
        replace_button.setMinimumWidth(128)
        replace_button.clicked.connect(self.replace_part_attribute)
        edit_actions.addWidget(replace_button)
        remove_button = QPushButton("項目を削除")
        remove_button.setMinimumWidth(108)
        remove_button.clicked.connect(self.remove_part_attribute)
        edit_actions.addWidget(remove_button)
        individual_layout.addLayout(edit_actions)
        individual_column.addWidget(individual_box)
        editor_layout.addLayout(individual_column, 3)

        common_box = QGroupBox("全候補への共通操作")
        common_box.setMinimumWidth(350)
        common_layout = QVBoxLayout(common_box)
        common_hint = QLabel("上書きは項目がなければ追加、あれば同じ値へ更新します。元JSONは保存するまで変わりません。")
        common_hint.setWordWrap(True)
        common_layout.addWidget(common_hint)

        common_kind_row = QHBoxLayout()
        common_kind_row.addWidget(QLabel("項目の種類"))
        self.common_part_scope_combo = QComboBox()
        self.common_part_scope_combo.setMinimumWidth(190)
        self.common_part_scope_combo.addItem("標準項目", "standard")
        self.common_part_scope_combo.addItem("一覧にある独自項目", "existing")
        self.common_part_scope_combo.addItem("完全自由項目", "custom")
        self.common_part_scope_combo.currentIndexChanged.connect(self._refresh_common_part_field_choices)
        common_kind_row.addWidget(self.common_part_scope_combo, 1)
        common_layout.addLayout(common_kind_row)

        common_key_row = QHBoxLayout()
        common_key_row.addWidget(QLabel("項目"))
        self.common_part_field_combo = QComboBox()
        self.common_part_field_combo.setMinimumWidth(260)
        self.common_part_field_combo.currentIndexChanged.connect(self._update_common_part_controls)
        common_key_row.addWidget(self.common_part_field_combo, 1)
        self.common_part_custom_key_input = QLineEdit()
        self.common_part_custom_key_input.setMinimumWidth(260)
        self.common_part_custom_key_input.setPlaceholderText("例: custom.note")
        self.common_part_custom_key_input.textChanged.connect(self._update_common_part_controls)
        common_key_row.addWidget(self.common_part_custom_key_input, 1)
        common_layout.addLayout(common_key_row)

        common_value_row = QHBoxLayout()
        common_value_row.addWidget(QLabel("値"))
        self.common_part_preset_combo = QComboBox()
        self.common_part_preset_combo.setMinimumWidth(180)
        self.common_part_preset_combo.currentIndexChanged.connect(self._update_common_part_controls)
        common_value_row.addWidget(self.common_part_preset_combo)
        self.common_part_value_input = QLineEdit()
        self.common_part_value_input.setMinimumWidth(220)
        self.common_part_value_input.setPlaceholderText("全候補に設定する値")
        self.common_part_value_input.textChanged.connect(self._update_common_part_controls)
        common_value_row.addWidget(self.common_part_value_input, 1)
        common_layout.addLayout(common_value_row)
        self.common_part_help_label = QLabel()
        self.common_part_help_label.setWordWrap(True)
        common_layout.addWidget(self.common_part_help_label)
        common_actions = QHBoxLayout()
        self.common_replace_button = QPushButton("全候補に上書き")
        self.common_replace_button.setMinimumWidth(138)
        self.common_replace_button.clicked.connect(self.replace_common_part_attribute)
        common_actions.addWidget(self.common_replace_button)
        self.common_remove_button = QPushButton("全候補から削除")
        self.common_remove_button.setMinimumWidth(138)
        self.common_remove_button.clicked.connect(self.remove_common_part_attribute)
        common_actions.addWidget(self.common_remove_button)
        common_layout.addLayout(common_actions)
        common_layout.addStretch(1)
        editor_layout.addWidget(common_box, 2)
        workspace.addWidget(editor_box, 6)
        layout.addLayout(workspace, 1)

        self._refresh_common_part_field_choices()

        output_box = QGroupBox("パーツの保存・評価パッチ結合・本台帳への受け渡し")
        output_layout = QVBoxLayout(output_box)
        save_row = QHBoxLayout()
        save_row.addWidget(QLabel("結合保存先"))
        self.parts_destination = PathLineInput(drop_as="full_path")
        self.parts_destination.setPlaceholderText("複数パーツを結合して作る .json ファイル")
        save_row.addWidget(self.parts_destination, 1)
        save_button = QPushButton("結合して別名保存")
        save_button.clicked.connect(self.save_merged_parts)
        save_row.addWidget(save_button)
        send_button = QPushButton("本台帳へ追加する候補にする")
        send_button.setToolTip("ここではIDを発行しません。本台帳モードの明示追加時だけIDを発行します。")
        send_button.clicked.connect(self.send_parts_to_ledger)
        save_row.addWidget(send_button)
        output_layout.addLayout(save_row)

        review_row = QHBoxLayout()
        review_row.addWidget(QLabel("評価パッチ"))
        self.review_patch_input = PathLineInput(drop_as="full_path")
        self.review_patch_input.setPlaceholderText("media_review_patch の .json ファイル")
        self.review_patch_input.setToolTip(
            "メディア情報整理のMPV評価・見どころパッチを指定します。現在のパーツはまだ変更しません。"
        )
        self.review_patch_input.textChanged.connect(self._invalidate_review_merge_plan)
        review_row.addWidget(self.review_patch_input, 1)
        check_review_button = QPushButton("照合を確認")
        check_review_button.clicked.connect(self.check_review_patch_merge)
        review_row.addWidget(check_review_button)
        self.apply_review_patch_button = QPushButton("評価・見どころを結合")
        self.apply_review_patch_button.setToolTip(
            "照合が安全な場合だけ、評価・見どころを画面内のパーツへ移植します。元JSONは保存まで変更しません。"
        )
        self.apply_review_patch_button.clicked.connect(self.apply_review_patch_merge)
        review_row.addWidget(self.apply_review_patch_button)
        self.review_patch_detail_button = QPushButton("照合詳細")
        self.review_patch_detail_button.setToolTip("直近の評価パッチ照合結果を別ウィンドウで表示します。")
        self.review_patch_detail_button.clicked.connect(self.show_review_patch_report)
        review_row.addWidget(self.review_patch_detail_button)
        output_layout.addLayout(review_row)
        self.review_patch_summary_label = QLabel(
            "評価パッチを指定して「照合を確認」を押すと、全件の一致状況を表示します。"
        )
        output_layout.addWidget(self.review_patch_summary_label)
        layout.addWidget(output_box)
        return page

    def _build_ledger_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        source_box = QGroupBox("本台帳JSON（1つだけ開く）")
        source_layout = QVBoxLayout(source_box)
        read_row = QHBoxLayout()
        read_row.addWidget(QLabel("開く"))
        self.ledger_source = PathLineInput(drop_as="full_path")
        self.ledger_source.setPlaceholderText("media_ledger 形式の .json ファイル")
        read_row.addWidget(self.ledger_source, 1)
        load_button = QPushButton("本台帳を読み込む")
        load_button.clicked.connect(self.load_main_ledger)
        read_row.addWidget(load_button)
        new_button = QPushButton("空の本台帳を作る")
        new_button.clicked.connect(self.new_main_ledger)
        read_row.addWidget(new_button)
        source_layout.addLayout(read_row)
        save_row = QHBoxLayout()
        save_row.addWidget(QLabel("保存先"))
        self.ledger_destination = PathLineInput(drop_as="full_path")
        self.ledger_destination.setPlaceholderText("明示保存する media_ledger の .json ファイル")
        save_row.addWidget(self.ledger_destination, 1)
        save_button = QPushButton("本台帳を明示保存")
        save_button.clicked.connect(self.save_main_ledger)
        save_row.addWidget(save_button)
        source_layout.addLayout(save_row)
        layout.addWidget(source_box)

        append_box = QGroupBox("パーツJSONを本台帳の末尾へ追加")
        append_layout = QVBoxLayout(append_box)
        import_row = QHBoxLayout()
        import_row.addWidget(QLabel("パーツJSON"))
        self.pending_parts_source = PathLineInput(drop_as="full_path")
        self.pending_parts_source.setPlaceholderText("メディア情報整理で保存した media_parts / media_catalog の .json")
        import_row.addWidget(self.pending_parts_source, 1)
        import_button = QPushButton("追加候補として読み込む")
        import_button.setToolTip("ここでは作品ID・ファイルIDを発行しません。内容を確認してから末尾追加します。")
        import_button.clicked.connect(self.load_pending_parts)
        import_row.addWidget(import_button)
        append_layout.addLayout(import_row)
        pending_row = QHBoxLayout()
        self.pending_parts_label = QLabel("追加候補: 0 件")
        pending_row.addWidget(self.pending_parts_label)
        pending_row.addStretch(1)
        self.append_parts_button = QPushButton("候補を末尾へ追加")
        self.append_parts_button.setToolTip("候補1件につき、新しい作品IDと最初のファイルIDを発行します。既存作品への版追加はまだ行いません。")
        self.append_parts_button.clicked.connect(self.append_pending_parts)
        pending_row.addWidget(self.append_parts_button)
        append_layout.addLayout(pending_row)
        layout.addWidget(append_box)

        workspace = QHBoxLayout()
        list_box = QGroupBox("作品一覧")
        list_layout = QVBoxLayout(list_box)
        self.work_tree = QTreeWidget()
        self.work_tree.setHeaderLabels(["作品ID", "タイトル", "シリーズ", "収集", "ファイル版"])
        self.work_tree.setRootIsDecorated(False)
        self.work_tree.setAlternatingRowColors(True)
        self.work_tree.itemSelectionChanged.connect(self._work_selected)
        list_layout.addWidget(self.work_tree, 1)
        add_work_button = QPushButton("空の作品を追加")
        add_work_button.setToolTip("本台帳にだけ新しい作品IDを発行します。")
        add_work_button.clicked.connect(self.add_empty_work)
        list_layout.addWidget(add_work_button)
        workspace.addWidget(list_box, 1)

        detail_box = QGroupBox("選択中作品の共通情報")
        detail_layout = QVBoxLayout(detail_box)
        self.work_detail_label = QLabel("本台帳を読み込み、作品を選択してください。")
        self.work_detail_label.setStyleSheet("font-weight: bold;")
        detail_layout.addWidget(self.work_detail_label)
        work_form = QFormLayout()
        self.work_title_input = QLineEdit()
        self.work_category_input = QLineEdit()
        self.work_series_input = QLineEdit()
        self.work_status_combo = QComboBox()
        self.work_status_combo.addItem("（未設定）", "")
        for status in COLLECTION_STATUSES:
            self.work_status_combo.addItem(status, status)
        work_form.addRow("正式タイトル", self.work_title_input)
        work_form.addRow("カテゴリツリー", self.work_category_input)
        work_form.addRow("シリーズ名", self.work_series_input)
        work_form.addRow("収集状態", self.work_status_combo)
        detail_layout.addLayout(work_form)
        apply_work_button = QPushButton("作品共通情報を反映")
        apply_work_button.clicked.connect(self.apply_work_edits)
        detail_layout.addWidget(apply_work_button)
        detail_layout.addWidget(QLabel("現在のファイル版（閲覧のみ。既存作品への別版追加は次段階で実装します）"))
        self.master_file_tree = QTreeWidget()
        self.master_file_tree.setHeaderLabels(["ファイルID", "ファイル名", "解像度", "保管状況"])
        self.master_file_tree.setRootIsDecorated(False)
        self.master_file_tree.setAlternatingRowColors(True)
        detail_layout.addWidget(self.master_file_tree, 1)
        workspace.addWidget(detail_box, 1)
        layout.addLayout(workspace, 1)
        return page

    # -- Parts -----------------------------------------------------------
    def load_part_sources(self) -> None:
        paths = self.parts_paths.paths(deduplicate=True)
        if not paths:
            self._notify("パーツJSONを1件以上追加してください。")
            return
        documents: list[MediaPartsDocument] = []
        errors: list[str] = []
        for path in paths:
            try:
                documents.append(load_parts(path))
            except ValueError as exc:
                errors.append(f"{path.name}: {exc}")
        if errors:
            self._notify("読み込めないJSONがあります。\n" + "\n".join(errors))
            return
        merged = merge_parts(documents)
        self._parts = list(merged.parts)
        self._selected_part_index = 0 if self._parts else None
        self._invalidate_review_merge_plan()
        self._refresh_parts()
        self._refresh_common_part_field_choices()
        converted = sum(1 for document in documents if document.converted_from_extraction_catalog)
        self._notify(f"パーツを {len(self._parts)} 件読み込みました。抽出JSONからの変換: {converted} ファイル。IDは発行していません。")

    def _part_selected(self) -> None:
        item = self.part_tree.currentItem()
        self._selected_part_index = int(item.data(0, Qt.ItemDataRole.UserRole)) if item is not None else None
        self._refresh_part_detail()

    def _part_attribute_selected(self) -> None:
        item = self.part_attribute_tree.currentItem()
        if item is None:
            return
        self.part_key_input.setText(item.text(0))
        self.part_value_input.setText(item.text(2))

    def replace_part_attribute(self) -> None:
        if self._selected_part_index is None or not (0 <= self._selected_part_index < len(self._parts)):
            self._notify("編集するパーツを選択してください。")
            return
        key = self.part_key_input.text().strip()
        if not key:
            self._notify("項目名を入力してください。")
            return
        part = self._parts[self._selected_part_index]
        attributes = replace_attribute(part.attributes, key=key, value=self.part_value_input.text())
        self._parts[self._selected_part_index] = replace_part_attributes(part, attributes)
        self._invalidate_review_merge_plan()
        self._refresh_parts()
        self._refresh_common_part_field_choices()
        self._notify("パーツ項目を画面内で更新しました。結合保存または本台帳への明示追加まで、ファイルは変更しません。")

    def remove_part_attribute(self) -> None:
        if self._selected_part_index is None or not (0 <= self._selected_part_index < len(self._parts)):
            self._notify("編集するパーツを選択してください。")
            return
        key = self.part_key_input.text().strip()
        if not key:
            self._notify("削除する項目名を選択または入力してください。")
            return
        part = self._parts[self._selected_part_index]
        attributes = tuple(attribute for attribute in part.attributes if attribute.key != key)
        self._parts[self._selected_part_index] = replace_part_attributes(part, attributes)
        self._invalidate_review_merge_plan()
        self._refresh_parts()
        self._refresh_common_part_field_choices()
        self._notify("パーツ項目を削除しました。保存前なので元JSONは変更していません。")

    def _existing_custom_part_keys(self) -> tuple[str, ...]:
        """Return durable non-standard keys already present in the loaded parts."""
        return tuple(sorted({
            attribute.key
            for part in self._parts
            for attribute in part.attributes
            if catalog_field_for_key(attribute.key) is None
        }))

    def _refresh_common_part_field_choices(self, *_unused: object) -> None:
        """Switch among standard fields, observed custom keys, and free input."""
        previous_key = self._selected_common_part_key()
        scope = str(self.common_part_scope_combo.currentData() or "standard")
        self.common_part_field_combo.blockSignals(True)
        self.common_part_field_combo.clear()
        if scope == "standard":
            for field in STANDARD_CATALOG_FIELDS:
                suffix = "（複数可）" if field.allows_multiple else ""
                self.common_part_field_combo.addItem(f"{field.label}{suffix}: {field.key}", field.key)
        elif scope == "existing":
            keys = self._existing_custom_part_keys()
            if keys:
                for key in keys:
                    self.common_part_field_combo.addItem(
                        f"{_attribute_meaning(key) or '独自項目'}: {key}", key
                    )
            else:
                self.common_part_field_combo.addItem("まだ独自項目はありません", "")
        self.common_part_field_combo.blockSignals(False)
        if scope != "custom":
            index = self.common_part_field_combo.findData(previous_key)
            self.common_part_field_combo.setCurrentIndex(index if index >= 0 else 0)
        self.common_part_field_combo.setVisible(scope != "custom")
        self.common_part_custom_key_input.setVisible(scope == "custom")
        self.common_part_custom_key_input.setEnabled(scope == "custom")
        self._update_common_part_controls()

    def _selected_common_part_key(self) -> str:
        """Return the currently selected shared-edit key without guessing it."""
        if str(self.common_part_scope_combo.currentData() or "") == "custom":
            return self.common_part_custom_key_input.text().strip()
        return str(self.common_part_field_combo.currentData() or "").strip()

    def _common_part_value_text(self) -> str:
        field = catalog_field_for_key(self._selected_common_part_key())
        if field is None or not preset_values_for_catalog_field(field.key):
            return self.common_part_value_input.text()
        selected = str(self.common_part_preset_combo.currentData() or "")
        return self.common_part_value_input.text() if selected == "__custom__" else selected

    @staticmethod
    def _configure_common_part_value_validator(value_input: QLineEdit, key: str) -> None:
        field = catalog_field_for_key(key)
        if field is None or field.value_type in {"text", "time_range"}:
            value_input.setValidator(None)
            return
        if field.value_type == "resolution":
            value_input.setValidator(
                QRegularExpressionValidator(
                    QRegularExpression(r"\d{0,6}(?:\s*[xX×*＊]\s*\d{0,6})?"), value_input
                )
            )
            return
        maximum = (
            1.0
            if field.key in {"review.score", "review.community_score"}
            else 1_000_000_000_000_000.0
        )
        value_input.setValidator(QDoubleValidator(0.0, maximum, 6, value_input))

    def _update_common_part_controls(self, *_unused: object) -> None:
        """Render the correct value control and validation for one chosen key."""
        key = self._selected_common_part_key()
        field = catalog_field_for_key(key)
        choices = preset_values_for_catalog_field(key) if field is not None else ()
        previous = self.common_part_preset_combo.currentData()
        self.common_part_preset_combo.blockSignals(True)
        self.common_part_preset_combo.clear()
        if choices:
            self.common_part_preset_combo.addItem("値を選択", "")
            for display, value in choices:
                self.common_part_preset_combo.addItem(display, value)
            self.common_part_preset_combo.addItem("自由入力…", "__custom__")
            index = self.common_part_preset_combo.findData(previous)
            self.common_part_preset_combo.setCurrentIndex(index if index >= 0 else 0)
        self.common_part_preset_combo.blockSignals(False)
        has_presets = bool(choices)
        custom_preset = str(self.common_part_preset_combo.currentData() or "") == "__custom__"
        self.common_part_preset_combo.setVisible(has_presets)
        self.common_part_value_input.setVisible(not has_presets or custom_preset)
        self._configure_common_part_value_validator(self.common_part_value_input, key)

        if not key:
            hint = "項目を選択してください。完全自由項目では独自の項目名を入力します。"
        elif field is None:
            hint = "独自項目は文字列として上書きします。空欄にしたい場合は削除を使います。"
        elif field.key == "collection.status":
            hint = "収集状態は候補から選びます。例外だけ「自由入力…」を使えます。"
        elif field.key == "storage.status":
            hint = "保管状況は候補から選べます。複数の保管先を残す編集は個別編集で行えます。"
        elif field.value_type == "resolution":
            hint = "解像度は 1920×1080（* でも可）の形式で入力します。"
        elif field.value_type == "time_range":
            hint = "見どころ時間は 13:23、または 13:11-14:25 の形式で入力します。"
        elif field.value_type in {"number", "duration_seconds"}:
            hint = "数値として入力します。評価は候補から選べます。"
        elif field.key == "classification.category_tree":
            hint = "カテゴリは 大分類/中分類/小分類 のように / で区切る単一値です。"
        else:
            hint = "上書きは全候補で同じ値に統一します。空欄にしたい場合は削除を使います。"
        self.common_part_help_label.setText(hint)
        has_value = bool(self._common_part_value_text().strip())
        self.common_replace_button.setEnabled(bool(key) and has_value)
        self.common_remove_button.setEnabled(bool(key))

    def replace_common_part_attribute(self) -> None:
        if not self._parts:
            self._notify("共通操作するパーツがありません。先にパーツJSONを読み込んでください。")
            return
        key = self._selected_common_part_key()
        value = self._common_part_value_text()
        if not key:
            self._notify("共通で上書きする項目を選択または入力してください。")
            return
        if not value.strip():
            self._notify("共通の上書き値を入力してください。削除する場合は「全候補から項目を削除」を使ってください。")
            return
        try:
            parsed_value = parse_catalog_field_value(key, value)
            field = catalog_field_for_key(key)
            self._parts = list(
                replace_parts_attribute(
                    self._parts,
                    key=key,
                    value=parsed_value,
                    value_type=field.value_type if field is not None else "text",
                )
            )
        except ValueError as exc:
            self._notify(str(exc))
            return
        self._invalidate_review_merge_plan()
        self._refresh_parts()
        self._refresh_common_part_field_choices()
        self._notify(f"全 {len(self._parts)} 件に「{key}」を上書き登録しました。保存前なので元JSONは変更していません。")

    def remove_common_part_attribute(self) -> None:
        if not self._parts:
            self._notify("共通操作するパーツがありません。先にパーツJSONを読み込んでください。")
            return
        key = self._selected_common_part_key()
        if not key:
            self._notify("全候補から削除する項目を選択または入力してください。")
            return
        affected = sum(1 for part in self._parts if any(attribute.key == key for attribute in part.attributes))
        try:
            self._parts = list(replace_parts_attribute(self._parts, key=key, value=None))
        except ValueError as exc:
            self._notify(str(exc))
            return
        self._invalidate_review_merge_plan()
        self._refresh_parts()
        self._refresh_common_part_field_choices()
        self._notify(f"全 {len(self._parts)} 件を確認し、「{key}」を {affected} 件から削除しました。保存前なので元JSONは変更していません。")

    def _invalidate_review_merge_plan(self, *_unused: object) -> None:
        """Require a fresh all-or-nothing preflight after either input changes."""
        self._review_patch = None
        self._review_merge_plan = None
        self._review_merge_parts_snapshot = None
        if hasattr(self, "apply_review_patch_button"):
            self.apply_review_patch_button.setEnabled(False)
        if hasattr(self, "review_patch_detail_button"):
            self.review_patch_detail_button.setEnabled(False)
        if hasattr(self, "review_patch_summary_label"):
            self.review_patch_summary_label.setText(
                "評価パッチを指定して「照合を確認」を押すと、全件の一致状況を表示します。"
            )

    def check_review_patch_merge(self) -> None:
        if not self._parts:
            self._notify("先に結合先となるパーツJSONを読み込んでください。")
            return
        path = self.review_patch_input.path()
        if path is None:
            self._notify("MPV評価・見どころパッチJSONを指定してください。")
            return
        try:
            patch = load_review_patch(path)
            plan = plan_review_patch_merge(tuple(self._parts), patch)
        except ValueError as exc:
            self._notify("評価パッチを照合できません。\n" + str(exc))
            return
        self._review_patch = patch
        self._review_merge_plan = plan
        self._review_merge_parts_snapshot = tuple(self._parts)
        self._review_patch_report_text = review_patch_merge_summary(plan, patch)
        self.review_patch_detail_button.setEnabled(True)
        self.apply_review_patch_button.setEnabled(plan.is_safe)
        if plan.is_safe:
            self.review_patch_summary_label.setText(
                f"照合済み: 一意に一致 {len(plan.matches)} 件 / 変更しない対象 {plan.unchanged_target_count} 件。"
            )
            self._notify(
                f"評価パッチ {plan.patch_entry_count} 件は、パーツ {plan.target_part_count} 件へ安全に照合できました。\n"
                "「評価・見どころを結合」を押すと画面内だけへ移植します。保存はまだ行いません。"
            )
        else:
            self.review_patch_summary_label.setText(
                "照合エラー: 詳細を確認してください。安全のため、画面内のパーツは変更していません。"
            )
            self._notify(
                "評価パッチの照合は安全ではありません。画面内のパーツは変更していません。\n"
                + self._review_patch_report_text
            )

    def apply_review_patch_merge(self) -> None:
        patch = self._review_patch
        plan = self._review_merge_plan
        if patch is None or plan is None or self._review_merge_parts_snapshot != tuple(self._parts):
            self._notify("先に現在のパーツと評価パッチで「照合を確認」を押してください。")
            return
        try:
            merged = apply_review_patch_merge(tuple(self._parts), patch, plan)
        except ValueError as exc:
            self._notify("評価・見どころを結合していません。\n" + str(exc))
            return
        self._parts = list(merged)
        self._review_patch = None
        self._review_merge_plan = None
        self._review_merge_parts_snapshot = None
        self.apply_review_patch_button.setEnabled(False)
        self._review_patch_report_text = (
            f"評価・見どころを {len(plan.matches)} 件へ画面内で移植しました。\n"
            "元のパーツJSON・評価パッチJSONは変更していません。結合して別名保存で保存してください。"
        )
        self.review_patch_detail_button.setEnabled(True)
        self.review_patch_summary_label.setText(
            f"画面内へ移植済み: {len(plan.matches)} 件。保存する場合は「結合して別名保存」を押してください。"
        )
        self._refresh_parts()
        self._notify(
            f"評価・見どころを {len(plan.matches)} 件のパーツへ移植しました。\n"
            "画面内だけの変更です。保存する場合は「結合して別名保存」を押してください。"
        )

    def show_review_patch_report(self) -> None:
        if not self._review_patch_report_text:
            self._notify("表示する評価パッチ照合結果がありません。")
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("評価・見どころパッチの照合詳細")
        dialog.setMinimumSize(720, 360)
        layout = QVBoxLayout(dialog)
        report = QPlainTextEdit()
        report.setReadOnly(True)
        report.setPlainText(self._review_patch_report_text)
        layout.addWidget(report, 1)
        close_button = QPushButton("閉じる")
        close_button.clicked.connect(dialog.accept)
        layout.addWidget(close_button)
        dialog.exec()

    def save_merged_parts(self) -> None:
        path = self.parts_destination.path()
        if path is None:
            self._notify("結合したパーツJSONの保存先を指定してください。")
            return
        try:
            saved = save_parts(path, MediaPartsDocument(tuple(self._parts)))
        except ValueError as exc:
            self._notify(str(exc))
            return
        self.parts_destination.setText(str(saved))
        self._notify(f"IDなしのパーツJSONを保存しました: {saved}（{len(self._parts)} 件）")

    def send_parts_to_ledger(self) -> None:
        if not self._parts:
            self._notify("先にパーツを読み込んでください。")
            return
        self._pending_parts = tuple(self._parts)
        self.stack.setCurrentIndex(1)
        self._refresh_ledger()
        self._notify(f"{len(self._pending_parts)} 件を本台帳へ追加する候補にしました。まだIDは発行していません。")

    def _refresh_parts(self) -> None:
        self.part_tree.blockSignals(True)
        self.part_tree.clear()
        for index, part in enumerate(self._parts):
            row = QTreeWidgetItem([
                str(index + 1), _part_title(part), _value(part.attributes, "file.name.observed"),
            ])
            row.setData(0, Qt.ItemDataRole.UserRole, index)
            self.part_tree.addTopLevelItem(row)
            if index == self._selected_part_index:
                row.setSelected(True)
        self.part_tree.blockSignals(False)
        self._refresh_part_detail()

    def _refresh_part_detail(self) -> None:
        part = self._parts[self._selected_part_index] if self._selected_part_index is not None and 0 <= self._selected_part_index < len(self._parts) else None
        self.part_attribute_tree.blockSignals(True)
        self.part_attribute_tree.clear()
        if part is None:
            self.part_detail_label.setText("パーツを選択してください。")
            self.part_attribute_tree.blockSignals(False)
            return
        self.part_detail_label.setText(f"候補 {self._selected_part_index + 1} — IDなしの編集可能パーツ")
        for attribute in part.attributes:
            self.part_attribute_tree.addTopLevelItem(QTreeWidgetItem([
                attribute.key, _attribute_meaning(attribute.key), _display(attribute.value), attribute.value_type, attribute.source,
            ]))
        self.part_attribute_tree.blockSignals(False)

    # -- Main ledger -----------------------------------------------------
    def load_pending_parts(self) -> None:
        """Load ID-free parts as a pending append, without issuing IDs yet."""
        path = self.pending_parts_source.path()
        if path is None:
            self._notify("メディア情報整理で保存したパーツJSONを指定してください。")
            return
        try:
            document = load_parts(path)
        except ValueError as exc:
            self._notify("パーツJSONを追加候補として読み込めません。\n" + str(exc))
            return
        self._pending_parts = tuple(document.parts)
        self._refresh_ledger()
        converted = "（抽出カタログJSONから変換）" if document.converted_from_extraction_catalog else ""
        self._notify(
            f"パーツ {len(self._pending_parts)} 件を追加候補として読み込みました{converted}。"
            "まだ作品ID・ファイルIDは発行していません。"
        )

    def load_main_ledger(self) -> None:
        path = self.ledger_source.path()
        if path is None:
            self._notify("本台帳JSONを指定してください。")
            return
        try:
            self._ledger = load_ledger(path)
        except ValueError as exc:
            self._notify(str(exc))
            return
        self.ledger_destination.setText(str(path))
        self._selected_work_id = self._ledger.works[0].work_id if self._ledger.works else None
        self._refresh_ledger()
        self._notify(f"本台帳を読み込みました。作品 {len(self._ledger.works)} 件。末尾追加・編集内容は明示保存までメモリ上だけです。")

    def new_main_ledger(self) -> None:
        self._ledger = empty_ledger()
        self._selected_work_id = None
        self.ledger_source.clear()
        self.ledger_destination.clear()
        self._refresh_ledger()
        self._notify("空の本台帳を作成しました。パーツ候補の末尾追加、または空の作品追加後に明示保存してください。")

    def append_pending_parts(self) -> None:
        if self._ledger is None:
            self._notify("先に本台帳を読み込むか、空の本台帳を作成してください。")
            return
        if not self._pending_parts:
            self._notify("メディア情報整理で作ったパーツJSONを「追加候補として読み込む」で指定してください。")
            return
        self._ledger, works = adopt_parts(self._ledger, self._pending_parts)
        self._selected_work_id = works[0].work_id if works else self._selected_work_id
        self._pending_parts = ()
        self._refresh_ledger()
        if works:
            self._notify(f"パーツ {len(works)} 件を本台帳の末尾へ追加しました。{works[0].work_id}〜 のIDを発行済みです。保存はまだです。")

    def add_empty_work(self) -> None:
        if self._ledger is None:
            self._notify("先に本台帳を読み込むか、空の本台帳を作成してください。")
            return
        self._ledger, work = add_work(self._ledger)
        self._selected_work_id = work.work_id
        self._refresh_ledger()
        self._notify(f"{work.work_id} を本台帳へ追加しました。保存はまだです。")

    def _work_selected(self) -> None:
        item = self.work_tree.currentItem()
        self._selected_work_id = str(item.data(0, Qt.ItemDataRole.UserRole)) if item is not None else None
        self._refresh_work_detail()

    def apply_work_edits(self) -> None:
        if self._ledger is None or self._selected_work_id is None:
            self._notify("編集する作品を選択してください。")
            return
        work = work_for_id(self._ledger, self._selected_work_id)
        if work is None:
            return
        attributes = replace_attribute(work.attributes, key="title.official", value=self.work_title_input.text())
        attributes = replace_attribute(attributes, key="classification.category_tree", value=self.work_category_input.text())
        attributes = replace_attribute(attributes, key="series.name", value=self.work_series_input.text())
        attributes = replace_attribute(attributes, key="collection.status", value=str(self.work_status_combo.currentData() or ""))
        self._ledger = replace_work_attributes(self._ledger, work.work_id, attributes)
        self._refresh_ledger()
        self._notify(f"{work.work_id} の作品共通情報を画面内へ反映しました。保存はまだです。")

    def save_main_ledger(self) -> None:
        if self._ledger is None:
            self._notify("保存する本台帳がありません。")
            return
        path = self.ledger_destination.path()
        if path is None:
            self._notify("本台帳の保存先 .json ファイルを指定してください。")
            return
        try:
            saved = save_ledger(path, self._ledger)
        except ValueError as exc:
            self._notify(str(exc))
            return
        self.ledger_source.setText(str(saved))
        self.ledger_destination.setText(str(saved))
        self._notify(f"本台帳を保存しました: {saved}（作品 {len(self._ledger.works)} 件）")

    def _refresh_ledger(self) -> None:
        self.pending_parts_label.setText(f"追加候補: {len(self._pending_parts)} 件")
        self.append_parts_button.setEnabled(self._ledger is not None and bool(self._pending_parts))
        self.work_tree.blockSignals(True)
        self.work_tree.clear()
        if self._ledger is not None:
            for work in self._ledger.works:
                row = QTreeWidgetItem([
                    work.work_id, _work_title(work), _value(work.attributes, "series.name"),
                    _value(work.attributes, "collection.status"), str(len(work.files)),
                ])
                row.setData(0, Qt.ItemDataRole.UserRole, work.work_id)
                self.work_tree.addTopLevelItem(row)
                if work.work_id == self._selected_work_id:
                    row.setSelected(True)
        self.work_tree.blockSignals(False)
        self._refresh_work_detail()

    def _refresh_work_detail(self) -> None:
        work = work_for_id(self._ledger, self._selected_work_id) if self._ledger and self._selected_work_id else None
        self.master_file_tree.clear()
        if work is None:
            self.work_detail_label.setText("本台帳を読み込み、作品を選択してください。")
            self.work_title_input.clear()
            self.work_category_input.clear()
            self.work_series_input.clear()
            self.work_status_combo.setCurrentIndex(0)
            return
        self.work_detail_label.setText(f"{work.work_id} — 作品共通情報")
        self.work_title_input.setText(_value(work.attributes, "title.official"))
        self.work_category_input.setText(_value(work.attributes, "classification.category_tree"))
        self.work_series_input.setText(_value(work.attributes, "series.name"))
        status_index = self.work_status_combo.findData(attribute_value(work.attributes, "collection.status") or "")
        self.work_status_combo.setCurrentIndex(status_index if status_index >= 0 else 0)
        for file in work.files:
            self.master_file_tree.addTopLevelItem(QTreeWidgetItem([
                file.file_id, _value(file.attributes, "file.name.observed"),
                _value(file.attributes, "video.resolution"), _value(file.attributes, "storage.status"),
            ]))

    def _notify(self, message: str) -> None:
        self.status_label.setText(message)


def create_screen(return_to_main: Callable[[], None]) -> MediaLedgerScreen:
    return MediaLedgerScreen(return_to_main)
