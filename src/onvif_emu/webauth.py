"""Web sign-in, sessions, per-role access to the web API, account management and the preview proxy.

* The browser signs in once (``POST /api/login``) and gets an HttpOnly session cookie; every tab and page
  then works without asking again. Scripts may still send Basic / Digest on each request.
* Roles: Administrator - everything; Operator - watch the allowed channels + PTZ; Viewer - watch only.
* Browser requests (header ``X-Emu-Gui``) never get a ``WWW-Authenticate`` challenge, so the browser's own
  password pop-up never appears; the page shows its sign-in screen instead.
* ``/preview/...`` proxies MediaMTX's HLS player through the emulator with a loopback-only account, so the
  live preview needs no second login (and honours the account's channels).
"""

from __future__ import annotations

import logging
import secrets
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import aiohttp
from aiohttp import web

from onvif_emu.access import is_admin, may_ptz
from onvif_emu.auth import AuthError, authenticate_http, www_authenticate
from onvif_emu.models import AuthMode, Role, UserRecord
from onvif_emu.users import ADMIN, UserError

if TYPE_CHECKING:
    from onvif_emu.runtime import Runtime

log = logging.getLogger(__name__)

COOKIE = "emu_session"
IDLE_S = 12 * 3600  # a session unused this long signs out
MAX_AGE_S = 7 * 24 * 3600
GUI_HEADER = "X-Emu-Gui"

# (method or "*", path prefix) -> needed level; first match wins; anything else under /api needs admin
_PUBLIC = ("/api/login", "/api/i18n", "/api/locales/")
_RULES: tuple[tuple[str, str, str], ...] = (
    ("GET", "/api/me", "view"),
    ("POST", "/api/logout", "view"),
    ("PUT", "/api/me/password", "view"),
    ("GET", "/api/status", "view"),
    ("GET", "/api/config", "view"),
    ("GET", "/snapshot/", "view"),
    ("GET", "/preview/", "view"),
    ("POST", "/api/ptz", "ptz"),
)


# ------------------------------------------------------------------ sessions
@dataclass
class Session:
    username: str
    created: float = field(default_factory=time.monotonic)
    last: float = field(default_factory=time.monotonic)


class Sessions:
    def __init__(self) -> None:
        self._by_token: dict[str, Session] = {}

    def create(self, username: str) -> str:
        now = time.monotonic()
        expired = [tok for tok, session in self._by_token.items()
                   if now - session.last > IDLE_S or now - session.created > MAX_AGE_S]
        for tok in expired:
            self._by_token.pop(tok, None)
        if len(self._by_token) >= 1024:
            oldest = min(self._by_token, key=lambda tok: self._by_token[tok].last)
            self._by_token.pop(oldest)
        token = secrets.token_urlsafe(32)
        self._by_token[token] = Session(username)
        return token

    def get(self, token: str | None) -> Session | None:
        if not token:
            return None
        s = self._by_token.get(token)
        now = time.monotonic()
        if s and (now - s.last > IDLE_S or now - s.created > MAX_AGE_S):
            self._by_token.pop(token, None)
            return None
        if s:
            s.last = now
        return s

    def drop(self, token: str | None) -> None:
        self._by_token.pop(token or "", None)

    def drop_user(self, username: str, keep: str | None = None) -> None:
        for tok in [t for t, s in self._by_token.items() if s.username == username and t != keep]:
            del self._by_token[tok]


class FailLimiter:
    """5 wrong passwords from one address within 5 minutes -> that address waits a minute."""

    def __init__(self, limit: int = 5, window_s: float = 300, block_s: float = 60):
        self.limit, self.window_s, self.block_s = limit, window_s, block_s
        self._fails: dict[str, list[float]] = {}
        self._blocked_until: dict[str, float] = {}

    def wait_s(self, ip: str) -> int:
        return max(0, round(self._blocked_until.get(ip, 0) - time.monotonic()))

    def fail(self, ip: str) -> None:
        now = time.monotonic()
        hits = [t for t in self._fails.get(ip, []) if now - t < self.window_s] + [now]
        self._fails[ip] = hits
        if len(hits) >= self.limit:
            self._blocked_until[ip] = now + self.block_s
            self._fails[ip] = []
            log.warning("Web sign-in: too many wrong passwords from %s - blocked for %ss", ip, int(self.block_s))

    def ok(self, ip: str) -> None:
        self._fails.pop(ip, None)


SESSIONS_KEY = web.AppKey("sessions", Sessions)
LIMITER_KEY = web.AppKey("limiter", FailLimiter)
PROXY_KEY = web.AppKey("preview_http", aiohttp.ClientSession)
USER_KEY = web.RequestKey("emu_user", object)  # UserRecord, or None when authentication is off


def _rt(request: web.Request) -> Runtime:
    from onvif_emu.http_app import RUNTIME_KEY

    return request.app[RUNTIME_KEY]


def request_user(request: web.Request) -> UserRecord | None:
    """The signed-in account; None only when authentication is off (everyone acts as administrator)."""
    return request.get(USER_KEY)


