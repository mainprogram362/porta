"""Read-only, code-owned environment audit for PORTA."""

from __future__ import annotations

from runtime import managed_process

from dataclasses import dataclass
import importlib.metadata
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Literal

from settings import persistent_settings
from settings import user_space
from media.mpv_player import find_mpv
from media.video_encode import FFmpegTools, validate_tools

from apps.file_tools.file_manager.archive_backends import archive_backend_candidates
from apps.file_tools.file_manager import persistent_settings as file_manager_settings
from apps.media_tools.media_information import settings as media_information_settings
from apps.media_tools.video_encoder import settings as video_encoder_settings
from apps.media_tools.youtube_downloader import settings as youtube_downloader_settings
from apps.porta_control.configuration.templates import (
    ConfigurationTemplate,
    all_configuration_templates,
)
from apps.system_tools.external_app_launcher.discovery import ApplicationLocation, scan_location
from apps.system_tools.external_app_launcher import locations as external_locations
from apps.system_tools.local_ai import settings as local_ai_settings
from apps.system_tools.storage_encryption import settings as storage_encryption_settings


AuditLevel = Literal["ok", "warning", "error", "info"]


@dataclass(frozen=True)
class AuditItem:
    level: AuditLevel
    category: str
    name: str
    detail: str
    path: Path | None = None


@dataclass(frozen=True)
class EnvironmentReport:
    items: tuple[AuditItem, ...]

    def count(self, level: AuditLevel) -> int:
        return sum(item.level == level for item in self.items)


_REQUIRED_DIRECTORIES = (
    ("アプリ実装", Path("src/apps")),
    ("基盤", Path("src/foundation")),
    ("共通画面部品", Path("src/gui")),
    ("メディア基盤", Path("src/media")),
    ("起動スクリプト群", Path("scripts")),
    ("統合バックエンド", Path("integrated_backends")),
)

_REQUIRED_FILES = (
    ("PORTA起動入口", Path("start.sh"), True),
    ("環境構築入口", Path("scripts/bootstrap.py"), False),
    ("GUI起動入口", Path("scripts/main.py"), False),
    ("Python依存関係定義", Path("pyproject.toml"), False),
)

_USER_DIRECTORIES = (
    ("CONFIG", "config"),
    ("ローカルデータ", "local_data"),
    ("AIモデル", "ai_models"),
    ("AI実行系", "ai_runners"),
    ("辞書", "dictionaries"),
    ("雛形", "templates"),
    ("非公開データ", "private"),
    ("キャッシュ", "cache"),
)

_PYTHON_DEPENDENCIES = (
    ("PySide6", "PySide6", "PySide6"),
    ("yt-dlp", "yt_dlp", "yt-dlp"),
    ("PyAutoGUI", "pyautogui", "PyAutoGUI"),
    ("pyperclip", "pyperclip", "pyperclip"),
    ("chardet", "chardet", "chardet"),
)

_FFMPEG_ENCODERS = (
    ("H.264 CPU", "libx264"),
    ("H.265 CPU", "libx265"),
    ("AV1 CPU", "libsvtav1"),
    ("H.264 NVIDIA", "h264_nvenc"),
    ("H.265 NVIDIA", "hevc_nvenc"),
    ("AV1 NVIDIA", "av1_nvenc"),
)


def run_environment_audit(project_root: Path | None = None) -> EnvironmentReport:
    """Inspect paths, formats and executable backends without changing anything."""
    root = (project_root or persistent_settings.PROJECT_ROOT).resolve(strict=False)
    items: list[AuditItem] = []
    _audit_fixed_structure(items, root)
    _audit_python_environment(items, root)
    _audit_user_space(items)
    _audit_configuration(items)
    _audit_configured_references(items)
    _audit_ffmpeg(items, root)
    _audit_archive_backends(items)
    _audit_mpv(items)
    _audit_local_ai(items)
    return EnvironmentReport(tuple(items))


