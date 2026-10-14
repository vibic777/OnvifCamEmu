"""Fleet (N devices, port ranges, overrides), config round-trip, constructor, limits, overlay, multi-channel ONVIF."""

from __future__ import annotations

import time

import pytest
import yaml

from onvif_emu import constructor, limits
from onvif_emu.confdump import render_conf
from onvif_emu.fleet import Fleet, build_fleet, derive_device, device_count, dict_diff, parse_ports
from onvif_emu.models import AppConfig, AuthMode, EventRule
from onvif_emu.onvif_dispatch import dispatch
from onvif_emu.overlay import Canvas, OverlayRenderer, canvas_size
from onvif_emu.soap import parse_envelope


def _cfg(**over) -> AppConfig:
    return AppConfig.model_validate(over)


# ---------------------------------------------------------------- ports / fleet
def test_parse_ports():
    assert parse_ports("8080-8089", 1, 10) == list(range(8080, 8090))
    assert parse_ports("", 8080, 3) == [8080, 8081, 8082]
    assert parse_ports("554,600", 1, 4) == [554, 600, 601, 602]
    assert parse_ports("10-8", 1, 3) == [8, 9, 10]  # reversed range
    with pytest.raises(ValueError):
        parse_ports("70000", 1, 1)


def test_ten_devices_on_port_ranges():
    root = _cfg(fleet={"onvif_ports": "8080-8089", "rtsp_ports": "554-563"})
    assert device_count(root) == 10
    devs = build_fleet(root)
    assert [d.onvif_port for d in devs] == list(range(8080, 8090))
    assert [d.network.rtsp_port for d in devs] == list(range(554, 564))
    assert len({d.device.serial for d in devs}) == 10 and len({d.state_path for d in devs}) == 10
    assert len({d.mediamtx.api_port for d in devs}) == 10 and len({d.instance for d in devs}) == 10
    assert all(d.fleet.count == 1 for d in devs)  # children never fleet again


def test_rules_are_scoped_by_device_and_overrides_apply():
    root = _cfg(
        fleet={"count": 3, "devices": [{"device": {"model": "X2"}, "channels_patch": {"1": {"ptz_enabled": False}}}]},
        diag={"rules": [
            {"kind": "tamper", "device": 0, "name": "all"},
            {"kind": "analytics", "device": 2, "name": "only2"},
        ]},
    )
    d1, d2, d3 = build_fleet(root)
    assert [r.name for r in d1.diag.rules] == ["all"]
    assert [r.name for r in d2.diag.rules] == ["all", "only2"]
    assert [r.name for r in d3.diag.rules] == ["all"]
    assert d2.device.model == "X2" and d1.device.model != "X2"
    assert d2.channels[0].ptz_enabled is False and d1.channels[0].ptz_enabled is True


def test_edits_in_a_device_gui_flow_back_into_the_root(tmp_path):
    root = _cfg(fleet={"count": 3}, diag={"rules": [{"kind": "tamper", "device": 0, "name": "all"}]})
    fleet = Fleet(root, tmp_path / "onvif-emulator.conf")
    d2 = derive_device(root, 2)
    d2.device.model = "CHANGED"
    d2.diag.rules.append(EventRule(kind="contact", name="mine", device=0))  # created in device 2's GUI
    fleet.absorb(2, d2)
    fleet.save()
    reloaded = AppConfig.model_validate(yaml.safe_load((tmp_path / "onvif-emulator.conf").read_text(encoding="utf-8")))
    assert reloaded.fleet.devices[0]["device"]["model"] == "CHANGED"
    assert derive_device(reloaded, 2).device.model == "CHANGED"
    assert derive_device(reloaded, 3).device.model != "CHANGED"
    names = {r.name: r.device for r in reloaded.diag.rules}
    assert names == {"all": 0, "mine": 2}  # a rule made in device 2 belongs to device 2 only
    # device 1 edits the shared template
    d1 = derive_device(reloaded, 1)
    d1.auth.mode = AuthMode.none
    fleet.root = reloaded
    fleet.absorb(1, d1)
    assert fleet.root.auth.mode.value == "none" and fleet.root.fleet.count == 3


