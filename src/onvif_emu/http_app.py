from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from aiohttp import web

from onvif_emu.auth import AuthError, authenticate, www_authenticate
from onvif_emu.config_apply import apply_config
from onvif_emu.connect import connection_info, is_legacy_browser, legacy_page, pick_lang
from onvif_emu.i18n import DEFAULT_LANG, SUPPORTED_LANGS, load_locale, normalize_lang
from onvif_emu.models import AppConfig, AuthMode, Role, SourceType
from onvif_emu.onvif_dispatch import dispatch, dispatch_pull
from onvif_emu.paths import package_dir
from onvif_emu.pipeline.ffmpeg import find_ffmpeg
from onvif_emu.pipeline.snapshot import grab_jpeg
from onvif_emu.runtime import Runtime
from onvif_emu.soap import parse_envelope, soap_fault
from onvif_emu.videos import (
    browse,
    check_local_file,
    dest_path,
    find_ffprobe,
    list_videos,
    probe_video,
    resolve_video_dir,
)
from onvif_emu.webauth import auth_middleware, request_user
from onvif_emu.webauth import register as register_webauth

log = logging.getLogger(__name__)
STATIC_DIR = package_dir() / "gui" / "static"
RUNTIME_KEY = web.AppKey("runtime", Runtime)
MAX_API_BODY_BYTES = 16 * 1024 * 1024



def _rt(request: web.Request) -> Runtime:
    return request.app[RUNTIME_KEY]


def _auth_failure_response(rt: Runtime, exc: AuthError, *, soap: bool) -> web.Response:
    headers: dict[str, str] = {"Connection": "close"}
    if exc.challenge:
        challenge = www_authenticate(rt.config.auth.mode)
        if challenge:
            headers["WWW-Authenticate"] = challenge
    if soap:
        body = soap_fault("NotAuthorized", str(exc), "ter:NotAuthorized")
        return web.Response(
            status=exc.status,
            text=body,
            content_type="application/soap+xml",
            headers=headers,
        )
    return web.json_response({"ok": False, "error": "unauthorized"}, status=exc.status, headers=headers)


def _service_path(path: str) -> str:
    return path.rstrip("/").rsplit("/", 1)[-1]


def _action_from_headers(request: web.Request) -> str:
    soap_action = request.headers.get("SOAPAction", "").strip().strip('"')
    if soap_action:
        return soap_action.rsplit("/", 1)[-1]
    ct = request.headers.get("Content-Type", "")
    match = re.search(r'action\s*=\s*"([^"]+)"', ct, re.I)
    if match:
        return match.group(1).rsplit("/", 1)[-1]
    return ""


async def handle_onvif(request: web.Request) -> web.Response:
    rt: Runtime = _rt(request)
    raw = await request.read()
    if request.method.upper() == "GET" and not raw.strip():
        # WCF probes GET; a Digest 401 here poisons the Anonymous channel.
        return web.Response(status=405, text="SOAP POST required", headers={"Allow": "POST"})
    try:
        req = parse_envelope(raw) if raw else parse_envelope("<s:Envelope xmlns:s='http://www.w3.org/2003/05/soap-envelope'><s:Body/></s:Envelope>")
    except ValueError as exc:
        return web.Response(status=400, text=str(exc), content_type="text/plain")
    action = req.action or _action_from_headers(request)
    req.action = action
    try:
        result = authenticate(
            rt.config.auth.mode,
            authorization=request.headers.get("Authorization"),
            method=request.method,
            path=request.path_qs,
            envelope=req.envelope,
            users=rt.state.active_users(),
            action=action,
        )
    except AuthError as exc:
        return _auth_failure_response(rt, exc, soap=True)
    # Poll asynchronously so idle subscriptions cannot exhaust worker threads.
    if action == "PullMessages":
        xml = await dispatch_pull(
            rt,
            req,
            user=result.user,
            anonymous=result.anonymous,
        )
    else:
        xml = dispatch(
            rt,
            req,
            request.path,
            user=result.user,
            anonymous=result.anonymous,
        )
    log.info("ONVIF %s → %s", action, request.path)
    headers = {}
    if result.anonymous and rt.config.auth.mode in (AuthMode.digest, AuthMode.basic):
        headers["Connection"] = "close"
    status = 500 if "<s:Fault>" in xml[:600] else 200
    return web.Response(text=xml, status=status, content_type="application/soap+xml", charset="utf-8", headers=headers)


