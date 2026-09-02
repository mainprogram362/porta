from pathlib import Path

import pytest

from apps.media_tools.media_information.path_actions import direct_children_for_selected_folders


def test_direct_child_expansion_replaces_only_selected_real_folders(tmp_path: Path):
    root = tmp_path / "root"
    root.mkdir()
    first = root / "alpha.txt"
    first.write_text("a", encoding="utf-8")
    folder = root / "folder"
    folder.mkdir()
    nested = folder / "nested.txt"
    nested.write_text("nested", encoding="utf-8")
    unrelated = tmp_path / "unrelated.txt"
    unrelated.write_text("u", encoding="utf-8")

    children, folder_count = direct_children_for_selected_folders([root, unrelated], "")

    assert folder_count == 1
    assert children == (first, folder)
    assert nested not in children


def test_direct_child_expansion_supports_same_name_conditions_as_file_manager(tmp_path: Path):
    root = tmp_path / "root"
    root.mkdir()
    kept = root / "video-01.mp4"
    kept.write_text("v", encoding="utf-8")
    skipped = root / "video-skip.mp4"
    skipped.write_text("v", encoding="utf-8")
    (root / "folder").mkdir()

    children, _count = direct_children_for_selected_folders(
        [root], "video,-skip", mode="contains", item_kind="file"
    )
    assert children == (kept,)

    with pytest.raises(ValueError, match="正規表現"):
        direct_children_for_selected_folders([root], "[", mode="regex")
