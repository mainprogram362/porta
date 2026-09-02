"""Small, non-persistent yt-dlp gateway used by media tools.

This module deliberately has no GUI, configuration file, download history, or
automatic filesystem writes. Callers inspect first and choose explicitly when
to download or export data.
"""

from __future__ import annotations

import asyncio
import csv
import importlib.util
import json
import os
import re
import shutil
import select
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from threading import RLock
from typing import Any, Callable, Iterable
from urllib.parse import urlparse

from yt_dlp import YoutubeDL, YoutubeDL as _YtdlpFormatSelector

from .video_catalog import CatalogVideo, catalog_timestamp


YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"}
_ANSI_ESCAPE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")

# The provider is deliberately kept outside the Git repository. It is a
# pinned third-party runtime, not application data, and it can be removed
# without affecting ordinary downloads.
_PORTA_ROOT = Path(__file__).resolve().parents[4]
_POT_PROVIDER_ROOT = _PORTA_ROOT / "private_space" / "private_apps" / "youtube_pot_provider"
_POT_PROVIDER_SERVER_HOME = _POT_PROVIDER_ROOT / "bgutil-ytdlp-pot-provider" / "server"
_POT_PROVIDER_DENO_CACHE = _POT_PROVIDER_ROOT / "deno_cache"
_POT_ENVIRONMENT_LOCK = RLock()
_WPC_BROWSER_TIMEOUT_SECONDS = 35
_WPC_TOKEN_TIMEOUT_SECONDS = 35
_WPC_READY_TIMEOUT_SECONDS = 90
_WPC_DOWNLOAD_TIMEOUT_SECONDS = 60 * 60


@dataclass(frozen=True)
class VideoRecord:
    """Minimal, useful information about one YouTube video."""

    title: str
    url: str
    video_id: str
    upload_date: str = ""
    duration_seconds: int | None = None
    channel_name: str = ""
    source_kind: str = "video"


@dataclass(frozen=True)
class DownloadReport:
    """Outcome of one explicit download run; it is not persisted."""

    succeeded: int
    failed: int
    messages: tuple[str, ...]
    catalog_videos: tuple[CatalogVideo, ...] = ()


@dataclass(frozen=True)
class ChannelExportReport:
    """Paths produced by one explicit channel-list export."""

    urls_path: Path
    details_path: Path
    count: int


@dataclass(frozen=True)
class CreatorOverview:
    """The complete public list sizes obtained before detail collection starts."""

    video_total: int
    short_total: int

    @property
    def total(self) -> int:
        return self.video_total + self.short_total


@dataclass(frozen=True)
class CreatorProgress:
    """One detail lookup attempt during creator-list collection."""

    source_kind: str
    completed_in_kind: int
    total_in_kind: int
    completed_total: int
    total: int
    record: VideoRecord | None = None
    error: str = ""


@dataclass(frozen=True)
class CreatorCollectionResult:
    """In-memory result of a creator-list collection, including safe cancellation."""

    records: tuple[VideoRecord, ...]
    errors: tuple[str, ...]
    overview: CreatorOverview
    cancelled: bool


@dataclass(frozen=True)
class _HighQualityFormat:
    """An exact adaptive video/audio pair used to prevent quality downgrades."""

    selector: str
    format_ids: tuple[str, ...]


def split_urls(text: str) -> list[str]:
    """Return non-empty, de-duplicated URL lines while retaining their order."""
    result: list[str] = []
    seen: set[str] = set()
    for raw in text.splitlines():
        url = raw.strip()
        if url and url not in seen:
            result.append(url)
            seen.add(url)
    return result


def is_youtube_url(value: str) -> bool:
    """Check that a URL belongs to a public YouTube host name."""
    parsed = urlparse(value.strip())
    return parsed.scheme in {"http", "https"} and parsed.netloc.lower() in YOUTUBE_HOSTS


def _format_upload_date(value: Any) -> str:
    text = str(value or "").strip()
    if len(text) == 8 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:]}"
    return text


def _record_from_info(info: dict[str, Any], *, source_kind: str = "video") -> VideoRecord:
    video_id = str(info.get("id") or "").strip()
    url = str(info.get("webpage_url") or info.get("original_url") or "").strip()
    if not url and video_id:
        url = f"https://www.youtube.com/watch?v={video_id}"
    title = str(info.get("title") or video_id or "(題名を取得できませんでした)")
    duration = info.get("duration")
    return VideoRecord(
        title=title,
        url=url,
        video_id=video_id,
        upload_date=_format_upload_date(info.get("upload_date")),
        duration_seconds=int(duration) if isinstance(duration, (int, float)) else None,
        channel_name=str(info.get("channel") or info.get("uploader") or ""),
        source_kind=source_kind,
    )


def _base_options() -> dict[str, Any]:
    options: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        # A stalled connection must not leave a GUI worker or WPC preparation
        # waiting indefinitely. Normal transfers may be long, but each socket
        # operation has a bounded wait.
        "socket_timeout": 25,
        # Avoid yt-dlp's hidden persistent cache. The app only persists its
        # explicitly editable output directories.
        "cachedir": False,
    }
    deno_path = _find_deno_path()
    if deno_path is not None:
        # Pin the user-local runtime explicitly so the GUI works even when it
        # was launched from a desktop environment with a short PATH.
        options["js_runtimes"] = {"deno": {"path": str(deno_path)}}
    return options


