import json

from foundation import persistent_settings


def _write_location(path, value: str) -> None:  # type: ignore[no-untyped-def]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"user_root": value}) + "\n", encoding="utf-8")


def test_missing_json_locator_never_discovers_app_settings(monkeypatch, tmp_path):
    monkeypatch.setattr(persistent_settings, "BOOTSTRAP_PATH", tmp_path / "persistent_settings.json")
    assert persistent_settings.settings_file_status("video_encoder.json").state == "missing_bootstrap"


def test_json_locator_uses_only_the_declared_user_root(monkeypatch, tmp_path):
    location_file = tmp_path / "app" / "persistent_settings.json"
    user_root = tmp_path / "external" / "porta_user"
    monkeypatch.setattr(persistent_settings, "BOOTSTRAP_PATH", location_file)
    _write_location(location_file, str(user_root))
    assert persistent_settings.settings_file_status("video_encoder.json").state == "missing_directory"
    (user_root / "config").mkdir(parents=True)
    assert persistent_settings.settings_file_status("video_encoder.json").state == "missing_file"
    (user_root / "config" / "video_encoder.json").write_text("{}", encoding="utf-8")
    assert persistent_settings.settings_file_status("video_encoder.json").state == "ready"


def test_locator_uses_the_common_porta_token(monkeypatch, tmp_path):
    location_file = tmp_path / "persistent_settings.json"
    monkeypatch.setattr(persistent_settings, "BOOTSTRAP_PATH", location_file)
    location_file.parent.mkdir(exist_ok=True)
    location_file.write_text(persistent_settings.bootstrap_template_text(), encoding="utf-8")
    assert persistent_settings.configured_user_root() == persistent_settings.PROJECT_ROOT / "porta_user"


def test_locator_rejects_non_json_and_shell_expansion(monkeypatch, tmp_path):
    location_file = tmp_path / "persistent_settings.json"
    monkeypatch.setattr(persistent_settings, "BOOTSTRAP_PATH", location_file)
    location_file.write_text("porta_user\n", encoding="utf-8")
    assert persistent_settings.locate_settings_directory().state == "invalid_bootstrap"
    _write_location(location_file, "~/porta_user")
    assert persistent_settings.locate_settings_directory().state == "invalid_bootstrap"
    _write_location(location_file, "$HOME/porta_user")
    assert persistent_settings.locate_settings_directory().state == "invalid_bootstrap"


def test_user_can_explicitly_create_missing_json_locator_template(monkeypatch, tmp_path):
    location_file = tmp_path / "app" / "persistent_settings.json"
    monkeypatch.setattr(persistent_settings, "BOOTSTRAP_PATH", location_file)
    assert persistent_settings.create_bootstrap_template() == location_file
    assert json.loads(location_file.read_text(encoding="utf-8")) == {"user_root": "@PORTA/porta_user"}
    try:
        persistent_settings.create_bootstrap_template()
    except FileExistsError:
        pass
    else:
        raise AssertionError("existing locator must never be replaced by its template")


def test_save_locator_canonicalizes_to_json_and_common_tokens(monkeypatch, tmp_path):
    location_file = tmp_path / "app" / "persistent_settings.json"
    monkeypatch.setattr(persistent_settings, "BOOTSTRAP_PATH", location_file)
    persistent_settings.save_user_root_path("porta_user")
    assert json.loads(location_file.read_text(encoding="utf-8")) == {
        "user_root": str(location_file.parent / "porta_user")
    }


def test_reset_config_backs_up_existing_content_and_writes_nested_templates(monkeypatch, tmp_path):
    location_file = tmp_path / "app" / "persistent_settings.json"
    user_root = tmp_path / "porta_user"
    monkeypatch.setattr(persistent_settings, "BOOTSTRAP_PATH", location_file)
    _write_location(location_file, str(user_root))
    config = user_root / "config"
    config.mkdir(parents=True)
    (config / "old.json").write_text('{"old": true}\n', encoding="utf-8")
    result = persistent_settings.reset_config({"example.json": "{}\n", "shared/example.json": "{}\n"})
    assert (config / "example.json").is_file()
    assert (config / "shared/example.json").is_file()
    assert result.backup_directory is not None
    assert result.backup_directory.name.startswith("config_backup_")
    assert (result.backup_directory / "old.json").read_text(encoding="utf-8") == '{"old": true}\n'


def test_reset_config_without_existing_directory_needs_no_backup(monkeypatch, tmp_path):
    location_file = tmp_path / "app" / "persistent_settings.json"
    user_root = tmp_path / "porta_user"
    monkeypatch.setattr(persistent_settings, "BOOTSTRAP_PATH", location_file)
    _write_location(location_file, str(user_root))
    result = persistent_settings.reset_config({"example.json": "{}\n"})
    assert result.backup_directory is None
    assert (user_root / "config" / "example.json").is_file()


def test_reset_config_rejects_unsafe_template_names_without_touching_config(monkeypatch, tmp_path):
    location_file = tmp_path / "app" / "persistent_settings.json"
    user_root = tmp_path / "porta_user"
    monkeypatch.setattr(persistent_settings, "BOOTSTRAP_PATH", location_file)
    _write_location(location_file, str(user_root))
    config = user_root / "config"
    config.mkdir(parents=True)
    original = config / "keep.json"
    original.write_text("keep\n", encoding="utf-8")
    try:
        persistent_settings.reset_config({"../outside.json": "{}\n"})
    except ValueError:
        pass
    else:
        raise AssertionError("unsafe template name must be rejected")
    assert original.read_text(encoding="utf-8") == "keep\n"
    assert not (user_root / "outside.json").exists()
