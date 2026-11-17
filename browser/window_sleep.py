# Tabs asleep and awake: Memory Saver's and the idle timer's sleeping, and pages woken ahead of you.
# (A part of MainWindow, kept in a file of its own: window.py's MainWindow takes it in. Its methods use the
# window's own attributes as if they were written there.)

import time

from PyQt6 import sip
from PyQt6.QtCore import QTimer, QUrl
from PyQt6.QtWebEngineCore import QWebEnginePage, QWebEnginePermission, QWebEngineScript
from PyQt6.QtWebEngineWidgets import QWebEngineView

from .permissions import origin_key
from .settings import DEFAULT_SLEEP_MINUTES


class TabSleep:
    # The Spaces either side, ready: a tab's page is only made the first time it's shown, so the first switch to a
    # Space after opening Eclipse showed its page a second or two late, loading from scratch. Once you've settled on
    # a Space, the tab each Space beside it opens on is made (or woken) in the background; a swipe heading for a
    # Space starts on its tab too (sidebar_native.py). Not in Low Power Mode.
    PREPARE_AFTER_MS = 1500

    def is_sleeping(self, tab) -> bool:
        view = tab.view
        return (isinstance(view, QWebEngineView)
                and view.page().lifecycleState() == QWebEnginePage.LifecycleState.Discarded)

    def _prepare_soon(self):
        if getattr(self, "_preparing", None) is None:
            self._preparing = QTimer(self, singleShot=True, interval=self.PREPARE_AFTER_MS,
                                     timeout=self._prepare_neighbors)
        self._preparing.start()

    def _prepare_neighbors(self):
        spaces = self.store.spaces
        if self.store.active_space not in spaces:
            return
        i = spaces.index(self.store.active_space)
        for j in (i - 1, i + 1):
            if 0 <= j < len(spaces):
                self.prepare_space(spaces[j])

    def prepare_space(self, space):
        """The tab `space` opens on, made or woken in the background, so a switch there shows it at once."""
        if self.saving_minutes or space is self.store.active_space or not space.active_tab_id:
            return
        tab = self.store.find_tab(space.active_tab_id)
        located = self.store.locate(tab) if tab is not None else None
        if located is None or located[1] is not space:
            return
        if tab.view is None:
            tab.last_active = time.time()  # (looked at, as good as: Memory Saver doesn't put it straight back)
            self._ensure_view(tab)
        elif self.is_sleeping(tab):
            self.wake_ahead(tab)

    def wake_ahead(self, tab):
        """The pointer's resting on a sleeping tab: wake it before the click (not in Low Power Mode, nor for a tab
        that's gone or awake already). Looked at, it counts as used, so Memory Saver doesn't put it straight back."""
        if self.saving_minutes or sip.isdeleted(tab) or not self.is_sleeping(tab):
            return
        tab.last_active = time.time()
        self._wake(tab)
        tab.changed.emit()  # (its row: no longer dimmed as asleep)

    def _wake(self, tab):
        if self.is_sleeping(tab):
            tab.view.page().setLifecycleState(QWebEnginePage.LifecycleState.Active)  # reloads the page

    def resting_tabs(self):
        """Background pages that could be let go: never the current tab, visible split panes, a tab you asked to
        keep awake, or one playing audio, on a call, or allowed to notify you (asleep, it couldn't)."""
        keep_awake = self.store.split_of(self.current_tab) or [self.current_tab] if self.current_tab else []
        # (and the one the Now Playing card plays, paused: frozen or asleep, the card's Play did nothing)
        playing = getattr(getattr(self.feature("now_playing"), "player", None), "tab", None)
        if playing is not None:
            keep_awake = [*keep_awake, playing]
        for tab in self.store.all_tabs():
            view = tab.view
            if not isinstance(view, QWebEngineView) or any(tab is t for t in keep_awake) or view.isVisible():
                continue
            if tab.awake:  # you asked for it to stay awake
                continue
            page = view.page()
            if page.lifecycleState() == QWebEnginePage.LifecycleState.Discarded or page.recentlyAudible():
                continue
            if self.permissions.is_capturing(view):  # on a call, or sharing the screen
                continue
            notifier = page.profile().queryPermission(QUrl(origin_key(QUrl(tab.url))),
                                                       QWebEnginePermission.PermissionType.Notifications)
            if notifier.state() == QWebEnginePermission.State.Granted:
                continue
            yield tab

    def sleep_idle_tabs(self, force=False):
        """Discard background pages nobody has looked at for a while, freeing their memory.
        They reload when clicked."""
        minutes = self.store.settings.get("sleep_after_minutes", DEFAULT_SLEEP_MINUTES)
        if not minutes and not force:
            return 0
        if self.saving_minutes:  # (Low Power Mode: sooner. power_saver.py)
            minutes = min(minutes, self.saving_minutes)
        cutoff = time.time() - minutes * 60
        slept = 0
        for tab in list(self.resting_tabs()):
            if force:
                self.put_to_sleep(tab)  # (you asked: Put Other Tabs to Sleep)
                slept += 1
            elif tab.last_active < cutoff:
                self.sleep_unless_typed_in(tab)
                slept += 1
        return slept

    def sleep_unless_typed_in(self, tab):
        """Put a background tab to sleep by itself (the idle timer, Memory Saver), unless something's been typed
        into it and not sent: asleep, it reloads, and a comment you were half way through went with it."""
        from .window import TYPED_IN  # (window imports this module)
        view = tab.view
        page = view.page()
        if page.lifecycleState() == QWebEnginePage.LifecycleState.Frozen:
            self.put_to_sleep(tab)  # (frozen by Low Power Mode, it can't answer: as before)
            return

        def answered(typed):
            if typed is True or sip.isdeleted(view) or tab.view is not view:
                return
            if page.lifecycleState() != QWebEnginePage.LifecycleState.Discarded and not view.isVisible():
                self.put_to_sleep(tab)
        page.runJavaScript(TYPED_IN, QWebEngineScript.ScriptWorldId.ApplicationWorld, answered)

    def put_to_sleep(self, tab):
        """Let a background page go, freeing its memory; it reloads, where you'd scrolled to, when you click it."""
        self._note_scroll(tab)
        tab.view.page().setLifecycleState(QWebEnginePage.LifecycleState.Discarded)

    def sleep_other_tabs(self):
        slept = self.sleep_idle_tabs(force=True)
        self.toast(f"Put {slept} tab{'s' if slept != 1 else ''} to sleep" if slept else "No other tabs are awake")
