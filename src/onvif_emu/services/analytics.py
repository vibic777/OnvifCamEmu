from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from onvif_emu.soap import soap_fault
from onvif_emu.timeutil import isoformat_z

if TYPE_CHECKING:
    from onvif_emu.runtime import Runtime
    from onvif_emu.soap import SoapRequest

log = logging.getLogger(__name__)


def handle_analytics(rt: Runtime, req: SoapRequest) -> str:
    a = req.action
    log.debug("Analytics action=%s", a)
    if a == "GetVideoAnalyticsConfigurations":
        return (
            "<tan:GetVideoAnalyticsConfigurationsResponse>"
            "<tan:Configurations token=\"analytics_1\"><tt:Name>Analytics</tt:Name><tt:UseCount>1</tt:UseCount>"
            "</tan:Configurations></tan:GetVideoAnalyticsConfigurationsResponse>"
        )
    if a == "GetAnalyticsModules":
        mods = []
        if rt.config.analytics.motion_module:
            mods.append(_module("motion", "MotionDetector"))
        if rt.config.analytics.line_cross_module:
            mods.append(_module("line", "LineDetector"))
        if rt.config.analytics.field_detector_module:
            mods.append(_module("field", "FieldDetector"))
        return f"<tan:GetAnalyticsModulesResponse>{''.join(mods)}</tan:GetAnalyticsModulesResponse>"
    if a == "GetAnalyticsModuleOptions":
        return (
            "<tan:GetAnalyticsModuleOptionsResponse>"
            "<tan:Options Type=\"tt:CellMotionEngine\" Name=\"MotionDetector\"/>"
            "<tan:Options Type=\"tt:LineDetector\" Name=\"LineDetector\"/>"
            "<tan:Options Type=\"tt:FieldDetector\" Name=\"FieldDetector\"/>"
            "<tan:Options Type=\"tt:MotionRegionDetector\" Name=\"MotionRegionDetector\"/>"
            "<tan:Options Type=\"tt:CountAggregation\" Name=\"CountAggregation\"/>"
            "<tan:Options Type=\"tt:LoiteringDetector\" Name=\"LoiteringDetector\"/>"
            "</tan:GetAnalyticsModuleOptionsResponse>"
        )
    if a == "GetRules":
        return (
            "<tan:GetRulesResponse>"
            "<tan:Rule Name=\"MotionAlarm\" Type=\"tt:CellMotionEngine\"/>"
            "<tan:Rule Name=\"LineCross\" Type=\"tt:LineDetector\"/>"
            "<tan:Rule Name=\"Intrusion\" Type=\"tt:FieldDetector\"/>"
            "<tan:Rule Name=\"MotionRegion\" Type=\"tt:MotionRegionDetector\"/>"
            "<tan:Rule Name=\"Counting\" Type=\"tt:CountAggregation\"/>"
            "<tan:Rule Name=\"Loitering\" Type=\"tt:LoiteringDetector\"/>"
            "</tan:GetRulesResponse>"
        )
    if a == "GetRuleOptions":
        return (
            "<tan:GetRuleOptionsResponse>"
            "<tan:RuleOptions RuleType=\"tt:CellMotionEngine\"/>"
            "<tan:RuleOptions RuleType=\"tt:LineDetector\"/>"
            "<tan:RuleOptions RuleType=\"tt:FieldDetector\"/>"
            "<tan:RuleOptions RuleType=\"tt:MotionRegionDetector\"/>"
            "<tan:RuleOptions RuleType=\"tt:CountAggregation\"/>"
            "<tan:RuleOptions RuleType=\"tt:LoiteringDetector\"/>"
            "</tan:GetRuleOptionsResponse>"
        )
    if a in {"GetSupportedRules", "GetSupportedAnalyticsModules"}:
        return f"<tan:{a}Response/>"
    if a in {"GetAnalyticsConfiguration", "GetServiceCapabilities"}:
        if a == "GetServiceCapabilities":
            return (
                "<tan:GetServiceCapabilitiesResponse>"
                "<tan:Capabilities RuleSupport=\"true\" AnalyticsModuleSupport=\"true\" CellBasedMotion=\"true\"/>"
                "</tan:GetServiceCapabilitiesResponse>"
            )
        return "<tan:GetAnalyticsConfigurationResponse/>"
    # Non-standard but useful for GUI/tests: dump current boxes
    if a == "GetMetadata":
        xml = rt.analytics.frame_xml(isoformat_z())
        return f"<tan:GetMetadataResponse>{xml}</tan:GetMetadataResponse>"
    return soap_fault("ActionNotSupported", f"Unknown Analytics action {a}")


def _module(token: str, name: str) -> str:
    return f"<tan:AnalyticsModule Name=\"{name}\" Type=\"tt:{name}\" token=\"{token}\"/>"
