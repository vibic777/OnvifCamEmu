from __future__ import annotations

import asyncio
import logging
import shutil
from pathlib import Path

from onvif_emu.models import ChannelConfig, OsdConfig, SourceType
from onvif_emu.pipeline.effects import (
    end_filters,
    image_graph,
    image_input_args,
    image_usable,
    impair_encoder_args,
    impair_filters,
    stamp_filters,
)
from onvif_emu.procguard import bind_child
from onvif_emu.util import subprocess_hidden_kwargs

log = logging.getLogger(__name__)


def find_ffmpeg(explicit: str = "ffmpeg") -> str | None:
    from onvif_emu.paths import iter_bin_candidates

    if explicit and explicit not in {"ffmpeg", "ffmpeg.exe"} and Path(explicit).is_file():
        return explicit
    for p in iter_bin_candidates("ffmpeg"):
        if p.is_file():
            return str(p)
    return shutil.which(explicit) or shutil.which("ffmpeg") or shutil.which("ffmpeg.exe")


def source_input_args(ch: ChannelConfig) -> list[str]:
    src = ch.source
    t = src.type
    if t == SourceType.testsrc:
        fps = ch.encoder.fps
        w, h = ch.encoder.width, ch.encoder.height
        # -re: generate frames in real time; without it FFmpeg encodes flat out and eats a whole CPU
        return ["-re", "-f", "lavfi", "-i", f"testsrc=size={w}x{h}:rate={fps}"]
    if t == SourceType.file:
        args = ["-stream_loop", "-1"] if src.loop else []
        args += ["-re", "-i", src.uri]
        return args
    if t in {SourceType.rtsp, SourceType.http, SourceType.hls, SourceType.dash}:
        args = ["-rtsp_transport", "tcp"] if t == SourceType.rtsp else []
        if src.buffer_ms:
            args += ["-analyzeduration", str(src.buffer_ms * 1000), "-probesize", "2M"]
        args += ["-i", src.uri]
        return args
    if t == SourceType.udp:
        return ["-i", src.uri]
    if t == SourceType.v4l2:
        return ["-f", "v4l2", "-i", src.uri or "/dev/video0"]
    if t == SourceType.x11grab:
        return ["-f", "x11grab", "-i", src.uri or ":0.0"]
    return ["-f", "lavfi", "-i", "testsrc=size=1280x720:rate=25"]


TIER_SUFFIX = ("", "_sub", "_third")
OVERLAY_FPS = 8


def tier_config(ch: ChannelConfig, tier: int):
    """Encoder block of stream tier 0 (full), 1 (medium/sub) or 2 (low/third)."""
    return (ch.encoder, ch.sub, ch.third)[int(tier)]


def encoder_args(ch: ChannelConfig, vf: str, is_sub: bool | int = False, *, force_encode: bool = False) -> list[str]:
    """``is_sub`` is the stream tier: False/0 main, True/1 sub, 2 third."""
    enc = ch.encoder
    w, h, fps, br, gop, codec = enc.width, enc.height, enc.fps, enc.bitrate, enc.gop, enc.codec.value
    if int(is_sub):
        t = tier_config(ch, int(is_sub))
        w, h, fps, br, gop = t.width, t.height, t.fps, t.bitrate, t.gop
        codec = t.codec.value
    copy = (
        (not ch.source.transcode) and ch.source.type not in {SourceType.testsrc}
        and not vf and not is_sub and not force_encode
    )
    args: list[str] = []
    if vf:
        args += ["-vf", vf]
    if copy:
        args += ["-c:v", "copy"]
    elif codec == "mjpeg":
        args += ["-c:v", "mjpeg", "-q:v", "5", "-r", str(fps), "-s", f"{w}x{h}"]
    elif codec == "h265":
        args += [
            "-c:v",
            "libx265",
            "-preset",
            "veryfast",
            "-x265-params",
            f"keyint={gop}:min-keyint={gop}:scenecut=0",
            "-b:v",
            f"{br}k",
            "-r",
            str(fps),
            "-s",
            f"{w}x{h}",
            "-pix_fmt",
            "yuv420p",
        ]
    else:
        args += [
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-tune",
            "zerolatency",
            "-profile:v",
            "main",
            "-g",
            str(gop),
            "-keyint_min",
            str(gop),
            "-b:v",
            f"{br}k",
            "-maxrate",
            f"{int(br * 1.2)}k",
            "-bufsize",
            f"{br * 2}k",
            "-r",
            str(fps),
            "-s",
            f"{w}x{h}",
            "-pix_fmt",
            "yuv420p",
        ]
    args += ["-an"]  # video-only for RTSP simplicity; audio optional
    return args


