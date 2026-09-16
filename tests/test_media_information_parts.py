"""Integration checks for pathless parts inside Media Information."""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QMenu, QTableWidget
from PySide6.QtCore import QEvent, QTimer, Qt

from apps.media_tools.media_information.window import MediaInformationScreen
from apps.media_tools.media_information.details_dialog import _selected_table_text
from apps.media_tools.media_information.workflow import mpv_highlight_time_text
from media.catalog import CatalogAttribute
from media.file_attributes import catalog_record_from_media_item
from media.file_attributes import media_item_token
from media.ledger import (
    MediaPart,
    MediaPartsDocument,
    create_parts,
    load_parts,
    resolve_new_parts_path,
)
from media.review_patch import MediaReviewPatch, plan_review_patch_merge, review_patch_entry
from media.single_field_patch import MediaSingleFieldPatch, plan_single_field_patch_merge, single_field_patch_entry


def _parts_document(name: str) -> MediaPartsDocument:
    return MediaPartsDocument(
        (
            MediaPart(
                (
                    CatalogAttribute("file.name.observed", name, "text", "filesystem"),
                    CatalogAttribute("title.official", "作品A", "text", "user"),
                )
            ),
        )
    )


def test_identical_bracket_times_are_stored_as_one_highlight_point() -> None:
    assert mpv_highlight_time_text(23 * 60 + 12.345, 23 * 60 + 12.345) == "23:12.345"
    assert mpv_highlight_time_text(23 * 60 + 12.345, 23 * 60 + 12.678) == (
        "23:12.345-23:12.678"
    )


def test_filename_sort_reorders_the_visible_candidates_and_next_output_order(tmp_path: Path):
    QApplication.instance() or QApplication([])
    later = tmp_path / "z-last.mp4"
    first = tmp_path / "a-first.mp4"
    later.write_bytes(b"test")
    first.write_bytes(b"test")
    screen = MediaInformationScreen(lambda: None)
    try:
        screen.path_input.append_items([str(later), str(first)])
        screen.confirm_all_paths()

        screen._sort_candidates_by_file_name(ascending=True)

        assert [item.path.name for item in screen._source_items if item.path is not None] == [
            "a-first.mp4",
            "z-last.mp4",
        ]
        assert [part.attributes[0].value for part in screen._parts_for_current_items()] == [
            "a-first.mp4",
            "z-last.mp4",
        ]
        assert [screen.path_input.item_token(screen.path_input._tree.topLevelItem(index)) for index in range(2)] == [
            media_item_token(screen._source_items[0]),
            media_item_token(screen._source_items[1]),
        ]
    finally:
        screen.close()


def test_parts_directory_output_uses_unique_new_files(tmp_path: Path):
    first = resolve_new_parts_path(tmp_path)
    assert first == tmp_path / "media_parts.json"
    create_parts(first, _parts_document("one.mp4"))

    assert resolve_new_parts_path(tmp_path) == tmp_path / "media_parts (1).json"


def test_parts_json_becomes_a_pathless_candidate_without_inventing_a_path(tmp_path: Path):
    QApplication.instance() or QApplication([])
    source = tmp_path / "parts.json"
    create_parts(source, _parts_document("clip.mp4"))
    screen = MediaInformationScreen(lambda: None)
    try:
        screen.path_input.append_items([str(source)])
        screen.confirm_all_paths()

        assert screen._paths_locked
        assert len(screen._source_items) == 1
        item = screen._source_items[0]
        assert item.path is None
        assert item.origin == "parts_json"
        values = {attribute.key: attribute.value for attribute in catalog_record_from_media_item(item).attributes}
        assert values["file.name.observed"] == "clip.mp4"
        assert "file.path" not in values
        assert screen._candidate_source_details[item.session_token][0] == "parts.json・1件目"
    finally:
        screen.close()


