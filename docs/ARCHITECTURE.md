# OnvifCamEmu — architecture & backlog

Living document (mirror of the Cursor plan). Discussion notes and phase checklist live here too.

## Discussion log

| Date | Topic | Note |
|------|-------|------|
| 2026-09-13 | Start | Full review: tests green, ruff noise, critical auth/media/docker gaps. |
| 2026-09-13 | Defaults | Admin API = same ONVIF users; RTSP URI credentials opt-in (`auth.embed_rtsp_credentials`); phases A→D. |
| 2026-09-13 | Phase A | **Сделано:** C1/C2/C3, M1/M2/M11, GUI login, тесты зелёные. |
| 2026-09-13 | Phase B | **Сделано:** C4 stderr DEVNULL, C5 healthy backoff, C6 publish_host + Compose, M3/M4 MTX restart, M16 substream. |
| 2026-09-13 | Phase C | **Сделано:** IdentityStore, MediaPlane, ConfigApplyResult, AppFacade (HTTP+discovery), SystemReboot = process stop. |
| 2026-09-13 | Phase D | **Сделано:** install.sh venv, roles, OSD config reload, stable EPR, ruff CI. |
| 2026-09-22 | Phase F | **Done:** PTZ preset/home/aux OSD banner, wider PTZ GUI panel (presets, home, iris/focus), Windows tray icon + app icon, `--tray`/`--no-tray`. |
| 2026-09-23 | Phase G | **Done:** Connect page + script-free `/info` (ODM "Web page" = embedded IE), analytics kinds (motion region, counting, loitering). |
| 2026-09-23 | Phase J | **Done:** encrypted account store, three roles with channel limits (web, ONVIF, RTSP), sign-in page + sessions, preview proxy, redesigned bilingual UI. |
| 2026-09-23 | Phase I | **Done:** burnt-in OSD (time/date stamps, picture), clip end action, per-channel trouble simulator with Tampering events and scheduled outages. |
| 2026-09-23 | Phase H | **Done:** video source = upload or local path (browse), any FFmpeg format with codec-aware remux/re-encode, MediaMTX side ports resolved per instance, MediaMTX output captured. |

## Process model (as-is)

Single asyncio process: HTTP (ONVIF + GUI) + optional WS-Discovery + MediaMTX + per-channel FFmpeg.

| Layer | Modules | Role |
|-------|---------|------|
| CLI | `cli.py` | argparse, `_serve`, exit codes |
| Config | `models.py`, `config.py`, `confdump.py` | `AppConfig` |
| Runtime | `runtime.py` | hub (still god-object) |
| Persist | `state.py` | users, PTZ, scopes (`state.json`) |
| HTTP | `http_app.py` | SOAP, `/api/*`, health, snapshot |
| Auth | `auth.py` | Digest / Basic / WSSE |
| Media | `pipeline/*` | MediaMTX + FFmpeg |
| ONVIF | `services/*`, `onvif_dispatch.py` | Profile S-ish handlers |

**Identity:** accounts live in `users.py` / `data/users.json` (encrypted by `secret.py`), shared by ONVIF, RTSP and the web UI. `auth.username`/`auth.password`/`users` in a config are one-shot inputs (CLI, old files), applied by `StateStore.apply_config_accounts()` and never written back.

**Admin API:** middleware requires Basic or Digest when `auth.mode != none`. Public: `/`, `/static/`, `/health`, `/api/i18n`, `/api/locales/*`. GUI stores Basic in `sessionStorage`.

**RTSP URI:** no `user:pass@` unless `auth.embed_rtsp_credentials: true`.

## Target (evolutionary)

Shipped in Phase C:

- `AppFacade` (`app.py`) — process surface (HTTP + discovery)
- `IdentityStore` (`identity.py`) — users/scopes/hostname
- `MediaPlane` (`media_plane.py`) — MediaMTX + pipelines
- `apply_config` / `ConfigApplyResult` (`config_apply.py`)

`Runtime` remains the wiring hub for SOAP handlers (`rt.*` compat aliases for `mediamtx` / `pipelines`).

