from pathlib import Path
import json

from apps.system_tools.external_app_launcher import locations
from foundation import persistent_settings


def _configure(tmp_path: Path, monkeypatch) -> tuple[Path, Path]:  # type: ignore[no-untyped-def]
    location_file = tmp_path / "app" / "persistent_settings.json"
    user_root = tmp_path / "user"
    location_file.parent.mkdir()
    location_file.write_text(json.dumps({"user_root": str(user_root)}), encoding="utf-8")
    monkeypatch.setattr(persistent_settings, "BOOTSTRAP_PATH", location_file)
    return user_root, location_file


def test_external_locations_support_absolute_and_file_relative_values(tmp_path: Path) -> None:
    base = tmp_path / "config" / "shared"
    absolute = tmp_path / "absolute-apps"

    paths = locations.parse_text(
        json.dumps({"locations": ["../relative-apps", str(absolute), "../relative-apps"]}),
        base_directory=base,
    )

    assert paths == (tmp_path / "config" / "relative-apps", absolute)


def test_external_locations_accept_common_tokens_and_reject_shell_expansion(tmp_path: Path) -> None:
    portable = locations.parse_text(
        '{"locations": ["@PORTA/external-apps"]}', base_directory=tmp_path
    )
    assert portable == (persistent_settings.PROJECT_ROOT / "external-apps",)
    for value in ("~/apps", "$HOME/apps", "@UNKNOWN/apps"):
        try:
            locations.parse_text(json.dumps({"locations": [value]}), base_directory=tmp_path)
        except ValueError:
            pass
        else:
            raise AssertionError("unsafe external-program location must be rejected")


def test_external_program_locations_save_and_load_from_config(tmp_path: Path, monkeypatch) -> None:
    user_root, _location_file = _configure(tmp_path, monkeypatch)
    apps = tmp_path / "apps"

    saved = locations.save_external_text(json.dumps({"locations": [str(apps)]}))
    loaded = locations.load_external_locations()

    assert saved == user_root / "config" / "shared" / "external_program_locations.json"
    assert loaded.state == "ready"
    assert loaded.directories == (apps,)


def test_external_locations_template_starts_empty() -> None:
    assert json.loads(locations.external_template_text()) == {"locations": []}


def test_missing_external_program_locations_are_optional(tmp_path: Path, monkeypatch) -> None:
    user_root, _location_file = _configure(tmp_path, monkeypatch)
    (user_root / "config").mkdir(parents=True)

    loaded = locations.load_external_locations()

    assert loaded.state == "missing_file"
    assert loaded.directories == ()
