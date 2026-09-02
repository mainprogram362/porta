#!/usr/bin/env bash
# LUKSコンテナ／LUKSデバイスを明示的に開閉する独立ツール。
# パスフレーズ、鍵、履歴、実行ログは保存しない。

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)" || exit 1
STANDALONE_APPS_DIR="$(cd "$SCRIPT_DIR/.." && pwd -P)" || exit 1
COMMON_LIB="$STANDALONE_APPS_DIR/libs/common.sh"

if [ ! -f "$COMMON_LIB" ]; then
    echo "エラー: 独立ツールの共通ライブラリが見つかりません。"
    exit 1
fi
source "$COMMON_LIB"

# Private presets intentionally live outside the public repository.  Each
# preset may use absolute paths, or paths relative to that preset file.
PRESET_DIR="$PRIVATE_SPACE_DIR/core_presets/luks_manager"

CURRENT_NAME=""
CURRENT_ORIGIN=""
CURRENT_SOURCE=""
CURRENT_MOUNT_POINT=""
CURRENT_MAPPING_NAME=""

error() {
    printf '%b\n' "${C_RED}エラー: $1${C_RESET}" >&2
}

info() {
    printf '%b\n' "${C_CYAN}$1${C_RESET}"
}

require_commands() {
    local command_name
    for command_name in cryptsetup findmnt mount umount lsblk sudo readlink; do
        if ! command -v "$command_name" >/dev/null 2>&1; then
            error "必要なコマンドが見つかりません: $command_name"
            return 1
        fi
    done
}

is_mapping_name() {
    [[ "$1" =~ ^[A-Za-z0-9_.+-]+$ ]]
}

