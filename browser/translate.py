# Translate Page, with the local Claude CLI (no API key, no translation service).
#
# The page's text is collected in its application world, a few hundred lines at a time are sent to
# Claude as a JSON list, and the answers are written back into the same text nodes, top of the page
# first. Show Original puts every node back. Code, inputs and anything marked translate="no" or
# .notranslate are left alone. Page text is untrusted, so it goes inside <page> tags like every
# other Claude feature, and the CLI runs with no tools: the worst a page can do is mistranslate.

import json

from PyQt6.QtCore import QLocale, QObject
from PyQt6.QtWebEngineCore import QWebEngineScript

WORLD = QWebEngineScript.ScriptWorldId.ApplicationWorld
CHUNK_CHARS = 3500
CHUNK_ITEMS = 80
PARALLEL = 3
MAX_CHARS = 60_000

COLLECT = r"""
(() => {
  const SKIP = new Set(['SCRIPT', 'STYLE', 'NOSCRIPT', 'CODE', 'PRE', 'TEXTAREA', 'INPUT', 'SVG', 'KBD', 'SAMP', 'VAR', 'TEMPLATE']);
  const nodes = [], texts = [];
  let total = 0;
  const walker = document.createTreeWalker(document.body || document.documentElement, NodeFilter.SHOW_TEXT, {
    acceptNode(n) {
      const t = n.nodeValue.trim();
      if (t.length < 2 || !/\p{L}/u.test(t)) return NodeFilter.FILTER_REJECT;
      for (let e = n.parentElement; e; e = e.parentElement) {
        if (SKIP.has(e.tagName) || e.isContentEditable || e.getAttribute('translate') === 'no' ||
            e.classList.contains('notranslate')) return NodeFilter.FILTER_REJECT;
      }
      return NodeFilter.FILTER_ACCEPT;
    }
  });
  while (walker.nextNode() && total < %d) {
    const n = walker.currentNode;
    nodes.push(n);
    texts.push(n.nodeValue.trim());
    total += n.nodeValue.length;
  }
  window.__pbTranslate = {nodes, originals: nodes.map((n) => n.nodeValue)};
  return {texts, lang: document.documentElement.lang || ''};
})()
""" % MAX_CHARS

APPLY = r"""
((start, items) => {
  const state = window.__pbTranslate;
  if (!state) return 0;
  let done = 0;
  items.forEach((text, i) => {
    const node = state.nodes[start + i], original = state.originals[start + i];
    if (!node || typeof text !== 'string' || !text.trim()) return;
    const [, lead, , trail] = original.match(/^(\s*)([\s\S]*?)(\s*)$/);
    node.nodeValue = lead + text + trail;
    done++;
  });
  return done;
})(%d, %s)
"""

RESTORE = r"""
(() => {
  const state = window.__pbTranslate;
  if (!state) return false;
  state.nodes.forEach((n, i) => { n.nodeValue = state.originals[i]; });
  delete window.__pbTranslate;
  return true;
})()
"""


def target_language(settings: dict) -> str:
    return settings.get("translate_to") or QLocale.languageToString(QLocale.system().language()) or "English"


def chunks(texts: list[str], max_chars=CHUNK_CHARS, max_items=CHUNK_ITEMS) -> list[tuple[int, list[str]]]:
    """(index of the first, texts) groups small enough for one request, in page order."""
    out, start, current, size = [], 0, [], 0
    for i, text in enumerate(texts):
        if current and (size + len(text) > max_chars or len(current) >= max_items):
            out.append((start, current))
            start, current, size = i, [], 0
        current.append(text)
        size += len(text)
    if current:
        out.append((start, current))
    return out


def build_prompt(items: list[str], language: str) -> str:
    return (f"Translate each string in the JSON array below into {language}. Answer with only a JSON array of "
            f"exactly {len(items)} strings, in the same order. Keep names, numbers, URLs, emoji and punctuation; "
            f"a string that's already in {language}, or can't be translated, comes back unchanged.\n"
            f"<page>\n{json.dumps(items, ensure_ascii=False)}\n</page>")


def parse_answer(answer: str, expected: int) -> list[str] | None:
    """The translated list, or None if the answer isn't a list of `expected` strings."""
    start, end = answer.find("["), answer.rfind("]")
    if start < 0 or end <= start:
        return None
    try:
        items = json.loads(answer[start:end + 1])
    except ValueError:
        return None
    if not isinstance(items, list) or len(items) != expected or not all(isinstance(i, str) for i in items):
        return None
    return items


class _Job:
    def __init__(self, view, groups, language):
        self.view, self.groups, self.language = view, groups, language
        self.next = 0
        self.running = 0
        self.done = 0
        self.failed = 0
        self.requests = []
        self.cancelled = False


