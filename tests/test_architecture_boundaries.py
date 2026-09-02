"""Keep the project dependency direction explicit as the app set grows."""

from __future__ import annotations

import ast
from pathlib import Path


SRC = Path(__file__).resolve().parents[1] / "src"


def _absolute_import_roots(directory: Path) -> set[str]:
    roots: set[str] = set()
    for path in directory.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                roots.add(node.module.split(".", 1)[0])
    return roots


def test_foundation_does_not_depend_on_product_or_gui_layers() -> None:
    assert _absolute_import_roots(SRC / "foundation").isdisjoint({"apps", "gui", "media"})


def test_media_domain_does_not_depend_on_apps_or_gui() -> None:
    assert _absolute_import_roots(SRC / "media").isdisjoint({"apps", "gui"})


def test_reusable_gui_does_not_depend_on_product_apps_or_media_domain() -> None:
    assert _absolute_import_roots(SRC / "gui").isdisjoint({"apps", "media"})
