"""SOAP 1.2 envelope parse/build with ONVIF 2.x namespaces."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from xml.etree import ElementTree as ET

log = logging.getLogger(__name__)

NS = {
    "s": "http://www.w3.org/2003/05/soap-envelope",
    "soap": "http://www.w3.org/2003/05/soap-envelope",
    "env": "http://schemas.xmlsoap.org/soap/envelope/",
    "tds": "http://www.onvif.org/ver10/device/wsdl",
    "trt": "http://www.onvif.org/ver10/media/wsdl",
    "tptz": "http://www.onvif.org/ver20/ptz/wsdl",
    "timg": "http://www.onvif.org/ver20/imaging/wsdl",
    "tev": "http://www.onvif.org/ver10/events/wsdl",
    "tan": "http://www.onvif.org/ver20/analytics/wsdl",
    "tt": "http://www.onvif.org/ver10/schema",
    "ter": "http://www.onvif.org/ver10/error",
    "wsnt": "http://docs.oasis-open.org/wsn/b-2",
    "wsa": "http://www.w3.org/2005/08/addressing",
    "wsa5": "http://schemas.xmlsoap.org/ws/2004/08/addressing",
    "wsd": "http://schemas.xmlsoap.org/ws/2005/04/discovery",
    "dn": "http://www.onvif.org/ver10/network/wsdl",
    "wsse": "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd",
    "wsu": "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd",
    "wstop": "http://docs.oasis-open.org/wsn/t-1",
    "tns1": "http://www.onvif.org/ver10/topics",
}

for prefix, uri in NS.items():
    ET.register_namespace(prefix, uri)

SOAP_NS_CANDIDATES = (
    NS["s"],
    NS["env"],
    "http://www.w3.org/2003/05/soap-envelope/",
)


@dataclass
class SoapRequest:
    action: str
    body: ET.Element
    envelope: ET.Element
    raw: bytes
    header_action: str = ""


def localname(tag: str) -> str:
    if tag[0] == "{":
        return tag.rsplit("}", 1)[-1]
    if ":" in tag:
        return tag.split(":", 1)[1]
    return tag


def find_child(el: ET.Element, name: str) -> ET.Element | None:
    want = name.lower()
    for child in el:
        if localname(child.tag).lower() == want:
            return child
    for child in el.iter():
        if localname(child.tag).lower() == want:
            return child
    return None


def child_text(el: ET.Element | None, name: str, default: str = "") -> str:
    if el is None:
        return default
    found = find_child(el, name)
    if found is None or found.text is None:
        return default
    return found.text.strip()


def child_float(el: ET.Element | None, name: str, default: float | None = None) -> float | None:
    text = child_text(el, name, "")
    if text == "":
        return default
    try:
        return float(text)
    except ValueError:
        return default


def attr(el: ET.Element | None, name: str, default: str = "") -> str:
    if el is None:
        return default
    for k, v in el.attrib.items():
        if localname(k).lower() == name.lower() or k.lower() == name.lower():
            return v
    return default


def parse_envelope(raw: bytes | str) -> SoapRequest:
    if isinstance(raw, str):
        raw_b = raw.encode("utf-8")
        text = raw
    else:
        raw_b = raw
        text = raw.decode("utf-8", errors="replace")
    # Strip BOM / leading junk some Windows stacks send
    text = text.lstrip("\ufeff \t\r\n")
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise ValueError(f"Malformed SOAP: {exc}") from exc
    body = None
    header_action = ""
    for el in root.iter():
        ln = localname(el.tag)
        if ln == "Body":
            body = el
        elif ln == "Action" and el.text:
            header_action = el.text.strip()
    if body is None:
        body = root
    action = ""
    for child in list(body):
        if localname(child.tag) in {"Fault"}:
            continue
        action = localname(child.tag)
        body = child
        break
    if not action and header_action:
        action = header_action.rsplit("/", 1)[-1]
    log.log(5, "SOAP parse action=%s bytes=%s", action, len(raw_b))  # 5 = TRACE
    return SoapRequest(action=action, body=body, envelope=root, raw=raw_b, header_action=header_action)


def soap_ok(inner_xml: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"'
        ' xmlns:tds="http://www.onvif.org/ver10/device/wsdl"'
        ' xmlns:trt="http://www.onvif.org/ver10/media/wsdl"'
        ' xmlns:tptz="http://www.onvif.org/ver20/ptz/wsdl"'
        ' xmlns:timg="http://www.onvif.org/ver20/imaging/wsdl"'
        ' xmlns:tev="http://www.onvif.org/ver10/events/wsdl"'
        ' xmlns:tan="http://www.onvif.org/ver20/analytics/wsdl"'
        ' xmlns:tt="http://www.onvif.org/ver10/schema"'
        ' xmlns:wsa="http://www.w3.org/2005/08/addressing"'
        ' xmlns:wsnt="http://docs.oasis-open.org/wsn/b-2"'
        ' xmlns:wstop="http://docs.oasis-open.org/wsn/t-1"'
        ' xmlns:xs="http://www.w3.org/2001/XMLSchema"'
        ' xmlns:tns1="http://www.onvif.org/ver10/topics">'
        "<s:Header/>"
        f"<s:Body>{inner_xml}</s:Body></s:Envelope>"
    )


def soap_fault(code: str, reason: str, subcode: str = "ter:ActionNotSupported") -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"'
        ' xmlns:ter="http://www.onvif.org/ver10/error" xmlns:tt="http://www.onvif.org/ver10/schema">'
        "<s:Body><s:Fault>"
        f"<s:Code><s:Value>s:Sender</s:Value><s:Subcode><s:Value>{subcode}</s:Value></s:Subcode></s:Code>"
        f"<s:Reason><s:Text xml:lang=\"en\">{_esc(reason)}</s:Text></s:Reason>"
        f"<s:Detail><ter:ErrorCode>{_esc(code)}</ter:ErrorCode></s:Detail>"
        "</s:Fault></s:Body></s:Envelope>"
    )


def _esc(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def fbool(v: bool) -> str:
    return "true" if v else "false"


def text_of(el: ET.Element | None) -> str:
    if el is None or el.text is None:
        return ""
    return el.text.strip()


def iter_local(el: ET.Element, name: str) -> list[ET.Element]:
    want = name.lower()
    return [c for c in el.iter() if localname(c.tag).lower() == want]


def nested_float(body: ET.Element, *path: str) -> float | None:
    el: ET.Element | None = body
    for name in path:
        el = find_child(el, name) if el is not None else None
    if el is None or el.text is None or el.text.strip() == "":
        # try attributes x/y
        if el is not None:
            for key in ("x", "y", "space"):
                if key in {localname(k).lower() for k in el.attrib}:
                    pass
            xs = attr(el, "x")
            if xs:
                try:
                    return float(xs)
                except ValueError:
                    return None
        return None
    try:
        return float(el.text.strip())
    except ValueError:
        return None


def vector_xy(body: ET.Element, container: str, default: tuple[float | None, float | None] = (None, None)) -> tuple[float | None, float | None]:
    el = find_child(body, container)
    if el is None:
        # sometimes nested under another wrapper already selected
        el = body if localname(body.tag).lower() == container.lower() else None
    if el is None:
        return default
    # child PanTilt / Zoom may have x y attrs
    pt = find_child(el, "PanTilt")
    if pt is None and localname(el.tag) == "PanTilt":
        pt = el
    x = y = None
    if pt is not None:
        xa, ya = attr(pt, "x"), attr(pt, "y")
        try:
            x = float(xa) if xa else None
        except ValueError:
            x = None
        try:
            y = float(ya) if ya else None
        except ValueError:
            y = None
    return x if x is not None else default[0], y if y is not None else default[1]


def vector_zoom(body: ET.Element, container: str) -> float | None:
    el = find_child(body, container)
    if el is None:
        el = body
    zel = find_child(el, "Zoom")
    if zel is None:
        return None
    xa = attr(zel, "x")
    try:
        return float(xa) if xa else child_float(zel, "x")
    except ValueError:
        return None
