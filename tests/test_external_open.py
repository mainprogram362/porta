from __future__ import annotations

import os
from pathlib import Path

import pytest

from foundation.external_open import (
    ExternalOpenValidationError,
    build_external_open_request,
)


def test_external_open_accepts_file_uris_and_deduplicates_in_order(tmp_path: Path):
    first = tmp_path / "日本語 file.txt"
    second = tmp_path / "folder"
    first.write_text("ok", encoding="utf-8")
    second.mkdir()

    request = build_external_open_request(
        "file-manager",
        [first.as_uri(), str(second), str(first)],
    )

    assert request.target == "file-manager"
    assert request.paths == (first, second)


def test_external_open_accepts_the_common_operation_chooser(tmp_path: Path):
    source = tmp_path / "source.txt"
    source.touch()
    request = build_external_open_request("choose", [str(source)])
    assert request.target == "choose"
    assert request.paths == (source,)


def test_external_open_rejects_the_whole_mixed_selection_and_lists_every_problem(tmp_path: Path):
    ordinary = tmp_path / "ordinary.txt"
    ordinary.write_text("ok", encoding="utf-8")
    linked = tmp_path / "linked.txt"
    linked.symlink_to(ordinary)
    broken = tmp_path / "broken.txt"
    broken.symlink_to(tmp_path / "absent-target")
    missing = tmp_path / "missing.txt"
    fifo = tmp_path / "pipe"
    os.mkfifo(fifo)

    with pytest.raises(ExternalOpenValidationError) as captured:
        build_external_open_request(
            "media-organizer",
            [str(ordinary), str(linked), str(broken), str(missing), str(fifo)],
        )

    error = captured.value
    assert len(error.issues) == 4
    assert str(linked) in error.summary()
    assert str(broken) in error.summary()
    assert str(missing) in error.summary()
    assert str(fifo) in error.summary()
    assert "今回は1件も取り込みません" in error.summary()


@pytest.mark.parametrize("value", ["https://example.com/file", "file://other-host/path"])
def test_external_open_rejects_nonlocal_uris(value: str):
    with pytest.raises(ExternalOpenValidationError):
        build_external_open_request("video-encoder", [value])


def test_external_open_requires_at_least_one_path():
    with pytest.raises(ExternalOpenValidationError) as captured:
        build_external_open_request("file-manager", [])
    assert "選択なし" in captured.value.summary()


def test_external_open_rejects_unknown_receiver(tmp_path: Path):
    source = tmp_path / "source"
    source.touch()
    with pytest.raises(ValueError, match="未対応"):
        build_external_open_request("unknown", [str(source)])
