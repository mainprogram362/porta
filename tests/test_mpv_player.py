from __future__ import annotations

from pathlib import Path
import subprocess
import sys

import pytest

from media.mpv_player import (
    begin_mpv_replacement,
    copy_porta_settings_to_normal_mpv,
    create_isolated_mpv_session,
    default_shortcut_settings,
    effective_shortcut_bindings,
    find_mpv,
    install_normal_mpv_replace_handler,
    normal_mpv_config_directory,
    release_owned_mpv,
    validate_shortcut_settings,
)


def test_find_mpv_accepts_only_an_executable_file(tmp_path: Path) -> None:
    executable = tmp_path / "mpv"
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o755)

    assert find_mpv(str(executable)) == executable
    assert find_mpv(str(tmp_path / "missing")) is None


def test_normal_mpv_config_directory_honors_explicit_mpv_home(tmp_path: Path) -> None:
    assert normal_mpv_config_directory({"MPV_HOME": str(tmp_path / "custom")}) == tmp_path / "custom"
    assert normal_mpv_config_directory({"XDG_CONFIG_HOME": str(tmp_path / "xdg")}) == tmp_path / "xdg" / "mpv"


def test_copy_porta_settings_adds_visible_managed_blocks_without_overwriting_user_values(tmp_path: Path) -> None:
    config_dir = tmp_path / "mpv"
    config_dir.mkdir()
    (config_dir / "mpv.conf").write_text("autocreate-playlist=no\n# user setting\n", encoding="utf-8")
    (config_dir / "input.conf").write_text("n playlist-next\n# user binding\n", encoding="utf-8")

    result = copy_porta_settings_to_normal_mpv(default_shortcut_settings(), config_directory=config_dir)

    mpv_conf = (config_dir / "mpv.conf").read_text(encoding="utf-8")
    input_conf = (config_dir / "input.conf").read_text(encoding="utf-8")
    assert "autocreate-playlist=no" in mpv_conf
    assert "# >>> PORTA shared playback settings >>>" in mpv_conf
    assert "autocreate-playlist=filter" not in mpv_conf
    assert "# >>> PORTA shared key bindings >>>" in input_conf
    assert "n playlist-next" in input_conf
    assert "N playlist-next" not in input_conf
    assert "Ctrl+RIGHT seek 10 exact" in input_conf
    assert "評価 1-10" not in input_conf
    assert ": porta-hold-double-speed" not in input_conf
    assert "開いたファイルと同じフォルダをプレイリストにする" in result.skipped_options
    assert "n: 次の動画" in result.skipped_bindings
    assert not result.copied_options

    # Re-running replaces only the managed blocks instead of duplicating them.
    copy_porta_settings_to_normal_mpv(default_shortcut_settings(), config_directory=config_dir)
    assert (config_dir / "mpv.conf").read_text(encoding="utf-8").count(
        "# >>> PORTA shared playback settings >>>"
    ) == 1
    assert (config_dir / "input.conf").read_text(encoding="utf-8").count(
        "# >>> PORTA shared key bindings >>>"
    ) == 1


def test_normal_mpv_settings_leave_an_existing_playlist_policy_unchanged(tmp_path: Path) -> None:
    config_dir = tmp_path / "mpv"
    config_dir.mkdir()
    (config_dir / "mpv.conf").write_text("autocreate-playlist=no\n", encoding="utf-8")

    result = copy_porta_settings_to_normal_mpv(default_shortcut_settings(), config_directory=config_dir)

    text = (config_dir / "mpv.conf").read_text(encoding="utf-8")
    assert "autocreate-playlist=no" in text
    assert "autocreate-playlist=filter" not in text
    assert "開いたファイルと同じフォルダをプレイリストにする" in result.skipped_options


def test_mpv_replacement_stops_only_the_recorded_player_and_releases_on_exit(tmp_path: Path) -> None:
    directory = tmp_path / "mpv-owners"
    first = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        with begin_mpv_replacement("porta", directory=directory) as replacement:
            replacement.claim(first.pid)
        with begin_mpv_replacement("porta", directory=directory) as replacement:
            assert first.wait(timeout=2) != 0
            second = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
            replacement.claim(second.pid)
        second.terminate()
        second.wait(timeout=2)
        release_owned_mpv("porta", second.pid, directory=directory)
        assert not (directory / "porta.json").exists()
    finally:
        if first.poll() is None:
            first.terminate()
            first.wait(timeout=2)
        if "second" in locals() and second.poll() is None:
            second.terminate()
            second.wait(timeout=2)


