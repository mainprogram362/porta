#!/usr/bin/env bash
# Open the interactive standalone system menu in a terminal when possible.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)" || exit 1
MENU_SCRIPT="$SCRIPT_DIR/system_menu.sh"

if [ ! -f "$MENU_SCRIPT" ]; then
    echo "エラー: システム操作メニューが見つかりません: $MENU_SCRIPT"
    exit 1
fi

if command -v gnome-terminal >/dev/null 2>&1; then
    exec gnome-terminal -- bash "$MENU_SCRIPT" "$@"
elif command -v x-terminal-emulator >/dev/null 2>&1; then
    exec x-terminal-emulator -e bash "$MENU_SCRIPT" "$@"
elif command -v konsole >/dev/null 2>&1; then
    exec konsole -e bash "$MENU_SCRIPT" "$@"
fi

echo "新規ターミナルを開けないため、この端末で実行します。"
exec bash "$MENU_SCRIPT" "$@"
