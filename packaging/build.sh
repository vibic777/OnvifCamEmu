#!/usr/bin/env bash
# Linux/macOS entry: ./packaging/build.sh linux|win|all
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TARGET="${1:-linux}"
cd "$ROOT"
if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 not found" >&2
  exit 1
fi
exec python3 "$ROOT/packaging/build.py" "$TARGET"
