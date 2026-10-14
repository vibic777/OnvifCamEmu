from onvif_emu.auth import min_role_for_action, require_action_role, role_allows
from onvif_emu.models import Role, UserRecord
from onvif_emu.onvif_dispatch import dispatch
from onvif_emu.soap import parse_envelope


def _req(action: str, body: str = ""):
    xml = f"""<?xml version="1.0"?>
    <s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope" xmlns:tds="http://www.onvif.org/ver10/device/wsdl">
      <s:Body><tds:{action}>{body}</tds:{action}></s:Body></s:Envelope>"""
    req = parse_envelope(xml)
    req.action = action
    return req


def test_role_hierarchy():
    assert role_allows(Role.Administrator, Role.Viewer)
    assert role_allows(Role.Operator, Role.Viewer)
    assert not role_allows(Role.Viewer, Role.Operator)
    assert min_role_for_action("GetDeviceInformation") == Role.Viewer
    assert min_role_for_action("ContinuousMove") == Role.Operator
    assert min_role_for_action("CreateUsers") == Role.Administrator
    assert min_role_for_action("GetSystemDateAndTime") is None


def test_viewer_blocked_from_admin_action(runtime):
    viewer = UserRecord(username="v", password="x", role=Role.Viewer)
    out = dispatch(
        runtime,
        _req(
            "CreateUsers",
            "<User><Username>n</Username><Password>p</Password><UserLevel>Operator</UserLevel></User>",
        ),
        "/onvif/device_service",
        user=viewer,
        anonymous=False,
    )
    assert "NotAuthorized" in out


def test_admin_allowed_create_users(runtime):
    admin = UserRecord(username="admin", password="admin", role=Role.Administrator)
    out = dispatch(
        runtime,
        _req(
            "CreateUsers",
            "<User><Username>op99</Username><Password>p</Password><UserLevel>Operator</UserLevel></User>",
        ),
        "/onvif/device_service",
        user=admin,
        anonymous=False,
    )
    assert "CreateUsersResponse" in out
    assert runtime.state.find_user("op99") is not None


def test_require_action_role_skips_without_user():
    require_action_role(None, "CreateUsers", anonymous=False)  # no raise


def test_stable_discovery_epr(runtime):
    from onvif_emu.discovery import probe_match_xml

    runtime.state.load(runtime.config)
    epr = runtime.state.data.endpoint_uuid
    assert epr
    a = probe_match_xml(xaddr="http://h/onvif/device_service", scopes=[], endpoint_uuid=epr)
    b = probe_match_xml(xaddr="http://h/onvif/device_service", scopes=[], endpoint_uuid=epr)
    assert f"urn:uuid:{epr}".encode() in a
    assert f"urn:uuid:{epr}".encode() in b
