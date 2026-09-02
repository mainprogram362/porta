#!/bin/bash
# ==========================================
# System Module: Dynamic Menu Launcher
# ==========================================

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
STANDALONE_APPS_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
COMMON_LIB="$STANDALONE_APPS_DIR/libs/common.sh"

if [ -f "$COMMON_LIB" ]; then
    source "$COMMON_LIB"
else
    echo "ERROR: 共通ライブラリが見つかりません: $COMMON_LIB"
    exit 1
fi

while true; do
    show_title "システム管理・メンテナンスメニュー"

    SCRIPT_FILES=()

    while IFS= read -r -d '' file; do
        FILENAME=$(basename "$file")

        # ランチャー自身と共通ライブラリは除外
        if [ "$FILENAME" != "start.sh" ] && [ "$FILENAME" != "system_menu.sh" ]; then
            SCRIPT_FILES+=("$file")
        fi
    done < <(find "$SCRIPT_DIR" -maxdepth 1 -name "*.sh" -print0 | sort -z)

    INDEX=1
    declare -A MENU_MAP

    if [ ${#SCRIPT_FILES[@]} -eq 0 ]; then
        echo "登録されているツールがありません。"
    else
        for script in "${SCRIPT_FILES[@]}"; do
            FILENAME=$(basename "$script")
            DISPLAY_NAME="${FILENAME%.sh}"

            printf " %2d) %s\n" "$INDEX" "$DISPLAY_NAME"
            MENU_MAP[$INDEX]="$script"
            INDEX=$((INDEX + 1))
        done
    fi

    echo "========================================"
    echo " 0) 戻る (統合メニューへ)"
    echo "========================================"

    prompt_with_timeout "番号を入力してください : " 60
    STATUS=$?

    # タイムアウト
    if [ $STATUS -eq 124 ]; then
        break
    fi

    CHOICE=$(normalize_input "$REPLY_INPUT")
    [ -z "$CHOICE" ] && continue

    # 統合メニューへ戻る
    if [ "$CHOICE" = "0" ]; then
        break
    fi

    SELECTED_SCRIPT="${MENU_MAP[$CHOICE]}"

    if [ -n "$SELECTED_SCRIPT" ]; then
        echo
        echo "実行中: $(basename "$SELECTED_SCRIPT")..."
        echo

        bash "$SELECTED_SCRIPT"

        echo
        prompt_with_timeout "エンターキーを押してメニューに戻ります..." 60
    else
        echo
        echo "無効な番号です。"
        sleep 1
    fi
done

exit 0
