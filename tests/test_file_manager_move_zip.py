from pathlib import Path

from apps.file_tools.file_manager import move_workflow
from apps.file_tools.file_manager.move_workflow import build_move_preview, execute_move_plan
from apps.file_tools.file_manager.zip_workflow import build_zip_preview, execute_zip_plan


def test_move_renames_without_copying_on_same_filesystem(tmp_path: Path):
    source = tmp_path / "source.txt"
    destination = tmp_path / "destination"
    source.write_text("content", encoding="utf-8")
    destination.mkdir()
    original_inode = source.stat().st_ino

    preview = build_move_preview(str(source), str(destination), mode="simple")
    assert preview.plan is not None
    assert "同じファイルシステム内の即時移動: 1 件" in preview.text
    assert "別ファイルシステムへのコピー・照合・元削除: 0 件" in preview.text
    outputs = execute_move_plan(preview.plan)

    assert outputs == [destination / "source.txt"]
    assert outputs[0].read_text(encoding="utf-8") == "content"
    assert outputs[0].stat().st_ino == original_inode
    assert not source.exists()


def test_move_uses_copy_verify_delete_across_filesystems(tmp_path: Path, monkeypatch):
    source = tmp_path / "source.txt"
    destination = tmp_path / "destination"
    source.write_text("content", encoding="utf-8")
    destination.mkdir()
    original_inode = source.stat().st_ino

    preview = build_move_preview(str(source), str(destination), mode="simple")
    assert preview.plan is not None
    monkeypatch.setattr(move_workflow, "_is_same_filesystem", lambda _planned: False)

    outputs = execute_move_plan(preview.plan)

    assert outputs == [destination / "source.txt"]
    assert outputs[0].read_text(encoding="utf-8") == "content"
    assert outputs[0].stat().st_ino != original_inode
    assert not source.exists()


def test_move_reports_and_keeps_completed_renames_if_a_later_rename_fails(
    tmp_path: Path, monkeypatch
):
    source_a = tmp_path / "a.txt"
    source_b = tmp_path / "b.txt"
    source_a.write_text("a", encoding="utf-8")
    source_b.write_text("b", encoding="utf-8")
    destination = tmp_path / "destination"
    destination.mkdir()
    preview = build_move_preview(
        f"{source_a}\n{source_b}", str(destination), mode="simple"
    )
    assert preview.plan is not None
    real_rename = move_workflow.rename_noreplace

    def fail_for_second_source(source, output):
        if Path(source) == source_b:
            raise OSError("simulated failure")
        real_rename(source, output)

    monkeypatch.setattr(move_workflow, "rename_noreplace", fail_for_second_source)

    try:
        execute_move_plan(preview.plan)
    except OSError as exc:
        assert "保持" in str(exc)
        assert str(destination / "a.txt") in str(exc)
    else:
        raise AssertionError("move should have failed")

    assert not source_a.exists()
    assert (destination / "a.txt").read_text(encoding="utf-8") == "a"
    assert source_b.read_text(encoding="utf-8") == "b"
    assert not (destination / "b.txt").exists()


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
