#!/bin/bash

# このスクリプトを実行しているターミナルを取得（自分自身は閉じない）
MY_TTY=$(ps -p $$ -o tty=)

if ! command -v ps >/dev/null 2>&1; then
    echo "ps がない環境では安全に判定できません。何もしません。"
    exit 1
fi
echo "注意: 他の端末の処理状態を完全には判定できません。"
read -r -p "待機中と判断した端末へHUPを送りますか？ [y/N] " answer
if [ "$answer" != "y" ] && [ "$answer" != "Y" ]; then
    echo "中止しました。"
    exit 0
fi
echo "🔍 ターミナルの確認を開始します..."

# 開いているすべての仮想ターミナル(pts)を順番にチェック
for tty in $(ls /dev/pts/[0-9]* 2>/dev/null | sed 's|/dev/||'); do
    # 自分自身のターミナルは処理から外す
    if [ "$tty" = "$MY_TTY" ]; then
        continue
    fi

    # そのターミナルで動いているプロセスのリストを取得
    PROCESSES=$(ps -t "$tty" --no-headers -o comm)

    # 存在しない場合はスキップ
    if [ -z "$PROCESSES" ]; then
        continue
    fi

    # 実行中のプロセスを確認（bashやpsなど、入力待ちの基本プロセス以外があるか判定）
    IS_BUSY=0
    for cmd in $PROCESSES; do
        if [[ "$cmd" != "bash" && "$cmd" != "zsh" && "$cmd" != "ps" ]]; then
            IS_BUSY=1
            break
        fi
    done

    if [ $IS_BUSY -eq 1 ]; then
        # 何か重要な処理が動いている場合：閉じずにデスクトップ通知を出す
        PROC_LIST=$(echo "$PROCESSES" | tr '\n' ' ')
        if command -v notify-send >/dev/null 2>&1; then
            notify-send "ターミナル一括整理" "⚠️ $tty は実行中 ($PROC_LIST) のため残しました。" --icon=dialog-warning
        fi
        echo "⚠️ $tty はスキップ: プロセスが実行中です"
    else
        # 何もしていない（入力待ち）場合：安全な切断シグナルを送って閉じる
        echo "✅ $tty は待機中のため閉じます"

        # 該当ターミナルのbashのPIDを探してHUP（ハングアップ＝正常な切断）を送る
        SHELL_PID=$(ps -t "$tty" --no-headers -o pid,comm | awk '$2=="bash" || $2=="zsh" {print $1}')
        if [ -n "$SHELL_PID" ]; then
            kill -HUP $SHELL_PID
        fi
    fi
done

echo "🎉 整理が完了しました！"