## Backlog

### Phase A — Ownership + security — DONE

- [x] C1 Admin API auth
- [x] C2 Sync identity on PUT/import
- [x] C3 Digest reject unknown nonce
- [x] M1 DeleteUsers keep last Administrator
- [x] M2 RTSP URI opt-in (`embed_rtsp_credentials`)
- [x] M11 WSSE Created required

### Phase B — MediaPlane — DONE

- [x] C4 FFmpeg stderr DEVNULL (no PIPE deadlock)
- [x] C5 Backoff reset only after healthy window (~10s)
- [x] C6 `mediamtx.publish_host` + Compose `mediamtx`
- [x] M3 Config PUT restarts MediaMTX when ports/host/embedded change
- [x] M4 MediaMTX supervise + restart with backoff
- [x] M16 Substream death triggers reconnect

### Phase C — Boundaries — DONE

- [x] IdentityStore (`identity.py`) over users/scopes/hostname
- [x] MediaPlane (`media_plane.py`) owns MTX + pipelines
- [x] ConfigApplyResult + `apply_config()` (`config_apply.py`)
- [x] AppFacade (`app.py`) owns HTTP + discovery + shutdown
- [x] M5 SystemReboot = process stop in 3s (supervisor may restart)

### Phase D — Polish — DONE

- [x] C7 `install.sh` installs via venv + `pip install`
- [x] M12 Roles Viewer/Operator/Administrator enforced on ONVIF actions
- [x] M17 OSD/imaging config change restarts encode pipeline
- [x] M15 Stable Discovery EPR (`state.endpoint_uuid`)
- [x] Ruff clean (CI lint job) + junitxml for GitHub/GitLab

### Phase E — Diagnostic device, fleet, resource control (v1.3) — DONE
New modules (all under `src/onvif_emu/`):

| Module | Role |
|---|---|
| `diag.py` | `DiagEngine`: event rules (manual / interval / random range, units s/m/h), relays, dry contacts, value templates, push (webhook, WS-Notify), overlay state, metadata frames |
| `api_diag.py` | REST `/api/diag/*`, `/api/metadata/*`, `/api/limits`, `/api/fleet` |
| `fleet.py` | derives N device configs from the root template (port ranges, overrides, rules per device), `Fleet` runner, write-back of GUI edits |
| `constructor.py` | console constructor: `--rule` parser, `--streams` ladder, `--source`, channel patches |
| `limits.py`, `sysmon.py` | load estimate, clamp/strict, runtime `Governor` (CPU/RAM, stream shedding) |
| `overlay.py` | pure-Python RGBA overlay (PTZ HUD, badges, boxes, LEDs) fed to FFmpeg's second input |
| `gui/static/diag.js` | Diagnostics / Fleet pages and the channel constructor |

Process model: one process, one asyncio loop, `Fleet` -> N x `AppFacade` (each with its own `Runtime`, HTTP site, MediaMTX, pipelines, `DiagEngine`).
`Runtime.save_hook` redirects saving to the fleet's root file; `REQUEST_HOST` (a contextvar) makes XAddr/RTSP URIs follow the address the client used.
Pipelines: per channel up to three FFmpeg encoders (full/medium/low); with the overlay enabled each gets a raw RGBA second input on stdin.
MediaMTX: one instance per distinct RTSP port; socket "slots" keep RTP/RTCP/WebRTC-UDP apart between instances.

### Phase F — PTZ presets on screen, tray icon (v1.4) — DONE

