import json
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from apps.media_tools.media_ledger.window import MediaLedgerScreen
from media.catalog import CatalogAttribute
from media.ledger import (
    LEDGER_KIND,
    MediaPart,
    add_file,
    add_work,
    adopt_parts,
    attribute_value,
    empty_ledger,
    ledger_as_dict,
    load_ledger,
    load_parts,
    merge_parts,
    replace_attribute,
    replace_parts_attribute,
    replace_file,
    save_ledger,
)
from media.review_patch import MediaReviewPatch, review_patch_entry, save_review_patch


def test_new_work_and_file_versions_use_separate_stable_ids():
    ledger, work = add_work(empty_ledger())
    ledger, original = add_file(
        ledger,
        work.work_id,
        variant_kind="original",
        attributes=(CatalogAttribute("file.name.observed", "original.mp4", "text", "filesystem"),),
    )
    ledger, encoded = add_file(
        ledger,
        work.work_id,
        variant_kind="reencoded",
        derived_from_file_id=original.file_id,
    )

    assert work.work_id == "W00001"
    assert original.file_id == "F00001"
    assert encoded.file_id == "F00002"
    assert encoded.derived_from_file_id == original.file_id
    assert ledger.works[0].work_id == work.work_id


def test_ledger_round_trip_preserves_work_and_file_specific_attributes(tmp_path: Path):
    ledger, work = add_work(
        empty_ledger(),
        (CatalogAttribute("title.official", "作品A", "text", "user"),),
    )
    ledger, file = add_file(
        ledger,
        work.work_id,
        variant_kind="original",
        attributes=(CatalogAttribute("video.resolution", {"width": 1920, "height": 1080}, "resolution", "file_metadata"),),
    )
    path = tmp_path / "ledger.json"

    save_ledger(path, ledger)
    loaded = load_ledger(path)

    assert ledger_as_dict(loaded)["kind"] == LEDGER_KIND
    assert loaded.works[0].work_id == work.work_id
    assert loaded.works[0].files[0].file_id == file.file_id
    assert attribute_value(loaded.works[0].attributes, "title.official") == "作品A"
    assert attribute_value(loaded.works[0].files[0].attributes, "video.resolution") == {"width": 1920, "height": 1080}


def test_extraction_catalog_is_read_as_id_free_parts_until_explicit_adoption(tmp_path: Path):
    source = tmp_path / "extraction.json"
    source.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "kind": "media_catalog",
                "media": [
                    {
                        "record_id": "00001",
                        "attributes": [
                            {"key": "title.official", "value": "作品A", "value_type": "text", "source": "user"},
                            {"key": "file.name.observed", "value": "clip.mp4", "value_type": "text", "source": "filesystem"},
                            {"key": "video.resolution", "value": {"width": 1920, "height": 1080}, "value_type": "resolution", "source": "file_metadata"},
                        ],
                    },
                    {
                        "record_id": "00002",
                        "attributes": [
                            {"key": "file.name.observed", "value": "欠番65", "value_type": "text", "source": "manual_placeholder"},
                            {"key": "record.kind", "value": "manual_placeholder", "value_type": "text", "source": "manual_placeholder"},
                            {"key": "collection.status", "value": "未保有", "value_type": "text", "source": "user"},
                        ],
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    parts = load_parts(source)

    assert parts.converted_from_extraction_catalog
    assert len(parts.parts) == 2
    assert attribute_value(parts.parts[0].attributes, "title.official") == "作品A"
    assert not any(attribute.key == "record_id" for attribute in parts.parts[0].attributes)
    ledger, adopted = adopt_parts(empty_ledger(), parts.parts)

    assert len(adopted) == 2
    assert [work.work_id for work in ledger.works] == ["W00001", "W00002"]
    assert len(ledger.works[0].files) == 1
    assert len(ledger.works[1].files) == 0
    assert attribute_value(ledger.works[0].files[0].attributes, "video.resolution") == {"width": 1920, "height": 1080}
    assert source.read_text(encoding="utf-8").startswith('{"schema_version"')


def test_file_version_rejects_itself_as_its_own_source():
    ledger, work = add_work(empty_ledger())
    ledger, file = add_file(ledger, work.work_id)

    with pytest.raises(ValueError, match="自分自身"):
        replace_file(
            ledger,
            work.work_id,
            file.file_id,
            variant_kind="reencoded",
            derived_from_file_id=file.file_id,
            attributes=file.attributes,
        )


def test_replace_attribute_keeps_unknown_future_keys_flexible():
    attributes = replace_attribute((), key="custom.future_value", value="任意の値")

    assert attribute_value(attributes, "custom.future_value") == "任意の値"


def test_part_documents_merge_without_ids_and_only_adoption_issues_master_ids(tmp_path: Path):
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    first.write_text(
        json.dumps({"schema_version": 1, "kind": "media_parts", "parts": [{"attributes": [{"key": "title.official", "value": "A", "value_type": "text", "source": "user"}]}]}),
        encoding="utf-8",
    )
    second.write_text(
        json.dumps({"schema_version": 1, "kind": "media_parts", "parts": [{"attributes": [{"key": "title.official", "value": "B", "value_type": "text", "source": "user"}]}]}),
        encoding="utf-8",
    )

    merged = merge_parts([load_parts(first), load_parts(second)])

    assert [attribute_value(part.attributes, "title.official") for part in merged.parts] == ["A", "B"]
    assert all(not any(attribute.key.endswith("_id") for attribute in part.attributes) for part in merged.parts)
    ledger, works = adopt_parts(empty_ledger(), merged.parts)
    assert [work.work_id for work in works] == ["W00001", "W00002"]
    assert [work.work_id for work in ledger.works] == ["W00001", "W00002"]


def test_common_part_attribute_update_and_removal_do_not_issue_ids(tmp_path: Path):
    source = tmp_path / "parts.json"
    source.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "kind": "media_parts",
                "parts": [
                    {"attributes": [{"key": "title.official", "value": "A", "value_type": "text", "source": "user"}]},
                    {"attributes": [{"key": "title.official", "value": "B", "value_type": "text", "source": "user"}]},
                ],
            }
        ),
        encoding="utf-8",
    )
    parts = load_parts(source).parts

    updated = replace_parts_attribute(parts, key="collection.status", value="未保有")
    removed = replace_parts_attribute(updated, key="title.official", value=None)

    assert [attribute_value(part.attributes, "collection.status") for part in updated] == ["未保有", "未保有"]
    assert [attribute_value(part.attributes, "title.official") for part in removed] == [None, None]
    assert all(not any(attribute.key.endswith("_id") for attribute in part.attributes) for part in removed)


