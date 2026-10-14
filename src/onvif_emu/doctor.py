from __future__ import annotations

import logging
import socket

from onvif_emu import __version__
from onvif_emu.exit_codes import MISSING_DEPENDENCY, OK, PORT_IN_USE
from onvif_emu.models import AppConfig
from onvif_emu.pipeline.ffmpeg import find_ffmpeg
from onvif_emu.pipeline.mediamtx import find_mediamtx
from onvif_emu.util import port_in_use

log = logging.getLogger(__name__)


def run_doctor(cfg: AppConfig) -> tuple[int, str]:
    log.info("Doctor start port=%s", cfg.onvif_port)
    lines = [f"onvif-emulator doctor v{__version__}", ""]
    code = OK
    ff = find_ffmpeg(cfg.ffmpeg.binary)
    if ff:
        lines.append(f"[ok]  FFmpeg: {ff}")
    else:
        lines.append("[!!] FFmpeg not found — pipelines will idle, snapshots use fallback JPEG")
        code = max(code, MISSING_DEPENDENCY)

    mtx = cfg.mediamtx.binary or find_mediamtx()
    if mtx:
        lines.append(f"[ok]  MediaMTX: {mtx}")
    else:
        lines.append("[!!] MediaMTX not found — RTSP publish skipped (URIs still advertised)")
        code = max(code, MISSING_DEPENDENCY)

    checks = [
        ("ONVIF HTTP", cfg.host, cfg.onvif_port, False),
        ("RTSP", "0.0.0.0", cfg.network.rtsp_port, False),
    ]
    if cfg.discovery:
        checks.append(("WS-Discovery UDP", "0.0.0.0", 3702, True))
    for name, host, port, udp in checks:
        busy = port_in_use(host, port, udp=udp)
        if busy:
            lines.append(f"[!!] {name} port {port} is in use")
            code = PORT_IN_USE
        else:
            lines.append(f"[ok]  {name} port {port} is free")

    # multicast membership is best-effort
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.close()
        lines.append("[ok]  UDP sockets available")
    except OSError as exc:
        lines.append(f"[!!] UDP sockets: {exc}")

    lines.append("")
    lines.append(f"auth={cfg.auth.mode.value} channels={len(cfg.channels)} gui={cfg.gui}")
    lines.append("See --help for launch examples.")
    text = "\n".join(lines) + "\n"
    log.info("Doctor done code=%s", code)
    return code, text
