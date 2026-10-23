# Arc Max's "Browse for Me": instead of a page of links, you get one page with the answer.
#
#   1. Search DuckDuckGo (its plain-HTML results page) in a hidden page
#   2. Load the top few results, also hidden, and take their text
#   3. Claude writes one answer from them, citing each source as [1], [2], …
#
# The answer page is a local HTML file (data folder: browse/<id>.html), so it stays a
# normal tab you can reload, pin or reopen after a restart.

import html
import json
import re
import time
from urllib.parse import parse_qs, quote_plus, urlparse

from PyQt6.QtCore import QObject, QTimer, QUrl, pyqtSignal
from PyQt6.QtWebEngineCore import QWebEnginePage, QWebEngineScript

from . import storage
from .ai import page_block
from .models import display_host, new_id

SEARCH_URL = "https://html.duckduckgo.com/html/?q={}"
CANDIDATES = 6       # search results to try, in order...
MAX_SOURCES = 3      # ...stopping once this many had real text
MIN_TEXT = 600       # less than this is a paywall, a blocker page or a failed load
PAGE_TEXT_LIMIT = 12_000
PAGE_TIMEOUT_MS = 7_000

# The page's readable text straight from the DOM. Unlike QWebEnginePage.toPlainText (which
# stays empty until the whole page, ads included, has finished loading), this works as soon
# as the HTML has arrived. Runs in an isolated world so the page's own scripts can't interfere.
EXTRACT_TEXT = """(() => {
  const clean = (root) => {
    if (!root) return '';
    const copy = root.cloneNode(true);
    copy.querySelectorAll('script, style, noscript, svg, nav, footer, iframe, form').forEach(e => e.remove());
    return copy.textContent.replace(/[ \\t]+/g, ' ').replace(/\\s*\\n\\s*/g, '\\n').trim();
  };
  // Prefer the article itself; fall back to the whole page when that's (nearly) empty
  const main = clean(document.querySelector('article, main, [role=main]'));
  return main.length > 500 ? main : clean(document.body);
})()"""

EXTRACT_RESULTS = "[...document.querySelectorAll('a.result__a')].slice(0, 12).map(a => [a.href, a.textContent.trim()])"


def _real_url(href: str) -> str:
    # DuckDuckGo wraps results as duckduckgo.com/l/?uddg=<the real url>
    parsed = urlparse(href)
    if parsed.netloc.endswith("duckduckgo.com") and parsed.path.startswith("/l/"):
        return parse_qs(parsed.query).get("uddg", [href])[0]
    return href


def pick_sources(results, limit=CANDIDATES) -> list[tuple[str, str]]:
    """Real result URLs (no ads), at most one per site."""
    picked, sites = [], set()
    for href, title in results:
        url = _real_url(href)
        site = display_host(url)
        if not url.startswith(("http://", "https://")) or "duckduckgo.com" in site or site in sites:
            continue
        sites.add(site)
        picked.append((url, title))
        if len(picked) == limit:
            break
    return picked


# --- A small Markdown → HTML converter for the answer (headings, lists, emphasis, code, links) --

def _inline(text: str, sources) -> str:
    text = html.escape(text, quote=False)
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<![*\w])\*([^*\n]+)\*(?!\w)", r"<em>\1</em>", text)
    # Text is already escaped above; quotes in the URL are too, so it can't break out of href
    text = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)",
                  lambda m: f'<a href="{m.group(2).replace(chr(34), "&quot;")}">{m.group(1)}</a>', text)

    def cite(match):
        n = int(match.group(1))
        if 1 <= n <= len(sources):
            url = html.escape(sources[n - 1][0])
            return f'<a class="cite" href="{url}" title="{html.escape(sources[n - 1][1])}">{n}</a>'
        return match.group(0)
    return re.sub(r"\[(\d+)\]", cite, text)


def markdown_to_html(markdown: str, sources=()) -> str:
    out, list_tag, paragraph = [], None, []

    def flush():
        nonlocal list_tag
        if paragraph:
            out.append("<p>" + " ".join(paragraph) + "</p>")
            paragraph.clear()
        if list_tag:
            out.append(f"</{list_tag}>")
            list_tag = None

    for raw in markdown.splitlines():
        line = raw.strip()
        heading = re.match(r"(#{1,4})\s+(.*)", line)
        bullet = re.match(r"[-*•]\s+(.*)", line)
        numbered = re.match(r"\d+[.)]\s+(.*)", line)
        if not line:
            flush()
        elif heading:
            flush()
            level = min(len(heading.group(1)) + 1, 4)
            out.append(f"<h{level}>{_inline(heading.group(2), sources)}</h{level}>")
        elif bullet or numbered:
            tag = "ul" if bullet else "ol"
            if paragraph or list_tag != tag:
                flush()
                out.append(f"<{tag}>")
                list_tag = tag
            out.append(f"<li>{_inline((bullet or numbered).group(1), sources)}</li>")
        else:
            if list_tag:
                flush()
            paragraph.append(_inline(line, sources))
    flush()
    return "\n".join(out)


