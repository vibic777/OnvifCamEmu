from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import TYPE_CHECKING

from onvif_emu.config import beside_exe_dir
from onvif_emu.pipeline.backoff import FullJitterBackoff
from onvif_emu.pipeline.ffmpeg import FfmpegProcess, build_command, find_ffmpeg
from onvif_emu.pipeline.osd import imaging_eq

if TYPE_CHECKING:
    from onvif_emu.runtime import Runtime

log = logging.getLogger(__name__)

# Reset reconnect backoff only after the process stayed up this long.
HEALTHY_S = 10.0


class ChannelPipeline:
    def __init__(self, runtime: Runtime, channel_id: int):
        self.runtime = runtime
        self.channel_id = channel_id
        self.main: FfmpegProcess | None = None
        self.sub: FfmpegProcess | None = None
        self.third: FfmpegProcess | None = None
        self.task: asyncio.Task | None = None
        self.backoff = FullJitterBackoff()
        self._last_sig = None
        self._stop = asyncio.Event()
        self.feeder = None
        self._overlay_failures = 0
        self.state = "starting"  # running | ended (clip played once) | outage (simulated link down) | idle
        self.outage_until = 0.0
        self._tamper_on = False

    def channel(self):
        for ch in self.runtime.config.channels:
            if ch.id == self.channel_id:
                return ch
        return None

    async def start(self) -> None:
        self._stop.clear()
        self.task = asyncio.create_task(self._run(), name=f"pipe-{self.channel_id}")

    async def stop(self) -> None:
        self._stop.set()
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except (asyncio.CancelledError, Exception):
                pass
            self.task = None
        # Stop the producer first: otherwise it can launch a new child while
        # shutdown is awaiting the previous child's exit.
        await self._stop_procs()

    def current_vf(self) -> str:
        """Static video filter: imaging correction only (nothing when every slider is neutral).

        The OSD is NOT part of it: it is a live overlay fed through stdin (see overlay.py).
        """
        img = self.runtime.config.imaging
        if (img.brightness, img.contrast, img.saturation, img.sharpness) == (50, 50, 50, 50):
            return ""
        return imaging_eq(img)

    def _osd_sig_now(self) -> tuple:
        """What forces an encoder restart: overlay on/off and imaging. Style changes are live."""
        rt = self.runtime
        img = rt.config.imaging
        return (rt.config.osd.enabled, img.brightness, img.contrast, img.saturation, img.sharpness)

    async def _must_encode(self, ch, ffmpeg: str) -> bool:
        """File sources are remuxed only when they already carry the advertised codec (H.264/H.265).

        MPEG-2, MPEG-4 Part 2 (Xvid/DivX), WMV, VP9, ProRes... are re-encoded, otherwise clients that
        were promised H.264 get a stream they cannot decode.
        """
        from onvif_emu.models import SourceType
        from onvif_emu.videos import can_copy, find_ffprobe, probe_video

        if ch.source.type != SourceType.file or ch.source.transcode:
            return False
        try:
            info = await asyncio.to_thread(probe_video, ch.source.uri, find_ffprobe(ffmpeg))
        except FileNotFoundError:
            return False  # no ffprobe: keep the old behaviour
        if info is None:
            log.warning("Channel %s: no video stream in %s (or unreadable)", ch.id, ch.source.uri)
            return True
        ok = can_copy(info, ch.encoder.codec.value)
        log.info("Channel %s file %s: %s %sx%s -> %s", ch.id, ch.source.uri, info["codec"], info["width"],
                 info["height"], "remux" if ok else f"re-encode to {ch.encoder.codec.value}")
        return not ok

    async def _stop_procs(self) -> None:
        if self.feeder:
            await self.feeder.stop()
            self.feeder = None
        for p in (self.main, self.sub, self.third):
            if p:
                await p.stop()

    def _clip_ended(self, ch) -> bool:
        """The main encoder finished cleanly on a file that must not loop."""
        from onvif_emu.models import SourceType

        proc = self.main.proc if self.main else None
        return bool(
            ch.source.type == SourceType.file and not ch.source.loop and ch.source.end_action == "stop"
            and proc is not None and proc.returncode == 0
        )

    @staticmethod
    def _next_outage(ch, now: float) -> float:
        imp = ch.impair
        if not (imp.enabled and imp.outage):
            return 0.0
        scale = {"seconds": 1.0, "minutes": 60.0, "hours": 3600.0}[imp.outage_unit]
        lo, hi = sorted((imp.outage_every_min, imp.outage_every_max))
        return now + max(1.0, random.uniform(lo, hi) * scale)

    async def _tamper_tick(self, ch, elapsed: float | None) -> None:
        """Raise / clear tns1:VideoSource/Tampering in step with shake / cover bursts (elapsed None = clear)."""
        from onvif_emu.pipeline.effects import burst_active

        imp = ch.impair
        want = False
        if elapsed is not None and imp.enabled and imp.tamper_events:
            want = (imp.shake > 0 and burst_active(imp.shake_burst, elapsed)) or (
                imp.cover != "off" and burst_active(imp.cover_burst, elapsed))
        if want == self._tamper_on:
            return
        self._tamper_on = want
        diag = getattr(self.runtime, "diag", None)
        if diag is None:
            return
        from onvif_emu.models import EventRule, RuleKind

        try:
            await diag.fire(EventRule(kind=RuleKind.tamper, name="impair", channel=ch.id, action="on" if want else "off"))
        except Exception:  # noqa: BLE001 - events must never break the video
            log.debug("tamper event failed", exc_info=True)

    async def _run(self) -> None:
        ffmpeg = find_ffmpeg(self.runtime.config.ffmpeg.binary)
        if not ffmpeg:
            log.warning("FFmpeg not found — pipelines idle for channel %s", self.channel_id)
            await self._stop.wait()
            return
        while not self._stop.is_set():
            ch = self.channel()
            if not ch or not ch.enabled:
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=1.0)
                except TimeoutError:
                    continue
                continue
            vf = self.current_vf()
            self._last_sig = self._osd_sig_now()
            rtsp_port = ch.rtsp_port or self.runtime.config.network.rtsp_port
            publish_host = self.runtime.config.mediamtx.publish_host or "127.0.0.1"
            encode = await self._must_encode(ch, ffmpeg)
            try:
                ov = None
                if self.runtime.config.osd.enabled and self._overlay_failures < 3:
                    from onvif_emu.overlay import OverlayFeeder, OverlayRenderer, canvas_size

                    ov = canvas_size(ch.encoder.width, ch.encoder.height)
                    self.feeder = OverlayFeeder(OverlayRenderer(self.runtime, ch.id, ov))
                shed = self.runtime.shed_level
                procs: list[tuple[FfmpegProcess, int]] = []

                osd = self.runtime.config.osd
                osd_dir = beside_exe_dir() / "data"

                def make(tier: int, name: str,
                         _a=(ffmpeg, ch, rtsp_port, vf, publish_host, ov, encode, osd, osd_dir)) -> FfmpegProcess:
                    ff, chan, port, filt, host, overlay, enc, osd_cfg, odir = _a
                    return FfmpegProcess(
                        build_command(ff, chan, port, filt, tier, publish_host=host, overlay=overlay,
                                      force_encode=enc, osd=osd_cfg, osd_dir=odir),
                        name, stdin_pipe=bool(overlay),
                    )

                self.main = make(0, f"ch{ch.id}")
                await self.main.start()
                procs.append((self.main, 0))
                self.sub = None
                if ch.sub.enabled and shed < 2:
                    self.sub = make(1, f"ch{ch.id}s")
                    await self.sub.start()
                    procs.append((self.sub, 1))
                self.third = None
                if ch.third.enabled and shed < 1:
                    self.third = make(2, f"ch{ch.id}t")
                    await self.third.start()
                    procs.append((self.third, 2))
                if ov and self.feeder:
                    for p, _tier in procs:
                        self.feeder.add(p.stdin)
                    self.feeder.start()
                started = time.monotonic()
                self._shed_started = shed
                self.state = "running"
                next_outage = self._next_outage(ch, started)
                outage_for = 0.0
                while not self._stop.is_set() and self.main and self.main.alive:
                    await self._tamper_tick(ch, time.monotonic() - started)
                    if next_outage and time.monotonic() >= next_outage:
                        imp = ch.impair
                        outage_for = random.uniform(min(imp.outage_len_min, imp.outage_len_max),
                                                    max(imp.outage_len_min, imp.outage_len_max))
                        log.warning("Pipeline ch%s: simulated network outage for %.1fs", self.channel_id, outage_for)
                        break
                    if self.runtime.shed_level != self._shed_started:
                        log.warning("Pipeline ch%s: load level changed - restarting streams", self.channel_id)
                        break
                    if self.sub is not None and not self.sub.alive:
                        log.warning("Pipeline ch%s substream exited", self.channel_id)
                        break
                    if self.third is not None and not self.third.alive:
                        log.warning("Pipeline ch%s third stream exited", self.channel_id)
                        break
                    sig = self._osd_sig_now()
                    if self._last_sig is not None and sig != self._last_sig:
                        log.info("Pipeline ch%s OSD/imaging changed — restarting encode", self.channel_id)
                        break
                    await asyncio.sleep(0.5)
                await self._tamper_tick(ch, None)
                if self._stop.is_set():
                    break
                if outage_for:
                    await self._stop_procs()
                    self.state = "outage"
                    self.outage_until = time.monotonic() + outage_for
                    try:
                        await asyncio.wait_for(self._stop.wait(), timeout=outage_for)
                    except TimeoutError:
                        pass
                    self.outage_until = 0.0
                    continue  # a simulated outage is not a failure: no backoff
                if self._clip_ended(ch):
                    log.warning("Pipeline ch%s: clip played once and ended (loop off) - stream stopped", self.channel_id)
                    await self._stop_procs()
                    self.state = "ended"
                    await self._stop.wait()
                    break
                lived = time.monotonic() - started
                if lived >= HEALTHY_S:
                    self.backoff.reset()
                    self._overlay_failures = 0
                elif ov and lived < 5.0 and not self.runtime.shed_level:
                    self._overlay_failures += 1
                    if self._overlay_failures >= 3:
                        log.warning("Pipeline ch%s: FFmpeg keeps rejecting the overlay input - overlay disabled", self.channel_id)
                log.warning("Pipeline ch%s exited after %.1fs, reconnecting", self.channel_id, lived)
            except Exception:
                log.exception("Pipeline ch%s error", self.channel_id)
            if self.feeder:
                await self.feeder.stop()
                self.feeder = None
            if self.main:
                await self.main.stop()
            if self.sub:
                await self.sub.stop()
            if self.third:
                await self.third.stop()
            delay = self.backoff.next_delay()
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=delay)
            except TimeoutError:
                continue


class PipelineManager:
    def __init__(self, runtime: Runtime):
        self.runtime = runtime
        self.pipes: dict[int, ChannelPipeline] = {}

    async def start(self) -> None:
        stagger = self.runtime.config.limits.stagger_ms / 1000.0
        for i, ch in enumerate(self.runtime.config.channels):
            pipe = ChannelPipeline(self.runtime, ch.id)
            self.pipes[ch.id] = pipe
            if i and stagger:
                await asyncio.sleep(stagger)  # do not start every encoder in the same instant
            await pipe.start()

    async def stop(self) -> None:
        for p in list(self.pipes.values()):
            await p.stop()
        self.pipes.clear()

    async def restart(self) -> None:
        await self.stop()
        await self.start()

    async def restart_channel(self, channel_id: int) -> None:
        old = self.pipes.get(channel_id)
        if old:
            await old.stop()
        pipe = ChannelPipeline(self.runtime, channel_id)
        self.pipes[channel_id] = pipe
        await pipe.start()
