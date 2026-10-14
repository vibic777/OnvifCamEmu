from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class AuthMode(StrEnum):
    digest = "digest"
    basic = "basic"
    wsse = "wsse"
    none = "none"


class EventMode(StrEnum):
    timer = "timer"
    motion = "motion"
    manual = "manual"


class SourceType(StrEnum):
    file = "file"
    rtsp = "rtsp"
    http = "http"
    hls = "hls"
    dash = "dash"
    udp = "udp"
    v4l2 = "v4l2"
    x11grab = "x11grab"
    testsrc = "testsrc"


class Codec(StrEnum):
    h264 = "h264"
    h265 = "h265"
    mjpeg = "mjpeg"


class Role(StrEnum):
    """Administrator: everything. Operator: watch (all / chosen channels) + PTZ. Viewer: watch only."""

    Administrator = "Administrator"
    Operator = "Operator"
    Viewer = "Viewer"

    @property
    def onvif_level(self) -> str:
        """tt:UserLevel on the wire (ONVIF calls the watch-only level "User")."""
        return "User" if self is Role.Viewer else self.value

    @classmethod
    def from_any(cls, text: str | None, default: Role | None = None) -> Role:
        t = (text or "").strip().lower()
        for r in cls:
            if t in {r.value.lower(), r.onvif_level.lower()}:
                return r
        aliases = {"admin": cls.Administrator, "user": cls.Viewer, "viewer": cls.Viewer, "operator": cls.Operator}
        if t in aliases:
            return aliases[t]
        if default is not None:
            return default
        raise ValueError(f"unknown role {text!r} (Administrator, Operator, Viewer)")


class AuthConfig(BaseModel):
    mode: AuthMode = AuthMode.digest
    # Accounts live in data/users.json (encrypted). These two are one-shot inputs only (CLI --user/--password,
    # old configs, scripts): applied to that account on start / apply, never written back to the config file.
    username: str | None = Field(default=None, exclude=True)
    password: str | None = Field(default=None, exclude=True)
    # When true, GetStreamUri embeds user:password@ in the RTSP URL (legacy VMS).
    embed_rtsp_credentials: bool = False


class DeviceConfig(BaseModel):
    manufacturer: str = "Vibic"
    model: str = "EMU-CAM-1"
    firmware: str = "1.0.0"
    serial: str = "EMU00001"
    hardware_id: str = "HW-EMU-1"
    hostname: str = "onvif-emu"
    name: str = "ONVIF Emulator"
    location: str = "Lab"
    timezone: str = "UTC"
    use_ntp: bool = False
    daylight_savings: bool = False


class NetworkConfig(BaseModel):
    http_port: int = Field(default=8080, ge=1, le=65535)
    rtsp_port: int = Field(default=8554, ge=1, le=65535)
    https_port: int = Field(default=8443, ge=1, le=65535)
    rtsp_enabled: bool = True
    http_enabled: bool = True


class NtpConfig(BaseModel):
    enabled: bool = False
    servers: list[str] = Field(default_factory=lambda: ["pool.ntp.org"])


class UserRecord(BaseModel):
    """An account (in memory the password is plain; on disk it is encrypted in data/users.json)."""

    username: str
    password: str
    role: Role = Role.Administrator
    enabled: bool = True
    channels: list[int] = Field(default_factory=list)  # Operator / Viewer: allowed channels, empty = all

    @field_validator("role", mode="before")
    @classmethod
    def _role(cls, v: Any) -> Any:
        return Role.from_any(v) if isinstance(v, str) else v

    def sees(self, channel_id: int) -> bool:
        return self.role == Role.Administrator or not self.channels or channel_id in self.channels


class EncoderConfig(BaseModel):
    codec: Codec = Codec.h264
    width: int = Field(default=1920, ge=160, le=7680)
    height: int = Field(default=1080, ge=120, le=4320)
    fps: int = Field(default=25, ge=1, le=120)
    bitrate: int = Field(default=4000, ge=64, le=100000)
    gop: int = Field(default=50, ge=1, le=300)
    quality: int = Field(default=80, ge=1, le=100)


