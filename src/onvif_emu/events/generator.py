"""Periodic / random / manual ONVIF event generation with stable hold times."""

from __future__ import annotations

import asyncio
import logging
import random
from typing import TYPE_CHECKING

from onvif_emu.models import EventMode

if TYPE_CHECKING:
    from onvif_emu.runtime import Runtime

log = logging.getLogger(__name__)

MOTION = "tns1:VideoSource/MotionAlarm"
TAMPER = "tns1:VideoSource/Tampering"
FIELD = "tns1:RuleEngine/FieldDetector/ObjectsInside"
DINPUT = "tns1:Device/Trigger/DigitalInput"


class EventGenerator:
    def __init__(self, runtime: Runtime):
        self.runtime = runtime
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    async def start(self) -> None:
        self._stop.clear()
        self._task = asyncio.create_task(self._loop(), name="events")

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass

    def trigger_motion(self, state: bool | None = None) -> None:
        cur = bool(self.runtime.events.current.get(MOTION))
        nxt = (not cur) if state is None else state
        log.info("Event motion -> %s", nxt)
        self.runtime.events.trigger(MOTION, nxt)

    def trigger_tamper(self, state: bool | None = None) -> None:
        cur = bool(self.runtime.events.current.get(TAMPER))
        nxt = (not cur) if state is None else state
        log.info("Event tamper -> %s", nxt)
        self.runtime.events.trigger(TAMPER, nxt)

    def trigger_analytics(self, state: bool | None = None) -> None:
        cur = bool(self.runtime.events.current.get(FIELD))
        nxt = (not cur) if state is None else state
        log.info("Event field -> %s", nxt)
        self.runtime.events.trigger(FIELD, nxt)

    def trigger_digital_input(self, state: bool | None = None) -> None:
        cur = bool(self.runtime.events.current.get(DINPUT))
        nxt = (not cur) if state is None else state
        log.info("Event digital_input -> %s", nxt)
        self.runtime.events.trigger(
            DINPUT,
            nxt,
            source={"InputToken": "di_1"},
        )

    async def _pulse(self, trigger, hold: float) -> float:
        trigger(True)
        await asyncio.sleep(hold)
        trigger(False)
        return hold

    async def _loop(self) -> None:
        def cfg():
            return self.runtime.config.events

        rng = random.Random()
        next_motion = 0.0
        next_tamper = 0.0
        next_field = 0.0
        next_di = 0.0
        elapsed = 0.0
        while not self._stop.is_set():
            ev = cfg()
            mode = ev.mode
            if mode == EventMode.timer:
                jitter = 0.25 if ev.randomize else 0.0
                if elapsed >= next_motion:
                    elapsed += await self._pulse(self.trigger_motion, ev.hold_s)
                    span = ev.motion_interval * (1 + rng.uniform(-jitter, jitter))
                    next_motion = elapsed + max(2.0, span)
                if elapsed >= next_tamper:
                    elapsed += await self._pulse(self.trigger_tamper, ev.hold_s)
                    span = ev.tamper_interval * (1 + rng.uniform(-jitter, jitter))
                    next_tamper = elapsed + max(5.0, span)
                if elapsed >= next_field:
                    elapsed += await self._pulse(self.trigger_analytics, ev.hold_s)
                    span = ev.analytics_interval * (1 + rng.uniform(-jitter, jitter))
                    next_field = elapsed + max(3.0, span)
                if ev.digital_input and elapsed >= next_di:
                    elapsed += await self._pulse(self.trigger_digital_input, ev.hold_s)
                    span = max(ev.motion_interval, 10.0) * (1 + rng.uniform(-jitter, jitter))
                    next_di = elapsed + max(5.0, span)
            elif mode == EventMode.motion:
                # Synthetic motion: timer pulses + optional Field when analytics objects move.
                jitter = 0.25 if ev.randomize else 0.0
                if elapsed >= next_motion:
                    elapsed += await self._pulse(self.trigger_motion, ev.hold_s)
                    span = ev.motion_interval * (1 + rng.uniform(-jitter, jitter))
                    next_motion = elapsed + max(2.0, span)
                # Mirror analytics presence into FieldDetector when simulator has objects.
                analytics = getattr(self.runtime, "analytics", None)
                objs = analytics.as_dicts() if analytics is not None else []
                want_field = bool(objs)
                cur_field = bool(self.runtime.events.current.get(FIELD))
                if want_field != cur_field and elapsed >= next_field:
                    self.trigger_analytics(want_field)
                    next_field = elapsed + max(1.0, ev.hold_s)
                if ev.digital_input and elapsed >= next_di:
                    elapsed += await self._pulse(self.trigger_digital_input, ev.hold_s)
                    span = max(ev.motion_interval * 2.0, 10.0) * (1 + rng.uniform(-jitter, jitter))
                    next_di = elapsed + max(5.0, span)
            # EventMode.manual: API / GUI triggers only
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=1.0)
                elapsed += 1.0
            except TimeoutError:
                elapsed += 1.0
