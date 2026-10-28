# Watch This Page: tell me when this page (or this part of it) changes. A price, a "sold out", a list of
# openings, a changelog.
#
# A tab's menu has "Watch for Changes…" (the page's main text), and selecting text on a page and right-clicking
# has "Watch This" (just that part: kept as a CSS selector for its element and the text it had, so it's found
# again if the page moves things about). Every half hour by default (a quarter of an hour to a day, per watch),
# each watched page is loaded in a hidden page with your usual logins, one at a time, and read in a world of its
# own. Errors back off: twice as long after each, up to a day, until one works.
#
# What counts as a change is the text with the noise taken out (whitespace, dates and times, "3 minutes ago",
# counters like "1,204 views"), so a page that only says it's later doesn't fire. A real change keeps the before
# and after, and you're told the way a site's notification tells you (notify.py: a banner, the Dock bouncing,
# and a dot on the site's tab in the sidebar until you look): in one line from Claude if it's here ("Price
# dropped to $249"), or else a plain summary of what changed.
#
# Watches are kept in the settings ("page_watches"); Settings → Watched Pages pauses them, changes how often,
# shows the last change, or removes them.

import difflib
import json
import re
import secrets
import time

from PyQt6.QtCore import QObject, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QImage
from PyQt6.QtWebEngineCore import QWebEnginePage, QWebEngineScript

from .features import Feature
from .models import display_host
from .timefmt import portable

SETTING = "page_watches"
INTERVALS = [15, 30, 60, 180, 360, 720, 1440]  # minutes: what Settings and the menu offer
INTERVAL_NAMES = {15: "15 minutes", 30: "30 minutes", 60: "hour", 180: "3 hours", 360: "6 hours", 720: "12 hours",
                  1440: "day"}
DEFAULT_INTERVAL = 30
MAX_DELAY = 24 * 3600
MAX_TEXT = 50_000
TICK_MS = 60_000           # how often it looks for a watch that's due
LOAD_TIMEOUT_MS = 45_000
SETTLE_MS = 3_000          # after loading, for apps that draw their page with scripts
WORLD = QWebEngineScript.ScriptWorldId.ApplicationWorld

# --- What counts as a change ----------------------------------------------------------------------------------

_MONTH = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?"
NOISE = [re.compile(p, re.IGNORECASE) for p in (
    r"\b(?:\d+|an?|one|a few)\s+(?:second|sec|minute|min|hour|hr|day|week|month|year)s?\s+ago\b",
    r"\b(?:just now|moments? ago|yesterday|today)\b",
    r"\b\d{4}-\d{2}-\d{2}(?:[t ][\d:.]+(?:z|[+-]\d{2}:?\d{2})?)?\b",
    r"\b\d{1,2}[/.]\d{1,2}[/.]\d{2,4}\b",
    rf"\b{_MONTH}\s+\d{{1,2}}(?:st|nd|rd|th)?(?:,?\s+\d{{4}})?\b",
    rf"\b\d{{1,2}}(?:st|nd|rd|th)?\s+{_MONTH}(?:,?\s+\d{{4}})?\b",
    r"\b\d{1,2}:\d{2}(?::\d{2})?\s*(?:[ap]\.?m\.?)?",
    r"\b\d[\d,.]*\s*[km]?\s+(?:views?|likes?|comments?|replies|reply|shares?|followers?|watching|online|visitors?|"
    r"reactions?|retweets?|reposts?|upvotes?|points?|stars?|downloads?|plays?|listens?)\b",
)]


def normalise(text: str) -> str:
    """The text with what changes by itself taken out: dates, times, "3 minutes ago", view counts, whitespace."""
    text = text or ""
    for pattern in NOISE:
        text = pattern.sub("#", text)
    return " ".join(text.split())


def changed(before: str, after: str) -> bool:
    return normalise(before) != normalise(after)


def _quote(text: str, limit=60) -> str:
    text = " ".join(text.split())
    return "“" + (text if len(text) <= limit else text[:limit - 1].rstrip() + "…") + "”"


