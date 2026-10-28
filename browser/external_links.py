# Links that open another app (zoommtg:, vscode:, mailto:, tel:…): a page doesn't get to launch apps
# on your Mac by itself. Like Chrome's "Open zoom.us?", a card at the top of the page asks first, and
# "Always allow" remembers it for that site and that kind of link.
#
# Every profile refuses these links outright (see webview.harden), so pages nobody's looking at (an
# extension's background, Browse for Me's readers) can't open anything. The browser's own visible pages
# let them through to their navigation check (webview.WebPage), which hands them here and refuses them;
# only your click on Open opens the link, with macOS. A page in the background can't ask at all, and a
# new question on the card (even the same card asking about another link) waits a moment before Open can
# be clicked, so it can't be slipped under a click. The card shows the link itself, too.
#
# Only the page itself speaks for its site: a frame inside it (an ad, an embed) or a page it's
# redirecting through is asked about as such, and never gets the site's "Always allow".

import time

from PyQt6 import sip
from PyQt6.QtCore import QEvent, QObject, QPoint, Qt, QTimer, QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

import qtawesome as qta

from . import macos, motion
from .permissions import origin_key

SETTING = "external_apps"  # {origin: [schemes it may open without asking]}
ARM_MS = 600
QUIET_AFTER_CANCEL = 3.0   # seconds a page can't ask again about a kind of link you just turned down
ALLOWED_GAP = 2.0          # seconds between links an allowed site opens by itself (no relaunch loops)
# Eclipse's own links, for Shortcuts and other apps (scripting.py): never followed from a page, which mustn't
# drive the browser it's in
OWN_SCHEME = "eclipse-browser"
# What the browser itself shows: anything else is another app's
WEB_SCHEMES = {"http", "https", "file", "data", "about", "blob", "javascript", "filesystem", "pbext", "qrc",
               "chrome", "chrome-error", "devtools", "view-source", "ws", "wss"}


def is_external(url: QUrl) -> bool:
    scheme = url.scheme().lower()
    return bool(scheme) and scheme not in WEB_SCHEMES


def allowed(settings: dict, origin: str, scheme: str) -> bool:
    return bool(origin) and scheme in settings.get(SETTING, {}).get(origin, [])


def set_allowed(settings: dict, origin: str, scheme: str, allow: bool):
    rules = settings.setdefault(SETTING, {})
    schemes = [s for s in rules.get(origin, []) if s != scheme] + ([scheme] if allow else [])
    if schemes:
        rules[origin] = schemes
    else:
        rules.pop(origin, None)


def all_rules(settings: dict) -> list[tuple[str, str]]:
    """(origin, scheme) for every site allowed to open a kind of link without asking."""
    return [(origin, scheme) for origin, schemes in sorted(settings.get(SETTING, {}).items()) for scheme in schemes]


def short_link(url: QUrl, limit=64) -> str:
    text = url.toString()
    return text if len(text) <= limit else text[:limit - 1] + "…"


