"""Every setting is reachable from the command line; windowed-exe helpers; live port moves."""

from __future__ import annotations

import json
import socket

import pytest

from onvif_emu import winui
from onvif_emu.app import AppFacade
from onvif_emu.cli import (
    SETTING_FLAGS,
    _settable_paths,
    build_parser,
    dump_config_text,
    load_from_namespace,
    main,
    overlay_from_args,
    parse_set_items,
)
from onvif_emu.config_apply import apply_config
from onvif_emu.exit_codes import OK, USAGE_ERROR
from onvif_emu.models import AppConfig


def _cfg(*argv: str) -> AppConfig:
    ns = build_parser().parse_args(["--no-conf", *argv])
    return load_from_namespace(ns)


def test_dedicated_flags_reach_config():
    cfg = _cfg(
        "--auto-port",
        "--ptz-pan-speed", "45",
        "--no-mediamtx",
        "--mediamtx-api-port", "19997",
        "--ffmpeg-arg=-threads", "--ffmpeg-arg=2",
        "--ntp-server", "a.example", "--ntp-server", "b.example",
        "--osd-position", "center",
        "--log-max-mb", "1.5", "--log-backups", "7", "--no-log-console",
        "--device-timezone", "Europe/Moscow",
    )
    assert cfg.auto_port is True
    assert cfg.ptz.pan_speed == 45
    assert cfg.mediamtx.embedded is False
    assert cfg.mediamtx.api_port == 19997
    assert cfg.ffmpeg.extra_args == ["-threads", "2"]
    assert cfg.ntp.servers == ["a.example", "b.example"]
    assert cfg.osd.style.position == "center"
    assert (cfg.logging.max_mb, cfg.logging.backups, cfg.logging.console) == (1.5, 7, False)
    assert cfg.device.timezone == "Europe/Moscow"


def test_every_settings_flag_has_a_valid_config_path():
    paths = _settable_paths()
    for row in SETTING_FLAGS:
        assert row[1] in paths, row


def test_set_overrides_flags_and_coerces_yaml():
    cfg = _cfg("--ptz-pan-speed", "10", "--set", "ptz.pan_speed=60", "--set", "ntp.servers=[x, y]", "--set", "gui=false")
    assert cfg.ptz.pan_speed == 60
    assert cfg.ntp.servers == ["x", "y"]
    assert cfg.gui is False


def test_set_rejects_unknown_key_with_hint():
    with pytest.raises(ValueError, match="did you mean"):
        parse_set_items(["ptz.pan_spede=1"])
    with pytest.raises(ValueError, match="PATH=VALUE"):
        parse_set_items(["oops"])


def test_service_gui_flags_defaults():
    ns = build_parser().parse_args([])
    assert ns.open_browser is None
    assert build_parser().parse_args(["--no-open-browser"]).open_browser is False
    assert overlay_from_args(build_parser().parse_args([])).get("auto_port") is None


def test_usage_errors_exit_5(capsys):
    assert main(["--definitely-not-a-flag"]) == USAGE_ERROR
    assert main(["--set", "nope=1", "--check-config", "--no-conf"]) == USAGE_ERROR
    assert "See --help" in capsys.readouterr().err


def test_print_config_and_list_settings(capsys):
    assert main(["--no-conf", "--print-config", "json", "--onvif-port", "9123", "--set", "ptz.flip=true"]) == OK
    data = json.loads(capsys.readouterr().out)
    assert data["onvif_port"] == 9123 and data["ptz"]["flip"] is True
    assert main(["--list-settings"]) == OK
    out = capsys.readouterr().out
    assert "logging.max_mb" in out and "mediamtx.api_port" in out


def test_dump_config_yaml_roundtrip():
    text = dump_config_text(_cfg("--onvif-port", "9001"), "yaml")
    assert "onvif_port: 9001" in text


# -- windowed exe helpers ---------------------------------------------------


