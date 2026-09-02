import json
from pathlib import Path

import pytest

from media import file_attributes
from media.catalog import CatalogAttribute, add_catalog_records, validate_catalog_write_path
from foundation.transient_paths import offer_media_paths, take_media_paths


def test_inspect_media_path_returns_non_destructive_basic_attribute_pairs(tmp_path: Path):
    path = tmp_path / "example.txt"
    path.write_text("sample", encoding="utf-8")

    item = file_attributes.inspect_media_path(path)
    values = item.attribute_map

    assert item.path == path
    assert values["file.name"].value == "example.txt"
    assert values["file.kind"].value == "file"
    assert values["file.size_bytes"].value == len("sample")
    assert values["file.exists"].value is True
    assert path.read_text(encoding="utf-8") == "sample"


def test_missing_path_is_a_readable_attribute_not_an_error(tmp_path: Path):
    item = file_attributes.inspect_media_path(tmp_path / "missing.mp4")

    assert item.attribute_map["file.kind"].value == "missing"
    assert item.attribute_map["file.exists"].value is False


def test_folder_is_a_small_recursive_collection_record_without_a_child_manifest(tmp_path: Path):
    root = tmp_path / "clips"
    nested = root / "nested"
    nested.mkdir(parents=True)
    (root / "one.mp4").write_bytes(b"abc")
    (nested / "notes.txt").write_bytes(b"defgh")

    item = file_attributes.inspect_media_path(root)
    values = item.attribute_map
    catalog_values = {
        attribute.key: attribute.value
        for attribute in file_attributes.catalog_attributes_from_media_item(item)
    }

    assert values["file.kind"].value == "directory"
    assert values["file.size_bytes"].value == 8
    assert catalog_values["record.kind"] == "folder_collection"
    assert catalog_values["folder.descendant_folder_count"] == 1
    assert catalog_values["folder.descendant_file_count"] == 2
    assert catalog_values["folder.media_file_count"] == 1
    assert catalog_values["folder.total_size_bytes"] == 8
    assert catalog_values["video.resolution"] is None
    assert "folder.contents_snapshot" not in catalog_values
    assert "video.resolution" in catalog_values["folder.uneditable_field_reasons"]

    try:
        file_attributes.apply_catalog_field_operation(
            item, key="video.resolution", operation="replace", value="1920×1080"
        )
    except ValueError as exc:
        assert "ばらつき" in str(exc)
    else:
        raise AssertionError("フォルダへ単一の解像度を設定してはいけません")