def test_same_workspace_locks_as_explicit_browse_or_edit_mode(tmp_path: Path):
    QApplication.instance() or QApplication([])
    media = tmp_path / "clip.mp4"
    media.write_bytes(b"test")
    screen = MediaInformationScreen(lambda: None)
    try:
        assert screen.confirm_browse_button.text() == "閲覧モード（読み取り専用）で確定"
        assert screen.confirm_edit_button.text() == "編集モード（編集内容を保存可能）で確定"

        screen.path_input.append_items([str(media)])
        screen.confirm_all_paths_read_only()

        assert screen._access_mode == "browse"
        assert screen.operation_box.isHidden()
        assert screen.final_actions_box.isHidden()
        assert screen.export_path_input.isHidden()
        assert not screen.browse_tools_box.isHidden()

        screen.unlock_confirmed_paths()
        screen.confirm_all_paths()

        assert screen._access_mode == "edit"
        assert not screen.operation_box.isHidden()
        assert not screen.final_actions_box.isHidden()
        assert not screen.export_path_input.isHidden()
        assert not screen.browse_tools_box.isHidden()
    finally:
        screen.close()


def test_single_loaded_parts_json_auto_fills_and_enables_only_its_overwrite(
    tmp_path: Path, monkeypatch
):
    QApplication.instance() or QApplication([])
    source = tmp_path / "parts.json"
    create_parts(source, _parts_document("clip.mp4"))
    extra = tmp_path / "extra.mp4"
    extra.write_bytes(b"extra")
    screen = MediaInformationScreen(lambda: None)
    try:
        screen.path_input.append_items([str(source), str(extra)])
        screen.confirm_all_paths()

        assert len(screen._loaded_json_sources) == 1
        assert screen.export_path_input.text() == str(source)
        assert screen.parts_overwrite_button.isEnabled()
        assert screen._new_parts_output_path() != source
        assert not screen._new_parts_output_path().exists()

        monkeypatch.setattr(screen, "_confirm_overwrite", lambda *_args, **_kwargs: True)
        screen.request_parts_overwrite()
        assert len(load_parts(source).parts) == 2

        screen.export_path_input.setText(str(tmp_path / "different.json"))
        assert not screen.parts_overwrite_button.isEnabled()
    finally:
        screen.close()


def test_multiple_loaded_json_files_do_not_enable_overwrite_or_auto_fill(tmp_path: Path):
    QApplication.instance() or QApplication([])
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    create_parts(first, _parts_document("first.mp4"))
    create_parts(second, _parts_document("second.mp4"))
    screen = MediaInformationScreen(lambda: None)
    try:
        screen.path_input.append_items([str(first), str(second)])
        screen.confirm_all_paths()

        assert len(screen._loaded_json_sources) == 2
        assert screen.export_path_input.text() == ""
        assert not screen.parts_overwrite_button.isEnabled()
    finally:
        screen.close()


def test_single_json_auto_fill_is_unconditional_but_does_not_write_automatically(tmp_path: Path):
    QApplication.instance() or QApplication([])
    source = tmp_path / "parts.json"
    create_parts(source, _parts_document("clip.mp4"))
    screen = MediaInformationScreen(lambda: None)
    try:
        screen.path_input.append_items([str(source)])
        screen.confirm_all_paths()

        assert screen.export_path_input.text() == str(source)
        assert screen.parts_overwrite_button.isEnabled()
        assert len(load_parts(source).parts) == 1
    finally:
        screen.close()


def test_locked_candidate_can_be_removed_without_touching_its_source_file(tmp_path: Path):
    QApplication.instance() or QApplication([])
    media = tmp_path / "clip.mp4"
    media.write_bytes(b"test")
    screen = MediaInformationScreen(lambda: None)
    try:
        screen.path_input.append_items([str(media)])
        screen.confirm_all_paths()

        row = screen.path_input._tree.topLevelItem(0)
        screen.remove_candidate(screen._source_items[0], row)

        assert media.exists()
        assert screen._paths_locked
        assert screen._source_items == []
        assert screen._items == []
        assert screen.path_input.items() == []
    finally:
        screen.close()


def test_item_details_open_in_a_detached_non_modal_window(tmp_path: Path):
    app = QApplication.instance() or QApplication([])
    media = tmp_path / "clip.mp4"
    media.write_bytes(b"test")
    screen = MediaInformationScreen(lambda: None)
    dialog = None
    try:
        screen.path_input.append_items([str(media)])
        screen.confirm_all_paths()

        dialog = screen.show_item_details(screen._source_items[0])
        app.processEvents()
        assert dialog.parentWidget() is None
        assert not dialog.isModal()
        assert dialog.isVisible()
        table = dialog.findChild(QTableWidget)
        assert table is not None
        assert table.contextMenuPolicy() == Qt.ContextMenuPolicy.CustomContextMenu
        table.setCurrentCell(0, 1)
        assert _selected_table_text(table) == table.item(0, 1).text()
        assert {button.text() for button in dialog.findChildren(type(screen.apply_button))} >= {
            "選択セルをコピー",
            "選択セルを展開…",
        }

        screen.close()
        app.processEvents()
        assert dialog.isVisible()
    finally:
        if dialog is not None:
            dialog.close()
        screen.close()


