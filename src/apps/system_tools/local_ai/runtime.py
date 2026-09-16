"""A disposable loopback-only llama.cpp session for local AI features."""

from __future__ import annotations

from runtime import managed_process
from runtime.runtime_activity import runtime_activity

from dataclasses import dataclass, field
import json
from pathlib import Path
import socket
import subprocess
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def build_server_command(runner_path: Path, model_path: Path, port: int) -> tuple[str, ...]:
    """Build the one local-only server command used by every AI client."""
    return (
        str(runner_path),
        "-m",
        str(model_path),
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "-c",
        "4096",
        "--jinja",
    )


@dataclass
class LocalAiSession:
    """One non-persistent llama-server process bound only to localhost."""

    runner_path: Path
    model_path: Path
    _port: int | None = None
    _process: subprocess.Popen[bytes] | None = field(default=None, init=False, repr=False)

    @property
    def endpoint(self) -> str:
        if self._port is None:
            raise RuntimeError("ローカルAIをまだ起動していません。")
        return f"http://127.0.0.1:{self._port}"

    @property
    def process_exit_code(self) -> int | None:
        return self._process.poll() if self._process is not None else None

    @property
    def process_id(self) -> int | None:
        """Expose only the current disposable server PID for UI transparency."""
        return self._process.pid if self._process is not None else None

    @property
    def launch_command(self) -> tuple[str, ...]:
        """Return the exact command after a local port has been chosen."""
        if self._port is None:
            return ()
        return build_server_command(self.runner_path, self.model_path, self._port)

    def start(self) -> None:
        if self._process is not None and self._process.poll() is None:
            return
        self._validate_launch_paths()
        self._port = _find_loopback_port()
        self._process = managed_process.popen(
            build_server_command(self.runner_path, self.model_path, self._port),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            label='ローカルAI',
        )

    def is_ready(self) -> bool:
        """Return readiness without waiting for model loading in the GUI thread."""
        if self._process is None or self._process.poll() is not None:
            return False
        try:
            with urlopen(f"{self.endpoint}/health", timeout=0.15) as response:
                return 200 <= response.status < 300
        except (HTTPError, URLError, TimeoutError):
            return False

    @runtime_activity("ローカルAI応答を生成中")
    def chat(self, messages: list[dict[str, str]]) -> str:
        """Submit one non-streaming chat request to the local-only endpoint."""
        payload: dict[str, Any] = {
            "messages": messages,
            "temperature": 0.7,
            "top_p": 0.8,
            "max_tokens": 512,
            "stream": False,
        }
        request = Request(
            f"{self.endpoint}/v1/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=120) as response:
                raw: Any = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"ローカルAIが応答を拒否しました: {detail}") from exc
        except (URLError, TimeoutError) as exc:
            raise RuntimeError("ローカルAIとの通信が途切れました。") from exc
        try:
            content = raw["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError("ローカルAIの応答形式を読み取れません。") from exc
        if not isinstance(content, str) or not content.strip():
            raise RuntimeError("ローカルAIが本文を返しませんでした。")
        return content.strip()

    def stop(self) -> None:
        process = self._process
        self._process = None
        if process is None or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)

    def _validate_launch_paths(self) -> None:
        if not self.runner_path.is_file():
            raise ValueError("runner_path の実行ファイルが見つかりません。")
        if not self.model_path.is_file():
            raise ValueError("model_path のGGUFモデルが見つかりません。")
        if not self.runner_path.stat().st_mode & 0o111:
            raise ValueError("runner_path は実行可能なファイルにしてください。")


def _find_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])