def _youtube_client_options(client: str | None) -> dict[str, Any]:
    """Return an explicit YouTube client configuration without credentials."""
    if client is None:
        return {}
    return {"extractor_args": {"youtube": {"player_client": [client]}}}


def _module_is_available(module: str) -> bool:
    """Treat an absent optional plugin namespace as an unavailable module."""
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ModuleNotFoundError):
        return False


def _po_token_provider_status() -> tuple[bool, str]:
    """Check local PO Token prerequisites without generating a token."""
    script = _POT_PROVIDER_SERVER_HOME / "src" / "generate_once.ts"
    node_modules = _POT_PROVIDER_SERVER_HOME / "node_modules"
    if not script.is_file() or not node_modules.is_dir():
        return False, "ローカルPO Token Providerが未導入です。"
    if not _module_is_available("yt_dlp_plugins.extractor.getpot_bgutil_script"):
        return False, "yt-dlp用のPO Token Providerプラグインが未導入です。"
    if _find_deno_path() is None:
        return False, "PO Token Provider用のDenoが見つかりません。"
    return True, "Cookieを使わず、一時PO Token補助を利用できます。"


def _find_wpc_browser_path() -> Path | None:
    """Locate a Chromium/Chrome binary for the WPC flow without persisting state."""
    candidates = [
        "chromium",
        "chromium-browser",
        "google-chrome",
        "google-chrome-stable",
        "google-chrome-beta",
        "chrome",
    ]
    for name in candidates:
        path = shutil.which(name)
        if path:
            return Path(path)
    search_roots = [
        Path("/usr/bin"),
        Path("/usr/local/bin"),
        Path.home() / ".local" / "bin",
        Path.home() / ".local" / "share" / "applications",
    ]
    for root in search_roots:
        for name in candidates:
            candidate = root / name
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return candidate
    return None


def po_token_provider_status() -> tuple[bool, str]:
    """Public GUI check that never creates files or makes a network request."""
    return _po_token_provider_status()


def wpc_provider_status() -> tuple[bool, str]:
    """Public check for the temporary Chromium/WPC path without creating user data."""
    browser = _find_wpc_browser_path()
    if browser is None:
        return False, "WPCは Chromium/Chrome が必要です。ブラウザが見つかりません。"
    candidates = (
        "yt_dlp_plugins.extractor.getpot_wpc",
        "yt_dlp_plugins.extractor.getpot_wpc_script",
        "yt_dlp_plugins.extractor.getpot_wpc_driver",
    )
    if not any(_module_is_available(module) for module in candidates):
        return False, "WPC プラグインが未導入です。"
    return True, f"一時プロファイルで WPC を使えます。ブラウザ: {browser}"


class _EphemeralPoTokenEnvironment:
    """Keep the script Provider's mandatory token cache in a temporary dir."""

    def __enter__(self) -> None:
        self._lock = _POT_ENVIRONMENT_LOCK
        self._lock.acquire()
        self._temporary_directory = tempfile.TemporaryDirectory(prefix="porta-ytdlp-pot-")
        self._previous = {name: os.environ.get(name) for name in ("XDG_CACHE_HOME", "TOKEN_TTL", "DENO_DIR")}
        os.environ["XDG_CACHE_HOME"] = self._temporary_directory.name
        # The upstream script writes a cache by design. Disable reuse and put
        # even that short-lived file in the disposable directory.
        os.environ["TOKEN_TTL"] = "0"
        os.environ["DENO_DIR"] = str(_POT_PROVIDER_DENO_CACHE)

    def __exit__(self, *_args: object) -> None:
        try:
            for name, previous_value in self._previous.items():
                if previous_value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = previous_value
            self._temporary_directory.cleanup()
        finally:
            self._lock.release()


class _EphemeralWpcProfile:
    """Create and remove the only Chromium profile used by a WPC run.

    The profile is on tmpfs in this Linux environment, never points at a real
    browser profile, and is passed directly to nodriver as ``--user-data-dir``.
    Environment variables alone are not enough because nodriver otherwise
    creates its own profile directory.
    """

    def __enter__(self) -> Path:
        memory_tmp = Path("/dev/shm")
        parent = str(memory_tmp) if memory_tmp.is_dir() else "/tmp"
        self.path = Path(tempfile.mkdtemp(prefix="porta-wpc-", dir=parent))
        return self.path

    def __exit__(self, *_args: object) -> None:
        if hasattr(self, "path"):
            shutil.rmtree(self.path, ignore_errors=True)


