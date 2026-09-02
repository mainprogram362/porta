from foundation import persistent_settings


def _write_location(path, value: str) -> None:  # type: ignore[no-untyped-def]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"# PORTA_LOCATION_V1\n{value}\n", encoding="utf-8")


def test_missing_location_file_never_discovers_app_settings(monkeypatch, tmp_path):
    monkeypatch.setattr(persistent_settings, "BOOTSTRAP_PATH", tmp_path / "location.txt")

    status = persistent_settings.settings_file_status("video_encoder.json")

    assert status.state == "missing_bootstrap"


def test_location_file_uses_only_user_root_config(monkeypatch, tmp_path):
    location_file = tmp_path / "app" / "persistent_settings_location.txt"
    user_root = tmp_path / "external" / "porta_user"
    monkeypatch.setattr(persistent_settings, "BOOTSTRAP_PATH", location_file)
    _write_location(location_file, str(user_root))

    assert persistent_settings.settings_file_status("video_encoder.json").state == "missing_directory"
    (user_root / "config").mkdir(parents=True)
    assert persistent_settings.settings_file_status("video_encoder.json").state == "missing_file"
    (user_root / "config" / "video_encoder.json").write_text("{}", encoding="utf-8")
    assert persistent_settings.settings_file_status("video_encoder.json").state == "ready"


def test_invalid_location_file_is_not_adopted(monkeypatch, tmp_path):
    location_file = tmp_path / "persistent_settings_location.txt"
    monkeypatch.setattr(persistent_settings, "BOOTSTRAP_PATH", location_file)
    location_file.write_text("first\nsecond\n", encoding="utf-8")

    status = persistent_settings.locate_settings_directory()

    assert status.state == "invalid_bootstrap"
    assert status.directory is None


def test_location_template_uses_the_default_relative_user_root(monkeypatch, tmp_path):
    location_file = tmp_path / "app" / "persistent_settings_location.txt"
    monkeypatch.setattr(persistent_settings, "BOOTSTRAP_PATH", location_file)
    location_file.parent.mkdir()
    location_file.write_text(persistent_settings.bootstrap_template_text(), encoding="utf-8")

    status = persistent_settings.locate_settings_directory()

    assert status.state == "missing_directory"
    assert status.directory == location_file.parent / "porta_user" / "config"


def test_user_can_explicitly_create_missing_location_template(monkeypatch, tmp_path):
    location_file = tmp_path / "app" / "persistent_settings_location.txt"
    monkeypatch.setattr(persistent_settings, "BOOTSTRAP_PATH", location_file)

    result = persistent_settings.create_bootstrap_template()

    assert result == location_file
    assert location_file.read_text(encoding="utf-8") == (
        "# PORTA_LOCATION_V1\nporta_user\n"
    )
    try:
        persistent_settings.create_bootstrap_template()
    except FileExistsError:
        pass
    else:
        raise AssertionError("existing locator must never be replaced by its template")


def test_relative_user_root_is_resolved_from_the_location_file(monkeypatch, tmp_path):
    location_file = tmp_path / "app" / "persistent_settings_location.txt"
    unrelated_cwd = tmp_path / "working"
    unrelated_cwd.mkdir()
    monkeypatch.setattr(persistent_settings, "BOOTSTRAP_PATH", location_file)
    monkeypatch.chdir(unrelated_cwd)
    _write_location(location_file, "../porta_user")

    status = persistent_settings.locate_settings_directory()

    assert status.directory == tmp_path / "porta_user" / "config"
    assert persistent_settings.configured_user_root_text() == "../porta_user"


def test_location_file_rejects_shell_expansion_syntax(monkeypatch, tmp_path):
    location_file = tmp_path / "persistent_settings_location.txt"
    monkeypatch.setattr(persistent_settings, "BOOTSTRAP_PATH", location_file)

    for value in ("~/porta_user", "$HOME/porta_user"):
        location_file.write_text(value + "\n", encoding="utf-8")
        assert persistent_settings.locate_settings_directory().state == "invalid_bootstrap"


def test_save_location_normalizes_to_the_formal_text_format(monkeypatch, tmp_path):
    location_file = tmp_path / "app" / "persistent_settings_location.txt"
    monkeypatch.setattr(persistent_settings, "BOOTSTRAP_PATH", location_file)

    persistent_settings.save_bootstrap_text("\n# comment\n../porta_user\n")

    assert location_file.read_text(encoding="utf-8") == "# PORTA_LOCATION_V1\n../porta_user\n"


def test_reset_config_backs_up_existing_content_and_writes_nested_templates(monkeypatch, tmp_path):
    location_file = tmp_path / "app" / "persistent_settings_location.txt"
    user_root = tmp_path / "porta_user"
    monkeypatch.setattr(persistent_settings, "BOOTSTRAP_PATH", location_file)
    _write_location(location_file, str(user_root))
    config = user_root / "config"
    config.mkdir(parents=True)
    (config / "old.json").write_text('{"old": true}\n', encoding="utf-8")

    result = persistent_settings.reset_config(
        {"example.json": "{}\n", "shared/external_program_locations.txt": "# locations\n"}
    )

    assert (config / "example.json").read_text(encoding="utf-8") == "{}\n"
    assert (config / "shared" / "external_program_locations.txt").is_file()
    assert not (config / "old.json").exists()
    assert result.backup_directory is not None
    assert result.backup_directory.name.startswith("config_backup_")
    assert (result.backup_directory / "old.json").read_text(encoding="utf-8") == '{"old": true}\n'


def test_reset_config_without_existing_directory_needs_no_backup(monkeypatch, tmp_path):
    location_file = tmp_path / "app" / "persistent_settings_location.txt"
    user_root = tmp_path / "porta_user"
    monkeypatch.setattr(persistent_settings, "BOOTSTRAP_PATH", location_file)
    _write_location(location_file, str(user_root))

    result = persistent_settings.reset_config({"example.json": "{}\n"})

    assert result.backup_directory is None
    assert (user_root / "config" / "example.json").is_file()


def test_reset_config_rejects_unsafe_template_names_without_touching_existing_config(
    monkeypatch, tmp_path
):
    location_file = tmp_path / "app" / "persistent_settings_location.txt"
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
