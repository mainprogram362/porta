"""Configured launcher for explicitly registered external programs."""

from __future__ import annotations

from collections.abc import Callable

from apps.system_tools.program_launcher import ConfiguredProgramLauncherScreen
from apps.system_tools.external_app_launcher import locations


class ExternalAppLauncherScreen(ConfiguredProgramLauncherScreen):
    """Show only external locations explicitly written by the user."""

    def __init__(self, return_to_main: Callable[[], None]) -> None:
        super().__init__(
            return_to_main,
            title="外部プログラム",
            explanation=(
                "内容を予測しない外部プログラムです。永続設定へ明示した置き場だけを読み、"
                "直下の各フォルダにあるstart.shを一覧表示します。"
            ),
            settings_title="外部プログラムの位置",
            settings_warning=(
                "locations 配列へ外部プログラム置き場を書きます。"
                "空配列なら何も探さず、登録していない場所を自動探索することもありません。"
            ),
            load_locations=locations.load_external_locations,
            editable_text=locations.external_editable_text,
            template_text=locations.external_template_text,
            validate_text=locations.validate_external_text,
            save_text=locations.save_external_text,
        )


def create_screen(return_to_main: Callable[[], None]) -> ExternalAppLauncherScreen:
    return ExternalAppLauncherScreen(return_to_main)
