import json
from pathlib import Path

from apps.file_tools.file_manager.rename_workflow import (
    build_mapped_rename_preview,
    execute_rename_plan,
)
from apps.text_tools.text_workbench.rename_mapping import match_record_paths
from foundation.record_bundle import (
    FieldExtractionRule,
    TransientRecordBundleStore,
    extract_candidate_field_matches,
    extract_candidate_fields,
    incomplete_row_numbers,
    parse_record_bundle,
    render_bundle_json,
    render_bundle_template,
    render_bundle_tsv,
    split_record_candidates,
    suggest_record_layout,
)


EXAMPLE = """お笑い芸人のライブ集

20220502
第３回全国お笑い一回戦
100mb
20230312
次のライブ
200mb
"""


def test_layout_suggestion_and_parser_keep_each_record_atomic():
    suggestion = suggest_record_layout(EXAMPLE)
    assert suggestion.field_count == 3
    assert suggestion.field_names == ("日付", "タイトル", "サイズ")
    assert suggestion.use_first_line_as_title

    bundle = parse_record_bundle(
        EXAMPLE,
        suggestion.field_names,
        use_first_line_as_title=suggestion.use_first_line_as_title,
    )
    assert bundle.title == "お笑い芸人のライブ集"
    assert bundle.rows[0].values == ("20220502", "第３回全国お笑い一回戦", "100mb")
    assert bundle.rows[1].values == ("20230312", "次のライブ", "200mb")
    assert bundle.rows[0].identifier != bundle.rows[1].identifier


def test_parser_keeps_an_incomplete_tail_visible_instead_of_discarding_it():
    bundle = parse_record_bundle("key1\nvalue1\nkey2", ("キー", "値"), use_first_line_as_title=False)
    assert len(bundle.rows) == 2
    assert bundle.rows[1].values == ("key2", "")
    assert incomplete_row_numbers(bundle) == (2,)


def test_record_candidates_support_fixed_lines_blank_blocks_and_regex_starts():
    fixed = split_record_candidates(
        "A\nB\nC\nD\nE",
        mode="fixed_lines",
        lines_per_record=2,
        use_first_line_as_title=False,
    )
    assert [candidate.lines for candidate in fixed.candidates] == [
        ("A", "B"),
        ("C", "D"),
        ("E",),
    ]
    assert "足りません" in fixed.issues[0]

    blocks = split_record_candidates(
        "A\nB\n\nC\nD",
        mode="blank_blocks",
        use_first_line_as_title=False,
    )
    assert [candidate.lines for candidate in blocks.candidates] == [
        ("A", "B"),
        ("C", "D"),
    ]

    dated = split_record_candidates(
        "20220502\n第1回\n100mb\n20230312\n第2回\n200mb",
        mode="start_regex",
        boundary=r"^\d{8}$",
        use_first_line_as_title=False,
    )
    assert [candidate.lines for candidate in dated.candidates] == [
        ("20220502", "第1回", "100mb"),
        ("20230312", "第2回", "200mb"),
    ]

    contained = split_record_candidates(
        "事前メモ\nID: 1\nfirst\nnote ID: 2\nsecond",
        mode="contains_text",
        boundary="ID:",
        use_first_line_as_title=True,
    )
    assert [candidate.lines for candidate in contained.candidates] == [
        ("ID: 1", "first"),
        ("note ID: 2", "second"),
    ]


def test_field_rules_extract_only_inside_each_candidate_without_row_drift():
    candidates = split_record_candidates(
        "key1\nred green blue\nkey2\norange",
        mode="fixed_lines",
        lines_per_record=2,
        use_first_line_as_title=False,
    )
    bundle = extract_candidate_fields(
        candidates,
        (
            FieldExtractionRule("キー", method="line", line_number=1),
            FieldExtractionRule("色1", method="split", line_number=2, value_index=1),
            FieldExtractionRule("色2", method="split", line_number=2, value_index=2),
            FieldExtractionRule("色3", method="split", line_number=2, value_index=3),
        ),
    )
    assert bundle.rows[0].values == ("key1", "red", "green", "blue")
    assert bundle.rows[1].values == ("key2", "orange", "", "")
    assert bundle.rows[0].identifier == candidates.candidates[0].identifier
    assert bundle.rows[1].identifier == candidates.candidates[1].identifier


