"""Process façade: owns Runtime + HTTP + WS-Discovery lifecycle."""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

from aiohttp import web

from onvif_emu.exit_codes import OK, PORT_IN_USE
from onvif_emu.models import AppConfig
from onvif_emu.runtime import Runtime

log = logging.getLogger("onvif_emu")

AUTO_PORT_TRIES = 50


class AppFacade:
    """Single entry for start/stop of the emulator process surface."""

    def __init__(self, cfg: AppConfig, config_path: Path | None = None):
        self.cfg = cfg
        self.config_path = config_path
        self.runtime = Runtime(cfg, config_path=config_path)
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None
        self.runtime.rebind = self.rebind
        self._disco: tuple | None = None
        self._current_url = ""

    async def run(self, *, open_browser: bool = False, tray: bool = False) -> int:
        try:
            return await self._run(open_browser=open_browser, tray=tray)
        finally:
            # Covers cancellation and failures in runner setup/discovery too.
            await self._shutdown()

    async def _run(self, *, open_browser: bool = False, tray: bool = False) -> int:
        from onvif_emu.discovery import start_discovery
        from onvif_emu.http_app import create_app
        from onvif_emu.logging_setup import install_asyncio_handler

        loop = asyncio.get_running_loop()
        install_asyncio_handler(loop)
        rt = self.runtime
        try:
            await rt.start_background()
        except Exception:
            log.exception("Failed to start background services")
            raise

        app = create_app(rt)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        bind_host = self.cfg.host or "0.0.0.0"
        try:
            await self._bind(bind_host)
        except OSError as exc:
            msg = f"Cannot bind {bind_host}:{self.cfg.onvif_port}: {exc}"
            print(f"{msg}\nSee --help", file=sys.stderr)
            # CRITICAL so it survives default CRITICAL-only file filter; also crash file for GUI exe.
            log.critical("%s", msg)
            try:
                from onvif_emu.logging_setup import record_fatal_exit

                record_fatal_exit(
                    PORT_IN_USE,
                    f"port bind failed {bind_host}:{self.cfg.onvif_port}: {exc} "
                    f"(another process may own the port or Windows reserved it; "
                    f"try --onvif-port <free> or --auto-port)",
                )
            except Exception:
                pass
            return PORT_IN_USE

        self._disco = await start_discovery(rt)
        self._announce(bind_host, open_browser=open_browser, tray=tray)
        try:
            await rt.wait_stopped()
        except asyncio.CancelledError:
            pass
        return OK

    async def rebind(self, host: str, port: int) -> str:
        """Listen on host:port too, then retire the old listener shortly after.

        Returns "" on success or an error text (the old listener stays in that case).
        """
        assert self._runner is not None
        old = self._site
        site = web.TCPSite(self._runner, host, port, reuse_address=True)
        try:
            await site.start()
        except OSError as exc:
            log.error("Cannot rebind to %s:%s: %s", host, port, exc)
            try:
                await site.stop()
            except Exception:
                pass
            return f"Cannot bind {host}:{port}: {exc}"
        self._site = site
        log.warning("HTTP listener moved to %s:%s", host, port)
        print(f"HTTP listener moved to {host}:{port}", flush=True)
        if old is not None:
            await old.stop()  # accepted connections (this very request) stay open
        if self._current_url:
            self._current_url = f"http://{self.runtime.public_host()}:{port}/"
        return ""

    async def _bind(self, bind_host: str) -> None:
        """Bind the HTTP site; with cfg.auto_port walk up to the next free port."""
        assert self._runner is not None
        first = self.cfg.onvif_port
        tries = AUTO_PORT_TRIES if self.cfg.auto_port else 1
        last_exc: OSError | None = None
        for port in range(first, min(first + tries, 65536)):
            site = web.TCPSite(self._runner, bind_host, port, reuse_address=True)
            try:
                await site.start()
            except OSError as exc:
                last_exc = exc
                log.warning("Cannot bind %s:%s: %s", bind_host, port, exc)
                try:
                    await site.stop()
                except Exception:
                    pass
                continue
            self._site = site
            if port != first:
                msg = f"Port {first} is unavailable, using {port} instead (--auto-port)"
                print(msg, flush=True)
                log.warning("%s", msg)
                self.cfg.onvif_port = port
                self.cfg.network.http_port = port
            return
        assert last_exc is not None
        raise last_exc

    def _announce(self, bind_host: str, *, open_browser: bool, tray: bool = False) -> None:
        cfg = self.cfg
        rt = self.runtime
        print(f"ONVIF emulator http://{rt.public_host()}:{cfg.onvif_port}/  auth={cfg.auth.mode.value}")
        print(f"Listening {bind_host}:{cfg.onvif_port} (0.0.0.0 = all interfaces)")
        log.info(
            "Listening %s:%s (all interfaces=%s) public=%s auth=%s",
            bind_host,
            cfg.onvif_port,
            bind_host in {"0.0.0.0", "::"},
            rt.public_host(),
            cfg.auth.mode.value,
        )
        log.info("ONVIF emulator http://%s:%s/ auth=%s", rt.public_host(), cfg.onvif_port, cfg.auth.mode.value)
        if not (rt.media and rt.media.mtx_alive):
            print("Note: MediaMTX is not running — RTSP is off. Web GUI and ONVIF still work.")
            log.warning("MediaMTX is not running — RTSP is off")
        if cfg.gui:
            url = f"http://{rt.public_host()}:{cfg.onvif_port}/"
            print(f"Web GUI (leave this process running): {url}")
            log.info("GUI: %s", url)
            if open_browser:
                import webbrowser

                webbrowser.open(url)
            if tray:
                self._start_tray(url)
        if cfg.logging.file:
            log.info("Log file: %s levels=%s", cfg.logging.file, ",".join(cfg.logging.levels))

    def _start_tray(self, url: str) -> None:
        """Notification-area icon: the windowed exe has no window/taskbar entry of its
        own, so without this the only sign it's running is Task Manager. Opt-in via
        --tray/--no-tray (on by default in the windowed exe, off in the console one)."""
        if self.cfg.instance != 1:
            return
        try:
            from onvif_emu import tray

            if not tray.is_supported():
                return
            from onvif_emu.paths import package_dir

            icon_path = package_dir() / "gui" / "static" / "app.ico"
            self._current_url = url
            loop = asyncio.get_running_loop()
            rt = self.runtime
            tray.ensure_started(
                icon_path=icon_path,
                tooltip=f"ONVIF Emulator — {self.cfg.device.name} :{self.cfg.onvif_port}",
                url_provider=lambda: self._current_url,
                on_exit=lambda: loop.call_soon_threadsafe(rt.request_stop),
            )
        except Exception:
            log.exception("tray icon setup failed")

    async def _shutdown(self) -> None:
        if self._current_url:
            try:
                from onvif_emu import tray

                tray.stop()
            except Exception:
                pass
        if self._disco:
            try:
                self._disco[0].close()
            except Exception:
                pass
            self._disco = None
        try:
            if self._runner:
                await self._runner.cleanup()
                self._runner = None
        finally:
            await self.runtime.stop()
