from __future__ import annotations

import asyncio
import logging
import shutil
import time
from collections.abc import Iterable, Sequence
from pathlib import Path

import yaml

from onvif_emu.pipeline.backoff import FullJitterBackoff
from onvif_emu.procguard import bind_child
from onvif_emu.util import subprocess_hidden_kwargs

log = logging.getLogger(__name__)

HEALTHY_S = 15.0

MEDIAMTX_YML = {
    "logLevel": "info",
    "api": True,
    "apiAddress": "127.0.0.1:9997",
    "rtspAddress": ":8554",
    "hls": True,
    "hlsAddress": ":8888",
    "webrtc": True,
    "webrtcAddress": ":8889",
    "paths": {
        "all_others": {
            "source": "publisher",
        }
    },
}


def find_mediamtx(explicit: str = "") -> str | None:
    from onvif_emu.paths import iter_bin_candidates, vendor_dir

    if explicit and Path(explicit).is_file():
        return explicit
    names = ["mediamtx", "mediamtx.exe"]
    for n in names:
        w = shutil.which(n)
        if w:
            return w
    for n in names:
        for p in iter_bin_candidates(n.replace(".exe", "")):
            if p.is_file() and p.name.lower().startswith("mediamtx"):
                return str(p)
    vendor = vendor_dir()
    for n in names:
        for folder in (vendor, vendor / "win64", vendor / "linux64"):
            p = folder / n
            if p.is_file():
                return str(p)
    return None


def _read_permissions(user: object, channel_paths: dict[int, list[str]] | None) -> list[dict]:
    """read + playback on every path, or only on the allowed channels' paths (all three profiles)."""
    allowed = list(getattr(user, "channels", None) or [])
    role = str(getattr(getattr(user, "role", ""), "value", getattr(user, "role", "")))
    if not allowed or role == "Administrator" or channel_paths is None:
        return [{"action": "read"}, {"action": "playback"}]
    perms: list[dict] = []
    for cid in allowed:
        for path in channel_paths.get(int(cid), []):
            perms += [{"action": "read", "path": path}, {"action": "playback", "path": path}]
    return perms or [{"action": "read", "path": "~^$"}]  # nothing allowed: a path that never exists


