import base64
import json

import pytest
from aiohttp import FormData
from aiohttp.test_utils import TestClient, TestServer

from onvif_emu.constructor import apply_impair, parse_impair_spec
from onvif_emu.http_app import create_app
from onvif_emu.models import AppConfig, ImpairBurst, OsdConfig
from onvif_emu.pipeline.effects import burst_active, gate, impair_encoder_args, impair_filters, stamp_filters
from onvif_emu.pipeline.ffmpeg import build_command


def _basic() -> dict[str, str]:
    return {"Authorization": "Basic " + base64.b64encode(b"admin:admin").decode()}


def test_bursts_at_the_end_of_each_period():
    b = ImpairBurst(period_s=10, duration_s=3)
    assert gate(b) == "gte(mod(t,10),7)"
    assert not burst_active(b, 0.5) and burst_active(b, 8) and not burst_active(b, 10.5)
    assert gate(ImpairBurst(mode="constant")) == "1"


def test_date_keeps_its_format_when_partly_configured():
    osd = OsdConfig.model_validate({"date": {"enabled": True, "position": "top-right"}})
    assert osd.date.format == "%d.%m.%Y"


def test_stamps_stack_and_escape(tmp_path):
    osd = OsdConfig()
    osd.time.enabled = osd.date.enabled = True
    f = stamp_filters(osd, 720, tmp_path)
    assert len(f) == 2 and all(s.startswith("drawtext=") for s in f)
    assert "+" in f[1].split(":y=")[1]  # time drawn below the date in the same corner
    assert (tmp_path / "osd_time.txt").read_text(encoding="utf-8") == "%{localtime:%H\\:%M\\:%S}"
    osd.date.bg_opacity = 0
    assert "box=1" not in stamp_filters(osd, 720, tmp_path)[0]


def test_impair_filters_and_encoder():
    cfg = AppConfig()
    imp = cfg.channels[0].impair
    assert impair_filters(imp) == [] and not imp.active()
    imp.enabled, imp.rain, imp.shake, imp.cover, imp.stutter, imp.freeze = True, 0.5, 0.4, "black", 10, True
    f = ",".join(impair_filters(imp))
    for part in ("select=", "crop=", "gblur", "drawbox", "tmix"):
        assert part in f
    imp.loss, imp.bitrate = 3, "cbr"
    enc = impair_encoder_args(imp, "h264", 2000)
    assert "-minrate" in enc and "nal-hrd=cbr:force-cfr=1" in enc and "-bsf:v" in enc


def test_build_command_graph_order(tmp_path):
    cfg = AppConfig()
    ch = cfg.channels[0]
    logo = tmp_path / "logo.png"
    logo.write_bytes(b"x")
    cfg.osd.time.enabled = True
    cfg.osd.image.enabled, cfg.osd.image.path = True, str(logo)
    ch.impair.enabled, ch.impair.rain = True, 0.3
    cmd = build_command("ffmpeg", ch, 8554, "", 0, overlay=(640, 360), osd=cfg.osd, osd_dir=tmp_path)
    graph = cmd[cmd.index("-filter_complex") + 1]
    assert cmd.index("pipe:0") < cmd.index(str(logo))  # HUD stays input 1, picture is input 2
    assert "[2:v]" in graph and "[1:v]" in graph and "shortest=1" in graph
    assert graph.index("gblur") < graph.index("drawtext")  # OSD on top of the trouble
    plain = build_command("ffmpeg", AppConfig().channels[0], 8554, "", 0)
    assert "-filter_complex" not in plain


def test_impair_spec_parsing():
    cid, d = parse_impair_spec("2:rain=0.6,shake=0.4,shake_every=15s,shake_for=always,loss=3,outage=1-5m,outage_len=5-20s")
    assert cid == 2 and d["rain"] == 0.6 and d["shake_burst"] == {"period_s": 15.0, "mode": "constant"}
    assert (d["outage_every_min"], d["outage_every_max"], d["outage_unit"]) == (1.0, 5.0, "minutes")
    assert (d["outage_len_min"], d["outage_len_max"]) == (5.0, 20.0)
    cfg = AppConfig()
    apply_impair(cfg, "0:noise=0.3")
    assert all(c.impair.enabled and c.impair.noise == 0.3 for c in cfg.channels)
    with pytest.raises(ValueError):
        parse_impair_spec("1:bogus=1")


def test_channel_spec_end_action():
    from onvif_emu.config import parse_channel_spec

    s = parse_channel_spec("1:source=a.mp4,end=freeze")["source"]
    assert s["end_action"] == "freeze" and s["loop"] is False


@pytest.mark.asyncio
async def test_impair_and_osd_image_api(runtime, tmp_path, monkeypatch):
    monkeypatch.setattr("onvif_emu.paths.executable_dir", lambda: tmp_path)
    app = create_app(runtime)
    async with TestServer(app) as server:
        async with TestClient(server) as client:
            r = await client.put("/api/channels/1/impair", json={"enabled": True, "rain": 0.4}, headers=_basic())
            body = await r.json()
            assert r.status == 200 and body["impair"]["rain"] == 0.4
            ch = next(c for c in runtime.config.channels if c.id == 1)
            assert ch.impair.enabled and ch.impair.rain == 0.4
            bad = await client.put("/api/channels/1/impair", json={"rain": 5}, headers=_basic())
            assert bad.status == 400
            form = FormData()
            form.add_field("file", b"\x89PNG fake", filename="Лого, v2.png", content_type="image/png")
            up = await client.post("/api/osd/image", data=form, headers=_basic())
            j = await up.json()
            assert up.status == 200 and j["path"].endswith("Лого_ v2.png"), j
            assert runtime.config.osd.image.enabled
            local = await client.post("/api/osd/image/local", json={"path": j["path"]}, headers=_basic())
            assert local.status == 200
            txt = tmp_path / "notes.txt"
            txt.write_text("x")
            refused = await client.post("/api/osd/image/local", json={"path": str(txt)}, headers=_basic())
            assert refused.status == 400
            listing = await (await client.get("/api/videos/browse", params={"dir": str(tmp_path / "osd"), "kind": "image"},
                                              headers=_basic())).json()
            assert [f["name"] for f in listing["files"]] == ["Лого_ v2.png"]
            assert (await client.post("/api/channels/9/restart", headers=_basic())).status == 404
    assert json.loads(runtime.config.model_dump_json())["osd"]["image"]["path"].endswith(".png")
