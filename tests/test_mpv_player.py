from __future__ import annotations

from pathlib import Path

import pytest

from media.mpv_player import (
    create_isolated_mpv_session,
    default_shortcut_settings,
    effective_shortcut_bindings,
    find_mpv,
    validate_shortcut_settings,
)


def test_find_mpv_accepts_only_an_executable_file(tmp_path: Path) -> None:
    executable = tmp_path / "mpv"
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o755)

    assert find_mpv(str(executable)) == executable
    assert find_mpv(str(tmp_path / "missing")) is None


def test_isolated_session_uses_only_disposable_writable_locations(tmp_path: Path) -> None:
    executable = tmp_path / "mpv"
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o755)
    media = tmp_path / "movie.mp4"
    media.write_bytes(b"test")

    session = create_isolated_mpv_session(media, configured_path=str(executable))
    directory = session.directory
    environment = session.environment({"HOME": "/home/example", "MPV_HOME": "/old"})

    assert "--no-config" in session.arguments
    assert "--save-position-on-quit=no" in session.arguments
    assert "--keep-open=always" in session.arguments
    assert "--playlist-start=0" in session.arguments
    assert "--force-media-title=${filename}" in session.arguments
    assert not any(argument.startswith("--osd-playing-msg=") for argument in session.arguments)
    assert "--osc=yes" in session.arguments
    assert not any(argument.startswith("--video-margin-ratio-bottom=") for argument in session.arguments)
    assert "--osd-align-x=left" in session.arguments
    assert "--osd-align-y=bottom" in session.arguments
    assert "--osd-margin-x=24" in session.arguments
    assert "--osd-margin-y=30" in session.arguments
    assert "--script-opts=osc-layout=slimbox,osc-valign=1,osc-barmargin=0,osc-hidetimeout=1000,osc-timetotal=yes" in session.arguments
    assert session.script_path.is_file()
    assert session.event_path.is_file()
    assert "mp.add_forced_key_binding(\"m\"" in session.script_path.read_text(encoding="utf-8")
    assert "MPV_HOME" not in environment
    assert environment["PORTA_MPV_RESUME_REQUEST_FILE"] == str(session.resume_request_path)
    assert all(
        Path(environment[name]).is_relative_to(directory)
        for name in ("HOME", "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME")
    )

    session.cleanup()
    assert not directory.exists()


def test_isolated_session_can_start_at_a_later_playlist_item(tmp_path: Path) -> None:
    executable = tmp_path / "mpv"
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o755)
    first = tmp_path / "first.mp4"
    second = tmp_path / "second.mp4"
    first.write_bytes(b"test")
    second.write_bytes(b"test")

    session = create_isolated_mpv_session(
        (first, second), configured_path=str(executable), playlist_start_index=1
    )

    assert session.media_paths == (first.absolute(), second.absolute())
    assert "--playlist-start=1" in session.arguments
    session.cleanup()


def test_isolated_session_rejects_non_file_media(tmp_path: Path) -> None:
    executable = tmp_path / "mpv"
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o755)

    with pytest.raises(ValueError, match="存在するファイル"):
        create_isolated_mpv_session(tmp_path / "missing.mp4", configured_path=str(executable))


def test_shortcut_template_keeps_required_keys_and_five_custom_keys_enabled() -> None:
    shortcuts = default_shortcut_settings()

    assert all(entry["enabled"] for entry in shortcuts["アプリ連携キー"].values())
    assert shortcuts["アプリ連携キー"]["評価 1"]["key"] == "1"
    assert shortcuts["アプリ連携キー"]["評価 10"]["key"] == "0"
    assert len(shortcuts["任意mpvキー"]) == 10
    assert all(entry["enabled"] for entry in shortcuts["任意mpvキー"][:5])
    assert not any(entry["enabled"] for entry in shortcuts["任意mpvキー"][5:])
    assert shortcuts["任意mpvキー"][0]["key"] == "r"
    assert shortcuts["任意mpvキー"][0]["mpv_command"] == "cycle-values video-rotate 0 90 180 270"
    assert shortcuts["任意mpvキー"][3]["key"] == "Ctrl+Right"
    assert shortcuts["任意mpvキー"][4]["key"] == "Ctrl+Left"
    assert shortcuts["任意mpvキー"][3]["mpv_command"] == "seek 10 exact"
    assert shortcuts["任意mpvキー"][4]["mpv_command"] == "seek -10 exact"
    assert not shortcuts["任意mpvキー"][5]["enabled"]
    assert not shortcuts["任意mpvキー"][6]["enabled"]


