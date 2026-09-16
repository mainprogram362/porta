"""Integrated, in-memory workspace for safe file operations."""

from __future__ import annotations

from gui.current_page_stack import CurrentPageStack, PageScrollArea
from gui.adaptive_splitter import AdaptiveSplitter
from gui.layout_policy import preferred_window_size

from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QCheckBox,
    QApplication,
    QGridLayout,
    QDialog,
    QInputDialog,
    QMenu,
    QFrame,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QStyle,
    QVBoxLayout,
    QWidget,
)

from apps.file_tools.file_manager.operation_service import (
    OperationPresentation,
)
from apps.file_tools.file_manager.operation_confirmation_dialog import (
    OperationConfirmationDialog,
)
from apps.file_tools.file_manager.path_actions import direct_children_for_selected_folders
from apps.file_tools.file_manager.path_context import (
    add_file_manager_path_actions,
    add_registered_paths_menu,
)
from apps.file_tools.file_manager.extract_workflow import is_supported_archive_path
from apps.file_tools.file_manager.persistent_settings import (
    editable_text,
    load_settings,
    save_text,
    settings_status,
    template_text,
    validate_text,
)
from apps.file_tools.file_manager.rename_panel import RenamePanel
from apps.file_tools.file_manager.rename_workflow import build_rename_preview
from apps.file_tools.file_manager.search_workflow import select_paths_by_conditions
from apps.file_tools.file_manager.quick_copy_dialog import QuickCopyDialog
from apps.file_tools.file_manager.quick_compress_dialog import QuickCompressDialog
from apps.file_tools.file_manager.quick_extract_dialog import QuickExtractDialog
from apps.file_tools.file_manager.tree_copy_dialog import TreeCopyDialog
from apps.file_tools.file_manager.linked_record_operations_dialog import (
    LinkedRecordOperationsDialog,
)
from apps.file_tools.file_manager.record_linkage import (
    RecordLinkageResult,
    link_paths_to_record_field,
)
from apps.file_tools.file_manager.record_bundle_viewer import RecordBundleViewerDialog
from apps.file_tools.file_manager.parent_move_dialog import ParentMoveDialog
from apps.file_tools.file_manager.directory_name_rename_dialog import (
    DirectoryNameRenameDialog,
)
from runtime.transient_paths import offer_media_paths, offer_video_encode_paths
from foundation.path import normalize_path
from foundation.path_inspection import inspect_path
from records.record_bundle import RecordBundle
from gui import (
    AppPageLayout,
    JsonFieldSpec,
    JsonSettingsEditor,
    add_path_list_input,
    NoWheelComboBox,
)
from gui.flow_layout import FlowLayout


_INDEPENDENT_OPERATION_WINDOWS: set[QDialog] = set()


class _DiscardAwareDialog(QDialog):
    """Ask before a settings dialog discards unsaved edits, including via ×."""

    def __init__(self, parent: QWidget, confirm_close: Callable[[], bool]) -> None:
        super().__init__(parent)
        self._confirm_close = confirm_close

    def closeEvent(self, event) -> None:  # type: ignore[override]
        if self._confirm_close():
            event.accept()
        else:
            event.ignore()


def _retain_independent_operation_window(dialog: QDialog) -> None:
    """Keep a frozen operation window alive after its source screen is gone."""
    _INDEPENDENT_OPERATION_WINDOWS.add(dialog)
    dialog.destroyed.connect(
        lambda _object=None, current=dialog: _INDEPENDENT_OPERATION_WINDOWS.discard(current)
    )


def has_independent_operation_windows() -> bool:
    """Whether closing the launcher would also terminate handed-off work."""
    return bool(_INDEPENDENT_OPERATION_WINDOWS)


