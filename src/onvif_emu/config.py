from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import yaml

from onvif_emu.confdump import beside_conf_path, write_conf
from onvif_emu.models import AppConfig, ChannelConfig, EncoderConfig, SourceConfig, SourceType
from onvif_emu.paths import executable_dir, package_dir

ENV_PREFIX = "ONVIF_EMU_"
DOTENV_NAME = ".env"


def load_dotenv_file(path: Path | str, *, override: bool = False) -> int:
    """Parse a KEY=VALUE file into os.environ. Returns number of keys applied.

    Does not override keys already in the environment unless override=True.
    """
    p = Path(path)
    if not p.is_file():
        return 0
    applied = 0
    for raw_line in p.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        key = key.strip()
        if not key or key.startswith("#"):
            continue
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in {"'", '"'}:
            val = val[1:-1]
        if not override and key in os.environ:
            continue
        os.environ[key] = val
        applied += 1
    return applied


def apply_dotenv() -> list[Path]:
    """Load .env from launch dir, then cwd. First file wins per key (no override)."""
    seen: set[Path] = set()
    loaded: list[Path] = []
    for cand in (executable_dir() / DOTENV_NAME, Path.cwd() / DOTENV_NAME):
        try:
            rp = cand.resolve()
        except OSError:
            rp = cand
        if rp in seen or not cand.is_file():
            continue
        seen.add(rp)
        if load_dotenv_file(cand, override=False):
            loaded.append(cand)
        elif cand.is_file():
            loaded.append(cand)
    return loaded


def appdata_dir() -> Path:
    if os.name == "nt":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / "onvif-emulator"
    return Path.home() / ".config" / "onvif-emulator"


def beside_exe_dir() -> Path:
    return executable_dir()


def default_config_path() -> Path:
    packaged = package_dir() / "data" / "default.yaml"
    if packaged.is_file():
        return packaged
    return Path("config") / "default.yaml"


def load_yaml(path: Path | str) -> dict[str, Any]:
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"Config file not found: {p}")
    text = p.read_text(encoding="utf-8")
    data = yaml.safe_load(text) or {}
    if not isinstance(data, dict):
        raise ValueError("Config root must be a mapping")
    return data


def dump_yaml(cfg: AppConfig, path: Path | str) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = json.loads(cfg.model_dump_json())
    p.write_text(yaml.safe_dump(payload, sort_keys=False, allow_unicode=True), encoding="utf-8")


def dump_json(cfg: AppConfig, path: Path | str) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(cfg.model_dump_json(indent=2), encoding="utf-8")


BOOL_ENV_KEYS = {
    "enabled",
    "discovery",
    "gui",
    "snapshot",
    "multicast_stream",
    "embedded",
    "loop",
    "transcode",
    "webrtc",
    "hls",
    "api",
    "use_ntp",
    "daylight_savings",
    "rtsp_enabled",
    "http_enabled",
    "motion_module",
    "line_cross_module",
    "field_detector_module",
    "show_text_status",
    "show_arrows",
    "show_zoom",
    "show_iris",
    "show_focus",
    "show_events",
    "embed_rtsp_credentials",
}


def apply_env(data: dict[str, Any]) -> dict[str, Any]:
    """Overlay ONVIF_EMU_* environment variables onto a nested dict.

    Examples:
      ONVIF_EMU_ONVIF_PORT=8081
      ONVIF_EMU_AUTH__MODE=none
      ONVIF_EMU_DEVICE__MODEL=TEST
    """
    for key, raw in os.environ.items():
        if not key.startswith(ENV_PREFIX):
            continue
        path = key[len(ENV_PREFIX) :].lower()
        parts = [p for p in path.split("__") if p]
        if not parts:
            continue
        cursor: Any = data
        for part in parts[:-1]:
            if part not in cursor or not isinstance(cursor[part], dict):
                cursor[part] = {}
            cursor = cursor[part]
        leaf = parts[-1]
        cursor[leaf] = _coerce_scalar(raw, key=leaf)
    return data


def _coerce_scalar(raw: str, *, key: str = "") -> Any:
    """Coerce env values. Only treat 1/0 as bool for known boolean keys (not usernames)."""
    low = raw.strip().lower()
    boolish = key in BOOL_ENV_KEYS or key.endswith("_enabled")
    if boolish:
        if low in {"true", "yes", "1", "on"}:
            return True
        if low in {"false", "no", "0", "off"}:
            return False
    elif low in {"true", "yes"}:
        return True
    elif low in {"false", "no"}:
        return False
    try:
        if "." in raw:
            return float(raw)
        return int(raw)
    except ValueError:
        return raw