def render_page(query, sources, body_html, status, accent) -> str:
    cards = "".join(
        f'<a class="source" href="{html.escape(url)}"><span class="n">{i}</span>'
        f'<span class="t">{html.escape(title)}</span><span class="h">{html.escape(display_host(url))}</span></a>'
        for i, (url, title) in enumerate(sources, 1))
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>✦ {html.escape(query)}</title><style>
:root {{ color-scheme: light dark; --accent: {accent}; }}
body {{ font: 16px/1.6 -apple-system, BlinkMacSystemFont, sans-serif; max-width: 760px; margin: 48px auto; padding: 0 28px; }}
.label {{ color: var(--accent); font-weight: 700; font-size: 12px; letter-spacing: .08em; text-transform: uppercase; }}
h1 {{ font-size: 30px; line-height: 1.25; margin: 6px 0 22px; }}
.sources {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(170px, 1fr)); gap: 10px; margin-bottom: 26px; }}
.source {{ display: flex; flex-direction: column; gap: 2px; padding: 10px 12px; border-radius: 12px; text-decoration: none;
          color: inherit; background: color-mix(in srgb, currentColor 6%, transparent); font-size: 13px; }}
.source .n {{ color: var(--accent); font-weight: 700; }} .source .t {{ font-weight: 600; display: -webkit-box;
          -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }} .source .h {{ opacity: .6; }}
#status {{ opacity: .6; font-style: italic; }}
a.cite {{ display: inline-block; min-width: 18px; padding: 0 5px; margin: 0 1px; border-radius: 9px; font-size: 11px;
          line-height: 18px; text-align: center; vertical-align: 2px; text-decoration: none; color: white; background: var(--accent); }}
