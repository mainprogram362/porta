from pathlib import Path

import pytest

from apps.system_tools.local_ai.runtime import LocalAiSession, build_server_command


def test_local_ai_server_command_is_loopback_only_and_has_a_small_context() -> None:
    command = build_server_command(Path("/tools/llama-server"), Path("/models/qwen.gguf"), 43123)

    assert command == (
        "/tools/llama-server", "-m", "/models/qwen.gguf", "--host", "127.0.0.1",
        "--port", "43123", "-c", "4096", "--jinja",
    )


def test_local_ai_session_refuses_missing_or_non_executable_runner(tmp_path: Path) -> None:
    model = tmp_path / "model.gguf"
    model.write_bytes(b"test")
    session = LocalAiSession(tmp_path / "missing-runner", model)

    with pytest.raises(ValueError, match="runner_path"):
        session.start()

    runner = tmp_path / "runner"
    runner.write_text("#!/bin/sh\n", encoding="utf-8")
    session = LocalAiSession(runner, model)
    with pytest.raises(ValueError, match="実行可能"):
        session.start()


def test_local_ai_session_reports_no_launch_command_before_a_port_is_chosen(tmp_path: Path) -> None:
    session = LocalAiSession(tmp_path / "runner", tmp_path / "model.gguf")

    assert session.process_id is None
    assert session.launch_command == ()
