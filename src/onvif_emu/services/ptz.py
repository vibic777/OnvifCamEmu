from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

from onvif_emu.soap import attr, child_text, find_child, soap_fault, vector_xy, vector_zoom
from onvif_emu.state import Preset
from onvif_emu.util import new_uuid

if TYPE_CHECKING:
    from onvif_emu.runtime import Runtime
    from onvif_emu.soap import SoapRequest

log = logging.getLogger(__name__)


def _e(t: str) -> str:
    return str(t).replace("&", "&amp;").replace("<", "&lt;")


def _channel_id(rt: Runtime, req: SoapRequest) -> int:
    import re

    token = child_text(req.body, "ProfileToken")
    m = re.fullmatch(r"profile_(\d+)(?:_sub|_third)?", token or "")
    if m:
        return int(m.group(1))
    chs = rt.ptz_channels() or rt.enabled_channels()
    return chs[0].id if chs else 1


_NEEDS_CHANNEL = {
    "GetStatus", "AbsoluteMove", "RelativeMove", "ContinuousMove", "Stop", "GetPresets", "SetPreset",
    "RemovePreset", "GotoPreset", "GotoHomePosition", "SetHomePosition", "GetPresetTours", "GetPresetTour",
    "SetPresetTour", "GetCompatibleConfigurations",
}


def _speed(req: SoapRequest) -> tuple[float, float, float]:
    sp = find_child(req.body, "Speed")
    if sp is None:
        return 1.0, 1.0, 1.0
    x, y = vector_xy(sp, "Speed", (1.0, 1.0))
    # PanTilt attrs on child
    pt = find_child(sp, "PanTilt")
    z = find_child(sp, "Zoom")
    pan = float(attr(pt, "x") or x or 1.0)
    tilt = float(attr(pt, "y") or y or 1.0)
    zoom = float(attr(z, "x") or 1.0)
    return abs(pan) or 1.0, abs(tilt) or 1.0, abs(zoom) or 1.0


def handle_ptz(rt: Runtime, req: SoapRequest) -> str:
    a = req.action
    log.debug("PTZ action=%s", a)
    if a in _NEEDS_CHANNEL:
        cid0 = _channel_id(rt, req)
        chan = next((c for c in rt.enabled_channels() if c.id == cid0), None)  # hidden channels: no PTZ
        if chan is None or not chan.ptz_enabled:
            return soap_fault("NoPTZProfile", "The profile has no PTZ configuration", "ter:NoPTZProfile")
    if a in {"GetNodes", "GetNode"}:
        return _nodes(rt, req)
    if a == "GetStatus":
        return _status(rt, req)
    if a == "GetConfigurations":
        return _configs(rt)
    if a == "GetConfiguration":
        token = child_text(req.body, "PTZConfigurationToken")
        inner = _configs(rt)
        one = [p for p in inner.split("<tptz:PTZConfiguration ")[1:] if f'token="{token}"' in p]
        body = ("<tptz:PTZConfiguration " + one[0].split("</tptz:GetConfigurationsResponse>")[0]) if one else ""
        return f"<tptz:GetConfigurationResponse>{body}</tptz:GetConfigurationResponse>"
    if a == "AbsoluteMove":
        return _abs(rt, req)
    if a == "RelativeMove":
        return _rel(rt, req)
    if a == "ContinuousMove":
        return _cont(rt, req)
    if a == "Stop":
        return _stop(rt, req)
    if a == "GetPresets":
        return _get_presets(rt, req)
    if a == "SetPreset":
        return _set_preset(rt, req)
    if a == "RemovePreset":
        return _remove_preset(rt, req)
    if a == "GotoPreset":
        return _goto_preset(rt, req)
    if a in {"GetPresetTours", "GetPresetTour"}:
        return f"<tptz:{a}Response/>"
    if a == "SetPresetTour":
        return "<tptz:SetPresetTourResponse/>"
    if a == "GetServiceCapabilities":
        return (
            "<tptz:GetServiceCapabilitiesResponse>"
            "<tptz:Capabilities EFlip=\"false\" Reverse=\"false\" GetCompatibleConfigurations=\"true\" "
            "MoveStatus=\"true\" StatusPosition=\"true\"/>"
            "</tptz:GetServiceCapabilitiesResponse>"
        )
    if a == "GetConfigurationOptions":
        return _config_options(rt)
    if a == "GetCompatibleConfigurations":
        return _configs(rt).replace("GetConfigurationsResponse", "GetCompatibleConfigurationsResponse")
    if a == "GotoHomePosition":
        cid = _channel_id(rt, req)
        servo = rt.ensure_servo(cid)
        home = rt.state.channel(cid)
        now = time.monotonic()
        servo.absolute_move(getattr(home, "home_pan", 0.0), getattr(home, "home_tilt", 0.0), getattr(home, "home_zoom", 0.0), now=now)
        servo.kind = "preset"
        servo.last_command = "GotoHomePosition"
        servo.flash("HOME", now=now)
        return "<tptz:GotoHomePositionResponse/>"
    if a == "SetHomePosition":
        cid = _channel_id(rt, req)
        servo = rt.ensure_servo(cid)
        st = servo.status()
        chs = rt.state.channel(cid)
        chs.home_pan, chs.home_tilt, chs.home_zoom = st.pan, st.tilt, st.zoom
        rt.state.mark_dirty()
        servo.flash("SET HOME", now=time.monotonic())
        return "<tptz:SetHomePositionResponse/>"
    if a in {"SetConfiguration", "ModifyPresetTour", "OperatePresetTour", "RemovePresetTour", "SendAuxiliaryCommand"}:
        if a == "SendAuxiliaryCommand":
            cmd = child_text(req.body, "AuxiliaryData") or ""
            if cmd:
                rt.ensure_servo(_channel_id(rt, req)).flash(f"AUX: {cmd}"[:40], now=time.monotonic())
            return "<tptz:SendAuxiliaryCommandResponse><tptz:AuxiliaryResponse/></tptz:SendAuxiliaryCommandResponse>"
        return f"<tptz:{a}Response/>"
    if a == "GetPresetTourOptions":
        return (
            "<tptz:GetPresetTourOptionsResponse><tptz:Options><tt:AutoStart>false</tt:AutoStart>"
            "<tt:StartingCondition><tt:RecurringTime><tt:Min>0</tt:Min><tt:Max>0</tt:Max></tt:RecurringTime>"
            "</tt:StartingCondition></tptz:Options></tptz:GetPresetTourOptionsResponse>"
        )
    return soap_fault("ActionNotSupported", f"Unknown PTZ action {a}")


