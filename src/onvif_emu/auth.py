"""ONVIF authentication: HTTP Basic, HTTP Digest (RFC 2617), WS-Security PasswordDigest.

Security properties:
- Passwords compared with hmac.compare_digest (constant time).
- Digest (nonce, nc) replay pairs are rejected via a bounded LRU.
- WSSE Created timestamps must be within ±skew; nonce replay is rejected.
- GetSystemDateAndTime is allowed without credentials (ONVIF core).
"""

from __future__ import annotations

import base64
import binascii
import logging
import re
import time
from collections import OrderedDict
from dataclasses import dataclass
from datetime import UTC
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree as ET

from onvif_emu.models import AuthMode, Role, UserRecord
from onvif_emu.timeutil import utc_now
from onvif_emu.util import constant_time_equals, md5_hex, new_nonce, sha1

log = logging.getLogger(__name__)

REALM = "ONVIF"
DIGEST_OPAQUE = "onvifemu"
ANON_ACTIONS = {
    "GetSystemDateAndTime",
    "GetWsdlUrl",
    "GetEndpointReference",
}
WSSE_NS = "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd"
WSU_NS = "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd"

_ROLE_RANK = {
    Role.Viewer: 0,
    Role.Operator: 1,
    Role.Administrator: 2,
}

ADMIN_ACTIONS = {
    "CreateUsers",
    "DeleteUsers",
    "SetUser",
    "SystemReboot",
    "SetScopes",
    "SetHostname",
    "SetNetworkProtocols",
    "SetNTP",
    "SetSystemDateAndTime",
    "SetDiscoveryMode",
}

OPERATOR_ACTIONS = {
    "ContinuousMove",
    "AbsoluteMove",
    "RelativeMove",
    "Stop",
    "SetPreset",
    "RemovePreset",
    "GotoPreset",
    "GotoHomePosition",
    "SetHomePosition",
    "SetImagingSettings",
    "Move",
    "CreatePullPointSubscription",
    "SetSynchronizationPoint",
}


class AuthError(Exception):
    def __init__(self, message: str = "Not Authorized", status: int = 401, *, challenge: bool = True):
        super().__init__(message)
        self.status = status
        # False: SOAP UsernameToken failed — do not send WWW-Authenticate.
        # ODM/WCF with scheme Anonymous throws if the server challenges Digest.
        self.challenge = challenge


class ReplayCache:
    """LRU of keys with TTL. O(1) insert/lookup amortized, bounded memory."""

    def __init__(self, maxsize: int = 2048, ttl_s: float = 300.0):
        self.maxsize = maxsize
        self.ttl_s = ttl_s
        self._data: OrderedDict[str, float] = OrderedDict()

    def seen(self, key: str, now: float | None = None) -> bool:
        t = now if now is not None else time.monotonic()
        self._gc(t)
        if key in self._data:
            return True
        self._data[key] = t
        self._data.move_to_end(key)
        if len(self._data) > self.maxsize:
            self._data.popitem(last=False)
        return False

    def _gc(self, now: float) -> None:
        cutoff = now - self.ttl_s
        while self._data:
            k, ts = next(iter(self._data.items()))
            if ts >= cutoff:
                break
            self._data.popitem(last=False)


_digest_replays = ReplayCache()
_wsse_replays = ReplayCache()
_digest_nonces = ReplayCache(ttl_s=120.0)


def register_digest_nonce(nonce: str, now: float | None = None) -> None:
    _digest_nonces.seen(nonce, now=now)


def issue_digest_challenge() -> str:
    nonce = new_nonce(16)
    register_digest_nonce(nonce)
    return (
        f'Digest realm="{REALM}", qop="auth", nonce="{nonce}", opaque="{DIGEST_OPAQUE}", algorithm=MD5'
    )


