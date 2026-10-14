"""Paths for source tree vs PyInstaller frozen (one-file) runtime."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"))


def meipass() -> Path:
    if is_frozen():
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent


def user_data_dir() -> Path:
    """Writable per-user dir when the launch folder is System32 / venv / read-only."""
    if os.name == "nt":
        base = os.environ.get("APPDATA") or os.environ.get("LOCALAPPDATA")
        if base:
            return Path(base) / "onvif-emulator"
        return Path.home() / "AppData" / "Roaming" / "onvif-emulator"
    return Path.home() / ".config" / "onvif-emulator"


def dir_is_writable(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".onvif-emu-write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def _is_windows_system_dir(path: Path) -> bool:
    if os.name != "nt":
        return False
    try:
        resolved = path.resolve()
    except OSError:
        return False
    windir = Path(os.environ.get("WINDIR", r"C:\Windows")).resolve()
    try:
        resolved.relative_to(windir)
        return True
    except ValueError:
        return False


def _is_venv_or_install_dir(path: Path) -> bool:
    try:
        parts = [p.lower() for p in path.resolve().parts]
    except OSError:
        return False
    if "site-packages" in parts or "dist-packages" in parts:
        return True
    name = path.name.lower()
    if name in {"scripts", "bin"}:
        cfg = path.parent / "pyvenv.cfg"
        if cfg.is_file():
            return True
        if (path.parent.parent / "pyvenv.cfg").is_file():
            return True
    return False


def _argv0_dir() -> Path | None:
    if not sys.argv or not sys.argv[0]:
        return None
    try:
        p = Path(sys.argv[0]).expanduser()
        if not p.is_absolute():
            p = (Path.cwd() / p).resolve()
        else:
            p = p.resolve()
        if p.is_file():
            return p.parent
        if p.is_dir():
            return p
    except OSError:
        return None
    return None


def executable_dir() -> Path:
    """Writable directory for log, .conf, video/, MediaMTX yaml.

    Portable exe: folder of the .exe (not the temp unpack dir).
    `python -m` from a project: current working directory.
    Double-click of a venv launcher: AppData — cwd is often System32 or .venv\\Scripts,
    which is not writable or not where the user looks for logs.
    """
    if getattr(sys, "frozen", False):
        cand = Path(sys.executable).resolve().parent
        if dir_is_writable(cand):
            return cand
        fallback = user_data_dir()
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback

    ordered: list[Path] = []
    try:
        ordered.append(Path.cwd())
    except OSError:
        pass
    argv_dir = _argv0_dir()
    if argv_dir is not None:
        ordered.append(argv_dir)

    for cand in ordered:
        if _is_windows_system_dir(cand) or _is_venv_or_install_dir(cand):
            continue
        if dir_is_writable(cand):
            return cand

    fallback = user_data_dir()
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


def package_dir() -> Path:
    """onvif_emu package dir (GUI, locales, default.yaml)."""
    bundled = meipass() / "onvif_emu"
    if bundled.is_dir():
        return bundled
    if is_frozen():
        return meipass()
    return Path(__file__).resolve().parent


def vendor_dir() -> Path:
    """FFmpeg / MediaMTX: unpacked next to the exe payload, then repo vendor/."""
    if is_frozen():
        for cand in (meipass() / "vendor", meipass()):
            if cand.is_dir():
                return cand
        return meipass()
    root = Path(__file__).resolve().parents[2]
    return root / "vendor"


def iter_bin_candidates(name: str) -> list[Path]:
    win = f"{name}.exe" if not name.lower().endswith(".exe") else name
    names = [win, name] if sys.platform == "win32" else [name]
    roots = [
        vendor_dir(),
        vendor_dir() / ("win64" if sys.platform == "win32" else "linux64"),
        executable_dir(),
        executable_dir() / "vendor",
        meipass(),
        meipass() / "vendor",
    ]
    out: list[Path] = []
    seen: set[Path] = set()
    for root in roots:
        for n in names:
            p = (root / n).resolve() if root.exists() else root / n
            if p not in seen:
                seen.add(p)
                out.append(p)
    return out
