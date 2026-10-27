# Reader view (⌥⌘R): the article on the page, alone, in a clean column, over the page.
#
# The article is found in the page (the block with the most paragraph text, the way Readability
# does it) and rebuilt from a short list of safe tags and attributes. It's shown in a separate
# view with JavaScript turned off, so nothing from the page can run there. Links open in the tab.
# A, A+, a theme (light, sepia, dark) and Translate sit in the bar at the top; Esc closes it.

import html
import tempfile
from pathlib import Path

from PyQt6.QtCore import QSize, Qt, QUrl
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWebEngineCore import QWebEnginePage, QWebEngineScript, QWebEngineSettings
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QToolButton, QVBoxLayout

import qtawesome as qta

WORLD = QWebEngineScript.ScriptWorldId.ApplicationWorld
MIN_TEXT = 400        # less article text than this, and there's nothing to read
SETHTML_LIMIT = 1_800_000  # bytes; Qt's own limit is 2 MB
SIZES = [15, 17, 19, 21, 24, 27]
THEMES = {  # background, text, secondary, links
    "light": ("#ffffff", "#1d1d1f", "#6e6e73", "#0a62c9"),
    "sepia": ("#f6efe2", "#3b2f22", "#7d6b55", "#8a4b10"),
    "dark": ("#1c1c1e", "#e8e8ea", "#9a9aa0", "#6cb2ff"),
}