def _add(
    items: list[AuditItem],
    level: AuditLevel,
    category: str,
    name: str,
    detail: str,
    path: Path | None = None,
) -> None:
    items.append(AuditItem(level, category, name, detail, path))


def _audit_fixed_structure(items: list[AuditItem], root: Path) -> None:
    _add(items, "ok" if root.is_dir() else "error", "PORTA本体", "本体ルート", "フォルダを確認しました。" if root.is_dir() else "本体ルートがありません。", root)
    for label, relative in _REQUIRED_DIRECTORIES:
        path = root / relative
        if path.is_dir():
            _add(items, "ok", "PORTA本体", label, "期待するフォルダです。", path)
        elif path.exists() or path.is_symlink():
            _add(items, "error", "PORTA本体", label, "存在しますがフォルダではありません。", path)
        else:
            _add(items, "error", "PORTA本体", label, "必要なフォルダがありません。", path)
    for label, relative, must_be_executable in _REQUIRED_FILES:
        path = root / relative
        if path.is_file():
            executable = os.access(path, os.X_OK)
            if must_be_executable and not executable:
                _add(items, "error", "PORTA本体", label, "ファイルはありますが実行権限がありません。", path)
            else:
                executable_note = " 実行可能です。" if must_be_executable else ""
                _add(items, "ok", "PORTA本体", label, f"ファイルを確認しました。{executable_note}", path)
        elif path.exists() or path.is_symlink():
            _add(items, "error", "PORTA本体", label, "存在しますが通常ファイルではありません。", path)
        else:
            _add(items, "error", "PORTA本体", label, "必要なファイルがありません。", path)


def _audit_python_environment(items: list[AuditItem], root: Path) -> None:
    executable = Path(sys.executable).resolve(strict=False)
    expected = (root / ".venv/bin/python").resolve(strict=False)
    if executable == expected:
        _add(items, "ok", "Python環境", "実行中Python", f"PORTAの仮想環境を使用中です（Python {sys.version.split()[0]}）。", executable)
    else:
        _add(items, "warning", "Python環境", "実行中Python", f"PORTA内の想定Pythonとは異なります（Python {sys.version.split()[0]}）。\n想定: {expected}", executable)

    marker = root / ".venv/.porta-root"
    try:
        recorded = Path(marker.read_text(encoding="utf-8").strip()).resolve(strict=False)
    except OSError as exc:
        _add(items, "warning", "Python環境", "移動検出マーカー", f"読み取れません: {exc}", marker)
    else:
        if recorded == root:
            _add(items, "ok", "Python環境", "移動検出マーカー", "現在のPORTA位置と一致しています。", marker)
        else:
            _add(items, "error", "Python環境", "移動検出マーカー", f"記録位置が現在地と違います。\n記録: {recorded}\n現在: {root}", marker)

    for label, module_name, distribution_name in _PYTHON_DEPENDENCIES:
        try:
            available = importlib.util.find_spec(module_name) is not None
        except (ImportError, ValueError) as exc:
            _add(items, "error", "Python環境", label, f"読込可否を確認できません: {exc}")
            continue
        if not available:
            _add(items, "error", "Python環境", label, "必要なPythonパッケージがありません。")
            continue
        try:
            version = importlib.metadata.version(distribution_name)
        except importlib.metadata.PackageNotFoundError:
            version = "版情報なし"
        _add(items, "ok", "Python環境", label, f"読込可能です（{version}）。")


