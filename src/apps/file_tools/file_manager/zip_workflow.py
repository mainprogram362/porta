"""In-memory planning and verified ZIP creation for local files and folders."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import zipfile
import tempfile

from foundation.path import path_entry_exists
from foundation.safe_transfer import rename_noreplace
from runtime.operation_progress import checkpoint, completed

from .copy_workflow import parse_destination_paths, parse_target_paths


@dataclass(frozen=True)
class PlannedZip:
    source: Path
    destination: Path
    output: Path


@dataclass(frozen=True)
class ZipPlan:
    archives: tuple[PlannedZip, ...]


@dataclass(frozen=True)
class ZipPreview:
    plan: ZipPlan | None
    text: str

    @property
    def is_ready(self) -> bool:
        return self.plan is not None


def build_zip_preview(source_text: str, destination_text: str, *, mode: str) -> ZipPreview:
    sources = parse_target_paths(source_text)
    header = f"操作: ZIP\nチェック済み: {len(sources)} 件\n\n"
    try:
        if not sources:
            raise ValueError("チェック済み項目を1件以上指定してください。")
        if any(not path.exists() for path in sources):
            raise FileNotFoundError("存在しないチェック済み項目があります。")
        link = _first_symbolic_link(sources)
        if link is not None:
            raise ValueError(
                "シンボリックリンクを含む対象は、ZIPへ安全に保存する方式が未対応です。\n"
                "リンク先を誤って収録しないため、リンクを除外してから実行してください。\n"
                f"検出: {link}"
            )
        if mode == "in_place":
            destinations = tuple(path.parent for path in sources)
        else:
            destinations = parse_destination_paths(destination_text)
            if mode == "simple":
                if len(destinations) != 1:
                    raise ValueError("指定先ZIPでは出力先フォルダを1件だけ指定してください。")
                destinations = destinations * len(sources)
            elif mode == "one_to_one":
                if len(destinations) != len(sources):
                    raise ValueError("一対一ZIPでは対象と出力先の件数を一致させてください。")
            else:
                raise ValueError("未対応のZIP方式です。")
            if any(not path.is_dir() for path in destinations):
                raise NotADirectoryError("ZIP先は存在するフォルダで指定してください。")
        reserved: set[Path] = set()
        archives: list[PlannedZip] = []
        for source, destination in zip(sources, destinations):
            output = _next_zip_output(destination / f"{source.name}.zip", reserved)
            reserved.add(output)
            archives.append(PlannedZip(source, destination, output))
        plan = ZipPlan(tuple(archives))
    except (OSError, ValueError) as exc:
        return ZipPreview(None, header + "実行できません。\n" + str(exc))
    details = "\n\n".join(f"{item.source}\n → {item.output}" for item in plan.archives)
    return ZipPreview(plan, header + "各対象を個別のZIPとして作成します。\n\n" + details)


def execute_zip_plan(plan: ZipPlan) -> list[Path]:
    _validate_zip_plan_is_current(plan)
    required_by_destination: dict[Path, int] = {}
    for item in plan.archives:
        required_by_destination[item.destination] = (
            required_by_destination.get(item.destination, 0) + _source_size(item.source)
        )
    for destination, required in required_by_destination.items():
        if shutil.disk_usage(destination).free < required:
            raise OSError(f"ZIP先の空き容量が不足しています: {destination}")

    created: list[Path] = []
    try:
        for item in plan.archives:
            checkpoint(str(item.source))
            with tempfile.TemporaryDirectory(prefix=".porta-zip-", dir=item.output.parent) as temporary:
                staged = Path(temporary) / "archive.zip"
                _write_zip(item.source, staged)
                _verify_zip(item.source, staged)
                checkpoint()
                rename_noreplace(staged, item.output)
            created.append(item.output)
            completed(item.source, item.output, "ZIP作成完了")
    except Exception as exc:
        raise OSError("ZIP作成が停止しました。完成した出力は保持しています。\n"
                      + "\n".join(map(str, created)) + f"\n{exc}") from exc
    return created


def _next_zip_output(candidate: Path, reserved: set[Path]) -> Path:
    if not path_entry_exists(candidate) and candidate not in reserved:
        return candidate
    counter = 1
    while True:
        output = candidate.with_name(f"{candidate.stem} ({counter}){candidate.suffix}")
        if not path_entry_exists(output) and output not in reserved:
            return output
        counter += 1


def _source_size(path: Path) -> int:
    return path.stat().st_size if path.is_file() else sum(
        item.stat().st_size for item in path.rglob("*") if item.is_file()
    )


def _write_zip(source: Path, output: Path) -> None:
    with zipfile.ZipFile(output, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        if source.is_file():
            archive.write(source, source.name)
        else:
            for path in source.rglob("*"):
                checkpoint()
                if path.is_file():
                    archive.write(path, path.relative_to(source.parent))


def _verify_zip(source: Path, output: Path) -> None:
    with zipfile.ZipFile(output) as archive:
        if archive.testzip() is not None:
            raise OSError(f"作成したZIPの検証に失敗しました: {output}")
        expected = (
            {source.name: source.stat().st_size}
            if source.is_file()
            else {
                str(path.relative_to(source.parent)): path.stat().st_size
                for path in source.rglob("*") if path.is_file()
            }
        )
        actual = {info.filename: info.file_size for info in archive.infolist() if not info.is_dir()}
        if actual != expected:
            raise OSError(f"作成したZIPの内容が元データと一致しません: {output}")


def _validate_zip_plan_is_current(plan: ZipPlan) -> None:
    """Abort if a displayed ZIP plan gained links or otherwise changed."""
    sources = tuple(item.source for item in plan.archives)
    if any(not path.exists() for path in sources):
        raise FileNotFoundError("プレビュー後にチェック済み項目が変化したため、ZIPを開始しません。")
    link = _first_symbolic_link(sources)
    if link is not None:
        raise ValueError(
            "プレビュー後にシンボリックリンクが検出されたため、ZIPを開始しません。\n"
            f"検出: {link}"
        )
    invalid_destinations = [item.destination for item in plan.archives if not item.destination.is_dir()]
    if invalid_destinations:
        raise NotADirectoryError("プレビュー後にZIP先フォルダが変化したため、ZIPを開始しません。")
    occupied = [item.output for item in plan.archives if path_entry_exists(item.output)]
    if occupied:
        raise FileExistsError("プレビュー後にZIP出力先が変化したため、ZIPを開始しません。")


def _first_symbolic_link(sources: tuple[Path, ...]) -> Path | None:
    """Find one link entry without traversing into any link target."""
    pending = list(sources)
    while pending:
        path = pending.pop()
        try:
            if path.is_symlink():
                return path
            if not path.is_dir():
                continue
            with os.scandir(path) as entries:
                for entry in entries:
                    child = Path(entry.path)
                    if entry.is_symlink():
                        return child
                    if entry.is_dir(follow_symlinks=False):
                        pending.append(child)
        except OSError:
            # Existing preview validation will surface the inaccessible source
            # through the normal ZIP write/verification path.
            continue
    return None
