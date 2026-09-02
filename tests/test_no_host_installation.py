from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_bootstrap_does_not_use_the_users_pip_cache() -> None:
    source = (ROOT / "scripts" / "bootstrap.py").read_text(encoding="utf-8")

    assert '"--no-cache-dir"' in source


def test_foundation_has_no_implicit_home_garbage_directory() -> None:
    source = (ROOT / "src" / "foundation" / "filesystem.py").read_text(encoding="utf-8")

    assert 'Path.home() / ".garbage"' not in source
