# "Pages you've read": search the text of pages you've visited, not just their titles.
#
# A few seconds after a page finishes loading in a tab, its readable text (up to 30,000 characters)
# goes into a SQLite full-text index in your data folder. Type in ⌘T and matching pages show up
# with the words around your match, even when the title doesn't mention them ("that article about
# sourdough hydration"). Everything stays on this Mac.
#
# Not indexed: private windows, pages with a password field, non-web pages, and sites you've told it
# to skip. Banks, health records and mail are skipped too, from the start (DEFAULT_SKIP: Settings →
# Privacy → Excluded Sites lists them, and any can be let back in). Clearing history clears the index;
# Settings → Privacy can turn it off. It keeps the 5,000 most recently read pages, each with when you
# last read it.
#
# With Claude, ⌘T can also answer a question from them: "Ask Claude About Pages You've Read" finds the
# best few, gives Claude the parts around your words, and links its [1], [2] citations to the pages.
# Only those pages are linked: a link the answer makes up itself (the pages' text could ask for one)
# keeps its words, not its address.

import queue
import re
import sqlite3
import threading
import time

from PyQt6.QtCore import QObject, QTimer, QUrl
from PyQt6.QtWebEngineCore import QWebEngineScript

from . import storage
from .models import display_host
from .timefmt import portable

WORLD = QWebEngineScript.ScriptWorldId.ApplicationWorld
SETTING = "page_index"
SKIP_SETTING = "page_index_skip"
UNSKIP_SETTING = "page_index_unskipped"  # (defaults you've let back in)
PASSAGE_CHARS = 2_500  # of each page, around your words, for Claude
ASK_PAGES = 5
# Kept out from the start: what a bank, a health record or your mail says is nobody's search result. A site and its
# subdomains; "mail.*" is any site's mail. (Hand-kept, so not every bank: Settings → Privacy adds more.)
DEFAULT_SKIP = [
    # Mail
    "mail.*", "webmail.*", "outlook.live.com", "outlook.office.com", "outlook.office365.com", "proton.me",
    "fastmail.com", "mail.yahoo.com", "icloud.com", "hey.com",
    # Banks, cards, payments and investing
    "onlinebanking.*", "netbanking.*", "chase.com", "bankofamerica.com", "wellsfargo.com", "citi.com",
    "capitalone.com", "usbank.com", "pnc.com", "truist.com", "ally.com", "sofi.com", "discover.com",
    "americanexpress.com", "schwab.com", "fidelity.com", "vanguard.com", "robinhood.com", "etrade.com",
    "paypal.com", "venmo.com", "wise.com", "revolut.com", "monzo.com", "chime.com", "mint.intuit.com",
    "barclays.co.uk", "hsbc.com", "hsbc.co.uk", "lloydsbank.co.uk", "natwest.com", "santander.co.uk", "td.com",
    "rbcroyalbank.com", "scotiabank.com", "commbank.com.au",
    # Health
    "mychart.*", "kp.org", "myhealth.va.gov", "healthcare.gov", "zocdoc.com", "onemedical.com", "teladoc.com",
    "followmyhealth.com", "cvs.com", "walgreens.com", "goodrx.com", "23andme.com", "ancestry.com", "nhs.uk",
]
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


def matches_site(host: str, entry: str) -> bool:
    """A host is one of the list's: the site or one of its subdomains, or for "mail.*", any site's mail."""
    host = host.lower().removeprefix("www.")
    if entry.endswith(".*"):
        return host.startswith(entry[:-1])
    return host == entry or host.endswith("." + entry)


def indexable(url: str, skip: list[str]) -> bool:
    q = QUrl(url)
    if q.scheme() not in ("http", "https"):
        return False
    return not any(matches_site(q.host(), s) for s in skip)


def skipped_sites(settings) -> list[str]:
    """Every site kept out: the defaults you haven't let back in, then your own."""
    back = set(settings.get(UNSKIP_SETTING, []))
    return [s for s in DEFAULT_SKIP if s not in back] + [s for s in settings.get(SKIP_SETTING, []) if s not in back]


def excluded(settings) -> list[tuple[str, bool]]:
    """For Settings: (site, whether it's one of the defaults), yours first."""
    back = set(settings.get(UNSKIP_SETTING, []))
    return ([(s, False) for s in settings.get(SKIP_SETTING, []) if s not in DEFAULT_SKIP]
            + [(s, True) for s in DEFAULT_SKIP if s not in back])