# Runs in the page's application world; returns {title, byline, site, html, words, lang} or null
EXTRACT = r"""
(() => {
  const SKIP = /(^|[-_ ])(comment|comments|share|sharing|social|nav|navbar|menu|footer|sidebar|related|recommend|promo|advert|ad|ads|sponsor|subscribe|newsletter|signup|cookie|banner|popup|modal|breadcrumb|toolbar|byline-share|references|reflist|footnotes|citation|navbox|catlinks|mw-editsection)([-_ ]|$)/i;
  const NOT_PROSE = 'nav, aside, footer, header[role=banner], form, .references, .reflist, .navbox, .footnotes, [role=navigation]';
  const BLOCKS = new Set(['P','H1','H2','H3','H4','H5','H6','UL','OL','LI','BLOCKQUOTE','PRE','FIGURE','FIGCAPTION',
                          'TABLE','THEAD','TBODY','TR','TD','TH','DL','DT','DD','HR','BR']);
  const INLINE = new Set(['A','EM','I','STRONG','B','CODE','SUP','SUB','SMALL','MARK','S','U','SPAN','ABBR','TIME','Q','KBD']);
  const DROP = new Set(['SCRIPT','STYLE','NOSCRIPT','IFRAME','FORM','BUTTON','INPUT','SELECT','TEXTAREA','SVG','CANVAS',
                        'NAV','ASIDE','FOOTER','HEADER','OBJECT','EMBED','DIALOG','TEMPLATE']);
  const esc = (s) => s.replace(/[&<>"]/g, (c) => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));
  const abs = (u) => { try { const x = new URL(u, location.href); return /^https?:$|^data:$/.test(x.protocol) ? x.href : null; } catch (e) { return null; } };

  // Score: each paragraph's text goes to its parent, half to its grandparent
  const scores = new Map();
  for (const p of document.querySelectorAll('p, pre, blockquote, li')) {
    const text = p.textContent.trim();
    if (text.length < 40 || p.closest(NOT_PROSE)) continue;
    // List items count for less: reference lists and link lists are long, but they aren't the article
    const value = (Math.min(text.length, 1500) + text.split(/[,.;]/).length * 8) * (p.tagName === 'LI' ? 0.3 : 1);
    const parent = p.parentElement, grand = parent && parent.parentElement;
    if (parent) scores.set(parent, (scores.get(parent) || 0) + value);
    if (grand) scores.set(grand, (scores.get(grand) || 0) + value / 2);
  }
  let best = null, top = 0;
  for (const [el, score] of scores) {
    const links = [...el.querySelectorAll('a')].reduce((n, a) => n + a.textContent.length, 0);
    const density = links / Math.max(el.textContent.length, 1);
    const adjusted = score * (1 - Math.min(density, 0.9));
    if (adjusted > top) { top = adjusted; best = el; }
  }
  if (!best) return null;
  // An article split into sections (Wikipedia) scores per section: take in the sections around it
  const prose = (el) => [...el.querySelectorAll('p')].filter((x) => !x.closest(NOT_PROSE))
    .reduce((n, x) => n + x.textContent.trim().length, 0);
  const linkShare = (el) => [...el.querySelectorAll('a')].reduce((n, a) => n + a.textContent.length, 0) /
                            Math.max(el.textContent.length, 1);
  for (let i = 0; i < 4; i++) {
    const up = best.parentElement;
    if (!up || up === document.body || up === document.documentElement) break;
    if (prose(up) < prose(best) * 1.25 || linkShare(up) > 0.35) break;
    best = up;
  }
  const article = best.closest('article');
  if (article && article.textContent.length < best.textContent.length * 2.5) best = article;

  const meta = (name) => (document.querySelector(`meta[property="${name}"], meta[name="${name}"]`) || {}).content || '';
  const h1 = best.querySelector('h1') || document.querySelector('h1');
  const heading = h1 ? h1.textContent.replace(/[¶#§]/g, '').trim() : '';
  const og = meta('og:title');
  // "Espresso - Wikipedia": the page's own heading, when the site name was tacked onto it
  const title = (heading && (!og || og.includes(heading))) ? heading : og || heading || document.title;
  const bare = (t) => t.toLowerCase().replace(/[^\p{L}\p{N}]+/gu, '');
  const same = (a, b) => bare(a) === bare(b);
  const out = [];
  let words = 0;
  const walk = (node, depth) => {
    if (node.nodeType === 3) { out.push(esc(node.nodeValue)); words += node.nodeValue.split(/\s+/).filter(Boolean).length; return; }
    if (node.nodeType !== 1 || depth > 40) return;
    const tag = node.tagName;
    if (DROP.has(tag)) return;
    if ((tag === 'H1' || tag === 'H2') && same(node.textContent, title)) return;  // shown above the article
    if (node !== best) {
      const label = (node.className && node.className.baseVal === undefined ? node.className : '') + ' ' + (node.id || '');
      if (SKIP.test(label) || node.getAttribute('aria-hidden') === 'true' || node.hidden) return;
      const style = getComputedStyle(node);
      if (style.display === 'none' || style.visibility === 'hidden') return;
    }
    if (tag === 'IMG' || tag === 'PICTURE') {
      const img = tag === 'IMG' ? node : node.querySelector('img');
      const src = img && abs(img.currentSrc || img.src || img.getAttribute('data-src') || '');
      if (src && !(src.startsWith('data:') && src.length > 20000) && (img.naturalWidth || img.width || 200) >= 80) out.push(`<img src="${esc(src)}" alt="${esc(img.alt || '')}">`);
      return;
    }
    let open = '', close = '';
    if (tag === 'A') {
      const href = abs(node.getAttribute('href') || '');
      if (href && !href.startsWith('data:')) { open = `<a href="${esc(href)}">`; close = '</a>'; }
    } else if (BLOCKS.has(tag) || (INLINE.has(tag) && tag !== 'SPAN')) {
      const t = tag.toLowerCase();
      open = t === 'br' || t === 'hr' ? `<${t}>` : `<${t}>`;
      close = t === 'br' || t === 'hr' ? '' : `</${t}>`;
    } else if (tag === 'DIV' || tag === 'SECTION' || tag === 'ARTICLE' || tag === 'MAIN') {
      open = '<div>'; close = '</div>';
    }
    out.push(open);
    for (const child of node.childNodes) walk(child, depth + 1);
    out.push(close);
  };
  walk(best, 0);
  return {
    title,
    byline: meta('author') || meta('article:author') || '',
    site: meta('og:site_name') || location.hostname.replace(/^www\./, ''),
    html: out.join(''), words, text: best.textContent.trim().length,
    lang: document.documentElement.lang || '',
  };
})()
"""