def publish_url(rtsp_port: int, path: str, host: str = "127.0.0.1") -> str:
    return f"rtsp://{host}:{rtsp_port}/{path}"


def build_command(
    ffmpeg: str,
    ch: ChannelConfig,
    rtsp_port: int,
    vf: str,
    is_sub: bool | int = False,
    *,
    publish_host: str = "127.0.0.1",
    overlay: tuple[int, int] | None = None,
    force_encode: bool = False,
    osd: OsdConfig | None = None,
    osd_dir: Path | None = None,
) -> list[str]:
    """One FFmpeg process = one profile of one channel, published to MediaMTX.

    ``overlay`` = (canvas width, height): the live HUD comes in as raw RGBA on stdin (input 1).
    ``osd`` + ``osd_dir``: burnt-in date/time stamps and picture (input 2 when present).
    The channel's ``impair`` block adds the trouble simulator; ``source.end_action`` a frozen / black tail.
    """
    tier = int(is_sub)
    t = tier_config(ch, tier)
    path = ch.rtsp_path + TIER_SUFFIX[tier]
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "warning"]
    if not overlay:
        cmd.append("-nostdin")
    cmd += source_input_args(ch)

    pre = end_filters(ch.source) + ([vf] if vf else []) + impair_filters(ch.impair)
    stamps = stamp_filters(osd, t.height, osd_dir) if (osd is not None and osd_dir is not None) else []
    img = osd.image if (osd is not None and image_usable(osd.image)) else None
    extra = impair_encoder_args(ch.impair, t.codec.value, t.bitrate)
    scale = f"scale={t.width}:{t.height}"

    if not (overlay or img or pre or stamps):
        cmd += encoder_args(ch, vf, is_sub=is_sub, force_encode=force_encode) + extra
    elif not (overlay or img):
        cmd += encoder_args(ch, ",".join(pre + [scale] + stamps), is_sub=is_sub, force_encode=True) + extra
    else:
        graph = [f"[0:v]{','.join(pre + [scale] + stamps)}[base]"]
        cur, idx = "base", 1
        ov_idx = None
        if overlay:
            cw, chh = overlay
            cmd += [
                "-thread_queue_size", "16", "-f", "rawvideo", "-pixel_format", "rgba",
                "-video_size", f"{cw}x{chh}", "-framerate", str(OVERLAY_FPS),
                "-use_wallclock_as_timestamps", "1", "-i", "pipe:0",
            ]
            ov_idx, idx = idx, idx + 1
        if img:
            cmd += image_input_args(img)
            graph.append(image_graph(img, idx, cur, "withlogo", t.width, t.height))
            cur, idx = "withlogo", idx + 1
        if ov_idx is not None:
            flag = "neighbor" if t.width >= overlay[0] else "area"  # crisp pixel font when enlarged
            graph.append(
                f"[{ov_idx}:v]{scale}:flags={flag}[ov];"
                f"[{cur}][ov]overlay=0:0:format=auto:eof_action=pass:shortest=1[vout]"
            )
            cur = "vout"
        cmd += ["-filter_complex", ";".join(graph), "-map", f"[{cur}]"]
        cmd += encoder_args(ch, "", is_sub=is_sub, force_encode=True) + extra
    cmd += ["-f", "rtsp", "-rtsp_transport", "tcp", publish_url(rtsp_port, path, host=publish_host)]
    return cmd


class FfmpegProcess:
    def __init__(self, cmd: list[str], name: str, *, stdin_pipe: bool = False):
        self.cmd = cmd
        self.name = name
        self.stdin_pipe = stdin_pipe  # overlay frames are written to stdin
        self.proc: asyncio.subprocess.Process | None = None

    async def start(self) -> None:
        log.info("Starting pipeline %s: %s", self.name, " ".join(self.cmd[:8]))
        kw = subprocess_hidden_kwargs(stderr=asyncio.subprocess.DEVNULL)
        if self.stdin_pipe:
            kw["stdin"] = asyncio.subprocess.PIPE
        self.proc = await asyncio.create_subprocess_exec(*self.cmd, **kw)
        bind_child(self.proc.pid)  # killed by the OS if the emulator dies

    @property
    def stdin(self):
        return self.proc.stdin if self.proc else None

    async def wait(self) -> int:
        if not self.proc:
            return 0
        return await self.proc.wait()

    async def stop(self) -> None:
        if not self.proc or self.proc.returncode is not None:
            return
        try:
            self.proc.terminate()
            try:
                await asyncio.wait_for(self.proc.wait(), timeout=3)
            except TimeoutError:
                self.proc.kill()
                await self.proc.wait()
        except ProcessLookupError:
            pass

    @property
    def alive(self) -> bool:
        return self.proc is not None and self.proc.returncode is None
