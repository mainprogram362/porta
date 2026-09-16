"""Launcher catalog and shared screen contracts for detached work."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QPushButton, QWidget
from apps.launcher.catalog import (APPS, CATEGORIES, app_for_key, apps_for_category,
                                   main_menu_apps, sole_app_for_category)
from apps.launcher.main_menu import MainMenuWindow
from apps.launcher.work_center import WorkCenter
from apps.launcher.external_open import ExternalOpenChooserScreen
from apps.system_tools.configuration.window import ConfigurationScreen
from gui import AppHeader, AppPageLayout


@pytest.fixture(autouse=True)
def no_background_discovery(monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(WorkCenter, "refresh", lambda self: None)
    yield app


def test_launcher_catalog_registers_the_file_manager():
    assert [category.key for category in CATEGORIES] == [
        "storage_encryption",
        "file_tools",
        "text_tools",
        "media_tools",
        "non_python_programs",
        "automation_tools",
        "local_ai",
    ]
    assert [app.key for app in APPS] == [
        "browser_automation",
        "storage_encryption",
        "porta_control",
        "local_ai",
        "external_app_launcher",
        "file_manager",
        "text_workbench",
        "youtube_downloader",
        "video_encoder",
        "media_information",
        "media_ledger",
        "board_response_archive",
        "text_thread_viewer",
    ]
    assert [app.key for app in apps_for_category("file_tools")] == ["file_manager"]
    assert [app.key for app in apps_for_category("porta_control")] == ["porta_control"]
    assert [app.key for app in apps_for_category("text_tools")] == ["text_workbench"]
    assert [app.key for app in apps_for_category("non_python_programs")] == ["external_app_launcher"]
    assert [app.key for app in apps_for_category("storage_encryption")] == ["storage_encryption"]
    assert [app.key for app in apps_for_category("media_tools")] == [
        "youtube_downloader",
        "video_encoder",
        "media_information",
        "media_ledger",
        "board_response_archive",
        "text_thread_viewer",
    ]
    assert [app.key for app in main_menu_apps()] == [
        "file_manager",
        "youtube_downloader",
        "video_encoder",
        "media_information",
    ]
    assert app_for_key("video_encoder") is not None
    assert app_for_key("missing") is None
    text_app = sole_app_for_category("text_tools")
    assert text_app is not None and text_app.key == "text_workbench"
    assert sole_app_for_category("media_tools") is None



def test_retired_media_json_viewer_is_not_a_separate_launcher_app():
    QApplication.instance() or QApplication([])
    window = MainMenuWindow()
    try:
        window.show_category("media_tools")
        assert all(
            button.text() != "メディアJSONビューアー"
            for button in window.findChildren(QPushButton)
        )
        assert app_for_key("media_json_viewer") is None
    finally:
        window.close()



def test_category_screen_uses_the_shared_header_and_returns_from_the_top():
    QApplication.instance() or QApplication([])
    window = MainMenuWindow()
    try:
        window.show_category("media_tools")
        screen = window._screens.currentWidget()
        assert isinstance(screen.layout(), AppPageLayout)

        headers = screen.findChildren(AppHeader)
        assert len(headers) == 1
        assert headers[0].title_label is not None
        assert headers[0].title_label.text() == "メディア操作"

        headers[0].back_button.click()
        assert window._screens.currentWidget() is window._menu_screen
    finally:
        window.close()



def test_configuration_offers_the_environment_setup_tutorial():
    QApplication.instance() or QApplication([])
    screen = ConfigurationScreen(lambda: None)
    try:
        assert any(
            button.text() == "環境整備チュートリアル"
            for button in screen.findChildren(QPushButton)
        )
        assert any(
            button.text() == "入口の雛形を作成"
            for button in screen.findChildren(QPushButton)
        )
    finally:
        screen.close()



@pytest.mark.parametrize("definition", APPS, ids=lambda item: item.key)
def test_registered_screens_report_semantic_state(definition, monkeypatch):
    from apps.system_tools.environment_check.window import EnvironmentCheckScreen
    monkeypatch.setattr(EnvironmentCheckScreen, "refresh", lambda self: None)
    screen = definition.create_screen(lambda: None)
    try:
        assert isinstance(screen, QWidget)
        assert isinstance(screen.layout(), AppPageLayout)
        state = screen.describe_work_state()
        assert state["level"] in (1, 2, 3, 4)
        assert isinstance(state["reason"], str) and state["reason"]
    finally:
        shutdown = getattr(screen, "shutdown", None)
        if callable(shutdown):
            shutdown()
        screen.close()


def test_common_chooser_disables_extract_for_mixed_selection(tmp_path):
    archive, ordinary = tmp_path / "archive.zip", tmp_path / "ordinary.txt"
    archive.touch()
    ordinary.touch()
    chooser = ExternalOpenChooserScreen((archive, ordinary), lambda _: None, lambda: None)
    try:
        extract = next(button for button in chooser.findChildren(QPushButton)
                       if button.text().startswith("解凍画面へ"))
        assert not extract.isEnabled()
        assert "全件が対応圧縮ファイル" in extract.toolTip()
        assert chooser.describe_work_state()["level"] == 3
    finally:
        chooser.close()


def test_file_manager_mode_only_change_is_configuration_state():
    from apps.file_tools.file_manager import FileManagerScreen
    screen = FileManagerScreen(lambda: None)
    try:
        screen.operation_combo.setCurrentIndex(screen.operation_combo.findData("rename"))
        assert screen.describe_work_state()["level"] == 2
    finally:
        screen.close()


def test_manager_window_closing_calls_local_shutdown():
    window = MainMenuWindow()
    stopped = []
    screen = QWidget()
    screen.shutdown = lambda: stopped.append(True)
    window._screens.addWidget(screen)
    window.close()
    assert stopped == [True]
