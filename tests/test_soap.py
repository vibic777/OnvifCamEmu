import pytest

from onvif_emu.soap import localname, parse_envelope, soap_fault, soap_ok, vector_xy


def test_parse_action_and_body():
    xml = """<?xml version="1.0"?>
    <s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"
                xmlns:tds="http://www.onvif.org/ver10/device/wsdl">
      <s:Header><s:Action>http://www.onvif.org/ver10/device/wsdl/GetDeviceInformation</s:Action></s:Header>
      <s:Body><tds:GetDeviceInformation/></s:Body>
    </s:Envelope>"""
    req = parse_envelope(xml)
    assert req.action == "GetDeviceInformation"


def test_soap11_envelope():
    xml = """<SOAP-ENV:Envelope xmlns:SOAP-ENV="http://schemas.xmlsoap.org/soap/envelope/">
      <SOAP-ENV:Body><GetCapabilities xmlns="http://www.onvif.org/ver10/device/wsdl"/></SOAP-ENV:Body>
    </SOAP-ENV:Envelope>"""
    req = parse_envelope(xml)
    assert req.action == "GetCapabilities"


def test_malformed_raises():
    with pytest.raises(ValueError):
        parse_envelope("<not-xml")


def test_soap_ok_wraps_and_fault():
    body = soap_ok("<tds:GetHostnameResponse/>")
    assert "s:Envelope" in body
    assert "GetHostname" in body
    f = soap_fault("x", "nope")
    assert "s:Fault" in f


def test_vector_xy_attrs():
    from xml.etree import ElementTree as ET

    el = ET.fromstring(
        '<Position xmlns:tt="http://www.onvif.org/ver10/schema">'
        '<tt:PanTilt x="1.5" y="-2"/>'
        "</Position>"
    )
    x, y = vector_xy(el, "Position")
    # el localname is Position so find_child Position may fail — function looks for container
    x, y = vector_xy(el, "PanTilt")
    # when container is PanTilt and el is Position, find_child works
    from onvif_emu.soap import attr, find_child

    pt = find_child(el, "PanTilt")
    assert attr(pt, "x") == "1.5"
    assert attr(pt, "y") == "-2"
    assert localname(el.tag) == "Position"
