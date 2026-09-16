"""Publish a complete output without replacing another writer's entry."""
from __future__ import annotations

import ctypes
import errno
import os
from pathlib import Path
import shutil
import tempfile
import stat

from runtime.operation_progress import checkpoint


def _copy_file(source, destination):
    checkpoint(str(source))
    descriptor = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as reader:
        before = os.fstat(reader.fileno())
        if not stat.S_ISREG(before.st_mode):
            raise OSError(f"通常ファイル以外はコピーしません: {source}")
        with open(destination, "xb") as writer:
            while block := reader.read(1024 * 1024):
                checkpoint()
                writer.write(block)
        after = os.fstat(reader.fileno())
        if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
            after.st_size, after.st_mtime_ns, after.st_ctime_ns
        ):
            raise OSError(f"コピー中に元ファイルが更新されました: {source}")
    shutil.copystat(source, destination)
    return destination


def rename_noreplace(source: Path, destination: Path) -> None:
    """Fail closed when the filesystem cannot provide atomic no-replace."""
    libc = ctypes.CDLL(None, use_errno=True)
    rename = getattr(libc, "renameat2", None)
    if rename is None:
        raise OSError(errno.ENOTSUP, "非上書き移動を保証できない環境です", str(destination))
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    if rename(-100, os.fsencode(source), -100, os.fsencode(destination), 1):
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code), str(destination))


def copy_noreplace(source: Path, destination: Path) -> Path:
    """Copy privately on the destination volume, then publish atomically.

    Cleanup touches only the private staging directory. A published output
    belongs to the user and is never removed because a later batch item fails.
    """
    with tempfile.TemporaryDirectory(prefix=".porta-copy-", dir=destination.parent) as temporary:
        checkpoint(str(source))
        staged = Path(temporary) / "entry"
        if source.is_symlink():
            staged.symlink_to(os.readlink(source))
        elif source.is_dir():
            shutil.copytree(source, staged, symlinks=True, copy_function=_copy_file)
        else:
            _copy_file(source, staged)
        checkpoint()
        rename_noreplace(staged, destination)
    return destination
