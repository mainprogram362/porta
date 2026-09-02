from pathlib import Path

from apps.file_tools.file_manager.move_workflow import build_move_preview, execute_move_plan
from apps.file_tools.file_manager.zip_workflow import build_zip_preview, execute_zip_plan


def test_move_copies_verifies_then_removes_source(tmp_path: Path):
    source = tmp_path / "source.txt"
    destination = tmp_path / "destination"
    source.write_text("content", encoding="utf-8")
    destination.mkdir()

    preview = build_move_preview(str(source), str(destination), mode="simple")
    assert preview.plan is not None
    outputs = execute_move_plan(preview.plan)

    assert outputs == [destination / "source.txt"]
    assert outputs[0].read_text(encoding="utf-8") == "content"
    assert not source.exists()


def test_move_preserves_the_symbolic_link_and_leaves_its_target_untouched(tmp_path: Path):
    target = tmp_path / "target.txt"
    target.write_text("content", encoding="utf-8")
    source = tmp_path / "visible-link.txt"
    source.symlink_to(target)
    destination = tmp_path / "destination"
    destination.mkdir()

    preview = build_move_preview(str(source), str(destination), mode="simple")
    assert preview.plan is not None
    outputs = execute_move_plan(preview.plan)

    output = destination / source.name
    assert outputs == [output]
    assert output.is_symlink()
    assert output.readlink() == target
    assert not source.exists()
    assert target.read_text(encoding="utf-8") == "content"


def test_zip_creates_and_verifies_an_archive_in_place(tmp_path: Path):
    source = tmp_path / "source.txt"
    source.write_text("content", encoding="utf-8")

    preview = build_zip_preview(str(source), "", mode="in_place")
    assert preview.plan is not None
    outputs = execute_zip_plan(preview.plan)

    assert outputs == [tmp_path / "source.txt.zip"]
    assert source.exists()


def test_zip_rejects_symbolic_links_instead_of_following_them(tmp_path: Path):
    target = tmp_path / "target.txt"
    target.write_text("content", encoding="utf-8")
    link = tmp_path / "visible-link.txt"
    link.symlink_to(target)

    preview = build_zip_preview(str(link), "", mode="in_place")

    assert preview.plan is None
    assert "シンボリックリンク" in preview.text
    assert not (tmp_path / "visible-link.txt.zip").exists()
