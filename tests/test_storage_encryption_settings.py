import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from apps.system_tools.storage_encryption import settings
from apps.system_tools.storage_encryption import StorageEncryptionScreen
from PySide6.QtWidgets import QApplication


def test_storage_encryption_template_has_five_non_sensitive_slots():
    template = json.loads(settings.template_text())

    assert template["container_favorites"] == ["", "", "", "", ""]
    assert template["mount_point_favorites"] == ["", "", "", "", ""]
    assert template["mount_presets"] == [
        {"name": "", "container_path": "", "mount_point": ""}
        for _ in range(5)
    ]


def test_storage_encryption_settings_expand_relative_paths_from_the_settings_json(tmp_path):
    settings_text = json.dumps(
        {
            "container_favorites": ["../containers/crypt.img", ""],
            "mount_point_favorites": ["./mounts/crypt"],
            "mount_presets": [
                {
                    "name": "日常用",
                    "container_path": "../containers/crypt.img",
                    "mount_point": "./mounts/crypt",
                }
            ],
        }
    )

    result = settings.validate_text(settings_text, base_directory=tmp_path / "config")

    assert result.container_favorites == (str(tmp_path / "containers" / "crypt.img"),)
    assert result.mount_point_favorites == (str(tmp_path / "config" / "mounts" / "crypt"),)
    assert result.mount_presets == (
        settings.MountPreset(
            "日常用",
            str(tmp_path / "containers" / "crypt.img"),
            str(tmp_path / "config" / "mounts" / "crypt"),
        ),
    )


def test_storage_encryption_settings_reject_partial_presets_and_non_strings(tmp_path):
    partial = json.dumps(
        {
            "container_favorites": [],
            "mount_point_favorites": [],
            "mount_presets": [{"name": "途中", "container_path": "./container", "mount_point": ""}],
        }
    )
    invalid_favorite = json.dumps(
        {
            "container_favorites": [1],
            "mount_point_favorites": [],
            "mount_presets": [],
        }
    )

    for text in (partial, invalid_favorite):
        try:
            settings.validate_text(text, base_directory=tmp_path)
        except ValueError:
            pass
        else:
            raise AssertionError("malformed settings must not be adopted")


def test_storage_encryption_settings_save_keeps_relative_text_but_loads_absolute_paths(tmp_path, monkeypatch):
    path = tmp_path / "storage_encryption.json"
    monkeypatch.setattr(settings, "SETTINGS_PATH", path)
    raw = json.dumps(
        {
            "container_favorites": ["../container.img"],
            "mount_point_favorites": ["./mount"],
            "mount_presets": [],
        }
    )

    settings.save_text(raw)

    assert json.loads(path.read_text(encoding="utf-8"))["container_favorites"] == ["../container.img"]
    assert settings.load_settings().container_favorites == (str(tmp_path.parent / "container.img"),)


def test_storage_encryption_screen_keeps_favorites_in_their_own_inputs(tmp_path, monkeypatch):
    QApplication.instance() or QApplication([])
    container = tmp_path / "vault.img"
    mount_point = tmp_path / "mount"
    settings_path = tmp_path / "storage_encryption.json"
    settings_path.write_text(
        json.dumps(
            {
                "container_favorites": [str(container)],
                "mount_point_favorites": [str(mount_point)],
                "mount_presets": [
                    {
                        "name": "日常用",
                        "container_path": str(container),
                        "mount_point": str(mount_point),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(settings, "SETTINGS_PATH", settings_path)
    screen = StorageEncryptionScreen(lambda: None)
    try:
        assert screen.container_favorites_combo.itemText(1) == str(container)
        assert screen.mount_favorites_combo.itemText(1) == str(mount_point)
        assert screen.preset_combo.itemText(1) == "日常用"

        container_menu = screen.container_input._build_context_menu()
        mount_menu = screen.mount_point_input._build_context_menu()
        try:
            assert any(action.text() == "お気に入りのコンテナを入力" for action in container_menu.actions())
            assert any(action.text() == "お気に入りのマウント先を入力" for action in mount_menu.actions())
            assert not any(action.text() == "お気に入りのマウント先を入力" for action in container_menu.actions())
            assert not any(action.text() == "お気に入りのコンテナを入力" for action in mount_menu.actions())
        finally:
            container_menu.deleteLater()
            mount_menu.deleteLater()

        screen.container_favorites_combo.setCurrentIndex(1)
        assert screen.container_input.text() == str(container)
        screen.mount_favorites_combo.setCurrentIndex(1)
        assert screen.mount_point_input.text() == str(mount_point)
        screen.preset_combo.setCurrentIndex(1)
        assert screen.container_input.text() == str(container)
        assert screen.mount_point_input.text() == str(mount_point)
    finally:
        screen.close()
