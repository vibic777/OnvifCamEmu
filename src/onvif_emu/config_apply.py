"""Apply AppConfig hot-updates with an explicit side-effect report."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from onvif_emu.models import AppConfig
from onvif_emu.videos import resolve_video_dir

if TYPE_CHECKING:
    from onvif_emu.runtime import Runtime

log = logging.getLogger(__name__)


@dataclass(slots=True)
class ConfigApplyResult:
    identity_synced: bool = False
    mediamtx_restarted: bool = False
    pipelines_restarted: bool = False
    logging_reloaded: bool = False
    port_changed: bool = False
    port_error: str = ""


async def apply_config(rt: Runtime, cfg: AppConfig) -> ConfigApplyResult:
    """Replace live config; restart media stack pieces that actually changed."""
    async with rt.config_lock:
        return await _apply_config(rt, cfg)


def _pipeline_signature(cfg: AppConfig) -> dict:
    return cfg.model_dump(include={"channels", "ffmpeg", "osd", "imaging", "limits"})


async def _apply_config(rt: Runtime, cfg: AppConfig) -> ConfigApplyResult:
    result = ConfigApplyResult()
    prev_listen = (rt.config.host, rt.config.onvif_port)
    prev_media = rt.media.signature() if rt.media else ()
    prev_pipelines = _pipeline_signature(rt.config)
    log.info("Applying config channels=%s auth=%s port=%s", len(cfg.channels), cfg.auth.mode.value, cfg.onvif_port)

    rt.config = cfg
    rt.identity.sync_from_config(cfg)
    result.identity_synced = True

    for ch in cfg.channels:
        rt.ensure_servo(ch.id).apply_config(ch.ptz or cfg.ptz)
        rt.analytics_for(ch.id)
    try:
        rt.diag.sync_config()
    except RuntimeError:  # no running loop (sync callers/tests): rules start with the engine
        pass

    from onvif_emu.logging_setup import setup_logging

    setup_logging(
        levels=cfg.logging.levels,
        log_file=cfg.logging.file or None,
        log_dir=cfg.logging.dir or None,
        console=cfg.logging.console,
        max_mb=cfg.logging.max_mb,
        backups=cfg.logging.backups,
    )
    result.logging_reloaded = True
    rt.video_dir = resolve_video_dir(cfg.video_dir)
    # public_host may change
    rt._public_host = cfg.public_host or ""
    rt.save_config()

    if (cfg.host, cfg.onvif_port) != prev_listen and rt.rebind is not None:
        result.port_error = await rt.rebind(cfg.host or "0.0.0.0", cfg.onvif_port)
        if result.port_error:
            # keep advertising the address we are really listening on
            cfg.host, cfg.onvif_port = prev_listen
            cfg.network.http_port = prev_listen[1]
            rt.save_config()
        else:
            result.port_changed = True

    if rt.media and prev_media != rt.media.signature():
        await rt.media.restart_mediamtx()
        result.mediamtx_restarted = True
    if rt.media and rt.media.pipelines and (
        result.mediamtx_restarted or prev_pipelines != _pipeline_signature(cfg)
    ):
        await rt.media.restart_pipelines()
        result.pipelines_restarted = True
    log.info(
        "Config applied identity=%s mtx_restart=%s pipelines_restart=%s levels=%s",
        result.identity_synced,
        result.mediamtx_restarted,
        result.pipelines_restarted,
        ",".join(cfg.logging.levels),
    )
    return result