def reader_document(article: dict, size: int, theme: str) -> str:
    """The whole reader page. Everything from the page is already escaped by EXTRACT; the parts added
    here are escaped again."""
    bg, fg, dim, link = THEMES.get(theme, THEMES["light"])
    title = html.escape(article.get("title") or "")
    byline = " · ".join(html.escape(x) for x in (article.get("site"), article.get("byline")) if x)
    minutes = max(1, round((article.get("words") or 0) / 230))
    lang = html.escape(article.get("lang") or "", quote=True)
    return f"""<!doctype html><html lang="{lang}"><head><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src http: https: data:; style-src 'unsafe-inline'">
<style>
  html {{ background: {bg}; }}
  body {{ margin: 0; padding: 56px 24px 96px; color: {fg}; font: {size}px/1.65 "New York", "Iowan Old Style", Georgia, serif; }}
  main {{ max-width: 680px; margin: 0 auto; }}
  .site {{ font: 600 13px -apple-system, system-ui, sans-serif; color: {dim}; letter-spacing: .02em; text-transform: uppercase; }}
  h1.title {{ font: 700 {round(size * 1.75)}px/1.2 -apple-system, system-ui, sans-serif; margin: 10px 0 8px; }}
  .meta {{ font: 13px -apple-system, system-ui, sans-serif; color: {dim}; margin-bottom: 32px; }}
  h1, h2, h3, h4 {{ font-family: -apple-system, system-ui, sans-serif; line-height: 1.3; }}
  a {{ color: {link}; text-decoration: none; }} a:hover {{ text-decoration: underline; }}
  img {{ max-width: 100%; max-height: 70vh; width: auto; height: auto; object-fit: contain; border-radius: 8px; display: block; margin: 20px auto; }}
  figcaption {{ font: 13px -apple-system, system-ui, sans-serif; color: {dim}; text-align: center; }}
  pre, code {{ font: .85em Menlo, monospace; background: rgba(127,127,127,.12); border-radius: 5px; }}
  pre {{ padding: 12px 14px; overflow-x: auto; }} code {{ padding: 1px 4px; }} pre code {{ background: none; padding: 0; }}
  blockquote {{ margin: 1.2em 0; padding-left: 18px; border-left: 3px solid {dim}; color: {dim}; }}
  table {{ border-collapse: collapse; font-size: .9em; }} td, th {{ border: 1px solid rgba(127,127,127,.3); padding: 4px 8px; }}
</style></head><body><main>
<div class="site">{html.escape(article.get("site") or "")}</div>
<h1 class="title">{title}</h1>
<div class="meta">{byline}{" · " if byline else ""}{minutes} min read</div>
{article.get("html") or ""}
</main></body></html>"""


class ReaderPage(QWebEnginePage):
    def __init__(self, reader, profile, parent):
        super().__init__(profile, parent)
        self.reader = reader

    def acceptNavigationRequest(self, url, nav_type, is_main_frame):
        if nav_type == QWebEnginePage.NavigationType.NavigationTypeLinkClicked:
            self.reader.open_link(url)
            return False
        return True  # our own setHtml()

    def createWindow(self, _type):
        return None