def unskip(settings, entry: str):
    """Let a site's pages back in (yours comes off your list; a default is remembered as let back in)."""
    if entry in settings.get(SKIP_SETTING, []):
        settings[SKIP_SETTING].remove(entry)
    if entry in DEFAULT_SKIP and entry not in settings.setdefault(UNSKIP_SETTING, []):
        settings[UNSKIP_SETTING].append(entry)


def read_on(visited, now=None) -> str:
    """"Read today", "Read yesterday", "Read Oct 3" (with the year when it isn't this one); "" if unknown."""
    if not isinstance(visited, (int, float)):
        return ""
    now = time.time() if now is None else now
    day, today = time.localtime(visited), time.localtime(now)
    if day[:3] == today[:3]:
        return "Read today"
    if day[:3] == time.localtime(now - 86_400)[:3]:
        return "Read yesterday"
    return "Read " + time.strftime(portable("%b %-d" if day.tm_year == today.tm_year else "%b %-d, %Y"), day)


# --- Asking about them -----------------------------------------------------------------------------------

LITTLE_WORDS = {"a", "an", "the", "and", "or", "but", "of", "to", "in", "on", "at", "for", "with", "from", "by",
                "about", "as", "is", "are", "was", "were", "be", "been", "it", "its", "that", "this", "these", "those",
                "what", "which", "who", "whom", "why", "how", "when", "where", "did", "do", "does", "i", "me", "my",
                "you", "your", "we", "our", "they", "their", "he", "she", "his", "her", "read", "say", "said", "says",
                "tell", "can", "could", "should", "would", "will", "any", "some", "there", "page", "pages", "article"}


def question_words(text: str) -> list[str]:
    """The words worth looking for in a question (not "what did I read about")."""
    words = [w.strip("'") for w in re.findall(r"[\w']+", text.lower())]
    return list(dict.fromkeys(w for w in words if w and w not in LITTLE_WORDS and len(w) > 1))


def excerpt(body: str, words: list[str], size: int = PASSAGE_CHARS) -> str:
    """Up to `size` characters of a page: the parts around the first place each word appears (the start, if
    none do)."""
    body = " ".join(body.split())
    lower = body.lower()
    hits = sorted(p for p in (lower.find(w) for w in words) if p >= 0)
    if not hits:
        return body[:size]
    share = size // len(hits)
    spans = []
    for at in hits:
        start = max(0, at - share // 3)
        end = min(len(body), start + share)
        if spans and start <= spans[-1][1]:
            spans[-1] = (spans[-1][0], max(spans[-1][1], end))
        else:
            spans.append((start, end))
    text = " … ".join(body[a:b] for a, b in spans)
    return text[:size]


def _as_text(text: str) -> str:
    """Page text inside a <page> block: it can't end the block and start talking as us."""
    return re.sub(r"</?\s*page\s*>", "[page]", text, flags=re.IGNORECASE)


def ask_prompt(question: str, passages: list[dict]) -> str:
    blocks = []
    for n, p in enumerate(passages, 1):
        when = read_on(p.get("visited"))
        blocks.append(f"<page>\n[{n}] Title: {_as_text(p['title'] or '')}\nURL: {p['url']}\n"
                      f"{when}\n\n{_as_text(p['text'])}\n</page>")
    return ("\n\n".join(blocks) + "\n\nThose are parts of pages I've read in my browser, numbered. Answer the "
            "question below from them, in under 200 words, using Markdown where it helps. After each thing you "
            "take from a page, cite it by its number in square brackets, like [2]. Don't write links or "
            "addresses yourself. If the pages don't answer it, say so in a sentence.\n\nQuestion: " + question)


def _md_url(url: str) -> str:
    return url.replace(" ", "%20").replace("(", "%28").replace(")", "%29")


def cite(answer: str, passages: list[dict]) -> str:
    """The answer with its [n] citations linked to those pages, any other link reduced to its words, and the
    pages listed under it."""
    sources = [p["url"] for p in passages]
    answer = re.sub(r"\[([^\]]*)\]\(([^)]*)\)",
                    lambda m: m.group(0) if m.group(2) in sources else m.group(1), answer)

    def link(m):
        n = int(m.group(1))
        return f"[{n}]({_md_url(sources[n - 1])})" if 1 <= n <= len(sources) else m.group(0)
    answer = re.sub(r"\[(\d{1,2})\](?!\()", link, answer)
    listed = []
    for n, p in enumerate(passages, 1):
        title = " ".join(re.sub(r"[\[\]]", "", p["title"] or display_host(p["url"])).split())
        when = read_on(p.get("visited"))
        listed.append(f"{n}. [{title}]({_md_url(p['url'])})" + (f" · {when}" if when else ""))
    return answer + "\n\n**Sources**\n\n" + "\n".join(listed)


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
        # Pages kept from sites that are skipped now (the defaults, new to an index from before them): gone
        self._writes.put(("forget_sites", skipped_sites(self.settings)))

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
        if not ok or not self.enabled or not indexable(tab.url, skipped_sites(self.settings)):
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
                elif job[0] == "forget_sites":
                    sites = job[1]
                    with connection:
                        rows = connection.execute("SELECT rowid, url FROM pages").fetchall()
                        gone = [r for r, u in rows if any(matches_site(QUrl(u).host(), s) for s in sites)]
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
        self._writes.put(("forget_sites", [host]))

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
                "SELECT url, title, snippet(pages, 2, '', '', '…', 14), visited FROM pages WHERE pages MATCH ? "
                "ORDER BY bm25(pages, 0, 4.0, 1.0) LIMIT ?", (query, limit)).fetchall()
        except sqlite3.Error:
            return []
        return [{"url": u, "title": t, "snippet": " ".join(s.split()), "host": display_host(u),
                 "visited": v if isinstance(v, (int, float)) else None} for u, t, s, v in rows]

    def passages(self, question, limit=ASK_PAGES) -> list[dict]:
        """The pages that best answer a question, [{url, title, visited, text}], `text` being the parts around its
        words. Any of the words will do (a question's every word is on no page), the pages with more of them, and
        rarer ones, first."""
        words = question_words(question)
        if not words or not self.enabled:
            return []
        try:
            if self._read is None:
                self._read = self._connect()
            rows = self._read.execute(
                "SELECT url, title, body, visited FROM pages WHERE pages MATCH ? ORDER BY bm25(pages, 0, 4.0, 1.0) "
                "LIMIT ?", (" OR ".join(f'"{w}"' for w in words[:12]), limit)).fetchall()
        except sqlite3.Error:
            return []
        return [{"url": u, "title": t, "visited": v if isinstance(v, (int, float)) else None,
                 "text": excerpt(b or "", words)} for u, t, b, v in rows]

    def count(self) -> int:
        try:
            if self._read is None:
                self._read = self._connect()
            return self._read.execute("SELECT count(*) FROM pages").fetchone()[0]
        except sqlite3.Error:
            return 0