def test_shortcut_validation_upgrades_only_legacy_blank_slots() -> None:
    legacy = default_shortcut_settings()
    legacy["任意mpvキー"][3] = {"enabled": False, "label": "", "key": "", "mpv_command": ""}
    legacy["任意mpvキー"][4] = {"enabled": False, "label": "", "key": "", "mpv_command": ""}

    upgraded = validate_shortcut_settings(legacy)

    assert upgraded["任意mpvキー"][3]["key"] == "Ctrl+Right"
    assert upgraded["任意mpvキー"][4]["key"] == "Ctrl+Left"


def test_shortcut_validation_retires_the_old_slash_skip_defaults() -> None:
    legacy = default_shortcut_settings()
    legacy["任意mpvキー"][5] = {
        "enabled": True, "label": "10秒戻る", "key": "/", "mpv_command": "seek -10 exact"
    }
    legacy["任意mpvキー"][6] = {
        "enabled": True, "label": "10秒進む", "key": "\\", "mpv_command": "seek 10 exact"
    }

    upgraded = validate_shortcut_settings(legacy)

    assert not upgraded["任意mpvキー"][5]["enabled"]
    assert not upgraded["任意mpvキー"][6]["enabled"]


def test_shortcut_validation_upgrades_only_the_unchanged_old_three_second_defaults() -> None:
    legacy = default_shortcut_settings()
    legacy["任意mpvキー"][3] = {
        "enabled": True,
        "label": "3秒進む",
        "key": "Ctrl+Right",
        "mpv_command": "seek 3 exact",
    }
    legacy["任意mpvキー"][4] = {
        "enabled": True,
        "label": "3秒戻る",
        "key": "Ctrl+Left",
        "mpv_command": "seek -3 exact",
    }

    upgraded = validate_shortcut_settings(legacy)

    assert upgraded["任意mpvキー"][3] == {
        "enabled": True,
        "label": "10秒進む",
        "key": "Ctrl+Right",
        "mpv_command": "seek 10 exact",
    }
    assert upgraded["任意mpvキー"][4] == {
        "enabled": True,
        "label": "10秒戻る",
        "key": "Ctrl+Left",
        "mpv_command": "seek -10 exact",
    }


def test_shortcut_validation_adds_the_new_tag_key_to_existing_settings() -> None:
    legacy = default_shortcut_settings()
    del legacy["アプリ連携キー"]["タグを選ぶ"]
    del legacy["アプリ連携キー"]["見どころ範囲の開始（コメントなし）"]
    del legacy["アプリ連携キー"]["見どころ範囲の終了（コメントなし）"]

    upgraded = validate_shortcut_settings(legacy)

    assert upgraded["アプリ連携キー"]["タグを選ぶ"] == {"enabled": True, "key": "t"}
    assert upgraded["アプリ連携キー"]["見どころ範囲の終了（コメントなし）"] == {
        "enabled": True,
        "key": "\\",
    }
    assert upgraded["アプリ連携キー"]["見どころ範囲の開始（コメントなし）"] == {
        "enabled": True,
        "key": "/",
    }


def test_shortcut_validation_makes_all_custom_seeks_exact() -> None:
    shortcuts = default_shortcut_settings()
    shortcuts["任意mpvキー"][5] = {
        "enabled": True,
        "label": "15秒進む",
        "key": "F6",
        "mpv_command": "seek 15 relative+keyframes",
    }

    normalized = validate_shortcut_settings(shortcuts)

    assert normalized["任意mpvキー"][5]["mpv_command"] == "seek 15 relative+exact"


def test_effective_shortcuts_show_workflow_defaults_after_local_overrides() -> None:
    bindings = effective_shortcut_bindings(default_shortcut_settings())
    by_key = {binding.key: binding for binding in bindings}

    assert by_key["r"].action == "画面を90度回転"
    assert by_key["n"].action == "次の動画"
    assert by_key["p"].action == "前の動画"
    assert by_key["m"].action == "見どころ地点を記録"
    assert by_key["Right"].action == "5秒進む"
    assert by_key["Space"].action == "再生 / 一時停止"
    assert not any(binding.key == "[" and binding.source == "mpv標準" for binding in bindings)
    rating = next(binding for binding in bindings if binding.action.startswith("評価を記録"))
    assert rating.key == "1、2、3、4、5、6、7、8、9、0"


