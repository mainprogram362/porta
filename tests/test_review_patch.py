from pathlib import Path

import pytest

from media.catalog import CatalogAttribute
from media.ledger import MediaPart
from media.review_patch import (
    MediaReviewPatch,
    ReviewPatchEntry,
    apply_review_patch_merge,
    load_review_patch,
    plan_review_patch_merge,
    review_patch_entry,
    save_review_patch,
)


def _part(name: str, *attributes: CatalogAttribute) -> MediaPart:
    return MediaPart((CatalogAttribute("file.name.observed", name, "text", "filesystem"),) + attributes)


def test_review_patch_keeps_no_path_and_merges_only_review_fields(tmp_path: Path):
    patch = MediaReviewPatch(
        (
            review_patch_entry(
                "one.mp4",
                (
                    CatalogAttribute("review.score", 0.7, "number", "user"),
                    CatalogAttribute(
                        "media.highlights",
                        [{"time": "01:23", "comment": ""}, {"time": "02:34-03:00", "comment": "つかみ"}],
                        "highlights",
                        "user",
                    ),
                ),
            ),
        )
    )
    path = tmp_path / "review.json"
    save_review_patch(path, patch)
    loaded = load_review_patch(path)
    raw = path.read_text(encoding="utf-8")

    assert "file_names" in raw
    assert "file.path" not in raw
    target = (_part("one.mp4", CatalogAttribute("title.official", "作品A", "text", "user")), _part("two.mp4"))
    plan = plan_review_patch_merge(target, loaded)
    merged = apply_review_patch_merge(target, loaded, plan)

    assert plan.is_safe
    assert plan.unchanged_target_count == 1
    assert [attribute.value for attribute in merged[0].attributes if attribute.key == "review.score"] == [0.7]
    assert [attribute.value for attribute in merged[0].attributes if attribute.key == "media.highlights"] == [[
        {"time": "01:23", "comment": ""}, {"time": "02:34-03:00", "comment": "つかみ"}
    ]]
    assert [attribute.value for attribute in merged[0].attributes if attribute.key == "title.official"] == ["作品A"]
    assert merged[1] == target[1]


def test_review_patch_rejects_ambiguous_filename_without_mutating_anything():
    parts = (_part("same.mp4"), _part("same.mp4"))
    patch = MediaReviewPatch((review_patch_entry("same.mp4", (CatalogAttribute("review.score", 1.0, "number", "user"),)),))

    plan = plan_review_patch_merge(parts, patch)

    assert not plan.is_safe
    assert plan.ambiguous_matches == ((0, (0, 1)),)
    with pytest.raises(ValueError, match="安全ではない"):
        apply_review_patch_merge(parts, patch, plan)


def test_review_patch_rejects_missing_or_duplicate_target_associations():
    parts = (_part("one.mp4"),)
    missing = MediaReviewPatch((review_patch_entry("missing.mp4", ()),))
    duplicated = MediaReviewPatch(
        (
            review_patch_entry("one.mp4", (CatalogAttribute("review.score", 0.5, "number", "user"),)),
            ReviewPatchEntry(("old-one.mp4", "one.mp4"), ()),
        )
    )

    assert plan_review_patch_merge(parts, missing).unmatched_patch_indexes == (0,)
    duplicate_plan = plan_review_patch_merge(parts, duplicated)
    assert duplicate_plan.duplicate_target_matches == ((0, (0, 1)),)


def test_review_patch_accepts_any_one_of_a_part_name_aliases():
    part = MediaPart(
        (
            CatalogAttribute("file.name.observed", "current-name.mp4", "text", "filesystem"),
            CatalogAttribute("file.name.observed", "old-name.mp4", "text", "user"),
        )
    )
    patch = MediaReviewPatch(
        (review_patch_entry("old-name.mp4", (CatalogAttribute("review.score", 0.6, "number", "user"),)),)
    )

    plan = plan_review_patch_merge((part,), patch)

    assert plan.is_safe
    assert plan.matches[0].part_index == 0


def test_review_patch_adds_only_new_tags_and_keeps_existing_tags_unchanged():
    target = (
        _part("one.mp4", CatalogAttribute("classification.tag", ["既存", "重複"], "text_list", "user")),
    )
    patch = MediaReviewPatch(
        (
            review_patch_entry(
                "one.mp4",
                (CatalogAttribute("classification.tag", ["重複", "追加"], "text_list", "mpv_review"),),
            ),
        )
    )

    merged = apply_review_patch_merge(target, patch, plan_review_patch_merge(target, patch))

    assert [attribute.value for attribute in merged[0].attributes if attribute.key == "classification.tag"] == [
        ["既存", "重複", "追加"]
    ]
    duplicate_only = MediaReviewPatch(
        (
            review_patch_entry(
                "one.mp4",
                (CatalogAttribute("classification.tag", "追加", "text", "mpv_review"),),
            ),
        )
    )
    assert apply_review_patch_merge(
        merged, duplicate_only, plan_review_patch_merge(merged, duplicate_only)
    ) == merged
