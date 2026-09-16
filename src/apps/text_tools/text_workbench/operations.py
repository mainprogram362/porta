"""Pure text transformations used by the text workbench screen."""

from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
import re
import unicodedata


class TextOperationError(ValueError):
    """An operation could not be applied without risking an unintended result."""


@dataclass(frozen=True)
class OperationResult:
    """Text produced by one operation and a compact account of its effect."""

    text: str
    affected_count: int
    summary: str


_UNIFIED_MARKS = str.maketrans(
    {
        "‐": "-",
        "‑": "-",
        "‒": "-",
        "–": "-",
        "—": "-",
        "―": "-",
        "−": "-",
        "“": '"',
        "”": '"',
        "„": '"',
        "‟": '"',
        "‘": "'",
        "’": "'",
        "‚": "'",
        "‛": "'",
    }
)


def _clean_value(value: str) -> str:
    return " ".join(value.split())


def _unique_values(values: list[str], *, case_sensitive: bool) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = _clean_value(value)
        if not cleaned:
            continue
        key = cleaned if case_sensitive else cleaned.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(cleaned)
    return result


def parse_custom_normalization_rules(value: str) -> tuple[tuple[str, str], ...]:
    """Parse one literal ``before => after`` replacement per nonblank line."""
    rules: list[tuple[str, str]] = []
    for line_number, raw_line in enumerate(value.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        separator = "=>" if "=>" in line else "→" if "→" in line else ""
        if not separator:
            raise TextOperationError(
                f"独自統一ルールの{line_number}行目を「置換前 => 置換後」で入力してください。"
            )
        before, after = (part.strip() for part in line.split(separator, 1))
        if not before:
            raise TextOperationError(
                f"独自統一ルールの{line_number}行目は、置換前の文字列が空です。"
            )
        rules.append((before, after))
    return tuple(rules)


def apply_strong_normalization(
    text: str,
    *,
    normalize_width: bool = True,
    lowercase: bool = True,
    normalize_spaces: bool = True,
    normalize_marks: bool = True,
    custom_rules_text: str = "",
) -> OperationResult:
    """Apply explicitly selected broad normalization and ordered literal rules."""
    custom_rules = parse_custom_normalization_rules(custom_rules_text)
    applied: list[str] = []
    if normalize_width:
        applied.append("全角・互換文字")
    if lowercase:
        applied.append("英字小文字")
    if normalize_spaces:
        applied.append("空白")
    if normalize_marks:
        applied.append("ダッシュ・引用符")

    def apply_builtins(value: str) -> str:
        if normalize_width:
            value = unicodedata.normalize("NFKC", value)
        if lowercase:
            value = value.casefold()
        if normalize_spaces:
            value = "\n".join(
                re.sub(r"[^\S\r\n]+", " ", line).strip()
                for line in value.splitlines()
            )
        if normalize_marks:
            value = value.translate(_UNIFIED_MARKS)
        return value

    output = apply_builtins(text)
    for before, after in custom_rules:
        # Normalize the matching side too, so users may copy its spelling
        # directly from the unnormalized source.  Preserve the replacement
        # exactly because it expresses the spelling the user wants to keep.
        normalized_before = apply_builtins(before)
        matched_before = normalized_before if normalized_before in output else before
        output = output.replace(matched_before, after)
    changed = sum(before != after for before, after in zip(text, output))
    changed += abs(len(text) - len(output))
    summary_parts = "・".join(applied) if applied else "組み込み規則なし"
    summary = f"強い正規化（{summary_parts}、独自ルール{len(custom_rules)}件）"
    return OperationResult(output, changed, summary)


class _TitleParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.values: list[str] = []
        self._capture_tag: str | None = None
        self._capture_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.casefold()
        if tag in {"title", "h1"} and self._capture_tag is None:
            self._capture_tag = tag
            self._capture_parts = []
            return
        if tag != "meta":
            return
        attributes = {name.casefold(): value or "" for name, value in attrs}
        key = (attributes.get("property") or attributes.get("name") or "").casefold()
        if key in {"og:title", "twitter:title"} and attributes.get("content"):
            self.values.append(attributes["content"])

    def handle_data(self, data: str) -> None:
        if self._capture_tag is not None:
            self._capture_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if self._capture_tag == tag.casefold():
            self.values.append("".join(self._capture_parts))
            self._capture_tag = None
            self._capture_parts = []


class _VisibleTextParser(HTMLParser):
    _BLOCK_TAGS = {
        "address",
        "article",
        "aside",
        "blockquote",
        "br",
        "dd",
        "div",
        "dl",
        "dt",
        "figcaption",
        "footer",
        "form",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "header",
        "hr",
        "li",
        "main",
        "nav",
        "ol",
        "p",
        "pre",
        "section",
        "table",
        "td",
        "th",
        "tr",
        "ul",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._ignored_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        tag = tag.casefold()
        if tag in {"script", "style", "noscript", "template"}:
            self._ignored_depth += 1
        elif not self._ignored_depth and tag in self._BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.casefold()
        if tag in {"script", "style", "noscript", "template"} and self._ignored_depth:
            self._ignored_depth -= 1
        elif not self._ignored_depth and tag in self._BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._ignored_depth:
            self.parts.append(data)


def _flags(case_sensitive: bool, *, dot_all: bool = False) -> re.RegexFlag:
    flags = re.MULTILINE
    if not case_sensitive:
        flags |= re.IGNORECASE
    if dot_all:
        flags |= re.DOTALL
    return flags


def _compile(pattern: str, case_sensitive: bool, *, dot_all: bool = False) -> re.Pattern[str]:
    if not pattern:
        raise TextOperationError("検索・抽出する正規表現を入力してください。")
    try:
        return re.compile(pattern, _flags(case_sensitive, dot_all=dot_all))
    except re.error as exc:
        raise TextOperationError(f"正規表現が正しくありません: {exc}") from exc


def _find_literal(text: str, marker: str, case_sensitive: bool, start: int = 0) -> int:
    if not marker:
        raise TextOperationError("区切り文字を入力してください。")
    if case_sensitive:
        return text.find(marker, start)
    match = re.search(re.escape(marker), text[start:], re.IGNORECASE)
    return -1 if match is None else start + match.start()


def _extract_html_titles(text: str, case_sensitive: bool) -> OperationResult:
    parser = _TitleParser()
    parser.feed(text)
    parser.close()
    values = _unique_values(parser.values, case_sensitive=case_sensitive)
    return OperationResult("\n".join(values), len(values), f"HTMLタイトルを{len(values)}件抽出")


def _html_to_text(text: str) -> OperationResult:
    parser = _VisibleTextParser()
    parser.feed(text)
    parser.close()
    lines = [_clean_value(line) for line in "".join(parser.parts).splitlines()]
    visible_lines = [line for line in lines if line]
    return OperationResult("\n".join(visible_lines), len(visible_lines), "HTMLタグを除去して本文を抽出")


def _regex_extract(text: str, pattern: str, case_sensitive: bool) -> OperationResult:
    expression = _compile(pattern, case_sensitive, dot_all=True)
    values: list[str] = []
    for match in expression.finditer(text):
        if not match.groups():
            values.append(match.group(0))
        elif len(match.groups()) == 1:
            values.append(match.group(1) or "")
        else:
            values.append("\t".join(value or "" for value in match.groups()))
    return OperationResult("\n".join(values), len(values), f"正規表現に一致した値を{len(values)}件抽出")


def _extract_urls(text: str, case_sensitive: bool) -> OperationResult:
    expression = re.compile(r"https?://[^\s<>\"']+", _flags(case_sensitive))
    trailing = ".,;:!?)]}。、，；：！？）」』】"
    values = [match.group(0).rstrip(trailing) for match in expression.finditer(text)]
    values = _unique_values(values, case_sensitive=case_sensitive)
    return OperationResult("\n".join(values), len(values), f"URLを{len(values)}件抽出")


def _extract_between(text: str, start_marker: str, end_marker: str, case_sensitive: bool) -> OperationResult:
    if not start_marker or not end_marker:
        raise TextOperationError("開始文字と終了文字を両方入力してください。")
    values: list[str] = []
    cursor = 0
    while True:
        start = _find_literal(text, start_marker, case_sensitive, cursor)
        if start < 0:
            break
        value_start = start + len(start_marker)
        end = _find_literal(text, end_marker, case_sensitive, value_start)
        if end < 0:
            break
        values.append(text[value_start:end])
        cursor = end + len(end_marker)
    return OperationResult("\n".join(values), len(values), f"区切り内の値を{len(values)}件抽出")


def _line_filter(
    text: str,
    needle: str,
    case_sensitive: bool,
    *,
    keep: bool,
    regex: bool,
) -> OperationResult:
    if regex:
        expression = _compile(needle, case_sensitive)

        def matches(line: str) -> bool:
            return expression.search(line) is not None

    else:
        if not needle:
            raise TextOperationError("行を判定する文字列を入力してください。")
        expected = needle if case_sensitive else needle.casefold()

        def matches(line: str) -> bool:
            compared = line if case_sensitive else line.casefold()
            return expected in compared

    lines = text.splitlines()
    result = [line for line in lines if matches(line) is keep]
    affected = len(lines) - len(result) if not keep else len(result)
    action = "残した" if keep else "除外した"
    return OperationResult("\n".join(result), affected, f"条件により{action}行は{affected}行")


def _literal_replace(text: str, old: str, new: str, case_sensitive: bool) -> OperationResult:
    if not old:
        raise TextOperationError("置換前の文字列を入力してください。")
    if case_sensitive:
        count = text.count(old)
        output = text.replace(old, new)
    else:
        output, count = re.subn(re.escape(old), lambda _match: new, text, flags=re.IGNORECASE)
    return OperationResult(output, count, f"文字列を{count}か所置換")


def _regex_replace(text: str, pattern: str, replacement: str, case_sensitive: bool) -> OperationResult:
    expression = _compile(pattern, case_sensitive, dot_all=True)
    try:
        output, count = expression.subn(replacement, text)
    except re.error as exc:
        raise TextOperationError(f"置換文字列が正しくありません: {exc}") from exc
    return OperationResult(output, count, f"正規表現で{count}か所置換")


def _keep_at_marker(text: str, marker: str, case_sensitive: bool, *, after: bool) -> OperationResult:
    position = _find_literal(text, marker, case_sensitive)
    if position < 0:
        raise TextOperationError(
            "指定した区切り文字が見つかりません。作業欄は変更していません。"
        )
    if after:
        output = text[position + len(marker) :]
        summary = "最初の区切り文字より後を保持"
    else:
        output = text[:position]
        summary = "最初の区切り文字より前を保持"
    return OperationResult(output, 1, summary)


def apply_text_operation(
    text: str,
    operation: str,
    parameter: str = "",
    replacement: str = "",
    *,
    case_sensitive: bool = True,
) -> OperationResult:
    """Apply one named operation to text without reading or writing files."""
    if operation == "html_titles":
        return _extract_html_titles(text, case_sensitive)
    if operation == "html_to_text":
        return _html_to_text(text)
    if operation == "regex_extract":
        return _regex_extract(text, parameter, case_sensitive)
    if operation == "extract_urls":
        return _extract_urls(text, case_sensitive)
    if operation == "extract_between":
        return _extract_between(text, parameter, replacement, case_sensitive)
    if operation == "keep_lines_text":
        return _line_filter(text, parameter, case_sensitive, keep=True, regex=False)
    if operation == "remove_lines_text":
        return _line_filter(text, parameter, case_sensitive, keep=False, regex=False)
    if operation == "keep_lines_regex":
        return _line_filter(text, parameter, case_sensitive, keep=True, regex=True)
    if operation == "remove_lines_regex":
        return _line_filter(text, parameter, case_sensitive, keep=False, regex=True)
    if operation == "keep_before":
        return _keep_at_marker(text, parameter, case_sensitive, after=False)
    if operation == "keep_after":
        return _keep_at_marker(text, parameter, case_sensitive, after=True)
    if operation == "replace_text":
        return _literal_replace(text, parameter, replacement, case_sensitive)
    if operation == "replace_regex":
        return _regex_replace(text, parameter, replacement, case_sensitive)

    lines = text.splitlines()
    if operation == "trim_lines":
        output_lines = [line.strip() for line in lines]
        count = sum(before != after for before, after in zip(lines, output_lines))
        return OperationResult("\n".join(output_lines), count, f"{count}行の前後空白を除去")
    if operation == "remove_blank_lines":
        output_lines = [line for line in lines if line.strip()]
        count = len(lines) - len(output_lines)
        return OperationResult("\n".join(output_lines), count, f"空行を{count}行除去")
    if operation == "unique_lines":
        output_lines: list[str] = []
        seen: set[str] = set()
        for line in lines:
            key = line if case_sensitive else line.casefold()
            if key in seen:
                continue
            seen.add(key)
            output_lines.append(line)
        count = len(lines) - len(output_lines)
        return OperationResult("\n".join(output_lines), count, f"重複を{count}行除去")
    if operation == "sort_lines":
        key = None if case_sensitive else str.casefold
        output_lines = sorted(lines, key=key)
        return OperationResult("\n".join(output_lines), len(lines), f"{len(lines)}行を昇順に並べ替え")
    if operation == "normalize_spaces":
        output_lines = [_clean_value(line) for line in lines]
        count = sum(before != after for before, after in zip(lines, output_lines))
        return OperationResult("\n".join(output_lines), count, f"{count}行の連続空白を整理")
    if operation == "normalize_nfkc":
        output = unicodedata.normalize("NFKC", text)
        count = sum(before != after for before, after in zip(text, output))
        count += abs(len(text) - len(output))
        return OperationResult(output, count, f"全角英数字などを正規化（変更{count}文字）")
    raise TextOperationError(f"未対応のテキスト操作です: {operation}")
