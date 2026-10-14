from __future__ import annotations

import math
from datetime import UTC, datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from onvif_emu.models import DeviceConfig


def utc_now() -> datetime:
    return datetime.now(UTC)


def resolve_tz(name: str) -> timezone | ZoneInfo:
    if not name or name.upper() in {"UTC", "Z", "GMT"}:
        return UTC
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        return UTC


def local_now(tz_name: str) -> datetime:
    return utc_now().astimezone(resolve_tz(tz_name))


def posix_tz(tz_name: str) -> str:
    """ONVIF TimeZone.TZ is a POSIX TZ string. Prefer IANA via offset snapshot.

    POSIX TZ is inverted vs ISO (UTC+3 → CST-3). We emit a stable numeric form
    `UTC±HH:MM` based on *current* offset so DST is reflected at call time.
    """
    now = local_now(tz_name)
    offset = now.utcoffset() or UTC.utcoffset(now)
    if offset is None:
        return "UTC0"
    total = int(offset.total_seconds())
    sign = "-" if total > 0 else "+"
    total = abs(total)
    hours, rem = divmod(total, 3600)
    minutes = rem // 60
    if minutes:
        return f"UTC{sign}{hours}:{minutes:02d}"
    return f"UTC{sign}{hours}"


def dt_parts(dt: datetime) -> dict[str, int]:
    return {
        "year": dt.year,
        "month": dt.month,
        "day": dt.day,
        "hour": dt.hour,
        "minute": dt.minute,
        "second": dt.second,
    }


def xml_datetime(dt: datetime, prefix: str = "tt") -> str:
    p = dt_parts(dt)
    return (
        f"<{prefix}:Time>"
        f"<{prefix}:Hour>{p['hour']}</{prefix}:Hour>"
        f"<{prefix}:Minute>{p['minute']}</{prefix}:Minute>"
        f"<{prefix}:Second>{p['second']}</{prefix}:Second>"
        f"</{prefix}:Time>"
        f"<{prefix}:Date>"
        f"<{prefix}:Year>{p['year']}</{prefix}:Year>"
        f"<{prefix}:Month>{p['month']}</{prefix}:Month>"
        f"<{prefix}:Day>{p['day']}</{prefix}:Day>"
        f"</{prefix}:Date>"
    )


def isoformat_z(dt: datetime | None = None) -> str:
    d = dt or utc_now()
    if d.tzinfo is None:
        d = d.replace(tzinfo=UTC)
    return d.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def system_datetime_fields(device: DeviceConfig) -> dict[str, object]:
    utc = utc_now()
    local = utc.astimezone(resolve_tz(device.timezone))
    return {
        "utc": utc,
        "local": local,
        "tz": posix_tz(device.timezone),
        "dst": bool(device.daylight_savings or (local.dst() and local.dst().total_seconds() != 0)),
        "type": "NTP" if device.use_ntp else "Manual",
    }


def clamp(value: float, lo: float, hi: float) -> float:
    if lo > hi:
        lo, hi = hi, lo
    return max(lo, min(hi, value))


def nearly_equal(a: float, b: float, eps: float = 1e-6) -> bool:
    return abs(a - b) <= eps


def finite(value: float, fallback: float = 0.0) -> float:
    if not math.isfinite(value):
        return fallback
    return value
