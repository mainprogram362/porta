"""Atomic same-filesystem moves from a path's parent into its grandparent."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from foundation.path import normalize_path, path_entry_exists
from foundation.safe_transfer import rename_noreplace
from runtime.operation_progress import OperationFailure, OperationRecord


@dataclass(frozen=True)
class PlannedParentMove:
    """One directory-entry rename which can be reversed while its plan is held."""

    source: Path
    destination: Path
    identity: tuple[int, int]


@dataclass(frozen=True)
class ParentMovePlan:
    """A fully checked same-filesystem move plan."""

    moves: tuple[PlannedParentMove, ...]


@dataclass(frozen=True)
class ParentMovePreview:
    """Human-readable plan that is executable only when validation succeeded."""

    plan: ParentMovePlan | None
    text: str

    @property
    def is_ready(self) -> bool:
        return self.plan is not None


def build_parent_move_preview(paths: Iterable[str | Path]) -> ParentMovePreview:
    """Plan moving ``a/b/c/item`` to ``a/b/item`` without copying data."""
    sources = tuple(normalize_path(path) for path in paths)
    try:
        plan = build_parent_move_plan(sources)
    except (OSError, ValueError) as exc:
        return ParentMovePreview(
            None,
            "親フォルダへ移動は実行しません。\n" + str(exc),
        )
    details = "\n".join(
        f"{index}. {move.source}\n   → {move.destination}"
        for index, move in enumerate(plan.moves, start=1)
    )
    return ParentMovePreview(
        plan,
        "操作：親フォルダへ移動\n"
        f"対象：{len(plan.moves)}件\n"
        "同じファイルシステム内の名前変更だけで移動します。"
        "コピー・削除による移動は行いません。\n"
        "このウィンドウを閉じるまで、実行後に戻すことができます。\n\n"
        + details,
    )


def build_parent_move_plan(paths: Iterable[Path]) -> ParentMovePlan:
    """Validate every planned move before changing one directory entry."""
    sources = tuple(paths)
    if not sources:
        raise ValueError("チェック済みのファイルまたはフォルダを1件以上選んでください。")
    if len(set(sources)) != len(sources):
        raise ValueError("同じパスが複数回含まれています。重複を除いてから実行してください。")
    _reject_nested_sources(sources)

    moves: list[PlannedParentMove] = []
    for source in sources:
        if not path_entry_exists(source):
            raise FileNotFoundError(f"対象が見つかりません: {source}")
        parent = source.parent
        destination_directory = parent.parent
        if destination_directory == parent:
            raise ValueError(f"これ以上親フォルダへ移動できません: {source}")
        if not destination_directory.is_dir():
            raise NotADirectoryError(f"親フォルダの親が見つかりません: {destination_directory}")
        _require_same_filesystem(parent, destination_directory, source)
        destination = destination_directory / source.name
        if path_entry_exists(destination):
            raise FileExistsError(f"移動先に同名の項目があります: {destination}")
        info = source.lstat()
        moves.append(PlannedParentMove(source, destination, (info.st_dev, info.st_ino)))

    destinations = tuple(move.destination for move in moves)
    if len(set(destinations)) != len(destinations):
        raise ValueError("複数の対象が同じ移動先名になります。対象を分けてください。")
    return ParentMovePlan(tuple(moves))


def execute_parent_move_plan(plan: ParentMovePlan) -> list[Path]:
    """Rename entries without overwriting; retain and report partial results."""
    _validate_current_plan(plan)
    completed: list[PlannedParentMove] = []
    try:
        for move in plan.moves:
            _require_identity(move.source, move)
            rename_noreplace(move.source, move.destination)
            completed.append(move)
    except OSError as exc:
        raise OperationFailure(
            "移動中に停止しました。完了済みの移動は保持します。\n" + str(exc),
            [OperationRecord(str(move.source), str(move.destination), "移動完了") for move in completed],
        ) from None
    return [move.destination for move in plan.moves]


def undo_parent_move_plan(plan: ParentMovePlan) -> list[Path]:
    """Reverse a completed plan, but never overwrite a newly created source path."""
    for move in plan.moves:
        _require_identity(move.destination, move)
        if not path_entry_exists(move.destination):
            raise FileNotFoundError(f"戻す対象が見つかりません: {move.destination}")
        if path_entry_exists(move.source):
            raise FileExistsError(f"戻し先が使用されています: {move.source}")
        _require_same_filesystem(move.destination.parent, move.source.parent, move.destination)

    completed: list[PlannedParentMove] = []
    try:
        for move in reversed(plan.moves):
            _require_identity(move.destination, move)
            rename_noreplace(move.destination, move.source)
            completed.append(move)
    except OSError as exc:
        raise OperationFailure(
            "戻す途中で停止しました。戻せた項目は元の場所に保持します。\n" + str(exc),
            [OperationRecord(str(move.destination), str(move.source), "復元完了") for move in completed],
        ) from None
    return [move.source for move in plan.moves]


def _validate_current_plan(plan: ParentMovePlan) -> None:
    for move in plan.moves:
        _require_identity(move.source, move)
        if not path_entry_exists(move.source):
            raise FileNotFoundError(f"対象が見つかりません: {move.source}")
        if path_entry_exists(move.destination):
            raise FileExistsError(f"移動先に同名の項目があります: {move.destination}")
        _require_same_filesystem(move.source.parent, move.destination.parent, move.source)


def _require_same_filesystem(first: Path, second: Path, source: Path) -> None:
    try:
        if first.stat().st_dev != second.stat().st_dev:
            raise OSError(
                "別のファイルシステムへは移動できません（コピーを伴うため中止しました）: "
                + str(source)
            )
    except OSError:
        raise


def _reject_nested_sources(sources: tuple[Path, ...]) -> None:
    for source in sources:
        for other in sources:
            if source != other and other.is_relative_to(source):
                raise ValueError(
                    "親子関係にある項目を同時には移動できません。"
                    "親または子のどちらかだけを選んでください: "
                    + str(source)
                )


def _require_identity(path: Path, move: PlannedParentMove) -> None:
    info = path.lstat()
    if (info.st_dev, info.st_ino) != move.identity:
        raise OSError(f"対象が別の項目に置き換わっています: {path}")
