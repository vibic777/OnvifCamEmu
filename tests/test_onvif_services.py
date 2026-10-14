from onvif_emu.onvif_dispatch import dispatch
from onvif_emu.services.media import profile_token
from onvif_emu.soap import parse_envelope


def _req(action: str, inner: str = ""):
    xml = f"""<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope">
      <s:Body><{action} xmlns="http://www.onvif.org/ver10/device/wsdl">{inner}</{action}></s:Body></s:Envelope>"""
    return parse_envelope(xml)


def test_get_device_information(runtime):
    xml = dispatch(runtime, _req("GetDeviceInformation"), "/onvif/device_service")
    assert "EMU-CAM-1" in xml
    assert "Vibic" in xml


def test_get_system_date_and_time(runtime):
    xml = dispatch(runtime, _req("GetSystemDateAndTime"), "/onvif/device_service")
    assert "UTCDateTime" in xml
    assert "LocalDateTime" in xml
    assert "TimeZone" in xml


def test_get_capabilities_xaddrs(runtime):
    xml = dispatch(runtime, _req("GetCapabilities"), "/onvif/device_service")
    assert "device_service" in xml
    assert "media_service" in xml
    assert "ptz_service" in xml
    assert "events_service" in xml


def test_get_service_capabilities_security(runtime):
    xml = dispatch(runtime, _req("GetServiceCapabilities"), "/onvif/device_service")
    assert "UsernameToken=" in xml
    assert "HttpDigest=" in xml
    services = dispatch(runtime, _req("GetServices"), "/onvif/device_service")
    assert "UsernameToken=" in services
    assert "HttpDigest=" in services


def test_profiles_and_stream_uri(runtime):
    xml = dispatch(runtime, _req("GetProfiles"), "/onvif/media_service")
    token = profile_token(runtime.config.channels[0])
    assert token in xml
    req = parse_envelope(
        f"""<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope" xmlns:trt="http://www.onvif.org/ver10/media/wsdl">
        <s:Body><trt:GetStreamUri><trt:ProfileToken>{token}</trt:ProfileToken>
        <trt:StreamSetup><tt:Stream xmlns:tt="http://www.onvif.org/ver10/schema">RTP-Unicast</tt:Stream>
        <tt:Transport xmlns:tt="http://www.onvif.org/ver10/schema"><tt:Protocol>RTSP</tt:Protocol></tt:Transport>
        </trt:StreamSetup></trt:GetStreamUri></s:Body></s:Envelope>"""
    )
    out = dispatch(runtime, req, "/onvif/media_service")
    assert "rtsp://" in out
    assert "ch1" in out
    assert "admin:admin@" not in out
    runtime.config.auth.embed_rtsp_credentials = True
    out2 = dispatch(runtime, req, "/onvif/media_service")
    assert "admin:admin@" in out2


def test_admin_is_never_deleted_other_users_are(runtime):
    from onvif_emu.models import Role, UserRecord

    runtime.state.upsert_user(UserRecord(username="op1", password="p", role=Role.Operator))
    runtime.state.upsert_user(UserRecord(username="admin2", password="p", role=Role.Administrator))
    out = dispatch(runtime, _req("DeleteUsers", "<Username>admin</Username>"), "/onvif/device_service")
    assert "OperationProhibited" in out and runtime.state.find_user("admin") is not None
    dispatch(runtime, _req("DeleteUsers", "<Username>op1</Username>"), "/onvif/device_service")
    assert runtime.state.find_user("op1") is None
    users = dispatch(runtime, _req("GetUsers"), "/onvif/device_service")
    assert "<tt:UserLevel>Administrator</tt:UserLevel>" in users


def test_ptz_move_and_status(runtime):
    cid = runtime.config.channels[0].id
    req = parse_envelope(
        """<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope" xmlns:tptz="http://www.onvif.org/ver20/ptz/wsdl" xmlns:tt="http://www.onvif.org/ver10/schema">
        <s:Body><tptz:AbsoluteMove>
          <tptz:ProfileToken>profile_1</tptz:ProfileToken>
          <tptz:Position><tt:PanTilt x="12" y="3"/><tt:Zoom x="0.2"/></tptz:Position>
        </tptz:AbsoluteMove></s:Body></s:Envelope>"""
    )
    out = dispatch(runtime, req, "/onvif/ptz_service")
    assert "AbsoluteMoveResponse" in out
    servo = runtime.ensure_servo(cid)
    assert servo.pan.target == 12 or abs(servo.pan.value - 12) < 20
    st = dispatch(runtime, parse_envelope(
        """<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope" xmlns:tptz="http://www.onvif.org/ver20/ptz/wsdl">
        <s:Body><tptz:GetStatus><tptz:ProfileToken>profile_1</tptz:ProfileToken></tptz:GetStatus></s:Body></s:Envelope>"""
    ), "/onvif/ptz_service")
    assert "PTZStatus" in st
    assert "PanTilt" in st


