"""Child-process lifetime and RTSP port ownership.

FFmpeg and MediaMTX are separate processes. If the emulator is killed hard (Task Manager, Stop-Process,
a crash) they used to keep running: the orphaned MediaMTX kept the RTSP port, the next start could not
bind it, and clients were silently served by the leftover instance.

* ``bind_child(pid)`` puts a child into a Windows Job Object with KILL_ON_JOB_CLOSE: when the emulator
  process ends for any reason, Windows closes the job handle and kills every child in it. Only children
  are put in the job, not the emulator itself, so a browser it opens is not affected.
* On Linux ``posix_child_setup`` asks the kernel for SIGKILL when the parent dies (PR_SET_PDEATHSIG).
* ``free_rtsp_port`` finds who holds a port and removes a MediaMTX left over from an earlier run of this
  same installation (same config file, parent process gone); anything else is only reported.
"""

from __future__ import annotations

import ctypes
import json
import logging
import os
import socket
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

_JOB = {"handle": None, "failed": False}


def _win_job():
    """Create (once) the job object every child is assigned to."""
    if _JOB["handle"] is not None or _JOB["failed"]:
        return _JOB["handle"]
    from ctypes import wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CloseHandle.argtypes = (wintypes.HANDLE,)
    k32.CloseHandle.restype = wintypes.BOOL
    k32.CreateJobObjectW.restype = wintypes.HANDLE
    k32.CreateJobObjectW.argtypes = (wintypes.LPVOID, wintypes.LPCWSTR)
    k32.SetInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD)

    class BASIC(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD),
        ]

    class IO(ctypes.Structure):
        _fields_ = [(n, ctypes.c_uint64) for n in (
            "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
            "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

    class EXTENDED(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", BASIC), ("IoInfo", IO), ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    job = k32.CreateJobObjectW(None, None)
    if not job:
        _JOB["failed"] = True
        log.warning("CreateJobObject failed (%s): children may outlive a hard kill", ctypes.get_last_error())
        return None
    info = EXTENDED()
    info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not k32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):  # ExtendedLimitInformation
        _JOB["failed"] = True
        log.warning("SetInformationJobObject failed (%s)", ctypes.get_last_error())
        k32.CloseHandle(job)
        return None
    _JOB["handle"] = job  # never closed: it has to live exactly as long as this process
    return job


def bind_child(pid: int | None) -> bool:
    """Tie a freshly started child to this process' lifetime (Windows; no-op elsewhere)."""
    if sys.platform != "win32" or not pid:
        return False
    job = _win_job()
    if job is None:
        return False
    from ctypes import wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CloseHandle.argtypes = (wintypes.HANDLE,)
    k32.CloseHandle.restype = wintypes.BOOL
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    k32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
    h = k32.OpenProcess(0x0001 | 0x0100, False, pid)  # PROCESS_TERMINATE | PROCESS_SET_QUOTA
    if not h:
        return False
    try:
        ok = bool(k32.AssignProcessToJobObject(job, h))
        if not ok:
            log.debug("AssignProcessToJobObject pid=%s failed (%s)", pid, ctypes.get_last_error())
        return ok
    finally:
        k32.CloseHandle(h)


def posix_child_setup(nice: int = 0):
    """preexec_fn for Linux children: die with the parent, optionally lower priority."""
    def setup() -> None:
        try:
            libc = ctypes.CDLL(None, use_errno=True)
            libc.prctl(1, 9)  # PR_SET_PDEATHSIG, SIGKILL
        except (OSError, AttributeError):
            pass
        if nice:
            os.nice(nice)

    return setup


# ------------------------------------------------------------------ RTSP port ownership
@dataclass
class PortOwner:
    pid: int
    name: str = ""
    cmdline: str = ""
    parent_alive: bool = True

    def describe(self) -> str:
        return f"{self.name or 'process'} (PID {self.pid})"


def port_in_use(port: int) -> bool:
    """Someone accepts connections on the port (a bind probe is not enough on Windows: Go listeners allow it)."""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        pass
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("0.0.0.0", port))
        return False
    except OSError:
        return True
    finally:
        s.close()


def os_bound_ports() -> tuple[set[int], set[int]] | None:
    """(listening TCP ports, bound UDP ports) from the OS socket table (Windows netstat).

    On Windows a bind probe is not trustworthy - it succeeded even with SO_EXCLUSIVEADDRUSE against
    MediaMTX's UDP sockets - so the table is the source of truth there. None elsewhere / on failure.
    """
    if sys.platform != "win32":
        return None
    text = _run(["netstat", "-ano"])
    if not text:
        return None
    tcp: set[int] = set()
    udp: set[int] = set()
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 4 or parts[0] not in ("TCP", "UDP"):
            continue
        local = parts[1]
        port = local.rsplit(":", 1)[-1]
        if not port.isdigit():
            continue
        if parts[0] == "UDP":
            udp.add(int(port))
        elif parts[3] == "LISTENING":
            tcp.add(int(port))
    return tcp, udp


def udp_free(port: int, table: tuple[set[int], set[int]] | None = None) -> bool:
    if table is not None:
        return port not in table[1]
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.bind(("0.0.0.0", port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def tcp_free(port: int, table: tuple[set[int], set[int]] | None = None) -> bool:
    if table is not None and port in table[0]:
        return False
    return not port_in_use(port)


def next_free_tcp(port: int, taken: set[int], span: int = 200, table=None) -> int:
    for p in range(port, min(65535, port + span) + 1):
        if p not in taken and tcp_free(p, table):
            return p
    return port


def mtx_udp_ports(slot: int) -> tuple[int, int, int]:
    """RTP, RTCP and WebRTC-UDP of a MediaMTX slot (slot 0 = MediaMTX defaults)."""
    if not slot:
        return 8000, 8001, 8189
    return 20000 + slot * 2, 20001 + slot * 2, 21000 + slot


def resolve_mtx_ports(api: int, hls: int, webrtc: int, slot: int, rtsp: int) -> tuple[int, int, int, int, list[str]]:
    """Side ports MediaMTX needs besides RTSP; busy ones (a second emulator on this PC) move to free ones.

    Returns (api, hls, webrtc, slot, notes); a note describes every move for the log and /health.
    """
    notes: list[str] = []
    table = os_bound_ports()
    taken = {rtsp}
    out = []
    for name, port in (("API", api), ("HLS", hls), ("WebRTC", webrtc)):
        new = next_free_tcp(port, taken, table=table)
        taken.add(new)
        if new != port:
            notes.append(f"{name} port {port} busy -> {new}")
        out.append(new)
    if not all(udp_free(p, table) for p in mtx_udp_ports(slot)):
        for s in range(400, 1400):  # clear of fleet slots ((instance-1)*10 + k)
            if all(udp_free(p, table) for p in mtx_udp_ports(s)):
                old, new = mtx_udp_ports(slot), mtx_udp_ports(s)
                notes.append(f"RTP/RTCP UDP {old[0]}/{old[1]} busy -> {new[0]}/{new[1]}")
                slot = s
                break
    return out[0], out[1], out[2], slot, notes


def _run(cmd: list[str], timeout: float = 8.0) -> str:
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
    try:
        out = subprocess.run(cmd, capture_output=True, timeout=timeout, creationflags=flags)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return out.stdout.decode("utf-8", "replace")


def port_owner(port: int) -> PortOwner | None:
    """Who listens on a TCP port (Windows only; None when unknown)."""
    if sys.platform != "win32":
        return None
    script = (
        f"$c = Get-NetTCPConnection -LocalPort {port} -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1;"
        "if ($c) { $p = Get-CimInstance Win32_Process -Filter \"ProcessId=$($c.OwningProcess)\";"
        "$pp = if ($p) { Get-Process -Id $p.ParentProcessId -ErrorAction SilentlyContinue } else { $null };"
        "$alive = [bool]($pp -and $pp.StartTime -le $p.CreationDate);"
        "@{pid=$c.OwningProcess; name=$p.Name; cmd=$p.CommandLine; parent_alive=$alive} | ConvertTo-Json -Compress }"
    )
    raw = _run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script]).strip()
    if not raw:
        return None
    try:
        d = json.loads(raw)
    except ValueError:
        return None
    return PortOwner(int(d.get("pid") or 0), str(d.get("name") or ""), str(d.get("cmd") or ""), bool(d.get("parent_alive")))


def _kill(pid: int) -> None:
    _run(["taskkill", "/PID", str(pid), "/T", "/F"])


def kill_orphan_publishers(port: int, publish_host: str = "127.0.0.1") -> int:
    """Kill FFmpeg left over from a hard-killed emulator that still publishes to our RTSP port (Windows).

    Only processes whose parent is gone and whose command line targets ``rtsp://<publish_host>:<port>/``
    are touched — that is exactly what our own pipelines look like, so nothing foreign is hit.
    """
    if sys.platform != "win32":
        return 0
    needle = f"rtsp://{publish_host}:{port}/"
    script = (
        "Get-CimInstance Win32_Process -Filter \"Name='ffmpeg.exe'\" | ForEach-Object {"
        "$pp = Get-Process -Id $_.ParentProcessId -ErrorAction SilentlyContinue;"
        "if (-not ($pp -and $pp.StartTime -le $_.CreationDate)) { \"$($_.ProcessId)`t$($_.CommandLine)\" } }"
    )
    killed = 0
    for line in _run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script]).splitlines():
        pid, _, cmd = line.partition("\t")
        if pid.strip().isdigit() and needle in cmd:
            _kill(int(pid))
            killed += 1
    if killed:
        log.critical("Stopped %s FFmpeg process(es) left over from an earlier run (publishing to %s)", killed, needle)
    return killed


def free_rtsp_port(port: int, cfg_path: Path, publish_host: str = "127.0.0.1") -> str:
    """Make sure MediaMTX can take ``port``. Returns "" when free, else a human-readable reason."""
    kill_orphan_publishers(port, publish_host)
    if not port_in_use(port):
        return ""
    owner = port_owner(port)
    # "ours" = its config lives in this install's data folder (mediamtx_<port>.yml, or mediamtx.yml before 1.5.1)
    ours = owner is not None and str(cfg_path.parent).lower() + os.sep in owner.cmdline.lower()
    if owner and owner.name.lower().startswith("mediamtx") and not owner.parent_alive and ours:
        log.critical("RTSP port %s held by %s left over from an earlier run (its emulator is gone) — stopping it", port, owner.describe())
        _kill(owner.pid)
        for _ in range(20):
            if not port_in_use(port):
                return ""
            import time

            time.sleep(0.1)
    who = owner.describe() if owner else "another program"
    return f"RTSP port {port} is taken by {who}; RTSP is off. Close it or start with another --rtsp-port."
