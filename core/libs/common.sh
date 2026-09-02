#!/usr/bin/env bash
# Minimal, portable launcher helpers. This file is safe to source repeatedly.

[ -n "${_CORE_COMMON_LOADED:-}" ] && return 0
_CORE_COMMON_LOADED=1
C_RESET='\033[0m'; C_BOLD='\033[1m'; C_RED='\033[31m'; C_GREEN='\033[32m'
C_YELLOW='\033[33m'; C_CYAN='\033[36m'; C_BLUE='\033[34m'; C_GRAY='\033[90m'

_core_error() { printf '%b\n' "${C_RED}PORTA Core: $1${C_RESET}" >&2; }
_core_warn() { printf '%b\n' "${C_YELLOW}PORTA Core: $1${C_RESET}" >&2; }

# Resolve from this library; neither mount location nor workspace name is required.
SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)" || return 1
export LIBS_DIR="$SELF_DIR"
export CORE_DIR="$(cd "$SELF_DIR/.." && pwd -P)" || return 1
export PORTA_DIR="$(cd "$CORE_DIR/.." && pwd -P)" || return 1
export OPEN_SPACE_DIR="$(cd "$PORTA_DIR/.." && pwd -P)" || return 1
export CORE_LIBS_DIR="$CORE_DIR/libs"
export STANDALONE_APPS_DIR="$PORTA_DIR/standalone_apps"
export PORTA_LOCATION_FILE="$PORTA_DIR/persistent_settings_location.txt"

if [ ! -f "$CORE_LIBS_DIR/common.sh" ] || [ ! -f "$CORE_DIR/main_menu.sh" ]; then
    _core_error "基本構成が見つかりません: $CORE_DIR"
    return 1
fi

PORTA_AVAILABLE=0
if [ -x "$PORTA_DIR/.venv/bin/python" ] && [ -f "$PORTA_DIR/scripts/main.py" ]; then
    PORTA_AVAILABLE=1
fi
export PORTA_AVAILABLE

