from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from onvif_emu.models import AuthMode, Role, UserRecord
from onvif_emu.soap import SoapRequest, child_text, fbool, find_child, iter_local, soap_fault
from onvif_emu.timeutil import system_datetime_fields, xml_datetime

if TYPE_CHECKING:
    from onvif_emu.runtime import Runtime

log = logging.getLogger(__name__)


def xaddr(rt: Runtime, service: str) -> str:
    return f"{rt.base_url()}/onvif/{service}"


def handle_device(rt: Runtime, req: SoapRequest) -> str:
    a = req.action
    log.debug("Device action=%s", a)
    if a == "GetSystemDateAndTime":
        return _get_time(rt)
    if a == "SetSystemDateAndTime":
        return _set_time(rt, req)
    if a == "GetDeviceInformation":
        d = rt.config.device
        return (
            "<tds:GetDeviceInformationResponse>"
            f"<tds:Manufacturer>{_e(d.manufacturer)}</tds:Manufacturer>"
            f"<tds:Model>{_e(d.model)}</tds:Model>"
            f"<tds:FirmwareVersion>{_e(d.firmware)}</tds:FirmwareVersion>"
            f"<tds:SerialNumber>{_e(d.serial)}</tds:SerialNumber>"
            f"<tds:HardwareId>{_e(d.hardware_id)}</tds:HardwareId>"
            "</tds:GetDeviceInformationResponse>"
        )
    if a == "GetCapabilities":
        return _capabilities(rt)
    if a == "GetServices":
        return _services(rt)
    if a in {"GetScopes", "GetDiscoveryMode"}:
        if a == "GetDiscoveryMode":
            return (
                "<tds:GetDiscoveryModeResponse>"
                f"<tds:DiscoveryMode>{'Discoverable' if rt.config.discovery else 'NonDiscoverable'}</tds:DiscoveryMode>"
                "</tds:GetDiscoveryModeResponse>"
            )
        scopes = "".join(
            f"<tds:Scopes><tt:ScopeDef>Fixed</tt:ScopeDef><tt:ScopeItem>{_e(s)}</tt:ScopeItem></tds:Scopes>"
            for s in rt.effective_scopes()
        )
        return f"<tds:GetScopesResponse>{scopes}</tds:GetScopesResponse>"
    if a == "SetScopes":
        items = [el.text.strip() for el in iter_local(req.body, "ScopeItem") if el.text]
        if items:
            rt.state.data.scopes = items
            rt.state.mark_dirty()
        return "<tds:SetScopesResponse/>"
    if a == "GetHostname":
        hn = rt.state.data.hostname or rt.config.device.hostname
        return (
            "<tds:GetHostnameResponse><tds:HostnameInformation>"
            f"<tt:FromDHCP>false</tt:FromDHCP><tt:Name>{_e(hn)}</tt:Name>"
            "</tds:HostnameInformation></tds:GetHostnameResponse>"
        )
    if a == "SetHostname":
        name = child_text(req.body, "Name")
        if name:
            rt.state.data.hostname = name
            rt.config.device.hostname = name
            rt.state.mark_dirty()
        return "<tds:SetHostnameResponse/>"
    if a == "GetNetworkProtocols":
        n = rt.config.network
        return (
            "<tds:GetNetworkProtocolsResponse>"
            f"<tds:NetworkProtocols><tt:Name>HTTP</tt:Name><tt:Enabled>{fbool(n.http_enabled)}</tt:Enabled><tt:Port>{n.http_port}</tt:Port></tds:NetworkProtocols>"
            f"<tds:NetworkProtocols><tt:Name>RTSP</tt:Name><tt:Enabled>{fbool(n.rtsp_enabled)}</tt:Enabled><tt:Port>{n.rtsp_port}</tt:Port></tds:NetworkProtocols>"
            f"<tds:NetworkProtocols><tt:Name>HTTPS</tt:Name><tt:Enabled>false</tt:Enabled><tt:Port>{n.https_port}</tt:Port></tds:NetworkProtocols>"
            "</tds:GetNetworkProtocolsResponse>"
        )
    if a == "SetNetworkProtocols":
        return "<tds:SetNetworkProtocolsResponse/>"
    if a == "GetNTP":
        ntp = rt.config.ntp
        servers = "".join(f"<tt:NTPManual><tt:Type>DNS</tt:Type><tt:DNSname>{_e(s)}</tt:DNSname></tt:NTPManual>" for s in ntp.servers)
        return (
            "<tds:GetNTPResponse><tds:NTPInformation>"
            f"<tt:FromDHCP>false</tt:FromDHCP>{servers}"
            "</tds:NTPInformation></tds:GetNTPResponse>"
        )
    if a == "SetNTP":
        names = [el.text.strip() for el in iter_local(req.body, "DNSname") if el.text]
        if names:
            rt.config.ntp.servers = names
            rt.config.ntp.enabled = True
            rt.config.device.use_ntp = True
        return "<tds:SetNTPResponse/>"
    if a == "GetUsers":
        users = "".join(
            f"<tds:User><tt:Username>{_e(u.username)}</tt:Username><tt:UserLevel>{u.role.onvif_level}</tt:UserLevel></tds:User>"
            for u in rt.state.users.all()
        )
        return f"<tds:GetUsersResponse>{users}</tds:GetUsersResponse>"
    if a == "CreateUsers":
        return _create_users(rt, req)
    if a == "DeleteUsers":
        return _delete_users(rt, req)
    if a == "SetUser":
        return _set_user(rt, req)
    if a == "SystemReboot":
        rt.request_reboot()
        return (
            "<tds:SystemRebootResponse>"
            "<tds:Message>Stopping process in 3 seconds "
            "(supervisor may restart the service)</tds:Message>"
            "</tds:SystemRebootResponse>"
        )
    if a == "GetWsdlUrl":
        return f"<tds:GetWsdlUrlResponse><tds:WsdlUrl>{rt.base_url()}/onvif/</tds:WsdlUrl></tds:GetWsdlUrlResponse>"
    if a == "GetServiceCapabilities":
        inner = _device_service_capabilities(rt)
        return f"<tds:GetServiceCapabilitiesResponse>{inner}</tds:GetServiceCapabilitiesResponse>"
    if a in {"GetDNS", "GetNetworkDefaultGateway", "GetNetworkInterfaces"}:
        if a == "GetNetworkInterfaces":
            return (
                "<tds:GetNetworkInterfacesResponse><tds:NetworkInterfaces token=\"eth0\">"
                f"<tt:Enabled>true</tt:Enabled><tt:Info><tt:Name>eth0</tt:Name><tt:HwAddress>02:42:AC:11:{rt.config.instance // 256:02X}:{rt.config.instance % 256 + 1:02X}</tt:HwAddress><tt:MTU>1500</tt:MTU></tt:Info>"
                "<tt:IPv4><tt:Enabled>true</tt:Enabled><tt:Config>"
                f"<tt:Manual><tt:Address>{rt.public_host()}</tt:Address><tt:PrefixLength>24</tt:PrefixLength></tt:Manual>"
                "<tt:DHCP>false</tt:DHCP></tt:Config></tt:IPv4>"
                "</tds:NetworkInterfaces></tds:GetNetworkInterfacesResponse>"
            )
        return f"<tds:{a}Response/>"
    return _device_more(rt, req, a)


