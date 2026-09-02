"""Shared, explicit local catalog for logical media items.

One record represents one video, work, or other logical media item.  The
record itself only has a permanent ID and a flexible set of attributes.  The
current filesystem path is intentionally not catalogued: it is a transient
observation, whereas observed file names, source IDs, categories and backup
facts remain useful after files move.
"""

from __future__ import annotations

import json
import os
import tempfile
from hashlib import sha256
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


CATALOG_SCHEMA_VERSION = 2
CATALOG_KIND = "media_catalog"
DEFAULT_CATALOG_FILENAME = "media_catalog.json"


@dataclass(frozen=True)
class CatalogAttribute:
    """One durable fact about a media item, expressed as key plus value."""

    key: str
    value: Any
    value_type: str
    source: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "value": self.value,
            "value_type": self.value_type,
            "source": self.source,
        }


@dataclass(frozen=True)
class CatalogRecordInput:
    """New facts to add to one logical media record.

    ``identity`` is optional.  A stable source ID, such as a YouTube video ID,
    updates the matching record.  Local files have no safe identity without a
    user decision, so they intentionally create a new record in this initial
    version rather than being guessed into an existing one.
    """

    attributes: tuple[CatalogAttribute, ...]
    identity: tuple[str, Any] | None = None


@dataclass(frozen=True)
class CatalogWriteReport:
    path: Path
    added: int
    updated: int
    skipped: int = 0


@dataclass(frozen=True)
class CatalogConservativeMatch:
    """One one-to-one match selected by a conservative catalog merge."""

    incoming_index: int
    target_index: int


@dataclass(frozen=True)
class CatalogValueConflict:
    """A non-empty value that would need an overwrite during a merge."""

    incoming_index: int
    target_index: int
    key: str


@dataclass(frozen=True)
class CatalogConservativeMergePlan:
    """A fully checked, non-mutating catalog merge proposal.

    This deliberately accepts only one-to-one associations.  The plan also
    carries a fingerprint of the target file so a changed file cannot be
    written based on an old confirmation dialog.
    """

    path: Path
    target_record_count: int
    incoming_record_count: int
    match_keys: tuple[str, ...]
    matches: tuple[CatalogConservativeMatch, ...]
    unmatched_incoming_indexes: tuple[int, ...]
    ambiguous_matches: tuple[tuple[int, tuple[int, ...]], ...]
    duplicate_target_matches: tuple[tuple[int, tuple[int, ...]], ...]
    value_conflicts: tuple[CatalogValueConflict, ...]
    document_fingerprint: str

    @property
    def unchanged_target_count(self) -> int:
        return self.target_record_count - len({match.target_index for match in self.matches})

    @property
    def is_safe(self) -> bool:
        return bool(self.match_keys) and not (
            self.unmatched_incoming_indexes
            or self.ambiguous_matches
            or self.duplicate_target_matches
            or self.value_conflicts
        ) and len(self.matches) == self.incoming_record_count


_IDENTITY_ATTRIBUTE_KEYS = frozenset({"source.youtube.video_id", "catalog.work_key"})


def resolve_catalog_path(value: str | Path, *, output_directory: Path) -> Path:
    """Resolve a directory or `.json` filename selected explicitly by a user."""
    raw = str(value).strip()
    if not raw:
        return output_directory.expanduser() / DEFAULT_CATALOG_FILENAME
    path = Path(raw).expanduser()
    if raw.endswith(("/", os.sep)) or path.is_dir():
        return path / DEFAULT_CATALOG_FILENAME
    if path.suffix:
        if path.suffix.lower() != ".json":
            raise ValueError("管理JSONのファイル名は .json で終わらせてください。")
        return path
    if path.exists():
        raise ValueError("管理JSONにはフォルダまたは .json ファイルを指定してください。")
    return path / DEFAULT_CATALOG_FILENAME