PORTA_CONFIG_STATE="入口ファイルなし"
PORTA_USER_ROOT=""
CONFIG_DIR=""
STANDALONE_APPS_FILE=""
EXTERNAL_PROGRAM_FILE=""
STANDALONE_APPS_DIRS=()
EXTERNAL_PROGRAM_DIRS=()
if command -v readlink >/dev/null 2>&1 && [ -f "$PORTA_LOCATION_FILE" ]; then
    location_value=""
    location_count=0
    while IFS= read -r raw_line || [ -n "$raw_line" ]; do
        line=$(printf '%s' "$raw_line" | sed 's/^[[:space:]]*//; s/[[:space:]]*$//')
        [ -z "$line" ] && continue
        case "$line" in \#*) continue ;; esac
        location_value="$line"
        location_count=$((location_count + 1))
    done < "$PORTA_LOCATION_FILE"
    case "$location_value" in
        '~'*|*'$'*) location_count=0 ;;
    esac
    if [ "$location_count" -eq 1 ]; then
        case "$location_value" in
            /*) PORTA_USER_ROOT=$(readlink -m -- "$location_value" 2>/dev/null) ;;
            *) PORTA_USER_ROOT=$(readlink -m -- "$PORTA_DIR/$location_value" 2>/dev/null) ;;
        esac
        if [ -n "$PORTA_USER_ROOT" ]; then
            CONFIG_DIR="$PORTA_USER_ROOT/config"
            STANDALONE_APPS_FILE="$CONFIG_DIR/shared/standalone_apps_location.txt"
            EXTERNAL_PROGRAM_FILE="$CONFIG_DIR/shared/external_program_locations.txt"
            PORTA_CONFIG_STATE="入口を確認"
        fi
    else
        PORTA_CONFIG_STATE="入口ファイル形式不正"
    fi
fi

read_program_locations() {
    local settings_file="$1" exactly_one="$2" result_name="$3"
    local settings_base line raw_line resolved duplicate existing
    local -n result="$result_name"
    result=()
    [ -f "$settings_file" ] || return 1
    settings_base=$(dirname "$settings_file")
    while IFS= read -r raw_line || [ -n "$raw_line" ]; do
        line=$(printf '%s' "$raw_line" | sed 's/^[[:space:]]*//; s/[[:space:]]*$//')
        [ -z "$line" ] && continue
        case "$line" in \#*) continue ;; esac
        case "$line" in
            '~'*|*'$'*) result=(); return 2 ;;
            @PORTA) resolved="$PORTA_DIR" ;;
            @PORTA/*) resolved=$(readlink -m -- "$PORTA_DIR/${line#@PORTA/}" 2>/dev/null) ;;
            @*) result=(); return 2 ;;
            /*) resolved=$(readlink -m -- "$line" 2>/dev/null) ;;
            *) resolved=$(readlink -m -- "$settings_base/$line" 2>/dev/null) ;;
        esac
        [ -n "$resolved" ] || { result=(); return 2; }
        duplicate=0
        for existing in "${result[@]}"; do
            [ "$existing" = "$resolved" ] && duplicate=1 && break
        done
        [ "$duplicate" -eq 1 ] || result+=("$resolved")
    done < "$settings_file"
    [ "$exactly_one" -eq 0 ] || [ "${#result[@]}" -eq 1 ] || { result=(); return 2; }
    return 0
}

STANDALONE_APPS_STATE="位置設定なし"
EXTERNAL_PROGRAM_STATE="位置設定なし"
if [ -n "$STANDALONE_APPS_FILE" ]; then
    read_program_locations "$STANDALONE_APPS_FILE" 1 STANDALONE_APPS_DIRS
    case "$?" in
        0) STANDALONE_APPS_STATE="位置設定を確認" ;;
        2) STANDALONE_APPS_STATE="位置設定形式不正" ;;
    esac
    read_program_locations "$EXTERNAL_PROGRAM_FILE" 0 EXTERNAL_PROGRAM_DIRS
    case "$?" in
        0) EXTERNAL_PROGRAM_STATE="位置設定を確認" ;;
        2) EXTERNAL_PROGRAM_STATE="位置設定形式不正" ;;
    esac
fi
export PORTA_CONFIG_STATE PORTA_USER_ROOT CONFIG_DIR STANDALONE_APPS_FILE EXTERNAL_PROGRAM_FILE

CORE_WARNING_COUNT=0
CORE_OPTIONAL_MISSING=()
CORE_LAUNCHER_TARGETS=("${STANDALONE_APPS_DIRS[@]}" "${EXTERNAL_PROGRAM_DIRS[@]}")
for optional_dir in "${CORE_LAUNCHER_TARGETS[@]}"; do
    if [ ! -d "$optional_dir" ]; then
        CORE_WARNING_COUNT=$((CORE_WARNING_COUNT + 1))
        CORE_OPTIONAL_MISSING+=("$optional_dir")
    fi
done
export CORE_WARNING_COUNT

normalize_input() {
    local val="$1"
    val=$(printf '%s' "$val" | tr -d '[:space:][:cntrl:]')
    val=$(printf '%s' "$val" | sed 'y/０１２３４５６７８９ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚ/0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz/')
    printf '%s\n' "${val,,}"
}

show_title() {
    command -v clear >/dev/null && clear
    printf '%b\n' "${C_CYAN}┌─────────────────────────────────────────────────────────┐${C_RESET}"
    printf '%b\n' "${C_CYAN}│${C_RESET}  ${C_BOLD}$1${C_RESET}"
    printf '%b\n\n' "${C_CYAN}└─────────────────────────────────────────────────────────┘${C_RESET}"
}

run_app() {
    local target="$1"; shift
    if [ ! -f "$target" ]; then _core_error "起動対象がありません: $target"; return 1; fi
    local app_dir; app_dir="$(dirname "$target")"
    printf '%b\n' "${C_GREEN}起動: $(basename "$app_dir")${C_RESET}"
    ( cd "$app_dir" && bash "./$(basename "$target")" "$@" )
}

open_in_file_manager() {
    local target="$1" opener=""
    if [ ! -d "$target" ]; then
        _core_error "開くフォルダがありません: $target"
        return 1
    fi
    for candidate in xdg-open nautilus dolphin thunar pcmanfm caja nemo; do
        if command -v "$candidate" >/dev/null 2>&1; then
            opener="$candidate"
            break
        fi
    done
    if [ -z "$opener" ]; then
        _core_error "利用可能なファイルマネージャーが見つかりません。"
        return 1
    fi
    printf '%b\n' "${C_CYAN}ファイルマネージャーで開く: $target${C_RESET}"
    "$opener" "$target" >/dev/null 2>&1 &
}

launch_main_application() {
    local python_bin="$PORTA_DIR/.venv/bin/python"
    local entrypoint="$PORTA_DIR/scripts/main.py"
    if [ ! -x "$python_bin" ] || [ ! -f "$entrypoint" ]; then
        _core_error "PORTA本体の起動環境が見つかりません: $PORTA_DIR"
        return 1
    fi
    printf '%b\n' "${C_GREEN}PORTAを仮想環境で切り離して起動します。${C_RESET}"
    printf '%b\n' "${C_GRAY}使用するPython: $python_bin${C_RESET}"
    # A GUI attached to this terminal can receive SIGHUP when the terminal
    # closes.  Start a fresh session and discard process output rather than
    # creating a persistent log.  The visible preflight happens beforehand.
    if command -v setsid >/dev/null 2>&1; then
        (
            cd "$PORTA_DIR" || exit 1
            exec setsid env PYTHONDONTWRITEBYTECODE=1 "$python_bin" scripts/main.py \
                </dev/null >/dev/null 2>&1
        ) &
    elif command -v nohup >/dev/null 2>&1; then
        (
            cd "$PORTA_DIR" || exit 1
            exec nohup env PYTHONDONTWRITEBYTECODE=1 "$python_bin" scripts/main.py \
                </dev/null >/dev/null 2>&1
        ) &
    else
        _core_error "setsid または nohup がないため、端末から安全に切り離せません。"
        return 1
    fi
    CORE_MAIN_APPLICATION_PID=$!
    export CORE_MAIN_APPLICATION_PID
    printf '%b\n' "${C_GREEN}PORTAの起動要求を送信しました（PID: $CORE_MAIN_APPLICATION_PID）。${C_RESET}"
}

show_main_application_detach_notice() {
    # Keep the shortcut terminal visible after a detached launch.  Enter is
    # an explicit opt-in to keep this terminal and open the fallback menu.
    local remaining ignored_input
    if [ ! -t 0 ]; then
        return 1
    fi
    for ((remaining=5; remaining>0; remaining--)); do
        printf '\rPORTAは端末から切り離して起動しています。PORTA Coreは %2d 秒後に終了します。EnterでCOREメニューを開く。' "$remaining"
        if read -r -t 1 ignored_input; then
            echo
            printf '%b\n' "${C_CYAN}COREメニューを開きます。PORTAはそのまま動作を続けます。${C_RESET}"
            return 0
        fi
    done
    echo
    return 1
}

preflight_main_application() {
    # Print a detailed, non-mutating launch check for the Python application.
    local python_bin="$PORTA_DIR/.venv/bin/python"
    local entrypoint="$PORTA_DIR/scripts/main.py"
    local diagnostic
    local failures=0

    printf '%b\n' "${C_CYAN}PORTA の起動確認${C_RESET}"
    printf 'PORTA Core: %s\n' "$CORE_DIR"
    printf 'PORTA本体候補: %s\n' "$PORTA_DIR"
    printf '仮想環境Python: %s\n' "$python_bin"
    printf '起動スクリプト: %s\n\n' "$entrypoint"

    if [ -f "$PORTA_DIR/pyproject.toml" ] && [ -d "$PORTA_DIR/src" ]; then
        printf '%b\n' "${C_GREEN}OK${C_RESET} 1/4 PORTA本体の構成を確認しました。"
    else
        printf '%b\n' "${C_RED}NG${C_RESET} 1/4 PORTA本体は同梱されていません。"
        failures=$((failures + 1))
    fi
    if [ -x "$python_bin" ]; then
        printf '%b\n' "${C_GREEN}OK${C_RESET} 2/4 仮想環境Pythonは実行可能です。"
    else
        printf '%b\n' "${C_RED}NG${C_RESET} 2/4 仮想環境Pythonがありません、または実行できません。"
        failures=$((failures + 1))
    fi
    if [ -f "$entrypoint" ]; then
        printf '%b\n' "${C_GREEN}OK${C_RESET} 3/4 起動スクリプトを確認しました。"
    else
        printf '%b\n' "${C_RED}NG${C_RESET} 3/4 起動スクリプトがありません。"
        failures=$((failures + 1))
    fi
    if [ "$failures" -eq 0 ]; then
        diagnostic=$(cd "$PORTA_DIR" && "$python_bin" -c \
            'import sys; sys.path.insert(0, "src"); import PySide6; from apps.launcher import MainMenuWindow' 2>&1)
        if [ "$?" -eq 0 ]; then
            printf '%b\n' "${C_GREEN}OK${C_RESET} 4/4 PySide6とPythonランチャーの読込みを確認しました。"
        else
            printf '%b\n' "${C_RED}NG${C_RESET} 4/4 Pythonランチャーを読込めません。"
            [ -n "$diagnostic" ] && printf '%s\n' "$diagnostic"
            failures=$((failures + 1))
        fi
    else
        printf '%b\n' "${C_YELLOW}未実行${C_RESET} 4/4 前段の確認に失敗したため、Python読込みは行いません。"
    fi

    if [ "$failures" -eq 0 ]; then
        printf '%b\n' "${C_GREEN}判定: PORTAを起動できます。${C_RESET}"
        return 0
    fi
    printf '%b\n' "${C_YELLOW}判定: PORTAは起動できません。PORTA Coreは単独で利用できます。${C_RESET}"
    return 1
}

show_workspace_status() {
    printf '%b\n' "${C_CYAN}現在の状態（読み取りのみ）${C_RESET}"
    printf 'PORTA Core: %s\n' "$CORE_DIR"
    if [ "$PORTA_AVAILABLE" -eq 1 ]; then
        printf 'PORTA本体: 接続済み（%s）\n' "$PORTA_DIR"
        printf 'メインアプリ: %s\n' "$PORTA_DIR/scripts/main.py"
    else
        printf 'PORTA本体: 未接続（CORE単体で利用可能）\n'
    fi
    printf '共有設定: %s\n' "$PORTA_CONFIG_STATE"
    [ -n "$CONFIG_DIR" ] && printf 'CONFIG: %s\n' "$CONFIG_DIR"
    printf '独立ツール: %s（%s 件）\n' "$STANDALONE_APPS_STATE" "${#STANDALONE_APPS_DIRS[@]}"
    printf '外部プログラム: %s（%s 件）\n' "$EXTERNAL_PROGRAM_STATE" "${#EXTERNAL_PROGRAM_DIRS[@]}"

    if command -v git >/dev/null 2>&1 && git -C "$CORE_DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
        local branch change_count git_root
        git_root=$(git -C "$CORE_DIR" rev-parse --show-toplevel 2>/dev/null || true)
        branch=$(git -C "$git_root" branch --show-current 2>/dev/null || true)
        change_count=$(git -C "$git_root" status --short 2>/dev/null | wc -l | tr -d ' ')
        printf 'Git: %s / 未コミット変更 %s 件\n' "${branch:-不明}" "${change_count:-不明}"
    else
        printf 'Git: 確認できません\n'
    fi

    if command -v findmnt >/dev/null 2>&1; then
        if findmnt -no SOURCE -T "$CORE_DIR" >/dev/null 2>&1; then
            printf '作業場所のマウント: 接続済み\n'
        else
            printf '作業場所のマウント: 確認できません\n'
        fi
    fi
    if [ "$CORE_WARNING_COUNT" -gt 0 ]; then
        printf '任意領域: %s 件が未接続（利用する機能だけが表示されません）\n' "$CORE_WARNING_COUNT"
    fi
}

core_self_check() {
    local failures=0 file path display_path
    printf '%b\n' "${C_CYAN}PORTA Core 自己診断（読み取りのみ）${C_RESET}"

    for required in bash find readlink; do
        if command -v "$required" >/dev/null 2>&1; then
            printf '%b\n' "${C_GREEN}OK${C_RESET} コマンド: $required"
        else
            printf '%b\n' "${C_RED}NG${C_RESET} 必須コマンドがありません: $required"
            failures=$((failures + 1))
        fi
    done

    for path in "$CORE_DIR" "$CORE_LIBS_DIR"; do
        display_path="${path#"$OPEN_SPACE_DIR"/}"
        [ "$display_path" = "$path" ] && display_path="$(basename "$path")"
        if [ -e "$path" ]; then
            printf '%b\n' "${C_GREEN}OK${C_RESET} パス: $display_path"
        else
            printf '%b\n' "${C_RED}NG${C_RESET} 見つかりません: $display_path"
            failures=$((failures + 1))
        fi
    done

    if [ "$PORTA_AVAILABLE" -eq 1 ]; then
        printf '%b\n' "${C_GREEN}OK${C_RESET} PORTA本体との任意連携"
    else
        printf '%b\n' "${C_YELLOW}情報${C_RESET} PORTA本体は未接続です（CORE単体利用には影響しません）"
    fi
    printf '%b\n' "${C_YELLOW}情報${C_RESET} 共有設定: $PORTA_CONFIG_STATE"
    printf '%b\n' "${C_YELLOW}情報${C_RESET} 独立ツール: $STANDALONE_APPS_STATE"
    printf '%b\n' "${C_YELLOW}情報${C_RESET} 外部プログラム: $EXTERNAL_PROGRAM_STATE"

    for path in "${CORE_OPTIONAL_MISSING[@]}"; do
        printf '%b\n' "${C_YELLOW}情報${C_RESET} 任意領域が未接続: $(basename "$path")"
    done

    while IFS= read -r -d '' file; do
        if bash -n "$file"; then
            printf '%b\n' "${C_GREEN}OK${C_RESET} 構文: ${file#$OPEN_SPACE_DIR/}"
        else
            printf '%b\n' "${C_RED}NG${C_RESET} 構文: ${file#$OPEN_SPACE_DIR/}"
            failures=$((failures + 1))
        fi
    done < <(find "$CORE_DIR" -type f -name '*.sh' -print0)

    if [ "$failures" -eq 0 ]; then
        printf '%b\n' "${C_GREEN}自己診断を完了しました。問題は見つかりませんでした。${C_RESET}"
    else
        printf '%b\n' "${C_RED}自己診断を完了しました。問題 %s 件を確認してください。${C_RESET}" "$failures"
        return 1
    fi
}

prompt_with_timeout() {
    local prompt_msg="$1" timeout_sec="${2:-60}" remaining
    REPLY_INPUT=""
    for ((remaining=timeout_sec; remaining>0; remaining--)); do
        printf '\r%s [%2d秒] ' "$prompt_msg" "$remaining"
        if read -r -t 1 REPLY_INPUT; then echo; return 0; fi
    done
    echo
    _core_warn "${timeout_sec}秒間入力がなかったため中止しました。"
    return 124
}