class SourceConfig(BaseModel):
    type: SourceType = SourceType.testsrc
    uri: str = ""
    loop: bool = True
    # file played once (loop off): "stop" ends the stream, "freeze" holds the last frame, "black" shows black
    end_action: Literal["stop", "freeze", "black"] = "stop"
    buffer_ms: int = Field(default=500, ge=0, le=30000)
    transcode: bool = False
    reconnect: bool = True
    reconnect_initial_s: float = Field(default=1.0, ge=0.1, le=60)
    reconnect_max_s: float = Field(default=30.0, ge=1, le=300)


class SubStreamConfig(BaseModel):
    enabled: bool = True
    codec: Codec = Codec.h264
    width: int = Field(default=640, ge=160, le=1920)
    height: int = Field(default=360, ge=120, le=1080)
    fps: int = Field(default=15, ge=1, le=60)
    bitrate: int = Field(default=512, ge=64, le=8000)
    gop: int = Field(default=30, ge=1, le=300)


class ImpairBurst(BaseModel):
    """When an effect is on: all the time, or for ``duration_s`` at the start of every ``period_s``."""

    mode: Literal["constant", "bursts"] = "bursts"
    period_s: float = Field(default=20.0, ge=1, le=86400)
    duration_s: float = Field(default=3.0, ge=0.1, le=86400)


class ImpairConfig(BaseModel):
    """Picture / network trouble simulator for one channel (diagnostics). Every effect is off at its zero value."""

    enabled: bool = False  # master switch: nothing below applies while off
    # weather / optics
    rain: float = Field(default=0.0, ge=0, le=1)  # wet lens: blur, smear, lower contrast, noise, colour fringes
    noise: float = Field(default=0.0, ge=0, le=1)  # sensor noise (night, cheap camera)
    pixelate: int = Field(default=1, ge=1, le=32)  # 1 = off; N = N-times coarser picture
    # vandalism
    shake: float = Field(default=0.0, ge=0, le=1)  # camera shaken / hit: amplitude
    shake_burst: ImpairBurst = Field(default_factory=lambda: ImpairBurst(period_s=15, duration_s=3))
    cover: Literal["off", "black", "blur", "spray"] = "off"  # lens covered / blinded / sprayed
    cover_burst: ImpairBurst = Field(default_factory=lambda: ImpairBurst(period_s=60, duration_s=5))
    tamper_events: bool = True  # raise tns1:VideoSource/Tampering while shaking / covered
    # network / stream
    loss: float = Field(default=0.0, ge=0, le=50)  # % of video packets dropped (smearing, grey blocks)
    stutter: float = Field(default=0.0, ge=0, le=90)  # % of frames dropped at random (jerky motion)
    freeze: bool = False  # picture freezes while the stream stays up
    freeze_burst: ImpairBurst = Field(default_factory=lambda: ImpairBurst(period_s=30, duration_s=2))
    bitrate: Literal["normal", "cbr", "vbr_jumps"] = "normal"  # strict CBR, or VBR with bitrate spikes
    bitrate_burst: ImpairBurst = Field(default_factory=lambda: ImpairBurst(period_s=20, duration_s=4))
    bitrate_cap: int = Field(default=0, ge=0, le=100000)  # kbps, 0 = profile bitrate (starved link below it)
    outage: bool = False  # the stream disappears (link down) and comes back
    outage_unit: Literal["seconds", "minutes", "hours"] = "minutes"
    outage_every_min: float = Field(default=1.0, ge=0.05, le=100000)
    outage_every_max: float = Field(default=5.0, ge=0.05, le=100000)
    outage_len_min: float = Field(default=5.0, ge=0.5, le=86400)  # seconds
    outage_len_max: float = Field(default=20.0, ge=0.5, le=86400)

    def active(self) -> bool:
        """Anything that changes the picture or the encoder (outages alone do not)."""
        return self.enabled and bool(
            self.rain or self.noise or self.pixelate > 1 or self.shake or self.cover != "off" or self.loss
            or self.stutter or self.freeze or self.bitrate != "normal" or self.bitrate_cap
        )


