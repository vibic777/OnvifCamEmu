from __future__ import annotations

import asyncio
import contextvars
import logging
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any
from urllib.parse import quote

from onvif_emu.analytics.simulator import AnalyticsSimulator
from onvif_emu.config import appdata_dir, beside_exe_dir, dump_yaml
from onvif_emu.events.bus import EventBus
from onvif_emu.events.generator import EventGenerator
from onvif_emu.identity import IdentityStore
from onvif_emu.media_plane import MediaPlane
from onvif_emu.models import AppConfig, ChannelConfig
from onvif_emu.ptz.servo import PtzServo
from onvif_emu.state import StateStore
from onvif_emu.util import detect_public_host

# Host the current HTTP request was addressed to; advertised in XAddr/RTSP URIs when public_host is not set.
# A real camera has one address; the emulator answers with the one the client actually reached it on.
REQUEST_HOST: contextvars.ContextVar[str] = contextvars.ContextVar("request_host", default="")

log = logging.getLogger(__name__)

TICK_DT = 0.05  # 20 Hz servo/analytics — smooth enough, cheap enough

# SystemReboot stops this process after this delay. A supervisor (systemd/Docker)
# with restart policy can bring it back; there is no in-process OS reboot.
REBOOT_DELAY_S = 3.0


class Runtime:
    def __init__(self, config: AppConfig, config_path: Path | None = None):
        self.config = config
        self.config_path = config_path
        self._public_host = config.public_host or ""
        self.state = StateStore(self._state_path())
        self.identity = IdentityStore(self.state)
        self.media = MediaPlane(self)
        self.events = EventBus()
        self.analytics = AnalyticsSimulator(config.analytics)
        self.analytics_sims: dict[int, AnalyticsSimulator] = {}
        from onvif_emu.diag import DiagEngine

        self.diag = DiagEngine(self)
        self.servos: dict[int, PtzServo] = {}
        self.generator: EventGenerator | None = None
        self._stop = asyncio.Event()
        self.config_lock = asyncio.Lock()
        self.snapshot_slots = asyncio.Semaphore(2)
        self._reboot_requested = False
        self.started_monotonic = 0.0
        self.logs: list[str] = []
        self.video_dir: Path | None = None
        self._bg_tasks: list[asyncio.Task] = []
        # Set by AppFacade: async (host, port) -> error text or "" once listening there.
        self.rebind: Callable[[str, int], Awaitable[str]] | None = None
        # Set by Fleet: called instead of writing the config file (the fleet writes the shared root file).
        self.save_hook: Callable[[AppConfig], None] | None = None
        self.fleet = None
        self.shed_level = 0  # set by the load governor: 1 = low streams off, 2 = low + medium off

    # --- backward-compatible aliases for media plane ---
    @property
    def mediamtx(self):
        return self.media.mediamtx

    @property
    def pipelines(self):
        return self.media.pipelines

    def _state_path(self) -> Path:
        if self.config.state_path:
            return Path(self.config.state_path)
        if self.config.config_dir_choice == "appdata":
            d = appdata_dir()
        else:
            d = beside_exe_dir() / "data"
        d.mkdir(parents=True, exist_ok=True)
        return d / "state.json"

    def public_host(self) -> str:
        if self._public_host:
            return self._public_host
        seen = REQUEST_HOST.get()
        if seen:
            return seen
        self._public_host = detect_public_host(self.config.host)
        return self._public_host

    def base_url(self) -> str:
        return f"http://{self.public_host()}:{self.config.onvif_port}"

    def effective_scopes(self) -> list[str]:
        return self.identity.effective_scopes(self.config)

    def enabled_channels(self) -> list[ChannelConfig]:
        """Enabled channels the account of the current ONVIF request may see (all outside a request)."""
        from onvif_emu.access import sees

        return [c for c in self.config.channels if c.enabled and sees(c.id)]

    def stream_uri(self, ch: ChannelConfig, sub: bool | int = False, multicast: bool = False) -> str:
        """RTSP URL of a channel's stream. ``sub`` is the tier: 0 full, 1 medium, 2 low."""
        path = ch.rtsp_path + ("", "_sub", "_third")[int(sub)]
        host = self.public_host()
        port = ch.rtsp_port or self.config.network.rtsp_port
        auth = ""
        if (
            self.config.auth.embed_rtsp_credentials
            and self.config.auth.mode.value != "none"
        ):
            from onvif_emu.access import current_user

            who = current_user() or self.state.users.find("admin")  # the asking account's own credentials
            if who is not None:
                auth = f"{quote(who.username, safe='')}:{quote(who.password, safe='')}@"
        if multicast:
            return f"rtsp://{auth}{host}:{port}/{path}?multicast=1"
        return f"rtsp://{auth}{host}:{port}/{path}"

    def analytics_for(self, channel_id: int) -> AnalyticsSimulator:
        """Every channel has its own object simulator (channel 1 shares rt.analytics)."""
        sim = self.analytics_sims.get(channel_id)
        if sim is None:
            first = self.config.channels[0].id if self.config.channels else 1
            if channel_id == first:
                sim = self.analytics
            else:
                sim = AnalyticsSimulator(self.config.analytics)
                sim.seed(42 + channel_id)
            self.analytics_sims[channel_id] = sim
        return sim

    def channel_ptz(self, channel_id: int):
        """PTZ settings of one channel: its own block, else the device-wide one."""
        for ch in self.config.channels:
            if ch.id == channel_id:
                return ch.ptz or self.config.ptz
        return self.config.ptz

    def ptz_channels(self) -> list[ChannelConfig]:
        return [c for c in self.enabled_channels() if c.ptz_enabled]

    def ensure_servo(self, channel_id: int) -> PtzServo:
        if channel_id not in self.servos:
            servo = PtzServo(channel_id, self.channel_ptz(channel_id))
            st = self.state.channel(channel_id)
            servo.restore(st.pan, st.tilt, st.zoom, st.iris, st.focus)
            self.servos[channel_id] = servo
        return self.servos[channel_id]

    def persist_ptz(self, channel_id: int) -> None:
        servo = self.servos.get(channel_id)
        if not servo:
            return
        st = self.state.channel(channel_id)
        d = servo.snapshot_dict()
        st.pan, st.tilt, st.zoom, st.iris, st.focus = d["pan"], d["tilt"], d["zoom"], d["iris"], d["focus"]
        self.state.mark_dirty()

    def request_reboot(self) -> None:
        """Schedule process stop (ONVIF SystemReboot). Not an OS reboot."""
        self._reboot_requested = True
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            self._stop.set()
            return
        loop.call_later(REBOOT_DELAY_S, self._stop.set)

    def request_stop(self) -> None:
        """Stop the process immediately (tray icon / GUI 'Exit', not an ONVIF reboot)."""
        self._stop.set()

    def wait_stopped(self):
        return self._stop.wait()

    def save_config(self) -> None:
        if self.save_hook is not None:
            self.save_hook(self.config)
        elif self.config_path:
            suffix = self.config_path.suffix.lower()
            if suffix == ".conf":
                from onvif_emu.confdump import write_conf

                write_conf(self.config, self.config_path, self.config.language)
            else:
                dump_yaml(self.config, self.config_path)
        self.state.save()

    def _scrub_config_passwords(self) -> None:
        """An old config still carries plain passwords: they now live encrypted in users.json - rewrite it without."""
        import re

        path = self.config_path
        try:
            text = path.read_text(encoding="utf-8") if path and path.is_file() else ""
        except OSError:
            return
        if re.search(r"^\s*[\"']?password[\"']?\s*:", text, re.M):
            log.warning("Plain passwords removed from %s (accounts are encrypted in data/users.json)", path)
            self.save_config()

    async def start_background(self) -> None:
        log.info("Runtime start channels=%s discovery=%s", len(self.config.channels), self.config.discovery)
        self.started_monotonic = time.monotonic()
        self.state.load(self.config)
        self._scrub_config_passwords()
        self.analytics.seed(42)
        for ch in self.config.channels:
            self.ensure_servo(ch.id)
            self.analytics_for(ch.id)
        await self.media.start()
        self.generator = EventGenerator(self)
        await self.generator.start()
        await self.diag.start()
        self._bg_tasks = [
            asyncio.create_task(self._tick(), name="servo-tick"),
            asyncio.create_task(self._persist_loop(), name="state-flush"),
        ]
        from onvif_emu.videos import resolve_video_dir

        self.video_dir = resolve_video_dir(self.config.video_dir)
        log.info("Video library: %s", self.video_dir)

    async def restart_mediamtx(self) -> None:
        await self.media.restart_mediamtx()

    def media_stack_signature(self) -> tuple:
        return self.media.signature()

    async def stop(self) -> None:
        log.info("Runtime stopping reboot=%s", self._reboot_requested)
        self._stop.set()
        for task in self._bg_tasks:
            task.cancel()
        for task in self._bg_tasks:
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        self._bg_tasks.clear()
        if self.generator:
            await self.generator.stop()
        await self.diag.stop()
        await self.media.stop()
        self.state.save()
        log.info("Runtime stopped")

    async def _tick(self) -> None:
        last = time.monotonic()
        while not self._stop.is_set():
            now = time.monotonic()
            dt = min(0.2, max(0.0, now - last))
            last = now
            for cid, servo in self.servos.items():
                if servo.step(dt, now=now):
                    self.persist_ptz(cid)
            self.analytics.step(dt)
            for sim in self.analytics_sims.values():
                if sim is not self.analytics:
                    sim.step(dt)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=TICK_DT)
            except TimeoutError:
                continue

    async def _persist_loop(self) -> None:
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=2.0)
            except TimeoutError:
                try:
                    await asyncio.to_thread(self.state.save_if_dirty)
                except OSError:
                    # Keep dirty state and retry; a full disk must not kill persistence.
                    log.exception("State save failed; will retry")

    def health(self) -> dict[str, Any]:
        pipes = {}
        states = {}
        if self.pipelines:
            for cid, p in self.pipelines.pipes.items():
                pipes[str(cid)] = bool(p.main and p.main.alive)
                states[str(cid)] = getattr(p, "state", "")
        return {
            "status": "ok",
            "uptime_s": round(time.monotonic() - self.started_monotonic, 1) if self.started_monotonic else 0,
            "channels": len(self.config.channels),
            "enabled": len(self.enabled_channels()),
            "auth": self.config.auth.mode.value,
            "discovery": self.config.discovery,
            "pipelines": pipes,
            "pipeline_state": states,  # running | ended (clip played once) | outage (simulated) | starting
            "mediamtx": bool(self.media and self.media.mtx_alive),
            "mediamtx_error": self.media.error if self.media else "",
            "mediamtx_ports": self.media.port_notes if self.media else [],
            "reboot_requested": self._reboot_requested,
            "subscriptions": self.events.count(),
            "version": __import__("onvif_emu").__version__,
        }
