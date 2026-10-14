# ONVIF Camera Emulator — Development Task List (for AI coding agent)

**Project:** ONVIF cam restreamer with PTZ (camera emulator + stream multiplier)
**Target test tool:** ONVIF Device Manager (ODM)
**Stack:** Python 3.11+ (asyncio), FFmpeg pipelines, MediaMTX as RTSP server, web GUI (FastAPI/aiohttp)

---

> **Status (v1.8.0):** phases 0-18 implemented and covered by tests; open items are marked `open` (they need a live ODM session or a long soak run). Phase 19 is planned work.

## Phase 0 — Scaffold & Infrastructure
- [x] T01. Project structure (monorepo layout, config loader YAML/JSON, logging, /health endpoint).
- [x] T02. Docker + docker-compose: app container + MediaMTX container, restart policies.
- [x] T03. State store: JSON persistent state (PTZ positions, presets, users, channel configs).

## Phase 1 — ONVIF Core (Device + Discovery)
- [x] T04. WS-Discovery responder: UDP multicast 239.255.255.250:3702, Probe → ProbeMatch, configurable scopes (onvif://www.onvif.org/...).
- [x] T05. ONVIF HTTP/SOAP server skeleton: correct ONVIF 2.x namespaces/WSDL, single entry for all services.
- [x] T06. Device service: GetSystemDateAndTime/SetSystemDateAndTime (local time, TZ, UTC offset, DST), GetDeviceInformation (configurable vendor/model/firmware/serial), GetCapabilities, GetScopes/SetScopes, GetHostname/SetHostname.
- [x] T07. Network config: GetNetworkProtocols/SetNetworkProtocols, NTP Get/Set.
- [x] T08. User management: GetUsers/CreateUsers/DeleteUsers/SetUser with roles (Administrator/Operator/Viewer); users shared with RTSP auth.

## Phase 2 — Authentication
- [x] T09. Auth middleware, 4 modes (GUI-switchable, applied to all ONVIF services AND RTSP): HTTP Digest / HTTP Basic / WS-Security UsernameToken (PasswordDigest) / anonymous.
- [x] T10. Credential validation for RTSP streams against the same user store.

## Phase 3 — Media + Restreamer
- [x] T11. Media service: GetProfiles/GetVideoSources/GetVideoSourceConfigurations/GetVideoEncoderConfigurations (H.264/H.265/MJPEG, resolution/FPS/bitrate/GOP from config).
- [x] T12. Source pipeline manager (FFmpeg): file (looped), RTSP/RTSPS, HTTP/HTTPS/HLS/DASH, UDP/RTP multicast; remux (copy) or transcode per channel; auto-reconnect with backoff.
- [x] T13. Publish each channel to MediaMTX (`rtsp://host:8554/chN`); GetStreamUri returns correct URI; optional multicast URI.
- [x] T14. Snapshot endpoint: grab current JPEG frame per channel; GetSnapshotUri.
- [x] T15. Stream multiplier: N channels (1..64), each = own source, own ONVIF profile, own RTSP path; enable/disable at runtime.

## Phase 4 — PTZ + Imaging
- [x] T16. Virtual PTZ servos: background state machine (pan/tilt degrees, zoom 0..1, iris, focus), speed-aware interpolation, configurable limits.
- [x] T17. PTZ service: GetNodes/GetNode, AbsoluteMove, RelativeMove, ContinuousMove, Stop, GetStatus (live position).
- [x] T18. Presets: GetPresets/SetPreset/RemovePreset/GotoPreset (persistent); optional preset tours + AutoMove + Flip.
- [x] T19. Imaging service: GetImagingSettings/SetImagingSettings/GetOptions — brightness, contrast, saturation, sharpness, backlight, white balance, exposure/iris, focus; apply brightness/contrast to pipeline.

## Phase 5 — OSD Overlay
- [x] T20. OSD renderer in pipeline (FFmpeg drawtext/drawbox or GStreamer): PTZ arrows (up/down/left/right/stop), zoom +/-, iris +/-, focus +/-, event icons (motion/tamper/analytics), text status (position, channel name, timestamp).
- [x] T21. OSD behavior: show icon while command/event active, configurable display duration; style config (color, size, opacity, position) from GUI.

## Phase 6 — Events & Analytics
- [x] T22. Events service: CreatePullPointSubscription, WS-PullPoint PullMessages, Renew, Unsubscribe, SetSynchronizationPoint, GetEventProperties.
- [x] T23. Event generator: tns1:VideoSource/MotionAlarm, tns1:VideoSource/Tampering, tns1:RuleEngine/FieldDetector/ObjectsInside, tns1:Device/Trigger/DigitalInput; modes = timer/random/periodic, real motion detection (optional), manual trigger from GUI; correct UTC+offset timestamps.
- [ ] T24. ODM Event Viewer verified receiving events.  _(open: needs a live ODM session)_
- [x] T25. Analytics service: GetVideoAnalyticsConfigurations, GetAnalyticsModuleOptions/Modules, GetRules/RuleOptions (motion, line-cross, field-intrusion modules).
- [x] T26. Metadata producer: per-frame ObjectTree/Frame with BoundingBox (normalized coords), class (Person/Vehicle/Animal/Unknown), confidence, track id; simulator mode (scripted boxes) + pluggable real detector.

## Phase 7 — Web GUI
- [x] T27. Built-in web GUI (FastAPI or aiohttp): setup wizard, menus — Device, Users, Channels, PTZ, Imaging, OSD, Events, Analytics, Security, System (logs, reboot).
- [x] T28. Live preview player per channel (HLS/WebRTC via MediaMTX).
- [x] T29. Config export/import (JSON/YAML); runtime apply without restart where possible; RU/EN localization.

## Phase 8 — Quality & Delivery
- [ ] T30. ODM test matrix execution (MATRIX_ODM.md): discovery, auth x4 modes, live view, snapshot, PTZ panel, presets, events, analytics metadata.  _(open: needs a live ODM session)_
- [ ] T31. Robustness: pipeline watchdog auto-restart, 24/7 soak test, memory leaks check.  _(open: 24/7 soak test not run yet)_
- [ ] T32. Final docs: README (run instructions), sample config, acceptance checklist sign-off.  _(open: final sign-off pending)_

---

## Definition of Done (global)
- ODM discovers the emulator, connects in all 4 auth modes, plays main/sub streams, shows snapshot, full PTZ works with live position feedback, presets persist, Event Viewer shows motion/tamper events, OSD icons visible on video during commands/events, channels appear as separate ONVIF profiles with separate RTSP URIs, analytics metadata returns BoundingBoxes.

## Phase 9 — Packaging & CLI
- [x] T33. PyInstaller one-file portable `onvif-emulator-vX.Y.Z-win64.exe` (embedded FFmpeg/MediaMTX or first-run downloader with cache); `--version`.
- [x] T34. Linux packaging: `bin/onvif-emulator` launcher, `install.sh` (/opt + optional systemd unit), `uninstall.sh`, portable `run.sh`; shellcheck-clean, exec bits set.
- [x] T35. Cleanup scripts `clean.sh`/`clean.ps1`/`make clean`: pycache, build/dist, venv, logs, downloaded artifacts; `--dry-run` mode.
- [ ] T36. CI release builds (GitHub Actions/GitLab CI): exe + linux tar.gz attached to tagged releases.  _(open: workflow files exist, tagged release not exercised)_
- [x] T37. Full CLI: every GUI parameter has a CLI flag; config priority CLI > -c file > default; env vars ONVIF_EMU_*; `--check-config`, meaningful exit codes, error messages pointing to --help.
- [x] T38. `onvif-emulator --help` (all flags, defaults, 5+ usage examples), `doctor` subcommand (ports, FFmpeg/MediaMTX presence, multicast, permissions), console/headless mode `--no-gui`.

## Phase 10 — Docs & In-App Help (UX)
- [x] T39. Docs set: README, docs/INSTALL.md, docs/CLI.md, docs/CONFIG.md (every YAML field: type/range/default), docs/ONVIF_MATRIX.md, docs/ODM_TESTING.md, docs/FAQ.md, docs/TROUBLESHOOTING.md.
- [x] T40. GUI help system: "?" per section, searchable Help window mirroring docs, tooltips on EVERY control (purpose, valid range, ONVIF equivalent), placeholder examples, inline validation with hints, reset-to-default per field.
- [x] T41. GUI hotkeys: F1, Ctrl+S/E/I/L/Q, Ctrl+1..9 section switching, PTZ panel via Alt+arrows and +/-, Esc, Ctrl+K command palette; shortcuts shown in menus.
- [x] T42. In-app cheat sheets: GUI "Cheat sheets" tab (ONVIF services ↔ GUI sections ↔ CLI flags, copyable launch commands, event-topics mapping, CLI exit codes, ODM test matrix with local pass/fail checkmarks) + CLI `cheat-sheet [topic]` printing the same content.

## Phase 11 — ODM compatibility hardening (v1.3)
- [x] T43. Strict-client protocol check (zeep `strict=True` over the official WSDL): correct element order for Device, Media, PTZ, Imaging, Events.
- [x] T44. SOAP faults are HTTP 500 with `ter:*` subcodes; unknown operations answer `ActionNotSupported`.
- [x] T45. Complete Profile S/T Media set (encoder/source options, metadata + analytics configurations, audio stubs, capabilities), PTZ options/home/capabilities, Imaging options, Device relay/system calls.
- [x] T46. `GetEventProperties` TopicSet with `MessageDescription`; WS-BaseNotification `Subscribe`/`Notify`.
- [x] T47. Advertised address taken from the request `Host` header; RTSP Basic + Digest, TCP/UDP; testsrc/file sources run in real time.

## Phase 12 — Multi-channel, profiles, fleet
- [x] T48. Three profiles per channel (full/medium/low) with an automatic quality ladder (`streams: 1..3`, `--streams`).
- [x] T49. Per-channel PTZ on/off + own limits, per-channel RTSP port (dedicated MediaMTX), per-channel event sources and analytics simulator.
- [x] T50. Fleet: N devices in one process, ONVIF/RTSP port ranges, per-device overrides, unique serial/MAC/Discovery UUID/state, write-back of GUI edits.
- [x] T51. Console constructor: `--devices --streams --source --rule --device-set --relays --inputs --webhook`, `channels.K.PATH` sets.
- [x] T52. Standard `onvif-emulator.conf` auto-pickup (program dir, then launch dir), lossless save, `--export-config`, GUI export/import.

## Phase 13 — Diagnostic device, overlay, resource protection
- [x] T53. Event rule engine: manual or timer (fixed / random range, seconds/minutes/hours), repeat, start delay, value templates; add/remove at runtime.
- [x] T54. Rule kinds: motion, tamper, analytics (field/line/cell), relay, dry contact, metadata, custom topic/data.
- [x] T55. Relay outputs (Bistable/Monostable) and dry-contact inputs as real ONVIF entities + events; push via PullPoint, WS-Notify, webhooks, metadata stream, live log; REST `/api/diag/*`.
- [x] T56. GUI: Diagnostics and Fleet sections, channel constructor.
- [x] T57. Live video overlay (pure-Python RGBA, FFmpeg second input): PTZ HUD, event badges, metadata boxes, relay/contact LEDs, load-shed marker.
- [x] T58. Resource protection: load estimate, limits, off/warn/clamp/strict, runtime governor (CPU/RAM) shedding low then medium streams, low-priority children, staggered start, event rate limit.

## Phase 14 — PTZ presets on-screen, wider PTZ GUI, tray icon (v1.4)
- [x] T59. Named-command OSD banner: `SetPreset`/`GotoPreset`/`RemovePreset`/`GotoHomePosition`/`SetHomePosition`/`SendAuxiliaryCommand` flash the preset/command name top-center for 3s (`servo.flash`, `overlay.py`).
- [x] T60. Web GUI PTZ panel: iris +/-, focus +/-, home goto/set, and a preset list (save current position with a name, go to, remove); `/api/ptz` gained `iris/focus/home_goto/home_set/preset_set/preset_goto/preset_remove`, `/api/status` returns presets per channel.
- [x] T61. Windows tray icon (`tray.py`, ctypes/Shell_NotifyIcon, no new dependency): "Open GUI" / "Exit" menu, so the console-less windowed exe is visible somewhere besides Task Manager. `--tray`/`--no-tray` flag, on by default in the windowed exe, off by default in the console exe (symmetric flag, different default).
- [x] T62. App + tray icon: hand-encoded multi-res PTZ-dome-camera `.ico` (`gui/static/app.ico`), used for both exe icons and the tray icon.

## Phase 15 — Connect page, ODM "Web page", analytics kinds (v1.5)
- [x] T63. Script-free `/info` page (ONVIF address, auth, channel/profile/token/RTSP table, ODM/VLC steps); `/` serves it to IE/Trident, i.e. ODM's embedded "Web page" browser that cannot run the ES2020 GUI. Embedded RTSP credentials are stripped there.
- [x] T64. Web GUI "Connect" section (default page): device service URL, auth mode, per-channel profile table (token, codec, RTSP, snapshot, HLS) with copy buttons, ODM/VLC how-to and troubleshooting; the Channels page lists every profile URI. `/api/status` returns `connect`; `/#<section>` deep links.
- [x] T65. Analytics kinds as a list in Diagnostics: line, field, cellmotion, motionregion (`MotionRegionDetector/Motion`), counting (`CountAggregation/Counter`, running `Count`, `Step`), loitering (`LoiteringDetector/ObjectIsLoitering`); advertised in the TopicSet and in GetRules/GetAnalyticsModuleOptions; readable rule-kind labels.
- [x] T66. Child processes die with the emulator (`procguard.py`: Windows Job Object KILL_ON_JOB_CLOSE, Linux PR_SET_PDEATHSIG); on start, leftovers of an earlier hard-killed run (own MediaMTX by config path, FFmpeg publishing to our port) are removed, a foreign port owner is reported with name/PID; `/health` `mediamtx_error`, shown on Connect and `/info`.

## Phase 16 — Video sources, side-by-side instances (v1.6)
- [x] T67. Channel video source: upload from the browser (progress, inline status, sanitized never-overwriting names) or a file already on the emulator's machine (path or server-side Browse, no copy); library pick applies at once.
- [x] T68. Any FFmpeg format (MP4/M4V/MOV/MKV/WebM/AVI/WMV/FLV/MPEG/VOB/TS/MTS/3GP/OGV/MXF...); ffprobe decides remux (codec equals the profile codec) vs re-encode to H.264; files without a video stream are refused.
- [x] T69. Two emulators on one PC: MediaMTX side ports (API/HLS/WebRTC, RTP/RTCP/WebRTC-UDP) moved off busy ones via the OS socket table, per-RTSP-port MediaMTX config file, MediaMTX output captured into `mediamtx_error`; `/health` `mediamtx_ports`.

## Phase 17 — OSD stamps and picture, clip end, trouble simulator (v1.7)
- [x] T70. Burnt-in date and time stamps: on/off, strftime format, 9 anchors or custom X/Y, size scaled per profile, font, text colour/opacity, background colour/opacity (0 = transparent), outline; stacked when sharing a corner.
- [x] T71. OSD picture: upload into osd/ or a local path (browse), position, width %, opacity, transparent colour (colorkey), plate.
- [x] T72. Clip without loop: stop the stream / freeze on the last frame / black; `pipeline_state` ended, restart button and API.
- [x] T73. Per-channel trouble simulator: rain, noise, pixelate, shake, lens cover (black/blur/spray) with Tampering events, packet loss (bsf), stutter, freezes, CBR / VBR spikes, bitrate cap, scheduled random outages; presets; `--impair`; REST.

## Phase 18 — Accounts, roles, sign-in, bilingual UI (v1.8)
- [x] T74. Encrypted account store `data/users.json` (migration from config / state.json, plain passwords scrubbed), admin always present and undeletable, reset by deleting its password line or `--reset-admin`.
- [x] T75. Roles Administrator / Operator / Viewer with channel limits enforced in the web API, ONVIF (profiles, stream / snapshot URIs, PTZ; UserLevel User) and RTSP (MediaMTX path permissions).
- [x] T76. Web sign-in page, cookie sessions, fail limiter, own password change, user management page, Live view page via an HLS proxy (no MediaMTX password prompt).
- [x] T77. Redesigned UI (grouped sidebar, user menu, toasts) fully in RU / EN incl. help, cheat sheets, diagnostics; `/info` in both languages; `--add-user`, `--reset-admin`.

## Phase 19 — Planned
- [ ] T78. Go implementation (lightweight single binary, MediaMTX/gortsplib): see `OnvifCamEmuGo/` (local, not in this repo).
- [ ] T79. Media2 (H.265 profiles), RTSP metadata track, HTTPS listener.
- [ ] T80. Live acceptance run in ONVIF Device Manager 2.2.250 (checklist in `docs/ODM_TESTING.md`).
