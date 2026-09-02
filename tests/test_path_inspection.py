from pathlib import Path

from foundation.path import normalize_path
from foundation.path_inspection import inspect_path, inspect_paths


def test_inspect_path_reports_file_directory_and_missing_states(tmp_path: Path):
    file_path = tmp_path / "file.txt"
    file_path.write_text("content", encoding="utf-8")
    directory = tmp_path / "folder"
    directory.mkdir()
    missing = tmp_path / "missing"

    assert inspect_path(file_path).kind == "file"
    assert inspect_path(directory).kind == "directory"
    missing_info = inspect_path(missing)
    assert not missing_info.exists
    assert missing_info.kind == "missing"


def test_inspect_paths_keeps_input_order(tmp_path: Path):
    first = tmp_path / "first"
    second = tmp_path / "second"

    infos = inspect_paths([first, second])

    assert [info.path for info in infos] == [first.resolve(), second.resolve()]


def test_path_inspection_keeps_a_symbolic_link_as_its_own_path(tmp_path: Path):
    target = tmp_path / "target.txt"
    target.write_text("content", encoding="utf-8")
    link = tmp_path / "visible-link.txt"
    link.symlink_to(target)
    broken = tmp_path / "broken-link.txt"
    broken.symlink_to(tmp_path / "gone.txt")

    info = inspect_path(link)
    broken_info = inspect_path(broken)

    assert normalize_path(link) == link
    assert info.path == link
    assert info.kind == "symlink_file"
    assert info.exists
    assert info.link_target == target
    assert broken_info.path == broken
    assert broken_info.kind == "symlink_broken"
    assert not broken_info.exists