class ReaderView(QFrame):
    def __init__(self, browser, parent):
        super().__init__(parent)
        self.browser = browser
        self.setObjectName("Reader")
        self.tab = None
        self.article = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        bar = QFrame()
        bar.setObjectName("ReaderBar")
        row = QHBoxLayout(bar)
        row.setContentsMargins(12, 6, 8, 6)
        row.setSpacing(4)
        self.label = QLabel("Reader")
        self.label.setObjectName("ReaderLabel")
        row.addWidget(self.label)
        row.addStretch(1)
        self.smaller = self._button("ph.text-aa", "Smaller text", lambda: self.step_size(-1), small=True)
        self.bigger = self._button("ph.text-aa", "Larger text", lambda: self.step_size(1))
        self.theme_btn = QPushButton()
        self.theme_btn.setObjectName("ReaderButton")
        self.theme_btn.setToolTip("Light, sepia or dark")
        self.theme_btn.clicked.connect(self.cycle_theme)
        self.translate_btn = QPushButton("Translate")
        self.translate_btn.setObjectName("ReaderButton")
        claude = browser.feature("claude")  # (Translate is Claude's)
        if claude is not None:
            self.translate_btn.clicked.connect(lambda: claude.translator.toggle(self.view, self.tab, reader=True))
        else:
            self.translate_btn.hide()
        close = self._button("ph.x", "Close Reader (Esc)", self.close_reader)
        for widget in (self.smaller, self.bigger, self.theme_btn, self.translate_btn, close):
            row.addWidget(widget)
        layout.addWidget(bar)
        self.view = QWebEngineView()
        layout.addWidget(self.view, 1)
        parent.installEventFilter(self)  # keep covering the page as the window resizes
        escape = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        escape.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        escape.activated.connect(self.close_reader)
        self.hide()

    def _button(self, icon, tip, slot, small=False):
        b = QToolButton()
        b.setObjectName("ReaderTool")
        b.setToolTip(tip)
        b.setIcon(qta.icon(icon, color=self.browser.theme.panel_text))
        b.setIconSize(QSize(12, 12) if small else QSize(16, 16))
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.clicked.connect(slot)
        return b

    def eventFilter(self, obj, event):
        if obj is self.parentWidget() and event.type() == event.Type.Resize and self.isVisible():
            self.setGeometry(obj.stack.geometry())
        return False

    @property
    def settings(self):
        return self.browser.store.settings

    def is_open_for(self, tab) -> bool:
        return self.isVisible() and tab is not None and tab is self.tab

    def open_for(self, tab, view):
        """Read the article out of `view` (the tab's page) and show it."""
        view.page().runJavaScript(EXTRACT, WORLD, lambda article, t=tab, v=view: self._extracted(t, v, article))

    def _extracted(self, tab, view, article):
        if tab is not self.browser.current_tab:
            return
        if not isinstance(article, dict) or (article.get("text") or 0) < MIN_TEXT:
            self.browser.toast("Reader isn't available: there's no article on this page")
            return
        self.tab, self.article = tab, article
        profile = view.page().profile()
        if self.view.page().profile() is not profile:
            page = ReaderPage(self, profile, self.view)
            self.view.setPage(page)
        page = self.view.page()
        for attribute, on in ((QWebEngineSettings.WebAttribute.JavascriptEnabled, False),
                              (QWebEngineSettings.WebAttribute.LocalStorageEnabled, False),
                              (QWebEngineSettings.WebAttribute.PluginsEnabled, False)):
            page.settings().setAttribute(attribute, on)
        self.label.setText(article.get("site") or "Reader")
        self.translate_btn.setText("Translate")
        self.render()
        self.setGeometry(self.parentWidget().stack.geometry())
        self.show()
        self.raise_()
        self.view.setFocus()

    def render(self):
        if self.article is None:
            return
        size = SIZES[self.settings.get("reader_size", 2) % len(SIZES)]
        theme = self.settings.get("reader_theme", "light")
        self.theme_btn.setText({"light": "Light", "sepia": "Sepia", "dark": "Dark"}[theme])
        base = QUrl(self.tab.url) if self.tab else QUrl()
        document = reader_document(self.article, size, theme)
        if len(document.encode("utf-8")) < SETHTML_LIMIT:
            self.view.setHtml(document, base)
        else:
            # setHtml can't take more than 2 MB (it becomes a data: address): a long article goes
            # through a file instead (its links are already absolute; JavaScript is still off)
            path = Path(tempfile.gettempdir()) / "eclipse-ui" / "reader.html"
            path.parent.mkdir(exist_ok=True)
            path.write_text(document, encoding="utf-8")
            self.view.load(QUrl.fromLocalFile(str(path)))

    def step_size(self, step):
        self.settings["reader_size"] = max(0, min(len(SIZES) - 1, self.settings.get("reader_size", 2) + step))
        self.browser.store.schedule_save()
        self.render()

    def cycle_theme(self):
        order = list(THEMES)
        current = self.settings.get("reader_theme", "light")
        self.settings["reader_theme"] = order[(order.index(current) + 1) % len(order)] if current in order else "light"
        self.browser.store.schedule_save()
        self.render()

    def open_link(self, url):
        tab = self.tab
        self.close_reader()
        if tab is not None and isinstance(tab.view, QWebEngineView):
            tab.view.load(url)

    def close_reader(self):
        if self.isVisible():
            self.hide()
            claude = self.browser.feature("claude")
            if claude is not None:
                claude.translator.forget(self.view)
            self.tab, self.article = None, None
            if self.browser.current_tab and self.browser.current_tab.view is not None:
                self.browser.current_tab.view.setFocus()

