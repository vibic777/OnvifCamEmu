"""FFmpeg pieces for burnt-in OSD (date / time stamps, picture) and the picture / network trouble simulator.

Everything here is static for the life of one FFmpeg process (a config change restarts the encoders);
live elements (PTZ HUD, event badges) are the separate RGBA overlay from ``overlay.py``.

Timed effects ("bursts") are expressions of the frame time ``t``: the effect is on during the last
``duration_s`` of every ``period_s`` - at the end, not the start, so a stream never begins frozen or covered.
"""

from __future__ import annotations

import sys
from pathlib import Path

from onvif_emu.models import (
    ImpairBurst,
    ImpairConfig,
    OsdConfig,
    OsdImage,
    OsdStamp,
    SourceConfig,
    SourceType,
)

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp", ".tif", ".tiff"}
REF_HEIGHT = 1080  # stamp sizes are given for 1080 lines and scaled for every profile


# ------------------------------------------------------------------ helpers
def ff_color(color: str, alpha: float = 1.0) -> str:
    c = (color or "").strip().lstrip("#")
    if len(c) == 3:
        c = "".join(ch * 2 for ch in c)
    if len(c) != 6 or any(ch not in "0123456789abcdefABCDEF" for ch in c):
        c = "FFFFFF"
    return f"0x{c.upper()}@{max(0.0, min(1.0, alpha)):.3f}"


def _esc_path(path: Path | str) -> str:
    """A file path as a filter option value (Windows drive colon escaped, quoted)."""
    return "'" + str(path).replace("\\", "/").replace(":", "\\:").replace("'", "") + "'"


def gate(b: ImpairBurst) -> str:
    """1 while a burst is active, else 0 (commas are fine: callers quote the expression)."""
    if b.mode == "constant":
        return "1"
    p = max(b.period_s, 0.1)
    d = min(b.duration_s, p)
    return f"gte(mod(t,{p:g}),{p - d:g})"


def burst_active(b: ImpairBurst, seconds_since_start: float) -> bool:
    """Same window in Python (to raise events in step with the picture)."""
    if b.mode == "constant":
        return True
    p = max(b.period_s, 0.1)
    d = min(b.duration_s, p)
    return (seconds_since_start % p) >= p - d


def _anchor_xy(pos: str, x_pct: float, y_pct: float, *, W: str, H: str, w: str, h: str, m: int) -> tuple[str, str]:
    """x/y expressions for an element of size (w, h) inside a frame (W, H) with margin m."""
    if pos == "custom":
        return f"min({W}*{x_pct / 100:g},{W}-{w})", f"min({H}*{y_pct / 100:g},{H}-{h})"
    vert, horiz = ("middle", "center") if pos == "center" else (pos.split("-", 1) + ["left"])[:2]
    x = {"left": f"{m}", "center": f"({W}-{w})/2", "right": f"{W}-{w}-{m}"}.get(horiz, f"{m}")
    y = {"top": f"{m}", "middle": f"({H}-{h})/2", "bottom": f"{H}-{h}-{m}"}.get(vert, f"{m}")
    return x, y


# ------------------------------------------------------------------ date / time stamps
def _font_opt(font: str) -> str:
    if sys.platform == "win32":
        name = "consola.ttf" if font == "mono" else "arial.ttf"
        f = Path("C:/Windows/Fonts") / name
        if f.is_file():
            return f"fontfile={_esc_path(f)}"
    return "font=" + ("Monospace" if font == "mono" else "Sans")


def stamp_textfile(kind: str, fmt: str, folder: Path) -> Path:
    """drawtext reads the strftime format from a file: no quoting levels to get wrong."""
    body = (fmt or "%H:%M:%S").replace("\\", "\\\\").replace(":", "\\:").replace("}", "\\}")
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"osd_{kind}.txt"
    text = "%{localtime:" + body + "}"
    if not path.is_file() or path.read_text(encoding="utf-8") != text:
        path.write_text(text, encoding="utf-8")
    return path


