"""Shared read-only projection, filtering and transient file-path matching."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import unicodedata

from media.catalog import CatalogAttribute
from media.file_attributes import display_catalog_attribute
from media.ledger import load_parts


VIDEO_SUFFIXES = frozenset(
    {
        ".3gp", ".asf", ".avi", ".flv", ".m2ts", ".m4v", ".mkv", ".mov",
        ".mp4", ".mpeg", ".mpg", ".mts", ".ogv", ".ts", ".webm", ".wmv",
    }
)
MAXIMUM_MATCH_CANDIDATES = 1000


@dataclass(frozen=True)
class FilterRule:
    """One AND-connected display condition."""

    field_key: str
    operator: str
    value: str = ""


@dataclass(frozen=True)
class WorkspaceRecord:
    """One stable display projection over source or working-copy attributes."""

    index: int
    attributes: tuple[CatalogAttribute, ...]

    def value(self, key: str) -> object | None:
        return next(
            (attribute.value for attribute in self.attributes if attribute.key == key),
            None,
        )

    def text(self, key: str) -> str:
        attribute = next(
            (attribute for attribute in self.attributes if attribute.key == key), None
        )
        return display_catalog_attribute(attribute) if attribute is not None else ""

    @property
    def title(self) -> str:
        return (
            self.text("title.official")
            or self.text("file.name.observed")
            or "（名称未設定）"
        )

    @property
    def observed_file_names(self) -> tuple[str, ...]:
        value = self.value("file.name.observed")
        values = value if isinstance(value, list) else (value,)
        return tuple(
            str(item).strip()
            for item in values
            if isinstance(item, (str, Path)) and str(item).strip()
        )

    @property
    def score(self) -> float | None:
        value = self.value("review.score")
        try:
            return float(value) if value is not None and str(value).strip() else None
        except (TypeError, ValueError):
            return None

    @property
    def rating_stars(self) -> float | None:
        score = self.score
        if score is None:
            return None
        return score * 10 if 0 <= score <= 1 else score

    @property
    def rating_text(self) -> str:
        stars = self.rating_stars
        return "" if stars is None else f"★{stars:g}"


# Compatibility name for callers of the retired viewer model.
ViewerRecord = WorkspaceRecord


def load_workspace_records(path: str | Path) -> tuple[WorkspaceRecord, ...]:
    """Load supported JSON as immutable projections without changing it."""
    document = load_parts(path)
    return tuple(
        WorkspaceRecord(index, part.attributes)
        for index, part in enumerate(document.parts)
    )


load_viewer_records = load_workspace_records


def path_candidates(path: str | Path) -> tuple[Path, ...]:
    """Return one file or up to 1000 direct files in stable filename order."""
    source = Path(path).expanduser()
    if source.is_file():
        return (source.absolute(),)
    if not source.is_dir():
        raise ValueError(
            "ファイル、またはファイルを直下に含むフォルダを指定してください。"
        )
    try:
        files = sorted(
            (
                candidate.absolute()
                for candidate in source.iterdir()
                if candidate.is_file()
            ),
            key=lambda candidate: (candidate.name.casefold(), str(candidate)),
        )
    except OSError as exc:
        raise ValueError("指定されたフォルダを読み取れません。") from exc
    return tuple(files[:MAXIMUM_MATCH_CANDIDATES])


def collect_path_candidates(paths: tuple[str | Path, ...]) -> tuple[Path, ...]:
    """Combine files/folders and stop on ambiguous duplicate filenames."""
    combined: list[Path] = []
    seen_paths: set[Path] = set()
    for source in paths:
        for candidate in path_candidates(source):
            if candidate in seen_paths:
                continue
            seen_paths.add(candidate)
            combined.append(candidate)

    by_name: dict[str, list[Path]] = {}
    for candidate in combined:
        key = unicodedata.normalize("NFKC", candidate.name).casefold()
        by_name.setdefault(key, []).append(candidate)
    duplicates = [values for values in by_name.values() if len(values) > 1]
    if duplicates:
        details = [
            f"{values[0].name}: " + " / ".join(str(path) for path in values[:3])
            for values in duplicates[:10]
        ]
        remaining = len(duplicates) - len(details)
        if remaining:
            details.append(f"ほか {remaining} 種類")
        raise ValueError(
            "同じファイル名の候補が複数あるため、紐づけを中止しました。\n"
            + "\n".join(details)
        )
    return tuple(combined[:MAXIMUM_MATCH_CANDIDATES])


def match_paths(
    records: tuple[WorkspaceRecord, ...], candidates: tuple[Path, ...]
) -> dict[int, Path]:
    """Associate an exact name first, then one conservative relaxed name."""
    record_names: dict[str, list[int]] = {}
    for record in records:
        normalized_for_record = {
            unicodedata.normalize("NFKC", name).casefold()
            for name in record.observed_file_names
        }
        for normalized_name in normalized_for_record:
            record_names.setdefault(normalized_name, []).append(record.index)
    duplicate_record_names = {
        name: indexes for name, indexes in record_names.items() if len(indexes) > 1
    }
    if duplicate_record_names:
        preview = " / ".join(sorted(duplicate_record_names)[:10])
        raise ValueError(
            "一覧側に同じファイル名が複数あるため、紐づけを中止しました: "
            + preview
        )

    candidate_names: dict[str, list[Path]] = {}
    for candidate in candidates:
        normalized_name = unicodedata.normalize("NFKC", candidate.name).casefold()
        candidate_names.setdefault(normalized_name, []).append(candidate)
    duplicate_candidate_names = {
        name: paths for name, paths in candidate_names.items() if len(paths) > 1
    }
    if duplicate_candidate_names:
        preview = " / ".join(sorted(duplicate_candidate_names)[:10])
        raise ValueError(
            "照合するファイル側に同じファイル名が複数あるため、紐づけを中止しました: "
            + preview
        )

    result: dict[int, Path] = {}
    for record in records:
        names = record.observed_file_names
        exact = next(
            (candidate for candidate in candidates if candidate.name in names), None
        )
        if exact is not None:
            result[record.index] = exact
            continue
        normalized_names = {_normalized_stem(name) for name in names}
        normalized_names.discard("")
        relaxed = next(
            (
                candidate
                for candidate in candidates
                if _normalized_stem(candidate.name) in normalized_names
            ),
            None,
        )
        if relaxed is not None:
            result[record.index] = relaxed
    return result


def is_video_path(path: str | Path) -> bool:
    return Path(path).suffix.casefold() in VIDEO_SUFFIXES


def record_matches_rules(
    record: WorkspaceRecord, rules: tuple[FilterRule, ...]
) -> bool:
    """Return whether every explicit display rule accepts this record."""
    for rule in rules:
        value = record.value(rule.field_key)
        display = record.text(rule.field_key)
        if rule.operator == "exists":
            if value is None or not display.strip():
                return False
            continue
        if rule.operator == "missing":
            if value is not None and display.strip():
                return False
            continue
        if rule.field_key == "review.score" and rule.operator in {
            "at_least",
            "at_most",
            "equals",
        }:
            try:
                expected = float(rule.value)
            except ValueError:
                return False
            actual = record.rating_stars
            if actual is None:
                return False
            if rule.operator == "at_least" and actual < expected:
                return False
            if rule.operator == "at_most" and actual > expected:
                return False
            if rule.operator == "equals" and actual != expected:
                return False
            continue
        actual_text = display.casefold()
        expected_text = rule.value.strip().casefold()
        if rule.operator == "contains" and expected_text not in actual_text:
            return False
        if rule.operator == "not_contains" and expected_text in actual_text:
            return False
        if rule.operator == "equals" and actual_text != expected_text:
            return False
    return True


def record_search_text(record: WorkspaceRecord) -> str:
    """Return all durable values used by the one-box workspace search."""
    return "\n".join(
        (
            record.title,
            *(
                f"{attribute.key} {display_catalog_attribute(attribute)}"
                for attribute in record.attributes
            ),
        )
    )


def _normalized_stem(value: str) -> str:
    stem = Path(value).stem
    normalized = unicodedata.normalize("NFKC", stem).casefold()
    return re.sub(r"[^\w]+", "", normalized, flags=re.UNICODE)
