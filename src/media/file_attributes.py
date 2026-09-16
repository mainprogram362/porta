"""Read-only local file inspection expressed as typed attribute pairs.

The generic minimum unit is always ``key + value``. Frequently queried
values may additionally use a small structured value, such as a resolution
containing separate width and height numbers. This module never modifies a
file; callers may convert stable observations into an explicit catalog update.
"""

from __future__ import annotations

from runtime import managed_process

import json
import mimetypes
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from foundation.path import normalize_path

from .catalog import CatalogAttribute, CatalogRecordInput


_MEDIA_SUFFIXES = {
    ".3gp", ".aac", ".avi", ".flac", ".m4a", ".mkv", ".mov", ".mp3", ".mp4",
    ".mpeg", ".mpg", ".ogg", ".opus", ".wav", ".webm", ".wmv",
}


@dataclass(frozen=True)
class CatalogField:
    """One always-present, portable field in a locally inspected record."""

    key: str
    label: str
    value_type: str
    source_key: str | None
    missing_source: str
    allows_multiple: bool = False


# These fields make the local-file preview predictable even when a folder,
# text file, image or damaged video is mixed into the same selection. A value
# that cannot be read is shown as an empty display cell and omitted from the
# durable JSON payload.
STANDARD_CATALOG_FIELDS: tuple[CatalogField, ...] = (
    CatalogField("file.name.observed", "ファイル名（観測）", "text", "file.name", "filesystem", True),
    CatalogField("file.extension.observed", "拡張子（観測）", "text", "file.extension", "filesystem", True),
    CatalogField("file.size_bytes.observed", "サイズ（観測）", "number", "file.size_bytes", "filesystem"),
    CatalogField("file.mime_type.observed", "MIMEタイプ（観測）", "text", "file.mime_type", "filesystem", True),
    CatalogField("media.duration_seconds", "長さ（秒）", "duration_seconds", "media.duration_seconds", "file_metadata"),
    CatalogField("video.resolution", "映像解像度", "resolution", "video.resolution", "file_metadata"),
    CatalogField("video.codec", "映像コーデック", "text", "video.codec", "file_metadata"),
    CatalogField("audio.codec", "音声コーデック", "text", "audio.codec", "file_metadata"),
    CatalogField("title.official", "正式タイトル", "text", "title.official", "user"),
    # Keep the first value as the main known name and append other public
    # names, former names or co-posters without flattening them into one text.
    CatalogField("source.uploader", "投稿者・名義", "text", "source.uploader", "user", True),
    CatalogField("group.name", "グループ名", "text", "group.name", "user"),
    CatalogField("classification.category_tree", "カテゴリツリー", "text", "classification.category_tree", "user"),
    CatalogField("classification.tag", "タグ", "text", "classification.tag", "user", True),
    CatalogField("series.name", "シリーズ名", "text", "series.name", "user"),
    CatalogField("series.episode_number", "話数・番号", "text", "series.episode_number", "user"),
    CatalogField("identity.public_identifier", "識別子・公のファイル名", "text", "identity.public_identifier", "user", True),
    CatalogField("review.score", "評価", "number", "review.score", "user"),
    CatalogField("review.community_score", "口コミ評価", "number", "review.community_score", "user"),
    CatalogField("review.community_recommendation", "口コミのおすすめ", "text", "review.community_recommendation", "user"),
    CatalogField("review.community_note", "口コミメモ", "text", "review.community_note", "user"),
    CatalogField("collection.status", "収集状態", "text", "collection.status", "user"),
    CatalogField("storage.status", "保管状況", "text", "storage.status", "user", True),
    CatalogField("storage.id", "保管id", "text", "storage.id", "user", True),
    CatalogField("media.highlights", "見どころ", "highlights", "media.highlights", "user"),
)

# This controls only the preferred editing view. Every standard field stays
# available in the complete view; only values that exist are emitted to JSON.
FREQUENT_CATALOG_FIELD_KEYS = frozenset(
    {
        "classification.category_tree",
        "review.score",
        "series.name",
        "classification.tag",
        "title.official",
        "source.uploader",
        "group.name",
        "identity.public_identifier",
        "series.episode_number",
        "collection.status",
        "storage.status",
        "storage.id",
        "media.highlights",
    }
)

COLLECTION_STATUS_PRESETS = (
    "保有",
    "保有（低画質のみ）",
    "未保有",
    "対象外",
)

