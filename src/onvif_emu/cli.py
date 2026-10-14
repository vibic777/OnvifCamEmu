"""Command-line interface. Every GUI setting has a flag; see docs/CLI.md."""

from __future__ import annotations

import argparse
import asyncio
import logging
import re
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from onvif_emu import __version__
from onvif_emu.cheat import print_cheat
from onvif_emu.config import parse_channel_spec, startup_config
from onvif_emu.doctor import run_doctor
from onvif_emu.exit_codes import CONFIG_ERROR, OK, RUNTIME_ERROR, USAGE_ERROR
from onvif_emu.logging_setup import (
    current_log_path,
    flush_logging,
    install_crash_handlers,
    install_faulthandler_to_log,
    pause_if_double_clicked,
    peek_log_dir,
    peek_log_file,
    peek_log_levels,
    record_fatal_exit,
    resolve_log_path,
    setup_logging,
)
from onvif_emu.models import AppConfig, AuthMode, EventMode

HELP_EXAMPLES = """
Examples:
  1) One channel from a file (looped):
       onvif-emulator --no-gui --channel 1:source=./video.mp4,encoder=h264

  2) Restream an RTSP camera:
       onvif-emulator --channel 1:source=rtsp://192.168.1.20/stream1,transcode=false

  3) Eight test-pattern channels:
       onvif-emulator --channels 8 --auth digest --user admin --password admin

  4) Long-lived service with web GUI (leave running, configure in the browser):
       onvif-emulator --service --open-browser

  5) Anonymous, no discovery, no web GUI:
       onvif-emulator --headless --auth none --no-discovery --onvif-port 8080

  6) Validate config and exit:
       onvif-emulator --check-config -c ./config/default.yaml

  7) Environment diagnostics:
       onvif-emulator doctor

  8) Other ports, take the next free one if busy:
       onvif-emulator --service --onvif-port 18080 --rtsp-port 18554 --auto-port

  9) Logging: levels are picked independently, file and rotation are yours:
       onvif-emulator --log-level INFO,ERROR,CRITICAL -L D:/logs/emu.log --log-max-mb 20 --log-backups 5
       onvif-emulator -v --no-log-console --log-dir D:/logs

 10) Any setting by dotted path, then inspect the result without starting:
       onvif-emulator --set ptz.pan_speed=45 --set ntp.servers=[a,b] --print-config
       onvif-emulator --list-settings

 11) Constructor: 3 devices x 3 profiles (full/medium/low), events on timers, analytics on device 2 only:
       onvif-emulator --service --devices 3 --onvif-port 8080-8082 --rtsp-port 554-556 --streams 3 \\
         --rule "kind=tamper,every=30s" --rule "kind=contact,random=5-200s" \\
         --rule "kind=analytics,topic=line,device=2,every=1m" --device-set 3:channels.1.ptz_enabled=false

 12) Save the whole stand and start it later with no flags (onvif-emulator.conf next to the program is picked up):
       onvif-emulator --export-config onvif-emulator.conf --devices 3 --streams 3
       onvif-emulator --service

 13) Resource guard: look before you leap, or refuse oversized setups:
       onvif-emulator --check-config --devices 20 --streams 3
       onvif-emulator --limits strict --max-streams 24 --service --devices 10

Windowed exe (*-gui.exe) takes exactly the same flags; with no flags it behaves like
"--service --open-browser --auto-port". Without a console its --help/errors show in a window.

Config priority (high -> low): CLI flags (--set last)  >  --config file  >  onvif-emulator.conf beside the program  >  packaged default  >  ONVIF_EMU_* env.
Auto .conf: loaded if present; first run writes it. Skip with --no-conf.
Log file: onvif-emulator.log beside the program (override: -L / --log-file / --log-dir). Crashes also go to onvif-emulator-crash.log.
Exit codes: 0 ok, 1 config (also: limits.mode=strict refusal), 2 port busy, 3 missing dep (doctor), 4 runtime, 5 usage.
See also: cheat-sheet, docs/CLI.md, docs/TROUBLESHOOTING.md
"""



_ANCHORS = ("top-left", "top-center", "top-right", "middle-left", "center", "middle-right",
            "bottom-left", "bottom-center", "bottom-right", "custom")

