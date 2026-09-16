"""Small, atomic edits for the values of one correspondence-table field."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
import unicodedata
from typing import Callable, Literal

from records.record_bundle import RecordBundle, RecordBundleRow


SortMode = Literal["text", "number", "length"]


def sort_bundle_rows(
    bundle: RecordBundle,
    field_index: int,
    *,
    mode: SortMode = "text",
    descending: bool = False,
) -> RecordBundle:
    """Order complete rows by one field, preserving every row identity."""
    _validate_field_index(bundle, field_index)
    keys: dict[str, tuple[object, ...]] = {}
    for row in bundle.rows:
        value = row.values[field_index]
        if mode == "text":
            keys[row.identifier] = (_normalized_text(value),)
        elif mode == "length":
            keys[row.identifier] = (len(value), _normalized_text(value))
        elif mode == "number":
            keys[row.identifier] = _numeric_key(value)
        else:
            raise ValueError("並べ替え方法が不正です。")
    rows = tuple(sorted(bundle.rows, key=lambda row: keys[row.identifier], reverse=descending))
    return RecordBundle(bundle.title, bundle.field_names, rows)


def remove_characters_from_field(
    bundle: RecordBundle, field_index: int, *, position: int, count: int
) -> RecordBundle:
    """Remove up to *count* characters starting at the one-based position.

    Values shorter than the chosen position stay as they are.  This makes one
    batch operation useful for fields whose values are not all the same length.
    """
    _validate_field_index(bundle, field_index)
    if position < 1 or count < 1:
        raise ValueError("文字位置と削除文字数は1以上で指定してください。")
    start = position - 1

    def transform(value: str) -> str:
        return value[:start] + value[start + count :] if start < len(value) else value

    return _transform_field(bundle, field_index, transform)


def insert_text_into_field(
    bundle: RecordBundle, field_index: int, *, position: int, text: str
) -> RecordBundle:
    """Insert text before the one-based position in every value.

    A position beyond a shorter value appends the text to that value, which is
    the only non-destructive interpretation for a mixed-length column.
    """
    _validate_field_index(bundle, field_index)
    if position < 1:
        raise ValueError("挿入位置は1以上で指定してください。")
    if not text:
        raise ValueError("挿入する文字列を入力してください。")
    index = position - 1
    return _transform_field(
        bundle,
        field_index,
        lambda value: value[: min(index, len(value))] + text + value[min(index, len(value)) :],
    )


def _transform_field(
    bundle: RecordBundle, field_index: int, transform: Callable[[str], str]
) -> RecordBundle:
    rows = tuple(
        RecordBundleRow(
            row.identifier,
            tuple(transform(value) if index == field_index else value for index, value in enumerate(row.values)),
        )
        for row in bundle.rows
    )
    return RecordBundle(bundle.title, bundle.field_names, rows)


def _validate_field_index(bundle: RecordBundle, field_index: int) -> None:
    if not 0 <= field_index < len(bundle.field_names):
        raise ValueError("操作する項目を選択してください。")


def _normalized_text(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold()


def _numeric_key(value: str) -> tuple[int, Decimal, str]:
    try:
        number = Decimal(unicodedata.normalize("NFKC", value).strip().replace(",", ""))
    except InvalidOperation:
        # Keep non-numeric values together after numeric values in ascending order.
        return (1, Decimal(0), _normalized_text(value))
    return (0, number, _normalized_text(value))