def diff_summary(before: str, after: str) -> str:
    """What changed, in a line: "“$299” → “$249”", "Added: “…”", or how many lines came and went."""
    old = [line for line in (before or "").splitlines() if line.strip()]
    new = [line for line in (after or "").splitlines() if line.strip()]
    matcher = difflib.SequenceMatcher(None, [normalise(x) for x in old], [normalise(x) for x in new], autojunk=False)
    removed, added = [], []
    for op, i1, i2, j1, j2 in matcher.get_opcodes():
        if op != "equal":
            removed += old[i1:i2]
            added += new[j1:j2]
    if len(removed) == 1 and len(added) == 1:  # one line changed: just the words that did
        a, b = removed[0].split(), added[0].split()
        ops = [o for o in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes() if o[0] != "equal"]
        if ops:
            was, now = " ".join(a[ops[0][1]:ops[-1][2]]), " ".join(b[ops[0][3]:ops[-1][4]])
            if was and now:
                return f"{_quote(was)} → {_quote(now)}"
            return f"Added: {_quote(now)}" if now else f"Removed: {_quote(was)}"
    if added and not removed:
        return f"Added: {_quote(added[0])}" + (f" and {len(added) - 1} more" if len(added) > 1 else "")
    if removed and not added:
        return f"Removed: {_quote(removed[0])}" + (f" and {len(removed) - 1} more" if len(removed) > 1 else "")
    lines = lambda n: f"{n} line{'s' if n != 1 else ''}"
    return f"{lines(len(added))} added, {len(removed)} removed: {_quote(added[0]) if added else ''}".rstrip(": ")


def next_delay(interval: int, failures: int) -> int:
    """Seconds until the next look: the watch's interval, doubled after each error in a row, a day at most."""
    return min(interval * 60 * 2 ** min(failures, 10), max(MAX_DELAY, interval * 60))


# --- What's kept ----------------------------------------------------------------------------------------------

def _text(value, limit):
    return value[:limit] if isinstance(value, str) else None


def _number(value, default):
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else default


def clean_watch(data) -> dict | None:
    """A watch as kept (from the settings, which a backup or an older version could have written oddly), or None if it
    isn't one: only web pages are ever watched."""
    if not isinstance(data, dict) or not isinstance(data.get("url"), str):
        return None
    if QUrl(data["url"]).scheme() not in ("http", "https"):
        return None
    interval = int(_number(data.get("interval"), DEFAULT_INTERVAL))
    return {
        "id": _text(data.get("id"), 40) or secrets.token_hex(6),
        "url": data["url"][:4096],
        "title": _text(data.get("title"), 200) or display_host(data["url"]),
        "selector": _text(data.get("selector"), 1000),
        "text": _text(data.get("text"), MAX_TEXT),
        "interval": min(max(interval, INTERVALS[0]), INTERVALS[-1]),
        "paused": data.get("paused") is True,
        "failures": max(int(_number(data.get("failures"), 0)), 0),
        "error": _text(data.get("error"), 300) or "",
        "due": _number(data.get("due"), 0.0),
        "checked": _number(data.get("checked"), 0.0),
        "changed": _number(data.get("changed"), None),
        "before": _text(data.get("before"), MAX_TEXT) or "",
        "after": _text(data.get("after"), MAX_TEXT) or "",
        "summary": _text(data.get("summary"), 200) or "",
    }


# --- Reading a page ---------------------------------------------------------------------------------------------

