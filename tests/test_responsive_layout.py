"""Real viewport geometry, including long paths and enlarged application fonts."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication, QPushButton, QStackedWidget

from apps.launcher.catalog import APPS, app_for_key
from apps.launcher.main_menu import MainMenuWindow


def settle(app):
    for _ in range(5):
        app.processEvents()


@pytest.mark.parametrize("point_size", [9, 13])
def test_all_tools_keep_user_size_and_expose_overflow(point_size, work_host_factory):
    app = QApplication.instance() or QApplication([])
    original_font = app.font()
    font = QFont(original_font)
    font.setPointSize(point_size)
    app.setFont(font)
    window = None
    try:
        for definition in APPS:
            window = work_host_factory(definition.key)
            window.show()
            window.resize(800, 640)
            settle(app)
            screen = window.work_screen
            area = QStackedWidget.currentWidget(window.centralWidget())
            assert window.size() == QSize(800, 640), definition.key
            for width, height in [(600, 440), (960, 720), (1440, 1000)]:
                window.resize(width, height)
                settle(app)
                assert window.size() == QSize(width, height), definition.key
                assert screen.width() >= screen.layout().minimumSize().width(), definition.key
                for bar in (area.horizontalScrollBar(), area.verticalScrollBar()):
                    bar.setValue(bar.maximum())
                settle(app)
                # Both far edges remain reachable, even below the page minimum.
                assert screen.geometry().right() < area.viewport().width(), definition.key
                assert screen.geometry().bottom() < area.viewport().height(), definition.key
                for button in screen.findChildren(QPushButton):
                    if button.isVisibleTo(screen) and not button.isWindow():
                        assert button.parentWidget().rect().contains(button.geometry()), (
                            definition.key, button.text(), button.geometry()
                        )
            window.hide()
    finally:
        if window is not None:
            window.hide()
        app.setFont(original_font)


def test_file_manager_long_paths_and_stacked_output_keep_data(work_host_factory):
    app = QApplication.instance() or QApplication([])
    window = work_host_factory('file_manager')
    paths = ["/別の場所/" + "長い日本語のフォルダ/" * 12 + "同じ名前.txt",
             "/空白 のある場所/同じ名前.txt"]
    try:
        window.show()
        window.resize(800, 640)
        screen = window.work_screen
        screen.search_results_input.setPlainText("\n".join(paths))
        screen.copy_mode_combo.setCurrentIndex(1)
        screen.selection_filter_toggle.setChecked(True)
        settle(app)
        tree = screen.search_results_input._tree
        assert tree.textElideMode() == Qt.TextElideMode.ElideNone
        assert tree.horizontalScrollBar().maximum() > 0
        assert tree.topLevelItem(0).text(1) == paths[0]
        assert screen.search_results_input.toPlainText() == "\n".join(paths)
        assert screen._work_splitter.orientation() == Qt.Orientation.Vertical
        assert screen._work_splitter.widget(1) is screen.output_operation_box
        for width in (1200, 800):
            window.resize(width, 720)
            settle(app)
            assert screen.search_results_input.toPlainText() == "\n".join(paths)
        screen.operation_combo.setCurrentIndex(screen.operation_combo.findData("rename"))
        for index in range(screen.rename_panel.rule_kind_combo.count()):
            screen.rename_panel.rule_kind_combo.setCurrentIndex(index)
            settle(app)
            assert window.size() == QSize(800, 720)
            for button in screen.findChildren(QPushButton):
                if button.isVisibleTo(screen) and not button.isWindow():
                    assert button.parentWidget().rect().contains(button.geometry()), button.text()
        assert screen.search_results_input.toPlainText() == "\n".join(paths)
        assert screen.favorite_full_paths_check.isChecked() is False
        screen.favorite_full_paths_check.setChecked(True)
        for index in range(screen.favorites_list.count()):
            item = screen.favorites_list.item(index)
            assert item.text() == item.data(Qt.ItemDataRole.UserRole)
    finally:
        window.hide()


def test_file_manager_keeps_the_workspace_visible_below_work_window_chrome(work_host_factory):
    """A status summary must not consume the file manager's working area."""
    app = QApplication.instance() or QApplication([])
    window = work_host_factory("file_manager")
    try:
        window.show()
        window.resize(1000, 700)
        settle(app)
        screen = window.work_screen
        assert window.centralWidget().height() >= 640
        assert window.statusBar().height() <= window.fontMetrics().lineSpacing() + 10
        assert screen._work_splitter.height() >= 480
    finally:
        window.hide()