class ExternalPrompt(QFrame):
    """The card: "zoom.us wants to open zoom.us" and the link, with Cancel, Always Allow and Open."""

    def __init__(self, manager, view, host):
        super().__init__(host)
        self.manager, self.view, self.host_widget = manager, view, host
        self.url = QUrl()
        self.origin = ""
        self.closed = False
        self._asked = None  # (the question on the card: asked again, it doesn't start the wait over)
        self.setObjectName("PermissionPrompt")  # (the permission card's look)
        row = QHBoxLayout(self)
        row.setContentsMargins(12, 8, 8, 8)
        row.setSpacing(10)
        self.icon = QLabel()
        self.icon.setPixmap(qta.icon("ph.arrow-square-out", color=manager.browser.theme.accent_text).pixmap(20, 20))
        words = QVBoxLayout()
        words.setSpacing(1)
        self.text = QLabel()
        self.text.setTextFormat(Qt.TextFormat.PlainText)
        self.text.setWordWrap(True)
        self.link = QLabel()  # (which link: a page asking twice can't swap it unseen)
        self.link.setObjectName("PromptDetail")
        self.link.setTextFormat(Qt.TextFormat.PlainText)
        words.addWidget(self.text)
        words.addWidget(self.link)
        self.cancel_button = QPushButton("Cancel")
        self.always_button = QPushButton("Always Allow")
        self.open_button = QPushButton("Open")
        self.open_button.setObjectName("PermissionAllow")
        self.open_button.setDefault(True)
        self.cancel_button.clicked.connect(lambda: manager.answer(self, False))
        self.always_button.clicked.connect(lambda: manager.answer(self, True, always=True))
        self.open_button.clicked.connect(lambda: manager.answer(self, True))
        row.addWidget(self.icon)
        row.addLayout(words, 1)
        row.addWidget(self.cancel_button)
        row.addWidget(self.always_button)
        row.addWidget(self.open_button)
        # One timer, started again by every new question, so an earlier one can't arm a later one
        self._arm = QTimer(self, singleShot=True, interval=ARM_MS, timeout=self._armed)
        host.installEventFilter(self)
        view.installEventFilter(self)  # (it shows, hides and moves with its own page)

    def ask(self, url: QUrl, origin: str, app: str, private: bool, own=True, redirect=False):
        """`own`: the page itself asked; otherwise a frame in it did, or (`redirect`) a link from it is
        being sent on to the app by another site."""
        self.url, self.origin = url, origin if own else ""
        site = QUrl(origin).host().removeprefix("www.") or "This page"
        self.text.setText(f"{site} wants to open {app}" if own else
                          f"A link from {site} wants to open {app}" if redirect else
                          f"A frame on {site} wants to open {app}")
        self.link.setText(short_link(url))
        self.setToolTip(url.toString()[:300])
        self.always_button.setToolTip(f"Open it, and let {site} open {url.scheme()}: links from now on without asking")
        self.always_button.setVisible(own and bool(origin) and not private)  # (a private window remembers nothing)
        # A new question: a moment before it can be answered yes (the same one asked again, by a page that
        # keeps trying, leaves the wait alone, or the card could never be answered)
        question = (url.toString(), self.origin, own, private)
        if question != self._asked:
            self._asked = question
            for button in (self.open_button, self.always_button):
                button.setEnabled(False)
            self._arm.start()
        self.place()

    def _armed(self):
        for button in (self.open_button, self.always_button):
            button.setEnabled(True)

    def place(self):
        """Over the top of its own page (in Split View, its own pane)."""
        if self.closed or sip.isdeleted(self.view) or sip.isdeleted(self.host_widget):
            return  # (answered: a layout change queued for its page doesn't bring it back)
        corner = self.view.mapTo(self.host_widget, QPoint(0, 0)) if self.host_widget.isAncestorOf(self.view) else QPoint()
        room = self.view.width() if self.host_widget.isAncestorOf(self.view) else self.host_widget.width()
        self.setFixedWidth(max(min(560, room - 24), 260))
        self.adjustSize()
        self.move(corner.x() + (room - self.width()) // 2, corner.y() + 12)
        if self.view.isVisible() and not self.isVisible() and not getattr(self, "_shown_once", False):
            self._shown_once = True
            motion.fade(self, True, motion.NORMAL)
        else:
            self.setVisible(self.view.isVisible())
        self.raise_()

    def eventFilter(self, obj, event):
        t = event.type()
        if obj is self.host_widget and t == QEvent.Type.Resize:
            self.place()
        elif obj is self.view and t in (QEvent.Type.Show, QEvent.Type.Hide, QEvent.Type.Resize, QEvent.Type.Move):
            QTimer.singleShot(0, lambda: not sip.isdeleted(self) and self.place())  # (once the layout has settled)
        return False


class ExternalLinks(QObject):
    def __init__(self, browser):
        super().__init__(browser)
        self.browser = browser
        self._prompts: dict[int, ExternalPrompt] = {}
        self._declined: dict[tuple[int, str], float] = {}  # (view, scheme): when you said Cancel
        self._opened: dict[int, float] = {}                # view: when an allowed link last opened by itself

    def requested(self, page, url: QUrl, main_frame=True, redirect=False):
        """A page tried to open another app's link (it's been refused): ask, or open it if allowed."""
        view = QWebEngineView.forPage(page)
        if view is None or sip.isdeleted(view) or not view.isVisible():
            return  # (a page in the background doesn't get to ask)
        owner = view.window()
        if owner is not self.browser and isinstance(getattr(owner, "external_links", None), ExternalLinks):
            return owner.external_links.requested(page, url, main_frame, redirect)  # (another window's page)
        scheme = url.scheme().lower()
        now = time.monotonic()
        if now - self._declined.get((id(view), scheme), -1e9) < QUIET_AFTER_CANCEL:
            return  # (you just said no: a page asking again and again doesn't bring the card back)
        app = macos.app_for_url(url.toString())
        if app is None:
            getattr(owner, "toast", self.browser.toast)(f"No app on this Mac opens “{url.scheme()}:” links")
            return
        origin = origin_key(page.url())
        private = page.profile().isOffTheRecord()
        own = main_frame and not redirect
        if own and not private and allowed(self.browser.store.settings, origin, scheme):
            if now - self._opened.get(id(view), -1e9) >= ALLOWED_GAP:
                self._opened[id(view)] = now
                QDesktopServices.openUrl(url)
            return
        prompt = self._prompts.get(id(view))
        if prompt is None or sip.isdeleted(prompt):
            prompt = self._prompts[id(view)] = ExternalPrompt(self, view, self._host_for(view))
            view.destroyed.connect(lambda _=None, key=id(view): self._forget(key))
        prompt.ask(url, origin, app, private, own=own, redirect=main_frame and redirect)

    def _host_for(self, view):
        widget = view
        while widget is not None:
            if widget is self.browser.content:
                return widget
            widget = widget.parentWidget()
        return view.parentWidget() or view

    def answer(self, prompt, open_it, always=False):
        if open_it and not prompt.open_button.isEnabled():
            return  # (not yet: the question has only just changed)
        if open_it:
            if always and prompt.always_button.isVisible() and prompt.origin:
                set_allowed(self.browser.store.settings, prompt.origin, prompt.url.scheme().lower(), True)
                self.browser.store.schedule_save()
            QDesktopServices.openUrl(prompt.url)
        else:
            self._declined[(id(prompt.view), prompt.url.scheme().lower())] = time.monotonic()
        self._close(id(prompt.view))

    def view_shown(self):
        """A tab came on screen (or went): each card shows only over its own page."""
        for prompt in list(self._prompts.values()):
            if not sip.isdeleted(prompt) and not sip.isdeleted(prompt.view) and not prompt.closed:
                prompt.setVisible(prompt.view.isVisible())

    def _forget(self, key):
        self._close(key)
        self._opened.pop(key, None)
        for declined in [k for k in self._declined if k[0] == key]:
            del self._declined[declined]

    def _close(self, key):
        prompt = self._prompts.pop(key, None)
        if prompt is not None and not sip.isdeleted(prompt):
            prompt.closed = True
            if not sip.isdeleted(prompt.host_widget):  # (closing the window: it can go first)
                prompt.host_widget.removeEventFilter(prompt)
            if not sip.isdeleted(prompt.view):
                prompt.view.removeEventFilter(prompt)
            prompt.hide()  # (gone now, not whenever it's deleted)
            prompt.deleteLater()
