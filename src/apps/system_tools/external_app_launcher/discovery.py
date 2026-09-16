"""Read-only discovery and explicit launch helpers for configured app folders."""

from __future__ import annotations

from runtime.process_registry import get_registry

from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import subprocess


@dataclass(frozen=True)
class ApplicationLocation:
    """One configured directory whose immediate children may be programs."""

    number: int
    display_name: str
    directory_path: Path
    launcher_name: str = "start.sh"


@dataclass(frozen=True)
class DiscoveredApplication:
    location: ApplicationLocation
    name: str
    launcher_path: Path


@dataclass(frozen=True)
class LocationScan:
    location: ApplicationLocation
    applications: tuple[DiscoveredApplication, ...]
    state: str
    detail: str


def scan_location(location: ApplicationLocation) -> LocationScan:
    """Find immediate child folders that contain the configured launcher name."""
    directory = location.directory_path
    try:
        directory.stat()
    except FileNotFoundError:
        return LocationScan(location, (), "missing_directory", f"指定した置き場がありません: {directory}")
    except PermissionError:
        return LocationScan(location, (), "unreadable_directory", f"置き場を読む権限がありません: {directory}")
    except OSError as exc:
        return LocationScan(location, (), "unreadable_directory", f"置き場を確認できません: {directory} ({exc})")
    if not directory.is_dir():
        return LocationScan(location, (), "not_directory", f"指定先はディレクトリではありません: {directory}")
    try:
        children = tuple(directory.iterdir())
    except PermissionError:
        return LocationScan(location, (), "unreadable_directory", f"置き場を読む権限がありません: {directory}")
    except OSError as exc:
        return LocationScan(location, (), "unreadable_directory", f"置き場を読めません: {directory} ({exc})")
    if not children:
        return LocationScan(location, (), "empty_directory", f"置き場は読み込めましたが空です: {directory}")
    child_directories = tuple(child for child in children if child.is_dir())
    applications = tuple(
        DiscoveredApplication(location, child.name, child / location.launcher_name)
        for child in sorted(child_directories, key=lambda item: item.name.casefold())
        if (child / location.launcher_name).is_file()
    )
    if applications:
        return LocationScan(
            location,
            applications,
            "ready",
            f"置き場を読み込みました。{len(applications)} 件のプログラムを見つけました。",
        )
    if not child_directories:
        return LocationScan(
            location,
            (),
            "no_child_directories",
            f"置き場は読み込めましたが、直下にプログラム用フォルダがありません: {directory}",
        )
    return LocationScan(
        location,
        (),
        "launcher_missing",
        f"{len(child_directories)} 個の子フォルダはありますが、{location.launcher_name} が見つかりません。",
    )


def launch(application: DiscoveredApplication) -> None:
    """Explicitly launch one user-configured ``start.sh`` without a shell string."""
    bash = shutil.which("bash")
    if bash is None:
        raise OSError("bash が見つからないため、このランチャーを起動できません。")
    environment = os.environ.copy()
    original_xdg_config = environment.pop("PORTA_ORIGINAL_XDG_CONFIG_HOME", "")
    for name in ("QT_IM_MODULE", "GTK_IM_MODULE", "XMODIFIERS"):
        if environment.pop(f"PORTA_SET_{name}", ""):
            environment.pop(name, None)
    if original_xdg_config:
        # The parent Qt process may temporarily use ~/.config to reach IBus
        # when it was launched from portable VS Code.  External applications
        # must instead receive exactly the desktop environment that launched
        # PORTA, including the portable application's XDG location.
        environment["XDG_CONFIG_HOME"] = original_xdg_config
    process = subprocess.Popen(
        [bash, str(application.launcher_path)],
        cwd=str(application.launcher_path.parent),
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    # External applications are independent as soon as their launcher starts.
    # Exclude the launcher PID before the registry's child observer can treat
    # its browser/editor/service descendants as PORTA work.
    get_registry().ignore(process.pid)