async def handle_health(request: web.Request) -> web.Response:
    rt: Runtime = _rt(request)
    return web.json_response(rt.health())


async def handle_snapshot(request: web.Request) -> web.Response:
    rt: Runtime = _rt(request)
    raw = request.match_info.get("channel", "1").replace(".jpg", "")
    try:
        cid = int(raw)
    except ValueError:
        cid = 1
    ch = next((c for c in rt.config.channels if c.id == cid), None)
    user = request_user(request)
    if ch is None or (user is not None and not user.sees(cid)):
        raise web.HTTPNotFound()
    ffmpeg = find_ffmpeg(rt.config.ffmpeg.binary)
    async with rt.snapshot_slots:
        data = await grab_jpeg(ffmpeg, ch)
    return web.Response(body=data, content_type="image/jpeg")


async def api_config_get(request: web.Request) -> web.Response:
    rt: Runtime = _rt(request)
    data = json.loads(rt.config.model_dump_json())
    user = request_user(request)
    if user is not None and user.role != Role.Administrator:  # Operator / Viewer: only their channels
        data["channels"] = [c for c in data["channels"] if user.sees(int(c["id"]))]
    return web.json_response(data)


async def api_config_put(request: web.Request) -> web.Response:
    rt: Runtime = _rt(request)
    data = await request.json()
    try:
        cfg = AppConfig.model_validate(data)
    except Exception as exc:
        return web.json_response({"ok": False, "error": str(exc)}, status=400)
    result = await apply_config(rt, cfg)
    return web.json_response(
        {
            "ok": True,
            "apply": {
                "identity_synced": result.identity_synced,
                "mediamtx_restarted": result.mediamtx_restarted,
                "pipelines_restarted": result.pipelines_restarted,
                "port_changed": result.port_changed,
                "port_error": result.port_error,
                "port": rt.config.onvif_port,
            },
        }
    )


async def api_status(request: web.Request) -> web.Response:
    rt: Runtime = _rt(request)
    user = request_user(request)

    def mine(cid: int) -> bool:
        return user is None or user.sees(int(cid))

    servos = {str(k): v.status().__dict__ for k, v in rt.servos.items() if mine(k)}
    presets = {str(k): [p.__dict__ for p in v.presets] for k, v in rt.state.data.channels.items() if mine(k)}
    conn = connection_info(rt)
    conn["channels"] = [c for c in conn["channels"] if mine(c["id"])]
    admin = user is None or user.role == Role.Administrator
    return web.json_response(
        {
            "me": _me_brief(user),
            "health": rt.health(),
            "ptz": servos,
            "presets": presets,
            "events": rt.events.current,
            "analytics": rt.analytics.as_dicts(),
            "users": rt.state.users_public() if admin else [],
            "wizard_done": rt.state.data.wizard_done or not admin,
            "odm_checks": rt.state.data.odm_checks,
            "connect": conn,
        }
    )


async def api_event_trigger(request: web.Request) -> web.Response:
    rt: Runtime = _rt(request)
    data = await request.json()
    kind = data.get("kind", "motion")
    if kind == "motion":
        rt.generator.trigger_motion() if rt.generator else rt.events.trigger(
            "tns1:VideoSource/MotionAlarm", True
        )
    elif kind == "tamper":
        rt.generator.trigger_tamper() if rt.generator else None
    else:
        rt.generator.trigger_analytics() if rt.generator else None
    return web.json_response({"ok": True, "current": rt.events.current})


def _me_brief(user) -> dict:
    if user is None:
        return {"username": "", "role": "Administrator", "channels": [], "anonymous": True}
    return {"username": user.username, "role": user.role.value, "channels": list(user.channels), "anonymous": False}


