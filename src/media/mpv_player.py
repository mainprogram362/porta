"""Disposable mpv launches and explicit shortcut bindings for PORTA.

Persistent shortcut choices are data only.  At each launch they are rendered
to one Lua file in a private RAM-backed directory, loaded explicitly by mpv,
and removed when that mpv process ends.  User mpv configuration, history and
watch-later state are never read or written.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any

from foundation.product import PRODUCT_NAME


DEFAULT_MPV_PATH = "/usr/bin/mpv"
_RUNTIME_DIRECTORY = Path("/dev/shm")

# App events are intentionally a closed, reviewable set. A setting can turn
# one off or change its key, but cannot invent a Python-side operation.
APPLICATION_SHORTCUT_DEFAULTS: dict[str, str] = {
    "見どころ地点を記録": "m",
    "見どころ範囲の開始": "[",
    "見どころ範囲の終了": "]",
    "見どころ範囲の開始（コメントなし）": "/",
    "見どころ範囲の終了（コメントなし）": "\\",
    "見どころコメントを入力": "c",
    "評価 1": "1",
    "評価 2": "2",
    "評価 3": "3",
    "評価 4": "4",
    "評価 5": "5",
    "評価 6": "6",
    "評価 7": "7",
    "評価 8": "8",
    "評価 9": "9",
    "評価 10": "0",
    "タグを選ぶ": "t",
}

# Only the built-ins useful while reviewing media are surfaced in the app.
# mpv has many more bindings; this deliberately remains a compact work list,
# not a second copy of its full reference manual.
WORKFLOW_STANDARD_SHORTCUTS: tuple[tuple[str, str, str], ...] = (
    ("Space", "再生 / 一時停止", "cycle pause"),
    ("p", "再生 / 一時停止", "cycle pause"),
    ("Left", "5秒戻る", "seek -5 exact"),
    ("Right", "5秒進む", "seek 5 exact"),
    ("Shift+Left", "正確に1秒戻る", "seek -1 exact"),
    ("Shift+Right", "正確に1秒進む", "seek 1 exact"),
    ("Up", "1分進む", "seek 60 exact"),
    ("Down", "1分戻る", "seek -60 exact"),
    ("Shift+Up", "正確に5秒進む", "seek 5 exact"),
    ("Shift+Down", "正確に5秒戻る", "seek -5 exact"),
    (",", "1フレーム戻る（停止）", "frame-back-step"),
    (".", "1フレーム進む（停止）", "frame-step"),
    ("<", "前のプレイリスト項目", "playlist-prev"),
    (">", "次のプレイリスト項目", "playlist-next"),
    ("Enter", "次のプレイリスト項目", "playlist-next"),
    ("f", "全画面を切替", "cycle fullscreen"),
    ("m", "ミュートを切替", "cycle mute"),
    ("[", "再生速度を下げる", "multiply speed 1/1.1"),
    ("]", "再生速度を上げる", "multiply speed 1.1"),
    ("Backspace", "再生速度を標準へ戻す", "set speed 1"),
)

_APPLICATION_EVENTS = {
    "見どころ地点を記録": ("highlight_point", ""),
    "見どころ範囲の開始": ("highlight_range_start", ""),
    "見どころ範囲の終了": ("highlight_range_end", ""),
    "見どころ範囲の開始（コメントなし）": ("highlight_range_start", ""),
    "見どころ範囲の終了（コメントなし）": ("highlight_range_end_no_comment", ""),
    "見どころコメントを入力": ("highlight_comment", ""),
    "タグを選ぶ": ("tag_selection", ""),
    **{f"評価 {score}": ("rating", str(score)) for score in range(1, 11)},
}

# Advanced mappings remain mpv-only. Prevent commands which can launch other
# programs or load code from becoming a hidden execution route in settings.
_FORBIDDEN_ADVANCED_COMMANDS = frozenset(
    {"run", "subprocess", "load-script", "script-message", "script-message-to"}
)

# These native bindings distinguish a tap from a held key, so leave them
# entirely to mpv rather than replacing their standard behavior.
_NATIVE_HOLD_BINDING_COMMANDS = frozenset({"frame-back-step", "frame-step"})


@dataclass(frozen=True)
class ShortcutBinding:
    """One effective shortcut shown to the user for this isolated mpv launch."""

    key: str
    action: str
    source: str
    command: str


def default_shortcut_settings() -> dict[str, Any]:
    """Return the complete, directly editable shortcut template."""
    return {
        "アプリ連携キー": {
            label: {"enabled": True, "key": key}
            for label, key in APPLICATION_SHORTCUT_DEFAULTS.items()
        },
        # These seven enabled mappings do not overlap with mpv's useful default
        # navigation keys. The remaining three are intentionally blank slots.
        "任意mpvキー": [
            {"enabled": True, "label": "画面を90度回転", "key": "r", "mpv_command": "cycle-values video-rotate 0 90 180 270"},
            {"enabled": True, "label": "次の動画", "key": "n", "mpv_command": "playlist-next"},
            {"enabled": True, "label": "前の動画", "key": "p", "mpv_command": "playlist-prev"},
            {"enabled": True, "label": "10秒進む", "key": "Ctrl+Right", "mpv_command": "seek 10 exact"},
            {"enabled": True, "label": "10秒戻る", "key": "Ctrl+Left", "mpv_command": "seek -10 exact"},
            *[{"enabled": False, "label": "", "key": "", "mpv_command": ""} for _ in range(5)],
        ],
    }


def shortcut_help() -> dict[str, str]:
    """Text kept in the JSON template because JSON itself has no comments."""
    return {
        "キーの書き方": "例: Space, Left, Right, f, 1, Ctrl+[, Ctrl+Shift+m。",
        "優先規則": "アプリ連携キーはmpv標準キーより優先します。同じキーを任意mpvキーへ書いた場合も、アプリ連携キーを優先します。",
        "任意mpvキー": (
            "enabled が true かつ label / key / mpv_command が全て埋まった行だけを使います。"
            "mpv_command はmpv命令です。秒数移動の seek は常に正確シーク（exact）として実行します。"
            "例の秒数を変える場合は label と command の数値を両方変えてください。左右キーは単押しで"
            "正確に5秒移動し、押し続けると標準と同じキーフレーム基準の移動に切り替わります。"
        ),
        "安全制限": "外部プログラム実行・スクリプト読込み命令は受け付けません。",
        "保存の範囲": "ここで保存するのはキー割当だけです。mpv起動時にRAM上の一時Lua設定へ変換し、終了後に消します。",
    }


def effective_shortcut_bindings(
    shortcuts: dict[str, Any], *, enable_tag_selection: bool = False, enable_rating_selection: bool = True
) -> tuple[ShortcutBinding, ...]:
    """Return the compact, actual shortcut list after all local overrides.

    The renderer uses forced bindings: enabled app bindings win first, then
    enabled custom mpv bindings in their configured order.  A selected mpv
    default appears only when neither layer has claimed its key.
    """
    normalized = validate_shortcut_settings(shortcuts)
    claimed: set[str] = set()
    rows: list[ShortcutBinding] = []

    rating_keys: list[str] = []
    for label, entry in normalized["アプリ連携キー"].items():
        if not entry["enabled"]:
            continue
        if label == "タグを選ぶ" and not enable_tag_selection:
            continue
        if label.startswith("評価 ") and not enable_rating_selection:
            continue
        key = str(entry["key"])
        claimed.add(_mpv_key(key))
        if label.startswith("評価 "):
            rating_keys.append(key)
            continue
        rows.append(
            ShortcutBinding(
                key=key,
                action=label,
                source=f"{PRODUCT_NAME}連携",
                command="メディア情報整理へ記録",
            )
        )
    if rating_keys:
        rows.append(
            ShortcutBinding(
                key="、".join(rating_keys),
                action=f"評価を記録（{len(rating_keys)}段階）",
                source=f"{PRODUCT_NAME}連携",
                command="メディア情報整理へ記録",
            )
        )

    for entry in normalized["任意mpvキー"]:
        if not entry["enabled"]:
            continue
        key = str(entry["key"])
        normalized_key = _mpv_key(key)
        if normalized_key in claimed:
            continue
        claimed.add(normalized_key)
        rows.append(
            ShortcutBinding(
                key=key,
                action=str(entry["label"]),
                source="永続設定の任意キー",
                command=str(entry["mpv_command"]),
            )
        )

    for key, action, command in WORKFLOW_STANDARD_SHORTCUTS:
        if _mpv_key(key) in claimed:
            continue
        rows.append(ShortcutBinding(key, action, "mpv標準", command))
    return tuple(rows)


def validate_shortcut_settings(raw: Any) -> dict[str, Any]:
    """Validate and normalize the complete transparent shortcut structure."""
    raw = upgrade_shortcut_settings(raw)
    expected = {"アプリ連携キー", "任意mpvキー"}
    if not isinstance(raw, dict) or set(raw) != expected:
        raise ValueError("mpv_shortcuts の項目は雛形どおりにしてください。")
    application = _validate_fixed_shortcuts(
        raw["アプリ連携キー"], APPLICATION_SHORTCUT_DEFAULTS, section="アプリ連携キー"
    )
    custom = _validate_custom_shortcuts(raw["任意mpvキー"])
    _ensure_application_keys_are_unique(application)
    return {
        "アプリ連携キー": application,
        "任意mpvキー": custom,
    }


def upgrade_shortcut_settings(raw: Any) -> Any:
    """Fill newly introduced defaults and migrate unchanged old defaults."""
    if not isinstance(raw, dict):
        return raw
    custom = raw.get("任意mpvキー")
    if not isinstance(custom, list) or len(custom) != 10:
        return raw
    blank = {"enabled": False, "label": "", "key": "", "mpv_command": ""}
    defaults = default_shortcut_settings()["任意mpvキー"]
    upgraded = list(custom)
    for index, default in enumerate(defaults):
        if default["enabled"] and upgraded[index] == blank:
            upgraded[index] = dict(default)
    old_forward_default = {
        "enabled": True,
        "label": "3秒進む",
        "key": "Ctrl+Right",
        "mpv_command": "seek 3 exact",
    }
    if upgraded[3] == old_forward_default:
        upgraded[3] = dict(defaults[3])
    old_backward_default = {
        "enabled": True,
        "label": "3秒戻る",
        "key": "Ctrl+Left",
        "mpv_command": "seek -3 exact",
    }
    if upgraded[4] == old_backward_default:
        upgraded[4] = dict(defaults[4])
    application = raw.get("アプリ連携キー")
    result = dict(raw)
    if isinstance(application, dict):
        upgraded_application = dict(application)
        upgraded_application.pop("見どころ範囲の終了（コメントなし）", None)
        for label, key in APPLICATION_SHORTCUT_DEFAULTS.items():
            upgraded_application.setdefault(label, {"enabled": True, "key": key})
        result["アプリ連携キー"] = upgraded_application
    old_slash_skip = {
        "enabled": True,
        "label": "10秒戻る",
        "key": "/",
        "mpv_command": "seek -10 exact",
    }
    old_backslash_skip = {
        "enabled": True,
        "label": "10秒進む",
        "key": "\\",
        "mpv_command": "seek 10 exact",
    }
    for index, old_default in ((5, old_slash_skip), (6, old_backslash_skip)):
        if upgraded[index] == old_default:
            upgraded[index] = dict(blank)
    result["任意mpvキー"] = upgraded
    return result


def _validate_fixed_shortcuts(
    raw: Any, defaults: dict[str, str], *, section: str
) -> dict[str, dict[str, Any]]:
    if not isinstance(raw, dict) or set(raw) != set(defaults):
        raise ValueError(f"{section} の項目は雛形どおりにしてください。")
    result: dict[str, dict[str, Any]] = {}
    for label in defaults:
        entry = raw[label]
        if not isinstance(entry, dict) or set(entry) != {"enabled", "key"}:
            raise ValueError(f"{section} の「{label}」は enabled と key を持つ形にしてください。")
        enabled, key = entry["enabled"], entry["key"]
        if not isinstance(enabled, bool) or not isinstance(key, str):
            raise ValueError(f"{section} の「{label}」の型が正しくありません。")
        if enabled:
            _validate_key(key, f"{section} の「{label}」")
        result[label] = {"enabled": enabled, "key": key.strip()}
    return result


def _validate_custom_shortcuts(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list) or len(raw) != 10:
        raise ValueError("任意mpvキーは、登録3件と空欄7件を含む10枠の一覧にしてください。")
    result: list[dict[str, Any]] = []
    for index, entry in enumerate(raw, start=1):
        if not isinstance(entry, dict) or set(entry) != {"enabled", "label", "key", "mpv_command"}:
            raise ValueError(f"任意mpvキー {index} は enabled、label、key、mpv_command を持つ形にしてください。")
        enabled, label, key, command = entry["enabled"], entry["label"], entry["key"], entry["mpv_command"]
        if not isinstance(enabled, bool) or not all(isinstance(value, str) for value in (label, key, command)):
            raise ValueError(f"任意mpvキー {index} の型が正しくありません。")
        label, key, command = label.strip(), key.strip(), command.strip()
        if not enabled:
            result.append({"enabled": False, "label": label, "key": key, "mpv_command": command})
            continue
        if not label or not key or not command:
            raise ValueError(f"任意mpvキー {index} を有効にするには label、key、mpv_command を全て入力してください。")
        _validate_key(key, f"任意mpvキー {index}")
        _validate_advanced_command(command, index)
        if _is_seek_command(command):
            command = _with_exact_seek(command)
        result.append({"enabled": True, "label": label, "key": key, "mpv_command": command})
    return result


def _validate_key(key: str, label: str) -> None:
    if not key.strip() or len(key.strip()) > 80 or any(character in key for character in "\r\n\t"):
        raise ValueError(f"{label} の key を通常のキー表記で入力してください。")


def _validate_advanced_command(command: str, index: int) -> None:
    if "\n" in command or "\r" in command:
        raise ValueError(f"任意mpvキー {index} の命令は1行にしてください。")
    first_word = command.split(maxsplit=1)[0].casefold()
    if first_word in _FORBIDDEN_ADVANCED_COMMANDS:
        raise ValueError(f"任意mpvキー {index} では外部実行・スクリプト読込み命令は使えません。")


def _ensure_application_keys_are_unique(application: dict[str, dict[str, Any]]) -> None:
    used: dict[str, str] = {}
    for label, entry in application.items():
        if not entry["enabled"]:
            continue
        key = _mpv_key(str(entry["key"]))
        if key in used:
            raise ValueError(f"アプリ連携キーが重複しています: {used[key]} と {label}")
        used[key] = label


def find_mpv(configured_path: str = DEFAULT_MPV_PATH) -> Path | None:
    """Return the explicit mpv binary, without searching arbitrary locations."""
    candidate = Path(configured_path).expanduser()
    return candidate if candidate.is_file() and os.access(candidate, os.X_OK) else None


@dataclass(frozen=True)
class MpvEvent:
    """One non-sensitive player action emitted by the temporary Lua script."""

    kind: str
    media_path: str
    time_seconds: float
    value: str = ""


@dataclass
class IsolatedMpvSession:
    """A private RAM-backed player environment, owned by the launching screen."""

    program: Path
    media_paths: tuple[Path, ...]
    shortcuts: dict[str, Any]
    _temporary_directory: tempfile.TemporaryDirectory[str]
    playlist_start_index: int = 0
    enable_tag_selection: bool = False
    enable_rating_selection: bool = True
    _event_offset: int = field(default=0, init=False)
    binding_warnings: tuple[str, ...] = field(default=(), init=False)

    def __post_init__(self) -> None:
        self.event_path.touch(mode=0o600)
        script, warnings = _render_lua_script(
            self.shortcuts,
            enable_tag_selection=self.enable_tag_selection,
            enable_rating_selection=self.enable_rating_selection,
        )
        self.script_path.write_text(script, encoding="utf-8")
        self.binding_warnings = tuple(warnings)

    @property
    def directory(self) -> Path:
        return Path(self._temporary_directory.name)

    @property
    def event_path(self) -> Path:
        return self.directory / "events.tsv"

    @property
    def script_path(self) -> Path:
        return self.directory / "porta_place_shortcuts.lua"

    @property
    def resume_request_path(self) -> Path:
        """A one-shot, local-only request for this mpv process to resume."""
        return self.directory / "resume.request"

    @property
    def arguments(self) -> tuple[str, ...]:
        return (
            "--no-config",
            "--save-position-on-quit=no",
            # Keep the completed item open and paused.  In particular, do not
            # advance through the supplied review playlist without a manual
            # playlist-next command.
            "--keep-open=always",
            "--no-terminal",
            # Keep filenames as the visible identity even when embedded media
            # metadata has a different title.
            "--force-media-title=${filename}",
            "--osd-playlist-entry=filename",
            # Temporary messages share slimbox's lower area, to its left.
            "--osd-align-x=left",
            "--osd-align-y=bottom",
            "--osd-margin-x=24",
            "--osd-margin-y=30",
            # Use mpv's compact, stock OSC layout. It contains only a seek
            # bar and timecodes; the filename remains in the window title.
            "--osc=yes",
            "--script-opts=osc-layout=slimbox,osc-valign=1,osc-barmargin=0,osc-hidetimeout=1000,osc-timetotal=yes",
            f"--playlist-start={self.playlist_start_index}",
            f"--script={self.script_path}",
            "--",
            *(str(path) for path in self.media_paths),
        )

    def environment(self, base: dict[str, str] | None = None) -> dict[str, str]:
        """Return mpv-only environment variables with no writable real HOME."""
        values = dict(os.environ if base is None else base)
        values.pop("MPV_HOME", None)
        for name in ("HOME", "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME"):
            path = self.directory / name.lower()
            path.mkdir(parents=True, exist_ok=True)
            values[name] = str(path)
        values["PORTA_MPV_EVENT_FILE"] = str(self.event_path)
        values["PORTA_MPV_RESUME_REQUEST_FILE"] = str(self.resume_request_path)
        return values

    def request_resume(self) -> bool:
        """Ask this still-running disposable player to resume after a note."""
        try:
            self.resume_request_path.touch(mode=0o600, exist_ok=True)
        except OSError:
            return False
        return True

    def read_events(self) -> tuple[MpvEvent, ...]:
        """Read only new local Lua event lines from this one session."""
        try:
            with self.event_path.open("r", encoding="utf-8") as handle:
                handle.seek(self._event_offset)
                lines = handle.readlines()
                self._event_offset = handle.tell()
        except OSError:
            return ()
        events: list[MpvEvent] = []
        for line in lines:
            fields = line.rstrip("\n").split("\t")
            if len(fields) != 4:
                continue
            try:
                seconds = float(fields[2])
            except ValueError:
                continue
            events.append(MpvEvent(fields[0], _unescape(fields[1]), seconds, _unescape(fields[3])))
        return tuple(events)

    def cleanup(self) -> None:
        """Remove all transient script, event and synthetic-home files."""
        self._temporary_directory.cleanup()


def create_isolated_mpv_session(
    media_paths: str | Path | Iterable[str | Path],
    *,
    configured_path: str = DEFAULT_MPV_PATH,
    shortcuts: dict[str, Any] | None = None,
    playlist_start_index: int = 0,
    enable_tag_selection: bool = False,
    enable_rating_selection: bool = True,
) -> IsolatedMpvSession:
    """Prepare one verified, disposable mpv playlist launch."""
    program = find_mpv(configured_path)
    if program is None:
        raise ValueError(f"mpvを実行できません: {configured_path}")
    raw_paths = (media_paths,) if isinstance(media_paths, (str, Path)) else tuple(media_paths)
    paths = tuple(Path(value).expanduser().absolute() for value in raw_paths)
    if not paths or any(not path.is_file() for path in paths):
        raise ValueError("再生対象は存在するファイルにしてください。")
    if not isinstance(playlist_start_index, int) or not 0 <= playlist_start_index < len(paths):
        raise ValueError("再生開始位置がプレイリストの範囲外です。")
    normalized_shortcuts = validate_shortcut_settings(
        default_shortcut_settings() if shortcuts is None else shortcuts
    )
    runtime = _RUNTIME_DIRECTORY if _RUNTIME_DIRECTORY.is_dir() and os.access(_RUNTIME_DIRECTORY, os.W_OK) else None
    return IsolatedMpvSession(
        program=program,
        media_paths=paths,
        shortcuts=normalized_shortcuts,
        _temporary_directory=tempfile.TemporaryDirectory(prefix="porta-place-mpv-", dir=runtime),
        playlist_start_index=playlist_start_index,
        enable_tag_selection=enable_tag_selection,
        enable_rating_selection=enable_rating_selection,
    )


def _render_lua_script(
    shortcuts: dict[str, Any], *, enable_tag_selection: bool = False, enable_rating_selection: bool = True
) -> tuple[str, list[str]]:
    """Render only validated bindings; application bindings always win."""
    lines = [
        "local mp = require 'mp'",
        "local event_file = os.getenv('PORTA_MPV_EVENT_FILE')",
        "local resume_request_file = os.getenv('PORTA_MPV_RESUME_REQUEST_FILE')",
        "local function escape(value)",
        "  local text = tostring(value or '')",
        "  text = text:gsub('%%', '%%25'):gsub('\\t', '%%09'):gsub('\\r', '%%0D'):gsub('\\n', '%%0A')",
        "  return text",
        "end",
        "local function emit(kind, value)",
        "  local handle = io.open(event_file, 'a')",
        "  if not handle then return end",
        "  local path = mp.get_property('path') or ''",
        "  local position = mp.get_property_number('time-pos', 0) or 0",
        "  handle:write(escape(kind) .. '\\t' .. escape(path) .. '\\t' .. string.format('%.3f', position) .. '\\t' .. escape(value) .. '\\n')",
        "  handle:close()",
        "end",
        "local function format_time(seconds)",
        "  seconds = math.max(0, math.floor((seconds or 0) + 0.5))",
        "  local hours = math.floor(seconds / 3600)",
        "  local minutes = math.floor((seconds % 3600) / 60)",
        "  local remainder = seconds % 60",
        "  if hours > 0 then return string.format('%d:%02d:%02d', hours, minutes, remainder) end",
        "  return string.format('%02d:%02d', minutes, remainder)",
        "end",
        "local function time_text()",
        "  return mp.get_property_osd('playback-time') or format_time(mp.get_property_number('time-pos', 0))",
        "end",
        "local notification_serial = 0",
        "local function notify(text)",
        "  notification_serial = notification_serial + 1",
        "  local serial = notification_serial",
        "  mp.osd_message(text, 1600)",
        "  mp.add_timeout(1.65, function()",
        "    if serial == notification_serial then mp.osd_message('', 0) end",
        "  end)",
        "end",
        "local function show_controller()",
        "  mp.commandv('script-message-to', 'osc', 'osc-show')",
        "end",
        "local function force_filename_title()",
        "  local filename = mp.get_property('filename') or ''",
        "  if filename ~= '' then mp.set_property('force-media-title', filename) end",
        "end",
        "mp.register_event('file-loaded', force_filename_title)",
        "local highlight_range_start = ''",
        "local function consume_resume_request()",
        "  if not resume_request_file then return end",
        "  local handle = io.open(resume_request_file, 'r')",
        "  if not handle then return end",
        "  handle:close()",
        "  os.remove(resume_request_file)",
        "  mp.set_property_native('pause', false)",
        "  notify('再生を再開')",
        "end",
        "mp.add_periodic_timer(0.1, consume_resume_request)",
    ]
    claimed: set[str] = set()
    warnings: list[str] = []
    binding_index = 0

    def bind(key: str, body: str, *, complex_events: bool = False) -> bool:
        nonlocal binding_index
        mpv_key = _mpv_key(key)
        if mpv_key in claimed:
            return False
        claimed.add(mpv_key)
        binding_index += 1
        event_argument = "event" if complex_events else ""
        flags = ", {complex = true}" if complex_events else ""
        lines.append(
            f"mp.add_forced_key_binding({_lua_string(mpv_key)}, {_lua_string(f'porta-{binding_index}')}, function({event_argument}) {body} end{flags})"
        )
        return True

    for label, entry in shortcuts["アプリ連携キー"].items():
        if entry["enabled"]:
            kind, value = _APPLICATION_EVENTS[label]
            if kind == "tag_selection" and not enable_tag_selection:
                continue
            if kind == "rating" and not enable_rating_selection:
                continue
            emit = f"emit({_lua_string(kind)}, {_lua_string(value)})"
            if kind == "highlight_point":
                body = f"notify({_lua_string('見どころ地点を記録: ')} .. time_text()); {emit}"
            elif kind == "highlight_range_start":
                body = f"highlight_range_start = time_text(); notify({_lua_string('見どころ範囲の開始: ')} .. highlight_range_start); {emit}"
            elif kind == "highlight_range_end":
                body = (
                    "local end_time = time_text(); "
                    "if highlight_range_start ~= '' then "
                    "mp.set_property_native('pause', true); "
                    f"notify({_lua_string('見どころ範囲: ')} .. highlight_range_start .. ' - ' .. end_time .. {_lua_string(' / コメント入力を開きます')}) "
                    f"else notify({_lua_string('見どころ範囲の終了: ')} .. end_time) end; "
                    f"highlight_range_start = ''; {emit}"
                )
            elif kind == "highlight_range_end_no_comment":
                body = (
                    "local end_time = time_text(); "
                    "if highlight_range_start ~= '' then "
                    f"notify({_lua_string('見どころ範囲: ')} .. highlight_range_start .. ' - ' .. end_time) "
                    f"else notify({_lua_string('見どころ範囲の終了: ')} .. end_time) end; "
                    f"highlight_range_start = ''; {emit}"
                )
            elif kind == "highlight_comment":
                body = (
                    "mp.set_property_native('pause', true); "
                    f"notify({_lua_string('見どころメモ: 一時停止して入力欄を開きます')}); {emit}"
                )
            elif kind == "tag_selection":
                body = f"notify({_lua_string('タグを選択して追加します')}); {emit}"
            elif kind == "rating":
                body = f"notify({_lua_string('評価: ' + value + ' / 10')}); {emit}"
            else:  # _APPLICATION_EVENTS is closed, but keep rendering defensive.
                body = emit
            bind(entry["key"], body)
    for entry in shortcuts["任意mpvキー"]:
        if _is_seek_command(str(entry["mpv_command"])):
            body = _seek_binding_body(str(entry["mpv_command"]), str(entry["label"]))
        else:
            body = (
                f"mp.command({_lua_string(entry['mpv_command'])}); "
                f"show_controller(); notify({_lua_string(entry['label'])})"
            )
        if entry["enabled"] and not bind(entry["key"], body):
            warnings.append(f"任意mpvキー「{entry['label']}」はアプリ連携キーまたは先の任意キーと重複するため使いません。")

    # Bind the review shortcuts explicitly. Exact seeks suppress mpv's own
    # OSD, while every playback operation reopens the standard slim OSC.
    for key, action, command in WORKFLOW_STANDARD_SHORTCUTS:
        if command in _NATIVE_HOLD_BINDING_COMMANDS:
            continue
        if key in {"Left", "Right"} and command in {"seek -5 exact", "seek 5 exact"}:
            bind(key, _hybrid_arrow_seek_binding_body(command, action), complex_events=True)
            continue
        if _is_seek_command(command):
            bind(key, _seek_binding_body(command, action))
        else:
            bind(key, f"mp.command({_lua_string(command)}); show_controller()")
    return "\n".join(lines) + "\n", warnings


def _is_seek_command(command: str) -> bool:
    """Whether an mpv command changes playback time by a seek operation."""
    normalized = command.strip().casefold()
    if normalized.startswith("no-osd "):
        normalized = normalized.removeprefix("no-osd ").lstrip()
    return normalized.startswith("seek ")


def _without_seek_osd(command: str) -> str:
    """Suppress mpv's large seek bar while preserving the requested seek."""
    normalized = command.strip()
    if normalized.casefold().startswith("no-osd "):
        return normalized
    return f"no-osd {normalized}"