def validate_catalog_write_path(value: str | Path, *, output_directory: Path) -> Path:
    """Return a currently usable catalog destination without creating it.

    This performs no mkdir or file creation. Callers can use it before
    locking an editing session; the later explicit write still reports any
    OS-level race or failure during its atomic write.
    """
    output = resolve_catalog_path(value, output_directory=output_directory)
    parent = output.parent
    if not parent.is_dir():
        raise ValueError(f"対象JSONの保存先フォルダがありません: {parent}")
    if not os.access(parent, os.W_OK | os.X_OK):
        raise ValueError(f"対象JSONの保存先フォルダへ書き込めません: {parent}")
    if output.exists():
        if not output.is_file():
            raise ValueError("対象JSONが通常ファイルではありません。")
        if not os.access(output, os.R_OK):
            raise ValueError(f"既存の対象JSONを読み取れません: {output}")
    return output


def resolve_new_catalog_path(value: str | Path, *, output_directory: Path) -> Path:
    """Resolve a destination for a *new* catalog without reusing a file.

    A directory is treated as an explicit request for a new catalog.  The
    ordinary name is used when available; otherwise `` (1)``, `` (2)`` and so
    on are added.  A direct non-existing ``.json`` filename is returned as-is.
    Direct existing files remain direct existing files so the UI can ask the
    user whether to append, conservatively merge, or create a sibling file.
    """
    raw = str(value).strip()
    if not raw:
        raise ValueError("共通メディア台帳の保存先を入力してください。")
    path = Path(raw).expanduser()
    if raw.endswith(("/", os.sep)) or path.is_dir() or (not path.suffix and not path.exists()):
        _validate_catalog_directory(path)
        return unique_json_path(path / DEFAULT_CATALOG_FILENAME)
    if path.suffix.lower() != ".json":
        raise ValueError("管理JSONのファイル名は .json で終わらせてください。")
    _validate_catalog_parent(path)
    if path.exists() and not path.is_file():
        raise ValueError("対象JSONが通常ファイルではありません。")
    return path


def unique_json_path(path: str | Path) -> Path:
    """Return a currently unused sibling name without creating a file."""
    candidate = Path(path).expanduser()
    if candidate.suffix.lower() != ".json":
        raise ValueError("JSONのファイル名は .json で終わらせてください。")
    if not candidate.exists():
        return candidate
    for number in range(1, 100_000):
        alternative = candidate.with_name(f"{candidate.stem} ({number}){candidate.suffix}")
        if not alternative.exists():
            return alternative
    raise ValueError("新しいJSON名を決められませんでした。保存先を整理してください。")


def validate_existing_catalog_path(path: str | Path) -> Path:
    """Validate an existing readable media catalog selected for a mutation."""
    candidate = Path(path).expanduser()
    _validate_catalog_parent(candidate)
    if not candidate.is_file():
        raise ValueError("既存の共通メディア台帳JSONを指定してください。")
    if not os.access(candidate, os.R_OK | os.W_OK):
        raise ValueError(f"既存の対象JSONを読み書きできません: {candidate}")
    _load_catalog_document(candidate)
    return candidate


def _validate_catalog_directory(directory: Path) -> None:
    if not directory.is_dir():
        raise ValueError(f"対象JSONの保存先フォルダがありません: {directory}")
    if not os.access(directory, os.W_OK | os.X_OK):
        raise ValueError(f"対象JSONの保存先フォルダへ書き込めません: {directory}")


def _validate_catalog_parent(path: Path) -> None:
    _validate_catalog_directory(path.parent)


def add_catalog_records(path: Path, records: Iterable[CatalogRecordInput]) -> CatalogWriteReport:
    """Add attributes to the selected shared catalog by atomic replacement."""
    return _write_catalog_records(path, records, update_existing=True)


