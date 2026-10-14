"""Tiny cross-platform CPU / RAM probes (no psutil): used by the resource governor."""

from __future__ import annotations

import os
import sys
import time


def cpu_count() -> int:
    return max(1, os.cpu_count() or 1)


def total_memory_mb() -> int:
    """Physical RAM in MB, 0 if unknown."""
    try:
        if sys.platform == "win32":
            return _win_mem()[0]
        with open("/proc/meminfo", encoding="ascii") as fh:
            for line in fh:
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) // 1024
    except (OSError, ValueError):
        pass
    return 0


def memory_percent() -> float:
    """Used physical memory in percent, 0.0 if unknown."""
    try:
        if sys.platform == "win32":
            return float(_win_mem()[1])
        info: dict[str, int] = {}
        with open("/proc/meminfo", encoding="ascii") as fh:
            for line in fh:
                k, _, v = line.partition(":")
                info[k] = int(v.split()[0])
        total, avail = info.get("MemTotal", 0), info.get("MemAvailable", 0)
        return 100.0 * (1 - avail / total) if total else 0.0
    except (OSError, ValueError, ZeroDivisionError):
        return 0.0


def _win_mem() -> tuple[int, int]:
    import ctypes

    class MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [
            ("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
            ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    st = MEMORYSTATUSEX()
    st.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st))
    return int(st.ullTotalPhys // (1024 * 1024)), int(st.dwMemoryLoad)


class CpuMeter:
    """Whole-machine CPU load in percent between two ``sample()`` calls."""

    def __init__(self) -> None:
        self._last = self._read()
        self._at = time.monotonic()

    @staticmethod
    def _read() -> tuple[int, int] | None:
        """(busy, total) tick counters."""
        try:
            if sys.platform == "win32":
                import ctypes
                from ctypes import wintypes

                idle, kernel, user = (wintypes.FILETIME() for _ in range(3))
                if not ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)):
                    return None

                def val(ft) -> int:
                    return (ft.dwHighDateTime << 32) | ft.dwLowDateTime

                total = val(kernel) + val(user)  # kernel time includes idle
                return total - val(idle), total
            with open("/proc/stat", encoding="ascii") as fh:
                parts = [int(x) for x in fh.readline().split()[1:8]]
            idle = parts[3] + parts[4]
            total = sum(parts)
            return total - idle, total
        except (OSError, ValueError, IndexError, AttributeError):
            return None

    def sample(self) -> float:
        cur = self._read()
        prev, self._last = self._last, cur
        if cur is None or prev is None or cur[1] <= prev[1]:
            return 0.0
        return max(0.0, min(100.0, 100.0 * (cur[0] - prev[0]) / (cur[1] - prev[1])))
