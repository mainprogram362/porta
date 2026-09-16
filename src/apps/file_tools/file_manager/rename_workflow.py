"""In-memory planning and safe direct execution for batch renaming."""

from __future__ import annotations

from foundation.safe_transfer import rename_noreplace
from runtime.operation_progress import checkpoint, completed as report_completed

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
import os
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
    "remove_spaces",
    "normalize_width",
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
    width_letters: bool = True
    width_digits: bool = True
    width_symbols: bool = True


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
    if any(rule.kind == "normalize_width" for rule in rule_list):
        source_paths = _with_directory_descendants(source_paths)
    missing = [info.path for info in inspect_paths(source_paths) if not info.is_operable]
    if missing:
        raise FileNotFoundError("存在しないチェック済み項目があります。リネームは開始していません。")

    renames: list[PlannedRename] = []
    for source in source_paths:
        name, suffix = _renameable_name_parts(source, include_extension=include_extension)
        renamed = _apply_rules(name, rule_list)
        _validate_name(renamed)
        output = source.with_name(renamed + suffix)
        if output != source:
            renames.append(PlannedRename(source=source, output=output))

    if not renames:
        raise ValueError("変更対象の名前がありません。リネームは開始していません。")

    _validate_outputs(tuple(renames))
    return RenamePlan(
        rules=rule_list,
        renames=tuple(renames),
        include_extension=include_extension,
    )


def build_mapped_rename_preview(
    mappings: Iterable[tuple[Path, str]], *, include_extension: bool = False
) -> RenamePreview:
    """Build a rename plan from explicit path/name pairs supplied by a record table."""
    pairs = tuple((Path(source), output_name) for source, output_name in mappings)
    scope = "拡張子を含む名前全体" if include_extension else "元の拡張子を維持"
    header = f"操作: 対応表からリネーム\n対象: {len(pairs)} 件\n対象範囲: {scope}\n\n"
    try:
        if not pairs:
            raise ValueError("対応するファイルと変更後の名前がありません。")
        sources = tuple(source for source, _output_name in pairs)
        if len(set(sources)) != len(sources):
            raise ValueError("同じ変更元パスが複数含まれています。")
        missing = [info.path for info in inspect_paths(sources) if not info.is_operable]
        if missing:
            raise FileNotFoundError(
                "存在しない変更元があります。リネームは開始していません。"
            )
        renames: list[PlannedRename] = []
        for source, output_name in pairs:
            _validate_name(output_name)
            suffix = "" if include_extension or not source.is_file() else source.suffix
            renames.append(PlannedRename(source, source.with_name(output_name + suffix)))
        _validate_outputs(tuple(renames))
        plan = RenamePlan(rules=(), renames=tuple(renames), include_extension=include_extension)
    except (OSError, ValueError) as exc:
        return RenamePreview(None, header + "実行できません。次を確認してください。\n" + str(exc))

    details = "\n".join(
        f"{index}. {rename.source.name}  →  {rename.output.name}"
        for index, rename in enumerate(plan.renames, start=1)
    )
    return RenamePreview(
        plan,
        header
        + "対応表で照合した変更予定です。"
        + "実行直前にパスの存在と名前の衝突を再確認します。\n\n"
        + details,
    )


def execute_rename_plan(plan: RenamePlan) -> list[Path]:
    """Revalidate and publish without overwrite; report completed names on failure."""
    _validate_rename_plan_is_current(plan)
    completed: list[PlannedRename] = []
    try:
        for rename in plan.renames:
            checkpoint(str(rename.source))
            if rename.source == rename.output:
                continue
            rename_noreplace(rename.source, rename.output)
            completed.append(rename)
            report_completed(rename.source, rename.output, "リネーム完了")
    except OSError as exc:
        raise OSError(
            "リネームが停止しました。変更済みの名前は保持しています。\n"
            + "\n".join(f"{item.source} → {item.output}" for item in completed)
            + f"\n{exc}"
        ) from exc
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
        elif rule.kind == "remove_spaces":
            value = "".join(character for character in value if not character.isspace())
        elif rule.kind == "normalize_width":
            value = _normalize_fullwidth_ascii(value, rule)
        else:
            raise ValueError("未対応のリネームルールです。")
    return value


