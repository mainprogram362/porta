"""Read-only parsing and navigation indexes for saved Shitaraba response text."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import shlex


_REPLY_HEADER = re.compile(r"^\[(?P<number>\d+)\] (?P<metadata>[^\n]*)\n", re.MULTILINE)
_REPLY_REFERENCE = re.compile(r">>\s*(?P<first>\d+)(?:\s*-\s*(?P<last>\d+))?")
_SAVED_NAME_SUFFIX = re.compile(r"__(?P<board>[^_]+)_(?P<thread>[^_]+)(?: \(\d+\))?$")


@dataclass(frozen=True)
class TextThreadReply:
    """One reply reconstructed from the response-only text saved by this app."""

    number: int
    name: str
    posted_at: str
    poster_id: str
    body: str
    references: tuple[int, ...]


@dataclass(frozen=True)
class TextThreadDocument:
    """One read-only saved thread and its response index."""

    path: Path
    title: str
    replies: tuple[TextThreadReply, ...]

    @property
    def reply_numbers(self) -> frozenset[int]:
        return frozenset(reply.number for reply in self.replies)


@dataclass(frozen=True)
class TextThreadLoadResult:
    """Successful documents and per-file diagnostics from one explicit read."""

    documents: tuple[TextThreadDocument, ...]
    errors: tuple[str, ...]


@dataclass(frozen=True)
class TextSearchHit:
    """A response containing the current cross-thread search text."""

    document_index: int
    reply_number: int


@dataclass(frozen=True)
class TextSearchQuery:
    """Parsed lightweight query: OR groups of AND terms plus global exclusions."""

    groups: tuple[tuple[str, ...], ...]
    excluded: tuple[str, ...]

    @property
    def active(self) -> bool:
        return bool(self.groups or self.excluded)


@dataclass(frozen=True)
class TextThreadReplyTree:
    """One reply and nested replies which explicitly refer to it."""

    reply: TextThreadReply
    children: tuple["TextThreadReplyTree", ...]
    depth_limit_reached: bool = False


def _title_from_path(path: Path) -> str:
    stem = path.stem
    return _SAVED_NAME_SUFFIX.sub("", stem) or stem


def _references_in(body: str) -> tuple[int, ...]:
    numbers: list[int] = []
    for match in _REPLY_REFERENCE.finditer(body):
        first = int(match.group("first"))
        last = int(match.group("last") or first)
        if last < first or last - first > 500:
            numbers.append(first)
            continue
        numbers.extend(range(first, last + 1))
    return tuple(dict.fromkeys(numbers))


def parse_shitaraba_saved_text(text: str, path: Path) -> TextThreadDocument:
    """Parse the exact response-only text format emitted by ``save_replies``."""
    matches = tuple(_REPLY_HEADER.finditer(text.lstrip("\ufeff")))
    if not matches:
        raise ValueError("このアプリで保存した、したらばレス形式として読み取れません。")

    normalized = text.lstrip("\ufeff")
    replies: list[TextThreadReply] = []
    seen_numbers: set[int] = set()
    for index, match in enumerate(matches):
        number = int(match.group("number"))
        if number in seen_numbers:
            raise ValueError(f"レス番号 {number} が重複しています。")
        seen_numbers.add(number)
        metadata = match.group("metadata").split(" / ")
        body_end = matches[index + 1].start() if index + 1 < len(matches) else len(normalized)
        body = normalized[match.end() : body_end].strip("\n")
        replies.append(
            TextThreadReply(
                number=number,
                name=metadata[0] if metadata else "",
                posted_at=metadata[1] if len(metadata) > 1 else "",
                poster_id=" / ".join(metadata[2:]),
                body=body,
                references=_references_in(body),
            )
        )
    return TextThreadDocument(path=path, title=_title_from_path(path), replies=tuple(replies))


def load_shitaraba_saved_texts(paths: list[Path]) -> TextThreadLoadResult:
    """Read listed .txt files now; do not watch, modify or remember them."""
    documents: list[TextThreadDocument] = []
    errors: list[str] = []
    for path in paths:
        if path.suffix.casefold() != ".txt":
            errors.append(f"{path.name}: .txt ファイルだけを読み取れます。")
            continue
        try:
            text = path.read_text(encoding="utf-8")
            documents.append(parse_shitaraba_saved_text(text, path))
        except (OSError, UnicodeError, ValueError) as exc:
            errors.append(f"{path.name}: {exc}")
    return TextThreadLoadResult(tuple(documents), tuple(errors))


def replies_to(document: TextThreadDocument, target_number: int) -> tuple[TextThreadReply, ...]:
    """Return replies whose body references one existing response number."""
    return tuple(reply for reply in document.replies if target_number in reply.references)


def reply_descendant_tree(
    document: TextThreadDocument,
    target_number: int,
    *,
    max_depth: int = 10,
) -> tuple[TextThreadReplyTree, ...]:
    """Return nested replies below one response, bounded against malformed links.

    A saved response can reference more than one other response, including an
    accidental cycle.  Each rendered branch therefore excludes already visited
    response numbers.  ``max_depth`` counts levels *below* the selected root.
    """
    if max_depth < 1:
        raise ValueError("max_depth は 1 以上にしてください。")

    def build(parent_number: int, depth: int, ancestors: frozenset[int]) -> tuple[TextThreadReplyTree, ...]:
        children: list[TextThreadReplyTree] = []
        for child in replies_to(document, parent_number):
            if child.number in ancestors:
                continue
            direct_children = replies_to(document, child.number)
            reached_limit = depth >= max_depth and any(
                grandchild.number not in ancestors | {child.number}
                for grandchild in direct_children
            )
            descendants = (
                ()
                if depth >= max_depth
                else build(child.number, depth + 1, ancestors | {child.number})
            )
            children.append(TextThreadReplyTree(child, descendants, reached_limit))
        return tuple(children)

    return build(target_number, 1, frozenset({target_number}))


def parse_text_search_query(query: str) -> TextSearchQuery:
    """Parse a forgiving human query without executing regular expressions.

    Whitespace and ``AND`` join required terms, ``OR``/``|`` split alternative
    groups, and a leading ``-`` excludes a term from every group. Quoted text
    is treated as one term. An unmatched quote falls back to literal words so
    typing an incomplete query never interrupts the viewer.
    """
    source = query.strip()
    if not source:
        return TextSearchQuery((), ())
    try:
        tokens = shlex.split(source)
    except ValueError:
        tokens = source.split()

    groups: list[list[str]] = [[]]
    excluded: list[str] = []
    for token in tokens:
        if token in {"OR", "|", "｜"}:
            if groups[-1]:
                groups.append([])
            continue
        if token == "AND":
            continue
        if token.startswith(("-", "−")) and len(token) > 1:
            term = token[1:].casefold()
            if term and term not in excluded:
                excluded.append(term)
            continue
        term = token.casefold()
        if term and term not in groups[-1]:
            groups[-1].append(term)
    positive_groups = tuple(tuple(group) for group in groups if group)
    return TextSearchQuery(positive_groups, tuple(excluded))


def find_text_hits(documents: tuple[TextThreadDocument, ...], query: str) -> tuple[TextSearchHit, ...]:
    """Find AND/OR/exclusion matches in document order for seamless navigation."""
    parsed = parse_text_search_query(query)
    if not parsed.active:
        return ()
    hits: list[TextSearchHit] = []
    for document_index, document in enumerate(documents):
        for reply in document.replies:
            searchable = "\n".join(
                (str(reply.number), reply.name, reply.posted_at, reply.poster_id, reply.body)
            ).casefold()
            positive = not parsed.groups or any(
                all(term in searchable for term in group) for group in parsed.groups
            )
            negative = any(term in searchable for term in parsed.excluded)
            if positive and not negative:
                hits.append(TextSearchHit(document_index, reply.number))
    return tuple(hits)