def test_locked_candidates_have_select_all_and_clear_all_buttons(tmp_path: Path):
    QApplication.instance() or QApplication([])
    first = tmp_path / "first.mp4"
    second = tmp_path / "second.mp4"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    screen = MediaInformationScreen(lambda: None)
    try:
        screen.path_input.append_items([str(first), str(second)])
        screen.confirm_all_paths()

        assert screen.select_all_candidates_button.isVisibleTo(screen.locked_tools_box)
        assert screen.clear_candidate_selection_button.isVisibleTo(screen.locked_tools_box)
        assert len(screen.path_input.selected_item_tokens()) == 2

        screen.clear_candidate_selection_button.click()
        assert screen.path_input.selected_item_tokens() == []
        assert len(screen.path_input.items()) == 2

        screen.select_all_candidates_button.click()
        assert len(screen.path_input.selected_item_tokens()) == 2
    finally:
        screen.close()


def test_selecting_a_locked_candidate_does_not_redraw_the_entire_table(tmp_path: Path):
    QApplication.instance() or QApplication([])
    first = tmp_path / "first.mp4"
    second = tmp_path / "second.mp4"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    screen = MediaInformationScreen(lambda: None)
    try:
        screen.path_input.append_items([str(first), str(second)])
        screen.confirm_all_paths()
        redraws: list[bool] = []
        screen._refresh_candidate_table = lambda: redraws.append(True)

        row = screen.path_input._tree.topLevelItem(0)
        screen._select_individual_row(row, 1)
        screen.path_input._set_item_selected(row, False)

        assert redraws == []
    finally:
        screen.close()


def test_media_operation_exposes_only_supported_operation_kinds_and_blocks_wheel_input():
    QApplication.instance() or QApplication([])
    screen = MediaInformationScreen(lambda: None)
    try:
        assert [value for _label, value in screen._allowed_operation_choices("title.official")] == [
            "replace", "clear"
        ]
        assert [value for _label, value in screen._allowed_operation_choices("classification.tag")] == [
            "replace", "append", "clear"
        ]
        assert [value for _label, value in screen._allowed_operation_choices("source.uploader")] == [
            "replace", "append", "clear"
        ]
        assert [value for _label, value in screen._allowed_operation_choices("media.highlights")] == ["clear"]
        assert [value for _label, value in screen._allowed_operation_choices("custom.note")] == [
            "replace", "append", "clear", "remove"
        ]
        assert screen.eventFilter(screen.operation_kind_combo, QEvent(QEvent.Type.Wheel))
        assert not screen.eventFilter(screen.path_input._tree, QEvent(QEvent.Type.Wheel))
    finally:
        screen.close()


def test_mpv_video_linking_requires_every_video_but_allows_unlinked_candidates(tmp_path: Path):
    QApplication.instance() or QApplication([])
    source = tmp_path / "parts.json"
    create_parts(
        source,
        MediaPartsDocument(
            (
                MediaPart((CatalogAttribute("file.name.observed", "first.mp4", "text", "filesystem"),)),
                MediaPart((CatalogAttribute("file.name.observed", "second.mp4", "text", "filesystem"),)),
            )
        ),
    )
    videos = tmp_path / "videos"
    videos.mkdir()
    first = videos / "first.mp4"
    second = videos / "second.mp4"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    screen = MediaInformationScreen(lambda: None)
    try:
        screen.path_input.append_items([str(source)])
        screen.confirm_all_paths()

        links = screen._exact_mpv_video_link_map(screen._source_items, (second, first))
        assert links[media_item_token(screen._source_items[0])] == first
        assert links[media_item_token(screen._source_items[1])] == second
        with pytest.raises(ValueError, match="左側候補に同じファイル名"):
            screen._exact_mpv_video_link_map((screen._source_items[0], screen._source_items[0]), (first,))
        partial_links = screen._exact_mpv_video_link_map(screen._source_items, (first,))
        assert partial_links == {media_item_token(screen._source_items[0]): first}
        extra = videos / "extra.mp4"
        extra.write_bytes(b"extra")
        with pytest.raises(ValueError, match="一致しないファイル名"):
            screen._exact_mpv_video_link_map(screen._source_items, (first, extra))
        duplicate_dir = tmp_path / "other-videos"
        duplicate_dir.mkdir()
        duplicate_first = duplicate_dir / "first.mp4"
        duplicate_first.write_bytes(b"duplicate")
        with pytest.raises(ValueError, match="右側動画に同じファイル名"):
            screen._exact_mpv_video_link_map(screen._source_items, (first, duplicate_first))

        screen._mpv_linked_paths = links
        screen._refresh_mpv_link_display()
        assert "（ファイル連携済み）" in screen.path_input._tree.topLevelItem(0).text(1)
        assert screen.play_mpv_playlist_button.isEnabled()
        launched: list[tuple[Path, ...]] = []
        screen._launch_mpv_playlist = lambda paths, **_kwargs: launched.append(paths)
        screen.play_confirmed_items_with_mpv()
        assert launched == [(first, second)]
    finally:
        screen.close()


