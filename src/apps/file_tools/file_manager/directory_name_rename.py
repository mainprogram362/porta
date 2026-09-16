"""Build safe filename changes using each file's immediate directory name."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
import re

from .rename_workflow import RenamePreview, build_mapped_rename_preview


DirectoryNameMode = Literal["insert", "replace"]
InsertPosition = Literal["start", "end", "position"]
_INSERT_TOKEN = "[@insert]"
_TOKEN_PATTERN = re.compile(r"\[@[^\]]+\]")


@dataclass(frozen=True)
class DirectoryNameRule:
    """One readable transformation driven by every source file's parent name."""

    mode: DirectoryNameMode
    template: str
    insert_position: InsertPosition = "end"
    position: int = 1
    preserve_extension: bool = True


def build_directory_name_rename_preview(
    paths: Iterable[str | Path],
    rule: DirectoryNameRule,
) -> RenamePreview:
    """Render parent-directory names and use the common collision-safe rename plan."""
    sources = tuple(Path(path) for path in paths)
    if not sources:
        return RenamePreview(None, "実行できません。チェック済みのファイルがありません。")
    try:
        rendered_template = _validate_template(rule.template)
        mappings: list[tuple[Path, str]] = []
        for source in sources:
            if not source.is_file():
                raise ValueError(f"通常のファイルだけを対象にしてください: {source}")
            if not source.parent.name:
                raise ValueError(f"親フォルダ名を取得できません: {source}")
            inserted = rendered_template.replace(_INSERT_TOKEN, source.parent.name)
            current_name = source.stem if rule.preserve_extension else source.name
            if rule.mode == "replace":
                output_name = inserted
            elif rule.mode == "insert":
                output_name = _insert_into_name(current_name, inserted, rule)
            else:
                raise ValueError("未対応の操作です。")
            mappings.append((source, output_name))
    except ValueError as exc:
        return RenamePreview(None, "実行できません。\n" + str(exc))

    return build_mapped_rename_preview(
        mappings,
        include_extension=not rule.preserve_extension,
    )


def _validate_template(template: str) -> str:
    value = template
    if not value:
        raise ValueError(f"装飾を入力してください。親フォルダ名は {_INSERT_TOKEN} で指定します。")
    unknown = set(_TOKEN_PATTERN.findall(value)) - {_INSERT_TOKEN}
    if unknown:
        raise ValueError("使える差し込み記号は [@insert] だけです。")
    if _INSERT_TOKEN not in value:
        raise ValueError("親フォルダ名を入れる位置に [@insert] を含めてください。")
    return value


def _insert_into_name(name: str, inserted: str, rule: DirectoryNameRule) -> str:
    if rule.insert_position == "start":
        return inserted + name
    if rule.insert_position == "end":
        return name + inserted
    if rule.insert_position != "position":
        raise ValueError("未対応の挿入位置です。")
    if not 1 <= rule.position <= len(name) + 1:
        raise ValueError(f"挿入位置 {rule.position} は名前「{name}」に指定できません。")
    offset = rule.position - 1
    return name[:offset] + inserted + name[offset:]
