"""Resource control: estimate the load of a configuration, refuse/clamp it, and shed load at runtime.

Nothing here needs the machine to be exotic: FFmpeg encoding dominates, so the model is
"megapixels per second" of encoded video against the CPU cores available.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from onvif_emu import sysmon
from onvif_emu.fleet import device_count, parse_ports
from onvif_emu.models import UNIT_SECONDS, AppConfig, ChannelConfig

if TYPE_CHECKING:
    from onvif_emu.runtime import Runtime

log = logging.getLogger(__name__)

CORE_MPX = 60.0  # megapixels/s one core encodes with libx264 veryfast+zerolatency (conservative)
MB_PER_STREAM = 90  # FFmpeg process
MB_PER_DEVICE = 110  # Python runtime + MediaMTX
MIN_FPS = 5


class LimitError(ValueError):
    """Configuration exceeds the limits and limits.mode is 'strict'."""


@dataclass
class Estimate:
    devices: int = 0
    channels: int = 0
    streams: int = 0
    mpx_per_s: float = 0.0
    cores_needed: float = 0.0
    memory_mb: int = 0
    rules: int = 0
    events_per_s: float = 0.0
    cores: int = 0
    ram_mb: int = 0
    budget_mpx: float = 0.0
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {k: (round(v, 2) if isinstance(v, float) else v) for k, v in self.__dict__.items()}


def channel_streams(ch: ChannelConfig) -> list[tuple[int, int, int]]:
    """(width, height, fps) of every encoded stream of an enabled channel."""
    if not ch.enabled:
        return []
    out = [(ch.encoder.width, ch.encoder.height, ch.encoder.fps)]
    if ch.sub.enabled:
        out.append((ch.sub.width, ch.sub.height, ch.sub.fps))
    if ch.third.enabled:
        out.append((ch.third.width, ch.third.height, ch.third.fps))
    return out


def rule_rate(cfg: AppConfig) -> float:
    """Upper-bound events per second the enabled timer rules can produce (a pulse = 2 events)."""
    rate = 0.0
    for r in cfg.diag.rules:
        if not r.enabled or r.schedule.value == "manual":
            continue
        scale = UNIT_SECONDS[r.unit.value]
        period = r.interval * scale if r.schedule.value == "interval" else (r.range_min + r.range_max) / 2 * scale
        per_fire = 2 if r.action == "pulse" else 1
        fan = max(1, len(cfg.channels)) if r.channel == 0 and r.kind.value not in {"relay", "contact"} else 1
        rate += per_fire * fan / max(period, 0.05)
    return rate


def estimate(cfgs: list[AppConfig]) -> Estimate:
    e = Estimate(cores=sysmon.cpu_count(), ram_mb=sysmon.total_memory_mb())
    e.devices = len(cfgs)
    for cfg in cfgs:
        e.channels += sum(1 for c in cfg.channels if c.enabled)
        e.rules += len(cfg.diag.rules)
        e.events_per_s += rule_rate(cfg)
        for ch in cfg.channels:
            for w, h, fps in channel_streams(ch):
                e.streams += 1
                e.mpx_per_s += w * h * fps / 1e6
    e.cores_needed = e.mpx_per_s / CORE_MPX
    e.memory_mb = e.streams * MB_PER_STREAM + e.devices * MB_PER_DEVICE
    lim = cfgs[0].limits if cfgs else AppConfig().limits
    e.budget_mpx = e.cores * CORE_MPX * lim.max_load_pct / 100.0
    return e


def _over(cfgs: list[AppConfig], e: Estimate) -> list[str]:
    lim = cfgs[0].limits
    why = []
    if e.devices > lim.max_devices:
        why.append(f"devices {e.devices} > max_devices {lim.max_devices}")
    if e.channels > lim.max_channels:
        why.append(f"channels {e.channels} > max_channels {lim.max_channels}")
    if e.streams > lim.max_streams:
        why.append(f"streams {e.streams} > max_streams {lim.max_streams}")
    if e.mpx_per_s > e.budget_mpx:
        why.append(f"encoding load {e.mpx_per_s:.0f} Mpx/s > budget {e.budget_mpx:.0f} Mpx/s "
                   f"({e.cores} cores x {lim.max_load_pct}%)")
    if e.ram_mb and e.memory_mb > e.ram_mb * 0.7:
        why.append(f"memory {e.memory_mb} MB > 70% of {e.ram_mb} MB RAM")
    if e.rules > lim.max_rules:
        why.append(f"rules {e.rules} > max_rules {lim.max_rules}")
    if e.events_per_s > lim.max_events_per_s:
        why.append(f"timer events {e.events_per_s:.0f}/s > max_events_per_s {lim.max_events_per_s}")
    return why


def _trim_devices(root: AppConfig, n: int) -> None:
    f = root.fleet
    n = max(1, n)
    f.count = n
    f.onvif_ports = ",".join(str(p) for p in parse_ports(f.onvif_ports, root.onvif_port, n)) if f.onvif_ports else ""
    f.rtsp_ports = ",".join(str(p) for p in parse_ports(f.rtsp_ports, root.network.rtsp_port, n)) if f.rtsp_ports else ""


def enforce(root: AppConfig) -> tuple[AppConfig, list[str]]:
    """Check the whole planned fleet; return a (possibly reduced) root and human-readable messages."""
    from onvif_emu.fleet import build_fleet

    lim = root.limits
    if lim.mode == "off":
        return root, []
    msgs: list[str] = []
    root = root.model_copy(deep=True)

    # timers and rule counts first: cheap and never needs video changes
    for r in root.diag.rules:
        floor = lim.min_interval_s / UNIT_SECONDS[r.unit.value]
        if r.schedule.value != "manual" and min(r.interval, r.range_min) < floor:
            msgs.append(f"rule '{r.name}': timer raised to the minimum {lim.min_interval_s}s")
            r.interval = max(r.interval, floor)
            r.range_min = max(r.range_min, floor)
            r.range_max = max(r.range_max, r.range_min)
    if len(root.diag.rules) > lim.max_rules:
        msgs.append(f"{len(root.diag.rules)} rules > max_rules {lim.max_rules}: extra rules dropped")
        root.diag.rules = root.diag.rules[: lim.max_rules]

    steps = 0
    while steps < 500:
        steps += 1
        cfgs = build_fleet(root)
        e = estimate(cfgs)
        why = _over(cfgs, e)
        if not why:
            break
        if lim.mode == "strict":
            raise LimitError("; ".join(why) + " (limits.mode=strict; raise limits or lower the load)")
        if lim.mode == "warn":
            msgs += [f"over limit: {w}" for w in why]
            return root, msgs
        action = _reduce(root, e, why)
        if not action:
            msgs.append("cannot reduce further: " + "; ".join(why))
            break
        msgs.append(action)
    if steps > 1 and msgs:
        msgs.append(f"planned load after clamping: {estimate(build_fleet(root)).as_dict()}")
    return root, msgs


def _reduce(root: AppConfig, e: Estimate, why: list[str]) -> str:
    """Take ONE step towards fitting; returns what was done ('' = nothing left to reduce)."""
    lim = root.limits
    n_dev = device_count(root)
    # 1) too many devices / channels for the hard caps
    if any(w.startswith("devices") for w in why) and n_dev > lim.max_devices:
        _trim_devices(root, lim.max_devices)
        return f"devices reduced to {lim.max_devices}"
    # 2) cheapest video reductions first, applied to the shared template: low stream, medium stream
    for tier, attr in ((2, "third"), (1, "sub")):
        for ch in reversed(root.channels):
            if getattr(ch, attr).enabled:
                getattr(ch, attr).enabled = False
                return f"channel {ch.id}: {'low' if tier == 2 else 'medium'} stream turned off"
    # 3) main streams: fps down to MIN_FPS, then half the resolution, then fewer channels, then fewer devices
    for ch in root.channels:
        if ch.enabled and ch.encoder.fps > 10:
            ch.encoder.fps = max(10, ch.encoder.fps // 2)
            return f"channel {ch.id}: main stream fps lowered to {ch.encoder.fps}"
    for ch in root.channels:
        if ch.enabled and ch.encoder.width > 640:
            ch.encoder.width = max(320, ch.encoder.width // 2 // 2 * 2)
            ch.encoder.height = max(240, ch.encoder.height // 2 // 2 * 2)
            return f"channel {ch.id}: main stream scaled to {ch.encoder.width}x{ch.encoder.height}"
    enabled = [c for c in root.channels if c.enabled]
    if len(enabled) > 1:
        enabled[-1].enabled = False
        return f"channel {enabled[-1].id} turned off"
    if n_dev > 1:
        _trim_devices(root, n_dev - 1)
        return f"devices reduced to {n_dev - 1}"
    return ""


class Governor:
    """Runtime watchdog: when the machine is overloaded, stop the extra streams; restore when calm."""

    SAMPLE_S = 2.0
    HIGH_SAMPLES = 5  # ~10 s of overload before acting
    CALM_SAMPLES = 15  # ~30 s of calm before restoring

    def __init__(self, runtimes: list[Runtime]):
        self.runtimes = runtimes
        self.level = 0  # 0 normal, 1 low streams off, 2 low + medium streams off
        self.cpu = 0.0
        self.mem = 0.0
        self._high = 0
        self._calm = 0
        self._task: asyncio.Task | None = None
        self._meter = sysmon.CpuMeter()

    def start(self) -> None:
        self._task = asyncio.get_running_loop().create_task(self._loop(), name="governor")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass

    async def _loop(self) -> None:
        lim = self.runtimes[0].config.limits
        if lim.mode == "off" or not lim.shed:
            return
        while True:
            await asyncio.sleep(self.SAMPLE_S)
            self.cpu = self._meter.sample()
            self.mem = sysmon.memory_percent()
            over = self.cpu >= lim.cpu_high_pct or (self.mem and self.mem >= lim.mem_high_pct)
            calm = self.cpu < lim.cpu_high_pct - 20 and (not self.mem or self.mem < lim.mem_high_pct - 5)
            self._high = self._high + 1 if over else 0
            self._calm = self._calm + 1 if calm else 0
            if self._high >= self.HIGH_SAMPLES and self.level < 2:
                self._set(self.level + 1, f"cpu {self.cpu:.0f}% mem {self.mem:.0f}%")
                self._high = 0
            elif self._calm >= self.CALM_SAMPLES and self.level > 0:
                self._set(self.level - 1, "load back to normal")
                self._calm = 0

    def _set(self, level: int, why: str) -> None:
        self.level = level
        names = ["all streams on", "LOW streams off", "LOW + MEDIUM streams off"]
        log.warning("Load governor -> level %s (%s): %s", level, names[level], why)
        print(f"Load governor: {names[level]} ({why})", flush=True)
        for rt in self.runtimes:
            rt.shed_level = level

    def status(self) -> dict[str, Any]:
        return {"level": self.level, "cpu_pct": round(self.cpu, 1), "mem_pct": round(self.mem, 1)}


def describe(e: Estimate) -> str:
    return (
        f"devices={e.devices} channels={e.channels} streams={e.streams} "
        f"encode={e.mpx_per_s:.0f} Mpx/s (~{e.cores_needed:.1f} of {e.cores} cores, budget {e.budget_mpx:.0f}) "
        f"ram~{e.memory_mb} MB of {e.ram_mb or '?'} MB rules={e.rules} timer-events~{e.events_per_s:.1f}/s"
    )

