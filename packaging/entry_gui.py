"""Entry for the windowed (no console) PyInstaller build.

Double-click / no args -> web GUI service, browser opens, free port is picked if
8080 is busy. Every CLI flag of the console build works here too:

* from cmd/PowerShell the output goes to that console;
* with no console, --help / --version / doctor / errors appear in a message box.

Crashes always land in onvif-emulator-crash.log beside the exe.
"""

from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path


def _exe_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path.cwd()


def _early_crash(text: str) -> None:
    """Last-resort dump before/without the logging package."""
    for base in (_exe_dir(), Path(os.environ.get("APPDATA", ".")) / "onvif-emulator"):
        try:
            base.mkdir(parents=True, exist_ok=True)
            with (base / "onvif-emulator-crash.log").open("a", encoding="utf-8") as fh:
                fh.write(text if text.endswith("\n") else text + "\n")
            return
        except OSError:
            continue


def run() -> int:
    from onvif_emu import winui

    argv = sys.argv[1:]
    have_console = sys.stdout is not None and sys.stderr is not None
    if not have_console:
        have_console = winui.attach_parent_console()
    capture = None
    if not have_console:
        capture = winui.Capture()
        sys.stdout = sys.stderr = capture
        sys.stdin = None

    info = winui.is_info_invocation(argv)
    argv = winui.apply_gui_defaults(argv)

    code = 1
    try:
        from onvif_emu.cli import main

        code = int(main(argv))
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
    except BaseException as exc:  # noqa: BLE001
        _early_crash(f"[fatal] {exc!r}\n{traceback.format_exc()}")
        if capture is not None:
            winui.message_box("ONVIF Emulator crashed", f"{exc!r}\n\nDetails: onvif-emulator-crash.log", error=True)
        raise

    if capture is not None:
        if info:
            winui.show_text("ONVIF Emulator", capture.visible_text(), error=code != 0)
        elif code != 0:
            from onvif_emu.logging_setup import LAST_FATAL, crash_log_path

            reason = LAST_FATAL[1] if LAST_FATAL else capture.visible_text() or f"exit code {code}"
            winui.message_box(
                "ONVIF Emulator - cannot start",
                f"{reason}\n\nExit code {code}. Try another port: --onvif-port 18080\n"
                f"Details: {crash_log_path()}",
                error=True,
            )
    return code


if __name__ == "__main__":
    try:
        raise SystemExit(run())
    except SystemExit:
        raise
    except BaseException:
        _early_crash("[entry_gui] top-level failure\n" + traceback.format_exc())
        raise
