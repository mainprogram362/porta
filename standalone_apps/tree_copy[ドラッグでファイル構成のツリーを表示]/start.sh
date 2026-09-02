#!/bin/bash
# ==========================================
# Tree Copy Launcher
# ==========================================

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"

# Prefer an available terminal emulator, but remain usable from an existing shell.
if command -v gnome-terminal >/dev/null 2>&1; then
    exec gnome-terminal -- bash "$SCRIPT_DIR/tree_copy.sh" "$@"
elif command -v x-terminal-emulator >/dev/null 2>&1; then
    exec x-terminal-emulator -e bash "$SCRIPT_DIR/tree_copy.sh" "$@"
else
    echo "新規ターミナルを開けないため、この端末で実行します。"
    exec bash "$SCRIPT_DIR/tree_copy.sh" "$@"
fi