def test_normal_mpv_handler_installs_a_private_wrapper_and_desktop_entry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    executable = tmp_path / "python"
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o755)
    relay = tmp_path / "porta_mpv_open.py"
    relay.write_text("# relay\n", encoding="utf-8")

    class Completed:
        returncode = 0

    monkeypatch.setattr("media.mpv_player.subprocess.run", lambda *_args, **_kwargs: Completed())
    result = install_normal_mpv_replace_handler(
        home=tmp_path / "home", python_executable=executable, relay_script=relay
    )

    assert result.wrapper_path.stat().st_mode & 0o777 == 0o700
    assert "porta_mpv_open.py" in result.wrapper_path.read_text(encoding="utf-8")
    desktop = result.desktop_path.read_text(encoding="utf-8")
    assert "Exec=" in desktop
    assert "video/mp4" in desktop
    assert not result.failed_mime_types


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
    assert "--af=scaletempo2" in session.arguments
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
    assert environment["PORTA_MPV_TAG_SELECTION_DONE_FILE"] == str(session.tag_selection_done_path)
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

    assert all(entry["enabled"] for entry in shortcuts["中枢キー"].values())
    assert shortcuts["中枢キー"]["見どころ範囲"] == {
        "enabled": True,
        "start_key": "[",
        "end_key": "]",
    }
    assert shortcuts["中枢キー"]["評価 1-10"]["key_sequence"] == "1-9, 0"
    assert len(shortcuts["任意mpvキー"]) == 6
    assert all(entry["enabled"] for entry in shortcuts["任意mpvキー"][:5])
    assert shortcuts["任意mpvキー"][5] == {
        "enabled": False,
        "label": "",
        "key": "",
        "mpv_command": "",
    }
    assert shortcuts["任意mpvキー"][0]["key"] == "r"
    assert shortcuts["任意mpvキー"][0]["mpv_command"] == "cycle-values video-rotate 0 90 180 270"
    assert shortcuts["任意mpvキー"][3]["key"] == "Ctrl+Right"
    assert shortcuts["任意mpvキー"][4]["key"] == "Ctrl+Left"
    assert shortcuts["任意mpvキー"][3]["mpv_command"] == "seek 10 exact"
    assert shortcuts["任意mpvキー"][4]["mpv_command"] == "seek -10 exact"


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
    legacy["任意mpvキー"].append({
        "enabled": True, "label": "10秒進む", "key": "\\", "mpv_command": "seek 10 exact"
    })

    upgraded = validate_shortcut_settings(legacy)

    assert upgraded["任意mpvキー"][-1] == {
        "enabled": False,
        "label": "",
        "key": "",
        "mpv_command": "",
    }
    assert len(upgraded["任意mpvキー"]) == 6


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
    legacy.pop("中枢キー")
    legacy["アプリ連携キー"] = {
        "見どころ範囲の開始": {"enabled": True, "key": "["},
        "見どころ範囲の終了": {"enabled": True, "key": "]"},
        **{f"評価 {score}": {"enabled": True, "key": str(score % 10)} for score in range(1, 11)},
        "見どころ地点を記録": {"enabled": True, "key": "m"},
        "見どころ範囲の開始（コメントなし）": {"enabled": True, "key": "/"},
        "見どころ範囲の終了（コメントなし）": {"enabled": True, "key": "\\"},
        "見どころコメントを入力": {"enabled": True, "key": "c"},
    }

    upgraded = validate_shortcut_settings(legacy)

    assert upgraded["中枢キー"]["タグを選ぶ"] == {"enabled": True, "key": "t"}
    assert upgraded["中枢キー"]["評価 1-10"]["key_sequence"] == "1-9, 0"
    assert "アプリ連携キー" not in upgraded


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
    assert by_key["m"].action == "ミュートを切替"
    assert by_key["Right"].action == "5秒進む"
    assert by_key["Space"].action == "再生 / 一時停止"
    assert by_key[":"].action == "押している間だけ2倍速"
    assert not any(binding.key == "[" and binding.source == "mpv標準" for binding in bindings)
    rating = next(binding for binding in bindings if binding.action.startswith("評価を記録"))
    assert rating.key == "1、2、3、4、5、6、7、8、9、0"


