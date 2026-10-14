"""Thin wrapper — prefer `python packaging/build.py win`."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

if __name__ == "__main__":
    script = Path(__file__).with_name("build.py")
    raise SystemExit(subprocess.call([sys.executable, str(script), "win"]))
