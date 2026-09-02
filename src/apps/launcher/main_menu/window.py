"""The first screen displayed by the PORTA launcher."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QApplication,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from apps.launcher.catalog import (
    AppCategory,
    AppDefinition,
    CATEGORIES,
    app_for_key,
    apps_for_category,
    main_menu_apps,
)
from apps.file_tools.file_manager import FileManagerScreen
from apps.launcher.external_open import ExternalOpenChooserScreen
from foundation.external_open import ExternalOpenIntent
from foundation.product import PRODUCT_NAME
from foundation.transient_paths import offer_media_paths, offer_video_encode_paths
from gui import AppHeader, AppPageLayout

_BASE_MINIMUM_WIDTH = 820
_BASE_MINIMUM_HEIGHT = 520


class MainMenuWindow(QMainWindow):
    """Show categories and open registered completed-tool screens."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(PRODUCT_NAME)
        # The menu itself is deliberately compact.  Dense child screens still
        # promote this window to their own required minimum size when opened.
        self.resize(960, 540)
        self.setMinimumSize(_BASE_MINIMUM_WIDTH, _BASE_MINIMUM_HEIGHT)

        self._screens = QStackedWidget()
        self._idle_seconds_remaining = 60
        self._idle_timer = QTimer(self)
        self._idle_timer.setInterval(1_000)
        self._idle_timer.timeout.connect(self._advance_idle_countdown)
        self.setCentralWidget(self._screens)
        self._menu_screen = self._build_menu_screen()
        self._screens.addWidget(self._menu_screen)
        self._category_screens = {
            category.key: self._build_category_screen(category) for category in CATEGORIES
        }
        for screen in self._category_screens.values():
            self._screens.addWidget(screen)
        self.show_menu()

    def _build_menu_screen(self) -> QWidget:
        screen = QWidget()
        layout = QVBoxLayout(screen)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)

        header = QHBoxLayout()
        title = QLabel(PRODUCT_NAME)
        title.setStyleSheet("font-size: 20px; font-weight: 600;")
        header.addWidget(title)
        header.addStretch(1)
        self._idle_status = QLabel()
        self._idle_status.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._idle_status.setStyleSheet("color: palette(text); font-size: 12px;")
        self._idle_status.setToolTip("操作を選ばない場合、60秒後にアプリを終了します。")
        header.addWidget(self._idle_status)
        layout.addLayout(header)

        favorites = QGroupBox("よく使う")
        favorites.setToolTip("ここは近道です。同じアプリは下の分類メニューにもあります。")
        favorites_layout = QGridLayout(favorites)
        favorites_layout.setContentsMargins(8, 10, 8, 8)
        favorites_layout.setHorizontalSpacing(8)
        favorites_layout.setVerticalSpacing(6)
        for index, app in enumerate(main_menu_apps()):
            self._add_app_card(
                favorites_layout,
                app,
                index // 2,
                index % 2,
                return_to=self.show_menu,
            )
        favorites_layout.setColumnStretch(0, 1)
        favorites_layout.setColumnStretch(1, 1)
        layout.addWidget(favorites)

        catalog = QGroupBox("Pythonプログラム")
        catalog.setToolTip("PORTAのPython画面を用途別に表示します。")
        catalog_layout = QGridLayout(catalog)
        catalog_layout.setContentsMargins(8, 10, 8, 8)
        catalog_layout.setHorizontalSpacing(8)
        catalog_layout.setVerticalSpacing(6)
        available_categories = [
            category
            for category in CATEGORIES
            if category.key != "non_python_programs"
            and (apps_for_category(category.key) or category.show_when_empty)
        ]
        for index, category in enumerate(available_categories):
            self._add_category_card(catalog_layout, category, index // 3, index % 3)
        for column in range(3):
            catalog_layout.setColumnStretch(column, 1)
        layout.addWidget(catalog)

        non_python = QGroupBox("それ以外のプログラム")
        non_python.setToolTip("Pythonを使わず、設定されたstart.shから起動するプログラムです。")
        non_python_layout = QGridLayout(non_python)
        non_python_layout.setContentsMargins(8, 10, 8, 8)
        non_python_layout.setHorizontalSpacing(8)
        non_python_layout.setVerticalSpacing(6)
        for index, app in enumerate(apps_for_category("non_python_programs")):
            self._add_app_card(
                non_python_layout,
                app,
                0,
                index,
                return_to=self.show_menu,
            )
        non_python_layout.setColumnStretch(0, 1)
        non_python_layout.setColumnStretch(1, 1)
        layout.addWidget(non_python)

        layout.addStretch(1)

        return screen

    def _build_category_screen(self, category: AppCategory) -> QWidget:
        screen = QWidget()
        layout = AppPageLayout(screen)

        layout.addWidget(AppHeader(self.show_menu, title=category.title))
        description = QLabel(category.description)
        description.setWordWrap(True)
        layout.addWidget(description)

        app_box = QGroupBox("アプリ一覧")
        app_layout = QGridLayout(app_box)
        app_layout.setContentsMargins(8, 10, 8, 8)
        app_layout.setHorizontalSpacing(8)
        app_layout.setVerticalSpacing(6)
        apps = apps_for_category(category.key)
        if not apps:
            empty = QLabel("この領域には、まだ完成アプリがありません。")
            if category.key == "storage_encryption":
                empty.setText(
                    "暗号化コンテナ管理をここへ追加します。"
                    "マウント・状態確認・安全なアンマウントを、この領域に集約します。"
                )
            empty.setWordWrap(True)
            app_layout.addWidget(empty, 0, 0)
        else:
            for index, app in enumerate(apps):
                self._add_app_card(app_layout, app, index // 2, index % 2)
        app_layout.setColumnStretch(0, 1)
        app_layout.setColumnStretch(1, 1)
        layout.addWidget(app_box)
        layout.addStretch(1)
        return screen

    def _add_app_card(
        self,
        layout: QGridLayout,
        app: AppDefinition,
        row: int,
        column: int,
        *,
        return_to: Callable[[], None] | None = None,
    ) -> None:
        """Place one direct launcher card in a responsive two-column grid."""
        card = QWidget()
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(0, 0, 0, 0)
        card_layout.setSpacing(4)
        button = QPushButton(app.title)
        button.setMinimumHeight(32)
        button.setToolTip(app.description)
        button.clicked.connect(
            lambda _checked=False, definition=app: self.open_app(definition, return_to=return_to)
        )
        card_layout.addWidget(button)
        description = QLabel(app.description)
        description.setWordWrap(True)
        description.setStyleSheet("color: palette(text); font-size: 12px;")
        description.setMaximumHeight(32)
        description.setToolTip(app.description)
        card_layout.addWidget(description)
        layout.addWidget(card, row, column)

    def _add_category_card(
        self, layout: QGridLayout, category: AppCategory, row: int, column: int
    ) -> None:
        """Place one category gateway; empty categories are omitted by the caller."""
        card = QWidget()
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(0, 0, 0, 0)
        card_layout.setSpacing(4)
        button = QPushButton(category.title)
        button.setMinimumHeight(30)
        button.setToolTip(category.description)
        button.clicked.connect(
            lambda _checked=False, category_key=category.key: self.show_category(category_key)
        )
        card_layout.addWidget(button)
        count = len(apps_for_category(category.key))
        description = QLabel(f"{category.description}（{count}件）")
        description.setWordWrap(True)
        description.setStyleSheet("color: palette(text); font-size: 12px;")
        description.setMaximumHeight(30)
        description.setToolTip(f"{category.description}（{count}件）")
        card_layout.addWidget(description)
        layout.addWidget(card, row, column)

    def show_menu(self) -> None:
        """Return to the top-level menu from a completed tool."""
        self._restore_base_minimum_size()
        self.setWindowTitle(PRODUCT_NAME)
        self._screens.setCurrentWidget(self._menu_screen)
        self._idle_seconds_remaining = 60
        self._idle_status.setText("無操作で 60 秒後に終了")
        self._idle_timer.start()

    def show_category(self, category_key: str) -> None:
        """Open one top-level category of completed tools."""
        self._restore_base_minimum_size()
        category = next(category for category in CATEGORIES if category.key == category_key)
        self.setWindowTitle(f"{PRODUCT_NAME} — {category.title}")
        self._idle_timer.stop()
        self._screens.setCurrentWidget(self._category_screens[category_key])

    def open_app(
        self, definition: AppDefinition, *, return_to: Callable[[], None] | None = None
    ) -> None:
        """Open one registered completed tool in the shared QApplication."""
        if return_to is None:
            return_to = lambda: self.show_category(definition.category_key)
        screen = definition.create_screen(
            return_to
        )
        if isinstance(screen, FileManagerScreen):
            screen.set_open_media_tool_callback(self.open_handoff_app)
        self.setWindowTitle(f"{PRODUCT_NAME} — {definition.title}")
        self._idle_timer.stop()
        self._screens.addWidget(screen)
        self._screens.setCurrentWidget(screen)
        self._adopt_screen_minimum_size(screen)

    def _adopt_screen_minimum_size(self, screen: QWidget) -> None:
        """Never let a dense child page be clipped by the launcher window.

        QStackedWidget does not automatically promote a later-added page's
        minimum size to its QMainWindow.  Use the current page's own lower
        limit while it is open, then restore the compact menu limit on return.
        """
        self.setMinimumSize(
            max(_BASE_MINIMUM_WIDTH, screen.minimumWidth()),
            max(_BASE_MINIMUM_HEIGHT, screen.minimumHeight()),
        )

    def _restore_base_minimum_size(self) -> None:
        """Allow the menu and category pages to return to their compact size."""
        self.setMinimumSize(_BASE_MINIMUM_WIDTH, _BASE_MINIMUM_HEIGHT)

    def open_handoff_app(self, key: str) -> None:
        """Open a receiver directly, without briefly returning to the idle menu."""
        definition = app_for_key(key)
        if definition is None:
            self.show_menu()
            return
        self.open_app(definition, return_to=self.show_menu)

    def open_external_paths(self, target: str, paths: tuple[Path, ...]) -> None:
        """Show the common chooser, or retain a compatible direct route."""
        if target == "choose":
            chooser = ExternalOpenChooserScreen(paths, self.dispatch_external_intent, self.show_menu)
            self.setWindowTitle(f"{PRODUCT_NAME} — 受信したパスの操作")
            self._idle_timer.stop()
            self._screens.addWidget(chooser)
            self._screens.setCurrentWidget(chooser)
            self._adopt_screen_minimum_size(chooser)
            return

        default_action = {
            "file-manager": "browse",
            "media-organizer": "inspect",
            "video-encoder": "encode",
        }.get(target)
        if default_action is None:
            raise ValueError(f"未対応の外部連携先です: {target}")
        self.dispatch_external_intent(
            ExternalOpenIntent(target=target, action=default_action, paths=paths)  # type: ignore[arg-type]
        )

    def dispatch_external_intent(self, intent: ExternalOpenIntent) -> None:
        """Transfer validated paths and the chosen operation to its app."""
        allowed_actions = {
            "file-manager": {"browse", "copy", "compress", "extract"},
            "media-organizer": {"inspect"},
            "video-encoder": {"encode"},
        }
        if intent.action not in allowed_actions[intent.target]:
            raise ValueError(
                f"未対応の外部連携操作です: {intent.target} / {intent.action}"
            )
        app_key = {
            "file-manager": "file_manager",
            "media-organizer": "media_information",
            "video-encoder": "video_encoder",
        }[intent.target]

        if intent.target == "media-organizer":
            offer_media_paths(intent.paths)
        elif intent.target == "video-encoder":
            offer_video_encode_paths(intent.paths)

        definition = app_for_key(app_key)
        if definition is None:
            raise RuntimeError(f"外部連携先の画面が登録されていません: {app_key}")
        self.open_app(definition, return_to=self.show_menu)
        screen = self._screens.currentWidget()
        if intent.target == "file-manager" and isinstance(screen, FileManagerScreen):
            screen.receive_external_paths(intent.paths, action=intent.action)

    def _advance_idle_countdown(self) -> None:
        self._idle_seconds_remaining -= 1
        if self._idle_seconds_remaining <= 0:
            self._idle_timer.stop()
            self._idle_status.setText("60秒間操作がなかったため終了します。")
            QApplication.quit()
            return
        self._idle_status.setText(
            f"無操作で {self._idle_seconds_remaining} 秒後に終了"
        )

    def closeEvent(self, event) -> None:  # type: ignore[override]
        """Let transient tool screens release owned processes before app exit.

        QStackedWidget hides inactive pages when the main window closes; it
        does not guarantee that every page receives its own close event first.
        Screens which own disposable resources expose ``shutdown`` explicitly.
        """
        for index in range(self._screens.count()):
            screen = self._screens.widget(index)
            shutdown = getattr(screen, "shutdown", None)
            if callable(shutdown):
                shutdown()
        super().closeEvent(event)
