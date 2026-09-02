"""Planning and validation for safe ZIP and 7z creation."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path

from foundation.path import path_entry_exists

from .archive_backends import ArchiveBackendUnavailable, select_archive_backend
from .copy_workflow import parse_destination_paths


SUPPORTED_COMPRESSION_FORMATS = frozenset({"7z", "zip"})
SUPPORTED_COMPRESSION_LEVELS = frozenset({0, 1, 3, 5, 7, 9})


@dataclass(frozen=True)
class PlannedArchive:
    sources: tuple[Path, ...]
    destination: Path
    output: Path


@dataclass(frozen=True)
class CompressionPlan:
    archives: tuple[PlannedArchive, ...]
    backend_id: str
    backend_name: str
    archive_format: str
    compression_level: int
    password_enabled: bool
    hide_names: bool
    individual: bool
    in_place: bool


@dataclass(frozen=True)
class CompressionPreview:
    plan: CompressionPlan | None
    text: str

    @property
    def is_ready(self) -> bool:
        return self.plan is not None


def build_compression_preview(
    sources: tuple[Path, ...],
    destination_text: str,
    *,
    archive_format: str,
    archive_name: str,
    compression_level: int,
    password_enabled: bool,
    password: str,
    hide_names: bool,
    individual: bool,
    in_place: bool,
) -> CompressionPreview:
    header = f"操作: 圧縮\n対象: {len(sources)} 件\n\n"
    try:
        _validate_options(
            archive_format,
            compression_level,
            password_enabled,
            password,
            hide_names,
            individual,
            in_place,
        )
        _validate_sources(sources, require_unique_names=not individual)
        backend = select_archive_backend()
        destinations = _destinations_for(sources, destination_text, individual, in_place)
        groups = tuple((source,) for source in sources) if individual else (sources,)
        reserved: set[Path] = set()
        planned: list[PlannedArchive] = []
        for group, destination in zip(groups, destinations):
            base_name = group[0].name if individual else _clean_archive_name(archive_name)
            candidate = destination / f"{base_name}.{archive_format}"
            output = _next_output(candidate, reserved)
            _validate_output_location(group, destination)
            reserved.add(output)
            planned.append(PlannedArchive(group, destination, output))
        plan = CompressionPlan(
            tuple(planned),
            backend.backend_id,
            backend.display_name,
            archive_format,
            compression_level,
            password_enabled,
            hide_names,
            individual,
            in_place,
        )
    except (ArchiveBackendUnavailable, OSError, ValueError) as exc:
        return CompressionPreview(None, header + "実行できません。\n" + str(exc))

    format_label = "7z" if archive_format == "7z" else "ZIP"
    password_label = "あり" if password_enabled else "なし"
    names_label = "隠す" if hide_names else "隠さない"
    grouping = "対象ごとに個別" if individual else "全対象を1つにまとめる"
    location = "各対象と同じ場所" if in_place else "指定した出力先"
    details = "\n\n".join(
        "\n".join(str(source) for source in item.sources) + f"\n → {item.output}"
        for item in plan.archives
    )
    return CompressionPreview(
        plan,
        header
        + f"使用エンジン: {plan.backend_name}\n"
        + f"形式: {format_label}\n"
        + f"圧縮レベル: {compression_level}\n"
        + f"パスワード: {password_label}\n"
        + f"ファイル名: {names_label}\n"
        + f"まとめ方: {grouping}\n"
        + f"出力方法: {location}\n\n"
        + details,
    )


def revalidate_compression_plan(plan: CompressionPlan) -> None:
    select_archive_backend(plan.backend_id)
    for item in plan.archives:
        _validate_sources(item.sources)
        if not item.destination.is_dir():
            raise NotADirectoryError(f"圧縮先が見つかりません: {item.destination}")
        _validate_output_location(item.sources, item.destination)
        if path_entry_exists(item.output):
            raise FileExistsError(f"プレビュー後に出力先が使用されました: {item.output}")


def source_file_bytes(sources: tuple[Path, ...]) -> int:
    return sum(_source_file_bytes(source) for source in sources)


def _validate_options(
    archive_format: str,
    compression_level: int,
    password_enabled: bool,
    password: str,
    hide_names: bool,
    individual: bool,
    in_place: bool,
) -> None:
    if archive_format not in SUPPORTED_COMPRESSION_FORMATS:
        raise ValueError("圧縮形式は7zまたはZIPを選択してください。")
    if compression_level not in SUPPORTED_COMPRESSION_LEVELS:
        raise ValueError("未対応の圧縮レベルです。")
    if password_enabled and not password:
        raise ValueError("パスワード付き圧縮が有効です。共通パスワードを入力してください。")
    if password_enabled and any(character in password for character in ("\x00", "\r", "\n")):
        raise ValueError("パスワードには改行やNUL文字を使用できません。")
    if not password_enabled and hide_names:
        raise ValueError("ファイル名を隠すにはパスワード付き7zが必要です。")
    if hide_names and archive_format != "7z":
        raise ValueError("ZIPではファイル名を隠せません。7zを選択してください。")
    if in_place and not individual:
        raise ValueError("その場で圧縮は、対象ごとの個別圧縮でのみ使用できます。")


def _validate_sources(
    sources: tuple[Path, ...], *, require_unique_names: bool = False
) -> None:
    if not sources:
        raise ValueError("圧縮対象を1件以上指定してください。")
    seen_names: set[str] = set()
    for source in sources:
        if source.is_symlink() or not (source.is_file() or source.is_dir()):
            raise FileNotFoundError(f"通常ファイルまたはフォルダとして読めません: {source}")
        folded = source.name.casefold()
        if require_unique_names and folded in seen_names:
            raise ValueError(
                f"同じ名前の対象は1つの書庫へ安全にまとめられません: {source.name}"
            )
        seen_names.add(folded)
    link = _first_symbolic_link(sources)
    if link is not None:
        raise ValueError(
            "リンク先を誤って収録しないため、シンボリックリンクを含む対象は圧縮できません。\n"
            f"検出: {link}"
        )
    if require_unique_names:
        for parent in sources:
            if not parent.is_dir():
                continue
            nested = next(
                (
                    candidate
                    for candidate in sources
                    if candidate != parent and candidate.is_relative_to(parent)
                ),
                None,
            )
            if nested is not None:
                raise ValueError(
                    "フォルダと、その配下の項目を同じ書庫へ重複して入れることはできません。\n"
                    f"フォルダ: {parent}\n配下の対象: {nested}"
                )


def _destinations_for(
    sources: tuple[Path, ...],
    destination_text: str,
    individual: bool,
    in_place: bool,
) -> tuple[Path, ...]:
    if in_place:
        return tuple(source.parent for source in sources)
    destinations = parse_destination_paths(destination_text)
    if len(destinations) != 1 or not destinations[0].is_dir():
        raise NotADirectoryError("圧縮先は存在するフォルダを1件だけ指定してください。")
    count = len(sources) if individual else 1
    return destinations * count


def _validate_output_location(sources: tuple[Path, ...], destination: Path) -> None:
    for source in sources:
        if source.is_dir() and destination.is_relative_to(source):
            raise ValueError(f"対象フォルダ自身または配下には圧縮出力できません: {source}")


def _clean_archive_name(value: str) -> str:
    name = value.strip()
    lowered = name.casefold()
    for suffix in (".7z", ".zip"):
        if lowered.endswith(suffix):
            name = name[: -len(suffix)].rstrip()
            break
    if not name or name in {".", ".."}:
        raise ValueError("圧縮ファイル名を入力してください。")
    if Path(name).name != name or "/" in name or "\\" in name or "\x00" in name:
        raise ValueError("圧縮ファイル名にはフォルダ部分を入力できません。")
    return name


def _next_output(candidate: Path, reserved: set[Path]) -> Path:
    if not path_entry_exists(candidate) and candidate not in reserved:
        return candidate
    counter = 1
    while True:
        output = candidate.with_name(f"{candidate.stem} ({counter}){candidate.suffix}")
        if not path_entry_exists(output) and output not in reserved:
            return output
        counter += 1


def _source_file_bytes(source: Path) -> int:
    if source.is_file():
        return source.stat().st_size
    total = 0
    for path in source.rglob("*"):
        if path.is_file() and not path.is_symlink():
            total += path.stat().st_size
    return total


def _first_symbolic_link(sources: tuple[Path, ...]) -> Path | None:
    pending = list(sources)
    while pending:
        path = pending.pop()
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
    return None
