from __future__ import annotations

import base64
import os
import time
from xml.etree import ElementTree as ET

import pytest

from onvif_emu.auth import (
    AuthError,
    AuthMode,
    ReplayCache,
    authenticate,
    digest_response,
    issue_digest_challenge,
    parse_digest_header,
    register_digest_nonce,
    verify_basic,
    verify_digest,
    verify_wsse,
    wsse_digest,
)
from onvif_emu.models import UserRecord
from onvif_emu.util import constant_time_equals

USERS = [UserRecord(username="admin", password="secret")]


def test_basic_ok_and_bad():
    token = base64.b64encode(b"admin:secret").decode()
    u = verify_basic(f"Basic {token}", USERS)
    assert u.username == "admin"
    with pytest.raises(AuthError):
        verify_basic("Basic " + base64.b64encode(b"admin:nope").decode(), USERS)
    with pytest.raises(AuthError):
        verify_basic("Basic not-base64", USERS)


def test_digest_roundtrip_and_replay():
    ch = issue_digest_challenge()
    params = parse_digest_header(ch)
    nonce = params["nonce"]
    nc, cnonce = "00000001", "deadbeef"
    resp = digest_response("admin", "secret", "POST", "/onvif/device_service", nonce, nc, cnonce)
    header = (
        f'Digest username="admin", realm="ONVIF", nonce="{nonce}", uri="/onvif/device_service", '
        f'qop=auth, nc={nc}, cnonce="{cnonce}", response="{resp}", opaque="onvifemu"'
    )
    u = verify_digest(header, "POST", "/onvif/device_service", USERS)
    assert u.username == "admin"
    with pytest.raises(AuthError):
        verify_digest(header, "POST", "/onvif/device_service", USERS)


def test_digest_wrong_password():
    register_digest_nonce("abc")
    resp = digest_response("admin", "wrong", "GET", "/", "abc", "00000001", "cc")
    header = (
        'Digest username="admin", realm="ONVIF", nonce="abc", uri="/", '
        f'qop=auth, nc=00000001, cnonce="cc", response="{resp}"'
    )
    with pytest.raises(AuthError):
        verify_digest(header, "GET", "/", USERS)


def test_wsse_password_digest():
    nonce = base64.b64encode(b"1234567890123456").decode()
    created = "2026-01-01T00:00:00.000Z"
    pwd = wsse_digest("secret", nonce, created)
    xml = f"""<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"
        xmlns:wsse="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd"
        xmlns:wsu="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd">
        <s:Header><wsse:Security>
          <wsse:UsernameToken>
            <wsse:Username>admin</wsse:Username>
            <wsse:Password>{pwd}</wsse:Password>
            <wsse:Nonce>{nonce}</wsse:Nonce>
            <wsu:Created>{created}</wsu:Created>
          </wsse:UsernameToken>
        </wsse:Security></s:Header><s:Body/></s:Envelope>"""
    # Created is old → skew fail unless we relax. Use current created.
    from onvif_emu.timeutil import isoformat_z

    created2 = isoformat_z()
    pwd2 = wsse_digest("secret", nonce, created2)
    xml2 = xml.replace(created, created2).replace(pwd, pwd2)
    env = ET.fromstring(xml2)
    u = verify_wsse(env, USERS, skew_s=300)
    assert u.username == "admin"
    with pytest.raises(AuthError):
        verify_wsse(env, USERS, skew_s=300)  # replay


def _wsse_envelope(username: str, password: str, nonce_raw: bytes | None = None) -> ET.Element:
    from onvif_emu.timeutil import isoformat_z

    nonce = base64.b64encode(nonce_raw or os.urandom(16)).decode()
    created = isoformat_z()
    pwd = wsse_digest(password, nonce, created)
    xml = f"""<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"
        xmlns:tds="http://www.onvif.org/ver10/device/wsdl"
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
        <s:Body><tds:GetDeviceInformation/></s:Body></s:Envelope>"""
    return ET.fromstring(xml)


def test_digest_mode_accepts_wsse_username_token():
    r = authenticate(
        AuthMode.digest,
        authorization=None,
        method="POST",
        path="/onvif/device_service",
        envelope=_wsse_envelope("admin", "secret"),
        users=USERS,
        action="GetDeviceInformation",
    )
    assert r.anonymous is False
    assert r.user is not None and r.user.username == "admin"


def test_digest_mode_missing_http_creds_challenges():
    env = ET.fromstring(
        "<s:Envelope xmlns:s='http://www.w3.org/2003/05/soap-envelope'><s:Body/></s:Envelope>"
    )
    with pytest.raises(AuthError) as ei:
        authenticate(
            AuthMode.digest,
            authorization=None,
            method="POST",
            path="/onvif/device_service",
            envelope=env,
            users=USERS,
            action="GetDeviceInformation",
        )
    assert ei.value.challenge is True


def test_invalid_wsse_in_digest_mode_no_http_challenge():
    with pytest.raises(AuthError) as ei:
        authenticate(
            AuthMode.digest,
            authorization=None,
            method="POST",
            path="/onvif/device_service",
            envelope=_wsse_envelope("admin", "wrong"),
            users=USERS,
            action="GetDeviceInformation",
        )
    assert ei.value.challenge is False


def test_anon_get_time_skips_auth():
    r = authenticate(
        AuthMode.digest,
        authorization=None,
        method="POST",
        path="/onvif/device_service",
        envelope=None,
        users=USERS,
        action="GetSystemDateAndTime",
    )
    assert r.anonymous is True


def test_digest_unknown_nonce_rejected():
    resp = digest_response("admin", "secret", "GET", "/", "not-issued", "00000001", "cc")
    header = (
        'Digest username="admin", realm="ONVIF", nonce="not-issued", uri="/", '
        f'qop=auth, nc=00000001, cnonce="cc", response="{resp}"'
    )
    with pytest.raises(AuthError, match="nonce"):
        verify_digest(header, "GET", "/", USERS)


def test_wsse_requires_created():
    nonce = base64.b64encode(b"1234567890123456").decode()
    xml = f"""<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"
        xmlns:wsse="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd">
        <s:Header><wsse:Security>
          <wsse:UsernameToken>
            <wsse:Username>admin</wsse:Username>
            <wsse:Password>secret</wsse:Password>
            <wsse:Nonce>{nonce}</wsse:Nonce>
          </wsse:UsernameToken>
        </wsse:Security></s:Header><s:Body/></s:Envelope>"""
    with pytest.raises(AuthError, match="Created"):
        verify_wsse(ET.fromstring(xml), USERS)


def test_replay_cache_lru_and_ttl():
    c = ReplayCache(maxsize=3, ttl_s=0.2)
    assert c.seen("a") is False
    assert c.seen("a") is True
    c.seen("b")
    c.seen("c")
    c.seen("d")  # evicts a
    time.sleep(0.25)
    assert c.seen("b") is False  # expired


def test_constant_time_equals():
    assert constant_time_equals("ab", "ab")
    assert not constant_time_equals("ab", "ac")
