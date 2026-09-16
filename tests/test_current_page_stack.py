"""Hidden work must not dictate geometry or be discarded when the menu shrinks."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSize
from PySide6.QtWidgets import QApplication, QLabel, QLineEdit, QVBoxLayout, QWidget, QStackedWidget

from gui.current_page_stack import CurrentPageStack
from apps.launcher.catalog import app_for_key
from apps.launcher.main_menu import MainMenuWindow


def test_hidden_page_does_not_enlarge_stack_and_keeps_its_input():
    app = QApplication.instance() or QApplication([])
    host = QWidget()
    layout = QVBoxLayout(host)
    stack = CurrentPageStack()
    layout.addWidget(stack)
    small = QLineEdit()
    small.setMinimumSize(100, 30)
    large = QLineEdit()
    large.setMinimumSize(1000, 800)
    large.setText("未保存の作業")
    stack.addWidget(small)
    stack.addWidget(large)
    try:
        host.show()
        stack.setCurrentWidget(large)
        app.processEvents()
        assert host.width() >= 1000
        stack.setCurrentWidget(small)
        app.processEvents()
        host.resize(400, 180)
        app.processEvents()
        assert host.size() == QSize(400, 180)
        assert stack.rect().contains(small.geometry())
        assert stack.minimumSizeHint().width() < 1000
        assert stack.minimumSizeHint().height() < 800
        stack.setCurrentWidget(large)
        app.processEvents()
        assert stack.rect().contains(large.geometry())
        assert large.text() == "未保存の作業"
    finally:
        host.close()


def test_only_current_page_contributes_wrapped_text_height():
    QApplication.instance() or QApplication([])
    stack = CurrentPageStack()
    plain = QLineEdit()
    wrapped = QLabel("長い説明文 " * 100)
    wrapped.setWordWrap(True)
    stack.addWidget(plain)
    stack.addWidget(wrapped)
    stack.setCurrentWidget(wrapped)
    assert stack.hasHeightForWidth()
    assert stack.heightForWidth(200) > stack.heightForWidth(600)
    stack.setCurrentWidget(plain)
    assert not stack.hasHeightForWidth()
    assert stack.heightForWidth(200) == -1
    stack.close()


def test_menu_can_shrink_and_close_without_losing_independent_work(work_host_factory):
    app = QApplication.instance() or QApplication([])
    window = MainMenuWindow()
    work_window = work_host_factory("text_workbench")
    work = work_window.work_screen
    try:
        work.text_editor.setPlainText("管理本体を閉じても保持")
        work_window.show()
        work_window.resize(work_window.minimumSize())
        window.show()
        app.processEvents()
        area = QStackedWidget.currentWidget(work_window.centralWidget())
        assert area.widget() is work
        area.verticalScrollBar().setValue(area.verticalScrollBar().maximum())
        app.processEvents()
        assert work.geometry().bottom() <= area.viewport().height()
        window.resize(window.minimumSize())
        app.processEvents()
        menu_area = QStackedWidget.currentWidget(window._screens)
        assert menu_area.verticalScrollBar().maximum() == 0
        assert menu_area.horizontalScrollBar().maximum() == 0
        assert work.text_editor.toPlainText() == "管理本体を閉じても保持"
        window.close()
        assert work_window.isVisible()
        assert work.text_editor.toPlainText() == "管理本体を閉じても保持"
    finally:
        window.close()
        work_window.hide()
