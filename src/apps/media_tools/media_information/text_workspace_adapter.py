"""Adapt media candidates to the reusable text-editing workspace."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from gui import TextWorkspaceRow
from media.file_attributes import MediaItem, STANDARD_CATALOG_FIELDS

from .workflow import (
    candidate_display_name,
    single_field_patch_file_name,
    text_value_for_catalog_field,
)


def text_workspace_rows(items: Iterable[MediaItem]) -> tuple[TextWorkspaceRow, ...]:
    """Build workspace rows, rejecting filenames that cannot match uniquely."""
    rows: list[TextWorkspaceRow] = []
    names: set[str] = set()
    for index, item in enumerate(items, start=1):
        file_name = single_field_patch_file_name(item)
        if file_name in names:
            raise ValueError(
                f"同じファイル名の候補が複数あります: {file_name}\n"
                "項目更新パッチはファイル名だけで照合するため、対象を分けてください。"
            )
        names.add(file_name)
        source_values = {
            "__file_name_stem__": Path(file_name).stem,
            "__file_name_full__": file_name,
        }
        for field in STANDARD_CATALOG_FIELDS:
            if field.value_type == "text" and field.source_key is not None:
                source_values[field.key] = text_value_for_catalog_field(item, field.key)
        rows.append(
            TextWorkspaceRow(
                identifier=file_name,
                label=f"{index}. {candidate_display_name(item)}",
                source_values=source_values,
            )
        )
    if not rows:
        raise ValueError("項目更新パッチを作る候補がありません。")
    return tuple(rows)
