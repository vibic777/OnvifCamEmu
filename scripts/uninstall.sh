#!/usr/bin/env bash
set -euo pipefail
PREFIX="${PREFIX:-/opt/onvif-emulator}"
if [[ -f /etc/systemd/system/onvif-emulator.service ]]; then
  systemctl disable --now onvif-emulator.service || true
  rm -f /etc/systemd/system/onvif-emulator.service
  systemctl daemon-reload || true
fi
rm -rf "$PREFIX"
echo "Removed $PREFIX"
