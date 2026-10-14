"""Bounded ONVIF event bus.

Each PullPoint subscription owns a deque(maxlen=N) — O(1) enqueue, oldest dropped.
Topics are interned strings. Fan-out is O(subscribers), typically < 10 in ODM.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from html import escape as _html_escape
from typing import Any

from onvif_emu.timeutil import isoformat_z, utc_now
from onvif_emu.util import new_uuid

log = logging.getLogger(__name__)

_STATE_ITEMS = ("State", "LogicalState", "IsInside", "IsMotion")
MAX_MESSAGES = 128


def escape(text: str) -> str:
    return _html_escape(text, quote=True)

MAX_SUBSCRIPTIONS = 64

MOTION = "tns1:VideoSource/MotionAlarm"
TAMPER = "tns1:VideoSource/Tampering"
FIELD = "tns1:RuleEngine/FieldDetector/ObjectsInside"
DINPUT = "tns1:Device/Trigger/DigitalInput"


def default_source_for(topic: str) -> dict[str, str]:
    if topic == DINPUT or topic.endswith("/DigitalInput"):
        return {"InputToken": "di_1"}
    return {"VideoSourceConfigurationToken": "vs_1"}


@dataclass(slots=True)
class OnvifEvent:
    topic: str
    utc: datetime
    data: dict[str, Any]
    source: dict[str, str] = field(default_factory=dict)
    operation: str = "Changed"
    property: str = "State"
    is_property: bool = True  # False: plain event (no PropertyOperation), e.g. line crossed
    channel: int = 0
    rule: str = ""
    push_url: str = ""

    def to_xml(self, prefix: str = "tev") -> str:
        ts = isoformat_z(self.utc)
        prop_attr = f' PropertyOperation="{self.operation}"' if self.is_property else ""
        data_xml = "".join(
            f'<tt:SimpleItem Name="{escape(str(k))}" Value="{escape(str(v))}"/>' for k, v in self.data.items()
        )
        src_xml = "".join(
            f'<tt:SimpleItem Name="{escape(str(k))}" Value="{escape(str(v))}"/>' for k, v in self.source.items()
        )
        return (
            f"<wsnt:NotificationMessage>"
            f"<wsnt:Topic Dialect=\"http://www.onvif.org/ver10/tev/topicExpression/ConcreteSet\">{self.topic}</wsnt:Topic>"
            f"<wsnt:Message>"
            f"<tt:Message UtcTime=\"{ts}\"{prop_attr}>"
            f"<tt:Source>{src_xml}</tt:Source>"
            f"<tt:Data>{data_xml}</tt:Data>"
            f"</tt:Message></wsnt:Message></wsnt:NotificationMessage>"
        )


@dataclass
class Subscription:
    token: str
    created: datetime
    expires_at: datetime
    sync_point: datetime | None = None
    messages: deque[OnvifEvent] = field(default_factory=lambda: deque(maxlen=MAX_MESSAGES))
    topics: list[str] = field(default_factory=list)
    _wake: threading.Event = field(default_factory=threading.Event)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _closed: bool = False

    def close(self) -> None:
        self._closed = True
        self._wake.set()

    def alive(self, now: datetime | None = None) -> bool:
        n = now or utc_now()
        return not self._closed and n <= self.expires_at

    def pull(self, max_items: int) -> list[OnvifEvent]:
        n = max(0, max_items)
        out: list[OnvifEvent] = []
        with self._lock:
            while self.messages and len(out) < n:
                out.append(self.messages.popleft())
        return out

    def pull_wait(self, max_items: int, timeout_s: float) -> list[OnvifEvent]:
        """Block up to timeout_s for at least one message (ONVIF PullMessages)."""
        deadline = time.monotonic() + min(30.0, max(0.0, timeout_s))
        while True:
            # Clear before checking so a publish between clear and pull still wakes us.
            self._wake.clear()
            if not self.alive() or max_items <= 0:
                return []
            out = self.pull(max_items)
            if out:
                return out
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return []
            self._wake.wait(timeout=min(0.25, remaining))


class EventBus:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._subs: dict[str, Subscription] = {}
        self._listeners: list = []
        # last property event per (topic, source items): what a synchronisation point replays
        self.last: dict[tuple, OnvifEvent] = {}
        self.current: dict[str, Any] = {
            MOTION: False,
            TAMPER: False,
            FIELD: False,
            DINPUT: False,
        }

    def create(self, timeout_s: float = 60.0, topics: list[str] | None = None) -> Subscription:
        with self._lock:
            self._gc()
            if len(self._subs) >= MAX_SUBSCRIPTIONS:
                oldest = min(self._subs.values(), key=lambda s: s.created)
                self._subs.pop(oldest.token, None)
                oldest.close()
            now = utc_now()
            from datetime import timedelta

            sub = Subscription(
                token=new_uuid(),
                created=now,
                expires_at=now + timedelta(seconds=max(5.0, timeout_s)),
                topics=topics or [],
            )
            self._subs[sub.token] = sub
            return sub

    def get(self, token: str) -> Subscription | None:
        with self._lock:
            sub = self._subs.get(token)
            if sub and sub.alive():
                return sub
            if sub:
                self._subs.pop(token, None)
                sub.close()
            return None

    def renew(self, token: str, timeout_s: float = 60.0) -> Subscription | None:
        with self._lock:
            sub = self.get(token)
            if not sub:
                return None
            from datetime import timedelta

            sub.expires_at = utc_now() + timedelta(seconds=max(5.0, timeout_s))
            return sub

    def unsubscribe(self, token: str) -> bool:
        with self._lock:
            sub = self._subs.pop(token, None)
            if sub:
                sub.close()
            return sub is not None

    def add_listener(self, fn) -> None:
        """fn(event) is called for every published event (from the publishing thread)."""
        self._listeners.append(fn)

    @staticmethod
    def state_key(event: OnvifEvent) -> tuple:
        return (event.topic, tuple(sorted(event.source.items())))

    def last_state(self, key: tuple) -> bool:
        ev = self.last.get(key)
        if ev is None:
            return False
        for name in _STATE_ITEMS:
            if name in ev.data:
                return str(ev.data[name]).lower() in {"true", "1"}
        return False

    def publish(self, event: OnvifEvent) -> None:
        with self._lock:
            for name in _STATE_ITEMS:
                if name in event.data:
                    self.current[event.topic] = str(event.data[name]).lower() in {"true", "1"}
                    break
            if event.is_property:
                self.last[self.state_key(event)] = event
            self._gc()
            delivered = 0
            for sub in self._subs.values():
                if sub.topics and event.topic not in sub.topics:
                    continue
                sub.messages.append(event)
                sub._wake.set()
                delivered += 1
        log.debug("Event publish topic=%s data=%s subs=%s", event.topic, event.data, delivered)
        for fn in list(self._listeners):
            try:
                fn(event)
            except Exception:  # noqa: BLE001 - a bad listener must not break event delivery
                log.exception("event listener failed")

    def trigger(self, topic: str, state: bool, source: dict[str, str] | None = None) -> OnvifEvent:
        ev = OnvifEvent(
            topic=topic,
            utc=utc_now(),
            data={"State": "true" if state else "false"},
            source=source or default_source_for(topic),
        )
        self.publish(ev)
        return ev

    def set_sync(self, token: str) -> None:
        with self._lock:
            sub = self.get(token)
            if sub:
                sub.sync_point = utc_now()
                # Initial property states as messages: real ones first, then the legacy defaults
                seen = {t for (t, _src) in self.last}
                for ev in list(self.last.values()):
                    if sub.topics and ev.topic not in sub.topics:
                        continue
                    sub.messages.append(
                        OnvifEvent(
                            topic=ev.topic, utc=utc_now(), data=dict(ev.data), source=dict(ev.source),
                            operation="Initialized", channel=ev.channel, rule=ev.rule,
                        )
                    )
                for topic, st in self.current.items():
                    if sub.topics and topic not in sub.topics:
                        continue
                    if topic in seen:
                        continue
                    sub.messages.append(
                        OnvifEvent(
                            topic=topic,
                            utc=utc_now(),
                            data={"State": "true" if st else "false"},
                            operation="Initialized",
                            source=default_source_for(topic),
                        )
                    )
                sub._wake.set()

    def _gc(self) -> None:
        dead = [t for t, s in self._subs.items() if not s.alive()]
        for t in dead:
            self._subs.pop(t).close()

    def count(self) -> int:
        with self._lock:
            self._gc()
            return len(self._subs)
