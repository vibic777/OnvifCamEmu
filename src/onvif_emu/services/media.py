from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from onvif_emu.models import ChannelConfig, Codec
from onvif_emu.soap import child_text, find_child, iter_local, soap_fault

if TYPE_CHECKING:
    from onvif_emu.runtime import Runtime
    from onvif_emu.soap import SoapRequest

log = logging.getLogger(__name__)


def _e(t: str) -> str:
    return str(t).replace("&", "&amp;").replace("<", "&lt;")


SUFFIX = ("", "_sub", "_third")
TIER_NAME = ("full", "medium", "low")


def profile_token(ch: ChannelConfig, sub: bool | int = False) -> str:
    return f"profile_{ch.id}{SUFFIX[int(sub)]}"


def tiers(ch: ChannelConfig) -> list[int]:
    """Enabled stream tiers of a channel: [0], [0, 1] or [0, 1, 2]."""
    out = [0]
    if ch.sub.enabled:
        out.append(1)
    if ch.third.enabled:
        out.append(2)
    return out


def vs_token(ch: ChannelConfig) -> str:
    return f"vs_{ch.id}"


def vec_token(ch: ChannelConfig, sub: bool | int = False) -> str:
    return f"vec_{ch.id}{SUFFIX[int(sub)]}"


def encoding_name(codec: Codec) -> str:
    # ver10 Media has no H265 enum value (that is Media2); a strict client would reject it.
    return {"h264": "H264", "h265": "H264", "mjpeg": "JPEG"}[codec.value]


def handle_media(rt: Runtime, req: SoapRequest) -> str:
    a = req.action
    log.debug("Media action=%s", a)
    if a == "GetProfiles":
        return _get_profiles(rt)
    if a == "GetProfile":
        token = child_text(req.body, "ProfileToken")
        xml = _one_profile_by_token(rt, token)
        if not xml:
            return soap_fault("NoProfile", "Unknown profile", "ter:InvalidArgVal")
        return f"<trt:GetProfileResponse>{xml.replace('trt:Profiles', 'trt:Profile')}</trt:GetProfileResponse>"
    if a == "GetVideoSources":
        items = []
        for ch in rt.enabled_channels():
            items.append(
                f"<trt:VideoSources token=\"{vs_token(ch)}\">"
                f"<tt:Framerate>{ch.encoder.fps}</tt:Framerate>"
                f"<tt:Resolution><tt:Width>{ch.encoder.width}</tt:Width><tt:Height>{ch.encoder.height}</tt:Height></tt:Resolution>"
                "</trt:VideoSources>"
            )
        return f"<trt:GetVideoSourcesResponse>{''.join(items)}</trt:GetVideoSourcesResponse>"
    if a == "GetVideoSourceConfigurations":
        items = []
        for ch in rt.enabled_channels():
            items.append(_vsc(ch))
        return f"<trt:GetVideoSourceConfigurationsResponse>{''.join(items)}</trt:GetVideoSourceConfigurationsResponse>"
    if a == "GetVideoEncoderConfigurations":
        items = []
        for ch in rt.enabled_channels():
            items.extend(_vec(ch, t) for t in tiers(ch))
        return f"<trt:GetVideoEncoderConfigurationsResponse>{''.join(items)}</trt:GetVideoEncoderConfigurationsResponse>"
    if a == "GetStreamUri":
        return _stream_uri(rt, req)
    if a == "GetSnapshotUri":
        return _snapshot_uri(rt, req)
    if a == "GetAudioSources":
        return "<trt:GetAudioSourcesResponse/>"
    if a == "GetGuaranteedNumberOfVideoEncoderInstances":
        return (
            "<trt:GetGuaranteedNumberOfVideoEncoderInstancesResponse>"
            "<trt:TotalNumber>2</trt:TotalNumber><trt:H264>2</trt:H264>"
            "</trt:GetGuaranteedNumberOfVideoEncoderInstancesResponse>"
        )
    if a == "GetVideoSourceConfiguration":
        token = child_text(req.body, "ConfigurationToken") or child_text(req.body, "VideoSourceConfigurationToken")
        for ch in rt.enabled_channels():
            if vs_token(ch) == token or f"vsc_{ch.id}" == token:
                inner = _vsc(ch).replace("trt:Configurations", "trt:Configuration")
                return f"<trt:GetVideoSourceConfigurationResponse>{inner}</trt:GetVideoSourceConfigurationResponse>"
        return soap_fault("NoConfig", "Unknown VSC", "ter:InvalidArgVal")
    if a == "GetCompatibleVideoEncoderConfigurations":
        ch = rt.enabled_channels()[0] if rt.enabled_channels() else None
        if not ch:
            return "<trt:GetCompatibleVideoEncoderConfigurationsResponse/>"
        return (
            "<trt:GetCompatibleVideoEncoderConfigurationsResponse>"
            f"{_vec(ch, False)}</trt:GetCompatibleVideoEncoderConfigurationsResponse>"
        )
    return _more(rt, req, a)


