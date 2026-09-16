"""Short-lived WPC download worker.

This module is intentionally invoked in a separate Python process.  It never
reads a browser profile from the user and receives a parent-created tmpfs
profile path which the parent removes even if this worker is killed.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from yt_dlp import YoutubeDL

from .downloader import (
    _EphemeralWpcProvider,
    _base_options,
    _selected_quality_summary,
    _select_high_quality_format,
    _summary_is_no_lower_quality,
    _wpc_options,
)


def _emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False), flush=True)


def _read_request() -> dict[str, Any]:
    raw = sys.stdin.readline()
    if not raw:
        raise ValueError("WPCワーカーへの入力がありません。")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("WPCワーカーへの入力形式が不正です。")
    return value


def main() -> int:
    try:
        request = _read_request()
        url = str(request["url"])
        output_directory = Path(str(request["output_directory"])).expanduser()
        browser_path = Path(str(request["browser_path"]))
        profile_path = Path(str(request["profile_path"]))
        reference = request["reference"]
        operation = str(request.get("operation") or "download")
        if not isinstance(reference, dict):
            raise ValueError("比較用の最高画質情報が不正です。")
        options: dict[str, Any] = {
            **_base_options(),
            **_wpc_options(browser_path),
            "skip_download": True,
            "noplaylist": True,
        }
        with _EphemeralWpcProvider(browser_path, profile_path=profile_path):
            with YoutubeDL(options) as ydl:
                info = ydl.extract_info(url, download=False)
            if not isinstance(info, dict):
                raise RuntimeError("一時ブラウザ経路で動画情報を取得できませんでした。")
            selection = _select_high_quality_format(info)
            summary = _selected_quality_summary(info, selection) if selection else None
            if selection is None or summary is None or not _summary_is_no_lower_quality(summary, reference):
                _emit({"state": "error", "error": "一時ブラウザ経路は最高画質を維持できません。"})
                return 0
            _emit({"state": "ready", "selector": selection.selector})
            if operation == "probe":
                _emit({"state": "complete", "selector": selection.selector})
                return 0
            if operation != "download":
                raise ValueError("WPCワーカーの操作種別が不正です。")
            options = {
                **_base_options(),
                **_wpc_options(browser_path),
                "outtmpl": str(output_directory / "%(title).180B [%(id)s].%(ext)s"),
                "format": selection.selector,
                "merge_output_format": "mp4",
                "noplaylist": True,
                "overwrites": False,
                "continuedl": False,
            }
            with YoutubeDL(options) as ydl:
                ydl.download([url])
        _emit({"state": "complete", "selector": selection.selector})
        return 0
    except Exception as exc:
        _emit({"state": "error", "error": str(exc) or exc.__class__.__name__})
        return 0


if __name__ == "__main__":
    from runtime.instance_presence import InstancePresence

    presence = InstancePresence(role="worker", screen="動画ダウンロード補助", state="処理中")
    try:
        raise SystemExit(main())
    finally:
        presence.close()
