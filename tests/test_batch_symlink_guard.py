from pathlib import Path

from foundation.batch import collect_items


def test_collect_items_skips_symlink_dirs(tmp_path: Path):
    root = tmp_path / "root"
    real_dir = root / "real"
    real_dir.mkdir(parents=True)
    target = real_dir / "file.txt"
    target.write_text("hello", encoding="utf-8")

    loop_dir = root / "loop"
    loop_dir.mkdir()
    (loop_dir / "back").symlink_to(real_dir, target_is_directory=True)
    # Create a symlink loop inside the target tree.
    (real_dir / "loop_link").symlink_to(loop_dir, target_is_directory=True)

    items = collect_items(root, recursive=True, include_files=True, include_dirs=False)
    assert target in items
    assert len(items) == 1
