import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication, QPushButton, QStackedWidget, QWidget

from apps.launcher.main_menu import MainMenuWindow
from gui.responsive_grid import ResponsiveGridLayout


def test_responsive_grid_reflows_from_control_size_hints():
    app = QApplication.instance() or QApplication([])
    host = QWidget()
    layout = ResponsiveGridLayout(host, maximum_columns=3)
    buttons = [QPushButton(text) for text in ("短い", "文言が長いボタン", "中くらい")]
    for button in buttons:
        layout.addWidget(button)
    required = max(button.sizeHint().width() for button in buttons)
    left, _top, right, _bottom = layout.getContentsMargins()
    host.show()
    host.resize(required * 3 + layout.spacing() * 2 + left + right, 200)
    app.processEvents()
    assert len({button.y() for button in buttons}) == 1
    host.resize(required + left + right, 300)
    app.processEvents()
    assert len({button.y() for button in buttons}) == 3


@pytest.mark.parametrize("point_size", (9, 13))
def test_main_menu_minimum_shows_every_entry_without_scrollbars(point_size):
    app = QApplication.instance() or QApplication([])
    original_font = app.font()
    font = QFont(original_font)
    font.setPointSize(point_size)
    app.setFont(font)
    window = MainMenuWindow()
    try:
        window.show()
        window.resize(window.minimumSize())
        for _ in range(8):
            app.processEvents()
        area = QStackedWidget.currentWidget(window._screens)
        assert area.verticalScrollBar().maximum() == 0
        assert area.horizontalScrollBar().maximum() == 0
    finally:
        window.close()
        app.setFont(original_font)
