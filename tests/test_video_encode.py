import json
import os
from pathlib import Path

import pytest

from apps.media_tools.video_encoder import settings
from apps.media_tools.video_encoder import window as encoder_window
from foundation.transient_paths import offer_video_encode_paths, take_video_encode_paths
from media import video_encode
from media.video_encode import (
    FFmpegTools,
    VideoEncodeRequest,
    build_encode_preview,
    keyframe_cut_concat_text,
)
from PySide6.QtWidgets import QApplication, QMenu
from foundation.power_status import PowerStatus


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def test_video_encode_preview_builds_non_overwriting_mp4_commands(tmp_path: Path, monkeypatch):
    source = tmp_path / "clip.webm"
    source.write_bytes(b"source")
    output_directory = tmp_path / "output"
    output_directory.mkdir()
    monkeypatch.setattr(video_encode, "_probe_video_basics", lambda *_args: (30.0, True))
    monkeypatch.setattr(video_encode, "_probe_duration", lambda *_args: 60.0)
    tools = FFmpegTools(Path("/opt/ffmpeg"), Path("/opt/ffprobe"))
    request = VideoEncodeRequest((source,), output_directory, "hevc", 21, trim_start_frames=90)

    preview = build_encode_preview(tools, request)

    assert preview.is_ready
    assert preview.plan is not None
    assert preview.plan.request.encode_backend == "software"
    assert "実行装置: CPU（ソフトウェア）" in preview.text
    item = preview.plan.items[0]
    assert item.output == output_directory / "clip_encoded.mp4"
    assert "trim=start=3.000000000" in " ".join(item.command)
    assert "libx265" in item.command
    assert "-n" in item.command
    assert source.exists()
    assert not item.output.exists()


def test_video_encode_requires_an_existing_output_directory(tmp_path: Path):
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"source")
    preview = build_encode_preview(
        FFmpegTools(Path("/ffmpeg"), Path("/ffprobe")),
        VideoEncodeRequest((source,), tmp_path / "missing", "h264", 20),
    )

    assert not preview.is_ready
    assert "出力先フォルダ" in preview.text


def test_video_encode_can_write_beside_each_source_without_output_directory(tmp_path: Path, monkeypatch):
    first_directory = tmp_path / "first"
    second_directory = tmp_path / "second"
    first_directory.mkdir()
    second_directory.mkdir()
    first = first_directory / "clip.mp4"
    second = second_directory / "clip.mp4"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    monkeypatch.setattr(video_encode, "_probe_video_basics", lambda *_args: (30.0, True))

    preview = build_encode_preview(
        FFmpegTools(Path("/ffmpeg"), Path("/ffprobe")),
        VideoEncodeRequest(
            (first, second), None, "h264", 20, output_mode="alongside_source"
        ),
    )

    assert preview.is_ready
    assert preview.plan is not None
    assert [item.output for item in preview.plan.items] == [
        first_directory / "clip_encoded.mp4",
        second_directory / "clip_encoded.mp4",
    ]
    assert "各動画と同じフォルダ" in preview.text


def test_video_encoder_screen_defaults_to_explicit_output_directory(tmp_path: Path, monkeypatch):
    QApplication.instance() or QApplication([])
    monkeypatch.setattr(
        encoder_window.settings,
        "load_settings",
        lambda: {
            "default_source_paths": [],
            "default_output_directory": "",
            "ffmpeg_path": "",
            "ffprobe_path": "",
        },
    )
    monkeypatch.setattr(
        encoder_window,
        "discover_tools",
        lambda: FFmpegTools(Path("/ffmpeg"), Path("/ffprobe")),
    )

    screen = encoder_window.VideoEncoderScreen(lambda: None)
    try:
        assert screen.output_mode_combo.currentData() == "specified_directory"
        assert screen.output_directory_input.isEnabled()
        assert screen.output_directory_input.text() == ""
        assert screen.encode_backend_combo.currentData() == "software"
        assert screen.encode_backend_combo.count() == 4
        for index in range(1, screen.encode_backend_combo.count()):
            assert not screen.encode_backend_combo.model().item(index).isEnabled()
        assert "検査していません" in screen.encode_backend_status.text()
        assert screen.rules_content.isHidden()
        assert not screen._edit_rules_enabled
    finally:
        screen.close()