def _audit_user_space(items: list[AuditItem]) -> None:
    paths = user_space.configured_paths()
    if paths is None:
        _add(items, "warning", "ユーザー領域", "porta_user", "有効な入口設定がないため場所を確定できません。", persistent_settings.BOOTSTRAP_PATH)
        return
    if paths.root.is_dir():
        _add(items, "ok", "ユーザー領域", "porta_user", "ユーザー領域を確認しました。", paths.root)
    elif paths.root.exists() or paths.root.is_symlink():
        _add(items, "error", "ユーザー領域", "porta_user", "設定先は存在しますがフォルダではありません。", paths.root)
    else:
        _add(items, "warning", "ユーザー領域", "porta_user", "場所は設定済みですが、ユーザー領域が未作成です。", paths.root)
    for label, attribute in _USER_DIRECTORIES:
        path = getattr(paths, attribute)
        if path.is_dir():
            _add(items, "ok", "ユーザー領域", label, "期待するフォルダを確認しました。", path)
        elif path.exists() or path.is_symlink():
            _add(items, "error", "ユーザー領域", label, "存在しますがフォルダではありません。", path)
        else:
            _add(items, "warning", "ユーザー領域", label, "期待するフォルダがありません。", path)


def _template_path(template: ConfigurationTemplate) -> Path | None:
    if template.is_bootstrap:
        return persistent_settings.BOOTSTRAP_PATH
    location = persistent_settings.locate_settings_directory()
    if location.directory is None or template.relative_path is None:
        return None
    return location.directory / template.relative_path


def _audit_configuration(items: list[AuditItem]) -> None:
    for template in all_configuration_templates():
        path = _template_path(template)
        template_text = template.template_text()
        base = path.parent if path is not None else persistent_settings.PROJECT_ROOT
        try:
            template.validate(template_text, base)
        except (OSError, ValueError) as exc:
            _add(items, "error", "雛形と代替", template.title, f"内蔵雛形そのものが形式不正です: {exc}", path)
        else:
            _add(items, "ok", "雛形と代替", template.title, "内蔵雛形をメモリ上で検証しました。", path)

        if path is None:
            _add(items, "warning", "設定ファイル", template.title, "CONFIGの場所を確定できないため実ファイルを確認できません。")
            _verify_missing_fallback(items, template, template_text, path)
            continue
        if not path.exists():
            _add(items, "warning", "設定ファイル", template.title, "実ファイルは未作成です。雛形代替の検証結果を併記します。", path)
            _verify_missing_fallback(items, template, template_text, path)
            continue
        if not path.is_file():
            _add(items, "error", "設定ファイル", template.title, "設定先が通常ファイルではありません。", path)
            continue
        try:
            text = path.read_text(encoding="utf-8")
            template.validate(text, path.parent)
        except UnicodeError as exc:
            _add(items, "error", "設定ファイル", template.title, f"UTF-8として読めません: {exc}", path)
        except OSError as exc:
            _add(items, "error", "設定ファイル", template.title, f"読み取れません: {exc}", path)
        except ValueError as exc:
            _add(items, "error", "設定ファイル", template.title, f"内容の形式が不正です: {exc}", path)
        else:
            _add(items, "ok", "設定ファイル", template.title, "実ファイルを読み込み、形式を検証しました。", path)


def _verify_missing_fallback(
    items: list[AuditItem],
    template: ConfigurationTemplate,
    expected: str,
    path: Path | None,
) -> None:
    try:
        actual = template.editable_text()
    except Exception as exc:  # a consumer fallback must not bring down the audit
        _add(items, "error", "雛形と代替", f"{template.title}・未作成時", f"アプリ側の代替読込に失敗しました: {exc}", path)
        return
    if actual == expected:
        _add(items, "ok", "雛形と代替", f"{template.title}・未作成時", "実ファイルなしでも、アプリが内蔵雛形を返すことを確認しました。", path)
    else:
        _add(items, "error", "雛形と代替", f"{template.title}・未作成時", "アプリ側が内蔵雛形と異なる内容を返しました。", path)


