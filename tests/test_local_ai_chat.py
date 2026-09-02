import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from apps.system_tools.local_ai.chat import LocalAiChatScreen


def test_local_ai_chat_keeps_a_transient_operational_activity_log():
    QApplication.instance() or QApplication([])
    screen = LocalAiChatScreen(lambda: None)
    try:
        assert "AIプロセスはまだ起動していません" in screen._activity.toPlainText()
        screen.clear_conversation()
        assert "会話文脈をメモリから消去" in screen._activity.toPlainText()
    finally:
        screen.close()


def test_local_ai_chat_shutdown_is_safe_to_call_more_than_once():
    QApplication.instance() or QApplication([])
    screen = LocalAiChatScreen(lambda: None)
    try:
        screen.shutdown()
        screen.shutdown()
        assert screen._is_shutdown
    finally:
        screen.close()