def test_gpu_backend_is_rejected_before_any_video_probe(tmp_path: Path, monkeypatch):
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"source")
    monkeypatch.setattr(
        video_encode,
        "_probe_video_basics",
        lambda *_args: (_ for _ in ()).throw(AssertionError("GPU設計段階では動画を調査しない")),
    )

    preview = build_encode_preview(
        FFmpegTools(Path("/ffmpeg"), Path("/ffprobe")),
        VideoEncodeRequest(
            (source,),
            tmp_path,
            "hevc",
            20,
            encode_backend="nvidia_nvenc",
        ),
    )

    assert not preview.is_ready
    assert "事前テスト機能をまだ接続していない" in preview.text


def test_collapsed_edit_rules_are_excluded_from_internal_request_state(monkeypatch):
    QApplication.instance() or QApplication([])
    monkeypatch.setattr(
        encoder_window.settings,
        "load_settings",
        lambda: {
            "default_source_paths": [],
            "default_output_directory": "",
            "ffmpeg_path": "",
            "ffprobe_path": "",
        },
    )
    monkeypatch.setattr(
        encoder_window,
        "discover_tools",
        lambda: FFmpegTools(Path("/ffmpeg"), Path("/ffprobe")),
    )
    screen = encoder_window.VideoEncoderScreen(lambda: None)
    try:
        rule = video_encode.VideoEditRule("cut", "seconds", 1, 2)
        screen._edit_rules.append(rule)
        assert screen._effective_edit_rules() == ()

        screen._plan = object()  # type: ignore[assignment]
        screen.execute_button.setEnabled(True)
        screen.rules_toggle.setChecked(True)
        assert screen._effective_edit_rules() == (rule,)
        assert not screen.rules_content.isHidden()
        assert screen._plan is None

        screen.rules_toggle.setChecked(False)
        assert screen._effective_edit_rules() == ()
        assert screen.rules_content.isHidden()
        prior_count = len(screen._edit_rules)
        screen.add_edit_rule()
        assert len(screen._edit_rules) == prior_count
    finally:
        screen.close()


def test_video_encoder_folder_double_click_replaces_list_with_video_candidates(
    tmp_path: Path, monkeypatch
):
    QApplication.instance() or QApplication([])
    folder = tmp_path / "collection"
    folder.mkdir()
    movie = folder / "movie.mkv"
    movie.write_bytes(b"video")
    note = folder / "note.txt"
    note.write_text("not video", encoding="utf-8")
    subfolder = folder / "more"
    subfolder.mkdir()
    old = tmp_path / "old.mp4"
    old.write_bytes(b"video")
    monkeypatch.setattr(
        encoder_window.settings,
        "load_settings",
        lambda: {
            "default_source_paths": [],
            "default_output_directory": "",
            "ffmpeg_path": "",
            "ffprobe_path": "",
        },
    )
    monkeypatch.setattr(
        encoder_window,
        "discover_tools",
        lambda: FFmpegTools(Path("/ffmpeg"), Path("/ffprobe")),
    )
    screen = encoder_window.VideoEncoderScreen(lambda: None)
    try:
        screen.path_input.setPlainText(f"{old}\n{folder}")
        folder_row = screen.path_input._tree.topLevelItem(1)
        screen.path_input._on_tree_item_double_clicked(folder_row, 1)

        assert screen.path_input.paths() == [subfolder, movie]
        assert note not in screen.path_input.paths()
        assert old not in screen.path_input.paths()
        assert "一覧を直下 2 件で置き換え" in screen.notice.toPlainText()
    finally:
        screen.close()


def test_video_encoder_blue_selection_can_change_checks_without_changing_rows(
    tmp_path: Path, monkeypatch
):
    QApplication.instance() or QApplication([])
    first = tmp_path / "first.mp4"
    second = tmp_path / "second.mp4"
    monkeypatch.setattr(
        encoder_window.settings,
        "load_settings",
        lambda: {
            "default_source_paths": [],
            "default_output_directory": "",
            "ffmpeg_path": "",
            "ffprobe_path": "",
        },
    )
    monkeypatch.setattr(
        encoder_window,
        "discover_tools",
        lambda: FFmpegTools(Path("/ffmpeg"), Path("/ffprobe")),
    )
    screen = encoder_window.VideoEncoderScreen(lambda: None)
    try:
        screen.path_input.setPlainText(f"{first}\n{second}")
        first_row = screen.path_input._tree.topLevelItem(0)
        second_row = screen.path_input._tree.topLevelItem(1)
        first_row.setSelected(True)
        second_row.setSelected(True)

        screen._set_selected_rows_checked(False)

        assert screen.path_input.paths() == [first, second]
        assert screen.path_input.selected_paths() == []
        assert screen.path_input.row_selected_paths() == [first, second]
        menu = QMenu()
        screen._add_input_path_context_menu(menu, first_row)
        assert any(action.text() == "青い選択行" for action in menu.actions())
        assert any(action.text() == "複数のパス・ファイル名をコピー" for action in menu.actions())
    finally:
        screen.close()