def test_gui_defaults_added_only_when_not_overridden():
    assert winui.apply_gui_defaults([]) == ["--service", "--open-browser", "--tray", "--auto-port"]
    assert winui.apply_gui_defaults(["--onvif-port", "9000"])[:4] == ["--service", "--open-browser", "--tray", "--auto-port"]
    assert "--open-browser" not in winui.apply_gui_defaults(["--no-open-browser"])
    assert "--auto-port" not in winui.apply_gui_defaults(["--no-auto-port"])
    assert "--tray" not in winui.apply_gui_defaults(["--no-tray"])
    headless = winui.apply_gui_defaults(["--headless"])
    assert "--service" not in headless and "--open-browser" not in headless and "--tray" not in headless
    assert winui.apply_gui_defaults(["--help"]) == ["--help"]
    assert winui.apply_gui_defaults(["doctor"]) == ["doctor"]


def test_info_invocation_detection():
    for argv in (["-h"], ["--version"], ["--print-config"], ["--print-config=json"], ["doctor"], ["--list-settings"]):
        assert winui.is_info_invocation(argv), argv
    assert not winui.is_info_invocation(["--service", "--onvif-port", "80"])


def test_capture_hides_log_records():
    cap = winui.Capture()
    cap.write("2026-01-01 10:00:00 | CRITICAL | onvif_emu | boom\nLog file: x.log\nreal output\n")
    assert cap.visible_text() == "real output"


# -- port handling ------------------------------------------------------------


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.mark.asyncio
async def test_auto_port_skips_busy_port(runtime, monkeypatch):
    monkeypatch.setattr("onvif_emu.app.AUTO_PORT_TRIES", 20)
    blocker = socket.socket()
    blocker.bind(("127.0.0.1", 0))
    blocker.listen(1)
    busy = blocker.getsockname()[1]
    try:
        for auto, expect_ok in ((False, False), (True, True)):
            cfg = runtime.config
            cfg.host, cfg.onvif_port, cfg.auto_port = "127.0.0.1", busy, auto
            facade = AppFacade(cfg)
            from aiohttp import web

            facade._runner = web.AppRunner(web.Application())
            await facade._runner.setup()
            try:
                if expect_ok:
                    await facade._bind("127.0.0.1")
                    assert cfg.onvif_port != busy and cfg.network.http_port == cfg.onvif_port
                else:
                    with pytest.raises(OSError):
                        await facade._bind("127.0.0.1")
                    assert cfg.onvif_port == busy
            finally:
                await facade._runner.cleanup()
    finally:
        blocker.close()


@pytest.mark.asyncio
async def test_port_change_from_config_moves_listener(runtime):
    import aiohttp
    from aiohttp import web

    cfg = runtime.config
    cfg.host = "127.0.0.1"
    cfg.onvif_port = _free_port()
    facade = AppFacade(cfg)
    facade._runner = web.AppRunner(web.Application())
    await facade._runner.setup()
    try:
        await facade._bind("127.0.0.1")
        old_port = cfg.onvif_port
        new_cfg = AppConfig.model_validate({**json.loads(cfg.model_dump_json()), "onvif_port": _free_port()})
        result = await apply_config(facade.runtime, new_cfg)
        assert result.port_changed and not result.port_error
        async with aiohttp.ClientSession() as s:
            async with s.get(f"http://127.0.0.1:{new_cfg.onvif_port}/") as r:
                assert r.status in (200, 404)
            with pytest.raises(aiohttp.ClientConnectorError):
                async with s.get(f"http://127.0.0.1:{old_port}/"):
                    pass

        # moving onto a taken port keeps the working one and reports why
        blocker = socket.socket()
        blocker.bind(("127.0.0.1", 0))
        blocker.listen(1)
        try:
            working = new_cfg.onvif_port
            bad_cfg = AppConfig.model_validate(
                {**json.loads(new_cfg.model_dump_json()), "onvif_port": blocker.getsockname()[1]}
            )
            result = await apply_config(facade.runtime, bad_cfg)
            assert result.port_error and not result.port_changed
            assert bad_cfg.onvif_port == working
        finally:
            blocker.close()
    finally:
        await facade._runner.cleanup()
