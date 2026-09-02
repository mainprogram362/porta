"""In-memory planning and safe direct execution for batch renaming."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
import re

from foundation.path_inspection import inspect_paths
from foundation.path import path_entry_exists

from .copy_workflow import parse_target_paths

RuleKind = Literal[
    "prepend",
    "insert",
    "append",
    "remove_range",
    "trim_end",
    "remove_text",
    "replace",
]


@dataclass(frozen=True)
class RenameRule:
    """One transformation applied to a name stem in the displayed order."""

    kind: RuleKind
    text: str = ""
    replacement: str = ""
    first: int = 0
    second: int = 0
    use_regex: bool = False


@dataclass(frozen=True)
class PlannedRename:
    """One current source path and its exact planned new path."""

    source: Path
    output: Path


@dataclass(frozen=True)
class RenamePlan:
    """A non-persistent, validated direct-rename plan."""

    rules: tuple[RenameRule, ...]
    renames: tuple[PlannedRename, ...]
    include_extension: bool


@dataclass(frozen=True)
class RenamePreview:
    """Preview text and its executable plan when all rules are valid."""

    plan: RenamePlan | None
    text: str

    @property
    def is_ready(self) -> bool:
        return self.plan is not None


def build_rename_preview(
    source_text: str, rules: Iterable[RenameRule], *, include_extension: bool = False
) -> RenamePreview:
    """Build a complete rename preview without changing the filesystem."""
    sources = parse_target_paths(source_text)
    rule_list = tuple(rules)
    scope = "拡張子を含む名前全体" if include_extension else "拡張子を除く名前本体"
    header = (
        f"操作: リネーム\nチェック済み: {len(sources)} 件\n"
        f"対象範囲: {scope}\nルール: {len(rule_list)} 件\n\n"
    )
    try:
        plan = build_rename_plan(sources, rule_list, include_extension=include_extension)
    except (OSError, ValueError) as exc:
        return RenamePreview(None, header + "実行できません。次を確認してください。\n" + str(exc))

    details = "\n\n".join(
        f"{index}. 変更前:\n{rename.source}\n   変更後:\n{rename.output}"
        for index, rename in enumerate(plan.renames, start=1)
    )
    return RenamePreview(
        plan,
        header
        + "実行内容:\n"
        + "上から順にルールを適用して名前を変更します。\n"
        + "実行直前に対象と変更後の名前を再確認します。\n\n"
        + "詳細な変更予定:\n"
        + details,
    )


def build_rename_plan(
    sources: Iterable[Path], rules: Iterable[RenameRule], *, include_extension: bool = False
) -> RenamePlan:
    """Validate rules and calculate every output path before any rename begins."""
    source_paths = tuple(sources)
    rule_list = tuple(rules)
    if not source_paths:
        raise ValueError("チェック済み項目を1行に1件ずつ入力またはドロップしてください。")
    if not rule_list:
        raise ValueError("リネームルールを1件以上追加してください。")
    _validate_rules(rule_list)
    missing = [info.path for info in inspect_paths(source_paths) if not info.is_operable]
    if missing:
        raise FileNotFoundError("存在しないチェック済み項目があります。リネームは開始していません。")

    renames: list[PlannedRename] = []
    for source in source_paths:
        name, suffix = _renameable_name_parts(source, include_extension=include_extension)
        renamed = _apply_rules(name, rule_list)
        _validate_name(renamed)
        renames.append(PlannedRename(source=source, output=source.with_name(renamed + suffix)))

    _validate_outputs(tuple(renames))
    return RenamePlan(
        rules=rule_list,
        renames=tuple(renames),
        include_extension=include_extension,
    )


def execute_rename_plan(plan: RenamePlan) -> list[Path]:
    """Revalidate and directly rename every planned output, with best-effort rollback."""
    _validate_rename_plan_is_current(plan)
    completed: list[PlannedRename] = []
    try:
        for rename in plan.renames:
            if rename.source == rename.output:
                continue
            rename.source.rename(rename.output)
            completed.append(rename)
    except OSError:
        _rollback_renames(completed)
        raise OSError(
            "リネーム中にエラーが発生しました。今回変更した名前は元に戻すことを試みました。"
        ) from None
    return [rename.output for rename in plan.renames]


def _renameable_name_parts(source: Path, *, include_extension: bool) -> tuple[str, str]:
    """Use a file stem by default; folders and other paths use their whole name."""
    if source.is_file() and not include_extension:
        return source.stem, source.suffix
    return source.name, ""


def _apply_rules(name: str, rules: tuple[RenameRule, ...]) -> str:
    value = name
    for rule in rules:
        if rule.kind == "prepend":
            value = rule.text + value
        elif rule.kind == "append":
            value = value + rule.text
        elif rule.kind == "insert":
            position = rule.first
            if position < 1 or position > len(value) + 1:
                raise ValueError(f"挿入位置 {position} は名前「{value}」に指定できません。")
            value = value[: position - 1] + rule.text + value[position - 1 :]
        elif rule.kind == "remove_range":
            start, end = rule.first, rule.second
            if start < 1 or end < start or end > len(value):
                raise ValueError(f"削除範囲 {start}〜{end} は名前「{value}」に指定できません。")
            value = value[: start - 1] + value[end:]
        elif rule.kind == "trim_end":
            count = rule.first
            if count < 0 or count > len(value):
                raise ValueError(f"末尾から {count} 文字は名前「{value}」から削除できません。")
            value = value[:-count] if count else value
        elif rule.kind == "remove_text":
            value = _substitute_text(value, rule, "")
        elif rule.kind == "replace":
            value = _substitute_text(value, rule, rule.replacement)
        else:
            raise ValueError("未対応のリネームルールです。")
    return value


def _validate_rules(rules: tuple[RenameRule, ...]) -> None:
    """Reject malformed regex rules before examining or changing any path."""
    for index, rule in enumerate(rules, start=1):
        if rule.kind not in {"remove_text", "replace"}:
            if rule.use_regex:
                raise ValueError(f"{index}件目：正規表現を使えるのは文字列の削除・置換だけです。")
            continue
        if not rule.text:
            action = "削除" if rule.kind == "remove_text" else "置換"
            raise ValueError(f"{index}件目：{action}する文字列を入力してください。")
        if not rule.use_regex:
            continue
        try:
            pattern = re.compile(rule.text)
            if rule.kind == "replace":
                # Validate replacement references such as \1 even when no
                # current filename happens to match the pattern.
                pattern.sub(rule.replacement, "")
        except re.error as exc:
            raise ValueError(f"{index}件目：正規表現または置換指定が正しくありません: {exc}") from exc


def _substitute_text(value: str, rule: RenameRule, replacement: str) -> str:
    """Apply literal replacement by default, or an explicitly opted-in regex."""
    if not rule.use_regex:
        return value.replace(rule.text, replacement)
    try:
        return re.sub(rule.text, replacement, value)
    except re.error as exc:
        # Plans call _validate_rules first.  This guard also keeps direct use
        # of this internal function from ever leaking a raw regex exception.
        raise ValueError(f"正規表現または置換指定が正しくありません: {exc}") from exc


def _validate_name(name: str) -> None:
    if not name:
        raise ValueError("リネーム後の名前が空になります。")
    if "/" in name or "\x00" in name:
        raise ValueError("リネーム文字列に使用できない文字が含まれています。")
    if name in {".", ".."}:
        raise ValueError("リネーム後の名前に . または .. は使用できません。")


def _validate_outputs(renames: tuple[PlannedRename, ...]) -> None:
    outputs = [rename.output for rename in renames]
    if len(set(outputs)) != len(outputs):
        raise ValueError("リネーム後の名前が重複します。ルールを見直してください。")
    conflicts = [
        rename.output
        for rename in renames
        if rename.output != rename.source and path_entry_exists(rename.output)
    ]
    if conflicts:
        raise FileExistsError("リネーム後の名前がすでに存在します。リネームは開始していません。")


def _validate_rename_plan_is_current(plan: RenamePlan) -> None:
    sources = tuple(rename.source for rename in plan.renames)
    missing = [info.path for info in inspect_paths(sources) if not info.is_operable]
    if missing:
        raise FileNotFoundError("プレビュー後にチェック済み項目が変化したため、リネームを開始しません。")
    _validate_outputs(plan.renames)


def _rollback_renames(completed: list[PlannedRename]) -> None:
    """Best-effort reverse rename of only paths changed by this invocation."""
    for rename in reversed(completed):
        try:
            if path_entry_exists(rename.output) and not path_entry_exists(rename.source):
                rename.output.rename(rename.source)
        except OSError:
            continue