def test_video_encoder_path_undo_redo_restores_only_list_and_checks(
    tmp_path: Path, monkeypatch
):
    QApplication.instance() or QApplication([])
    movie = tmp_path / "movie.mp4"
    monkeypatch.setattr(
        encoder_window.settings,
        "load_settings",
        lambda: {
            "default_source_paths": [],
            "default_output_directory": "",
            "ffmpeg_path": "",
            "ffprobe_path": "",
        },
    )
    monkeypatch.setattr(
        encoder_window,
        "discover_tools",
        lambda: FFmpegTools(Path("/ffmpeg"), Path("/ffprobe")),
    )
    screen = encoder_window.VideoEncoderScreen(lambda: None)
    try:
        screen.output_suffix_input.setText("_keep")
        screen.path_input.append_items((str(movie),))
        row = screen.path_input._tree.topLevelItem(0)
        screen.path_input._set_item_selected(row, False)

        screen.undo_path_change()
        assert screen.path_input.paths() == [movie]
        assert screen.path_input.selected_paths() == [movie]
        assert screen.output_suffix_input.text() == "_keep"

        screen.undo_path_change()
        assert screen.path_input.paths() == []
        assert screen.output_suffix_input.text() == "_keep"

        screen.redo_path_change()
        screen.redo_path_change()
        assert screen.path_input.paths() == [movie]
        assert screen.path_input.selected_paths() == []
        assert screen.output_suffix_input.text() == "_keep"
    finally:
        screen.close()


def test_video_encode_output_suffix_defaults_safely_and_can_be_changed(tmp_path: Path, monkeypatch):
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"source")
    monkeypatch.setattr(video_encode, "_probe_video_basics", lambda *_args: (30.0, True))
    tools = FFmpegTools(Path("/ffmpeg"), Path("/ffprobe"))

    default_preview = build_encode_preview(
        tools, VideoEncodeRequest((source,), tmp_path, "h264", 20, output_suffix="")
    )
    custom_preview = build_encode_preview(
        tools, VideoEncodeRequest((source,), tmp_path, "h264", 20, output_suffix="_compressed")
    )

    assert default_preview.plan is not None
    assert default_preview.plan.items[0].output.name == "clip_encoded.mp4"
    assert custom_preview.plan is not None
    assert custom_preview.plan.items[0].output.name == "clip_compressed.mp4"


def test_normal_encode_can_plan_multiple_original_timeline_rules(tmp_path: Path, monkeypatch):
    source = tmp_path / "clip.mp4"
    image = tmp_path / "mark.png"
    source.write_bytes(b"source")
    image.write_bytes(b"image")
    monkeypatch.setattr(video_encode, "_probe_video_basics", lambda *_args: (30.0, True))
    monkeypatch.setattr(video_encode, "_probe_duration", lambda *_args: 120.0)
    request = VideoEncodeRequest(
        (source,), tmp_path, "h264", 20,
        resolution=(1280, 720),
        edit_rules=(
            video_encode.VideoEditRule("mosaic", "seconds", 10, 20, 4, 8, 100, 80, 10),
            video_encode.VideoEditRule("image_overlay", "frames", 900, 1200, 12, 16, image_path=image),
            video_encode.VideoEditRule("cut", "seconds", 40, 45),
        ),
    )

    preview = build_encode_preview(FFmpegTools(Path("/ffmpeg"), Path("/ffprobe")), request)

    assert preview.plan is not None
    command = " ".join(preview.plan.items[0].command)
    assert "overlay=4:8" in command
    assert "overlay=12:16" in command
    assert "concat=n=2:v=1:a=0" in command
    assert "scale=1280:720:force_original_aspect_ratio=decrease" in command


def test_size_target_uses_video_bitrate_instead_of_crf(tmp_path: Path, monkeypatch):
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"source")
    monkeypatch.setattr(video_encode, "_probe_video_basics", lambda *_args: (30.0, True))
    monkeypatch.setattr(video_encode, "_probe_duration", lambda *_args: 120.0)

    preview = build_encode_preview(
        FFmpegTools(Path("/ffmpeg"), Path("/ffprobe")),
        VideoEncodeRequest((source,), tmp_path, "h264", 20, target_size_mib=100, audio_bitrate_kbps=128),
    )

    assert preview.plan is not None
    command = preview.plan.items[0].command
    assert "-b:v" in command
    assert "-crf" not in command
    assert "サイズ指定: 1動画あたり約 100 MiB" in preview.text


