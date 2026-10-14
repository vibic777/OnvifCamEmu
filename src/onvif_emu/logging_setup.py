"""Console + rotating file logging.

Levels are selected independently (not a threshold): enable any subset of
TRACE, DEBUG, INFO, WARNING, ERROR, CRITICAL/FATAL. Default: CRITICAL only.
Log file defaults to onvif-emulator.log beside the program; --log-dir /
logging.dir selects the directory for that file.
"""

from __future__ import annotations

import logging
import os
import re
import sys
import threading
import traceback
from collections.abc import Iterable, Sequence
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, TextIO

from onvif_emu.paths import executable_dir, user_data_dir

LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
LOG_NAME = "onvif-emulator.log"
TRACE = 5
logging.addLevelName(TRACE, "TRACE")
logging.addLevelName(logging.CRITICAL, "CRITICAL")

LEVEL_ALIASES = {
    "WARN": "WARNING",
    "FATAL": "CRITICAL",
    "TRACER": "TRACE",
    "OFF": "CRITICAL",
}

# Selectable names shown in CLI/GUI (FATAL kept as alias label for CRITICAL).
NAMED_LEVELS = ("TRACE", "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL", "FATAL")
CANONICAL_LEVELS = ("TRACE", "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
DEFAULT_LEVELS: tuple[str, ...] = ("CRITICAL",)
MAX_BYTES = 5 * 1024 * 1024
BACKUP_COUNT = 3

_FILE_PATH: Path | None = None
_ENABLED_LEVELS: frozenset[str] = frozenset(DEFAULT_LEVELS)


def _logger_trace(self: logging.Logger, msg: str, *args: Any, **kwargs: Any) -> None:
    if self.isEnabledFor(TRACE):
        self._log(TRACE, msg, args, **kwargs)


if not hasattr(logging.Logger, "trace"):
    logging.Logger.trace = _logger_trace  # type: ignore[attr-defined]


class SelectedLevelsFilter(logging.Filter):
    """Pass only records whose level name is in the enabled set (independent of hierarchy)."""

    def __init__(self, levels: Iterable[str]) -> None:
        super().__init__()
        self._allowed_nos = {level_to_numeric(n) for n in levels}

    def filter(self, record: logging.LogRecord) -> bool:
        return record.levelno in self._allowed_nos


def default_log_path() -> Path:
    return executable_dir() / LOG_NAME


def normalize_level_name(level: str | None, *, default: str = "CRITICAL") -> str:
    raw = str(level or default).strip().upper()
    raw = LEVEL_ALIASES.get(raw, raw)
    if raw in CANONICAL_LEVELS:
        return raw
    if raw == "FATAL":
        return "CRITICAL"
    return default


def parse_log_level(level: str | None, default: str = "CRITICAL") -> str:
    """Single-level parse (compat). Prefer parse_log_levels for multi-select."""
    return normalize_level_name(level, default=default)


def parse_log_levels(raw: str | Sequence[str] | None, *, default: Sequence[str] | None = None) -> list[str]:
    """Parse independently selected levels from a string or sequence.

    Accepts ``ALL`` / ``*`` for every level, comma/semicolon/space separated lists,
    and aliases (WARN, FATAL, TRACER, OFF).
    """
    fallback = list(default if default is not None else DEFAULT_LEVELS)
    if raw is None or raw == "":
        return list(fallback)
    if isinstance(raw, str):
        text = raw.strip()
        if text.upper() in {"ALL", "*", "EVERYTHING"}:
            return list(CANONICAL_LEVELS)
        parts = [p for p in re.split(r"[,;|\s]+", text) if p]
    else:
        parts = [str(p) for p in raw if str(p).strip()]
    out: list[str] = []
    seen: set[str] = set()
    for part in parts:
        name = normalize_level_name(part)
        if name not in seen:
            seen.add(name)
            out.append(name)
    return out or list(fallback)


def level_to_numeric(level: str | None) -> int:
    name = normalize_level_name(level)
    if name == "TRACE":
        return TRACE
    return int(getattr(logging, name, logging.CRITICAL))


def enabled_levels() -> frozenset[str]:
    return _ENABLED_LEVELS


def resolve_log_path(
    configured: str | Path | None = None,
    cli: str | Path | None = None,
    *,
    log_dir: str | Path | None = None,
    cli_dir: str | Path | None = None,
) -> Path:
    """Resolve log file path.

    Priority: CLI --log-file > config logging.file >
    CLI --log-dir / config logging.dir > beside the program.
    If the chosen path is an existing directory, place onvif-emulator.log inside it.
    """
    file_raw = cli if cli not in (None, "") else configured
    dir_raw = cli_dir if cli_dir not in (None, "") else log_dir

    if file_raw not in (None, ""):
        path = Path(str(file_raw)).expanduser()
        if not path.is_absolute():
            path = executable_dir() / path
        if path.exists() and path.is_dir():
            path = path / LOG_NAME
        return _ensure_writable(path)

    if dir_raw not in (None, ""):
        folder = Path(str(dir_raw)).expanduser()
        if not folder.is_absolute():
            folder = executable_dir() / folder
        return _ensure_writable(folder / LOG_NAME)

    return _ensure_writable(default_log_path())


def _ensure_writable(path: Path) -> Path:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8"):
            pass
        return path
    except OSError:
        fallback = user_data_dir() / LOG_NAME
        fallback.parent.mkdir(parents=True, exist_ok=True)
        return fallback


def current_log_path() -> Path | None:
    return _FILE_PATH


def setup_logging(
    level: str | Sequence[str] | None = None,
    log_file: str | Path | None = None,
    *,
    levels: Sequence[str] | None = None,
    log_dir: str | Path | None = None,
    console: bool = True,
    max_mb: float | None = None,
    backups: int | None = None,
) -> logging.Logger:
    """Configure root logger: stderr + rotating file.

    ``levels`` (or ``level``) is an independent multi-select set — not a minimum threshold.
    Default is CRITICAL only.
    """
    global _FILE_PATH, _ENABLED_LEVELS
    selected = parse_log_levels(levels if levels is not None else level)
    _ENABLED_LEVELS = frozenset(selected)
    filt = SelectedLevelsFilter(selected)

    root = logging.getLogger()
    # Accept every record; SelectedLevelsFilter decides what is emitted.
    root.setLevel(TRACE)
    for handler in list(root.handlers):
        root.removeHandler(handler)
        handler.close()

    formatter = logging.Formatter(LOG_FORMAT, DATE_FORMAT)
    stream: TextIO | None = sys.stderr
    if console and stream is not None:  # windowed exe has no stderr
        sh = logging.StreamHandler(stream)
        sh.setFormatter(formatter)
        sh.setLevel(TRACE)
        sh.addFilter(filt)
        root.addHandler(sh)
    max_bytes = int(max_mb * 1024 * 1024) if max_mb else MAX_BYTES
    backup_count = BACKUP_COUNT if backups is None else int(backups)

    path = resolve_log_path(log_file, log_dir=log_dir)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fh = RotatingFileHandler(
            path,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
            delay=False,
        )
        fh.setFormatter(formatter)
        fh.setLevel(TRACE)
        fh.addFilter(filt)
        root.addHandler(fh)
        _FILE_PATH = path
    except OSError as exc:
        fallback = user_data_dir() / LOG_NAME
        try:
            fallback.parent.mkdir(parents=True, exist_ok=True)
            fh = RotatingFileHandler(
                fallback,
                maxBytes=max_bytes,
                backupCount=backup_count,
                encoding="utf-8",
                delay=False,
            )
            fh.setFormatter(formatter)
            fh.setLevel(TRACE)
            fh.addFilter(filt)
            root.addHandler(fh)
            _FILE_PATH = fallback
            root.error("Cannot open log file %s (%s); using %s", path, exc, fallback)
        except OSError as exc2:
            _FILE_PATH = None
            root.error("Cannot open log file %s or fallback %s: %s / %s", path, fallback, exc, exc2)

    # aiohttp access noise: only when INFO (or lower names) selected
    access = logging.getLogger("aiohttp.access")
    if "INFO" in _ENABLED_LEVELS or "DEBUG" in _ENABLED_LEVELS or "TRACE" in _ENABLED_LEVELS:
        access.setLevel(logging.INFO)
    else:
        access.setLevel(logging.CRITICAL + 1)

    log = logging.getLogger("onvif_emu")
    log.debug("File log: %s levels=%s", _FILE_PATH, ",".join(selected))
    return log


def flush_logging() -> None:
    for handler in logging.getLogger().handlers:
        try:
            handler.flush()
        except OSError:
            pass


def _sole_console_process() -> bool:
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.windll.kernel32
        GetConsoleProcessList = kernel32.GetConsoleProcessList
        GetConsoleProcessList.argtypes = [ctypes.POINTER(wintypes.DWORD), wintypes.DWORD]
        GetConsoleProcessList.restype = wintypes.DWORD
        buf = (wintypes.DWORD * 16)()
        n = int(GetConsoleProcessList(buf, 16))
        return n == 1
    except Exception:
        return False


def pause_if_double_clicked(code: int) -> None:
    """Keep the console readable after a double-click launch that exited with an error."""
    if code == 0:
        return
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return
    if not sys.stdin or not sys.stdin.isatty():
        return
    if not _sole_console_process():
        return
    path = _FILE_PATH or default_log_path()
    print(f"\nExited with code {code}. Log file: {path}", flush=True)
    try:
        input("Press Enter to close...")
    except EOFError:
        pass


def _log_crash(where: str, exc_info: tuple | BaseException | None = None) -> None:
    log = logging.getLogger("onvif_emu")
    if isinstance(exc_info, BaseException):
        log.critical("%s: %s", where, exc_info, exc_info=exc_info)
        try:
            write_crash_file(f"{where}: {exc_info!r}", exc=exc_info)
        except Exception:
            pass
        return
    if isinstance(exc_info, tuple) and exc_info[0] is not None:
        log.critical("%s", where, exc_info=exc_info)
        try:
            tb = "".join(traceback.format_exception(exc_info[0], exc_info[1], exc_info[2]))
            write_crash_file(f"{where}\n{tb}")
        except Exception:
            pass
        return
    log.critical("%s", where)
    try:
        write_crash_file(str(where))
    except Exception:
        pass



CRASH_LOG_NAME = "onvif-emulator-crash.log"


def crash_log_path() -> Path:
    """Preferred crash dump path: beside the program (or user_data_dir fallback)."""
    try:
        return _ensure_writable(executable_dir() / CRASH_LOG_NAME)
    except Exception:
        fallback = user_data_dir() / CRASH_LOG_NAME
        try:
            fallback.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        return fallback


def write_crash_file(message: str, *, exc: BaseException | None = None) -> Path | None:
    """Append to onvif-emulator-crash.log beside the exe (always, ignore level filter).

    Also mirrors a CRITICAL line into the normal logger when possible.
    """
    from datetime import datetime

    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    parts = [f"===== {stamp} =====", message.rstrip()]
    if exc is not None:
        parts.append("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)).rstrip())
    block = "\n".join(parts) + "\n\n"
    out: Path | None = None
    for candidate in (crash_log_path(), default_log_path().with_name(CRASH_LOG_NAME)):
        try:
            candidate.parent.mkdir(parents=True, exist_ok=True)
            with candidate.open("a", encoding="utf-8") as fh:
                fh.write(block)
                fh.flush()
            out = candidate
            break
        except OSError:
            continue
    try:
        logging.getLogger("onvif_emu").critical("%s", message.splitlines()[0] if message else "crash")
        flush_logging()
    except Exception:
        pass
    # Also append a short note into the regular log file unfiltered when possible.
    log_path = _FILE_PATH or default_log_path()
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(f"{stamp} | CRITICAL | onvif_emu.crash | {message.splitlines()[0]}\n")
            if len(message.splitlines()) > 1:
                for line in message.splitlines()[1:]:
                    fh.write(f"{stamp} | CRITICAL | onvif_emu.crash | {line}\n")
            fh.flush()
    except OSError:
        pass
    return out


LAST_FATAL: tuple[int, str] | None = None


def record_fatal_exit(code: int, reason: str) -> None:
    """Non-exception process exit (port busy, config error) still gets a crash file."""
    global LAST_FATAL
    if LAST_FATAL is not None and LAST_FATAL[0] == code:
        return  # keep the first, most specific reason for this exit code
    LAST_FATAL = (code, reason)
    write_crash_file(f"[exit] code={code} reason={reason}")
    flush_logging()


def install_faulthandler_to_log() -> None:
    """Dump hard crashes (segfaults) into onvif-emulator-crash.log."""
    import faulthandler

    path = crash_log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # Keep file handle open for the process lifetime (faulthandler requirement).
    global _FAULT_FH
    if _FAULT_FH is not None:
        try:
            _FAULT_FH.close()
        except Exception:
            pass
    _FAULT_FH = open(path, "a", encoding="utf-8")  # noqa: SIM115
    faulthandler.enable(file=_FAULT_FH, all_threads=True)


_FAULT_FH = None  # type: ignore[var-annotated]


def install_crash_handlers() -> None:
    """sys.excepthook + threading so a portable exe crash is on disk."""

    def hook(exc_type, exc, tb) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc, tb)
            return
        _log_crash("Unhandled exception", (exc_type, exc, tb))
        flush_logging()
        try:
            sys.__excepthook__(exc_type, exc, tb)
        except Exception:
            traceback.print_exception(exc_type, exc, tb)

    sys.excepthook = hook

    if hasattr(threading, "excepthook"):

        def thread_hook(args: threading.ExceptHookArgs) -> None:
            if args.exc_type is KeyboardInterrupt:
                return
            _log_crash(
                f"Unhandled exception in thread {args.thread}",
                (args.exc_type, args.exc_value, args.exc_traceback),
            )

        threading.excepthook = thread_hook


def install_asyncio_handler(loop) -> None:
    def handler(_loop, context: dict) -> None:
        exc = context.get("exception")
        msg = context.get("message", "asyncio error")
        if exc:
            logging.getLogger("onvif_emu").critical("Asyncio: %s", msg, exc_info=exc)
        else:
            logging.getLogger("onvif_emu").critical("Asyncio: %s | %s", msg, context)

    loop.set_exception_handler(handler)


def peek_log_file(argv: list[str]) -> str | None:
    """Read --log-file / -L from argv before full argparse (early crash log)."""
    for i, arg in enumerate(argv):
        if arg in {"--log-file", "--logfile", "-L"} and i + 1 < len(argv):
            return argv[i + 1]
        if arg.startswith("--log-file="):
            return arg.split("=", 1)[1]
        if arg.startswith("--logfile="):
            return arg.split("=", 1)[1]
    return None


def peek_log_dir(argv: list[str]) -> str | None:
    for i, arg in enumerate(argv):
        if arg in {"--log-dir", "--logdir"} and i + 1 < len(argv):
            return argv[i + 1]
        if arg.startswith("--log-dir="):
            return arg.split("=", 1)[1]
        if arg.startswith("--logdir="):
            return arg.split("=", 1)[1]
    return None


def peek_log_levels(argv: list[str]) -> list[str]:
    """Read --log-level / -v / -q before full argparse. Default CRITICAL only."""
    if "-v" in argv or "--verbose" in argv:
        levels: list[str] = list(CANONICAL_LEVELS)
    elif "-q" in argv or "--quiet" in argv:
        levels = ["ERROR", "CRITICAL"]
    else:
        levels = list(DEFAULT_LEVELS)
    for i, arg in enumerate(argv):
        if arg in {"--log-level", "--loglevel", "--log-levels"} and i + 1 < len(argv):
            levels = parse_log_levels(argv[i + 1])
        elif arg.startswith("--log-level="):
            levels = parse_log_levels(arg.split("=", 1)[1])
        elif arg.startswith("--loglevel="):
            levels = parse_log_levels(arg.split("=", 1)[1])
        elif arg.startswith("--log-levels="):
            levels = parse_log_levels(arg.split("=", 1)[1])
    return levels


def peek_log_level(argv: list[str]) -> str:
    """Compat: first selected level (or CRITICAL)."""
    levels = peek_log_levels(argv)
    return levels[0] if levels else "CRITICAL"
