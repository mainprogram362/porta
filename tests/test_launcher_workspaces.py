"""Tabs within one process, explicit detachment, and the unified overview."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QPushButton, QWidget

from apps.launcher.catalog import AppDefinition, app_for_key
from apps.launcher.main_menu import MainMenuWindow
from apps.porta_control.work_overview import WorkCenter, unified_inventory


class Screen(QWidget):
    def __init__(self, level=1):
        super().__init__()
        self.level = level

    def describe_work_state(self):
        return {"level": self.level, "reason": "テスト作業"}


@pytest.fixture
def window(monkeypatch):
    QApplication.instance() or QApplication([])
    monkeypatch.setattr(WorkCenter, "refresh", lambda self: None)
    # Opening the diagnostics section is what this test exercises.  The audit
    # itself has separate tests and must not outlive a short-lived GUI fixture.
    monkeypatch.setattr(
        "apps.porta_control.diagnostics.environment_check.window.EnvironmentCheckScreen.refresh",
        lambda self: None,
    )
    result = MainMenuWindow()
    yield result
    result.close()


def definition(factory=lambda _: Screen()):
    return AppDefinition("test", "file_tools", "テスト作業", "", factory)


def test_menu_opens_and_selects_a_tab_in_the_current_window(window, monkeypatch):
    created = []
    item = definition(lambda _: created.append(Screen()) or created[-1])
    monkeypatch.setattr("apps.launcher.work_tabs.spawn", lambda _: pytest.fail("通常タブで別プロセスを起動しない"))
    window.open_app(item)
    assert len(window._work_tabs.entries) == 1
    assert window._work_tabs.stack.currentWidget() is created[0]
    assert created[0].window() is window


def test_each_menu_open_creates_another_tab_not_another_window(window):
    item = definition()
    window.open_app(item)
    window.open_app(item)
    assert len(window._work_tabs.entries) == 2
    assert window._work_tabs.bar.count() == 3


def test_paths_and_record_identity_are_loaded_into_the_new_tab(window, tmp_path):
    path = tmp_path / "対象.txt"
    screen = window._open_local_work(app_for_key("file_manager"), paths=(path,), action="copy")
    assert str(path) in screen.search_results_input.toPlainText().splitlines()
    assert screen.window() is window


def test_settings_and_diagnostics_open_as_selected_control_tabs(window):
    window.show_control_section("settings")
    first = window._work_tabs.stack.currentWidget()
    assert first.stack.currentWidget() is first.pages["settings"]
    window.show_control_section("environment")
    second = window._work_tabs.stack.currentWidget()
    assert second is not first
    assert second.stack.currentWidget() is second.pages["environment"]


def test_tab_is_visible_to_peer_overview_protocol(window):
    window.open_app(definition())
    tab_id, entry = next(iter(window._work_tabs.entries.items()))
    result = window._work_tabs.command(tab_id, {"version": 1, "op": "status"})
    assert result["host"] == "tab"
    assert result["pid"] == os.getpid()
    assert result["title"] == "テスト作業"
    assert entry["server"].server.isListening()


def test_detach_transfers_safe_state_then_removes_source_after_ack(window, monkeypatch):
    from apps.text_tools.text_workbench import TextWorkbenchScreen
    screen = TextWorkbenchScreen(lambda: None)
    screen.text_editor.setPlainText("移動する文章")
    window._work_tabs.add_work(app_for_key("text_workbench"), screen)
    tab_id = next(iter(window._work_tabs.entries))
    payloads = []
    monkeypatch.setattr("apps.launcher.work_tabs.spawn", lambda value: payloads.append(value) or 123)
    window._work_tabs.detach(tab_id)
    assert tab_id in window._work_tabs.entries
    assert payloads[0]["text"] == "移動する文章"
    transfer_id = payloads[0]["transfer_id"]
    monkeypatch.setattr("apps.launcher.work_tabs.inventory", lambda: [{"transfer_id": transfer_id}])
    window._work_tabs._confirm_transfers()
    assert tab_id not in window._work_tabs.entries


def test_record_linked_file_manager_cannot_be_detached(window):
    from apps.file_tools.file_manager import FileManagerScreen
    screen = FileManagerScreen(lambda: None)
    screen._record_link_bundle = object()
    window._work_tabs.add_work(app_for_key("file_manager"), screen)
    entry = next(iter(window._work_tabs.entries.values()))
    payload, reason = window._work_tabs._detach_payload(entry)
    assert payload is None
    assert "対応表" in reason


def test_right_edge_has_only_the_two_universal_entries(window, monkeypatch):
    button = window._work_tabs.overview_button
    assert button.text() == "☰"
    assert button.accessibleName() == "PORTAの画面"
    explorer = window._work_tabs.explorer_button
    assert explorer.accessibleName() == "ファイルエクスプローラーを開く"
    assert explorer.size().width() == explorer.size().height() == 34
    opened = []
    monkeypatch.setattr("apps.launcher.work_tabs.open_in_standard_file_manager", lambda: opened.append(True))
    explorer.click()
    assert opened == [True]
    labels = [item.text() for item in window.findChildren(QPushButton)]
    assert "独立した作業" not in labels
    assert "プロセス一覧" not in labels


def test_unified_inventory_combines_menu_tab_window_and_record(monkeypatch):
    class Identity:
        key = "main-id"

    class Process:
        role = "main"
        identity = Identity()
        screen = "メインメニュー"
        health = "alive"
        state = "待機中"
        pid = 100

    monkeypatch.setattr("apps.porta_control.work_overview.process_inventory", lambda **_: [Process()])
    monkeypatch.setattr("apps.porta_control.work_overview.work_inventory", lambda: [
        {"endpoint": "/tmp/tab.sock", "pid": 100, "app": "file_manager", "host": "tab",
         "title": "ファイル", "level": 2, "reason": "対象を選択中"},
        {"endpoint": "/tmp/window.sock", "pid": 200, "app": "video_encoder", "host": "window",
         "title": "動画変換", "level": 4, "reason": "処理中"},
    ])
    monkeypatch.setattr("records.record_service.request", lambda *_, **__: {"ok": True, "records": [{
        "id": "TABLE", "title": "対応表A", "pid": 300, "level": 3, "reason": "編集中",
    }]})
    items = unified_inventory()
    assert [(item["kind"], item["title"]) for item in items] == [
        ("menu", "メインメニュー"), ("tab", "ファイル"),
        ("window", "動画変換"), ("record", "対応表A"),
    ]


def test_automation_remains_one_application_with_internal_sections():
    from apps.launcher.catalog import apps_for_category
    assert len(apps_for_category("automation_tools")) == 1
    workspace = app_for_key("browser_automation").create_screen(lambda: None)
    try:
        page = Screen(3)
        workspace.open_section("test", lambda back: page)
        workspace.stack.setCurrentIndex(0)
        assert workspace.describe_work_state()["level"] == 3
    finally:
        workspace.close()