async def api_ptz(request: web.Request) -> web.Response:
    from onvif_emu.state import Preset
    from onvif_emu.util import new_uuid

    rt: Runtime = _rt(request)
    data = await request.json()
    cid = int(data.get("channel", 1))
    user = request_user(request)
    ch = next((c for c in rt.config.channels if c.id == cid), None)
    if ch is None or not ch.ptz_enabled or (user is not None and not user.sees(cid)):
        return web.json_response({"ok": False, "error": f"no PTZ on channel {cid} for you"}, status=403)
    servo = rt.ensure_servo(cid)
    chs = rt.state.channel(cid)
    cmd = data.get("cmd", "stop")
    now = __import__("time").monotonic()
    if cmd == "continuous":
        servo.continuous_move(float(data.get("pan", 0)), float(data.get("tilt", 0)), float(data.get("zoom", 0)), now=now)
    elif cmd == "absolute":
        servo.absolute_move(data.get("pan"), data.get("tilt"), data.get("zoom"), now=now)
    elif cmd == "stop":
        servo.stop(now=now)
    elif cmd == "iris":
        servo.nudge_iris(float(data.get("delta", 0.1)), now=now)
    elif cmd == "focus":
        servo.nudge_focus(float(data.get("delta", 0.1)), now=now)
    elif cmd == "home_goto":
        servo.absolute_move(getattr(chs, "home_pan", 0.0), getattr(chs, "home_tilt", 0.0), getattr(chs, "home_zoom", 0.0), now=now)
        servo.kind = "preset"
        servo.flash("HOME", now=now)
    elif cmd == "home_set":
        st = servo.status()
        chs.home_pan, chs.home_tilt, chs.home_zoom = st.pan, st.tilt, st.zoom
        rt.state.mark_dirty()
        rt.state.save()
        servo.flash("SET HOME", now=now)
    elif cmd == "preset_set":
        name = str(data.get("name") or "Preset")
        token = str(data.get("token") or new_uuid()[:8])
        st = servo.status()
        chs.presets = [p for p in chs.presets if p.token != token]
        chs.presets.append(Preset(token=token, name=name, pan=st.pan, tilt=st.tilt, zoom=st.zoom))
        rt.state.mark_dirty()
        rt.state.save()
        servo.flash(f"SET PRESET: {name}", now=now)
    elif cmd == "preset_goto":
        token = str(data.get("token") or "")
        p = next((p for p in chs.presets if p.token == token), None)
        if p:
            servo.absolute_move(p.pan, p.tilt, p.zoom, now=now)
            servo.kind = "preset"
            servo.flash(f"GOTO PRESET: {p.name}", now=now)
    elif cmd == "preset_remove":
        token = str(data.get("token") or "")
        removed = next((p for p in chs.presets if p.token == token), None)
        chs.presets = [p for p in chs.presets if p.token != token]
        rt.state.mark_dirty()
        rt.state.save()
        if removed:
            servo.flash(f"DEL PRESET: {removed.name}", now=now)
    return web.json_response({**servo.status().__dict__, "presets": [p.__dict__ for p in chs.presets]})


async def api_export(request: web.Request) -> web.Response:
    """The complete configuration (in a fleet: the shared root incl. all devices, rules and port ranges)."""
    rt: Runtime = _rt(request)
    fmt = request.rel_url.query.get("fmt", "yaml")
    cfg = rt.fleet.root if rt.fleet else rt.config
    if fmt == "json":
        return web.Response(
            text=cfg.model_dump_json(indent=2),
            content_type="application/json",
            headers={"Content-Disposition": "attachment; filename=onvif-emu.json"},
        )
    import yaml

    text = yaml.safe_dump(json.loads(cfg.model_dump_json()), sort_keys=False, allow_unicode=True)
    return web.Response(
        text=text,
        content_type="text/yaml",
        headers={"Content-Disposition": "attachment; filename=onvif-emu.yaml"},
    )


async def api_import(request: web.Request) -> web.Response:
    rt: Runtime = _rt(request)
    data = await request.json()
    cfg = AppConfig.model_validate(data)
    if rt.fleet:  # the file describes the whole fleet: store it, devices pick it up on restart
        rt.fleet.root = cfg
        rt.fleet.save()
        return web.json_response({"ok": True, "restart_required": True})
    await apply_config(rt, cfg)
    return web.json_response({"ok": True})


async def api_wizard_done(request: web.Request) -> web.Response:
    rt: Runtime = _rt(request)
    rt.state.data.wizard_done = True
    rt.state.mark_dirty()
    rt.state.save()
    return web.json_response({"ok": True})


async def api_odm_check(request: web.Request) -> web.Response:
    rt: Runtime = _rt(request)
    data = await request.json()
    key = str(data.get("key", ""))
    val = bool(data.get("pass", False))
    if key:
        rt.state.data.odm_checks[key] = val
        rt.state.mark_dirty()
        rt.state.save()
    return web.json_response(rt.state.data.odm_checks)


