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
from apps.text_tools.text_workbench import create_screen as create_text_workbench_screen
from apps.porta_control import create_screen as create_control_screen
from apps.system_tools.external_app_launcher import create_screen as create_external_app_launcher_screen
from apps.system_tools.storage_encryption import create_screen as create_storage_encryption_screen
from apps.system_tools.local_ai import create_workspace as create_local_ai_screen
from apps.automation_tools.browser import create_screen as create_browser_screen


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
        "明示登録した外部プログラムを起動します。",
    ),
    AppCategory("automation_tools", "自動操作", "ブラウザとデスクトップ操作"),
    AppCategory("local_ai", "ローカルAI", "今回だけの会話と、全アプリ共通のAIファイル設定"),
)

APPS: tuple[AppDefinition, ...] = (
    AppDefinition("browser_automation", "automation_tools", "自動操作",
                  "接続済みFirefoxのウィンドウとタブを選び、明示した手順を実行します。", create_browser_screen),
    AppDefinition(
        "storage_encryption",
        "storage_encryption",
        "暗号化・保護",
        "LUKS、VeraCrypt、7z、ZIP、RAR解凍を方式ごとに確認して扱います。",
        create_storage_encryption_screen,
    ),
    AppDefinition(
        "porta_control",
        "porta_control",
        "PORTA管理",
        "設定保存先の確認、入口編集、退避付きのCONFIG初期化を行います。",
        create_control_screen,
    ),
    AppDefinition(
        "local_ai",
        "local_ai",
        "ローカルAI",
        "全アプリ共通で使う llama-server とGGUFモデルの場所を設定します。",
        create_local_ai_screen,
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
        "text_workbench",
        "text_tools",
        "テキスト加工ワークベンチ",
        "HTMLや文章から必要な部分を抽出し、カット・絞り込み・置換・整形を重ねます。",
        create_text_workbench_screen,
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


def sole_app_for_category(category_key: str) -> AppDefinition | None:
    """Return the direct destination when a category currently has one app."""
    apps = apps_for_category(category_key)
    return apps[0] if len(apps) == 1 else None


def main_menu_apps() -> tuple[AppDefinition, ...]:
    """Return completed apps intentionally placed on the first menu."""
    return tuple(app for app in APPS if app.show_on_main_menu)


def app_for_key(key: str) -> AppDefinition | None:
    """Return one registered application for direct one-shot handoff routing."""
    key = {
        'configuration': 'porta_control',
        'environment_check': 'porta_control',
        'browser_cartridge_editor': 'browser_automation',
        'local_ai_settings': 'local_ai',
        'local_ai_chat': 'local_ai',
    }.get(key, key)
    return next((app for app in APPS if app.key == key), None)
