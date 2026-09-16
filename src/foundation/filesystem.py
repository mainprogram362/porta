import os
import errno
import shutil
from pathlib import Path
from typing import Literal

import chardet

from .path import PathLike, normalize_path, path_entry_exists
from .safe_transfer import copy_noreplace, rename_noreplace


def ensure_unique_destination(dest: PathLike) -> Path:
    """Return a path that does not collide with an existing file or directory."""
    target = normalize_path(dest)
    if not path_entry_exists(target):
        return target

    parent = target.parent
    stem = target.stem
    suffix = target.suffix
    counter = 1
    while True:
        candidate = parent / f"{stem} ({counter}){suffix}"
        if not path_entry_exists(candidate):
            return candidate
        counter += 1


def detect_encoding(path: PathLike, sample_size: int = 10240) -> str:
    """
    ファイルの文字コードを自動検出（先頭 sample_size バイトのみ参照して高速化）
    """
    p = normalize_path(path)
    if not p.is_file():
        raise FileNotFoundError(f"ファイルが存在しません: {p}")

    with open(p, "rb") as f:
        raw_data = f.read(sample_size)

    result = chardet.detect(raw_data)
    return result["encoding"] or "utf-8"


def read_lines(path: PathLike, encoding: str | None = None) -> list[str]:
    """
    テキストファイルを読み込み、行のリストとして返す
    encoding が None の場合は自動検出
    """
    p = normalize_path(path)
    if not p.is_file():
        raise FileNotFoundError(f"ファイルが存在しません: {p}")

    enc = encoding or detect_encoding(p)
    try:
        with open(p, "r", encoding=enc, errors="replace") as f:
            return f.read().splitlines()
    except Exception as e:
        raise IOError(f"ファイル読み込みエラー ({p}): {e}")


def list_items(
    directory: PathLike,
    search_type: Literal["all", "file", "folder"] = "all",
    include: str | list[str] | None = None,
    exclude: str | list[str] | None = None,
    sort: Literal["name_asc", "name_desc", "ctime_asc", "ctime_desc"] | None = None,
    limit: int | None = None,
) -> list[Path]:
    """
    指定ディレクトリ内のファイル/フォルダを検索して Path のリストで返す
    """
    target_dir = normalize_path(directory)
    if not target_dir.is_dir():
        raise NotADirectoryError(f"ディレクトリが存在しません: {target_dir}")

    # include / exclude のリスト化
    includes = [include] if isinstance(include, str) else (include or [])
    excludes = [exclude] if isinstance(exclude, str) else (exclude or [])

    items: list[Path] = []
    for entry in target_dir.iterdir():
        # タイプ判定
        if search_type == "file" and not entry.is_file():
            continue
        if search_type == "folder" and not entry.is_dir():
            continue

        name = entry.name
        # 包含・除外フィルター
        if includes and not any(inc in name for inc in includes):
            continue
        if excludes and any(exc in name for exc in excludes):
            continue

        items.append(entry)

    # ソート処理
    if sort:
        reverse = sort.endswith("_desc")
        if sort.startswith("name"):
            items.sort(key=lambda x: x.name, reverse=reverse)
        elif sort.startswith("ctime"):
            items.sort(key=lambda x: x.stat().st_ctime, reverse=reverse)

    # 件数制限
    if limit and limit > 0:
        items = items[:limit]

    return items


def copy_or_move(
    src: PathLike,
    dest: PathLike,
    mode: Literal["move", "copy"] = "move",
    overwrite: bool = False,
    create_dirs: bool = True,
    collision_mode: Literal["unique", "error"] = "unique",
) -> Path:
    """
    ファイルまたはフォルダを安全に移動・コピーする
    同名が存在し overwrite=False の場合は "(1)", "(2)" を自動付与
    """
    src_path = normalize_path(src)
    if not path_entry_exists(src_path):
        raise FileNotFoundError(f"転送元が存在しません: {src_path}")

    dest_path = normalize_path(dest)

    # 移動先が既存のディレクトリなら、その配下に元ファイル名で配置
    if collision_mode != "error" and dest_path.is_dir():
        dest_path = dest_path / src_path.name

    # 親ディレクトリの自動作成
    if create_dirs:
        dest_path.parent.mkdir(parents=True, exist_ok=True)

    # 上書きなしの場合のリネーム処理 (同名回避)
    if path_entry_exists(dest_path) and not overwrite:
        if collision_mode == "error":
            raise FileExistsError(f"コピー先がすでに存在します: {dest_path}")
        dest_path = ensure_unique_destination(dest_path)

    # Reserve/publish atomically, including a competitor arriving after the
    # existence check above. Never let shutil overwrite in this mode.
    if not overwrite:
        while True:
            try:
                if mode == "copy":
                    return copy_noreplace(src_path, dest_path)
                if mode != "move":
                    raise ValueError(f"無効なモードです: {mode}")
                try:
                    rename_noreplace(src_path, dest_path)
                except OSError as exc:
                    if exc.errno != errno.EXDEV:
                        raise
                    copy_noreplace(src_path, dest_path)
                    if src_path.is_symlink() or src_path.is_file():
                        src_path.unlink()
                    else:
                        shutil.rmtree(src_path)
                return dest_path
            except FileExistsError:
                if collision_mode == "error":
                    raise
                dest_path = ensure_unique_destination(dest_path)

    # Explicit overwrite remains a separate caller-selected operation.
    if mode == "move":
        if src_path.is_symlink():
            # ``shutil.move`` may dereference a directory symlink on a
            # cross-device move.  Move the link entry itself instead.
            os.symlink(os.readlink(src_path), dest_path, target_is_directory=src_path.is_dir())
            src_path.unlink()
            res = str(dest_path)
        else:
            res = shutil.move(src_path, dest_path)
    elif mode == "copy":
        if src_path.is_symlink():
            # Preserve the link rather than silently copying its target.
            res = shutil.copy2(src_path, dest_path, follow_symlinks=False)
        elif src_path.is_dir():
            # Nested links are entries in the copied tree, never traversal
            # roots into an unrelated location.
            res = shutil.copytree(src_path, dest_path, symlinks=True)
        else:
            res = shutil.copy2(src_path, dest_path)
    else:
        raise ValueError(f"無効なモードです: {mode}")

    return Path(res)


def move_to_garbage(path: PathLike, garbage_dir: PathLike) -> Path:
    """
    ファイルを呼び出し側が明示した退避先へ移動する。

    PORTAはホーム直下などに独自のゴミ箱を暗黙作成しない。通常のGUIでは
    ``gio trash`` を用いるため、この関数は明示的な退避先が必要な処理専用。
    """
    src_path = normalize_path(path)
    if not src_path.exists():
        raise FileNotFoundError(f"対象が存在しません: {src_path}")
    target_garbage = normalize_path(garbage_dir)

    return copy_or_move(
        src=src_path,
        dest=target_garbage,
        mode="move",
        overwrite=False,
        create_dirs=True,
    )
