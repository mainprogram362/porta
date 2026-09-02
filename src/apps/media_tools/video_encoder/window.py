"""Working UI skeleton for explicit, non-destructive FFmpeg video conversion."""

from __future__ import annotations

from collections.abc import Callable
import os
from pathlib import Path
import signal
import tempfile

from PySide6.QtCore import QProcess, QTimer
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from foundation.power_status import PowerStatus, read_power_status
from foundation.runtime_activity import RuntimeActivity, begin_runtime_activity
from foundation.transient_paths import take_video_encode_paths
from gui import AppHeader, AppPageLayout, NoWheelComboBox, PathLineInput, PathListInput
from media.video_encode import (
    FFmpegTools,
    VideoEncodePlan,
    VideoEncodeRequest,
    VideoEditRule,
    build_encode_preview,
    build_keyframe_cut_command,
    discover_tools,
    keyframe_cut_concat_text,
    validate_tools,
)

from . import settings
from gui.persistent_settings import create_app_settings_file, show_settings_location_editor


_ENCODE_PRESETS = {
    "quality_hevc": {
        "label": "圧縮・劣化小（H.265 / CRF 20 / 解像度維持）",
        "description": "映像はH.265で圧縮し、解像度を変えずに出力します。コンテナメタデータは引き継ぎます。",
        "mode": "reencode", "codec": "hevc", "crf": 20, "resolution": "", "resize": "fit", "audio": 192, "size": 1000,
        "rate": "crf", "locked": {"codec", "crf", "resolution", "resize", "audio", "rate", "size"},
    },
    "h264_resolution": {
        "label": "解像度を統一（H.264 / CRF 20）",
        "description": "指定解像度へ変換します。既定では縦横比を維持し、余白を付けて収めます。",
        "mode": "reencode", "codec": "h264", "crf": 20, "resolution": "1920×1080", "resize": "fit", "audio": 192, "size": 1000,
        "rate": "crf", "locked": {"codec", "crf", "audio", "rate", "size"},
    },
    "target_size": {
        "label": "サイズ目安を指定（H.264）",
        "description": "1動画あたりの目標MiBから映像ビットレートを計算します。厳密な上限ではありません。",
        "mode": "reencode", "codec": "h264", "crf": 20, "resolution": "", "resize": "fit", "audio": 128, "size": 1000,
        "rate": "target_size", "locked": {"codec", "crf", "rate"},
    },
    "free": {
        "label": "完全自由",
        "description": "普通の用途で使うコーデック・CRF・解像度・音声・サイズ目安を自由に指定します。",
        "mode": "reencode", "codec": "h264", "crf": 20, "resolution": "", "resize": "fit", "audio": 192, "size": 1000,
        "rate": "crf", "locked": set(),
    },
    "keyframe_cut": {
        "label": "高速キーフレームカット（再エンコードなし）",
        "description": "指定範囲を含むキーフレーム境界まで広げて削除します。ほかの編集や画質変更は使えません。",
        "mode": "keyframe_cut", "codec": "h264", "crf": 20, "resolution": "", "resize": "fit", "audio": 192, "size": 1000,
        "rate": "crf", "locked": {"codec", "crf", "resolution", "resize", "audio", "rate", "size"},
    },
}

_VIDEO_INPUT_SUFFIXES = frozenset(
    {
        ".3gp", ".asf", ".avi", ".flv", ".m2ts", ".m4v", ".mkv", ".mov",
        ".mp4", ".mpeg", ".mpg", ".mts", ".ogv", ".ts", ".webm", ".wmv",
    }
)