def _validate_rules(rules: tuple[RenameRule, ...]) -> None:
    """Reject malformed regex rules before examining or changing any path."""
    for index, rule in enumerate(rules, start=1):
        if rule.kind == "normalize_width" and not any(
            (rule.width_letters, rule.width_digits, rule.width_symbols)
        ):
            raise ValueError(f"{index}件目：半角にする対象を1つ以上選んでください。")
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


def _normalize_fullwidth_ascii(value: str, rule: RenameRule) -> str:
    """Convert selected full-width ASCII groups, never Japanese punctuation."""
    pairs: list[tuple[str, str]] = []
    if rule.width_digits:
        pairs.append(("０１２３４５６７８９", "0123456789"))
    if rule.width_letters:
        pairs.extend((("ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺ", "ABCDEFGHIJKLMNOPQRSTUVWXYZ"),
                      ("ａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚ", "abcdefghijklmnopqrstuvwxyz")))
    if rule.width_symbols:
        pairs.extend((("！\"＃＄％＆＇（）＊＋，－．／", "!\"#$%&'()*+,-./"),
                      ("：；＜＝＞？＠", ":;<=>?@"),
                      ("［＼］＾＿｀", "[\\]^_`"),
                      ("｛｜｝～", "{|}~")))
    translation = {
        source: destination
        for sources, destinations in pairs
        for source, destination in zip(sources, destinations)
    }
    return value.translate(str.maketrans(translation))


def _with_directory_descendants(sources: tuple[Path, ...]) -> tuple[Path, ...]:
    """Include ordinary-directory descendants for width normalization rules."""
    expanded: list[Path] = []
    seen: set[Path] = set()
    for source in sources:
        if source not in seen:
            expanded.append(source)
            seen.add(source)
        if not source.is_dir() or source.is_symlink():
            continue
        try:
            for directory, directories, files in os.walk(source, topdown=True, followlinks=False):
                parent = Path(directory)
                for name in (*directories, *files):
                    candidate = parent / name
                    if candidate not in seen:
                        expanded.append(candidate)
                        seen.add(candidate)
        except OSError as exc:
            raise OSError(f"フォルダ配下を確認できません: {source}\n{exc}") from exc
    # Direct execution must rename children before their parent directory.
    return tuple(sorted(expanded, key=lambda path: len(path.parts), reverse=True))


def _validate_name(name: str) -> None:
    if not name:
        raise ValueError("リネーム後の名前が空になります。")
    if "/" in name or "\x00" in name:
        raise ValueError("リネーム文字列に使用できない文字が含まれています。")
    if name in {".", ".."}:
        raise ValueError("リネーム後の名前に . または .. は使用できません。")


def _validate_outputs(renames: tuple[PlannedRename, ...]) -> None:
    """Reject every output collision together, with all involved path names."""
    renames_by_output: dict[Path, list[PlannedRename]] = defaultdict(list)
    for rename in renames:
        renames_by_output[rename.output].append(rename)

    duplicate_outputs = {
        output: planned
        for output, planned in renames_by_output.items()
        if len(planned) > 1
    }
    existing_conflicts = [
        rename
        for rename in renames
        if rename.output != rename.source and path_entry_exists(rename.output)
    ]
    if not duplicate_outputs and not existing_conflicts:
        return

    lines = [
        "リネーム後の名前が重複または既存項目と衝突します。リネームは開始していません。"
    ]
    if duplicate_outputs:
        lines.append("\n今回の変更後名どうしの重複:")
        for output, planned in sorted(duplicate_outputs.items(), key=lambda item: str(item[0])):
            lines.append(f"変更後の名前: {output}")
            lines.extend(f"  変更元: {rename.source}" for rename in planned)

    if existing_conflicts:
        source_paths = {rename.source for rename in renames}
        lines.append("\nすでに存在する項目との衝突:")
        for rename in existing_conflicts:
            lines.append(f"変更元: {rename.source}")
            lines.append(f"  衝突する名前: {rename.output}")
            if rename.output in source_paths:
                lines.append("  原因: この名前は選択中の別の変更元でもあります")
            else:
                lines.append("  原因: 同じ場所に既存のファイルまたはフォルダがあります")

    raise FileExistsError("\n".join(lines))


def _validate_rename_plan_is_current(plan: RenamePlan) -> None:
    sources = tuple(rename.source for rename in plan.renames)
    missing = [info.path for info in inspect_paths(sources) if not info.is_operable]
    if missing:
        raise FileNotFoundError("プレビュー後にチェック済み項目が変化したため、リネームを開始しません。")
    _validate_outputs(plan.renames)