def test_mpv_video_linker_button_opens_the_exact_matching_dialog(tmp_path: Path):
    app = QApplication.instance() or QApplication([])
    media = tmp_path / "clip.mp4"
    media.write_bytes(b"video")
    screen = MediaInformationScreen(lambda: None)
    try:
        screen.path_input.append_items([str(media)])
        screen.confirm_all_paths()
        opened: list[str] = []

        def close_linker() -> None:
            for widget in app.topLevelWidgets():
                if widget.windowTitle() == "mpv連携動画をファイル名で紐付け":
                    opened.append(widget.windowTitle())
                    widget.reject()

        QTimer.singleShot(20, close_linker)
        screen.open_mpv_video_linker()
        assert opened == ["mpv連携動画をファイル名で紐付け"]
    finally:
        screen.close()


def test_review_patch_can_safely_merge_into_pathless_parts_candidates(tmp_path: Path):
    QApplication.instance() or QApplication([])
    source = tmp_path / "parts.json"
    create_parts(
        source,
        MediaPartsDocument(
            (
                MediaPart(
                    (
                        CatalogAttribute("file.name.observed", "clip.mp4", "text", "filesystem"),
                        CatalogAttribute("classification.tag", ["既存"], "text_list", "user"),
                    )
                ),
            )
        ),
    )
    screen = MediaInformationScreen(lambda: None)
    try:
        screen.path_input.append_items([str(source)])
        screen.confirm_all_paths()
        patch = MediaReviewPatch(
            (
                review_patch_entry(
                    "clip.mp4",
                    (
                        CatalogAttribute("review.score", 0.9, "number", "mpv_review"),
                        CatalogAttribute("classification.tag", "追加", "text", "mpv_review"),
                        CatalogAttribute(
                            "media.highlights", [{"time": "01:23", "comment": ""}], "highlights", "mpv_review"
                        ),
                    ),
                ),
            )
        )
        plan = plan_review_patch_merge(screen._parts_for_current_items(), patch)
        assert plan.is_safe
        screen._review_patch = patch
        screen._review_merge_plan = plan
        screen._review_merge_items_snapshot = tuple(screen._items)
        screen._apply_verified_review_patch_merge()

        values = {
            attribute.key: attribute.value
            for attribute in catalog_record_from_media_item(screen._items[0]).attributes
        }
        assert values["review.score"] == 0.9
        assert values["classification.tag"] == ["既存", "追加"]
        assert values["media.highlights"] == [{"time": "01:23", "comment": ""}]
        assert screen._items[0].path is None
    finally:
        screen.close()