async def api_locales(request: web.Request) -> web.Response:
    lang = normalize_lang(request.match_info.get("lang", DEFAULT_LANG))
    return web.json_response(load_locale(lang))


def _upload_name(raw: str | None, default: str) -> str:
    """Browsers send UTF-8 names as-is, other clients percent-encode them (%D0%9B...): accept both."""
    from urllib.parse import unquote

    return unquote(raw) if raw else default


async def api_videos_list(request: web.Request) -> web.Response:
    rt: Runtime = _rt(request)
    folder = resolve_video_dir(rt.config.video_dir)
    rt.video_dir = folder
    return web.json_response({"dir": str(folder), "files": list_videos(folder)})


async def api_videos_upload(request: web.Request) -> web.Response:
    rt: Runtime = _rt(request)
    folder = resolve_video_dir(rt.config.video_dir)
    rt.video_dir = folder
    reader = await request.multipart()
    saved = None
    channel = None
    max_upload_bytes = rt.config.limits.max_upload_mb * 1024 * 1024
    while True:
        part = await reader.next()
        if part is None:
            break
        if part.name == "channel":
            channel = (await part.text()).strip()
            continue
        if part.name not in {"file", "video"}:
            continue
        filename = _upload_name(part.filename, "upload.mp4")
        try:
            dest = dest_path(folder, filename, unique=True)
            while True:
                try:
                    fh = dest.open("xb")
                    break
                except FileExistsError:
                    dest = dest_path(folder, filename, unique=True)
        except ValueError as exc:
            return web.json_response({"ok": False, "error": str(exc)}, status=400)
        size = 0
        complete = False
        try:
            with fh:
                while True:
                    chunk = await part.read_chunk(1024 * 1024)
                    if not chunk:
                        break
                    if size + len(chunk) > max_upload_bytes:
                        return web.json_response(
                            {"ok": False, "error": f"video exceeds the {rt.config.limits.max_upload_mb} MB upload limit"},
                            status=413,
                        )
                    size += len(chunk)
                    fh.write(chunk)
            complete = True
        finally:
            if not complete:
                dest.unlink(missing_ok=True)
        saved = {"name": dest.name, "size": size, "path": str(dest)}
        log.info("Uploaded video %s (%s bytes)", dest, size)
    if not saved:
        return web.json_response({"ok": False, "error": "no file"}, status=400)
    saved["video"] = await _probe(rt, saved["path"])
    if saved["video"] is False:  # not a video after all: do not keep it in the library
        Path(saved["path"]).unlink(missing_ok=True)
        return web.json_response({"ok": False, "error": f"no video stream in {saved['name']}"}, status=400)
    if channel:
        try:
            await _use_file(rt, int(channel), saved["path"])
        except ValueError:
            pass
    return web.json_response({"ok": True, "file": saved, "dir": str(folder), "files": list_videos(folder)})


async def _probe(rt: Runtime, path: str) -> dict | bool | None:
    """Video stream info; False = the file has no video stream; None = cannot tell (no ffprobe)."""
    import asyncio

    try:
        info = await asyncio.to_thread(probe_video, path, find_ffprobe(find_ffmpeg(rt.config.ffmpeg.binary)))
    except FileNotFoundError:
        return None
    return info if info else False


async def _use_file(rt: Runtime, cid: int, path: str) -> bool:
    ch = next((c for c in rt.config.channels if c.id == cid), None)
    if not ch:
        return False
    ch.source.uri = path
    ch.source.type = SourceType.file
    rt.save_config()
    if rt.pipelines:
        await rt.pipelines.restart_channel(cid)
    return True


async def api_videos_local(request: web.Request) -> web.Response:
    """Play a file that already lies on this machine (no copy): {"channel": 1, "path": "D:/clips/a.mp4"}."""
    rt: Runtime = _rt(request)
    data = await request.json()
    try:
        path = check_local_file(str(data.get("path", "")))
        cid = int(data.get("channel", 0))
    except (ValueError, TypeError) as exc:
        return web.json_response({"ok": False, "error": str(exc)}, status=400)
    info = await _probe(rt, str(path))
    if info is False:
        return web.json_response({"ok": False, "error": f"no video stream in {path}"}, status=400)
    if not await _use_file(rt, cid, str(path)):
        return web.json_response({"ok": False, "error": f"no channel {cid}"}, status=404)
    log.info("Channel %s now plays local file %s", cid, path)
    return web.json_response({"ok": True, "path": str(path), "video": info})