def _vsc(ch: ChannelConfig) -> str:
    return (
        f"<trt:Configurations token=\"vsc_{ch.id}\">"
        f"<tt:Name>VSC_{ch.id}</tt:Name><tt:UseCount>1</tt:UseCount>"
        f"<tt:SourceToken>{vs_token(ch)}</tt:SourceToken>"
        f"<tt:Bounds x=\"0\" y=\"0\" width=\"{ch.encoder.width}\" height=\"{ch.encoder.height}\"/>"
        "</trt:Configurations>"
    )


def _vec(ch: ChannelConfig, sub: bool | int) -> str:
    t = (ch.encoder, ch.sub, ch.third)[int(sub)]
    w, h, fps, br, gop, codec = t.width, t.height, t.fps, t.bitrate, t.gop, t.codec
    enc = encoding_name(codec)
    token = vec_token(ch, sub)
    name = f"{ch.name} {TIER_NAME[int(sub)]}"
    extra = ""
    if enc == "H264":
        extra = (
            f"<tt:H264><tt:GovLength>{gop}</tt:GovLength><tt:H264Profile>Main</tt:H264Profile></tt:H264>"
        )
    elif enc == "H265":
        extra = (
            f"<tt:H265><tt:GovLength>{gop}</tt:GovLength><tt:H265Profile>Main</tt:H265Profile></tt:H265>"
        )
    return (
        f"<trt:Configurations token=\"{token}\">"
        f"<tt:Name>{_e(name)}</tt:Name><tt:UseCount>1</tt:UseCount>"
        f"<tt:Encoding>{enc}</tt:Encoding>"
        f"<tt:Resolution><tt:Width>{w}</tt:Width><tt:Height>{h}</tt:Height></tt:Resolution>"
        f"<tt:Quality>4</tt:Quality>"
        f"<tt:RateControl><tt:FrameRateLimit>{fps}</tt:FrameRateLimit>"
        f"<tt:EncodingInterval>1</tt:EncodingInterval><tt:BitrateLimit>{br}</tt:BitrateLimit></tt:RateControl>"
        f"{extra}<tt:Multicast><tt:Address><tt:Type>IPv4</tt:Type><tt:IPv4Address>0.0.0.0</tt:IPv4Address></tt:Address>"
        f"<tt:Port>0</tt:Port><tt:TTL>1</tt:TTL><tt:AutoStart>false</tt:AutoStart></tt:Multicast>"
        f"<tt:SessionTimeout>PT60S</tt:SessionTimeout></trt:Configurations>"
    )


def _profile_xml(ch: ChannelConfig, sub: bool | int = False) -> str:
    token = profile_token(ch, sub)
    name = f"{ch.name} {TIER_NAME[int(sub)]}"
    t = (ch.encoder, ch.sub, ch.third)[int(sub)]
    w, h = t.width, t.height
    ptz_xml = _ptz_config_xml(ch) if ch.ptz_enabled else ""
    return f"""<trt:Profiles token="{token}" fixed="true">
      <tt:Name>{_e(name)}</tt:Name>
      <tt:VideoSourceConfiguration token="vsc_{ch.id}">
        <tt:Name>VSC_{ch.id}</tt:Name><tt:UseCount>1</tt:UseCount>
        <tt:SourceToken>{vs_token(ch)}</tt:SourceToken>
        <tt:Bounds x="0" y="0" width="{w}" height="{h}"/>
      </tt:VideoSourceConfiguration>
      {_vec(ch, sub).replace('trt:Configurations', 'tt:VideoEncoderConfiguration')}
      {_vac(ch, 'tt:VideoAnalyticsConfiguration')}
      {ptz_xml}
      {_meta(ch, 'tt:MetadataConfiguration')}
    </trt:Profiles>"""