def test_dict_diff():
    assert dict_diff({"a": 1, "b": {"c": 2}}, {"a": 1, "b": {"c": 3}}) == {"b": {"c": 3}}
    assert dict_diff({"a": 1}, {"a": 1}) is None


# ---------------------------------------------------------------- config file round-trip
def test_conf_roundtrip_keeps_everything():
    cfg = _cfg(
        channels=[{"id": 1, "name": "A", "streams": 3, "rtsp_port": 9554, "ptz_enabled": False},
                  {"id": 2, "name": "B", "ptz": {"pan_speed": 50}}],
        diag={"relays": 3, "inputs": 4, "webhooks": ["http://x/h"], "rules": [
            {"kind": "tamper", "schedule": "random", "range_min": 1, "range_max": 100, "unit": "minutes"},
            {"kind": "custom", "topic": "tns1:A/B", "data": {"k": "rand:1-9"}, "device": 2}]},
        fleet={"count": 3, "onvif_ports": "8080-8089", "rtsp_ports": "554-563", "devices": [{"device": {"model": "X"}}]},
        limits={"mode": "strict", "max_streams": 7},
        osd={"style": {"boxes": "all", "show_io": False}},
    )
    for lang in ("ru", "en"):
        back = AppConfig.model_validate(yaml.safe_load(render_conf(cfg, lang)))
        assert back.model_dump() == cfg.model_dump()


# ---------------------------------------------------------------- constructor
def test_streams_ladder_from_main_encoder():
    cfg = _cfg(channels=[{"id": 1, "streams": 3, "encoder": {"width": 1920, "height": 1080, "fps": 30, "bitrate": 6000}}])
    ch = cfg.channels[0]
    assert (ch.sub.enabled, ch.third.enabled) == (True, True)
    assert (ch.sub.width, ch.sub.height) == (960, 540) and (ch.third.width, ch.third.height) == (480, 270)
    assert ch.sub.bitrate == 1500 and ch.third.bitrate == 500 and ch.sub.fps == 18
    assert _cfg(channels=[{"id": 1, "streams": 1}]).channels[0].sub.enabled is False
    assert _cfg(channels=[{"id": 1, "streams": 2}]).channels[0].third.enabled is False


def test_console_constructor_helpers():
    cfg = _cfg()
    constructor.make_channels(cfg, 3)
    constructor.apply_source(cfg, "rtsp://cam/x")
    constructor.apply_streams(cfg, 3)
    assert [c.id for c in cfg.channels] == [1, 2, 3]
    assert all(c.source.uri == "rtsp://cam/x" and c.source.type.value == "rtsp" and c.third.enabled for c in cfg.channels)
    constructor.apply_channel_patches(cfg, {2: {"ptz_enabled": False}})
    assert [c.ptz_enabled for c in cfg.channels] == [True, False, True]
    over = {"channels.2.ptz_enabled": False, "x": 1}
    assert constructor.split_channel_paths(over) == {2: {"ptz_enabled": False}} and over == {"x": 1}


def test_cli_builds_the_three_by_three_scenario():
    from onvif_emu.cli import build_parser, load_from_namespace

    ns = build_parser().parse_args([
        "--no-conf", "--devices", "3", "--onvif-port", "8080-8082", "--rtsp-port", "554-556", "--streams", "3",
        "--rule", "kind=tamper,every=30s", "--rule", "kind=analytics,device=2,every=1m",
        "--device-set", "3:channels.1.ptz_enabled=false", "--relays", "2",
    ])
    from onvif_emu.cli import _post_process, overlay_from_args

    overlay = overlay_from_args(ns)
    patches = overlay.pop("_ch_patches", {})
    cfg = load_from_namespace(ns)
    _post_process(cfg, ns, patches)
    d1, d2, d3 = build_fleet(cfg)
    assert [d.onvif_port for d in (d1, d2, d3)] == [8080, 8081, 8082]
    assert all(len([c for c in d.channels if c.third.enabled]) == 1 for d in (d1, d2, d3))
    assert [len(d.diag.rules) for d in (d1, d2, d3)] == [1, 2, 1]
    assert (d1.channels[0].ptz_enabled, d3.channels[0].ptz_enabled) == (True, False)


def test_port_range_flag_validation(capsys):
    from onvif_emu.cli import main

    assert main(["--no-conf", "--check-config", "--onvif-port", "9-x"]) == 5
    assert main(["--no-conf", "--check-config", "--rule", "kind=nope"]) in (1, 5)