resolve_path() {
    # Resolve an absolute path as-is, and any relative path against an
    # explicit caller-supplied base.  -m intentionally works before targets
    # exist so the UI can explain a missing path without guessing elsewhere.
    local base="$1" value="$2" candidate
    [ -n "$value" ] || return 1
    case "$value" in
        /*) candidate="$value" ;;
        *) candidate="$base/$value" ;;
    esac
    readlink -m -- "$candidate" 2>/dev/null
}

suggest_mapping_name() {
    local source="$1" base
    base="$(basename "$source")"
    base="$(printf '%s' "$base" | tr -c 'A-Za-z0-9_.+-' '_')"
    [ -n "$base" ] || base="container"
    printf 'luks_%s\n' "$base"
}

clear_selection() {
    CURRENT_NAME=""
    CURRENT_ORIGIN=""
    CURRENT_SOURCE=""
    CURRENT_MOUNT_POINT=""
    CURRENT_MAPPING_NAME=""
}

has_selection() {
    [ -n "$CURRENT_SOURCE" ] && [ -n "$CURRENT_MOUNT_POINT" ] && [ -n "$CURRENT_MAPPING_NAME" ]
}

show_selection() {
    if ! has_selection; then
        echo "現在の対象: 未選択"
        return
    fi
    echo "現在の対象: $CURRENT_NAME"
    echo "選択方法: $CURRENT_ORIGIN"
    echo "LUKS: $CURRENT_SOURCE"
    echo "マウント先: $CURRENT_MOUNT_POINT"
    echo "解除名: $CURRENT_MAPPING_NAME"
}

set_selection() {
    local name="$1" origin="$2" source="$3" mount_point="$4" mapping_name="$5"
    if ! is_mapping_name "$mapping_name"; then
        error "解除名は英数字、_、.、+、- だけで指定してください。"
        return 1
    fi
    CURRENT_NAME="$name"
    CURRENT_ORIGIN="$origin"
    CURRENT_SOURCE="$source"
    CURRENT_MOUNT_POINT="$mount_point"
    CURRENT_MAPPING_NAME="$mapping_name"
    info "対象を設定しました。"
    show_selection
}

prompt_mount_and_mapping() {
    local source="$1" name="$2" origin="$3" base="$4" default_mapping mount_input mount_path mapping_input
    default_mapping="$(suggest_mapping_name "$source")"
    echo "マウント先は絶対パス、またはこの入力アプリを基準にした相対パスで指定できます。"
    prompt_with_timeout "マウント先を入力してください : " 120 || return 1
    mount_input="$REPLY_INPUT"
    mount_path="$(resolve_path "$base" "$mount_input")" || {
        error "マウント先のパスを解釈できません。"
        return 1
    }
    printf '解除名の既定値: %s\n' "$default_mapping"
    prompt_with_timeout "解除名（空欄で既定値）: " 120 || return 1
    mapping_input="$REPLY_INPUT"
    [ -n "$mapping_input" ] || mapping_input="$default_mapping"
    set_selection "$name" "$origin" "$source" "$mount_path" "$mapping_input"
}

select_manual_paths() {
    local source_input source_path
    show_title "手動でLUKSとマウント先を入力"
    echo "LUKSはファイルコンテナまたは /dev/... のブロックデバイスを指定できます。"
    echo "相対パスはこのアプリのフォルダを基準に解決します: $SCRIPT_DIR"
    prompt_with_timeout "LUKSコンテナ／デバイスのパス: " 120 || return 1
    source_input="$REPLY_INPUT"
    source_path="$(resolve_path "$SCRIPT_DIR" "$source_input")" || {
        error "LUKSのパスを解釈できません。"
        return 1
    }
    prompt_mount_and_mapping "$source_path" "手動入力" "手動入力" "$SCRIPT_DIR"
}

load_preset() {
    local preset="$1" line key value source_input mount_input mapping_input name=""
    local preset_base source mount_point
    declare -A values=()
    declare -A seen=()

    [ -f "$preset" ] || { error "プリセットが見つかりません: $preset"; return 1; }
    while IFS= read -r line || [ -n "$line" ]; do
        case "$line" in
            ''|'#'*) continue ;;
            *=*) ;;
            *) error "プリセットの形式が不正です: $preset"; return 1 ;;
        esac
        key="${line%%=*}"
        value="${line#*=}"
        case "$key" in
            name|container_path|mount_point|mapping_name) ;;
            *) error "許可されていないプリセット項目です: $key"; return 1 ;;
        esac
        [ -z "${seen[$key]:-}" ] || { error "プリセットの項目が重複しています: $key"; return 1; }
        seen[$key]=1
        values[$key]="$value"
    done < "$preset"

    name="${values[name]:-}"
    source_input="${values[container_path]:-}"
    mount_input="${values[mount_point]:-}"
    mapping_input="${values[mapping_name]:-}"
    if [ -z "$name" ] || [ -z "$source_input" ] || [ -z "$mount_input" ]; then
        error "プリセットには name、container_path、mount_point が必要です。"
        return 1
    fi
    preset_base="$(dirname "$preset")"
    source="$(resolve_path "$preset_base" "$source_input")" || {
        error "プリセットの container_path を解釈できません。"
        return 1
    }
    mount_point="$(resolve_path "$preset_base" "$mount_input")" || {
        error "プリセットの mount_point を解釈できません。"
        return 1
    }
    [ -n "$mapping_input" ] || mapping_input="$(suggest_mapping_name "$source")"
    set_selection "$name" "プリセット: $(basename "$preset")" "$source" "$mount_point" "$mapping_input"
}

select_preset() {
    local -a presets=()
    local preset choice index=1
    show_title "LUKSプリセットを選択"
    if [ ! -d "$PRESET_DIR" ]; then
        error "プリセット置き場がありません: $PRESET_DIR"
        echo "必要ならこのフォルダに *.preset を明示作成してください。"
        return 1
    fi
    mapfile -d '' presets < <(find "$PRESET_DIR" -maxdepth 1 -type f -name '*.preset' -print0 2>/dev/null | sort -z)
    if [ "${#presets[@]}" -eq 0 ]; then
        error "利用可能なプリセットがありません: $PRESET_DIR"
        return 1
    fi
    for preset in "${presets[@]}"; do
        printf '  %d) %s\n' "$index" "$(basename "$preset" .preset)"
        index=$((index + 1))
    done
    echo "  0) 戻る"
    prompt_with_timeout "番号を入力してください : " 120 || return 1
    choice="$(normalize_input "$REPLY_INPUT")"
    [ "$choice" = "0" ] && return 0
    [[ "$choice" =~ ^[0-9]+$ ]] || { error "無効な番号です。"; return 1; }
    preset="${presets[$((choice - 1))]:-}"
    [ -n "$preset" ] || { error "無効な番号です。"; return 1; }
    load_preset "$preset"
}

select_detected_luks() {
    local -a devices=()
    local device choice index=1
    show_title "接続済みLUKSデバイスを自動検出"
    mapfile -t devices < <(lsblk -rpno PATH,FSTYPE 2>/dev/null | awk '$2 == "crypto_LUKS" {print $1}')
    if [ "${#devices[@]}" -eq 0 ]; then
        error "接続済みブロックデバイスの中に crypto_LUKS は見つかりませんでした。"
        echo "ファイル型コンテナは手動入力またはプリセットで指定してください。"
        return 1
    fi
    for device in "${devices[@]}"; do
        printf '  %d) %s\n' "$index" "$device"
        index=$((index + 1))
    done
    echo "  0) 戻る"
    prompt_with_timeout "番号を入力してください : " 120 || return 1
    choice="$(normalize_input "$REPLY_INPUT")"
    [ "$choice" = "0" ] && return 0
    [[ "$choice" =~ ^[0-9]+$ ]] || { error "無効な番号です。"; return 1; }
    device="${devices[$((choice - 1))]:-}"
    [ -n "$device" ] || { error "無効な番号です。"; return 1; }
    prompt_mount_and_mapping "$(readlink -f "$device")" "自動検出: $(basename "$device")" "自動検出" "$SCRIPT_DIR"
}

validate_selected_paths() {
    has_selection || { error "先に手動入力・プリセット・自動検出で対象を選んでください。"; return 1; }
    if [ ! -f "$CURRENT_SOURCE" ] && [ ! -b "$CURRENT_SOURCE" ]; then
        error "LUKSの指定先が通常ファイルでもブロックデバイスでもありません: $CURRENT_SOURCE"
        return 1
    fi
    if [ ! -d "$CURRENT_MOUNT_POINT" ]; then
        error "マウント先フォルダがありません: $CURRENT_MOUNT_POINT"
        return 1
    fi
}

mapping_is_active() {
    [ -e "/dev/mapper/$CURRENT_MAPPING_NAME" ]
}

mount_source() {
    findmnt -rn -M "$CURRENT_MOUNT_POINT" -o SOURCE 2>/dev/null || true
}

mount_matches_selection() {
    local source map_real source_real
    source="$(mount_source)"
    [ -n "$source" ] || return 1
    map_real="$(readlink -f "/dev/mapper/$CURRENT_MAPPING_NAME" 2>/dev/null)" || return 1
    source_real="$(readlink -f "$source" 2>/dev/null)" || return 1
    [ "$source_real" = "$map_real" ]
}

verify_active_mapping() {
    local status device expected actual backing
    status="$(sudo cryptsetup status "$CURRENT_MAPPING_NAME" 2>/dev/null)" || {
        error "既存の解除名を確認できません: $CURRENT_MAPPING_NAME"
        return 1
    }
    printf '%s\n' "$status" | grep -q 'is active' || {
        error "解除名は有効なLUKSマッピングではありません: $CURRENT_MAPPING_NAME"
        return 1
    }
    device="$(printf '%s\n' "$status" | awk '/^[[:space:]]*device:/ {print $2; exit}')"
    [ -n "$device" ] && [ "$device" != "(null)" ] || {
        error "既存マッピングの接続元を安全に確認できません。何もしません。"
        return 1
    }
    expected="$(readlink -f "$CURRENT_SOURCE" 2>/dev/null)" || return 1
    if [ -f "$CURRENT_SOURCE" ]; then
        case "$device" in
            /dev/loop*) ;;
            *) error "ファイル型コンテナの既存マッピングがループデバイスではありません。何もしません。"; return 1 ;;
        esac
        backing="$(sudo losetup --noheadings --output BACK-FILE "$device" 2>/dev/null | head -n 1)"
        [ -n "$backing" ] || { error "既存ループデバイスの元ファイルを確認できません。"; return 1; }
        actual="$(readlink -f "$backing" 2>/dev/null)" || return 1
    else
        actual="$(readlink -f "$device" 2>/dev/null)" || return 1
    fi
    if [ "$actual" != "$expected" ]; then
        error "同じ解除名が別のLUKSに使われています。何もしません。"
        return 1
    fi
}

mount_point_is_empty() {
    local entries
    entries="$(find "$CURRENT_MOUNT_POINT" -mindepth 1 -maxdepth 1 -print -quit 2>/dev/null)" || {
        error "マウント先フォルダの中身を確認できません。"
        return 1
    }
    [ -z "$entries" ]
}

open_and_mount() {
    local opened_here=0
    require_commands || return 1
    validate_selected_paths || return 1
    if [ -n "$(mount_source)" ]; then
        if mount_matches_selection; then
            info "既に指定先へマウントされています。"
            return 0
        fi
        error "マウント先は別のファイルシステムで使用中です。何もしません。"
        return 1
    fi
    mount_point_is_empty || {
        error "マウント先フォルダが空ではありません。隠れるファイルを防ぐため中止しました。"
        return 1
    }
    if ! sudo cryptsetup isLuks "$CURRENT_SOURCE" >/dev/null 2>&1; then
        error "指定先はLUKSとして確認できません: $CURRENT_SOURCE"
        return 1
    fi
    if mapping_is_active; then
        verify_active_mapping || return 1
        info "LUKSは既に開いています。"
    else
        info "LUKSを開きます。sudo のパスワード入力は端末だけで扱われ、保存されません。"
        sudo cryptsetup open "$CURRENT_SOURCE" "$CURRENT_MAPPING_NAME" || return 1
        opened_here=1
    fi
    if ! sudo mount "/dev/mapper/$CURRENT_MAPPING_NAME" "$CURRENT_MOUNT_POINT"; then
        error "マウントに失敗しました。"
        if [ "$opened_here" -eq 1 ]; then
            info "今回開いたLUKSを閉じて元の状態へ戻します。"
            sudo cryptsetup close "$CURRENT_MAPPING_NAME" || error "自動で閉じられませんでした。状態確認してください。"
        fi
        return 1
    fi
    info "マウントしました: $CURRENT_MOUNT_POINT"
}

show_status() {
    require_commands || return 1
    validate_selected_paths || return 1
    show_selection
    if mapping_is_active; then
        if verify_active_mapping; then
            echo "LUKS: 開いています（選択中のLUKSと一致）"
        else
            echo "LUKS: 開いていますが、安全に一致確認できません"
        fi
    else
        echo "LUKS: 閉じています"
    fi
    if [ -n "$(mount_source)" ]; then
        if mount_matches_selection; then
            echo "マウント: 指定先に接続済み"
        else
            echo "マウント: 指定先は別のファイルシステムで使用中"
        fi
    else
        echo "マウント: 接続していません"
    fi
}

unmount_and_close() {
    require_commands || return 1
    validate_selected_paths || return 1
    if [ -n "$(mount_source)" ]; then
        if ! mount_matches_selection; then
            error "指定先は別のファイルシステムで使用中です。何もしません。"
            return 1
        fi
        info "アンマウントします。"
        sudo umount "$CURRENT_MOUNT_POINT" || {
            error "使用中などの理由でアンマウントできません。LUKSは閉じません。"
            return 1
        }
    fi
    if mapping_is_active; then
        verify_active_mapping || return 1
        info "LUKSを閉じます。"
        sudo cryptsetup close "$CURRENT_MAPPING_NAME" || return 1
        info "LUKSを閉じました。"
    else
        info "LUKSは既に閉じています。"
    fi
}

pause_after_action() {
    echo
    prompt_with_timeout "エンターキーを押して続けます..." 60 || true
}

while true; do
    show_title "LUKSコンテナ管理"
    echo "プリセット置き場: $PRESET_DIR"
    show_selection
    echo
    echo "  1) 手動でLUKSとマウント先を入力"
    echo "  2) プリセットから入力"
    echo "  3) 接続済みLUKSデバイスを自動検出"
    echo "  4) 状態を確認"
    echo "  5) 開く・マウントする"
    echo "  6) アンマウントして閉じる"
    echo "  0) 終了"
    prompt_with_timeout "番号を入力してください : " 120
    status=$?
    [ "$status" -eq 124 ] && exit 0
    choice="$(normalize_input "$REPLY_INPUT")"
    case "$choice" in
        1) select_manual_paths; pause_after_action ;;
        2) select_preset; pause_after_action ;;
        3) select_detected_luks; pause_after_action ;;
        4) show_status; pause_after_action ;;
        5) open_and_mount; pause_after_action ;;
        6) unmount_and_close; pause_after_action ;;
        0) exit 0 ;;
        *) error "無効な番号です。"; pause_after_action ;;
    esac
done
