from pathlib import Path
import json

from foundation import persistent_settings, user_space


def _write_location(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"user_root": value}) + "\n", encoding="utf-8")


def test_user_space_uses_only_the_explicit_root(tmp_path: Path, monkeypatch) -> None:
    location_file = tmp_path / "app" / "persistent_settings.json"
    monkeypatch.setattr(persistent_settings, "BOOTSTRAP_PATH", location_file)

    assert user_space.configured_paths() is None

    external_root = tmp_path / "elsewhere" / "porta_user"
    paths = user_space.save_root_path(str(external_root))

    assert paths == user_space.paths_for_root(external_root)
    assert paths is not None
    assert paths.config == external_root / "config"
    assert paths.ai_models == external_root / "local_data/ai/models"
    assert not external_root.exists()
    assert json.loads(location_file.read_text(encoding="utf-8")) == {"user_root": str(external_root)}


def test_user_space_structure_is_created_only_on_request(tmp_path: Path, monkeypatch) -> None:
    location_file = tmp_path / "app" / "persistent_settings.json"
    root = tmp_path / "external" / "porta_user"
    _write_location(location_file, str(root))
    monkeypatch.setattr(persistent_settings, "BOOTSTRAP_PATH", location_file)

    paths = user_space.create_configured_directories()

    assert paths.root == root
    assert all(directory.is_dir() for directory in paths.directories())


def test_relative_user_space_root_is_resolved_from_the_locator(
    tmp_path: Path, monkeypatch
) -> None:
    location_file = tmp_path / "app" / "persistent_settings.json"
    unrelated_cwd = tmp_path / "working-directory"
    unrelated_cwd.mkdir()
    monkeypatch.setattr(persistent_settings, "BOOTSTRAP_PATH", location_file)
    monkeypatch.chdir(unrelated_cwd)

    paths = user_space.save_root_path("porta_user")

    assert paths is not None
    assert paths.root == location_file.parent / "porta_user"
    assert user_space.configured_root_text() == str(location_file.parent / "porta_user")
    assert json.loads(location_file.read_text(encoding="utf-8")) == {
        "user_root": str(location_file.parent / "porta_user")
    }