async def api_videos_browse(request: web.Request) -> web.Response:
    """Folders and video files on the emulator's machine, one level: ?dir=D:/clips (empty = drives / root)."""
    import asyncio

    from onvif_emu.pipeline.effects import IMAGE_EXTS

    exts = IMAGE_EXTS if request.query.get("kind") == "image" else None
    try:
        listing = await asyncio.to_thread(browse, request.query.get("dir", ""), exts)
    except ValueError as exc:
        return web.json_response({"ok": False, "error": str(exc)}, status=400)
    return web.json_response({"ok": True, **listing})


async def _use_osd_image(rt: Runtime, path: str) -> None:
    rt.config.osd.image.path = path
    rt.config.osd.image.enabled = True
    rt.save_config()
    if rt.media:
        await rt.media.restart_pipelines()


async def api_osd_image_upload(request: web.Request) -> web.Response:
    """Upload a picture for the OSD into osd/ beside the program and show it."""
    from onvif_emu.paths import executable_dir
    from onvif_emu.pipeline.effects import IMAGE_EXTS

    rt: Runtime = _rt(request)
    folder = executable_dir() / "osd"
    folder.mkdir(parents=True, exist_ok=True)
    reader = await request.multipart()
    saved = None
    while True:
        part = await reader.next()
        if part is None:
            break
        if part.name not in {"file", "image"}:
            continue
        try:
            dest = dest_path(folder, _upload_name(part.filename, "logo.png"), unique=True, exts=IMAGE_EXTS)
        except ValueError as exc:
            return web.json_response({"ok": False, "error": str(exc)}, status=400)
        with dest.open("wb") as fh:
            while chunk := await part.read_chunk(1024 * 1024):
                fh.write(chunk)
        saved = str(dest)
    if not saved:
        return web.json_response({"ok": False, "error": "no file"}, status=400)
    await _use_osd_image(rt, saved)
    return web.json_response({"ok": True, "path": saved})


async def api_osd_image_local(request: web.Request) -> web.Response:
    """Show a picture that already lies on this machine: {"path": "D:/logo.png"}."""
    from onvif_emu.pipeline.effects import IMAGE_EXTS

    rt: Runtime = _rt(request)
    data = await request.json()
    try:
        path = check_local_file(str(data.get("path", "")))
    except ValueError as exc:
        return web.json_response({"ok": False, "error": str(exc)}, status=400)
    if path.suffix.lower() not in IMAGE_EXTS:
        return web.json_response({"ok": False, "error": f"not a picture: {path.name}"}, status=400)
    await _use_osd_image(rt, str(path))
    return web.json_response({"ok": True, "path": str(path)})


async def api_channel_impair(request: web.Request) -> web.Response:
    """Replace one channel's trouble simulator block and restart only that channel."""
    from onvif_emu.models import ImpairConfig

    rt: Runtime = _rt(request)
    cid = int(request.match_info["channel"])
    ch = next((c for c in rt.config.channels if c.id == cid), None)
    if ch is None:
        return web.json_response({"ok": False, "error": f"no channel {cid}"}, status=404)
    try:
        ch.impair = ImpairConfig.model_validate(await request.json())
    except Exception as exc:  # noqa: BLE001 - pydantic / JSON errors go back to the GUI
        return web.json_response({"ok": False, "error": str(exc)}, status=400)
    rt.save_config()
    if rt.media:
        await rt.media.restart_channel(cid)
    log.info("Channel %s trouble simulator: %s", cid, "on" if ch.impair.enabled else "off")
    return web.json_response({"ok": True, "impair": json.loads(ch.impair.model_dump_json())})


async def api_channel_restart(request: web.Request) -> web.Response:
    """Start a channel again (e.g. after a clip played once and the stream ended)."""
    rt: Runtime = _rt(request)
    cid = int(request.match_info["channel"])
    if not any(c.id == cid for c in rt.config.channels):
        return web.json_response({"ok": False, "error": f"no channel {cid}"}, status=404)
    if rt.media:
        await rt.media.restart_channel(cid)
    return web.json_response({"ok": True})


