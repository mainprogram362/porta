import json
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QApplication

from apps.media_tools.text_thread_viewer import settings
from apps.media_tools.text_thread_viewer.window import TextThreadViewerScreen


def test_text_thread_viewer_settings_save_favorites_and_initial_input(tmp_path, monkeypatch):
    settings_path = tmp_path / "text_thread_viewer.json"
    favorite = tmp_path / "threads"
    favorite.mkdir()
    monkeypatch.setattr(settings, "SETTINGS_PATH", settings_path)

    saved = settings.save_text(
        json.dumps(
            {
                "favorite_paths": [
                    {"path": str(favorite), "initial_input": True},
                    {"path": "", "initial_input": False},
                ]
            }
        )
    )

    assert saved == {
        "favorite_paths": [{"path": str(favorite), "initial_input": True}]
    }
    assert json.loads(settings_path.read_text(encoding="utf-8"))["favorite_paths"][1] == {
        "path": "",
        "initial_input": False,
    }

    QApplication.instance() or QApplication([])
    screen = TextThreadViewerScreen(lambda: None)
    try:
        assert screen.source_input.paths() == [favorite]
    finally:
        screen.close()


def test_viewer_favorite_menu_and_expansion_match_path_list_behavior(tmp_path, monkeypatch):
    settings_path = tmp_path / "text_thread_viewer.json"
    favorite = tmp_path / "threads"
    favorite.mkdir()
    text_file = favorite / "thread.txt"
    text_file.write_text("[1] A / 2026 / ID:a\n本文\n", encoding="utf-8")
    (favorite / "ignore.md").write_text("ignored", encoding="utf-8")
    child_folder = favorite / "next"
    child_folder.mkdir()
    monkeypatch.setattr(settings, "SETTINGS_PATH", settings_path)
    settings.save_text(
        json.dumps(
            {"favorite_paths": [{"path": str(favorite), "initial_input": False}]}
        )
    )

    QApplication.instance() or QApplication([])
    screen = TextThreadViewerScreen(lambda: None)
    try:
        empty_menu = screen.source_input._tree._build_path_context_menu(QPoint(0, 0))
        favorite_menu = next(
            action.menu()
            for action in empty_menu.actions()
            if action.text() == "お気に入りパスを追加"
        )
        favorite_menu.actions()[0].trigger()
        assert screen.source_input.paths() == [favorite]

        item = screen.source_input._tree.topLevelItem(0)
        position = screen.source_input._tree.visualItemRect(item).center()
        row_menu = screen.source_input._tree._build_path_context_menu(position)
        expansion = next(
            action.menu() for action in row_menu.actions() if action.text() == "展開"
        )
        assert expansion is not None
        expansion.actions()[0].trigger()
        assert screen.source_input.paths() == [child_folder, text_file]
    finally:
        screen.close()


def test_text_thread_viewer_template_is_registered_for_user_space_generation():
    from apps.system_tools.configuration.templates import config_reset_templates

    assert "text_thread_viewer.json" in config_reset_templates()
