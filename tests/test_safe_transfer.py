from pathlib import Path

import pytest

from foundation import safe_transfer
from foundation.operation_progress import observe_operation, OperationCancelled
from apps.file_tools.file_manager import copy_workflow, move_workflow


@pytest.mark.parametrize("kind", ["file", "directory", "symlink"])
def test_competing_output_is_not_removed_or_overwritten(tmp_path, monkeypatch, kind):
    source = tmp_path / "source"
    source.write_text("mine")
    folder = tmp_path / "out"
    folder.mkdir()
    preview = copy_workflow.build_simple_copy_preview(str(source), str(folder))
    output = folder / source.name
    original = safe_transfer.rename_noreplace

    def compete(staged, destination):
        if kind == "file":
            output.write_text("other writer")
        elif kind == "directory":
            output.mkdir()
            (output / "other").write_text("other writer")
        else:
            output.symlink_to(source)
        original(staged, destination)

    monkeypatch.setattr(safe_transfer, "rename_noreplace", compete)
    with pytest.raises(OSError):
        copy_workflow.execute_copy_plan(preview.plan)
    assert output.is_symlink() if kind == "symlink" else output.exists()
    if kind == "file":
        assert output.read_text() == "other writer"
    elif kind == "directory":
        assert (output / "other").read_text() == "other writer"
    assert source.read_text() == "mine"
    assert not list(folder.glob(".porta-copy-*"))


def test_collision_before_copy_is_never_cleaned_up(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.write_text("mine")
    folder = tmp_path / "out"
    folder.mkdir()
    preview = copy_workflow.build_simple_copy_preview(str(source), str(folder))
    original = copy_workflow.copy_or_move

    def compete(*args, **kwargs):
        (folder / source.name).write_text("other")
        return original(*args, **kwargs)

    monkeypatch.setattr(copy_workflow, "copy_or_move", compete)
    with pytest.raises(OSError):
        copy_workflow.execute_copy_plan(preview.plan)
    assert (folder / source.name).read_text() == "other"


def test_cancel_during_copy_never_publishes_partial_file(tmp_path):
    source = tmp_path / "source"
    source.write_bytes(b"a" * (2 * 1024 * 1024))
    output = tmp_path / "output"
    calls = 0

    def cancel(_):
        nonlocal calls
        calls += 1
        if calls == 4:
            raise OperationCancelled("cancel")

    with observe_operation(cancel), pytest.raises(OperationCancelled):
        safe_transfer.copy_noreplace(source, output)
    assert not output.exists()
    assert not list(tmp_path.glob(".porta-copy-*"))


def test_content_verification_rejects_equal_size_different_bytes(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.write_bytes(b"AAAA")
    b.write_bytes(b"BBBB")
    assert not move_workflow._paths_match(a, b)
