"""Unified media-information workspace with explicit browse and edit modes."""

from __future__ import annotations

from collections.abc import Callable, Iterable
import json
from pathlib import Path
from typing import Literal

from PySide6.QtCore import QEvent, QProcess, QProcessEnvironment, QRegularExpression, QTimer, Qt, QUrl
from PySide6.QtGui import (
    QDesktopServices,
    QDoubleValidator,
    QKeySequence,
    QRegularExpressionValidator,
    QShortcut,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QAbstractScrollArea,
    QDialog,
    QDialogButtonBox,
    QComboBox,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTreeWidget,
    QTextEdit,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from gui import (
    AppHeader,
    AppPageLayout,
    NoWheelComboBox,
    PathLineInput,
    PathListInput,
    TextValueWorkspaceDialog,
    TextWorkspaceRow,
    TextWorkspaceSource,
    TextWorkspaceTarget,
)
from foundation.path import normalize_path
from foundation.product import PRODUCT_NAME
from foundation.transient_paths import take_media_paths
from media.file_attributes import (
    STANDARD_CATALOG_FIELDS,
    COLLECTION_STATUS_PRESETS,
    FREQUENT_CATALOG_FIELD_KEYS,
    MediaItem,
    apply_catalog_field_operation,
    append_media_highlight,
    catalog_field_for_key,
    catalog_attributes_from_media_item,
    catalog_record_from_media_item,
    create_manual_placeholder_item,
    display_catalog_attribute,
    folder_field_uneditable_reason,
    media_item_display_name,
    media_item_token,
    preset_values_for_catalog_field,
    replace_media_highlights,
)
from media.catalog import unique_json_path
from media.mpv_player import (
    MpvEvent,
    IsolatedMpvSession,
    create_isolated_mpv_session,
    effective_shortcut_bindings,
)
from media.review_patch import (
    MediaReviewPatch,
    ReviewPatchMergePlan,
    apply_review_patch_merge,
    load_review_patch,
    plan_review_patch_merge,
    review_patch_entry,
    review_patch_merge_summary,
    resolve_new_review_patch_path,
    replace_review_patch,
    save_review_patch,
)
from media.single_field_patch import (
    PATCHABLE_TEXT_FIELD_KEYS,
    MediaSingleFieldPatch,
    SingleFieldPatchMergePlan,
    apply_single_field_patch_merge,
    load_single_field_patch,
    plan_single_field_patch_merge,
    resolve_new_single_field_patch_path,
    save_single_field_patch,
    single_field_patch_entry,
    single_field_patch_merge_summary,
)
from media.ledger import (
    MediaPart,
    MediaPartsDocument,
    create_parts,
    load_parts,
    resolve_new_parts_path,
    save_parts,
)

from . import settings
from .details_dialog import show_detached_item_details
from .path_actions import direct_children_for_selected_folders
from .text_workspace_adapter import text_workspace_rows
from gui.persistent_settings import create_app_settings_file, show_settings_location_editor
from apps.file_tools.file_manager import FileManagerScreen
from .browsing import (
    MAXIMUM_MATCH_CANDIDATES,
    FilterRule,
    WorkspaceRecord,
    collect_path_candidates,
    is_video_path,
    match_paths,
    record_matches_rules,
    record_search_text,
)
from .filter_dialog import DetailedFilterDialog
from .workflow import (
    AppliedOperation as _AppliedOperation,
    LoadedJsonSource as _LoadedJsonSource,
    UndoState as _UndoState,
    candidate_display_name as _candidate_display_name,
    candidate_source_detail as _candidate_source_detail,
    catalog_value as _catalog_value,
    mpv_time_text as _mpv_time_text,
    operation_description as _operation_description,
    operation_label as _operation_label,
    overwrite_safety_report,
    part_file_name_sets,
    read_candidate_sources,
)


_PRIMARY_MEDIA_COLUMNS: tuple[tuple[str, str], ...] = (
    ("サイズ", "file.size_bytes.observed"),
    ("拡張子", "file.extension.observed"),
    ("解像度", "video.resolution"),
    ("タグ", "classification.tag"),
    ("評価", "review.score"),
    ("見どころ", "media.highlights"),
)
_PRIMARY_MEDIA_KEYS = frozenset(key for _label, key in _PRIMARY_MEDIA_COLUMNS)
_MEDIA_ATTRIBUTE_COLUMNS: tuple[tuple[str, str], ...] = _PRIMARY_MEDIA_COLUMNS + tuple(
    (field.label, field.key)
    for field in STANDARD_CATALOG_FIELDS
    if field.key not in _PRIMARY_MEDIA_KEYS
    and field.key not in {"file.name.observed", "folder.descendant_file_count", "folder.media_file_count"}
)
_ATTRIBUTE_KEY_FOR_COLUMN = dict(_MEDIA_ATTRIBUTE_COLUMNS)
_RAW_ATTRIBUTES_COLUMN = "生データ"
_VIDEO_SUFFIXES = frozenset(
    {".3gp", ".avi", ".flv", ".m2ts", ".m4v", ".mkv", ".mov", ".mp4", ".mpeg", ".mpg", ".mts", ".ogv", ".ts", ".webm", ".wmv"}
)
_INDEPENDENT_FILE_MANAGERS: set[FileManagerScreen] = set()
AccessMode = Literal["browse", "edit"]


def _json_signature(value: object) -> str:
    """Compare scalar/list/structured attributes without relying on display text."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class MediaInformationScreen(QWidget):
    """Browse or stage JSON edits without ever modifying the media files."""

    def __init__(self, return_to_main: Callable[[], None]) -> None:
        super().__init__()
        self._return_to_main = return_to_main
        self._source_items: list[MediaItem] = []
        self._items: list[MediaItem] = []
        self._paths_locked = False
        self._access_mode: AccessMode | None = None
        self._filter_rules: tuple[FilterRule, ...] = ()
        self._browse_session_notes: dict[str, list[str]] = {}
        self._candidate_path_snapshot: tuple[tuple[str, bool], ...] | None = None
        self._candidate_source_details: dict[str, tuple[str, str]] = {}
        self._loaded_json_sources: tuple[_LoadedJsonSource, ...] = ()
        self._locked_input_summary = ""
        self._review_patch: MediaReviewPatch | None = None
        self._review_merge_plan: ReviewPatchMergePlan | None = None
        self._review_merge_items_snapshot: tuple[MediaItem, ...] | None = None
        self._review_patch_report_text = ""
        self._single_field_patch: MediaSingleFieldPatch | None = None
        self._single_field_merge_plan: SingleFieldPatchMergePlan | None = None
        self._single_field_merge_items_snapshot: tuple[MediaItem, ...] | None = None
        self._applied_operation_summaries: list[str] = []
        self._applied_operations: list[_AppliedOperation] = []
        self._undo_history: list[_UndoState] = []
        self._settings = settings.load_settings()
        self._work_mode = "catalog"
        self._parts_output_text = ""
        self._review_patch_output_text = ""
        self._settings_dialog: QDialog | None = None
        self._settings_editor: QTextEdit | None = None
        self._mpv_sessions: dict[QProcess, IsolatedMpvSession] = {}
        self._mpv_pending_ranges: dict[tuple[str, str], float] = {}
        self._mpv_linked_paths: dict[str, Path] = {}
        self._individual_row: QTreeWidgetItem | None = None
        # This intentionally has no parent.  A parented dialog would also
        # bring the entire workbench forward over mpv on many compositors.
        self._mpv_comment_dialog: QInputDialog | None = None
        self._mpv_tag_dialog: QDialog | None = None
        self._mpv_event_timer = QTimer(self)
        self._mpv_event_timer.setInterval(120)
        self._mpv_event_timer.timeout.connect(self._read_mpv_events)
        # This is a workbench, not a form: keep it compact at rest and let the
        # two data tables claim additional room only when the window grows.
        self.setMinimumSize(900, 700)
        self._build_ui()
        self._disable_mouse_wheel_interaction()
        received_paths = take_media_paths()
        if received_paths:
            self.path_input.append_items(received_paths)
            self._notify(
                f"ファイルマネージャーから{len(received_paths)}件を受け取りました。",
                "まだ対象は確定していません。「一覧全てを確定」を押すと読み込みを始めます。",
            )

    def _build_ui(self) -> None:
        layout = AppPageLayout(self)

        header = AppHeader(
            self._return_to_main,
            title="メディア情報ワークスペース",
            on_settings=self.show_settings,
            settings_tooltip=(
                "単一JSON保存先の自動入力、mpvキーとタグ候補を保存します。"
                "パス一覧・読取結果・履歴・ファイル情報は保存しません。"
            ),
        )
        self.source_box = QGroupBox("対象一覧")
        self.source_layout = QVBoxLayout(self.source_box)
        self.source_layout.setContentsMargins(8, 6, 8, 8)
        self.source_layout.setSpacing(4)
        setup_layout = header.content_layout
        setup_layout.addWidget(QLabel("作業モード"))
        self.export_target_label = QLabel("パーツ保存先")
        self.export_target_label.setStyleSheet("font-weight: bold;")
        setup_layout.addWidget(self.export_target_label)
        self.export_path_input = PathLineInput(
            drop_transform=self._output_path_from_drop,
        )
        self.export_path_input.setText("")
        self.export_path_input.setPlaceholderText("フォルダ、または .json ファイル名を入力")
        self.export_path_input.setToolTip(
            "フォルダなら、新しい media_parts.json を作ります（同名があれば番号を付けます）。"
            "ドロップ時はフォルダと .json をそのまま使い、それ以外のファイルは親フォルダにします。"
            "対象を確定するときに保存先の利用可否を確認します。確定後も変更でき、出力時に再確認します。"
            "新規保存は既存JSONを避けて別名で作成します。上書きは、読み込んだ単一JSONと一致するときだけ専用ボタンで行います。"
        )
        self.export_target_label.setToolTip("対象を確定した後も変更できます。パーツ保存時に保存先を再確認します。")
        self.export_path_input.dropRejected.connect(self._notify)
        self.export_path_input.set_context_menu_augmenter(self._add_output_registered_paths_menu)
        setup_layout.addWidget(self.export_path_input, 1)
        layout.addWidget(header)
        self.path_input = PathListInput(
            rows=5,
            accepted_path_kind="all",
            drop_replaces=False,
            supplemental_columns=(
                "出所",
                "ファイル数",
                "メディア数",
                *(label for label, _key in _MEDIA_ATTRIBUTE_COLUMNS),
                _RAW_ATTRIBUTES_COLUMN,
                "種別",
            ),
            show_controls=False,
            # Every information column uses a direct, left-to-right resize.
            # A stretch column before interactive columns makes header drags
            # feel as if the opposite side is being pulled.
            path_column_resizable=True,
            show_column_headers=True,
            double_click_directory_selection=True,
            enable_row_selection=True,
            selection_editable_when_locked=True,
        )
        self.path_input.setToolTip(
            "実ファイル・フォルダ・パーツJSONを追加できます。"
            "JSONは確定時に候補へ展開し、実在パスのようには扱いません。"
        )
        self.path_input.set_context_menu_augmenter(self._add_read_item_context_actions)
        self.path_input.set_exclusive_context_menu_builder(self._build_rating_context_menu)
        self.path_input.selectionChanged.connect(self._show_current_operation_state)
        self.path_input.selectionChanged.connect(self._update_browse_status)
        self.path_input.rowSelectionChanged.connect(self._update_browse_status)
        self.path_input.itemClicked.connect(self._select_individual_row)
        self.path_input.itemDoubleClicked.connect(self._candidate_double_clicked)
        self.path_input.set_supplemental_column_width("サイズ", 90)
        self.path_input.set_supplemental_column_width("出所", 135)
        self.path_input.set_supplemental_column_width("ファイル数", 70)
        self.path_input.set_supplemental_column_width("メディア数", 70)
        for label, _key in _MEDIA_ATTRIBUTE_COLUMNS:
            self.path_input.set_supplemental_column_width(label, 130)
        self.path_input.set_supplemental_column_width("サイズ", 90)
        self.path_input.set_supplemental_column_width("拡張子", 75)
        self.path_input.set_supplemental_column_width("解像度", 95)
        self.path_input.set_supplemental_column_width("評価", 70)
        self.path_input.set_supplemental_column_width("見どころ", 130)
        self.path_input.set_supplemental_column_width(_RAW_ATTRIBUTES_COLUMN, 240)
        self.path_input.set_supplemental_column_width("種別", 60)
        # 種別は表示する場合だけ、状態・除外の直前に固定する。
        self.path_input.set_supplemental_column_fixed("種別", 60)
        # The path, kind, state icon and removal control are structural.  The
        # optional fact columns alone are deliberately local settings.
        visible_columns = set(self._settings["default_visible_columns"])
        for column_label in (
            "種別", "出所", "ファイル数", "メディア数", *(_ATTRIBUTE_KEY_FOR_COLUMN.keys()), _RAW_ATTRIBUTES_COLUMN
        ):
            self.path_input.set_supplemental_column_visible(
                column_label, column_label in visible_columns
            )
        self.source_layout.addWidget(self.path_input, 1)
        source_actions_box = QWidget()
        source_actions_layout = QVBoxLayout(source_actions_box)
        source_actions_layout.setContentsMargins(0, 0, 0, 0)
        source_actions_layout.setSpacing(2)

        self.read_actions_box = QWidget()
        read_actions = QHBoxLayout(self.read_actions_box)
        read_actions.setContentsMargins(0, 0, 0, 0)
        self.confirm_browse_button = QPushButton("閲覧モード（読み取り専用）で確定")
        self.confirm_browse_button.setToolTip(
            "一覧の全パス・JSONを読み取り、検索・並べ替え・再生用としてロックします。"
            "元JSONと画面内データは編集しません。"
        )
        self.confirm_browse_button.clicked.connect(self.confirm_all_paths_read_only)
        read_actions.addWidget(self.confirm_browse_button)
        self.confirm_edit_button = QPushButton("編集モード（編集内容を保存可能）で確定")
        self.confirm_edit_button.setToolTip(
            "一覧の全パス・JSONを読み取り、作業用コピーを編集できる状態でロックします。"
            "元JSONへは明示的な保存確認まで書き込みません。"
        )
        self.confirm_edit_button.clicked.connect(self.confirm_all_paths)
        read_actions.addWidget(self.confirm_edit_button)
        # Compatibility alias for integrations that referred to the former
        # single confirmation button.  The default remains safe edit mode.
        self.confirm_all_button = self.confirm_edit_button
        self.clear_paths_button = QPushButton("空にする")
        self.clear_paths_button.setToolTip("一覧と、今回読み取った画面内データを空にします。実ファイルは削除しません。")
        self.clear_paths_button.clicked.connect(self.clear_read_paths)
        read_actions.addWidget(self.clear_paths_button)
        read_actions.addStretch(1)
        source_actions_layout.addWidget(self.read_actions_box)

        self.unlock_paths_button = QPushButton("ロック解除")
        self.unlock_paths_button.setToolTip("パス編集を再開し、今回の読取り・反映内容をすべて白紙に戻します。")
        self.unlock_paths_button.clicked.connect(self.unlock_confirmed_paths)

        self.locked_tools_box = QWidget()
        locked_tools = QHBoxLayout(self.locked_tools_box)
        locked_tools.setContentsMargins(0, 0, 0, 0)
        locked_tools.addWidget(self.unlock_paths_button)
        self.select_all_candidates_button = QPushButton("チェック全選択")
        self.select_all_candidates_button.setToolTip(
            "確定後の候補すべてにチェックを入れます。実ファイルや出力予定の内容は変更しません。"
        )
        self.select_all_candidates_button.clicked.connect(self.select_all_locked_candidates)
        locked_tools.addWidget(self.select_all_candidates_button)
        self.clear_candidate_selection_button = QPushButton("チェック全解除")
        self.clear_candidate_selection_button.setToolTip(
            "確定後の候補すべてのチェックを外します。候補自体は一覧から削除しません。"
        )
        self.clear_candidate_selection_button.clicked.connect(self.clear_locked_candidate_selection)
        locked_tools.addWidget(self.clear_candidate_selection_button)
        self.play_mpv_playlist_button = QPushButton("表示中の動画をmpvで再生")
        self.play_mpv_playlist_button.setToolTip(
            "検索・詳細条件で現在表示している動画を、表示順のmpvプレイリストとして最大1000件再生します。"
            "設定・履歴・再生位置は保存しません。"
        )
        self.play_mpv_playlist_button.clicked.connect(self.play_confirmed_items_with_mpv)
        self.link_mpv_videos_button = QPushButton("mpv連携動画を紐付け…")
        self.link_mpv_videos_button.setToolTip(
            "別途指定する実動画はすべてチェック済み候補のファイル名へ一対一で照合し、"
            "一致した候補だけを今回のmpv再生・評価パッチへ紐付けます。"
        )
        self.link_mpv_videos_button.clicked.connect(self.open_mpv_video_linker)
        self.mpv_shortcuts_button = QPushButton("mpvショートカット一覧")
        self.mpv_shortcuts_button.setToolTip(
            "今回の設定で有効になる、作業向けmpv標準キーと追加キーを確認します。"
        )
        self.mpv_shortcuts_button.clicked.connect(self.show_mpv_shortcuts)
        self.add_placeholder_button = QPushButton("仮登録を追加…")
        self.add_placeholder_button.setToolTip(
            "実在パスを持たない未収集項目を、今回の確定対象へ追加します。"
        )
        self.add_placeholder_button.clicked.connect(self.add_manual_placeholder)
        locked_tools.addWidget(self.add_placeholder_button)
        locked_tools.addStretch(1)
        locked_tools.addWidget(self.link_mpv_videos_button)
        locked_tools.addWidget(self.play_mpv_playlist_button)
        locked_tools.addWidget(self.mpv_shortcuts_button)
        source_actions_layout.addWidget(self.locked_tools_box)

        self.browse_tools_box = QWidget()
        browse_tools = QVBoxLayout(self.browse_tools_box)
        browse_tools.setContentsMargins(0, 0, 0, 0)
        browse_tools.setSpacing(2)
        browse_filter_row = QHBoxLayout()
        browse_filter_row.addWidget(QLabel("表示を整理"))
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("名前・投稿者・タグ・全属性を検索")
        self.search_input.textChanged.connect(self._refresh_browse_view)
        browse_filter_row.addWidget(self.search_input, 1)
        self.filter_button = QPushButton("詳細条件…")
        self.filter_button.clicked.connect(self._open_filter_dialog)
        browse_filter_row.addWidget(self.filter_button)
        browse_filter_row.addWidget(QLabel("並べ替え"))
        self.sort_combo = NoWheelComboBox()
        self.sort_combo.addItem("入力順", "input")
        self.sort_combo.addItem("名前順", "name")
        self.sort_combo.addItem("評価が高い順", "rating_desc")
        self.sort_combo.addItem("評価が低い順", "rating_asc")
        self.sort_combo.currentIndexChanged.connect(self._refresh_browse_view)
        browse_filter_row.addWidget(self.sort_combo)
        self.link_files_button = QPushButton("ファイルパスを紐付け…")
        self.link_files_button.setToolTip(
            "複数のファイル・フォルダを集め、ファイル名で最大1000件を今回だけ照合します。"
        )
        self.link_files_button.clicked.connect(self._open_file_path_link_dialog)
        browse_filter_row.addWidget(self.link_files_button)
        browse_tools.addLayout(browse_filter_row)

        browse_selection_row = QHBoxLayout()
        self.browse_status_label = QLabel("表示: 0件 / 青い選択: 0件 / チェック: 0件")
        self.browse_status_label.setToolTip(
            "Ctrl+A、Shift+↑/↓、Ctrl+クリックで青い選択を操作できます。"
        )
        browse_selection_row.addWidget(self.browse_status_label)
        check_blue = QPushButton("青い選択をチェック")
        check_blue.clicked.connect(lambda: self._set_blue_selection_checked(True))
        browse_selection_row.addWidget(check_blue)
        uncheck_blue = QPushButton("青い選択のチェックを外す")
        uncheck_blue.clicked.connect(lambda: self._set_blue_selection_checked(False))
        browse_selection_row.addWidget(uncheck_blue)
        browse_selection_row.addStretch(1)
        self.file_manager_button = QPushButton(
            "表示中・チェック済みのパスをファイルマネージャーへ"
        )
        self.file_manager_button.clicked.connect(self._send_checked_paths_to_file_manager)
        browse_selection_row.addWidget(self.file_manager_button)
        browse_tools.addLayout(browse_selection_row)
        source_actions_layout.addWidget(self.browse_tools_box)
        self.source_layout.addWidget(source_actions_box)
        self.source_box.setMinimumHeight(250)
        layout.addWidget(self.source_box, 1)

        self.operation_box = QGroupBox("編集")
        operation_layout = QVBoxLayout(self.operation_box)
        operation_layout.setSpacing(4)

        self.field_box = QWidget()
        self.field_box.setToolTip(
            "全体操作と個別操作で共通の対象項目です。一覧で1件をクリックすると、"
            "その項目の読み取り時と現在の出力予定を確認できます。"
        )
        field_layout = QVBoxLayout(self.field_box)
        field_layout.setContentsMargins(0, 0, 0, 0)
        field_layout.setSpacing(4)
        self.operation_field_scope_combo = QComboBox()
        self.operation_field_scope_combo.addItem("よく使う項目", "frequent")
        self.operation_field_scope_combo.addItem("すべての項目", "all")
        self.operation_field_scope_combo.currentIndexChanged.connect(self._refresh_operation_field_choices)
        self.operation_field_scope_combo.setToolTip("項目の候補を、よく使うものだけ／すべて、で切り替えます。")
        self.operation_field_combo = QComboBox()
        self.operation_field_combo.currentIndexChanged.connect(self._update_operation_controls)
        self.operation_field_combo.setToolTip("全体操作と個別編集で共通の、今回確認・編集する項目を選びます。")
        self.operation_custom_key_input = QLineEdit()
        self.operation_custom_key_input.setPlaceholderText("独自項目名")
        self.operation_custom_key_input.textChanged.connect(self._update_operation_controls)
        self.operation_custom_key_input.setToolTip("新しい独自項目名です。英小文字と . を使うと整理しやすくなります。")
        operation_field_row = QHBoxLayout()
        operation_field_row.setContentsMargins(0, 0, 0, 0)
        operation_field_row.addWidget(self.operation_field_scope_combo)
        operation_field_row.addWidget(QLabel("項目"))
        operation_field_row.addWidget(self.operation_field_combo, 1)
        operation_field_row.addWidget(self.operation_custom_key_input, 1)
        field_layout.addLayout(operation_field_row)
        self.selected_field_target_label = QLabel(
            "一覧から1件をクリックすると、選択中項目の値をここに表示します。"
        )
        self.selected_field_target_label.setWordWrap(True)
        self.selected_field_target_label.setStyleSheet("font-weight: bold;")
        self.selected_field_source_value = QLineEdit()
        self.selected_field_source_value.setReadOnly(True)
        self.selected_field_source_value.setPlaceholderText("候補を選ぶと表示します")
        self.selected_field_planned_value = QLineEdit()
        self.selected_field_planned_value.setReadOnly(True)
        self.selected_field_planned_value.setPlaceholderText("候補を選ぶと表示します")
        self.field_box.setVisible(False)
        operation_layout.addWidget(self.field_box)

        common_box = QWidget()
        common_box.setToolTip("対象を全体／選択中の1件から選び、同じ編集を反映します。")
        common_layout = QVBoxLayout(common_box)
        common_layout.setContentsMargins(0, 0, 0, 0)
        common_layout.setSpacing(4)
        controls_layout = QVBoxLayout()
        controls_layout.setContentsMargins(0, 0, 0, 0)
        self.operation_kind_combo = NoWheelComboBox()
        self.operation_kind_combo.currentIndexChanged.connect(self._update_operation_controls)
        self.operation_kind_combo.setToolTip(
            "上書きは既存値を置換、追加は既存値を残して追加、空欄化は値を空にし、"
            "項目削除はその項目自体を出力予定から外します。"
        )
        self.operation_value_input = QLineEdit()
        self.operation_value_input.setPlaceholderText("上書き・追加する値")
        self.operation_value_input.textChanged.connect(self._show_current_operation_state)
        self.operation_value_input.setToolTip("上書きまたは追加する値を入力します。項目ごとの形式はプレースホルダーで確認できます。")
        self.preset_value_combo = QComboBox()
        self.preset_value_combo.currentIndexChanged.connect(self._update_operation_controls)
        self.preset_value_combo.setToolTip("選択肢がある項目では、ここから値を選べます。例外だけ自由入力を使います。")
        operation_value_row = QHBoxLayout()
        operation_value_row.setContentsMargins(0, 0, 0, 0)
        operation_value_row.addWidget(QLabel("対象"))
        self.operation_target_combo = QComboBox()
        self.operation_target_combo.addItem("現在の対象1件のみ", "selected")
        self.operation_target_combo.addItem("チェック済み全て", "checked")
        self.operation_target_combo.addItem("一覧にあるもの全て", "all")
        self.operation_target_combo.setCurrentIndex(0)
        self.operation_target_combo.currentIndexChanged.connect(self._show_current_operation_state)
        self.operation_target_combo.setToolTip(
            "同じ項目・操作・値を、現在の対象1件、チェック済み全て、または一覧全体へ反映するか選びます。"
        )
        operation_value_row.addWidget(self.operation_target_combo)
        operation_value_row.addWidget(QLabel("操作"))
        operation_value_row.addWidget(self.operation_kind_combo)
        operation_value_row.addWidget(QLabel("値"))
        operation_value_row.addWidget(self.preset_value_combo)
        operation_value_row.addWidget(self.operation_value_input, 1)
        controls_layout.addLayout(operation_value_row)
        self.apply_button = QPushButton("対象へ反映")
        self.apply_button.setToolTip("選んだ対象の画面内データへ編集を反映します。実ファイル・JSONは変更しません。")
        self.apply_button.clicked.connect(self.apply_common_operation)
        self.operation_value_input.returnPressed.connect(self.apply_button.click)
        operation_value_row.addWidget(self.apply_button)
        self.undo_button = QPushButton("1つ戻す")
        self.undo_button.setToolTip(
            "直前の画面内編集を戻します。既に保存したJSONや実ファイルは変更しません（Ctrl+Z）。"
        )
        self.undo_button.clicked.connect(self.undo_last_edit)
        operation_value_row.addWidget(self.undo_button)
        self.planned_output_preview_button = QPushButton("出力予定を確認…")
        self.planned_output_preview_button.setToolTip(
            "チェック済みの候補について、今この画面からJSONへ出る予定の項目と値を確認します。保存はしません。"
        )
        self.planned_output_preview_button.clicked.connect(self.show_planned_output_preview)
        self.operation_preview_button = QPushButton("操作内容を確認")
        self.operation_preview_button.setToolTip("反映済み操作と、全基本項目の出力方針を一覧で確認します。")
        self.operation_preview_button.clicked.connect(self.show_edit_content_confirmation)
        common_layout.addLayout(controls_layout)
        operation_layout.addWidget(common_box)

        self.parts_output_button = QPushButton("パーツJSONを新規保存")
        self.parts_output_button.setToolTip(
            "IDを持たない編集用パーツJSONを、必ず新しいファイルとして保存します。同名があれば連番を付けます。"
        )
        self.parts_output_button.clicked.connect(self.request_parts_output)
        self.parts_overwrite_button = QPushButton("読み込んだJSONへ上書き")
        self.parts_overwrite_button.setEnabled(False)
        self.parts_overwrite_button.clicked.connect(self.request_parts_overwrite)
        self.export_path_input.textChanged.connect(self._update_parts_overwrite_button)
        self.common_box = common_box
        layout.addWidget(self.operation_box)

        self.info_box = QGroupBox("現在の情報")
        info_layout = QVBoxLayout(self.info_box)
        info_layout.setSpacing(0)
        self.info_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.info_splitter.setChildrenCollapsible(False)

        values_panel = QWidget()
        values_layout = QGridLayout(values_panel)
        values_layout.setContentsMargins(0, 0, 6, 0)
        values_layout.setHorizontalSpacing(6)
        values_layout.setVerticalSpacing(2)
        values_layout.addWidget(QLabel("読み取り時"), 0, 0)
        values_layout.addWidget(self.selected_field_source_value, 0, 1)
        self.operation_draft_preview = QLineEdit()
        self.operation_draft_preview.setReadOnly(True)
        self.operation_draft_preview.setPlaceholderText("候補・項目・操作を選ぶと表示します")
        values_layout.addWidget(QLabel("操作のプレビュー"), 1, 0)
        values_layout.addWidget(self.operation_draft_preview, 1, 1)
        values_layout.addWidget(QLabel("出力予定"), 2, 0)
        values_layout.addWidget(self.selected_field_planned_value, 2, 1)
        values_layout.setColumnStretch(1, 1)

        notice_panel = QWidget()
        notice_layout = QVBoxLayout(notice_panel)
        notice_layout.setContentsMargins(6, 0, 0, 0)
        notice_layout.setSpacing(0)
        self.notice = QPlainTextEdit()
        self.notice.setReadOnly(True)
        self.notice.setPlaceholderText("通知欄：操作結果や注意事項がここに表示されます")
        self.notice.setToolTip("通知欄です。内容を選択してコピーできます。")
        self.notice.setStyleSheet(
            "QPlainTextEdit { background: palette(alternate-base); "
            "border: 1px solid palette(mid); padding: 3px; }"
        )
        notice_layout.addWidget(self.notice, 1)

        self.info_splitter.addWidget(values_panel)
        self.info_splitter.addWidget(notice_panel)
        self.info_splitter.setStretchFactor(0, 1)
        self.info_splitter.setStretchFactor(1, 2)
        self.info_splitter.setSizes([320, 640])
        info_layout.addWidget(self.info_splitter)
        self.info_box.setMinimumHeight(120)
        self.info_box.setMaximumHeight(145)
        layout.addWidget(self.info_box)

        self.final_actions_box = QGroupBox("確認と保存")
        final_actions = QHBoxLayout(self.final_actions_box)
        final_actions.addWidget(self.operation_preview_button)
        final_actions.addWidget(self.planned_output_preview_button)
        final_actions.addStretch(1)
        final_actions.addWidget(self.parts_output_button)
        final_actions.addWidget(self.parts_overwrite_button)
        layout.addWidget(self.final_actions_box)

        # Standard fields must be available before the first file is read.
        # This also hides the custom-key input until the user explicitly
        # chooses the custom-field entry.
        self._refresh_operation_field_choices()
        self._set_paths_locked(False)
        self.undo_shortcut = QShortcut(QKeySequence.StandardKey.Undo, self)
        self.undo_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self.undo_shortcut.activated.connect(self.undo_last_edit)
        self._update_undo_button()

    def _disable_mouse_wheel_interaction(self) -> None:
        """Block wheel-driven value changes while retaining box navigation."""
        self.installEventFilter(self)
        for widget in self.findChildren(QWidget):
            widget.installEventFilter(self)

    def eventFilter(self, watched: object, event: QEvent) -> bool:  # noqa: N802
        if event.type() == QEvent.Type.Wheel:
            current = watched if isinstance(watched, QWidget) else None
            while current is not None:
                if isinstance(current, QAbstractScrollArea):
                    # Lists and text boxes may use the wheel purely to move
                    # their viewport; value selectors remain blocked below.
                    return super().eventFilter(watched, event)
                if current is self:
                    break
                current = current.parentWidget()
            event.accept()
            return True
        return super().eventFilter(watched, event)

    def show_settings(self) -> None:
        """Show the saved JSON directly, so its only persistent value is visible."""
        dialog = QDialog(self)
        dialog.setWindowTitle("メディア情報整理の永続設定")
        dialog.setMinimumSize(620, 360)
        layout = QVBoxLayout(dialog)
        state, detail = settings.settings_status()
        layout.addWidget(QLabel(f"設定状態: {state} — {detail}"))
        layout.addWidget(
            QLabel(
                "保存するのは単一JSON読み込み時の保存先自動入力、右クリック用の登録パス、mpvキー・タグ候補、一覧の初期表示項目です。\n"
                "auto_fill_single_json_path を true にすると、読み込んだJSONが1個だけのとき、そのJSONをパーツ保存先へ自動入力します。\n"
                "パーツ保存先そのものは永続設定へ保存しません。\n"
                "mpv_path は、再生に使う mpv 実行ファイルの絶対パスです。既定値は /usr/bin/mpv です。\n"
                "mpv_shortcuts は、アプリ連携キーと任意mpvキーを直接確認・編集できます。\n"
                "mpv_tag_choices は、評価・タグ・見どころパッチ作成中に t で選べるタグ候補です。空の配列なら自由入力だけを使えます。\n"
                "パス一覧、読取結果、編集履歴、ファイル情報、再生位置は保存しません。\n"
                "状態・×は一覧の右端に常時表示します。default_visible_columns には、種別 / 出所 / サイズ / ファイル数 / メディア数 / 拡張子 / 解像度 / タグ / 評価 / 見どころ を配列で書けます。\n"
                "パーツの新規保存は必ず新しいJSONを作り、上書きは今回読み込んだ単一パーツJSONにだけ許可します。"
            )
        )
        editor = QTextEdit()
        editor.setPlainText(settings.editable_text())
        layout.addWidget(editor, 1)
        buttons = QDialogButtonBox()
        template_button = buttons.addButton("雛形へ戻す", QDialogButtonBox.ButtonRole.ResetRole)
        location_button = buttons.addButton("保存先入口", QDialogButtonBox.ButtonRole.ActionRole)
        create_button = buttons.addButton("保存先・設定を作成", QDialogButtonBox.ButtonRole.ActionRole)
        save_button = buttons.addButton("保存", QDialogButtonBox.ButtonRole.AcceptRole)
        close_button = buttons.addButton("閉じる", QDialogButtonBox.ButtonRole.RejectRole)
        template_button.clicked.connect(lambda: editor.setPlainText(settings.template_text()))
        location_button.clicked.connect(lambda: show_settings_location_editor(dialog))
        create_button.clicked.connect(lambda: create_app_settings_file(dialog, settings.create_settings_file))
        save_button.clicked.connect(self.save_settings)
        close_button.clicked.connect(dialog.reject)
        layout.addWidget(buttons)
        self._settings_dialog = dialog
        self._settings_editor = editor
        dialog.exec()
        self._settings_dialog = None
        self._settings_editor = None

    def save_settings(self) -> None:
        if self._settings_editor is None:
            return
        try:
            self._settings = settings.save_text(self._settings_editor.toPlainText())
        except ValueError as exc:
            self._notify("永続設定を保存できません。", str(exc))
            return
        if self._work_mode == "catalog" and self._settings["auto_fill_single_json_path"]:
            self._auto_fill_single_json_output_path()
        if self._settings_dialog is not None:
            self._settings_dialog.accept()
        self._notify(
            "永続設定を保存しました。",
            "保存したのは単一JSON保存先の自動入力設定、登録パス、mpvキー・タグ候補、一覧の初期表示項目です。パーツ保存先は保存していません。",
        )

    def confirm_all_paths(self) -> None:
        """Compatibility/default entry: lock one editable working copy."""
        self._confirm_paths(
            self.path_input.paths(deduplicate=True),
            label="一覧全て",
            access_mode="edit",
        )

    def confirm_all_paths_read_only(self) -> None:
        """Lock the same inputs as an explicitly non-editable browsing session."""
        self._confirm_paths(
            self.path_input.paths(deduplicate=True),
            label="一覧全て",
            access_mode="browse",
        )

    def select_all_locked_candidates(self) -> None:
        """Check every confirmed candidate without changing candidate data."""
        if not self._paths_locked:
            return
        self.path_input.select_all_items()
        self._notify("確定後の候補をすべてチェックしました。")

    def clear_locked_candidate_selection(self) -> None:
        """Clear every confirmed candidate check without removing any row."""
        if not self._paths_locked:
            return
        self.path_input.clear_item_selection()
        self._notify("確定後の候補のチェックをすべて外しました。候補は削除していません。")

    def _auto_fill_single_json_output_path(self) -> None:
        """Optionally copy the sole loaded JSON path into the session field."""
        if (
            self._access_mode != "edit"
            or self._work_mode != "catalog"
            or not self._settings["auto_fill_single_json_path"]
            or len(self._loaded_json_sources) != 1
        ):
            self._update_parts_overwrite_button()
            return
        path_text = str(self._loaded_json_sources[0].path)
        self._parts_output_text = path_text
        self.export_path_input.setText(path_text)
        self._update_parts_overwrite_button()

    def _parts_overwrite_target(self) -> tuple[Path | None, str]:
        """Return the sole safe overwrite target and a visible reason."""
        if (
            not self._paths_locked
            or self._access_mode != "edit"
            or self._work_mode != "catalog"
        ):
            return None, "候補を通常モードで確定すると、条件を確認します。"
        if len(self._loaded_json_sources) != 1:
            return None, "今回読み込んだJSONがちょうど1個のときだけ上書きできます。"
        source = self._loaded_json_sources[0]
        raw = self.export_path_input.text().strip()
        if not raw:
            return None, "パーツ保存先へ、読み込んだJSON自身のパスを入力してください。"
        try:
            output = normalize_path(Path(raw).expanduser())
        except (OSError, ValueError):
            return None, "パーツ保存先のパスを確認してください。"
        if output != source.path:
            return None, "パーツ保存先が、今回読み込んだ1個のJSON自身と一致していません。"
        if not output.is_file():
            return None, "上書き対象のJSONファイルが見つかりません。"
        return (
            output,
            "今回読み込んだ単一パーツJSONへ、安全確認後に全体を上書きします。"
            if source.is_parts_json
            else "今回読み込んだ単一JSONを、安全確認後にパーツJSON形式で全体上書きします。",
        )

    def _update_parts_overwrite_button(self, *_unused: object) -> None:
        if not hasattr(self, "parts_overwrite_button"):
            return
        output, reason = self._parts_overwrite_target()
        self.parts_overwrite_button.setEnabled(output is not None)
        self.parts_overwrite_button.setToolTip(reason)

    def _work_mode_changed(self) -> None:
        requested = str(self.work_mode_combo.currentData() or "catalog")
        if requested == self._work_mode:
            return
        if self._paths_locked:
            self.work_mode_combo.blockSignals(True)
            index = self.work_mode_combo.findData(self._work_mode)
            self.work_mode_combo.setCurrentIndex(index if index >= 0 else 0)
            self.work_mode_combo.blockSignals(False)
            self._notify("対象を確定中は作業モードを変更できません。先にロック解除してください。")
            return
        if self._work_mode == "catalog":
            self._parts_output_text = self.export_path_input.text()
        else:
            self._review_patch_output_text = self.export_path_input.text()
        self._work_mode = requested
        self.export_path_input.setText(
            self._review_patch_output_text if requested == "review_patch" else self._parts_output_text
        )
        self._configure_work_mode()
        if requested == "review_patch":
            self._notify(
                "MPV評価・タグ・見どころパッチ作成モードへ切り替えました。",
                "通常の項目編集・仮登録・パーツJSON保存は使えません。通常ファイルまたはパーツJSONを確定し、必要ならmpv連携動画を紐付けてください。",
            )
        else:
            self._notify("通常の情報整理モードへ戻しました。")

    def _configure_work_mode(self) -> None:
        """Keep the pathless MPV-review workflow visibly separate from editing."""
        review_mode = self._work_mode == "review_patch"
        self.export_target_label.setText(
            "パッチ保存先" if review_mode else "パーツ保存先"
        )
        self.export_path_input.setPlaceholderText(
            "フォルダ、または新しい media_review_patch の .json ファイル"
            if review_mode
            else "フォルダ、または .json ファイル名を入力"
        )
        self.export_path_input.setToolTip(
                "フォルダなら、新しい評価・タグ・見どころパッチJSONを作ります（同名があれば番号を付けます）。"
                "照合用ファイル名と、mpvで付けた評価・タグ・見どころだけを保存します。"
                " 実パス、解像度、サイズ、通常の編集項目は保存しません。"
            if review_mode
            else (
                "新規保存はフォルダへ新しい media_parts.json を作り、同名があれば番号を付けます。"
                "ドロップ時はフォルダと .json をそのまま使い、それ以外のファイルは親フォルダにします。"
                "上書きは、今回読み込んだJSONが1個だけで、保存先欄がそのJSON自身のときだけ専用ボタンで行えます。"
            )
        )
        if hasattr(self, "review_patch_output_button"):
            self._set_paths_locked(self._paths_locked)

    def clear_read_paths(self) -> None:
        if self._paths_locked:
            self._notify("対象は確定済みです。パスを変えるには先に「ロック解除」を押してください。")
            return
        self.path_input.clear_items()
        self._candidate_path_snapshot = None
        self._source_items.clear()
        self._items.clear()
        self._clear_session_edits()
        self._show_items()
        self._notify("一覧と今回の読取りデータを空にしました。実ファイル・JSONは変更していません。")

    def _confirm_paths(
        self, paths: Iterable[Path], *, label: str, access_mode: AccessMode
    ) -> None:
        if access_mode not in {"browse", "edit"}:
            raise ValueError("未対応の確定モードです。")
        if self._paths_locked:
            self._notify("対象はすでに確定済みです。パスを変えるには先に「ロック解除」を押してください。")
            return
        values = list(paths)
        if not values:
            self._notify("実ファイル・フォルダ・パーツJSONを追加し、チェックしてください。")
            return
        if self._work_mode == "review_patch":
            unsupported = [path for path in values if not path.is_file()]
            if unsupported:
                preview = "\n".join(str(path) for path in unsupported[:3])
                suffix = "\n…" if len(unsupported) > 3 else ""
                self._notify(
                    "MPV評価・タグ・見どころパッチ作成には、通常ファイルまたはパーツJSONを指定してください。",
                    preview + suffix,
                )
                return
        try:
            read_result = read_candidate_sources(values)
        except ValueError as exc:
            self._notify("入力元を候補へ読み込めません。", str(exc))
            return
        source_items = list(read_result.items)
        if not source_items:
            self._notify("候補として読み込める項目がありません。")
            return
        self._candidate_path_snapshot = self.path_input.snapshot()
        self._candidate_source_details = read_result.source_details
        self._loaded_json_sources = read_result.json_sources
        self._locked_input_summary = read_result.summary
        self._source_items = source_items
        self._items = list(self._source_items)
        self._access_mode = access_mode
        self._filter_rules = ()
        self._browse_session_notes.clear()
        self.search_input.clear()
        self.filter_button.setText("詳細条件…")
        self.sort_combo.setCurrentIndex(0)
        self._clear_session_edits()
        # After locking, the same table becomes a candidate list.  Every row
        # is virtual deliberately: its visible name is not mistaken for a
        # writable filesystem path, and JSON candidates can stand alongside
        # real files without creating fake paths.
        self.path_input.setPlainText("")
        for item in self._source_items:
            token = media_item_token(item)
            source_label, tooltip = self._candidate_source_details.get(
                token, _candidate_source_detail(item)
            )
            self.path_input.append_virtual_item(
                token,
                _candidate_display_name(item),
                tooltip=tooltip,
            )
            self._candidate_source_details[token] = (source_label, tooltip)
        # The source-list checks do not restrict loading.  Once the candidates
        # exist, checks become the default output/operation selection instead.
        self.path_input.select_all_items()
        self._set_paths_locked(True)
        self._auto_fill_single_json_output_path()
        self._show_items()
        if access_mode == "browse":
            self._notify(
                f"{label}{len(self._items)}件を閲覧モードで確定してロックしました。",
                "検索・並べ替え・ファイル紐付け・mpv再生を使えます。元JSONと画面内データは編集しません。",
            )
        elif self._work_mode == "review_patch":
            self._notify(
                f"{label}{len(self._items)}件をMPV評価用に確定してロックしました。",
                "mpvで評価・タグ・見どころを付け、JSON候補は「mpv連携動画を紐付け…」で実動画を結びます。最後にパッチを出力してください。実パスはJSONへ保存しません。",
            )
        else:
            self._notify(
                f"{label}{len(self._items)}件を候補として確定してロックしました。",
                "編集欄で候補を編集できます。実ファイル・元JSONは変更していません。",
            )

    def unlock_confirmed_paths(self) -> None:
        """Return to collection mode and deliberately discard all session edits."""
        if not self._paths_locked:
            return
        snapshot = self._candidate_path_snapshot
        self._source_items.clear()
        self._items.clear()
        self._candidate_source_details.clear()
        self._loaded_json_sources = ()
        self._locked_input_summary = ""
        self._access_mode = None
        self._filter_rules = ()
        self._browse_session_notes.clear()
        self.search_input.clear()
        self.filter_button.setText("詳細条件…")
        self.sort_combo.setCurrentIndex(0)
        self._clear_session_edits()
        self._set_paths_locked(False)
        if snapshot is not None:
            self.path_input.restore_snapshot(snapshot)
        self._candidate_path_snapshot = None
        self._show_items()
        self._notify(
            "対象のロックを解除し、今回の読取り・編集内容を白紙に戻しました。",
            "パス一覧だけは残しています。必要な対象へ再びチェックして確定してください。",
        )

    def add_manual_placeholder(self) -> None:
        """Add one explicitly pathless, uncollected record to the locked batch."""
        if self._access_mode != "edit":
            self._notify("閲覧モードでは仮登録を追加できません。")
            return
        if self._work_mode == "review_patch":
            self._notify("MPV評価・タグ・見どころパッチ作成では仮登録を追加できません。通常ファイルだけを扱います。")
            return
        if not self._paths_locked:
            self._notify("仮登録は、先に実パスの対象を確定してから追加できます。")
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("仮登録を追加")
        dialog.setMinimumWidth(460)
        layout = QVBoxLayout(dialog)
        layout.addWidget(
            QLabel(
                "実在パスは作りません。名前と収集状態だけで、今回の編集・JSON出力対象へ追加します。"
            )
        )
        name_input = QLineEdit()
        name_input.setPlaceholderText("仮題・予定ファイル名（必須）")
        layout.addWidget(name_input)
        status_row = QHBoxLayout()
        status_row.addWidget(QLabel("収集状態"))
        status_combo = QComboBox()
        for status in COLLECTION_STATUS_PRESETS:
            status_combo.addItem(status, status)
        missing_index = status_combo.findData("未保有")
        status_combo.setCurrentIndex(missing_index if missing_index >= 0 else 0)
        status_row.addWidget(status_combo, 1)
        layout.addLayout(status_row)
        note = QLabel("サイズ・解像度・長さなど、実ファイルから読む項目は空欄で編集できません。")
        note.setWordWrap(True)
        layout.addWidget(note)
        buttons = QDialogButtonBox()
        buttons.addButton("キャンセル", QDialogButtonBox.ButtonRole.RejectRole).clicked.connect(dialog.reject)
        buttons.addButton("追加", QDialogButtonBox.ButtonRole.AcceptRole).clicked.connect(dialog.accept)
        layout.addWidget(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            placeholder = create_manual_placeholder_item(
                name_input.text(), collection_status=str(status_combo.currentData() or "")
            )
            source_label, tooltip = _candidate_source_detail(placeholder)
            self.path_input.append_virtual_item(
                media_item_token(placeholder),
                _candidate_display_name(placeholder),
                tooltip=tooltip,
            )
        except ValueError as exc:
            self._notify("仮登録を追加できません。", str(exc))
            return
        self._source_items.append(placeholder)
        self._items.append(placeholder)
        self._candidate_source_details[media_item_token(placeholder)] = (source_label, tooltip)
        self._invalidate_review_patch_merge()
        self._show_items()
        self._notify(
            f"仮登録を追加しました: {media_item_display_name(placeholder)}",
            "実在パスは作成していません。編集欄で情報を補い、出力時だけJSONへ追加します。",
        )

    def _clear_session_edits(self) -> None:
        self._invalidate_review_patch_merge()
        self._applied_operation_summaries.clear()
        self._applied_operations.clear()
        self._undo_history.clear()
        self._individual_row = None
        self.operation_value_input.clear()
        self.preset_value_combo.setCurrentIndex(0)
        self._update_undo_button()

    def _remember_edit_for_undo(self, description: str) -> None:
        """Capture the complete output plan before one in-memory edit."""
        self._undo_history.append(
            _UndoState(
                items=tuple(self._items),
                applied_operations=tuple(self._applied_operations),
                summaries=tuple(self._applied_operation_summaries),
                description=description,
            )
        )
        del self._undo_history[:-20]
        self._update_undo_button()

    def undo_last_edit(self) -> None:
        """Restore the last in-memory output plan without touching saved data."""
        if self._access_mode != "edit":
            self._notify("閲覧モードでは編集内容を変更できません。")
            return
        if not self._undo_history:
            self._notify("戻せる画面内操作はありません。")
            return
        previous = self._undo_history.pop()
        self._items = list(previous.items)
        self._applied_operations = list(previous.applied_operations)
        self._applied_operation_summaries = list(previous.summaries)
        self._invalidate_review_patch_merge()
        self._show_items()
        self._show_current_operation_state()
        self._update_undo_button()
        self._notify(
            f"直前の操作を戻しました: {previous.description}",
            "画面内の出力予定だけを戻しました。保存済みJSON・実ファイルは変更していません。",
        )

    def _update_undo_button(self) -> None:
        if not hasattr(self, "undo_button"):
            return
        self.undo_button.setEnabled(bool(self._undo_history))

    def _invalidate_review_patch_merge(self) -> None:
        """Invalidate a reviewed patch when candidate data has changed."""
        self._review_patch = None
        self._review_merge_plan = None
        self._review_merge_items_snapshot = None
        self._single_field_patch = None
        self._single_field_merge_plan = None
        self._single_field_merge_items_snapshot = None

    def _set_paths_locked(self, locked: bool) -> None:
        self._paths_locked = locked
        editable = locked and self._access_mode == "edit"
        if not locked:
            self._mpv_linked_paths.clear()
        self.path_input.set_paths_locked(locked)
        self.read_actions_box.setVisible(not locked)
        self.locked_tools_box.setVisible(locked)
        for widget in (
            self.confirm_all_button,
            self.clear_paths_button,
        ):
            widget.setVisible(not locked)
        self.unlock_paths_button.setVisible(locked)
        self.play_mpv_playlist_button.setVisible(locked)
        self.link_mpv_videos_button.setVisible(locked)
        self.browse_tools_box.setVisible(locked)
        self.add_placeholder_button.setVisible(editable)
        self.operation_box.setVisible(editable)
        self.field_box.setVisible(editable)
        self.common_box.setVisible(editable)
        self.operation_preview_button.setVisible(editable)
        self.planned_output_preview_button.setVisible(editable)
        self.parts_output_button.setVisible(editable)
        self.parts_overwrite_button.setVisible(editable)
        self.final_actions_box.setVisible(editable)
        self.export_target_label.setVisible(editable)
        self.export_path_input.setVisible(editable)
        self.operation_box.setEnabled(editable)
        self._update_parts_overwrite_button()
        self._show_current_operation_state()

    def _show_items(self) -> None:
        self._refresh_operation_field_choices()
        if self._paths_locked:
            self._refresh_candidate_table()
            self._refresh_browse_view()

    def _workspace_records(self) -> tuple[WorkspaceRecord, ...]:
        """Project the current working rows into the shared browse model."""
        return tuple(
            WorkspaceRecord(index, catalog_attributes_from_media_item(item))
            for index, item in enumerate(self._items)
        )

    def _refresh_browse_view(self, *_unused: object) -> None:
        """Apply UI-only search, conditions and ordering to the common table."""
        if not self._paths_locked:
            return
        query = self.search_input.text().strip().casefold()
        records = list(self._workspace_records())
        visible_indexes = {
            record.index
            for record in records
            if (
                not query
                or query in self._workspace_record_search_text(record).casefold()
            )
            and record_matches_rules(record, self._filter_rules)
        }
        sort_mode = str(self.sort_combo.currentData() or "input")
        if sort_mode == "name":
            records.sort(key=lambda record: record.title.casefold())
        elif sort_mode == "rating_desc":
            records.sort(
                key=lambda record: (
                    record.rating_stars is None,
                    -(record.rating_stars or 0),
                    record.title.casefold(),
                )
            )
        elif sort_mode == "rating_asc":
            records.sort(
                key=lambda record: (
                    record.rating_stars is None,
                    record.rating_stars or 0,
                    record.title.casefold(),
                )
            )
        ordered_tokens = [
            media_item_token(self._source_items[record.index]) for record in records
        ]
        hidden_tokens = {
            media_item_token(item)
            for index, item in enumerate(self._source_items)
            if index not in visible_indexes
        }
        self.path_input.reorder_items(ordered_tokens)
        self.path_input.set_hidden_item_tokens(hidden_tokens)
        self._update_browse_status()

    def _workspace_record_search_text(self, record: WorkspaceRecord) -> str:
        token = media_item_token(self._source_items[record.index])
        notes = " / ".join(self._browse_session_notes.get(token, ()))
        return "\n".join(value for value in (record_search_text(record), notes) if value)

    def _update_browse_status(self, *_unused: object) -> None:
        if not hasattr(self, "browse_status_label"):
            return
        visible = set(self.path_input.visible_item_tokens()) if self._paths_locked else set()
        checked = set(self.path_input.selected_item_tokens(deduplicate=True))
        blue = set(self.path_input.row_selected_item_tokens(deduplicate=True))
        transferable = len(self._visible_checked_paths()) if self._paths_locked else 0
        playable = (
            sum(
                self._mpv_path_for_item(item) is not None
                for item in self._visible_source_items()
            )
            if self._paths_locked
            else 0
        )
        mode = "閲覧" if self._access_mode == "browse" else "編集" if self._access_mode == "edit" else "未確定"
        self.browse_status_label.setText(
            f"モード: {mode} / 表示: {len(visible)}件 / 青い選択: {len(blue)}件 / "
            f"表示中のチェック: {len(visible & checked)}件 / 転送可能: {transferable}件 / "
            f"再生可能: {playable}件"
        )
        self.file_manager_button.setEnabled(transferable > 0)
        self.play_mpv_playlist_button.setEnabled(playable > 0)

    def _set_blue_selection_checked(self, checked: bool) -> None:
        count = self.path_input.set_row_selected_items_checked(checked)
        if not count:
            self._notify(
                "青く選択した候補がないか、チェック状態が既に同じです。",
                "Ctrl+A、Shift+↑/↓、Ctrl+クリックで複数行を選択できます。",
            )
            return
        action = "チェックしました" if checked else "チェックを外しました"
        self._notify(f"青く選択した{count}件を{action}。")
        self._update_browse_status()

    def _open_filter_dialog(self) -> None:
        if not self._paths_locked or not self._items:
            self._notify("先に対象を閲覧または編集モードで確定してください。")
            return
        dialog = DetailedFilterDialog(
            self._workspace_records(), self._filter_rules, self
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._filter_rules = dialog.rules
        count = len(self._filter_rules)
        self.filter_button.setText(
            f"詳細条件…（{count}件）" if count else "詳細条件…"
        )
        self._refresh_browse_view()
        self._notify(
            f"詳細条件を{'適用' if count else '解除'}しました。",
            f"現在の表示は{len(self.path_input.visible_item_tokens())}件です。",
        )

    def _open_file_path_link_dialog(self) -> None:
        if not self._paths_locked or not self._items:
            self._notify("先に対象を閲覧または編集モードで確定してください。")
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("ファイルパスを今回だけ紐付ける")
        dialog.setMinimumSize(780, 460)
        layout = QVBoxLayout(dialog)
        explanation = QLabel(
            "ファイルとフォルダを複数登録できます。フォルダは直下のファイルだけを使います。"
            "完全一致を優先し、その後に記号・空白・拡張子の違いを緩く照合します。"
            "紐付けはこの画面を閉じると消え、JSONには保存しません。"
        )
        explanation.setWordWrap(True)
        layout.addWidget(explanation)
        paths = PathListInput(
            rows=9,
            accepted_path_kind="all",
            drop_replaces=False,
            show_controls=False,
            show_column_headers=True,
            path_column_label="照合するファイル・フォルダ",
        )
        layout.addWidget(paths, 1)
        actions = QHBoxLayout()
        add_files = QPushButton("ファイルを複数追加…")
        add_files.clicked.connect(lambda: self._add_file_link_files(paths, dialog))
        actions.addWidget(add_files)
        add_folder = QPushButton("フォルダを追加…")
        add_folder.clicked.connect(lambda: self._add_file_link_directory(paths, dialog))
        actions.addWidget(add_folder)
        clear = QPushButton("一覧を空にする")
        clear.clicked.connect(paths.clear_items)
        actions.addWidget(clear)
        actions.addStretch(1)
        run = QPushButton("照合して紐付け")
        run.clicked.connect(
            lambda: self._link_file_paths(paths.items(deduplicate=True), dialog)
        )
        actions.addWidget(run)
        layout.addLayout(actions)
        notice = QLabel(
            f"全入力を合わせて先頭{MAXIMUM_MATCH_CANDIDATES}件まで照合します。"
            "同じファイル名が複数あれば、何も紐付けず停止します。"
        )
        notice.setWordWrap(True)
        layout.addWidget(notice)
        dialog.exec()

    def _add_file_link_files(self, target: PathListInput, dialog: QDialog) -> None:
        paths, _filter = QFileDialog.getOpenFileNames(
            dialog, "照合するファイルを追加", "", "すべてのファイル (*)"
        )
        if paths:
            target.append_items(paths)

    def _add_file_link_directory(self, target: PathListInput, dialog: QDialog) -> None:
        path = QFileDialog.getExistingDirectory(dialog, "照合するフォルダを追加")
        if path:
            target.append_items((path,))

    def _link_file_paths(self, values: Iterable[str | Path], dialog: QDialog) -> None:
        sources = tuple(values)
        if not sources:
            QMessageBox.warning(dialog, "ファイルパス紐付け", "ファイルまたはフォルダを1件以上登録してください。")
            return
        try:
            candidates = collect_path_candidates(sources)
            matches = match_paths(self._workspace_records(), candidates)
        except ValueError as exc:
            QMessageBox.warning(dialog, "ファイルパス紐付け", str(exc))
            return
        self._mpv_linked_paths = {
            media_item_token(self._source_items[index]): path
            for index, path in matches.items()
        }
        self._refresh_mpv_link_display()
        self._refresh_browse_view()
        unmatched = len(self._items) - len(matches)
        self._notify(
            f"ファイル候補{len(candidates)}件から、{len(matches)}件を今回だけ紐付けました。",
            f"候補{unmatched}件は未紐付けのままです。JSON・ファイルは変更していません。",
        )
        dialog.accept()

    def _linked_or_source_path(self, item: MediaItem) -> Path | None:
        linked = self._mpv_linked_paths.get(media_item_token(item))
        if linked is not None:
            return linked
        return item.path

    def _visible_checked_paths(self) -> tuple[Path, ...]:
        checked = set(self.path_input.selected_item_tokens(deduplicate=True))
        paths: list[Path] = []
        for item in self._visible_source_items():
            token = media_item_token(item)
            path = self._linked_or_source_path(item)
            if token in checked and path is not None and path.exists():
                paths.append(path)
        return tuple(dict.fromkeys(paths))

    def _send_checked_paths_to_file_manager(self) -> None:
        paths = self._visible_checked_paths()
        if not paths:
            self._notify("表示中・チェック済みの候補に転送できるパスがありません。")
            return
        manager: FileManagerScreen
        manager = FileManagerScreen(lambda: manager.close())
        manager.setWindowTitle("PORTA — ファイルマネージャー（メディア情報から）")
        manager.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        manager.receive_external_paths(paths)
        _INDEPENDENT_FILE_MANAGERS.add(manager)
        manager.destroyed.connect(
            lambda _object=None, screen=manager: _INDEPENDENT_FILE_MANAGERS.discard(screen)
        )
        manager.show()
        manager.raise_()
        manager.activateWindow()
        self._notify(
            f"表示中・チェック済みの{len(paths)}件を独立したファイルマネージャーへ渡しました。"
        )

    def _add_read_item_context_actions(self, menu: QMenu, row: QTreeWidgetItem | None) -> None:
        """Add list collection, inspection and session-only column actions."""
        columns_menu = menu.addMenu("表示する項目")
        columns_menu.setToolTipsVisible(True)
        for label in (
            "種別",
            "出所",
            "ファイル数",
            "メディア数",
            *(column_label for column_label, _key in _MEDIA_ATTRIBUTE_COLUMNS),
            _RAW_ATTRIBUTES_COLUMN,
        ):
            action = columns_menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(self.path_input.supplemental_column_visible(label))
            if label == _RAW_ATTRIBUTES_COLUMN:
                action.setToolTip("標準項目として意味を付けていない属性を、JSON形式の生データで表示します。")
            elif label in _ATTRIBUTE_KEY_FOR_COLUMN:
                action.setToolTip(f"{_ATTRIBUTE_KEY_FOR_COLUMN[label]} を表示します。")
            action.toggled.connect(
                lambda visible, column_label=label: self.path_input.set_supplemental_column_visible(
                    column_label, visible
                )
            )
        if self._paths_locked and self._access_mode == "edit":
            sort_menu = menu.addMenu("候補をファイル名で並べ替え")
            ascending_action = sort_menu.addAction("昇順（A → Z）")
            ascending_action.setToolTip("候補をファイル名の昇順へ並べ替えます。次の新規保存の順序にも反映します。")
            ascending_action.triggered.connect(lambda: self._sort_candidates_by_file_name(ascending=True))
            descending_action = sort_menu.addAction("降順（Z → A）")
            descending_action.setToolTip("候補をファイル名の降順へ並べ替えます。次の新規保存の順序にも反映します。")
            descending_action.triggered.connect(lambda: self._sort_candidates_by_file_name(ascending=False))
        if not self._paths_locked:
            menu.addSeparator()
            expand_action = menu.addAction("チェック済みフォルダを直下項目へ展開")
            expand_action.setToolTip("チェック済みの項目を一覧から外し、選択フォルダの直下項目だけを追加します。")
            expand_action.triggered.connect(lambda: self.expand_checked_directories(with_query=False))
            query_action = menu.addAction("条件を指定して直下項目へ展開…")
            query_action.setToolTip("検索語・方式・種別を指定し、直下の一致項目だけを追加します。")
            query_action.triggered.connect(lambda: self.expand_checked_directories(with_query=True))
            self._add_read_registered_paths_menu(menu)
        item = self._media_item_for_tree_row(row)
        if item is None:
            return
        if (
            self._paths_locked
            and self._access_mode == "edit"
            and self._work_mode != "review_patch"
        ):
            rating_menu = menu.addMenu("評価")
            self._add_rating_actions(rating_menu, item)
        if (
            self._paths_locked
            and self._access_mode == "edit"
            and self._work_mode == "review_patch"
        ):
            clear_rating_action = menu.addAction("この候補の評価を消去")
            clear_rating_action.setToolTip(
                "今回の出力予定から、この候補の評価だけを外します。"
                "実ファイルと既存JSONは変更しません。"
            )
            clear_rating_action.triggered.connect(
                lambda _checked=False, selected_item=item: self._clear_item_review_fields(
                    selected_item, ("review.score",), "評価"
                )
            )
            clear_tags_action = menu.addAction("この候補のタグを消去")
            clear_tags_action.setToolTip(
                "今回の出力予定から、この候補のタグだけを外します。"
                "実ファイルと既存JSONは変更しません。"
            )
            clear_tags_action.triggered.connect(
                lambda _checked=False, selected_item=item: self._clear_item_review_fields(
                    selected_item, ("classification.tag",), "タグ"
                )
            )
            clear_highlights_action = menu.addAction("この候補の見どころを消去")
            clear_highlights_action.setToolTip(
                "今回の出力予定から、この候補の見どころ時間・見どころメモを外します。"
                "実ファイルと既存JSONは変更しません。"
            )
            clear_highlights_action.triggered.connect(
                lambda _checked=False, selected_item=item: self._clear_item_review_fields(
                    selected_item, ("media.highlights",), "見どころ"
                )
            )
        menu.addSeparator()
        detail_action = menu.addAction("詳細項目を別ウィンドウで表示")
        detail_action.triggered.connect(
            lambda _checked=False, selected_item=item: show_detached_item_details(selected_item)
        )
        if (
            self._paths_locked
            and self._access_mode == "edit"
            and self._work_mode != "review_patch"
        ):
            remove_action = menu.addAction("この候補を今回の対象から除く")
            remove_action.setToolTip("実ファイル・元JSONは変更せず、今回の一覧からだけ除きます。")
            remove_action.triggered.connect(
                lambda: self.remove_candidate(item, row)
            )
        if item.path is not None:
            open_action = menu.addAction("標準アプリで開く")
            open_action.triggered.connect(lambda: self.open_item(item))
            parent_action = menu.addAction("親フォルダを開く")
            parent_action.triggered.connect(lambda: self._open_parent_folder(item.path))
        if self._paths_locked and self._mpv_path_for_item(item) is not None:
            play_from_here_action = menu.addAction("ここからmpvで再生")
            play_from_here_action.setToolTip(
                "対象一覧全体をプレイリストへ渡し、この候補から再生を始めます。"
            )
            play_from_here_action.triggered.connect(
                lambda _checked=False, selected_item=item: self.play_confirmed_items_from_mpv(selected_item)
            )

    def _add_read_registered_paths_menu(self, menu: QMenu) -> None:
        """Offer only configured persistent paths; blank defaults show no menu."""
        paths = self._settings["registered_paths"]
        if not paths:
            return
        registered_menu = QMenu("登録パスを追加", menu)
        registered_menu.setToolTipsVisible(True)
        menu.addMenu(registered_menu)
        for path_text in paths:
            action = registered_menu.addAction(path_text)
            action.setToolTip(path_text)
            action.triggered.connect(
                lambda _checked=False, value=path_text: self._add_registered_read_path(value)
            )

    def _add_output_registered_paths_menu(self, menu: QMenu) -> None:
        """Apply a configured path to the one-line output input."""
        paths = self._settings["registered_paths"]
        if not paths:
            return
        menu.addSeparator()
        registered_menu = QMenu("登録パスを設定", menu)
        registered_menu.setToolTipsVisible(True)
        menu.addMenu(registered_menu)
        for path_text in paths:
            action = registered_menu.addAction(path_text)
            action.setToolTip(path_text)
            action.triggered.connect(
                lambda _checked=False, value=path_text: self._set_registered_output_path(value)
            )

    def _add_registered_read_path(self, value: str) -> None:
        count = self.path_input.add_external_paths([normalize_path(value)])
        self._notify(f"登録パスを作業一覧へ追加しました（{count}件）。")

    def _set_registered_output_path(self, value: str) -> None:
        path = self.export_path_input.set_path_from_external_value(normalize_path(value))
        self._notify(f"出力先を登録パスで変更しました。\n{path}")

    def expand_checked_directories(self, *, with_query: bool) -> None:
        """Replace checked rows with direct child paths, without touching files."""
        checked_paths = self.path_input.selected_paths(deduplicate=True)
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
            mode = str(mode_combo.currentData() or "contains")
            item_kind = str(kind_combo.currentData() or "all")
        try:
            children, folder_count = direct_children_for_selected_folders(
                checked_paths, query, mode=mode, item_kind=item_kind
            )
        except (OSError, ValueError) as exc:
            self._notify("直下項目へ展開していません。", str(exc))
            return
        self.path_input.replace_checked_items(str(path) for path in children)
        condition = f"条件「{query}」" if with_query and query.strip() else "条件なし"
        self._notify(
            f"チェック済み{len(checked_paths)}件を置換しました。"
            f" 展開: {folder_count}フォルダ / 追加: {len(children)}件 / {condition}"
        )

    def _open_parent_folder(self, path: Path) -> None:
        target = path if path.is_dir() else path.parent
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(target))):
            self._notify(f"親フォルダを開く要求を送れませんでした。\n{target}")

    def _build_rating_context_menu(
        self, row: QTreeWidgetItem | None, column: int
    ) -> QMenu | None:
        """Use an uncluttered rating-only menu when the rating cell is clicked."""
        if (
            column != self.path_input.supplemental_column_index("評価")
            or not self._paths_locked
            or self._access_mode != "edit"
            or self._work_mode == "review_patch"
        ):
            return None
        item = self._media_item_for_tree_row(row)
        if item is None:
            return None
        menu = QMenu(self.path_input)
        self._add_rating_actions(menu, item)
        return menu

    def _add_rating_actions(self, menu: QMenu, source_item: MediaItem) -> None:
        """Add the compact 0–10 rating choices shared by both context menus."""
        for score in range(11):
            stars = "★" * score + "☆" * (10 - score)
            action = menu.addAction(f"{stars}  {score}/10")
            action.setToolTip(f"このファイルの評価を {score}/10 にします。JSONへの書込みは「出力」時だけです。")
            action.triggered.connect(
                lambda _checked=False, selected_score=score, item=source_item: self._set_item_rating(
                    item, selected_score
                )
            )

    def _set_item_rating(self, source_item: MediaItem, score: int) -> None:
        """Stage a one-item rating without touching its file or target JSON."""
        if not self._paths_locked or self._access_mode != "edit":
            if self._access_mode == "browse":
                self._notify("閲覧モードでは評価を変更できません。")
            return
        try:
            item_index = self._source_items.index(source_item)
        except ValueError:
            self._notify("評価対象を特定できませんでした。")
            return
        try:
            proposed = apply_catalog_field_operation(
                self._items[item_index],
                key="review.score",
                operation="replace",
                value=f"{score / 10:.1f}",
            )
        except ValueError as exc:
            self._notify("評価を反映できません。", str(exc))
            return
        before_value = _catalog_value(self._items[item_index], "review.score") or "（空欄）"
        after_value = _catalog_value(proposed, "review.score") or "（空欄）"
        self._remember_edit_for_undo(f"{media_item_display_name(source_item)} の評価変更")
        self._items[item_index] = proposed
        self._invalidate_review_patch_merge()
        applied = _AppliedOperation(
            key="review.score",
            operation="replace",
            value=f"{score / 10:.1f}",
            item_count=1,
            scope="individual",
            item_name=media_item_display_name(source_item),
        )
        self._applied_operations.append(applied)
        self._applied_operation_summaries.append(
            f"{media_item_display_name(source_item)}: {self._applied_operation_summary(applied)} "
            f"[{before_value} → {after_value}]"
        )
        self._show_items()
        self._notify(
            f"{media_item_display_name(source_item)} を {score}/10 と評価しました。",
            "出力予定へ反映済みです。実ファイル・JSONはまだ変更していません。",
        )

    def _clear_item_review_fields(
        self, source_item: MediaItem, keys: tuple[str, ...], label: str
    ) -> None:
        """Remove selected player-created facts from one pending patch entry."""
        if (
            not self._paths_locked
            or self._access_mode != "edit"
            or self._work_mode != "review_patch"
        ):
            return
        try:
            item_index = self._source_items.index(source_item)
        except ValueError:
            self._notify("消去する候補を特定できませんでした。")
            return
        try:
            proposed = self._items[item_index]
            for key in keys:
                proposed = apply_catalog_field_operation(proposed, key=key, operation="clear")
        except ValueError as exc:
            self._notify(f"{label}を消去できません。", str(exc))
            return
        self._remember_edit_for_undo(f"{media_item_display_name(source_item)} の{label}消去")
        self._items[item_index] = proposed
        self._invalidate_review_patch_merge()
        applied = _AppliedOperation(
            key=label,
            operation="clear",
            value="",
            item_count=1,
            scope="individual",
            item_name=media_item_display_name(source_item),
        )
        self._applied_operations.append(applied)
        self._applied_operation_summaries.append(
            f"{media_item_display_name(source_item)}: {self._applied_operation_summary(applied)}"
        )
        self._show_items()
        self._notify(
            f"{media_item_display_name(source_item)} の{label}を消去しました。",
            f"今回の評価パッチにはこの候補の{label}を出力しません。実ファイル・既存JSONは変更していません。",
        )

    def _media_item_for_tree_row(self, row: QTreeWidgetItem | None) -> MediaItem | None:
        if row is None:
            return None
        token = self.path_input.item_token(row)
        return next(
            (
                item
                for item in self._source_items
                if media_item_token(item) == token
            ),
            None,
        )

    def remove_candidate(
        self, source_item: MediaItem, row: QTreeWidgetItem | None
    ) -> None:
        """Remove one locked candidate from this session without touching its source."""
        if not self._paths_locked or self._access_mode != "edit" or row is None:
            return
        token = media_item_token(source_item)
        index = next(
            (
                candidate_index
                for candidate_index, candidate in enumerate(self._source_items)
                if media_item_token(candidate) == token
            ),
            None,
        )
        if index is None:
            return
        self.path_input.remove_item(row)
        del self._source_items[index]
        del self._items[index]
        self._candidate_source_details.pop(token, None)
        self._mpv_linked_paths.pop(token, None)
        self._invalidate_review_patch_merge()
        if self._individual_row is row:
            self._individual_row = None
        self._set_paths_locked(True)
        self._show_items()
        self._notify(
            f"候補を今回の対象から除きました: {media_item_display_name(source_item)}",
            "実ファイル・元JSONは変更していません。",
        )

    def show_item_details(self, item: MediaItem) -> QDialog:
        """Compatibility entry for the detached, read-only detail window."""
        return show_detached_item_details(item)

    def _refresh_operation_field_choices(self) -> None:
        current_key = self._selected_operation_key(allow_blank=True)
        self.operation_field_combo.blockSignals(True)
        self.operation_field_combo.clear()
        scope = str(self.operation_field_scope_combo.currentData() or "frequent")
        fields = (
            STANDARD_CATALOG_FIELDS
            if scope == "all"
            else tuple(field for field in STANDARD_CATALOG_FIELDS if field.key in FREQUENT_CATALOG_FIELD_KEYS)
        )
        for field in fields:
            suffix = "（追加可）" if field.allows_multiple else ""
            self.operation_field_combo.addItem(f"{field.label}: {field.key}{suffix}", field.key)
        extras = {
            attribute.key
            for item in self._items
            for attribute in catalog_attributes_from_media_item(item)
            if catalog_field_for_key(attribute.key) is None
        }
        for key in sorted(extras):
            self.operation_field_combo.addItem(f"独自項目: {key}", key)
        self.operation_field_combo.addItem("新しい独自項目を指定…", "__custom__")
        index = self.operation_field_combo.findData(current_key)
        self.operation_field_combo.setCurrentIndex(index if index >= 0 else 0)
        self.operation_field_combo.blockSignals(False)
        self._update_operation_controls()

    def _operation_value_text(self) -> str:
        """Return the selected preset or free text in the compact common editor."""
        if not preset_values_for_catalog_field(self._selected_operation_key(allow_blank=True)):
            return self.operation_value_input.text()
        selected = str(self.preset_value_combo.currentData() or "")
        return self.operation_value_input.text() if selected == "__custom__" else selected

    def _populate_preset_value_choices(self, key: str) -> bool:
        """Show fixed choices only for fields whose values have a small vocabulary."""
        choices = preset_values_for_catalog_field(key)
        previous = self.preset_value_combo.currentData()
        self.preset_value_combo.blockSignals(True)
        self.preset_value_combo.clear()
        if choices:
            label = "評価を選択" if key == "review.score" else "値を選択"
            self.preset_value_combo.addItem(label, "")
            for display, value in choices:
                self.preset_value_combo.addItem(display, value)
            self.preset_value_combo.addItem("自由入力…", "__custom__")
            previous_index = self.preset_value_combo.findData(previous)
            self.preset_value_combo.setCurrentIndex(previous_index if previous_index >= 0 else 0)
        self.preset_value_combo.blockSignals(False)
        return bool(choices)

    def _selected_operation_key(self, *, allow_blank: bool = False) -> str:
        key = str(self.operation_field_combo.currentData() or "")
        if key == "__custom__":
            key = self.operation_custom_key_input.text().strip()
        return key if key or allow_blank else ""

    @staticmethod
    def _allowed_operation_choices(key: str) -> tuple[tuple[str, str], ...]:
        """Expose only operations the selected field can actually accept."""
        field = catalog_field_for_key(key)
        if field is None:
            return (
                ("値を上書き", "replace"),
                ("値を追加", "append"),
                ("値を空にする", "clear"),
                ("項目そのものを削除", "remove"),
            )
        if field.key == "media.highlights":
            return (("値を空にする", "clear"),)
        choices = [("値を上書き", "replace"), ("値を空にする", "clear")]
        if field.allows_multiple:
            choices.insert(1, ("値を追加", "append"))
        return tuple(choices)

    def _refresh_operation_kind_choices(self, key: str) -> None:
        choices = self._allowed_operation_choices(key)
        current = str(self.operation_kind_combo.currentData() or "")
        if [self.operation_kind_combo.itemData(index) for index in range(self.operation_kind_combo.count())] == [
            value for _label, value in choices
        ]:
            return
        self.operation_kind_combo.blockSignals(True)
        self.operation_kind_combo.clear()
        for label, value in choices:
            self.operation_kind_combo.addItem(label, value)
        index = self.operation_kind_combo.findData(current)
        self.operation_kind_combo.setCurrentIndex(index if index >= 0 else 0)
        self.operation_kind_combo.blockSignals(False)

    def _update_operation_controls(self) -> None:
        selected_data = str(self.operation_field_combo.currentData() or "")
        is_custom = selected_data == "__custom__"
        self.operation_custom_key_input.setEnabled(is_custom)
        self.operation_custom_key_input.setVisible(is_custom)
        key = self._selected_operation_key(allow_blank=True)
        self._refresh_operation_kind_choices(key)
        field = catalog_field_for_key(key)
        operation = str(self.operation_kind_combo.currentData() or "")
        needs_value = operation in {"replace", "append"}
        folder_reason = self._selected_folder_field_reason(key)
        can_edit = folder_reason is None
        self.operation_kind_combo.setEnabled(can_edit)
        has_presets = self._populate_preset_value_choices(key)
        self.preset_value_combo.setVisible(has_presets)
        self.preset_value_combo.setEnabled(has_presets and needs_value and can_edit)
        is_custom_preset = str(self.preset_value_combo.currentData() or "") == "__custom__"
        self.operation_value_input.setVisible(not has_presets or is_custom_preset)
        self.operation_value_input.setEnabled(needs_value and can_edit and (not has_presets or is_custom_preset))
        self.apply_button.setEnabled(
            self._paths_locked and self._access_mode == "edit" and can_edit
        )
        self._configure_value_input_validator(self.operation_value_input, field)
        if field is not None and field.value_type == "resolution":
            self.operation_value_input.setPlaceholderText("例: 1920×1080")
        elif field is not None and field.key == "review.score":
            self.operation_value_input.setPlaceholderText("0〜1（個別編集では1〜10ボタンも使用可）")
        elif field is not None and field.key == "review.community_score":
            self.operation_value_input.setPlaceholderText("口コミ評価を 0〜1 で入力")
        elif field is not None and field.key == "media.highlights":
            self.operation_value_input.setPlaceholderText("mpvの [ と ] で時間とコメントをセットで追加")
        elif field is not None and field.key == "source.uploader":
            self.operation_value_input.setPlaceholderText("投稿者名・別名義を1回に1件入力")
        elif field is not None and field.value_type in {"number", "duration_seconds"}:
            self.operation_value_input.setPlaceholderText("数値を入力")
        elif field is not None and field.allows_multiple:
            self.operation_value_input.setPlaceholderText("追加・上書きする値（1回に1項目）")
        else:
            self.operation_value_input.setPlaceholderText("上書き・追加する値")
        if folder_reason is not None:
            disabled_reason = f"編集不可: {folder_reason}"
            self.operation_kind_combo.setToolTip(disabled_reason)
            self.operation_value_input.setToolTip(disabled_reason)
            self.preset_value_combo.setToolTip(disabled_reason)
        else:
            self.operation_kind_combo.setToolTip(
                "上書きは既存値を置換、追加は既存値を残して追加、空欄化は値を空にし、"
                "項目削除はその項目自体を出力予定から外します。"
            )
            self.operation_value_input.setToolTip(
                "上書きまたは追加する値を入力します。項目ごとの形式はプレースホルダーで確認できます。"
            )
            self.preset_value_combo.setToolTip(
                "選択肢がある項目では、ここから値を選べます。例外だけ自由入力を使います。"
            )
        self._show_current_operation_state()

    @staticmethod
    def _configure_value_input_validator(value_input: QLineEdit, field) -> None:  # type: ignore[no-untyped-def]
        """Reject clearly invalid typed values before the explicit apply check."""
        if field is None or field.value_type == "text":
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

    def _checked_item_indexes(self) -> list[int]:
        selected_tokens = set(self.path_input.selected_item_tokens(deduplicate=True))
        return [
            index for index, item in enumerate(self._items)
            if media_item_token(item) in selected_tokens
        ]

    def _selected_folder_field_reason(self, key: str) -> str | None:
        """Return the first incompatibility among the current operation targets."""
        if not key:
            return None
        for index in self._operation_target_indexes():
            if index >= len(self._source_items):
                continue
            reason = folder_field_uneditable_reason(self._source_items[index], key)
            if reason is not None:
                return reason
        return None

    def _show_current_operation_state(self, *_unused: object) -> None:
        """Refresh only the compact previews needed while the user interacts."""
        key = self._selected_operation_key(allow_blank=True)
        operation = str(self.operation_kind_combo.currentData() or "")
        has_required_value = operation not in {"replace", "append"} or bool(self._operation_value_text().strip())
        planned_items = list(self._items)
        preview_index = self._individual_operation_target_index()
        if key and has_required_value and preview_index is not None:
            try:
                planned_items[preview_index] = apply_catalog_field_operation(
                    planned_items[preview_index],
                    key=key,
                    operation=operation,
                    value=self._operation_value_text(),
                )
            except ValueError:
                # Keep the working output visible until valid input is supplied.
                pass
        self._update_selected_field_values(key)
        self._update_operation_draft_preview(key, operation, preview_index, planned_items)

    def _refresh_candidate_table(self) -> None:
        """Redraw all candidate facts after a real edit or initial load only.

        This intentionally does not run for ordinary row clicks, checkbox
        changes, or each typed character.  Those interactions refresh the
        compact preview above, while the table continues to show the actual
        in-memory output state.
        """
        values: dict[str, dict[str, str]] = {}
        change_kinds: dict[str, dict[str, str]] = {}
        for index, source_item in enumerate(self._source_items):
            planned = self._items[index] if index < len(self._items) else source_item
            file_kind = source_item.attribute_map.get("file.kind")
            is_folder = file_kind is not None and file_kind.value == "directory"
            source_label, _tooltip = self._candidate_source_details.get(
                media_item_token(source_item), _candidate_source_detail(source_item)
            )
            token = media_item_token(source_item)
            row_values = {
                "出所": source_label,
                "種別": (
                    "仮登録" if source_item.origin == "manual_placeholder"
                    else "JSON候補" if source_item.origin == "parts_json"
                    else "フォルダ" if is_folder else "ファイル"
                ),
                "サイズ": _catalog_value(source_item, "file.size_bytes.observed"),
                "ファイル数": _catalog_value(source_item, "folder.descendant_file_count"),
                "メディア数": _catalog_value(source_item, "folder.media_file_count"),
                "拡張子": _catalog_value(source_item, "file.extension.observed"),
                "解像度": _catalog_value(source_item, "video.resolution"),
                "タグ": _catalog_value(planned, "classification.tag"),
                # 評価は一覧上で素早く更新する操作があるため、ここだけは
                # 読み取り値ではなく今回の出力予定値を表示する。
                "評価": _catalog_value(planned, "review.score"),
                "見どころ": _catalog_value(planned, "media.highlights"),
            }
            session_notes = " / ".join(self._browse_session_notes.get(token, ()))
            if session_notes:
                existing_highlights = row_values["見どころ"]
                row_values["見どころ"] = " / ".join(
                    value
                    for value in (existing_highlights, f"今回のみ: {session_notes}")
                    if value
                )
            row_values.update(
                {
                    label: _catalog_value(planned, key)
                    for label, key in _MEDIA_ATTRIBUTE_COLUMNS
                    if label not in row_values
                }
            )
            row_values[_RAW_ATTRIBUTES_COLUMN] = self._raw_attribute_text(planned)
            values[token] = row_values
            marks = {
                label: kind
                for label, attribute_key in _MEDIA_ATTRIBUTE_COLUMNS
                if (kind := self._catalog_change_kind(source_item, planned, attribute_key))
            }
            raw_kind = self._raw_attribute_change_kind(source_item, planned)
            if raw_kind:
                marks[_RAW_ATTRIBUTES_COLUMN] = raw_kind
            change_kinds[token] = marks
        self.path_input.set_supplemental_values(values)
        self.path_input.set_supplemental_cell_change_kinds(change_kinds)

    @staticmethod
    def _raw_attribute_text(item: MediaItem) -> str:
        """Show unrecognised durable fields exactly enough to inspect safely."""
        standard_keys = {field.key for field in STANDARD_CATALOG_FIELDS}
        unknown = [
            attribute.as_dict()
            for attribute in catalog_attributes_from_media_item(item)
            if attribute.key not in standard_keys and attribute.value is not None
        ]
        return json.dumps(unknown, ensure_ascii=False, separators=(",", ":")) if unknown else ""

    @staticmethod
    def _catalog_change_kind(source: MediaItem, planned: MediaItem, key: str) -> str:
        """Classify a durable value delta without treating list edits as replacement."""
        before = MediaInformationScreen._catalog_raw_value(source, key)
        after = MediaInformationScreen._catalog_raw_value(planned, key)
        if _json_signature(before) == _json_signature(after):
            return ""
        if before is None:
            return "added"
        if after is None:
            return "removed"
        if isinstance(before, list) or isinstance(after, list):
            old_values = {_json_signature(value) for value in (before if isinstance(before, list) else [before])}
            new_values = {_json_signature(value) for value in (after if isinstance(after, list) else [after])}
            if old_values.issubset(new_values):
                return "added"
            if new_values.issubset(old_values):
                return "removed"
        return "changed"

    @staticmethod
    def _catalog_raw_value(item: MediaItem, key: str) -> object | None:
        return next(
            (
                attribute.value
                for attribute in catalog_attributes_from_media_item(item)
                if attribute.key == key
            ),
            None,
        )

    @staticmethod
    def _raw_attribute_change_kind(source: MediaItem, planned: MediaItem) -> str:
        standard_keys = {field.key for field in STANDARD_CATALOG_FIELDS}
        before = [
            attribute.as_dict()
            for attribute in catalog_attributes_from_media_item(source)
            if attribute.key not in standard_keys and attribute.value is not None
        ]
        after = [
            attribute.as_dict()
            for attribute in catalog_attributes_from_media_item(planned)
            if attribute.key not in standard_keys and attribute.value is not None
        ]
        if _json_signature(before) == _json_signature(after):
            return ""
        if not before:
            return "added"
        if not after:
            return "removed"
        return "changed"

    def _sort_candidates_by_file_name(self, *, ascending: bool) -> None:
        """Sort candidates by portable filename and retain that output order."""
        if (
            not self._paths_locked
            or self._access_mode != "edit"
            or not self._source_items
        ):
            return

        def sort_key(index: int) -> str:
            source = self._source_items[index]
            try:
                value = self._mpv_match_file_name(source)
            except ValueError:
                value = media_item_display_name(source)
            return value.casefold()

        order = sorted(range(len(self._source_items)), key=sort_key, reverse=not ascending)
        self._source_items = [self._source_items[index] for index in order]
        self._items = [self._items[index] for index in order]
        self.path_input.reorder_items(media_item_token(item) for item in self._source_items)
        self._invalidate_review_patch_merge()
        self._show_items()
        direction = "昇順" if ascending else "降順"
        self._notify(
            f"ファイル名で{direction}に並べ替えました。",
            "画面と次の新規保存の順序を更新しました。既存JSONは上書き保存を明示した場合だけ変わります。",
        )

    def _update_operation_draft_preview(
        self,
        key: str,
        operation: str,
        preview_index: int | None,
        planned_items: list[MediaItem],
    ) -> None:
        """Show the pending result for the one candidate selected in the list."""
        if not self._paths_locked or self._work_mode == "review_patch":
            self.operation_draft_preview.clear()
            return
        source_item = self._media_item_for_tree_row(self._individual_row)
        if source_item is None:
            self.operation_draft_preview.setText("一覧から候補を1件選んでください")
            return
        try:
            index = self._source_items.index(source_item)
        except ValueError:
            self.operation_draft_preview.clear()
            return
        if not key:
            self.operation_draft_preview.setText("項目を選択してください")
            return
        field = catalog_field_for_key(key)
        label = field.label if field is not None else key
        current_value = _catalog_value(self._items[index], key)
        if index != preview_index:
            self.operation_draft_preview.setText(f"操作対象外（{label}: {current_value}）")
            return
        value = self._operation_value_text().strip()
        if operation in {"replace", "append"} and not value:
            self.operation_draft_preview.setText("値を入力してください")
            return
        self.operation_draft_preview.setText(_catalog_value(planned_items[index], key))

    def _individual_operation_target_index(self) -> int | None:
        """Return only the clicked row's preview target without scanning all rows."""
        source_item = self._media_item_for_tree_row(self._individual_row)
        if source_item is None:
            return None
        try:
            index = self._source_items.index(source_item)
        except ValueError:
            return None
        target_kind = str(self.operation_target_combo.currentData() or "")
        if target_kind == "all" or target_kind == "selected":
            return index
        return index if self.path_input.item_is_selected(self._individual_row) else None

    def _select_individual_row(self, row: QTreeWidgetItem, _column: int) -> None:
        """Choose one frozen row as the target for the individual-editor dialog."""
        if not self._paths_locked or self._media_item_for_tree_row(row) is None:
            return
        self._individual_row = row
        self._show_current_operation_state()

    def _candidate_double_clicked(self, row: QTreeWidgetItem, column: int) -> None:
        """Select the row and start its validated video position in the playlist."""
        self._select_individual_row(row, column)
        item = self._media_item_for_tree_row(row)
        if (
            not self._paths_locked
            or item is None
            or self._mpv_path_for_item(item) is None
        ):
            return
        self.play_confirmed_items_from_mpv(item)

    def _update_selected_field_values(self, key: str) -> None:
        """Show one selected candidate's read-time and current planned values."""
        source_item = self._media_item_for_tree_row(self._individual_row)
        if not self._paths_locked or source_item is None:
            self.selected_field_target_label.setText(
                "一覧から1件をクリックすると、選択中項目の値をここに表示します。"
            )
            self.selected_field_target_label.setToolTip("")
            self.selected_field_source_value.clear()
            self.selected_field_planned_value.clear()
            self.selected_field_source_value.setToolTip("")
            self.selected_field_planned_value.setToolTip("")
            return
        try:
            item_index = self._source_items.index(source_item)
        except ValueError:
            self._individual_row = None
            self._update_selected_field_values(key)
            return
        display_name = media_item_display_name(source_item)
        if not key:
            self.selected_field_target_label.setText(
                f"対象: {display_name}　/　確認する項目を選択してください。"
            )
            self.selected_field_target_label.setToolTip(
                str(source_item.path) if source_item.path is not None else "仮登録（実在パスなし）"
            )
            self.selected_field_source_value.clear()
            self.selected_field_planned_value.clear()
            self.selected_field_source_value.setToolTip("")
            self.selected_field_planned_value.setToolTip("")
            return
        field = catalog_field_for_key(key)
        field_label = field.label if field is not None else key
        source_value = _catalog_value(source_item, key)
        planned_value = _catalog_value(self._items[item_index], key)
        self.selected_field_target_label.setText(
            f"対象: {display_name}　/　項目: {field_label}"
        )
        self.selected_field_target_label.setToolTip(
            str(source_item.path) if source_item.path is not None else "仮登録（実在パスなし）"
        )
        self.selected_field_source_value.setText(source_value or "（空欄）")
        self.selected_field_planned_value.setText(planned_value or "（空欄）")
        self.selected_field_source_value.setToolTip(source_value or "（空欄）")
        self.selected_field_planned_value.setToolTip(planned_value or "（空欄）")

    def _operation_target_indexes(self) -> list[int]:
        """Return the selected row, checked set, or all frozen candidates."""
        target_kind = str(self.operation_target_combo.currentData() or "")
        if target_kind == "all":
            return list(range(len(self._items)))
        if target_kind == "checked":
            return self._checked_item_indexes()
        source_item = self._media_item_for_tree_row(self._individual_row)
        if source_item is None:
            return []
        try:
            return [self._source_items.index(source_item)]
        except ValueError:
            self._individual_row = None
            return []

    def _operation_changes(self) -> list[tuple[int, MediaItem, MediaItem]] | None:
        if not self._paths_locked:
            self._notify("先に対象を確定してください。")
            return None
        indexes = self._operation_target_indexes()
        if not indexes:
            target_kind = str(self.operation_target_combo.currentData() or "")
            if target_kind == "selected":
                self._notify("一覧の行をクリックして、「現在の対象1件のみ」の対象を選んでください。")
            elif target_kind == "checked":
                self._notify("先にパス一覧で、チェック済みの対象を選んでください。")
            else:
                self._notify("一覧に操作できる候補がありません。")
            return None
        key = self._selected_operation_key()
        if not key:
            self._notify("対象項目を選択してください。独自項目なら項目名も入力してください。")
            return None
        for index in indexes:
            if index < len(self._source_items):
                folder_reason = folder_field_uneditable_reason(self._source_items[index], key)
                if folder_reason is not None:
                    self._notify("対象のフォルダではこの項目を編集できません。", folder_reason)
                    return None
        operation = str(self.operation_kind_combo.currentData() or "")
        try:
            return [
                (
                    index,
                    self._items[index],
                    apply_catalog_field_operation(
                        self._items[index],
                        key=key,
                        operation=operation,
                        value=self._operation_value_text(),
                    ),
                )
                for index in indexes
            ]
        except ValueError as exc:
            self._notify("共通操作を確認できません。", str(exc))
            return None

    def apply_common_operation(self) -> None:
        if self._access_mode != "edit":
            self._notify("閲覧モードでは項目を編集できません。")
            return
        if self._work_mode == "review_patch":
            self._notify("MPV評価・見どころパッチ作成では通常の共通編集は使えません。")
            return
        changes = self._operation_changes()
        if changes is None:
            return
        self._remember_edit_for_undo("項目操作の反映")
        for index, _before, after in changes:
            self._items[index] = after
        self._invalidate_review_patch_merge()
        target_scope = str(self.operation_target_combo.currentData() or "selected")
        item_name = ""
        if target_scope == "selected" and changes:
            item_name = media_item_display_name(self._source_items[changes[0][0]])
        applied = _AppliedOperation(
            key=self._selected_operation_key(),
            operation=str(self.operation_kind_combo.currentData() or ""),
            value=self._operation_value_text().strip(),
            item_count=len(changes),
            scope="individual" if target_scope == "selected" else "common",
            item_name=item_name,
        )
        self._applied_operations.append(applied)
        self._applied_operation_summaries.append(self._applied_operation_summary(applied))
        self.operation_value_input.clear()
        self.preset_value_combo.setCurrentIndex(0)
        self._show_items()
        self._show_current_operation_state()
        self._notify(
            (
                f"{item_name} の出力予定へ操作を反映しました。"
                if target_scope == "selected"
                else f"一覧全体{len(changes)}件へ操作を反映しました。"
                if target_scope == "all"
                else f"チェック済み{len(changes)}件へ操作を反映しました。"
            ),
            "実ファイルは変更していません。JSONへ保存するには「実際に出力」を押してください。",
        )

    def _applied_operation_summary(self, applied: _AppliedOperation) -> str:
        field = catalog_field_for_key(applied.key)
        field_text = f"{field.label}（{applied.key}）" if field is not None else applied.key
        operation_text = _operation_label(applied.operation)
        value = applied.value
        value_text = f"「{value}」" if value and operation_text in {"値を上書き", "値を追加"} else ""
        return f"{applied.item_count}件: {field_text}を{operation_text}{value_text}"

    def show_edit_content_confirmation(self) -> None:
        """Show the complete field-by-field plan without writing anything."""
        dialog = QDialog(self)
        dialog.setWindowTitle("編集内容確認（まだ出力しません）")
        dialog.setMinimumSize(760, 680)
        dialog_layout = QVBoxLayout(dialog)
        dialog_layout.addWidget(QLabel("反映済みの操作と、対象JSONへ出る項目を確認できます。"))
        report = QPlainTextEdit()
        report.setReadOnly(True)
        report.setPlainText(self._edit_content_confirmation_text())
        dialog_layout.addWidget(report, 1)
        close_button = QPushButton("閉じる")
        close_button.clicked.connect(dialog.accept)
        dialog_layout.addWidget(close_button)
        dialog.exec()

    def show_planned_output_preview(self) -> None:
        """Show the exact current attribute plan for checked JSON output items."""
        indexes = self._checked_item_indexes()
        if not indexes:
            self._notify("出力予定を確認するには、一覧で対象へチェックしてください。")
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("出力予定の確認（まだ保存しません）")
        dialog.setMinimumSize(760, 680)
        dialog_layout = QVBoxLayout(dialog)
        dialog_layout.addWidget(
            QLabel("チェック済みの候補について、現時点でJSONへ出る項目と値を表示します。")
        )
        report = QPlainTextEdit()
        report.setReadOnly(True)
        lines = [f"出力予定: {len(indexes)}件（実ファイル・JSONはまだ変更しません）"]
        for number, index in enumerate(indexes, start=1):
            item = self._items[index]
            record = catalog_record_from_media_item(item)
            lines.extend(("", f"{number}. {media_item_display_name(item)}"))
            lines.extend(
                f"  {attribute.key}: {display_catalog_attribute(attribute)}"
                for attribute in record.attributes
            )
        report.setPlainText("\n".join(lines))
        dialog_layout.addWidget(report, 1)
        close_button = QPushButton("閉じる")
        close_button.clicked.connect(dialog.accept)
        dialog_layout.addWidget(close_button)
        dialog.exec()

    def _edit_content_confirmation_text(self) -> str:
        lines = ["反映済みの操作"]
        individual = [entry for entry in self._applied_operations if entry.scope == "individual"]
        common = [entry for entry in self._applied_operations if entry.scope == "common"]
        if not individual and not common:
            lines.append("反映済みの編集はありません。")
        if individual:
            lines.extend(("", f"個別操作（{len(individual)}回）"))
            lines.extend(
                f"{index}. {entry.item_name}: {self._applied_operation_summary(entry)}"
                for index, entry in enumerate(individual, start=1)
            )
        if common:
            lines.extend(("", f"共通操作（{len(common)}回）"))
            lines.extend(
                f"{index}. {self._applied_operation_summary(entry)}"
                for index, entry in enumerate(common, start=1)
            )

        changed_standard = {
            entry.key for entry in self._applied_operations if catalog_field_for_key(entry.key) is not None
        }
        unchanged_count = len(STANDARD_CATALOG_FIELDS) - len(changed_standard)
        lines.extend(("", f"基本項目（最大 {len(STANDARD_CATALOG_FIELDS)}項目・値があるものだけを出力）"))
        lines.append(f"変更なし: {unchanged_count}項目。取得できた値だけをそのまま出力します。")
        if changed_standard:
            lines.append("変更あり:")
            for field in STANDARD_CATALOG_FIELDS:
                operations = [entry for entry in self._applied_operations if entry.key == field.key]
                if not operations:
                    continue
                description = " → ".join(_operation_description(entry) for entry in operations)
                lines.append(f"- {field.label}: {description}")

        custom_keys: list[str] = []
        for entry in self._applied_operations:
            if catalog_field_for_key(entry.key) is None and entry.key not in custom_keys:
                custom_keys.append(entry.key)
        if custom_keys:
            lines.extend(("", "追加・独自項目"))
            for number, key in enumerate(custom_keys, start=len(STANDARD_CATALOG_FIELDS) + 1):
                operations = [entry for entry in self._applied_operations if entry.key == key]
                description = " → ".join(_operation_description(entry) for entry in operations)
                lines.append(f"{number}. {key}: {description}")
        return "\n".join(lines)

    def request_parts_output(self) -> None:
        """Always create a new, ID-free parts snapshot from checked candidates."""
        if self._access_mode != "edit":
            self._notify("閲覧モードではJSONを保存できません。")
            return
        if self._work_mode == "review_patch":
            self._notify("MPV評価・見どころパッチ作成ではパーツJSONを保存できません。評価パッチを出力してください。")
            return
        items = [self._items[index] for index in self._checked_item_indexes()]
        if not items:
            self._notify("先に一覧で、パーツJSONへ保存する項目へチェックしてください。")
            return
        try:
            output = self._new_parts_output_path()
        except ValueError as exc:
            self._notify("パーツJSONの保存先を確認してください。", str(exc))
            return
        document = MediaPartsDocument(
            tuple(
                MediaPart(tuple(catalog_record_from_media_item(item).attributes))
                for item in items
            )
        )
        dialog = QDialog(self)
        dialog.setWindowTitle("パーツJSONの新規保存確認")
        dialog.setMinimumSize(620, 270)
        layout = QVBoxLayout(dialog)
        report = QPlainTextEdit()
        report.setReadOnly(True)
        report.setPlainText(
            "\n".join(
                (
                    "次の候補を、IDを持たないパーツJSONとして新規保存します。",
                    "",
                    f"保存先: {output}",
                    f"保存する候補: {len(document.parts)} 件",
                    "",
                    "実ファイル・入力元JSON・既存JSONは変更しません。",
                    "作品ID・ファイルIDは発行しません。後で本台帳へ明示的に取り込めます。",
                )
            )
        )
        layout.addWidget(report, 1)
        buttons = QDialogButtonBox()
        buttons.addButton("戻る", QDialogButtonBox.ButtonRole.RejectRole).clicked.connect(dialog.reject)
        buttons.addButton("この内容を新規保存", QDialogButtonBox.ButtonRole.AcceptRole).clicked.connect(dialog.accept)
        layout.addWidget(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            saved = create_parts(output, document)
        except ValueError as exc:
            self._notify("パーツJSONを保存できません。", str(exc))
            return
        self._notify(
            f"パーツJSONを新規保存しました: {saved}",
            f"候補 {len(document.parts)} 件。実パス・編集履歴・IDは保存していません。",
        )

    def _new_parts_output_path(self) -> Path:
        """Resolve a destination which can never replace an existing file."""
        output = resolve_new_parts_path(self.export_path_input.text())
        return unique_json_path(output) if output.exists() else output

    def request_parts_overwrite(self) -> None:
        """Overwrite only the sole supported JSON explicitly loaded this session."""
        if self._access_mode != "edit":
            self._notify("閲覧モードではJSONを上書きできません。")
            return
        output, reason = self._parts_overwrite_target()
        if output is None:
            self._notify("パーツJSONを上書きできません。", reason)
            self._update_parts_overwrite_button()
            return
        items = [self._items[index] for index in self._checked_item_indexes()]
        if not items:
            self._notify("上書きする候補を1件以上チェックしてください。")
            return
        document = MediaPartsDocument(
            tuple(
                MediaPart(tuple(catalog_record_from_media_item(item).attributes))
                for item in items
            )
        )
        try:
            existing = load_parts(output)
        except ValueError as exc:
            self._notify("読み込んだパーツJSONを確認してください。", str(exc))
            return
        safe, report = overwrite_safety_report(
            output=output,
            existing_names=part_file_name_sets(existing.parts),
            current_names=part_file_name_sets(document.parts),
            existing_attribute_count=sum(len(part.attributes) for part in existing.parts),
            current_attribute_count=sum(len(part.attributes) for part in document.parts),
            label=(
                "今回読み込んだ単一のパーツJSON"
                if not existing.converted_from_extraction_catalog
                else "今回読み込んだ単一JSON（パーツJSON形式へ変換して保存）"
            ),
        )
        if not safe or not self._confirm_overwrite(report, title="読み込んだパーツJSONの上書き確認"):
            return
        try:
            save_parts(output, document)
        except ValueError as exc:
            self._notify("パーツJSONを上書きできません。", str(exc))
            return
        self._notify(
            f"読み込んだパーツJSONを上書き保存しました: {output}",
            f"候補 {len(document.parts)} 件。実ファイルは変更していません。",
        )

    def _confirm_overwrite(self, report_text: str, *, title: str) -> bool:
        dialog = QDialog(self)
        dialog.setWindowTitle(title)
        dialog.setMinimumSize(700, 390)
        layout = QVBoxLayout(dialog)
        report = QPlainTextEdit()
        report.setReadOnly(True)
        report.setPlainText(report_text)
        layout.addWidget(report, 1)
        buttons = QDialogButtonBox()
        buttons.addButton("戻る", QDialogButtonBox.ButtonRole.RejectRole).clicked.connect(dialog.reject)
        buttons.addButton("このJSON全体を上書き", QDialogButtonBox.ButtonRole.DestructiveRole).clicked.connect(dialog.accept)
        layout.addWidget(buttons)
        return dialog.exec() == QDialog.DialogCode.Accepted

    def open_single_field_patch_workspace(self) -> None:
        """Open the reusable text workspace and save its one-field result separately."""
        if self._access_mode != "edit":
            self._notify("閲覧モードでは項目更新パッチを作成できません。")
            return
        if self._work_mode == "review_patch" or not self._paths_locked:
            self._notify("項目更新パッチは、通常モードで候補を確定してから作れます。")
            return
        try:
            rows = self._text_workspace_rows()
        except ValueError as exc:
            self._notify("項目更新パッチの作業場を開けません。", str(exc))
            return
        sources = self._text_workspace_sources()
        targets = tuple(
            TextWorkspaceTarget(field.key, field.label)
            for field in STANDARD_CATALOG_FIELDS
            if field.key in PATCHABLE_TEXT_FIELD_KEYS
        )
        dialog = TextValueWorkspaceDialog(
            title="項目更新パッチを作る",
            rows=rows,
            sources=sources,
            targets=targets,
            default_source_key="__file_name_stem__",
            default_target_key="title.official",
            parent=self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted or dialog.result is None:
            return
        try:
            patch = MediaSingleFieldPatch(
                dialog.result.target_key,
                tuple(single_field_patch_entry(entry.identifier, entry.value) for entry in dialog.result.entries),
            )
            self._request_single_field_patch_save(patch)
        except ValueError as exc:
            self._notify("項目更新パッチを作成できません。", str(exc))

    def _text_workspace_sources(self) -> tuple[TextWorkspaceSource, ...]:
        """Offer filename first, then the same text fields available to media editing."""
        sources = [
            TextWorkspaceSource("__file_name_stem__", "ファイル名（拡張子なし）"),
            TextWorkspaceSource("__file_name_full__", "ファイル名（拡張子あり）"),
        ]
        for field in STANDARD_CATALOG_FIELDS:
            if field.value_type != "text" or field.source_key is None:
                continue
            sources.append(TextWorkspaceSource(field.key, field.label))
        return tuple(sources)

    def _text_workspace_rows(self) -> tuple[TextWorkspaceRow, ...]:
        """Adapt current candidates to the generic, storage-agnostic text rows."""
        return text_workspace_rows(self._items)

    def _request_single_field_patch_save(self, patch: MediaSingleFieldPatch) -> None:
        """Ask for an explicit fresh destination after text values are confirmed."""
        if self._access_mode != "edit":
            self._notify("閲覧モードでは項目更新パッチを保存できません。")
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("項目更新パッチの保存先")
        dialog.setMinimumSize(680, 260)
        layout = QVBoxLayout(dialog)
        field = catalog_field_for_key(patch.field_key)
        field_label = field.label if field is not None else patch.field_key
        layout.addWidget(
            QLabel(
                f"更新する項目: {field_label}\n"
                f"更新する候補: {len(patch.entries)} 件\n\n"
                "保存するのは照合用ファイル名、更新する項目、置換する文字列だけです。"
                "元の候補・JSON・実ファイルは変更しません。"
            )
        )
        path_input = PathLineInput(drop_transform=self._output_path_from_drop)
        path_input.setPlaceholderText("フォルダ、または新しい media_single_field_patch の .json ファイル")
        previous_output = self.export_path_input.text().strip()
        if previous_output:
            previous_path = Path(previous_output).expanduser()
            path_input.setText(str(previous_path.parent if previous_path.suffix else previous_path))
        layout.addWidget(path_input)
        buttons = QDialogButtonBox()
        buttons.addButton("戻る", QDialogButtonBox.ButtonRole.RejectRole).clicked.connect(dialog.reject)
        buttons.addButton("この内容で保存", QDialogButtonBox.ButtonRole.AcceptRole).clicked.connect(dialog.accept)
        layout.addWidget(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        path = path_input.path()
        if path is None:
            self._notify("項目更新パッチの保存先を入力してください。")
            return
        try:
            output = resolve_new_single_field_patch_path(path)
            saved = save_single_field_patch(output, patch)
        except ValueError as exc:
            self._notify("項目更新パッチを保存できません。", str(exc))
            return
        self._notify(
            f"項目更新パッチを保存しました: {saved}",
            f"{field_label}を {len(patch.entries)} 件だけ置き換えるパッチです。"
            "元の候補・JSON・実ファイルは変更していません。",
        )

    def request_single_field_patch_merge(self) -> None:
        """Load one one-field patch, show its complete preflight, then merge in memory."""
        if self._access_mode != "edit":
            self._notify("閲覧モードでは項目更新パッチを結合できません。")
            return
        if self._work_mode == "review_patch" or not self._paths_locked:
            self._notify("項目更新パッチの結合は、通常モードで候補を確定してから使えます。")
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("項目更新パッチを結合")
        dialog.setMinimumSize(700, 220)
        layout = QVBoxLayout(dialog)
        layout.addWidget(
            QLabel(
                "正式タイトルなど1項目だけを更新するパッチJSONを指定します。"
                "全件をファイル名で一意照合し、不足・重複・曖昧さが1件でもあれば何も変更しません。"
            )
        )
        path_input = PathLineInput(drop_transform=self._output_path_from_drop)
        path_input.setPlaceholderText("項目更新パッチの .json ファイル")
        layout.addWidget(path_input)
        buttons = QDialogButtonBox()
        buttons.addButton("戻る", QDialogButtonBox.ButtonRole.RejectRole).clicked.connect(dialog.reject)
        buttons.addButton("照合結果を確認", QDialogButtonBox.ButtonRole.AcceptRole).clicked.connect(dialog.accept)
        layout.addWidget(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        path = path_input.path()
        if path is None:
            self._notify("項目更新パッチJSONを指定してください。")
            return
        self._check_and_confirm_single_field_patch_merge(path)

    def _check_and_confirm_single_field_patch_merge(self, path: Path) -> None:
        try:
            patch = load_single_field_patch(path)
            plan = plan_single_field_patch_merge(self._parts_for_current_items(), patch)
        except ValueError as exc:
            self._notify("項目更新パッチを照合できません。", str(exc))
            return
        self._single_field_patch = patch
        self._single_field_merge_plan = plan
        self._single_field_merge_items_snapshot = tuple(self._items)
        field = catalog_field_for_key(patch.field_key)
        field_label = field.label if field is not None else patch.field_key
        dialog = QDialog(self)
        dialog.setWindowTitle("項目更新パッチの照合結果")
        dialog.setMinimumSize(700, 390)
        layout = QVBoxLayout(dialog)
        report = QPlainTextEdit()
        report.setReadOnly(True)
        report.setPlainText(
            f"パッチ: {path}\n\n{single_field_patch_merge_summary(plan, patch)}\n\n"
            "結合しても画面内の出力予定だけが変わります。"
            "元のパーツJSON・項目更新パッチ・実ファイルは変更しません。"
        )
        layout.addWidget(report, 1)
        buttons = QDialogButtonBox()
        buttons.addButton("戻る", QDialogButtonBox.ButtonRole.RejectRole).clicked.connect(dialog.reject)
        apply_button = buttons.addButton(
            f"{field_label}を結合", QDialogButtonBox.ButtonRole.AcceptRole
        )
        apply_button.setEnabled(plan.is_safe)
        apply_button.clicked.connect(dialog.accept)
        layout.addWidget(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._apply_verified_single_field_patch_merge()

    def _apply_verified_single_field_patch_merge(self) -> None:
        if self._access_mode != "edit":
            self._notify("閲覧モードでは項目更新パッチを結合できません。")
            return
        patch = self._single_field_patch
        plan = self._single_field_merge_plan
        if patch is None or plan is None or self._single_field_merge_items_snapshot != tuple(self._items):
            self._notify("候補または編集内容が変わりました。現在の状態で、もう一度パッチを照合してください。")
            return
        try:
            # The media-level operation proves that the saved patch and the
            # target parts still match.  Apply through the normal item editor
            # below as well, so field-specific validation stays identical to
            # direct editing (for example category-tree normalization).
            apply_single_field_patch_merge(self._parts_for_current_items(), patch, plan)
            result = list(self._items)
            for match in plan.matches:
                entry = patch.entries[match.patch_index]
                result[match.part_index] = apply_catalog_field_operation(
                    result[match.part_index],
                    key=patch.field_key,
                    operation="replace",
                    value=entry.value,
                )
        except ValueError as exc:
            self._notify("項目更新パッチを結合していません。", str(exc))
            return
        field = catalog_field_for_key(patch.field_key)
        field_label = field.label if field is not None else patch.field_key
        match_count = len(plan.matches)
        self._remember_edit_for_undo(f"項目更新パッチの結合（{field_label}）")
        self._items = result
        self._applied_operations.append(
            _AppliedOperation(
                key=patch.field_key,
                operation="single_field_patch",
                value="",
                item_count=match_count,
                scope="common",
            )
        )
        self._applied_operation_summaries.append(
            f"項目更新パッチを{match_count}件へ安全に移植（{field_label}）"
        )
        self._invalidate_review_patch_merge()
        self._show_items()
        self._notify(
            f"{field_label}を {match_count} 件の候補へ移植しました。",
            "画面内の出力予定だけを更新しました。保存するには「パーツJSONを新規保存」または、条件を満たす専用上書きボタンを押してください。",
        )

    def request_review_patch_merge(self) -> None:
        """Read one patch, prove a one-to-one match, then offer in-memory merge."""
        if self._access_mode != "edit":
            self._notify("閲覧モードでは評価・見どころパッチを結合できません。")
            return
        if self._work_mode == "review_patch" or not self._paths_locked:
            self._notify("評価・見どころパッチの結合は、通常モードで候補を確定してから使えます。")
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("評価・見どころパッチを結合")
        dialog.setMinimumSize(700, 210)
        layout = QVBoxLayout(dialog)
        layout.addWidget(
            QLabel(
                "MPV評価・見どころパッチJSONを指定します。ファイル名だけで全件を一意照合し、"
                "不足・重複・曖昧さが1件でもあれば何も変更しません。"
            )
        )
        path_input = PathLineInput(drop_transform=self._output_path_from_drop)
        path_input.setPlaceholderText("評価・見どころパッチの .json ファイル")
        layout.addWidget(path_input)
        buttons = QDialogButtonBox()
        buttons.addButton("戻る", QDialogButtonBox.ButtonRole.RejectRole).clicked.connect(dialog.reject)
        buttons.addButton("照合結果を確認", QDialogButtonBox.ButtonRole.AcceptRole).clicked.connect(dialog.accept)
        layout.addWidget(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        path = path_input.path()
        if path is None:
            self._notify("評価・見どころパッチJSONを指定してください。")
            return
        self._check_and_confirm_review_patch_merge(path)

    def _check_and_confirm_review_patch_merge(self, path: Path) -> None:
        """Display the full preflight, and apply only a verified patch plan."""
        try:
            patch = load_review_patch(path)
            parts = self._parts_for_current_items()
            plan = plan_review_patch_merge(parts, patch)
        except ValueError as exc:
            self._notify("評価・見どころパッチを照合できません。", str(exc))
            return
        self._review_patch = patch
        self._review_merge_plan = plan
        self._review_merge_items_snapshot = tuple(self._items)
        self._review_patch_report_text = review_patch_merge_summary(plan, patch)
        dialog = QDialog(self)
        dialog.setWindowTitle("評価・見どころパッチの照合結果")
        dialog.setMinimumSize(700, 380)
        layout = QVBoxLayout(dialog)
        report = QPlainTextEdit()
        report.setReadOnly(True)
        report.setPlainText(
            f"パッチ: {path}\n\n{self._review_patch_report_text}\n\n"
            "結合しても画面内の出力予定だけが変わります。元のパーツJSON・評価パッチ・実ファイルは変更しません。"
        )
        layout.addWidget(report, 1)
        buttons = QDialogButtonBox()
        buttons.addButton("戻る", QDialogButtonBox.ButtonRole.RejectRole).clicked.connect(dialog.reject)
        apply_button = buttons.addButton("評価・見どころを結合", QDialogButtonBox.ButtonRole.AcceptRole)
        apply_button.setEnabled(plan.is_safe)
        apply_button.clicked.connect(dialog.accept)
        layout.addWidget(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._apply_verified_review_patch_merge()

    def _parts_for_current_items(self) -> tuple[MediaPart, ...]:
        """Build pathless matching payloads without retaining source paths."""
        return tuple(
            MediaPart(tuple(catalog_record_from_media_item(item).attributes))
            for item in self._items
        )

    def _apply_verified_review_patch_merge(self) -> None:
        if self._access_mode != "edit":
            self._notify("閲覧モードでは評価・見どころパッチを結合できません。")
            return
        patch = self._review_patch
        plan = self._review_merge_plan
        if patch is None or plan is None or self._review_merge_items_snapshot != tuple(self._items):
            self._notify("候補または編集内容が変わりました。現在の状態で、もう一度パッチを照合してください。")
            return
        try:
            merged_parts = apply_review_patch_merge(self._parts_for_current_items(), patch, plan)
        except ValueError as exc:
            self._notify("評価・見どころパッチを結合していません。", str(exc))
            return
        result = list(self._items)
        for match in plan.matches:
            updated = result[match.part_index]
            attributes = {
                attribute.key: attribute
                for attribute in merged_parts[match.part_index].attributes
                if attribute.key in {"review.score", "classification.tag", "media.highlights"}
            }
            score = attributes.get("review.score")
            if score is not None:
                updated = apply_catalog_field_operation(
                    updated, key="review.score", operation="replace", value=str(score.value)
                )
            highlights = attributes.get("media.highlights")
            if highlights is not None:
                updated = replace_media_highlights(updated, highlights.value)
            tags = attributes.get("classification.tag")
            if tags is not None:
                updated = apply_catalog_field_operation(
                    updated, key="classification.tag", operation="clear"
                )
                values = tags.value if isinstance(tags.value, list) else [tags.value]
                for value in values:
                    updated = apply_catalog_field_operation(
                        updated, key="classification.tag", operation="append", value=str(value)
                    )
            result[match.part_index] = updated
        self._remember_edit_for_undo("評価・見どころパッチの結合")
        self._items = result
        self._applied_operations.append(
            _AppliedOperation(
                key="評価・見どころパッチ",
                operation="patch",
                value="",
                item_count=len(plan.matches),
                scope="common",
            )
        )
        self._applied_operation_summaries.append(
            f"評価・見どころパッチを{len(plan.matches)}件へ安全に移植"
        )
        self._review_patch = None
        self._review_merge_plan = None
        self._review_merge_items_snapshot = None
        self._review_patch_report_text = (
            f"評価・見どころを {len(plan.matches)} 件の候補へ画面内で移植しました。\n"
            "元のパーツJSON・評価パッチJSON・実ファイルは変更していません。"
        )
        self._show_items()
        self._notify(
            f"評価・見どころを {len(plan.matches)} 件の候補へ移植しました。",
            "画面内の出力予定だけを更新しました。保存するには「パーツJSONを新規保存」または、条件を満たす専用上書きボタンを押してください。",
        )

    def request_review_patch_output(self) -> None:
        """Explicitly write only MPV-created facts and file-name match keys."""
        if self._access_mode != "edit":
            self._notify("閲覧モードでは評価・見どころパッチを保存できません。")
            return
        if self._work_mode != "review_patch":
            self._notify("評価・見どころパッチは、専用作業モードでだけ出力できます。")
            return
        if not self._paths_locked:
            self._notify("先に評価対象の通常ファイルを確定してください。")
            return
        try:
            output = self._review_patch_output_path()
            patch = self._review_patch_for_locked_items()
        except ValueError as exc:
            self._notify("評価・見どころパッチを出力できません。", str(exc))
            return
        if output.exists():
            alternate = self._request_review_patch_overwrite_or_new(output, patch)
            if alternate is None:
                return
            output = alternate
        changed_entries = sum(1 for entry in patch.entries if entry.attributes)
        value_count = sum(len(entry.attributes) for entry in patch.entries)
        dialog = QDialog(self)
        dialog.setWindowTitle("評価・見どころパッチの出力確認")
        dialog.setMinimumSize(640, 300)
        dialog_layout = QVBoxLayout(dialog)
        report = QPlainTextEdit()
        report.setReadOnly(True)
        report.setPlainText(
            "\n".join(
                (
                    "次の内容を別の評価・見どころパッチJSONへ出力します。",
                    "",
                    f"保存先: {output}",
                    f"評価・見どころのあるファイル: {len(patch.entries)} 件",
                    f"評価・見どころを書き込んだ項目: {changed_entries} 件 / 属性 {value_count} 件",
                    "",
                    "保存するのはファイル名、評価、見どころ時間、見どころメモだけです。",
                    "実パス、解像度、サイズ、通常の編集項目、実ファイルは変更しません。",
                )
            )
        )
        dialog_layout.addWidget(report, 1)
        buttons = QDialogButtonBox()
        buttons.addButton("戻る", QDialogButtonBox.ButtonRole.RejectRole).clicked.connect(dialog.reject)
        buttons.addButton("この内容で出力", QDialogButtonBox.ButtonRole.AcceptRole).clicked.connect(dialog.accept)
        dialog_layout.addWidget(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            saved = save_review_patch(output, patch)
        except ValueError as exc:
            self._notify("評価・見どころパッチを保存できません。", str(exc))
            return
        # Keep the user's directory choice in the input.  The actual unique
        # filename is reported below; replacing the input with it would make
        # the next export look like an accidental reuse of that file.
        self._review_patch_output_text = self.export_path_input.text()
        self._notify(
            f"評価・見どころパッチを保存しました: {saved}",
            f"評価・見どころのあるファイル {len(patch.entries)} 件。実パスは保存していません。",
        )

    def _review_patch_output_path(self) -> Path:
        return resolve_new_review_patch_path(self.export_path_input.text())

    def _request_new_review_patch_path(self, existing: Path) -> Path | None:
        """Review patches are immutable output snapshots, never overwrite files."""
        dialog = QDialog(self)
        dialog.setWindowTitle("既存の評価・見どころパッチがあります")
        dialog.setMinimumSize(580, 220)
        layout = QVBoxLayout(dialog)
        message = QLabel(
            "指定した .json は既に存在します。評価・見どころパッチは後で照合して使う独立した記録のため、"
            "ここでは既存ファイルを上書き・自動結合しません。\n\n"
            f"既存: {existing}\n\n"
            "別名の新規JSONを作りますか？"
        )
        message.setWordWrap(True)
        layout.addWidget(message, 1)
        buttons = QDialogButtonBox()
        buttons.addButton("別名で新規作成", QDialogButtonBox.ButtonRole.AcceptRole).clicked.connect(dialog.accept)
        buttons.addButton("中止", QDialogButtonBox.ButtonRole.RejectRole).clicked.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        return unique_json_path(existing)

    def _request_review_patch_overwrite_or_new(
        self, output: Path, patch: MediaReviewPatch
    ) -> Path | None:
        try:
            existing = load_review_patch(output)
        except ValueError as exc:
            self._notify("既存の評価・見どころパッチを確認してください。", str(exc))
            return None
        dialog = QDialog(self)
        dialog.setWindowTitle("既存の評価・見どころパッチがあります")
        dialog.setMinimumSize(620, 240)
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel(f"保存先: {output}\n\n保存方法を明示的に選んでください。"))
        buttons = QDialogButtonBox()
        new_button = buttons.addButton("別名で新規保存", QDialogButtonBox.ButtonRole.ActionRole)
        overwrite_button = buttons.addButton("安全確認して全体を上書き…", QDialogButtonBox.ButtonRole.ActionRole)
        buttons.addButton("中止", QDialogButtonBox.ButtonRole.RejectRole).clicked.connect(dialog.reject)
        layout.addWidget(buttons)
        choice: dict[str, str] = {}
        new_button.clicked.connect(lambda: choice.__setitem__("mode", "new"))
        overwrite_button.clicked.connect(lambda: choice.__setitem__("mode", "overwrite"))
        new_button.clicked.connect(dialog.accept)
        overwrite_button.clicked.connect(dialog.accept)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        if choice.get("mode") == "new":
            return unique_json_path(output)
        existing_names = tuple(frozenset(entry.file_names) for entry in existing.entries)
        current_names = tuple(frozenset(entry.file_names) for entry in patch.entries)
        safe, report = overwrite_safety_report(
            output=output,
            existing_names=existing_names,
            current_names=current_names,
            existing_attribute_count=sum(len(entry.attributes) for entry in existing.entries),
            current_attribute_count=sum(len(entry.attributes) for entry in patch.entries),
            label="評価・見どころパッチ",
        )
        if not safe or not self._confirm_overwrite(report, title="評価・見どころパッチの上書き確認"):
            return None
        try:
            replace_review_patch(output, patch)
        except ValueError as exc:
            self._notify("評価・見どころパッチを上書きできません。", str(exc))
            return None
        self._notify(
            f"評価・見どころパッチを上書き保存しました: {output}",
            f"評価・見どころのあるファイル {len(patch.entries)} 件。実パスは保存していません。",
        )
        return None

    def _review_patch_for_locked_items(self) -> MediaReviewPatch:
        entries = []
        names: set[str] = set()
        for source_item, planned_item in zip(self._source_items, self._items, strict=True):
            file_name = self._mpv_match_file_name(source_item)
            entry = review_patch_entry(
                file_name,
                catalog_attributes_from_media_item(planned_item),
            )
            # A patch is a review delta, not an inventory of every file that
            # happened to be in the player playlist.  Empty entries neither
            # change a target nor need a later filename association.
            if not entry.attributes:
                continue
            if self._mpv_path_for_item(source_item) is None:
                raise ValueError(f"MPV評価パッチ用の動画が未紐付けです: {file_name}")
            if file_name in names:
                raise ValueError(f"同じファイル名が重複しています: {file_name}")
            names.add(file_name)
            entries.append(entry)
        return MediaReviewPatch(tuple(entries))

    @staticmethod
    def _mpv_match_file_name(item: MediaItem) -> str:
        """Return the one portable filename allowed as an MPV link identity."""
        values = [
            str(value).strip()
            for attribute in catalog_attributes_from_media_item(item)
            if attribute.key == "file.name.observed"
            for value in (attribute.value if isinstance(attribute.value, list) else [attribute.value])
            if isinstance(value, str) and value.strip() and Path(value).name == value.strip()
        ]
        unique = tuple(dict.fromkeys(values))
        if len(unique) != 1:
            raise ValueError(
                f"mpv連携にはファイル名がちょうど1つ必要です: {media_item_display_name(item)}"
            )
        return unique[0]

    def _mpv_path_for_item(self, item: MediaItem) -> Path | None:
        linked = self._mpv_linked_paths.get(media_item_token(item))
        path = linked if linked is not None else item.path
        if path is None or not path.is_file():
            return None
        mime = item.attribute_map.get("file.mime_type")
        is_video = mime is not None and str(mime.value).casefold().startswith("video/")
        return path if is_video or is_video_path(path) else None

    @staticmethod
    def _exact_mpv_video_link_map(
        items: Iterable[MediaItem], video_paths: Iterable[Path]
    ) -> dict[str, Path]:
        """Link every supplied video to one unique candidate basename.

        Candidates which do not have a supplied video are deliberately allowed;
        the supplied video side is the complete side of this association.
        """
        candidates = tuple(items)
        names = tuple(MediaInformationScreen._mpv_match_file_name(item) for item in candidates)
        duplicate_names = sorted({name for name in names if names.count(name) > 1})
        if duplicate_names:
            raise ValueError("左側候補に同じファイル名があります: " + " / ".join(duplicate_names))
        videos = tuple(video_paths)
        if any(not path.is_file() for path in videos):
            raise ValueError("右側には存在する通常ファイルだけを指定してください。")
        video_names = tuple(path.name for path in videos)
        duplicate_videos = sorted({name for name in video_names if video_names.count(name) > 1})
        if duplicate_videos:
            raise ValueError("右側動画に同じファイル名があります: " + " / ".join(duplicate_videos))
        unmatched_videos = sorted(set(video_names) - set(names))
        if unmatched_videos:
            raise ValueError(
                "右側の実動画に、左側候補と一致しないファイル名があります: "
                + " / ".join(unmatched_videos)
            )
        videos_by_name = {path.name: path for path in videos}
        return {
            media_item_token(item): videos_by_name[name]
            for item, name in zip(candidates, names, strict=True)
            if name in videos_by_name
        }

    def open_mpv_video_linker(self) -> None:
        """Link checked pathless candidates to real videos by exact basename."""
        if not self._paths_locked:
            self._notify("mpv連携動画は、先に候補を確定してから紐付けてください。")
            return
        selected_tokens = set(self.path_input.selected_item_tokens(deduplicate=True))
        selected = [item for item in self._source_items if media_item_token(item) in selected_tokens]
        if not selected:
            self._notify("mpvへ紐付ける候補へチェックを入れてください。")
            return
        try:
            names = [self._mpv_match_file_name(item) for item in selected]
        except ValueError as exc:
            self._notify("mpv連携動画を紐付けできません。", str(exc))
            return
        duplicates = sorted({name for name in names if names.count(name) > 1})
        if duplicates:
            self._notify("左側候補に同じファイル名があります。一対一で紐付けできません。", " / ".join(duplicates))
            return

        dialog = QDialog(self)
        dialog.setWindowTitle("mpv連携動画をファイル名で紐付け")
        dialog.setMinimumSize(980, 520)
        layout = QVBoxLayout(dialog)
        explanation = QLabel(
            "左のチェック済み候補と、右へ追加する実動画をファイル名で完全一致照合します。"
            "左右とも同名重複は不可です。右側の実動画はすべて左側候補へ一致する必要がありますが、"
            "左側候補に対応する実動画がないことは許可します。"
        )
        explanation.setWordWrap(True)
        layout.addWidget(explanation)
        tables = QSplitter(Qt.Orientation.Horizontal, dialog)
        left = QTreeWidget(tables)
        left.setHeaderLabels(("候補のファイル名",))
        left.setRootIsDecorated(False)
        left.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        for name in names:
            left.addTopLevelItem(QTreeWidgetItem((name,)))
        right_box = QWidget(tables)
        right_layout = QVBoxLayout(right_box)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.addWidget(QLabel("実動画のパス（ドラッグ＆ドロップで追加できます）"))
        paths = PathListInput(rows=9, accepted_path_kind="all", drop_replaces=False)
        paths.setToolTip("紐付ける実動画だけを追加してください。フォルダは照合できません。")
        right_layout.addWidget(paths, 1)
        tables.addWidget(left)
        tables.addWidget(right_box)
        tables.setSizes((380, 600))
        layout.addWidget(tables, 1)
        notice = QLabel("右側へ紐付ける実動画を追加して「動画を照合して紐付け」を押してください。")
        notice.setWordWrap(True)
        layout.addWidget(notice)
        buttons = QDialogButtonBox(dialog)
        confirm = buttons.addButton("動画を照合して紐付け", QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton("中止", QDialogButtonBox.ButtonRole.RejectRole).clicked.connect(dialog.reject)
        layout.addWidget(buttons)

        def verify() -> None:
            try:
                links = self._exact_mpv_video_link_map(selected, paths.paths())
            except ValueError as exc:
                notice.setText(str(exc))
                return
            # A later checked subset may be linked independently.  Replace
            # only that subset while retaining prior session-only links.
            self._mpv_linked_paths = {
                token: path
                for token, path in self._mpv_linked_paths.items()
                if token not in links
            }
            self._mpv_linked_paths.update(links)
            self._refresh_mpv_link_display()
            unmatched_candidates = len(selected) - len(links)
            notice.setText(
                f"実動画 {len(links)} 件を今回だけmpvへ紐付けました。"
                + (
                    f"候補 {unmatched_candidates} 件は未紐付けのままです。"
                    if unmatched_candidates
                    else ""
                )
            )
            confirm.setEnabled(False)

        confirm.clicked.connect(verify)
        dialog.exec()

    def _refresh_mpv_link_display(self) -> None:
        for item in self._source_items:
            token = media_item_token(item)
            label = _candidate_display_name(item)
            linked = self._mpv_linked_paths.get(token)
            if linked is not None:
                label += "（ファイル連携済み）"
            self.path_input.set_item_display_text(token, label)
        self._update_browse_status()

    def open_item(self, item: MediaItem) -> None:
        """Ask the OS to open one inspected item without changing its catalog data."""
        path = item.path
        if path is None:
            self._notify("仮登録には実在パスがないため、標準アプリでは開けません。")
            return
        if not path.exists():
            self._notify("存在しないパスは開けません。")
            return
        if QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
            self._notify(f"標準アプリへ開く要求を送りました。\n{path}")
        else:
            self._notify(f"標準アプリへ開く要求を送れませんでした。\n{path}")

    def play_confirmed_items_with_mpv(self) -> None:
        """Launch currently visible linked videos as the explicit playlist."""
        if not self._paths_locked:
            self._notify("mpv連携は、先に対象を確定してから使えます。")
            return
        visible_items = self._visible_source_items()
        all_paths = tuple(
            path
            for item in visible_items
            if (path := self._mpv_path_for_item(item)) is not None
        )
        paths = all_paths[:MAXIMUM_MATCH_CANDIDATES]
        unplayable = len(visible_items) - len(all_paths)
        if unplayable:
            self._notify(f"動画として再生できない候補 {unplayable} 件はスキップします。")
        over_limit = len(all_paths) - len(paths)
        if over_limit:
            self._notify(
                f"上限{MAXIMUM_MATCH_CANDIDATES}件を超えた動画 {over_limit} 件はスキップします。"
            )
        self._launch_mpv_playlist(paths)

    def play_confirmed_items_from_mpv(self, source_item: MediaItem) -> None:
        """Keep the visible playlist, but begin at one chosen candidate."""
        path = self._mpv_path_for_item(source_item)
        if not self._paths_locked or path is None:
            self._notify("この候補から対象一覧を再生できません。")
            return
        paths = tuple(
            linked
            for item in self._visible_source_items()
            if (linked := self._mpv_path_for_item(item)) is not None
        )[:MAXIMUM_MATCH_CANDIDATES]
        try:
            start_index = paths.index(path)
        except ValueError:
            self._notify("再生開始候補を対象一覧から特定できませんでした。")
            return
        self._launch_mpv_playlist(paths, playlist_start_index=start_index)

    def _visible_source_items(self) -> tuple[MediaItem, ...]:
        by_token = {media_item_token(item): item for item in self._source_items}
        return tuple(
            by_token[token]
            for token in self.path_input.visible_item_tokens()
            if token in by_token
        )

    def show_mpv_shortcuts(self) -> None:
        """Show only the shortcuts that will actually be active for this app."""
        self._settings = settings.load_settings()
        bindings = effective_shortcut_bindings(
            self._settings["mpv_shortcuts"],
            enable_tag_selection=True,
            enable_rating_selection=self._access_mode == "edit",
        )
        dialog = QDialog(self)
        dialog.setWindowTitle("mpvショートカット一覧")
        dialog.setMinimumSize(700, 510)
        layout = QVBoxLayout(dialog)
        layout.addWidget(
            QLabel(
                "このアプリが --no-config で起動するmpvにおける最終結果です。"
                f"同じキーは{PRODUCT_NAME}連携、次に任意キー、最後にmpv標準の順で優先します。"
            )
        )
        table = QTableWidget(len(bindings), 4, dialog)
        table.setHorizontalHeaderLabels(("キー", "効果", "種類", "mpv命令 / 連携先"))
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        table.verticalHeader().setVisible(False)
        table.setAlternatingRowColors(True)
        for row, binding in enumerate(bindings):
            for column, value in enumerate((binding.key, binding.action, binding.source, binding.command)):
                cell = QTableWidgetItem(value)
                cell.setToolTip(value)
                table.setItem(row, column, cell)
        header = table.horizontalHeader()
        header.setStretchLastSection(True)
        table.resizeColumnsToContents()
        layout.addWidget(table, 1)
        layout.addWidget(
            QLabel(
                "評価キーは一覧を短くするため1行にまとめています。"
                "任意キーは永続設定の mpv_command をそのまま表示します。"
            )
        )
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.reject)
        buttons.accepted.connect(dialog.accept)
        layout.addWidget(buttons)
        dialog.exec()

    def _launch_mpv_playlist(self, paths: tuple[Path, ...], *, playlist_start_index: int = 0) -> None:
        playable = tuple(path for path in paths if path.is_file())
        skipped = len(paths) - len(playable)
        if not playable:
            self._notify("mpvで再生できる通常ファイルがありません。")
            return
        # Settings may have been edited while this screen stayed open.  Read
        # them at the explicit launch point so the next player gets them.
        self._settings = settings.load_settings()
        try:
            session = create_isolated_mpv_session(
                playable,
                configured_path=self._settings["mpv_path"],
                shortcuts=self._settings["mpv_shortcuts"],
                playlist_start_index=playlist_start_index,
                enable_tag_selection=True,
                enable_rating_selection=self._access_mode == "edit",
            )
        except ValueError as exc:
            self._notify("mpvで再生できません。", str(exc))
            return

        process = QProcess(self)
        environment = QProcessEnvironment.systemEnvironment()
        for name, value in session.environment().items():
            environment.insert(name, value)
        process.setProcessEnvironment(environment)
        process.setProgram(str(session.program))
        process.setArguments(list(session.arguments))
        self._mpv_sessions[process] = session
        process.finished.connect(
            lambda exit_code, _exit_status, player=process: self._finish_mpv_session(player, exit_code)
        )
        process.errorOccurred.connect(
            lambda _error, player=process: self._report_mpv_error(player)
        )
        process.start()
        self._mpv_event_timer.start()
        lines = [
            "mpvをRAM上の一時設定で起動しました。",
            f"再生: {len(playable)}件（一覧順のプレイリスト）",
            f"実行ファイル: {session.program}",
            "[: コメントあり範囲開始 / ]: コメント入力して記録 / /: コメントなし範囲開始 / \\: コメントなしで記録 / →長押し: キーフレーム送り / m: 見どころ / c: 見どころメモ / n: 次の動画",
        ]
        if self._access_mode == "edit":
            lines.append("1〜9、0: 評価1〜10。反映先は今回の出力予定のみです。")
        else:
            lines.append("閲覧モードのため評価キーは無効です。見どころ・タグは今回だけ表示します。")
        lines.append("t: 登録済み候補または自由入力からタグを追加。")
        if playlist_start_index:
            lines.append(f"開始位置: {playlist_start_index + 1}件目")
        if skipped:
            lines.append(f"フォルダなど再生できない{skipped}件はプレイリストへ入れませんでした。")
        lines.extend(session.binding_warnings)
        self._notify(
            *lines,
        )

    def _finish_mpv_session(self, process: QProcess, exit_code: int) -> None:
        session = self._mpv_sessions.pop(process, None)
        if session is not None:
            for event in session.read_events():
                self._apply_mpv_event(session, event)
            self._mpv_pending_ranges = {
                key: value
                for key, value in self._mpv_pending_ranges.items()
                if key[0] != str(session.directory)
            }
            session.cleanup()
        if not self._mpv_sessions:
            self._mpv_event_timer.stop()
        if exit_code != 0:
            self._notify("mpvが通常終了しませんでした。", f"終了コード: {exit_code}")

    def _report_mpv_error(self, process: QProcess) -> None:
        session = self._mpv_sessions.pop(process, None)
        if session is not None:
            session.cleanup()
        if not self._mpv_sessions:
            self._mpv_event_timer.stop()
        self._notify("mpvを起動できませんでした。", process.errorString())

    def _read_mpv_events(self) -> None:
        """Apply player-originated actions to in-memory output data only."""
        for session in tuple(self._mpv_sessions.values()):
            for event in session.read_events():
                self._apply_mpv_event(session, event)

    def _apply_mpv_event(self, session: IsolatedMpvSession, event: MpvEvent) -> None:
        index = self._media_item_index_for_mpv_path(event.media_path)
        if index is None:
            self._notify("mpvからの操作対象を特定できませんでした。", event.media_path)
            return
        source_item = self._source_items[index]
        if event.kind == "rating":
            if self._access_mode != "edit":
                return
            try:
                score = int(event.value)
            except ValueError:
                return
            if 1 <= score <= 10:
                self._set_item_rating(source_item, score)
            return
        if event.kind == "highlight_point":
            self._append_player_highlight(index, _mpv_time_text(event.time_seconds), "", "見どころ地点")
            return
        if event.kind == "tag_selection":
            self._request_player_tag(index)
            return
        mpv_path = self._mpv_path_for_item(source_item)
        if mpv_path is None:
            return
        range_key = (str(session.directory), str(mpv_path))
        if event.kind == "highlight_range_start":
            self._mpv_pending_ranges[range_key] = event.time_seconds
            self._notify(f"{media_item_display_name(source_item)} の見どころ範囲開始を記録しました。", _mpv_time_text(event.time_seconds))
            return
        if event.kind == "highlight_range_end":
            start = self._mpv_pending_ranges.pop(range_key, None)
            if start is None:
                self._notify("先に「見どころ範囲の開始」を押してください。")
                return
            beginning, end = sorted((start, event.time_seconds))
            self._request_highlight_range_comment(session, index, beginning, end)
            return
        if event.kind == "highlight_range_end_no_comment":
            start = self._mpv_pending_ranges.pop(range_key, None)
            if start is None:
                self._notify("先に「見どころ範囲の開始」を押してください。")
                return
            beginning, end = sorted((start, event.time_seconds))
            self._append_player_highlight(
                index,
                f"{_mpv_time_text(beginning)}-{_mpv_time_text(end)}",
                "",
                "見どころ範囲",
            )
            return
        if event.kind == "highlight_comment":
            self._request_highlight_comment(session, index, event.time_seconds)
            return

    def _media_item_index_for_mpv_path(self, value: str) -> int | None:
        if not value:
            return None
        try:
            path = normalize_path(value)
        except (OSError, ValueError):
            return None
        return next(
            (index for index, item in enumerate(self._source_items) if item.path == path),
            next(
                (
                    index
                    for index, item in enumerate(self._source_items)
                    if self._mpv_linked_paths.get(media_item_token(item)) == path
                ),
                None,
            ),
        )

    def _append_player_value(self, index: int, key: str, value: str, label: str) -> None:
        if self._access_mode != "edit":
            self._notify(f"閲覧モードでは{label}を編集できません。")
            return
        try:
            proposed = apply_catalog_field_operation(
                self._items[index], key=key, operation="append", value=value
            )
        except ValueError as exc:
            self._notify(f"{label}を反映できません。", str(exc))
            return
        source_item = self._source_items[index]
        self._remember_edit_for_undo(f"{media_item_display_name(source_item)} の{label}追加")
        self._items[index] = proposed
        self._invalidate_review_patch_merge()
        applied = _AppliedOperation(
            key=key,
            operation="append",
            value=value,
            item_count=1,
            scope="individual",
            item_name=media_item_display_name(source_item),
        )
        self._applied_operations.append(applied)
        self._applied_operation_summaries.append(
            f"{media_item_display_name(source_item)}: {self._applied_operation_summary(applied)}"
        )
        self._show_items()
        self._notify(
            f"{media_item_display_name(source_item)} に{label}を反映しました。",
            f"{value}。実ファイル・JSONはまだ変更していません。",
        )

    def _append_player_highlight(self, index: int, time: str, comment: str, label: str) -> None:
        """Stage one `{time, comment}` highlight; comments never imply tags."""
        if self._access_mode == "browse":
            detail = " ".join(value for value in (time, comment) if value).strip()
            self._append_browse_session_note(index, detail or label)
            return
        try:
            proposed = append_media_highlight(self._items[index], time=time, comment=comment)
        except ValueError as exc:
            self._notify(f"{label}を反映できません。", str(exc))
            return
        source_item = self._source_items[index]
        self._remember_edit_for_undo(f"{media_item_display_name(source_item)} の{label}追加")
        self._items[index] = proposed
        self._invalidate_review_patch_merge()
        self._applied_operations.append(
            _AppliedOperation(
                key="media.highlights",
                operation="append",
                value=time,
                item_count=1,
                scope="individual",
                item_name=media_item_display_name(source_item),
            )
        )
        self._applied_operation_summaries.append(
            f"{media_item_display_name(source_item)}: {label} {time}"
        )
        self._show_items()
        detail = f"{time} {comment}".strip()
        self._notify(
            f"{media_item_display_name(source_item)} に{label}を反映しました。",
            f"{detail}。実ファイル・JSONはまだ変更していません。",
        )

    def _append_player_tag(self, index: int, value: str) -> bool:
        """Append one player-selected tag, retaining an identical tag untouched."""
        tag = value.strip()
        if not tag:
            self._notify("タグを入力または選択してください。")
            return False
        if self._access_mode == "browse":
            self._append_browse_session_note(index, f"タグ: {tag}")
            return True
        existing = self._items[index].attribute_map.get("classification.tag")
        existing_values = existing.value if existing is not None and isinstance(existing.value, list) else [
            existing.value if existing is not None else None
        ]
        if tag in {str(item).strip() for item in existing_values if item is not None and str(item).strip()}:
            self._notify(
                f"{media_item_display_name(self._source_items[index])} には、タグ「{tag}」がすでにあります。",
                "同じタグなので今回の出力予定は変更していません。",
            )
            return False
        self._append_player_value(index, "classification.tag", tag, "タグ")
        return True

    def _append_browse_session_note(self, index: int, text: str) -> None:
        """Display one mpv fact for this browse session without editing records."""
        token = media_item_token(self._source_items[index])
        notes = self._browse_session_notes.setdefault(token, [])
        if text not in notes:
            notes.append(text)
        self._refresh_candidate_table()
        self._refresh_browse_view()
        self._notify(
            f"{media_item_display_name(self._source_items[index])} に今回だけ表示する情報を追加しました。",
            f"{text}。元JSON・ファイルは変更していません。",
        )

    def _request_player_tag(self, index: int) -> None:
        """Open a transient chooser for the registered tags or one free entry."""
        if self._mpv_tag_dialog is not None:
            self._notify("タグの選択画面はすでに開いています。")
            return
        source_item = self._source_items[index]
        dialog = QDialog()
        dialog.setWindowTitle("タグを追加")
        dialog.setModal(False)
        dialog.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint)
        dialog.setMinimumWidth(440)
        layout = QVBoxLayout(dialog)
        title = QLabel(f"{media_item_display_name(source_item)} に付けるタグ")
        layout.addWidget(title)
        description = QLabel(
            "登録済みの候補から選ぶか、ここへ自由に入力してください。"
            "同じタグは追加しません。"
        )
        description.setWordWrap(True)
        layout.addWidget(description)
        choices = QComboBox(dialog)
        choices.setEditable(True)
        choices.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        choices.addItems(self._settings["mpv_tag_choices"])
        choices.setCurrentIndex(-1)
        if choices.lineEdit() is not None:
            choices.lineEdit().setPlaceholderText("タグを選択、または入力")
        layout.addWidget(choices)
        buttons = QDialogButtonBox(dialog)
        add_button = buttons.addButton("タグを追加", QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton("中止", QDialogButtonBox.ButtonRole.RejectRole).clicked.connect(dialog.reject)
        layout.addWidget(buttons)

        def add_tag() -> None:
            if self._append_player_tag(index, choices.currentText()):
                dialog.accept()
            else:
                choices.setFocus()

        def forget_dialog() -> None:
            if self._mpv_tag_dialog is dialog:
                self._mpv_tag_dialog = None
            dialog.deleteLater()

        add_button.clicked.connect(add_tag)
        dialog.finished.connect(lambda _result: forget_dialog())
        self._mpv_tag_dialog = dialog
        dialog.show()
        choices.setFocus()

    def _request_highlight_comment(
        self, session: IsolatedMpvSession, index: int, seconds: float
    ) -> None:
        if self._mpv_comment_dialog is not None:
            self._notify("見どころメモの入力欄はすでに開いています。")
            return
        source_item = self._source_items[index]
        dialog = QInputDialog()
        dialog.setWindowTitle("見どころメモ")
        dialog.setLabelText(f"{media_item_display_name(source_item)} / {_mpv_time_text(seconds)}")
        dialog.setInputMode(QInputDialog.InputMode.TextInput)
        dialog.setTextEchoMode(QLineEdit.EchoMode.Normal)
        dialog.setModal(False)
        dialog.setWindowFlags(
            Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint
        )
        dialog.setMinimumWidth(440)

        def save_comment() -> None:
            value = dialog.textValue().strip()
            if value:
                self._append_player_highlight(
                    index, _mpv_time_text(seconds), value, "見どころコメント"
                )
                if not session.request_resume():
                    self._notify("見どころメモは反映しましたが、mpvへ再生再開を送れませんでした。")

        def forget_dialog() -> None:
            if self._mpv_comment_dialog is dialog:
                self._mpv_comment_dialog = None
            dialog.deleteLater()

        dialog.accepted.connect(save_comment)
        dialog.finished.connect(lambda _result: forget_dialog())
        self._mpv_comment_dialog = dialog
        dialog.show()
        # This is only a request to focus the small transient dialog.  Once it
        # closes, a Wayland compositor may return focus to the prior mpv window;
        # we deliberately do not try to force-focus another application.
        dialog.raise_()
        dialog.activateWindow()

    def _request_highlight_range_comment(
        self, session: IsolatedMpvSession, index: int, beginning: float, end: float
    ) -> None:
        """Ask for the comment when a bracketed highlight range is completed.

        Tag choices are only a convenient comment preset. Nothing selected
        here changes the item's tags. Nothing is persisted until the normal
        explicit patch/parts output action.
        """
        if self._mpv_comment_dialog is not None:
            self._notify("見どころコメントの入力欄はすでに開いています。")
            return
        source_item = self._source_items[index]
        time_range = f"{_mpv_time_text(beginning)}-{_mpv_time_text(end)}"
        dialog = QDialog()
        dialog.setWindowTitle("見どころ範囲のコメント")
        dialog.setModal(False)
        dialog.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint)
        dialog.setMinimumWidth(440)
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel(f"{media_item_display_name(source_item)} / {time_range}"))
        description = QLabel(
            "コメントを選択または入力してください。候補はタグ用設定を流用しますが、"
            "ここで選んだ内容はコメントとしてだけ記録します。空欄のままでも時間範囲だけ記録できます。"
        )
        description.setWordWrap(True)
        layout.addWidget(description)
        choices = QComboBox(dialog)
        choices.setEditable(True)
        choices.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        choices.addItems(self._settings["mpv_tag_choices"])
        choices.setCurrentIndex(-1)
        if choices.lineEdit() is not None:
            choices.lineEdit().setPlaceholderText("コメントを選択、または入力")
        layout.addWidget(choices)
        buttons = QDialogButtonBox(dialog)
        save_button = buttons.addButton("見どころを記録", QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton("中止", QDialogButtonBox.ButtonRole.RejectRole).clicked.connect(dialog.reject)
        layout.addWidget(buttons)

        def save_range() -> None:
            comment = choices.currentText().strip()
            self._append_player_highlight(index, time_range, comment, "見どころ範囲")
            if not session.request_resume():
                self._notify("見どころは反映しましたが、mpvへ再生再開を送れませんでした。")
            dialog.accept()

        def forget_dialog() -> None:
            if self._mpv_comment_dialog is dialog:
                self._mpv_comment_dialog = None
            dialog.deleteLater()

        save_button.clicked.connect(save_range)
        dialog.finished.connect(lambda _result: forget_dialog())
        self._mpv_comment_dialog = dialog
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()
        choices.setFocus()

    @staticmethod
    def _output_path_from_drop(path: Path) -> Path:
        """Use a dropped directory/JSON directly; otherwise target its parent."""
        if path.is_dir() or path.suffix.casefold() == ".json":
            return path
        return path.parent

    def _notify(self, *lines: str) -> None:
        self.notice.setPlainText("\n".join(line for line in lines if line))


def create_screen(return_to_main: Callable[[], None]) -> MediaInformationScreen:
    """Factory used by the central launcher catalog."""
    return MediaInformationScreen(return_to_main)
