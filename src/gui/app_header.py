"""Stable in-app navigation header shared by application screens."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QSizePolicy, QWidget


class AppHeader(QWidget):
    """Keep navigation and settings outside OS-owned window decorations.

    Screens place their own always-needed controls in ``content_layout``.
    The left and right actions remain consistent across applications.
    """

    def __init__(
        self,
        on_back: Callable[[], None],
        *,
        title: str = "",
        on_settings: Callable[[], None] | None = None,
        settings_tooltip: str = "",
    ) -> None:
        super().__init__()
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self.back_button = QPushButton("← メインメニュー")
        self.back_button.setToolTip("メインメニューへ戻ります。")
        self.back_button.clicked.connect(on_back)
        layout.addWidget(self.back_button)

        content = QWidget()
        self.content_layout = QHBoxLayout(content)
        self.content_layout.setContentsMargins(0, 0, 0, 0)
        self.content_layout.setSpacing(6)
        layout.addWidget(content, 1)

        self.title_label: QLabel | None = None
        if title:
            self.title_label = QLabel(title)
            self.title_label.setObjectName("app_page_title")
            self.title_label.setStyleSheet("font-size: 15px; font-weight: 600;")
            self.content_layout.addWidget(self.title_label)

        self.settings_button: QPushButton | None = None
        if on_settings is not None:
            self.settings_button = QPushButton("永続設定")
            if settings_tooltip:
                self.settings_button.setToolTip(settings_tooltip)
            self.settings_button.clicked.connect(on_settings)
            layout.addWidget(self.settings_button)
