"""Diagnostic event engine: rules with timers, manual firing, relays, dry contacts, push targets.

Every rule is an independent source of ONVIF events:

* schedule ``manual``   - fired only by hand (GUI button, ``POST /api/diag/fire``);
* schedule ``interval`` - every ``interval`` <unit>;
* schedule ``random``   - a new random delay in [range_min, range_max] <unit> before every firing.

Units are seconds / minutes / hours, so "5..200 s" and "1..100 min" are both just settings.
A firing produces ONVIF events (PullPoint queues, WS-BaseNotification consumers, webhooks),
drives the simulated relay outputs / dry-contact inputs and paints the video overlay.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from collections import deque
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from onvif_emu.events.bus import OnvifEvent
from onvif_emu.models import UNIT_SECONDS, EventRule, RuleKind, Schedule
from onvif_emu.timeutil import isoformat_z, utc_now

if TYPE_CHECKING:
    from onvif_emu.runtime import Runtime

log = logging.getLogger(__name__)

T_MOTION = "tns1:VideoSource/MotionAlarm"
T_TAMPER = "tns1:VideoSource/Tampering"
T_FIELD = "tns1:RuleEngine/FieldDetector/ObjectsInside"
T_LINE = "tns1:RuleEngine/LineDetector/Crossed"
T_CELL = "tns1:RuleEngine/CellMotionDetector/Motion"
T_REGION = "tns1:RuleEngine/MotionRegionDetector/Motion"
T_COUNT = "tns1:RuleEngine/CountAggregation/Counter"
T_LOITER = "tns1:RuleEngine/LoiteringDetector/ObjectIsLoitering"
T_RELAY = "tns1:Device/Trigger/Relay"
T_INPUT = "tns1:Device/Trigger/DigitalInput"

# topic -> (is_property, data item name, source item names)
_VS = ("VideoSourceConfigurationToken",)
_RULE = ("VideoSourceConfigurationToken", "VideoAnalyticsConfigurationToken", "Rule")
TOPIC_SPEC: dict[str, tuple[bool, str, tuple[str, ...]]] = {
    T_MOTION: (True, "State", _VS),
    T_TAMPER: (True, "State", _VS),
    T_FIELD: (True, "IsInside", _RULE),
    T_CELL: (True, "IsMotion", _RULE),
    T_LINE: (False, "ObjectId", _RULE),
    T_REGION: (True, "State", _RULE),
    T_COUNT: (True, "Count", _RULE),
    T_LOITER: (True, "IsLoitering", _RULE),
    T_RELAY: (True, "LogicalState", ("RelayToken",)),
    T_INPUT: (True, "LogicalState", ("InputToken",)),
}

# analytics kind (EventRule.topic) -> ONVIF topic; the GUI offers the first name of each
ANALYTICS_TOPICS = {
    "line": T_LINE, "linecross": T_LINE,
    "field": T_FIELD, "intrusion": T_FIELD,
    "cellmotion": T_CELL, "cell": T_CELL,
    "motionregion": T_REGION, "region": T_REGION,
    "counting": T_COUNT, "count": T_COUNT,
    "loitering": T_LOITER,
}
# default <tt:Source Rule="..."> per analytics topic when the rule has no name
RULE_NAMES = {T_LINE: "LineCross", T_FIELD: "Intrusion", T_CELL: "MotionAlarm", T_REGION: "MotionRegion",
              T_COUNT: "Counting", T_LOITER: "Loitering"}


def relay_token(n: int) -> str:
    return f"relay_{n}"


def input_token(n: int) -> str:
    return f"di_{n}"


def render_value(raw: str, *, rng: random.Random, counter: int, channel: int) -> str:
    """Expand value templates: rand:a-b, randf:a-b, choice:a|b|c, seq, now, uuid, channel."""
    text = str(raw)
    try:
        if text.startswith("rand:"):
            lo, hi = text[5:].split("-", 1)
            return str(rng.randint(int(lo), int(hi)))
        if text.startswith("randf:"):
            lo, hi = text[6:].split("-", 1)
            return f"{rng.uniform(float(lo), float(hi)):.3f}"
        if text.startswith("choice:"):
            return rng.choice(text[7:].split("|"))
    except (ValueError, IndexError):
        return text
    if text == "seq":
        return str(counter)
    if text == "now":
        return isoformat_z()
    if text == "uuid":
        from onvif_emu.util import new_uuid

        return new_uuid()
    if text == "channel":
        return str(channel)
    return text


def rule_delay_s(rule: EventRule, rng: random.Random) -> float:
    """Seconds until the next firing for interval/random schedules."""
    scale = UNIT_SECONDS[rule.unit.value]
    if rule.schedule == Schedule.random:
        return max(0.05, rng.uniform(rule.range_min, rule.range_max) * scale)
    return max(0.05, rule.interval * scale)


@dataclass
class RuleRuntime:
    rule: EventRule
    task: asyncio.Task | None = None
    fired: int = 0
    next_at: float = 0.0  # monotonic; 0 = not scheduled
    last_at: float = 0.0
    last_error: str = ""


@dataclass
class RelayState:
    token: str
    active: bool = False
    mode: str = "Bistable"  # Bistable | Monostable
    delay_s: float = 1.0
    idle_state: str = "open"  # open | closed


@dataclass
class Badge:
    text: str
    until: float
    kind: str = "event"


class DiagEngine:
    def __init__(self, runtime: Runtime):
        self.rt = runtime
        self.rng = random.Random()
        self.rules: dict[str, RuleRuntime] = {}
        self.relays: dict[str, RelayState] = {}
        self.inputs: dict[str, bool] = {}
        self.log: deque[dict[str, Any]] = deque(maxlen=runtime.config.diag.log_size)
        self.badges: dict[int, list[Badge]] = {}
        self.channel_flags: dict[int, dict[str, bool]] = {}
        self.notify_subs: dict[str, dict[str, Any]] = {}
        self._http = None
        self._running = False
        self._seq = 0
        self._counters: dict[tuple, int] = {}  # CountAggregation totals per (topic, source)
        self._monostable: dict[str, asyncio.TimerHandle] = {}
        self._window: deque[float] = deque()
        self.dropped = 0
        self._push_tasks: set[asyncio.Task] = set()
        runtime.events.add_listener(self._on_event)

    # ---------------------------------------------------------------- lifecycle
    async def start(self) -> None:
        self._running = True
        self.sync_config()

    async def stop(self) -> None:
        self._running = False
        for task in self._push_tasks:
            task.cancel()
        if self._push_tasks:
            await asyncio.gather(*self._push_tasks, return_exceptions=True)
        self._push_tasks.clear()
        for rr in self.rules.values():
            if rr.task:
                rr.task.cancel()
        for rr in self.rules.values():
            if rr.task:
                try:
                    await rr.task
                except (asyncio.CancelledError, Exception):
                    pass
        self.rules.clear()
        for h in self._monostable.values():
            h.cancel()
        self._monostable.clear()
        if self._http is not None:
            await self._http.close()
            self._http = None

    def sync_config(self) -> None:
        """Reconcile running rules / relays / inputs with rt.config.diag (call after every config change)."""
        cfg = self.rt.config.diag
        self.log = deque(self.log, maxlen=cfg.log_size)
        for n in range(1, cfg.relays + 1):
            self.relays.setdefault(relay_token(n), RelayState(relay_token(n)))
        for tok in [t for t in self.relays if int(t.rsplit("_", 1)[-1]) > cfg.relays]:
            self.relays.pop(tok)
            timer = self._monostable.pop(tok, None)
            if timer:
                timer.cancel()
        for n in range(1, cfg.inputs + 1):
            self.inputs.setdefault(input_token(n), False)
        for tok in [t for t in self.inputs if int(t.rsplit("_", 1)[-1]) > cfg.inputs]:
            self.inputs.pop(tok)

        wanted = {r.id: r for r in cfg.rules}
        for rid in [i for i in self.rules if i not in wanted]:
            self._cancel(rid)
            self.rules.pop(rid, None)
        for rid, rule in wanted.items():
            rr = self.rules.get(rid)
            if rr is None:
                rr = self.rules[rid] = RuleRuntime(rule)
            elif rr.rule != rule:
                self._cancel(rid)
                rr.rule = rule
                rr.fired = 0
            else:
                rr.rule = rule
            if self._running and rule.enabled and rule.schedule != Schedule.manual and (rr.task is None or rr.task.done()):
                rr.task = asyncio.get_running_loop().create_task(self._run_rule(rr), name=f"rule-{rid}")

    def _cancel(self, rid: str) -> None:
        rr = self.rules.get(rid)
        if rr and rr.task:
            rr.task.cancel()
            rr.task = None
        if rr:
            rr.next_at = 0.0

    # ------------------------------------------------------------------ timers
    async def _run_rule(self, rr: RuleRuntime) -> None:
        rule = rr.rule
        try:
            if rule.start_delay_s:
                rr.next_at = time.monotonic() + rule.start_delay_s
                await asyncio.sleep(rule.start_delay_s)
            while self._running:
                if rule.repeat and rr.fired >= rule.repeat:
                    break
                delay = rule_delay_s(rule, self.rng)
                rr.next_at = time.monotonic() + delay
                await asyncio.sleep(delay)
                await self.fire_rule(rule.id)
            rr.next_at = 0.0
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - a broken rule must not stop the others
            rr.last_error = repr(exc)
            log.exception("Rule %s crashed", rule.id)

    # ------------------------------------------------------------------ firing
    def channels_for(self, rule: EventRule) -> list[int]:
        enabled = [c.id for c in self.rt.enabled_channels()]
        if rule.kind in {RuleKind.relay, RuleKind.contact}:
            return [0]
        if rule.channel and rule.channel in enabled:
            return [rule.channel]
        if rule.channel:
            return []
        return enabled or [1]

    async def fire_rule(self, rule_id: str) -> list[OnvifEvent]:
        rr = self.rules.get(rule_id)
        if rr is None:
            raise KeyError(rule_id)
        return await self.fire(rr.rule, rr)

    async def fire(self, rule: EventRule, rr: RuleRuntime | None = None) -> list[OnvifEvent]:
        """Fire a rule once (also used for ad-hoc events that are not stored as rules)."""
        out: list[OnvifEvent] = []
        for ch in self.channels_for(rule):
            self._seq += 1
            out += await self._fire_one(rule, ch, self._seq)
        if rr is not None:
            rr.fired += 1
            rr.last_at = time.monotonic()
        return out

    async def _fire_one(self, rule: EventRule, ch: int, seq: int) -> list[OnvifEvent]:
        kind = rule.kind
        if kind == RuleKind.relay:
            return await self._do_relay(rule, seq)
        if kind == RuleKind.contact:
            return await self._do_contact(rule, seq)
        if kind == RuleKind.metadata:
            return self._do_metadata(rule, ch, seq)
        if kind == RuleKind.custom:
            return self._do_custom(rule, ch, seq)
        topic = {RuleKind.motion: T_MOTION, RuleKind.tamper: T_TAMPER}.get(kind)
        if kind == RuleKind.analytics:
            topic = ANALYTICS_TOPICS.get((rule.topic or "field").lower().replace("_", ""), rule.topic or T_FIELD)
        assert topic
        return await self._state_event(rule, topic, ch, seq)

    def _items(self, rule: EventRule, topic: str, ch: int, seq: int) -> tuple[dict[str, str], dict[str, str]]:
        _, _, src_names = TOPIC_SPEC.get(topic, (True, "State", ()))
        source: dict[str, str] = {}
        for name in src_names:
            if name == "VideoSourceConfigurationToken":
                source[name] = f"vsc_{ch}"
            elif name == "VideoAnalyticsConfigurationToken":
                source[name] = f"vac_{ch}"
            elif name == "Rule":
                source[name] = rule.name or RULE_NAMES.get(topic, "Rule")
        for k, v in rule.source.items():
            source[k] = render_value(v, rng=self.rng, counter=seq, channel=ch)
        data = {k: render_value(v, rng=self.rng, counter=seq, channel=ch) for k, v in rule.data.items()}
        return source, data

    def _emit(self, topic: str, source: dict[str, str], data: dict[str, str], *, ch: int, rule: EventRule,
              op: str = "Changed", prop: bool = True) -> OnvifEvent:
        ev = OnvifEvent(topic=topic, utc=utc_now(), data=dict(data), source=dict(source), operation=op,
                        is_property=prop, channel=ch, rule=rule.name, push_url=rule.push_url)
        if self._rate_limited():
            self.dropped += 1
            if self.dropped == 1 or self.dropped % 500 == 0:
                log.warning("Event rate limit (%s/s) hit: %s events dropped so far", self.rt.config.limits.max_events_per_s, self.dropped)
            return ev
        self.rt.events.publish(ev)
        return ev

    def _rate_limited(self) -> bool:
        lim = self.rt.config.limits
        if lim.mode == "off":
            return False
        now = time.monotonic()
        w = self._window
        while w and now - w[0] > 1.0:
            w.popleft()
        if len(w) >= lim.max_events_per_s:
            return True
        w.append(now)
        return False

    async def _state_event(self, rule: EventRule, topic: str, ch: int, seq: int) -> list[OnvifEvent]:
        is_prop, data_name, _ = TOPIC_SPEC.get(topic, (True, "State", ()))
        source, extra = self._items(rule, topic, ch, seq)
        events: list[OnvifEvent] = []
        if not is_prop:  # instantaneous event (e.g. line crossed)
            extra.setdefault(data_name, str(seq))
            events.append(self._emit(topic, source, extra, ch=ch, rule=rule, prop=False))
            return events

        key = (topic, tuple(sorted(source.items())))
        if topic == T_COUNT:  # counter: running total, +1 per firing unless data sets Count/Step
            step = int(float(extra.pop("Step", "1") or 1))
            total = self._counters.get(key, 0) + step
            if "Count" in extra:
                total = int(float(extra["Count"] or 0))
            self._counters[key] = total
            extra["Count"] = str(total)
            events.append(self._emit(topic, source, extra, ch=ch, rule=rule))
            return events
        cur = self.rt.events.last_state(key)
        if rule.action == "on":
            plan = [True]
        elif rule.action == "off":
            plan = [False]
        elif rule.action == "toggle":
            plan = [not cur]
        else:  # pulse: on ... hold ... off
            plan = [True, False]
        for i, val in enumerate(plan):
            data = {data_name: "true" if val else "false", **extra}
            events.append(self._emit(topic, source, data, ch=ch, rule=rule))
            self._flag(ch, topic, val)
            if len(plan) == 2 and i == 0:
                await asyncio.sleep(rule.hold_s)
        return events

    def _label(self, topic: str) -> str:
        english = {T_MOTION: "MOTION", T_TAMPER: "TAMPER", T_FIELD: "FIELD", T_CELL: "CELL MOTION",
                   T_LINE: "LINE CROSS", T_REGION: "MOTION REGION", T_LOITER: "LOITERING"}
        russian = {T_MOTION: "ТРЕВОГА", T_TAMPER: "ВСКРЫТИЕ", T_FIELD: "ВТОРЖЕНИЕ", T_CELL: "ДВИЖЕНИЕ",
                   T_LINE: "ПЕРЕСЕЧЕНИЕ ЛИНИИ", T_REGION: "ЗОНА ДВИЖЕНИЯ", T_LOITER: "ДОЛГОЕ ПРЕБЫВАНИЕ"}
        labels = russian if self.rt.config.language == "ru" else english
        return labels.get(topic, topic.rsplit("/", 1)[-1].upper())

    def _flag(self, ch: int, topic: str, val: bool) -> None:
        self.channel_flags.setdefault(ch, {})[topic] = val

    def _badge(self, ch: int, text: str, seconds: float, kind: str = "event") -> None:
        lst = self.badges.setdefault(ch, [])
        now = time.monotonic()
        lst[:] = [b for b in lst if b.until > now and b.text != text]
        lst.append(Badge(text, now + seconds, kind))

    def _do_custom(self, rule: EventRule, ch: int, seq: int) -> list[OnvifEvent]:
        topic = rule.topic or "tns1:Custom/Event"
        source, data = self._items(rule, topic, ch, seq)
        if not rule.source and not TOPIC_SPEC.get(topic):
            source = {"VideoSourceConfigurationToken": f"vsc_{ch}"}
        if not data:
            val = {"on": True, "off": False}.get(rule.action, True)
            data = {"State": "true" if val else "false"}
        ev = self._emit(topic, source, data, ch=ch, rule=rule)
        return [ev]

    def _do_metadata(self, rule: EventRule, ch: int, seq: int) -> list[OnvifEvent]:
        sim = self.rt.analytics_for(ch)
        _, data = self._items(rule, T_FIELD, ch, seq)
        count = int(float(data.get("count", "1") or 1))
        klass = data.get("class", "")
        ttl = float(data.get("ttl_s", rule.hold_s or 5.0) or 5.0)
        made = sim.inject(count=count, klass=klass or None, ttl_s=ttl, speed=float(data.get("speed", "0") or 0))
        ev = self._emit(
            "tns1:RuleEngine/ObjectDetector/Objects",
            {"VideoSourceConfigurationToken": f"vsc_{ch}", "VideoAnalyticsConfigurationToken": f"vac_{ch}"},
            {"ObjectId": ",".join(str(o.track_id) for o in made), "Count": str(len(made))},
            ch=ch, rule=rule, prop=False,
        )
        self._record_metadata(ch)
        return [ev]

    def _record_metadata(self, ch: int) -> None:
        xml = self.metadata_xml(ch)
        for url in self.rt.config.diag.webhooks:
            self._post(url, xml.encode(), "application/xml")

    def metadata_xml(self, ch: int) -> str:
        sim = self.rt.analytics_for(ch)
        return (
            '<tt:MetadataStream xmlns:tt="http://www.onvif.org/ver10/schema"><tt:VideoAnalytics>'
            f"{sim.frame_xml(isoformat_z(), f'vs_{ch}')}</tt:VideoAnalytics></tt:MetadataStream>"
        )

    # ------------------------------------------------- relays and dry contacts
    def _relay_for(self, rule: EventRule) -> RelayState | None:
        tok = rule.target or relay_token(1)
        return self.relays.get(tok)

    async def _do_relay(self, rule: EventRule, seq: int) -> list[OnvifEvent]:
        relay = self._relay_for(rule)
        if relay is None:
            return []
        out: list[OnvifEvent] = []
        if rule.action == "toggle":
            plan = [not relay.active]
        elif rule.action == "on":
            plan = [True]
        elif rule.action == "off":
            plan = [False]
        else:
            plan = [True, False]
        for i, val in enumerate(plan):
            out.append(self.set_relay(relay.token, val, rule=rule))
            if len(plan) == 2 and i == 0:
                await asyncio.sleep(rule.hold_s)
        return out

    def set_relay(self, token: str, active: bool, *, rule: EventRule | None = None) -> OnvifEvent:
        relay = self.relays[token]
        relay.active = active
        r = rule or EventRule(kind=RuleKind.relay, name=f"manual {token}")
        ev = self._emit(T_RELAY, {"RelayToken": token}, {"LogicalState": "true" if active else "false"},
                        ch=0, rule=r)
        if active and relay.mode == "Monostable":
            loop = asyncio.get_running_loop()
            old = self._monostable.pop(token, None)
            if old:
                old.cancel()
            self._monostable[token] = loop.call_later(relay.delay_s, lambda: self.set_relay(token, False))
        return ev

    async def _do_contact(self, rule: EventRule, seq: int) -> list[OnvifEvent]:
        tok = rule.target or input_token(1)
        if tok not in self.inputs:
            return []
        cur = self.inputs[tok]
        plan = {"toggle": [not cur], "on": [True], "off": [False]}.get(rule.action, [True, False])
        out = []
        for i, val in enumerate(plan):
            out.append(self.set_input(tok, val, rule=rule))
            if len(plan) == 2 and i == 0:
                await asyncio.sleep(rule.hold_s)
        return out

    def set_input(self, token: str, active: bool, *, rule: EventRule | None = None) -> OnvifEvent:
        self.inputs[token] = active
        r = rule or EventRule(kind=RuleKind.contact, name=f"manual {token}")
        return self._emit(T_INPUT, {"InputToken": token}, {"LogicalState": "true" if active else "false"},
                          ch=0, rule=r)

    # ------------------------------------------------------------- push targets
    def _on_event(self, ev: OnvifEvent) -> None:
        """Called by the bus for every published event (loop thread)."""
        self._overlay_event_badge(ev)
        rec = {
            "ts": isoformat_z(ev.utc),
            "topic": ev.topic,
            "operation": ev.operation,
            "source": dict(ev.source),
            "data": dict(ev.data),
            "channel": getattr(ev, "channel", 0),
            "rule": getattr(ev, "rule", ""),
        }
        self.log.appendleft(rec)
        if not self._running:
            return
        body = json.dumps(rec, ensure_ascii=False).encode()
        urls = list(self.rt.config.diag.webhooks)
        extra = getattr(ev, "push_url", "")
        if extra:
            urls.append(extra)
        for url in urls:
            self._post(url, body, "application/json")
        if self.rt.config.diag.notify:
            self._notify(ev)

    def _overlay_event_badge(self, ev: OnvifEvent) -> None:
        """Mirror every relevant bus event into the video HUD, including EventGenerator events."""
        topic = ev.topic
        channel = getattr(ev, "channel", 0)
        data = ev.data
        is_ru = self.rt.config.language == "ru"
        if topic == T_RELAY:
            token = ev.source.get("RelayToken", "relay_1")
            state = str(data.get("LogicalState", "false")).lower() == "true"
            word = "РЕЛЕ" if is_ru else "RELAY"
            value = ("ВКЛ" if state else "ВЫКЛ") if is_ru else ("ON" if state else "OFF")
            label = f"{word} {token.rsplit('_', 1)[-1]} {value}"
            self._badge(0, label, 3.0, "relay")
            return
        if topic == T_INPUT:
            token = ev.source.get("InputToken", "di_1")
            state = str(data.get("LogicalState", "false")).lower() == "true"
            value = ("АКТИВЕН" if state else "СБРОС") if is_ru else ("ACTIVE" if state else "CLEAR")
            self._badge(0, f"{'ВХОД' if is_ru else 'INPUT'} {token.rsplit('_', 1)[-1]} {value}", 3.0, "contact")
            return
        if topic == "tns1:RuleEngine/ObjectDetector/Objects":
            count = data.get("Count", "1")
            self._badge(channel, f"{'МЕТА' if is_ru else 'META'} x{count}", 5.0, "metadata")
            return
        if topic == T_COUNT:
            count = data.get("Count", "0")
            self._badge(channel, f"{'СЧЁТ' if is_ru else 'COUNT'} {count}", 3.0, "analytics")
            return
        if topic in TOPIC_SPEC:
            name = TOPIC_SPEC[topic][1]
            value = str(data.get(name, "true")).lower()
            label = self._label(topic)
            if topic != T_LINE:
                label += (" АКТИВНА" if is_ru else " ACTIVE") if value not in {"false", "0", "off"} else (" СБРОС" if is_ru else " CLEAR")
            self._badge(channel, label, max(1.5, self.rt.config.events.hold_s))
            return
        # Arbitrary custom topics still get a visible notification. Keep its text
        # bounded because the HUD is a fixed-size, low-overhead bitmap overlay.
        if "/" in topic or getattr(ev, "rule", ""):
            text = getattr(ev, "rule", "") or topic.rsplit("/", 1)[-1]
            self._badge(channel, str(text).upper()[:24], 3.0, "event")

    def _post(self, url: str, body: bytes, ctype: str) -> None:
        if not url:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        # Bound queued requests too: the HTTP connector only bounds sockets.
        if len(self._push_tasks) >= 128:
            self.dropped += 1
            return
        task = loop.create_task(self._post_async(url, body, ctype))
        self._push_tasks.add(task)
        task.add_done_callback(self._push_tasks.discard)

    async def _post_async(self, url: str, body: bytes, ctype: str) -> None:
        import aiohttp

        try:
            if self._http is None:
                self._http = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=4))
            async with self._http.post(url, data=body, headers={"Content-Type": ctype}) as resp:
                log.debug("push %s -> %s", url, resp.status)
        except Exception as exc:  # noqa: BLE001
            log.debug("push %s failed: %s", url, exc)
            self.log.appendleft({"ts": isoformat_z(), "topic": "push-error", "data": {"url": url, "error": str(exc)[:120]},
                                 "source": {}, "operation": "", "channel": 0, "rule": ""})

    def _notify(self, ev: OnvifEvent) -> None:
        now = utc_now()
        for sid, sub in list(self.notify_subs.items()):
            if sub["expires"] <= now:
                self.notify_subs.pop(sid, None)
                continue
            if sub["topics"] and not any(t in ev.topic for t in sub["topics"]):
                continue
            envelope = (
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope" '
                'xmlns:wsnt="http://docs.oasis-open.org/wsn/b-2" xmlns:wsa="http://www.w3.org/2005/08/addressing" '
                'xmlns:tt="http://www.onvif.org/ver10/schema" xmlns:tns1="http://www.onvif.org/ver10/topics">'
                "<s:Header><wsa:Action>http://docs.oasis-open.org/wsn/bw-2/NotificationConsumer/Notify</wsa:Action></s:Header>"
                f"<s:Body><wsnt:Notify>{ev.to_xml()}</wsnt:Notify></s:Body></s:Envelope>"
            )
            self._post(sub["url"], envelope.encode(), "application/soap+xml")

    # --------------------------------------------------------------- overlay
    def overlay_state(self, ch: int) -> dict[str, Any]:
        """Everything the video overlay needs for one channel, as plain data."""
        now = time.monotonic()
        badges = [b for b in self.badges.get(ch, []) if b.until > now] + [b for b in self.badges.get(0, []) if b.until > now]
        self.badges[ch] = [b for b in self.badges.get(ch, []) if b.until > now]
        return {
            "badges": [(b.text, b.kind) for b in badges],
            "flags": dict(self.channel_flags.get(ch, {})),
            "relays": {t: r.active for t, r in self.relays.items()},
            "inputs": dict(self.inputs),
        }

    # ------------------------------------------------------------- API views
    def status(self) -> dict[str, Any]:
        now = time.monotonic()
        rules = []
        for rr in self.rules.values():
            r = rr.rule
            rules.append(
                {
                    "id": r.id,
                    "name": r.name,
                    "kind": r.kind.value,
                    "enabled": r.enabled,
                    "schedule": r.schedule.value,
                    "fired": rr.fired,
                    "next_in_s": round(rr.next_at - now, 1) if rr.next_at else None,
                    "running": bool(rr.task and not rr.task.done()),
                    "error": rr.last_error,
                }
            )
        return {
            "rules": rules,
            "relays": {t: {"active": r.active, "mode": r.mode, "delay_s": r.delay_s, "idle_state": r.idle_state}
                       for t, r in self.relays.items()},
            "inputs": dict(self.inputs),
            "notify_subscribers": len(self.notify_subs),
            "dropped_events": self.dropped,
        }

    def recent(self, limit: int = 100) -> list[dict[str, Any]]:
        return list(self.log)[:limit]