def _nodes(rt: Runtime, req: SoapRequest) -> str:
    items = []
    for ch in rt.ptz_channels():
        items.append(
            f"""<tptz:PTZNode token="ptz_node_{ch.id}" FixedHomePosition="false" GeoMove="false">
            <tt:Name>PTZ Node {ch.id}</tt:Name>
            <tt:SupportedPTZSpaces>
              <tt:AbsolutePanTiltPositionSpace>
                <tt:URI>http://www.onvif.org/ver10/tptz/PanTiltSpaces/PositionGenericSpace</tt:URI>
                <tt:XRange><tt:Min>{rt.config.ptz.pan_min}</tt:Min><tt:Max>{rt.config.ptz.pan_max}</tt:Max></tt:XRange>
                <tt:YRange><tt:Min>{rt.config.ptz.tilt_min}</tt:Min><tt:Max>{rt.config.ptz.tilt_max}</tt:Max></tt:YRange>
              </tt:AbsolutePanTiltPositionSpace>
              <tt:AbsoluteZoomPositionSpace>
                <tt:URI>http://www.onvif.org/ver10/tptz/ZoomSpaces/PositionGenericSpace</tt:URI>
                <tt:XRange><tt:Min>{rt.config.ptz.zoom_min}</tt:Min><tt:Max>{rt.config.ptz.zoom_max}</tt:Max></tt:XRange>
              </tt:AbsoluteZoomPositionSpace>
              <tt:RelativePanTiltTranslationSpace>
                <tt:URI>http://www.onvif.org/ver10/tptz/PanTiltSpaces/TranslationGenericSpace</tt:URI>
                <tt:XRange><tt:Min>-1.0</tt:Min><tt:Max>1.0</tt:Max></tt:XRange>
                <tt:YRange><tt:Min>-1.0</tt:Min><tt:Max>1.0</tt:Max></tt:YRange>
              </tt:RelativePanTiltTranslationSpace>
              <tt:RelativeZoomTranslationSpace>
                <tt:URI>http://www.onvif.org/ver10/tptz/ZoomSpaces/TranslationGenericSpace</tt:URI>
                <tt:XRange><tt:Min>-1.0</tt:Min><tt:Max>1.0</tt:Max></tt:XRange>
              </tt:RelativeZoomTranslationSpace>
              <tt:ContinuousPanTiltVelocitySpace>
                <tt:URI>http://www.onvif.org/ver10/tptz/PanTiltSpaces/VelocityGenericSpace</tt:URI>
                <tt:XRange><tt:Min>-1.0</tt:Min><tt:Max>1.0</tt:Max></tt:XRange>
                <tt:YRange><tt:Min>-1.0</tt:Min><tt:Max>1.0</tt:Max></tt:YRange>
              </tt:ContinuousPanTiltVelocitySpace>
              <tt:ContinuousZoomVelocitySpace>
                <tt:URI>http://www.onvif.org/ver10/tptz/ZoomSpaces/VelocityGenericSpace</tt:URI>
                <tt:XRange><tt:Min>-1.0</tt:Min><tt:Max>1.0</tt:Max></tt:XRange>
              </tt:ContinuousZoomVelocitySpace>
              <tt:PanTiltSpeedSpace>
                <tt:URI>http://www.onvif.org/ver10/tptz/PanTiltSpaces/GenericSpeedSpace</tt:URI>
                <tt:XRange><tt:Min>0.0</tt:Min><tt:Max>1.0</tt:Max></tt:XRange>
              </tt:PanTiltSpeedSpace>
              <tt:ZoomSpeedSpace>
                <tt:URI>http://www.onvif.org/ver10/tptz/ZoomSpaces/ZoomGenericSpeedSpace</tt:URI>
                <tt:XRange><tt:Min>0.0</tt:Min><tt:Max>1.0</tt:Max></tt:XRange>
              </tt:ZoomSpeedSpace>
            </tt:SupportedPTZSpaces>
            <tt:MaximumNumberOfPresets>32</tt:MaximumNumberOfPresets>
            <tt:HomeSupported>true</tt:HomeSupported>
            </tptz:PTZNode>"""
        )
    if req.action == "GetNode":
        return f"<tptz:GetNodeResponse>{items[0] if items else ''}</tptz:GetNodeResponse>"
    wrapped = "".join(items)
    return f"<tptz:GetNodesResponse>{wrapped}</tptz:GetNodesResponse>"


