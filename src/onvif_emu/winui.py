"""Helpers for the windowed (console-less) Windows build.

A PyInstaller ``console=False`` exe has no stdout/stderr, so ``--help``, argument
errors and "port busy" would vanish. Two mechanisms make the CLI usable anyway:

* launched from cmd/PowerShell -> attach to the parent console and print there;
* launched by double-click / no console -> show the text in a message box (or open
  it in Notepad when it is long).
"""

from __future__ import annotations

import io
import os
import re
import sys
import tempfile
from pathlib import Path

_LOG_LINE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} \| ")
MAX_BOX_CHARS = 1600

# argv tokens that only print something and exit
INFO_FLAGS = frozenset({"-h", "--help", "--version", "--check-config", "--list-settings"})
INFO_COMMANDS = frozenset({"doctor", "cheat-sheet"})


def is_windows() -> bool:
    return sys.platform == "win32"


def is_info_invocation(argv: list[str]) -> bool:
    """True when argv asks for text output only (help, version, doctor, ...)."""
    for arg in argv:
        if arg in INFO_FLAGS or arg in INFO_COMMANDS or arg.startswith("--print-config"):
            return True
    return False


def attach_parent_console() -> bool:
    """Bind stdout/stderr/stdin to the launching console. Returns True on success."""
    if not is_windows():
        return False
    try:
        import ctypes

        if not ctypes.windll.kernel32.AttachConsole(-1):  # ATTACH_PARENT_PROCESS
            return False
        sys.stdout = open("CONOUT$", "w", encoding="utf-8", errors="replace", buffering=1)  # noqa: SIM115
        sys.stderr = sys.stdout
        try:
            sys.stdin = open("CONIN$", encoding="utf-8", errors="replace")  # noqa: SIM115
        except OSError:
            pass
        return True
    except Exception:
        return False


def message_box(title: str, text: str, *, error: bool = False) -> None:
    if not is_windows():
        return
    try:
        import ctypes

        flags = 0x10 if error else 0x40  # MB_ICONERROR / MB_ICONINFORMATION
        ctypes.windll.user32.MessageBoxW(0, text, title, flags | 0x40000)  # MB_TOPMOST
    except Exception:
        pass


def show_text(title: str, text: str, *, error: bool = False) -> None:
    """Short text -> message box; long text -> temp .txt opened in the default viewer."""
    text = text.strip()
    if not text:
        return
    if len(text) <= MAX_BOX_CHARS:
        message_box(title, text, error=error)
        return
    try:
        path = Path(tempfile.gettempdir()) / "onvif-emulator-output.txt"
        path.write_text(text + "\n", encoding="utf-8")
        os.startfile(path)  # type: ignore[attr-defined]  # noqa: S606
    except Exception:
        message_box(title, text[:MAX_BOX_CHARS] + "\n...", error=error)


class Capture(io.StringIO):
    """In-memory stdout/stderr for a process that has no console."""

    def isatty(self) -> bool:
        return False

    def visible_text(self) -> str:
        """Captured text without log records (only real program output / errors)."""
        lines = [ln for ln in self.getvalue().splitlines() if not _LOG_LINE.match(ln) and not ln.startswith("Log file:")]
        return "\n".join(lines).strip()


def apply_gui_defaults(argv: list[str]) -> list[str]:
    """The windowed exe is the 'GUI' build: unless told otherwise it serves the web UI,
    opens the browser and picks another port if the default one is unavailable."""
    if is_info_invocation(argv):
        return argv

    def has(*names: str) -> bool:
        return any(a == n or a.startswith(n + "=") for a in argv for n in names)

    headless = has("--headless", "--no-gui")
    prefix: list[str] = []
    if not headless and not has("--service"):
        prefix.append("--service")
    if not headless and not has("--open-browser", "--no-open-browser"):
        prefix.append("--open-browser")
    if not headless and not has("--tray", "--no-tray"):
        prefix.append("--tray")
    if not has("--auto-port", "--no-auto-port"):
        prefix.append("--auto-port")
    return prefix + argv
