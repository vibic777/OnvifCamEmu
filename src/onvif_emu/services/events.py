from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from onvif_emu.soap import child_text, find_child, soap_fault
from onvif_emu.timeutil import isoformat_z, utc_now

if TYPE_CHECKING:
    from onvif_emu.runtime import Runtime
    from onvif_emu.soap import SoapRequest

log = logging.getLogger(__name__)

def _desc(is_property: bool, source: tuple[str, ...], data_name: str) -> str:
    src = "".join(
        f'<tt:SimpleItemDescription Name="{n}" Type="tt:ReferenceToken"/>' for n in source
    )
    dtype = {"ObjectId": "tt:ReferenceToken", "Count": "xs:int"}.get(data_name, "xs:boolean")
    return (
        f'<tt:MessageDescription IsProperty="{"true" if is_property else "false"}">'
        f"<tt:Source>{src}</tt:Source>"
        f'<tt:Data><tt:SimpleItemDescription Name="{data_name}" Type="{dtype}"/></tt:Data>'
        "</tt:MessageDescription>"
    )


def topic_set(rt: Runtime) -> str:
    """WS-Topics TopicSet with a MessageDescription per topic (what ODM builds its event tree from)."""
    from onvif_emu.diag import TOPIC_SPEC

    known: dict[str, str] = {
        t: _desc(p, src, name) for t, (p, name, src) in TOPIC_SPEC.items()
    }
    known["tns1:RuleEngine/ObjectDetector/Objects"] = _desc(False, ("VideoSourceConfigurationToken", "VideoAnalyticsConfigurationToken"), "ObjectId")
    for rule in rt.config.diag.rules:
        if rule.topic.startswith("tns1:") and rule.topic not in known:
            known[rule.topic] = _desc(True, ("VideoSourceConfigurationToken",), "State")
    tree: dict = {}
    for topic, desc in known.items():
        node = tree
        for part in topic[len("tns1:"):].split("/"):
            node = node.setdefault(part, {})
        node["@desc"] = desc

    def render(node: dict) -> str:
        out = []
        for name, child in node.items():
            if name == "@desc":
                continue
            inner = child.get("@desc", "") + render(child)
            out.append(f'<tns1:{name} wstop:topic="true">{inner}</tns1:{name}>')
        return "".join(out)

    return f"<wstop:TopicSet>{render(tree)}</wstop:TopicSet>"


