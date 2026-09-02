import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from gui import UrlListTextEdit


def test_url_list_text_edit_appends_dropped_urls_as_new_lines():
    QApplication.instance() or QApplication([])
    widget = UrlListTextEdit()
    widget.setPlainText("https://example.test/first")

    widget.append_urls(["https://example.test/second", "http://example.test/third"])

    assert widget.toPlainText().splitlines() == [
        "https://example.test/first",
        "https://example.test/second",
        "http://example.test/third",
    ]