def test_presets_roundtrip(runtime):
    runtime.ensure_servo(1).absolute_move(1.0, 2.0, 0.3)
    runtime.ensure_servo(1).pan.value = 1.0
    runtime.ensure_servo(1).tilt.value = 2.0
    runtime.ensure_servo(1).zoom.value = 0.3
    setp = parse_envelope(
        """<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope" xmlns:tptz="http://www.onvif.org/ver20/ptz/wsdl">
        <s:Body><tptz:SetPreset><tptz:ProfileToken>profile_1</tptz:ProfileToken>
        <tptz:PresetName>Home</tptz:PresetName></tptz:SetPreset></s:Body></s:Envelope>"""
    )
    out = dispatch(runtime, setp, "/onvif/ptz_service")
    assert "PresetToken" in out
    got = dispatch(runtime, parse_envelope(
        """<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope" xmlns:tptz="http://www.onvif.org/ver20/ptz/wsdl">
        <s:Body><tptz:GetPresets><tptz:ProfileToken>profile_1</tptz:ProfileToken></tptz:GetPresets></s:Body></s:Envelope>"""
    ), "/onvif/ptz_service")
    assert "Home" in got


def test_events_pullpoint(runtime):
    cr = dispatch(runtime, parse_envelope(
        """<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope" xmlns:tev="http://www.onvif.org/ver10/events/wsdl">
        <s:Body><tev:CreatePullPointSubscription>
        <tev:InitialTerminationTime>PT60S</tev:InitialTerminationTime>
        </tev:CreatePullPointSubscription></s:Body></s:Envelope>"""
    ), "/onvif/events_service")
    assert "SubscriptionReference" in cr
    runtime.events.trigger("tns1:VideoSource/MotionAlarm", True)
    pull = dispatch(runtime, parse_envelope(
        """<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope" xmlns:tev="http://www.onvif.org/ver10/events/wsdl">
        <s:Body><tev:PullMessages><tev:Timeout>PT1S</tev:Timeout><tev:MessageLimit>10</tev:MessageLimit></tev:PullMessages></s:Body></s:Envelope>"""
    ), "/onvif/events_service")
    assert "MotionAlarm" in pull


def test_imaging_and_analytics(runtime):
    img = dispatch(runtime, parse_envelope(
        """<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope" xmlns:timg="http://www.onvif.org/ver20/imaging/wsdl">
        <s:Body><timg:GetImagingSettings/></s:Body></s:Envelope>"""
    ), "/onvif/imaging_service")
    assert "Brightness" in img
    an = dispatch(runtime, parse_envelope(
        """<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope" xmlns:tan="http://www.onvif.org/ver20/analytics/wsdl">
        <s:Body><tan:GetAnalyticsModules/></s:Body></s:Envelope>"""
    ), "/onvif/analytics_service")
    assert "MotionDetector" in an or "FieldDetector" in an
    md = dispatch(runtime, parse_envelope(
        """<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope" xmlns:tan="http://www.onvif.org/ver20/analytics/wsdl">
        <s:Body><tan:GetMetadata/></s:Body></s:Envelope>"""
    ), "/onvif/analytics_service")
    assert "BoundingBox" in md


def test_discovery_probe_match(runtime):
    from onvif_emu.discovery import extract_message_id, is_probe, probe_match_xml

    probe = b"""<?xml version="1.0"?><s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"
      xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery">
      <s:Header><a:MessageID xmlns:a="http://schemas.xmlsoap.org/ws/2004/08/addressing">uuid:abc</a:MessageID></s:Header>
      <s:Body><d:Probe/></s:Body></s:Envelope>"""
    assert is_probe(probe)
    assert extract_message_id(probe) == "uuid:abc"
    match = probe_match_xml(xaddr="http://127.0.0.1:8080/onvif/device_service", scopes=runtime.effective_scopes(), relates_to="uuid:abc")
    assert b"ProbeMatch" in match
    assert b"Profile/Streaming" in match
    assert b"uuid:abc" in match