# --- Settings → Privacy → Excluded Sites… -------------------------------------------------------------------

def excluded_sites_dialog(parent, browser):
    """The sites whose pages are never kept: yours (from the shield menu, or added here) and the built-in banks,
    health and mail, each removable."""
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import (QDialog, QDialogButtonBox, QHBoxLayout, QInputDialog, QLabel, QListWidget,
                                 QListWidgetItem, QPushButton, QVBoxLayout)
    index, settings = browser.page_index, browser.store.settings
    dialog = QDialog(parent)
    dialog.setWindowTitle("Excluded Sites")
    v = QVBoxLayout(dialog)
    v.setContentsMargins(20, 18, 20, 16)
    about = QLabel("Pages on these sites are never kept for searching or asking about. Banks, health records and "
                   "mail are on the list from the start; remove one to let its pages in.")
    about.setWordWrap(True)
    about.setObjectName("Hint")
    about.setFixedWidth(420)
    v.addWidget(about)
    sites = QListWidget()
    sites.setMinimumHeight(280)
    v.addWidget(sites)

    def fill():
        sites.clear()
        for site, built_in in excluded(settings):
            label = "Any site's " + site[:-2] + "." if site.endswith(".*") else site
            item = QListWidgetItem(label + ("    (built in)" if built_in else ""))
            item.setData(Qt.ItemDataRole.UserRole, site)
            sites.addItem(item)

    def remove():
        for item in sites.selectedItems():
            unskip(settings, item.data(Qt.ItemDataRole.UserRole))
        browser.store.schedule_save()
        fill()

    def add():
        host, ok = QInputDialog.getText(dialog, "Exclude a Site", "Site (example.com; its subdomains too):")
        host = QUrl.fromUserInput(host.strip()).host().lower().removeprefix("www.") if ok and host.strip() else ""
        if host:
            back = settings.get(UNSKIP_SETTING, [])
            if host in back:
                back.remove(host)  # (a default you'd let back in, kept out again)
            index.skip_site(host)
            fill()

    row = QHBoxLayout()
    add_button, remove_button = QPushButton("Add…"), QPushButton("Remove")
    add_button.clicked.connect(add)
    remove_button.clicked.connect(remove)
    row.addWidget(add_button)
    row.addWidget(remove_button)
    row.addStretch(1)
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
    buttons.rejected.connect(dialog.reject)
    row.addWidget(buttons)
    v.addLayout(row)
    fill()
    dialog.exec()