# Storage facts can coexist, so the editor offers these as appendable choices.
# Free input remains available for a storage method not represented here.
STORAGE_STATUS_PRESETS = (
    "ローカル保存",
    "DVD保存済み",
    "クラウド保存済み",
    "原本保持",
    "再エンコード版のみ",
)


def preset_values_for_catalog_field(key: str) -> tuple[tuple[str, str], ...]:
    """Return fixed, human-readable choices for a field when they exist."""
    if key == "collection.status":
        return tuple((status, status) for status in COLLECTION_STATUS_PRESETS)
    if key == "storage.status":
        return tuple((status, status) for status in STORAGE_STATUS_PRESETS)
    if key == "review.score":
        return tuple((f"{score}/10", f"{score / 10:.1f}") for score in range(11))
    if key == "review.community_recommendation":
        return (("おすすめ", "おすすめ"), ("非推奨", "非推奨"))
    return ()


def parse_catalog_field_value(key: str, value: str) -> Any:
    """Validate and normalize one editable standard catalog value.

    This is shared by the extraction workbench and the ID-free parts editor so
    choices such as collection status and typed values such as resolution do
    not acquire subtly different rules in each screen.
    """
    return _parse_catalog_edit_value(catalog_field_for_key(key), value)

_STANDARD_CATALOG_KEYS = frozenset(field.key for field in STANDARD_CATALOG_FIELDS)
_SESSION_ONLY_KEYS = frozenset({"file.path", "file.exists", "file.kind", "file.modified_at"})

# A folder is a collection, not one media stream. Its unavailable standard
# fields stay blank in the editing view and are not emitted, and must never be
# edited as though the collection had one representative video value.
_FOLDER_UNEDITABLE_REASONS = {
    "file.name.observed": "フォルダ名は観測情報です。作品名を付けるなら「正式タイトル」を使ってください。",
    "file.extension.observed": "フォルダには一つの拡張子がありません。",
    "file.size_bytes.observed": "フォルダのサイズは配下の合計として自動集計される観測情報です。",
    "file.mime_type.observed": "フォルダには一つのMIMEタイプがありません。",
    "media.duration_seconds": "フォルダ内の動画には長さのばらつきがあるため、フォルダ全体の長さは扱いません。",
    "video.resolution": "フォルダ内の動画には解像度のばらつきがあるため、フォルダ全体の解像度は扱いません。",
    "video.codec": "フォルダ内の動画にはコーデックのばらつきがあるため、フォルダ全体のコーデックは扱いません。",
    "audio.codec": "フォルダ内の動画には音声コーデックのばらつきがあるため、フォルダ全体の音声コーデックは扱いません。",
}


def catalog_field_for_key(key: str) -> CatalogField | None:
    """Return the standard field definition, if a key has one."""
    return next((field for field in STANDARD_CATALOG_FIELDS if field.key == key), None)


def folder_field_uneditable_reason(item: "MediaItem", key: str) -> str | None:
    """Return why a standard field cannot be edited for one folder record."""
    if item.origin == "manual_placeholder":
        if key in {
            "file.extension.observed",
            "file.size_bytes.observed",
            "file.mime_type.observed",
            "media.duration_seconds",
            "video.resolution",
            "video.codec",
            "audio.codec",
        }:
            return "仮登録には実ファイルがないため、この観測項目は編集できません。"
        return None
    kind = item.attribute_map.get("file.kind")
    if kind is None or kind.value != "directory":
        return None
    if key in _FOLDER_UNEDITABLE_REASONS:
        return _FOLDER_UNEDITABLE_REASONS[key]
    if key == "record.kind" or key.startswith("folder."):
        return "フォルダ走査で得た自動集計情報です。内容を変えるには対象を確定し直してください。"
    return None


@dataclass(frozen=True)
class MediaAttribute:
    """One inspectable fact, with a stable key and a display-safe value."""

    key: str
    value: Any
    value_type: str
    source: str
    display: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "value": self.value,
            "value_type": self.value_type,
            "source": self.source,
        }


@dataclass(frozen=True)
class MediaItem:
    """A local observation or a deliberately pathless manual placeholder."""

    path: Path | None
    attributes: tuple[MediaAttribute, ...]
    origin: str = "filesystem"
    session_token: str = ""

    @property
    def attribute_map(self) -> dict[str, MediaAttribute]:
        """Return the first attribute for each key for simple summary views."""
        result: dict[str, MediaAttribute] = {}
        for attribute in self.attributes:
            result.setdefault(attribute.key, attribute)
        return result

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path) if self.path is not None else None,
            "origin": self.origin,
            "attributes": [attribute.as_dict() for attribute in self.attributes],
        }


