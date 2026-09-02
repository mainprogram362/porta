"""Configured launcher for PORTA's independently launchable tools."""

from __future__ import annotations

from collections.abc import Callable

from foundation import shared_launchers

from apps.system_tools.program_launcher import ConfiguredProgramLauncherScreen


class StandaloneAppsScreen(ConfiguredProgramLauncherScreen):
    """Expose the recommended standalone-apps location through the common flow."""

    def __init__(self, return_to_main: Callable[[], None]) -> None:
        super().__init__(
            return_to_main,
            title="独立ツール",
            explanation=(
                "PORTAから切り離して単体起動できる、低依存のツールです。"
                "緊急時にも使えるようPythonに依存せず、置き場がなくてもPORTA本体は壊れません。"
            ),
            settings_title="独立ツールの位置",
            settings_warning=(
                "コメントと空行を除き、置き場を1行だけ書きます。"
                "@PORTA/standalone_apps が移動に強い既定値です。変更はできますが通常は変更しないでください。"
            ),
            load_locations=shared_launchers.load_standalone_apps_location,
            editable_text=shared_launchers.standalone_apps_editable_text,
            template_text=shared_launchers.standalone_apps_template_text,
            save_text=shared_launchers.save_standalone_apps_text,
        )


def create_screen(return_to_main: Callable[[], None]) -> StandaloneAppsScreen:
    return StandaloneAppsScreen(return_to_main)
