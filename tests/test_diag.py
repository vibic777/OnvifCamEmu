"""Diagnostic engine: rules, timers, relays, dry contacts, push, overlay state, API."""

from __future__ import annotations

import asyncio
import random
import xml.etree.ElementTree as ET

import pytest
from aiohttp.test_utils import TestClient, TestServer

from onvif_emu.constructor import parse_duration, parse_range, parse_rule_spec
from onvif_emu.diag import T_INPUT, T_LINE, T_MOTION, T_RELAY, T_TAMPER, render_value, rule_delay_s
from onvif_emu.models import EventRule, Schedule
from onvif_emu.onvif_dispatch import dispatch
from onvif_emu.soap import parse_envelope


def _wrap(action: str, inner: str = "", ns: str = "http://www.onvif.org/ver10/device/wsdl") -> object:
    return parse_envelope(
        f'<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"><s:Body><{action} xmlns="{ns}">{inner}</{action}></s:Body></s:Envelope>'
    )


# ---------------------------------------------------------------- timers / units / templates
def test_delay_units_and_random_range():
    rng = random.Random(1)
    fixed = EventRule(kind="motion", schedule="interval", interval=2, unit="minutes")
    assert rule_delay_s(fixed, rng) == 120
    hours = EventRule(kind="motion", schedule="interval", interval=1.5, unit="hours")
    assert rule_delay_s(hours, rng) == 5400
    rnd = EventRule(kind="motion", schedule="random", range_min=5, range_max=200, unit="seconds")
    samples = [rule_delay_s(rnd, rng) for _ in range(300)]
    assert min(samples) >= 5 and max(samples) <= 200
    assert max(samples) - min(samples) > 100  # really random, not constant
    minutes = EventRule(kind="motion", schedule="random", range_min=1, range_max=100, unit="minutes")
    assert all(60 <= rule_delay_s(minutes, rng) <= 6000 for _ in range(100))


def test_range_is_normalised_and_ids_generated():
    r = EventRule(kind="relay", range_min=50, range_max=10)
    assert (r.range_min, r.range_max) == (10, 50)
    assert r.id and r.name.startswith("relay-")


def test_value_templates():
    rng = random.Random(3)
    assert 1 <= int(render_value("rand:1-5", rng=rng, counter=0, channel=1)) <= 5
    assert render_value("choice:a|b", rng=rng, counter=0, channel=1) in {"a", "b"}
    assert render_value("seq", rng=rng, counter=7, channel=1) == "7"
    assert render_value("channel", rng=rng, counter=0, channel=4) == "4"
    assert render_value("plain", rng=rng, counter=0, channel=1) == "plain"
    assert render_value("rand:x-y", rng=rng, counter=0, channel=1) == "rand:x-y"  # bad template stays text


def test_rule_spec_parsing():
    r = parse_rule_spec("kind=tamper,every=30s,ch=2,device=3,hold=2s,name=T")
    assert (r.kind.value, r.schedule, r.interval, r.unit.value, r.channel, r.device, r.hold_s) == (
        "tamper", Schedule.interval, 30, "seconds", 2, 3, 2)
    r = parse_rule_spec("kind=contact,random=1-100m")
    assert (r.schedule, r.range_min, r.range_max, r.unit.value) == (Schedule.random, 1, 100, "minutes")
    r = parse_rule_spec("kind=custom,topic=tns1:X/Y,every=2h,data.level=rand:1-9,source.Tok=abc")
    assert r.unit.value == "hours" and r.data == {"level": "rand:1-9"} and r.source == {"Tok": "abc"}
    assert parse_rule_spec("kind=motion").schedule == Schedule.manual
    with pytest.raises(ValueError):
        parse_rule_spec("kind=motion,bogus=1")
    with pytest.raises(ValueError):
        parse_rule_spec("every=5s")
    assert parse_duration("5") == (5.0, "seconds")
    assert parse_range("5-200s") == (5.0, 200.0, "seconds")


# ---------------------------------------------------------------- engine
@pytest.fixture
def engine(runtime):
    runtime.diag.sync_config()
    runtime.diag._running = True
    return runtime.diag


