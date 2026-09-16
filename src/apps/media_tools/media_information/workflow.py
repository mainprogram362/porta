"""UI-independent workflow state and transformations for Media Information."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from foundation.path import normalize_path
from media.file_attributes import (
    MediaItem,
    catalog_attributes_from_media_item,
    catalog_field_for_key,
    display_catalog_attribute,
    folder_field_uneditable_reason,
    inspect_media_path,
    media_item_display_name,
    media_item_from_catalog_attributes,
    media_item_token,
)
from media.ledger import MediaPart, load_parts


@dataclass(frozen=True)
class AppliedOperation:
    """One explicit in-memory edit made during a workbench session."""

    key: str
    operation: str
    value: str
    item_count: int
    scope: str
    item_name: str = ""


@dataclass(frozen=True)
class UndoState:
    """One in-memory state captured immediately before an edit."""

    items: tuple[MediaItem, ...]
    applied_operations: tuple[AppliedOperation, ...]
    summaries: tuple[str, ...]
    description: str


@dataclass(frozen=True)
class LoadedJsonSource:
    """One JSON explicitly included in the current locked input batch."""

    path: Path
    is_parts_json: bool


@dataclass(frozen=True)
class CandidateReadResult:
    """All candidate data derived from one explicit input batch."""

    items: tuple[MediaItem, ...]
    source_details: dict[str, tuple[str, str]]
    summary: str
    json_sources: tuple[LoadedJsonSource, ...]


def read_candidate_sources(paths: Iterable[Path]) -> CandidateReadResult:
    """Turn local paths and supported JSON into one pathless-safe candidate set."""
    items: list[MediaItem] = []
    details: dict[str, tuple[str, str]] = {}
    filesystem_count = 0
    json_file_count = 0
    json_part_count = 0
    json_sources: list[LoadedJsonSource] = []
    for path in paths:
        if path.suffix.casefold() != ".json":
            item = inspect_media_path(path)
            items.append(item)
            details[media_item_token(item)] = candidate_source_detail(item)
            filesystem_count += 1
            continue
        try:
            document = load_parts(path)
        except ValueError as exc:
            raise ValueError(f"{path.name}: {exc}") from exc
        json_file_count += 1
        json_sources.append(
            LoadedJsonSource(
                path=normalize_path(path),
                is_parts_json=not document.converted_from_extraction_catalog,
            )
        )
        json_kind = "抽出カタログJSON" if document.converted_from_extraction_catalog else "パーツJSON"
        for number, part in enumerate(document.parts, start=1):
            item = media_item_from_catalog_attributes(
                part.attributes,
                origin="parts_json",
                session_token=f"parts-json:{path}:{number}",
            )
            token = media_item_token(item)
            items.append(item)
            details[token] = (
                f"{path.name}・{number}件目",
                (
                    f"{json_kind}から読み込んだ候補\n"
                    f"入力元: {path}\n"
                    f"記録: {number}件目\n"
                    "実在パスはJSONに含まれていないため、開く・再生・親フォルダ操作は使えません。"
                ),
            )
            json_part_count += 1
    summary_parts: list[str] = []
    if filesystem_count:
        summary_parts.append(f"実ファイル・フォルダ {filesystem_count}件")
    if json_file_count:
        summary_parts.append(f"JSON {json_file_count}ファイルから候補 {json_part_count}件")
    return CandidateReadResult(
        items=tuple(items),
        source_details=details,
        summary=" / ".join(summary_parts),
        json_sources=tuple(json_sources),
    )


def candidate_source_detail(item: MediaItem) -> tuple[str, str]:
    """Describe where one candidate originated without inventing a path."""
    if item.path is not None:
        parent = item.path.parent
        return parent.name or str(parent), f"実在パス（確認用）\n{item.path}"
    if item.origin == "manual_placeholder":
        return "仮登録", "仮登録：実在パスなし。再生・解析・親フォルダ操作は使えません。"
    return "JSON候補", "JSONから読んだ候補：実在パスなし。"


def part_file_name_sets(parts: Iterable[MediaPart]) -> tuple[frozenset[str], ...]:
    """Return each part's portable filename identifiers."""
    return tuple(
        frozenset(
            str(value).strip()
            for attribute in part.attributes
            if attribute.key == "file.name.observed"
            for value in (attribute.value if isinstance(attribute.value, list) else [attribute.value])
            if isinstance(value, str) and value.strip()
        )
        for part in parts
    )


def overwrite_safety_report(
    *,
    output: Path,
    existing_names: tuple[frozenset[str], ...],
    current_names: tuple[frozenset[str], ...],
    existing_attribute_count: int,
    current_attribute_count: int,
    label: str,
) -> tuple[bool, str]:
    """Require every old candidate to match exactly one current candidate."""
    lines = [
        f"上書き対象: {output}",
        f"形式: {label}",
        f"候補数: 既存 {len(existing_names)} 件 → 今回 {len(current_names)} 件",
        f"値のある属性数: 既存 {existing_attribute_count} → 今回 {current_attribute_count}",
    ]
    if len(current_names) < len(existing_names):
        lines.extend(("", "上書き不可: 今回の候補数が既存より減っています。新規保存を使ってください。"))
        return False, "\n".join(lines)
    if any(not names for names in existing_names) or any(not names for names in current_names):
        lines.extend(("", "上書き不可: ファイル名を持たない候補があり、安全に照合できません。新規保存を使ってください。"))
        return False, "\n".join(lines)
    matches: list[int] = []
    for old_index, old_names in enumerate(existing_names, start=1):
        candidates = [
            current_index
            for current_index, current_set in enumerate(current_names, start=1)
            if old_names.intersection(current_set)
        ]
        if len(candidates) != 1:
            detail = "見つかりません" if not candidates else f"{len(candidates)}件に一致"
            lines.extend(("", f"上書き不可: 既存候補 {old_index} が {detail}。新規保存を使ってください。"))
            return False, "\n".join(lines)
        matches.append(candidates[0])
    if len(set(matches)) != len(matches):
        lines.extend(("", "上書き不可: 複数の既存候補が同じ今回候補へ一致します。新規保存を使ってください。"))
        return False, "\n".join(lines)
    lines.extend(
        (
            "",
            "安全照合: 既存の全候補が、今回の候補のちょうど1件へファイル名で一致しました。",
            "注意: この操作はJSON全体を今回の内容へ置き換えます。属性の変更・削除はそのまま反映されます。",
        )
    )
    return True, "\n".join(lines)