# ---------------------------------------------------------------- limits
def test_estimate_and_clamp_reduce_the_load():
    root = _cfg(fleet={"count": 40}, channels=[{"id": 1, "streams": 3, "encoder": {"width": 1920, "height": 1080, "fps": 30}}],
                limits={"max_devices": 8, "max_streams": 12})
    plan, notes = limits.enforce(root)
    fleet = build_fleet(plan)
    est = limits.estimate(fleet)
    assert est.devices <= 8 and est.streams <= 12 and est.mpx_per_s <= est.budget_mpx
    assert notes and any("devices" in n or "stream" in n for n in notes)
    assert root.fleet.count == 40  # the caller's config is not modified


def test_strict_refuses_and_warn_keeps_and_off_ignores():
    big = {"fleet": {"count": 50}, "limits": {"max_devices": 4}}
    with pytest.raises(limits.LimitError):
        limits.enforce(_cfg(**{**big, "limits": {"max_devices": 4, "mode": "strict"}}))
    plan, notes = limits.enforce(_cfg(**{**big, "limits": {"max_devices": 4, "mode": "warn"}}))
    assert device_count(plan) == 50 and notes
    plan, notes = limits.enforce(_cfg(**{**big, "limits": {"max_devices": 4, "mode": "off"}}))
    assert device_count(plan) == 50 and not notes


def test_timers_and_rule_count_are_clamped():
    root = _cfg(diag={"rules": [{"kind": "motion", "schedule": "interval", "interval": 0.05, "unit": "seconds"}] * 3},
                limits={"min_interval_s": 1.0, "max_rules": 2})
    plan, notes = limits.enforce(root)
    assert len(plan.diag.rules) == 2 and all(r.interval >= 1.0 for r in plan.diag.rules)
    assert any("minimum" in n for n in notes) and any("max_rules" in n for n in notes)


def test_rule_rate_estimate():
    cfg = _cfg(diag={"rules": [{"kind": "motion", "schedule": "interval", "interval": 1, "unit": "seconds"},
                               {"kind": "motion", "schedule": "manual"}]})
    assert 1.9 < limits.rule_rate(cfg) < 2.1  # one pulse per second = 2 events/s; manual rules cost nothing


def test_governor_sheds_and_restores(runtime, monkeypatch):
    gov = limits.Governor([runtime])
    gov._set(1, "test")
    assert runtime.shed_level == 1
    gov._set(0, "calm")
    assert runtime.shed_level == 0


# ---------------------------------------------------------------- overlay
def test_font_covers_the_text_we_draw():
    from onvif_emu.overlay import FONT

    for ch in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 .:-+/_%#(),!?*[]<>|'=":
        assert ch in FONT and len(FONT[ch]) == 7, ch
    for ch in "АБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯ":
        assert ch in FONT and len(FONT[ch]) == 7, ch


def test_canvas_primitives():
    cv = Canvas(20, 10)
    cv.rect(2, 2, 4, 3, (255, 0, 0, 255))
    assert cv.buf[(2 * 20 + 2) * 4 : (2 * 20 + 2) * 4 + 4] == bytes((255, 0, 0, 255))
    assert cv.buf[0:4] == bytes(4)  # untouched pixels stay transparent
    cv.rect(-5, -5, 100, 100, (1, 2, 3, 4))  # clipped, no exception
    assert len(cv.tobytes()) == 20 * 10 * 4
    cv.text(0, 0, "Az9", 1, (255, 255, 255, 255))
    cv.triangle([(1, 1), (8, 1), (4, 8)], (9, 9, 9, 255))
    cv.circle(10, 5, 3, (7, 7, 7, 255))


def _overlay(runtime):
    w, h = canvas_size(1280, 720)
    return OverlayRenderer(runtime, 1, (w, h)), w, h


def _lit_pixels(data: bytes) -> int:
    return sum(1 for i in range(3, len(data), 4) if data[i])