def _config_options(rt: Runtime) -> str:
    c = rt.config.ptz
    base = "http://www.onvif.org/ver10/tptz/"
    return (
        "<tptz:GetConfigurationOptionsResponse><tptz:PTZConfigurationOptions><tt:Spaces>"
        f"<tt:AbsolutePanTiltPositionSpace><tt:URI>{base}PanTiltSpaces/PositionGenericSpace</tt:URI>"
        f"<tt:XRange><tt:Min>{c.pan_min}</tt:Min><tt:Max>{c.pan_max}</tt:Max></tt:XRange>"
        f"<tt:YRange><tt:Min>{c.tilt_min}</tt:Min><tt:Max>{c.tilt_max}</tt:Max></tt:YRange></tt:AbsolutePanTiltPositionSpace>"
        f"<tt:AbsoluteZoomPositionSpace><tt:URI>{base}ZoomSpaces/PositionGenericSpace</tt:URI>"
        f"<tt:XRange><tt:Min>{c.zoom_min}</tt:Min><tt:Max>{c.zoom_max}</tt:Max></tt:XRange></tt:AbsoluteZoomPositionSpace>"
        f"<tt:RelativePanTiltTranslationSpace><tt:URI>{base}PanTiltSpaces/TranslationGenericSpace</tt:URI>"
        "<tt:XRange><tt:Min>-1</tt:Min><tt:Max>1</tt:Max></tt:XRange>"
        "<tt:YRange><tt:Min>-1</tt:Min><tt:Max>1</tt:Max></tt:YRange></tt:RelativePanTiltTranslationSpace>"
        f"<tt:RelativeZoomTranslationSpace><tt:URI>{base}ZoomSpaces/TranslationGenericSpace</tt:URI>"
        "<tt:XRange><tt:Min>-1</tt:Min><tt:Max>1</tt:Max></tt:XRange></tt:RelativeZoomTranslationSpace>"
        f"<tt:ContinuousPanTiltVelocitySpace><tt:URI>{base}PanTiltSpaces/VelocityGenericSpace</tt:URI>"
        "<tt:XRange><tt:Min>-1</tt:Min><tt:Max>1</tt:Max></tt:XRange>"
        "<tt:YRange><tt:Min>-1</tt:Min><tt:Max>1</tt:Max></tt:YRange></tt:ContinuousPanTiltVelocitySpace>"
        f"<tt:ContinuousZoomVelocitySpace><tt:URI>{base}ZoomSpaces/VelocityGenericSpace</tt:URI>"
        "<tt:XRange><tt:Min>-1</tt:Min><tt:Max>1</tt:Max></tt:XRange></tt:ContinuousZoomVelocitySpace>"
        f"<tt:PanTiltSpeedSpace><tt:URI>{base}PanTiltSpaces/GenericSpeedSpace</tt:URI>"
        "<tt:XRange><tt:Min>0</tt:Min><tt:Max>1</tt:Max></tt:XRange></tt:PanTiltSpeedSpace>"
        f"<tt:ZoomSpeedSpace><tt:URI>{base}ZoomSpaces/ZoomGenericSpeedSpace</tt:URI>"
        "<tt:XRange><tt:Min>0</tt:Min><tt:Max>1</tt:Max></tt:XRange></tt:ZoomSpeedSpace>"
        "</tt:Spaces><tt:PTZTimeout><tt:Min>PT0S</tt:Min><tt:Max>PT600S</tt:Max></tt:PTZTimeout>"
        "</tptz:PTZConfigurationOptions></tptz:GetConfigurationOptionsResponse>"
    )


