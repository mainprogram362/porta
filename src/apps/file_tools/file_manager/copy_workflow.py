"""Validation, previews, and execution for file-manager copy operations."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
import shutil

from foundation.filesystem import copy_or_move
from foundation.path import normalize_path, path_entry_exists
from foundation.path_inspection import inspect_path, inspect_paths


@dataclass(frozen=True)
class CopyRequest:
    """A fully validated request to copy every source into one directory."""

    sources: tuple[Path, ...]
    destination: Path


@dataclass(frozen=True)
class OneToOneCopyRequest:
    """A fully validated request pairing each source with one destination."""

    pairs: tuple[tuple[Path, Path], ...]


@dataclass(frozen=True)
class PlannedCopy:
    """One source and the exact currently planned output path."""

    source: Path
    destination: Path
    output: Path


@dataclass(frozen=True)
class CopyPlan:
    """A validated, in-memory plan that can be previewed before execution."""

    request: CopyRequest | OneToOneCopyRequest
    copies: tuple[PlannedCopy, ...]


@dataclass(frozen=True)
class CopyPreview:
    """A non-persistent description of the pending copy operation."""

    request: CopyRequest | OneToOneCopyRequest | None
    plan: CopyPlan | None
    text: str

    @property
    def is_ready(self) -> bool:
        """Whether all required information is currently valid."""
        return self.request is not None


def _parse_path_lines(text: str, *, deduplicate: bool) -> tuple[Path, ...]:
    paths: list[Path] = []
    seen: set[Path] = set()
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        path = normalize_path(line)
        if deduplicate and path in seen:
            continue
        paths.append(path)
        seen.add(path)
    return tuple(paths)


def parse_target_paths(text: str) -> tuple[Path, ...]:
    """Parse one target per line, ignoring blanks and duplicate targets."""
    return _parse_path_lines(text, deduplicate=True)


def parse_destination_paths(text: str) -> tuple[Path, ...]:
    """Parse destination lines, preserving repeated destinations for pairing."""
    return _parse_path_lines(text, deduplicate=False)


def _validate_sources(source_text: str) -> tuple[Path, ...]:
    sources = parse_target_paths(source_text)
    if not sources:
        raise ValueError("チェック済み項目を1行に1件ずつ入力またはドロップしてください。")
    missing = [info.path for info in inspect_paths(sources) if not info.is_operable]
    if missing:
        details = "\n".join(str(path) for path in missing)
        raise FileNotFoundError(
            "存在しないチェック済み項目があります。コピーは開始していません。\n" + details
        )
    return sources


def _validate_destination(destination: Path) -> None:
    info = inspect_path(destination)
    if not info.exists:
        raise FileNotFoundError(f"コピー先フォルダが存在しません: {destination}")
    if info.kind not in {"directory", "symlink_directory"}:
        raise NotADirectoryError(f"コピー先はフォルダで指定してください: {destination}")


def _validate_no_recursive_copy(sources: tuple[Path, ...], destinations: tuple[Path, ...]) -> None:
    invalid_sources = [
        source
        for source, destination in zip(sources, destinations)
        if not source.is_symlink() and source.is_dir() and destination.is_relative_to(source)
    ]
    if invalid_sources:
        details = "\n".join(str(path) for path in invalid_sources)
        raise ValueError(
            "フォルダをそのフォルダ自身または配下へコピーすることはできません。\n"
            + details
        )


def validate_copy_request(source_text: str, destination_text: str) -> CopyRequest:
    """Validate a simple copy with exactly one destination folder."""
    sources = _validate_sources(source_text)
    destinations = parse_destination_paths(destination_text)
    if not destinations:
        raise ValueError("コピー先フォルダを1件入力してください。")
    if len(destinations) != 1:
        raise ValueError("シンプルコピーではコピー先フォルダは1件だけ指定してください。")
    destination = destinations[0]
    _validate_destination(destination)
    _validate_no_recursive_copy(sources, (destination,) * len(sources))
    return CopyRequest(sources=sources, destination=destination)


def validate_one_to_one_copy_request(
    source_text: str, destination_text: str
) -> OneToOneCopyRequest:
    """Validate ordered source/destination pairs before changing any files."""
    sources = _validate_sources(source_text)
    destinations = parse_destination_paths(destination_text)
    if not destinations:
        raise ValueError("コピー先フォルダを対象と同じ件数だけ入力してください。")
    if len(sources) != len(destinations):
        raise ValueError(
            "一対一コピーでは、チェック済み項目とコピー先フォルダの件数を一致させてください。"
            f"（チェック済み: {len(sources)} 件、コピー先: {len(destinations)} 件）"
        )
    for destination in destinations:
        _validate_destination(destination)
    _validate_no_recursive_copy(sources, destinations)
    return OneToOneCopyRequest(pairs=tuple(zip(sources, destinations)))


def build_copy_preview(
    source_text: str, destination_text: str, *, mode: str
) -> CopyPreview:
    """Describe the selected copy mode without changing any files."""
    source_count = len(parse_target_paths(source_text))
    destination_count = len(parse_destination_paths(destination_text))
    mode_name = "シンプルコピー" if mode == "simple" else "一対一コピー"
    header = (
        f"操作: コピー / {mode_name}\n"
        f"チェック済み: {source_count} 件\n"
        f"コピー先フォルダ: {destination_count} 件\n\n"
    )
    try:
        request: CopyRequest | OneToOneCopyRequest
        if mode == "simple":
            request = validate_copy_request(source_text, destination_text)
        elif mode == "one_to_one":
            request = validate_one_to_one_copy_request(source_text, destination_text)
        else:
            raise ValueError("未対応のコピー方式です。")
    except (OSError, ValueError) as exc:
        return CopyPreview(None, None, header + "実行できません。次を確認してください。\n" + str(exc))

    plan = build_copy_plan(request)

    if isinstance(request, CopyRequest):
        action = f"{len(request.sources)} 件を同じコピー先フォルダへコピーします。"
    else:
        action = f"{len(request.pairs)} 組を、上から順に対応させてコピーします。"
    return CopyPreview(
        request,
        plan,
        header
        + "実行内容:\n"
        + action
        + "\n同名の項目がある場合は、(1) などを付けて上書きせずに保存します。\n"
        + "実行直前に対象とコピー先フォルダをもう一度確認します。\n\n"
        + "詳細な実行予定:\n"
        + _format_planned_copies(plan.copies),
    )


def build_simple_copy_preview(source_text: str, destination_text: str) -> CopyPreview:
    """Compatibility wrapper for the initial simple-copy screen."""
    return build_copy_preview(source_text, destination_text, mode="simple")


def build_copy_plan(request: CopyRequest | OneToOneCopyRequest) -> CopyPlan:
    """Calculate exact non-overwriting output names for a validated request."""
    pairs = (
        tuple((source, request.destination) for source in request.sources)
        if isinstance(request, CopyRequest)
        else request.pairs
    )
    reserved: set[Path] = set()
    copies: list[PlannedCopy] = []
    for source, destination in pairs:
        output = _next_available_output(destination / source.name, reserved)
        reserved.add(output)
        copies.append(PlannedCopy(source=source, destination=destination, output=output))
    return CopyPlan(request=request, copies=tuple(copies))


def _next_available_output(candidate: Path, reserved: set[Path]) -> Path:
    """Mirror collision-safe naming while also reserving this plan's outputs."""
    if not path_entry_exists(candidate) and candidate not in reserved:
        return candidate
    stem = candidate.stem
    suffix = candidate.suffix
    counter = 1
    while True:
        numbered = candidate.parent / f"{stem} ({counter}){suffix}"
        if not path_entry_exists(numbered) and numbered not in reserved:
            return numbered
        counter += 1