def _level(method: str, path: str) -> str | None:
    if path.startswith(_PUBLIC):
        return None
    for m, prefix, level in _RULES:
        if (m == "*" or m == method) and (path == prefix or path.startswith(prefix.rstrip("/") + "/") or path == prefix.rstrip("/")):
            return level
    return "admin" if path.startswith("/api/") else None


def _allowed(user: UserRecord | None, level: str) -> bool:
    if level == "view":
        return True
    if level == "ptz":
        return may_ptz(user)
    return is_admin(user)


def _unauthorized(rt: Runtime, request: web.Request, exc: AuthError | None = None) -> web.Response:
    headers = {}
    if GUI_HEADER not in request.headers and (exc is None or exc.challenge):
        challenge = www_authenticate(rt.config.auth.mode)
        if challenge:
            headers["WWW-Authenticate"] = challenge
    return web.json_response({"ok": False, "error": "unauthorized"}, status=401, headers=headers)


@web.middleware
async def auth_middleware(request: web.Request, handler):
    path = request.path
    guarded = path.startswith(("/api/", "/snapshot/", "/preview/"))
    level = _level(request.method, path) if guarded else None
    if level is None:
        return await handler(request)
    rt = _rt(request)
    if rt.config.auth.mode == AuthMode.none:
        request[USER_KEY] = None
        return await handler(request)
    user: UserRecord | None = None
    sess = request.app[SESSIONS_KEY].get(request.cookies.get(COOKIE))
    if sess is not None:
        user = rt.state.users.find(sess.username)
        if user is None or not user.enabled:
            request.app[SESSIONS_KEY].drop(request.cookies.get(COOKIE))
            user = None
    if user is None and request.headers.get("Authorization"):
        try:
            res = authenticate_http(rt.config.auth.mode, authorization=request.headers.get("Authorization"),
                                    method=request.method, path=request.path_qs, users=rt.state.active_users())
            user = res.user
        except AuthError as exc:
            return _unauthorized(rt, request, exc)
    if user is None:
        return _unauthorized(rt, request)
    if not _allowed(user, level):
        return web.json_response({"ok": False, "error": "forbidden", "role": user.role.value}, status=403)
    request[USER_KEY] = user
    return await handler(request)


# ------------------------------------------------------------------ sign-in
def _me(rt: Runtime, user: UserRecord | None) -> dict[str, Any]:
    if user is None:
        return {"username": "", "role": Role.Administrator.value, "channels": [], "anonymous": True,
                "auth": rt.config.auth.mode.value, "default_password": False}
    return {"username": user.username, "role": user.role.value, "channels": list(user.channels), "anonymous": False,
            "auth": rt.config.auth.mode.value, "main": user.username == ADMIN,
            "default_password": user.username == ADMIN and rt.state.users.admin_default_password}


async def api_login(request: web.Request) -> web.Response:
    rt = _rt(request)
    limiter = request.app[LIMITER_KEY]
    ip = request.remote or "?"
    wait = limiter.wait_s(ip)
    if wait:
        return web.json_response({"ok": False, "error": "too_many", "wait_s": wait}, status=429)
    try:
        data = await request.json()
    except ValueError:
        data = {}
    user = rt.state.users.check(str(data.get("username", "")).strip(), str(data.get("password", "")))
    if user is None:
        limiter.fail(ip)
        log.warning("Web sign-in failed user=%r from %s", data.get("username"), ip)
        return web.json_response({"ok": False, "error": "bad_credentials"}, status=401)
    limiter.ok(ip)
    token = request.app[SESSIONS_KEY].create(user.username)
    log.info("Web sign-in user=%s role=%s from %s", user.username, user.role.value, ip)
    resp = web.json_response({"ok": True, "me": _me(rt, user)})
    resp.set_cookie(COOKIE, token, httponly=True, samesite="Strict", path="/")
    return resp


async def api_logout(request: web.Request) -> web.Response:
    request.app[SESSIONS_KEY].drop(request.cookies.get(COOKIE))
    resp = web.json_response({"ok": True})
    resp.del_cookie(COOKIE, path="/")
    return resp


async def api_me(request: web.Request) -> web.Response:
    return web.json_response({"ok": True, "me": _me(_rt(request), request_user(request))})


async def api_my_password(request: web.Request) -> web.Response:
    """Any account changes its own password (old one required)."""
    rt = _rt(request)
    user = request_user(request)
    if user is None:
        return web.json_response({"ok": False, "error": "authentication is off"}, status=400)
    data = await request.json()
    if rt.state.users.check(user.username, str(data.get("old", ""))) is None:
        return web.json_response({"ok": False, "error": "wrong_old_password"}, status=400)
    try:
        rt.state.users.update(user.username, password=str(data.get("new", "")))
    except UserError as exc:
        return web.json_response({"ok": False, "error": str(exc)}, status=400)
    request.app[SESSIONS_KEY].drop_user(user.username, keep=request.cookies.get(COOKIE))
    _accounts_changed(rt)
    return web.json_response({"ok": True})


