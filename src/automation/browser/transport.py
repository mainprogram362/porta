"""Local socket transport. No browser discovery through command-line guessing."""
from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import stat

MAX_MESSAGE = 800_000


def bridge_directory():
    # A short, user-specific path is shared with the standalone native host.
    return Path(f"/tmp/porta-browser-{os.getuid()}")


def validate_directory(path):
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise OSError("ブラウザ接続ディレクトリの所有者・権限が不正です。")


def request(endpoint, command, *, timeout=35):
    path = Path(endpoint)
    if path.parent != bridge_directory():
        raise OSError("不正な接続先です。")
    validate_directory(path.parent)
    info = path.lstat()
    if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid():
        raise OSError("接続先がユーザー所有ソケットではありません。")
    body = json.dumps(command, ensure_ascii=False).encode("utf-8") + b"\n"
    if len(body) > MAX_MESSAGE:
        raise ValueError("要求が大きすぎます。")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(timeout)
        connection.connect(str(path))
        connection.sendall(body)
        with connection.makefile("rb") as stream:
            reply = stream.readline(MAX_MESSAGE + 1)
    if not reply.endswith(b"\n") or len(reply) > MAX_MESSAGE:
        raise OSError("応答が切断されたか、上限を超えました。結果を確認してください。")
    data = json.loads(reply)
    if not isinstance(data, dict) or not data.get("ok"):
        raise OSError(data.get("error", "ブラウザ操作に失敗しました。") if isinstance(data, dict) else "応答形式が不正です。")
    return data["result"]


def discover():
    directory = bridge_directory()
    if not directory.exists():
        return [], []
    validate_directory(directory)
    sessions, errors = [], []
    for path in sorted(directory.glob("*.sock")):
        try:
            result = request(path, {"op": "inventory"}, timeout=2)
            sessions.append({"endpoint": str(path), **result})
        except (OSError, ValueError) as exc:
            errors.append(f"{path.name}: {exc}")
    return sessions, errors