def test_effective_shortcuts_restore_or_override_mpv_defaults_as_configured() -> None:
    shortcuts = default_shortcut_settings()
    shortcuts["アプリ連携キー"]["見どころ地点を記録"]["enabled"] = False
    shortcuts["任意mpvキー"][5] = {
        "enabled": True,
        "label": "15秒進む",
        "key": "Right",
        "mpv_command": "seek 15",
    }

    bindings = effective_shortcut_bindings(shortcuts)
    by_key = {binding.key: binding for binding in bindings}

    assert by_key["m"].action == "ミュートを切替"
    assert by_key["Right"].action == "15秒進む"
    assert by_key["Right"].source == "永続設定の任意キー"


def test_shortcut_rendering_gives_app_binding_priority_and_uses_custom_playlist_navigation(tmp_path: Path) -> None:
    executable = tmp_path / "mpv"
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o755)
    media = tmp_path / "movie.mp4"
    media.write_bytes(b"test")
    shortcuts = default_shortcut_settings()
    shortcuts["任意mpvキー"][0]["enabled"] = True
    shortcuts["任意mpvキー"][0]["key"] = "m"

    session = create_isolated_mpv_session(media, configured_path=str(executable), shortcuts=shortcuts)
    script = session.script_path.read_text(encoding="utf-8")

    assert 'mp.add_forced_key_binding("n"' in script
    assert 'mp.command("playlist-next")' in script
    assert "playlist_next" not in script
    assert "画面を90度回転" in session.binding_warnings[0]
    session.cleanup()


def test_shortcut_rendering_shows_transient_osd_feedback_for_actions(tmp_path: Path) -> None:
    executable = tmp_path / "mpv"
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o755)
    media = tmp_path / "movie.mp4"
    media.write_bytes(b"test")

    session = create_isolated_mpv_session(media, configured_path=str(executable))
    script = session.script_path.read_text(encoding="utf-8")

    assert "mp.osd_message(text, 1600)" in script
    assert "if serial == notification_serial then mp.osd_message('', 0) end" in script
    assert "mp.register_event('file-loaded', force_filename_title)" in script
    assert "mp.create_osd_overlay" not in script
    assert "見どころ地点を記録: " in script
    assert "見どころ範囲の開始: " in script
    assert "コメント入力を開きます" in script
    assert "mp.set_property_native('pause', true)" in script
    assert "見どころメモ: 一時停止して入力欄を開きます" in script
    assert "評価: 10 / 10" in script
    assert 'mp.command("playlist-next"); show_controller(); notify("次の動画")' in script
    session.cleanup()


def test_tag_selection_key_is_bound_only_for_review_patch_playback(tmp_path: Path) -> None:
    executable = tmp_path / "mpv"
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o755)
    media = tmp_path / "movie.mp4"
    media.write_bytes(b"test")

    ordinary = create_isolated_mpv_session(media, configured_path=str(executable))
    review = create_isolated_mpv_session(
        media, configured_path=str(executable), enable_tag_selection=True
    )

    assert 'mp.add_forced_key_binding("t"' not in ordinary.script_path.read_text(encoding="utf-8")
    script = review.script_path.read_text(encoding="utf-8")
    assert 'mp.add_forced_key_binding("t"' in script
    assert "タグを選択して追加します" in script
    assert 'emit("tag_selection", "")' in script
    ordinary.cleanup()
    review.cleanup()


def test_rating_keys_can_be_excluded_for_read_only_playback(tmp_path: Path) -> None:
    executable = tmp_path / "mpv"
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o755)
    media = tmp_path / "movie.mp4"
    media.write_bytes(b"test")

    session = create_isolated_mpv_session(
        media, configured_path=str(executable), enable_rating_selection=False
    )

    script = session.script_path.read_text(encoding="utf-8")
    assert 'mp.add_forced_key_binding("1"' not in script
    assert "評価: 10 / 10" not in script
    session.cleanup()