| Module | Role |
|---|---|
| `ptz/servo.py` | `PtzServo.flash(label)`: names the active command (`SET PRESET: ...`, `GOTO PRESET: ...`, `HOME`, `AUX: ...`) for the OSD, 3 s TTL (`osd_label` / `osd_label_until`) |
| `services/ptz.py` | SOAP handlers for `SetPreset` / `GotoPreset` / `RemovePreset` / `GotoHomePosition` / `SetHomePosition` / `SendAuxiliaryCommand` call `servo.flash(...)` |
| `overlay.py` | draws the named-command banner top-center (independent of the PTZ-HUD corner) |
| `http_app.py` | `/api/ptz` gained `iris`, `focus`, `home_goto`, `home_set`, `preset_set`, `preset_goto`, `preset_remove`; `/api/status` now includes `presets` per channel |
| `gui/static/app.js` | PTZ page: iris/focus nudge, home goto/set, preset save/goto/remove list |
| `tray.py` | Windows notification-area icon via ctypes (no new dependency): Shell_NotifyIcon + a hidden message-only-ish window, "Open GUI" / "Exit" menu |
| `gui/static/app.ico` | multi-res (16…256px) PTZ-dome-camera icon, hand-encoded PNG-in-ICO (stdlib `zlib`/`struct`, no Pillow); used for both exe icons and the tray icon |
| `cli.py`, `winui.py` | `--tray` / `--no-tray` flag; `winui.apply_gui_defaults` turns it on by default for the windowed exe only (the console exe already has a taskbar/console presence, so it defaults off there) |

Why a tray icon at all: the windowed exe (`console=False`) has no window and thus no taskbar entry; before this the only sign it was running was Task Manager. `--no-tray` keeps that headless-looking behavior for anyone who wants a pure background service.

### Phase G — Connect page, ODM "Web page", analytics kinds (v1.5) — DONE

| Module | Role |
|---|---|
| `connect.py` | `connection_info(rt)`: device service URL, auth, per-channel profiles (token, codec, RTSP, snapshot, HLS); `legacy_page(rt)`: the same as plain HTML without scripts, RTSP credentials stripped; `is_legacy_browser(ua)` (MSIE/Trident) |
| `http_app.py` | `/info` route; `/` serves `legacy_page` to IE/Trident; `/api/status` adds `connect` |
| `gui/static/app.js` | "Connect" section (default), profile table on Channels, copy buttons, `#section` deep links |
| `diag.py` | topics `MotionRegionDetector/Motion`, `CountAggregation/Counter` (running total per rule/source, `Step`/`Count` overrides), `LoiteringDetector/ObjectIsLoitering`; default `Rule` names |
| `services/analytics.py`, `services/events.py` | new rule/module types advertised; `Count` typed `xs:int` in the TopicSet |
| `gui/static/diag.js` | rule kind labels, "Analytics kind" select instead of the free topic field |
| `procguard.py` | child lifetime: `bind_child(pid)` puts every FFmpeg/MediaMTX (and snapshot grab) into a Windows Job Object with KILL_ON_JOB_CLOSE (only the children, so a browser the emulator opened is not affected); Linux `preexec_fn` sets `PR_SET_PDEATHSIG`. `free_rtsp_port()` before MediaMTX starts: kills FFmpeg orphans publishing to our port and a MediaMTX orphan of this same install (same config path, parent gone), otherwise reports the owner (name, PID) |
| `media_plane.py`, `runtime.py` | `MediaPlane.error`; `/health` adds `mediamtx_error`, the Connect page and `/info` show it |

Why procguard: a hard-killed emulator used to leave MediaMTX and FFmpeg running. The orphan kept the RTSP port, the next start's MediaMTX exited at once (logged only at WARNING, below the default CRITICAL level), `/health` said `mediamtx: false`, and clients were quietly served by the stale instance with the old config.

Why a separate page for IE: ODM's "Web page" item hosts the device page in the WebBrowser control (IE engine), which throws a script error on the ES2020 GUI. Sniffing the UA keeps the modern GUI untouched while ODM users still get every address they need.

### Phase H — Video sources, side-by-side instances (v1.6) — DONE

