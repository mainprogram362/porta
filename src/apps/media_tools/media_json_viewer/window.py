"""Compatibility entry redirected to the unified media-information workspace."""

from collections.abc import Callable

from apps.media_tools.media_information.window import (
    MediaInformationScreen,
    _INDEPENDENT_FILE_MANAGERS,
)


MediaJsonViewerScreen = MediaInformationScreen


def create_screen(return_to_main: Callable[[], None]) -> MediaInformationScreen:
    return MediaInformationScreen(return_to_main)


__all__ = ["MediaJsonViewerScreen", "create_screen", "_INDEPENDENT_FILE_MANAGERS"]
