# Arc's Command Bar (⌘T / ⌘L): one floating box for URLs, searches, open tabs,
# history, archived tabs and app commands. There is no "new tab page" — a tab
# only exists once you pick something here.

import re
import time
from dataclasses import dataclass
from typing import Callable

import qtawesome as qta
from PyQt6.QtCore import QEvent, QRectF, QSize, Qt, QUrl
from PyQt6.QtGui import QColor, QFont, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PyQt6.QtWidgets import (QApplication, QFrame, QGraphicsDropShadowEffect, QLabel, QLineEdit, QListWidget,
                             QListWidgetItem, QStyle, QStyledItemDelegate, QVBoxLayout, QWidget)

from . import motion
from .answers import answer

COPIED_FRESH_SECONDS = 5 * 60


def _path_hint(url: str) -> str:
    """"/r/python/comments/…" after the host, so two links on one site look different."""
    path = QUrl(url).path().rstrip("/")
    return "" if not path else path if len(path) <= 40 else path[:38] + "…"
from .content import answer_browser, fit_answer
from .models import display_host, frecency
from .page_index import read_on
from .shortcuts import for_here
from .storage import favicon
from .sidebar import tab_icon
from .theme import legible, letter_icon, quiet_scrollbars

SEARCH_ENGINES = {
    "google": ("Google", "https://www.google.com/search?q={}"),
    "duckduckgo": ("DuckDuckGo", "https://duckduckgo.com/?q={}"),
    "bing": ("Bing", "https://www.bing.com/search?q={}"),
    "brave": ("Brave Search", "https://search.brave.com/search?q={}"),
    "kagi": ("Kagi", "https://kagi.com/search?q={}"),
    "ecosia": ("Ecosia", "https://www.ecosia.org/search?q={}"),
}
# Site search, like Arc: type a site's name or keyword and press Tab to search just that site, or
# start with a keyword ("yt lofi beats"). One-letter keywords only work with Tab, so ordinary
# searches like "x factor" aren't taken over.
SITE_SEARCHES = {
    "youtube": ("YouTube", "https://www.youtube.com/results?search_query={}", ("yt", "youtube")),
    "github": ("GitHub", "https://github.com/search?q={}", ("gh", "github")),
    "wikipedia": ("Wikipedia", "https://en.wikipedia.org/wiki/Special:Search?search={}", ("w", "wiki", "wikipedia")),
    "maps": ("Google Maps", "https://www.google.com/maps/search/{}", ("maps", "gmaps")),
    "images": ("Google Images", "https://www.google.com/search?tbm=isch&q={}", ("img", "images")),
    "amazon": ("Amazon", "https://www.amazon.com/s?k={}", ("az", "amazon")),
    "reddit": ("Reddit", "https://www.reddit.com/search/?q={}", ("r", "reddit")),
    "x": ("X", "https://x.com/search?q={}", ("x", "twitter")),
    "stackoverflow": ("Stack Overflow", "https://stackoverflow.com/search?q={}", ("so", "stackoverflow")),
    "mdn": ("MDN", "https://developer.mozilla.org/en-US/search?q={}", ("mdn",)),
    "pypi": ("PyPI", "https://pypi.org/search/?q={}", ("pypi",)),
    "npm": ("npm", "https://www.npmjs.com/search?q={}", ("npm",)),
    "gmail": ("Gmail", "https://mail.google.com/mail/u/0/#search/{}", ("gmail", "mail")),
    "drive": ("Google Drive", "https://drive.google.com/drive/search?q={}", ("drive",)),
    "spotify": ("Spotify", "https://open.spotify.com/search/{}", ("spotify",)),
    "claude": ("Claude", "https://claude.ai/new?q={}", ("claude",)),
}
# Keywords that also start everyday searches ("so what", "mail order") need Tab, like one-letter ones
_TAB_ONLY = {"so", "mail", "drive", "claude", "images", "maps"}


BUILTIN_SITES = dict(SITE_SEARCHES)
CUSTOM_SETTING = "search_shortcuts"