def test_keyframe_cut_expands_to_safe_boundaries_and_keeps_stream_copy_plan(tmp_path: Path, monkeypatch):
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"source")
    monkeypatch.setattr(
        video_encode,
        "_probe_duration_and_keyframes",
        lambda *_args: (60.0, (0.0, 12.5, 38.72, 44.18, 59.0)),
    )

    preview = build_encode_preview(
        FFmpegTools(Path("/ffmpeg"), Path("/ffprobe")),
        VideoEncodeRequest(
            (source,), tmp_path, "h264", 20,
            encode_mode="keyframe_cut", cut_start_seconds=39.0, cut_end_seconds=43.0,
        ),
    )

    assert preview.plan is not None
    item = preview.plan.items[0]
    assert item.command == ()
    assert item.keyframe_cut is not None
    assert item.keyframe_cut.effective_start == 38.72
    assert item.keyframe_cut.effective_end == 44.18
    assert "指定削除: 39.000秒〜43.000秒" in preview.text
    assert "実際削除: 38.720秒〜44.180秒" in preview.text
    manifest = keyframe_cut_concat_text(item)
    assert "outpoint 38.720000000" in manifest
    assert "inpoint 44.180000000" in manifest


def test_video_encoder_requires_a_new_preview_after_any_output_change(tmp_path: Path, monkeypatch):
    QApplication.instance() or QApplication([])
    monkeypatch.setattr(
        encoder_window.settings,
        "load_settings",
        lambda: {
            "default_source_paths": [],
            "default_output_directory": str(tmp_path),
            "ffmpeg_path": "",
            "ffprobe_path": "",
        },
    )
    monkeypatch.setattr(
        encoder_window,
        "discover_tools",
        lambda: FFmpegTools(Path("/ffmpeg"), Path("/ffprobe")),
    )
    screen = encoder_window.VideoEncoderScreen(lambda: None)
    try:
        screen._plan = object()  # type: ignore[assignment]
        screen.execute_button.setEnabled(True)
        screen.preview.setPlainText("前回の変換計画")

        screen.output_suffix_input.setText("_rechecked")

        assert screen._plan is None
        assert not screen.execute_button.isEnabled()
        assert "もう一度" in screen.preview.toPlainText()
    finally:
        screen.close()


def test_video_encoder_charge_wait_holds_and_releases_only_at_safe_boundaries(tmp_path: Path, monkeypatch):
    QApplication.instance() or QApplication([])
    monkeypatch.setattr(
        encoder_window.settings,
        "load_settings",
        lambda: {
            "default_source_paths": [],
            "default_output_directory": str(tmp_path),
            "ffmpeg_path": "",
            "ffprobe_path": "",
        },
    )
    monkeypatch.setattr(
        encoder_window,
        "discover_tools",
        lambda: FFmpegTools(Path("/ffmpeg"), Path("/ffprobe")),
    )
    state = [PowerStatus(25, "Charging", True, 1)]
    monkeypatch.setattr(encoder_window, "read_power_status", lambda: state[0])
    screen = encoder_window.VideoEncoderScreen(lambda: None)
    try:
        screen.charge_wait_check.setChecked(True)
        existing_plan = object()
        completed = tmp_path / "completed.mp4"
        screen._plan = existing_plan  # type: ignore[assignment]
        screen._queue_index = 2
        screen._completed_outputs = [completed]
        screen.execute_button.setEnabled(True)

        assert screen._wait_for_charge_before_next_item()
        assert screen._charge_waiting
        assert screen._charge_poll_timer.isActive()
        assert "25%" in screen.charge_status.text()
        assert "成功 1件" in screen.charge_status.text()
        assert "成功 1件" in screen.notice.toPlainText()
        assert not screen.preview_button.isEnabled()
        assert not screen.execute_button.isEnabled()
        assert not screen.input_box.isEnabled()

        screen.update_preview()
        screen.execute_plan()

        assert screen._plan is existing_plan
        assert screen._queue_index == 2
        assert screen._completed_outputs == [completed]
        assert screen._temporary_run_directory is None

        state[0] = PowerStatus(60, "Charging", True, 1)

        assert not screen._wait_for_charge_before_next_item()
        assert not screen._charge_waiting
        assert not screen._charge_poll_timer.isActive()
        screen._update_execution_buttons()
        assert screen.preview_button.isEnabled()
        assert screen.input_box.isEnabled()
    finally:
        screen._stop_charge_wait()
        screen.close()