| Module | Role |
|---|---|
| `videos.py` | extension list = what FFmpeg demuxes; `safe_filename` sanitizes (`,&+#'` → `_`) instead of rejecting; `dest_path(unique=True)` never overwrites; `check_local_file`, `browse(dir)` (drives on Windows); `probe_video` (ffprobe, cached by size/mtime); `can_copy` |
| `http_app.py` | `POST /api/videos/local {channel, path}`, `GET /api/videos/browse?dir=`; upload probes the file and refuses one without a video stream |
| `pipeline/manager.py` | `_must_encode`: a file is remuxed only when its codec equals the profile codec (h264 / hevc), otherwise re-encoded |
| `procguard.py` | `resolve_mtx_ports`: API/HLS/WebRTC TCP and RTP/RTCP/WebRTC-UDP moved off busy ports using the OS socket table (`netstat`; bind probes lie on Windows) |
| `media_plane.py`, `pipeline/mediamtx.py` | config per RTSP port `data/mediamtx_<port>.yml` (MediaMTX hot-reloads its file, a shared one let instances reconfigure each other); MediaMTX stdout captured, its error lands in `mediamtx_error`; effective HLS port used by Connect |
| `gui/static/app.js` | Channels: option 1 upload (XHR progress), option 2 local path + server-side browser, other sources folded; inline status instead of the hidden log |

### Phase I — OSD stamps / picture, clip end, trouble simulator (v1.7) — DONE

| Module | Role |
|---|---|
| `models.py` | `OsdStamp` / `OsdDateStamp` (own defaults, so a partial dict keeps the date format), `OsdImage`, `SourceConfig.end_action`, `ImpairBurst`, `ImpairConfig` per channel |
| `pipeline/effects.py` | drawtext stamps (format read from `data/osd_<kind>.txt`: no quoting levels), picture overlay (colorkey, opacity, plate), `end_filters` (tpad clone/black + realtime), `impair_filters` (select, crop shake, rain chain, noise, pixelate, cover boxes, VBR detail bursts), `impair_encoder_args` (CBR, cap, VBR headroom, `noise` bsf packet loss); bursts sit at the end of each period |
| `pipeline/ffmpeg.py` | `build_command` builds one graph: source → end/imaging/impair → scale → stamps → picture (input 2) → HUD (input 1, stdin); overlays `shortest=1` so a clip that ends ends the process |
| `pipeline/manager.py` | `state` (running/ended/outage), clip end detection (exit 0 on a non-looping file), scheduled outages (no backoff), Tampering events in step with shake/cover bursts |
| `http_app.py` | `PUT /api/channels/<id>/impair`, `POST /api/channels/<id>/restart`, `POST /api/osd/image`, `POST /api/osd/image/local`, browse `kind=image`; upload names percent-decoded |
| `constructor.py`, `cli.py`, `config.py` | `--impair N:SPEC`, `--osd-time*`, `--osd-date*`, `--osd-image*`, channel spec `end=` |
| `gui/static/app.js` | OSD page (stamps, picture, HUD), Trouble page (per channel, presets), loop / end / restart on Channels |

### Phase J — Accounts, roles, sign-in, bilingual UI (v1.8) — DONE

| Module | Role |
|---|---|
| `secret.py` | stdlib encryption for passwords (HMAC-SHA256 CTR keystream + HMAC tag, `enc1:` prefix, built-in key) |
| `users.py` | `UserStore` per data folder (shared by a fleet): load/migrate, admin guarantees, reset by a missing password, plain → encrypted rewrite, CRUD with `UserError` |
| `access.py` | `CURRENT_USER` contextvar set by `dispatch`; `Runtime.enabled_channels()` hides channels of Operator / Viewer inside ONVIF requests only |
| `webauth.py` | cookie sessions, sign-in with a fail limiter, route levels (view / ptz / admin), no `WWW-Authenticate` for GUI requests, `/api/users*`, `/api/me*`, `/preview/*` HLS proxy with a loopback-only MediaMTX account |
| `pipeline/mediamtx.py`, `media_plane.py` | per-user path permissions (channel limits on all three profiles), internal preview account, `refresh_users()` rewrites the file (MediaMTX hot-reload, streams keep running) |
| `gui/static/*` | sign-in screen, grouped sidebar by role, user menu / own password, Users and Live view pages, toasts, every text from `locales/*.json` (diag.js texts moved there) |

## Out of scope (for now)

Microservices, in-process HTTPS, real video analytics, SPA rewrite.
