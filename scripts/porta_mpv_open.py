#!/usr/bin/env python3
"""Desktop-entry relay for ordinary mpv replacement launches."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Open local media in PORTA's normal mpv player.")
    parser.add_argument("paths", nargs="+", metavar="PATH")
    arguments = parser.parse_args(argv)
    from media.mpv_player import launch_normal_mpv

    paths: list[Path] = []
    for raw in arguments.paths:
        if raw.startswith("file://"):
            from urllib.parse import unquote, urlparse

            parsed = urlparse(raw)
            if parsed.netloc not in ("", "localhost"):
                parser.error("ローカルファイルだけを開けます。")
            paths.append(Path(unquote(parsed.path)))
        else:
            paths.append(Path(raw))
    try:
        launch_normal_mpv(paths)
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"mpvを開けません: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