READ = r"""
(() => {
  const SELECTOR = %(selector)s, WAS = %(was)s, MAX = %(max)d;
  const read = (el) => (el.innerText || el.textContent || '').slice(0, MAX);
  if (!document.body) return {error: "The page didn't load"};
  if (SELECTOR) {
    let el = null;
    try { el = document.querySelector(SELECTOR); } catch (e) {}
    if (!el && WAS) {  // moved about: the deepest element that still starts the way it did
      const start = WAS.trim().slice(0, 40);
      const all = [...document.body.querySelectorAll('*')].filter((e) => (e.innerText || '').includes(start));
      el = all.length ? all[all.length - 1] : null;
    }
    return el ? {text: read(el)} : {error: "The part of the page you're watching isn't there any more"};
  }
  const main = document.querySelector('main, article, [role=main]');
  return {text: read(main && (main.innerText || '').length > 500 ? main : document.body)};
})()
"""

# The element around what you selected, and a selector that finds it again: its own id if it has a unique one,
# or else the path down from the nearest ancestor that does (or the page), by tag and position
SELECTION = r"""
(() => {
  const selection = getSelection();
  if (!selection.rangeCount) return null;
  let el = selection.getRangeAt(0).commonAncestorContainer;
  if (el.nodeType !== 1) el = el.parentElement;
  if (!el || el === document.body || el === document.documentElement) return null;
  const unique = (id) => { try { return document.querySelectorAll('#' + CSS.escape(id)).length === 1; } catch (e) { return false; } };
  const parts = [];
  for (let node = el; node && node !== document.body && node.parentElement; node = node.parentElement) {
    if (node.id && unique(node.id)) { parts.unshift('#' + CSS.escape(node.id)); break; }
    const same = [...node.parentElement.children].filter((c) => c.tagName === node.tagName);
    const tag = node.tagName.toLowerCase();
    parts.unshift(same.length > 1 ? `${tag}:nth-of-type(${same.indexOf(node) + 1})` : tag);
  }
  return {selector: parts.join(' > '), text: (el.innerText || '').slice(0, %(max)d)};
})()
"""


def read_script(selector, was) -> str:
    return READ % {"selector": json.dumps(selector), "was": json.dumps((was or "")[:200] or None), "max": MAX_TEXT}


class _Notice(QObject):
    """A change, told the way a site's notification is (notify.NotificationCenter shows these)."""
    closed = pyqtSignal()

    def __init__(self, window, watch):
        super().__init__(window if isinstance(window, QObject) else None)
        self.window, self.watch = window, dict(watch)

    def title(self):
        return f"“{self.watch['title']}” changed"

    def message(self):
        return self.watch["summary"]

    def origin(self):
        return QUrl(self.watch["url"])

    def icon(self):
        return QImage()

    def show(self):
        pass

    def close(self):
        self.closed.emit()

    def click(self):
        """Back to the page: its tab if one's open on it, or a new one."""
        window = self.window
        tab = next((t for t in window.store.all_tabs() if t.url == self.watch["url"]), None)
        window.activate_tab(tab) if tab is not None else window.open_url(self.watch["url"])


