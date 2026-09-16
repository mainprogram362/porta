#!/usr/bin/env python3
"""Firefox stdio <-> local Unix socket bridge, one instance per connection.

Only imported/executed by the native-messaging launcher. No GUI dependency;
stdout contains only Firefox framing. Tab lists are never written to disk.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import queue
import socket
import stat
import struct
import sys
import threading
import time
import uuid

MAX_MESSAGE = 800_000


def read_exact(stream, size):
    parts = bytearray()
    while len(parts) < size:
        block = stream.read(size - len(parts))
        if not block:
            raise EOFError
        parts.extend(block)
    return bytes(parts)


def main():
    directory = Path(f"/tmp/porta-browser-{os.getuid()}")
    directory.mkdir(mode=0o700, exist_ok=True)
    info = directory.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise OSError("Unsafe bridge directory")
    endpoint = directory / (uuid.uuid4().hex + ".sock")
    replies = {}
    lock = threading.Lock()
    stopped = threading.Event()
    output_lock = threading.Lock()

    def receive():
        try:
            while not stopped.is_set():
                length = struct.unpack("=I", read_exact(sys.stdin.buffer, 4))[0]
                if length > MAX_MESSAGE:
                    raise ValueError("Message too large")
                message = json.loads(read_exact(sys.stdin.buffer, length))
                with lock:
                    destination = replies.get(message.get("id"))
                if destination is not None:
                    destination.put(message)
        except (EOFError, OSError, ValueError, AttributeError):
            stopped.set()
            with lock:
                for destination in replies.values():
                    destination.put({"ok": False, "error": "Firefoxとの接続が切れました。結果不明の操作は再送しません。"})

    def handle(connection):
        code = uuid.uuid4().hex
        inbox = queue.Queue()
        try:
            with connection:
                connection.settimeout(40)
                with connection.makefile("rb") as stream:
                    raw = stream.readline(MAX_MESSAGE + 1)
                if not raw.endswith(b"\n") or len(raw) > MAX_MESSAGE:
                    return
                command = json.loads(raw)
                if not isinstance(command, dict):
                    raise ValueError("Command must be an object")
                command["deadline"] = int(time.time() * 1000) + 32000
                with lock:
                    replies[code] = inbox
                encoded = json.dumps({"id": code, "command": command}, ensure_ascii=False).encode("utf-8")
                if len(encoded) > MAX_MESSAGE:
                    raise ValueError("Message too large")
                with output_lock:
                    sys.stdout.buffer.write(struct.pack("=I", len(encoded)) + encoded)
                    sys.stdout.buffer.flush()
                try:
                    response = inbox.get(timeout=34)
                except queue.Empty:
                    response = {"ok": False, "error": "応答がありません。実行結果不明です。自動再送しません。"}
                connection.sendall(json.dumps(response, ensure_ascii=False).encode("utf-8") + b"\n")
        except (OSError, ValueError):
            pass
        finally:
            with lock:
                replies.pop(code, None)

    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        listener.bind(str(endpoint))
        os.chmod(endpoint, 0o600)
        listener.listen(16)
        listener.settimeout(1)
        threading.Thread(target=receive, daemon=True).start()
        while not stopped.is_set():
            try:
                connection, _ = listener.accept()
            except socket.timeout:
                continue
            threading.Thread(target=handle, args=(connection,), daemon=True).start()
    finally:
        stopped.set()
        listener.close()
        endpoint.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
