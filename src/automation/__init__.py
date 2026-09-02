"""Automation helpers exposed as a stable public API."""

__all__ = [
    "move_and_click",
    "open_target",
    "paste_text",
    "reveal_in_file_manager",
    "repeat_key",
    "scroll_page",
]


def paste_text(content: str, press_enter: bool = False, delay: float = 0.3) -> None:
    from .input import paste_text as _paste_text

    return _paste_text(content=content, press_enter=press_enter, delay=delay)


def repeat_key(key: str, count: int = 1, interval: float = 0.1) -> None:
    from .input import repeat_key as _repeat_key

    return _repeat_key(key=key, count=count, interval=interval)


def move_and_click(
    x: int,
    y: int,
    click: bool = True,
    modifiers: list[str] | None = None,
    delay: float = 0.5,
) -> None:
    from .input import move_and_click as _move_and_click

    return _move_and_click(x=x, y=y, click=click, modifiers=modifiers, delay=delay)


def scroll_page(clicks: int = -3, direction: str = "down", delay: float = 0.5) -> None:
    from .input import scroll_page as _scroll_page

    return _scroll_page(clicks=clicks, direction=direction, delay=delay)


def open_target(path):
    from .desktop import open_target as _open_target

    return _open_target(path)


def reveal_in_file_manager(path):
    from .desktop import reveal_in_file_manager as _reveal_in_file_manager

    return _reveal_in_file_manager(path)
