#!/usr/bin/env bash
# PORTA Coreの日常メニュー。本体からも、CORE単体からも起動できる。

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)" || exit 1
COMMON_LIB="$SCRIPT_DIR/libs/common.sh"

if [ ! -f "$COMMON_LIB" ]; then
    echo "ERROR: PORTA Coreの共通ライブラリが見つかりません。"
    exit 1
fi
source "$COMMON_LIB"

CORE_MENU_ONLY=0
case "${1:-}" in
    "") ;;
    --menu-only) CORE_MENU_ONLY=1 ;;
    *)
        _core_error "不明な引数です: $1"
        exit 2
        ;;
esac

# CORE is the shortcut entrypoint.  Prefer the full Python workspace after a
# visible preflight; retain this menu only when the workspace cannot be used.
if [ "$CORE_MENU_ONLY" -eq 1 ]; then
    CORE_PRESERVE_STARTUP_OUTPUT=1
elif preflight_main_application; then
    printf '%b\n' "${C_GREEN}PORTA本体を確認しました。起動します。${C_RESET}"
    if launch_main_application; then
        if show_main_application_detach_notice; then
            CORE_PRESERVE_STARTUP_OUTPUT=1
        else
            exit 0
        fi
    else
        launch_status=$?
        printf '%b\n' "${C_RED}PORTAが終了コード $launch_status で終了しました。COREメニューを開きます。${C_RESET}"
    fi
    CORE_PRESERVE_STARTUP_OUTPUT=1
else
    CORE_PRESERVE_STARTUP_OUTPUT=1
fi

choose_application() {
    local base_dir classification display_name item_dir start_sh
    local index=1 choice selected
    declare -a launcher_paths targets

    while true; do
        show_title "アプリを選択"
        unset launcher_paths
        declare -a launcher_paths
        index=1

        for classification in standalone external; do
            case "$classification" in
                standalone)
                    display_name="独立ツール（位置変更は非推奨）"
                    targets=("${STANDALONE_APPS_DIRS[@]}")
                    ;;
                external)
                    display_name="外部プログラム（明示登録のみ）"
                    targets=("${EXTERNAL_PROGRAM_DIRS[@]}")
                    ;;
            esac
            printf '%b\n' "${C_BLUE}${display_name}${C_RESET}"
            if [ "${#targets[@]}" -eq 0 ]; then
                printf '%b\n\n' "  ${C_GRAY}置き場は登録されていません${C_RESET}"
                continue
            fi
            for base_dir in "${targets[@]}"; do
                if [ ! -d "$base_dir" ]; then
                    printf '  %s: %b\n' "$(basename "$base_dir")" "${C_GRAY}利用できません${C_RESET}"
                    continue
                fi
                for item_dir in "$base_dir"/*; do
                    start_sh="$item_dir/start.sh"
                    if [ -f "$start_sh" ]; then
                        printf '  %2d) %s\n' "$index" "$(basename "$item_dir")"
                        launcher_paths[$index]="$start_sh"
                        index=$((index + 1))
                    fi
                done
            done
            echo
        done
        echo "  0) 戻る"
        prompt_with_timeout "番号を入力してください : " 60 || return 0
        choice=$(normalize_input "$REPLY_INPUT")
        [ "$choice" = "0" ] && return 0
        selected="${launcher_paths[$choice]}"
        if [ -z "$selected" ]; then
            printf '%b\n' "${C_RED}無効な番号です。${C_RESET}"
            sleep 1
            continue
        fi
        run_app "$selected"
        return 0
    done
}

pause_for_menu() {
    echo
    prompt_with_timeout "エンターキーを押してメニューに戻ります..." 60 || true
}

run_standalone_system_tools() {
    local base_dir target
    for base_dir in "${STANDALONE_APPS_DIRS[@]}"; do
        target="$base_dir/system[システム操作ツール]/start.sh"
        if [ -f "$target" ]; then
            run_app "$target"
            return
        fi
    done
    _core_error "独立ツールにシステム操作ツールがありません。"
}

while true; do
    if [ "${CORE_PRESERVE_STARTUP_OUTPUT:-0}" = "1" ]; then
        printf '\n'
        printf '%b\n' "${C_CYAN}┌─────────────────────────────────────────────────────────┐${C_RESET}"
        printf '%b\n' "${C_CYAN}│${C_RESET}  ${C_BOLD}PORTA Core 日常メニュー${C_RESET}"
        printf '%b\n\n' "${C_CYAN}└─────────────────────────────────────────────────────────┘${C_RESET}"
        CORE_PRESERVE_STARTUP_OUTPUT=0
    else
        show_title "PORTA Core 日常メニュー"
    fi
    printf '%b\n' "${C_GREEN}Pythonプログラム${C_RESET}"
    if [ "$PORTA_AVAILABLE" -eq 1 ]; then
        echo "  1) PORTA メインメニューを開く"
    else
        echo "  1) PORTA メインメニューを開く（本体未接続）"
    fi
    echo
    printf '%b\n' "${C_BLUE}それ以外のプログラム${C_RESET}"
    echo "  2) 基幹・外部プログラムを選択して開く"
    echo "  3) PORTA Coreの配置場所をファイルマネージャーで開く"
    echo
    printf '%b\n' "${C_CYAN}確認・保守（読み取りのみ）${C_RESET}"
    echo "  4) 現在の状態を確認する"
    echo "  5) PORTA Coreの自己診断をする"
    echo
    printf '%b\n' "${C_YELLOW}慎重な操作${C_RESET}"
    echo "  6) システム管理・メンテナンス"
    echo
    echo "  0) 終了"

    prompt_with_timeout "番号を入力してください : " 60
    status=$?
    [ "$status" -eq 124 ] && exit 0
    choice=$(normalize_input "$REPLY_INPUT")

    case "$choice" in
        1)
            if preflight_main_application; then
                if launch_main_application; then
                    if show_main_application_detach_notice; then
                        continue
                    fi
                    exit 0
                fi
                _core_error "PORTAの起動に失敗しました。"
            fi
            ;;
        2) choose_application ;;
        3) open_in_file_manager "$PORTA_DIR"; exit 0 ;;
        4) show_workspace_status; pause_for_menu ;;
        5) core_self_check; pause_for_menu ;;
        6) run_standalone_system_tools ;;
        0) exit 0 ;;
        *) printf '%b\n' "${C_RED}無効な番号です。${C_RESET}"; sleep 1 ;;
    esac
done