class FileManagerScreen(QWidget):
    """File-manager workspace with a path workbench and copy operations."""

    _WIDE_COLUMN_MINIMUM = 240
    _FAVORITES_COLUMN_MINIMUM = 120

    def __init__(
        self,
        on_back: Callable[[], None],
        *,
        on_open_media_tool: Callable[[str], None] | None = None,
    ) -> None:
        super().__init__()
        self._closing = False
        self._on_back = on_back
        self._on_open_media_tool = on_open_media_tool
        self._operation_presentation: OperationPresentation | None = None
        self._session_last_destination = ""
        self._operation_confirmation_dialogs: set[OperationConfirmationDialog] = set()
        self._history_restoring = False
        self._history_suspended = False
        self._undo_states: list[dict[str, object]] = []
        self._redo_states: list[dict[str, object]] = []
        self._settings_dialog: QDialog | None = None
        self._settings_editor: JsonSettingsEditor | None = None
        self._settings_baseline_text = ""
        self._quick_copy_dialogs: set[QuickCopyDialog] = set()
        self._quick_compress_dialogs: set[QuickCompressDialog] = set()
        self._quick_extract_dialogs: set[QuickExtractDialog] = set()
        self._tree_copy_dialogs: set[TreeCopyDialog] = set()
        self._linked_record_dialogs: set[LinkedRecordOperationsDialog] = set()
        self._record_bundle_viewers: set[RecordBundleViewerDialog] = set()
        self._parent_move_dialogs: set[ParentMoveDialog] = set()
        self._directory_name_rename_dialogs: set[DirectoryNameRenameDialog] = set()
        self._record_link_bundle: RecordBundle | None = None
        self._record_source_id: str | None = None
        self._record_source_revision: int | None = None
        self._record_link_field_index: int | None = None
        self._record_linkage: RecordLinkageResult | None = None
        self._record_linkage_refresh_suspended = False
        self._persistent_settings = load_settings()
        # Content layouts provide the remaining constraints for each mode.
        self._build_ui()
        self._work_initial_state = self._capture_ui_state()

    def describe_work_state(self):
        if self._record_link_bundle is not None or self._operation_presentation is not None:
            return {"level": 3, "reason": "対応表または操作予定を保持しています。"}
        state = self._capture_ui_state()
        if state["rename_rules"]:
            return {"level": 3, "reason": "リネーム規則を作成しています。"}
        if any(item.get("results") or item.get("destinations") for item in self._undo_states[:-1] + self._redo_states):
            return {"level": 3, "reason": "一覧編集の取り消し履歴を保持しています。"}
        if (state != self._work_initial_state or self.selection_filter_query.text()
                or self.rename_panel.text_input.text() or self.rename_panel.replacement_input.text()):
            return {"level": 2, "reason": f"{self.operation_combo.currentText()}を選択・入力しています。作業一覧{len(state['results'])}件。"}
        return {"level": 1, "reason": "作業一覧・操作設定は初期状態です。"}

    def receive_external_paths(
        self, paths: Iterable[str | Path], *, action: str = "browse"
    ) -> None:
        """Replace the work list and enter the externally selected operation."""
        values = tuple(str(path) for path in paths)
        self.search_results_input.setPlainText("\n".join(values))
        self.search_results_input.select_all_items()
        self._notify(f"外部ファイルマネージャーから{len(values)}件を受け取りました。")
        if action == "browse":
            return
        operations = {
            "copy": self._open_quick_copy_for_checked,
            "compress": self._open_quick_compress_for_checked,
            "extract": self._open_quick_extract_for_checked,
        }
        try:
            open_operation = operations[action]
        except KeyError as exc:
            raise ValueError(f"未対応のファイル操作です: {action}") from exc
        # The receiver can be closed before the event loop gets to this
        # handoff (for example when its tab is immediately closed).  Keep the
        # deferred action inert once shutdown has begun.
        QTimer.singleShot(0, lambda: None if self._closing else open_operation())

    def shutdown(self) -> None:
        """Cancel deferred UI handoffs before an owning tab is destroyed."""
        self._closing = True

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self.shutdown()
        super().closeEvent(event)

    def receive_record_bundle(self, bundle: RecordBundle) -> None:
        """Keep one session-only record table ready for explicit field linking."""
        self._record_link_bundle = bundle
        self._record_source_id = None
        self._record_source_revision = None
        self._record_link_field_index = None
        self._record_linkage = None
        self.search_results_input.set_supplemental_column_visible("紐づけ", True)
        self._refresh_record_linkage_display()
        title = bundle.title or "名称なし"
        self._notify(
            f"対応表「{title}」{len(bundle.rows)}レコードを受け取りました。"
            "作業一覧の右クリック「対応表との紐づけ」で項目を選んでください。"
        )

    def acquire_record(self, code: str) -> bool:
        from records import record_service
        try:
            response = record_service.request({"action": "get", "id": code})
            if not response.get("ok"):
                raise ValueError(response.get("error", "対応表に接続できません。適用内容は変更していません。"))
            bundle = record_service.decode_bundle(response["bundle"])
            revision = int(response["revision"])
        except (ValueError, KeyError, TypeError) as exc:
            self._notify(str(exc))
            QMessageBox.warning(self, "対応表を取得できません", str(exc))
            return False
        self.receive_record_bundle(bundle)
        self._record_source_id = code
        self._record_source_revision = revision
        self._update_record_bundle_status()
        self._notify(f"対応表 [{code}] 第{revision}版を取得しました。紐づけを解除しました。紐づけ項目を選んでください。")
        return True

    def choose_record(self) -> None:
        from records import record_service
        response = record_service.request({"action": "list"})
        records = response.get("records", [])
        if not records:
            QMessageBox.information(self, "対応表を選ぶ", "起動中の対応表はありません。テキスト分析から対応表を作成してください。")
            return
        labels = [f"{r['title'] or '無題'} [{r['id']}] 第{r['revision']}版 — {r['rows']}レコード / {r['fields']}項目" for r in records]
        choice, ok = QInputDialog.getItem(self, "対応表を選ぶ", "取得する対応表（取得成功時に紐づけを解除）", labels, 0, False)
        if ok:
            self.acquire_record(records[labels.index(choice)]["id"])

    def _record_button_menu(self) -> None:
        menu = QMenu(self)
        menu.addAction("対応表を選んで取得…", self.choose_record)
        view = menu.addAction("適用中の対応表を見る…", self.show_record_bundle_contents)
        view.setEnabled(self._record_link_bundle is not None)
        update = menu.addAction("最新版を取得（紐づけをすべて解除）", lambda: self.acquire_record(self._record_source_id))
        update.setEnabled(self._record_source_id is not None)
        edit = menu.addAction("元の対応表を開いて編集…", self._open_record_source)
        edit.setEnabled(self._record_source_id is not None)
        menu.exec(self.show_record_bundle_button.mapToGlobal(self.show_record_bundle_button.rect().bottomLeft()))

    def _open_record_source(self) -> None:
        from records import record_service
        response = record_service.request({"action": "edit", "id": self._record_source_id})
        if not response.get("ok"):
            QMessageBox.information(self, "元の対応表を開けません", response.get("error", "元の対応表に接続できません。取得済みの内容はそのまま使えます。"))

    def _build_ui(self) -> None:
        outer_layout = AppPageLayout(self)
        self.edit_controls = QWidget()
        edit_layout = QHBoxLayout(self.edit_controls)
        edit_layout.setSpacing(3)
        edit_layout.setContentsMargins(0, 0, 0, 0)
        # The main workspace prepares targets and rules.  Final destination,
        # preview and execution live in a separate frozen confirmation window.
        workspace = QWidget()
        self._workspace_layout = QGridLayout(workspace)
        self._workspace_layout.setSpacing(8)
        outer_layout.addWidget(workspace)

        self.favorites_box, favorites_layout = self._make_group("お気に入り")
        favorites_hint = QLabel("ダブルクリックで作業一覧へ追加")
        favorites_hint.setWordWrap(True)
        favorites_layout.addWidget(favorites_hint)
        self.favorites_list = QListWidget()
        self.favorites_list.setToolTip("設定で「favorite」が true のパスです。フルパス表示へ切り替えるか、カーソルを合わせると場所を確認できます。")
        self.favorites_list.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.favorites_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.favorites_list.itemDoubleClicked.connect(self._add_favorite_to_work_list)
        favorites_layout.addWidget(self.favorites_list, 1)
        self.favorite_full_paths_check = QCheckBox("フルパス表示")
        self.favorite_full_paths_check.toggled.connect(self._refresh_favorites)
        favorites_layout.addWidget(self.favorite_full_paths_check)
        self._refresh_favorites()

        self.search_results_box, search_layout = self._make_group("作業一覧")
        work_list_actions = FlowLayout()
        work_list_actions.setContentsMargins(0, 0, 0, 0)
        self.list_cleanup_button = QPushButton("一覧整理")
        cleanup_menu = QMenu(self.list_cleanup_button)
        self.list_cleanup_button.setMenu(cleanup_menu)
        clear_action = cleanup_menu.addAction("一覧を空にする")
        clear_action.triggered.connect(lambda: self.search_results_input.clear_items())
        self.show_record_bundle_button = QPushButton("対応表：なし")
        self.show_record_bundle_button.clicked.connect(self._record_button_menu)
        self.check_menu_button = QPushButton("チェック")
        check_menu = QMenu(self.check_menu_button)
        self.check_menu_button.setMenu(check_menu)
        check_menu.addAction("全件チェック", lambda: self.search_results_input.select_all_items())
        check_menu.addAction("全チェック解除", lambda: self.search_results_input.clear_item_selection())
        check_menu.addSeparator()
        self.selected_rows_menu_button = self.list_cleanup_button
        self.selected_rows_menu_button.setToolTip(
            "行を一覧から除外します。実ファイルは削除しません。"
        )
        selected_rows_menu = cleanup_menu
        check_selected_rows = check_menu.addAction("選択対象にチェックを入れる")
        check_selected_rows.setToolTip("青く選択した行だけを実行対象としてチェックします。")
        check_selected_rows.triggered.connect(
            lambda: self._set_selected_work_rows_checked(True)
        )
        uncheck_selected_rows = check_menu.addAction("選択対象のチェックを外す")
        uncheck_selected_rows.setToolTip("青く選択した行だけのチェックを外します。")
        uncheck_selected_rows.triggered.connect(
            lambda: self._set_selected_work_rows_checked(False)
        )
        selected_rows_menu.addSeparator()
        remove_selected_rows = selected_rows_menu.addAction("選択対象をリストから消す")
        remove_selected_rows.setToolTip("青く選択した行を作業一覧から除きます。実ファイルは削除しません。")
        remove_selected_rows.triggered.connect(self._remove_selected_work_rows)
        self.selected_rows_menu_button.setMenu(selected_rows_menu)
        work_list_actions.addWidget(self.check_menu_button)
        work_list_actions.addWidget(self.selected_rows_menu_button)
        self.selection_filter_toggle = QPushButton("条件で選別 ▸")
        self.selection_filter_toggle.setCheckable(True)
        self.selection_filter_toggle.setToolTip("現在のパス一覧を条件でチェック選別する欄を開閉します。")
        self.selection_filter_toggle.toggled.connect(self._toggle_selection_filter_panel)
        work_list_actions.addWidget(self.selection_filter_toggle)
        work_list_actions.addWidget(self.show_record_bundle_button)
        search_layout.addLayout(work_list_actions)
        self._update_record_bundle_status()

        work_list_body = QHBoxLayout()
        work_list_body.setContentsMargins(0, 0, 0, 0)
        search_layout.addLayout(work_list_body, 1)
        self.search_results_input = add_path_list_input(
            work_list_body,
            rows=10,
            placeholder="手入力・外部ドロップ・右クリック展開で候補を集めます。実行する項目にチェックを入れます。",
            show_operation_targets=False,
            show_controls=False,
            supplemental_column_label="紐づけ",
            context_menu_selection_actions=False,
            context_menu_operation_target_actions=False,
            context_menu_copy_actions=False,
            double_click_directory_selection=True,
            double_click_directory_replaces_all=True,
            enable_row_selection=True,
        )
        self.search_results_input.set_supplemental_column_visible("紐づけ", False)
        self.search_results_input.set_supplemental_column_fixed("紐づけ", 235)
        self.search_results_input.show_full_paths()
        self.search_results_input.set_visible_rows(4)
        self.search_results_input.itemDoubleClicked.connect(
            self._handle_work_list_double_click
        )
        self.selection_filter_panel = self._build_selection_filter_panel()
        work_list_body.addWidget(self.selection_filter_panel)
        self.selection_filter_panel.hide()
        self.search_results_input.setPlainText(
            "\n".join(
                entry["path"]
                for entry in self._persistent_settings["favorite_paths"]
                if entry["initial_work_list"]
            )
        )
        self.output_operation_box, output_operation_layout = self._make_group("一対一の出力先")
        self.output_operation_box.setFlat(False)
        self.output_operation_box.setStyleSheet(
            "QGroupBox { border: 1px solid #888; border-radius: 4px; margin-top: 8px; }"
            "QGroupBox::title { subcontrol-origin: margin; left: 7px; padding: 0 3px; }"
        )

        self.destination_box = QWidget()
        destination_layout = QVBoxLayout(self.destination_box)
        destination_layout.setContentsMargins(0, 0, 0, 0)
        destination_layout.setSpacing(2)
        self.destination_input = add_path_list_input(
            destination_layout,
            rows=10,
            placeholder="一対一方式の対象と同じ順序・件数で出力先フォルダを指定します。",
            accepted_path_kind="directory",
            drop_replaces=False,
            show_controls=False,
            path_column_label="出力先パス",
            context_menu_selection_actions=False,
            context_menu_copy_actions=False,
            enable_row_selection=True,
        )
        self.destination_input.setPlainText("")
        self.destination_input.show_full_paths()
        self.destination_input.set_visible_rows(4)
        self.destination_input.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self.destination_list_actions = QWidget()
        destination_actions_layout = FlowLayout(self.destination_list_actions)
        destination_actions_layout.setContentsMargins(0, 0, 0, 0)
        clear_destination_button = QPushButton("一覧を空にする")
        clear_destination_button.setToolTip("出力先一覧の行だけを空にします。実フォルダは削除しません。")
        clear_destination_button.clicked.connect(self.destination_input.clear_items)
        destination_actions_layout.addWidget(clear_destination_button)
        select_all_destination_button = QPushButton("全件チェック")
        select_all_destination_button.setToolTip("出力先一覧のチェックをすべて入れます。")
        select_all_destination_button.clicked.connect(self.destination_input.select_all_items)
        destination_actions_layout.addWidget(select_all_destination_button)
        clear_selection_destination_button = QPushButton("全チェック解除")
        clear_selection_destination_button.setToolTip("出力先一覧のチェックをすべて外します。")
        clear_selection_destination_button.clicked.connect(self.destination_input.clear_item_selection)
        destination_actions_layout.addWidget(clear_selection_destination_button)
        # Work-list actions are above its table; keep the destination actions
        # at the same height when one-to-one mode shows both columns.
        destination_layout.insertWidget(0, self.destination_list_actions)
        self.destination_list_actions.setVisible(False)
        output_operation_layout.addWidget(self.destination_box, 1)

        self.operation_box = QWidget()
        settings_layout = QVBoxLayout(self.operation_box)
        settings_layout.setContentsMargins(0, 0, 0, 0)
        settings_layout.setSpacing(2)
        # Kept as a compatibility alias for callers that used the former name.
        self.operation_settings_box = self.operation_box
        operation_controls = FlowLayout()
        self._operation_controls = operation_controls
        operation_controls.addWidget(self.edit_controls)
        target_controls = QWidget()
        target_layout = QHBoxLayout(target_controls)
        target_layout.setContentsMargins(0, 0, 0, 0)
        target_layout.addWidget(QLabel("操作対象"))
        self.target_scope_combo = NoWheelComboBox()
        self.target_scope_combo.addItem("チェック済み", "checked")
        self.target_scope_combo.addItem("選択中", "selected")
        self.target_scope_combo.addItem("一覧すべて", "all")
        self.target_scope_combo.setToolTip("選択中は青く選択した行です。プレビューを開いた時点の対象で処理します。")
        target_layout.addWidget(self.target_scope_combo)
        operation_controls.addWidget(target_controls)
        operation_choice = QWidget()
        operation_choice_layout = QHBoxLayout(operation_choice)
        operation_choice_layout.setContentsMargins(0, 0, 0, 0)
        operation_choice_layout.addWidget(QLabel("操作"))
        self.operation_combo = NoWheelComboBox()
        self.operation_combo.addItem("コピー", "copy")
        self.operation_combo.addItem("移動", "move")
        self.operation_combo.addItem("圧縮", "zip")
        self.operation_combo.addItem("ゴミ箱へ送る", "trash")
        self.operation_combo.addItem("リネーム", "rename")
        self.operation_combo.addItem("解凍", "extract")
        operation_choice_layout.addWidget(self.operation_combo)
        operation_controls.addWidget(operation_choice)
        self.copy_mode_controls = QWidget()
        copy_mode_controls_layout = QHBoxLayout(self.copy_mode_controls)
        copy_mode_controls_layout.setContentsMargins(0, 0, 0, 0)
        self.output_mode_label = QLabel("コピー方式")
        copy_mode_controls_layout.addWidget(self.output_mode_label)
        self.copy_mode_combo = NoWheelComboBox()
        copy_mode_controls_layout.addWidget(self.copy_mode_combo, 1)
        operation_controls.addWidget(self.copy_mode_controls)
        settings_layout.addLayout(operation_controls)
        self.operation_stack = CurrentPageStack()
        self.operation_stack.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed
        )

        self.operation_stack.addWidget(QWidget())

        self.rename_panel = RenamePanel()
        self.operation_stack.addWidget(self.rename_panel)
        self.rename_operation_box, self._rename_operation_layout = self._make_group("リネーム設定")
        self._rename_operation_layout.addWidget(self.operation_stack, 1)
        self.rename_operation_box.setFlat(False)
        self.rename_operation_box.setStyleSheet(
            "QGroupBox { border: 1px solid #888; border-radius: 4px; margin-top: 8px; }"
            "QGroupBox::title { subcontrol-origin: margin; left: 7px; padding: 0 3px; }"
        )
        self.rename_operation_box.hide()

        # Readiness can wrap across the full width; long notifications remain
        # selectable in a single line instead of taking height from the list.
        self.state_bar = QFrame()
        self.state_bar.setObjectName("file_manager_notification_bar")
        self.state_bar.setFrameShape(QFrame.Shape.StyledPanel)
        self.state_bar.setStyleSheet(
            "QFrame#file_manager_notification_bar {"
            " border: 1px solid rgba(127, 127, 127, 180);"
            " border-radius: 4px;"
            "}"
        )
        self.state_bar.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        state_layout = QVBoxLayout(self.state_bar)
        state_layout.setContentsMargins(5, 1, 5, 1)
        state_layout.setSpacing(0)
        self.operation_summary_label = QLabel()
        self.operation_summary_label.setWordWrap(True)
        self.operation_summary_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        state_layout.addWidget(self.operation_summary_label)
        self.readiness_label = QLabel()
        self.readiness_label.setWordWrap(True)
        self.readiness_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        notification_layout = QVBoxLayout()
        notification_layout.setContentsMargins(0, 0, 0, 0)
        notification_layout.setSpacing(0)
        notification_layout.addWidget(self.readiness_label)
        self.status_label = QLineEdit()
        self.status_label.setReadOnly(True)
        self.status_label.setFrame(False)
        self.status_label.setToolTip("通知の全文を選択・コピーできます。長い通知は左右キーでも確認できます。")
        notification_layout.addWidget(self.status_label)
        state_layout.addLayout(notification_layout)
        self._workspace_layout.addWidget(self.state_bar, 2, 0, 1, 2)

        self.execution_bar = QWidget()
        self._execution_outer_layout = QVBoxLayout(self.execution_bar)
        self._execution_outer_layout.setContentsMargins(0, 0, 0, 0)
        self._execution_outer_layout.setSpacing(2)
        self._execution_outer_layout.addWidget(self.operation_box)
        # Kept as an empty compatibility object.  The actionable controls now
        # share the operation row, which avoids reserving a second row.
        self.execution_controls = QWidget()
        self.execution_controls.hide()
        refresh_button = QPushButton("状態更新")
        refresh_button.setToolTip("表示中のパスについて、種別と存在状態を更新します。")
        refresh_button.clicked.connect(self.refresh_path_states)
        refresh_button.setText("")
        refresh_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_BrowserReload))
        refresh_button.setAccessibleName("状態更新")
        self.persistent_settings_button = QPushButton("永続設定")
        self.persistent_settings_button.setToolTip(
            "パスごとの初期表示・お気に入り・右クリック候補を編集します。出力先は保存しません。"
        )
        self.persistent_settings_button.clicked.connect(self.show_persistent_settings)
        self.undo_button = QPushButton()
        icons = Path(__file__).resolve().parents[3] / "gui" / "assets" / "icons"
        self.undo_button.setIcon(QIcon.fromTheme("edit-undo", QIcon(str(icons / "edit_undo.svg"))))
        self.undo_button.setAccessibleName("画面編集を戻す")
        self.undo_button.clicked.connect(self.undo_ui_change)
        edit_layout.addWidget(self.undo_button)
        self.redo_button = QPushButton()
        self.redo_button.setIcon(QIcon.fromTheme("edit-redo", QIcon(str(icons / "edit_redo.svg"))))
        self.redo_button.setAccessibleName("画面編集をやり直す")
        self.redo_button.setToolTip("画面編集をやり直します。実ファイルの操作は対象外です。")
        self.redo_button.clicked.connect(self.redo_ui_change)
        edit_layout.addWidget(self.redo_button)
        separator = QFrame()
        separator.setFrameShape(QFrame.Shape.VLine)
        separator.setFrameShadow(QFrame.Shadow.Sunken)
        edit_layout.addWidget(separator)
        work_list_actions.addWidget(refresh_button)
        favorites_actions = FlowLayout()
        favorites_actions.addWidget(self.favorite_full_paths_check)
        favorites_actions.addWidget(self.persistent_settings_button)
        favorites_layout.addLayout(favorites_actions)
        self.copy_button = QPushButton("プレビュー・実行…")
        self.copy_button.setToolTip("別ウィンドウで設定とプレビューを確認し、実行します。このボタンだけでは実行しません。実行結果も開いたウィンドウに表示します。")
        self.copy_button.clicked.connect(self.show_operation_confirmation)
        operation_controls.addWidget(self.copy_button)

        self.search_results_input.textChanged.connect(self._work_list_changed)
        self.search_results_input.operationTargetsChanged.connect(self.update_operation_preview)
        self.search_results_input.rowSelectionChanged.connect(self.update_operation_preview)
        self.search_results_input.selectionChanged.connect(self.update_operation_preview)
        self.target_scope_combo.currentIndexChanged.connect(self.update_operation_preview)
        self.target_scope_combo.currentIndexChanged.connect(self._record_ui_state)
        self.search_results_input.operationTargetsChanged.connect(self._record_ui_state)
        self.destination_input.textChanged.connect(self._destination_list_changed)
        self.copy_mode_combo.currentIndexChanged.connect(self._copy_mode_changed)
        self.operation_combo.currentIndexChanged.connect(self._operation_changed)
        self.rename_panel.rules_changed.connect(self.update_operation_preview)
        self._operation_changed()
        for path_list in (
            self.search_results_input,
            self.destination_input,
        ):
            path_list.textChanged.connect(self._record_ui_state)
            path_list.selectionChanged.connect(self._record_ui_state)
            path_list.actionPerformed.connect(
                lambda text, name=self._path_list_name(path_list): self._notify(f"{name}：{text}")
            )
            path_list.set_context_menu_augmenter(
                lambda menu, item, source=path_list: self._add_path_list_context_actions(
                    menu, item, source
                )
            )
        self.operation_combo.currentIndexChanged.connect(self._record_ui_state)
        self.copy_mode_combo.currentIndexChanged.connect(self._record_ui_state)
        self.rename_panel.rules_changed.connect(self._record_ui_state)
        self._undo_states = [self._capture_ui_state()]
        self._update_history_buttons()

    @staticmethod
    def _add_compact_action(
        layout: QGridLayout,
        row: int,
        column: int,
        text: str,
        callback: Callable[[], None],
        tooltip: str,
    ) -> None:
        """Add an equally sized, concise action while preserving its explanation."""
        button = QPushButton(text)
        button.setToolTip(tooltip)
        button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        button.clicked.connect(callback)
        layout.addWidget(button, row, column)

    @staticmethod
    def _make_group(title: str) -> tuple[QGroupBox, QVBoxLayout]:
        """Create one visible workspace boundary with a concise title."""
        group = QGroupBox(title)
        group.setStyleSheet(
            "QGroupBox {"
            " border: 1px solid rgba(127, 127, 127, 180);"
            " border-radius: 4px;"
            " margin-top: 10px;"
            "}"
            "QGroupBox::title {"
            " subcontrol-origin: margin;"
            " left: 8px;"
            " padding: 0 4px;"
            "}"
        )
        layout = QVBoxLayout(group)
        return group, layout

    def refresh_path_states(self) -> None:
        """Refresh UI-only path status columns across every visible path list."""
        lists = (self.search_results_input, self.destination_input)
        for path_list in lists:
            path_list.refresh_states()
        self._notify("表示中のパス状態を更新しました。")

    def _work_list_changed(self) -> None:
        """Refresh only operation readiness when the transient list changes."""
        self.update_operation_preview()
        if not self._record_linkage_refresh_suspended:
            self._refresh_record_linkage_display()

    def _set_selected_work_rows_checked(self, checked: bool) -> None:
        """Apply a checkbox state to blue-selected work-list rows only."""
        selected_count = len(self.search_results_input.row_selected_item_tokens())
        if not selected_count:
            self._notify("作業一覧で、対象行を青く選択してください。")
            return
        changed = self.search_results_input.set_row_selected_items_checked(checked)
        if checked:
            message = f"選択対象 {selected_count}件をチェックしました（変更 {changed}件）。"
        else:
            message = f"選択対象 {selected_count}件のチェックを外しました（変更 {changed}件）。"
        self.update_operation_preview()
        self._notify(f"作業一覧：{message}")

    def _remove_selected_work_rows(self) -> None:
        """Remove blue-selected rows from the transient work list only."""
        selected_count = len(self.search_results_input.row_selected_item_tokens())
        if not selected_count:
            self._notify("作業一覧で、対象行を青く選択してください。")
            return
        removed = self.search_results_input.remove_row_selected_items()
        self._notify(f"作業一覧：選択対象 {removed}件をリストから除外しました。実ファイルは残ります。")

    def send_targets_to_media_information(self) -> None:
        """Offer checked operation targets to the media tool without persistence."""
        paths = self.search_results_input.selected_paths(deduplicate=True)
        if not paths:
            self._notify("作業一覧で、送る項目を1件以上チェックしてください。")
            return
        count = offer_media_paths(paths)
        self._notify(f"{count}件をメディア情報整理へ一時的に渡しました。直接開きます。")
        self._open_media_tool_or_return("media_information")

    def send_targets_to_video_encoder(self) -> None:
        """Offer checked operation targets to the encoder without persistence."""
        paths = self.search_results_input.selected_paths(deduplicate=True)
        if not paths:
            self._notify("作業一覧で、送る項目を1件以上チェックしてください。")
            return
        count = offer_video_encode_paths(paths)
        self._notify(f"{count}件を動画変換へ一時的に渡しました。直接開きます。")
        self._open_media_tool_or_return("video_encoder")

    def _open_media_tool_or_return(self, key: str) -> None:
        """Navigate directly after a one-shot handoff when the launcher supports it."""
        if self._on_open_media_tool is not None:
            self._on_open_media_tool(key)
        else:
            self._on_back()

    def set_open_media_tool_callback(self, callback: Callable[[str], None]) -> None:
        """Let the central launcher replace this screen with a handoff receiver."""
        self._on_open_media_tool = callback

    def _notify(self, text: str) -> None:
        """Show the latest non-persistent, copyable UI notification."""
        self.status_label.setText(f"通知：{text}")
        self.status_label.setCursorPosition(0)
        self.status_label.setToolTip(self.status_label.text())

    def _path_list_name(self, path_list) -> str:  # type: ignore[no-untyped-def]
        """Return a compact location name for a list-originated notification."""
        names = {
            self.search_results_input: "作業一覧",
            self.destination_input: "出力先",
        }
        return names[path_list]

    def _refresh_favorites(self) -> None:
        """Render only paths explicitly marked for the left-side favorites list."""
        self.favorites_list.clear()
        full_paths = self.favorite_full_paths_check.isChecked()
        self.favorites_list.setTextElideMode(
            Qt.TextElideMode.ElideNone if full_paths else Qt.TextElideMode.ElideRight
        )
        self.favorites_list.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded if full_paths else Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        for entry in self._persistent_settings["favorite_paths"]:
            if not entry["favorite"]:
                continue
            path = entry["path"]
            item = QListWidgetItem(path if full_paths else _favorite_display_name(path))
            item.setData(Qt.ItemDataRole.UserRole, path)
            item.setToolTip(path)
            self.favorites_list.addItem(item)

    def _add_favorite_to_work_list(self, item: QListWidgetItem) -> None:
        """Append one favorite without changing any filesystem item."""
        path = str(item.data(Qt.ItemDataRole.UserRole) or "")
        if not path:
            return
        self.search_results_input.append_items((path,))
        self._notify(f"お気に入りを作業一覧へ追加しました: {path}")

    def _build_selection_filter_panel(self) -> QGroupBox:
        """Build the collapsible, transient checkbox-selection conditions."""
        panel, layout = self._make_group("チェック選別条件")

        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(4)
        layout.addLayout(form)
        self.selection_filter_query = QLineEdit()
        self.selection_filter_query.setPlaceholderText("例: .mp4, -sample")
        self.selection_filter_query.setToolTip(", で複数の検索語、-語で除外を指定します。")
        self.selection_filter_query.returnPressed.connect(self.apply_selection_filter)
        form.addRow("検索語", self.selection_filter_query)

        self.selection_filter_mode = NoWheelComboBox()
        self.selection_filter_mode.addItem("部分一致", "contains")
        self.selection_filter_mode.addItem("正規表現", "regex")
        form.addRow("照合", self.selection_filter_mode)

        self.selection_filter_field = NoWheelComboBox()
        self.selection_filter_field.addItem("ファイル名／フォルダ名", "name")
        self.selection_filter_field.addItem("フルパス", "path")
        form.addRow("場所", self.selection_filter_field)

        self.selection_filter_kind = NoWheelComboBox()
        self.selection_filter_kind.addItem("すべて", "all")
        self.selection_filter_kind.addItem("ファイル", "file")
        self.selection_filter_kind.addItem("フォルダ", "directory")
        form.addRow("種別", self.selection_filter_kind)

        self.selection_filter_action = NoWheelComboBox()
        self.selection_filter_action.addItem("一致するものだけチェック", "replace")
        self.selection_filter_action.addItem("一致するものを追加チェック", "add")
        self.selection_filter_action.addItem("一致するもののチェックを外す", "remove")
        form.addRow("更新", self.selection_filter_action)

        layout.addStretch(1)
        apply_button = QPushButton("現在の一覧に反映")
        apply_button.clicked.connect(self.apply_selection_filter)
        layout.addWidget(apply_button)
        return panel

    def _toggle_selection_filter_panel(self, visible: bool) -> None:
        self.selection_filter_panel.setVisible(visible)
        self.selection_filter_toggle.setText("条件で選別 ◂" if visible else "条件で選別 ▸")

    def apply_selection_filter(self) -> None:
        """Update only checks; paths and blue row selection remain untouched."""
        paths = tuple(self.search_results_input.paths())
        try:
            matches = select_paths_by_conditions(
                paths,
                self.selection_filter_query.text(),
                mode=self.selection_filter_mode.currentData(),
                item_kind=self.selection_filter_kind.currentData(),
                match_field=self.selection_filter_field.currentData(),
            )
            changed = self.search_results_input.update_checked_paths(
                matches, mode=self.selection_filter_action.currentData()
            )
        except ValueError as exc:
            self._notify(f"選別条件を反映できません：{exc}")
            QMessageBox.warning(self, "選別条件のエラー", str(exc))
            return
        self._notify(
            f"作業一覧：条件一致 {len(matches)} 件｜チェック変更 {changed} 件"
        )

    def _add_path_list_context_actions(self, menu, item, source) -> None:  # type: ignore[no-untyped-def]
        """Attach file-manager actions and valid persistent path choices."""
        clicked_path = (
            Path(item.text(1))
            if item is not None and item.text(1)
            else None
        )
        checked_archives = (
            tuple(
                path
                for path in source.selected_paths(deduplicate=True)
                if is_supported_archive_path(path)
            )
            if source is self.search_results_input
            else ()
        )
        selected_archives = (
            tuple(
                path
                for path in source.row_selected_paths(deduplicate=True)
                if is_supported_archive_path(path)
            )
            if source is self.search_results_input
            else ()
        )
        clicked_is_directory = (
            clicked_path is not None and inspect_path(clicked_path).kind == "directory"
        )
        checked_tree_paths = self._normal_directory_paths(source.selected_paths(deduplicate=True))
        selected_tree_paths = self._normal_directory_paths(source.row_selected_paths(deduplicate=True))
        all_tree_paths = self._normal_directory_paths(source.paths())
        add_file_manager_path_actions(
            menu,
            item,
            request_expand=lambda scope, with_query: self.expand_directories(
                source, scope=scope, with_query=with_query
            ),
            open_one_directory=(
                (lambda _checked=False, row=item: source.open_directory_item(row))
                if item is not None and clicked_is_directory
                else None
            ),
            choose_one_directory=(
                (lambda _checked=False, row=item: source.choose_direct_children_for_item(row))
                if item is not None and clicked_is_directory
                else None
            ),
            send_to_media_information=(
                self.send_targets_to_media_information
                if source is self.search_results_input
                else None
            ),
            send_to_video_encoder=(
                self.send_targets_to_video_encoder
                if source is self.search_results_input
                else None
            ),
            copy_one_path=lambda path: self._copy_one_path(source, path),
            copy_checked_paths=lambda: self._copy_checked_paths(source),
            copy_selected_paths=lambda: self._copy_selected_paths(source),
            copy_all_paths=lambda: self._copy_all_paths(source),
            copy_one_file_name=lambda path: self._copy_one_file_name(source, path),
            copy_checked_file_names=lambda: self._copy_checked_file_names(source),
            copy_selected_file_names=lambda: self._copy_selected_file_names(source),
            copy_all_file_names=lambda: self._copy_all_file_names(source),
            show_one_tree=lambda path: self._open_tree_copy_dialog((path,)),
            show_checked_trees=lambda: self._open_tree_copy_dialog(checked_tree_paths),
            show_selected_trees=lambda: self._open_tree_copy_dialog(selected_tree_paths),
            show_all_trees=lambda: self._open_tree_copy_dialog(all_tree_paths),
            checked_tree_directory_count=len(checked_tree_paths),
            selected_tree_directory_count=len(selected_tree_paths),
            all_tree_directory_count=len(all_tree_paths),
            copy_one_real_item=(
                self._open_quick_copy_for_one
                if source is self.search_results_input
                else None
            ),
            copy_checked_real_items=(
                self._open_quick_copy_for_checked
                if source is self.search_results_input
                else None
            ),
            copy_selected_real_items=(
                self._open_quick_copy_for_selected
                if source is self.search_results_input
                else None
            ),
            checked_real_item_count=(
                len(source.selected_paths(deduplicate=True))
                if source is self.search_results_input
                else 0
            ),
            selected_real_item_count=(
                len(source.row_selected_paths(deduplicate=True))
                if source is self.search_results_input
                else 0
            ),
            compress_one_real_item=(
                self._open_quick_compress_for_one
                if source is self.search_results_input
                else None
            ),
            compress_checked_real_items=(
                self._open_quick_compress_for_checked
                if source is self.search_results_input
                else None
            ),
            compress_selected_real_items=(
                self._open_quick_compress_for_selected
                if source is self.search_results_input
                else None
            ),
            checked_compression_item_count=(
                len(source.selected_paths(deduplicate=True))
                if source is self.search_results_input
                else 0
            ),
            selected_compression_item_count=(
                len(source.row_selected_paths(deduplicate=True))
                if source is self.search_results_input
                else 0
            ),
            extract_one_archive=(
                self._open_quick_extract_for_one
                if source is self.search_results_input
                and clicked_path is not None
                and is_supported_archive_path(clicked_path)
                else None
            ),
            extract_checked_archives=(
                self._open_quick_extract_for_checked
                if source is self.search_results_input
                else None
            ),
            extract_selected_archives=(
                self._open_quick_extract_for_selected
                if source is self.search_results_input
                else None
            ),
            checked_archive_count=len(checked_archives),
            selected_archive_count=len(selected_archives),
        )
        add_registered_paths_menu(
            menu,
            [
                entry["path"]
                for entry in self._persistent_settings["favorite_paths"]
                if entry["context_menu"]
            ],
            choose_path=lambda value: self._add_registered_path_to_list(source, value),
        )
        if source is self.search_results_input:
            self._add_special_operations_context_menu(menu, item)
        if source is self.search_results_input and self._record_link_bundle is not None:
            self._add_record_linkage_context_menu(menu)

    def _add_special_operations_context_menu(self, menu, item) -> None:  # type: ignore[no-untyped-def]
        """Collect exceptional file operations which do not fit ordinary copy/move flows."""
        menu.addSeparator()
        special_menu = menu.addMenu("特殊操作")
        checked = tuple(self.search_results_input.selected_paths(deduplicate=True))
        parent_move_action = special_menu.addAction(
            f"チェック済み{len(checked)}件を親フォルダへ移動…"
        )
        parent_move_action.setEnabled(bool(checked))
        parent_move_action.setToolTip(
            "全件を事前検査します。名前衝突・別ファイルシステム・親子同時選択があれば移動しません。"
        )
        parent_move_action.triggered.connect(
            lambda _checked=False, paths=checked: self._open_parent_move_dialog(paths)
        )
        directory_name_action = special_menu.addAction(
            f"チェック済み{len(checked)}件の親フォルダ名をファイル名へ…"
        )
        directory_name_action.setEnabled(bool(checked))
        directory_name_action.setToolTip(
            "親フォルダ名を、ファイル名の先頭・末尾・指定位置へ挿入または置換します。"
        )
        directory_name_action.triggered.connect(
            lambda _checked=False, paths=checked: self._open_directory_name_rename_dialog(paths)
        )

    def _add_record_linkage_context_menu(self, menu) -> None:  # type: ignore[no-untyped-def]
        """Keep every record-link command inside one work-list submenu."""
        bundle = self._record_link_bundle
        if bundle is None:
            return
        menu.addSeparator()
        linkage_menu = menu.addMenu("対応表との紐づけ")
        title = bundle.title or "名称なし"
        summary = linkage_menu.addAction(f"対応表：{title}（{len(bundle.rows)}レコード）")
        summary.setEnabled(False)
        linkage_menu.addAction("受け取った対応表の内容を見る…").triggered.connect(
            self.show_record_bundle_contents
        )

        fields_menu = linkage_menu.addMenu("紐づける項目を選ぶ")
        for field_index, field_name in enumerate(bundle.field_names):
            action = fields_menu.addAction(f"項目{field_index + 1}「{field_name}」の名前と紐づける")
            action.setCheckable(True)
            action.setChecked(field_index == self._record_link_field_index)
            action.triggered.connect(
                lambda _checked=False, index=field_index: self._select_record_link_field(index)
            )

        linkage_menu.addSeparator()
        recalculate = linkage_menu.addAction("現在の作業一覧で紐づけを再計算")
        recalculate.setEnabled(self._record_link_field_index is not None)
        recalculate.triggered.connect(self._recalculate_record_linkage)
        keep_linked = linkage_menu.addAction("紐づけ済みのみ一覧に残す")
        keep_linked.setEnabled(bool(self._record_linkage and self._record_linkage.links))
        keep_linked.triggered.connect(self._keep_only_linked_paths)
        bulk_operation = linkage_menu.addAction("紐づけファイル一括操作…")
        bulk_operation.setEnabled(bool(self._record_linkage and self._record_linkage.links))
        bulk_operation.triggered.connect(self._open_linked_record_operations)
        linkage_menu.addSeparator()
        clear_action = linkage_menu.addAction("対応表の紐づけを解除")
        clear_action.triggered.connect(self._clear_record_linkage)

    def _select_record_link_field(self, field_index: int) -> None:
        bundle = self._record_link_bundle
        if bundle is None:
            return
        self._record_link_field_index = field_index
        self._record_linkage = None
        self._refresh_record_linkage_display()
        linkage = self._record_linkage
        if linkage is None:
            return
        self._notify(
            f"項目{field_index + 1}「{bundle.field_names[field_index]}」で紐づけ："
            f"成功 {len(linkage.links)}件｜失敗 {len(linkage.failures)}件"
        )

    def _refresh_record_linkage_display(self, *, preserve_existing: bool = True) -> None:
        """Rebuild path links after any work-list edit without changing normal checks."""
        bundle = self._record_link_bundle
        field_index = self._record_link_field_index
        if bundle is None:
            self._record_linkage = None
            if hasattr(self, "search_results_input"):
                self.search_results_input.set_supplemental_values({})
                self.search_results_input.set_supplemental_column_visible("紐づけ", False)
            self._update_record_bundle_status()
            return
        self.search_results_input.set_supplemental_column_visible("紐づけ", True)
        paths = tuple(self.search_results_input.paths())
        if field_index is None:
            self._record_linkage = None
            self.search_results_input.set_supplemental_values(
                {str(path): {"紐づけ": "項目を選択してください"} for path in paths}
            )
            self._update_record_bundle_status()
            return
        fresh = link_paths_to_record_field(bundle, paths, field_index=field_index)
        previous = self._record_linkage
        if (
            preserve_existing
            and previous is not None
            and previous.bundle == bundle
            and previous.field_index == field_index
        ):
            old_links = {link.source: link for link in previous.links}
            fresh_links = {link.source: link for link in fresh.links}
            fresh_failures = {failure.source: failure for failure in fresh.failures}
            path_counts = Counter(paths)
            links = []
            failures = []
            for path in paths:
                if path in old_links and path_counts[path] == 1:
                    links.append(old_links[path])
                elif path in fresh_links:
                    links.append(fresh_links[path])
                else:
                    failures.append(fresh_failures[path])
            linkage = RecordLinkageResult(bundle, field_index, tuple(links), tuple(failures))
        else:
            linkage = fresh
        self._record_linkage = linkage
        self._apply_record_linkage_display(linkage)

    def _apply_record_linkage_display(self, linkage: RecordLinkageResult) -> None:
        """Render an already-decided linkage without recalculating its identity."""
        values = {
            str(link.source): {"紐づけ": link.display_text}
            for link in linkage.links
        }
        values.update(
            {
                str(failure.source): {"紐づけ": failure.display_text}
                for failure in linkage.failures
            }
        )
        self.search_results_input.set_supplemental_values(values)
        self._update_record_bundle_status()

    def _update_record_bundle_status(self) -> None:
        """Summarize the held table in one compact work-list action button."""
        if not hasattr(self, "show_record_bundle_button"):
            return
        bundle = self._record_link_bundle
        if bundle is None:
            text = "対応表：なし"
            tooltip = "ファイルマネージャーには対応表が渡されていません。"
            style = ""
            self.show_record_bundle_button.setEnabled(True)
        else:
            title = bundle.title or "名称なし"
            base = (
                f"対応表：{title} ｜ {len(bundle.rows)}レコード × "
                f"{len(bundle.field_names)}項目"
            )
            if self._record_link_field_index is None:
                text = "対応表：あり（未紐づけ）"
                detail = base + " ｜ 紐づけ項目：未選択"
                style = (
                    "QPushButton { background: #e7f1ea; border: 1px solid #57966b; "
                    "border-radius: 4px; padding: 3px 8px; font-weight: 600; }"
                )
            else:
                field_index = self._record_link_field_index
                field_name = bundle.field_names[field_index]
                linkage = self._record_linkage
                linked_count = len({
                    link.row_identifier for link in linkage.links
                }) if linkage is not None else 0
                failure_count = len(linkage.failures) if linkage is not None else 0
                text = f"対応表：成功 {linked_count}/{len(bundle.rows)}"
                detail = (
                    base
                    + f" ｜ 項目{field_index + 1}「{field_name}」"
                    + f" ｜ 紐づけ済みレコード {linked_count} / "
                    + f"未紐づけレコード {len(bundle.rows) - linked_count}"
                    + f" ｜ 対象パス側の失敗 {failure_count}"
                )
                color = "#dff3e4" if linked_count == len(bundle.rows) else "#fff1c9"
                border = "#57966b" if linked_count == len(bundle.rows) else "#b68a28"
                style = (
                    f"QPushButton {{ background: {color}; border: 1px solid {border}; "
                    "border-radius: 4px; padding: 3px 8px; font-weight: 600; }"
                )
            tooltip = (
                detail
                + "\nクリックすると全レコードと紐づけ結果を確認できます。"
                + "\n紐づけ操作は作業一覧の右クリックにあります。"
            )
            self.show_record_bundle_button.setEnabled(True)
        if self._record_source_id:
            text += f" [{self._record_source_id}]"
            tooltip += f"\n取得時の対応表：[{self._record_source_id}] 第{self._record_source_revision}版。元の編集は自動反映されません。"
        self.show_record_bundle_button.setText(text)
        self.show_record_bundle_button.setToolTip(tooltip)
        self.show_record_bundle_button.setStyleSheet(style)

    def show_record_bundle_contents(self) -> None:
        """Open a non-editable table containing the exact received snapshot."""
        bundle = self._record_link_bundle
        if bundle is None:
            self._notify("確認できる対応表はありません。")
            return
        linkage = self._record_linkage
        dialog = RecordBundleViewerDialog(
            bundle,
            linked_field_index=self._record_link_field_index,
            linkage=linkage,
            source_description=(f"取得済み：[{self._record_source_id}] 第{self._record_source_revision}版（元の編集は自動反映しません）" if self._record_source_id else "取得済みの対応表"),
            parent=self,
        )
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self._record_bundle_viewers.add(dialog)
        dialog.destroyed.connect(
            lambda _object=None, current=dialog: self._record_bundle_viewers.discard(current)
        )
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _recalculate_record_linkage(self) -> None:
        self._refresh_record_linkage_display(preserve_existing=False)
        linkage = self._record_linkage
        if linkage is not None:
            self._notify(
                f"紐づけを再計算：成功 {len(linkage.links)}件｜失敗 {len(linkage.failures)}件"
            )

    def _keep_only_linked_paths(self) -> None:
        linkage = self._record_linkage
        if linkage is None or not linkage.links:
            self._notify("一覧に残せる紐づけ済みパスがありません。")
            return
        linked = {str(path) for path in linkage.linked_paths}
        retained = tuple(
            row
            for row in self.search_results_input.snapshot()
            if str(normalize_path(str(row[0]))) in linked
        )
        self.search_results_input.restore_snapshot(retained)
        self._notify(f"紐づけ済み {len(retained)}件だけを作業一覧に残しました。")

    def _open_linked_record_operations(self) -> None:
        self._refresh_record_linkage_display()
        linkage = self._record_linkage
        if linkage is None or not linkage.links:
            self._notify("一括操作できる紐づけ済みパスがありません。")
            return
        dialog = LinkedRecordOperationsDialog(linkage)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self._linked_record_dialogs.add(dialog)
        _retain_independent_operation_window(dialog)
        dialog.paths_renamed.connect(self._linked_paths_renamed)
        dialog.destroyed.connect(
            lambda _object=None, current=dialog: self._linked_record_dialogs.discard(current)
        )
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _linked_paths_renamed(self, changes: dict[str, str]) -> None:
        """Reflect successful external names in-place while preserving check states."""
        normalized_changes = {
            str(normalize_path(source)): output for source, output in changes.items()
        }
        rows = []
        for row in self.search_results_input.snapshot():
            current = str(normalize_path(str(row[0])))
            rows.append((normalized_changes.get(current, row[0]), *row[1:]))
        old_linkage = self._record_linkage
        self._record_linkage_refresh_suspended = True
        try:
            self.search_results_input.restore_snapshot(tuple(rows))
        finally:
            self._record_linkage_refresh_suspended = False
        if old_linkage is not None:
            migrated_links = tuple(
                replace(
                    link,
                    source=Path(normalized_changes.get(str(link.source), str(link.source))),
                )
                for link in old_linkage.links
            )
            self._record_linkage = RecordLinkageResult(
                old_linkage.bundle,
                old_linkage.field_index,
                migrated_links,
                old_linkage.failures,
            )
            self._apply_record_linkage_display(self._record_linkage)
        self._notify(f"紐づけ一括操作：{len(changes)}件の新しいパスを作業一覧へ反映しました。")

    def _clear_record_linkage(self) -> None:
        self._record_source_id = None
        self._record_source_revision = None
        self._record_link_bundle = None
        self._record_link_field_index = None
        self._record_linkage = None
        self.search_results_input.set_supplemental_values({})
        self.search_results_input.set_supplemental_column_visible("紐づけ", False)
        self._update_record_bundle_status()
        self._notify("対応表との紐づけを解除しました。作業一覧のパスは変更していません。")

    def _copy_checked_paths(self, path_list) -> None:  # type: ignore[no-untyped-def]
        """Copy every checked filesystem path from one list as newline-separated text."""
        self._copy_path_values(path_list, path_list.selected_paths(), "チェック済み")

    @staticmethod
    def _normal_directory_paths(paths: Iterable[Path]) -> tuple[Path, ...]:
        """Keep ordinary folders only; a tree never follows symbolic links."""
        return tuple(
            normalize_path(path)
            for path in paths
            if inspect_path(path).kind == "directory"
        )

    def _open_tree_copy_dialog(self, paths: tuple[Path, ...]) -> None:
        """Freeze a bounded folder tree in a parentless editable text window."""
        if not paths:
            self._notify("ツリー表示：対象になる通常のフォルダがありません。")
            return
        try:
            dialog = TreeCopyDialog(paths)
        except ValueError as exc:
            self._notify(f"ツリー表示を開けません：{exc}")
            QMessageBox.warning(self, "ツリー表示", str(exc))
            return
        self._tree_copy_dialogs.add(dialog)
        dialog.destroyed.connect(
            lambda _object=None, current=dialog: self._tree_copy_dialogs.discard(current)
        )
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _copy_selected_paths(self, path_list) -> None:  # type: ignore[no-untyped-def]
        self._copy_path_values(path_list, path_list.row_selected_paths(), "選択中")

    def _copy_all_paths(self, path_list) -> None:  # type: ignore[no-untyped-def]
        self._copy_path_values(path_list, path_list.paths(), "全件")

    def _copy_one_path(self, path_list, path: Path) -> None:  # type: ignore[no-untyped-def]
        self._copy_path_values(path_list, (path,), "この1件")

    def _copy_path_values(self, path_list, paths: Iterable[Path], label: str) -> None:  # type: ignore[no-untyped-def]
        values = tuple(paths)
        if not values:
            self._notify(f"{self._path_list_name(path_list)}：{label}のパスがありません。")
            return
        QApplication.clipboard().setText("\n".join(str(path) for path in values))
        self._notify(f"{self._path_list_name(path_list)}：{label}{len(values)}件のパスをコピーしました。")

    def _copy_checked_file_names(self, path_list) -> None:  # type: ignore[no-untyped-def]
        """Copy only the file-name portion of every checked filesystem path."""
        self._copy_file_name_values(path_list, path_list.selected_paths(), "チェック済み")

    def _copy_selected_file_names(self, path_list) -> None:  # type: ignore[no-untyped-def]
        self._copy_file_name_values(path_list, path_list.row_selected_paths(), "選択中")

    def _copy_all_file_names(self, path_list) -> None:  # type: ignore[no-untyped-def]
        self._copy_file_name_values(path_list, path_list.paths(), "全件")

    def _copy_one_file_name(self, path_list, path: Path) -> None:  # type: ignore[no-untyped-def]
        self._copy_file_name_values(path_list, (path,), "この1件")

    def _copy_file_name_values(self, path_list, paths: Iterable[Path], label: str) -> None:  # type: ignore[no-untyped-def]
        values = tuple(paths)
        if not values:
            self._notify(f"{self._path_list_name(path_list)}：{label}のファイル名がありません。")
            return
        QApplication.clipboard().setText("\n".join(path.name for path in values))
        self._notify(f"{self._path_list_name(path_list)}：{label}{len(values)}件のファイル名をコピーしました。")

    def _current_quick_copy_destination(self) -> str:
        """Reuse only this open screen's last validated ordinary destination."""
        return self._session_last_destination

    def _open_parent_move_dialog(self, paths: tuple[Path, ...]) -> None:
        """Open the reversible special operation with a frozen set of path entries."""
        if not paths:
            self._notify("親フォルダへ移動：対象がありません。")
            return
        dialog = ParentMoveDialog(paths)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self._parent_move_dialogs.add(dialog)
        _retain_independent_operation_window(dialog)
        dialog.paths_moved.connect(self._parent_move_paths_changed)
        dialog.paths_restored.connect(self._parent_move_paths_changed)
        dialog.destroyed.connect(
            lambda _object=None, current=dialog: self._parent_move_dialogs.discard(current)
        )
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _open_directory_name_rename_dialog(self, paths: tuple[Path, ...]) -> None:
        """Open the special filename editor for a frozen checked-file set."""
        if not paths:
            self._notify("親フォルダ名をファイル名へ：対象がありません。")
            return
        dialog = DirectoryNameRenameDialog(paths)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self._directory_name_rename_dialogs.add(dialog)
        _retain_independent_operation_window(dialog)
        dialog.paths_renamed.connect(self._directory_name_paths_renamed)
        dialog.destroyed.connect(
            lambda _object=None, current=dialog: self._directory_name_rename_dialogs.discard(current)
        )
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _parent_move_paths_changed(self, changes: dict[str, str]) -> None:
        """Keep the transient work list pointing at the entry after move or undo."""
        normalized_changes = {
            str(normalize_path(source)): output for source, output in changes.items()
        }
        rows = []
        for row in self.search_results_input.snapshot():
            current = str(normalize_path(str(row[0])))
            rows.append((normalized_changes.get(current, row[0]), *row[1:]))
        self.search_results_input.restore_snapshot(tuple(rows))
        self._notify(f"親フォルダへ移動：作業一覧のパスを{len(changes)}件更新しました。")

    def _directory_name_paths_renamed(self, changes: dict[str, str]) -> None:
        """Update renamed rows and preserve an already-established record link."""
        normalized_changes = {
            str(normalize_path(source)): output for source, output in changes.items()
        }
        rows = []
        for row in self.search_results_input.snapshot():
            current = str(normalize_path(str(row[0])))
            rows.append((normalized_changes.get(current, row[0]), *row[1:]))
        old_linkage = self._record_linkage
        self._record_linkage_refresh_suspended = True
        try:
            self.search_results_input.restore_snapshot(tuple(rows))
        finally:
            self._record_linkage_refresh_suspended = False
        if old_linkage is not None:
            migrated_links = tuple(
                replace(
                    link,
                    source=Path(normalized_changes.get(str(link.source), str(link.source))),
                )
                for link in old_linkage.links
            )
            self._record_linkage = RecordLinkageResult(
                old_linkage.bundle,
                old_linkage.field_index,
                migrated_links,
                old_linkage.failures,
            )
            self._apply_record_linkage_display(self._record_linkage)
        self._notify(
            f"親フォルダ名をファイル名へ：作業一覧のパスを{len(changes)}件更新しました。"
        )

    def _open_quick_copy_for_one(self, path: Path) -> None:
        """Open a parentless copy window for exactly the clicked filesystem entry."""
        self._open_quick_copy_dialog((normalize_path(path),))

    def _open_quick_copy_for_checked(self) -> None:
        """Freeze checked rows into an independent copy window."""
        paths = tuple(self.search_results_input.selected_paths(deduplicate=True))
        if not paths:
            self._notify("作業一覧：チェック済みのコピー対象がありません。")
            return
        self._open_quick_copy_dialog(paths)

    def _open_quick_copy_for_selected(self) -> None:
        """Freeze blue-highlighted rows, independently of their check states."""
        paths = tuple(self.search_results_input.row_selected_paths(deduplicate=True))
        if not paths:
            self._notify("作業一覧：選択中のコピー対象がありません。")
            return
        self._open_quick_copy_dialog(paths)

    def _open_quick_copy_dialog(self, paths: tuple[Path, ...]) -> None:
        dialog = QuickCopyDialog(paths, self._current_quick_copy_destination())
        self._quick_copy_dialogs.add(dialog)
        _retain_independent_operation_window(dialog)
        dialog.destroyed.connect(
            lambda _object=None, current=dialog: self._quick_copy_dialogs.discard(current)
        )
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _open_quick_extract_for_one(self, path: Path) -> None:
        """Open a parentless extraction window for the clicked archive only."""
        self._open_quick_extract_dialog((normalize_path(path),))

    def _handle_work_list_double_click(self, item, column: int) -> None:  # type: ignore[no-untyped-def]
        """Open archives in extraction; ordinary files retain their inert double click."""
        if column != 1:
            return
        value = item.text(1)
        if not value:
            return
        path = normalize_path(value)
        if is_supported_archive_path(path) and inspect_path(path).kind == "file":
            self._open_quick_extract_dialog((path,))

    def _open_quick_extract_for_checked(self) -> None:
        """Freeze checked ZIP, 7z and RAR paths into an independent window."""
        archives = tuple(
            path
            for path in self.search_results_input.selected_paths(deduplicate=True)
            if is_supported_archive_path(path)
        )
        if not archives:
            self._notify("作業一覧：チェック済みのZIP・7z・RARがありません。")
            return
        self._open_quick_extract_dialog(archives)

    def _open_quick_extract_for_selected(self) -> None:
        archives = tuple(
            path
            for path in self.search_results_input.row_selected_paths(deduplicate=True)
            if is_supported_archive_path(path)
        )
        if not archives:
            self._notify("作業一覧：選択中のZIP・7z・RARがありません。")
            return
        self._open_quick_extract_dialog(archives)

    def _open_quick_extract_dialog(self, archives: tuple[Path, ...]) -> None:
        dialog = QuickExtractDialog(archives, self._current_quick_copy_destination())
        self._quick_extract_dialogs.add(dialog)
        _retain_independent_operation_window(dialog)
        dialog.destroyed.connect(
            lambda _object=None, current=dialog: self._quick_extract_dialogs.discard(current)
        )
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _open_quick_compress_for_one(self, path: Path) -> None:
        """Open a parentless compression window for the clicked entry only."""
        self._open_quick_compress_dialog((normalize_path(path),))

    def _open_quick_compress_for_checked(self) -> None:
        """Freeze checked files and folders into an independent compression window."""
        sources = tuple(self.search_results_input.selected_paths(deduplicate=True))
        if not sources:
            self._notify("作業一覧：チェック済みの圧縮対象がありません。")
            return
        self._open_quick_compress_dialog(sources)

    def _open_quick_compress_for_selected(self) -> None:
        sources = tuple(self.search_results_input.row_selected_paths(deduplicate=True))
        if not sources:
            self._notify("作業一覧：選択中の圧縮対象がありません。")
            return
        self._open_quick_compress_dialog(sources)

    def _open_quick_compress_dialog(self, sources: tuple[Path, ...]) -> None:
        dialog = QuickCompressDialog(sources)
        self._quick_compress_dialogs.add(dialog)
        _retain_independent_operation_window(dialog)
        dialog.destroyed.connect(
            lambda _object=None, current=dialog: self._quick_compress_dialogs.discard(current)
        )
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _add_registered_path_to_list(self, path_list, value: str) -> None:  # type: ignore[no-untyped-def]
        """Apply a registered path using the receiving list's normal policy."""
        count = path_list.add_external_paths(
            [normalize_path(value)], replace=path_list.drop_replaces
        )
        action = "置換" if path_list.drop_replaces else "追加"
        self._notify(f"{self._path_list_name(path_list)}：登録パスを{action}しました（{count}件）。")

    def expand_checked_directories(self, path_list, *, with_query: bool) -> None:  # type: ignore[no-untyped-def]
        """Compatibility entry point for the checkbox-scoped expansion."""
        self.expand_directories(path_list, scope="checked", with_query=with_query)

    def expand_directories(
        self, path_list, *, scope: str, with_query: bool  # type: ignore[no-untyped-def]
    ) -> None:
        """Expand checked or blue-selected folders without conflating the scopes."""
        if scope == "checked":
            source_paths = path_list.selected_paths(deduplicate=True)
            scope_label = "チェック済み"
        elif scope == "selected":
            source_paths = path_list.row_selected_paths(deduplicate=True)
            scope_label = "選択中"
        else:
            raise ValueError(f"未対応の展開範囲です: {scope}")
        query = ""
        mode = "contains"
        item_kind = "all"
        if with_query:
            dialog = QDialog(self)
            dialog.setWindowTitle("条件を指定して直下項目へ展開")
            layout = QVBoxLayout(dialog)
            layout.addWidget(QLabel("範囲: 直下のみ"))
            query_input = QLineEdit()
            query_input.setPlaceholderText("検索語（, で複数・-語で除外）")
            layout.addWidget(query_input)
            condition_layout = QHBoxLayout()
            condition_layout.addWidget(QLabel("方法"))
            mode_combo = NoWheelComboBox()
            mode_combo.addItem("部分一致", "contains")
            mode_combo.addItem("正規表現", "regex")
            condition_layout.addWidget(mode_combo)
            condition_layout.addWidget(QLabel("種別"))
            kind_combo = NoWheelComboBox()
            kind_combo.addItem("すべて", "all")
            kind_combo.addItem("ファイル", "file")
            kind_combo.addItem("フォルダ", "directory")
            condition_layout.addWidget(kind_combo)
            layout.addLayout(condition_layout)
            buttons = QHBoxLayout()
            cancel = QPushButton("キャンセル")
            cancel.clicked.connect(dialog.reject)
            buttons.addWidget(cancel)
            apply = QPushButton("展開")
            apply.clicked.connect(dialog.accept)
            buttons.addWidget(apply)
            layout.addLayout(buttons)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            query = query_input.text()
            mode = mode_combo.currentData()
            item_kind = kind_combo.currentData()
        try:
            children, folder_count = direct_children_for_selected_folders(
                source_paths,
                query,
                mode=mode,
                item_kind=item_kind,
            )
        except (OSError, ValueError) as exc:
            self._notify(f"展開していません：{exc}")
            QMessageBox.warning(self, "展開できません", str(exc))
            return

        if scope == "checked":
            path_list.replace_checked_items(str(path) for path in children)
        else:
            path_list.replace_row_selected_items(str(path) for path in children)
        condition = (
            f"条件「{query}」"
            if with_query and query.strip()
            else ("条件なし" if with_query else "すべて")
        )
        self._notify(
            f"{self._path_list_name(path_list)}：{scope_label}{len(source_paths)}件を置換"
            f"｜展開: {folder_count}フォルダ｜追加: {len(children)}件｜{condition}"
        )

    def _capture_ui_state(self) -> dict[str, object]:
        """Capture only in-memory controls eligible for UI undo/redo."""
        state = {
            "results": self.search_results_input.snapshot(),
            "destinations": self.destination_input.snapshot(),
            "operation": self.operation_combo.currentIndex(),
            "copy_mode": self.copy_mode_combo.currentIndex(),
            "target_scope": self.target_scope_combo.currentIndex(),
            "rename_rules": self.rename_panel.rules(),
            "rename_extension": self.rename_panel.include_extension(),
        }
        state["_memory_cost"] = sum(
            len(str(row[0])) * 4 + 128
            for key in ("results", "destinations") for row in state[key]
        ) + sum(len(str(rule)) * 4 for rule in state["rename_rules"])
        return state

    def _record_ui_state(self) -> None:
        if self._history_restoring or self._history_suspended:
            return
        state = self._capture_ui_state()
        if self._undo_states and state == self._undo_states[-1]:
            return
        self._undo_states.append(state)
        self._redo_states.clear()
        # Keep at most 50 undo steps and roughly 16 MiB of path characters.
        # The current state is always retained, even for one enormous list.
        while len(self._undo_states) > 1 and (
            len(self._undo_states) > 51
            or sum(entry["_memory_cost"] for entry in self._undo_states) > 16 * 1024 * 1024
        ):
            self._undo_states.pop(0)
        self._update_history_buttons()

    def _restore_ui_state(self, state: dict[str, object]) -> None:
        self._history_restoring = True
        try:
            self.search_results_input.restore_snapshot(state["results"])  # type: ignore[arg-type]
            self.operation_combo.setCurrentIndex(state["operation"])  # type: ignore[arg-type]
            self.copy_mode_combo.setCurrentIndex(state["copy_mode"])  # type: ignore[arg-type]
            self.target_scope_combo.setCurrentIndex(state.get("target_scope", 0))
            # Mode first: one-to-one snapshots may legitimately contain more
            # than one destination, while normal mode deliberately cannot.
            self.destination_input.restore_snapshot(state["destinations"])  # type: ignore[arg-type]
            self.rename_panel.restore_rules(
                state["rename_rules"], include_extension=state["rename_extension"]  # type: ignore[arg-type]
            )
            self.update_operation_preview()
        finally:
            self._history_restoring = False

    def undo_ui_change(self) -> None:
        if len(self._undo_states) < 2:
            return
        self._redo_states.append(self._undo_states.pop())
        self._restore_ui_state(self._undo_states[-1])
        self._notify("画面操作を1つ戻しました。")
        self._update_history_buttons()

    def redo_ui_change(self) -> None:
        if not self._redo_states:
            return
        state = self._redo_states.pop()
        self._undo_states.append(state)
        self._restore_ui_state(state)
        self._notify("画面操作をやり直しました。")
        self._update_history_buttons()

    def _update_history_buttons(self) -> None:
        self.undo_button.setToolTip("画面の操作を戻します。履歴は最大50段階・概算16MiBまで、この起動中だけ保持します。実ファイル操作は戻しません。")
        self.undo_button.setEnabled(len(self._undo_states) > 1)
        self.redo_button.setEnabled(bool(self._redo_states))

    def show_persistent_settings(self) -> None:
        """Show every persistable value as editable text; nothing is saved automatically."""
        if self._settings_dialog is None:
            dialog = _DiscardAwareDialog(self, self._confirm_close_persistent_settings)
            dialog.setWindowTitle("永続設定を編集")
            dialog.resize(preferred_window_size(dialog))
            layout = QVBoxLayout(dialog)
            self._settings_status_label = QLabel()
            self._settings_status_label.setWordWrap(True)
            layout.addWidget(self._settings_status_label)
            layout.addWidget(QLabel("保存対象は、各パスの初期作業一覧・お気に入り・右クリック候補です。出力先は保存できません。"))
            layout.addWidget(QLabel("書き方: @HOME、@PORTA、@USER、@CONFIG、設定ファイル基準の相対パス、または絶対パスを使えます。"))
            layout.addWidget(QLabel("initial_work_list: 起動時の作業一覧／favorite: 左側のお気に入り／context_menu: 右クリック候補。各値は true または false です。"))
            editor = JsonSettingsEditor(
                validate=validate_text,
                path_keys={"path"},
                fields={
                    "favorite_paths": JsonFieldSpec("登録する場所", "同じ場所に複数の役割を付けられます。"),
                    "path": JsonFieldSpec("パス", "空欄の項目は使用されません。"),
                    "initial_work_list": JsonFieldSpec("起動時の作業一覧", "起動時にこの場所を作業一覧へ入れます。"),
                    "favorite": JsonFieldSpec("お気に入り", "左側のお気に入りへ表示します。"),
                    "context_menu": JsonFieldSpec("右クリック候補", "パス選択用の右クリックメニューへ表示します。"),
                },
            )
            layout.addWidget(editor, 1)
            actions = QHBoxLayout()
            template_button = QPushButton("雛形に戻す")
            editor.bind_edit_button(template_button)
            template_button.clicked.connect(lambda: editor.setPlainText(template_text()))
            actions.addWidget(template_button)
            actions.addStretch()
            save_button = QPushButton("保存")
            editor.bind_save_button(save_button)
            save_button.clicked.connect(self.save_persistent_settings)
            actions.addWidget(save_button)
            close_button = QPushButton("閉じる")
            close_button.clicked.connect(dialog.close)
            actions.addWidget(close_button)
            layout.addLayout(actions)
            self._settings_dialog = dialog
            self._settings_editor = editor
        self._settings_baseline_text = editable_text()
        self._settings_editor.setPlainText(self._settings_baseline_text)
        state, detail = settings_status()
        self._settings_editor.set_source_state(state, detail)
        self._settings_status_label.setText(f"設定状態: {state} — {detail}")
        self._settings_dialog.show()
        self._settings_dialog.raise_()
        self._settings_dialog.activateWindow()

    def _confirm_close_persistent_settings(self) -> bool:
        """Allow a close immediately unless the visible JSON differs from its saved state."""
        editor = self._settings_editor
        if editor is None or not editor.has_unsaved_changes(self._settings_baseline_text):
            return True
        answer = QMessageBox.question(
            self._settings_dialog or self,
            "保存していない変更があります",
            "変更を保存せずに閉じますか？",
            QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        return answer == QMessageBox.StandardButton.Discard

    def save_persistent_settings(self) -> None:
        if self._settings_editor is None:
            return
        try:
            saved_text = self._settings_editor.toPlainText()
            self._persistent_settings = save_text(saved_text)
        except ValueError as exc:
            QMessageBox.warning(self, "保存できません", str(exc))
            return
        self._settings_baseline_text = saved_text
        self._refresh_favorites()
        self._notify("永続設定を保存しました。お気に入りと右クリック候補はすぐ使えます。初期作業一覧は次回起動時に反映されます。")
        QMessageBox.information(
            self,
            "永続設定を保存しました",
            "お気に入りと右クリック候補はすぐ使えます。出力先は保存していません。",
        )

    def _operation_changed(self) -> None:
        """Show only inputs used by the selected operation, preserving other inputs."""
        operation = self.operation_combo.currentData()
        self.operation_stack.setCurrentIndex(1 if operation == "rename" else 0)
        self.copy_mode_controls.setVisible(operation not in {"rename", "trash", "extract"})
        self.operation_box.setVisible(True)
        self.operation_stack.setVisible(operation == "rename")
        self.operation_stack.setSizePolicy(
            QSizePolicy.Policy.Preferred,
            QSizePolicy.Policy.Expanding
            if operation == "rename"
            else QSizePolicy.Policy.Fixed,
        )
        if operation not in {"rename", "trash", "extract"}:
            self.copy_mode_combo.blockSignals(True)
            self.copy_mode_combo.clear()
            if operation == "zip":
                self.output_mode_label.setText("圧縮方式")
                self.copy_mode_combo.addItem("その場でZIP", "in_place")
                self.copy_mode_combo.addItem("指定先へZIP", "simple")
                self.copy_mode_combo.addItem("一対一ZIP", "one_to_one")
                self.copy_mode_combo.addItem("7z / ZIP（詳細設定）", "archive")
            else:
                self.output_mode_label.setText("コピー方式" if operation == "copy" else "移動方式")
                prefix = "コピー" if operation == "copy" else "移動"
                self.copy_mode_combo.addItem(f"通常{prefix}", "simple")
                self.copy_mode_combo.addItem(f"一対一{prefix}", "one_to_one")
            self.copy_mode_combo.blockSignals(False)
        self._configure_destination_input()
        self._set_destination_visible(self._uses_multiple_destinations())
        self._update_workspace_layout()
        self.update_operation_preview()

    def _copy_mode_changed(self) -> None:
        """Reflow the workspace when a mode needs multi-destination space."""
        self._configure_destination_input()
        self._set_destination_visible(self._uses_multiple_destinations())
        self._update_workspace_layout()
        self.update_operation_preview()

    def _set_destination_visible(self, visible: bool) -> None:
        """Hide destination controls entirely when the selected operation has none."""
        self.output_operation_box.setVisible(visible)
        self.destination_box.setVisible(visible)
        self.destination_input.setVisible(visible)
        self.destination_list_actions.setVisible(visible)

    def _destination_list_changed(self) -> None:
        """Update one-to-one readiness without creating a final preview."""
        self.update_operation_preview()

    def _configure_destination_input(self) -> None:
        """The main-screen destination list belongs only to one-to-one modes."""
        self.destination_input.set_maximum_items(None)
        self.destination_input.set_drop_replaces(False)
        self.destination_input.set_visible_rows(4)
        self.destination_input.setToolTip(
            "一対一方式の出力先です。対象と同じ順序・件数で指定します。"
            "通常方式の出力先は実行確認画面で入力します。"
        )

    def _uses_multiple_destinations(self) -> bool:
        return (
            self.operation_combo.currentData() in {"copy", "move", "zip"}
            and self.copy_mode_combo.currentData() == "one_to_one"
        )

    def _uses_three_column_layout(self) -> bool:
        """Return whether preparation needs a second full-height panel."""
        return self._uses_multiple_destinations() or self.operation_combo.currentData() == "rename"

    def _place_operation_box(self) -> None:
        """Only rename rules occupy the side pane; common actions stay below."""
        self.rename_operation_box.setVisible(self.operation_combo.currentData() == "rename")
        self.operation_box.show()

    def _update_workspace_layout(self) -> None:
        """Let the user divide space, with separate narrow/wide arrangements."""
        self._place_operation_box()
        self._narrow_workspace = self.width() < 960
        if not hasattr(self, "_panes_splitter"):
            self._split_sizes = {}
            self._split_key = None
            self._panes_splitter = AdaptiveSplitter(Qt.Orientation.Horizontal)
            self._panes_splitter.setChildrenCollapsible(False)
            self._work_splitter = AdaptiveSplitter()
            self._work_splitter.setChildrenCollapsible(False)
            self.favorites_box.setMaximumWidth(16_777_215)
            self._panes_splitter.addWidget(self.favorites_box)
            self._panes_splitter.addWidget(self._work_splitter)
            for panel in (self.search_results_box, self.output_operation_box, self.rename_operation_box):
                self._work_splitter.addWidget(panel)
            self._panes_splitter.setStretchFactor(0, 0)
            self._panes_splitter.setStretchFactor(1, 1)
            self._panes_splitter.setSizes([
                self.favorites_box.sizeHint().width(), self._work_splitter.sizeHint().width()
            ])
            self._panes_scroll = PageScrollArea()
            self._panes_scroll.setWidgetResizable(True)
            self._panes_scroll.setFrameShape(QFrame.Shape.NoFrame)
            self._panes_scroll.setWidget(self._panes_splitter)
            self._workspace_layout.addWidget(self._panes_scroll, 0, 0, 1, 3)
            self._workspace_layout.addWidget(self.execution_bar, 1, 0, 1, 3)
            self._workspace_layout.addWidget(self.state_bar, 2, 0, 1, 3)
            self._workspace_layout.setRowStretch(0, 1)
            self._workspace_layout.setRowStretch(1, 0)
            self._workspace_layout.setRowStretch(2, 0)

        rename = self.operation_combo.currentData() == "rename"
        multiple = self._uses_multiple_destinations()
        key = (self._narrow_workspace, rename, multiple)
        if self._split_key != key:
            if self._split_key is not None:
                self._split_sizes[self._split_key] = self._work_splitter.sizes()
            self._work_splitter.setOrientation(
                Qt.Orientation.Vertical if self._narrow_workspace else Qt.Orientation.Horizontal
            )
            self.output_operation_box.setVisible(multiple)
            self.rename_operation_box.setVisible(rename)
            dimension = (
                (lambda panel: panel.sizeHint().height())
                if self._narrow_workspace
                else (lambda panel: panel.sizeHint().width())
            )
            defaults = [max(1, dimension(panel)) for panel in (
                self.search_results_box, self.output_operation_box, self.rename_operation_box
            )]
            self._work_splitter.setSizes(self._split_sizes.get(key, defaults))
            self._split_key = key
        self._workspace_layout.invalidate()
        self.updateGeometry()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if (hasattr(self, "_narrow_workspace")
                and self._narrow_workspace != (self.width() < 960)):
            self._update_workspace_layout()

    def _operation_targets(self) -> tuple[Path, ...]:
        scope = self.target_scope_combo.currentData()
        source = self.search_results_input
        if scope == "selected":
            return tuple(source.row_selected_paths(deduplicate=True))
        if scope == "all":
            return tuple(source.paths(deduplicate=True))
        return tuple(source.selected_paths(deduplicate=True))

    def update_operation_preview(self) -> None:
        """Show preparation readiness; final preview belongs to another window."""
        operation = self.operation_combo.currentData()
        mode = self.copy_mode_combo.currentData()
        targets = self._operation_targets()
        target_count = len(targets)
        destination_count = len(self.destination_input.items())
        operation_label = {
            "copy": "コピー",
            "move": "移動",
            "zip": "圧縮",
            "extract": "解凍",
            "trash": "ゴミ箱へ送る",
            "rename": "リネーム",
        }[operation]
        summary = [f"操作: {operation_label}", f"{self.target_scope_combo.currentText()}: {target_count} 件"]
        ready = target_count > 0
        reason = ""
        if not ready:
            reason = f"操作対象「{self.target_scope_combo.currentText()}」に項目がありません。"
        elif operation == "extract" and not all(is_supported_archive_path(path) for path in targets):
            ready = False
            reason = "解凍対象にはZIP・7z・RARだけを指定してください。"
        elif self._uses_multiple_destinations() and destination_count != target_count:
            ready = False
            reason = f"一対一の出力先を対象と同じ{target_count}件にしてください。"
        elif operation == "rename" and not self.rename_panel.rules():
            ready = False
            reason = "リネーム規則を1件以上追加してください。"
        if self._uses_multiple_destinations():
            summary.append(f"一対一の出力先: {destination_count} 件")
        self._operation_presentation = None
        self.operation_summary_label.setText(" ｜ ".join(summary))
        self.readiness_label.setText(
            "準備完了：確認画面で最終プレビューを作成します。"
            if ready
            else f"確認画面を開けません：{reason}"
        )
        self.copy_button.setEnabled(ready)
        if operation == "rename":
            self._refresh_rename_inline_preview(targets)

    def _refresh_rename_inline_preview(self, targets: tuple[Path, ...]) -> None:
        """Keep the unused rename-panel space as a live filename-only preview."""
        rules = self.rename_panel.rules()
        if not targets:
            self.rename_panel.set_preview((), "操作対象を1件以上指定してください。")
            return
        if not rules:
            self.rename_panel.set_preview((), "リネーム規則を追加すると、ここに変更予定を表示します。")
            return
        preview = build_rename_preview(
            "\n".join(str(path) for path in targets),
            rules,
            include_extension=self.rename_panel.include_extension(),
        )
        if preview.plan is None:
            detail = preview.text.rsplit("\n", 1)[-1]
            self.rename_panel.set_preview((), f"プレビューできません: {detail}")
            return
        self.rename_panel.set_preview(
            tuple((rename.source.name, rename.output.name) for rename in preview.plan.renames)
        )

    def show_operation_confirmation(self) -> None:
        """Freeze main-screen input and open destination/preview/final execution."""
        self.update_operation_preview()
        if not self.copy_button.isEnabled():
            return
        targets = self._operation_targets()
        if self.operation_combo.currentData() == "extract":
            self._open_quick_extract_dialog(targets)
            return
        if self.operation_combo.currentData() == "zip" and self.copy_mode_combo.currentData() == "archive":
            self._open_quick_compress_dialog(targets)
            return
        dialog = OperationConfirmationDialog(
            kind=self.operation_combo.currentData(),
            targets=targets,
            mode=self.copy_mode_combo.currentData(),
            destinations=tuple(self.destination_input.paths()),
            rename_rules=tuple(self.rename_panel.rules()),
            include_extension=self.rename_panel.include_extension(),
            initial_destination=self._session_last_destination,
            registered_paths=[
                entry["path"]
                for entry in self._persistent_settings["favorite_paths"]
                if entry["context_menu"]
            ],
            parent=None,
        )
        self._operation_confirmation_dialogs.add(dialog)
        _retain_independent_operation_window(dialog)
        dialog.destination_remembered.connect(self._remember_session_destination)
        dialog.operation_succeeded.connect(self._operation_confirmation_succeeded)
        dialog.operation_failed.connect(self._operation_confirmation_failed)
        dialog.destroyed.connect(
            lambda _object=None, current=dialog: self._operation_confirmation_dialogs.discard(current)
        )
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _remember_session_destination(self, destination: str) -> None:
        """Remember a validated destination only until this screen is closed."""
        self._session_last_destination = destination

    def _operation_confirmation_failed(self, message: str) -> None:
        self._notify("実行していません。確認画面の内容を見直してください。")

    def _operation_confirmation_succeeded(
        self, presentation: OperationPresentation, results: list[Path]
    ) -> None:
        if presentation.kind == "rename":
            # Use the frozen plan, even if selection/checks changed while its
            # independent confirmation window was open.
            replacements = {item.source: item.output for item in presentation.plan.renames}
            tree = self.search_results_input._tree
            for index in range(tree.topLevelItemCount()):
                row = tree.topLevelItem(index)
                if row.text(1):
                    output = replacements.get(normalize_path(row.text(1)))
                    if output is not None:
                        row.setText(1, str(output))
                        self.search_results_input._set_item_state(row, inspect_path(output))
            self.search_results_input._normalize_input_row()
            self.search_results_input.textChanged.emit()
        self._notify(f"{presentation.completed_label}: {len(results)} 件")
        self.update_operation_preview()


def create_screen(on_back: Callable[[], None]) -> FileManagerScreen:
    """Create the launcher-compatible file-manager screen."""
    return FileManagerScreen(on_back)


def _favorite_display_name(value: str) -> str:
    """Keep the narrow favorites pane readable while retaining the full tooltip."""
    path = Path(value)
    return path.name or str(path)
