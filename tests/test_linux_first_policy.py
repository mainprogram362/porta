from pathlib import Path


def test_linux_desktop_policy_stays_at_the_automation_boundary():
    desktop_source = Path("src/automation/desktop.py").read_text(encoding="utf-8")
    path_source = Path("src/foundation/path.py").read_text(encoding="utf-8")

    assert not Path("src/os_platform").exists()
    assert 'sys.platform != "linux"' in desktop_source
    assert "sys.platform" not in path_source