def test_device_users_scopes_network(runtime):
    users = dispatch(runtime, _req("GetUsers"), "/onvif/device_service")
    assert "admin" in users
    scopes = dispatch(runtime, _req("GetScopes"), "/onvif/device_service")
    assert "Profile/Streaming" in scopes
    host = dispatch(runtime, _req("GetHostname"), "/onvif/device_service")
    assert "HostnameInformation" in host
    net = dispatch(runtime, _req("GetNetworkProtocols"), "/onvif/device_service")
    assert "RTSP" in net
    ntp = dispatch(runtime, _req("GetNTP"), "/onvif/device_service")
    assert "NTPInformation" in ntp
    svc = dispatch(runtime, _req("GetServices"), "/onvif/device_service")
    assert "device/wsdl" in svc
    create = dispatch(
        runtime,
        _req(
            "CreateUsers",
            "<User><Username>op1</Username><Password>p</Password><UserLevel>Operator</UserLevel></User>",
        ),
        "/onvif/device_service",
    )
    assert "CreateUsersResponse" in create
    assert runtime.state.find_user("op1") is not None
    setsc = dispatch(
        runtime,
        _req("SetScopes", "<Scopes>onvif://www.onvif.org/name/LabCam</Scopes><ScopeItem>onvif://www.onvif.org/name/LabCam</ScopeItem>"),
        "/onvif/device_service",
    )
    assert "SetScopesResponse" in setsc


def test_snapshot_uri_and_continuous_stop(runtime):
    snap = dispatch(
        runtime,
        parse_envelope(
            """<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope" xmlns:trt="http://www.onvif.org/ver10/media/wsdl">
            <s:Body><trt:GetSnapshotUri><trt:ProfileToken>profile_1</trt:ProfileToken></trt:GetSnapshotUri></s:Body></s:Envelope>"""
        ),
        "/onvif/media_service",
    )
    assert "/snapshot/" in snap
    sources = dispatch(runtime, parse_envelope(
        """<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope" xmlns:trt="http://www.onvif.org/ver10/media/wsdl">
        <s:Body><trt:GetVideoSources/></s:Body></s:Envelope>"""
    ), "/onvif/media_service")
    assert "VideoSources" in sources
    cont = dispatch(
        runtime,
        parse_envelope(
            """<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope" xmlns:tptz="http://www.onvif.org/ver20/ptz/wsdl" xmlns:tt="http://www.onvif.org/ver10/schema">
            <s:Body><tptz:ContinuousMove><tptz:ProfileToken>profile_1</tptz:ProfileToken>
            <tptz:Velocity><tt:PanTilt x="0.5" y="0"/><tt:Zoom x="0"/></tptz:Velocity>
            </tptz:ContinuousMove></s:Body></s:Envelope>"""
        ),
        "/onvif/ptz_service",
    )
    assert "ContinuousMoveResponse" in cont
    stop = dispatch(
        runtime,
        parse_envelope(
            """<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope" xmlns:tptz="http://www.onvif.org/ver20/ptz/wsdl">
            <s:Body><tptz:Stop><tptz:ProfileToken>profile_1</tptz:ProfileToken>
            <tptz:PanTilt>true</tptz:PanTilt><tptz:Zoom>true</tptz:Zoom></tptz:Stop></s:Body></s:Envelope>"""
        ),
        "/onvif/ptz_service",
    )
    assert "StopResponse" in stop
    nodes = dispatch(runtime, parse_envelope(
        """<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope" xmlns:tptz="http://www.onvif.org/ver20/ptz/wsdl">
        <s:Body><tptz:GetNodes/></s:Body></s:Envelope>"""
    ), "/onvif/ptz_service")
    assert "PTZNode" in nodes


def test_imaging_set_and_event_properties(runtime):
    img = dispatch(
        runtime,
        parse_envelope(
            """<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope" xmlns:timg="http://www.onvif.org/ver20/imaging/wsdl" xmlns:tt="http://www.onvif.org/ver10/schema">
            <s:Body><timg:SetImagingSettings><timg:ImagingSettings>
            <tt:Brightness>70</tt:Brightness><tt:Contrast>40</tt:Contrast>
            </timg:ImagingSettings></timg:SetImagingSettings></s:Body></s:Envelope>"""
        ),
        "/onvif/imaging_service",
    )
    assert "SetImagingSettingsResponse" in img
    assert runtime.config.imaging.brightness == 70
    opt = dispatch(runtime, parse_envelope(
        """<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope" xmlns:timg="http://www.onvif.org/ver20/imaging/wsdl">
        <s:Body><timg:GetOptions/></s:Body></s:Envelope>"""
    ), "/onvif/imaging_service")
    assert "ImagingOptions" in opt
    props = dispatch(runtime, parse_envelope(
        """<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope" xmlns:tev="http://www.onvif.org/ver10/events/wsdl">
        <s:Body><tev:GetEventProperties/></s:Body></s:Envelope>"""
    ), "/onvif/events_service")
    assert "MotionAlarm" in props
    rules = dispatch(runtime, parse_envelope(
        """<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope" xmlns:tan="http://www.onvif.org/ver20/analytics/wsdl">
        <s:Body><tan:GetRules/></s:Body></s:Envelope>"""
    ), "/onvif/analytics_service")
    assert "MotionAlarm" in rules

