"""Detached work launches and versioned, user-private control messages.

Inputs travel over an anonymous pipe, never through command arguments or files.
The manager owns no process termination handle and performs no automatic retry.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import socket
import stat
import subprocess
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[2]
MAX_BYTES = 1024 * 1024


def directory():
    key = hashlib.sha256(os.fsencode(ROOT.resolve())).hexdigest()[:16]
    return Path(f"/tmp/porta-works-{os.getuid()}-{key}")


def validate_command(value):
    if not isinstance(value, dict) or type(value.get("version")) is not int or value["version"] != 1 or value.get("op") not in ("status", "focus", "close"):
        raise ValueError("未対応の作業管理要求です。")
    deadline = value.get("deadline")
    if deadline is not None and (type(deadline) not in (int, float) or not math.isfinite(deadline) or time.monotonic() >= deadline):
        raise ValueError("作業管理要求の期限を超えました。再送しません。")
    return value


def request(endpoint, op, *, timeout=.6):
    path = Path(endpoint)
    if path.parent != directory():
        raise ValueError("作業の接続先が不正です。")
    for candidate, kind in ((path.parent, stat.S_ISDIR), (path, stat.S_ISSOCK)):
        info = candidate.lstat()
        if not kind(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise OSError("作業の接続先の権限を確認できません。")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as peer:
        peer.settimeout(timeout)
        peer.connect(str(path))
        peer.sendall(json.dumps({"version": 1, "op": op, "deadline": time.monotonic() + timeout}).encode() + b"\n")
        with peer.makefile("rb") as stream:
            raw = stream.readline(MAX_BYTES + 1)
    if not raw.endswith(b"\n") or len(raw) > MAX_BYTES:
        raise OSError("作業からの応答が不完全です。自動再送しません。")
    reply = json.loads(raw)
    if not isinstance(reply, dict) or reply.get("ok") is not True:
        raise OSError("作業が要求を受け付けませんでした。")
    return reply.get("result")


def inventory():
    result = []
    for path in sorted(directory().glob("*.sock")):
        try:
            state = request(path, "status")
            if not isinstance(state, dict) or type(state.get("version")) is not int or state["version"] != 1:
                raise ValueError("未対応の状態形式")
            if type(state.get("level")) is not int or state["level"] not in (1, 2, 3, 4):
                state["level"] = None
            if not isinstance(state.get("title"), str) or not isinstance(state.get("reason"), str):
                raise ValueError("作業名・状態説明を確認できません")
            if type(state.get("pid")) is not int or state["pid"] <= 0 or not isinstance(state.get("app"), str):
                raise ValueError("作業の識別情報を確認できません")
            result.append({**state, "endpoint": str(path)})
        except (FileNotFoundError, ConnectionRefusedError):
            continue  # An exited process may have left its socket behind.
        except (OSError, ValueError) as error:
            result.append({"endpoint": str(path), "title": "応答を確認できない作業",
                           "level": None, "reason": str(error), "pid": "不明"})
    return result


def spawn(payload):
    raw = json.dumps({"version": 1, **payload}, ensure_ascii=False).encode()
    if len(raw) > MAX_BYTES:
        raise ValueError("作業への引き継ぎは1MiB以内にしてください。")
    # A PORTA window owns itself after launch.  The launcher deliberately
    # keeps no parent/child management record for another PORTA window.
    process = subprocess.Popen(
        [sys.executable, str(ROOT / "scripts/main.py"), "--work-process"],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    from runtime.process_registry import get_registry
    get_registry().ignore(process.pid)

    def deliver():
        try:
            with process.stdin:
                process.stdin.write(raw)
        except (OSError, ValueError):
            pass  # Never replay a possibly delivered operation.
        finally:
            threading.Thread(target=process.wait, daemon=True).start()

    # Finish delivering the initial input even if the manager closes immediately.
    threading.Thread(target=deliver, daemon=False).start()
    return process.pid


def open_manager():
    process = subprocess.Popen([sys.executable, str(ROOT / "scripts/main.py")],
                               stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, start_new_session=True)
    from runtime.process_registry import get_registry
    get_registry().ignore(process.pid)
    threading.Thread(target=process.wait, daemon=True).start()
