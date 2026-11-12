# Handoff: the page you're looking at in Eclipse shows on your iPhone and iPad (in the app switcher, or the Dock),
# to carry on reading there, as Safari's and Chrome's pages do. The work is macOS's (macos.handoff). Not a private
# window's pages (they have no tab here), nor Eclipse's own pages; nothing while the window's closed.
#
# Settings → General → "Offer pages to your iPhone and iPad (Handoff)" turns it off.

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

from . import macos
from .features import Feature

SETTING = "handoff"



def offerable(url: str) -> bool:
    """A page your other devices could open: on the web, not this Mac's own servers or your network's (localhost:5173,
    a router, a .test name), which they can't reach, and whose addresses (with their tokens) needn't go to iCloud;
    nor one with a name and password in its address."""
    from PyQt6.QtCore import QUrl
    from .favicons import is_private_host
    q = QUrl(url)
    if q.scheme() not in ("http", "https") or q.userInfo():
        return False
    host = q.host().lower()
    return bool(host) and "." in host and not host.endswith((".test", ".local", ".localhost", ".internal", ".lan",
                                                            ".home.arpa")) and not is_private_host(host)

class HandoffFeature(Feature):
    name = "handoff"

    def __init__(self, window):
        super().__init__(window)
        QApplication.instance().applicationStateChanged.connect(self._app_state)

    def on(self) -> bool:
        return self.window.store.settings.get(SETTING, True)

    def offer(self, tab=None):
        """Offer the current tab's page (or stop, if there's nothing to offer)."""
        tab = tab if tab is not None else self.window.current_tab
        if not self.on() or tab is None or self.window.isHidden() or not offerable(tab.url):
            macos.end_handoff()
            return
        macos.handoff(tab.url, tab.label)

    def tab_shown(self, tab):
        self.offer(tab) if tab is not None else macos.end_handoff()

    def url_changed(self, tab, url):
        if tab is self.window.current_tab:
            self.offer(tab)

    def load_finished(self, tab, ok):
        if tab is self.window.current_tab:
            self.offer(tab)  # (now with its title)

    def put_away(self):
        macos.end_handoff()

    def _app_state(self, state):
        if state == Qt.ApplicationState.ApplicationActive:
            self.offer()

    def shutdown(self):
        macos.end_handoff()


FEATURE = HandoffFeature