def media_item_token(item: MediaItem) -> str:
    """Return a transient row identity without inventing a filesystem path."""
    if item.path is not None:
        return str(item.path)
    return item.session_token


def media_item_display_name(item: MediaItem) -> str:
    """Return a human-facing name without treating a JSON part as a path."""
    if item.path is not None:
        return item.path.name
    for key in ("file.name", "title.official", "identity.public_identifier"):
        attribute = item.attribute_map.get(key)
        if attribute is not None and attribute.value:
            values = attribute.value if isinstance(attribute.value, list) else [attribute.value]
            if values and str(values[0]).strip():
                return str(values[0]).strip()
    if item.origin == "manual_placeholder":
        return "名称未設定の仮登録"
    if item.origin == "parts_json":
        return "名称未設定のJSON候補"
    return "名称未設定"


def create_manual_placeholder_item(name: str, *, collection_status: str) -> MediaItem:
    """Create a pathless, user-created item that can join catalog edits.

    It deliberately has no synthetic ``Path``.  Its stable row token exists
    only during this open screen session and is never written to JSON.
    """
    title = name.strip()
    if not title:
        raise ValueError("仮登録名を入力してください。")
    status = collection_status.strip()
    if not status:
        raise ValueError("収集状態を選択してください。")
    return MediaItem(
        path=None,
        origin="manual_placeholder",
        session_token=f"manual-placeholder:{uuid4().hex}",
        attributes=(
            _text("file.name", title, source="manual_placeholder"),
            _text("record.kind", "manual_placeholder", source="manual_placeholder", display="仮登録"),
            _text("record.origin", "manual_placeholder", source="manual_placeholder", display="手入力"),
            _text("collection.status", status, source="user"),
        ),
    )


def media_item_from_catalog_attributes(
    attributes: tuple[CatalogAttribute, ...] | list[CatalogAttribute],
    *,
    origin: str = "parts_json",
    session_token: str | None = None,
) -> MediaItem:
    """Adapt one pathless JSON candidate for the normal information editor.

    A parts JSON stores durable catalog keys (for example
    ``file.name.observed``), while the editor internally keeps observation
    keys (``file.name``).  This adapter deliberately performs only that key
    translation.  It never invents a filesystem path, existence state or
    filename, so incomplete JSON stays visibly incomplete rather than being
    mistaken for a local file.
    """
    grouped: dict[str, list[CatalogAttribute]] = {}
    for attribute in attributes:
        if not isinstance(attribute, CatalogAttribute) or not attribute.key.strip():
            continue
        field = catalog_field_for_key(attribute.key)
        storage_key = field.source_key if field is not None and field.source_key else attribute.key
        grouped.setdefault(storage_key, []).append(attribute)

    adapted: list[MediaAttribute] = []
    for storage_key, entries in grouped.items():
        field = catalog_field_for_key(entries[0].key)
        source = entries[0].source or "parts_json"
        value_type = entries[0].value_type or (field.value_type if field is not None else "text")
        values = [entry.value for entry in entries if entry.value is not None]
        if not values:
            value: Any = None
        elif field is not None and field.allows_multiple:
            distinct_values = _distinct_catalog_values(values)
            # Keep ordinary one-name JSON candidates visually and structurally
            # equivalent to a freshly inspected local file.  A list appears
            # only when the durable JSON really carries multiple values.
            value = distinct_values[0] if len(distinct_values) == 1 else distinct_values
            if isinstance(value, list) and all(isinstance(item, str) for item in value):
                value_type = "text_list"
        elif len(values) == 1:
            value = values[0]
        else:
            # Non-standard repeated keys remain a list rather than losing a
            # value. Standard scalar fields are intentionally left as their
            # first value; conservative JSON merging will flag disagreement.
            value = values[0] if field is not None else _distinct_catalog_values(values)
        adapted.append(
            MediaAttribute(
                storage_key,
                value,
                value_type,
                source,
                _display_value(value, value_type),
            )
        )

    # A saved folder collection has no live folder path, but its observation
    # fields must retain the same edit restriction as a freshly scanned folder.
    record_kind = next((attribute.value for attribute in adapted if attribute.key == "record.kind"), None)
    if record_kind == "folder_collection":
        adapted.append(MediaAttribute("file.kind", "directory", "text", "parts_json", "フォルダ（記録）"))
    token = session_token or f"parts-json:{uuid4().hex}"
    return MediaItem(path=None, attributes=tuple(adapted), origin=origin, session_token=token)


