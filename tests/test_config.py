from __future__ import annotations

from pathlib import Path

import pytest

from onvif_emu.config import apply_env, build_config, infer_source_type, parse_channel_spec
from onvif_emu.models import AppConfig, AuthMode


def test_default_config_valid():
    cfg = AppConfig()
    assert cfg.channels
    assert cfg.users == []  # accounts live in data/users.json, never in the config
    assert cfg.auth.mode == AuthMode.digest
    assert cfg.host == "0.0.0.0"
    assert AppConfig.model_validate({"host": ""}).host == "0.0.0.0"
    assert AppConfig.model_validate({"host": "*"}).host == "0.0.0.0"
    assert 1 <= cfg.channels[0].id <= 64


def test_channel_spec_rtsp_and_file():
    d = parse_channel_spec("2:source=rtsp://cam/s,encoder=h265,width=1920,fps=30,name=Front")
    assert d["id"] == 2
    assert d["source"]["type"] == "rtsp"
    assert d["encoder"]["codec"] == "h265"
    assert d["name"] == "Front"
    assert infer_source_type("clip.mp4") == "file"
    assert infer_source_type("https://x/a.m3u8") == "hls"
    assert infer_source_type("udp://@239.0.0.1:1234") == "udp"


def test_bad_channel_spec():
    with pytest.raises(ValueError):
        parse_channel_spec("not-a-spec")
    with pytest.raises(ValueError):
        parse_channel_spec("1:oops")


def test_env_overlay(monkeypatch):
    monkeypatch.setenv("ONVIF_EMU_ONVIF_PORT", "9090")
    monkeypatch.setenv("ONVIF_EMU_AUTH__MODE", "none")
    monkeypatch.setenv("ONVIF_EMU_DEVICE__MODEL", "X")
    data = apply_env({})
    assert data["onvif_port"] == 9090
    assert data["auth"]["mode"] == "none"
    assert data["device"]["model"] == "X"


def test_env_username_one_not_bool(monkeypatch):
    monkeypatch.setenv("ONVIF_EMU_AUTH__USERNAME", "1")
    monkeypatch.setenv("ONVIF_EMU_DISCOVERY", "1")
    data = apply_env({"auth": {}})
    # "1" must not become True for username (may become int 1 before pydantic)
    assert data["auth"]["username"] is not True
    assert data["auth"]["username"] in (1, "1")
    assert data["discovery"] is True


def test_cli_overlay_wins_over_file(tmp_path: Path):
    p = tmp_path / "c.yaml"
    p.write_text("onvif_port: 7000\nauth:\n  mode: basic\n", encoding="utf-8")
    cfg = build_config(cli_overlay={"onvif_port": 8000}, config_path=p, use_env=False)
    assert cfg.onvif_port == 8000
    assert cfg.auth.mode == AuthMode.basic


def test_invalid_port_rejected():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        AppConfig.model_validate({"onvif_port": 99999})
