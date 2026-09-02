from __future__ import annotations

import json
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QAbstractItemView, QApplication
import pytest

from apps.media_tools.media_information.window import (
    MediaInformationScreen,
    _INDEPENDENT_FILE_MANAGERS,
)
from apps.media_tools.media_json_viewer.window import MediaJsonViewerScreen
from apps.media_tools.media_json_viewer.model import (
    FilterRule,
    MAXIMUM_MATCH_CANDIDATES,
    ViewerRecord,
    collect_path_candidates,
    is_video_path,
    load_viewer_records,
    match_paths,
    path_candidates,
    record_matches_rules,
)
from media.catalog import CatalogAttribute


def _record(index: int, name: str) -> ViewerRecord:
    return ViewerRecord(index, (CatalogAttribute("file.name.observed", name, "text", "filesystem"),))


def test_loads_media_information_catalog_without_changing_source(tmp_path: Path) -> None:
    source = tmp_path / "catalog.json"
    source.write_text(
        json.dumps(
            {
                "kind": "media_catalog",
                "media": [
                    {
                        "attributes": [
                            {"key": "file.name.observed", "value": "movie.mp4", "value_type": "text", "source": "filesystem"},
                            {"key": "review.score", "value": 8, "value_type": "number", "source": "user"},
                        ]
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    before = source.read_bytes()

    records = load_viewer_records(source)

    assert len(records) == 1
    assert records[0].title == "movie.mp4"
    assert records[0].score == 8
    assert source.read_bytes() == before


def test_path_matching_uses_exact_name_before_relaxed_name(tmp_path: Path) -> None:
    relaxed = tmp_path / "My Movie.mkv"
    exact = tmp_path / "my-movie.mp4"
    relaxed.write_bytes(b"video")
    exact.write_bytes(b"video")
    record = _record(3, "my-movie.mp4")

    assert match_paths((record,), (relaxed, exact)) == {3: exact}
    assert match_paths((_record(4, "my movie.webm"),), (relaxed, exact)) == {4: relaxed}


def test_path_matching_stops_when_the_workspace_has_duplicate_file_names(
    tmp_path: Path,
) -> None:
    candidate = tmp_path / "same.mp4"
    candidate.write_bytes(b"video")

    with pytest.raises(ValueError, match="一覧側に同じファイル名"):
        match_paths((_record(1, "same.mp4"), _record(2, "same.mp4")), (candidate,))


def test_path_folder_candidates_include_all_direct_files_and_are_limited(tmp_path: Path) -> None:
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "hidden.mp4").write_bytes(b"video")
    for index in range(MAXIMUM_MATCH_CANDIDATES + 2):
        (tmp_path / f"video-{index:04d}.mp4").write_bytes(b"video")
    (tmp_path / "note.txt").write_text("not a video", encoding="utf-8")

    candidates = path_candidates(tmp_path)

    assert len(candidates) == MAXIMUM_MATCH_CANDIDATES
    assert candidates[0].name == "note.txt"
    assert all(candidate.parent == tmp_path for candidate in candidates)


def test_path_candidates_accept_non_video_file(tmp_path: Path) -> None:
    text = tmp_path / "note.txt"
    text.write_text("x", encoding="utf-8")

    assert path_candidates(text) == (text.absolute(),)
    assert not is_video_path(text)


def test_collect_path_candidates_combines_multiple_files_and_folders(tmp_path: Path) -> None:
    first_directory = tmp_path / "first"
    second_directory = tmp_path / "second"
    first_directory.mkdir()
    second_directory.mkdir()
    first = first_directory / "alpha.txt"
    second = second_directory / "beta.bin"
    direct = tmp_path / "gamma.mp4"
    first.write_text("a", encoding="utf-8")
    second.write_bytes(b"b")
    direct.write_bytes(b"video")

    candidates = collect_path_candidates((first_directory, second_directory, direct))

    assert candidates == (first.absolute(), second.absolute(), direct.absolute())


def test_collect_path_candidates_stops_on_duplicate_file_names(tmp_path: Path) -> None:
    first_directory = tmp_path / "first"
    second_directory = tmp_path / "second"
    first_directory.mkdir()
    second_directory.mkdir()
    first = first_directory / "same.txt"
    second = second_directory / "same.txt"
    first.write_text("a", encoding="utf-8")
    second.write_text("b", encoding="utf-8")

    with pytest.raises(ValueError, match="同じファイル名"):
        collect_path_candidates((first_directory, second_directory))


def test_collect_path_candidates_deduplicates_the_same_path(tmp_path: Path) -> None:
    source = tmp_path / "same.txt"
    source.write_text("a", encoding="utf-8")

    assert collect_path_candidates((source, source)) == (source.absolute(),)


def test_rating_rules_use_the_user_facing_zero_to_ten_scale() -> None:
    high = ViewerRecord(
        1,
        (
            CatalogAttribute("file.name.observed", "high.mp4", "text", "filesystem"),
            CatalogAttribute("review.score", 0.8, "number", "user"),
            CatalogAttribute("classification.tag", ["音楽", "ライブ"], "text", "user"),
        ),
    )

    assert high.rating_text == "★8"
    assert record_matches_rules(high, (FilterRule("review.score", "at_least", "8"),))
    assert not record_matches_rules(high, (FilterRule("review.score", "at_least", "9"),))
    assert record_matches_rules(high, (FilterRule("classification.tag", "contains", "ライブ"),))


def test_retired_viewer_name_is_an_alias_of_the_unified_workspace() -> None:
    assert MediaJsonViewerScreen is MediaInformationScreen


def test_browse_mode_starts_checked_and_applies_check_to_blue_multi_selection(
    tmp_path: Path,
) -> None:
    QApplication.instance() or QApplication([])
    paths = tuple(tmp_path / name for name in ("first.mp4", "second.mp4", "third.mp4"))
    for path in paths:
        path.write_bytes(b"video")
    screen = MediaInformationScreen(lambda: None)
    screen.path_input.append_items(str(path) for path in paths)
    screen.confirm_all_paths_read_only()
    try:
        tree = screen.path_input._tree
        assert screen._access_mode == "browse"
        assert tree.selectionMode() == QAbstractItemView.SelectionMode.ExtendedSelection
        assert len(screen.path_input.selected_item_tokens()) == 3
        tree.clearSelection()
        tree.topLevelItem(0).setSelected(True)
        tree.topLevelItem(2).setSelected(True)

        screen._set_blue_selection_checked(False)

        checked = screen.path_input.selected_item_tokens()
        assert len(checked) == 1
        assert "second.mp4" in checked[0]
    finally:
        screen.close()


def test_only_visible_checked_linked_paths_open_in_independent_file_manager(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    first.write_text("first", encoding="utf-8")
    second.write_text("second", encoding="utf-8")
    screen = MediaInformationScreen(lambda: None)
    screen.path_input.append_items((str(first), str(second)))
    screen.confirm_all_paths_read_only()
    screen._filter_rules = (FilterRule("file.name.observed", "contains", "first"),)
    screen._refresh_browse_view()
    try:
        screen._send_checked_paths_to_file_manager()
        app.processEvents()
        manager = next(iter(_INDEPENDENT_FILE_MANAGERS))
        assert manager.search_results_input.items() == [str(first)]
    finally:
        for manager in tuple(_INDEPENDENT_FILE_MANAGERS):
            manager.close()
        app.processEvents()
        screen.close()


def test_mpv_playlist_uses_only_currently_visible_linked_videos(tmp_path: Path) -> None:
    QApplication.instance() or QApplication([])
    first = tmp_path / "a-first.mp4"
    non_video = tmp_path / "b-note.txt"
    second = tmp_path / "c-second.mkv"
    for path in (first, non_video, second):
        path.write_bytes(b"content")
    screen = MediaInformationScreen(lambda: None)
    screen.path_input.append_items((str(second), str(non_video), str(first)))
    screen.confirm_all_paths_read_only()
    try:
        screen.sort_combo.setCurrentIndex(screen.sort_combo.findData("name"))
        assert tuple(
            path
            for item in screen._visible_source_items()
            if (path := screen._mpv_path_for_item(item)) is not None
        ) == (first, second)
        assert screen.play_mpv_playlist_button.isEnabled()

        screen._filter_rules = (FilterRule("file.name.observed", "contains", "second"),)
        screen._refresh_browse_view()

        assert tuple(
            path
            for item in screen._visible_source_items()
            if (path := screen._mpv_path_for_item(item)) is not None
        ) == (second,)
    finally:
        screen.close()


def test_browse_mode_does_not_stage_edits_or_write_the_source_json(tmp_path: Path) -> None:
    QApplication.instance() or QApplication([])
    source = tmp_path / "catalog.json"
    source.write_text(
        json.dumps(
            {
                "kind": "media_catalog",
                "media": [
                    {
                        "attributes": [
                            {
                                "key": "file.name.observed",
                                "value": "movie.mp4",
                                "value_type": "text",
                                "source": "filesystem",
                            },
                            {
                                "key": "review.score",
                                "value": 0.4,
                                "value_type": "number",
                                "source": "user",
                            },
                        ]
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    before = source.read_bytes()
    screen = MediaInformationScreen(lambda: None)
    screen.path_input.append_items((str(source),))
    screen.confirm_all_paths_read_only()
    try:
        original_items = tuple(screen._items)
        screen._set_item_rating(screen._source_items[0], 10)
        screen.apply_common_operation()

        assert screen._access_mode == "browse"
        assert tuple(screen._items) == original_items
        assert screen._items == screen._source_items
        assert screen.operation_box.isHidden()
        assert screen.export_path_input.isHidden()
        assert source.read_bytes() == before
    finally:
        screen.close()
