"""
PORTA - Text & String Processing Module
汎用テキスト編集、行操作、フィルタリング、リネーム操作を提供する基盤モジュール
"""

import re
from pathlib import Path
from typing import Any, Callable, Sequence

# 自作基盤モジュールのインポート
from foundation.path import PathLike, normalize_path

# ----------------------------------------------------------------------
# 1. テキスト分割・クレンジング・フィルタリング
# ----------------------------------------------------------------------


def split_and_clean(
    data: str | Sequence[str],
    delimiter: str = "\n",
    strip: bool = True,
    exclude_empty: bool = True,
) -> list[str]:
    """
    文字列または文字列シーケンスを受け取り、前後の空白除去や空要素の除外を行ったリストを返す。
    """
    if isinstance(data, str):
        parts = data.split(delimiter)
    else:
        parts = [str(x) for x in data]

    if strip:
        parts = [p.strip() for p in parts]

    if exclude_empty:
        parts = [p for p in parts if p]

    return parts


def filter_lines(
    lines: list[str],
    *,
    include: str | None = None,
    exclude: str | None = None,
    include_regex: str | None = None,
    exclude_regex: str | None = None,
    remove_duplicates: bool = False,
) -> list[str]:
    """
    文字列リストに対して各種条件（含む/除外/正規表現/重複排除）のフィルタリングを一括適用する。
    """
    result = []
    seen = set()

    inc_re = re.compile(include_regex) if include_regex else None
    exc_re = re.compile(exclude_regex) if exclude_regex else None

    for line in lines:
        if include is not None and include not in line:
            continue
        if exclude is not None and exclude in line:
            continue
        if inc_re and not inc_re.search(line):
            continue
        if exc_re and exc_re.search(line):
            continue
        if remove_duplicates:
            if line in seen:
                continue
            seen.add(line)

        result.append(line)

    return result


# ----------------------------------------------------------------------
# 2. 行単位のテキスト操作（挿入・削除・切り出し）
# ----------------------------------------------------------------------


def apply_line_op(
    content: str | list[str],
    op_fn: Callable[[str], str],
    target_line: int | None = None,
) -> str | list[str]:
    """
    指定した行（target_line=None の場合は全行）に対して関数 op_fn を適用する。
    """
    is_str_input = isinstance(content, str)
    lines = content.splitlines(keepends=True) if is_str_input else list(content)

    if target_line is None:
        new_lines = [op_fn(line) for line in lines]
    else:
        if not (0 <= target_line < len(lines)):
            raise IndexError(
                f"指定された行番号が範囲外です: {target_line} (総行数: {len(lines)})"
            )
        lines[target_line] = op_fn(lines[target_line])
        new_lines = lines

    return "".join(new_lines) if is_str_input else new_lines


def insert_text(
    text: str | list[str],
    line: int,
    pos: int,
    content: str,
) -> str | list[str]:
    """指定行の特定文字位置に文字列を挿入する"""

    def _op(line: str) -> str:
        raw = line.rstrip("\r\n")
        nl = line[len(raw) :]
        p = min(max(0, pos), len(raw))
        return raw[:p] + content + raw[p:] + nl

    return apply_line_op(text, _op, target_line=line)


def delete_text(
    text: str | list[str],
    line: int,
    start_pos: int,
    end_pos: int,
) -> str | list[str]:
    """指定行の特定範囲を削除する"""

    def _op(line: str) -> str:
        raw = line.rstrip("\r\n")
        nl = line[len(raw) :]
        return raw[:start_pos] + raw[end_pos:] + nl

    return apply_line_op(text, _op, target_line=line)


def trim_text(
    text: str | list[str],
    line: int,
    start_pos: int,
    end_pos: int,
) -> str:
    """指定行の特定範囲を抽出する"""
    lines = text.splitlines() if isinstance(text, str) else text

    if not (0 <= line < len(lines)):
        raise IndexError(f"指定された行番号が範囲外です: {line}")

    raw_line = lines[line].rstrip("\r\n")
    return raw_line[start_pos:end_pos]


# ----------------------------------------------------------------------
# 3. 数値抽出・欠番検索
# ----------------------------------------------------------------------


def extract_integers(data: Sequence[Any]) -> list[int]:
    """データリストから整数（負数含む）に変換可能な要素を正しく抽出する"""
    result = []
    int_pattern = re.compile(r"^-?\d+$")
    for item in data:
        s = str(item).strip()
        if int_pattern.match(s):
            result.append(int(s))
    return result


def find_missing_numbers(data: Sequence[Any]) -> list[int]:
    """
    リスト内の最小値〜最大値の範囲の中から、連番として欠落している整数を抽出する。
    """
    nums = extract_integers(data)
    if not nums:
        return []

    complete_set = set(range(min(nums), max(nums) + 1))
    return sorted(complete_set - set(nums))


# ----------------------------------------------------------------------
# 4. ファイル名リネームユーティリティ
# ----------------------------------------------------------------------


def rename_filename(
    filename: str,
    operations: list[dict[str, Any]],
    include_ext: bool = False,
) -> str:
    """
    一連の操作ルール（insert, delete, trim, replace）に従ってファイル名を変換する。
    """
    p = Path(filename)
    if not include_ext and p.suffix:
        base_name = p.stem
        ext = p.suffix
    else:
        base_name = filename
        ext = ""

    curr = base_name
    for op in operations:
        op_type = op.get("type")
        if op_type == "insert":
            pos = op.get("pos", 0)
            content = op.get("content", "")
            curr = curr[:pos] + content + curr[pos:]
        elif op_type == "delete":
            start = op.get("start", 0)
            end = op.get("end", 0)
            curr = curr[:start] + curr[end:]
        elif op_type == "trim":
            start = op.get("start", 0)
            end = op.get("end", len(curr))
            curr = curr[start:end]
        elif op_type == "replace":
            old = op.get("old", "")
            new = op.get("new", "")
            count = op.get("count", -1)
            curr = (
                curr.replace(old, new, count) if count > 0 else curr.replace(old, new)
            )
        else:
            raise ValueError(f"未サポートのリネーム操作タイプです: {op_type}")

    return curr + ext


def batch_rename_filenames(
    filenames: list[str],
    operations: list[dict[str, Any]],
    include_ext: bool = False,
) -> list[str]:
    """複数のファイル名に対して一括でリネーム変換を適用する"""
    results = []
    for fname in filenames:
        try:
            results.append(rename_filename(fname, operations, include_ext))
        except Exception:
            results.append(fname)
    return results


def insert_parent_folder_name(
    filepath: PathLike,
    target_line: int,
    pos: int,
    lines: list[str],
    levels_up: int = 0,
) -> list[str]:
    """
    指定ファイルの親フォルダ名をテキストの指定行・指定位置に安全に挿入する。
    """
    p = normalize_path(filepath)
    target_dir = p.parents[levels_up] if len(p.parents) > levels_up else p.parent
    folder_name = target_dir.name

    def _op(line: str) -> str:
        raw = line.rstrip("\r\n")
        nl = line[len(raw) :]
        p_idx = min(max(0, pos), len(raw))
        return raw[:p_idx] + folder_name + raw[p_idx:] + nl

    res = apply_line_op(lines, _op, target_line=target_line)
    return res if isinstance(res, list) else res.splitlines(keepends=True)
