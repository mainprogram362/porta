"""Standard-library-only setup and relocation entrypoint for PORTA."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
VENV = ROOT / ".venv"
VENV_PYTHON = VENV / "bin" / "python"
LOCATION_MARKER = VENV / ".porta-root"
BASE_PYTHON = Path(getattr(sys, "_base_executable", sys.executable))


def _environment_works() -> bool:
    if not VENV_PYTHON.is_file():
        return False
    result = subprocess.run(
        [str(VENV_PYTHON), "-c", "import PySide6"],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return result.returncode == 0


def _recorded_root() -> Path | None:
    try:
        value = LOCATION_MARKER.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return Path(value) if value else None


def _needs_setup() -> tuple[bool, str]:
    if not _environment_works():
        return True, "Python仮想環境がないか、必要なライブラリを読み込めません。"
    recorded = _recorded_root()
    if recorded is None:
        return True, "この仮想環境がどのPORTA用に作られたか確認できません。"
    if recorded != ROOT:
        return True, f"PORTAの移動を検出しました。\n旧位置: {recorded}\n現在: {ROOT}"
    return False, ""


def _confirm_setup(reason: str) -> bool:
    print("PORTA 初期セットアップ・環境修復")
    print(reason)
    print(f"作成先: {VENV}")
    print("PORTAのCONFIG、AIモデル、ユーザーデータは変更しません。")
    if not sys.stdin.isatty():
        print("対話可能なターミナルから start.sh を実行してください。", file=sys.stderr)
        return False
    answer = input("仮想環境を作成して必要ライブラリを導入しますか？ [y/N]: ").strip().casefold()
    return answer in {"y", "yes"}


def _build_environment() -> None:
    backup = ROOT / ".venv-porta-old"
    if backup.exists():
        raise RuntimeError("以前の環境作成用フォルダが残っています。内容を確認してから再実行してください。")
    if VENV.exists():
        VENV.replace(backup)
    try:
        print("1/2 新しい仮想環境を作成しています。")
        subprocess.run([str(BASE_PYTHON), "-m", "venv", str(VENV)], check=True)
        print("2/2 PORTAと必要ライブラリを導入しています。")
        subprocess.run(
            [
                str(VENV_PYTHON),
                "-m",
                "pip",
                "install",
                "--no-cache-dir",
                "-e",
                str(ROOT),
            ],
            check=True,
        )
        LOCATION_MARKER.write_text(f"{ROOT}\n", encoding="utf-8")
    except Exception:
        if VENV.exists():
            shutil.rmtree(VENV)
        if backup.exists():
            backup.replace(VENV)
        raise
    if backup.exists():
        shutil.rmtree(backup)


def main() -> int:
    needs_setup, reason = _needs_setup()
    if needs_setup:
        if not _confirm_setup(reason):
            return 1
        try:
            _build_environment()
        except (OSError, subprocess.CalledProcessError, RuntimeError) as exc:
            print(f"PORTAの環境を作成できませんでした: {exc}", file=sys.stderr)
            return 1
    os.execv(str(VENV_PYTHON), [str(VENV_PYTHON), str(ROOT / "scripts" / "main.py"), *sys.argv[1:]])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
