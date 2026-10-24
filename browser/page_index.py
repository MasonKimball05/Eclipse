# "Pages you've read": search the text of pages you've visited, not just their titles.
#
# A few seconds after a page finishes loading in a tab, its readable text (up to 30,000 characters)
# goes into a SQLite full-text index in your data folder. Type in ⌘T and matching pages show up
# with the words around your match, even when the title doesn't mention them ("that article about
# sourdough hydration"). Everything stays on this Mac.
#
# Not indexed: private windows, pages with a password field, non-web pages, and sites you've told it
# to skip. Clearing history clears the index; Settings → Privacy can turn it off. It keeps the 5,000
# most recently read pages.

import queue
import re
import sqlite3
import threading
import time

from PyQt6.QtCore import QObject, QTimer, QUrl
from PyQt6.QtWebEngineCore import QWebEngineScript

from . import storage
from .models import display_host

WORLD = QWebEngineScript.ScriptWorldId.ApplicationWorld
SETTING = "page_index"
SKIP_SETTING = "page_index_skip"
MAX_CHARS = 30_000
MAX_PAGES = 5_000
SETTLE_MS = 3_000

READ = r"""
(() => {
  if (document.querySelector('input[type=password]')) return null;  // a sign-in page: keep it out
  const clean = (root) => {
    if (!root) return '';
    const copy = root.cloneNode(true);
    copy.querySelectorAll('script, style, noscript, svg, nav, footer, iframe, form, [aria-hidden=true]').forEach((e) => e.remove());
    // textContent runs paragraphs together ("end.Next"): mark where each block ends
    copy.querySelectorAll('p, div, li, h1, h2, h3, h4, h5, h6, br, tr, td, th, dt, dd, blockquote, pre, section, article')
      .forEach((e) => e.append('\n'));
    return copy.textContent.replace(/[ \t]+/g, ' ').replace(/\s*\n\s*/g, '\n').trim();
  };
  const main = clean(document.querySelector('article, main, [role=main]'));
  return (main.length > 500 ? main : clean(document.body)).slice(0, %d);
})()
""" % MAX_CHARS

SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS pages USING fts5(url UNINDEXED, title, body, visited UNINDEXED,
                                                    tokenize = 'porter unicode61 remove_diacritics 2');
