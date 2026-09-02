from pathlib import Path

from foundation import persistent_settings, shared_launchers


def _configure(tmp_path: Path, monkeypatch) -> tuple[Path, Path]:  # type: ignore[no-untyped-def]
    location_file = tmp_path / "app" / "persistent_settings_location.txt"
    user_root = tmp_path / "user"
    location_file.parent.mkdir()
    location_file.write_text(f"# PORTA_LOCATION_V1\n{user_root}\n", encoding="utf-8")
    monkeypatch.setattr(persistent_settings, "BOOTSTRAP_PATH", location_file)
    return user_root, location_file


def test_shared_launcher_paths_support_absolute_and_file_relative_values(tmp_path: Path):
    base = tmp_path / "config" / "shared"
    absolute = tmp_path / "absolute-apps"

    paths = shared_launchers.parse_text(
        f"# comment\n../relative-apps\n{absolute}\n../relative-apps\n",
        base_directory=base,
    )

    assert paths == (tmp_path / "config" / "relative-apps", absolute)


def test_porta_token_tracks_the_current_project_root(tmp_path: Path):
    paths = shared_launchers.parse_text(
        "@PORTA/standalone_apps\n",
        base_directory=tmp_path,
    )

    assert paths == (shared_launchers.DEFAULT_STANDALONE_APPS_DIRECTORY,)


def test_shared_launcher_paths_reject_shell_expansion(tmp_path: Path):
    for value in ("~/apps", "$HOME/apps"):
        try:
            shared_launchers.parse_text(value, base_directory=tmp_path)
        except ValueError:
            pass
        else:
            raise AssertionError("shell expansion syntax must be rejected")


def test_external_program_locations_save_and_load_from_config(tmp_path: Path, monkeypatch):
    user_root, _location_file = _configure(tmp_path, monkeypatch)
    apps = tmp_path / "apps"

    saved = shared_launchers.save_external_text(f"# locations\n{apps}\n")
    loaded = shared_launchers.load_external_locations()

    assert saved == user_root / "config" / "shared" / "external_program_locations.txt"
    assert loaded.state == "ready"
    assert loaded.directories == (apps,)


def test_standalone_apps_location_requires_exactly_one_path(tmp_path: Path, monkeypatch):
    user_root, _location_file = _configure(tmp_path, monkeypatch)
    first = tmp_path / "first"
    second = tmp_path / "second"

    saved = shared_launchers.save_standalone_apps_text(f"{first}\n")
    loaded = shared_launchers.load_standalone_apps_location()

    assert saved == user_root / "config" / "shared" / "standalone_apps_location.txt"
    assert loaded.directories == (first,)
    try:
        shared_launchers.save_standalone_apps_text(f"{first}\n{second}\n")
    except ValueError:
        pass
    else:
        raise AssertionError("standalone apps location must contain exactly one path")


def test_missing_program_location_files_are_optional(tmp_path: Path, monkeypatch):
    user_root, _location_file = _configure(tmp_path, monkeypatch)
    (user_root / "config").mkdir(parents=True)

    standalone_apps = shared_launchers.load_standalone_apps_location()
    external = shared_launchers.load_external_locations()

    assert standalone_apps.state == "missing_file"
    assert external.state == "missing_file"
    assert standalone_apps.directories == external.directories == ()
