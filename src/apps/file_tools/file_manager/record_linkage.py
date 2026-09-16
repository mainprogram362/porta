"""Session-only links between filesystem names and one record-bundle field."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from records.record_bundle import RecordBundle


LinkFailureReason = Literal["no_match", "ambiguous", "multiple_paths"]


@dataclass(frozen=True)
class PathRecordLink:
    """One path linked to exactly one record through one explicit field."""

    source: Path
    row_identifier: str
    row_number: int
    field_index: int
    field_name: str
    matched_value: str
    record_values: tuple[str, ...]

    @property
    def display_text(self) -> str:
        return f"項目{self.field_index + 1}「{self.field_name}」→ レコード{self.row_number}"


@dataclass(frozen=True)
class PathLinkFailure:
    """A path which cannot be linked under the strict one-match rule."""

    source: Path
    reason: LinkFailureReason
    matching_row_identifiers: tuple[str, ...] = ()
    matching_path_count: int = 0

    @property
    def display_text(self) -> str:
        if self.reason == "ambiguous":
            return f"失敗：同名が{len(self.matching_row_identifiers)}レコード"
        if self.reason == "multiple_paths":
            return f"失敗：同名の対象が{self.matching_path_count}件"
        return "未紐づけ：完全一致なし"


@dataclass(frozen=True)
class RecordLinkageResult:
    """Complete result for one bundle, one field and one path-list snapshot."""

    bundle: RecordBundle
    field_index: int
    links: tuple[PathRecordLink, ...]
    failures: tuple[PathLinkFailure, ...]

    @property
    def field_name(self) -> str:
        return self.bundle.field_names[self.field_index]

    @property
    def linked_paths(self) -> tuple[Path, ...]:
        return tuple(link.source for link in self.links)

    def link_for_path(self, path: str | Path) -> PathRecordLink | None:
        target = Path(path)
        return next((link for link in self.links if link.source == target), None)


def link_paths_to_record_field(
    bundle: RecordBundle,
    paths: Iterable[str | Path],
    *,
    field_index: int,
) -> RecordLinkageResult:
    """Match each whole basename to exactly one value in one selected field.

    Matching is deliberately case-sensitive and literal.  Files and folders
    use the same rule: only ``Path.name`` participates; stems and partial
    matches are never considered.
    """
    if not 0 <= field_index < len(bundle.field_names):
        raise ValueError("紐づける項目が対応表の範囲外です。")

    rows_by_value: dict[str, list[tuple[int, str, tuple[str, ...]]]] = defaultdict(list)
    for row_number, row in enumerate(bundle.rows, start=1):
        value = row.values[field_index]
        if value:
            rows_by_value[value].append((row_number, row.identifier, row.values))

    source_paths = tuple(Path(value) for value in paths)
    path_counts: dict[str, int] = defaultdict(int)
    for source in source_paths:
        path_counts[source.name] += 1

    links: list[PathRecordLink] = []
    failures: list[PathLinkFailure] = []
    for source in source_paths:
        matches = rows_by_value.get(source.name, ())
        if len(matches) == 1 and path_counts[source.name] == 1:
            row_number, identifier, record_values = matches[0]
            links.append(
                PathRecordLink(
                    source=source,
                    row_identifier=identifier,
                    row_number=row_number,
                    field_index=field_index,
                    field_name=bundle.field_names[field_index],
                    matched_value=source.name,
                    record_values=record_values,
                )
            )
        elif len(matches) != 1:
            failures.append(
                PathLinkFailure(
                    source=source,
                    reason="ambiguous" if matches else "no_match",
                    matching_row_identifiers=tuple(match[1] for match in matches),
                )
            )
        else:
            failures.append(
                PathLinkFailure(
                    source=source,
                    reason="multiple_paths",
                    matching_row_identifiers=(matches[0][1],),
                    matching_path_count=path_counts[source.name],
                )
            )
    return RecordLinkageResult(bundle, field_index, tuple(links), tuple(failures))
