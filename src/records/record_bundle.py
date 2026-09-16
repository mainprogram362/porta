"""Transient, ordered records built from plain text.

The model deliberately stores every value as text.  It neither guesses data
types nor persists anything; user-facing apps decide when to import or export
a snapshot.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from io import StringIO
import json
import re
from typing import Literal


@dataclass(frozen=True)
class RecordBundleRow:
    """One atomic row whose values must never drift out of alignment."""

    identifier: str
    values: tuple[str, ...]


@dataclass(frozen=True)
class RecordBundle:
    """An ordered, named group of text records."""

    title: str
    field_names: tuple[str, ...]
    rows: tuple[RecordBundleRow, ...]

    def __post_init__(self) -> None:
        _validate_field_names(self.field_names)
        expected = len(self.field_names)
        identifiers: set[str] = set()
        for row in self.rows:
            if not row.identifier or row.identifier in identifiers:
                raise ValueError("対応表の行識別子が空または重複しています。")
            if len(row.values) != expected:
                raise ValueError("対応表の行と列の数が一致していません。")
            identifiers.add(row.identifier)


@dataclass(frozen=True)
class RecordLayoutSuggestion:
    """A conservative starting point which the user can edit before parsing."""

    field_count: int
    field_names: tuple[str, ...]
    use_first_line_as_title: bool


RecordSplitMode = Literal[
    "fixed_lines",
    "blank_blocks",
    "start_text",
    "contains_text",
    "start_regex",
]
FieldExtractionMethod = Literal["line", "split", "slice", "regex", "whole"]


@dataclass(frozen=True)
class RecordCandidate:
    """One source block whose identity survives all later field extraction."""

    identifier: str
    lines: tuple[str, ...]
    start_line: int
    end_line: int

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


@dataclass(frozen=True)
class RecordSplitResult:
    title: str
    candidates: tuple[RecordCandidate, ...]
    issues: tuple[str, ...] = ()


@dataclass(frozen=True)
class FieldExtractionRule:
    """One repeatable extraction applied inside every candidate record."""

    field_name: str
    method: FieldExtractionMethod = "line"
    line_number: int = 1
    argument: str = ""
    value_index: int = 1
    start_position: int = 0
    end_position: int = 0
    case_sensitive: bool = True


@dataclass(frozen=True)
class CandidateFieldMatch:
    """One extracted value and its source range inside a candidate's text."""

    field_name: str
    value: str
    start_offset: int | None
    end_offset: int | None

    @property
    def found(self) -> bool:
        return self.start_offset is not None and self.end_offset is not None


class TransientRecordBundleStore:
    """One process-local handoff slot with no filesystem behavior."""

    def __init__(self) -> None:
        self._bundle: RecordBundle | None = None
        self._revision = 0

    @property
    def bundle(self) -> RecordBundle | None:
        return self._bundle

    @property
    def revision(self) -> int:
        return self._revision

    def replace(self, bundle: RecordBundle) -> None:
        self._bundle = bundle
        self._revision += 1

    def clear(self) -> None:
        if self._bundle is not None:
            self._bundle = None
            self._revision += 1


def _validate_field_names(field_names: tuple[str, ...]) -> None:
    if len(field_names) < 2:
        raise ValueError("対応表には2列以上の項目名が必要です。")
    cleaned = tuple(name.strip() for name in field_names)
    if any(not name for name in cleaned):
        raise ValueError("項目名をすべて入力してください。")
    if len({name.casefold() for name in cleaned}) != len(cleaned):
        raise ValueError("同じ項目名を複数の列には使用できません。")


def parse_field_names(value: str, field_count: int) -> tuple[str, ...]:
    """Parse comma/tab-separated labels and require the configured count."""
    names = tuple(part.strip() for part in re.split(r"[,，\t]", value))
    if len(names) != field_count:
        raise ValueError(f"項目名をカンマ区切りで{field_count}件入力してください。")
    _validate_field_names(names)
    return names


def parse_record_bundle(
    text: str,
    field_names: tuple[str, ...],
    *,
    use_first_line_as_title: bool = True,
    ignore_blank_lines: bool = True,
    strip_values: bool = True,
) -> RecordBundle:
    """Split ordered lines into fixed-width atomic rows without dropping a tail."""
    _validate_field_names(field_names)
    lines = text.splitlines()
    if strip_values:
        lines = [line.strip() for line in lines]
    if ignore_blank_lines:
        lines = [line for line in lines if line]
    title = lines.pop(0) if use_first_line_as_title and lines else ""
    width = len(field_names)
    rows: list[RecordBundleRow] = []
    for offset in range(0, len(lines), width):
        values = lines[offset : offset + width]
        values.extend("" for _ in range(width - len(values)))
        rows.append(
            RecordBundleRow(
                identifier=f"row-{len(rows) + 1:06d}",
                values=tuple(values),
            )
        )
    return RecordBundle(title=title, field_names=field_names, rows=tuple(rows))