class _EphemeralWpcProvider:
    """Run WPC alone with a disposable Chromium profile, then restore yt-dlp.

    Both installed providers register globally inside yt-dlp.  BgUtil has a
    higher preference, so simply enabling WPC would never select it.  This
    context temporarily presents only WPC to the single YoutubeDL instance;
    the global registry and the plugin method are restored before returning to
    the rest of the application.
    """

    def __init__(self, browser_path: Path, *, profile_path: Path | None = None) -> None:
        self._browser_path = browser_path
        self._provided_profile_path = profile_path

    def __enter__(self) -> Path:
        self._lock = _POT_ENVIRONMENT_LOCK
        self._lock.acquire()
        self._profile = None
        if self._provided_profile_path is None:
            self._profile = _EphemeralWpcProfile()
            self.path = self._profile.__enter__()
        else:
            self.path = self._provided_profile_path
        try:
            from yt_dlp.extractor.youtube.pot._registry import _pot_providers, _ptp_preferences

            # yt-dlp discovers external plugins while a YoutubeDL instance is
            # constructed. Load that registry before narrowing it to WPC.
            if "WPC" not in _pot_providers.value:
                with YoutubeDL(_base_options()):
                    pass

            self._providers = _pot_providers
            self._preferences = _ptp_preferences
            self._original_provider_registry = _pot_providers.value
            self._original_preference_registry = _ptp_preferences.value
            wpc_provider = _pot_providers.value.get("WPC")
            if wpc_provider is None:
                raise RuntimeError("WPC プラグインをyt-dlpへ読み込めませんでした。")
            self._wpc_provider = wpc_provider
            self._original_get_config = wpc_provider.get_nodriver_config
            module = sys.modules.get(wpc_provider.__module__)
            if module is None:
                raise RuntimeError("WPC プラグインの実行モジュールを読み込めませんでした。")
            self._wpc_module = module
            self._original_launch_browser = module.launch_browser
            self._original_mint_po_token = module.mint_po_token

            profile_path = self.path
            browser_path = self._browser_path

            def get_disposable_config(provider: object, proxy: str | None = None) -> object:
                config = self._original_get_config(provider, proxy)
                config.browser_executable_path = str(browser_path)
                config.user_data_dir = str(profile_path)
                # Do not synchronize, restore, or retain browser activity.
                config.add_argument("--disable-sync")
                config.add_argument("--disable-background-networking")
                config.add_argument("--disable-component-update")
                return config

            async def launch_with_timeout(config: object) -> object:
                try:
                    return await asyncio.wait_for(
                        self._original_launch_browser(config),
                        timeout=_WPC_BROWSER_TIMEOUT_SECONDS,
                    )
                except TimeoutError as exc:
                    raise RuntimeError(
                        f"一時Chromiumの起動が{_WPC_BROWSER_TIMEOUT_SECONDS}秒以内に完了しませんでした。"
                    ) from exc

            async def mint_with_timeout(*args: object, **kwargs: object) -> object:
                try:
                    return await asyncio.wait_for(
                        self._original_mint_po_token(*args, **kwargs),
                        timeout=_WPC_TOKEN_TIMEOUT_SECONDS,
                    )
                except TimeoutError as exc:
                    raise RuntimeError(
                        f"一時ブラウザPO Tokenの取得が{_WPC_TOKEN_TIMEOUT_SECONDS}秒以内に完了しませんでした。"
                    ) from exc

            wpc_provider.get_nodriver_config = get_disposable_config
            module.launch_browser = launch_with_timeout
            module.mint_po_token = mint_with_timeout
            _pot_providers.value = {wpc_provider.PROVIDER_KEY: wpc_provider}
            # A sole provider needs no priority callback. This also avoids
            # accidentally applying BgUtil's higher global priority.
            _ptp_preferences.value = set()
            return self.path
        except Exception:
            if self._profile is not None:
                self._profile.__exit__()
            self._lock.release()
            raise

    def __exit__(self, *_args: object) -> None:
        try:
            self._providers.value = self._original_provider_registry
            self._preferences.value = self._original_preference_registry
            self._wpc_provider.get_nodriver_config = self._original_get_config
            self._wpc_module.launch_browser = self._original_launch_browser
            self._wpc_module.mint_po_token = self._original_mint_po_token
        finally:
            if self._profile is not None:
                self._profile.__exit__()
            self._lock.release()


def _po_token_options() -> dict[str, Any]:
    """Select mweb and let yt-dlp request a token from the local script."""
    return {
        "extractor_args": {
            "youtube": {"player_client": ["mweb"]},
            "youtubepot-bgutilscript": {"server_home": [str(_POT_PROVIDER_SERVER_HOME)]},
        }
    }


def _wpc_options(browser_path: Path) -> dict[str, Any]:
    """Select mweb and explicitly give WPC the disposable Chromium binary."""
    return {
        "extractor_args": {
            "youtube": {"player_client": ["mweb"]},
            "youtubepot-wpc": {"browser_path": [str(browser_path)]},
        }
    }


def _selected_quality_summary(
    info: dict[str, Any], selection: _HighQualityFormat
) -> dict[str, Any] | None:
    streams = _selected_streams(info, selection)
    if streams is None:
        return None
    video, audio = streams
    return {
        "selector": selection.selector,
        "video": {key: video.get(key) for key in ("vcodec", "width", "height", "fps", "tbr", "dynamic_range")},
        "audio": {key: audio.get(key) for key in ("acodec", "abr", "tbr")},
    }


