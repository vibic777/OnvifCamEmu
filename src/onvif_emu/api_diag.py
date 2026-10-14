"""REST API of the diagnostic device and the fleet (all under /api, protected like the rest of the GUI API)."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Any

from aiohttp import web

from onvif_emu.models import AppConfig, DiagConfig, EventRule

if TYPE_CHECKING:
    from onvif_emu.runtime import Runtime

log = logging.getLogger(__name__)

RUNTIME_KEY = web.AppKey("diag_runtime", object)


def _rt(request: web.Request) -> Runtime:
    return request.app[RUNTIME_KEY]  # type: ignore[return-value]


def _err(msg: str, status: int = 400) -> web.Response:
    return web.json_response({"ok": False, "error": msg}, status=status)


def _save(rt: Runtime) -> None:
    rt.diag.sync_config()
    rt.save_config()


def _limits_view(rt: Runtime) -> dict[str, Any]:
    from onvif_emu.fleet import build_fleet
    from onvif_emu.limits import estimate

    root = rt.fleet.root if rt.fleet else rt.config
    view: dict[str, Any] = {"estimate": estimate(build_fleet(root)).as_dict(), "mode": rt.config.limits.mode,
                            "shed_level": rt.shed_level, "dropped_events": rt.diag.dropped}
    gov = getattr(rt.fleet, "governor", None)
    if gov is not None:
        view["governor"] = gov.status()
    return view


async def diag_get(request: web.Request) -> web.Response:
    rt = _rt(request)
    return web.json_response(
        {
            "config": json.loads(rt.config.diag.model_dump_json()),
            "status": rt.diag.status(),
            "instance": rt.config.instance,
            "fleet": bool(rt.fleet),
            "limits": _limits_view(rt),
        }
    )


async def diag_put(request: web.Request) -> web.Response:
    """Replace the whole diagnostics block (relays, inputs, rules, webhooks)."""
    rt = _rt(request)
    try:
        rt.config.diag = DiagConfig.model_validate(await request.json())
    except Exception as exc:  # noqa: BLE001
        return _err(str(exc))
    _save(rt)
    return web.json_response({"ok": True, "status": rt.diag.status()})


async def rule_add(request: web.Request) -> web.Response:
    rt = _rt(request)
    body = await request.json()
    if "device" not in body:  # created in a device's own GUI => belongs to that device
        body["device"] = rt.config.instance if rt.fleet and rt.config.instance > 1 else 0
    try:
        rule = EventRule.model_validate(body)
    except Exception as exc:  # noqa: BLE001
        return _err(str(exc))
    if len(rt.config.diag.rules) >= rt.config.limits.max_rules and rt.config.limits.mode != "off":
        return _err(f"max_rules ({rt.config.limits.max_rules}) reached")
    rt.config.diag.rules.append(rule)
    _save(rt)
    return web.json_response({"ok": True, "rule": json.loads(rule.model_dump_json())})


async def rule_update(request: web.Request) -> web.Response:
    rt = _rt(request)
    rid = request.match_info["rid"]
    body = await request.json()
    rules = rt.config.diag.rules
    for i, old in enumerate(rules):
        if old.id == rid:
            try:
                merged = {**json.loads(old.model_dump_json()), **body, "id": rid}
                rules[i] = EventRule.model_validate(merged)
            except Exception as exc:  # noqa: BLE001
                return _err(str(exc))
            _save(rt)
            return web.json_response({"ok": True, "rule": json.loads(rules[i].model_dump_json())})
    return _err("no such rule", 404)


async def rule_delete(request: web.Request) -> web.Response:
    rt = _rt(request)
    rid = request.match_info["rid"]
    before = len(rt.config.diag.rules)
    rt.config.diag.rules = [r for r in rt.config.diag.rules if r.id != rid]
    if len(rt.config.diag.rules) == before:
        return _err("no such rule", 404)
    _save(rt)
    return web.json_response({"ok": True})


async def fire(request: web.Request) -> web.Response:
    """Fire a stored rule once (``{"rule_id": ...}``) or an ad-hoc event described inline (``{"rule": {...}}``)."""
    rt = _rt(request)
    body = await request.json()
    wait = request.rel_url.query.get("wait") == "1"  # a pulse lasts hold_s; by default answer right away
    try:
        if body.get("rule_id"):
            rid = str(body["rule_id"])
            if rid not in rt.diag.rules:
                return _err("no such rule", 404)
            coro = rt.diag.fire_rule(rid)
        else:
            rule = EventRule.model_validate({**(body.get("rule") or body), "schedule": "manual"})
            coro = rt.diag.fire(rule)
    except Exception as exc:  # noqa: BLE001
        return _err(str(exc))
    if wait:
        events = await coro
        return web.json_response({"ok": True, "events": len(events)})
    task = asyncio.get_running_loop().create_task(coro)
    task.add_done_callback(lambda t: t.exception() and log.warning("fire failed: %s", t.exception()))
    return web.json_response({"ok": True, "queued": True})


async def relay_set(request: web.Request) -> web.Response:
    rt = _rt(request)
    tok = request.match_info["token"]
    body = await request.json()
    if tok not in rt.diag.relays:
        return _err("no such relay", 404)
    rt.diag.set_relay(tok, bool(body.get("active", True)))
    return web.json_response({"ok": True, "status": rt.diag.status()["relays"]})


async def input_set(request: web.Request) -> web.Response:
    rt = _rt(request)
    tok = request.match_info["token"]
    body = await request.json()
    if tok not in rt.diag.inputs:
        return _err("no such input", 404)
    rt.diag.set_input(tok, bool(body.get("active", True)))
    return web.json_response({"ok": True, "status": rt.diag.status()["inputs"]})


async def diag_log(request: web.Request) -> web.Response:
    rt = _rt(request)
    limit = max(1, min(1000, int(request.rel_url.query.get("limit", "100"))))
    return web.json_response({"events": rt.diag.recent(limit)})


async def metadata_get(request: web.Request) -> web.Response:
    rt = _rt(request)
    ch = int(request.match_info.get("channel", "1"))
    return web.Response(text=rt.diag.metadata_xml(ch), content_type="application/xml")


async def metadata_stream(request: web.Request) -> web.StreamResponse:
    """Chunked ONVIF MetadataStream frames (analytics objects), ~5 per second, until the client leaves."""
    rt = _rt(request)
    ch = int(request.match_info.get("channel", "1"))
    resp = web.StreamResponse(headers={"Content-Type": "application/xml", "Cache-Control": "no-store"})
    await resp.prepare(request)
    try:
        while True:
            await resp.write((rt.diag.metadata_xml(ch) + "\n").encode())
            await asyncio.sleep(0.2)
    except (ConnectionResetError, asyncio.CancelledError):
        pass
    return resp


async def limits_get(request: web.Request) -> web.Response:
    return web.json_response(_limits_view(_rt(request)))


async def fleet_get(request: web.Request) -> web.Response:
    rt = _rt(request)
    if rt.fleet:
        return web.json_response({"devices": rt.fleet.status(), "root_file": str(rt.fleet.root_path or ""),
                                  "fleet": json.loads(rt.fleet.root.fleet.model_dump_json())})
    return web.json_response({"devices": [], "root_file": str(rt.config_path or ""), "fleet": {}})


async def fleet_put(request: web.Request) -> web.Response:
    """Change the fleet definition (count / port ranges). Saved to the config file; takes effect on restart."""
    rt = _rt(request)
    if not rt.fleet:
        return _err("not running as a fleet")
    body = await request.json()
    root = json.loads(rt.fleet.root.model_dump_json())
    for k in ("count", "onvif_ports", "rtsp_ports"):
        if k in body:
            root["fleet"][k] = body[k]
    try:
        rt.fleet.root = AppConfig.model_validate(root)
    except Exception as exc:  # noqa: BLE001
        return _err(str(exc))
    rt.fleet.save()
    return web.json_response({"ok": True, "restart_required": True})


def register(app: web.Application, runtime: Runtime) -> None:
    app[RUNTIME_KEY] = runtime
    r = app.router
    r.add_get("/api/diag", diag_get)
    r.add_put("/api/diag", diag_put)
    r.add_post("/api/diag/rules", rule_add)
    r.add_put("/api/diag/rules/{rid}", rule_update)
    r.add_delete("/api/diag/rules/{rid}", rule_delete)
    r.add_post("/api/diag/fire", fire)
    r.add_post("/api/diag/relay/{token}", relay_set)
    r.add_post("/api/diag/input/{token}", input_set)
    r.add_get("/api/diag/log", diag_log)
    r.add_get("/api/metadata/{channel}", metadata_get)
    r.add_get("/api/metadata/stream/{channel}", metadata_stream)
    r.add_get("/api/limits", limits_get)
    r.add_get("/api/fleet", fleet_get)
    r.add_put("/api/fleet", fleet_put)