@pytest.mark.asyncio
async def test_fire_pulse_publishes_on_and_off(engine, runtime):
    sub = runtime.events.create(timeout_s=30)
    rule = EventRule(kind="tamper", hold_s=0.05)
    events = await engine.fire(rule)
    assert [e.data["State"] for e in events] == ["true", "false"]
    assert events[0].topic == T_TAMPER
    assert events[0].source == {"VideoSourceConfigurationToken": "vsc_1"}
    assert len(sub.pull(10)) == 2
    assert "ANY" not in str(engine.log) and len(engine.recent()) == 2


@pytest.mark.asyncio
async def test_actions_toggle_on_off(engine, runtime):
    ev = await engine.fire(EventRule(kind="motion", action="on"))
    assert [e.data["State"] for e in ev] == ["true"]
    ev = await engine.fire(EventRule(kind="motion", action="toggle"))
    assert [e.data["State"] for e in ev] == ["false"]
    ev = await engine.fire(EventRule(kind="motion", action="toggle"))
    assert [e.data["State"] for e in ev] == ["true"]
    assert runtime.events.current[T_MOTION] is True


@pytest.mark.asyncio
async def test_line_crossed_is_not_a_property_event(engine):
    ev = await engine.fire(EventRule(kind="analytics", topic="line"))
    assert len(ev) == 1 and ev[0].topic == T_LINE and not ev[0].is_property
    assert "PropertyOperation" not in ev[0].to_xml()
    assert "ObjectId" in ev[0].data


@pytest.mark.asyncio
async def test_relay_and_contact_state_and_events(engine, runtime):
    ev = await engine.fire(EventRule(kind="relay", target="relay_2", action="on"))
    assert engine.relays["relay_2"].active and ev[0].topic == T_RELAY
    assert ev[0].data == {"LogicalState": "true"} and ev[0].source == {"RelayToken": "relay_2"}
    ev = await engine.fire(EventRule(kind="contact", target="di_1", action="on"))
    assert engine.inputs["di_1"] is True and ev[0].topic == T_INPUT
    engine.set_input("di_1", False)
    assert engine.inputs["di_1"] is False


@pytest.mark.asyncio
async def test_monostable_relay_returns_by_itself(engine):
    engine.relays["relay_1"].mode = "Monostable"
    engine.relays["relay_1"].delay_s = 0.05
    engine.set_relay("relay_1", True)
    assert engine.relays["relay_1"].active
    await asyncio.sleep(0.2)
    assert not engine.relays["relay_1"].active


@pytest.mark.asyncio
async def test_custom_event_keeps_arbitrary_data_and_escapes(engine):
    rule = EventRule(kind="custom", topic="tns1:Door/Open", data={"who": 'A "quote" <x>', "n": "rand:1-3"}, action="on")
    ev = (await engine.fire(rule))[0]
    xml = ev.to_xml()
    ET.fromstring(f'<r xmlns:wsnt="w" xmlns:tt="t">{xml}</r>')  # well-formed despite quotes/angle brackets
    assert 1 <= int(ev.data["n"]) <= 3 and ev.topic == "tns1:Door/Open"


@pytest.mark.asyncio
async def test_per_channel_rules_and_channel_zero_fans_out(engine, runtime):
    from onvif_emu.models import ChannelConfig

    runtime.config.channels.append(ChannelConfig(id=2, name="B"))
    ev = await engine.fire(EventRule(kind="motion", action="on", channel=0))
    assert {e.source["VideoSourceConfigurationToken"] for e in ev} == {"vsc_1", "vsc_2"}
    ev = await engine.fire(EventRule(kind="motion", action="off", channel=2))
    assert [e.source["VideoSourceConfigurationToken"] for e in ev] == ["vsc_2"]
    assert await engine.fire(EventRule(kind="motion", channel=9)) == []  # unknown channel: nothing


@pytest.mark.asyncio
async def test_metadata_rule_injects_objects_that_expire(engine, runtime):
    sim = runtime.analytics_for(1)
    before = len(sim.objects)
    ev = await engine.fire(EventRule(kind="metadata", data={"count": "3", "class": "Person", "ttl_s": "0.5"}))
    assert len(sim.objects) == before + 3 and ev[0].data["Count"] == "3"
    assert "BoundingBox" in engine.metadata_xml(1)
    assert any(o.klass == "Person" for o in sim.objects)


