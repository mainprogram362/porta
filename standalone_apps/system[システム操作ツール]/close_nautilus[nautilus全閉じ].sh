#!/usr/bin/env bash
# Nautilus has no reliable public "is copying" signal. Never guess.

if ! command -v nautilus >/dev/null 2>&1; then
    echo "Nautilus が見つかりません。何もしません。"
    exit 0
fi
if ! pgrep -x nautilus >/dev/null; then
    echo "Nautilus は起動していません。"
    exit 0
fi
echo "注意: コピー・移動中かどうかは安全に判定できません。"
read -r -p "Nautilus に終了要求を送りますか？ [y/N] " answer
if [ "$answer" != "y" ] && [ "$answer" != "Y" ]; then
    echo "中止しました。"
    exit 0
fi
nautilus --quit
echo "終了要求を送信しました。"
