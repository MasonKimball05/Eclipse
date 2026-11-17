# Highlights: select text on a page, right-click → Highlight, and it's marked in yellow, there again each time you
# come back to the page. Each site's highlights are kept in a note of their own ("Highlights from example.com", in
# the Library with your other notes), each quote linked to its page, and ⌘T finds them by what they say.
# Right-click highlighted text (a double-click selects a word of it) → Remove Highlight. Never in Private windows.
#
# A highlight is kept as its text and a little of what's either side of it, and found again by those when the page
# loads (pages change: one whose text has gone just isn't marked). The marking runs in the application world, where
# the page's own scripts can't see or change it.

import json
import re
import time

from PyQt6.QtCore import QUrl
from PyQt6.QtWebEngineCore import QWebEngineScript

from . import storage
from .features import Feature
from .models import display_host, new_id

FILE = "highlights.json"
NOTES_SETTING = "highlight_notes"  # ({site: the note:<id> its highlights are kept in})
CONTEXT = 40  # (characters either side of a highlight kept to find it again)
MAX_TEXT = 2000  # (a highlight's own text, at most)
WORLD = QWebEngineScript.ScriptWorldId.ApplicationWorld
MARK_STYLE = "background: rgba(255, 214, 10, 0.45); color: inherit; border-radius: 2px; padding: 0;"

# The page's text as one string, each text node's place in it, and a range from places in it back to the nodes.
_TEXT = r"""
function eclipseTextNodes() {
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, {acceptNode(node) {
    const tag = node.parentElement && node.parentElement.tagName;
    return tag === "SCRIPT" || tag === "STYLE" || tag === "NOSCRIPT" || tag === "TEXTAREA"
      ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT;
  }});
  const nodes = [];
  let text = "";
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    nodes.push({node, start: text.length});
    text += node.data;
  }
  return {nodes, text};
}
function eclipseMark(range, id) {
  // Each text node the range covers, wrapped on its own (a range across elements can't be wrapped whole)
  const marks = [];
  const walker = document.createTreeWalker(range.commonAncestorContainer.nodeType === 3
    ? range.commonAncestorContainer.parentNode : range.commonAncestorContainer, NodeFilter.SHOW_TEXT);
  const covered = [];
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    if (range.intersectsNode(node)) covered.push(node);
  }
  for (let node of covered) {
    let start = node === range.startContainer ? range.startOffset : 0;
    let end = node === range.endContainer ? range.endOffset : node.data.length;
    if (end <= start || !node.data.slice(start, end).trim()) continue;
    if (end < node.data.length) node.splitText(end);
    if (start > 0) node = node.splitText(start);
    const mark = document.createElement("mark");
    mark.dataset.eclipseHighlight = id;
    mark.setAttribute("style", "STYLE");
    node.parentNode.insertBefore(mark, node);
    mark.appendChild(node);
    marks.push(mark);
  }
  return marks.length;
}
""".replace("STYLE", MARK_STYLE)

# The selection: its text and what's either side, marked, and told back
SELECTION = _TEXT + r"""
(() => {
  const selection = window.getSelection();
  if (!selection || selection.isCollapsed || !document.body) return null;
  const range = selection.getRangeAt(0);
  // (the range's own text, as the page's text nodes have it: the selection's is as drawn, its spaces and lines between
  // paragraphs changed, so a highlight across two was never found again)
  const text = range.toString();
  if (!text.trim()) return null;
  const before = document.createRange();
  before.setStart(document.body, 0);
  before.setEnd(range.startContainer, range.startOffset);
  const after = document.createRange();
  after.setStart(range.endContainer, range.endOffset);
  after.setEnd(document.body, document.body.childNodes.length);
  const found = {text: text.slice(0, MAX), prefix: before.toString().slice(-CONTEXT),
                 suffix: after.toString().slice(0, CONTEXT)};
  const marked = eclipseMark(range, ID);
  selection.removeAllRanges();
  return marked ? found : null;
})();
"""

# Highlights kept for this page, found again by their text (and what's either side) and marked
APPLY = _TEXT + r"""
(() => {
  if (!document.body) return 0;
  let done = 0;
  for (const h of HIGHLIGHTS) {
    if (document.querySelector('mark[data-eclipse-highlight="' + CSS.escape(h.id) + '"]')) { done++; continue; }
    const {nodes, text} = eclipseTextNodes();
    let at = -1, from = 0, first = -1;
    while ((from = text.indexOf(h.text, from)) !== -1) {
      if (first === -1) first = from;
      if (text.slice(Math.max(0, from - h.prefix.length), from) === h.prefix) { at = from; break; }
      from += 1;
    }
    if (at === -1) at = first;  // (what's around it changed: the first place it's said)
    if (at === -1) continue;
    const place = (offset, end) => {
      for (let i = nodes.length - 1; i >= 0; i--) {
        const n = nodes[i];
        if (end ? n.start < offset : n.start <= offset) return [n.node, offset - n.start];
      }
      return null;
    };
    const start = place(at, false), end = place(at + h.text.length, true);
    if (!start || !end) continue;
    const range = document.createRange();
    range.setStart(start[0], start[1]);
    range.setEnd(end[0], end[1]);
    if (eclipseMark(range, h.id)) done++;
  }
  return done;
})();
"""