def test_resolution_is_one_structured_attribute_with_two_numeric_components(tmp_path: Path, monkeypatch):
    path = tmp_path / "sample.mp4"
    path.write_bytes(b"not-a-real-video")

    class Result:
        returncode = 0
        stdout = json.dumps(
            {
                "format": {"duration": "61.5"},
                "streams": [
                    {"codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080},
                    {"codec_type": "audio", "codec_name": "aac"},
                ],
            }
        )

    monkeypatch.setattr(file_attributes.shutil, "which", lambda _name: "/usr/bin/ffprobe")
    monkeypatch.setattr(file_attributes.subprocess, "run", lambda *_args, **_kwargs: Result())

    item = file_attributes.inspect_media_path(path)
    resolution = item.attribute_map["video.resolution"]

    assert resolution.value == {"width": 1920, "height": 1080}
    assert resolution.display == "1920×1080"
    assert item.attribute_map["media.duration_seconds"].value == 61.5


def test_custom_attributes_are_added_to_the_shared_catalog_without_a_live_path(tmp_path: Path):
    path = tmp_path / "clip.txt"
    path.write_text("clip", encoding="utf-8")
    item = file_attributes.add_user_attribute(
        file_attributes.inspect_media_path(path), "review.score", "0.8"
    )

    output = tmp_path / "catalog.json"
    report = add_catalog_records(output, [file_attributes.catalog_record_from_media_item(item)])
    document = json.loads(output.read_text(encoding="utf-8"))
    attributes = document["media"][0]["attributes"]

    assert report.added == 1
    assert document["kind"] == "media_catalog"
    assert not any(attribute["key"] == "file.path" for attribute in attributes)
    assert any(
        attribute["key"] == "file.name.observed" and attribute["value"] == "clip.txt"
        for attribute in attributes
    )
    assert any(attribute["key"] == "review.score" and attribute["value"] == "0.8" for attribute in attributes)


def test_catalog_preview_has_the_same_fixed_fields_for_every_file_and_uses_null_when_unknown(tmp_path: Path):
    path = tmp_path / "plain.txt"
    path.write_text("sample", encoding="utf-8")

    attributes = file_attributes.catalog_attributes_from_media_item(file_attributes.inspect_media_path(path))
    values = {attribute.key: attribute.value for attribute in attributes}

    assert len(file_attributes.STANDARD_CATALOG_FIELDS) == 24
    assert {field.key for field in file_attributes.STANDARD_CATALOG_FIELDS}.issubset(values)
    assert values["file.name.observed"] == "plain.txt"
    assert values["video.resolution"] is None
    assert values["media.duration_seconds"] is None
    assert values["identity.public_identifier"] is None
    assert values["source.uploader"] is None
    assert values["group.name"] is None
    assert values["review.community_score"] is None
    assert values["review.community_recommendation"] is None
    assert values["review.community_note"] is None
    assert values["storage.status"] is None
    assert values["media.highlights"] is None
    assert "file.path" not in values


def test_catalog_record_omits_unknown_standard_fields_from_json_payload(tmp_path: Path):
    path = tmp_path / "plain.txt"
    path.write_text("sample", encoding="utf-8")

    record = file_attributes.catalog_record_from_media_item(file_attributes.inspect_media_path(path))
    values = {attribute.key: attribute.value for attribute in record.attributes}

    assert values["file.name.observed"] == "plain.txt"
    assert "video.resolution" not in values
    assert "media.duration_seconds" not in values
    assert "identity.public_identifier" not in values


def test_catalog_write_destination_must_already_have_a_usable_parent(tmp_path: Path):
    output_directory = tmp_path / "catalogs"
    output_directory.mkdir()

    assert validate_catalog_write_path(
        output_directory / "items.json", output_directory=tmp_path
    ) == output_directory / "items.json"
    with pytest.raises(ValueError, match="保存先フォルダがありません"):
        validate_catalog_write_path(
            tmp_path / "missing" / "items.json", output_directory=tmp_path
        )


def test_catalog_frequent_fields_and_new_user_values_have_stable_types(tmp_path: Path):
    item = file_attributes.inspect_media_path(tmp_path / "plain.txt")
    item = file_attributes.apply_catalog_field_operation(
        item, key="identity.public_identifier", operation="append", value="ABC-001"
    )
    item = file_attributes.apply_catalog_field_operation(
        item, key="identity.public_identifier", operation="append", value="公開名"
    )
    item = file_attributes.apply_catalog_field_operation(
        item, key="source.uploader", operation="replace", value="現在の名義"
    )
    item = file_attributes.apply_catalog_field_operation(
        item, key="source.uploader", operation="append", value="以前の名義"
    )
    item = file_attributes.append_media_highlight(item, time="13:23")
    item = file_attributes.append_media_highlight(item, time="13:11 - 14:25", comment="つかみ")
    values = {attribute.key: attribute.value for attribute in file_attributes.catalog_attributes_from_media_item(item)}

    assert "collection.status" in file_attributes.FREQUENT_CATALOG_FIELD_KEYS
    assert "source.uploader" in file_attributes.FREQUENT_CATALOG_FIELD_KEYS
    assert "group.name" in file_attributes.FREQUENT_CATALOG_FIELD_KEYS
    assert file_attributes.COLLECTION_STATUS_PRESETS == (
        "保有", "保有（低画質のみ）", "未保有", "対象外"
    )
    assert values["identity.public_identifier"] == ["ABC-001", "公開名"]
    assert values["source.uploader"] == ["現在の名義", "以前の名義"]
    assert values["media.highlights"] == [
        {"time": "13:23", "comment": ""},
        {"time": "13:11-14:25", "comment": "つかみ"},
    ]


def test_legacy_single_uploader_can_gain_aliases_without_losing_the_original():
    item = file_attributes.media_item_from_catalog_attributes(
        [CatalogAttribute("source.uploader", "旧名義", "text", "user")]
    )
    assert item.attribute_map["source.uploader"].value == "旧名義"

    updated = file_attributes.apply_catalog_field_operation(
        item,
        key="source.uploader",
        operation="append",
        value="現在の名義",
    )
    uploader = updated.attribute_map["source.uploader"]
    assert uploader.value == ["旧名義", "現在の名義"]
    assert uploader.value_type == "text_list"
    values = {
        attribute.key: attribute.value
        for attribute in file_attributes.catalog_record_from_media_item(updated).attributes
    }
    assert values["source.uploader"] == ["旧名義", "現在の名義"]


def test_catalog_excluded_fields_are_omitted_from_the_actual_json_payload(tmp_path: Path):
    path = tmp_path / "plain.txt"
    path.write_text("sample", encoding="utf-8")

    record = file_attributes.catalog_record_from_media_item(
        file_attributes.inspect_media_path(path),
        excluded_keys={"file.size_bytes.observed", "video.resolution"},
    )

    keys = {attribute.key for attribute in record.attributes}
    assert "file.size_bytes.observed" not in keys
    assert "video.resolution" not in keys
    assert "file.name.observed" in keys


def test_common_catalog_operations_use_json_lists_only_for_appendable_fields(tmp_path: Path):
    item = file_attributes.inspect_media_path(tmp_path / "plain.txt")
    item = file_attributes.apply_catalog_field_operation(
        item, key="classification.tag", operation="append", value="動物"
    )
    item = file_attributes.apply_catalog_field_operation(
        item, key="classification.tag", operation="append", value="ハプニング"
    )
    values = {attribute.key: attribute.value for attribute in file_attributes.catalog_attributes_from_media_item(item)}

    assert values["classification.tag"] == ["動物", "ハプニング"]
    assert file_attributes.display_catalog_attribute(
        next(attribute for attribute in file_attributes.catalog_attributes_from_media_item(item) if attribute.key == "classification.tag")
    ) == "動物 / ハプニング"

    try:
        file_attributes.apply_catalog_field_operation(
            item, key="video.resolution", operation="append", value="1920x1080"
        )
    except ValueError as exc:
        assert "上書き" in str(exc)
    else:
        raise AssertionError("単一値の解像度へ追加を許可してはいけません")


def test_resolution_edit_accepts_an_unambiguous_multiplication_sign(tmp_path: Path):
    item = file_attributes.inspect_media_path(tmp_path / "plain.txt")

    edited = file_attributes.apply_catalog_field_operation(
        item, key="video.resolution", operation="replace", value="1920*1080"
    )
    values = {attribute.key: attribute.value for attribute in file_attributes.catalog_attributes_from_media_item(edited)}

    assert values["video.resolution"] == {"width": 1920, "height": 1080}


def test_review_score_is_a_number_from_zero_to_one(tmp_path: Path):
    item = file_attributes.inspect_media_path(tmp_path / "plain.txt")
    edited = file_attributes.apply_catalog_field_operation(
        item, key="review.score", operation="replace", value="0.7"
    )
    values = {attribute.key: attribute.value for attribute in file_attributes.catalog_attributes_from_media_item(edited)}

    assert values["review.score"] == 0.7
    try:
        file_attributes.apply_catalog_field_operation(
            item, key="review.score", operation="replace", value="1.1"
        )
    except ValueError as exc:
        assert "0〜1" in str(exc)
    else:
        raise AssertionError("範囲外の評価を受け入れてはいけません")


def test_community_review_fields_are_separate_from_the_subjective_score(tmp_path: Path):
    item = file_attributes.inspect_media_path(tmp_path / "plain.txt")
    item = file_attributes.apply_catalog_field_operation(
        item, key="review.community_score", operation="replace", value="0.8"
    )
    item = file_attributes.apply_catalog_field_operation(
        item,
        key="review.community_recommendation",
        operation="replace",
        value="おすすめ",
    )
    item = file_attributes.apply_catalog_field_operation(
        item, key="review.community_note", operation="replace", value="複数の口コミを要約"
    )
    values = {
        attribute.key: attribute.value
        for attribute in file_attributes.catalog_record_from_media_item(item).attributes
    }

    assert values["review.community_score"] == 0.8
    assert values["review.community_recommendation"] == "おすすめ"
    assert values["review.community_note"] == "複数の口コミを要約"
    assert "review.score" not in values
    with pytest.raises(ValueError, match="0〜1"):
        file_attributes.apply_catalog_field_operation(
            item, key="review.community_score", operation="replace", value="1.1"
        )


def test_common_catalog_operations_can_clear_standard_values_and_remove_custom_fields(tmp_path: Path):
    item = file_attributes.inspect_media_path(tmp_path / "plain.txt")
    item = file_attributes.apply_catalog_field_operation(
        item, key="title.official", operation="replace", value="正式名"
    )
    item = file_attributes.apply_catalog_field_operation(
        item, key="title.official", operation="clear"
    )
    item = file_attributes.apply_catalog_field_operation(
        item, key="custom.note", operation="replace", value="一時メモ"
    )
    item = file_attributes.apply_catalog_field_operation(item, key="custom.note", operation="remove")
    values = {attribute.key: attribute.value for attribute in file_attributes.catalog_attributes_from_media_item(item)}

    assert values["title.official"] is None
    assert "custom.note" not in values


def test_category_tree_is_one_normalized_root_to_leaf_value(tmp_path: Path):
    item = file_attributes.inspect_media_path(tmp_path / "plain.txt")
    item = file_attributes.apply_catalog_field_operation(
        item,
        key="classification.category_tree",
        operation="replace",
        value=" ゲーム実況 / ホラー / 投稿者A ",
    )
    values = {attribute.key: attribute.value for attribute in file_attributes.catalog_attributes_from_media_item(item)}

    assert values["classification.category_tree"] == "ゲーム実況/ホラー/投稿者A"
    try:
        file_attributes.apply_catalog_field_operation(
            item, key="classification.category_tree", operation="append", value="別分類"
        )
    except ValueError as exc:
        assert "上書き" in str(exc)
    else:
        raise AssertionError("カテゴリツリーを複数値にしてはいけません")


def test_manual_placeholder_is_pathless_but_writes_transparent_catalog_facts():
    item = file_attributes.create_manual_placeholder_item(
        "シリーズA Part 65", collection_status="未保有"
    )
    record = file_attributes.catalog_record_from_media_item(item)
    values = {attribute.key: attribute.value for attribute in record.attributes}

    assert item.path is None
    assert item.origin == "manual_placeholder"
    assert file_attributes.media_item_display_name(item) == "シリーズA Part 65"
    assert file_attributes.media_item_token(item).startswith("manual-placeholder:")
    assert values["file.name.observed"] == "シリーズA Part 65"
    assert values["collection.status"] == "未保有"
    assert values["record.kind"] == "manual_placeholder"
    assert values["record.origin"] == "manual_placeholder"
    try:
        file_attributes.apply_catalog_field_operation(
            item, key="video.resolution", operation="replace", value="1920×1080"
        )
    except ValueError as exc:
        assert "実ファイルがない" in str(exc)
    else:
        raise AssertionError("仮登録へ実ファイルの観測値を入力してはいけません")


def test_fixed_catalog_fields_offer_presets_but_keep_free_input_possible():
    assert file_attributes.preset_values_for_catalog_field("collection.status") == (
        ("保有", "保有"),
        ("保有（低画質のみ）", "保有（低画質のみ）"),
        ("未保有", "未保有"),
        ("対象外", "対象外"),
    )
    assert ("DVD保存済み", "DVD保存済み") in file_attributes.preset_values_for_catalog_field(
        "storage.status"
    )
    assert file_attributes.preset_values_for_catalog_field("review.score")[-1] == ("10/10", "1.0")
    assert file_attributes.preset_values_for_catalog_field("review.community_recommendation") == (
        ("おすすめ", "おすすめ"),
        ("非推奨", "非推奨"),
    )
    assert file_attributes.preset_values_for_catalog_field("classification.tag") == ()


def test_media_path_handoff_is_in_memory_and_consumed_once(tmp_path: Path):
    path = tmp_path / "clip.mp4"

    assert offer_media_paths([path, path]) == 1
    assert take_media_paths() == (str(path.resolve()),)
    assert take_media_paths() == ()