def _drawtext(st: OsdStamp, textfile: Path, height: int, dy: int) -> str:
    fs = max(6, round(st.size * height / REF_HEIGHT))
    m = max(4, round(height * 0.02))
    x, y = _anchor_xy(st.position, st.x, st.y, W="w", H="h", w="tw", h="th", m=m)
    if dy:
        y = f"{y}+{dy}" if dy > 0 else f"{y}-{-dy}"
    opts = [
        f"textfile={_esc_path(textfile)}", _font_opt(st.font), f"fontsize={fs}",
        f"fontcolor={ff_color(st.color, st.opacity)}", f"x='{x}'", f"y='{y}'",
    ]
    if st.bg_opacity > 0:
        opts += ["box=1", f"boxcolor={ff_color(st.bg_color, st.bg_opacity)}", f"boxborderw={max(2, fs // 4)}"]
    if st.outline:
        opts += [f"borderw={max(1, fs // 18)}", f"bordercolor={ff_color('#000000', 0.8 * st.opacity)}"]
    return "drawtext=" + ":".join(opts)


def stamp_filters(osd: OsdConfig | None, height: int, folder: Path) -> list[str]:
    """drawtext filters for the date and time stamps (stacked when they share a corner)."""
    if osd is None:
        return []
    items = [(k, s) for k, s in (("date", osd.date), ("time", osd.time)) if s.enabled]
    out: list[str] = []
    for i, (kind, st) in enumerate(items):
        dy = 0
        if len(items) == 2 and items[0][1].position == items[1][1].position and st.position != "custom":
            other = items[1 - i][1]
            line = round(max(6, round(other.size * height / REF_HEIGHT)) * 1.6)
            if st.position.startswith("bottom"):
                dy = -line if i == 0 else 0  # date above the time
            else:
                dy = line if i == 1 else 0  # time below the date
        out.append(_drawtext(st, stamp_textfile(kind, st.format, folder), height, dy))
    return out


# ------------------------------------------------------------------ picture
def image_usable(img: OsdImage | None) -> bool:
    return bool(img and img.enabled and img.path and Path(img.path).is_file())


def image_input_args(img: OsdImage) -> list[str]:
    return ["-loop", "1", "-framerate", "1", "-i", img.path]


