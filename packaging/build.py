"""Portable one-file builder for Windows (.exe) and Linux (ELF).

Bundles Python + the emulator + FFmpeg + MediaMTX into a single file.

  python packaging/build.py win      # onvif-emulator-vX.Y.Z-win64.exe
  python packaging/build.py linux    # onvif-emulator-vX.Y.Z-linux-x86_64
  python packaging/build.py all      # current OS binary + cross-archive

Linux one-file requires Linux (or CI). On Windows, `linux` still writes
dist/onvif-emulator-vX.Y.Z-linux.tar.gz (portable tree + onefile builder).
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / "vendor"
DIST = ROOT / "dist"
MEDIAMTX_VER = "1.20.1"

GYAN_FFMPEG = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
GYAN_FALLBACK = "https://github.com/GyanD/codexffmpeg/releases/download/8.0/ffmpeg-8.0-essentials_build.zip"
FFMPEG_LINUX = (
    "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/"
    "ffmpeg-master-latest-linux64-gpl.tar.xz"
)
MTX_WIN = f"https://github.com/bluenviron/mediamtx/releases/download/v{MEDIAMTX_VER}/mediamtx_v{MEDIAMTX_VER}_windows_amd64.zip"
MTX_LINUX = f"https://github.com/bluenviron/mediamtx/releases/download/v{MEDIAMTX_VER}/mediamtx_v{MEDIAMTX_VER}_linux_amd64.tar.gz"


def _version() -> str:
    sys.path.insert(0, str(ROOT / "src"))
    from onvif_emu import __version__

    return __version__


def _progress(prefix: str):
    def hook(block: int, bsize: int, total: int) -> None:
        if total <= 0:
            return
        got = block * bsize
        pct = min(100, got * 100 // total)
        mb = total / (1024 * 1024)
        print(f"\r{prefix} {pct:3d}% of {mb:.1f} MB", end="", flush=True)

    return hook


def download(url: str, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.is_file() and dest.stat().st_size > 1024:
        print(f"[skip] {dest.name} already present")
        return dest
    print(f"[get] {url}")
    tmp = dest.with_suffix(dest.suffix + ".part")
    try:
        urllib.request.urlretrieve(url, tmp, reporthook=_progress(dest.name))
        print()
        tmp.replace(dest)
    except Exception:
        if tmp.is_file():
            tmp.unlink()
        raise
    return dest


def _extract_tar(archive: Path, dest: str) -> None:
    with tarfile.open(archive) as tf:
        try:
            tf.extractall(dest, filter="data")
        except TypeError:
            tf.extractall(dest)


def _copy_named(src_dir: Path, names: tuple[str, ...], dest_dir: Path) -> list[Path]:
    dest_dir.mkdir(parents=True, exist_ok=True)
    found: list[Path] = []
    for path in src_dir.rglob("*"):
        if path.name.lower() in {n.lower() for n in names} and path.is_file():
            target = dest_dir / path.name
            shutil.copy2(path, target)
            found.append(target)
    return found


def fetch_windows_bins() -> Path:
    out = VENDOR / "win64"
    out.mkdir(parents=True, exist_ok=True)
    need = {"ffmpeg.exe", "ffprobe.exe", "mediamtx.exe"}
    if need <= {p.name.lower() for p in out.iterdir() if p.is_file()}:
        print(f"[ok] Windows vendor bins in {out}")
        return out
    zpath = VENDOR / "cache" / "ffmpeg-win.zip"
    try:
        download(GYAN_FFMPEG, zpath)
    except Exception as exc:
        print(f"[warn] gyan.dev failed ({exc}), fallback GitHub")
        download(GYAN_FALLBACK, zpath)
    with tempfile.TemporaryDirectory() as td:
        with zipfile.ZipFile(zpath) as zf:
            zf.extractall(td)
        copied = _copy_named(Path(td), ("ffmpeg.exe", "ffprobe.exe"), out)
        if len(copied) < 2:
            raise RuntimeError("ffmpeg.exe / ffprobe.exe not found in archive")
    mz = VENDOR / "cache" / "mediamtx-win.zip"
    download(MTX_WIN, mz)
    with tempfile.TemporaryDirectory() as td:
        with zipfile.ZipFile(mz) as zf:
            zf.extractall(td)
        copied = _copy_named(Path(td), ("mediamtx.exe",), out)
        if not copied:
            raise RuntimeError("mediamtx.exe not found in archive")
    print(f"[ok] Windows bins -> {out}")
    return out


def fetch_linux_bins() -> Path:
    out = VENDOR / "linux64"
    out.mkdir(parents=True, exist_ok=True)
    have = {p.name for p in out.iterdir() if p.is_file()}
    if {"ffmpeg", "ffprobe", "mediamtx"} <= have:
        print(f"[ok] Linux vendor bins in {out}")
        return out
    tar_path = VENDOR / "cache" / "ffmpeg-linux.tar.xz"
    download(FFMPEG_LINUX, tar_path)
    with tempfile.TemporaryDirectory() as td:
        _extract_tar(tar_path, td)
        copied = _copy_named(Path(td), ("ffmpeg", "ffprobe"), out)
        if len(copied) < 1:
            raise RuntimeError("ffmpeg not found in Linux archive")
    mt = VENDOR / "cache" / "mediamtx-linux.tar.gz"
    download(MTX_LINUX, mt)
    with tempfile.TemporaryDirectory() as td:
        _extract_tar(mt, td)
        copied = _copy_named(Path(td), ("mediamtx",), out)
        if not copied:
            raise RuntimeError("mediamtx not found in Linux archive")
    print(f"[ok] Linux bins -> {out}")
    return out


APP_ICON = ROOT / "src" / "onvif_emu" / "gui" / "static" / "app.ico"


def write_spec(target: str, bins: Path, name: str, *, console: bool = True, entry: str = "entry.py") -> Path:
    bin_lines = []
    if target == "win":
        mapping = [("ffmpeg.exe", "."), ("ffprobe.exe", "."), ("mediamtx.exe", ".")]
    else:
        mapping = [("ffmpeg", "."), ("ffprobe", "."), ("mediamtx", ".")]
    for fname, dest in mapping:
        src = bins / fname
        if src.is_file():
            bin_lines.append(f"    (r'{src}', '{dest}'),")
    binaries = "\n".join(bin_lines) or "    "
    entry_path = ROOT / "packaging" / entry
    console_flag = "True" if console else "False"
    icon_line = f"    icon=r'{APP_ICON}'," if target == "win" and APP_ICON.is_file() else ""
    spec = f"""# Generated by packaging/build.py — do not edit by hand