def create_catalog_records(path: Path, records: Iterable[CatalogRecordInput]) -> CatalogWriteReport:
    """Create a new catalog only; refuse to touch an existing JSON file."""
    values = _nonempty_catalog_records(records)
    destination = Path(path).expanduser()
    _validate_catalog_parent(destination)
    if destination.suffix.lower() != ".json":
        raise ValueError("管理JSONのファイル名は .json で終わらせてください。")
    if destination.exists():
        raise ValueError(f"新規保存先に既存JSONがあります: {destination}")
    document = _new_catalog_document(values)
    _create_json_exclusive(destination, document)
    return CatalogWriteReport(path=destination, added=len(values), updated=0)


def replace_catalog_records(path: Path, records: Iterable[CatalogRecordInput]) -> CatalogWriteReport:
    """Atomically replace one verified catalog with the current full snapshot."""
    destination = validate_existing_catalog_path(path)
    values = _nonempty_catalog_records(records)
    _atomic_write_json(destination, _new_catalog_document(values))
    return CatalogWriteReport(path=destination, added=len(values), updated=0)


def append_catalog_records(path: Path, records: Iterable[CatalogRecordInput]) -> CatalogWriteReport:
    """Append distinct records at the end; never match or update old records."""
    destination = validate_existing_catalog_path(path)
    values = _nonempty_catalog_records(records)
    if not values:
        return CatalogWriteReport(path=destination, added=0, updated=0)
    document = _load_catalog_document(destination)
    media = document["media"]
    assert isinstance(media, list)
    for incoming in values:
        media.append(_catalog_record_dict(_next_record_id(media), incoming))
    _atomic_write_json(destination, document)
    return CatalogWriteReport(path=destination, added=len(values), updated=0)


def available_conservative_match_keys(
    path: Path, records: Iterable[CatalogRecordInput]
) -> tuple[str, ...]:
    """Return keys which occur on both sides and can be selected for matching."""
    destination = validate_existing_catalog_path(path)
    incoming = _nonempty_catalog_records(records)
    document = _load_catalog_document(destination)
    target_keys = {
        key
        for record in document["media"]
        if isinstance(record, dict)
        for key in _record_attribute_value_map(record)
    }
    incoming_keys = {
        attribute.key
        for record in incoming
        for attribute in _with_identity_attribute(record)
        if attribute.key.strip() and attribute.value is not None
    }
    return tuple(sorted(target_keys.intersection(incoming_keys)))


def plan_conservative_catalog_merge(
    path: Path,
    records: Iterable[CatalogRecordInput],
    *,
    match_keys: Iterable[str],
) -> CatalogConservativeMergePlan:
    """Check an all-or-nothing merge without modifying the target catalog.

    Every incoming record must map to exactly one existing record, and every
    existing record can receive no more than one incoming record.  For those
    pairs an attribute may be added only when it is absent on one side or its
    entire value is already equal.  Nothing is silently overwritten.
    """
    destination = validate_existing_catalog_path(path)
    incoming = _nonempty_catalog_records(records)
    selected_keys = _normalized_match_keys(match_keys)
    document = _load_catalog_document(destination)
    target = document["media"]
    assert isinstance(target, list)
    target_maps = tuple(_record_attribute_value_map(record) for record in target)
    incoming_maps = tuple(_incoming_attribute_value_map(record) for record in incoming)

    candidates: list[tuple[int, tuple[int, ...]]] = []
    matches: list[CatalogConservativeMatch] = []
    unmatched: list[int] = []
    ambiguous: list[tuple[int, tuple[int, ...]]] = []
    for incoming_index, incoming_values in enumerate(incoming_maps):
        target_indexes = tuple(
            target_index
            for target_index, target_values in enumerate(target_maps)
            if _matches_all_keys(incoming_values, target_values, selected_keys)
        )
        candidates.append((incoming_index, target_indexes))
        if not target_indexes:
            unmatched.append(incoming_index)
        elif len(target_indexes) == 1:
            matches.append(CatalogConservativeMatch(incoming_index, target_indexes[0]))
        else:
            ambiguous.append((incoming_index, target_indexes))

    target_to_incoming: dict[int, list[int]] = {}
    for incoming_index, target_indexes in candidates:
        if len(target_indexes) == 1:
            target_to_incoming.setdefault(target_indexes[0], []).append(incoming_index)
    duplicates = tuple(
        (target_index, tuple(indexes))
        for target_index, indexes in target_to_incoming.items()
        if len(indexes) > 1
    )

    conflicts: list[CatalogValueConflict] = []
    for match in matches:
        target_values = target_maps[match.target_index]
        incoming_values = incoming_maps[match.incoming_index]
        for key in target_values.keys() & incoming_values.keys():
            if target_values[key] != incoming_values[key]:
                conflicts.append(CatalogValueConflict(match.incoming_index, match.target_index, key))

    return CatalogConservativeMergePlan(
        path=destination,
        target_record_count=len(target),
        incoming_record_count=len(incoming),
        match_keys=selected_keys,
        matches=tuple(matches),
        unmatched_incoming_indexes=tuple(unmatched),
        ambiguous_matches=tuple(ambiguous),
        duplicate_target_matches=duplicates,
        value_conflicts=tuple(conflicts),
        document_fingerprint=_catalog_document_fingerprint(document),
    )


