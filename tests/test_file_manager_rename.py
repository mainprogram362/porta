from pathlib import Path

import pytest

from apps.file_tools.file_manager.rename_workflow import (
    RenameRule,
    build_rename_preview,
    execute_rename_plan,
)


def test_rename_preview_applies_ordered_rules_to_a_file_stem(tmp_path: Path):
    source = tmp_path / "movie.mp4"
    source.write_text("content", encoding="utf-8")
    rules = (
        RenameRule("insert", text="222t", first=5),
        RenameRule("append", text="_done"),
    )

    preview = build_rename_preview(str(source), rules)

    assert preview.is_ready
    assert preview.plan is not None
    assert preview.plan.renames[0].output == tmp_path / "movi222te_done.mp4"
    assert str(source) in preview.text
    assert str(tmp_path / "movi222te_done.mp4") in preview.text


def test_rename_preview_supports_range_and_end_removal(tmp_path: Path):
    source = tmp_path / "abcdef.txt"
    source.write_text("content", encoding="utf-8")
    rules = (
        RenameRule("remove_range", first=2, second=4),
        RenameRule("trim_end", first=1),
    )

    preview = build_rename_preview(str(source), rules)

    assert preview.plan is not None
    assert preview.plan.renames[0].output == tmp_path / "ae.txt"


def test_rename_can_include_the_extension_when_explicitly_selected(tmp_path: Path):
    source = tmp_path / "movie.mp4"
    source.write_text("content", encoding="utf-8")

    preview = build_rename_preview(
        str(source), [RenameRule("append", text=".bak")], include_extension=True
    )

    assert preview.plan is not None
    assert preview.plan.renames[0].output == tmp_path / "movie.mp4.bak"
    assert "拡張子を含む" in preview.text


def test_rename_preview_supports_text_removal_and_replacement(tmp_path: Path):
    source = tmp_path / "report_2025_draft.txt"
    source.write_text("content", encoding="utf-8")
    rules = (
        RenameRule("remove_text", text="_draft"),
        RenameRule("replace", text="2025", replacement="2026"),
    )

    preview = build_rename_preview(str(source), rules)

    assert preview.plan is not None
    assert preview.plan.renames[0].output == tmp_path / "report_2026.txt"


def test_rename_preview_supports_explicit_regex_removal_and_replacement(tmp_path: Path):
    source = tmp_path / "episode_001_draft.mp4"
    source.write_text("content", encoding="utf-8")
    rules = (
        RenameRule("remove_text", text=r"_draft$", use_regex=True),
        RenameRule("replace", text=r"_(\d+)$", replacement=r"-\1", use_regex=True),
    )

    preview = build_rename_preview(str(source), rules)

    assert preview.plan is not None
    assert preview.plan.renames[0].output == tmp_path / "episode-001.mp4"


def test_rename_preview_rejects_invalid_regex_before_any_rename(tmp_path: Path):
    source = tmp_path / "episode_001.mp4"
    source.write_text("content", encoding="utf-8")

    preview = build_rename_preview(str(source), [RenameRule("remove_text", text="(", use_regex=True)])

    assert not preview.is_ready
    assert "正規表現" in preview.text
    assert source.exists()


def test_rename_rejects_an_existing_output_before_starting(tmp_path: Path):
    source = tmp_path / "movie.txt"
    source.write_text("source", encoding="utf-8")
    existing = tmp_path / "movie_done.txt"
    existing.write_text("existing", encoding="utf-8")

    preview = build_rename_preview(str(source), [RenameRule("append", text="_done")])

    assert not preview.is_ready
    assert "すでに存在" in preview.text
    assert source.exists()
    assert existing.read_text(encoding="utf-8") == "existing"


def test_execute_rename_plan_changes_names_without_a_temporary_copy(tmp_path: Path):
    source = tmp_path / "movie.txt"
    source.write_text("content", encoding="utf-8")
    preview = build_rename_preview(str(source), [RenameRule("prepend", text="new_")])
    assert preview.plan is not None

    results = execute_rename_plan(preview.plan)

    assert results == [tmp_path / "new_movie.txt"]
    assert not source.exists()
    assert results[0].read_text(encoding="utf-8") == "content"


def test_rename_plan_stops_if_an_output_appears_after_preview(tmp_path: Path):
    source = tmp_path / "movie.txt"
    source.write_text("content", encoding="utf-8")
    preview = build_rename_preview(str(source), [RenameRule("append", text="_done")])
    assert preview.plan is not None
    output = tmp_path / "movie_done.txt"
    output.write_text("external", encoding="utf-8")

    with pytest.raises(FileExistsError, match="すでに存在"):
        execute_rename_plan(preview.plan)

    assert source.exists()
    assert output.read_text(encoding="utf-8") == "external"
