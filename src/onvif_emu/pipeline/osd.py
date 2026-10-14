"""FFmpeg drawtext/drawbox OSD graph.

Rebuild the filter string only when the signature of visible flags + style + imaging
changes (dirty-flag). Constant rebuilds of drawtext graphs cause stutter.
"""

from __future__ import annotations

from dataclasses import dataclass

from onvif_emu.models import ImagingConfig, OsdConfig, OsdStyle


def hex_to_ffmpeg(color: str, opacity: float) -> str:
    c = color.lstrip("#")
    if len(c) == 3:
        c = "".join(ch * 2 for ch in c)
    if len(c) != 6:
        c = "00FF88"
    r, g, b = int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)
    a = int(max(0.0, min(1.0, opacity)) * 255)
    return f"0x{r:02X}{g:02X}{b:02X}{a:02X}"


def _pos(style: OsdStyle, w_expr: str = "12", h_expr: str = "12") -> tuple[str, str]:
    margin = 16
    table = {
        "top-left": (str(margin), str(margin)),
        "top-right": (f"w-tw-{margin}", str(margin)),
        "bottom-left": (str(margin), f"h-th-{margin}"),
        "bottom-right": (f"w-tw-{margin}", f"h-th-{margin}"),
        "center": ("(w-tw)/2", "(h-th)/2"),
    }
    return table.get(style.position, table["top-left"])


@dataclass(frozen=True, slots=True)
class OsdFrame:
    pan: int = 0
    tilt: int = 0
    zoom: int = 0
    iris: int = 0
    focus: int = 0
    stop: int = 0
    motion: bool = False
    tamper: bool = False
    analytics: bool = False
    text: str = ""


def imaging_eq(img: ImagingConfig) -> str:
    """Map 0..100 UI sliders to FFmpeg eq filter. 50 = identity."""
    brightness = (img.brightness - 50) / 50.0  # -1..1
    contrast = max(0.01, img.contrast / 50.0)  # 0..2
    saturation = max(0.0, img.saturation / 50.0)
    gamma = 1.0 + (img.sharpness - 50) / 200.0  # mild
    return f"eq=brightness={brightness:.3f}:contrast={contrast:.3f}:saturation={saturation:.3f}:gamma={gamma:.3f}"


def build_osd_filter(frame: OsdFrame, osd: OsdConfig, imaging: ImagingConfig) -> str:
    parts: list[str] = [imaging_eq(imaging)]
    if not osd.enabled:
        return ",".join(parts)
    style = osd.style
    color = hex_to_ffmpeg(style.color, style.opacity)
    fontsize = style.size
    x, y = _pos(style)
    icons: list[str] = []
    if style.show_arrows:
        if frame.tilt > 0:
            icons.append("UP")
        elif frame.tilt < 0:
            icons.append("DOWN")
        if frame.pan < 0:
            icons.append("LEFT")
        elif frame.pan > 0:
            icons.append("RIGHT")
        if frame.stop:
            icons.append("STOP")
    if style.show_zoom:
        if frame.zoom > 0:
            icons.append("Z+")
        elif frame.zoom < 0:
            icons.append("Z-")
    if style.show_iris:
        if frame.iris > 0:
            icons.append("IRIS+")
        elif frame.iris < 0:
            icons.append("IRIS-")
    if style.show_focus:
        if frame.focus > 0:
            icons.append("F+")
        elif frame.focus < 0:
            icons.append("F-")
    if style.show_events:
        if frame.motion:
            icons.append("MOTION")
        if frame.tamper:
            icons.append("TAMPER")
        if frame.analytics:
            icons.append("ANALYTICS")
    label = "  ".join(icons)
    if style.show_text_status and frame.text:
        label = f"{label} | {frame.text}" if label else frame.text
    if label:
        escaped = label.replace("\\", "\\\\").replace(":", "\\:").replace("'", r"\'")
        parts.append(
            f"drawtext=text='{escaped}':fontcolor={color}:fontsize={fontsize}:"
            f"x={x}:y={y}:box=1:boxcolor=0x00000099:boxborderw=8"
        )
    return ",".join(parts)


def osd_signature(frame: OsdFrame, osd: OsdConfig, imaging: ImagingConfig) -> tuple:
    return (
        frame,
        osd.enabled,
        osd.style.color,
        osd.style.opacity,
        osd.style.size,
        osd.style.position,
        osd.style.show_text_status,
        osd.style.show_arrows,
        imaging.brightness,
        imaging.contrast,
        imaging.saturation,
        imaging.sharpness,
    )


def osd_config_signature(osd: OsdConfig, imaging: ImagingConfig) -> tuple:
    """Signature for pipeline restart: style/imaging only (not live PTZ/event frame)."""
    st = osd.style
    return (
        osd.enabled,
        st.color,
        st.opacity,
        st.size,
        st.position,
        st.duration_ms,
        st.show_text_status,
        st.show_arrows,
        st.show_zoom,
        st.show_iris,
        st.show_focus,
        st.show_events,
        imaging.brightness,
        imaging.contrast,
        imaging.saturation,
        imaging.sharpness,
    )