def parse_digest_header(header: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for match in re.finditer(r'(\w+)=("([^"]*)"|([^\s,]+))', header):
        key = match.group(1)
        val = match.group(3) if match.group(3) is not None else match.group(4)
        out[key] = val
    return out


def digest_response(
    username: str,
    password: str,
    method: str,
    uri: str,
    nonce: str,
    nc: str,
    cnonce: str,
    qop: str = "auth",
    realm: str = REALM,
) -> str:
    ha1 = md5_hex(f"{username}:{realm}:{password}")
    ha2 = md5_hex(f"{method}:{uri}")
    if qop:
        return md5_hex(f"{ha1}:{nonce}:{nc}:{cnonce}:{qop}:{ha2}")
    return md5_hex(f"{ha1}:{nonce}:{ha2}")


def verify_digest(
    header: str,
    method: str,
    uri: str,
    users: list[UserRecord],
    *,
    now: float | None = None,
) -> UserRecord:
    params = parse_digest_header(header)
    username = params.get("username", "")
    nonce = params.get("nonce", "")
    nc = params.get("nc", "00000001")
    cnonce = params.get("cnonce", "")
    qop = params.get("qop", "auth")
    response = params.get("response", "")
    if not username or not nonce or not response:
        raise AuthError("Malformed Digest")
    # Unknown nonce → challenge again (prevents offline guessing of stale nonces).
    # Tests must call register_digest_nonce() or use issue_digest_challenge().
    if nonce not in _digest_nonces._data:
        raise AuthError("Stale or unknown Digest nonce")
    replay_key = f"{nonce}:{nc}:{cnonce}"
    if _digest_replays.seen(replay_key, now=now):
        raise AuthError("Digest replay detected")
    user = _find_user(users, username)
    if not user:
        raise AuthError()
    expected = digest_response(user.username, user.password, method, uri, nonce, nc, cnonce, qop)
    # Some clients digest the request-uri without query; also try params uri
    alt_uri = params.get("uri", uri)
    expected_alt = digest_response(user.username, user.password, method, alt_uri, nonce, nc, cnonce, qop)
    if not (constant_time_equals(expected, response) or constant_time_equals(expected_alt, response)):
        raise AuthError()
    return user


def verify_basic(header: str, users: list[UserRecord]) -> UserRecord:
    try:
        encoded = header.split(" ", 1)[1].strip()
        decoded = base64.b64decode(encoded).decode("utf-8")
    except (IndexError, ValueError, UnicodeDecodeError, binascii.Error) as exc:
        raise AuthError("Malformed Basic") from exc
    if ":" not in decoded:
        raise AuthError("Malformed Basic")
    username, password = decoded.split(":", 1)
    user = _find_user(users, username)
    if not user or not constant_time_equals(user.password, password):
        raise AuthError()
    return user


def wsse_digest(password: str, nonce_b64: str, created: str) -> str:
    try:
        nonce = base64.b64decode(nonce_b64)
    except binascii.Error as exc:
        raise AuthError("Malformed WSSE nonce") from exc
    digest = sha1(nonce + created.encode("utf-8") + password.encode("utf-8"))
    return base64.b64encode(digest).decode("ascii")


def parse_wsse(envelope: ET.Element) -> dict[str, str] | None:
    # Search regardless of prefix
    token = None
    for el in envelope.iter():
        if el.tag.endswith("UsernameToken") or el.tag == f"{{{WSSE_NS}}}UsernameToken":
            token = el
            break
    if token is None:
        return None
    data: dict[str, str] = {}
    for child in token:
        local = child.tag.rsplit("}", 1)[-1]
        data[local] = (child.text or "").strip()
        if local == "Nonce":
            data["NonceEncoding"] = child.attrib.get("EncodingType", "")
    return data if data.get("Username") else None


def verify_wsse(
    envelope: ET.Element,
    users: list[UserRecord],
    *,
    skew_s: float = 300.0,
    now: float | None = None,
) -> UserRecord:
    data = parse_wsse(envelope)
    if not data:
        raise AuthError("Missing UsernameToken")
    username = data.get("Username", "")
    created = data.get("Created", "")
    nonce = data.get("Nonce", "")
    password = data.get("Password", "")
    user = _find_user(users, username)
    if not user:
        raise AuthError()
    if not created:
        raise AuthError("Missing WSSE Created")
    if not _created_fresh(created, skew_s):
        raise AuthError("WSSE Created outside allowed skew")
    if not nonce:
        raise AuthError("Missing WSSE Nonce")
    replay_key = f"{username}:{nonce}:{created}"
    if _wsse_replays.seen(replay_key, now=now):
        raise AuthError("WSSE replay detected")
    expected = wsse_digest(user.password, nonce, created)
    if not constant_time_equals(expected, password):
        # Some stacks send PasswordText (still require Created + Nonce above)
        if not constant_time_equals(user.password, password):
            raise AuthError()
    return user


def _created_fresh(created: str, skew_s: float) -> bool:
    text = created.strip()
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        from datetime import datetime

        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:

            dt = dt.replace(tzinfo=UTC)
        delta = abs((utc_now() - dt).total_seconds())
        return delta <= skew_s
    except ValueError:
        try:
            dt = parsedate_to_datetime(created)
            delta = abs((utc_now() - dt).total_seconds())
            return delta <= skew_s
        except (TypeError, ValueError):
            return False


def _find_user(users: list[UserRecord], username: str) -> UserRecord | None:
    for u in users:
        if u.username == username:
            return u
    return None


@dataclass
class AuthResult:
    user: UserRecord | None
    anonymous: bool


def authenticate(
    mode: AuthMode,
    *,
    authorization: str | None,
    method: str,
    path: str,
    envelope: ET.Element | None,
    users: list[UserRecord],
    action: str,
) -> AuthResult:
    if mode == AuthMode.none or action in ANON_ACTIONS:
        log.log(5, "Auth skip mode=%s action=%s anonymous", mode.value, action)  # 5 = TRACE
        return AuthResult(user=None, anonymous=True)

    authz = (authorization or "").strip()
    kind = authz.split(" ", 1)[0].lower() if authz else ""

    # HTTP scheme matching the configured mode (and WSSE mode accepts HTTP if sent).
    try:
        if kind == "digest" and mode in (AuthMode.digest, AuthMode.wsse):
            user = verify_digest(authz, method, path, users)
            log.debug("Auth digest ok user=%s action=%s", user.username, action)
            return AuthResult(user=user, anonymous=False)
        if kind == "basic" and mode in (AuthMode.basic, AuthMode.wsse):
            user = verify_basic(authz, users)
            log.debug("Auth basic ok user=%s action=%s", user.username, action)
            return AuthResult(user=user, anonymous=False)

        # Real cameras in Digest/Basic mode still accept WS-UsernameToken. ODM's
        # default ("User name token") uses HTTP Anonymous + SOAP header; a 401
        # Digest challenge then becomes WCF: scheme Anonymous vs WWW-Authenticate.
        if envelope is not None and parse_wsse(envelope) is not None:
            try:
                user = verify_wsse(envelope, users)
                log.debug("Auth wsse ok user=%s action=%s", user.username, action)
                return AuthResult(user=user, anonymous=False)
            except AuthError as exc:
                log.warning("Auth wsse failed action=%s: %s", action, exc)
                exc.challenge = False
                raise

        if mode == AuthMode.wsse:
            log.warning("Auth missing UsernameToken action=%s", action)
            raise AuthError("Missing UsernameToken", challenge=False)
        log.warning("Auth required mode=%s action=%s scheme=%s", mode.value, action, kind or "-")
        raise AuthError()
    except AuthError:
        raise


def role_allows(user_role: Role, required: Role) -> bool:
    return _ROLE_RANK.get(user_role, -1) >= _ROLE_RANK.get(required, 99)


def min_role_for_action(action: str) -> Role | None:
    """Minimum role for an ONVIF action. None = anonymous / no role gate."""
    if not action or action in ANON_ACTIONS:
        return None
    if action in ADMIN_ACTIONS:
        return Role.Administrator
    if action in OPERATOR_ACTIONS or action.startswith(("Set", "Create", "Delete", "Add", "Remove")):
        return Role.Operator
    return Role.Viewer


def require_action_role(user: UserRecord | None, action: str, *, anonymous: bool) -> None:
    """Raise AuthError if the authenticated user may not perform action.

    Skipped for anonymous requests and when no user is attached (unit tests / internal).
    """
    if anonymous or user is None:
        return
    needed = min_role_for_action(action)
    if needed is None:
        return
    if not role_allows(user.role, needed):
        raise AuthError(f"Insufficient privileges for {action}", status=401, challenge=False)


def www_authenticate(mode: AuthMode) -> str | None:
    if mode == AuthMode.digest:
        return issue_digest_challenge()
    if mode == AuthMode.basic:
        return f'Basic realm="{REALM}"'
    if mode == AuthMode.wsse:
        # Admin/HTTP plane has no SOAP header; challenge Basic for browser GUI.
        return f'Basic realm="{REALM}"'
    return None


def authenticate_http(
    mode: AuthMode,
    *,
    authorization: str | None,
    method: str,
    path: str,
    users: list[UserRecord],
) -> AuthResult:
    """HTTP admin plane: same users as ONVIF. Accepts Digest or Basic when auth is on."""
    if mode == AuthMode.none:
        return AuthResult(user=None, anonymous=True)

    authz = (authorization or "").strip()
    kind = authz.split(" ", 1)[0].lower() if authz else ""
    if kind == "digest":
        return AuthResult(user=verify_digest(authz, method, path, users), anonymous=False)
    if kind == "basic":
        # GUI always sends Basic; allowed for all non-none modes on /api/*.
        return AuthResult(user=verify_basic(authz, users), anonymous=False)
    raise AuthError()
