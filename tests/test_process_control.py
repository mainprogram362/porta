import json

from foundation.process_control import active_main_operations, running_main_processes
from foundation.runtime_activity import RuntimeActivityInfo


def _write_process(proc_root, pid, cwd, arguments):
    process = proc_root / str(pid)
    process.mkdir()
    (process / "cmdline").write_bytes(b"\0".join(argument.encode() for argument in arguments) + b"\0")
    (process / "cwd").symlink_to(cwd)


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
    runtime.mkdir()
    (runtime / "201.json").write_text(
        json.dumps({"pid": 201, "activities": ["動画変換中"]}), encoding="utf-8"
    )
    (runtime / "202.json").write_text(
        json.dumps({"pid": 202, "activities": ["偽の状態"]}), encoding="utf-8"
    )

    assert active_main_operations(
        script, proc_root=proc_root, runtime_directory=runtime
    ) == (RuntimeActivityInfo(201, "動画変換中"),)