def _ptz_config_xml(ch: ChannelConfig) -> str:
    return f"""<tt:PTZConfiguration token="ptz_{ch.id}">
        <tt:Name>PTZ_{ch.id}</tt:Name><tt:UseCount>1</tt:UseCount>
        <tt:NodeToken>ptz_node_{ch.id}</tt:NodeToken>
        <tt:DefaultAbsolutePantTiltPositionSpace>http://www.onvif.org/ver10/tptz/PanTiltSpaces/PositionGenericSpace</tt:DefaultAbsolutePantTiltPositionSpace>
        <tt:DefaultAbsoluteZoomPositionSpace>http://www.onvif.org/ver10/tptz/ZoomSpaces/PositionGenericSpace</tt:DefaultAbsoluteZoomPositionSpace>
        <tt:DefaultRelativePanTiltTranslationSpace>http://www.onvif.org/ver10/tptz/PanTiltSpaces/TranslationGenericSpace</tt:DefaultRelativePanTiltTranslationSpace>
        <tt:DefaultRelativeZoomTranslationSpace>http://www.onvif.org/ver10/tptz/ZoomSpaces/TranslationGenericSpace</tt:DefaultRelativeZoomTranslationSpace>
        <tt:DefaultContinuousPanTiltVelocitySpace>http://www.onvif.org/ver10/tptz/PanTiltSpaces/VelocityGenericSpace</tt:DefaultContinuousPanTiltVelocitySpace>
        <tt:DefaultContinuousZoomVelocitySpace>http://www.onvif.org/ver10/tptz/ZoomSpaces/VelocityGenericSpace</tt:DefaultContinuousZoomVelocitySpace>
        <tt:DefaultPTZSpeed>
          <tt:PanTilt x="0.5" y="0.5" space="http://www.onvif.org/ver10/tptz/PanTiltSpaces/GenericSpeedSpace"/>
          <tt:Zoom x="0.5" space="http://www.onvif.org/ver10/tptz/ZoomSpaces/ZoomGenericSpeedSpace"/>
        </tt:DefaultPTZSpeed>
        <tt:DefaultPTZTimeout>PT5S</tt:DefaultPTZTimeout>
      </tt:PTZConfiguration>"""


def _get_profiles(rt: Runtime) -> str:
    parts = []
    for ch in rt.enabled_channels():
        parts.extend(_profile_xml(ch, t) for t in tiers(ch))
    return f"<trt:GetProfilesResponse>{''.join(parts)}</trt:GetProfilesResponse>"


def _one_profile_by_token(rt: Runtime, token: str) -> str:
    for ch in rt.enabled_channels():
        for t in tiers(ch):
            if profile_token(ch, t) == token:
                return _profile_xml(ch, t)
    return ""


def _channel_from_profile(rt: Runtime, token: str) -> tuple[ChannelConfig | None, int]:
    for ch in rt.enabled_channels():
        for t in tiers(ch):
            if token == profile_token(ch, t):
                return ch, t
    if rt.enabled_channels():
        return rt.enabled_channels()[0], 0
    return None, 0


def _stream_uri(rt: Runtime, req: SoapRequest) -> str:
    token = child_text(req.body, "ProfileToken")
    ch, sub = _channel_from_profile(rt, token)
    if not ch:
        return soap_fault("NoProfile", "No channel", "ter:InvalidArgVal")
    ch.rtsp_path + ("_sub" if sub else "")
    setup = find_child(req.body, "StreamSetup")
    if setup is None:
        setup = req.body
    child_text(setup, "Transport")
    protocol = "RTSP"
    for el in iter_local(req.body, "Protocol"):
        if el.text:
            protocol = el.text.strip()
    uri = rt.stream_uri(ch, sub=sub, multicast=rt.config.multicast_stream and protocol.upper() == "UDP")
    return (
        "<trt:GetStreamUriResponse><trt:MediaUri>"
        f"<tt:Uri>{_e(uri)}</tt:Uri>"
        "<tt:InvalidAfterConnect>false</tt:InvalidAfterConnect>"
        "<tt:InvalidAfterReboot>false</tt:InvalidAfterReboot>"
        "<tt:Timeout>PT60S</tt:Timeout>"
        "</trt:MediaUri></trt:GetStreamUriResponse>"
    )


