from pathlib import Path

import pytest

from apps.file_tools.file_manager.search_workflow import (
    parse_query,
    search_direct_children,
    search_direct_children_from_roots,
    search_skip_two_levels_from_roots,
    select_paths_by_conditions,
)


def test_parse_query_separates_include_and_minus_exclude_terms():
    assert parse_query("report, -draft\n2026") == (("report", "2026"), ("draft",))


def test_current_list_selection_conditions_support_name_kind_and_exclusion(tmp_path: Path):
    keep = tmp_path / "keep-video.mp4"
    excluded = tmp_path / "sample-video.mp4"
    folder = tmp_path / "video-folder"
    keep.write_bytes(b"")
    excluded.write_bytes(b"")
    folder.mkdir()

    assert select_paths_by_conditions(
        (keep, excluded, folder), ".mp4, -sample", item_kind="file"
    ) == (keep,)


def test_current_list_selection_conditions_can_match_full_path_by_regex(tmp_path: Path):
    nested = tmp_path / "library"
    nested.mkdir()
    item = nested / "clip.mkv"
    item.write_bytes(b"")

    assert select_paths_by_conditions(
        (item,), r"library/.+\.mkv$", mode="regex", match_field="path"
    ) == (item,)


def test_search_direct_children_filters_by_partial_match_and_kind(tmp_path: Path):
    (tmp_path / "report-2026.txt").write_text("", encoding="utf-8")
    (tmp_path / "report-draft.txt").write_text("", encoding="utf-8")
    (tmp_path / "report-folder").mkdir()
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "report-hidden.txt").write_text("", encoding="utf-8")

    results = search_direct_children(
        str(tmp_path), "report, -draft", mode="contains", item_kind="file"
    )

    assert results == ((tmp_path / "report-2026.txt").resolve(),)


def test_search_direct_children_supports_regular_expressions(tmp_path: Path):
    (tmp_path / "image-01.png").write_text("", encoding="utf-8")
    (tmp_path / "image-final.png").write_text("", encoding="utf-8")

    results = search_direct_children(
        str(tmp_path), r"^image-\d+\.png$", mode="regex"
    )

    assert results == ((tmp_path / "image-01.png").resolve(),)


def test_search_direct_children_rejects_invalid_regular_expression(tmp_path: Path):
    with pytest.raises(ValueError, match="正規表現"):
        search_direct_children(str(tmp_path), "[", mode="regex")


def test_search_from_multiple_roots_combines_only_their_direct_children(tmp_path: Path):
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    first_root.mkdir()
    second_root.mkdir()
    (first_root / "first-result.txt").write_text("", encoding="utf-8")
    (second_root / "second-result.txt").write_text("", encoding="utf-8")
    nested = first_root / "nested"
    nested.mkdir()
    (nested / "hidden.txt").write_text("", encoding="utf-8")

    results = search_direct_children_from_roots(
        f"{first_root}\n{second_root}", "result", item_kind="file"
    )

    assert results == (
        (first_root / "first-result.txt").resolve(),
        (second_root / "second-result.txt").resolve(),
    )


def test_skip_search_ignores_root_files_and_stops_after_second_level(tmp_path: Path):
    (tmp_path / "match-at-root.txt").write_text("", encoding="utf-8")
    first = tmp_path / "match-first"
    first.mkdir()
    second = first / "match-second"
    second.mkdir()
    (first / "match-file.txt").write_text("", encoding="utf-8")
    third = second / "match-third"
    third.mkdir()

    results = search_skip_two_levels_from_roots(str(tmp_path), "match")

    assert results == (
        (first / "match-file.txt").resolve(),
        second.resolve(),
    )