def conservative_catalog_merge_summary(
    plan: CatalogConservativeMergePlan,
    records: Iterable[CatalogRecordInput],
) -> str:
    """Render a complete count-based preview for a merge confirmation."""
    incoming = _nonempty_catalog_records(records)
    lines = [
        f"対象JSONの記録: {plan.target_record_count} 件",
        f"今回の入力: {plan.incoming_record_count} 件",
        f"照合に使う項目: {', '.join(plan.match_keys) if plan.match_keys else '未選択'}",
        f"一意に照合: {len(plan.matches)} 件",
        f"対象側のみ残る: {plan.unchanged_target_count} 件（変更しません）",
        f"入力側照合不能: {len(plan.unmatched_incoming_indexes)} 件",
        f"複数候補に一致: {len(plan.ambiguous_matches)} 件",
        f"同じ対象へ複数入力: {len(plan.duplicate_target_matches)} 件",
        f"値の競合: {len(plan.value_conflicts)} 件",
    ]
    if plan.unmatched_incoming_indexes:
        lines.append("照合不能: " + _incoming_record_names(incoming, plan.unmatched_incoming_indexes))
    if plan.ambiguous_matches:
        lines.append(
            "複数候補: "
            + " / ".join(
                f"{_incoming_record_name(incoming, source)} → {', '.join(str(index + 1) for index in targets)}"
                for source, targets in plan.ambiguous_matches
            )
        )
    if plan.duplicate_target_matches:
        lines.append(
            "重複照合: "
            + " / ".join(
                f"対象 {target + 1} ← {_incoming_record_names(incoming, sources)}"
                for target, sources in plan.duplicate_target_matches
            )
        )
    if plan.value_conflicts:
        lines.append(
            "競合項目: "
            + " / ".join(
                f"{_incoming_record_name(incoming, conflict.incoming_index)}: {conflict.key}"
                for conflict in plan.value_conflicts
            )
        )
    lines.append(
        "照合は安全です。確認後、空欄の補完だけを結合します。"
        if plan.is_safe
        else "安全条件を満たさないため、確認してもJSONは変更しません。"
    )
    return "\n".join(lines)


def apply_conservative_catalog_merge(
    path: Path,
    records: Iterable[CatalogRecordInput],
    plan: CatalogConservativeMergePlan,
) -> CatalogWriteReport:
    """Apply a confirmed safe plan, rejecting stale or unsafe confirmation."""
    destination = validate_existing_catalog_path(path)
    incoming = _nonempty_catalog_records(records)
    document = _load_catalog_document(destination)
    if _catalog_document_fingerprint(document) != plan.document_fingerprint:
        raise ValueError("対象JSONが確認後に変わりました。もう一度照合してください。")
    expected = plan_conservative_catalog_merge(destination, incoming, match_keys=plan.match_keys)
    if expected != plan:
        raise ValueError("照合対象または入力内容が変わりました。もう一度照合してください。")
    if not plan.is_safe:
        raise ValueError("安全条件を満たさないため、JSONを結合しません。")
    media = document["media"]
    assert isinstance(media, list)
    for match in plan.matches:
        record = media[match.target_index]
        assert isinstance(record, dict)
        _append_new_attributes(record, _with_identity_attribute(incoming[match.incoming_index]))
    _atomic_write_json(destination, document)
    return CatalogWriteReport(path=destination, added=0, updated=len(plan.matches))