def _snapshot_uri(rt: Runtime, req: SoapRequest) -> str:
    token = child_text(req.body, "ProfileToken")
    ch, _ = _channel_from_profile(rt, token)
    cid = ch.id if ch else 1
    uri = f"{rt.base_url()}/snapshot/{cid}.jpg"
    return (
        "<trt:GetSnapshotUriResponse><trt:MediaUri>"
        f"<tt:Uri>{_e(uri)}</tt:Uri>"
        "<tt:InvalidAfterConnect>false</tt:InvalidAfterConnect>"
        "<tt:InvalidAfterReboot>false</tt:InvalidAfterReboot>"
        "<tt:Timeout>PT60S</tt:Timeout>"
        "</trt:MediaUri></trt:GetSnapshotUriResponse>"
    )


_EMPTY_OK = {
    # operations a fixed-profile emulator accepts and ignores (real fixed cameras answer the same way)
    "AddVideoSourceConfiguration", "AddVideoEncoderConfiguration", "AddPTZConfiguration",
    "AddMetadataConfiguration", "AddVideoAnalyticsConfiguration", "AddAudioSourceConfiguration",
    "AddAudioEncoderConfiguration", "RemoveVideoSourceConfiguration", "RemoveVideoEncoderConfiguration",
    "RemovePTZConfiguration", "RemoveMetadataConfiguration", "RemoveVideoAnalyticsConfiguration",
    "RemoveAudioSourceConfiguration", "RemoveAudioEncoderConfiguration", "SetVideoSourceConfiguration",
    "SetMetadataConfiguration", "SetVideoAnalyticsConfiguration", "StartMulticastStreaming",
    "StopMulticastStreaming", "SetSynchronizationPoint",
}
_EMPTY_LISTS = {
    "GetAudioSourceConfigurations", "GetAudioEncoderConfigurations", "GetAudioOutputs",
    "GetAudioOutputConfigurations", "GetAudioDecoderConfigurations", "GetOSDs",
    "GetCompatibleAudioSourceConfigurations", "GetCompatibleAudioEncoderConfigurations",
    "GetCompatibleMetadataConfigurations", "GetCompatibleAudioOutputConfigurations",
    "GetCompatibleAudioDecoderConfigurations", "GetCompatibleVideoAnalyticsConfigurations",
}


def meta_token(ch: ChannelConfig) -> str:
    return f"meta_{ch.id}"


def vac_token(ch: ChannelConfig) -> str:
    return f"vac_{ch.id}"


def _find_channel(rt: Runtime, token: str, *prefixes: str) -> tuple[ChannelConfig | None, int]:
    for ch in rt.enabled_channels():
        for pref in prefixes:
            for t, suf in enumerate(SUFFIX):
                if token == f"{pref}_{ch.id}{suf}":
                    return ch, t
    return None, 0


def _vac(ch: ChannelConfig, tag: str) -> str:
    return (
        f"<{tag} token=\"{vac_token(ch)}\"><tt:Name>Analytics_{ch.id}</tt:Name><tt:UseCount>1</tt:UseCount>"
        "<tt:AnalyticsEngineConfiguration>"
        "<tt:AnalyticsModule Name=\"MotionDetector\" Type=\"tt:CellMotionEngine\"><tt:Parameters/></tt:AnalyticsModule>"
        "</tt:AnalyticsEngineConfiguration>"
        "<tt:RuleEngineConfiguration>"
        "<tt:Rule Name=\"MotionAlarm\" Type=\"tt:CellMotionDetector\"><tt:Parameters/></tt:Rule>"
        "</tt:RuleEngineConfiguration>"
        f"</{tag}>"
    )


def _meta(ch: ChannelConfig, tag: str) -> str:
    return (
        f"<{tag} token=\"{meta_token(ch)}\"><tt:Name>Metadata_{ch.id}</tt:Name><tt:UseCount>1</tt:UseCount>"
        "<tt:PTZStatus><tt:Status>true</tt:Status><tt:Position>true</tt:Position></tt:PTZStatus>"
        "<tt:Events/>"
        "<tt:Analytics>true</tt:Analytics>"
        "<tt:Multicast><tt:Address><tt:Type>IPv4</tt:Type><tt:IPv4Address>0.0.0.0</tt:IPv4Address></tt:Address>"
        "<tt:Port>0</tt:Port><tt:TTL>1</tt:TTL><tt:AutoStart>false</tt:AutoStart></tt:Multicast>"
        "<tt:SessionTimeout>PT60S</tt:SessionTimeout>"
        f"</{tag}>"
    )


