"""
PORTA - Automation Input Module
汎用的なキーボード・マウス入力エミュレーション
"""

import time
from typing import List, Optional

import pyautogui
import pyperclip

def paste_text(content: str, press_enter: bool = False, delay: float = 0.3) -> None:
    """
    クリップボードを経由してテキストを貼り付ける。
    日本語などのマルチバイト文字が文字化けしたり、キー入力が抜けたりするのを防ぐ最も安全な方法。
    """
    pyperclip.copy(content)
    time.sleep(0.1)  # クリップボード反映待ち
    pyautogui.hotkey("ctrl", "v")

    if press_enter:
        time.sleep(delay)
        pyautogui.press("enter")


def repeat_key(key: str, count: int = 1, interval: float = 0.1) -> None:
    """
    指定したキーを一定間隔で連続入力する。（例: tabキーを5回押す 等）
    """
    for _ in range(count):
        pyautogui.press(key)
        time.sleep(interval)


def move_and_click(
    x: int,
    y: int,
    click: bool = True,
    modifiers: Optional[List[str]] = None,
    delay: float = 0.5,
) -> None:
    """
    指定座標に移動し、必要に応じて修飾キー（ctrl, shift等）を押しながらクリックする。

    :param modifiers: 同時押しする修飾キーのリスト（例: ['ctrl', 'shift']）
    """
    time.sleep(delay)
    pyautogui.moveTo(x, y)

    if modifiers:
        for mod in modifiers:
            pyautogui.keyDown(mod)

    if click:
        pyautogui.click()

    if modifiers:
        for mod in reversed(modifiers):
            pyautogui.keyUp(mod)


def scroll_page(clicks: int = -3, direction: str = "down", delay: float = 0.5) -> None:
    """
    画面をスクロールする。

    :param clicks: スクロール量（マイナスが下方向、プラスが上方向になる環境が多い）
    :param direction: 'down', 'up', 'pagedown', 'pageup' （キーボードでのスクロール指定用）
    """
    time.sleep(delay)

    # ページ単位で大きく動かす場合
    if direction in ["pagedown", "pageup"]:
        pyautogui.press(direction)
    else:
        # マウスホイールによるスクロール
        pyautogui.scroll(clicks)
