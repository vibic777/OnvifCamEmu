"""Billiard-style analytics object simulator.

Each object reflects off frame edges with energy-preserving velocity inversion.
Positions stay in [0,1]; boxes never invert (left<right, top<bottom).
O(objects) per tick, objects << 32.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field

from onvif_emu.models import AnalyticsConfig
from onvif_emu.timeutil import clamp

CLASSES = ("Person", "Vehicle", "Animal", "Unknown")


@dataclass(slots=True)
class TrackedObject:
    track_id: int
    klass: str
    confidence: float
    x: float
    y: float
    w: float
    h: float
    vx: float
    vy: float
    expires: float = 0.0  # monotonic deadline for injected objects, 0 = lives forever

    def bbox(self) -> dict[str, float]:
        left = clamp(self.x, 0.0, 1.0)
        top = clamp(self.y, 0.0, 1.0)
        right = clamp(self.x + self.w, 0.0, 1.0)
        bottom = clamp(self.y + self.h, 0.0, 1.0)
        if right < left:
            left, right = right, left
        if bottom < top:
            top, bottom = bottom, top
        return {"left": left, "top": top, "right": right, "bottom": bottom}


@dataclass
class AnalyticsSimulator:
    cfg: AnalyticsConfig
    objects: list[TrackedObject] = field(default_factory=list)
    _next_id: int = 1
    _rng: random.Random = field(default_factory=random.Random)

    def seed(self, seed: int | None = None) -> None:
        self._rng = random.Random(seed)
        self.objects.clear()
        self._next_id = 1
        n = max(0, min(self.cfg.max_objects, 8 if self.cfg.max_objects else 0))
        for _ in range(n):
            self.objects.append(self._spawn())

    def _spawn(self) -> TrackedObject:
        klass = self._rng.choice(self.cfg.object_classes or list(CLASSES))
        w = self._rng.uniform(0.08, 0.22)
        h = self._rng.uniform(0.12, 0.35)
        speed = max(0.01, self.cfg.box_speed)
        ang_x = self._rng.choice([-1.0, 1.0]) * speed * self._rng.uniform(0.4, 1.0)
        ang_y = self._rng.choice([-1.0, 1.0]) * speed * self._rng.uniform(0.4, 1.0)
        obj = TrackedObject(
            track_id=self._next_id,
            klass=klass,
            confidence=round(self._rng.uniform(0.55, 0.99), 2),
            x=self._rng.uniform(0.0, 1.0 - w),
            y=self._rng.uniform(0.0, 1.0 - h),
            w=w,
            h=h,
            vx=ang_x,
            vy=ang_y,
        )
        self._next_id += 1
        return obj

    def inject(self, *, count: int = 1, klass: str | None = None, ttl_s: float = 5.0, speed: float = 0.0) -> list[TrackedObject]:
        """Add synthetic objects on demand (diagnostics). They disappear after ttl_s."""
        made: list[TrackedObject] = []
        for _ in range(max(1, min(32, count))):
            obj = self._spawn()
            if klass:
                obj.klass = klass
            if speed:
                obj.vx = obj.vx / (abs(obj.vx) or 1.0) * speed
                obj.vy = obj.vy / (abs(obj.vy) or 1.0) * speed
            obj.expires = time.monotonic() + max(0.5, ttl_s)
            self.objects.append(obj)
            made.append(obj)
        return made

    def step(self, dt: float) -> None:
        dt = max(0.0, dt)
        if any(o.expires for o in self.objects):
            now = time.monotonic()
            self.objects = [o for o in self.objects if not o.expires or o.expires > now]
        if not self.objects and self.cfg.max_objects > 0:
            self.seed()
        for obj in self.objects:
            obj.x += obj.vx * dt
            obj.y += obj.vy * dt
            # Reflect: invert the component, clamp inside
            if obj.x < 0.0:
                obj.x = -obj.x
                obj.vx = abs(obj.vx)
            elif obj.x + obj.w > 1.0:
                obj.x = 2.0 * (1.0 - obj.w) - obj.x
                obj.vx = -abs(obj.vx)
            if obj.y < 0.0:
                obj.y = -obj.y
                obj.vy = abs(obj.vy)
            elif obj.y + obj.h > 1.0:
                obj.y = 2.0 * (1.0 - obj.h) - obj.y
                obj.vy = -abs(obj.vy)
            obj.x = clamp(obj.x, 0.0, max(0.0, 1.0 - obj.w))
            obj.y = clamp(obj.y, 0.0, max(0.0, 1.0 - obj.h))

    def frame_xml(self, utc_iso: str, source: str = "vs_1") -> str:
        parts = [
            f'<tt:Frame UtcTime="{utc_iso}">',
            f'<tt:Source>{source}</tt:Source>',
        ]
        for obj in self.objects:
            b = obj.bbox()
            parts.append(
                "<tt:Object ObjectId=\"{id}\">"
                "<tt:Appearance>"
                "<tt:Shape><tt:BoundingBox left=\"{l:.4f}\" top=\"{t:.4f}\" right=\"{r:.4f}\" bottom=\"{b:.4f}\"/>"
                "</tt:Shape>"
                "<tt:Class><tt:Type Likelihood=\"{c:.2f}\">{k}</tt:Type></tt:Class>"
                "</tt:Appearance></tt:Object>".format(
                    id=obj.track_id,
                    l=b["left"],
                    t=b["top"],
                    r=b["right"],
                    b=b["bottom"],
                    c=obj.confidence,
                    k=obj.klass,
                )
            )
        parts.append("</tt:Frame>")
        return "".join(parts)

    def as_dicts(self) -> list[dict[str, object]]:
        out = []
        for obj in self.objects:
            d = obj.bbox()
            d.update(
                {
                    "id": obj.track_id,
                    "class": obj.klass,
                    "confidence": obj.confidence,
                }
            )
            out.append(d)
        return out
