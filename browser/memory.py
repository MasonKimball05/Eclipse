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


def process_mb(pid: int) -> float | None:
    """How much memory a process is using, in megabytes: on a Mac its footprint, as Activity Monitor's Memory
    column shows it (not counting what it shares with others); None if it can't be told."""
    if not pid or pid <= 0:
        return None
    try:
        import ctypes
        if sys.platform == "darwin":
            libproc = ctypes.CDLL("/usr/lib/libproc.dylib")
            info = (ctypes.c_uint64 * 32)()  # (rusage_info_v2: 16 bytes of uuid, then 64-bit counts)
            if libproc.proc_pid_rusage(ctypes.c_int(pid), ctypes.c_int(2), ctypes.byref(info)) != 0:
                return None
            return info[9] / 2 ** 20  # ri_phys_footprint
        if sys.platform == "win32":
            from ctypes import wintypes

            class Counters(ctypes.Structure):
                _fields_ = [("cb", wintypes.DWORD), ("faults", wintypes.DWORD)] + [
                    (name, ctypes.c_size_t) for name in ("peak_working", "working", "peak_paged_pool", "paged_pool",
                                                         "peak_nonpaged_pool", "nonpaged_pool", "pagefile",
                                                         "peak_pagefile", "private")]
            handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)  # (query limited information)
            if not handle:
                return None
            try:
                counters = Counters(cb=ctypes.sizeof(Counters))
                if not ctypes.windll.psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
                    return None
                return counters.private / 2 ** 20
            finally:
                ctypes.windll.kernel32.CloseHandle(handle)
        with open(f"/proc/{pid}/statm") as statm:  # (Linux: resident pages)
            return int(statm.read().split()[1]) * os.sysconf("SC_PAGE_SIZE") / 2 ** 20
    except (OSError, ValueError, AttributeError, IndexError):
        return None
