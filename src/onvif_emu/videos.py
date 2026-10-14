"""Video sources from files: the library next to the program (web uploads) and any local path.

Two ways to give a channel a video file:

* upload it from the browser — it is copied into ``video/`` beside the program (or ``video_dir``);
* point the channel at a file that already lies on the machine running the emulator (no copy).
"""

from __future__ import annotations

import json
import logging
import os
import re
import string
import subprocess
import sys
from pathlib import Path

from onvif_emu.paths import executable_dir

log = logging.getLogger(__name__)

VIDEO_DIR_NAME = "video"
# anything FFmpeg demuxes as video; the list only filters the library / browser and guards uploads
VIDEO_EXTS = {
    ".mp4", ".m4v", ".mov", ".qt", ".mkv", ".webm", ".avi", ".divx", ".wmv", ".asf", ".flv", ".f4v",
    ".ts", ".m2ts", ".mts", ".m2t", ".mpg", ".mpeg", ".mpe", ".m1v", ".m2v", ".vob", ".3gp", ".3g2",
    ".ogv", ".mxf", ".dv", ".h264", ".264", ".h265", ".265", ".hevc", ".y4m", ".mjpeg", ".mjpg",
}
_UNSAFE = re.compile(r"[^\w.\- ()\[\]]", re.UNICODE)


def resolve_video_dir(configured: str | None = "") -> Path:
    raw = (configured or "").strip()
    if raw:
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = executable_dir() / path
    else:
        path = executable_dir() / VIDEO_DIR_NAME
    path.mkdir(parents=True, exist_ok=True)
    resolved = path.resolve()
    log.debug("Video dir → %s", resolved)
    return resolved


def is_video_name(name: str) -> bool:
    return Path(name).suffix.lower() in VIDEO_EXTS


def safe_filename(name: str, exts: set[str] | None = None) -> str:
    """Upload name -> a plain file name: path parts dropped, odd characters (``,&+#'``...) become ``_``."""
    exts = exts or VIDEO_EXTS
    base = Path(name.replace("\\", "/")).name
    stem, ext = os.path.splitext(base)
    stem = re.sub(r"_+", "_", _UNSAFE.sub("_", stem)).strip(" ._")
    if not stem or base in {".", ".."}:
        raise ValueError("invalid filename")
    if ext.lower() not in exts:
        raise ValueError(f"unsupported file type: {ext or '(none)'}")
    return stem + ext.lower()


def list_videos(folder: Path) -> list[dict]:
    folder.mkdir(parents=True, exist_ok=True)
    out: list[dict] = []
    for p in sorted(folder.iterdir()):
        if p.is_file() and p.suffix.lower() in VIDEO_EXTS:
            out.append({"name": p.name, "size": p.stat().st_size, "path": str(p)})
    return out


def dest_path(folder: Path, name: str, *, unique: bool = False, exts: set[str] | None = None) -> Path:
    """Where an upload lands. ``unique`` adds " (1)", " (2)" ... instead of overwriting (the old file may be playing)."""
    folder = folder.resolve()
    dest = (folder / safe_filename(name, exts)).resolve()
    try:
        dest.relative_to(folder)
    except ValueError as exc:
        raise ValueError("path escape") from exc
    if unique:
        n = 1
        stem, ext = dest.stem, dest.suffix
        while dest.exists():
            dest = folder / f"{stem} ({n}){ext}"
            n += 1
    return dest


# ------------------------------------------------------------------ local files
def check_local_file(raw: str) -> Path:
    """A file on this machine to play as-is. Raises ValueError with a readable reason."""
    text = (raw or "").strip().strip('"').strip("'")
    if not text:
        raise ValueError("empty path")
    path = Path(os.path.expandvars(text)).expanduser()
    if not path.is_absolute():
        path = executable_dir() / path
    if not path.exists():
        raise ValueError(f"file not found: {path}")
    if not path.is_file():
        raise ValueError(f"not a file: {path}")
    return path.resolve()