def test_overlay_is_empty_until_something_happens_and_reacts_to_ptz(runtime):
    runtime.diag.sync_config()
    r, w, h = _overlay(runtime)
    idle = r.frame()
    idle_px = _lit_pixels(idle)
    servo = runtime.ensure_servo(1)
    servo.continuous_move(0.5, 0.0, 0.0, now=time.monotonic())  # pan right
    moving = r.frame()
    assert "right" in r._lit and _lit_pixels(moving) > idle_px + 500  # the HUD appears
    assert r.frame() == moving  # unchanged state is not redrawn
    servo.stop(now=time.monotonic())
    r.frame()
    assert "stop" in r._lit


def test_overlay_draws_badges_boxes_and_io(runtime):
    runtime.diag.sync_config()
    r, w, h = _overlay(runtime)
    base = _lit_pixels(r.frame())
    runtime.diag._badge(1, "TAMPER", 5)
    with_badge = _lit_pixels(r.frame())
    assert with_badge > base
    runtime.analytics_for(1).objects.clear()
    runtime.analytics_for(1).inject(count=2, ttl_s=30)
    assert _lit_pixels(r.frame()) > with_badge
    runtime.diag.set_relay("relay_1", True)
    r.frame()
    assert r._sig is not None
    runtime.config.osd.enabled = False
    assert _lit_pixels(r.frame()) == 0  # OSD off -> fully transparent