def _summary_is_no_lower_quality(candidate: dict[str, Any], reference: dict[str, Any]) -> bool:
    candidate_video = candidate.get("video", {})
    candidate_audio = candidate.get("audio", {})
    reference_video = reference.get("video", {})
    reference_audio = reference.get("audio", {})
    if candidate_video.get("vcodec") != reference_video.get("vcodec"):
        return False
    if candidate_audio.get("acodec") != reference_audio.get("acodec"):
        return False
    if candidate_video.get("dynamic_range") != reference_video.get("dynamic_range"):
        return False
    return all(
        _number_is_not_lower(candidate_video.get(field), reference_video.get(field))
        for field in ("width", "height", "fps", "tbr")
    ) and all(
        _number_is_not_lower(candidate_audio.get(field), reference_audio.get(field))
        for field in ("abr", "tbr")
    )


def _select_high_quality_format(info: dict[str, Any]) -> _HighQualityFormat | None:
    """Ask yt-dlp itself which pair its normal highest-quality rule selects.

    Reimplementing yt-dlp's sorting would risk choosing a lower format. The
    selected IDs are later used to allow a fallback only when it exposes that
    exact same pair.
    """
    formats = [item for item in info.get("formats", []) if isinstance(item, dict)]
    if not formats:
        return None
    options = {"quiet": True, "no_warnings": True, "format": "bestvideo*+bestaudio/best", "merge_output_format": "mp4"}
    with _YtdlpFormatSelector(options) as ydl:
        selector = ydl.build_format_selector(options["format"])
        selected = ydl._select_formats(formats, selector)
    if not selected:
        return None
    selected_formats = selected[0].get("requested_formats", (selected[0],))
    format_ids = tuple(str(item.get("format_id") or "") for item in selected_formats)
    if not format_ids or any(not format_id for format_id in format_ids):
        return None
    return _HighQualityFormat("+".join(format_ids), format_ids)


def _offers_exact_format(info: dict[str, Any], selection: _HighQualityFormat) -> bool:
    available = {
        str(item.get("format_id"))
        for item in info.get("formats", [])
        if isinstance(item, dict) and item.get("format_id")
    }
    return all(format_id in available for format_id in selection.format_ids)


def _fetch_video_info(
    url: str,
    *,
    client: str | None = None,
    use_po_token_provider: bool = False,
    use_wpc_provider: bool = False,
) -> dict[str, Any]:
    if use_po_token_provider and use_wpc_provider:
        raise ValueError("PO Token Providerは一度に1つだけ選択してください。")
    options: dict[str, Any] = {
        **_base_options(),
        **_youtube_client_options(client),
        "skip_download": True,
        "noplaylist": True,
    }
    if use_po_token_provider:
        options["extractor_args"] = _po_token_options()["extractor_args"]
        with _EphemeralPoTokenEnvironment():
            with YoutubeDL(options) as ydl:
                info = ydl.extract_info(url, download=False)
    elif use_wpc_provider:
        ready, message = wpc_provider_status()
        if not ready:
            raise RuntimeError(message)
        browser_path = _find_wpc_browser_path()
        assert browser_path is not None
        options["extractor_args"] = _wpc_options(browser_path)["extractor_args"]
        with _EphemeralWpcProvider(browser_path):
            with YoutubeDL(options) as ydl:
                info = ydl.extract_info(url, download=False)
    else:
        with YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=False)
    if not isinstance(info, dict):
        raise ValueError("動画情報を取得できませんでした。")
    return info


def _selected_format_items(
    info: dict[str, Any], selection: _HighQualityFormat
) -> tuple[dict[str, Any], ...] | None:
    formats = {
        str(item.get("format_id")): item
        for item in info.get("formats", [])
        if isinstance(item, dict) and item.get("format_id")
    }
    try:
        return tuple(formats[format_id] for format_id in selection.format_ids)
    except KeyError:
        return None