class VideoEncoderScreen(QWidget):
    """Batch encode videos only after tool selection and a complete preview."""

    def __init__(self, return_to_main: Callable[[], None]) -> None:
        super().__init__()
        self._return_to_main = return_to_main
        self._settings = settings.load_settings()
        self._path_history_restoring = False
        self._path_undo_states: list[tuple[tuple[object, ...], ...]] = []
        self._path_redo_states: list[tuple[tuple[object, ...], ...]] = []
        self._plan: VideoEncodePlan | None = None
        self._process: QProcess | None = None
        self._runtime_activity: RuntimeActivity | None = None
        self._queue_index = 0
        self._completed_outputs: list[Path] = []
        self._temporary_run_directory: tempfile.TemporaryDirectory[str] | None = None
        self._current_output: Path | None = None
        self._paused_process = False
        self._pause_after_current = False
        self._stop_after_current = False
        self._stop_now_requested = False
        self._queue_paused = False
        self._charge_waiting = False
        self._charge_poll_timer = QTimer(self)
        self._charge_poll_timer.setInterval(30_000)
        self._charge_poll_timer.timeout.connect(self._poll_charge_wait)
        self._execution_log: list[str] = []
        self._detail_dialog: QDialog | None = None
        self._detail_text: QPlainTextEdit | None = None
        self._settings_dialog: QDialog | None = None
        self._settings_editor: QTextEdit | None = None
        # ここは外側をスクロールさせない画面なので、各操作欄が崩れない最小値を
        # 明示する。通常の作業では、必要に応じてウィンドウを広げて使える。
        self.setMinimumSize(820, 760)
        self._build_ui()
        received = take_video_encode_paths()
        if received:
            self.path_input.append_items(received)
            self._notify(f"ファイルマネージャーから{len(received)}件を受け取りました。", "まだ変換はしていません。")
        else:
            self.path_input.append_items(settings.paths_for(self._settings, "initial_source"))
        self._apply_settings_to_controls()
        self._reset_path_history()

    def _build_ui(self) -> None:
        layout = AppPageLayout(self)

        self.header = AppHeader(
            self._return_to_main,
            title="動画変換",
            on_settings=self.show_settings,
            settings_tooltip="入力・出力パスの役割とFFmpeg/ffprobeのパスだけを保存します。履歴は保存しません。",
        )
        self.header.content_layout.addStretch(1)
        layout.addWidget(self.header)

        self.input_box = QGroupBox("① 変換する動画")
        input_layout = QVBoxLayout(self.input_box)
        self.path_input = PathListInput(
            rows=3,
            accepted_path_kind="all",
            drop_replaces=True,
            show_controls=False,
            double_click_directory_selection=True,
            double_click_directory_replaces_all=True,
            enable_row_selection=True,
            direct_child_filter=self._is_video_browser_candidate,
        )
        self.path_input.setToolTip(
            "ファイルマネージャーから一時送信できます。外部からドロップした場合は、"
            "現在の一覧を空にしてドロップしたパスへ置き換えます。"
            "通常フォルダをダブルクリックすると、一覧全体を直下の動画とフォルダへ置き換えます。"
        )
        self.path_input.set_context_menu_augmenter(self._add_input_path_context_menu)
        self.path_input.actionPerformed.connect(self._notify)
        input_actions = QHBoxLayout()
        input_actions.setContentsMargins(0, 0, 0, 0)
        check_selected = QPushButton("選択中をチェック")
        check_selected.setToolTip("青く選択中の行だけを変換対象にします。ほかのチェックは変えません。")
        check_selected.clicked.connect(lambda: self._set_selected_rows_checked(True))
        input_actions.addWidget(check_selected)
        uncheck_selected = QPushButton("選択中のチェック解除")
        uncheck_selected.setToolTip("青く選択中の行だけを変換対象から外します。行自体は残します。")
        uncheck_selected.clicked.connect(lambda: self._set_selected_rows_checked(False))
        input_actions.addWidget(uncheck_selected)
        input_actions.addStretch(1)
        self.path_undo_button = QPushButton("戻る")
        self.path_undo_button.setToolTip("入力一覧のパスとチェック状態だけを1つ前へ戻します。実ファイルは変更しません。")
        self.path_undo_button.clicked.connect(self.undo_path_change)
        input_actions.addWidget(self.path_undo_button)
        self.path_redo_button = QPushButton("進む")
        self.path_redo_button.setToolTip("入力一覧で取り消したパスとチェック状態だけをやり直します。")
        self.path_redo_button.clicked.connect(self.redo_path_change)
        input_actions.addWidget(self.path_redo_button)
        input_layout.addLayout(input_actions)
        input_layout.addWidget(self.path_input)

        self.tools_box = QGroupBox("② 出力先・FFmpeg")
        tools_layout = QFormLayout(self.tools_box)
        tools_layout.setContentsMargins(8, 7, 8, 7)
        tools_layout.setVerticalSpacing(3)
        self.output_mode_combo = NoWheelComboBox()
        self.output_mode_combo.addItem("各動画と同じ場所", "alongside_source")
        self.output_mode_combo.addItem("指定先へまとめる（既定）", "specified_directory")
        self.output_mode_combo.setCurrentIndex(1)
        self.output_mode_combo.setToolTip(
            "指定フォルダ方式では出力先の入力が必須です。各動画と同じフォルダ方式では入力欄を使いません。"
        )
        self.output_mode_combo.currentIndexChanged.connect(self._update_output_mode_controls)
        output_options = QWidget()
        output_options_layout = QHBoxLayout(output_options)
        output_options_layout.setContentsMargins(0, 0, 0, 0)
        output_options_layout.setSpacing(4)
        output_options_layout.addWidget(QLabel("出力"))
        output_options_layout.addWidget(self.output_mode_combo, 1)
        self.output_directory_input = PathLineInput(drop_as="directory")
        self.output_directory_input.setToolTip(
            "今回の出力先です。フォルダまたはファイルをドロップすると、既存の入力を置き換えます。"
            "編集しても永続設定は変わりません。"
        )
        self.output_directory_input.set_context_menu_augmenter(
            lambda menu: self._add_registered_paths_menu(
                menu,
                settings.paths_for(self._settings, "context_menu"),
                title="登録した出力先を使用",
                choose_path=self._use_registered_output_path,
            )
        )
        self.output_suffix_input = QLineEdit("_encoded")
        self.output_suffix_input.setToolTip(
            "出力ファイル名の末尾です。例: _compressed → movie_compressed.mp4。"
            "空欄は従来どおり _encoded を使います。"
        )
        self.output_suffix_input.setMinimumWidth(82)
        output_options_layout.addWidget(QLabel("末尾"))
        output_options_layout.addWidget(self.output_suffix_input)
        tools_layout.addRow(output_options)
        self.output_directory_label = QLabel("保存先")
        tools_layout.addRow(self.output_directory_label, self.output_directory_input)
        self.ffmpeg_input = QLineEdit()
        self.ffmpeg_input.setPlaceholderText("ffmpeg のフルパス")
        tools_layout.addRow("ffmpeg", self.ffmpeg_input)
        self.ffprobe_input = QLineEdit()
        self.ffprobe_input.setPlaceholderText("ffprobe のフルパス")
        tools_layout.addRow("ffprobe", self.ffprobe_input)
        tool_buttons = QHBoxLayout()
        detect_button = QPushButton("標準の場所から探索")
        detect_button.clicked.connect(self.detect_tools)
        tool_buttons.addWidget(detect_button)
        test_tools_button = QPushButton("表示中のパスをテスト")
        test_tools_button.clicked.connect(self.test_displayed_tools)
        tool_buttons.addWidget(test_tools_button)
        tools_layout.addRow(tool_buttons)
        self.tool_status = QLabel()
        self.tool_status.setWordWrap(False)
        tools_layout.addRow("確認", self.tool_status)
        top_row = QHBoxLayout()
        top_row.setSpacing(6)
        top_row.addWidget(self.input_box, 1)
        top_row.addWidget(self.tools_box, 1)
        layout.addLayout(top_row)

        self.edit_box = QGroupBox("③ エンコード方法")
        edit_layout = QFormLayout(self.edit_box)
        edit_layout.setContentsMargins(8, 7, 8, 7)
        edit_layout.setVerticalSpacing(3)
        self.preset_combo = NoWheelComboBox()
        for key, preset in _ENCODE_PRESETS.items():
            self.preset_combo.addItem(preset["label"], key)
        self.preset_combo.currentIndexChanged.connect(self._apply_encode_preset)
        edit_layout.addRow("プリセット", self.preset_combo)
        self.encode_backend_combo = NoWheelComboBox()
        self.encode_backend_combo.addItem("CPU（ソフトウェア・現在利用可能）", "software")
        for label, key in (
            ("NVIDIA NVENC（未検査）", "nvidia_nvenc"),
            ("Intel Quick Sync（未検査）", "intel_qsv"),
            ("VA-API / Intel・AMD（未検査）", "vaapi"),
        ):
            self.encode_backend_combo.addItem(label, key)
            model_item = self.encode_backend_combo.model().item(
                self.encode_backend_combo.count() - 1
            )
            if model_item is not None:
                model_item.setEnabled(False)
                model_item.setToolTip("GPUとFFmpegの事前テストをまだ実装していないため選択できません。")
        self.encode_backend_combo.setToolTip(
            "GPU方式は候補と利用可否を分離しています。現在は検査を行わず、CPUだけを使用できます。"
        )
        self.encode_backend_combo.currentIndexChanged.connect(self._mark_preview_stale)
        edit_layout.addRow("実行装置", self.encode_backend_combo)
        self.encode_backend_status = QLabel(
            "GPU事前テスト: 未実装（FFmpeg・GPUを検査していません）"
        )
        self.encode_backend_status.setWordWrap(True)
        edit_layout.addRow("装置確認", self.encode_backend_status)
        self.preset_description = QLabel()
        self.preset_description.setWordWrap(True)
        edit_layout.addRow("内容", self.preset_description)
        self.fixed_settings = QLabel()
        self.fixed_settings.setWordWrap(True)
        self.fixed_settings_label = QLabel("固定設定")
        edit_layout.addRow(self.fixed_settings_label, self.fixed_settings)
        self.encode_mode_combo = NoWheelComboBox()
        self.encode_mode_combo.addItem("通常エンコード", "reencode")
        self.encode_mode_combo.addItem("高速キーフレームカット（再エンコードなし）", "keyframe_cut")
        self.encode_mode_combo.setVisible(False)
        self.trim_frames = QSpinBox()
        self.trim_frames.setRange(0, 10_000_000)
        self.trim_frames.setToolTip("先頭から削除するフレーム数です。可変フレームレートでは音声開始位置が概算になります。")
        self.trim_label = QLabel("先頭から削除するフレーム")
        edit_layout.addRow(self.trim_label, self.trim_frames)
        self.cut_start_seconds = QDoubleSpinBox()
        self.cut_start_seconds.setRange(0, 864_000)
        self.cut_start_seconds.setDecimals(3)
        self.cut_start_seconds.setSuffix(" 秒")
        self.cut_start_seconds.setToolTip("削除する範囲の開始です。元動画の時刻を基準にします。")
        self.cut_start_label = QLabel("削除開始")
        edit_layout.addRow(self.cut_start_label, self.cut_start_seconds)
        self.cut_end_seconds = QDoubleSpinBox()
        self.cut_end_seconds.setRange(0, 864_000)
        self.cut_end_seconds.setDecimals(3)
        self.cut_end_seconds.setSuffix(" 秒")
        self.cut_end_seconds.setValue(1)
        self.cut_end_seconds.setToolTip("削除する範囲の終了です。動画の長さを越える場合は末尾へ丸めます。")
        self.cut_end_label = QLabel("削除終了")
        edit_layout.addRow(self.cut_end_label, self.cut_end_seconds)
        self.codec_combo = NoWheelComboBox()
        self.codec_combo.addItem("H.264（互換性重視）", "h264")
        self.codec_combo.addItem("H.265 / HEVC（高圧縮）", "hevc")
        self.codec_combo.addItem("AV1（高圧縮・低速）", "av1")
        self.codec_label = QLabel("映像コーデック")
        edit_layout.addRow(self.codec_label, self.codec_combo)
        self.crf_spin = QSpinBox()
        self.crf_spin.setRange(0, 63)
        self.crf_spin.setValue(20)
        self.crf_spin.setToolTip("小さいほど高画質・大容量です。まずH.264なら18〜22程度で比較してください。")
        self.crf_label = QLabel("品質（CRF、小さいほど高画質）")
        edit_layout.addRow(self.crf_label, self.crf_spin)
        self.resolution_input = QLineEdit()
        self.resolution_input.setPlaceholderText("空欄なら元の解像度を維持。例: 1920×1080")
        self.resolution_label = QLabel("出力解像度")
        edit_layout.addRow(self.resolution_label, self.resolution_input)
        self.resize_mode_combo = NoWheelComboBox()
        self.resize_mode_combo.addItem("縦横比を維持して収める", "fit")
        self.resize_mode_combo.addItem("指定解像度へ強制変形", "stretch")
        self.resize_mode_label = QLabel("解像度の扱い")
        edit_layout.addRow(self.resize_mode_label, self.resize_mode_combo)
        self.audio_bitrate_spin = QSpinBox()
        self.audio_bitrate_spin.setRange(32, 512)
        self.audio_bitrate_spin.setSingleStep(16)
        self.audio_bitrate_spin.setValue(192)
        self.audio_bitrate_spin.setSuffix(" kbps")
        self.rate_control_combo = NoWheelComboBox()
        self.rate_control_combo.addItem("CRF（品質優先）", "crf")
        self.rate_control_combo.addItem("サイズ目安（1動画あたり）", "target_size")
        self.rate_control_combo.currentIndexChanged.connect(self._update_rate_control)
        self.rate_control_label = QLabel("映像の決め方")
        edit_layout.addRow(self.rate_control_label, self.rate_control_combo)
        self.target_size_spin = QSpinBox()
        self.target_size_spin.setRange(1, 1_000_000)
        self.target_size_spin.setValue(1000)
        self.target_size_spin.setSuffix(" MiB / 動画")
        self.audio_bitrate_label = QLabel("音声")
        self.target_size_label = QLabel("目標")
        self.rate_detail_row = QWidget()
        rate_detail_layout = QHBoxLayout(self.rate_detail_row)
        rate_detail_layout.setContentsMargins(0, 0, 0, 0)
        rate_detail_layout.setSpacing(5)
        rate_detail_layout.addWidget(self.audio_bitrate_label)
        rate_detail_layout.addWidget(self.audio_bitrate_spin)
        rate_detail_layout.addWidget(self.target_size_label)
        rate_detail_layout.addWidget(self.target_size_spin)
        rate_detail_layout.addStretch(1)
        self.rate_detail_row.setToolTip(
            "サイズ目安では、目標容量と音声ビットレートを同じ行で指定します。"
            "目標容量は厳密な上限ではありません。"
        )
        edit_layout.addRow(self.rate_detail_row)
        layout.addWidget(self.edit_box)

        self.rules_box = QGroupBox("④ 編集ルール")
        rules_box_layout = QVBoxLayout(self.rules_box)
        rules_box_layout.setContentsMargins(8, 7, 8, 7)
        rules_box_layout.setSpacing(3)
        self.rules_toggle = QPushButton()
        self.rules_toggle.setCheckable(True)
        self.rules_toggle.setChecked(False)
        self.rules_toggle.setToolTip(
            "開いている間だけ、登録済みの編集ルールを変換へ適用します。"
            "閉じると登録済みルールが残っていても内部処理で無効になります。"
        )
        self.rules_toggle.toggled.connect(self._set_edit_rules_enabled)
        rules_box_layout.addWidget(self.rules_toggle)
        self.rules_content = QWidget()
        rules_layout = QVBoxLayout(self.rules_content)
        rules_layout.setContentsMargins(8, 7, 8, 7)
        rules_layout.setSpacing(3)
        rule_controls = QHBoxLayout()
        self.rule_basis_combo = NoWheelComboBox()
        self.rule_basis_combo.addItem("秒", "seconds")
        self.rule_basis_combo.addItem("フレーム", "frames")
        self.rule_basis_combo.currentIndexChanged.connect(self._update_rule_input_mode)
        rule_controls.addWidget(QLabel("時間基準"))
        rule_controls.addWidget(self.rule_basis_combo)
        self.rule_kind_combo = NoWheelComboBox()
        self.rule_kind_combo.addItem("範囲を削除", "cut")
        self.rule_kind_combo.addItem("モザイク", "mosaic")
        self.rule_kind_combo.addItem("画像を重ねる", "image_overlay")
        self.rule_kind_combo.currentIndexChanged.connect(self._update_rule_input_mode)
        rule_controls.addWidget(QLabel("操作"))
        rule_controls.addWidget(self.rule_kind_combo)
        self.rule_start_seconds = QDoubleSpinBox()
        self.rule_start_seconds.setRange(0, 864_000)
        self.rule_start_seconds.setDecimals(3)
        self.rule_start_seconds.setSuffix(" 秒")
        self.rule_end_seconds = QDoubleSpinBox()
        self.rule_end_seconds.setRange(0, 864_000)
        self.rule_end_seconds.setDecimals(3)
        self.rule_end_seconds.setValue(1)
        self.rule_end_seconds.setSuffix(" 秒")
        self.rule_start_frames = QSpinBox()
        self.rule_start_frames.setRange(0, 10_000_000)
        self.rule_end_frames = QSpinBox()
        self.rule_end_frames.setRange(0, 10_000_000)
        self.rule_end_frames.setValue(1)
        rule_controls.addWidget(QLabel("開始"))
        rule_controls.addWidget(self.rule_start_seconds)
        rule_controls.addWidget(self.rule_start_frames)
        rule_controls.addWidget(QLabel("終了"))
        rule_controls.addWidget(self.rule_end_seconds)
        rule_controls.addWidget(self.rule_end_frames)
        rules_layout.addLayout(rule_controls)
        self.rule_detail_row = QHBoxLayout()
        self.mosaic_x = QSpinBox(); self.mosaic_x.setRange(0, 100_000)
        self.mosaic_y = QSpinBox(); self.mosaic_y.setRange(0, 100_000)
        self.mosaic_width = QSpinBox(); self.mosaic_width.setRange(1, 100_000); self.mosaic_width.setValue(100)
        self.mosaic_height = QSpinBox(); self.mosaic_height.setRange(1, 100_000); self.mosaic_height.setValue(100)
        self.mosaic_block = QSpinBox(); self.mosaic_block.setRange(2, 1_000); self.mosaic_block.setValue(16)
        self.overlay_path = PathLineInput(drop_as="full_path")
        self.overlay_x = QSpinBox(); self.overlay_x.setRange(0, 100_000)
        self.overlay_y = QSpinBox(); self.overlay_y.setRange(0, 100_000)
        rules_layout.addLayout(self.rule_detail_row)
        rule_buttons = QHBoxLayout()
        add_rule = QPushButton("ルールを追加")
        add_rule.clicked.connect(self.add_edit_rule)
        rule_buttons.addWidget(add_rule)
        clear_rules = QPushButton("ルールを空にする")
        clear_rules.clicked.connect(self.clear_edit_rules)
        rule_buttons.addWidget(clear_rules)
        remove_last_rule = QPushButton("最後のルールを削除")
        remove_last_rule.clicked.connect(self.remove_last_edit_rule)
        rule_buttons.addWidget(remove_last_rule)
        self.rule_hint = QLabel()
        rule_buttons.addWidget(self.rule_hint, 1)
        rules_layout.addLayout(rule_buttons)
        self._add_rule_detail_widgets()
        self.rule_list = QPlainTextEdit()
        self.rule_list.setReadOnly(True)
        self.rule_list.setFixedHeight(58)
        self.rule_list.setPlaceholderText("追加した編集ルールをここに表示します。削除は「最後のルールを削除」で末尾から行えます。")
        rules_layout.addWidget(self.rule_list)
        self._edit_rules = []
        self._update_rule_input_mode()
        rules_box_layout.addWidget(self.rules_content)
        self._edit_rules_enabled = False
        self._refresh_edit_rules_visibility()
        layout.addWidget(self.rules_box)

        actions = QHBoxLayout()
        self.preview_button = QPushButton("変換内容を確認")
        self.preview_button.clicked.connect(self.update_preview)
        actions.addWidget(self.preview_button)
        self.execute_button = QPushButton("変換を実行")
        self.execute_button.setEnabled(False)
        self.execute_button.clicked.connect(self.execute_plan)
        actions.addWidget(self.execute_button)
        detail_button = QPushButton("実行詳細")
        detail_button.setToolTip("今回のFFmpeg実行状況と標準エラー出力を表示します。保存しません。")
        detail_button.clicked.connect(self.show_execution_details)
        actions.addWidget(detail_button)
        self.charge_wait_check = QCheckBox("低充電で待機")
        self.charge_wait_check.setToolTip(
            "有効時、残量が開始値以下なら次の動画を開始せず待機します。"
            "現在の動画は途中で止めず、休止・シャットダウンも行いません。"
        )
        self.charge_wait_check.toggled.connect(self._charge_wait_settings_changed)
        actions.addWidget(self.charge_wait_check)
        actions.addWidget(QLabel("開始 ≤"))
        self.charge_start_spin = QSpinBox()
        self.charge_start_spin.setRange(1, 98)
        self.charge_start_spin.setValue(25)
        self.charge_start_spin.setSuffix(" %")
        self.charge_start_spin.setToolTip("この残量以下では、次の動画を開始せず充電待ちにします。")
        self.charge_start_spin.valueChanged.connect(self._charge_wait_settings_changed)
        actions.addWidget(self.charge_start_spin)
        actions.addWidget(QLabel("再開 ≥"))
        self.charge_resume_spin = QSpinBox()
        self.charge_resume_spin.setRange(2, 100)
        self.charge_resume_spin.setValue(60)
        self.charge_resume_spin.setSuffix(" %")
        self.charge_resume_spin.setToolTip("外部給電を確認でき、この残量以上になったら次の動画から再開します。")
        self.charge_resume_spin.valueChanged.connect(self._charge_wait_settings_changed)
        actions.addWidget(self.charge_resume_spin)
        actions.addStretch(1)
        layout.addLayout(actions)

        queue_actions = QHBoxLayout()
        self.pause_button = QPushButton("一時停止")
        self.pause_button.setEnabled(False)
        self.pause_button.clicked.connect(self.toggle_pause)
        queue_actions.addWidget(self.pause_button)
        self.pause_after_button = QPushButton("この動画の後に一時停止")
        self.pause_after_button.setEnabled(False)
        self.pause_after_button.clicked.connect(self.pause_after_current_item)
        queue_actions.addWidget(self.pause_after_button)
        self.stop_after_button = QPushButton("この動画の後に中止")
        self.stop_after_button.setEnabled(False)
        self.stop_after_button.clicked.connect(self.stop_after_current_item)
        queue_actions.addWidget(self.stop_after_button)
        self.cancel_button = QPushButton("すぐ中止")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel_now)
        queue_actions.addWidget(self.cancel_button)
        self.charge_status = QLabel("充電待ち: 無効")
        self.charge_status.setToolTip("電源情報はこのPCの現在の状態だけを読み、保存しません。")
        queue_actions.addWidget(self.charge_status, 1)
        layout.addLayout(queue_actions)

        preview_box = QGroupBox("⑤ 変換プレビュー")
        preview_layout = QVBoxLayout(preview_box)
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setMinimumHeight(160)
        self.preview.setPlaceholderText("FFmpegの選定と、作成予定のファイル名をここで確認します。")
        preview_layout.addWidget(self.preview, 1)

        notice_box = QGroupBox("実行結果")
        notice_layout = QVBoxLayout(notice_box)
        self.notice = QPlainTextEdit()
        self.notice.setReadOnly(True)
        self.notice.setMinimumHeight(160)
        self.notice.setPlaceholderText("実行結果をここに表示します。選択してコピーできます。")
        notice_layout.addWidget(self.notice, 1)
        bottom_row = QHBoxLayout()
        bottom_row.addWidget(preview_box, 3)
        bottom_row.addWidget(notice_box, 2)
        layout.addLayout(bottom_row, 1)

        # プレビューは「この時点の入力から作った実行計画」です。入力が変わった
        # なら古い計画を実行できないよう、ここで全ての可変入力を結び付ける。
        self.path_input.textChanged.connect(self._mark_preview_stale)
        self.path_input.selectionChanged.connect(self._mark_preview_stale)
        self.path_input.textChanged.connect(self._record_path_state)
        self.path_input.selectionChanged.connect(self._record_path_state)
        for line_edit in (
            self.output_directory_input,
            self.output_suffix_input,
            self.ffmpeg_input,
            self.ffprobe_input,
            self.resolution_input,
        ):
            line_edit.textChanged.connect(self._mark_preview_stale)
        for spin_box in (
            self.cut_start_seconds,
            self.cut_end_seconds,
            self.crf_spin,
            self.audio_bitrate_spin,
            self.target_size_spin,
        ):
            spin_box.valueChanged.connect(self._mark_preview_stale)

    @staticmethod
    def _is_video_browser_candidate(path: Path) -> bool:
        """Keep folder browsing useful without claiming FFmpeg accepts every file."""
        if path.is_dir() and not path.is_symlink():
            return True
        return path.is_file() and path.suffix.casefold() in _VIDEO_INPUT_SUFFIXES

    def _set_selected_rows_checked(self, checked: bool) -> None:
        changed = self.path_input.set_row_selected_items_checked(checked)
        action = "チェック" if checked else "チェック解除"
        self._notify(f"青く選択中の行を{action}しました（変更 {changed}件）。")

    def _reset_path_history(self) -> None:
        self._path_undo_states = [self.path_input.snapshot()]
        self._path_redo_states.clear()
        self._update_path_history_buttons()

    def _record_path_state(self) -> None:
        if self._path_history_restoring or self._execution_session_active():
            return
        state = self.path_input.snapshot()
        if self._path_undo_states and state == self._path_undo_states[-1]:
            return
        self._path_undo_states.append(state)
        # This is transient UI history. A finite bound avoids retaining an
        # unexpectedly large path list forever during a long-running screen.
        self._path_undo_states = self._path_undo_states[-100:]
        self._path_redo_states.clear()
        self._update_path_history_buttons()

    def _update_path_history_buttons(self) -> None:
        if not hasattr(self, "path_undo_button"):
            return
        unlocked = not self._execution_session_active()
        self.path_undo_button.setEnabled(unlocked and len(self._path_undo_states) > 1)
        self.path_redo_button.setEnabled(unlocked and bool(self._path_redo_states))

    def _restore_path_state(self, state: tuple[tuple[object, ...], ...]) -> None:
        self._path_history_restoring = True
        try:
            self.path_input.restore_snapshot(state)
        finally:
            self._path_history_restoring = False
        self._mark_preview_stale()
        self._update_path_history_buttons()

    def undo_path_change(self) -> None:
        if self._execution_session_active() or len(self._path_undo_states) <= 1:
            return
        current = self._path_undo_states.pop()
        self._path_redo_states.append(current)
        self._restore_path_state(self._path_undo_states[-1])
        self._notify("入力一覧の操作を1つ戻しました。", "実ファイルや変換設定は変更していません。")

    def redo_path_change(self) -> None:
        if self._execution_session_active() or not self._path_redo_states:
            return
        state = self._path_redo_states.pop()
        self._path_undo_states.append(state)
        self._restore_path_state(state)
        self._notify("入力一覧の操作を1つ進めました。", "実ファイルや変換設定は変更していません。")

    def _input_paths_for_scope(self, scope: str) -> list[Path]:
        if scope == "checked":
            return self.path_input.selected_paths(deduplicate=True)
        if scope == "selected":
            return self.path_input.row_selected_paths(deduplicate=True)
        if scope == "all":
            return self.path_input.paths(deduplicate=True)
        raise ValueError(f"未対応の一覧範囲です: {scope}")

    def _copy_input_values(self, scope: str, *, file_names: bool) -> None:
        paths = self._input_paths_for_scope(scope)
        scope_label = {"checked": "チェック済み", "selected": "選択中", "all": "全件"}[scope]
        value_label = "ファイル名" if file_names else "パス"
        if not paths:
            self._notify(f"{scope_label}の{value_label}はありません。")
            return
        values = (path.name for path in paths) if file_names else (str(path) for path in paths)
        QApplication.clipboard().setText("\n".join(values))
        self._notify(f"{scope_label}{len(paths)}件の{value_label}をコピーしました。")

    def _add_input_path_context_menu(self, menu: QMenu, item) -> None:  # type: ignore[no-untyped-def]
        """Add only non-destructive list operations useful before encoding."""
        clicked_path = (
            Path(item.text(1).strip())
            if item is not None and item.text(1).strip()
            else None
        )
        if clicked_path is not None and clicked_path.is_dir() and not clicked_path.is_symlink():
            menu.addSeparator()
            expand_menu = QMenu("フォルダを展開", menu)
            menu.addMenu(expand_menu)
            replace_action = expand_menu.addAction("このフォルダを開く（一覧全体を置換）")
            replace_action.setToolTip("直下の通常フォルダと一般的な動画だけを表示し、現在の一覧を置き換えます。")
            replace_action.triggered.connect(
                lambda _checked=False, row=item: self.path_input.open_directory_item(row)
            )
            choose_action = expand_menu.addAction("直下を選んで展開…")
            choose_action.setToolTip("このフォルダ行だけを、選んだ直下の動画・フォルダへ置き換えます。")
            choose_action.triggered.connect(
                lambda _checked=False, row=item: self.path_input.choose_direct_children_for_item(row)
            )

        menu.addSeparator()
        selected_menu = QMenu("青い選択行", menu)
        menu.addMenu(selected_menu)
        selected_count = len(self.path_input.row_selected_paths(deduplicate=True))
        check_action = selected_menu.addAction("選択中をチェック")
        check_action.setEnabled(selected_count > 0)
        check_action.triggered.connect(lambda: self._set_selected_rows_checked(True))
        uncheck_action = selected_menu.addAction("選択中のチェックを外す")
        uncheck_action.setEnabled(selected_count > 0)
        uncheck_action.triggered.connect(lambda: self._set_selected_rows_checked(False))

        copy_menu = QMenu("複数のパス・ファイル名をコピー", menu)
        menu.addMenu(copy_menu)
        for scope, scope_label in (
            ("checked", "チェック済み"),
            ("selected", "選択中"),
            ("all", "全件"),
        ):
            count = len(self._input_paths_for_scope(scope))
            path_action = copy_menu.addAction(f"パス：{scope_label}")
            path_action.setEnabled(count > 0)
            path_action.triggered.connect(
                lambda _checked=False, value=scope: self._copy_input_values(value, file_names=False)
            )
            name_action = copy_menu.addAction(f"ファイル名：{scope_label}")
            name_action.setEnabled(count > 0)
            name_action.triggered.connect(
                lambda _checked=False, value=scope: self._copy_input_values(value, file_names=True)
            )

        self._add_registered_paths_menu(
            menu,
            settings.paths_for(self._settings, "context_menu"),
            title="登録パスを入力一覧へ追加",
            choose_path=lambda path: self.path_input.append_items((path,)),
        )

    def _add_registered_paths_menu(
        self,
        menu: QMenu,
        paths: list[str],
        *,
        title: str,
        choose_path: Callable[[str], None],
    ) -> None:
        """Add settings-backed paths while leaving the normal input menu intact."""
        if not paths:
            return
        if menu.actions():
            menu.addSeparator()
        registered_menu = QMenu(title, menu)
        registered_menu.setToolTipsVisible(True)
        menu.addMenu(registered_menu)
        for path in paths:
            action = registered_menu.addAction(self._registered_path_label(path))
            action.setToolTip(path)
            action.triggered.connect(
                lambda _checked=False, value=path: choose_path(value)
            )

    @staticmethod
    def _registered_path_label(path: str) -> str:
        """Keep a short name while always exposing enough parent context."""
        candidate = Path(path)
        if not candidate.name:
            return str(candidate)
        return f"{candidate.name} — {candidate.parent}"

    def _use_registered_output_path(self, path: str) -> None:
        specified_index = self.output_mode_combo.findData("specified_directory")
        if specified_index >= 0:
            self.output_mode_combo.setCurrentIndex(specified_index)
        self.output_directory_input.set_path_from_external_value(Path(path))

    def _apply_settings_to_controls(self) -> None:
        initial_outputs = settings.paths_for(self._settings, "initial_output")
        self.output_directory_input.setText(initial_outputs[0] if initial_outputs else "")
        specified_index = self.output_mode_combo.findData("specified_directory")
        if specified_index >= 0:
            self.output_mode_combo.setCurrentIndex(specified_index)
        ffmpeg_path = self._settings["ffmpeg_path"]
        ffprobe_path = self._settings["ffprobe_path"]
        self.ffmpeg_input.setText(ffmpeg_path)  # type: ignore[arg-type]
        self.ffprobe_input.setText(ffprobe_path)  # type: ignore[arg-type]
        self._update_output_mode_controls()
        if ffmpeg_path and ffprobe_path:
            self.test_displayed_tools(configured=True)
        else:
            self.detect_tools(initial=True)
        self._apply_encode_preset()

    def _update_output_mode_controls(self) -> None:
        """Make the required output path explicit for the selected mode."""
        specified_directory = self.output_mode_combo.currentData() == "specified_directory"
        self.output_directory_label.setEnabled(specified_directory)
        self.output_directory_input.setEnabled(specified_directory)
        self.output_directory_input.setPlaceholderText(
            "出力先フォルダを入力（必須）" if specified_directory else "各動画と同じフォルダへ出力します"
        )
        self._mark_preview_stale()

    def _mark_preview_stale(self, *_unused: object) -> None:
        """Invalidate a plan as soon as any value it depends on changes."""
        if self._execution_session_active():
            # The active queue owns an immutable plan.  Programmatic signals
            # must never erase it while FFmpeg, a pause, or charge wait is live.
            return
        self._plan = None
        self.execute_button.setEnabled(False)
        if self.preview.toPlainText():
            self.preview.setPlainText("条件を変更しました。もう一度「変換内容を確認」を押してください。")

    def detect_tools(self, *, initial: bool = False) -> None:
        try:
            tools = discover_tools()
        except ValueError as exc:
            self.ffmpeg_input.clear()
            self.ffprobe_input.clear()
            self.tool_status.setText("自動探索: 使えるFFmpeg / ffprobeを確認できませんでした。")
            if not initial:
                self._notify("標準の場所からFFmpegを使えません。", str(exc))
            return
        self.ffmpeg_input.setText(str(tools.ffmpeg))
        self.ffprobe_input.setText(str(tools.ffprobe))
        self.tool_status.setText("FFmpeg / ffprobe: 使用可")
        if not initial:
            self._notify("ffmpeg と ffprobe を見つけ、実行テストに合格しました。")

    def test_displayed_tools(self, *, configured: bool = False) -> None:
        try:
            validate_tools(self.ffmpeg_input.text(), self.ffprobe_input.text())
        except ValueError as exc:
            self.tool_status.setText("表示中のパスは使えません。")
            self._notify("表示中のFFmpegを使えません。", str(exc))
            return
        self.tool_status.setText("FFmpeg / ffprobe: 使用可")
        self._notify("ffmpeg と ffprobe の実行テストに合格しました。")

    def _selected_tools(self) -> FFmpegTools:
        return validate_tools(self.ffmpeg_input.text(), self.ffprobe_input.text())

    def _update_encode_mode_controls(self) -> None:
        """Refresh preset locks and the deliberately narrow stream-copy mode."""
        self._refresh_encode_controls()

    def _update_rate_control(self) -> None:
        """Keep one source of truth for rate control and preset visibility."""
        self._refresh_encode_controls()

    def _refresh_encode_controls(self) -> None:
        """Show only settings that can affect the currently selected preset.

        A disabled input still looks like something the user should set.  Fixed
        preset values are therefore summarized in one line, while editable
        controls remain visible.  This also keeps the normal working screen
        compact without hiding what the preset promises.
        """
        keyframe_mode = self.encode_mode_combo.currentData() == "keyframe_cut"
        for widget in (self.trim_label, self.trim_frames):
            widget.setVisible(False)
        field_widgets = {
            "codec": (self.codec_label, self.codec_combo),
            "crf": (self.crf_label, self.crf_spin),
            "resolution": (self.resolution_label, self.resolution_input),
            "resize": (self.resize_mode_label, self.resize_mode_combo),
            "audio": (self.audio_bitrate_label, self.audio_bitrate_spin),
            "rate": (self.rate_control_label, self.rate_control_combo),
            "size": (self.target_size_label, self.target_size_spin),
        }
        locked = getattr(self, "_locked_encode_fields", set())
        visibility: dict[str, bool] = {}
        for name, widgets in field_widgets.items():
            visible = not keyframe_mode and name not in locked
            if name == "size":
                visible = visible and self.rate_control_combo.currentData() == "target_size"
            elif name == "crf":
                visible = visible and self.rate_control_combo.currentData() != "target_size"
            elif name == "audio":
                # サイズ指定では目標容量と並べて表示するため、通常の単独行には出さない。
                visible = visible and self.rate_control_combo.currentData() != "target_size"
            visibility[name] = visible
            for widget in widgets:
                widget.setEnabled(visible)
                widget.setVisible(visible)
        combined_rate_details = (
            not keyframe_mode
            and self.rate_control_combo.currentData() == "target_size"
            and "audio" not in locked
            and "size" not in locked
        )
        if combined_rate_details:
            self.audio_bitrate_label.setVisible(True)
            self.audio_bitrate_spin.setVisible(True)
            self.audio_bitrate_spin.setEnabled(True)
            self.target_size_label.setVisible(True)
            self.target_size_spin.setVisible(True)
            self.target_size_spin.setEnabled(True)
        self.rate_detail_row.setVisible(
            combined_rate_details or visibility["audio"] or visibility["size"]
        )
        for widget in (self.cut_start_label, self.cut_start_seconds, self.cut_end_label, self.cut_end_seconds):
            widget.setVisible(keyframe_mode)
        fixed_text = self._fixed_settings_text(keyframe_mode, locked)
        self.fixed_settings_label.setVisible(bool(fixed_text))
        self.fixed_settings.setVisible(bool(fixed_text))
        self.fixed_settings.setText(fixed_text)
        self._refresh_edit_rules_visibility(keyframe_mode=keyframe_mode)
        self._mark_preview_stale()

    def _fixed_settings_text(self, keyframe_mode: bool, locked: set[str]) -> str:
        if keyframe_mode:
            return "再エンコードなし。通常の編集ルールは使わず、指定範囲だけをキーフレーム境界へ広げて削除します。"
        if not locked:
            return ""
        parts: list[str] = []
        if "codec" in locked:
            parts.append(self.codec_combo.currentText())
        if "crf" in locked and self.rate_control_combo.currentData() == "crf":
            parts.append(f"CRF {self.crf_spin.value()}")
        if "resolution" in locked:
            parts.append(self.resolution_input.text().strip() or "解像度は元のまま")
        if "resize" in locked and self.resolution_input.text().strip():
            parts.append(self.resize_mode_combo.currentText())
        if "audio" in locked:
            parts.append(f"音声 {self.audio_bitrate_spin.value()} kbps")
        if "rate" in locked:
            parts.append(self.rate_control_combo.currentText())
        return " / ".join(parts)

    def _apply_encode_preset(self) -> None:
        """Fill all ordinary choices from a preset, then lock only its promises."""
        key = str(self.preset_combo.currentData() or "quality_hevc")
        preset = _ENCODE_PRESETS[key]
        self.preset_description.setText(str(preset["description"]))
        self.encode_mode_combo.setCurrentIndex(self.encode_mode_combo.findData(preset["mode"]))
        self.codec_combo.setCurrentIndex(self.codec_combo.findData(preset["codec"]))
        self.crf_spin.setValue(int(preset["crf"]))
        self.resolution_input.setText(str(preset["resolution"]))
        self.resize_mode_combo.setCurrentIndex(self.resize_mode_combo.findData(preset["resize"]))
        self.audio_bitrate_spin.setValue(int(preset["audio"]))
        self.target_size_spin.setValue(int(preset["size"]))
        self.rate_control_combo.setCurrentIndex(self.rate_control_combo.findData(preset["rate"]))
        locked = set(preset["locked"])
        self._locked_encode_fields = locked
        self._refresh_encode_controls()

    def _add_rule_detail_widgets(self) -> None:
        while self.rule_detail_row.count():
            child = self.rule_detail_row.takeAt(0)
            if child.widget() is not None:
                child.widget().setParent(None)
        kind = self.rule_kind_combo.currentData()
        if kind == "mosaic":
            for label, widget in (
                ("モザイク X", self.mosaic_x), ("Y", self.mosaic_y), ("幅", self.mosaic_width),
                ("高さ", self.mosaic_height), ("粗さ", self.mosaic_block),
            ):
                self.rule_detail_row.addWidget(QLabel(label))
                self.rule_detail_row.addWidget(widget)
            self.rule_hint.setText("指定範囲に、指定矩形だけモザイクをかけます。")
        elif kind == "image_overlay":
            self.rule_detail_row.addWidget(QLabel("画像"))
            self.rule_detail_row.addWidget(self.overlay_path, 1)
            self.rule_detail_row.addWidget(QLabel("X"))
            self.rule_detail_row.addWidget(self.overlay_x)
            self.rule_detail_row.addWidget(QLabel("Y"))
            self.rule_detail_row.addWidget(self.overlay_y)
            self.rule_hint.setText("指定範囲だけ画像を重ねます。画像自体は変更しません。")
        else:
            self.rule_detail_row.addWidget(QLabel("指定範囲を出力から削除します。"))
            self.rule_detail_row.addStretch(1)
            self.rule_hint.setText("複数の削除範囲は重なりを自動的に整理します。")

    def _update_rule_input_mode(self) -> None:
        frames = self.rule_basis_combo.currentData() == "frames"
        self.rule_start_seconds.setVisible(not frames)
        self.rule_end_seconds.setVisible(not frames)
        self.rule_start_frames.setVisible(frames)
        self.rule_end_frames.setVisible(frames)
        self._add_rule_detail_widgets()

    def _set_edit_rules_enabled(self, enabled: bool) -> None:
        """Make the collapsed state an execution boundary, not only decoration."""
        if self._execution_session_active():
            self.rules_toggle.blockSignals(True)
            self.rules_toggle.setChecked(self._edit_rules_enabled)
            self.rules_toggle.blockSignals(False)
            self._notify("変換中は編集ルールの有効・無効を切り替えられません。")
            return
        self._edit_rules_enabled = bool(enabled)
        self._refresh_edit_rules_visibility()
        self._mark_preview_stale()
        if enabled:
            self._notify("編集ルールを有効にしました。", "この欄に登録したルールを変換へ適用します。")
        else:
            self._notify(
                "編集ルールを無効にしました。",
                f"登録済み{len(self._edit_rules)}件は保持していますが、変換には一切適用しません。",
            )

    def _refresh_edit_rules_visibility(self, *, keyframe_mode: bool | None = None) -> None:
        if keyframe_mode is None:
            keyframe_mode = self.encode_mode_combo.currentData() == "keyframe_cut"
        usable = self._edit_rules_enabled and not keyframe_mode
        self.rules_content.setVisible(usable)
        self.rules_toggle.setEnabled(not keyframe_mode)
        marker = "▼" if usable else "▶"
        if keyframe_mode:
            state = "高速キーフレームカット中は無効"
        elif self._edit_rules_enabled:
            state = f"有効・登録済み{len(self._edit_rules)}件"
        else:
            state = f"無効・登録済み{len(self._edit_rules)}件"
        self.rules_toggle.setText(f"{marker} 編集ルールを使う（{state}）")

    def _effective_edit_rules(self) -> tuple[VideoEditRule, ...]:
        """Return rules only while the explicit editor gate is open."""
        if not self._edit_rules_enabled:
            return ()
        if self.encode_mode_combo.currentData() == "keyframe_cut":
            return ()
        return tuple(self._edit_rules)

    def add_edit_rule(self) -> None:
        if not self._edit_rules_enabled:
            self._notify("編集ルールは無効です。", "「編集ルールを使う」を開いてから追加してください。")
            return
        if self.encode_mode_combo.currentData() == "keyframe_cut":
            self._notify("高速キーフレームカットでは、通常の編集ルールを追加できません。")
            return
        frames = self.rule_basis_combo.currentData() == "frames"
        start = float(self.rule_start_frames.value() if frames else self.rule_start_seconds.value())
        end = float(self.rule_end_frames.value() if frames else self.rule_end_seconds.value())
        kind = str(self.rule_kind_combo.currentData())
        try:
            rule = VideoEditRule(
                kind=kind,
                time_basis="frames" if frames else "seconds",
                start=start,
                end=end,
                x=self.mosaic_x.value() if kind == "mosaic" else self.overlay_x.value(),
                y=self.mosaic_y.value() if kind == "mosaic" else self.overlay_y.value(),
                width=self.mosaic_width.value() if kind == "mosaic" else 0,
                height=self.mosaic_height.value() if kind == "mosaic" else 0,
                mosaic_block_size=self.mosaic_block.value(),
                image_path=Path(self.overlay_path.text()).expanduser() if kind == "image_overlay" and self.overlay_path.text().strip() else None,
            )
            # Reuse the core validator through a tiny request-independent check.
            if end <= start:
                raise ValueError("終了は開始より後にしてください。")
            if kind == "image_overlay" and (rule.image_path is None or not rule.image_path.is_file()):
                raise ValueError("重ねる画像ファイルを指定してください。")
        except ValueError as exc:
            self._notify("編集ルールを追加できません。", str(exc))
            return
        self._edit_rules.append(rule)
        self._refresh_rule_list()
        self._refresh_edit_rules_visibility()
        self._mark_preview_stale()
        self._notify(f"編集ルールを追加しました（{len(self._edit_rules)}件）。")

    def clear_edit_rules(self) -> None:
        self._edit_rules.clear()
        self._refresh_rule_list()
        self._refresh_edit_rules_visibility()
        self._mark_preview_stale()
        self._notify("編集ルールを空にしました。")

    def remove_last_edit_rule(self) -> None:
        if not self._edit_rules:
            self._notify("削除する編集ルールはありません。")
            return
        self._edit_rules.pop()
        self._refresh_rule_list()
        self._refresh_edit_rules_visibility()
        self._mark_preview_stale()
        self._notify(f"最後の編集ルールを削除しました（残り{len(self._edit_rules)}件）。")

    def _refresh_rule_list(self) -> None:
        lines = []
        for number, rule in enumerate(self._edit_rules, start=1):
            unit = "フレーム" if rule.time_basis == "frames" else "秒"
            action = {"cut": "削除", "mosaic": "モザイク", "image_overlay": "画像重ね"}[rule.kind]
            extra = (
                f" X={rule.x} Y={rule.y} 幅={rule.width} 高さ={rule.height}"
                if rule.kind == "mosaic" else
                f" {rule.image_path} X={rule.x} Y={rule.y}" if rule.kind == "image_overlay" else ""
            )
            lines.append(f"{number}. {action}: {rule.start:g}〜{rule.end:g} {unit}{extra}")
        self.rule_list.setPlainText("\n".join(lines))

    def update_preview(self) -> None:
        if self._execution_session_active():
            self._notify(
                "実行中の変換内容は変更できません。",
                "現在のキューを完了するか、「すぐ中止」で終了してから確認し直してください。",
            )
            return
        self._plan = None
        self.execute_button.setEnabled(False)
        try:
            tools = self._selected_tools()
            output_mode = self.output_mode_combo.currentData()
            output_text = self.output_directory_input.text().strip()
            encode_mode = self.encode_mode_combo.currentData()
            resolution = self._selected_resolution()
            target_size = (
                self.target_size_spin.value()
                if self.rate_control_combo.currentData() == "target_size"
                else None
            )
            request = VideoEncodeRequest(
                sources=tuple(self.path_input.selected_paths(deduplicate=True)),
                output_directory=Path(output_text).expanduser() if output_text else None,
                codec=self.codec_combo.currentData(),
                crf=self.crf_spin.value(),
                trim_start_frames=0 if encode_mode == "keyframe_cut" else self.trim_frames.value(),
                output_mode=output_mode,
                output_suffix=self.output_suffix_input.text(),
                encode_mode=encode_mode,
                cut_start_seconds=self.cut_start_seconds.value() if encode_mode == "keyframe_cut" else None,
                cut_end_seconds=self.cut_end_seconds.value() if encode_mode == "keyframe_cut" else None,
                resolution=resolution,
                resize_mode=self.resize_mode_combo.currentData(),
                audio_bitrate_kbps=self.audio_bitrate_spin.value(),
                target_size_mib=target_size,
                edit_rules=self._effective_edit_rules(),
                encode_backend=self.encode_backend_combo.currentData(),
            )
            preview = build_encode_preview(tools, request)
        except ValueError as exc:
            self.preview.setPlainText("実行できません。次を確認してください。\n" + str(exc))
            return
        self.preview.setPlainText(preview.text)
        self._plan = preview.plan
        self.execute_button.setEnabled(preview.is_ready)

    def _selected_resolution(self) -> tuple[int, int] | None:
        text = self.resolution_input.text().strip()
        if not text:
            return None
        normalized = text.replace("×", "x").replace("*", "x").replace("X", "x")
        try:
            width_text, height_text = (part.strip() for part in normalized.split("x", 1))
            width, height = int(width_text), int(height_text)
        except (ValueError, TypeError):
            raise ValueError("出力解像度は 1920×1080 の形式で入力してください。") from None
        if width <= 0 or height <= 0:
            raise ValueError("出力解像度は縦横とも1以上にしてください。")
        return width, height

    def _charge_wait_settings_changed(self, *_unused: object) -> None:
        """Keep thresholds meaningful and apply a user change immediately."""
        if not hasattr(self, "charge_resume_spin"):
            return
        start = self.charge_start_spin.value()
        resume = self.charge_resume_spin.value()
        if start >= resume:
            if self.sender() is self.charge_start_spin:
                self.charge_resume_spin.setValue(min(100, start + 1))
            else:
                self.charge_start_spin.setValue(max(1, resume - 1))
            return
        if not self.charge_wait_check.isChecked():
            if self._charge_waiting:
                self._stop_charge_wait()
                self._notify("充電待ちを解除し、次の動画を開始します。")
                self._update_execution_buttons()
                if self._plan is not None and self._queue_index < len(self._plan.items):
                    self._run_next_item()
            else:
                self.charge_status.setText("充電待ち: 無効")
            return
        if self._charge_waiting:
            self._poll_charge_wait()
        else:
            self._show_charge_status(read_power_status())

    def _wait_for_charge_before_next_item(self) -> bool:
        """Return true only when an enabled policy intentionally holds the queue."""
        if not self.charge_wait_check.isChecked():
            return False
        status = read_power_status()
        if not status.battery_available:
            self.charge_status.setText("充電待ち: 電池情報なし（判定せず続行）")
            return False
        assert status.battery_percent is not None
        if self._charge_waiting:
            if status.battery_percent >= self.charge_resume_spin.value() and status.receiving_external_power:
                self._stop_charge_wait()
                self.charge_status.setText(
                    f"充電待ち: {status.battery_percent}%、給電確認済み。再開します"
                )
                self._notify("充電が回復したため、次の動画から変換を再開します。", status.summary())
                return False
            self._show_charge_status(status, waiting=True)
            return True
        if status.battery_percent <= self.charge_start_spin.value():
            self._charge_waiting = True
            self._charge_poll_timer.start()
            self._show_charge_status(status, waiting=True)
            self._notify(
                "充電待ち: 現在の動画を開始しません。",
                self._queue_progress_text(),
                f"{status.summary()}。{self.charge_resume_spin.value()}%以上かつ外部給電を確認できたら自動再開します。",
            )
            self._update_execution_buttons()
            return True
        self._show_charge_status(status)
        return False

    def _poll_charge_wait(self) -> None:
        """Poll only while waiting; no history or background power action is used."""
        if not self._charge_waiting:
            self._charge_poll_timer.stop()
            return
        if self._wait_for_charge_before_next_item():
            return
        if self._plan is None or self._queue_index >= len(self._plan.items):
            return
        self._update_execution_buttons()
        self._run_next_item()

    def _show_charge_status(self, status: PowerStatus, *, waiting: bool = False) -> None:
        if not status.battery_available:
            self.charge_status.setText("充電待ち: 電池情報なし")
            return
        assert status.battery_percent is not None
        if waiting:
            requirement = f"{self.charge_resume_spin.value()}%・外部給電待ち"
            self.charge_status.setText(
                f"充電待ち: {status.battery_percent}% → {requirement}｜{self._queue_progress_text()}"
            )
            return
        self.charge_status.setText(f"充電待ち: {status.summary()}")

    def _queue_progress_text(self) -> str:
        completed = len(self._completed_outputs)
        items = getattr(self._plan, "items", None)
        if items is None:
            return f"成功 {completed}件"
        total = len(items)
        remaining = max(0, total - completed)
        return f"成功 {completed}/{total}件・残り {remaining}件"

    def _stop_charge_wait(self) -> None:
        self._charge_waiting = False
        self._charge_poll_timer.stop()

    def execute_plan(self) -> None:
        if self._execution_session_active():
            self._notify(
                "変換キューはすでに実行中です。",
                "変換中・一時停止中・充電待ち中に、新しい実行を重ねることはできません。",
            )
            return
        if self._plan is None:
            self._notify("先に実行可能な変換プレビューを作成してください。")
            return
        self._queue_index = 0
        self._completed_outputs = []
        self._temporary_run_directory = tempfile.TemporaryDirectory(prefix="video-encode-")
        try:
            self._runtime_activity = begin_runtime_activity("動画変換中")
        except OSError as exc:
            self._temporary_run_directory.cleanup()
            self._temporary_run_directory = None
            self._notify("安全な実行状態を準備できないため、変換を開始しません。", str(exc))
            return
        self._current_output = None
        self._paused_process = False
        self._pause_after_current = False
        self._stop_after_current = False
        self._stop_now_requested = False
        self._queue_paused = False
        self._stop_charge_wait()
        self._execution_log = []
        self.execute_button.setEnabled(False)
        self._update_execution_buttons()
        self._run_next_item()

    def _run_next_item(self) -> None:
        assert self._plan is not None
        if self._queue_index >= len(self._plan.items):
            self._process = None
            self._cleanup_temporary_run_directory()
            self._update_execution_buttons()
            self._notify(f"変換完了: {len(self._completed_outputs)}件", *map(str, self._completed_outputs))
            return
        if self._wait_for_charge_before_next_item():
            return
        item = self._plan.items[self._queue_index]
        if not item.source.is_file() or item.output.exists():
            self._cleanup_temporary_run_directory()
            self._update_execution_buttons()
            self._notify("プレビュー後に対象または出力先が変化したため中止しました。", str(item.source))
            return
        try:
            command = self._command_for_item(item)
        except (OSError, ValueError) as exc:
            self._cleanup_temporary_run_directory()
            self._update_execution_buttons()
            self._notify("実行準備をできません。", str(exc))
            return
        process = QProcess(self)
        process.setProgram(command[0])
        process.setArguments(list(command[1:]))
        process.finished.connect(lambda code, _status: self._finish_current_item(item.output, code))
        process.errorOccurred.connect(lambda _error: self._report_process_error())
        process.readyReadStandardError.connect(lambda: self._capture_process_error_output(process))
        self._process = process
        self._current_output = item.output
        self._append_execution_detail(
            f"開始 {self._queue_index + 1}/{len(self._plan.items)}\n"
            f"入力: {item.source}\n出力: {item.output}\n"
            f"コマンド: {' '.join(command)}\n"
        )
        self._update_execution_buttons()
        self._notify(f"変換中: {self._queue_index + 1}/{len(self._plan.items)}", str(item.source))
        process.start()

    def _command_for_item(self, item) -> tuple[str, ...]:  # type: ignore[no-untyped-def]
        if item.keyframe_cut is None:
            return item.command
        if self._temporary_run_directory is None or self._plan is None:
            raise ValueError("高速カット用の一時領域を準備できません。")
        manifest = Path(self._temporary_run_directory.name) / f"cut-{self._queue_index + 1}.ffconcat"
        manifest.write_text(keyframe_cut_concat_text(item), encoding="utf-8")
        return build_keyframe_cut_command(self._plan.tools, item, manifest)

    def _finish_current_item(self, output: Path, exit_code: int) -> None:
        process = self._process
        if process is not None:
            self._capture_process_error_output(process)
        self._process = None
        self._current_output = None
        self._paused_process = False
        if self._stop_now_requested:
            if output.exists():
                try:
                    output.unlink()
                except OSError:
                    pass
            self._cleanup_temporary_run_directory()
            self._update_execution_buttons()
            self._notify("変換をすぐ中止しました。", "現在の未完成出力は削除を試みました。完了済みの動画は残しています。")
            return
        if exit_code != 0 or not output.is_file():
            detail = bytes(process.readAllStandardError()).decode(errors="replace").strip() if process else ""
            self._append_execution_detail(f"失敗: 終了コード {exit_code}\n{detail}\n")
            self._cleanup_temporary_run_directory()
            self._update_execution_buttons()
            self._notify("変換に失敗しました。以降は実行しません。", detail[-1500:])
            return
        self._completed_outputs.append(output)
        self._queue_index += 1
        self._append_execution_detail(f"完了: {output}\n")
        if self._stop_after_current:
            self._cleanup_temporary_run_directory()
            self._update_execution_buttons()
            self._notify("現在の動画まで完了し、その後の変換を中止しました。", str(output))
            return
        if self._pause_after_current:
            self._queue_paused = True
            self._update_execution_buttons()
            self._notify("現在の動画まで完了し、一時停止しています。", "「再開」で次の動画を開始します。")
            return
        self._run_next_item()

    def _report_process_error(self) -> None:
        if self._process is not None:
            message = self._process.errorString()
            self._append_execution_detail(f"FFmpegエラー: {message}\n")
            self._notify("FFmpegを開始できません。", message)

    def _capture_process_error_output(self, process: QProcess) -> None:
        text = bytes(process.readAllStandardError()).decode(errors="replace")
        if text:
            self._append_execution_detail(text)

    def _append_execution_detail(self, text: str) -> None:
        """Keep only this session's live output; no log file is created."""
        if not text:
            return
        self._execution_log.append(text)
        joined = "".join(self._execution_log)
        if len(joined) > 120_000:
            joined = joined[-120_000:]
            self._execution_log = [joined]
        if self._detail_text is not None:
            self._detail_text.setPlainText(joined)
            scrollbar = self._detail_text.verticalScrollBar()
            scrollbar.setValue(scrollbar.maximum())

    def show_execution_details(self) -> None:
        """Show the transient FFmpeg command/output without writing a log."""
        if self._detail_dialog is not None:
            self._detail_dialog.show()
            self._detail_dialog.raise_()
            self._detail_dialog.activateWindow()
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("動画エンコードの実行詳細（一時表示）")
        dialog.setMinimumSize(760, 480)
        layout = QVBoxLayout(dialog)
        state, detail = settings.settings_status()
        layout.addWidget(QLabel(f"設定状態: {state} — {detail}"))
        layout.addWidget(QLabel("今回の実行コマンドとFFmpeg出力です。閉じると表示を閉じるだけで、保存はしません。"))
        detail = QPlainTextEdit()
        detail.setReadOnly(True)
        detail.setPlainText("".join(self._execution_log) or "まだ実行していません。")
        layout.addWidget(detail, 1)
        close = QPushButton("閉じる")
        close.clicked.connect(dialog.close)
        layout.addWidget(close)
        dialog.finished.connect(self._clear_detail_dialog)
        self._detail_dialog = dialog
        self._detail_text = detail
        dialog.show()

    def _clear_detail_dialog(self, *_unused: object) -> None:
        self._detail_dialog = None
        self._detail_text = None

    def _execution_session_active(self) -> bool:
        """Return whether one queue still owns its plan and runtime state."""
        return (
            self._process is not None
            or self._runtime_activity is not None
            or self._queue_paused
            or self._charge_waiting
        )

    def _update_execution_buttons(self) -> None:
        running = self._process is not None
        resumable = self._queue_paused and self._plan is not None and self._queue_index < len(self._plan.items)
        session_active = self._execution_session_active()
        self.preview_button.setEnabled(not session_active)
        if session_active:
            self.execute_button.setEnabled(False)
        for box in (self.input_box, self.tools_box, self.edit_box, self.rules_box):
            box.setEnabled(not session_active)
        if self.header.settings_button is not None:
            self.header.settings_button.setEnabled(not session_active)
        self.pause_button.setEnabled(running or resumable)
        self.pause_button.setText("再開" if self._paused_process or resumable else "一時停止")
        self.pause_after_button.setEnabled(running and not self._pause_after_current)
        self.stop_after_button.setEnabled(running and not self._stop_after_current)
        self.cancel_button.setEnabled(running or resumable or self._charge_waiting)

    def toggle_pause(self) -> None:
        if self._queue_paused:
            self._queue_paused = False
            self._pause_after_current = False
            self._update_execution_buttons()
            self._notify("次の動画から変換を再開します。")
            self._run_next_item()
            return
        if self._process is None:
            return
        try:
            process_id = int(self._process.processId())
            if self._paused_process:
                os.kill(process_id, signal.SIGCONT)
                self._paused_process = False
                self._notify("現在の動画の変換を再開しました。")
            else:
                os.kill(process_id, signal.SIGSTOP)
                self._paused_process = True
                self._notify("現在の動画の変換を一時停止しました。")
        except (OSError, ValueError) as exc:
            self._notify("一時停止・再開を行えません。", str(exc))
        self._update_execution_buttons()

    def pause_after_current_item(self) -> None:
        if self._process is None:
            return
        self._pause_after_current = True
        self._notify("現在の動画が完了したら一時停止します。")
        self._update_execution_buttons()

    def stop_after_current_item(self) -> None:
        if self._process is None:
            return
        self._stop_after_current = True
        self._pause_after_current = False
        self._notify("現在の動画が完了したら、その後の変換を中止します。")
        self._update_execution_buttons()

    def cancel_now(self) -> None:
        if self._charge_waiting:
            self._stop_charge_wait()
            self._cleanup_temporary_run_directory()
            self._update_execution_buttons()
            self.charge_status.setText("充電待ち: 中止")
            self._notify("充電待ちを中止しました。次の動画は開始しません。完了済みの動画は残しています。")
            return
        if self._queue_paused:
            self._queue_paused = False
            self._cleanup_temporary_run_directory()
            self._update_execution_buttons()
            self._notify("次の動画を開始せず、中止しました。完了済みの動画は残しています。")
            return
        if self._process is None:
            return
        self._stop_now_requested = True
        if self._paused_process:
            try:
                os.kill(int(self._process.processId()), signal.SIGCONT)
            except OSError:
                pass
            self._paused_process = False
        self._process.kill()
        self._notify("現在の変換を中止しています。")

    def _cleanup_temporary_run_directory(self) -> None:
        self._stop_charge_wait()
        if self._temporary_run_directory is not None:
            self._temporary_run_directory.cleanup()
            self._temporary_run_directory = None
        if self._runtime_activity is not None:
            self._runtime_activity.close()
            self._runtime_activity = None

    def show_settings(self) -> None:
        if self._execution_session_active():
            self._notify(
                "変換中は永続設定を変更できません。",
                "現在のキューを完了または中止してから開いてください。",
            )
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("動画エンコードの永続設定")
        dialog.setMinimumSize(720, 520)
        layout = QVBoxLayout(dialog)
        explanation = QLabel(settings.help_text())
        explanation.setWordWrap(True)
        layout.addWidget(explanation)
        editor = QTextEdit(settings.editable_text())
        layout.addWidget(editor, 1)
        buttons = QDialogButtonBox()
        template = buttons.addButton("雛形へ戻す", QDialogButtonBox.ButtonRole.ResetRole)
        location = buttons.addButton("保存先入口", QDialogButtonBox.ButtonRole.ActionRole)
        create = buttons.addButton("保存先・設定を作成", QDialogButtonBox.ButtonRole.ActionRole)
        save = buttons.addButton("保存", QDialogButtonBox.ButtonRole.AcceptRole)
        close = buttons.addButton("閉じる", QDialogButtonBox.ButtonRole.RejectRole)
        template.clicked.connect(lambda: editor.setPlainText(settings.template_text()))
        location.clicked.connect(lambda: show_settings_location_editor(dialog))
        create.clicked.connect(lambda: create_app_settings_file(dialog, settings.create_settings_file))
        save.clicked.connect(self.save_settings)
        close.clicked.connect(dialog.reject)
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
        self._apply_settings_to_controls()
        if self._settings_dialog is not None:
            self._settings_dialog.accept()
        self._notify("永続設定を保存しました。", "パスの役割とFFmpeg/ffprobeのパスだけを保存しました。")

    def _notify(self, *lines: str) -> None:
        self.notice.setPlainText("\n".join(line for line in lines if line))


def create_screen(return_to_main: Callable[[], None]) -> VideoEncoderScreen:
    """Factory used by the central launcher catalog."""
    return VideoEncoderScreen(return_to_main)
