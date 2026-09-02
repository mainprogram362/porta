"""YouTube adapter for the shared attribute-based media catalog."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

from .catalog import (
    CATALOG_SCHEMA_VERSION,
    DEFAULT_CATALOG_FILENAME,
    CatalogAttribute,
    CatalogRecordInput,
    CatalogWriteReport,
    add_catalog_records,
    resolve_catalog_path,
)


@dataclass(frozen=True)
class CatalogVideo:
    """A successful YouTube download represented without a permanent path."""

    title: str
    source_url: str
    source_video_id: str
    upload_date: str
    channel_name: str
    source_kind: str
    downloaded_at: str
    output_path: Path | None
    size_bytes: int | None
    format_selector: str


def write_video_catalog(path: Path, videos: Iterable[CatalogVideo]) -> CatalogWriteReport:
    """Add YouTube-derived facts to the common media catalog.

    Paths are deliberately not stored. When available, only the observed
    basename is retained as a searchable alias.
    """
    return add_catalog_records(path, (_record_from_video(video) for video in videos))


def _record_from_video(video: CatalogVideo) -> CatalogRecordInput:
    candidates = (
        _text("title.observed", video.title, "youtube"),
        _text("source.youtube.video_id", video.source_video_id, "youtube"),
        _text("source.youtube.url", video.source_url, "youtube"),
        _text("source.youtube.channel", video.channel_name, "youtube"),
        _text("source.youtube.upload_date", video.upload_date, "youtube"),
        _text("source.youtube.kind", video.source_kind, "youtube"),
        _text("download.observed_at", video.downloaded_at, "youtube"),
        _text("download.format_selector", video.format_selector, "youtube"),
    )
    attributes = [attribute for attribute in candidates if attribute is not None]
    if video.output_path is not None:
        attributes.append(_text("file.name.observed", video.output_path.name, "filesystem"))
    if video.size_bytes is not None:
        attributes.append(
            CatalogAttribute("file.size_bytes.observed", video.size_bytes, "number", "filesystem")
        )
    return CatalogRecordInput(
        attributes=tuple(attribute for attribute in attributes if attribute is not None),
        identity=("source.youtube.video_id", video.source_video_id),
    )


def _text(key: str, value: str, source: str) -> CatalogAttribute | None:
    text = value.strip()
    return CatalogAttribute(key, text, "text", source) if text else None


def catalog_timestamp() -> str:
    """Return a local ISO timestamp for an explicit successful download."""
    return datetime.now().astimezone().isoformat(timespec="seconds")


__all__ = [
    "CATALOG_SCHEMA_VERSION",
    "CatalogVideo",
    "CatalogWriteReport",
    "DEFAULT_CATALOG_FILENAME",
    "catalog_timestamp",
    "resolve_catalog_path",
    "write_video_catalog",
]
