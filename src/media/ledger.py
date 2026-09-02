"""Portable JSON ledger for works and their concrete file editions.

The existing ``media_catalog`` format is an extraction result: one record is
one observed item.  This module keeps that format intact and provides the
separate, editable ledger format used by the media-ledger workbench.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Iterable

from .catalog import CatalogAttribute, unique_json_path
from .file_attributes import COLLECTION_STATUS_PRESETS


LEDGER_KIND = "media_ledger"
LEDGER_SCHEMA_VERSION = 1
PARTS_KIND = "media_parts"
PARTS_SCHEMA_VERSION = 1
DEFAULT_PARTS_FILENAME = "media_parts.json"

VARIANT_KINDS = (
    ("元ファイル", "original"),
    ("再エンコード版", "reencoded"),
    ("複製・別保存版", "copy"),
    ("その他", "other"),
)

# Keep the main-ledger choice list identical to the media-information editor.
# This is a collection fact: "低画質のみ" does not imply re-encoding.
COLLECTION_STATUSES = COLLECTION_STATUS_PRESETS
STORAGE_STATUSES = ("ローカル保存", "DVD保存済み", "クラウド保存済み", "原本保持", "再エンコード版のみ")

_FILE_ATTRIBUTE_PREFIXES = ("file.", "video.", "audio.", "folder.")
_FILE_ATTRIBUTE_KEYS = frozenset(
    {"media.duration_seconds", "record.kind", "record.origin", "storage.status"}
)


@dataclass(frozen=True)
class LedgerFile:
    """One concrete saved edition belonging to a logical work."""

    file_id: str
    variant_kind: str
    derived_from_file_id: str | None
    attributes: tuple[CatalogAttribute, ...]


@dataclass(frozen=True)
class LedgerWork:
    """One logical work and the file editions currently known for it."""

    work_id: str
    attributes: tuple[CatalogAttribute, ...]
    files: tuple[LedgerFile, ...]


@dataclass(frozen=True)
class MediaLedger:
    """The in-memory editable state of one ledger JSON document."""

    works: tuple[LedgerWork, ...]


@dataclass(frozen=True)
class MediaPart:
    """An ID-free candidate assembled from extracted or manually edited facts."""

    attributes: tuple[CatalogAttribute, ...]


@dataclass(frozen=True)
class MediaPartsDocument:
    """A freely mergeable collection of candidates, without ledger IDs."""

    parts: tuple[MediaPart, ...]
    converted_from_extraction_catalog: bool = False


def empty_ledger() -> MediaLedger:
    return MediaLedger(works=())


def load_ledger(path: str | Path) -> MediaLedger:
    """Read only an explicit main-ledger JSON document."""
    source = Path(path).expanduser()
    if not source.is_file():
        raise ValueError("台帳JSONが通常ファイルとして見つかりません。")
    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("台帳JSONを読み取れません。JSON形式を確認してください。") from exc
    if not isinstance(raw, dict):
        raise ValueError("台帳JSONは { } で囲んだオブジェクトにしてください。")
    if raw.get("kind") == LEDGER_KIND:
        return _parse_ledger(raw)
    raise ValueError("対応する本台帳JSONではありません。パーツは「パーツ」モードで読み込んでください。")


def load_parts(path: str | Path) -> MediaPartsDocument:
    """Read ID-free parts, or non-destructively adapt the current extraction JSON."""
    source = Path(path).expanduser()
    if not source.is_file():
        raise ValueError("パーツJSONが通常ファイルとして見つかりません。")
    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("パーツJSONを読み取れません。JSON形式を確認してください。") from exc
    if not isinstance(raw, dict):
        raise ValueError("パーツJSONは { } で囲んだオブジェクトにしてください。")
    if raw.get("kind") == PARTS_KIND:
        if raw.get("schema_version") != PARTS_SCHEMA_VERSION or not isinstance(raw.get("parts"), list):
            raise ValueError("パーツJSONの形式またはバージョンが不正です。")
        return MediaPartsDocument(tuple(MediaPart(_parse_attributes(part.get("attributes"), "パーツ属性")) for part in raw["parts"] if isinstance(part, dict)))
    if raw.get("kind") == "media_catalog" and isinstance(raw.get("media"), list):
        parts: list[MediaPart] = []
        for record in raw["media"]:
            if not isinstance(record, dict):
                raise ValueError("メディア情報整理JSONの記録が不正です。")
            # record_id is intentionally not copied: it identifies an old
            # extraction row, not a work or file in the main ledger.
            parts.append(MediaPart(_parse_attributes(record.get("attributes"), "抽出属性")))
        return MediaPartsDocument(tuple(parts), converted_from_extraction_catalog=True)
    raise ValueError("対応するパーツJSON、またはメディア情報整理のJSONではありません。")


def merge_parts(documents: Iterable[MediaPartsDocument]) -> MediaPartsDocument:
    """Concatenate parts without IDs, deduplication guesses or source mutation."""
    return MediaPartsDocument(tuple(part for document in documents for part in document.parts))


def resolve_new_parts_path(value: str | Path) -> Path:
    """Resolve an explicit *new* ID-free parts JSON destination.

    A directory always means a fresh snapshot.  Its ordinary filename is
    used first and then a numbered sibling is selected.  A direct existing
    JSON path remains unchanged so the GUI can ask the user whether to make a
    separate file instead of silently replacing it.
    """
    raw = str(value).strip()
    if not raw:
        raise ValueError("パーツJSONの保存先を入力してください。")
    path = Path(raw).expanduser()
    if raw.endswith(("/", os.sep)) or path.is_dir() or (not path.suffix and not path.exists()):
        _validate_parts_directory(path)
        return unique_json_path(path / DEFAULT_PARTS_FILENAME)
    if path.suffix.lower() != ".json":
        raise ValueError("パーツの保存先は .json ファイルにしてください。")
    _validate_parts_directory(path.parent)
    if path.exists() and not path.is_file():
        raise ValueError("パーツの保存先が通常ファイルではありません。")
    return path


def create_parts(path: str | Path, document: MediaPartsDocument) -> Path:
    """Create a parts snapshot and refuse to overwrite an existing file."""
    destination = Path(path).expanduser()
    if destination.suffix.lower() != ".json":
        raise ValueError("パーツの保存先は .json ファイルにしてください。")
    _validate_parts_directory(destination.parent)
    if destination.exists():
        raise ValueError(f"新規保存先に既存JSONがあります: {destination}")
    _create_json_exclusive(destination, _parts_as_dict(document))
    return destination


def save_parts(path: str | Path, document: MediaPartsDocument) -> Path:
    """Explicitly save an ID-free part collection by atomic replacement."""
    destination = Path(path).expanduser()
    if destination.suffix.lower() != ".json":
        raise ValueError("パーツの保存先は .json ファイルにしてください。")
    _validate_parts_directory(destination.parent)
    if destination.exists() and not destination.is_file():
        raise ValueError("パーツの保存先が通常ファイルではありません。")
    _atomic_write_json(destination, _parts_as_dict(document))
    return destination


def _parts_as_dict(document: MediaPartsDocument) -> dict[str, Any]:
    return {
        "schema_version": PARTS_SCHEMA_VERSION,
        "kind": PARTS_KIND,
        "parts": [
            {"attributes": [_attribute_as_dict(attribute) for attribute in part.attributes]}
            for part in document.parts
        ],
    }


def _validate_parts_directory(directory: Path) -> None:
    if not directory.is_dir():
        raise ValueError(f"パーツの保存先フォルダがありません: {directory}")
    if not os.access(directory, os.W_OK | os.X_OK):
        raise ValueError(f"パーツの保存先フォルダへ書き込めません: {directory}")


def replace_part_attributes(part: MediaPart, attributes: Iterable[CatalogAttribute]) -> MediaPart:
    """Return the edited candidate without adding any master-ledger identity."""
    return MediaPart(tuple(attributes))


def replace_parts_attribute(
    parts: Iterable[MediaPart], *, key: str, value: Any, value_type: str | None = None
) -> tuple[MediaPart, ...]:
    """Apply one explicit attribute replacement to every ID-free part.

    A blank/``None`` value follows :func:`replace_attribute`'s usual rule and
    removes that key.  This intentionally performs no persistence and issues
    no work or file IDs; callers still decide whether to save the resulting
    parts JSON or adopt the parts into a main ledger.
    """
    normalized_key = key.strip()
    if not normalized_key:
        raise ValueError("項目名を入力してください。")
    return tuple(
        replace_part_attributes(
            part,
            replace_attribute(part.attributes, key=normalized_key, value=value, value_type=value_type),
        )
        for part in parts
    )


def adopt_parts(ledger: MediaLedger, parts: Iterable[MediaPart]) -> tuple[MediaLedger, tuple[LedgerWork, ...]]:
    """Explicitly append parts as new works and their first file editions.

    This is the only path here that issues ``W`` and ``F`` identifiers.  A
    later feature may attach a part to an existing work as another edition;
    this initial form deliberately does not guess such a relationship.
    """
    result = ledger
    adopted: list[LedgerWork] = []
    for part in parts:
        work_attributes = tuple(attribute for attribute in part.attributes if not _is_file_attribute(attribute.key))
        file_attributes = tuple(attribute for attribute in part.attributes if _is_file_attribute(attribute.key))
        result, work = add_work(result, work_attributes)
        if attribute_value(part.attributes, "record.kind") != "manual_placeholder":
            result, _file = add_file(result, work.work_id, variant_kind="other", attributes=file_attributes)
        adopted.append(work)
    return result, tuple(adopted)


def save_ledger(path: str | Path, ledger: MediaLedger) -> Path:
    """Explicitly write one normal JSON ledger by atomic replacement."""
    destination = Path(path).expanduser()
    if destination.suffix.lower() != ".json":
        raise ValueError("台帳の保存先は .json ファイルにしてください。")
    if not destination.parent.is_dir():
        raise ValueError(f"台帳の保存先フォルダがありません: {destination.parent}")
    if destination.exists() and not destination.is_file():
        raise ValueError("台帳の保存先が通常ファイルではありません。")
    _atomic_write_json(destination, ledger_as_dict(ledger))
    return destination


def ledger_as_dict(ledger: MediaLedger) -> dict[str, Any]:
    return {
        "schema_version": LEDGER_SCHEMA_VERSION,
        "kind": LEDGER_KIND,
        "works": [
            {
                "work_id": work.work_id,
                "attributes": [_attribute_as_dict(attribute) for attribute in work.attributes],
                "files": [
                    {
                        "file_id": file.file_id,
                        "variant_kind": file.variant_kind,
                        "derived_from_file_id": file.derived_from_file_id,
                        "attributes": [_attribute_as_dict(attribute) for attribute in file.attributes],
                    }
                    for file in work.files
                ],
            }
            for work in ledger.works
        ],
    }


def add_work(ledger: MediaLedger, attributes: Iterable[CatalogAttribute] = ()) -> tuple[MediaLedger, LedgerWork]:
    work = LedgerWork(_next_id("W", (item.work_id for item in ledger.works)), tuple(attributes), ())
    return replace(ledger, works=ledger.works + (work,)), work


def add_file(
    ledger: MediaLedger,
    work_id: str,
    *,
    variant_kind: str = "other",
    derived_from_file_id: str | None = None,
    attributes: Iterable[CatalogAttribute] = (),
) -> tuple[MediaLedger, LedgerFile]:
    if variant_kind not in {value for _label, value in VARIANT_KINDS}:
        raise ValueError("ファイル版種別が不正です。")
    work = work_for_id(ledger, work_id)
    if work is None:
        raise ValueError("追加先の作品IDが見つかりません。")
    known_file_ids = {file.file_id for item in ledger.works for file in item.files}
    if derived_from_file_id and derived_from_file_id not in known_file_ids:
        raise ValueError("元ファイルIDが見つかりません。")
    file = LedgerFile(
        _next_id("F", known_file_ids), variant_kind, derived_from_file_id, tuple(attributes)
    )
    return _replace_work(ledger, replace(work, files=work.files + (file,))), file


def replace_work_attributes(
    ledger: MediaLedger, work_id: str, attributes: Iterable[CatalogAttribute]
) -> MediaLedger:
    work = work_for_id(ledger, work_id)
    if work is None:
        raise ValueError("作品IDが見つかりません。")
    return _replace_work(ledger, replace(work, attributes=tuple(attributes)))


def replace_file(
    ledger: MediaLedger,
    work_id: str,
    file_id: str,
    *,
    variant_kind: str,
    derived_from_file_id: str | None,
    attributes: Iterable[CatalogAttribute],
) -> MediaLedger:
    work = work_for_id(ledger, work_id)
    if work is None:
        raise ValueError("作品IDが見つかりません。")
    if variant_kind not in {value for _label, value in VARIANT_KINDS}:
        raise ValueError("ファイル版種別が不正です。")
    files = list(work.files)
    index = next((i for i, item in enumerate(files) if item.file_id == file_id), -1)
    if index < 0:
        raise ValueError("ファイルIDが見つかりません。")
    if derived_from_file_id == file_id:
        raise ValueError("自分自身を元ファイルには指定できません。")
    known_ids = {file.file_id for item in ledger.works for file in item.files}
    if derived_from_file_id and derived_from_file_id not in known_ids:
        raise ValueError("元ファイルIDが見つかりません。")
    files[index] = LedgerFile(file_id, variant_kind, derived_from_file_id, tuple(attributes))
    return _replace_work(ledger, replace(work, files=tuple(files)))


def work_for_id(ledger: MediaLedger, work_id: str) -> LedgerWork | None:
    return next((work for work in ledger.works if work.work_id == work_id), None)


def file_for_id(work: LedgerWork, file_id: str) -> LedgerFile | None:
    return next((file for file in work.files if file.file_id == file_id), None)


def attribute_value(attributes: Iterable[CatalogAttribute], key: str) -> Any | None:
    value = next((attribute.value for attribute in attributes if attribute.key == key), None)
    return value


def replace_attribute(
    attributes: Iterable[CatalogAttribute],
    *,
    key: str,
    value: Any,
    source: str = "user",
    value_type: str | None = None,
) -> tuple[CatalogAttribute, ...]:
    """Replace one key while retaining the stable key/value attribute format."""
    existing = tuple(attribute for attribute in attributes if attribute.key != key)
    if value is None or (isinstance(value, str) and not value.strip()):
        return existing
    inferred_type = "text" if isinstance(value, str) else "number" if isinstance(value, (int, float)) else "json"
    return existing + (CatalogAttribute(key, value, value_type or inferred_type, source),)


def _parse_ledger(raw: dict[str, Any]) -> MediaLedger:
    if raw.get("schema_version") != LEDGER_SCHEMA_VERSION or not isinstance(raw.get("works"), list):
        raise ValueError("メディア台帳JSONの形式またはバージョンが不正です。")
    works: list[LedgerWork] = []
    work_ids: set[str] = set()
    file_ids: set[str] = set()
    for raw_work in raw["works"]:
        if not isinstance(raw_work, dict):
            raise ValueError("台帳内の作品記録が不正です。")
        work_id = _validated_id(raw_work.get("work_id"), "W", work_ids, "作品ID")
        attributes = _parse_attributes(raw_work.get("attributes"), "作品属性")
        raw_files = raw_work.get("files")
        if not isinstance(raw_files, list):
            raise ValueError("台帳内のファイル一覧が不正です。")
        files: list[LedgerFile] = []
        for raw_file in raw_files:
            if not isinstance(raw_file, dict):
                raise ValueError("台帳内のファイル記録が不正です。")
            file_id = _validated_id(raw_file.get("file_id"), "F", file_ids, "ファイルID")
            variant_kind = str(raw_file.get("variant_kind") or "")
            if variant_kind not in {value for _label, value in VARIANT_KINDS}:
                raise ValueError("台帳内のファイル版種別が不正です。")
            parent_id = raw_file.get("derived_from_file_id")
            if parent_id is not None and (not isinstance(parent_id, str) or not parent_id.startswith("F")):
                raise ValueError("台帳内の元ファイルIDが不正です。")
            files.append(LedgerFile(file_id, variant_kind, parent_id, _parse_attributes(raw_file.get("attributes"), "ファイル属性")))
        works.append(LedgerWork(work_id, attributes, tuple(files)))
    all_file_ids = {file.file_id for work in works for file in work.files}
    for work in works:
        for file in work.files:
            if file.derived_from_file_id and file.derived_from_file_id not in all_file_ids:
                raise ValueError("台帳内の元ファイルIDが見つかりません。")
    return MediaLedger(tuple(works))


def _is_file_attribute(key: str) -> bool:
    return key.startswith(_FILE_ATTRIBUTE_PREFIXES) or key in _FILE_ATTRIBUTE_KEYS


def _parse_attributes(raw: Any, label: str) -> tuple[CatalogAttribute, ...]:
    if not isinstance(raw, list):
        raise ValueError(f"{label}が一覧ではありません。")
    result: list[CatalogAttribute] = []
    for item in raw:
        if not isinstance(item, dict) or not isinstance(item.get("key"), str) or not item["key"].strip():
            raise ValueError(f"{label}に不正な項目があります。")
        result.append(
            CatalogAttribute(
                item["key"].strip(), item.get("value"), str(item.get("value_type") or "text"), str(item.get("source") or "unknown")
            )
        )
    return tuple(result)


def _validated_id(value: Any, prefix: str, known: set[str], label: str) -> str:
    if not isinstance(value, str) or not value.startswith(prefix) or not value[1:].isdigit() or value in known:
        raise ValueError(f"台帳内の{label}が不正または重複しています。")
    known.add(value)
    return value


def _replace_work(ledger: MediaLedger, replacement: LedgerWork) -> MediaLedger:
    return replace(ledger, works=tuple(replacement if work.work_id == replacement.work_id else work for work in ledger.works))


def _next_id(prefix: str, existing: Iterable[str]) -> str:
    highest = 0
    for value in existing:
        try:
            highest = max(highest, int(value.removeprefix(prefix)))
        except ValueError:
            continue
    return f"{prefix}{highest + 1:05d}"


def _attribute_as_dict(attribute: CatalogAttribute) -> dict[str, Any]:
    return attribute.as_dict()


def _atomic_write_json(path: Path, document: dict[str, Any]) -> None:
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.stem}.", suffix=".tmp", dir=path.parent, text=True)
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
    """Write a new JSON file without ever replacing a concurrent existing one."""
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