def catalog_value(item: MediaItem, key: str, *, precise_highlights: bool = False) -> str:
    """Return the display value of one output attribute, including blanks."""
    return next(
        (
            display_catalog_attribute(attribute, precise_highlights=precise_highlights)
            for attribute in catalog_attributes_from_media_item(item)
            if attribute.key == key
        ),
        "（項目なし）",
    )


def single_field_patch_file_name(item: MediaItem) -> str:
    """Return the one portable filename which can safely identify a candidate."""
    names: list[str] = []
    for attribute in catalog_attributes_from_media_item(item):
        if attribute.key != "file.name.observed":
            continue
        raw_values = attribute.value if isinstance(attribute.value, list) else [attribute.value]
        for raw in raw_values:
            if not isinstance(raw, str) or not raw.strip():
                continue
            name = raw.strip()
            if Path(name).name != name:
                continue
            if name not in names:
                names.append(name)
    if len(names) != 1:
        raise ValueError(
            f"{candidate_display_name(item)} は照合用のファイル名を1つに決められません。"
            "ファイル名が空・複数・パス形式の候補は項目更新パッチにできません。"
        )
    return names[0]


def text_value_for_catalog_field(item: MediaItem, field_key: str) -> str:
    """Expose an item's current text field as one generic-workspace source."""
    field = catalog_field_for_key(field_key)
    if field is None or field.source_key is None:
        return ""
    attribute = item.attribute_map.get(field.source_key)
    if attribute is None or attribute.value is None:
        return ""
    raw_values = attribute.value if isinstance(attribute.value, list) else [attribute.value]
    values: list[str] = []
    for raw in raw_values:
        text = str(raw).strip() if raw is not None else ""
        if text and text not in values:
            values.append(text)
    return " / ".join(values)


def candidate_display_name(item: MediaItem) -> str:
    """Keep candidate tables name-first and avoid fake paths for JSON rows."""
    name = media_item_display_name(item).strip()
    if name:
        return name
    if item.path is not None:
        return str(item.path)
    return "名称未設定のJSON候補"


def operation_label(operation: str) -> str:
    return {
        "replace": "値を上書き",
        "append": "値を追加",
        "clear": "値を空にする",
        "remove": "項目を削除",
        "patch": "評価・見どころを安全照合で移植",
        "single_field_patch": "項目更新パッチを安全照合で移植",
    }.get(operation, operation)


def operation_description(operation: AppliedOperation) -> str:
    text = operation_label(operation.operation)
    if operation.value and operation.operation in {"replace", "append"}:
        text += f"「{operation.value}」"
    return f"{text}（{operation.item_count}件へ反映）"


def mpv_time_text(seconds: float) -> str:
    """Format a player position in the existing portable highlight notation."""
    total = max(0, int(round(seconds)))
    hours, remainder = divmod(total, 3600)
    minutes, seconds_part = divmod(remainder, 60)
    return f"{hours}:{minutes:02d}:{seconds_part:02d}" if hours else f"{minutes:02d}:{seconds_part:02d}"



def mpv_precise_time_text(seconds: float) -> str:
    """Format an mpv position to milliseconds for durable highlight storage."""
    total_milliseconds = max(0, int(round(seconds * 1000)))
    hours, remainder = divmod(total_milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds_part, milliseconds = divmod(remainder, 1000)
    base = (
        f"{hours}:{minutes:02d}:{seconds_part:02d}"
        if hours
        else f"{minutes:02d}:{seconds_part:02d}"
    )
    return f"{base}.{milliseconds:03d}"


def mpv_highlight_time_text(beginning: float, end: float) -> str:
    """Store a zero-length mpv range as a point, otherwise as a range."""
    start_text = mpv_precise_time_text(beginning)
    end_text = mpv_precise_time_text(end)
    return start_text if start_text == end_text else f"{start_text}-{end_text}"

def editable_attribute_rows(item: MediaItem) -> tuple[tuple[str, str, str, str, str], ...]:
    """Build a stable, read-only table model for the detached details window."""
    rows: list[tuple[str, str, str, str, str]] = []
    for attribute in catalog_attributes_from_media_item(item):
        reason = folder_field_uneditable_reason(item, attribute.key)
        editable = f"編集不可: {reason}" if reason is not None else "編集可"
        rows.append(
            (
                attribute.key,
                display_catalog_attribute(attribute),
                attribute.value_type,
                attribute.source,
                editable,
            )
        )
    return tuple(rows)
