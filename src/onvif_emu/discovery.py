"""WS-Discovery Probe → ProbeMatch on 239.255.255.250:3702.

Reply to the Probe *source address* (not always multicast) — required by ODM on Windows.
ProbeMatch is built once and reused; only MessageID/RelatesTo change (O(1) per probe).
"""

from __future__ import annotations

import asyncio
import logging
import socket
import struct
from typing import TYPE_CHECKING

from onvif_emu.util import new_uuid

if TYPE_CHECKING:
    from onvif_emu.runtime import Runtime

log = logging.getLogger(__name__)

WS_DISCOVERY = "239.255.255.250"
WS_PORT = 3702


def is_probe(data: bytes) -> bool:
    text = data.decode("utf-8", errors="ignore")
    return "Probe" in text and ("wsd" in text or "discovery" in text.lower() or "ProbeMatches" not in text)


def extract_message_id(data: bytes) -> str:
    text = data.decode("utf-8", errors="ignore")
    lower = text.lower()
    for key in ("messageid", "messageid"):
        i = 0
        while True:
            j = lower.find(key, i)
            if j < 0:
                break
            # skip closing tags like </a:MessageID>
            lt = text.rfind("<", 0, j)
            if lt >= 0 and text[lt + 1 : lt + 2] != "/":
                gt = text.find(">", j)
                if gt > j:
                    end = text.find("<", gt)
                    if end > gt:
                        val = text[gt + 1 : end].strip()
                        if val:
                            return val
            i = j + len(key)
    return ""


def probe_match_xml(
    *,
    xaddr: str,
    scopes: list[str],
    types: str = "dn:NetworkVideoTransmitter",
    relates_to: str = "",
    endpoint_uuid: str = "",
) -> bytes:
    msgid = f"uuid:{new_uuid()}"
    rel = relates_to or msgid
    epr = endpoint_uuid or new_uuid()
    scopes_s = " ".join(scopes)
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"
            xmlns:a="http://schemas.xmlsoap.org/ws/2004/08/addressing"
            xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery"
            xmlns:dn="http://www.onvif.org/ver10/network/wsdl"
            xmlns:tds="http://www.onvif.org/ver10/device/wsdl">
  <s:Header>
    <a:MessageID>{msgid}</a:MessageID>
    <a:RelatesTo>{rel}</a:RelatesTo>
    <a:To>http://schemas.xmlsoap.org/ws/2004/08/addressing/role/anonymous</a:To>
    <a:Action>http://schemas.xmlsoap.org/ws/2005/04/discovery/ProbeMatches</a:Action>
  </s:Header>
  <s:Body>
    <d:ProbeMatches>
      <d:ProbeMatch>
        <a:EndpointReference><a:Address>urn:uuid:{epr}</a:Address></a:EndpointReference>
        <d:Types>{types} tds:Device</d:Types>
        <d:Scopes>{scopes_s}</d:Scopes>
        <d:XAddrs>{xaddr}</d:XAddrs>
        <d:MetadataVersion>1</d:MetadataVersion>
      </d:ProbeMatch>
    </d:ProbeMatches>
  </s:Body>
</s:Envelope>"""
    return xml.encode("utf-8")


class DiscoveryProtocol(asyncio.DatagramProtocol):
    def __init__(self, runtime: Runtime):
        self.runtime = runtime
        self.transport: asyncio.DatagramTransport | None = None

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = transport  # type: ignore[assignment]

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        if not is_probe(data):
            return
        rt = self.runtime
        xaddr = f"{rt.base_url()}/onvif/device_service"
        epr = rt.state.data.endpoint_uuid or ""
        payload = probe_match_xml(
            xaddr=xaddr,
            scopes=rt.effective_scopes(),
            relates_to=extract_message_id(data),
            endpoint_uuid=epr,
        )
        if self.transport:
            self.transport.sendto(payload, addr)
            log.debug("ProbeMatch → %s:%s", addr[0], addr[1])


async def start_discovery(runtime: Runtime) -> tuple[asyncio.DatagramTransport, DiscoveryProtocol] | None:
    if not runtime.config.discovery:
        return None
    loop = asyncio.get_running_loop()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
    except (AttributeError, OSError):
        pass
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 4)
    try:
        sock.bind(("", WS_PORT))
    except OSError:
        log.warning("WS-Discovery port %s busy — discovery disabled", WS_PORT)
        sock.close()
        return None
    try:
        mreq = struct.pack("=4s4s", socket.inet_aton(WS_DISCOVERY), socket.inet_aton("0.0.0.0"))
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
    except OSError:
        log.warning("Cannot join multicast %s — discovery may miss probes", WS_DISCOVERY)
    sock.setblocking(False)
    transport, protocol = await loop.create_datagram_endpoint(lambda: DiscoveryProtocol(runtime), sock=sock)
    log.info("WS-Discovery listening on %s:%s", WS_DISCOVERY, WS_PORT)
    return transport, protocol
