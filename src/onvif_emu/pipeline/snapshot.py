from __future__ import annotations

import asyncio
import logging
import shutil
from pathlib import Path

from onvif_emu.models import ChannelConfig
from onvif_emu.procguard import bind_child
from onvif_emu.util import subprocess_hidden_kwargs

log = logging.getLogger(__name__)

# 1x1 grey JPEG fallback (valid JFIF) when ffmpeg is unavailable.
MINI_JPEG = bytes.fromhex(
    "ffd8ffe000104a46494600010100000100010000ffdb004300080606070605080707070909080a0c140d0c0b0b0c191213"
    "0f141d1a1f1e1d1a1c1c20242e2720222c231c1c2837292c30313434341f27393d38323c2e333432ffc0000b0800010001"
    "01011f00ffc4001f0000010501010101010100000000000000000102030405060708090a0bffc400b51000020103030204"
    "03050504040000017d01020300041105122131410613516107227114328191a1082342b1c11552d1f02433627282f125"
    "090a161718191a262728292a3435363738393a434445464748494a535455565758595a636465666768696a7374757677"
    "78797a838485868788898a92939495969798999aa2a3a4a5a6a7a8a9aab2b3b4b5b6b7b8b9bac2c3c4c5c6c7c8c9cad2"
    "d3d4d5d6d7d8d9dae1e2e3e4e5e6e7e8e9eaf1f2f3f4f5f6f7f8f9faffda00080001000100003f00fb4efbffd9"
)


async def grab_jpeg(ffmpeg: str | None, ch: ChannelConfig, timeout: float = 8.0) -> bytes:
    if not ffmpeg:
        return MINI_JPEG
    ff_path = Path(ffmpeg)
    if not ff_path.is_file() and not shutil.which(ffmpeg):
        return MINI_JPEG
    src = ch.source.uri if ch.source.uri else None
    cmd: list[str]
    if ch.source.type.value == "testsrc" or not src:
        cmd = [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"testsrc=size={ch.encoder.width}x{ch.encoder.height}:rate=1",
            "-frames:v",
            "1",
            "-f",
            "image2",
            "pipe:1",
        ]
    else:
        cmd = [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            src,
            "-frames:v",
            "1",
            "-f",
            "image2",
            "pipe:1",
        ]
    try:
        kw = subprocess_hidden_kwargs(stderr=asyncio.subprocess.DEVNULL)
        kw["stdout"] = asyncio.subprocess.PIPE
        proc = await asyncio.create_subprocess_exec(*cmd, **kw)
        bind_child(proc.pid)
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        finally:
            # Also reap on request cancellation, not only on timeout.
            if proc.returncode is None:
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
                await proc.communicate()
        if out.startswith(b"\xff\xd8"):
            return out
    except (TimeoutError, FileNotFoundError, OSError) as exc:
        log.debug("snapshot fallback: %s", exc)
    return MINI_JPEG
