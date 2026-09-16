"""Private, no-persistence entrypoint for PORTA."""

from __future__ import annotations

import sys

# Set before importing project@ modules so normal use never creates __pycache__.
sys.dont_write_bytecode = True

import argparse
import os
from pathlib import Path

import shutil

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def configure_qt_input_method() -> None:
    """Make bundled Qt use the desktop IBus session without changing its settings."""
    # PySide bundles its own Qt, which does not always inherit the desktop's
    # input-method selection when launched through a detached shell script.
    # Respect an explicit choice such as fcitx; oterwise, use IBus only when
    # the host actually provides its daemon.
    requested_module = os.environ.get("QT_IM_MODULE", "")
    use_ibus = requested_module == "ibus" or (
        not requested_module and shutil.which("ibus-daemon") is not None
    )
    if not use_ibus:
        return
    for name, value in (
        ("QT_IM_MODULE", "ibus"),
        ("GTK_IM_MODULE", "ibus"),
        ("XMODIFIERS", "@im=ibus"),
    ):
        if name not in os.environ:
            os.environ[name] = value
            os.environ[f"PORTA_SET_{name}"] = "1"

    # Portable VS Code redirects XDG_CONFIG_HOME under its own
    # application directory.  IBus stores the address of the already-running
    # desktop daemon in XDG_CONFIG_HOME/ibus/bus, so a Qt child started by that
    # VS Code instance would otherwise look in the wrong empty location.  This
    # app has no XDG settings of its own; use the user's ordinary IBus location
    # only for that clearly editor-private launch environment.
    configured_xdg = os.environ.get("XDG_CONFIG_HOME", "")
    config_path = Path(configured_xdg).expanduser() if configured_xdg else None
    if config_path is not None and any(part.casefold() == "vscode" for part in config_path.parts):
        os.environ["PORTA_ORIGINAL_XDG_CONFIG_HOME"] = configured_xdg
        os.environ["XDG_CONFIG_HOME"] = str(Path.home() / ".config")


def main(argv: list[str] | None = None) -> int:
    from foundation.privacy import enable_private_runtime
    from foundation.product import PRODUCT_NAME

    enable_private_runtime()
    configure_qt_input_method()

    parser = argparse.ArgumentParser(description=f"{PRODUCT_NAME} main CLI")
    parser.add_argument(
        "--close-instances",
        action="store_true",
        help="このPORTAの別の main.py 起動を、実行中の作業も含めて終了します。",
    )
    parser.add_argument(
        "--external-open",
        choices=("choose", "file-manager", "media-organizer", "video-encoder"),
        metavar="連携先",
        help="外部ファイルマネージャーから受け取ったパスの用途選択、または指定画面を開きます。",
    )
    parser.add_argument(
        "external_paths",
        nargs="*",
        metavar="パス",
        help="--external-open へ渡すローカルパスまたは file:// URIです。",
    )
    parser.add_argument("--list-processes", action="store_true", help="このPORTAのプロセス一覧をJSONで表示します。")
    parser.add_argument("--record-center", action="store_true")
    parser.add_argument("--work-process", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--record-id", help="独立した対応表の識別コードを取得してファイルマネージャーを開きます。")
    parser.add_argument("--record-output", help="対応表の出力をテキスト分析で開きます。")
    arguments = parser.parse_args(argv)

    if arguments.work_process:
        # Drain the anonymous pipe before importing every application's GUI.
        # The launching manager can exit once delivery has completed.
        from runtime.work_process import MAX_BYTES
        raw = sys.stdin.buffer.read(MAX_BYTES + 1)
        try:
            from apps.launcher.work_window import run_work
            return run_work(raw)
        except Exception as error:
            from PySide6.QtWidgets import QApplication, QMessageBox
            app = QApplication.instance() or QApplication([])
            QMessageBox.critical(None, "作業を起動できません", str(error))
            return 1

    if arguments.list_processes:
        import json
        from dataclasses import asdict
        from runtime.process_control import process_inventory

        try:
            print(json.dumps([asdict(item) for item in process_inventory()], ensure_ascii=False, indent=2))
        except OSError as exc:
            print(f"プロセス一覧を確認できません: {exc}", file=sys.stderr)
            return 1
        return 0

    if arguments.record_center:
        from apps.launcher.record_center import run_record_center
        return run_record_center()

    if arguments.close_instances:
        from runtime.process_control import terminate_main_processes

        terminate_main_processes(ROOT / "scripts" / "main.py", exclude_pid=os.getpid())
        return 0

    if arguments.external_paths and arguments.external_open is None:
        parser.error("パスを渡す場合は --external-open も指定してください。")

    external_request = None
    external_error = None
    if arguments.external_open is not None:
        from foundation.external_open import (
            ExternalOpenValidationError,
            build_external_open_request,
        )

        try:
            external_request = build_external_open_request(
                arguments.external_open, arguments.external_paths
            )
        except ExternalOpenValidationError as exc:
            external_error = exc

    from PySide6.QtWidgets import QApplication, QMessageBox

    app = QApplication.instance() or QApplication([])
    app.setApplicationName(PRODUCT_NAME)
    app.setApplicationDisplayName(PRODUCT_NAME)
    if external_error is not None:
        dialog = QMessageBox(QMessageBox.Icon.Warning, "外部パスを開けません", external_error.summary())
        dialog.setDetailedText("\n".join(f"{item.reason}\t{item.value}" for item in external_error.issues))
        dialog.exec()
        return 2
    from runtime.single_instance import MainInstance, InstanceConnectionError

    launch_request = {
        "version": 1,
        "target": external_request.target if external_request is not None else None,
        "paths": [str(path) for path in external_request.paths] if external_request is not None else [],
        "record_id": arguments.record_id,
        "record_output": arguments.record_output,
    }
    instance = MainInstance(ROOT, app)
    try:
        if not instance.start_or_forward(launch_request):
            return 0
    except (InstanceConnectionError, ValueError) as exc:
        QMessageBox.warning(None, "PORTAを起動できません", str(exc))
        return 1

    app.aboutToQuit.connect(instance.close)
    try:
        from apps.launcher import MainMenuWindow
        from apps.launcher.launch_requests import LaunchRequests
        from runtime.instance_presence import InstancePresence
        from gui.process_tracking import PresenceHeartbeat

        process_lifetime = InstancePresence(screen="PORTA", state="起動中")
        lifetime_heartbeat = PresenceHeartbeat(process_lifetime, app)
        app.aboutToQuit.connect(lifetime_heartbeat.close)
        window = None
        try:
            window = MainMenuWindow()
            requests = LaunchRequests(window)
            instance.set_handler(requests.submit)
            requests.submit(launch_request)
            return app.exec()
        finally:
            lifetime_heartbeat.close()
            if window is not None:
                window._presence_heartbeat.close()
    finally:
        instance.close()

if __name__ == "__main__":
    raise SystemExit(main())