def parse_channel_spec(spec: str) -> dict[str, Any]:
    """Parse `--channel N:source=<uri>,encoder=h264,...` into a dict."""
    if ":" not in spec:
        raise ValueError("Channel spec must look like N:key=value,...")
    idx, rest = spec.split(":", 1)
    channel_id = int(idx)
    fields: dict[str, str] = {}
    if rest:
        for chunk in rest.split(","):
            if not chunk:
                continue
            if "=" not in chunk:
                raise ValueError(f"Invalid channel field: {chunk}")
            k, v = chunk.split("=", 1)
            fields[k.strip()] = v.strip()
    out: dict[str, Any] = {"id": channel_id}
    if "name" in fields:
        out["name"] = fields["name"]
    if "path" in fields:
        out["rtsp_path"] = fields["path"]
    if "enabled" in fields:
        out["enabled"] = fields["enabled"].lower() in {"1", "true", "yes"}
    source: dict[str, Any] = {}
    if "source" in fields:
        uri = fields["source"]
        source["uri"] = uri
        source["type"] = infer_source_type(uri)
    if "loop" in fields:
        source["loop"] = fields["loop"].lower() in {"1", "true", "yes"}
    if "end" in fields:  # clip played once: stop | freeze | black
        source["end_action"] = fields["end"].lower()
        source.setdefault("loop", False)
    if "transcode" in fields:
        source["transcode"] = fields["transcode"].lower() in {"1", "true", "yes"}
    if source:
        out["source"] = source
    encoder: dict[str, Any] = {}
    if "encoder" in fields:
        encoder["codec"] = fields["encoder"]
    if "width" in fields:
        encoder["width"] = int(fields["width"])
    if "height" in fields:
        encoder["height"] = int(fields["height"])
    if "fps" in fields:
        encoder["fps"] = int(fields["fps"])
    if "bitrate" in fields:
        encoder["bitrate"] = int(fields["bitrate"])
    if encoder:
        out["encoder"] = encoder
    return out


def infer_source_type(uri: str) -> str:
    u = uri.lower()
    if u.startswith("rtsp://") or u.startswith("rtsps://"):
        return SourceType.rtsp.value
    if u.startswith("http://") or u.startswith("https://"):
        if ".m3u8" in u or "hls" in u:
            return SourceType.hls.value
        if "dash" in u or ".mpd" in u:
            return SourceType.dash.value
        return SourceType.http.value
    if u.startswith("udp://") or u.startswith("rtp://"):
        return SourceType.udp.value
    if u in {"testsrc", "test", ""}:
        return SourceType.testsrc.value
    return SourceType.file.value


def merge_dict(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for k, v in overlay.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = merge_dict(out[k], v)
        else:
            out[k] = v
    return out


def build_config(
    *,
    cli_overlay: dict[str, Any] | None = None,
    config_path: Path | str | None = None,
    use_env: bool = True,
) -> AppConfig:
    """Priority: CLI overlay > file > packaged default > env overlay applied after file.

    Actual order applied here:
      1. packaged / file config
      2. environment ONVIF_EMU_*
      3. CLI overlay (highest)
    """
    data: dict[str, Any] = {}
    apply_dotenv()
    path = Path(config_path) if config_path else default_config_path()
    if path.is_file():
        data = load_yaml(path)
    if use_env:
        data = apply_env(data)
    if cli_overlay:
        data = merge_dict(data, cli_overlay)
    return AppConfig.model_validate(data)


def startup_config(
    *,
    explicit: Path | str | None,
    no_conf: bool = False,
    cli_overlay: dict[str, Any] | None = None,
    use_env: bool = True,
    create_missing: bool = True,
) -> tuple[AppConfig, Path | None, bool]:
    """Load config: -c file, else auto onvif-emulator.conf beside the program.

    --no-conf skips the auto file (packaged defaults only).
    First run without --no-conf writes a commented .conf next to the program.
    """
    overlay = cli_overlay or {}
    if explicit:
        path = Path(explicit)
        return build_config(cli_overlay=overlay, config_path=path, use_env=use_env), path, False
    if no_conf:
        return build_config(cli_overlay=overlay, config_path=None, use_env=use_env), None, False
    beside = beside_conf_path()
    # standard file name: next to the program first, then the directory the program was launched from
    for cand in (beside, Path.cwd() / beside.name):
        if cand.is_file():
            return build_config(cli_overlay=overlay, config_path=cand, use_env=use_env), cand, False
    cfg = build_config(cli_overlay=overlay, config_path=None, use_env=use_env)
    if create_missing:
        write_conf(cfg, beside, cfg.language)
        return cfg, beside, True
    return cfg, None, False


def channel_from_spec(spec: str) -> ChannelConfig:
    return ChannelConfig.model_validate(parse_channel_spec(spec))


def encoder_from_dict(d: dict[str, Any]) -> EncoderConfig:
    return EncoderConfig.model_validate(d)


def source_from_dict(d: dict[str, Any]) -> SourceConfig:
    return SourceConfig.model_validate(d)