"""


def fts_query(text: str) -> str | None:
    """A safe FTS5 query from what you typed: every word must appear; the last one can be a prefix."""
    words = re.findall(r"[\w']+", text.lower())
    words = [w.strip("'") for w in words if w.strip("'")]
    if not words:
        return None
    parts = [f'"{w}"' for w in words[:-1]] + [f'"{words[-1]}"*' if len(words[-1]) >= 2 else f'"{words[-1]}"']
    return " ".join(parts)


def indexable(url: str, skip: list[str]) -> bool:
    q = QUrl(url)
    if q.scheme() not in ("http", "https"):
        return False
    host = q.host().lower().removeprefix("www.")
    return not any(host == s or host.endswith("." + s) for s in skip)


class PageIndex(QObject):
    def __init__(self, browser, path=None, parent=None):
        super().__init__(parent if parent is not None else browser)
        self.browser = browser
        self.path = path or storage.data_dir() / "pages.db"
        self._read = None  # the main thread's connection, for searching
        try:
            # Set up once, here, before the writer starts: two connections switching the journal mode
            # at the same moment would make one of them fail
            setup = sqlite3.connect(str(self.path), timeout=5)
            setup.execute("PRAGMA journal_mode=WAL")
            setup.executescript(SCHEMA)
            setup.close()
        except sqlite3.Error:
            pass
        self._writes = queue.Queue()
        self._writer = threading.Thread(target=self._write_loop, daemon=True)
        self._writer.start()

    @property
    def settings(self):
        return self.browser.store.settings

    @property
    def enabled(self) -> bool:
        return self.settings.get(SETTING, True)

    def _connect(self):
        return sqlite3.connect(str(self.path), timeout=5)

    # --- Reading pages ----------------------------------------------------------------------------

    def page_loaded(self, tab, ok):
        """A tab finished loading: read it once it has settled."""
        if not ok or not self.enabled or not indexable(tab.url, self.settings.get(SKIP_SETTING, [])):
            return
        url = tab.url
        QTimer.singleShot(SETTLE_MS, lambda: self._read_page(tab, url))

    def _read_page(self, tab, url):
        view = tab.view
        if not hasattr(view, "page") or view.url().toString() != url or view.page().profile().isOffTheRecord():
            return
        view.page().runJavaScript(READ, WORLD, lambda text, t=tab, u=url: self._got(t, u, text))

    def _got(self, tab, url, text):
        if isinstance(text, str) and len(text) > 200:
            self._writes.put(("add", url, tab.title or url, text))

    # --- Writing (on its own thread, so a big page never stalls the window) -------------------------

    def _write_loop(self):
        connection = None
        while True:
            job = self._writes.get()
            if job is None:
                break
            try:
                connection = connection or self._connect()
                if job[0] == "add":
                    _kind, url, title, text = job
                    with connection:
                        connection.execute("DELETE FROM pages WHERE url = ?", (url,))
                        connection.execute("INSERT INTO pages (url, title, body, visited) VALUES (?, ?, ?, ?)",
                                           (url, title, text, time.time()))
                        # Over the limit: drop the oldest (FTS5 tables don't do "NOT IN (… LIMIT n)" reliably)
                        if connection.execute("SELECT count(*) FROM pages").fetchone()[0] > MAX_PAGES:
                            cutoff = connection.execute("SELECT visited FROM pages ORDER BY visited DESC "
                                                        "LIMIT 1 OFFSET ?", (MAX_PAGES,)).fetchone()[0]
                            connection.execute("DELETE FROM pages WHERE visited <= ?", (cutoff,))
                elif job[0] == "forget_site":
                    host = job[1]
                    with connection:
                        rows = connection.execute("SELECT rowid, url FROM pages").fetchall()
                        gone = [r for r, u in rows if (QUrl(u).host().lower().removeprefix("www.") == host or
                                                       QUrl(u).host().lower().endswith("." + host))]
                        connection.executemany("DELETE FROM pages WHERE rowid = ?", [(r,) for r in gone])
                elif job[0] == "forget_url":
                    with connection:
                        connection.execute("DELETE FROM pages WHERE url = ?", (job[1],))
                elif job[0] == "clear":
                    with connection:
                        connection.execute("DELETE FROM pages")
                    connection.execute("VACUUM")
            except sqlite3.Error:
                pass  # a locked or damaged index loses a page, never the browser

    def clear(self):
        self._writes.put(("clear",))

    def forget_url(self, url):
        self._writes.put(("forget_url", url))

    def skip_site(self, host):
        host = host.lower().removeprefix("www.")
        skip = self.settings.setdefault(SKIP_SETTING, [])
        if host and host not in skip:
            skip.append(host)
            self.browser.store.schedule_save()
        self._writes.put(("forget_site", host))

    def shutdown(self):
        self._writes.put(None)
        self._writer.join(timeout=2)

    # --- Searching ----------------------------------------------------------------------------------

    def search(self, text, limit=4) -> list[dict]:
        """[{url, title, snippet, host}] best matches first; [] when off or nothing matches."""
        query = fts_query(text)
        if not query or not self.enabled or len(text.strip()) < 3:
            return []
        try:
            if self._read is None:
                self._read = self._connect()
            rows = self._read.execute(
                "SELECT url, title, snippet(pages, 2, '', '', '…', 14) FROM pages WHERE pages MATCH ? "
                "ORDER BY bm25(pages, 0, 4.0, 1.0) LIMIT ?", (query, limit)).fetchall()
        except sqlite3.Error:
            return []
        return [{"url": u, "title": t, "snippet": " ".join(s.split()), "host": display_host(u)} for u, t, s in rows]

    def count(self) -> int:
        try:
            if self._read is None:
                self._read = self._connect()
            return self._read.execute("SELECT count(*) FROM pages").fetchone()[0]
        except sqlite3.Error:
            return 0
