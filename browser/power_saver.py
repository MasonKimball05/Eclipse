# Low Power Mode: when the Mac's saving its battery (System Settings → Battery), or running hot enough to slow
# itself down, Eclipse saves energy too:
#   - background tabs you haven't looked at for a couple of minutes are frozen (kept, but their scripts stop
#     running: no timers, no animations), and wake as you switch to them;
#   - idle tabs go to sleep sooner (after 10 minutes, if your own setting's longer; "Never" stays never);
#   - the sidebar, toasts and the rest just appear instead of moving.
# Everything goes back as it was when Low Power Mode's turned off. macOS's state is looked at every half
# minute (asking is cheap). Settings → General → "Save energy in Low Power Mode" turns it off.

import time

from PyQt6.QtCore import QTimer
from PyQt6.QtWebEngineCore import QWebEnginePage

from . import macos, motion
from .features import Feature

SETTING = "power_saver"
CHECK_MS = 30 * 1000
FREEZE_AFTER = 2 * 60   # seconds in the background before a tab's frozen
SLEEP_MINUTES = 10      # idle tabs sleep after this, at most


class PowerSaverFeature(Feature):
    name = "power_saver"

    def __init__(self, window):
        super().__init__(window)
        self.saving = False
        self.timer = QTimer(window, interval=CHECK_MS, timeout=self.check)

    def on(self) -> bool:
        return self.window.store.settings.get(SETTING, True)

    def ready(self):
        self.timer.start()
        self.check(announce=False)

    def check(self, announce=True):
        saving = self.on() and macos.low_power()
        if saving != self.saving:
            self.saving = saving
            motion.saving = saving
            self.window.saving_minutes = SLEEP_MINUTES if saving else None
            if saving:
                if announce:
                    self.window.toast("Low Power Mode: Eclipse is saving energy too")
                self.window.sleep_idle_tabs()
            else:
                self.thaw()
        if saving:
            self.freeze_idle()

    def freeze_idle(self):
        cutoff = time.time() - FREEZE_AFTER
        for tab in list(self.window.resting_tabs()):
            page = tab.view.page()
            if page.lifecycleState() == QWebEnginePage.LifecycleState.Active and tab.last_active < cutoff:
                page.setLifecycleState(QWebEnginePage.LifecycleState.Frozen)

    def thaw(self):
        """Low Power Mode's over: frozen tabs carry on (sleeping ones stay asleep until you look at them)."""
        for tab in self.window.store.all_tabs():
            page = tab.view.page() if hasattr(tab.view, "page") else None
            if page is not None and page.lifecycleState() == QWebEnginePage.LifecycleState.Frozen:
                page.setLifecycleState(QWebEnginePage.LifecycleState.Active)

    def tab_shown(self, tab):
        page = tab.view.page() if tab is not None and hasattr(tab.view, "page") else None
        if page is not None and page.lifecycleState() == QWebEnginePage.LifecycleState.Frozen:
            page.setLifecycleState(QWebEnginePage.LifecycleState.Active)  # (a frozen page shown carries on)

    def setting_changed(self):
        self.check(announce=False)

    def shutdown(self):
        self.timer.stop()
        motion.saving = False


FEATURE = PowerSaverFeature
