#!/bin/bash
# ==========================================
# システム操作: ハイバネート（確認・抑止を無視）
# ==========================================

if ! command -v systemctl >/dev/null 2>&1; then
    echo "systemctl がない環境では実行できません。"
    exit 1
fi
if [ "$(systemctl can-hibernate 2>/dev/null)" != "yes" ]; then
    echo "この環境ではハイバネートを利用できません。"
    exit 1
fi
read -r -p "ハイバネートします。保存していない作業を確認しましたか？ [y/N] " answer
if [ "$answer" != "y" ] && [ "$answer" != "Y" ]; then
    echo "中止しました。"
    exit 0
fi
sudo systemctl hibernate
