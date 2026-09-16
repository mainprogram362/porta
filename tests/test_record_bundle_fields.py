from foundation.record_bundle import RecordBundle, RecordBundleRow
from foundation.record_bundle_fields import (
    add_extensionless_field,
    add_inserted_field,
    add_range_removed_field,
)


def _bundle() -> RecordBundle:
    return RecordBundle(
        "テスト",
        ("ファイル名", "タイトル"),
        (
            RecordBundleRow("one", ("archive/movie.tar.gz", "abcde")),
            RecordBundleRow("two", (".hidden", "vwxyz")),
        ),
    )


def test_extensionless_field_preserves_original_values_and_identifiers() -> None:
    updated = add_extensionless_field(_bundle(), 0, "拡張子なし")

    assert updated.field_names == ("ファイル名", "タイトル", "拡張子なし")
    assert updated.rows[0].identifier == "one"
    assert updated.rows[0].values == ("archive/movie.tar.gz", "abcde", "archive/movie.tar")
    assert updated.rows[1].values[-1] == ".hidden"


def test_range_and_insert_fields_support_start_end_and_explicit_positions() -> None:
    removed = add_range_removed_field(
        _bundle(),
        1,
        "中央を削除",
        start_anchor="start",
        start_position=2,
        end_anchor="end",
        end_position=2,
    )
    inserted = add_inserted_field(
        removed,
        1,
        "接頭辞つき",
        text="ID_",
        position="index",
        index=2,
    )

    assert removed.rows[0].values[-1] == "ae"
    assert removed.rows[1].values[-1] == "vz"
    assert inserted.rows[0].values[-1] == "aID_bcde"
