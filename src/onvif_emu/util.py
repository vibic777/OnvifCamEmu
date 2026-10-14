from __future__ import annotations

import asyncio
import hashlib
import hmac
import os
import socket
import subprocess
import sys
import tempfile
import uuid
from collections.abc import Iterable
from pathlib import Path
from typing import Any


def constant_time_equals(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def sha1(data: bytes) -> bytes:
    return hashlib.sha1(data, usedforsecurity=False).digest()


def md5_hex(data: str | bytes) -> str:
    raw = data if isinstance(data, bytes) else data.encode("utf-8")
    return hashlib.md5(raw, usedforsecurity=False).hexdigest()


def new_nonce(nbytes: int = 16) -> str:
    return os.urandom(nbytes).hex()


def new_uuid() -> str:
    return str(uuid.uuid4())


def atomic_write_text(path: Path | str, text: str, encoding: str = "utf-8") -> None:
    """Write file atomically: temp file in same dir + os.replace.

    Same-directory replace is atomic on NTFS and POSIX for the metadata update.
    """
    dest = Path(path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=dest.name, suffix=".tmp", dir=str(dest.parent))
    try:
        with os.fdopen(fd, "w", encoding=encoding) as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, dest)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def detect_public_host(bind_host: str = "0.0.0.0") -> str:
    if bind_host not in {"0.0.0.0", "::", ""}:
        return bind_host
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def port_in_use(host: str, port: int, udp: bool = False) -> bool:
    family = socket.AF_INET
    kind = socket.SOCK_DGRAM if udp else socket.SOCK_STREAM
    probe_host = "127.0.0.1" if host in {"0.0.0.0", ""} else host
    with socket.socket(family, kind) as s:
        s.settimeout(0.3)
        if udp:
            try:
                s.bind((probe_host, port))
                return False
            except OSError:
                return True
        try:
            return s.connect_ex((probe_host, port)) == 0
        except OSError:
            return False


def which(names: Iterable[str]) -> str | None:
    from shutil import which as _which

    for name in names:
        found = _which(name)
        if found:
            return found
    return None


def version_string() -> str:
    from onvif_emu import __version__

    return f"onvif-emulator {__version__}"


LOW_PRIORITY = {"on": True}


def set_low_priority(on: bool) -> None:
    """FFmpeg/MediaMTX start below normal priority so the PC stays responsive under load."""
    LOW_PRIORITY["on"] = bool(on)


def subprocess_hidden_kwargs(*, stderr: int | None = None) -> dict[str, Any]:
    """Start FFmpeg/MediaMTX without a flashing Windows console; never inherit stdin.

    Default stderr=PIPE (caller must drain). Pass DEVNULL for long-lived FFmpeg
    to avoid pipe-buffer deadlocks.
    """
    kw: dict[str, Any] = {
        "stdin": asyncio.subprocess.DEVNULL,
        "stdout": asyncio.subprocess.DEVNULL,
        "stderr": asyncio.subprocess.PIPE if stderr is None else stderr,
    }
    if sys.platform == "win32":
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        if LOW_PRIORITY["on"]:
            flags |= getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0x00004000)
        kw["creationflags"] = flags
    else:
        from onvif_emu.procguard import posix_child_setup

        kw["preexec_fn"] = posix_child_setup(5 if LOW_PRIORITY["on"] else 0)  # also: die with the emulator
    return kw