def _format_planned_copies(copies: tuple[PlannedCopy, ...]) -> str:
    """Render every planned source/output pair for the scrollable UI preview."""
    return "\n\n".join(
        f"{index}. コピー元:\n{copy.source}\n   作成予定:\n{copy.output}"
        for index, copy in enumerate(copies, start=1)
    )


def execute_copy(request: CopyRequest) -> list[Path]:
    """Build and execute a fresh direct-copy plan for non-UI callers."""
    return execute_copy_plan(build_copy_plan(request))


def execute_one_to_one_copy(request: OneToOneCopyRequest) -> list[Path]:
    """Build and execute a fresh direct-copy plan for non-UI callers."""
    return execute_copy_plan(build_copy_plan(request))


def execute_copy_plan(plan: CopyPlan) -> list[Path]:
    """Revalidate a displayed plan, then copy directly to its exact outputs."""
    _validate_plan_is_current(plan)
    return _execute_direct_copy(plan.copies)


def _validate_plan_is_current(plan: CopyPlan) -> None:
    """Abort before copying if anything relevant changed after the preview."""
    sources = tuple(copy.source for copy in plan.copies)
    destinations = tuple(copy.destination for copy in plan.copies)
    missing = [info.path for info in inspect_paths(sources) if not info.is_operable]
    if missing:
        raise FileNotFoundError("プレビュー後にチェック済み項目が変化したため、コピーを開始しません。")
    for destination in destinations:
        _validate_destination(destination)
    _validate_no_recursive_copy(sources, destinations)
    occupied = [copy.output for copy in plan.copies if path_entry_exists(copy.output)]
    if occupied:
        raise FileExistsError("プレビュー後にコピー先が変化したため、コピーを開始しません。")


def _execute_direct_copy(copies: Iterable[PlannedCopy]) -> list[Path]:
    """Copy directly to final destinations without creating a staging area.

    If an error is detected, only outputs created by this invocation are
    removed. No operation log or temporary copy tree is created.
    """
    created: list[Path] = []
    pending_output: Path | None = None
    try:
        for planned in copies:
            pending_output = planned.output
            copied = copy_or_move(
                planned.source,
                pending_output,
                mode="copy",
                overwrite=False,
                create_dirs=False,
                collision_mode="error",
            )
            created.append(copied)
            pending_output = None
    except Exception:
        _remove_created_outputs(created, pending_output)
        raise OSError(
            "コピー中にエラーが発生しました。今回作成した出力は削除を試みました。"
        ) from None
    return created


def _remove_created_outputs(created: list[Path], pending_output: Path | None) -> None:
    """Best-effort cleanup limited to outputs from the current direct copy."""
    outputs = [*created, *([pending_output] if pending_output is not None else [])]
    for output in reversed(outputs):
        try:
            if output.is_symlink() or output.is_file():
                output.unlink(missing_ok=True)
            elif output.is_dir():
                shutil.rmtree(output, ignore_errors=True)
        except OSError:
            continue
