"""Copy-then-verify move workflow that never deletes sources before validation."""

from __future__ import annotations

import os
from pathlib import Path
import shutil

from .copy_workflow import CopyPlan, CopyPreview, build_copy_preview, execute_copy_plan


def build_move_preview(source_text: str, destination_text: str, *, mode: str) -> CopyPreview:
    """Build a move preview using the same collision-safe plan as copying."""
    preview = build_copy_preview(source_text, destination_text, mode=mode)
    return CopyPreview(
        preview.request,
        preview.plan,
        preview.text.replace("操作: コピー", "操作: 移動").replace(
            "コピーします", "コピーして照合後に元を削除します"
        ),
    )


def execute_move_plan(plan: CopyPlan) -> list[Path]:
    """Copy all outputs, verify all trees, then and only then delete all sources."""
    outputs = execute_copy_plan(plan)
    mismatches = [
        planned.source
        for planned, output in zip(plan.copies, outputs)
        if not _paths_match(planned.source, output)
    ]
    if mismatches:
        details = "\n".join(str(path) for path in mismatches)
        raise OSError("コピー後の照合に失敗したため、元データは削除していません。\n" + details)

    try:
        for planned in plan.copies:
            _remove_source(planned.source)
    except OSError as exc:
        raise OSError(
            "全件の照合後に元データの削除中エラーが発生しました。"
            "既に削除した元データは復元できません。\n" + str(exc)
        ) from exc
    return outputs


def _paths_match(source: Path, output: Path) -> bool:
    if source.is_symlink():
        try:
            return output.is_symlink() and os.readlink(source) == os.readlink(output)
        except OSError:
            return False
    if source.is_file():
        return (
            not output.is_symlink()
            and output.is_file()
            and source.stat().st_size == output.stat().st_size
        )
    if not source.is_dir() or not output.is_dir():
        return False
    return _tree_snapshot(source) == _tree_snapshot(output)


def _tree_snapshot(root: Path) -> dict[str, tuple[str, int | str]] | None:
    """Capture entries without ever descending through a symbolic link."""
    entries: dict[str, tuple[str, int | str]] = {}
    pending = [root]
    try:
        while pending:
            current = pending.pop()
            with os.scandir(current) as children:
                for child in children:
                    path = Path(child.path)
                    relative = str(path.relative_to(root))
                    if child.is_symlink():
                        entries[relative] = ("symlink", os.readlink(child.path))
                    elif child.is_dir(follow_symlinks=False):
                        entries[relative] = ("directory", 0)
                        pending.append(path)
                    elif child.is_file(follow_symlinks=False):
                        entries[relative] = ("file", child.stat(follow_symlinks=False).st_size)
                    else:
                        entries[relative] = ("other", 0)
    except OSError:
        return None
    return entries


def _remove_source(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    else:
        shutil.rmtree(path)