def _auth_users_block(
    users: Sequence[object] | None,
    *,
    auth_required: bool,
    channel_paths: dict[int, list[str]] | None = None,
    internal: tuple[str, str] | None = None,
) -> list[dict]:
    """Build MediaMTX authInternalUsers from the ONVIF/Device user store.

    MediaMTX 1.x uses authInternalUsers (not legacy path readUser/readPass).
    - Localhost may publish (FFmpeg) and use API without credentials.
    - When auth_required, remote read/playback needs ONVIF users.
    - When auth is off (AuthMode.none), anonymous read/publish stays open.
    """
    local_admin = {
        "user": "any",
        "pass": "",
        "ips": ["127.0.0.1", "::1"],
        "permissions": [
            {"action": "api"},
            {"action": "metrics"},
            {"action": "pprof"},
        ],
    }
    if not auth_required:
        return [
            {
                "user": "any",
                "pass": "",
                "ips": [],
                "permissions": [
                    {"action": "publish"},
                    {"action": "read"},
                    {"action": "playback"},
                ],
            },
            local_admin,
        ]

    # FFmpeg publishes to 127.0.0.1 without credentials.
    local_publish = {
        "user": "any",
        "pass": "",
        "ips": ["127.0.0.1", "::1"],
        "permissions": [
            {"action": "publish"},
            {"action": "api"},
            {"action": "metrics"},
            {"action": "pprof"},
        ],
    }
    out: list[dict] = [local_publish]
    if internal:  # the emulator itself, for the web preview proxy: loopback only
        out.append({"user": internal[0], "pass": internal[1], "ips": ["127.0.0.1", "::1"],
                    "permissions": [{"action": "read"}, {"action": "playback"}]})
    seen: set[str] = set()
    for u in users or ():
        name = str(getattr(u, "username", "") or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        pwd = str(getattr(u, "password", "") or "")
        out.append(
            {
                "user": name,
                "pass": pwd,
                "ips": [],
                "permissions": _read_permissions(u, channel_paths),
            }
        )
    if len(out) <= 2:
        log.warning(
            "MediaMTX auth enabled but user store empty — RTSP read will fail until users exist"
        )
    return out


def write_mtx_config(
    path: Path,
    rtsp_port: int,
    api_port: int,
    hls_port: int,
    webrtc_port: int,
    *,
    users: Iterable[object] | None = None,
    auth_required: bool = False,
    hls: bool = True,
    webrtc: bool = True,
    slot: int = 0,
    channel_paths: dict[int, list[str]] | None = None,
    internal: tuple[str, str] | None = None,
) -> Path:
    """Write mediamtx.yml; optionally wire Device/ONVIF users into authInternalUsers.

    Extra per-channel RTSP ports run their own MediaMTX with HLS/WebRTC switched off.
    """
    data = dict(MEDIAMTX_YML)
    data["rtspAddress"] = f":{rtsp_port}"
    data["apiAddress"] = f"127.0.0.1:{api_port}"
    data["hls"] = hls
    data["webrtc"] = webrtc
    # Several MediaMTX processes live side by side (fleet devices, per-channel ports): keep the shared
    # sockets apart. Slot 0 is the plain default instance; slot N moves RTP/RTCP/WebRTC-UDP out of the way.
    data["rtmp"] = False
    data["srt"] = False
    data["moq"] = False
    data["rtspTransports"] = ["udp", "tcp"]
    data["rtspAuthMethods"] = ["basic", "digest"]  # real cameras speak Digest; MediaMTX defaults to Basic only
    if slot:
        data["rtpAddress"] = f":{20000 + slot * 2}"
        data["rtcpAddress"] = f":{20001 + slot * 2}"
        data["webrtcLocalUDPAddress"] = f":{21000 + slot}"
    data["hlsAddress"] = f":{hls_port}"
    data["webrtcAddress"] = f":{webrtc_port}"
    data["authMethod"] = "internal"
    user_list = list(users) if users is not None else []
    data["authInternalUsers"] = _auth_users_block(user_list, auth_required=auth_required,
                                                  channel_paths=channel_paths, internal=internal)
    named = sum(1 for u in data["authInternalUsers"] if u.get("user") not in ("", "any"))
    log.info(
        "MediaMTX config auth_required=%s named_readers=%s path=%s",
        auth_required,
        named,
        path,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return path


class MediaMtx:
    def __init__(self, binary: str | None, cfg_path: Path, enabled: bool = True):
        self.binary = binary
        self.cfg_path = cfg_path
        self.enabled = enabled
        self.proc: asyncio.subprocess.Process | None = None
        self._watch_task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self.backoff = FullJitterBackoff(base=1.0, cap=30.0)
        self._last_err = ""

    @property
    def alive(self) -> bool:
        return self.proc is not None and self.proc.returncode is None

    async def start(self) -> bool:
        if not self.enabled:
            return False
        if not self.binary:
            log.warning(
                "MediaMTX not found — RTSP publish is off. ONVIF and the web GUI still run. "
                "Put mediamtx.exe in PATH or vendor/win64, or set mediamtx.binary."
            )
            return False
        self._stop.clear()
        ok = await self._spawn_once()
        if ok:
            self._watch_task = asyncio.create_task(self._supervise(), name="mediamtx-watch")
        return ok

    async def _spawn_once(self) -> bool:
        try:
            self.proc = await asyncio.create_subprocess_exec(
                self.binary,
                str(self.cfg_path),
                **self._spawn_kwargs(),
            )
            bind_child(self.proc.pid)  # killed by the OS if the emulator dies
        except OSError as exc:
            log.warning(
                "Cannot start MediaMTX (%s): %s — ONVIF/GUI still run, RTSP off",
                self.binary,
                exc,
            )
            self.proc = None
            return False
        await asyncio.sleep(0.4)
        if self.proc.returncode is not None:
            err = await self._read_stderr_rest(timeout=1.0)
            log.warning(
                "MediaMTX exited immediately code=%s. RTSP off, GUI still available.\n%s",
                self.proc.returncode,
                err or "(no stderr)",
            )
            return False
        log.info("MediaMTX started pid=%s bin=%s cfg=%s", self.proc.pid, self.binary, self.cfg_path)
        return True

    @staticmethod
    def _spawn_kwargs() -> dict:
        # MediaMTX logs (and reports "address already in use") on stdout: merge both into one pipe
        kw = subprocess_hidden_kwargs()
        kw["stdout"] = asyncio.subprocess.PIPE
        kw["stderr"] = asyncio.subprocess.STDOUT
        return kw

    def _pipe(self):
        return self.proc.stdout if self.proc else None

    def last_output(self, limit: int = 600) -> str:
        """Tail of what MediaMTX printed (errors first), for messages."""
        lines = [ln for ln in self._last_err.splitlines() if ln.strip()]
        errs = [ln for ln in lines if " ERR " in ln or "error" in ln.lower()]
        return "\n".join(errs[-3:] or lines[-3:])[-limit:]

    async def _read_stderr_rest(self, limit: int = 4000, timeout: float | None = 1.0) -> str:
        pipe = self._pipe()
        if pipe is None:
            return self._last_err[-limit:]
        try:
            if timeout is None:
                raw = await pipe.read()
            else:
                raw = await asyncio.wait_for(pipe.read(), timeout=timeout)
        except (TimeoutError, OSError):
            return self._last_err[-limit:]
        text = raw.decode("utf-8", errors="replace").strip()
        if text:
            self._last_err = (self._last_err + "\n" + text)[-8000:]
        return self._last_err[-limit:]

    async def _drain_stderr(self) -> None:
        pipe = self._pipe()
        if pipe is None:
            return
        try:
            while True:
                chunk = await pipe.read(4096)
                if not chunk:
                    break
                self._last_err = (self._last_err + chunk.decode("utf-8", errors="replace"))[-8000:]
        except (OSError, asyncio.CancelledError):
            return

    async def _supervise(self) -> None:
        while not self._stop.is_set():
            if self.proc is not None and self.proc.returncode is None:
                started = time.monotonic()
                drain = asyncio.create_task(self._drain_stderr(), name="mediamtx-stderr")
                try:
                    code = await self.proc.wait()
                finally:
                    drain.cancel()
                    try:
                        await drain
                    except (asyncio.CancelledError, Exception):
                        pass
                if self._stop.is_set():
                    break
                lived = time.monotonic() - started
                if lived >= HEALTHY_S:
                    self.backoff.reset()
                log.warning(
                    "MediaMTX stopped code=%s lived=%.1fs — restarting\n%s",
                    code,
                    lived,
                    self._last_err[-2000:] or "(no stderr)",
                )
            if self._stop.is_set():
                break
            delay = self.backoff.next_delay()
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=delay)
                break
            except TimeoutError:
                pass
            await self._spawn_once()

    async def stop(self) -> None:
        self._stop.set()
        if self._watch_task:
            self._watch_task.cancel()
            try:
                await self._watch_task
            except (asyncio.CancelledError, Exception):
                pass
            self._watch_task = None
        if not self.proc or self.proc.returncode is not None:
            return
        # The supervisor's reader is gone. Keep draining until termination,
        # otherwise a full stdout pipe can prevent Process.wait() completing.
        drain = asyncio.create_task(self._drain_stderr())
        try:
            self.proc.terminate()
            try:
                await asyncio.wait_for(self.proc.wait(), timeout=5)
            except TimeoutError:
                self.proc.kill()
                await self.proc.wait()
        except ProcessLookupError:
            pass
        finally:
            drain.cancel()
            await asyncio.gather(drain, return_exceptions=True)
