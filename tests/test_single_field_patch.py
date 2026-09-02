from pathlib import Path

import pytest

from media.catalog import CatalogAttribute
from media.ledger import MediaPart
from media.single_field_patch import (
    MediaSingleFieldPatch,
    SingleFieldPatchEntry,
    apply_single_field_patch_merge,
    load_single_field_patch,
    plan_single_field_patch_merge,
    save_single_field_patch,
    single_field_patch_entry,
)


def _part(name: str, *attributes: CatalogAttribute) -> MediaPart:
    return MediaPart((CatalogAttribute("file.name.observed", name, "text", "filesystem"),) + attributes)


def test_single_field_patch_is_pathless_and_replaces_only_its_declared_field(tmp_path: Path):
    patch = MediaSingleFieldPatch(
        "title.official",
        (single_field_patch_entry("owarai34-1.mp4", "吉本第35回お笑いライブ第34回part1"),),
    )
    destination = tmp_path / "title_patch.json"
    save_single_field_patch(destination, patch)
    loaded = load_single_field_patch(destination)
    target = (
        _part(
            "owarai34-1.mp4",
            CatalogAttribute("series.name", "お笑いライブ", "text", "user"),
            CatalogAttribute("title.official", "仮題", "text", "user"),
        ),
        _part("other.mp4"),
    )

    plan = plan_single_field_patch_merge(target, loaded)
    merged = apply_single_field_patch_merge(target, loaded, plan)

    raw = destination.read_text(encoding="utf-8")
    assert '"field": "title.official"' in raw
    assert "file.path" not in raw
    assert plan.is_safe
    assert plan.unchanged_target_count == 1
    assert [attribute.value for attribute in merged[0].attributes if attribute.key == "title.official"] == [
        "吉本第35回お笑いライブ第34回part1"
    ]
    assert [attribute.value for attribute in merged[0].attributes if attribute.key == "series.name"] == ["お笑いライブ"]
    assert merged[1] == target[1]


def test_single_field_patch_rejects_ambiguous_targets_and_non_text_fields():
    patch = MediaSingleFieldPatch(
        "title.official",
        (single_field_patch_entry("same.mp4", "名称"),),
    )
    target = (_part("same.mp4"), _part("same.mp4"))

    plan = plan_single_field_patch_merge(target, patch)

    assert not plan.is_safe
    with pytest.raises(ValueError, match="安全ではない"):
        apply_single_field_patch_merge(target, patch, plan)
    with pytest.raises(ValueError, match="更新できない"):
        plan_single_field_patch_merge(
            (_part("one.mp4"),),
            MediaSingleFieldPatch("review.score", (single_field_patch_entry("one.mp4", "1"),)),
        )


def test_single_field_patch_rejects_duplicate_patch_entries_for_the_same_name():
    patch = MediaSingleFieldPatch(
        "title.official",
        (
            SingleFieldPatchEntry(("one.mp4",), "一"),
            SingleFieldPatchEntry(("one.mp4",), "二"),
        ),
    )

    with pytest.raises(ValueError, match="重複"):
        plan_single_field_patch_merge((_part("one.mp4"),), patch)
