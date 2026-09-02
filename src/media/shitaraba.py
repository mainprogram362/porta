"""Explicit, read-only parsing of public Shitaraba thread responses."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from html import unescape
from pathlib import Path
import re
from urllib.parse import urlparse
from urllib.request import Request, urlopen


_SHITARABA_HOSTS = frozenset({"jbbs.shitaraba.net", "jbbs.livedoor.jp"})
_BREAK_TAG = re.compile(r"<br\s*/?>", re.IGNORECASE)
_TAG = re.compile(r"<[^>]*>")


@dataclass(frozen=True)
class ShitarabaThreadReference:
    """The stable identifiers of one public Shitaraba thread."""

    category: str
    board_id: str
    thread_id: str

    @property
    def source_url(self) -> str:
        return (
            "https://jbbs.shitaraba.net/bbs/read.cgi/"
            f"{self.category}/{self.board_id}/{self.thread_id}/"
        )

    @property
    def raw_url(self) -> str:
        return (
            "https://jbbs.shitaraba.net/bbs/rawmode.cgi/"
            f"{self.category}/{self.board_id}/{self.thread_id}/"
        )


@dataclass(frozen=True)
class ShitarabaReply:
    """One response, reduced to readable response data only."""

    number: int
    name: str
    posted_at: str
    body: str
    poster_id: str


@dataclass(frozen=True)
class ShitarabaThread:
    """A successfully inspected thread kept only in memory."""

    reference: ShitarabaThreadReference
    title: str
    replies: tuple[ShitarabaReply, ...]


@dataclass(frozen=True)
class ShitarabaInspection:
    """The explicit URL, connection and response-format test result."""

    ok: bool
    message: str
    thread: ShitarabaThread | None = None
    input_url: str = ""


@dataclass(frozen=True)
class ShitarabaBatchInspection:
    """A bounded batch of explicitly checked thread URLs."""

    inspections: tuple[ShitarabaInspection, ...]

    @property
    def threads(self) -> tuple[ShitarabaThread, ...]:
        return tuple(
            inspection.thread
            for inspection in self.inspections
            if inspection.ok and inspection.thread is not None
        )

    @property
    def is_ready(self) -> bool:
        return bool(self.inspections) and all(inspection.ok for inspection in self.inspections)


@dataclass(frozen=True)
class ShitarabaSaveReport:
    """Paths created by one explicit response-only save action."""

    paths: tuple[Path, ...]


def parse_thread_url(value: str) -> ShitarabaThreadReference:
    """Accept only ordinary public thread URLs, never board-wide URLs."""
    parsed = urlparse(value.strip())
    host = (parsed.hostname or "").casefold()
    if parsed.scheme not in {"http", "https"} or host not in _SHITARABA_HOSTS:
        raise ValueError("したらばの公開スレッドURLを入力してください。")
    if parsed.username or parsed.password or parsed.port:
        raise ValueError("認証情報やポート番号を含むURLは受け付けません。")

    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 5 or parts[:2] != ["bbs", "read.cgi"]:
        raise ValueError("スレッドURL（…/bbs/read.cgi/カテゴリ/掲示板ID/スレッドID/）を入力してください。")
    category, board_id, thread_id = parts[2:5]
    if not all(part.isascii() and part.replace("_", "").replace("-", "").isalnum() for part in (category, board_id, thread_id)):
        raise ValueError("URL内のカテゴリ・掲示板ID・スレッドIDを確認してください。")
    return ShitarabaThreadReference(category, board_id, thread_id)


def _decode_raw_payload(payload: bytes | str) -> str:
    if isinstance(payload, str):
        return payload
    for encoding in ("utf-8-sig", "euc_jp", "cp932"):
        try:
            return payload.decode(encoding)
        except UnicodeDecodeError:
            continue
    return payload.decode("utf-8", errors="replace")


def _plain_text(value: str) -> str:
    text = _BREAK_TAG.sub("\n", value)
    text = _TAG.sub("", text)
    return unescape(text).replace("\r", "").strip()


def parse_raw_replies(payload: bytes | str, reference: ShitarabaThreadReference) -> ShitarabaThread:
    """Parse the documented rawmode response into response-only data."""
    title = ""
    replies: list[ShitarabaReply] = []
    for line in _decode_raw_payload(payload).splitlines():
        fields = line.split("<>", 6)
        if len(fields) < 7 or not fields[0].strip().isdigit():
            continue
        number, name, _email, posted_at, body, candidate_title, poster_id = fields
        if candidate_title.strip() and not title:
            title = _plain_text(candidate_title)
        replies.append(
            ShitarabaReply(
                number=int(number.strip()),
                name=_plain_text(name),
                posted_at=_plain_text(posted_at),
                body=_plain_text(body),
                poster_id=_plain_text(poster_id),
            )
        )
    if not replies:
        raise ValueError("レス形式として読み取れるデータがありません。URLまたは公開状態を確認してください。")
    return ShitarabaThread(reference=reference, title=title, replies=tuple(replies))


def _fetch_rawmode(url: str) -> bytes:
    request = Request(
        url,
        headers={
            "User-Agent": "Porta-Place/1.0 (personal response archive; no cookies)",
            "Accept": "text/plain, text/*;q=0.9, */*;q=0.1",
        },
    )
    with urlopen(request, timeout=15) as response:  # noqa: S310 - URL is validated above.
        return response.read()


def inspect_thread_url(
    value: str,
    *,
    fetcher: Callable[[str], bytes | str] | None = None,
) -> ShitarabaInspection:
    """Test one URL and response format, retaining no data outside the result."""
    try:
        reference = parse_thread_url(value)
        payload = (fetcher or _fetch_rawmode)(reference.raw_url)
        thread = parse_raw_replies(payload, reference)
    except (OSError, ValueError) as exc:
        return ShitarabaInspection(False, str(exc), input_url=value)
    except Exception as exc:  # Network libraries use several exception classes.
        return ShitarabaInspection(False, f"接続確認に失敗しました: {exc}", input_url=value)
    return ShitarabaInspection(
        True,
        f"URL・接続・レス形式を確認しました。{len(thread.replies)} 件のレスを抽出できます。",
        thread,
        value,
    )


def split_thread_urls(value: str, *, maximum: int = 100) -> tuple[str, ...]:
    """Return nonblank URL lines while limiting one explicit batch."""
    urls = tuple(dict.fromkeys(line.strip() for line in value.splitlines() if line.strip()))
    if not urls:
        raise ValueError("スレッドURLを1件以上入力してください。")
    if len(urls) > maximum:
        raise ValueError(f"URLは最大 {maximum} 件までです。")
    return urls


def inspect_thread_urls(
    value: str,
    *,
    fetcher: Callable[[str], bytes | str] | None = None,
) -> ShitarabaBatchInspection:
    """Check up to one hundred unique threads; trailing view selectors are ignored."""
    try:
        urls = split_thread_urls(value)
    except ValueError as exc:
        return ShitarabaBatchInspection((ShitarabaInspection(False, str(exc)),))

    inspections: list[ShitarabaInspection] = []
    seen_threads: set[str] = set()
    for url in urls:
        try:
            reference = parse_thread_url(url)
        except ValueError as exc:
            inspections.append(ShitarabaInspection(False, str(exc), input_url=url))
            continue
        if reference.source_url in seen_threads:
            continue
        seen_threads.add(reference.source_url)
        inspections.append(inspect_thread_url(url, fetcher=fetcher))
    return ShitarabaBatchInspection(tuple(inspections))


def render_replies(thread: ShitarabaThread) -> str:
    """Render only readable response data; no page chrome, ads or images."""
    blocks: list[str] = []
    for reply in thread.replies:
        metadata = " / ".join(
            value for value in (reply.name, reply.posted_at, reply.poster_id) if value
        )
        blocks.append(f"[{reply.number}] {metadata}\n{reply.body}".rstrip())
    return "\n\n".join(blocks)


def _safe_filename_stem(value: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", value).strip(" ._")
    cleaned = cleaned or "したらばスレッド"
    encoded = bytearray()
    for character in cleaned:
        character_bytes = character.encode("utf-8")
        if len(encoded) + len(character_bytes) > 180:
            break
        encoded.extend(character_bytes)
    return encoded.decode("utf-8", errors="ignore").rstrip(" ._") or "したらばスレッド"


def _next_available_path(directory: Path, stem: str) -> Path:
    candidate = directory / f"{stem}.txt"
    suffix = 1
    while candidate.exists():
        candidate = directory / f"{stem} ({suffix}).txt"
        suffix += 1
    return candidate


def save_replies(threads: tuple[ShitarabaThread, ...], directory: str | Path) -> ShitarabaSaveReport:
    """Write one response-only UTF-8 text file per explicitly checked thread."""
    destination = Path(directory).expanduser().resolve(strict=False)
    if not destination.is_dir():
        raise ValueError("存在する出力先フォルダを指定してください。")
    if not threads:
        raise ValueError("保存できる確認済みスレッドがありません。")

    paths: list[Path] = []
    for thread in threads:
        stem = _safe_filename_stem(
            f"{thread.title or '題名なし'}__{thread.reference.board_id}_{thread.reference.thread_id}"
        )
        path = _next_available_path(destination, stem)
        path.write_text(render_replies(thread) + "\n", encoding="utf-8")
        paths.append(path)
    return ShitarabaSaveReport(tuple(paths))
