"""The first screen displayed by the PORTA launcher."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QTimer

from PySide6.QtWidgets import (
    QApplication,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
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
    sole_app_for_category,
)
from foundation.external_open import ExternalOpenIntent
from foundation.product import PRODUCT_NAME
from records.record_bundle import RecordBundle
from runtime.instance_presence import InstancePresence
from gui import AppHeader, AppPageLayout, ResponsiveGridLayout
from gui.current_page_stack import ScrollablePageStack
from gui.layout_policy import bounded_to_available, preferred_window_size, usable_window_floor
from gui.process_tracking import PresenceHeartbeat
from settings.persistent_settings import BOOTSTRAP_PATH


class MainMenuWindow(QMainWindow):
    """Show categories and open registered completed-tool screens."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(PRODUCT_NAME)
        # Font-relative defaults survive DPI, theme and application-font changes.
        self.setMinimumSize(usable_window_floor(self))
        self.resize(preferred_window_size(self))

        self._screens = ScrollablePageStack()
        self._presence = InstancePresence()
        self._presence_heartbeat = PresenceHeartbeat(self._presence, self)
        self.setCentralWidget(self._screens)
        self._menu_screen = self._build_menu_screen()
        self._screens.addWidget(self._menu_screen)
        self._category_screens = {
            category.key: self._build_category_screen(category) for category in CATEGORIES
        }
        for screen in self._category_screens.values():
            self._screens.addWidget(screen)
        self.show_menu()

        from apps.porta_control.work_overview import WorkCenter
        self._work_center = WorkCenter(self)
        from apps.launcher.work_tabs import WorkTabs
        self.takeCentralWidget()
        self._work_tabs = WorkTabs(self)
        self.setCentralWidget(self._work_tabs)
        self.show_menu()
        # Toolbars receive their final width only once the window is shown.
        # Recalculate then so the advertised minimum never needs a scrollbar.
        self._menu_floor_timer = QTimer(self)
        self._menu_floor_timer.setSingleShot(True)
        self._menu_floor_timer.timeout.connect(self._refresh_menu_floor)
        self._menu_floor_timer.start(0)

    def _refresh_menu_floor(self) -> None:
        if hasattr(self, "_work_tabs") and self._work_tabs.bar.currentIndex() != 0:
            return
        if self._screens.currentWidget() is self._menu_screen:
            self.setMinimumSize(self._menu_window_floor())

    def show_work_overview(self):
        self._work_center.stopping = False
        self._work_center.show()
        self._work_center.raise_()
        self._work_center.activateWindow()

    # Compatibility for callers from an older running menu.
    show_independent_works = show_work_overview

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
        layout.addLayout(header)

        if not BOOTSTRAP_PATH.exists():
            setup = QGroupBox("初期設定が必要です")
            setup_layout = QHBoxLayout(setup)
            setup_layout.addWidget(QLabel("PORTAの設定保存先をまだ作成していません。"))
            setup_layout.addStretch(1)
            button = QPushButton("初期設定を開く")
            button.clicked.connect(lambda: self.show_control_section("settings"))
            setup_layout.addWidget(button)
            layout.addWidget(setup)

        favorites = QGroupBox("よく使う")
        favorites.setToolTip("ここは近道です。同じアプリは下の分類メニューにもあります。")
        favorites_layout = ResponsiveGridLayout(favorites, maximum_columns=2)
        favorites_layout.setContentsMargins(8, 10, 8, 8)
        for app in main_menu_apps():
            self._add_app_card(
                favorites_layout,
                app,
                return_to=self.show_menu,
                show_description=False,
            )
        layout.addWidget(favorites)

        catalog = QGroupBox("Pythonプログラム")
        catalog.setToolTip("PORTAのPython画面を用途別に表示します。")
        catalog_layout = ResponsiveGridLayout(catalog, maximum_columns=3)
        catalog_layout.setContentsMargins(8, 10, 8, 8)
        available_categories = [
            category
            for category in CATEGORIES
            if category.key != "non_python_programs"
            and (apps_for_category(category.key) or category.show_when_empty)
        ]
        for category in available_categories:
            self._add_category_card(catalog_layout, category, show_description=False)
        layout.addWidget(catalog)

        non_python = QGroupBox("それ以外のプログラム")
        non_python.setToolTip("Pythonを使わず、設定されたstart.shから起動するプログラムです。")
        non_python_layout = ResponsiveGridLayout(non_python, maximum_columns=2)
        non_python_layout.setContentsMargins(8, 10, 8, 8)
        for app in apps_for_category("non_python_programs"):
            self._add_app_card(
                non_python_layout,
                app,
                return_to=self.show_menu,
                show_description=False,
            )
        layout.addWidget(non_python)

        layout.addStretch(1)

        return screen

    def _build_category_screen(self, category: AppCategory) -> QWidget:
        screen = QWidget()
        layout = AppPageLayout(screen)

        header = AppHeader(self.show_menu, title=category.title)
        layout.addWidget(header)
        description = QLabel(category.description)
        description.setWordWrap(True)
        layout.addWidget(description)

        app_box = QGroupBox("アプリ一覧")
        app_layout = ResponsiveGridLayout(app_box, maximum_columns=2)
        app_layout.setContentsMargins(8, 10, 8, 8)
        apps = apps_for_category(category.key)
        if not apps:
            empty = QLabel("この領域には、まだ完成アプリがありません。")
            if category.key == "storage_encryption":
                empty.setText(
                    "暗号化コンテナ管理をここへ追加します。"
                    "マウント・状態確認・安全なアンマウントを、この領域に集約します。"
                )
            empty.setWordWrap(True)
            app_layout.addWidget(empty)
        else:
            for app in apps:
                self._add_app_card(app_layout, app)
        layout.addWidget(app_box)
        layout.addStretch(1)
        return screen

    def _add_app_card(
        self,
        layout: ResponsiveGridLayout,
        app: AppDefinition,
        *,
        return_to: Callable[[], None] | None = None,
        show_description: bool = True,
    ) -> None:
        """Place one direct launcher card in a responsive two-column grid."""
        card = QWidget()
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(0, 0, 0, 0)
        card_layout.setSpacing(4)
        button = QPushButton(app.title)
        button.setToolTip(app.description)
        button.clicked.connect(
            lambda _checked=False, definition=app: self.open_app(definition, return_to=return_to)
        )
        card_layout.addWidget(button)
        if show_description:
            description = QLabel(app.description)
            description.setWordWrap(True)
            description.setToolTip(app.description)
            card_layout.addWidget(description)
        layout.addWidget(card)

    def _add_category_card(
        self, layout: ResponsiveGridLayout, category: AppCategory, *, show_description: bool = True
    ) -> None:
        """Open a sole app directly, or show a category containing several apps."""
        card = QWidget()
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(0, 0, 0, 0)
        card_layout.setSpacing(4)
        button = QPushButton(category.title)
        direct_app = sole_app_for_category(category.key)
        if direct_app is None:
            button.setToolTip(category.description)
            button.clicked.connect(
                lambda _checked=False, category_key=category.key: self.show_category(
                    category_key
                )
            )
        else:
            button.setToolTip(
                f"{category.description}\n{direct_app.title}を直接開きます。"
            )
            button.clicked.connect(
                lambda _checked=False, definition=direct_app: self.open_app(
                    definition, return_to=self.show_menu
                )
            )
        card_layout.addWidget(button)
        count = len(apps_for_category(category.key))
        description_text = (
            category.description
            if direct_app is not None
            else f"{category.description}（{count}件）"
        )
        if show_description:
            description = QLabel(description_text)
            description.setWordWrap(True)
            description.setToolTip(description_text)
            card_layout.addWidget(description)
        layout.addWidget(card)

    def show_control_section(self, section):
        from apps.porta_control.configuration import create_screen as settings
        from apps.porta_control.diagnostics.environment_check import create_screen as environment
        screen = self._open_local_work(app_for_key("porta_control"))
        screen.open_section(section, {"settings": settings, "environment": environment}[section])

    def show_menu(self) -> None:
        """Return to the top-level menu from a completed tool."""
        self.setWindowTitle(PRODUCT_NAME)
        self._screens.setCurrentWidget(self._menu_screen)
        if hasattr(self, "_work_tabs"):
            self._work_tabs.show_menu()
        self.setMinimumSize(self._menu_window_floor())
        self._presence.update("メインメニュー", "待機中")

    def show_category(self, category_key: str) -> None:
        """Open one top-level category of completed tools."""
        category = next(category for category in CATEGORIES if category.key == category_key)
        self.setWindowTitle(f"{PRODUCT_NAME} — {category.title}")
        self._screens.setCurrentWidget(self._category_screens[category_key])
        self._work_tabs.show_menu()
        self._restore_base_minimum_size()
        self._presence.update(category.title, "アプリを選択中")

    def return_to_menu_if_navigation(self) -> None:
        """A plain relaunch shows the menu without destroying any work."""
        if QApplication.activeModalWidget() is not None:
            return
        self.show_menu()

    def open_app(
        self, definition: AppDefinition, *, return_to: Callable[[], None] | None = None,
        new_work: bool = False,
    ) -> None:
        """Open a new tab in this PORTA window."""
        self._open_local_work(definition)

    def _open_local_work(self, definition, *, paths=(), action="browse", text=None,
                         record_id=None, section=None):
        """Create and select an ordinary tab in this window process."""
        if definition is None:
            raise ValueError("開くアプリを確認できません。")
        from runtime.transient_paths import offer_media_paths, offer_video_encode_paths
        if definition.key == "media_information":
            offer_media_paths(paths)
        elif definition.key == "video_encoder":
            offer_video_encode_paths(paths)
        screen = definition.create_screen(self.show_menu)
        from apps.file_tools.file_manager import FileManagerScreen
        from apps.text_tools.text_workbench import TextWorkbenchScreen
        if isinstance(screen, FileManagerScreen):
            screen.set_open_media_tool_callback(self.open_handoff_app)
            if paths:
                screen.receive_external_paths(paths, action=action)
            if record_id:
                screen.acquire_record(record_id)
        if isinstance(screen, TextWorkbenchScreen):
            screen.set_record_bundle_callback(self.open_record_bundle_workspace)
            if text is not None:
                screen.receive_text(text)
        return self._work_tabs.add_work(definition, screen)

    def _adopt_screen_minimum_size(self, screen: QWidget) -> None:
        """Keep one font-relative interaction floor; page overflow belongs to its viewport."""
        self.setMinimumSize(self._application_window_floor())

    def _application_window_floor(self):
        return usable_window_floor(self)

    def _menu_window_floor(self):
        """Fit every launcher entry using the current text, font and grid flow."""
        preferred = preferred_window_size(self)
        width = max(preferred.width(), self._menu_screen.sizeHint().width())
        content_layout = self._menu_screen.layout()
        height = content_layout.heightForWidth(width) if content_layout.hasHeightForWidth() else content_layout.sizeHint().height()
        tab_height = self._work_tabs.bar.sizeHint().height() if hasattr(self, "_work_tabs") else 0
        status_height = self.statusBar().sizeHint().height() if self.statusBar().isVisible() else 0
        required = preferred.expandedTo(type(preferred)(width, height + tab_height + status_height))
        return bounded_to_available(self, required)

    def _restore_base_minimum_size(self) -> None:
        """Allow the menu and category pages to return to their compact size."""
        self._adopt_screen_minimum_size(self._screens.currentWidget())

    def open_handoff_app(self, key: str) -> None:
        """Open a receiver directly, without briefly returning to the menu."""
        definition = app_for_key(key)
        if definition is None:
            self.show_menu()
            return
        self._open_local_work(definition)

    def open_record_bundle_workspace(self, bundle: RecordBundle | None = None) -> None:
        """Hand ownership to the detached table process without retaining a document."""
        from records import record_service
        try:
            if bundle is not None:
                record_service.create(bundle)
            else:
                self.show_work_overview()
        except ValueError as exc:
            QMessageBox.warning(self, "対応表を開けません", str(exc))

    def receive_record_bundle_output(self, text: str) -> None:
        """Open a fresh text workbench containing an explicitly generated table output."""
        self._open_local_work(app_for_key("text_workbench"), text=text)

    def receive_record_bundle_in_file_manager(self, bundle: RecordBundle) -> None:
        """Open File Manager with one explicit transient table snapshot."""
        from records import record_service
        try:
            snapshot = record_service.create(bundle)
            self._open_local_work(app_for_key("file_manager"), record_id=snapshot["id"])
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "対応表を渡せません", str(exc))

    def open_external_paths(self, target: str, paths: tuple[Path, ...]) -> None:
        """Show the common chooser, or retain a compatible direct route."""
        if target == "choose":
            from apps.launcher.external_open import ExternalOpenChooserScreen
            screen = ExternalOpenChooserScreen(paths, self.dispatch_external_intent, self.show_menu)
            definition = type("ExternalDefinition", (), {
                "key": "external_choose", "title": "受信したパスの操作",
            })()
            self._work_tabs.add_work(definition, screen)
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
        keys = {"file-manager": "file_manager", "media-organizer": "media_information", "video-encoder": "video_encoder"}
        self._open_local_work(app_for_key(keys[intent.target]), paths=intent.paths, action=intent.action)

    def closeEvent(self, event) -> None:  # type: ignore[override]
        """Let transient tool screens release owned processes before app exit.

        QStackedWidget hides inactive pages when the main window closes; it
        does not guarantee that every page receives its own close event first.
        Screens which own disposable resources expose ``shutdown`` explicitly.
        """
        self._work_center.shutdown()
        if self._work_center.worker is not None and self._work_center.worker.isRunning():
            event.ignore()
            QTimer.singleShot(150, self.close)
            return
        local_running = self._work_tabs.running_reason()
        if local_running:
            self.statusBar().showMessage("作業中のタブがあります。完了または停止してから閉じてください: " + local_running)
            event.ignore()
            return
        self._work_tabs.shutdown()
        for index in range(self._screens.count()):
            screen = self._screens.widget(index)
            shutdown = getattr(screen, "shutdown", None)
            if callable(shutdown):
                shutdown()
        self._presence_heartbeat.close()
        super().closeEvent(event)
