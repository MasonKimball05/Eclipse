# The Library (⌘Y), drawn by SwiftUI (native/Sources/Library.swift) on a Mac where the native pages are built.
# LibraryWindow (library.py) is still made, never shown: it reads the entries and does what's asked (open, remove,
# clear); this sends the section's entries over and passes on what's done in the native window.

import json

from PyQt6 import sip
from PyQt6.QtCore import QObject, QTimer

from . import native
from .library import SECTIONS, SHOW_AT_MOST, LibraryWindow, day_label, when

POLL_MS = 2000  # (while it's open: a page you visit meanwhile shows up)
SYMBOLS = {"history": "clock.arrow.circlepath", "archive": "archivebox", "notes": "note.text", "answers": "safari"}


class NativeLibrary(QObject):
    def __init__(self, browser):
        super().__init__(browser)
        self.browser = browser
        self.qt = LibraryWindow(browser)  # (never shown: the entries, and what's done to them)
        self.page = None
        self.query = ""
        self._entries: list[dict] = []
        self._icons: dict[str, str] = {}  # (each site's icon as a picture, made once)
        self._sent = ""
        self._poll = QTimer(self, interval=POLL_MS, timeout=self.refresh)

    def open(self, section="history"):
        self.qt.section = section
        if self.page is not None and self.page.is_open():
            self.page.update({"section": section, **self.describe()})
            self.page.front()
            return
        state = self.describe()
        self._sent = json.dumps(state)
        self.page = native.NativePage({
            "page": "library", "title": "Library", "section": section, "show_title": True, "width": 940, "height": 680,
            "min_width": 640, "min_height": 400, "sections": [{"key": k, "title": t, "symbol": SYMBOLS[k]}
                                                              for k, _, t in SECTIONS],
            **state, **native.theme_state(self.browser.theme)}, self)
        self.qt.native_page = self.page  # (its questions: sheets on the native window, prompts.py)
        self.page.action.connect(self._act)
        self.page.closed.connect(self._closed)
        self._poll.start()

    def _closed(self):
        self._poll.stop()
        self.page = None

    def isVisible(self) -> bool:
        return self.page is not None and self.page.is_open()

    def close(self):
        if self.page is not None:
            self.page.close()

    def apply_theme(self):
        self.qt.apply_theme()
        if self.page is not None:
            self.page.update(native.theme_state(self.browser.theme))

    def describe(self) -> dict:
        qt = self.qt
        self._entries = qt.matching(self.query)[:SHOW_AT_MOST]
        entries, icons = [], {}
        for i, entry in enumerate(self._entries):
            key = self._icon_key(entry["url"])
            if key not in self._icons:
                self._icons[key] = native.png(qt._icon(entry["url"]).pixmap(32, 32))
            icons[key] = self._icons[key]
            entries.append({"id": i, "title": entry["title"], "subtitle": entry.get("subtitle", ""),
                            "url": entry["url"], "icon": key, "when": when(entry["ts"]) if entry["ts"] else "",
                            "day": day_label(entry["ts"]) if entry["ts"] else "Earlier",
                            "web": entry["url"].startswith("http"), "note": entry["url"].startswith("note:"),
                            "file": "path" in entry})
        total = len(qt.matching(""))
        return {"entries": entries, "icons": icons, "empty": qt.empty_text(self.query), "query": self.query,
                "clear": {"history": "Clear History…", "archive": "Empty Archive…"}.get(qt.section, ""),
                "can_clear": total > 0, "count": total, "grouped": qt.section in ("history", "archive")}

    def _icon_key(self, url: str) -> str:
        from .models import display_host
        if url.startswith("note:"):
            return "note"
        if url.startswith("file:"):
            return "answer"
        return display_host(url) or url[:40]

    def refresh(self):
        if self.page is None or sip.isdeleted(self.qt):
            return
        state = self.describe()
        text = json.dumps(state)
        if text != self._sent:
            self._sent = text
            self.page.update(state)

    def _act(self, name, event):
        if name == "section" and event.get("key") in [k for k, _, _ in SECTIONS]:
            self.qt.section = event["key"]
            self.query = ""
        elif name == "search":
            self.query = str(event.get("value", ""))
        elif name == "clear":
            self._place_dialogs()
            self.qt._clear_all()
        elif name in ("open", "little", "copy", "remove") and isinstance(event.get("id"), int) \
                and 0 <= event["id"] < len(self._entries):
            entry = self._entries[event["id"]]
            if name == "copy":
                self.qt.copy_link(entry)
                return
            if name == "remove":
                self._place_dialogs()
                self.qt._remove(entry)
            else:
                self.qt.open_entry(entry, little=name == "little")
        elif name != "focus":
            return
        self.refresh()

    def _place_dialogs(self):
        if (frame := self.page.frame()) is not None:
            self.qt.setGeometry(frame)  # (its question opens over the native window)


def window_for(browser):
    """The Library, natively where that's built, else Qt's window."""
    return NativeLibrary(browser) if native.available() else LibraryWindow(browser)
