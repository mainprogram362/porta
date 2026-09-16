"""Disposable mpv launches and explicit shortcut bindings for PORTA.

Persistent shortcut choices are data only.  At each launch they are rendered
to one Lua file in a private RAM-backed directory, loaded explicitly by mpv,
and removed when that mpv process ends.  User mpv configuration, history and
watch-later state are never read or written.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
import fcntl
import json
import os
from pathlib import Path
import re
import signal
import shlex
import stat
import subprocess
import sys
import tempfile
import time
from typing import Any

from foundation.product import PRODUCT_NAME
from runtime.process_registry import ProcessIdentity, identity_status, read_process, runtime_directory


DEFAULT_MPV_PATH = "/usr/bin/mpv"
_RUNTIME_DIRECTORY = Path("/dev/shm")

# App events are intentionally a closed, reviewable set. The compact
# user-facing form groups the ten rating keys into one core entry.
_DEFAULT_CORE_SHORTCUTS: dict[str, dict[str, object]] = {
    "見どころ範囲": {"enabled": True, "start_key": "[", "end_key": "]"},
    "評価 1-10": {"enabled": True, "key_sequence": "1-9, 0"},
    "タグを選ぶ": {"enabled": True, "key": "t"},
}
_DEFAULT_RATING_KEYS = tuple("1234567890")

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
    (":", "押している間だけ2倍速", "porta-hold-double-speed"),
    ("Backspace", "再生速度を標準へ戻す", "set speed 1"),
)

_HOLD_DOUBLE_SPEED_COMMAND = "porta-hold-double-speed"

_RETIRED_APPLICATION_SHORTCUTS = frozenset(
    {
        "見どころ地点を記録",
        "見どころ範囲の開始（コメントなし）",
        "見どころ範囲の終了（コメントなし）",
        "見どころコメントを入力",
    }
)

# Advanced mappings remain mpv-only. Prevent commands which can launch other
# programs or load code from becoming a hidden execution route in settings.
_FORBIDDEN_ADVANCED_COMMANDS = frozenset(
    {"run", "subprocess", "load-script", "script-message", "script-message-to"}
)

# These native bindings distinguish a tap from a held key, so leave them
# entirely to mpv rather than replacing their standard behavior.
_NATIVE_HOLD_BINDING_COMMANDS = frozenset({"frame-back-step", "frame-step"})

# These delimiters make the optional normal-mpv integration both visible and
# reversible.  Nothing outside the two blocks is changed by PORTA.
_NORMAL_MPV_CONF_BEGIN = "# >>> PORTA shared playback settings >>>"
_NORMAL_MPV_CONF_END = "# <<< PORTA shared playback settings <<<"
_NORMAL_MPV_INPUT_BEGIN = "# >>> PORTA shared key bindings >>>"
_NORMAL_MPV_INPUT_END = "# <<< PORTA shared key bindings <<<"

# A normal mpv session is owned by the user. Do not copy PORTA's OSD, window
# title, or controller layout there: those settings can make playlist changes
# look different from the user's normal player. This is the one neutral
# behavior explicitly shared with normal mpv.
_SHARED_NORMAL_MPV_OPTIONS: tuple[tuple[str, str, str], ...] = (
    (
        "autocreate-playlist",
        "filter",
        "開いたファイルと同じフォルダをプレイリストにする",
    ),
)

_MPV_OWNER_VERSION = 1
_MPV_DOMAINS = frozenset({"normal", "porta"})
_NORMAL_HANDLER_ID = "porta-mpv-replace.desktop"
_NORMAL_HANDLER_MIME_TYPES = (
    "video/mp4",
    "video/x-matroska",
    "video/webm",
    "video/x-msvideo",
    "video/quicktime",
    "audio/mpeg",
    "audio/flac",
    "audio/ogg",
    "audio/wav",
)


def _mpv_owner_directory(directory: Path | None = None) -> Path:
    """Return the private, short-lived state directory for owned players."""
    return (runtime_directory() / "mpv-players") if directory is None else Path(directory)


def _ensure_private_directory(path: Path) -> None:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or (info.st_mode & 0o777) != 0o700:
        raise PermissionError("mpvの所有情報を安全に保存できません。")


def _owner_path(directory: Path, domain: str) -> Path:
    if domain not in _MPV_DOMAINS:
        raise ValueError("mpvの起動区分が不正です。")
    return directory / f"{domain}.json"


def _decode_owned_identity(data: object, domain: str) -> ProcessIdentity:
    if not isinstance(data, dict) or data.get("version") != _MPV_OWNER_VERSION or data.get("domain") != domain:
        raise ValueError("mpvの所有情報が不正です。")
    value = data.get("identity")
    if not isinstance(value, dict):
        raise ValueError("mpvの所有情報が不正です。")
    pid, ticks, boot = value.get("pid"), value.get("start_ticks"), value.get("boot_id")
    if type(pid) is not int or pid <= 0 or type(ticks) is not int or ticks < 0 or not isinstance(boot, str) or not boot:
        raise ValueError("mpvの所有情報が不正です。")
    return ProcessIdentity(pid, ticks, boot)


def _read_owned_identity(path: Path, domain: str) -> ProcessIdentity | None:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "r", encoding="utf-8") as stream:
            if os.fstat(stream.fileno()).st_size > 4096:
                return None
            return _decode_owned_identity(json.load(stream), domain)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None


def _write_owned_identity(path: Path, domain: str, identity: ProcessIdentity) -> None:
    temporary: str | None = None
    try:
        descriptor, temporary = tempfile.mkstemp(prefix=".", suffix=".tmp", dir=path.parent)
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(
                {
                    "version": _MPV_OWNER_VERSION,
                    "domain": domain,
                    "identity": {
                        "pid": identity.pid,
                        "start_ticks": identity.start_ticks,
                        "boot_id": identity.boot_id,
                    },
                },
                stream,
            )
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except OSError:
                pass


def _terminate_owned_identity(identity: ProcessIdentity, *, wait_seconds: float = 2.0) -> bool:
    """Request orderly exit, pinned to an exact Linux process identity.

    A pidfd prevents a PID recycled between the validation and the signal from
    ever receiving the termination request.  If pidfds are unavailable, this
    deliberately declines to signal rather than guessing from a bare PID.
    """
    descriptor: int | None = None
    try:
        descriptor = os.pidfd_open(identity.pid)
        if read_process(identity.pid).identity != identity:
            return False
        signal.pidfd_send_signal(descriptor, signal.SIGTERM)
    except (AttributeError, OSError, ValueError, IndexError):
        return False
    finally:
        if descriptor is not None:
            os.close(descriptor)
    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        if identity_status(identity) == "exited":
            return True
        time.sleep(0.025)
    return identity_status(identity) == "exited"


class MpvReplacement:
    """Serialize replacement of one PORTA-owned mpv domain.

    The lock covers stopping the previous player and registering the new PID,
    so two near-simultaneous launches cannot both become the active player.
    It contains no filenames, commands, playback history, or user settings.
    """

    def __init__(self, domain: str, *, directory: Path | None = None) -> None:
        self.domain = domain
        self.directory = _mpv_owner_directory(directory)
        _ensure_private_directory(self.directory)
        self.path = _owner_path(self.directory, domain)
        self._lock = open(self.directory / f".{domain}.lock", "a+", encoding="utf-8")
        os.chmod(self._lock.name, 0o600)
        fcntl.flock(self._lock.fileno(), fcntl.LOCK_EX)
        self._claimed: ProcessIdentity | None = None
        previous = _read_owned_identity(self.path, domain)
        if previous is None:
            return
        if identity_status(previous) == "exited":
            self.path.unlink(missing_ok=True)
            return
        if not _terminate_owned_identity(previous):
            self.close()
            raise RuntimeError("前回PORTAが起動したmpvを安全に終了できませんでした。")
        self.path.unlink(missing_ok=True)

    def claim(self, pid: int) -> ProcessIdentity:
        """Record the just-started player while the replacement lock is held."""
        identity = read_process(pid).identity
        _write_owned_identity(self.path, self.domain, identity)
        self._claimed = identity
        return identity

    def close(self) -> None:
        if getattr(self, "_lock", None) is not None:
            fcntl.flock(self._lock.fileno(), fcntl.LOCK_UN)
            self._lock.close()
            self._lock = None

    def __enter__(self) -> "MpvReplacement":
        return self

    def __exit__(self, _type, _value, _traceback) -> None:
        self.close()


def begin_mpv_replacement(domain: str, *, directory: Path | None = None) -> MpvReplacement:
    """Stop the preceding owned player and reserve its domain for a new one."""
    return MpvReplacement(domain, directory=directory)


def release_owned_mpv(domain: str, pid: int, *, directory: Path | None = None) -> None:
    """Forget an exited player, but never remove a newer replacement record."""
    try:
        base = _mpv_owner_directory(directory)
        path = _owner_path(base, domain)
        current = _read_owned_identity(path, domain)
        if current is not None and current.pid == pid:
            # PID alone is sufficient only after confirming it is no longer
            # alive; an active reused PID must leave the newer record intact.
            if identity_status(current) == "exited":
                path.unlink(missing_ok=True)
    except OSError:
        return


def launch_normal_mpv(
    media_paths: str | Path | Iterable[str | Path], *, configured_path: str = DEFAULT_MPV_PATH
) -> subprocess.Popen[bytes]:
    """Replace only PORTA's ordinary-mpv player, preserving normal mpv config."""
    program = find_mpv(configured_path)
    if program is None:
        raise ValueError(f"mpvを実行できません: {configured_path}")
    raw_paths = (media_paths,) if isinstance(media_paths, (str, Path)) else tuple(media_paths)
    paths = tuple(Path(value).expanduser().absolute() for value in raw_paths)
    if not paths or any(not path.is_file() for path in paths):
        raise ValueError("再生対象は存在するファイルにしてください。")
    with begin_mpv_replacement("normal") as replacement:
        process = subprocess.Popen(
            [str(program), "--", *(str(path) for path in paths)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        replacement.claim(process.pid)
    return process


@dataclass(frozen=True)
class ShortcutBinding:
    """One effective shortcut shown to the user for this isolated mpv launch."""

    key: str
    action: str
    source: str
    command: str


@dataclass(frozen=True)
class NormalMpvSettingsCopyResult:
    """Result of safely adding PORTA's portable choices to normal mpv."""

    config_directory: Path
    mpv_conf: Path
    input_conf: Path
    copied_options: tuple[str, ...]
    skipped_options: tuple[str, ...]
    copied_bindings: tuple[str, ...]
    skipped_bindings: tuple[str, ...]


@dataclass(frozen=True)
class NormalMpvHandlerInstallResult:
    """Files installed for ordinary mpv's replace-on-open launch path."""

    wrapper_path: Path
    desktop_path: Path
    configured_mime_types: tuple[str, ...]
    failed_mime_types: tuple[str, ...]


def install_normal_mpv_replace_handler(
    *,
    home: Path | None = None,
    python_executable: Path | None = None,
    relay_script: Path | None = None,
) -> NormalMpvHandlerInstallResult:
    """Make normal local media opens use a PORTA-owned normal mpv player.

    Ordinary mpv settings remain in effect. The relay only replaces a prior
    player it recorded itself; it never scans for or signals arbitrary mpv.
    """
    user_home = Path.home() if home is None else Path(home)
    executable = Path(sys.executable) if python_executable is None else Path(python_executable)
    script = (
        Path(__file__).resolve().parents[2] / "scripts" / "porta_mpv_open.py"
        if relay_script is None
        else Path(relay_script)
    )
    if not executable.is_file() or not script.is_file():
        raise ValueError("通常mpv用の起動リレーを見つけられません。")
    wrapper = user_home / ".local" / "bin" / "porta-mpv-replace"
    desktop = user_home / ".local" / "share" / "applications" / _NORMAL_HANDLER_ID
    wrapper.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    desktop.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    _atomic_write_text(
        wrapper,
        "#!/bin/sh\nexec " + shlex.quote(str(executable)) + " " + shlex.quote(str(script)) + ' "$@"\n',
    )
    os.chmod(wrapper, 0o700)
    _atomic_write_text(
        desktop,
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=mpv（PORTAで置換起動）\n"
        f"Exec={wrapper} %U\n"
        "Terminal=false\n"
        "NoDisplay=true\n"
        "MimeType=" + ";".join(_NORMAL_HANDLER_MIME_TYPES) + ";\n",
    )
    configured: list[str] = []
    failed: list[str] = []
    for mime_type in _NORMAL_HANDLER_MIME_TYPES:
        try:
            result = subprocess.run(
                ["xdg-mime", "default", _NORMAL_HANDLER_ID, mime_type],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        except OSError:
            failed.append(mime_type)
            continue
        (configured if result.returncode == 0 else failed).append(mime_type)
    return NormalMpvHandlerInstallResult(wrapper, desktop, tuple(configured), tuple(failed))


def default_shortcut_settings() -> dict[str, Any]:
    """Return the complete, directly editable shortcut template."""
    return {
        # These five enabled mappings do not overlap with mpv's useful default.
        # Keep just one blank row; more rows can be added explicitly in JSON.
        "任意mpvキー": [
            {"enabled": True, "label": "画面を90度回転", "key": "r", "mpv_command": "cycle-values video-rotate 0 90 180 270"},
            {"enabled": True, "label": "次の動画", "key": "n", "mpv_command": "playlist-next"},
            {"enabled": True, "label": "前の動画", "key": "p", "mpv_command": "playlist-prev"},
            {"enabled": True, "label": "10秒進む", "key": "Ctrl+Right", "mpv_command": "seek 10 exact"},
            {"enabled": True, "label": "10秒戻る", "key": "Ctrl+Left", "mpv_command": "seek -10 exact"},
            {"enabled": False, "label": "", "key": "", "mpv_command": ""},
        ],
        "中枢キー": {label: dict(value) for label, value in _DEFAULT_CORE_SHORTCUTS.items()},
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
    for label, key, kind, _value in _core_shortcut_events(normalized["中枢キー"]):
        if kind == "tag_selection" and not enable_tag_selection:
            continue
        if kind == "rating" and not enable_rating_selection:
            continue
        claimed.add(_mpv_key(key))
        if kind == "rating":
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


def normal_mpv_config_directory(environment: dict[str, str] | None = None) -> Path:
    """Return the ordinary mpv configuration directory, never PORTA's RAM one."""
    values = os.environ if environment is None else environment
    # MPV_HOME is mpv's explicit configuration-root override.  Otherwise use
    # the normal XDG path on this Linux-focused portable application.
    if configured := values.get("MPV_HOME", "").strip():
        return Path(configured).expanduser()
    if xdg_config := values.get("XDG_CONFIG_HOME", "").strip():
        return Path(xdg_config).expanduser() / "mpv"
    return Path.home() / ".config" / "mpv"


def copy_porta_settings_to_normal_mpv(
    shortcuts: dict[str, Any], *, config_directory: Path | None = None
) -> NormalMpvSettingsCopyResult:
    """Add compatible PORTA settings to regular mpv without overwriting user choices.

    The two generated blocks are regenerated on subsequent runs.  A setting
    or key already written elsewhere by the user wins and is omitted from the
    PORTA block.  App-event keys (ratings, tags, highlights) and the special
    hold-to-double-speed key are intentionally not portable.
    """
    normalized = validate_shortcut_settings(shortcuts)
    directory = normal_mpv_config_directory() if config_directory is None else Path(config_directory)
    directory.mkdir(parents=True, exist_ok=True)
    mpv_conf = directory / "mpv.conf"
    input_conf = directory / "input.conf"

    plain_mpv_conf = _read_text_without_managed_block(
        mpv_conf, _NORMAL_MPV_CONF_BEGIN, _NORMAL_MPV_CONF_END
    )
    used_options = _configured_mpv_option_names(plain_mpv_conf)
    selected_options: list[tuple[str, str, str]] = []
    skipped_options: list[str] = []
    for option, value, label in _SHARED_NORMAL_MPV_OPTIONS:
        if option.casefold() in used_options:
            skipped_options.append(label)
        else:
            selected_options.append((option, value, label))
    mpv_block = _render_managed_block(
        _NORMAL_MPV_CONF_BEGIN,
        _NORMAL_MPV_CONF_END,
        [f"{option}={value}" for option, value, _label in selected_options],
    )
    _atomic_write_text(mpv_conf, _append_managed_block(plain_mpv_conf, mpv_block))

    plain_input_conf = _read_text_without_managed_block(
        input_conf, _NORMAL_MPV_INPUT_BEGIN, _NORMAL_MPV_INPUT_END
    )
    used_keys = _configured_mpv_keys(plain_input_conf)
    selected_bindings: list[ShortcutBinding] = []
    skipped_bindings: list[str] = []
    for binding in _portable_normal_mpv_bindings(normalized):
        normalized_key = _mpv_key(binding.key).casefold()
        if normalized_key in used_keys:
            skipped_bindings.append(f"{binding.key}: {binding.action}")
            continue
        if any(_mpv_key(existing.key).casefold() == normalized_key for existing in selected_bindings):
            continue
        selected_bindings.append(binding)
    input_block = _render_managed_block(
        _NORMAL_MPV_INPUT_BEGIN,
        _NORMAL_MPV_INPUT_END,
        [f"{_mpv_key(binding.key)} {binding.command}" for binding in selected_bindings],
    )
    _atomic_write_text(input_conf, _append_managed_block(plain_input_conf, input_block))
    return NormalMpvSettingsCopyResult(
        config_directory=directory,
        mpv_conf=mpv_conf,
        input_conf=input_conf,
        copied_options=tuple(label for _option, _value, label in selected_options),
        skipped_options=tuple(skipped_options),
        copied_bindings=tuple(f"{binding.key}: {binding.action}" for binding in selected_bindings),
        skipped_bindings=tuple(skipped_bindings),
    )


def _portable_normal_mpv_bindings(shortcuts: dict[str, Any]) -> tuple[ShortcutBinding, ...]:
    """Return bindings that make sense without a running PORTA window."""
    portable: list[ShortcutBinding] = []
    for binding in effective_shortcut_bindings(shortcuts):
        # PORTA連携 means event delivery to the media workspace.  The hold key
        # requires the temporary Lua script, so neither has an equivalent in
        # a plain input.conf.
        if binding.source == f"{PRODUCT_NAME}連携" or binding.command == _HOLD_DOUBLE_SPEED_COMMAND:
            continue
        portable.append(binding)
    return tuple(portable)


def _read_text_without_managed_block(path: Path, begin: str, end: str) -> str:
    try:
        text = path.read_text(encoding="utf-8") if path.exists() else ""
    except OSError as exc:
        raise ValueError(f"通常mpvの設定を読めません: {path}\n{exc}") from exc
    pattern = re.compile(rf"(?:^|\n){re.escape(begin)}.*?{re.escape(end)}\n?", re.DOTALL)
    return pattern.sub("", text).rstrip() + ("\n" if text.strip() else "")


def _configured_mpv_option_names(text: str) -> set[str]:
    names: set[str] = set()
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("["):
            continue
        match = re.match(r"([A-Za-z0-9][A-Za-z0-9_-]*)\s*(?:=|\s)", stripped)
        if match:
            names.add(match.group(1).casefold())
    return names


def _configured_mpv_keys(text: str) -> set[str]:
    keys: set[str] = set()
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        key = stripped.split(maxsplit=1)[0]
        if key:
            keys.add(_mpv_key(key).casefold())
    return keys


def _render_managed_block(begin: str, end: str, lines: Iterable[str]) -> str:
    body = "\n".join(lines)
    return f"{begin}\n{body}\n{end}\n"


def _append_managed_block(text: str, block: str) -> str:
    return f"{text.rstrip()}\n\n{block}" if text.strip() else block


def _atomic_write_text(path: Path, text: str) -> None:
    temporary: Path | None = None
    try:
        previous_mode = path.stat().st_mode & 0o777 if path.exists() else None
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False
        ) as handle:
            handle.write(text)
            temporary = Path(handle.name)
        if previous_mode is not None:
            temporary.chmod(previous_mode)
        os.replace(temporary, path)
    except OSError as exc:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        raise ValueError(f"通常mpvの設定を書き込めません: {path}\n{exc}") from exc


def validate_shortcut_settings(raw: Any) -> dict[str, Any]:
    """Validate and normalize the complete transparent shortcut structure."""
    raw = upgrade_shortcut_settings(raw)
    expected = {"中枢キー", "任意mpvキー"}
    if not isinstance(raw, dict) or set(raw) != expected:
        raise ValueError("mpv_shortcuts の項目は雛形どおりにしてください。")
    core = _validate_core_shortcuts(raw["中枢キー"])
    custom = _validate_custom_shortcuts(raw["任意mpvキー"])
    _ensure_core_keys_are_unique(core)
    return {
        "任意mpvキー": custom,
        "中枢キー": core,
    }


def upgrade_shortcut_settings(raw: Any) -> Any:
    """Fill newly introduced defaults and migrate unchanged old defaults."""
    if not isinstance(raw, dict):
        return raw
    custom = raw.get("任意mpvキー")
    if not isinstance(custom, list) or len(custom) < 5:
        return raw
    blank = {"enabled": False, "label": "", "key": "", "mpv_command": ""}
    defaults = default_shortcut_settings()["任意mpvキー"]
    upgraded = list(custom)
    for index, default in enumerate(defaults[:5]):
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
    result = dict(raw)
    application = raw.get("アプリ連携キー")
    core = raw.get("中枢キー")
    if isinstance(core, dict):
        upgraded_core = {label: dict(value) for label, value in _DEFAULT_CORE_SHORTCUTS.items()}
        upgraded_core.update(core)
        result["中枢キー"] = upgraded_core
    elif isinstance(application, dict):
        result["中枢キー"] = _core_shortcuts_from_legacy(application)
    else:
        result["中枢キー"] = {
            label: dict(value) for label, value in _DEFAULT_CORE_SHORTCUTS.items()
        }
    result.pop("アプリ連携キー", None)
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
        if index < len(upgraded) and upgraded[index] == old_default:
            upgraded[index] = dict(blank)
    # Older templates had five fully empty spare rows.  Retain configured
    # entries, but collapse all blank spares to one visible row.
    configured_tail = [entry for entry in upgraded[5:] if entry != blank]
    upgraded = upgraded[:5] + configured_tail + [dict(blank)]
    result["任意mpvキー"] = upgraded
    return result


def _core_shortcuts_from_legacy(application: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Collapse old per-rating shortcut rows into the compact core-key form."""
    application = dict(application)
    for label in _RETIRED_APPLICATION_SHORTCUTS:
        application.pop(label, None)
    defaults = _DEFAULT_CORE_SHORTCUTS
    start = application.get("見どころ範囲の開始")
    end = application.get("見どころ範囲の終了")
    range_entry = dict(defaults["見どころ範囲"])
    if isinstance(start, dict) and isinstance(end, dict):
        range_entry = {
            "enabled": bool(start.get("enabled", True) and end.get("enabled", True)),
            "start_key": str(start.get("key", "[")).strip(),
            "end_key": str(end.get("key", "]")).strip(),
        }
    rating_entries = [application.get(f"評価 {score}") for score in range(1, 11)]
    rating_entry = dict(defaults["評価 1-10"])
    if all(isinstance(entry, dict) for entry in rating_entries):
        keys = [str(entry.get("key", "")).strip() for entry in rating_entries]
        rating_entry = {
            "enabled": all(bool(entry.get("enabled", True)) for entry in rating_entries),
            "key_sequence": "1-9, 0" if tuple(keys) == _DEFAULT_RATING_KEYS else ", ".join(keys),
        }
    tag = application.get("タグを選ぶ")
    tag_entry = dict(defaults["タグを選ぶ"])
    if isinstance(tag, dict):
        tag_entry = {
            "enabled": bool(tag.get("enabled", True)),
            "key": str(tag.get("key", "t")).strip(),
        }
    return {"見どころ範囲": range_entry, "評価 1-10": rating_entry, "タグを選ぶ": tag_entry}


def _validate_core_shortcuts(raw: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(raw, dict) or set(raw) != set(_DEFAULT_CORE_SHORTCUTS):
        raise ValueError("中枢キーの項目は雛形どおりにしてください。")
    range_raw = raw["見どころ範囲"]
    rating_raw = raw["評価 1-10"]
    tag_raw = raw["タグを選ぶ"]
    if not isinstance(range_raw, dict) or set(range_raw) != {"enabled", "start_key", "end_key"}:
        raise ValueError("中枢キーの「見どころ範囲」は enabled、start_key、end_key を持つ形にしてください。")
    if not isinstance(rating_raw, dict) or set(rating_raw) != {"enabled", "key_sequence"}:
        raise ValueError("中枢キーの「評価 1-10」は enabled と key_sequence を持つ形にしてください。")
    if not isinstance(tag_raw, dict) or set(tag_raw) != {"enabled", "key"}:
        raise ValueError("中枢キーの「タグを選ぶ」は enabled と key を持つ形にしてください。")
    if not isinstance(range_raw["enabled"], bool) or not all(isinstance(range_raw[key], str) for key in ("start_key", "end_key")):
        raise ValueError("中枢キーの「見どころ範囲」の型が正しくありません。")
    if not isinstance(rating_raw["enabled"], bool) or not isinstance(rating_raw["key_sequence"], str):
        raise ValueError("中枢キーの「評価 1-10」の型が正しくありません。")
    if not isinstance(tag_raw["enabled"], bool) or not isinstance(tag_raw["key"], str):
        raise ValueError("中枢キーの「タグを選ぶ」の型が正しくありません。")
    rating_keys = _rating_keys_from_sequence(rating_raw["key_sequence"])
    if range_raw["enabled"]:
        _validate_key(range_raw["start_key"], "中枢キーの「見どころ範囲」の start_key")
        _validate_key(range_raw["end_key"], "中枢キーの「見どころ範囲」の end_key")
    if rating_raw["enabled"]:
        for score, key in enumerate(rating_keys, start=1):
            _validate_key(key, f"中枢キーの「評価 1-10」の {score}")
    if tag_raw["enabled"]:
        _validate_key(tag_raw["key"], "中枢キーの「タグを選ぶ」")
    return {
        "見どころ範囲": {"enabled": range_raw["enabled"], "start_key": range_raw["start_key"].strip(), "end_key": range_raw["end_key"].strip()},
        "評価 1-10": {"enabled": rating_raw["enabled"], "key_sequence": rating_raw["key_sequence"].strip()},
        "タグを選ぶ": {"enabled": tag_raw["enabled"], "key": tag_raw["key"].strip()},
    }


def _rating_keys_from_sequence(sequence: str) -> tuple[str, ...]:
    """Read either compact `1-9, 0` or ten comma-separated mpv keys."""
    if "".join(sequence.split()) == "1-9,0":
        return _DEFAULT_RATING_KEYS
    keys = tuple(part.strip() for part in sequence.split(","))
    if len(keys) != 10 or any(not key for key in keys):
        raise ValueError("中枢キーの「評価 1-10」の key_sequence は「1-9, 0」または10個のキーをカンマ区切りで入力してください。")
    return keys


def _validate_custom_shortcuts(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list) or len(raw) < 5:
        raise ValueError("任意mpvキーは、既定5件を含む一覧にしてください。空欄行は必要に応じて追加できます。")
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


def _core_shortcut_events(core: dict[str, dict[str, Any]]) -> tuple[tuple[str, str, str, str], ...]:
    """Expand the compact user-facing core settings into concrete bindings."""
    rows: list[tuple[str, str, str, str]] = []
    range_entry = core["見どころ範囲"]
    if range_entry["enabled"]:
        rows.extend(
            (
                ("見どころ範囲の開始", str(range_entry["start_key"]), "highlight_range_start", ""),
                ("見どころ範囲の終了", str(range_entry["end_key"]), "highlight_range_end", ""),
            )
        )
    rating_entry = core["評価 1-10"]
    if rating_entry["enabled"]:
        rows.extend(
            (f"評価 {score}", key, "rating", str(score))
            for score, key in enumerate(_rating_keys_from_sequence(str(rating_entry["key_sequence"])), start=1)
        )
    tag_entry = core["タグを選ぶ"]
    if tag_entry["enabled"]:
        rows.append(("タグを選ぶ", str(tag_entry["key"]), "tag_selection", ""))
    return tuple(rows)


def _ensure_core_keys_are_unique(core: dict[str, dict[str, Any]]) -> None:
    used: dict[str, str] = {}
    for label, key, _kind, _value in _core_shortcut_events(core):
        key = _mpv_key(key)
        if key in used:
            raise ValueError(f"中枢キーが重複しています: {used[key]} と {label}")
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
    def tag_selection_done_path(self) -> Path:
        """A one-shot request which returns mpv keys to normal navigation."""
        return self.directory / "tag-selection-done.request"

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
            # mpv normally inserts/removes this pitch-correction filter when
            # speed crosses 1.0. Keeping it active avoids reconfiguring audio
            # at every press and release of the temporary speed key.
            "--af=scaletempo2",
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
        values["PORTA_MPV_TAG_SELECTION_DONE_FILE"] = str(self.tag_selection_done_path)
        return values

    def request_resume(self) -> bool:
        """Ask this still-running disposable player to resume after a note."""
        try:
            self.resume_request_path.touch(mode=0o600, exist_ok=True)
        except OSError:
            return False
        return True

    def finish_tag_selection(self) -> bool:
        """Tell mpv that the transient external tag chooser has closed."""
        try:
            self.tag_selection_done_path.touch(mode=0o600, exist_ok=True)
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
        "local tag_selection_done_file = os.getenv('PORTA_MPV_TAG_SELECTION_DONE_FILE')",
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
        "local tag_selection_active = false",
        "local function consume_tag_selection_done()",
        "  if not tag_selection_done_file then return end",
        "  local handle = io.open(tag_selection_done_file, 'r')",
        "  if not handle then return end",
        "  handle:close()",
        "  os.remove(tag_selection_done_file)",
        "  tag_selection_active = false",
        "end",
        "mp.add_periodic_timer(0.1, consume_tag_selection_done)",
        "local temporary_speed_original = nil",
        "local temporary_speed_serial = 0",
        "local function restore_temporary_speed()",
        "  if temporary_speed_original == nil then return end",
        "  mp.set_property_number('speed', temporary_speed_original)",
        "  temporary_speed_original = nil",
        "end",
        "local function hold_double_speed(event)",
        "  if event.event == 'down' then",
        "    if temporary_speed_original == nil then",
        "      temporary_speed_serial = temporary_speed_serial + 1",
        "      temporary_speed_original = mp.get_property_number('speed', 1) or 1",
        "      mp.set_property_number('speed', 2)",
        "    end",
        "  elseif event.event == 'up' then",
        "    temporary_speed_serial = temporary_speed_serial + 1",
        "    restore_temporary_speed()",
        "  elseif event.event == 'press' then",
        "    temporary_speed_serial = temporary_speed_serial + 1",
        "    local serial = temporary_speed_serial",
        "    if temporary_speed_original == nil then",
        "      temporary_speed_original = mp.get_property_number('speed', 1) or 1",
        "      mp.set_property_number('speed', 2)",
        "    end",
        "    mp.add_timeout(0.35, function()",
        "      if serial == temporary_speed_serial then restore_temporary_speed() end",
        "    end)",
        "  end",
        "end",
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
        if enable_tag_selection:
            body = _tag_selection_aware_binding_body(mpv_key, body)
        event_argument = "event" if complex_events else ""
        flags = ", {complex = true}" if complex_events else ""
        lines.append(
            f"mp.add_forced_key_binding({_lua_string(mpv_key)}, {_lua_string(f'porta-{binding_index}')}, function({event_argument}) {body} end{flags})"
        )
        return True

    for _label, key, kind, value in _core_shortcut_events(shortcuts["中枢キー"]):
        if kind == "tag_selection" and not enable_tag_selection:
            continue
        if kind == "rating" and not enable_rating_selection:
            continue
        emit = f"emit({_lua_string(kind)}, {_lua_string(value)})"
        if kind == "highlight_range_start":
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
        elif kind == "tag_selection":
            body = (
                "if not tag_selection_active then "
                "tag_selection_active = true; "
                f"notify({_lua_string('タグを選択して追加します')}); {emit} end"
            )
        else:  # The core event set is closed; remaining events are ratings.
            body = f"notify({_lua_string('評価: ' + value + ' / 10')}); {emit}"
        bind(key, body)
    for entry in shortcuts["任意mpvキー"]:
        if _is_seek_command(str(entry["mpv_command"])):
            body = _seek_binding_body(str(entry["mpv_command"]), str(entry["label"]))
        else:
            body = (
                f"mp.command({_lua_string(entry['mpv_command'])}); "
                f"show_controller(); notify({_lua_string(entry['label'])})"
            )
        if entry["enabled"] and not bind(entry["key"], body):
            warnings.append(f"任意mpvキー「{entry['label']}」は中枢キーまたは先の任意キーと重複するため使いません。")

    # Bind the review shortcuts explicitly. Exact seeks suppress mpv's own
    # OSD, while every playback operation reopens the standard slim OSC.
    for key, action, command in WORKFLOW_STANDARD_SHORTCUTS:
        if command in _NATIVE_HOLD_BINDING_COMMANDS:
            continue
        if key in {"Left", "Right"} and command in {"seek -5 exact", "seek 5 exact"}:
            bind(key, _hybrid_arrow_seek_binding_body(command, action), complex_events=True)
            continue
        if command == _HOLD_DOUBLE_SPEED_COMMAND:
            bind(key, "hold_double_speed(event)", complex_events=True)
            continue
        if _is_seek_command(command):
            bind(key, _seek_binding_body(command, action))
        else:
            bind(key, f"mp.command({_lua_string(command)}); show_controller()")
    return "\n".join(lines) + "\n", warnings


def _tag_selection_aware_binding_body(mpv_key: str, ordinary_body: str) -> str:
    """Route navigation back to the chooser if the OS leaves focus on mpv."""
    tag_events = {
        "UP": ("tag_navigation", "-1"),
        "DOWN": ("tag_navigation", "1"),
        "ENTER": ("tag_accept", ""),
    }
    event = tag_events.get(mpv_key)
    if event is None:
        return ordinary_body
    kind, value = event
    return (
        f"if tag_selection_active then emit({_lua_string(kind)}, {_lua_string(value)}) "
        f"else {ordinary_body} end"
    )


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