class Watcher(QObject):
    """The watches, and looking at them when they're due: one page at a time, in the background."""

    def __init__(self, window, start=True):
        super().__init__(window if isinstance(window, QObject) else None)
        self.window = window
        self.busy = None   # the watch being looked at
        self.page = None
        self._timeout = None
        settings = window.store.settings
        kept = settings.get(SETTING)
        settings[SETTING] = [w for w in map(clean_watch, kept if isinstance(kept, list) else []) if w]
        if start:
            self._timer = QTimer(self, interval=TICK_MS, timeout=self.tick)
            self._timer.start()
            QTimer.singleShot(20_000, self.tick)  # (soon after opening: what came due while it was closed)

    @property
    def watches(self) -> list[dict]:
        return self.window.store.settings.setdefault(SETTING, [])

    def get(self, watch_id) -> dict | None:
        return next((w for w in self.watches if w["id"] == watch_id), None)

    def find(self, url, selector=None) -> dict | None:
        return next((w for w in self.watches if w["url"] == url and w["selector"] == selector), None)

    def _save(self):
        self.window.store.schedule_save()

    # --- Changing them ------------------------------------------------------------------------------------

    def add(self, url, title, selector=None, text=None, interval=DEFAULT_INTERVAL) -> dict | None:
        """Start watching (None if it's watched already, or isn't a web page). With `text` (a selection's), that's
        the baseline; otherwise the first look is."""
        if self.find(url, selector) is not None:
            return None
        watch = clean_watch({"url": url, "title": title, "selector": selector, "text": text, "interval": interval,
                             "due": time.time()})
        if watch is not None:
            self.watches.append(watch)
            self._save()
        return watch

    def remove(self, watch_id):
        self.watches[:] = [w for w in self.watches if w["id"] != watch_id]
        self._save()

    def set_paused(self, watch_id, paused):
        watch = self.get(watch_id)
        if watch is not None:
            watch["paused"] = paused
            watch["due"] = time.time() if not paused else watch["due"]  # (back on: a look soon)
            self._save()

    def set_interval(self, watch_id, minutes):
        watch = self.get(watch_id)
        if watch is not None:
            watch["interval"] = min(max(int(minutes), INTERVALS[0]), INTERVALS[-1])
            watch["due"] = (watch["checked"] or time.time()) + next_delay(watch["interval"], watch["failures"])
            self._save()

    # --- Looking -------------------------------------------------------------------------------------------

    def tick(self):
        if self.busy is not None:
            return
        now = time.time()
        due = sorted((w for w in self.watches if not w["paused"] and w["due"] <= now), key=lambda w: w["due"])
        if due:
            self.check(due[0])

    def check(self, watch):
        """Load the page out of sight (your default profile: its logins), let it settle, and read it."""
        self.busy = watch_id = watch["id"]
        page = self.page = QWebEnginePage(self.window.profile, self)
        page.setAudioMuted(True)
        self._timeout = QTimer(self, singleShot=True, interval=LOAD_TIMEOUT_MS,
                               timeout=lambda: self._result(watch_id, {"error": "It took too long to load"}))
        self._timeout.start()

        def loaded(ok):
            if self.page is not page:
                return
            if not ok:
                self._result(watch_id, {"error": "Couldn't load it"})
                return
            page.loadFinished.disconnect(loaded)
            QTimer.singleShot(SETTLE_MS, lambda: self.page is page and page.runJavaScript(
                read_script(watch["selector"], watch["text"]), WORLD, lambda data: self._result(watch_id, data)))
        page.loadFinished.connect(loaded)
        page.load(QUrl(watch["url"]))

    def _done_with_page(self):
        if self._timeout is not None:
            self._timeout.stop()
            self._timeout = None
        if self.page is not None:
            self.page.deleteLater()
            self.page = None
        self.busy = None

    def _result(self, watch_id, data):
        if self.busy != watch_id:
            return  # (an answer from a look that's already over: timed out, say, with the page still answering)
        self._done_with_page()
        watch = self.get(watch_id)
        if watch is None:
            return  # (removed while it was being looked at)
        now = time.time()
        text = data.get("text") if isinstance(data, dict) else None
        if not isinstance(text, str):
            watch["failures"] += 1
            error = data.get("error") if isinstance(data, dict) else None
            watch["error"] = str(error or "Couldn't read it")[:300]
            watch["due"] = now + next_delay(watch["interval"], watch["failures"])
            self._save()
            return
        text = text[:MAX_TEXT]
        old = watch["text"]
        watch.update(failures=0, error="", checked=now, due=now + next_delay(watch["interval"], 0), text=text)
        if old is not None and changed(old, text):
            watch.update(before=old, after=text, changed=now, summary=diff_summary(old, text))
            self._tell(watch)
        self._save()

    def _tell(self, watch):
        """Claude's line for it if Claude's here (the plain summary meanwhile, and if it can't), then the notice."""
        claude = self.window.feature("claude")
        if claude is None or not claude.available():
            self._notify(watch)
            return
        from .ai import page_block
        request = claude.claude.ask(
            page_block(watch["title"], watch["url"], "BEFORE:\n" + watch["before"][:20_000] + "\n\nAFTER:\n"
                       + watch["after"][:20_000]) +
            "\n\nThat's part of a web page before and after it changed. Say what changed in one short line, under 12 "
            "words, the way a person would tell a friend (for example: Price dropped to $249). Only the line.")
        watch_id = watch["id"]

        def said(line):
            kept = self.get(watch_id)
            lines = line.strip().splitlines()
            line = lines[0].strip(" \"'“”`*") if lines else ""
            if kept is not None and line:
                kept["summary"] = line[:200]
                self._save()
            self._notify(kept or watch)
        request.finished.connect(said)
        request.failed.connect(lambda _message: self._notify(self.get(watch_id) or watch))

    def _notify(self, watch):
        self.window.notifications.present(self.window.profile, _Notice(self.window, watch))

    def stop(self):
        self._done_with_page()


