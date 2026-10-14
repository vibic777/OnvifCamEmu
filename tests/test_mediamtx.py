import asyncio
from pathlib import Path

import pytest

from onvif_emu.pipeline.mediamtx import MediaMtx, write_mtx_config


def test_write_mtx_config(tmp_path: Path):
    p = write_mtx_config(tmp_path / "mediamtx.yml", 18554, 19997, 18888, 18889)
    text = p.read_text(encoding="utf-8")
    assert ":18554" in text
    assert "127.0.0.1:19997" in text


@pytest.mark.asyncio
async def test_mediamtx_missing_binary_does_not_raise():
    mtx = MediaMtx(None, Path("mediamtx.yml"), enabled=True)
    assert await mtx.start() is False


@pytest.mark.asyncio
async def test_mediamtx_spawn_oserror_is_warning(tmp_path, monkeypatch):
    async def boom(*_a, **_k):
        raise OSError(2, "The system cannot find the file specified")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", boom)
    mtx = MediaMtx(str(tmp_path / "no-such-mediamtx.exe"), tmp_path / "m.yml", enabled=True)
    assert await mtx.start() is False


def test_mediamtx_alive_false_when_not_started():
    mtx = MediaMtx(None, Path("mediamtx.yml"), enabled=False)
    assert mtx.alive is False
