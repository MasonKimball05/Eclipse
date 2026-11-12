# Pages first, for most of the window's shortcuts: as in Chrome, a page can take a key it uses itself (⌘S in Google
# Docs saves, ⌘K in Slack or Notion searches, ⌘F in a web code editor finds) and the window's shortcut runs only
# when the page lets the key go by. The browser's own keys (new and closed tabs, switching tabs and Spaces, reload,
# back and forward, the address) are always the window's, so a page can't keep you in it.
#
# How: Qt asks the focused widget whether it wants a key before running a shortcut (ShortcutOverride). For a web
# view's page, this says yes, so the key goes to the page. A key the page doesn't use (no preventDefault) comes back
# from Qt WebEngine to the view's parent; then the window's shortcut runs, as if the page had never been asked.

import time

from PyQt6 import sip
from PyQt6.QtCore import QEvent, QObject
from PyQt6.QtGui import QKeySequence
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWidgets import QApplication

# The window's, whatever the page wants (by the menu item, so it holds when you change the item's shortcut)
BROWSER_KEEPS = {"New Tab", "New Little Window", "New Private Window", "Open Location…", "Reopen Closed Tab",
                 "Close Tab", "Next Tab", "Previous Tab", "Last Tab", "Reload", "Hard Reload", "Back", "Forward",
                 "Next Space", "Previous Space", "Settings…", "Minimize", "Toggle Full Screen",
                 "Go to Prompt on Page",
                 *(f"Tab {n}" for n in range(1, 9)), *(f"Space {n}" for n in range(1, 10))}


PASSED_S = 3.0  # (a key the page hasn't let go by in this long, it took)
MAX_PASSED = 16


def page_view(widget):
    """The web view `widget` draws the page of (its focus proxy), or None."""
    parent = widget.parent() if widget is not None else None
    return parent if isinstance(parent, QWebEngineView) and parent.focusProxy() is widget else None


class PageKeys(QObject):
    """Installed on the application, for `window`'s pages and shortcuts."""

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self._passed = []  # (keys pages were given first, oldest first: each its view and the menu item it would run)

    def action_for(self, event):
        """The window's menu item `event`'s key would run, if a page should have the key first; else None."""
        keys = QKeySequence(event.keyCombination())
        for action in self.window.actions():
            if action.text() in BROWSER_KEEPS or not action.isEnabled():
                continue
            if any(shortcut == keys for shortcut in action.shortcuts()):
                return action
        return None

    def eventFilter(self, obj, event):
        kind = event.type()
        if kind == QEvent.Type.ShortcutOverride:
            view = page_view(obj)
            if view is not None and view.window() is self.window:  # (held down too: each repeat the page's first)
                action = self.action_for(event)
                if action is not None:
                    now = time.monotonic()
                    # (the ones a page took never come back: forgotten after a while)
                    self._passed = [p for p in self._passed if now - p[3] < PASSED_S][-MAX_PASSED:]
                    self._passed.append((event.keyCombination(), view, action, now))
                    event.accept()  # (the page has it first)
                    return True
        elif kind == QEvent.Type.KeyPress and self._passed and not event.spontaneous():
            for i, (keys, view, action, _at) in enumerate(self._passed):
                if event.keyCombination() == keys and not sip.isdeleted(view) and obj is view.parentWidget():
                    # The page let it go by: the window's, after all (the oldest of that key: pressed twice, twice)
                    del self._passed[i]
                    event.accept()
                    if not sip.isdeleted(action):
                        action.trigger()
                    return True
        return False


def install(window):
    keys = PageKeys(window)
    QApplication.instance().installEventFilter(keys)
    return keys