# Flags generated from one table: (flag, config path, kind, help[, choices]).
# kind "bool" registers --flag / --no-flag; "list" may repeat. Anything not
# listed here is still reachable with --set PATH=VALUE (see --list-settings).
SETTING_FLAGS: list[tuple] = [
    # -- service / network
    ("--auto-port", "auto_port", "bool", "If the ONVIF/GUI port is busy or forbidden, use the next free one"),
    ("--https-port", "network.https_port", "int", "HTTPS port reported by GetNetworkProtocols (reserved, no listener)"),
    ("--multicast-stream", "multicast_stream", "bool", "Advertise multicast RTSP URIs"),
    ("--state-path", "state_path", "str", "Live PTZ/users state JSON (default data/state.json beside the program)"),
    ("--config-dir-choice", "config_dir_choice", "str", "Where to keep state: beside | appdata", ("beside", "appdata")),
    # -- security
    ("--embed-rtsp-credentials", "auth.embed_rtsp_credentials", "bool", "Put user:password@ into GetStreamUri URLs"),
    # -- device identity
    ("--device-name", "device.name", "str", "Device name (ONVIF scope / GetDeviceInformation)"),
    ("--device-location", "device.location", "str", "Location scope"),
    ("--device-hostname", "device.hostname", "str", "Hostname reported by GetHostname"),
    ("--device-timezone", "device.timezone", "str", "IANA time zone, e.g. Europe/Moscow"),
    ("--device-hardware-id", "device.hardware_id", "str", "HardwareId"),
    ("--dst", "device.daylight_savings", "bool", "Report daylight saving time"),
    ("--ntp", "ntp.enabled", "bool", "Enable the NTP settings block"),
    ("--ntp-server", "ntp.servers", "list", "NTP server (repeatable)"),
    # -- PTZ
    ("--ptz-pan-speed", "ptz.pan_speed", "float", "Pan speed, deg/s"),
    ("--ptz-tilt-speed", "ptz.tilt_speed", "float", "Tilt speed, deg/s"),
    ("--ptz-zoom-speed", "ptz.zoom_speed", "float", "Zoom speed, units/s"),
    ("--ptz-automove", "ptz.automove", "bool", "Auto-patrol"),
    ("--ptz-flip", "ptz.flip", "bool", "Flip / mirror"),
    ("--ptz-tours", "ptz.tours_enabled", "bool", "Enable preset tours"),
    # -- imaging
    ("--imaging-brightness", "imaging.brightness", "int", "0..100"),
    ("--imaging-contrast", "imaging.contrast", "int", "0..100"),
    ("--imaging-saturation", "imaging.saturation", "int", "0..100"),
    ("--imaging-sharpness", "imaging.sharpness", "int", "0..100"),
    # -- OSD
    ("--osd-color", "osd.style.color", "str", "OSD colour, #RRGGBB"),
    ("--osd-opacity", "osd.style.opacity", "float", "OSD opacity 0..1"),
    ("--osd-size", "osd.style.size", "int", "OSD font size, px"),
    (
        "--osd-position",
        "osd.style.position",
        "str",
        "OSD position",
        ("top-left", "top-right", "bottom-left", "bottom-right", "center"),
    ),
    ("--osd-duration-ms", "osd.style.duration_ms", "int", "How long an OSD notice stays, ms"),
    ("--osd-time", "osd.time.enabled", "bool", "Time stamp burnt into the video"),
    ("--osd-time-format", "osd.time.format", "str", "Time stamp strftime format, e.g. %H:%M:%S"),
    ("--osd-time-position", "osd.time.position", "str", "Time stamp position", _ANCHORS),
    ("--osd-time-size", "osd.time.size", "int", "Time stamp font size, px at 1080 lines"),
    ("--osd-time-color", "osd.time.color", "str", "Time stamp colour, #RRGGBB"),
    ("--osd-time-bg", "osd.time.bg_color", "str", "Time stamp background colour, #RRGGBB"),
    ("--osd-time-bg-opacity", "osd.time.bg_opacity", "float", "Time stamp background opacity, 0 = transparent"),
    ("--osd-date", "osd.date.enabled", "bool", "Date stamp burnt into the video"),
    ("--osd-date-format", "osd.date.format", "str", "Date stamp strftime format, e.g. %d.%m.%Y"),
    ("--osd-date-position", "osd.date.position", "str", "Date stamp position", _ANCHORS),
    ("--osd-date-size", "osd.date.size", "int", "Date stamp font size, px at 1080 lines"),
    ("--osd-date-color", "osd.date.color", "str", "Date stamp colour, #RRGGBB"),
    ("--osd-date-bg", "osd.date.bg_color", "str", "Date stamp background colour, #RRGGBB"),
    ("--osd-date-bg-opacity", "osd.date.bg_opacity", "float", "Date stamp background opacity, 0 = transparent"),
    ("--osd-image", "osd.image.path", "str", "Picture (logo) burnt into the video: path on this machine"),
    ("--osd-image-show", "osd.image.enabled", "bool", "Show the OSD picture"),
    ("--osd-image-position", "osd.image.position", "str", "OSD picture position", _ANCHORS),
    ("--osd-image-size", "osd.image.size", "float", "OSD picture width, % of the frame"),
    ("--osd-image-opacity", "osd.image.opacity", "float", "OSD picture opacity 0..1"),
    ("--osd-image-key", "osd.image.transparent_color", "str", "Colour of the picture made transparent, #RRGGBB"),
    ("--osd-image-bg", "osd.image.bg_color", "str", "Plate colour behind the picture, #RRGGBB"),
    ("--osd-image-bg-opacity", "osd.image.bg_opacity", "float", "Plate opacity, 0 = none"),
    # -- events / analytics
    ("--event-analytics-interval", "events.analytics_interval", "float", "Seconds between analytics events"),
    ("--event-hold", "events.hold_s", "float", "Seconds an event stays active"),
    ("--event-randomize", "events.randomize", "bool", "Jitter event timing"),
    ("--digital-input", "events.digital_input", "bool", "Emit digital-input events"),
    ("--analytics", "analytics.enabled", "bool", "Analytics simulator"),
    ("--analytics-mode", "analytics.mode", "str", "simulator | external", ("simulator", "external")),
    ("--analytics-max-objects", "analytics.max_objects", "int", "Max simulated objects per frame"),
    ("--analytics-box-speed", "analytics.box_speed", "float", "Simulated box speed 0..1"),
    # -- media plane
    ("--mediamtx", "mediamtx.embedded", "bool", "Spawn the embedded MediaMTX"),
    ("--mediamtx-binary", "mediamtx.binary", "str", "MediaMTX executable (default: bundled / PATH)"),
    ("--mediamtx-publish-host", "mediamtx.publish_host", "str", "Host FFmpeg publishes to"),
    ("--mediamtx-api", "mediamtx.api", "bool", "MediaMTX control API"),
    ("--mediamtx-api-port", "mediamtx.api_port", "int", "MediaMTX API port"),
    ("--hls", "mediamtx.hls", "bool", "HLS preview"),
    ("--hls-port", "mediamtx.hls_port", "int", "HLS port"),
    ("--webrtc", "mediamtx.webrtc", "bool", "WebRTC preview"),
    ("--webrtc-port", "mediamtx.webrtc_port", "int", "WebRTC port"),
    ("--ffmpeg", "ffmpeg.binary", "str", "FFmpeg executable (default: bundled / PATH)"),
    ("--ffprobe", "ffmpeg.ffprobe", "str", "ffprobe executable"),
    ("--ffmpeg-arg", "ffmpeg.extra_args", "list", "Extra FFmpeg argument (repeatable; write --ffmpeg-arg=-x)"),
    # -- diagnostics device
    ("--relays", "diag.relays", "int", "Number of simulated relay outputs"),
    ("--inputs", "diag.inputs", "int", "Number of simulated dry-contact inputs"),
    ("--webhook", "diag.webhooks", "list", "POST every event as JSON to this URL (repeatable)"),
    ("--diag-notify", "diag.notify", "bool", "Honour WS-BaseNotification Subscribe (push Notify to the consumer)"),
    # -- resource limits
    ("--limits", "limits.mode", "str", "Resource guard: off | warn | clamp | strict", ("off", "warn", "clamp", "strict")),
    ("--max-devices", "limits.max_devices", "int", "Hard cap on emulated devices"),
    ("--max-channels", "limits.max_channels", "int", "Hard cap on channels over all devices"),
    ("--max-streams", "limits.max_streams", "int", "Hard cap on simultaneously encoded streams"),
    ("--max-load", "limits.max_load_pct", "int", "Share of the CPU (percent) planned encoding may use"),
    ("--max-events-per-sec", "limits.max_events_per_s", "int", "Event rate cap (excess is dropped and counted)"),
    ("--max-rules", "limits.max_rules", "int", "Maximum number of event rules"),
    ("--min-timer-interval", "limits.min_interval_s", "float", "Fastest allowed rule timer, seconds"),
    ("--cpu-high", "limits.cpu_high_pct", "int", "CPU percent above which streams are shed"),
    ("--shed", "limits.shed", "bool", "Drop low/medium streams on overload, restore when calm"),
    ("--stagger-ms", "limits.stagger_ms", "int", "Pause between starting FFmpeg pipelines, ms"),
    ("--low-priority", "limits.low_priority", "bool", "Run FFmpeg/MediaMTX below normal priority"),
    # -- logging
    ("--log-console", "logging.console", "bool", "Mirror enabled log levels to the console (stderr)"),
    ("--log-max-mb", "logging.max_mb", "float", "Rotate the log file at this size, MB"),
    ("--log-backups", "logging.backups", "int", "Rotated log files to keep"),
]


