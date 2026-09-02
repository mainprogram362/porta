import shutil
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CORE_ROOT = PROJECT_ROOT / "core"
STANDALONE_APPS_ROOT = PROJECT_ROOT / "standalone_apps"


def _run_bash(command: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "-c", command],
        check=False,
        capture_output=True,
        text=True,
    )


def test_bundled_core_discovers_porta_from_its_parent() -> None:
    result = _run_bash(
        f'source "{CORE_ROOT}/libs/common.sh"; '
        'printf "%s\n%s\n" "$PORTA_DIR" "$PORTA_AVAILABLE"'
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [str(PROJECT_ROOT), "1"]


def test_core_self_check_succeeds_without_porta(tmp_path: Path) -> None:
    detached_core = tmp_path / "core"
    shutil.copytree(CORE_ROOT, detached_core)

    result = _run_bash(
        f'source "{detached_core}/libs/common.sh"; '
        'printf "PORTA_AVAILABLE=%s\n" "$PORTA_AVAILABLE"; core_self_check'
    )

    assert result.returncode == 0, result.stderr
    assert "PORTA_AVAILABLE=0" in result.stdout
    assert "PORTA本体は未接続です（CORE単体利用には影響しません）" in result.stdout


def test_core_reads_separate_standalone_and_external_program_locations(tmp_path: Path) -> None:
    porta = tmp_path / "porta"
    detached_core = porta / "core"
    shutil.copytree(CORE_ROOT, detached_core)
    shared = porta / "user" / "config" / "shared"
    shared.mkdir(parents=True)
    (porta / "persistent_settings_location.txt").write_text(
        "# PORTA_LOCATION_V1\nuser\n",
        encoding="utf-8",
    )
    (shared / "standalone_apps_location.txt").write_text(
        "# PORTA_STANDALONE_APPS_LOCATION_V1\n@PORTA/standalone_apps\n",
        encoding="utf-8",
    )
    (shared / "external_program_locations.txt").write_text(
        "# PORTA_EXTERNAL_PROGRAM_LOCATIONS_V1\n../../external_apps\n",
        encoding="utf-8",
    )

    result = _run_bash(
        f'source "{detached_core}/libs/common.sh"; '
        'printf "%s\n%s\n%s\n%s\n%s\n" "$CONFIG_DIR" "$STANDALONE_APPS_STATE" '
        '"${STANDALONE_APPS_DIRS[0]}" "$EXTERNAL_PROGRAM_STATE" '
        '"${EXTERNAL_PROGRAM_DIRS[0]}"'
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        str(porta / "user" / "config"),
        "位置設定を確認",
        str(porta / "standalone_apps"),
        "位置設定を確認",
        str(porta / "user" / "external_apps"),
    ]


def test_standalone_apps_do_not_depend_on_core(tmp_path: Path) -> None:
    detached_apps = tmp_path / "standalone_apps"
    shutil.copytree(STANDALONE_APPS_ROOT, detached_apps)

    result = _run_bash(
        f'source "{detached_apps}/libs/common.sh"; '
        'printf "%s\n" "$STANDALONE_APPS_DIR"'
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == str(detached_apps)
    for shell_file in detached_apps.rglob("*.sh"):
        assert "core/libs" not in shell_file.read_text(encoding="utf-8")


def test_all_bundled_shell_files_have_valid_syntax() -> None:
    shell_files = sorted((*CORE_ROOT.rglob("*.sh"), *STANDALONE_APPS_ROOT.rglob("*.sh")))

    assert shell_files
    for shell_file in shell_files:
        result = subprocess.run(
            ["bash", "-n", str(shell_file)],
            check=False,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"{shell_file}: {result.stderr}"
