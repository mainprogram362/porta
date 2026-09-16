import json
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from apps.file_tools.file_manager import FileManagerScreen
from apps.file_tools.file_manager import persistent_settings
from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QApplication


def entry(path: str, *, initial: bool = False, favorite: bool = False, context: bool = False) -> dict[str, object]:
    return {"path": path, "initial_work_list": initial, "favorite": favorite, "context_menu": context}


def test_file_manager_migrates_prior_work_list_and_registered_paths():
    settings = persistent_settings.validate_text(json.dumps({"default_work_list_paths": ["/tmp/work"], "registered_paths": ["/tmp/registered"], "default_destination_paths": ["/tmp/output"]}))
    assert settings["favorite_paths"] == [entry("/tmp/work", initial=True), entry("/tmp/registered", context=True)]


def test_file_manager_settings_template_uses_clear_home_token_and_attributes():
    template = json.loads(persistent_settings.template_text())
    assert template["favorite_paths"] == [
        entry("@HOME", initial=True, favorite=True, context=True),
        entry(""), entry(""), entry(""), entry(""),
    ]
    assert persistent_settings.validate_text(persistent_settings.template_text())["favorite_paths"] == [entry(str(Path.home()), initial=True, favorite=True, context=True)]


def test_file_manager_settings_reject_unknown_or_invalid_values():
    try:
        persistent_settings.validate_text('{"operation_history": []}')
    except ValueError as exc:
        assert "未対応" in str(exc)
    else:
        raise AssertionError("unknown persisted data must be rejected")
    try:
        persistent_settings.validate_text('{"favorite_paths": [{"path": "@HOME"}]}')
    except ValueError as exc:
        assert "favorite_paths" in str(exc)
    else:
        raise AssertionError("a path must carry all three attributes")


def test_file_manager_settings_ignore_retired_search_values_during_migration():
    settings = persistent_settings.validate_text('{"default_search_roots": ["/tmp/old"], "search_word_presets": ["old"]}')
    assert settings == {"favorite_paths": [entry(str(Path.home()), initial=True, favorite=True, context=True)]}


def test_file_manager_settings_save_only_when_explicitly_requested(tmp_path, monkeypatch):
    path = tmp_path / "file_manager.json"
    monkeypatch.setattr(persistent_settings, "SETTINGS_PATH", path)
    saved = persistent_settings.save_text(json.dumps({"favorite_paths": [entry("@HOME/Downloads", favorite=True), entry("")]}))
    assert saved["favorite_paths"] == [entry(str(Path.home() / "Downloads"), favorite=True)]
    assert json.loads(path.read_text(encoding="utf-8")) == {"favorite_paths": [entry("@HOME/Downloads", favorite=True), entry("")]}


def test_file_manager_restores_initial_paths_and_shows_favorites(tmp_path, monkeypatch):
    settings_path = tmp_path / "file_manager.json"
    work_item = tmp_path / "work-item.txt"
    favorite = tmp_path / "favorite"
    work_item.write_text("content", encoding="utf-8")
    favorite.mkdir()
    monkeypatch.setattr(persistent_settings, "SETTINGS_PATH", settings_path)
    persistent_settings.save_text(json.dumps({"favorite_paths": [entry(str(work_item), initial=True), entry(str(favorite), favorite=True)]}))
    app = QApplication.instance() or QApplication([])
    screen = FileManagerScreen(lambda: None)
    try:
        assert screen.search_results_input.paths() == [work_item.resolve()]
        assert screen.favorites_list.count() == 1
        assert screen.favorites_list.item(0).text() == favorite.name
        assert screen.favorites_list.item(0).toolTip() == str(favorite)
        screen.favorites_list.itemDoubleClicked.emit(screen.favorites_list.item(0))
        app.processEvents()
        assert screen.search_results_input.paths() == [work_item.resolve(), favorite.resolve()]
    finally:
        screen.close()


def test_file_manager_context_attribute_appears_in_path_menus(tmp_path, monkeypatch):
    settings_path = tmp_path / "file_manager.json"
    registered_file = tmp_path / "registered-file.txt"
    registered_file.write_text("content", encoding="utf-8")
    monkeypatch.setattr(persistent_settings, "SETTINGS_PATH", settings_path)
    persistent_settings.save_text(json.dumps({"favorite_paths": [entry(str(registered_file), context=True)]}))
    QApplication.instance() or QApplication([])
    screen = FileManagerScreen(lambda: None)
    try:
        list_menu = screen.search_results_input._tree._build_path_context_menu(QPoint(0, 0))
        chooser = next(action.menu() for action in list_menu.actions() if action.text() == "登録パスを追加")
        assert chooser is not None
        chooser.actions()[0].trigger()
        assert screen.search_results_input.paths() == [registered_file]
        screen.show_operation_confirmation()
        dialog = next(iter(screen._operation_confirmation_dialogs))
        line_menu = dialog.destination_input._build_context_menu()
        line_chooser = next(action.menu() for action in line_menu.actions() if action.text() == "登録パスを設定（置換）")
        line_chooser.actions()[0].trigger()
        assert dialog.destination_input.path() == tmp_path
    finally:
        for dialog in tuple(screen._operation_confirmation_dialogs):
            dialog.close()
        QApplication.instance().processEvents()
        screen.close()


def test_file_manager_hides_context_menus_when_attribute_is_off(tmp_path, monkeypatch):
    settings_path = tmp_path / "file_manager.json"
    monkeypatch.setattr(persistent_settings, "SETTINGS_PATH", settings_path)
    persistent_settings.save_text(json.dumps({"favorite_paths": [entry(str(tmp_path), favorite=True)]}))
    QApplication.instance() or QApplication([])
    screen = FileManagerScreen(lambda: None)
    try:
        list_menu = screen.search_results_input._tree._build_path_context_menu(QPoint(0, 0))
        assert all(action.text() != "登録パスを追加" for action in list_menu.actions())
    finally:
        screen.close()