def _with_exact_seek(command: str) -> str:
    """Normalize an mpv seek command to its precise, non-keyframe form."""
    normalized = command.strip()
    prefix = ""
    if normalized.casefold().startswith("no-osd "):
        prefix = "no-osd "
        normalized = normalized[len(prefix):].lstrip()
    parts = normalized.split(maxsplit=2)
    if len(parts) < 2 or parts[0].casefold() != "seek":
        return command.strip()
    flags = parts[2].split("+") if len(parts) == 3 else []
    normalized_flags = [flag for flag in flags if flag and flag.casefold() != "keyframes"]
    if not any(flag.casefold() == "exact" for flag in normalized_flags):
        normalized_flags.append("exact")
    suffix = f" {'+'.join(normalized_flags)}" if normalized_flags else ""
    return f"{prefix}seek {parts[1]}{suffix}"


def _seek_binding_body(command: str, label: str) -> str:
    """Run an exact seek and briefly identify the invoked operation."""
    return (
        f"show_controller(); notify({_lua_string(label)}); "
        f"mp.command({_lua_string(_with_exact_seek(_without_seek_osd(command)))})"
    )


def _hybrid_arrow_seek_binding_body(command: str, label: str) -> str:
    """Make a tap exact, while using mpv's normal keyframe seek on hold."""
    exact_command = _with_exact_seek(_without_seek_osd(command))
    keyframe_command = _with_keyframe_seek(_without_seek_osd(command))
    return (
        "if event.event == 'down' or event.event == 'press' then "
        f"show_controller(); notify({_lua_string(label)}); mp.command({_lua_string(exact_command)}) "
        "elseif event.event == 'repeat' then "
        f"show_controller(); mp.command({_lua_string(keyframe_command)}) "
        "end"
    )