# -*- mode: python ; coding: utf-8 -*-
import sys
sys.path.insert(0, r'{ROOT / "src"}')
from PyInstaller.utils.hooks import collect_all

datas, binaries, hiddenimports = collect_all("onvif_emu")
aio_datas, aio_bins, aio_hidden = collect_all("aiohttp")
datas += aio_datas
binaries += aio_bins
hiddenimports += aio_hidden
hiddenimports = [name for name in hiddenimports if name != "aiohttp.pytest_plugin"]
hiddenimports += ["pydantic", "pydantic_settings", "yaml", "zoneinfo"]

binaries += [
{binaries}
]

a = Analysis(
    [r'{entry_path}'],
    pathex=[r'{ROOT / "src"}'],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={{}},
    runtime_hooks=[],
    excludes=["pytest", "_pytest", "pytest_asyncio", "aiohttp.pytest_plugin"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="{name}",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console={console_flag},
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    uac_admin=False,
{icon_line}
)
"""
    kind = "console" if console else "gui"
    path = ROOT / "packaging" / f"_gen_{target}_{kind}.spec"
    path.write_text(spec, encoding="utf-8")
    return path


def run_pyinstaller(spec: Path) -> int:
    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--distpath",
        str(DIST),
        "--workpath",
        str(ROOT / "build" / "pyinstaller"),
        str(spec),
    ]
    print("[run]", " ".join(cmd))
    return subprocess.call(cmd, cwd=ROOT)


def ensure_pyinstaller() -> None:
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pyinstaller>=6.4.0"])


def _linux_tar_info(info: tarfile.TarInfo) -> tarfile.TarInfo:
    if info.isfile() and (
        "/vendor/linux64/" in info.name or info.name.endswith(".sh")
        or info.name.endswith("/bin/onvif-emulator")
    ):
        info.mode = 0o755
    return info


def linux_source_archive(ver: str) -> Path:
    """Portable Linux tree + one-file builder, produced even on Windows."""
    name = f"onvif-emulator-v{ver}-linux"
    staging = DIST / name
    if staging.exists():
        shutil.rmtree(staging)
    for rel in ("src", "config", "docs", "packaging", "scripts", "bin"):
        src = ROOT / rel
        if src.is_dir():
            shutil.copytree(src, staging / rel, dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "_gen_*.spec"))
    for f in ("pyproject.toml", "README.md", "Makefile", "LICENSE"):
        shutil.copy2(ROOT / f, staging / f)
    try:
        fetch_linux_bins()
        shutil.copytree(VENDOR / "linux64", staging / "vendor" / "linux64", dirs_exist_ok=True)
    except Exception as exc:
        print(f"[warn] Linux vendor bins not fetched: {exc}")
    (staging / "BUILD.txt").write_text(
        "One-file Linux binary:\n"
        "  python3 -m pip install -e '.[packaging]'\n"
        "  python3 packaging/build.py linux\n"
        "Result: dist/onvif-emulator-v"
        f"{ver}-linux-x86_64\n"
        "No-install run: bash scripts/run.sh --headless\n",
        encoding="utf-8",
    )
    DIST.mkdir(parents=True, exist_ok=True)
    archive = DIST / f"{name}.tar.gz"
    with tarfile.open(archive, "w:gz") as tf:
        tf.add(staging, arcname=name, filter=_linux_tar_info)
    print(f"[ok] {archive}")
    return archive


def build_win(ver: str) -> list[Path]:
    if sys.platform != "win32":
        raise SystemExit("Windows one-file exe must be built on Windows (or CI windows-latest).")
    ensure_pyinstaller()
    bins = fetch_windows_bins()
    produced: list[Path] = []
    # Console / web-service build (CLI + logs in terminal).
    name_web = f"onvif-emulator-v{ver}-win64"
    spec_web = write_spec("win", bins, name_web, console=True, entry="entry.py")
    code = run_pyinstaller(spec_web)
    if code != 0:
        raise SystemExit(code)
    exe_web = DIST / f"{name_web}.exe"
    if not exe_web.is_file():
        raise SystemExit(f"Expected {exe_web} was not produced")
    print(f"[ok] {exe_web}  ({exe_web.stat().st_size / (1024 * 1024):.1f} MB)  [web/console]")
    produced.append(exe_web)

    # Windowed GUI build (no console; opens browser by default).
    name_gui = f"onvif-emulator-v{ver}-win64-gui"
    spec_gui = write_spec("win", bins, name_gui, console=False, entry="entry_gui.py")
    code = run_pyinstaller(spec_gui)
    if code != 0:
        raise SystemExit(code)
    exe_gui = DIST / f"{name_gui}.exe"
    if not exe_gui.is_file():
        raise SystemExit(f"Expected {exe_gui} was not produced")
    print(f"[ok] {exe_gui}  ({exe_gui.stat().st_size / (1024 * 1024):.1f} MB)  [gui/windowed]")
    produced.append(exe_gui)
    return produced


def build_linux_onefile(ver: str) -> Path:
    if sys.platform == "win32":
        print("[info] Cannot link a Linux ELF on Windows; writing portable Linux archive + builder.")
        return linux_source_archive(ver)
    ensure_pyinstaller()
    bins = fetch_linux_bins()
    name = f"onvif-emulator-v{ver}-linux-x86_64"
    spec = write_spec("linux", bins, name)
    code = run_pyinstaller(spec)
    if code != 0:
        raise SystemExit(code)
    binary = DIST / name
    if not binary.is_file():
        raise SystemExit(f"Expected {binary} was not produced")
    binary.chmod(binary.stat().st_mode | 0o111)
    print(f"[ok] {binary}  ({binary.stat().st_size / (1024 * 1024):.1f} MB)")
    linux_source_archive(ver)
    return binary


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        reconf = getattr(stream, "reconfigure", None)
        if reconf:
            try:
                reconf(encoding="utf-8", errors="replace")
            except Exception:
                pass
    p = argparse.ArgumentParser(description="Build portable one-file ONVIF emulator")
    p.add_argument("target", choices=["win", "linux", "all"], help="win = .exe, linux = ELF or tar.gz")
    ns = p.parse_args(argv)
    ver = _version()
    DIST.mkdir(parents=True, exist_ok=True)
    if ns.target in {"win", "all"}:
        if sys.platform == "win32":
            build_win(ver)
        else:
            print("[info] Skipping win exe (not Windows). CI builds it on windows-latest.")
    if ns.target in {"linux", "all"}:
        build_linux_onefile(ver)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
