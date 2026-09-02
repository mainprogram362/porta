#!/usr/bin/env bash
# Relocatable PORTA entrypoint. Only the system Python is required initially.

set -u
PORTA_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)" || exit 1
SYSTEM_PYTHON="$(command -v python3 || true)"
if [ -z "$SYSTEM_PYTHON" ]; then
    printf '%s\n' "PORTA: python3が見つかりません。Python 3.11以降を先に導入してください。" >&2
    exit 1
fi
exec "$SYSTEM_PYTHON" "$PORTA_DIR/scripts/bootstrap.py" "$@"