def _configs(rt: Runtime) -> str:
    parts = []
    for ch in rt.ptz_channels():
        parts.append(
            f"<tptz:PTZConfiguration token=\"ptz_{ch.id}\"><tt:Name>PTZ_{ch.id}</tt:Name>"
            f"<tt:UseCount>1</tt:UseCount><tt:NodeToken>ptz_node_{ch.id}</tt:NodeToken></tptz:PTZConfiguration>"
        )
    return f"<tptz:GetConfigurationsResponse>{''.join(parts)}</tptz:GetConfigurationsResponse>"


def _status(rt: Runtime, req: SoapRequest) -> str:
    cid = _channel_id(rt, req)
    servo = rt.servos.get(cid)
    st = servo.status() if servo else None
    pan = st.pan if st else 0.0
    tilt = st.tilt if st else 0.0
    zoom = st.zoom if st else 0.0
    move = "IDLE"
    if st and st.moving:
        move = "MOVING"
    return (
        "<tptz:GetStatusResponse><tptz:PTZStatus>"
        f"<tt:Position><tt:PanTilt x=\"{pan:.4f}\" y=\"{tilt:.4f}\" space=\"http://www.onvif.org/ver10/tptz/PanTiltSpaces/PositionGenericSpace\"/>"
        f"<tt:Zoom x=\"{zoom:.4f}\" space=\"http://www.onvif.org/ver10/tptz/ZoomSpaces/PositionGenericSpace\"/></tt:Position>"
        f"<tt:MoveStatus><tt:PanTilt>{move}</tt:PanTilt><tt:Zoom>{move}</tt:Zoom></tt:MoveStatus>"
        f"<tt:UtcTime>{_utc()}</tt:UtcTime>"
        "</tptz:PTZStatus></tptz:GetStatusResponse>"
    )


def _utc() -> str:
    from onvif_emu.timeutil import isoformat_z

    return isoformat_z()


def _abs(rt: Runtime, req: SoapRequest) -> str:
    cid = _channel_id(rt, req)
    servo = rt.ensure_servo(cid)
    pos = find_child(req.body, "Position")
    if pos is None:
        pos = req.body
    pan, tilt = vector_xy(pos, "Position")
    if pan is None and tilt is None:
        pt = find_child(pos, "PanTilt")
        pan = float(attr(pt, "x") or "nan") if pt is not None else None
        tilt = float(attr(pt, "y") or "nan") if pt is not None else None
        if pan is not None and pan != pan:
            pan = None
        if tilt is not None and tilt != tilt:
            tilt = None
    zoom = vector_zoom(pos, "Position")
    if zoom is None:
        zel = find_child(pos, "Zoom")
        if zel is not None and attr(zel, "x"):
            zoom = float(attr(zel, "x"))
    ps, ts, zs = _speed(req)
    servo.absolute_move(pan, tilt, zoom, ps, ts, zs, now=time.monotonic())
    rt.persist_ptz(cid)
    return "<tptz:AbsoluteMoveResponse/>"


def _rel(rt: Runtime, req: SoapRequest) -> str:
    cid = _channel_id(rt, req)
    servo = rt.ensure_servo(cid)
    tr = find_child(req.body, "Translation")
    if tr is None:
        tr = req.body
    pan, tilt = vector_xy(tr, "Translation")
    pt = find_child(tr, "PanTilt")
    if pt is not None:
        if attr(pt, "x"):
            pan = float(attr(pt, "x"))
        if attr(pt, "y"):
            tilt = float(attr(pt, "y"))
    zoom = vector_zoom(tr, "Translation")
    zel = find_child(tr, "Zoom")
    if zel is not None and attr(zel, "x"):
        zoom = float(attr(zel, "x"))
    # Relative spaces are often -1..1; map to a fraction of full range
    cfg = rt.config.ptz
    dpan = (pan or 0.0) * (cfg.pan_max - cfg.pan_min) * 0.5
    dtilt = (tilt or 0.0) * (cfg.tilt_max - cfg.tilt_min) * 0.5
    dzoom = (zoom or 0.0) * (cfg.zoom_max - cfg.zoom_min)
    ps, ts, zs = _speed(req)
    servo.relative_move(dpan, dtilt, dzoom, ps, ts, zs, now=time.monotonic())
    rt.persist_ptz(cid)
    return "<tptz:RelativeMoveResponse/>"