def _selected_streams(
    info: dict[str, Any], selection: _HighQualityFormat
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """Return the selected video and audio streams, including a muxed stream."""
    selected = _selected_format_items(info, selection)
    if not selected:
        return None
    video = next((item for item in selected if item.get("vcodec") not in {None, "none"}), None)
    audio = next((item for item in selected if item.get("acodec") not in {None, "none"}), None)
    if not isinstance(video, dict) or not isinstance(audio, dict):
        return None
    return video, audio


def _number_is_not_lower(candidate: Any, reference: Any) -> bool:
    """Compare a numeric field conservatively when the reference defines it."""
    if reference is None:
        return True
    if candidate is None:
        return False
    try:
        return float(candidate) >= float(reference)
    except (TypeError, ValueError):
        return False


def _offers_no_lower_quality(
    reference_info: dict[str, Any],
    reference: _HighQualityFormat,
    candidate_info: dict[str, Any],
    candidate: _HighQualityFormat,
) -> bool:
    """Accept a PO route only when its selected streams are not lower quality.

    yt-dlp may suffix an otherwise identical audio ID (for example ``251-12``)
    when a client exposes duplicate representations.  Comparing IDs alone
    would reject that same-quality case.  Instead, retain strict observable
    output properties: video codec, resolution, frame rate, dynamic range and
    bitrate; audio codec and bitrate.  Unknown candidate values fail closed.
    """
    reference_streams = _selected_streams(reference_info, reference)
    candidate_streams = _selected_streams(candidate_info, candidate)
    if reference_streams is None or candidate_streams is None:
        return False
    reference_video, reference_audio = reference_streams
    candidate_video, candidate_audio = candidate_streams
    if candidate_video.get("vcodec") != reference_video.get("vcodec"):
        return False
    if candidate_audio.get("acodec") != reference_audio.get("acodec"):
        return False
    if candidate_video.get("dynamic_range") != reference_video.get("dynamic_range"):
        return False
    return all(
        _number_is_not_lower(candidate_video.get(field), reference_video.get(field))
        for field in ("width", "height", "fps", "tbr")
    ) and all(
        _number_is_not_lower(candidate_audio.get(field), reference_audio.get(field))
        for field in ("abr", "tbr")
    )


def _is_stream_forbidden(exc: Exception) -> bool:
    message = str(exc)
    return "HTTP Error 403" in message or "403: Forbidden" in message


def _error_text(exc: Exception) -> str:
    """Remove terminal-only color control characters before showing GUI text."""
    text = _ANSI_ESCAPE.sub("", str(exc)).strip()
    return text or exc.__class__.__name__


def _run_wpc_download_worker(
    url: str,
    output_directory: Path,
    reference_info: dict[str, Any],
    reference_selection: _HighQualityFormat,
) -> str:
    """Run the fragile browser-backed path outside the GUI process.

    The child first emits a ready record after it has minted a token and
    confirmed equal-or-better quality.  That phase is strictly timed.  Once a
    download has actually started, the child receives a longer, practical time
    allowance without ever blocking the main GUI process.
    """
    browser_path = _find_wpc_browser_path()
    if browser_path is None:
        raise RuntimeError("WPCは Chromium/Chrome が必要です。ブラウザが見つかりません。")
    reference_summary = _selected_quality_summary(reference_info, reference_selection)
    if reference_summary is None:
        raise RuntimeError("通常経路の最高画質を比較できませんでした。")
    request = {
        "url": url,
        "output_directory": str(output_directory),
        "browser_path": str(browser_path),
        "reference": reference_summary,
    }
    with _EphemeralWpcProfile() as profile_path:
        request["profile_path"] = str(profile_path)
        process = subprocess.Popen(
            [sys.executable, "-m", "media.wpc_worker"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            bufsize=1,
        )
        try:
            assert process.stdin is not None
            process.stdin.write(json.dumps(request, ensure_ascii=False) + "\n")
            process.stdin.close()
            process.stdin = None
            assert process.stdout is not None
            readable, _unused, _errors = select.select([process.stdout], [], [], _WPC_READY_TIMEOUT_SECONDS)
            if not readable:
                process.kill()
                raise RuntimeError(
                    f"一時ブラウザPO Tokenの準備が{_WPC_READY_TIMEOUT_SECONDS}秒以内に完了しませんでした。"
                )
            first_line = process.stdout.readline().strip()
            if not first_line:
                stdout, stderr = process.communicate(timeout=5)
                detail = (stderr or stdout).strip()
                raise RuntimeError(detail or "一時ブラウザPO Tokenの子プロセスが準備前に終了しました。")
            first = json.loads(first_line)
            if first.get("state") != "ready":
                raise RuntimeError(str(first.get("error") or "一時ブラウザPO Tokenの準備に失敗しました。"))
            stdout, stderr = process.communicate(timeout=_WPC_DOWNLOAD_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired as exc:
            process.kill()
            raise RuntimeError("一時ブラウザPO Token経路のダウンロードが1時間以内に完了しませんでした。") from exc
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
        lines = [line for line in stdout.splitlines() if line.strip()]
        if process.returncode:
            detail = (stderr or "\n".join(lines)).strip()
            raise RuntimeError(detail or "一時ブラウザPO Tokenの子プロセスが異常終了しました。")
        if not lines:
            raise RuntimeError("一時ブラウザPO Tokenの完了結果を取得できませんでした。")
        result = json.loads(lines[-1])
        if result.get("state") != "complete":
            raise RuntimeError(str(result.get("error") or "一時ブラウザPO Tokenのダウンロードに失敗しました。"))
        return str(result.get("selector") or reference_selection.selector)


def _find_deno_path() -> Path | None:
    """Find an optional Deno runtime without creating files or changing PATH."""
    candidates = [
        shutil.which("deno"),
        str(Path.home() / ".local" / "bin" / "deno"),
        str(Path.home() / ".deno" / "bin" / "deno"),
    ]
    for raw_path in candidates:
        if not raw_path:
            continue
        candidate = Path(raw_path)
        if candidate.is_file() and candidate.stat().st_mode & 0o111:
            return candidate
    return None


def inspect_videos(urls: Iterable[str]) -> tuple[list[VideoRecord], list[str]]:
    """Read video information without downloading or creating output folders."""
    records: list[VideoRecord] = []
    errors: list[str] = []
    with YoutubeDL({**_base_options(), "skip_download": True, "noplaylist": True}) as ydl:
        for url in urls:
            if not is_youtube_url(url):
                errors.append(f"YouTube URLではありません: {url}")
                continue
            try:
                info = ydl.extract_info(url, download=False)
                if not isinstance(info, dict):
                    raise ValueError("動画情報を取得できませんでした。")
                record = _record_from_info(info)
                if not record.url:
                    raise ValueError("動画URLを取得できませんでした。")
                records.append(record)
            except Exception as exc:
                errors.append(f"確認できませんでした: {url} ({exc})")
    return records, errors


def _creator_tab_urls(creator_url: str) -> tuple[tuple[str, str], ...]:
    """Build normal-video and Shorts tabs from a creator/channel URL."""
    cleaned = creator_url.strip().rstrip("/")
    parsed = urlparse(cleaned)
    if not is_youtube_url(cleaned) or parsed.netloc.lower() == "youtu.be":
        raise ValueError("YouTube の投稿者・チャンネルURLを入力してください。")
    root = cleaned
    for suffix in ("/videos", "/shorts", "/streams", "/playlists", "/featured"):
        if root.endswith(suffix):
            root = root[: -len(suffix)]
            break
    return (("video", f"{root}/videos"), ("short", f"{root}/shorts"))


def collect_creator_videos(
    creator_url: str,
    *,
    on_overview: Callable[[CreatorOverview], None] | None = None,
    on_progress: Callable[[CreatorProgress], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> CreatorCollectionResult:
    """Collect a creator's newest videos and Shorts one detail item at a time.

    The public tab lists are collected first, so the caller can show the total
    before slower per-video metadata requests begin. Cancellation is checked
    before every network request; an in-flight request is allowed to finish so
    no worker thread or partially written output has to be force-killed.
    """
    def cancelled() -> bool:
        return bool(should_cancel and should_cancel())

    errors: list[str] = []
    entries_by_kind: dict[str, list[dict[str, Any]]] = {"video": [], "short": []}
    seen_ids: set[str] = set()
    # Phase 1: obtain only the public list structure and exact available counts.
    with YoutubeDL({**_base_options(), "skip_download": True, "extract_flat": True}) as ydl:
        for source_kind, tab_url in _creator_tab_urls(creator_url):
            if cancelled():
                overview = CreatorOverview(
                    video_total=len(entries_by_kind["video"]),
                    short_total=len(entries_by_kind["short"]),
                )
                if on_overview:
                    on_overview(overview)
                return CreatorCollectionResult((), tuple(errors), overview, True)
            try:
                info = ydl.extract_info(tab_url, download=False)
                raw_entries = info.get("entries", []) if isinstance(info, dict) else []
                for entry in raw_entries:
                    if not isinstance(entry, dict):
                        continue
                    flat_record = _record_from_info(entry, source_kind=source_kind)
                    identity = flat_record.video_id or flat_record.url
                    if not identity or identity in seen_ids:
                        continue
                    seen_ids.add(identity)
                    entries_by_kind[source_kind].append(entry)
            except Exception as exc:
                errors.append(f"{source_kind} 一覧を取得できませんでした: {exc}")

    overview = CreatorOverview(
        video_total=len(entries_by_kind["video"]),
        short_total=len(entries_by_kind["short"]),
    )
    if on_overview:
        on_overview(overview)
    records: list[VideoRecord] = []
    completed_total = 0
    # Phase 2: look up full metadata in the ordering returned by each tab.
    with YoutubeDL({**_base_options(), "skip_download": True, "noplaylist": True}) as ydl:
        for source_kind in ("video", "short"):
            entries = entries_by_kind[source_kind]
            for completed_in_kind, entry in enumerate(entries, start=1):
                if cancelled():
                    return CreatorCollectionResult(tuple(records), tuple(errors), overview, True)
                completed_total += 1
                flat_record = _record_from_info(entry, source_kind=source_kind)
                try:
                    info = ydl.extract_info(flat_record.url, download=False)
                    if not isinstance(info, dict):
                        raise ValueError("動画情報を取得できませんでした。")
                    record = _record_from_info(info, source_kind=source_kind)
                    if not record.url:
                        raise ValueError("動画URLを取得できませんでした。")
                    records.append(record)
                    progress = CreatorProgress(
                        source_kind,
                        completed_in_kind,
                        len(entries),
                        completed_total,
                        overview.total,
                        record=record,
                    )
                except Exception as exc:
                    message = f"{source_kind} の詳細を取得できませんでした: {flat_record.url} ({exc})"
                    errors.append(message)
                    progress = CreatorProgress(
                        source_kind,
                        completed_in_kind,
                        len(entries),
                        completed_total,
                        overview.total,
                        error=message,
                    )
                if on_progress:
                    on_progress(progress)
    return CreatorCollectionResult(tuple(records), tuple(errors), overview, False)


def _safe_name(value: str) -> str:
    cleaned = re.sub(r"[^0-9A-Za-z._-]+", "_", value).strip("._")
    return cleaned[:80] or "youtube_channel"


def _unique_path(directory: Path, stem: str, suffix: str) -> Path:
    candidate = directory / f"{stem}{suffix}"
    index = 1
    while candidate.exists():
        candidate = directory / f"{stem} ({index}){suffix}"
        index += 1
    return candidate


def export_creator_videos(
    records: Iterable[VideoRecord], export_directory: Path, *, creator_label: str
) -> ChannelExportReport:
    """Write a paste-ready URL list and a small CSV metadata file explicitly."""
    items = list(records)
    if not items:
        raise ValueError("書き出す動画情報がありません。先に一覧を取得してください。")
    export_directory = export_directory.expanduser()
    export_directory.mkdir(parents=True, exist_ok=True)
    if not export_directory.is_dir():
        raise ValueError("情報出力先がフォルダではありません。")
    timestamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    stem = f"{_safe_name(creator_label)}_{timestamp}"
    urls_path = _unique_path(export_directory, f"{stem}_urls", ".txt")
    details_path = _unique_path(export_directory, f"{stem}_info", ".csv")
    acquired_at = datetime.now().astimezone().isoformat(timespec="seconds")
    urls_path.write_text("\n".join(item.url for item in items) + "\n", encoding="utf-8")
    with details_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["title", "url", "upload_date", "acquired_at", "channel", "kind"])
        for item in items:
            writer.writerow(
                [item.title, item.url, item.upload_date, acquired_at, item.channel_name, item.source_kind]
            )
    return ChannelExportReport(urls_path=urls_path, details_path=details_path, count=len(items))


def download_videos(
    records: Iterable[VideoRecord],
    output_directory: Path,
    *,
    progress: Callable[[str], None] | None = None,
    allow_po_token_provider: bool = False,
    allow_wpc_provider: bool = False,
) -> DownloadReport:
    """Download previously inspected videos after the caller explicitly requests it."""
    items = list(records)
    if not items:
        raise ValueError("ダウンロードする確認済み動画がありません。")
    output_directory = output_directory.expanduser()
    output_directory.mkdir(parents=True, exist_ok=True)
    if not output_directory.is_dir():
        raise ValueError("ダウンロード先がフォルダではありません。")
    messages: list[str] = []
    catalog_videos: list[CatalogVideo] = []
    succeeded = 0
    failed = 0
    for item in items:
        if progress:
            progress(item.title)
        selection: _HighQualityFormat | None = None
        existing_output_files = _output_file_snapshot(output_directory)
        try:
            primary_info = _fetch_video_info(item.url)
            selection = _select_high_quality_format(primary_info)
            selector = selection.selector if selection is not None else "bestvideo*+bestaudio/best"
            _download_one(item.url, output_directory, selector=selector, client=None, restart=False)
            succeeded += 1
            catalog_videos.append(
                _catalog_video_for_download(item, output_directory, existing_output_files, selector)
            )
            messages.append(f"完了: {item.title}")
            continue
        except Exception as exc:
            primary_error = exc

        # A no-credential fallback is useful only for YouTube stream 403s.
        # It is deliberately strict: a different client must expose the exact
        # same video/audio format IDs, otherwise no quality-reduced retry occurs.
        if not _is_stream_forbidden(primary_error) or selection is None:
            failed += 1
            messages.append(f"失敗: {item.title} ({_error_text(primary_error)})")
            continue
        retried = False
        for client in ("web_embedded", "android_vr", "tv"):
            try:
                fallback_info = _fetch_video_info(item.url, client=client)
                if not _offers_exact_format(fallback_info, selection):
                    messages.append(
                        f"{item.title}: {client} は同一の最高画質形式を提供しないため使いません。"
                    )
                    continue
                retried = True
                _download_one(
                    item.url,
                    output_directory,
                    selector=selection.selector,
                    client=client,
                    restart=True,
                )
                succeeded += 1
                catalog_videos.append(
                    _catalog_video_for_download(
                        item, output_directory, existing_output_files, selection.selector
                    )
                )
                messages.append(f"完了: {item.title}（{client} で同一品質を再試行）")
                break
            except Exception as fallback_error:
                messages.append(
                    f"{item.title}: {client} で再試行できませんでした ({_error_text(fallback_error)})"
                )
        else:
            # This explicit final attempt is still exact-format only. A PO
            # Token must never become a reason to silently lower quality.
            provider_ready, provider_message = _po_token_provider_status()
            if allow_po_token_provider and provider_ready:
                try:
                    # mweb can expose only a low-quality list before a Player
                    # PO Token is present. Inspect it with the Provider first.
                    po_info = _fetch_video_info(
                        item.url,
                        client="mweb",
                        use_po_token_provider=True,
                    )
                    po_selection = _select_high_quality_format(po_info)
                    if po_selection is None or not _offers_no_lower_quality(
                        primary_info, selection, po_info, po_selection
                    ):
                        messages.append(
                            f"{item.title}: 一時PO Token経路は最高画質を維持できないため使いません。"
                        )
                    else:
                        _download_one(
                            item.url,
                            output_directory,
                            selector=po_selection.selector,
                            client="mweb",
                            restart=True,
                            use_po_token_provider=True,
                        )
                        succeeded += 1
                        catalog_videos.append(
                            _catalog_video_for_download(
                                item, output_directory, existing_output_files, po_selection.selector
                            )
                        )
                        messages.append(f"完了: {item.title}（一時PO Tokenで同一品質を再試行）")
                        continue
                except Exception as provider_error:
                    messages.append(
                        f"{item.title}: 一時PO Tokenで再試行できませんでした ({_error_text(provider_error)})"
                    )
            elif allow_po_token_provider:
                messages.append(f"{item.title}: 一時PO Token補助は使えません ({provider_message})")

            # WPC uses a real, disposable Chromium session.  It is intentionally
            # a second provider: use it only after the lower-overhead BgUtil
            # route did not produce a usable stream.
            wpc_ready, wpc_message = wpc_provider_status()
            if allow_wpc_provider and wpc_ready:
                try:
                    selector = _run_wpc_download_worker(
                        item.url,
                        output_directory,
                        primary_info,
                        selection,
                    )
                    succeeded += 1
                    catalog_videos.append(
                        _catalog_video_for_download(
                            item, output_directory, existing_output_files, selector
                        )
                    )
                    messages.append(
                        f"完了: {item.title}（一時ブラウザPO Tokenで同一品質を再試行: {selector}）"
                    )
                    continue
                except Exception as wpc_error:
                    messages.append(
                        f"{item.title}: 一時ブラウザPO Tokenで再試行できませんでした ({_error_text(wpc_error)})"
                    )
            elif allow_wpc_provider:
                messages.append(f"{item.title}: 一時ブラウザPO Token補助は使えません ({wpc_message})")
            failed += 1
            if not retried:
                messages.append(
                    f"失敗: {item.title}（403後、同一品質を提供するCookieなしの経路がありません）"
                )
            else:
                messages.append(f"失敗: {item.title}（同一品質の再試行も403でした）")
    return DownloadReport(
        succeeded=succeeded,
        failed=failed,
        messages=tuple(messages),
        catalog_videos=tuple(catalog_videos),
    )


def _output_file_snapshot(directory: Path) -> set[Path]:
    """Remember current completed files so cataloging can identify new output."""
    try:
        return {path for path in directory.iterdir() if path.is_file()}
    except OSError:
        return set()


def _catalog_video_for_download(
    item: VideoRecord,
    output_directory: Path,
    existing_files: set[Path],
    selector: str,
) -> CatalogVideo:
    """Build catalog data from a confirmed download without writing anything."""
    output_path = _find_new_downloaded_file(output_directory, item.video_id, existing_files)
    try:
        size_bytes = output_path.stat().st_size if output_path is not None else None
    except OSError:
        size_bytes = None
    return CatalogVideo(
        title=item.title,
        source_url=item.url,
        source_video_id=item.video_id,
        upload_date=item.upload_date,
        channel_name=item.channel_name,
        source_kind=item.source_kind,
        downloaded_at=catalog_timestamp(),
        output_path=output_path,
        size_bytes=size_bytes,
        format_selector=selector,
    )


def _find_new_downloaded_file(
    directory: Path, video_id: str, existing_files: set[Path]
) -> Path | None:
    """Return the newest completed output bearing the yt-dlp video ID.

    The output template contains ``[%(id)s]`` specifically so this lookup does
    not have to guess from a mutable title.  If yt-dlp reused an existing file
    or its final path cannot be determined, the catalog still records the
    source information but leaves the file path blank rather than guessing.
    """
    marker = f"[{video_id}]" if video_id else ""
    if not marker:
        return None
    try:
        candidates = [
            path
            for path in directory.iterdir()
            if path.is_file()
            and path not in existing_files
            and marker in path.name
            and path.suffix.lower() not in {".part", ".ytdl", ".temp"}
        ]
    except OSError:
        return None
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime_ns)


def _download_one(
    url: str,
    output_directory: Path,
    *,
    selector: str,
    client: str | None,
    restart: bool,
    use_po_token_provider: bool = False,
    use_wpc_provider: bool = False,
) -> None:
    """Download one exact format pair; retry paths restart rather than mix parts."""
    options: dict[str, Any] = {
        **_base_options(),
        **_youtube_client_options(client),
        "outtmpl": str(output_directory / "%(title).180B [%(id)s].%(ext)s"),
        "format": selector,
        "merge_output_format": "mp4",
        "noplaylist": True,
        "overwrites": False,
        "continuedl": not restart,
    }
    if use_po_token_provider and use_wpc_provider:
        raise ValueError("PO Token Providerは一度に1つだけ選択してください。")
    if use_po_token_provider:
        # Replace (rather than merge) the ordinary client setting with mweb.
        options["extractor_args"] = _po_token_options()["extractor_args"]
        with _EphemeralPoTokenEnvironment():
            with YoutubeDL(options) as ydl:
                ydl.download([url])
        return
    if use_wpc_provider:
        ready, message = wpc_provider_status()
        if not ready:
            raise RuntimeError(message)
        browser_path = _find_wpc_browser_path()
        assert browser_path is not None
        options["extractor_args"] = _wpc_options(browser_path)["extractor_args"]
        with _EphemeralWpcProvider(browser_path):
            with YoutubeDL(options) as ydl:
                ydl.download([url])
        return
    with YoutubeDL(options) as ydl:
        ydl.download([url])


class MediaDownloader:
    """Compatibility wrapper for the former small downloader API.

    New screens should use ``inspect_videos`` and ``download_videos`` so a
    user can always confirm metadata first. This wrapper remains for existing
    callers that intentionally invoke a direct download.
    """

    def __init__(self, output_dir: str | Path):
        self.output_dir = Path(output_dir).expanduser()

    def download_multiple(
        self, urls: list[str], status_callback: Callable[[str], None] | None = None
    ) -> tuple[int, int]:
        records = [
            VideoRecord(title=url, url=url, video_id="")
            for url in urls
            if is_youtube_url(url)
        ]
        report = download_videos(records, self.output_dir, progress=status_callback)
        if status_callback:
            for message in report.messages:
                status_callback(message)
        return report.succeeded, report.failed

    def download_single(self, url: str) -> bool:
        success, _failed = self.download_multiple([url])
        return success == 1