UNMARK = r"""
(() => {
  for (const mark of document.querySelectorAll('mark[data-eclipse-highlight="' + CSS.escape(ID) + '"]')) {
    const parent = mark.parentNode;
    while (mark.firstChild) parent.insertBefore(mark.firstChild, mark);
    parent.removeChild(mark);
    parent.normalize();
  }
})();
"""


def page_key(url) -> str:
    """The page a highlight belongs to: its address without the #fragment (a heading's link is the same page)."""
    url = QUrl(url) if isinstance(url, str) else QUrl(url)
    url.setFragment(None)
    return url.toString()


def highlightable(url) -> bool:
    return QUrl(url).scheme() in ("http", "https")


def site_of(url) -> str:
    return QUrl(url).host().lower().removeprefix("www.")


class Store:
    """Every highlight, in the data folder: [{"id", "url", "title", "text", "prefix", "suffix", "created"}]."""

    def __init__(self):
        self.path = storage.data_dir() / FILE
        data = storage.read_json(self.path) or {}
        self.items: list[dict] = [h for h in data.get("highlights", []) if isinstance(h, dict) and h.get("text")]

    def save(self):
        storage.write_json(self.path, {"version": 1, "highlights": self.items})

    def for_page(self, url) -> list[dict]:
        key = page_key(url)
        return [h for h in self.items if h.get("url") == key]

    def add(self, url, title, found, item_id=None) -> dict:
        item = {"id": item_id or new_id(), "url": page_key(url), "title": title or display_host(url),
                "text": str(found.get("text", ""))[:MAX_TEXT], "prefix": str(found.get("prefix", ""))[-CONTEXT:],
                "suffix": str(found.get("suffix", ""))[:CONTEXT], "created": time.time()}
        self.items.append(item)
        self.save()
        return item

    def remove(self, item):
        self.items = [h for h in self.items if h.get("id") != item.get("id")]
        self.save()

    def overlapping(self, url, selected: str) -> dict | None:
        """The highlight on this page the selected text is a real part of (a third of it at least: "the" selected
        anywhere offered to take off a highlight with "the" in it). Selecting more than a highlight highlights."""
        selected = " ".join(selected.split())
        for item in self.for_page(url):
            text = " ".join(item["text"].split())
            if selected and selected in text and len(selected) >= max(4, len(text) // 3):
                return item
        return None

    def matching(self, query: str) -> list[dict]:
        words = query.lower().split()
        return [h for h in reversed(self.items) if words and all(w in h["text"].lower() for w in words)]


_MARKDOWN = re.compile(r"([\\`*_\[\]<>#|~])")


def _escaped(text: str) -> str:
    """Page text as plain words in Markdown: its *, _, [, < and the rest don't become formatting or links."""
    return _MARKDOWN.sub(r"\\\1", " ".join(text.split()))


def quote_line(item) -> str:
    title = item.get("title", "").replace("[", "(").replace("]", ")")
    return f"- “{_escaped(item['text'])}” — [{_escaped(title)}]({item['url']})"


def without_quote(markdown: str, item) -> str:
    """The note without `item`'s quote: found by its link and its first words, however the note's editor has
    rewrapped or re-escaped it since (saved again, its lines were rewritten, and an exact line never matched)."""
    lines = markdown.splitlines(keepends=True)
    blocks, current = [], []
    for line in lines:  # (a list item, with the lines it carries on onto)
        if line.lstrip().startswith(("- ", "* ")) or not line.strip() or line.startswith("#"):
            if current:
                blocks.append(current)
            current = [line]
        else:
            current.append(line)
    if current:
        blocks.append(current)
    plain = lambda text: " ".join(re.sub(r"\\(.)", r"\1", text).split())  # noqa: E731
    start = plain(item["text"])[:24]
    for i, block in enumerate(blocks):
        joined = plain("".join(block))
        if item["url"] in joined and start in joined:
            return "".join(line for j, b in enumerate(blocks) if j != i for line in b)
    return markdown


def _script(source: str, **values) -> str:
    for name, value in values.items():
        source = source.replace(name, value)
    return source


class HighlightsFeature(Feature):
    name = "highlights"

    def __init__(self, window):
        super().__init__(window)
        self.store = Store()

    # --- On the page ---------------------------------------------------------------------------------------

    def _usable(self, view) -> bool:
        return highlightable(view.url().toString()) and not view.page().profile().isOffTheRecord()

    def selection_menu(self, menu, view, text):
        from PyQt6.QtGui import QAction
        if not self._usable(view):
            return []
        url = view.url().toString()
        existing = self.store.overlapping(url, text)
        if existing is not None:
            action = QAction("Remove Highlight", menu)
            action.triggered.connect(lambda: self.remove(existing, view))
        else:
            action = QAction("Highlight", menu)
            action.triggered.connect(lambda: self.highlight_selection(view))
        return [action]

    def highlight_selection(self, view, done=None):
        """The selection on `view`'s page: marked, kept, and added to its site's note."""
        url, title = view.url().toString(), view.title()
        item_id = new_id()  # (the marks carry it, to be taken off again)
        source = _script(SELECTION, MAX=str(MAX_TEXT), CONTEXT=str(CONTEXT), ID=json.dumps(item_id))

        def kept(found):
            if not isinstance(found, dict) or not str(found.get("text", "")).strip():
                if done is not None:
                    done(None)
                return
            item = self.store.add(url, title, found, item_id)
            self.add_to_note(item)
            self.window.toast(f"Highlighted · in “Highlights from {site_of(url)}”", lambda: self.remove(item, view))
            if done is not None:
                done(item)
        view.page().runJavaScript(source, WORLD, kept)

    def remove(self, item, view=None):
        self.store.remove(item)
        self.take_from_note(item)
        for tab in self.window.store.all_tabs():
            other = tab.view
            if other is not None and hasattr(other, "page") and page_key(other.url().toString()) == item["url"]:
                other.page().runJavaScript(_script(UNMARK, ID=json.dumps(item["id"])), WORLD)

    def show_on(self, view):
        """This page's highlights, marked (as it finishes loading)."""
        if view is None or not hasattr(view, "page") or not self._usable(view):
            return
        items = self.store.for_page(view.url().toString())
        if items:
            payload = json.dumps([{k: h[k] for k in ("id", "text", "prefix")} for h in items])
            view.page().runJavaScript(_script(APPLY, HIGHLIGHTS=payload), WORLD)

    def load_finished(self, tab, ok):
        if ok and tab is not None:
            self.show_on(tab.view)

    # --- The site's note -----------------------------------------------------------------------------------

    def _note_for(self, site: str, create: bool):
        from .notes import _path, is_note, new_note_url
        notes = self.window.store.settings.setdefault(NOTES_SETTING, {})
        url = notes.get(site)
        if url and is_note(url) and _path(url).exists():
            return url
        if not create:
            return None
        url = notes[site] = new_note_url()
        storage.write_text(_path(url), f"# Highlights from {site}\n\n")
        self.window.store.schedule_save()
        return url

    def _editing(self, note_url, change):
        """`change(markdown) -> markdown` on a note's file, through its editor when it's open in a tab (so the two
        never write over each other)."""
        from .notes import _path
        editor = next((t.view for t in self.window.store.all_tabs() if t.url == note_url and t.view is not None
                       and hasattr(t.view, "toMarkdown")), None)
        if editor is not None:
            editor.save()  # (anything typed there first)
        path = _path(note_url)
        text = path.read_text() if path.exists() else ""
        storage.write_text(path, change(text))
        if editor is not None:
            editor.setMarkdown(path.read_text())
            editor.document().setModified(False)

    def add_to_note(self, item):
        note = self._note_for(site_of(item["url"]), create=True)
        editor = self._editor_of(note)
        if editor is not None:
            # (open in a tab: added at its end, one step to undo; reloaded whole, its undo history and where you were
            # typing went)
            from PyQt6.QtGui import QTextCursor
            cursor = QTextCursor(editor.document())
            cursor.movePosition(QTextCursor.MoveOperation.End)
            cursor.beginEditBlock()
            if cursor.block().text().strip():
                cursor.insertBlock()
            cursor.insertMarkdown(quote_line(item))
            cursor.endEditBlock()
            editor.save()
            return
        self._editing(note, lambda text: text.rstrip("\n") + "\n" + quote_line(item) + "\n")

    def _editor_of(self, note_url):
        return next((t.view for t in self.window.store.all_tabs() if t.url == note_url and t.view is not None
                     and hasattr(t.view, "toMarkdown")), None)

    def take_from_note(self, item):
        note = self._note_for(site_of(item["url"]), create=False)
        if note is not None:
            self._editing(note, lambda text: without_quote(text, item))

    # --- ⌘T ------------------------------------------------------------------------------------------------

    def command_results(self, bar, query, seen):
        from .command_bar import Result
        if getattr(bar, "mode", "") == "split" or len(query.strip()) < 3:
            return []
        found = []
        for item in self.store.matching(query)[:4]:
            key = f"{item['url']}#highlight-{item['id']}"
            if key in seen:
                continue
            seen.add(key)
            text = " ".join(item["text"].split())
            found.append(Result(f"“{text[:90]}”", f"Highlight · {item.get('title') or site_of(item['url'])}",
                                "Highlight", bar._icon("ph.highlighter-circle"), bar._open(item["url"])))
        return found

    def commands(self):
        return [("Highlight Selection", "ph.highlighter-circle", self._highlight_current)]

    def _highlight_current(self):
        view = getattr(self.window.current_tab, "view", None)
        if view is not None and hasattr(view, "page") and self._usable(view):
            self.highlight_selection(view)

    def history_cleared(self):
        pass  # (highlights are yours, like notes: kept when history's cleared)


FEATURE = HighlightsFeature
