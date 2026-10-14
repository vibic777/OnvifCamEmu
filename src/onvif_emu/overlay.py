"""Live video overlay: PTZ command HUD, event badges, analytics boxes, relay / dry-contact LEDs.

Drawn in pure Python into an RGBA canvas (built-in 5x7 pixel font, no fonts or drawing libraries needed),
streamed to FFmpeg's stdin as a second raw-video input and composited with the ``overlay`` filter.
Because the canvas is redrawn ~8 times a second from the live state, everything reacts to real commands:

* PTZ HUD  - D-pad arrows, STOP, ZOOM +/-, IRIS +/-, FOCUS +/- light up while the command is active;
* badges   - MOTION, TAMPER, FIELD, LINE CROSS, META xN and any custom event, colour-coded;
* boxes    - bounding boxes (+ class, confidence, track id) of injected analytics/metadata objects;
* I/O      - dry-contact inputs and relay outputs as LEDs (green idle, red active).
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from onvif_emu.runtime import Runtime

log = logging.getLogger(__name__)

CANVAS_W = 640
FPS = 8

# --------------------------------------------------------------------------- 5x7 font
_FONT_SRC = """
A .###. #...# #...# ##### #...# #...# #...#
B ####. #...# #...# ####. #...# #...# ####.
C .#### #.... #.... #.... #.... #.... .####
D ####. #...# #...# #...# #...# #...# ####.
E ##### #.... #.... ####. #.... #.... #####
F ##### #.... #.... ####. #.... #.... #....
G .#### #.... #.... #..## #...# #...# .####
H #...# #...# #...# ##### #...# #...# #...#
I ##### ..#.. ..#.. ..#.. ..#.. ..#.. #####
J ..### ...#. ...#. ...#. ...#. #..#. .##..
K #...# #..#. #.#.. ##... #.#.. #..#. #...#
L #.... #.... #.... #.... #.... #.... #####
M #...# ##.## #.#.# #.#.# #...# #...# #...#
N #...# ##..# #.#.# #..## #...# #...# #...#
O .###. #...# #...# #...# #...# #...# .###.
P ####. #...# #...# ####. #.... #.... #....
Q .###. #...# #...# #...# #.#.# #..#. .##.#
R ####. #...# #...# ####. #.#.. #..#. #...#
S .#### #.... #.... .###. ....# ....# ####.
T ##### ..#.. ..#.. ..#.. ..#.. ..#.. ..#..
U #...# #...# #...# #...# #...# #...# .###.
V #...# #...# #...# #...# #...# .#.#. ..#..
W #...# #...# #...# #.#.# #.#.# ##.## #...#
X #...# #...# .#.#. ..#.. .#.#. #...# #...#
Y #...# #...# .#.#. ..#.. ..#.. ..#.. ..#..
Z ##### ....# ...#. ..#.. .#... #.... #####
0 .###. #...# #..## #.#.# ##..# #...# .###.
1 ..#.. .##.. ..#.. ..#.. ..#.. ..#.. .###.
2 .###. #...# ....# ...#. ..#.. .#... #####
3 ##### ...#. ..#.. ...#. ....# #...# .###.
4 ...#. ..##. .#.#. #..#. ##### ...#. ...#.
5 ##### #.... ####. ....# ....# #...# .###.
6 ..##. .#... #.... ####. #...# #...# .###.
7 ##### ....# ...#. ..#.. .#... .#... .#...
8 .###. #...# #...# .###. #...# #...# .###.
9 .###. #...# #...# .#### ....# ...#. .##..
. ..... ..... ..... ..... ..... ..##. ..##.
: ..... ..##. ..##. ..... ..##. ..##. .....
- ..... ..... ..... ##### ..... ..... .....
+ ..... ..#.. ..#.. ##### ..#.. ..#.. .....
/ ....# ....# ...#. ..#.. .#... #.... #....
_ ..... ..... ..... ..... ..... ..... #####
% ##..# ##..# ...#. ..#.. .#... #..## #..##
( ...#. ..#.. .#... .#... .#... ..#.. ...#.
) .#... ..#.. ...#. ...#. ...#. ..#.. .#...
= ..... ..... ##### ..... ##### ..... .....
# .#.#. .#.#. ##### .#.#. ##### .#.#. .#.#.
, ..... ..... ..... ..... ..##. ..#.. .#...
! ..#.. ..#.. ..#.. ..#.. ..#.. ..... ..#..
? .###. #...# ....# ...#. ..#.. ..... ..#..
* ..... #.#.# .###. ##### .###. #.#.# .....
[ .###. .#... .#... .#... .#... .#... .###.
] .###. ...#. ...#. ...#. ...#. ...#. .###.
< ...#. ..#.. .#... #.... .#... ..#.. ...#.
> .#... ..#.. ...#. ....# ...#. ..#.. .#...
| ..#.. ..#.. ..#.. ..#.. ..#.. ..#.. ..#..
' ..#.. ..#.. .#... ..... ..... ..... .....
"""

FONT: dict[str, tuple[int, ...]] = {}
for _line in _FONT_SRC.strip().splitlines():
    _ch, *_rows = _line.split()
    FONT[_ch] = tuple(int("".join("1" if c == "#" else "0" for c in r), 2) for r in _rows)
FONT[" "] = (0,) * 7

_CYRILLIC_FONT_SRC = """
А #...# .#.#. #...# ##### #...# #...# #...#
Б ##### #.... #.... ####. #...# #...# ####.
В ####. #...# #...# ####. #...# #...# ####.
Г ##### #.... #.... #.... #.... #.... #....
Д ..##. .#.#. .#.#. #...# ##### #...# #...#
Е ##### #.... #.... ####. #.... #.... #####
Ё ##### #.#.. #.... ####. #.... #.... #####
Ж #.#.# #.#.# .###. ..#.. .###. #.#.# #.#.#
З ####. ....# ...#. ..##. ....# ....# ####.
И #...# #..## #.#.# ##..# #...# #...# #...#
Й .#.#. #..## #.#.# ##..# #...# #...# #...#
К #...# #..#. #.#.. ##... #.#.. #..#. #...#
Л ..##. .#.#. .#.#. #...# #...# #...# #...#
М #...# ##.## #.#.# #.#.# #...# #...# #...#
Н #...# #...# #...# ##### #...# #...# #...#
О .###. #...# #...# #...# #...# #...# .###.
П ##### #...# #...# #...# #...# #...# #...#
Р ####. #...# #...# ####. #.... #.... #....
С .#### #.... #.... #.... #.... #.... .####
Т ##### ..#.. ..#.. ..#.. ..#.. ..#.. ..#..
У #...# #...# .#.#. ..#.. ..#.. ..#.. .##..
Ф ..#.. .###. #.#.# #.#.# #.#.# .###. ..#..
Х #...# #...# .#.#. ..#.. .#.#. #...# #...#
Ц #...# #...# #...# #...# #...# #...# .####
Ч #...# #...# #...# .#### ....# ....# ....#
Ш #.#.# #.#.# #.#.# #.#.# #.#.# #.#.# #####
Щ #.#.# #.#.# #.#.# #.#.# #.#.# #.#.# #####
Ъ ##... #.... #.... ###.. #...# #...# ###..
Ы #...# #...# #...# ####. #...# #...# ####.
Ь #.... #.... #.... ####. #...# #...# ####.
Э .#### ....# ....# .###. ....# ....# .####
Ю #...# #...# #.### #.#.# #.### #...# #...#
Я .#### #...# #...# .#### .#.#. #..#. #...#
"""
for _line in _CYRILLIC_FONT_SRC.strip().splitlines():
    _ch, *_rows = _line.split()
    FONT[_ch] = tuple(int("".join("1" if c == "#" else "0" for c in r), 2) for r in _rows)

Color = tuple[int, int, int, int]


def parse_color(text: str, alpha: int = 255) -> Color:
    c = text.lstrip("#")
    if len(c) == 3:
        c = "".join(ch * 2 for ch in c)
    try:
        return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16), alpha
    except (ValueError, IndexError):
        return 0, 255, 136, alpha


class Canvas:
    """RGBA canvas; transparent until drawn on."""

    def __init__(self, w: int, h: int):
        self.w, self.h = w, h
        self.buf = bytearray(w * h * 4)

    def rect(self, x: int, y: int, w: int, h: int, color: Color) -> None:
        x0, y0 = max(0, x), max(0, y)
        x1, y1 = min(self.w, x + w), min(self.h, y + h)
        if x1 <= x0 or y1 <= y0:
            return
        row = bytes(color) * (x1 - x0)
        n = (x1 - x0) * 4
        stride = self.w * 4
        idx = y0 * stride + x0 * 4
        for _ in range(y0, y1):
            self.buf[idx : idx + n] = row
            idx += stride

    def frame(self, x: int, y: int, w: int, h: int, color: Color, t: int = 1) -> None:
        self.rect(x, y, w, t, color)
        self.rect(x, y + h - t, w, t, color)
        self.rect(x, y, t, h, color)
        self.rect(x + w - t, y, t, h, color)

    def circle(self, cx: int, cy: int, r: int, color: Color) -> None:
        for dy in range(-r, r + 1):
            half = int((r * r - dy * dy) ** 0.5)
            self.rect(cx - half, cy + dy, 2 * half + 1, 1, color)

    def triangle(self, pts: list[tuple[int, int]], color: Color) -> None:
        """Filled triangle by scanlines."""
        ys = [p[1] for p in pts]
        for y in range(min(ys), max(ys) + 1):
            xs = []
            for i in range(3):
                (x1, y1), (x2, y2) = pts[i], pts[(i + 1) % 3]
                if y1 == y2:
                    continue
                if min(y1, y2) <= y <= max(y1, y2):
                    xs.append(x1 + (y - y1) * (x2 - x1) / (y2 - y1))
            if len(xs) >= 2:
                self.rect(int(min(xs)), y, int(max(xs)) - int(min(xs)) + 1, 1, color)

    def text_width(self, s: str, scale: int) -> int:
        return max(0, len(s) * 6 * scale - scale)

    def text(self, x: int, y: int, s: str, scale: int, color: Color) -> None:
        for i, ch in enumerate(s.upper()):
            rows = FONT.get(ch) or FONT["?"]
            gx = x + i * 6 * scale
            for ry, bits in enumerate(rows):
                for rx in range(5):
                    if bits & (1 << (4 - rx)):
                        self.rect(gx + rx * scale, y + ry * scale, scale, scale, color)

    def tobytes(self) -> bytes:
        return bytes(self.buf)


# --------------------------------------------------------------------------- scene
BADGE_COLORS = {
    "MOTION": (255, 160, 0), "TAMPER": (255, 50, 50), "FIELD": (230, 60, 230), "CELL MOTION": (255, 160, 0),
    "LINE CROSS": (230, 60, 230), "metadata": (40, 210, 240), "relay": (255, 80, 80),
    "contact": (255, 170, 50), "event": (250, 220, 40),
}
CLASS_LABELS_RU = {"Person": "ЧЕЛОВЕК", "Vehicle": "ТРАНСПОРТ", "Animal": "ЖИВОТНОЕ", "Unknown": "НЕИЗВЕСТНО"}
BOX_COLORS = [(255, 80, 80), (80, 255, 120), (80, 160, 255), (255, 220, 60), (240, 90, 240), (60, 230, 230)]


class OverlayRenderer:
    """Turns the runtime's live state of one channel into RGBA frames."""

    def __init__(self, rt: Runtime, channel_id: int, size: tuple[int, int]):
        self.rt = rt
        self.ch = channel_id
        self.w, self.h = size
        self._lit: dict[str, float] = {}  # button -> monotonic time until which it stays lit
        self._stop_seen = 0.0
        self._sig: tuple | None = None
        self._bytes = b""

    # -- state ---------------------------------------------------------------
    def _collect(self, now: float) -> dict[str, Any]:
        rt = self.rt
        style = rt.config.osd.style
        dur = max(0.2, style.duration_ms / 1000.0)
        ch_cfg = next((c for c in rt.config.channels if c.id == self.ch), None)
        ptz_on = bool(ch_cfg and ch_cfg.ptz_enabled)
        servo = rt.servos.get(self.ch) if ptz_on else None
        pos = ""
        label = ""
        if servo is not None:
            f = servo.osd_flags(now)
            for name, val in (("up", f["tilt"] > 0), ("down", f["tilt"] < 0), ("left", f["pan"] < 0),
                              ("right", f["pan"] > 0), ("zp", f["zoom"] > 0), ("zm", f["zoom"] < 0),
                              ("ip", f["iris"] > 0), ("im", f["iris"] < 0), ("fp", f["focus"] > 0),
                              ("fm", f["focus"] < 0)):
                if val:
                    self._lit[name] = now + dur
            if servo.last_command == "Stop" and servo.osd_until != self._stop_seen:
                self._stop_seen = servo.osd_until
                self._lit["stop"] = now + dur
            st = servo.status()
            pos = f"PAN {st.pan:.1f} TILT {st.tilt:.1f} ZOOM {st.zoom:.2f}"
            if now <= servo.osd_label_until:
                label = servo.osd_label
        lit = {k for k, until in self._lit.items() if until > now}
        for k in [k for k, until in self._lit.items() if until <= now]:
            del self._lit[k]
        state = rt.diag.overlay_state(self.ch)
        sim = rt.analytics_for(self.ch)
        mode = getattr(style, "boxes", "injected")
        boxes = []
        if mode != "off":
            for o in sim.objects:
                if mode == "all" or o.expires:
                    b = o.bbox()
                    boxes.append((o.track_id, o.klass, round(o.confidence, 2), round(b["left"], 3), round(b["top"], 3),
                                  round(b["right"], 3), round(b["bottom"], 3)))
        return {"lit": lit, "ptz_on": ptz_on, "pos": pos, "label": label, "badges": state["badges"],
                "relays": state["relays"], "inputs": state["inputs"], "boxes": boxes, "shed": rt.shed_level}

    def signature(self, s: dict[str, Any]) -> tuple:
        style = self.rt.config.osd.style
        return (
            frozenset(s["lit"]), s["ptz_on"], s["pos"], s["label"], tuple(s["badges"]), tuple(sorted(s["relays"].items())),
            tuple(sorted(s["inputs"].items())), tuple(s["boxes"]), s["shed"], style.color, style.opacity,
            style.position, self.rt.config.language, style.show_arrows, style.show_zoom, style.show_iris, style.show_focus,
            style.show_events, style.event_position, getattr(style, "show_io", True), self.rt.config.osd.enabled,
        )

    # -- drawing -------------------------------------------------------------
    def frame(self) -> bytes:
        now = time.monotonic()
        s = self._collect(now)
        sig = self.signature(s)
        if sig == self._sig:
            return self._bytes
        self._sig = sig
        self._bytes = self._draw(s).tobytes()
        return self._bytes

    def _draw(self, s: dict[str, Any]) -> Canvas:
        cv = Canvas(self.w, self.h)
        osd = self.rt.config.osd
        if not osd.enabled:
            return cv
        st = osd.style
        u = self.h / 360.0
        accent = parse_color(st.color, int(255 * max(0.2, st.opacity)))
        pos = st.position if st.position != "center" else "bottom-left"
        m = int(12 * u)
        hud_right = pos.endswith("right")
        hud_bottom = pos.startswith("bottom")

        # ---- named-command banner (SET/GOTO/DEL PRESET, HOME, AUX...) - top-center toast
        if s["label"]:
            sc = max(1, int(2.1 * u))
            text = s["label"]
            tw = cv.text_width(text, sc) + 16 * sc
            th = 13 * sc
            bx = (self.w - tw) // 2
            by = m
            cv.rect(bx, by, tw, th, (0, 0, 0, 190))
            cv.frame(bx, by, tw, th, accent[:3] + (255,), max(1, int(1.5 * u)))
            cv.text(bx + 8 * sc, by + 3 * sc, text, sc, accent[:3] + (255,))

        # ---- PTZ HUD (only while a command is active or just finished)
        show_hud = s["ptz_on"] and (st.show_arrows or st.show_zoom or st.show_iris or st.show_focus) and bool(s["lit"])
        hud_w = hud_h = 0
        if show_hud:
            hud_w, hud_h = self._hud(cv, s, accent, u, m, hud_right, hud_bottom)

        # ---- event badges: opposite vertical side of the HUD, same horizontal side as the HUD's opposite
        if st.show_events and s["badges"]:
            self._badges(cv, s["badges"], u, m, st.event_position)

        # ---- I/O LEDs
        if getattr(st, "show_io", True) and (s["inputs"] or s["relays"]):
            self._io(cv, s, u, m, right=hud_right, bottom=not hud_bottom)

        # ---- analytics boxes
        for (tid, klass, conf, left, t, r, b) in s["boxes"]:
            color = BOX_COLORS[tid % len(BOX_COLORS)] + (235,)
            x0, y0 = int(left * self.w), int(t * self.h)
            x1, y1 = int(r * self.w), int(b * self.h)
            cv.frame(x0, y0, max(2, x1 - x0), max(2, y1 - y0), color, max(1, int(2 * u)))
            if self.rt.config.language == "ru":
                klass = CLASS_LABELS_RU.get(klass, klass)
            label = f"{klass} {int(conf * 100)}% #{tid}"
            sc = max(1, int(1.5 * u))
            tw = cv.text_width(label, sc) + 6 * sc
            ly = max(0, y0 - 9 * sc)
            cv.rect(x0, ly, tw, 9 * sc, color[:3] + (215,))
            cv.text(x0 + 3 * sc, ly + sc, label, sc, (0, 0, 0, 255))

        if s["shed"]:
            sc = max(1, int(1.5 * u))
            msg = f"LOAD SHED L{s['shed']}"
            cv.rect(m, self.h - m - 9 * sc - (0 if hud_bottom and not hud_right else 0), cv.text_width(msg, sc) + 6 * sc, 9 * sc,
                    (255, 120, 0, 220))
            cv.text(m + 3 * sc, self.h - m - 8 * sc, msg, sc, (0, 0, 0, 255))
        return cv

    def _btn(self, cv: Canvas, x: int, y: int, w: int, h: int, lit: bool, accent: Color, u: float, glyph: str) -> None:
        cv.rect(x, y, w, h, accent[:3] + (235,) if lit else (0, 0, 0, 110))
        cv.frame(x, y, w, h, accent[:3] + (255,) if lit else accent[:3] + (110,), max(1, int(u)))
        fg = (0, 0, 0, 255) if lit else accent[:3] + (150,)
        cx, cy = x + w // 2, y + h // 2
        r = min(w, h) // 3
        if glyph == "up":
            cv.triangle([(cx, cy - r), (cx - r, cy + r), (cx + r, cy + r)], fg)
        elif glyph == "down":
            cv.triangle([(cx, cy + r), (cx - r, cy - r), (cx + r, cy - r)], fg)
        elif glyph == "left":
            cv.triangle([(cx - r, cy), (cx + r, cy - r), (cx + r, cy + r)], fg)
        elif glyph == "right":
            cv.triangle([(cx + r, cy), (cx - r, cy - r), (cx - r, cy + r)], fg)
        elif glyph == "stop":
            cv.rect(cx - r + 1, cy - r + 1, 2 * r - 1, 2 * r - 1, fg)
        else:
            sc = max(1, int(1.4 * u))
            cv.text(cx - cv.text_width(glyph, sc) // 2, cy - 3 * sc, glyph, sc, fg)

    def _hud(self, cv: Canvas, s: dict[str, Any], accent: Color, u: float, m: int, right: bool, bottom: bool) -> tuple[int, int]:
        st = self.rt.config.osd.style
        cell = int(30 * u)
        gap = int(3 * u)
        lit = s["lit"]
        pad_w = cell * 3 + gap * 2
        cols = 3
        side_w = cols * cell + (cols - 1) * gap
        total_w = pad_w + int(14 * u) + side_w
        total_h = cell * 3 + gap * 2 + int(14 * u)
        x0 = self.w - m - total_w if right else m
        y0 = self.h - m - total_h if bottom else m
        # dim backdrop
        cv.rect(x0 - gap, y0 - gap, total_w + 2 * gap, total_h + 2 * gap, (0, 0, 0, 70))
        if st.show_arrows:
            for name, gx, gy, glyph in (("up", 1, 0, "up"), ("left", 0, 1, "left"), ("stop", 1, 1, "stop"),
                                        ("right", 2, 1, "right"), ("down", 1, 2, "down")):
                self._btn(cv, x0 + gx * (cell + gap), y0 + gy * (cell + gap), cell, cell, name in lit, accent, u, glyph)
        sx = x0 + pad_w + int(14 * u)
        for c, (show, plus, minus, label) in enumerate(((st.show_zoom, "zp", "zm", "Z"), (st.show_iris, "ip", "im", "I"),
                                                        (st.show_focus, "fp", "fm", "F"))):
            if not show:
                continue
            cx = sx + c * (cell + gap)
            self._btn(cv, cx, y0 + cell // 2, cell, cell, plus in lit, accent, u, label + "+")
            self._btn(cv, cx, y0 + cell // 2 + cell + gap, cell, cell, minus in lit, accent, u, label + "-")
        if s["pos"]:
            sc = max(1, int(1.3 * u))
            cv.text(x0, y0 + cell * 3 + gap * 2 + int(4 * u), s["pos"], sc, accent[:3] + (235,))
        return total_w, total_h

    def _badges(self, cv: Canvas, badges: list, u: float, m: int, position: str) -> None:
        sc = max(1, int(2 * u))
        seen: set[str] = set()
        unique = []
        for text, kind in badges[-8:]:
            if text in seen:
                continue
            seen.add(text)
            unique.append((text, kind))
        right = position.endswith("right")
        bottom = position.startswith("bottom")
        middle = position.startswith("middle") or position == "center"
        heights = [11 * sc for _ in unique]
        gap = 2 * sc
        total_h = sum(heights) + max(0, len(heights) - 1) * gap
        y = (self.h - total_h) // 2 if middle else (self.h - m - total_h if bottom else m)
        for text, kind in unique:
            color = BADGE_COLORS.get(text) or BADGE_COLORS.get(kind) or BADGE_COLORS["event"]
            tw = cv.text_width(text, sc) + 8 * sc
            center = position in {"top-center", "center", "bottom-center"}
            x = (self.w - tw) // 2 if center else (self.w - m - tw if right else m)
            cv.rect(x, y, tw, 11 * sc, color + (225,))
            cv.text(x + 4 * sc, y + 2 * sc, text, sc, (0, 0, 0, 255))
            y += 11 * sc + gap

    def _io(self, cv: Canvas, s: dict[str, Any], u: float, m: int, right: bool, bottom: bool) -> None:
        sc = max(1, int(1.3 * u))
        r = max(3, int(5 * u))
        input_prefix = "ВХ" if self.rt.config.language == "ru" else "IN"
        items = [(f"{input_prefix}{k.rsplit('_', 1)[-1]}", v) for k, v in sorted(s["inputs"].items())]
        items += [(f"R{k.rsplit('_', 1)[-1]}", v) for k, v in sorted(s["relays"].items())]
        width = sum(cv.text_width(n, sc) + 2 * r + 12 * sc for n, _ in items)
        x = self.w - m - width if right else m
        y = self.h - m - 2 * r - 4 * sc if bottom else m
        cv.rect(x - 3 * sc, y - 3 * sc, width + 3 * sc, 2 * r + 8 * sc, (0, 0, 0, 90))
        for name, on in items:
            cv.circle(x + r, y + r, r, (255, 50, 50, 255) if on else (40, 90, 50, 255))
            cv.text(x + 2 * r + 3 * sc, y + r - 3 * sc, name, sc, (255, 255, 255, 235))
            x += cv.text_width(name, sc) + 2 * r + 12 * sc


class OverlayFeeder:
    """Pushes overlay frames to the stdin of one or more FFmpeg processes (main / medium / low)."""

    def __init__(self, renderer: OverlayRenderer):
        self.renderer = renderer
        self.writers: list[asyncio.StreamWriter] = []
        self._task: asyncio.Task | None = None

    def add(self, writer: asyncio.StreamWriter | None) -> None:
        if writer is not None:
            self.writers.append(writer)

    def start(self) -> None:
        self._task = asyncio.get_running_loop().create_task(self._run(), name=f"overlay-{self.renderer.ch}")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        for w in self.writers:
            try:
                w.close()
            except Exception:  # noqa: BLE001
                pass
        self.writers.clear()

    async def _run(self) -> None:
        period = 1.0 / FPS
        nxt = time.monotonic()
        while self.writers:
            try:
                data = self.renderer.frame()
            except Exception:  # noqa: BLE001 - drawing must never take the stream down
                log.exception("overlay draw failed")
                data = bytes(self.renderer.w * self.renderer.h * 4)
            for w in list(self.writers):
                try:
                    w.write(data)
                    await asyncio.wait_for(w.drain(), timeout=1.0)
                except (ConnectionError, BrokenPipeError, OSError, TimeoutError):
                    self.writers.remove(w)
            nxt += period
            await asyncio.sleep(max(0.0, nxt - time.monotonic()))
            if nxt < time.monotonic() - 1.0:
                nxt = time.monotonic()


def canvas_size(width: int, height: int) -> tuple[int, int]:
    h = int(CANVAS_W * height / max(1, width)) // 2 * 2
    return CANVAS_W, max(180, min(640, h))
