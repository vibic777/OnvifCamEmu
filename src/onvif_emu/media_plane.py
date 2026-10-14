"""Media plane: MediaMTX + per-channel FFmpeg pipelines."""

from __future__ import annotations

import asyncio
import hashlib
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from onvif_emu.config import beside_exe_dir
from onvif_emu.models import AuthMode
from onvif_emu.pipeline.manager import PipelineManager
from onvif_emu.pipeline.mediamtx import MediaMtx, find_mediamtx, write_mtx_config
from onvif_emu.procguard import free_rtsp_port, resolve_mtx_ports

if TYPE_CHECKING:
    from onvif_emu.runtime import Runtime

log = logging.getLogger(__name__)


def _users_sig(users) -> tuple:
    """Stable fingerprint of credentials without retaining plaintext in signature logs."""
    out = []
    for u in users or ():
        name = getattr(u, "username", "") or ""
        pwd = getattr(u, "password", "") or ""
        role = str(getattr(u, "role", "") or "")
        digest = hashlib.sha256(f"{name}:{pwd}".encode()).hexdigest()[:16]
        out.append((name, digest, role, tuple(getattr(u, "channels", ()) or ())))
    return tuple(out)


class MediaPlane:
    def __init__(self, runtime: Runtime):
        self.runtime = runtime
        self.mediamtx: MediaMtx | None = None  # the device-wide RTSP port
        self.extra_mtx: dict[int, MediaMtx] = {}  # one more instance per distinct channel RTSP port
        self.pipelines: PipelineManager | None = None
        self.error = ""  # why RTSP is off (port taken, ...); shown in /health and the GUI
        self.hls_port = runtime.config.mediamtx.hls_port  # effective (may move off a busy port)
        # account the web preview proxy reads HLS with (loopback only, new every run)
        import secrets

        self.internal_creds = ("emu-preview-" + secrets.token_hex(4), secrets.token_urlsafe(18))
        self._written: list[tuple[Path, tuple, dict]] = []  # config files, for refresh_users()
        self.port_notes: list[str] = []

    def channel_ports(self) -> list[int]:
        """Distinct RTSP ports used by channels that override the device-wide port."""
        cfg = self.runtime.config
        main = cfg.network.rtsp_port
        return sorted({c.rtsp_port for c in cfg.channels if c.rtsp_port and c.rtsp_port != main})

    def signature(self) -> tuple:
        cfg = self.runtime.config
        m = cfg.mediamtx
        users = self.runtime.identity.users if self.runtime.identity else cfg.users
        return (
            cfg.network.rtsp_port,
            tuple(self.channel_ports()),
            tuple((c.id, c.rtsp_path) for c in cfg.channels),
            cfg.instance,
            m.embedded,
            m.binary,
            m.publish_host,
            m.api_port,
            m.hls_port,
            m.webrtc_port,
            cfg.auth.mode.value,
            _users_sig(users),
        )

    @property
    def mtx_alive(self) -> bool:
        return bool(self.mediamtx and self.mediamtx.alive)

    async def start(self) -> None:
        log.info("Media plane starting")
        await self._ensure_mediamtx()
        self.pipelines = PipelineManager(self.runtime)
        await self.pipelines.start()
        log.info("Media plane started mtx=%s", self.mtx_alive)

    async def stop(self) -> None:
        log.info("Media plane stopping")
        if self.pipelines:
            await self.pipelines.stop()
            self.pipelines = None
        if self.mediamtx:
            await self.mediamtx.stop()
            self.mediamtx = None
        for mtx in self.extra_mtx.values():
            await mtx.stop()
        self.extra_mtx.clear()
        log.info("Media plane stopped")

    async def _ensure_mediamtx(self) -> None:
        cfg = self.runtime.config
        mtx_bin = cfg.mediamtx.binary or find_mediamtx() or ""
        # one file per RTSP port: two emulators started from the same folder must not rewrite each
        # other's config (MediaMTX hot-reloads it); the name also identifies our own leftovers
        cfg_path = beside_exe_dir() / "data" / f"mediamtx_{cfg.network.rtsp_port}.yml"
        m = cfg.mediamtx
        api, hls, webrtc, slot = m.api_port, m.hls_port, m.webrtc_port, (cfg.instance - 1) * 10
        self.error = ""
        self.port_notes = []
        if mtx_bin and m.embedded:
            reason = await asyncio.to_thread(free_rtsp_port, cfg.network.rtsp_port, cfg_path, m.publish_host)
            if reason:
                self.error = reason
                log.critical(reason)
                self.mediamtx = MediaMtx(None, cfg_path, enabled=False)
                return
            api, hls, webrtc, slot, self.port_notes = await asyncio.to_thread(
                resolve_mtx_ports, api, hls, webrtc, slot, cfg.network.rtsp_port)
            for note in self.port_notes:
                log.critical("MediaMTX: %s (used by another program or emulator)", note)
        self.hls_port = hls
        self._written = []
        try:
            self._write(cfg_path, (cfg.network.rtsp_port, api, hls, webrtc), {"slot": slot})
        except OSError as exc:
            log.warning(
                "Cannot write MediaMTX config %s: %s — RTSP off, ONVIF/GUI still start",
                cfg_path,
                exc,
            )
            self.mediamtx = MediaMtx(None, cfg_path, enabled=False)
            return
        self.mediamtx = MediaMtx(mtx_bin or None, cfg_path, enabled=cfg.mediamtx.embedded)
        if not await self.mediamtx.start() and mtx_bin and m.embedded:
            self.error = "MediaMTX failed to start: " + (self.mediamtx.last_output() or "no output")
            log.critical(self.error)
        # streams on their own ports: a dedicated MediaMTX each (no HLS/WebRTC, API port after the main one)
        for k, port in enumerate(self.channel_ports(), start=1):
            extra_cfg = cfg_path.with_name(f"{cfg_path.stem}_p{port}.yml")
            try:
                self._write(extra_cfg, (port, api + k, hls + k, webrtc + k),
                            {"hls": False, "webrtc": False, "slot": slot + k})
            except OSError as exc:
                log.warning("Cannot write MediaMTX config %s: %s", extra_cfg, exc)
                continue
            if mtx_bin and cfg.mediamtx.embedded:
                reason = await asyncio.to_thread(free_rtsp_port, port, extra_cfg, cfg.mediamtx.publish_host)
                if reason:
                    self.error = (self.error + " " + reason).strip()
                    log.critical(reason)
                    continue
            mtx = MediaMtx(mtx_bin or None, extra_cfg, enabled=cfg.mediamtx.embedded)
            await mtx.start()
            self.extra_mtx[port] = mtx
            log.info("Extra RTSP port %s on its own MediaMTX", port)

    def _channel_paths(self) -> dict[int, list[str]]:
        return {c.id: [c.rtsp_path + s for s in ("", "_sub", "_third")] for c in self.runtime.config.channels}

    def _write(self, path: Path, ports: tuple, extra: dict) -> None:
        cfg = self.runtime.config
        users = self.runtime.identity.users if self.runtime.identity else []
        write_mtx_config(
            path, *ports, users=users, auth_required=cfg.auth.mode != AuthMode.none,
            channel_paths=self._channel_paths(), internal=self.internal_creds, **extra,
        )
        self._written.append((path, ports, extra))

    def refresh_users(self) -> None:
        """Accounts changed: rewrite the MediaMTX files; MediaMTX reloads them without dropping streams."""
        written, self._written = self._written, []
        for path, ports, extra in written:
            try:
                self._write(path, ports, extra)
            except OSError as exc:
                log.warning("Cannot rewrite MediaMTX config %s: %s", path, exc)

    async def restart_mediamtx(self) -> None:
        if self.mediamtx:
            await self.mediamtx.stop()
            self.mediamtx = None
        for mtx in self.extra_mtx.values():
            await mtx.stop()
        self.extra_mtx.clear()
        await self._ensure_mediamtx()

    async def restart_pipelines(self) -> None:
        if self.pipelines:
            await self.pipelines.restart()

    async def restart_channel(self, channel_id: int) -> None:
        if self.pipelines:
            await self.pipelines.restart_channel(channel_id)
