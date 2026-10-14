"""Project garbage cleaner (TZ §6.3).

Deletes caches, build artifacts, logs, downloaded FFmpeg/MediaMTX.
Does not touch .git. .venv is removed only with --venv / --all.

  python scripts/clean.py --dry-run
  python scripts/clean.py
  python scripts/clean.py --all
"""

from __future__ import annotations

import argparse
import os
import shutil
from pathlib import Path

DIR_NAMES = {
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".tox",
    "htmlcov",
    "build",
    "dist",
    ".eggs",
    "logs",
    "tmp",
}
FILE_SUFFIXES = {".pyc", ".pyo"}
WALK_PRUNE = {".git", "node_modules", ".venv", "venv", "env"}
VENDOR_PRUNE = {"win64", "linux64", "cache", "ffmpeg", "mediamtx"}


def iter_targets(root: Path, *, venv: bool = False, vendor: bool = True) -> list[Path]:
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root, topdown=True):
        dp = Path(dirpath)
        if dp.name == "vendor" and vendor:
            dirnames[:] = [d for d in dirnames if d not in VENDOR_PRUNE]
        keep: list[str] = []
        for d in dirnames:
            if d in WALK_PRUNE:
                continue
            p = dp / d
            if d in DIR_NAMES or d.endswith(".egg-info"):
                found.append(p)
                continue
            keep.append(d)
        dirnames[:] = keep
        for f in filenames:
            suffix = Path(f).suffix
            if suffix in FILE_SUFFIXES or (f.endswith(".spec") and f.startswith("_gen_")):
                found.append(dp / f)

    extras = [
        root / ".coverage",
        root / "htmlcov",
        root / "build",
        root / "dist",
        root / "logs",
        root / "tmp",
        root / "data" / "state.json",
        root / "data" / "mediamtx.yml",
        root / "config" / "local.yaml",
        root / "config" / "runtime.yaml",
        root / "packaging" / "_gen_win.spec",
        root / "packaging" / "_gen_linux.spec",
    ]
    extras.extend(root.glob("*.egg-info"))
    extras.extend(root.glob("src/*.egg-info"))
    if vendor:
        extras.extend(
            [
                root / "vendor" / "cache",
                root / "vendor" / "win64",
                root / "vendor" / "linux64",
                root / "vendor" / "ffmpeg",
                root / "vendor" / "mediamtx",
            ]
        )
        extras.extend(root.glob("vendor/*.zip"))
        extras.extend(root.glob("vendor/*.tar.gz"))
        extras.extend(root.glob("vendor/*.tgz"))
        extras.extend(root.glob("vendor/*.xz"))
    if venv:
        extras.extend([root / ".venv", root / "venv", root / "env"])

    for e in extras:
        if e.exists():
            found.append(e)

    uniq: list[Path] = []
    seen: set[Path] = set()
    for p in sorted(found, key=lambda x: len(x.parts), reverse=True):
        try:
            rp = p.resolve()
        except OSError:
            rp = p
        if rp in seen:
            continue
        seen.add(rp)
        uniq.append(p)
    return uniq


def delete(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)
    elif path.is_file() or path.is_symlink():
        path.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Remove build/test/vendor junk from the tree")
    parser.add_argument("--dry-run", action="store_true", help="Print paths, do not delete")
    parser.add_argument("--venv", action="store_true", help="Also delete .venv / venv")
    parser.add_argument("--all", action="store_true", help="Same as --venv (full wipe of generated files)")
    parser.add_argument("--keep-vendor", action="store_true", help="Keep downloaded FFmpeg/MediaMTX")
    parser.add_argument("--root", type=Path, default=None)
    ns = parser.parse_args(argv)
    root = (ns.root or Path(__file__).resolve().parents[1]).resolve()
    targets = iter_targets(root, venv=ns.venv or ns.all, vendor=not ns.keep_vendor)
    prefix = "[dry-run] " if ns.dry_run else "[del] "
    for t in targets:
        try:
            rel = t.relative_to(root)
        except ValueError:
            rel = t
        print(prefix + str(rel))
        if not ns.dry_run:
            delete(t)
    if not targets:
        print("Nothing to clean.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