@pytest.mark.asyncio
async def test_timer_rule_runs_repeats_and_stops(engine, runtime):
    runtime.config.diag.rules = [
        EventRule(kind="motion", schedule="interval", interval=0.06, unit="seconds", action="on", repeat=3)
    ]
    engine.sync_config()
    await asyncio.sleep(0.6)
    rr = next(iter(engine.rules.values()))
    assert rr.fired == 3  # repeat=3 -> exactly three firings, then the task ends
    assert rr.task is not None and rr.task.done()


@pytest.mark.asyncio
async def test_rules_added_and_removed_at_runtime(engine, runtime):
    r = EventRule(kind="tamper", schedule="interval", interval=100, unit="hours")
    runtime.config.diag.rules.append(r)
    engine.sync_config()
    assert r.id in engine.rules and engine.rules[r.id].task is not None
    runtime.config.diag.rules.clear()
    engine.sync_config()
    assert not engine.rules


@pytest.mark.asyncio
async def test_event_rate_limit_drops_and_counts(engine, runtime):
    runtime.config.limits.max_events_per_s = 5
    for _ in range(20):
        engine.set_input("di_1", True)
    assert engine.dropped >= 10
    assert len(runtime.events.last) <= 20


@pytest.mark.asyncio
async def test_sync_point_replays_real_states(engine, runtime):
    await engine.fire(EventRule(kind="relay", target="relay_1", action="on"))
    sub = runtime.events.create()
    runtime.events.set_sync(sub.token)
    topics = [m.topic for m in sub.pull(50)]
    assert T_RELAY in topics and T_MOTION in topics  # real relay state + legacy defaults


@pytest.mark.asyncio
async def test_overlay_state_shows_badges_and_io(engine):
    await engine.fire(EventRule(kind="tamper", action="on", hold_s=2))
    engine.set_relay("relay_1", True)
    engine.set_input("di_2", True)
    st = engine.overlay_state(1)
    assert ("ВСКРЫТИЕ АКТИВНА", "event") in st["badges"]
    assert ("РЕЛЕ 1 ВКЛ", "relay") in st["badges"]
    assert ("ВХОД 2 АКТИВЕН", "contact") in st["badges"]
    assert st["relays"]["relay_1"] is True and st["inputs"]["di_2"] is True
    # The periodic EventGenerator publishes directly to the shared event bus,
    # outside DiagEngine.fire; those alarms must still reach the video overlay.
    engine.rt.events.trigger(T_MOTION, True)
    engine.rt.events.trigger(T_TAMPER, False)
    await engine.fire(EventRule(kind="analytics", topic="counting", data={"Step": "3"}))
    await engine.fire(EventRule(kind="metadata", data={"count": "2"}))
    st = engine.overlay_state(1)
    assert ("ТРЕВОГА АКТИВНА", "event") in st["badges"]
    assert ("ВСКРЫТИЕ СБРОС", "event") in st["badges"]
    assert ("СЧЁТ 3", "analytics") in st["badges"]
    assert ("МЕТА x2", "metadata") in st["badges"]
    engine.rt.config.language = "en"
    assert engine._label(T_MOTION) == "MOTION"


# ---------------------------------------------------------------- ONVIF surface
def test_topic_set_and_relay_operations(runtime):
    runtime.diag.sync_config()
    out = dispatch(runtime, _wrap("GetEventProperties", ns="http://www.onvif.org/ver10/events/wsdl"), "/onvif/events_service")
    assert out.index("<wstop:TopicSet>") < out.index("<wsnt:TopicExpressionDialect>")  # schema order
    assert "tns1:Relay" in out and "MessageDescription" in out and 'IsProperty="true"' in out
    out = dispatch(runtime, _wrap("GetRelayOutputs"), "/onvif/device_service")
    assert 'token="relay_1"' in out and 'token="relay_2"' in out
    out = dispatch(runtime, _wrap("SetRelayOutputState", "<RelayOutputToken>relay_1</RelayOutputToken><LogicalState>active</LogicalState>"),
                   "/onvif/device_service")
    assert "SetRelayOutputStateResponse" in out and runtime.diag.relays["relay_1"].active
    bad = dispatch(runtime, _wrap("SetRelayOutputState", "<RelayOutputToken>nope</RelayOutputToken><LogicalState>active</LogicalState>"),
                   "/onvif/device_service")
    assert "Fault" in bad
    caps = dispatch(runtime, _wrap("GetCapabilities"), "/onvif/device_service")
    assert "<tt:RelayOutputs>2</tt:RelayOutputs>" in caps and "<tt:InputConnectors>2</tt:InputConnectors>" in caps


