"""Ephemeral local transport for independently owned correspondence tables."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from PySide6.QtNetwork import QLocalSocket
from records.record_bundle import RecordBundle, RecordBundleRow


PORTA_ROOT = Path(__file__).resolve().parents[2]


def server_name() -> str:
    """Keep the existing endpoint so live correspondence tables remain reachable."""
    identity = hashlib.sha256(str(PORTA_ROOT).encode()).hexdigest()[:12]
    return f"porta-records-{os.getuid()}-{identity}"


def encode_bundle(bundle: RecordBundle) -> dict:
    return {"title": bundle.title, "fields": list(bundle.field_names),
            "rows": [{"id": row.identifier, "values": list(row.values)} for row in bundle.rows]}


def decode_bundle(data: dict) -> RecordBundle:
    if not isinstance(data, dict) or not isinstance(data.get("title"), str):
        raise ValueError("対応表の形式が不正です。")
    fields = data["fields"]
    rows = data["rows"]
    if not isinstance(fields, list) or not all(isinstance(x, str) for x in fields):
        raise ValueError("項目名の形式が不正です。")
    parsed = []
    for row in rows:
        if not isinstance(row["id"], str) or not isinstance(row["values"], list) or not all(isinstance(x, str) for x in row["values"]):
            raise ValueError("レコードの形式が不正です。")
        parsed.append(RecordBundleRow(row["id"], tuple(row["values"])))
    return RecordBundle(data["title"], tuple(fields), tuple(parsed))


def request(message: dict, timeout_ms: int = 3000) -> dict:
    raw = json.dumps(message, ensure_ascii=False).encode() + b"\n"
    if len(raw) > 32 * 1024 * 1024:
        raise ValueError("対応表が送信上限（32MB）を超えています。")
    socket = QLocalSocket()
    socket.connectToServer(server_name())
    if not socket.waitForConnected(timeout_ms):
        return {}
    socket.write(raw)
    deadline = time.monotonic() + timeout_ms / 1000
    result = bytearray()
    try:
        while time.monotonic() < deadline:
            if socket.bytesToWrite():
                socket.waitForBytesWritten(50)
            result.extend(bytes(socket.readAll()))
            if b"\n" in result:
                return json.loads(bytes(result).split(b"\n", 1)[0])
            socket.waitForReadyRead(50)
        return {}
    finally:
        socket.abort()


def ensure_service() -> bool:
    if request({"action": "ping"}, 250).get("ok"):
        return True
    process = subprocess.Popen(
        [sys.executable, str(PORTA_ROOT / "scripts/main.py"), "--record-center"],
        cwd=PORTA_ROOT,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    from runtime.process_registry import get_registry
    get_registry().ignore(process.pid)
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        if request({"action": "ping"}, 250).get("ok"):
            return True
        time.sleep(.05)
    return False


def create(bundle: RecordBundle) -> dict:
    if not ensure_service():
        raise ValueError("独立した対応表プロセスを起動できませんでした。")
    response = request({"action": "create", "bundle": encode_bundle(bundle)}, 10000)
    if not response.get("ok"):
        raise ValueError(response.get("error", "対応表の受領を確認できませんでした。"))
    return response