class ChannelConfig(BaseModel):
    id: int = Field(ge=1, le=64)
    name: str = "Channel 1"
    enabled: bool = True
    rtsp_path: str = ""
    # own RTSP port for this channel (None = the device-wide network.rtsp_port); one MediaMTX per distinct port
    rtsp_port: int | None = Field(default=None, ge=1, le=65535)
    ptz_enabled: bool = True  # False: no PTZ node/configuration for this channel
    ptz: PtzConfig | None = None  # per-channel limits/speeds; None = the device-wide ptz block
    source: SourceConfig = Field(default_factory=SourceConfig)
    encoder: EncoderConfig = Field(default_factory=EncoderConfig)  # profile 1: "full"
    sub: SubStreamConfig = Field(default_factory=SubStreamConfig)  # profile 2: "medium"
    third: SubStreamConfig = Field(  # profile 3: "low" (off by default)
        default_factory=lambda: SubStreamConfig(enabled=False, width=320, height=180, fps=10, bitrate=256, gop=20)
    )
    impair: ImpairConfig = Field(default_factory=ImpairConfig)  # diagnostics: picture / network trouble

    @model_validator(mode="before")
    @classmethod
    def auto_ladder(cls, data: Any) -> Any:
        """``streams: 1..3`` -> fill the medium/low profiles from the main encoder (input only, not stored)."""
        if not isinstance(data, dict) or data.get("streams") in (None, ""):
            return data
        data = dict(data)
        n = max(1, min(3, int(data.pop("streams"))))
        enc = EncoderConfig.model_validate(data.get("encoder") or {})

        def even(v: float, lo: int) -> int:
            return max(lo, int(v) // 2 * 2)

        sub = dict(data.get("sub") or {})
        third = dict(data.get("third") or {})
        sub.update(
            enabled=n >= 2, codec=enc.codec.value if enc.codec.value != "h265" else "h264",
            width=even(enc.width / 2, 160), height=even(enc.height / 2, 120),
            fps=max(5, round(enc.fps * 0.6)), bitrate=max(64, enc.bitrate // 4), gop=max(1, enc.fps * 2),
        )
        third.update(
            enabled=n >= 3, codec="h264",
            width=even(enc.width / 4, 160), height=even(enc.height / 4, 120),
            fps=max(5, round(enc.fps * 0.4)), bitrate=max(64, enc.bitrate // 12), gop=max(1, enc.fps * 2),
        )
        data["sub"], data["third"] = sub, third
        return data

    @model_validator(mode="after")
    def default_path(self) -> ChannelConfig:
        if not self.rtsp_path:
            self.rtsp_path = f"ch{self.id}"
        return self


class PtzConfig(BaseModel):
    pan_min: float = Field(default=-180.0, ge=-180, le=180)
    pan_max: float = Field(default=180.0, ge=-180, le=180)
    tilt_min: float = Field(default=-90.0, ge=-90, le=90)
    tilt_max: float = Field(default=90.0, ge=-90, le=90)
    zoom_min: float = Field(default=0.0, ge=0, le=1)
    zoom_max: float = Field(default=1.0, ge=0, le=1)
    pan_speed: float = Field(default=30.0, ge=0.1, le=180)  # deg/s
    tilt_speed: float = Field(default=20.0, ge=0.1, le=90)
    zoom_speed: float = Field(default=0.25, ge=0.01, le=1)
    iris_min: float = Field(default=0.0, ge=0, le=1)
    iris_max: float = Field(default=1.0, ge=0, le=1)
    focus_min: float = Field(default=0.0, ge=0, le=1)
    focus_max: float = Field(default=1.0, ge=0, le=1)
    automove: bool = False
    flip: bool = False
    tours_enabled: bool = False


class ImagingConfig(BaseModel):
    brightness: int = Field(default=50, ge=0, le=100)
    contrast: int = Field(default=50, ge=0, le=100)
    saturation: int = Field(default=50, ge=0, le=100)
    sharpness: int = Field(default=50, ge=0, le=100)
    backlight: bool = False
    backlight_level: int = Field(default=50, ge=0, le=100)
    white_balance: Literal["auto", "manual"] = "auto"
    wb_cr: int = Field(default=50, ge=0, le=100)
    wb_cb: int = Field(default=50, ge=0, le=100)
    exposure_mode: Literal["auto", "manual"] = "auto"
    iris: float = Field(default=0.5, ge=0, le=1)
    focus_mode: Literal["auto", "manual"] = "auto"
    focus: float = Field(default=0.5, ge=0, le=1)


class OsdStyle(BaseModel):
    color: str = "#00FF88"
    opacity: float = Field(default=0.85, ge=0, le=1)
    size: int = Field(default=28, ge=8, le=96)
    position: Literal["top-left", "top-right", "bottom-left", "bottom-right", "center"] = "top-left"
    event_position: Literal["top-left", "top-center", "top-right", "middle-left", "center", "middle-right", "bottom-left", "bottom-center", "bottom-right"] = "top-right"
    font: str = "sans"
    duration_ms: int = Field(default=1500, ge=100, le=30000)
    show_text_status: bool = True
    show_arrows: bool = True
    show_zoom: bool = True
    show_iris: bool = True
    show_focus: bool = True
    show_events: bool = True
    show_io: bool = True  # relay / dry-contact LEDs
    boxes: Literal["injected", "all", "off"] = "injected"  # analytics boxes: only injected objects / all / none


OsdAnchor = Literal[
    "top-left", "top-center", "top-right", "middle-left", "center", "middle-right",
    "bottom-left", "bottom-center", "bottom-right", "custom",
]


class OsdStamp(BaseModel):
    """Date or time stamp burnt into the video (FFmpeg drawtext, PC clock)."""

    enabled: bool = False
    format: str = "%H:%M:%S"  # strftime: %d.%m.%Y, %Y-%m-%d, %H:%M:%S, %I:%M %p, ... plain text allowed
    position: OsdAnchor = "top-right"
    x: float = Field(default=5.0, ge=0, le=100)  # custom position, % of the frame width
    y: float = Field(default=5.0, ge=0, le=100)  # custom position, % of the frame height
    size: int = Field(default=36, ge=6, le=300)  # font size in px at 1080 lines (scaled for every profile)
    font: Literal["sans", "mono"] = "sans"
    color: str = "#FFFFFF"
    opacity: float = Field(default=1.0, ge=0, le=1)
    bg_color: str = "#000000"
    bg_opacity: float = Field(default=0.4, ge=0, le=1)  # 0 = transparent background
    outline: bool = True  # thin dark outline: readable on any picture without a background


class OsdDateStamp(OsdStamp):
    """Same as the time stamp, with date defaults (a partial dict from YAML / CLI keeps them)."""

    format: str = "%d.%m.%Y"


class OsdImage(BaseModel):
    """Picture (logo, watermark) burnt into the video."""

    enabled: bool = False
    path: str = ""  # uploaded into osd/ beside the program, or any file on the emulator's machine
    position: OsdAnchor = "top-left"
    x: float = Field(default=2.0, ge=0, le=100)
    y: float = Field(default=2.0, ge=0, le=100)
    size: float = Field(default=15.0, ge=1, le=100)  # width, % of the frame width (height keeps the aspect)
    opacity: float = Field(default=1.0, ge=0, le=1)
    transparent_color: str = ""  # e.g. #FFFFFF: this colour becomes transparent (logo on a white JPEG)
    key_similarity: float = Field(default=0.15, ge=0.01, le=1)
    bg_color: str = "#000000"
    bg_opacity: float = Field(default=0.0, ge=0, le=1)  # plate behind the picture; 0 = none


class OsdConfig(BaseModel):
    enabled: bool = True  # live service HUD (PTZ arrows, event badges, boxes, LEDs)
    style: OsdStyle = Field(default_factory=OsdStyle)
    preset: str = "default"
    time: OsdStamp = Field(default_factory=OsdStamp)
    date: OsdDateStamp = Field(default_factory=OsdDateStamp)
    image: OsdImage = Field(default_factory=OsdImage)


class EventsConfig(BaseModel):
    mode: EventMode = EventMode.timer
    motion_interval: float = Field(default=15.0, ge=1, le=3600)
    tamper_interval: float = Field(default=60.0, ge=1, le=3600)
    analytics_interval: float = Field(default=20.0, ge=1, le=3600)
    digital_input: bool = False
    randomize: bool = True
    hold_s: float = Field(default=3.0, ge=0.2, le=60)


class AnalyticsConfig(BaseModel):
    enabled: bool = True
    mode: Literal["simulator", "external"] = "simulator"
    motion_module: bool = True
    line_cross_module: bool = True
    field_detector_module: bool = True
    object_classes: list[str] = Field(default_factory=lambda: ["Person", "Vehicle", "Animal", "Unknown"])
    max_objects: int = Field(default=4, ge=0, le=32)
    box_speed: float = Field(default=0.08, ge=0, le=1)


class LoggingConfig(BaseModel):
    """Log levels are selected independently (multi-select), not as a threshold.

    Default ``levels=["CRITICAL"]`` ≈ off except critical/fatal crash traces.
    Legacy ``level`` is kept in sync with ``levels`` for older configs/CLI.
    """

    levels: list[str] = Field(default_factory=lambda: ["CRITICAL"])
    level: str = "CRITICAL"  # mirror of primary/legacy single value
    file: str = ""
    dir: str = ""  # if set (and file empty), write onvif-emulator.log into this directory
    console: bool = True  # mirror enabled levels to stderr (no effect for the windowed exe)
    max_mb: float = Field(default=5.0, ge=0.1, le=1024)  # rotate the log file at this size
    backups: int = Field(default=3, ge=0, le=100)  # rotated files to keep

    @field_validator("levels", mode="before")
    @classmethod
    def normalize_levels(cls, v: Any) -> list[str]:
        from onvif_emu.logging_setup import parse_log_levels

        if v is None or v == "":
            return ["CRITICAL"]
        if isinstance(v, str):
            return parse_log_levels(v)
        if isinstance(v, (list, tuple, set)):
            return parse_log_levels(list(v))
        return ["CRITICAL"]

    @field_validator("level", mode="before")
    @classmethod
    def normalize_level(cls, v: Any) -> str:
        from onvif_emu.logging_setup import normalize_level_name

        if isinstance(v, str):
            return normalize_level_name(v)
        return "CRITICAL"

    @model_validator(mode="before")
    @classmethod
    def migrate_level_to_levels(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        raw_levels = data.get("levels")
        raw_level = data.get("level")
        if (raw_levels is None or raw_levels == "" or raw_levels == []) and raw_level is not None:
            from onvif_emu.logging_setup import parse_log_levels

            data["levels"] = parse_log_levels(raw_level)
        return data

    @model_validator(mode="after")
    def sync_level_mirror(self) -> LoggingConfig:
        if self.levels:
            self.level = self.levels[0]
        else:
            self.levels = ["CRITICAL"]
            self.level = "CRITICAL"
        return self


class MediaMtxConfig(BaseModel):
    embedded: bool = True
    binary: str = ""
    # Host FFmpeg publishes to (127.0.0.1 for embedded; "mediamtx" in Compose).
    publish_host: str = "127.0.0.1"
    webrtc: bool = True
    hls: bool = True
    api: bool = True
    api_port: int = Field(default=9997, ge=1, le=65535)
    hls_port: int = Field(default=8888, ge=1, le=65535)
    webrtc_port: int = Field(default=8889, ge=1, le=65535)


class FfmpegConfig(BaseModel):
    binary: str = "ffmpeg"
    ffprobe: str = "ffprobe"
    extra_args: list[str] = Field(default_factory=list)


class TimeUnit(StrEnum):
    seconds = "seconds"
    minutes = "minutes"
    hours = "hours"


UNIT_SECONDS = {"seconds": 1.0, "minutes": 60.0, "hours": 3600.0}


class RuleKind(StrEnum):
    motion = "motion"  # tns1:VideoSource/MotionAlarm
    tamper = "tamper"  # tns1:VideoSource/Tampering
    analytics = "analytics"  # RuleEngine: field / line / cell motion / object detector
    relay = "relay"  # tns1:Device/Trigger/Relay + the relay output itself
    contact = "contact"  # dry contact: tns1:Device/Trigger/DigitalInput
    metadata = "metadata"  # analytics metadata frame with synthetic objects
    custom = "custom"  # any topic, any source/data items


class Schedule(StrEnum):
    manual = "manual"  # only fired by hand (button / API)
    interval = "interval"  # every `interval` <unit>
    random = "random"  # random delay in [range_min, range_max] <unit>, drawn again every time


class EventRule(BaseModel):
    """One event source of the diagnostic device. Add as many as needed, each with its own timer."""

    id: str = ""
    name: str = ""
    enabled: bool = True
    kind: RuleKind = RuleKind.motion
    device: int = Field(default=0, ge=0, le=256)  # fleet: 0 = every device, N = only device N
    channel: int = Field(default=0, ge=0, le=64)  # 0 = every enabled channel
    target: str = ""  # relay/contact token (relay_1 / di_1); empty = first one
    topic: str = ""  # custom topic, or a specific analytics topic
    source: dict[str, str] = Field(default_factory=dict)  # extra <tt:Source> SimpleItems
    data: dict[str, str] = Field(default_factory=dict)  # <tt:Data> SimpleItems; values may be templates
    action: Literal["pulse", "toggle", "on", "off"] = "pulse"
    hold_s: float = Field(default=3.0, ge=0.0, le=86400)  # how long a pulse stays active
    schedule: Schedule = Schedule.interval
    unit: TimeUnit = TimeUnit.seconds
    interval: float = Field(default=10.0, ge=0.05, le=1_000_000)
    range_min: float = Field(default=5.0, ge=0.05, le=1_000_000)
    range_max: float = Field(default=60.0, ge=0.05, le=1_000_000)
    repeat: int = Field(default=0, ge=0, le=10_000_000)  # 0 = until stopped
    start_delay_s: float = Field(default=0.0, ge=0, le=86400)
    push_url: str = ""  # additionally POST this event as JSON to this URL

    @field_validator("source", "data", mode="before")
    @classmethod
    def _stringify(cls, v: Any) -> Any:
        """SimpleItem values are text; accept numbers/bools from JSON and YAML."""
        if isinstance(v, dict):
            return {str(k): ("true" if x is True else "false" if x is False else str(x)) for k, x in v.items()}
        return v

    @model_validator(mode="after")
    def _fix(self) -> EventRule:
        if not self.id:
            import uuid

            self.id = uuid.uuid4().hex[:8]
        if not self.name:
            self.name = f"{self.kind.value}-{self.id[:4]}"
        if self.range_max < self.range_min:
            self.range_min, self.range_max = self.range_max, self.range_min
        return self


class DiagConfig(BaseModel):
    """Diagnostic device: dry contacts, relays, event rules, push targets."""

    relays: int = Field(default=2, ge=0, le=32)
    inputs: int = Field(default=2, ge=0, le=32)
    rules: list[EventRule] = Field(default_factory=list)
    webhooks: list[str] = Field(default_factory=list)  # every event is POSTed here as JSON
    notify: bool = True  # honour WS-BaseNotification Subscribe (push to ConsumerReference)
    log_size: int = Field(default=300, ge=10, le=100000)


class LimitsConfig(BaseModel):
    """Guard rails so a big fleet / wrong settings cannot freeze the machine.

    mode: off (no checks) | warn (log only) | clamp (reduce to fit, default) | strict (refuse to start).
    """

    mode: Literal["off", "warn", "clamp", "strict"] = "clamp"
    max_devices: int = Field(default=32, ge=1, le=256)
    max_channels: int = Field(default=64, ge=1, le=1024)  # over all devices
    max_streams: int = Field(default=48, ge=1, le=4096)  # simultaneously encoded streams (FFmpeg processes)
    max_load_pct: int = Field(default=85, ge=10, le=100)  # share of the CPU the planned encoding may use
    max_events_per_s: int = Field(default=200, ge=1, le=100000)
    max_rules: int = Field(default=500, ge=1, le=100000)
    max_upload_mb: int = Field(default=2048, ge=1, le=65536)  # max size of one browser video upload
    min_interval_s: float = Field(default=0.2, ge=0.01, le=60)  # fastest allowed rule timer
    cpu_high_pct: int = Field(default=92, ge=50, le=100)  # runtime: above this for ~10 s -> shed streams
    mem_high_pct: int = Field(default=90, ge=50, le=100)
    shed: bool = True  # drop the low/medium streams when the machine is overloaded, bring them back later
    stagger_ms: int = Field(default=250, ge=0, le=60000)  # pause between starting FFmpeg pipelines
    low_priority: bool = True  # run FFmpeg/MediaMTX below normal priority (the PC stays responsive)


class FleetConfig(BaseModel):
    """Run several emulated devices at once (one process, one HTTP+RTSP port pair each)."""

    count: int = Field(default=1, ge=1, le=256)
    onvif_ports: str = ""  # "8080-8089" (or empty = onvif_port, onvif_port+1, ...)
    rtsp_ports: str = ""  # "554-563"
    # per-device overrides, index 0 = second device; any AppConfig keys, merged over the shared settings
    devices: list[dict[str, Any]] = Field(default_factory=list)


class AppConfig(BaseModel):
    version: int = 1
    host: str = "0.0.0.0"
    public_host: str = ""
    onvif_port: int = Field(default=8080, ge=1, le=65535)
    discovery: bool = True
    gui: bool = True
    auto_port: bool = False  # if onvif_port is busy/forbidden, take the next free one
    snapshot: bool = True
    multicast_stream: bool = False
    language: Literal["ru", "en"] = "ru"
    state_path: str = ""
    config_dir_choice: Literal["beside", "appdata"] = "beside"
    video_dir: str = ""
    auth: AuthConfig = Field(default_factory=AuthConfig)
    device: DeviceConfig = Field(default_factory=DeviceConfig)
    network: NetworkConfig = Field(default_factory=NetworkConfig)
    ntp: NtpConfig = Field(default_factory=NtpConfig)
    users: list[UserRecord] = Field(default_factory=list, exclude=True)  # legacy input: moved to data/users.json
    channels: list[ChannelConfig] = Field(default_factory=list)
    ptz: PtzConfig = Field(default_factory=PtzConfig)
    imaging: ImagingConfig = Field(default_factory=ImagingConfig)
    osd: OsdConfig = Field(default_factory=OsdConfig)
    events: EventsConfig = Field(default_factory=EventsConfig)
    analytics: AnalyticsConfig = Field(default_factory=AnalyticsConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    limits: LimitsConfig = Field(default_factory=LimitsConfig)
    diag: DiagConfig = Field(default_factory=DiagConfig)
    fleet: FleetConfig = Field(default_factory=FleetConfig)
    instance: int = Field(default=1, ge=1, le=256)  # which fleet member this config drives (internal)
    mediamtx: MediaMtxConfig = Field(default_factory=MediaMtxConfig)
    ffmpeg: FfmpegConfig = Field(default_factory=FfmpegConfig)
    scopes: list[str] = Field(default_factory=list)

    @field_validator("host", mode="before")
    @classmethod
    def normalize_bind_host(cls, v: Any) -> str:
        if v is None:
            return "0.0.0.0"
        s = str(v).strip()
        if s in {"", "*", "all", "ANY", "any"}:
            return "0.0.0.0"
        return s

    @field_validator("language", mode="before")
    @classmethod
    def normalize_lang(cls, v: Any) -> str:
        if isinstance(v, str):
            return v.lower()[:2]
        return v

    @model_validator(mode="after")
    def fill_defaults(self) -> AppConfig:
        if not self.channels:
            self.channels = [
                ChannelConfig(id=1, name="Main", rtsp_path="ch1"),
            ]
        if not self.scopes:
            self.scopes = default_scopes(self.device)
        ids = [ch.id for ch in self.channels]
        if len(ids) != len(set(ids)):
            raise ValueError("Channel IDs must be unique")
        paths: set[tuple[int, str]] = set()
        for ch in self.channels:
            port = ch.rtsp_port or self.network.rtsp_port
            for suffix in ("", "_sub", "_third"):
                key = (port, ch.rtsp_path + suffix)
                if key in paths:
                    raise ValueError(f"Conflicting RTSP path on port {port}: {key[1]}")
                paths.add(key)
        self.network.http_port = self.onvif_port
        return self


ChannelConfig.model_rebuild()


def default_scopes(device: DeviceConfig) -> list[str]:
    return [
        "onvif://www.onvif.org/Profile/Streaming",
        "onvif://www.onvif.org/hardware/EMU-CAM-1",
        f"onvif://www.onvif.org/name/{device.name.replace(' ', '_')}",
        f"onvif://www.onvif.org/location/{device.location.replace(' ', '_')}",
        "onvif://www.onvif.org/Profile/T",
        "onvif://www.onvif.org/type/ptz",
    ]


def make_default() -> AppConfig:
    return AppConfig()