def test_text_workspace_uses_filename_by_default_and_single_field_patch_updates_only_that_field(tmp_path: Path):
    QApplication.instance() or QApplication([])
    media = tmp_path / "owarai34-1.mp4"
    media.write_bytes(b"test")
    screen = MediaInformationScreen(lambda: None)
    try:
        screen.path_input.append_items([str(media)])
        screen.confirm_all_paths()

        rows = screen._text_workspace_rows()
        assert rows[0].identifier == "owarai34-1.mp4"
        assert rows[0].source_values["__file_name_stem__"] == "owarai34-1"
        assert rows[0].source_values["__file_name_full__"] == "owarai34-1.mp4"

        patch = MediaSingleFieldPatch(
            "title.official",
            (single_field_patch_entry("owarai34-1.mp4", "吉本第35回お笑いライブ第34回part1"),),
        )
        plan = plan_single_field_patch_merge(screen._parts_for_current_items(), patch)
        screen._single_field_patch = patch
        screen._single_field_merge_plan = plan
        screen._single_field_merge_items_snapshot = tuple(screen._items)
        screen._apply_verified_single_field_patch_merge()

        values = {
            attribute.key: attribute.value
            for attribute in catalog_record_from_media_item(screen._items[0]).attributes
        }
        assert values["title.official"] == "吉本第35回お笑いライブ第34回part1"
        assert values["file.name.observed"] == "owarai34-1.mp4"
    finally:
        screen.close()


def test_shared_editor_can_apply_an_operation_to_the_selected_one_item(tmp_path: Path):
    QApplication.instance() or QApplication([])
    media = tmp_path / "clip.mp4"
    media.write_bytes(b"test")
    screen = MediaInformationScreen(lambda: None)
    try:
        screen.path_input.append_items([str(media)])
        screen.confirm_all_paths()

        assert not hasattr(screen, "individual_source_value")
        assert screen.apply_button.text() == "対象へ反映"
        assert screen.operation_target_combo.currentData() == "selected"
        assert [screen.operation_target_combo.itemData(index) for index in range(3)] == [
            "selected", "checked", "all"
        ]
        assert screen.path_input.selected_item_tokens() == [
            screen.path_input.item_token(screen.path_input._tree.topLevelItem(0))
        ]
        selection_button = screen.path_input._tree.itemWidget(
            screen.path_input._tree.topLevelItem(0), 0
        )
        assert selection_button.isEnabled()
        selection_button.click()

        screen.operation_target_combo.setCurrentIndex(
            screen.operation_target_combo.findData("selected")
        )
        assert not screen.path_input.selected_item_tokens()
        selection_button.click()

        row = screen.path_input._tree.topLevelItem(0)
        screen._select_individual_row(row, 1)
        screen.operation_field_scope_combo.setCurrentIndex(
            screen.operation_field_scope_combo.findData("all")
        )
        screen.operation_field_combo.setCurrentIndex(
            screen.operation_field_combo.findData("group.name")
        )
        screen.operation_value_input.setText("編集後のグループ")
        screen.apply_common_operation()

        assert "項目: グループ名" in screen.selected_field_target_label.text()
        assert screen.selected_field_source_value.text() == "（空欄）"
        assert screen.selected_field_planned_value.text() == "編集後のグループ"
        assert screen._applied_operations[-1].scope == "individual"
        assert screen.undo_button.isEnabled()

        screen.undo_last_edit()

        assert screen.selected_field_planned_value.text() == "（空欄）"
        assert not screen._applied_operations
        assert not screen.undo_button.isEnabled()
    finally:
        screen.close()


def test_shared_editor_appends_uploader_aliases_as_distinct_values(tmp_path: Path):
    QApplication.instance() or QApplication([])
    media = tmp_path / "clip.mp4"
    media.write_bytes(b"test")
    screen = MediaInformationScreen(lambda: None)
    try:
        screen.path_input.append_items([str(media)])
        screen.confirm_all_paths()
        screen._select_individual_row(screen.path_input._tree.topLevelItem(0), 1)
        field_index = screen.operation_field_combo.findData("source.uploader")
        screen.operation_field_combo.setCurrentIndex(field_index)
        assert "追加可" in screen.operation_field_combo.currentText()
        assert screen.operation_value_input.placeholderText() == "投稿者名・別名義を1回に1件入力"

        screen.operation_kind_combo.setCurrentIndex(
            screen.operation_kind_combo.findData("replace")
        )
        screen.operation_value_input.setText("現在の名義")
        screen.apply_common_operation()
        screen.operation_kind_combo.setCurrentIndex(
            screen.operation_kind_combo.findData("append")
        )
        screen.operation_value_input.setText("以前の名義")
        screen.apply_common_operation()
        screen.operation_value_input.setText("以前の名義")
        screen.apply_common_operation()

        values = {
            attribute.key: attribute.value
            for attribute in catalog_record_from_media_item(screen._items[0]).attributes
        }
        assert values["source.uploader"] == ["現在の名義", "以前の名義"]
        assert screen.selected_field_planned_value.text() == "現在の名義 / 以前の名義"
    finally:
        screen.close()


