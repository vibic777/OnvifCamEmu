from datetime import timedelta

from onvif_emu.events.bus import MAX_MESSAGES, EventBus, OnvifEvent
from onvif_emu.timeutil import utc_now


def test_pull_fifo_and_bound():
    bus = EventBus()
    sub = bus.create(timeout_s=30)
    for i in range(MAX_MESSAGES + 20):
        bus.publish(OnvifEvent(topic="tns1:VideoSource/MotionAlarm", utc=utc_now(), data={"State": "true", "n": str(i)}))
    assert len(sub.messages) == MAX_MESSAGES
    batch = sub.pull(10)
    assert len(batch) == 10
    assert sub.pull(1000)  # remaining
    assert sub.pull(5) == []


def test_expiry_and_renew():
    bus = EventBus()
    sub = bus.create(timeout_s=5)
    assert bus.get(sub.token) is not None
    sub.expires_at = utc_now() - timedelta(seconds=1)
    assert bus.get(sub.token) is None
    sub2 = bus.create(timeout_s=30)
    r = bus.renew(sub2.token, timeout_s=120)
    assert r is not None
    assert bus.unsubscribe(sub2.token) is True
    assert bus.unsubscribe("nope") is False


def test_sync_point_injects_initialized():
    bus = EventBus()
    bus.trigger("tns1:VideoSource/MotionAlarm", True)
    sub = bus.create()
    bus.set_sync(sub.token)
    msgs = sub.pull(20)
    assert any(m.operation == "Initialized" for m in msgs)
    assert any(m.topic.endswith("MotionAlarm") for m in msgs)


def test_xml_contains_topic_and_utc():
    ev = OnvifEvent(topic="tns1:VideoSource/Tampering", utc=utc_now(), data={"State": "true"})
    xml = ev.to_xml()
    assert "Tampering" in xml
    assert "UtcTime" in xml