def _write_catalog_records(
    path: Path, records: Iterable[CatalogRecordInput], *, update_existing: bool
) -> CatalogWriteReport:
    values = _nonempty_catalog_records(records)
    if not values:
        return CatalogWriteReport(path=path, added=0, updated=0)
    path = path.expanduser()
    if path.suffix.lower() != ".json":
        raise ValueError("管理JSONのファイル名は .json で終わらせてください。")
    path.parent.mkdir(parents=True, exist_ok=True)
    document = _load_catalog_document(path)
    media = document["media"]
    assert isinstance(media, list)

    identities = _existing_identity_map(media)
    added = 0
    updated = 0
    skipped = 0
    for incoming in values:
        existing = identities.get(_identity_signature(incoming.identity)) if incoming.identity else None
        if existing is None:
            record = _catalog_record_dict(_next_record_id(media), incoming)
            media.append(record)
            if incoming.identity:
                identities[_identity_signature(incoming.identity)] = record
            added += 1
            continue
        if update_existing:
            _append_new_attributes(existing, _with_identity_attribute(incoming))
            updated += 1
        else:
            skipped += 1

    _atomic_write_json(path, document)
    return CatalogWriteReport(path=path, added=added, updated=updated, skipped=skipped)


def _nonempty_catalog_records(records: Iterable[CatalogRecordInput]) -> tuple[CatalogRecordInput, ...]:
    return tuple(record for record in records if record.attributes)


def _new_catalog_document(records: Iterable[CatalogRecordInput]) -> dict[str, Any]:
    media: list[dict[str, Any]] = []
    for record in records:
        media.append(_catalog_record_dict(_next_record_id(media), record))
    return {"schema_version": CATALOG_SCHEMA_VERSION, "kind": CATALOG_KIND, "media": media}


def _catalog_record_dict(record_id: str, incoming: CatalogRecordInput) -> dict[str, Any]:
    return {
        "record_id": record_id,
        "attributes": [
            attribute.as_dict()
            for attribute in _deduplicate_attributes(_with_identity_attribute(incoming))
        ],
    }


def _normalized_match_keys(keys: Iterable[str]) -> tuple[str, ...]:
    result: list[str] = []
    for raw in keys:
        key = str(raw).strip()
        if key and key not in result:
            result.append(key)
    return tuple(result)


def _record_attribute_value_map(record: Any) -> dict[str, frozenset[str]]:
    if not isinstance(record, dict):
        return {}
    attributes = record.get("attributes")
    if not isinstance(attributes, list):
        return {}
    values: dict[str, set[str]] = {}
    for attribute in attributes:
        if not isinstance(attribute, dict):
            continue
        key = attribute.get("key")
        value = attribute.get("value")
        if not isinstance(key, str) or not key.strip() or value is None:
            continue
        values.setdefault(key.strip(), set()).add(_value_signature(value))
    return {key: frozenset(entries) for key, entries in values.items()}


def _incoming_attribute_value_map(record: CatalogRecordInput) -> dict[str, frozenset[str]]:
    values: dict[str, set[str]] = {}
    for attribute in _with_identity_attribute(record):
        if not attribute.key.strip() or attribute.value is None:
            continue
        values.setdefault(attribute.key.strip(), set()).add(_value_signature(attribute.value))
    return {key: frozenset(entries) for key, entries in values.items()}


