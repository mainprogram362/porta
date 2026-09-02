"""Integrated, in-memory workspace for safe file operations."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QApplication,
    QGridLayout,
    QDialog,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QStackedWidget,
    QTextEdit,
    QPushButton,
    QSizePolicy,
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
    create_settings_file,
    editable_text,
    load_settings,
    save_text,
    settings_status,
    template_text,
)
from gui.persistent_settings import create_app_settings_file, show_settings_location_editor
from apps.file_tools.file_manager.rename_panel import RenamePanel
from apps.file_tools.file_manager.rename_workflow import build_rename_preview
from apps.file_tools.file_manager.search_workflow import select_paths_by_conditions
from apps.file_tools.file_manager.quick_copy_dialog import QuickCopyDialog
from apps.file_tools.file_manager.quick_compress_dialog import QuickCompressDialog
from apps.file_tools.file_manager.quick_extract_dialog import QuickExtractDialog
from foundation.transient_paths import offer_media_paths, offer_video_encode_paths
from foundation.path import normalize_path
from foundation.path_inspection import inspect_path
from gui import (
    AppHeader,
    AppPageLayout,
    add_path_list_input,
    NoWheelComboBox,
)


class FileManagerScreen(QWidget):
    """File-manager workspace with a path workbench and copy operations."""

    _WIDE_COLUMN_MINIMUM = 240
    _FAVORITES_COLUMN_MINIMUM = 120
    _FAVORITES_COLUMN_MAXIMUM = 168

    def __init__(
        self,
        on_back: Callable[[], None],
        *,
        on_open_media_tool: Callable[[str], None] | None = None,
    ) -> None:
        super().__init__()
        self._on_back = on_back
        self._on_open_media_tool = on_open_media_tool
        self._operation_presentation: OperationPresentation | None = None
        self._session_last_destination = ""
        self._operation_confirmation_dialogs: set[OperationConfirmationDialog] = set()
        self._history_restoring = False
        self._history_suspended = False
        self._undo_states: list[dict[str, object]] = []
        self._redo_states: list[dict[str, object]] = []
        self._execution_results: list[str] = []
        self._results_dialog: QDialog | None = None
        self._results_text: QTextEdit | None = None
        self._settings_dialog: QDialog | None = None
        self._settings_editor: QTextEdit | None = None
        self._quick_copy_dialogs: set[QuickCopyDialog] = set()
        self._quick_compress_dialogs: set[QuickCompressDialog] = set()
        self._quick_extract_dialogs: set[QuickExtractDialog] = set()
        self._persistent_settings = load_settings()
        # Below this width, two independently useful path tables cannot fit.
        self.setMinimumSize(1_020, 720)
        self._build_ui()

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
        QTimer.singleShot(0, open_operation)

    def _build_ui(self) -> None:
        outer_layout = AppPageLayout(self)
        header = AppHeader(
            self._on_back,
            title="ファイルマネージャー",
            on_settings=self.show_persistent_settings,
            settings_tooltip="パスごとの初期表示・お気に入り・右クリック候補を編集します。出力先は保存しません。",
        )
        header.content_layout.addStretch(1)
        outer_layout.addWidget(header)
        # The main workspace prepares targets and rules.  Final destination,
        # preview and execution live in a separate frozen confirmation window.
        workspace = QWidget()
        self._workspace_layout = QGridLayout(workspace)
        self._workspace_layout.setSpacing(8)
        outer_layout.addWidget(workspace)

        self.favorites_box, favorites_layout = self._make_group("お気に入り")
        self.favorites_box.setMaximumWidth(self._FAVORITES_COLUMN_MAXIMUM)
        favorites_hint = QLabel("ダブルクリックで作業一覧へ追加")
        favorites_hint.setWordWrap(True)
        favorites_layout.addWidget(favorites_hint)
        self.favorites_list = QListWidget()
        self.favorites_list.setToolTip("設定で「favorite」が true のパスです。表示は末尾名だけです。カーソルを合わせるとフルパスを表示します。")
        self.favorites_list.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.favorites_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.favorites_list.itemDoubleClicked.connect(self._add_favorite_to_work_list)
        favorites_layout.addWidget(self.favorites_list, 1)
        self._refresh_favorites()

        self.search_results_box, search_layout = self._make_group("作業一覧")
        work_list_actions = QHBoxLayout()
        work_list_actions.setContentsMargins(0, 0, 0, 0)
        clear_work_list_button = QPushButton("一覧を空にする")
        clear_work_list_button.setToolTip("作業一覧の行だけを空にします。実ファイル・フォルダは削除しません。")
        clear_work_list_button.clicked.connect(lambda: self.search_results_input.clear_items())
        work_list_actions.addWidget(clear_work_list_button)
        select_all_work_list_button = QPushButton("全件チェック")
        select_all_work_list_button.setToolTip("作業一覧のチェックをすべて入れます。")
        select_all_work_list_button.clicked.connect(lambda: self.search_results_input.select_all_items())
        work_list_actions.addWidget(select_all_work_list_button)
        clear_selection_work_list_button = QPushButton("全チェック解除")
        clear_selection_work_list_button.setToolTip("作業一覧のチェックをすべて外します。")
        clear_selection_work_list_button.clicked.connect(
            lambda: self.search_results_input.clear_item_selection()
        )
        work_list_actions.addWidget(clear_selection_work_list_button)
        self.selection_filter_toggle = QPushButton("条件で選別 ▸")
        self.selection_filter_toggle.setCheckable(True)
        self.selection_filter_toggle.setToolTip("現在のパス一覧を条件でチェック選別する欄を開閉します。")
        self.selection_filter_toggle.toggled.connect(self._toggle_selection_filter_panel)
        work_list_actions.addWidget(self.selection_filter_toggle)
        search_layout.addLayout(work_list_actions)

        work_list_body = QHBoxLayout()
        work_list_body.setContentsMargins(0, 0, 0, 0)
        search_layout.addLayout(work_list_body, 1)
        self.search_results_input = add_path_list_input(
            work_list_body,
            rows=10,
            placeholder="手入力・外部ドロップ・右クリック展開で候補を集めます。実行する項目にチェックを入れます。",
            show_operation_targets=False,
            show_controls=False,
            context_menu_selection_actions=False,
            context_menu_operation_target_actions=False,
            context_menu_copy_actions=False,
            double_click_directory_selection=True,
            double_click_directory_replaces_all=True,
            enable_row_selection=True,
        )
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
        self.destination_input.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self.destination_list_actions = QWidget()
        destination_actions_layout = QHBoxLayout(self.destination_list_actions)
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
        operation_controls = QHBoxLayout()
        operation_controls.addWidget(QLabel("操作"))
        self.operation_combo = NoWheelComboBox()
        self.operation_combo.addItem("コピー", "copy")
        self.operation_combo.addItem("移動", "move")
        self.operation_combo.addItem("ZIP", "zip")
        self.operation_combo.addItem("ゴミ箱へ送る", "trash")
        self.operation_combo.addItem("リネーム", "rename")
        self.operation_combo.setMinimumWidth(108)
        operation_controls.addWidget(self.operation_combo)
        self.copy_mode_controls = QWidget()
        copy_mode_controls_layout = QHBoxLayout(self.copy_mode_controls)
        copy_mode_controls_layout.setContentsMargins(0, 0, 0, 0)
        self.output_mode_label = QLabel("コピー方式")
        copy_mode_controls_layout.addWidget(self.output_mode_label)
        self.copy_mode_combo = NoWheelComboBox()
        self.copy_mode_combo.setMinimumWidth(142)
        copy_mode_controls_layout.addWidget(self.copy_mode_combo, 1)
        operation_controls.addWidget(self.copy_mode_controls, 1)
        settings_layout.addLayout(operation_controls)
        self.operation_stack = QStackedWidget()
        self.operation_stack.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed
        )
        settings_layout.addWidget(self.operation_stack)

        self.operation_stack.addWidget(QWidget())

        self.rename_panel = RenamePanel()
        self.operation_stack.addWidget(self.rename_panel)
        self.rename_operation_box, self._rename_operation_layout = self._make_group("リネーム設定")
        self.rename_operation_box.setFlat(False)
        self.rename_operation_box.setStyleSheet(
            "QGroupBox { border: 1px solid #888; border-radius: 4px; margin-top: 8px; }"
            "QGroupBox::title { subcontrol-origin: margin; left: 7px; padding: 0 3px; }"
        )
        self.rename_operation_box.hide()

        # The fixed two-line notification spans all active columns beneath
        # the work area, without competing with the preview for height.
        self.state_bar = QFrame()
        self.state_bar.setObjectName("file_manager_notification_bar")
        self.state_bar.setFrameShape(QFrame.Shape.StyledPanel)
        self.state_bar.setStyleSheet(
            "QFrame#file_manager_notification_bar {"
            " border: 1px solid rgba(127, 127, 127, 180);"
            " border-radius: 4px;"
            "}"
        )
        self.state_bar.setFixedHeight(46)
        state_layout = QVBoxLayout(self.state_bar)
        state_layout.setContentsMargins(5, 1, 5, 1)
        state_layout.setSpacing(0)
        self.operation_summary_label = QLabel()
        self.operation_summary_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        state_layout.addWidget(self.operation_summary_label)
        self.readiness_label = QLabel()
        self.readiness_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        notification_layout = QHBoxLayout()
        notification_layout.setContentsMargins(0, 0, 0, 0)
        notification_layout.setSpacing(8)
        notification_layout.addWidget(self.readiness_label)
        self.status_label = QLabel()
        self.status_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        notification_layout.addWidget(self.status_label, 1)
        state_layout.addLayout(notification_layout)
        self._workspace_layout.addWidget(self.state_bar, 2, 0, 1, 2)

        self.execution_bar = QWidget()
        self._execution_outer_layout = QVBoxLayout(self.execution_bar)
        self._execution_outer_layout.setContentsMargins(0, 0, 0, 0)
        self._execution_outer_layout.setSpacing(2)
        self._execution_outer_layout.addWidget(self.operation_box)
        self.execution_controls = QWidget()
        execution_layout = QHBoxLayout(self.execution_controls)
        execution_layout.setContentsMargins(0, 0, 0, 0)
        refresh_button = QPushButton("状態更新")
        refresh_button.setToolTip("表示中のパスについて、種別と存在状態を更新します。")
        refresh_button.clicked.connect(self.refresh_path_states)
        execution_layout.addWidget(refresh_button)
        self.undo_button = QPushButton("戻す")
        self.undo_button.clicked.connect(self.undo_ui_change)
        execution_layout.addWidget(self.undo_button)
        self.redo_button = QPushButton("やり直す")
        self.redo_button.clicked.connect(self.redo_ui_change)
        execution_layout.addWidget(self.redo_button)
        self.results_button = QPushButton("結果を見る")
        self.results_button.setEnabled(False)
        self.results_button.clicked.connect(self.show_execution_results)
        execution_layout.addWidget(self.results_button)
        self.copy_button = QPushButton("実行内容を確認…")
        self.copy_button.clicked.connect(self.show_operation_confirmation)
        execution_layout.addWidget(self.copy_button)
        self._execution_outer_layout.addWidget(self.execution_controls)

        self.search_results_input.textChanged.connect(self._work_list_changed)
        self.search_results_input.operationTargetsChanged.connect(self.update_operation_preview)
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
        for entry in self._persistent_settings["favorite_paths"]:
            if not entry["favorite"]:
                continue
            path = entry["path"]
            item = QListWidgetItem(_favorite_display_name(path))
            item.setData(Qt.ItemDataRole.UserRole, path)
            item.setToolTip(path)
            self.favorites_list.addItem(item)

    def _add_favorite_to_work_list(self, item: QListWidgetItem) -> None:
        """Append one favorite without changing any filesystem item."""
        path = str(item.data(Qt.ItemDataRole.UserRole) or "").strip()
        if not path:
            return
        self.search_results_input.append_items((path,))
        self._notify(f"お気に入りを作業一覧へ追加しました: {path}")

    def _build_selection_filter_panel(self) -> QGroupBox:
        """Build the collapsible, transient checkbox-selection conditions."""
        panel, layout = self._make_group("チェック選別条件")
        panel.setMinimumWidth(220)
        panel.setMaximumWidth(260)

        layout.addWidget(QLabel("検索語（, で複数・-語で除外）"))
        self.selection_filter_query = QLineEdit()
        self.selection_filter_query.setPlaceholderText("例: .mp4, -sample")
        self.selection_filter_query.returnPressed.connect(self.apply_selection_filter)
        layout.addWidget(self.selection_filter_query)

        layout.addWidget(QLabel("照合方法"))
        self.selection_filter_mode = NoWheelComboBox()
        self.selection_filter_mode.addItem("部分一致", "contains")
        self.selection_filter_mode.addItem("正規表現", "regex")
        layout.addWidget(self.selection_filter_mode)

        layout.addWidget(QLabel("照合する場所"))
        self.selection_filter_field = NoWheelComboBox()
        self.selection_filter_field.addItem("ファイル名／フォルダ名", "name")
        self.selection_filter_field.addItem("フルパス", "path")
        layout.addWidget(self.selection_filter_field)

        layout.addWidget(QLabel("種別"))
        self.selection_filter_kind = NoWheelComboBox()
        self.selection_filter_kind.addItem("すべて", "all")
        self.selection_filter_kind.addItem("ファイル", "file")
        self.selection_filter_kind.addItem("フォルダ", "directory")
        layout.addWidget(self.selection_filter_kind)

        layout.addWidget(QLabel("チェックの更新"))
        self.selection_filter_action = NoWheelComboBox()
        self.selection_filter_action.addItem("一致するものだけチェック", "replace")
        self.selection_filter_action.addItem("一致するものを追加チェック", "add")
        self.selection_filter_action.addItem("一致するもののチェックを外す", "remove")
        layout.addWidget(self.selection_filter_action)

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
            Path(item.text(1).strip())
            if item is not None and item.text(1).strip()
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

    def _copy_checked_paths(self, path_list) -> None:  # type: ignore[no-untyped-def]
        """Copy every checked filesystem path from one list as newline-separated text."""
        self._copy_path_values(path_list, path_list.selected_paths(), "チェック済み")

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
        value = item.text(1).strip()
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
        return {
            "results": self.search_results_input.snapshot(),
            "destinations": self.destination_input.snapshot(),
            "operation": self.operation_combo.currentIndex(),
            "copy_mode": self.copy_mode_combo.currentIndex(),
            "rename_rules": self.rename_panel.rules(),
            "rename_extension": self.rename_panel.include_extension(),
        }

    def _record_ui_state(self) -> None:
        if self._history_restoring or self._history_suspended:
            return
        state = self._capture_ui_state()
        if self._undo_states and state == self._undo_states[-1]:
            return
        self._undo_states.append(state)
        self._redo_states.clear()
        self._update_history_buttons()

    def _restore_ui_state(self, state: dict[str, object]) -> None:
        self._history_restoring = True
        try:
            self.search_results_input.restore_snapshot(state["results"])  # type: ignore[arg-type]
            self.operation_combo.setCurrentIndex(state["operation"])  # type: ignore[arg-type]
            self.copy_mode_combo.setCurrentIndex(state["copy_mode"])  # type: ignore[arg-type]
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
        self.undo_button.setEnabled(len(self._undo_states) > 1)
        self.redo_button.setEnabled(bool(self._redo_states))

    def _record_execution_result(self, title: str, text: str) -> None:
        """Keep execution results only for this open screen instance."""
        self._execution_results.append(f"=== {title} ===\n{text}")
        self.results_button.setEnabled(True)
        if self._results_text is not None:
            self._results_text.setPlainText("\n\n".join(self._execution_results))

    def show_execution_results(self) -> None:
        """Show all in-memory execution results without writing a log file."""
        if self._results_dialog is None:
            dialog = QDialog(self)
            dialog.setWindowTitle("実行結果")
            dialog.resize(720, 500)
            layout = QVBoxLayout(dialog)
            text = QTextEdit()
            text.setReadOnly(True)
            layout.addWidget(text)
            clear_button = QPushButton("結果を空にする")
            clear_button.clicked.connect(self.clear_execution_results)
            layout.addWidget(clear_button)
            self._results_dialog = dialog
            self._results_text = text
        self._results_text.setPlainText("\n\n".join(self._execution_results))
        self._results_dialog.show()
        self._results_dialog.raise_()
        self._results_dialog.activateWindow()

    def clear_execution_results(self) -> None:
        self._execution_results.clear()
        self.results_button.setEnabled(False)
        if self._results_text is not None:
            self._results_text.clear()

    def show_persistent_settings(self) -> None:
        """Show every persistable value as editable text; nothing is saved automatically."""
        if self._settings_dialog is None:
            dialog = QDialog(self)
            dialog.setWindowTitle("永続設定を編集")
            dialog.resize(680, 460)
            layout = QVBoxLayout(dialog)
            self._settings_status_label = QLabel()
            self._settings_status_label.setWordWrap(True)
            layout.addWidget(self._settings_status_label)
            layout.addWidget(QLabel("保存対象は、各パスの初期作業一覧・お気に入り・右クリック候補です。出力先は保存できません。"))
            layout.addWidget(QLabel("書き方: path は \"@HOME\"、\"@HOME/Downloads\"、\"~\"、または / から始めます。@HOME はこのPCの /home/ユーザー名 を表します。"))
            layout.addWidget(QLabel("initial_work_list: 起動時の作業一覧／favorite: 左側のお気に入り／context_menu: 右クリック候補。各値は true または false です。"))
            editor = QTextEdit()
            editor.setToolTip("JSON形式。保存を押すまでファイルは変更されません。")
            layout.addWidget(editor)
            actions = QHBoxLayout()
            current_button = QPushButton("現在の作業一覧を反映")
            current_button.clicked.connect(self._put_current_paths_into_settings_editor)
            actions.addWidget(current_button)
            location_button = QPushButton("保存先入口")
            location_button.clicked.connect(lambda: show_settings_location_editor(dialog))
            actions.addWidget(location_button)
            create_button = QPushButton("保存先・設定を作成")
            create_button.clicked.connect(lambda: create_app_settings_file(dialog, create_settings_file))
            actions.addWidget(create_button)
            template_button = QPushButton("雛形に戻す")
            template_button.clicked.connect(lambda: editor.setPlainText(template_text()))
            actions.addWidget(template_button)
            reload_button = QPushButton("保存済みを再読み込み")
            reload_button.clicked.connect(lambda: editor.setPlainText(editable_text()))
            actions.addWidget(reload_button)
            actions.addStretch()
            save_button = QPushButton("保存")
            save_button.clicked.connect(self.save_persistent_settings)
            actions.addWidget(save_button)
            close_button = QPushButton("閉じる（保存しない）")
            close_button.clicked.connect(dialog.close)
            actions.addWidget(close_button)
            layout.addLayout(actions)
            self._settings_dialog = dialog
            self._settings_editor = editor
        self._settings_editor.setPlainText(editable_text())
        state, detail = settings_status()
        self._settings_status_label.setText(f"設定状態: {state} — {detail}")
        self._settings_dialog.show()
        self._settings_dialog.raise_()
        self._settings_dialog.activateWindow()

    def _put_current_paths_into_settings_editor(self) -> None:
        """Copy current lists into the editor only; saving remains an explicit next step."""
        if self._settings_editor is None:
            return
        import json

        from foundation.path_tokens import home_tokenized

        existing = {
            entry["path"]: entry for entry in self._persistent_settings["favorite_paths"]
        }
        entries = []
        for value in self.search_results_input.items():
            path = str(Path(value).expanduser().resolve(strict=False))
            previous = existing.get(path)
            entries.append(
                {
                    "path": home_tokenized(path),
                    "initial_work_list": True,
                    "favorite": previous["favorite"] if previous is not None else False,
                    "context_menu": previous["context_menu"] if previous is not None else False,
                }
            )
        self._settings_editor.setPlainText(
            json.dumps(
                {"favorite_paths": entries},
                ensure_ascii=False,
                indent=2,
            )
            + "\n"
        )

    def save_persistent_settings(self) -> None:
        if self._settings_editor is None:
            return
        try:
            self._persistent_settings = save_text(self._settings_editor.toPlainText())
        except ValueError as exc:
            QMessageBox.warning(self, "保存できません", str(exc))
            return
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
        self.copy_mode_controls.setVisible(operation not in {"rename", "trash"})
        self.operation_combo.setMaximumWidth(128 if self.copy_mode_controls.isVisible() else 16_777_215)
        self.operation_box.setVisible(True)
        self.operation_stack.setVisible(operation == "rename")
        self.operation_stack.setSizePolicy(
            QSizePolicy.Policy.Preferred,
            QSizePolicy.Policy.Expanding
            if operation == "rename"
            else QSizePolicy.Policy.Fixed,
        )
        if operation not in {"rename", "trash"}:
            self.copy_mode_combo.blockSignals(True)
            self.copy_mode_combo.clear()
            if operation == "zip":
                self.output_mode_label.setText("ZIP方式")
                self.copy_mode_combo.addItem("その場でZIP", "in_place")
                self.copy_mode_combo.addItem("指定先へZIP", "simple")
                self.copy_mode_combo.addItem("一対一ZIP", "one_to_one")
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
        self.destination_input.set_visible_rows(10)
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
        """Keep rename configuration beside its list; other controls stay below."""
        self._workspace_layout.removeWidget(self.operation_box)
        self._execution_outer_layout.removeWidget(self.operation_box)
        self._rename_operation_layout.removeWidget(self.operation_box)
        if self.operation_combo.currentData() == "rename":
            self.operation_box.setParent(self.rename_operation_box)
            self._rename_operation_layout.addWidget(self.operation_box)
            self.operation_box.show()
            self.rename_operation_box.show()
            return
        self.rename_operation_box.hide()
        self.operation_box.setParent(self.execution_bar)
        self._execution_outer_layout.insertWidget(0, self.operation_box)
        self.operation_box.show()

    def _update_workspace_layout(self) -> None:
        """Keep the main screen focused on targets and operation settings."""
        widgets = (
            self.favorites_box,
            self.search_results_box,
            self.output_operation_box,
            self.state_bar,
            self.execution_bar,
            self.rename_operation_box,
        )
        for widget in widgets:
            self._workspace_layout.removeWidget(widget)

        self._place_operation_box()
        if self._uses_three_column_layout():
            centre_widget = (
                self.rename_operation_box
                if self.operation_combo.currentData() == "rename"
                else self.output_operation_box
            )
            centre_widget.setMinimumHeight(0)
            centre_widget.setMaximumHeight(16_777_215)
            self._workspace_layout.addWidget(self.favorites_box, 0, 0)
            self._workspace_layout.addWidget(self.search_results_box, 0, 1)
            self._workspace_layout.addWidget(centre_widget, 0, 2)
            self._workspace_layout.addWidget(self.execution_bar, 1, 0, 1, 3)
            self._workspace_layout.addWidget(self.state_bar, 2, 0, 1, 3)
            self._workspace_layout.setColumnStretch(0, 0)
            self._workspace_layout.setColumnStretch(1, 1)
            self._workspace_layout.setColumnStretch(2, 1)
            self._workspace_layout.setColumnMinimumWidth(0, self._FAVORITES_COLUMN_MINIMUM)
            for column in (1, 2):
                self._workspace_layout.setColumnMinimumWidth(
                    column, self._WIDE_COLUMN_MINIMUM
                )
        else:
            self._workspace_layout.addWidget(self.favorites_box, 0, 0)
            self._workspace_layout.addWidget(self.search_results_box, 0, 1, 1, 2)
            self._workspace_layout.addWidget(self.execution_bar, 1, 0, 1, 3)
            self._workspace_layout.addWidget(self.state_bar, 2, 0, 1, 3)
            self._workspace_layout.setColumnStretch(0, 0)
            self._workspace_layout.setColumnStretch(1, 1)
            self._workspace_layout.setColumnStretch(2, 1)
            self._workspace_layout.setColumnMinimumWidth(0, self._FAVORITES_COLUMN_MINIMUM)
            self._workspace_layout.setColumnMinimumWidth(1, self._WIDE_COLUMN_MINIMUM)
            self._workspace_layout.setColumnMinimumWidth(2, self._WIDE_COLUMN_MINIMUM)

        self._workspace_layout.setRowStretch(0, 1)
        self._workspace_layout.setRowStretch(1, 0)
        self._workspace_layout.setRowStretch(2, 0)
        self._workspace_layout.setRowStretch(3, 0)
        self._workspace_layout.invalidate()
        self._workspace_layout.activate()
        self.updateGeometry()

    def update_operation_preview(self) -> None:
        """Show preparation readiness; final preview belongs to another window."""
        operation = self.operation_combo.currentData()
        mode = self.copy_mode_combo.currentData()
        targets = tuple(self.search_results_input.selected_paths(deduplicate=True))
        target_count = len(targets)
        destination_count = len(self.destination_input.items())
        operation_label = {
            "copy": "コピー",
            "move": "移動",
            "zip": "ZIP",
            "trash": "ゴミ箱へ送る",
            "rename": "リネーム",
        }[operation]
        summary = [f"操作: {operation_label}", f"チェック済み: {target_count} 件"]
        ready = target_count > 0
        reason = ""
        if not ready:
            reason = "実行する項目を1件以上チェックしてください。"
        elif mode == "one_to_one" and destination_count != target_count:
            ready = False
            reason = f"一対一の出力先を対象と同じ{target_count}件にしてください。"
        elif operation == "rename" and not self.rename_panel.rules():
            ready = False
            reason = "リネーム規則を1件以上追加してください。"
        if mode == "one_to_one":
            summary.append(f"一対一の出力先: {destination_count} 件")
        self._operation_presentation = None
        self.operation_summary_label.setText(" ｜ ".join(summary))
        self.readiness_label.setText(
            "準備完了：確認画面で最終プレビューを作成します。"
            if ready
            else f"確認画面を開けません：{reason}"
        )
        self.copy_button.setText("実行内容を確認…")
        self.copy_button.setEnabled(ready)
        if operation == "rename":
            self._refresh_rename_inline_preview(targets)

    def _refresh_rename_inline_preview(self, targets: tuple[Path, ...]) -> None:
        """Keep the unused rename-panel space as a live filename-only preview."""
        rules = self.rename_panel.rules()
        if not targets:
            self.rename_panel.set_preview((), "チェック済み項目を1件以上選んでください。")
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
        dialog = OperationConfirmationDialog(
            kind=self.operation_combo.currentData(),
            targets=tuple(self.search_results_input.selected_paths(deduplicate=True)),
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
            parent=self,
        )
        self._operation_confirmation_dialogs.add(dialog)
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
        self._record_execution_result("実行できません", message)

    def _operation_confirmation_succeeded(
        self, presentation: OperationPresentation, results: list[Path]
    ) -> None:
        if presentation.kind == "rename":
            self.search_results_input.replace_checked_item_values(str(path) for path in results)
        result_paths = "\n".join(str(path) for path in results)
        self._notify(f"{presentation.completed_label}: {len(results)} 件")
        self._record_execution_result(
            presentation.completed_label,
            f"{len(results)} 件を処理しました。\n\n{presentation.result_label}:\n{result_paths}",
        )
        self.update_operation_preview()


def create_screen(on_back: Callable[[], None]) -> FileManagerScreen:
    """Create the launcher-compatible file-manager screen."""
    return FileManagerScreen(on_back)


def _favorite_display_name(value: str) -> str:
    """Keep the narrow favorites pane readable while retaining the full tooltip."""
    path = Path(value)
    return path.name or str(path)
