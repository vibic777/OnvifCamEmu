from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from onvif_emu.soap import child_text, find_child, soap_fault

if TYPE_CHECKING:
    from onvif_emu.runtime import Runtime
    from onvif_emu.soap import SoapRequest

log = logging.getLogger(__name__)


def handle_imaging(rt: Runtime, req: SoapRequest) -> str:
    a = req.action
    log.debug("Imaging action=%s", a)
    if a == "GetImagingSettings":
        return _settings(rt)
    if a == "SetImagingSettings":
        return _set(rt, req)
    if a == "GetOptions":
        return _options()
    if a == "GetMoveOptions":
        return (
            "<timg:GetMoveOptionsResponse><timg:MoveOptions>"
            "<tt:Absolute><tt:Position><tt:Min>0</tt:Min><tt:Max>1</tt:Max></tt:Position>"
            "<tt:Speed><tt:Min>0</tt:Min><tt:Max>1</tt:Max></tt:Speed></tt:Absolute>"
            "<tt:Continuous><tt:Speed><tt:Min>-1</tt:Min><tt:Max>1</tt:Max></tt:Speed></tt:Continuous>"
            "</timg:MoveOptions></timg:GetMoveOptionsResponse>"
        )
    if a == "Move":
        focus = find_child(req.body, "Focus")
        pos = child_text(focus, "Position") if focus is not None else ""
        cid = 1
        chs = rt.enabled_channels()
        if chs:
            cid = chs[0].id
        servo = rt.ensure_servo(cid)
        if pos:
            servo.focus.goto(float(pos))
        return "<timg:MoveResponse/>"
    if a == "Stop":
        return "<timg:StopResponse/>"
    if a == "GetStatus":
        img = rt.config.imaging
        return (
            "<timg:GetStatusResponse><timg:Status>"
            f"<tt:FocusStatus20><tt:Position>{img.focus:.3f}</tt:Position>"
            f"<tt:MoveStatus>IDLE</tt:MoveStatus></tt:FocusStatus20>"
            "</timg:Status></timg:GetStatusResponse>"
        )
    if a == "GetServiceCapabilities":
        return (
            "<timg:GetServiceCapabilitiesResponse><timg:Capabilities ImageStabilization=\"false\" "
            "Presets=\"false\"/></timg:GetServiceCapabilitiesResponse>"
        )
    if a in {"GetPresets", "GetCurrentPreset"}:
        return f"<timg:{a}Response/>"
    if a == "SetCurrentPreset":
        return "<timg:SetCurrentPresetResponse/>"
    return soap_fault("ActionNotSupported", f"Unknown Imaging action {a}")


def _settings(rt: Runtime) -> str:
    i = rt.config.imaging
    return f"""<timg:GetImagingSettingsResponse><timg:ImagingSettings>
      <tt:BacklightCompensation>
        <tt:Mode>{'ON' if i.backlight else 'OFF'}</tt:Mode>
        <tt:Level>{i.backlight_level}</tt:Level>
      </tt:BacklightCompensation>
      <tt:Brightness>{i.brightness}</tt:Brightness>
      <tt:ColorSaturation>{i.saturation}</tt:ColorSaturation>
      <tt:Contrast>{i.contrast}</tt:Contrast>
      <tt:Exposure>
        <tt:Mode>{i.exposure_mode.upper()}</tt:Mode>
        <tt:Iris>{i.iris:.3f}</tt:Iris>
      </tt:Exposure>
      <tt:Focus>
        <tt:AutoFocusMode>{'AUTO' if i.focus_mode == 'auto' else 'MANUAL'}</tt:AutoFocusMode>
        <tt:DefaultSpeed>0.5</tt:DefaultSpeed>
        <tt:NearLimit>0</tt:NearLimit>
        <tt:FarLimit>1</tt:FarLimit>
      </tt:Focus>
      <tt:Sharpness>{i.sharpness}</tt:Sharpness>
      <tt:WhiteBalance>
        <tt:Mode>{i.white_balance.upper()}</tt:Mode>
        <tt:CrGain>{i.wb_cr}</tt:CrGain>
        <tt:CbGain>{i.wb_cb}</tt:CbGain>
      </tt:WhiteBalance>
    </timg:ImagingSettings></timg:GetImagingSettingsResponse>"""


def _set(rt: Runtime, req: SoapRequest) -> str:
    i = rt.config.imaging
    settings = find_child(req.body, "ImagingSettings")
    if settings is None:
        settings = req.body

    def maybe_int(name: str, current: int) -> int:
        t = child_text(settings, name)
        if not t:
            return current
        try:
            return max(0, min(100, int(float(t))))
        except ValueError:
            return current

    def maybe_float(name: str, current: float) -> float:
        t = child_text(settings, name)
        if not t:
            return current
        try:
            return max(0.0, min(1.0, float(t)))
        except ValueError:
            return current

    i.brightness = maybe_int("Brightness", i.brightness)
    i.contrast = maybe_int("Contrast", i.contrast)
    i.saturation = maybe_int("ColorSaturation", i.saturation)
    i.sharpness = maybe_int("Sharpness", i.sharpness)
    iris = child_text(find_child(settings, "Exposure"), "Iris")
    if iris:
        try:
            i.iris = max(0.0, min(1.0, float(iris)))
            cid = rt.enabled_channels()[0].id if rt.enabled_channels() else 1
            rt.ensure_servo(cid).iris.goto(i.iris)
        except ValueError:
            pass
    return "<timg:SetImagingSettingsResponse/>"


def _options() -> str:
    rng = "<tt:Min>0</tt:Min><tt:Max>100</tt:Max>"
    return f"""<timg:GetOptionsResponse><timg:ImagingOptions>
      <tt:BacklightCompensation><tt:Mode>ON</tt:Mode><tt:Mode>OFF</tt:Mode><tt:Level>{rng}</tt:Level></tt:BacklightCompensation>
      <tt:Brightness>{rng}</tt:Brightness>
      <tt:ColorSaturation>{rng}</tt:ColorSaturation>
      <tt:Contrast>{rng}</tt:Contrast>
      <tt:Exposure><tt:Mode>AUTO</tt:Mode><tt:Mode>MANUAL</tt:Mode>
        <tt:Iris><tt:Min>0</tt:Min><tt:Max>1</tt:Max></tt:Iris></tt:Exposure>
      <tt:Focus><tt:AutoFocusModes>AUTO</tt:AutoFocusModes><tt:AutoFocusModes>MANUAL</tt:AutoFocusModes></tt:Focus>
      <tt:Sharpness>{rng}</tt:Sharpness>
      <tt:WhiteBalance><tt:Mode>AUTO</tt:Mode><tt:Mode>MANUAL</tt:Mode></tt:WhiteBalance>
    </timg:ImagingOptions></timg:GetOptionsResponse>"""
