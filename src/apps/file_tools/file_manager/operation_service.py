"""Operation-neutral preview and execution dispatch for the file-manager UI."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .copy_workflow import build_copy_preview, execute_copy_plan
from .move_workflow import build_move_preview, execute_move_plan
from .rename_workflow import RenameRule, build_rename_preview, execute_rename_plan
from .trash_workflow import build_trash_preview, execute_trash_plan
from .zip_workflow import build_zip_preview, execute_zip_plan

OperationKind = Literal["copy", "move", "zip", "trash", "rename"]


@dataclass(frozen=True)
class OperationPresentation:
    """Everything the UI needs to display and execute one current operation."""

    kind: OperationKind
    plan: object | None
    text: str
    is_ready: bool
    summary_lines: tuple[str, ...]
    execute_label: str
    completed_label: str
    result_label: str


def build_operation_presentation(
    kind: OperationKind,
    targets_text: str,
    destinations_text: str,
    *,
    mode: str,
    target_count: int,
    destination_count: int,
    rename_rules: list[RenameRule],
    include_extension: bool,
) -> OperationPresentation:
    """Build one UI-ready, non-persistent operation plan without changing files."""
    if kind == "copy":
        preview = build_copy_preview(targets_text, destinations_text, mode=mode)
        return OperationPresentation(
            kind, preview.plan, preview.text, preview.is_ready,
            (f"操作: コピー（{'通常' if mode == 'simple' else '一対一'}）", f"チェック済み: {target_count} 件", f"出力先: {destination_count} 件"),
            "コピーを実行", "コピー完了", "コピー先",
        )
    if kind == "move":
        preview = build_move_preview(targets_text, destinations_text, mode=mode)
        return OperationPresentation(
            kind, preview.plan, preview.text, preview.is_ready,
            (f"操作: 移動（{'通常' if mode == 'simple' else '一対一'}）", f"チェック済み: {target_count} 件", f"出力先: {destination_count} 件"),
            "移動を実行", "移動完了", "移動先",
        )
    if kind == "zip":
        preview = build_zip_preview(targets_text, destinations_text, mode=mode)
        mode_name = {"in_place": "その場", "simple": "指定先", "one_to_one": "一対一"}[mode]
        return OperationPresentation(
            kind, preview.plan, preview.text, preview.is_ready,
            (f"操作: ZIP（{mode_name}）", f"チェック済み: {target_count} 件", f"出力先: {destination_count} 件"),
            "ZIPを実行", "ZIP作成完了", "ZIP出力先",
        )
    if kind == "trash":
        preview = build_trash_preview(targets_text)
        return OperationPresentation(
            kind, preview.plan, preview.text, preview.is_ready,
            ("操作: ゴミ箱へ送る", f"チェック済み: {target_count} 件", "出力先: OSが自動選択"),
            "ゴミ箱へ送る", "ゴミ箱へ送信完了", "ゴミ箱へ送った項目",
        )
    preview = build_rename_preview(targets_text, rename_rules, include_extension=include_extension)
    scope = "拡張子を含む" if include_extension else "拡張子を除く"
    return OperationPresentation(
        kind, preview.plan, preview.text, preview.is_ready,
        ("操作: リネーム", f"チェック済み: {target_count} 件（ルール: {len(rename_rules)}件）", f"対象範囲: {scope}"),
        "リネームを実行", "リネーム完了", "変更後",
    )


def execute_operation(presentation: OperationPresentation) -> list:
    """Execute a revalidated plan selected by the current presentation."""
    if presentation.plan is None:
        raise ValueError("実行可能なプレビューを作成してください。")
    if presentation.kind == "copy":
        return execute_copy_plan(presentation.plan)  # type: ignore[arg-type]
    if presentation.kind == "move":
        return execute_move_plan(presentation.plan)  # type: ignore[arg-type]
    if presentation.kind == "zip":
        return execute_zip_plan(presentation.plan)  # type: ignore[arg-type]
    if presentation.kind == "trash":
        return execute_trash_plan(presentation.plan)  # type: ignore[arg-type]
    return execute_rename_plan(presentation.plan)  # type: ignore[arg-type]
