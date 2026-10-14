"""Console "constructor": turn short command-line specs into fleet / channel / rule configuration.

    --devices 3 --streams 3 --source ./clip.mp4
    --rule "kind=tamper,every=30s"                      # every device, every channel
    --rule "kind=contact,device=1,random=5-200s"        # dry contact of device 1, random 5..200 s
    --rule "kind=analytics,topic=line,device=2,every=1m,ch=1"
    --device-set 1:channels.1.ptz_enabled=true --set channels.2.ptz_enabled=false
    --relays 2 --inputs 2 --webhook http://host/hook
"""

from __future__ import annotations

import re
from typing import Any

from onvif_emu.config import infer_source_type, merge_dict
from onvif_emu.models import AppConfig, EventRule, SourceType

_UNITS = {"s": "seconds", "sec": "seconds", "m": "minutes", "min": "minutes", "h": "hours", "hr": "hours",
          "": "seconds"}
_DUR = re.compile(r"^\s*([0-9]*\.?[0-9]+)\s*([a-zA-Z]*)\s*$")


def parse_duration(text: str) -> tuple[float, str]:
    """'30s' -> (30, 'seconds'); '5m' -> (5, 'minutes'); '2' -> (2, 'seconds')."""
    m = _DUR.match(text)
    if not m:
        raise ValueError(f"bad duration {text!r} (use 30s, 5m, 2h)")
    unit = _UNITS.get(m.group(2).lower())
    if unit is None:
        raise ValueError(f"bad time unit in {text!r} (use s, m or h)")
    return float(m.group(1)), unit


def parse_range(text: str) -> tuple[float, float, str]:
    """'5-200s' -> (5, 200, 'seconds'); '1-100m' -> (1, 100, 'minutes')."""
    m = re.match(r"^\s*([0-9]*\.?[0-9]+)\s*-\s*([0-9]*\.?[0-9]+)\s*([a-zA-Z]*)\s*$", text)
    if not m:
        raise ValueError(f"bad range {text!r} (use 5-200s or 1-100m)")
    unit = _UNITS.get(m.group(3).lower())
    if unit is None:
        raise ValueError(f"bad time unit in {text!r}")
    return float(m.group(1)), float(m.group(2)), unit


_SECONDS = {"seconds": 1.0, "minutes": 60.0, "hours": 3600.0}
_ON = {"1", "true", "yes", "on"}


def _seconds(text: str) -> float:
    v, unit = parse_duration(text)
    return v * _SECONDS[unit]


def parse_impair_spec(spec: str) -> tuple[int, dict[str, Any]]:
    """``N:rain=0.6,shake=0.4,shake_every=15s,shake_for=3s,cover=black,loss=3,outage=1-5m,...`` -> (channel, fields).

    Channel 0 = every channel. ``*_for=always`` makes an effect constant instead of periodic bursts.
    """
    idx, _, rest = spec.partition(":")
    if not idx.strip().isdigit():
        raise ValueError("impair spec must look like N:key=value,... (N = channel, 0 = all)")
    d: dict[str, Any] = {"enabled": True}
    for chunk in [c for c in rest.split(",") if c.strip()]:
        if "=" not in chunk:
            raise ValueError(f"impair field {chunk!r} must be key=value")
        k, v = (s.strip() for s in chunk.split("=", 1))
        key = k.lower()
        burst = next((b for b in ("shake", "cover", "freeze", "bitrate") if key in {f"{b}_every", f"{b}_for"}), None)
        if burst:
            b = d.setdefault(f"{burst}_burst", {})
            if key.endswith("_every"):
                b["period_s"] = _seconds(v)
            elif v.lower() in {"always", "constant", "on"}:
                b["mode"] = "constant"
            else:
                b["duration_s"], b["mode"] = _seconds(v), "bursts"
        elif key in {"rain", "noise", "shake", "loss", "stutter"}:
            d[key] = float(v)
        elif key == "pixelate":
            d[key] = int(v)
        elif key in {"cap", "bitrate_cap"}:
            d["bitrate_cap"] = int(v)
        elif key in {"cover", "bitrate"}:
            d[key] = v.lower()
        elif key in {"freeze", "tamper_events", "enabled"}:
            d[key] = v.lower() in _ON
        elif key == "outage":
            if "-" in v:
                d["outage_every_min"], d["outage_every_max"], d["outage_unit"] = parse_range(v)
            else:
                val, d["outage_unit"] = parse_duration(v)
                d["outage_every_min"] = d["outage_every_max"] = val
            d["outage"] = True
        elif key == "outage_len":
            if "-" in v:
                lo, hi, unit = parse_range(v)
                d["outage_len_min"], d["outage_len_max"] = lo * _SECONDS[unit], hi * _SECONDS[unit]
            else:
                d["outage_len_min"] = d["outage_len_max"] = _seconds(v)
        else:
            raise ValueError(f"unknown impair field {k!r}")
    return int(idx), d


def parse_user_spec(spec: str):
    """``NAME:PASSWORD:ROLE[:1,2]`` -> UserRecord (applied to data/users.json on start)."""
    from onvif_emu.models import Role, UserRecord

    parts = spec.split(":")
    if len(parts) < 3 or not parts[0] or not parts[1]:
        raise ValueError("--add-user must look like NAME:PASSWORD:ROLE[:CH,CH]")
    chans = [int(c) for c in parts[3].split(",") if c.strip()] if len(parts) > 3 else []
    return UserRecord(username=parts[0], password=parts[1], role=Role.from_any(parts[2]), channels=chans)