# --- The feature: its menus and Settings page ---------------------------------------------------------------------

def every(minutes) -> str:
    return f"Every {INTERVAL_NAMES.get(minutes, f'{minutes} minutes')}"


class PageWatchFeature(Feature):
    name = "page_watch"

    def __init__(self, window):
        super().__init__(window)
        self.watcher = Watcher(window)
        self._settings_page_fill = None

    def shutdown(self):
        self.watcher.stop()

    def _watchable(self, url) -> bool:
        return QUrl(url or "").scheme() in ("http", "https")

    def watch_tab(self, tab):
        """"Watch for Changes…": the page's main text, as often as you choose."""
        from PyQt6.QtWidgets import QInputDialog
        window = self.window
        if tab is None or not self._watchable(tab.url):
            window.toast("Only web pages can be watched")
            return
        names = [every(m) for m in INTERVALS]
        chosen, ok = QInputDialog.getItem(window, "Watch for Changes", f"Check {display_host(tab.url)} for changes:",
                                          names, INTERVALS.index(DEFAULT_INTERVAL), False)
        if not ok:
            return
        watch = self.watcher.add(tab.url, tab.label, interval=INTERVALS[names.index(chosen)])
        window.toast(f"Watching {display_host(tab.url)} · you'll hear when it changes" if watch is not None
                     else "You're already watching this page")
        self._refresh_settings()

    def watch_selection(self, view, tab):
        """"Watch This": the element around the selection, found again by its selector (or the text it had)."""
        window = self.window
        url = view.url().toString()

        def got(found):
            if not isinstance(found, dict) or not found.get("selector"):
                window.toast("Couldn't tell which part of the page that is: try Watch for Changes on the tab")
                return
            watch = self.watcher.add(url, (tab.label if tab is not None else view.title()) or display_host(url),
                                     found["selector"], found.get("text") or "")
            window.toast(f"Watching that part of {display_host(url)} · you'll hear when it changes"
                         if watch is not None else "You're already watching that")
            self._refresh_settings()
        view.page().runJavaScript(SELECTION % {"max": MAX_TEXT}, WORLD, got)

    def tab_menu(self, menu, tab, kind):
        if not self._watchable(tab.url):
            return
        watched = self.watcher.find(tab.url)
        if watched is not None:
            self.window._act(menu, "ph.bell-ringing", "Stop Watching for Changes",
                             lambda: (self.watcher.remove(watched["id"]), self._refresh_settings()))
        else:
            self.window._act(menu, "ph.bell-ringing", "Watch for Changes…", lambda: self.watch_tab(tab))

    def selection_menu(self, menu, view, text):
        from PyQt6.QtGui import QAction
        if not self._watchable(view.url().toString()):
            return []
        tab = next((t for t in self.window.store.all_tabs() if t.view is view), None)
        action = QAction("Watch This", menu)
        action.triggered.connect(lambda: self.watch_selection(view, tab))
        return [action]

    def commands(self):
        return [("Watch This Page for Changes", "ph.bell-ringing", lambda: self.watch_tab(self.window.current_tab))]

    # --- Settings → Watched Pages ---------------------------------------------------------------------------

    def settings_pages(self):
        return [("watched", "ph.bell-ringing", "Watched Pages", self._settings_page, None)]

    def settings_shown(self, settings):
        self._refresh_settings()

    def _refresh_settings(self):
        if self._settings_page_fill is not None:
            try:
                self._settings_page_fill()
            except RuntimeError:  # (Settings was closed: its widgets are gone)
                self._settings_page_fill = None

    def _settings_page(self, settings):
        from PyQt6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget
        from .settings import _combo
        watcher = self.watcher
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, 0, 0, 0)

        def detail(w):
            parts = [display_host(w["url"]), "Paused" if w["paused"] else every(w["interval"])]
            if w["error"]:
                parts.append(f"Last look: {w['error']}")
            if w["summary"]:
                when = time.strftime(portable("%b %-d"), time.localtime(w["changed"])) if w["changed"] else ""
                parts.append(f"Changed{' ' + when if when else ''}: {w['summary']}")
            return " · ".join(parts)

        def fill():
            while outer.count():
                item = outer.takeAt(0)
                if item.widget() is not None:
                    item.widget().hide()
                    item.widget().deleteLater()
            rows = []
            for w in list(watcher.watches):
                actions = QWidget()
                a = QHBoxLayout(actions)
                a.setContentsMargins(0, 0, 0, 0)
                often = _combo([(m, every(m)) for m in INTERVALS], w["interval"])
                often.currentIndexChanged.connect(lambda _=0, c=often, i=w["id"]: watcher.set_interval(i, c.currentData()))
                pause = QPushButton("Resume" if w["paused"] else "Pause")
                pause.clicked.connect(lambda _=False, x=w: (watcher.set_paused(x["id"], not x["paused"]), fill()))
                remove = QPushButton("Remove")
                remove.clicked.connect(lambda _=False, x=w: (watcher.remove(x["id"]), fill()))
                for widget in (often, pause):
                    a.addWidget(widget)
                if w["before"] or w["after"]:
                    changes = QPushButton("Changes…")
                    changes.clicked.connect(lambda _=False, x=w: show_changes(settings, x))
                    a.addWidget(changes)
                a.addWidget(remove)
                title = w["title"] + (" (a part of it)" if w["selector"] else "")
                rows.append(settings._row(title, detail(w), actions))
            if not rows:
                empty = QLabel("Nothing watched yet. Right-click a tab and choose Watch for Changes…, or select part "
                               "of a page, right-click it and choose Watch This.")
                empty.setObjectName("Hint")
                empty.setWordWrap(True)
                empty.setContentsMargins(0, 8, 0, 8)
                rows.append(empty)
            about = QLabel("Each page is checked in the background with your logins, one at a time. Dates, times, "
                           "\"3 minutes ago\" and view counts don't count as changes.")
            about.setObjectName("Hint")
            about.setWordWrap(True)
            outer.addWidget(settings._card(rows))
            outer.addWidget(about)
            outer.addStretch(1)
        self._settings_page_fill = fill
        fill()
        return page


def show_changes(parent, watch):
    """The last change: what was there and what is now, line by line (- before, + after)."""
    from PyQt6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QPlainTextEdit, QVBoxLayout
    dialog = QDialog(parent)
    dialog.setWindowTitle(f"Changes · {watch['title']}")
    v = QVBoxLayout(dialog)
    heading = QLabel(watch["summary"] or "What changed")
    heading.setWordWrap(True)
    v.addWidget(heading)
    lines = difflib.unified_diff((watch["before"] or "").splitlines(), (watch["after"] or "").splitlines(),
                                 "Before", "After", lineterm="", n=2)
    text = QPlainTextEdit("\n".join(list(lines)[2:][:2000]) or "(Only spacing changed.)")
    text.setReadOnly(True)
    text.setMinimumSize(560, 360)
    v.addWidget(text)
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
    buttons.rejected.connect(dialog.reject)
    v.addWidget(buttons)
    dialog.exec()


FEATURE = PageWatchFeature