def handle_events(rt: Runtime, req: SoapRequest) -> str:
    a = req.action
    log.debug("Events action=%s", a)
    if a == "GetEventProperties":
        return (
            "<tev:GetEventPropertiesResponse>"
            "<tev:TopicNamespaceLocation>http://www.onvif.org/onvif/ver10/topics/topicns.xml</tev:TopicNamespaceLocation>"
            "<wsnt:FixedTopicSet>true</wsnt:FixedTopicSet>"
            f"{topic_set(rt)}"
            "<wsnt:TopicExpressionDialect>http://www.onvif.org/ver10/tev/topicExpression/ConcreteSet</wsnt:TopicExpressionDialect>"
            "<wsnt:TopicExpressionDialect>http://docs.oasis-open.org/wsn/t-1/TopicExpression/Concrete</wsnt:TopicExpressionDialect>"
            "<tev:MessageContentFilterDialect>http://www.onvif.org/ver10/tev/messageContentFilter/ItemFilter</tev:MessageContentFilterDialect>"
            "<tev:MessageContentSchemaLocation>http://www.onvif.org/onvif/ver10/schema/onvif.xsd</tev:MessageContentSchemaLocation>"
            "</tev:GetEventPropertiesResponse>"
        )
    if a == "CreatePullPointSubscription":
        timeout = _timeout_seconds(child_text(req.body, "InitialTerminationTime") or "PT60S")
        sub = rt.events.create(timeout_s=timeout)
        addr = f"{rt.base_url()}/onvif/events_service"
        exp = isoformat_z(sub.expires_at)
        return (
            "<tev:CreatePullPointSubscriptionResponse>"
            f"<tev:SubscriptionReference><wsa:Address>{addr}</wsa:Address>"
            f"<wsa:ReferenceParameters><tev:SubscriptionId>{sub.token}</tev:SubscriptionId></wsa:ReferenceParameters>"
            "</tev:SubscriptionReference>"
            f"<wsnt:CurrentTime>{isoformat_z()}</wsnt:CurrentTime>"
            f"<wsnt:TerminationTime>{exp}</wsnt:TerminationTime>"
            "</tev:CreatePullPointSubscriptionResponse>"
        )
    if a == "PullMessages":
        sub, timeout, limit = _pull_args(rt, req)
        if sub is None:
            return soap_fault("ResourceUnknown", "No such subscription", "ter:ResourceUnknown")
        return _pull_response(sub, sub.pull_wait(limit, timeout))
    if a == "Subscribe":
        return _subscribe(rt, req)
    if a == "Renew":
        token = _sub_token(req)
        timeout = _timeout_seconds(child_text(req.body, "TerminationTime") or "PT60S")
        if token in rt.diag.notify_subs:
            from datetime import timedelta

            sub_n = rt.diag.notify_subs[token]
            sub_n["expires"] = utc_now() + timedelta(seconds=max(5.0, timeout))
            return (
                "<wsnt:RenewResponse>"
                f"<wsnt:TerminationTime>{isoformat_z(sub_n['expires'])}</wsnt:TerminationTime>"
                f"<wsnt:CurrentTime>{isoformat_z()}</wsnt:CurrentTime></wsnt:RenewResponse>"
            )
        sub = rt.events.renew(token, timeout) if token else None
        if not sub:
            return soap_fault("ResourceUnknown", "No such subscription", "ter:ResourceUnknown")
        return (
            "<wsnt:RenewResponse>"
            f"<wsnt:TerminationTime>{isoformat_z(sub.expires_at)}</wsnt:TerminationTime>"
            f"<wsnt:CurrentTime>{isoformat_z()}</wsnt:CurrentTime>"
            "</wsnt:RenewResponse>"
        )
    if a == "Unsubscribe":
        token = _sub_token(req)
        if token:
            rt.events.unsubscribe(token)
            rt.diag.notify_subs.pop(token, None)
        return "<wsnt:UnsubscribeResponse/>"
    if a == "SetSynchronizationPoint":
        token = _sub_token(req)
        if token:
            rt.events.set_sync(token)
        elif rt.events.count():
            with rt.events._lock:
                tok = next(iter(rt.events._subs))
            rt.events.set_sync(tok)
        return "<tev:SetSynchronizationPointResponse/>"
    if a == "GetServiceCapabilities":
        return (
            "<tev:GetServiceCapabilitiesResponse><tev:Capabilities WSSubscriptionPolicySupport=\"true\" "
            "WSPullPointSupport=\"true\" WSPausableSubscriptionManagerInterfaceSupport=\"false\"/>"
            "</tev:GetServiceCapabilitiesResponse>"
        )
    return soap_fault("ActionNotSupported", f"Unknown Events action {a}")


