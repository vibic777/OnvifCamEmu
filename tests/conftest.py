from pathlib import Path

import pytest

pytest_plugins = ("aiohttp.pytest_plugin",)

from onvif_emu.models import AppConfig
from onvif_emu.runtime import Runtime


@pytest.fixture
def cfg() -> AppConfig:
    return AppConfig()


@pytest.fixture
def runtime(tmp_path: Path, cfg: AppConfig) -> Runtime:
    cfg.state_path = str(tmp_path / "state.json")
    cfg.mediamtx.embedded = False
    cfg.discovery = False
    cfg.gui = False
    rt = Runtime(cfg, config_path=tmp_path / "cfg.yaml")
    rt.state.load(cfg)
    rt.analytics.seed(1)
    for ch in cfg.channels:
        rt.ensure_servo(ch.id)
    return rt
