#!/usr/bin/env python3
"""Explicit native-host registration for a conventional Linux Firefox install.

This script is never called by discovery or application startup.
"""
import argparse
import json
import os
from pathlib import Path
import shlex
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--replace", action="store_true", help="既存のPORTA用登録を更新する")
    args = parser.parse_args()
    if not sys.platform.startswith("linux"):
        parser.error("初期版のNative Host登録はLinuxのみ対応します。")
    root = Path(__file__).resolve().parents[1]
    folder = Path.home() / ".mozilla" / "native-messaging-hosts"
    launcher = folder / "porta_browser_launcher.sh"
    manifest = folder / "porta_browser.json"
    for path in (launcher, manifest):
        if path.is_symlink() or (path.exists() and not args.replace):
            parser.error(f"既存ファイルがあります: {path}（更新は--replace）")
    folder.mkdir(parents=True, exist_ok=True)
    text = "#!/bin/sh\nexec " + shlex.quote(sys.executable) + " -u " + shlex.quote(str(root / "scripts/browser_native_host.py")) + ' "$@"\n'
    flags = os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | (os.O_TRUNC if args.replace else os.O_EXCL)
    with os.fdopen(os.open(launcher, flags, 0o700), "w", encoding="utf-8") as stream:
        stream.write(text)
    os.chmod(launcher, 0o700)
    data = {"name": "porta_browser", "description": "PORTA Firefox bridge", "path": str(launcher),
            "type": "stdio", "allowed_extensions": ["porta-browser@porta.local"]}
    with os.fdopen(os.open(manifest, flags, 0o600), "w", encoding="utf-8") as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)
    print(f"登録しました: {manifest}\n拡張機能: {root / 'extensions/porta_firefox/manifest.json'}")
    print("各Firefoxプロファイルで拡張機能を導入し、接続名を付けて接続してください。")


if __name__ == "__main__":
    main()
