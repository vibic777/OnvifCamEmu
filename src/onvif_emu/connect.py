"""Connection cheat sheet: ONVIF/RTSP/snapshot URLs per channel and profile.

Used by the web GUI (``/api/status`` -> ``connect``) and by the script-free
``/info`` page that old embedded browsers (ONVIF Device Manager uses IE) get
instead of the JS application.
"""

from __future__ import annotations

import re
from html import escape
from typing import Any

from onvif_emu.runtime import Runtime
from onvif_emu.services.media import TIER_NAME, profile_token, tiers


def is_legacy_browser(user_agent: str | None) -> bool:
    """IE / the WebBrowser control embedded in ODM cannot run the ES2020 GUI."""
    ua = user_agent or ""
    return "MSIE " in ua or "Trident/" in ua


def _strip_credentials(uri: str) -> str:
    return re.sub(r"^(rtsp://)[^@/]*@", r"\1", uri)


def connection_info(rt: Runtime, *, credentials: bool = True) -> dict[str, Any]:
    """``credentials=False`` drops user:pass embedded in RTSP URIs (public pages)."""
    cfg = rt.config
    host = rt.public_host()
    base = rt.base_url()
    channels = []
    for ch in rt.enabled_channels():
        profiles = []
        for tier in tiers(ch):
            enc = ch.encoder if tier == 0 else (ch.sub if tier == 1 else ch.third)
            profiles.append(
                {
                    "tier": tier,
                    "quality": TIER_NAME[tier],
                    "token": profile_token(ch, tier),
                    "codec": enc.codec.value,
                    "resolution": f"{enc.width}x{enc.height}",
                    "fps": enc.fps,
                    "rtsp": rt.stream_uri(ch, tier) if credentials else _strip_credentials(rt.stream_uri(ch, tier)),
                }
            )
        channels.append(
            {
                "id": ch.id,
                "name": ch.name,
                "ptz": ch.ptz_enabled,
                "profiles": profiles,
                "snapshot": f"{base}/snapshot/{ch.id}",
                "hls": f"{base}/preview/{ch.rtsp_path}/",  # MediaMTX player through the emulator (same sign-in)
            }
        )
    return {
        "host": host,
        "onvif_port": cfg.onvif_port,
        "device_service": f"{base}/onvif/device_service",
        "gui": f"{base}/",
        "auth": cfg.auth.mode.value,
        "rtsp_credentials_embedded": bool(cfg.auth.embed_rtsp_credentials),
        "discovery": cfg.discovery,
        "rtsp_error": rt.media.error if rt.media else "",
        "channels": channels,
    }


_CSS = """body{font-family:Segoe UI,Arial,sans-serif;background:#fff;color:#111;margin:16px;font-size:14px}
h1{font-size:20px}h2{font-size:16px;margin-top:20px}table{border-collapse:collapse;margin:6px 0}
td,th{border:1px solid #bbb;padding:4px 8px;text-align:left;vertical-align:top}th{background:#eee}
code{font-family:Consolas,monospace;background:#f3f3f3;padding:1px 3px}.note{background:#fff7d6;border:1px solid #e0c96b;padding:8px}"""


_L = {
    "ru": {
        "auth_off": "Авторизация выключена (none) — логин и пароль не нужны.",
        "auth_on": "Авторизация: <b>{mode}</b>. Логин и пароль те же, что для ONVIF и RTSP (раздел «Пользователи» веб-интерфейса).",
        "snapshot": "Снимок (JPEG)", "no_channels": "Нет доступных каналов", "title": "подключение",
        "rtsp_off": "RTSP не работает:",
        "note": "Это упрощённая страница для встроенного браузера ONVIF Device Manager / Internet Explorer. Полный веб-интерфейс откройте в Chrome, Edge или Firefox:",
        "device_service": "Адрес устройства (Device service)", "port": "Порт ONVIF",
        "disc_on": "включено — камера находится автоматически", "disc_off": "выключено — добавляйте вручную по адресу выше",
        "creds": "Учётные данные", "channels": "Каналы и профили",
        "channels_hint": "Каждый канал — отдельный VideoSource. Профиль 1 = основной поток, 2 = средний, 3 = низкий (если включены).",
        "th": ("Канал", "Профиль", "ONVIF token", "Поток", "RTSP URI"),
        "howto": "Как подключиться в ONVIF Device Manager",
        "steps": (
            "Refresh — устройство появится в списке (или Add → введите <code>{ds}</code>).",
            "Слева вверху введите логин/пароль и нажмите Log in.",
            "Live video — выбор канала/профиля: токены <code>profile_N</code>, <code>profile_N_sub</code>, <code>profile_N_third</code>.",
            "В VLC: Медиа → Открыть URL → RTSP URI из таблицы; VLC спросит тот же логин/пароль.",
        ),
    },
    "en": {
        "auth_off": "Authentication is off (none) — no user name or password needed.",
        "auth_on": "Authentication: <b>{mode}</b>. Same user name and password as ONVIF and RTSP (Users page of the web UI).",
        "snapshot": "Snapshot (JPEG)", "no_channels": "No channels available", "title": "connect",
        "rtsp_off": "RTSP is off:",
        "note": "This is a simplified page for the browser built into ONVIF Device Manager / Internet Explorer. Open the full web UI in Chrome, Edge or Firefox:",
        "device_service": "Device address (Device service)", "port": "ONVIF port",
        "disc_on": "on — the camera is found automatically", "disc_off": "off — add it manually with the address above",
        "creds": "Credentials", "channels": "Channels and profiles",
        "channels_hint": "Each channel is a separate VideoSource. Profile 1 = main stream, 2 = medium, 3 = low (when enabled).",
        "th": ("Channel", "Profile", "ONVIF token", "Stream", "RTSP URI"),
        "howto": "Connecting with ONVIF Device Manager",
        "steps": (
            "Refresh — the device appears in the list (or Add → enter <code>{ds}</code>).",
            "Enter user name / password at the top left and press Log in.",
            "Live video — pick a channel / profile: tokens <code>profile_N</code>, <code>profile_N_sub</code>, <code>profile_N_third</code>.",
            "VLC: Media → Open Network Stream → an RTSP URI from the table; VLC asks for the same user / password.",
        ),
    },
}


