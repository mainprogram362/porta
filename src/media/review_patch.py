"""Pathless MPV-review patches and strict filename-based part merging.

An MPV session needs real local paths, but a portable parts JSON must not keep
those paths.  This module bridges the two deliberately: a review patch keeps
only file-name aliases plus player-created rating/tag/highlight facts.  Applying a
patch is a separate, all-or-nothing operation against ID-free media parts.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .catalog import CatalogAttribute, unique_json_path
from .ledger import MediaPart, replace_part_attributes
from .file_attributes import normalize_media_highlights


REVIEW_PATCH_KIND = "media_review_patch"
REVIEW_PATCH_SCHEMA_VERSION = 1
DEFAULT_REVIEW_PATCH_FILENAME = "media_review_patch.json"
REVIEW_PATCH_ATTRIBUTE_KEYS = frozenset(
    {
        "review.score",
        "classification.tag",
        "media.highlights",
    }
)


@dataclass(frozen=True)
class ReviewPatchEntry:
    """One pathless player-review result, keyed by one or more file names."""

    file_names: tuple[str, ...]
    attributes: tuple[CatalogAttribute, ...]


@dataclass(frozen=True)
class MediaReviewPatch:
    """A portable, deliberately narrow collection of player-created facts."""

    entries: tuple[ReviewPatchEntry, ...]


@dataclass(frozen=True)
class ReviewPatchMatch:
    """One unambiguous association between a patch entry and a target part."""

    patch_index: int
    part_index: int


@dataclass(frozen=True)
class ReviewPatchMergePlan:
    """A non-mutating result of checking a patch against candidate parts."""

    target_part_count: int
    patch_entry_count: int
    matches: tuple[ReviewPatchMatch, ...]
    unmatched_patch_indexes: tuple[int, ...]
    ambiguous_matches: tuple[tuple[int, tuple[int, ...]], ...]
    duplicate_target_matches: tuple[tuple[int, tuple[int, ...]], ...]

    @property
    def is_safe(self) -> bool:
        """True only when every patch entry maps one-to-one to one target."""
        return not (
            self.unmatched_patch_indexes
            or self.ambiguous_matches
            or self.duplicate_target_matches
        ) and len(self.matches) == self.patch_entry_count

    @property
    def unchanged_target_count(self) -> int:
        return self.target_part_count - len({match.part_index for match in self.matches})


def review_patch_entry(
    file_name: str, attributes: Iterable[CatalogAttribute]
) -> ReviewPatchEntry:
    """Build one patch entry while retaining only player-owned review keys."""
    normalized_name = file_name.strip()
    if not normalized_name:
        raise ValueError("評価パッチには照合用のファイル名が必要です。")
    review_attributes = tuple(
        CatalogAttribute(attribute.key, attribute.value, attribute.value_type, "mpv_review")
        for attribute in attributes
        if attribute.key in REVIEW_PATCH_ATTRIBUTE_KEYS and attribute.value is not None
    )
    _validate_review_attributes(review_attributes)
    return ReviewPatchEntry((normalized_name,), review_attributes)


def resolve_new_review_patch_path(value: str | Path) -> Path:
    """Resolve a patch destination without selecting an old file by accident."""
    raw = str(value).strip()
    if not raw:
        raise ValueError("評価・タグ・見どころパッチの保存先を入力してください。")
    path = Path(raw).expanduser()
    if raw.endswith(("/", os.sep)) or path.is_dir() or (not path.suffix and not path.exists()):
        _validate_review_patch_parent(path / DEFAULT_REVIEW_PATCH_FILENAME)
        return unique_json_path(path / DEFAULT_REVIEW_PATCH_FILENAME)
    if path.suffix.lower() != ".json":
        raise ValueError("評価・見どころパッチの保存先は .json ファイルにしてください。")
    _validate_review_patch_parent(path)
    if path.exists() and not path.is_file():
        raise ValueError("評価パッチの保存先が通常ファイルではありません。")
    return path


def save_review_patch(path: str | Path, patch: MediaReviewPatch) -> Path:
    """Create a pathless review patch; never replace an existing file."""
    destination = validate_review_patch_write_path(path)
    if destination.exists():
        raise ValueError("既存の評価・タグ・見どころパッチは上書きしません。別の新規JSONを選んでください。")
    normalized = _validated_patch(patch)
    _atomic_write_json(
        destination,
        {
            "schema_version": REVIEW_PATCH_SCHEMA_VERSION,
            "kind": REVIEW_PATCH_KIND,
            "entries": [
                {
                    "file_names": list(entry.file_names),
                    "attributes": [_attribute_as_dict(attribute) for attribute in entry.attributes],
                }
                for entry in normalized.entries
            ],
        },
    )
    return destination


def replace_review_patch(path: str | Path, patch: MediaReviewPatch) -> Path:
    """Atomically replace an existing review patch after caller preflight."""
    destination = validate_review_patch_write_path(path)
    if not destination.is_file():
        raise ValueError("上書きする評価・見どころパッチJSONが見つかりません。")
    normalized = _validated_patch(patch)
    _atomic_write_json(
        destination,
        {
            "schema_version": REVIEW_PATCH_SCHEMA_VERSION,
            "kind": REVIEW_PATCH_KIND,
            "entries": [
                {
                    "file_names": list(entry.file_names),
                    "attributes": [_attribute_as_dict(attribute) for attribute in entry.attributes],
                }
                for entry in normalized.entries
            ],
        },
    )
    return destination


def validate_review_patch_write_path(path: str | Path) -> Path:
    """Check a selected patch destination without creating or changing it."""
    destination = Path(path).expanduser()
    if destination.suffix.lower() != ".json":
        raise ValueError("評価・見どころパッチの保存先は .json ファイルにしてください。")
    _validate_review_patch_parent(destination)
    if destination.exists() and not destination.is_file():
        raise ValueError("評価パッチの保存先が通常ファイルではありません。")
    return destination


def _validate_review_patch_parent(destination: Path) -> None:
    if not destination.parent.is_dir():
        raise ValueError(f"評価パッチの保存先フォルダがありません: {destination.parent}")
    if not os.access(destination.parent, os.W_OK | os.X_OK):
        raise ValueError(f"評価パッチの保存先フォルダへ書き込めません: {destination.parent}")


def load_review_patch(path: str | Path) -> MediaReviewPatch:
    """Read only the narrow, explicit MPV-review patch format."""
    source = Path(path).expanduser()
    if not source.is_file():
        raise ValueError("評価・見どころパッチJSONが通常ファイルとして見つかりません。")
    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("評価・見どころパッチJSONを読み取れません。JSON形式を確認してください。") from exc
    if not isinstance(raw, dict):
        raise ValueError("評価・見どころパッチJSONは { } で囲んだオブジェクトにしてください。")
    if raw.get("kind") != REVIEW_PATCH_KIND or raw.get("schema_version") != REVIEW_PATCH_SCHEMA_VERSION:
        raise ValueError("対応する評価・タグ・見どころパッチJSONではありません。")
    raw_entries = raw.get("entries")
    if not isinstance(raw_entries, list):
        raise ValueError("評価・見どころパッチの entries は一覧にしてください。")
    entries: list[ReviewPatchEntry] = []
    for index, raw_entry in enumerate(raw_entries, start=1):
        if not isinstance(raw_entry, dict) or set(raw_entry) != {"file_names", "attributes"}:
            raise ValueError(f"評価パッチ {index} 件目の形式が不正です。")
        names = _parse_file_names(raw_entry["file_names"], index)
        attributes = _parse_attributes(raw_entry["attributes"], f"評価パッチ {index} 件目の属性")
        _validate_review_attributes(attributes)
        entries.append(ReviewPatchEntry(names, attributes))
    return _validated_patch(MediaReviewPatch(tuple(entries)))


def plan_review_patch_merge(
    parts: Iterable[MediaPart], patch: MediaReviewPatch
) -> ReviewPatchMergePlan:
    """Check exact filename associations without changing any target part.

    Both directions must be one-to-one.  In particular, a single local file
    matching two candidate parts is unsafe, and two patch entries selecting
    the same target part are unsafe as well.
    """
    target_parts = tuple(parts)
    patch = _validated_patch(patch)
    target_name_sets = tuple(_part_file_names(part) for part in target_parts)
    candidate_matches: list[tuple[int, tuple[int, ...]]] = []
    matches: list[ReviewPatchMatch] = []
    unmatched: list[int] = []
    ambiguous: list[tuple[int, tuple[int, ...]]] = []
    for patch_index, entry in enumerate(patch.entries):
        matched_part_indexes = tuple(
            index
            for index, names in enumerate(target_name_sets)
            if set(entry.file_names).intersection(names)
        )
        candidate_matches.append((patch_index, matched_part_indexes))
        if not matched_part_indexes:
            unmatched.append(patch_index)
        elif len(matched_part_indexes) == 1:
            matches.append(ReviewPatchMatch(patch_index, matched_part_indexes[0]))
        else:
            ambiguous.append((patch_index, matched_part_indexes))

    by_target: dict[int, list[int]] = {}
    for patch_index, target_indexes in candidate_matches:
        if len(target_indexes) == 1:
            by_target.setdefault(target_indexes[0], []).append(patch_index)
    duplicates = tuple(
        (target_index, tuple(patch_indexes))
        for target_index, patch_indexes in by_target.items()
        if len(patch_indexes) > 1
    )
    return ReviewPatchMergePlan(
        target_part_count=len(target_parts),
        patch_entry_count=len(patch.entries),
        matches=tuple(matches),
        unmatched_patch_indexes=tuple(unmatched),
        ambiguous_matches=tuple(ambiguous),
        duplicate_target_matches=duplicates,
    )


def review_patch_merge_summary(plan: ReviewPatchMergePlan, patch: MediaReviewPatch) -> str:
    """Render the complete non-mutating association result for confirmation."""
    lines = [
        f"照合対象パーツ: {plan.target_part_count} 件",
        f"評価・見どころパッチ: {plan.patch_entry_count} 件",
        f"一意に一致: {len(plan.matches)} 件",
        f"変更しない対象パーツ: {plan.unchanged_target_count} 件",
    ]
    if plan.unmatched_patch_indexes:
        lines.append("照合不能: " + _patch_entry_names(patch, plan.unmatched_patch_indexes))
    if plan.ambiguous_matches:
        details = []
        for patch_index, target_indexes in plan.ambiguous_matches:
            details.append(
                f"{_patch_entry_name(patch, patch_index)} → 候補 {', '.join(str(index + 1) for index in target_indexes)}"
            )
        lines.append("複数候補に一致: " + " / ".join(details))
    if plan.duplicate_target_matches:
        details = []
        for target_index, patch_indexes in plan.duplicate_target_matches:
            details.append(
                f"候補 {target_index + 1} ← {_patch_entry_names(patch, patch_indexes)}"
            )
        lines.append("同じ候補へ複数のパッチが一致: " + " / ".join(details))
    if plan.is_safe:
        lines.append("照合は安全です。明示実行すると、評価・タグ・見どころだけを画面内のパーツへ移植します。")
    else:
        lines.append("照合が曖昧または不足しています。安全のため、全体を結合しません。")
    return "\n".join(lines)


def apply_review_patch_merge(
    parts: Iterable[MediaPart], patch: MediaReviewPatch, plan: ReviewPatchMergePlan
) -> tuple[MediaPart, ...]:
    """Apply a previously verified plan to memory, or reject the whole merge."""
    target_parts = tuple(parts)
    patch = _validated_patch(patch)
    expected = plan_review_patch_merge(target_parts, patch)
    if plan != expected:
        raise ValueError("照合対象またはパッチが変わりました。もう一度「照合を確認」を押してください。")
    if not plan.is_safe:
        raise ValueError("照合が安全ではないため、評価・見どころを結合しません。")
    result = list(target_parts)
    for match in plan.matches:
        result[match.part_index] = replace_part_attributes(
            result[match.part_index],
            _merge_review_attributes(result[match.part_index].attributes, patch.entries[match.patch_index].attributes),
        )
    return tuple(result)


def _validated_patch(patch: MediaReviewPatch) -> MediaReviewPatch:
    if not isinstance(patch, MediaReviewPatch):
        raise ValueError("評価・見どころパッチの形式が不正です。")
    entries: list[ReviewPatchEntry] = []
    for index, entry in enumerate(patch.entries, start=1):
        if not isinstance(entry, ReviewPatchEntry):
            raise ValueError(f"評価パッチ {index} 件目の形式が不正です。")
        names = _parse_file_names(entry.file_names, index)
        attributes = tuple(entry.attributes)
        _validate_review_attributes(attributes)
        entries.append(ReviewPatchEntry(names, attributes))
    return MediaReviewPatch(tuple(entries))


def _parse_file_names(raw: Any, index: int) -> tuple[str, ...]:
    if not isinstance(raw, (list, tuple)) or not raw:
        raise ValueError(f"評価パッチ {index} 件目には file_names を1件以上指定してください。")
    names: list[str] = []
    for value in raw:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"評価パッチ {index} 件目の file_names が不正です。")
        name = value.strip()
        if Path(name).name != name:
            raise ValueError(f"評価パッチ {index} 件目の file_names はファイル名だけにしてください。")
        if name not in names:
            names.append(name)
    return tuple(names)


def _parse_attributes(raw: Any, label: str) -> tuple[CatalogAttribute, ...]:
    if not isinstance(raw, list):
        raise ValueError(f"{label}が一覧ではありません。")
    attributes: list[CatalogAttribute] = []
    for item in raw:
        if not isinstance(item, dict) or not isinstance(item.get("key"), str) or not item["key"].strip():
            raise ValueError(f"{label}に不正な項目があります。")
        attributes.append(
            CatalogAttribute(
                item["key"].strip(),
                item.get("value"),
                str(item.get("value_type") or "text"),
                str(item.get("source") or "unknown"),
            )
        )
    return tuple(attributes)


def _validate_review_attributes(attributes: Iterable[CatalogAttribute]) -> None:
    score_count = 0
    for attribute in attributes:
        if attribute.key not in REVIEW_PATCH_ATTRIBUTE_KEYS or attribute.value is None:
            raise ValueError("評価パッチには評価・タグ・見どころだけを入れられます。")
        if attribute.key == "review.score":
            score_count += 1
            if score_count > 1 or not isinstance(attribute.value, (int, float)) or not 0 <= attribute.value <= 1:
                raise ValueError("評価パッチの評価は 0〜1 の数値を1件だけにしてください。")
        elif attribute.key == "media.highlights":
            normalize_media_highlights(attribute.value)
        else:
            values = attribute.value if isinstance(attribute.value, list) else [attribute.value]
            if not values or any(not isinstance(value, str) or not value.strip() for value in values):
                raise ValueError("タグ・見どころ時間・メモは空欄でない文字列、または文字列の配列にしてください。")


def _part_file_names(part: MediaPart) -> frozenset[str]:
    names: set[str] = set()
    for attribute in part.attributes:
        if attribute.key == "file.name.observed":
            names.update(_text_values(attribute.value))
    return frozenset(names)


def _merge_review_attributes(
    existing: Iterable[CatalogAttribute], incoming: Iterable[CatalogAttribute]
) -> tuple[CatalogAttribute, ...]:
    result = tuple(existing)
    for attribute in incoming:
        if attribute.key == "review.score":
            result = tuple(item for item in result if item.key != attribute.key) + (attribute,)
            continue
        if attribute.key == "classification.tag":
            existing_values = [
                value
                for item in result
                if item.key == attribute.key
                for value in _text_values(item.value)
            ]
            additions = [
                value for value in _text_values(attribute.value) if value not in existing_values
            ]
            # A tag already present in the target is a true no-op: retain the
            # original attribute exactly, including its source and value shape.
            if not additions:
                continue
            result = tuple(item for item in result if item.key != attribute.key) + (
                CatalogAttribute(
                    attribute.key,
                    existing_values + additions,
                    "text_list",
                    "mpv_review",
                ),
            )
            continue
        if attribute.key == "media.highlights":
            existing_highlights = [
                highlight
                for item in result
                if item.key == attribute.key
                for highlight in normalize_media_highlights(item.value)
            ]
            for highlight in normalize_media_highlights(attribute.value):
                if highlight not in existing_highlights:
                    existing_highlights.append(highlight)
            result = tuple(item for item in result if item.key != attribute.key) + (
                CatalogAttribute(attribute.key, existing_highlights, "highlights", "mpv_review"),
            )
            continue
        existing_values = [
            value
            for item in result
            if item.key == attribute.key
            for value in _text_values(item.value)
        ]
        merged_values = list(existing_values)
        for value in _text_values(attribute.value):
            if value not in merged_values:
                merged_values.append(value)
        result = tuple(item for item in result if item.key != attribute.key) + (
            CatalogAttribute(attribute.key, merged_values, "text_list", "mpv_review"),
        )
    return result


def _text_values(value: Any) -> tuple[str, ...]:
    values = value if isinstance(value, list) else [value]
    result: list[str] = []
    for entry in values:
        if not isinstance(entry, str) or not entry.strip():
            continue
        text = entry.strip()
        if text not in result:
            result.append(text)
    return tuple(result)


def _patch_entry_name(patch: MediaReviewPatch, index: int) -> str:
    return " | ".join(patch.entries[index].file_names)


def _patch_entry_names(patch: MediaReviewPatch, indexes: Iterable[int]) -> str:
    return " / ".join(_patch_entry_name(patch, index) for index in indexes)


def _attribute_as_dict(attribute: CatalogAttribute) -> dict[str, Any]:
    return {
        "key": attribute.key,
        "value": attribute.value,
        "value_type": attribute.value_type,
        "source": attribute.source,
    }


def _atomic_write_json(path: Path, document: dict[str, Any]) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.stem}.", suffix=".tmp", dir=path.parent, text=True
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(document, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