def _encoder_options(rt: Runtime) -> str:
    sizes = "".join(
        f"<tt:ResolutionsAvailable><tt:Width>{w}</tt:Width><tt:Height>{h}</tt:Height></tt:ResolutionsAvailable>"
        for w, h in ((1920, 1080), (1280, 720), (704, 576), (640, 480), (640, 360), (320, 240))
    )
    common = (
        "<tt:FrameRateRange><tt:Min>1</tt:Min><tt:Max>60</tt:Max></tt:FrameRateRange>"
        "<tt:EncodingIntervalRange><tt:Min>1</tt:Min><tt:Max>1</tt:Max></tt:EncodingIntervalRange>"
    )
    return (
        "<trt:Options><tt:QualityRange><tt:Min>1</tt:Min><tt:Max>6</tt:Max></tt:QualityRange>"
        f"<tt:JPEG>{sizes}{common}</tt:JPEG>"
        f"<tt:H264>{sizes}<tt:GovLengthRange><tt:Min>1</tt:Min><tt:Max>300</tt:Max></tt:GovLengthRange>"
        f"{common}<tt:H264ProfilesSupported>Baseline</tt:H264ProfilesSupported>"
        "<tt:H264ProfilesSupported>Main</tt:H264ProfilesSupported>"
        "<tt:H264ProfilesSupported>High</tt:H264ProfilesSupported></tt:H264>"
        "</trt:Options>"
    )


def _apply_vec(rt: Runtime, req: SoapRequest) -> str:
    cfg = find_child(req.body, "Configuration")
    token = ""
    if cfg is not None:
        for k, v in cfg.attrib.items():
            if k.rsplit("}", 1)[-1] == "token":
                token = v
    ch, sub = _find_channel(rt, token, "vec")
    if ch is None:
        return soap_fault("NoConfig", "Unknown video encoder configuration", "ter:InvalidArgVal")
    tgt = (ch.encoder, ch.sub, ch.third)[int(sub)]
    res = find_child(cfg, "Resolution")
    try:
        if res is not None:
            tgt.width = int(child_text(res, "Width") or tgt.width)
            tgt.height = int(child_text(res, "Height") or tgt.height)
        fps = child_text(cfg, "FrameRateLimit")
        if fps:
            tgt.fps = int(float(fps))
        br = child_text(cfg, "BitrateLimit")
        if br:
            tgt.bitrate = int(float(br))
        gov = child_text(cfg, "GovLength")
        if gov:
            tgt.gop = int(float(gov))
    except (ValueError, TypeError):
        return soap_fault("ConfigModify", "Bad encoder parameters", "ter:InvalidArgVal")
    rt.save_config()
    try:
        import asyncio

        asyncio.get_running_loop().create_task(rt.pipelines.restart_channel(ch.id))
    except RuntimeError:
        pass
    return "<trt:SetVideoEncoderConfigurationResponse/>"


