import json
from pathlib import Path

import pytest

from media.catalog import (
    CatalogAttribute,
    CatalogRecordInput,
    append_catalog_records,
    apply_conservative_catalog_merge,
    create_catalog_records,
    plan_conservative_catalog_merge,
    replace_catalog_records,
    resolve_new_catalog_path,
)
from media.review_patch import (
    MediaReviewPatch,
    resolve_new_review_patch_path,
    review_patch_entry,
    replace_review_patch,
    save_review_patch,
)


def _record(name: str, *attributes: CatalogAttribute) -> CatalogRecordInput:
    return CatalogRecordInput(
        (CatalogAttribute("file.name.observed", name, "text", "filesystem"),) + attributes
    )


def test_directory_destination_always_selects_a_new_catalog_name(tmp_path: Path):
    first = resolve_new_catalog_path(tmp_path, output_directory=tmp_path)
    assert first == tmp_path / "media_catalog.json"
    first.write_text("{}", encoding="utf-8")

    assert resolve_new_catalog_path(tmp_path, output_directory=tmp_path) == tmp_path / "media_catalog (1).json"
    assert resolve_new_catalog_path(tmp_path / "direct.json", output_directory=tmp_path) == tmp_path / "direct.json"


def test_new_and_append_modes_never_update_existing_catalog_records(tmp_path: Path):
    destination = tmp_path / "catalog.json"
    first = _record("one.mp4", CatalogAttribute("source.youtube.video_id", "one", "text", "youtube"))
    second = _record("one-new-name.mp4", CatalogAttribute("source.youtube.video_id", "one", "text", "youtube"))

    assert create_catalog_records(destination, [first]).added == 1
    with pytest.raises(ValueError, match="既存JSON"):
        create_catalog_records(destination, [second])
    assert append_catalog_records(destination, [second]).added == 1

    document = json.loads(destination.read_text(encoding="utf-8"))
    assert [record["record_id"] for record in document["media"]] == ["00001", "00002"]
    assert len(document["media"]) == 2


def test_conservative_merge_requires_one_to_one_matching_and_no_value_conflicts(tmp_path: Path):
    destination = tmp_path / "catalog.json"
    create_catalog_records(
        destination,
        [
            _record("one.mp4", CatalogAttribute("title.official", "作品A", "text", "user")),
            _record("untouched.mp4", CatalogAttribute("title.official", "作品B", "text", "user")),
        ],
    )
    incoming = _record("one.mp4", CatalogAttribute("classification.tag", "動物", "text", "user"))

    plan = plan_conservative_catalog_merge(
        destination, [incoming], match_keys=["file.name.observed"]
    )

    assert plan.is_safe
    assert len(plan.matches) == 1
    assert plan.unchanged_target_count == 1
    assert apply_conservative_catalog_merge(destination, [incoming], plan).updated == 1
    written = json.loads(destination.read_text(encoding="utf-8"))
    assert any(
        attribute["key"] == "classification.tag" and attribute["value"] == "動物"
        for attribute in written["media"][0]["attributes"]
    )

    unmatched = plan_conservative_catalog_merge(
        destination, [_record("missing.mp4")], match_keys=["file.name.observed"]
    )
    assert not unmatched.is_safe
    assert unmatched.unmatched_incoming_indexes == (0,)

    conflict = plan_conservative_catalog_merge(
        destination,
        [_record("one.mp4", CatalogAttribute("title.official", "別作品", "text", "user"))],
        match_keys=["file.name.observed"],
    )
    assert not conflict.is_safe
    assert conflict.value_conflicts[0].key == "title.official"

    duplicate = plan_conservative_catalog_merge(
        destination,
        [_record("one.mp4"), _record("one.mp4")],
        match_keys=["file.name.observed"],
    )
    assert not duplicate.is_safe
    assert duplicate.duplicate_target_matches == ((0, (0, 1)),)


def test_review_patch_directory_is_unique_and_existing_file_is_not_overwritten(tmp_path: Path):
    output = resolve_new_review_patch_path(tmp_path)
    assert output == tmp_path / "media_review_patch.json"
    patch = MediaReviewPatch((review_patch_entry("clip.mp4", ()),))
    save_review_patch(output, patch)

    assert resolve_new_review_patch_path(tmp_path) == tmp_path / "media_review_patch (1).json"
    with pytest.raises(ValueError, match="上書きしません"):
        save_review_patch(output, patch)


def test_explicit_replace_operations_atomically_replace_existing_snapshots(tmp_path: Path):
    catalog = tmp_path / "catalog.json"
    create_catalog_records(catalog, [_record("old.mp4")])

    report = replace_catalog_records(catalog, [_record("current.mp4")])

    assert report.added == 1
    assert [entry["attributes"][0]["value"] for entry in json.loads(catalog.read_text(encoding="utf-8"))["media"]] == ["current.mp4"]

    patch_path = tmp_path / "patch.json"
    save_review_patch(patch_path, MediaReviewPatch((review_patch_entry("old.mp4", ()),)))
    replace_review_patch(
        patch_path,
        MediaReviewPatch((review_patch_entry("current.mp4", (CatalogAttribute("review.score", 0.8, "number", "mpv_review"),)),)),
    )

    saved = json.loads(patch_path.read_text(encoding="utf-8"))
    assert saved["entries"][0]["file_names"] == ["current.mp4"]
