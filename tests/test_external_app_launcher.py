import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QPushButton, QSizePolicy

from apps.system_tools.external_app_launcher import discovery
from apps.system_tools.external_app_launcher.discovery import ApplicationLocation
from apps.system_tools.external_app_launcher.window import ExternalAppLauncherScreen
from apps.system_tools.external_app_launcher import window as external_window
from apps.system_tools.external_app_launcher.locations import LauncherLocations


def _location(path, number: int = 1) -> ApplicationLocation:  # type: ignore[no-untyped-def]
    return ApplicationLocation(number, path.name, path)


def test_external_program_scan_uses_only_immediate_child_folders(tmp_path):
    root = tmp_path / "apps"
    valid = root / "firefox"
    valid.mkdir(parents=True)
    (valid / "start.sh").write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    nested = root / "nested" / "other"
    nested.mkdir(parents=True)
    (nested / "start.sh").write_text("#!/usr/bin/env bash\n", encoding="utf-8")

    scan = discovery.scan_location(_location(root))

    assert [app.name for app in scan.applications] == ["firefox"]
    assert scan.state == "ready"


def test_external_launch_restores_original_desktop_xdg_configuration(tmp_path, monkeypatch):
    root = tmp_path / "apps"
    application_dir = root / "firefox"
    application_dir.mkdir(parents=True)
    (application_dir / "start.sh").write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    application = discovery.scan_location(_location(root)).applications[0]
    captured: dict[str, object] = {}

    def fake_popen(*args, **kwargs):  # type: ignore[no-untyped-def]
        captured["environment"] = kwargs["env"]
        return type("Process", (), {"pid": 12345})()

    monkeypatch.setenv("XDG_CONFIG_HOME", "/home/example/.config")
    monkeypatch.setenv("PORTA_ORIGINAL_XDG_CONFIG_HOME", "/portable/vscode/config")
    monkeypatch.setattr(discovery.shutil, "which", lambda _name: "/bin/bash")
    monkeypatch.setattr(discovery.subprocess, "Popen", fake_popen)
    ignored = []
    monkeypatch.setattr(discovery, "get_registry", lambda: type("Registry", (), {"ignore": ignored.append})())

    discovery.launch(application)

    environment = captured["environment"]
    assert isinstance(environment, dict)
    assert environment["XDG_CONFIG_HOME"] == "/portable/vscode/config"
    assert "PORTA_ORIGINAL_XDG_CONFIG_HOME" not in environment
    assert ignored == [12345]


def test_external_program_scan_explains_missing_and_unmatched_content(tmp_path):
    missing = _location(tmp_path / "missing")
    assert discovery.scan_location(missing).state == "missing_directory"

    empty_root = tmp_path / "empty"
    empty_root.mkdir()
    assert discovery.scan_location(_location(empty_root)).state == "empty_directory"

    child_root = tmp_path / "without-launcher"
    (child_root / "application").mkdir(parents=True)
    scan = discovery.scan_location(_location(child_root))
    assert scan.state == "launcher_missing"
    assert "start.sh" in scan.detail


def test_external_program_screen_uses_only_explicit_locations(tmp_path, monkeypatch):
    QApplication.instance() or QApplication([])
    root = tmp_path / "explicit-apps"
    app = root / "a-longer-than-usual-program-name"
    app.mkdir(parents=True)
    (app / "start.sh").write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    monkeypatch.setattr(
        external_window.locations,
        "load_external_locations",
        lambda: LauncherLocations("ready", "外部プログラムの置き場を1件読み込みました。", None, (root,)),
    )

    screen = ExternalAppLauncherScreen(lambda: None)
    try:
        button = next(button for button in screen.findChildren(QPushButton) if button.text() == app.name)
        assert button.minimumHeight() == 0
        assert button.sizeHint().height() >= button.fontMetrics().lineSpacing()
        assert button.sizePolicy().horizontalPolicy() == QSizePolicy.Policy.Ignored
        assert "起動:" in button.toolTip()
        assert "1件のプログラム" in screen.status.text()
    finally:
        screen.close()


def test_external_program_screen_is_nonfatal_with_no_registered_locations(monkeypatch):
    QApplication.instance() or QApplication([])
    monkeypatch.setattr(
        external_window.locations,
        "load_external_locations",
        lambda: LauncherLocations("ready", "外部プログラムの置き場を0件読み込みました。", None),
    )

    screen = ExternalAppLauncherScreen(lambda: None)
    try:
        assert "0件" in screen.status.text()
        assert not any(
            button.text() in {"vscode", "firefox_individual"}
            for button in screen.findChildren(QPushButton)
        )
    finally:
        screen.close()
