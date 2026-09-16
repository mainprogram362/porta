"""Safe derived-column operations for transient record bundles."""

from __future__ import annotations

from typing import Literal

from records.record_bundle import RecordBundle, RecordBundleRow


RangeAnchor = Literal["start", "end"]


def add_extensionless_field(
    bundle: RecordBundle, source_field_index: int, field_name: str
) -> RecordBundle:
    """Append a field made by removing the final filename extension from each value."""
    return _append_field(
        bundle,
        source_field_index,
        field_name,
        _remove_final_extension,
    )


def add_range_removed_field(
    bundle: RecordBundle,
    source_field_index: int,
    field_name: str,
    *,
    start_anchor: RangeAnchor,
    start_position: int,
    end_anchor: RangeAnchor,
    end_position: int,
) -> RecordBundle:
    """Append a field with an inclusive character range removed from every value."""
    if start_position < 1 or end_position < 1:
        raise ValueError("削除する位置は1以上で指定してください。")

    def transform(value: str) -> str:
        if not value:
            return value
        start = _position_to_index(value, start_anchor, start_position)
        end = _position_to_index(value, end_anchor, end_position)
        if start > end:
            raise ValueError("削除範囲の開始位置が終了位置より後になっています。")
        return value[:start] + value[end + 1 :]

    return _append_field(bundle, source_field_index, field_name, transform)


def add_inserted_field(
    bundle: RecordBundle,
    source_field_index: int,
    field_name: str,
    *,
    text: str,
    position: Literal["start", "end", "index"],
    index: int = 1,
) -> RecordBundle:
    """Append a field with text inserted at the beginning, end, or a character position."""
    if position == "start":
        return _append_field(bundle, source_field_index, field_name, lambda value: text + value)
    if position == "end":
        return _append_field(bundle, source_field_index, field_name, lambda value: value + text)
    if index < 1:
        raise ValueError("挿入位置は1以上で指定してください。")

    def transform(value: str) -> str:
        if index > len(value) + 1:
            raise ValueError(f"挿入位置 {index} は文字数 {len(value)} の値には指定できません。")
        before = index - 1
        return value[:before] + text + value[before:]

    return _append_field(bundle, source_field_index, field_name, transform)


def _append_field(
    bundle: RecordBundle,
    source_field_index: int,
    field_name: str,
    transform,
) -> RecordBundle:
    if not 0 <= source_field_index < len(bundle.field_names):
        raise ValueError("複製元の項目が対応表にありません。")
    cleaned_name = field_name.strip()
    if not cleaned_name:
        raise ValueError("新しい項目名を入力してください。")
    if any(name.casefold() == cleaned_name.casefold() for name in bundle.field_names):
        raise ValueError(f"項目名「{cleaned_name}」はすでに使われています。")

    rows: list[RecordBundleRow] = []
    failures: list[str] = []
    for row_number, row in enumerate(bundle.rows, start=1):
        try:
            value = transform(row.values[source_field_index])
        except ValueError as exc:
            failures.append(f"レコード{row_number}「{row.values[source_field_index]}」: {exc}")
            continue
        rows.append(RecordBundleRow(row.identifier, (*row.values, value)))
    if failures:
        raise ValueError("項目を追加していません。\n" + "\n".join(failures))
    return RecordBundle(bundle.title, (*bundle.field_names, cleaned_name), tuple(rows))


def _remove_final_extension(value: str) -> str:
    """Remove only the final extension in the final path component, preserving dotfiles."""
    slash = max(value.rfind("/"), value.rfind("\\"))
    prefix, name = value[: slash + 1], value[slash + 1 :]
    dot = name.rfind(".")
    if dot <= 0:
        return value
    return prefix + name[:dot]


def _position_to_index(value: str, anchor: RangeAnchor, position: int) -> int:
    if position > len(value):
        raise ValueError(f"位置 {position} は文字数 {len(value)} の値には指定できません。")
    return position - 1 if anchor == "start" else len(value) - position
