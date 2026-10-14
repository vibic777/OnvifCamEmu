import base64

import pytest
from aiohttp.test_utils import TestClient, TestServer

from onvif_emu.http_app import create_app
from onvif_emu.pipeline.snapshot import MINI_JPEG


def _basic(user: str = "admin", password: str = "admin") -> dict[str, str]:
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


@pytest.mark.asyncio
async def test_health(runtime):
    app = create_app(runtime)
    async with TestServer(app) as server:
        async with TestClient(server) as client:
            resp = await client.get("/health")
            assert resp.status == 200
            data = await resp.json()
            assert data["status"] == "ok"
            assert "version" in data


@pytest.mark.asyncio
async def test_snapshot_and_config(runtime):
    app = create_app(runtime)
    auth = _basic()
    async with TestServer(app) as server:
        async with TestClient(server) as client:
            denied = await client.get("/api/config")
            assert denied.status == 401
            snap = await client.get("/snapshot/1.jpg", headers=auth)
            assert snap.status == 200
            body = await snap.read()
            assert len(body) > 10
            r = await client.get("/api/config", headers=auth)
            cfg = await r.json()
            cfg["device"]["model"] = "TEST"
            cfg["auth"]["password"] = "newpass"
            put = await client.put("/api/config", json=cfg, headers=auth)
            assert (await put.json())["ok"] is True
            assert runtime.state.find_user("admin").password == "newpass"
            # old password no longer works
            assert (await client.get("/api/config", headers=auth)).status == 401
            auth2 = _basic(password="newpass")
            assert (await client.get("/api/config", headers=auth2)).status == 200
            xml = """<?xml version="1.0"?>
            <s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope" xmlns:tds="http://www.onvif.org/ver10/device/wsdl">
              <s:Body><tds:GetSystemDateAndTime/></s:Body></s:Envelope>"""
            resp = await client.post(
                "/onvif/device_service", data=xml, headers={"Content-Type": "application/soap+xml"}
            )
            assert resp.status == 200
            assert "SystemDateAndTime" in await resp.text()
            info = """<?xml version="1.0"?>
            <s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope" xmlns:tds="http://www.onvif.org/ver10/device/wsdl">
              <s:Body><tds:GetDeviceInformation/></s:Body></s:Envelope>"""
            unauth = await client.post("/onvif/device_service", data=info)
            assert unauth.status == 401
            assert "Digest" in unauth.headers.get("WWW-Authenticate", "")


def _wsse_device_information(username: str, password: str) -> str:
    import os

    from onvif_emu.auth import wsse_digest
    from onvif_emu.timeutil import isoformat_z

    nonce = base64.b64encode(os.urandom(16)).decode()
    created = isoformat_z()
    pwd = wsse_digest(password, nonce, created)
    return f"""<?xml version="1.0"?>
    <s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope" xmlns:tds="http://www.onvif.org/ver10/device/wsdl"
        xmlns:wsse="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd"
        xmlns:wsu="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd">
      <s:Header><wsse:Security>
        <wsse:UsernameToken>
          <wsse:Username>{username}</wsse:Username>
          <wsse:Password Type="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-username-token-profile-1.0#PasswordDigest">{pwd}</wsse:Password>
          <wsse:Nonce EncodingType="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-soap-message-security-1.0#Base64Binary">{nonce}</wsse:Nonce>
          <wsu:Created>{created}</wsu:Created>
        </wsse:UsernameToken>
      </wsse:Security></s:Header>
      <s:Body><tds:GetDeviceInformation/></s:Body>
    </s:Envelope>"""


@pytest.mark.asyncio
async def test_digest_accepts_wsse_without_www_authenticate_on_failure(runtime):
    app = create_app(runtime)
    async with TestServer(app) as server:
        async with TestClient(server) as client:
            ok = await client.post("/onvif/device_service", data=_wsse_device_information("admin", "admin"))
            assert ok.status == 200
            body = await ok.text()
            assert "GetDeviceInformationResponse" in body
            assert "WWW-Authenticate" not in ok.headers

            bad = await client.post("/onvif/device_service", data=_wsse_device_information("admin", "nope"))
            assert bad.status == 401
            assert "WWW-Authenticate" not in bad.headers

            probe = await client.get("/onvif/device_service")
            assert probe.status == 405
            assert "WWW-Authenticate" not in probe.headers


@pytest.mark.asyncio
async def test_gui_wizard_exposes_auth_mode(runtime):
    runtime.config.gui = True
    app = create_app(runtime)
    async with TestServer(app) as server:
        async with TestClient(server) as client:
            js = await (await client.get("/static/app.js")).text()
            assert "auth.mode" in js
            assert "authModeOptions" in js
            loc = await (await client.get("/api/locales/ru.json")).json()
            assert loc["wizard"]["auth"]
            assert loc["security"]["opt_digest"]
            assert loc["security"]["opt_none"]
            assert loc["login"]["title"]
    assert len(MINI_JPEG) > 8


@pytest.mark.asyncio
async def test_api_public_locales_without_auth(runtime):
    app = create_app(runtime)
    async with TestServer(app) as server:
        async with TestClient(server) as client:
            assert (await client.get("/api/locales/en")).status == 200
            assert (await client.get("/api/i18n")).status == 200
            assert (await client.get("/api/status")).status == 401


@pytest.mark.asyncio
async def test_connect_info_and_legacy_page(runtime):
    runtime.config.auth.embed_rtsp_credentials = True
    app = create_app(runtime)
    async with TestServer(app) as server:
        async with TestClient(server) as client:
            st = await (await client.get("/api/status", headers=_basic())).json()
            conn = st["connect"]
            assert conn["device_service"].endswith("/onvif/device_service")
            ch = conn["channels"][0]
            assert ch["profiles"][0]["token"] == f"profile_{ch['id']}"
            assert ch["profiles"][0]["rtsp"].startswith("rtsp://admin:admin@")
            # ODM's "Web page" is an embedded IE: it gets the script-free page, never the password
            ie = {"User-Agent": "Mozilla/4.0 (compatible; MSIE 7.0; Windows NT 10.0; Trident/7.0)"}
            page = await (await client.get("/", headers=ie)).text()
            assert "<script" not in page and "/onvif/device_service" in page
            assert "admin:admin@" not in page and f"profile_{ch['id']}" in page
            assert (await client.get("/info")).status == 200
            modern = await (await client.get("/")).text()
            assert "app.js" in modern