def test_parts_screen_only_applies_a_verified_review_patch_in_memory(tmp_path: Path):
    QApplication.instance() or QApplication([])
    patch_path = tmp_path / "review.json"
    save_review_patch(
        patch_path,
        MediaReviewPatch(
            (
                review_patch_entry(
                    "clip.mp4",
                    (CatalogAttribute("review.score", 0.9, "number", "user"),),
                ),
            )
        ),
    )
    screen = MediaLedgerScreen(lambda: None)
    try:
        screen._parts = [
            MediaPart((CatalogAttribute("file.name.observed", "clip.mp4", "text", "filesystem"),))
        ]
        screen.review_patch_input.setText(str(patch_path))
        screen.check_review_patch_merge()

        assert screen.apply_review_patch_button.isEnabled()
        screen.apply_review_patch_merge()
        assert attribute_value(screen._parts[0].attributes, "review.score") == 0.9
        assert "元のパーツJSON" in screen._review_patch_report_text
    finally:
        screen.close()


def test_parts_common_editor_uses_standard_types_presets_and_existing_custom_keys():
    QApplication.instance() or QApplication([])
    screen = MediaLedgerScreen(lambda: None)
    try:
        screen._parts = [
            MediaPart((
                CatalogAttribute("title.official", "作品A", "text", "user"),
                CatalogAttribute("custom.note", "元のメモ", "text", "user"),
            )),
            MediaPart((CatalogAttribute("title.official", "作品B", "text", "user"),)),
        ]
        screen._refresh_common_part_field_choices()

        assert screen.minimumWidth() <= 800
        assert screen.common_part_field_combo.findData("source.uploader") >= 0
        assert screen.common_part_field_combo.findData("group.name") >= 0

        screen.common_part_scope_combo.setCurrentIndex(
            screen.common_part_scope_combo.findData("existing")
        )
        assert screen.common_part_field_combo.findData("custom.note") >= 0

        screen.common_part_scope_combo.setCurrentIndex(
            screen.common_part_scope_combo.findData("standard")
        )
        screen.common_part_field_combo.setCurrentIndex(
            screen.common_part_field_combo.findData("collection.status")
        )
        choice = screen.common_part_preset_combo.findData("未保有")
        assert choice >= 0
        screen.common_part_preset_combo.setCurrentIndex(choice)
        screen.replace_common_part_attribute()
        assert [attribute_value(part.attributes, "collection.status") for part in screen._parts] == [
            "未保有", "未保有"
        ]

        screen.common_part_field_combo.setCurrentIndex(
            screen.common_part_field_combo.findData("video.resolution")
        )
        screen.common_part_value_input.setText("1920*1080")
        screen.replace_common_part_attribute()
        attributes = screen._parts[0].attributes
        resolution = next(attribute for attribute in attributes if attribute.key == "video.resolution")
        assert resolution.value == {"width": 1920, "height": 1080}
        assert resolution.value_type == "resolution"
    finally:
        screen.close()
