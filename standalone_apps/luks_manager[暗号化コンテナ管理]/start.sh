#!/usr/bin/env bash
# CORE外から起動された場合も、LUKSの対話操作に必要な端末を確保する入口。

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)" || exit 1
MANAGER_SCRIPT="$SCRIPT_DIR/luks_manager.sh"

if [ ! -f "$MANAGER_SCRIPT" ]; then
    echo "エラー: LUKS管理本体が見つかりません: $MANAGER_SCRIPT"
    exit 1
fi

# This mirrors tree_copy's launcher: Python's detached external-app launcher
# has no stdin/stdout, so the interactive manager must own a fresh terminal.
if command -v gnome-terminal >/dev/null 2>&1; then
    exec gnome-terminal -- bash "$MANAGER_SCRIPT" "$@"
elif command -v x-terminal-emulator >/dev/null 2>&1; then
    exec x-terminal-emulator -e bash "$MANAGER_SCRIPT" "$@"
elif command -v konsole >/dev/null 2>&1; then
    exec konsole -e bash "$MANAGER_SCRIPT" "$@"
fi

echo "新規ターミナルを開けないため、この端末で実行します。"
exec bash "$MANAGER_SCRIPT" "$@"