async def api_i18n_meta(request: web.Request) -> web.Response:
    rt: Runtime = _rt(request)
    return web.json_response(
        {
            "default": DEFAULT_LANG,
            "languages": list(SUPPORTED_LANGS),
            "current": normalize_lang(rt.config.language),
            "cli": "en",
            # shown on the sign-in screen (also public via WS-Discovery / GetDeviceInformation scopes)
            "device": {"name": rt.config.device.name, "model": rt.config.device.model},
            "version": __import__("onvif_emu").__version__,
        }
    )


async def index_html(request: web.Request) -> web.StreamResponse:
    # ODM's "Web page" opens the device in an embedded IE that cannot run the GUI's JS
    if is_legacy_browser(request.headers.get("User-Agent")):
        return await info_html(request)
    return web.FileResponse(STATIC_DIR / "index.html")


async def info_html(request: web.Request) -> web.Response:
    """Script-free connection page (URIs, profiles, auth) for any browser."""
    rt = _rt(request)
    lang = pick_lang(request.headers.get("Accept-Language"), request.query.get("lang"), normalize_lang(rt.config.language))
    return web.Response(text=legacy_page(rt, lang), content_type="text/html", charset="utf-8")


@web.middleware
async def request_host_middleware(request: web.Request, handler):
    """Remember which address the client used, so XAddr / RTSP URIs are reachable from that client."""
    from onvif_emu.runtime import REQUEST_HOST

    host = request.host or ""
    if host.startswith("["):  # [::1]:8080
        host = host.split("]", 1)[0] + "]"
    else:
        host = host.rsplit(":", 1)[0] if host.count(":") == 1 else host
    token = REQUEST_HOST.set(host)
    try:
        return await handler(request)
    finally:
        REQUEST_HOST.reset(token)


@web.middleware
async def error_middleware(request: web.Request, handler):
    try:
        return await handler(request)
    except web.HTTPException:
        raise
    except Exception:
        log.exception("Request failed %s %s", request.method, request.path)
        if "/onvif" in request.path:
            return web.Response(
                text=soap_fault("ter:Action", "Internal error", "ter:Action"),
                content_type="application/soap+xml",
                status=500,
            )
        return web.json_response({"ok": False, "error": "internal"}, status=500)


def create_app(runtime: Runtime) -> web.Application:
    app = web.Application(
        client_max_size=MAX_API_BODY_BYTES,
        middlewares=[request_host_middleware, error_middleware, auth_middleware],
    )
    app[RUNTIME_KEY] = runtime
    from onvif_emu.api_diag import register as register_diag

    register_diag(app, runtime)
    register_webauth(app)
    app.router.add_get("/health", handle_health)
    app.router.add_get("/snapshot/{channel}", handle_snapshot)
    app.router.add_get("/onvif/{service}", handle_onvif)
    app.router.add_post("/onvif/{service}", handle_onvif)
    app.router.add_get("/onvif", handle_onvif)
    app.router.add_post("/onvif", handle_onvif)
    app.router.add_get("/api/config", api_config_get)
    app.router.add_put("/api/config", api_config_put)
    app.router.add_get("/api/status", api_status)
    app.router.add_post("/api/events/trigger", api_event_trigger)
    app.router.add_post("/api/ptz", api_ptz)
    app.router.add_get("/api/export", api_export)
    app.router.add_post("/api/import", api_import)
    app.router.add_post("/api/wizard", api_wizard_done)
    app.router.add_post("/api/odm-check", api_odm_check)
    app.router.add_get("/api/i18n", api_i18n_meta)
    app.router.add_get("/api/locales/{lang}", api_locales)
    app.router.add_get("/api/videos", api_videos_list)
    app.router.add_post("/api/videos", api_videos_upload)
    app.router.add_post("/api/videos/local", api_videos_local)
    app.router.add_get("/api/videos/browse", api_videos_browse)
    app.router.add_post("/api/osd/image", api_osd_image_upload)
    app.router.add_post("/api/osd/image/local", api_osd_image_local)
    app.router.add_put("/api/channels/{channel}/impair", api_channel_impair)
    app.router.add_post("/api/channels/{channel}/restart", api_channel_restart)
    app.router.add_get("/info", info_html)
    if STATIC_DIR.is_dir():
        app.router.add_get("/", index_html)
        app.router.add_static("/static/", STATIC_DIR, show_index=False)
    else:
        app.router.add_get("/", handle_health)
    return app