def test_field_rules_can_slice_or_regex_match_one_line_records():
    candidates = split_record_candidates(
        "1234\n5678",
        mode="fixed_lines",
        lines_per_record=1,
        use_first_line_as_title=False,
    )
    bundle = extract_candidate_fields(
        candidates,
        (
            FieldExtractionRule("前半", method="slice", start_position=0, end_position=2),
            FieldExtractionRule("後半", method="regex", argument=r"\d{2}(\d{2})"),
        ),
    )
    assert [row.values for row in bundle.rows] == [("12", "34"), ("56", "78")]

    matches = extract_candidate_field_matches(
        candidates,
        (
            FieldExtractionRule("前半", method="slice", start_position=0, end_position=2),
            FieldExtractionRule("後半", method="regex", argument=r"\d{2}(\d{2})"),
        ),
    )
    assert (matches[0][0].start_offset, matches[0][0].end_offset) == (0, 2)
    assert (matches[0][1].start_offset, matches[0][1].end_offset) == (2, 4)


def test_bundle_exports_tsv_json_and_named_templates():
    bundle = parse_record_bundle(EXAMPLE, ("日付", "タイトル", "サイズ"))
    tsv = render_bundle_tsv(bundle)
    assert tsv.startswith("日付\tタイトル\tサイズ\n")
    assert "20220502\t第３回全国お笑い一回戦\t100mb" in tsv

    payload = json.loads(render_bundle_json(bundle))
    assert payload["title"] == "お笑い芸人のライブ集"
    assert payload["records"][0]["values"]["タイトル"] == "第３回全国お笑い一回戦"

    rendered = render_bundle_template(bundle, "{日付}_{タイトル}")
    assert rendered.splitlines()[0] == "20220502_第３回全国お笑い一回戦"


def test_transient_store_never_writes_and_replaces_only_its_memory_snapshot():
    store = TransientRecordBundleStore()
    bundle = parse_record_bundle("a\nb", ("キー", "値"), use_first_line_as_title=False)
    store.replace(bundle)
    assert store.bundle == bundle
    assert store.revision == 1
    store.clear()
    assert store.bundle is None
    assert store.revision == 2


def test_record_mapping_matches_keys_to_file_stems_and_builds_safe_plan(tmp_path: Path):
    first = tmp_path / "20220502.mp4"
    second = tmp_path / "20230312.mp4"
    first.write_text("first", encoding="utf-8")
    second.write_text("second", encoding="utf-8")
    bundle = parse_record_bundle(EXAMPLE, ("日付", "タイトル", "サイズ"))

    result = match_record_paths(
        bundle,
        (first, second),
        key_field="日付",
        output_field="タイトル",
    )
    assert result.is_ready
    preview = build_mapped_rename_preview(
        ((match.source, match.output_name) for match in result.matches)
    )
    assert preview.plan is not None
    assert preview.plan.renames[0].output == tmp_path / "第３回全国お笑い一回戦.mp4"

    outputs = execute_rename_plan(preview.plan)
    assert outputs[0].read_text(encoding="utf-8") == "first"
    assert outputs[1].read_text(encoding="utf-8") == "second"


def test_order_mapping_requires_the_same_number_of_paths_and_rows(tmp_path: Path):
    bundle = parse_record_bundle(EXAMPLE, ("日付", "タイトル", "サイズ"))
    result = match_record_paths(
        bundle,
        (tmp_path / "only-one.mp4",),
        key_field="日付",
        output_field="タイトル",
        mode="order",
    )
    assert not result.is_ready
    assert "同数" in result.issues[0]