# ------------------------------------------------------------------ accounts (administrator)
def _accounts_changed(rt: Runtime) -> None:
    if rt.media:
        rt.media.refresh_users()  # RTSP picks the change up without dropping streams


def _user_from(data: dict[str, Any], base: UserRecord | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if "role" in data and data["role"]:
        out["role"] = Role.from_any(str(data["role"]))
    if "enabled" in data:
        out["enabled"] = bool(data["enabled"])
    if "channels" in data:
        out["channels"] = sorted({int(c) for c in (data.get("channels") or [])})
    if data.get("password"):
        out["password"] = str(data["password"])
    return out


async def api_users_list(request: web.Request) -> web.Response:
    rt = _rt(request)
    chans = [{"id": c.id, "name": c.name} for c in rt.config.channels]
    return web.json_response({"ok": True, "users": rt.state.users.public(), "channels": chans,
                              "roles": [r.value for r in Role]})


async def api_users_create(request: web.Request) -> web.Response:
    rt = _rt(request)
    data = await request.json()
    try:
        fields = _user_from(data)
        user = UserRecord(username=str(data.get("username", "")).strip(), password=fields.pop("password", ""),
                          **{"role": Role.Viewer, **fields})
        rt.state.users.upsert(user, create=True)
    except (UserError, ValueError) as exc:
        return web.json_response({"ok": False, "error": str(exc)}, status=400)
    _accounts_changed(rt)
    return web.json_response({"ok": True, "users": rt.state.users.public()})


async def api_users_update(request: web.Request) -> web.Response:
    rt = _rt(request)
    name = request.match_info["name"]
    data = await request.json()
    try:
        rt.state.users.update(name, **_user_from(data))
    except (UserError, ValueError) as exc:
        return web.json_response({"ok": False, "error": str(exc)}, status=400)
    me = request_user(request)
    if data.get("password") or data.get("enabled") is False or "role" in data or "channels" in data:
        keep = request.cookies.get(COOKIE) if me and me.username == name else None
        request.app[SESSIONS_KEY].drop_user(name, keep=keep)  # rights changed: sign in again
    _accounts_changed(rt)
    return web.json_response({"ok": True, "users": rt.state.users.public()})


async def api_users_delete(request: web.Request) -> web.Response:
    rt = _rt(request)
    name = request.match_info["name"]
    try:
        rt.state.users.delete(name)
    except UserError as exc:
        return web.json_response({"ok": False, "error": str(exc)}, status=400)
    request.app[SESSIONS_KEY].drop_user(name)
    _accounts_changed(rt)
    return web.json_response({"ok": True, "users": rt.state.users.public()})


# ------------------------------------------------------------------ live preview proxy
def channel_of_path(rt: Runtime, first_segment: str) -> int | None:
    for c in rt.config.channels:
        if first_segment in (c.rtsp_path, c.rtsp_path + "_sub", c.rtsp_path + "_third"):
            return c.id
    return None


async def preview_proxy(request: web.Request) -> web.StreamResponse:
    rt = _rt(request)
    tail = request.match_info.get("tail", "")
    cid = channel_of_path(rt, tail.split("/", 1)[0])
    user = request_user(request)
    if cid is None or (user is not None and not user.sees(cid)):
        raise web.HTTPNotFound()
    port = rt.media.hls_port if rt.media else rt.config.mediamtx.hls_port
    url = f"http://127.0.0.1:{port}/{tail}"
    creds = rt.media.internal_creds if rt.media else None
    auth = aiohttp.BasicAuth(*creds) if creds and rt.config.auth.mode != AuthMode.none else None
    try:
        async with request.app[PROXY_KEY].get(url, params=request.query, auth=auth, allow_redirects=False) as up:
            headers = {k: v for k, v in up.headers.items()
                       if k.lower() in {"content-type", "cache-control", "location"}}
            if "Location" in headers and headers["Location"].startswith("/"):
                headers["Location"] = "/preview" + headers["Location"]
            resp = web.StreamResponse(status=up.status, headers=headers)
            await resp.prepare(request)
            async for chunk in up.content.iter_chunked(64 * 1024):
                await resp.write(chunk)
            await resp.write_eof()
            return resp
    except aiohttp.ClientError as exc:
        return web.Response(status=502, text=f"preview unavailable: {exc}")


async def _proxy_session(app: web.Application):
    app[PROXY_KEY] = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30))
    yield
    await app[PROXY_KEY].close()


def register(app: web.Application) -> None:
    app[SESSIONS_KEY] = Sessions()
    app[LIMITER_KEY] = FailLimiter()
    app.cleanup_ctx.append(_proxy_session)
    app.router.add_post("/api/login", api_login)
    app.router.add_post("/api/logout", api_logout)
    app.router.add_get("/api/me", api_me)
    app.router.add_put("/api/me/password", api_my_password)
    app.router.add_get("/api/users", api_users_list)
    app.router.add_post("/api/users", api_users_create)
    app.router.add_put("/api/users/{name}", api_users_update)
    app.router.add_delete("/api/users/{name}", api_users_delete)
    app.router.add_get("/preview/{tail:.*}", preview_proxy)