def test_video_encoder_settings_migrate_old_paths_to_role_based_entries(tmp_path: Path, monkeypatch):
    path = tmp_path / "video_encoder.json"
    monkeypatch.setattr(settings, "SETTINGS_PATH", path)

    saved = settings.save_text(
        """{
          "default_source_paths": ["/home/example"],
          "default_output_directory": "/home/example/output",
          "ffmpeg_path": "/opt/ffmpeg",
          "ffprobe_path": "/opt/ffprobe"
        }"""
    )

    assert settings.paths_for(saved, "initial_source") == ["/home/example"]
    assert settings.paths_for(saved, "initial_output") == ["/home/example/output"]
    assert settings.load_settings() == saved
    written = json.loads(path.read_text(encoding="utf-8"))
    assert written["path_settings"] == [
        {"path": "/home/example", "initial_source": True},
        {"path": "/home/example/output", "initial_output": True},
    ]
    with pytest.raises(ValueError, match="未対応"):
        settings.validate_text('{"ffmpeg_path": "", "history": ["x"]}')


def test_video_encoder_path_roles_allow_omitted_or_explicit_false(tmp_path: Path, monkeypatch):
    path = tmp_path / "video_encoder.json"
    monkeypatch.setattr(settings, "SETTINGS_PATH", path)

    saved = settings.save_text(
        """{
          "path_settings": [
            {"path": "/video/a", "context_menu": true},
            {"path": "/video/out", "initial_source": false, "initial_output": true},
            {"path": "/video/legacy", "output_context_menu": true},
            {"path": ""}
          ],
          "ffmpeg_path": "",
          "ffprobe_path": ""
        }"""
    )

    assert settings.paths_for(saved, "context_menu") == ["/video/a", "/video/legacy"]
    assert settings.paths_for(saved, "initial_source") == []
    assert settings.paths_for(saved, "initial_output") == ["/video/out"]
    written = json.loads(path.read_text(encoding="utf-8"))
    assert written["path_settings"] == [
        {"path": "/video/a", "context_menu": True},
        {"path": "/video/out", "initial_output": True},
        {"path": "/video/legacy", "context_menu": True},
        {"path": ""},
    ]


def test_video_encoder_registered_paths_are_shared_by_both_context_menus(tmp_path: Path, monkeypatch):
    QApplication.instance() or QApplication([])
    source = tmp_path / "registered.mp4"
    output = tmp_path / "output"
    monkeypatch.setattr(
        encoder_window.settings,
        "load_settings",
        lambda: {
            "path_settings": [
                {
                    "path": str(source),
                    "initial_source": False,
                    "initial_output": False,
                    "context_menu": True,
                },
                {
                    "path": str(output),
                    "initial_source": False,
                    "initial_output": False,
                    "context_menu": True,
                },
            ],
            "ffmpeg_path": "",
            "ffprobe_path": "",
        },
    )
    monkeypatch.setattr(
        encoder_window,
        "discover_tools",
        lambda: FFmpegTools(Path("/ffmpeg"), Path("/ffprobe")),
    )
    screen = encoder_window.VideoEncoderScreen(lambda: None)
    try:
        source_menu = QMenu()
        screen.path_input._augment_context_menu(source_menu, None)
        source_submenu = source_menu.actions()[-1].menu()
        assert source_submenu is not None
        assert len(source_submenu.actions()) == 2
        assert source_submenu.actions()[0].text() == f"{source.name} — {source.parent}"
        assert source_submenu.actions()[0].toolTip() == str(source)
        source_submenu.actions()[0].trigger()
        assert str(source) in screen.path_input.items()

        output_menu = screen.output_directory_input._build_context_menu()
        output_submenu = output_menu.actions()[-1].menu()
        assert output_submenu is not None
        assert len(output_submenu.actions()) == 2
        assert output_submenu.actions()[1].text() == f"{output.name} — {output.parent}"
        assert output_submenu.actions()[0].toolTip() == str(source)
        output_submenu.actions()[0].trigger()
        assert screen.output_mode_combo.currentData() == "specified_directory"
        assert screen.output_directory_input.text() == str(tmp_path)
    finally:
        screen.close()


def test_video_encoder_settings_help_explains_every_path_attribute():
    help_text = settings.help_text()

    assert "initial_source" in help_text
    assert "initial_output" in help_text
    assert "context_menu" in help_text
    assert "両方の右クリック候補" in help_text
    assert '"path": "@HOME/Videos"' in help_text


def test_video_encoder_path_handoff_is_in_memory_and_consumed_once(tmp_path: Path):
    source = tmp_path / "clip.mp4"

    assert offer_video_encode_paths([source, source]) == 1
    assert take_video_encode_paths() == (str(source.resolve()),)
    assert take_video_encode_paths() == ()
