#!/usr/bin/env bash
# Install ONVIF emulator system-wide (Linux). Prefer a venv so imports work.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PREFIX="${PREFIX:-/opt/onvif-emulator}"
PYTHON="${PYTHON:-python3}"

echo "Installing onvif-emulator into $PREFIX"
mkdir -p "$PREFIX/bin" "$PREFIX/config" "$PREFIX/data"

if ! command -v "$PYTHON" >/dev/null 2>&1; then
  echo "ERROR: $PYTHON not found" >&2
  exit 1
fi

"$PYTHON" -m venv "$PREFIX/venv"
# shellcheck disable=SC1091
source "$PREFIX/venv/bin/activate"
pip install --upgrade pip
pip install "$ROOT"

# Wrapper so PATH users get the venv entry point
cat > "$PREFIX/bin/onvif-emulator" <<EOF
#!/usr/bin/env bash
exec "$PREFIX/venv/bin/onvif-emulator" "\$@"
EOF
chmod 0755 "$PREFIX/bin/onvif-emulator"

# Sample config (do not overwrite existing)
if [[ ! -f "$PREFIX/config/default.yaml" ]] && [[ -f "$ROOT/config/default.yaml" ]]; then
  cp "$ROOT/config/default.yaml" "$PREFIX/config/default.yaml"
elif [[ ! -f "$PREFIX/config/default.yaml" ]] && [[ -f "$ROOT/src/onvif_emu/data/default.yaml" ]]; then
  cp "$ROOT/src/onvif_emu/data/default.yaml" "$PREFIX/config/default.yaml"
fi

if [[ "${1:-}" == "--systemd" ]]; then
  if [[ "$(id -u)" -ne 0 ]]; then
    echo "ERROR: --systemd requires root" >&2
    exit 1
  fi
  UNIT=/etc/systemd/system/onvif-emulator.service
  cat > "$UNIT" <<EOF
[Unit]
Description=ONVIF Camera Emulator
After=network.target

[Service]
Type=simple
WorkingDirectory=$PREFIX
ExecStart=$PREFIX/bin/onvif-emulator --service -c $PREFIX/config/default.yaml
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF
  systemctl daemon-reload
  systemctl enable --now onvif-emulator.service
  echo "systemd unit installed: $UNIT"
fi

echo "Done."
echo "  Run:  $PREFIX/bin/onvif-emulator --help"
echo "  Or:   $PREFIX/venv/bin/onvif-emulator --service"
