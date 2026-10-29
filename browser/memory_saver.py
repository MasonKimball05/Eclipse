# Memory Saver: when the Mac runs short of memory (macOS's memory pressure, as Activity Monitor shows it; on
# Windows, most of it in use), the tabs you've gone longest without looking at go to sleep, a few at a time,
# until it isn't short any more. Each awake tab holds its own page process (a hundred megabytes or more), so
# this frees far more than anything else could, and matters most on a Mac with 8 GB.
#   - short (macOS's "warning"): tabs unused for 5 minutes, 3 at a time;
#   - critical: tabs unused for a minute, 6 at a time.
# The same tabs never sleep as with the idle timer: the one you're on, split panes beside it, tabs playing
# sound, on a call, allowed to notify you, or kept awake (window.resting_tabs). Asked every 15 seconds (it's
# cheap). Settings → Tabs → "Sleep tabs when memory runs low" turns it off.

import time

from PyQt6.QtCore import QTimer

from . import memory
from .features import Feature

SETTING = "memory_saver"
CHECK_MS = 15 * 1000
UNUSED = {memory.LOW: 5 * 60, memory.CRITICAL: 60}  # seconds since you last looked, before a tab can sleep
AT_ONCE = {memory.LOW: 3, memory.CRITICAL: 6}       # (then a look again, next time, at whether that was enough)
TOAST_EVERY = 10 * 60  # seconds: say so, but not every time


class MemorySaverFeature(Feature):
    name = "memory_saver"

    def __init__(self, window):
        super().__init__(window)
        self.timer = QTimer(window, interval=CHECK_MS, timeout=self.check)
        self.told = 0.0  # (when it last said so)

    def on(self) -> bool:
        return self.window.store.settings.get(SETTING, True)

    def ready(self):
        self.timer.start()

    def check(self) -> int:
        """Put some tabs to sleep if memory's short; how many it put to sleep."""
        level = memory.pressure() if self.on() else memory.NORMAL
        if level == memory.NORMAL:
            return 0
        cutoff = time.time() - UNUSED[level]
        idle = sorted((t for t in self.window.resting_tabs() if t.last_active < cutoff), key=lambda t: t.last_active)
        chosen = idle[:AT_ONCE[level]]
        for tab in chosen:
            self.window.put_to_sleep(tab)
        if chosen and time.time() - self.told > TOAST_EVERY:
            self.told = time.time()
            count = len(chosen)
            self.window.toast(f"Memory's running low: put {count} tab{'s' if count != 1 else ''} you haven't used "
                              "lately to sleep")
        return len(chosen)

    def setting_changed(self):
        self.check()

    def shutdown(self):
        self.timer.stop()


FEATURE = MemorySaverFeature