_DEVICE_EMPTY = {
    "GetSystemUris", "GetIPAddressFilter", "GetStorageConfigurations", "GetCertificates",
    "GetCACertificates", "GetClientCertificateMode", "GetPasswordComplexityConfiguration", "GetRemoteUser",
    "GetAccessPolicy", "GetDot1XConfigurations", "GetDot11Status", "GetPasswordHistoryConfiguration",
}


def _device_more(rt: Runtime, req: SoapRequest, a: str) -> str:
    if a == "GetRelayOutputs":
        items = "".join(
            f'<tds:RelayOutputs token="{r.token}"><tt:Properties><tt:Mode>{r.mode}</tt:Mode>'
            f"<tt:DelayTime>PT{r.delay_s:g}S</tt:DelayTime><tt:IdleState>{r.idle_state}</tt:IdleState>"
            "</tt:Properties></tds:RelayOutputs>"
            for r in rt.diag.relays.values()
        )
        return f"<tds:GetRelayOutputsResponse>{items}</tds:GetRelayOutputsResponse>"
    if a == "SetRelayOutputState":
        tok = child_text(req.body, "RelayOutputToken")
        state = child_text(req.body, "LogicalState").lower()
        if tok not in rt.diag.relays:
            return soap_fault("RelayToken", "Unknown relay output", "ter:InvalidArgVal")
        rt.diag.set_relay(tok, state == "active")
        return "<tds:SetRelayOutputStateResponse/>"
    if a == "SetRelayOutputSettings":
        tok = child_text(req.body, "RelayOutputToken")
        relay = rt.diag.relays.get(tok)
        if relay is None:
            return soap_fault("RelayToken", "Unknown relay output", "ter:InvalidArgVal")
        props = find_child(req.body, "Properties")
        mode = child_text(props, "Mode")
        if mode in {"Bistable", "Monostable"}:
            relay.mode = mode
        idle = child_text(props, "IdleState")
        if idle in {"open", "closed"}:
            relay.idle_state = idle
        delay = child_text(props, "DelayTime")
        if delay.startswith("PT") and delay.endswith("S"):
            try:
                relay.delay_s = float(delay[2:-1])
            except ValueError:
                pass
        return "<tds:SetRelayOutputSettingsResponse/>"
    if a == "GetDynamicDNS":
        return (
            "<tds:GetDynamicDNSResponse><tds:DynamicDNSInformation><tt:Type>NoUpdate</tt:Type>"
            "</tds:DynamicDNSInformation></tds:GetDynamicDNSResponse>"
        )
    if a == "GetZeroConfiguration":
        return (
            "<tds:GetZeroConfigurationResponse><tds:ZeroConfiguration><tt:InterfaceToken>eth0</tt:InterfaceToken>"
            "<tt:Enabled>false</tt:Enabled></tds:ZeroConfiguration></tds:GetZeroConfigurationResponse>"
        )
    if a == "GetEndpointReference":
        return (
            "<tds:GetEndpointReferenceResponse><tds:GUID>"
            f"urn:uuid:{rt.identity.endpoint_uuid() if hasattr(rt.identity, 'endpoint_uuid') else '00000000-0000-0000-0000-000000000001'}"
            "</tds:GUID></tds:GetEndpointReferenceResponse>"
        )
    if a == "GetSystemSupportInformation":
        d = rt.config.device
        return (
            "<tds:GetSystemSupportInformationResponse><tds:SupportInformation><tt:String>"
            f"{_e(d.manufacturer)} {_e(d.model)} fw {_e(d.firmware)} sn {_e(d.serial)} (emulator)"
            "</tt:String></tds:SupportInformation></tds:GetSystemSupportInformationResponse>"
        )
    if a == "GetSystemLog":
        return (
            "<tds:GetSystemLogResponse><tds:SystemLog><tt:String>onvif-emulator diagnostic device: no system log"
            "</tt:String></tds:SystemLog></tds:GetSystemLogResponse>"
        )
    if a == "GetSystemBackup":
        return "<tds:GetSystemBackupResponse/>"
    if a == "GetRemoteDiscoveryMode":
        return "<tds:GetRemoteDiscoveryModeResponse><tds:RemoteDiscoveryMode>NonDiscoverable</tds:RemoteDiscoveryMode></tds:GetRemoteDiscoveryModeResponse>"
    if a == "GetDot11Capabilities":
        return "<tds:GetDot11CapabilitiesResponse/>"
    if a in _DEVICE_EMPTY:
        return f"<tds:{a}Response/>"
    if a.startswith("Set") or a in {"StartFirmwareUpgrade", "StartSystemRestore", "SetHashingAlgorithm", "RemoveScopes", "AddScopes"}:
        return f"<tds:{a}Response/>"
    return soap_fault("ActionNotSupported", f"Unknown Device action {a}")


