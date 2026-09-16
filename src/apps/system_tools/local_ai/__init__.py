"""Shared local-AI screens."""

from .chat import LocalAiChatScreen
from .window import LocalAiSettingsScreen, create_screen

__all__ = ["LocalAiChatScreen", "LocalAiSettingsScreen", "create_screen"]


def create_workspace(back):
    from gui.section_workspace import SectionWorkspace
    return SectionWorkspace(back, 'ローカルAI', (
        ('chat', 'チャット', LocalAiChatScreen),
        ('settings', 'ローカルAI設定', create_screen),
    ))
