#!/usr/bin/env bash
# Create or recreate the local Python virtualenv (.venv). Never committed.
# Usage: ./scripts/venv.sh            # create if missing, then pip install
#        ./scripts/venv.sh --recreate # delete .venv and make a fresh one
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VENV="$ROOT/.venv"
cd "$ROOT"
RECREATE=0
for arg in "$@"; do
  case "$arg" in
    --recreate|-r) RECREATE=1 ;;
  esac
done

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 not found" >&2
  exit 1
fi

if [[ "$RECREATE" -eq 1 && -d "$VENV" ]]; then
  echo "Removing $VENV"
  rm -rf "$VENV"
fi

if [[ ! -d "$VENV" ]]; then
  echo "Creating $VENV"
  python3 -m venv "$VENV"
fi

"$VENV/bin/python" -m pip install -U pip
"$VENV/bin/python" -m pip install -e ".[dev]"
echo "OK. Activate: source .venv/bin/activate"
echo "This folder is gitignored and is not part of the repository."
