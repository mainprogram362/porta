"""Checks for reusable batch operations outside any one app screen."""

from __future__ import annotations

from pathlib import Path
from zipfile import ZipFile

from foundation.batch_operations import batch_zip


def test_batch_zip_creates_readable_unique_archives(tmp_path: Path) -> None:
    source = tmp_path / "clip.txt"
    source.write_text("portable", encoding="utf-8")
    output = tmp_path / "archives"

    first = batch_zip([source], output)
    second = batch_zip([source], output)

    assert first == [output / "clip.zip"]
    assert second == [output / "clip (1).zip"]
    with ZipFile(first[0]) as archive:
        assert archive.read("clip.txt").decode("utf-8") == "portable"