class Translator(QObject):
    def __init__(self, browser):
        super().__init__(browser)
        self.browser = browser
        self._jobs: dict[int, _Job] = {}
        self._translated: set[int] = set()  # id(view) of pages showing a translation
        self._watched: set[int] = set()     # views whose navigations we listen for

    def state(self, view) -> str:
        """"idle", "translating" or "translated"."""
        if view is None:
            return "idle"
        if id(view) in self._jobs:
            return "translating"
        return "translated" if id(view) in self._translated else "idle"

    def toggle(self, view=None, tab=None, reader=False):
        view = view or self.browser._current_view()
        if view is None:
            return
        state = self.state(view)
        if state == "translating":
            self._cancel(view)
            self.browser.toast("Stopped translating")
        elif state == "translated":
            self.show_original(view)
        else:
            self.translate(view)

    def translate(self, view):
        if not self.browser.feature("claude").claude.available:
            self.browser.toast("Translate uses the Claude Code CLI, which isn't available")
            return
        language = target_language(self.browser.store.settings)
        view.page().runJavaScript(COLLECT, WORLD, lambda found, v=view: self._collected(v, found, language))

    def _collected(self, view, found, language):
        if found is None:
            return  # (the page closed while it was being read: Qt answers with nothing)
        texts = (found or {}).get("texts") or []
        if not texts:
            self.browser.toast("There's no text on this page to translate")
            return
        job = _Job(view, chunks(texts), language)
        self._jobs[id(view)] = job
        self._translated.add(id(view))
        if id(view) not in self._watched:
            self._watched.add(id(view))
            view.loadStarted.connect(lambda v=view: self.forget(v))
            view.destroyed.connect(lambda _=None, key=id(view): self._drop(key))
        self.browser.toast(f"Translating into {language}…")
        self._changed(view)
        for _ in range(min(PARALLEL, len(job.groups))):
            self._send_next(job)

    def _send_next(self, job):
        if job.cancelled or job.next >= len(job.groups):
            return
        start, items = job.groups[job.next]
        job.next += 1
        job.running += 1
        request = self.browser.feature("claude").claude.ask(build_prompt(items, job.language))
        job.requests.append(request)
        request.finished.connect(lambda answer, j=job, s=start, n=len(items): self._answered(j, s, n, answer))
        request.failed.connect(lambda _message, j=job: self._group_done(j, failed=True))

    def _answered(self, job, start, count, answer):
        if job.cancelled:
            return
        items = parse_answer(answer, count)
        if items is None:
            self._group_done(job, failed=True)
            return
        try:
            job.view.page().runJavaScript(APPLY % (start, json.dumps(items, ensure_ascii=False)), WORLD)
        except RuntimeError:
            job.cancelled = True  # the page went away
            return
        self._group_done(job)

    def _group_done(self, job, failed=False):
        if job.cancelled:
            return
        job.running -= 1
        job.done += 1
        job.failed += failed
        if job.done >= len(job.groups):
            self._jobs.pop(id(job.view), None)
            if job.failed == len(job.groups):
                self._translated.discard(id(job.view))
                self.browser.toast("Couldn't translate this page")
            else:
                note = f" ({job.failed} parts couldn't be translated)" if job.failed else ""
                self.browser.toast(f"Translated into {job.language}{note} · Translate again shows the original")
            self._changed(job.view)
        else:
            self._send_next(job)

    def _drop(self, key):
        """A page closed mid-translation: stop its remaining requests (each one costs) and forget it."""
        self._watched.discard(key)
        self._translated.discard(key)
        job = self._jobs.pop(key, None)
        if job is not None:
            job.cancelled = True
            for request in job.requests:
                request.cancel()

    def _cancel(self, view):
        job = self._jobs.pop(id(view), None)
        if job is not None:
            job.cancelled = True
            for request in job.requests:
                request.cancel()
        self._changed(view)

    def show_original(self, view):
        self._cancel(view)
        self._translated.discard(id(view))
        try:
            view.page().runJavaScript(RESTORE, WORLD)
        except RuntimeError:
            pass
        self.browser.toast("Showing the original")
        self._changed(view)

    def forget(self, view):
        """The page navigated or closed: its translation is gone with it."""
        self._cancel(view)
        self._translated.discard(id(view))
        self._changed(view)

    def _changed(self, view):
        reader = getattr(self.browser, "reader", None)
        if reader is not None and view is reader.view:
            reader.translate_btn.setText({"idle": "Translate", "translating": "Translating…",
                                          "translated": "Show Original"}[self.state(view)])