def suggest_record_layout(text: str) -> RecordLayoutSuggestion:
    """Suggest common date/title/size groups while keeping the setup editable."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    use_title = bool(
        len(lines) >= 2
        and not re.fullmatch(r"\d{8}", lines[0])
        and re.fullmatch(r"\d{8}", lines[1])
    )
    values = lines[1:] if use_title else lines
    date_indexes = [index for index, value in enumerate(values) if re.fullmatch(r"\d{8}", value)]
    distances = [second - first for first, second in zip(date_indexes, date_indexes[1:])]
    field_count = distances[0] if distances and all(value == distances[0] for value in distances) else 2
    if not 2 <= field_count <= 10:
        field_count = 2

    names = [f"項目{index}" for index in range(1, field_count + 1)]
    columns = [values[index::field_count] for index in range(field_count)]
    date_column: int | None = None
    for index, column in enumerate(columns):
        nonblank = [value for value in column if value]
        if nonblank and all(re.fullmatch(r"\d{8}", value) for value in nonblank):
            names[index] = "日付"
            date_column = index
        elif nonblank and all(
            re.fullmatch(r"\d+(?:\.\d+)?\s*(?:b|kb|mb|gb|tb)", value, re.IGNORECASE)
            for value in nonblank
        ):
            names[index] = "サイズ"
    if date_column is not None and field_count >= 2:
        following = (date_column + 1) % field_count
        if names[following].startswith("項目"):
            names[following] = "タイトル"
    return RecordLayoutSuggestion(field_count, tuple(names), use_title)


def split_record_candidates(
    text: str,
    *,
    mode: RecordSplitMode = "fixed_lines",
    lines_per_record: int = 2,
    boundary: str = "",
    use_first_line_as_title: bool = True,
    ignore_blank_lines: bool = True,
    strip_values: bool = True,
    case_sensitive: bool = True,
) -> RecordSplitResult:
    """Find record boundaries before extracting any fields from those records."""
    if mode not in {
        "fixed_lines",
        "blank_blocks",
        "start_text",
        "contains_text",
        "start_regex",
    }:
        raise ValueError(f"未対応のレコード区切り方法です: {mode}")
    if lines_per_record < 1:
        raise ValueError("1レコードの行数は1以上にしてください。")

    source_lines = [
        (number, line.strip() if strip_values else line)
        for number, line in enumerate(text.splitlines(), start=1)
    ]
    title = ""
    if use_first_line_as_title:
        title_index = next(
            (index for index, (_number, value) in enumerate(source_lines) if value.strip()),
            None,
        )
        if title_index is not None:
            _number, title = source_lines.pop(title_index)

    if mode == "blank_blocks":
        return _split_at_blank_lines(source_lines, title)

    usable_lines = (
        [(number, value) for number, value in source_lines if value.strip()]
        if ignore_blank_lines
        else source_lines
    )
    if mode == "fixed_lines":
        candidates = _candidate_chunks(usable_lines, lines_per_record)
        issues = ()
        if candidates and len(candidates[-1].lines) != lines_per_record:
            issues = (
                f"最後の候補は{len(candidates[-1].lines)}行で、指定した{lines_per_record}行に足りません。",
            )
        if not candidates:
            issues = ("レコード候補になるテキストがありません。",)
        return RecordSplitResult(title, candidates, issues)

    if not boundary:
        label = "正規表現" if mode == "start_regex" else "区切り文字列"
        raise ValueError(f"レコード先頭を判定する{label}を入力してください。")
    if mode == "start_regex":
        flags = re.MULTILINE | (0 if case_sensitive else re.IGNORECASE)
        try:
            expression = re.compile(boundary, flags)
        except re.error as exc:
            raise ValueError(f"レコード開始の正規表現が正しくありません: {exc}") from exc

        def begins_record(value: str) -> bool:
            return expression.search(value) is not None

    else:
        expected = boundary if case_sensitive else boundary.casefold()

        def begins_record(value: str) -> bool:
            compared = value if case_sensitive else value.casefold()
            if mode == "contains_text":
                return expected in compared
            return compared.startswith(expected)

    groups: list[list[tuple[int, str]]] = []
    current: list[tuple[int, str]] = []
    leading_unmatched = False
    found_boundary = False
    for numbered_line in usable_lines:
        _number, value = numbered_line
        if begins_record(value):
            found_boundary = True
            if current:
                groups.append(current)
                current = []
        elif not found_boundary and value.strip():
            leading_unmatched = True
        current.append(numbered_line)
    if current:
        groups.append(current)
    candidates = _groups_to_candidates(groups)
    issues: list[str] = []
    if not found_boundary:
        issues.append("開始条件に一致する行がありません。全体を1候補として残しました。")
    elif leading_unmatched:
        issues.append("最初の開始条件より前の行を、先頭の候補として残しました。")
    if not candidates:
        issues.append("レコード候補になるテキストがありません。")
    return RecordSplitResult(title, candidates, tuple(issues))


def _split_at_blank_lines(
    source_lines: list[tuple[int, str]], title: str
) -> RecordSplitResult:
    groups: list[list[tuple[int, str]]] = []
    current: list[tuple[int, str]] = []
    for numbered_line in source_lines:
        _number, value = numbered_line
        if not value.strip():
            if current:
                groups.append(current)
                current = []
            continue
        current.append(numbered_line)
    if current:
        groups.append(current)
    candidates = _groups_to_candidates(groups)
    issues = () if candidates else ("空行で区切れるレコード候補がありません。",)
    return RecordSplitResult(title, candidates, issues)


def _candidate_chunks(
    lines: list[tuple[int, str]], width: int
) -> tuple[RecordCandidate, ...]:
    groups = [lines[offset : offset + width] for offset in range(0, len(lines), width)]
    return _groups_to_candidates(groups)


def _groups_to_candidates(
    groups: list[list[tuple[int, str]]],
) -> tuple[RecordCandidate, ...]:
    return tuple(
        RecordCandidate(
            identifier=f"candidate-{index:06d}",
            lines=tuple(value for _number, value in group),
            start_line=group[0][0],
            end_line=group[-1][0],
        )
        for index, group in enumerate(groups, start=1)
        if group
    )


def extract_candidate_fields(
    split_result: RecordSplitResult,
    rules: tuple[FieldExtractionRule, ...],
) -> RecordBundle:
    """Apply each field rule within each stable candidate, never across candidates."""
    matches = extract_candidate_field_matches(split_result, rules)
    field_names = tuple(rule.field_name.strip() for rule in rules)
    rows = tuple(
        RecordBundleRow(
            candidate.identifier,
            tuple(match.value for match in candidate_matches),
        )
        for candidate, candidate_matches in zip(split_result.candidates, matches)
    )
    return RecordBundle(split_result.title, field_names, rows)


def extract_candidate_field_matches(
    split_result: RecordSplitResult,
    rules: tuple[FieldExtractionRule, ...],
) -> tuple[tuple[CandidateFieldMatch, ...], ...]:
    """Extract values together with source offsets for a visual confirmation UI."""
    field_names = tuple(rule.field_name.strip() for rule in rules)
    _validate_field_names(field_names)
    prepared_regex: dict[str, re.Pattern[str]] = {}
    for rule in rules:
        _validate_extraction_rule(rule)
        if rule.method == "regex":
            flags = re.MULTILINE | re.DOTALL
            if not rule.case_sensitive:
                flags |= re.IGNORECASE
            try:
                expression = re.compile(rule.argument, flags)
            except re.error as exc:
                raise ValueError(f"「{rule.field_name}」の正規表現が正しくありません: {exc}") from exc
            if rule.value_index > expression.groups:
                raise ValueError(
                    f"「{rule.field_name}」の正規表現にはグループ{rule.value_index}がありません。"
                )
            prepared_regex[rule.field_name] = expression

    return tuple(
        tuple(
            _extract_candidate_match(candidate, rule, prepared_regex)
            for rule in rules
        )
        for candidate in split_result.candidates
    )


def _validate_extraction_rule(rule: FieldExtractionRule) -> None:
    if rule.method not in {"line", "split", "slice", "regex", "whole"}:
        raise ValueError(f"未対応の項目抽出方法です: {rule.method}")
    if rule.method in {"line", "split", "slice"} and rule.line_number < 1:
        raise ValueError(f"「{rule.field_name}」の行番号は1以上にしてください。")
    if rule.method == "regex" and not rule.argument:
        raise ValueError(f"「{rule.field_name}」の正規表現を入力してください。")
    if rule.method in {"split", "regex"} and rule.value_index < 1:
        raise ValueError(f"「{rule.field_name}」の取得番号は1以上にしてください。")
    if rule.method == "slice" and (
        rule.start_position < 0 or rule.end_position < 0
    ):
        raise ValueError(f"「{rule.field_name}」の文字位置は0以上にしてください。")
    if rule.method == "slice" and rule.end_position and rule.end_position < rule.start_position:
        raise ValueError(f"「{rule.field_name}」の終了位置を開始位置以上にしてください。")


def _extract_candidate_match(
    candidate: RecordCandidate,
    rule: FieldExtractionRule,
    prepared_regex: dict[str, re.Pattern[str]],
) -> CandidateFieldMatch:
    if rule.method == "whole":
        return CandidateFieldMatch(rule.field_name, candidate.text, 0, len(candidate.text))
    if rule.method == "regex":
        match = prepared_regex[rule.field_name].search(candidate.text)
        if match is None or match.start(rule.value_index) < 0:
            return CandidateFieldMatch(rule.field_name, "", None, None)
        return CandidateFieldMatch(
            rule.field_name,
            match.group(rule.value_index) or "",
            match.start(rule.value_index),
            match.end(rule.value_index),
        )
    if rule.line_number > len(candidate.lines):
        return CandidateFieldMatch(rule.field_name, "", None, None)
    line_offset = sum(len(line) + 1 for line in candidate.lines[: rule.line_number - 1])
    line = candidate.lines[rule.line_number - 1]
    if rule.method == "line":
        return CandidateFieldMatch(
            rule.field_name,
            line,
            line_offset,
            line_offset + len(line),
        )
    if rule.method == "slice":
        end = rule.end_position or len(line)
        start = min(rule.start_position, len(line))
        end = min(max(end, start), len(line))
        value = line[start:end]
        if not value:
            return CandidateFieldMatch(rule.field_name, "", None, None)
        return CandidateFieldMatch(
            rule.field_name,
            value,
            line_offset + start,
            line_offset + end,
        )
    split_match = _split_value_match(line, rule.argument, rule.value_index)
    if split_match is None:
        return CandidateFieldMatch(rule.field_name, "", None, None)
    value, start, end = split_match
    return CandidateFieldMatch(
        rule.field_name,
        value,
        line_offset + start,
        line_offset + end,
    )


def _split_value_match(
    line: str, separator: str, value_index: int
) -> tuple[str, int, int] | None:
    if not separator:
        matches = tuple(re.finditer(r"\S+", line))
        if value_index > len(matches):
            return None
        match = matches[value_index - 1]
        return match.group(0), match.start(), match.end()

    values: list[tuple[str, int, int]] = []
    start = 0
    while True:
        boundary = line.find(separator, start)
        if boundary < 0:
            values.append((line[start:], start, len(line)))
            break
        values.append((line[start:boundary], start, boundary))
        start = boundary + len(separator)
    if value_index > len(values):
        return None
    value, start, end = values[value_index - 1]
    return (value, start, end) if value else None


def incomplete_row_numbers(bundle: RecordBundle) -> tuple[int, ...]:
    """Return one-based rows containing at least one blank cell."""
    return tuple(
        index
        for index, row in enumerate(bundle.rows, start=1)
        if any(not value.strip() for value in row.values)
    )


def render_bundle_tsv(bundle: RecordBundle, *, include_headers: bool = True) -> str:
    """Render a spreadsheet-friendly tab-separated snapshot."""
    output = StringIO(newline="")
    writer = csv.writer(output, delimiter="\t", lineterminator="\n")
    if include_headers:
        writer.writerow(bundle.field_names)
    writer.writerows(row.values for row in bundle.rows)
    return output.getvalue()


def render_bundle_json(bundle: RecordBundle) -> str:
    """Render title, columns, stable row identifiers and values as JSON."""
    payload = {
        "title": bundle.title,
        "fields": list(bundle.field_names),
        "records": [
            {
                "id": row.identifier,
                "values": dict(zip(bundle.field_names, row.values)),
            }
            for row in bundle.rows
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def render_bundle_column(bundle: RecordBundle, field_name: str) -> str:
    """Render one selected field in row order."""
    try:
        index = bundle.field_names.index(field_name)
    except ValueError as exc:
        raise ValueError(f"対応表に「{field_name}」列がありません。") from exc
    return "\n".join(row.values[index] for row in bundle.rows)


def render_bundle_template(bundle: RecordBundle, template: str) -> str:
    """Render one line per row using field names such as ``{日付}_{タイトル}``."""
    if not template:
        raise ValueError("出力テンプレートを入力してください。")
    lines: list[str] = []
    for row_number, row in enumerate(bundle.rows, start=1):
        values = dict(zip(bundle.field_names, row.values))
        values["行番号"] = str(row_number)
        try:
            lines.append(template.format_map(values))
        except KeyError as exc:
            raise ValueError(f"テンプレートの項目名が対応表にありません: {exc.args[0]}") from exc
        except ValueError as exc:
            raise ValueError(f"テンプレートの書式が正しくありません: {exc}") from exc
    return "\n".join(lines)
