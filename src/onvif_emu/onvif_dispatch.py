from __future__ import annotations

import logging

from onvif_emu.models import UserRecord
from onvif_emu.services import analytics, device, events, imaging, media, ptz
from onvif_emu.soap import SoapRequest, soap_fault, soap_ok

log = logging.getLogger(__name__)

PATH_HANDLERS = {
    "device_service": device.handle_device,
    "media_service": media.handle_media,
    "ptz_service": ptz.handle_ptz,
    "imaging_service": imaging.handle_imaging,
    "events_service": events.handle_events,
    "analytics_service": analytics.handle_analytics,
}


def dispatch(
    rt,
    req: SoapRequest,
    path: str,
    *,
    user: UserRecord | None = None,
    anonymous: bool = False,
) -> str:
    from onvif_emu.auth import AuthError, require_action_role

    try:
        require_action_role(user, req.action, anonymous=anonymous)
    except AuthError as exc:
        log.warning("ONVIF role deny action=%s user=%s: %s", req.action, getattr(user, "username", None), exc)
        return soap_fault("NotAuthorized", str(exc), "ter:NotAuthorized")

    from onvif_emu.access import acting_as

    with acting_as(user):
        return _dispatch(rt, req, path, user=user, anonymous=anonymous)


def _dispatch(rt, req: SoapRequest, path: str, *, user: UserRecord | None, anonymous: bool) -> str:
    hint = path.rstrip("/").rsplit("/", 1)[-1]
    handler = PATH_HANDLERS.get(hint)
    log.debug("ONVIF dispatch action=%s path=%s user=%s anon=%s", req.action, path, getattr(user, "username", None), anonymous)
    try:
        if handler is not None:
            return _ok(handler(rt, req))
        # Ambiguous endpoint: prefer unique actions, then Device.
        action = req.action
        table = [
            (media.handle_media, media),
            (ptz.handle_ptz, ptz),
            (imaging.handle_imaging, imaging),
            (events.handle_events, events),
            (analytics.handle_analytics, analytics),
            (device.handle_device, device),
        ]
        # Stop + GetStatus collide: default Stop/GetStatus without path → PTZ (ODM PTZ panel).
        if action in {"Stop", "GetStatus"}:
            return _ok(ptz.handle_ptz(rt, req))
        if action in {"GetServiceCapabilities"}:
            return _ok(device.handle_device(rt, req))
        for fn, _mod in table:
            inner = fn(rt, req)
            if "ActionNotSupported" not in inner and "Unknown" not in inner[:80]:
                return _ok(inner)
        log.info("Unhandled ONVIF action %s path=%s", action, path)
        return soap_fault("ActionNotSupported", f"Unsupported action {action}")
    except Exception:
        log.exception("ONVIF handler crash on %s", req.action)
        return soap_fault("Action", "Internal error", "ter:Action")


def _ok(inner: str) -> str:
    if inner.lstrip().startswith("<?xml") or inner.lstrip().startswith("<s:Envelope"):
        return inner
    return soap_ok(inner)


async def dispatch_pull(rt, req: SoapRequest, *, user=None, anonymous=False) -> str:
    """Long polling without occupying the shared thread pool."""
    from onvif_emu.access import acting_as
    from onvif_emu.auth import AuthError, require_action_role

    try:
        require_action_role(user, req.action, anonymous=anonymous)
        with acting_as(user):
            return _ok(await events.pull_messages_async(rt, req))
    except AuthError as exc:
        return soap_fault("NotAuthorized", str(exc), "ter:NotAuthorized")
    except (ValueError, OverflowError):
        return soap_fault("InvalidArgs", "Invalid pull parameters", "ter:InvalidArgVal")
