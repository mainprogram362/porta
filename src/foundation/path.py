import os
from pathlib import Path

# 型定義: str と Path の両方をスマートに受け入れる
PathLike = str | Path


def clean_path(path: PathLike) -> Path:
    """Make a path without changing any character in its name.

    POSIX permits leading and trailing whitespace (and quote characters) in a
    file name.  A path helper is used at the filesystem boundary, so it must
    not treat those characters as input decoration.  UI fields that want to
    trim a free-form search word must do so before calling this function.
    """
    return Path(os.fspath(path)).expanduser()


def path_text_from_input(value: str) -> str:
    """Keep literal whitespace, except for an unambiguous pasted-path wrapper.

    A pasted `` /path/file `` has historically been accepted as ``/path/file``.
    Retain that convenience only when the literal form does *not* exist and
    the trimmed form already does.  Thus an existing ``file `` is never
    silently changed into ``file``.
    """
    trimmed = value.strip()
    if value != trimmed and trimmed and not path_entry_exists(value) and path_entry_exists(trimmed):
        return trimmed
    return value


def absolute_path(path: PathLike) -> Path:
    """Return an absolute lexical path without following symbolic links.

    This expands ``~`` and removes ordinary ``.`` / ``..`` components, but
    deliberately keeps every symbolic-link component exactly as entered.  It
    is therefore the safe default for UI lists and filesystem operations that
    must act on the link itself rather than its target.
    """
    return Path(os.path.abspath(os.fspath(clean_path(path))))


def normalize_path(path: PathLike) -> Path:
    """Return an absolute path while preserving symbolic-link identity.

    Kept as the project's compatibility entry point.  Use ``Path.resolve()``
    only at a call site that explicitly needs the final target for inspection.
    """
    return absolute_path(path)


def path_entry_exists(path: PathLike) -> bool:
    """Return whether a directory entry exists, including a broken symlink."""
    return os.path.lexists(os.fspath(absolute_path(path)))


def get_project_root() -> Path:
    """プロジェクトのルートディレクトリを取得"""
    return Path(__file__).resolve().parent.parent.parent


def to_posix(path: PathLike) -> str:
    """POSIX 形式 ('/') の文字列に変換"""
    return clean_path(path).as_posix()


def to_windows(path: PathLike, ensure_trailing_sep: bool = False) -> str:
    """Windows 形式 ('\\') の文字列に変換"""
    p = clean_path(path)
    s = str(p).replace("/", "\\")
    if ensure_trailing_sep and not s.endswith("\\"):
        s += "\\"
    return s


def get_directory(path: PathLike, is_file: bool = True) -> Path:
    """ディレクトリを取得 (is_file=True なら親フォルダを返す)"""
    p = clean_path(path)
    return p.parent if is_file else p


def get_filename(path: PathLike) -> str:
    """末尾のファイル名（またはフォルダ名）を純粋に取得"""
    return clean_path(path).name


def get_ancestor(
    path: PathLike, levels_up: int = 1, return_name_only: bool = False
) -> Path | str:
    """
    指定した階層だけ親に遡る
    - levels_up=1: 親フォルダ
    - levels_up=2: 親の親フォルダ
    """
    if levels_up < 0:
        raise ValueError("levels_up は 0 以上を指定してください")

    p = clean_path(path)
    for _ in range(levels_up):
        p = p.parent

    return p.name if return_name_only else p


def join_paths(base_path: PathLike, *sub_paths: PathLike) -> Path:
    """絶対パス上書きバグを起こさずに複数パスを安全に結合"""
    res = clean_path(base_path)
    for sub in sub_paths:
        # 先頭の斜線を剥がして相対パス化して結合
        sub_str = os.fspath(sub).lstrip("/\\")
        res = res / sub_str
    return res


# =====================================================================
# 動作確認・テスト用コード
# =====================================================================
if __name__ == "__main__":
    print("=== path.py テスト開始 ===")

    # 1. パス正規化 & 引用符除去のテスト
    raw_path = '  "~/documents/../downloads/test_file.txt"  '
    norm = normalize_path(raw_path)
    print(f"[Norm] 元データ: {raw_path}")
    print(f"[Norm] 変換後  : {norm} (型: {type(norm)})")

    # 2. ファイル名取得のテスト（実在しなくても取れること）
    filename = get_filename("C:/folder\\subfolder/sample.mp4")
    print(f"[Filename] 'sample.mp4' が取れるか: {filename}")

    # 3. POSIX / Windows 形式変換のテスト
    posix_str = to_posix(norm)
    win_str = to_windows(norm, ensure_trailing_sep=True)
    print(f"[Format] POSIX形式: {posix_str}")
    print(f"[Format] Windows形式(末尾セパレータ付き): {win_str}")

    # 4. パス結合のテスト (絶対パス上書きバグが起きないこと)
    base = "/home/user/projects"
    sub1 = "src"
    sub2 = "/foundation/path.py"  # スラッシュ混入
    joined = join_paths(base, sub1, sub2)
    print(f"[Join] 結合結果: {joined}")

    # 5. 階層を遡るテスト (ancestor)
    target_path = "/opt/example/porta/src/foundation/path.py"
    parent_dir = get_ancestor(target_path, levels_up=1)
    grand_parent_name = get_ancestor(target_path, levels_up=2, return_name_only=True)
    print(f"[Ancestor] 1階層上の親: {parent_dir}")
    print(f"[Ancestor] 2階層上のフォルダ名のみ: {grand_parent_name}")

    print("=== 全テスト完了 ===")