def _cont(rt: Runtime, req: SoapRequest) -> str:
    cid = _channel_id(rt, req)
    servo = rt.ensure_servo(cid)
    vel = find_child(req.body, "Velocity")
    if vel is None:
        vel = req.body
    pan, tilt = 0.0, 0.0
    pt = find_child(vel, "PanTilt")
    if pt is not None:
        pan = float(attr(pt, "x") or 0)
        tilt = float(attr(pt, "y") or 0)
    zel = find_child(vel, "Zoom")
    zoom = float(attr(zel, "x") or 0) if zel is not None else 0.0
    servo.continuous_move(pan, tilt, zoom, now=time.monotonic())
    return "<tptz:ContinuousMoveResponse/>"


def _stop(rt: Runtime, req: SoapRequest) -> str:
    cid = _channel_id(rt, req)
    servo = rt.ensure_servo(cid)
    pt = child_text(req.body, "PanTilt", "true").lower() != "false"
    zm = child_text(req.body, "Zoom", "true").lower() != "false"
    servo.stop(pan_tilt=pt, zoom=zm, now=time.monotonic())
    rt.persist_ptz(cid)
    return "<tptz:StopResponse/>"


def _get_presets(rt: Runtime, req: SoapRequest) -> str:
    cid = _channel_id(rt, req)
    chs = rt.state.channel(cid)
    parts = []
    for p in chs.presets:
        parts.append(
            f"<tptz:Preset token=\"{_e(p.token)}\"><tt:Name>{_e(p.name)}</tt:Name>"
            f"<tt:PTZPosition><tt:PanTilt x=\"{p.pan:.4f}\" y=\"{p.tilt:.4f}\"/>"
            f"<tt:Zoom x=\"{p.zoom:.4f}\"/></tt:PTZPosition></tptz:Preset>"
        )
    return f"<tptz:GetPresetsResponse>{''.join(parts)}</tptz:GetPresetsResponse>"


def _set_preset(rt: Runtime, req: SoapRequest) -> str:
    cid = _channel_id(rt, req)
    name = child_text(req.body, "PresetName") or "Preset"
    token = child_text(req.body, "PresetToken") or new_uuid()[:8]
    servo = rt.ensure_servo(cid)
    st = servo.status()
    chs = rt.state.channel(cid)
    chs.presets = [p for p in chs.presets if p.token != token]
    chs.presets.append(Preset(token=token, name=name, pan=st.pan, tilt=st.tilt, zoom=st.zoom))
    rt.state.mark_dirty()
    rt.state.save()
    servo.flash(f"SET PRESET: {name}", now=time.monotonic())
    return f"<tptz:SetPresetResponse><tptz:PresetToken>{_e(token)}</tptz:PresetToken></tptz:SetPresetResponse>"


def _remove_preset(rt: Runtime, req: SoapRequest) -> str:
    cid = _channel_id(rt, req)
    token = child_text(req.body, "PresetToken")
    chs = rt.state.channel(cid)
    removed = next((p for p in chs.presets if p.token == token), None)
    chs.presets = [p for p in chs.presets if p.token != token]
    rt.state.mark_dirty()
    rt.state.save()
    if removed:
        rt.ensure_servo(cid).flash(f"DEL PRESET: {removed.name}", now=time.monotonic())
    return "<tptz:RemovePresetResponse/>"


def _goto_preset(rt: Runtime, req: SoapRequest) -> str:
    cid = _channel_id(rt, req)
    token = child_text(req.body, "PresetToken")
    chs = rt.state.channel(cid)
    for p in chs.presets:
        if p.token == token:
            servo = rt.ensure_servo(cid)
            servo.absolute_move(p.pan, p.tilt, p.zoom, now=time.monotonic())
            servo.kind = "preset"
            servo.last_command = "GotoPreset"
            servo.flash(f"GOTO PRESET: {p.name}", now=time.monotonic())
            return "<tptz:GotoPresetResponse/>"
    return soap_fault("NoToken", "Unknown preset", "ter:InvalidArgVal")