def _more(rt: Runtime, req: SoapRequest, a: str) -> str:
    chans = rt.enabled_channels()
    if a == "GetServiceCapabilities":
        return (
            "<trt:GetServiceCapabilitiesResponse>"
            "<trt:Capabilities SnapshotUri=\"true\" Rotation=\"false\" VideoSourceMode=\"false\" OSD=\"false\" "
            "TemporaryOSDText=\"false\" EXICompression=\"false\">"
            "<trt:ProfileCapabilities MaximumNumberOfProfiles=\"64\"/>"
            "<trt:StreamingCapabilities RTPMulticast=\"false\" RTP_TCP=\"true\" RTP_RTSP_TCP=\"true\" "
            "NonAggregateControl=\"false\" NoRTSPStreaming=\"false\"/>"
            "</trt:Capabilities></trt:GetServiceCapabilitiesResponse>"
        )
    if a in _EMPTY_LISTS or a in _EMPTY_OK:
        return f"<trt:{a}Response/>"
    if a == "GetMetadataConfigurations":
        return f"<trt:GetMetadataConfigurationsResponse>{''.join(_meta(c, 'trt:Configurations') for c in chans)}</trt:GetMetadataConfigurationsResponse>"
    if a == "GetMetadataConfiguration":
        ch, _ = _find_channel(rt, child_text(req.body, "ConfigurationToken"), "meta")
        if ch is None:
            return soap_fault("NoConfig", "Unknown metadata configuration", "ter:InvalidArgVal")
        return f"<trt:GetMetadataConfigurationResponse>{_meta(ch, 'trt:Configuration')}</trt:GetMetadataConfigurationResponse>"
    if a == "GetMetadataConfigurationOptions":
        return (
            "<trt:GetMetadataConfigurationOptionsResponse><trt:Options>"
            "<tt:PTZStatusFilterOptions><tt:PanTiltStatusSupported>true</tt:PanTiltStatusSupported>"
            "<tt:ZoomStatusSupported>true</tt:ZoomStatusSupported>"
            "<tt:PanTiltPositionSupported>true</tt:PanTiltPositionSupported>"
            "<tt:ZoomPositionSupported>true</tt:ZoomPositionSupported></tt:PTZStatusFilterOptions>"
            "</trt:Options></trt:GetMetadataConfigurationOptionsResponse>"
        )
    if a == "GetVideoAnalyticsConfigurations":
        return f"<trt:GetVideoAnalyticsConfigurationsResponse>{''.join(_vac(c, 'trt:Configurations') for c in chans)}</trt:GetVideoAnalyticsConfigurationsResponse>"
    if a == "GetVideoAnalyticsConfiguration":
        ch, _ = _find_channel(rt, child_text(req.body, "ConfigurationToken"), "vac")
        if ch is None:
            return soap_fault("NoConfig", "Unknown analytics configuration", "ter:InvalidArgVal")
        return f"<trt:GetVideoAnalyticsConfigurationResponse>{_vac(ch, 'trt:Configuration')}</trt:GetVideoAnalyticsConfigurationResponse>"
    if a == "GetVideoEncoderConfigurationOptions":
        return f"<trt:GetVideoEncoderConfigurationOptionsResponse>{_encoder_options(rt)}</trt:GetVideoEncoderConfigurationOptionsResponse>"
    if a == "GetVideoEncoderConfiguration":
        ch, sub = _find_channel(rt, child_text(req.body, "ConfigurationToken"), "vec")
        if ch is None:
            return soap_fault("NoConfig", "Unknown video encoder configuration", "ter:InvalidArgVal")
        inner = _vec(ch, sub).replace("trt:Configurations", "trt:Configuration")
        return f"<trt:GetVideoEncoderConfigurationResponse>{inner}</trt:GetVideoEncoderConfigurationResponse>"
    if a == "SetVideoEncoderConfiguration":
        return _apply_vec(rt, req)
    if a == "GetVideoSourceConfigurationOptions":
        toks = "".join(f"<tt:VideoSourceTokensAvailable>{vs_token(c)}</tt:VideoSourceTokensAvailable>" for c in chans)
        w = max((c.encoder.width for c in chans), default=1920)
        h = max((c.encoder.height for c in chans), default=1080)
        return (
            "<trt:GetVideoSourceConfigurationOptionsResponse><trt:Options>"
            f"<tt:BoundsRange><tt:XRange><tt:Min>0</tt:Min><tt:Max>{w}</tt:Max></tt:XRange>"
            f"<tt:YRange><tt:Min>0</tt:Min><tt:Max>{h}</tt:Max></tt:YRange>"
            f"<tt:WidthRange><tt:Min>1</tt:Min><tt:Max>{w}</tt:Max></tt:WidthRange>"
            f"<tt:HeightRange><tt:Min>1</tt:Min><tt:Max>{h}</tt:Max></tt:HeightRange></tt:BoundsRange>"
            f"{toks}</trt:Options></trt:GetVideoSourceConfigurationOptionsResponse>"
        )
    if a == "GetCompatibleVideoSourceConfigurations":
        return f"<trt:GetCompatibleVideoSourceConfigurationsResponse>{''.join(_vsc(c) for c in chans)}</trt:GetCompatibleVideoSourceConfigurationsResponse>"
    if a in {"CreateProfile"}:
        return soap_fault("MaxNVTProfiles", "Profiles are fixed on this device", "ter:MaxNVTProfiles")
    if a in {"DeleteProfile"}:
        return soap_fault("DeletionOfFixedProfile", "Fixed profile cannot be deleted", "ter:DeletionOfFixedProfile")
    return soap_fault("ActionNotSupported", f"Unknown Media action {a}")