def _reference_state(path: Path, expected: Literal["any", "file", "directory"]) -> tuple[AuditLevel, str]:
    if not path.exists():
        return "warning", "設定されていますが、現在は存在しません。"
    if expected == "file" and not path.is_file():
        return "warning", "存在しますが、期待するファイルではありません。"
    if expected == "directory" and not path.is_dir():
        return "warning", "存在しますが、期待するフォルダではありません。"
    kind = "フォルダ" if path.is_dir() else "ファイル"
    return "ok", f"参照先を確認しました（{kind}）。"


def _add_reference(
    items: list[AuditItem],
    name: str,
    value: str | Path,
    expected: Literal["any", "file", "directory"] = "any",
) -> None:
    path = Path(value).expanduser()
    level, detail = _reference_state(path, expected)
    _add(items, level, "設定された参照先", name, detail, path)


def _audit_configured_references(items: list[AuditItem]) -> None:
    """Check effective paths embedded in otherwise valid settings."""
    file_manager = file_manager_settings.load_settings()
    for index, entry in enumerate(file_manager.get("favorite_paths", ()), start=1):
        path = str(entry.get("path", ""))
        if path:
            roles = [
                label
                for key, label in (
                    ("initial_work_list", "初期一覧"),
                    ("favorite", "お気に入り"),
                    ("context_menu", "右クリック"),
                )
                if entry.get(key)
            ]
            _add_reference(items, f"ファイルマネージャー {index}（{'・'.join(roles) or '登録'}）", path)

    encoder = video_encoder_settings.load_settings()
    for index, entry in enumerate(encoder.get("path_settings", ()), start=1):
        path = str(entry.get("path", ""))
        if not path:
            continue
        roles = [
            label
            for key, label in (
                ("initial_source", "初期入力"),
                ("initial_output", "初期出力"),
                ("context_menu", "右クリック"),
            )
            if entry.get(key)
        ]
        expected: Literal["any", "file", "directory"] = "directory" if entry.get("initial_output") else "any"
        _add_reference(items, f"動画エンコード {index}（{'・'.join(roles) or '登録'}）", path, expected)

    downloader = youtube_downloader_settings.load_settings()
    for key, label in (
        ("download_output_directory", "YouTube動画出力先"),
        ("metadata_export_directory", "YouTubeメタデータ出力先"),
    ):
        value = downloader.get(key, "")
        if value:
            _add_reference(items, label, value, "directory")
    catalog_path = downloader.get("catalog_json_path", "")
    if catalog_path:
        catalog = Path(catalog_path)
        level, detail = _reference_state(catalog.parent, "directory")
        suffix_note = "" if catalog.suffix.casefold() == ".json" else " 保存先名が .json ではありません。"
        if suffix_note and level == "ok":
            level = "warning"
        _add(items, level, "設定された参照先", "YouTube台帳JSON保存先", detail + suffix_note, catalog)

    media_information = media_information_settings.load_settings()
    for index, path in enumerate(media_information.get("registered_paths", ()), start=1):
        _add_reference(items, f"メディア情報・登録パス {index}", str(path))

    encryption = storage_encryption_settings.load_settings()
    for index, path in enumerate(encryption.container_favorites, start=1):
        _add_reference(items, f"暗号化コンテナお気に入り {index}", path)
    for index, path in enumerate(encryption.mount_point_favorites, start=1):
        _add_reference(items, f"マウント先お気に入り {index}", path, "directory")
    for preset in encryption.mount_presets:
        _add_reference(items, f"暗号化プリセット「{preset.name}」の対象", preset.container_path)
        _add_reference(items, f"暗号化プリセット「{preset.name}」のマウント先", preset.mount_point, "directory")

    for title, locations in (("外部プログラム置き場", external_locations.load_external_locations()),):
        if locations.state != "ready":
            # The settings-file section already explains a missing or invalid
            # source file.  Avoid reporting the same condition twice here.
            continue
        for index, path in enumerate(locations.directories, start=1):
            _add_reference(items, f"{title} {index}", path, "directory")
            scan = scan_location(ApplicationLocation(index, path.name or str(path), path))
            _add(
                items,
                "ok" if scan.state == "ready" else "warning",
                "検出できるプログラム",
                f"{title} {index}",
                scan.detail,
                path,
            )
            for application in scan.applications:
                executable = os.access(application.launcher_path, os.X_OK)
                _add(
                    items,
                    "ok" if executable else "warning",
                    "検出できるプログラム",
                    application.name,
                    "メニューが検出する実行可能な start.sh を確認しました。"
                    if executable
                    else "Python側では検出しますが、実行権限がありません。",
                    application.launcher_path,
                )