def test_effective_shortcuts_restore_or_override_mpv_defaults_as_configured() -> None:
    shortcuts = default_shortcut_settings()
    shortcuts["中枢キー"]["見どころ範囲"]["enabled"] = False
    shortcuts["任意mpvキー"][5] = {
        "enabled": True,
        "label": "15秒進む",
        "key": "Right",
        "mpv_command": "seek 15",
    }

    bindings = effective_shortcut_bindings(shortcuts)
    by_key = {binding.key: binding for binding in bindings}

    assert by_key["["].action == "再生速度を下げる"
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
    shortcuts["任意mpvキー"][0]["key"] = "["

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
    assert "見どころ範囲の開始: " in script
    assert "コメント入力を開きます" in script
    assert "mp.set_property_native('pause', true)" in script
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
    assert "tag_selection_active = true" in script
    assert 'emit("tag_navigation", "-1")' in script
    assert 'emit("tag_navigation", "1")' in script
    assert 'emit("tag_accept", "")' in script
    ordinary.cleanup()
    review.cleanup()


def test_colon_holds_double_speed_and_restores_the_previous_speed(tmp_path: Path) -> None:
    executable = tmp_path / "mpv"
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o755)
    media = tmp_path / "movie.mp4"
    media.write_bytes(b"test")

    session = create_isolated_mpv_session(media, configured_path=str(executable))
    script = session.script_path.read_text(encoding="utf-8")

    assert 'mp.add_forced_key_binding(":"' in script
    assert "function(event) hold_double_speed(event) end, {complex = true}" in script
    assert "temporary_speed_original = mp.get_property_number('speed', 1) or 1" in script
    assert "mp.set_property_number('speed', 2)" in script
    assert "elseif event.event == 'up' then" in script
    assert "mp.set_property_number('speed', temporary_speed_original)" in script
    assert "notify('押している間だけ2倍速')" not in script
    session.cleanup()


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


def test_retired_highlight_shortcuts_are_not_bound(tmp_path: Path) -> None:
    executable = tmp_path / "mpv"
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o755)
    media = tmp_path / "movie.mp4"
    media.write_bytes(b"test")

    session = create_isolated_mpv_session(media, configured_path=str(executable))
    script = session.script_path.read_text(encoding="utf-8")

    bindings = {binding.key: binding for binding in effective_shortcut_bindings(default_shortcut_settings())}
    assert "/" not in bindings
    assert "\\" not in bindings
    assert 'emit("highlight_point", "")' not in script
    assert 'emit("highlight_range_end_no_comment", "")' not in script
    assert 'emit("highlight_comment", "")' not in script
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


def test_session_can_finish_transient_tag_selection(tmp_path: Path) -> None:
    executable = tmp_path / "mpv"
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o755)
    media = tmp_path / "movie.mp4"
    media.write_bytes(b"test")

    session = create_isolated_mpv_session(media, configured_path=str(executable))
    script = session.script_path.read_text(encoding="utf-8")

    assert "mp.add_periodic_timer(0.1, consume_tag_selection_done)" in script
    assert session.finish_tag_selection()
    assert session.tag_selection_done_path.is_file()
    session.cleanup()


def test_shortcut_validation_rejects_duplicate_required_keys_and_external_commands() -> None:
    duplicate = default_shortcut_settings()
    duplicate["中枢キー"]["タグを選ぶ"]["key"] = "["
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
        "test_event\t/a%250A.mp4\t12.500\tline%0Aone\n", encoding="utf-8"
    )

    events = session.read_events()

    assert events[0].media_path == "/a%0A.mp4"
    assert events[0].value == "line\none"
    assert session.read_events() == ()
    session.cleanup()
