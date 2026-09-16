"""Explicit OS-managed trash requests; never fall back to permanent deletion."""

from __future__ import annotations

from runtime import managed_process

from dataclasses import dataclass
from pathlib import Path
import shutil
import subprocess

from foundation.path import path_entry_exists
from runtime.operation_progress import checkpoint, completed as report_completed

from .copy_workflow import parse_target_paths


@dataclass(frozen=True)
class TrashPlan:
    sources: tuple[Path, ...]


@dataclass(frozen=True)
class TrashPreview:
    plan: TrashPlan | None
    text: str

    @property
    def is_ready(self) -> bool:
        return self.plan is not None


def build_trash_preview(source_text: str) -> TrashPreview:
    """Validate an OS trash request without moving anything."""
    sources = parse_target_paths(source_text)
    header = f"操作: ゴミ箱へ送る\nチェック済み: {len(sources)} 件\n\n"
    if not sources:
        return TrashPreview(None, header + "実行できません。項目を1件以上チェックしてください。")
    missing = [path for path in sources if not path_entry_exists(path)]
    if missing:
        return TrashPreview(
            None,
            header + "実行できません。存在しないチェック済み項目があります。\n" + "\n".join(map(str, missing)),
        )
    if shutil.which("gio") is None:
        return TrashPreview(None, header + "実行できません。OSの gio コマンドが見つかりません。")
    return TrashPreview(
        TrashPlan(sources),
        header
        + "OSのゴミ箱へ送ります。ゴミ箱の場所はOSが自動選択します。\n"
        + "複数件の途中で失敗した場合、既に送れた項目はゴミ箱に残ります。\n\n"
        + "対象:\n"
        + "\n".join(map(str, sources)),
    )


def execute_trash_plan(plan: TrashPlan) -> list[Path]:
    """Ask gio to trash each item and stop on the first failure without a destructive fallback."""
    completed: list[Path] = []
    for source in plan.sources:
        checkpoint(str(source))
        result = managed_process.run(
            ["gio", "trash", str(source)], capture_output=True, text=True, check=False,
            label='ゴミ箱へ移動中',
        )
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip() or str(source)
            raise OSError("ゴミ箱へ送れませんでした。\n" + detail
                          + "\n送信済み:\n" + "\n".join(map(str, completed)))
        completed.append(source)
        report_completed(source, "OSのゴミ箱", "送信完了")
    return completed
