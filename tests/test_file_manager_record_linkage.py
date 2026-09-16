import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMenu

from apps.file_tools.file_manager.linked_record_rename import (
    LinkedRenameRule,
    build_linked_rename_preview,
    render_record_tokens,
)
from apps.file_tools.file_manager.record_linkage import link_paths_to_record_field
from apps.file_tools.file_manager.window import FileManagerScreen
from foundation.record_bundle import RecordBundle, RecordBundleRow


def _bundle() -> RecordBundle:
    return RecordBundle(
        "ライブ集",
        ("現在名", "タイトル", "サイズ"),
        (
            RecordBundleRow("row-1", ("20220502.mp4", "全国お笑い", "100mb")),
            RecordBundleRow("row-2", ("20230312.mp4", "春ライブ", "200mb")),
        ),
    )


def test_linkage_uses_only_one_selected_field_and_the_whole_basename(tmp_path: Path):
    exact = tmp_path / "20220502.mp4"
    stem_only = tmp_path / "20230312.txt"
    result = link_paths_to_record_field(
        _bundle(), (exact, stem_only), field_index=0
    )

    assert result.linked_paths == (exact,)
    assert result.links[0].row_identifier == "row-1"
    assert result.links[0].display_text == "項目1「現在名」→ レコード1"
    assert result.failures[0].source == stem_only
    assert result.failures[0].reason == "no_match"


def test_duplicate_record_values_fail_as_ambiguous(tmp_path: Path):
    duplicate = RecordBundle(
        "重複",
        ("名前", "値"),
        (
            RecordBundleRow("a", ("same", "one")),
            RecordBundleRow("b", ("same", "two")),
        ),
    )
    result = link_paths_to_record_field(
        duplicate, (tmp_path / "same",), field_index=0
    )

    assert not result.links
    assert result.failures[0].reason == "ambiguous"
    assert result.failures[0].matching_row_identifiers == ("a", "b")


def test_two_target_paths_cannot_claim_the_same_record(tmp_path: Path):
    first = tmp_path / "one" / "20220502.mp4"
    second = tmp_path / "two" / "20220502.mp4"
    result = link_paths_to_record_field(_bundle(), (first, second), field_index=0)

    assert not result.links
    assert [failure.reason for failure in result.failures] == [
        "multiple_paths",
        "multiple_paths",
    ]
    assert result.failures[0].matching_path_count == 2


def test_beginner_tokens_render_and_append_before_a_file_extension(tmp_path: Path):
    source = tmp_path / "20220502.mp4"
    source.write_text("video", encoding="utf-8")
    result = link_paths_to_record_field(_bundle(), (source,), field_index=0)
    link = result.links[0]

    assert render_record_tokens("_[title:[@2]]", link) == "_[title:全国お笑い]"
    preview = build_linked_rename_preview(
        result.links,
        LinkedRenameRule("append", "_[title:[@2]]"),
    )

    assert preview.plan is not None
    assert preview.plan.renames[0].output == tmp_path / "20220502_[title:全国お笑い].mp4"


def test_replace_and_advanced_regex_can_use_record_tokens(tmp_path: Path):
    source = tmp_path / "20220502.mp4"
    source.write_text("video", encoding="utf-8")
    links = link_paths_to_record_field(_bundle(), (source,), field_index=0).links

    replaced = build_linked_rename_preview(
        links, LinkedRenameRule("replace", "[@2]")
    )
    advanced = build_linked_rename_preview(
        links,
        LinkedRenameRule(
            "regex",
            r"\1_[@2]",
            regex_pattern=r"^(\d{4}).*$",
        ),
    )

    assert replaced.plan is not None
    assert replaced.plan.renames[0].output == tmp_path / "全国お笑い.mp4"
    assert advanced.plan is not None
    assert advanced.plan.renames[0].output == tmp_path / "2022_全国お笑い.mp4"


def test_file_manager_keeps_all_link_actions_in_one_submenu_and_migrates_links():
    app = QApplication.instance() or QApplication([])
    screen = FileManagerScreen(lambda: None)
    try:
        assert screen.show_record_bundle_button.text() == "対応表：なし"
        assert screen.show_record_bundle_button.isEnabled()  # Allows acquiring an existing document.
        screen.receive_record_bundle(_bundle())
        assert screen.show_record_bundle_button.text() == "対応表：あり（未紐づけ）"
        assert "対応表：ライブ集" in screen.show_record_bundle_button.toolTip()
        assert "2レコード × 3項目" in screen.show_record_bundle_button.toolTip()
        assert screen.show_record_bundle_button.isEnabled()
        screen.show_record_bundle_contents()
        viewer = next(iter(screen._record_bundle_viewers))
        assert viewer.table.rowCount() == 2
        assert viewer.table.columnCount() == 3
        assert viewer.table.item(0, 1).text() == "20220502.mp4"
        assert "未判定" in viewer.table.item(0, 2).text()
        viewer.field_combo.setCurrentIndex(1)
        assert viewer.table.item(0, 1).text() == "全国お笑い"
        viewer.close()
        app.processEvents()
        screen.search_results_input.setPlainText("/tmp/20220502.mp4\n/tmp/not-linked.mp4")
        screen.search_results_input.select_all_items()
        screen._select_record_link_field(0)

        assert screen.search_results_input.supplemental_column_visible("紐づけ")
        assert screen._record_linkage is not None
        assert len(screen._record_linkage.links) == 1
        assert screen.show_record_bundle_button.text() == "対応表：成功 1/2"
        assert "項目1「現在名」" in screen.show_record_bundle_button.toolTip()
        assert "対象パス側の失敗 1" in screen.show_record_bundle_button.toolTip()
        menu = QMenu()
        screen._add_record_linkage_context_menu(menu)
        linkage_action = next(
            action for action in menu.actions() if action.text() == "対応表との紐づけ"
        )
        labels = [action.text() for action in linkage_action.menu().actions()]
        assert "紐づけ済みのみ一覧に残す" in labels
        assert "紐づけファイル一括操作…" in labels

        screen.show_record_bundle_contents()
        linked_viewer = next(iter(screen._record_bundle_viewers))
        assert linked_viewer.table.item(0, 2).text() == "/tmp/20220502.mp4"
        assert "紐づけなし" in linked_viewer.table.item(1, 2).text()

        screen._linked_paths_renamed(
            {"/tmp/20220502.mp4": "/tmp/全国お笑い.mp4"}
        )
        # An already-open viewer remains an intentional point-in-time snapshot.
        assert linked_viewer.table.item(0, 2).text() == "/tmp/20220502.mp4"
        linked_viewer.close()
        app.processEvents()
        screen.show_record_bundle_contents()
        refreshed_viewer = next(iter(screen._record_bundle_viewers))
        assert refreshed_viewer.table.item(0, 2).text() == "/tmp/全国お笑い.mp4"
        refreshed_viewer.close()
        app.processEvents()
        assert screen.search_results_input.items()[0] == "/tmp/全国お笑い.mp4"
        assert screen._record_linkage.links[0].row_identifier == "row-1"
        assert screen._record_linkage.links[0].source.name == "全国お笑い.mp4"
        assert len(screen.search_results_input.selected_items()) == 2

        screen._keep_only_linked_paths()
        assert screen.search_results_input.items() == ["/tmp/全国お笑い.mp4"]
        assert screen.search_results_input.selected_items() == ["/tmp/全国お笑い.mp4"]
    finally:
        screen.close()
