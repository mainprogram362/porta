from pathlib import Path

from foundation import persistent_settings, user_space


def _write_location(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"# PORTA_LOCATION_V1\n{value}\n", encoding="utf-8")


def test_user_space_uses_only_the_explicit_root(tmp_path: Path, monkeypatch) -> None:
    location_file = tmp_path / "app" / "persistent_settings_location.txt"
    monkeypatch.setattr(persistent_settings, "BOOTSTRAP_PATH", location_file)

    assert user_space.configured_paths() is None

    external_root = tmp_path / "elsewhere" / "porta_user"
    paths = user_space.save_root_path(str(external_root))

    assert paths == user_space.paths_for_root(external_root)
    assert paths is not None
    assert paths.config == external_root / "config"
    assert paths.ai_models == external_root / "local_data/ai/models"
    assert not external_root.exists()
    assert location_file.read_text(encoding="utf-8") == (
        f"# PORTA_LOCATION_V1\n{external_root}\n"
    )


def test_user_space_structure_is_created_only_on_request(tmp_path: Path, monkeypatch) -> None:
    location_file = tmp_path / "app" / "persistent_settings_location.txt"
    root = tmp_path / "external" / "porta_user"
    _write_location(location_file, str(root))
    monkeypatch.setattr(persistent_settings, "BOOTSTRAP_PATH", location_file)

    paths = user_space.create_configured_directories()

    assert paths.root == root
    assert all(directory.is_dir() for directory in paths.directories())


def test_relative_user_space_root_is_resolved_from_the_locator(
    tmp_path: Path, monkeypatch
) -> None:
    location_file = tmp_path / "app" / "persistent_settings_location.txt"
    unrelated_cwd = tmp_path / "working-directory"
    unrelated_cwd.mkdir()
    monkeypatch.setattr(persistent_settings, "BOOTSTRAP_PATH", location_file)
    monkeypatch.chdir(unrelated_cwd)

    paths = user_space.save_root_path("porta_user")

    assert paths is not None
    assert paths.root == location_file.parent / "porta_user"
    assert user_space.configured_root_text() == "porta_user"
    assert location_file.read_text(encoding="utf-8") == "# PORTA_LOCATION_V1\nporta_user\n"
