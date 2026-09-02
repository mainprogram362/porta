#!/bin/bash
# ==========================================
# App: Tree Copy (動的階層変更・無制限版)
# ==========================================

# Never kill the parent menu or unrelated terminal processes.
trap 'echo -e "\n終了します。"; exit 0' SIGINT SIGTERM

# --- 1. クリップボードツールの準備 ---
CLIP_CMD=""

if command -v xclip >/dev/null 2>&1; then
    CLIP_CMD="xclip -selection clipboard"
elif command -v xsel >/dev/null 2>&1; then
    CLIP_CMD="xsel --clipboard --input"
elif command -v wl-copy >/dev/null 2>&1; then
    CLIP_CMD="wl-copy"
fi

if [ -z "$CLIP_CMD" ]; then
    echo "【エラー】コピー用ツール(xclip/wl-copy等)が見つかりません。"
    echo "sudo apt install xclip でインストールしてください。"
    read -rp "エンターキーを押して終了..."
    exit 1
fi

# ランチャーの入力の残骸（Enterなど）を読み捨てる
while read -r -t 0.1 -n 1 2>/dev/null; do :; done

# --- 初期設定 ---
IDLE_TIMEOUT=20
LAST_ACTIVE=$SECONDS
MAX_DEPTH=4  # デフォルトの階層数

# --- 2. メインループ ---
while true; do
    clear

    echo "========================================"
    echo " 🌳 Tree Copy (連続モード)"
    echo "========================================"
    echo " 【現在の設定階層: $MAX_DEPTH 階層】"
    echo "----------------------------------------"
    echo "・フォルダをドラッグ＆ドロップ ➔ ツリー生成＆コピー"
    echo "・数字(1〜9)を入力して Enter  ➔ 表示階層数を変更"
    echo "（終了するには Ctrl+C またはウィンドウを閉じてください）"
    echo "----------------------------------------"
    echo " 20秒間操作がない場合、自動終了します。"
    echo "========================================"

    TARGET_DIR=""
    buffer=""

    # 入力待ちループ
    while true; do
        if read -r -t 0.05 -n 1 char 2>/dev/null; then
            buffer+="$char"
            LAST_ACTIVE=$SECONDS  # 入力があったらタイマーリセット
        else
            sleep 0.05

            # タイムアウト判定
            if [ $((SECONDS - LAST_ACTIVE)) -ge "$IDLE_TIMEOUT" ]; then
                echo -e "\n\n【タイムアウト】${IDLE_TIMEOUT}秒間操作がなかったため終了します。"
                sleep 2
                exit 0
            fi

            if [ -n "$buffer" ]; then
                clean_input="$buffer"

                # 1. 前後の改行や余計なスペースをトリム
                clean_input=$(echo "$clean_input" | tr -d '\r\n' | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')

                # 2. 数字指定の判定 (1〜9の数値入力の場合)
                if [[ "$clean_input" =~ ^[1-9][0-9]*$ ]]; then
                    MAX_DEPTH="$clean_input"
                    echo -e "\n\n⚙️  表示階層を 【 $MAX_DEPTH 】 に変更しました。"
                    sleep 1
                    LAST_ACTIVE=$SECONDS
                    break
                fi

                # 3. パス文字列の整形（引用符やエスケープの除去）
                clean_path=$(echo "$clean_input" \
                    | sed \
                        -e "s/^'//" \
                        -e "s/'$//" \
                        -e 's/^"//' \
                        -e 's/"$//' \
                        -e 's/\\ / /g' \
                        -e 's/\\//g')

                # 4. フォルダパスとして存在するか判定
                if [ -d "$clean_path" ]; then
                    TARGET_DIR="$clean_path"
                    break
                else
                    # 途中入力中、または無効な入力の場合はバッファをリセット
                    buffer=""
                fi
            fi
        fi
    done

    # 数字が入力されて階層変更が行われた場合は画面をクリアして再開
    if [ -z "$TARGET_DIR" ]; then
        continue
    fi

    # --- 3. ツリー生成（フルパスをルートとして含める） ---
    if command -v tree >/dev/null 2>&1; then

        # tree本体を生成
        TREE_CONTENT=$(tree -L "$MAX_DEPTH" --noreport "$TARGET_DIR" 2>/dev/null)

        # treeが自動的に表示する先頭のディレクトリ名を削除
        TREE_CONTENT=$(printf '%s\n' "$TREE_CONTENT" | tail -n +2)

        # 指定したフルパスをルートとして追加
        RESULT="$TARGET_DIR"$'\n'"$TREE_CONTENT"

    else

        TREE_CONTENT=$(find "$TARGET_DIR" -maxdepth "$MAX_DEPTH" \
            | sort \
            | sed \
                -e "s#^$TARGET_DIR##" \
                -e 's#^/##' \
                -e 's#[^/]*/#|  #g' \
                -e 's#|  \([^|]\)#|-- \1#')

        RESULT="$TARGET_DIR"$'\n'"$TREE_CONTENT"
    fi

    # --- 4. コピー ＆ 表示 ---
    echo -n "$RESULT" | $CLIP_CMD

    DIR_NAME=$(basename "$TARGET_DIR")

    echo
    echo "----------------------------------------"
    echo "【生成されたツリー構造 ($DIR_NAME / $MAX_DEPTH階層)】"
    echo "----------------------------------------"
    echo
    echo "$RESULT"
    echo
    echo "----------------------------------------"
    echo "✅ クリップボードへコピーしました。"
    echo "========================================"

    if command -v notify-send >/dev/null 2>&1; then
        notify-send \
            "🌳 Tree Copy" \
            "「$DIR_NAME」のツリー構造（$MAX_DEPTH階層）をコピーしました。" \
            -t 2000
    fi

    echo
    echo "⏳ 6秒後に次の入力を受け付けます..."
    sleep 6

    LAST_ACTIVE=$SECONDS
done