def use_custom_sites(entries: list[dict]):
    """Add your own search shortcuts (Settings → General) to the built-in ones. Each is
    {"name", "keyword", "url"} with %s where the search terms go."""
    SITE_SEARCHES.clear()
    SITE_SEARCHES.update(BUILTIN_SITES)
    for i, entry in enumerate(entries or []):
        url, keyword = (entry.get("url") or "").strip(), (entry.get("keyword") or "").strip().lower()
        if "%s" not in url or not keyword or not url.startswith(("http://", "https://")):
            continue
        name = (entry.get("name") or "").strip() or keyword
        # Yours win over a built-in that uses the same keyword
        for site, (_n, _u, words) in list(SITE_SEARCHES.items()):
            if keyword in words:
                SITE_SEARCHES[site] = (_n, _u, tuple(w for w in words if w != keyword))
        SITE_SEARCHES[f"custom:{i}"] = (name, url.replace("{", "{{").replace("}", "}}").replace("%s", "{}"), (keyword,))


def site_url(site: str, terms: str) -> str:
    return SITE_SEARCHES[site][1].format(QUrl.toPercentEncoding(terms.strip()).data().decode())


def site_for_tab(text: str) -> str | None:
    """The site Tab would switch to for what's typed: a keyword, or the start of a site's name."""
    t = text.strip().lower()
    if not t:
        return None
    for site, (name, _url, keywords) in SITE_SEARCHES.items():
        if t in keywords:
            return site
    if len(t) >= 2:
        for site, (name, _url, keywords) in SITE_SEARCHES.items():
            if name.lower().startswith(t):
                return site
    return None


def parse_site_search(query: str) -> tuple[str, str] | None:
    """("youtube", "lofi beats") for "yt lofi beats"; None if it doesn't start with a keyword."""
    keyword, _, terms = query.strip().partition(" ")
    keyword = keyword.lower()
    if not terms.strip() or len(keyword) < 2 or keyword in _TAB_ONLY:
        return None
    for site, (_name, _url, keywords) in SITE_SEARCHES.items():
        if keyword in keywords:
            return site, terms.strip()
    return None


def url_key(url: str) -> str:
    """"https://www.github.com/x" → "github.com/x": what you'd type to mean it."""
    return re.sub(r"^[a-z][a-z0-9+.-]*://(www\.)?", "", url, flags=re.IGNORECASE)


def inline_completion(typed: str, candidates) -> tuple[str, str, str] | None:
    """Arc/Chrome-style autocomplete: (text to show, URL to open, title) for what's typed so far.

    candidates: (url, title, score) for history, tabs and favorites. Plain words complete to a site
    ("git" → "github.com", the site you use most that starts with it); once there's a "/", whole
    addresses ("github.com/pyqt" → "github.com/pyqt/pyqt6"). What you typed keeps its case."""
    t = typed.lower()
    if not t or " " in t or t.startswith(("http:", "https:")):
        return None
    if "/" in t:
        best = max((c for c in candidates if url_key(c[0]).lower().startswith(t)), key=lambda c: c[2], default=None)
        if best is None:
            return None
        key = url_key(best[0])
        return typed + key[len(typed):], best[0], best[1]
    sites: dict[str, list] = {}  # host → [total score, best (url, title, score)]
    for url, title, score in candidates:
        host = url_key(url).split("/", 1)[0].lower()
        if not host.startswith(t) or host == t:
            continue
        site = sites.setdefault(host, [0.0, (url, title, score)])
        site[0] += score
        # The site's front page names it best; otherwise its most-used page
        if url_key(url).rstrip("/").lower() == host or score > site[1][2]:
            site[1] = (url, title, score)
    if not sites:
        return None
    host, (_total, (url, title, _score)) = max(sites.items(), key=lambda kv: kv[1][0])
    scheme, _, rest = url.partition("://")
    front = f"{scheme or 'https'}://{rest.split('/', 1)[0]}/"  # the address you actually use (with its "www.")
    return typed + host[len(typed):], front, title if url_key(url).rstrip("/").lower() == host else host


def matches_query(query: str, title: str, url: str) -> bool:
    """Every word of the query appears in the title or address ("pyqt docs" finds "Qt for Python · docs")."""
    haystack = f"{title} {url}".lower()
    return all(word in haystack for word in query.lower().split())


ROW_HEIGHT = 42
MAX_ROWS = 8

