from pathlib import Path

from automation import open_target, paste_text, repeat_key, reveal_in_file_manager
from foundation.filesystem import ensure_unique_destination
from media import MediaDownloader


def test_ensure_unique_destination_adds_suffix_for_existing_path(tmp_path: Path):
    target = tmp_path / "report.txt"
    target.write_text("first", encoding="utf-8")

    unique = ensure_unique_destination(target)

    assert unique.name == "report (1).txt"


def test_ensure_unique_destination_does_not_overwrite_a_broken_symbolic_link(tmp_path: Path):
    target = tmp_path / "report.txt"
    target.symlink_to(tmp_path / "missing-target.txt")

    unique = ensure_unique_destination(target)

    assert unique.name == "report (1).txt"


def test_public_package_exports_are_exposed():
    assert callable(paste_text)
    assert callable(repeat_key)
    assert hasattr(MediaDownloader, "download_single")
    assert callable(open_target)
    assert callable(reveal_in_file_manager)
