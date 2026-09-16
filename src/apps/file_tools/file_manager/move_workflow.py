"""Filesystem-aware move workflow with safe cross-device copying."""

from __future__ import annotations

import os
import hashlib
from pathlib import Path
import shutil

from foundation.path import path_entry_exists
from foundation.safe_transfer import rename_noreplace
from runtime.operation_progress import checkpoint, completed

from .copy_workflow import (
    CopyPlan,
    CopyPreview,
    PlannedCopy,
    build_copy_preview,
    execute_copy_plan,
    validate_copy_plan_is_current,
)


def build_move_preview(source_text: str, destination_text: str, *, mode: str) -> CopyPreview:
    """Build a collision-safe preview and show how each move will be performed."""
    preview = build_copy_preview(source_text, destination_text, mode=mode)
    text = preview.text.replace("コピー", "移動")
    if preview.plan is not None:
        direct_count = sum(_is_same_filesystem(item) for item in preview.plan.copies)
        copied_count = len(preview.plan.copies) - direct_count
        text += (
            "\n\n移動方式:\n"
            f"・同じファイルシステム内の即時移動: {direct_count} 件\n"
            f"・別ファイルシステムへのコピー・照合・元削除: {copied_count} 件\n"
            "実行直前にも全件を検査し、方式を再判定します。"
        )
    return CopyPreview(
        preview.request,
        preview.plan,
        text,
    )


def execute_move_plan(plan: CopyPlan) -> list[Path]:
    """Rename on one filesystem; copy, verify, and remove across filesystems."""
    # Validate the complete displayed plan before changing even one entry.
    validate_copy_plan_is_current(plan)
    direct_moves: list[PlannedCopy] = []
    copied_moves: list[PlannedCopy] = []
    for planned in plan.copies:
        (direct_moves if _is_same_filesystem(planned) else copied_moves).append(planned)

    copied_outputs: list[Path] = []
    source_identities = {}
    for item in copied_moves:
        identity = item.source.lstat()
        source_identities[item.source] = (identity.st_dev, identity.st_ino)
    if copied_moves:
        copied_plan = CopyPlan(request=plan.request, copies=tuple(copied_moves))
        copied_outputs = execute_copy_plan(copied_plan)

    mismatches = [
        planned.source
        for planned, output in zip(copied_moves, copied_outputs)
        if not _paths_match(planned.source, output)
    ]
    if mismatches:
        details = "\n".join(str(path) for path in mismatches)
        raise OSError(
            "別ファイルシステムへのコピー後の照合に失敗しました。"
            "作成した出力は保持し、元データは移動していません。\n" + details
        )

    renamed: list[PlannedCopy] = []
    try:
        for planned in direct_moves:
            checkpoint(str(planned.source))
            # Do not let os.rename replace something created after the preview.
            if path_entry_exists(planned.output):
                raise FileExistsError(f"移動先が実行直前に使用されました: {planned.output}")
            rename_noreplace(planned.source, planned.output)
            renamed.append(planned)
            completed(planned.source, planned.output, "移動完了")
    except OSError as exc:
        raise OSError(
            "同じファイルシステム内の即時移動中にエラーが発生しました。"
            "完了済みの移動とコピーは保持しています。\n移動済み:\n"
            + "\n".join(f"{item.source} → {item.output}" for item in renamed)
            + "\nコピー済み:\n" + "\n".join(map(str, copied_outputs)) + "\n"
            + str(exc)
        ) from exc

    try:
        for planned in copied_moves:
            checkpoint(str(planned.source))
            current = planned.source.lstat()
            if (current.st_dev, current.st_ino) != source_identities[planned.source]:
                raise OSError(f"コピー後に元の項目が置き換わりました: {planned.source}")
            if not _paths_match(planned.source, planned.output):
                raise OSError(f"元削除の直前に内容が変わりました: {planned.source}")
            _remove_source(planned.source)
            completed(planned.source, planned.output, "元削除完了")
    except OSError as exc:
        raise OSError(
            "別ファイルシステムへのコピーを全件照合後、元データの削除中にエラーが発生しました。"
            "既に削除した元データは復元できません。出力はすべて保持しています。\n"
            + "\n".join(f"{item.source} → {item.output}" for item in copied_moves)
            + "\n" + str(exc)
        ) from exc
    return [planned.output for planned in plan.copies]


def _is_same_filesystem(planned: PlannedCopy) -> bool:
    """Compare the directories whose entries are changed, without following a source link."""
    return planned.source.parent.stat().st_dev == planned.output.parent.stat().st_dev


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
            and _digest(source) == _digest(output)
        )
    if not source.is_dir() or not output.is_dir():
        return False
    snapshot = _tree_snapshot(source)
    return snapshot is not None and snapshot == _tree_snapshot(output)


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            checkpoint()
            digest.update(block)
    return digest.hexdigest()


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
                        entries[relative] = ("file", _digest(path))
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
