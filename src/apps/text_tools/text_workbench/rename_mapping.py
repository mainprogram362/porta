"""Match transient record rows to explicitly selected local paths."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from records.record_bundle import RecordBundle

MatchMode = Literal["exact", "contains", "order"]


@dataclass(frozen=True)
class RecordPathMatch:
    source: Path
    row_identifier: str
    key: str
    output_name: str


@dataclass(frozen=True)
class RecordPathMatchResult:
    matches: tuple[RecordPathMatch, ...]
    issues: tuple[str, ...]

    @property
    def is_ready(self) -> bool:
        return bool(self.matches) and not self.issues


def match_record_paths(
    bundle: RecordBundle,
    paths: tuple[Path, ...],
    *,
    key_field: str,
    output_field: str,
    mode: MatchMode = "exact",
    case_sensitive: bool = True,
) -> RecordPathMatchResult:
    """Build a one-to-one mapping without renaming or touching any path."""
    if mode not in {"exact", "contains", "order"}:
        return RecordPathMatchResult((), (f"未対応の照合方法です: {mode}",))
    if not paths:
        return RecordPathMatchResult(
            (), ("変更するファイルまたはフォルダを追加してください。",)
        )
    if len(set(paths)) != len(paths):
        return RecordPathMatchResult((), ("同じパスが複数追加されています。",))
    try:
        key_index = bundle.field_names.index(key_field)
        output_index = bundle.field_names.index(output_field)
    except ValueError:
        return RecordPathMatchResult((), ("照合列または出力名列が対応表にありません。",))

    if mode == "order":
        if len(paths) != len(bundle.rows):
            message = (
                f"順番対応では、パス{len(paths)}件とレコード{len(bundle.rows)}件を"
                "同数にしてください。"
            )
            return RecordPathMatchResult(
                (),
                (message,),
            )
        matches = tuple(
            RecordPathMatch(
                source=path,
                row_identifier=row.identifier,
                key=row.values[key_index],
                output_name=row.values[output_index],
            )
            for path, row in zip(paths, bundle.rows)
        )
        blank_rows = [index for index, match in enumerate(matches, start=1) if not match.output_name.strip()]
        issues = (
            (f"出力名が空の対応があります: {', '.join(map(str, blank_rows))}",)
            if blank_rows
            else ()
        )
        return RecordPathMatchResult(matches, issues)

    def compare(value: str) -> str:
        return value if case_sensitive else value.casefold()

    row_candidates = tuple(
        (row, row.values[key_index].strip(), row.values[output_index].strip()) for row in bundle.rows
    )
    matches: list[RecordPathMatch] = []
    issues: list[str] = []
    used_rows: set[str] = set()
    for path in paths:
        path_values = {compare(path.name), compare(path.stem)}
        candidates = []
        for row, key, output_name in row_candidates:
            compared_key = compare(key)
            if not key:
                continue
            matched = (
                compared_key in path_values
                if mode == "exact"
                else compared_key in compare(path.stem)
            )
            if matched:
                candidates.append((row, key, output_name))
        if not candidates:
            issues.append(f"対応するレコードがありません: {path.name}")
            continue
        if len(candidates) > 1:
            issues.append(f"複数のレコードが一致します: {path.name}")
            continue
        row, key, output_name = candidates[0]
        if row.identifier in used_rows:
            issues.append(f"同じレコードが複数のパスに対応します: {key}")
            continue
        if not output_name:
            issues.append(f"変更後の名前が空です: {path.name}")
            continue
        used_rows.add(row.identifier)
        matches.append(RecordPathMatch(path, row.identifier, key, output_name))
    return RecordPathMatchResult(tuple(matches), tuple(issues))