def _run_version(path: Path) -> str:
    result = managed_process.run(
        [str(path), "-version"],
        capture_output=True,
        text=True,
        check=False,
        timeout=8,
        label='実行環境を確認中',
    )
    if result.returncode != 0:
        raise ValueError(f"終了コード {result.returncode}")
    lines = (result.stdout or result.stderr).splitlines()
    return lines[0].strip() if lines else "バージョン文字列なし"


def _check_ffmpeg_pair(
    items: list[AuditItem],
    label: str,
    ffmpeg: Path,
    ffprobe: Path,
    *,
    missing_level: AuditLevel,
) -> FFmpegTools | None:
    if not ffmpeg.is_file() or not ffprobe.is_file():
        _add(items, missing_level, "FFmpeg", label, f"ffmpeg / ffprobe の組が揃っていません。\nffmpeg: {ffmpeg}\nffprobe: {ffprobe}")
        return None
    try:
        tools = validate_tools(ffmpeg, ffprobe)
        ffmpeg_version = _run_version(tools.ffmpeg)
        ffprobe_version = _run_version(tools.ffprobe)
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        _add(items, "error", "FFmpeg", label, f"実行検査に失敗しました: {exc}", ffmpeg)
        return None
    _add(items, "ok", "FFmpeg", label, f"ffmpeg と ffprobe が正常応答しました。\n{ffmpeg_version}\n{ffprobe_version}", ffmpeg)
    return tools


