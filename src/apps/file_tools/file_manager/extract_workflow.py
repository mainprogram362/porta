"""Safe plans and validation shared by the independent extraction window."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
from typing import Literal

from foundation.path import path_entry_exists

from .archive_backends import (
    ArchiveBackend,
    ArchiveBackendError,
    ArchiveBackendUnavailable,
    ArchiveCommandCancelled,
    ArchiveMember,
    ArchivePasswordRejected,
    ArchivePasswordRequired,
    CancelCheck,
    ProcessCallback,
    ProgressCallback,
    select_archive_backend,
)
from .copy_workflow import parse_destination_paths


SUPPORTED_ARCHIVE_SUFFIXES = frozenset({".zip", ".7z", ".rar"})


@dataclass(frozen=True)
class PlannedExtraction:
    archive: Path
    destination: Path
    output: Path
    members: tuple[str, ...] | None = None


@dataclass(frozen=True)
class ExtractPlan:
    extractions: tuple[PlannedExtraction, ...]
    backend_id: str
    backend_name: str


@dataclass(frozen=True)
class ExtractPreview:
    plan: ExtractPlan | None
    text: str

    @property
    def is_ready(self) -> bool:
        return self.plan is not None


ArchiveInspectionState = Literal[
    "normal",
    "password_required",
    "password_verified",
    "password_rejected",
    "damaged",
    "unsafe",
]


@dataclass(frozen=True)
class ArchiveInspection:
    archive: Path
    state: ArchiveInspectionState
    detail: str
    members: tuple[ArchiveMember, ...] = ()

    @property
    def can_extract(self) -> bool:
        return self.state in {"normal", "password_verified"}

    @property
    def display_status(self) -> str:
        labels = {
            "normal": "パスワード不要",
            "password_required": "パスワードが必要（未解除）",
            "password_verified": "パスワードが必要（解除済み）",
            "password_rejected": "パスワード付き（共通パスワードが不一致）",
            "damaged": "破損または読取不能",
            "unsafe": "危険な格納パスを含むため解凍不可",
        }
        return labels[self.state]


def is_supported_archive_path(path: Path) -> bool:
    return path.suffix.casefold() in SUPPORTED_ARCHIVE_SUFFIXES


def build_extract_preview(
    archives: tuple[Path, ...],
    destination_text: str,
    *,
    in_place: bool = False,
    selected_members: dict[Path, tuple[str, ...]] | None = None,
) -> ExtractPreview:
    header = f"操作: 解凍\n圧縮ファイル: {len(archives)} 件\n\n"
    try:
        if not archives:
            raise ValueError("ZIP・7z・RARを1件以上指定してください。")
        invalid = [path for path in archives if not is_supported_archive_path(path)]
        if invalid:
            raise ValueError(f"対応していない形式です: {invalid[0].name}")
        missing = [path for path in archives if not path.is_file() or path.is_symlink()]
        if missing:
            raise FileNotFoundError(f"通常ファイルとして読み込めません: {missing[0]}")
        if in_place:
            destination = None
        else:
            destinations = parse_destination_paths(destination_text)
            if len(destinations) != 1 or not destinations[0].is_dir():
                raise NotADirectoryError("解凍先は存在するフォルダを1件だけ指定してください。")
            destination = destinations[0]
        backend = select_archive_backend()
        reserved: set[Path] = set()
        extractions: list[PlannedExtraction] = []
        for archive in archives:
            item_destination = archive.parent if in_place else destination
            assert item_destination is not None
            output = _next_output(item_destination / archive.stem, reserved)
            reserved.add(output)
            members = None
            if selected_members is not None and archive in selected_members:
                members = selected_members[archive]
                if not members:
                    raise ValueError(f"個別展開する中身が選ばれていません: {archive.name}")
            extractions.append(
                PlannedExtraction(archive, item_destination, output, members)
            )
        plan = ExtractPlan(tuple(extractions), backend.backend_id, backend.display_name)
    except (ArchiveBackendUnavailable, OSError, ValueError) as exc:
        return ExtractPreview(None, header + "実行できません。\n" + str(exc))
    details = "\n\n".join(
        f"{item.archive}\n → {item.output}"
        + (
            f"\n   個別選択: {len(item.members)} 項目"
            if item.members is not None
            else "\n   展開範囲: すべて"
        )
        for item in plan.extractions
    )
    return ExtractPreview(
        plan,
        header
        + f"使用エンジン: {plan.backend_name}\n"
        + (
            "その場モード: 各圧縮ファイルと同じ場所に、個別のフォルダを作ります。\n\n"
            if in_place
            else "指定した解凍先に、圧縮ファイルごとのフォルダを作ります。\n\n"
        )
        + details,
    )


def inspect_archive(
    backend: ArchiveBackend,
    archive: Path,
    *,
    password: str | None,
    progress: ProgressCallback,
    cancelled: CancelCheck,
    process_changed: ProcessCallback,
) -> ArchiveInspection:
    """Classify one archive by making the selected backend actually read it."""
    try:
        members = backend.list_members(
            archive,
            password=password,
            cancelled=cancelled,
            process_changed=process_changed,
        )
    except ArchiveCommandCancelled:
        raise
    except ArchivePasswordRequired:
        return ArchiveInspection(
            archive,
            "password_required",
            "内容一覧が暗号化されています。共通パスワードを入力して再確認してください。",
        )
    except ArchivePasswordRejected:
        return ArchiveInspection(
            archive,
            "password_rejected",
            "入力された共通パスワードでは内容を確認できませんでした。",
        )
    except (ArchiveBackendError, OSError) as exc:
        return ArchiveInspection(archive, "damaged", str(exc))

    try:
        validate_archive_members(members)
    except ValueError as exc:
        return ArchiveInspection(archive, "unsafe", str(exc))

    encrypted = any(member.is_encrypted for member in members)
    if encrypted and not password:
        return ArchiveInspection(
            archive,
            "password_required",
            "暗号化された項目があります。共通パスワードを入力して再確認してください。",
        )
    try:
        backend.test_archive(
            archive,
            password=password,
            progress=progress,
            cancelled=cancelled,
            process_changed=process_changed,
        )
    except ArchiveCommandCancelled:
        raise
    except ArchivePasswordRequired:
        return ArchiveInspection(
            archive,
            "password_required",
            "共通パスワードを入力して再確認してください。",
        )
    except ArchivePasswordRejected:
        return ArchiveInspection(
            archive,
            "password_rejected",
            "入力された共通パスワードが一致しません。",
        )
    except (ArchiveBackendError, OSError) as exc:
        return ArchiveInspection(archive, "damaged", str(exc))
    state: ArchiveInspectionState = "password_verified" if encrypted else "normal"
    return ArchiveInspection(
        archive,
        state,
        f"{backend.display_name}で全内容を読み取り、破損がないことを確認しました。",
        members,
    )


def probe_archive_access(
    backend: ArchiveBackend,
    archive: Path,
    *,
    password: str | None,
    cancelled: CancelCheck,
    process_changed: ProcessCallback,
) -> ArchiveInspection:
    """Check only password access and safe member names before extraction."""
    try:
        members = backend.list_members(
            archive,
            password=password,
            cancelled=cancelled,
            process_changed=process_changed,
        )
    except ArchiveCommandCancelled:
        raise
    except ArchivePasswordRequired:
        return ArchiveInspection(
            archive,
            "password_required",
            "内容一覧の読み取りにパスワードが必要です。",
        )
    except ArchivePasswordRejected:
        return ArchiveInspection(
            archive,
            "password_rejected",
            "入力された共通パスワードが一致しません。",
        )
    except (ArchiveBackendError, OSError) as exc:
        return ArchiveInspection(archive, "damaged", str(exc))

    try:
        validate_archive_members(members)
    except ValueError as exc:
        return ArchiveInspection(archive, "unsafe", str(exc), members)
    encrypted = any(member.is_encrypted for member in members)
    if encrypted and not password:
        return ArchiveInspection(
            archive,
            "password_required",
            "暗号化された項目があります。共通パスワードを入力してください。",
            members,
        )
    return ArchiveInspection(
        archive,
        "password_verified" if encrypted else "normal",
        (
            "パスワードで内容一覧を開けました。"
            if encrypted
            else "パスワードなしで内容一覧を開けました。"
        ),
        members,
    )


def retain_selected_members(staging: Path, selected_members: tuple[str, ...]) -> None:
    """Keep selected member paths and their required ancestors/descendants."""
    selected = tuple(_safe_member_path(value) for value in selected_members)
    if not selected:
        raise ValueError("個別展開する中身が選ばれていません。")

    def selected_file(path: PurePosixPath) -> bool:
        return path in selected or any(choice in path.parents for choice in selected)

    def required_directory(path: PurePosixPath) -> bool:
        return selected_file(path) or any(path in choice.parents for choice in selected)

    for current, directories, files in os.walk(staging, topdown=False, followlinks=False):
        current_path = Path(current)
        for name in files:
            path = current_path / name
            relative = PurePosixPath(path.relative_to(staging).as_posix())
            if not selected_file(relative):
                path.unlink()
        for name in directories:
            path = current_path / name
            relative = PurePosixPath(path.relative_to(staging).as_posix())
            if path.is_symlink():
                if not selected_file(relative):
                    path.unlink()
            elif not required_directory(relative):
                shutil.rmtree(path)


def resolve_selected_archive_members(
    members: tuple[ArchiveMember, ...],
    selected_members: tuple[str, ...],
) -> tuple[str, ...]:
    """Resolve UI selections to the exact archive entries sent to the backend.

    A selected directory includes every entry below it.  A selected file includes
    only that file.  The result keeps the archive's original order and rejects a
    selection if the archive changed after the contents window was opened.
    """
    if not selected_members:
        raise ValueError("個別展開する中身が選ばれていません。")
    selected_paths = tuple(_safe_member_path(value) for value in selected_members)
    archived = tuple((member, _safe_member_path(member.path)) for member in members)
    available = {path for _member, path in archived}
    unavailable = [
        value
        for value, path in zip(selected_members, selected_paths, strict=True)
        if path not in available
    ]
    if unavailable:
        raise OSError(
            "個別選択後に圧縮ファイルの中身が変化しました: "
            + ", ".join(unavailable[:3])
        )
    resolved = tuple(
        member.path
        for member, path in archived
        if any(path == choice or choice in path.parents for choice in selected_paths)
    )
    if not resolved:
        raise ValueError("個別展開する中身を圧縮ファイル内で確認できませんでした。")
    return resolved


def revalidate_extract_plan(plan: ExtractPlan) -> None:
    select_archive_backend(plan.backend_id)
    for item in plan.extractions:
        if not item.archive.is_file() or item.archive.is_symlink():
            raise FileNotFoundError(f"圧縮ファイルが変化しました: {item.archive}")
        if not item.destination.is_dir():
            raise NotADirectoryError(f"解凍先が見つかりません: {item.destination}")
        if path_entry_exists(item.output):
            raise FileExistsError(f"プレビュー後に出力先が使用されました: {item.output}")


def validate_archive_members(members: tuple[ArchiveMember, ...]) -> None:
    """Reject paths and links that could leave the caller-owned staging root."""
    for member in members:
        relative = _safe_member_path(member.path)
        if member.link_target is not None:
            target = member.link_target.replace("\\", "/")
            if "\x00" in target or target.startswith("/") or _WINDOWS_DRIVE.match(target):
                raise ValueError(f"外部を指すリンクを含むため解凍できません: {member.path}")
            combined = relative.parent.joinpath(PurePosixPath(target))
            if _escapes_root(combined.parts):
                raise ValueError(f"外部を指すリンクを含むため解凍できません: {member.path}")


def validate_extracted_tree(staging: Path) -> None:
    """Verify the real output tree before it is promoted to its final name."""
    root = staging.resolve()
    for current, directories, files in os.walk(staging, followlinks=False):
        for name in [*directories, *files]:
            path = Path(current) / name
            mode = path.lstat().st_mode
            if stat.S_ISLNK(mode):
                resolved = path.resolve(strict=False)
                if not resolved.is_relative_to(root):
                    raise ValueError(f"解凍先の外部を指すリンクを検出しました: {path}")
            elif not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
                raise ValueError(f"未対応の特殊ファイルを検出しました: {path}")


def _safe_member_path(value: str) -> PurePosixPath:
    normalized = value.replace("\\", "/")
    if "\x00" in normalized or normalized.startswith("/") or _WINDOWS_DRIVE.match(normalized):
        raise ValueError(f"危険な格納パスを含むため解凍できません: {value}")
    path = PurePosixPath(normalized)
    if _escapes_root(path.parts):
        raise ValueError(f"危険な格納パスを含むため解凍できません: {value}")
    return path


def _escapes_root(parts: tuple[str, ...]) -> bool:
    depth = 0
    for part in parts:
        if part in {"", "."}:
            continue
        if part == "..":
            depth -= 1
            if depth < 0:
                return True
        else:
            depth += 1
    return False


def _next_output(candidate: Path, reserved: set[Path]) -> Path:
    if not path_entry_exists(candidate) and candidate not in reserved:
        return candidate
    counter = 1
    while True:
        output = candidate.with_name(f"{candidate.name} ({counter})")
        if not path_entry_exists(output) and output not in reserved:
            return output
        counter += 1


_WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:")