def _get_time(rt: Runtime) -> str:
    fields = system_datetime_fields(rt.config.device)
    utc_xml = xml_datetime(fields["utc"])  # type: ignore[arg-type]
    loc_xml = xml_datetime(fields["local"])  # type: ignore[arg-type]
    return (
        "<tds:GetSystemDateAndTimeResponse><tds:SystemDateAndTime>"
        f"<tt:DateTimeType>{fields['type']}</tt:DateTimeType>"
        f"<tt:DaylightSavings>{fbool(bool(fields['dst']))}</tt:DaylightSavings>"
        f"<tt:TimeZone><tt:TZ>{fields['tz']}</tt:TZ></tt:TimeZone>"
        f"<tt:UTCDateTime>{utc_xml}</tt:UTCDateTime>"
        f"<tt:LocalDateTime>{loc_xml}</tt:LocalDateTime>"
        "</tds:SystemDateAndTime></tds:GetSystemDateAndTimeResponse>"
    )


def _set_time(rt: Runtime, req: SoapRequest) -> str:
    tz = child_text(find_child(req.body, "TimeZone"), "TZ")
    if tz:
        # Accept POSIX or IANA; store as-is
        rt.config.device.timezone = tz if "/" in tz or tz.upper().startswith("UTC") else rt.config.device.timezone
    dst = child_text(req.body, "DaylightSavings")
    if dst:
        rt.config.device.daylight_savings = dst.lower() == "true"
    typ = child_text(req.body, "DateTimeType")
    if typ:
        rt.config.device.use_ntp = typ.upper() == "NTP"
    return "<tds:SetSystemDateAndTimeResponse/>"


