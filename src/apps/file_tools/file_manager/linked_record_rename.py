"""Rename rules whose variable values come from linked record rows."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
import re

from .record_linkage import PathRecordLink
from .rename_workflow import RenamePreview, build_mapped_rename_preview


LinkedRenameMode = Literal["replace", "prepend", "append", "regex"]
_TOKEN_PATTERN = re.compile(r"\[@(\d+)\]")


@dataclass(frozen=True)
class LinkedRenameRule:
    """One simple or advanced rule applied to every linked path."""

    mode: LinkedRenameMode
    template: str
    regex_pattern: str = ""
    preserve_extension: bool = True


def render_record_tokens(template: str, link: PathRecordLink) -> str:
    """Replace beginner-friendly ``[@N]`` tokens with one record's values."""
    invalid: set[int] = set()

    def replace(match: re.Match[str]) -> str:
        field_number = int(match.group(1))
        if not 1 <= field_number <= len(link.record_values):
            invalid.add(field_number)
            return match.group(0)
        return link.record_values[field_number - 1]

    rendered = _TOKEN_PATTERN.sub(replace, template)
    if invalid:
        numbers = "、".join(str(value) for value in sorted(invalid))
        raise ValueError(f"対応表にない項目番号です: {numbers}")
    return rendered


def build_linked_rename_preview(
    links: Iterable[PathRecordLink],
    rule: LinkedRenameRule,
) -> RenamePreview:
    """Render a record-aware rule and delegate collision checks to rename workflow."""
    link_list = tuple(links)
    if not link_list:
        return RenamePreview(None, "実行できません。紐づけ済みのパスがありません。")
    if rule.mode not in {"replace", "prepend", "append", "regex"}:
        return RenamePreview(None, "実行できません。未対応の名前変更方法です。")
    if not rule.template and rule.mode != "regex":
        return RenamePreview(None, "実行できません。追加または置換する内容を入力してください。")

    try:
        expression = re.compile(rule.regex_pattern) if rule.mode == "regex" else None
    except re.error as exc:
        return RenamePreview(None, f"実行できません。正規表現が不正です: {exc}")
    if rule.mode == "regex" and not rule.regex_pattern:
        return RenamePreview(None, "実行できません。検索する正規表現を入力してください。")

    mappings: list[tuple[Path, str]] = []
    try:
        for link in link_list:
            source = link.source
            keep_suffix = rule.preserve_extension and source.is_file()
            current_name = source.stem if keep_suffix else source.name
            rendered = render_record_tokens(rule.template, link)
            if rule.mode == "replace":
                output_name = rendered
            elif rule.mode == "prepend":
                output_name = rendered + current_name
            elif rule.mode == "append":
                output_name = current_name + rendered
            else:
                assert expression is not None
                output_name = expression.sub(rendered, current_name)
            mappings.append((source, output_name))
    except (ValueError, re.error) as exc:
        return RenamePreview(None, f"実行できません。ルールを確認してください。\n{exc}")

    return build_mapped_rename_preview(
        mappings,
        include_extension=not rule.preserve_extension,
    )
