from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "nautilus_handoff.py"
SPEC = importlib.util.spec_from_file_location("porta_nautilus_handoff", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_nautilus_handoff_is_directly_executable():
    assert SCRIPT.stat().st_mode & 0o111


def test_nautilus_handoff_preserves_each_selected_uri():
    assert MODULE.selected_uris(
        {
            "NAUTILUS_SCRIPT_SELECTED_URIS": (
                "file:///tmp/one%20file\nfile:///tmp/two%0Aline\n"
            )
        }
    ) == ("file:///tmp/one%20file", "file:///tmp/two%0Aline")


def test_nautilus_handoff_recognizes_installed_link_names():
    assert MODULE.target_from_invocation("PORTAへ送る", None) == "choose"
    assert MODULE.target_from_invocation("PORTA-ファイルマネージャーで開く", None) == "file-manager"
    assert MODULE.target_from_invocation("ignored", "video-encoder") == "video-encoder"
    assert MODULE.target_from_invocation("nautilus_handoff.py", None) == "choose"


def test_nautilus_handoff_notifies_if_porta_exits_before_opening():
    process = type(
        "ExitedProcess",
        (),
        {
            "returncode": 1,
            "poll": lambda self: 1,
            "communicate": lambda self: ("", "test launch error"),
        },
    )()
    with (
        patch.object(MODULE.subprocess, "Popen", return_value=process),
        patch.object(MODULE, "notify_launch_failure") as notify,
        patch.object(MODULE, "notify_handoff_received"),
        patch.object(MODULE, "selected_uris", return_value=("file:///tmp/item",)),
    ):
        assert MODULE.main(["choose"]) == 1
    notify.assert_called_once_with("test launch error")