def _subscribe(rt: Runtime, req: SoapRequest) -> str:
    """WS-BaseNotification Subscribe: events are pushed to ConsumerReference as Notify messages."""
    from datetime import timedelta

    from onvif_emu.soap import iter_local
    from onvif_emu.util import new_uuid

    consumer = find_child(req.body, "ConsumerReference")
    url = child_text(consumer, "Address") if consumer is not None else ""
    if not url:
        return soap_fault("InvalidArgs", "ConsumerReference/Address is required", "ter:InvalidArgVal")
    topics = [el.text.strip() for el in iter_local(req.body, "TopicExpression") if el.text and el.text.strip()]
    timeout = _timeout_seconds(child_text(req.body, "InitialTerminationTime") or "PT1H")
    from onvif_emu.events.bus import MAX_SUBSCRIPTIONS

    now = utc_now()
    for token, sub in list(rt.diag.notify_subs.items()):
        if sub["expires"] <= now:
            rt.diag.notify_subs.pop(token, None)
    if len(rt.diag.notify_subs) >= MAX_SUBSCRIPTIONS:
        return soap_fault("Action", "Push subscription limit reached", "ter:Action")
    sid = new_uuid()
    expires = utc_now() + timedelta(seconds=max(5.0, timeout))
    rt.diag.notify_subs[sid] = {"url": url, "topics": [t.split("//.")[0].rstrip("/") for t in topics], "expires": expires}
    addr = f"{rt.base_url()}/onvif/events_service"
    return (
        "<wsnt:SubscribeResponse>"
        f"<wsnt:SubscriptionReference><wsa:Address>{addr}</wsa:Address>"
        f"<wsa:ReferenceParameters><tev:SubscriptionId>{sid}</tev:SubscriptionId></wsa:ReferenceParameters>"
        "</wsnt:SubscriptionReference>"
        f"<wsnt:CurrentTime>{isoformat_z()}</wsnt:CurrentTime>"
        f"<wsnt:TerminationTime>{isoformat_z(expires)}</wsnt:TerminationTime>"
        "</wsnt:SubscribeResponse>"
    )


def _sub_token(req: SoapRequest) -> str:
    t = child_text(req.envelope, "SubscriptionId") or child_text(req.body, "SubscriptionId")
    return t


def _timeout_seconds(text: str) -> float:
    t = (text or "").strip()
    if not t:
        return 60.0
    # ISO-8601 duration PT#S / PT#M or xs:datetime
    if t.startswith("P"):
        seconds = 0.0
        num = ""
        t = t.replace("P", "").replace("T", "")
        for ch in t:
            if ch.isdigit() or ch == ".":
                num += ch
            else:
                v = float(num or 0)
                num = ""
                if ch == "H":
                    seconds += v * 3600
                elif ch == "M":
                    seconds += v * 60
                elif ch == "S":
                    seconds += v
        if num:
            seconds += float(num)
        return max(1.0, seconds)
    return 60.0


def _pull_args(rt, req):
    token = _sub_token(req)
    sub = rt.events.get(token) if token else None
    if sub is None and not token and rt.events.count() == 1:
        # ODM often hits the same endpoint; use the only live subscription
        with rt.events._lock:
            sub = next(iter(rt.events._subs.values()), None)
    if sub is None:
        return None, 0.0, 0
    timeout = _timeout_seconds(child_text(req.body, "Timeout") or "PT5S")
    limit = int(float(child_text(req.body, "MessageLimit") or "10"))
    return sub, min(30.0, timeout), max(1, min(limit, 32))


def _pull_response(sub, msgs):
    inner = "".join(m.to_xml() for m in msgs)
    return (
        "<tev:PullMessagesResponse>"
        f"<tev:CurrentTime>{isoformat_z()}</tev:CurrentTime>"
        f"<tev:TerminationTime>{isoformat_z(sub.expires_at)}</tev:TerminationTime>"
        f"{inner}"
        "</tev:PullMessagesResponse>"
    )


async def pull_messages_async(rt, req):
    import asyncio
    import time

    sub, timeout, limit = _pull_args(rt, req)
    if sub is None:
        return soap_fault("ResourceUnknown", "No such subscription", "ter:ResourceUnknown")
    deadline = time.monotonic() + timeout
    while sub.alive():
        msgs = sub.pull(limit)
        remaining = deadline - time.monotonic()
        if msgs or remaining <= 0:
            return _pull_response(sub, msgs)
        await asyncio.sleep(min(0.1, remaining))
    return soap_fault("ResourceUnknown", "Subscription expired or closed", "ter:ResourceUnknown")