def pick_lang(accept_language: str | None, query_lang: str | None, default: str) -> str:
    if query_lang in _L:
        return query_lang
    al = (accept_language or "").lower()
    if al:
        return "ru" if al.startswith("ru") or ",ru" in al else "en"
    return default if default in _L else "ru"


def legacy_page(rt: Runtime, lang: str = "ru") -> str:
    """Plain HTML (no scripts) with everything needed to connect a client, in Russian or English."""
    L = _L.get(lang, _L["ru"])
    info = connection_info(rt, credentials=False)
    d = rt.config.device
    auth = info["auth"]
    auth_line = L["auth_off"] if auth == "none" else L["auth_on"].format(mode=escape(auth))
    rows = []
    for ch in info["channels"]:
        for p in ch["profiles"]:
            rows.append(
                "<tr>"
                f"<td>{ch['id']} — {escape(ch['name'])}</td>"
                f"<td>{p['tier'] + 1} ({p['quality']})</td>"
                f"<td><code>{escape(p['token'])}</code></td>"
                f"<td>{p['codec']} {p['resolution']}@{p['fps']}</td>"
                f"<td><code>{escape(p['rtsp'])}</code></td>"
                "</tr>"
            )
        rows.append(f"<tr><td></td><td colspan=\"3\">{L['snapshot']}</td><td><code>{escape(ch['snapshot'])}</code></td></tr>")
    table = "\n".join(rows) or f'<tr><td colspan="5">{L["no_channels"]}</td></tr>'
    ds = escape(info["device_service"])
    th = "".join(f"<th>{h}</th>" for h in L["th"])
    steps = "".join(f"<li>{s.format(ds=ds)}</li>" for s in L["steps"])
    other = "en" if lang == "ru" else "ru"
    rtsp_err = f'<p class="note"><b>{L["rtsp_off"]}</b> {escape(info["rtsp_error"])}</p>' if info["rtsp_error"] else ""
    return f"""<!DOCTYPE html>
<html lang="{lang}"><head><meta charset="UTF-8"/><meta http-equiv="X-UA-Compatible" content="IE=edge"/>
<title>{escape(d.name)} — {L['title']}</title><style>{_CSS}</style></head><body>
<p style="float:right"><a href="?lang={other}">{other.upper()}</a></p>
<h1>{escape(d.manufacturer)} {escape(d.model)} — {escape(d.name)}</h1>
{rtsp_err}
<p class="note">{L['note']} <code>{escape(info['gui'])}</code></p>
<h2>ONVIF</h2>
<table>
<tr><th>{L['device_service']}</th><td><code>{ds}</code></td></tr>
<tr><th>{L['port']}</th><td>{info['onvif_port']}</td></tr>
<tr><th>WS-Discovery</th><td>{L['disc_on'] if info['discovery'] else L['disc_off']}</td></tr>
<tr><th>{L['creds']}</th><td>{auth_line}</td></tr>
</table>
<h2>{L['channels']}</h2>
<p>{L['channels_hint']}</p>
<table><tr>{th}</tr>
{table}
</table>
<h2>{L['howto']}</h2>
<ol>{steps}</ol>
</body></html>"""