def _dest(path: str) -> str:
    return "s_" + path.replace(".", "_")


_KIND_TYPES = {"int": int, "float": float, "str": str}


def _port_spec(text: str):
    """'8080' -> 8080, '8080-8089' or '8080,8085' -> the string (a fleet port list)."""
    t = text.strip()
    if t.isdigit():
        port = int(t)
        if not 1 <= port <= 65535:
            raise argparse.ArgumentTypeError(f"port out of range: {port}")
        return port
    from onvif_emu.fleet import parse_ports

    try:
        parse_ports(t, 1, 1)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"bad port list {text!r}: {exc}") from exc
    return t


def _register_settings(p: argparse.ArgumentParser) -> None:
    g = p.add_argument_group("more settings (each is also reachable as --set PATH=VALUE; see --list-settings)")
    for row in SETTING_FLAGS:
        flag, path, kind, help_ = row[:4]
        choices = row[4] if len(row) > 4 else None
        dest = _dest(path)
        text = f"{help_} [{path}]".replace("%", "%%")  # argparse formats help with %
        meta = {"int": "N", "float": "X", "str": "TEXT", "list": "TEXT"}.get(kind)
        if kind == "bool":
            g.add_argument(flag, dest=dest, action="store_true", default=None, help=f"{text}; --no-{flag[2:]} disables")
            g.add_argument("--no-" + flag[2:], dest=dest, action="store_false", help=argparse.SUPPRESS)
        elif kind == "list":
            g.add_argument(flag, dest=dest, action="append", default=None, metavar=meta, help=text)
        else:
            g.add_argument(
                flag, dest=dest, type=_KIND_TYPES[kind], choices=choices, default=None,
                metavar=None if choices else meta, help=text,
            )