def apply_impair(cfg: AppConfig, spec: str) -> None:
    from onvif_emu.models import ImpairConfig

    cid, fields = parse_impair_spec(spec)
    targets = [c for c in cfg.channels if cid in (0, c.id)]
    if not targets:
        raise ValueError(f"--impair: no channel {cid}")
    for ch in targets:
        merged = ch.impair.model_dump()
        for k, v in fields.items():
            if isinstance(v, dict):
                merged[k] = {**merged.get(k, {}), **v}
            else:
                merged[k] = v
        ch.impair = ImpairConfig.model_validate(merged)


def parse_rule_spec(spec: str) -> EventRule:
    """Parse ``kind=tamper,every=30s,ch=1,...`` into an EventRule."""
    d: dict[str, Any] = {"source": {}, "data": {}}
    for chunk in [c for c in spec.split(",") if c.strip()]:
        if "=" not in chunk:
            raise ValueError(f"rule field {chunk!r} must be key=value")
        k, v = (s.strip() for s in chunk.split("=", 1))
        key = k.lower()
        if key == "kind":
            d["kind"] = v.lower()
        elif key in {"every", "interval"}:
            d["interval"], d["unit"] = parse_duration(v)
            d["schedule"] = "interval"
        elif key in {"random", "rand", "between"}:
            d["range_min"], d["range_max"], d["unit"] = parse_range(v)
            d["schedule"] = "random"
        elif key in {"hold", "duration"}:
            secs, unit = parse_duration(v)
            d["hold_s"] = secs * {"seconds": 1, "minutes": 60, "hours": 3600}[unit]
        elif key in {"delay", "start"}:
            secs, unit = parse_duration(v)
            d["start_delay_s"] = secs * {"seconds": 1, "minutes": 60, "hours": 3600}[unit]
        elif key in {"ch", "channel"}:
            d["channel"] = 0 if v.lower() in {"all", "*"} else int(v)
        elif key in {"device", "dev"}:
            d["device"] = 0 if v.lower() in {"all", "*"} else int(v)
        elif key in {"manual"}:
            d["schedule"] = "manual"
        elif key == "schedule":
            d["schedule"] = v.lower()
        elif key in {"name", "topic", "target", "action"}:
            d[key] = v
        elif key == "repeat":
            d["repeat"] = int(v)
        elif key in {"push", "push_url", "url"}:
            d["push_url"] = v
        elif key == "enabled":
            d["enabled"] = v.lower() in {"1", "true", "yes", "on"}
        elif key.startswith("data."):
            d["data"][k[5:]] = v
        elif key.startswith("source."):
            d["source"][k[7:]] = v
        else:
            raise ValueError(f"unknown rule field {k!r}")
    if "kind" not in d:
        raise ValueError("rule needs kind=motion|tamper|analytics|relay|contact|metadata|custom")
    if "schedule" not in d:
        d["schedule"] = "manual"
    return EventRule.model_validate(d)


def apply_streams(cfg: AppConfig, n: int) -> None:
    """Give every channel ``n`` profiles (1 = full, 2 = + medium, 3 = + low), derived from its main encoder."""
    from onvif_emu.models import ChannelConfig

    out = []
    for ch in cfg.channels:
        raw = ch.model_dump(mode="json")
        raw["streams"] = n
        out.append(ChannelConfig.model_validate(raw))
    cfg.channels = out


def apply_source(cfg: AppConfig, uri: str) -> None:
    """One video for all channels (each channel then streams it in its own quality ladder)."""
    for ch in cfg.channels:
        ch.source.uri = uri
        ch.source.type = SourceType(infer_source_type(uri))


def make_channels(cfg: AppConfig, count: int) -> None:
    """Grow/shrink to ``count`` channels; new ones copy channel 1 (same source, own path)."""
    chans = list(cfg.channels)
    count = max(1, min(64, count))
    while len(chans) < count:
        base = chans[-1].model_dump(mode="json")
        nid = max(c.id for c in chans) + 1
        base.update(id=nid, name=f"Channel {nid}", rtsp_path=f"ch{nid}", rtsp_port=None)
        chans.append(type(chans[0]).model_validate(base))
    cfg.channels = chans[:count]


_CH_PATH = re.compile(r"^channels\.(\d+)\.(.+)$")


def split_channel_paths(overlay: dict[str, Any]) -> dict[int, dict[str, Any]]:
    """Pull ``channels.<id>.<path>`` items out of an overlay -> {id: nested overrides}."""
    patches: dict[int, dict[str, Any]] = {}
    for key in [k for k in list(overlay) if k.startswith("channels.")]:
        m = _CH_PATH.match(key)
        if not m:
            continue
        cur = patches.setdefault(int(m.group(1)), {})
        parts = m.group(2).split(".")
        for p in parts[:-1]:
            cur = cur.setdefault(p, {})
        cur[parts[-1]] = overlay.pop(key)
    return patches


def apply_channel_patches(cfg: AppConfig, patches: dict[int, dict[str, Any]]) -> None:
    from onvif_emu.models import ChannelConfig

    if not patches:
        return
    out = []
    for ch in cfg.channels:
        p = patches.get(ch.id)
        out.append(ChannelConfig.model_validate(merge_dict(ch.model_dump(mode="json"), p)) if p else ch)
    cfg.channels = out