code {{ font: 14px Menlo, monospace; padding: 1px 5px; border-radius: 5px; background: color-mix(in srgb, currentColor 8%, transparent); }}
a {{ color: var(--accent); }}
</style></head><body>
<div class="label">✦ Browse for Me</div><h1>{html.escape(query)}</h1>
<div class="sources" id="sources">{cards}</div>
<div id="status">{html.escape(status)}</div>
<div id="answer">{body_html}</div>
</body></html>"""


class PageReader(QObject):
    """Loads a page off-screen and reports its text as soon as the article is there.

    Ad-heavy pages can take 30+ seconds to finish loading every ad and tracker, long after
    the text is readable, so instead of waiting for "finished" this checks the text every
    second and stops once there's a decent amount and it has stopped growing.
    """

    done = pyqtSignal(str, str)  # title, text

    def __init__(self, profile, url, parent, timeout_ms=PAGE_TIMEOUT_MS, enough_chars=1500):
        super().__init__(parent)
        self._enough = enough_chars
        self._last_len = -1
        self._loaded = False
        self._finished = False
        self._stopped = False
        self.page = QWebEnginePage(profile, self)
        self.page.setAudioMuted(True)
        self.page.loadFinished.connect(self._on_loaded)
        self._poll = QTimer(self, interval=1000, timeout=self._check)
        self._poll.start()
        QTimer.singleShot(timeout_ms, self._give_up)
        self.page.load(QUrl(url))

    def _on_loaded(self, _ok):
        self._loaded = True
        self._check()

    def _check(self):
        if not self._finished:
            self.page.runJavaScript(EXTRACT_TEXT, QWebEngineScript.ScriptWorldId.ApplicationWorld, self._got)

    def _got(self, text):
        if self._finished or not isinstance(text, str):
            text = text if isinstance(text, str) else ""
            if self._finished:
                return
        size = len(text)
        settled = size >= self._enough and size == self._last_len
        if self._loaded or settled:
            self._finish(text or "")
        self._last_len = size

    def _give_up(self):
        if not self._finished:
            self.page.runJavaScript(EXTRACT_TEXT, QWebEngineScript.ScriptWorldId.ApplicationWorld,
                                    lambda text: self._finish(text if isinstance(text, str) else ""))
            # A hung renderer never answers at all: don't let one bad site stall everything
            QTimer.singleShot(2000, lambda: self._finish(""))

    def _finish(self, text):
        if self._finished:
            return
        title = self.page.title()
        self.cancel()
        self.done.emit(title, text)

    def cancel(self):
        # Stop the page outright: left running, it keeps loading ads and trackers in the
        # background, and enough of those running at once stalls every other page
        if self._stopped:
            return
        self._stopped = self._finished = True
        self._poll.stop()
        self.page.triggerAction(QWebEnginePage.WebAction.Stop)
        self.page.setUrl(QUrl("about:blank"))
        self.page.deleteLater()


class BrowseForMe(QObject):
    """One run. Emits status updates, the answer as it streams, then the final answer."""

    status = pyqtSignal(str)
    sources_found = pyqtSignal(list)
    partial = pyqtSignal(str)   # answer HTML so far
    finished = pyqtSignal(str)  # final answer HTML
    failed = pyqtSignal(str)

    def __init__(self, claude, profile, query, parent):
        super().__init__(parent)
        self.claude = claude
        self.profile = profile
        self.query = query
        self.sources: list[tuple[str, str]] = []
        self._texts: dict[int, str] = {}
        self._candidates: list[tuple[str, str]] = []
        self._next = 0
        self._pages: list[QWebEnginePage] = []
        self._readers: list[PageReader] = []
        self._request = None

    def start(self):
        self.status.emit("Searching…")
        page = self._hidden_page()
        page.loadFinished.connect(lambda ok: page.runJavaScript(EXTRACT_RESULTS, 0, self._got_results))
        page.load(QUrl(SEARCH_URL.format(quote_plus(self.query))))

    def cancel(self):
        if self._request is not None:
            self._request.cancel()
        self._cleanup()

    def _hidden_page(self) -> QWebEnginePage:
        page = QWebEnginePage(self.profile, self)
        page.setAudioMuted(True)
        self._pages.append(page)
        return page

    def _got_results(self, results):
        self._candidates = pick_sources(results or [])
        if not self._candidates:
            self.failed.emit("The search didn't return any results to read.")
            self._cleanup()
            return
        self._next = 0
        self._read_next()

    def _read_next(self):
        # One page at a time: several hidden pages loading at once can stall each other
        # (seen when macOS proxy auto-discovery is slow)
        if len(self.sources) == MAX_SOURCES or self._next == len(self._candidates):
            self._ready()
            return
        url, title = self._candidates[self._next]
        self._next += 1
        self.status.emit(f"Reading {display_host(url)}…  ({len(self.sources)} of {MAX_SOURCES} sources so far)")
        reader = PageReader(self.profile, url, self)
        reader.done.connect(lambda page_title, text: self._got_text(url, page_title or title, text))
        self._readers.append(reader)

    def _got_text(self, url, title, text):
        if len(text or "") >= MIN_TEXT:
            self._texts[len(self.sources)] = text
            self.sources.append((url, title))
        self._read_next()

    def _ready(self):
        if not self.sources:
            self.failed.emit("Couldn't read any of the search results (they may block automated reading).")
            self._cleanup()
            return
        self.sources_found.emit(self.sources)
        self._ask()

    def _ask(self):
        blocks = "\n\n".join(f"Source [{i + 1}]\n" + page_block(title, url, self._texts.get(i, "")[:PAGE_TEXT_LIMIT])
                             for i, (url, title) in enumerate(self.sources))
        self.status.emit("Writing the answer…")
        self._request = self.claude.ask(
            blocks + f"\n\nQuestion: {self.query}\n\n"
            "Using the sources above, write a helpful answer in Markdown (under 350 words). Start with a "
            "one or two sentence direct answer, then the useful details as short sections or bullet points. "
            "Be specific: name the actual products, people, places, numbers, prices or steps the sources "
            "mention, and say what makes each one different. Never write vague filler like 'several options "
            "exist' without naming them. Cite sources inline with their numbers in square brackets, like [1] "
            "or [2][3]. Don't add a source list at the end. If the sources disagree or don't cover "
            "something, say so.")
        self._request.partial.connect(lambda md: self.partial.emit(markdown_to_html(md, self.sources)))
        self._request.finished.connect(self._done)
        self._request.failed.connect(lambda msg: (self.failed.emit(msg), self._cleanup()))

    def _done(self, markdown):
        self.finished.emit(markdown_to_html(markdown, self.sources))
        self._cleanup()

    def _cleanup(self):
        for page in self._pages:
            page.deleteLater()
        for reader in self._readers:
            reader.cancel()
        self._pages, self._readers = [], []


def is_answer_page(url: str) -> bool:
    return url.startswith("file:") and "/browse/" in url and url.endswith(".html")


def answer_path(run_id: str):
    folder = storage.data_dir() / "browse"
    folder.mkdir(exist_ok=True)
    return folder / f"{run_id}.html"


def new_run_id() -> str:
    return time.strftime("%Y%m%d-%H%M%S-") + new_id()[:6]


def js_set(element_id: str, html_text: str) -> str:
    return f"document.getElementById({json.dumps(element_id)}).innerHTML = {json.dumps(html_text)};"