def _with_keyframe_seek(command: str) -> str:
    """Render the same relative seek mode mpv uses for its normal arrows."""
    normalized = command.strip()
    prefix = ""
    if normalized.casefold().startswith("no-osd "):
        prefix = "no-osd "
        normalized = normalized[len(prefix):].lstrip()
    parts = normalized.split(maxsplit=2)
    if len(parts) < 2 or parts[0].casefold() != "seek":
        return command.strip()
    flags = parts[2].split("+") if len(parts) == 3 else []
    normalized_flags = [
        flag for flag in flags if flag and flag.casefold() not in {"exact", "keyframes"}
    ]
    if not any(flag.casefold() == "relative" for flag in normalized_flags):
        normalized_flags.append("relative")
    normalized_flags.append("keyframes")
    return f"{prefix}seek {parts[1]} {'+'.join(normalized_flags)}"


def _mpv_key(key: str) -> str:
    replacements = {
        "Space": "SPACE", "Left": "LEFT", "Right": "RIGHT", "Up": "UP", "Down": "DOWN",
        "PageUp": "PGUP", "PageDown": "PGDWN", "Enter": "ENTER", "Escape": "ESC",
    }
    return "+".join(replacements.get(part, part) for part in key.strip().split("+"))


def _lua_string(value: str) -> str:
    """JSON string quoting is compatible with the needed Lua string subset."""
    return json.dumps(value, ensure_ascii=False)


def _unescape(value: str) -> str:
    replacements = {"25": "%", "09": "\t", "0D": "\r", "0A": "\n"}
    return re.sub(r"%(25|09|0D|0A)", lambda match: replacements[match.group(1)], value)
