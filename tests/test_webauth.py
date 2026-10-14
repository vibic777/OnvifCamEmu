import base64

import pytest
from aiohttp.test_utils import TestClient, TestServer

from onvif_emu.http_app import create_app
from onvif_emu.models import ChannelConfig, Role, UserRecord
from onvif_emu.onvif_dispatch import dispatch
from onvif_emu.soap import parse_envelope

GUI = {"X-Emu-Gui": "1"}


def _basic(user="admin", pw="admin"):
    return {"Authorization": "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode()}


def _two_channels(runtime):
    runtime.config.channels = [ChannelConfig(id=1, name="A", rtsp_path="ch1"), ChannelConfig(id=2, name="B", rtsp_path="ch2")]
    runtime.state.users.upsert(UserRecord(username="op", password="op1", role=Role.Operator, channels=[2]))
    runtime.state.users.upsert(UserRecord(username="view", password="v1", role=Role.Viewer))


async def _login(client, user, pw):
    r = await client.post("/api/login", json={"username": user, "password": pw}, headers=GUI)
    return r, await r.json()


@pytest.mark.asyncio
async def test_session_login_logout_and_no_browser_popup(runtime):
    app = create_app(runtime)
    async with TestServer(app) as server, TestClient(server) as client:
        r = await client.get("/api/config", headers=GUI)
        assert r.status == 401 and "WWW-Authenticate" not in r.headers  # the page shows its own sign-in
        r = await client.get("/api/config")
        assert r.status == 401 and "WWW-Authenticate" in r.headers  # plain clients still get a challenge
        r, j = await _login(client, "admin", "wrong")
        assert r.status == 401 and j["error"] == "bad_credentials"
        r, j = await _login(client, "admin", "admin")
        assert j["ok"] and j["me"]["role"] == "Administrator" and j["me"]["default_password"]
        assert (await client.get("/api/config", headers=GUI)).status == 200  # cookie, no header
        assert (await client.get("/api/users", headers=GUI)).status == 200
        await client.post("/api/logout", headers=GUI)
        assert (await client.get("/api/config", headers=GUI)).status == 401


@pytest.mark.asyncio
async def test_roles_and_channels(runtime):
    _two_channels(runtime)
    app = create_app(runtime)
    async with TestServer(app) as server, TestClient(server) as client:
        _, j = await _login(client, "op", "op1")
        assert j["me"]["role"] == "Operator" and j["me"]["channels"] == [2]
        cfg = await (await client.get("/api/config", headers=GUI)).json()
        assert [c["id"] for c in cfg["channels"]] == [2]
        st = await (await client.get("/api/status", headers=GUI)).json()
        assert [c["id"] for c in st["connect"]["channels"]] == [2] and st["users"] == []
        assert (await client.put("/api/config", json=cfg, headers=GUI)).status == 403
        assert (await client.get("/api/users", headers=GUI)).status == 403
        assert (await client.get("/snapshot/1.jpg", headers=GUI)).status == 404
        assert (await client.get("/snapshot/2.jpg", headers=GUI)).status == 200
        assert (await client.get("/preview/ch1/", headers=GUI)).status == 404
        r = await client.post("/api/ptz", json={"channel": 1, "cmd": "stop"}, headers=GUI)
        assert r.status == 403
        r = await client.post("/api/ptz", json={"channel": 2, "cmd": "stop"}, headers=GUI)
        assert r.status == 200
        await client.post("/api/logout", headers=GUI)
        await _login(client, "view", "v1")
        assert (await client.post("/api/ptz", json={"channel": 1, "cmd": "stop"}, headers=GUI)).status == 403
        st = await (await client.get("/api/status", headers=GUI)).json()
        assert [c["id"] for c in st["connect"]["channels"]] == [1, 2]


@pytest.mark.asyncio
async def test_user_management_api(runtime):
    app = create_app(runtime)
    async with TestServer(app) as server, TestClient(server) as client:
        h = _basic()
        r = await client.post("/api/users", json={"username": "guard", "password": "g1", "role": "Viewer", "channels": [1]}, headers=h)
        assert r.status == 200
        assert runtime.state.users.find("guard").channels == [1]
        assert (await client.post("/api/users", json={"username": "guard", "password": "x"}, headers=h)).status == 400
        assert (await client.delete("/api/users/admin", headers=h)).status == 400
        assert (await client.put("/api/users/admin", json={"enabled": False}, headers=h)).status == 400
        assert (await client.put("/api/users/admin", json={"password": "N3w"}, headers=h)).status == 200
        h = _basic("admin", "N3w")
        assert (await client.put("/api/users/guard", json={"enabled": False}, headers=h)).status == 200
        _, j = await _login(client, "guard", "g1")
        assert not j["ok"]  # disabled
        assert (await client.delete("/api/users/guard", headers=h)).status == 200
        assert runtime.state.users.find("guard") is None


@pytest.mark.asyncio
async def test_own_password_and_rate_limit(runtime):
    runtime.state.users.upsert(UserRecord(username="view", password="v1", role=Role.Viewer))
    app = create_app(runtime)
    async with TestServer(app) as server, TestClient(server) as client:
        await _login(client, "view", "v1")
        r = await client.put("/api/me/password", json={"old": "nope", "new": "v2"}, headers=GUI)
        assert (await r.json())["error"] == "wrong_old_password"
        r = await client.put("/api/me/password", json={"old": "v1", "new": "v2"}, headers=GUI)
        assert r.status == 200 and runtime.state.users.check("view", "v2")
        await client.post("/api/logout", headers=GUI)
        for _ in range(5):
            await _login(client, "view", "bad")
        r, j = await _login(client, "view", "v2")
        assert r.status == 429 and j["error"] == "too_many"


def test_onvif_hides_channels_and_uses_own_rtsp_credentials(runtime):
    _two_channels(runtime)
    runtime.config.auth.embed_rtsp_credentials = True
    op = runtime.state.users.find("op")

    def call(action, body=""):
        xml = f"""<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope" xmlns:trt="http://www.onvif.org/ver10/media/wsdl">
          <s:Body><trt:{action}>{body}</trt:{action}></s:Body></s:Envelope>"""
        req = parse_envelope(xml)
        req.action = action
        return dispatch(runtime, req, "/onvif/media_service", user=op, anonymous=False)

    profiles = call("GetProfiles")
    assert "profile_2" in profiles and "profile_1" not in profiles
    uri = call("GetStreamUri", "<trt:ProfileToken>profile_2</trt:ProfileToken>")
    assert "op:op1@" in uri and "admin:" not in uri
    assert "ch1" not in call("GetStreamUri", "<trt:ProfileToken>profile_1</trt:ProfileToken>")
    assert len(runtime.enabled_channels()) == 2  # outside a request nothing is hidden


def test_fleet_devices_keep_one_shot_account_inputs():
    from onvif_emu.fleet import derive_device
    from onvif_emu.models import AppConfig

    root = AppConfig()
    root.auth.password = "cli-pass"
    root.users = [UserRecord(username="op1", password="p", role=Role.Operator, channels=[2])]
    dev = derive_device(root, 1)
    assert dev.auth.password == "cli-pass" and dev.users[0].username == "op1"
