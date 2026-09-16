import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLabel, QWidget

from gui import AppHeader, AppPageLayout


def test_app_header_keeps_common_actions_around_screen_specific_content():
    QApplication.instance() or QApplication([])
    called: list[str] = []
    header = AppHeader(
        lambda: called.append("back"),
        title="画面名",
        on_settings=lambda: called.append("settings"),
        settings_tooltip="設定の説明",
    )
    try:
        header.content_layout.addWidget(QLabel("画面固有"))

        assert header.back_button.text() == "← メインメニュー"
        assert header.settings_button is not None
        assert header.settings_button.text() == "⚙"
        assert header.settings_button.accessibleName() == "永続設定"
        assert header.settings_button.toolTip() == "設定の説明"
        assert header.settings_button.size().width() == header.settings_button.size().height() == 34
        assert header.title_label is not None
        assert header.title_label.text() == "画面名"
        assert header.title_label.objectName() == "app_page_title"

        header.back_button.click()
        header.settings_button.click()
        assert called == ["back", "settings"]
    finally:
        header.close()


def test_app_page_layout_uses_the_shared_compact_outer_spacing():
    QApplication.instance() or QApplication([])
    page = QWidget()
    layout = AppPageLayout(page)
    try:
        margins = layout.contentsMargins()
        assert (
            margins.left(),
            margins.top(),
            margins.right(),
            margins.bottom(),
        ) == (10, 8, 10, 10)
        assert layout.spacing() == 6
    finally:
        page.close()


def test_app_header_can_omit_settings_for_screens_without_persistent_settings():
    QApplication.instance() or QApplication([])
    header = AppHeader(lambda: None)
    try:
        assert header.settings_button is None
    finally:
        header.close()


def test_hosted_header_moves_settings_and_keeps_screen_specific_controls():
    from gui.wrapping_toolbar import WrappingToolBar
    app = QApplication.instance() or QApplication([])
    calls = []
    host = QWidget()
    toolbar = WrappingToolBar("作業", host)
    header = AppHeader(lambda: calls.append("back"), title="機能名", on_settings=lambda: calls.append("settings"))
    extra = QLabel("機能固有の設定")
    header.content_layout.addWidget(extra)
    header.use_work_toolbar(toolbar)
    assert header.back_button.isHidden()
    assert header.title_label.isHidden()
    assert header.content_layout.indexOf(extra) >= 0
    header.settings_button.click()
    assert calls == ["settings"]
    assert header.settings_button.parentWidget() is toolbar._content
    header.close()
    host.close()