def _distinct_catalog_values(values: list[Any]) -> list[Any]:
    """Keep stable JSON values distinct without forcing them into text."""
    result: list[Any] = []
    signatures: set[str] = set()
    for value in values:
        for candidate in value if isinstance(value, list) else [value]:
            signature = json.dumps(candidate, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            if signature not in signatures:
                signatures.add(signature)
                result.append(candidate)
    return result


@dataclass(frozen=True)
class FolderSummary:
    """Small recursive inventory kept as aggregate facts, never a manifest."""

    folder_count: int
    file_count: int
    media_file_count: int
    total_size_bytes: int
    unavailable_count: int


def _scan_folder_summary(path: Path) -> FolderSummary:
    """Safely count a folder tree without following links or retaining child paths."""
    folder_count = 0
    file_count = 0
    media_file_count = 0
    total_size_bytes = 0
    unavailable_count = 0
    pending = [path]

    while pending:
        directory = pending.pop()
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    try:
                        # Following a symlink could escape the selected tree or
                        # create a cycle, so it is deliberately not scanned.
                        if entry.is_symlink():
                            unavailable_count += 1
                        elif entry.is_dir(follow_symlinks=False):
                            folder_count += 1
                            pending.append(Path(entry.path))
                        elif entry.is_file(follow_symlinks=False):
                            file_count += 1
                            total_size_bytes += entry.stat(follow_symlinks=False).st_size
                            if Path(entry.name).suffix.lower() in _MEDIA_SUFFIXES:
                                media_file_count += 1
                        else:
                            unavailable_count += 1
                    except OSError:
                        unavailable_count += 1
        except OSError:
            unavailable_count += 1

    return FolderSummary(
        folder_count=folder_count,
        file_count=file_count,
        media_file_count=media_file_count,
        total_size_bytes=total_size_bytes,
        unavailable_count=unavailable_count,
    )


def inspect_media_path(value: str | Path) -> MediaItem:
    """Read basic and available media information without changing the path."""
    path = normalize_path(value)
    attributes: list[MediaAttribute] = [
        _text("file.path", str(path), source="filesystem"),
        _text("file.name", path.name, source="filesystem"),
    ]
    if not path.exists():
        attributes.extend(
            (
                _text("file.kind", "missing", source="filesystem", display="存在しない"),
                _boolean("file.exists", False, source="filesystem"),
            )
        )
        return MediaItem(path=path, attributes=tuple(attributes))

    try:
        stat = path.stat()
    except OSError:
        attributes.extend(
            (
                _text("file.kind", "unavailable", source="filesystem", display="確認不可"),
                _boolean("file.exists", True, source="filesystem"),
            )
        )
        return MediaItem(path=path, attributes=tuple(attributes))

    if path.is_dir():
        attributes.append(_text("file.kind", "directory", source="filesystem", display="フォルダ"))
        folder_summary = _scan_folder_summary(path)
        attributes.extend(
            (
                _boolean("file.exists", True, source="filesystem"),
                _number(
                    "file.size_bytes",
                    folder_summary.total_size_bytes,
                    source="filesystem",
                    display=_size_text(folder_summary.total_size_bytes),
                ),
                _text(
                    "file.modified_at",
                    datetime.fromtimestamp(stat.st_mtime).astimezone().isoformat(timespec="seconds"),
                    source="filesystem",
                ),
                _text("record.kind", "folder_collection", source="filesystem", display="フォルダのまとまり"),
                _number(
                    "folder.descendant_folder_count",
                    folder_summary.folder_count,
                    source="filesystem",
                    display=str(folder_summary.folder_count),
                ),
                _number(
                    "folder.descendant_file_count",
                    folder_summary.file_count,
                    source="filesystem",
                    display=str(folder_summary.file_count),
                ),
                _number(
                    "folder.media_file_count",
                    folder_summary.media_file_count,
                    source="filesystem",
                    display=str(folder_summary.media_file_count),
                ),
                _number(
                    "folder.total_size_bytes",
                    folder_summary.total_size_bytes,
                    source="filesystem",
                    display=_size_text(folder_summary.total_size_bytes),
                ),
                _number(
                    "folder.unavailable_count",
                    folder_summary.unavailable_count,
                    source="filesystem",
                    display=str(folder_summary.unavailable_count),
                ),
                _text("folder.scan_scope", "recursive_without_symlinks", source="filesystem"),
                MediaAttribute(
                    "folder.uneditable_field_reasons",
                    dict(_FOLDER_UNEDITABLE_REASONS),
                    "field_reason_map",
                    "schema",
                    f"{len(_FOLDER_UNEDITABLE_REASONS)}項目",
                ),
            )
        )
    elif path.is_file():
        attributes.append(_text("file.kind", "file", source="filesystem", display="ファイル"))
    else:
        attributes.append(_text("file.kind", "other", source="filesystem", display="その他"))
    if not path.is_dir():
        attributes.extend(
            (
                _boolean("file.exists", True, source="filesystem"),
                _text("file.extension", path.suffix.lower(), source="filesystem"),
                _number("file.size_bytes", stat.st_size, source="filesystem", display=_size_text(stat.st_size)),
                _text(
                    "file.modified_at",
                    datetime.fromtimestamp(stat.st_mtime).astimezone().isoformat(timespec="seconds"),
                    source="filesystem",
                ),
            )
        )
    mime_type, _encoding = mimetypes.guess_type(path.name)
    if mime_type and not path.is_dir():
        attributes.append(_text("file.mime_type", mime_type, source="filesystem"))
    if path.is_file() and path.suffix.lower() in _MEDIA_SUFFIXES:
        attributes.extend(_probe_media_attributes(path))
    return MediaItem(path=path, attributes=tuple(attributes))


def add_user_attribute(item: MediaItem, key: str, value: str) -> MediaItem:
    """Return an in-memory item with one user-owned free-text attribute."""
    normalized_key = key.strip()
    normalized_value = value.strip()
    if not normalized_key or not normalized_value:
        raise ValueError("項目名と値の両方を入力してください。")
    return replace(
        item,
        attributes=item.attributes + (_text(normalized_key, normalized_value, source="user"),),
    )


def append_media_highlight(item: MediaItem, *, time: str, comment: str = "") -> MediaItem:
    """Add one structured highlight without deriving a tag from its comment."""
    highlight = _normalized_highlight({"time": time, "comment": comment})
    existing = item.attribute_map.get("media.highlights")
    values = _normalized_highlights(existing.value if existing is not None else [])
    if highlight not in values:
        values.append(highlight)
    return _replace_media_highlights(item, values)



def append_mpv_highlight_replacing_overlaps(
    item: MediaItem, *, time: str, comment: str = ""
) -> tuple[MediaItem, int]:
    """Add an mpv highlight and replace prior highlights occupying its time.

    A point represents its displayed second and the following second.  Thus a
    one-second correction replaces the prior point, while ranges use their
    inclusive displayed bounds.  This helper is deliberately for transient
    mpv input only; normal catalog editing never removes highlights implicitly.
    """
    highlight = _normalized_highlight({"time": time, "comment": comment})
    beginning, end = _highlight_interval_seconds(highlight["time"])
    existing = item.attribute_map.get("media.highlights")
    values = _normalized_highlights(existing.value if existing is not None else [])
    retained = [
        value
        for value in values
        if not _highlight_intervals_overlap(
            beginning, end, *_highlight_interval_seconds(value["time"])
        )
    ]
    removed = len(values) - len(retained)
    if highlight not in retained:
        retained.append(highlight)
    return _replace_media_highlights(item, retained), removed



def _highlight_interval_seconds(value: str) -> tuple[float, float]:
    """Return an inclusive interval for one validated highlight time."""
    start_text, separator, end_text = value.partition("-")
    start = _highlight_timestamp_seconds(start_text)
    if not separator:
        return start, start + 1
    return start, _highlight_timestamp_seconds(end_text)



def _highlight_timestamp_seconds(value: str) -> float:
    parts = value.split(":")
    if len(parts) == 2:
        minutes, seconds = parts
        return int(minutes) * 60 + float(seconds)
    hours, minutes, seconds = parts
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def _highlight_intervals_overlap(
    first_start: float, first_end: float, second_start: float, second_end: float
) -> bool:
    return max(first_start, second_start) <= min(first_end, second_end)

def replace_media_highlights(item: MediaItem, value: Any) -> MediaItem:
    """Replace structured highlights after validating their portable JSON form."""
    return _replace_media_highlights(item, _normalized_highlights(value))


def normalize_media_highlights(value: Any) -> list[dict[str, str]]:
    """Validate the durable list of `{time, comment}` highlight objects."""
    return _normalized_highlights(value)


def _replace_media_highlights(item: MediaItem, values: list[dict[str, str]]) -> MediaItem:
    updated = MediaAttribute(
        "media.highlights", values or None, "highlights", "user", _display_value(values, "highlights")
    )
    return _replace_item_attribute(item, "media.highlights", updated)


def _normalized_highlights(value: Any) -> list[dict[str, str]]:
    raw_values = value if isinstance(value, list) else [value]
    result: list[dict[str, str]] = []
    for raw in raw_values:
        highlight = _normalized_highlight(raw)
        if highlight not in result:
            result.append(highlight)
    return result


def _normalized_highlight(value: Any) -> dict[str, str]:
    if not isinstance(value, dict) or set(value) != {"time", "comment"}:
        raise ValueError("見どころは time と comment を持つオブジェクトにしてください。")
    time = value["time"]
    comment = value["comment"]
    if not isinstance(time, str) or not isinstance(comment, str):
        raise ValueError("見どころの time と comment は文字列にしてください。")
    normalized_time = _parse_catalog_edit_value(
        CatalogField("media.highlights", "見どころ", "time_range", None, "user"), time
    )
    if "\n" in comment or "\r" in comment:
        raise ValueError("見どころコメントは1行で入力してください。")
    return {"time": normalized_time, "comment": comment.strip()}


def apply_catalog_field_operation(
    item: MediaItem,
    *,
    key: str,
    operation: str,
    value: str = "",
) -> MediaItem:
    """Apply one in-memory catalog edit without touching the real file.

    Standard fields have deliberate scalar/list rules. Custom fields default
    to text lists when values are appended, so delimiters never become part of
    a hidden file format convention.
    """
    normalized_key = key.strip()
    if not normalized_key:
        raise ValueError("対象項目を選択してください。")
    field = catalog_field_for_key(normalized_key)
    reason = folder_field_uneditable_reason(item, normalized_key)
    if reason is not None:
        raise ValueError(reason)
    storage_key = field.source_key if field and field.source_key else normalized_key
    existing = item.attribute_map.get(storage_key)

    if operation == "remove":
        if field is not None:
            raise ValueError("基本項目そのものは削除できません。値を空にするか、JSON出力を外してください。")
        return _replace_item_attribute(item, storage_key, None)
    if operation not in {"replace", "append", "clear"}:
        raise ValueError("未対応の共通操作です。")
    if operation == "append" and field is not None and not field.allows_multiple:
        raise ValueError(f"{field.label} は値を一つだけ持つ項目です。上書きを使ってください。")

    if operation == "clear":
        value_type = "text_list" if field and field.allows_multiple else (field.value_type if field else "text")
        updated = MediaAttribute(storage_key, None, value_type, "user", "")
        return _replace_item_attribute(item, storage_key, updated)

    if field is not None and field.value_type == "highlights":
        raise ValueError("見どころはmpvの [ と ] で時間とコメントをセットで追加してください。")

    parsed = _parse_catalog_edit_value(field, value)
    if operation == "append":
        values = _as_distinct_text_list(existing.value if existing is not None else None)
        if parsed not in values:
            values.append(parsed)
        updated = MediaAttribute(storage_key, values, "text_list", "user", " / ".join(values))
        return _replace_item_attribute(item, storage_key, updated)

    if field is not None and field.allows_multiple:
        values = _as_distinct_text_list(parsed)
        updated = MediaAttribute(storage_key, values, "text_list", "user", " / ".join(values))
    else:
        updated = MediaAttribute(
            storage_key,
            parsed,
            field.value_type if field is not None else "text",
            "user",
            _display_value(parsed, field.value_type if field is not None else "text"),
        )
    return _replace_item_attribute(item, storage_key, updated)


def _replace_item_attribute(
    item: MediaItem, storage_key: str, updated: MediaAttribute | None
) -> MediaItem:
    attributes = list(item.attributes)
    indexes = [index for index, attribute in enumerate(attributes) if attribute.key == storage_key]
    if indexes:
        first = indexes[0]
        if updated is None:
            attributes = [attribute for attribute in attributes if attribute.key != storage_key]
        else:
            attributes[first] = updated
            for index in reversed(indexes[1:]):
                del attributes[index]
    elif updated is not None:
        attributes.append(updated)
    return replace(item, attributes=tuple(attributes))


def _parse_catalog_edit_value(field: CatalogField | None, value: str) -> Any:
    text = value.strip()
    if not text:
        raise ValueError("上書き・追加する値を入力してください。空にしたい場合は「値を空にする」を使ってください。")
    if field is None or field.value_type == "text":
        if field is not None and field.key == "classification.category_tree":
            return _normalize_category_tree(text)
        return text
    if field.value_type == "number":
        try:
            number = int(text) if text.isdigit() else float(text)
        except ValueError as exc:
            raise ValueError("数値として入力してください。") from exc
        if field.key in {"review.score", "review.community_score"} and not 0 <= number <= 1:
            raise ValueError(f"{field.label}は 0〜1 の数値で入力してください。")
        return number
    if field.value_type == "duration_seconds":
        try:
            seconds = float(text)
        except ValueError as exc:
            raise ValueError("長さは秒数の数値として入力してください。") from exc
        if seconds < 0:
            raise ValueError("長さは 0 以上にしてください。")
        return seconds
    if field.value_type == "resolution":
        # Display uses the multiplication sign.  Accept the common keyboard
        # alternatives too, so users do not have to enter a visually subtle x.
        match = re.fullmatch(r"\s*(\d+)\s*[x×*＊]\s*(\d+)\s*", text, flags=re.IGNORECASE)
        if match is None:
            raise ValueError("解像度は 1920×1080 の形式で入力してください。")
        return {"width": int(match.group(1)), "height": int(match.group(2))}
    if field.value_type == "time_range":
        # A highlight is either one position (13:23.417) or one inclusive
        # range (13:11.250-14:25.900). Hours remain optional.
        timestamp = r"(?:\d+:)?[0-5]\d:[0-5]\d(?:\.\d{1,3})?"
        if re.fullmatch(rf"{timestamp}(?:\s*-\s*{timestamp})?", text) is None:
            raise ValueError("見どころ時間は 13:23.417、または 13:11.250-14:25.900 の形式で入力してください。")
        return re.sub(r"\s*-\s*", "-", text)
    return text


def _normalize_category_tree(value: str) -> str:
    """Store one category path in root-to-leaf order without ambiguous gaps."""
    parts = [part.strip() for part in value.split("/")]
    if not parts or any(not part for part in parts):
        raise ValueError("カテゴリツリーは 大分類/中分類/小分類 のように、空の階層なしで入力してください。")
    return "/".join(parts)


def _as_distinct_text_list(value: Any) -> list[str]:
    raw_values = value if isinstance(value, list) else [value]
    result: list[str] = []
    for raw in raw_values:
        text = str(raw).strip() if raw is not None else ""
        if text and text not in result:
            result.append(text)
    return result


def catalog_attributes_from_media_item(
    item: MediaItem, *, excluded_keys: set[str] | frozenset[str] = frozenset()
) -> tuple[CatalogAttribute, ...]:
    """Return exactly the attributes that would be written to the catalog.

    The standard 15 fields are always represented unless the caller explicitly
    excludes a key. Unknown values remain ``None`` (JSON ``null``), rather
    than disappearing because the inspected item was not a video. User-added
    non-standard fields remain available as additional attributes.
    """
    observed = item.attribute_map
    attributes: list[CatalogAttribute] = []
    consumed_source_keys: set[str] = set()
    for field in STANDARD_CATALOG_FIELDS:
        if field.key in excluded_keys:
            continue
        source = observed.get(field.source_key) if field.source_key else None
        if source is not None:
            consumed_source_keys.add(source.key)
            attributes.append(CatalogAttribute(field.key, source.value, source.value_type, source.source))
        else:
            attributes.append(CatalogAttribute(field.key, None, field.value_type, "not_observed"))

    for attribute in item.attributes:
        if attribute.key in _SESSION_ONLY_KEYS or attribute.key in consumed_source_keys:
            continue
        if attribute.key in _STANDARD_CATALOG_KEYS or attribute.key in excluded_keys:
            continue
        attributes.append(
            CatalogAttribute(attribute.key, attribute.value, attribute.value_type, attribute.source)
        )
    return tuple(attributes)


def catalog_record_from_media_item(
    item: MediaItem, *, excluded_keys: set[str] | frozenset[str] = frozenset()
) -> CatalogRecordInput:
    """Convert an item into the exact, explicitly selected catalog payload.

    The current path, existence, type and modified time remain session-only.
    The editing view has every common field, while the portable record includes
    only fields whose values were actually observed or entered by the user.
    """
    attributes = catalog_attributes_from_media_item(item, excluded_keys=excluded_keys)
    return CatalogRecordInput(
        attributes=tuple(attribute for attribute in attributes if attribute.value is not None)
    )


def _display_highlight_time(value: str) -> str:
    """Keep list cells compact while JSON preserves millisecond precision."""
    return re.sub(r"\.\d{1,3}(?=$|-)", "", value)



def display_catalog_attribute(
    attribute: CatalogAttribute, *, precise_highlights: bool = False
) -> str:
    """Render a catalog value for the UI without changing what JSON receives."""
    if attribute.value is None:
        return ""
    if isinstance(attribute.value, list):
        if attribute.value_type == "highlights":
            return " / ".join(
                (
                    f"{str(value.get('time', ''))} {value.get('comment', '')}".strip()
                    if precise_highlights
                    else f"{_display_highlight_time(str(value.get('time', '')))} {value.get('comment', '')}".strip()
                )
                for value in attribute.value
                if isinstance(value, dict)
            )
        return " / ".join(str(value) for value in attribute.value)


    if attribute.value_type == "resolution" and isinstance(attribute.value, dict):
        width, height = attribute.value.get("width"), attribute.value.get("height")
        if isinstance(width, int) and isinstance(height, int):
            return f"{width}×{height}"
    if attribute.value_type == "duration_seconds":
        try:
            return _duration_text(float(attribute.value))
        except (TypeError, ValueError):
            pass
    if attribute.value_type == "number" and attribute.key == "file.size_bytes.observed":
        try:
            return _size_text(int(attribute.value))
        except (TypeError, ValueError):
            pass
    return str(attribute.value)


def _display_value(value: Any, value_type: str) -> str:
    return display_catalog_attribute(CatalogAttribute("", value, value_type, "user"))


def _probe_media_attributes(path: Path) -> tuple[MediaAttribute, ...]:
    ffprobe = shutil.which("ffprobe")
    if ffprobe is None:
        return ()
    try:
        process = managed_process.run(
            [
                ffprobe,
                "-v", "error",
                "-show_entries", "format=duration:stream=codec_type,codec_name,width,height",
                "-of", "json",
                str(path),
            ],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=15,
            label='メディア属性を確認中',
        )
        if process.returncode != 0:
            return ()
        info = json.loads(process.stdout)
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return ()
    if not isinstance(info, dict):
        return ()
    attributes: list[MediaAttribute] = []
    format_info = info.get("format")
    if isinstance(format_info, dict):
        try:
            duration = float(format_info.get("duration"))
        except (TypeError, ValueError):
            duration = None
        if duration is not None and duration >= 0:
            attributes.append(
                MediaAttribute(
                    "media.duration_seconds",
                    duration,
                    "duration_seconds",
                    "file_metadata",
                    _duration_text(duration),
                )
            )
    streams = info.get("streams")
    if not isinstance(streams, list):
        return tuple(attributes)
    for stream in streams:
        if not isinstance(stream, dict):
            continue
        codec_type = str(stream.get("codec_type") or "")
        codec_name = str(stream.get("codec_name") or "")
        if codec_type == "video":
            width, height = stream.get("width"), stream.get("height")
            if isinstance(width, int) and isinstance(height, int) and width > 0 and height > 0:
                attributes.append(
                    MediaAttribute(
                        "video.resolution",
                        {"width": width, "height": height},
                        "resolution",
                        "file_metadata",
                        f"{width}×{height}",
                    )
                )
            if codec_name:
                attributes.append(_text("video.codec", codec_name, source="file_metadata"))
            break
    for stream in streams:
        if isinstance(stream, dict) and str(stream.get("codec_type") or "") == "audio":
            codec_name = str(stream.get("codec_name") or "")
            if codec_name:
                attributes.append(_text("audio.codec", codec_name, source="file_metadata"))
            break
    return tuple(attributes)


def _text(key: str, value: str, *, source: str, display: str | None = None) -> MediaAttribute:
    return MediaAttribute(key, value, "text", source, display if display is not None else value)


def _number(key: str, value: int | float, *, source: str, display: str) -> MediaAttribute:
    return MediaAttribute(key, value, "number", source, display)


def _boolean(key: str, value: bool, *, source: str) -> MediaAttribute:
    return MediaAttribute(key, value, "boolean", source, "はい" if value else "いいえ")


def _size_text(value: int) -> str:
    units = ("B", "KB", "MB", "GB", "TB")
    size = float(value)
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{value} B"


def _duration_text(value: float) -> str:
    seconds = int(round(value))
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    return f"{hours}:{minutes:02}:{seconds:02}" if hours else f"{minutes}:{seconds:02}"
