#!/usr/bin/env bash
# Shared terminal helpers for PORTA standalone tools. No Python or CORE required.

[ -n "${_PORTA_STANDALONE_COMMON_LOADED:-}" ] && return 0
_PORTA_STANDALONE_COMMON_LOADED=1

C_RESET='\033[0m'; C_BOLD='\033[1m'; C_RED='\033[31m'; C_GREEN='\033[32m'
C_YELLOW='\033[33m'; C_CYAN='\033[36m'; C_BLUE='\033[34m'; C_GRAY='\033[90m'

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)" || return 1
export STANDALONE_APPS_DIR="$(cd "$SELF_DIR/.." && pwd -P)" || return 1
export PORTA_DIR="$(cd "$STANDALONE_APPS_DIR/.." && pwd -P)" || return 1
export OPEN_SPACE_DIR="$(cd "$PORTA_DIR/.." && pwd -P)" || return 1
export PORTABLE_WORKSPACE_DIR="$(cd "$OPEN_SPACE_DIR/.." && pwd -P)" || return 1
export PRIVATE_SPACE_DIR="$PORTABLE_WORKSPACE_DIR/private_space"

normalize_input() {
    local value="$1"
    value=$(printf '%s' "$value" | tr -d '[:space:][:cntrl:]')
    value=$(printf '%s' "$value" | sed 'y/０１２３４５６７８９ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚ/0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz/')
    printf '%s\n' "${value,,}"
}

show_title() {
    command -v clear >/dev/null && clear
    printf '%b\n' "${C_CYAN}┌─────────────────────────────────────────────────────────┐${C_RESET}"
    printf '%b\n' "${C_CYAN}│${C_RESET}  ${C_BOLD}$1${C_RESET}"
    printf '%b\n\n' "${C_CYAN}└─────────────────────────────────────────────────────────┘${C_RESET}"
}

prompt_with_timeout() {
    local prompt_msg="$1" timeout_sec="${2:-60}" remaining
    REPLY_INPUT=""
    for ((remaining=timeout_sec; remaining>0; remaining--)); do
        printf '\r%s [%2d秒] ' "$prompt_msg" "$remaining"
        if read -r -t 1 REPLY_INPUT; then echo; return 0; fi
    done
    echo
    printf '%b\n' "${C_YELLOW}${timeout_sec}秒間入力がなかったため中止しました。${C_RESET}" >&2
    return 124
}