_DOMAIN = re.compile(r"^([\w-]+\.)+[a-z]{2,}(:\d+)?([/?#].*)?$", re.IGNORECASE)
_LOCAL = re.compile(r"^(localhost|127\.0\.0\.1|\[::1\])(:\d+)?([/?#].*)?$", re.IGNORECASE)
# A router or a machine on your network: 192.168.1.1, 10.0.0.5:8080/admin (http: they rarely have https)
_IPV4 = re.compile(r"^((25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(25[0-5]|2[0-4]\d|1?\d?\d)(:\d+)?([/?#].*)?$")
_LAN = re.compile(r"^[\w-]+(\.[\w-]+)*\.(local|lan|home\.arpa|internal)(:\d+)?([/?#].*)?$", re.IGNORECASE)
# File names that look like addresses ("node.js", "index.html"): they open, but search comes first
_FILE_LIKE = re.compile(r"^[\w-]+\.(js|ts|jsx|tsx|mjs|html?|css|json|ya?ml|toml|txt|pdf|png|jpe?g|gif|svg|csv|xml|"
                        r"py|rb|rs|go|java|kt|swift|cpp|c|h|sh|zsh|lock|log|zip|md)$", re.IGNORECASE)
_SCHEME = re.compile(r"^(https?|file|about|chrome|view-source|data):", re.IGNORECASE)


def to_url(text: str) -> str | None:
    """Return a URL if the text looks like one, otherwise None (meaning: search for it)."""
    text = text.strip()
    if not text or " " in text:
        return None
    if _SCHEME.match(text):
        return text
    if _LOCAL.match(text) or _IPV4.match(text) or _LAN.match(text):
        return "http://" + text
    if _DOMAIN.match(text):
        return "https://" + text
    return None


def looks_like_a_file(text: str) -> bool:
    """"node.js", "config.yaml": more likely something to search for than a site to open."""
    return bool(_FILE_LIKE.match(text.strip()))


def search_url(text: str, engine: str = "google") -> str:
    template = SEARCH_ENGINES.get(engine, SEARCH_ENGINES["google"])[1]
    return template.format(QUrl.toPercentEncoding(text.strip()).data().decode())


@dataclass
class Result:
    title: str
    subtitle: str
    badge: str
    icon: object
    run: Callable[[], None]
    keep_open: bool = False  # e.g. "Ask Claude": the answer appears inside the bar


class ResultDelegate(QStyledItemDelegate):
    def __init__(self, bar):
        super().__init__(bar)
        self.bar = bar

    def sizeHint(self, option, index):
        return QSize(option.rect.width(), ROW_HEIGHT)

    def paint(self, painter: QPainter, option, index):
        result: Result = index.data(Qt.ItemDataRole.UserRole)
        theme = self.bar.browser.theme
        text = theme.panel_text
        rect = QRectF(option.rect).adjusted(0, 1, 0, -1)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        highlight = QColor(theme.accent)
        highlight.setAlphaF(0.85 if not theme.dark else 0.65)
        glide = self.bar.highlight_y
        if glide is not None:
            # The highlight on its way from one row to the next: each row paints its own slice of it
            moving = QRectF(rect.left(), glide + 1, rect.width(), rect.height())
            if moving.intersects(QRectF(option.rect)):
                path = QPainterPath()
                path.addRoundedRect(moving, 9, 9)
                painter.setClipRect(option.rect)
                painter.fillPath(path, highlight)
                painter.setClipping(False)
        elif option.state & QStyle.StateFlag.State_Selected:
            path = QPainterPath()
            path.addRoundedRect(rect, 9, 9)
            painter.fillPath(path, highlight)
        if option.state & QStyle.StateFlag.State_Selected:
            text = QColor(theme.on_accent)  # (white, or dark on a pale Space colour)

        icon_rect = QRectF(rect.left() + 12, rect.center().y() - 9, 18, 18)
        result.icon.paint(painter, icon_rect.toRect())

        left = icon_rect.right() + 12
        right = rect.right() - 12
        font = QFont(painter.font())
        font.setPixelSize(12)
        painter.setFont(font)
        badge_w = painter.fontMetrics().horizontalAdvance(result.badge) if result.badge else 0
        if result.badge:
            dim = QColor(text)
            dim.setAlphaF(0.6)
            painter.setPen(dim)
            painter.drawText(QRectF(right - badge_w, rect.top(), badge_w, rect.height()),
                             Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, result.badge)
            right -= badge_w + 14

        font.setPixelSize(14)
        font.setWeight(QFont.Weight.Medium)  # (SF Pro Medium, like the sidebar and Arc)
        painter.setFont(font)
        fm = painter.fontMetrics()
        title = fm.elidedText(result.title, Qt.TextElideMode.ElideRight, int((right - left) * 0.7))
        painter.setPen(text)
        painter.drawText(QRectF(left, rect.top(), right - left, rect.height()),
                         Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, title)

        if result.subtitle:
            sub_left = left + fm.horizontalAdvance(title) + 10
            font.setPixelSize(12)
            font.setWeight(QFont.Weight.Normal)
            painter.setFont(font)
            dim = QColor(text)
            dim.setAlphaF(0.5)
            painter.setPen(dim)
            sub = painter.fontMetrics().elidedText("— " + result.subtitle, Qt.TextElideMode.ElideRight,
                                                   int(max(right - sub_left, 0)))
            painter.drawText(QRectF(sub_left, rect.top(), right - sub_left, rect.height()),
                             Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, sub)
        painter.restore()