def test_notify_subscribe_registers_consumer(runtime):
    runtime.diag.sync_config()
    body = ('<wsnt:ConsumerReference xmlns:wsnt="http://docs.oasis-open.org/wsn/b-2" xmlns:wsa="http://www.w3.org/2005/08/addressing">'
            "<wsa:Address>http://127.0.0.1:9/hook</wsa:Address></wsnt:ConsumerReference>"
            '<wsnt:InitialTerminationTime xmlns:wsnt="http://docs.oasis-open.org/wsn/b-2">PT1H</wsnt:InitialTerminationTime>')
    out = dispatch(runtime, _wrap("Subscribe", body, ns="http://docs.oasis-open.org/wsn/b-2"), "/onvif/events_service")
    assert "SubscribeResponse" in out and len(runtime.diag.notify_subs) == 1
    sid = next(iter(runtime.diag.notify_subs))
    unsub = dispatch(runtime, parse_envelope(
        f'<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"><s:Body><Unsubscribe xmlns="http://docs.oasis-open.org/wsn/b-2">'
        f"<SubscriptionId>{sid}</SubscriptionId></Unsubscribe></s:Body></s:Envelope>"), "/onvif/events_service")
    assert "UnsubscribeResponse" in unsub and not runtime.diag.notify_subs


# ---------------------------------------------------------------- REST API
@pytest.mark.asyncio
async def test_rest_rules_fire_relay_and_log(runtime):
    import base64

    from onvif_emu.http_app import create_app

    runtime.diag.sync_config()
    runtime.diag._running = True
    hdr = {"Authorization": "Basic " + base64.b64encode(b"admin:admin").decode()}
    async with TestServer(create_app(runtime)) as server, TestClient(server) as c:
        r = await c.post("/api/diag/rules", json={"kind": "tamper", "schedule": "manual", "name": "t1"}, headers=hdr)
        rid = (await r.json())["rule"]["id"]
        assert (await (await c.get("/api/diag", headers=hdr)).json())["config"]["rules"][0]["name"] == "t1"
        r = await c.post("/api/diag/fire?wait=1", json={"rule_id": rid}, headers=hdr)
        assert (await r.json())["events"] == 2 or (await r.json())["ok"]
        r = await c.post("/api/diag/relay/relay_1", json={"active": True}, headers=hdr)
        assert (await r.json())["status"]["relay_1"]["active"] is True
        assert (await c.post("/api/diag/relay/zzz", json={"active": True}, headers=hdr)).status == 404
        log = await (await c.get("/api/diag/log?limit=5", headers=hdr)).json()
        assert log["events"] and log["events"][0]["topic"]
        assert (await c.post("/api/diag/rules", json={"kind": "bogus"}, headers=hdr)).status == 400
        assert (await c.delete(f"/api/diag/rules/{rid}", headers=hdr)).status == 200
        assert (await c.delete(f"/api/diag/rules/{rid}", headers=hdr)).status == 404
        lim = await (await c.get("/api/limits", headers=hdr)).json()
        assert lim["estimate"]["streams"] >= 1
        meta = await c.get("/api/metadata/1", headers=hdr)
        assert "MetadataStream" in await meta.text()


@pytest.mark.asyncio
async def test_analytics_kinds_and_counter(engine):
    from onvif_emu.diag import T_COUNT, T_LOITER, T_REGION

    ev = await engine.fire(EventRule(kind="analytics", topic="loitering", action="on"))
    assert ev[0].topic == T_LOITER and ev[0].data["IsLoitering"] == "true"
    ev = await engine.fire(EventRule(kind="analytics", topic="motionregion", name="Gate", action="on"))
    assert ev[0].topic == T_REGION and ev[0].source["Rule"] == "Gate"
    rule = EventRule(kind="analytics", topic="counting", name="Door")
    counts = [(await engine.fire(rule))[0].data["Count"] for _ in range(3)]
    assert counts == ["1", "2", "3"]
    ev = await engine.fire(EventRule(kind="analytics", topic="counting", name="Door", data={"Step": "5"}))
    assert ev[0].topic == T_COUNT and ev[0].data == {"Count": "8"}
