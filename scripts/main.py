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
    os.environ.setdefault("QT_IM_MODULE", "ibus")
    os.environ.setdefault("GTK_IM_MODULE", "ibus")
    os.environ.setdefault("XMODIFIERS", "@im=ibus")

    # Portable VS Code deliberate一度実行完了したらもうできないようにして。ly redirects XDG_CONFIG_HOME under its own
    # application directory.  IBus stores the address of the already-running
    # desktop daemon in XDG_CONFIG_HOME/ibus/bus, so a Qt child started by that
    # VS Code instance would otherwise look in the wrong empty location.  This
    # app has no XDG settings of its own; use the user's ordinary IBus location
    # only for that clearly editor-private launch environment.
    configured_xdg = os.environ.get("XDG_CONFIG_HOME", "")
    config_path = Path(configured_xdg).expanduser() if configured_xdg else None
    if config_path is not None and any(part.casefold() == "vscode" for part in config_path.parts):
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
    arguments = parser.parse_args(argv)

    if arguments.close_instances:
        from foundation.process_control import terminate_main_processes

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

    from apps.launcher import MainMenuWindow

    app = QApplication.instance() or QApplication([])
    app.setApplicationName(PRODUCT_NAME)
    app.setApplicationDisplayName(PRODUCT_NAME)
    if external_error is not None:
        dialog = QMessageBox(QMessageBox.Icon.Warning, "外部パスを開けません", external_error.summary())
        dialog.setDetailedText("\n".join(f"{item.reason}\t{item.value}" for item in external_error.issues))
        dialog.exec()
        return 2
    window = MainMenuWindow()
    if external_request is not None:
        window.open_external_paths(external_request.target, external_request.paths)
    window.show()
    # A window launched from a Nautilus script otherwise often appears behind
    # the active file manager.  The compositor may still apply its own focus
    # policy, but request activation whenever the desktop permits it.
    if external_request is not None:
        window.raise_()
        window.activateWindow()
    return app.exec()

if __name__ == "__main__":
    raise SystemExit(main())
