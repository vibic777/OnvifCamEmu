"""Virtual PTZ axis with time-optimal bounded motion.

Design goals (stability + optimality):
- Time-based integration (monotonic clock), independent of tick jitter.
- No overshoot of target or mechanical limits.
- Constant-velocity profile by default (ONVIF GetStatus is linear and testable).
- Optional acceleration (trapezoidal) for smoother visual motion.
- O(1) per axis per tick; 5 axes × 64 channels is trivial.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Literal

from onvif_emu.models import PtzConfig
from onvif_emu.timeutil import clamp, finite

MoveKind = Literal["idle", "absolute", "relative", "continuous", "preset"]


def _sign(x: float) -> float:
    if x > 0:
        return 1.0
    if x < 0:
        return -1.0
    return 0.0


@dataclass(slots=True)
class Axis:
    name: str
    value: float
    lo: float
    hi: float
    max_speed: float
    accel: float = 0.0
    velocity: float = 0.0
    target: float | None = None
    cmd_velocity: float | None = None

    def set_limits(self, lo: float, hi: float, max_speed: float, accel: float = 0.0) -> None:
        self.lo, self.hi = (lo, hi) if lo <= hi else (hi, lo)
        self.max_speed = max(0.0, max_speed)
        self.accel = max(0.0, accel)
        self.value = clamp(self.value, self.lo, self.hi)

    def stop(self) -> None:
        self.target = None
        self.cmd_velocity = None
        self.velocity = 0.0

    def goto(self, target: float) -> None:
        self.cmd_velocity = None
        self.target = clamp(finite(target, self.value), self.lo, self.hi)

    def continuous(self, normalized_velocity: float) -> None:
        """normalized_velocity in [-1, 1] maps to ±max_speed."""
        self.target = None
        nv = clamp(finite(normalized_velocity), -1.0, 1.0)
        self.cmd_velocity = nv * self.max_speed

    def step(self, dt: float, speed_scale: float = 1.0) -> bool:
        """Advance axis by dt seconds. Returns True if value changed."""
        dt = max(0.0, finite(dt))
        if dt == 0.0:
            return False
        old = self.value
        speed = max(0.0, self.max_speed * max(0.0, speed_scale))

        if self.cmd_velocity is not None:
            desired = clamp(self.cmd_velocity * max(0.0, speed_scale), -speed, speed)
            self._approach_velocity(desired, dt)
            self.value = clamp(self.value + self.velocity * dt, self.lo, self.hi)
            at_hi = self.value >= self.hi - 1e-12 and self.velocity > 0
            at_lo = self.value <= self.lo + 1e-12 and self.velocity < 0
            if at_hi or at_lo:
                self.velocity = 0.0
                self.value = self.hi if at_hi else self.lo
            return self.value != old

        if self.target is None:
            if self.velocity != 0.0:
                self._approach_velocity(0.0, dt)
                self.value = clamp(self.value + self.velocity * dt, self.lo, self.hi)
                if abs(self.velocity) < 1e-9:
                    self.velocity = 0.0
            return self.value != old

        target = self.target
        remaining = target - self.value
        if remaining == 0.0:
            self.velocity = 0.0
            self.target = None
            return False

        if self.accel <= 0.0 or speed <= 0.0:
            # Exact constant-velocity: guaranteed no overshoot, dt-stable.
            max_delta = speed * dt
            if abs(remaining) <= max_delta:
                self.value = target
                self.velocity = 0.0
                self.target = None
            else:
                self.velocity = _sign(remaining) * speed
                self.value = clamp(self.value + self.velocity * dt, self.lo, self.hi)
            return self.value != old

        # Trapezoidal: decelerate when remaining <= v^2 / (2a)
        direction = _sign(remaining)
        v = self.velocity
        stop_dist = (v * v) / (2.0 * self.accel) if self.accel else 0.0
        approaching = direction * v >= 0.0
        if approaching and abs(remaining) <= stop_dist:
            desired = 0.0
        else:
            desired = direction * speed
        self._approach_velocity(desired, dt)
        self.value += self.velocity * dt
        # Prevent overshoot past target
        if direction > 0 and self.value >= target:
            self.value = target
            self.velocity = 0.0
            self.target = None
        elif direction < 0 and self.value <= target:
            self.value = target
            self.velocity = 0.0
            self.target = None
        self.value = clamp(self.value, self.lo, self.hi)
        return self.value != old

    def _approach_velocity(self, desired: float, dt: float) -> None:
        if self.accel <= 0.0:
            self.velocity = desired
            return
        delta = desired - self.velocity
        max_dv = self.accel * dt
        if abs(delta) <= max_dv:
            self.velocity = desired
        else:
            self.velocity += _sign(delta) * max_dv


@dataclass
class PtzStatus:
    pan: float
    tilt: float
    zoom: float
    iris: float
    focus: float
    moving: bool
    kind: MoveKind
    pan_velocity: float = 0.0
    tilt_velocity: float = 0.0
    zoom_velocity: float = 0.0


@dataclass
class PtzServo:
    channel_id: int
    cfg: PtzConfig
    pan: Axis = field(init=False)
    tilt: Axis = field(init=False)
    zoom: Axis = field(init=False)
    iris: Axis = field(init=False)
    focus: Axis = field(init=False)
    kind: MoveKind = "idle"
    last_command: str = "idle"
    speed_pan: float = 1.0
    speed_tilt: float = 1.0
    speed_zoom: float = 1.0
    osd_pan: int = 0  # -1 left, 1 right
    osd_tilt: int = 0
    osd_zoom: int = 0
    osd_iris: int = 0
    osd_focus: int = 0
    osd_until: float = 0.0
    osd_label: str = ""
    osd_label_until: float = 0.0

    def __post_init__(self) -> None:
        c = self.cfg
        self.pan = Axis("pan", 0.0, c.pan_min, c.pan_max, c.pan_speed)
        self.tilt = Axis("tilt", 0.0, c.tilt_min, c.tilt_max, c.tilt_speed)
        self.zoom = Axis("zoom", 0.0, c.zoom_min, c.zoom_max, c.zoom_speed)
        self.iris = Axis("iris", 0.5, c.iris_min, c.iris_max, 0.4)
        self.focus = Axis("focus", 0.5, c.focus_min, c.focus_max, 0.4)

    def apply_config(self, cfg: PtzConfig) -> None:
        self.cfg = cfg
        self.pan.set_limits(cfg.pan_min, cfg.pan_max, cfg.pan_speed)
        self.tilt.set_limits(cfg.tilt_min, cfg.tilt_max, cfg.tilt_speed)
        self.zoom.set_limits(cfg.zoom_min, cfg.zoom_max, cfg.zoom_speed)
        self.iris.set_limits(cfg.iris_min, cfg.iris_max, 0.4)
        self.focus.set_limits(cfg.focus_min, cfg.focus_max, 0.4)

    def restore(self, pan: float, tilt: float, zoom: float, iris: float = 0.5, focus: float = 0.5) -> None:
        self.pan.value = clamp(pan, self.pan.lo, self.pan.hi)
        self.tilt.value = clamp(tilt, self.tilt.lo, self.tilt.hi)
        self.zoom.value = clamp(zoom, self.zoom.lo, self.zoom.hi)
        self.iris.value = clamp(iris, self.iris.lo, self.iris.hi)
        self.focus.value = clamp(focus, self.focus.lo, self.focus.hi)

    def status(self) -> PtzStatus:
        moving = any(
            ax.target is not None or ax.cmd_velocity or ax.velocity
            for ax in (self.pan, self.tilt, self.zoom)
        )
        return PtzStatus(
            pan=self.pan.value,
            tilt=self.tilt.value,
            zoom=self.zoom.value,
            iris=self.iris.value,
            focus=self.focus.value,
            moving=bool(moving),
            kind=self.kind,
            pan_velocity=self.pan.velocity,
            tilt_velocity=self.tilt.velocity,
            zoom_velocity=self.zoom.velocity,
        )

    def snapshot_dict(self) -> dict[str, float]:
        return {
            "pan": self.pan.value,
            "tilt": self.tilt.value,
            "zoom": self.zoom.value,
            "iris": self.iris.value,
            "focus": self.focus.value,
        }

    def absolute_move(
        self,
        pan: float | None,
        tilt: float | None,
        zoom: float | None,
        pan_speed: float = 1.0,
        tilt_speed: float = 1.0,
        zoom_speed: float = 1.0,
        now: float = 0.0,
    ) -> None:
        self.kind = "absolute"
        self.last_command = "AbsoluteMove"
        self.speed_pan = clamp(finite(pan_speed, 1.0), 0.0, 1.0)
        self.speed_tilt = clamp(finite(tilt_speed, 1.0), 0.0, 1.0)
        self.speed_zoom = clamp(finite(zoom_speed, 1.0), 0.0, 1.0)
        if pan is not None:
            self.pan.goto(pan)
            self.osd_pan = 1 if pan > self.pan.value else (-1 if pan < self.pan.value else 0)
        if tilt is not None:
            self.tilt.goto(tilt)
            self.osd_tilt = 1 if tilt > self.tilt.value else (-1 if tilt < self.tilt.value else 0)
        if zoom is not None:
            self.zoom.goto(zoom)
            self.osd_zoom = 1 if zoom > self.zoom.value else (-1 if zoom < self.zoom.value else 0)
        self.osd_until = now + 1.5

    def relative_move(
        self,
        dpan: float = 0.0,
        dtilt: float = 0.0,
        dzoom: float = 0.0,
        pan_speed: float = 1.0,
        tilt_speed: float = 1.0,
        zoom_speed: float = 1.0,
        now: float = 0.0,
    ) -> None:
        self.kind = "relative"
        self.last_command = "RelativeMove"
        self.absolute_move(
            self.pan.value + dpan if dpan else None,
            self.tilt.value + dtilt if dtilt else None,
            self.zoom.value + dzoom if dzoom else None,
            pan_speed,
            tilt_speed,
            zoom_speed,
            now,
        )
        self.kind = "relative"
        self.last_command = "RelativeMove"

    def continuous_move(
        self,
        vpan: float = 0.0,
        vtilt: float = 0.0,
        vzoom: float = 0.0,
        now: float = 0.0,
    ) -> None:
        self.kind = "continuous"
        self.last_command = "ContinuousMove"
        self.pan.continuous(vpan)
        self.tilt.continuous(vtilt)
        self.zoom.continuous(vzoom)
        self.osd_pan = int(_sign(vpan))
        self.osd_tilt = int(_sign(vtilt))
        self.osd_zoom = int(_sign(vzoom))
        self.osd_until = now + 1.5

    def stop(self, pan_tilt: bool = True, zoom: bool = True, now: float = 0.0) -> None:
        self.last_command = "Stop"
        if pan_tilt:
            self.pan.stop()
            self.tilt.stop()
            self.osd_pan = 0
            self.osd_tilt = 0
        if zoom:
            self.zoom.stop()
            self.osd_zoom = 0
        if not self.pan.cmd_velocity and not self.tilt.cmd_velocity and self.pan.target is None:
            self.kind = "idle"
        self.osd_until = now + 0.8

    def flash(self, label: str, now: float = 0.0, duration: float = 3.0) -> None:
        """Show a named-command banner (preset / home / aux) on the OSD for a few seconds."""
        self.osd_label = label
        self.osd_label_until = now + duration

    def nudge_iris(self, delta: float, now: float = 0.0) -> None:
        self.iris.goto(self.iris.value + delta)
        self.osd_iris = int(_sign(delta))
        self.osd_until = now + 1.5

    def nudge_focus(self, delta: float, now: float = 0.0) -> None:
        self.focus.goto(self.focus.value + delta)
        self.osd_focus = int(_sign(delta))
        self.osd_until = now + 1.5

    def step(self, dt: float, now: float = 0.0) -> bool:
        changed = False
        changed |= self.pan.step(dt, self.speed_pan)
        changed |= self.tilt.step(dt, self.speed_tilt)
        changed |= self.zoom.step(dt, self.speed_zoom)
        changed |= self.iris.step(dt)
        changed |= self.focus.step(dt)
        if self.kind in {"absolute", "relative", "preset"}:
            if self.pan.target is None and self.tilt.target is None and self.zoom.target is None:
                self.kind = "idle"
                self.osd_pan = self.osd_tilt = self.osd_zoom = 0
        if now > self.osd_until and self.kind != "continuous":
            self.osd_pan = self.osd_tilt = self.osd_zoom = 0
            self.osd_iris = self.osd_focus = 0
        return changed

    def osd_flags(self, now: float) -> dict[str, int]:
        active = now <= self.osd_until or self.kind == "continuous"
        if not active:
            return {"pan": 0, "tilt": 0, "zoom": 0, "iris": 0, "focus": 0, "stop": 1 if self.last_command == "Stop" else 0}
        return {
            "pan": self.osd_pan,
            "tilt": self.osd_tilt,
            "zoom": self.osd_zoom,
            "iris": self.osd_iris,
            "focus": self.osd_focus,
            "stop": 1 if self.last_command == "Stop" else 0,
        }


def simulate_until(servo: PtzServo, duration: float, dt: float = 0.05) -> PtzStatus:
    """Deterministic offline integration for tests and doctor checks."""
    t = 0.0
    steps = max(1, int(math.ceil(duration / dt)))
    for _ in range(steps):
        servo.step(dt, now=t)
        t += dt
    return servo.status()