def test_overlay_event_position_and_cyrillic_text(runtime):
    from onvif_emu.overlay import Canvas

    text_canvas = Canvas(640, 360)
    text_canvas.text(0, 0, "ТРЕВОГА РЕЛЕ 1 ВКЛ", 1, (255, 255, 255, 255))
    assert any(text_canvas.buf)
    r, _, _ = _overlay(runtime)
    cv = Canvas(640, 360)
    r._badges(cv, [("ТРЕВОГА", "event")], 1.0, 12, "bottom-center")
    ys = [i // (cv.w * 4) for i in range(3, len(cv.buf), 4) if cv.buf[i]]
    xs = [(i % (cv.w * 4)) // 4 for i in range(3, len(cv.buf), 4) if cv.buf[i]]
    assert min(ys) > 300
    assert min(xs) > 250 and max(xs) < 390


def test_overlay_respects_channel_without_ptz(runtime):
    runtime.config.channels[0].ptz_enabled = False
    r, w, h = _overlay(runtime)
    runtime.ensure_servo(1).continuous_move(0.5, 0.5, 0.5, now=time.monotonic())
    r.frame()
    assert not r._lit  # no PTZ on this channel => no HUD


def test_ffmpeg_command_with_overlay_input():
    from onvif_emu.models import ChannelConfig
    from onvif_emu.pipeline.ffmpeg import build_command

    ch = ChannelConfig(id=1, name="A", streams=3)
    plain = build_command("ffmpeg", ch, 8554, "", 0)
    assert "-nostdin" in plain and "-filter_complex" not in plain
    cmd = build_command("ffmpeg", ch, 8554, "eq=brightness=0.1", 2, overlay=(640, 360))
    assert "-nostdin" not in cmd and "pipe:0" in cmd and "rawvideo" in cmd
    graph = cmd[cmd.index("-filter_complex") + 1]
    assert graph.startswith("[0:v]eq=brightness=0.1,scale=480:270[base]")  # tier 2 = low, 480x270
    assert "overlay=0:0" in graph and cmd[-1].endswith("/ch1_third")
    assert "-re" in cmd  # testsrc runs in real time


# ---------------------------------------------------------------- multi-channel ONVIF
def _soap(action: str, inner: str = "", ns: str = "http://www.onvif.org/ver10/media/wsdl"):
    return parse_envelope(
        f'<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"><s:Body><{action} xmlns="{ns}">{inner}</{action}></s:Body></s:Envelope>'
    )


def test_three_profiles_and_ptz_only_where_enabled(runtime):
    from onvif_emu.models import ChannelConfig

    runtime.config.channels = [
        ChannelConfig(id=1, name="Cam1", streams=3, rtsp_port=9554),
        ChannelConfig(id=2, name="Cam2", streams=2, ptz_enabled=False),
    ]
    out = dispatch(runtime, _soap("GetProfiles"), "/onvif/media_service")
    for tok in ("profile_1", "profile_1_sub", "profile_1_third", "profile_2", "profile_2_sub"):
        assert f'token="{tok}"' in out, tok
    assert "profile_2_third" not in out
    assert out.count("<tt:PTZConfiguration") == 3  # channel 1's three profiles only
    ptz_ns = "http://www.onvif.org/ver20/ptz/wsdl"
    nodes = dispatch(runtime, _soap("GetNodes", ns=ptz_ns), "/onvif/ptz_service")
    assert "ptz_node_1" in nodes and "ptz_node_2" not in nodes
    fault = dispatch(runtime, _soap("GetStatus", "<ProfileToken>profile_2</ProfileToken>", ns=ptz_ns), "/onvif/ptz_service")
    assert "NoPTZProfile" in fault
    ok = dispatch(runtime, _soap("GetStatus", "<ProfileToken>profile_1_third</ProfileToken>", ns=ptz_ns), "/onvif/ptz_service")
    assert "PTZStatus" in ok


def test_stream_uri_uses_channel_port_and_tier(runtime):
    from onvif_emu.models import ChannelConfig

    runtime.config.channels = [ChannelConfig(id=1, name="Cam1", streams=3, rtsp_port=9554)]
    runtime._public_host = "10.0.0.5"
    body = "<StreamSetup><Stream>RTP-Unicast</Stream><Transport><Protocol>RTSP</Protocol></Transport></StreamSetup><ProfileToken>profile_1_third</ProfileToken>"
    out = dispatch(runtime, _soap("GetStreamUri", body), "/onvif/media_service")
    assert "rtsp://10.0.0.5:9554/ch1_third" in out


def test_request_host_is_advertised_when_public_host_unset(runtime):
    from onvif_emu.runtime import REQUEST_HOST

    runtime._public_host = ""
    tok = REQUEST_HOST.set("192.168.7.7")
    try:
        assert runtime.public_host() == "192.168.7.7"
        assert "192.168.7.7" in runtime.base_url()
    finally:
        REQUEST_HOST.reset(tok)
    runtime.config.public_host = "1.2.3.4"
    runtime._public_host = "1.2.3.4"
    tok = REQUEST_HOST.set("192.168.7.7")
    try:
        assert runtime.public_host() == "1.2.3.4"  # an explicit public_host always wins
    finally:
        REQUEST_HOST.reset(tok)


def test_media_options_and_profile_ops_exist(runtime):
    for action, needle in (
        ("GetServiceCapabilities", "ProfileCapabilities"),
        ("GetVideoEncoderConfigurationOptions", "H264ProfilesSupported"),
        ("GetMetadataConfigurations", "meta_1"),
        ("GetVideoAnalyticsConfigurations", "vac_1"),
        ("GetAudioEncoderConfigurations", "GetAudioEncoderConfigurationsResponse"),
    ):
        assert needle in dispatch(runtime, _soap(action), "/onvif/media_service"), action
    assert "Fault" in dispatch(runtime, _soap("CreateProfile", "<Name>x</Name>"), "/onvif/media_service")


def test_media_get_profile_matches_schema_names(runtime):
    out = dispatch(runtime, _soap("GetProfile", "<ProfileToken>profile_1</ProfileToken>"), "/onvif/media_service")
    assert "<trt:Profile " in out and "<trt:Profiles" not in out
    out = dispatch(runtime, _soap("GetVideoSourceConfiguration", "<ConfigurationToken>vsc_1</ConfigurationToken>"), "/onvif/media_service")
    assert "<trt:Configuration " in out


@pytest.mark.asyncio
async def test_soap_fault_is_http_500(runtime):
    from aiohttp.test_utils import TestClient, TestServer

    from onvif_emu.http_app import create_app
    from onvif_emu.models import AuthMode

    runtime.config.auth.mode = AuthMode.none
    xml = ('<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"><s:Body>'
           '<NoSuchThing xmlns="http://www.onvif.org/ver10/media/wsdl"/></s:Body></s:Envelope>')
    async with TestServer(create_app(runtime)) as s, TestClient(s) as c:
        r = await c.post("/onvif/media_service", data=xml, headers={"Content-Type": "application/soap+xml"})
        assert r.status == 500 and "ActionNotSupported" in await r.text()
        ok = await c.post("/onvif/media_service", data=xml.replace("NoSuchThing", "GetProfiles"),
                          headers={"Content-Type": "application/soap+xml"})
        assert ok.status == 200
