import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from apps.media_tools.board_response_archive import BoardResponseArchiveScreen
from media.shitaraba import (
    ShitarabaBatchInspection,
    ShitarabaInspection,
    ShitarabaReply,
    ShitarabaThread,
    parse_thread_url,
)


def test_board_response_archive_enables_extraction_only_after_a_valid_check():
    QApplication.instance() or QApplication([])
    screen = BoardResponseArchiveScreen(lambda: None)
    try:
        assert not screen.extract_button.isEnabled()
        thread = ShitarabaThread(
            reference=parse_thread_url(
                "https://jbbs.shitaraba.net/bbs/read.cgi/computer/10298/1158291064/"
            ),
            title="題名",
            replies=(ShitarabaReply(1, "名前", "日時", "本文", "ID"),),
        )
        screen._inspection_finished(
            ShitarabaBatchInspection(
                (ShitarabaInspection(True, "1 件のレスを抽出できます。", thread),)
            )
        )

        assert screen.extract_button.isEnabled()
        screen.extract_replies()
        assert "[1] 名前 / 日時 / ID" in screen.replies_editor.toPlainText()
        screen.destination_input.setText("/tmp")
        assert screen.save_button.isEnabled()
    finally:
        screen.close()