def test_slash_and_backslash_record_a_range_without_opening_a_comment_prompt(tmp_path: Path) -> None:
    executable = tmp_path / "mpv"
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o755)
    media = tmp_path / "movie.mp4"
    media.write_bytes(b"test")

    session = create_isolated_mpv_session(media, configured_path=str(executable))
    script = session.script_path.read_text(encoding="utf-8")

    bindings = {binding.key: binding for binding in effective_shortcut_bindings(default_shortcut_settings())}
    assert bindings["/"].action == "見どころ範囲の開始（コメントなし）"
    assert bindings["\\"].action == "見どころ範囲の終了（コメントなし）"
    assert "Shift+]" not in script
    assert 'emit("highlight_range_end_no_comment", "")' in script
    session.cleanup()


def test_seconds_based_seek_keys_show_the_operation_and_reveal_slimbox(tmp_path: Path) -> None:
    executable = tmp_path / "mpv"
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o755)
    media = tmp_path / "movie.mp4"
    media.write_bytes(b"test")
    shortcuts = default_shortcut_settings()
    shortcuts["任意mpvキー"][5] = {
        "enabled": True,
        "label": "15秒進む",
        "key": "F6",
        "mpv_command": "seek 15",
    }

    session = create_isolated_mpv_session(media, configured_path=str(executable), shortcuts=shortcuts)
    script = session.script_path.read_text(encoding="utf-8")

    assert 'mp.add_forced_key_binding("RIGHT"' in script
    assert "local function show_controller()" in script
    assert "mp.commandv('script-message-to', 'osc', 'osc-show')" in script
    assert 'show_controller(); notify("5秒進む"); mp.command("no-osd seek 5 exact")' in script
    assert 'mp.command("no-osd seek 5 relative+keyframes")' in script
    assert 'mp.command("no-osd seek -5 relative+keyframes")' in script
    assert 'show_controller(); notify("15秒進む"); mp.command("no-osd seek 15 exact")' in script
    assert "notify_seek_result" not in script
    session.cleanup()


def test_frame_step_keys_keep_mpv_native_tap_and_hold_behavior(tmp_path: Path) -> None:
    executable = tmp_path / "mpv"
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o755)
    media = tmp_path / "movie.mp4"
    media.write_bytes(b"test")

    session = create_isolated_mpv_session(media, configured_path=str(executable))
    script = session.script_path.read_text(encoding="utf-8")

    assert 'mp.add_forced_key_binding(".",' not in script
    assert 'mp.add_forced_key_binding(",",' not in script
    session.cleanup()


def test_session_can_send_a_temporary_resume_request(tmp_path: Path) -> None:
    executable = tmp_path / "mpv"
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o755)
    media = tmp_path / "movie.mp4"
    media.write_bytes(b"test")

    session = create_isolated_mpv_session(media, configured_path=str(executable))
    script = session.script_path.read_text(encoding="utf-8")

    assert "mp.add_periodic_timer(0.1, consume_resume_request)" in script
    assert session.request_resume()
    assert session.resume_request_path.is_file()
    session.cleanup()


def test_shortcut_validation_rejects_duplicate_required_keys_and_external_commands() -> None:
    duplicate = default_shortcut_settings()
    duplicate["アプリ連携キー"]["見どころコメントを入力"]["key"] = "m"
    with pytest.raises(ValueError, match="重複"):
        validate_shortcut_settings(duplicate)

    unsafe = default_shortcut_settings()
    unsafe["任意mpvキー"][0] = {
        "enabled": True,
        "label": "外部実行",
        "key": "F12",
        "mpv_command": "run rm -rf /",
    }
    with pytest.raises(ValueError, match="外部実行"):
        validate_shortcut_settings(unsafe)


def test_session_reads_only_new_escaped_events(tmp_path: Path) -> None:
    executable = tmp_path / "mpv"
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o755)
    media = tmp_path / "movie.mp4"
    media.write_bytes(b"test")
    session = create_isolated_mpv_session(media, configured_path=str(executable))
    session.event_path.write_text(
        "highlight_comment\t/a%250A.mp4\t12.500\tline%0Aone\n", encoding="utf-8"
    )

    events = session.read_events()

    assert events[0].media_path == "/a%0A.mp4"
    assert events[0].value == "line\none"
    assert session.read_events() == ()
    session.cleanup()