def _drives() -> list[str]:
    return [f"{d}:\\" for d in string.ascii_uppercase if os.path.exists(f"{d}:\\")]


def browse(raw: str = "", exts: set[str] | None = None) -> dict:
    """One folder level for the GUI file picker: sub-folders and files with ``exts`` (default: video)."""
    exts = exts or VIDEO_EXTS
    text = (raw or "").strip().strip('"')
    if not text:
        if sys.platform == "win32":
            return {"dir": "", "parent": None, "dirs": _drives(), "files": []}
        text = "/"
    folder = Path(os.path.expandvars(text)).expanduser()
    if folder.is_file():
        folder = folder.parent
    if not folder.is_dir():
        raise ValueError(f"folder not found: {folder}")
    folder = folder.resolve()
    dirs: list[str] = []
    files: list[dict] = []
    try:
        entries = sorted(folder.iterdir(), key=lambda p: p.name.lower())
    except OSError as exc:
        raise ValueError(f"cannot read {folder}: {exc.strerror or exc}") from exc
    for p in entries:
        try:
            if p.name.startswith(".") or p.name.startswith("$"):
                continue
            if p.is_dir():
                dirs.append(p.name)
            elif p.suffix.lower() in exts:
                files.append({"name": p.name, "size": p.stat().st_size, "path": str(p)})
        except OSError:
            continue
    parent = str(folder.parent) if folder.parent != folder else ("" if sys.platform == "win32" else None)
    return {"dir": str(folder), "parent": parent, "dirs": dirs, "files": files}


# ------------------------------------------------------------------ ffprobe
def find_ffprobe(ffmpeg: str | None) -> str | None:
    from onvif_emu.paths import iter_bin_candidates

    if ffmpeg:
        p = Path(ffmpeg)
        cand = p.with_name(p.name.replace("ffmpeg", "ffprobe"))
        if cand.is_file():
            return str(cand)
    for c in iter_bin_candidates("ffprobe"):
        if c.is_file():
            return str(c)
    import shutil

    return shutil.which("ffprobe")


_PROBE_CACHE: dict[tuple, dict | None] = {}


def probe_video(path: str | Path, ffprobe: str | None) -> dict | None:
    """First video stream of a file: {codec, width, height, duration}. None = no video stream / unreadable.

    Raises FileNotFoundError when ffprobe itself is missing (the caller then just skips the check).
    """
    if not ffprobe:
        raise FileNotFoundError("ffprobe")
    p = Path(path)
    try:
        st = p.stat()
    except OSError:
        return None
    key = (str(p), st.st_size, st.st_mtime_ns)
    if key in _PROBE_CACHE:
        return _PROBE_CACHE[key]
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
    try:
        out = subprocess.run(
            [ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries",
             "stream=codec_name,width,height:format=duration,format_name", "-of", "json", str(p)],
            capture_output=True, timeout=20, creationflags=flags,
        )
        data = json.loads(out.stdout or b"{}")
    except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
        log.debug("ffprobe %s failed: %s", p, exc)
        return None
    streams = data.get("streams") or []
    if not streams:
        info = None
    else:
        s = streams[0]
        fmt = data.get("format") or {}
        try:
            duration = round(float(fmt.get("duration") or 0), 1)
        except ValueError:
            duration = 0.0
        info = {"codec": s.get("codec_name", ""), "width": s.get("width", 0), "height": s.get("height", 0),
                "duration": duration, "container": fmt.get("format_name", "")}
    _PROBE_CACHE[key] = info
    return info


# codec names ffprobe reports for what each ONVIF encoder setting streams as-is
COPY_CODECS = {"h264": "h264", "h265": "hevc"}


def can_copy(info: dict | None, encoder_codec: str) -> bool:
    """Remux without re-encoding only when the file already carries the codec the profile advertises."""
    return bool(info) and info.get("codec") == COPY_CODECS.get(encoder_codec)