def _capabilities(rt: Runtime) -> str:
    host = rt.base_url()
    return f"""<tds:GetCapabilitiesResponse><tds:Capabilities>
      <tt:Device>
        <tt:XAddr>{host}/onvif/device_service</tt:XAddr>
        <tt:Network><tt:IPFilter>false</tt:IPFilter><tt:ZeroConfiguration>false</tt:ZeroConfiguration>
          <tt:IPVersion6>false</tt:IPVersion6><tt:DynDNS>false</tt:DynDNS></tt:Network>
        <tt:System><tt:DiscoveryResolve>true</tt:DiscoveryResolve><tt:DiscoveryBye>true</tt:DiscoveryBye>
          <tt:RemoteDiscovery>false</tt:RemoteDiscovery><tt:SystemBackup>false</tt:SystemBackup>
          <tt:SystemLogging>true</tt:SystemLogging><tt:FirmwareUpgrade>false</tt:FirmwareUpgrade>
          <tt:SupportedVersions><tt:Major>2</tt:Major><tt:Minor>42</tt:Minor></tt:SupportedVersions>
        </tt:System>
        <tt:IO><tt:InputConnectors>{len(rt.diag.inputs)}</tt:InputConnectors><tt:RelayOutputs>{len(rt.diag.relays)}</tt:RelayOutputs></tt:IO>
        <tt:Security><tt:TLS1.1>false</tt:TLS1.1><tt:TLS1.2>false</tt:TLS1.2>
          <tt:OnboardKeyGeneration>false</tt:OnboardKeyGeneration><tt:AccessPolicyConfig>false</tt:AccessPolicyConfig>
          <tt:X.509Token>false</tt:X.509Token><tt:SAMLToken>false</tt:SAMLToken>
          <tt:KerberosToken>false</tt:KerberosToken><tt:RELToken>false</tt:RELToken>
          <tt:Extension><tt:TLS1.0>false</tt:TLS1.0></tt:Extension></tt:Security>
      </tt:Device>
      <tt:Events>
        <tt:XAddr>{host}/onvif/events_service</tt:XAddr>
        <tt:WSSubscriptionPolicySupport>true</tt:WSSubscriptionPolicySupport>
        <tt:WSPullPointSupport>true</tt:WSPullPointSupport>
        <tt:WSPausableSubscriptionManagerInterfaceSupport>false</tt:WSPausableSubscriptionManagerInterfaceSupport>
      </tt:Events>
      <tt:Imaging><tt:XAddr>{host}/onvif/imaging_service</tt:XAddr></tt:Imaging>
      <tt:Media>
        <tt:XAddr>{host}/onvif/media_service</tt:XAddr>
        <tt:StreamingCapabilities>
          <tt:RTPMulticast>{fbool(rt.config.multicast_stream)}</tt:RTPMulticast>
          <tt:RTP_TCP>true</tt:RTP_TCP>
          <tt:RTP_RTSP_TCP>true</tt:RTP_RTSP_TCP>
        </tt:StreamingCapabilities>
        <tt:Extension><tt:ProfileCapabilities><tt:MaximumNumberOfProfiles>64</tt:MaximumNumberOfProfiles></tt:ProfileCapabilities></tt:Extension>
      </tt:Media>
      <tt:PTZ><tt:XAddr>{host}/onvif/ptz_service</tt:XAddr></tt:PTZ>
      <tt:Extension>
        <tt:AnalyticsDevice>
          <tt:XAddr>{host}/onvif/analytics_service</tt:XAddr>
          <tt:RuleSupport>true</tt:RuleSupport>
          <tt:AnalyticsModuleSupport>true</tt:AnalyticsModuleSupport>
        </tt:AnalyticsDevice>
      </tt:Extension>
    </tds:Capabilities></tds:GetCapabilitiesResponse>"""