def image_graph(img: OsdImage, idx: int, base: str, out: str, width: int, height: int) -> str:
    """[idx:v] -> scaled, keyed, faded, plated logo overlaid on [base] as [out]."""
    pw = max(2, round(width * img.size / 100) // 2 * 2)
    chain = ["format=rgba", f"scale={pw}:-2"]
    if img.transparent_color:
        chain.append(f"colorkey={ff_color(img.transparent_color).split('@')[0]}:{img.key_similarity:g}:0.05")
    if img.opacity < 1:
        chain.append(f"colorchannelmixer=aa={img.opacity:.3f}")
    if img.bg_opacity > 0:
        pad = max(2, round(height * 0.008))
        chain.append(f"pad=iw+{2 * pad}:ih+{2 * pad}:{pad}:{pad}:color={ff_color(img.bg_color, img.bg_opacity)}")
    m = max(4, round(height * 0.02))
    x, y = _anchor_xy(img.position, img.x, img.y, W="W", H="H", w="w", h="h", m=m)
    return f"[{idx}:v]{','.join(chain)}[logo];[{base}][logo]overlay=x='{x}':y='{y}':format=auto:shortest=1[{out}]"


# ------------------------------------------------------------------ end of a clip that does not loop
def end_filters(src: SourceConfig) -> list[str]:
    if src.type != SourceType.file or src.loop:
        return []
    if src.end_action == "freeze":
        return ["tpad=stop_mode=clone:stop=-1", "realtime"]
    if src.end_action == "black":
        return ["tpad=stop_mode=add:stop=-1:color=black", "realtime"]
    return []


# ------------------------------------------------------------------ trouble simulator
def impair_filters(imp: ImpairConfig | None) -> list[str]:
    """Filters on the source picture, before scaling to the profile size."""
    if imp is None or not imp.enabled:
        return []
    f: list[str] = []
    keep = []
    if imp.stutter:
        keep.append(f"gt(random(0),{imp.stutter / 100:g})")
    if imp.freeze:
        keep.append(f"not({gate(imp.freeze_burst)})")
    if keep:
        f.append("select='" + "*".join(keep) + "'")  # dropped frames are repeated by -r: jerks / freezes
    if imp.shake:
        a = 0.015 + 0.06 * imp.shake  # amplitude, share of the frame
        g = gate(imp.shake_burst)
        f.append(
            f"crop=w='iw*{1 - 2 * a:.4f}':h='ih*{1 - 2 * a:.4f}'"
            f":x='iw*{a:.4f}*(1+{g}*(0.6*sin(t*29)+0.4*sin(t*47+1)))'"
            f":y='ih*{a:.4f}*(1+{g}*(0.6*sin(t*31+2)+0.4*sin(t*53)))'"
        )
    if imp.rain:
        r = imp.rain
        shift = max(1, round(3 * r))
        f += [
            f"gblur=sigma={0.8 + 4 * r:.2f}",
            "tmix=frames=3",
            f"eq=contrast={1 - 0.3 * r:.3f}:saturation={1 - 0.35 * r:.3f}:brightness={-0.06 * r:.3f}",
            f"rgbashift=rh=-{shift}:bh={shift}",
            f"noise=alls={round(6 + 14 * r)}:allf=t",
        ]
    if imp.noise:
        f.append(f"noise=alls={round(8 + 45 * imp.noise)}:allf=t+u")
    if imp.pixelate > 1:
        n = imp.pixelate
        f += [f"scale=trunc(iw/{n}/2)*2:trunc(ih/{n}/2)*2:flags=area", f"scale=iw*{n}:ih*{n}:flags=neighbor"]
    if imp.bitrate == "vbr_jumps":
        f.append(f"noise=alls=70:allf=t:enable='{gate(imp.bitrate_burst)}'")  # detail bursts -> bitrate spikes
    if imp.cover != "off":
        g = gate(imp.cover_burst)
        if imp.cover == "black":
            f.append(f"drawbox=x=0:y=0:w=iw:h=ih:color=black@1:t=fill:enable='{g}'")
        elif imp.cover == "blur":
            f.append(f"boxblur=lr='min(w,h)/8':lp=3:enable='{g}'")
        else:  # spray paint over most of the lens
            f += [
                f"drawbox=x=iw*0.12:y=ih*0.18:w=iw*0.62:h=ih*0.55:color=0xB01515@0.9:t=fill:enable='{g}'",
                f"drawbox=x=iw*0.45:y=ih*0.05:w=iw*0.4:h=ih*0.35:color=0x202020@0.85:t=fill:enable='{g}'",
            ]
    return f


def impair_encoder_args(imp: ImpairConfig | None, codec: str, bitrate: int) -> list[str]:
    """Extra encoder options (appended after the normal ones, so they win)."""
    if imp is None or not imp.enabled or codec == "mjpeg":
        return []
    out: list[str] = []
    br = min(bitrate, imp.bitrate_cap) if imp.bitrate_cap else bitrate
    if imp.bitrate == "cbr":
        out += ["-b:v", f"{br}k", "-minrate", f"{br}k", "-maxrate", f"{br}k", "-bufsize", f"{max(1, br // 2)}k"]
        if codec == "h264":
            out += ["-x264-params", "nal-hrd=cbr:force-cfr=1"]
    elif imp.bitrate == "vbr_jumps":
        out += ["-b:v", f"{br}k", "-maxrate", f"{br * 4}k", "-bufsize", f"{br * 4}k"]
    elif imp.bitrate_cap:
        out += ["-b:v", f"{br}k", "-maxrate", f"{br}k", "-bufsize", f"{br * 2}k"]
    if imp.loss:
        out += ["-bsf:v", f"noise=drop='lt(random(0),{imp.loss / 100:g})'"]
    return out
