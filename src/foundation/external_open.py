"""Validated, one-shot path input from external desktop applications.

The receiver deliberately validates every selected entry before a PORTA
screen is opened.  A mixed valid/invalid selection is rejected as one unit so
the user never has to guess which subset was imported.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Iterable, Literal
from urllib.parse import unquote, urlparse

from .path import normalize_path
from .path_inspection import inspect_path


ExternalOpenTarget = Literal["choose", "file-manager", "media-organizer", "video-encoder"]
SUPPORTED_TARGETS: tuple[ExternalOpenTarget, ...] = (
    "choose",
    "file-manager",
    "media-organizer",
    "video-encoder",
)


@dataclass(frozen=True)
class ExternalPathIssue:
    value: str
    reason: str


@dataclass(frozen=True)
class ExternalOpenRequest:
    target: ExternalOpenTarget
    paths: tuple[Path, ...]


@dataclass(frozen=True)
class ExternalOpenIntent:
    """A validated, transient instruction selected inside PORTA."""

    target: Literal["file-manager", "media-organizer", "video-encoder"]
    action: str
    paths: tuple[Path, ...]


class ExternalOpenValidationError(ValueError):
    """Raised when even one externally supplied entry cannot be accepted."""

    def __init__(self, issues: Iterable[ExternalPathIssue]) -> None:
        self.issues = tuple(issues)
        super().__init__(self.summary())

    def summary(self) -> str:
        lines = [
            "外部アプリから受け取ったパスを開けませんでした。",
            "安全のため、正常なものを含めて今回は1件も取り込みません。",
            "",
        ]
        lines.extend(f"・{issue.reason}: {issue.value}" for issue in self.issues)
        return "\n".join(lines)


def _local_path(raw_value: str) -> tuple[Path | None, ExternalPathIssue | None]:
    value = os.fspath(raw_value)
    if not value:
        return None, ExternalPathIssue("（空の値）", "パスが空です")

    parsed = urlparse(value)
    if parsed.scheme:
        if parsed.scheme.casefold() != "file":
            return None, ExternalPathIssue(value, "ローカルファイル以外のURIです")
        if parsed.netloc not in {"", "localhost"}:
            return None, ExternalPathIssue(value, "別ホストを指すfile URIです")
        value = unquote(parsed.path)
        if not value:
            return None, ExternalPathIssue(raw_value, "file URIにパスがありません")

    return normalize_path(Path(value).expanduser()), None


def build_external_open_request(
    target: str, raw_paths: Iterable[str]
) -> ExternalOpenRequest:
    """Normalize, deduplicate and atomically validate external path input."""
    if target not in SUPPORTED_TARGETS:
        raise ValueError(f"未対応の外部連携先です: {target}")

    paths: list[Path] = []
    issues: list[ExternalPathIssue] = []
    seen: set[str] = set()
    for raw_value in raw_paths:
        path, conversion_issue = _local_path(raw_value)
        if conversion_issue is not None:
            issues.append(conversion_issue)
            continue
        assert path is not None
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        paths.append(path)

        info = inspect_path(path)
        if info.kind.startswith("symlink_"):
            reason = (
                "壊れたシンボリックリンクです"
                if info.kind == "symlink_broken"
                else "シンボリックリンクです"
            )
            issues.append(ExternalPathIssue(key, reason))
        elif info.kind == "missing":
            issues.append(ExternalPathIssue(key, "存在しません"))
        elif info.kind == "unavailable":
            issues.append(ExternalPathIssue(key, "状態を確認できません"))
        elif info.kind == "other":
            issues.append(ExternalPathIssue(key, "通常のファイル／フォルダではありません"))
        elif info.kind == "directory" and not os.access(path, os.R_OK | os.X_OK):
            issues.append(ExternalPathIssue(key, "フォルダを読み取る権限がありません"))
        elif info.kind == "file" and not os.access(path, os.R_OK):
            issues.append(ExternalPathIssue(key, "ファイルを読み取る権限がありません"))

    if not paths and not issues:
        issues.append(ExternalPathIssue("（選択なし）", "パスが指定されていません"))
    if issues:
        raise ExternalOpenValidationError(issues)
    return ExternalOpenRequest(target=target, paths=tuple(paths))  # type: ignore[arg-type]
