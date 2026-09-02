import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from apps.file_tools.file_manager import FileManagerScreen
from apps.launcher.catalog import APPS, CATEGORIES, app_for_key, apps_for_category, main_menu_apps
from apps.launcher.main_menu import MainMenuWindow
from apps.launcher.external_open import ExternalOpenChooserScreen
from apps.media_tools.board_response_archive import BoardResponseArchiveScreen
from apps.media_tools.media_information import MediaInformationScreen
from apps.media_tools.media_ledger import MediaLedgerScreen
from apps.media_tools.text_thread_viewer import TextThreadViewerScreen
from apps.media_tools.video_encoder import VideoEncoderScreen
from apps.media_tools.youtube_downloader import YouTubeDownloaderScreen
from apps.system_tools.configuration.window import ConfigurationScreen
from apps.system_tools.external_app_launcher import ExternalAppLauncherScreen
from apps.system_tools.standalone_apps import StandaloneAppsScreen
from apps.system_tools.standalone_apps import window as standalone_apps_window
from apps.system_tools.storage_encryption import StorageEncryptionScreen
from apps.system_tools.system_remote import SystemRemoteScreen
from gui import AppHeader, AppPageLayout
from PySide6.QtWidgets import QApplication, QGroupBox, QPushButton, QWidget


def test_launcher_catalog_registers_the_file_manager():
    assert [category.key for category in CATEGORIES] == [
        "storage_encryption",
        "file_tools",
        "text_tools",
        "media_tools",
        "non_python_programs",
        "automation_tools",
        "system_operations",
        "local_ai",
        "settings",
    ]
    assert [app.key for app in APPS] == [
        "storage_encryption",
        "configuration",
        "local_ai_settings",
        "local_ai_chat",
        "system_remote",
        "standalone_apps",
        "external_app_launcher",
        "file_manager",
        "youtube_downloader",
        "video_encoder",
        "media_information",
        "media_ledger",
        "board_response_archive",
        "text_thread_viewer",
    ]
    assert [app.key for app in apps_for_category("file_tools")] == ["file_manager"]
    assert [app.key for app in apps_for_category("non_python_programs")] == [
        "standalone_apps",
        "external_app_launcher",
    ]
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


def test_main_menu_window_can_be_created():
    QApplication.instance() or QApplication([])
    window = MainMenuWindow()
    try:
        assert window.windowTitle() == "PORTA"
        assert window.centralWidget() is not None
        assert window._idle_timer.isActive()
        file_manager_button = next(
            button
            for button in window.findChildren(QPushButton)
            if button.text() == "ファイルマネージャー"
        )
        assert any(
            box.title() == "Pythonプログラム" for box in window.findChildren(QGroupBox)
        )
        assert any(
            box.title() == "それ以外のプログラム" for box in window.findChildren(QGroupBox)
        )
        assert any(
            button.text() == "ストレージ・暗号化" for button in window.findChildren(QPushButton)
        )
        file_manager_button.click()
        assert isinstance(window.centralWidget().currentWidget(), FileManagerScreen)
        assert not window._idle_timer.isActive()
    finally:
        window.close()


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
        screen = window.centralWidget().currentWidget()
        assert isinstance(screen.layout(), AppPageLayout)

        headers = screen.findChildren(AppHeader)
        assert len(headers) == 1
        assert headers[0].title_label is not None
        assert headers[0].title_label.text() == "メディア操作"

        headers[0].back_button.click()
        assert window.centralWidget().currentWidget() is window._menu_screen
    finally:
        window.close()


def test_main_window_closing_shuts_down_resource_owning_stacked_screens():
    QApplication.instance() or QApplication([])

    class ShutdownScreen(QWidget):
        def __init__(self) -> None:
            super().__init__()
            self.shutdown_count = 0

        def shutdown(self) -> None:
            self.shutdown_count += 1

    window = MainMenuWindow()
    screen = ShutdownScreen()
    window._screens.addWidget(screen)
    try:
        window.close()
        assert screen.shutdown_count == 1
    finally:
        window.close()


def test_file_manager_handoff_opens_video_encoder_without_returning_to_menu(tmp_path):
    QApplication.instance() or QApplication([])
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"video")
    window = MainMenuWindow()
    try:
        file_manager_button = next(
            button for button in window.findChildren(QPushButton) if button.text() == "ファイルマネージャー"
        )
        file_manager_button.click()
        manager = window.centralWidget().currentWidget()
        assert isinstance(manager, FileManagerScreen)
        manager.search_results_input.append_items([str(source)])
        manager.send_targets_to_video_encoder()
        assert isinstance(window.centralWidget().currentWidget(), VideoEncoderScreen)
        assert not window._idle_timer.isActive()
    finally:
        window.close()


def test_external_open_replaces_file_manager_defaults(tmp_path):
    QApplication.instance() or QApplication([])
    first = tmp_path / "first.txt"
    second = tmp_path / "second folder"
    first.touch()
    second.mkdir()
    window = MainMenuWindow()
    try:
        window.open_external_paths("file-manager", (first, second))
        manager = window.centralWidget().currentWidget()
        assert isinstance(manager, FileManagerScreen)
        assert manager.search_results_input.items() == [str(first), str(second)]
        assert not window._idle_timer.isActive()
    finally:
        window.close()


def test_external_open_first_shows_common_operation_chooser(tmp_path):
    QApplication.instance() or QApplication([])
    source = tmp_path / "source.txt"
    source.touch()
    window = MainMenuWindow()
    try:
        window.open_external_paths("choose", (source,))
        chooser = window.centralWidget().currentWidget()
        assert isinstance(chooser, ExternalOpenChooserScreen)
        assert chooser.paths == (source,)
        assert any(
            button.text().startswith("コピー画面へ")
            for button in chooser.findChildren(QPushButton)
        )
        assert not window._idle_timer.isActive()
    finally:
        window.close()


def test_common_chooser_transfers_paths_and_action_to_media_screen(tmp_path):
    QApplication.instance() or QApplication([])
    source = tmp_path / "clip.mp4"
    source.touch()
    window = MainMenuWindow()
    try:
        window.open_external_paths("choose", (source,))
        chooser = window.centralWidget().currentWidget()
        media_button = next(
            button
            for button in chooser.findChildren(QPushButton)
            if button.text().startswith("メディア整理へ")
        )
        media_button.click()
        media = window.centralWidget().currentWidget()
        assert isinstance(media, MediaInformationScreen)
        assert media.path_input.items() == [str(source)]
    finally:
        window.close()


def test_common_chooser_enters_the_selected_file_operation(tmp_path):
    app = QApplication.instance() or QApplication([])
    source = tmp_path / "source.txt"
    source.write_text("content", encoding="utf-8")
    window = MainMenuWindow()
    try:
        window.open_external_paths("choose", (source,))
        chooser = window.centralWidget().currentWidget()
        copy_button = next(
            button
            for button in chooser.findChildren(QPushButton)
            if button.text().startswith("コピー画面へ")
        )
        copy_button.click()
        app.processEvents()

        manager = window.centralWidget().currentWidget()
        assert isinstance(manager, FileManagerScreen)
        assert manager.search_results_input.items() == [str(source)]
        assert len(manager._quick_copy_dialogs) == 1
        assert next(iter(manager._quick_copy_dialogs))._sources == (source,)
    finally:
        window.close()


def test_common_chooser_disables_extract_for_a_mixed_selection(tmp_path):
    QApplication.instance() or QApplication([])
    archive = tmp_path / "archive.zip"
    ordinary = tmp_path / "ordinary.txt"
    archive.touch()
    ordinary.touch()
    window = MainMenuWindow()
    try:
        window.open_external_paths("choose", (archive, ordinary))
        chooser = window.centralWidget().currentWidget()
        extract_button = next(
            button
            for button in chooser.findChildren(QPushButton)
            if button.text().startswith("解凍画面へ")
        )
        assert not extract_button.isEnabled()
        assert "全件が対応圧縮ファイル" in extract_button.toolTip()
    finally:
        window.close()


def test_external_open_replaces_media_and_encoder_input_lists(tmp_path):
    QApplication.instance() or QApplication([])
    source = tmp_path / "clip.mp4"
    source.touch()

    media_window = MainMenuWindow()
    try:
        media_window.open_external_paths("media-organizer", (source,))
        media = media_window.centralWidget().currentWidget()
        assert isinstance(media, MediaInformationScreen)
        assert media.path_input.items() == [str(source)]
    finally:
        media_window.close()

    encoder_window = MainMenuWindow()
    try:
        encoder_window.open_external_paths("video-encoder", (source,))
        encoder = encoder_window.centralWidget().currentWidget()
        assert isinstance(encoder, VideoEncoderScreen)
        assert encoder.path_input.items() == [str(source)]
    finally:
        encoder_window.close()


def test_launcher_opens_youtube_downloader_from_media_category():
    QApplication.instance() or QApplication([])
    window = MainMenuWindow()
    try:
        media_button = next(
            button for button in window.findChildren(QPushButton) if button.text() == "メディア操作"
        )
        media_button.click()
        youtube_button = next(
            button
            for button in window.findChildren(QPushButton)
            if button.text() == "YouTube ダウンローダー"
        )
        youtube_button.click()
        assert isinstance(window.centralWidget().currentWidget(), YouTubeDownloaderScreen)
    finally:
        window.close()


def test_launcher_opens_external_app_launcher_from_the_top_menu():
    QApplication.instance() or QApplication([])
    window = MainMenuWindow()
    try:
        launcher_button = next(
            button
            for button in window.findChildren(QPushButton)
            if button.text() == "外部プログラム"
        )
        launcher_button.click()
        assert isinstance(window.centralWidget().currentWidget(), ExternalAppLauncherScreen)
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
            button.text() == "直下へ看板の雛形を作成"
            for button in screen.findChildren(QPushButton)
        )
    finally:
        screen.close()


def test_launcher_opens_standalone_apps_from_its_category(monkeypatch):
    QApplication.instance() or QApplication([])
    monkeypatch.setattr(
        standalone_apps_window.shared_launchers,
        "load_standalone_apps_location",
        lambda: standalone_apps_window.shared_launchers.LauncherLocations(
            "ready",
            "独立ツールの置き場を1件読み込みました。",
            None,
            (standalone_apps_window.shared_launchers.DEFAULT_STANDALONE_APPS_DIRECTORY,),
        ),
    )
    window = MainMenuWindow()
    try:
        tools_button = next(
            button
            for button in window.findChildren(QPushButton)
            if button.text() == "独立ツール"
        )
        tools_button.click()
        screen = window.centralWidget().currentWidget()
        assert isinstance(screen, StandaloneAppsScreen)
        assert "3件のプログラム" in screen.status.text()
    finally:
        window.close()


def test_standalone_apps_screen_is_nonfatal_when_tools_are_missing(tmp_path, monkeypatch):
    QApplication.instance() or QApplication([])
    monkeypatch.setattr(
        standalone_apps_window.shared_launchers,
        "load_standalone_apps_location",
        lambda: standalone_apps_window.shared_launchers.LauncherLocations(
            "missing_file",
            "独立ツールの位置設定がありません。",
            None,
        ),
    )

    screen = StandaloneAppsScreen(lambda: None)
    try:
        assert "PORTA本体には影響しません" in screen.status.text()
    finally:
        screen.close()


def test_launcher_opens_unified_media_information_workspace_from_media_category():
    QApplication.instance() or QApplication([])
    window = MainMenuWindow()
    try:
        media_button = next(
            button for button in window.findChildren(QPushButton) if button.text() == "メディア操作"
        )
        media_button.click()
        information_button = next(
            button
            for button in window.findChildren(QPushButton)
            if button.text() == "メディア情報ワークスペース"
        )
        information_button.click()
        assert isinstance(window.centralWidget().currentWidget(), MediaInformationScreen)
    finally:
        window.close()


def test_launcher_opens_media_ledger_from_media_category():
    QApplication.instance() or QApplication([])
    window = MainMenuWindow()
    try:
        media_button = next(
            button for button in window.findChildren(QPushButton) if button.text() == "メディア操作"
        )
        media_button.click()
        ledger_button = next(
            button
            for button in window.findChildren(QPushButton)
                if button.text() == "メディア本台帳（初期版）"
        )
        ledger_button.click()
        assert isinstance(window.centralWidget().currentWidget(), MediaLedgerScreen)
    finally:
        window.close()


def test_launcher_adopts_a_dense_screen_minimum_size_and_restores_the_menu_limit():
    QApplication.instance() or QApplication([])
    window = MainMenuWindow()
    try:
        definition = app_for_key("media_information")
        assert definition is not None
        window.open_app(definition)
        screen = window.centralWidget().currentWidget()
        assert isinstance(screen, MediaInformationScreen)
        assert window.minimumWidth() == screen.minimumWidth() == 900

        window.show_menu()
        assert window.minimumWidth() == 820
    finally:
        window.close()


def test_launcher_opens_board_response_archive_from_media_category():
    QApplication.instance() or QApplication([])
    window = MainMenuWindow()
    try:
        media_button = next(
            button for button in window.findChildren(QPushButton) if button.text() == "メディア操作"
        )
        media_button.click()
        archive_button = next(
            button
            for button in window.findChildren(QPushButton)
            if button.text() == "掲示板レス保存（初期版）"
        )
        archive_button.click()
        assert isinstance(window.centralWidget().currentWidget(), BoardResponseArchiveScreen)
    finally:
        window.close()


def test_launcher_opens_text_thread_viewer_from_media_category():
    QApplication.instance() or QApplication([])
    window = MainMenuWindow()
    try:
        media_button = next(
            button for button in window.findChildren(QPushButton) if button.text() == "メディア操作"
        )
        media_button.click()
        viewer_button = next(
            button
            for button in window.findChildren(QPushButton)
            if button.text() == "テキストスレッドビューア（初期版）"
        )
        viewer_button.click()
        assert isinstance(window.centralWidget().currentWidget(), TextThreadViewerScreen)
    finally:
        window.close()


def test_launcher_opens_video_encoder_prototype_from_media_category():
    QApplication.instance() or QApplication([])
    window = MainMenuWindow()
    try:
        media_button = next(
            button for button in window.findChildren(QPushButton) if button.text() == "メディア操作"
        )
        media_button.click()
        encoder_button = next(
            button
            for button in window.findChildren(QPushButton)
            if button.text() == "動画エンコード・圧縮（プロトタイプ）"
        )
        encoder_button.click()
        assert isinstance(window.centralWidget().currentWidget(), VideoEncoderScreen)
    finally:
        window.close()


def test_launcher_opens_system_remote_from_system_operations_category():
    QApplication.instance() or QApplication([])
    window = MainMenuWindow()
    try:
        category_button = next(
            button for button in window.findChildren(QPushButton) if button.text() == "システム操作"
        )
        category_button.click()
        remote_button = next(
            button
            for button in window.findChildren(QPushButton)
            if button.text() == "システム操作リモコン"
        )
        remote_button.click()
        assert isinstance(window.centralWidget().currentWidget(), SystemRemoteScreen)
        assert any(
            button.text() == "PORTA を問答無用で全終了"
            for button in window.findChildren(QPushButton)
        )
    finally:
        window.close()


def test_launcher_opens_storage_encryption_from_its_own_category():
    QApplication.instance() or QApplication([])
    window = MainMenuWindow()
    try:
        category_button = next(
            button for button in window.findChildren(QPushButton) if button.text() == "ストレージ・暗号化"
        )
        category_button.click()
        mount_button = next(
            button
            for button in window.findChildren(QPushButton)
            if button.text() == "暗号化領域を開く・マウント"
        )
        mount_button.click()
        assert isinstance(window.centralWidget().currentWidget(), StorageEncryptionScreen)
    finally:
        window.close()
