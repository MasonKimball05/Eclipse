# How much memory this Mac (or PC) has, for keeping fewer pictures in memory on one that hasn't much: an older
# Mac with 8 GB, say, where the pages themselves need most of it. And whether it's running short right now
# (memory_saver.py puts tabs to sleep then).

import os
import sys

SMALL_GB = 8  # this much or less counts as not much
NORMAL, LOW, CRITICAL = 0, 1, 2  # (how short of memory it is: pressure())
WINDOWS_LOW, WINDOWS_CRITICAL = 85, 93  # (Windows: the percentage of memory in use)


def total_gb() -> float:
    try:
        return os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE") / 2 ** 30
    except (ValueError, OSError, AttributeError):  # (Windows has neither)
        status = _windows_status()
        if status is not None:
            return status.total / 2 ** 30
    return 16.0  # (can't tell: as if plenty)


def _windows_status():
    try:
        import ctypes

        class Status(ctypes.Structure):
            _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong), ("total", ctypes.c_ulonglong),
                        ("free", ctypes.c_ulonglong), ("page_total", ctypes.c_ulonglong),
                        ("page_free", ctypes.c_ulonglong), ("virtual_total", ctypes.c_ulonglong),
                        ("virtual_free", ctypes.c_ulonglong), ("extended", ctypes.c_ulonglong)]
        status = Status(length=ctypes.sizeof(Status))
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return status
    except (AttributeError, OSError):
        pass
    return None


def pressure() -> int:
    """NORMAL, LOW or CRITICAL: how short of memory the machine is now (macOS's own memory pressure, as
    Activity Monitor shows it; on Windows, how much of it's in use)."""
    if sys.platform == "darwin":
        try:
            import ctypes
            import ctypes.util
            libc = ctypes.CDLL(ctypes.util.find_library("c"))
            level, size = ctypes.c_int(0), ctypes.c_size_t(ctypes.sizeof(ctypes.c_int))
            if libc.sysctlbyname(b"kern.memorystatus_vm_pressure_level", ctypes.byref(level), ctypes.byref(size),
                                 None, 0) == 0:
                return {2: LOW, 4: CRITICAL}.get(level.value, NORMAL)  # (1 is normal)
        except (OSError, AttributeError):
            pass
        return NORMAL
    status = _windows_status()
    if status is None:
        return NORMAL
    return CRITICAL if status.load >= WINDOWS_CRITICAL else LOW if status.load >= WINDOWS_LOW else NORMAL


def small() -> bool:
    return total_gb() <= SMALL_GB


def kept(plenty: int, little: int) -> int:
    """How many of something to keep: `little` on a machine without much memory."""
    return little if small() else plenty