def _audit_ffmpeg(items: list[AuditItem], root: Path) -> None:
    bundled_dir = root / "integrated_backends/ffmpeg/bin"
    bundled = _check_ffmpeg_pair(items, "PORTA同梱版", bundled_dir / "ffmpeg", bundled_dir / "ffprobe", missing_level="error")
    system_ffmpeg = shutil.which("ffmpeg")
    system_ffprobe = shutil.which("ffprobe")
    system = _check_ffmpeg_pair(
        items,
        "OSのPATH版",
        Path(system_ffmpeg or "ffmpeg-not-found"),
        Path(system_ffprobe or "ffprobe-not-found"),
        missing_level="info",
    )

    settings = video_encoder_settings.load_settings()
    configured_ffmpeg = str(settings.get("ffmpeg_path", ""))
    configured_ffprobe = str(settings.get("ffprobe_path", ""))
    configured: FFmpegTools | None = None
    has_configured_pair = bool(configured_ffmpeg) and bool(configured_ffprobe)
    if bool(configured_ffmpeg) != bool(configured_ffprobe):
        _add(items, "error", "FFmpeg", "設定指定", "ffmpeg と ffprobe は2つ揃えて設定する必要があります。")
    elif configured_ffmpeg:
        configured = _check_ffmpeg_pair(items, "設定指定", Path(configured_ffmpeg), Path(configured_ffprobe), missing_level="error")
    else:
        _add(items, "info", "FFmpeg", "設定指定", "個別パスは未指定です。PORTA同梱版、OSのPATH版の順に自動選択します。")

    # The encoder honors an explicit pair even when it is broken; it does not
    # silently replace the user's choice with an auto-detected executable.
    selected = configured if has_configured_pair else bundled or system
    if selected is None:
        _add(items, "error", "FFmpeg", "実際の選択結果", "現在利用できる ffmpeg / ffprobe の組がありません。")
        return
    source = "設定指定" if has_configured_pair else "PORTA同梱版" if bundled is not None else "OSのPATH版"
    _add(items, "ok", "FFmpeg", "実際の選択結果", f"{source}を利用できます。\nffmpeg: {selected.ffmpeg}\nffprobe: {selected.ffprobe}", selected.ffmpeg)
    try:
        result = managed_process.run(
            [str(selected.ffmpeg), "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            check=False,
            timeout=8,
            label='実行環境を確認中',
        )
    except (OSError, subprocess.SubprocessError) as exc:
        _add(items, "error", "FFmpeg", "エンコーダー一覧", f"確認できません: {exc}", selected.ffmpeg)
        return
    if result.returncode != 0:
        _add(items, "error", "FFmpeg", "エンコーダー一覧", f"一覧取得に失敗しました（終了コード {result.returncode}）。", selected.ffmpeg)
        return
    available = result.stdout
    for title, encoder in _FFMPEG_ENCODERS:
        present = encoder in available
        level: AuditLevel = "ok" if present else ("warning" if encoder.startswith("lib") else "info")
        note = "利用候補に含まれています。" if present else "このFFmpegの一覧にはありません。"
        if "NVIDIA" in title:
            note += " 一覧への搭載確認であり、GPU・ドライバの実機成功までは保証しません。"
        _add(items, level, "FFmpegエンコーダー", f"{title}（{encoder}）", note, selected.ffmpeg)


def _audit_archive_backends(items: list[AuditItem]) -> None:
    backends = archive_backend_candidates()
    if not backends:
        _add(items, "error", "バックエンド", "圧縮・展開", "登録されたバックエンドがありません。")
        return
    for backend in backends:
        availability = backend.availability()
        _add(items, "ok" if availability.available else "error", "バックエンド", backend.display_name, availability.detail)


def _audit_mpv(items: list[AuditItem]) -> None:
    settings = media_information_settings.load_settings()
    configured = str(settings.get("mpv_path", ""))
    path = find_mpv(configured)
    if path is None:
        _add(items, "warning", "外部実行系", "mpv", f"設定された実行ファイルを利用できません: {configured}", Path(configured) if configured else None)
        return
    try:
        result = managed_process.run([str(path), "--version"], capture_output=True, text=True, check=False, timeout=8, label='実行環境を確認中')
    except (OSError, subprocess.SubprocessError) as exc:
        _add(items, "warning", "外部実行系", "mpv", f"起動確認に失敗しました: {exc}", path)
        return
    first = (result.stdout or result.stderr).splitlines()
    if result.returncode == 0:
        _add(items, "ok", "外部実行系", "mpv", first[0] if first else "正常応答しました。", path)
    else:
        _add(items, "warning", "外部実行系", "mpv", f"--version に正常応答しません（終了コード {result.returncode}）。", path)


def _audit_local_ai(items: list[AuditItem]) -> None:
    settings = local_ai_settings.load_settings()
    runner_text = settings.get("runner_path", "")
    model_text = settings.get("model_path", "")
    if not runner_text:
        _add(items, "info", "ローカルAI", "llama-server", "未設定です。ローカルAIを使わない場合は問題ありません。")
    else:
        runner = Path(runner_text)
        usable = runner.is_file() and os.access(runner, os.X_OK)
        _add(items, "ok" if usable else "warning", "ローカルAI", "llama-server", "実行可能ファイルを確認しました。" if usable else "設定先を実行可能ファイルとして確認できません。", runner)
    if not model_text:
        _add(items, "info", "ローカルAI", "GGUFモデル", "未設定です。ローカルAIを使わない場合は問題ありません。")
    else:
        model = Path(model_text)
        usable = model.is_file() and model.suffix.casefold() == ".gguf"
        _add(items, "ok" if usable else "warning", "ローカルAI", "GGUFモデル", "GGUFファイルを確認しました。" if usable else "設定先を .gguf ファイルとして確認できません。", model)