def _security_capability_attrs(rt: Runtime) -> str:
    mode = rt.config.auth.mode
    user_token = "false" if mode == AuthMode.none else "true"
    http_digest = "true" if mode == AuthMode.digest else "false"
    return (
        'TLS1.0="false" TLS1.1="false" TLS1.2="false" OnboardKeyGeneration="false" '
        'AccessPolicyConfig="false" DefaultAccessPolicy="true" '
        f'UsernameToken="{user_token}" HttpDigest="{http_digest}" RELToken="false"'
    )


def _device_service_capabilities(rt: Runtime) -> str:
    return (
        "<tds:Capabilities>"
        '<tds:Network IPFilter="false" ZeroConfiguration="false" IPVersion6="false" DynDNS="false" '
        'Dot11Configuration="false" HostnameFromDHCP="false" NTP="1" DHCPv6="false"/>'
        f"<tds:Security {_security_capability_attrs(rt)}/>"
        '<tds:System DiscoveryResolve="true" DiscoveryBye="true" RemoteDiscovery="false" '
        'SystemBackup="false" SystemLogging="true" FirmwareUpgrade="false" HttpFirmwareUpgrade="false" '
        'HttpSystemBackup="false" HttpSystemLogging="false" HttpSupportInformation="false"/>'
        "</tds:Capabilities>"
    )


def _services(rt: Runtime) -> str:
    host = rt.base_url()
    items = [
        (
            "http://www.onvif.org/ver10/device/wsdl",
            f"{host}/onvif/device_service",
            2,
            42,
            _device_service_capabilities(rt),
        ),
        ("http://www.onvif.org/ver10/media/wsdl", f"{host}/onvif/media_service", 2, 10, ""),
        ("http://www.onvif.org/ver20/ptz/wsdl", f"{host}/onvif/ptz_service", 2, 14, ""),
        ("http://www.onvif.org/ver20/imaging/wsdl", f"{host}/onvif/imaging_service", 2, 14, ""),
        ("http://www.onvif.org/ver10/events/wsdl", f"{host}/onvif/events_service", 2, 21, ""),
        ("http://www.onvif.org/ver20/analytics/wsdl", f"{host}/onvif/analytics_service", 2, 14, ""),
    ]
    body = []
    for ns, addr, maj, minor, caps in items:
        cap_xml = f"<tds:Capabilities>{caps}</tds:Capabilities>" if caps else ""
        body.append(
            f"<tds:Service><tds:Namespace>{ns}</tds:Namespace><tds:XAddr>{addr}</tds:XAddr>"
            f"{cap_xml}"
            f"<tds:Version><tt:Major>{maj}</tt:Major><tt:Minor>{minor}</tt:Minor></tds:Version></tds:Service>"
        )
    return f"<tds:GetServicesResponse>{''.join(body)}</tds:GetServicesResponse>"


def _create_users(rt: Runtime, req: SoapRequest) -> str:
    from onvif_emu.users import UserError

    for el in iter_local(req.body, "User"):
        name = child_text(el, "Username")
        pwd = child_text(el, "Password")
        if not name:
            continue
        role = Role.from_any(child_text(el, "UserLevel", "User"), Role.Viewer)
        try:
            rt.state.users.upsert(UserRecord(username=name, password=pwd or "", role=role), create=True)
        except UserError as exc:
            return soap_fault("OperationProhibited", str(exc), "ter:UsernameClash")
    return "<tds:CreateUsersResponse/>"


def _delete_users(rt: Runtime, req: SoapRequest) -> str:
    from onvif_emu.users import UserError

    for el in iter_local(req.body, "Username"):
        name = (el.text or "").strip()
        if not name:
            continue
        try:
            rt.state.users.delete(name)
        except UserError as exc:
            return soap_fault("OperationProhibited", str(exc), "ter:FixedUser")
    return "<tds:DeleteUsersResponse/>"


def _set_user(rt: Runtime, req: SoapRequest) -> str:
    from onvif_emu.users import UserError

    for el in iter_local(req.body, "User"):
        name = child_text(el, "Username")
        pwd = child_text(el, "Password")
        level = child_text(el, "UserLevel", "")
        existing = rt.state.users.find(name)
        if not existing:
            return soap_fault("InvalidArgVal", f"no user {name}", "ter:UsernameMissing")
        changes: dict = {}
        if pwd:
            changes["password"] = pwd
        if level and name != "admin":
            changes["role"] = Role.from_any(level, existing.role)
        try:
            rt.state.users.update(name, **changes)
        except UserError as exc:
            return soap_fault("OperationProhibited", str(exc), "ter:FixedUser")
    return "<tds:SetUserResponse/>"


def _e(text: str) -> str:
    return (
        str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )
