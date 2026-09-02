"""Code-owned catalog of completed apps shown by the launcher.

Keep app factories here instead of JSON: each entry must name Python code that
creates a screen. User-adjustable preferences and presets belong in config.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from PySide6.QtWidgets import QWidget

from apps.file_tools.file_manager import create_screen as create_file_manager_screen
from apps.media_tools.media_information import create_screen as create_media_information_screen
from apps.media_tools.media_ledger import create_screen as create_media_ledger_screen
from apps.media_tools.board_response_archive import create_screen as create_board_response_archive_screen
from apps.media_tools.text_thread_viewer import create_screen as create_text_thread_viewer_screen
from apps.media_tools.youtube_downloader import create_screen as create_youtube_downloader_screen
from apps.media_tools.video_encoder import create_screen as create_video_encoder_screen
from apps.system_tools.system_remote import create_screen as create_system_remote_screen
from apps.system_tools.configuration import create_screen as create_configuration_screen
from apps.system_tools.external_app_launcher import create_screen as create_external_app_launcher_screen
from apps.system_tools.storage_encryption import create_screen as create_storage_encryption_screen
from apps.system_tools.local_ai import LocalAiChatScreen, create_screen as create_local_ai_settings_screen
from apps.system_tools.standalone_apps import create_screen as create_standalone_apps_screen


@dataclass(frozen=True)
class AppCategory:
    """A top-level grouping shown in the main menu."""

    key: str
    title: str
    description: str
    show_when_empty: bool = False


@dataclass(frozen=True)
class AppDefinition:
    """A completed tool that the launcher can open.

    ``create_screen`` receives a callback that returns the user to the main
    menu, then returns the tool's root widget.
    """

    key: str
    category_key: str
    title: str
    description: str
    create_screen: Callable[[Callable[[], None]], QWidget]
    show_on_main_menu: bool = False


CATEGORIES: tuple[AppCategory, ...] = (
    AppCategory(
        "storage_encryption",
        "ストレージ・暗号化",
        "暗号化コンテナとストレージを、慎重な確認付きで扱う領域",
        show_when_empty=True,
    ),
    AppCategory("file_tools", "ファイル操作", "整理・コピー・リネームなど"),
    AppCategory("text_tools", "テキスト操作", "テキストと名前の加工"),
    AppCategory("media_tools", "メディア操作", "画像・動画・ダウンロード"),
    AppCategory(
        "non_python_programs",
        "それ以外のプログラム",
        "単体で起動できる独立ツールと、明示登録した外部プログラム",
    ),
    AppCategory("automation_tools", "自動操作", "ブラウザとデスクトップ操作"),
    AppCategory("system_operations", "システム操作", "Linuxの状態確認と、明示した電源・セッション操作"),
    AppCategory("local_ai", "ローカルAI", "今回だけの会話と、全アプリ共通のAIファイル設定"),
    AppCategory("settings", "設定", "保存先と、明示的に保存する既定設定を確認・編集"),
)

APPS: tuple[AppDefinition, ...] = (
    AppDefinition(
        "storage_encryption",
        "storage_encryption",
        "暗号化領域を開く・マウント",
        "LUKSコンテナまたはLUKSデバイスを、確認してから開き、空のフォルダへマウントします。",
        create_storage_encryption_screen,
    ),
    AppDefinition(
        "configuration",
        "settings",
        "設定",
        "設定保存先の確認、入口編集、退避付きのCONFIG初期化を行います。",
        create_configuration_screen,
    ),
    AppDefinition(
        "local_ai_settings",
        "local_ai",
        "ローカルAI設定",
        "全アプリ共通で使う llama-server とGGUFモデルの場所を設定します。",
        create_local_ai_settings_screen,
    ),
    AppDefinition(
        "local_ai_chat",
        "local_ai",
        "ローカルAI チャット",
        "AIを必要な間だけ読み込み、会話履歴を保存せずに使います。",
        LocalAiChatScreen,
    ),
    AppDefinition(
        "system_remote",
        "system_operations",
        "システム操作リモコン",
        "Linuxの標準的な電源・セッション操作と、PORTA Coreへの入口です。",
        create_system_remote_screen,
    ),
    AppDefinition(
        "standalone_apps",
        "non_python_programs",
        "独立ツール",
        "PORTAから切り離して単体起動できる低依存ツールを、設定位置から読み込みます。",
        create_standalone_apps_screen,
    ),
    AppDefinition(
        "external_app_launcher",
        "non_python_programs",
        "外部プログラム",
        "明示的に設定した置き場だけを読み、直下のプログラムを一覧表示します。",
        create_external_app_launcher_screen,
    ),
    AppDefinition(
        "file_manager",
        "file_tools",
        "ファイルマネージャー",
        "対象パスをまとめて安全にコピーします。",
        create_file_manager_screen,
        show_on_main_menu=True,
    ),
    AppDefinition(
        "youtube_downloader",
        "media_tools",
        "YouTube ダウンローダー",
        "動画の確認・ダウンロードと投稿者一覧の出力を行います。",
        create_youtube_downloader_screen,
        show_on_main_menu=True,
    ),
    AppDefinition(
        "video_encoder",
        "media_tools",
        "動画エンコード・圧縮（プロトタイプ）",
        "FFmpegで先頭カットと品質指定を行い、新しいMP4を作成します。",
        create_video_encoder_screen,
        show_on_main_menu=True,
    ),
    AppDefinition(
        "media_information",
        "media_tools",
        "メディア情報ワークスペース",
        "JSON・ローカルファイルを同じ整理画面で読み取り専用閲覧または編集します。",
        create_media_information_screen,
        show_on_main_menu=True,
    ),
    AppDefinition(
        "media_ledger",
        "media_tools",
        "メディア本台帳（初期版）",
        "パーツJSONを明示追加し、作品IDとファイルIDを管理します。パーツ編集はメディア情報整理で行います。",
        create_media_ledger_screen,
    ),
    AppDefinition(
        "board_response_archive",
        "media_tools",
        "掲示板レス保存（初期版）",
        "公開したらばスレッドを確認し、レスだけを抽出表示します。",
        create_board_response_archive_screen,
    ),
    AppDefinition(
        "text_thread_viewer",
        "media_tools",
        "テキストスレッドビューア（初期版）",
        "保存した掲示板レス用テキストを、返信と横断検索で閲覧します。",
        create_text_thread_viewer_screen,
    ),
)


def apps_for_category(category_key: str) -> tuple[AppDefinition, ...]:
    """Return registered completed apps in their display order."""
    return tuple(app for app in APPS if app.category_key == category_key)


def main_menu_apps() -> tuple[AppDefinition, ...]:
    """Return completed apps intentionally placed on the first menu."""
    return tuple(app for app in APPS if app.show_on_main_menu)


def app_for_key(key: str) -> AppDefinition | None:
    """Return one registered application for direct one-shot handoff routing."""
    return next((app for app in APPS if app.key == key), None)
