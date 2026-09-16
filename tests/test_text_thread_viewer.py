import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QUrl
from PySide6.QtWidgets import QApplication, QDialog

from apps.media_tools.text_thread_viewer import TextThreadViewerScreen
from apps.media_tools.text_thread_viewer.window import _reference_preview


def _write_thread(path, title, first_body, second_body):
    path.write_text(
        f"[1] {title} / 2026/08/24 / ID:first\n{first_body}\n\n"
        f"[2] 名前二 / 2026/08/24 / ID:second\n{second_body}\n",
        encoding="utf-8",
    )


def test_viewer_loads_threads_searches_across_them_and_expands_replies(tmp_path):
    QApplication.instance() or QApplication([])
    first = tmp_path / "Part 4__100_200.txt"
    second = tmp_path / "Part 5__100_201.txt"
    third = tmp_path / "Part 6__100_202.txt"
    _write_thread(first, "名前一", "最初の検索語", ">>1 への返信")
    _write_thread(second, "名前三", "次の検索語", "検索語を含む二つ目")
    _write_thread(third, "名前四", "対象外", "別の本文")
    screen = TextThreadViewerScreen(lambda: None)
    try:
        screen.source_input.append_items([str(first), str(second), str(third)])
        screen.load_texts()

        assert screen.thread_combo.count() == 3
        assert screen.status_label.isHidden()
        assert screen.source_box.isHidden()
        assert "[1]" in screen.reply_browser.toPlainText()

        screen.search_input.setText("検索語")
        assert screen.thread_combo.itemText(0) == "Part 4（ヒット 1件 / レス 2件）"
        assert screen.thread_combo.itemText(2) == "Part 6（ヒット 0件 / レス 2件）"
        screen._move_search_hit(1)
        assert screen._current_document_index == 0
        assert screen._current_reply_number == 1

        # スレッドを手動切替したら、検索位置を忘れてそのスレッドから再開する。
        screen.thread_combo.setCurrentIndex(1)
        assert screen._search_hit_index == -1
        assert "このスレッド 2件 / 全3件" == screen.search_info_label.text()
        screen._move_search_hit(1)
        assert screen._current_document_index == 1
        assert screen._current_reply_number == 1
        screen._move_search_hit(1)
        assert screen._current_document_index == 1
        assert screen._current_reply_number == 2

        screen._move_document(-1)
        assert screen._current_document_index == 0
        assert screen._search_hit_index == -1
        assert "このスレッド 1件 / 全3件" == screen.search_info_label.text()

        screen._toggle_expanded_viewer()
        assert not screen.source_box.isHidden()
        assert not screen.setup_widget.isHidden()
        screen._toggle_expanded_viewer()
        assert screen.source_box.isHidden()

        screen._open_search_results_window()
        result_window = next(window for window in screen.findChildren(QDialog) if window.isVisible())
        assert result_window.thread_combo.count() == 3
        result_window.thread_combo.setCurrentIndex(2)
        assert "0件" in result_window.browser.toPlainText()
        result_window.close()

        screen._show_reply(0, 1)
        scroll_before = screen.reply_browser.verticalScrollBar().value()
        screen._browser_link_clicked(QUrl("children:///1"))
        assert (0, 1) in screen._expanded_replies
        assert "返信ツリー 1件" in screen.reply_browser.toPlainText()
        assert "[2]" in screen.reply_browser.toPlainText()
        assert screen.reply_browser.verticalScrollBar().value() == scroll_before
        screen._browser_link_clicked(QUrl("reply:///2"))
        assert screen._current_reply_number == 2
    finally:
        screen.close()


def test_viewer_and_search_results_expand_nested_reply_trees(tmp_path):
    QApplication.instance() or QApplication([])
    path = tmp_path / "tree__100_204.txt"
    path.write_text(
        "[1] 親 / 2026/08/24 / ID:one\n検索対象の親\n\n"
        "[2] 子 / 2026/08/24 / ID:two\n>>1 子コメント\n\n"
        "[3] 孫 / 2026/08/24 / ID:three\n>>2 孫コメント\n",
        encoding="utf-8",
    )
    screen = TextThreadViewerScreen(lambda: None)
    try:
        screen.source_input.append_items([str(path)])
        screen.load_texts()
        screen._browser_link_clicked(QUrl("children:///1"))
        assert "孫コメント" in screen.reply_browser.toPlainText()
        assert "返信ツリー 2件" in screen.reply_browser.toPlainText()
        assert "[1] 親" in _reference_preview(screen._documents[0], "reply:///1")
        assert not _reference_preview(screen._documents[0], "children:///1")

        screen.search_input.setText("検索対象")
        screen._open_search_results_window()
        result_window = next(window for window in screen.findChildren(QDialog) if window.isVisible())
        result_window._browser_link_clicked(QUrl("children:///1"))
        assert "孫コメント" in result_window.browser.toPlainText()
        result_window.close()
    finally:
        screen.close()
