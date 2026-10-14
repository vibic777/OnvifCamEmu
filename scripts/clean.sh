#!/usr/bin/env bash
# Native Linux/macOS cleaner. Falls back to Python if present.
# Usage: ./scripts/clean.sh [--dry-run] [--all] [--venv] [--keep-vendor]
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DRY=0
VENV=0
VENDOR=1
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY=1 ;;
    --all|--venv) VENV=1 ;;
    --keep-vendor) VENDOR=0 ;;
    -h|--help)
      echo "Usage: $0 [--dry-run] [--all] [--venv] [--keep-vendor]"
      exit 0
      ;;
  esac
done

if command -v python3 >/dev/null 2>&1; then
  extra=()
  [[ "$DRY" -eq 1 ]] && extra+=(--dry-run)
  [[ "$VENV" -eq 1 ]] && extra+=(--venv)
  [[ "$VENDOR" -eq 0 ]] && extra+=(--keep-vendor)
  exec python3 "$ROOT/scripts/clean.py" --root "$ROOT" "${extra[@]}"
fi

# Native fallback (no Python)
log() { if [[ "$DRY" -eq 1 ]]; then echo "[dry-run] $1"; else echo "[del] $1"; fi; }
rm_path() {
  local p="$1"
  [[ -e "$p" ]] || return 0
  log "${p#"$ROOT"/}"
  [[ "$DRY" -eq 1 ]] && return 0
  rm -rf "$p"
}

while IFS= read -r -d '' d; do
  rm_path "$d"
done < <(find "$ROOT" \( -name .git -o -name node_modules \) -prune -o \
  \( -type d \( -name __pycache__ -o -name .pytest_cache -o -name .mypy_cache -o -name .ruff_cache \
     -o -name build -o -name dist -o -name htmlcov -o -name .eggs -o -name logs -o -name tmp -o -name .tox \) -print0 \) \
  2>/dev/null || true)

while IFS= read -r -d '' f; do
  rm_path "$f"
done < <(find "$ROOT" \( -name .git -o -name node_modules \) -prune -o \
  \( -type f \( -name '*.pyc' -o -name '*.pyo' -o -name '_gen_*.spec' \) -print0 \) 2>/dev/null || true)

rm_path "$ROOT/.coverage"
rm_path "$ROOT/data/state.json"
rm_path "$ROOT/packaging/_gen_win.spec"
rm_path "$ROOT/packaging/_gen_linux.spec"
if [[ "$VENDOR" -eq 1 ]]; then
  rm_path "$ROOT/vendor/cache"
  rm_path "$ROOT/vendor/win64"
  rm_path "$ROOT/vendor/linux64"
fi
if [[ "$VENV" -eq 1 ]]; then
  rm_path "$ROOT/.venv"
  rm_path "$ROOT/venv"
fi
echo "clean.sh done"