def _set_path(root: dict[str, Any], path: str, value: Any) -> None:
    cur = root
    parts = path.split(".")
    for part in parts[:-1]:
        nxt = cur.get(part)
        if not isinstance(nxt, dict):
            nxt = cur[part] = {}
        cur = nxt
    cur[parts[-1]] = value


def _settable_paths() -> dict[str, str]:
    """Every dotted AppConfig path that --set may address (channels use --channel)."""
    from pydantic import BaseModel

    out: dict[str, str] = {}

    def walk(model: type[BaseModel], prefix: str) -> None:
        for name, field in model.model_fields.items():
            path = f"{prefix}{name}"
            ann = field.annotation
            if isinstance(ann, type) and issubclass(ann, BaseModel):
                walk(ann, path + ".")
            else:
                out[path] = str(getattr(ann, "__name__", ann)).replace("typing.", "")

    walk(AppConfig, "")
    for skip in ("version", "channels", "users", "logging.level"):
        out.pop(skip, None)
    return out


def parse_set_items(items: Sequence[str] | None) -> dict[str, Any]:
    """Turn ['a.b=1', 'c=[x,y]'] into a nested overlay; values are parsed as YAML scalars."""
    import difflib

    import yaml

    known = _settable_paths()
    overlay: dict[str, Any] = {}
    for item in items or []:
        if "=" not in item:
            raise ValueError(f"--set expects PATH=VALUE, got {item!r}")
        path, raw = item.split("=", 1)
        path = path.strip().replace("-", "_")
        if re.match(r"^channels\.\d+\.", path):
            overlay[path] = yaml.safe_load(raw) if raw.strip() else ""  # flat key, split by the constructor
            continue
        if path not in known:
            hint = difflib.get_close_matches(path, list(known), n=3)
            extra = f" (did you mean: {', '.join(hint)})" if hint else ""
            raise ValueError(f"unknown setting {path!r}{extra}; run --list-settings")
        try:
            value = yaml.safe_load(raw)
        except yaml.YAMLError:
            value = raw
        _set_path(overlay, path, "" if raw.strip() == "" else value)
    return overlay


def dump_config_text(cfg: AppConfig, fmt: str = "yaml") -> str:
    import json

    import yaml

    data = json.loads(cfg.model_dump_json())
    if fmt == "json":
        return json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)


def list_settings_text() -> str:
    from onvif_emu.models import make_default

    defaults = make_default().model_dump(mode="json")
    lines = ["Settable with --set PATH=VALUE (dotted path; value is YAML: true, 8080, [a, b], text).", ""]
    for path, typ in sorted(_settable_paths().items()):
        cur: Any = defaults
        for part in path.split("."):
            cur = cur.get(part) if isinstance(cur, dict) else None
        lines.append(f"  {path:<34} {typ:<12} default={cur!r}")
    lines += ["", "Channels: --channels N and --channel SPEC. Users: config file / web GUI.", ""]
    return "\n".join(lines)


class _Parser(argparse.ArgumentParser):
    """argparse that exits with our USAGE_ERROR (5) instead of argparse's 2 (= port busy here)."""

    def error(self, message: str):  # type: ignore[override]
        self.print_usage(sys.stderr)
        sys.stderr.write(f"{self.prog}: error: {message}\nSee --help\n")
        raise SystemExit(USAGE_ERROR)


