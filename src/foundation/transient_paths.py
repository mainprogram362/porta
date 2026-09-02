"""One-shot in-memory path handoff between completed local tools.

This module intentionally has no file I/O. A receiving screen consumes the
paths once, so no path history remains after the handoff.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from .path import normalize_path


_pending_media_paths: tuple[str, ...] = ()
_pending_video_encode_paths: tuple[str, ...] = ()


def offer_media_paths(paths: Iterable[str | Path]) -> int:
    """Replace the one-shot media handoff with normalized, unique paths."""
    global _pending_media_paths
    values = tuple(dict.fromkeys(str(normalize_path(path)) for path in paths))
    _pending_media_paths = values
    return len(values)


def take_media_paths() -> tuple[str, ...]:
    """Consume and clear the pending media paths."""
    global _pending_media_paths
    values = _pending_media_paths
    _pending_media_paths = ()
    return values


def offer_video_encode_paths(paths: Iterable[str | Path]) -> int:
    """Replace the one-shot handoff for the video encoder with local paths."""
    global _pending_video_encode_paths
    values = tuple(dict.fromkeys(str(normalize_path(path)) for path in paths))
    _pending_video_encode_paths = values
    return len(values)


def take_video_encode_paths() -> tuple[str, ...]:
    """Consume and clear paths sent to the video encoder."""
    global _pending_video_encode_paths
    values = _pending_video_encode_paths
    _pending_video_encode_paths = ()
    return values
