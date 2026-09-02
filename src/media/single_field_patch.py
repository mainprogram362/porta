"""Safe, pathless patches that replace one text field on matched media parts.

Unlike an editing session, a patch carries no local path and no arbitrary
commands.  It explicitly names one permitted text field, uses only ``replace``
and identifies each target by observed file name.  Loading is therefore able to
prove every association before the caller changes its in-memory candidates.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .catalog import unique_json_path
from .file_attributes import STANDARD_CATALOG_FIELDS
from .ledger import MediaPart, replace_attribute, replace_part_attributes


SINGLE_FIELD_PATCH_KIND = "media_single_field_patch"
SINGLE_FIELD_PATCH_SCHEMA_VERSION = 1
DEFAULT_SINGLE_FIELD_PATCH_FILENAME = "media_single_field_patch.json"

# A text workspace can create descriptive values, not filesystem observations,
# decoder facts, or list-valued attributes such as tags.  Those latter fields
# have their own controlled workflows and merge rules.
PATCHABLE_TEXT_FIELD_KEYS = frozenset(
    field.key
    for field in STANDARD_CATALOG_FIELDS
    if field.value_type == "text"
    and not field.allows_multiple
    and field.source_key is not None
    and not field.source_key.startswith(("file.", "video.", "audio."))
)


@dataclass(frozen=True)
class SingleFieldPatchEntry:
    """One replacement value associated with one or more filename aliases."""

    file_names: tuple[str, ...]
    value: str


@dataclass(frozen=True)
class MediaSingleFieldPatch:
    """A portable replacement patch for exactly one allowed text field."""

    field_key: str
    entries: tuple[SingleFieldPatchEntry, ...]


@dataclass(frozen=True)
class SingleFieldPatchMatch:
    patch_index: int
    part_index: int


@dataclass(frozen=True)
class SingleFieldPatchMergePlan:
    """The entire non-mutating filename-association result."""

    target_part_count: int
    patch_entry_count: int
    matches: tuple[SingleFieldPatchMatch, ...]
    unmatched_patch_indexes: tuple[int, ...]
    ambiguous_matches: tuple[tuple[int, tuple[int, ...]], ...]
    duplicate_target_matches: tuple[tuple[int, tuple[int, ...]], ...]

    @property
    def is_safe(self) -> bool:
        return not (
            self.unmatched_patch_indexes
            or self.ambiguous_matches
            or self.duplicate_target_matches
        ) and len(self.matches) == self.patch_entry_count

    @property
    def unchanged_target_count(self) -> int:
        return self.target_part_count - len({match.part_index for match in self.matches})


def single_field_patch_entry(file_name: str, value: str) -> SingleFieldPatchEntry:
    """Create one strict, single-name entry from a workspace row."""
    return SingleFieldPatchEntry(_parse_file_names((file_name,), 1), _parse_value(value, 1))


def resolve_new_single_field_patch_path(value: str | Path) -> Path:
    """Resolve a requested fresh patch destination without writing anything."""
    raw = str(value).strip()
    if not raw:
        raise ValueError("項目更新パッチの保存先を入力してください。")
    path = Path(raw).expanduser()
    if raw.endswith(("/", os.sep)) or path.is_dir() or (not path.suffix and not path.exists()):
        _validate_parent(path / DEFAULT_SINGLE_FIELD_PATCH_FILENAME)
        return unique_json_path(path / DEFAULT_SINGLE_FIELD_PATCH_FILENAME)
    if path.suffix.lower() != ".json":
        raise ValueError("項目更新パッチの保存先は .json ファイルにしてください。")
    _validate_parent(path)
    if path.exists() and not path.is_file():
        raise ValueError("項目更新パッチの保存先が通常ファイルではありません。")
    return path


def save_single_field_patch(path: str | Path, patch: MediaSingleFieldPatch) -> Path:
    """Create a new patch only; a caller must opt in separately to replacement."""
    destination = Path(path).expanduser()
    if destination.suffix.lower() != ".json":
        raise ValueError("項目更新パッチの保存先は .json ファイルにしてください。")
    _validate_parent(destination)
    if destination.exists():
        raise ValueError("既存の項目更新パッチは上書きしません。別名の新規JSONを選んでください。")
    normalized = _validated_patch(patch)
    _create_json_exclusive(destination, _patch_as_dict(normalized))
    return destination


def load_single_field_patch(path: str | Path) -> MediaSingleFieldPatch:
    """Load only the narrow one-field patch schema."""
    source = Path(path).expanduser()
    if not source.is_file():
        raise ValueError("項目更新パッチJSONが通常ファイルとして見つかりません。")
    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("項目更新パッチJSONを読み取れません。JSON形式を確認してください。") from exc
    if not isinstance(raw, dict):
        raise ValueError("項目更新パッチJSONは { } で囲んだオブジェクトにしてください。")
    if raw.get("kind") != SINGLE_FIELD_PATCH_KIND or raw.get("schema_version") != SINGLE_FIELD_PATCH_SCHEMA_VERSION:
        raise ValueError("対応する項目更新パッチJSONではありません。")
    if raw.get("operation") != "replace":
        raise ValueError("項目更新パッチの操作は replace だけにしてください。")
    field_key = raw.get("field")
    entries_raw = raw.get("entries")
    if not isinstance(entries_raw, list):
        raise ValueError("項目更新パッチの entries は一覧にしてください。")
    entries: list[SingleFieldPatchEntry] = []
    for index, raw_entry in enumerate(entries_raw, start=1):
        if not isinstance(raw_entry, dict) or set(raw_entry) != {"file_names", "value"}:
            raise ValueError(f"項目更新パッチ {index} 件目の形式が不正です。")
        entries.append(
            SingleFieldPatchEntry(
                _parse_file_names(raw_entry["file_names"], index),
                _parse_value(raw_entry["value"], index),
            )
        )
    return _validated_patch(MediaSingleFieldPatch(str(field_key or ""), tuple(entries)))


def plan_single_field_patch_merge(
    parts: Iterable[MediaPart], patch: MediaSingleFieldPatch
) -> SingleFieldPatchMergePlan:
    """Prove one-to-one matching before any caller-owned item is changed."""
    target_parts = tuple(parts)
    patch = _validated_patch(patch)
    target_name_sets = tuple(_part_file_names(part) for part in target_parts)
    candidate_matches: list[tuple[int, tuple[int, ...]]] = []
    matches: list[SingleFieldPatchMatch] = []
    unmatched: list[int] = []
    ambiguous: list[tuple[int, tuple[int, ...]]] = []
    for patch_index, entry in enumerate(patch.entries):
        target_indexes = tuple(
            index
            for index, names in enumerate(target_name_sets)
            if set(entry.file_names).intersection(names)
        )
        candidate_matches.append((patch_index, target_indexes))
        if not target_indexes:
            unmatched.append(patch_index)
        elif len(target_indexes) == 1:
            matches.append(SingleFieldPatchMatch(patch_index, target_indexes[0]))
        else:
            ambiguous.append((patch_index, target_indexes))
    by_target: dict[int, list[int]] = {}
    for patch_index, target_indexes in candidate_matches:
        if len(target_indexes) == 1:
            by_target.setdefault(target_indexes[0], []).append(patch_index)
    duplicates = tuple(
        (target_index, tuple(patch_indexes))
        for target_index, patch_indexes in by_target.items()
        if len(patch_indexes) > 1
    )
    return SingleFieldPatchMergePlan(
        target_part_count=len(target_parts),
        patch_entry_count=len(patch.entries),
        matches=tuple(matches),
        unmatched_patch_indexes=tuple(unmatched),
        ambiguous_matches=tuple(ambiguous),
        duplicate_target_matches=duplicates,
    )


def single_field_patch_merge_summary(
    plan: SingleFieldPatchMergePlan, patch: MediaSingleFieldPatch
) -> str:
    """Return a complete, human-readable preflight report."""
    lines = [
        f"更新する項目: {patch.field_key}",
        f"照合対象パーツ: {plan.target_part_count} 件",
        f"項目更新パッチ: {plan.patch_entry_count} 件",
        f"一意に一致: {len(plan.matches)} 件",
        f"変更しない対象パーツ: {plan.unchanged_target_count} 件",
    ]
    if plan.unmatched_patch_indexes:
        lines.append("照合不能: " + _entry_names(patch, plan.unmatched_patch_indexes))
    if plan.ambiguous_matches:
        details = [
            f"{_entry_name(patch, patch_index)} → 候補 {', '.join(str(index + 1) for index in target_indexes)}"
            for patch_index, target_indexes in plan.ambiguous_matches
        ]
        lines.append("複数候補に一致: " + " / ".join(details))
    if plan.duplicate_target_matches:
        details = [
            f"候補 {target_index + 1} ← {_entry_names(patch, patch_indexes)}"
            for target_index, patch_indexes in plan.duplicate_target_matches
        ]
        lines.append("同じ候補へ複数のパッチが一致: " + " / ".join(details))
    lines.append(
        "照合は安全です。明示実行すると、この項目だけを画面内のパーツへ置き換えます。"
        if plan.is_safe
        else "照合が曖昧または不足しています。安全のため、何も結合しません。"
    )
    return "\n".join(lines)


def apply_single_field_patch_merge(
    parts: Iterable[MediaPart], patch: MediaSingleFieldPatch, plan: SingleFieldPatchMergePlan
) -> tuple[MediaPart, ...]:
    """Apply a verified plan to memory, or reject the entire operation."""
    target_parts = tuple(parts)
    patch = _validated_patch(patch)
    expected = plan_single_field_patch_merge(target_parts, patch)
    if plan != expected:
        raise ValueError("照合対象またはパッチが変わりました。もう一度照合を確認してください。")
    if not plan.is_safe:
        raise ValueError("照合が安全ではないため、項目更新パッチを結合しません。")
    result = list(target_parts)
    for match in plan.matches:
        entry = patch.entries[match.patch_index]
        result[match.part_index] = replace_part_attributes(
            result[match.part_index],
            replace_attribute(
                result[match.part_index].attributes,
                key=patch.field_key,
                value=entry.value,
                value_type="text",
                source="text_workspace_patch",
            ),
        )
    return tuple(result)


def _validated_patch(patch: MediaSingleFieldPatch) -> MediaSingleFieldPatch:
    if not isinstance(patch, MediaSingleFieldPatch):
        raise ValueError("項目更新パッチの形式が不正です。")
    field_key = patch.field_key.strip()
    if field_key not in PATCHABLE_TEXT_FIELD_KEYS:
        raise ValueError("項目更新パッチで更新できない項目です。")
    entries: list[SingleFieldPatchEntry] = []
    known_name_sets: set[frozenset[str]] = set()
    for index, entry in enumerate(patch.entries, start=1):
        if not isinstance(entry, SingleFieldPatchEntry):
            raise ValueError(f"項目更新パッチ {index} 件目の形式が不正です。")
        names = _parse_file_names(entry.file_names, index)
        name_set = frozenset(names)
        if name_set in known_name_sets:
            raise ValueError("同じ照合用ファイル名の項目更新が重複しています。")
        known_name_sets.add(name_set)
        entries.append(SingleFieldPatchEntry(names, _parse_value(entry.value, index)))
    if not entries:
        raise ValueError("項目更新パッチには1件以上の更新が必要です。")
    return MediaSingleFieldPatch(field_key, tuple(entries))


def _parse_file_names(raw: Any, index: int) -> tuple[str, ...]:
    if not isinstance(raw, (list, tuple)) or not raw:
        raise ValueError(f"項目更新パッチ {index} 件目には file_names を1件以上指定してください。")
    names: list[str] = []
    for value in raw:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"項目更新パッチ {index} 件目の file_names が不正です。")
        name = value.strip()
        if Path(name).name != name:
            raise ValueError(f"項目更新パッチ {index} 件目の file_names はファイル名だけにしてください。")
        if name not in names:
            names.append(name)
    return tuple(names)


def _parse_value(raw: Any, index: int) -> str:
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError(f"項目更新パッチ {index} 件目の value は空欄でない文字列にしてください。")
    return raw.strip()


def _part_file_names(part: MediaPart) -> frozenset[str]:
    names: set[str] = set()
    for attribute in part.attributes:
        if attribute.key != "file.name.observed":
            continue
        raw_values = attribute.value if isinstance(attribute.value, list) else [attribute.value]
        names.update(str(value).strip() for value in raw_values if isinstance(value, str) and value.strip())
    return frozenset(names)


def _entry_name(patch: MediaSingleFieldPatch, index: int) -> str:
    return " | ".join(patch.entries[index].file_names)


def _entry_names(patch: MediaSingleFieldPatch, indexes: Iterable[int]) -> str:
    return " / ".join(_entry_name(patch, index) for index in indexes)


def _patch_as_dict(patch: MediaSingleFieldPatch) -> dict[str, Any]:
    return {
        "schema_version": SINGLE_FIELD_PATCH_SCHEMA_VERSION,
        "kind": SINGLE_FIELD_PATCH_KIND,
        "field": patch.field_key,
        "operation": "replace",
        "entries": [
            {"file_names": list(entry.file_names), "value": entry.value}
            for entry in patch.entries
        ],
    }


def _validate_parent(destination: Path) -> None:
    if not destination.parent.is_dir():
        raise ValueError(f"項目更新パッチの保存先フォルダがありません: {destination.parent}")
    if not os.access(destination.parent, os.W_OK | os.X_OK):
        raise ValueError(f"項目更新パッチの保存先フォルダへ書き込めません: {destination.parent}")


def _create_json_exclusive(path: Path, document: dict[str, Any]) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(document, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        path.unlink(missing_ok=True)
        raise