def test_edit_value_enter_applies_to_the_selected_target(tmp_path: Path):
    QApplication.instance() or QApplication([])
    media = tmp_path / "clip.mp4"
    media.write_bytes(b"test")
    screen = MediaInformationScreen(lambda: None)
    try:
        screen.path_input.append_items([str(media)])
        screen.confirm_all_paths()
        screen._select_individual_row(screen.path_input._tree.topLevelItem(0), 1)
        screen.operation_field_combo.setCurrentIndex(
            screen.operation_field_combo.findData("group.name")
        )
        screen.operation_value_input.setText("Enterで反映したグループ")

        screen.operation_value_input.returnPressed.emit()

        values = {
            attribute.key: attribute.value
            for attribute in catalog_record_from_media_item(screen._items[0]).attributes
        }
        assert values["group.name"] == "Enterで反映したグループ"
        assert screen.operation_value_input.text() == ""
    finally:
        screen.close()


def test_video_double_click_starts_the_confirmed_playlist_from_that_candidate(tmp_path: Path):
    QApplication.instance() or QApplication([])
    first = tmp_path / "first.mp4"
    second = tmp_path / "second.mkv"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    screen = MediaInformationScreen(lambda: None)
    try:
        screen.path_input.append_items([str(first), str(second)])
        screen.confirm_all_paths()
        launched = []
        screen._launch_mpv_playlist = lambda paths, **options: launched.append((paths, options))
        row = screen.path_input._tree.topLevelItem(1)

        screen.path_input.itemDoubleClicked.emit(row, 1)

        assert screen._individual_row is row
        assert launched == [((first, second), {"playlist_start_index": 1})]
        menu = QMenu()
        screen._add_read_item_context_actions(menu, row)
        labels = [action.text() for action in menu.actions()]
        assert "ここからmpvで再生" in labels
        assert "mpvでこの1件を再生" not in labels
    finally:
        screen.close()


def test_linked_video_double_click_uses_the_link_but_plain_files_do_not_autoplay(tmp_path: Path):
    QApplication.instance() or QApplication([])
    source = tmp_path / "parts.json"
    create_parts(source, _parts_document("linked.mp4"))
    linked = tmp_path / "linked.mp4"
    linked.write_bytes(b"video")
    screen = MediaInformationScreen(lambda: None)
    try:
        screen.path_input.append_items([str(source)])
        screen.confirm_all_paths()
        row = screen.path_input._tree.topLevelItem(0)
        launched = []
        screen._launch_mpv_playlist = lambda paths, **options: launched.append((paths, options))

        screen.path_input.itemDoubleClicked.emit(row, 1)
        assert launched == []

        screen._mpv_linked_paths[media_item_token(screen._source_items[0])] = linked
        screen.path_input.itemDoubleClicked.emit(row, 1)
        assert launched == [((linked,), {"playlist_start_index": 0})]
    finally:
        screen.close()


def test_locked_editor_keeps_the_target_list_in_one_stable_position(tmp_path: Path):
    QApplication.instance() or QApplication([])
    media = tmp_path / "clip.mp4"
    media.write_bytes(b"test")
    screen = MediaInformationScreen(lambda: None)
    try:
        assert not hasattr(screen, "confirm_selected_button")
        assert not hasattr(screen, "operation_help_label")
        assert not hasattr(screen, "common_operation_preview_label")
        assert screen.source_box.title() == "対象一覧"
        assert screen.operation_box.title() == "編集"
        assert screen.source_layout.indexOf(screen.path_input) == 0

        screen.path_input.append_items([str(media)])
        screen.confirm_all_paths()

        assert screen.source_layout.indexOf(screen.path_input) == 0
        screen._select_individual_row(screen.path_input._tree.topLevelItem(0), 1)
        screen.operation_field_scope_combo.setCurrentIndex(
            screen.operation_field_scope_combo.findData("all")
        )
        screen.operation_field_combo.setCurrentIndex(
            screen.operation_field_combo.findData("group.name")
        )
        screen.operation_value_input.setText("全体編集用グループ")
        assert "全体編集用グループ" in screen.operation_draft_preview.text()

        screen.unlock_confirmed_paths()

        assert screen.source_layout.indexOf(screen.path_input) == 0
    finally:
        screen.close()
