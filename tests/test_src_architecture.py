"""Migration keeps one state owner and shared code independent of app widgets."""
import ast
import importlib
from pathlib import Path

import pytest


GROUPS = {
    "runtime": "process process_registry process_control managed_process instance_presence runtime_activity operation_progress single_instance work_process transient_paths".split(),
    "settings": "persistent_settings json_settings user_space path_tokens".split(),
    "records": "record_bundle record_bundle_fields record_bundle_editing record_service".split(),
}
ALIASES = [(f"foundation.{name}", f"{group}.{name}")
           for group, names in GROUPS.items() for name in names]
ALIASES += [(f"apps.automation_tools.browser.{name}", f"automation.browser.{name}")
            for name in ("cartridge", "transport")]


@pytest.mark.parametrize("old,new", ALIASES)
def test_compatibility_imports_share_the_actual_module(old, new, monkeypatch):
    current = importlib.import_module(new)
    legacy = importlib.import_module(old)
    assert legacy is current
    sentinel = object()
    monkeypatch.setattr(legacy, "_migration_probe", sentinel, raising=False)
    assert current._migration_probe is sentinel


def test_shared_packages_do_not_import_app_screens_or_gui():
    root = Path(__file__).resolve().parents[1] / "src"
    for package in (*GROUPS, "automation/browser"):
        for path in (root / package).rglob("*.py"):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                imports = []
                if isinstance(node, ast.Import):
                    imports = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and not node.level:
                    imports = [node.module or ""]
                assert not any(name.split(".")[0] in {"apps", "gui"} for name in imports), path


def test_moved_modules_keep_installation_paths():
    from records.record_service import PORTA_ROOT as record_root
    from runtime.process_registry import PORTA_ROOT as process_root
    from runtime.work_process import ROOT as work_root
    from settings.persistent_settings import PROJECT_ROOT
    root = Path(__file__).resolve().parents[1]
    assert record_root == process_root == work_root == PROJECT_ROOT == root