def build_parser() -> argparse.ArgumentParser:
    p = _Parser(
        prog="onvif-emulator",
        usage="onvif-emulator [options] [doctor | cheat-sheet [topic]]",
        description="ONVIF Profile S camera emulator with PTZ, restreamer and stream multiplier.",
        epilog=HELP_EXAMPLES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--version", action="version", version=f"onvif-emulator {__version__}")
    p.add_argument("-c", "--config", type=Path, help="YAML/JSON config path")
    p.add_argument("--check-config", action="store_true", help="Validate config and exit")
    p.add_argument(
        "--print-config",
        nargs="?",
        const="yaml",
        choices=["yaml", "json"],
        default=None,
        metavar="FORMAT",
        help="Print the effective config (after all overrides) as yaml|json and exit",
    )
    p.add_argument("--list-settings", action="store_true", help="List every setting reachable through --set and exit")
    p.add_argument(
        "--set",
        dest="set_items",
        action="append",
        default=[],
        metavar="PATH=VALUE",
        help="Set any config value by dotted path, repeatable (e.g. --set ptz.pan_speed=45 --set ntp.servers=[a,b])",
    )
    p.add_argument("--host", default=None, help="Bind address (default 0.0.0.0)")
    p.add_argument("--onvif-port", type=_port_spec, default=None, metavar="PORT|A-B",
                   help="HTTP/ONVIF/GUI port (default 8080); a range like 8080-8089 starts one device per port")
    p.add_argument("--rtsp-port", type=_port_spec, default=None, metavar="PORT|A-B",
                   help="MediaMTX RTSP port (default 8554); a range like 554-563 gives each device its own")
    p.add_argument("--devices", type=int, default=None, metavar="N",
                   help="Run N emulated devices at once (ports step up from the base, or follow the ranges)")
    p.add_argument("--streams", type=int, choices=(1, 2, 3), default=None,
                   help="Profiles per channel: 1 full, 2 = + medium, 3 = + low (quality ladder built automatically)")
    p.add_argument("--source", default=None, metavar="URI",
                   help="One video (file/rtsp/http/testsrc) for every channel; each streams it in its own quality")
    p.add_argument("--impair", action="append", default=[], metavar="N:SPEC",
                   help="Trouble simulator for channel N (0 = all), e.g. 1:rain=0.6,shake=0.4,shake_every=15s,"
                        "shake_for=3s,cover=black,loss=3,stutter=10,freeze=on,bitrate=vbr_jumps,outage=1-5m,outage_len=5-20s")
    p.add_argument("--rule", action="append", default=[], metavar="SPEC",
                   help="Add an event rule, e.g. kind=tamper,every=30s | kind=contact,random=5-200s,device=1")
    p.add_argument("--device-set", action="append", default=[], metavar="N:PATH=VALUE",
                   help="--set for device N only (N>=2 writes fleet.devices; channels.K.PATH allowed)")
    p.add_argument("--export-config", default=None, metavar="FILE",
                   help="Write the complete effective configuration (fleet, rules, channels) to FILE and exit; "
                        ".conf / .yaml / .json by extension")
    p.add_argument("--public-host", default=None, help="Host advertised in XAddr / RTSP URIs")
    p.add_argument("--discovery", dest="discovery", action="store_true", default=None)
    p.add_argument("--no-discovery", dest="discovery", action="store_false")
    p.add_argument("--auth", choices=[m.value for m in AuthMode], default=None)
    p.add_argument("--user", dest="username", default=None)
    p.add_argument("--password", default=None,
                   help="Password of --user (default admin) for this and later runs (stored encrypted in data/users.json)")
    p.add_argument("--add-user", action="append", default=[], metavar="NAME:PASSWORD:ROLE[:CH,CH]",
                   help="Add or update an account: role Administrator | Operator | Viewer, optional allowed channels")
    p.add_argument("--reset-admin", action="store_true",
                   help="Set the admin password back to admin (same as deleting its password line in data/users.json)")
    p.add_argument("--channels", type=int, default=None, metavar="N", help="Create N testsrc channels (1..64)")
    p.add_argument(
        "--channel",
        action="append",
        default=[],
        metavar="SPEC",
        help="N:source=<uri>,encoder=h264,width=1920,height=1080,fps=25,bitrate=4000",
    )
    p.add_argument("--ptz-pan-limit", type=float, default=None, help="Absolute pan limit degrees (applies ±)")
    p.add_argument("--ptz-tilt-limit", type=float, default=None)
    p.add_argument("--ptz-zoom-limit", type=float, default=None, help="Max zoom (0..1)")
    p.add_argument("--events", choices=[m.value for m in EventMode], default=None)
    p.add_argument("--event-motion-interval", type=float, default=None)
    p.add_argument("--event-tamper-interval", type=float, default=None)
    p.add_argument("--osd", choices=["on", "off"], default=None)
    p.add_argument("--osd-style", default=None, help="OSD preset name (stored in config.osd.preset)")
    p.add_argument("--device-manufacturer", default=None)
    p.add_argument("--device-model", default=None)
    p.add_argument("--device-firmware", default=None)
    p.add_argument("--device-serial", default=None)
    p.add_argument("--scopes", default=None, help="Space-separated ONVIF scopes")
    p.add_argument("--snapshot", choices=["on", "off"], default=None)
    p.add_argument("--gui", dest="gui", action="store_true", default=None)
    p.add_argument("--no-gui", dest="gui", action="store_false")
    p.add_argument("--headless", action="store_true", help="Alias for --no-gui (no web UI). Do not use this if you want to configure in the browser.")
    p.add_argument(
        "--service",
        action="store_true",
        help="Run as a long-lived server: web GUI stays ON (open http://IP:8080/). Implies not exiting.",
    )
    p.add_argument(
        "--open-browser", dest="open_browser", action="store_true", default=None,
        help="Open the web GUI in the default browser (default in the windowed exe)",
    )
    p.add_argument(
        "--no-open-browser", dest="open_browser", action="store_false",
        help="Do not open the browser",
    )
    p.add_argument(
        "--tray", dest="tray", action="store_true", default=None,
        help="Windows notification-area icon (default ON in the windowed exe, OFF in the console exe)",
    )
    p.add_argument(
        "--no-tray", dest="tray", action="store_false",
        help="No tray icon: pure background service, only Task Manager shows it",
    )
    p.add_argument(
        "--video-dir",
        default=None,
        help="Folder for videos uploaded from the GUI (default: video/ next to the program)",
    )
    p.add_argument(
        "--log-level",
        "--loglevel",
        "--log-levels",
        dest="log_level",
        default=None,
        help=(
            "Enabled log levels (independent multi-select), comma-separated: "
            "TRACE,DEBUG,INFO,WARNING,ERROR,CRITICAL or ALL. Default: CRITICAL only."
        ),
    )
    p.add_argument("-v", "--verbose", action="store_true", help="Enable all log levels (same as --log-level ALL)")
    p.add_argument("-q", "--quiet", action="store_true", help="Enable ERROR+CRITICAL only")
    p.add_argument(
        "-L",
        "--log-file",
        "--logfile",
        dest="log_file",
        default=None,
        help="Log file path (default: onvif-emulator.log next to the program)",
    )
    p.add_argument(
        "--log-dir",
        "--logdir",
        dest="log_dir",
        default=None,
        help="Directory for onvif-emulator.log (used when --log-file is not set)",
    )
    p.add_argument(
        "-l",
        "--lang",
        "--language",
        dest="language",
        choices=["ru", "en"],
        default=None,
        help="GUI language and first-run .conf comments (default ru). CLI output is always English.",
    )
    p.add_argument(
        "--no-conf",
        action="store_true",
        help="Do not auto-load or auto-create onvif-emulator.conf next to the program",
    )

    _register_settings(p)

    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("doctor", help="Diagnose ports, FFmpeg, MediaMTX, multicast")
    cs = sub.add_parser("cheat-sheet", help="Print in-app cheat sheets")
    cs.add_argument("topic", nargs="?", default=None, help="onvif|launch|events|exit|odm")
    return p


def overlay_from_args(ns: argparse.Namespace) -> dict[str, Any]:
    o: dict[str, Any] = {}

    def put(path: str, value: Any) -> None:
        if value is None:
            return
        cur = o
        parts = path.split(".")
        for p in parts[:-1]:
            cur = cur.setdefault(p, {})
        cur[parts[-1]] = value

    from onvif_emu.fleet import parse_ports

    put("host", ns.host)
    if isinstance(ns.onvif_port, str):
        put("onvif_port", parse_ports(ns.onvif_port, 8080, 1)[0])
        put("fleet.onvif_ports", ns.onvif_port)
    else:
        put("onvif_port", ns.onvif_port)
    put("fleet.count", ns.devices)
    put("public_host", ns.public_host)
    put("discovery", ns.discovery)
    gui = False if ns.headless else ns.gui
    if ns.service and not ns.headless:
        gui = True if gui is None else gui
    put("gui", gui)
    put("video_dir", ns.video_dir)
    put("auth.mode", ns.auth)
    put("auth.username", ns.username)
    put("auth.password", ns.password)
    if isinstance(ns.rtsp_port, str):
        put("network.rtsp_port", parse_ports(ns.rtsp_port, 8554, 1)[0])
        put("fleet.rtsp_ports", ns.rtsp_port)
    else:
        put("network.rtsp_port", ns.rtsp_port)
    put("events.mode", ns.events)
    put("events.motion_interval", ns.event_motion_interval)
    put("events.tamper_interval", ns.event_tamper_interval)
    if ns.osd == "on":
        put("osd.enabled", True)
    elif ns.osd == "off":
        put("osd.enabled", False)
    put("osd.preset", ns.osd_style)
    put("device.manufacturer", ns.device_manufacturer)
    put("device.model", ns.device_model)
    put("device.firmware", ns.device_firmware)
    put("device.serial", ns.device_serial)
    put("snapshot", True if ns.snapshot == "on" else False if ns.snapshot == "off" else None)
    level = ns.log_level
    if level is None and ns.verbose:
        level = "ALL"
    elif level is None and ns.quiet:
        level = "ERROR,CRITICAL"
    if level is not None:
        from onvif_emu.logging_setup import parse_log_levels

        put("logging.levels", parse_log_levels(level))
    put("logging.file", ns.log_file)
    put("logging.dir", getattr(ns, "log_dir", None))
    put("language", ns.language)
    if ns.scopes:
        put("scopes", ns.scopes.split())
    if ns.ptz_pan_limit is not None:
        put("ptz.pan_min", -abs(ns.ptz_pan_limit))
        put("ptz.pan_max", abs(ns.ptz_pan_limit))
    if ns.ptz_tilt_limit is not None:
        put("ptz.tilt_min", -abs(ns.ptz_tilt_limit))
        put("ptz.tilt_max", abs(ns.ptz_tilt_limit))
    if ns.ptz_zoom_limit is not None:
        put("ptz.zoom_max", ns.ptz_zoom_limit)
    for row in SETTING_FLAGS:
        val = getattr(ns, _dest(row[1]), None)
        if val is not None:
            put(row[1], val)
    # --set wins over every dedicated flag; channels are handled below
    from onvif_emu.config import merge_dict

    set_over = parse_set_items(getattr(ns, "set_items", None))
    from onvif_emu.constructor import split_channel_paths

    ch_patches = split_channel_paths(set_over)
    o = merge_dict(o, set_over)
    devices: list[dict[str, Any]] = []
    for item in getattr(ns, "device_set", None) or []:
        num, _, rest = item.partition(":")
        if not num.isdigit() or not rest:
            raise ValueError(f"--device-set expects N:PATH=VALUE, got {item!r}")
        idx = int(num)
        dev_over = parse_set_items([rest])
        dev_patches = split_channel_paths(dev_over)
        if idx == 1:
            o = merge_dict(o, dev_over)
            for cid, pv in dev_patches.items():
                ch_patches.setdefault(cid, {}).update(pv)
            continue
        while len(devices) < idx - 1:
            devices.append({})
        devices[idx - 2] = merge_dict(devices[idx - 2], dev_over)
        if dev_patches:
            devices[idx - 2].setdefault("channels_patch", {}).update({str(k): v for k, v in dev_patches.items()})
    if devices:
        o.setdefault("fleet", {})["devices"] = devices
    if ch_patches:
        o["_ch_patches"] = ch_patches
    channels: list[dict[str, Any]] = []
    if ns.channels:
        n = max(1, min(64, ns.channels))
        channels = [{"id": i, "name": f"Channel {i}", "rtsp_path": f"ch{i}"} for i in range(1, n + 1)]
    if ns.channel:
        by_id = {c["id"]: c for c in channels}
        for spec in ns.channel:
            parsed = parse_channel_spec(spec)
            cid = parsed["id"]
            if cid in by_id:
                by_id[cid] = {**by_id[cid], **parsed}
            else:
                by_id[cid] = parsed
        channels = [by_id[k] for k in sorted(by_id)]
    if channels:
        o["channels"] = channels
    return o


def load_from_namespace(ns: argparse.Namespace) -> AppConfig:
    overlay = overlay_from_args(ns)
    no_conf = bool(getattr(ns, "no_conf", False))
    cfg, _, _ = startup_config(
        explicit=ns.config,
        no_conf=no_conf,
        cli_overlay=overlay,
        create_missing=False,
    )
    return cfg


def _post_process(cfg: AppConfig, ns: argparse.Namespace, patches: dict[int, dict[str, Any]]) -> None:
    """Constructor flags that act on the loaded config (order matters: shape, video, patches, ladder, rules)."""
    from onvif_emu import constructor

    if ns.channels:
        constructor.make_channels(cfg, ns.channels)
    if ns.source:
        constructor.apply_source(cfg, ns.source)
    constructor.apply_channel_patches(cfg, patches)
    if ns.streams:
        constructor.apply_streams(cfg, ns.streams)
    for spec in ns.rule or []:
        cfg.diag.rules.append(constructor.parse_rule_spec(spec))
    for spec in getattr(ns, "add_user", None) or []:
        cfg.users.append(constructor.parse_user_spec(spec))
    if getattr(ns, "reset_admin", False):
        cfg.auth.username, cfg.auth.password = "admin", "admin"
    for spec in getattr(ns, "impair", None) or []:
        constructor.apply_impair(cfg, spec)
    if getattr(ns, "s_osd_image_path", None):
        cfg.osd.image.enabled = True  # giving a picture means showing it


async def _serve(cfg: AppConfig, config_path: Path | None, open_browser: bool = False, tray: bool = False) -> int:
    """Run the configured fleet (a single device is a fleet of one) under the resource limits."""
    from onvif_emu.fleet import Fleet
    from onvif_emu.limits import LimitError, enforce
    from onvif_emu.util import set_low_priority

    set_low_priority(cfg.limits.low_priority and cfg.limits.mode != "off")
    try:
        plan, notes = enforce(cfg)
    except LimitError as exc:
        print(f"Refusing to start: {exc}", file=sys.stderr)
        logging.getLogger("onvif_emu").critical("Refusing to start: %s", exc)
        record_fatal_exit(CONFIG_ERROR, f"limits: {exc}")
        return CONFIG_ERROR
    for note in notes:
        print(f"limits: {note}", flush=True)
        logging.getLogger("onvif_emu").warning("limits: %s", note)
    return await Fleet(plan, config_path).run(open_browser=open_browser, tray=tray)


def main(argv: Sequence[str] | None = None) -> int:
    code = RUNTIME_ERROR
    try:
        code = _main(argv)
        return code
    finally:
        flush_logging()
        pause_if_double_clicked(code)


def _main(argv: Sequence[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    early = peek_log_file(argv)
    early_dir = peek_log_dir(argv)
    setup_logging(levels=peek_log_levels(argv), log_file=early, log_dir=early_dir)
    install_crash_handlers()
    try:
        install_faulthandler_to_log()
    except Exception as _fh_exc:  # noqa: BLE001
        logging.getLogger("onvif_emu").critical("faulthandler setup failed: %s", _fh_exc)
    log = logging.getLogger("onvif_emu")

    parser = build_parser()
    try:
        ns = parser.parse_args(argv)
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else USAGE_ERROR
        return code

    if ns.cmd == "cheat-sheet":
        sys.stdout.write(print_cheat(ns.topic))
        return OK

    try:
        parse_set_items(ns.set_items)
    except ValueError as exc:
        print(f"onvif-emulator: error: {exc}\nSee --help", file=sys.stderr)
        return USAGE_ERROR

    if ns.list_settings:
        sys.stdout.write(list_settings_text())
        return OK

    create_missing = ns.cmd is None and not ns.check_config and ns.print_config is None
    try:
        overlay = overlay_from_args(ns)
        cfg, conf_path, created = startup_config(
            explicit=ns.config,
            no_conf=ns.no_conf,
            cli_overlay=overlay,
            create_missing=create_missing,
        )
    except Exception as exc:
        log.exception("Config error")
        print(f"Config error: {exc}\nSee --help (log: {current_log_path()})", file=sys.stderr)
        record_fatal_exit(CONFIG_ERROR, f"Config error: {exc}")
        return CONFIG_ERROR

    if ns.print_config is not None:
        sys.stdout.write(dump_config_text(cfg, ns.print_config))
        return OK

    try:
        patches = overlay.pop("_ch_patches", {}) if isinstance(overlay, dict) else {}
        _post_process(cfg, ns, patches)
    except (ValueError, KeyError) as exc:
        print(f"onvif-emulator: error: {exc}\nSee --help", file=sys.stderr)
        return USAGE_ERROR

    if ns.export_config:
        from onvif_emu.fleet import _write_root

        dest = Path(ns.export_config)
        _write_root(cfg, dest)
        print(f"Wrote configuration: {dest}")
        return OK

    if created and conf_path:
        msg = f"Wrote first-run config: {conf_path}"
        print(msg)
        log.info("%s", msg)

    log_path = resolve_log_path(
        cfg.logging.file,
        ns.log_file,
        log_dir=cfg.logging.dir or None,
        cli_dir=ns.log_dir,
    )
    cfg.logging.file = str(log_path)
    setup_logging(
        levels=cfg.logging.levels,
        log_file=log_path,
        console=cfg.logging.console,
        max_mb=cfg.logging.max_mb,
        backups=cfg.logging.backups,
    )
    print(f"Log file: {log_path}", flush=True)
    log.info(
        "Starting onvif-emulator v%s log=%s levels=%s",
        __version__,
        log_path,
        ",".join(cfg.logging.levels),
    )
    log.info(
        "cwd=%s argv=%r host=%s onvif_port=%s rtsp_port=%s gui=%s discovery=%s auth=%s",
        Path.cwd(),
        argv,
        cfg.host,
        cfg.onvif_port,
        cfg.network.rtsp_port,
        cfg.gui,
        cfg.discovery,
        cfg.auth.mode.value,
    )

    if ns.cmd == "doctor":
        code, text = run_doctor(cfg)
        sys.stdout.write(text)
        return code

    if ns.check_config:
        from onvif_emu.fleet import build_fleet
        from onvif_emu.limits import LimitError, describe, enforce, estimate

        print("Config OK")
        try:
            plan, notes = enforce(cfg)
            for note in notes:
                print(f"  limits: {note}")
            print(f"  load: {describe(estimate(build_fleet(plan)))}")
        except LimitError as exc:
            print(f"  limits: {exc}")
        print(f"  channels={len(cfg.channels)} auth={cfg.auth.mode.value} port={cfg.onvif_port}")
        print(f"  log={cfg.logging.file} levels={','.join(cfg.logging.levels)}")
        print(f"  host={cfg.host} rtsp_port={cfg.network.rtsp_port} auto_port={cfg.auto_port} gui={cfg.gui}")
        return OK

    try:
        if ns.service and ns.headless:
            log.warning("--service with --headless: web GUI is disabled; drop --headless to configure in the browser")
        code = asyncio.run(
            _serve(
                cfg,
                conf_path if conf_path is not None else ns.config,
                open_browser=bool(ns.open_browser),
                tray=bool(ns.tray),
            )
        )
        if code not in (None, 0, OK):
            record_fatal_exit(int(code), f"serve exited with code {code}")
        return code
    except KeyboardInterrupt:
        log.info("Interrupted")
        return OK
    except Exception as exc:
        log.exception("Runtime error")
        print(f"Runtime error: {exc}\nSee --help (log: {log_path})", file=sys.stderr)
        record_fatal_exit(RUNTIME_ERROR, f"Runtime error: {exc}")
        return RUNTIME_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
