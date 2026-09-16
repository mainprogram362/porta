"""One main GUI per resolved installation and user, using ephemeral local IPC.

The empty flock file is deliberately retained: unlinking it would let waiters
lock different inodes. The kernel releases the lock even after a crash. No
launch arguments or document contents are written to disk.
"""
from __future__ import annotations

from collections.abc import Callable
import fcntl
import hashlib
import json
import os
from pathlib import Path
import socket
import stat
import tempfile
import time

from PySide6.QtCore import QObject, QTimer
from PySide6.QtNetwork import QLocalServer

MAX_REQUEST_BYTES = 1024 * 1024


class InstanceConnectionError(RuntimeError):
    pass


def validate_request(value: object) -> dict:
    """Accept only the supported GUI launch fields, never arbitrary CLI flags."""
    if not isinstance(value, dict) or set(value) != {"version", "target", "paths", "record_id", "record_output"}:
        raise ValueError("起動要求の形式が不正です。")
    if type(value["version"]) is not int or value["version"] != 1:
        raise ValueError("起動要求のバージョンが未対応です。")
    target, paths = value["target"], value["paths"]
    if target is not None and target not in ("choose", "file-manager", "media-organizer", "video-encoder"):
        raise ValueError("外部連携先が不正です。")
    if not isinstance(paths, list) or not all(isinstance(p, str) and Path(p).is_absolute() and "\0" not in p for p in paths):
        raise ValueError("受信するパスが不正です。")
    if (target is None and paths) or (target is not None and not paths):
        raise ValueError("外部連携先とパスが一致しません。")
    for name in ("record_id", "record_output"):
        field = value[name]
        if field is not None and (not isinstance(field, str) or not field or len(field) > 256):
            raise ValueError("対応表の識別コードが不正です。")
    return value


class MainInstance(QObject):
    """Elect an owner before constructing windows; acknowledge queued requests."""

    def __init__(self, root: Path, parent: QObject | None = None, *, directory: Path | None = None,
                 validator=validate_request) -> None:
        super().__init__(parent)
        self._validator = validator
        self.directory = directory or Path(tempfile.gettempdir()) / f"porta-main-{os.getuid()}"
        key = hashlib.sha256(os.fsencode(root.resolve())).hexdigest()[:24]
        # Portable editors may set TMPDIR to a path longer than sockaddr_un.
        # Use the ordinary OS temporary directory in that case.
        if directory is None and len(os.fsencode(self.directory / f"{key}.sock")) >= 104:
            self.directory = Path("/tmp") / f"porta-main-{os.getuid()}"
        self.endpoint = str(self.directory / f"{key}.sock")
        self.lock_path = self.directory / f"{key}.lock"
        self._fd: int | None = None
        self._owner = False
        self._handler: Callable[[dict], None] | None = None
        self._connections: dict = {}
        self.server = QLocalServer(self)
        self.server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        self.server.newConnection.connect(self._accept)

    def _open_lock(self) -> None:
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = self.directory.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise OSError("単一起動の通信先の所有者・権限を確認できません。")
        fd = os.open(self.lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, 0o600)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1:
            os.close(fd)
            raise OSError("単一起動のロックファイルを確認できません。")
        self._fd = fd

    def start_or_forward(self, request: dict, *, timeout_ms: int = 5000) -> bool:
        """Return True for the owner, False only after an explicit receipt ACK.

        Retry election while the owner is starting or exiting. Once connected,
        never resend a request or create another GUI on an ambiguous timeout.
        """
        raw = json.dumps(self._validator(request), ensure_ascii=False).encode() + b"\n"
        if len(raw) > MAX_REQUEST_BYTES:
            raise InstanceConnectionError("起動要求が送信上限（1MB）を超えています。")
        deadline = time.monotonic() + timeout_ms / 1000
        try:
            self._open_lock()
            while time.monotonic() < deadline:
                try:
                    fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    self._owner = True
                except BlockingIOError:
                    pass
                if self._owner:
                    # Only the lock owner may remove a socket left by a crash.
                    QLocalServer.removeServer(self.endpoint)
                    if not self.server.listen(self.endpoint):
                        raise OSError(self.server.errorString())
                    return True
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as peer:
                    peer.settimeout(max(.001, deadline - time.monotonic()))
                    try:
                        peer.connect(self.endpoint)
                    except (FileNotFoundError, ConnectionRefusedError):
                        time.sleep(.025)
                        continue
                    peer.sendall(raw)
                    response = bytearray()
                    while b"\n" not in response:
                        peer.settimeout(max(.001, deadline - time.monotonic()))
                        part = peer.recv(4096)
                        if not part or len(response) + len(part) > 4096:
                            raise InstanceConnectionError("既存本体から受領確認を取得できませんでした。再起動先は増やしていません。")
                        response.extend(part)
                    reply = json.loads(response.split(b"\n", 1)[0])
                    if not isinstance(reply, dict) or reply.get("ok") is not True:
                        raise InstanceConnectionError("既存本体が起動要求を受け付けられませんでした。")
                    self.close()
                    return False
            raise InstanceConnectionError("既存本体が起動中または応答待ちです。少し待ってから起動し直してください。")
        except (OSError, ValueError, InstanceConnectionError) as exc:
            self.close()
            if isinstance(exc, InstanceConnectionError):
                raise
            raise InstanceConnectionError("既存本体への接続・受領確認ができませんでした。追加の本体は起動していません。") from exc

    def set_handler(self, handler: Callable[[dict], None]) -> None:
        self._handler = handler

    def _accept(self) -> None:
        while self.server.hasPendingConnections():
            peer = self.server.nextPendingConnection()
            if len(self._connections) >= 32:
                peer.abort()
                peer.deleteLater()
                continue
            peer.setReadBufferSize(MAX_REQUEST_BYTES + 1)
            self._connections[peer] = bytearray()
            timer = QTimer(peer)
            timer.setSingleShot(True)
            timer.timeout.connect(peer.abort)
            timer.start(5000)
            peer.disconnected.connect(lambda p=peer: self._discard(p))
            peer.readyRead.connect(lambda p=peer: self._read(p))
            self._read(peer)

    def _discard(self, peer) -> None:
        self._connections.pop(peer, None)
        peer.deleteLater()

    def _read(self, peer) -> None:
        buffer = self._connections.get(peer)
        if buffer is None:
            return
        buffer.extend(bytes(peer.readAll()))
        if len(buffer) > MAX_REQUEST_BYTES:
            peer.abort()
            return
        if b"\n" not in buffer:
            return
        self._connections.pop(peer, None)
        try:
            request = self._validator(json.loads(buffer.split(b"\n", 1)[0]))
            if self._handler is None:
                raise ValueError("起動準備中です。")
            result = self._handler(request)
            reply = (b'{"ok":true}\n' if result is None else
                     json.dumps({"ok": True, "result": result}, ensure_ascii=False).encode() + b"\n")
        except (ValueError, RuntimeError):
            reply = b'{"ok":false}\n'
        peer.write(reply)
        peer.disconnectFromServer()

    def close(self) -> None:
        for peer in tuple(self._connections):
            peer.abort()
        self.server.close()
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None
        self._owner = False
