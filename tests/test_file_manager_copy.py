from pathlib import Path

import pytest

from apps.file_tools.file_manager import copy_workflow
from apps.file_tools.file_manager.copy_workflow import (
    build_copy_preview,
    build_simple_copy_preview,
    execute_copy,
    execute_copy_plan,
    execute_one_to_one_copy,
    parse_target_paths,
    validate_copy_request,
    validate_one_to_one_copy_request,
)


def test_simple_copy_preview_shows_missing_required_values():
    preview = build_simple_copy_preview("", "")

    assert not preview.is_ready
    assert "チェック済み" in preview.text
    assert "コピー先フォルダ" in preview.text


def test_simple_copy_preview_describes_a_valid_copy(tmp_path: Path):
    source = tmp_path / "source.txt"
    source.write_text("content", encoding="utf-8")
    destination = tmp_path / "destination"
    destination.mkdir()

    preview = build_simple_copy_preview(str(source), str(destination))

    assert preview.is_ready
    assert preview.request is not None
    assert preview.request.sources == (source.resolve(),)
    assert "上書きせず" in preview.text


def test_copy_preview_lists_each_source_and_exact_planned_output(tmp_path: Path):
    source = tmp_path / "report.txt"
    source.write_text("new", encoding="utf-8")
    destination = tmp_path / "destination"
    destination.mkdir()
    (destination / "report.txt").write_text("existing", encoding="utf-8")

    preview = build_simple_copy_preview(str(source), str(destination))

    assert preview.plan is not None
    assert preview.plan.copies[0].output == destination / "report (1).txt"
    assert str(source) in preview.text
    assert str(destination / "report (1).txt") in preview.text
    assert "詳細な実行予定" in preview.text


def test_copy_plan_stops_before_copy_when_previewed_output_becomes_occupied(tmp_path: Path):
    source = tmp_path / "report.txt"
    source.write_text("new", encoding="utf-8")
    destination = tmp_path / "destination"
    destination.mkdir()
    preview = build_simple_copy_preview(str(source), str(destination))
    assert preview.plan is not None
    (destination / "report.txt").write_text("external", encoding="utf-8")

    with pytest.raises(FileExistsError, match="プレビュー後"):
        execute_copy_plan(preview.plan)

    assert (destination / "report.txt").read_text(encoding="utf-8") == "external"


def test_parse_target_paths_ignores_blank_lines_and_duplicate_paths(tmp_path: Path):
    target = tmp_path / "target.txt"
    target.write_text("content", encoding="utf-8")

    paths = parse_target_paths(f"\n {target} \n\n{target}\n")

    assert paths == (target.resolve(),)


def test_copy_validation_rejects_missing_sources_before_copying(tmp_path: Path):
    destination = tmp_path / "destination"
    destination.mkdir()
    missing = tmp_path / "missing.txt"

    with pytest.raises(FileNotFoundError, match="コピーは開始していません"):
        validate_copy_request(str(missing), str(destination))

    assert list(destination.iterdir()) == []


def test_copy_creates_unique_names_for_existing_destinations(tmp_path: Path):
    source = tmp_path / "report.txt"
    source.write_text("new", encoding="utf-8")
    destination = tmp_path / "destination"
    destination.mkdir()
    (destination / "report.txt").write_text("old", encoding="utf-8")

    request = validate_copy_request(str(source), str(destination))
    copied = execute_copy(request)

    assert copied == [destination / "report (1).txt"]
    assert copied[0].read_text(encoding="utf-8") == "new"


def test_copy_preserves_a_symbolic_link_instead_of_copying_its_target(tmp_path: Path):
    target = tmp_path / "target.txt"
    target.write_text("original", encoding="utf-8")
    link = tmp_path / "visible-link.txt"
    link.symlink_to(target)
    destination = tmp_path / "destination"
    destination.mkdir()

    preview = build_simple_copy_preview(str(link), str(destination))
    assert preview.plan is not None
    assert preview.plan.copies[0].source == link
    copied = execute_copy_plan(preview.plan)

    output = destination / link.name
    assert copied == [output]
    assert output.is_symlink()
    assert output.readlink() == link.readlink()
    assert target.read_text(encoding="utf-8") == "original"


def test_copy_removes_outputs_created_by_this_run_after_a_detected_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    first.write_text("first", encoding="utf-8")
    second.write_text("second", encoding="utf-8")
    destination = tmp_path / "destination"
    destination.mkdir()
    request = validate_copy_request(f"{first}\n{second}", str(destination))
    original_copy = copy_workflow.copy_or_move
    calls = 0

    def fail_after_second_copy(*args, **kwargs):
        nonlocal calls
        calls += 1
        result = original_copy(*args, **kwargs)
        if calls == 2:
            raise OSError("simulated failure after writing")
        return result

    monkeypatch.setattr(copy_workflow, "copy_or_move", fail_after_second_copy)

    with pytest.raises(OSError, match="削除を試みました"):
        execute_copy(request)

    assert list(destination.iterdir()) == []


def test_one_to_one_copy_requires_equal_source_and_destination_counts(tmp_path: Path):
    source = tmp_path / "source.txt"
    source.write_text("content", encoding="utf-8")
    destination = tmp_path / "destination"
    destination.mkdir()

    with pytest.raises(ValueError, match="件数を一致"):
        validate_one_to_one_copy_request(str(source), f"{destination}\n{destination}")


def test_one_to_one_copy_pairs_sources_in_line_order(tmp_path: Path):
    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    first.write_text("first", encoding="utf-8")
    second.write_text("second", encoding="utf-8")
    first_destination = tmp_path / "first_destination"
    second_destination = tmp_path / "second_destination"
    first_destination.mkdir()
    second_destination.mkdir()

    request = validate_one_to_one_copy_request(
        f"{first}\n{second}", f"{first_destination}\n{second_destination}"
    )
    copied = execute_one_to_one_copy(request)

    assert copied == [first_destination / "first.txt", second_destination / "second.txt"]
    assert copied[0].read_text(encoding="utf-8") == "first"
    assert copied[1].read_text(encoding="utf-8") == "second"


def test_one_to_one_preview_explains_ordered_pairs(tmp_path: Path):
    source = tmp_path / "source.txt"
    source.write_text("content", encoding="utf-8")
    destination = tmp_path / "destination"
    destination.mkdir()

    preview = build_copy_preview(str(source), str(destination), mode="one_to_one")

    assert preview.is_ready
    assert "上から順" in preview.text
