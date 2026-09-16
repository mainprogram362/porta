"""Reusable context-menu additions for path-list inputs."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtWidgets import QMenu


def add_favorite_paths_menu(
    menu: QMenu,
    paths: list[str],
    *,
    choose_path: Callable[[str], None],
    title: str = "登録パスを追加",
) -> None:
    """Append one explicit path chooser when configured entries exist."""
    if not paths:
        return
    if menu.actions():
        menu.addSeparator()
    favorites = QMenu(title, menu)
    menu.addMenu(favorites)
    favorites.setToolTipsVisible(True)
    for path_text in paths:
        action = favorites.addAction(path_text)
        action.setToolTip(path_text)
        action.triggered.connect(
            lambda _checked=False, value=path_text: choose_path(value)
        )


def add_path_expansion_menu(
    menu: QMenu,
    *,
    request_expand: Callable[[str, bool], None],
    open_one_directory: Callable[[], None] | None = None,
    choose_one_directory: Callable[[], None] | None = None,
) -> None:
    """Append the standard single/checked/selected direct-child actions."""
    expansion = QMenu("展開", menu)
    menu.addMenu(expansion)
    if open_one_directory is not None:
        expansion.addAction("この1件を開く（一覧全体を置換）").triggered.connect(
            open_one_directory
        )
    if choose_one_directory is not None:
        expansion.addAction("この1件の直下を選んで展開…").triggered.connect(
            choose_one_directory
        )
    if open_one_directory is not None or choose_one_directory is not None:
        expansion.addSeparator()
    expansion.addAction("チェック済みフォルダを展開").triggered.connect(
        lambda: request_expand("checked", False)
    )
    expansion.addAction("チェック済みに条件を指定して展開…").triggered.connect(
        lambda: request_expand("checked", True)
    )
    expansion.addAction("選択中フォルダを展開").triggered.connect(
        lambda: request_expand("selected", False)
    )
    expansion.addAction("選択中に条件を指定して展開…").triggered.connect(
        lambda: request_expand("selected", True)
    )