def _matches_all_keys(
    incoming: dict[str, frozenset[str]], target: dict[str, frozenset[str]], keys: tuple[str, ...]
) -> bool:
    return bool(keys) and all(incoming.get(key, frozenset()).intersection(target.get(key, frozenset())) for key in keys)


def _value_signature(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _catalog_document_fingerprint(document: dict[str, Any]) -> str:
    text = json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(text.encode("utf-8")).hexdigest()


def _incoming_record_name(records: tuple[CatalogRecordInput, ...], index: int) -> str:
    attributes = _with_identity_attribute(records[index])
    for preferred_key in ("file.name.observed", "title.observed", "source.youtube.video_id", "catalog.work_key"):
        for attribute in attributes:
            if attribute.key == preferred_key and attribute.value is not None:
                return str(attribute.value)
    return f"入力 {index + 1}"


def _incoming_record_names(records: tuple[CatalogRecordInput, ...], indexes: Iterable[int]) -> str:
    return " / ".join(_incoming_record_name(records, index) for index in indexes)


def _load_catalog_document(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"schema_version": CATALOG_SCHEMA_VERSION, "kind": CATALOG_KIND, "media": []}
    if not path.is_file():
        raise ValueError("管理JSONの保存先が通常ファイルではありません。")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("既存の管理JSONを読み取れません。JSON形式を確認してください。") from exc
    if not isinstance(raw, dict):
        raise ValueError("既存の管理JSONは { } で囲んだオブジェクトにしてください。")
    if raw.get("schema_version") == 1 and raw.get("kind") is None:
        return _migrate_legacy_youtube_catalog(raw)
    if raw.get("schema_version") != CATALOG_SCHEMA_VERSION or raw.get("kind") != CATALOG_KIND:
        raise ValueError("既存のJSONは対応する共通メディア台帳ではありません。")
    if not isinstance(raw.get("media"), list):
        raise ValueError("既存の管理JSONの media は一覧である必要があります。")
    for record in raw["media"]:
        if not isinstance(record, dict) or not isinstance(record.get("attributes"), list):
            raise ValueError("既存の管理JSONに不正なメディア記録があります。")
    return raw


def _migrate_legacy_youtube_catalog(raw: dict[str, Any]) -> dict[str, Any]:
    """Convert the earlier download-only catalog without retaining live paths."""
    old_media = raw.get("media")
    if not isinstance(old_media, list):
        raise ValueError("既存の管理JSONの media は一覧である必要があります。")
    media: list[dict[str, Any]] = []
    for old_record in old_media:
        if not isinstance(old_record, dict):
            continue
        attributes: list[CatalogAttribute] = []
        _append_text_if_present(attributes, "title.observed", old_record.get("title"), "youtube")
        source = old_record.get("source")
        if isinstance(source, dict):
            _append_text_if_present(attributes, "source.youtube.video_id", source.get("video_id"), "youtube")
            _append_text_if_present(attributes, "source.youtube.url", source.get("url"), "youtube")
        _append_text_if_present(attributes, "source.youtube.channel", old_record.get("channel_name"), "youtube")
        _append_text_if_present(attributes, "source.youtube.upload_date", old_record.get("upload_date"), "youtube")
        _append_text_if_present(attributes, "source.youtube.kind", old_record.get("source_kind"), "youtube")
        _append_text_if_present(attributes, "download.observed_at", old_record.get("downloaded_at"), "youtube")
        _append_text_if_present(attributes, "download.format_selector", old_record.get("format_selector"), "youtube")
        file_info = old_record.get("file")
        if isinstance(file_info, dict):
            path_text = str(file_info.get("path") or "").strip()
            if path_text:
                _append_text_if_present(attributes, "file.name.observed", Path(path_text).name, "filesystem")
            size = file_info.get("size_bytes")
            if isinstance(size, int):
                attributes.append(CatalogAttribute("file.size_bytes.observed", size, "number", "filesystem"))
        for tag in old_record.get("user_tags", ()) if isinstance(old_record.get("user_tags"), list) else ():
            _append_text_if_present(attributes, "classification.tag", tag, "user")
        media.append(
            {
                "record_id": str(old_record.get("record_id") or _next_record_id(media)),
                "attributes": [attribute.as_dict() for attribute in _deduplicate_attributes(attributes)],
            }
        )
    return {"schema_version": CATALOG_SCHEMA_VERSION, "kind": CATALOG_KIND, "media": media}


def _append_text_if_present(
    attributes: list[CatalogAttribute], key: str, value: Any, source: str
) -> None:
    text = str(value or "").strip()
    if text:
        attributes.append(CatalogAttribute(key, text, "text", source))


def _existing_identity_map(media: list[Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for record in media:
        if not isinstance(record, dict):
            continue
        attributes = record.get("attributes")
        if not isinstance(attributes, list):
            continue
        for attribute in attributes:
            if not isinstance(attribute, dict):
                continue
            key = str(attribute.get("key") or "")
            if key in _IDENTITY_ATTRIBUTE_KEYS:
                result[_identity_signature((key, attribute.get("value")))] = record
    return result


def _with_identity_attribute(record: CatalogRecordInput) -> tuple[CatalogAttribute, ...]:
    """Persist a supported identity too, so later writes can find the record."""
    attributes = tuple(record.attributes)
    if record.identity is None:
        return attributes
    key, value = record.identity
    if key not in _IDENTITY_ATTRIBUTE_KEYS:
        return attributes
    if any(attribute.key == key and attribute.value == value for attribute in attributes):
        return attributes
    return attributes + (CatalogAttribute(key, value, "identity", "catalog"),)


def _identity_signature(identity: tuple[str, Any] | None) -> str:
    if identity is None:
        return ""
    key, value = identity
    return f"{key}\u0000{json.dumps(value, ensure_ascii=False, sort_keys=True)}"


def _append_new_attributes(record: dict[str, Any], incoming: Iterable[CatalogAttribute]) -> None:
    existing = record.get("attributes")
    if not isinstance(existing, list):
        existing = []
        record["attributes"] = existing
    known = {
        _attribute_signature(attribute)
        for attribute in existing
        if isinstance(attribute, dict) and isinstance(attribute.get("key"), str)
    }
    for attribute in _deduplicate_attributes(incoming):
        signature = _attribute_signature(attribute.as_dict())
        if signature not in known:
            existing.append(attribute.as_dict())
            known.add(signature)


def _deduplicate_attributes(attributes: Iterable[CatalogAttribute]) -> tuple[CatalogAttribute, ...]:
    result: list[CatalogAttribute] = []
    known: set[str] = set()
    for attribute in attributes:
        key = attribute.key.strip()
        if not key:
            continue
        normalized = CatalogAttribute(key, attribute.value, attribute.value_type or "text", attribute.source or "unknown")
        signature = _attribute_signature(normalized.as_dict())
        if signature not in known:
            known.add(signature)
            result.append(normalized)
    return tuple(result)


def _attribute_signature(attribute: dict[str, Any]) -> str:
    return f"{attribute.get('key')}\u0000{json.dumps(attribute.get('value'), ensure_ascii=False, sort_keys=True)}"


def _next_record_id(media: list[Any]) -> str:
    highest = 0
    for record in media:
        if not isinstance(record, dict):
            continue
        try:
            highest = max(highest, int(str(record.get("record_id") or "")))
        except ValueError:
            continue
    return f"{highest + 1:05d}"


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


def _create_json_exclusive(path: Path, document: dict[str, Any]) -> None:
    """Create a JSON file once, refusing a race rather than overwriting it."""
    descriptor: int | None = None
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            descriptor = None
            json.dump(document, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError as exc:
        raise ValueError(f"新規保存先に既存JSONがあります: {path}") from exc
    except Exception:
        if descriptor is not None:
            os.close(descriptor)
        # A partial file is only possible after a rare I/O failure.  It was
        # created by this call, so removing it is safe and avoids a misleading
        # future merge target.
        path.unlink(missing_ok=True)
        raise