class CommandBar(QWidget):
    """Full-window overlay: a dimmed backdrop with the command panel floating on top."""

    def __init__(self, browser, parent):
        super().__init__(parent)
        self.browser = browser
        self.mode = "new"
        self.request = None
        self._copied_at = 0.0  # when the clipboard last changed (macOS tells us as the app comes forward)
        QApplication.clipboard().dataChanged.connect(lambda: setattr(self, "_copied_at", time.time()))
        self.setObjectName("CommandBackdrop")
        self._intro = 1.0  # 0 → 1 as the bar appears: the backdrop darkens and the panel settles into place
        self.hide()

        self.panel = QFrame(self)
        self.panel.setObjectName("CommandPanel")
        shadow = QGraphicsDropShadowEffect(self.panel)
        shadow.setBlurRadius(48)
        shadow.setOffset(0, 12)
        shadow.setColor(QColor(0, 0, 0, 90))
        self.panel.setGraphicsEffect(shadow)

        layout = QVBoxLayout(self.panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.input = QLineEdit()
        self.input.setObjectName("CommandInput")
        self.input.setPlaceholderText("Search or Enter URL…")
        self.input.textChanged.connect(self.update_results)
        self.input.installEventFilter(self)
        # Tab-to-search: a pill naming the site sits at the start of the box
        self.site = None
        self.site_pill = QLabel(self.input)
        self.site_pill.setObjectName("SitePill")
        self.site_pill.hide()
        self.divider = QFrame()
        self.divider.setObjectName("CommandDivider")
        self.divider.setFixedHeight(1)
        self.list = QListWidget()
        self.list.setObjectName("CommandList")
        quiet_scrollbars(self.list)
        self.list.setItemDelegate(ResultDelegate(self))
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.list.itemClicked.connect(self._run_item)
        self.highlight_y = None  # where the highlight is while it glides between rows (None: on its row)
        layout.addWidget(self.input)
        layout.addWidget(self.divider)
        layout.addWidget(self.list)
        self.answer = answer_browser(browser, margin=16)
        layout.addWidget(self.answer)

    # --- Showing / hiding --------------------------------------------------------

    def open(self, mode="new", text=""):
        """mode "new" opens results in a new tab; "current" navigates the current tab."""
        self.mode = mode
        self._completion = None  # (text shown, url, title) while an autocompletion is in the box
        self._prev_typed = ""
        self._quiet = True  # the text we start with (the current address, say) isn't typing
        now = time.time()
        store = self.browser.store
        self._ranked = sorted(store.history, key=lambda e: frecency(e, now), reverse=True)[:2000]
        # What autocomplete can suggest: history by frecency, plus open tabs and favorites (strongly)
        self._candidates = [(e["url"], e.get("title", ""), frecency(e, now)) for e in self._ranked]
        self._candidates += [(t.home_url or t.url, t.label, 400) for t in store.all_tabs()
                             if (t.home_url or t.url).startswith(("http://", "https://"))]
        self.browser.emit("command_bar_opened")  # (your GitHub repos, say, refreshed for next time)
        self._set_site(None)
        self.input.setPlaceholderText("Open a page next to this one…" if mode == "split" else "Search or Enter URL…")
        self.setGeometry(self.parentWidget().rect())
        self._intro = 0.0
        self.show()
        self.raise_()
        motion.animate(self, 0.0, 1.0, self._set_intro, motion.FAST)
        self.input.setText(text)
        self.input.selectAll()
        self.input.setFocus()
        self.update_results()
        self._quiet = False
        self._prev_typed = ""  # the starting text is selected, so the first key typed replaces it

    def close_bar(self):
        self._cancel_answer()
        self.hide()
        self.input.clear()
        if self.browser.current_tab and self.browser.current_tab.view:
            self.browser.current_tab.view.setFocus()

    def resizeEvent(self, event):
        self._place_panel()

    def mousePressEvent(self, event):
        # Clicking the backdrop dismisses, like Arc
        if not self.panel.geometry().contains(event.position().toPoint()):
            self.close_bar()

    def _place_panel(self):
        width = min(660, self.width() - 80)
        answering = self.answer.isVisible()
        rows = 0 if answering else min(self.list.count(), MAX_ROWS)
        list_h = rows * ROW_HEIGHT + 12 if rows else 0
        self.list.setFixedHeight(list_h)
        self.list.setVisible(bool(rows))
        self.divider.setVisible(bool(rows) or answering)
        body_h = self.answer.height() if answering else list_h
        height = self.input.sizeHint().height() + (body_h + 1 if body_h else 0)
        drop = round((1 - self._intro) * 12)  # arriving, it drifts down the last few points
        self.panel.setGeometry((self.width() - width) // 2, int(self.height() * 0.17) - drop, width, height)

    def paintEvent(self, event):
        # The dimmed page behind the bar, fading in with it
        dark = self.browser.theme.dark
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(0, 0, 0, round((60 if dark else 28) * self._intro)))
        p.end()

    def _set_intro(self, value):
        self._intro = value
        self._place_panel()
        self.update()

    # --- Keyboard ----------------------------------------------------------------------

    def eventFilter(self, obj, event):
        if obj is self.input and event.type() == QEvent.Type.KeyPress:
            key = event.key()
            completing = self._completion is not None and self.input.hasSelectedText() and \
                self.input.selectionStart() + len(self.input.selectedText()) == len(self.input.text())
            if key in (Qt.Key.Key_Right, Qt.Key.Key_End) and completing:
                self.input.deselect()  # take the completion
                self.input.setCursorPosition(len(self.input.text()))
                self._completion = None
                self._prev_typed = self.input.text()  # so deleting from here doesn't complete it again
                return True
            if key == Qt.Key.Key_Escape and completing:
                typed = self.input.text()[:self.input.selectionStart()]
                self._completion = None
                self.input.blockSignals(True)  # drop the suggestion; a second Esc closes the bar
                self.input.setText(typed)
                self.input.blockSignals(False)
                self._prev_typed = typed
                self.update_results()
                return True
            if key == Qt.Key.Key_Escape:
                if self.site is not None:
                    self._set_site(None)
                else:
                    self.close_bar()
                return True
            # (what was typed, not the autocompleted address: "you" + "tube.com" is still YouTube's Tab)
            if key == Qt.Key.Key_Tab and self.site is None and site_for_tab(self._typed()):
                self._set_site(site_for_tab(self._typed()))
                return True
            if key == Qt.Key.Key_Backspace and self.site is not None and not self.input.text():
                self._set_site(None)
                return True
            if key in (Qt.Key.Key_Down, Qt.Key.Key_Up):
                step = 1 if key == Qt.Key.Key_Down else -1
                if self.list.count():
                    self._glide_to((self.list.currentRow() + step) % self.list.count())
                return True
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                # ⌘↩: Browse for Me with whatever is typed
                query = self.input.text().strip()
                claude = self.browser.feature("claude")
                if query and claude is not None and claude.available():
                    self.close_bar()
                    claude.browse_for_me(query)
                return True
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                item = self.list.currentItem()
                if item:
                    self._run_item(item)
                return True
        return super().eventFilter(obj, event)

    def _set_site(self, site):
        self.site = site
        if site is None:
            self.site_pill.hide()
            self.input.setTextMargins(0, 0, 0, 0)
            self.input.setPlaceholderText("Search or Enter URL…")
        else:
            name = SITE_SEARCHES[site][0]
            self.site_pill.setText(name)
            self.site_pill.adjustSize()
            self.site_pill.move(14, (self.input.height() - self.site_pill.height()) // 2)
            self.site_pill.show()
            self.input.setTextMargins(self.site_pill.width() + 8, 0, 0, 0)
            self.input.clear()
            self.input.setPlaceholderText(f"Search {name}…")
        self.update_results()

    def _glide_to(self, row):
        """Arrow keys: the highlight slides to the next row instead of jumping (wrapping round from
        the last to the first, it just jumps)."""
        old = self.list.currentItem()
        start = self.highlight_y if self.highlight_y is not None else (
            self.list.visualItemRect(old).top() if old is not None else None)
        self.list.setCurrentRow(row)
        end = self.list.visualItemRect(self.list.currentItem()).top()
        if start is None or abs(end - start) > ROW_HEIGHT * 1.5:
            self._settle_highlight()
            return

        def step(y):
            self.highlight_y = y
            self.list.viewport().update()
        motion.animate(self.list, start, end, step, 110, self._settle_highlight)

    def _settle_highlight(self):
        animation = getattr(self.list, "_pb_animation", None)
        if animation is not None:
            animation.stop()
        self.highlight_y = None
        self.list.viewport().update()

    def _run_item(self, item: QListWidgetItem):
        result: Result = item.data(Qt.ItemDataRole.UserRole)
        if result.keep_open:
            result.run()
            return
        self.close_bar()
        result.run()

    # --- Building results ---------------------------------------------------------------

    def _icon(self, name):
        return qta.icon(name, color=self.browser.theme.panel_text)

    def _open(self, url):
        if self.mode == "split":
            return lambda: self.browser.open_in_split(url)
        if self.mode == "current" and self.browser.current_tab:
            return lambda: self.browser.navigate_current(url)
        return lambda: self.browser.open_url(url)

    # --- Ask Claude --------------------------------------------------------------------

    def ask(self, question, ai=None, prompt=None, shape=None):
        """Ask, and show the answer as it comes. shape: the answer → what's shown (citations made links, say)."""
        self._cancel_answer()
        claude = self.browser.feature("claude")
        ai = ai or self.browser.assistant() or (claude.claude if claude is not None else None)
        if ai is None:
            return
        self._show_answer("*Thinking…*")
        self.request = ai.ask(prompt or (
            "Answer this question from someone using their web browser. Be direct and concise "
            "(under 200 words unless they ask for more); use Markdown where it helps.\n\n" + question))
        show = (lambda text: self._show_answer(shape(text))) if shape is not None else self._show_answer
        self.request.partial.connect(show)
        self.request.finished.connect(show)
        name = self.browser.assistant_name(ai)
        self.request.failed.connect(lambda msg: self._show_answer(f"**Couldn't ask {name}:** {msg}"))

    def ask_about(self, shown, prompt, ai=None):
        """Open the bar with `shown` in the box and the answer to `prompt` under it (right-clicking a
        selection: explain it, translate it)."""
        self.open("new")
        self._quiet = True
        self.input.blockSignals(True)
        self.input.setText(shown)
        self.input.blockSignals(False)
        self._quiet = False
        self._settle_highlight()  # (new results: the highlight starts on its row)
        self.list.clear()
        self.ask(shown, ai, prompt)

    def _show_answer(self, text):
        fit_answer(self.answer, self.panel.width(), text, max_height=int(self.height() * 0.6))
        self._place_panel()

    def _cancel_answer(self):
        if self.request is not None:
            self.request.cancel()
            self.request = None
        self.answer.hide()

    def _typed(self) -> str:
        """What you've actually typed: the box's text minus an autocompletion selected after it."""
        text = self.input.text()
        if (self._completion is not None and text == self._completion[0] and self.input.hasSelectedText()
                and self.input.selectionStart() + len(self.input.selectedText()) == len(text)):
            return text[:self.input.selectionStart()]
        return text

    def _autocomplete(self, typed) -> Result | None:
        """Fill in the rest of a site you use as you type (only while typing forwards, not deleting)."""
        grew = len(typed) > len(self._prev_typed) and typed.startswith(self._prev_typed)
        self._prev_typed = typed
        self._completion = None
        if self._quiet or not grew or self.site is not None or self.mode == "split" and not typed:
            return None
        found = inline_completion(typed, self._candidates)
        if found is None:
            return None
        text, url, title = found
        self._completion = found
        self.input.blockSignals(True)
        self.input.setText(text)
        self.input.setSelection(len(typed), len(text) - len(typed))
        self.input.blockSignals(False)
        icon = legible(favicon(url), self.browser.theme) or letter_icon(display_host(url), self.browser.theme.accent)
        return Result(url_key(url).rstrip("/"), title if title != url_key(url).rstrip("/") else "", "Open", icon,
                      self._open(url))

    def update_results(self):
        self._cancel_answer()
        typed = self._typed()
        completed = self._autocomplete(typed) if self.input.hasFocus() or not self._quiet else None
        query = typed.strip()
        q = query.lower()
        results: list[Result] = []
        store = self.browser.store
        theme = self.browser.theme

        if self.site is not None:
            name = SITE_SEARCHES[self.site][0]
            if query:
                results.append(Result(query, f"Search {name}", "", self._site_icon(self.site),
                                      self._open(site_url(self.site, query))))
            self._show(results)
            return

        tab_site = site_for_tab(query) if " " not in query else None
        prefixed = parse_site_search(query)
        if prefixed:
            site, terms = prefixed
            results.append(Result(terms, f"Search {SITE_SEARCHES[site][0]}", SITE_SEARCHES[site][0],
                                  self._site_icon(site), self._open(site_url(site, terms))))

        if query:
            url = to_url(query)
            if url:
                results.append(Result(url, "", "Open", self._icon("ph.globe-simple"), self._open(url)))
            engine = self.browser.store.settings.get("search_engine", "google")
            search = Result(query, f"Search {SEARCH_ENGINES.get(engine, SEARCH_ENGINES['google'])[0]}",
                            "Search" if url else "", self._icon("ph.magnifying-glass"),
                            self._open(search_url(query, engine)))
            if url and looks_like_a_file(query):  # (Return searches; the address is a row below)
                results.insert(len(results) - 1, search)
            else:
                results.append(search)
            self.browser.emit("query_rows", self, query, results, url)  # (Ask Claude, Browse for Me…)

        if tab_site:  # a hint that Tab searches this site (after the search, so Return still searches)
            results.insert(min(len(results), 2), Result(f"Search {SITE_SEARCHES[tab_site][0]}", "", "Tab",
                                                        self._site_icon(tab_site), lambda s=tab_site: self._set_site(s),
                                                        keep_open=True))

        # Commands are only offered once the query clearly names one
        if len(q) >= 2:
            for name, icon, run in self.browser.commands():
                if q in name.lower():
                    results.append(Result(name, "", "Command", self._icon(icon), run))

        seen = set()
        current = self.browser.current_tab
        copied = None if q or self.mode == "current" else self._copied_link()
        if copied:  # Arc's "paste and go": a link you just copied is the first thing offered
            seen.add(copied)
            results.insert(0, Result(display_host(copied) + _path_hint(copied), "Copied link", "Open",
                                     self._icon("ph.clipboard-text"), self._open(copied)))
        for tab in store.all_tabs():
            if tab is current or not tab.url:
                continue
            if not q or q in tab.label.lower() or q in tab.url.lower():
                if not q and tab not in store.visible_tabs():
                    continue
                seen.add(tab.url)
                if self.mode == "split":
                    badge, run = "Split With", (lambda t=tab: self.browser.add_to_split(t))
                else:
                    badge, run = "Switch to Tab", (lambda t=tab: self.browser.activate_tab(t))
                results.append(Result(tab.label, display_host(tab.url), badge, tab_icon(tab, theme), run))
            if len(results) >= MAX_ROWS:
                break

        results += self.browser.gather("command_results", self, query, seen)  # (your GitHub repos, by name…)

        limit = MAX_ROWS + 4
        for entry in self._ranked:  # most-used first, not just most recent
            if len(results) >= limit:
                break
            if entry["url"] in seen:
                continue
            if not q or matches_query(q, entry["title"], entry["url"]):
                seen.add(entry["url"])
                results.append(self._url_result(entry, ""))
        if completed is not None:  # the autocompleted site goes first, so Return opens it
            results = [completed] + [r for r in results if not (r.badge == "Open" and r.title == completed.title)]

        if len(q) >= 3 and self.site is None and self.mode != "split":
            # Pages whose text matches, even when their title doesn't
            for hit in self.browser.page_index.search(query, limit=3):
                if hit["url"] in seen or len(results) >= limit:
                    continue
                seen.add(hit["url"])
                icon = legible(favicon(hit["url"]), theme) or letter_icon(hit["host"], theme.accent)
                when = read_on(hit.get("visited"))
                results.append(Result(hit["title"] or hit["host"],
                                      " · ".join(p for p in (when, hit["snippet"] or hit["host"]) if p), "Read",
                                      icon, self._open(hit["url"])))

        if q:
            for entry in store.archive:
                if len(results) >= limit:
                    break
                if entry["url"] not in seen and (q in entry["title"].lower() or q in entry["url"].lower()):
                    seen.add(entry["url"])
                    results.append(self._url_result(entry, "Archived"))

        instant = answer(query) if self.mode != "split" else None
        if instant is not None:  # a sum, a conversion, a time or a colour: the answer goes first
            results.insert(0, Result(instant.text, instant.detail, "Copy", self._answer_icon(instant),
                                     lambda a=instant: self._copy_answer(a)))
        self._show(results[:limit])

    def _answer_icon(self, instant):
        if instant.kind == "color":  # the colour, ringed so it shows on a selected row of the same colour
            pm = QPixmap(40, 40)
            pm.setDevicePixelRatio(2)
            pm.fill(Qt.GlobalColor.transparent)
            p = QPainter(pm)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.setPen(QPen(QColor(255, 255, 255, 200), 1.5))
            p.setBrush(QColor(instant.color))
            p.drawEllipse(QRectF(1.5, 1.5, 17, 17))
            p.end()
            return QIcon(pm)
        return self._icon({"math": "ph.calculator", "unit": "ph.ruler", "time": "ph.clock"}[instant.kind])

    def _copy_answer(self, instant):
        QApplication.clipboard().setText(instant.text)
        self.browser.toast(f"Copied {instant.text}")

    def _show(self, results):
        self._settle_highlight()  # (new results: the highlight starts on its row)
        self.list.clear()
        for result in results:
            result.subtitle, result.badge = for_here(result.subtitle), for_here(result.badge)  # ("⌘↩": Ctrl+Enter)
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, result)
            # (the row is painted by hand: these are what VoiceOver reads for it)
            item.setText(result.title)
            item.setData(Qt.ItemDataRole.AccessibleTextRole, result.title)
            extra = ", ".join(part for part in (result.subtitle, result.badge) if part)
            if extra:
                item.setData(Qt.ItemDataRole.AccessibleDescriptionRole, extra)
            self.list.addItem(item)
        if self.list.count():
            self.list.setCurrentRow(0)
        self._place_panel()

    def _copied_link(self) -> str | None:
        """The link on the clipboard, if it was copied in the last few minutes and isn't open already."""
        if time.time() - self._copied_at > COPIED_FRESH_SECONDS:
            return None
        text = QApplication.clipboard().text().strip()
        if not text or len(text) > 2000 or any(c.isspace() for c in text):
            return None
        if not re.match(r"^(https?://|www\.)", text, re.IGNORECASE) and not re.match(r"^[\w-]+(\.[\w-]+)+/", text):
            return None
        url = to_url(text)
        if not url or not url.startswith(("http://", "https://")):
            return None
        if any(t.url.rstrip("/") == url.rstrip("/") for t in self.browser.store.all_tabs()):
            return None
        return url

    def _site_icon(self, site):
        url = SITE_SEARCHES[site][1]
        theme = self.browser.theme
        return legible(favicon(url), theme) or letter_icon(SITE_SEARCHES[site][0], theme.accent)

    def _url_result(self, entry, badge):
        url = entry["url"]
        icon = legible(favicon(url), self.browser.theme) or letter_icon(display_host(url), self.browser.theme.accent)
        return Result(entry.get("title") or url, display_host(url), badge, icon, self._open(url))
