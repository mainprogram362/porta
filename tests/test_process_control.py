from pathlib import Path

from foundation import process_registry

from foundation.process_control import active_main_operations, running_main_processes
from foundation.runtime_activity import RuntimeActivityInfo


def _write_process(proc_root, pid, cwd, arguments):
    process = proc_root / str(pid)
    process.mkdir()
    (process / "cmdline").write_bytes(b"\0".join(argument.encode() for argument in arguments) + b"\0")
    (process / "cwd").symlink_to(cwd)
    fields = ["S", "1"] + ["0"] * 17 + [str(pid * 10)]
    (process / "stat").write_text(f"{pid} (python) " + " ".join(fields))
    boot = proc_root / "sys/kernel/random/boot_id"
    boot.parent.mkdir(parents=True, exist_ok=True)
    boot.write_text("test-boot")


def test_running_main_processes_matches_only_the_exact_project_main_script(tmp_path):
    project = tmp_path / "open_space" / "porta"
    script = project / "scripts" / "main.py"
    script.parent.mkdir(parents=True)
    script.touch()
    proc_root = tmp_path / "proc"
    proc_root.mkdir()
    _write_process(proc_root, 101, project, ("python", "scripts/main.py"))
    _write_process(proc_root, 102, project, ("firefox", "https://example.com"))
    _write_process(proc_root, 103, project, ("code", str(script.parent)))
    other = tmp_path / "other" / "scripts" / "main.py"
    other.parent.mkdir(parents=True)
    other.touch()
    _write_process(proc_root, 104, other.parents[1], ("python", str(other)))

    matches = running_main_processes(script, proc_root=proc_root)

    assert [(process.pid, process.command) for process in matches] == [
        (101, ("python", "scripts/main.py")),
    ]


def test_active_main_operations_uses_only_matching_main_processes(tmp_path):
    project = tmp_path / "open_space" / "porta"
    script = project / "scripts" / "main.py"
    script.parent.mkdir(parents=True)
    script.touch()
    proc_root = tmp_path / "proc"
    proc_root.mkdir()
    _write_process(proc_root, 201, project, ("python", "scripts/main.py"))
    _write_process(proc_root, 202, project, ("firefox", "https://example.com"))
    runtime = tmp_path / "runtime"
    first = process_registry.ProcessRegistry(runtime, root=project, monitor=False)
    first.identity = process_registry.read_process(201, proc_root).identity
    token = first.begin_activity("動画変換中")
    other = process_registry.ProcessRegistry(runtime, root=tmp_path / "another-porta", monitor=False)
    other.identity = process_registry.read_process(202, proc_root).identity
    other.begin_activity("別のPORTAの作業")
    try:
        assert active_main_operations(
            script, proc_root=proc_root, runtime_directory=runtime
        ) == (RuntimeActivityInfo(201, "動画変換中"),)
        # PID reuse must not inherit a previous process's active operations.
        (proc_root / "201/stat").write_text("201 (python) " + " ".join(["S", "1"] + ["0"] * 17 + ["99999"]))
        assert active_main_operations(script, proc_root=proc_root, runtime_directory=runtime) == ()
    finally:
        first.end_activity(token)
        first.close()
        other.close()


def test_script_path_as_data_does_not_identify_a_porta_process(tmp_path):
    project = tmp_path / "porta"
    script = project / "scripts/main.py"
    script.parent.mkdir(parents=True)
    script.touch()
    proc_root = tmp_path / "proc"
    proc_root.mkdir()
    for pid, command in enumerate((
        ("code", str(script)),
        ("python", "other.py", str(script)),
        ("python", "-c", "print(1)", str(script)),
        ("python", "-m", "other", str(script)),
        ("python", str(script), "--list-processes"),
    ), 301):
        _write_process(proc_root, pid, project, command)
    _write_process(proc_root, 400, project, ("python3.13", "-B", "-W", "ignore", str(script)))
    assert [item.pid for item in running_main_processes(script, proc_root=proc_root)] == [400]


def test_force_close_skips_pid_reuse_and_pins_validated_targets(monkeypatch):
    from dataclasses import replace
    from foundation import process_control as control
    current = process_registry.read_process(__import__('os').getpid())
    item = control.MainProcess(current.identity.pid, ("python", "scripts/main.py"), current.identity)
    monkeypatch.setattr(control, "running_main_processes", lambda _: (item,))
    monkeypatch.setattr(control.os, "pidfd_open", lambda _: 123)
    closed, sent = [], []
    monkeypatch.setattr(control.os, "close", closed.append)
    monkeypatch.setattr(control.signal, "pidfd_send_signal", lambda *args: sent.append(args))
    monkeypatch.setattr(control, "_main_arguments", lambda *args: item.command)
    monkeypatch.setattr(control, "read_process", lambda _: replace(current, identity=replace(current.identity, start_ticks=current.identity.start_ticks + 1)))
    assert control.terminate_main_processes(Path("scripts/main.py")) == ()
    assert sent == []
    assert closed == [123]
    monkeypatch.setattr(control, "read_process", lambda _: current)
    assert control.terminate_main_processes(Path("scripts/main.py")) == (item.pid,)
    assert sent == [(123, control.signal.SIGTERM)]
    assert closed == [123, 123]
