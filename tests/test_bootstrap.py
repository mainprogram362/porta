from pathlib import Path
from types import SimpleNamespace

from scripts import bootstrap


def test_environment_without_location_marker_is_rebuilt(monkeypatch) -> None:
    monkeypatch.setattr(bootstrap, "_environment_works", lambda: True)
    monkeypatch.setattr(bootstrap, "_recorded_root", lambda: None)

    needs_setup, reason = bootstrap._needs_setup()

    assert needs_setup is True
    assert "確認できません" in reason


def test_environment_marker_detects_a_moved_porta(monkeypatch, tmp_path: Path) -> None:
    old_root = tmp_path / "old" / "porta"
    monkeypatch.setattr(bootstrap, "_environment_works", lambda: True)
    monkeypatch.setattr(bootstrap, "_recorded_root", lambda: old_root)

    needs_setup, reason = bootstrap._needs_setup()

    assert needs_setup is True
    assert str(old_root) in reason
    assert str(bootstrap.ROOT) in reason


def test_environment_at_current_location_is_reused(monkeypatch) -> None:
    monkeypatch.setattr(bootstrap, "_environment_works", lambda: True)
    monkeypatch.setattr(bootstrap, "_recorded_root", lambda: bootstrap.ROOT)

    assert bootstrap._needs_setup() == (False, "")


def test_build_uses_base_python_instead_of_an_activated_venv(monkeypatch, tmp_path: Path) -> None:
    root = tmp_path / "porta"
    venv = root / ".venv"
    venv_python = venv / "bin" / "python"
    marker = venv / ".porta-root"
    base_python = tmp_path / "system" / "python3"
    calls: list[list[str]] = []

    root.mkdir()
    monkeypatch.setattr(bootstrap, "ROOT", root)
    monkeypatch.setattr(bootstrap, "VENV", venv)
    monkeypatch.setattr(bootstrap, "VENV_PYTHON", venv_python)
    monkeypatch.setattr(bootstrap, "LOCATION_MARKER", marker)
    monkeypatch.setattr(bootstrap, "BASE_PYTHON", base_python)

    def fake_run(command, **_kwargs):  # type: ignore[no-untyped-def]
        calls.append([str(value) for value in command])
        if command[1:3] == ["-m", "venv"]:
            venv_python.parent.mkdir(parents=True)
            venv_python.touch()
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(bootstrap.subprocess, "run", fake_run)

    bootstrap._build_environment()

    assert calls[0] == [str(base_python), "-m", "venv", str(venv)]
    assert calls[1] == [
        str(venv_python),
        "-m",
        "pip",
        "install",
        "--no-cache-dir",
        "-e",
        str(root),
    ]
    assert marker.read_text(encoding="utf-8") == f"{root}\n"
